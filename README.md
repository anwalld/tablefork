# CppAiServer

多人加多个 AI 的聊天室。当前阶段实现多人聊天室基础，AI 接入在后续变更中完成。设计见 `openspec/changes/add-chatroom-foundation/design.md`。

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

数据库结构来自 `migrations/`。每次启动时 `migrate` 服务先按文件名顺序执行还没执行过的迁移，记录在 `schema_migrations` 表里，完成后应用才会启动。

修改表结构时新增一个文件，例如 `migrations/002_add_ai_members.sql`，不要修改已执行过的文件。MySQL 的 DDL 不能回滚，一个迁移中途失败时，`migrate` 会停下并让应用不启动，需要手动修正数据库后再重启。

清空全部数据：

```bash
docker compose down -v
```

修改已有环境的 `MYSQL_PASSWORD` 或 `MYSQL_ROOT_PASSWORD` 时，只改 `.env` 不够，MySQL 的密码保存在数据卷里，要同时用 `ALTER USER` 修改。

公开部署时，把 `ALLOWED_ORIGINS` 改成实际域名，并在 HTTPS 下设置 `COOKIE_SECURE=1`。

## 运行测试

```bash
scripts/test.sh
```

脚本先在容器内运行单元测试，再启动完整环境并运行 `tests/integration` 下的集成测试。传给脚本的参数会交给 pytest，例如 `scripts/test.sh -k rooms`。

登录和注册由 Nginx 按客户端地址限流，默认每分钟 10 次，可以突发 10 次，超出返回 429 和 `rate_limited`，通过 `.env` 的 `AUTH_RATE` 和 `AUTH_BURST` 调整。测试会频繁注册，所以脚本先放开限制运行全部测试，再恢复默认值单独测试限流。

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

默认值：

- `WORKER_COUNT=16`：测试过的配置里吞吐最高。`MYSQL_CONNECTIONS=24` 比它多 8 个连接，留给 HTTP 接口。
- `WORKER_QUEUE_LIMIT=1024`：单个 worker 约每秒处理 500 条，排满时最多积压约 2 秒。超过后返回可重试错误。
- `HOT_CACHE_SIZE=200`：足够覆盖两页最大分页，这个值按分页大小确定，没有压测。

## 目录

| 路径 | 内容 |
|---|---|
| `src/` | C++ 后端 |
| `web/` | Vue 前端 |
| `migrations/` | 数据库迁移，由 `scripts/migrate.sh` 执行 |
| `docker/` | Nginx 配置和 Drogon 补丁 |
| `tests/` | 单元测试和集成测试 |

## Drogon 补丁

镜像构建时会给 Drogon 1.9.13 打上 `docker/drogon-mysql-disconnect.patch`。未打补丁时，MySQL 不可达会让应用在 `MysqlConnection::disconnect()` 中段错误退出。
