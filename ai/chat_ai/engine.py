"""Persist room decisions before acknowledging events; recover unfinished replies."""

import asyncio
import contextlib
import copy
import json
import logging
import random
import time
import uuid
from typing import Awaitable, Callable

from . import rules
from .context import recent_messages, to_chat
from .gen import ai_pb2
from .providers.base import ChatMessage, KeyRejected

log = logging.getLogger(__name__)
PROVIDER_NAMES = {"deepseek": "DeepSeek", "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini"}
MAX_CONTENT = 4000
PROACTIVE_PROMPT = "（对方有一阵子没说话了。请你自然地主动说点什么，比如接着刚才的话题，或者问候一下。）"
GOODBYE_PROMPT = "（对方很久没有回复了。请简短、自然地道个别，说下次再聊。）"

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
# A room decision, its queue, timer and event receipt become visible together.
_COMMIT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', 2592000)
if ARGV[3] == '1' then redis.call('SADD', KEYS[3], ARGV[4])
else redis.call('SREM', KEYS[3], ARGV[4]) end
if ARGV[5] ~= '' then redis.call('ZADD', KEYS[4], ARGV[5], ARGV[4])
else redis.call('ZREM', KEYS[4], ARGV[4]) end
if ARGV[6] == '1' then redis.call('SET', KEYS[5], 'done', 'EX', 86400) end
return 1
"""


class Engine:
    def __init__(self, settings, cache, pool, keys, agents, providers, send,
                 rng=None, clock=time.time):
        self.settings = settings
        self.cache = cache
        self.pool = pool
        self.keys = keys
        self.agents = agents
        self.providers = providers
        self.send = send
        self.rng = rng or random.Random()
        self.clock = clock
        self.tasks: set[asyncio.Task] = set()
        self.running_rooms: set[int] = set()

    async def handle(self, event, ack: Callable[[], Awaitable[None]]) -> None:
        seen = f"ai:event:{event.instance_id}:{event.event_id}"
        if await self.cache.exists(seen):
            await ack()
            return
        body = event.WhichOneof('body')
        value = getattr(event, body) if body else None
        if body in ('message_saved', 'members_changed'):
            room_id = value.room.room_id
        elif body in ('room_deleted', 'generation_rejected'):
            room_id = value.room_id
        else:
            await ack()
            return

        # This can fail. No receipt has been recorded, so resending is safe.
        available = set()
        if body == 'message_saved':
            if value.sender.is_ai:
                state = await self._load(room_id)
                job = state.active
                if job and job['id'] == value.generation_id:
                    available = await self.keys.providers(job['payer'][1])
            else:
                available = await self.keys.providers(value.sender.user_id)

        decision = rules.Decision()
        async with self._room(room_id, seen) as state:
            if state is not None and not state.deleted:
                if body == 'room_deleted':
                    state.deleted = True
                    state.pending.clear()
                    state.active = None
                    state.timer_due = None
                    state.target = None
                elif body == 'generation_rejected':
                    if state.active and state.active['id'] == value.generation_id:
                        self._finish_failed(state, state.active)
                elif body == 'members_changed':
                    _apply_snapshot(state, value.room)
                elif not value.sender.is_ai:
                    _apply_snapshot(state, value.room)
                    payer = rules.Human(value.sender.member_id, value.sender.user_id)
                    decision = rules.on_human_message(state, payer, value.content, available, self.rng)
                    state.timer_due = self._next_timer(state)
                    self._enqueue(state, decision, {
                        'name': value.sender.name or '用户', 'content': value.content,
                    })
                else:
                    _apply_snapshot(state, value.room)
                    job = state.active
                    if job and job['id'] == value.generation_id:
                        state.active = None
                        if job['epoch'] == state.epoch:
                            if job['kind'] in ('proactive', 'goodbye'):
                                rules.on_proactive_saved(state, job['epoch'], job['slot'])
                                state.timer_due = self._next_timer(state)
                            payer = rules.Human(*job['payer'])
                            decision = rules.on_ai_message(state, value.sender.slug, value.content,
                                payer, job['hop'], available, self.settings.chain_p, self.rng)
                            self._enqueue(state, decision)
        # The state and queue have committed, including for duplicate delivery.
        await ack()
        self._notices(room_id, decision)
        await self._drain(room_id)

    def _next_timer(self, state):
        if len(state.humans) == 1 and state.target is not None:
            return self.clock() + self.settings.idle_seconds
        return None

    def _enqueue(self, state, decision, trigger=None):
        for request in decision.requests:
            state.pending.append({
                'id': uuid.uuid4().hex, 'slug': request.slug,
                'payer': [request.payer.member_id, request.payer.user_id],
                'kind': request.kind, 'hop': request.hop, 'epoch': state.epoch,
                'slot': state.proactive_mask.bit_count(), 'attempt': 0,
                'member_id': state.ai(request.slug).member_id, 'trigger': trigger,
                'status': 'queued', 'owner': '', 'lease_until': 0, 'retry_at': 0,
            })

    async def fire_due_timers(self):
        due = await self.cache.zrangebyscore('ai:timers', '-inf', self.clock())
        for raw in due:
            room_id = int(raw)
            # Fetching keys may take time. Recheck the timer under the same
            # room lock used by human messages before accepting this firing.
            state = await self._load(room_id)
            human = state.humans[0] if len(state.humans) == 1 else None
            available = await self.keys.providers(human.user_id) if human else set()
            decision = rules.Decision()
            async with self._room(room_id) as state:
                if state.deleted or state.timer_due is None or state.timer_due > self.clock():
                    continue
                if human is None or state.humans != [human]:
                    continue
                decision, again = rules.on_idle_timer(state, available, self.settings.proactive_p1, self.rng)
                self._enqueue(state, decision)
                # Wait for successful storage before scheduling the next slot.
                state.timer_due = None if decision.requests or not again else self._next_timer(state)
            self._notices(room_id, decision)
            await self._drain(room_id)

    async def recover_work(self):
        for raw in await self.cache.smembers('ai:work'):
            await self._drain(int(raw))

    async def run_timers(self):
        while True:
            try:
                await self.recover_work()
                await self.fire_due_timers()
            except Exception:
                log.exception('AI background work')
            await asyncio.sleep(self.settings.timer_poll_seconds)

    async def _drain(self, room_id):
        if room_id in self.running_rooms:
            return
        self.running_rooms.add(room_id)
        task = asyncio.create_task(self._run_room(room_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _claim(self, room_id):
        job = None
        exhausted = None
        async with self._room(room_id) as state:
            if state.deleted:
                return None
            if state.active is None:
                # Messages queued before generation starts share one reply
                # per AI. The latest trigger supplies the payer and epoch.
                merged = {}
                for item in state.pending:
                    if item['kind'] in ('proactive', 'goodbye') and item['epoch'] != state.epoch:
                        continue
                    merged[item['slug']] = item
                pending = list(merged.values())
                state.active = pending.pop(0) if pending else None
                state.pending = pending
            active = state.active
            if active is None or active.get('retry_at', 0) > self.clock():
                return None
            if active['status'] == 'final':
                active['retry_at'] = self.clock() + self.settings.retry_base_seconds
                job = copy.deepcopy(active)
            elif active['lease_until'] <= self.clock():
                if active['attempt'] >= self.settings.generation_max_attempts:
                    exhausted = copy.deepcopy(active)
                    self._finish_failed(state, active)
                else:
                    # The claim is the start boundary. A human message that
                    # arrives later does not cancel this already-started reply.
                    active['attempt'] += 1
                    active['status'] = 'generating'
                    active['owner'] = uuid.uuid4().hex
                    active['lease_until'] = self.clock() + self.settings.work_lease_seconds
                    job = copy.deepcopy(active)
        if exhausted:
            self._failed(room_id, exhausted, 'interrupted', 'AI 回复多次中断，请稍后重试')
        return job

    async def _run_room(self, room_id):
        try:
            job = await self._claim(room_id)
            if job is None:
                return
            if job['status'] == 'final':
                self._send_final(room_id, job)
                return
            heartbeat = asyncio.create_task(self._heartbeat(room_id, job))
            try:
                try:
                    # Include key lookup and context loading in the deadline.
                    async with asyncio.timeout(self.settings.generation_timeout):
                        await self._generate(room_id, job)
                except TimeoutError:
                    await self._fail_job(room_id, job, 'timeout', 'AI 回复超时', True)
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
        except asyncio.CancelledError:
            # The durable job stays leased and is reclaimed after expiry.
            raise
        except Exception:
            log.exception('generation in room %s', room_id)
        finally:
            self.running_rooms.discard(room_id)

    @staticmethod
    def _owns(state, job):
        active = state.active
        return active is not None and active['id'] == job['id'] and active['owner'] == job['owner']

    async def _heartbeat(self, room_id, job):
        while True:
            await asyncio.sleep(self.settings.work_lease_seconds / 3)
            async with self._room(room_id) as state:
                if not self._owns(state, job):
                    return
                state.active['lease_until'] = self.clock() + self.settings.work_lease_seconds

    async def _generate(self, room_id, job):
        state = await self._load(room_id)
        ai = state.ai(job['slug'])
        agent = self.agents.get(job['slug'])
        if ai is None or agent is None:
            await self._fail_job(room_id, job, 'ai_removed', 'AI 已离开房间', False)
            return
        key = await self.keys.get(job['payer'][1], ai.provider)
        if key is None:
            await self._fail_job(room_id, job, 'api_key_required',
                                 f'请先填写 {PROVIDER_NAMES[ai.provider]} 的 API key', False)
            return
        entries = await recent_messages(self.cache, self.pool, room_id, self.settings.context_messages)
        messages = to_chat(entries, ai.member_id, self.settings.context_messages, self.settings.context_chars)
        if job['kind'] in ('proactive', 'goodbye'):
            prompt = GOODBYE_PROMPT if job['kind'] == 'goodbye' else PROACTIVE_PROMPT
            messages.append(ChatMessage('user', prompt))
        if not messages:
            messages.append(ChatMessage('user', '（聊天开始）'))
        elif job['kind'] == 'reply' and messages[-1].role == 'assistant' and job.get('trigger'):
            trigger = job['trigger']
            messages.append(ChatMessage('user', f"请回复之前的新消息：{trigger['name']}：{trigger['content']}"))

        parts = []
        buffered = []
        sequence = 0
        last_flush = time.monotonic()
        def flush():
            nonlocal sequence, last_flush
            sequence += 1
            # Snapshots let a reconnect or a new attempt replace partial text.
            delta = ''.join(buffered)
            buffered.clear()
            self.send(ai_pb2.AiAction(chunk=ai_pb2.Chunk(
                room_id=room_id, generation_id=job['id'], ai_member_id=ai.member_id,
                text=delta, snapshot=''.join(parts)[:MAX_CONTENT], attempt=job['attempt'],
                sequence=sequence, cumulative=True)))
            last_flush = time.monotonic()
        flush()  # clears the previous attempt even before the first token
        try:
            provider = self.providers.create(ai.provider)
            async with asyncio.timeout(self.settings.generation_timeout):
                async for piece in provider.stream(model=agent.model, system=agent.persona,
                        messages=messages, api_key=key, max_tokens=self.settings.max_output_tokens):
                    parts.append(piece)
                    buffered.append(piece)
                    if time.monotonic() - last_flush >= self.settings.chunk_interval:
                        flush()
            flush()
        except KeyRejected:
            await self._fail_job(room_id, job, 'api_key_invalid',
                                 f'{PROVIDER_NAMES[ai.provider]} 拒绝了你的 API key，请在设置里更新', False)
            return
        except TimeoutError:
            await self._fail_job(room_id, job, 'timeout', f'{ai.name} 回复超时', True)
            return
        except Exception as error:
            log.warning('generation by %s failed: %s', ai.slug, type(error).__name__)
            await self._fail_job(room_id, job, 'provider_error', f'{ai.name} 暂时无法回复', True)
            return
        content = ''.join(parts).strip()[:MAX_CONTENT]
        if not content:
            await self._fail_job(room_id, job, 'empty_reply', f'{ai.name} 没有给出回复', True)
            return
        async with self._room(room_id) as state:
            if not self._owns(state, job):
                return
            state.active.update(status='final', content=content, member_id=ai.member_id,
                                retry_at=self.clock() + self.settings.retry_base_seconds)
            final = copy.deepcopy(state.active)
        self._send_final(room_id, final)

    def _send_final(self, room_id, job):
        self.send(ai_pb2.AiAction(final=ai_pb2.Final(room_id=room_id,
            generation_id=job['id'], ai_member_id=job['member_id'], content=job['content'])))

    def _finish_failed(self, state, job):
        state.active = None
        if job['kind'] in ('proactive', 'goodbye') and job['epoch'] == state.epoch:
            state.timer_due = self._next_timer(state)

    async def _fail_job(self, room_id, job, code, message, retryable):
        terminal = False
        async with self._room(room_id) as state:
            if not self._owns(state, job):
                return
            if retryable and job['attempt'] < self.settings.generation_max_attempts:
                state.active.update(status='queued', owner='', lease_until=0,
                    retry_at=self.clock() + self.settings.retry_base_seconds * 2 ** (job['attempt'] - 1))
            else:
                self._finish_failed(state, job)
                terminal = True
        if terminal:
            self._failed(room_id, job, code, message)

    def _notices(self, room_id, decision):
        for missing in decision.missing:
            self._notice(room_id, missing.payer, missing.provider)

    def _notice(self, room_id, payer, provider):
        text = (f'你还没有填写 {PROVIDER_NAMES[provider]} 的 API key，请先在设置里填写'
                if provider else '房间里没有你能使用的 AI，请先在设置里填写对应的 API key')
        self.send(ai_pb2.AiAction(notice=ai_pb2.Notice(room_id=room_id,
            notify_member_id=payer.member_id, code='api_key_required', provider=provider, message=text)))

    def _failed(self, room_id, job, code, message):
        self.send(ai_pb2.AiAction(failed=ai_pb2.Failed(room_id=room_id,
            generation_id=job['id'], ai_member_id=job.get('member_id', 0),
            notify_member_id=job['payer'][0], code=code, message=message)))

    async def _load(self, room_id):
        raw = await self.cache.get(f'ai:room:{room_id}')
        data = json.loads(raw) if raw else {}
        state = rules.RoomState.from_json(data)
        if raw and 'timer_due' not in data:
            state.timer_due = await self.cache.zscore('ai:timers', room_id)
        return state

    @contextlib.asynccontextmanager
    async def _room(self, room_id, seen=None):
        lock = f'ai:lock:{room_id}'
        token = uuid.uuid4().hex
        deadline = time.monotonic() + 5
        while not await self.cache.set(lock, token, nx=True, px=5000):
            if time.monotonic() > deadline:
                raise TimeoutError(f'room {room_id} state is locked')
            await asyncio.sleep(0.01)
        try:
            if seen and await self.cache.exists(seen):
                yield None
                return
            state = await self._load(room_id)
            yield state
            encoded = json.dumps(state.to_json())
            timer = '' if state.timer_due is None else str(state.timer_due)
            committed = await self.cache.eval(_COMMIT, 5, lock, f'ai:room:{room_id}',
                'ai:work', 'ai:timers', seen or lock, token, encoded,
                '1' if state.active or state.pending else '0', str(room_id), timer,
                '1' if seen else '0')
            if not committed:
                raise TimeoutError('room lease expired before commit')
        finally:
            await self.cache.eval(_RELEASE, 1, lock, token)


def _apply_snapshot(state, snapshot):
    state.humans = [rules.Human(h.member_id, h.user_id) for h in snapshot.humans]
    state.ais = [rules.RoomAi(a.member_id, a.slug, a.provider, a.name) for a in snapshot.ais]
    if state.target is not None and (len(state.humans) != 1 or state.ai(state.target) is None):
        state.target = None
        state.timer_due = None
