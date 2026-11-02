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

# The suite registers and logs in far more often than the login limit allows,
# so it runs with the limit lifted. The limit itself is tested afterwards with
# the configured values, which also leaves the stack as it normally runs.
# Compose recreates dependencies whose settings differ, so both commands need
# the same environment.
(
    export AUTH_RATE=100000r/m AUTH_BURST=100000
    docker compose up -d --build --wait
    docker compose --profile test run --rm tests pytest -q "$@"
)

docker compose up -d --wait nginx
docker compose --profile test run --rm -e STRICT_AUTH_RATE=1 tests \
    pytest -q test_auth.py -k rate_limited
