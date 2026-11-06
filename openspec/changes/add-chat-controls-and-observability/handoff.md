# 下一批聊天功能交接

2026-11-06，提交前收尾版。**让他double check。** 本文件替代早期“停止等待/2项完成”的临时交接。

## 当前状态与授权

实现工作区/private/tmp/cppai-chat-controls，分支codex/chat-controls-and-observability，基于main a1bfbc020dedfe316e1428b099204e4f503d0a61（PR12已合并）。本批尚未提交/创建PR，OpenSpec任务8/10完成；剩余最终源码完整回归和准确PR head CI。

用户授权本批apply→两轮review→verify→CI/PR→archive，且已授权分支CI成功后合并main并检查main CI，然后整理已完成分支/工作区。以后先在功能分支/PR跑CI，最后一次合并main并检查main CI，参见docs/development-workflow.md。保留未合并/有改动的Claude工作，不清理无关内容。

## 做了什么

- V6迁移增加room_members.read_seq、message_mentions、messages.stopped、room_ai_controls和ai_generations；升级/重新加入按当前最后消息建立阅读基线。
- 房间列表未读/@、单调共享已读、仅本人接收read_updated通知、200ms合并刷新与10秒补偿轮询；前台成功展示后推进阅读。人的补全与提醒、搜索/上下文、撤权后迟到上下文保护。
- 关键词字面搜索、发送者/时间/游标组合筛选及成员权限，复用历史上下文。
- 普通成员停自己触发的生成，群主/管理员停任意生成及暂停/恢复房间。部分结果用原generation_id保存一次并标记stopped；空停止无空消息；迟到Final按持久停止快照归一；跨重启恢复、暂停截止线及不续旧接话。
- C++/Python结构化阶段日志，HTTP/WS/队列/SQL/Redis/AI编号关联、稳定摘要处理非法编号、真实排队等待与即时flush；Nginx排除参数/正文；Docker默认10MiB×3轮转。
- CI增加真实Redis队列及迁移/SQL/HTTP专项；测试日志使用通用临时目录。
- docs/follow-up-plan.md保留先前全部产品决定，旧归档计划改为指向新文档的入口；docs/chat-controls.md说明协议与运维。

## 测了什么

详见verification-report.md和review-report.md。关键结果：本地完整260 passed/27 skipped、严格9 passed、恢复9.512秒、113条确认消息恰好一次、旧主回归53样本零连接。完整运行早于最后遥测/即时同步细化；最终源码有C++构建/ctest、108前端、81 Python、18真实Redis、4真实SQL/HTTP、3真实TLS网关专项及最终镜像构建，最终源码全量由PR CI确认。

最终日志探针1228条记录，验证HTTP/WS/队列/SQL/Redis/AI关联，队列真实等待1133ms，字段白名单与固定敏感标记排除通过。浏览器实际验证未读/@定位、双页已读、搜索上下文、暂停及停止标签。部分停止的确定性证明来自阻塞provider和真实SQL，浏览器停止时接近生成结束。

原始日志均在/private/tmp，列表和失败历史见verify报告。两轮review为同一执行者完成。首次SQL锁权限失败、夹具污染完整回归、wait_ms缺失和stdout缓冲失败均保留，没有放宽原5秒首次响应、30秒恢复、不断线和旧主零连接断言。

## 未做/限制

没有新增真实模型调用费用，使用fake provider；未纳入备份、撤回、追加接话硬上限、上下文重置及旧WS根因调查。日志原Drogon/依赖诊断仍有自身格式；轮转限制在各容器。暂停/停止的上游取消约100ms轮询，数据库/网络延迟会影响生效耗时；202只表示排队受理。

当前尚无本批PR、CI成功记录或归档；不要把基线PR12的CI当作本批通过。ai_generations失败终态复用saved且内容为空，应由接手者复核是否需未来独立失败状态。

## 还要做什么

1. 提交前核对diff与handoff-source-hashes.json，排除.env、证书、compose.chat-test.yml和生成物。创建PR并跟踪准确head SHA完整CI。修复真实失败，保留原断言。
2. 成功后更新任务4.1/4.2和verify报告，同步5项delta并archive；归档提交推同一PR，再确认最终head CI。
3. 合并main，检查准确merge SHA的CI。记录PR、head、merge SHA、CI URL与archive路径到本文件和用户入口文档。
4. 核对已完成且干净的worktree/分支再清理，记录原SHA。保留当前仍挂载测试栈的工作区、未合并/有改动的Claude分支。
5. 原主目录有未跟踪旧计划，与新tracked redirect同路径：先验证docs/follow-up-plan.md完整保留旧内容，备份到/private/tmp，再只删除该已备份旧文件后快进main，避免丢失讨论。
6. 完成交付后删除已暂停的一次性任务apply-verify-ci-pr-archive，避免重复执行。

## double check重点

让他double check：SQL控制与Redis生成状态在取消/失败/租约接手/迟到Final中的一致性；memo→room锁序与AI最小权限FOR SHARE；已读更新只向本人投递、撤权后上下文迟到保护、搜索成员权限；日志编号跨异步阶段、字段白名单和热路径开销。不要从主目录重复实现已存在能力。

## 本地现场

隔离Compose项目cppai-chat-controls，HTTPS https://localhost:18486，网络172.33.0.0/16。原业务栈保留。临时夹具必须docker compose run --no-deps，完整回归时禁止重配依赖。/private/tmp/run-chat-full.sh封装隔离完整测试。当前正式web和app镜像已构建；两个浏览器页为测试账户。

.env/证书为忽略文件，禁止输出或提交。compose.chat-test.yml是本机镜像名覆盖，不提交。AI测试关闭付费provider。Git元数据位于原主仓库，提交/推送需要现有沙箱批准方式。gh CLI可用，GitHub连接器此前对私库404；使用既有认证，不打印token。
