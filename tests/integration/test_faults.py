import os
import time

import pytest

from conftest import CONFIRM_DELETE
from test_messaging import history, seqs


def test_message_survives_failed_publish(make_user, make_room, make_socket, redis):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    alice_socket, bob_socket = make_socket(alice), make_socket(bob)
    bob_socket.subscribe(room_id)
    redis.execute_command("ACL", "SETUSER", "default", "-publish")
    try:
        ack = alice_socket.post(room_id, "not pushed")
        assert ack["type"] == "ack"
        bob_socket.assert_silent()
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+publish")
    assert seqs(history(bob, room_id)) == [ack["seq"]]


def test_message_survives_failed_cache_write(make_user, make_room, make_socket, redis):
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    redis.execute_command("ACL", "SETUSER", "default", "-zadd")
    try:
        assert socket.post(room_id)["type"] == "ack"
        time.sleep(0.1)
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+zadd")
    assert not redis.exists(f"room:{room_id}:recent")
    assert seqs(history(user, room_id)) == [1]


def test_push_recovers_after_subscriber_connection_drops(
    make_user, make_room, make_socket, redis
):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    alice_socket, bob_socket = make_socket(alice), make_socket(bob)
    bob_socket.subscribe(room_id)
    assert redis.client_kill_filter(_type="pubsub") >= 1
    channel = f"room:{room_id}"
    deadline = time.monotonic() + 10
    while dict(redis.pubsub_numsub(channel))[channel] == 0:
        assert time.monotonic() < deadline, "subscription was not restored"
        time.sleep(0.1)
    ack = alice_socket.post(room_id)
    assert bob_socket.expect("message")["seq"] == ack["seq"]


def test_subscribe_does_not_hang_when_publishing_fails(
    make_user, make_room, make_socket, redis
):
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    redis.execute_command("ACL", "SETUSER", "default", "-publish")
    try:
        started = time.monotonic()
        assert socket.subscribe(room_id)["type"] == "subscribed"
        assert time.monotonic() - started < 0.5
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+publish")


def test_deleted_room_leaves_no_cached_messages(
    make_user, make_room, make_socket, redis
):
    user = make_user()
    socket = make_socket(user)
    rooms = []
    for _ in range(40):
        room_id = make_room(user)
        rooms.append(room_id)
        socket.send_message(room_id, "secret")
        user.post(f"/api/rooms/{room_id}/leave", json=CONFIRM_DELETE)
        socket.expect("ack", "error")
    time.sleep(0.3)
    assert not [r for r in rooms if redis.exists(f"room:{r}:recent")]


@pytest.mark.skipif(
    not os.environ.get("SMALL_QUEUE"),
    reason="needs WORKER_COUNT=1 WORKER_QUEUE_LIMIT=1 on the app",
)
def test_full_queue_rejects_without_saving(make_user, make_room, make_socket):
    user = make_user()
    room_id = make_room(user)
    socket = make_socket(user)
    total = 200
    for index in range(total):
        socket.send_message(room_id, f"m{index}")
    replies = [socket.expect("ack", "error", timeout=30) for _ in range(total)]
    acks = [reply for reply in replies if reply["type"] == "ack"]
    busy = [reply for reply in replies if reply["type"] == "error"]
    assert busy, "expected the queue to overflow"
    assert all(
        reply["error"]["code"] == "busy" and reply["error"]["retryable"]
        for reply in busy
    )
    assert history(user, room_id)["last_seq"] == len(acks)
