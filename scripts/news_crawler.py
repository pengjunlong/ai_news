#!/usr/bin/env python3
"""
新闻联播摘要爬虫 — 从央视网抓取每日新闻联播概要，生成 Jekyll Markdown 文章。
优化点：
  • asyncio + aiohttp 并发抓取多日新闻
  • 连接池复用、自动重试、指数退避
  • 用 lxml 加速 HTML 解析
  • 纯 Markdown 原生语法输出，不依赖自定义 CSS
  • 去除冗余编码处理
"""

import asyncio
import logging
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Referer": "https://tv.cctv.com/lm/xwlb/index.shtml",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
}

# 并发上限 & 重试配置
MAX_CONCURRENT = 5
MAX_RETRIES = 3
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
BASE_DELAY = (0.5, 2.0)  # 并发时的最小/最大随机延迟(秒)

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class NewsItem:
    index: int
    title: str
    subitems: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def get_target_date(delta_days: int) -> datetime:
    """返回要抓取的目标日期（北京时间）。"""
    now = datetime.now(TZ_SHANGHAI)
    # 新闻联播约 19:00 播出，23 点前用前一天的
    if now.hour < 23:
        return (now - timedelta(days=delta_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return (now - timedelta(days=delta_days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def list_url_for(date: datetime) -> str:
    """根据日期生成列表页 URL。"""
    return f"https://tv.cctv.com/lm/xwlb/day/{date.strftime('%Y%m%d')}.shtml"


def file_date_str(date: datetime) -> str:
    return date.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------
def parse_main_page(html: str) -> str:
    """从列表页 HTML 中提取第一条新闻详情页链接。"""
    soup = BeautifulSoup(html, "lxml")
    # 策略 1: 匹配 /YYYY/ 路径下的 .shtml 链接
    year_pattern = re.compile(r"/\d{4}/.*\.shtml$")
    link = soup.find("a", href=lambda h: h and year_pattern.search(h))
    if link and link.get("href"):
        return link["href"]
    # 策略 2: 通过 CSS class
    link = soup.select_one("ul.content_list > li:first-child > a")
    if link and link.get("href"):
        return link["href"]
    # 策略 3: clickStyle
    li = soup.find("li", class_="clickStyle")
    if li:
        link = li.find("a")
        if link and link.get("href"):
            return link["href"]
    raise ValueError("未找到有效的详情页链接")


def parse_detail_page(html: str) -> str:
    """从详情页 HTML 中提取新闻概要纯文本。"""
    soup = BeautifulSoup(html, "lxml")
    # 按优先级尝试多个选择器
    for selector in (
        {"name": "div", "class_": "video_brief"},
        {"name": "div", "class_": "cnt_bd"},
        {"name": "div", "attrs": {"id": "content_area"}},
    ):
        div = soup.find(**selector)
        if div:
            return div.get_text(strip=True, separator="\n")
    raise ValueError("未找到有效内容区域")


def parse_news_content(text: str) -> List[NewsItem]:
    """将概要纯文本拆分为结构化的 NewsItem 列表。"""
    items: List[NewsItem] = []
    current: Optional[NewsItem] = None

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # 主条目：以 "1." "2." 等数字点开头
        if m := re.match(r"^(\d+)\.\s*(.*)", line):
            if current:
                items.append(current)
            current = NewsItem(index=int(m.group(1)), title=m.group(2).rstrip("；;"))
            continue

        # 子条目：以 "（1）" 等中文括号数字开头
        if m := re.match(r"^（(\d+)）\s*(.*)", line):
            if current:
                current.subitems.append(m.group(2).rstrip("；;"))
            continue

        # 续行：追加到当前条目
        if current:
            if current.subitems:
                current.subitems[-1] += line
            else:
                current.title += line

    if current:
        items.append(current)
    return items


# ---------------------------------------------------------------------------
# Markdown 生成 — 纯原生语法，不依赖 notice 等 CSS class
# ---------------------------------------------------------------------------
def generate_front_matter(date_str: str) -> str:
    return f"""---
layout: single-with-ga
classes: wide
title: "{date_str} 新闻联播摘要"
date: {date_str} 19:00:00 +0800
categories: daily-news
---

"""


def generate_markdown_body(items: List[NewsItem]) -> str:
    """使用纯 Markdown 语法生成美观的新闻摘要。"""
    parts: List[str] = []

    # 概述表格
    parts.append("## 每日要闻")
    parts.append("")
    parts.append(
        "| # | 标题 | 详情 |"
    )
    parts.append(
        "|---|------|------|"
    )

    for item in items:
        subitem_text = "；".join(item.subitems) if item.subitems else "—"
        # 截断过长内容用于表格预览
        preview = subitem_text[:60] + "…" if len(subitem_text) > 60 else subitem_text
        parts.append(f"| {item.index} | {item.title} | {preview} |")

    parts.append("")
    parts.append("---")
    parts.append("")

    # 详细列表
    parts.append("## 详细内容")
    parts.append("")

    for item in items:
        parts.append(f"### {item.index}. {item.title}")
        parts.append("")
        if item.subitems:
            for sub in item.subitems:
                parts.append(f"- {sub}")
            parts.append("")

    return "\n".join(parts)


def write_post(output_dir: Path, date_str: str, content: str) -> Path:
    """将生成的 Markdown 写入 _posts 目录。"""
    filename = output_dir / f"{date_str}-news.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    full_content = generate_front_matter(date_str) + content
    filename.write_text(full_content, encoding="utf-8")
    logger.info("已生成: %s", filename)
    return filename


# ---------------------------------------------------------------------------
# 异步抓取
# ---------------------------------------------------------------------------
class AsyncCrawler:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        """带重试和信号量的 HTTP GET。"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self.semaphore:
                    await asyncio.sleep(random.uniform(*BASE_DELAY))
                    async with session.get(url, timeout=REQUEST_TIMEOUT) as resp:
                        if resp.status != 200:
                            raise ValueError(f"HTTP {resp.status} for {url}")
                        return await resp.text()
            except Exception as exc:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2 ** attempt + random.random()
                logger.warning("第 %d 次重试 %s（原因: %s）", attempt, url, exc)
                await asyncio.sleep(wait)

    async def crawl_one_day(
        self, session: aiohttp.ClientSession, date: datetime, output_dir: Path
    ) -> Optional[Path]:
        """抓取单日新闻并写入 Markdown 文件。"""
        ds = file_date_str(date)
        target_file = output_dir / f"{ds}-news.md"

        # 跳过已存在的文件
        if target_file.exists():
            logger.info("跳过已存在: %s", target_file.name)
            return None

        logger.info("开始抓取 %s 的新闻", ds)
        try:
            # 1) 列表页
            lurl = list_url_for(date)
            list_html = await self._fetch(session, lurl)
            detail_url = parse_main_page(list_html)
            logger.debug("详情页: %s", detail_url)

            # 2) 详情页
            detail_html = await self._fetch(session, detail_url)
            text = parse_detail_page(detail_html)

            # 3) 解析 + 生成
            items = parse_news_content(text)
            if not items:
                logger.warning("%s 未解析到任何新闻条目，跳过", ds)
                return None

            body = generate_markdown_body(items)
            return write_post(output_dir, ds, body)

        except Exception as exc:
            logger.error("%s 抓取失败: %s", ds, exc)
            return None

    async def run(self, days: int = 1) -> int:
        """并发抓取最近 days 天的新闻，返回成功数量。"""
        dates = [get_target_date(i) for i in range(1, days)]
        output_dir = Path(__file__).resolve().parent.parent / "_posts"

        connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, force_close=False)
        async with aiohttp.ClientSession(
            headers=HEADERS, connector=connector, trust_env=True
        ) as session:
            tasks = [
                self.crawl_one_day(session, d, output_dir) for d in dates
            ]
            results = await asyncio.gather(*tasks)

        return sum(1 for r in results if r is not None)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    logger.info("将抓取最近 %d 天的新闻", days)
    crawler = AsyncCrawler()
    count = asyncio.run(crawler.run(days))
    logger.info("完成，成功 %d 篇", count)
    return 0 if count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
