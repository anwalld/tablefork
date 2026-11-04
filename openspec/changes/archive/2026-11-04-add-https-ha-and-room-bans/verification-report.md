# Verification Report: add-https-ha-and-room-bans

验证日期：2026-11-04，Asia/Shanghai。按 `.agents/skills/openspec-verify-change/SKILL.md` 核对 status、apply context、proposal、四份 delta、design、tasks、当前源码及实测日志。以 CC 最新交接和用户确认的来源隔离、watcher 异常退出恢复决定为准。

## Summary

| Dimension | Status |
|---|---|
| Completeness | 33/33 tasks；16/16 ADDED/MODIFIED requirements 找到实现路径，64 个正式场景已对照。2.4、2.8 已在全新 checkout 上按 README 验收通过（见“全新环境浏览器验收”）；6.2 已由本次远程 CI 验证通过，详见收尾记录 |
| Correctness | C++、前端 76 项与构建通过；最新完整回归 exit 0，含 247 项首轮、受控切换断连接、旧主重加入采样、独立来源长周期撤权、严格限流。W2 的切换窗口业务断言部分 Not verified；Chrome 证书信任、双账号收发和踢人/封禁/解封/再邀请通过 |
| Coherence | D7 单一 Sentinel 路由、D9a 定期对账、D12 保留本地消息/ack 替换与代码一致；已同步风险、三个新增场景及 3.8/4.6。现有目录、命名和测试模式核查通过 |

14 条 ADDED、2 条 MODIFIED，无 REMOVED/RENAMED。CLI 规划 artifact 完整不等于全部实施验收完成。

## CRITICAL

无未完成的正式任务。2.4、2.8 已由 Claude 的全新环境浏览器验收补齐；用户随后明确授权提交、推送、PR、CI，通过后合并 main 再归档。6.2 已由 [PR #8](https://github.com/anwalld/CppAiWebServer/pull/8) 的本次 [CI](https://github.com/anwalld/CppAiWebServer/actions/runs/41808271464) 验证，33/33。

原有 W2/W3 与 S1/S2 继续保留，不将归档当成这些问题已经解决。

## WARNING

### W2：Redis 切换窗口内的业务错误响应尚未直接断言

`test_failover.py::test_redis_primary_failure_recovers_without_restarts` 在恢复前轮询 health，登录、消息、AI、登出断言在恢复后执行。真实登录/发送在切换窗口的成功或有界 retryable 错误仍部分 **Not verified**。

CC 明确本轮不做此项，保留待用户决定；后续补直接业务断言，不用 health 代替，也不放宽 30 秒恢复要求。

### W3：同步 TLS 1.3 的已知测试范围限制保留

`conftest.py` 同步 Socket 仍限定 TLS 1.2；生产 TLS 1.2/1.3 均开启，`test_gateway.py::test_wss_protocols_exchange_messages` 有分别强制两种版本的异步 WSS 测试。同步 TLS 1.3 先前超时根因未消除，不能把同步整套结果当作 TLS 1.3 全覆盖。

按交接保留现状，不改协议或证书验证。

W2、W3 已按用户要求写进 `design.md` 的 Risks 和 `tasks.md` 的 Workflow follow-up，作为本次 change 不处理的遗留问题。

## SUGGESTION

### S1：对账的“再跑一轮”标记有一个窄竞态

`src/ws/room_hub.cc` 的 `RoomHub::reconcile`：本轮读到 `reconcileAgain_` 为 false 之后、把 `reconciling_` 清为 false 之前，如果有新的对账请求到达，它会看到 `reconciling_` 仍为 true，只设置 `reconcileAgain_` 就返回了，而这个标记已经没人再检查。最坏情况是推迟到下一个检查周期，属于已接受的撤权延迟范围。

建议：清除 `reconciling_` 之后再检查一次 `reconcileAgain_`，有就重新进入循环。已写进 design 的 Risks 和 tasks 的 Workflow follow-up。

### S2：WebSocket 握手被拒（401）时，Drogon 响应的 Content-Length 与实际长度不符

全新环境验收中，浏览器带着一个已失效的会话 cookie 连接 `/ws`，应用返回 401，L7 两次记录了 nginx 警告 `upstream sent more data than specified in "Content-Length" header while reading upstream`，与这两次 401 一一对应（日志 `/private/tmp/claude-501/-Users-mima0000-CLionProjects-CppAiServer/0ad4b137-783c-4944-9e5e-0dacf4c95cbd/scratchpad/fresh-web.log`）。功能上没有影响：客户端收到 401 后回到登录页。但这说明拒绝 WebSocket 握手时，应用发出的响应体长度和声明的不一致。nginx 会因此丢弃这条上游连接，在 keepalive 的连接池里则可能造成后续响应错位。

建议：另开一项检查 `src/ws/chat_socket.cc` 拒绝握手的路径，确认响应头和响应体一致，并补一个用失效 cookie 连接 `/ws` 的回归测试，断言 L7 日志里不再出现这条警告。本次 change 没有处理，已写进 tasks 的 Workflow follow-up。

## 新旧问题结论

- 原先每条推送查询 MySQL 的方案已删除。`RoomHub::publish` 直接 PUBLISH，原有私人通知的目标名单处理保留；正常 member_left 即时移除，订阅回调 queueInLoop 死锁修复保留。
- 新 `reconcile` 在锁内取去重快照，每 500 对顺序参数化查询；查 bans 判原因，回应用循环本地移除；原子标记合并重入。共用会话定时器并随机首次偏移，Redis 发布/会话检查异常置脏，每秒用 PUBLISH 探测并广播恢复。相关周期/故障/片段顺序测试通过。
- 原双后端只验 role:master 时曾出现约 11 秒双主路由；两个独立 Sentinel 检查同样不能原子更新 UP/DOWN。现为一个 `master` 条目，由 watcher 根据至少 2/3 Sentinel 指定切换 FQDN，先改地址后 shutdown sessions。健康检查只验证当前目标存活且是 master。
- 起始没有多数派时等待；运行中失去多数时保留路由，当前目标不可用则健康检查使其 DOWN。没有实施已废弃的 external-check、密码文件或双后端方案。
- 用户明确确认：watcher 异常退出时终止 HAProxy 主进程，触发 `restart: unless-stopped`；单纯 unhealthy 不触发 Docker 重启。手动终止 watcher 后已实测 RestartCount 增加且路由恢复。
- 用户接受改地址与断旧连接之间的短暂旧写窗口及代理单点；不声称任意时刻都没有在途旧连接写入，不部署第二个代理，也不新增强一致复制承诺。
- 原第二轮 429、后一次 Redis prepare 503 的失败记录保留；最新完整脚本通过后才能关闭整体回归阻塞。没有将过去的推断改写成已直接捕获某条 Redis READONLY 命令。

## Requirement Implementation Mapping

所有路径相对于项目根目录；测试均在 tests/integration，除特别说明。

| Requirement | 实现与主要证据 |
|---|---|
| chat-rooms / 邀请用户 | RoomService invite/join 的房间锁与 bans 检查；test_rooms、test_room_bans 各角色邀请检查 |
| chat-rooms / 踢出成员 | canManage/kick、RoomHub removeMember/reconcile；权限、全连接 reason、拒发、历史/再邀请、短/长周期丢失通知、ack 后跨实例踢人 |
| chat-rooms / 封禁用户 | ban 同事务删除成员/邀请；成员/房间外用户、幂等、并发邀请、banned 原因、周期与恢复触发 |
| chat-rooms / 解除封禁 | unban；幂等、权限、不自动恢复成员或邀请 |
| chat-rooms / 查看封禁列表 | bans 只读事务不再 lockRoom；V4 FK、排序、执行者退出姓名/账号删除 null |
| edge-gateway / HTTPS | L7 TLS 1.2/1.3，test_gateway 协议与 HTTPS/WSS 全套；当前 checkout 的 Chrome 登录/双向聊天通过；同步客户端范围见 W3 |
| edge-gateway / HTTP 301 | 80 仅跳转；路径/查询保持、HTTP 登录不改变失败计数、明文 WS 拒绝 |
| edge-gateway / Secure cookie | config/Compose 默认值；cookie Secure/HttpOnly、HTTPS 会话套件 |
| edge-gateway / 本地证书 | gen-certs/一次性 certs/忽略规则；CA/SAN、既有首次/续签证据，本轮沿用证书；Chrome 明确显示连接安全，全新 checkout 验收已由 Claude 补齐，详见后文 |
| edge-gateway / 真实 IP | PROXY protocol、real_ip、覆盖头；严格认证伪造头/新连接配额 |
| edge-gateway / 连接限制 | gateway limit_conn；满额拒绝、保留现有连接、释放配额、第二来源隔离 |
| high-availability / 多实例 | 双 app、共享会话与推送；固定双向实例、退出/登出/踢人、丢失通知恢复 |
| high-availability / app 故障 | upstream failover、客户端重连/seq；实际 app-1 停机、会话保留、补齐/实时消息 |
| high-availability / L7 故障 | 双 L7 的 L4 分发；实际 web-1 停机后多次新 HTTPS 请求 |
| high-availability / Redis 主切换 | Sentinel 多数派单一后端、AUTH/PING/INFO 检查、watcher 与 PID 健康；实际三阶段、受控切换后 1 秒撤连接、旧主重加入零代理连接、消息保留、AI/登出；切换窗口业务断言见 W2 |
| user-auth / 来源限流 | 按 IP 固定 L7 与 limit_req 429 JSON；不同 IP、伪造头、多新连接、非认证接口与 refresh |

## Scenario Coverage


### chat-rooms / 邀请用户

- `specs/chat-rooms/spec.md:8` 邀请成功：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:12` 被邀请者不存在：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:16` 被邀请者已是成员：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:20` 非成员邀请：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:24` 被邀请者已被封禁：源码及上表对应场景相关测试已核对。

### chat-rooms / 踢出成员

- `specs/chat-rooms/spec.md:33` 群主踢出管理员：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:37` 管理员踢出普通成员：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:41` 被踢者的在线连接：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:45` 移出通知丢失：短周期及独立来源的 30 秒周期用例通过；恢复后 5 秒内 room_left。
- `specs/chat-rooms/spec.md:49` 被踢者发送消息：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:53` 被踢后重新邀请：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:57` 权限不足：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:61` 踢出群主：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:65` 踢出自己：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:69` 踢出 AI 或非成员：源码及上表对应场景相关测试已核对。

### chat-rooms / 封禁用户

- `specs/chat-rooms/spec.md:76` 封禁成员：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:80` 封禁被邀请者：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:84` 封禁不在房间里的用户：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:88` 被封禁者加入：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:92` 重复封禁：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:96` 权限不足：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:100` 封禁自己或群主：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:104` 用户不存在：源码及上表对应场景相关测试已核对。

### chat-rooms / 解除封禁

- `specs/chat-rooms/spec.md:111` 解封后邀请：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:115` 解封未被封禁的用户：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:119` 普通成员解封：源码及上表对应场景相关测试已核对。

### chat-rooms / 查看封禁列表

- `specs/chat-rooms/spec.md:126` 管理员查看：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:130` 普通成员查看：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:134` 执行者已离开：源码及上表对应场景相关测试已核对。
- `specs/chat-rooms/spec.md:138` 执行者账号已删除：源码及上表对应场景相关测试已核对。

### edge-gateway / 只通过 HTTPS 访问

- `specs/edge-gateway/spec.md:12` 通过 HTTPS 登录：源码及上表对应场景相关测试已核对。
- `specs/edge-gateway/spec.md:16` 通过 WSS 建立连接：源码及上表对应场景相关测试已核对。
- `specs/edge-gateway/spec.md:20` 旧协议：源码及上表对应场景相关测试已核对。

### edge-gateway / HTTP 跳转到 HTTPS

- `specs/edge-gateway/spec.md:27` 打开 HTTP 页面：源码及上表对应场景相关测试已核对。
- `specs/edge-gateway/spec.md:31` 通过 HTTP 提交登录：源码及上表对应场景相关测试已核对。
- `specs/edge-gateway/spec.md:35` 明文 WebSocket：源码及上表对应场景相关测试已核对。

### edge-gateway / cookie 只经 HTTPS 发送

- `specs/edge-gateway/spec.md:42` 登录后的 cookie：源码及上表对应场景相关测试已核对。

### edge-gateway / 本地证书

- `specs/edge-gateway/spec.md:49` 首次启动：当前源码及既有证据；本轮沿用证书，未新建干净 checkout。
- `specs/edge-gateway/spec.md:53` 再次启动：当前源码及既有证据；本轮沿用证书，未新建干净 checkout。
- `specs/edge-gateway/spec.md:57` 信任本地 CA 的客户端：自动 CA 校验通过；本轮用户信任当前 CA 后，Chrome 明确显示“连接是安全的”，登录和双向收发通过。
- `specs/edge-gateway/spec.md:61` 版本库：当前源码及既有证据；本轮沿用证书，未新建干净 checkout。

### edge-gateway / 识别真实客户端地址

- `specs/edge-gateway/spec.md:68` 经过多层接入：源码及上表对应场景相关测试已核对。
- `specs/edge-gateway/spec.md:72` 伪造转发头：源码及上表对应场景相关测试已核对。

### edge-gateway / 按客户端地址限制并发连接数

- `specs/edge-gateway/spec.md:79` 超出上限：源码及上表对应场景相关测试已核对。
- `specs/edge-gateway/spec.md:83` 连接关闭后：源码及上表对应场景相关测试已核对。
- `specs/edge-gateway/spec.md:87` 其他地址不受影响：源码及上表对应场景相关测试已核对。

### high-availability / 聊天服务多实例

- `specs/high-availability/spec.md:12` 会话在其他实例上有效：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:16` 跨实例推送：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:20` 跨实例断开会话：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:24` 跨实例移出房间：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:28` 跨实例移出通知丢失：短周期及独立来源的 30 秒周期用例通过；恢复后 5 秒内 room_left。

### high-availability / 聊天服务实例故障

- `specs/high-availability/spec.md:35` 实例停止后的新请求：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:39` 实例停止后重连：源码及上表对应场景相关测试已核对。

### high-availability / 接入实例故障

- `specs/high-availability/spec.md:46` 一个接入实例停止：源码及上表对应场景相关测试已核对。

### high-availability / Redis 主节点故障自动切换

- `specs/high-availability/spec.md:53` 主节点停止：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:57` 切换期间的请求：部分 Not verified，见 W2。
- `specs/high-availability/spec.md:61` 已确认的消息：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:65` 切换后断开会话：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:69` 原主节点恢复：源码及上表对应场景相关测试已核对。
- `specs/high-availability/spec.md:73` 切换后不再写入原主节点：受控切换后 1 秒内撤连接、旧主重加入零代理连接采样通过。

### user-auth / 认证请求按来源限流

- `specs/user-auth/spec.md:8` 同一 IP 频繁请求：源码及上表对应场景相关测试已核对。
- `specs/user-auth/spec.md:12` 其他接口不受影响：源码及上表对应场景相关测试已核对。
- `specs/user-auth/spec.md:16` 多个新连接：源码及上表对应场景相关测试已核对。
- `specs/user-auth/spec.md:20` 不同 IP：源码及上表对应场景相关测试已核对。

“相关证据已核对”表示源码和对应需求的测试映射；不将未执行的浏览器动作当作通过，也不覆盖 W2/W3 的限制。

## 前端与代码模式核查

- 移出状态只存内存，保留房间/已加载消息、停订阅及跟踪、禁用输入、关成员面板；重新点开只读本地，不请求成员/历史/封禁接口。列表轮询保留标记，同 id 再次出现在服务端时以服务端为准。left/缺省 reason 原行为保留。
- ack 使用现有 seq/created_at 加待发送项拼 local 消息；receive/addPage/fetchMissing 三条路径允许正式版本替换 local，非 local 不被重复覆盖。被移出后的缺口不请求历史。服务端 chat_socket.cc/ack 格式未改。
- AI 草稿只处理累积快照；用户确认后统一相关模拟数据，保留生命周期/重连/乱序快照测试，删除指定非累积专用测试。20 次回复的 sequence 严格递增和最后 snapshot 等于最终内容均在首轮验证。
- 延续 service/controller/ws、Flyway、pytest、Vue/Vitest 模式；全局对账订阅回调离开 Drogon 锁再处理。HAProxy admin 仅回环 9998，只读状态端口仅 Docker 内网 9999，均未发布。密码不进入命令行、文件或日志。

## 本轮浏览器验收（2026-11-04，Asia/Shanghai）

用户确认已处理证书后，使用本机 Google Chrome 正常窗口（房主）和无痕窗口（成员），通过 `https://localhost:8443` 操作。测试房间为 `浏览器验收-20261104`，房主 `ud9fcf0e9e989`，成员 `uceb75a32c962`。仅使用专门创建的测试账号和测试消息，未使用用户账号或添加 AI。

| 操作 | 实际可见结果 |
|---|---|
| HTTPS/证书 | 正常显示登录页；网站信息明确显示“连接是安全的”；没有绕过警告或关闭校验 |
| 登录、邀请、加入及双向聊天 | 两账号均登录；成员从“待加入”加入；16:52 房主消息在成员可见，16:55 成员回复在房主可见 |
| 房主踢人 | 二次确认显示目标用户名；成员显示“已被踢出”和“你已被踢出房间 浏览器验收-20261104”；房间及已加载消息保留，输入框/发送按钮 disabled |
| 踢出后再邀请 | 标记变为“待加入”；重新加入后恢复输入，16:58 发送成功并在房主可见 |
| 普通成员面板 | 可见两名成员；无踢人、封禁、封禁列表管理按钮 |
| 房主封禁 | 二次确认显示目标用户名；成员显示“已被封禁”和“你已被禁止进入房间 浏览器验收-20261104”；三条旧消息保留，输入/发送 disabled |
| 封禁期间邀请 | 房主收到“该用户已被此房间封禁，需要先解封” |
| 封禁列表 | 显示被封用户名、执行者 `ud9fcf0e9e989`、时间 `2026/11/4 16:59:34` 和解封按钮 |
| 解封 | 房主显示“已解除对 … 的封禁”和“暂无封禁记录”；成员没有自动恢复成员资格，仍为只读状态 |
| 解封后再邀请 | 成员重新出现“待加入”，加入后恢复输入；17:02 的“解封并重新邀请后：聊天恢复成功”在双方可见 |

证据为本轮 CUA 的实际 AX 状态和踢出后截图（保存在对话工具记录中，未另存截图文件）。没有通过 DevTools 捕获网络请求；“不再请求成员/历史接口”和 ack/local 的极端时序仍以现有源码及前端测试为证据，不声称本次手工重演了这些竞争条件。

本轮没有改实现、配置、证书或用户 `.env`；仅回填任务 5.6，并更新验证/交接文档。复查 23 个已测实现/配置/测试文件的 SHA-256 均与完整回归快照一致；C++、前端测试/构建和完整回归 `.exit` 均仍为 0，未重复运行整套回归。13 个常驻服务均 running/healthy。没有新增真实模型/计费、压测或生产服务器部署验收。

测试账号和房间保留，结束时两人均已加入，封禁列表为空，便于 Claude 复核；不执行全局清理或永久删账号。

## Evidence

| 验证 | 结果 | 日志 |
|---|---|---|
| C++ 单元与 1000 次固定种子首次偏移 | exit 0 | `/private/tmp/cppai-reconcile-unit.log`、`.exit` |
| Vue/消息流/草稿/Socket | 13 文件、76 passed；构建 exit 0 | `/private/tmp/cppai-reconcile-web-test.log`、`cppai-reconcile-web-build.log` 及 `.exit` |
| 单一后端手动演示 | 多数丢失保路由、恢复、watcher 退出触发重启、主停机后切换均通过，exit 0 | `/private/tmp/cppai-single-master-manual.log`、`.exit` |
| 手动切换时间 | 停主到新路由 UP 6.77 秒；Sentinel 确认后约 0.4 秒记录切换 | `/private/tmp/cppai-single-master-switch-timing.log` |
| 最新完整脚本 | exit 0；首轮 247 passed / 24 skipped；长周期 kick/ban 各 1 passed，原严格轮次 9 passed；全部故障阶段通过 | `/private/tmp/cppai-sentinel-check-full.log`、`.exit` |
| 源码快照 | 23 个实现/配置/测试文件哈希与当前一致 | `/private/tmp/cppai-sentinel-check-source-hashes.json` |
| Chrome 双账号实操 | HTTPS、收发、踢人、封禁、解封、重新邀请通过；5.6 完成 | 本轮 CUA 工具记录；详见上节 |
| strict validate / diff --check | 通过 | 本次命令结果 |

本轮在现有项目上执行完整脚本，没有新建 checkout 或删除用户数据卷；6.1 的全新环境证据来自上一轮。AI 为 fake providers，没有新增真实模型/费用验证或压测。24 skipped 含阶段/配置条件，不声称所有可选压力模式都运行过。

旧失败证据：`cppai-reconcile-before-isolation-full.log/.exit` 为第二轮 429；`cppai-reconcile-full.log/.exit` 为 Redis prepare 503；`cppai-reconcile-503-services.log`、`cppai-reconcile-503-redis.log` 为只读调查。它们不替代本轮结果。

阶段摘要：

```text
247 passed, 24 skipped in 330.32s (0:05:30)
1 passed, 4 skipped in 0.26s
1 passed, 4 skipped in 0.09s
1 passed, 4 skipped in 6.09s
1 passed, 4 skipped in 2.17s
1 passed, 4 skipped in 3.03s
controlled switch: old proxy connections drained within 1s and stayed at zero
baseline primary: redis-1
1 passed, 4 skipped in 16.59s
sprimary changed redis-1 -> redis-2, ready in 8.97s
1 passed, 4 skipped in 17.82s
old primary rejoin: 52 samples, zero proxy connections
redis-1 rejoined as replica; proxy still targets redis-2
1 passed, 4 skipped in 15.76s
1 passed, 4 skipped in 0.46s
1 passed, 4 skipped in 0.15s
1 passed in 0.12s
1 passed in 0.53s
1 passed in 1.23s
1 passed in 0.99s
1 passed in 27.31s
9 passed in 24.23s
```

## 全新环境浏览器验收（2026-11-04，Claude）

用于勾选 2.4 和 2.8。只修改了文档，没有改实现。

**准备**
- 用 `git ls-files -co --exclude-standard` 把当前分支已跟踪的文件加未跟踪的非忽略文件复制到临时目录，得到一份全新的 checkout：没有 `.env`，没有 `data/certs/`，没有任何数据卷。
- 按 README：`cp .env.example .env`，只把 6 个 `change-me` 换成随机值（5 个 `openssl rand -hex 24` 等长随机串，`AI_KEY_ENCRYPTION_KEY` 用 32 字节 base64）。用 diff 确认除这 6 行外与 `.env.example` 完全相同，所以 `ALLOWED_ORIGINS`、`COOKIE_SECURE`、端口和网段都是默认值。
- 默认网段 `172.28.0.0/16` 与主项目冲突，所以先对主项目执行 `docker compose down`（保留 8 个数据卷），验收后再 `docker compose up -d --wait` 恢复。

**启动**
- 按 README 执行 `docker compose up -d --build --wait`，退出码 0，13 个服务都健康。
- `certs` 生成了新 CA（`CN=CppAiServer Local CA`，SHA-256 指纹 `88:11:85:5C:...:F8:50`），与主项目的 CA 不同。
- `curl --cacert data/certs/ca.crt https://localhost:8443/health` 返回 `{"mysql":"ok","redis":"ok"}`；`http://localhost:8080/x?y=1` 返回 301，跳到 `https://localhost:8443/x?y=1`。

**浏览器**（Claude 桌面应用的内置浏览器）
1. **导入 CA 之前**：打开 `https://localhost:8443` 报 `ERR_CERT_AUTHORITY_INVALID`，证明这个 CA 确实是新生成的、此前未被信任。
2. **用户按 README 的步骤**，把新 CA 导入“登录”钥匙串并设为“始终信任”。这一步由用户本人完成，Claude 没有修改系统信任设置。
3. **导入之后**：页面直接打开，没有证书警告；页面内 `window.isSecureContext` 为 `true`，协议为 `https:`。
4. 注册新测试账号并自动登录，创建房间“全新环境验收”。
5. 再开一个标签页，沿用同一会话自动进入同一房间。标签页二发的消息实时出现在标签页一，标签页一的回复也实时出现在标签页二，两边显示一致。
6. 登录响应的 `chat_session` 和 `chat_device` 两个 cookie 都带 `Secure` 和 `HttpOnly`。
7. 实时连接走的是 `wss`：L7 记录了 `"GET /ws HTTP/1.1" 101`，而 L7 只有 443 端口转发 `/ws`，80 端口一律返回 301。

**清理**
- 临时环境执行 `docker compose down -v`，容器、网络和数据卷都已删除；临时 checkout（含 `.env` 和私钥）和测试账号文件也已删除。
- 主项目已恢复，13 个服务健康，`/health` 正常。
- 用户可以在钥匙串里删除指纹为 `88:11:85:5C:...:F8:50` 的那个临时 CA。

**边界**
- 这次验收用的是单个账号的两个标签页，验证的是“登录 + 实时收发”。多账号的踢人、封禁、解封、重新邀请沿用本报告前文的浏览器验收（5.6）。
- 浏览器的网络面板没有列出 WebSocket 请求，`wss` 的结论来自 L7 日志，不是浏览器面板。

## CI、PR 与归档收尾（2026-11-04，Codex）

- 功能提交：`dbecc810dd47758ad957e3f5f2c7436f7706900f`；[CI run 41808271464](https://github.com/anwalld/CppAiWebServer/actions/runs/41808271464) 为 success，测试使用临时密码和正常 CA 校验。
- 与最近成功的 main CI（run 41630969602）比较，没有新增弃用警告，也没有弃用类 check annotation；原有依赖警告不冒称已消除。
- [PR #8](https://github.com/anwalld/CppAiWebServer/pull/8) 已按用户明确授权合并到 main，合并提交 `077e21417e829f64799815bfe1bd674b12ea6ec7`。
- 6.2 按 CLI 返回的任务原文和行号回填，刷新后为 **33/33**。
- 后续按 `openspec-archive-change` 内联执行规格同步：新增 edge-gateway、high-availability，更新 chat-rooms 与 user-auth；保留原有 Purpose 和无关需求/场景。归档目录为 `openspec/changes/archive/2026-11-04-add-https-ha-and-room-bans/`。归档文档通过独立 PR 交付。
- 本轮没有修改实现、TLS 配置或测试断言，没有关闭校验、跳过测试或重新调用真实模型。CI 日志和摘要存于 `/private/tmp/cppai-ci-41808271464.log`、`.json`。

收尾校验：四份主规格均与 delta 一致；原有标题/Purpose 与无关需求场景保留；本 change 和四份主规格的 strict validate 通过，全规格普通校验 8/8 通过，git diff --check 通过，活动 change 列表为空，归档文件（含 .openspec.yaml）移动前后的 SHA-256 逐项相同。

基线格式警告：额外执行全规格 strict 时，已有 realtime-messaging 的 Purpose 为 45 字，触发少于 50 字警告（7/8 strict 通过）；已核对与合并后的 main 完全相同。本次没有改它或隐藏该结果；标准全规格校验与本次四份规格严格校验均通过。后续可另行润色 Purpose。

## Final Assessment

**0 critical issues；2 warnings（W2、W3）；2 suggestions（S1、S2）。33/33 正式任务完成，按用户指令保留遗留问题并归档。**

适用检查中未验证的范围仍为 W2 的切换窗口内直接业务响应、W3 的同步客户端 TLS 1.3 覆盖；这些限制和 S1/S2 已写入 Workflow follow-up，不声称全部场景已经直接实测。2.4、2.8 的全新环境验收及 6.2 本次远程 CI 均已有证据。前文未提交/未推送的表述描述的是此前 verify 阶段，当前收尾状态以本节为准。
