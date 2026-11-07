# 基于 C++ 的分布式 AI 实时通信系统 · 当前交接

更新：2026-11-07（Asia/Shanghai）。本文件是项目进度、部署结果和AI接手的唯一当前入口；[文档索引](README.md)负责导航，[后续计划](roadmap.md)负责未实施需求。过去的会话、临时工作区和归档报告不再作为“当前状态”重复维护。

## 项目名称

2026-11-06，用户确认正式项目名称为“基于 C++ 的分布式 AI 实时通信系统”。后续项目介绍和文档标题统一使用该名称。`CppAiServer`、`CppAiWebServer`继续作为现有目录、仓库及工程标识使用，历史记录中的原名称保留。

当前验证范围为单机 Docker 环境的多实例部署与故障恢复；项目更名不扩大已有验证结论。

## 当前交接（2026-11-07，Claude Code → 下一位接手者）

**让他double check。** 本节替代此前的“当前结论”。2026-11-06、11-07 两天由 Claude Code 接手 Codex 的交接，复核两次都由同一执行者完成，不代表独立审计。

### 一句话状态

功能全部合并进 main（截至 PR #15）。**main 当前 CI 是红的**：`712572a` 在 Redis 故障切换时后端偶发崩溃。修复在 [PR #17](https://github.com/anwalld/CppAiWebServer/pull/17)，分支 CI 已通过，**还没合并**，在等用户确认。原服务**没有部署**，仍是 PR #12。

### 提交与 CI 准确状态

| 对象 | 提交 | CI | 状态 |
| --- | --- | --- | --- |
| [PR #15](https://github.com/anwalld/CppAiWebServer/pull/15) 私人AI角色与房间人设 | head `0950fd9`，合并 `5f33431` | 分支 [42255568447](https://github.com/anwalld/CppAiWebServer/actions/runs/42255568447) 成功；main [42263663594](https://github.com/anwalld/CppAiWebServer/actions/runs/42263663594) 成功 | 已合并 |
| [PR #16](https://github.com/anwalld/CppAiWebServer/pull/16) 归档 personas、文档整合 | head `2bb8817`，合并 `712572a` | 分支 [42264305171](https://github.com/anwalld/CppAiWebServer/actions/runs/42264305171) 成功；**main [42268223848](https://github.com/anwalld/CppAiWebServer/actions/runs/42268223848) 失败** | 已合并；失败与本 PR 的文档改动无关，见下面的 bug |
| [PR #17](https://github.com/anwalld/CppAiWebServer/pull/17) Drogon Redis 断连补丁 | 分支 `claude/drogon-redis-disconnect-fix` | 分支 [42274473079](https://github.com/anwalld/CppAiWebServer/actions/runs/42274473079)（`734dbf6`）成功 | **待合并**；本交接文档的提交会再触发一次分支 CI |

### 做了什么

1. **复核 PR #13 并修两个 bug**（代码已随 PR #15 进入 main）
   - AI 片段、失败、提示的异步入口没有异常边界，异常逃出 `drogon::async_run` 会导致进程终止；已加保护。
   - AI 被移出后残留的“已停止”快照存不进去，导致该房间之后每次停止或暂停都返回 404；改为直接结束为 `saved` 并清除草稿，纯空白快照同样处理。
2. **接手私人AI角色调试**（Codex 留下：未提交，ASAN 回归 1 项失败，堆损坏和登录超时没有定位）
   - Chunk/Failed 原先在 Redis 推送期间一直持有房间行锁和 MySQL 连接，改为一次只读校验后再推送。
   - 修复组件探针的编译错误，删除临时登录诊断日志。
   - 在准确源码上完整跑 `scripts/test.sh`，退出 0。随后提交并建 PR #15，CI 通过后合并。
   - 详细记录：[调试记录](../openspec/changes/archive/2026-11-07-add-private-ai-personas/claude-debug-2026-11-07.md)。
3. **归档与文档**（PR #16）
   - 归档 `add-private-ai-personas`，同步 ai-members、ai-replies 两份主规格，新增 ai-personas 主规格；14 份主规格校验全部通过。
   - 把主工作区里另一位执行者没提交的文档整理并入版本控制：索引、本文件、roadmap、history，并修正过时的状态。
   - README 改为私人角色的邀请流程；AI 账号授权、迁移示例同步更新；Codex 共同作者邮箱统一为 `codex@openai.com`。
4. **清理**
   - 停掉三套隔离测试栈：`cppai-personas`、`cppai-chat-controls`、`cppai-buffering-static`，连同数据卷一起删除。原服务 `cppaiserver` 没动。
   - 只在本机存在的文件先备份到 `~/.local/share/cppai-backups/worktree-cleanup-20261107`（目录权限 700）。
5. **定位并修复 main CI 崩溃**（PR #17），见下一节。

### 本次发现的 bug：Drogon Redis 连接断开后的释放后使用

- **现象**：main CI `712572a` 的 `test_failover.py::test_business_requests_during_redis_failover` 失败。app-2 日志打出 `FATAL It is forbidden to run loop on threads other than event-loop thread - EventLoop.cc:269`，随后 trantor `abortNotInLoopThread()` 调用 `exit(1)`，WebSocket 没有关闭帧就断开。同样的代码在 `5f33431` 上通过，所以是偶发的。
- **根因**：Drogon 1.9.13 的 `RedisConnection::sendvCommand`/`sendFormattedCommand`/`sendSubscribe`/`sendUnsubscribe` 跨线程时，用 `queueInLoop([this, ...])` 排队，只捕获裸指针。连接断开时，`handleDisconnect` 移除 channel、清空 `ev.data`，hiredis 在断开回调返回后释放 `redisAsyncContext`。断开之后才执行的排队发送，仍用这个悬空的 `redisContext_` 调用 `redisAsyncFormattedCommand`，属于释放后使用。结果可能是 trantor 线程断言后 `exit(1)`（这次），也可能是 Codex 调试时 app-2 的 `malloc(): unsorted double linked list corrupted`；`exit` 在其他线程运行期间析构全局对象，也会放大成堆损坏。
- **修复**：`docker/drogon-redis-disconnect.patch`，在 `Dockerfile` 里和已有的 MySQL 补丁一起应用：
  - 排队 lambda 改为捕获 `shared_from_this()`。
  - `handleDisconnect` 结束时把 `redisContext_` 置空。
  - `sendCommandInLoop` 遇到已结束的连接时，直接回调 `Connection is broken`；订阅、取消订阅和 `disconnect` 遇到已结束的连接时跳过。
- **证据**：
  - 打补丁后 Drogon 编译通过，ctest 2/2 通过；PR #17 完整 CI 通过。
  - 压力探针（64 个协程从 8 个应用线程持续发命令，同时每 20ms `CLIENT KILL`）：补丁版 60 秒内拦下 3241 次发往已断开连接的发送。
  - **缺口**：未打补丁的发布版在同样条件下碰巧没崩，所以没有拿到“补丁前必现、补丁后消失”的直接复现。要更强的证据，需要用 ASAN 编译 hiredis 和 Drogon 后再跑探针。
- **同一原因的连带现象**：发往已断开连接的命令，回调会进入一个永远不会处理的队列，只能等 2 秒超时；这很可能就是 Codex 记录的登录偶发超过 5 秒。打补丁后这类发送会立刻失败，但登录超时本身没有复现验证过。

### 现在的问题

1. **main 的 CI 是红的**，PR #17 合并后才会恢复。合并后要核对新 merge SHA 的 main CI。
2. **原服务没有部署**：`https://localhost:8443` 仍是 PR #12 的 `a1bfbc0`；PR #13、#15、#17 都没上线，V6、V7 迁移也没在原服务的数据库上执行过。
3. **三个已合并的临时工作区和分支还在**：`/private/tmp/cppai-chat-controls`、`/private/tmp/cppai-shared-request-buffering`、`/private/tmp/cppai-private-ai-personas`，以及本地和远端的同名分支 `codex/chat-controls-and-observability`、`codex/shared-request-buffering`、`codex/private-ai-personas`；另外还有已合并的 `claude/project-update-handover-24e3c2`。删除命令被权限拦下，需要用户授权或自己删。
4. **主工作区** `/Users/mima0000/CLionProjects/CppAiServer`：本地 main 停在 `9dae3ff`，还留着另一位执行者 24 个未提交的文档改动。这些内容已经由 PR #16 并入 main，但按约定没有 reset 或覆盖。用户确认后可以丢弃这些改动，再把本地 main 快进到 origin/main。
5. **PR #15 的提交**里 Codex 的署名邮箱是 `noreply@openai.com`（按当时的 AGENTS.md），GitHub 可能不会把它关联到 codex 账号。合并提交和之后的提交都已统一为 `codex@openai.com`；按约定不改写历史。
6. `claude/project-framework-overview-22187f` 工作区属于另一个 Claude 会话，没有动。

### 接下来要做什么（按顺序）

1. **合并 PR #17**（需要用户确认），然后核对 main CI 是否恢复成功。
2. **部署到原服务**（需要用户单独授权），参照下面 PR #12 的升级记录：
   - 受保护备份：.env、一致性 SQL、证书、Redis/Sentinel 状态。
   - 先构建镜像，停写入，执行 V6、V7 迁移，逐表核对计数，`db-users` 应用新授权。
   - 冒烟验收：登录、消息、私人AI的创建和邀请，用 fake provider 或不调用付费模型。
   - 保留旧镜像便于回退，不运行 `down -v`。
3. **清理**上面第 3、4 项列出的工作区、分支和主工作区（需要用户点头）。
4. **以后再做**：见[后续计划](roadmap.md)第 8 节，包括 Redis 补丁的后续观察、停止控制的数据库轮询压力、生成失败的独立状态、搜索全文索引和既有遗留。

### 环境与约定

- 测试和故障演练只在隔离 Compose 项目里做，临时夹具用 `docker compose run --no-deps`。
- 不要提交 `.env`、`data/certs` 或本机日志。提交消息末尾按 [分支与CI流程](development-workflow.md) 加 Claude Code 和 Codex 两条 Co-authored-by，邮箱以该文档为准。
- 合并、部署、删除分支或工作区，都需要用户对当次操作的明确授权，前一批的授权不延续。

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
