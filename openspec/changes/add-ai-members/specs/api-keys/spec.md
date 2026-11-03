# Spec Delta

## Purpose

让用户按服务商保存自己的模型 API key，AI 调用的费用由触发调用的用户自己承担；平台负责安全地保存这些 key。

## ADDED Requirements

### Requirement: 保存 API key
系统 SHALL 允许已登录用户为 `deepseek`、`openai`、`anthropic`、`gemini` 中的每个服务商保存一个 API key。保存前系统 MUST 用该 key 向服务商发起一次验证调用，验证失败时 MUST 拒绝保存并返回 `api_key_invalid` 错误。再次保存同一服务商的 key MUST 替换旧 key。

#### Scenario: 保存有效的 key
- **WHEN** 用户为 `anthropic` 提交一个能通过验证的 key
- **THEN** 系统保存该 key 并返回成功

#### Scenario: 保存无效的 key
- **WHEN** 用户提交的 key 被服务商拒绝
- **THEN** 系统返回 `api_key_invalid` 错误，不保存

#### Scenario: 替换 key
- **WHEN** 用户为已有 key 的服务商再次保存一个有效的 key
- **THEN** 之后的调用使用新 key

#### Scenario: 未知的服务商
- **WHEN** 用户为不支持的服务商保存 key
- **THEN** 系统返回参数错误

### Requirement: API key 的保密
系统 MUST 只保存加密后的 API key，加密主密钥 MUST NOT 保存在数据库中。任何接口 MUST NOT 返回 key 的原文，只能返回服务商、末 4 位和保存时间。key 的原文 MUST NOT 出现在日志中。

#### Scenario: 查询已保存的 key
- **WHEN** 用户查询自己的 API key
- **THEN** 系统返回每个已保存服务商的末 4 位和保存时间，不包含原文

#### Scenario: 数据库中的 key
- **WHEN** 检查数据库中保存的 key
- **THEN** 其中不包含 key 的原文

#### Scenario: 其他用户的 key
- **WHEN** 用户查询 API key
- **THEN** 只返回自己的 key，看不到其他用户的

### Requirement: 删除 API key
系统 SHALL 允许用户删除某个服务商的 API key。删除后，需要该 key 的调用 MUST 按“缺少 key”处理，已在房间里的 AI 成员 MUST NOT 因此被移出。

#### Scenario: 删除 key
- **WHEN** 用户删除 `anthropic` 的 key
- **THEN** 查询结果中不再有 `anthropic`，由该用户触发的 Claude 调用按缺少 key 处理

#### Scenario: AI 留在房间里
- **WHEN** 用户删除了 key，而 Claude 已在房间里
- **THEN** Claude 仍是房间成员
