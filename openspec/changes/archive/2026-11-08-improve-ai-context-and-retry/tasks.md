# Tasks

## 1. A3 回复清洗
- [x] 1.1 `strip_self_name`、流式前缀暂缓显示，开关 `AI_STRIP_SELF_NAME`
- [x] 1.2 单测：前缀去除、草稿与正式一致、默认关闭

## 2. A2 缓存友好窗口
- [x] 2.1 `to_chat` 块对齐与按块丢弃，读取 N+B 条，开关 `AI_CONTEXT_BLOCK`
- [x] 2.2 Anthropic `cache_control` 标记，`cache_prefix` 参数
- [x] 2.3 单测：起点稳定、条数范围、超预算按块丢弃、无 seq 回退、请求体标记

## 3. A1 检索增强上下文
- [x] 3.1 检索词提取、全文查询、去重和预算、参考段格式
- [x] 3.2 注入到最新一条消息之前并扣除历史预算，仅人类触发的普通回复
- [x] 3.3 单测：检索词、去重/预算/排序、失败回退、默认关闭、注入位置
- [x] 3.4 真实 MySQL：只取本房间、只取窗口之前、排除失败生成

## 4. A4 分类重试与 A5 token 预算
- [x] 4.1 `ProviderError.kind/retry_after` 与四个适配器映射
- [x] 4.2 引擎按类型处理，开关 `AI_CLASSIFIED_RETRY`
- [x] 4.3 `estimate_tokens` 与 `AI_CONTEXT_TOKENS`
- [x] 4.4 单测：分类、Retry-After、拒绝不重试、减半一次、退避时间、token 预算

## 5. 配置与回归
- [x] 5.1 compose、`.env.example`、README
- [x] 5.2 `scripts/test.sh` 全量回归
