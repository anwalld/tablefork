# Design

## Context

动机见 proposal.md。与设计相关的现状：

- C++ 服务负责账号、房间、消息和推送，`seq` 只在 C++ 分配；AI 的最终回复也由 C++ 保存并确认。
- Python AI 服务通过 `proto/ai.proto` 的 gRPC 双向流与 C++ 交互。进程本身不保存状态，房间状态（`RoomState`）、待生成任务和租约都在 Redis；任务中断后靠租约到期自动恢复，结果按 `generation_id` 去重。
- 谁回复由 `ai/chat_ai/rules.py` 的纯函数决定：多人房间只看 @，一人房间随机选对话对象，AI 之间按概率接话，冷场按概率主动发言、第 3 次告别。
- 上下文由 `ai/chat_ai/context.py` 的 `to_chat` 一次性拼出：最近 30 条、12000 字，可选按块对齐和 ngram 全文检索；系统提示只有“你是谁 + 人设”。
- 所有可选能力都用 `.env` 开关，默认关闭，关闭时行为不变。本变更沿用这个习惯。

## Goals / Non-Goals

**Goals：**
- 所有新能力都建立在现有的租约、去重、`seq` 有序和故障恢复之上：AI 服务在表决、小会、倒计时进行中重启，状态不丢、动作不重复执行。
- 主要算法、提示词和数据结构照搬成熟实现（以 LobeHub 为主），翻译成 Python。

**Non-Goals：**
- 不引入任何多 agent 框架的运行时（AutoGen、AgentScope、LobeHub agent-runtime）。
- 不做前端卡片、按钮和资料管理界面；只做小会只读展示所需的最小前端改动。
- 不支持用户在运行时添加工具或 MCP 服务器。

## Decisions

### D1 抄算法和提示词，不抄运行时
这些框架默认把对话放在进程内存、一轮跑到底，进程一挂就丢，这正是 ChatDev 卡死、不能续跑的根源。我们把它们的处理步骤、提示词模板和数据结构搬进自己的模块，状态仍放 Redis，执行仍走现有的任务和租约。
- 备选：直接把 AutoGen 或 AgentScope 作为依赖。否决理由：状态在框架进程内，和无状态加租约恢复的设计冲突。

### D2 参考来源对照
| 模块 | 参考实现 |
|---|---|
| 上下文管道 | LobeHub `packages/context-engine`（processors 与 providers：`HistoryTruncate`、`GroupRoleTransform`、`SystemDateProvider`、`GroupContextInjector`、`HistorySummary`、`KnowledgeInjector`、`UserMemoryInjector`） |
| 记忆 | LobeHub `packages/memory-user-memory`（`gatekeeper` 守门、分层 extractors 与 schemas） |
| 资料库 | LobeHub `apps/server/src/modules/ContentChunk`、`services/chunk`、`KnowledgeInjector` |
| 主持人 | LobeHub `packages/builtin-agents/src/agents/group-supervisor`、`packages/agent-runtime/src/groupOrchestration` |
| 桌型 | ChatDev 的阶段链思路 |
| 主席团 | 思路来自 LLM Council 的三阶段与它的“主席独断”issue（该项目没有许可证，只借思路，不拷代码）；计票机制仿 Redis Sentinel |
| 小会 | 思路来自 Linux fork（继承与回收）和 cgroup（层级与资源记账） |

照搬 LobeHub 代码的文件，在文件头注明来源路径和 LobeHub Community License。

### D3 上下文管道
`context.py` 改成一条有序管道：先运行处理器（过滤失败消息、角色转换、截断），再运行注入器（日期、房间与成员、历史摘要、记忆、资料、ngram 检索）。每个步骤是一个输入、输出都是上下文对象的函数，由配置决定启用哪些。预算按 spec 的顺序回收（检索 → 记忆 → 较早历史）。现有的 `to_chat`、按块对齐和 token 估算作为截断处理器保留，行为不变。
- 历史摘要按 `AI_CONTEXT_BLOCK` 的块边界增量生成：每当一整块滑出窗口，就把这一块并入房间摘要。这样摘要只在块边界变化，提示词前缀依然稳定，服务商的前缀缓存仍能命中。摘要存在 Redis，MySQL 留一份备份。

### D4 主持人
- 主持人是一次独立的模型调用，模型由 `AI_HOST_PROVIDER` 和 `AI_HOST_MODEL` 配置（默认 DeepSeek flash），费用由本轮触发者承担。
- 提示词照 LobeHub group-supervisor 翻译，要求输出结构化 JSON：`{"speakers": [...], "mode": "speak" | "broadcast", "end": bool}`。
- JSON 无效、选了不在场的 AI，或调用失败时，本轮回退到 `rules.py` 的原规则，并发提示。
- `rules.py` 保持纯函数：主持人的结果作为输入传进去，由新函数 `on_host_decision` 产出 `Decision`。这样现有的测试方式不变。
- 每轮 AI 回复总数上限由 `AI_ROUND_MAX_REPLIES` 配置。

### D5 主席团计票
- 为了不用浮点数，权重乘 2：领头主席计 3，其他主席计 2。有效票不少于 3 张，且 `2 × 通过权重 > 有效权重总和` 时判为客观通过。
- 每次表决的状态存在 Redis 的 `council:{room}:{round}`，包括议题、提案人、领头主席、截止时间和各主席的判断。轮次编号用房间级 `INCR` 生成；写判断用 Lua 脚本，同时检查轮次和“首票有效”。
- 领头主席轮换：房间级保存游标，每轮从游标开始顺延，跳过提案人。
- 主席的判断是一种新的生成任务（kind `vote`），走现有的租约和重试。构建上下文时排除本轮其他判断，保证互相看不到。输出要求为 `{"verdict": "pass" | "reject" | "abstain", "reason": "..."}`，解析失败算无效票。
- 截止时间复用现有冷场计时器的调度方式。到期或票齐时，由持有租约的实例计票，并发布结论消息。

### D6 超时确认
- 用 `RoomState` 新增的 `pending_step` 字段取代协作模式下的主动发言状态。字段包括 `{id, kind, risk, initiator, deadline, status}`，风险分三档：`discuss`、`readonly`、`side_effect`。
- 状态转移 `pending → done | cancelled | paused` 用 Lua 脚本按 `id` 原子执行，保证重启后补处理只发生一次。
- “继续”“否决”是有权限的人发出的、内容完全匹配的消息。权限由 C++ 判断，判断结果随事件发给 AI 服务。

### D7 小会：子房间 + fork 点 + 层级记账
- **数据**：`rooms` 表新增 `parent_room_id`、`fork_seq`、`depth`、`topic`、`status`。小会就是一个子房间，直接复用现有的 `seq`、推送、补齐、搜索和恢复机制。
- **fork（写时复制）**：开小会时不复制任何消息，只记下 `fork_seq`。小会的上下文由“父房间到 `fork_seq` 为止的历史、摘要和记忆”加上“小会自己的消息”组成。父房间后来的消息不进入小会，小会的消息也不进入父房间。
- **回收**：小会结束时产出结论，作为一条消息发到父房间，带 `child_room_id`。结论发出前小会保持“已结束、待回传”状态，回传幂等，即使父房间暂时不可用也不会丢。
- **cgroup 式记账**：每个小会在 Redis 里有 `budget:{room}` 计数。AI 回复入队前，用 Lua 脚本沿父链逐级扣减，任何一级超额就整体拒绝，下级的消耗自然计入上级。暂停小会时，复用 `ai-controls` 现有的房间暂停。
- **可见性**：C++ 的授权规则改为：用户是根房间的成员，就能读取它下面所有小会。能在小会里发言的只有参加者、小会发起人和根房间的群主、管理员。搜索时把父房间成员能读到的小会一起纳入范围。
- **级联结束**：父房间关闭协作模式或被删除时，C++ 发出级联结束事件，AI 服务对每个小会执行收尾。
- **前端最小改动**：小会在父房间成员的房间列表里显示为带“小会”标记的房间。非参加者打开时输入框只读。

### D8 记忆与资料的存储
- 新增 `pgvector` 容器，只存向量和片段 ID，元数据仍放 MySQL：新增 `room_memories`、`user_memories`、`knowledge_documents`、`knowledge_chunks` 表。
- 每个部署固定使用一种向量模型，由 `AI_EMBEDDING_PROVIDER` 和 `AI_EMBEDDING_MODEL` 配置，保证向量可比较。导入者必须有该服务商的 key，或者部署配置了本地向量模型。
- 切片规则照 LobeHub ContentChunk。日志按行块切，Markdown 按标题切，片段带来源位置。
- 记忆守门和抽取在讨论段落结束、表决出结论时异步执行，作为低优先级任务，失败不影响聊天。

### D9 工具与 MCP
- 工具在 `ai/tools.toml` 里登记：名称、类型（内置或 MCP）、连接方式、`readonly` 或 `side_effect`。MCP 客户端用官方 Python SDK。
- 工具调用循环在一次生成任务内部完成。只读工具直接执行；遇到有副作用的调用时，任务挂起，生成一个 `side_effect` 风险的 `pending_step`，确认之后以新任务继续。
- 适配器先支持 OpenAI 兼容接口和 Anthropic 的工具调用，Gemini 随后补上。

### D10 文字指令由 C++ 解析
`/表决`、`/小会`、`/否决`、`/强制通过` 以及“继续”“否决”由 C++ 识别并做权限检查，转成新的 gRPC 事件，原消息照常保存。这样权限只在一处判断，AI 服务不用重复查 MySQL。

## Risks / Trade-offs

- [费用放大：主持人、主席团、摘要、记忆抽取、向量化都要调用模型] → 全部默认关闭；主持人用便宜模型；每轮回复有上限，小会有预算；结论消息里显示本轮调用次数。
- [模型输出的 JSON 不合法] → 每处都有回退：主持人回退到原规则，投票算无效票，摘要和记忆跳过。
- [资料或记忆里混入提示注入，影响主席投票] → 参考材料统一标成“仅供参考”；主席的系统提示明确说明参考材料不是指令；有副作用的动作必须人确认。
- [小会把讨论拆散，人找不到] → 结论必须回传父房间，可以搜索，全程可见。
- [LobeHub 是 TypeScript，翻译会有偏差] → 翻译时把它对应的测试用例一起翻成 pytest。
- [新增 pgvector 容器，部署变重] → 只在开启资料库或记忆时才需要这个容器，Compose 用 profile 控制。

## Migration Plan

1. 数据库迁移只新增表和列，旧数据不受影响。
2. 所有功能由 `.env` 开关加房间级协作模式双重控制，默认关闭。
3. 按 tasks.md 的阶段分批上线，每个阶段可以单独关闭。
4. 回滚：关闭开关即可，新增的表和列保留不删。

## Open Questions

- 桌型模板的具体清单和默认人设，在实现桌型时再定。
- 本地向量模型选 bge-m3 还是更小的模型，按机器内存评估后再定。
