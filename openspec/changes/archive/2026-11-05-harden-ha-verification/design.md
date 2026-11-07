# Design

## Context

见 proposal.md。当前生产源码固定 Drogon v1.9.13；测试镜像为 Python 3.12、websockets 13.1。用户明确排除握手拒绝后多发关闭帧的问题，本轮不补 Drogon。

实测对照：原同步客户端 TLS 1.2 连续 20 次通过，TLS 1.3 第一次在原有 5 秒诊断时限内超时；只将首次 sendall 完成置于第一次 recv 之前，相同服务器、CA、时限下 TLS 1.3 连续 30 次握手/订阅通过。上游问题与诊断相符：
https://github.com/python-websockets/websockets/issues/1779
https://github.com/openssl/openssl/issues/7967

## Goals / Non-Goals

**Goals:** 补齐切换窗口业务证据；移除同步夹具的 TLS 1.2 限制；用线程与协程证据关闭或确认 S1；修复 Sentinel 自身 ID 进入同伴表的缺陷。

**Non-Goals:** 不改生产 TLS、session tickets、重连策略、限流、Redis 拓扑、协议、Drogon 版本或握手拒绝响应；不新增真实模型调用。

## Decisions

### D1. 同步 TLS 客户端首次发送完成后再读取

增加仅供同步 WebSocket 测试使用的 SSL socket 类。websockets 13.1 的接收线程会在发送 HTTP Upgrade 前读取 TLS session tickets；通过每连接 Event，让 recv/recv_into 等待首次 sendall 完成。sendall 的 finally、shutdown 和 close 都释放门闩，避免失败清理时留下线程；Event 初始化有锁，因为 SSLContext 不会调用普通 socket 子类的 __init__。

门闩是读写次序约束，不是 sleep、重试或放宽时限。保留严格 CA/主机名验证，不设置协议上限；默认夹具可协商 TLS 1.3，单独强制 TLS 1.2/1.3 的重复握手与订阅用例覆盖两者。异步协议测试保持原样。增加未信任 CA、错误主机名和首次写入前关闭的回归。

不采用禁用服务端 session tickets 或升级 websockets 碰运气：前者改变被测服务器，后者上游新版本仍有相同报告。也不把现有全部同步测试改成异步 API。

### D2. 故障前就绪的业务观察客户端

在 test_failover.py 新增 window 阶段，脚本在 stop redis-1 之前后台启动它。观察者创建独立用户/房间，提前建立订阅，在同一 Redis 连接先写 fixture-barrier，再对测试夹具执行 WAIT 1（与现有 prepare 一样，仅保证测试起点）。通过带 FAULT_TEST_ID、短 TTL 的 Redis ready/begin/started 键与编排脚本握手；开始连续业务探测后才停主。现有 after/rejoined 断言全部保留。

窗口内每轮直接登录并通过已建立 WebSocket 发送一条新的、带独立 client_msg_id 的消息；HTTP 单次请求及 WebSocket 回复均使用 5 秒等待，不用 health 门控。只接受业务成功或结构完整、retryable=true 的服务不可用错误，不把超时、断线、401 或任意 5xx 一律当成通过。记录样本数、响应类型、最大耗时、确认消息 ID/seq；不打印 cookie 或密码。

观察者在切换前的 begin 信号处起算 30 秒，持续探测到代理已指向 redis-2 且两种业务均成功。该时钟略早于实际停机，保持原有恢复要求；原 after 阶段仍使用 FAILOVER_STOPPED_AT 验证 30 秒。恢复后分页读取历史，所有 ack 的消息都应存在且每个 ID 恰好一条。未确认消息不增加丢失承诺。

### D3. S1 先作证明，不添加无依据的修补

当前两个标记只有 reconcile 本身访问。入口先 switchThreadCoro(app loop)，末尾也先切回同一个 loop；检查 reconcileAgain 到清除 reconciling 之间没有挂起、事件泵或会重新进入 reconcile 的回调。v1.9.13 SwitchThreadAwaiter 通过目标 loop 的 runInLoop 恢复协程，所以两步不能被另一个 reconcile 插入。

据当前源码，旧 S1 的“另一个线程恰在两步中间写标记”不成立。记录所有调用路径、挂起点和 upstream 版本证据，保留快照后重新加入等已接受风险，不改变原子标记实现，也不添加模拟不存在竞态的测试。未来若取消 loop 约束，应重新设计同步。

## Risks / Trade-offs

- 首写门闩依赖 websockets 13.1 使用 sendall/recv；固定版本、真实 TLS 回归和取消清理用例约束这一点。
- 故障采样会额外创建测试用户/消息；使用独立夹具、唯一标识及短 TTL 控制键，不清理用户真实数据。
- 新业务断言可能揭示生产缺陷；首次定位不清或需要变更生产方案时报告用户，不通过放宽断言消除失败。
- 握手拒绝 401/403 多发关闭帧及 realtime-messaging 旧 Purpose 长度警告继续保留。

## Migration Plan

构建测试镜像和 Sentinel 补丁镜像；docker compose up -d --build --wait 使用原 Sentinel 数据卷，启动时只清除历史自身条目。先跑 TLS/取消/证书及隔离 Sentinel 定向测试，再跑完整 scripts/test.sh。最后执行 strict change validate、diff check 和 verify，记录验证结果。回滚可恢复本轮测试文件和原 Sentinel 镜像配置，但原镜像仍有自引用缺陷；不删除数据卷、证书或 .env。

## D4. Sentinel 自引用根因与最小补丁（用户追加）

现场启动日志在 +sentinel-invalid-addr 后添加了与 MYID 相同的同伴。Redis 7.4.5 sentinelReceiveHelloMessages 过滤自身，但 sentinelPublishCommand 直接调用 sentinelProcessHelloMessage，后者漏检自身。容器 IP 复用后，持久 known-sentinel 的旧地址可能指回自己；连接向自己发送 HELLO，触发自发现并写入配置。

在统一 HELLO 解析入口按第三字段精确比较 MYID 并丢弃；配置加载 known-sentinel 同样忽略自身 ID，修复已持久化的自身条目。仅跳过自身，不删除其他节点、epoch、票或主节点信息，不执行 RESET。构建固定版本/校验和的 Redis 7.4.5，只有 Sentinel 服务使用补丁镜像。

新增隔离回归使用无网络容器的 loopback，不接触业务数据卷：对照原镜像复现直接 HELLO/旧地址自连接，再验证补丁对直接 HELLO、持久自身条目、三 Sentinel 地址变化与重启的处理，严格核对同伴 ID 集合与 CKQUORUM。回归接入 scripts/test.sh。原生产故障测试的 len==2、30 秒恢复和代理严格断言不变。构建与部署改动为用户追加范围；前 7 项任务的历史通过日志单独保留。

## D5. 完整回归揭示的实际 IP 切换竞态

新增 Sentinel 补丁后的首轮完整回归在原 observe_controlled_switch 断言失败：HAProxy 先接受 fqdn，watcher 立即 shutdown sessions，但 DNS 约两秒后才更新 srv_addr；新连接可以在此期间重新落到旧主，实际地址切换后仍然存活。原日志和失败完整脚本保留。

watcher 在固定受信任主机名解析后，要求 show servers state 同时匹配 fqdn 和实际 IP，才关闭全部旧会话。实际地址在 10 秒内未更新则退出，沿用既有异常退出触发容器重启的机制。保持一个后端、多数派查询、0.5 秒轮询、30 秒业务恢复及切换后 1 秒清连接断言；不改拓扑或通过延长测试时限掩盖失败。

## D6. 最终 CI 复查与故障诊断（2026-11-05）

c6dd101 的 CI #41852645569 在 Redis 故障窗口出现 WebSocket 无关闭帧断连，虽然36239e3完整CI曾成功，也不能当作当前最终验收通过。保留严格原断言并重新打开任务；恢复服务前保存故障容器日志和状态，窗口输出关闭码、底层接收异常类型与已完成采样，不输出凭据或cookie。

首次三轮定向复现在第二轮发现独立的HTTP问题：两台app返回共享Redis故障的503，Nginx的proxy_next_upstream http_503把两台暂时摘除，后续请求变为裸502。改为仅对error/timeout/http_502尝试其他实例，保留应用503的JSON及retryable语义。新增ACL临时拒绝GET的真实网关回归，finally恢复权限；修复前失败，修复后连同TLS测试8项通过。此项不等同于已证明WS断连根因，继续定向复现。

## User Scope Decision (2026-11-05)

2026-11-05用户明确决定：“那先跳过吧 不查了”。停止WS根因调查，原5.6移为用户暂缓项，不计作完成。c6dd101的CI #41852645569曾出现无关闭帧断连，根因未查明；另一次并发诊断有5秒消息超时，阶段日志出现约2秒排队后再等约2秒会话查询，未证明与断连同因。原失败记录保留cppai-sentinel-guard-ci-final-failed.log/.json。之后三轮定向、本地完整和两次原生CI成功；隔离阶段镜像五轮15个并发观察窗口及TLS-only 20,000次Ping/Pong通过，均不能替代根因证明。额外诊断准备阶段的直接采样连接重置/主从未稳而中断没有计作通过。相关/tmp证据以cppai-ws-parallel*、cppai-ws-trace*、cppai-ws-tls-ping-diagnostic.log命名。没有实施针对原断连的确定修复；原5秒/30秒/不断线/历史和旧主零连接断言保留。不要自动恢复此调查。
