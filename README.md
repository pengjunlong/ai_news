# AI 行业日报

自动抓取每日 AI 行业重要进展，生成 Jekyll 博客文章并部署到 GitHub Pages，同时发送邮件摘要。

基于 [Minimal Mistakes](https://github.com/mmistakes/minimal-mistakes) 主题，纯 Markdown 渲染。

## 功能特性

- **自动抓取**：GitHub Actions 定时任务，每天北京时间 08:30 自动运行
- **多来源聚合**：36氪、量子位、第一财经、联商网、虎嗅
- **并发爬取**：基于 `asyncio` + `aiohttp`，多来源并发抓取
- **双路输出**：
  - 每日 Markdown 文章（段落式汇总，存入 `_posts/`，发布为网站 post）
  - 邮件摘要（每条新闻一句话概括 + 来源链接，标题格式：`YYYY-MM-DD AI行业新闻`）
- **增量更新**：跳过已抓取的日期，避免重复

## 项目结构

```
ai_news/
├── _config.yml           # Jekyll 站点配置
├── _posts/               # 生成的 AI 新闻 Markdown 文章
├── _layouts/
│   └── single-with-ga.html   # 文章布局（含 Google Analytics）
├── _includes/
│   └── analytics.html    # GA 跟踪代码
├── _data/
│   └── subsites.yml      # 子站点元数据
├── scripts/
│   └── ai_news_crawler.py  # AI新闻爬虫主脚本（含邮件发送）
├── .github/workflows/
│   └── deploy.yml        # CI/CD 定时任务
├── Gemfile
└── index.html            # 首页
```

## 数据来源

| 来源 | 抓取入口 | 备注 |
|------|----------|------|
| [36氪](https://36kr.com/information/AI) | AI 频道 | |
| [量子位](https://www.qbitai.com/) | 首页 | 按文章 URL 模式匹配 |
| [第一财经](https://www.yicai.com/news/?tag=AI) | AI 标签 | |
| [联商网](http://www.linkshop.com.cn/) | 首页新闻 | HTTP + GBK 编码，AI 关键词优先 |
| [虎嗅](https://www.huxiu.com/) | 首页 | AI 关键词过滤，替代亿邦动力（反爬） |

## 配置邮件通知

在 GitHub 仓库 Settings → Secrets and variables → Actions 中添加以下 Secrets：

| Secret 名称 | 说明 |
|------------|------|
| `SMTP_HOST` | SMTP 服务器地址（如 `smtp.gmail.com`） |
| `SMTP_PORT` | SMTP 端口（如 `587`） |
| `SMTP_USER` | 发件邮箱地址 |
| `SMTP_PASS` | 邮箱密码或应用专用密码 |
| `EMAIL_TO` | 收件邮箱地址 |

## 本地开发

### 前置依赖

- Ruby >= 3.0
- Python >= 3.10
- Bundler

### 运行爬虫

```bash
# 安装 Python 依赖
pip install aiohttp beautifulsoup4 lxml

# 抓取今日 AI 新闻
python scripts/ai_news_crawler.py
```

### 本地预览 Jekyll 站点

```bash
bundle install
bundle exec jekyll serve
```

访问 `http://localhost:4000/ai_news/` 查看效果。

## 许可

MIT
