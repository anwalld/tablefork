# Proposal

## Why

前端操作缺少反馈：多数按钮请求期间没有状态，结果回来后界面直接跳变，看起来像点了没反应。新建房间、邀请用户、创建私人AI都是常驻输入框，界面杂乱。邀请AI的下拉框有一个不能选的占位，只有一个角色时也要多选一次。人设只有小文本框，写长人设不方便；每次邀请都要重新填写人设。

## What Changes

- 按钮按下时有渐变和下沉反馈；提交类按钮请求期间禁用并显示“处理中…”，完成后用提示条说明结果。
- 新建房间、邀请用户、创建私人AI改为先点按钮、再弹出对话框填写。
- 邀请AI去掉占位，默认选中第一个可用角色；只有一个可邀请角色时直接显示名字。点“邀请”后在弹窗里确认本房间人设。
- 人设编辑器：编辑/预览（Markdown，DOMPurify清洗）、导入 .md 文件、示例模板、按UTF-8字节显示与16KiB上限的对比。
- 角色默认人设：创建时可写，在“我的AI”里点“人设”修改；邀请时自动预填本房间人设。新增迁移V12和接口`PUT /api/ai/roles/{member}/persona`。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `ai-personas`：增加私人角色默认人设及邀请预填。

## Impact

- 前端：`ChatView`、`MembersPanel`，新增`AppDialog`、`PersonaEditor`、`lib/markdown.js`，新增依赖`marked@15.0.12`、`dompurify@3.2.6`。
- 后端：`ai_profiles.default_persona`（V12，可空，16KiB检查）；创建角色可带`persona`；角色列表返回`persona`；新接口仅创建者可用，经请求暂存可恢复并按请求ID去重。
- 每房间人设、版本、审批与生成快照规则不变；AI服务和gRPC不变。
