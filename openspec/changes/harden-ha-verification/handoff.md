# 给 Claude 的交接：harden-ha-verification

日期：2026-11-04。**让他double check。**

## 范围与状态

分支 codex/harden-ha-verification，基于 main 809a34a7aefe0317d9f23fc216c9926a203bdf80。上一 change 的 PR #8/#9 已合并，本次是新的 PR。
用户要求：修 Sentinel 自引用与 .DS_Store，连同此前 TLS1.3、切换窗口、对账复核一起 verify、CI、PR。握手拒绝后多发 close frame 仍按用户决定保留。

当前12/12：本地完整脚本exit0、verify完成，PR已创建、实现提交的完整CI已通过。生产C++源码未改；Sentinel镜像与watcher已修改，勿沿用此前“生产配置未改”的结论。

## 改了什么

1. docker/sentinel/Dockerfile、ignore-self.patch：Redis7.4.5 固定源码/SHA 构建；公共 HELLO 入口拒绝自身，配置加载后仅删各 master 的自身同伴条目，保留 MYID/其他同伴/epoch/票。docker-compose.yml 仅三个 Sentinel 改用此镜像。无需清卷或 RESET。
2. scripts/test_sentinel.py：无网络、无业务数据卷的真实 Redis 隔离回归，覆盖直接 HELLO、旧地址回环、脏配置、三台发现/仲裁、地址轮换重启。原版对照能复现，补丁通过。
3. scripts/redis-master-watch.sh：完整回归新发现 FQDN 接受与实际 IP 更新之间的竞态；等待实际 IP 和 FQDN 同时匹配再清会话，解析超时退出沿用原重启机制。不是修改测试放行。
4. scripts/test.sh、test_failover.py：增加 controlled 阶段让全新CI也经历双向受控切主；原 prepare/after/rejoined 和 1秒清连接、零残留、30秒要求保留。故障前 ready/begin/started 启动业务窗口，直接登录/发消息并按 ID核对 ack 历史。
5. sync_tls.py、test_sync_tls.py、conftest.py：首次 sendall 后再 recv，释放失败/关闭等待；移除TLS1.2上限，强制TLS1.2/1.3重复握手和证书/取消回归。证书与主机名校验未关闭，websockets仍13.1。
6. .gitignore 增加 .DS_Store；保留磁盘上的原文件。新OpenSpec change明确 skip_specs: true，产品契约不变。没有改 C++ src、普通Redis拓扑、Drogon版本、.env或证书。

## 对账复核

旧S1尾部竞态在当前源码下不成立。全部标记访问先切回同一app loop；末尾条件检查到清reconciling之间无await/事件泵。依赖Drogon v1.9.13的SwitchThreadAwaiter确实用runInLoop恢复。见 reconcile-audit.md。没有机械补一次检查，也不声称消除快照后重新加入、周期延迟等边界。

## 测了什么

- 原镜像直接自身HELLO、旧地址回环成功复现：cppai-sentinel-guard-baseline-fixed.log。
- 补丁隔离定向通过：cppai-sentinel-guard-targeted-stable.log；最终完整脚本也实际执行通过。
- watcher双向真实切主，90/94次严格零残留采样；新controlled阶段单独1passed。原来的受控清连接断言没有删除或加宽。
- 首轮 254 passed / 26 skipped；AI 故障、受控双向切主、Redis 停主/旧主重加入、app/L7 故障全部通过；长周期 kick/ban 各 1 passed，原严格轮次 9 passed。阶段性 skips 在对应 phase 实际执行，不声称所有可选模式都运行。 日志：/private/tmp/cppai-sentinel-guard-fixed-full.log/.exit（0）。
- 登录 7 次：5 成功、2 可重试，最长 2.008 秒；消息 7 次：6 ack、1 可重试，最长 2.014 秒。7.092 秒恢复，6 条确认消息各保存一次，迟发 post-ack 错误 0。旧主重加入 54 次采样，代理连接始终为 0。
- 恢复后 13 个常驻服务均 healthy；严格 CA 验证的 HTTPS /health 返回 mysql/redis ok；三台 Sentinel 身份与修复前一致，同伴集合恰为其余两台，CKQUORUM 均为三台可用。证据：cppai-sentinel-guard-fixed-services.json、cppai-sentinel-guard-fixed-health.log/.exit（0）。
- OpenSpec strict change validate、全仓普通validate（9/9，旧Purpose warning保留）、语法和diff检查通过。除补丁格式外的10个受测文件哈希匹配；补丁上下文规范化前后生成的sentinel.c逐字节相同，证据为cppai-sentinel-guard-patch-format-proof.json。
- 以测试fake providers运行；未新增真实模型调用验收、压力模式或生产服务器验收。

## 失败与保留证据

所有路径均为本机 /private/tmp；不会随Git上传。可复跑回归，不以文档替代原始日志。
- 本次第一轮full在原受控切主失败：cppai-sentinel-guard-full.log/.exit（1）。先保留现场，定位HAProxy异步DNS问题，修实现后重跑，不靠重跑碰绿或删断言。现场日志为 cppai-sentinel-guard-full-failure-proxy-sentinel.log 和容器快照json。
- 新隔离工具/补丁的早期失败保留，具体见 sentinel-root-cause.md。修正了SHUTDOWN无回复处理、Redis SDS键类型和地址收敛稳定窗口。
- 此前 cppai-hardening-* 的失败与成功日志也保留。此前7项验证中曾对 sentinel-1 做一次RESET是历史事实；本次已证明自引用路径并实现修复，没有再次RESET或清除真实数据。
- 原握手401/403后的4字节close(1000)仍未修，不用改Content-Length掩盖。全仓旧Purpose格式warning也保留。

## 交给 Claude 复核什么

**让他double check**：
- 自身ID过滤是否同时覆盖直接HELLO和历史配置；启动清理使用SDS且只删自身，epoch/票/其他同伴不变。
- Sentinel定制镜像只用在Sentinel，版本/校验和/TLS构建一致；未来升级Redis需复核上游等效修复及补丁，不盲目套用。
- watcher的实际IP确认是否确实在shutdown sessions之前；全新CI的controlled+prepare覆盖两个方向，1秒/30秒/零残留断言未放宽。
- TLS门闩的首次发送/失败/关闭次序，CA/主机名校验和TLS1.3实测；业务观察者在停主前运行，5秒/30秒与ack历史完整性不变。
- S1线程归属证明成立，而不是把“没有复现”冒充没有竞态。

## PR / CI 与下一步

PR #10：https://github.com/anwalld/CppAiWebServer/pull/10。
实现提交 `36239e3d35830178c92ea836ee0cff07cb02436c` 的完整 CI **success**：
https://github.com/anwalld/CppAiWebServer/actions/runs/41848533867 。

CI 在 ubuntu-24.04 全新环境验证 C++ ctest、AI 单测76项、前端13文件/76项、Sentinel隔离回归、集成首轮254通过/26跳过、全部故障/撤权阶段及原严格9项。窗口登录5次（3成功/2可重试，最长2.323秒），消息5次（4ack/1可重试，最长2.023秒），8.899秒恢复，4条确认消息各一次；旧主重加入52次采样零代理连接。日志与状态保存在本机 /private/tmp/cppai-sentinel-guard-ci-implementation.log/.json。

后续提交仅记录本交接、verify结果和任务完成状态，不修改功能源码；最新文档提交的检查以PR Checks为准，不把实现提交的结果冒充另一个提交的结果。未合并或归档。

本change不自动合并或归档。若继续做用户明确保留的握手问题，需要另开范围。
