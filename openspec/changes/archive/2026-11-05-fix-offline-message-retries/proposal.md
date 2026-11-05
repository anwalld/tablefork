# Proposal

## Why

当前 ChatSocket 在离线且实际发送零帧时仍扣次数，20 秒即失败；暂时性错误也会在 4 次尝试后结束，无法覆盖已有 30 秒恢复窗口。用户已同意先实现前端待发重试这一独立小任务。

## What Changes

- 每条待发消息使用从用户发送开始的 60 秒总期限，替代 4 次即失败的终止条件；单次 ACK 等待仍为 5 秒。
- 离线时保留待发消息，不计发送次数，不重复启动 ACK 等待；重连后用原 ID 和内容补发。
- 可重试错误在总期限内退避重发；服务端限流等待仍被尊重，重连不能跳过等待。
- 成功、不可重试错误、超时、关闭或会话结束时清理定时器，防止迟发与重复结算。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `realtime-messaging`：新增客户端待发消息有界重试行为；不改变持久化 ACK、服务端权限检查与去重契约。

## Impact

仅 web/src/lib/chat-socket.js、对应测试与本 change 文档；不新增依赖。页面内存待发列表不保证刷新/关闭后恢复，不实现后端协调闸门或临时队列。
独立分支 codex/fix-offline-message-retries，基于 PR #10 提交 36239e3；不混入该 PR 的后台交接提交。
