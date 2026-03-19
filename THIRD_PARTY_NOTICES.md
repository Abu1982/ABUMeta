# THIRD_PARTY_NOTICES

This document records the third-party dependencies, external services, and public-distribution boundaries used by the ABUMeta open-source demo.

本文档记录 ABUMeta 开源演示版当前使用到的第三方依赖、外部服务，以及公开分发时应注意的边界。

## 1. Summary / 审计结论

- `EN` The repository depends on multiple third-party open-source projects, but no clear evidence of directly vendored third-party source dumps was found during review.
- `中文` 当前仓库依赖多个第三方开源项目，但审计中未发现明显的第三方源码整段 vendoring 痕迹。
- `EN` The public repository may publish code and static configuration, but should not ship runtime logs, reports, caches, crawl results, databases, or model caches.
- `中文` 当前公开仓可以发布代码与静态配置，但不应附带运行日志、报告、缓存、抓取结果、数据库或模型缓存。
- `EN` Core runtime dependencies are mainly under permissive licenses such as `MIT`, `BSD-3-Clause`, and `Apache-2.0`, which are generally compatible with public release and commercial use.
- `中文` 当前基础运行依赖主要采用 `MIT`、`BSD-3-Clause`、`Apache-2.0` 等宽松许可证，通常可用于公开发布与商用。
- `EN` The highest-risk boundaries are external crawling terms, model-weight licensing, browser binary distribution, and Docker image/runtime policy.
- `中文` 当前更需要重点关注的是外部抓取条款、模型权重许可、浏览器二进制分发边界，以及 Docker 镜像与运行环境边界。

## 2. Core Runtime Dependencies / 基础运行依赖

These dependencies are part of the core runtime path and may be distributed with the source repository, provided their original license terms and attribution notices are preserved.

下列依赖已进入基础运行链，可随源码一起公开，但应保留各自许可证与版权声明。

| Package | Purpose | Code Evidence | License Review | Distribution Assessment |
|---|---|---|---|---|
| `requests` | HTTP 请求 | `src/execution/page_fetcher.py:9` | `Apache-2.0` | 可公开，可商用 |
| `beautifulsoup4` | HTML 解析 | `src/execution/page_fetcher.py:10` | `MIT` | 可公开，可商用 |
| `lxml` | HTML 解析后端 | `src/execution/lead_capture.py:129` | `BSD-3-Clause` | 可公开，可商用 |
| `sqlalchemy` | SQLite/ORM | `src/memory/storage.py:6` | `MIT` | 可公开，可商用 |
| `pydantic` | 结构化数据模型 | `src/social/trade_warning.py:11` | `MIT` | 可公开，可商用 |
| `pydantic-settings` | 配置加载 | `config/settings.py:5` | `MIT` | 可公开，可商用 |
| `loguru` | 日志 | `src/utils/logger.py:3` | `MIT` | 可公开，可商用 |
| `psutil` | 资源感知 | `src/main_production.py:10` | `BSD-3-Clause` | 可公开，可商用 |
| `numpy` | 数值计算与导图处理 | `src/utils/map_exporter.py:9` | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | 可公开，可商用；需保留 bundled notices |
| `APScheduler` | 调度 | `src/chronos/scheduler.py:7` | `MIT` | 可公开，可商用 |
| `python-dotenv` | 环境变量加载 | `config/settings.py:11` | `BSD-3-Clause` | 可公开，可商用 |
| `python-dateutil` | 时间处理 | `requirements-demo.txt:12` | `Dual License` | 可公开，可商用；建议随分发保留上游 license |
| `openpyxl` | Excel 导入 | `src/data_connector/trade_adapter.py:350` | `MIT` | 可公开，可商用 |

## 3. Optional Enhancement Dependencies / 可选增强依赖

These dependencies are integrated into the repository, but they are optional and not required for the minimal demo path.

下列依赖已接入仓库，但在开源演示版中属于可选能力，不作为最小运行前提。

| Package | Purpose | Code Evidence | License Review | Distribution Assessment |
|---|---|---|---|---|
| `scrapling` | 抓取后端 | `src/execution/page_fetcher.py:14` | `BSD-3-Clause` | 可作为可选依赖公开 |
| `playwright` | 动态页面抓取 | `src/execution/lead_capture.py:304` | `Apache-2.0` | 可作为可选依赖公开；若分发浏览器二进制需补 notice |
| `chromadb` | 向量后端 | `src/memory/retrieval.py:133` | `Apache-2.0` | 可作为可选依赖公开；建议锁版本并单独保留 notice |
| `sentence-transformers` | 向量模型接口 | `src/memory/retrieval.py:135` | `Apache-2.0` | 可作为可选依赖公开；模型权重需另审 |
| `torch` | GPU 与模型运行 | `src/memory/retrieval.py:16` | `BSD-3-Clause` | 可作为可选依赖公开；二进制与 CUDA 组件需按部署环境复核 |
| `docker` | 影子沙盒 SDK | `src/execution/sandbox.py:230` | `Apache-2.0` | 可作为可选依赖公开；镜像与 Docker Engine 需另审 |

## 4. Development and Test Dependencies / 开发与测试依赖

These dependencies are not part of the minimal runtime path, but they support the public repository's testing and contribution workflow.

下列依赖不属于最小运行链，但属于公开仓库的开发与测试工具。

| Package | Purpose | Code Evidence | License Review | Distribution Assessment |
|---|---|---|---|---|
| `pytest` | 测试框架 | `requirements-dev.txt:2` | `MIT` | 可公开，可商用 |
| `pytest-asyncio` | 异步测试支持 | `requirements-dev.txt:3` | `Apache-2.0` | 可公开，可商用 |

## 5. Focused Review Notes / 重点复核结论

### 5.1 `numpy`

- `EN` Current requirement is `numpy>=1.26.4`. Runtime metadata reports a compound license expression: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.
- `中文` 当前版本使用 `numpy>=1.26.4`，运行环境元数据显示许可证表达式为 `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`。
- `EN` Conclusion: public release and commercial use are generally allowed, but do not reduce NumPy to a single-license shorthand in your notices.
- `中文` 结论：允许公开与商用，但分发时不应只写“NumPy = BSD”，最好保留上游自带 license files 或在 notice 中明确其 bundled components。

### 5.2 `playwright`

- `EN` Current requirement is `playwright==1.58.0`, with active usage at `src/execution/lead_capture.py:304`.
- `中文` 当前版本使用 `playwright==1.58.0`，代码证据为 `src/execution/lead_capture.py:304`。
- `EN` The upstream repository `microsoft/playwright-python` is licensed under `Apache-2.0`.
- `中文` 上游仓库 `microsoft/playwright-python` 的许可证为 `Apache-2.0`。
- `EN` Conclusion: the Python package itself is suitable for public release and commercial use, but browser binaries and packaged browser installs require additional notice review.
- `中文` 结论：Python 包本身可公开、可商用；如果后续分发浏览器安装产物或打包浏览器二进制，需要额外检查浏览器组件的 notice 与分发规则。

### 5.3 `chromadb`

- `EN` Current requirement is `chromadb==0.4.22`, with code usage at `src/memory/retrieval.py:133`.
- `中文` 当前版本使用 `chromadb==0.4.22`，代码证据为 `src/memory/retrieval.py:133`。
- `EN` The upstream repository `chroma-core/chroma` is licensed under `Apache-2.0`.
- `中文` 上游仓库 `chroma-core/chroma` 的许可证为 `Apache-2.0`。
- `EN` Conclusion: it can remain an optional dependency for public and commercial use, but production distribution should add version pinning, notices, and a dedicated security review.
- `中文` 结论：可作为可选依赖公开与商用；若后续将其作为生产数据库服务分发，建议补充版本锁定、NOTICE 与安全审计。

### 5.4 `docker`

- `EN` Current requirement is `docker==7.1.0`, with code usage at `src/execution/sandbox.py:230`.
- `中文` 当前版本使用 `docker==7.1.0`，代码证据为 `src/execution/sandbox.py:230`。
- `EN` The upstream repository `docker/docker-py` is licensed under `Apache-2.0`.
- `中文` 上游仓库 `docker/docker-py` 的许可证为 `Apache-2.0`。
- `EN` Conclusion: the Python SDK itself is suitable for public release and commercial use, but the full deployment story also depends on Docker Engine, base images, image layers, and host permissions.
- `中文` 结论：Python SDK 本身可公开与商用；但真实部署还涉及 Docker Engine、基础镜像、镜像层内容与宿主机权限，不应仅凭 SDK 许可证直接放行整体部署方案。

### 5.5 `pytest` 与 `pytest-asyncio`

- `EN` `pytest` is `MIT` and `pytest-asyncio` is `Apache-2.0`.
- `中文` `pytest` 为 `MIT`，`pytest-asyncio` 为 `Apache-2.0`。
- `EN` Conclusion: both are suitable for public repositories, internal commercial development, and CI use. They are development dependencies and do not change the commercial-use assessment of the main application.
- `中文` 结论：二者均可用于公开仓库、内部商业开发与 CI；它们属于开发依赖，不影响主程序是否可商用的判断。

## 6. External Service Interfaces / 第三方在线服务

| Service | Purpose | Code Evidence | Public Release Guidance |
|---|---|---|---|
| OpenAI 兼容聊天接口 | 蒸馏、页面策略、发现搜索词 | `src/memory/distiller.py:352`、`src/execution/site_onboarding.py:243`、`src/skills/web_explorer.py:158` | 允许保留接口接入逻辑，但必须通过 `.env.example` 提供占位配置，不能提交真实密钥 |
| Anthropic 配置项 | 预留服务配置 | `config/settings.py:25` | 当前未见主链直接调用，可保留为预留配置 |

- `EN` Commercial use of third-party APIs depends not only on repository licensing, but also on API contracts, billing terms, and output policies.
- `中文` 第三方 API 是否可商用，不只取决于代码仓许可证，还取决于服务合同、计费条款与输出使用政策。

## 7. External Content Boundaries / 第三方站点与外部内容边界

The following sources are not third-party code, but they still affect public-release and commercial-use boundaries.

下列内容属于外部网站或外部平台来源，不等于第三方代码，但会影响开源与商用边界。

| Source | Code Evidence | Risk | Public Release Guidance |
|---|---|---|---|
| TradeIndia / ExportersIndia | `config/site_templates.json` | 抓取结果可能涉及第三方内容与站点条款 | 代码可开源，但抓取结果和衍生数据不应随仓库分发 |
| DuckDuckGo 搜索 | `src/skills/web_explorer.py:279` | 搜索结果缓存不适合打包 | 仅开源搜索逻辑，不附带结果数据 |
| GitHub Trending | `src/learning/github_monitor.py:10` | 抓取结果和缓存不适合打包 | 保留逻辑，不附带抓取结果 |
| 新闻/资讯站点 | `src/learning/news_parser.py:126` | 正文、摘要与缓存可能涉及外部内容 | 保留解析逻辑，不附带正文与运行数据 |

## 8. High-Risk Items Not Found / 未发现的高风险项

The review did not find clear evidence of the following high-risk patterns:

本次审计未发现以下明显证据：

- Git submodule
- `.gitmodules`
- `vendor/` 或 `third_party/` 目录
- 明显标记为 “Copied from ...” 的大段第三方源码
- 明显整段附带第三方版权头的源码文件

- `EN` More precisely: the repository uses multiple third-party open-source dependencies, but there is no clear evidence of directly copied third-party source blocks being imported wholesale into the repository.
- `中文` 因此，更准确的结论是：当前仓库依赖多个第三方开源项目，但未发现明显的第三方源码直接整段搬运进仓库的证据。

## 9. Content That Must Not Ship With the Public Repo / 不应随开源版分发的内容

- 数据库与向量库文件
- 历史抓取结果
- 运行日志
- 行动账本与治理账本
- 缓存与报告产物
- 外部站点正文、摘要与衍生索引
- 模型缓存与浏览器安装产物

## 10. Release Guidance / 发布建议

- `EN` Keep `LICENSE` and this file in the repository as public-facing legal and compliance guidance.
- `中文` 保留 `LICENSE` 与本文件，作为仓库级公开说明。
- `EN` Runtime outputs such as `data/action_journal.jsonl`, `data/reports/*.md`, `data/reports/*.json*`, and `data/logs/*.log` should remain ignored by Git.
- `中文` 运行期生成的 `data/action_journal.jsonl`、`data/reports/*.md`、`data/reports/*.json*`、`data/logs/*.log` 应继续被 `.gitignore` 屏蔽。
- `EN` If you add or change third-party dependencies later, update this file in the same change.
- `中文` 若后续新增或调整第三方依赖，必须同步更新本文件。
- `EN` If you later ship browser binaries, model weights, or Docker images, add dedicated notices and separate commercial-use guidance.
- `中文` 若后续打包浏览器、模型或镜像，需要额外补充对应的 NOTICE 与商用边界说明。
