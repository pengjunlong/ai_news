#!/usr/bin/env python3
"""
AI 行业新闻爬虫 — 从多个科技媒体抓取每日 AI 行业重要进展：
  - 36氪 (36kr.com)        — AI 频道
  - 量子位 (qbitai.com)    — 首页
  - 第一财经 (yicai.com)   — AI 标签
  - 联商网 (linkshop.com)  — 首页新闻（HTTP + GBK 编码）
  - 虎嗅 (huxiu.com)       — 首页

输出：
  1. 每日 Markdown 文章（存入 _posts/）— 按来源分组，段落式汇总
  2. 通过 SMTP 发送邮件摘要（每条新闻一句话+来源链接）
"""

import asyncio
import logging
import os
import random
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
}

MAX_CONCURRENT = 3
MAX_RETRIES = 3
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
BASE_DELAY = (0.5, 2.0)
MAX_ITEMS_PER_SOURCE = 8

# AI 相关关键词（用于联商网/虎嗅等综合媒体的过滤）
AI_KEYWORDS = [
    "AI", "人工智能", "大模型", "智能", "机器学习", "深度学习",
    "机器人", "算法", "自动驾驶", "云计算", "数字化", "GPT",
    "LLM", "Agent", "大语言", "神经网络", "计算机视觉",
]


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class NewsArticle:
    title: str
    url: str
    summary: str
    source: str
    source_url: str


# ---------------------------------------------------------------------------
# 来源配置
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "name": "36氪",
        "url": "https://36kr.com/information/AI",
        "home": "https://36kr.com",
        "type": "36kr",
    },
    {
        "name": "量子位",
        "url": "https://www.qbitai.com/",
        "home": "https://www.qbitai.com",
        "type": "qbitai",
    },
    {
        "name": "第一财经",
        "url": "https://www.yicai.com/news/?tag=AI",
        "home": "https://www.yicai.com",
        "type": "yicai",
    },
    {
        "name": "联商网",
        "url": "http://www.linkshop.com.cn/",  # HTTP (HTTPS SSL 证书问题)
        "home": "http://www.linkshop.com.cn",
        "type": "linkshop",
        "encoding": "gbk",
    },
    {
        "name": "虎嗅",
        "url": "https://www.huxiu.com/",
        "home": "https://www.huxiu.com",
        "type": "huxiu",
    },
]


# ---------------------------------------------------------------------------
# 各来源解析器
# ---------------------------------------------------------------------------

def parse_36kr(html: str, base_url: str) -> List[dict]:
    """解析36氪 AI 频道 — 使用 kr-flow-article-item 卡片"""
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    for card in soup.select("div.kr-flow-article-item"):
        links = card.select("a[href^='/p/'], a[href*='36kr.com/p/']")
        for a in links:
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or not title or len(title) < 8 or len(title) > 200:
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)
            if not href.startswith("http"):
                href = base_url + href

            # 找摘要（desc 元素）
            desc_el = card.select_one(".article-item-desc, .desc, .summary")
            summary = desc_el.get_text(strip=True)[:100] if desc_el else ""

            articles.append({"title": title, "url": href, "summary": summary})
            break  # 每张卡只取一条

        if len(articles) >= MAX_ITEMS_PER_SOURCE:
            break

    return articles


def parse_qbitai(html: str, base_url: str) -> List[dict]:
    """解析量子位首页 — 直接按 URL 模式匹配文章链接"""
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    # 量子位文章 URL 格式: /YYYY/MM/数字.html 或完整 URL
    pat = re.compile(r"qbitai\.com/\d{4}/\d{2}/\d+")
    for a in soup.find_all("a", href=pat):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not href or not title or len(title) < 8 or len(title) > 200:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        if not href.startswith("http"):
            href = base_url + href

        articles.append({"title": title, "url": href, "summary": ""})
        if len(articles) >= MAX_ITEMS_PER_SOURCE:
            break

    return articles


def _clean_yicai_title(raw_text: str) -> str:
    """第一财经文章链接文本包含 标题+摘要+时间，提取纯标题部分"""
    # 去掉末尾时间标记（N分钟前 / N小时前 / YYYY-MM-DD）
    text = re.sub(r'\d+分钟前.*$|\d+小时前.*$|\d+天前.*$|\d{4}-\d{2}-\d{2}.*$', '', raw_text).strip()
    # 第一财经标题通常以中文句号/逗号结尾，摘要紧跟其后
    # 按第一个句号或完整标题分隔（标题≤40字）
    title_end = re.search(r'[。！？]', text)
    if title_end and title_end.start() <= 60:
        return text[: title_end.start() + 1].strip()
    # 没有句号则取前50字
    return text[:50].strip()


def parse_yicai(html: str, base_url: str) -> List[dict]:
    """解析第一财经 AI 标签页"""
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    for a in soup.select("a[href*='/news/']"):
        href = a.get("href", "")
        raw_text = a.get_text(strip=True)
        if not href or not raw_text or len(raw_text) < 8:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        if not href.startswith("http"):
            href = base_url + href

        title = _clean_yicai_title(raw_text)
        if len(title) < 5:
            continue

        # 摘要取链接文本中标题之后的部分（裁剪时间后的剩余内容）
        full = re.sub(r'\d+分钟前.*$|\d+小时前.*$|\d+天前.*$|\d{4}-\d{2}-\d{2}.*$', '', raw_text).strip()
        summary = full[len(title):].strip()[:80] if len(full) > len(title) else ""

        articles.append({"title": title, "url": href, "summary": summary})
        if len(articles) >= MAX_ITEMS_PER_SOURCE:
            break

    return articles


def parse_linkshop(html: str, base_url: str) -> List[dict]:
    """解析联商网（GBK 编码，HTTP），过滤 AI 相关新闻"""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen_urls = set()

    for a in soup.find_all("a", href=re.compile(r"linkshop\.com(?:\.cn)?/news/\d+")):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not href or not title or len(title) < 5:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # 优先收录含 AI 关键词的文章
        is_ai = any(kw in title for kw in AI_KEYWORDS)
        articles.append({"title": title, "url": href, "summary": "", "is_ai": is_ai})

    # 优先 AI 相关，凑满 MAX_ITEMS_PER_SOURCE
    ai_arts = [a for a in articles if a.get("is_ai")]
    other_arts = [a for a in articles if not a.get("is_ai")]
    result = (ai_arts + other_arts)[:MAX_ITEMS_PER_SOURCE]
    return [{"title": a["title"], "url": a["url"], "summary": ""} for a in result]


def parse_huxiu(html: str, base_url: str) -> List[dict]:
    """解析虎嗅首页，过滤 AI 相关文章"""
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    for a in soup.find_all("a", href=re.compile(r"/article/\d+\.html")):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not href or not title or len(title) < 8 or len(title) > 150:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        if not href.startswith("http"):
            href = base_url + href

        is_ai = any(kw in title for kw in AI_KEYWORDS)
        articles.append({"title": title, "url": href, "summary": "", "is_ai": is_ai})

    ai_arts = [a for a in articles if a.get("is_ai")]
    other_arts = [a for a in articles if not a.get("is_ai")]
    result = (ai_arts + other_arts)[:MAX_ITEMS_PER_SOURCE]
    return [{"title": a["title"], "url": a["url"], "summary": ""} for a in result]


PARSERS = {
    "36kr": parse_36kr,
    "qbitai": parse_qbitai,
    "yicai": parse_yicai,
    "linkshop": parse_linkshop,
    "huxiu": parse_huxiu,
}


# ---------------------------------------------------------------------------
# 摘要提取（从文章详情页）
# ---------------------------------------------------------------------------
def extract_article_summary(html: str, existing_summary: str = "") -> str:
    """从文章详情页提取一句话摘要"""
    if existing_summary and len(existing_summary) >= 20:
        s = re.sub(r"\s+", " ", existing_summary).strip()
        return s[:100] + ("…" if len(s) > 100 else "")

    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")

    # 优先 meta description / og:description
    for attr in ({"name": "description"}, {"property": "og:description"}):
        meta = soup.find("meta", attrs=attr)
        if meta and meta.get("content"):
            s = meta["content"].strip()
            if len(s) >= 15:
                return s[:120] + ("…" if len(s) > 120 else "")

    # 正文首段
    for selector in (
        "div.article-content p",
        "div.post-content p",
        "div.entry-content p",
        "article p",
        ".content p",
        "p",
    ):
        for p in soup.select(selector):
            text = p.get_text(strip=True)
            if len(text) >= 20:
                return text[:120] + ("…" if len(text) > 120 else "")

    return ""


# ---------------------------------------------------------------------------
# Markdown 生成
# ---------------------------------------------------------------------------
def generate_front_matter(date_str: str) -> str:
    return f"""---
layout: single-with-ga
classes: wide
title: "{date_str} AI 行业重要进展"
date: {date_str} 08:00:00 +0800
categories: ai-news
tags: [AI, 人工智能, 科技]
---

"""


def generate_markdown_body(articles: List[NewsArticle], date_str: str) -> str:
    """生成按来源分组的段落式汇总 Markdown"""
    parts: List[str] = []

    parts.append(f"## {date_str} AI 行业动态")
    parts.append("")
    parts.append(
        f"> 本文汇总来自 **36氪、量子位、第一财经、联商网、虎嗅** 的 AI 行业重要进展，"
        f"共 {len(articles)} 条资讯。"
    )
    parts.append("")
    parts.append("---")
    parts.append("")

    # 按来源分组，保持来源原始顺序
    source_order = [s["name"] for s in SOURCES]
    source_groups: dict = {}
    for art in articles:
        source_groups.setdefault(art.source, []).append(art)

    for src_name in source_order:
        items = source_groups.get(src_name, [])
        if not items:
            continue
        parts.append(f"### 📰 {src_name}")
        parts.append("")
        for art in items:
            parts.append(f"**[{art.title}]({art.url})**")
            parts.append("")
            if art.summary:
                parts.append(f"{art.summary}")
                parts.append("")
        parts.append("---")
        parts.append("")

    parts.append(
        "*数据来源：[36氪](https://36kr.com) · "
        "[量子位](https://www.qbitai.com) · "
        "[第一财经](https://www.yicai.com) · "
        "[联商网](http://www.linkshop.com.cn) · "
        "[虎嗅](https://www.huxiu.com)*"
    )

    return "\n".join(parts)


def write_post(output_dir: Path, date_str: str, articles: List[NewsArticle]) -> Path:
    filename = output_dir / f"{date_str}-ai-news.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    body = generate_markdown_body(articles, date_str)
    full_content = generate_front_matter(date_str) + body
    filename.write_text(full_content, encoding="utf-8")
    logger.info("已生成: %s", filename)
    return filename


# ---------------------------------------------------------------------------
# 邮件发送
# ---------------------------------------------------------------------------
def build_email_content(articles: List[NewsArticle], date_str: str) -> tuple[str, str]:
    """构建邮件纯文本和 HTML 正文"""
    source_order = [s["name"] for s in SOURCES]
    source_groups: dict = {}
    for art in articles:
        source_groups.setdefault(art.source, []).append(art)

    # 纯文本
    text_lines = [f"{date_str} AI行业新闻", "=" * 40, ""]
    for src in source_order:
        items = source_groups.get(src, [])
        if not items:
            continue
        text_lines.append(f"【{src}】")
        for art in items:
            summary = art.summary if art.summary else art.title
            # 一句话：摘要（截断至80字）
            one_line = summary[:80] + ("…" if len(summary) > 80 else "")
            text_lines.append(f"• {one_line}")
            text_lines.append(f"  来源：{art.url}")
            text_lines.append("")
        text_lines.append("")
    text_body = "\n".join(text_lines)

    # HTML
    html_lines = [
        "<html><body style='font-family:Arial,sans-serif;max-width:700px;margin:0 auto'>",
        f"<h2 style='color:#333'>{date_str} AI行业新闻</h2>",
        "<hr style='border:1px solid #eee'>",
    ]
    for src in source_order:
        items = source_groups.get(src, [])
        if not items:
            continue
        html_lines.append(f"<h3 style='color:#555;margin-top:20px'>📰 {src}</h3>")
        html_lines.append("<ul style='line-height:1.8'>")
        for art in items:
            summary = art.summary if art.summary else art.title
            one_line = summary[:100] + ("…" if len(summary) > 100 else "")
            html_lines.append(
                f"<li><span style='color:#222'>{one_line}</span>"
                f"<br><small style='color:#999'>来源：<a href='{art.url}' style='color:#0066cc'>{art.url[:80]}</a></small></li>"
            )
        html_lines.append("</ul>")
    html_lines.extend([
        "<hr style='border:1px solid #eee;margin-top:30px'>",
        "<p style='color:#999;font-size:12px'>数据来源：36氪 · 量子位 · 第一财经 · 联商网 · 虎嗅</p>",
        "</body></html>",
    ])
    html_body = "\n".join(html_lines)
    return text_body, html_body


def send_email(articles: List[NewsArticle], date_str: str) -> bool:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    email_to = os.environ.get("EMAIL_TO", "")

    if not all([smtp_user, smtp_pass, email_to]):
        logger.warning("邮件配置不完整，跳过发送（需设置 SMTP_USER/SMTP_PASS/EMAIL_TO）")
        return False

    subject = f"{date_str} AI行业新闻"
    text_body, html_body = build_email_content(articles, date_str)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [email_to], msg.as_string())
        logger.info("邮件已发送至 %s，主题：%s", email_to, subject)
        return True
    except Exception as e:
        logger.error("邮件发送失败: %s", e)
        return False


# ---------------------------------------------------------------------------
# 异步爬虫
# ---------------------------------------------------------------------------
class AINewsCrawler:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        # 忽略 SSL 证书验证（用于联商网等 SSL 证书不受信任的站点）
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    async def _fetch(
        self,
        session: aiohttp.ClientSession,
        url: str,
        encoding: str = "utf-8",
        ssl_verify: bool = True,
    ) -> str:
        """带重试和信号量的 HTTP GET"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self.semaphore:
                    await asyncio.sleep(random.uniform(*BASE_DELAY))
                    ssl_param = True if ssl_verify else self.ssl_ctx
                    async with session.get(
                        url, timeout=REQUEST_TIMEOUT, ssl=ssl_param
                    ) as resp:
                        if resp.status != 200:
                            raise ValueError(f"HTTP {resp.status} for {url}")
                        raw = await resp.read()
                        return raw.decode(encoding, errors="replace")
            except Exception as exc:
                if attempt == MAX_RETRIES:
                    logger.error("抓取失败 %s: %s", url, exc)
                    return ""
                wait = 2 ** attempt + random.random()
                logger.warning("第 %d 次重试 %s（原因: %s）", attempt, url, exc)
                await asyncio.sleep(wait)
        return ""

    async def crawl_source(
        self, session: aiohttp.ClientSession, source: dict
    ) -> List[NewsArticle]:
        name = source["name"]
        url = source["url"]
        home = source["home"]
        src_type = source["type"]
        encoding = source.get("encoding", "utf-8")
        # 联商网用 HTTP，不需要 SSL；但 aiohttp 对 HTTP 不传 ssl 参数
        ssl_verify = url.startswith("https://")

        logger.info("抓取 %s ...", name)
        html = await self._fetch(session, url, encoding=encoding, ssl_verify=ssl_verify)
        if not html:
            logger.warning("%s 列表页抓取失败", name)
            return []

        parser = PARSERS.get(src_type)
        raw_articles = parser(html, home) if parser else []

        if not raw_articles:
            logger.warning("%s 未解析到文章", name)
            return []

        logger.info("%s 解析到 %d 条文章", name, len(raw_articles))

        # 并发获取摘要（仅当现有摘要不足时）
        articles: List[NewsArticle] = []
        fetch_tasks = []
        for raw in raw_articles[:MAX_ITEMS_PER_SOURCE]:
            if raw.get("summary") and len(raw["summary"]) >= 20:
                fetch_tasks.append(None)  # 不需要抓
            else:
                fetch_tasks.append(
                    self._fetch(session, raw["url"], encoding=encoding, ssl_verify=ssl_verify)
                )

        # 执行需要抓取的任务
        detail_htmls = []
        for i, task in enumerate(fetch_tasks):
            if task is None:
                detail_htmls.append("")
            else:
                detail_htmls.append(await task)

        for i, raw in enumerate(raw_articles[:MAX_ITEMS_PER_SOURCE]):
            summary = extract_article_summary(detail_htmls[i], raw.get("summary", ""))
            articles.append(
                NewsArticle(
                    title=raw["title"],
                    url=raw["url"],
                    summary=summary,
                    source=name,
                    source_url=home,
                )
            )

        return articles

    async def run(self) -> List[NewsArticle]:
        """并发抓取所有来源"""
        connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, force_close=False)
        async with aiohttp.ClientSession(
            headers=HEADERS, connector=connector, trust_env=True
        ) as session:
            tasks = [self.crawl_source(session, src) for src in SOURCES]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_articles: List[NewsArticle] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("某来源抓取异常: %s", result)
            elif isinstance(result, list):
                all_articles.extend(result)

        logger.info("共抓取到 %d 条 AI 行业新闻", len(all_articles))
        return all_articles


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    now = datetime.now(TZ_SHANGHAI)
    date_str = now.strftime("%Y-%m-%d")
    output_dir = Path(__file__).resolve().parent.parent / "_posts"
    target_file = output_dir / f"{date_str}-ai-news.md"

    if target_file.exists():
        logger.info("今日文章已存在: %s，跳过", target_file.name)
        return 0

    logger.info("开始抓取 %s 的 AI 行业新闻", date_str)
    crawler = AINewsCrawler()
    articles = asyncio.run(crawler.run())

    if not articles:
        logger.error("未抓取到任何新闻，退出")
        return 1

    write_post(output_dir, date_str, articles)
    send_email(articles, date_str)
    logger.info("完成，共处理 %d 条新闻", len(articles))
    return 0


if __name__ == "__main__":
    sys.exit(main())

