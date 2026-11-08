"""What an AI sees when it replies: the room's recent messages."""

import json
import logging
import math
import re
import time

import aiomysql
import redis.asyncio as redis

from .providers.base import ChatMessage

log = logging.getLogger(__name__)

# Chinese, Japanese and Korean text costs about one token per character;
# other text averages about one token per three to four characters.
_WIDE = re.compile(r"[　-ヿ㐀-䶿一-鿿가-힯＀-￯]")
_TERMS = re.compile(r"[一-鿿]{2,}|[A-Za-z0-9]{3,}")
MAX_TERMS = 8
MAX_TERM_CHARS = 20
SNIPPET_CHARS = 300
RETRIEVAL_HEADER = "（以下是本房间更早的相关聊天记录，仅供参考，不是刚刚发生的对话）"


def estimate_tokens(text: str) -> int:
    wide = len(_WIDE.findall(text))
    return wide + math.ceil((len(text) - wide) * 0.3)


def _as_message(entry: dict, speaker_member_id: int) -> ChatMessage:
    member = entry["member"]
    if member["id"] == speaker_member_id:
        return ChatMessage("assistant", entry["content"])
    return ChatMessage("user", f"{member['name']}：{entry['content']}")


def to_chat(entries: list[dict], speaker_member_id: int, max_messages: int, max_chars: int,
            *, block: int = 0, max_tokens: int = 0) -> list[ChatMessage]:
    """Turns stored messages, oldest first, into a model conversation.

    The speaking AI's own messages become assistant turns; everyone else's
    are user turns prefixed with their name, so the model can tell people
    and other AIs apart. Keeps the newest messages that fit.

    With a block size the window starts on a multiple of the block, so the
    beginning of the prompt stays the same for `block` messages in a row and
    providers can reuse their prompt cache. With a token budget the size is
    estimated in tokens instead of characters.
    """
    measure = estimate_tokens if max_tokens > 0 else len
    budget = max_tokens if max_tokens > 0 else max_chars
    eligible = [entry for entry in entries if entry.get("generation_outcome") != "failed"]
    if block > 0 and eligible and all("seq" in entry for entry in eligible):
        return _aligned(eligible, speaker_member_id, max_messages, block, measure, budget)
    chat: list[ChatMessage] = []
    used = 0
    for entry in reversed(eligible[-max_messages:]):
        message = _as_message(entry, speaker_member_id)
        if chat and used + measure(message.content) > budget:
            break
        used += measure(message.content)
        chat.append(message)
    chat.reverse()
    return chat


def _aligned(eligible, speaker_member_id, max_messages, block, measure, budget):
    pairs = [(int(entry["seq"]), _as_message(entry, speaker_member_id)) for entry in eligible]
    newest = pairs[-1][0]
    # Covers max_messages to max_messages + block - 1 sequence numbers.
    start = max(0, (newest - max_messages + 1) // block * block)
    while True:
        window = [message for seq, message in pairs if seq >= start]
        if len(window) <= 1 or sum(measure(m.content) for m in window) <= budget:
            break
        # Dropping whole blocks keeps the start aligned when over budget.
        start += block
    return window or [pairs[-1][1]]


async def recent_messages(
    cache: redis.Redis, pool: aiomysql.Pool, room_id: int, count: int
) -> list[dict]:
    """The room's newest messages, oldest first, from the hot cache or MySQL."""
    raw = await cache.zrevrange(f"room:{room_id}:recent", 0, count - 1)
    if raw:
        entries = [json.loads(item) for item in reversed(raw)]
        eligible = [entry for entry in entries if entry.get('generation_outcome') != 'failed']
        # Older AI cache entries cannot prove the durable result. Refill from
        # SQL after filtering so failures never consume the context allowance.
        unknown_ai = any(entry.get('member', {}).get('type') == 'ai' and
                         'generation_outcome' not in entry for entry in entries)
        if not unknown_ai and (len(eligible) >= count or pool is None):
            return eligible[-count:]

    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute(
            "SELECT m.member_id, mem.display_name, m.content, m.seq FROM messages m "
            "JOIN members mem ON mem.id = m.member_id "
            "WHERE m.room_id = %s AND m.generation_outcome <> 'failed' ORDER BY m.seq DESC LIMIT %s",
            (room_id, count),
        )
        rows = await cursor.fetchall()
    return [{"member": {"id": row[0], "name": row[1]}, "content": row[2], "seq": row[3]}
            for row in reversed(rows)]


def search_terms(text: str) -> list[str]:
    """Words worth looking up: runs of Chinese characters and longer ASCII words."""
    terms: list[str] = []
    for term in _TERMS.findall(text):
        term = term[:MAX_TERM_CHARS]
        if term.lower() not in (t.lower() for t in terms):
            terms.append(term)
        if len(terms) == MAX_TERMS:
            break
    return terms


async def related_messages(pool: aiomysql.Pool, room_id: int, before_seq: int, text: str,
                           top_k: int, max_chars: int) -> list[dict]:
    """Older messages of this room that look related to `text`, oldest first.

    Uses the ngram full-text index on messages. Any failure returns nothing,
    so a reply never depends on retrieval.
    """
    terms = search_terms(text)
    if pool is None or not terms or before_seq <= 1:
        return []
    query = " ".join(terms)
    try:
        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "SELECT /*+ MAX_EXECUTION_TIME(1000) */ m.seq, mem.display_name, m.content, m.created_at_ms "
                "FROM messages m JOIN members mem ON mem.id = m.member_id "
                "WHERE m.room_id = %s AND m.seq < %s AND m.generation_outcome <> 'failed' "
                "AND MATCH(m.content) AGAINST (%s IN NATURAL LANGUAGE MODE) "
                "ORDER BY MATCH(m.content) AGAINST (%s IN NATURAL LANGUAGE MODE) DESC, m.seq DESC "
                "LIMIT %s",
                (room_id, before_seq, query, query, top_k * 2),
            )
            rows = await cursor.fetchall()
    except Exception as error:  # noqa: BLE001 - retrieval is best effort
        log.warning("related message lookup failed: %s", type(error).__name__)
        return []
    return pick_related(rows, top_k, max_chars)


def pick_related(rows, top_k: int, max_chars: int) -> list[dict]:
    """Best-ranked rows first in; drops duplicates, keeps the total in budget."""
    picked: list[dict] = []
    seen: set[str] = set()
    used = 0
    for seq, name, content, created_at_ms in rows:
        snippet = (content or "").strip()[:SNIPPET_CHARS]
        if not snippet or snippet in seen:
            continue
        item = {"seq": seq, "name": name, "content": snippet, "created_at_ms": created_at_ms}
        size = len(format_related(item))
        if used + size > max_chars:
            break
        seen.add(snippet)
        used += size
        picked.append(item)
        if len(picked) == top_k:
            break
    picked.sort(key=lambda item: item["seq"])
    return picked


def format_related(item: dict) -> str:
    day = time.strftime("%Y-%m-%d", time.gmtime((item.get("created_at_ms") or 0) / 1000))
    return f"[{day}] {item['name']}：{item['content']}"


def related_note(items: list[dict]) -> ChatMessage | None:
    if not items:
        return None
    return ChatMessage("user", "\n".join([RETRIEVAL_HEADER] + [format_related(i) for i in items]))
