#!/bin/sh
# Usage: sh tests/redis_disconnect/run_late_send.sh image evidence-directory [late|reconnect|invalid-auth|invalid-db]
set -eu
image=$1
evidence=$2
mode=${3:-late}
case "$mode" in late|reconnect|invalid-auth|invalid-db) ;; *) exit 64;; esac
mkdir -p "$evidence"
name="cppai-redis-late-$$"
cleanup() {
    docker rm -f "$name-client" "$name-redis" >/dev/null 2>&1 || true
    docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker network create "$name" >/dev/null
docker run -d --name "$name-redis" --network "$name" redis:7.4.5 redis-server --save '' --appendonly no >/dev/null
redis_ready=0
for attempt in $(seq 1 100); do
    if docker exec "$name-redis" redis-cli PING 2>/dev/null | grep -q '^PONG$'; then redis_ready=1; break; fi
    sleep .1
done
[ "$redis_ready" -eq 1 ] || { echo 'Disposable Redis did not become ready' >&2; exit 1; }
ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$name-redis")
if [ "$mode" = late ]; then
    docker run -d --name "$name-client" --network "$name" -e REDIS_IP="$ip" "$image" >/dev/null
else
    docker run -d --name "$name-client" --network "$name" -e REDIS_IP="$ip" \
        --entrypoint /probe/build/redis-lifecycle "$image" "$mode" >/dev/null
fi
ready=0
for attempt in $(seq 1 100); do
    if docker logs "$name-client" 2>&1 | grep -q READY; then ready=1; break; fi
    sleep .1
done
[ "$ready" -eq 1 ] || { docker logs "$name-client" > "$evidence/client.log" 2>&1; exit 1; }
if [ "$mode" = late ]; then
    docker exec "$name-redis" redis-cli CLIENT KILL TYPE normal >/dev/null
elif [ "$mode" = reconnect ]; then
    # Faults are restricted to this invocation's randomly named local Redis.
    for attempt in $(seq 1 40); do
        docker exec "$name-redis" redis-cli CLIENT KILL TYPE normal >/dev/null
        docker exec "$name-redis" redis-cli CLIENT KILL TYPE pubsub >/dev/null
        sleep .02
    done
fi
docker wait "$name-client" > "$evidence/client.exit"
docker logs "$name-client" > "$evidence/client.log" 2>&1
cat "$evidence/client.exit"
cat "$evidence/client.log"
# The result is the client status. A baseline failure is compared explicitly
# by the caller and must never be relabelled as a successful patched run.
exit "$(cat "$evidence/client.exit")"
