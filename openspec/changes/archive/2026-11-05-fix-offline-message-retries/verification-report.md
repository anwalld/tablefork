# Verification Report: fix-offline-message-retries

日期：2026-11-05，Asia/Shanghai。

## Summary

| Dimension | Result |
| --- | --- |
| Completeness | 3/3任务完成；1个新增需求、6个场景均有源码与测试对应 |
| Correctness | 前端13个测试文件88项通过，其中ChatSocket23项；生产构建通过 |
| Coherence | D1–D4与实际实现一致；保持原消息ID、服务端ACK及权限/去重协议 |

## 场景与证据

- 离线30秒：未实际发送且未失败，恢复后原ID/房间/内容发出并ACK成功。
- 暂时性错误持续35秒：仍退避重试，不因旧四次上限提前终止；重复ACK只成功结算一次。
- ACK丢失：超过旧四次预算仍发送相同载荷，收到ACK后清理所有消息定时器。
- 离线/在线60秒边界：到期仅失败一次，迟到ACK与重连不会重新发送；后台计时器尚未执行但绝对时间已过时也不发送。
- 限流等待：重连不跳过retry_after，长等待不能延长总期限。
- 不可重试错误、主动关闭和4401：立即终止或清理，取消ACK/期限/重连任务，无迟发或重复结算。

测试位于web/src/lib/chat-socket.test.js。全量日志/private/tmp/cppai-offline-batch-tests.log，13 files/88 passed；构建日志/private/tmp/cppai-offline-batch-build.log，成功。源码与暂停时两文件SHA-256核对一致。
OpenSpec本change strict validate与git diff --check通过。API和锁文件无改动，不引入新依赖。

## CRITICAL

无。

## WARNING / 证据限制

尚未运行真实浏览器加Redis停主的端到端验收；本报告以实际ChatSocket源码、假WebSocket与确定性时钟回归证明客户端状态机，不冒称证明数据库只保存一次或生产故障恢复。后端共享桶是另一个change，不属于本change验收。

## Final Assessment

本change要求的实现、单测、构建和规格映射通过；允许按用户授权归档并同步主规格，保留上述真实网络证据限制。让他double check：60秒期限不被重连/限流重置、发送前绝对期限校验、所有终止路径的清理，以及后台页面恢复时的行为。

## 归档结果

已由OpenSpec CLI归档至2026-11-05-fix-offline-message-retries，3/3任务完成，新增1个需求已同步；归档后核对原13个需求逐块保持、新增需求与delta一致。未借此修改旧Purpose格式警告。
