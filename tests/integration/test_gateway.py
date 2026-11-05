"""HTTPS 接入、明文拒绝和真实客户端 IP 限流。"""
import asyncio
import json
import hashlib
import os
import socket
import time
import ssl
from urllib.parse import urlsplit

import pytest
import requests
from cryptography import x509
from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus, InvalidURI

from conftest import BASE_URL, CA_BUNDLE, ORIGIN, PASSWORD, WS_URL, Client, error_code, tls_context, unique_name

HOST = urlsplit(BASE_URL).hostname
HTTP_URL = f"http://{HOST}"


@pytest.mark.parametrize("path", ["/api/auth/me", "/x?y=1&z=two"])
def test_http_redirect_preserves_path_and_query(path):
    response = requests.get(HTTP_URL + path, allow_redirects=False, timeout=5)
    assert response.status_code == 301
    assert response.headers["Location"] == f"https://{HOST}:{urlsplit(ORIGIN).port or 443}{path}"


def test_http_login_does_not_reach_application(make_user, redis):
    user = make_user()
    key = "login_fail:u:" + hashlib.sha256(user.username.encode()).hexdigest().upper()
    before = redis.get(key)
    response = requests.post(HTTP_URL + "/api/auth/login", json={"username": user.username, "password": "wrong"}, allow_redirects=False, timeout=5)
    assert response.status_code == 301
    assert redis.get(key) == before


def test_plain_websocket_is_not_established():
    with pytest.raises((InvalidStatus, InvalidURI)):
        with connect(f"ws://{HOST}/ws", origin=ORIGIN, open_timeout=5):
            pytest.fail("明文 WebSocket 不应建立")


@pytest.mark.filterwarnings("ignore:ssl.TLSVersion.TLSv1_1 is deprecated:DeprecationWarning")
def test_tls_11_is_rejected():
    context = tls_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_1
    context.maximum_version = ssl.TLSVersion.TLSv1_1
    context.set_ciphers("ALL:@SECLEVEL=0")
    with socket.create_connection((HOST, 443), timeout=5) as tcp:
        with pytest.raises(ssl.SSLError, match="(?i)protocol.version|unsupported.protocol"):
            context.wrap_socket(tcp, server_hostname=HOST)


def test_local_ca_and_san():
    # 握手本身验证签发链、有效期和 gateway 主机名，不能关闭校验。
    with socket.create_connection((HOST, 443), timeout=5) as tcp:
        with tls_context().wrap_socket(tcp, server_hostname=HOST) as secure:
            cert = x509.load_der_x509_certificate(secure.getpeercert(binary_form=True))
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {"localhost", "gateway"} <= set(names.get_values_for_type(x509.DNSName))
    assert {"127.0.0.1", "::1"} <= {str(v) for v in names.get_values_for_type(x509.IPAddress)}
    with open(CA_BUNDLE, "rb") as source:
        ca = x509.load_pem_x509_certificate(source.read())
    assert cert.issuer == ca.subject


def test_login_cookies_are_secure_and_http_only(make_user):
    user = make_user()
    assert {c.name for c in user.session.cookies} >= {"chat_session", "chat_device"}
    assert all(c.secure and "HttpOnly" in c._rest for c in user.session.cookies)
    assert user.get("/api/auth/me").status_code == 200


@pytest.mark.skipif(not os.environ.get("STRICT_AUTH_RATE"), reason="需要默认认证限流")
def test_real_ip_rate_limit_across_new_connections(redis):
    statuses = []
    for index in range(30):
        # 每个 Session 都新建 TCP/TLS 连接；伪造转发头不能得到新的配额。
        with requests.Session() as session:
            response = session.post(BASE_URL + "/api/auth/login", verify=CA_BUNDLE,
                json={"username": unique_name(), "password": PASSWORD},
                headers={"X-Forwarded-For": f"192.0.2.{index + 1}", "X-Real-IP": f"198.51.100.{index + 1}"}, timeout=5)
            statuses.append(response.status_code)
    assert statuses[0] == 401
    assert 429 in statuses
    assert response.status_code == 429
    assert error_code(response) == "rate_limited"
    assert response.json()["error"]["retryable"] is True
    assert int(response.headers["Retry-After"]) >= 1
    with Client().session as session:
        assert session.get(BASE_URL + "/health", timeout=5).status_code == 200
        assert session.get(BASE_URL + "/api/auth/me", timeout=5).status_code == 401
    if os.environ.get("GATEWAY_RATE_TEST"):
        prefix = f"test:gateway-rate:{os.environ['RATE_TEST_ID']}"
        try:
            redis.set(prefix + ":limited", "1", ex=90)
            deadline = time.monotonic() + 30
            while not redis.exists(prefix + ":peer-ok"):
                assert time.monotonic() < deadline, "另一个来源的认证配额验证超时"
                time.sleep(0.1)
        finally:
            redis.delete(prefix + ":limited", prefix + ":peer-ok")


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
def test_wss_protocols_exchange_messages(make_user, make_room, version):
    from websockets.asyncio.client import connect as async_connect
    user = make_user()
    room_id = make_room(user)

    async def exchange():
        context = tls_context()
        context.minimum_version = context.maximum_version = version
        for _ in range(8):
            async with async_connect(WS_URL, ssl=context, origin=ORIGIN,
                    additional_headers={"Cookie": user.cookie_header()}, open_timeout=5) as ws:
                assert ws.transport.get_extra_info("ssl_object").version() == version.name.replace("_", ".")
                await ws.send(json.dumps({"type": "subscribe", "room_id": room_id}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert reply["type"] == "subscribed"
    asyncio.run(exchange())


def test_repeated_http_then_wss(make_user, make_socket):
    user = make_user()
    for _ in range(20):
        assert user.get("/api/auth/me").status_code == 200
        make_socket(user).close()


@pytest.mark.skipif(not os.environ.get("GATEWAY_RATE_PEER"), reason="第二个来源的严格认证限流测试")
def test_another_ip_keeps_its_auth_budget(redis):
    prefix = f"test:gateway-rate:{os.environ['RATE_TEST_ID']}"
    deadline = time.monotonic() + 60
    while not redis.exists(prefix + ":limited"):
        assert time.monotonic() < deadline, "主来源未完成认证限流验证"
        time.sleep(0.1)
    with Client().session as session:
        response = session.post(BASE_URL + "/api/auth/login", timeout=5,
            json={"username": unique_name(), "password": PASSWORD})
    assert response.status_code == 401
    redis.set(prefix + ":peer-ok", "1", ex=90)


def test_repeated_service_unavailable_preserves_json_error(make_user, redis):
    user = make_user()
    assert user.get("/api/auth/me").status_code == 200
    # Both app processes stay alive; only the shared session lookup is denied.
    # An application 503 must not exhaust the gateway's healthy upstream set.
    redis.execute_command("ACL", "SETUSER", "default", "-get")
    try:
        statuses = []
        for _ in range(3):
            response = user.get("/api/auth/me")
            statuses.append(response.status_code)
            assert response.status_code == 503, statuses
            assert response.json()["error"]["retryable"] is True
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+get")
