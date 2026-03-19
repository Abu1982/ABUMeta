# GitHub Final Settings Checklist

This checklist is the final repository setup guide for ABUMeta after the public release has been prepared.

本文档是 ABUMeta 在完成公开发布准备后可直接执行的 GitHub 仓库最终设置清单。

## 1. Repository Basics / 基础仓库设置

### Repository Name / 仓库名称

- `ABUMeta`

### Repository URL / 仓库地址

- `https://github.com/Abu1982/ABUMeta`

### License / 许可证

- Current status: `MIT` detected by GitHub
- 当前状态：GitHub 已识别为 `MIT`

## 2. About Section / About 区域

### Recommended Description / 推荐简介

`Bilingual open-source demo of a human-like autonomous agent architecture with memory, psyche, decision, execution, and governance modules.`

### Recommended Topics / 推荐 Topics

- `agi`
- `ai-research`
- `autonomous-agent`
- `bilingual`
- `cognitive-architecture`
- `decision-engine`
- `llm-agent`
- `memory-system`
- `multi-agent`
- `python`
- `web-automation`
- `open-source-demo`

### Checklist / 检查项

- [x] Description updated
- [x] Topics updated
- [ ] Website field configured if you want an external landing page
- [ ] Social preview image uploaded manually in repository settings

## 3. Social Preview / 社交预览图

### Upload Asset / 上传素材

- PNG file: `assets/social_preview.png`
- SVG source: `assets/social_preview.svg`
- Generator script: `scripts/generate_social_preview.py`

### Upload Path / 上传位置

- GitHub repository page
- `Settings` -> `General` -> `Social preview`

### Recommended Check / 推荐复核

- [ ] Image uploads successfully
- [ ] Mobile preview remains readable
- [ ] `ABUMeta` title is visually dominant
- [ ] Chinese support line is crisp and not too small

## 4. Pinned Repository Copy / 置顶仓库文案

Use these when pinning the repo on your GitHub profile.

如果你要在 GitHub 个人主页置顶该仓库，可使用以下文案。

### Short

`A bilingual autonomous-agent demo with memory, psyche, decision, execution, and governance modules.`

### Medium

`ABUMeta is a bilingual open-source demo of a human-like autonomous agent architecture, built for research, reading, and secondary development.`

### Chinese

`ABUMeta 是一个双语开源演示版自治 Agent 架构，适合研究、阅读和二次开发。`

## 5. README and Release / README 与 Release

### Checklist / 检查项

- [x] Bilingual public README is live
- [x] Release `v1.0.04` is published
- [x] `THIRD_PARTY_NOTICES.md` is included
- [x] `LICENSE` is visible and correctly detected
- [ ] Optional: pin the release in project communications or profile links

## 6. Discussions / Discussions 设置

### Recommendation / 建议

- `EN` Enable Discussions if you want architecture questions, research discussion, and community brainstorming to stay out of the issue tracker.
- `中文` 如果你希望架构讨论、研究交流和社区 brainstorm 不占用 Issue 流程，建议开启 Discussions。

### Suggested Categories / 建议分类

- `Announcements`
- `Ideas`
- `Architecture`
- `Q&A`
- `Show and tell`

### Checklist / 检查项

- [x] Discussions enabled
- [ ] Categories configured
- [ ] One welcome post published if community interaction is expected

## 7. Security Settings / 安全设置

### Recommended Actions / 建议动作

- [x] `SECURITY.md` exists
- [x] Issue template contact link points to security policy
- [ ] Enable Dependabot alerts
- [x] Enable Dependabot security updates
- [ ] Enable secret scanning if available
- [ ] Enable push protection for secrets if available
- [ ] Review GitHub Security tab after first public exposure

### Repository Hygiene / 仓库卫生

- [x] `.env.example` is public
- [x] `.env` is ignored
- [x] runtime reports and logs are ignored
- [x] `data/action_journal.jsonl` is ignored

## 8. Actions and CI / Actions 与持续集成

### Current State / 当前状态

- Workflow: `Cross Platform Smoke`
- Platforms: `windows-latest`, `ubuntu-latest`
- Current status: passing

### Checklist / 检查项

- [x] Workflow passes on Windows
- [x] Workflow passes on Linux
- [x] README badge is present
- [ ] Optional: add status badge to profile or release notes

## 9. Suggested Launch Pass / 发布前最后复核

- [ ] Open repository home page in logged-out mode
- [ ] Confirm README renders correctly in desktop and mobile layouts
- [x] Confirm Release page is visible and readable
- [x] Confirm Actions page shows latest green workflow run
- [x] Confirm no runtime data appears in the file tree
- [ ] Confirm Social Preview has been uploaded manually

## 10. Manual Items That Still Require GitHub UI / 仍需在 GitHub 页面手工完成的项目

- Upload the Social Preview image
- Decide whether Discussions should be enabled
- Enable advanced Security features if your account plan allows them
- Optionally pin the repository on your profile
