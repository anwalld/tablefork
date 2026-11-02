# Proposal

## Why

第一阶段的登录和注册没有任何次数限制。每次登录要做一次 scrypt，而 scrypt 只在 2 个线程上运行，所以单个 IP 就能占满登录能力；同一个账号也能被无限次猜密码。已登录用户发消息同样没有按用户的限制。另外，数据库迁移只在 MySQL 数据卷为空时执行，下一阶段一改表结构就会卡住。这些都要在接入 AI 之前解决。

`f431c93` 已经提交了 L7 限流、自写的迁移脚本、客户端测试和 CI。本 change 以它为起点：保留 L7 限流、客户端测试和 CI，把迁移脚本换成 Flyway，并补上应用层的防护。

## What Changes

- **注册密码规则**：最短长度从 8 提高到 10，拒绝常见密码黑名单中的密码，也拒绝与用户名相同的密码。只对新注册生效，已有账号不受影响。**BREAKING**：之前合法的 8 到 9 位密码，注册时会被拒绝。
- **登录失败延迟**：按账号计数，与 IP 无关。连续失败超过免费次数后，下次可尝试的时间按指数增长并有上限，在等待时间内的尝试直接返回 429 `too_many_attempts` 和 `retry_after`，不做 scrypt 计算。不锁号，用户名不存在时行为一致。
- **设备令牌**：登录成功后下发长期有效的 `HttpOnly` Cookie `chat_device`。带有效设备令牌、且令牌属于该用户名的登录，走这台设备自己的失败计数，不受别人对这个账号的攻击影响。
- **L7 按 IP 限流**：保留 `f431c93` 中 Nginx 对 `/api/auth/login` 和 `/api/auth/register` 的 `limit_req`，超出返回 429 `rate_limited`。这个行为补进规格。
- **按用户限制消息频率**：在 Redis 中统计每个用户发送的消息数，超过限额时以 `rate_limited` 错误拒绝，并带上 `retry_after`。不影响其他用户。
- **迁移换成 Flyway**：删除 `scripts/migrate.sh`，`migrate` 服务改用官方 Flyway 镜像，迁移文件按 Flyway 规则命名为 `V1__init.sql` 这类格式。已有数据库自动登记为版本 1，并清理自写脚本留下的 `schema_migrations` 表。
- **保留**：`f431c93` 中的 `web/src/lib/leave.js` 和客户端测试，以及 GitHub Actions CI。
- **前端**：登录页显示 `too_many_attempts` 和 `rate_limited` 的等待时间；注册页说明新的密码规则；发消息被限流时提示稍后再试，并保留待发消息。
- 所有阈值放进 `.env`，并在 `.env.example` 中给出默认值。

不在本次范围内：全站登录失败率触发的变慢或验证码（已决定以后再定）、L4 层和 `limit_conn`（多实例阶段）、二步验证、登录提醒、修改密码。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `user-auth`：注册密码规则变严格；新增登录失败延迟、设备令牌、按 IP 限制认证请求三项要求。
- `realtime-messaging`：新增按用户限制发消息频率的要求。

## Impact

- 后端：`src/services/user_service.cc`（密码规则、失败延迟），新增登录限流服务（基于 Redis），`src/controllers/auth_controller.cc`（设备 Cookie、429 响应），`src/ws/chat_socket.cc`（消息频率检查），`src/common/config` 和 `errors`。
- 新增数据文件：常见密码黑名单，构建时打包进镜像。
- 部署：`docker-compose.yml` 的 `migrate` 服务改用 `flyway/flyway` 镜像；`migrations/` 重命名；删除 `scripts/migrate.sh`；`.env.example` 增加阈值。
- 测试：单元测试覆盖密码规则和退避计算；集成测试覆盖失败延迟、设备令牌豁免、消息限流、Flyway 在空库和已有库上的行为；`scripts/test.sh` 在跑全部测试时放宽这些限额。
- 前端：`AuthView.vue`、`ChatView.vue`、`lib/api.js`。
- Redis 新增几类带 TTL 的 key，不需要持久化，主从切换后丢失只会让计数归零。
