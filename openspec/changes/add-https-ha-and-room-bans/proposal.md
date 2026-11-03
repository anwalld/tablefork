# Proposal

## Why

浏览器到 Nginx 这一段现在是明文 HTTP：登录、注册的密码，保存 API key 的请求，以及 WebSocket 里的聊天内容都没有加密。项目背景里定下的接入层是 L4 和 L7 两层、聊天服务多实例，但目前只有一个 Nginx 和一个聊天服务实例。Redis 虽然有一主一从，主节点挂了不会自动切换。房间里也只能任免管理员，管理员和群主没办法把捣乱的人请出去，或者阻止他再被拉回来。

## What Changes

- **HTTPS**：
  - 浏览器只通过 HTTPS 和 WSS 访问，入口是 `https://localhost:8443`。
  - 访问 `http://localhost:8080` 一律 301 跳到 HTTPS，HTTP 请求不会转给聊天服务。
  - 会话 cookie 和设备 cookie 默认带 `Secure`。
  - 首次启动时由一个一次性容器生成本地 CA 和服务器证书，放在不进版本库的 `data/certs/`。之后启动时，已有证书不会被覆盖。
  - **BREAKING**：默认入口地址、`ALLOWED_ORIGINS` 默认值和 `COOKIE_SECURE` 默认值都会变。
- **接入层改为两层**：
  - L4 用 Nginx stream，只转发 TCP，不解开 TLS。它按客户端 IP 限制并发连接数，并用 PROXY protocol 把真实客户端地址交给 L7。
  - 两个 L7 实例负责结束 TLS、按 IP 限流认证请求、提供前端静态文件，并把请求分发到聊天服务。
  - L4 按客户端 IP 做一致性哈希，让同一个 IP 固定落到同一个 L7，按 IP 限流就不会因为有两个 L7 而被放大一倍。
- **聊天服务多实例**：运行两个聊天服务实例，任一实例都能处理任何请求和 WebSocket 连接。补上真正跨实例的集成测试：在一个实例上订阅、在另一个实例上发送；在一个实例上登出，另一个实例上的连接被断开。
- **Redis 自动故障切换**：
  - 增加 3 个 Sentinel 监控 Redis 主从，主节点故障时自动提升从节点。
  - 增加一个 HAProxy 作为统一的 Redis 入口，它只把连接转给当前的主节点。聊天服务和 AI 服务都只连这个入口，不需要重启就能跟上切换。
- **房间内踢人与封禁**：
  - 群主可以把管理员和普通成员踢出房间或封禁，管理员只能踢出或封禁普通成员。
  - 被踢出的人可以再被邀请。被封禁的人在解封前不能被邀请，也不能加入；封禁时会同时取消他未处理的邀请。
  - 群主和管理员可以查看封禁列表并解封。
  - 被踢或被封的人，在所有实例上的连接都会立刻收到带原因的 `room_left`，不再收到这个房间的推送。
  - 前端在成员面板里加入踢出、封禁按钮和封禁列表。

不在本次范围内：
- 全站封号和站点管理员；
- 真正的公网部署和公网证书；
- Redis Cluster。受信任设备的 Lua 脚本在 Sentinel 下照常可用，只在 design 里写明前提；
- HSTS 和 HTTP/2。

## Capabilities

### New Capabilities

- `edge-gateway`：浏览器到系统的接入。包括只用 HTTPS 和 WSS 传输、HTTP 跳转、cookie 只经 HTTPS 发送、本地证书、识别真实客户端地址、按 IP 限制并发连接数。
- `high-availability`：多个聊天服务实例和接入实例的行为一致，单个实例故障时服务继续可用，Redis 主节点故障时自动切换。

### Modified Capabilities

- `chat-rooms`：
  - 修改“邀请用户”：不能邀请被封禁的用户。
  - 新增“踢出成员”“封禁用户”“解除封禁”“查看封禁列表”。
- `user-auth`：修改“认证请求按来源限流”。限流按浏览器的真实 IP 计算，经过两层接入、分到不同的接入实例时也不会被放大。

## Impact

- 部署（`docker-compose.yml`）：
  - 新增 `certs`、`gateway`（L4）、`web-1` 和 `web-2`（L7）、`redis-1` 和 `redis-2`、`sentinel-1` 到 `sentinel-3`、`redis-proxy`（HAProxy）；`app` 拆成 `app-1` 和 `app-2`。
  - 原来的 `nginx`、`redis-master`、`redis-replica` 服务被替换。Redis 数据卷改名，本地开发库的 Redis 数据不保留（MySQL 不受影响）。
- Nginx：
  - 新增 L4 的 stream 配置；
  - L7 配置改为监听 TLS 和 PROXY protocol，并通过 upstream 分发到两个应用实例；
  - 镜像固定到支持 upstream `resolve` 的 1.27.5。
- 新增 HAProxy 配置、Sentinel 启动脚本、证书生成脚本。
- C++：
  - `RoomController` 和 `RoomService` 新增踢出、封禁、解封、封禁列表；
  - 邀请和加入时检查封禁；
  - `RoomHub` 的 `room_left` 带上原因。
- 数据库：新增迁移 `V4__room_bans.sql`（`room_bans` 表）。
- 前端：`MembersPanel` 加入踢出、封禁和封禁列表；`api.js` 新增对应接口；处理带原因的 `room_left`。
- 测试：
  - 集成测试改为通过 HTTPS 入口访问，并信任本地 CA；
  - 新增跨实例、封禁、HTTPS、连接数限制的测试；
  - `scripts/test.sh` 新增 Redis 故障切换和应用实例故障阶段；
  - 清理脚本兼顾 `room_bans`。
- 配置：`.env.example` 新增 `HTTPS_PORT`、`HTTP_PORT`、`GATEWAY_CONN_PER_IP`、Sentinel 相关参数；`COOKIE_SECURE` 和 `ALLOWED_ORIGINS` 改变默认值。
- 文档：README 说明 HTTPS 入口、导入本地 CA、新的服务拓扑和踢人封禁。
