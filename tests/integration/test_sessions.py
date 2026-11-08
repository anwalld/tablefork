"""Session lifecycle: closing connections when sessions end, trusted devices,
refreshing. Each Client is one browser, so one device.

scripts/test.sh runs this with SESSION_CHECK_SECONDS=1 (SHORT_SESSION_CHECK).
"""

import os
import time

import pytest
from websockets.exceptions import ConnectionClosed

from conftest import PASSWORD, Client, Socket, error_code, unique_name

PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
COMPUTER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15"
SESSION_ENDED = 4401
TTL = 604800

short_check = pytest.mark.skipif(
    not os.environ.get("SHORT_SESSION_CHECK"), reason="needs SESSION_CHECK_SECONDS=1"
)


def register():
    username = unique_name()
    assert Client().post(
        "/api/auth/register", json={"username": username, "password": PASSWORD}
    ).status_code == 201
    return username


def device(username, agent=COMPUTER):
    """A browser of the given kind, logged in as username."""
    client = Client()
    client.username = username
    client.session.headers["User-Agent"] = agent
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def refresh(client):
    return client.post("/api/auth/refresh")


def session_key(redis, client):
    import hashlib

    token = client.session.cookies.get("chat_session")
    return "session:" + hashlib.sha256(token.encode()).hexdigest().upper()


def user_id(client):
    return client.get("/api/auth/me").json()["id"]


def room_of(client):
    room_id = client.post("/api/rooms", json={"name": "devices"}).json()["id"]
    return room_id


def send(client, room_id, count):
    socket = Socket(client)
    for index in range(count):
        assert socket.post(room_id, f"m{index}")["type"] == "ack"
    socket.close()


def wait_counted(redis, client, expected, timeout=10):
    """Waits until the device's sends are counted. The count is recorded in
    the background after each message's ACK, so a refresh right after the
    last ACK could otherwise see an older count."""
    device = redis.get(session_key(redis, client)).split("|")[3]
    deadline = time.time() + timeout
    while True:
        counted = sum(int(redis.get(key) or 0)
                      for key in redis.scan_iter(match=f"device_sends:{device}:*"))
        if counted >= expected:
            assert counted == expected, counted
            return
        assert time.time() < deadline, f"{counted} of {expected} sends counted"
        time.sleep(0.05)


def wait_closed(socket, timeout):
    """The close code once the server closes the socket."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.ws.recv(timeout=max(0.1, deadline - time.time()))
        except ConnectionClosed as closed:
            return closed.rcvd.code if closed.rcvd else None
        except TimeoutError:
            break
    return None


def connected(client):
    socket = Socket(client)
    socket.subscribe(room_of(client))
    return socket


# Trusted devices -------------------------------------------------------------


def test_first_phone_is_trusted():
    phone = device(register(), PHONE)
    assert refresh(phone).status_code == 200


def test_phone_and_computer_are_trusted_independently(redis):
    username = register()
    phone = device(username, PHONE)
    computer = device(username, COMPUTER)
    assert refresh(phone).status_code == 200
    assert refresh(computer).status_code == 200
    assert set(redis.hkeys(f"trusted:{user_id(phone)}")) == {"phone", "computer"}


def test_new_phone_needs_more_than_one_and_a_half_times_the_messages(redis):
    username = register()
    old = device(username, PHONE)
    new = device(username, PHONE)
    room_id = room_of(old)
    old.post(f"/api/rooms/{room_id}/invites", json={"username": username})
    send(old, room_id, 2)
    wait_counted(redis, old, 2)

    response = refresh(new)
    assert response.status_code == 403
    assert error_code(response) == "device_required"

    send(new, room_id, 3)  # 3 is exactly 1.5 times 2: not enough
    wait_counted(redis, new, 3)
    assert refresh(new).status_code == 403
    send(new, room_id, 1)  # 4 is more than 1.5 times 2
    wait_counted(redis, new, 4)
    assert refresh(new).status_code == 200
    assert refresh(old).status_code == 403


def test_at_most_one_trusted_device_of_each_kind(redis):
    username = register()
    devices = [device(username, agent) for agent in (PHONE, PHONE, COMPUTER, COMPUTER, PHONE)]
    trusted = redis.hgetall(f"trusted:{user_id(devices[0])}")
    assert set(trusted) == {"phone", "computer"}
    assert sum(refresh(d).status_code == 200 for d in devices) == 2


# Refresh ---------------------------------------------------------------------


def test_refresh_restores_the_full_lifetime_and_keeps_the_token(redis):
    phone = device(register(), PHONE)
    key = session_key(redis, phone)
    token = phone.session.cookies.get("chat_session")
    redis.expire(key, 100)
    response = refresh(phone)
    assert response.status_code == 200
    assert response.json()["expires_in"] == TTL
    assert redis.ttl(key) > TTL - 60
    assert phone.session.cookies.get("chat_session") == token
    assert phone.get("/api/auth/me").status_code == 200


def test_refresh_after_logout_is_unauthenticated():
    phone = device(register(), PHONE)
    token = phone.session.cookies.get("chat_session")
    assert phone.post("/api/auth/logout").status_code == 200
    phone.session.cookies.set("chat_session", token)
    assert refresh(phone).status_code == 401


def test_refresh_needs_a_session(anon):
    assert refresh(anon).status_code == 401


@pytest.mark.skipif(not os.environ.get("STRICT_AUTH_RATE"), reason="needs Nginx's default limits")
def test_refresh_is_not_rate_limited_by_nginx():
    phone = device(register(), PHONE)
    assert all(refresh(phone).status_code == 200 for _ in range(15))


# Logout ----------------------------------------------------------------------


def test_logout_from_an_untrusted_device_ends_only_its_session():
    username = register()
    trusted = device(username, COMPUTER)
    untrusted = device(username, COMPUTER)
    trusted_socket, untrusted_socket = connected(trusted), connected(untrusted)

    assert untrusted.post("/api/auth/logout").status_code == 200
    assert wait_closed(untrusted_socket, 5) == SESSION_ENDED
    assert trusted.get("/api/auth/me").status_code == 200
    assert trusted_socket.post(room_of(trusted))["type"] == "ack"
    trusted_socket.close()


def test_logout_from_a_trusted_device_ends_every_session():
    username = register()
    computer = device(username, COMPUTER)
    phone = device(username, PHONE)
    other = device(username, PHONE)
    sockets = [connected(c) for c in (computer, phone, other)]
    phone_token = phone.session.cookies.get("chat_session")

    assert computer.post("/api/auth/logout").status_code == 200
    assert [wait_closed(s, 5) for s in sockets] == [SESSION_ENDED] * 3
    assert phone.get("/api/auth/me").status_code == 401
    assert other.get("/api/auth/me").status_code == 401
    phone.session.cookies.set("chat_session", phone_token)
    assert phone.get("/api/auth/me").status_code == 401


def test_no_pushes_after_logout(make_user, make_room):
    username = register()
    alice = device(username)
    bob = device(register())
    room_id = alice.post("/api/rooms", json={"name": "push"}).json()["id"]
    alice.post(f"/api/rooms/{room_id}/invites", json={"username": bob.username})
    bob.post(f"/api/rooms/{room_id}/join")
    socket = Socket(alice)
    socket.subscribe(room_id)
    alice.post("/api/auth/logout")
    assert wait_closed(socket, 5) == SESSION_ENDED
    send(bob, room_id, 1)  # the closed socket receives nothing more


# Expiry ----------------------------------------------------------------------


@short_check
def test_expired_session_is_closed_within_the_check_interval(redis):
    client = device(register())
    socket = connected(client)
    redis.delete(session_key(redis, client))  # as if it expired
    assert wait_closed(socket, 4) == SESSION_ENDED


@short_check
def test_connections_stay_open_while_sessions_cannot_be_checked(redis):
    client = device(register())
    socket = connected(client)
    redis.execute_command("ACL", "SETUSER", "default", "-mget")
    try:
        redis.delete(session_key(redis, client))
        assert wait_closed(socket, 3) is None
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+mget")
    assert wait_closed(socket, 4) == SESSION_ENDED
