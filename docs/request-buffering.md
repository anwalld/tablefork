# Redis 切换期间的业务暂存

本功能启用后，HTTP业务和WS聊天发送使用同一共享暂存机制。正常状态不创建任务，暂停时入桶；恢复时新旧请求同桶，由每台后端按自己的节奏领取；全部等待及执行任务清空后原子切回直通。首次受理不是业务成功，聊天最终ACK仍以MySQL提交为依据。

## 部署与地址

- `queue-redis`与业务Redis及`redis-proxy`独立，单实例，关闭RDB/AOF，不挂业务卷，使用独立密码。
- 所有后端使用同一`QUEUE_ENCRYPTION_KEY`（64位十六进制）与`BUFFER_CONTROL_TOKEN`（至少32字节），分别生成，不能复用模型API key。部署秘密放`.env`，不写入Git、日志或交接。
- Compose默认代理地址`172.28.0.10`、桶地址`172.28.0.11`，普通容器从`172.28.128.0/17`动态分配。两固定地址必须位于`DOCKER_SUBNET`内、动态池外。自定义网段时同时设置`DOCKER_DYNAMIC_RANGE`、`REDIS_PROXY_IP`和`QUEUE_REDIS_IP`。
- 固定地址是恢复保证的一部分：Drogon当前仅启动时解析Redis主机名。动态地址曾导致代理重启后，旧地址被副本复用，出现PING成功但写入READONLY；不能只看PING就认为后端路由正确。
- 原网络修改IPAM配置需要维护窗口重建网络，保留MySQL、业务Redis和Sentinel的数据卷。不要使用`down -v`迁移真实环境。本次只操作隔离测试项目，不自动部署或重建原项目。
- 前后端须同步更新。新协议引入202/queued，旧页面需要刷新；不把旧客户端误认成功当成兼容保证。

## 参数

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| BUFFER_REQUESTS_ENABLED | 1（Compose） | 启用业务闸门与协调；单独运行二进制默认关闭 |
| BUFFER_PERMIT_MS | 5000 | 本机单调时钟许可期限 |
| BUFFER_RENEW_SECONDS | 1 | 协调续约间隔 |
| BUFFER_BACKENDS | app-1 app-2 | 受协调的后端控制地址 |
| QUEUE_MAX_WAITING | 1024 | 全局尚未开始的任务数 |
| QUEUE_MAX_BODY_BYTES | 16777216 | 全局等待请求体字节 |
| QUEUE_MAX_MEMORY_BYTES | 67108864 | 应用计量的载荷/结果及元数据预算 |
| QUEUE_DRAIN_RATE | 50 | 每实例每秒最多开始的任务数 |
| QUEUE_MAX_RUNNING | 8 | 每实例同时执行上限 |
| QUEUE_WAIT_MS | 45000 | 尚未开始时的排队期限，不因重试重置 |
| QUEUE_RESULT_MS | 300000 | 终态结果缓存时间 |
| QUEUE_CLAIM_MS | 10000（代码默认） | 执行者丢失后的接手判断期限 |
| QUEUE_MAX_RESULT_BYTES | 16777216（代码默认） | 单个加密结果上限 |

50/s和8并发是放行上限，不是吞吐实测承诺。FIFO是共享桶原子领取顺序；并发任务完成时间可以不同。漏桶不积累信用，长时间暂停后不会追赶欠下的额度。

共享容量和期限在桶初始化时保存；只重启应用不会热更新已有桶的配置。调整这些参数应先排空任务，再重启专用桶并保持所有后端配置一致。`QUEUE_CLAIM_MS`和`QUEUE_MAX_RESULT_BYTES`若需在Compose中修改，通过应用的environment覆盖传入。

计量包括加密载荷/结果长度和每任务2KiB元数据，等待请求体另受16MiB限制。Redis自身设置128MiB和`noeviction`，留出数据结构/分配器余量。达到任一受理预算就拒绝新请求，保留旧任务；结果写回不能确认时，不伪造业务成功。

## 客户端协议

HTTP调用携带`X-Queue-Owner`（客户端生成的64位十六进制安全随机凭证）和`X-Request-Id`（一次业务的稳定编号，最长128字节）。自动重试须复用同一编号和参数。新客户端在登录/登出成功后轮换后续任务的凭证，正在查询的任务继续使用它原来的凭证。

暂停/积压时返回202与`task_id`、`state`、`claim_token`。响应不会提前建立登录Cookie。客户端约每秒查询`GET /api/tasks/{task_id}`，在请求头继续传原`X-Queue-Owner`。任务ID单独不构成领取权限，错误凭证拿不到结果或Cookie。

查询状态：

- `waiting`：尚未开始；`running`：正在执行；`recovering`：SQL等可安全恢复任务正在接手。
- `succeeded`、`failed`：返回`response.status`、原`response.body`（JSON字符串）及必要响应信息。
- 登录等成功结果的Cookie通过真正的Set-Cookie交付，不放在前端可读的JSON中。刷新/登出保留原Cookie属性和清除语义。
- `expired`：45秒仍未开始，之后不得执行。
- `unknown`：不能确认执行结果，不能解读为“没有执行”，不能盲目重做。

查询暂不可用时返回可重试503，仅重试查询。正常直通也需要共享状态原子判断，所以共享Redis不可用时新业务同样返回可重试503，不在未知积压状态下旁路。

WS发送保留原`room_id`、`client_msg_id`和内容，附带`queue_owner`及物理`queue_attempt`。收到`queued`只表示临时受理，不能删除待发副本或当作ACK；查询最终结果后才按原ACK完成。明确的限流拒绝可换物理排队attempt，逻辑消息ID保持不变。页面和共享桶都没有持久化保证，刷新页面不承诺恢复原待发副本。

## 业务恢复矩阵

| 业务 | 去重和恢复依据 | 无法确认时 |
| --- | --- | --- |
| 建房、邀请、加入、离开、角色、踢人、封禁、解封 | 调用者/请求编号唯一约束，业务写与结果记录同一MySQL事务 | 新执行者进入同一事务保护，不能先查不到就盲写 |
| 注册、AI成员申请/审批/移除、删除key | 同一事务结果记录；注册绑定匿名领取者 | 按已提交结果恢复或保留未确认 |
| 聊天发送 | 原`(room_id, member_id, client_msg_id)`唯一键，提交后ACK；同ID变内容拒绝 | 重新认证/核对权限后恢复保存结果 |
| 登录、刷新、登出、gRPC存储key | 先持久化`kind=external`意向；凭据只在加密临时结果内 | 旧意向不得再次执行，返回unknown，不用SQL事务冒充跨系统原子性 |
| 读取 | 执行时重新认证和查询当前状态 | 可安全恢复读取，但权限变化仍生效 |

SQL请求记录不随房间删除、不自动过期，防止旧编号重新创建已删除资源。它记录业务结果/意向，不是持久化任务桶。密钥和参数HMAC须保持稳定，不能在仍有旧请求需恢复时随意更换。

恢复旧SQL结果时不重复旧的踢人/离开通知；改为按当前SQL状态对账。否则目标重新加入后，旧请求重放可能错误移除其新订阅。

## 切换与许可

主动切主使用既有代理内脚本入口：

```sh
docker compose exec redis-proxy sh /redis-proxy.sh switch
```

脚本先暂停并等待后端确认，缺一台就取消尚未发起的Sentinel切主。恢复前核对原主与实际路由。不要用直接Sentinel FAILOVER代替这个生产入口；测试可把外部直接改票作为故障注入。

自动故障不阻止Sentinel选新主。先停旧路由并清旧会话，确认多数派、新主角色、HAProxy实际FQDN/IP及代理所连节点身份；旧许可失效后，健康后端可以先恢复。

许可绑定后端启动标识、协调epoch、暂停revision及一次性challenge。期限从后端发起challenge时起算，迟到grant不从收到时重新计时。业务入口、领取和挂起后的新SQL/Redis副作用会重新检查；冻结进程恢复后也不能沿用过期stamp。

协调线程在发送grant时持phase锁，撤销phase后不会再发送它的旧grant；迟到challenge回复必须再次匹配当前phase。路由证明过旧时不能续约。协调者重启建立新epoch并等待旧期限。控制、存活、结果查询与成员安全对账独立于业务桶，不新增Docker socket、宿主机管理或后端容器隔离权限。

## 验证与交接

当前change的`handoff.md`和`verification-report.md`记录准确提交/源码、日志、退出码、未测项目和归档结果。单元、组件、真实进程故障、Sentinel切换及完整严格回归分别报告，不能用旧PR的CI或一次数据库查询代替完整链路。

测试专用Compose覆盖使用独立镜像/项目和控制路径故障代理；它不是生产组件。C++提交后等待钩子仅在`CHAT_BUILD_TESTS`构建，runtime不编译。只使用fake模型，不增加真实模型费用。
