# Spec Delta

## MODIFIED Requirements

### Requirement: 认证请求按来源限流
系统 SHALL 按客户端 IP 限制登录和注册请求的速率，超出时 MUST 返回 HTTP 429 和可重试的 `rate_limited` 错误，且不把请求交给应用处理。客户端 IP MUST 按“识别真实客户端地址”的规则认定；同一 IP 的限额 MUST NOT 因为请求被分到不同的接入实例而放大。

#### Scenario: 同一 IP 频繁请求
- **WHEN** 同一 IP 在短时间内发出超过允许次数的登录或注册请求
- **THEN** 超出的请求收到 HTTP 429 和 `rate_limited` 错误

#### Scenario: 其他接口不受影响
- **WHEN** 同一 IP 的认证请求已被限流，它访问其他接口或 WebSocket
- **THEN** 这些请求正常处理

#### Scenario: 多个新连接
- **WHEN** 同一 IP 每次请求都新建连接，连续发出超过允许次数的登录请求
- **THEN** 超出的请求同样收到 HTTP 429，与这些连接被分到哪个接入实例无关

#### Scenario: 不同 IP
- **WHEN** 一个 IP 已被限流，另一个 IP 发出登录请求
- **THEN** 另一个 IP 的请求正常处理
