# 账号、房间与 AI 参考

从 README 移过来的账号安全、房间管理和 AI 配置细节。协作能力（主持人、主席团、小会、记忆、资料库、工具、桌型）见[多AI协作房间](collaboration.md)。

## 账号与安全

注册时密码至少 10 个字符，不能与用户名相同，也不能是常见密码。常见密码列表在 `data/common-passwords.txt`，取自 [SecLists](https://github.com/danielmiessler/SecLists) 的 `Passwords/Common-Credentials/xato-net-10-million-passwords-100000.txt`（MIT 许可），只保留长度至少 10 的条目并转为小写。

注册时可以设置一个安全问题和答案，忘记密码时在登录页点“忘记密码”，输入用户名取回问题，答对后设置新密码。答案像密码一样只保存慢哈希；比较时忽略首尾空格（含全角空格）且不区分英文大小写。重置成功会结束这个用户在所有设备上的会话。没有设置安全问题的旧账号无法自助找回。答错同样计入该用户名的登录失败次数（见下文），问题查询和重置接口也计入 Nginx 的认证限流。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/auth/password-recovery/question` | `{"username"}` | `200 {"question"}`，用户不存在或未设置时为空字符串 |
| POST | `/api/auth/password-recovery/reset` | `{"username","answer","password","password_confirm"}` | `200 {}`；答错为 `401 invalid_answer`，两次密码不一致为 `400 password_mismatch` |

登录、注册和找回密码由 Nginx 按客户端地址限流（共用一个额度），默认每分钟 10 次，可以突发 10 次，超出返回 429 和 `rate_limited`，并带上 `retry_after` 和 `Retry-After` 头，告诉客户端要等几秒。通过 `.env` 的 `AUTH_RATE`、`AUTH_BURST` 和 `AUTH_RETRY_AFTER` 调整，改 `AUTH_RATE` 时把 `AUTH_RETRY_AFTER` 设为 60 除以每分钟次数。

应用还按用户名延迟登录，与客户端地址无关：每个用户名有 5 次免费尝试，之后要等 1、2、4 秒……最多 60 秒才能再试，期间返回 429 和 `too_many_attempts`，带 `retry_after`，不校验密码。登录成功会清零，15 分钟没有新尝试也会清零，不会锁号。登录成功的浏览器会收到 `chat_device` Cookie（180 天），之后这台浏览器单独计数，别人猜这个用户名不会拖慢它。这些数值都在 `.env` 的 `LOGIN_*` 和 `DEVICE_TTL_DAYS` 里。

每个用户每 10 秒最多发 30 条消息，所有连接和房间合计，超出的消息返回 `rate_limited` 和 `retry_after`，不会保存，之后可以用同一个 `client_msg_id` 重发。数值在 `.env` 的 `MESSAGE_RATE_*` 里。

### 会话与受信任设备

- 会话默认 7 天（`SESSION_TTL_SECONDS`）。登出或会话过期后，已打开页面的 WebSocket 会以关闭码 `4401` 断开，前端回到登录页：登出时立即断开，过期时在 `SESSION_CHECK_SECONDS`（默认 30 秒）内断开。
- 每个用户最多有一台受信任的手机和一台受信任的电脑（按浏览器的 User-Agent 判断，平板算手机）。某类的第一台设备登录后直接受信任；之后另一台同类设备最近 30 天发的消息超过原设备的 1.5 倍时，它在登录或续期时替换原设备。
- 从受信任设备登出会结束这个用户在所有设备上的会话；从其他设备登出只结束当前会话。
- 受信任设备的前端每 6 小时调用一次 `POST /api/auth/refresh`，会话不会过期；其他设备的会话照常 7 天过期。

## 房间踢人与封禁

| 操作者 | 管理员 | 普通成员 | 房间外的用户 |
|---|---|---|---|
| 群主 | 可踢出、封禁 | 可踢出、封禁 | 可封禁 |
| 管理员 | 不可操作 | 可踢出、封禁 | 可封禁 |
| 普通成员 | 不可操作 | 不可操作 | 不可操作 |

不能对自己执行踢出或封禁，群主也不能被其他人移出；离开使用“退出房间”，群主需先转让所有权。AI 使用已有的“移出 AI”，不能走人类成员踢出接口。

被踢出者可再次被邀请，重新加入时是普通成员；旧消息保留。封禁会同时移出成员、取消未接受的邀请，并阻止再次邀请和加入，直到群主或管理员解封。解封不自动恢复成员关系或邀请。所有实例上的连接都会收到带 `kicked` 或 `banned` 原因的 `room_left`，其余在线成员刷新成员列表。

成员面板按权限显示“踢出”“封禁”，两者都需要确认用户名。群主和管理员还能打开封禁列表、按用户名封禁房间外的人或解封。列表保留执行者退出房间前的姓名；只有执行者账号被删除时显示为空。

| 方法 | 路径 | 响应 |
|---|---|---|
| DELETE | `/api/rooms/{id}/members/{username}` | `200 {}` |
| PUT | `/api/rooms/{id}/bans/{username}` | `200 {}`，重复封禁保留原记录 |
| DELETE | `/api/rooms/{id}/bans/{username}` | `200 {}`，解封幂等 |
| GET | `/api/rooms/{id}/bans` | `200 {"bans":[{"username","banned_by","created_at"}]}` |

用户名须进行 URL 编码。封禁列表按时间升序，`created_at` 是 Unix 毫秒时间戳，`banned_by` 是用户名或 null。非成员访问返回 `room_not_found`，权限不足为 `forbidden`，邀请被封禁者返回 `409 user_banned`。

## AI

### 密钥和账号

`.env` 里有三个 AI 相关的密钥，`.env.example` 的注释里写了生成方法：

| 变量 | 用途 | 谁能看到 |
|---|---|---|
| `AI_GRPC_TOKEN` | C++ 和 AI 服务之间 gRPC 的共享令牌 | app、ai |
| `AI_KEY_ENCRYPTION_KEY` | 加密用户 API key 的主密钥（AES-256-GCM），丢失后已保存的 key 全部无法解密 | 只有 ai |
| `AI_MYSQL_PASSWORD` | AI 服务的 MySQL 账号 `ai` | db-users、ai |

`ai` 账号由一次性的 `db-users` 服务创建，只能读写 `user_api_keys` 和 `ai_generations`（不能删除）、读和新增 `room_ai_controls`、读 `ai_profiles`、`ai_model_types`、`room_ai_personas` 和 `messages`，以及读 `members` 的 `id`、`type`、`display_name` 三列。协作能力另外需要：读 `room_council`，读写 `room_summaries`，读和新增 `room_memories`、`user_memories`，读、更新、删除 `knowledge_documents`，读、新增、删除 `knowledge_chunks`。它不能读 `rooms`、`room_members`、`users` 和 `members.user_id`。授权以 `scripts/db-users.sh` 为准。root 密码只给 MySQL 和 `db-users`。

### 填写 API key 和邀请 AI

1. 登录后点左上角的“设置”，按服务商填写 API key。保存前会先向服务商验证，之后只显示末 4 位。
2. 在房间里点“成员”，点“创建私人AI”，选底层模型、起名，可顺便写一份默认人设（支持 Markdown，可导入 .md 文件）。角色是私有的，只有创建者能邀请；邀请时按默认人设预填本房间的人设，之后也只有创建者能修改。群主和管理员邀请自己的角色时直接加入；普通成员提交申请，由群主或管理员审批。邀请人必须有对应服务商的 key。创建者退出、被踢或被封禁时，他的 AI 一起离开房间，聊天记录保留。详见[私人AI角色与房间人设](private-ai-personas.md)。
3. 只有一个人的房间里，AI 回复你的每条消息，冷场时还会主动说话，第 3 条主动发言是告别；多人房间里，用 `@` 选择 AI 成员才会回复，重名时按选中的成员区分。完整规则见 `openspec/specs/ai-replies/spec.md`。

DeepSeek 默认使用 `deepseek-flash`，并在调用时显式关闭思考模式，减少日常聊天的 token 开销。模型与定价以 [DeepSeek 官方价格表](https://api-docs.deepseek.com/quick_start/pricing/) 为准。

四类底层模型的型号和旧版预置 AI 的默认人设在 `ai/agents.toml`（旧版预置 AI 只保留在已有房间里，不能再新邀请），改完运行 `docker compose up -d --build --wait ai` 重新构建并启动 AI 服务。模型名要按各家当时可用的型号填写。国内网络访问 OpenAI、Anthropic、Gemini 可能需要代理，适配器支持修改 `base_url`。

行为参数（都在 `.env`）：`AI_CHAIN_P`（AI 之间接话的起始概率）、`AI_PROACTIVE_P1`（主动发言的概率）、`AI_IDLE_SECONDS`（冷场多久算冷场）、`AI_CONTEXT_MESSAGES`（带多少条历史）、`AI_GENERATION_TIMEOUT`（单次生成超时秒数）。

### AI 上下文与重试

下面这些开关都在 `.env`，默认全部关闭，关闭时行为和以前完全一样。

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `AI_STRIP_SELF_NAME` | `0` | 设为 `1` 时，去掉模型模仿历史格式写在回复开头的“自己名字：”，草稿和正式消息一致 |
| `AI_CONTEXT_BLOCK` | `0` | 大于 0 时，历史窗口的起点按这个条数对齐，每满一块才整体前移，带 `AI_CONTEXT_MESSAGES` 到 `AI_CONTEXT_MESSAGES + 块大小 - 1` 条历史。提示词开头连续多轮不变，DeepSeek、OpenAI、Gemini 的自动前缀缓存能命中；Claude 请求同时加上缓存标记。超出预算时按整块丢弃 |
| `AI_RETRIEVAL_ENABLED` | `0` | 设为 `1` 时，用户触发的普通回复会用消息里的中文词和英文单词，在本房间、历史窗口之前的消息里做全文检索（复用搜索用的 ngram 索引），最多取 `AI_RETRIEVAL_TOP_K` 条、共 `AI_RETRIEVAL_CHARS` 字，作为标明“仅供参考”的一段放在最新一条消息之前。检索占用历史预算；查询超过 1 秒或出错时跳过，照常回复 |
| `AI_CONTEXT_CHARS` | `12000` | 历史的字符预算 |
| `AI_CONTEXT_TOKENS` | `0` | 大于 0 时改用 token 估算（中日韩字符算 1，其他字符算 0.3）作为历史预算，取代字符预算 |
| `AI_CLASSIFIED_RETRY` | `0` | 设为 `1` 时按服务商错误类型处理：限流或过载按服务商建议的等待时间退避（最多 60 秒）；上下文过长时把历史条数和预算减半，只重试一次；内容审核拒绝不重试，直接提示。都计入总尝试次数 |

冷场、告别和 AI 接话不做检索。检索只读当前房间的消息，范围和成员能看到的历史一致。

### 未读、搜索和 AI 控制

房间列表显示未读数和“有人@我”，房间内可按关键词、发送者和时间搜索。群主和管理员可以暂停或恢复房间里的 AI，触发者本人也可以停止自己引发的回复，已输出的部分会保存为带“已停止”标记的消息。接口、并发语义和结构化日志见[聊天阅读、搜索与 AI 控制](chat-controls.md)。

### AI 回复恢复

房间事件的状态和待生成任务原子保存后才确认，C++ 会重发超时未确认的事件。AI 服务中断后，Redis 中的未完成任务在租约到期后自动恢复，重新生成可能再次产生服务商费用。缺 key 或 key 被拒绝时直接提示用户更新。

`AI_GENERATION_MAX_ATTEMPTS` 控制每条回复的总尝试次数（默认 3），`AI_RETRY_BASE_SECONDS` 控制退避基数（默认 1 秒），`AI_WORK_LEASE_SECONDS` 控制任务租约（默认 10 秒，运行中自动续期）。完整结果会重投直到 C++ 确认保存，重复投递按 `generation_id` 去重。

冷场任务开始前会检查用户是否已重新发言。已开始的冷场回复可以完成，后来的用户消息随后正常回复。主动发言用三个成功位记录，只在消息保存成功后置位，三位全为 1 时结束。

浏览器从实时推送和历史查询得到正式消息时都会清除对应草稿。流式片段携带尝试编号和累计文本，重试及重连后能替换旧草稿。`scripts/test.sh` 包含 Python 单元测试与生成中重启 AI 服务的故障测试。

本机运行完整故障测试时应保持 Docker Desktop 的时钟连续。脚本在 macOS 测试进程存活期间用 `caffeinate` 防止空闲休眠；测试准备会等 Sentinel 退出 TILT，实际主节点故障后的恢复仍按 30 秒验收。暂停虚拟机或强制系统休眠仍会影响计时。

### 不用真实 key 在本地试

设置 `AI_FAKE_PROVIDERS=1` 后，所有服务商都换成一个假模型：以 `fake-` 开头的 key 都能通过验证，回复内容是“AI 名字: 最后一条消息”。测试和 CI 都用这种方式，不会调用真实的服务商。

```bash
AI_FAKE_PROVIDERS=1 AI_IDLE_SECONDS=20 docker compose up -d --build --wait
```

这只用于本地试用，公开部署时不要打开。

公开部署时，把 `ALLOWED_ORIGINS` 改成实际域名，并在 HTTPS 下设置 `COOKIE_SECURE=1`。
