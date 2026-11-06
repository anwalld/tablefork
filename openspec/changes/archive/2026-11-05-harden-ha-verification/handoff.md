# 给 Claude 的交接：harden-ha-verification

> 历史阶段记录：保留本阶段的提交、失败与验收范围，不代表当前部署或剩余任务。最新结果见[当前交接](../../../../docs/handoff.md)，未完成需求见[后续计划](../../../../docs/roadmap.md)。历史操作限制不替代用户后续明确指令。

> 最新交付状态：用户随后明确要求合并并归档，PR #10已合并。归档后的状态见同目录 archive-completion.md；下文的“未合并或归档”是此前交接时的历史状态。

日期：2026-11-05。**让他double check。**

## 范围与状态

分支 codex/harden-ha-verification，基于 main 809a34a7aefe0317d9f23fc216c9926a203bdf80。上一 change 的 PR #8/#9 已合并，本次是新的 PR。
用户要求：修 Sentinel 自引用与 .DS_Store，连同此前 TLS1.3、切换窗口、对账复核一起 verify、CI、PR。握手拒绝后多发 close frame 仍按用户决定保留。

当前保留任务12/12；用户已明确暂缓WS调查，未计为完成。当前实现本地完整及两次原生CI成功。正常服务已恢复。

## 改了什么

1. docker/sentinel/Dockerfile、ignore-self.patch：Redis7.4.5 固定源码/SHA 构建；公共 HELLO 入口拒绝自身，配置加载后仅删各 master 的自身同伴条目，保留 MYID/其他同伴/epoch/票。docker-compose.yml 仅三个 Sentinel 改用此镜像。无需清卷或 RESET。
2. scripts/test_sentinel.py：无网络、无业务数据卷的真实 Redis 隔离回归，覆盖直接 HELLO、旧地址回环、脏配置、三台发现/仲裁、地址轮换重启。原版对照能复现，补丁通过。
3. scripts/redis-master-watch.sh：完整回归新发现 FQDN 接受与实际 IP 更新之间的竞态；等待实际 IP 和 FQDN 同时匹配再清会话，解析超时退出沿用原重启机制。不是修改测试放行。
4. scripts/test.sh、test_failover.py：增加 controlled 阶段让全新CI也经历双向受控切主；原 prepare/after/rejoined 和 1秒清连接、零残留、30秒要求保留。故障前 ready/begin/started 启动业务窗口，直接登录/发消息并按 ID核对 ack 历史。
5. sync_tls.py、test_sync_tls.py、conftest.py：首次 sendall 后再 recv，释放失败/关闭等待；移除TLS1.2上限，强制TLS1.2/1.3重复握手和证书/取消回归。证书与主机名校验未关闭，websockets仍13.1。
6. .gitignore 增加 .DS_Store；保留磁盘上的原文件。新OpenSpec change明确 skip_specs: true，产品契约不变。没有改 C++ src、普通Redis拓扑、Drogon版本、.env或证书。

8. Nginx原proxy_next_upstream http_503会将共享Redis故障的应用503计成实例失败，两台被暂时摘除后返回裸502。已移除http_503，保留error/timeout/http_502，原样返回应用可重试JSON。ACL拒绝GET的真实网关回归修复前失败，修复后连同TLS共8项通过，finally恢复权限。恢复前保存日志已接入scripts/test.sh，窗口断连保留关闭码和接收异常诊断。此修复没有被当作WS根因已证明。

## 对账复核

旧S1尾部竞态在当前源码下不成立。全部标记访问先切回同一app loop；末尾条件检查到清reconciling之间无await/事件泵。依赖Drogon v1.9.13的SwitchThreadAwaiter确实用runInLoop恢复。见 reconcile-audit.md。没有机械补一次检查，也不声称消除快照后重新加入、周期延迟等边界。

## 测了什么

- 原镜像直接自身HELLO、旧地址回环成功复现：cppai-sentinel-guard-baseline-fixed.log。
- 补丁隔离定向通过：cppai-sentinel-guard-targeted-stable.log；最终完整脚本也实际执行通过。
- watcher双向真实切主，90/94次严格零残留采样；新controlled阶段单独1passed。原来的受控清连接断言没有删除或加宽。
- 首轮255 passed / 26 skipped；全部AI、受控切主、Redis停主/重加入、app/L7故障、长周期撤权和原严格9项通过。日志：/private/tmp/cppai-ci-ws-fixed-full.log/.exit（0）。
- 登录6次：4成功、2可重试，最长2.913秒；消息6次：5ack、1可重试，最长2.009秒。9.421秒恢复，5条确认消息各保存一次；旧主重加入53次采样，代理连接始终为0。
- 13个常驻服务healthy，正常cppaiserver-app镜像已恢复；13个受测文件与cppai-ci-ws-fixed-full-source-hashes.json完全匹配。临时诊断只使用/tmp源码副本和独立镜像，没有修改项目C++源码。
- OpenSpec strict change validate、全仓普通validate（9/9，旧Purpose warning保留）、语法和diff检查通过。13个受测文件哈希匹配。
- 以测试fake providers运行；未新增真实模型调用验收、压力模式或生产服务器验收。

## 失败与保留证据

所有路径均为本机 /private/tmp；不会随Git上传。可复跑回归，不以文档替代原始日志。
- 本次第一轮full在原受控切主失败：cppai-sentinel-guard-full.log/.exit（1）。先保留现场，定位HAProxy异步DNS问题，修实现后重跑，不靠重跑碰绿或删断言。现场日志为 cppai-sentinel-guard-full-failure-proxy-sentinel.log 和容器快照json。
- 新隔离工具/补丁的早期失败保留，具体见 sentinel-root-cause.md。修正了SHUTDOWN无回复处理、Redis SDS键类型和地址收敛稳定窗口。
- 此前 cppai-hardening-* 的失败与成功日志也保留。此前7项验证中曾对 sentinel-1 做一次RESET是历史事实；本次已证明自引用路径并实现修复，没有再次RESET或清除真实数据。
- 原握手401/403后的4字节close(1000)仍未修，不用改Content-Length掩盖。全仓旧Purpose格式warning也保留。

## 用户已决定停止的WS调查

2026-11-05用户明确决定：“那先跳过吧 不查了”。停止WS根因调查，原5.6移为用户暂缓项，不计作完成。c6dd101的CI #41852645569曾出现无关闭帧断连，根因未查明；另一次并发诊断有5秒消息超时，阶段日志出现约2秒排队后再等约2秒会话查询，未证明与断连同因。原失败记录保留cppai-sentinel-guard-ci-final-failed.log/.json。之后三轮定向、本地完整和两次原生CI成功；隔离阶段镜像五轮15个并发观察窗口及TLS-only 20,000次Ping/Pong通过，均不能替代根因证明。额外诊断准备阶段的直接采样连接重置/主从未稳而中断没有计作通过。相关/tmp证据以cppai-ws-parallel*、cppai-ws-trace*、cppai-ws-tls-ping-diagnostic.log命名。没有实施针对原断连的确定修复；原5秒/30秒/不断线/历史和旧主零连接断言保留。不要自动恢复此调查。

## 交给 Claude 复核什么

**让他double check**：
- 自身ID过滤是否同时覆盖直接HELLO和历史配置；启动清理使用SDS且只删自身，epoch/票/其他同伴不变。
- Sentinel定制镜像只用在Sentinel，版本/校验和/TLS构建一致；未来升级Redis需复核上游等效修复及补丁，不盲目套用。
- watcher的实际IP确认是否确实在shutdown sessions之前；全新CI的controlled+prepare覆盖两个方向，1秒/30秒/零残留断言未放宽。
- TLS门闩的首次发送/失败/关闭次序，CA/主机名校验和TLS1.3实测；业务观察者在停主前运行，5秒/30秒与ack历史完整性不变。
- S1线程归属证明成立，而不是把“没有复现”冒充没有竞态。

## PR / CI 与下一步

PR #10：https://github.com/anwalld/CppAiWebServer/pull/10 。功能实现提交e0cad511a453738df0f1078d72e8449b11552286的两次原生完整CI成功：https://github.com/anwalld/CppAiWebServer/actions/runs/41958390082 。证据为本机cppai-ci-e0cad51-result.json + cppai-ci-e0cad51.log与cppai-ci-e0cad51-attempt2-result.json + cppai-ci-e0cad51-attempt2.log；首次窗口6.781秒恢复、6条确认消息各一次、旧主重加入52次采样零代理连接。本交接收尾仅改文档，最新文档提交以PR Checks为准。未合并或归档。

本change不自动合并或归档。若继续做用户明确保留的握手问题，需要另开范围。

新的请求暂存/补发计划文档保持原样，不属于本PR，没有修改或提交。
