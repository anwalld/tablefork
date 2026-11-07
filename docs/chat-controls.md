# 聊天阅读、搜索与AI控制

## 阅读与提及

房间列表返回read_seq、unread_count及mention_seq。未读只统计阅读位置之后其他成员的正式消息。升级时以当前房间last_seq回填阅读位置，加入/重新加入时建立同样的基线。

PUT /api/rooms/{id}/read接受整数seq。服务端按房间锁串行、单调推进并约束到房间当前last_seq。客户端在前台、最新消息区已成功展示并处于底部时更新。已读更新通过仅向该成员推送的read_updated通知其他页面刷新；新消息触发200ms合并刷新，10秒轮询作为补偿。各设备查询共享阅读位置。点击“有人@我”加载该消息周围上下文，成功展示后提交对应位置。搜索上下文可用“回到最新消息”恢复正常阅读。

人的提及复用成员@补全。消息保存时识别以空白分隔的完整@成员显示名，并在同一事务保存提醒关联。AI的原有文本提及规则继续使用。

## 搜索

GET /api/rooms/{id}/search支持q（最多200字符的字面子串，%和_不作通配符，区分大小写（消息正文列为utf8mb4_bin））、sender（成员ID）、from/to（Unix毫秒）、before（seq游标）及limit（1—100）。结果按seq倒序，next_before用于下一页。每次查询重新约束房间成员权限。结果定位复用messages接口的before/after分页。

V10建立ngram全文索引和专用空停用词表。基本汉字U+4E00至U+9FFF组成、至少两个字符的关键词使用完整短语候选，最终仍通过LOCATE与相同发送者、时间和seq条件复核。其他输入沿用字面查询。运行时确认ngram_token_size为2且停用词表为空；配置不符、索引缺失或查询失败时执行准确回退。停用词表应保持为空，修改分词配置后应在维护窗口重建索引并重新验证召回。

AI搜索结果与历史包含generation_outcome和generation_attempt。failed记录显示生成失败，保留的残文可搜索。空失败消息在空关键词查询中可见。

## AI控制

GET /api/rooms/{id}/ai-state返回paused及当前generation列表，含payer_member_id。PUT同路径接受布尔paused，仅群主/管理员可操作。POST /api/rooms/{id}/ai-generations/{generation}/stop允许群主、管理员或原触发者停止回复。

控制接口沿用共享请求桶协议。202表示已受理，前端等待任务最终结果后更新控制状态。业务Redis暂不可用时可排队，最终结果未知时明确提示未知。操作成功以服务端控制事务为界。提交后通过现有gRPC事件流发送带room_id与revision的ControlChanged提示。Python收到提示后批量读取SQL权威状态，默认每1秒补偿校验，AI_CONTROL_POLL_SECONDS可配置。连接建立时立即唤醒同步，单次SQL最多读取256个活动生成。重复或旧提示仅触发读取。数据库不可判定时取消上游，并保留租约任务等待状态可读后恢复。实际耗时包含数据库与网络延迟。

隔离测试栈实测：单次停止请求端到端约8ms（含控制事务提交、停止消息落库与提交后提示）；Python一次批量读取覆盖32个活动生成；C++每片段仍执行一次合并成员/入群/phase查询，未声称该查询已减少。提示丢失或SQL短暂不可用时由1秒周期校验与租约恢复补偿。

房间暂停会停止当前生成、保存其已输出快照并阻止新回复。恢复以当时房间last_seq为截止线，旧消息、冷场任务及接话链不会补发。历史仍用于后续新回复上下文。

ai_generations保存每次生成的触发者、尝试、快照和结束状态。停止与消息提交都先锁房间，再处理生成记录。完整回复先保存时，停止保留该完整消息；停止先提交时，迟到Final改用已停止快照并沿用generation_id去重。部分消息的stopped状态在实时和历史中一致，停止消息结束接话链。空内容或仅空白只结束草稿。AI已离开房间时，其停止快照无法保存，结束为finished并清除草稿，不阻塞该房间之后的停止或暂停。

## 生成结果与失败历史

ai_generations的phase区分generating、ready、finished，outcome区分completed、failed、stopped、cancelled、legacy_unknown。ready表示已有可重投的持久化结果，finished表示结果已处理。旧终态缺少消息证据时使用legacy_unknown。

同一逻辑回复复用generation_id，每次模型尝试递增attempt。当前快照与最近非空快照分别保存，新尝试的空快照保留前次候选；新非空快照原子替换候选。最终失败选最近非空尝试并保留来源content_attempt，全部为空也创建一条失败历史。详细失败原因只推送触发者，房间展示通用生成失败标签。

Final携带attempt。服务端校验角色、当前入群编号和尝试，使用SQL中的结果正文，在同一事务写消息并推进finished。消息唯一键恢复原seq，重复结果重投只补确认。generation_outcome为failed的记录从SQL及热缓存模型上下文排除，过滤后回源补足上下文。失败确认结束草稿，主动发言成功标记及后续AI接话按结束结果判断。已经持久化为failed的结果保留失败分类，迟到停止和暂停继续阻止新任务。生成开始前缺少key继续走提示事件。

历史、实时和搜索中的generation_outcome取completed、failed或stopped，generation_attempt表示正文来源尝试。既有stopped标记继续用于停止消息展示。

## 升级与回滚

V8扩展并按旧列和实际消息回填阶段/结果，V9删除生成status/stopped旧列，V10建立搜索索引。迁移前备份并停掉全部旧C++及Python写入者，在同一个维护窗口完成迁移和匹配应用升级。可在独立升级库将迁移停在V8，逐行核对旧列再验证V9。

收缩后的旧应用依赖已删除列，回滚需要匹配的备份恢复或经过验证的兼容迁移。迁移期间保持停写，恢复开放前核对旧数据、待投递任务和历史。可用`python3 tests/ai_personas/run_schema_tests.py --suite generation`验证空库和旧库升级，默认persona专项仍以V7为目标。

## 运行与日志

AI账号拥有room_ai_controls的SELECT/INSERT及ai_generations的SELECT/INSERT/UPDATE，启动时db-users应用授权。控制提示使用既有gRPC认证。数据库迁移先于匹配应用与AI镜像开放。

结构化request_stage日志记录request_id、operation、stage、result、instance、elapsed_ms。HTTP返回X-Request-Id，MySQL/Redis事务阶段和共享桶受理/等待/执行关联业务请求编号，WS及AI消息用room_id与client_msg_id/generation_id关联。阶段可能重叠，避免将嵌套耗时相加。队列等待使用Redis的受理/领取时刻差。

AI日志记录generation_id、attempt及生成阶段耗时。Nginx输出JSON访问日志，仅记录方法、状态、上游、连接与响应耗时和上游请求编号，排除URL参数。所有这些字段排除Cookie、凭据、正文及搜索词。

Docker日志默认每个容器10MiB、保留3份，可用LOG_MAX_SIZE与LOG_MAX_FILES调整。Docker日志外层保留标准输出内容，应用结构化行可按event字段筛选。原Drogon及依赖库诊断日志仍有自身格式。
