# 聊天阅读、搜索与AI控制

## 阅读与提及

房间列表返回read_seq、unread_count及mention_seq。未读只统计阅读位置之后其他成员的正式消息。升级时以当前房间last_seq回填阅读位置，加入/重新加入时建立同样的基线。

PUT /api/rooms/{id}/read接受整数seq。服务端按房间锁串行、单调推进并约束到房间当前last_seq。客户端在前台、最新消息区已成功展示并处于底部时更新。已读更新通过仅向该成员推送的read_updated通知其他页面刷新；新消息触发200ms合并刷新，10秒轮询作为补偿。各设备查询共享阅读位置。点击“有人@我”加载该消息周围上下文，成功展示后提交对应位置。搜索上下文可用“回到最新消息”恢复正常阅读。

人的提及复用成员@补全。消息保存时识别以空白分隔的完整@成员显示名，并在同一事务保存提醒关联。AI的原有文本提及规则继续使用。

## 搜索

GET /api/rooms/{id}/search支持q（最多200字符的字面子串，%和_不作通配符，按数据库排序规则不区分大小写）、sender（成员ID）、from/to（Unix毫秒）、before（seq游标）及limit（1—100）。结果按seq倒序，next_before用于下一页。每次查询重新约束房间成员权限。结果定位复用messages接口的before/after分页。

## AI控制

GET /api/rooms/{id}/ai-state返回paused及当前generation列表，含payer_member_id。PUT同路径接受布尔paused，仅群主/管理员可操作。POST /api/rooms/{id}/ai-generations/{generation}/stop允许群主、管理员或原触发者停止回复。

控制接口沿用共享请求桶协议。202表示已受理，前端等待任务最终结果后更新控制状态。业务Redis暂不可用时可排队，最终结果未知时明确提示未知。操作成功以服务端控制事务为界，上游取消检查约每100毫秒进行一次，数据库或网络延迟会影响实际耗时。

房间暂停会停止当前生成、保存其已输出快照并阻止新回复。恢复以当时房间last_seq为截止线，旧消息、冷场任务及接话链不会补发。历史仍用于后续新回复上下文。

ai_generations保存每次生成的触发者、尝试、快照和结束状态。停止与消息提交都先锁房间，再处理生成记录。完整回复先保存时，停止保留该完整消息；停止先提交时，迟到Final改用已停止快照并沿用generation_id去重。部分消息的stopped状态在实时和历史中一致，停止消息结束接话链。空内容或仅空白只结束草稿。AI已离开房间时，其停止快照无法保存，直接结束为saved并清除草稿，不阻塞该房间之后的停止或暂停。

## 运行与日志

数据库新增V6迁移，AI账号获得room_ai_controls的SELECT/INSERT及ai_generations的SELECT/INSERT/UPDATE，启动时db-users应用授权。迁移先于新应用与AI镜像启动。回滚应用时保留新增列和表。

结构化request_stage日志记录request_id、operation、stage、result、instance、elapsed_ms。HTTP返回X-Request-Id，MySQL/Redis事务阶段和共享桶受理/等待/执行关联业务请求编号，WS及AI消息用room_id与client_msg_id/generation_id关联。阶段可能重叠，避免将嵌套耗时相加。队列等待使用Redis的受理/领取时刻差。

AI日志记录generation_id、attempt及生成阶段耗时。Nginx输出JSON访问日志，仅记录方法、状态、上游、连接与响应耗时和上游请求编号，排除URL参数。所有这些字段排除Cookie、凭据、正文及搜索词。

Docker日志默认每个容器10MiB、保留3份，可用LOG_MAX_SIZE与LOG_MAX_FILES调整。Docker日志外层保留标准输出内容，应用结构化行可按event字段筛选。原Drogon及依赖库诊断日志仍有自身格式。
