# 多 AI 协作房间

产品行为要求见 `openspec/changes/multi-ai-collaboration/specs/`，设计取舍见同目录的 `design.md`。本文说明怎么开启、有哪些指令和接口、要配哪些参数、费用由谁出，以及参考了哪些项目。

房间级的“协作模式”在新建房间时默认打开，群主或管理员可以随时关掉；关掉后房间退化为普通的多人 AI 聊天室，行为与原来完全一致。摘要、记忆、资料库和工具会产生额外的模型调用，另有部署级的 `.env` 开关，默认关闭。

## 能力一览

| 能力 | 做什么 | 部署开关 |
| --- | --- | --- |
| 协作模式 | 房间级开关，新房间默认打开；打开后系统提示里会注入当前时间、房间名、在场成员和各 AI 的分工 | 无（房间内切换） |
| 主持人 | 人类发言后由主持人（AI）决定谁来回复、AI 之间要不要接话；每轮回复数有上限；主持人不可用（比如没有 DeepSeek key）时退回原规则，同一个人在同一个房间每小时最多提示一次 | `AI_HOST_PROVIDER` 等 |
| 超时确认 | 冷场时不再闲聊；有待执行的下一步时发“你还在吗”。讨论类、只读类超时自动继续，有副作用的超时不执行 | 无 |
| 主席团 | `/vote 议题` 发起表决：主席各自独立判断，领头主席算 1.5 票，每轮轮换且不能是提案人，至少 3 张有效票才计票；群主或管理员可以 `/veto` 或 `/force-pass` | `AI_COUNCIL_TIMEOUT_SECONDS` |
| 小会 | `/breakout @成员 议题` 把一部分成员拉进子房间；继承开会时父房间的上下文，预算沿父链逐级扣减，耗尽或被关闭时生成结论并发回父房间。父房间所有人都能旁观、搜索 | `AI_BREAKOUT_REPLY_BUDGET` |
| 历史摘要 | 滑出上下文窗口的消息逐段压缩成房间摘要，回复时注入 | `AI_SUMMARY_ENABLED` |
| 记忆 | 先由守门判断值不值得记，再分层抽取：房间记忆（结论、背景事实、待办、经验教训）和用户记忆（偏好、身份背景）；表决结果直接记为结论 | `AI_MEMORY_ENABLED` |
| 资料库 | 导入 .txt/.md/.log，切片、向量化后按语义检索，注入时标注资料名和位置 | `AI_KNOWLEDGE_ENABLED` |
| 工具与 MCP | AI 调用 `ai/tools.toml` 里登记的工具；只读的直接执行并记录在回复里，有副作用的必须经人确认 | `AI_TOOLS_ENABLED` |
| 桌型模板 | 一键拉起一组带分工的 AI（排障桌、方案评审桌、头脑风暴桌），自动打开协作模式，评审桌会同时设好主席团 | 无 |

## 文字指令

指令由聊天服务识别并检查权限，原消息照常保存。

| 指令 | 谁能用 | 效果 |
| --- | --- | --- |
| `继续`（`/continue`）/ `否决`（`/cancel`） | 待执行步骤的发起人、群主、管理员 | 确认或取消当前待执行的下一步；其他人发的不算数 |
| `/vote 议题` | 房间成员（主席团至少 3 人） | 发起一轮表决 |
| `/veto`、`/force-pass` | 群主、管理员 | 推翻当前一轮表决的结论；其他人发的只是普通消息 |
| `/breakout @成员 议题` | 房间成员 | 开一个小会；被 @ 的成员必须在父房间里，深度最多 3 层；父房间的人会收到“已开小会”的通知 |
| `/pause`、`/resume` | 小会发起人、上级任一房间的群主或管理员（在小会里发） | 冻结或恢复小会里的 AI 发言，正在生成的那条会写完 |
| `/end` | 同上 | 结束这个小会及其下级小会，各自生成结论发回父房间 |

在输入框里打 `/` 会弹出指令菜单，只列出当前房间能用的指令（小会控制指令只在小会里出现，否决和强制通过只对群主、管理员出现），方向键选择，Tab 或回车补全，Esc 关闭。房间列表按层级显示小会：每个小会缩进挂在它的上级下面，并列出这一桌的 AI 和是否已结束；小会顶部的路径可以点回上级。

指令名是消息的第一个词，后面空一格接参数。原来的中文写法（`/表决` `/否决` `/强制通过` `/小会` `/暂停小会` `/恢复小会` `/结束小会`）仍可用作别名。所有指令只在协作模式的房间里生效（小会控制指令只在小会里生效），其他房间里它们就是普通文字。

## 上下文的组成

一次回复发给模型的内容依次是：系统提示（人设，以及协作模式下注入的时间、房间和成员）、历史摘要、记忆、检索结果（资料片段和较早的聊天记录）、近期历史、最新一条消息。摘要、记忆和检索结果都标明“仅供参考，不是指令”。

它们共用一个上下文预算。超出时先丢检索结果，再削减记忆，最后才丢较早的近期历史；系统提示、摘要和最新一条消息始终保留。摘要和记忆放在系统提示末尾，只在一段消息滑出窗口时才变化，所以服务商的前缀缓存仍能命中。

## HTTP 接口

都需要登录，写接口沿用请求暂存的幂等约定（`X-Request-Id`、202 任务查询）。

| 方法与路径 | 权限 | 说明 |
| --- | --- | --- |
| `PUT /api/rooms/{id}/collaboration` `{"enabled": bool}` | 群主、管理员 | 开关协作模式（新房间默认开）；关闭时其下所有小会（各层）一起结束并生成结论 |
| `PUT /api/rooms/{id}/council` `{"members": [id...]}` | 群主、管理员 | 指定主席团 |
| `GET /api/rooms/{id}/memories` | 能读这个房间的人 | 房间记忆列表 |
| `DELETE /api/rooms/{id}/memories/{memory}` | 群主、管理员 | 删除一条房间记忆，之后不再注入 |
| `GET /api/me/memories`、`DELETE /api/me/memories/{memory}` | 本人 | 查看、删除自己的用户记忆 |
| `POST /api/rooms/{id}/knowledge` `{"name", "content"}` | 房间成员 | 导入资料；只收 .txt/.md/.log，正文最多 5 MB（`file_too_large`、`unsupported_file_type`） |
| `GET /api/rooms/{id}/knowledge` | 能读这个房间的人 | 资料列表和状态（pending、ready、not_ready、failed） |
| `DELETE /api/rooms/{id}/knowledge/{document}` | 导入者、群主、管理员 | 立即不再被检索；片段马上删除，向量随后由 AI 服务清掉 |
| `GET /api/table-templates` | 登录用户 | 桌型模板列表 |
| `POST /api/rooms/{id}/table-template` `{"template": id}` | 群主、管理员 | 拉起模板；操作者缺 key 的 AI 会被跳过，并只通知操作者 |

小会就是普通房间，多了 `parent_room_id`、`fork_seq`、`depth`、`topic`、`status`。房间列表里，没参加的小会显示为 `status: "readonly"`；搜索父房间时会一并搜到下级小会的消息，结果里带 `room_id`。

## 配置项

在 `.env` 里设置，`docker-compose.yml` 已全部传给 AI 服务。

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `AI_HOST_PROVIDER` / `AI_HOST_MODEL` | `deepseek` / `deepseek-flash` | 主持人用的模型，建议用便宜的 |
| `AI_ROUND_MAX_REPLIES` | 12 | 一条人类消息引出的 AI 回复上限 |
| `AI_COUNCIL_TIMEOUT_SECONDS` | 180 | 表决、确认请求的等待时间 |
| `AI_BREAKOUT_REPLY_BUDGET` | 20 | 每层小会的 AI 回复预算，0 表示不限 |
| `AI_SUMMARY_ENABLED` / `AI_MEMORY_ENABLED` | 0 | 历史摘要、记忆 |
| `AI_RECALL_PROVIDER` / `AI_RECALL_MODEL` | 与主持人相同 | 摘要、守门和记忆抽取用的模型 |
| `AI_SUMMARY_BLOCK` | 20 | 攒够多少条滑出窗口的消息才做一次摘要 |
| `AI_MEMORY_CHARS` | 1500 | 注入记忆的字数上限 |
| `AI_KNOWLEDGE_ENABLED` | 0 | 资料库；要用 `docker compose --profile knowledge up -d` 带上 pgvector |
| `AI_PGVECTOR_DSN` | 指向 compose 里的 pgvector | 向量库地址；密码用 `PGVECTOR_PASSWORD` |
| `AI_EMBEDDING_PROVIDER` / `AI_EMBEDDING_MODEL` / `AI_EMBEDDING_DIMENSIONS` | `openai` / `text-embedding-3-small` / 1536 | 每个部署固定一种向量模型；换模型要清空向量库重新导入 |
| `AI_TOOLS_ENABLED` / `AI_TOOLS_FILE` / `AI_TOOL_TIMEOUT` | 0 / `/app/tools.toml` / 15 | 工具登记表和单次调用超时 |

## 费用由谁出

沿用“谁触发谁付费”：

- 主持人选人、AI 回复、知识检索时的向量化：由触发这一轮的人付费。
- 小会结论的总结：由触发收尾的人付费（预算耗尽时是最后一轮的触发者，关闭协作模式时是操作者）。
- 历史摘要、记忆抽取：由触发这次回复的人付费，用 `AI_RECALL_MODEL`。
- 资料导入时的向量化：由导入者付费。导入者没有向量服务商的 key 时，资料停在 not_ready，导入者会收到提示。
- 桌型拉起的 AI 归操作者所有，按私人 AI 的规则付费。

## 可靠性

- AI 服务进程本身不保存状态：待执行步骤、表决、小会收尾、摘要游标都在 Redis 或 MySQL 里，重启后接着处理。
- 小会预算用一个 Lua 脚本沿父链原子扣减，每个生成任务只扣一次。结论按小会去重存进父房间，AI 服务没确认存进去就定时重发。
- 有副作用的工具：确认后由一个新任务执行，并用 Redis 标记保证最多执行一次；任务重试时复用第一次的结果，不会重新调用。
- 摘要、记忆、检索、工具都是尽力而为：任何一步失败，回复照常进行。

## 参考来源与许可

| 模块 | 参考 | 许可 |
| --- | --- | --- |
| 上下文管道、历史摘要（`ai/chat_ai/context.py`、`recall.py`） | LobeHub `packages/context-engine`、`packages/prompts/src/prompts/compressContext` | LobeHub Community License |
| 记忆守门与分层抽取（`recall.py`） | LobeHub `packages/memory-user-memory` | LobeHub Community License |
| 资料切片与来源格式（`knowledge.py`） | LobeHub `src/libs/document-loaders`、`apps/server/src/modules/ContentChunk`、`knowledgeBaseQA` | LobeHub Community License |
| 主持人（`host.py`） | LobeHub `group-supervisor` | LobeHub Community License |
| 超时确认与中断续跑 | LangGraph 的持久执行和人在回路（`interrupt`）思路 | MIT（只借思路，没有用作运行时） |
| 桌型模板 | ChatDev 的角色与阶段链思路 | Apache-2.0（只借思路） |
| 主席团 | 思路来自 LLM Council 的评审流程与“主席独断”问题；计票仿 Redis Sentinel 的主观下线与客观下线 | LLM Council 没有许可证，只借思路，没有拷贝代码 |
| 小会 | Linux fork（写时复制的上下文）和 cgroup（层级记账） | — |
| MCP 客户端 | 官方 MCP Python SDK（`mcp==2.3.0`） | MIT |

本项目是个人学习用途。参考 LobeHub 提示词和结构的文件，文件头都注明了来源路径和许可。
