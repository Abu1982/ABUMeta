# Contributing / 贡献指南

Thank you for your interest in `ABUMeta`.

感谢你关注 `ABUMeta`。

## Project Positioning / 项目定位

- `EN` ABUMeta is a public demo repository, not a full private production snapshot.
- `中文` ABUMeta 是一个公开演示仓库，不是私有生产环境的完整快照。
- `EN` Contributions should preserve the public-demo positioning and should not reintroduce private runtime assets.
- `中文` 所有贡献都应保持“公开演示版”定位，不应重新带回私有运行资产。

## Before You Start / 开始之前

- `EN` Read `README.md` for install and run instructions.
- `中文` 先阅读 `README.md`，了解安装与运行方式。
- `EN` Read `THIRD_PARTY_NOTICES.md` before adding, removing, or changing dependencies.
- `中文` 若涉及依赖增删改，请先阅读 `THIRD_PARTY_NOTICES.md`。
- `EN` Make sure your change does not add databases, logs, reports, caches, or secrets to the public repo.
- `中文` 请先确认你的改动不会把数据库、日志、报告、缓存或密钥重新带回公开仓库。

## Local Setup / 本地开发环境

### Base Environment / 基础环境

```text
Windows: 一键安装依赖.bat 或 install_demo_env.bat
Linux:   bash install_demo_env.sh
```

### Dev and Test Dependencies / 开发与测试依赖

```text
Windows: install_dev_env.bat
Linux:   bash install_dev_env.sh
```

### Run the Demo / 运行演示

```text
Windows: 运行演示.bat 或 run_demo.bat
Linux:   bash run_demo.sh
```

### Run Smoke Tests / 运行 Smoke 测试

```powershell
.\.venv\Scripts\python -m pytest tests\test_smoke_demo.py
```

```bash
. .venv/bin/activate && python -m pytest tests/test_smoke_demo.py
```

## Contribution Scope / 贡献范围

Good contribution areas / 适合贡献的方向：

- documentation clarity / 文档清晰度
- demo stability / 演示链路稳定性
- test coverage for public flows / 公开流程测试覆盖
- dependency hygiene / 依赖治理
- install and CI improvements / 安装与 CI 改进
- internationalization and bilingual presentation / 双语与国际化呈现

Avoid in this public repo / 本公开仓库不建议直接提交：

- private production logic coupled to unreleased assets / 依赖私有资产的生产逻辑
- raw crawl results or contact data / 原始抓取结果或联系人数据
- logs, reports, journals, and caches / 日志、报告、账本与缓存
- real credentials, cookies, tokens, or local configs / 真实密钥、Cookie、Token 与本地配置

## Pull Request Expectations / PR 要求

Please keep pull requests focused and reviewable.

请尽量保持 PR 聚焦、易审阅。

- `EN` Avoid unrelated refactors in the same PR.
- `中文` 不要在同一个 PR 里混入无关重构。
- `EN` Explain what changed, why it changed, and how you validated it.
- `中文` 说明改了什么、为什么改、如何验证。
- `EN` If public-facing text changes, add or update English-facing wording as needed.
- `中文` 如果改动影响公开文案，请同步补齐英文表达。

## Dependency Changes / 依赖变更要求

If you add, remove, or upgrade dependencies, update all related files:

如果你新增、移除或升级依赖，请同步更新相关文件：

- `requirements-*.txt`
- `THIRD_PARTY_NOTICES.md`
- `README.md` if install steps change / 如果安装步骤变化，也要同步更新 `README.md`

## Documentation Sync / 文档同步

Update documentation when behavior, install steps, or public boundaries change.

如果行为、安装步骤或公开边界发生变化，请同步更新文档。

Common files to review / 常见需要同步检查的文件：

- `README.md`
- `SECURITY.md`
- `THIRD_PARTY_NOTICES.md`
- `更新日志.MD`

## Security and Privacy / 安全与隐私

- `EN` Never commit `.env`, private environment files, tokens, cookies, or passwords.
- `中文` 不要提交 `.env`、私有环境文件、Token、Cookie 或密码。
- `EN` Never commit runtime-generated files such as `data/action_journal.jsonl`, logs, or reports.
- `中文` 不要提交运行时生成文件，如 `data/action_journal.jsonl`、日志或报告。
- `EN` If you discover a security issue, follow `SECURITY.md` instead of filing a public exploit report.
- `中文` 如果发现安全问题，请按 `SECURITY.md` 处理，不要直接公开披露可利用细节。

## Communication / 沟通建议

- `EN` Check existing issues before opening a new one.
- `中文` 提新 Issue 前先检查是否已有相似问题。
- `EN` Use clear, reproducible examples whenever possible.
- `中文` 尽量提供清晰、可复现的示例。
- `EN` Bilingual issue and PR text is welcome, but concise English context helps international contributors review faster.
- `中文` 欢迎双语提交，但如果能附上简洁英文背景，会更利于国际协作者理解与审阅。
