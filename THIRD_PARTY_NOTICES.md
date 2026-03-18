# THIRD_PARTY_NOTICES

本文档用于记录 `D:\ABUMeta` 开源演示版在发布前审计到的第三方依赖、第三方服务、外部数据来源与开源注意事项。

## 1. 审计结论

- 当前 `D:\ABUMeta` 主要是对主开发仓 `D:\Agent` 的精简副本。
- 本次审计未发现明显的第三方源码整段 vendoring 证据。
- 当前项目确实依赖多个第三方开源库。
- 当前项目还支持第三方在线服务和第三方站点抓取，但开源版未附带这些站点的抓取结果与历史数据。

## 2. 已实际使用的基础依赖

下列依赖已经进入演示版基础运行链，通常可以保留，不必优先替换。

| 项目 | 用途 | 代码证据 | 许可证线索 | 开源建议 |
|---|---|---|---|---|
| `requests` | HTTP 请求 | `src/execution/page_fetcher.py:9` | 已核到 `Apache-2.0` | 可直接发布，保留说明 |
| `beautifulsoup4` | HTML 解析 | `src/execution/page_fetcher.py:10` | 已核到 `MIT` | 可直接发布，保留说明 |
| `lxml` | HTML 解析后端 | `src/execution/lead_capture.py:129` | 本次未单独核许可证 | 可直接发布，建议随依赖清单说明 |
| `sqlalchemy` | SQLite/ORM | `src/memory/storage.py:6` | 已核到 `MIT` | 可直接发布 |
| `pydantic` | 结构化数据模型 | `src/social/trade_warning.py:11` | 本次需上游再确认 | 可直接发布，建议补许可证确认 |
| `pydantic-settings` | 配置加载 | `config/settings.py:5` | 本次需上游再确认 | 可直接发布，建议补许可证确认 |
| `loguru` | 日志 | `src/utils/logger.py:3` | 已核到 `MIT` | 可直接发布 |
| `psutil` | 资源感知 | `src/main_production.py:10` | 已核到 `BSD-3-Clause` | 可直接发布 |
| `numpy` | 导图与数值处理 | `src/utils/map_exporter.py:9` | 本次需上游再确认 | 可直接发布，建议补许可证确认 |
| `APScheduler` | 调度 | `src/chronos/scheduler.py:7` | 已核到 `MIT` | 可直接发布 |
| `python-dotenv` | 环境变量加载 | `config/settings.py` 间接使用 | 已核到 `BSD-3-Clause` | 可直接发布 |
| `python-dateutil` | 时间处理 | 依赖链使用 | 本次仅核到项目源，未做许可证定稿 | 可直接发布，建议补确认 |
| `openpyxl` | Excel 导入 | `src/data_connector/trade_adapter.py:350` | 已核到 `MIT` | 可直接发布 |

## 3. 可选增强依赖

下列依赖已被项目接入，但在开源演示版中被降为可选增强能力，不作为最小运行前提。

| 项目 | 用途 | 代码证据 | 来源 | 审计判断 | 建议 |
|---|---|---|---|---|---|
| `scrapling` | 抓取后端 | `src/execution/page_fetcher.py:14` | `D4Vinci/Scrapling` | 已实际使用 | 建议保留为可选依赖，后续可自研替换 |
| `playwright` | 动态页面抓取 | `src/execution/lead_capture.py:304` | `Microsoft/playwright-python` | 已实际使用 | 建议保留为可选依赖，并在 README 说明安装浏览器步骤 |
| `chromadb` | 向量后端 | `src/memory/retrieval.py:129` | `chroma-core/chroma` | 已接入，但演示版可降级 | 建议保留为可选依赖 |
| `sentence-transformers` | 向量模型 | `src/memory/retrieval.py:131` | `UKPLab/sentence-transformers` | 已接入，但演示版可降级 | 建议保留为可选依赖 |
| `torch` | GPU 与模型运行 | `src/memory/retrieval.py:14` | 上游需再确认 | 已接入，但演示版已允许缺失降级 | 建议保留为可选依赖 |
| `docker` | 影子沙盒 SDK | `src/execution/sandbox.py:230` | 上游需再确认 | 演示版不强制依赖 | 建议保留为可选依赖 |

## 4. 第三方在线服务

| 项目 | 用途 | 代码证据 | 开源建议 |
|---|---|---|---|
| OpenAI 兼容聊天接口 | 蒸馏、页面策略、发现搜索词 | `src/memory/distiller.py:352`、`src/execution/site_onboarding.py:243`、`src/skills/web_explorer.py:158` | 允许保留，但必须通过 `.env.example` 说明，不能提交真实密钥 |
| Anthropic 配置项 | 预留服务配置 | `config/settings.py:25` | 当前未见主链直接调用，最小开源版可保留为预留项 |

## 5. 第三方站点与外部内容来源

下列内容属于外部网站或外部平台来源，不等于第三方代码，但会影响开源内容边界。

| 来源 | 代码证据 | 风险 | 开源建议 |
|---|---|---|---|
| TradeIndia / ExportersIndia | `config/site_templates.json` | 抓取结果可能涉及第三方内容 | 代码可开源，抓取结果和衍生数据不应随仓库分发 |
| DuckDuckGo 搜索 | `src/skills/web_explorer.py:279` | 搜索结果缓存不适合打包 | 仅开源搜索逻辑，不附带结果数据 |
| GitHub Trending | `src/learning/github_monitor.py:10` | 抓取结果和缓存不适合打包 | 保留逻辑，不附带抓取结果 |
| 新闻/资讯站点 | `src/learning/news_parser.py:126` | 正文、摘要与缓存可能涉及外部内容 | 保留解析逻辑，不附带正文与运行数据 |

## 6. 本次未发现的高风险项

本次审计未发现以下明显证据：

- Git submodule
- `.gitmodules`
- `vendor/` 或 `third_party/` 目录
- 明显标记为 “Copied from ...” 的大段第三方源码
- 明显整段附带第三方版权头的源码文件

因此，本次更准确的结论是：

- 当前项目依赖多个第三方开源项目
- 但未发现明显的第三方源码直接整段搬运进仓库的证据

## 7. 开源发布建议

### 7.1 可以不改直接发布的部分

- 基础 Python 运行依赖
- 数据库与配置加载基础设施
- 日志、调度、HTML 解析、HTTP 请求层

### 7.2 建议保留但做成可选依赖的部分

- `scrapling`
- `playwright`
- `chromadb`
- `sentence-transformers`
- `torch`
- `docker`

### 7.3 不应随开源版带出的部分

- 主仓数据库
- 历史抓取结果
- 运行日志
- 缓存
- 外部站点正文或衍生摘要
- 报告产物

## 8. 后续建议动作

- 增加正式 `LICENSE`
- 增加发布版依赖与许可证复核表
- 对 `playwright`、`pydantic`、`numpy`、`torch`、`chromadb` 做一次上游许可证二次确认
- 若后续追求更自主，优先替换 `scrapling`
