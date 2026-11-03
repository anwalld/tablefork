import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from google.genai import errors

from chat_ai.providers.anthropic_provider import AnthropicProvider
from chat_ai.providers.base import ChatMessage, KeyRejected, ProviderError, alternate
from chat_ai.providers.factory import ProviderFactory
from chat_ai.providers.fake import FakeProvider
from chat_ai.providers.gemini_provider import GeminiProvider
from chat_ai.providers.openai_compatible import OpenAiCompatible

MESSAGES = [ChatMessage("user", "alice：hi")]


def sse(events):
    body = "".join(events).encode()
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def sse2(events):
    body = "".join(events).encode()
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def client2(handler):
    # The Anthropic SDK ships its own fork of httpx.
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def collect(provider, key="k"):
    return [p async for p in provider.stream(model="m", system="s", messages=MESSAGES, api_key=key, max_tokens=50)]


def openai_chunk(text):
    return "data: " + json.dumps({
        "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }) + "\n\n"


async def test_openai_streams_text():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return sse([openai_chunk("你"), openai_chunk("好"), "data: [DONE]\n\n"])

    provider = OpenAiCompatible("https://api.deepseek.com", 10, client(handler))
    assert await collect(provider, "sk-1") == ["你", "好"]
    assert seen["auth"] == "Bearer sk-1"
    assert seen["body"]["stream"] is True
    assert seen["body"]["messages"][0] == {"role": "system", "content": "s"}


async def test_openai_rejected_key():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "bad key", "type": "invalid_request_error"}})

    provider = OpenAiCompatible("https://api.openai.com/v1", 10, client(handler))
    with pytest.raises(KeyRejected):
        await collect(provider)
    with pytest.raises(KeyRejected):
        await provider.validate("k")


async def test_openai_server_error():
    provider = OpenAiCompatible("https://api.openai.com/v1", 10, client(lambda r: httpx.Response(500, json={})))
    with pytest.raises(ProviderError):
        await collect(provider)


def anthropic_event(kind, data):
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n"


async def test_anthropic_streams_text():
    message = {"id": "m", "type": "message", "role": "assistant", "content": [], "model": "m",
               "stop_reason": None, "stop_sequence": None,
               "usage": {"input_tokens": 1, "output_tokens": 0}}
    events = [
        anthropic_event("message_start", {"message": message}),
        anthropic_event("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
        anthropic_event("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "你"}}),
        anthropic_event("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "好"}}),
        anthropic_event("content_block_stop", {"index": 0}),
        anthropic_event("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                          "usage": {"output_tokens": 2}}),
        anthropic_event("message_stop", {}),
    ]
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-api-key") or request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return sse2(events)

    provider = AnthropicProvider(10, http_client=client2(handler))
    assert await collect(provider, "sk-ant") == ["你", "好"]
    assert seen["key"] in ("sk-ant", "Bearer sk-ant")
    assert seen["body"]["system"] == "s"


async def test_anthropic_rejected_key():
    def handler(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "x"}})

    provider = AnthropicProvider(10, http_client=client2(handler))
    with pytest.raises(KeyRejected):
        await collect(provider)


def gemini_with(stream=None, error=None):
    provider = GeminiProvider(10)

    async def generate(**kwargs):
        if error:
            raise error

        async def pieces():
            for text in stream:
                yield SimpleNamespace(text=text)

        return pieces()

    fake = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate)))
    provider._client = lambda api_key: fake
    return provider


async def test_gemini_streams_text():
    assert await collect(gemini_with(["你", None, "好"])) == ["你", "好"]


async def test_gemini_rejected_key():
    error = errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}})
    with pytest.raises(KeyRejected):
        await collect(gemini_with(error=error))


async def test_gemini_other_error():
    error = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
    with pytest.raises(ProviderError):
        await collect(gemini_with(error=error))


def test_alternate_merges_and_starts_with_user():
    merged = alternate([ChatMessage("assistant", "a"), ChatMessage("user", "b"), ChatMessage("user", "c")])
    assert [(m.role, m.content) for m in merged] == [("user", "（聊天开始）"), ("assistant", "a"), ("user", "b\nc")]


def test_factory_only_builds_fakes_when_asked():
    assert isinstance(ProviderFactory(10, fake=True).create("anthropic"), FakeProvider)
    for name in ("openai", "deepseek", "anthropic", "gemini"):
        assert not isinstance(ProviderFactory(10).create(name), FakeProvider)
    with pytest.raises(ValueError):
        ProviderFactory(10).create("nope")


async def test_fake_provider():
    fake = FakeProvider("anthropic")
    text = "".join([p async for p in fake.stream(model="m", system="你是 Claude，x", messages=MESSAGES,
                                                 api_key="fake-1", max_tokens=1)])
    assert text == "Claude: alice：hi"
    with pytest.raises(KeyRejected):
        await fake.validate("fake-reject")
    with pytest.raises(KeyRejected):
        await fake.validate("sk-real")
