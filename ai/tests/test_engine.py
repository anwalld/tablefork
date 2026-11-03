import asyncio
import json
import random

import fakeredis
import pytest

from chat_ai.agents import load_agents
from chat_ai.config import Settings
from chat_ai.engine import Engine
from chat_ai.gen import ai_pb2
from chat_ai.providers.factory import ProviderFactory

ALICE = (10, 1)
BOB = (11, 2)
CLAUDE = (100, "claude", "anthropic", "Claude")
GPT = (101, "gpt", "openai", "GPT")


def settings(**overrides):
    values = dict(
        grpc_port=0, grpc_token="t", redis_host="", redis_port=0, redis_password="",
        mysql_host="", mysql_user="", mysql_password="", mysql_database="",
        encryption_key=b"0" * 32, agents_file="agents.toml", fake_providers=True,
        chain_p=0.0, proactive_p1=1.0, idle_seconds=60, timer_poll_seconds=1,
        context_messages=30, context_chars=12000, max_output_tokens=100,
        generation_timeout=2, chunk_interval=0.0,
    )
    values.update(overrides)
    return Settings(**values)


class MemoryKeys:
    def __init__(self, keys):
        self.keys = keys  # {(user_id, provider): key}

    async def providers(self, user_id):
        return {p for (u, p) in self.keys if u == user_id}

    async def get(self, user_id, provider):
        return self.keys.get((user_id, provider))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Harness:
    def __init__(self, keys=None, **overrides):
        self.cache = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.actions = []
        self.clock = Clock()
        self.keys = MemoryKeys(keys if keys is not None else {
            (1, "anthropic"): "fake-a", (1, "openai"): "fake-o",
            (2, "anthropic"): "fake-b",
        })
        self.engine = Engine(
            settings(**overrides), self.cache, None, self.keys, load_agents("agents.toml"),
            ProviderFactory(2, fake=True), self.actions.append, random.Random(0), self.clock,
        )
        self.event_id = 0
        self.acks = []
        self.seq = 0

    def snapshot(self, humans, ais, room_id=1):
        return ai_pb2.RoomSnapshot(
            room_id=room_id,
            humans=[ai_pb2.Human(member_id=m, user_id=u) for m, u in humans],
            ais=[ai_pb2.AiMember(member_id=m, slug=s, provider=p, name=n) for m, s, p, n in ais],
        )

    async def cache_message(self, member_id, name, content, room_id=1):
        self.seq += 1
        entry = {"member": {"id": member_id, "name": name}, "content": content, "seq": self.seq}
        await self.cache.zadd(f"room:{room_id}:recent", {json.dumps(entry): self.seq})

    async def send_event(self, body, event_id=None):
        if event_id is None:
            self.event_id += 1
            event_id = self.event_id
        event = ai_pb2.GatewayEvent(instance_id="i", event_id=event_id, **body)

        async def ack():
            self.acks.append(event_id)

        await self.engine.handle(event, ack)
        return event_id

    async def human_says(self, human, content, humans, ais, name="alice"):
        await self.cache_message(human[0], name, content)
        saved = ai_pb2.MessageSaved(
            room=self.snapshot(humans, ais), seq=self.seq,
            sender=ai_pb2.Sender(member_id=human[0], user_id=human[1], name=name),
            content=content,
        )
        return await self.send_event({"message_saved": saved})

    async def ai_saved(self, final, ai, humans, ais):
        """The chat server stored a Final and reports it back."""
        await self.cache_message(ai[0], ai[3], final.content)
        saved = ai_pb2.MessageSaved(
            room=self.snapshot(humans, ais), seq=self.seq,
            sender=ai_pb2.Sender(member_id=ai[0], is_ai=True, slug=ai[1], name=ai[3]),
            content=final.content, generation_id=final.generation_id,
        )
        return await self.send_event({"message_saved": saved})

    async def settle(self):
        while self.engine.tasks:
            await asyncio.gather(*list(self.engine.tasks))

    def of(self, kind):
        return [getattr(a, kind) for a in self.actions if a.WhichOneof("body") == kind]


async def test_one_human_room_reply_streams_then_finishes():
    h = Harness()
    await h.human_says(ALICE, "你好", [ALICE], [CLAUDE])
    await h.settle()
    assert h.acks == [1]
    chunks, finals = h.of("chunk"), h.of("final")
    assert len(finals) == 1
    assert finals[0].ai_member_id == CLAUDE[0]
    assert finals[0].content == "Claude: alice：你好"
    assert "".join(c.text for c in chunks) == finals[0].content
    assert {c.generation_id for c in chunks} == {finals[0].generation_id}


async def test_resent_event_is_acknowledged_but_ignored():
    h = Harness()
    event_id = await h.human_says(ALICE, "hi", [ALICE], [CLAUDE])
    await h.settle()
    await h.send_event({"message_saved": ai_pb2.MessageSaved(
        room=h.snapshot([ALICE], [CLAUDE]),
        sender=ai_pb2.Sender(member_id=ALICE[0], user_id=ALICE[1]), content="hi",
    )}, event_id=event_id)
    await h.settle()
    assert h.acks == [event_id, event_id]
    assert len(h.of("final")) == 1


async def test_multi_human_room_needs_a_mention():
    h = Harness()
    await h.human_says(ALICE, "hello all", [ALICE, BOB], [CLAUDE])
    await h.settle()
    assert h.of("final") == []
    await h.human_says(ALICE, "@claude hello", [ALICE, BOB], [CLAUDE])
    await h.settle()
    assert len(h.of("final")) == 1


async def test_missing_key_notifies_only_the_payer():
    h = Harness(keys={})
    await h.human_says(BOB, "@claude hi", [ALICE, BOB], [CLAUDE], name="bob")
    await h.settle()
    notices = h.of("notice")
    assert len(notices) == 1
    assert notices[0].notify_member_id == BOB[0]
    assert notices[0].provider == "anthropic"
    assert h.of("final") == []


async def test_messages_during_a_generation_are_answered_once():
    h = Harness()
    await h.human_says(ALICE, "one", [ALICE], [CLAUDE])
    while not h.of("chunk"):  # the first reply is being generated
        await asyncio.sleep(0.01)
    await h.human_says(ALICE, "two", [ALICE], [CLAUDE])
    await h.human_says(ALICE, "three", [ALICE], [CLAUDE])
    await h.settle()
    finals = h.of("final")
    assert len(finals) == 2
    # The second reply saw everything sent meanwhile.
    assert finals[1].content.endswith("three")


async def test_timeout_fails_without_retry():
    h = Harness(generation_timeout=0.2)
    await h.human_says(ALICE, "#slow please", [ALICE], [CLAUDE])
    await h.settle()
    failed = h.of("failed")
    assert len(failed) == 1 and failed[0].code == "timeout"
    assert failed[0].notify_member_id == ALICE[0]
    assert h.of("final") == []


async def test_rejected_key_fails_with_its_code():
    h = Harness(keys={(1, "anthropic"): "fake-reject"})
    await h.human_says(ALICE, "hi", [ALICE], [CLAUDE])
    await h.settle()
    assert [f.code for f in h.of("failed")] == ["api_key_invalid"]


async def test_chain_continues_with_p_one_and_is_paid_by_the_human():
    h = Harness(chain_p=1.0)
    await h.human_says(ALICE, "hi", [ALICE], [CLAUDE, GPT])
    await h.settle()
    first = h.of("final")[0]
    replier = CLAUDE if first.ai_member_id == CLAUDE[0] else GPT
    other = GPT if replier is CLAUDE else CLAUDE
    await h.ai_saved(first, replier, [ALICE], [CLAUDE, GPT])
    await h.settle()
    second = h.of("final")[1]
    assert second.ai_member_id == other[0]


async def test_human_message_ends_an_old_chain():
    h = Harness(chain_p=1.0)
    await h.human_says(ALICE, "hi", [ALICE], [CLAUDE, GPT])
    await h.settle()
    first = h.of("final")[0]
    replier = CLAUDE if first.ai_member_id == CLAUDE[0] else GPT
    await h.human_says(ALICE, "new topic", [ALICE], [CLAUDE, GPT])
    await h.settle()
    count = len(h.of("final"))
    # The reply to the old message arrives late: it must not start a chain.
    await h.ai_saved(first, replier, [ALICE], [CLAUDE, GPT])
    await h.settle()
    assert len(h.of("final")) == count


async def test_silence_brings_proactive_messages_then_goodbye():
    h = Harness(proactive_p1=1.0)
    await h.human_says(ALICE, "hi", [ALICE], [CLAUDE])
    await h.settle()
    for _ in range(4):
        h.clock.now += 61
        await h.engine.fire_due_timers()
        await h.settle()
    finals = h.of("final")
    assert len(finals) == 4  # the reply, two proactive messages, the goodbye
    assert "道个别" in finals[-1].content
    assert await h.cache.zscore("ai:timers", 1) is None


async def test_timer_does_not_fire_early():
    h = Harness()
    await h.human_says(ALICE, "hi", [ALICE], [CLAUDE])
    await h.settle()
    h.clock.now += 30
    await h.engine.fire_due_timers()
    await h.settle()
    assert len(h.of("final")) == 1


async def test_second_human_joining_stops_the_timer():
    h = Harness()
    await h.human_says(ALICE, "hi", [ALICE], [CLAUDE])
    await h.settle()
    await h.send_event({"members_changed": ai_pb2.MembersChanged(room=h.snapshot([ALICE, BOB], [CLAUDE]))})
    assert await h.cache.zscore("ai:timers", 1) is None


async def test_failure_is_reported_for_provider_errors():
    h = Harness()
    await h.human_says(ALICE, "#fail now", [ALICE], [CLAUDE])
    await h.settle()
    assert [f.code for f in h.of("failed")] == ["provider_error"]


async def test_room_deleted_forgets_state():
    h = Harness()
    await h.human_says(ALICE, "hi", [ALICE], [CLAUDE])
    await h.settle()
    await h.send_event({"room_deleted": ai_pb2.RoomDeleted(room_id=1)})
    assert await h.cache.get("ai:room:1") is None
    assert await h.cache.zscore("ai:timers", 1) is None


@pytest.mark.parametrize("token,ok", [("Bearer t", True), ("Bearer wrong", False), ("", False)])
async def test_gateway_checks_the_token(token, ok):
    import grpc

    from chat_ai.server import Gateway

    class Context:
        def invocation_metadata(self):
            return [("authorization", token)] if token else []

        async def abort(self, code, message):
            raise PermissionError(code)

    gateway = Gateway(settings(), None, None, None, None)
    if ok:
        await gateway._authorize(Context())
    else:
        with pytest.raises(PermissionError):
            await gateway._authorize(Context())


async def test_messages_before_a_generation_starts_share_one_reply():
    h = Harness()
    await h.human_says(ALICE, "one", [ALICE], [CLAUDE])
    await h.human_says(ALICE, "two", [ALICE], [CLAUDE])
    await h.settle()
    assert len(h.of("final")) == 1
