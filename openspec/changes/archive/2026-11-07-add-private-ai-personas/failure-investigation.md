# 故障定位补充

2026-11-06。此次只读检查源码、原始失败日志与容器状态，未修改产品代码或重跑故障。verify仍未通过。

## WebSocket异常断开

原日志 `/private/tmp/cppai-personas-final-relevant.log` 第3187行记录 app-1 在UTC 10:54:58.275462发生 `FATAL Exception escaping AsyncTask`；第3221—3223行记录未捕获 `chat::ApiError`，内容为 `service temporarily unavailable`，随后重新订阅。原故障快照中app-1启动时间仅10秒。可确认后端进程异常终止与该阶段断线一致。当前容器是收尾脚本重建的容器，其restart=0不能否定旧容器崩溃。

源码存在明确异常边界缺陷。`src/ai/ai_actions.cc` 的 onChunk新增backgroundContext、受许可保护的事务和commit；许可不可用或途中失效会抛ApiError。handleAiAction通过async_run调用onChunk，整个调用链没有catch。正常切换期间的停发异常因而可以逃逸到AsyncTask并终止整个后端。Failed和Notice异步入口也没有异常兜底，需要一并核查其数据库与发布失败边界。

原日志没有调用栈，因此具体那一次异常是否在onChunk创建上下文、执行SQL或提交检查处抛出，仍需定向注入确认。不能把静态可达路径当作已完成动态复现。

## 登录超过5秒

原日志第2850—2855行把请求定位到直通登录。UTC 10:54:51.671进入AuthController::login，两次Redis调用成功后，第三次于51.854以Network failure结束，耗时113.48ms。第2865行同时记录login limiter clear失败。源码确认clear吞掉Redis异常后继续创建会话。

网关第3789—3790行记录该窗口登录由客户端等待5.003秒后关闭，状态499。进程致命异常发生在58.275，晚于客户端超时，不能用这次崩溃直接解释先前登录超时。

目前证据将登录问题缩小到clear失败后继续执行的链路。对已有device的请求下一步是SessionStore::create。原日志没有后续阶段完成记录，不能证明是多个2秒命令叠加，也不能确认Redis客户端断连排队的超时覆盖。需要增加仅含阶段名、请求编号和耗时的观测，在断连时定向复现；严禁记录密码、Cookie、token或请求体。

## 后续修复与验证

1. 定向验证AI片段在许可已暂停或途中失效时的异常边界，确保进程和其他WebSocket连接存活，同时保留停发、旧入群拒绝和最终消息持久化确认。
2. 给AI异步入口补正确异常处理，区分临时流片段和须持久化确认的Final；不能删除许可校验来避免异常。
3. 定位登录clear之后的实际挂起阶段，再修复有界返回；保留5秒响应断言与原认证限流。
4. 两项分别验证后重跑相称故障和完整验收。6.1继续保持未完成。

让他double check。

## 21点后继续调试

- 暂停许可下收到Chunk的独立进程探针已确定复现。修复前退出134，日志为`/private/tmp/cppai-personas-paused-chunk-before.log/.exit`，出现与原故障相同的AsyncTask未捕获ApiError。补异步瞬时动作异常边界后，该探针及C++ ctest通过，见`cppai-personas-paused-chunk-after.log/.exit=0`。Final仍走原持久化确认路径。
- double check发现片段持有事务连接后又从全局池借连接查询入群与生成状态，可能形成池耗尽等待。改为同一事务连接完成查询，并使Failed校验/广播持有相同房间锁。新增32并发片段探针，待执行。
- 发现已保存消息重发时，原提及AI已退群会先被当前成员检查拦截，无法取得原回执。将新消息的当前目标校验放到既有消息去重之后，保留参数冲突与新消息非法目标拒绝。新增HTTP/WS回归，待执行。
- 带登录阶段日志的故障定位轮中，故障后AI回复用例通过，但窗口观察用例发现另一个后端堆损坏重启。`/private/tmp/cppai-personas-app2-crash.log`含`malloc(): unsorted double linked list corrupted`。该轮`cppai-personas-debug-failover.exit=1`，不能记为验收通过。重启次数由Docker只读检查确认。
- 使用AddressSanitizer重新编译后，受控切换和故障窗口定向轮通过，38条确认各一次、恢复8.940秒，见`cppai-personas-asan-failover.log/.exit=0`。这次通过不足以解释此前堆损坏；正在全量组合压力下取证。
- 断连后续接的两个独立Redis探针均有界返回；包装器+SessionStore版本见`cppai-personas-guarded-disconnect-probe.log/.exit=0`。因此尚无证据把问题归为Redis断连后的所有调用必然挂死。临时诊断程序保存在/private/tmp，未纳入产品。
- ASAN全量轮日志为`/private/tmp/cppai-personas-asan-full.log`，受测源码快照为`/private/tmp/cppai-personas-asan-full-source.json`。该轮开始后才补的32并发与退群后原回执测试及相应源码，不算该轮已验证内容。

当前仍有待定位故障，任务6.1未完成，未提交或创建PR。登录阶段日志为临时诊断，提交前应删除。让他double check。
