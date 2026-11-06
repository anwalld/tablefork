# Verification Report: add-chat-controls-and-observability

2026-11-06。本报告检查实际代码、规格、设计和测试证据。当前为提交前报告；最终源码完整回归与准确head CI待PR执行后补充。

## Summary

| Dimension | Status |
|---|---|
| Completeness | 8/10任务，7项需求均有实现；4.1最终全量与4.2 PR/CI待完成 |
| Correctness | 7/7需求与9/9场景映射到代码及测试；最终源码全量由分支CI确认 |
| Coherence | 锁顺序、幂等、共享桶、迁移、Vue/Python结构与设计一致 |

## Requirement Implementation Mapping and Scenario Coverage

| 需求/场景 | 实现证据 | 验证证据 |
|---|---|---|
| 跨设备未读 / 两设备查看 | V6迁移；src/services/room_service.cc的list、join、markRead；room_controller.cc的私有read_updated；ChatView.vue前台展示与合并刷新 | tests/chat_controls/test_http.py的升级/并发/重新加入/自发排除；test_chat_controls.py私有事件；ChatView.test.js前后台、轮询及同步；真实双页已读清除 |
| 人的提及提醒 / 定位提及 | message_service.cc同事务保存message_mentions；mentions.js补全；ChatView.vue定位上下文 | 真实SQL未读提及、前端补全与列表；浏览器guest发送@并点击定位、提醒清除；撤权后上下文迟到回归 |
| 受权限约束的搜索 / 筛选与越权 | message_service.cc的search绑定LOCATE与成员权限、游标/时间/发送者；ChatView.vue搜索及上下文 | 真实HTTP字面%_、分页、发送者、时间合法性与越权；网关搜索；浏览器关键词搜索和上下文跳转 |
| 停止与权限 / 停止进行中回复 | ai_control_controller.cc的stop/deliverStopped；message_service.cc按房间锁串行并以generation_id去重；controls.py与engine.py取消和恢复；stopped协议/历史 | SQL部分只保存一次/空停止/权限；网关并发停止完成；ai/tests/test_controls.py阻塞provider、迟到确认、过期worker恢复、不续链；浏览器停止标记 |
| 房间暂停与恢复 / 暂停后恢复 | room_ai_controls截止线；pause的memo→room锁序；controls.reserve的FOR SHARE与engine旧触发/冷场/接话判断 | 真实SQL角色和状态；网关暂停/恢复无重播；Python跨恢复与旧触发回归；浏览器暂停状态 |
| 结构化关联日志 / 关联与隐私 | src/common/trace.*字段白名单与稳定编号；HTTP advice、SQL/Redis包装、RequestBuffer/Lua等待计时；AI根请求编号；Nginx JSON；Compose轮转 | tests/unit/trace_test.cc；18项真实Redis队列含wait_ms回归；check_logs.py；最终真实探针1228记录，队列等待1133ms，WS→AI→保存及HTTP/队列/SQL/Redis关联通过 |
| 流式显示回复 / 逐段显示、完成后成为普通消息、片段不入历史 | ai_actions.cc、ai_events.cc、message_service.cc、proto/ai.proto；原流式路径保留，停止边界保存快照 | 原AI集成/规则与流式测试包含在260项完整回归；AI草稿前端回归；SQL停止快照及网关竞争检查 |

7项需求为6项ADDED、1项MODIFIED；无REMOVED或RENAMED条目。所有9项场景已检查，未仅以任务勾选当作证据。

## Design Adherence and Code Pattern Consistency

阅读与消息提交共用房间行锁，已读GREATEST单调推进；搜索使用绑定参数与有界分页。AI控制跨实例持久化，以同一generation_id保存停止快照；恢复先核对SQL终态。pause遵循RequestMemo→room→control顺序，Python使用真实AI最小权限支持的FOR SHARE。所有新业务路由进入既有RequestBuffer分派，202受理与最终结果分开。

新增迁移V6而未改旧迁移；gRPC新增字段7/8向后兼容；Vue组件、ai/chat_ai和src/controllers遵循现有目录结构。JSON字段白名单及Docker轮转复用原部署。未发现必须先修复的设计或样式偏离。

## Actual Local Validation

原始证据位于本机/private/tmp，不包含在Git提交内。

- cppai-chat-full.log/.exit：退出0，常规260 passed / 27 skipped，AI、受控/自动Redis切换、app/web故障、撤权等阶段通过。严格阶段9 passed；恢复9.512秒；113条确认消息各持久化一次；旧主回归53次采样零连接。**此完整运行早于最终遥测、即时已读及撤权上下文细化，不能称为最终源码的完整通过。**
- cppai-chat-cpp-final.log/.exit：C++编译和ctest退出0；后续仅日志flush修改已由最终正式runtime镜像构建覆盖。
- cppai-chat-web-final-complete.log：15文件108 passed；cppai-chat-web-final-complete-build.log及cppai-chat-web-release-final.log/.exit：生产构建和最终web镜像成功。
- cppai-chat-ai-telemetry-final.log：81 passed。
- cppai-chat-queue-wait-fixed.log：真实Redis 18 passed，先失败的基线日志保留。
- cppai-chat-controls-ci-portable.log/.exit：真实SQL/HTTP 4项通过，用CI默认镜像内二进制入口，验证升级基线。
- cppai-chat-gateway-final.log/.exit：TLS/共享桶下最新3项通过，含私有read_updated。只读挂载导致pytest缓存2条警告，不影响断言。
- cppai-chat-trace-verdict.log/.exit：退出0，1228条真实日志；HTTP/WS/队列/SQL/Redis/AI关联、队列等待1133ms及字段/敏感固定标记排除通过。
- 浏览器使用可信本机CA及fake模型，实测登录/建房/发消息/搜索/上下文、未读/@定位、两页已读同步、暂停与停止标记。停止点击接近完整生成，浏览器没有单独证明“恰好半段”；部分结果由阻塞provider及真实SQL测试证明。

## Preserved Failed Attempts

1. cppai-chat-full-locking-failed.log/.exit：退出137，真实AI账号FOR UPDATE权限不足；改FOR SHARE后验证通过。
2. cppai-chat-full-environment-failed.log/.exit：夹具遗漏--no-deps重建正在回归的依赖，133 failed/86 passed/27 skipped/41 errors。污染运行不计成功；后续完整运行退出0。以后临时夹具一律--no-deps。
3. cppai-chat-queue-wait-baseline.log：claim未返回wait_ms的真实失败；补字段后18项通过。
4. cppai-chat-trace-buffered-failed.log/.exit：stdout缓冲使立即采集缺记录；逐条flush后最终探针通过。
5. Docker Hub anonymous token EOF曾中断前端metadata下载；拉取官方node基础镜像后最终标准构建成功。

## Issues by Priority

- CRITICAL：交付任务4.1最终源码完整CI、4.2提交/PR/准确head CI尚待完成；执行后更新报告，不提前archive。
- WARNING：无已确认未修复的实现/规格偏离。最终源码全量验证仍待CI，不作为已通过。
- SUGGESTION：让他double check权限、控制状态持久化和迟到事件，以及日志热路径开销。本次两轮review来自同一执行者。

## Final Assessment

实现与专项证据齐全；当前交付验证未闭合。PR准确head完整回归成功后再更新任务、同步规格和归档，归档提交还需最终head CI；合并后检查main准确merge SHA的CI。
