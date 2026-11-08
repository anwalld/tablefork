"""Persist room decisions before acknowledging events; recover unfinished replies."""

import asyncio
import contextlib
import copy
import json
import hashlib
import re
import logging
import random
import time
import uuid
from typing import Awaitable, Callable

from . import rules
from . import host
from .controls import GenerationControls
from .context import build_system_prompt, estimate_tokens, recent_messages, related_messages, related_note, to_chat
from .gen import ai_pb2
from .providers.base import ChatMessage, KeyRejected, ProviderError

log = logging.getLogger(__name__)
PROVIDER_NAMES = {"deepseek": "DeepSeek", "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini"}
MAX_CONTENT = 4000
PROACTIVE_PROMPT = "（对方有一阵子没说话了。请你自然地主动说点什么，比如接着刚才的话题，或者问候一下。）"
GOODBYE_PROMPT = "（对方很久没有回复了。请简短、自然地道个别，说下次再聊。）"
MAX_RETRY_AFTER = 60


def strip_self_name(text: str, name: str) -> str:
    """Removes a leading "<own name>:" that models copy from the history format."""
    stripped = text.lstrip()
    for colon in ('：', ':'):
        if name and stripped.startswith(name + colon):
            return stripped[len(name) + 1:].lstrip()
    return text


def _maybe_own_name(text: str, name: str) -> bool:
    """True while the reply so far could still turn into "<own name>:"."""
    stripped = text.lstrip()
    return bool(name) and any((name + colon).startswith(stripped) for colon in ('：', ':'))

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
        self.controls = GenerationControls(pool) if pool is not None else None
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
        self.control_watchers = {}
        self.control_wakeup = asyncio.Event()
        self.control_monitor_task = None

    async def handle(self, event, ack: Callable[[], Awaitable[None]]) -> None:
        seen = f"ai:event:{event.instance_id}:{event.event_id}"
        if await self.cache.exists(seen):
            await ack()
            return
        body = event.WhichOneof('body')
        value = getattr(event, body) if body else None
        if body in ('message_saved', 'members_changed'):
            room_id = value.room.room_id
        elif body in ('room_deleted', 'generation_rejected', 'control_changed'):
            room_id = value.room_id
        else:
            await ack()
            return

        if body == 'control_changed':
            self.sync_controls()
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

        paused, cutoff = await self.controls.room(room_id) if self.controls else (False, 0)
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
                        if self.controls:
                            await self.controls.cancelled(state.active)
                        self._finish_failed(state, state.active)
                elif body == 'members_changed':
                    _apply_snapshot(state, value.room)
                elif body == 'message_saved' and not value.sender.is_ai and (paused or (cutoff and value.seq <= cutoff)):
                    state.pending.clear()
                    state.timer_due = None
                    state.target = None
                elif not value.sender.is_ai:
                    state.last_trigger_seq = value.seq
                    raw_trace = f"{room_id}:{value.client_msg_id or value.seq}"
                    state.last_trigger_id = (raw_trace if re.fullmatch(r'[a-zA-Z0-9:_-]{1,128}', raw_trace)
                                             else hashlib.sha256(raw_trace.encode()).hexdigest())
                    _apply_snapshot(state, value.room)
                    payer = rules.Human(value.sender.member_id, value.sender.user_id)
                    if state.collaboration_enabled:
                        decision = await self._host_round(state, room_id, payer, value.content, available, value.mention_ids)
                    else:
                        decision = rules.on_human_message(state, payer, value.content, available, self.rng, value.mention_ids)
                    state.timer_due = self._next_timer(state)
                    self._enqueue(state, decision, {
                        'name': value.sender.name or '用户', 'content': value.content,
                    })
                else:
                    _apply_snapshot(state, value.room)
                    job = state.active
                    if job and job['id'] == value.generation_id:
                        state.active = None
                        if value.generation_outcome == 'failed' or job.get('outcome') == 'failed':
                            if job['kind'] in ('proactive', 'goodbye') and job['epoch'] == state.epoch:
                                state.timer_due = self._next_timer(state)
                        if (job['epoch'] == state.epoch and not paused and
                            (not cutoff or job.get('trigger_seq', 0) > cutoff) and
                            not value.stopped and not job.get('stopped') and
                            value.generation_outcome != 'failed' and job.get('outcome') != 'failed'):
                            if job['kind'] in ('proactive', 'goodbye'):
                                rules.on_proactive_saved(state, job['epoch'], job['slot'])
                                state.timer_due = self._next_timer(state)
                            payer = rules.Human(*job['payer'])
                            if state.collaboration_enabled:
                                speakers, end = await self._host_pick(room_id, state, payer.user_id)
                                if speakers is not None:
                                    decision = rules.on_host_decision(
                                        state, speakers, end, payer, available, [],
                                        self.settings.round_max_replies, kind="chain", hop=job['hop'] + 1)
                                else:
                                    decision = rules.on_ai_message(state, value.sender.slug, value.content,
                                        payer, job['hop'], available, self.settings.chain_p, self.rng)
                            else:
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
                'member_id': state.ai(request.slug).member_id, 'admission_id':state.ai(request.slug).admission_id, 'trigger': trigger,
                'trigger_seq': state.last_trigger_seq, 'trigger_id': state.last_trigger_id,
                'status': 'queued', 'owner': '', 'lease_until': 0, 'retry_at': 0,
            })

    async def fire_due_timers(self):
        due = await self.cache.zrangebyscore('ai:timers', '-inf', self.clock())
        for raw in due:
            room_id = int(raw)
            # Fetching keys may take time. Recheck the timer under the same
            # room lock used by human messages before accepting this firing.
            state = await self._load(room_id)
            if self.controls:
                paused, cutoff = await self.controls.room(room_id)
                if paused or (cutoff and state.last_trigger_seq <= cutoff):
                    async with self._room(room_id) as current:
                        current.timer_due = None
                        current.target = None
                        current.pending.clear()
                    continue
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
        paused, cutoff = await self.controls.room(room_id) if self.controls else (False, 0)
        durable_id = None
        durable = None
        if self.controls:
            observed = await self._load(room_id)
            if observed.active:
                durable_id = observed.active['id']
                durable = await self.controls.generation(durable_id)
        async with self._room(room_id) as state:
            if state.deleted:
                return None
            if paused or cutoff:
                state.pending = [item for item in state.pending if not paused and item.get('trigger_seq', 0) > cutoff]
                if state.active and state.active['status'] != 'final' and (paused or state.active.get('trigger_seq', 0) <= cutoff):
                    # A running owner will observe the SQL stopped snapshot.
                    # Keep it until then, or recover it after its lease expires.
                    if state.active.get('lease_until', 0) > self.clock():
                        return None
                if paused:
                    state.timer_due = None
                    state.target = None
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
            if active and active['id'] == durable_id and durable:
                active['attempt'] = max(active['attempt'], durable.attempt)
            if active and active['id'] == durable_id and durable and durable.phase != 'generating':
                if durable.outcome in ('completed', 'failed') or (durable.stopped and durable.content.strip()):
                    active.update(status='final', content=durable.content, stopped=durable.stopped,
                                  outcome=durable.outcome, attempt=durable.attempt)
                else:
                    state.active = None
                    return None
            if active is None or active.get('retry_at', 0) > self.clock():
                return None
            if active['status'] == 'final':
                active['retry_at'] = self.clock() + self.settings.retry_base_seconds
                job = copy.deepcopy(active)
            elif active['lease_until'] <= self.clock():
                if active['attempt'] >= self.settings.generation_max_attempts:
                    exhausted = copy.deepcopy(active)
                    if self.controls:
                        durable = await self.controls.failed(active)
                        if durable and durable.outcome in ('completed', 'failed', 'stopped'):
                            active.update(status='final', content=durable.content, outcome=durable.outcome, stopped=durable.stopped)
                            job = copy.deepcopy(active)
                        else:
                            self._finish_failed(state, active)
                    else:
                        active.update(status='final', content=active.get('last_nonempty_content', ''), outcome='failed')
                        job = copy.deepcopy(active)
                else:
                    # The claim is the start boundary. A human message that
                    # arrives later does not cancel this already-started reply.
                    if 'persona_snapshot' not in active:
                        character = state.ai(active['slug'])
                        if character:
                            model = self.agents.get(character.model_slug or character.slug)
                            active['persona_snapshot'] = model.persona if character.is_legacy and model else character.persona
                            active['persona_version'] = character.persona_version or 1
                    active['attempt'] += 1
                    active['status'] = 'generating'
                    active['owner'] = uuid.uuid4().hex
                    active['lease_until'] = self.clock() + self.settings.work_lease_seconds
                    job = copy.deepcopy(active)
        if exhausted:
            self._failure_notice(room_id, exhausted, 'interrupted', 'AI 回复多次中断，请稍后重试')
        return job

    async def _run_room(self, room_id):
        started = time.monotonic()
        job = None
        result = 'idle'
        try:
            job = await self._claim(room_id)
            if job is None:
                return
            result = 'processed'
            if job['status'] == 'final':
                self._send_final(room_id, job)
                return
            if self.controls and not await self.controls.reserve(room_id, job):
                result = 'controlled'
                await self._controlled_finish(room_id, job)
                return
            if self.controls:
                async with self._room(room_id) as state:
                    if not self._owns(state, job):
                        return
                    for field in ('admission_id', 'persona_version', 'persona_snapshot'):
                        if field in job:
                            state.active[field] = job[field]
            generation = asyncio.create_task(self._generate(room_id, job))
            watcher = asyncio.create_task(self._watch_control(room_id, job, generation)) if self.controls else None
            heartbeat = asyncio.create_task(self._heartbeat(room_id, job))
            try:
                try:
                    # Include key lookup and context loading in the deadline.
                    async with asyncio.timeout(self.settings.generation_timeout):
                        await generation
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    result = 'stopped'
                    await self._controlled_finish(room_id, job)
                except TimeoutError:
                    result = 'timeout'
                    await self._fail_job(room_id, job, 'timeout', 'AI 回复超时', True)
            finally:
                if watcher:
                    watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher
                generation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await generation
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
        except asyncio.CancelledError:
            result = 'interrupted'
            # The durable job stays leased and is reclaimed after expiry.
            raise
        except Exception:
            result = 'error'
            log.exception('generation in room %s', room_id)
        finally:
            log.info(json.dumps({'event': 'request_stage', 'instance': 'ai',
                'operation': 'ai.generate', 'stage': 'ai', 'result': result, 'room_id': room_id,
                'generation_id': job['id'] if job else '', 'request_id': job.get('trigger_id', '') if job else '',
                'attempt': job['attempt'] if job else 0,
                'elapsed_ms': (time.monotonic() - started) * 1000}))
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
        agent = self.agents.get(ai.model_slug or ai.slug) if ai else None
        if ai is None or agent is None or (not ai.is_legacy and ai.admission_id != job.get('admission_id', ai.admission_id)):
            await self._fail_job(room_id, job, 'ai_removed', 'AI 已离开房间', False)
            return
        key = await self.keys.get(job['payer'][1], ai.provider)
        if key is None:
            await self._fail_job(room_id, job, 'api_key_required',
                                 f'请先填写 {PROVIDER_NAMES[ai.provider]} 的 API key', False)
            return
        messages, related = await self._context(room_id, job, ai.member_id)
        if 'persona_snapshot' not in job:
            job['persona_snapshot'] = agent.persona if ai.is_legacy else ai.persona
        system = build_system_prompt(ai, job['persona_snapshot'],
                                     collaboration=state.collaboration_enabled,
                                     room_name=state.room_name,
                                     humans=state.humans, ais=state.ais)
        if job['kind'] in ('proactive', 'goodbye'):
            prompt = GOODBYE_PROMPT if job['kind'] == 'goodbye' else PROACTIVE_PROMPT
            messages.append(ChatMessage('user', prompt))
        if not messages:
            messages.append(ChatMessage('user', '（聊天开始）'))
        elif job['kind'] == 'reply' and messages[-1].role == 'assistant' and job.get('trigger'):
            trigger = job['trigger']
            messages.append(ChatMessage('user', f"请回复之前的新消息：{trigger['name']}：{trigger['content']}"))
        if related is not None:
            # Placed just before the newest turn, so the cached prefix stays the same.
            messages.insert(len(messages) - 1, related)
        strip = self.settings.strip_self_name

        def visible(text):
            if not strip:
                return text
            if _maybe_own_name(text, ai.name):
                return ''
            return strip_self_name(text, ai.name)

        shown = ''
        parts = []
        buffered = []
        sequence = 0
        last_flush = time.monotonic()
        async def flush():
            nonlocal sequence, last_flush, shown
            sequence += 1
            # Snapshots let a reconnect or a new attempt replace partial text.
            snapshot = visible(''.join(parts))[:MAX_CONTENT]
            if self.controls:
                saved = await self.controls.snapshot(job, snapshot)
                if not saved:
                    raise asyncio.CancelledError
            elif snapshot.strip():
                async with self._room(room_id) as current:
                    if not self._owns(current, job):
                        raise asyncio.CancelledError
                    current.active['last_nonempty_content'] = snapshot
            delta = ''.join(buffered)
            if strip:
                delta = snapshot[len(shown):] if snapshot.startswith(shown) else snapshot
                shown = snapshot
            buffered.clear()
            self.send(ai_pb2.AiAction(chunk=ai_pb2.Chunk(
                room_id=room_id, generation_id=job['id'], ai_member_id=ai.member_id,
                text=delta, snapshot=snapshot, attempt=job['attempt'],
                sequence=sequence, cumulative=True, admission_id=job.get("admission_id", ""))))
            last_flush = time.monotonic()
        await flush()  # clears the previous attempt even before the first token
        try:
            provider = self.providers.create(ai.provider)
            async with asyncio.timeout(self.settings.generation_timeout):
                async for piece in provider.stream(model=agent.model, system=system,
                        messages=messages, api_key=key, max_tokens=self.settings.max_output_tokens,
                        cache_prefix=self.settings.context_block > 0):
                    parts.append(piece)
                    buffered.append(piece)
                    if time.monotonic() - last_flush >= self.settings.chunk_interval:
                        await flush()
            await flush()
        except KeyRejected:
            await flush()
            await self._fail_job(room_id, job, 'api_key_invalid',
                                 f'{PROVIDER_NAMES[ai.provider]} 拒绝了你的 API key，请在设置里更新', False)
            return
        except TimeoutError:
            await flush()
            await self._fail_job(room_id, job, 'timeout', f'{ai.name} 回复超时', True)
            return
        except asyncio.CancelledError:
            if buffered:
                await flush()
            raise
        except Exception as error:
            await flush()
            log.warning('generation by %s failed: %s', ai.slug, type(error).__name__)
            if self.settings.classified_retry and isinstance(error, ProviderError):
                await self._fail_classified(room_id, job, ai, error)
                return
            await self._fail_job(room_id, job, 'provider_error', f'{ai.name} 暂时无法回复', True)
            return
        raw = ''.join(parts)
        content = (strip_self_name(raw, ai.name) if strip else raw).strip()[:MAX_CONTENT]
        if not content:
            await self._fail_job(room_id, job, 'empty_reply', f'{ai.name} 没有给出回复', True)
            return
        if self.controls and not await self.controls.snapshot(job, content, final=True):
            await self._controlled_finish(room_id, job)
            return
        async with self._room(room_id) as state:
            if not self._owns(state, job):
                return
            state.active.update(status='final' , content=content, member_id=ai.member_id,
                                retry_at=self.clock() + self.settings.retry_base_seconds)
            final = copy.deepcopy(state.active)
        self._send_final(room_id, final)

    async def _host_pick(self, room_id, state, payer_user_id):
        """Asks the host who should speak next; (None, False) on any failure."""
        settings = self.settings
        key = await self.keys.get(payer_user_id, settings.host_provider)
        if key is None:
            return None, False
        entries = await recent_messages(self.cache, self.pool, room_id, 20)
        history = "\n".join(f"{e['member']['name']}：{e['content']}" for e in entries[-20:])
        participants = "\n".join(f"{ai.name}：{(ai.persona or '').strip()}" for ai in state.ais)
        system = host.HOST_PROMPT.format(participants=participants, history=history)
        provider = self.providers.create(settings.host_provider)
        try:
            parts = []
            async with asyncio.timeout(settings.generation_timeout):
                async for piece in provider.stream(
                        model=settings.host_model, system=system,
                        messages=[ChatMessage("user", history)],
                        api_key=key, max_tokens=256):
                    parts.append(piece)
        except (KeyRejected, ProviderError, TimeoutError):
            return None, False
        parsed = host.parse_host_json("".join(parts))
        if parsed is None:
            return None, False
        return parsed["speakers"], parsed["end"]

    async def _host_round(self, state, room_id, payer, content, available, mention_ids):
        """Lets the host decide the round's speakers; falls back on failure."""
        if mention_ids:
            mentioned = list(dict.fromkeys(a.slug for m in mention_ids
                                           for a in state.ais if a.member_id == m))
        else:
            mentioned = rules.mentions(content, state.ais)
        speakers, end = await self._host_pick(room_id, state, payer.user_id)
        if speakers is not None:
            return rules.on_host_decision(state, speakers, end, payer, available,
                                          mentioned, self.settings.round_max_replies)
        self.send(ai_pb2.AiAction(notice=ai_pb2.Notice(
            room_id=room_id, notify_member_id=payer.member_id,
            code='host_unavailable', message='主持人暂时不可用，已按原规则回复')))
        return rules.on_human_message(state, payer, content, available, self.rng, mention_ids)

    async def _context(self, room_id, job, member_id):
        """Recent messages, plus related older ones when retrieval is on."""
        settings = self.settings
        count, chars, tokens = settings.context_messages, settings.context_chars, settings.context_tokens
        if job.get('context_halved'):
            count, chars, tokens = max(1, count // 2), max(1, chars // 2), tokens // 2
        entries = await recent_messages(self.cache, self.pool, room_id, count + settings.context_block)
        trigger = job.get('trigger')
        related = None
        if (settings.retrieval_enabled and job['kind'] == 'reply' and trigger and entries
                and all('seq' in entry for entry in entries)):
            items = await related_messages(self.pool, room_id, min(int(e['seq']) for e in entries),
                                           trigger.get('content', ''), settings.retrieval_top_k,
                                           settings.retrieval_chars)
            related = related_note(items)
            if related is not None:
                # The reference shares the history budget instead of adding to it.
                if tokens:
                    tokens = max(1, tokens - estimate_tokens(related.content))
                else:
                    chars = max(1, chars - len(related.content))
        return to_chat(entries, member_id, count, chars, block=settings.context_block,
                       max_tokens=tokens), related

    async def _fail_classified(self, room_id, job, ai, error):
        backoff = self.settings.retry_base_seconds * 2 ** (job['attempt'] - 1)
        if error.kind == 'refused':
            await self._fail_job(room_id, job, 'content_refused', f'{ai.name} 拒绝回复这条内容', False)
        elif error.kind == 'context_too_long' and not job.get('context_halved'):
            await self._fail_job(room_id, job, 'context_too_long', f'{ai.name} 暂时无法回复', True,
                                 updates={'context_halved': True})
        elif error.kind == 'rate_limit':
            delay = min(MAX_RETRY_AFTER, max(backoff, error.retry_after or 0))
            await self._fail_job(room_id, job, 'rate_limited', f'{ai.name} 暂时无法回复', True, delay=delay)
        else:
            await self._fail_job(room_id, job, 'provider_error', f'{ai.name} 暂时无法回复', True)

    async def _controlled_finish(self, room_id, job):
        durable = await self.controls.generation(job['id'])
        if durable and durable.phase == 'generating':
            # A failed control read cancels upstream conservatively. Keep the
            # leased job recoverable until SQL confirms a terminal result.
            return
        final = None
        async with self._room(room_id) as state:
            if not self._owns(state, job):
                return
            character = state.ai(job['slug'])
            removed = character is None or (not character.is_legacy and character.admission_id != job.get('admission_id', character.admission_id))
            if removed:
                state.active = None
                return
            state.pending.clear()
            state.timer_due = None
            state.target = None
            if durable and (durable.outcome in ('completed', 'failed') or (durable.stopped and durable.content.strip())):
                state.active.update(status='final', content=durable.content, stopped=durable.stopped, outcome=durable.outcome,
                    retry_at=self.clock() + self.settings.retry_base_seconds)
                final = copy.deepcopy(state.active)
            else:
                state.active = None
        if final:
            self._send_final(room_id, final)
        else:
            self._failed(room_id, job, 'stopped', '已停止')

    def sync_controls(self):
        # Versions are wake hints only. Every observation reads authoritative
        # SQL, so even an older duplicate event cannot restore an old state.
        self.control_wakeup.set()

    async def _watch_control(self, room_id, job, generation):
        self.control_watchers[job['id']] = (job, generation)
        self.sync_controls()
        if self.control_monitor_task is None or self.control_monitor_task.done():
            self.control_monitor_task = asyncio.create_task(self._monitor_controls())
        try:
            await asyncio.Event().wait()
        finally:
            self.control_watchers.pop(job['id'], None)
            if not self.control_watchers and self.control_monitor_task:
                self.control_monitor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.control_monitor_task
                self.control_monitor_task = None

    async def _monitor_controls(self):
        while self.control_watchers:
            self.control_wakeup.clear()
            observed = dict(self.control_watchers)
            try:
                durable = await self.controls.generations(observed)
                for generation_id, (job, generation) in observed.items():
                    row = durable.get(generation_id)
                    if row is None or row.phase != 'generating' or row.attempt != job['attempt']:
                        generation.cancel()
            except asyncio.CancelledError:
                raise
            except Exception:
                # SQL uncertainty stops upstream work. The leased durable job
                # is recovered only after its state can be read again.
                log.exception('generation control sync')
                for _, generation in observed.values():
                    generation.cancel()
            try:
                await asyncio.wait_for(self.control_wakeup.wait(), self.settings.control_poll_seconds)
            except TimeoutError:
                pass

    def _send_final(self, room_id, job):
        self.send(ai_pb2.AiAction(final=ai_pb2.Final(room_id=room_id,
            generation_id=job['id'], ai_member_id=job['member_id'], content=job['content'],admission_id=job.get('admission_id',''),attempt=job['attempt'])))

    def _finish_failed(self, state, job):
        state.active = None
        if job['kind'] in ('proactive', 'goodbye') and job['epoch'] == state.epoch:
            state.timer_due = self._next_timer(state)

    async def _fail_job(self, room_id, job, code, message, retryable, *, delay=None, updates=None):
        terminal = False
        async with self._room(room_id) as state:
            if not self._owns(state, job):
                return
            if retryable and job['attempt'] < self.settings.generation_max_attempts:
                if delay is None:
                    delay = self.settings.retry_base_seconds * 2 ** (job['attempt'] - 1)
                state.active.update(status='queued', owner='', lease_until=0,
                    retry_at=self.clock() + delay, **(updates or {}))
            elif code == 'ai_removed' or (code == 'api_key_required' and job['attempt'] == 1):
                if self.controls:
                    await self.controls.cancelled(job)
                self._finish_failed(state, job)
                terminal = True
            else:
                if self.controls:
                    durable = await self.controls.failed(job, code)
                    if durable and durable.outcome in ('completed', 'failed', 'stopped'):
                        state.active.update(status='final', content=durable.content,
                                            outcome=durable.outcome, stopped=durable.stopped)
                    else:
                        self._finish_failed(state, job)
                else:
                    state.active.update(status='final', content=state.active.get('last_nonempty_content', ''), outcome='failed')
                terminal = True
        log.info(json.dumps({'event': 'request_stage', 'instance': 'ai',
            'operation': 'ai.generate', 'stage': 'ai_attempt', 'result': code,
            'generation_id': job['id'], 'request_id': job.get('trigger_id', ''),
            'attempt': job['attempt'], 'room_id': room_id}))
        if terminal:
            self._failure_notice(room_id, job, code, message)
            state = await self._load(room_id)
            if state.active and state.active['id'] == job['id'] and state.active['status'] == 'final':
                self._send_final(room_id, state.active)

    def _failure_notice(self, room_id, job, code, message):
        self.send(ai_pb2.AiAction(notice=ai_pb2.Notice(room_id=room_id,
            notify_member_id=job['payer'][0], code=code, message=message)))

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
            notify_member_id=job['payer'][0], code=code, message=message,admission_id=job.get("admission_id",""))))

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
    state.ais = [rules.RoomAi(a.member_id,a.slug,a.provider,a.name,a.model_slug,a.admission_id,a.persona,a.persona_version,a.creator_name,a.is_legacy or not a.model_slug) for a in snapshot.ais]
    state.collaboration_enabled = snapshot.collaboration_enabled
    state.room_name = snapshot.room_name
    state.pending = [j for j in state.pending if any(a.member_id==j.get('member_id') and a.admission_id==j.get('admission_id',a.admission_id) for a in state.ais)]
    if state.target is not None and (len(state.humans) != 1 or state.ai(state.target) is None):
        state.target = None
        state.timer_due = None
