# Security Policy / 安全策略

This document describes how to report security issues for the public ABUMeta repository and what kinds of content must never be exposed publicly.

本文档说明 ABUMeta 公开仓库的安全问题报告方式，以及哪些内容绝不能公开暴露。

## Supported Scope / 支持范围

- `EN` Security maintenance currently focuses on the public `main` branch of the open-source demo repository.
- `中文` 当前安全维护重点是开源演示版仓库的 `main` 分支。
- `EN` The public repository is intentionally narrower than any private development environment.
- `中文` 公开仓库的能力边界有意小于任何私有开发环境。

## Do Not Publicly Commit / 不要公开提交的内容

- API keys / API Key
- passwords / 账号密码
- cookies or sessions / Cookie 或 Session
- local databases / 本地数据库
- external crawl results / 外部站点抓取结果
- logs, reports, and journals / 日志、报告与账本
- `data/action_journal.jsonl`
- `data/reports/*.md`
- `data/reports/*.json`
- `data/reports/*.jsonl`
- `data/logs/*.log`
- local environment files such as `.env` / 本地环境文件，如 `.env`

## What Counts as a Security Issue / 哪些问题算安全问题

Examples include / 常见安全问题包括：

- credential exposure / 密钥或凭据泄露
- unsafe logging of secrets or user data / 日志中泄露敏感信息或用户数据
- unintended publication of private runtime assets / 私有运行资产被误公开
- sandbox escape or unsafe Docker behavior / 沙盒逃逸或不安全的 Docker 行为
- insecure handling of external API configuration / 外部 API 配置处理不安全
- security regressions caused by dependency upgrades / 依赖升级带来的安全回归

## Reporting Process / 报告方式

- `EN` Do not publish exploitable details in a public issue.
- `中文` 不要在公开 Issue 中披露可利用细节。
- `EN` Use GitHub Security Advisories or the repository security policy contact flow when possible.
- `中文` 优先使用 GitHub Security Advisories 或仓库安全策略页面提供的方式私下报告。

When reporting, please include / 报告时建议包含：

- issue summary / 问题概述
- affected files or components / 受影响文件或模块
- reproduction steps / 复现步骤
- impact assessment / 风险影响判断
- suggested mitigation if known / 如已知，可提供修复建议

## Known High-Risk Areas / 当前高风险区域

- external crawling capabilities / 外部抓取能力
- external LLM or API integration / 外部 LLM 与 API 接入
- local file output, reports, and action journals / 本地文件输出、报告与行动账本
- optional browser automation / 可选浏览器自动化能力
- optional Docker-based sandbox execution / 可选 Docker 沙盒执行能力

## Security Handling Principles / 处理原则

- `EN` Fix secret exposure and accidental publication issues first.
- `中文` 优先修复密钥泄露和误公开问题。
- `EN` Minimize the chance of logs, caches, databases, and reports entering Git.
- `中文` 优先降低日志、缓存、数据库和报告进入 Git 的可能性。
- `EN` Recheck legal and security impact whenever third-party dependencies change.
- `中文` 每次第三方依赖发生变化，都应重新检查许可证与安全影响。

## Disclosure Expectations / 披露原则

- `EN` Please allow time for verification and remediation before public disclosure.
- `中文` 请在公开披露前预留修复与验证时间。
- `EN` Once an issue is fixed, a public summary without harmful exploit detail is welcome.
- `中文` 问题修复后，可以公开发布不包含可利用细节的摘要说明。
