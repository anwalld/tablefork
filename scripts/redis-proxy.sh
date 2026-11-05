#!/bin/sh
set -eu
export LC_ALL=C
: "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
auth_command() {
    printf '*2\r\n$4\r\nAUTH\r\n$%s\r\n%s\r\n' "${#REDIS_PASSWORD}" "$REDIS_PASSWORD"
}
if [ "${1:-}" = health ]; then
    [ -s /tmp/redis-master-watch.pid ] && kill -0 "$(cat /tmp/redis-master-watch.pid)" || exit 1
    { auth_command; printf '*1\r\n$4\r\nPING\r\n*1\r\n$4\r\nQUIT\r\n'; } |
        nc -w 2 127.0.0.1 6379 | grep -q '^+PONG'
    exit $?
fi
if [ "${1:-}" = switch ]; then
    [ "${BUFFER_REQUESTS_ENABLED:-0}" = 1 ] || exit 1
    exec python3 /usr/local/bin/request-buffer-coordinator.py request
fi
# RESP 的十六进制形式，避免空格、引号或反斜杠改变 AUTH 的参数边界。
REDIS_AUTH_HEX=$(auth_command | od -An -v -tx1 | tr -d ' \n')
export REDIS_AUTH_HEX
until REDIS_MASTER_HOST=$(sh /usr/local/bin/redis-master-watch.sh query); do
    sleep 1
done
export REDIS_MASTER_HOST
sh /usr/local/bin/redis-master-watch.sh &
echo "$!" > /tmp/redis-master-watch.pid
exec docker-entrypoint.sh haproxy -f /usr/local/etc/haproxy/haproxy.cfg
