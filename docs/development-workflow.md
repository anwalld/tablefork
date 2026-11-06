# 分支与CI流程

从更新后的main建立一个功能分支（Codex用codex/前缀，Claude Code用claude/前缀），使用对应独立工作区。每批工作集中在自己的分支内完成实现、OpenSpec验证、测试和文档整理。

创建或更新同一个PR，在功能分支的最终提交上检查CI。当前工作流由pull_request触发分支CI，push main触发合并后CI，workflow_dispatch提供手动入口。

用户确认该批可合并且PR最新提交CI通过后，一次合并main，再检查main合并提交的CI。推送前集中整理文档，减少中途提交触发的重复构建。

合并main需要用户对当批的明确授权，前一批的授权不延续到后续批次。

交付后核对分支和工作区。清理已合并且干净的旧分支及已完成临时工作区，记录原提交。保留仍在使用、有未提交改动或尚未合并的分支，指定其用途。

完整测试运行时维持单一隔离Compose项目。临时夹具使用docker compose run --no-deps，避免按其他环境重建依赖并打断回归。

提交时在消息末尾为实际参与的 AI 添加共同作者署名，保留开发者本人的作者身份。Codex 与 Claude Code 共同参与时使用：

```text
Co-authored-by: Codex <codex@openai.com>
Co-authored-by: Claude Code <noreply@anthropic.com>
```

合并提交也保留对应署名，使 GitHub 能在提交详情中关联共同作者；README 的 Contributors 作为项目协作说明。

每次与用户确定设计后，在回复中给出“设计更改对照表”（至少含“原来”“现在”，必要时加“用途/影响”），并把同一张表写入对应change的design.md或阶段报告，区分已设计、已实现和已验证。
