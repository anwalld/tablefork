"""What every model provider offers: streamed text and key validation."""

from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


class KeyRejected(Exception):
    """The provider refused the API key."""


class ProviderError(Exception):
    """Any other failure: network, rate limit, server error."""


class Provider(Protocol):
    def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[ChatMessage],
        api_key: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Yields the reply in pieces. Raises KeyRejected or ProviderError."""
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
