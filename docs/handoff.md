# 基于 C++ 的分布式 AI 实时通信系统 · 当前交接

更新：2026-11-07（Asia/Shanghai）。本文件是项目进度、部署结果和AI接手的唯一当前入口；[文档索引](README.md)负责导航，[后续计划](roadmap.md)负责未实施需求。过去的会话、临时工作区和归档报告不再作为“当前状态”重复维护。

## 项目名称

2026-11-06，用户确认正式项目名称为“基于 C++ 的分布式 AI 实时通信系统”。后续项目介绍和文档标题统一使用该名称。`CppAiServer`、`CppAiWebServer`继续作为现有目录、仓库及工程标识使用，历史记录中的原名称保留。

当前验证范围为单机 Docker 环境的多实例部署与故障恢复；项目更名不扩大已有验证结论。

## 当前结论

2026-11-07由Claude Code接手并更新。功能基线为已合并的PR #15。

- [PR #15](https://github.com/anwalld/CppAiWebServer/pull/15)（私人AI角色与房间人设）已正常合并，合并提交`5f33431504a067f3bd385957e1ece65568e6e027`。PR准确head `0950fd9`的分支CI成功；合并提交的main CI见该提交的Actions记录，本文件不预先宣称结果。
- 合并前在准确源码上本机完整运行`scripts/test.sh`退出0：常规集成276 passed/27 skipped，AI、Redis受控和自动切换、app/web故障阶段及最后严格9项通过，共享桶恢复9.792秒、102条确认各一次，旧主重加入51次采样零代理连接；Python 88项、前端114项实测通过。详见[调试记录](../openspec/changes/archive/2026-11-07-add-private-ai-personas/claude-debug-2026-11-07.md)。
- 已完成并归档：HTTPS/多实例/Sentinel/房间管理、HA加固、离线重试与共享桶（PR #12），结构化日志、未读/@提醒、AI停止/暂停、搜索（PR #13），私人AI角色与房间人设（PR #15）。活动change为空。
- 同批文档整理：本文件、[文档索引](README.md)、[后续计划](roadmap.md)和`docs/history/`随修复文档PR进入版本控制，并修正了AI账号授权、迁移示例、README中的旧版邀请流程等过时内容。
- **未部署**：原服务`https://localhost:8443`最近一次有证据的部署仍是PR #12的`a1bfbc0`，PR #13、#15均未上线。部署需要用户单独授权，步骤见[后续计划](roadmap.md)第8节。

**让他double check**：Codex调试时出现过一次堆损坏（`malloc(): unsorted double linked list corrupted`，Redis故障切换窗口），根因未查明，后续ASAN和发布版多轮故障切换均未复现。可疑点及加固方案见调试记录和roadmap第8节。

## PR #12既有部署与验证记录

以下各节保留PR #12当时的double check、CI及原服务部署证据。所述提交、234项源码清单和100项前端结果仅对应PR #12，不作为PR #13或未来角色功能的验证。

## 验证依据

- [PR最终CI 42116097289](https://github.com/anwalld/CppAiWebServer/actions/runs/42116097289)：准确对应`dd9e329f7293576d3b5c398cf9d7eb1ca042fc1c`，成功。100项前端、76项AI单元、常规258 passed/27阶段跳过、全部故障阶段及最后9项严格检查通过。
- 该CI连续故障窗口12.356秒恢复、27条已确认消息各一次、54次旧主重加入采样零代理连接。本地完整回归8.841秒恢复、79条确认各一次；两者不是同一次运行。
- 真实浏览器验过排队登录/最终Cookie、故障恢复、消息唯一性，以及退出unknown时保留聊天、最终确认退出后回登录页。原服务部署验收另见下文。
- [共享桶逐需求验证报告](../openspec/changes/archive/2026-11-05-buffer-business-requests/verification-report.md)保留专项、失败现场及源码清单；[离线重试报告](../openspec/changes/archive/2026-11-05-fix-offline-message-retries/verification-report.md)保留早期88项测试的历史范围，不冒充最终100项结果。
- 新request-buffering规格strict、8个归档任务strict、全规格普通校验通过；全规格strict唯一既有realtime-messaging Purpose过短warning保留，不能写成全仓strict全绿。

以下是最近一次复核、合并和部署的具体记录。本次整理仅改文档，未重新运行产品回归，也未改变部署或调用模型。

## 交付位置

- [PR #12](https://github.com/anwalld/CppAiWebServer/pull/12)已合并，merge commit：`a1bfbc020dedfe316e1428b099204e4f503d0a61`。原项目main已仅快进到该提交。
- 本次实际部署目标是**原本机Docker Desktop项目`cppaiserver`**，入口[https://localhost:8443](https://localhost:8443)。没有声称部署到其他远程/公网服务器。
- 源码目录`/Users/mima0000/CLionProjects/CppAiServer`。原未跟踪探索全稿及较早计划已合并进[探索历史](history/exploration-2026-11-05.md)，有效后续需求迁至[后续计划](roadmap.md)；旧路径已删除，原文内容保留。
- 之前的隔离项目`cppai-buffering-static`没有当作此次部署目标，也没有运行全套故障测试来破坏原项目数据。

## double check 结果

本次实际重新阅读了运行许可及协调器、共享Lua桶、SQL请求去重/external意向、可信路由分派、查询认证/Cookie、删房缓存锁和清理、前端未知结果处理等关键路径，未发现新的阻塞问题。234个受测构建/运行/测试输入哈希与归档清单匹配，原PR准确head的CI已成功。没有宣称有第二位独立审查人。

重点核对：许可从原challenge起算且绑定boot/epoch/revision/nonce，原请求stamp不会因续约无限延长；撤销后不发旧phase grant；缺ACK主动取消、自动健康实例先恢复；SQL唯一插入与结果同事务；external无证明不重做；查询凭证不能被task_id替代；最终Cookie通过Set-Cookie；queued与持久化ACK分开；缓存写持SQL共享锁，删房等DEL，失败明确unknown、原编号恢复补清。

源码未做额外修改。新规格strict、归档任务strict和diff检查通过；既有Purpose过短warning未修。此前100项前端、258常规/全部故障阶段等完整证据仍由准确提交CI及归档报告证明，不把本次静态复核冒称重跑了全部测试。

## 升级过程与数据保障

1. 受保护备份目录：`/Users/mima0000/.local/share/cppai-backups/pr12-a1bfbc0-20261106`，目录权限700；原.env、SQL备份、证书和Redis/Sentinel归档均受限。保留旧镜像标签及镜像ID用于回退准备，未伪称做过备份恢复演练。
2. 保留全部既有.env值，仅追加共享桶所需配置；三个新秘密分别随机生成、不复用旧密码，不输出秘密。原CA没有替换。
3. 先构建镜像，再暂停入口/应用/AI写入，做一致性SQL备份，确认Redis复制屏障后正常停服务。重建网络但没有删除任何命名数据卷，也没有运行`down -v`。
4. 固定redis-proxy为`172.28.0.10`、共享桶为`172.28.0.11`，动态池`172.28.128.0/17`。共享桶单实例纯内存，与业务Redis数据卷独立。
5. V5迁移成功。应用开放前逐个既有业务表CHECKSUM完全相同，用户/房间/消息原计数为7023/2860/12848。验收清理后这三个计数再次相同。
6. 启动14个正式长期服务并确认全部running/healthy。原服务已有数据卷（包括未使用的旧命名卷）均保留。原证书CA SHA256不变。

初次编排把单独启动后正常退出0的certs容器交给Compose --wait，错误判成失败；保留`/private/tmp/cppai-pr12-deploy.exit=1`和阶段现场。修正为检查certs/migrate/db-users全部退出0、再启动完整依赖图；`cppai-pr12-deploy-resume.exit=0`。这是本次操作脚本的等待方式错误，没有修改产品代码或数据库数据来掩盖失败。

本机首次curl受错误环境代理`_PROXY`影响；改为localhost直连并继续使用原CA验证，未关闭TLS校验。

## 原服务上的真实验收

使用临时独立账户和没有AI成员的房间，没有发起真实模型测试。

- HTTPS健康接口实际返回MySQL/Redis均ok；浏览器正常打开原8443入口登录页，没有证书警告。[入口截图](/private/tmp/cppai-pr12-production-login.png)。
- 注册、登录最终双Cookie及Secure属性、TLS1.3 WebSocket连接正常。
- 同client_msg_id重发返回原seq及duplicate=true，SQL只存一次。
- 使用生产既定入口`redis-proxy.sh switch`做一次受控切主。暂停期HTTP先202且不提前发Cookie，WS先queued；恢复后取得真实用户结果和持久化ACK。**从发起验证到恢复6.519秒，保留原30秒总期限与5秒初始响应要求。**
- 两个后端均能查询同一任务；错误owner被401拒绝。
- 两条确认消息在SQL各一次，最终历史seq为1、2。
- 正常退出后读取会话401；临时房间、消息、用户、成员及本次请求记录已按确切ID清理。没有批量清理原项目测试数据。

原始证据前缀`/private/tmp/cppai-pr12-`：`double-check.json`、`review-spec.json`、`review-archive.json`、`merged.json`、`deploy-build.log/.exit`、`deploy.log/.exit`、`deploy-failure.json`、`deploy-resume.log/.json/.exit`、`production-smoke.log/.json/.exit`、`production-switch.log`、`post-smoke-data.json`、`production-delivery.json/.exit`。日志仅在本机，未声称上传GitHub附件。

## CI准确状态

- 合并前PR最终head `dd9e329f7293576d3b5c398cf9d7eb1ca042fc1c`：[CI 42116097289](https://github.com/anwalld/CppAiWebServer/actions/runs/42116097289) completed/success。
- 合并后main提交 `a1bfbc020dedfe316e1428b099204e4f503d0a61`：[CI 42122780111](https://github.com/anwalld/CppAiWebServer/actions/runs/42122780111)已completed/success；本次文档整理已实时核对准确head与结论。merge提交的文件树与已通过PR源码相同。

## 交接与边界

**让他double check**：仍可独立复核许可原期限/代次、SQL与external边界、查询认证/Cookie、缓存锁与删房补清、内存预算和旧claim。当前用户已经授权且已完成合并/本机部署，不要再把归档旧handoff的“不得自动部署”当作当前阻塞。

共享桶是已确认的单实例纯内存方案，重启可丢任务，unknown不等于未执行；不承诺跨MySQL/Redis/gRPC全局exactly-once，50/s与8并发不是性能承诺。真实模型付费验证、其他远程服务器部署和备份恢复演练不在本次已验证结果内。没有创建新的定时任务，也没有向Claude发送消息。
