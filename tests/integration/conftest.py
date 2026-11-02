import json
import os
import uuid

import pymysql
import pytest
import redis as redis_client
import requests
from websockets.sync.client import connect

BASE_URL = os.environ.get("BASE_URL", "http://nginx")
WS_URL = os.environ.get("WS_URL", "ws://nginx/ws")
ORIGIN = os.environ.get("ORIGIN", "http://localhost:8080")
PASSWORD = "correct-horse-battery"


class Client:
    """An HTTP client with its own cookie jar."""

    def __init__(self):
        self.session = requests.Session()
        self.username = None

    def request(self, method, path, **kwargs):
        return self.session.request(method, BASE_URL + path, timeout=30, **kwargs)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def cookie_header(self):
        return "; ".join(f"{c.name}={c.value}" for c in self.session.cookies)


def unique_name(prefix="u"):
    return f"{prefix}{uuid.uuid4().hex[:12]}"


CONFIRM_DELETE = {"confirm_delete": True}


def error_code(response):
    return response.json()["error"]["code"]


class Socket:
    """A WebSocket client that hands out replies by type."""

    def __init__(self, client, origin=ORIGIN):
        headers = {"Cookie": client.cookie_header()}
        self.ws = connect(WS_URL, origin=origin, additional_headers=headers)
        self.buffer = []

    def send(self, **message):
        self.ws.send(json.dumps(message))

    def send_message(self, room_id, content="hello", client_msg_id=None):
        client_msg_id = client_msg_id or uuid.uuid4().hex
        self.send(
            type="send",
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
        return self.expect("ack", "error")

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
        host=os.environ.get("REDIS_HOST", "redis-master"),
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
        assert client.post(
            "/api/auth/login",
            json={"username": client.username, "password": PASSWORD},
        ).status_code == 200
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
