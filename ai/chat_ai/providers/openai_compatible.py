"""OpenAI's chat completions API, which DeepSeek also speaks."""

from typing import AsyncIterator

import httpx
import openai

from .base import ChatMessage, KeyRejected, ProviderError, classify, retry_after


class OpenAiCompatible:
    def __init__(self, base_url: str, timeout: float, http_client: httpx.AsyncClient | None = None,
                 *, disable_thinking: bool = False):
        self.base_url = base_url
        self.timeout = timeout
        self.http_client = http_client
        self.disable_thinking = disable_thinking

    def _client(self, api_key: str) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=0,
            http_client=self.http_client,
        )

    async def stream(
        self, *, model: str, system: str, messages: list[ChatMessage], api_key: str, max_tokens: int,
        cache_prefix: bool = False,
    ) -> AsyncIterator[str]:
        payload = [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content} for m in messages
        ]
        # DeepSeek defaults to thinking mode; casual chat should not spend tokens on it.
        options = {"extra_body": {"thinking": {"type": "disabled"}}} if self.disable_thinking else {}
        try:
            response = await self._client(api_key).chat.completions.create(
                model=model, messages=payload, max_tokens=max_tokens, stream=True, **options
            )
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except openai.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except openai.APIStatusError as e:
            raise ProviderError(str(e), classify(e.status_code, str(e)),
                                retry_after(e.response.headers)) from e
        except openai.APIError as e:
            raise ProviderError(str(e)) from e

    async def validate(self, api_key: str) -> None:
        try:
            await self._client(api_key).models.list()
        except openai.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except openai.APIError as e:
            raise ProviderError(str(e)) from e
