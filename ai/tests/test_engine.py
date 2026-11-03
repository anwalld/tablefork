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
        generation_timeout=2, chunk_interval=0.0, generation_max_attempts=1,
        retry_base_seconds=0.1, work_lease_seconds=1,
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
    def __init__(self, keys=None, auto_save=True, **overrides):
        self.auto_save = auto_save
        self.saved_ids = set()
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
        while True:
            if self.engine.tasks:
                await asyncio.gather(*list(self.engine.tasks))
            unsaved = [f for f in self.of('final') if f.generation_id not in self.saved_ids]
            if not self.auto_save or not unsaved:
                break
            for final in unsaved:
                self.saved_ids.add(final.generation_id)
                state = await self.engine._load(final.room_id)
                ai = next(a for a in state.ais if a.member_id == final.ai_member_id)
                await self.ai_saved(final, (ai.member_id, ai.slug, ai.provider, ai.name),
                    [(h.member_id, h.user_id) for h in state.humans],
                    [(a.member_id, a.slug, a.provider, a.name) for a in state.ais])

    def of(self, kind):
        return [getattr(a, kind) for a in self.actions if a.WhichOneof("body") == kind]


async def test_one_human_room_reply_streams_then_finishes():
    h = Harness()
    await h.human_says(ALICE, "你好", [ALICE], [CLAUDE])
    await h.settle()
    assert h.acks[:1] == [1]
    chunks, finals = h.of("chunk"), h.of("final")
    assert len(finals) == 1
    assert finals[0].ai_member_id == CLAUDE[0]
    assert finals[0].content == "Claude: alice：你好"
    assert "".join(c.text for c in chunks) == finals[0].content
    assert chunks[-1].snapshot == finals[0].content
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
    assert h.acks.count(event_id) == 2
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
    h = Harness(chain_p=1.0, auto_save=False)
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
    h = Harness(chain_p=1.0, auto_save=False)
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
    assert len(h.of("final")) == count + 1
    assert h.of("final")[-1].content.endswith("new topic")


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
    assert (await h.engine._load(1)).deleted is True
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


async def test_dependency_failure_can_be_replayed(monkeypatch):
    h = Harness()
    original = h.keys.providers
    async def unavailable(user_id):
        raise ConnectionError('injected failure')
    monkeypatch.setattr(h.keys, 'providers', unavailable)
    with pytest.raises(ConnectionError):
        await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE])
    assert not await h.cache.exists('ai:event:i:1')
    assert h.acks == []
    monkeypatch.setattr(h.keys, 'providers', original)
    await h.send_event({'message_saved': ai_pb2.MessageSaved(
        room=h.snapshot([ALICE], [CLAUDE]), content='hello',
        sender=ai_pb2.Sender(member_id=10, user_id=1))}, event_id=1)
    await h.settle()
    assert len(h.of('final')) == 1


async def test_receipt_and_pending_are_visible_before_ack():
    h = Harness(auto_save=False)
    event = ai_pb2.GatewayEvent(instance_id='atomic', event_id=1,
        message_saved=ai_pb2.MessageSaved(room=h.snapshot([ALICE], [CLAUDE]),
            content='hello', sender=ai_pb2.Sender(member_id=10, user_id=1)))
    async def ack():
        state = await h.engine._load(1)
        assert len(state.pending) == 1
        assert await h.cache.exists('ai:event:atomic:1')
        assert await h.cache.sismember('ai:work', 1)
    await h.cache_message(10, 'alice', 'hello')
    await h.engine.handle(event, ack)
    await h.settle()


async def test_timer_is_rechecked_after_human_resets_it(monkeypatch):
    h = Harness()
    await h.human_says(ALICE, 'first', [ALICE], [CLAUDE]); await h.settle()
    h.clock.now += 61
    original = h.cache.zrangebyscore
    async def scan_then_human(*args, **kwargs):
        due = await original(*args, **kwargs)
        await h.human_says(ALICE, 'just spoke', [ALICE], [CLAUDE]); await h.settle()
        return due
    monkeypatch.setattr(h.cache, 'zrangebyscore', scan_then_human)
    await h.engine.fire_due_timers(); await h.settle()
    assert len(h.of('final')) == 2
    assert (await h.engine._load(1)).proactive_mask == 0


async def test_failed_proactive_reply_does_not_count_or_end_conversation():
    h = Harness()
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE]); await h.settle()
    h.keys.keys[(1, 'anthropic')] = 'fake-reject'
    for _ in range(3):
        h.clock.now += 61
        await h.engine.fire_due_timers(); await h.settle()
    state = await h.engine._load(1)
    assert state.proactive_mask == 0 and state.target == 'claude'
    assert len(h.of('final')) == 1 and len(h.of('failed')) == 3


async def test_completed_result_is_redelivered_without_generating_again():
    h = Harness(auto_save=False)
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE]); await h.settle()
    first = h.of('final')[0]
    chunks = len(h.of('chunk'))
    h.clock.now += 1
    await h.engine.recover_work(); await h.settle()
    assert len(h.of('final')) == 2
    assert h.of('final')[1] == first
    assert len(h.of('chunk')) == chunks
    await h.ai_saved(first, CLAUDE, [ALICE], [CLAUDE]); await h.settle()
    assert (await h.engine._load(1)).active is None
    await h.ai_saved(first, CLAUDE, [ALICE], [CLAUDE]); await h.settle()
    assert len(h.of('final')) == 2


async def test_interrupted_generation_is_reclaimed_by_another_engine():
    h = Harness(auto_save=False, generation_max_attempts=3)
    started = asyncio.Event()
    class InterruptedProvider:
        async def stream(self, **kwargs):
            yield 'old partial'
            started.set()
            await asyncio.Event().wait()
    h.engine.providers.create = lambda _: InterruptedProvider()
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE])
    await started.wait()
    first = (await h.engine._load(1)).active
    tasks = list(h.engine.tasks)
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert h.of('final') == []
    h.clock.now += 2
    recovered = Engine(h.engine.settings, h.cache, None, h.keys, h.engine.agents,
        ProviderFactory(2, fake=True), h.actions.append, random.Random(0), h.clock)
    await recovered.recover_work()
    await asyncio.gather(*list(recovered.tasks))
    assert h.of('final')[0].generation_id == first['id']
    assert [c for c in h.of('chunk') if c.attempt == 2][0].text == ''
    assert h.of('chunk')[-1].snapshot.endswith('hello')


async def test_transient_failure_retries_with_same_id_and_new_attempt():
    from chat_ai.providers.base import ProviderError
    h = Harness(generation_max_attempts=3)
    class FlakyProvider:
        attempts = 0
        async def stream(self, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                yield 'old partial'
                raise ProviderError('injected failure')
            yield 'recovered reply'
    provider = FlakyProvider()
    h.engine.providers.create = lambda _: provider
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE]); await h.settle()
    generation_id = h.of('chunk')[0].generation_id
    assert h.of('failed') == []
    h.clock.now += 1
    await h.engine.recover_work(); await h.settle()
    assert provider.attempts == 2
    assert h.of('final')[0].generation_id == generation_id
    assert h.of('final')[0].content == 'recovered reply'
    assert {c.attempt for c in h.of('chunk')} == {1, 2}


async def test_proactive_slot_counts_only_on_storage_confirmation():
    h = Harness()
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE]); await h.settle()
    h.auto_save = False
    h.clock.now += 61
    await h.engine.fire_due_timers(); await h.settle()
    final = h.of('final')[-1]
    assert (await h.engine._load(1)).proactive_mask == 0
    await h.ai_saved(final, CLAUDE, [ALICE], [CLAUDE]); await h.settle()
    assert (await h.engine._load(1)).proactive_mask == 1
    await h.ai_saved(final, CLAUDE, [ALICE], [CLAUDE]); await h.settle()
    assert (await h.engine._load(1)).proactive_mask == 1


async def test_started_proactive_reply_can_finish_after_human_message():
    h = Harness(auto_save=False)
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE]); await h.settle()
    await h.ai_saved(h.of('final')[0], CLAUDE, [ALICE], [CLAUDE]); await h.settle()
    h.clock.now += 61
    await h.engine.fire_due_timers()
    while not h.engine.tasks or (await h.engine._load(1)).active is None:
        await asyncio.sleep(.01)
    await h.human_says(ALICE, 'back', [ALICE], [CLAUDE])
    await h.settle()
    proactive = h.of('final')[-1]
    assert '有一阵子' in proactive.content
    await h.ai_saved(proactive, CLAUDE, [ALICE], [CLAUDE]); await h.settle()
    assert (await h.engine._load(1)).proactive_mask == 0
    assert h.of('final')[-1].content.endswith('back')


async def test_two_engines_replaying_one_event_generate_only_once():
    h = Harness(auto_save=False)
    other = Engine(h.engine.settings, h.cache, None, h.keys, h.engine.agents,
        ProviderFactory(2, fake=True), h.actions.append, random.Random(0), h.clock)
    await h.cache_message(10, 'alice', 'hello')
    event = ai_pb2.GatewayEvent(instance_id='shared', event_id=1,
        message_saved=ai_pb2.MessageSaved(room=h.snapshot([ALICE], [CLAUDE]),
            content='hello', sender=ai_pb2.Sender(member_id=10, user_id=1)))
    acknowledgments = []
    async def ack(): acknowledgments.append(1)
    await asyncio.gather(h.engine.handle(event, ack), other.handle(event, ack))
    await asyncio.gather(*list(h.engine.tasks), *list(other.tasks))
    assert len(acknowledgments) == 2
    assert len(h.of('final')) == 1


async def test_trigger_and_chain_use_the_triggering_users_actual_keys():
    from chat_ai.providers.fake import FakeProvider
    h = Harness(auto_save=False, chain_p=1.0, keys={
        (1, 'anthropic'): 'fake-alice', (1, 'openai'): 'fake-alice-openai',
        (2, 'anthropic'): 'fake-bob', (2, 'openai'): 'fake-bob-openai',
    })
    charged_keys = []
    class RecordingProvider(FakeProvider):
        async def stream(self, **kwargs):
            charged_keys.append(kwargs['api_key'])
            async for piece in super().stream(**kwargs):
                yield piece
    h.engine.providers.create = lambda provider: RecordingProvider(provider)
    await h.human_says(BOB, '@Claude hi', [BOB], [CLAUDE, GPT], name='bob')
    await h.settle()
    await h.ai_saved(h.of('final')[0], CLAUDE, [BOB], [CLAUDE, GPT]); await h.settle()
    assert charged_keys == ['fake-bob', 'fake-bob-openai']


async def test_queued_reply_targets_the_new_human_message_after_previous_final():
    from chat_ai.providers.fake import FakeProvider
    h = Harness(auto_save=False)
    seen = []
    class RecordingProvider(FakeProvider):
        async def stream(self, **kwargs):
            seen.append(kwargs['messages'])
            async for piece in super().stream(**kwargs):
                yield piece
    h.engine.providers.create = lambda provider: RecordingProvider(provider)
    await h.human_says(ALICE, 'first', [ALICE], [CLAUDE]); await h.settle()
    first = h.of('final')[0]
    await h.human_says(ALICE, 'new question', [ALICE], [CLAUDE]); await h.settle()
    await h.ai_saved(first, CLAUDE, [ALICE], [CLAUDE]); await h.settle()
    assert seen[-1][-1].role == 'user'
    assert 'new question' in seen[-1][-1].content
