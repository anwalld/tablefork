"""Anthropic's messages API (Claude)."""

from typing import AsyncIterator

import anthropic

from .base import ChatMessage, ToolCall, ToolTurn, KeyRejected, ProviderError, alternate, classify, retry_after

_CACHE = {"type": "ephemeral"}


def request_parts(system: str, messages: list[ChatMessage], cache_prefix: bool):
    """System and messages for the API; with cache_prefix, marks the stable prefix."""
    turns = [{"role": m.role, "content": m.content} for m in alternate(messages)]
    if not cache_prefix:
        return system, turns
    if len(turns) >= 2:
        # Everything before the newest turn repeats on the next request.
        turns[-2] = {"role": turns[-2]["role"],
                     "content": [{"type": "text", "text": turns[-2]["content"], "cache_control": _CACHE}]}
    return [{"type": "text", "text": system, "cache_control": _CACHE}], turns


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
        self, *, model: str, system: str, messages: list[ChatMessage], api_key: str, max_tokens: int,
        cache_prefix: bool = False,
    ) -> AsyncIterator[str]:
        try:
            system_part, turns = request_parts(system, messages, cache_prefix)
            response = await self._client(api_key).messages.create(
                model=model,
                system=system_part,
                messages=turns,
                max_tokens=max_tokens,
                stream=True,
            )
            async for event in response:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    yield event.delta.text
        except anthropic.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(str(e), classify(e.status_code, str(e)),
                                retry_after(e.response.headers)) from e
        except anthropic.APIError as e:
            raise ProviderError(str(e)) from e

    async def complete(self, *, model: str, system: str, messages: list[ChatMessage], exchanges: list,
                       tools: list[dict], api_key: str, max_tokens: int) -> ToolTurn:
        """One non-streamed turn with tools (Anthropic tool use)."""
        turns = [{"role": m.role, "content": m.content} for m in alternate(messages)]
        for exchange in exchanges:
            turns.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in exchange.calls]})
            turns.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": text}
                for call_id, text in exchange.results]})
        definitions = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                       for t in tools]
        try:
            response = await self._client(api_key).messages.create(
                model=model, system=system, messages=turns, max_tokens=max_tokens, tools=definitions)
        except anthropic.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(str(e), classify(e.status_code, str(e)),
                                retry_after(e.response.headers)) from e
        except anthropic.APIError as e:
            raise ProviderError(str(e)) from e
        text = "".join(block.text for block in response.content if block.type == "text")
        calls = tuple(ToolCall(block.id, block.name, dict(block.input or {}))
                      for block in response.content if block.type == "tool_use")
        return ToolTurn(text, calls)

    async def validate(self, api_key: str) -> None:
        try:
            await self._client(api_key).models.list(limit=1)
        except anthropic.AuthenticationError as e:
            raise KeyRejected(str(e)) from e
        except anthropic.APIError as e:
            raise ProviderError(str(e)) from e
