"""Anthropic's messages API (Claude)."""

from typing import AsyncIterator

import anthropic

from .base import ChatMessage, KeyRejected, ProviderError, alternate


class AnthropicProvider:
    def __init__(self, timeout: float, base_url: str | None = None, http_client=None):
        self.timeout = timeout
        self.base_url = base_url
        self.http_client = http_client

    def _client(self, api_key: str) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=0,
            http_client=self.http_client,
        )

    async def stream(
        self, *, model: str, system: str, messages: list[ChatMessage], api_key: str, max_tokens: int
    ) -> AsyncIterator[str]:
        try:
            response = await self._client(api_key).messages.create(
                model=model,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in alternate(messages)],
                max_tokens=max_tokens,
                stream=True,
            )
            async for event in response:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    yield event.delta.text
        except anthropic.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except anthropic.APIError as e:
            raise ProviderError(str(e)) from e

    async def validate(self, api_key: str) -> None:
        try:
            await self._client(api_key).models.list(limit=1)
        except anthropic.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except anthropic.APIError as e:
            raise ProviderError(str(e)) from e
