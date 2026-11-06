# 基于 C++ 的分布式 AI 实时通信系统 · 文档索引

## 当前文档

| 要找什么 | 唯一入口 |
| --- | --- |
| 项目介绍、拓扑、启动、开发与测试 | [项目README](../README.md) |
| 最新提交、CI、部署、已做/未做、AI接手 | [当前交接](handoff.md) |
| 当前任务顺序、下一批已确认设计及已完成项状态 | [后续计划](roadmap.md) |
| 请求暂存协议、配置、故障语义与运维边界 | [请求暂存与恢复](request-buffering.md) |
| 未读、搜索、AI停止/暂停接口与结构化日志 | [聊天阅读、搜索与AI控制](chat-controls.md) |
| 私人AI角色、房间人设接口与设计对照 | [私人AI角色与房间人设](private-ai-personas.md) |
| 分支、PR、CI、合并与共同作者署名 | [分支与CI流程](development-workflow.md) |
| 当前产品行为要求 | [OpenSpec主规格](../openspec/specs/) |

当前状态只更新handoff；待办只更新roadmap；日常操作只更新README或对应专题。历史材料不再通过不断追加“最新进度”充当第二份当前交接。临时日志不进仓库，引用要说明提交、退出码、测试范围和本机材料限制。

## 历史与审计

- [PR #13原计划与历史讨论](follow-up-plan.md)：保留原文，当前任务以roadmap为准；PR #13已完成的功能不重复列为待办。

- [2026-11-03交接/验证快照汇编](history/2026-11-03-handoffs.md)：三份原外部报告合并，保留各自失败和未测事实。
- [2026-11-05探索历史](history/exploration-2026-11-05.md)：完整集中探索及旧副本的独有补记；包括已被撤回的候选方案和旧调度，只供追溯。
- [OpenSpec归档](../openspec/changes/archive/)：保留proposal、design、tasks、delta规格、验证和故障根因证据。核心归档不为精简文件数而删除；历史交接开头统一链接当前入口。

## 本次文档变换（2026-11-06）

| 原文档 | 处理与去向 |
| --- | --- |
| 工作区CppAiServer-进度.md + CppAiServer-部署验收.md | 合并到[当前交接](handoff.md)，main CI更新为实际成功，删除原两份散落文件 |
| 工作区2026-11-03项目交接 + HTTPS高可用封禁交接 + OpenSpec验证报告 | 合并到[历史汇编](history/2026-11-03-handoffs.md)，删除原三份散落文件 |
| docs/request-buffering-plan.md + harden-ha-verification内未跟踪follow-up-request-buffering.md | 合并到[探索历史](history/exploration-2026-11-05.md)；提取有效未完成项到[后续计划](roadmap.md)，删除旧副本 |
| 工作区五份散落文档入口 | 用CppAiServer-文档入口.md代替，链接本索引 |
| README.md | 加统一文档入口、共享桶拓扑与密钥说明；修正app服务名、CLion依赖和旧升级说明 |
| docs/request-buffering.md | 保留专题，更新已部署状态，链接真实部署记录和历史变更验证 |
| OpenSpec历史交接/验证/根因补充 | 保留证据，增加历史标识和当前入口，修正已迁移文档的引用 |

整理前Markdown备份在本机`/private/tmp/cppai-docs-before-cleanup-20261106`，仅供本次整理回退；不作为长期文档入口。源码、运行配置、数据卷和秘密没有改动，本次未运行业务回归或重新部署。
