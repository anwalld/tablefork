"""A provider for tests. Never reachable unless AI_FAKE_PROVIDERS=1.

Keys must start with "fake-"; "fake-reject" is refused. The reply is
"<agent>: <last message>" in three pieces. A last message containing "#fail"
makes it fail, "#slow" makes it hang until the generation times out.
"""

import asyncio
from typing import AsyncIterator

from .base import ChatMessage, KeyRejected, ProviderError


class FakeProvider:
    def __init__(self, provider: str):
        self.provider = provider

    def _check(self, api_key: str) -> None:
        if not api_key.startswith("fake-") or api_key == "fake-reject":
            raise KeyRejected("fake provider refused the key")

    async def stream(
        self, *, model: str, system: str, messages: list[ChatMessage], api_key: str, max_tokens: int
    ) -> AsyncIterator[str]:
        self._check(api_key)
        last = messages[-1].content if messages else ""
        if "#fail" in last:
            raise ProviderError("fake failure")
        if "#slow" in last:
            await asyncio.sleep(3600)
        name = system.split("，", 1)[0].removeprefix("你是 ").strip()
        text = f"{name}: {last}"
        third = max(1, len(text) // 3)
        for piece in (text[:third], text[third : 2 * third], text[2 * third :]):
            await asyncio.sleep(0.05)
            yield piece

    async def validate(self, api_key: str) -> None:
        self._check(api_key)
