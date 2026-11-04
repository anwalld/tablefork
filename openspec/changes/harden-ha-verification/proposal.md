# Proposal

## Why

上一轮高可用改造已归档，但 Redis 切换窗口只测了 health，缺少真实登录和消息请求的有界响应证据；同步 WebSocket 测试夹具仍限制 TLS 1.2。用户要求把这两项与对账竞态复核一起收尾，并明确不修握手拒绝后的多余关闭帧警告。

## What Changes

- 在停止 Redis 主节点之前启动业务观察客户端，持续检查登录及发送消息的成功/可重试错误；保留 30 秒恢复要求和已有全部故障断言。
- 修正同步测试客户端首次读写的时序，移除 TLS 1.2 上限；在 TLS 1.2/1.3 下重复握手、订阅并验证证书校验仍然有效。
- 复核 reconcile 标记的线程归属与挂起点，以证据判断窄竞态是否存在；不预设需要修改生产代码。
- 自动执行 verify，写交接，保留未处理警告及“让他double check”。
- 用户追加：修复 Sentinel 地址复用后把自己当同伴的问题，增加隔离回归与 .DS_Store 忽略规则，执行 CI 并创建 PR。
- 完整回归揭示原 watcher 在实际 IP 更新前就清连接的竞态；修正切换顺序，并让全新 CI 也执行双向受控切主，保留原严格断言。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无。现有 high-availability、edge-gateway 和 realtime-messaging 的产品契约不变；本轮是测试工具、现有高可用实现的缺陷修复与证据完善，使用 `skip_specs: true`，不伪造需求变更。

## Impact

- 测试：`tests/integration/conftest.py`、新的同步 TLS 辅助模块/回归、`test_failover.py`、`scripts/test.sh`、`scripts/test_sentinel.py`。
- 部署：Sentinel 补丁镜像与 compose 引用、`scripts/redis-master-watch.sh` 的实际地址就绪判断；`.gitignore`。
- 文档：本 change 的 design、tasks、verification-report、handoff。
- 不改变生产 TLS、Nginx、Redis 拓扑或认证/消息接口；不调用真实模型。Sentinel 使用基于原 7.4.5 的最小源码补丁镜像，过滤自身 ID；普通 Redis 节点仍用原镜像。
- 握手 401/403 正文后的 WebSocket 关闭帧问题继续保留，本轮不改 Dockerfile 或 Drogon。
