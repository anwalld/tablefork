# 历史快照：不代表当前状态

> 历史阶段记录：保留本阶段的提交、失败与验收范围，不代表当前部署或剩余任务。最新结果见[当前交接](../../../../docs/handoff.md)，未完成需求见[后续计划](../../../../docs/roadmap.md)。历史操作限制不替代用户后续明确指令。

本文件保留原交接与 Claude 的死锁调查；当前状态以 handoff.md 和 verification-report.md 为准。

# CppAiServer 当前开发交接

交接时间：2026-11-03 晚间，Asia/Shanghai。用户最新指示：**停止继续开发，整理交接文档**。本文件记录本轮实际工作；下一位 AI 应以当前仓库和本文件为准，不要根据早先的 AI 成员交接切回旧分支。

> **2026-11-03 23:20 补充：全量失败的根因已经定位，是多实例下 RoomHub 在 Redis 订阅回调里退订造成的死锁，见第 0 节。尚未修复。**
>
> **最终交接状态：最新 HTTPS 全量没有通过。已按用户要求中断，脚本退出码 2；中断前 12 failed / 69 passed / 12 skipped / 1 error。正常配置已恢复，全部常驻服务健康。详见第 5 节。**

> **2026-11-04 Codex 续跑：已修复第 0 节的订阅回调死锁。固定跨实例测试已验证旧服务失败、修复后通过；新增直连实例帮助函数，当前 6/31。源码证实独立 WSS 上游仍需保留；移除 TLS 1.2 限制后同步客户端仍有握手超时，而两个实例健康，故保留测试兼容设置和异步 TLS 1.3 覆盖。最新完整回归正在运行，日志 `/private/tmp/cppai-https-deadlock-fixed-full.log`，退出码将保存到同名前缀 `.exit`。以下旧状态作为历史保留。**

## 0. 补充（2026-11-03 23:20，Claude）：全量失败的根因已定位，尚未修复

**第 5 节里“根因尚未定位”的全量失败，根因是多实例下的一个真实死锁，不是 TLS 或测试客户端的问题。** 代码没有改，下一位接手者应先修这个。

### 复现

在第一轮配置下运行 `pytest -v -x`，结果为 108 passed / 17 skipped，然后在 `test_messaging.py::test_leaving_stops_delivery` 停下：被移出房间的连接 5 秒内没有收到 `room_left`。这次 `test_faults.py` 通过了。失败的位置每次不同，因为它取决于两个连接被分到哪个实例。

现场证据：
- `app-2` 从 15:11:13 起，Redis 命令全部 `Command execution timeout`，持续不恢复。日志里的线程号都是 42。
- 它的 `/health` 因此返回 503。L7 的 `max_fails=1` 把它判为不可用，于是后面出现了 HTTP 超时、WSS 握手超时、登录返回 503 这类连带失败，与 `/private/tmp/cppai-https-group2-final.log` 的表现一致。
- 同一轮中，`test_e2e.py::test_full_room_lifecycle` 的 teardown 耗时 20 秒，`test_ai_members.py` 两个测试的 teardown 各 10 秒，说明 WebSocket 关闭握手等满了 websockets 默认的 10 秒，这也是卡死的表现。

### 根因

调用链如下：

1. 用户经实例 A 退出房间，`RoomHub::removeMember` 先在本地移除，再发布 `member_left`。
2. 实例 B 的 Redis 订阅回调 `RoomHub::onChannelMessage` 收到 `member_left`，调用 `removeMemberLocally`。B 上这个房间的连接移除后为空，于是调用 `eraseIfEmpty`，进而**在订阅回调里**调用 `subscriber()->unsubscribe(同一个频道)`。
3. Drogon v1.9.13 调用这个回调时，持有该频道 `SubscribeContext::mutex_` 的锁（`SubscribeContext.cc` 的 `onMessage`）。`RedisSubscriberImpl::unsubscribe` 会调用 `subCtx->disable()`，再去拿同一把锁（`SubscribeContext.h` 的 `disable()`）。这把锁是不可重入的 `std::mutex`，所以 Redis 的事件循环线程永久死锁。
4. 卡死发生在 `removeMemberLocally` 发出 `room_left` 之前，所以被移出者收不到通知。之后分到这条 Redis 线程上的命令全部超时。

单实例时这条路径不会触发：发布方的本地已经先移除了成员，回调里找不到要移除的连接，也就不会调用 `unsubscribe`。所以这是拆成 `app-1` 和 `app-2` 之后才暴露的。

源码依据：本机 `~/Library/Caches/Homebrew/drogon--git` 的 `git describe` 是 `v1.9.13`，与 Dockerfile 的 `DROGON_VERSION` 一致。具体看 `nosql_lib/redis/src/SubscribeContext.cc:114-121`、`SubscribeContext.h:62-67`、`RedisSubscriberImpl.cc:110-127`。

### 建议的修法（未实施）

- 在 `RoomHub::onChannelMessage` 中，收到 `member_left` 时不要同步调用 `removeMemberLocally`，而是投递到应用的事件循环去执行。例如 `drogon::app().getLoop()->queueInLoop([this, roomId, memberId] { removeMemberLocally(roomId, memberId); });`，这样 `unsubscribe` 就不会在 Drogon 持锁的回调里执行。
- 顺带检查其他从订阅回调里调用 `unsubscribe` 的路径。`SessionWatcher` 的 `sessions:ended` 回调 → `end()` → `RoomHub::removeConnection` → `eraseIfEmpty`，退订的是另一个订阅器上的房间频道，不是同一把锁，所以不会死锁。为了保险，也可以同样投递到事件循环执行。
- 在 `RoomHub` 的 `subscriber()` 或 `onChannelMessage` 旁边加注释：Drogon 的消息回调持有频道锁，回调里不能订阅或退订。
- 回归测试：放在第 4 组的跨实例测试里，用直连实例的夹具，把甲的连接放在 `app-1`、乙的连接放在 `app-2`。乙经 `app-1` 退出房间后：
  - 乙在 `app-2` 上收到 `room_left`；
  - 之后 `app-2` 的 `/health` 仍是 200，甲和其他用户在 `app-2` 上的收发都正常。

  第 5 组的踢人和封禁走的也是 `removeMember`，修好之后同样受益。

### 需要重新评估的两个绕行办法

第 4 节的两个结论是在这个死锁存在时得出的，表现（握手超时、404、关闭卡住）都可能是 Redis 卡死带出来的。修完死锁后：

1. 去掉 `conftest.py` 的 `Socket` 里 `maximum_version = TLSv1_2` 的限制，用 TLS 1.3 重新跑全量。通过就删掉这个限制和相关注释；仍有问题再保留，并写明修死锁后复现的证据。
2. 把 `/ws` 重新指向带 keepalive 的 `app_servers`，跑 `test_gateway.py` 的 `test_repeated_http_then_wss` 和协议收发测试。仍然返回 404 就保留独立的 `app_websockets`；不再出现就合并回一个 upstream，并同步修改 design D6。

### 当前状态

- 代码、配置、OpenSpec 任务都没有改动，勾选仍为 5/31。
- 系统已用 `docker compose up -d --wait` 恢复为正常配置，9 个服务都健康，卡住的 `app-2` 已被重建。
- 复现用的脚本和日志放在 Claude 会话的临时目录，不属于项目，接手者不需要它们。按上面的步骤就能复现。

## 1. 从这里开始

- 项目：`/Users/mima0000/CLionProjects/CppAiServer`
- 分支：`feat/https-ha-and-room-bans`
- HEAD：`71855d174c11ecdc77496d8f83221c1bc1ff7892`
- HEAD 标题：`docs: 规划 add-https-ha-and-room-bans（HTTPS、两层接入、多实例、Sentinel、房间踢人封禁）`
- 当前唯一活动 OpenSpec 变更：`add-https-ha-and-room-bans`，schema 为 `spec-driven`。
- **OpenSpec 已勾选 5/31：1.1、1.2、2.1、2.2、2.3。剩余 26 项。部分未勾选任务已有实现，但验证未完成，不能当作全未做或全已做。**
- 本轮没有提交、推送、创建 PR、执行远程 CI，也没有归档该变更。
- 工作区有修改和新文件，必须保留。`git diff --stat` 不含未跟踪的新文件，审查时同时看 `git status --short`。
- 用户暂不处理本地 CA 导入，要求交接。**不要把浏览器验证写成已通过，也不要再次要求用户立即导入。**

本轮开始时仓库已经不在旧的 `feat/add-ai-members` 分支；旧变更不再是活动变更。本轮没有继续旧的真实 DeepSeek 恢复测试。

## 2. 用户确认的产品规则

本变更原有完整需求见 proposal、design 和四份 specs，不在此重复替代。

本轮额外澄清并已修正规格：小王作为管理员封禁小李，随后小王退出房间，封禁列表仍应显示执行者“小王”；只有小王账号被删除才显示为空。已修改：

`openspec/changes/add-https-ha-and-room-bans/specs/chat-rooms/spec.md`

它此前和 design D10 有冲突，现在保持一致。封禁代码尚未开始实现。

用户沿用 `/opsx:*`、`/openspec:*` 到项目对应 OpenSpec skill 的映射。本轮使用 `.agents/skills/openspec-apply-change/SKILL.md`。需要先读 CLI 返回的 contextFiles；每项按实际行为及验证结果勾选。tasks.md 要求每组完整跑通 `scripts/test.sh` 后再进入下一组。

## 3. 本轮已经写入的实现

### 本地证书（第 1 组完成）

新增：

- `docker/certs.Dockerfile`：Alpine 3.20 + OpenSSL。
- `scripts/gen-certs.sh`：生成 ECDSA P-256 本地 CA（10 年）及服务器证书（825 天）。SAN 是 localhost、gateway、127.0.0.1、::1。
- Compose 的一次性 `certs` 服务，将证书写入 `data/certs/`。
- `.gitignore` 忽略 `data/certs/`；新增 `.dockerignore` 排除 `.env`、`.git/` 和 `data/certs/`。这很重要，因为原单元测试 Dockerfile 有 `COPY data ./data`，否则会把 CA 私钥复制进测试镜像。

重复运行保留原 CA 与服务器证书；服务器证书剩余不足 30 天、SAN 不符或验证失败时续签，CA 不变；不完整的 CA 文件会报错，不静默覆盖。

下游以 `service_completed_successfully` 依赖 `certs`。没有这种依赖时，Compose `up --wait` 曾把证书容器成功退出误判为失败，已经修正。

### HTTPS、L4/L7 与必要的双应用配置（第 2 组部分完成）

- `docker/gateway.Dockerfile`、`docker/gateway.conf.template`：Nginx stream L4，监听 80/443；按真实源 IP 一致性哈希到两个 L7；发送 PROXY protocol；按 IP 限制 TCP 并发连接；动态解析上游。
- `docker/nginx.conf.template`：L7 监听 80（只做 301）、443（TLS + PROXY protocol）和 8081（内部健康检查）。TLS 1.2/1.3，覆盖而非追加 X-Forwarded-For/X-Real-IP，认证限流按真实 IP 计算。
- L4 与 L7 镜像固定 `nginx:1.27.5-alpine`。官方该镜像的 stream **静态编译**，不要重新添加 `load_module ...ngx_stream_module.so`；本轮已验证该文件不存在，重复加载会启动失败。
- Compose 原 `nginx` 替换为 `gateway`、`web-1`、`web-2`；仅 gateway 发布宿主机端口；网段默认 `172.28.0.0/16`，可用 `DOCKER_SUBNET` 调整；L7 只读挂载服务器证书和私钥，不挂 CA 私钥。
- 原 `app` 已通过 `x-app` YAML 锚点拆成 `app-1`、`app-2`，共用 `cppaiserver-app` 镜像。这是第 2 组上游配置的依赖，提前写了第 4 组部分部署工作。**第 4 组跨实例和故障验证尚未做，4.1 未勾选。**
- `.env.example`、Compose、`src/common/config.cc`：Cookie 默认 Secure；默认 Origin 改 HTTPS；增加 HTTP_PORT、HTTPS_PORT、GATEWAY_CONN_PER_IP、DOCKER_SUBNET 注释。
- 本地 `.env` 仅调整已有入口地址/COOKIE_SECURE，凭据未打印，也未复制进文档。它被忽略，不会出现在 git diff 中。
- README 已添加 HTTPS 地址、CA 导入、Cookie 调试和连接上限说明。**顶部旧拓扑图及后文 Redis/开发命令还没整体更新。**

### 测试实现

- `tests/integration/conftest.py`：HTTP 和 WSS 改经 gateway，读取 `CA_BUNDLE`，严格验证 CA；保留 Secure Cookie。同步 Socket 夹具目前限定 TLS 1.2，原因和额外 TLS 1.3 覆盖见下一节。
- `tests/integration/load_test.py`：异步 WSS 同样信任 CA。
- 新增 `test_gateway.py`：HTTP 跳转、登录不进入应用/不增加失败计数、明文 WS 拒绝、TLS 1.1 拒绝、CA/SAN、Secure/HttpOnly Cookie、伪造转发头和新连接限流，以及 TLS 1.2/1.3 异步 WSS 收发和 HTTP/WSS 重复切换。
- 新增 `test_gateway_limits.py`：一个测试容器占满 5 个连接、第六个拒绝、已有连接仍正常、关闭后释放名额；另一个容器以不同源 IP 访问。两者经带唯一 ID 的 Redis 测试 key 同步，避免仅靠人工验证。
- `scripts/test.sh`：替换日志服务名；第 2 轮连接上限设为 5，启动第二个来源容器；最后认证限流检查改为含伪造头和新 TLS 连接的测试；增加 EXIT trap，无论测试成功或失败都会尝试恢复正常 Compose 配置，并保留原退出码。
- `test_messaging.py` 的订阅计数测试：两个连接可能落到两个实例，允许 1–2 个 Redis 订阅；关闭其中一个后仍为 1，全部关闭后为 0。

## 4. 已定位的两个 WSS 问题，交接时请保留结论和回归

### 普通 HTTP 连接池不能直接用于此版本 Drogon 的协议升级

最初把 `/ws` 和普通 HTTP 都指向带 keepalive 的 `app_servers`。出现大量握手 404。分别建立不带 keepalive 的 `app_websockets` 后，问题消失；移除独立 upstream、恢复共享池时，新增的两个协议收发测试及重复 HTTP/WSS 测试再次全部失败，返回 404。再次恢复独立 upstream 后定向套件通过。

最终配置：普通 HTTP 使用 `app_servers`，保留 keepalive 32；WSS 使用 `app_websockets`，目标同样是 app-1/app-2，带 zone/resolve/故障转移，但不复用 HTTP 连接。design D6 已同步说明。这里的结论基于实际对照测试，未完成 Drogon 内部源码根因审计。

### 同步 Python TLS 1.3 客户端间歇握手超时

即使独立 WSS upstream，websockets 13.1 的同步客户端也间歇超时；直连应用 WS 正常，直连 L7 的同步 TLS 1.3 也能复现。临时容器升级 websockets 15.0.1 没有得到修复证据，项目依赖仍保持 13.1。

对照：同步客户端限定 TLS 1.2，40 次 HTTP/WSS 连续切换通过；异步客户端以 TLS 1.3 连续 40 次 WSS 订阅也通过。因此目前只把同步测试夹具限定在 TLS 1.2，生产入口继续支持 TLS 1.2/1.3，并新增分别强制两个版本的异步收发测试。**不能声称已经从 CPython/OpenSSL 源码证明根因；这是基于实测的测试客户端兼容处理。**

不要删除 TLS 1.3 的独立覆盖，也不要用关闭证书校验解决问题。

## 5. 验证证据

### 已通过

1. 证书重复运行：CA 与服务器证书哈希不变；`openssl verify` 通过；四项 SAN 正确。
2. 临时容器内错误 SAN 和 1 天有效期续签：只更新服务器证书，CA 不变。
3. Git 忽略和测试镜像隔离：公共密码表仍被跟踪；重建单元测试镜像后 `/src/data/certs/ca.key` 不存在，而公共密码表存在。
4. **第 1 组完整 `scripts/test.sh` 退出码 0**：首轮集成 180 passed / 15 skipped；5 个 AI 故障阶段各通过；第二轮 8 passed；正常配置恢复。日志：`/private/tmp/cppai-https-group1.log`。注意这是 HTTPS 改动之前，不能拿它证明当前 HTTPS 全量通过。
5. gateway、web-1 的 `nginx -t` 通过；两个 web、两个 app 都成功启动健康。
6. `curl --noproxy '*' --cacert data/certs/ca.crt https://localhost:8443/health` 返回 `{"mysql":"ok","redis":"ok"}`；HTTP `/x?y=1` 返回 301，Location 为 `https://localhost:8443/x?y=1`。宿主机 curl 有异常代理环境，所以本地验证加 `--noproxy '*'`，不要打印所有环境变量查凭据。
7. 最新定向接入测试：**10 passed / 1 skipped**，无新增弃用警告，日志 `/private/tmp/cppai-wss-split-targeted.log`。跳过的是需 STRICT_AUTH_RATE 的严格认证限流测试，正常留到第二轮。
8. `test_ai_delivery.py test_ai_members.py` 定向 15 passed；日志 `/private/tmp/cppai-https-first-failure.log`。这是排错阶段的结果，不替代最终全量。
9. 最近 `git diff --check`、`openspec validate add-https-ha-and-room-bans --strict` 均通过。

### 最新全量回归：已有失败，已中断并恢复正常配置

- 命令：`bash scripts/test.sh`
- 日志：`/private/tmp/cppai-https-group2-final.log`
- **最终脚本退出码 2**。用户要求交接后，向测试容器发送 SIGINT，pytest 输出 KeyboardInterrupt 和已执行部分的总结：**12 failed / 69 passed / 12 skipped / 1 error，运行 292.07 秒**。
- 全量未完成，严格连接限制、第二来源及第二轮认证限流尚未执行到，不能勾选 2.5–2.7。
- 首个失败是 `test_faults.py::test_message_survives_failed_publish`，表现为 HTTP 请求超时；接着 Pub/Sub 重连、publish 故障下订阅、删除房间缓存等测试失败。随后 gateway 两种协议的 WSS 测试也超时，登录延迟测试出现 503，另有一个 fixture error。**根因尚未定位，不应把它们都归因于前面的同步 TLS 1.3 客户端问题。**
- 值得优先核查故障测试临时修改 Redis ACL 与双实例/AI 并发后的恢复情况，以及两个后端的状态；只是排查方向，尚无根因结论。
- 本轮完整 pytest 摘要及堆栈已在该日志中；先读首个失败，避免只看最后大量连带错误。
- EXIT trap 已完成恢复。最终 `docker compose ps`：ai、app-1、app-2、gateway、mysql、redis-master、redis-replica、web-1、web-2 全部 running / healthy；额外确认 app-1 的 COOKIE_SECURE=1，ai 的 AI_FAKE_PROVIDERS 为空（恢复正常服务商配置）。
- 原测试容器已删除，工具 session 76697 已结束。**当前没有这轮回归继续运行，不需要继续等待，也不要沿用旧 session。**
- 早先 `/private/tmp/cppai-https-group2.log` 为更早失败版本（64 failed / 123 passed / 18 skipped），发生在两个 WSS 改动之前；它不代表最终工作区的唯一故障结果。

### 未验证

- 浏览器人工登录、聊天、证书信任：in-app browser 打开 `https://localhost:8443` 报 `net::ERR_CERT_AUTHORITY_INVALID`。未绕过，未替用户导入 CA。用户对此回复“算了，你整理交接文档吧”。2.4、2.8 继续未完成。
- 真正首次启动、无证书的完整拓扑：已准备隔离新 checkout `/private/tmp/cppai-https-clean-b4izvk6z`，有本轮修改及随机临时凭据，HTTP 18080 / HTTPS 18443，网段 172.30.0.0/16，假模型。**尚未运行 compose up，也没有创建该环境数据卷**。路径亦写在 `/private/tmp/cppai-https-clean-path`。不要把“已准备”写成“验证通过”。
- TLS 定向测试虽通过，整体 HTTPS 回归已失败并中断，需要先定位故障、重新完整跑通，才考虑勾选 2.5–2.7。

## 6. 还没有实现的工作

| 任务 | 当前状态 |
|---|---|
| 2.4、2.8 | 配置/文档已改，浏览器与新环境手工验收未完成 |
| 2.5–2.7 | 已有实现，全量已失败并中断；双来源连接测试未执行到 |
| 3.1–3.7 | Sentinel、HAProxy、Redis 改名、超时检查、故障测试、文档均未实现；目前仍为 redis-master / redis-replica |
| 4.1 | 双 app 部署已提前写入，完整验收/勾选未做 |
| 4.2–4.5 | 没有直连实例夹具、正式跨实例套件、app/L7 故障阶段，也没更新完整拓扑文档 |
| 5.1–5.7 | 踢人/封禁迁移、C++ 服务接口、推送 reason、清理脚本、Vue 和测试均未开始，仅澄清并同步了执行者退出的规则 |
| 6.1 | 当前变更最终干净 checkout 全量验证未执行 |
| 6.2 | 没有提交/推送/PR/远程 CI；不得写成绿色 |

README 还有旧的 `redis-master`/`redis-replica`、旧开发命令和顶部旧拓扑，须随后续各组一起更新。`src/common/config.cc`、AI 配置及清理脚本中的 Redis 默认值目前仍为旧主机，等第 3 组统一迁移，不要现在误称已经使用 redis-proxy。

## 7. 推荐的接手顺序

1. 本轮回归已停止、正常配置已恢复。先读 `/private/tmp/cppai-https-group2-final.log` 中的第一个失败及完整堆栈。
2. 重新读当前 git status、项目规则、OpenSpec CLI 状态；保留所有已修改和未跟踪文件。
3. 第 2 组全量已经失败，先定位 publish 故障测试之后的连带超时/503；修复后继续验证双容器连接上限同步、严格认证限流顺序及服务重建后的 DNS 暂态。当前 gateway TCP healthcheck 只检查监听，不证明所有上游已收敛，重建时曾出现短暂 no live upstreams/SSL EOF。
4. 完成证据核查后再勾选确实满足描述的任务。浏览器步骤继续单独列未验收，遵守用户暂不导入的选择；如这阻碍原定工作流，应明确说明，而不是静默伪造或删掉验收条件。
5. 按原 tasks.md 继续 Redis HA → 多实例验证 → 房间管理 → 最终干净环境/CI，除非用户有新指示。
6. 最后才考虑归档；用户本次没有要求归档或合并。

常用只读命令：

```bash
cd /Users/mima0000/CLionProjects/CppAiServer
git status --short --branch
openspec list --json
openspec status --change add-https-ha-and-room-bans --json
openspec instructions apply --change add-https-ha-and-room-bans --json
tail -n 80 /private/tmp/cppai-https-group2-final.log
```

需要重跑时：

```bash
cd /Users/mima0000/CLionProjects/CppAiServer
bash scripts/test.sh > /private/tmp/cppai-https-next-full.log 2>&1
```

后续可把退出码另存文件，避免交接时只能根据日志推测。完整脚本耗时数分钟，失败或超时时可能更久；需等待退出，不能启动后直接声称验证结束。

## 8. 权限、凭据和历史资料

- 本会话可写根不包含 CLion 项目，项目写入、Docker/网络操作通过 `require_escalated` 审核执行。不要绕过沙箱。
- 本轮未触发推送；早先会话的推送曾被自动审核拒绝，理由为缺少明确授权向 `github.com/anwalld/CppAiWebServer` 披露代码。没有新增的具体推送授权，接手者不要自行推送。
- 不要输出 `.env`、CA 私钥、API key 或会话 cookie。临时隔离 checkout 的 `.env` 也不应打印。
- 不要运行旧的 `/private/tmp/cppai-recovery-edit/sync.py`，它面向旧分支，可能覆盖当前工作。
- 旧历史交接：`/Users/mima0000/Documents/ChatGPT/项目/CppAiServer-项目交接-2026-11-03.md`，描述旧 AI 成员阶段；已在其开头添加本轮入口说明。旧验证报告同样不是本变更的当前结论。
- 项目内简要记录：`openspec/changes/add-https-ha-and-room-bans/implementation-notes.md`；完整交接以本文件为准。
