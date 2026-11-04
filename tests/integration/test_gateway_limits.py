"""两个独立测试容器通过 Redis 同步，验证连接上限按来源隔离。"""
import os
import socket
import ssl
import time
from urllib.parse import urlsplit

import pytest
from conftest import BASE_URL, Client, tls_context

pytestmark = pytest.mark.skipif(not os.environ.get("GATEWAY_LIMIT_TEST"), reason="需要 5 连接上限及第二个测试容器")
HOST = urlsplit(BASE_URL).hostname


def key(suffix):
    return f"test:gateway-limit:{os.environ['LIMIT_TEST_ID']}:{suffix}"


def wait_for(redis, name):
    deadline = time.monotonic() + 30
    while not redis.exists(key(name)):
        assert time.monotonic() < deadline, f"第二个测试容器同步超时：{name}"
        time.sleep(0.1)


def open_connection():
    tcp = socket.create_connection((HOST, 443), timeout=5)
    try:
        return tls_context().wrap_socket(tcp, server_hostname=HOST)
    except BaseException:
        tcp.close()
        raise


@pytest.mark.skipif(bool(os.environ.get("GATEWAY_LIMIT_PEER")), reason="主测试容器运行")
def test_limit_releases_slots_and_preserves_existing_connections(redis):
    connections = []
    try:
        for _ in range(5):
            connections.append(open_connection())
        with pytest.raises((OSError, ssl.SSLError)):
            with open_connection():
                pytest.fail("第六个连接应该被拒绝")
        redis.set(key("full"), "1", ex=60)
        wait_for(redis, "peer-ok")
        connections.pop().close()
        time.sleep(0.3)
        connections.append(open_connection())
        for connection in connections:
            connection.sendall(b"GET /health HTTP/1.1\r\nHost: gateway\r\nConnection: close\r\n\r\n")
            assert b" 200 " in connection.recv(4096).split(b"\r\n", 1)[0]
    finally:
        for connection in connections:
            connection.close()
        redis.delete(key("full"), key("peer-ok"))


@pytest.mark.skipif(not os.environ.get("GATEWAY_LIMIT_PEER"), reason="第二个测试容器运行")
def test_other_source_address_is_unaffected(redis):
    wait_for(redis, "full")
    client = Client()
    try:
        assert client.get("/health").status_code == 200
        redis.set(key("peer-ok"), "1", ex=60)
    finally:
        client.session.close()
