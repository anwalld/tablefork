# Spec Delta

## Purpose

让房间里的 AI 在受控范围内调用管理员配置的工具和 MCP 服务器，并保证有副作用的调用必须经人确认。

## ADDED Requirements

### Requirement: 工具来源
AI 可用的工具 SHALL 只来自部署配置中登记的内置工具和 MCP 服务器，每个工具 MUST 标注为只读或有副作用。用户和 AI MUST NOT 在运行时添加工具。

#### Scenario: 未登记的工具不可用
- **WHEN** AI 尝试调用部署配置中没有登记的工具
- **THEN** 调用被拒绝，AI 收到工具不存在的结果

### Requirement: 只读工具直接调用
协作模式下，AI SHALL 能直接调用只读工具，调用和结果摘要 MUST 记录在房间时间线中。

#### Scenario: 查询资料
- **WHEN** AI 调用只读的检索工具
- **THEN** 工具直接执行，时间线中出现调用记录和结果摘要

### Requirement: 有副作用的工具需确认
AI 调用有副作用的工具时 SHALL 先发出确认请求，只有在有权的人明确回复“继续”后才执行；超时 MUST NOT 执行。

#### Scenario: 等待确认
- **WHEN** AI 请求调用有副作用的工具
- **THEN** 工具不立即执行，房间收到确认请求

### Requirement: 调用失败
工具调用失败或超时时，AI SHALL 收到失败结果并照常完成回复，MUST NOT 自动重试有副作用的调用。

#### Scenario: 工具超时
- **WHEN** 只读工具调用超时
- **THEN** AI 收到超时结果并据此回复
