"""Settings, all read from the environment."""

import base64
import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    value = os.environ.get(name, "")
    return float(value) if value else default


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    grpc_port: int
    grpc_token: str
    redis_host: str
    redis_port: int
    redis_password: str
    mysql_host: str
    mysql_user: str
    mysql_password: str
    mysql_database: str
    encryption_key: bytes
    agents_file: str
    fake_providers: bool
    chain_p: float
    proactive_p1: float
    idle_seconds: float
    timer_poll_seconds: float
    context_messages: int
    context_chars: int
    max_output_tokens: int
    generation_timeout: float
    chunk_interval: float
    control_poll_seconds: float = 1.0
    generation_max_attempts: int = 3
    retry_base_seconds: float = 1.0
    work_lease_seconds: float = 10.0
    # Optional context and retry behavior. Every default keeps the original.
    strip_self_name: bool = False
    context_block: int = 0
    context_tokens: int = 0
    retrieval_enabled: bool = False
    retrieval_top_k: int = 5
    retrieval_chars: int = 1500
    classified_retry: bool = False
    # Collaboration host (group supervisor). Uses the round trigger's key.
    host_provider: str = "deepseek"
    host_model: str = "deepseek-flash"
    round_max_replies: int = 12

    @classmethod
    def from_env(cls) -> "Settings":
        key = base64.b64decode(os.environ["AI_KEY_ENCRYPTION_KEY"])
        if len(key) != 32:
            raise ValueError("AI_KEY_ENCRYPTION_KEY must be 32 bytes, base64 encoded")
        token = os.environ["AI_GRPC_TOKEN"]
        if not token or token == "change-me":
            raise ValueError("AI_GRPC_TOKEN must be set to a random value")
        return cls(
            grpc_port=_int("AI_GRPC_PORT", 50051),
            grpc_token=token,
            redis_host=os.environ.get("REDIS_HOST", "redis-proxy"),
            redis_port=_int("REDIS_PORT", 6379),
            redis_password=os.environ.get("REDIS_PASSWORD", ""),
            mysql_host=os.environ.get("MYSQL_HOST", "mysql"),
            mysql_user=os.environ.get("AI_MYSQL_USER", "ai"),
            mysql_password=os.environ["AI_MYSQL_PASSWORD"],
            mysql_database=os.environ.get("MYSQL_DATABASE", "chat"),
            encryption_key=key,
            agents_file=os.environ.get("AI_AGENTS_FILE", "/app/agents.toml"),
            fake_providers=os.environ.get("AI_FAKE_PROVIDERS", "") == "1",
            chain_p=_float("AI_CHAIN_P", 0.5),
            proactive_p1=_float("AI_PROACTIVE_P1", 0.5),
            idle_seconds=_float("AI_IDLE_SECONDS", 1800),
            timer_poll_seconds=_float("AI_TIMER_POLL_SECONDS", 5),
            context_messages=_int("AI_CONTEXT_MESSAGES", 30),
            context_chars=_int("AI_CONTEXT_CHARS", 12000),
            max_output_tokens=_int("AI_MAX_OUTPUT_TOKENS", 1500),
            generation_timeout=_float("AI_GENERATION_TIMEOUT", 60),
            chunk_interval=_float("AI_CHUNK_INTERVAL", 0.1),
            control_poll_seconds=max(0.05, _float("AI_CONTROL_POLL_SECONDS", 1.0)),
            generation_max_attempts=max(1, _int("AI_GENERATION_MAX_ATTEMPTS", 3)),
            retry_base_seconds=max(0.1, _float("AI_RETRY_BASE_SECONDS", 1)),
            work_lease_seconds=max(1.0, _float("AI_WORK_LEASE_SECONDS", 10)),
            strip_self_name=os.environ.get("AI_STRIP_SELF_NAME", "") == "1",
            context_block=max(0, _int("AI_CONTEXT_BLOCK", 0)),
            context_tokens=max(0, _int("AI_CONTEXT_TOKENS", 0)),
            retrieval_enabled=os.environ.get("AI_RETRIEVAL_ENABLED", "") == "1",
            retrieval_top_k=max(1, min(20, _int("AI_RETRIEVAL_TOP_K", 5))),
            retrieval_chars=max(100, _int("AI_RETRIEVAL_CHARS", 1500)),
            classified_retry=os.environ.get("AI_CLASSIFIED_RETRY", "") == "1",
            host_provider=os.environ.get("AI_HOST_PROVIDER", "deepseek"),
            host_model=os.environ.get("AI_HOST_MODEL", "deepseek-flash"),
            round_max_replies=max(1, _int("AI_ROUND_MAX_REPLIES", 12)),
        )
