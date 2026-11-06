# Spec Delta

## MODIFIED Requirements

### Requirement: 流式显示回复
AI 生成回复时，系统 SHALL 把生成中的文本逐段推送给房间里的在线成员，每段 MUST 带有本次生成的标识和 AI 成员。这些片段 MUST NOT 作为正式消息保存；用户主动停止时，系统 SHALL 将停止边界的部分内容保存为带“已停止”状态的正式消息。生成完成后，系统 MUST 把完整回复保存为该 AI 成员发送的普通消息，分配 `seq` 并按正常消息推送，消息 MUST 带有同一个生成标识，以便客户端用它替换生成中的文本。

#### Scenario: 逐段显示
- **WHEN** Claude 正在生成回复
- **THEN** 房间里的在线成员陆续收到带同一生成标识的文本片段

#### Scenario: 完成后成为普通消息
- **WHEN** Claude 生成完成
- **THEN** 房间里出现一条 Claude 发送的消息，带有 `seq` 和同一生成标识，历史查询能查到它

#### Scenario: 片段不入历史
- **WHEN** 生成过程中查询历史消息
- **THEN** 结果中没有生成中的片段
