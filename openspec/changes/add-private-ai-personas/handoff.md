> 2026-11-07更新（Claude）：最终源码完整回归退出0，任务6.1完成；堆损坏未复现但根因未查明。以[Claude接手调试记录](claude-debug-2026-11-07.md)为准。未提交、未建PR。

> 2026-11-06 21:55更新。已达用户设定的10%剩余额度交接线。最新修复、未解堆损坏与正在运行的ASAN回归见[继续调试交接](resume-debug.md)。verify仍未通过。

# 私人AI角色与房间人设交接

2026-11-06（Asia/Shanghai）。**让他double check。**

## 接手位置与范围

- 工作区：`/private/tmp/cppai-private-ai-personas`。
- 分支：`codex/private-ai-personas`，基线为PR #13合并提交`9dae3ff9cbe90966acc0944e51dbb4e6e1b7b853`。
- change：`openspec/changes/add-private-ai-personas`。完整需求、设计和任务在本目录；[接口和设计更改对照表](../../../docs/private-ai-personas.md)。
- 用户先要求只完成规划/数据结构后停下；该阶段已完成并汇报，[phase-1-report.md](phase-1-report.md)是历史记录。用户随后明确授权继续apply、verify和交接，旧“只做前两项”不是当前阻塞。
- 没有提交、推送、创建PR、合并、归档或升级原服务。不要把当前HEAD的PR #13 CI当作本功能CI；功能代码仍为本工作区未提交改动。
- 当前为最终交接版：功能实现和专项完成，任务14/15；最后故障复验失败，verify未全通过，不能归档/合并/部署。准确结果见“交付验收结果”和verification-report.md。

## 做了什么

1. 建立完整OpenSpec，三份delta覆盖ai-members、ai-replies及新ai-personas。V7拆开模型、私人角色、房间人设；既有成员ID和历史保留。
2. 模型目录、私人角色创建/目录、按具体角色邀请/审批/移出、房间人设版本编辑。写入口接共享桶与SQL同事务请求记录，创建角色可恢复且同编号不重复创建。
3. 创建者身份由SQL校验，管理员不能邀请/编辑他人私人角色；人设编辑有版本冲突。邀请仍要求创建者有对应key，调用仍由触发者付费。
4. Python启动校验改查ai_model_types；gRPC快照与动作携带模型类型、创建者、入群编号。新AI账号权限仅增加模型目录和房间人设SELECT。
5. SQL登记生成开始时固定人设快照与版本，重试/重启恢复使用原快照，并同步回Redis活动任务。旧四字段成员状态可读，旧预置AI兼容回填后的入群编号。
6. 创建者退出/被踢/被封禁时，同事务清理自己的房间AI和申请，并将未完成生成终结；正式历史保留。重邀新编号；Chunk/Final/Failed检查当前入群，旧结果不得混入。
7. 前端“成员→我的AI”创建、邀请填写本房间人设、创建者编辑、created by/模型/旧版标记。@选择传真实成员ID，离线重试保留；歧义提示选择，已选ID失效不能按名字改选别人。
8. 前端草稿带admission_id，成员重邀后丢弃旧编号片段。服务器片段广播原先持房间共享锁和退出事务串行；2026-11-07改为一次只读校验后再广播，不再在发布期间持锁或占用连接，隔离靠客户端按admission_id过滤（见claude-debug-2026-11-07.md）。

## 设计更改对照表

| 原来 | 现在 |
| --- | --- |
| 一个模型slug对应一个公共AI | member_id为独立角色，model_slug只选模型，多个Claude可以同房 |
| 角色没有所有者 | creator_user_id及is_legacy；旧AI不擅自认领 |
| 全局人设 | room_ai_personas按房间和成员保存正文、版本、入群编号 |
| 邀请不含人设 | 申请保存创建者提交的人设，审批不替换 |
| 生成每次读当前配置 | SQL固定本轮快照，重试恢复不换版本 |
| 退群后只认原成员ID | 每次入群新编号；退出清理、迟到动作和前端草稿均隔离 |
| 显示名同时承担提及身份 | 有结构化目标时ID为准；纯文本只作无结构化目标的兼容入口 |
| 角色创建者可能被误认为付款人 | 继续使用原触发者key，接话/冷场不换付款人 |

同一用户、同名、同模型的两个角色靠不同member_id区分，不用拼接用户ID/名字/序列号。旧slug保留为一对一技术键role_<member_id>，不是模型类型；Python内部唯一任务键仍用该角色slug，但任务同时持有实际member_id与入群编号，模型适配器只按model_slug选。

## 实测与证据

- 数据结构：一次性MySQL 8.4.6/Flyway 11.14.1，新库12 passed/1不适用跳过、V6升级13 passed；重复migrate及validate退出0，原有列逐行快照不变。
- 较早完整回归：`/private/tmp/cppai-personas-full-3.log/.exit=0`，270 passed/27 skipped；恢复8.838秒、94条确认各一次、旧主52样本零代理连接。
- 最后身份边界补丁前完整回归：`/private/tmp/cppai-personas-full-4.log/.exit=0`，274 passed/27 skipped；该轮全量单元、全部故障与严格阶段通过。最终结果与此轮不得混为同一次运行。
- 最后补丁只收敛结构化提及身份、失效目标拒绝及前端入群编号检查。最终源码运行全量C++/Python/前端单元、全部受影响AI/聊天/SQL接口并开始原故障阶段，日志为`/private/tmp/cppai-personas-final-relevant.log/.exit=1`；业务窗口与故障后连接检查失败后停止，后续故障/严格阶段未执行。本轮没有重复未受影响的其他常规HTTP用例。
- 最新真实C++/MySQL/Redis探针：`/private/tmp/cppai-personas-component-delivery.log/.exit`，验证角色创建幂等、同名独立身份、重邀旧Final拒绝且新Final只存一次，以及Chunk/Failed旧编号不广播、新编号正常广播。
- 定向SQL/HTTP补充：`/private/tmp/cppai-personas-targeted-final.log/.exit=0`，3 passed，检查退出/审批并发、旧预置AI回复、生成中编辑仍保存旧快照且下一轮用新快照。只读挂载产生两个pytest缓存写入提示，业务断言通过。
- 真实浏览器：受信任HTTPS `https://localhost:18487`，一次性测试账号创建Claude A、填写/修改房间人设并收到fake回复，页面显示created by；最终构建刷新后再次确认保存的人设及回复，截图`/private/tmp/cppai-personas-browser-final.png`和`cppai-personas-browser-chat.png`。不把fake回声当作真实模型语气质量验证。

首轮失败日志全部保留：`cppai-personas-build-unit.log/.exit`为GCC协程布尔表达式内部错误，改为先await再判断；`cppai-personas-build-ai.log/.exit`为旧AI提示词兼容断言，保留旧提示词；full-1为新测试夹具默认不重试，明确把用例设置成3次；full-2为旧测试移除错目标及通知角色标识不一致（2 failed/268 passed/27 skipped），分别修复。没有靠不改原因反复重试、放宽恢复窗口或修改去重断言碰绿。

## 交付验收结果

**实现和专项完成，最终verify未全通过，任务14/15；6.1保持未完成。** [逐条verify报告](verification-report.md)记录12项要求、41个场景的实现/测试映射及1组CRITICAL（2个失败用例）。

- 最新C++构建/ctest、88 Python、114前端及构建成功；受影响接口64 passed/2阶段跳过；真实C++/SQL/Redis服务探针退出0。
- 最新故障失败：业务窗口POST登录ReadTimeout（保留5秒期限）；新主6.89秒就绪且跨实例消息成功后，等待AI回复时WebSocket无关闭帧异常断开。后续阶段未跑，不能用full-4已通过的274项或任何PR #13 CI替代。
- 原因尚未确认。排查方向是原登录链多个顺序Redis操作与单条2秒超时/许可的整体期限，以及WS关闭来源；未修改原认证、超时方案或放宽断言，没有清数据/RESET或盲重跑碰绿。
- 故障后只读记录：`/private/tmp/cppai-personas-failure-state-counts.txt`、`cppai-personas-failure-redis-counts.txt`。SQL634房间/211私人角色/368个saved生成；Redis工作集0/计时器2/键11376。数字不证明负载是根因。
- 最终源码清单：[verification-source-sha256.json](verification-source-sha256.json)，按未提交文件内容标识受测代码，HEAD仍是PR #13基线；不是新提交CI。文件不含.env、证书或运行秘密。
- 故障后的原脚本已恢复测试栈健康，入口随后收敛为仅127.0.0.1的18087/18487；变更只涉及一次性测试覆盖文件和测试网关，不影响原服务。

## Git、文档和共同作者要求

本次规则只写Markdown：主目录和当前工作区的AGENTS.md、docs/development-workflow.md，以及用户明确要求保存的个人Markdown记忆。每次设计确认后，回复和设计文档提供“原来→现在”的设计更改对照表。

用户要求以后获准创建的新提交及获准合并生成的提交消息包含：

```text
Co-authored-by: Claude Code <noreply@anthropic.com>
Co-authored-by: Codex <noreply@openai.com>
```

不要改写历史。当前没有新提交，不能宣称GitHub已出现署名。用户将上述规则限定为本机Markdown，不要为规则单独提交GitHub，也不要在后续git add全部文件时无意上传本机协作规则；需要上传规则本身时应有相应授权。工具署名不代表两位执行者分别做过独立审查，本次实际执行者是Codex。

## 现场与未做事项

- 独立Compose项目cppai-personas，仅本机HTTPS18487/HTTP18087，网络172.36.0.0/16；镜像使用cppai-personas前缀，原cppaiserver服务、数据卷和部署不动。
- `.env`、data/certs、`/private/tmp/cppai-personas-compose.yml`只用于本机。秘密/私钥不能输出、提交或写交接。证书沿用原可信证书，验证签名，不绕过浏览器警告。
- 完整测试命令封装在`/private/tmp/run-cppai-personas-full-4.sh`；最后相关复验封装在`/private/tmp/run-cppai-personas-final-relevant.sh`。临时夹具必须--no-deps，不能重配正在测试的依赖。
- 主工作区`/Users/mima0000/CLionProjects/CppAiServer`有前次文档整理的未提交改动，保持原样，不reset/stash/覆盖。当前功能不是在main直接修改。
- 没有真实模型费用测试、其他服务器部署、备份恢复演练、角色转让或角色删除/改名；没有恢复定时任务或向Claude代发消息。
- main主规格未同步，本change未归档。CI/PR、合并/部署属于后续交付步骤，不能冒用PR #13旧CI，也不要未经指令自动合并或部署。

## 给下一个AI的double check清单

1. 先读当前任务、设计、verify和源码清单，核对未提交文件，而不是只看HEAD或阶段一旧报告。
2. 重点复核SQL开始边界与人设快照：第一次读取最新版本，同generation_id重试/重启固定；SQL为准后Redis元数据同步；付款归属保持原触发者。
3. 重点复核room锁→成员→生成的顺序、创建者退出与审批并发、共享桶memo锁序、移出不得补存部分文本。
4. 检查私人成员缺编号必须拒绝、旧预置状态可以兼容；Chunk发布与退出序列化，Final去重，Failed和前端草稿不能串入群。
5. 检查结构化AI目标为准，已选角色退出不能文本fallback，人的@不能占用AI目标数组，同名不能全触发。
6. 后续需要提交/CI时使用准确新head和共同作者trailer，明确区分本机规则文件与可上传的产品文档；不携带.env、证书、临时覆盖文件或旧主工作区的其他文档改动。
7. 当前优先接手verify报告的两个真实故障失败。不要先归档、合并或部署，也不要把任务6.1勾上掩盖未通过的原5秒/30秒及连接保持要求。

**让他double check。**

## 2026-11-06故障定位补充

[最新定位记录](failure-investigation.md)已确认原日志含AsyncTask未捕获ApiError和后端异常重启，并查到AI片段入口的异常边界缺陷；登录超时已缩小到限流计数清理失败后的链路，具体挂起点仍待复现。此次未修改代码或重新执行验收，verify状态保持未通过。
