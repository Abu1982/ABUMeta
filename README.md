# ABUMeta / 开源演示版

[![Cross Platform Smoke](https://github.com/Abu1982/ABUMeta/actions/workflows/cross-platform-smoke.yml/badge.svg)](https://github.com/Abu1982/ABUMeta/actions/workflows/cross-platform-smoke.yml)
[![Release](https://img.shields.io/github/v/release/Abu1982/ABUMeta)](https://github.com/Abu1982/ABUMeta/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

ABUMeta is a bilingual open-source demo of a human-like autonomous agent architecture with memory, psyche, decision, execution, and governance modules.

ABUMeta 是一个双语开源演示版项目，展示了一个具备记忆、心理、决策、执行与治理骨架的类人自治 Agent 架构。

## Overview / 项目简介

- `EN` ABUMeta keeps the core architecture of a long-running autonomous agent while removing private runtime assets, databases, caches, reports, and secrets.
- `中文` ABUMeta 保留了长期运行自治 Agent 的核心架构，同时移除了私有运行资产、数据库、缓存、报告和密钥。
- `EN` The public repository is designed for demonstration, research, reading, and secondary development rather than for reproducing a private production environment.
- `中文` 公开仓库的定位是演示、研究、阅读和二次开发，而不是复刻私有生产环境。

## Current Release / 当前版本

- `EN` Display version: `v1.0.04`
- `中文` 展示版本：`v1.0.04`
- `EN` Internal version: `1.0.4`
- `中文` 内部版本：`1.0.4`
- `EN` Version files: `VERSION`, `version.json`, `src/__init__.py`
- `中文` 版本文件：`VERSION`、`version.json`、`src/__init__.py`
- `EN` License: `MIT`, see `LICENSE`
- `中文` 开源许可证：`MIT`，见 `LICENSE`

## Highlights / 亮点

- `EN` Runnable public demo with Windows and Linux bootstrap scripts.
- `中文` 提供可运行的公开演示版，并附带 Windows 与 Linux 一键安装脚本。
- `EN` GitHub Actions smoke validation on `windows-latest` and `ubuntu-latest`.
- `中文` GitHub Actions 已在 `windows-latest` 和 `ubuntu-latest` 上执行 smoke 校验。
- `EN` Dependency split into demo, optional, and dev layers for safer public distribution.
- `中文` 依赖按 demo、optional、dev 三层拆分，更适合公开分发。
- `EN` Clear third-party notices and commercial-use boundaries.
- `中文` 已补齐第三方依赖说明与商用边界说明。

## Quick Start / 快速开始

### Windows

```powershell
./一键安装依赖.bat
./运行演示.bat
```

### Linux

```bash
bash install_demo_env.sh
bash run_demo.sh
```

## Environment Requirements / 环境前提

- `EN` Windows: install Python 3.11+ and make sure `python` or `py` is available in `PATH`.
- `中文` Windows：安装 Python 3.11+，并确保 `python` 或 `py` 已加入 `PATH`。
- `EN` Linux: install Python 3.11+, `python3-venv`, and `python3-pip`, and ensure `python3` is available.
- `中文` Linux：安装 Python 3.11+、`python3-venv`、`python3-pip`，并确保 `python3` 可用。
- `EN` Optional browser, vector, and Docker features should be installed only when needed.
- `中文` 浏览器、向量和 Docker 相关能力都属于按需安装的可选增强能力。

## Installation / 安装说明

### Base Demo Environment / 基础演示环境

- `EN` Windows entry points: `一键安装依赖.bat` or `install_demo_env.bat`
- `中文` Windows 入口：`一键安装依赖.bat` 或 `install_demo_env.bat`
- `EN` Linux entry point: `bash install_demo_env.sh`
- `中文` Linux 入口：`bash install_demo_env.sh`

These scripts will / 这些脚本会：

- create `.venv` / 创建 `.venv`
- upgrade `pip` / 升级 `pip`
- install `requirements-demo.txt` / 安装 `requirements-demo.txt`

### Optional Extras / 可选增强依赖

- `EN` Windows: `安装可选增强依赖.bat` or `install_optional_extras.bat`
- `中文` Windows：`安装可选增强依赖.bat` 或 `install_optional_extras.bat`
- `EN` Linux: `bash install_optional_extras.sh`
- `中文` Linux：`bash install_optional_extras.sh`

`requirements-optional.txt` includes `scrapling`, `playwright`, `chromadb`, `sentence-transformers`, `torch`, and `docker`.

`requirements-optional.txt` 包含 `scrapling`、`playwright`、`chromadb`、`sentence-transformers`、`torch`、`docker`。

For Debian/Ubuntu-like systems, if Playwright reports missing system packages, run:

对于 Debian / Ubuntu 一类系统，如果 Playwright 提示缺少系统依赖，可执行：

```bash
sudo .venv/bin/python -m playwright install --with-deps chromium
```

### Dev and Test Dependencies / 开发与测试依赖

- `EN` Windows: `install_dev_env.bat`
- `中文` Windows：`install_dev_env.bat`
- `EN` Linux: `bash install_dev_env.sh`
- `中文` Linux：`bash install_dev_env.sh`

## Run the Demo / 运行演示

- `EN` Windows: `运行演示.bat` or `run_demo.bat`
- `中文` Windows：`运行演示.bat` 或 `run_demo.bat`
- `EN` Linux: `bash run_demo.sh`
- `中文` Linux：`bash run_demo.sh`

The demo runs `scripts/demo_showcase.py` and showcases:

演示脚本会执行 `scripts/demo_showcase.py`，展示以下能力：

- treasury state / 金库状态
- psyche state / 心理状态
- decision engine output / 决策引擎输出
- page analysis flow / 页面分析流程

## Run Tests / 运行测试

### Windows

```powershell
.\.venv\Scripts\python -m pytest tests
```

### Linux

```bash
. .venv/bin/activate && python -m pytest tests
```

## Repository Layout / 仓库结构

- `src/` - core architecture and runtime modules / 核心架构与运行模块
- `config/` - public configuration and rule files / 公开配置与规则文件
- `scripts/` - showcase and helper scripts / 演示与辅助脚本
- `tests/` - minimal smoke tests / 最小 smoke 测试
- `docs/` - public documentation / 公开文档
- `data/` - empty runtime folders plus schema placeholders / 空运行目录与 schema 占位文件

## Dependency Layers / 依赖分层

- `requirements-demo.txt` - minimal runtime dependencies / 最小运行依赖
- `requirements-optional.txt` - browser, vector, and sandbox extras / 浏览器、向量与沙盒增强依赖
- `requirements-dev.txt` - testing and development tools / 测试与开发工具

## Open-Source Boundaries / 开源边界

This public repository does **not** include:

本公开仓库 **不包含**：

- historical databases / 历史数据库
- raw crawl results / 原始抓取结果
- runtime logs / 运行日志
- caches / 缓存
- report artifacts / 报告产物
- historical state snapshots / 历史状态快照
- private secrets or local environment files / 私有密钥与本地环境文件

## Third-Party and Commercial Use / 三方依赖与商用说明

- `EN` Review `THIRD_PARTY_NOTICES.md` for dependency licenses, optional component boundaries, and commercial-use notes.
- `中文` 依赖许可证、可选组件边界和商用说明请查看 `THIRD_PARTY_NOTICES.md`。
- `EN` If you use external APIs, copy `.env.example` into your local environment file and fill in your own credentials.
- `中文` 如果需要使用外部 API，请复制 `.env.example` 到本地环境文件，并填写你自己的密钥。
- `EN` Never commit real API keys, cookies, sessions, logs, reports, or crawl results.
- `中文` 不要提交真实 API Key、Cookie、Session、日志、报告或抓取结果。

## Public Review Files / 公开审核文件

- `THIRD_PARTY_NOTICES.md`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `更新日志.MD`

## Community / 社区协作

- Issue templates: `.github/ISSUE_TEMPLATE/`
- Pull request template: `.github/PULL_REQUEST_TEMPLATE.md`
- Security policy: `SECURITY.md`
- Contributing guide: `CONTRIBUTING.md`

## Notes / 备注

- `EN` Windows shortcut files stay local and are intentionally excluded from GitHub.
- `中文` Windows 快捷方式仅保留本地使用，故意不进入 GitHub。
- `EN` Linux one-click scripts are plain Bash scripts and do not require extra orchestration tools.
- `中文` Linux 一键脚本是纯 Bash 脚本，不依赖额外编排工具。
- `EN` Runtime-generated journals, reports, caches, and logs are ignored by Git and should remain local.
- `中文` 运行时生成的账本、报告、缓存与日志都被 Git 忽略，应始终保留在本地。
