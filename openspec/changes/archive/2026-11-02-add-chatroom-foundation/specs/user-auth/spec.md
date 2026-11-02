# Spec Delta

## Purpose

让用户通过用户名和密码注册并登录，并为 HTTP 和 WebSocket 接口提供统一的登录态校验。

## ADDED Requirements

### Requirement: 用户注册
系统 SHALL 允许访客使用唯一的用户名和密码注册账号。用户名 MUST 为 3 到 32 个字符且不含控制字符，密码 MUST 不少于 8 个字符。

#### Scenario: 注册成功
- **WHEN** 访客提交未被占用且格式合法的用户名和密码
- **THEN** 系统创建账号并返回成功

#### Scenario: 用户名已被占用
- **WHEN** 访客提交已存在的用户名
- **THEN** 系统拒绝注册并返回用户名已被占用的错误

#### Scenario: 格式不合法
- **WHEN** 访客提交长度不符合要求的用户名或密码，或用户名包含控制字符
- **THEN** 系统拒绝注册并返回参数错误

### Requirement: 密码安全存储
系统 MUST NOT 保存或在任何响应中返回明文密码，只保存加盐的单向哈希。

#### Scenario: 查询账号数据
- **WHEN** 账号注册成功后检查其存储数据
- **THEN** 存储中不存在明文密码

### Requirement: 用户登录
系统 SHALL 在用户名和密码正确时建立会话，并通过 `HttpOnly` Cookie 下发随机会话令牌。

#### Scenario: 登录成功
- **WHEN** 用户提交正确的用户名和密码
- **THEN** 系统建立会话并在响应中设置 `HttpOnly` Cookie

#### Scenario: 凭据错误
- **WHEN** 用户提交不存在的用户名或错误的密码
- **THEN** 系统返回同样的认证失败错误，不透露用户名是否存在

### Requirement: 登录态校验
系统 SHALL 拒绝未携带有效会话的受保护 HTTP 请求和 WebSocket 连接。

#### Scenario: 会话有效
- **WHEN** 用户携带有效会话访问受保护的 HTTP 接口
- **THEN** 系统正常处理请求

#### Scenario: 缺少或无效会话
- **WHEN** 请求未携带会话，或携带的会话无效
- **THEN** 系统返回未认证错误

#### Scenario: 会话过期
- **WHEN** 用户携带已过期的会话访问受保护接口
- **THEN** 系统返回未认证错误

### Requirement: 用户登出
系统 SHALL 允许已登录用户登出，登出后其当前会话立即失效。

#### Scenario: 登出成功
- **WHEN** 已登录用户请求登出
- **THEN** 系统使当前会话失效，之后使用该会话的请求返回未认证错误
