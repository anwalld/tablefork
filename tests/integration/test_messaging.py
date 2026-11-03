import hashlib
import threading
import time
import uuid

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.exceptions import InvalidStatus

from conftest import CONFIRM_DELETE, ORIGIN, Socket, error_code


def history(client, room_id, **params):
    response = client.get(f"/api/rooms/{room_id}/messages", params=params)
    assert response.status_code == 200
    return response.json()


def seqs(body):
    return [message["seq"] for message in body["messages"]]


def fill(socket, room_id, count):
    return [socket.post(room_id, f"m{i}")["seq"] for i in range(count)]


# Connections


def test_connection_requires_session(anon):
    with pytest.raises(InvalidStatus) as error:
        Socket(anon)
    assert error.value.response.status_code == 401


def test_connection_rejects_foreign_origin(make_user):
    with pytest.raises(InvalidStatus) as error:
        Socket(make_user(), origin="https://evil.example")
    assert error.value.response.status_code == 403


def test_connection_with_session_and_origin(make_user, make_socket):
    assert make_socket(make_user(), origin=ORIGIN)


# Sending


def test_ack_contains_seq(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    ack = make_socket(user).post(room_id)
    assert ack["type"] == "ack"
    assert ack["seq"] == 1
    assert ack["duplicate"] is False


def test_seq_is_per_room_and_consecutive(make_user, make_room, make_socket):
    user = make_user()
    room_a, room_b = make_room(user), make_room(user)
    socket = make_socket(user)
    assert fill(socket, room_a, 3) == [1, 2, 3]
    assert fill(socket, room_b, 2) == [1, 2]


@pytest.mark.parametrize("content", ["", "x" * 4001])
def test_invalid_content_is_rejected_without_using_seq(
    make_user, make_room, make_socket, content
):
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    reply = socket.post(room_id, content)
    assert reply["type"] == "error"
    assert reply["error"]["code"] == "invalid_argument"
    assert socket.post(room_id)["seq"] == 1


def test_content_limit_counts_characters(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    assert make_socket(user).post(room_id, "字" * 4000)["type"] == "ack"


def test_non_member_cannot_send(make_user, make_room, make_socket):
    owner, stranger = make_user(), make_user()
    room_id = make_room(owner)
    reply = make_socket(stranger).post(room_id)
    assert reply["type"] == "error"
    assert reply["error"]["code"] == "room_not_found"
    assert history(owner, room_id)["last_seq"] == 0


def test_send_to_deleted_room(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    user.post(f"/api/rooms/{room_id}/leave", json=CONFIRM_DELETE)
    assert socket.post(room_id)["error"]["code"] == "room_not_found"


def test_logout_closes_the_connection(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    user.post("/api/auth/logout")
    with pytest.raises(ConnectionClosed) as closed:
        socket.post(room_id)
    assert closed.value.rcvd.code == 4401


def test_send_after_the_session_ended_is_unauthenticated(make_user, make_room, make_socket, redis):
    # A session that expired is noticed by the next periodic check; until
    # then every message is still checked on its own.
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    token = user.session.cookies.get("chat_session")
    # Keep the periodic check (MGET) from closing the socket first.
    redis.execute_command("ACL", "SETUSER", "default", "-mget")
    try:
        redis.delete("session:" + hashlib.sha256(token.encode()).hexdigest().upper())
        reply = socket.post(room_id)
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+mget")
    assert reply["error"]["code"] == "unauthenticated"


def test_malformed_frame_gets_an_error(make_user, make_socket):
    socket = make_socket(make_user())
    socket.ws.send("not json")
    assert socket.expect("error")["error"]["code"] == "invalid_argument"


# Deduplication


def test_resend_returns_original_seq(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    client_msg_id = uuid.uuid4().hex
    first = socket.post(room_id, client_msg_id=client_msg_id)
    second = socket.post(room_id, client_msg_id=client_msg_id)
    assert second["seq"] == first["seq"]
    assert second["duplicate"] is True
    assert history(user, room_id)["last_seq"] == 1
    assert socket.post(room_id)["seq"] == 2


def test_same_id_from_different_members(make_user, make_room, make_socket):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    client_msg_id = uuid.uuid4().hex
    first = make_socket(alice).post(room_id, client_msg_id=client_msg_id)
    second = make_socket(bob).post(room_id, client_msg_id=client_msg_id)
    assert (first["seq"], second["seq"]) == (1, 2)


# Ordering under concurrency


def test_concurrent_senders_get_consecutive_seq(make_user, make_room, make_socket):
    users = [make_user() for _ in range(5)]
    room_id = make_room(users[0], *users[1:])
    per_user = 40
    seen = []
    lock = threading.Lock()

    def run(user, socket):
        for index in range(per_user):
            socket.send_message(room_id, f"{user.username}-{index}")
        acks = [socket.expect("ack") for _ in range(per_user)]
        with lock:
            seen.extend(ack["seq"] for ack in acks)

    sockets = [make_socket(user) for user in users]
    threads = [
        threading.Thread(target=run, args=pair) for pair in zip(users, sockets)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(seen) == list(range(1, len(users) * per_user + 1))


def test_busy_room_does_not_starve_others(make_user, make_room, make_socket):
    user = make_user()
    busy_room = make_room(user)
    socket = make_socket(user)
    for index in range(300):
        socket.send_message(busy_room, f"m{index}")
    other = make_user()
    start = time.monotonic()
    assert other.post("/api/rooms", json={"name": "other"}).status_code == 201
    assert time.monotonic() - start < 2
    for _ in range(300):
        socket.expect("ack", timeout=30)


# Push


def test_online_members_receive_messages(make_user, make_room, make_socket):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    alice_socket, bob_socket = make_socket(alice), make_socket(bob)
    assert alice_socket.subscribe(room_id)["type"] == "subscribed"
    assert bob_socket.subscribe(room_id)["type"] == "subscribed"
    ack = alice_socket.post(room_id, "你好")
    for socket in (alice_socket, bob_socket):
        message = socket.expect("message")
        assert message["room_id"] == room_id
        assert message["seq"] == ack["seq"]
        assert message["content"] == "你好"
        assert message["member"]["name"] == alice.username
        assert message["member"]["type"] == "human"
        assert message["created_at"] > 0


def test_non_member_cannot_subscribe(make_user, make_room, make_socket):
    owner, stranger = make_user(), make_user()
    room_id = make_room(owner)
    socket = make_socket(stranger)
    reply = socket.subscribe(room_id)
    assert reply["error"]["code"] == "room_not_found"
    make_socket(owner).post(room_id)
    socket.assert_silent()


def test_leaving_stops_delivery(make_user, make_room, make_socket):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    alice_socket, bob_socket = make_socket(alice), make_socket(bob)
    alice_socket.subscribe(room_id)
    bob_socket.subscribe(room_id)
    bob.post(f"/api/rooms/{room_id}/leave")
    assert bob_socket.expect("room_left")["room_id"] == room_id
    alice_socket.post(room_id)
    alice_socket.expect("message")
    bob_socket.assert_silent()


def test_redis_subscription_follows_connections(
    make_user, make_room, make_socket, redis
):
    user = make_user()
    room_id = make_room(user)
    channel = f"room:{room_id}"

    def subscribers():
        return dict(redis.pubsub_numsub(channel))[channel]

    assert subscribers() == 0
    first, second = make_socket(user), make_socket(user)
    first.subscribe(room_id)
    second.subscribe(room_id)
    assert subscribers() == 1
    first.close()
    time.sleep(0.2)
    assert subscribers() == 1
    second.close()
    time.sleep(0.2)
    assert subscribers() == 0


def test_no_message_missed_between_subscribe_and_history(
    make_user, make_room, make_socket
):
    """A message published right after subscribed is pushed or in history."""
    for _ in range(10):
        alice, bob = make_user(), make_user()
        room_id = make_room(alice, bob)
        writer = make_socket(alice)
        reader = make_socket(bob)
        writer.send_message(room_id)
        reader.subscribe(room_id)
        known = set(seqs(history(bob, room_id)))
        ack = writer.expect("ack")
        if ack["seq"] not in known:
            assert reader.expect("message")["seq"] == ack["seq"]


# History


def test_history_latest_page(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    fill(make_socket(user), room_id, 5)
    body = history(user, room_id, limit=3)
    assert seqs(body) == [3, 4, 5]
    assert body["last_seq"] == 5
    assert body["messages"][-1]["content"] == "m4"


def test_history_page_backwards(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    fill(make_socket(user), room_id, 5)
    assert seqs(history(user, room_id, before=3, limit=10)) == [1, 2]
    assert seqs(history(user, room_id, before=1)) == []


def test_history_backfill_after_seq(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    fill(make_socket(user), room_id, 5)
    assert seqs(history(user, room_id, after=2)) == [3, 4, 5]
    assert seqs(history(user, room_id, after=2, limit=2)) == [3, 4]
    assert seqs(history(user, room_id, after=5)) == []


def test_history_with_extreme_cursors(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    fill(make_socket(user), room_id, 3)
    largest = 2**63 - 1
    assert seqs(history(user, room_id, after=largest)) == []
    assert seqs(history(user, room_id, before=largest)) == [1, 2, 3]


def test_connection_receives_only_subscribed_rooms(make_user, make_room, make_socket):
    alice = make_user()
    room_id = make_room(alice)
    writer, idle = make_socket(alice), make_socket(alice)
    writer.post(room_id)
    idle.assert_silent()


def test_history_rejects_conflicting_cursors(make_user, make_room):
    user = make_user()
    room_id = make_room(user)
    response = user.get(
        f"/api/rooms/{room_id}/messages", params={"before": 2, "after": 1}
    )
    assert response.status_code == 400


def test_history_requires_membership(make_user, make_room):
    owner, stranger = make_user(), make_user()
    room_id = make_room(owner)
    response = stranger.get(f"/api/rooms/{room_id}/messages")
    assert response.status_code == 404
    assert error_code(response) == "room_not_found"


def test_leaver_messages_stay_for_others(make_user, make_room, make_socket):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    make_socket(bob).post(room_id, "from bob")
    bob.post(f"/api/rooms/{room_id}/leave")
    assert bob.get(f"/api/rooms/{room_id}/messages").status_code == 404
    assert history(alice, room_id)["messages"][0]["content"] == "from bob"


# Hot cache


def test_history_is_identical_without_the_cache(
    make_user, make_room, make_socket, redis
):
    user = make_user()
    room_id = make_room(user)
    fill(make_socket(user), room_id, 5)
    time.sleep(0.2)
    assert redis.zcard(f"room:{room_id}:recent") == 5
    cached = history(user, room_id)
    redis.delete(f"room:{room_id}:recent")
    assert history(user, room_id) == cached


def test_history_served_from_cache(make_user, make_room, make_socket, redis):
    user = make_user()
    room_id = make_room(user)
    fill(make_socket(user), room_id, 3)
    time.sleep(0.2)
    key = f"room:{room_id}:recent"
    entries = redis.zrange(key, 0, -1, withscores=True)
    redis.zadd(key, {entries[0][0].replace("m0", "cached"): entries[0][1]})
    redis.zrem(key, entries[0][0])
    assert history(user, room_id)["messages"][0]["content"] == "cached"


def test_partial_cache_falls_back_to_mysql(make_user, make_room, make_socket, redis):
    user = make_user()
    room_id = make_room(user)
    fill(make_socket(user), room_id, 5)
    time.sleep(0.2)
    redis.zremrangebyscore(f"room:{room_id}:recent", 2, 2)
    assert seqs(history(user, room_id)) == [1, 2, 3, 4, 5]
