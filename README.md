# news-digest

自动抓取央视《新闻联播》每日概要，生成 Jekyll 博客文章并部署到 GitHub Pages。

基于 [Minimal Mistakes](https://github.com/mmistakes/minimal-mistakes) 主题，纯 Markdown 渲染，无自定义 CSS。

## 功能特性

- **自动抓取**：GitHub Actions 定时任务，每天北京时间 21:00 和 7:00 自动运行
- **并发爬取**：基于 `asyncio` + `aiohttp`，多日新闻并发抓取
- **结构化解析**：将新闻概要拆分为编号、标题、子条目三级结构
- **表格 + 列表**：生成包含概览表格和详细列表的 Markdown 文章
- **增量更新**：跳过已抓取的日期，避免重复

## 项目结构

```
news-digest/
├── _config.yml           # Jekyll 站点配置
├── _posts/               # 生成的新闻 Markdown 文章
├── _layouts/
│   └── single-with-ga.html   # 文章布局（含 Google Analytics）
├── _includes/
│   └── analytics.html    # GA 跟踪代码
├── _data/
│   └── subsites.yml      # 子站点元数据
├── scripts/
│   └── news_crawler.py   # 爬虫主脚本
├── .github/workflows/
│   └── deploy.yml        # CI/CD 定时任务
├── Gemfile
└── index.html            # 首页
```

## 本地开发

### 前置依赖

- Ruby >= 3.0
- Python >= 3.10
- Bundler

### 运行爬虫

```bash
# 安装 Python 依赖
pip install aiohttp beautifulsoup4 lxml

# 抓取最近 7 天的新闻
python scripts/news_crawler.py 7
```

### 本地预览 Jekyll 站点

```bash
bundle install
bundle exec jekyll serve
```

访问 `http://localhost:4000/news-digest/` 查看效果。

## 数据来源

- [央视网新闻联播](https://tv.cctv.com/lm/xwlb/index.shtml) — 每日新闻概要

## 许可

MIT
