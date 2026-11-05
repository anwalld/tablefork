# 给 Claude 的交接（2026-11-05）

**让他double check。**

## 已完成与代码位置

本轮共享业务暂存已实现，本地verify通过。实现提交：`3f34eaa684cbdca8cb439efb0ad488c7721acbd5`。工作区`/private/tmp/cppai-shared-request-buffering`，分支`codex/shared-request-buffering`，基于adcd294。后续规格/验证材料单独提交；受测227个非秘密输入记录在同目录`verified-source.sha256`，提交前后逐文件核对一致。

原CLion窗口仍指向原工作区，所以不会显示这里的改动。没有部署原项目、修改其数据卷、自动合并PR或发送消息给别人。前置fix-offline-message-retries在adcd294已归档，本轮不重复归档；PR #10旧CI不证明本功能。

change为buffer-business-requests，verify逐条覆盖七项需求、十四个场景和设计，12/12任务完成；主规格已同步并完成归档，结果见文末。

## 做了什么

- HTTP全部28个业务路由及WS发送统一受理：正常直通、暂停入桶、恢复新旧同桶、排空直通。独立单Redis纯内存，1024条/16MiB等待体，满桶拒新保旧，45秒未开始过期；每实例50/s、8并发且不积累放行额度。
- 请求/结果AES-GCM加密、HMAC凭证绑定、任意实例查询；最终Cookie通过真正Set-Cookie交付；queued不是业务成功或持久化ACK。前端约1秒轮询，查询失败只重查结果。
- MySQL V5请求编号/结果同事务与唯一约束，涵盖房间、成员、封禁、注册、AI成员、删除key。聊天保留原唯一编号、同ID变内容拒绝；重新认证/检查权限。
- 登录/刷新/登出/存储key先记external意向；无法证明时unknown且不盲重放；SQL不保存密码/key/Cookie。GuardedTransaction异常回滚、每次SQL/commit和请求Redis副作用检查原许可stamp。
- 现有watcher扩展主动屏障与自动许可：boot/epoch/revision/一次性challenge/原始单调期限，失联实例过期停发，健康实例先恢复；不增加Docker控制权限。
- 固定代理/桶IP并隔离动态地址池，解决Drogon只解析一次DNS后IP被副本复用。原部署迁移须维护窗口重建网络并保留卷，不使用down -v。
- 运维、恢复矩阵、完整探索副本在docs/；CI生成新增三个独立临时密钥；Nginx保留应用503。提交后等待钩子仅测试构建，runtime不编译。

## 测了什么

下列前缀均在`/private/tmp/`，对应`.log/.exit`退出0；详细映射见同目录verification-report.md。

| 日志前缀 | 真实结果 |
| --- | --- |
| cppai-buffering-full-protocol-final | 原完整脚本全通过：常规256 passed/27 skipped、五AI阶段、受控切换、自动故障、旧主重加入、app/web停机、网关连接/限流、踢人/封禁对账及最终9项严格用例 |
| cppai-buffering-control-real-final | 44.90秒；B控制断链A先恢复7.470秒、主动缺ACK取消且Sentinel不切主、真实WS queued/ACK、冻结拒旧grant、同容器同IP协调器重启新epoch6.939秒 |
| cppai-buffering-http-result-rejection | 61.968秒；实际默认45秒/1024条，非满桶OOM拒新且旧记录可读保留，专用Redis真实停/启，SQL已提交/任务running后ACL拒绝结果写回并SIGKILL A，B在30秒内取回同房间结果且计数1 |
| cppai-buffering-components-final | MySQL/C++8并发同编号只写一次、409冲突、提交恢复、许可过期回滚；真实C++Redis adapter |
| cppai-buffering-http-tests-cookies | 双真实C++排队注册/登录、最终双Cookie、跨实例领取、正常SQL重试/冲突、会话撤销 |
| cppai-buffering-coordinator-tests-final | 10项，含禁用GC时真实TCP控制连接及时关闭 |
| cppai-buffering-redis-tests-final | 17项真实Redis脚本：预算/并发领取/FIFO/过期/恢复/旧claim/所有权 |
| cppai-buffering-web-tests-final | 14文件92项前端测试 |
| cppai-buffering-web-build | 前端production构建 |
| cppai-buffering-schema-tests | 19项：V5、去重记录保留、踢人后重加入不被历史请求重复移除 |
| cppai-buffering-external-intents | 重复登录意向不建第二会话、变参数409、SQL不存响应凭据 |
| cppai-buffering-ci-env-shell | CI新增秘密的格式/长度/相互独立性实际执行，无秘密输出 |

完整回归中连续业务9.229秒恢复，82条已确认消息各保存一次；旧主重加入采样55次始终零代理连接。最终健康检查`cppai-buffering-final-health.json/.exit`为0：14个长期服务全部running/healthy，隔离测试现场已恢复。

`cppai-buffering-probe-binary-proof.log/.exit`和源码清单记录测试二进制与镜像输入的字节一致性。只用fake模型，未增加真实模型费用。

## 已修正且保留原始现场的问题

- Python递归RESP读取闭包延迟关闭控制socket，旧主零连接原断言抓到；明确关闭makefile并加入禁用GC回归，不放宽断言。
- 连续探针不停追加任务，同时要求首响应200才判断恢复；现场30秒内已确认1023条仍在draining。改为路由就绪后等待全部已受理结果，再验证新的直通登录/ACK；仍用原30秒、5秒首次响应和消息唯一保存断言。
- 真实内存压力允许旧waiting查询成功；不能假定查询也503，也不能把满桶503当OOM。专项用未满桶状态验证OOM拒新。
- maxmemory=1不保证Lua缩小数据的完成写被拒绝；改用Redis ACL真实拒绝EVAL后再强杀，不伪称结果写回OOM。原始失败/诊断/网络超时日志均保留，详见验证报告。
- JsonCpp读取error字段创建null、Drogon Cookie header丢失、MySQL临时socket误判ready、GCC协程临时初始化列表崩溃等已纠正，真实测试已覆盖相关链路。

## 没做与后续

1. **让他double check**：许可代次/原期限和撤销证明；挂起后副作用；SQL同事务与external边界；任务所有权/最终Cookie；Lua内存异常和旧claim；历史结果对账。
2. 分支基于adcd294，整合最新main与原项目其他人提交尚未执行。Nginx e0cad51等价改动已带入，合并时仍应检查，不能覆盖别人工作。
3. 新功能远端CI/PR尚未执行；旧PR #10结果无关。CI临时密钥步骤已补并本地执行验证，但这不等于GitHub CI通过。
4. 真实浏览器故障E2E未执行；本次是前端单元/构建＋真实TLS API/WS/进程故障。原服务器未部署验收。
5. 独立Redis单实例纯内存，重启可丢任务；unknown不代表未执行。正常直通也依赖其原子状态，故桶不可用会503。SQL编号记录首版不自动过期，HMAC密钥须稳定；共享参数仅初始化保存，调整须先排空后重启专用桶。
6. 50/s和8并发是验证初值/限额，不是吞吐承诺。没有承诺跨Redis/MySQL/gRPC的全局exactly-once。不要新增第二套队列HA或Docker隔离权限来改变已确认方案。
7. 全仓主规格strict有一条既有WARNING：realtime-messaging的Purpose不足50字符；与adcd294字节相同，未改写。本change及新主规格strict通过，全仓主规格普通验证通过。不能宣称全仓strict全绿。

## 归档与一次性任务

已归档至`openspec/changes/archive/2026-11-05-buffer-business-requests/`，12/12任务完成，`.openspec.yaml`保留，活动列表无重复。主规格`openspec/specs/request-buffering/spec.md`已按七项需求/十四个场景同步，Purpose原文完整保留，无delta操作标题。先行内联同步并逐字检查后，CLI以`--skip-specs --yes --json`归档，所以CLI返回specsUpdated=false；这不表示未同步主规格。

归档后`openspec validate --archived --strict`退出0，7个归档change全部通过；源码清单再校验0，diff check通过。原始结果：`/private/tmp/cppai-buffering-archive-result.json/.exit`、`cppai-buffering-archive-validation-final.json/.exit`和`cppai-buffering-source-postarchive.log/.exit`。

一次性automation apply-verify-archive已通过应用工具删除（deleteStatus=deleted），未重新排期；不向Claude发送消息，只交付本文。
