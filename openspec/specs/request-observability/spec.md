# request-observability Specification

## Purpose

通过结构化日志及请求关联信息记录业务处理阶段与耗时，支持多实例异步链路的故障定位，并以字段白名单和日志保留策略保护敏感信息。

## Requirements

### Requirement: 结构化关联日志
系统 SHALL 输出含实例、操作、结果、请求关联编号及阶段耗时的结构化日志，覆盖HTTP、WS、队列和AI阶段。日志排除密码、API key、Cookie、令牌、正文和搜索词，配置有界轮转与保留。

#### Scenario: 关联与隐私
- **WHEN** 请求经过排队和异步处理后成功或失败
- **THEN** 日志能关联同一业务及各次尝试、说明阶段耗时，输出不包含敏感载荷，日志体积受配置约束
