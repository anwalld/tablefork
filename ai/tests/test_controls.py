"""Control fences must prevent recovery from resurrecting a stopped reply."""
import asyncio
import copy

from test_engine import Harness, ALICE, CLAUDE, GPT


from chat_ai.controls import Generation


class Controls:
    def __init__(self):
        self.paused = False
        self.cutoff = 0
        self.rows = {}

    async def room(self, room_id):
        return self.paused, self.cutoff

    async def generation(self, generation_id):
        row = self.rows.get(generation_id)
        return Generation(row['phase'], row['content'], row.get('outcome'),
                          row.get('attempt', 1), row.get('content_attempt', 0),
                          row.get('failure_code', '')) if row else None

    async def generations(self, generation_ids):
        return {generation_id: await self.generation(generation_id) for generation_id in generation_ids
                if generation_id in self.rows}

    async def reserve(self, room_id, job):
        if self.paused or (self.cutoff and job.get('trigger_seq', 0) <= self.cutoff):
            return False
        row = self.rows.setdefault(job['id'], dict(phase='generating', content='', attempt=job['attempt']))
        if row['phase'] == 'generating' and row['attempt'] < job['attempt']:
            row.update(content='', attempt=job['attempt'])
        return row['phase'] == 'generating'

    async def snapshot(self, job, content, final=False):
        row = self.rows[job['id']]
        if row['phase'] != 'generating' or row['attempt'] != job['attempt']:
            return False
        row.update(content=content, phase='ready' if final else 'generating',
                   outcome='completed' if final else None)
        if content.strip():
            row.update(last_nonempty_content=content, content_attempt=job['attempt'])
        return True

    async def failed(self, job, code='interrupted'):
        row = self.rows.get(job['id'])
        if row and row['phase'] == 'generating' and row['attempt'] == job['attempt']:
            row.update(phase='ready', outcome='failed', content=row.get('last_nonempty_content', ''), failure_code=code)
        return await self.generation(job['id'])

    async def cancelled(self, job):
        row = self.rows.get(job['id'])
        if row and row['phase'] != 'finished':
            row.update(phase='finished', outcome='cancelled', content='')

    def stop(self, generation_id):
        self.rows[generation_id].update(phase='ready', outcome='stopped')


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
    controls.rows[job['id']] = dict(phase='ready', content='preserved', outcome='stopped')
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
    assert controls.rows[final.generation_id]['phase'] == 'finished'
    assert controls.rows[final.generation_id]['content'] == ''
    assert (await h.engine._load(1)).active is None


async def test_failure_preserves_latest_nonempty_attempt_across_empty_retry():
    h = Harness(auto_save=False, generation_max_attempts=3, chunk_interval=60)
    controls = Controls()
    h.engine.controls = controls
    calls = 0

    class Failing:
        async def stream(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield 'first attempt'
            elif calls == 2:
                yield 'latest text'
            raise RuntimeError('provider offline')

    h.engine.providers.create = lambda _: Failing()
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE])
    await h.settle()
    for _ in range(2):
        h.clock.now += 5
        await h.engine.recover_work()
        await h.settle()
    final = h.of('final')[-1]
    assert final.attempt == 3 and final.content == 'latest text'
    durable = await controls.generation(final.generation_id)
    assert durable.phase == 'ready' and durable.outcome == 'failed'
    assert durable.content_attempt == 2
    count = calls
    h.clock.now += 5
    await h.engine.recover_work()
    await h.settle()
    assert calls == count
    assert h.of('final')[-1] == final
    assert len({item.generation_id for item in h.of('final')}) == 1


async def test_empty_failure_is_final_history_and_never_starts_chain():
    h = Harness(chain_p=1)
    controls = Controls()
    h.engine.controls = controls
    await h.human_says(ALICE, '#fail now', [ALICE], [CLAUDE, GPT])
    await h.settle()
    assert len(h.of('final')) == 1 and h.of('final')[0].content == ''
    state = await h.engine._load(1)
    assert state.active is None and not state.pending
    assert (await controls.generation(h.of('final')[0].generation_id)).outcome == 'failed'


async def test_stale_attempt_snapshot_cannot_replace_failure_candidate():
    controls = Controls()
    controls.rows['g'] = dict(phase='generating', content='', attempt=2)
    assert await controls.snapshot({'id':'g', 'attempt':2}, 'new text')
    assert not await controls.snapshot({'id':'g', 'attempt':1}, 'old text')
    result = await controls.failed({'id':'g', 'attempt':2})
    assert result.content == 'new text' and result.content_attempt == 2
    assert not await controls.snapshot({'id':'g', 'attempt':2}, 'late completed', final=True)
    assert (await controls.generation('g')).outcome == 'failed'


async def test_control_notification_wakes_long_fallback_and_old_revision_cannot_resume():
    h = Harness(control_poll_seconds=60, chunk_interval=0)
    controls = Controls()
    h.engine.controls = controls
    started = asyncio.Event()

    class Blocking:
        async def stream(self, **kwargs):
            yield 'partial'
            started.set()
            await asyncio.Event().wait()

    h.engine.providers.create = lambda _: Blocking()
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE])
    await asyncio.wait_for(started.wait(), 1)
    state = await h.engine._load(1)
    controls.stop(state.active['id'])
    from chat_ai.gen import ai_pb2
    for revision in (9, 2, 9):
        await h.send_event({'control_changed':ai_pb2.ControlChanged(room_id=1,revision=revision)})
    await asyncio.wait_for(h.settle(), 1)
    assert len(h.of('final')) == 1 and h.of('final')[0].content == 'partial'
    assert not h.engine.control_watchers
    assert h.engine.control_monitor_task is None


async def test_one_control_read_checks_many_active_generations():
    h = Harness(control_poll_seconds=60)
    controls = Controls()
    h.engine.controls = controls
    reads = []
    checked = asyncio.Event()
    original = controls.generations

    async def measured(ids):
        reads.append(set(ids))
        checked.set()
        return await original(ids)

    controls.generations = measured
    upstream = []
    for index in range(32):
        generation_id = str(index)
        task = asyncio.create_task(asyncio.Event().wait())
        upstream.append(task)
        controls.rows[generation_id] = dict(phase='generating', content='', attempt=1)
        h.engine.control_watchers[generation_id] = ({'id':generation_id,'attempt':1}, task)
    monitor = asyncio.create_task(h.engine._monitor_controls())
    await asyncio.wait_for(checked.wait(), 1)
    assert len(reads) == 1 and len(reads[0]) == 32
    monitor.cancel()
    await asyncio.gather(monitor,return_exceptions=True)
    for task in upstream:
        task.cancel()
    await asyncio.gather(*upstream,return_exceptions=True)
    h.engine.control_watchers.clear()


async def test_transient_control_read_failure_keeps_leased_job_recoverable():
    h = Harness(auto_save=False, generation_max_attempts=3, control_poll_seconds=60)
    controls = Controls()
    h.engine.controls = controls
    original = controls.generations
    reads = 0

    async def flaky(ids):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise OSError('temporary SQL outage')
        return await original(ids)

    controls.generations = flaky
    await h.human_says(ALICE, 'hello', [ALICE], [CLAUDE])
    await h.settle()
    state = await h.engine._load(1)
    assert state.active is not None and state.active['status'] == 'generating'
    generation_id = state.active['id']
    assert not h.of('final')
    h.clock.now += 5
    await h.engine.recover_work()
    await h.settle()
    assert len(h.of('final')) == 1
    assert h.of('final')[0].generation_id == generation_id
    assert h.of('final')[0].attempt == 2
