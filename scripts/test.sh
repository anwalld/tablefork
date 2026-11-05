#!/usr/bin/env bash
# Runs the unit tests in a container, then the integration tests against the
# Compose stack. Arguments are passed to pytest.
set -euo pipefail
cd "$(dirname "$0")/.."

# Docker Desktop 的 VM 暂停会触发 Sentinel TILT；仅在测试进程存活时防止空闲休眠。
if [[ "$(uname -s)" == Darwin ]] && command -v caffeinate >/dev/null; then
    caffeinate -i -w "$$" >/dev/null 2>&1 &
fi

if [[ ! -f .env ]]; then
    echo "Missing .env. Copy .env.example to .env and set the passwords." >&2
    exit 1
fi

# 任一阶段失败也恢复正常配置，保留原始退出码。
restore_stack() {
    result=$?
    trap - EXIT
    if ! docker compose up -d --wait; then
        echo "恢复正常配置失败" >&2
        [[ "$result" != 0 ]] || result=1
    fi
    exit "$result"
}
trap restore_stack EXIT

docker build --target unit -t "${UNIT_TEST_IMAGE:-cppaiserver-unit}" .
docker build -f ai/Dockerfile --target test -t "${AI_UNIT_TEST_IMAGE:-cppaiserver-ai-test}" .
docker compose --profile test build tests
docker compose build sentinel-1
python3 scripts/test_sentinel.py --image "${SENTINEL_TEST_IMAGE:-cppaiserver-sentinel:7.4.5-self-guard}"

# The suite registers, logs in and sends far more often than the limits allow,
# so it runs with the Nginx and message limits lifted, a short login count
# TTL so that its expiry can be seen, and the AI service on fake providers
# with short timers. Then the AI service is stopped and started again around
# a message, the Nginx limit is tested with the configured values and the
# message limit with small ones, and last the stack is left running as it
# normally does.
# Compose recreates dependencies whose settings differ, so both commands of a
# round need the same environment.
wait_gateway() {
    docker compose --profile test run --rm --no-deps tests python wait_gateway.py
}

ai_test_env() {
    export AI_FAKE_PROVIDERS=1 AI_PROACTIVE_P1=1 AI_IDLE_SECONDS=3
    export AI_TIMER_POLL_SECONDS=0.5 AI_GENERATION_TIMEOUT=3
    export AI_RETRY_BASE_SECONDS=0.1 AI_WORK_LEASE_SECONDS=2 AI_GENERATION_MAX_ATTEMPTS=3
}

(
    export AUTH_RATE=100000r/m AUTH_BURST=100000 LOGIN_FAIL_TTL_SECONDS=5
    export MESSAGE_RATE_LIMIT=100000 AI_CHAIN_P=0 SESSION_CHECK_SECONDS=1
    ai_test_env
    docker compose up -d --build --wait
    wait_gateway
    docker compose --profile test run --rm -e SHORT_LOGIN_TTL=5 -e AI_TESTS=1 \
        -e SHORT_SESSION_CHECK=1 tests pytest -q "$@"

    run_phase() {
        docker compose --profile test run --rm -e AI_PHASE="$1" tests pytest -q test_ai_faults.py
    }
    run_phase prepare
    docker compose stop ai
    run_phase down
    docker compose start ai
    docker compose up -d --wait ai
    run_phase up
    run_phase generating
    docker compose kill -s SIGKILL ai
    docker compose up -d --wait ai
    run_phase recovered

    run_failover_phase() {
        docker compose --profile test run --rm --no-deps \
            -e FAILOVER_PHASE="$1" -e FAILOVER_STOPPED_AT -e FAULT_TEST_ID tests \
            pytest -q -s test_failover.py
    }
    export FAULT_TEST_ID="$$-$RANDOM"
    run_failover_phase controlled
    run_failover_phase prepare
    run_failover_phase window &
    redis_window_pid=$!
    docker compose --profile test run --rm --no-deps -T -e FAULT_TEST_ID tests python - <<'WINDOW_READY'
import os, time
import redis
cache = redis.Redis(host=os.environ.get("REDIS_HOST", "redis-proxy"),
    password=os.environ["REDIS_PASSWORD"], socket_connect_timeout=2, socket_timeout=2)
prefix = f"test:redis-window:{os.environ['FAULT_TEST_ID']}"
deadline = time.monotonic() + 30
while not cache.exists(prefix + ":ready"):
    assert time.monotonic() < deadline, "business observer did not become ready"
    time.sleep(0.1)
cache.set(prefix + ":begin", "1", ex=120)
while not cache.exists(prefix + ":started"):
    assert time.monotonic() < deadline, "business observer did not start"
    time.sleep(0.1)
cache.close()
print("business window: observer active before stopping redis-1")
WINDOW_READY
    export FAILOVER_STOPPED_AT="$(date +%s)"
    docker compose stop redis-1
    run_failover_phase after
    wait "$redis_window_pid"
    # Start observing before Redis starts, so its temporary master role cannot
    # be missed while a test container is still being created.
    run_failover_phase rejoined &
    redis_rejoin_pid=$!
    docker compose --profile test run --rm --no-deps -T -e FAULT_TEST_ID tests python - <<'REJOIN_READY'
import os, time
import redis
cache = redis.Redis(host=os.environ.get("REDIS_HOST", "redis-proxy"),
    password=os.environ["REDIS_PASSWORD"], socket_connect_timeout=2, socket_timeout=2)
key = f"test:redis-rejoin:{os.environ['FAULT_TEST_ID']}:ready"
deadline = time.monotonic() + 30
while not cache.exists(key):
    assert time.monotonic() < deadline, "旧主重加入采样器未就绪"
    time.sleep(0.1)
cache.close()
REJOIN_READY
    docker compose start redis-1
    wait "$redis_rejoin_pid"
    docker compose up -d --wait

    export FAULT_TEST_ID="$$-$RANDOM"
    run_failover_phase app-down &
    app_fault_pid=$!
    docker compose --profile test run --rm --no-deps -T -e FAULT_TEST_ID tests python - <<'READY'
import os, time
import redis
cache = redis.Redis(host=os.environ.get("REDIS_HOST", "redis-proxy"),
    password=os.environ["REDIS_PASSWORD"], socket_connect_timeout=2, socket_timeout=2)
key = f"test:app-fault:{os.environ['FAULT_TEST_ID']}:ready"
deadline = time.monotonic() + 40
while not cache.exists(key):
    assert time.monotonic() < deadline, "应用故障测试未就绪"
    time.sleep(0.1)
cache.close()
READY
    docker compose stop app-1
    wait "$app_fault_pid"
    docker compose start app-1
    docker compose up -d --wait app-1
    docker compose stop web-1
    run_failover_phase web-down
    docker compose start web-1
    docker compose up -d --wait

    # API keys must never reach the logs in the clear.
    if docker compose logs app-1 app-2 ai web-1 web-2 gateway | grep -F "fake-plaintext-marker-7f3a"; then
        echo "an API key appeared in the logs" >&2
        exit 1
    fi
)

(
    export MESSAGE_RATE_LIMIT=5 MESSAGE_RATE_WINDOW_SECONDS=2 AI_CHAIN_P=1
    export GATEWAY_CONN_PER_IP=5
    ai_test_env
    docker compose up -d --wait
    wait_gateway
    # 两个容器有不同源地址；主容器占满 5 个连接时，另一个仍能访问。
    export LIMIT_TEST_ID="$$-$RANDOM"
    docker compose --profile test run --rm --no-deps \
        -e GATEWAY_LIMIT_TEST=1 -e GATEWAY_LIMIT_PEER=1 -e LIMIT_TEST_ID \
        tests pytest -q test_gateway_limits.py::test_other_source_address_is_unaffected &
    peer_pid=$!
    docker compose --profile test run --rm --no-deps \
        -e GATEWAY_LIMIT_TEST=1 -e LIMIT_TEST_ID tests pytest -q \
        test_gateway_limits.py::test_limit_releases_slots_and_preserves_existing_connections
    wait "$peer_pid"
    reconcile_kick_container=""
    reconcile_ban_container=""
    # 独立来源分别验证 kick/ban；依次执行，避免两条 ACL 故障注入互相干扰。
    # 两个容器持续存活，保证 IP 彼此不同且不会被后续认证测试复用。
    trap 'for container in "$reconcile_kick_container" "$reconcile_ban_container"; do
        if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null || true; fi
    done' EXIT
    reconcile_kick_container=$(docker compose --profile test run -d --no-deps -e AI_TESTS=1 tests sleep infinity)
    reconcile_ban_container=$(docker compose --profile test run -d --no-deps -e AI_TESTS=1 tests sleep infinity)
    echo "30-second reconciliation tests: isolated source addresses"
    docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
        "$reconcile_kick_container" "$reconcile_ban_container"
    docker compose exec -T app-1 printenv SESSION_CHECK_SECONDS
    docker exec "$reconcile_kick_container" pytest -q \
        'test_room_bans.py::test_lost_removal_notification_cannot_leak_new_messages[kick]'
    docker exec "$reconcile_ban_container" pytest -q \
        'test_room_bans.py::test_lost_removal_notification_cannot_leak_new_messages[ban]'

    # 认证限流测试放在最后，会耗尽当前来源的配额。
    export RATE_TEST_ID="$$-$RANDOM"
    docker compose --profile test run --rm --no-deps \
        -e GATEWAY_RATE_PEER=1 -e RATE_TEST_ID tests pytest -q \
        test_gateway.py::test_another_ip_keeps_its_auth_budget &
    rate_peer_pid=$!
    docker compose --profile test run --rm \
        -e GATEWAY_RATE_TEST=1 -e RATE_TEST_ID \
        -e STRICT_MESSAGE_RATE=1 -e MESSAGE_RATE_LIMIT -e MESSAGE_RATE_WINDOW_SECONDS \
        -e AI_TESTS=1 -e AI_CHAIN_ONE=1 -e STRICT_AUTH_RATE=1 tests \
        pytest -q test_message_rate.py \
            test_ai_replies.py::test_ais_pick_up_each_other \
            test_ai_replies.py::test_ai_replies_do_not_use_up_the_users_limit \
            test_sessions.py::test_refresh_is_not_rate_limited_by_nginx \
            test_gateway.py::test_real_ip_rate_limit_across_new_connections
    wait "$rate_peer_pid"
)
