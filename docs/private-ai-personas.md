# 私人AI角色与房间人设

状态：已随[PR #15](https://github.com/anwalld/CppAiWebServer/pull/15)合并进main（合并提交`5f33431`），change已归档为[2026-11-07-add-private-ai-personas](../openspec/changes/archive/2026-11-07-add-private-ai-personas/)，主规格见`openspec/specs/ai-personas`。原服务尚未部署。

## 设计更改对照表

| 原来 | 现在 | 用途 |
| --- | --- | --- |
| 一个模型对应一个公共AI | 一个模型可对应多个独立member_id | 同用户、同名字、同模型仍是不同角色 |
| 公共AI目录 | 四类模型目录＋只列自己的私人角色 | 其他人不能从目录获取或邀请自己的角色 |
| 无创建者 | creator_user_id及旧版标记 | 服务端校验所有权，不凭管理员身份越权 |
| TOML全局人设 | 私人角色按房间保存人设与版本 | 修改学习群不影响闲聊群；旧AI默认人设保持兼容 |
| 生成直接读当前全局配置 | 开始时固定SQL人设快照，重试恢复沿用 | 编辑不打断当前生成、不让重试换人设 |
| 退群重邀成员ID不变 | 每次入群有新admission_id | 旧Chunk、Final、Failed不串到新入群 |
| 前端草稿只认成员ID | 草稿同时携带入群编号并与当前成员配置比对 | 已广播但迟到的旧片段也不会混进重邀后的界面 |
| 文本名称就是提及目标 | 选择成员ID并随WS和重试保留 | 同名AI只触发选中的角色 |
| 创建者退群不联动AI | 退出、踢出、封禁同事务移除角色和申请 | 保留正式历史，取消未完成生成 |

## 页面操作

打开房间的“成员”面板，在“我的AI”中选择底层模型并填写名称创建。创建角色不要求已有key；邀请时需要创建者有对应服务商key，并填写本房间人设（可留空）。普通成员申请需审批，群主/管理员仅对自己的角色可直接邀请。

成员列表显示模型类型和created by。仍在房间内的创建者可编辑对应人设，管理员不能替别人编辑；提交携带当前版本，过期页面收到冲突后须重新加载。保存只影响下一轮开始的回复。

谁触发谁用自己的key，接话和冷场保留原付款归属；创建者不替其他人付费。停止/暂停沿用既有权限、部分保存和恢复不补答规则。

## HTTP接口

接口均需登录，沿用共享桶的`X-Queue-Owner`、稳定`X-Request-Id`及202任务查询；202只表示受理。写接口复查身份并使用SQL同事务请求记录，重放不重复创建角色或递增版本。

| 操作 | 接口与参数 | 结果 |
| --- | --- | --- |
| 模型目录 | GET `/api/ai/models` | `models`数组，含model_slug/name/provider/has_key |
| 我的角色 | GET `/api/ai/roles` | 为兼容旧客户端，数组字段名仍是`agents`；只列自己的私人角色 |
| 创建角色 | POST `/api/ai/roles`，`{model_slug,name}` | 201，包含id/slug/name/model_slug/creator_user_id/creator_name |
| 邀请自己的角色 | POST `/api/rooms/{room}/ai-members`，`{member_id,persona}` | joined或pending；member_id为正整数 |
| 审批列表 | GET `/api/rooms/{room}/ai-requests` | 仅群主/管理员可读，包含具体角色id和申请者 |
| 批准/拒绝 | POST `/api/rooms/{room}/ai-requests/{member}/approve`或reject | 沿用审批规则，批准不能改写申请人设 |
| 修改本房间人设 | PUT `/api/rooms/{room}/ais/{member}/persona`，`{persona,persona_version}` | 返回递增后的persona_version；旧版本409 |
| 移出角色 | DELETE `/api/rooms/{room}/ai-members/{member}` | 群主/管理员可移出本房间角色 |

`GET /api/ai-agents`保留为私人角色目录的兼容入口。旧slug形式仅对调用者自己的具体私人角色保留兼容，不能据此邀请四个公共预置AI。旧预置AI已有房间可继续聊天、可移出，不能新增邀请或批准旧申请。

角色名为1—64个有效UTF-8字符且不可全为空格或包含控制字符；模型类型固定四种。人设按UTF-8字节计最多16KiB，空串合法。版本从1开始。角色ID不是权限凭证，入群编号也不是查询/登录凭证。

## WS提及与生成协议

发消息仍使用原client_msg_id，在frame中可携带`mention_ids: [member_id]`。发送时目标已不在房间则明确拒绝，不提前保存消息；重发同编号不得换内容或有效AI提及目标。人的文本@提醒继续兼容。

唯一名称的旧`@名称`文本在没有结构化AI目标时继续识别；有非空`mention_ids`时，AI目标以该数组为准，多个目标需逐个选择。选中的角色在处理前退出时不退回按名字匹配其他同名AI。同名时需从候选选择具体角色，不能全触发；一人房间也不会因歧义@文本随机选同名AI。人的提及不会占用AI目标数组。前端重连/重试保留原提及ID；编辑删除已选择文本后会清除相应选择。

gRPC快照增加model_slug、creator_name、房间人设版本与入群编号，Chunk/Final/Failed携带入群编号。生成开始时读取最新SQL配置，SQL登记成功后固定人设快照，重试/重启恢复以SQL为准，并同步回Redis活动任务。旧四字段Redis成员状态仍能读，旧预置AI可使用迁移回填的入群编号恢复。

私人成员缺少编号、编号不匹配或没有注册生成记录的Final不能入库。片段广播持房间共享锁，与离房事务序列化；旧入群失败事件不能影响新生成。AI移出/创建者离房取消未完成内容，不把移出当作人工“停止并保存部分”。已正式保存的消息不删除。

## 验证与运维

- V7只增加结构，V1—V6不改。AI账号仅增加模型目录与房间人设SELECT权限，继续不能读取用户表或人类成员user_id。
- 升级需C++、Python及前端配套；出现私人角色后，不可直接回退只认四个预置AI的旧应用。原服务升级需另行授权与备份。
- `python3 tests/ai_personas/run_schema_tests.py`运行一次性MySQL/Flyway新库与升级测试；需要本机相应镜像，默认不拉取未知软件。
- `UNIT_TEST_IMAGE=... COMPONENT_BUILD_DIR=... tests/buffering/run_component_tests.sh`运行真实C++/MySQL/Redis服务探针，包含重邀旧Final、Chunk和Failed的正负验证。
- 完整回归和浏览器均使用独立`cppai-personas`栈及fake provider，具体日志、退出码、源码哈希和限制见交接。未增加真实模型费用测试。
