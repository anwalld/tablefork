# Design

## Context

动机见 proposal.md 的 Why，要求见各 specs。这里只记录实现前需要知道的现状：

- **接入**：只有一个 `nginx` 服务（`docker/web.Dockerfile`，`nginx:1.27-alpine`，配置模板 `docker/nginx.conf.template`）。
  - 它监听 80，映射到宿主机 `8080`，既提供前端静态文件，也反向代理 `/api/`、`/ws`、`/health`。
  - `proxy_pass` 用的是变量加 `resolver 127.0.0.11`，这样应用容器换了地址也能找到。
  - 登录和注册按 `$binary_remote_addr` 做 `limit_req`（`AUTH_RATE`、`AUTH_BURST`、`AUTH_RETRY_AFTER`）。
  - 转发头用的是 `$proxy_add_x_forwarded_for`，会把客户端自带的头也拼进去。
- **聊天服务**：只有一个 `app` 服务。跨实例需要的东西已经都放在 Redis 里了：
  - 房间推送全部走 `RoomHub` 的 Redis Pub/Sub，发送者所在的实例也从频道里收消息；
  - 会话结束走 `sessions:ended` 频道，再加上 `SessionWatcher` 的定期 MGET；
  - 移出成员走 `RoomHub::removeMember`，先本地移除，再发布 `member_left`，其他实例收到后各自移除并给被移出者发 `room_left`。

  所以多实例主要是部署和测试上的工作。
- **Redis**：`redis-master` 和 `redis-replica`，没有 Sentinel。
  - C++ 在 `main.cc` 里用 `resolveIpv4(config.redisHost)` 把主机名解析成固定 IP 后调用 `createRedisClient`，因为 Drogon 的 Redis 客户端只接受 IP。
  - AI 服务用 `redis.Redis(host=settings.redis_host, ...)`。
  - 集成测试的 `redis` 夹具直接连 `redis-master`。
- **Drogon 的 Redis 断线行为**（看的是 1.9.x 源码 `RedisClientImpl.cc` 和 `RedisSubscriberImpl.cc`）：
  - 订阅连接断开后清空连接，2 秒后新建订阅连接，连上后 `subscribeAll()` 重新订阅所有频道。
  - 普通连接断开后也会自动重连。
  - 实现时 MUST 在 `v1.9.13` 上用故障测试确认这一点（见 D9）。
- **房间角色**：`room_members.role` 取 `owner`、`admin` 或 `member`（V3）。已有转让群主、任免管理员、退出房间。
  - `RoomService::leave` 在事务里先 `lockRoom`，再删除成员关系；
  - 控制器随后调用 `reportMembersChanged` 和 `RoomHub::removeMember`。

  踢人可以照搬这条路径。
- **前端**：`chat-socket.js` 已按 `location.protocol` 选择 `ws` 或 `wss`，收到 `room_left` 时有处理。成员面板在 `MembersPanel.vue`。
- **测试**：
  - 集成测试的环境变量 `BASE_URL=http://nginx`、`WS_URL=ws://nginx/ws`、`ORIGIN=http://localhost:8080`。
  - `scripts/test.sh` 里多处写死了 `app`、`nginx` 这些服务名，比如 `docker compose logs app ai nginx`、`stop ai`。

## Goals / Non-Goals

**Goals:**
- 一条 `docker compose up -d --wait` 就能启动完整的两层接入、两个应用实例和 Redis Sentinel，不需要在宿主机安装任何工具。
- 每个故障场景（应用实例、L7 实例、Redis 主节点）都有可重复的自动化测试，并在 CI 里跑。
- 踢人和封禁复用现有的退出房间和跨实例移除路径，不新增第二套推送机制。

**Non-Goals:**
- 不让 L7 和 Sentinel 本身具备跨主机的高可用（只有一台机器）。L4 是单点，这次不处理。
- 不做 Redis 写入不丢失的强一致保证（见 Risks）。
- 不做 HSTS 和 HTTP/2，不引入公网证书或 ACME。

## Decisions

### D1. 服务拓扑与命名

```
 host :8080 (HTTP)  :8443 (HTTPS)
        |              |
        v              v
 +-----------------------------------+
 | gateway  (nginx stream, L4)       |  limit_conn per $binary_remote_addr
 |  :80  -> web-*:80   proxy_protocol|  hash $remote_addr consistent
 |  :443 -> web-*:443  proxy_protocol|
 +-----------------------------------+
        |                   |
        v                   v
 +-------------+     +-------------+
 | web-1 (L7)  |     | web-2 (L7)  |  :80 proxy_protocol -> 301 https
 |             |     |             |  :443 ssl proxy_protocol
 |             |     |             |  :8081 plain, health only (not published)
 +-------------+     +-------------+
        |    upstream app_servers (resolve)
        v                   v
 +-------------+     +-------------+
 | app-1:8000  |     | app-2:8000  |
 +-------------+     +-------------+
        |                   |             +------+
        +---------+---------+-------------|  ai  |
                  |                       +------+
                  v                           |
        +--------------------+  <-------------+
        | redis-proxy :6379  |  (HAProxy, master only)
        +--------------------+
             |          |
             v          v
        +---------+ +---------+    sentinel-1..3 watch both,
        | redis-1 | | redis-2 |    quorum 2
        +---------+ +---------+
```

- `nginx` 服务拆成 `gateway`（L4）和 `web-1`、`web-2`（L7）。`gateway` 只在宿主机发布 `${HTTP_PORT:-8080}:80` 和 `${HTTPS_PORT:-8443}:443` 两个端口，其余服务都不发布端口。
- `app` 拆成 `app-1` 和 `app-2`，用 YAML 锚点（`x-app: &app`）共享同一份定义，只有服务名不同。
  - 两个服务都用同一个构建结果（`image: cppaiserver-app` 加 `build`），避免重复构建。
  - 选择显式的两个服务而不是 `deploy.replicas: 2`：跨实例测试需要用稳定的名字分别连到某一个实例，故障测试也需要 `docker compose stop app-1`。
- `redis-master` 和 `redis-replica` 改名为 `redis-1` 和 `redis-2`。切换后谁是主节点会变，名字里再写“master”就有误导性。
  - 启动时 `redis-1` 是主节点，`redis-2` 作为它的从节点。
  - 数据卷改名为 `redis-1-data` 和 `redis-2-data`。
- 依赖顺序：
  - `app-*` 依赖 `migrate` 完成和 `redis-proxy` 健康；
  - `web-*` 依赖 `certs` 完成和任一 `app-*` 健康；
  - `gateway` 依赖 `web-*` 健康。
- 前端镜像仍由 `docker/web.Dockerfile` 构建，L7 用它。`gateway` 另用一个只含 stream 配置的镜像，或者用官方镜像挂载配置。

### D2. TLS 在 L7 结束，L4 只转发 TCP

- 在 L4 结束 TLS，L7 拿到的就是明文 HTTP，两层就退化成“TLS 代理加 HTTP 代理”。而且 L4 看不到 HTTP，没法做 301。
- 在 L7 结束 TLS 是常规做法：L4 只负责连接层面的限制和分发，L7 负责 HTTP 层面的一切。
- L7 的 TLS 配置：
  - `ssl_protocols TLSv1.2 TLSv1.3`，`ssl_ciphers` 用 Mozilla intermediate 的列表，`ssl_prefer_server_ciphers off`；
  - `ssl_session_cache shared:SSL:10m`，`ssl_session_timeout 1d`；
  - 证书和私钥从 `data/certs/` 只读挂载。
- HTTP 跳转：
  - `gateway` 的 80 端口同样用 PROXY protocol 转给 L7 的 80 端口；
  - L7 的 80 端口只有一条 `return 301 https://$host:${HTTPS_PORT}$request_uri;`。`$host` 不带端口；`HTTPS_PORT` 由模板替换，默认 8443。
  - 这样 HTTP 请求在 L7 就返回了，不会被转给应用（满足 spec 的“HTTP 跳转到 HTTPS”）。
  - 明文 WebSocket 握手同样收到 301，浏览器不会跟随 WebSocket 的重定向，所以连接建立不了。
- 不开 HSTS：对 `localhost` 开启后，浏览器会长期记住，影响本机其他项目。不开 HTTP/2：Nginx 上的 WebSocket 仍走 HTTP/1.1，开启 HTTP/2 没有收益，反而多一个变量。

### D3. 本地证书：一次性的 `certs` 容器

- 新增 `docker/certs.Dockerfile`（`FROM alpine:3.20`，`apk add --no-cache openssl`）和 `scripts/gen-certs.sh`。在 compose 里的服务名叫 `certs`，`restart: "no"`，把 `./data/certs` 挂载到 `/certs`。
- 生成规则：
  - CA：`ca.key`（ECDSA P-256）和 `ca.crt`，有效期 10 年，`CN=CppAiServer Local CA`，带 `basicConstraints=critical,CA:TRUE` 和 `keyUsage=critical,keyCertSign,cRLSign`。已存在就不动。
  - 服务器证书：`server.key`（ECDSA P-256）和 `server.crt`，由 CA 签发，有效期 825 天（Apple 对私有 CA 签发的 TLS 证书的上限）。
    - SAN 包含 `DNS:localhost`、`DNS:gateway`、`IP:127.0.0.1`、`IP:::1`。其中 `gateway` 用于测试容器在 Docker 网络里访问。
    - 带 `extendedKeyUsage=serverAuth`。
  - 服务器证书不存在、剩余有效期不足 30 天（`openssl x509 -checkend 2592000` 失败），或者 SAN 和脚本里的期望不一致时，重新生成服务器证书，CA 不变。
- 权限：私钥 `chmod 600`。`server.key` 需要让 Nginx 的 master 进程（root）读取，所以属主保持 root。`ca.key` 只在 `certs` 容器里使用，L7 只挂载 `server.crt` 和 `server.key`，测试容器只挂载 `ca.crt`。
- `.gitignore` 增加 `data/certs/`。现在 `data/` 里只有被跟踪的 `common-passwords.txt`，所以不能整个忽略 `data/`。
- 备选方案 mkcert 需要在宿主机安装，而且 CI 里也要装，所以没有采用（用户已选择容器自签）。README 说明怎样把 `data/certs/ca.crt` 导入 macOS 钥匙串，并设为“始终信任”；不导入的话，浏览器会弹一次警告。

### D4. 真实客户端地址：PROXY protocol 加 `real_ip`

- `gateway`：stream 里 `proxy_protocol on;`，把原始的 `$remote_addr` 交给 L7。
- L7：
  - `listen 443 ssl proxy_protocol;`、`listen 80 proxy_protocol;`；
  - `set_real_ip_from` 设为 Docker 网络的网段（compose 里给默认网络固定一个 `ipam` 网段，例如 `172.28.0.0/16`，模板里引用同一个值）；
  - `real_ip_header proxy_protocol;`。

  之后 `$remote_addr` 和 `$binary_remote_addr` 都是浏览器的地址，`limit_req` 无需改动。
- L7 转给应用时，用 `proxy_set_header X-Forwarded-For $remote_addr;` 和 `X-Real-IP $remote_addr;` 直接覆盖，不再用 `$proxy_add_x_forwarded_for` 拼接客户端自带的值，这样伪造的转发头就不会进入应用（满足 spec 的“伪造转发头”）。应用目前不读这些头，但以后的日志或审计会用到。
- 健康检查端口 `8081` 不开 PROXY protocol，只监听在容器内，供 compose 的 healthcheck 用 `wget http://127.0.0.1:8081/health` 访问。它转发到 upstream 的 `/health`，但不对外发布。

### D5. L4 分发与连接数限制

- `upstream web_https { hash $remote_addr consistent; server web-1:443 ...; server web-2:443 ...; }`，HTTP 也一样。
  - `limit_req` 的计数在每个 L7 实例的共享内存里，如果按轮询分发，同一 IP 的限额就会变成两倍。一致性哈希让同一个 IP 固定落到同一个 L7（满足 user-auth 修改后的“多个新连接”场景）。
  - 一个 L7 停止时，`max_fails=1 fail_timeout=5s` 加上 stream 的 `proxy_next_upstream on`，会把这个 IP 的新连接交给另一个 L7。这期间这个 IP 的计数从零开始，这一点可以接受。
- stream 的 upstream 只在启动时解析主机名。`web-*` 重建后地址可能变，所以 `gateway` 用 `resolver 127.0.0.11` 加 upstream 的 `server ... resolve`（开源版 Nginx 1.27.3 起支持，需要 upstream 里有 `zone`）。镜像固定到 `nginx:1.27.5-alpine`，L7 也用同一版本。
- 连接数限制：`limit_conn_zone $binary_remote_addr zone=perip:10m;`，`limit_conn perip ${GATEWAY_CONN_PER_IP};`，默认 `100`，可在 `.env` 里配置。超出时 stream 模块直接关闭新连接，已有连接不受影响。
  - 按 TCP 连接计数，所以也覆盖 WebSocket 长连接。
  - 100 的依据：一个浏览器对同一个源通常最多 6 个 HTTP/1.1 连接，加上几个标签页的 WebSocket，正常用户远到不了 100；同一 NAT 后面有多个用户时也留有余量。
- 在 macOS 的 Docker Desktop 上，从宿主机访问时，看到的客户端地址可能是 Docker 的网关地址，而不是 `127.0.0.1`。本机只有一个用户，所以没有影响。测试在 Docker 网络内的测试容器里运行，地址是真实的。

### D6. L7 到应用：upstream 加 `resolve`

```
upstream app_servers {
    zone app_servers 64k;
    server app-1:8000 resolve max_fails=1 fail_timeout=5s;
    server app-2:8000 resolve max_fails=1 fail_timeout=5s;
    keepalive 32;
}
```

- 原来的“变量加 resolver”写法只能指向一个主机名。换成 upstream 后，用 `resolve`（同样需要 1.27.3+ 和 `resolver`）保留“容器重建后自动重新解析”的能力。
- `proxy_next_upstream error timeout http_502 http_503;`：一个实例停止时，请求自动重试到另一个。
  - 非幂等的请求（POST）默认不会被重试，除非还没发出去（`error` 指连接阶段的错误）。这正好避免了重复发消息，HTTP 发消息本来也只有登录这一类。
- WebSocket（`/ws`）使用指向同一组应用实例、但不设 `keepalive` 的独立 `app_websockets` upstream，默认轮询分发，不需要会话保持：所有状态都在 Redis 和 MySQL 里，任一实例都能接管。
  - 实现中验证发现：复用普通 HTTP 的上游连接会使 Drogon 的 WebSocket 握手返回 404；已核对 v1.9.13 的 `lib/src/HttpServer.cc`，协议升级仅在 `requestParser->firstReq()` 时检查。因此普通 HTTP 仍保留连接池，WebSocket 升级另建连接。两个 upstream 都使用 `zone`、`resolve` 和相同的故障转移参数。
  - 实例停止后连接断开，前端已有的重连逻辑会连到另一个实例，然后按 seq 补齐。
- 前端静态文件仍由 L7 自己提供。

### D7. Redis 高可用：3 个 Sentinel 加 HAProxy

- **Sentinel**：
  - `sentinel-1..3` 都用 `redis:7.4.5` 镜像，命令是 `redis-sentinel /data/sentinel.conf`。
  - Sentinel 运行时会改写自己的配置文件，所以由 `scripts/sentinel.sh` 在启动时把模板写到可写的 `/data/sentinel.conf`。用具名卷保存，重启后沿用改写过的配置；第一次启动时才写入模板。
  - 关键配置：
    - `sentinel resolve-hostnames yes`
    - `sentinel announce-hostnames yes`
    - `sentinel monitor chat redis-1 6379 2`
    - `sentinel auth-pass chat ${REDIS_PASSWORD}`
    - `sentinel down-after-milliseconds chat ${SENTINEL_DOWN_AFTER_MS:-3000}`
    - `sentinel failover-timeout chat 10000`
    - `sentinel parallel-syncs chat 1`
    - `requirepass ${REDIS_PASSWORD}`（Sentinel 自己也要密码）
  - 默认 3 秒判定下线，加上选举和提升，通常在 10 秒内完成切换，满足 spec 的 30 秒。
  - 两个 Redis 节点都配置 `--masterauth` 和 `--requirepass`，这样任一个被降级后都能连上新的主节点。`redis-2` 启动时带 `--replicaof redis-1 6379`；`redis-1` 不带，之后由 Sentinel 改写它的角色。
  - 从节点向主节点报告的是自己的 IP，Sentinel 再从主节点那里得知从节点地址。所以 `redis-1` 和 `redis-2` 都带 `--replica-announce-ip <自己的服务名>`，让 Sentinel 记录的是主机名，容器重建后仍然有效。
- **HAProxy**（`redis-proxy`，`haproxy:3.0.5-alpine`）：后端只保留一个 `master` 条目，Redis 仍为两个节点和三个 Sentinel。2026-11-04 测试观察到旧方案有约 11 秒两个节点都自称 master、代理同时转发的窗口；不再用两个条目分别检查，因为独立检查也不能同时切换 UP/DOWN 状态。

```text
global
    log stdout format raw local0
    maxconn 4096
    stats socket ipv4@127.0.0.1:9998 level admin
    stats socket ipv4@0.0.0.0:9999 level user

defaults
    log global
    mode tcp
    option tcplog
    timeout connect 2s
    timeout client 0
    timeout server 0
    timeout check 2s
    default-server init-addr last,libc,none resolvers docker check inter 1s fall 1 rise 1 on-marked-down shutdown-sessions

resolvers docker
    nameserver dns 127.0.0.11:53
    resolve_retries 3
    timeout resolve 1s
    timeout retry 1s
    hold valid 5s

frontend redis
    bind :6379
    default_backend redis_master

backend redis_master
    option tcp-check
    tcp-check send-binary "${REDIS_AUTH_HEX}"
    tcp-check expect string +OK
    tcp-check send "PING\r\n"
    tcp-check expect string +PONG
    tcp-check send "INFO replication\r\n"
    tcp-check expect string role:master
    tcp-check send "QUIT\r\n"
    tcp-check expect string +OK
    server master "${REDIS_MASTER_HOST}:6379"
```

  - 启动脚本先通过 RESP AUTH 和 `SENTINEL get-master-addr-by-name chat` 查询三个 Sentinel，至少两个回答同一主机名才导出 `REDIS_MASTER_HOST` 并启动。启动时没有多数就每秒等待，健康检查不通过，下游不会启动。
  - `scripts/redis-master-watch.sh` 作为后台子进程，继承 `REDIS_PASSWORD`，顺序用 `nc -w 1` 查询三个 Sentinel，每轮后等待 0.5 秒。至少两个回答同一主机名、且不同于当前路由时，先执行 `set server redis_master/master fqdn <新主>`，确认 HAProxy 接受 FQDN 后再 `shutdown sessions server redis_master/master`，记录一次无密码的切换日志。只用轮询，不订阅 `+switch-master`。
  - 查询不到多数时保持当前路由，不主动切走。当前路由节点失效或不再是主节点时，AUTH→PING→INFO role:master→QUIT 的健康检查使该条目 DOWN；检查只判断目标是否可用，不决定选哪个节点。
  - 原节点被 Sentinel 选为从节点时不会因为自己暂时仍报告 master 而获得路由；单一条目保证新连接在任一时刻只有一个去处。旧主重启到被降级的过程中，代理继续指向 Sentinel 多数派认定的当前主节点。
  - 改地址与关闭已有连接是两条命令，中间短暂窗口内旧连接仍可能写入旧节点；受控切换时这些写入可能在后续同步中丢失。接受与异步复制相同类型的短暂数据丢失风险，不使用 `min-replicas-to-write`：仅一个从节点时，从节点故障不应导致主节点拒绝所有写入。
  - `127.0.0.1:9998` 为容器回环 admin 接口，供 watcher 改路由；`0.0.0.0:9999` 为 Docker 内网只读接口，供测试读取 `show servers state`。两个端口均不发布到宿主机。
  - watcher PID 写入 `/tmp/redis-master-watch.pid`；容器健康检查同时要求 PID 存活和代理 PING 成功。用户追加确认：watcher 异常退出时终止 HAProxy 主进程，由 `restart: unless-stopped` 重启容器；不能依赖 unhealthy 状态本身触发重启。
  - 密码仍仅经环境变量传入；AUTH 用 RESP 字节后转 `REDIS_AUTH_HEX` 给 tcp-check，不写入配置、临时密码文件或命令行参数。健康与 watcher 子进程均不输出密码。
  - `redis-proxy` 是单容器单点；不增加第二个代理，避免两个代理路由切换时刻不同，重新形成分别指向旧主和新主的窗口。故障时靠容器自动重启恢复。
- **C++ 和 AI 服务都只连 `redis-proxy`**（用户的决定）：
  - `.env.example` 和 compose 里 `REDIS_HOST` 的默认值改为 `redis-proxy`。
  - `resolveIpv4("redis-proxy")` 得到的是 HAProxy 容器的地址，它在整个运行期间不变，所以 `main.cc` 不用改。
  - 只有一个入口，判断主节点的逻辑也只有一份。
  - 备选方案是 C++ 自己订阅 Sentinel 的 `+switch-master` 并重建客户端。这需要把 9 个文件里 22 处 `getRedisClient()`/`newSubscriber()` 收拢成一个可以替换的封装，还要自己处理订阅迁移，所以没有采用。
- **AI 服务**：`redis.Redis` 每次命令失败后会在下一次命令时自动重连，所以不用改连接方式，只要改 `REDIS_HOST`。
  - 实现时检查 `ai/chat_ai` 里有没有阻塞命令（如 `BLPOP`、`XREAD BLOCK`）或长时间持有的连接。如果有，要确认断开后会抛出可以被现有重试逻辑捕获的异常，而不是挂住。
  - `socket_timeout` 需要比阻塞时长大一些；`socket_connect_timeout` 设为 2 秒，与 C++ 的 2 秒超时一致。
- **集成测试**的 `redis` 夹具和清理脚本的默认 `REDIS_HOST` 改为 `redis-proxy`。
  - 测试里用到的 `ACL SETUSER default -mget` 只作用于当前的主节点，ACL 不会复制到从节点。这些测试在故障切换之前的轮次里运行，用完立即恢复，所以没有问题。

### D8. 受信任设备的 Lua 脚本：保持不变

Sentinel 下只有一个主节点，`DeviceTrust` 的 `kEvaluate` 在脚本里拼 key，照常可以运行。改成兼容 Cluster（所有 key 用 `{user_id}` hash tag，`device_sends:` 也改成带 user 的形式）只在将来换成 Redis Cluster 时才需要，届时作为独立的 change 处理。在 `device_trust.cc` 那段脚本旁边的注释里写明这个前提。

### D9. Redis 切换期间聊天服务的行为

- 切换期间：
  - HAProxy 断开旧连接，或者在新主节点出现前拒绝连接；
  - Drogon 的普通命令在 2 秒超时内失败，现有代码把 Redis 失败当成可重试错误（发消息返回 `retryable`；`SessionWatcher` 跳过这一轮）；
  - 订阅连接断开后约 2 秒重连，并重新订阅。
- **订阅断开期间发布的消息会丢失**。Redis Pub/Sub 不保留消息。受影响的只是实时推送：
  - 消息已经先写入 MySQL，所以 spec 的“已确认的消息”不受影响；
  - 客户端在收到下一条消息时，会通过 seq 不连续发现缺口，并按“客户端补齐”拉取。
  - 不额外实现“重连后通知客户端补齐”：Drogon 没有暴露订阅重连的回调，而上面的补齐已经覆盖了大部分情况。在 Risks 里记录。
- 会话结束事件在订阅断开期间也会丢失。由 `SessionWatcher` 的定期检查在 `SESSION_CHECK_SECONDS` 内兜底。spec 要求的是“切换完成后”的登出，那时订阅已经恢复，走正常路径。
- 必须用故障测试确认上面关于 Drogon 的假设：
  - 停掉 `redis-1`，等切换完成后，在两个实例上订阅同一个房间，然后从一个实例发消息，另一个实例要收到；
  - 再登出，另一实例上的连接要在 2 秒内收到 4401。

  这能说明普通连接和订阅连接都重连成功、订阅也恢复了。如果 `v1.9.13` 的行为与读到的源码不符，就在 `RoomHub` 和 `SessionWatcher` 里补一个小的重新订阅逻辑（用 Redis 失败作为信号，重建 `newSubscriber()` 并重新订阅已有的房间频道），作为这个 change 的一部分。

### D9a. 移除通知丢失时的兜底：定期对账

- 问题：Redis 故障导致 `member_left` 丢失时，其他实例上被移出者的订阅会残留，Redis 恢复后会继续收到房间的新消息。
- 正常情况下仍由 `member_left` 即时撤权。另外，每个实例定期用 MySQL 核对本实例的所有“房间—成员”订阅：快照去重，每 500 对一批，依次参数化查询 `room_members`；失去成员关系的再查 `room_bans` 判断原因（`banned`，否则 `kicked`；对账分不出被踢和自己退出，统一按被踢处理），然后只在应用循环本地移除并发送 `room_left`，不再发布移除事件。
- 周期沿用 `SESSION_CHECK_SECONDS`，与会话检查在同一个定时器里顺序执行。每个实例启动时在 `[0, 周期)` 内均匀随机延迟首次执行，尽量错开各实例的对账时刻。有重入保护，上一轮未结束时只记一次“再跑一轮”，以循环完成。
- 故障触发：实例在发布或会话检查遇到 Redis 错误时记“脏”标记，此后每秒尝试 `PUBLISH rooms:reconcile 1`；成功即清除标记，所有实例收到广播后立即对账。订阅回调先 `queueInLoop` 再执行对账，避免 Drogon 的频道锁重入。用 PUBLISH 是否成功判断恢复，因为 PING 成功不代表能发布。
- 曾经的方案是在每次推送前查询成员名单。它会让并发发布的 AI 片段乱序，开销也随消息量增长，因此弃用。AI 片段为带 sequence 的累积快照，乱序会丢掉中间快照、让显示跳动，不会造成文字拼接错乱。现在 `publish` 直接发布，不再按消息查询成员或注入收件人名单；原有私人通知目标机制保留。
- 代价：故障触发没能覆盖到的情况（例如只有某个实例的订阅连接被单独断开，而普通命令正常），撤权最多延迟约一个周期（默认 30 秒）。取快照后被移出又立即重新加入的用户，可能被误移除一次，刷新即可恢复。对账数据库异常时记录警告并放弃本轮，等待下一次。

### D10. 封禁的数据模型

迁移 `migrations/V4__room_bans.sql`：

```sql
CREATE TABLE room_bans (
    room_id    BIGINT UNSIGNED NOT NULL,
    user_id    BIGINT UNSIGNED NOT NULL,
    banned_by  BIGINT UNSIGNED NULL,          -- members.id of whoever banned
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (room_id, user_id),
    KEY idx_room_bans_user (user_id),
    CONSTRAINT fk_room_bans_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE,
    CONSTRAINT fk_room_bans_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_room_bans_by   FOREIGN KEY (banned_by) REFERENCES members (id) ON DELETE SET NULL
);
```

- 封禁按 `user_id` 记录，而不是 `member_id`：对象可能不是成员（被邀请者或与房间无关的用户），而且用户被踢出后再加入时，成员关系是重建的。
- `banned_by` 记录的是执行者的 `members.id`。执行者账号被删除时置空，封禁本身保留（满足 spec 的“执行者已离开”）。执行者只是退出房间时，`members` 行还在，列表里仍显示他的用户名。
- 房间删除时级联删除封禁记录。清理脚本删除用户时由外键级联，删除 `members` 时 `banned_by` 置空。不过清理脚本里显式的删除顺序要确认不会因为 `banned_by` 的外键报错，并在报告里加一项 `bans_deleted`。

### D11. 踢人与封禁的接口和流程

| 方法 | 路径 | 说明 | 成功响应 |
|---|---|---|---|
| `DELETE` | `/api/rooms/{id}/members/{username}` | 踢出成员 | `200 {}` |
| `PUT` | `/api/rooms/{id}/bans/{username}` | 封禁，幂等 | `200 {}` |
| `DELETE` | `/api/rooms/{id}/bans/{username}` | 解封，幂等 | `200 {}` |
| `GET` | `/api/rooms/{id}/bans` | 封禁列表 | `200 {"bans":[{"username","banned_by","created_at"}]}`，按 `created_at` 升序；`banned_by` 是用户名，可能为 `null` |

用户名按现有的 `appointAdmin` 那样从路径里取，并做 URL 解码。

- **权限判断**集中在 `RoomService` 的一个函数里：

```
canManage(actorRole, targetRole):
    owner  -> target in {admin, member, none}
    admin  -> target in {member, none}
    member -> never
```

  - `targetRole` 为 `none` 表示对象不是成员。踢人要求对象是成员，所以 `none` 对踢人返回参数错误。
  - 判断顺序：
    1. 操作者不是成员时返回 `room_not_found`；
    2. 对象用户不存在时返回 `user_not_found`；
    3. 对象是自己时返回 `invalid_argument`（沿用现有的参数错误码）；
    4. 对象是群主，或权限不足时返回 `forbidden`；
    5. 踢人时对象不是成员，返回 `invalid_argument`。路径里按用户名查找，只会找到人类；AI 没有用户名，所以“对象是 AI”也落在这一步，返回参数错误。
- **踢出**：照搬 `leave` 的事务结构。
  1. `lockRoom`，查询双方的角色，做权限判断；
  2. `DELETE FROM room_members WHERE room_id = ? AND member_id = ?`。管理员身份在这一行上，删除即失去；
  3. 提交事务。

  然后在控制器里：
  1. `reportMembersChanged(roomId)`；
  2. `RoomHub::instance().removeMember(roomId, memberId, "kicked")`。

  被踢者不会是最后一名人类成员，因为操作者本身就是留在房间里的人类，所以不涉及删除房间。
- **封禁**：同一事务里依次：
  1. `lockRoom`，做权限判断；
  2. `INSERT IGNORE INTO room_bans (room_id, user_id, banned_by) VALUES (...)`；
  3. `DELETE FROM room_invites WHERE room_id = ? AND user_id = ?`；
  4. 对象是成员时删除成员关系。

  提交后，如果删除了成员关系，就和踢出一样通知，原因改为 `banned`。重复封禁时 `INSERT IGNORE` 不改变原记录，这和 spec 的“封禁记录不变”一致；这时如果对象仍是成员（不可能，因为封禁时已经移出，除非是迁移前的数据），也照样移出。
- **邀请检查**：`RoomService` 的邀请逻辑在写入邀请前，先查询 `room_bans` 里是否有这个用户，有就返回 `409 user_banned`。被邀请者已是成员时仍返回成功，这一步在封禁检查之前，因为成员不可能处于封禁状态。
- **加入检查**：封禁时已经删除了邀请，所以被封禁者没有邀请，现有逻辑会返回 `room_not_found`。为了防止并发（封禁和邀请同时进行），加入时也在事务里再查一次 `room_bans`，有记录就当作没有邀请。两边都已经在 `lockRoom` 之后执行，所以只要邀请的写入也在 `lockRoom` 之后，就不会有竞态。实现时确认邀请路径是否加了房间锁；如果没有，就加上。
- **封禁列表**：`SELECT u.username, bu.username AS banned_by, b.created_at FROM room_bans b JOIN users u ON u.id = b.user_id LEFT JOIN members bm ON bm.id = b.banned_by LEFT JOIN users bu ON bu.id = bm.user_id WHERE b.room_id = ? ORDER BY b.created_at, b.user_id`。只有群主和管理员可以查看。
- **`room_left` 带原因**：
  - `RoomHub::removeMember(roomId, memberId, reason)` 新增参数，默认值 `"left"`，退出房间时仍调用无原因的版本。
  - 跨实例发布的 `member_left` 事件里带上 `reason`，接收方在 `removeMemberLocally` 里把它写进发给被移出者的 `room_left`：`{"type":"room_left","room_id":7,"reason":"kicked"}`。
  - 旧客户端忽略多出的字段，所以兼容。
- 发消息时校验成员身份：`MessageService` 每次发送都会校验成员关系（`发送权限校验`）。实现时确认这个校验没有依赖可能过期的缓存，否则被踢者在缓存过期前还能发消息。如果有缓存，就在踢出时一起清除，跟 `leave` 保持一致。

### D12. 前端

- `api.js` 新增 `kickMember(roomId, username)`、`banUser(roomId, username)`、`unbanUser(roomId, username)`、`listBans(roomId)`。
- `MembersPanel.vue`：
  - 当前用户能管理某个人类成员时（按 D11 的 `canManage` 规则，前端用成员列表里的角色判断），在这一行显示“踢出”和“封禁”按钮。两个按钮都要二次确认，确认框里写明对方的用户名。
  - 群主和管理员可以看到“封禁列表”入口：
    - 列表显示用户名、执行者、时间，每行一个“解封”按钮；
    - 另有一个输入框，可以按用户名封禁不在房间里的人。
- 收到带原因的 `room_left`（`kicked` 或 `banned`）时：房间保留在列表并标记“已被踢出”或“已被封禁”，停止订阅与消息流跟踪，保留已经加载的消息。正在打开时继续显示原消息，并持久显示“你已被踢出房间 X”或“你已被禁止进入房间 X”，禁用输入框和发送按钮，关闭成员面板；之后不再请求该房间的成员、历史或封禁列表。未打开的房间以后点开时同样只显示本地已有消息和提示。
- 刷新列表时保留本地标记的房间；服务端重新返回同一个房间 id（例如重新邀请）时清除本地标记，以服务端状态为准。移出状态只存内存，刷新页面后消失。没有 `reason` 或 `reason` 为 `left` 时保持原行为。
- `ai-drafts.js` 只处理带 attempt/sequence 的累积片段，使用完整快照并忽略旧序号，不再保留非累积分支。
- 收到成功 ack 后，前端使用 ack 的 seq/created_at、待发送项的 client_msg_id/content 和当前用户信息，先拼出 `local: true` 的消息显示，再移除待发送项并补齐；ack 格式和服务端不变。`RoomStream` 的 receive、addPage、fetchMissing 均允许同 seq 的服务端版本替换本地版本，正式版本不被后到的重复数据覆盖。
- 被踢或被封的房间不再补齐历史；fetchAfter 直接返回空数组，已知 seq 缺口不会触发接口请求。
- 邀请时收到 `user_banned`，提示“该用户已被此房间封禁，需要先解封”。
- 前端单元测试覆盖：按角色显示按钮的规则，以及 `room_left` 按原因分支。

### D13. 配置与默认值

`.env.example` 和 `docker-compose.yml` 要做的修改：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HTTP_PORT` | `8080` | 宿主机 HTTP 端口，只做 301 |
| `HTTPS_PORT` | `8443` | 宿主机 HTTPS 端口，也用于 301 的目标 |
| `GATEWAY_CONN_PER_IP` | `100` | L4 每个 IP 的并发连接上限 |
| `SENTINEL_DOWN_AFTER_MS` | `3000` | Sentinel 判定主节点下线的时间 |
| `REDIS_HOST` | `redis-proxy` | 原为 `redis-master` |
| `COOKIE_SECURE` | `1` | 原为 `0`；只在直接用 HTTP 调试应用时才设为 0 |
| `ALLOWED_ORIGINS` | `https://localhost:8443` | 原为 `http://localhost:8080`。如果要用 `127.0.0.1` 访问，就加上 `https://127.0.0.1:8443` |

### D14. 测试

- **测试容器**：
  - `BASE_URL=https://gateway`、`WS_URL=wss://gateway/ws`、`ORIGIN=https://localhost:8443`；
  - 只读挂载 `data/certs/ca.crt`，用环境变量 `CA_BUNDLE` 指向它；
  - `conftest.py` 的 `requests.Session.verify` 和 `websockets` 的 `ssl` 上下文都用它，不允许关闭证书校验；
  - 测试容器依赖 `gateway` 健康。
- **cookie**：带 `Secure` 的 cookie 在 HTTPS 下正常收发，现有测试不用改。直连应用实例的跨实例测试走的是 HTTP，`requests` 不会在 HTTP 上发送 `Secure` cookie，所以 `conftest.py` 新增一个帮助函数：先经入口登录，再把 cookie 值手动放进 `Cookie` 头，直连 `http://app-1:8000` 或 `http://app-2:8000`。WebSocket 也一样，`Origin` 用 `ORIGIN`。
- **新测试文件**：
  - `test_gateway.py`：
    - HTTP 跳转：用 `allow_redirects=False` 访问 `http://gateway/api/auth/me` 和带查询参数的路径，检查 `Location`；
    - 通过 HTTP POST 登录拿到 301，并且这个账号的登录失败计数没有增加；
    - 明文 `ws://gateway/ws` 握手失败；
    - TLS 1.1 握手失败（`ssl.SSLContext` 的 `maximum_version = TLSv1_1`）；
    - 登录响应的 cookie 带 `Secure` 和 `HttpOnly`；
    - 伪造的 `X-Forwarded-For` 不改变限流：在 STRICT 轮次里，带不同的伪造头连续登录仍然被限流；
    - 每次新建连接（`requests` 每次用新的 Session）连续登录仍被限流（STRICT 轮次）；
    - 证书由本地 CA 签发、SAN 包含 `localhost`。
  - `test_gateway_limits.py`：在第 2 轮用 `GATEWAY_CONN_PER_IP=5` 运行。打开 5 个 TCP 连接（TLS 握手完成）后，第 6 个被关闭或者握手失败；关闭一个后又能新开；用 `limit_conn` 拒绝的连接不影响已有的 5 个。“其他地址不受影响”需要第二个源地址，在测试容器里很难构造，改由在同一轮次里另起一个容器（`docker compose run` 一个新的测试容器，它的 IP 不同）验证，或者在 tasks 里标明只做人工验证。实现时二选一，优先用第二个容器。
  - `test_multi_instance.py`：会话在两个实例上都有效；在 A 上订阅、经 B 发送，A 收到且 seq 连续；在 B 上登出，A 上的连接收到 4401；经 B 踢人，A 上被踢者的连接收到带 `kicked` 的 `room_left`。
  - `test_room_bans.py`：覆盖 chat-rooms 增量 spec 的每个场景（权限矩阵、自己、群主、AI 或非成员、被踢后重新邀请、封禁被邀请者和无关用户、重复封禁、解封、列表、执行者已离开、被封禁者邀请返回 `user_banned`、被封禁者加入返回 `room_not_found`、被踢者发消息被拒绝）。
  - `test_failover.py`：由 `scripts/test.sh` 分阶段调用，和现有的 `test_ai_faults.py` 一样用 `FAILOVER_PHASE` 区分：
    - `prepare`：创建用户、房间和一条消息，把状态写入 Redis 里的一个测试专用 key，或写入文件；
    - 测试脚本执行 `docker compose stop redis-1`；
    - `after`：在 30 秒内轮询，直到登录成功；然后验证发送和推送（两个实例）、登出断开、AI 回复（fake provider）、历史中有 `prepare` 阶段的消息。用 `redis-proxy` 执行 `INFO replication`，确认主节点已经是 `redis-2`；
    - 测试脚本执行 `docker compose start redis-1`，等它恢复；
    - `rejoined`：`redis-1` 的 `INFO replication` 显示 `role:slave`，系统继续工作。
  - 应用实例故障：`app-down` 阶段 `docker compose stop app-1`，验证新请求正常（不需要重新登录），WebSocket 重连后补齐；然后 `start app-1`。
  - L7 故障：`web-down` 阶段 `docker compose stop web-1`，验证所有新连接都成功（多发几次，覆盖哈希到 `web-1` 的情况）；然后 `start web-1`。
- **`scripts/test.sh`**：
  - 所有写死的服务名改为新名字，例如 `docker compose logs app-1 app-2 ai web-1 web-2 gateway`。
  - 第 2 轮加入 `GATEWAY_CONN_PER_IP=5`。
  - 新增故障阶段放在 AI 故障阶段之后、第 2 轮之前。每个阶段结束后用 `docker compose up -d --wait` 恢复完整的拓扑。
  - 故障切换之后，主节点变成了 `redis-2`。后续轮次不依赖哪个节点是主节点，所以不需要切回来。只有那些直接连某个具体节点的测试会受影响，这样的测试一律改为连 `redis-proxy`。
- **CI**：同一个 `scripts/test.sh`。拓扑变大后，启动时间和内存占用都会增加。GitHub Actions 的 `ubuntu-latest` 有 16 GB 内存，足够。如果超时，就调大 `--wait` 的超时，而不是删减测试。

### D15. README

- 访问地址改为 `https://localhost:8443`，并给出导入本地 CA 的步骤（macOS 钥匙串，设为“始终信任”）。
- 新的服务拓扑图（同 D1），以及每个服务的作用。
- Redis 故障切换：怎样手动演示（`docker compose stop redis-1`，然后看 `docker compose exec redis-proxy` 或者 Sentinel 的 `SENTINEL get-master-addr-by-name chat`）。
- 踢人与封禁的权限规则。
- 升级说明：旧的 `redis-master-data`、`redis-replica-data` 卷不再使用，可以用 `docker volume rm` 删除；MySQL 数据保留。

## Risks / Trade-offs

- [Redis 异步复制，切换时最后几毫秒的写入可能丢失] → 丢失的可能是刚创建的会话（用户需要重新登录）、限流计数、受信任设备的发送计数，或 AI 服务的任务状态。聊天消息以 MySQL 为准，不受影响。AI 任务丢失时，这条回复不会生成，用户可以重新提及。接受这个风险，不使用 `WAIT` 或 `min-replicas-to-write`：只有一个从节点时，它们会让从节点故障也变成写入故障。
- [改地址与断开旧连接之间的短暂窗口] → 单一后端的新连接只有一个去处，但两条管理命令之间，旧连接仍可能向旧主写入；受控切换时这些写入可能丢失，接受该风险，见 D7。Sentinel 没有多数时保持路由，目标实际不可用则返回可重试错误。
- [`redis-proxy` 是单点] → watcher 异常退出时结束主进程，由 Docker 重启策略恢复；不部署第二个代理，避免不同代理切换时刻不一致。
- [Pub/Sub 断开期间的实时推送丢失] → 消息已经在 MySQL 里，客户端靠下一条消息的 seq 缺口，或者重连时补齐。房间之后一直没有新消息时，缺失的那条要等到用户刷新或重连才出现。接受这个风险，见 D9。
- [Drogon 订阅的重连行为与阅读的源码不一致] → D9 的故障测试会发现，届时补上重新订阅逻辑。
- [L4 是单点] → 单机部署下无法消除，这次不处理。
- [一致性哈希让一个 L7 承担的 IP 不均衡] → 本地和测试流量很小，不受影响。
- [TLS 握手增加 CPU 开销] → 对聊天室的流量来说可以忽略。WebSocket 是长连接，握手只发生一次。
- [`COOKIE_SECURE` 改为默认开启后，直接用 HTTP 访问应用的调试方式会失效] → 在 README 和 `.env.example` 里说明，需要时设为 0。
- [测试和启动时间变长] → 接受。所有服务都有健康检查，`--wait` 能可靠地等到就绪。

- [撤权兜底延迟与快照竞态] → D9a 的故障触发未覆盖时，用户接受最多约一个检查周期继续收到数据；查询失败等待下轮。快照后退出又立即重新加入并订阅，可能被误移除一次，刷新恢复。
- [ack 丢失与被移出同时发生] → 重连重发时服务端先查成员再去重，可能返回 room_not_found，前端显示发送失败，而原消息已保存且其他成员可见。用户接受此极少见边界，本轮不修改服务端 ack 或去重顺序。
- [被移出前刚触发的 AI 回复] → 已保存的 @AI 消息照常触发生成，费用仍由触发者承担；不新增被踢后取消生成或改用其他 key 的检查。

以下三项是 2026-11-04 verify 留下的遗留问题，本次 change 不处理，后续另行补上：

- [切换窗口内的业务响应没有被直接测试] → high-availability 规格“切换期间的请求”场景要求：切换未完成时，登录和发消息要么正常完成，要么在超时内返回可重试的错误。现在的 `tests/integration/test_failover.py` 在切换期间只轮询 `/health`，登录、发消息、AI 回复和登出都是在恢复之后才断言的，所以这个场景只算部分验证。后续要补的测试：停主节点之前先准备好已登录的连接，在切换窗口内实际发起登录和发消息，断言每次都在有限时间内成功，或者返回 `retryable` 的错误，不能挂起。
- [同步测试客户端限定 TLS 1.2] → 生产入口同时支持 TLS 1.2 和 1.3，`test_gateway.py` 的异步用例也分别强制两个版本做了 WSS 收发。但 `tests/integration/conftest.py` 的同步 `Socket` 夹具限定为 TLS 1.2，因为 websockets 13.1 的同步客户端在 TLS 1.3 下会间歇握手超时。修好 Redis 回调死锁后这个现象仍然存在，根因还没查明。所以用同步夹具跑的那部分测试，不能算作 TLS 1.3 的覆盖。后续：用最小复现单独调查这个客户端问题，不能用关闭证书校验来绕过。
- [对账的“再跑一轮”标记有一个窄竞态] → `src/ws/room_hub.cc` 的 `RoomHub::reconcile` 在本轮读到 `reconcileAgain_` 为 false 之后、把 `reconciling_` 清为 false 之前，如果有新的对账请求到达，它会看到 `reconciling_` 仍为 true，只设置 `reconcileAgain_` 就返回了，而这个标记已经没人再检查。最坏情况是那次对账推迟到下一个检查周期，属于已接受的撤权延迟范围。后续：在清除 `reconciling_` 之后再检查一次 `reconcileAgain_`，有就重新进入循环。

## Migration Plan

1. 拉取后执行 `docker compose down`（保留卷），再 `docker compose up -d --build --wait`。
   - `certs` 会在 `data/certs/` 生成证书；
   - Flyway 会执行 `V4__room_bans.sql`。
2. Redis 数据卷改了名，开发库里 Redis 的数据（会话、受信任设备、缓存）会清空，用户需要重新登录。MySQL 数据不受影响。确认无误后，可以删除旧卷 `redis-master-data` 和 `redis-replica-data`。
3. 浏览器改用 `https://localhost:8443`，按 README 导入 `data/certs/ca.crt`。
4. 回滚：切回旧的提交，执行 `docker compose down` 和 `up`。`room_bans` 表留在库里不影响旧代码。如果需要彻底回滚，手动 `DROP TABLE room_bans`，并删除 Flyway 历史表里 V4 的记录。

## Open Questions

- ~~HAProxy 的 `tcp-check send` 能否直接展开 `${REDIS_PASSWORD}`？~~ 已解决：`scripts/redis-proxy.sh` 把 AUTH 命令编成 RESP 十六进制，放进环境变量 `REDIS_AUTH_HEX`，配置里用 `tcp-check send-binary` 发送。这样密码中的空格或引号不会破坏参数边界，密码也不会写进配置文件。
- ~~测试“其他地址的连接不受影响”时，能不能在 CI 里稳定地拿到第二个源地址？~~ 已解决：`scripts/test.sh` 另起一个测试容器作为第二个来源，两者通过 Redis 里的测试 key 同步，自动验证，不需要人工。
