#!/usr/bin/env bash
# Runs the unit tests in a container, then the integration tests against the
# Compose stack. Arguments are passed to pytest.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "Missing .env. Copy .env.example to .env and set the passwords." >&2
    exit 1
fi

docker build --target unit -t cppaiserver-unit .
docker build -f ai/Dockerfile --target test -t cppaiserver-ai-test .
docker compose --profile test build tests

# The suite registers, logs in and sends far more often than the limits allow,
# so it runs with the Nginx and message limits lifted, a short login count
# TTL so that its expiry can be seen, and the AI service on fake providers
# with short timers. Then the AI service is stopped and started again around
# a message, the Nginx limit is tested with the configured values and the
# message limit with small ones, and last the stack is left running as it
# normally does.
# Compose recreates dependencies whose settings differ, so both commands of a
# round need the same environment.
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

    # API keys must never reach the logs in the clear.
    if docker compose logs app ai nginx | grep -F "fake-plaintext-marker-7f3a"; then
        echo "an API key appeared in the logs" >&2
        exit 1
    fi
)

(
    export MESSAGE_RATE_LIMIT=5 MESSAGE_RATE_WINDOW_SECONDS=2 AI_CHAIN_P=1
    ai_test_env
    docker compose up -d --wait
    # The Nginx test goes last: it uses up the auth budget.
    docker compose --profile test run --rm \
        -e STRICT_MESSAGE_RATE=1 -e MESSAGE_RATE_LIMIT -e MESSAGE_RATE_WINDOW_SECONDS \
        -e AI_TESTS=1 -e AI_CHAIN_ONE=1 -e STRICT_AUTH_RATE=1 tests \
        pytest -q test_message_rate.py \
            test_ai_replies.py::test_ais_pick_up_each_other \
            test_ai_replies.py::test_ai_replies_do_not_use_up_the_users_limit \
            test_sessions.py::test_refresh_is_not_rate_limited_by_nginx \
            test_auth.py::test_auth_attempts_are_rate_limited
)

docker compose up -d --wait
