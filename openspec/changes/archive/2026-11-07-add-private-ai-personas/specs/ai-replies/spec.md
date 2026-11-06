# Spec Delta

## MODIFIED Requirements

### Requirement: 提及 AI
系统 SHALL 支持按房间内具体AI成员ID提及，候选展示名称和创建者，一条消息可提及多个角色。有结构化AI目标时 MUST 以这些ID为准，目标失效不得按名字改选别人。旧`@显示名`文本提及 SHALL 在未提供结构化AI目标且房间内名称唯一时继续兼容且不区分大小写；名称歧义时 MUST NOT 触发所有同名AI。结构化成员ID MUST 验证目标当前在房间，显示文本不能替代身份校验。

#### Scenario: 提及房间里的 AI
- **WHEN** 用户从候选选中房间中的Claude A
- **THEN** 仅所选成员ID被提及，其他同名角色不被一起触发

#### Scenario: 提及不在房间里的 AI
- **WHEN** 用户提交不在该房间的AI成员ID或`@gemini`文本且Gemini不在房间
- **THEN** 该目标不被视为有效提及

#### Scenario: 唯一旧名称兼容
- **WHEN** 用户发送`@claude 你好`且房间内只有一个名为Claude的AI
- **THEN** 沿用旧文本提及行为

#### Scenario: 歧义文本
- **WHEN** 房间内两个AI都名为Claude A，用户只手输`@Claude A`
- **THEN** 不按该歧义文本触发任一个同名AI，客户端提示选择具体角色

#### Scenario: 已选角色在处理前退出
- **WHEN** 用户已选中的AI在处理前退出，房间中仍有一个同名AI
- **THEN** 不退回文本匹配另一个角色；发送时目标已失效则明确拒绝并不保存消息

### Requirement: 调用费用由触发者承担
每次AI调用 MUST 使用触发者在对应服务商的API key：多人房间为发出提及的人类成员，一人房间包括主动发言为房间里的人类成员，接话链为引出这条链的人类成员。角色创建者身份 MUST NOT 改变付款归属，系统 MUST NOT 使用其他用户或平台的key。

#### Scenario: 多人房间按提及者付费
- **WHEN** 用户甲提及用户乙创建的Claude A
- **THEN** 使用用户甲的Anthropic key，不使用乙的key

#### Scenario: 接话链按起点付费
- **WHEN** 用户甲引出Claude A回复，GPT B随后接话
- **THEN** GPT B使用甲的OpenAI key，不按角色创建者换人

#### Scenario: 触发者缺 key
- **WHEN** 触发者没有Anthropic key而Claude A创建者有key
- **THEN** 不调用模型，沿用只向触发者发送缺key提示的规则

## ADDED Requirements

### Requirement: 按角色和人设版本恢复
每个逻辑回复 SHALL 绑定具体AI成员、该次入群编号及开始时的人设快照。重试和进程恢复 MUST 使用原generation_id、原成员及同版人设，不得因另一同模型角色存在或人设已更新而换角色/换版。迟到结果 MUST 同时满足当前成员关系和入群编号；停止/暂停与已有生成去重规则继续适用。

#### Scenario: 两个 Claude 不串任务
- **WHEN** 同一房间有两个Claude角色，其中一个正在生成且AI进程重启
- **THEN** 恢复仍属于原角色及原人设快照，不切换到另一个Claude

#### Scenario: 退群重邀后结果重投
- **WHEN** 原入群结果重投时角色已重新入群
- **THEN** 旧入群编号被拒绝，不因成员ID仍相同而保存或广播

#### Scenario: 停止后修改人设
- **WHEN** 回复已被有权限用户停止，随后人设被编辑并发生恢复扫描
- **THEN** 原回复不重新开始，已停止的历史内容不被覆盖
