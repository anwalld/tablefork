"""Control fences must prevent recovery from resurrecting a stopped reply."""
import asyncio
import copy

from test_engine import Harness, ALICE, CLAUDE, GPT


class Controls:
    def __init__(self):
        self.paused = False
        self.cutoff = 0
        self.rows = {}

    async def room(self, room_id):
        return self.paused, self.cutoff

    async def generation(self, generation_id):
        row = self.rows.get(generation_id)
        return (row['status'], row['content'], row['stopped']) if row else None

    async def reserve(self, room_id, job):
        if self.paused or (self.cutoff and job.get('trigger_seq', 0) <= self.cutoff):
            return False
        row = self.rows.setdefault(job['id'], dict(status='generating', content='', stopped=False))
        return row['status'] == 'generating'

    async def snapshot(self, job, content, final=False):
        row = self.rows[job['id']]
        if row['status'] != 'generating':
            return False
        row.update(content=content, status='final' if final else 'generating')
        return True

    async def failed(self, job):
        row = self.rows.get(job['id'])
        if row and row['status'] in ('generating', 'final'):
            row.update(status='saved', content='')

    def stop(self, generation_id):
        self.rows[generation_id].update(status='stopped', stopped=True)


async def test_manual_stop_keeps_partial_and_never_continues_chain():
    h = Harness(chain_p=1, chunk_interval=0)
    controls = Controls()
    h.engine.controls = controls
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Blocking:
        async def stream(self, **kwargs):
            try:
                yield 'keep this'
                started.set()
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    h.engine.providers.create = lambda _: Blocking()
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE, GPT])
    await asyncio.wait_for(started.wait(), 1)
    state = await h.engine._load(1)
    controls.stop(state.active['id'])
    await asyncio.wait_for(h.settle(), 2)
    assert cancelled.is_set()
    finals = h.of('final')
    assert len(finals) == 1 and finals[0].content == 'keep this'
    assert h.saved_ids == {state.active['id']}
    await h.engine.recover_work()
    await h.settle()
    assert len(h.of('final')) == 1
    assert not (await h.engine._load(1)).pending


async def test_pause_ignores_old_events_and_recovers_only_new_trigger():
    h = Harness()
    controls = Controls()
    h.engine.controls = controls
    controls.paused = True
    await h.human_says(ALICE, 'during pause', [ALICE], [CLAUDE])
    await h.settle()
    assert not h.of('chunk') and not h.of('final')
    controls.paused = False
    controls.cutoff = h.seq
    await h.engine.recover_work()
    await h.engine.fire_due_timers()
    await h.settle()
    assert not h.of('final')
    await h.human_says(ALICE, 'after resume', [ALICE], [CLAUDE])
    await h.settle()
    assert len(h.of('final')) == 1


async def test_expired_worker_recovers_stopped_snapshot_without_provider():
    h = Harness(auto_save=False)
    controls = Controls()
    h.engine.controls = controls
    # Seed a leased job as if a process died after the SQL stop committed.
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE])
    await h.settle()
    state = await h.engine._load(1)
    job = copy.deepcopy(state.active)
    job.update(status='generating', lease_until=0, owner='old-worker')
    controls.rows[job['id']] = dict(status='stopped', content='preserved', stopped=True)
    async with h.engine._room(1) as current:
        current.active = job
    h.clock.now += 10
    before = len(h.of('chunk'))
    await h.engine.recover_work()
    await h.settle()
    assert len(h.of('chunk')) == before
    assert h.of('final')[-1].content == 'preserved'


async def test_saved_confirmation_while_paused_clears_final_without_chain():
    h = Harness(auto_save=False, chain_p=1)
    controls = Controls()
    h.engine.controls = controls
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE, GPT])
    await h.settle()
    final = h.of('final')[0]
    controls.paused = True
    controls.cutoff = h.seq
    await h.ai_saved(final, CLAUDE, [ALICE], [CLAUDE, GPT])
    await h.settle()
    state = await h.engine._load(1)
    assert state.active is None and not state.pending


async def test_rejected_final_clears_persistent_generation_state():
    h = Harness(auto_save=False)
    controls = Controls()
    h.engine.controls = controls
    await h.human_says(ALICE, '@claude hello', [ALICE], [CLAUDE])
    await h.settle()
    final = h.of('final')[0]
    from chat_ai.gen import ai_pb2
    await h.send_event({'generation_rejected': ai_pb2.GenerationRejected(
        room_id=1, generation_id=final.generation_id)})
    assert controls.rows[final.generation_id]['status'] == 'saved'
    assert controls.rows[final.generation_id]['content'] == ''
    assert (await h.engine._load(1)).active is None
