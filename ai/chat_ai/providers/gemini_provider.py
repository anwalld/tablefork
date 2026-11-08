"""Google's Gemini API."""

from typing import AsyncIterator

from google import genai
from google.genai import errors, types

from .base import ChatMessage, KeyRejected, ProviderError, alternate, classify

# Gemini reports a bad key as 400 INVALID_ARGUMENT, 401 or 403.
_REJECTED = {400, 401, 403}


def _rejected(error: errors.APIError) -> bool:
    return error.code in _REJECTED and "key" in str(error).lower()


class GeminiProvider:
    def __init__(self, timeout: float, base_url: str | None = None):
        self.timeout = timeout
        self.base_url = base_url

    def _client(self, api_key: str) -> genai.Client:
        options = types.HttpOptions(timeout=int(self.timeout * 1000), base_url=self.base_url)
        return genai.Client(api_key=api_key, http_options=options)

    async def stream(
        self, *, model: str, system: str, messages: list[ChatMessage], api_key: str, max_tokens: int,
        cache_prefix: bool = False,
    ) -> AsyncIterator[str]:
        contents = [
            types.Content(role="model" if m.role == "assistant" else "user", parts=[types.Part(text=m.content)])
            for m in alternate(messages)
        ]
        config = types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens)
        try:
            response = await self._client(api_key).aio.models.generate_content_stream(
                model=model, contents=contents, config=config
            )
            async for chunk in response:
                feedback = getattr(chunk, "prompt_feedback", None)
                if feedback is not None and getattr(feedback, "block_reason", None):
                    raise ProviderError(f"prompt blocked: {feedback.block_reason}", "refused")
                if chunk.text:
                    yield chunk.text
        except errors.APIError as e:
            if _rejected(e):
                raise KeyRejected(str(e)) from e
            raise ProviderError(str(e), classify(e.code, str(e))) from e

    async def validate(self, api_key: str) -> None:
        try:
            pager = await self._client(api_key).aio.models.list(config={"page_size": 1})
            async for _ in pager:
                break
        except errors.APIError as e:
            if _rejected(e):
                raise KeyRejected(str(e)) from e
            raise ProviderError(str(e)) from e
