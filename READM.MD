# ABUMeta 开源演示版

一个可演示的类人自治 Agent 开源副本，保留记忆、心理、决策、抓取与治理骨架，适合作为 AGI 工程化原型与研究型演示项目。

## 当前版本

- 展示版本：`v1.0.03`
- 内部版本：`1.0.3`
- 版本文件：`VERSION`、`version.json`、`src/__init__.py`
- 开源许可证：`MIT`，见 `LICENSE`

## 目标

- 保留核心源码结构，方便阅读与二次开发
- 保留最小可演示能力，避免依赖主仓私有运行数据
- 不包含主开发仓中的数据库、缓存、日志、报告和历史运行产物

## 快速开始

```powershell
./一键安装依赖.bat
./运行演示.bat
```

## 当前版本定位

- 本目录是独立导出的演示副本
- 不会回写或修改 `D:\Agent`
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

如果你需要跑测试或参与开发，再额外安装：

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

- 主仓数据库
- 原始抓取结果
- 运行日志
- 缓存
- 报告产物
- 历史状态快照

这意味着：

- 它可以完整运行“演示版核心能力”
- 但不会复现主开发仓的长期记忆和生产态历史上下文

## 第三方依赖说明

依赖已拆分为三层：

- `requirements-demo.txt`：最小演示运行依赖
- `requirements-optional.txt`：浏览器、抓取、向量等可选增强依赖
- `requirements-dev.txt`：测试与开发依赖

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

## 注意

- 根目录文件名按要求提供了 `READM.MD`
- 为了 GitHub 展示兼容性，建议后续同步保留 `README.md`
- 第三方依赖与开源前审核结果见 `THIRD_PARTY_NOTICES.md`
- 本次导出与审核收口见 `更新日志.MD`
- 许可证选择建议见 `开源许可证建议.MD`

## GitHub 协作文件

- 贡献指南：`CONTRIBUTING.md`
- 安全策略：`SECURITY.md`
- Issue 模板：`.github/ISSUE_TEMPLATE/`
- Pull Request 模板：`.github/PULL_REQUEST_TEMPLATE.md`

## GitHub 首发元数据

- 仓库元数据建议：`GITHUB_REPOSITORY_METADATA.MD`
- 本次发布说明：`RELEASE_NOTES_v1.0.03.MD`
