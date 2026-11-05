"""同步夹具在真实接入层的 TLS 协商、校验及取消回归。"""
import json
import socket
import ssl
import threading
from urllib.parse import urlsplit

import pytest
from websockets.sync.client import connect

from conftest import CA_BUNDLE, ORIGIN, WS_URL
from sync_tls import sync_tls_context


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
def test_sync_tls_versions_exchange_messages(make_user, make_room, version):
    user = make_user()
    room_id = make_room(user)
    for _ in range(20):
        assert user.get("/api/auth/me").status_code == 200
        context = sync_tls_context(CA_BUNDLE)
        context.minimum_version = context.maximum_version = version
        with connect(WS_URL, ssl=context, origin=ORIGIN,
                additional_headers={"Cookie": user.cookie_header()},
                open_timeout=5, close_timeout=5) as ws:
            assert ws.socket.version() == version.name.replace("_", ".")
            ws.send(json.dumps({"type": "subscribe", "room_id": room_id}))
            assert json.loads(ws.recv(timeout=5))["type"] == "subscribed"


def test_default_socket_negotiates_tls13(make_user, make_socket):
    ws = make_socket(make_user()).ws
    assert ws.socket.version() == "TLSv1.3"


def test_sync_tls_rejects_untrusted_ca():
    context = sync_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    with pytest.raises(ssl.SSLCertVerificationError):
        connect(WS_URL, ssl=context, origin=ORIGIN, open_timeout=5)


def test_sync_tls_rejects_wrong_hostname():
    endpoint = urlsplit(WS_URL)
    with socket.create_connection((endpoint.hostname, endpoint.port or 443), timeout=5) as tcp:
        with pytest.raises(ssl.SSLCertVerificationError):
            sync_tls_context(CA_BUNDLE).wrap_socket(tcp, server_hostname="invalid.example")


@pytest.mark.parametrize("operation", ["close", "shutdown"])
def test_close_before_first_write_releases_reader(operation):
    endpoint = urlsplit(WS_URL)
    tcp = socket.create_connection((endpoint.hostname, endpoint.port or 443), timeout=5)
    secure = sync_tls_context(CA_BUNDLE).wrap_socket(tcp, server_hostname=endpoint.hostname)
    ready, done = threading.Event(), threading.Event()
    results = []

    def read():
        ready.set()
        try:
            results.append(secure.recv(1))
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        assert ready.wait(5)
        if operation == "shutdown":
            secure.shutdown(socket.SHUT_RDWR)
        else:
            secure.close()
        assert done.wait(5), "首次写入前关闭必须释放等待读取的线程"
    finally:
        secure.close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert results in ([], [b""])
