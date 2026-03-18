# 贡献指南

感谢你关注 `ABUMeta`。

## 开始之前

- 先阅读 `README.md`
- 先阅读 `THIRD_PARTY_NOTICES.md`
- 先确认你的修改不会把主开发仓的私有运行资产重新带回开源版

## 本地开发建议流程

1. 运行 `一键安装依赖.bat`
2. 如需测试与开发工具，再执行：

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

3. 运行 smoke 测试：

```powershell
.\.venv\Scripts\python -m pytest tests\test_smoke_demo.py
```

## 提交内容要求

- 保持改动聚焦，不做无关重构
- 不要提交 `.venv`、数据库、缓存、日志、报告和抓取结果
- 不要提交真实 API Key、密码、Cookie 或私有配置
- 如果新增第三方依赖，必须同时更新：
  - `requirements-*.txt`
  - `THIRD_PARTY_NOTICES.md`

## 文档同步要求

如果你的改动会影响使用方式，请同步更新：

- `README.md`
- `READM.MD`
- 必要时更新 `更新日志.MD`

## Issue 与 Pull Request

- 提问题前先检查是否已有同类 Issue
- 提交 PR 时请说明：
  - 改了什么
  - 为什么改
  - 如何验证
  - 是否引入新依赖
