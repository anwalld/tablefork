"""Per-user message rate limit.

Needs the app started with MESSAGE_RATE_LIMIT and MESSAGE_RATE_WINDOW_SECONDS
set to the same values as this process, small enough to hit quickly.
scripts/test.sh runs it with 5 messages per 2 seconds.
"""

import os
import time

import pytest

from conftest import PASSWORD, Client, Socket, unique_name

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRICT_MESSAGE_RATE"),
    reason="needs a small MESSAGE_RATE_LIMIT on the app",
)

LIMIT = int(os.environ.get("MESSAGE_RATE_LIMIT", "30"))
WINDOW = int(os.environ.get("MESSAGE_RATE_WINDOW_SECONDS", "10"))


def new_user():
    # Registration is limited by Nginx in this run, so the module shares users.
    client = Client()
    client.username = unique_name()
    body = {"username": client.username, "password": PASSWORD}
    assert client.post("/api/auth/register", json=body).status_code == 201
    assert client.post("/api/auth/login", json=body).status_code == 200
    return client


@pytest.fixture(scope="module")
def users():
    alice, bob = new_user(), new_user()
    room_id = alice.post("/api/rooms", json={"name": "rate"}).json()["id"]
    assert alice.post(
        f"/api/rooms/{room_id}/invites", json={"username": bob.username}
    ).status_code == 200
    assert bob.post(f"/api/rooms/{room_id}/join").status_code == 200
    return alice, bob, room_id


@pytest.fixture
def sockets():
    opened = []

    def make(client):
        socket = Socket(client)
        opened.append(socket)
        return socket

    yield make
    for socket in opened:
        socket.close()


def start_of_window():
    """Sleeps into a fresh window, so every test starts from a zero count."""
    time.sleep(WINDOW - time.time() % WINDOW + 0.05)


def send_burst(socket, room_id, count):
    ids = [socket.send_message(room_id, f"m{index}") for index in range(count)]
    replies = {}
    for _ in ids:
        reply = socket.expect("ack", "error")
        replies[reply["client_msg_id"]] = reply
    return ids, replies


def saved(db, client_msg_id):
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM messages WHERE client_msg_id=%s", (client_msg_id,))
        return cursor.fetchone()[0] == 1


def test_messages_within_the_limit_are_saved(users, sockets):
    alice, _, room_id = users
    start_of_window()
    _, replies = send_burst(sockets(alice), room_id, LIMIT)
    assert all(reply["type"] == "ack" for reply in replies.values())


def test_messages_over_the_limit_are_refused_and_not_saved(users, sockets, db):
    alice, _, room_id = users
    start_of_window()
    ids, replies = send_burst(sockets(alice), room_id, LIMIT + 1)
    refused = [replies[i] for i in ids if replies[i]["type"] == "error"]
    assert len(refused) == 1
    error = refused[0]
    assert error["error"]["code"] == "rate_limited"
    assert error["error"]["retryable"]
    assert 1 <= error["error"]["retry_after"] <= WINDOW
    assert error["room_id"] == room_id
    assert not saved(db, error["client_msg_id"])


def test_connections_and_rooms_share_one_limit(users, sockets):
    alice, _, room_id = users
    other_room = alice.post("/api/rooms", json={"name": "rate-2"}).json()["id"]
    first, second = sockets(alice), sockets(alice)
    start_of_window()
    half = LIMIT // 2
    _, a = send_burst(first, room_id, half)
    _, b = send_burst(second, other_room, LIMIT - half)
    assert all(reply["type"] == "ack" for reply in [*a.values(), *b.values()])
    assert second.post(room_id)["error"]["code"] == "rate_limited"


def test_a_limited_user_does_not_slow_others(users, sockets):
    alice, bob, room_id = users
    start_of_window()
    send_burst(sockets(alice), room_id, LIMIT + 1)
    assert sockets(bob).post(room_id)["type"] == "ack"


def test_resend_after_the_window_is_saved(users, sockets, db):
    alice, _, room_id = users
    socket = sockets(alice)
    start_of_window()
    ids, replies = send_burst(socket, room_id, LIMIT + 1)
    refused = next(replies[i] for i in ids if replies[i]["type"] == "error")
    time.sleep(refused["error"]["retry_after"] + 0.05)
    reply = socket.post(room_id, "again", client_msg_id=refused["client_msg_id"])
    assert reply["type"] == "ack"
    assert not reply["duplicate"]
    assert saved(db, refused["client_msg_id"])
