# Verification Report: harden-ha-verification

日期：2026-11-05，Asia/Shanghai。按 openspec-verify-change 核对 status、apply context、proposal、design、tasks、实际源码与原始日志。

## Summary

| Dimension | Status |
|---|---|
| Completeness | 实现与本地验证任务完成；12/12当前保留范围完成；用户暂缓的原5.6未计作完成 |
| Correctness | Sentinel 原版对照、补丁隔离回归、双向受控切主、完整脚本通过 |
| Coherence | D1–D6 均有实现或源码证据；两个生产缺陷采用最小修补，原严格断言保留 |

Spec Coverage、Requirement Implementation Mapping、Scenario Coverage：**Not applicable**，status 确认 skip_specs: true。修复已有实现并增强测试，产品契约不变；没有伪造 delta，也没有把未验证项算成通过。Code Pattern Consistency 已检查：补丁构建沿用项目已有依赖源码补丁方式，测试复用原等待/采样助手，shell 失败沿用 watcher 退出触发重启的机制。

## CRITICAL

当前保留范围无。用户明确暂缓的WS调查按warning保留，不宣称已修复。

## WARNING

- WS断连根因未明，用户2026-11-05明确停止调查；不能把后续通过算作根因已解决。详见下文用户暂缓项。

- 用户明确排除的握手拒绝 401/403 后多发 close frame 问题仍存在；本轮未改 Drogon/Nginx/认证响应。
- 全仓既有 realtime-messaging Purpose 少于 50 字的格式警告保留。全仓普通校验 9/9 通过，本 change strict 通过；不声称全仓 strict 无旧警告。

Sentinel 自引用成因不明的旧 warning 已有源码、现场日志和隔离对照闭环，见 sentinel-root-cause.md。未跟踪 .DS_Store 已加入 basename 忽略规则，文件保留。

## SUGGESTION

无新增。

## 实现与实测

### Sentinel

- 原 Redis 7.4.5 在无网络、无业务卷的容器内，直接自身 HELLO、旧同伴地址回环均复现自身同伴。订阅路径过滤自身，直接 PUBLISH 所用公共入口漏检，证据与现场启动两秒后 +sentinel 自身 ID 对应。
- 公共 HELLO 入口精确比较第三字段 MYID。配置完整加载后仅从各 master 同伴字典删除自身 ID并持久化，保留其他身份、current-epoch、leader-epoch、master config-epoch；不 RESET。
- 固定版本、SHA-256 构建仅 Sentinel 使用的镜像，保留 TLS。普通 Redis 节点与 C++ 源码未改。
- 隔离回归验证自身/外部 HELLO、脏配置、三台发现、CKQUORUM、监听地址轮换重启及 epoch/票保留。定向：cppai-sentinel-guard-targeted-stable.log；完整脚本再次实际执行通过。

### HAProxy 实际地址更新

- 第一轮完整脚本的原 observe_controlled_switch 失败，发现 FQDN 接受早于 srv_addr 更新；先 shutdown sessions 会让重连在 DNS 等待期重新落到旧主。
- watcher 等待固定主机名的实际 IP与 FQDN 同时匹配后才清连接，10 秒内未解析则退出，沿用既有异常退出重启机制。没有更改拓扑、单后端、多数派查询或原断言。
- 定向双向真实切主：旧连接在实际切换后 1 秒内排空，并持续零连接，分别 90/94 次采样。cppai-sentinel-guard-proxy-switch-targeted.log/.exit（0）。
- 新 controlled 阶段已单独通过，且完整脚本先到 redis-2，再让原 prepare 切回 redis-1；新建 CI 环境也会覆盖两个方向，不再因默认 redis-1 已是主而跳过受控切换。

### Nginx 503 与故障日志

Nginx原proxy_next_upstream http_503会将共享Redis故障的应用503计成实例失败，两台被暂时摘除后返回裸502。已移除http_503，保留error/timeout/http_502，原样返回应用可重试JSON。ACL拒绝GET的真实网关回归修复前失败，修复后连同TLS共8项通过，finally恢复权限。恢复前保存日志已接入scripts/test.sh，窗口断连保留关闭码和接收异常诊断。此修复没有被当作WS根因已证明。

### TLS 与对账

- sync_tls.py 为固定 websockets 13.1 增加每连接首次 sendall 后读的门闩；close/shutdown 和发送失败释放，关闭标记避免落回原始 socket。无协议降级、禁用校验、sleep 或测试重试。
- TLS 1.2/1.3 各 20 次真实握手/订阅、默认协商 1.3、未知 CA/错误主机名拒绝、写前 close/shutdown，以及原异步协议用例均有测试。此前定向10项通过，最终完整首轮再次包含这些回归。
- reconcile-audit.md 已逐项核对全部标记访问、入口/尾部 switchThreadCoro、精确 Drogon v1.9.13 runInLoop 实现。尾部两步在同一 app loop且之间无挂起，旧 S1 前提不成立；没有虚构“修复竞态”，未改 C++ 同步代码。

### Redis 故障业务窗口

- ready/begin/started 屏障保证观察者实际在停主前开始；直接登录和发消息，不以 health 成功门控。
- 每请求 5 秒、恢复 30 秒；仅接受成功或结构化 retryable 错误，按 client_msg_id 关联回包，确认消息历史各一条。原 after/rejoined、停应用/L7、撤权及限流断言保留。
- 登录6次：4成功、2可重试，最长2.913秒；消息6次：5ack、1可重试，最长2.009秒。9.421秒恢复，5条确认消息各保存一次；旧主重加入53次采样，代理连接始终为0。

## 完整回归与恢复

- 日志：/private/tmp/cppai-ci-ws-fixed-full.log/.exit，**exit 0**。
- 首轮255 passed / 26 skipped；全部AI、受控切主、Redis停主/重加入、app/L7故障、长周期撤权和原严格9项通过。日志：/private/tmp/cppai-ci-ws-fixed-full.log/.exit（0）。
- 13 个受测实现/配置文件与开跑前哈希一致：cppai-ci-ws-fixed-full-source-hashes.json。
- 13个常驻服务healthy，正常cppaiserver-app镜像已恢复；13个受测文件与cppai-ci-ws-fixed-full-source-hashes.json完全匹配。临时诊断只使用/tmp源码副本和独立镜像，没有修改项目C++源码。
- 使用测试 fake providers；未新增真实模型费用测试、可选压力模式或生产服务器验收。
- strict change validate、全仓普通 validate、Python/shell 语法与 diff check 通过；data/.DS_Store 被忽略。

## 失败记录（保留）

- 旧 TLS/部署就绪失败与此前7项任务最终通过的 cppai-hardening-* 日志保留，先前完整成功为 cppai-hardening-complete-full.log/.exit（0）。此前一次 Sentinel RESET 是历史操作，本次定位与修复没有再次 RESET。
- Sentinel 隔离工具：初版 SHUTDOWN 无响应的 JSON 解析失败；初版补丁普通 C 字符串用作 SDS 键导致脏配置测试失败；初版回归未等待地址切换连接稳定。各日志及修正见 sentinel-root-cause.md，最终回归严格检查自身 ID、稳定集合和选举状态。
- cppai-sentinel-guard-full.log/.exit（1）：首轮254通过/25跳过、AI故障阶段通过后，原受控切换因旧主重连失败。代理与三 Sentinel 日志保留 cppai-sentinel-guard-full-failure-proxy-sentinel.log，容器快照 cppai-sentinel-guard-full-failure-containers.json。修复实际地址等待，再从头执行完整脚本；未放宽原断言。

## 用户暂缓的未查明项

2026-11-05用户明确决定：“那先跳过吧 不查了”。停止WS根因调查，原5.6移为用户暂缓项，不计作完成。c6dd101的CI #41852645569曾出现无关闭帧断连，根因未查明；另一次并发诊断有5秒消息超时，阶段日志出现约2秒排队后再等约2秒会话查询，未证明与断连同因。原失败记录保留cppai-sentinel-guard-ci-final-failed.log/.json。之后三轮定向、本地完整和两次原生CI成功；隔离阶段镜像五轮15个并发观察窗口及TLS-only 20,000次Ping/Pong通过，均不能替代根因证明。额外诊断准备阶段的直接采样连接重置/主从未稳而中断没有计作通过。相关/tmp证据以cppai-ws-parallel*、cppai-ws-trace*、cppai-ws-tls-ping-diagnostic.log命名。没有实施针对原断连的确定修复；原5秒/30秒/不断线/历史和旧主零连接断言保留。不要自动恢复此调查。

## 远程交付

PR #10：https://github.com/anwalld/CppAiWebServer/pull/10 。功能实现提交e0cad511a453738df0f1078d72e8449b11552286的两次原生完整CI成功：https://github.com/anwalld/CppAiWebServer/actions/runs/41958390082 。证据为本机cppai-ci-e0cad51-result.json + cppai-ci-e0cad51.log与cppai-ci-e0cad51-attempt2-result.json + cppai-ci-e0cad51-attempt2.log；首次窗口6.781秒恢复、6条确认消息各一次、旧主重加入52次采样零代理连接。本交接收尾仅改文档，最新文档提交以PR Checks为准。未合并或归档。

## Final Assessment

当前保留范围12/12，0 critical、3项已记录warnings（WS未查明、握手拒绝关闭帧、主规格Purpose格式），0新增suggestions。适用检查完成；规格相关检查Not applicable。WS调查按用户决定暂缓，不自动恢复。让他double check。
