# Design

## Context

动机和范围见 proposal.md，行为要求见 `specs/ai-members`、`specs/ai-replies`、`specs/api-keys` 和 `specs/chat-rooms`。

和本设计有关的现状：

- `members.type` 已支持 `ai`，`user_id` 可以为空；消息表不区分发送者类型。
- `MessageService::save` 要求传入人类用户的 `Session`，并在其中检查会话和消息限流；AI 消息需要另一个入口。
- 消息广播：C++ 发布到 Redis 频道 `room:{id}`，所有 C++ 实例订阅后转发给 WebSocket。`RoomHub::onChannelMessage` 对普通消息直接转发，其他事件先解析再处理。`RoomHub` 记录了每条连接属于哪个成员。
- Redis 热缓存 `room:{id}:recent`（ZSET，最近 200 条，TTL 24 小时）由 C++ 维护。
- Drogon 的 HttpClient 不能流式读取响应，所以 C++ 不能用 HTTP 去读取流式回复。
- Ubuntu 24.04 的 apt 源有 `libgrpc++-dev` 和 `protobuf-compiler-grpc` 1.51.1，支持 callback 风格的异步 API（`ClientBidiReactor`）。
- 第一阶段确立的原则：C++ 实例无状态、MySQL 是权威数据、Redis 存热数据和共享状态。

## Goals / Non-Goals

**Goals:**

- C++ 和 Python 都保持无状态，可以各自多实例运行。
- 最终消息只由 C++ 的消息服务保存，`seq` 分配和去重只有一份实现。
- key 的明文只出现在 Python 进程内存里和发往服务商的请求中。
- 不依赖真实服务商也能跑完全部测试（CI 没有真实 key）。

**Non-Goals:**

- 事件的持久化投递：C++ 进程崩溃时，内存里尚未被确认的事件会丢失（对应的 AI 回复不会发生，人类消息不受影响）。
- 回复内容审核、长期记忆、用户自建 AI。
- 平台侧的费用统计和额度。

## Decisions

### D1 整体结构

```text
浏览器 --WS--> C++ 聊天服务 ----保存、广播（不变）----> MySQL / Redis
                  |   ^
     房间事件     |   |  回复片段、最终消息、提示、确认
   （gRPC 双向流） v   |
               Python AI 服务（ai/）
                  |-- 触发规则、对话状态、冷场计时器      状态全部在 Redis（ai:* 键）
                  |-- 上下文：读 Redis 热缓存，缺失时查 MySQL
                  |-- key：读 MySQL 的 user_api_keys 并解密
                  |-- ProviderFactory --> OpenAI 兼容 / Anthropic / Gemini / Fake（仅测试）
```

C++ 作为 gRPC 客户端，每个实例维护一条到 AI 服务的长连接双向流。Python 是服务端，只在 Compose 内部网络上开放端口。C++ 启动时不依赖 AI 服务：连接失败时按退避重连，期间人类之间的聊天不受影响。

### D2 接口定义（`proto/ai.proto`）

```text
service AiGateway {
  rpc Connect(stream GatewayEvent) returns (stream AiAction);   // 长连接
  rpc StoreKey(StoreKeyRequest) returns (StoreKeyReply);        // 验证、加密、保存
}

GatewayEvent（C++ -> Python），带 event_id：
  MessageSaved    房间、seq、发送成员（类型、user_id、AI 标识）、内容、generation_id（AI 消息才有）、
                  房间快照（人类成员的 user_id 列表、AI 成员的标识、member_id、服务商）
  MembersChanged  房间快照（AI 被邀请或移出、人加入或退出时）
  RoomDeleted     房间 id

AiAction（Python -> C++）：
  Ack       event_id
  Chunk     room_id、generation_id、ai_member_id、文本片段
  Final     room_id、generation_id、ai_member_id、完整内容
  Failed    room_id、generation_id、ai_member_id、通知对象 user_id、原因
  Notice    room_id、通知对象 user_id、代码（如 api_key_required）、服务商
```

- 每个事件都带上房间快照，Python 不需要回查房间成员，也就不需要访问房间表。
- 鉴权：C++ 在流的 metadata 里带共享令牌 `AI_GRPC_TOKEN`（来自 `.env`），Python 校验后才接受。
- `StoreKey` 是普通的一元调用，只用于保存 key。

### D3 事件的确认与重发

- C++ 给每个事件分配一个在本进程内递增的 `event_id`，事件发出后放进“待确认”表，收到 `Ack` 后删除。
- 流断开后按退避重连（0.5 秒起翻倍，最多 10 秒），重连后按顺序重发待确认表里的全部事件。待确认表有上限（默认 10000 条），超出时丢弃最早的事件，并记一条错误日志。
- `instance` 和 `event_id` 共同标识事件。房间锁内将状态、待生成任务、计时器和已处理标记通过 Lua 原子提交。失败时不写已处理标记，成功后重复事件只回 Ack。标记保留 24 小时。
- Python 在状态和待生成任务提交成功后回 `Ack`。C++ 在连接仍正常但 Ack 超时（默认 5 秒）时也重发原事件。

### D4 gRPC 与 Drogon 的桥接

- 使用 gRPC 的 callback API（`ClientBidiReactor`）。gRPC 的回调运行在 gRPC 自己的线程上，收到的 `AiAction` 用 `drogon::app().getLoop()->queueInLoop` 投递到 Drogon 的线程处理，在那里用协程调用 `MessageService` 和 `RoomHub`。
- 写操作：gRPC 同一时刻只允许一个未完成的写。发送队列用互斥锁保护，上一个 `OnWriteDone` 回来后再写下一条。
- 生命周期：reactor 对象由一个 `AiGatewayClient` 单例持有；断开（`OnDone`）后由它在 Drogon 的定时器上安排重连。
- 这一层与业务无关，可以单独测试：用一个假的 Python 服务端验证断线重连和重发。

### D5 C++ 侧的改动

- `MessageService::saveAiMessage(aiMemberId, roomId, generationId, content)`：检查 AI 仍是房间成员；`client_msg_id` 用 `generation_id`，同一次生成重复保存只会得到一条消息；不经过消息限流；其余流程（锁房间行、`last_seq + 1`、写入、提交、热缓存、广播）与人类消息相同。
- `Final` 的处理顺序：用 `room_id` 对应的 room worker 执行，与人类消息共用串行队列。保存成功后照常广播，并向 AI 服务发出 `MessageSaved`（这时带 `generation_id`），用于接话链。
- `Chunk`：C++ 把它包装成 `{"type":"ai_chunk", ...}` 发布到 `room:{id}`，不经过 worker、不落库。Python 每约 100 毫秒合并一次片段再发送，避免每个字一条 Redis 消息。
- 只发给某个成员的事件：发布的 JSON 带 `only_member_id`。`RoomHub::onChannelMessage` 只把它发给属于该成员的连接，并在转发前去掉这个字段。`Notice` 和 `Failed` 的原因都用这种方式发送；`Failed` 另外向全房间发一个不带原因的 `ai_failed`，让所有人移除“正在输入”。
- 何时发事件：人类消息保存成功后发 `MessageSaved`；邀请或移出 AI、人加入或退出时发 `MembersChanged`；房间删除时发 `RoomDeleted`。都在提交之后发送，发送失败不影响 HTTP 和 WebSocket 的结果。

### D6 触发规则的实现（Python）

每个房间在 Redis 中有一个状态哈希 `ai:room:{id}`：

```text
humans        人类成员的 user_id 列表（来自最新快照）
ais           AI 成员：标识 -> member_id、服务商
target        当前对话对象的标识（一人房间），空表示没有对话
proactive_mask 当前对话已成功保存的三条主动发言，三个位分别代表一次成功
chain         当前接话链：起点 user_id、当前跳数
pending       生成期间到达的、需要合并回复的触发
```

处理 `MessageSaved`：

```text
人类消息：
  清零 chain，起点 = 发送者；proactive_mask = 0；重置冷场计时
  人数 >= 2：被提及的每个 AI -> 排入生成（付费者 = 发送者）
  人数 == 1：
    提及了 AI     -> target = 第一个被提及的 AI
    没提及、无 target -> target = 发送者有 key 的 AI 中随机一个；没有就发 Notice 并结束
    排入生成（target，付费者 = 发送者）
AI 消息（带 generation_id）：
  跳数 = chain.跳数 + 1，按 p / 2^(跳数 - 2) 抽签，不中则结束
  候选：人数 >= 2 时是被这条消息提及的 AI；人数 == 1 时是房间里其他有 key 的 AI（随机一个）
  排入生成（付费者 = chain 起点）
```

- 随机数和抽签由一个可注入的随机源提供，测试中可以固定结果。
- 房间 JSON 内持久保存 pending 和 active。领取任务时写入唯一 owner、尝试编号和带续租的租约。同一 AI 在开始生成前的多次触发合并，以最后一次触发者和 epoch 为准。后台扫描 ai:work，进程退出后租约到期即可恢复。
- 生成前检查付费者是否有该服务商的 key，没有就发 `Notice`。

### D7 冷场计时器

- ai:timers 是房间 timer_due 的索引。扫描到期房间后，领取时在房间锁内重新核对 timer_due 与 epoch，并原子提交待生成任务。用户消息先到则重设计时，旧任务不开始；任务已经开始生成后用户再发消息，允许原回复完成。
- 到点处理：

```text
没有 target 或人数 != 1 -> 不处理
n = popcount(proactive_mask) + 1
按第 n 条的概率抽签
  中签 -> 排入任务（slot = n - 1，第 3 条为告别），等待保存确认
  不中 -> 保持 mask，安排下一次计时
成功保存 -> 同 epoch 置 slot 位，111 才结束，否则安排下一次计时
```

- 人类每发一条消息，epoch 加一、mask 清零，并把计时重设为 `now + 冷场时长`。主动发言成功保存且收到 MessageSaved 后，对同 epoch 的 slot 执行 mask |= 1 << slot；mask == 7 才结束。失败不置位，重复确认幂等，旧 epoch 不影响新对话。

### D8 模型调用：策略工厂

```text
class Provider(Protocol):
    async def stream(model, system, messages, api_key, max_tokens) -> AsyncIterator[str]
    async def validate(api_key) -> None    # 失败时抛 KeyRejected

ProviderFactory.create(provider):
  "openai"    -> OpenAiCompatible(base_url="https://api.openai.com/v1")
  "deepseek"  -> OpenAiCompatible(base_url="https://api.deepseek.com")
  "anthropic" -> AnthropicProvider        （anthropic 官方 SDK）
  "gemini"    -> GeminiProvider           （google-genai 官方 SDK）
  "fake"      -> FakeProvider             （仅在 AI_FAKE_PROVIDERS=1 时可用）
```

- 预置 AI 的配置在 `ai/agents.toml`：标识、显示名、服务商、模型名、人设（system prompt）。具体模型名在实施时按各家当时可用的型号填写，以后改配置即可，不改代码。
- 上下文：读取 `room:{id}:recent` 中最近 30 条消息，热缓存缺失时查 MySQL；再按约 12000 个字符截断，丢掉最早的。由当前 AI 发出的消息作为 `assistant`，其他人和其他 AI 的消息作为 `user`，并加上 `名字：` 前缀，这样模型能分清多方对话。
- 限制：单次最多输出约 1000 字（换算成各家的 `max_tokens`），单次生成超时 60 秒。可恢复错误自动重试，默认最多 3 次尝试，指数退避；缺 key 和 key 无效为终止错误。
- 测试时，`FakeProvider` 把每个服务商都替换掉，按固定规则回复（例如回显最后一条消息），通过配置还能模拟超时和 key 被拒。只有以 `fake-` 开头的 key 能通过验证。

### D9 API key 的保存

- 表 `user_api_keys(user_id, provider, ciphertext, nonce, last4, updated_at)`，主键 `(user_id, provider)`。
- 加密：AES-256-GCM，附加数据（AAD）为 `"{user_id}:{provider}"`，防止把密文挪给别的用户或服务商使用。主密钥 `AI_KEY_ENCRYPTION_KEY`（32 字节，base64）只配置在 AI 服务的容器里。
- 保存流程：浏览器 `PUT /api/keys/{provider}` -> C++（校验会话，不记录请求体）-> gRPC `StoreKey(user_id, provider, key)` -> Python 调用 `validate`，加密后写入 MySQL -> 返回末 4 位。
- 查询和删除不需要明文：C++ 直接读或删 `user_api_keys` 的元数据行。
- 邀请 AI 时的 key 检查也由 C++ 查这张表（只看是否存在）。
- AI 服务使用单独的 MySQL 账号 `ai`，权限见 D15。

### D10 数据库（Flyway `V3__ai_members.sql`）

- `room_members` 新增 `role ENUM('owner','admin','member') NOT NULL DEFAULT 'member'` 和 `role_changed_at`。已有房间：把每个房间 `joined_at` 最早的人类成员设为 `owner`（D14）。
- `ai_profiles(member_id PK -> members.id, slug UNIQUE, provider)`。
- 插入 4 个 `type = 'ai'` 的成员（`display_name` 为 DeepSeek、GPT、Claude、Gemini）和对应的 `ai_profiles`。
- `ai_member_requests(room_id, member_id, requested_by, created_at)`，主键 `(room_id, member_id)`，房间删除时级联删除（D16）。
- `user_api_keys` 如 D9。
- 模型名和人设不进数据库，只在 `agents.toml` 里；`slug` 是两边的关联键。

### D11 HTTP 和 WebSocket 接口

```text
GET    /api/ai-agents                         AI 目录，带“我是否有 key”
POST   /api/rooms/{id}/ai-members  {slug}     申请邀请 AI（检查 key；群主和管理员直接加入，
                                              其他人创建申请）→ 返回 joined 或 pending
GET    /api/rooms/{id}/ai-requests            待审批的申请（群主、管理员）
POST   /api/rooms/{id}/ai-requests/{slug}/approve
POST   /api/rooms/{id}/ai-requests/{slug}/reject
DELETE /api/rooms/{id}/ai-members/{slug}      移出 AI（群主、管理员）
POST   /api/rooms/{id}/owner       {username} 转让群主
PUT    /api/rooms/{id}/admins/{username}      任命管理员
DELETE /api/rooms/{id}/admins/{username}      撤销管理员
GET    /api/keys                              我的 key：服务商、末 4 位、时间
PUT    /api/keys/{provider}        {key}      保存（验证后）
DELETE /api/keys/{provider}                   删除

WebSocket 新事件：
  ai_chunk   {room_id, generation_id, member_id, text}      全房间
  ai_failed  {room_id, generation_id, member_id}            全房间
  ai_notice  {room_id, code, provider?, message}            只发给触发者
  role_changed      {room_id, member_id, role}              全房间
  ai_request        {room_id, slug, requested_by}           只发给群主和管理员
  ai_request_done   {room_id, slug, approved}               只发给申请人
普通 message 对 AI 消息多带 generation_id（就是它的 client_msg_id）
```

成员列表接口中，每个成员多返回 `role`，AI 成员多返回 `slug` 和 `provider`。退出接口在群主还有其他人类成员时返回 409 `owner_must_transfer`；越权操作返回 403 `forbidden`。

“只发给群主和管理员”的事件：`RoomHub` 的定向字段支持成员列表（`only_member_ids`），C++ 在发布前查出当前的群主和管理员。

### D12 前端

- 设置页：每个服务商一行，显示末 4 位和时间，可以保存和删除；保存时显示“验证中”。
- 邀请框：在原来的用户名邀请旁边加一个 AI 下拉框；没有 key 的 AI 置灰，并提示去设置页填写。普通成员提交后显示“已提交，等待群主或管理员审批”。
- 群主和管理员：房间里显示待审批申请的数量，点开后可以批准或拒绝。
- 成员列表：显示群主和管理员标记；群主可以在成员上操作“转让群主”“设为管理员”“取消管理员”；群主点退出且还有其他人时，提示先转让。
- 输入框：输入 `@` 时弹出房间里 AI 的列表。
- 生成中：按 `generation_id` 显示一条 AI 的临时消息，收到 `ai_chunk` 就追加文本；收到带同一 `generation_id` 的正式 `message` 时替换为正式消息，收到 `ai_failed` 时移除。
- `ai_notice` 显示为只有自己能看到的系统提示。

### D13 配置

```text
.env（新增）
  AI_GRPC_TOKEN               C++ 与 Python 共用
  AI_KEY_ENCRYPTION_KEY       只给 ai 服务
  AI_MYSQL_PASSWORD           ai 账号的密码，给 db-users 和 ai 服务
  AI_CHAIN_P=0.5              接话概率 p
  AI_PROACTIVE_P1=0.5         主动发言概率 p1
  AI_IDLE_SECONDS=1800        冷场时长
  AI_CONTEXT_MESSAGES=30
  AI_GENERATION_TIMEOUT=60
```

测试时把 `AI_IDLE_SECONDS` 设为几秒、概率设为 1 或 0，并打开 `AI_FAKE_PROVIDERS`。

### D14 群主和管理员

- 角色存在 `room_members.role`。一个房间只有一个 `owner`，由应用保证：转让在同一个事务里，先锁房间行，把原群主改成 `member`、把对方改成 `owner`。
- 创建房间时创建者写入 `owner`。
- 退出：在已有的“锁房间行”事务里，先检查是否为群主；是群主且还有其他人类成员时返回 `owner_must_transfer`。
- 管理员退出或被移出时，成员行被删除，角色也就随之消失，不需要额外处理。
- 权限检查集中在 `RoomService::requireRole(memberId, roomId, roles)` 一处。

### D15 AI 服务的 MySQL 账号

一次性的 `db-users` 服务（`mysql:8.4.6` 镜像，root 连接），在 `migrate` 完成后执行一段可重复执行的 SQL：

```text
CREATE USER IF NOT EXISTS 'ai'@'%' IDENTIFIED BY '<AI_MYSQL_PASSWORD>';
ALTER USER 'ai'@'%' IDENTIFIED BY '<AI_MYSQL_PASSWORD>';   -- 换密码时生效
GRANT SELECT, INSERT, UPDATE ON chat.user_api_keys TO 'ai'@'%';
GRANT SELECT ON chat.ai_profiles TO 'ai'@'%';
GRANT SELECT ON chat.messages TO 'ai'@'%';
GRANT SELECT (id, type, display_name) ON chat.members TO 'ai'@'%';
```

- `ai` 服务依赖 `db-users` 的 `service_completed_successfully`。
- 删除 key 由 C++ 用 `chat` 账号执行，所以 `ai` 账号不需要 `DELETE`。
- 以后新增 AI 服务需要的表时，在这段 SQL 里加授权；集成测试会检查 `ai` 账号访问 `users` 表时被拒绝。

### D16 AI 邀请的审批

- 申请表 `ai_member_requests` 以 `(room_id, member_id)` 为主键，重复申请用 `INSERT IGNORE`，天然只有一条。
- 申请、批准、拒绝都在“锁房间行”的事务里完成，和退出、邀请用户串行，避免“批准时 AI 已被别人加进来”这类竞争；第二个审批人会发现申请已不存在。
- 申请时检查申请人有没有 key；批准时不再检查，费用本来就按触发者计算。
- 申请人退出房间后，申请仍然保留，由群主或管理员处理。

## Risks / Trade-offs

- [C++ 进程崩溃会丢失待确认的事件] -> 只影响那一刻正在触发的 AI 回复，人类消息已保存。需要更强的保证时，可以把待确认表放进 Redis。
- [`db-users` 服务需要 root 密码] -> 它是一次性的服务，只在启动时运行几秒；root 密码只传给它和 MySQL，不给 app 和 ai。
- [群主一直不转让、也不再上线] -> 房间仍可正常使用，只是无人能任命管理员或审批 AI；以后可以加“群主长期不活跃时转让”的机制。
- [key 明文经过 C++ 的内存] -> 保存时 C++ 只转发、不保存、不记录；HTTPS 上线前，浏览器到 Nginx 这一段是明文传输。
- [多实例时 Python 的计时器轮询有 5 秒误差] -> 冷场是分钟级的，可以接受。
- [进程中断后自动重新生成可能再次计费] -> 用户已接受该取舍。默认尝试上限 3 次，可配置；同一逻辑回复稳定 generation_id，存储去重。模型调用与服务商计费不承诺恰好一次。
- [服务商 SDK 的接口变化] -> 都隔离在各自的适配器里，靠适配器的单元测试（用录制的流式响应）发现。
- [国内网络访问 OpenAI、Anthropic、Gemini] -> 适配器支持通过配置改 `base_url`，可以接代理或兼容网关。
- [多个 AI 在一人房间里接话，用户的费用不可预期] -> 由概率衰减限制：p = 0.5 时，每条人类消息平均多带出约 0.6 条接话；概率可以配置为 0 来关闭接话。
- [gRPC 依赖增加构建时间] -> 用 apt 包，不从源码编译。

## Migration Plan

1. Flyway 执行 `V3`：新增表并写入 4 个预置 AI，已有数据不受影响。
2. 先部署 AI 服务，再部署 C++；C++ 在 AI 服务不可用时照常运行，只是 AI 不回复。
   注意时序：V3 执行后、新版 C++ 启动前，仍在运行的旧版 C++ 创建的房间没有群主。部署时先停旧版 C++ 再执行迁移；若已出现这类房间，再执行一次 V3 中“为最早加入的人类成员设群主”的语句（限定没有群主的房间）即可，开发环境中已遇到并这样修复过。
3. 用户在设置页填写 key 后，才能邀请 AI。
4. 回滚：停掉 AI 服务、回退 C++ 即可；`V3` 新增的表和成员留着不影响旧版本。注意旧版本的成员列表会显示 AI 成员，但它们不会说话。


### D17 回复恢复与客户端草稿

- active 保存 generation_id、payer、epoch、slot、attempt、owner、lease_until、status。默认租约 10 秒，每三分之一租约续期。每次写结果都校验 owner，失去租约的旧生成不能覆盖新尝试。
- 完整模型结果先持久保存为 final，再向 C++ 投递。C++ 保存成功后回传 MessageSaved，重复 Final 也回传确认。数据库临时失败可重复投递已有结果，永久的房间/成员错误回传 GenerationRejected 终止任务。
- 生成途中进程中断或可恢复服务商错误自动重新生成。尝试总数默认 3，退避基数默认 1 秒。重试时 generation_id 不变，attempt 递增。
- Chunk 保留原 text 增量字段，并新增 attempt、sequence、cumulative 和 snapshot，传递本次尝试累计文本。新尝试从空快照开始，前端只接受较新的快照，迟到的旧片段不会拼入新回复。
- 实时消息、历史首屏、翻页和缺口补齐共用正式消息通知，按 generation_id 清除草稿。断线时清理临时草稿，重连后的累计快照可恢复当前文本。
- 原实现只测试停机期间发送消息。补充收到片段后终止 AI 服务、恢复后自动生成并且历史只有一条正式消息的测试。
