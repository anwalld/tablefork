"""The chat while the AI service is down, and its recovery.

scripts/test.sh runs the "prepare" phase, stops the ai service, runs "down",
starts it again and runs "up". The phases share the room through Redis.
"""

import os
import time

import pytest

from ai_helpers import add_ai, store_key
from conftest import Client, Socket

PHASE = os.environ.get("AI_PHASE")
KEY = "test:ai_faults"
TRIGGER = "@Claude AI 服务停了也要回我"


def restore_user(redis):
    state = redis.hgetall(KEY)
    client = Client()
    for part in state["cookie"].split("; "):
        name, value = part.split("=", 1)
        client.session.cookies.set(name, value)
    return client, int(state["room"])


def ai_replies(client, room_id):
    history = client.get(f"/api/rooms/{room_id}/messages").json()["messages"]
    return [m for m in history if m["member"]["type"] == "ai"]


@pytest.mark.skipif(PHASE != "prepare", reason="part of the ai service fault sequence")
def test_prepare_room_with_ai(make_user, make_room, redis):
    # Keep proactive one-human-room messages out of the reconnect assertion.
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    store_key(alice, "anthropic")
    assert add_ai(alice, room_id, "claude").json()["status"] == "joined"
    redis.hset(KEY, mapping={"room": room_id, "cookie": alice.cookie_header()})


@pytest.mark.skipif(PHASE != "down", reason="part of the ai service fault sequence")
def test_chat_works_while_ai_service_is_down(redis):
    alice, room_id = restore_user(redis)
    socket = Socket(alice)
    socket.subscribe(room_id)
    ack = socket.post(room_id, TRIGGER)
    assert ack["type"] == "ack"
    assert socket.expect("message")["content"] == TRIGGER
    socket.close()
    assert ai_replies(alice, room_id) == []


@pytest.mark.skipif(PHASE != "up", reason="part of the ai service fault sequence")
def test_reply_arrives_once_after_reconnecting(redis):
    alice, room_id = restore_user(redis)
    deadline = time.time() + 30
    while not ai_replies(alice, room_id) and time.time() < deadline:
        time.sleep(0.5)
    replies = ai_replies(alice, room_id)
    assert len(replies) == 1
    assert replies[0]["content"].endswith(TRIGGER)
    # Resent events must not bring a second reply.
    time.sleep(3)
    assert len(ai_replies(alice, room_id)) == 1


@pytest.mark.skipif(PHASE != "generating", reason="part of the ai service fault sequence")
def test_start_a_generation_before_stopping_ai(redis):
    alice, room_id = restore_user(redis)
    with __import__('contextlib').closing(Socket(alice)) as socket:
        socket.subscribe(room_id)
        assert socket.post(room_id, "@Claude #restart 回复恢复验证")["type"] == "ack"
        while True:
            chunk = socket.expect("ai_chunk", timeout=15)
            if chunk["text"]:
                redis.hset(KEY, mapping={"generation": chunk["generation_id"]})
                break


@pytest.mark.skipif(PHASE != "recovered", reason="part of the ai service fault sequence")
def test_generation_recovers_with_one_stored_message(redis):
    alice, room_id = restore_user(redis)
    generation = redis.hget(KEY, "generation")
    deadline = time.time() + 30
    matching = []
    while time.time() < deadline:
        matching = [m for m in ai_replies(alice, room_id) if m["generation_id"] == generation]
        if matching: break
        time.sleep(.25)
    assert len(matching) == 1
    assert matching[0]["content"].endswith("回复恢复验证")
    time.sleep(3)
    assert len([m for m in ai_replies(alice, room_id) if m["generation_id"] == generation]) == 1
