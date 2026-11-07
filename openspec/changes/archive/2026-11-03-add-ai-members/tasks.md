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
