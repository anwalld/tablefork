# 当前实现与验证索引

> 历史阶段记录：保留本阶段的提交、失败与验收范围，不代表当前部署或剩余任务。最新结果见[当前交接](../../../../docs/handoff.md)，未完成需求见[后续计划](../../../../docs/roadmap.md)。历史操作限制不替代用户后续明确指令。

更新时间：2026-11-04。完整当前交接见 [handoff.md](handoff.md)，OpenSpec 验证见 [verification-report.md](verification-report.md)，原始调查见 [handoff-history-2026-11-03.md](handoff-history-2026-11-03.md)。历史记录中的“5/31”“尚未修复”不再代表当前状态。

- 当前 27/31。HTTPS/L4/L7、Redis Sentinel/HAProxy、双应用、房间踢人封禁、Vue 交互及回归已实现。
- Claude 定位的 Redis 订阅回调退订死锁已修复并验证；独立 WebSocket upstream 保留，同步 TLS1.2 兼容设置和异步 TLS1.3 覆盖保留。
- 通知丢失后的旧订阅越权已复现并修复：发布前当前成员名单、私人目标交集、接收过滤，成员元数据也经过同一路径。每个数据推送新增一次 SQL，未作新压测；发布快照不等于全局线性化撤权。
- 最新定向 105 passed / exit 0；最新独立新 checkout 完整脚本 exit 0，首轮 243 passed / 24 skipped，后续故障及严格轮次全部通过；独立测试项目 teardown exit 0。178 个实现/配置文件哈希与当前代码一致。
- 最新日志 /private/tmp/cppai-final-clean-full.log；元数据 /private/tmp/cppai-final-clean-metadata.json；定向 /private/tmp/cppai-recipient-targeted-final.log。
- verify 已执行：4 项未完成验收/CI 任务（2.4、2.8、5.6、6.2），以及通知可靠性边界、切换窗口业务断言缺口、同步 TLS1.3 范围警告。不能声称全部完成或可归档。
- 浏览器 CA 不受信任，用户选择暂不导入；没有绕过。AI 测试用假模型，没有本轮真实服务商验证。未提交/推送/CI/合并/归档。
- 请 Claude 接手时：**让他double check**。
