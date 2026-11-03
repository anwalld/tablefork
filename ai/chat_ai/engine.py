"""Turns room events into AI replies.

All state lives in Redis so that any number of AI service processes can run:
  ai:room:{id}      RoomState as JSON
  ai:lock:{id}      short lock around reading and writing that state
  ai:gen:{id}       held while the room has a reply being generated
  ai:pending:{id}   replies waiting for the generation lock, merged per AI
  ai:genmeta:{gid}  who paid for a generation and its hop, for chains
  ai:timers         ZSET of room ids by when their silence timer fires
  ai:event:{...}    events already handled, so resent ones are ignored
"""

import asyncio
import contextlib
import json
import logging
import random
import time
import uuid
from typing import Awaitable, Callable

import aiomysql
import redis.asyncio as redis

from . import rules
from .agents import Agent
from .config import Settings
from .context import recent_messages, to_chat
from .gen import ai_pb2
from .keys import KeyStore
from .providers.base import ChatMessage, KeyRejected
from .providers.factory import ProviderFactory

log = logging.getLogger(__name__)

PROVIDER_NAMES = {"deepseek": "DeepSeek", "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini"}
MAX_CONTENT = 4000

PROACTIVE_PROMPT = "（对方有一阵子没说话了。请你自然地主动说点什么，比如接着刚才的话题，或者问候一下。）"
GOODBYE_PROMPT = "（对方很久没有回复了。请简短、自然地道个别，说下次再聊。）"

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

Send = Callable[[ai_pb2.AiAction], None]


class Engine:
    def __init__(
        self,
        settings: Settings,
        cache: redis.Redis,
        pool: aiomysql.Pool | None,
        keys: KeyStore,
        agents: dict[str, Agent],
        providers: ProviderFactory,
        send: Send,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
    ):
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

    # Events from the chat server -------------------------------------------

    async def handle(self, event: ai_pb2.GatewayEvent, ack: Callable[[], Awaitable[None]]) -> None:
        """Records the event's effect on room state, acknowledges it, then
        starts any replies. Resent events are acknowledged and skipped."""
        seen = f"ai:event:{event.instance_id}:{event.event_id}"
        if not await self.cache.set(seen, 1, nx=True, ex=600):
            await ack()
            return
        body = event.WhichOneof("body")
        if body == "message_saved":
            await self._on_message(event.message_saved, ack)
        elif body == "members_changed":
            await self._on_members(event.members_changed.room, ack)
        elif body == "room_deleted":
            await self._on_deleted(event.room_deleted.room_id)
            await ack()
        else:
            await ack()

    async def _on_message(self, saved: ai_pb2.MessageSaved, ack) -> None:
        room_id = saved.room.room_id
        sender = saved.sender
        if not sender.is_ai:
            payer = rules.Human(sender.member_id, sender.user_id)
            keys = await self.keys.providers(payer.user_id)
            async with self._room(room_id) as state:
                _apply_snapshot(state, saved.room)
                decision = rules.on_human_message(state, payer, saved.content, keys, self.rng)
                restart_timer = len(state.humans) == 1 and state.target is not None
                epoch = state.epoch
            await ack()
            await self._set_timer(room_id, restart_timer)
            await self._act(room_id, decision, epoch)
            return

        meta = await self._meta(saved.generation_id)
        if meta is None:
            await ack()
            return
        payer = rules.Human(meta["payer_member"], meta["payer_user"])
        keys = await self.keys.providers(payer.user_id)
        async with self._room(room_id) as state:
            _apply_snapshot(state, saved.room)
            if meta["epoch"] != state.epoch:
                decision = rules.Decision()
            else:
                decision = rules.on_ai_message(
                    state, sender.slug, saved.content, payer, meta["hop"], keys,
                    self.settings.chain_p, self.rng,
                )
            epoch = state.epoch
        await ack()
        await self._act(room_id, decision, epoch)

    async def _on_members(self, snapshot: ai_pb2.RoomSnapshot, ack) -> None:
        async with self._room(snapshot.room_id) as state:
            _apply_snapshot(state, snapshot)
            keep_timer = len(state.humans) == 1 and state.target is not None
        await ack()
        if not keep_timer:
            await self._set_timer(snapshot.room_id, False)

    async def _on_deleted(self, room_id: int) -> None:
        await self.cache.delete(f"ai:room:{room_id}", f"ai:pending:{room_id}")
        await self.cache.zrem("ai:timers", room_id)

    # Silence timers ---------------------------------------------------------

    async def _set_timer(self, room_id: int, on: bool) -> None:
        if on:
            await self.cache.zadd("ai:timers", {room_id: self.clock() + self.settings.idle_seconds})
        else:
            await self.cache.zrem("ai:timers", room_id)

    async def fire_due_timers(self) -> None:
        """Handles every room whose silence timer has fired. Each firing is
        claimed with ZREM, so with several processes only one handles it."""
        due = await self.cache.zrangebyscore("ai:timers", "-inf", self.clock())
        for raw in due:
            room_id = int(raw)
            if await self.cache.zrem("ai:timers", room_id):
                await self._on_timer(room_id)

    async def _on_timer(self, room_id: int) -> None:
        async with self._room(room_id) as state:
            human = state.humans[0] if len(state.humans) == 1 else None
        keys = await self.keys.providers(human.user_id) if human else set()
        async with self._room(room_id) as state:
            decision, again = rules.on_idle_timer(state, keys, self.settings.proactive_p1, self.rng)
            epoch = state.epoch
        if again:
            await self._set_timer(room_id, True)
        await self._act(room_id, decision, epoch)

    async def run_timers(self) -> None:
        while True:
            try:
                await self.fire_due_timers()
            except Exception:
                log.exception("silence timers")
            await asyncio.sleep(self.settings.timer_poll_seconds)

    # Replies ----------------------------------------------------------------

    async def _act(self, room_id: int, decision: rules.Decision, epoch: int) -> None:
        for missing in decision.missing:
            self._notice(room_id, missing.payer, missing.provider)
        if not decision.requests:
            return
        for request in decision.requests:
            item = {
                "slug": request.slug,
                "payer": [request.payer.member_id, request.payer.user_id],
                "kind": request.kind,
                "hop": request.hop,
                "epoch": epoch,
            }
            await self.cache.rpush(f"ai:pending:{room_id}", json.dumps(item))
        await self._drain(room_id)

    async def _drain(self, room_id: int) -> None:
        token = uuid.uuid4().hex
        if await self.cache.set(f"ai:gen:{room_id}", token, nx=True, px=120_000):
            task = asyncio.create_task(self._generate_all(room_id, token))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _generate_all(self, room_id: int, token: str) -> None:
        """Generates pending replies one at a time while holding the room's
        generation lock. Requests that arrived meanwhile were queued and are
        merged, so each AI answers them once."""
        lock = f"ai:gen:{room_id}"
        pending = f"ai:pending:{room_id}"
        try:
            while True:
                async with self.cache.pipeline(transaction=True) as pipe:
                    pipe.lrange(pending, 0, -1)
                    pipe.delete(pending)
                    raw, _ = await pipe.execute()
                if not raw:
                    break
                merged: dict[str, dict] = {}
                for item in map(json.loads, raw):
                    merged.setdefault(item["slug"], item)
                for item in merged.values():
                    await self.cache.pexpire(lock, 120_000)
                    try:
                        await self._generate(room_id, item)
                    except Exception:
                        log.exception("generation in room %s", room_id)
        finally:
            await self.cache.eval(_RELEASE, 1, lock, token)
        if await self.cache.llen(pending):
            await self._drain(room_id)

    async def _generate(self, room_id: int, item: dict) -> None:
        state = await self._load(room_id)
        ai = state.ai(item["slug"])
        agent = self.agents.get(item["slug"])
        if ai is None or agent is None:
            return
        payer = rules.Human(*item["payer"])
        api_key = await self.keys.get(payer.user_id, ai.provider)
        if api_key is None:
            self._notice(room_id, payer, ai.provider)
            return

        generation_id = uuid.uuid4().hex
        entries = await recent_messages(self.cache, self.pool, room_id, self.settings.context_messages)
        messages = to_chat(entries, ai.member_id, self.settings.context_messages, self.settings.context_chars)
        if item["kind"] == "proactive":
            messages.append(ChatMessage("user", PROACTIVE_PROMPT))
        elif item["kind"] == "goodbye":
            messages.append(ChatMessage("user", GOODBYE_PROMPT))
        if not messages:
            messages.append(ChatMessage("user", "（聊天开始）"))

        provider = self.providers.create(ai.provider)
        parts: list[str] = []
        buffered: list[str] = []
        last_flush = time.monotonic()

        def flush():
            nonlocal last_flush
            if buffered:
                self.send(ai_pb2.AiAction(chunk=ai_pb2.Chunk(
                    room_id=room_id, generation_id=generation_id,
                    ai_member_id=ai.member_id, text="".join(buffered),
                )))
                buffered.clear()
            last_flush = time.monotonic()

        try:
            async with asyncio.timeout(self.settings.generation_timeout):
                async for piece in provider.stream(
                    model=agent.model, system=agent.persona, messages=messages,
                    api_key=api_key, max_tokens=self.settings.max_output_tokens,
                ):
                    parts.append(piece)
                    buffered.append(piece)
                    if time.monotonic() - last_flush >= self.settings.chunk_interval:
                        flush()
            flush()
        except KeyRejected:
            self._failed(room_id, generation_id, ai, payer, "api_key_invalid",
                         f"{PROVIDER_NAMES[ai.provider]} 拒绝了你的 API key，请在设置里更新")
            return
        except TimeoutError:
            self._failed(room_id, generation_id, ai, payer, "timeout", f"{ai.name} 回复超时")
            return
        except Exception as e:
            log.warning("generation by %s failed: %s", ai.slug, type(e).__name__)
            self._failed(room_id, generation_id, ai, payer, "provider_error", f"{ai.name} 暂时无法回复")
            return

        content = "".join(parts).strip()[:MAX_CONTENT]
        if not content:
            self._failed(room_id, generation_id, ai, payer, "empty_reply", f"{ai.name} 没有给出回复")
            return
        meta = {"payer_member": payer.member_id, "payer_user": payer.user_id,
                "hop": item["hop"], "epoch": item["epoch"]}
        await self.cache.set(f"ai:genmeta:{generation_id}", json.dumps(meta), ex=86400)
        self.send(ai_pb2.AiAction(final=ai_pb2.Final(
            room_id=room_id, generation_id=generation_id,
            ai_member_id=ai.member_id, content=content,
        )))

    def _notice(self, room_id: int, payer: rules.Human, provider: str) -> None:
        if provider:
            text = f"你还没有填写 {PROVIDER_NAMES[provider]} 的 API key，请先在设置里填写"
        else:
            text = "房间里没有你能使用的 AI，请先在设置里填写对应的 API key"
        self.send(ai_pb2.AiAction(notice=ai_pb2.Notice(
            room_id=room_id, notify_member_id=payer.member_id,
            code="api_key_required", provider=provider, message=text,
        )))

    def _failed(self, room_id, generation_id, ai, payer, code, message) -> None:
        self.send(ai_pb2.AiAction(failed=ai_pb2.Failed(
            room_id=room_id, generation_id=generation_id, ai_member_id=ai.member_id,
            notify_member_id=payer.member_id, code=code, message=message,
        )))

    # State ------------------------------------------------------------------

    async def _meta(self, generation_id: str) -> dict | None:
        raw = await self.cache.get(f"ai:genmeta:{generation_id}") if generation_id else None
        return json.loads(raw) if raw else None

    async def _load(self, room_id: int) -> rules.RoomState:
        raw = await self.cache.get(f"ai:room:{room_id}")
        return rules.RoomState.from_json(json.loads(raw)) if raw else rules.RoomState()

    @contextlib.asynccontextmanager
    async def _room(self, room_id: int):
        """The room's state, locked for reading and writing back."""
        lock = f"ai:lock:{room_id}"
        token = uuid.uuid4().hex
        deadline = time.monotonic() + 5
        while not await self.cache.set(lock, token, nx=True, px=5000):
            if time.monotonic() > deadline:
                raise TimeoutError(f"room {room_id} state is locked")
            await asyncio.sleep(0.01)
        try:
            state = await self._load(room_id)
            yield state
            await self.cache.set(f"ai:room:{room_id}", json.dumps(state.to_json()), ex=30 * 86400)
        finally:
            await self.cache.eval(_RELEASE, 1, lock, token)


def _apply_snapshot(state: rules.RoomState, snapshot: ai_pb2.RoomSnapshot) -> None:
    state.humans = [rules.Human(h.member_id, h.user_id) for h in snapshot.humans]
    state.ais = [rules.RoomAi(a.member_id, a.slug, a.provider, a.name) for a in snapshot.ais]
    if state.target is not None and (len(state.humans) != 1 or state.ai(state.target) is None):
        state.target = None
