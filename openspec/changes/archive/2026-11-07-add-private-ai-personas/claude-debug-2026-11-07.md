# Claude 接手调试记录（2026-11-07，Asia/Shanghai）

接手自 [resume-debug.md](resume-debug.md)。执行者 Claude Code；没有第二位独立审查人。**让他double check。**

## 结论

在准确最终源码上，完整 `scripts/test.sh` 退出 0，任务 6.1 完成。Codex 记录的并发停止 503、登录超时、堆损坏三项，修复后都没有再出现；但堆损坏的根因**仍未查明**，只能说在本次所有运行中未复现（见“未解决风险”）。代码仍未提交、未推送、未建 PR，也没有合并或部署。

## 设计更改对照表

| 原来 | 现在 | 用途/影响 |
| --- | --- | --- |
| Chunk/Failed 开事务，`rooms FOR SHARE` 持锁，同时在 Redis 发布期间一直占着 MySQL 连接，发布完才提交 | 用一条自动提交的只读查询校验当前入群编号和停止标记，查完立即释放连接，再发布 | Redis 变慢时不再挤占 MySQL 连接池，也不再阻塞停止、暂停、消息保存和登录。客户端本来就按 `admission_id` 过滤片段，并在成员变化时关闭草稿，退群和重邀的隔离保持不变 |
| `deliverStopped` 遇到存不进去的停止快照（AI 已离开、入群编号不符）会抛错，导致该房间之后的停止和暂停一直失败 | 这类快照以及纯空白快照直接结束为 `saved`，并清除草稿 | 停止和暂停不会被一条残留快照卡死。personas 在移出 AI 时已经同事务终结生成记录，这里是兜底 |
| 组件探针第 203 行重复声明 `final`，探针编译失败，ctest 显示 paused-ai-chunk “Not Run” | 改名为 `finished` | 让 Codex 新增的探针真正参与编译和运行 |
| `auth_controller.cc` 里有六条临时登录诊断日志 | 已删除，文件和基线一致 | 不把诊断代码带进提交 |

## 做了什么、测了什么

| 项目 | 结果 | 证据（本机） |
| --- | --- | --- |
| ASAN 重新编译 + ctest | 2/2 通过（含 paused-ai-chunk） | `/private/tmp/cppai-personas-asan-build2.log` |
| 真实组件探针（32 并发片段、旧入群 Final/Failed、重邀） | 修改前后都全部 PASS | `cppai-personas-component-claude.log`、`cppai-personas-component-claude2.log`，`.exit=0` |
| 并发停止用例，ASAN 栈单独跑 3 次 | 3/3 通过 | 原全量失败时容器已重建，当时的服务日志没有保留 |
| ASAN 故障切换定向轮（带登录阶段日志） | 退出 0；8.546 秒恢复，27 条各一次；无 ASAN 报告；登录最慢阶段 0.39 秒 | `cppai-personas-asan2-failover.log`、`-services.log` |
| 发布版故障切换循环 4 轮 | 4 轮均无重启、无 malloc 报错；第 2–4 轮通过（恢复 8.4–9.9 秒）；第 1 轮“受控切换旧连接”检查失败 | `cppai-personas-release-loop*.log`。第 1 轮失败是因为循环脚本在这一轮开始前连续切了 3 次主，Sentinel 还没稳定，属于测试工具问题，不计为通过 |
| SQL/HTTP 专项（新增“存不进去的快照不阻塞”用例） | 5/5 | 本次完整回归内 `Ran 5 tests OK` |
| **最终完整回归** `scripts/test.sh` | **退出 0**。ctest 2/2；常规集成 276 passed / 27 skipped；AI 故障阶段、受控和自动 Redis 切换、app/web 故障均通过；共享桶 9.792 秒恢复，102 条确认各一次，确认后迟到可重试错误 0；旧主重加入 51 次采样零代理连接；严格阶段 9 passed；真实 Redis 队列 18 项、SQL/HTTP 5 项通过；日志中没有测试 key 明文；app-1/app-2 重启次数 0 | `/private/tmp/cppai-personas-full-claude-2.log/.exit`；受测源码清单 `cppai-personas-full-claude-2-source.sha256`，跑完后复核源码未变 |
| Python 单元测试 | 88 passed（完整回归中这一层是缓存命中，另外用同一镜像实际重跑） | 本次会话输出 |
| 前端单元测试 | 16 个文件 114 passed（完整回归中这一层是缓存命中，另外无缓存重建实际重跑） | 本次会话输出 |

## 未解决风险

1. **堆损坏根因未查明。** 崩溃现场 `/private/tmp/cppai-personas-app2-crash.log` 显示，崩溃前一刻 Drogon Redis 客户端报了“Command execution timeout”，紧接着三个 Redis IO 线程都报 Network failure。已确认 Drogon 1.9.13 在 hiredis 的回复/断开回调里直接恢复业务协程（`RedisAwaiter` 内联调用 `handle.resume()`），业务协程会接着在 Redis IO 线程上继续执行，包括立刻发下一条 Redis 命令。这种重入是可疑点，但没有动态证据。去掉跨发布持锁后，本次 ASAN 和发布版多轮运行都没有复现。可选的加固：在 `GuardedRedis` 每次 await 之后切回应用线程。这会影响 main 上所有 Redis 路径，需要单独评审和回归，没有在本批改动。
2. **登录 5 秒超时没有复现**，根因同样未证实。登录链路中每条 Redis 命令都有 2 秒超时；本次登录最慢 1.066 秒。原来跨发布持锁可能挤占 MySQL 连接池，是一个合理的推测，没有被证明。
3. 两轮复核都由同一执行者完成，不代表独立审计。

## 下一步（需要用户授权）

按 AGENTS.md 逐个挑选文件提交（排除 AGENTS.md、`.env`、证书和本机日志），带用户规定的两条 Co-authored-by trailer，推送分支、创建 PR 并跟踪准确 head 的 CI。不自动合并或部署。PR 合入后再同步主规格并归档本 change。
