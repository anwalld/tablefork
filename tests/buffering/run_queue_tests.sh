#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
name="cppai-buffering-test-$$"
cleanup() { docker rm -fv "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
# Only a newly-created disposable container, no volumes or project network.
docker run -d --name "$name" -p 127.0.0.1::6379 \
    -v "$PWD/docker/queue-redis.conf:/queue-redis.conf:ro" \
    redis:7.4.5 redis-server /queue-redis.conf >/dev/null
port=$(docker port "$name" 6379/tcp)
QUEUE_TEST_PORT=${port##*:}; export QUEUE_TEST_PORT
for i in 1 2 3 4 5; do
    if docker exec "$name" redis-cli ping >/dev/null 2>&1; then break; fi
    sleep 1
done
python3 tests/buffering/test_queue.py
