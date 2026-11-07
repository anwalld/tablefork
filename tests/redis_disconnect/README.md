# Redis断连回归

使用一次性本机Redis验证Drogon v1.9.13断连后发送、订阅重连、错误认证和非法数据库选择。正常应用构建使用`docker/drogon-redis-disconnect.patch`。

## 构建

先按项目Dockerfile构建unit镜像，再构建此目录的检测镜像。

```sh
docker build --target unit -t cppaiserver-unit .
docker build -f tests/redis_disconnect/Dockerfile \
  --build-arg BUILD_IMAGE=cppaiserver-unit --build-arg PATCHED=1 \
  -t cppai-redis-asan-patched .
```

检测镜像固定Drogon提交`4c5430757ea5451a7c38fbbef4b4bef7dbb47f2f`和hiredis v1.2.0，对hiredis、Trantor、Drogon及探针启用ASan/UBSan。CMake显式链接`/opt/redis-probe/lib/libhiredisd.so`，Debug库名有`d`后缀。可通过镜像内`ldd /probe/build/redis-lifecycle`确认实际动态库路径。检测日志应保留原始退出码。

## 运行

每次运行创建独立容器与网络，结束时清理该次资源。证据目录由调用者选择并保留在仓库外。

```sh
sh tests/redis_disconnect/run_late_send.sh cppai-redis-asan-patched /tmp/redis-late late
sh tests/redis_disconnect/run_late_send.sh cppai-redis-asan-patched /tmp/redis-reconnect reconnect
sh tests/redis_disconnect/run_late_send.sh cppai-redis-asan-patched /tmp/redis-auth invalid-auth
sh tests/redis_disconnect/run_late_send.sh cppai-redis-asan-patched /tmp/redis-db invalid-db
```

late在连接关闭后跨线程发送，要求仅一次Connection is broken回调。连接构造和回调安装在同一事件循环串行完成。

reconnect发送200条命令，故障阶段关闭本次Redis的普通和订阅连接，检查后续合法命令恢复、每个命令回调一次及订阅取消。错误配置模式要求全部命令仅一次失败回调。检测器报错或断言失败保留非零退出码。

将PATCHED改为0可构建同版本原版对照。原版的非零退出、超时、缺失回调与检测器堆栈应分别记录。单个探针解释其覆盖的机制，历史聊天室崩溃的关联仍需故障现场证据。

## 生命周期约束

hiredis断连回调可能释放客户端持有的最后一个RedisConnection引用，连接析构同时销毁Channel。Channel事件处理在读回调返回后仍会检查写事件。补丁将连接所有权保留到当前活动事件批次完成后的队列执行阶段，同时对已结束连接的发送和订阅操作执行状态检查。
