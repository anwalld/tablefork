# ai-members Specification

## Purpose

管理系统预置的 AI 成员：有哪些 AI、它们属于哪个服务商，以及房间成员如何申请把 AI 加入房间、群主和管理员如何审批和移出 AI。

## Requirements

### Requirement: 预置 AI 目录
系统 SHALL 向已登录用户提供DeepSeek、GPT、Claude、Gemini四类底层模型目录及调用者自己的私人角色目录，标明服务商及调用者是否有相应key。模型类型 MUST 与角色成员身份分离，同一类型允许多个角色。旧四个公共成员 MUST 仅为已有房间兼容保留并标记旧版，不能作为新增邀请选项。

#### Scenario: 查询 AI 目录
- **WHEN** 已登录用户查询AI目录
- **THEN** 返回四类模型、其对应key状态及调用者自己的角色，其他人的私人角色不在目录中

#### Scenario: 未登录
- **WHEN** 未登录的请求查询 AI 目录
- **THEN** 系统返回未认证错误

### Requirement: 申请邀请 AI
系统 SHALL 允许房间成员按成员ID邀请自己创建的私人AI并提供房间人设。申请人 MUST 已填写该AI服务商的key，否则返回含服务商的`api_key_required`。群主/管理员邀请自己的角色立即入群，普通成员创建待审批项并通知在线管理者。房间与角色相同的申请 MUST 只有一条，重复请求不得静默改写已提交人设。知道他人角色ID MUST NOT 获得邀请权限。

#### Scenario: 群主邀请
- **WHEN** 已填写Anthropic key的群主邀请自己创建的Claude A并提交人设
- **THEN** 该角色立即入群，生成新的入群编号，房间在线成员收到成员变化

#### Scenario: 普通成员申请
- **WHEN** 已填写Anthropic key的普通成员申请邀请自己的Claude A
- **THEN** 保存其提交的人设及待审批项，角色尚未加入，在线群主和管理员收到通知

#### Scenario: 没有 key 时申请
- **WHEN** 没有Anthropic key的创建者申请邀请自己的Claude A
- **THEN** 返回`api_key_required`并指明`anthropic`，不创建申请

#### Scenario: 重复申请
- **WHEN** 同一创建者再次申请同一房间同一角色且人设相同
- **THEN** 返回成功，申请仍只有一条；若另交不同人设则明确冲突，已提交人设不变

#### Scenario: AI 已在房间里
- **WHEN** 创建者再次邀请已在房间的角色
- **THEN** 返回成功，不新增成员、不更换入群编号、不把邀请当成人设编辑

#### Scenario: 非成员申请
- **WHEN** 非房间成员申请向该房间邀请自己的角色
- **THEN** 系统返回房间不存在错误

#### Scenario: 未知的 AI
- **WHEN** 房间成员用不存在或不属于自己的角色ID申请邀请
- **THEN** 返回相同的`ai_not_found`，不泄露其他人的私人目录

#### Scenario: 管理者邀请他人角色
- **WHEN** 群主或管理员知道另一人的角色ID并申请邀请
- **THEN** 仍返回`ai_not_found`，管理员权限不代替创建者所有权

### Requirement: 审批 AI 邀请
群主和管理员 SHALL 能查看本房间待审批邀请并批准或拒绝，其他成员 MUST NOT 查看或处理。批准 MUST 重新确认角色为私人角色、申请人为创建者且仍是房间成员，使用申请中保存的人设入群；拒绝删除申请。结果 MUST 通知仍在房间的申请人。审批人 MUST NOT 需要对应key，审批不得替创建者修改人设。

#### Scenario: 批准
- **WHEN** 管理员批准有效的私人Claude A邀请
- **THEN** 角色按申请中人设加入，申请删除，申请人收到通知

#### Scenario: 拒绝
- **WHEN** 群主拒绝邀请Claude A的申请
- **THEN** 角色不加入，申请删除，申请人收到通知

#### Scenario: 普通成员处理申请
- **WHEN** 普通成员试图查看、批准或拒绝申请
- **THEN** 系统返回`forbidden`

#### Scenario: 申请已被处理
- **WHEN** 两名管理员先后处理同一申请
- **THEN** 第一次生效，第二次返回申请不存在

#### Scenario: 创建者已退出
- **WHEN** 批准时创建者已退出或被踢出/封禁
- **THEN** 角色不入群，残留申请被清理，不能让旧申请绕过当前成员检查

### Requirement: 移出 AI
群主和管理员 SHALL 能按具体成员ID移出AI，其他成员 MUST NOT 手动移出AI。创建者失去房间成员资格时系统 MUST 自动移除其私人AI。AI已发正式消息 MUST 保留。AI MUST NOT 主动退出或邀请成员，旧预置AI移出后 MUST NOT 再次新增邀请。

#### Scenario: 管理员移出 AI
- **WHEN** 管理员移出Claude A
- **THEN** 该角色不再是房间成员，入群编号失效，其已保存消息继续保留

#### Scenario: 普通成员移出 AI
- **WHEN** 普通成员尝试手动移出Claude A
- **THEN** 返回`forbidden`，角色仍在房间

#### Scenario: 移出后再邀请
- **WHEN** 私人角色移出后，仍在房间且有对应key的创建者重新邀请它
- **THEN** 沿用申请者角色对应的审批规则重新入群并取得新编号
