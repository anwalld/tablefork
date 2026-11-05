# 验证报告：buffer-business-requests

本地verify通过：逐条核对OpenSpec七项需求、十四个场景、设计和实际测试证据；没有未实现的需求或未解决的行为验收失败。受测实现提交为`3f34eaa684cbdca8cb439efb0ad488c7721acbd5`，分支codex/shared-request-buffering，基于adcd294。`verified-source.sha256`冻结227个非秘密构建/运行/测试输入，提交前后校验均通过。后续归档提交只变动规格和验证材料，不混淆为新运行代码。尚无新功能远端CI。

## Completeness

- 28个HTTP业务路由（auth 5、room 14、ai 9）均有统一受理入口及可信分派；WS send另接入，健康/控制/结果查询独立。
- CLI确认12/12任务完成；实现、文档、所有本地验收及规格/归档闭环均已检查。
- 主规格已按当前delta新增request-buffering，七项需求/十四个场景完整同步，Purpose原文保留；不将规划、CLI验证或代码存在当成端到端完成。

## Correctness：需求/场景映射

| 需求与场景 | 实现 | 真实依据 |
| --- | --- | --- |
| 暂停期受理：排队登录、正常直通 | request_buffer.cc；三个业务控制器；chat_socket.cc | 双实例HTTP最终双Cookie；常规集成测试；真实WS queued不是ACK |
| 共享桶边界：满桶拒新、45秒过期 | lua/queue.lua、shared_queue.cc、queue-redis.conf | 17项真实Redis脚本；真实默认1024条/45秒；非满桶OOM拒新与旧任务可读保留 |
| 恢复节流：双实例原子领取、恢复新请求同桶 | leaky_gate.h、tick、Lua claim/maybeDirect | C++注入单调时钟验证50/s、8并发及无追赶；真实Redis并发领取/FIFO/新旧同桶/排空直通 |
| 查询权限：任意实例、桶不可用 | PayloadCrypto、BufferController、query、queued-task.js | 双实例所有权/错误凭证401、登录Cookie不进入JSON；专用Redis真实停止受理/查询503、重启unknown；前端92项 |
| 去重恢复：提交后崩溃、编号变参数 | V5、RequestMemo、GuardedTransaction、external_request | 真实MySQL 8并发同编号只写一次/409/过期rollback；SQL已提交且队列running后ACL实际拒绝写回、SIGKILL A，B在30秒内同结果且计数1 |
| 主动确认：缺ACK取消 | coordinator controlled/pause/resume | 实际仅阻断B控制，取消且Sentinel epoch/主节点不变；无Docker管理入口 |
| 自动许可：控制失联、延迟grant、写结果不明 | RunPermit、guarded Redis/SQL、coordinator phase锁/挑战期限 | 确定性时钟延迟/重放/错boot/过期stamp；最终真实B失联A先恢复7.470秒、冻结恢复拒绝旧grant、控制者同容器同IP重启新epoch6.939秒；完整严格回归通过，旧主重加入55次采样零代理连接 |

源码目录均相对仓库根：运行许可/桶位于src/buffering；SQL服务位于src/services；协调器scripts/request-buffer-coordinator.py；前端web/src/lib。

## Coherence：设计核对

- D1–D3：独立单实例Redis、不落盘、请求与结果加密、凭证绑定、可信业务描述分派、结果Set-Cookie按原属性交付；查询不重做业务。
- D4：SQL结果记录与业务事务同提交，唯一插入/FOR UPDATE串行保护，参数冲突拒绝；外部副作用先记意向，不能证明时unknown，不把密码/key/Cookie写SQL。重放旧移除结果按当前SQL对账，不能再次踢掉重新加入者。
- D5–D6：后端本机单调期限从challenge起算，boot/epoch/revision/一次性nonce；入口、领取、挂起后副作用和commit重查；协调phase撤销后不再发旧grant，等旧期限再恢复健康后端。没有容器隔离权限扩展。
- D7：等待条数/体积之外，加密实际长度＋2KiB/任务计入64MiB应用预算；Redis128MiB/noeviction留余量，单结果16MiB。共享配置初始化固定，运维明确排空后改配。
- 固定代理/桶IP位于独立动态池之外，处理Drogon一次DNS解析限制；已有部署网络迁移另需维护窗口并保留卷。
- 沿用Drogon协程/Task、RoomHub和现有权限/消息唯一约束；context随协程携带，不依赖thread_local。
- 已核对CI新增三个独立临时32字节秘密的生成步骤；本地执行校验通过，远端CI未执行。

## 原始证据

所有路径以/private/tmp/cppai-buffering-为前缀，日志后缀.log，退出码文件.exit。

| 后缀前缀 | 退出码 | 结论 |
| --- | --- | --- |
| coordinator-tests-final | 0 | 10项，含禁用GC的真实TCP关闭回归 |
| redis-tests-final | 0 | 17项真实脚本 |
| components-final | 0 | MySQL/C++及Redis适配器 |
| web-tests-final | 0 | 14文件92项 |
| web-build | 0 | 前端生产构建 |
| http-tests-cookies | 0 | 注册/登录最终Cookie、权限、跨实例 |
| http-extended | 0 | 默认45秒/1024条、Redis重启、真实SIGKILL接手 |
| http-result-rejection | 0 | 61.968秒；独立OOM拒新/保旧、实际结果写拒绝后强杀与接手 |
| control-real-static | 0 | 真实控制分区、主动取消、冻结与控制者重启 |
| control-real-final | 0 | 最终源码44.90秒：控制分区、主动取消、WS持久化ACK、自动恢复7.470秒、真实冻结和同容器同IP重启6.939秒 |
| schema-tests | 0 | 19项迁移/记录保留/历史踢人不会移除新订阅 |
| external-intents | 0 | 同编号不建第二会话、409、SQL不保存响应凭据 |
| window-protocol | 0 | 9.851秒恢复；80条已确认消息各保存一次；新直通登录/ACK |
| ci-env-shell | 0 | CI临时秘密长度/格式/独立性实际执行，无秘密输出 |
| full-protocol-final | 0 | 常规256 passed/27 skipped、五AI故障阶段、受控/自动切换、旧主重加入、app/web停机、gateway连接及限流、30秒对账与最后9项严格用例全部通过；业务9.229秒恢复，82条确认消息各保存一次 |
| final-health.json / final-health.exit | 0 | 最终14个长期服务全部running/healthy，隔离现场已恢复 |
| strict-before-final.json | 通过 | OpenSpec strict无issue；不替代行为验证 |

## 已定位失败，不能冒充通过

- final-full退出1：常规256 passed/27 skipped、五AI阶段、严格受控切换旧连接归零、路由6.81秒通过，但连续窗口一直追加请求并要求首响应200才结束，30秒内无法排空。现场window-live记录2053次取样、已确认1023条，仍在draining。
- 已修正新协议探针：路由恢复后停止继续追加探针，等待所有已受理结果，然后验证新直通登录与ACK。仍在原30秒内完成，保持5秒首次响应/最终持久化/唯一保存断言；window-protocol退出0。不是提高漏桶速率或放宽期限。
- http-oom-final错误期待内存压力下读取也失败；实际旧waiting可读是noeviction的正确行为。另将OOM放到重启后未满桶阶段，避免把满桶503当作OOM证明。
- maxmemory=1不保证Lua缩小数据的结果写被拒绝，故没有声称结果写OOM；改为真实Redis ACL拒绝EVAL，仍检查原任务running后强杀及30秒同结果接手。
- 诊断中的错误文件版本/误插入finally已纠正；这些失败日志保留，不作验收依据。
- 构建曾遇外部镜像metadata超时；本地已构建镜像用于专项，不把网络失败当功能结论。

## 最终结论与材料限制

- 行为验收没有剩余CRITICAL；全部原故障/严格阶段通过，最终服务健康。归档必须保留完整任务/规格/源码清单及本报告，并执行归档后校验。
- 新change及新request-buffering主规格的strict验证通过；全体主规格普通验证通过。全体主规格strict为8/9通过，唯一既有WARNING是realtime-messaging的Purpose不足50字符。已证明该文件与adcd294字节相同，未改写别人的Purpose；证据`/private/tmp/cppai-buffering-existing-purpose-warning.log`。不得宣称全仓主规格strict全绿。
- 真实浏览器故障E2E未运行；TLS API/WS、前端单元、真实进程故障分别记录，不互相替代。未部署原服务器，不声称服务器验收。
- 新功能远端CI尚无结果；旧PR #10不能替代。当前分支整合最新main尚需另行复核。
- 50/s、8并发是可配置验证初值/上限，不是吞吐承诺；桶重启可丢任务，unknown不代表未执行。external意向防盲重放并不承诺跨存储exactly-once。

## 归档检查

已归档至`openspec/changes/archive/2026-11-05-buffer-business-requests`；`.openspec.yaml`保留，12项完成，无原活动目录或活动列表重复。主规格先内联同步，再与delta逐字比对Purpose和全部需求/场景；因此CLI使用--skip-specs避免重复合并，archive结果specsUpdated=false不代表未同步。

归档命令退出0；归档后strict检查7个归档change全部通过。新主规格strict退出0，全仓主规格普通验证退出0；全仓strict的既有Purpose警告如上保留。归档后227个受测源码输入SHA256再校验通过，git diff --check通过。

结果文件：`/private/tmp/cppai-buffering-archive-result.json/.exit`、`cppai-buffering-archive-validation-final.json/.exit`、`cppai-buffering-new-main-strict.json/.exit`、`cppai-buffering-main-specs-normal.json/.exit`、`cppai-buffering-source-postarchive.log/.exit`。
