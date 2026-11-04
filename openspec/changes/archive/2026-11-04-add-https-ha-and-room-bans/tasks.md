# Tasks

每组完成后都要能单独跑通 `scripts/test.sh`，再开始下一组。设计细节见 design.md 中对应的 D 编号。

## 1. 本地证书（D3）

- [x] 1.1 新增 `docker/certs.Dockerfile` 和 `scripts/gen-certs.sh`，按 D3 生成 CA 和服务器证书（ECDSA P-256，SAN 为 `localhost`、`gateway`、`127.0.0.1`、`::1`；已有的不覆盖；剩余不足 30 天或 SAN 不一致时只重签服务器证书）。验证：连续运行两次，`ca.crt` 的指纹不变；`openssl verify -CAfile ca.crt server.crt` 通过；`openssl x509 -ext subjectAltName` 显示 4 个 SAN。
- [x] 1.2 在 `docker-compose.yml` 加入一次性的 `certs` 服务（挂载 `./data/certs`）；`.gitignore` 加入 `data/certs/`。验证：`docker compose run --rm certs` 后 `git status` 看不到任何证书文件，`data/common-passwords.txt` 仍被跟踪。

## 2. 接入层：L4 + 两个 L7 + HTTPS（D1、D2、D4、D5、D6、D13）

- [x] 2.1 把 L7 模板 `docker/nginx.conf.template` 改为三个监听：`443 ssl proxy_protocol`（TLS 配置按 D2）、`80 proxy_protocol`（只有 301 到 `https://$host:${HTTPS_PORT}$request_uri`）、`8081`（明文，只有 `/health`）。加入 `set_real_ip_from`（compose 固定的网段）和 `real_ip_header proxy_protocol`；`X-Forwarded-For` 和 `X-Real-IP` 改为直接设成 `$remote_addr`；应用改走 `upstream app_servers`（`zone`、`resolve`、`max_fails`、`keepalive`，`proxy_next_upstream error timeout http_502 http_503`）。验证：`nginx -t` 通过。
- [x] 2.2 新增 L4 的 stream 配置（例如 `docker/gateway.conf.template`），包含 `limit_conn_zone`/`limit_conn ${GATEWAY_CONN_PER_IP}`，`:80` 和 `:443` 各自一个 `hash $remote_addr consistent` 的 upstream（`resolve`、`zone`、`max_fails=1 fail_timeout=5s`），`proxy_protocol on`，`proxy_next_upstream on`。镜像和 L7 一起固定到 `nginx:1.27.5-alpine`。验证：`nginx -t` 通过。
- [x] 2.3 `docker-compose.yml`：用 `gateway`、`web-1`、`web-2` 替换 `nginx`，只有 `gateway` 发布 `${HTTP_PORT:-8080}:80` 和 `${HTTPS_PORT:-8443}:443`；给默认网络固定 `ipam` 网段；L7 只读挂载 `server.crt` 和 `server.key`；健康检查分别用 `8081/health`（L7）和对 `127.0.0.1:443` 的 TCP 检查（L4）。验证：`docker compose up -d --build --wait` 成功，`curl --cacert data/certs/ca.crt https://localhost:8443/health` 返回 200，`curl -I http://localhost:8080/x?y=1` 返回 301，`Location` 为 `https://localhost:8443/x?y=1`。
- [x] 2.4 `.env.example` 和 compose 按 D13 加入 `HTTP_PORT`、`HTTPS_PORT`、`GATEWAY_CONN_PER_IP`，`COOKIE_SECURE` 默认改为 1，`ALLOWED_ORIGINS` 默认改为 `https://localhost:8443`，每个变量都写注释。验证：用一份只按 `.env.example` 填好密码的 `.env` 启动，在浏览器里打开 `https://localhost:8443`，能登录并收发消息。
- [x] 2.5 测试接入：测试容器改为 `BASE_URL=https://gateway`、`WS_URL=wss://gateway/ws`、`ORIGIN=https://localhost:8443`，挂载 `ca.crt`；`conftest.py` 的 `requests` 和 `websockets` 都用 `CA_BUNDLE` 校验证书，不允许关闭校验；`scripts/test.sh` 里的服务名改为新的。验证：现有的全部集成测试在 HTTPS 下通过。
- [x] 2.6 新增 `tests/integration/test_gateway.py`，覆盖 edge-gateway spec 中除连接数限制以外的所有场景，以及 user-auth 修改后的“多个新连接”“伪造转发头”（这两个放在 STRICT 轮次里），具体见 D14。验证：`scripts/test.sh` 通过。
- [x] 2.7 新增 `tests/integration/test_gateway_limits.py`，在第 2 轮用 `GATEWAY_CONN_PER_IP=5` 运行，覆盖“超出上限”和“连接关闭后”；“其他地址不受影响”优先用第二个测试容器验证，做不到就在 README 的测试说明里标为人工验证，并在这里写明原因。验证：`scripts/test.sh` 通过。
- [x] 2.8 README：访问地址改为 HTTPS、导入本地 CA 的步骤、`COOKIE_SECURE=0` 的用途。验证：照着 README 在一台全新的 checkout 上操作，浏览器能打开页面且没有证书警告（导入 CA 之后）。

## 3. Redis：Sentinel + HAProxy（D7、D8、D9）

- [x] 3.1 `redis-master`/`redis-replica` 改名为 `redis-1`/`redis-2`（数据卷同步改名），两个节点都加 `--masterauth`、`--requirepass`、`--replica-announce-ip <服务名>`，`redis-2` 带 `--replicaof redis-1 6379`。验证：`redis-cli -h redis-2 INFO replication` 显示 `master_link_status:up`。
- [x] 3.2 新增 `scripts/sentinel.sh` 和 `sentinel-1..3` 服务（各自一个具名卷存放会被改写的配置），配置按 D7。验证：`redis-cli -p 26379 SENTINEL get-master-addr-by-name chat` 返回 `redis-1`，`SENTINEL replicas chat` 列出 `redis-2`，`SENTINEL sentinels chat` 列出另外两个。
- [x] 3.3 新增 `docker/haproxy.cfg` 和 `redis-proxy` 服务（`haproxy:3.0.5-alpine`），配置按 D7。按 Open Questions 确认 `tcp-check send` 能展开 `${REDIS_PASSWORD}`，不能就用启动脚本生成配置。验证：`redis-cli -h redis-proxy INFO replication` 显示 `role:master`；手动停掉 `redis-1`，10 秒内同一条命令显示另一个节点是主节点。
- [x] 3.4 `app`、`ai`、测试容器的 `REDIS_HOST` 默认改为 `redis-proxy`，依赖改为 `redis-proxy` 健康；清理脚本 `cleanup_test_data.py` 和 `conftest.py` 的默认主机同步修改。检查 `ai/chat_ai` 中的阻塞命令和超时设置（D7），必要时设置 `socket_connect_timeout=2` 和合适的 `socket_timeout`。验证：`scripts/test.sh` 全部通过。
- [x] 3.5 在 `src/services/device_trust.cc` 的 Lua 脚本旁边写明“只在 Sentinel 或单主下可用，换 Cluster 需要改 key 设计”（D8）。验证：注释存在，`openspec` 的 design D8 与之一致。
- [x] 3.6 新增 `tests/integration/test_failover.py`，以及 `scripts/test.sh` 里的 Redis 故障阶段（`prepare` → 停 `redis-1` → `after` → 启 `redis-1` → `rejoined`），覆盖 high-availability spec 的“Redis 主节点故障自动切换”全部场景，具体见 D14。如果 D9 的 Drogon 重新订阅假设不成立，就在 `RoomHub` 和 `SessionWatcher` 中补上重新订阅逻辑，并为它加 C++ 单元测试。验证：`scripts/test.sh` 通过，并且故障阶段的日志显示主节点从 `redis-1` 变成了 `redis-2`。
- [x] 3.7 README：新的 Redis 拓扑、怎样手动演示故障切换、旧数据卷的清理方法。验证：照着 README 操作，能看到切换发生。

- [x] 3.8 HAProxy 改为单一后端条目，由常驻脚本按 Sentinel 多数派切换地址并断开旧连接，消除切换时的双主路由。验证：原主节点重新加入期间没有来自 redis-proxy 的连接；受控切换后 1 秒内旧主节点上的代理连接降为 0；Sentinel 失去多数时路由不变；`scripts/test.sh` 全部通过。

## 4. 聊天服务多实例（D1、D6、D14）

- [x] 4.1 compose 里把 `app` 拆成 `app-1`、`app-2`（YAML 锚点，共享同一镜像），L7 的 upstream 指向两者，`scripts/test.sh` 里的 `app` 改为两个实例。验证：`docker compose up -d --wait` 后两个实例都健康，`docker compose logs app-1 app-2` 都有请求记录。
- [x] 4.2 `conftest.py` 新增直连某个实例的帮助函数（经入口登录后，手动把 cookie 放进 `Cookie` 头，用 HTTP 直连 `app-1:8000`/`app-2:8000`，WebSocket 同理）。验证：一个用它在 `app-1` 和 `app-2` 上分别调用 `/api/auth/me` 的测试通过。
- [x] 4.3 新增 `tests/integration/test_multi_instance.py`，覆盖 high-availability spec 的“聊天服务多实例”全部场景（跨实例移出房间的场景依赖第 5 组，可以先用退出房间验证，第 5 组完成后补上踢人）。验证：`scripts/test.sh` 通过。
- [x] 4.4 在 `scripts/test.sh` 中加入应用实例故障阶段（停 `app-1`，验证新请求和 WebSocket 重连补齐，再启动）和 L7 故障阶段（停 `web-1`，多次新建连接都成功，再启动），覆盖“聊天服务实例故障”和“接入实例故障”。验证：`scripts/test.sh` 通过。
- [x] 4.5 README：新的整体拓扑图（D1）和每个服务的作用。验证：图中的服务名与 `docker compose ps` 一致。

- [x] 4.6 按 design D9a 用定期对账（随机偏移、故障触发）替换按消息查询成员；删除 publish 中的成员查询。验证：`test_room_bans.py` 的通知丢失用例在第一轮和第二轮（周期 30 秒）都在 5 秒内收到 `room_left`；定期对账两个用例通过；AI 片段 `sequence` 递增的回归通过；`scripts/test.sh` 全部通过。

## 5. 房间踢人与封禁（D10、D11、D12）

- [x] 5.1 新增迁移 `migrations/V4__room_bans.sql`（D10）。验证：在已有数据的库上 `docker compose up` 后 Flyway 显示 V4 成功，`SHOW CREATE TABLE room_bans` 与 D10 一致。
- [x] 5.2 `RoomService` 新增 `canManage` 规则、`kick`、`ban`、`unban`、`bans`，事务结构和判断顺序按 D11；邀请和加入时检查 `room_bans`（邀请返回 `409 user_banned`）；确认发消息的成员校验不依赖会过期的缓存。为 `canManage` 加 C++ 单元测试覆盖整个权限矩阵。验证：单元测试通过。
- [x] 5.3 `RoomController` 新增 D11 表中的 4 个路由；`RoomHub::removeMember` 增加 `reason` 参数，经 `member_left` 跨实例传递，写入发给被移出者的 `room_left`；退出房间仍用默认的 `left`。验证：`curl` 手动调用 4 个接口，返回 D11 中的响应。
- [x] 5.4 新增 `tests/integration/test_room_bans.py`，覆盖 chat-rooms 增量 spec 的所有场景，包括被踢者和被封者的连接收到带原因的 `room_left`；并在 `test_multi_instance.py` 中补上经另一实例踢人的场景。验证：`scripts/test.sh` 通过。
- [x] 5.5 `cleanup_test_data.py` 兼顾 `room_bans`（报告中加入 `bans_deleted`），`test_cleanup.py` 加一个带封禁记录的用例。验证：`pytest test_cleanup.py` 通过，预演模式下封禁记录不被删除。
- [x] 5.6 前端按 D12 实现：`api.js` 的 4 个接口、`MembersPanel.vue` 的踢出和封禁按钮（带二次确认）、封禁列表和按用户名封禁、带原因的 `room_left` 提示（房间保留在列表、保留已有消息、禁用输入）、邀请时 `user_banned` 的提示；为按钮显示规则和 `room_left` 分支补前端单元测试。验证：`npm test` 通过；在浏览器里用两个账号走一遍踢人、封禁、解封、重新邀请。
- [x] 5.7 README：踢人与封禁的权限规则和接口。验证：README 中的权限表与 `canManage` 的单元测试一致。

## 6. 整体验证

- [x] 6.1 在一份全新的 checkout 上（删除 `data/certs/` 和所有卷），执行 `scripts/test.sh`，全部阶段通过。验证：退出码为 0，并且日志检查没有发现 API key 的标记。
- [x] 6.2 推送分支后 CI 通过。验证：GitHub Actions 的这次运行为绿色，没有新的弃用警告。

## Workflow follow-up

以下四项是 verify 留下的遗留问题，前三项详见 design 的 Risks，第四项见 verification-report 的 S2，不计入本 change 的任务进度，建议归档后另开一个 change 处理：

- 在 Redis 切换窗口内直接断言登录和发消息：要么在有限时间内成功，要么返回 `retryable` 错误，不能挂起。
- 调查 websockets 13.1 同步客户端在 TLS 1.3 下间歇握手超时的原因，查清后去掉 `conftest.py` 同步夹具的 TLS 1.2 限制。
- 修复 `RoomHub::reconcile` 的“再跑一轮”窄竞态：清除 `reconciling_` 后再检查一次 `reconcileAgain_`。
- 检查 `src/ws/chat_socket.cc` 拒绝 WebSocket 握手（401）时的响应：nginx 报告 Content-Length 与实际长度不符。修正后补一个用失效 cookie 连接 `/ws` 的回归测试（见 verification-report 的 S2）。

- CI 通过后合并 PR，然后执行 `/opsx:archive`，把 `edge-gateway`、`high-availability` 两个新规格和 `chat-rooms`、`user-auth` 的修改同步进主规格。
