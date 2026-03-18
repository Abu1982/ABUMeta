# ABU 通用抓取工具栈

## 目标

- 不把 ABU 绑定到单一抓取工具。
- 根据站型、页面特征和失败模式，在多个抓取后端之间切换。
- 为未来接入淘宝、阿里巴巴、京东、B2B 平台、资讯站、社区站提供统一方法。

## 当前推荐工具栈

### 第一层：默认内置后端

- `requests + BeautifulSoup`
  - 适用：静态页面、轻量探测、简单列表页
- `Scrapling`
  - 适用：半结构页面、动态页面、通用页面理解
- `Scrapling DynamicFetcher`
  - 适用：需要浏览器渲染或动态加载的页面

### 第二层：建议纳入的开源能力

- `Trafilatura`（`adbar/trafilatura`）
  - 强项：正文与元数据抽取，特别适合资讯/文章页
- `Scrapy`（`scrapy/scrapy`）
  - 强项：大规模爬取、任务调度、pipeline
- `Crawl4AI`（`unclecode/crawl4ai`）
  - 强项：LLM 友好抓取与 AI 数据流
- `Crawlee`（`apify/crawlee`）
  - 强项：复杂站点和大规模 Node/TS 爬取
- `SeleniumBase`（`seleniumbase/SeleniumBase`）
  - 强项：高反爬浏览器自动化和复杂交互

## 站型 -> 后端选择原则

- `静态列表页`
  - 优先：`requests + BeautifulSoup`
- `半结构页面 / 需要更稳 DOM 理解`
  - 优先：`Scrapling`
- `动态页面 / 登录墙 / 交互站`
  - 优先：`Scrapling DynamicFetcher` 或 Playwright
- `资讯正文页`
  - 后续优先：`Trafilatura`
- `大规模多页任务`
  - 后续优先：`Scrapy` / `Crawlee`

## 导师模式下的使用规则

- 遇到站点抓取失败，不直接手工修最终产出物。
- 先判断失败层：
  - 站型识别错误
  - 后端选择错误
  - 字段策略错误
  - 噪声过滤错误
- 再升级 ABU：
  - 调整后端选择器
  - 调整字段模板族
  - 调整自动迭代策略
- 若当前工具栈仍不够，再做 GitHub 能力雷达，决定复用/借鉴/自研。

## 未来方向

- 把 `Trafilatura` 接入资讯页字段抽取链
- 把 `Scrapling` 从 probe 深入到字段级提取
- 把 GitHub 能力雷达沉淀成 ABU 固定流程
- 构建真正的多后端调度器，而不是单次条件选择
