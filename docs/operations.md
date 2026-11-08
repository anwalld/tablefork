# 部署、运维与开发

从 README 移过来的部署细节、故障切换演示、测试、压测和开发环境说明。快速开始见[项目 README](../README.md)。

## 启动与配置细节

需要 Docker。先创建 `.env`，把里面的 `change-me` 都换成自己的密码。`.env` 不进 Git，缺少必填项时 Docker Compose 会拒绝启动。

```bash
cp .env.example .env
```

共享暂存还需要独立的`QUEUE_REDIS_PASSWORD`、`QUEUE_ENCRYPTION_KEY`和`BUFFER_CONTROL_TOKEN`，各自用一次`openssl rand -hex 32`生成，不能复用模型API key或其他密码；已有环境保留原`.env`的值，只补缺少的项。代理和共享桶使用动态分配池之外的固定地址；自定义网段时须同时修改对应地址和动态池，详见[请求暂存与恢复](request-buffering.md)。

```bash
docker compose up -d --build --wait
```

启动后访问 <https://localhost:8443>，用下面的命令确认 MySQL 和 Redis 正常：

```bash
curl --cacert data/certs/ca.crt https://localhost:8443/health
```

证书有两种模式，由 `.env` 的 `CERT_MODE` 选择：默认 `local` 由本地 CA 签发，`PUBLIC_HOSTS` 可以把别人访问用的域名或 IPv4 加进证书（逗号分隔）；`provided` 使用你放在 `data/certs/` 的 `server.crt`（完整证书链）和 `server.key`，例如 Let's Encrypt 的正式证书，脚本只校验配对和有效期，从不生成或覆盖。部署到服务器的步骤见[项目 README](../README.md#和别人一起用)。

`local` 模式下，首次启动由 `certs` 容器生成本地 CA 和服务器证书，保存在不进 Git 和 Docker 构建上下文的 `data/certs/`。再次启动沿用已有证书；服务器证书不足 30 天有效期或 SAN 变化时，只重新签发服务器证书。私钥请勿分享。

macOS 上打开“钥匙串访问”，将 `data/certs/ca.crt` 导入“登录”钥匙串，双击 **CppAiServer Local CA**，展开“信任”，将“使用此证书时”设为“始终信任”。完成系统确认后重新打开浏览器，访问 `https://localhost:8443` 应没有证书警告。其他系统需将同一个 CA 导入其受信任根证书库。命令行用上面的 `--cacert` 即可，无需关闭证书校验。

HTTP 入口 `http://localhost:8080` 仅做 301 跳转，不处理登录或 WebSocket。`HTTP_PORT`、`HTTPS_PORT` 可配置；修改 HTTPS 端口时须同步 `ALLOWED_ORIGINS`。Cookie 默认带 `Secure`、`HttpOnly`，仅在直接通过 HTTP 调试内部应用时才使用 `COOKIE_SECURE=0`。`GATEWAY_CONN_PER_IP` 默认 100，按真实客户端地址限制包括 WebSocket 在内的 TCP 并发连接。

数据库结构来自 `migrations/`，由 [Flyway](https://documentation.red-gate.com/flyway) 管理。每次启动时 `migrate` 服务先执行还没执行过的迁移，记录在 `flyway_schema_history` 表里，完成后应用才会启动。Flyway 之前建的库会自动登记为版本 1。

修改表结构时新增一个文件，按 `V<版本>__<说明>.sql` 命名，例如 `migrations/V18__add_message_reactions.sql`（当前最新为 V17）。不要修改已执行过的文件，Flyway 会校验文件的校验和，不一致时拒绝执行。MySQL 的 DDL 不能回滚，一个迁移中途失败时，`migrate` 会停下并让应用不启动，需要手动修正数据库，再用 `repair` 清掉失败记录后重启。

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

## Redis 自动故障切换

应用和 AI 统一连接 `redis-proxy:6379`（HAProxy），它只转发到回答 `role:master` 的节点。`redis-1`/`redis-2` 是两个数据节点，初次启动由 redis-1 任主；3 个 Sentinel 以 quorum 2 监控，默认 3 秒判定下线，再自动提升从节点。Sentinel 的配置分别保存在具名卷中，重启沿用选举后的状态。

手动演示（以下假定当前主节点为 redis-1，先查询确认）：

```bash
docker compose exec redis-2 redis-cli -h sentinel-1 -p 26379 SENTINEL get-master-addr-by-name chat
docker compose exec redis-2 redis-cli -h redis-proxy INFO replication
docker compose stop redis-1
```

等待数秒，再运行查询。代理应连接 redis-2，应用和 AI 不需重启：

```bash
docker compose exec redis-2 redis-cli -h redis-proxy INFO replication
curl --cacert data/certs/ca.crt https://localhost:8443/health
docker compose start redis-1
docker compose exec redis-1 redis-cli INFO replication
```

原主节点会由 Sentinel 降为从节点，最终显示 `role:slave` 和 `master_link_status:up`。若查询显示 redis-2 已是主节点，演示时对调两个节点，不能再次停止已经是从节点的 redis-1 来证明主故障恢复。

启用业务暂存后，暂停期间请求先返回排队编号，由独立内存Redis共享保存；恢复后每实例按50次/秒、8并发初值处理，客户端查询最终业务结果。入队不等于成功，聊天ACK仍在MySQL提交后。共享桶不可用会明确报可重试错误，丢失的任务结果可能是unknown，不能盲重放。主动切主使用`docker compose exec redis-proxy sh /redis-proxy.sh switch`，缺暂停确认就取消。配置、内存边界、查询凭证和恢复矩阵见[请求暂存与恢复](request-buffering.md)。

Redis 异步复制仍可能丢失最后一小段会话、限流或 AI 状态。聊天消息已保存于 MySQL，历史可补齐。Pub/Sub 在断开期间不保留推送，客户端通过 seq 缺口或重连补齐。此部署是 Sentinel 单主，不支持直接换成 Redis Cluster：受信任设备的 Lua key 设计需要届时单独修改。

共享桶版本升级前补齐独立密钥并备份现有配置、证书和数据。网络IPAM有变化时，需要维护窗口：先准备镜像，停止写入并备份，再用`docker compose down`（不加`-v`）重建网络，最后`docker compose up -d --wait`并核对迁移、数据、许可和业务结果。保留现有MySQL、Redis和Sentinel命名卷，不自动删除旧卷或承诺会话一定失效；固定地址约束见[请求暂存专题](request-buffering.md)。

`SENTINEL_DOWN_AFTER_MS` 控制判定下线的时长。测试脚本会在 AI 故障阶段之后执行 Redis 主节点停止、恢复验证、旧主节点重新加入，并用测试专用复制屏障保存跨阶段夹具；产品不承诺 Redis 写入强一致。

## 运行测试

```bash
scripts/test.sh
```

脚本先在容器内运行单元测试，再启动完整环境并运行 `tests/integration` 下的集成测试。传给脚本的参数会交给 pytest，例如 `scripts/test.sh -k rooms`。

测试会频繁注册、登录和发消息，所以脚本分几步：

1. 放开 Nginx 和消息的限制，把登录计数的有效期缩短到 5 秒，AI 服务用假模型、冷场时长 3 秒，运行全部测试。
2. 先准备一个有 AI 的房间，停掉 AI 服务后发一条消息，确认人和人的聊天照常；再启动 AI 服务，确认这条消息在重连后得到且只得到一次回复。
3. 检查日志里没有出现测试用的 API key 明文。
4. 用每 2 秒 5 条的消息限额测试消息限流、AI 之间接话（接话概率设为 1）和 AI 回复不占用户限额，用默认值测试 Nginx 限流。
5. 按正常配置重启。

队列满的测试需要极小的队列，默认跳过。单独运行时，环境变量要在两条命令中都生效：

```bash
export WORKER_COUNT=1 WORKER_QUEUE_LIMIT=1 SMALL_QUEUE=1
docker compose up -d --wait app-1 app-2
docker compose --profile test run --rm tests pytest -q test_faults.py -k full_queue
unset WORKER_COUNT WORKER_QUEUE_LIMIT SMALL_QUEUE
docker compose up -d --wait app-1 app-2
```

### 清理测试数据

集成测试会在数据库里留下大量测试账号和房间。用下面的命令清理，默认只预演并打印数量：

```bash
docker compose --profile test run --rm tests python cleanup_test_data.py
```

确认数量后加 `--apply` 才真正删除，全部在一个事务里完成：

```bash
docker compose --profile test run --rm tests python cleanup_test_data.py --apply
```

- 默认匹配测试生成的用户名：`u` 加 12 位十六进制，或 `用户u` 加 5 位十六进制。
- 其他前缀用 `--prefix` 加入（可重复），`--exclude` 排除前缀，默认排除 `realtest_`。
- 成员全是待删用户的房间整间删除；共享房间里只删待删用户的消息和成员关系，待删用户是群主时，群主转给最早加入的其他人类成员。

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

## CLion 开发

日常开发在容器内构建，和部署使用同一份依赖。

1. 构建开发镜像：

   ```bash
   docker build --target dev -t cppaiserver-dev .
   ```

2. 打开 Settings > Build, Execution, Deployment > Toolchains，新建 Docker 工具链，Image 选择 `cppaiserver-dev:latest`。
3. 在 Settings > Build, Execution, Deployment > CMake 中把工具链切换为刚建的 Docker 工具链。
4. 用 `docker compose up -d mysql redis-1 redis-2 sentinel-1 sentinel-2 sentinel-3 redis-proxy queue-redis` 启动依赖。运行配置的容器参数加上 `--network cppaiserver_default`，环境变量设置 `MYSQL_PASSWORD` 和 `REDIS_PASSWORD`，取值和 `.env` 相同。

## Drogon 补丁

镜像构建时会给 Drogon 1.9.13 打上 `docker/drogon-mysql-disconnect.patch`。未打补丁时，MySQL 不可达会让应用在 `MysqlConnection::disconnect()` 中段错误退出。

还会打上 `docker/drogon-redis-disconnect.patch`。Drogon 1.9.13 跨线程发送 Redis 命令时，排队的任务只持有连接的裸指针；如果连接在这期间断开，hiredis 会先释放上下文，排队的发送随后访问已释放的内存，表现为 Redis 故障切换窗口里后端偶发 `It is forbidden to run loop on threads other than event-loop thread` 后退出，或 `malloc(): ... corrupted`。补丁让排队任务持有连接的所有权，并让断开后的发送、订阅直接以 `Connection is broken` 失败。
