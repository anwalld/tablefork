# Proposal

## Why

CppAiServer 目标是一个让多名用户和多个 AI 模型同房间聊天的公开服务，但项目目前只有 Hello World。AI 接入依赖房间、成员和实时消息这些基础能力，因此需要先做出一个可运行的多人聊天室，作为后续功能的底座。

## What Changes

- 搭建 C++20 + Drogon 后端工程，从源码构建 Drogon 并启用 MySQL 和 Redis 支持。
- 新增用户注册、登录和登录态校验。
- 新增聊天室的创建、邀请、加入、退出和列表查询。房间采用邀请制，只有被邀请的用户才能看见和加入。
- 最后一名人类成员退出时，服务端删除房间和历史消息。
- 新增基于 WebSocket 的实时消息收发，消息持久化到 MySQL，并支持查询历史消息。
- 新增 Vue 前端，覆盖登录、房间列表和聊天界面，支持人类用户把聊天记录导出到本地。
- 提供 Nginx 接入和 Docker 部署，能在本地一键启动完整环境。
- 房间成员模型保持通用，后续 AI 可以作为成员加入，无需重构。

**本次不包含：**

- 接入任何 AI 模型，以及独立 AI 服务、人设和记忆。
- 多实例部署。本次按单实例运行，Nginx 的 L4 层也随多实例阶段加入。
- 内容审核、付费和额度体系。

## Capabilities

### New Capabilities

- `user-auth`: 用户注册、登录和登录态校验。
- `chat-rooms`: 聊天室的创建、邀请、加入、退出、列表查询和删除，以及房间成员关系。
- `realtime-messaging`: 房间内消息的实时收发、持久化、历史查询和本地导出。

### Modified Capabilities

无。

## Impact

- **代码：** 新增后端和前端工程，替换现有的 `main.cpp` 示例，`CMakeLists.txt` 从 C++17 升级到 C++20。
- **接口：** 新增用于账号和房间管理的 HTTP API，以及用于实时消息的 WebSocket 接口。
- **依赖：** 新增源码构建并打补丁的 Drogon、MariaDB Connector/C、hiredis、Vue，运行时依赖 MySQL、Redis 和 Nginx。Redis 用于消息热缓存、房间广播（Pub/Sub）和登录会话，详见 `design.md`。
- **部署：** 新增 Dockerfile、Docker Compose 配置和 Nginx 配置。Redis 部署一主一从并启用密码认证，本次手动切换，自动切换随多实例阶段加入。密码等敏感配置放在不进 Git 的 `.env` 中，仓库只提供 `.env.example`。
