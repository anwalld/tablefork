# Tasks

## 1. 同步 TLS 客户端

- [x] 1.1 增加首写后读的同步 TLS socket/context，移除 Socket 的 TLS 1.2 上限；以强制 TLS 1.2/1.3 的重复握手、订阅及默认协商回归验证，说明首次读写约束。
- [x] 1.2 补未信任 CA、错误主机名和首次写入前关闭的回归；定向测试通过，证书验证与取消路径保持有效。

## 2. 对账复核

- [x] 2.1 写出 reconcile 全部标记访问、调用路径、挂起点及 Drogon v1.9.13 的切线程依据，明确 S1 是否成立；通过源码逐项核对且不虚构已修竞态。

## 3. Redis 故障窗口

- [x] 3.1 增加 window 阶段业务观察者，提前建立夹具和订阅，直接检查登录/消息成功或可重试错误、单次有界响应及 ack 消息历史；通过真实停主和恢复验证并记录样本。
- [x] 3.2 scripts/test.sh 在停主前通过 ready/begin/started 屏障启动观察者，保留 after/rejoined 和 30 秒要求；完整脚本显示观察者成功且原故障断言全部通过。

## 4. 整体验证与交接

- [x] 4.1 完整 scripts/test.sh 全部阶段通过，保存原始日志/退出码与实现哈希，结束恢复正常服务；不绕过 TLS、失败断言或新增重试。
- [x] 4.2 自动执行 verify、strict change validate 和 diff check；交接写明改动、测试、S1 复核依据、未修的握手警告，并写“让他double check”。

## Workflow follow-up

- 用户已追加授权 Sentinel 自引用修复、忽略 .DS_Store，以及 verify、CI、PR；本 change 不自动合并或归档。
- 握手 401/403 多发关闭帧按用户决定本轮不修；旧 realtime-messaging Purpose 格式警告保留。

## 5. Sentinel 自引用与提交验证（用户追加）

- [x] 5.1 隔离复现旧同伴地址指向自身时的 HELLO 自发现，补 Redis HELLO 统一入口与持久配置加载的自身 ID 检查；保留运行 ID、epoch、选票和数据，不靠 RESET。
- [x] 5.2 自动回归直接 HELLO、持久自身条目、地址复用后重启的三个 Sentinel 同伴集合；接入完整脚本，新增 .DS_Store 忽略规则。
- [x] 5.3 重跑完整脚本，执行 verify 与 strict change validate，更新根因、证据和 Claude 交接（让他double check）。
- [ ] 5.4 提交推送并创建 PR，跟踪准确提交的远程 CI，记录结果；不自动合并或归档。

- [x] 5.5 修正完整回归发现的 HAProxy 异步 DNS 地址更新晚于清连接的竞态；实际 IP 切换后再清连接，通过双向真实切主和原严格断言，保留失败日志。
