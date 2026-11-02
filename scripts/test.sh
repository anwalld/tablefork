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
docker compose --profile test build tests

# The suite registers, logs in and sends far more often than the limits allow,
# so it runs with the Nginx and message limits lifted, and with a short login
# count TTL so that its expiry can be seen. Then the Nginx limit is tested with
# the configured values and the message limit with small ones, and last the
# stack is left running as it normally does.
# Compose recreates dependencies whose settings differ, so both commands of a
# round need the same environment.
(
    export AUTH_RATE=100000r/m AUTH_BURST=100000 LOGIN_FAIL_TTL_SECONDS=5
    export MESSAGE_RATE_LIMIT=100000
    docker compose up -d --build --wait
    docker compose --profile test run --rm -e SHORT_LOGIN_TTL=5 tests pytest -q "$@"
)

(
    export MESSAGE_RATE_LIMIT=5 MESSAGE_RATE_WINDOW_SECONDS=2
    docker compose up -d --wait
    # The message tests go first: the Nginx test uses up the auth budget.
    docker compose --profile test run --rm \
        -e STRICT_MESSAGE_RATE=1 -e MESSAGE_RATE_LIMIT -e MESSAGE_RATE_WINDOW_SECONDS \
        -e STRICT_AUTH_RATE=1 tests \
        pytest -q test_message_rate.py test_auth.py::test_auth_attempts_are_rate_limited
)

docker compose up -d --wait
