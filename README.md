# ABUMeta 开源演示版

一个可演示的类人自治 Agent 开源副本，保留记忆、心理、决策、抓取与治理骨架，适合作为 AGI 工程化原型与研究型演示项目。

## 当前版本

- 展示版本：`v1.0.04`
- 内部版本：`1.0.4`
- 版本文件：`VERSION`、`version.json`、`src/__init__.py`
- 开源许可证：`MIT`，见 `LICENSE`

## 目标

- 保留核心源码结构，方便阅读与二次开发
- 保留最小可演示能力，避免依赖私有运行数据
- 不包含数据库、缓存、日志、报告和历史运行产物

## 快速开始

```powershell
./一键安装依赖.bat
./运行演示.bat
```

## 当前版本定位

- 本目录是独立导出的演示副本
- 与原始私有开发环境隔离
- 适合放到 GitHub 作为公开演示项目

## 目录说明

- `src/`：核心源码副本
- `config/`：必要配置副本
- `docs/`：精简文档
- `scripts/`：开源演示脚本
- `tests/`：精简 smoke 测试
- `data/`：只保留运行所需空目录和 schema，不带历史数据

## 一键安装

双击运行根目录下：

- `一键安装依赖.bat`

它会自动：

- 创建 `.venv`
- 安装 `requirements-demo.txt` 基础演示依赖

如果你需要动态抓取、向量检索或浏览器增强能力，再执行：

- `安装可选增强依赖.bat`

如果你需要运行测试或参与开发，再执行：

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

## 一键演示

安装完成后，可直接运行：

- `运行演示.bat`

它会执行：

- `scripts/demo_showcase.py`

展示以下能力：

- 金库统计
- 心理引擎状态
- 决策引擎裁决
- 页面获取器的页面分析能力

## 运行测试

```powershell
.\.venv\Scripts\python -m pytest tests
```

## 开源说明

本演示版刻意移除了以下内容：

- 历史数据库
- 原始抓取结果
- 运行日志
- 缓存
- 报告产物
- 历史状态快照

这意味着：

- 它可以完整运行“演示版核心能力”
- 但不会复现私有环境中的长期记忆和历史运行上下文

## 第三方依赖说明

依赖已拆分为三层：

- `requirements-demo.txt`
- `requirements-optional.txt`
- `requirements-dev.txt`

基础演示依赖保留常见 Python 组件，如：

- `requests`
- `beautifulsoup4`
- `sqlalchemy`
- `pydantic`
- `loguru`

可选增强依赖单独拆分到 `requirements-optional.txt`：

- `scrapling`
- `playwright`
- `chromadb`
- `sentence-transformers`
- `torch`
- `docker`

使用外部 API 或浏览器增强能力时：

- 请先复制 `.env.example` 为本地环境文件并自行填写密钥
- 不要提交真实 API Key、Cookie、Session、日志、报告或抓取结果
- 如需商用，请同时阅读 `THIRD_PARTY_NOTICES.md` 中的许可证与外部内容边界说明

## 审核文件

- 第三方依赖与开源前审计：`THIRD_PARTY_NOTICES.md`
- 当前版本变更记录：`更新日志.MD`

## GitHub 社区文件

- 贡献指南：`CONTRIBUTING.md`
- 安全策略：`SECURITY.md`
- Issue 模板：`.github/ISSUE_TEMPLATE/`
- Pull Request 模板：`.github/PULL_REQUEST_TEMPLATE.md`

## 注意

- Windows 快捷方式仅保留本地使用，不建议作为公开仓库内容提交
- 运行过程中生成的日志、报告、行动账本和缓存默认不应进入 Git
