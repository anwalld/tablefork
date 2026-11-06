# Proposal

## Why

房间列表缺少未读与人的提及提醒，历史消息只能翻页。用户需要控制AI发言，并通过关联日志定位操作耗时。

## What Changes

- 增加跨设备已读进度、未读与人的@提醒，复用现有成员补全和历史定位。
- 增加关键词、发送者、时间筛选搜索。
- 增加AI停止及房间暂停/恢复，保留已停止部分回复，取消任务可跨重启生效。
- 增加结构化关联日志、HTTP/WS及异步阶段耗时与日志保留策略。

## Capabilities

### New Capabilities

- `room-attention` 负责已读、未读与人的提及提醒。
- `message-search` 负责房间消息筛选与上下文定位。
- `ai-controls` 负责生成停止、部分结果保存与房间暂停。
- `request-observability` 负责关联日志、阶段耗时及日志输出策略。

### Modified Capabilities

- `ai-replies` 增补流式回复的人工停止结果及终止任务恢复语义。

## Impact

MySQL新增迁移，C++服务与共享桶分派、Python生成状态、gRPC消息、Vue聊天界面及定向/完整回归。复用main已合入的离线重试和共享桶。
