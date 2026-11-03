# Tasks

## 1. 数据库与配置

- [x] 1.1 新增 `migrations/V3__ai_members.sql`：`room_members.role`（已有房间最早加入的人类成员设为群主）、`ai_profiles`、`ai_member_requests`、`user_api_keys`，写入 4 个预置 AI 成员；验证空库和已有库（含你的数据）上 `flyway info` 都显示 V3 成功，`test_schema.py` 断言新表、4 个 AI 成员，以及每个已有房间恰好一名群主
- [x] 1.2 新增一次性的 `db-users` 服务（D15），创建并授权 `ai` 账号；集成测试用 `ai` 账号验证：能读 `user_api_keys`、`messages`、`ai_profiles` 和 `members` 的三列，读 `users` 表和 `members` 的其他列被拒绝；改 `AI_MYSQL_PASSWORD` 后重启，新密码生效
- [x] 1.3 `.env.example` 和 `docker-compose.yml` 加入 D13 的配置；`AI_KEY_ENCRYPTION_KEY` 只传给 ai 服务，root 密码只传给 mysql 和 db-users；CI 工作流为 `AI_GRPC_TOKEN`、`AI_KEY_ENCRYPTION_KEY`、`AI_MYSQL_PASSWORD` 生成随机值；验证 `docker compose config` 中 app 和 ai 容器看不到不该有的密钥

## 2. 群主和管理员（C++）

- [x] 2.1 `RoomService::requireRole`，创建房间时写入群主，退出时检查群主（D14）；集成测试覆盖 `chat-rooms` 中“退出房间”的全部场景，包括群主未转让就退出和群主是最后一名人类
- [x] 2.2 转让群主、任命和撤销管理员的接口，以及 `role_changed` 事件；成员列表返回 `role`；集成测试覆盖“群主”“管理员”“已有房间的群主”的全部场景

## 3. 接口定义与 AI 服务骨架

- [x] 3.1 编写 `proto/ai.proto`（D2）；Python 端用 `grpcio-tools` 生成代码，验证 `python -c "import ai_pb2_grpc"` 成功
- [x] 3.2 搭建 `ai/` 服务：配置读取、Redis 和 MySQL（`ai` 账号）连接、`grpc.aio` 服务端、共享令牌校验；Dockerfile 和 Compose 服务（只在内部网络上开放端口，有健康检查）；单元测试覆盖令牌校验，`docker compose up --wait` 时 ai 服务为 healthy
- [x] 3.3 `ai/agents.toml` 写入 4 个预置 AI 的服务商、模型名和人设；启动时校验与 `ai_profiles` 的 `slug` 一致，不一致时拒绝启动；单元测试覆盖校验

## 4. 模型适配器

- [x] 4.1 定义 `Provider` 接口和 `ProviderFactory`（D8），实现 `FakeProvider`（回显、超时、拒绝 key 三种行为，只在 `AI_FAKE_PROVIDERS=1` 时可用）；单元测试覆盖工厂分发，以及未开启时无法创建 fake
- [x] 4.2 实现 `OpenAiCompatible`（OpenAI、DeepSeek）、`AnthropicProvider`、`GeminiProvider` 的 `stream` 和 `validate`；每个适配器用录制好的流式响应做单元测试，验证逐段文本、结束和 key 被拒时的异常
- [x] 4.3 上下文构建：读热缓存（缺失时查 MySQL），角色映射加名字前缀，按条数和字数截断；单元测试覆盖多人、多 AI 的映射和截断

## 5. API key

- [x] 5.1 Python：AES-256-GCM 加解密（AAD 为 user_id:provider）和 `StoreKey`（验证、加密、写库、返回末 4 位）；单元测试覆盖加解密往返、换 AAD 后解密失败、日志中不出现明文
- [x] 5.2 C++：`GET /api/keys`、`PUT /api/keys/{provider}`（转发 `StoreKey`）、`DELETE /api/keys/{provider}`；集成测试（fake 服务商）覆盖 spec `api-keys` 的全部场景，并检查数据库中没有明文、应用日志中没有明文

## 6. gRPC 桥接（C++）

- [x] 6.1 Dockerfile 加装 `libgrpc++-dev`、`protobuf-compiler-grpc`，CMake 生成并编译 proto 代码；验证 build 和 unit 阶段都成功
- [x] 6.2 实现 `AiGatewayClient`（D3、D4）：callback 双向流、令牌、写队列、待确认表、退避重连和重发；单元测试用进程内的假 gRPC 服务端验证：事件按序到达、断开后重发未确认的事件、写操作不会并发
- [x] 6.3 处理 `AiAction`：`Chunk` 发布 `ai_chunk`；`Final` 交给 room worker 调用 `saveAiMessage`；`Failed` 和 `Notice` 发布定向事件；单元或集成测试验证每种动作的效果

## 7. C++ 的 AI 成员与消息

- [x] 7.1 `MessageService::saveAiMessage`（D5）：检查 AI 仍在房间、`generation_id` 去重、不计入限流；集成测试覆盖重复保存只有一条、被移出后保存失败
- [x] 7.2 `RoomHub` 支持 `only_member_ids` 定向事件；集成测试验证只有目标成员的连接收到，并且字段已被去掉
- [x] 7.3 AI 目录、申请邀请 AI、待审批列表、批准和拒绝、移出 AI（D11、D16），以及 `ai_request`、`ai_request_done` 事件；成员列表返回 `slug` 和 `provider`；集成测试覆盖 spec `ai-members` 的全部场景（包括两名管理员先后审批同一申请）和 `chat-rooms` 修改后的成员类型场景
- [x] 7.4 在人类消息保存、成员变化、房间删除后发出 `MessageSaved`、`MembersChanged`、`RoomDeleted`；集成测试验证 AI 服务不可用时聊天、邀请和退出都正常

## 8. 触发规则（Python）

- [x] 8.1 房间状态（D6）和事件去重、`Ack` 时机；单元测试用 fakeredis 覆盖重复事件只处理一次
- [x] 8.2 提及解析和 A、B 规则、对话对象的锁定与切换、只在有 key 的 AI 中随机；单元测试用固定的随机源覆盖 spec `ai-replies` 中提及、多人房间、一人房间对话对象的全部场景
- [x] 8.3 接话链：跳数衰减、候选规则、付费者是链条起点；单元测试覆盖 p = 1、p = 0、第 3 和第 4 跳的概率
- [x] 8.4 生成锁与 `pending` 合并、生成流程（检查 key -> 调用 -> 合并片段每 100 毫秒发 `Chunk` -> `Final`）、超时与出错时发 `Failed`；单元测试覆盖合并只再生成一次、可恢复错误自动重试与永久错误终止
- [x] 8.5 冷场计时器（D7）：ZSET 抢占、按已发条数衰减、第 3 条告别后结束、人发消息后重新计数；单元测试用可控时钟覆盖 spec 中冷场主动发言和对话结束后续上的全部场景

## 9. 端到端集成测试

- [x] 9.1 `scripts/test.sh` 用 fake 服务商、短冷场时长和固定概率启动 ai 服务；新增 `tests/integration/test_ai_replies.py`：多人房间提及、一人房间锁定和换人、接话（p = 1）、合并回复、主动发言到告别、缺 key 的提示只有触发者收到、生成失败、片段不入历史、AI 消息不占用户限额
- [x] 9.2 故障测试：生成过程中重启 ai 服务，C++ 重连后不出现重复回复；ai 服务停止期间人类聊天正常

## 10. 前端

- [x] 10.1 设置页（D12）：按服务商保存、删除 key，显示末 4 位；vitest 覆盖保存中、验证失败、删除
- [x] 10.2 申请邀请 AI（没有 key 时置灰并提示，普通成员显示“等待审批”）、群主和管理员的审批入口、成员列表显示 AI 和角色、群主的转让和任命操作、群主退出时提示先转让、`@` 提示列表；vitest 覆盖提及列表的过滤和审批数量的更新
- [x] 10.3 `chat-socket.js` 和 `ChatView.vue` 处理 `ai_chunk`、`ai_failed`、`ai_notice`，按 `generation_id` 把生成中的文本替换为正式消息；vitest 覆盖替换、失败移除、乱序到达（正式消息先于最后一个片段到达）
- [x] 10.4 浏览器手动验证（fake 服务商）：普通成员申请、管理员审批、一人房间聊天和换人、看到逐段输出、主动发言和告别、多人房间提及、缺 key 提示、群主转让后退出

## 11. 文档与整体验证

- [x] 11.1 README：架构图加入 AI 服务、配置项说明、如何填写 key、如何用 fake 服务商本地运行；验证按 README 从 `.env.example` 全新启动可以完成一次 fake 对话
- [x] 11.2 用一个真实服务商的 key 手动验证一次端到端（需要你提供 key，或由你在本地执行），记录结果；没有 key 时记为未完成并说明
- [x] 11.3 `scripts/test.sh` 全部通过，推送后 CI 通过

## 12. 验证问题修复与恢复策略

- [x] 12.1 原子提交事件状态、任务和完成标记，提交后 Ack，补 C++ 未 Ack 超时重发及失败重放测试
- [x] 12.2 Redis 持久生成任务、租约和完整结果，固定 generation_id、递增 attempt，生成中重启自动恢复、结果重复投递只保存一次
- [x] 12.3 冷场任务执行前重新检查计时，已开始允许完成；用三位成功标记，在同 epoch 保存确认后置位，111 才结束
- [x] 12.4 前端统一实时和历史正式消息收尾、重连清草稿、累计快照按 attempt/sequence 替换，并补回归测试
- [x] 12.5 补设置页交互测试、无 key 禁用与 Python 单测 CI 步骤，修复验证报告中的覆盖缺口
- [x] 12.6 完整本地回归及真实 DeepSeek 生成中断恢复验证，更新验证记录；远端 CI 仍按 11.3 跟踪

## Workflow follow-up

- 实施完成后运行 `/opsx:verify`，再提交并 `/opsx:archive`。

## 实施记录

记录时间：2026-11-03，分支 `feat/add-ai-members`。进度 32/34。

### 已完成并验证

- 数据库：Flyway `V3` 已在空库和已有库（800 个房间）上执行；已有房间都有恰好一名群主。执行前的库导出在本机临时目录，没有进仓库。
- `ai` 账号：`db-users` 服务授权后，实测允许和拒绝的访问都符合 D15；集成测试覆盖。
- 群主和管理员：创建者为群主、转让、任命和撤销、群主未转让不能退出；集成测试 `test_roles.py`。
- Python AI 服务（`ai/`）：配置、预置 AI 校验、三种适配器加假模型、key 加解密、上下文、触发规则、生成锁与合并、冷场计时器、gRPC 服务端；60 个单元测试。
- C++：gRPC 网关客户端（确认、断线重发、退避重连）、AI 动作处理、事件上报、AI 消息保存、定向事件、AI 成员与 key 接口；单元测试 26 个用例（其中网关 3 个）。
- 前端：设置页、成员面板（角色、邀请与审批、移出）、`@` 补全、AI 流式草稿（处理片段晚于正式消息到达）、群主退出提示；vitest 42 个。
- 完整测试 `scripts/test.sh`：第一轮 163 通过；AI 服务停机与恢复三步通过；第二轮 8 通过；日志中没有 key 明文。
- 浏览器手动验证（假模型）：填 key（含被拒的 key）、邀请 AI、`@` 补全、流式草稿被正式消息替换、一人房间锁定、两条主动发言加告别、多人房间缺 key 提示只给触发者、普通成员申请与群主审批、群主未转让被拦、转让后退出、只在有 key 的 AI 中随机。
- 全新启动：用 `.env.example` 生成配置，在空数据卷上从零启动，完成一次假模型对话；V1–V3 全部执行。

### 与计划的偏差

- 9.2 测的是“AI 服务停机期间发消息，恢复后只回复一次”，而不是“生成过程中重启”。生成过程中重启时，事件已被确认，这次回复会丢失（不会重复）；这与 design 的 Non-Goals 一致。
- Flyway 用 MariaDB 驱动（`jdbc:mariadb://`），已写进 design。
- aiomysql 0.3.2 搭配 PyMySQL 1.2.3 无法转义 `bytes` 参数，密文和 nonce 改为十六进制传入、SQL 里 `UNHEX()`。
- Anthropic SDK 2.x 改用 `httpx2`，认证头是 `authorization: Bearer`；测试按此调整。
- Ubuntu 的 gRPC 包 CMake 配置缺 `c-ares::cares`，`CMakeLists.txt` 手动补了这个目标。
- 迁移时序：V3 执行后、新版 C++ 启动前，旧版 C++ 建的房间没有群主；开发库里出现 4 个并已修复，处理方法写进了 design 的迁移计划。
- 成员面板在窄屏下会盖住输入框，已加“关闭”按钮。

### 还没做

- 11.2：用真实服务商的 key 端到端验证一次。需要你提供 key，或在本地设置里填真实 key 后自己试。`ai/agents.toml` 里的模型名（`deepseek-chat`、`gpt-4o-mini`、`claude-sonnet-4-5`、`gemini-2.5-flash`）未经真实调用验证，可能需要按各家当前型号调整。
- 11.3：前端在上次完整测试之后又有改动（成员面板关闭按钮等），需要再跑一次 `scripts/test.sh`，并推送后确认 CI 通过。
- 之后：`/opsx:verify`，再 `/opsx:archive`。

### 已知问题（不阻塞）

- MySQL 提示 `VALUES()` 写法已弃用（`keys.py` 的 `ON DUPLICATE KEY UPDATE`），以后改成别名写法。
- C++ 进程崩溃会丢失未确认的事件（对应的 AI 回复不会发生），见 design 的风险。
- 首次推送和开 PR 时 GitHub Actions 曾不自动触发，后来恢复；如再出现，用 `gh workflow run CI --ref <分支>` 手动触发。


### Codex 续跑验证（2026-11-03）

- 验证提交：`ef6fb9fa1c252e0daf40be7954c26fa041c89e8b`（`feat/add-ai-members`）。
- `scripts/test.sh` 完整运行成功（退出码 0）：第一轮 163 通过、12 跳过；AI 故障 prepare/down/up 各 1 通过、2 跳过；第二轮 8 通过；最后恢复正常配置，服务健康。
- C++ 单元测试和前端测试/构建阶段复用了 Docker 构建缓存；本轮集成测试实际重新运行。
- 另行构建 `ai/Dockerfile` 的 `test` 阶段，Python 单元测试实际重新运行，60 通过。
- `openspec validate add-ai-members --strict` 通过。
- 本轮测试日志在本机临时目录：`/private/tmp/cppaiserver-openspec-apply-tests.log`、`/private/tmp/cppaiserver-openspec-ai-tests.log`，未加入 Git。
- 11.2 仍未完成：本轮未使用真实服务商 key，也未进行真实模型调用。
- 11.3 的本地完整测试部分完成；推送到 `github.com/anwalld/CppAiWebServer` 并触发 CI 被自动审批拒绝，需用户明确授权该远端后继续。推送和 CI 未执行，该任务保持未勾选。


### 真实模型验证与省钱配置（2026-11-03）

- 用户通过 macOS 隐藏输入框提交 DeepSeek key；本地程序直接调用项目的 key 保存接口，验证后加密入库。密钥明文未回显给助手，也未写入临时文件。
- 按用户的最低成本要求，将 DeepSeek 模型由旧的 `deepseek-chat` 更新为官方当前较便宜的 `deepseek-flash`，并显式传入 `thinking.type=disabled`。官方依据：https://api-docs.deepseek.com/quick_start/pricing/ 和 https://api-docs.deepseek.com/api/create-chat-completion/ 。
- 11.2 完成：真实服务商模式（`AI_FAKE_PROVIDERS=False`），专用验证账号通过 HTTP 创建房间、邀请 DeepSeek，通过 WebSocket 发送一次简短消息。
- 本次输出上限 128 tokens，关闭接话并将冷场计时延后至一天，避免额外调用；真实模型回复“连接成功”。收到 2 个 `ai_chunk`，拼接与最终消息相同，`generation_id=3173fd89537d475995f4089e1269b6f2`、`seq=2`。历史查询只有 1 条人类消息和 1 条 AI 正式消息。
- 首次验证脚本在 WebSocket 参数兼容处退出，未发出生成请求；修正脚本后验证通过。整个验证仅触发 1 次真实生成。未获取实际账单金额。
- 验证后删除了专用测试房间，避免后续主动发言产生费用；key 仍按项目机制加密保存在专用验证账号。
- Python 单元测试实际重新运行，62 通过；新增测试确认仅 DeepSeek 发送关闭思考模式的参数，OpenAI 请求不受影响。
- 当前进度 33/34；最终代码的本地完整测试已通过，11.3 仍需远端 CI，推送待用户明确授权。


### 最终本地回归（2026-11-03）

- `scripts/test.sh` 最终完整运行成功，退出码 0：第一轮 163 通过、12 跳过；AI 停机恢复的 prepare/down/up 三个阶段各 1 通过、2 跳过；第二轮 8 通过。日志中的测试 key 明文检查通过，最后全部服务恢复正常配置并健康。
- 修复故障测试的时序误报：一人房间的 3 秒冷场主动发言可能被当成重连后的重复回复；改为两名人类成员的房间，并明确提及 Claude，继续验证重连后恰好回复一次。
- 修复登录测试的时序误报：分别耗尽两个用户名的尝试次数后才一起检查，可能跨过前一个用户名的 1 秒退避窗口；改为每个用户名耗尽次数后立即检查，并保留相同错误码和完整限流断言。未修改应用登录行为。
- 期间一次 Docker Hub 匿名令牌请求因 EOF 中断构建，重试后恢复。最终完整日志：`/private/tmp/cppaiserver-openspec-final-tests.log`。
- Python 测试此前在同一份最终 AI 代码上实际运行，62 通过；C++ 与前端测试/构建复用未变代码的 Docker 缓存。
- 真实模型验证仍只有 1 次生成；本轮回归全部使用假模型。
- 11.3 保持未勾选：推送与远端 CI 尚未获得所需的具体远端授权，没有执行。当前修改尚未提交。


### Claude 接手验证（2026-11-03）

- 接手时工作区有 Codex 未提交的第 12 组改动（37 个文件）。通读引擎代码：持久任务、租约与续租、完整结果先落 Redis 再投递、`generation_id` 固定与尝试编号递增、超过尝试上限终止，均已实现。
- 单元测试实际运行：Python 76 通过，前端 45 通过（完整测试中 49），C++ 全部通过。
- `scripts/test.sh` 完整运行，退出码 0：第一轮 165 通过、14 跳过；AI 故障五个阶段（prepare、down、up、generating、recovered）各 1 通过；第二轮 8 通过；日志 key 明文检查通过。
- 12.6 真实 DeepSeek 生成中断恢复：用 Codex 准备的验证脚本（`/private/tmp/cppai-real-recovery-*`，专用账号 `realtest_3db5deded2`）。
  - 第一次运行未触发中断即收到正式回复，脚本断言失败。推测是脚本的时序问题：回复很快，正式消息先于首个带文本的片段到达，脚本收到正式消息就结束等待。该次未记录片段细节，推测未直接证实。
  - 第二次（加片段记录的副本）通过：第 1 次尝试收到首段文本后强杀 AI 服务并重启，第 2 次尝试沿用同一 `generation_id`，历史中恰好 1 条正式回复（1108 字），测试房间已删除，AI 服务恢复正常配置。
  - 两次运行共触发约 3 次真实生成（含被中断的一次），输出上限 1024 tokens，模型 `deepseek-flash`。
- 11.3 待 verify 通过后推送并跑 CI。
- verify：只有 11.3 未完成；修正了 design D2 接口清单过期和一个与新行为不符的测试名（`62be6cb`）。
- 11.3 完成：推送 `feat/add-ai-members`，PR #4 的 CI 运行 41614811210 通过（约 15 分钟）。
