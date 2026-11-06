# 聊天功能、AI控制与日志交接

2026-11-06，最终实现验收及归档版。**让他double check。** 本文件替代早期临时快照，以本文当前状态为准。

## 当前交付状态

工作区/private/tmp/cppai-chat-controls，功能分支codex/chat-controls-and-observability；PR13：https://github.com/anwalld/CppAiWebServer/pull/13。基线main为a1bfbc0（PR12已合并），实现提交b9883480ce5c5ca51c103f973065981740f9a264。

首次准确实现head分支CI42154648877已completed/success，2026-11-06 11:33:02 Asia/Shanghai完成：https://github.com/anwalld/CppAiWebServer/actions/runs/42154648877。任务10/10，verify的7项需求和9个场景均有代码/测试证据。已同步四个新能力及ai-replies修改，归档到openspec/changes/archive/2026-11-06-add-chat-controls-and-observability，活动change为0。随后推同一PR确认归档提交的最终head CI；尚未合并main，merge SHA及main CI待填写。准确机器记录见ci-evidence.json。

用户已授权本批apply→两轮review→verify→CI/PR→同步/archive→最终head CI→合main并检查main CI。后续仍按docs/development-workflow.md先分支/PR CI再合main。无须重新实施已完成能力。

## 做了什么

- V6迁移：共享阅读位置、人的提及、正式消息stopped、房间AI控制及持久生成快照。升级/加入/重新加入按当前房间最后消息作为阅读基线。
- 房间未读/@、成员补全、点击提及定位、前台成功展示后单调已读；私有read_updated仅向本人投递，200ms合并刷新和10秒补偿轮询。
- 关键词字面搜索、发送者/时间/游标组合筛选，重新检查房间权限；上下文定位与撤权后迟到结果保护。
- 普通成员停自己触发的生成，群主/管理员停任意生成或暂停/恢复房间。部分内容用原generation_id保存一次，空停止无空消息，迟到Final归一到停止快照，跨重启恢复且停止不续链；恢复不重播旧触发。
- HTTP/WS/队列/SQL/Redis/AI结构化阶段日志及根请求关联，非法编号稳定摘要，Lua真实排队等待与stdout即时flush；Nginx排除参数/正文，Docker默认10MiB×3轮转。
- CI沿用原workflow，在scripts/test.sh追加18项真实Redis与4项升级/SQL/HTTP测试，兼容Linux临时目录。首次推送被OAuth缺workflow scope拒绝，调整入口后成功推送，没有额外扩大账号权限。
- 文档：协议与运维docs/chat-controls.md；原产品决定完整保留于docs/follow-up-plan.md；旧归档计划为指向该文档的入口。

## 测了什么

最终实现head b988348的完整CI：C++构建/ctest，81 Python，108前端，261集成通过/27跳过，各AI/Redis/app/web故障阶段及最后严格9项通过；共享桶恢复13.163秒，58条确认消息各一次，确认后迟到retryable错误0；旧主54样本零代理连接；真实Redis18项与升级/SQL/HTTP4项通过。原始日志/private/tmp/cppai-pr13-ci-b988348.log。

本地较早完整回归为260 passed/27 skipped、严格9项，恢复9.512秒、113条确认消息各一次、旧主53样本零连接；与最终CI分开记录。最终专项3项TLS网关包含私有已读通知断言，生产app/web镜像构建成功。

真实日志探针1228记录验证HTTP/WS/队列/SQL/Redis/AI关联、队列等待1133ms及字段/敏感固定标记排除。浏览器实测注册/登录/建房/发消息、搜索/上下文、人的未读/@定位、双页共享已读、暂停状态与停止标签。浏览器停止接近完成，确定性部分保存由阻塞provider和真实SQL证明。原始专项日志与失败记录清单见verification-report.md。

两轮review由同一执行者完成，不代表独立审计。失败现场均保留：FOR UPDATE权限不足后改FOR SHARE；临时夹具漏--no-deps污染完整运行后重新通过；wait_ms返回遗漏、stdout缓冲即时采集缺失均复现修复。原5秒首次响应、30秒恢复、不断线、幂等和旧主零连接断言没有放宽。

## 限制与double check重点

未增加真实模型费用，使用fake provider；备份、撤回、追加接话硬上限、上下文重置和旧WS根因调查未纳入。原Drogon/依赖诊断保留自身格式。暂停/停止的上游取消约100ms轮询，数据库/网络延迟影响耗时；202只表示受理，前端等最终结果。

让他double check：SQL控制与Redis生成状态在取消/失败/租约恢复/迟到Final中的一致性，memo→room锁序、最小权限FOR SHARE；失败终态复用saved且空内容是否值得未来单独状态；已读事件仅本人、撤权上下文保护、搜索权限；日志跨异步编号、白名单及热路径开销。

## 分支与他人工作

用户要求已实际清理14个本地、11个远端已合并旧分支，移除完成的offline-message-retries工作区。原SHA及名称在/Users/mima0000/Documents/ChatGPT/项目/分支整理记录-2026-11-06.md和/private/tmp/cppai-branch-cleanup-2026-11-06.json。不要再处理已不存在的工作区。

本地保留main、当前PR13分支、codex/shared-request-buffering（cppai-buffering-static运行栈仍挂载其脚本/证书）及Claude工作区分支。当前验收工作区也仍被运行栈使用，保留。主目录/Users/mima0000/CLionProjects/CppAiServer有另一执行者的未提交README/docs/旧归档文档修改，不得reset/stash/覆盖/删除。此前未跟踪旧计划已不在旧路径，不能按历史交接盲删。

## 后续执行

1. 同步与archive已完成；推同一PR，确认归档提交的最终准确head完整CI。
2. 成功后正常合并main，不绕过保护；核对准确merge SHA的main CI。保留主工作区另一批未提交改动，若无法安全快进则不要强行处理。
3. 更新本文件/ci-evidence.json及用户入口/Users/mima0000/Documents/ChatGPT/项目/下一批聊天功能交接.md，记录最终head、merge SHA、全部CI、归档路径。避免为状态文档反复触发无必要的main构建；最终运行结果可写用户入口文件并在本归档中预先保留准确查询办法。
4. 全部交付后删除后台任务apply-verify-ci-pr-archive。该任务每5分钟跟进本批，普通等待静默，不重复开发或新建PR。

## 本地运行现场

隔离Compose项目cppai-chat-controls，HTTPS https://localhost:18486，网络172.33.0.0/16；原业务和shared测试栈保留。临时夹具必须docker compose run --no-deps，完整回归期间不重配依赖。/private/tmp/run-chat-full.sh封装本地完整脚本。禁止输出或提交.env、data/certs、compose.chat-test.yml，后者仅覆盖本机镜像名。
