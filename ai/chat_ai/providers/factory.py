"""Picks the adapter for a provider name."""

from .anthropic_provider import AnthropicProvider
from .base import Provider
from .fake import FakeProvider
from .gemini_provider import GeminiProvider
from .openai_compatible import OpenAiCompatible


class ProviderFactory:
    def __init__(self, timeout: float, fake: bool = False):
        self.timeout = timeout
        self.fake = fake
        self._cache: dict[str, Provider] = {}

    def create(self, provider: str) -> Provider:
        if provider not in self._cache:
            self._cache[provider] = self._build(provider)
        return self._cache[provider]

    def _build(self, provider: str) -> Provider:
        if self.fake:
            # Tests replace every provider, so no real one is ever called.
            return FakeProvider(provider)
        if provider == "openai":
            return OpenAiCompatible("https://api.openai.com/v1", self.timeout)
        if provider == "deepseek":
            return OpenAiCompatible("https://api.deepseek.com", self.timeout, disable_thinking=True)
        if provider == "anthropic":
            return AnthropicProvider(self.timeout)
        if provider == "gemini":
            return GeminiProvider(self.timeout)
        raise ValueError(f"unknown provider {provider}")
