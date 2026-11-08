"""OpenAI's chat completions API, which DeepSeek also speaks."""

import json

from typing import AsyncIterator

import httpx
import openai

from .base import ChatMessage, ToolCall, ToolTurn, KeyRejected, ProviderError, classify, retry_after


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

    async def complete(self, *, model: str, system: str, messages: list[ChatMessage], exchanges: list,
                       tools: list[dict], api_key: str, max_tokens: int) -> ToolTurn:
        """One non-streamed turn with tools (OpenAI function calling)."""
        payload = [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content} for m in messages]
        for exchange in exchanges:
            payload.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in exchange.calls]})
            payload += [{"role": "tool", "tool_call_id": call_id, "content": text}
                        for call_id, text in exchange.results]
        functions = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        options = {"extra_body": {"thinking": {"type": "disabled"}}} if self.disable_thinking else {}
        try:
            response = await self._client(api_key).chat.completions.create(
                model=model, messages=payload, max_tokens=max_tokens, tools=functions, **options)
        except openai.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except openai.APIStatusError as e:
            raise ProviderError(str(e), classify(e.status_code, str(e)),
                                retry_after(e.response.headers)) from e
        except openai.APIError as e:
            raise ProviderError(str(e)) from e
        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except ValueError:
                arguments = {}
            calls.append(ToolCall(call.id, call.function.name, arguments if isinstance(arguments, dict) else {}))
        return ToolTurn(message.content or "", tuple(calls))

    async def embed(self, *, model: str, texts: list[str], api_key: str,
                    dimensions: int | None = None) -> list[list[float]]:
        """Embeddings for `texts`, in order."""
        options = {"dimensions": dimensions} if dimensions else {}
        try:
            response = await self._client(api_key).embeddings.create(model=model, input=texts, **options)
        except openai.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except openai.APIStatusError as e:
            raise ProviderError(str(e), classify(e.status_code, str(e)),
                                retry_after(e.response.headers)) from e
        except openai.APIError as e:
            raise ProviderError(str(e)) from e
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    async def validate(self, api_key: str) -> None:
        try:
            await self._client(api_key).models.list()
        except openai.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except openai.APIError as e:
            raise ProviderError(str(e)) from e
