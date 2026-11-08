"""A provider for tests. Never reachable unless AI_FAKE_PROVIDERS=1.

Keys must start with "fake-"; "fake-reject" is refused. The reply is
"<agent>: <last message>" in three pieces. A last message containing "#fail"
makes it fail, "#slow" makes it hang until the generation times out.
"#ratelimit" fails as rate limited, "#refuse" as refused by moderation, and
"#toolong" as too long while the whole conversation is 100 characters or more.
"""

import asyncio
import re
from typing import AsyncIterator

from .base import ChatMessage, KeyRejected, ProviderError


class FakeProvider:
    def __init__(self, provider: str):
        self.provider = provider

    def _check(self, api_key: str) -> None:
        if not api_key.startswith("fake-") or api_key == "fake-reject":
            raise KeyRejected("fake provider refused the key")

    async def stream(
        self, *, model: str, system: str, messages: list[ChatMessage], api_key: str, max_tokens: int,
        cache_prefix: bool = False,
    ) -> AsyncIterator[str]:
        self._check(api_key)
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if "#fail" in last:
            raise ProviderError("fake failure")
        if "#ratelimit" in last:
            raise ProviderError("fake rate limit", "rate_limit", 0.2)
        if "#refuse" in last:
            raise ProviderError("fake refusal", "refused")
        if "#toolong" in last and sum(len(m.content) for m in messages) >= 100:
            raise ProviderError("fake context too long", "context_too_long")
        if "#slow" in last:
            await asyncio.sleep(3600)
        name = system.split("，", 1)[0].removeprefix("你是 ").strip()
        text = f"{name}: {last}"
        third = max(1, len(text) // 3)
        restart = "#restart" in last
        for index, piece in enumerate((text[:third], text[third : 2 * third], text[2 * third :])):
            # Arriving after the engine's chunk interval makes the first piece
            # go out on its own, so the two second hold starts with a visible
            # draft instead of ending just before the reply completes.
            await asyncio.sleep(0.25 if restart and index == 0 else 0.05)
            yield piece
            if index == 0 and "#restart" in last:
                await asyncio.sleep(2)

    async def complete(self, *, model: str, system: str, messages: list[ChatMessage], exchanges: list,
                       tools: list[dict], api_key: str, max_tokens: int):
        """Tool turns for tests: "#tool:name {json}" in the newest user message
        asks for that tool once; after results arrive, the reply quotes them."""
        import json
        from .base import ToolCall, ToolTurn
        self._check(api_key)
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        name = system.split("，", 1)[0].removeprefix("你是 ").strip()
        if exchanges:
            results = "；".join(text for exchange in exchanges for _, text in exchange.results)
            return ToolTurn(f"{name}: 工具结果：{results}")
        match = re.search(r"#tool:(\w+)\s*(\{.*\})?", last)
        if match:
            arguments = json.loads(match.group(2)) if match.group(2) else {}
            return ToolTurn("", (ToolCall("call-1", match.group(1), arguments),))
        return ToolTurn(f"{name}: {last}")

    async def embed(self, *, model: str, texts: list[str], api_key: str,
                    dimensions: int | None = None) -> list[list[float]]:
        """Deterministic bag-of-character-bigrams vectors: texts that share
        words land close together, which is enough to test retrieval."""
        self._check(api_key)
        size = dimensions or 64
        vectors = []
        for text in texts:
            if "#fail" in text:
                raise ProviderError("fake embedding failure")
            vector = [0.0] * size
            lowered = text.lower()
            for a, b in zip(lowered, lowered[1:]):
                vector[(ord(a) * 31 + ord(b)) % size] += 1.0
            norm = sum(v * v for v in vector) ** 0.5 or 1.0
            vectors.append([v / norm for v in vector])
        return vectors

    async def validate(self, api_key: str) -> None:
        self._check(api_key)
