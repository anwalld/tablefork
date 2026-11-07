# Tasks

## 1. 恢复起点

- [x] 1.1 把工作区里被回滚的 6 个文件恢复到 `f431c93`（`git checkout HEAD -- .` 并删除未跟踪的 `docker/nginx.conf`），验证 `git status` 干净、`web` 的 `npm test` 通过

## 2. 迁移换成 Flyway

- [x] 2.1 选定 `flyway/flyway` 的确切版本，验证它能用 `jdbc:mysql://` 连接 MySQL 8.4.6；不能时改用 `jdbc:mariadb://`，并把结论写进 design.md 的风险项
- [x] 2.2 `migrations/001_init.sql` 重命名为 `V1__init.sql`，新增 `V2__drop_legacy_schema_migrations.sql`；`docker-compose.yml` 的 `migrate` 服务改用 Flyway 镜像和 D7 的环境变量，删除 `scripts/migrate.sh`；验证空库启动后 `flyway info` 显示 V1、V2 都已执行
- [x] 2.3 验证已有库：在当前数据卷（含你的账号）上启动，`flyway info` 显示基线 1、V2 已执行，`schema_migrations` 被删除，原账号仍能登录
- [x] 2.4 `tests/integration/test_schema.py` 增加断言：`flyway_schema_history` 中有 V1、V2 且全部成功，`schema_migrations` 不存在；更新 README 的迁移说明（新增文件的命名规则、不要修改已执行的文件、`docker compose run --rm migrate info` 的用法）

## 3. 错误响应与配置

- [x] 3.1 `ApiError` 增加可选的 `retryAfterSeconds` 和 `tooManyAttempts()`、`rateLimited()`、`weakPassword()`；`errorBody` 输出 `retry_after`，HTTP 响应设置 `Retry-After` 头；单元测试验证这几种错误的 JSON 和状态码
- [x] 3.2 `Config` 增加 `LOGIN_FREE_ATTEMPTS`、`LOGIN_BACKOFF_BASE_SECONDS`、`LOGIN_BACKOFF_MAX_SECONDS`、`LOGIN_FAIL_TTL_SECONDS`、`DEVICE_TTL_DAYS`、`MESSAGE_RATE_LIMIT`、`MESSAGE_RATE_WINDOW_SECONDS`，写入 `.env.example` 和 `docker-compose.yml`；单元测试验证默认值和非法值的处理

## 4. 注册密码规则

- [x] 4.1 征得你同意后下载 SecLists 常见密码列表，过滤出长度 10 以上的条目，存为 `data/common-passwords.txt`，在 README 注明来源和 MIT 许可；验证文件已打包进 runtime 镜像
- [x] 4.2 实现 D3 的规则（最短 10 个字符、黑名单、与用户名相同），返回 `weak_password`；单元测试覆盖边界长度、大小写和黑名单命中
- [x] 4.3 更新集成测试中所有不足 10 位的测试密码；新增集成测试覆盖 spec 的“常见密码”“密码与用户名相同”“已有账号不受影响”三个场景（最后一个直接向数据库写入 8 位密码的哈希）

## 5. 登录失败延迟与设备令牌

- [x] 5.1 实现 D1 的 Lua 脚本和登录限流服务（`check(key)`、`clear(key)`），把退避时间的计算抽成纯函数；单元测试覆盖免费次数、指数增长和上限
- [x] 5.2 在 `AuthController::login` 中、`authenticate` 之前接入检查，成功时清零；Redis 出错时返回 `unavailable`；集成测试覆盖 spec 的“免费次数内”“超过免费次数后过早重试”“等待结束后重试”“用户名不存在”，以及 20 个并发请求只有免费次数个通过检查
- [x] 5.3 实现 D2 的设备令牌（下发、校验、续期，Cookie 属性），按令牌选择计数 key；集成测试覆盖“常用设备不受攻击影响”“设备令牌属于其他用户”“设备上的失败”“无效或过期的设备令牌”，并检查 Cookie 的 `HttpOnly`、`Path` 和 `Max-Age`
- [x] 5.4 用小阈值的集成测试验证“不永久锁定”：TTL 过后计数清零；更新 README 中登录限流的说明

## 6. 消息频率

- [x] 6.1 实现 D4 的固定窗口计数，接入 `handleSend` 的 worker 任务；单元测试覆盖窗口 key 和 `retry_after` 的计算
- [x] 6.2 集成测试覆盖 spec 的五个场景：限额内、超过限额（错误中带 `client_msg_id` 且消息未写库）、多连接共享限额、不影响其他用户、窗口过后用同一 `client_msg_id` 重发成功
- [x] 6.3 `load_test.py` 在放宽限额的情况下重测单房间和 32 房间的吞吐，记录在 README；如果比之前下降超过 10%，按 design 的风险项合并 Redis 调用

## 7. 前端

- [x] 7.1 `lib/api.js` 把 `retry_after` 带到错误对象；`AuthView.vue` 对 `too_many_attempts` 和 `rate_limited` 显示倒计时并禁用提交，注册页显示密码规则和 `weak_password` 原因；vitest 覆盖倒计时的格式和结束
- [x] 7.2 `ChatSocket` 收到 `rate_limited` 时按 `retry_after` 延后重发，不计入 `MAX_ATTEMPTS`；`ChatView.vue` 显示“发送过快，稍后自动重试”；在 `chat-socket.test.js` 中增加对应测试
- [x] 7.3 在浏览器中手动验证：连续输错密码后看到倒计时；换一个浏览器（没有设备令牌）同样受延迟影响，原浏览器输入正确密码可以登录；快速连发消息后看到提示并自动补发成功

## 8. 整体验证

- [x] 8.1 `scripts/test.sh` 按 D8 分两轮运行（宽松限额跑全部测试，默认值跑限流测试），本地全部通过
- [x] 8.2 从 `.env.example` 在空数据卷上全新启动，验证 Flyway、注册、登录、发消息正常
- [x] 8.3 推送到 GitHub 后 CI 通过（需要你先创建远端仓库；没有远端时记为未完成并说明）
