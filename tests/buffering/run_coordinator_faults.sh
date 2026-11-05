#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-cppai-buffering-static}
export COMPOSE_FILE=docker-compose.yml:docker/buffering-test.override.yml
export BUFFER_FAULT_DIR=/private/tmp/cppai-buffering-control-faults
mkdir -p "$BUFFER_FAULT_DIR"
# Only flags created by this dedicated probe, no business resources.
rm -f "$BUFFER_FAULT_DIR"/blocked "$BUFFER_FAULT_DIR"/*.done "$BUFFER_FAULT_DIR"/*.json "$BUFFER_FAULT_DIR"/manual-switch "$BUFFER_FAULT_DIR"/stop-redis-1 "$BUFFER_FAULT_DIR"/stop-redis-2 "$BUFFER_FAULT_DIR"/start-redis-1 "$BUFFER_FAULT_DIR"/start-redis-2 "$BUFFER_FAULT_DIR"/pause-b "$BUFFER_FAULT_DIR"/unpause-b "$BUFFER_FAULT_DIR"/restart-proxy
docker compose run --rm --no-deps -T tests python wait_gateway.py
name=cppai-buffering-real-control-probe
cleanup() {
    docker compose unpause app-2 >/dev/null 2>&1 || true
    docker compose start redis-1 redis-2 >/dev/null 2>&1 || true
    rm -f "$BUFFER_FAULT_DIR/blocked"
    docker rm -fv "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker compose run -d --no-deps --name "$name" -e BUFFER_CONTROL_REAL=1 tests \
    pytest -q -s test_buffering_coordinator_real.py >/dev/null
while [ "$(docker inspect --format '{{.State.Running}}' "$name")" = true ]; do
    for action in manual-switch stop-redis-1 stop-redis-2 start-redis-1 start-redis-2 pause-b unpause-b restart-proxy; do
        if [ -f "$BUFFER_FAULT_DIR/$action" ] && [ ! -f "$BUFFER_FAULT_DIR/$action.done" ]; then
            case "$action" in
                manual-switch)
                    set +e
                    docker compose exec -T redis-proxy sh /redis-proxy.sh switch > "$BUFFER_FAULT_DIR/manual-output" 2>&1
                    result=$?
                    set -e
                    printf '%s' "$result" > "$BUFFER_FAULT_DIR/manual-exit"
                    /usr/bin/python3 -c 'import json,pathlib; p=pathlib.Path("/private/tmp/cppai-buffering-control-faults"); (p/"manual-switch.json").write_text(json.dumps({"exit":int((p/"manual-exit").read_text()),"output":(p/"manual-output").read_text()}))'
                    ;;
                stop-redis-1) docker compose stop redis-1 >/dev/null;;
                stop-redis-2) docker compose stop redis-2 >/dev/null;;
                start-redis-1) docker compose start redis-1 >/dev/null;;
                start-redis-2) docker compose start redis-2 >/dev/null;;
                pause-b) docker compose pause app-2 >/dev/null;;
                unpause-b) docker compose unpause app-2 >/dev/null;;
                restart-proxy)
                    before=$(docker compose ps -q redis-proxy)
                    ip_before=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$before")
                    docker compose restart redis-proxy >/dev/null
                    after=$(docker compose ps -q redis-proxy)
                    ip_after=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$after")
                    container_same=false; ip_same=false
                    [ "$before" != "$after" ] || container_same=true
                    [ "$ip_before" != "$ip_after" ] || ip_same=true
                    printf '{"same_container":%s,"same_ip":%s}' "$container_same" "$ip_same" > "$BUFFER_FAULT_DIR/restart-proxy.json"
                    ;;
            esac
            touch "$BUFFER_FAULT_DIR/$action.done"
        fi
    done
    sleep .1
done
docker logs "$name"
result=$(docker inspect --format '{{.State.ExitCode}}' "$name")
exit "$result"
