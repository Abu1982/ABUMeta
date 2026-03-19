# Public Launch Announcements

This document provides ready-to-publish launch copy for ABUMeta across several public channels.

本文档提供 ABUMeta 在不同公开渠道可直接使用的首发文案。

## 1. GitHub Release Long Copy / GitHub Release 长文案

### Title

`v1.0.04 - ABUMeta Open-Source Demo First Public Release`

### Body

```md
## What this release is

ABUMeta is a public demo edition of a human-like autonomous agent architecture. This release keeps the core memory, psyche, decision, execution, and governance skeleton while removing private runtime assets and historical state.

## Highlights

- ships a runnable open-source demo with core agent architecture preserved
- keeps the repository clean of private databases, logs, caches, reports, and historical runtime artifacts
- splits dependencies into demo, optional, and dev layers for safer public distribution
- adds one-click setup and demo scripts for both Windows and Linux
- adds GitHub Actions smoke validation on both `windows-latest` and `ubuntu-latest`

## Included in this public release

- core source code under `src/`
- required public config under `config/`
- minimal smoke tests under `tests/`
- public docs under `docs/`
- empty runtime directories and schema placeholders under `data/`

## Not included

- private databases
- cached crawl results
- runtime logs and reports
- local action journals
- model caches and vector store data
- private API credentials or local environment files

## Quick start

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

## Validation

- local smoke test passes: `tests/test_smoke_demo.py`
- local demo script passes: `scripts/demo_showcase.py`
- GitHub Actions workflow `Cross Platform Smoke` passes on Windows and Linux

## Notes

- optional browser, vector, model, and Docker features remain outside the minimal demo path
- if you use external APIs, copy `.env.example` to your local environment file and fill in your own credentials
- see `THIRD_PARTY_NOTICES.md` for dependency licenses and commercial-use boundaries
```

## 2. X / Twitter Short Post / X 短帖

### English

```text
ABUMeta is now public.

A bilingual open-source demo of a human-like autonomous agent architecture with memory, psyche, decision, execution, and governance modules.

Windows + Linux bootstrap scripts included.
Cross-platform smoke CI included.

Repo: https://github.com/Abu1982/ABUMeta
Release: https://github.com/Abu1982/ABUMeta/releases/tag/v1.0.04
```

### Chinese

```text
ABUMeta 现已开源。

这是一个双语自治 Agent 演示版仓库，保留了记忆、心理、决策、执行与治理骨架，适合研究、阅读与二次开发。

已附带 Windows / Linux 一键安装脚本，且通过跨平台 smoke CI 校验。

仓库： https://github.com/Abu1982/ABUMeta
Release： https://github.com/Abu1982/ABUMeta/releases/tag/v1.0.04
```

## 3. Zhihu Launch Draft / 知乎首发稿

### Suggested Title / 标题建议

`我把一个双语自治 Agent 演示版开源了：ABUMeta`

### Suggested Body / 正文建议

```md
最近把一个长期打磨的自治 Agent 架构副本整理成了可以公开展示的开源演示版，名字叫 **ABUMeta**。

它不是把私有生产环境直接扔到 GitHub 上，而是做了比较明确的公开收口：

- 保留记忆、心理、决策、执行、治理这些核心骨架
- 去掉数据库、日志、缓存、报告、历史运行资产
- 拆分运行依赖、可选增强依赖、开发依赖
- 补了第三方依赖说明、商用边界说明和安全策略
- 增加了 Windows / Linux 一键安装脚本和 GitHub Actions 跨平台 smoke 校验

这个仓库更适合以下几类人：

1. 想研究自治 Agent 工程化骨架的人
2. 想看“记忆 + 心理 + 决策 + 执行”如何组织到一个系统里的人
3. 想在一个相对干净的公开副本上继续二次开发的人

仓库地址：

`https://github.com/Abu1982/ABUMeta`

首个公开 Release：

`https://github.com/Abu1982/ABUMeta/releases/tag/v1.0.04`

如果你对自治体架构、长期运行 Agent、认知模块拆分、公开版工程收口这些话题感兴趣，欢迎交流。
```

## 4. WeChat / Blog Long Intro / 公众号或博客长文引言

```text
ABUMeta 正式公开了。

这是一个双语开源演示版自治 Agent 架构仓库，保留了记忆、心理、决策、执行与治理等核心骨架，同时把私有数据库、日志、缓存、报告和历史运行资产从公开版中剥离出来。

这次公开的重点，不只是“把代码放上 GitHub”，而是尽量把公开边界、依赖边界、商用边界和协作边界都整理清楚：

一方面，仓库本身保持最小可运行，支持 Windows 与 Linux 两套一键安装与演示脚本；
另一方面，又尽量避免把日志、抓取结果、数据库、模型缓存或私有配置混入公开版。

如果你关注自治 Agent、长期运行系统、认知架构、规则化治理、或者开源工程收口，这个仓库应该会有一定参考价值。

仓库地址：
https://github.com/Abu1982/ABUMeta
```

## 5. Tech Group Short Copy / 技术群短介绍

### 100-character style / 极短版

```text
ABUMeta 已开源：一个双语自治 Agent 演示版仓库，保留记忆/心理/决策/执行/治理骨架，支持 Windows/Linux 一键运行。https://github.com/Abu1982/ABUMeta
```

### 200-character style / 稍长版

```text
ABUMeta 刚刚公开发布到 GitHub。这是一个双语自治 Agent 演示版项目，保留了记忆、心理、决策、执行、治理骨架，同时去掉了数据库、日志、缓存和历史运行资产。仓库已补齐双语 README、安装脚本、第三方说明和跨平台 smoke CI，适合研究和二次开发。https://github.com/Abu1982/ABUMeta
```

## 6. Pinned Repo One-Liner / 主页置顶仓一句话

### English

`A bilingual autonomous-agent demo with memory, psyche, decision, execution, and governance modules.`

### Chinese

`ABUMeta 是一个双语开源演示版自治 Agent 架构，适合研究、阅读和二次开发。`

## 7. Usage Notes / 使用建议

- Use the GitHub Release version for formal release notes or changelog-style announcements.
- 用 GitHub Release 长文案做正式发布说明。
- Use the X version for short social announcements.
- 用 X 短帖版做社交平台快速扩散。
- Use the Zhihu or WeChat versions when you want to explain the open-source boundaries and engineering positioning in more detail.
- 知乎或公众号版本更适合解释“为什么开源”“边界如何收口”“适合谁使用”。
