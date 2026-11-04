#!/bin/sh
set -eu
: "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
if [ ! -s /data/sentinel.conf ]; then
    # Redis 配置字符串需要转义，不能把密码当成配置指令。
    password=$(printf '%s' "$REDIS_PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')
    umask 077
    cat > /data/sentinel.conf <<EOF
port 26379
bind 0.0.0.0
protected-mode yes
dir /data
requirepass "$password"
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
sentinel monitor chat redis-1 6379 2
sentinel auth-pass chat "$password"
sentinel down-after-milliseconds chat ${SENTINEL_DOWN_AFTER_MS:-3000}
sentinel failover-timeout chat 10000
sentinel parallel-syncs chat 1
EOF
fi
exec redis-sentinel /data/sentinel.conf
