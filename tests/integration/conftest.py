import ssl
import json
import os
import uuid
import time

import pymysql
import pytest
import redis as redis_client
import requests
from websockets.sync.client import connect

from sync_tls import sync_tls_context

BASE_URL = os.environ.get("BASE_URL", "https://gateway")
WS_URL = os.environ.get("WS_URL", "wss://gateway/ws")
ORIGIN = os.environ.get("ORIGIN", "https://localhost:8443")
CA_BUNDLE = os.environ.get("CA_BUNDLE", "/certs/ca.crt")


def tls_context():
    return ssl.create_default_context(cafile=CA_BUNDLE)


PASSWORD = "correct-horse-battery"


class Client:
    """An HTTP client with its own cookie jar."""

    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.verify = CA_BUNDLE
        self.username = None
        self.queue_owner = uuid.uuid4().hex + uuid.uuid4().hex
        self.session.headers["X-Queue-Owner"] = self.queue_owner

    def request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers.setdefault("X-Request-Id", uuid.uuid4().hex)
        response = self.session.request(method, self.base_url + path, timeout=30, headers=headers, **kwargs)
        if response.status_code == 202:
            return self.task_result(response.json()["task_id"])
        return response

    def task_result(self, task_id, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                query = self.session.get(self.base_url + "/api/tasks/" + task_id, timeout=min(5, max(.01, deadline-time.monotonic())))
                if query.status_code != 200:
                    time.sleep(.1)
                    continue
                task = query.json()
            except requests.RequestException:
                time.sleep(.1)
                continue
            if task["state"] in ("waiting", "running", "recovering"):
                time.sleep(.1)
                continue
            response = requests.Response()
            response.url = self.base_url + "/api/tasks/" + task_id
            if task["state"] in ("unknown", "expired"):
                response.status_code = 503 if task["state"] == "unknown" else 408
                response._content = json.dumps({"error": {"code": "result_unknown" if task["state"] == "unknown" else "queue_timeout", "retryable": False}}).encode()
            else:
                response.status_code = task["response"]["status"]
                response._content = task["response"]["body"].encode()
                response.headers["Content-Type"] = task["response"].get("content_type") or "application/json"
                if task["response"].get("retry_after"):
                    response.headers["Retry-After"] = task["response"]["retry_after"]
            return response
        raise AssertionError("queued business result exceeded its bounded deadline")

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def cookie_header(self):
        return "; ".join(f"{c.name}={c.value}" for c in self.session.cookies)

    def at_instance(self, instance):
        """仅内部多实例测试：显式 Cookie 头允许直连 HTTP 携带 Secure 会话。"""
        if instance not in (1, 2):
            raise ValueError("instance must be 1 or 2")
        client = Client(f"http://app-{instance}:8000")
        client.username = self.username
        client.session.headers["Cookie"] = self.cookie_header()
        return client


def unique_name(prefix="u"):
    return f"{prefix}{uuid.uuid4().hex[:12]}"


CONFIRM_DELETE = {"confirm_delete": True}


def error_code(response):
    return response.json()["error"]["code"]


class Socket:
    """A WebSocket client that hands out replies by type."""

    def __init__(self, client, origin=ORIGIN, instance=None):
        headers = {"Cookie": client.cookie_header()}
        context = sync_tls_context(CA_BUNDLE)
        url = WS_URL if instance is None else f"ws://app-{instance}:8000/ws"
        if instance not in (None, 1, 2):
            raise ValueError("instance must be 1 or 2")
        tls_options = {"ssl": context} if url.startswith("wss://") else {}
        self.ws = connect(url, origin=origin, additional_headers=headers, **tls_options)
        self.buffer = []
        self.client = client

    def send(self, **message):
        self.ws.send(json.dumps(message))

    def send_message(self, room_id, content="hello", client_msg_id=None):
        client_msg_id = client_msg_id or uuid.uuid4().hex
        self.send(
            type="send",
            queue_owner=self.client.queue_owner,
            queue_attempt=uuid.uuid4().hex,
            room_id=room_id,
            client_msg_id=client_msg_id,
            content=content,
        )
        return client_msg_id

    def expect(self, *types, timeout=5):
        for index, message in enumerate(self.buffer):
            if message["type"] in types:
                return self.buffer.pop(index)
        while True:
            message = json.loads(self.ws.recv(timeout=timeout))
            if message["type"] in types:
                return message
            self.buffer.append(message)

    def subscribe(self, room_id):
        self.send(type="subscribe", room_id=room_id)
        return self.expect("subscribed", "error")

    def post(self, room_id, content="hello", client_msg_id=None):
        client_msg_id = self.send_message(room_id, content, client_msg_id)
        frame = self.expect("ack", "error", "queued")
        if frame["type"] == "queued":
            response = self.client.task_result(frame["task_id"])
            if response.status_code < 400:
                return response.json()
            return {"type": "error", "client_msg_id": client_msg_id, "room_id": room_id, "error": response.json()["error"]}
        return frame

    def assert_silent(self, seconds=0.5):
        with pytest.raises(TimeoutError):
            self.expect("message", timeout=seconds)

    def close(self):
        self.ws.close()


@pytest.fixture
def make_socket():
    sockets = []

    def make(client, **kwargs):
        socket = Socket(client, **kwargs)
        sockets.append(socket)
        return socket

    yield make
    for socket in sockets:
        socket.close()


@pytest.fixture
def db():
    connection = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "mysql"),
        user="chat",
        password=os.environ["MYSQL_PASSWORD"],
        database="chat",
        autocommit=True,
    )
    yield connection
    connection.close()


@pytest.fixture
def redis():
    client = redis_client.Redis(
        host=os.environ.get("REDIS_HOST", "redis-proxy"),
        password=os.environ["REDIS_PASSWORD"],
        decode_responses=True,
    )
    yield client
    client.close()


@pytest.fixture
def anon():
    return Client()


@pytest.fixture
def make_user():
    def make():
        client = Client()
        client.username = unique_name()
        assert client.post(
            "/api/auth/register",
            json={"username": client.username, "password": PASSWORD},
        ).status_code == 201
        login = client.post(
            "/api/auth/login",
            json={"username": client.username, "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        return client

    return make


@pytest.fixture
def make_room():
    def make(owner, *guests):
        response = owner.post("/api/rooms", json={"name": "room"})
        assert response.status_code == 201
        room_id = response.json()["id"]
        for guest in guests:
            assert owner.post(
                f"/api/rooms/{room_id}/invites", json={"username": guest.username}
            ).status_code == 200
            assert guest.post(f"/api/rooms/{room_id}/join").status_code == 200
        return room_id

    return make
