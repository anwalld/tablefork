# Design

## Context

动机见 proposal.md 的 Why，要求见 `specs/user-auth` 和 `specs/realtime-messaging`。

现状中和本设计有关的约束：

- 起点是 `f431c93`。工作区目前回滚了其中 6 个文件，实施第一步是恢复到 `f431c93`。
- 登录流程：`UserService::authenticate` 先查库，再在 2 个 `CryptoLoop` 线程上做 scrypt（N=2^15，r=8）；用户名不存在时也做一次 scrypt，避免通过耗时判断账号是否存在。
- 会话已经依赖 Redis：没有 Redis 就无法登录，每条消息也要读一次 Redis 来校验会话。所以新增的计数放在 Redis，不会带来新的可用性依赖。
- `users.username` 的排序规则是 `utf8mb4_bin`，用户名区分大小写。
- 客户端的 `ChatSocket` 遇到可重试错误会按 `1s × attempts` 重发，最多 4 次。
- Redis 是一主一从、手动切换，Redis 里的计数丢失可以接受。

## Goals / Non-Goals

**Goals:**

- 判断是否需要等待的检查在 scrypt **之前**完成，被拒绝的尝试不消耗 `CryptoLoop`。
- 计数在多实例下天然共享（放在 Redis），不依赖进程内状态。
- 并发请求不能绕过限制：检查和计数必须是一次原子操作。
- 所有阈值可以通过 `.env` 调整。

**Non-Goals:**

- 全站失败率触发的变慢或验证码（以后再定）。
- 按 IP 的应用层限流：按 IP 只在 Nginx L7 做。
- 修改密码、二步验证、登录提醒。

## Decisions

### D1 登录延迟：在校验前计数，成功时清零

每次登录尝试在校验密码**之前**，执行一段 Redis Lua 脚本（原子操作）：

```text
key = login_fail:u:<sha256(用户名)>   （有设备令牌时用 login_fail:d:<sha256(设备令牌)>）
字段 count（已尝试次数）、next_at（下次允许的时间，毫秒）

脚本：
  if now < next_at          -> 返回 拒绝，retry_after = ceil((next_at - now) / 1000)
  count = count + 1
  if count >= FREE          -> next_at = now + min(BASE * 2^(count - FREE), MAX)
  EXPIRE key TTL
  返回 允许
校验成功 -> DEL key
校验失败 -> 不再做额外操作（这次尝试在校验前已经计入）
```

- **为什么在校验前计数，而不是失败后计数**：如果失败后才计数，攻击者并发发出 1000 个请求，它们都会在第一次失败被记录之前通过检查。在校验前计数，并发请求也会逐个累加。代价是成功登录在脚本里也会被计一次，但随后就被清零。
- **为什么用 `next_at`，而不是让服务器 sleep**：sleep 会让连接一直挂着，被拒绝的请求也会占用资源。直接返回 429 和 `retry_after`，客户端自己等待。
- **用户名 key 用 SHA-256**：用户名可能包含任意字符，hash 之后 key 的长度固定。不存在的用户名也用同样的方式计数，行为与存在的用户名一致。
- 用完第 FREE 次就开始等待：默认第 5 次之后等 1 秒，第 6 次之后等 2 秒，依此类推。等待时间表由 C++ 的 `backoffSchedule()` 生成（例如 `1,2,4,8,16,32,60`），作为参数传给脚本，所以退避规则只有一处实现，并有单元测试。
- 时间取 Redis 的 `TIME`，多个实例之间不受各自时钟偏差影响。
- 默认值：`FREE=5`、`BASE=1s`、`MAX=60s`、`TTL=15min`。连续攻击时，每个账号每分钟大约 1 次，一天约 1440 次；有 10 位以上长度和黑名单约束时，这个速率猜不出密码。TTL 是从最后一次尝试起算的，停止尝试 15 分钟后计数恢复。
- 备选方案：滑动窗口计数（ZSET）。拒绝的原因：要存每次的时间戳，指数退避用 `next_at` 一个字段就能表达。

### D2 设备令牌

- 登录成功后，如果请求没有带属于该用户名的有效 `chat_device`，就生成 48 个字符的随机令牌（与会话令牌相同，由 `secureRandomString(48)` 生成），下发 Cookie `chat_device`，属性为 `HttpOnly`、`SameSite=Lax`、`Path=/api/auth`、`Max-Age=180 天`，`Secure` 跟随 `COOKIE_SECURE`。
- Redis 中存 `device:<sha256(令牌)>` -> 用户名，TTL 180 天，每次用它登录成功就续期。与会话一样只存哈希。
- 登录时，如果 `chat_device` 有效且对应的用户名与请求中的用户名相同，就用 `login_fail:d:<hash>` 计数，否则用 `login_fail:u:<hash>`。
- `Path=/api/auth`：只有认证接口能收到这个 Cookie，其他请求不带它。
- 登出不删除设备令牌：它代表“这台设备曾登录成功”，与会话是两回事。
- 为什么存用户名而不是用户 ID：检查发生在查库之前，用用户名就可以直接比较，不需要额外查一次数据库。用户名不可修改。

### D3 密码规则

- 最短 10 个字符（按 UTF-8 字符计），最长仍是 1024 字节。
- 黑名单：打包一个常见密码列表文件，取自 SecLists 的常见密码列表（MIT 许可），启动时加载到 `unordered_set`，比较前统一转小写。只保留长度至少 10 的条目，短的已经被长度规则挡住了。
- 与用户名相同的密码（不区分大小写）也拒绝。
- 错误码 `weak_password`，HTTP 400，错误信息说明具体原因，便于前端提示。
- 只在注册时检查。登录不检查，所以已有账号的旧密码仍然可以使用。
- 下载列表文件需要你的确认，实施时会先问。

### D4 消息频率

- 固定窗口：`msg_rate:<user_id>:<floor(now / WINDOW)>`，用 Lua 原子执行 `INCR` 和首次设置 `EXPIRE`。默认每 10 秒 30 条。超过时返回 `rate_limited`，`retry_after` 是窗口剩余的秒数。
- 检查放在 room worker 的任务里、会话校验之后、`MessageService::save` 之前，与会话校验一样每条消息都执行。
  - 备选方案：在提交 worker 之前检查。拒绝的原因：Redis 连接池有 4 条连接，同一连接上两条消息的计数请求可能乱序完成，进而改变它们进入 worker 的顺序。放在 worker 内，同一房间的消息仍然串行。
  - 代价：被限流的消息也会短暂占用 worker 队列中的一个位置。队列本身有上限，可以接受。
- 被拒绝的消息没有写库，所以客户端用同一个 `client_msg_id` 重发时不会被当作重复消息。
- 固定窗口在边界处最多允许 2 倍的突发，作为简单实现可以接受。

### D5 错误响应带上 `retry_after`

`ApiError` 增加一个可选字段 `retryAfterSeconds`。`errorBody` 在有值时输出 `error.retry_after`，HTTP 响应同时设置 `Retry-After` 头。新增三个错误码：

```text
too_many_attempts   429   可重试   登录延迟
rate_limited        429   可重试   消息限流（Nginx 的 rate_limited 已经是同一个错误码）
weak_password       400   不可重试 注册密码不合规
```

### D6 Redis 不可用时的处理

登录限流和消息限流都在 Redis 出错时返回 `unavailable`（503）。原因：没有 Redis，会话也无法创建或校验，放行也做不下去；而且“限流失效时放行”正好会在攻击时打开缺口。

### D7 迁移换成 Flyway

- `migrate` 服务改用 `flyway/flyway:11.14.1` 官方镜像。配置都通过环境变量传入：

  ```text
  FLYWAY_URL=jdbc:mariadb://mysql:3306/chat?allowPublicKeyRetrieval=true&useSsl=false
  FLYWAY_USER=chat
  FLYWAY_PASSWORD=${MYSQL_PASSWORD}
  FLYWAY_LOCATIONS=filesystem:/flyway/sql
  FLYWAY_BASELINE_ON_MIGRATE=true
  FLYWAY_BASELINE_VERSION=1
  命令：migrate
  ```

- `migrations/001_init.sql` 重命名为 `migrations/V1__init.sql`，新增 `V2__drop_legacy_schema_migrations.sql`（`DROP TABLE IF EXISTS schema_migrations`），用来清理 `f431c93` 的自写脚本可能留下的表。
- 三种数据库的结果：

  ```text
  空库                    -> 执行 V1、V2
  已有库（initdb 建的）     -> 没有 flyway_schema_history，库不为空 -> 登记基线 1 -> 执行 V2
  已有库（跑过 migrate.sh） -> 同上，V2 删除 schema_migrations
  ```

- 应用仍然依赖 `migrate` 的 `service_completed_successfully`。
- 选 Flyway 是你定的。相比自写脚本，它多了校验和（发现已执行的文件被改过）、`repair` 和 `info` 等命令。

### D8 测试中的限额

全部测试会频繁注册、登录和发消息，所以 `scripts/test.sh` 分两轮，最后按正常配置重启：

```text
第一轮  放开 Nginx 限流和消息限流，登录计数有效期缩短到 5 秒
        -> 运行全部测试，包括登录延迟（默认的免费次数和退避）和计数过期
第二轮  消息限额改为每 2 秒 5 条，Nginx 用默认值
        -> 先跑消息限流测试，再跑 Nginx 限流测试（它会用掉认证请求的额度，所以放最后）
最后    按正常配置重启
```

登录延迟的测试在默认的免费次数和退避下也能快速完成，只有计数过期需要缩短有效期。消息限流在默认的每 10 秒 30 条下要等很久，所以用小限额。

### D9 前端

- 登录页：遇到 `too_many_attempts` 或 `rate_limited` 时，显示“请 N 秒后再试”并倒计时，期间禁用提交按钮。Nginx 的 429 也带 `retry_after`（`AUTH_RETRY_AFTER`，默认 6 秒，即每分钟 10 次的间隔）和 `Retry-After` 头；万一缺少等待时间，显示“尝试次数过多，请稍后再试”。
- 注册页：说明“至少 10 个字符，不能是常见密码或与用户名相同”，并显示 `weak_password` 的具体原因。
- `ChatSocket`：收到 `rate_limited` 时，按 `retry_after` 等待后重发，这类重试不计入 `MAX_ATTEMPTS`。界面显示“发送过快，稍后自动重试”。

## Risks / Trade-offs

- [攻击者用同一用户名拖慢别人在新设备上的登录] → 常用设备有设备令牌，不受影响；新设备最多等 60 秒。全站策略以后再定。
- [撞库（每个账号只试 1 次）仍然防不住] → 已知风险，等全站失败率策略再处理；注册时的黑名单可以降低被撞中的可能。
- [Redis 主从切换后计数丢失] → 计数归零只是让攻击者多得 5 次免费尝试，可以接受。
- [Flyway 镜像不自带 MySQL Connector/J] → 已验证：`flyway/flyway:11.14.1` 只带 `mariadb-java-client`，用 `jdbc:mariadb://...?allowPublicKeyRetrieval=true&useSsl=false` 可以连接 MySQL 8.4.6，在空库和已有库上都执行成功。Flyway 会警告 8.4 比它验证过的 8.1 新，目前不影响使用。
- [Flyway 是 JVM 程序，启动多花几秒] → 只在启动时执行一次，可以接受。
- [固定窗口允许边界处的突发] → 有 worker 队列上限兜底。
- [黑名单文件增大镜像] → 过滤后只保留长度 10 以上的条目，体积很小。
- [每条消息多一次 Redis 往返] → 实施后用 `load_test.py` 对比吞吐，如果下降明显，就把会话校验和计数合并到一个 Lua 脚本里。

## Migration Plan

1. 恢复工作区到 `f431c93`。
2. 部署时，`migrate` 服务先运行：已有库登记基线 1，并执行 V2。
3. 应用启动后，老用户照常登录；第一次成功登录时下发设备令牌。
4. 回滚：如果回到 `f431c93`，`flyway_schema_history` 表会被留下，但没有影响；`schema_migrations` 已被删除，自写脚本会重新登记 001 并认为已是最新。
