# 鸣谢

Tablefork 站在很多开源项目的肩膀上。本文列出参考过的项目：借鉴了什么、在什么方面做了改进、各自用什么许可。本项目为个人学习用途。Tablefork 本身以 [MIT](LICENSE) 许可发布；参考了 LobeHub 的文件同时遵守 LobeHub Community License。

- [参考的多 AI 项目](#参考的多-ai-项目)
- [逐项对比](#逐项对比)
- [参考的设计思路](#参考的设计思路)
- [开源组件](#开源组件)
- [贡献者](#贡献者)

## 参考的多 AI 项目

| 项目 | 许可 | 借鉴 | Tablefork 的改进 |
|---|---|---|---|
| [LobeHub](https://github.com/lobehub/lobehub) | LobeHub Community License | 上下文引擎（处理器 + 注入器）、历史压缩提示词、记忆守门与分层抽取、资料切片参数和来源格式、群聊主持人（AI）提示词 | 把这些算法放到有序推送、断线补齐、确认后才算完成的底座上，避开社区反映的“群聊消息存了不显示、广播卡死”；个人记忆只在本人所在的房间使用；摘要、记忆、检索与历史共用一个预算 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | MIT | 持久执行（检查点、中断后续跑）和人在回路（`interrupt` 审批）的思路 | LangGraph 是编排库，提供图执行和状态检查点，多人房间、消息排序、推送、账号权限要自己搭；Tablefork 是成品系统，持久状态直接绑在房间的有序消息日志上（MySQL `seq` 是唯一事实来源）。确认按风险分级，只有发起人或群主、管理员等人类能批准，有副作用的动作超时绝不执行 |
| [LLM Council](https://github.com/karpathy/llm-council) | 无许可证（只借思路，未拷贝代码） | 多个模型独立作答、主席汇总的评审流程 | 由 AI 组成的主席团加权表决，代替单个主席（AI）独断：领头主席 1.5 票、每轮轮换、不能是提案人，至少 3 张有效票；保留少数派意见，人类可以否决或强制通过；投票解析失败算无效票，不会卡住流程；从单人问答变成多人房间 |
| [ChatDev](https://github.com/OpenBMB/ChatDev) | Apache-2.0（只借思路） | 角色分工、按阶段推进 | 桌型模板一键拉起一组分工 AI；任务有租约，中断后自动恢复，避开社区反映的“卡死、不能续跑”；人类随时可以插话 |
| [OpenAgents](https://github.com/openagents-org/openagents) | Apache-2.0（只借思路） | 人类和 agent 混编、关键动作审批 | 不用给每个 agent 单独装运行时，一条 `docker compose up` 起全套，没有 key 也能用假模型试；审批改为按风险分级的超时确认 |

参考了 LobeHub 提示词或结构的文件，文件头都注明了来源路径和许可，分别是 `ai/chat_ai/recall.py`、`knowledge.py` 和 `host.py`。

## 逐项对比

这几个项目和 Tablefork 不在同一个层次：LangGraph 是给开发者用的编排库，LobeHub 是功能完整的成品应用，Tablefork 是一个学习性质的个人项目。下表只是说明各自擅长什么，不代表孰优孰劣。

✅ 有　🟡 部分或需自行搭建　❌ 没有

| | Tablefork | LobeHub | LangGraph | LLM Council | ChatDev | OpenAgents |
|---|---|---|---|---|---|---|
| 形态 | 个人项目 | 成品应用 | 编排库 | 演示应用 | 平台 | 平台 |
| 多个人类在同一房间实时交流 | ✅ | 🟡 | ❌ | ❌ | ❌ | ✅ |
| 主持人（AI）安排发言 | ✅ | ✅ | 🟡 supervisor 模式 | ❌ | 🟡 固定流程 | 🟡 |
| 由 AI 主席团投票，人类可否决 | ✅ | ❌ | ❌ | 🟡 单个主席 | ❌ | 🟡 审批 |
| 分组讨论，结论回传 | ✅ | ❌ | 🟡 子图 | ❌ | ❌ | ❌ |
| 中断后恢复 | 🟡 整条回复重新生成 | 🟡 | ✅ 从断点继续 | ❌ | ❌ | 🟡 |
| 人工审批 | 🟡 限工具和表决步骤 | 🟡 | ✅ 任意节点 interrupt | ❌ | ❌ | ✅ |
| 自定义工作流编排 | ❌ 只有固定的协作模式 | 🟡 | ✅ 任意图、分支、循环 | ❌ | ✅ | 🟡 |
| 检查点回放（time travel） | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 可观测与调试工具 | 🟡 结构化日志 | 🟡 | ✅ LangSmith | ❌ | 🟡 | 🟡 |
| 记忆 / 知识库 | 🟡 基础实现 | ✅ | ✅ | ❌ | ❌ | 🟡 |
| 模型与插件生态 | 🟡 4 家模型、登记的 MCP 工具 | ✅ 数十家模型、插件市场 | ✅ LangChain 集成 | 🟡 OpenRouter | ✅ | ✅ |
| 界面完成度 | 🟡 协作操作靠文字指令 | ✅ | ❌ 库，无界面 | ✅ | ✅ | ✅ |
| 社区规模与生产验证 | ❌ 个人项目 | ✅ | ✅ 已在企业中使用 | 🟡 | ✅ | 🟡 |
| 一条命令自托管 | ✅ | ✅ | 🟡 需自行搭建应用 | ✅ | ✅ | ❌ |

对比基于 2026-11 各项目的公开文档和 issue，有出入欢迎指正。

## 参考的设计思路

| 来源 | 用在哪里 |
|---|---|
| Redis Sentinel 的“主观下线 / 客观下线” | 主席团计票：每个主席给出主观判断，按加权票数判定客观通过 |
| Linux `fork`（写时复制） | 小会：开会时只记下分叉点，继承父房间到那一刻的上下文，不复制消息 |
| Linux cgroup（层级记账、冻结） | 小会预算：沿父链逐级扣减，下级的消耗计入上级；可以暂停 |

## 开源组件

| 组件 | 用途 | 许可 |
|---|---|---|
| [Drogon](https://github.com/drogonframework/drogon) 1.9.13 | C++ HTTP / WebSocket 框架（打了两个断线修复补丁，见 `docker/`） | MIT |
| [gRPC](https://github.com/grpc/grpc) / Protocol Buffers | C++ 与 AI 服务之间的双向流 | Apache-2.0 / BSD-3-Clause |
| [Vue](https://github.com/vuejs/core) 3 | 前端 | MIT |
| [marked](https://github.com/markedjs/marked)、[DOMPurify](https://github.com/cure53/DOMPurify) | 人设的 Markdown 渲染与过滤 | MIT；MPL-2.0 或 Apache-2.0 |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 接入 MCP 工具服务 | MIT |
| [pgvector](https://github.com/pgvector/pgvector) | 资料库向量检索 | PostgreSQL License |
| [Flyway](https://github.com/flyway/flyway) | 数据库迁移 | Apache-2.0 |
| MySQL、Redis、HAProxy、Nginx | 存储、缓存与故障切换、反向代理 | 各自许可 |
| [SecLists](https://github.com/danielmiessler/SecLists) | 常见密码列表（`data/common-passwords.txt`） | MIT |

## 贡献者

本项目由开发者 [anwalld](https://github.com/anwalld) 与以下 AI 编程工具协作完成代码实现、评审、测试和文档整理：

- **Codex**（OpenAI）
- **Claude Code**（Anthropic）
