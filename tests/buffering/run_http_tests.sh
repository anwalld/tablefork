#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
binary=/private/tmp/cppai-buffering-component-build/chat-server
[ -f "$binary" ] || { echo 'Compile the current source first' >&2; exit 1; }
[ -z "$(find src -type f -newer "$binary" -print -quit)" ] || { echo 'Binary is older than source; rebuild first' >&2; exit 1; }
name="cppai-http-buffer-$$"
fault_dir=$(mktemp -d /private/tmp/cppai-buffer-faults.XXXXXX)
cleanup() {
    result=$?
    if [ "$result" -ne 0 ]; then
        docker logs "$name-a" 2>&1 || true
        docker logs "$name-b" 2>&1 || true
    fi
    docker rm -fv "$name-a" "$name-b" "$name-mysql" "$name-redis" "$name-queue" "$name-tests" >/dev/null 2>&1 || true
    docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
password=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
queue_password=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
control=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
encryption=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
docker network create "$name" >/dev/null
docker run -d --name "$name-mysql" --network "$name" --network-alias mysql \
    -e MYSQL_RANDOM_ROOT_PASSWORD=yes -e MYSQL_DATABASE=chat -e MYSQL_USER=chat \
    -e "MYSQL_PASSWORD=$password" mysql:8.4.6 >/dev/null
docker run -d --name "$name-redis" --network "$name" --network-alias redis-proxy \
    -e "REDISCLI_AUTH=$password" redis:7.4.5 sh -c 'exec redis-server --save "" --appendonly no --requirepass "$REDISCLI_AUTH"' >/dev/null
docker run -d --name "$name-queue" --network "$name" --network-alias queue-redis \
    -e "REDISCLI_AUTH=$queue_password" -v "$PWD/docker/queue-redis.conf:/queue-redis.conf:ro" \
    redis:7.4.5 sh -c 'exec redis-server /queue-redis.conf --requirepass "$REDISCLI_AUTH"' >/dev/null
ready=0
for i in $(seq 1 60); do
    if docker exec "$name-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat -e "SELECT 1" chat' >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
done
[ "$ready" -eq 1 ] || exit 1
for sql in migrations/V*.sql; do docker exec -i "$name-mysql" sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat chat' < "$sql"; done
for instance in a b; do
    case "$instance" in a) alias=app-1; delay=2000;; b) alias=app-2; delay=0;; esac
    docker run -d --name "$name-$instance" --network "$name" --network-alias "$alias" \
        -e "MYSQL_PASSWORD=$password" -e "REDIS_PASSWORD=$password" \
        -e BUFFER_REQUESTS_ENABLED=1 -e "BUFFER_CONTROL_TOKEN=$control" \
        -e "QUEUE_REDIS_PASSWORD=$queue_password" -e "QUEUE_ENCRYPTION_KEY=$encryption" \
        -e "BUFFER_TEST_AFTER_EXECUTION_MS=$delay" -e COOKIE_SECURE=0 -e PASSWORD_BLOCKLIST_FILE=/work/data/common-passwords.txt \
        -v "$PWD:/work:ro" -v /private/tmp/cppai-buffering-component-build:/build:ro \
        --entrypoint /build/chat-server cppaiserver-unit >/dev/null
done
docker run -d --name "$name-tests" --network "$name" -v "$PWD:/work:ro" -v "$fault_dir:/fault-control" \
    -e "BUFFER_CONTROL_TOKEN=$control" -e "REDIS_PASSWORD=$password" -e "MYSQL_PASSWORD=$password" \
    -e "BUFFER_EXTENDED_TESTS=${BUFFER_EXTENDED_TESTS:-0}" \
    cppaiserver-tests python /work/tests/buffering/test_http.py >/dev/null
while [ "$(docker inspect --format '{{.State.Running}}' "$name-tests")" = true ]; do
    for action in queue-stop queue-start kill-a memory-on-1 memory-off-1 result-block inspect-result result-unblock; do
        if [ -f "$fault_dir/$action" ] && [ ! -f "$fault_dir/$action.done" ]; then
            case "$action" in
                queue-stop) docker stop "$name-queue" >/dev/null;;
                queue-start) docker start "$name-queue" >/dev/null;;
                kill-a) docker kill -s SIGKILL "$name-a" >/dev/null;;
                memory-on-1) docker exec "$name-queue" redis-cli CONFIG SET maxmemory 1 >/dev/null;;
                memory-off-1) docker exec "$name-queue" redis-cli CONFIG SET maxmemory 134217728 >/dev/null;;
                result-block) docker exec "$name-queue" redis-cli ACL SETUSER default -eval >/dev/null;;
                result-unblock) docker exec "$name-queue" redis-cli ACL SETUSER default +eval >/dev/null;;
                inspect-result)
                    task_id=$(cat "$fault_dir/result-task-id")
                    case "$task_id" in *[!a-f0-9]*|"") exit 1;; esac
                    [ "${#task_id}" -eq 64 ] || exit 1
                    docker exec "$name-queue" redis-cli --raw GET "business-buffer:v1:task:$task_id" |
                        /usr/bin/python3 -c 'import json,sys; print(json.dumps({"state":json.load(sys.stdin)["state"]}))' > "$fault_dir/inspect-result.json"
                    ;;
            esac
            touch "$fault_dir/$action.done"
        fi
    done
    sleep .1
done
docker logs "$name-tests"
result=$(docker inspect --format '{{.State.ExitCode}}' "$name-tests")
exit "$result"
