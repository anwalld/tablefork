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
docker compose up -d --build --wait
docker compose --profile test build tests
docker compose --profile test run --rm tests pytest -q "$@"
