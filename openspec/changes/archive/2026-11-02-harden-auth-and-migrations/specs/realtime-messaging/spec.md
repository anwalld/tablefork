# Spec Delta

## ADDED Requirements

### Requirement: 按用户限制发消息频率
系统 SHALL 按用户统计一段时间内发送的消息数，与连接数和房间无关。超过限额时，系统 MUST 拒绝该消息并返回可重试的 `rate_limited` 错误和需要等待的秒数，消息 MUST NOT 被保存或推送。

#### Scenario: 限额内发送
- **WHEN** 用户在时间窗口内发送的消息数未超过限额
- **THEN** 消息按正常流程保存并确认

#### Scenario: 超过限额
- **WHEN** 用户在时间窗口内发送的消息数超过限额
- **THEN** 超出的消息收到 `rate_limited` 错误，包含等待秒数和原 `client_msg_id`，消息不被保存

#### Scenario: 多连接共享限额
- **WHEN** 同一用户通过多个连接或在多个房间发送消息
- **THEN** 所有消息计入同一个限额

#### Scenario: 不影响其他用户
- **WHEN** 某用户已被限流
- **THEN** 同一房间的其他用户发送消息不受影响

#### Scenario: 窗口过后恢复
- **WHEN** 被限流的用户等待时间结束后，用相同 `client_msg_id` 重发
- **THEN** 消息被正常保存并确认
