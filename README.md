# CppAiServer

多人加多个 AI 的聊天室。房间里可以有多名用户和 DeepSeek、GPT、Claude、Gemini 四个预置 AI，AI 的调用费用由用户自己的 API key 承担。行为规格在 `openspec/specs/`，各阶段的设计在 `openspec/changes/archive/`。

```text
浏览器 --HTTP/WS--> Nginx --> C++ 聊天服务（Drogon） --> MySQL（权威数据）
                                   |      ^             --> Redis（会话、热缓存、广播、AI 状态）
                         房间事件 |      | 回复片段、最终回复、提示
                     （gRPC 双向流） v      |
                               Python AI 服务（ai/）--> 各家模型 API
```

- C++ 聊天服务负责账号、房间、消息和推送；AI 的最终回复也由它保存，`seq` 只在这里分配。
- Python AI 服务决定 AI 什么时候说话、由哪个 AI 说，调用模型并流式输出，同时负责 API key 的加解密。它的状态都在 Redis，进程本身无状态。
- 两者通过 `proto/ai.proto` 定义的 gRPC 双向流通信。AI 服务停止时，人和人之间的聊天不受影响，重连后补回期间的事件。

## 一键启动

需要 Docker。先创建 `.env`，把里面的 `change-me` 都换成自己的密码。`.env` 不进 Git，缺少必填项时 Docker Compose 会拒绝启动。

```bash
cp .env.example .env
```

```bash
docker compose up -d --build --wait
```

启动后访问 <http://localhost:8080>，用下面的命令确认 MySQL 和 Redis 正常：

```bash
curl http://localhost:8080/health
```

数据库结构来自 `migrations/`，由 [Flyway](https://documentation.red-gate.com/flyway) 管理。每次启动时 `migrate` 服务先执行还没执行过的迁移，记录在 `flyway_schema_history` 表里，完成后应用才会启动。Flyway 之前建的库会自动登记为版本 1。

修改表结构时新增一个文件，按 `V<版本>__<说明>.sql` 命名，例如 `migrations/V4__add_message_reactions.sql`。不要修改已执行过的文件，Flyway 会校验文件的校验和，不一致时拒绝执行。MySQL 的 DDL 不能回滚，一个迁移中途失败时，`migrate` 会停下并让应用不启动，需要手动修正数据库，再用 `repair` 清掉失败记录后重启。

查看迁移状态，或在修正后清理失败记录：

```bash
docker compose run --rm migrate info
```

```bash
docker compose run --rm migrate repair
```

Flyway 镜像只带 MariaDB 驱动，所以连接地址是 `jdbc:mariadb://`，它可以连接 MySQL 8.4，启动时会提示 8.4 比 Flyway 验证过的版本新，可以忽略。

清空全部数据：

```bash
docker compose down -v
```

修改已有环境的 `MYSQL_PASSWORD` 或 `MYSQL_ROOT_PASSWORD` 时，只改 `.env` 不够，MySQL 的密码保存在数据卷里，要同时用 `ALTER USER` 修改。`AI_MYSQL_PASSWORD` 例外：`db-users` 服务每次启动都会用 root 把 `ai` 账号的密码同步成 `.env` 里的值。

## AI

### 密钥和账号

`.env` 里有三个 AI 相关的密钥，`.env.example` 的注释里写了生成方法：

| 变量 | 用途 | 谁能看到 |
|---|---|---|
| `AI_GRPC_TOKEN` | C++ 和 AI 服务之间 gRPC 的共享令牌 | app、ai |
| `AI_KEY_ENCRYPTION_KEY` | 加密用户 API key 的主密钥（AES-256-GCM），丢失后已保存的 key 全部无法解密 | 只有 ai |
| `AI_MYSQL_PASSWORD` | AI 服务的 MySQL 账号 `ai` | db-users、ai |

`ai` 账号由一次性的 `db-users` 服务创建，只能读写 `user_api_keys`（不能删除）、读 `ai_profiles` 和 `messages`，以及读 `members` 的 `id`、`type`、`display_name` 三列。root 密码只给 MySQL 和 `db-users`。

### 填写 API key 和邀请 AI

1. 登录后点左上角的“设置”，按服务商填写 API key。保存前会先向服务商验证，之后只显示末 4 位。
2. 在房间里点“成员”，从“邀请 AI”里选择。群主和管理员邀请时 AI 直接加入；普通成员提交申请，由群主或管理员审批。邀请人必须有对应服务商的 key。
3. 只有一个人的房间里，AI 回复你的每条消息，冷场时还会主动说话，第 3 条主动发言是告别；多人房间里，用 `@Claude` 这样的方式提及 AI 才会回复。完整规则见 `openspec/specs/ai-replies/spec.md`。

DeepSeek 默认使用 `deepseek-flash`，并在调用时显式关闭思考模式，减少日常聊天的 token 开销。模型与定价以 [DeepSeek 官方价格表](https://api-docs.deepseek.com/quick_start/pricing/) 为准。

预置 AI 的模型名和人设在 `ai/agents.toml`，改完运行 `docker compose up -d --build --wait ai` 重新构建并启动 AI 服务。模型名要按各家当时可用的型号填写。国内网络访问 OpenAI、Anthropic、Gemini 可能需要代理，适配器支持修改 `base_url`。

行为参数（都在 `.env`）：`AI_CHAIN_P`（AI 之间接话的起始概率）、`AI_PROACTIVE_P1`（主动发言的概率）、`AI_IDLE_SECONDS`（冷场多久算冷场）、`AI_CONTEXT_MESSAGES`（带多少条历史）、`AI_GENERATION_TIMEOUT`（单次生成超时秒数）。

### 不用真实 key 在本地试

设置 `AI_FAKE_PROVIDERS=1` 后，所有服务商都换成一个假模型：以 `fake-` 开头的 key 都能通过验证，回复内容是“AI 名字: 最后一条消息”。测试和 CI 都用这种方式，不会调用真实的服务商。

```bash
AI_FAKE_PROVIDERS=1 AI_IDLE_SECONDS=20 docker compose up -d --build --wait
```

这只用于本地试用，公开部署时不要打开。

公开部署时，把 `ALLOWED_ORIGINS` 改成实际域名，并在 HTTPS 下设置 `COOKIE_SECURE=1`。

## 运行测试

```bash
scripts/test.sh
```

脚本先在容器内运行单元测试，再启动完整环境并运行 `tests/integration` 下的集成测试。传给脚本的参数会交给 pytest，例如 `scripts/test.sh -k rooms`。

注册时密码至少 10 个字符，不能与用户名相同，也不能是常见密码。常见密码列表在 `data/common-passwords.txt`，取自 [SecLists](https://github.com/danielmiessler/SecLists) 的 `Passwords/Common-Credentials/xato-net-10-million-passwords-100000.txt`（MIT 许可），只保留长度至少 10 的条目并转为小写。

登录和注册由 Nginx 按客户端地址限流，默认每分钟 10 次，可以突发 10 次，超出返回 429 和 `rate_limited`，并带上 `retry_after` 和 `Retry-After` 头，告诉客户端要等几秒。通过 `.env` 的 `AUTH_RATE`、`AUTH_BURST` 和 `AUTH_RETRY_AFTER` 调整，改 `AUTH_RATE` 时把 `AUTH_RETRY_AFTER` 设为 60 除以每分钟次数。

应用还按用户名延迟登录，与客户端地址无关：每个用户名有 5 次免费尝试，之后要等 1、2、4 秒……最多 60 秒才能再试，期间返回 429 和 `too_many_attempts`，带 `retry_after`，不校验密码。登录成功会清零，15 分钟没有新尝试也会清零，不会锁号。登录成功的浏览器会收到 `chat_device` Cookie（180 天），之后这台浏览器单独计数，别人猜这个用户名不会拖慢它。这些数值都在 `.env` 的 `LOGIN_*` 和 `DEVICE_TTL_DAYS` 里。

每个用户每 10 秒最多发 30 条消息，所有连接和房间合计，超出的消息返回 `rate_limited` 和 `retry_after`，不会保存，之后可以用同一个 `client_msg_id` 重发。数值在 `.env` 的 `MESSAGE_RATE_*` 里。

测试会频繁注册、登录和发消息，所以脚本分几步：

1. 放开 Nginx 和消息的限制，把登录计数的有效期缩短到 5 秒，AI 服务用假模型、冷场时长 3 秒，运行全部测试。
2. 先准备一个有 AI 的房间，停掉 AI 服务后发一条消息，确认人和人的聊天照常；再启动 AI 服务，确认这条消息在重连后得到且只得到一次回复。
3. 检查日志里没有出现测试用的 API key 明文。
4. 用每 2 秒 5 条的消息限额测试消息限流、AI 之间接话（接话概率设为 1）和 AI 回复不占用户限额，用默认值测试 Nginx 限流。
5. 按正常配置重启。

队列满的测试需要极小的队列，默认跳过。单独运行时，环境变量要在两条命令中都生效：

```bash
export WORKER_COUNT=1 WORKER_QUEUE_LIMIT=1 SMALL_QUEUE=1
docker compose up -d --wait app
docker compose --profile test run --rm tests pytest -q test_faults.py -k full_queue
unset WORKER_COUNT WORKER_QUEUE_LIMIT SMALL_QUEUE
docker compose up -d --wait app
```

## CLion 开发

日常开发在容器内构建，和部署使用同一份依赖。

1. 构建开发镜像：

   ```bash
   docker build --target dev -t cppaiserver-dev .
   ```

2. 打开 Settings > Build, Execution, Deployment > Toolchains，新建 Docker 工具链，Image 选择 `cppaiserver-dev:latest`。
3. 在 Settings > Build, Execution, Deployment > CMake 中把工具链切换为刚建的 Docker 工具链。
4. 用 `docker compose up -d mysql redis-master redis-replica` 启动依赖。运行配置的容器参数加上 `--network cppaiserver_default`，环境变量设置 `MYSQL_PASSWORD` 和 `REDIS_PASSWORD`，取值和 `.env` 相同。

## Redis 手动切换

当前是一主一从、手动切换，自动切换留到多实例阶段。主节点故障时，已保存的消息不受影响，但登录校验依赖 Redis，切换完成前无法发送消息，也无法访问需要登录的接口。

1. 确认主节点已经停止，不要让两个节点同时接受写入：

   ```bash
   docker compose stop redis-master
   ```

2. 把从节点提升为主节点：

   ```bash
   docker compose exec redis-replica redis-cli REPLICAOF NO ONE
   ```

3. 让应用连到新的主节点：

   ```bash
   REDIS_HOST=redis-replica docker compose up -d --no-deps --wait app
   ```

4. 确认状态：

   ```bash
   curl http://localhost:8080/health
   ```

切换会让所有 WebSocket 断开，客户端自动重连并按 `seq` 补齐。切换前最后几秒内的会话变更可能丢失：受影响的用户需要重新登录，刚登出的会话可能恢复。

## 压测

```bash
docker compose --profile test run --rm tests python load_test.py <房间数> <每房间发送者> <每人消息数>
```

每个发送者等到确认后才发下一条。以下结果来自 2026-11-02 在 Docker Desktop（arm64，10 核，8 GB）上的测试，测试端和服务端在同一台机器上，只能作为相对参考：

| 场景 | 配置 | 吞吐 | p50 | p99 |
|---|---|---|---|---|
| 1 个房间，5 人发送 | `WORKER_COUNT=16` | 465 条/秒 | 10.2 ms | 20.2 ms |
| 32 个房间，各 1 人发送 | `WORKER_COUNT=2` | 1033 条/秒 | 30.2 ms | 41.8 ms |
| 32 个房间，各 1 人发送 | `WORKER_COUNT=8` | 2196 条/秒 | 13.9 ms | 21.7 ms |
| 32 个房间，各 1 人发送 | `WORKER_COUNT=16` | 2772 到 3372 条/秒 | 9.1 到 11.2 ms | 16.3 到 18.3 ms |

全部场景都没有错误。

加入按用户的消息限流后（每条消息多一次 Redis 调用），2026-11-02 在同一台机器上、放宽限额重测两次，`WORKER_COUNT=16`：

| 场景 | 吞吐 | p50 | p99 |
|---|---|---|---|
| 1 个房间，5 人发送，各 200 条 | 936 到 943 条/秒 | 5.0 ms | 10.4 到 10.7 ms |
| 32 个房间，各 1 人发送，各 200 条 | 4604 到 4611 条/秒 | 6.6 ms | 11.7 到 11.8 ms |

吞吐没有下降，反而比上表高，但两次测试时机器的负载不同，这个差别不能归因于代码，只能说明多出的 Redis 调用没有造成明显下降。压测时需要放宽限额：

```bash
export AUTH_RATE=100000r/m AUTH_BURST=100000 MESSAGE_RATE_LIMIT=100000
docker compose up -d --wait
```

默认值：

- `WORKER_COUNT=16`：测试过的配置里吞吐最高。`MYSQL_CONNECTIONS=24` 比它多 8 个连接，留给 HTTP 接口。
- `WORKER_QUEUE_LIMIT=1024`：单个 worker 约每秒处理 500 条，排满时最多积压约 2 秒。超过后返回可重试错误。
- `HOT_CACHE_SIZE=200`：足够覆盖两页最大分页，这个值按分页大小确定，没有压测。

## 目录

| 路径 | 内容 |
|---|---|
| `src/` | C++ 后端 |
| `web/` | Vue 前端 |
| `ai/` | Python AI 服务：触发规则、模型适配器、key 加密 |
| `proto/` | C++ 和 AI 服务共用的 gRPC 接口 |
| `migrations/` | 数据库迁移，由 Flyway 执行 |
| `docker/` | Nginx 配置和 Drogon 补丁 |
| `scripts/` | 测试脚本、`ai` 账号的授权脚本 |
| `tests/` | 单元测试和集成测试 |

## Drogon 补丁

镜像构建时会给 Drogon 1.9.13 打上 `docker/drogon-mysql-disconnect.patch`。未打补丁时，MySQL 不可达会让应用在 `MysqlConnection::disconnect()` 中段错误退出。


### AI 回复恢复

房间事件的状态和待生成任务原子保存后才确认，C++ 会重发超时未确认的事件。AI 服务中断后，Redis 中的未完成任务在租约到期后自动恢复，重新生成可能再次产生服务商费用。缺 key 或 key 被拒绝时直接提示用户更新。

`AI_GENERATION_MAX_ATTEMPTS` 控制每条回复的总尝试次数（默认 3），`AI_RETRY_BASE_SECONDS` 控制退避基数（默认 1 秒），`AI_WORK_LEASE_SECONDS` 控制任务租约（默认 10 秒，运行中自动续期）。完整结果会重投直到 C++ 确认保存，重复投递按 `generation_id` 去重。

冷场任务开始前会检查用户是否已重新发言。已开始的冷场回复可以完成，后来的用户消息随后正常回复。主动发言用三个成功位记录，只在消息保存成功后置位，三位全为 1 时结束。

浏览器从实时推送和历史查询得到正式消息时都会清除对应草稿。流式片段携带尝试编号和累计文本，重试及重连后能替换旧草稿。`scripts/test.sh` 包含 Python 单元测试与生成中重启 AI 服务的故障测试。
