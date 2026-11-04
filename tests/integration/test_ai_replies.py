"""AI reply rules end to end, with fake providers.

scripts/test.sh runs this with AI_IDLE_SECONDS=3, AI_PROACTIVE_P1=1,
AI_CHAIN_P=0 and AI_GENERATION_TIMEOUT=3.
"""

import os
import time

import pytest

from ai_helpers import add_ai, ai_reply, store_key

pytestmark = pytest.mark.skipif(
    not os.environ.get("AI_TESTS"), reason="needs the AI service with fake providers"
)


def room_with(make_user, make_room, slugs, humans=1):
    users = [make_user() for _ in range(humans)]
    room_id = make_room(*users)
    owner = users[0]
    providers = {"claude": "anthropic", "gpt": "openai", "deepseek": "deepseek", "gemini": "gemini"}
    for slug in slugs:
        store_key(owner, providers[slug])
        assert add_ai(owner, room_id, slug).json()["status"] == "joined"
    return users, room_id


def assert_no_ai_reply(socket, seconds=2.0):
    # Chunks of replies already seen may still sit in the buffer; a chunk can
    # even arrive after its finished message, since they travel separately.
    socket.buffer = [m for m in socket.buffer if m["type"] != "ai_chunk"]
    deadline = time.time() + seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        try:
            message = socket.expect("message", "ai_chunk", timeout=remaining)
        except TimeoutError:
            return
        assert message["type"] == "message" and message["member"]["type"] == "human", message


def test_multi_human_room_answers_only_mentions(make_user, make_room, make_socket):
    (alice, bob), room_id = room_with(make_user, make_room, ["claude", "gpt"], humans=2)
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "大家好")
    assert_no_ai_reply(socket)
    socket.post(room_id, "@claude 你好")
    assert ai_reply(socket)["member"]["name"] == "Claude"
    socket.post(room_id, "@claude @gpt 你们好")
    names = {ai_reply(socket)["member"]["name"], ai_reply(socket)["member"]["name"]}
    assert names == {"Claude", "GPT"}


def test_one_human_room_keeps_its_partner_until_a_mention(make_user, make_room, make_socket):
    (alice,), room_id = room_with(make_user, make_room, ["claude", "gpt"])
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "第一句")
    partner = ai_reply(socket)["member"]["name"]
    for text in ("第二句", "第三句"):
        socket.post(room_id, text)
        assert ai_reply(socket)["member"]["name"] == partner
    other = "GPT" if partner == "Claude" else "Claude"
    socket.post(room_id, f"@{other} 换你")
    assert ai_reply(socket)["member"]["name"] == other
    socket.post(room_id, "继续")
    assert ai_reply(socket)["member"]["name"] == other


def test_random_partner_only_among_ais_with_keys(make_user, make_room, make_socket):
    (alice,), room_id = room_with(make_user, make_room, ["claude", "gpt"])
    alice.request("DELETE", "/api/keys/anthropic")
    socket = make_socket(alice)
    socket.subscribe(room_id)
    for text in ("一", "二"):
        socket.post(room_id, text)
        assert ai_reply(socket)["member"]["name"] == "GPT"


def test_no_usable_ai_tells_the_sender(make_user, make_room, make_socket):
    (alice,), room_id = room_with(make_user, make_room, ["claude"])
    alice.request("DELETE", "/api/keys/anthropic")
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "有人吗")
    notice = socket.expect("ai_notice", timeout=10)
    assert notice["code"] == "api_key_required"
    assert_no_ai_reply(socket)


def test_missing_key_notice_reaches_only_the_sender(make_user, make_room, make_socket):
    (alice, bob), room_id = room_with(make_user, make_room, ["claude"], humans=2)
    alice_socket, bob_socket = make_socket(alice), make_socket(bob)
    alice_socket.subscribe(room_id)
    bob_socket.subscribe(room_id)
    bob_socket.post(room_id, "@claude 你好")
    notice = bob_socket.expect("ai_notice", timeout=10)
    assert notice["provider"] == "anthropic"
    with pytest.raises(TimeoutError):
        alice_socket.expect("ai_notice", timeout=1.5)
    assert_no_ai_reply(alice_socket, 1)


def test_reply_streams_and_only_the_final_text_is_stored(make_user, make_room, make_socket):
    (alice, bob), room_id = room_with(make_user, make_room, ["claude"], humans=2)
    bob_socket = make_socket(bob)
    bob_socket.subscribe(room_id)
    make_socket(alice).post(room_id, "@claude 讲个笑话")
    chunks = []
    while True:
        event = bob_socket.expect("ai_chunk", "message", timeout=10)
        if event["type"] == "ai_chunk":
            chunks.append(event)
        elif event["member"]["type"] == "ai":
            final = event
            break
    assert chunks
    assert {c["generation_id"] for c in chunks} == {final["generation_id"]}
    assert chunks[-1]["cumulative"] is True
    assert "".join(c["text"] for c in chunks).strip() == final["content"]
    assert chunks[-1]["snapshot"].strip() == final["content"]
    history = bob.get(f"/api/rooms/{room_id}/messages").json()["messages"]
    assert [m["member"]["type"] for m in history] == ["human", "ai"]
    assert history[1]["generation_id"] == final["generation_id"]


def test_messages_during_a_reply_are_answered_once(make_user, make_room, make_socket):
    # Two humans, so no silence timer interferes.
    (alice, bob), room_id = room_with(make_user, make_room, ["claude"], humans=2)
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "@claude 第一条")
    socket.expect("ai_chunk", timeout=10)
    socket.post(room_id, "@claude 第二条")
    socket.post(room_id, "@claude 第三条")
    first, second = ai_reply(socket), ai_reply(socket)
    assert second["content"].endswith("第三条")
    assert_no_ai_reply(socket, 2)


def test_failure_clears_the_room_and_tells_the_sender(make_user, make_room, make_socket):
    (alice, bob), room_id = room_with(make_user, make_room, ["claude"], humans=2)
    alice_socket, bob_socket = make_socket(alice), make_socket(bob)
    alice_socket.subscribe(room_id)
    bob_socket.subscribe(room_id)
    alice_socket.post(room_id, "@claude #fail")
    assert bob_socket.expect("ai_failed", timeout=10)["member_id"]
    alice_socket.expect("ai_failed", timeout=10)
    assert alice_socket.expect("ai_notice", timeout=10)["code"] == "provider_error"
    with pytest.raises(TimeoutError):
        bob_socket.expect("ai_notice", timeout=1)
    history = alice.get(f"/api/rooms/{room_id}/messages").json()["messages"]
    assert [m["member"]["type"] for m in history] == ["human"]


def test_timeout_is_a_failure(make_user, make_room, make_socket):
    (alice,), room_id = room_with(make_user, make_room, ["claude"])
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "#slow")
    assert socket.expect("ai_notice", timeout=15)["code"] == "timeout"


def test_silence_brings_proactive_messages_then_goodbye(make_user, make_room, make_socket):
    (alice,), room_id = room_with(make_user, make_room, ["claude"])
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "在吗")
    ai_reply(socket)
    proactive = [ai_reply(socket, timeout=60) for _ in range(3)]
    assert "道个别" not in proactive[0]["content"]
    assert "道个别" not in proactive[1]["content"]
    assert "道个别" in proactive[2]["content"]
    # The conversation is over: no more proactive messages.
    assert_no_ai_reply(socket, 8)


def test_after_goodbye_a_mention_continues(make_user, make_room, make_socket):
    (alice,), room_id = room_with(make_user, make_room, ["claude", "gpt"])
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "@claude 在吗")
    ai_reply(socket)
    for _ in range(3):
        ai_reply(socket, timeout=60)
    socket.post(room_id, "@claude 我们接着说")
    assert ai_reply(socket)["member"]["name"] == "Claude"


def test_multi_human_room_has_no_proactive_messages(make_user, make_room, make_socket):
    (alice, bob), room_id = room_with(make_user, make_room, ["claude"], humans=2)
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "@claude 你好")
    ai_reply(socket)
    assert_no_ai_reply(socket, 8)


@pytest.mark.skipif(not os.environ.get("AI_CHAIN_ONE"), reason="needs AI_CHAIN_P=1")
def test_ais_pick_up_each_other(make_user, make_room, make_socket):
    (alice,), room_id = room_with(make_user, make_room, ["claude", "gpt"])
    socket = make_socket(alice)
    socket.subscribe(room_id)
    socket.post(room_id, "聊聊天气")
    first = ai_reply(socket)["member"]["name"]
    second = ai_reply(socket)["member"]["name"]
    assert {first, second} == {"Claude", "GPT"}


@pytest.mark.skipif(not os.environ.get("STRICT_MESSAGE_RATE"), reason="needs a small MESSAGE_RATE_LIMIT")
def test_ai_replies_do_not_use_up_the_users_limit(make_user, make_room, make_socket):
    limit = int(os.environ["MESSAGE_RATE_LIMIT"])
    window = int(os.environ["MESSAGE_RATE_WINDOW_SECONDS"])
    (alice,), room_id = room_with(make_user, make_room, ["claude"])
    socket = make_socket(alice)
    socket.subscribe(room_id)
    time.sleep(window - time.time() % window + 0.05)
    assert socket.post(room_id, "第一条")["type"] == "ack"
    ai_reply(socket)  # stored while the window is still open
    for index in range(limit - 1):
        reply = socket.post(room_id, f"再来 {index}")
        assert reply["type"] == "ack", reply



def test_twenty_replies_publish_increasing_chunk_sequences(make_user, make_room, make_socket):
    (alice, bob), room_id = room_with(make_user, make_room, ["claude"], humans=2)
    reader = make_socket(bob)
    reader.subscribe(room_id)
    writer = make_socket(alice)
    generations = set()
    for index in range(20):
        assert writer.post(room_id, f"@Claude ordered reply {index}")["type"] == "ack"
        chunks = []
        while True:
            event = reader.expect("ai_chunk", "message", timeout=10)
            if event["type"] == "ai_chunk":
                chunks.append(event)
            elif event["member"]["type"] == "ai":
                final = event
                break
        generation = final["generation_id"]
        assert generation not in generations
        generations.add(generation)
        assert chunks
        assert {chunk["generation_id"] for chunk in chunks} == {generation}
        sequences = [chunk["sequence"] for chunk in chunks]
        assert all(left < right for left, right in zip(sequences, sequences[1:]))
        assert chunks[-1]["snapshot"] == final["content"]
