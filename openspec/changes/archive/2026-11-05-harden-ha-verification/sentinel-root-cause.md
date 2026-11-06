# Sentinel 自引用根因与修复

> 历史阶段记录：保留本阶段的提交、失败与验收范围，不代表当前部署或剩余任务。最新结果见[当前交接](../../../../docs/handoff.md)，未完成需求见[后续计划](../../../../docs/roadmap.md)。历史操作限制不替代用户后续明确指令。

## 现场证据

`/private/tmp/cppai-sentinel-investigation-*` 保留三台 MYID、同伴表、过滤凭据后的配置和日志、容器地址、启动时间与重启次数；三台使用独立数据卷，restartCount 均为 0。

sentinel-1 启动日志的 ID 为 `8e6890d84ce341b59ae6c779f3d6e9fbfb580f85`。2026-11-04 09:21:16.337 UTC，在 `+sentinel-invalid-addr` 标记旧同伴地址 `172.28.0.5:26379` 后，同一毫秒 `+sentinel` 添加了上述自身 ID，地址也是 `.5:26379`。另两台同期出现同伴 address-switch。由此可见自身条目是在启动后动态添加，而非只从配置加载。

## 源码与隔离对照

固定源码：https://github.com/redis/redis/blob/7.4.5/src/sentinel.c

- `sentinelReceiveHelloMessages` 过滤包含自身 ID 的订阅消息。
- `sentinelPublishCommand` 直接调用 `sentinelProcessHelloMessage`；原公共入口没有自身判断。
- `known-sentinel` 的旧地址在容器地址复用后指向自己，Sentinel 的定期 HELLO 会经直接 PUBLISH 回到自己，创建并持久化自身同伴。
- 原版 `redis:7.4.5` 在无网络、无业务数据卷的容器中：直接自身 HELLO、仅一条旧同伴指向当前监听地址这两种场景均成功复现。后者没有人为注入自身 HELLO，由进程定时发布触发。

对照日志：`/private/tmp/cppai-sentinel-guard-baseline-fixed.log`。最初工具把 SHUTDOWN 无回复当成 JSON，失败记录仍保留 `cppai-sentinel-guard-baseline.log`。

## 补丁

`docker/sentinel/ignore-self.patch` 仅两处：

1. 公共 HELLO 入口拆成八字段后，按第三字段精确比较 MYID，忽略自身。
2. 完整配置加载且 MYID 确定之后，从每个 master 的同伴字典仅删除自身 ID，并在发生删除时持久化。使用 SDS 键匹配 Redis 字典类型，保留其他同伴、MYID、current-epoch、leader-epoch 与 master config-epoch；不受配置指令顺序影响。

初版第二处错误使用普通 C 字符串作 SDS 字典键，真实脏配置回归失败。已改为 sdsnew/sdsfree；不放宽断言。失败日志 `cppai-sentinel-guard-targeted.log`、修正构建 `cppai-sentinel-guard-build-fixed.log` 保留。

镜像固定 Redis 7.4.5 与源码 SHA-256 `00bb280528f5d7934bec8ab309b8125088c209131e10609cb1563b91365633bb`，构建保留 TLS。仅三个 Sentinel 使用新镜像，普通 Redis、拓扑、认证、业务源码不变。

## 自动回归

`python3 scripts/test_sentinel.py`：直接自身 HELLO 被拒、外部 HELLO 被接受；修复已持久化自身条目并保持 epoch/票；三个 Sentinel 各只有另两台、CKQUORUM 报三台可用；停止三台、轮换监听地址模拟 IP/端口复用、保留配置重启后再次核对上述集合与身份。

`--image redis:7.4.5 --expect-vulnerable` 用于原镜像对照。隔离回归已接入完整脚本，不执行 RESET、删除真实数据卷或改变原 len==2 断言。

## 运维与边界

正常 `docker compose up -d --build --wait` 会重建 Sentinel 服务，使用原卷并自动去掉历史自身条目；不需要清卷。回滚镜像会重新暴露直接 HELLO 漏检，不代表旧版安全。以后升级 Redis 时需核对上游是否合入等效检查，并保留行为回归；不要对新版本盲目重用补丁。

本修复针对已证明的自身 ID 路径，不承诺清除任意伪造/重复的其他同伴身份。若出现不同问题，保留三台 MYID/同伴表/地址与重启日志再定位。

新增隔离测试的初版在地址轮换后首次看到完整同伴集合就立刻要求连接持续稳定，碰到正常断线重连而失败，记录在 `cppai-sentinel-guard-targeted-fixed.log`。测试改为在同一 30 秒界限内要求连续 3 秒连接和集合稳定；任何自身 ID 仍立即失败。最终定向日志 `cppai-sentinel-guard-targeted-stable.log` 通过。该调整只涉及新测试的收敛判断，原故障断言不变。
