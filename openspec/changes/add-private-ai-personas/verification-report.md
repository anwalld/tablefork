> 2026-11-07更新（Claude）：最终源码完整回归退出0，任务6.1完成；堆损坏未复现但根因未查明。以[Claude接手调试记录](claude-debug-2026-11-07.md)为准。未提交、未建PR。

> 2026-11-06 21:55更新。已达用户设定的10%剩余额度交接线。最新修复、未解堆损坏与正在运行的ASAN回归见[继续调试交接](resume-debug.md)。verify仍未通过。

# Verification Report: add-private-ai-personas

2026-11-06（Asia/Shanghai）。实际复核执行者为Codex；没有第二位独立审查人。**让他double check。**

## Summary

| Dimension | Status |
| --- | --- |
| Completeness | 功能与专项任务已实现；15条细分任务中14条完成，6.1因最终故障复验失败保持未完成。新规格12项ADDED/MODIFIED要求、41个场景均完成实现映射；无REMOVED/RENAMED操作 |
| Correctness | C++构建/ctest、88 Python、114前端及构建、64项受影响HTTP/AI/SQL检查通过；最新故障阶段2项失败，整体验收不通过。不能用此前274项完整回归成功替代最终版本故障结果 |
| Coherence | 模型/身份分离、人设SQL快照、退出事务、入群隔离、私有权限及共享桶模式按设计实现；最新运行源码与镜像哈希另行保存。CI/PR/部署未执行，不属于本次已验证结果 |

## 逐要求实现与证据

| 能力/要求 | 实现位置 | 对应验证 |
| --- | --- | --- |
| ai-members：目录 | AiService::models/catalog、ai_model_types、前端我的AI | test_catalog_marks_my_keys、私人目录/同名身份测试、浏览器 |
| ai-members：邀请 | AiService::request、AiController、可信dispatch | 创建者限制、管理员越权、无key、重复/不同人设冲突、失效ID测试 |
| ai-members：审批 | AiService::review、room锁 | 普通成员无权、管理员无需key、重复审批、退出/审批并发 |
| ai-members：移出 | AiService::remove/removeOwned及RoomService退出/踢/封禁 | 三种成员失效路径、C++探针重邀、历史保留 |
| ai-personas：身份与所有权 | member_id、creator_user_id、目录WHERE过滤 | 同用户同名同模型两角色、SQL FK/CHECK、HTTP幂等、私人ID不泄露 |
| ai-personas：房间独立人设 | room_ai_personas、editPersona条件版本更新 | 两房间不串、非创建者403、旧版本409、16KiB字节边界、浏览器编辑 |
| ai-personas：本轮固定人设 | GenerationControls::reserve、Engine claim/run/generate | 单元中编辑后重试仍旧版本；真实SQL生成中编辑，当前快照1/下一轮2；开始边界和恢复代码逐条复核 |
| ai-personas：离房与代次 | removeOwned事务、SQL快照、Chunk/Final/Failed处理器、前端admission检查 | 真实SQL/C++重邀后旧Final拒绝、新Final去重；真实Redis监听旧Chunk/Failed零发布而新动作发布；前端迟到片段负向用例 |
| ai-personas：旧角色兼容 | V7回填、模型启动校验、legacy分支 | 新库/V6升级原列全相同；旧预置AI真实HTTP/WS回复；旧四字段状态及回填编号恢复单元 |
| ai-replies：提及 | message_mentions AI目标、gRPC mention_ids、rules、ChatSocket | 同名只选一个；歧义不触发；已选角色退出不文本fallback；非法目标保存前拒绝；重试保留ID |
| ai-replies：费用 | 继续job.payer与provider取key | 不同于创建者的触发者key捕获断言；既有接话/冷场付款规则测试 |
| ai-replies：按角色/版本恢复 | 任务成员ID/入群编号、SQL固定快照、原generation_id | AI进程故障阶段已通过；重试快照与旧入群动作负向通过；最终Redis故障后AI回复链受连接异常影响，属于下方阻塞 |

41个场景采用组合用例与逐条源码复核，不宣称41条各自独立的浏览器端到端测试。真实模型风格质量/计费测试未纳入本轮；fake模型已按计划覆盖身份、权限和控制链路。

## 实际执行清单

1. V7结构专项：`/private/tmp/cppai-personas-schema-ybchujng/run.log`、`run.exit=0`。MySQL8.4.6、Flyway11.14.1，新库12 passed/1不适用跳过；升级13 passed；重复migrate/validate成功，所有旧表原有列逐行不变。
2. `/private/tmp/cppai-personas-full-4.log/.exit=0`：274 passed/27阶段跳过、全量单元、完整故障及严格9项、18队列、4升级/HTTP通过。该轮在最后提及身份/前端入群补丁之前，只作为准确范围的历史证据。
3. 最新源码：`/private/tmp/cppai-personas-final-relevant.log/.exit=1`。C++构建/ctest成功，88 Python、114前端及构建通过，64项受影响接口通过/2阶段跳过；AI停启/生成中重启、受控Redis切换和故障准备通过，随后业务窗口及故障后连接验证失败。后续旧主重加入、app/web故障和最后严格阶段未运行，不能计为当前版本通过。
4. 最新服务探针：`/private/tmp/cppai-personas-component-delivery.log/.exit=0`，实际C++/MySQL/Redis：SQL角色创建幂等、同名独立ID、旧Final拒绝且新Final只存一次、旧Chunk/Failed不广播、新Chunk/Failed正常广播。
5. 补充HTTP/SQL：`/private/tmp/cppai-personas-targeted-final.log/.exit=0`，3 passed：并发退出审批、旧预置AI回复、当前SQL快照不随编辑变化/下一轮取新版本。两个只读pytest缓存提示保留，不影响断言。
6. 浏览器：可信HTTPS18487，创建、邀请、编辑、回复、created by及最终构建刷新后保存的人设均实际核对。`/private/tmp/cppai-personas-browser-final.png`、`cppai-personas-browser-chat.png`。fake回复不证明真实模型风格质量。
7. 本change strict通过；全仓普通14/14通过；全仓strict13/14，唯一旧realtime-messaging Purpose不足50字符WARNING，用户此前要求保留，主规格未修改。

## CRITICAL

### 1. 最新故障回归尚未通过，任务6.1未完成

- `test_failover.py::test_business_requests_during_redis_failover`：`POST /api/auth/login`触发requests ReadTimeout，原read timeout=5秒，没有放宽。
- `test_failover.py::test_redis_primary_failure_recovers_without_restarts`：新主6.89秒就绪，跨实例消息已确认，随后等待AI回复时WebSocket报`no close frame received or sent`。
- 现场保留在最新相关复验日志。失败后按原脚本恢复栈健康；没有改断言、清空数据、RESET Sentinel或盲目重跑碰绿。
- 初步源码证据：登录链顺序调用DeviceStore、LoginLimiter、SessionStore等Redis操作；`src/main.cc`对每条Redis命令设2秒，GuardedRedis目前只在每条操作前检查许可。这是整体5秒边界的排查方向，**尚未证明为本次超时的确定根因**。WebSocket异常关闭原因也未查明，不能直接认定来自同一个原因或人设补丁。
- 故障后只读统计：SQL634个房间、211个私人角色、368个saved生成；Redis工作集0、计时器2、键11376。只是多轮本地测试后的规模，不证明负载导致失败。
- 建议接手：先读取保留现场及原5秒/30秒断言，聚焦在途登录各阶段耗时、许可期限/Redis重连和WS关闭来源。确认真实原因后再改动相应范围和定向复现；不要把此前成功CI或full-4作为最终故障验收。

## WARNING

- 全仓strict已有Purpose过短警告未修，与本功能新增delta无关。
- 失败pytest traceback包含一次性测试账号请求头，原始日志仅本机并已设为600；不要复制Cookie/密码头到交接、提交或GitHub附件。

## SUGGESTION

无新增非阻塞建议；独立复核重点见handoff。不能因文档收尾或额度压力把上述CRITICAL降级成建议。

## Final Assessment

发现1组阻塞性故障回归问题（2个失败用例），修复并复验前不具备归档/合并/部署条件。实现、专项及浏览器已交付，最终故障验收未通过；由于测试流程在失败后停止，后续故障/严格阶段未验证，已逐项列出原因。没有宣称全绿。

## 2026-11-06故障定位补充

[最新定位记录](failure-investigation.md)已确认原日志含AsyncTask未捕获ApiError和后端异常重启，并查到AI片段入口的异常边界缺陷；登录超时已缩小到限流计数清理失败后的链路，具体挂起点仍待复现。此次未修改代码或重新执行验收，verify状态保持未通过。
