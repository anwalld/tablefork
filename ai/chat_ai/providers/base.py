"""What every model provider offers: streamed text and key validation."""

from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolTurn:
    """One model turn in a tool loop: final text, or tool calls to run."""
    text: str
    calls: tuple = ()


@dataclass(frozen=True)
class ToolExchange:
    """Calls the model made and their results, replayed on the next turn."""
    calls: tuple   # ToolCall
    results: tuple  # (call id, result text)


class KeyRejected(Exception):
    """The provider refused the API key."""


class ProviderError(Exception):
    """Any other failure: network, rate limit, server error.

    `kind` is one of "rate_limit", "context_too_long", "refused" or "other";
    `retry_after` is the provider's suggested wait in seconds, if it gave one.
    """

    def __init__(self, message: str = "", kind: str = "other", retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


_TOO_LONG = ("context_length", "context length", "maximum context", "prompt is too long",
             "too many tokens", "input token count", "exceeds the maximum")
_REFUSED = ("content_filter", "content_policy", "content management", "safety", "blocked")


def classify(status: int | None, message: str) -> str:
    """Maps an HTTP status and error text to a ProviderError kind."""
    text = message.lower()
    if status in (429, 529) or "overloaded" in text:
        return "rate_limit"
    if status in (400, 413) and any(word in text for word in _TOO_LONG):
        return "context_too_long"
    if status == 400 and any(word in text for word in _REFUSED):
        return "refused"
    return "other"


def retry_after(headers) -> float | None:
    """Seconds from a Retry-After header, when it is a number."""
    try:
        value = float((headers or {}).get("retry-after", ""))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


class Provider(Protocol):
    def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[ChatMessage],
        api_key: str,
        max_tokens: int,
        cache_prefix: bool = False,
    ) -> AsyncIterator[str]:
        """Yields the reply in pieces. Raises KeyRejected or ProviderError.

        cache_prefix asks providers with explicit prompt caching to mark the
        system prompt and the history before the newest turn as cacheable.
        """
        ...

    async def validate(self, api_key: str) -> None:
        """Raises KeyRejected unless the key works, ProviderError if unsure."""
        ...


def alternate(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Merges consecutive messages of the same role.

    Some providers require user and assistant turns to alternate and the
    first turn to come from the user.
    """
    merged: list[ChatMessage] = []
    for message in messages:
        if merged and merged[-1].role == message.role:
            merged[-1] = ChatMessage(message.role, merged[-1].content + "\n" + message.content)
        else:
            merged.append(message)
    if merged and merged[0].role == "assistant":
        merged.insert(0, ChatMessage("user", "（聊天开始）"))
    return merged
