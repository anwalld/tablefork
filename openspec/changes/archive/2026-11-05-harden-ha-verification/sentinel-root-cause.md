# Sentinel自引用缺陷

## 原因

Redis 7.4.5的`sentinelReceiveHelloMessages`会过滤自身ID，但`sentinelPublishCommand`也能直接调用`sentinelProcessHelloMessage`，公共入口缺少自身检查。持久同伴地址在容器地址复用后可能指向当前Sentinel，定期HELLO使其把自己加入同伴集合。

源码见[Redis 7.4.5 sentinel.c](https://github.com/redis/redis/blob/7.4.5/src/sentinel.c)。

## 修复

`docker/sentinel/ignore-self.patch`在HELLO公共入口校验第三字段MYID，忽略自身消息。配置加载且MYID确定后，仅移除同伴字典中匹配自身ID的条目并持久化，保留其他同伴、MYID、epoch和选票。字典键使用SDS类型。

构建固定Redis 7.4.5及源码SHA-256 `00bb280528f5d7934bec8ab309b8125088c209131e10609cb1563b91365633bb`，保留TLS支持。

## 回归入口

`python3 scripts/test_sentinel.py`覆盖直接自身HELLO、正常外部HELLO、持久化自身条目，以及地址复用后三个Sentinel重启的身份、同伴集合与CKQUORUM。使用`--image redis:7.4.5 --expect-vulnerable`验证原镜像缺陷。

## 运维

重建Sentinel服务时使用原数据卷并清理自身条目。升级Redis时核对上游实现与回归结果，再决定补丁的适用版本。
