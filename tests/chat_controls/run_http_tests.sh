#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
name="cppai-chat-controls-$$"
unit_image=${CHAT_CONTROL_UNIT_IMAGE:-cppaiserver-unit}
test_image=$(docker compose --profile test config --format json | python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["services"]["tests"].get("image") or c["name"]+"-tests")')
cleanup() {
    result=$?
    if [ "$result" -ne 0 ]; then docker logs "$name-app" 2>&1 || true; fi
    docker logs "$name-app" > "${TMPDIR:-/tmp}/cppai-chat-controls-http-app.log" 2>&1 || true
    docker rm -fv "$name-app" "$name-mysql" "$name-redis" >/dev/null 2>&1 || true
    docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
password=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
docker network create "$name" >/dev/null
docker run -d --name "$name-mysql" --network "$name" --network-alias mysql \
    -e MYSQL_RANDOM_ROOT_PASSWORD=yes -e MYSQL_DATABASE=chat -e MYSQL_USER=chat \
    -e "MYSQL_PASSWORD=$password" mysql:8.4.6 >/dev/null
docker run -d --name "$name-redis" --network "$name" --network-alias redis-proxy \
    -e "REDISCLI_AUTH=$password" redis:7.4.5 sh -c 'exec redis-server --save "" --appendonly no --requirepass "$REDISCLI_AUTH"' >/dev/null
ready=0
for i in $(seq 1 60); do
    if docker exec "$name-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat -e "SELECT 1" chat' >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
done
[ "$ready" -eq 1 ] || exit 1
for sql in migrations/V*.sql; do
    if [ "$(basename "$sql")" = V6__attention_and_ai_controls.sql ]; then
        docker exec -i "$name-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat chat' < tests/chat_controls/upgrade_fixture.sql
    fi
    docker exec -i "$name-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat chat' < "$sql"
done
set -- --entrypoint /src/build/chat-server
if [ -n "${CHAT_CONTROL_BUILD_DIR:-}" ]; then
    set -- -v "$CHAT_CONTROL_BUILD_DIR:/build:ro" --entrypoint /build/chat-server
fi
docker run -d --name "$name-app" --network "$name" --network-alias app \
    -e "MYSQL_PASSWORD=$password" -e "REDIS_PASSWORD=$password" -e BUFFER_REQUESTS_ENABLED=0 \
    -e COOKIE_SECURE=0 -e MESSAGE_RATE_LIMIT=10000 -e PASSWORD_BLOCKLIST_FILE=/work/data/common-passwords.txt \
    -v "$PWD:/work:ro" "$@" "$unit_image" >/dev/null
docker run --rm --network "$name" -v "$PWD:/work:ro" -e "MYSQL_PASSWORD=$password" \
    "$test_image" python /work/tests/chat_controls/test_http.py
