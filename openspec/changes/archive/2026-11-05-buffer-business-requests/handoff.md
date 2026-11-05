# 给 Claude 的交接（2026-11-06，PR #12）

**让他double check。** 本文替换归档时的旧状态；change已归档，不再归档一次。

## 当前交付

- 工作区 `/private/tmp/cppai-shared-request-buffering`，分支 `codex/shared-request-buffering`；[PR #12](https://github.com/anwalld/CppAiWebServer/pull/12)。原功能提交3f34eaa、归档ef55548，最新main 193a53d已通过d12013b整合；运行修正f6c3c1d、共享桶测试同步2979626；234个最终输入见`verified-integration-source.sha256`。旧`verified-source.sha256`只证明3f34eaa历史输入，不能用于最终源码。
- 已实现全部28个HTTP业务路由和WS发送的共享异步暂存：正常直通、暂停入桶、恢复新旧同桶、排空直通；单独Redis纯内存，默认1024条/16MiB等待体、45秒未开始过期、每台50/s与8并发、结果5分钟。加密载荷/结果、领取认证、真实最终Cookie；queued不是持久化ACK。
- MySQL同事务请求编号/唯一约束，聊天复用原消息ID；执行前重查认证、权限、限流；external副作用先记意向，不能证明时unknown，不盲重放。原脚本主动缺ACK取消，自动故障等待旧运行许可失效后健康实例先恢复。
- 已同步request-buffering主规格并归档至本目录，12/12任务完成。保留完整探索副本`docs/request-buffering-plan.md`及协议/运维`docs/request-buffering.md`。

## 本次 double check 修了什么

1. 退出请求unknown/timeout时旧App仍清用户并显示登录，已改为仅确认成功才切登录页；等待时禁用重复退出。房间、成员、审批、key界面统一明确区分超时与未知结果；key删除未知不伪报成功。
2. CI实际发现删房后缓存偶发残留：ACK后的异步分发可晚于删房，跨实例也不受本机room worker保护。分发持SQL房间共享锁到缓存写结束，删除独占锁与其串行；后来的分发看不到房间。删房等待DEL成功再响应，失败明确result_unknown；同请求编号恢复先清缓存再通知。没有改原0.3秒断言。
3. 恢复探针最多一个登录在途，避免自己堆积同账号登录触发合法429；独立真实排队突发验证默认限制仍为5次401、7次429，没放宽产品限流。

4. 最终回归又定位了测试并发竞争：窗口观察者仅等自己的任务完成，而并行after阶段仍向同一个公共桶提交业务，随后立即要求直通会合法收到202。现先同步另一生产者完成、确认公共桶空且双许可有效，再执行原HTTP200/WS ACK断言；仍在同一个原30秒总期限内。专项8.836秒恢复、86条确认各一次，未放宽成“202也算恢复”。

## 真实验证

路径统一前缀`/private/tmp/cppai-buffering-`，对应`.log/.exit`保留原始退出码，具体映射和失败记录见verification-report.md。

- **最终完整回归：退出0；258 passed/27 skipped，五AI阶段、受控与自动切换、app/web故障、网关/限流/30秒对账及最后9项严格检查全部通过；窗口8.841秒恢复，79条确认各一次，旧主54次采样零代理连接。** `shared-ready-final-full`；第一次`cache-final-full`的258常规通过，但故障窗口如上失败，退出1，保留不计完整通过。
- 当前源码C++组件/ctest/MySQL/Redis：`cache-final-components`退出0；包含8并发SQL去重、参数冲突、许可过期回滚、故意让分发晚于删房的确定性回归。旧代码同回归实际失败，`cache-race-baseline`退出1。
- `cache-fixed-http`退出0，3项真实HTTP故障测试：原删房缓存测试、DEL被拒后SQL已提交/unknown及原编号恢复、缓存写故障。
- 前端15文件100项与生产构建通过：`resumed-web`和`resumed-web-build`退出0。旧App的退出回归先复现2失败，日志保留。
- 真实可信TLS浏览器：排队登录、最终Cookie与刷新；代理暂停30秒后的消息确认；真实停主且固定12秒解除人为代理暂停，18.266秒恢复；刷新后6条消息在SQL各一次。`browser-final-sql.json/.exit`为0。
- **退出unknown负向浏览器已完成**：实际logout 202后重启隔离RAM桶，页面保留聊天且提示“执行结果尚无法确认，请勿重复提交”；刷新仍登录；恢复后显式正常退出及再刷新都回登录页。`browser-logout-real`和`browser-logout-verdict.json/.exit`为0，截图`browser-logout-unknown-confirmed.png`与`browser-logout-completed.png`。保存的unknown-confirmed.txt仅AX差量，不是完整页面文本，未拿它作断言。
- 仍有效的专项：17真实Redis脚本、10协调测试；默认45秒/1024条、未满桶OOM拒新保旧、桶停启、SQL提交/结果写拒绝后SIGKILL A由B恢复同一结果；控制分区、主动取消、冻结/旧许可及同容器同IP控制者重启。详细日志与对应代码阶段见报告。

## CI：必须认准提交

- d12013b的[42017515952](https://github.com/anwalld/CppAiWebServer/actions/runs/42017515952)失败于探针触发合法429；4036965的42019259388被新提交取代并取消，不计通过。
- 1a12fc6的[42021442408](https://github.com/anwalld/CppAiWebServer/actions/runs/42021442408)失败：256 passed、1 failed、27 skipped，真实缓存竞争如上已修。失败日志`ci-1a12-failed.log`保留，未盲重跑旧提交。
- f6c3c1d的42111009051因共享桶测试同步修正取代而取消，不计通过。
- **最终源码CI：通过。** 提交`297962660a433f3543f7160695f9eb22e60baca2`的原生[CI 42111975435](https://github.com/anwalld/CppAiWebServer/actions/runs/42111975435)已completed/success。该运行实际包含C++单元、76项AI单元、100项前端、常规258 passed/27 skipped、全部故障阶段和最后9项严格检查；原30秒内13.734秒恢复、36条确认各一次、52次旧主采样零代理连接。准确快照`/private/tmp/cppai-buffering-ci-2979626-final.json/.exit`及`ci-2979626-passed.log`保留。后续本次提交仅更新交接/报告/设计，234个运行/构建/测试输入与2979626保持一致。最终文档提交的CI仍单独核对最新PR head；准确最终结果交付在[PR Checks](https://github.com/anwalld/CppAiWebServer/pull/12/checks)、PR说明及本机`CppAiServer-进度.md`，不把源码CI冒称文档提交CI。 本文提交自身的最终CI以PR Checks及交付进度为准，旧PR #10或旧head都不能代替。远端材料只引用Git提交内文件；`/private/tmp`原始日志仅在当前机器，不是GitHub上传附件。

最终隔离现场健康证据：`pr12-final-health.json/.exit`为0，14个生产服务全部running/healthy，另有测试专用buffer-control-2运行中但没有healthcheck。

## 没做及仍需复核

- **让他double check**：许可原始期限与代次撤销；SQL同事务/external边界；查询权限与最终Cookie；SQL共享锁/删除锁顺序、DEL失败和同编号恢复；任务内存/旧claim和权限变化。当前double check由本次执行者完成，未向Claude发送消息，也未伪称有独立第二位审查人。
- 不自动合并PR，不部署原服务器，不改原CLion工作区/数据卷，不新增真实模型费用。原服务器验收仍未做。
- 专用Redis单实例纯内存，重启会丢任务，unknown不等于未执行；直通也依赖它。external无跨MySQL/Redis/gRPC全局exactly-once保证；SQL编号记录首版不自动过期，密钥须稳定。
- 50/s、8并发是限额/验证初值，不是吞吐承诺。固定IP迁移需单独维护窗口保留卷，不能down -v生产数据。
- 新request-buffering规格strict通过、8个归档change的任务strict通过、全规格普通通过；全规格strict仍只有既有realtime-messaging Purpose不足50字符警告，与adcd294字节相同，未修无关warning。
- 本次一次性任务4-pr12完成或真实阻塞后删除，不重排期；最新结果同时写入用户可见`CppAiServer-进度.md`。
