# 验证报告：buffer-business-requests / PR #12

本报告以最终源码清单和逐阶段日志区分验收，不将归档、脚本清理成功或旧CI当作业务通过。原实现3f34eaa、归档ef55548、main整合d12013b；运行修正提交`f6c3c1dfac2272e21c95e5eb683564dee45ecbeb`；最终测试同步提交`297962660a433f3543f7160695f9eb22e60baca2`。234个最终构建/运行/测试输入冻结在`verified-integration-source.sha256`；原`verified-source.sha256`只作历史证据。

## Completeness 与 Coherence

- 已核对全部28个HTTP业务入口（auth 5、room 14、AI 9）及WS send、七项主规格需求/十四个场景、D1–D7设计和12/12任务；健康/控制/查询独立。已归档并同步主规格，不重复归档。
- 共享单实例内存Redis；安全随机领取凭证、HMAC绑定、AES-GCM载荷/结果；真正Set-Cookie交付最终登录结果；FIFO原子领取、每实例无信用积累的50/s及8并发。
- SQL写/请求记录同事务和唯一约束；挂起后检查原许可；external意向不盲重放、不把凭据写SQL；旧SQL结果按当前状态对账。
- 许可绑定boot/epoch/revision/challenge，期限从原challenge起算；phase锁撤销后不能续旧grant。自动等待旧许可失效后健康实例先恢复，主动缺ACK取消，不增加Docker隔离权限。
- 元数据与密文/结果实际长度计入64MiB应用预算，Redis128MiB/noeviction、单结果16MiB；固定代理/桶IP在独立动态池之外。
- PR修正进一步核对UI未知结果与SQL删房/缓存清理边界，已写入design及运维文档。读取SQL共享锁到缓存写结束，删除的独占锁覆盖跨实例竞争；DEL不明时不提前报告成功，原编号恢复补清缓存。

## Correctness：需求及场景

| 需求 | 真实依据 |
| --- | --- |
| 暂停受理/正常直通 | 双C++HTTP与真实WS、排队登录浏览器最终Cookie/刷新；queued和持久化ACK分别验证 |
| 桶容量/超时 | 17真实Redis脚本；实际默认1024条及45秒；非满桶OOM拒新保旧，桶停启查询/受理503与unknown |
| 恢复限速/FIFO/排空 | 可注入时钟C++50/s8并发无追赶；真实Redis并发领取/新旧同桶；完整连续窗口最终直通 |
| 结果权限/跨实例 | owner/错误凭证、匿名登录、Cookie不进JSON、跨实例领取；前端100项、浏览器unknown及成功退出 |
| SQL恢复与参数冲突 | 真实MySQL8并发、409、原编号；SQL提交后拒绝结果写并SIGKILL A，B在30秒内同房间且计数1 |
| 主动取消 | 仅阻断B控制链路，缺ACK取消且Sentinel epoch和主不变 |
| 自动健康实例先恢复 | B失联A恢复7.470秒、冻结拒旧grant、控制者同容器同IP重启6.939秒；完整旧主重加入零连接 |

## 最终源码验证

所有日志位于`/private/tmp/cppai-buffering-<前缀>.log/.exit`。这里的成功均核对实际退出码和业务断言。

| 前缀 | 退出码/结果 |
| --- | --- |
| shared-ready-final-full | 退出0；258 passed/27 skipped，五AI阶段、受控与自动切换、app/web故障、网关/限流/30秒对账及最后9项严格检查全部通过；窗口8.841秒恢复，79条确认各一次，旧主54次采样零代理连接 |
| window-shared-ready | 0；8.836秒恢复，86条确认各一次；并行生产者完成/共享桶空/双许可就绪后原直通断言通过 |
| cache-final-components | 0；ctest、真实MySQL/C++适配、8并发去重、409、许可过期回滚及晚分发不重建已删缓存 |
| cache-fixed-http | 0；3项/2.34秒：原0.3秒删缓存断言、DEL拒绝后SQL已删/unknown及原编号恢复、缓存写故障 |
| resumed-web | 0；15文件100项 |
| resumed-web-build | 0；生产构建 |
| pr12-final-health.json | 0；14生产服务running/healthy，1测试控制代理running且未设healthcheck |
| browser-logout-real | 0；实际观察logout 202后才重启隔离RAM桶，代理自动恢复 |
| browser-logout-verdict.json | 0；实际UI+截图复核未知提示/保留会话/刷新/最终成功退出；不是仅凭driver退出码 |
| browser-final-sql.json | 0；此前真实浏览器6条消息各一次，最后seq 1..6 |

负向浏览器截图：`browser-logout-unknown-confirmed.png`，已目视核对明确未知提示且聊天仍在；成功并刷新截图`browser-logout-completed.png`。unknown-confirmed.txt是AX差量“无变化”，没有将其冒称完整AX证据。正向浏览器18.266秒恢复对应`browser-master-bounded`、恢复页面`browser-restored.png`。全程使用已信任TLS证书，未绕过警告。

## 保留的既有专项及代码阶段

这些是同分支对应阶段的成功证据；它们不能独立证明后来的所有改动。最终完整回归和新增回归在上表另列。

| 前缀 | 真实结果（均退出0） |
| --- | --- |
| integrated-full | 4036965阶段257 passed/27 skipped、全部故障与最后9项严格检查；9.187秒恢复，74确认各一次，51重加入采样零连接 |
| probe-fix-window | 1a12fc6阶段8.887秒恢复、107确认各一次；最多一个登录在途，5秒/30秒保持 |
| login-burst-final | 默认12次排队错密码登录：5次401、7次429且retry_after>0；实际45秒/1024条、OOM、桶停启、SQL提交后强杀接手，62.108秒 |
| control-real-final | 44.90秒：控制分区A先恢复7.470秒、主动缺ACK取消、真实WS queued/ACK、冻结拒旧许可、同IP控制者重启6.939秒 |
| coordinator-tests-final / redis-tests-final | 10协调测试（含禁GC真实socket关闭）/17真实Redis脚本 |
| http-tests-cookies | 双真实C++注册/登录最终双Cookie、权限/跨实例、参数冲突及会话撤销 |
| schema-tests / external-intents | 19项V5/记录保留/踢后重加对账；external重复不建第二会话、参数409、SQL无响应凭据 |
| ci-env-shell | 实际生成三个独立临时密钥并验证长度/格式，不输出秘密 |

## 失败现场和修正依据

- d12013b [CI 42017515952](https://github.com/anwalld/CppAiWebServer/actions/runs/42017515952)：恢复探针积累同账号登录，合法429。改为最多一个登录在途，额外用真实排队突发证明默认限流仍生效。4036965的42019259388被取代取消，不算通过。
- 1a12fc6 [CI 42021442408](https://github.com/anwalld/CppAiWebServer/actions/runs/42021442408)：256 passed/1 failed/27 skipped，`test_deleted_room_leaves_no_cached_messages`原0.3秒断言发现真实异步缓存竞争；`ci-1a12-failed.log`保留。旧代码确定性回归`cache-race-baseline`退出1、报late distribution repopulated a deleted room cache；最终组件与HTTP回归均通过，没放宽断言或盲重跑旧SHA。
- `cache-final-full`退出1：常规258 passed/27 skipped及五AI阶段通过，连续窗口在另一after生产者尚未结束时立即要求直通，收到合法202。此为公共桶就绪前置条件竞争；新增生产者完成屏障与全局空桶/双许可检查，最后HTTP200/WS ACK和原30秒/5秒/持久化断言不变，`window-shared-ready`真实专项8.836秒通过。f6c3c1d的42111009051因此被后续测试同步提交取代并取消，不算通过。
- 旧App在unknown/timeout后假退出的回归先复现2失败/1通过；修复后100项全过。中途一次新测试误选断连banner而不是toast，修正定位，不更改产品断言。
- 先前浏览器人工等待超过45秒、首次主故障脚本人为等待占满30秒的失败保留；最终用绝对12秒恢复人工代理暂停，保持原30秒要求。旧browser-logout-unknown脚本退出0只证明清理，不算负向业务通过；本次real+verdict才算。
- 更早的窗口无限追加/期望首响应200、OOM期待旧任务查询也失败、假设maxmemory=1必拒绝缩小结果写等诊断错误均在原日志保留；修正测试前置条件，不提高漏桶速率/期限。结果写拒绝使用真实ACL后SIGKILL。

## CI 与最终交付

提交`297962660a433f3543f7160695f9eb22e60baca2`的原生[CI 42111975435](https://github.com/anwalld/CppAiWebServer/actions/runs/42111975435)已completed/success。该运行实际包含C++单元、76项AI单元、100项前端、常规258 passed/27 skipped、全部故障阶段和最后9项严格检查；原30秒内13.734秒恢复、36条确认各一次、52次旧主采样零代理连接。准确快照`/private/tmp/cppai-buffering-ci-2979626-final.json/.exit`及`ci-2979626-passed.log`保留。后续本次提交仅更新交接/报告/设计，234个运行/构建/测试输入与2979626保持一致。最终文档提交的CI仍单独核对最新PR head；准确最终结果交付在[PR Checks](https://github.com/anwalld/CppAiWebServer/pull/12/checks)、PR说明及本机`CppAiServer-进度.md`，不把源码CI冒称文档提交CI。

最终提交和最新CI须精确匹配PR #12 head；本报告所引用历史失败不作为最终通过，PR #10更无关。最终PR Checks和用户可见进度保留最新准确SHA/run；本报告提交本身产生的新CI仍需通过。原始日志留在当前机器`/private/tmp`，未当作远端上传附件。

## 验证边界

- `pr12-new-main-strict`退出0；`pr12-archived-strict-final`退出0（8/8归档change）；`pr12-specs-normal`退出0。`pr12-specs-strict`退出1：8/9通过，唯一既有realtime-messaging Purpose不足50字符警告。与adcd294的该文件SHA256相同，不改无关warning、不声称全仓strict全绿。
- 原change归档和主规格同步已完成，当前再核对12项全勾选、无活动同名change；归档strict只验证任务完整，不替代行为测试。
- 只在隔离cppai-buffering-static项目做故障，最终14个生产服务running/healthy，另1个测试专用控制代理running（无healthcheck）。原服务器未部署/验收；无真实模型费用；不合并PR、不向别人发消息。
- 共享RAM桶可丢任务；unknown不代表未执行。external无法证明的副作用不自动重放，不承诺全局exactly-once；初值不等于性能承诺。固定IP生产迁移另需维护窗口。
- **让他double check**：许可/代次、挂起后的副作用、SQL与external边界、查询认证/Cookie、缓存锁与删房清理、旧claim和历史结果对账。本次执行者已复核，不冒称独立审查已完成。
