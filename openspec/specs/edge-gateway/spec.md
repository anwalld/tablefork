# edge-gateway Specification

## Purpose

规定浏览器怎样进入系统：只用加密的 HTTPS 和 WSS 传输，系统能识别真实的客户端地址，并按客户端地址限制并发连接，避免单个来源占满入口。

## Requirements

### Requirement: 只通过 HTTPS 访问
系统 SHALL 只通过 HTTPS 提供页面、接口和 WebSocket（WSS），浏览器与系统之间的流量 MUST 加密，包括密码、API key 和聊天内容。系统 MUST 至少支持 TLS 1.2，MUST NOT 接受 TLS 1.0、TLS 1.1 和 SSL。

#### Scenario: 通过 HTTPS 登录
- **WHEN** 用户通过 HTTPS 入口提交用户名和密码
- **THEN** 系统正常处理登录，请求在传输中是加密的

#### Scenario: 通过 WSS 建立连接
- **WHEN** 已登录用户通过 HTTPS 页面发起实时连接
- **THEN** 连接使用 WSS 建立，并按“建立实时连接”的规则校验

#### Scenario: 旧协议
- **WHEN** 客户端只支持 TLS 1.1 或更早的协议
- **THEN** 握手失败，客户端无法访问

### Requirement: HTTP 跳转到 HTTPS
系统 SHALL 把所有明文 HTTP 请求用 301 重定向到相同路径的 HTTPS 地址。明文 HTTP 请求 MUST NOT 被交给聊天服务处理，明文的 WebSocket 连接 MUST NOT 建立。

#### Scenario: 打开 HTTP 页面
- **WHEN** 浏览器访问 HTTP 入口的某个路径
- **THEN** 系统返回 301，`Location` 指向 HTTPS 入口的同一路径和查询参数

#### Scenario: 通过 HTTP 提交登录
- **WHEN** 客户端通过 HTTP 入口提交登录请求
- **THEN** 系统返回 301，不处理这次登录，也不产生登录失败计数

#### Scenario: 明文 WebSocket
- **WHEN** 客户端通过 HTTP 入口发起 WebSocket 连接
- **THEN** 连接不会建立

### Requirement: cookie 只经 HTTPS 发送
系统设置的会话 cookie 和设备 cookie MUST 默认带有 `Secure` 属性，浏览器只会在 HTTPS 请求中发送它们。本地调试时 SHALL 可以通过配置关闭 `Secure`。

#### Scenario: 登录后的 cookie
- **WHEN** 用户通过 HTTPS 登录成功
- **THEN** 响应设置的会话 cookie 和设备 cookie 都带有 `Secure`、`HttpOnly` 属性

### Requirement: 本地证书
系统 SHALL 在首次启动时自动生成一个本地 CA 和由它签发的服务器证书，证书 MUST 覆盖 `localhost` 和 `127.0.0.1`。证书已存在时 MUST NOT 被覆盖；服务器证书临近过期时 SHALL 用原 CA 重新签发。私钥 MUST NOT 进入版本库。

#### Scenario: 首次启动
- **WHEN** 在没有证书的环境中启动系统
- **THEN** 系统生成本地 CA 和服务器证书，HTTPS 入口使用该服务器证书

#### Scenario: 再次启动
- **WHEN** 证书已存在且未临近过期时再次启动
- **THEN** 系统沿用原有的 CA 和证书，已导入该 CA 的浏览器不需要重新导入

#### Scenario: 信任本地 CA 的客户端
- **WHEN** 客户端信任本地 CA 后访问 `https://localhost` 入口
- **THEN** 证书校验通过

#### Scenario: 版本库
- **WHEN** 检查版本库中跟踪的文件
- **THEN** 其中不包含 CA 或服务器证书的私钥

### Requirement: 识别真实客户端地址
请求经过多层接入后，系统 SHALL 仍以浏览器的真实地址作为客户端地址，用于限流、限制连接数和日志。客户端自己发送的 `X-Forwarded-For` 等头部 MUST NOT 改变系统认定的客户端地址。

#### Scenario: 经过多层接入
- **WHEN** 浏览器的请求依次经过各层接入到达聊天服务
- **THEN** 系统认定的客户端地址是浏览器的地址，而不是任何一层接入的地址

#### Scenario: 伪造转发头
- **WHEN** 客户端在请求中自带 `X-Forwarded-For` 头，填写其他地址
- **THEN** 系统认定的客户端地址仍是它的真实地址

### Requirement: 按客户端地址限制并发连接数
系统 SHALL 限制每个客户端地址同时打开的 TCP 连接数，上限可配置。超出上限的新连接 MUST 被直接拒绝，MUST NOT 影响该地址已建立的连接和其他地址的连接。

#### Scenario: 超出上限
- **WHEN** 某个客户端地址已打开上限数量的连接，再打开一个新连接
- **THEN** 新连接被拒绝，已有连接继续正常工作

#### Scenario: 连接关闭后
- **WHEN** 达到上限的客户端地址关闭了部分连接
- **THEN** 它可以再打开同样数量的新连接

#### Scenario: 其他地址不受影响
- **WHEN** 一个客户端地址达到上限
- **THEN** 其他客户端地址仍能正常建立连接
