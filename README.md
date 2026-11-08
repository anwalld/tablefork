<div align="center">

# Tablefork 分桌

### 可表决、可分桌、断线不丢的人类与 AI 实时会议室

[![CI](https://github.com/anwalld/tablefork/actions/workflows/ci.yml/badge.svg)](https://github.com/anwalld/tablefork/actions/workflows/ci.yml)
![C++20](https://img.shields.io/badge/C%2B%2B-20-00599C?logo=cplusplus)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Vue 3](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[快速开始](#快速开始) · [协作能力](docs/collaboration.md) · [架构](docs/architecture.md) · [文档](docs/README.md) · [鸣谢](ACKNOWLEDGEMENTS.md)

</div>

Tablefork 是一个可自托管的人类与 AI 实时会议室，让多个人类和多个 AI 在同一个房间里讨论、表决、分组推进工作。主持人（AI）安排发言，主席团（由 AI 组成）投票拍板，需要时把一部分成员拉去“分桌”开小会，结论回到主桌。

> **分桌（fork）**：小会像 `fork` 一样继承开会那一刻主桌的上下文，消耗按 cgroup 的方式记在上级账上，结束时把结论交回主桌。主桌的人类全程可见。

## 核心特性

- **[主席团表决](docs/collaboration.md#能力一览)**：`/vote` 发起表决，由主席（AI）各自独立判断，仿 Redis Sentinel 的“主观下线 / 客观下线”计票。至少 3 张有效票、加权过半才通过；领头主席只算 1.5 票，每轮轮换；结论保留少数派意见，人类可以否决。
- **[分桌小会](docs/collaboration.md#文字指令)**：`/breakout @成员 议题` 拉一部分成员开小会。小会继承上下文，有层级预算，可以暂停、恢复、结束；结论作为消息回到主桌，主桌可以旁观和搜索。
- **[主持人与超时确认](docs/collaboration.md#能力一览)**：主持人（AI）按分工安排谁发言；冷场时只在有待办时才确认；有副作用的动作必须有权限的人类点头，超时也不执行。
- **[记忆与资料库](docs/collaboration.md#上下文的组成)**：长对话滚动摘要，分层记忆可查看、可删除；导入日志和文档后做语义检索，回答时标明资料名和行号。
- **[工具与 MCP](docs/collaboration.md#能力一览)**：只能调用部署时登记的工具，只读工具直接执行，有副作用的经人类确认后最多执行一次。
- **[可靠的实时底座](docs/architecture.md#工程亮点)**：消息按 `seq` 有序、去重、断线补齐；双实例加 Redis Sentinel 自动切主，切主期间请求暂存；AI 回复有租约，崩溃后自动重新生成，只保存一次。

新建房间默认打开协作模式；群主或管理员关掉后，房间退化为普通的多人 AI 聊天室。摘要、记忆、资料库和工具会产生额外的模型调用，需要在部署时单独打开。

## 为什么用 C++

- **实时链路要快**：每条消息都要在房间锁内分配序号、落库、跨实例推送，同时撑住大量 WebSocket 长连接。用 C++20 协程（Drogon）实现，在 10 核的 Docker Desktop 上压测，32 个房间并发约每秒 4600 条，p99 约 12 ms。
- **顺序和权限要严**：消息排序、去重、权限判断只在 C++ 这一处完成，AI 层不直接接触用户和房间数据。
- **AI 层要生态，就交给 Python**：模型 SDK、MCP、提示词迭代放在 Python 服务里，两边用 gRPC 隔开。AI 服务崩溃或重启，人类之间的聊天照常，未完成的回复会自动重新生成。

详见[架构](docs/architecture.md#为什么用-c)。

## 快速开始

### 环境要求

- Docker Engine 与 Docker Compose v2（开发和 CI 使用 Compose 5.x）
- OpenSSL，用来生成本地密码
- 整套服务有 14 个容器，开发和测试是在 10 核、8 GB 内存的 Docker Desktop 上进行的

### 1. 获取代码

```bash
git clone https://github.com/anwalld/tablefork.git
```

```bash
cd tablefork
```

### 2. 配置

```bash
cp .env.example .env
```

把 `.env` 里每个 `change-me` 换成随机值（`openssl rand -hex 24`，加密主密钥用 `openssl rand -base64 32`），各项含义见 `.env.example` 的注释。

### 3. 启动

```bash
docker compose up -d --build --wait
```

首次启动会构建镜像、生成本地证书并执行数据库迁移。

### 4. 验证

```bash
curl --cacert data/certs/ca.crt https://localhost:8443/health
```

然后在浏览器打开 <https://localhost:8443>，注册账号，在“设置”里填写模型服务商的 API key，建房间、邀请 AI。浏览器信任本地证书的方法见[部署、运维与开发](docs/operations.md#启动与配置细节)。

### 和别人一起用

Tablefork 是自托管的：由一个人部署服务，其他人类用浏览器打开同一个网址、注册账号就能进房间，不需要安装任何东西。每个人用自己的 API key 为自己触发的 AI 回复付费。

**部署到有域名的服务器**（推荐）：

1. 准备一台装有 Docker 的服务器，把域名解析到它，并申请正式证书（例如 Let's Encrypt）。
2. 把完整证书链存为 `data/certs/server.crt`，私钥存为 `data/certs/server.key`。
3. 在 `.env` 里设置：

   ```bash
   CERT_MODE=provided
   HTTPS_PORT=443
   HTTP_PORT=80
   ALLOWED_ORIGINS=https://chat.example.com
   COOKIE_SECURE=1
   ```

4. 启动后，把 `https://chat.example.com` 发给要一起用的人。

`CERT_MODE=provided` 时只校验证书和私钥是否匹配、是否过期，从不生成或覆盖它们；续期后替换这两个文件再重启 `web-1`、`web-2` 即可。

**在局域网或没有域名时**：在 `.env` 里用 `PUBLIC_HOSTS` 加上别人访问用的 IP 或主机名（例如 `PUBLIC_HOSTS=192.168.1.20`），并把它加进 `ALLOWED_ORIGINS`。本地 CA 签发的证书会包含这个地址，访问者需要先导入 `data/certs/ca.crt`。

### 可选：没有 API key 先试

```bash
AI_FAKE_PROVIDERS=1 docker compose up -d --build --wait
```

所有服务商都换成假模型，以 `fake-` 开头的 key 都能通过，AI 回复“名字: 你的上一条消息”。仅用于本地试用。

### 可选：打开协作能力

| 能力 | 开关（`.env`） | 额外要求 |
|---|---|---|
| 主持人（AI）、主席团、小会、超时确认 | 无需开关 | 新房间默认打开协作模式；主持人使用发言者自己的 DeepSeek key，没有时退回原规则 |
| 历史摘要、记忆 | `AI_SUMMARY_ENABLED=1`、`AI_MEMORY_ENABLED=1` | 房间处于协作模式 |
| 资料库 | `AI_KNOWLEDGE_ENABLED=1` | 用 `docker compose --profile knowledge up -d --wait` 启动 pgvector |
| 工具与 MCP | `AI_TOOLS_ENABLED=1` | 在 `ai/tools.toml` 登记工具 |
| 桌型模板 | 无需开关 | 房间里一键拉起 |

完整配置项和费用规则见[多 AI 协作房间](docs/collaboration.md#配置项)。

## 文档

| 文档 | 内容 |
|---|---|
| [多 AI 协作房间](docs/collaboration.md) | 指令、HTTP 接口、配置项、费用、可靠性 |
| [架构](docs/architecture.md) | 服务拓扑、消息生命周期、技术栈、为什么用 C++、目录结构 |
| [部署、运维与开发](docs/operations.md) | 证书、端口、迁移、Redis 切换演示、测试、压测、开发环境 |
| [账号、房间与 AI 参考](docs/accounts-rooms-ai.md) | 账号安全、踢人封禁、AI 密钥与开关 |
| [文档索引](docs/README.md) | 全部文档与 OpenSpec 规格 |

## 参与开发

行为规格和设计在 `openspec/` 下，按 OpenSpec 流程先写规格再实现。提交前运行完整测试：

```bash
scripts/test.sh
```

它会依次运行 C++、Python、前端单元测试，完整环境的集成测试，以及 AI 服务中断、Redis 主节点宕机、web 实例宕机等故障测试。说明见[运行测试](docs/operations.md#运行测试)。

## 安全

发现安全问题时，请通过 GitHub 的 [Security Advisories](https://github.com/anwalld/tablefork/security/advisories/new) 私下报告，不要公开提 issue。

## 许可证

[MIT](LICENSE)。其中 `ai/chat_ai/recall.py`、`knowledge.py` 和 `host.py` 参考了 LobeHub 的提示词和结构，这部分同时遵守 [LobeHub Community License](https://github.com/lobehub/lobehub/blob/main/LICENSE)，详见[鸣谢](ACKNOWLEDGEMENTS.md)。

## 鸣谢

Tablefork 的协作能力参考了 [LobeHub](https://github.com/lobehub/lobehub)、[LangGraph](https://github.com/langchain-ai/langgraph)、[LLM Council](https://github.com/karpathy/llm-council)、[ChatDev](https://github.com/OpenBMB/ChatDev) 和 [OpenAgents](https://github.com/openagents-org/openagents)，计票思路来自 Redis Sentinel，小会的设计来自 Linux `fork` 和 cgroup。各项目借鉴了什么、我们改进了什么，以及贡献者名单，见 [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md)。
