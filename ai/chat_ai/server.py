"""The gRPC service the chat servers connect to."""

import asyncio
import hmac
import logging
import random

import aiomysql
import grpc
import redis.asyncio as redis

from .agents import PROVIDERS, check_against_database, load_agents
from .config import Settings
from .engine import Engine
from .gen import ai_pb2, ai_pb2_grpc
from .keys import KeyCipher, KeyStore
from .providers.base import KeyRejected
from .providers.factory import ProviderFactory

log = logging.getLogger(__name__)


class Outbox:
    """Actions for the chat servers, sent over whichever stream is open.

    Any chat server can deliver any action, since chunks go out through
    Redis and finished replies are stored by room. Actions produced while
    no stream is open wait, up to a limit.
    """

    def __init__(self, limit: int = 10_000):
        self.streams: list[asyncio.Queue] = []
        self.waiting: list[ai_pb2.AiAction] = []
        self.limit = limit
        self.turn = 0

    def send(self, action: ai_pb2.AiAction) -> None:
        if not self.streams:
            if len(self.waiting) >= self.limit:
                self.waiting.pop(0)
                log.error("no chat server connected; dropped an action")
            self.waiting.append(action)
            return
        self.turn = (self.turn + 1) % len(self.streams)
        self.streams[self.turn].put_nowait(action)

    def open(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.streams.append(queue)
        for action in self.waiting:
            queue.put_nowait(action)
        self.waiting.clear()
        return queue

    def close(self, queue: asyncio.Queue) -> None:
        self.streams.remove(queue)
        while not queue.empty():
            item = queue.get_nowait()
            if item is not None and item.WhichOneof("body") != "ack":
                self.send(item)


class Gateway(ai_pb2_grpc.AiGatewayServicer):
    def __init__(self, settings: Settings, engine: Engine, keys: KeyStore,
                 providers: ProviderFactory, outbox: Outbox):
        self.settings = settings
        self.engine = engine
        self.keys = keys
        self.providers = providers
        self.outbox = outbox

    async def _authorize(self, context: grpc.aio.ServicerContext) -> None:
        metadata = dict(context.invocation_metadata())
        expected = f"Bearer {self.settings.grpc_token}"
        if not hmac.compare_digest(metadata.get("authorization", ""), expected):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "bad token")

    async def Connect(self, request_iterator, context):
        await self._authorize(context)
        queue = self.outbox.open()
        log.info("chat server connected")

        async def read():
            try:
                async for event in request_iterator:
                    async def ack(event_id=event.event_id):
                        queue.put_nowait(ai_pb2.AiAction(ack=ai_pb2.Ack(event_id=event_id)))
                    try:
                        await self.engine.handle(event, ack)
                    except Exception:
                        # Unacknowledged, so the chat server resends it later.
                        log.exception("event %s", event.event_id)
            finally:
                queue.put_nowait(None)

        reader = asyncio.create_task(read())
        try:
            while True:
                action = await queue.get()
                if action is None:
                    break
                yield action
        finally:
            reader.cancel()
            self.outbox.close(queue)
            log.info("chat server disconnected")

    async def StoreKey(self, request, context):
        await self._authorize(context)
        if request.provider not in PROVIDERS or not request.api_key.strip():
            return ai_pb2.StoreKeyReply(ok=False, error_code="invalid_argument")
        api_key = request.api_key.strip()
        try:
            await self.providers.create(request.provider).validate(api_key)
        except KeyRejected:
            return ai_pb2.StoreKeyReply(ok=False, error_code="api_key_invalid")
        except Exception as e:
            log.warning("validating a %s key failed: %s", request.provider, type(e).__name__)
            return ai_pb2.StoreKeyReply(ok=False, error_code="unavailable")
        last4 = await self.keys.save(request.user_id, request.provider, api_key)
        return ai_pb2.StoreKeyReply(ok=True, last4=last4)


async def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    cache = redis.Redis(host=settings.redis_host, port=settings.redis_port,
                        password=settings.redis_password or None, decode_responses=True,
                        socket_connect_timeout=2, socket_timeout=2)
    pool = await aiomysql.create_pool(
        host=settings.mysql_host, user=settings.mysql_user, password=settings.mysql_password,
        db=settings.mysql_database, autocommit=True, minsize=1, maxsize=8, charset="utf8mb4",
    )
    agents = load_agents(settings.agents_file)
    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute("SELECT slug, provider FROM ai_profiles")
        check_against_database(agents, dict(await cursor.fetchall()))

    keys = KeyStore(pool, KeyCipher(settings.encryption_key))
    providers = ProviderFactory(settings.generation_timeout, fake=settings.fake_providers)
    if settings.fake_providers:
        log.warning("AI_FAKE_PROVIDERS is on: no real model will be called")
    outbox = Outbox()
    engine = Engine(settings, cache, pool, keys, agents, providers, outbox.send, random.Random())

    server = grpc.aio.server()
    ai_pb2_grpc.add_AiGatewayServicer_to_server(Gateway(settings, engine, keys, providers, outbox), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    await server.start()
    log.info("listening on %s", settings.grpc_port)
    timers = asyncio.create_task(engine.run_timers())
    try:
        await server.wait_for_termination()
    finally:
        timers.cancel()


def main() -> None:
    asyncio.run(serve())
