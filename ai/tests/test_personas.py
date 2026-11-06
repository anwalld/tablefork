import asyncio

from chat_ai import rules
from chat_ai.gen import ai_pb2
from chat_ai.providers.base import ProviderError
from test_engine import Harness, ALICE, BOB


def private_harness(**kwargs):
    h = Harness(**kwargs)
    original = h.snapshot
    personas = {'role_501': ('严谨', 1, 'A'*32), 'role_502': ('活泼', 1, 'B'*32)}
    def snapshot(humans, ais, room_id=1):
        value = original(humans, ais, room_id)
        for ai in value.ais:
            if ai.slug in personas:
                text, version, admission = personas[ai.slug]
                ai.model_slug = 'claude'
                ai.persona = text
                ai.persona_version = version
                ai.admission_id = admission
                ai.creator_name = 'creator'
        return value
    h.snapshot = snapshot
    return h, personas


A = (501, 'role_501', 'anthropic', 'Claude A')
B = (502, 'role_502', 'anthropic', 'Claude A')


async def test_same_model_same_name_explicit_id_only_selects_one_and_payer_stays_trigger():
    h, _ = private_harness()
    calls = []
    class Capture:
        async def stream(self, **values):
            calls.append(values)
            yield 'done'
    h.engine.providers.create = lambda _: Capture()
    await h.cache_message(BOB[0], 'bob', '@Claude A hello')
    await h.send_event({'message_saved': ai_pb2.MessageSaved(
        room=h.snapshot([ALICE, BOB], [A, B]), seq=h.seq,
        sender=ai_pb2.Sender(member_id=BOB[0], user_id=BOB[1], name='bob'),
        content='@Claude A hello', mention_ids=[502])})
    await h.settle()
    assert [f.ai_member_id for f in h.of('final')] == [502]
    assert calls[0]['api_key'] == 'fake-b'
    assert '活泼' in calls[0]['system'] and '严谨' not in calls[0]['system']


async def test_ambiguous_plain_text_does_not_trigger_either_role():
    h, _ = private_harness()
    await h.human_says(ALICE, '@Claude A hello', [ALICE, BOB], [A, B])
    await h.settle()
    assert not h.of('chunk') and not h.of('final')


def test_explicit_role_that_left_does_not_fall_back_to_same_named_survivor():
    survivor=rules.RoomAi(502,'role_502','anthropic','Claude A',model_slug='claude',is_legacy=False)
    state=rules.RoomState(humans=[rules.Human(*ALICE)],ais=[survivor])
    import random
    decision=rules.on_human_message(state,rules.Human(*ALICE),'@Claude A hello',{'anthropic'},random.Random(0),[501])
    assert not decision.requests and state.target is None


async def test_edit_during_generation_and_retry_keeps_old_persona():
    h, personas = private_harness(generation_max_attempts=3)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    class Retry:
        async def stream(self, **values):
            calls.append(values['system'])
            if len(calls) == 1:
                started.set()
                await release.wait()
                raise ProviderError('recoverable')
            yield 'done'
    h.engine.providers.create = lambda _: Retry()
    await h.human_says(ALICE, 'hello', [ALICE], [A])
    await asyncio.wait_for(started.wait(), 1)
    personas['role_501'] = ('新版人设', 2, 'A'*32)
    await h.send_event({'members_changed': ai_pb2.MembersChanged(room=h.snapshot([ALICE], [A]))})
    release.set()
    await h.settle()
    h.clock.now += 10
    await h.engine.recover_work()
    await h.settle()
    assert len(calls) == 2 and calls[0] == calls[1] and '严谨' in calls[1]
    await h.human_says(ALICE, 'next', [ALICE], [A])
    await h.settle()
    assert '新版人设' in calls[-1]


async def test_old_admission_pending_job_not_reused_after_rejoin():
    h, personas = private_harness(auto_save=False)
    await h.human_says(ALICE, 'hello', [ALICE], [A])
    await h.settle()
    state = await h.engine._load(1)
    async with h.engine._room(1) as current:
        old = dict(current.active)
        old['status'] = 'queued'
        current.active = None
        current.pending = [old]
    personas['role_501'] = ('new', 1, 'C'*32)
    await h.send_event({'members_changed': ai_pb2.MembersChanged(room=h.snapshot([ALICE], [A]))})
    await h.settle()
    assert not (await h.engine._load(1)).pending
    assert state.ais[0].admission_id == 'A'*32


def test_old_serialized_room_state_remains_readable():
    old = {'ais': [[100, 'claude', 'anthropic', 'Claude']], 'target': 'claude'}
    restored = rules.RoomState.from_json(old)
    assert restored.ais[0].is_legacy and restored.target == 'claude'


async def test_legacy_state_without_admission_can_resume_after_sql_backfill():
    from test_controls import Controls
    from test_engine import CLAUDE
    class BackfilledControls(Controls):
        async def reserve(self, room, job):
            accepted = await super().reserve(room,job)
            job['admission_id']='D'*32
            return accepted
    h=Harness()
    h.engine.controls=BackfilledControls()
    await h.human_says(ALICE,'legacy resume',[ALICE],[CLAUDE])
    await h.settle()
    assert len(h.of('final'))==1 and h.of('final')[0].content=='Claude: alice：legacy resume'
