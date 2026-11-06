#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
name="cppai-components-$$"
cleanup() {
    docker rm -fv "$name-mysql" "$name-redis" >/dev/null 2>&1 || true
    docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
password=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
docker network create "$name" >/dev/null
docker run -d --name "$name-mysql" --network "$name" --network-alias mysql \
    -e MYSQL_RANDOM_ROOT_PASSWORD=yes -e MYSQL_DATABASE=chat -e MYSQL_USER=chat \
    -e "MYSQL_PASSWORD=$password" mysql:8.4.6 >/dev/null
docker run -d --name "$name-redis" --network "$name" redis:7.4.5 \
    redis-server --save '' --appendonly no >/dev/null
ready=0
for i in $(seq 1 60); do
    if docker exec "$name-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat -e "SELECT 1" chat' >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
done
[ "$ready" -eq 1 ] || { echo 'Disposable MySQL did not start' >&2; exit 1; }
for sql in migrations/V*.sql; do
    docker exec -i "$name-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat chat' < "$sql"
done
ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$name-redis")
build_dir=${COMPONENT_BUILD_DIR:-/private/tmp/cppai-buffering-component-build}
mkdir -p "$build_dir"
docker run --rm --network "$name" --entrypoint sh \
    -e "MYSQL_PASSWORD=$password" -e "QUEUE_TEST_IP=$ip" \
    -v "$PWD:/work" -v "$build_dir:/build" -w /work \
    "${UNIT_TEST_IMAGE:-cppaiserver-unit}" -c 'cmake -S . -B /build -DCMAKE_BUILD_TYPE=Debug -DCHAT_BUILD_TESTS=ON && cmake --build /build -j4 && ctest --test-dir /build --output-on-failure && /build/tests/unit/buffering-component-probe'
