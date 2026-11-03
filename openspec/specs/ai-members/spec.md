# ai-members Specification

## Purpose

管理系统预置的 AI 成员：有哪些 AI、它们属于哪个服务商，以及房间成员如何申请把 AI 加入房间、群主和管理员如何审批和移出 AI。

## Requirements

### Requirement: 预置 AI 目录
系统 SHALL 预置 DeepSeek、GPT、Claude、Gemini 四个 AI 成员，每个 AI 有唯一标识、显示名和所属服务商（`deepseek`、`openai`、`anthropic`、`gemini`）。系统 SHALL 向已登录用户返回 AI 目录，目录 MUST 标明当前用户是否已填写每个 AI 所属服务商的 API key。

#### Scenario: 查询 AI 目录
- **WHEN** 已登录用户查询 AI 目录
- **THEN** 系统返回四个预置 AI 的标识、显示名、服务商，以及当前用户是否已有对应的 key

#### Scenario: 未登录
- **WHEN** 未登录的请求查询 AI 目录
- **THEN** 系统返回未认证错误

### Requirement: 申请邀请 AI
系统 SHALL 允许房间成员按标识申请把预置 AI 加入房间。申请人 MUST 已填写该 AI 所属服务商的 API key，否则系统 MUST 拒绝并返回 `api_key_required` 错误，错误中 MUST 包含缺少的服务商。申请人是群主或管理员时，AI MUST 立即加入房间；否则系统 MUST 创建一条待审批的申请，并通知房间里在线的群主和管理员。同一房间对同一 AI 的待审批申请 MUST 只有一条。

#### Scenario: 群主邀请
- **WHEN** 已填写 Anthropic key 的群主邀请 Claude
- **THEN** Claude 立即成为房间成员，房间里的在线成员收到成员变化

#### Scenario: 普通成员申请
- **WHEN** 已填写 Anthropic key 的普通成员申请邀请 Claude
- **THEN** 系统创建待审批的申请，Claude 尚未加入，在线的群主和管理员收到通知

#### Scenario: 没有 key 时申请
- **WHEN** 没有填写 Anthropic key 的成员申请邀请 Claude
- **THEN** 系统返回 `api_key_required` 错误并指明服务商 `anthropic`，不创建申请

#### Scenario: 重复申请
- **WHEN** 已有一条邀请 Claude 的待审批申请，另一名成员再次申请
- **THEN** 系统返回成功，待审批的申请仍只有一条

#### Scenario: AI 已在房间里
- **WHEN** 成员申请邀请一个已在房间里的 AI
- **THEN** 系统返回成功，房间里只有一个该 AI 成员

#### Scenario: 非成员申请
- **WHEN** 非房间成员申请向该房间邀请 AI
- **THEN** 系统返回房间不存在错误

#### Scenario: 未知的 AI
- **WHEN** 房间成员用不存在的标识申请邀请 AI
- **THEN** 系统返回 `ai_not_found` 错误

### Requirement: 审批 AI 邀请
群主和管理员 SHALL 能查看本房间待审批的 AI 邀请，并批准或拒绝。批准后 AI MUST 立即加入房间；拒绝后申请被删除。处理结果 MUST 通知仍在房间里的申请人。审批人 MUST NOT 需要填写对应的 API key。其他成员 MUST NOT 查看或处理申请。

#### Scenario: 批准
- **WHEN** 管理员批准邀请 Claude 的申请
- **THEN** Claude 成为房间成员，申请被删除，申请人收到通知

#### Scenario: 拒绝
- **WHEN** 群主拒绝邀请 Claude 的申请
- **THEN** Claude 没有加入，申请被删除，申请人收到通知

#### Scenario: 普通成员处理申请
- **WHEN** 普通成员试图查看、批准或拒绝申请
- **THEN** 系统返回 `forbidden` 错误

#### Scenario: 申请已被处理
- **WHEN** 两名管理员先后处理同一条申请
- **THEN** 第一个处理生效，第二个返回申请不存在

### Requirement: 移出 AI
群主和管理员 SHALL 能把 AI 移出房间，其他成员 MUST NOT 移出 AI。AI 已发送的消息 MUST 继续保留。AI MUST NOT 主动退出房间，也 MUST NOT 邀请其他成员。

#### Scenario: 管理员移出 AI
- **WHEN** 管理员移出 Claude
- **THEN** Claude 不再是房间成员，它此前发送的消息仍在历史中

#### Scenario: 普通成员移出 AI
- **WHEN** 普通成员试图移出 Claude
- **THEN** 系统返回 `forbidden` 错误，Claude 仍在房间里

#### Scenario: 移出后再邀请
- **WHEN** Claude 被移出后，有 key 的群主再次邀请它
- **THEN** Claude 重新成为房间成员
