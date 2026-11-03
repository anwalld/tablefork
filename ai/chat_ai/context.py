"""What an AI sees when it replies: the room's recent messages."""

import json

import aiomysql
import redis.asyncio as redis

from .providers.base import ChatMessage


def to_chat(entries: list[dict], speaker_member_id: int, max_messages: int, max_chars: int) -> list[ChatMessage]:
    """Turns stored messages, oldest first, into a model conversation.

    The speaking AI's own messages become assistant turns; everyone else's
    are user turns prefixed with their name, so the model can tell people
    and other AIs apart. Keeps the newest messages that fit.
    """
    chat: list[ChatMessage] = []
    used = 0
    for entry in reversed(entries[-max_messages:]):
        member = entry["member"]
        if member["id"] == speaker_member_id:
            message = ChatMessage("assistant", entry["content"])
        else:
            message = ChatMessage("user", f"{member['name']}：{entry['content']}")
        if chat and used + len(message.content) > max_chars:
            break
        used += len(message.content)
        chat.append(message)
    chat.reverse()
    return chat


async def recent_messages(
    cache: redis.Redis, pool: aiomysql.Pool, room_id: int, count: int
) -> list[dict]:
    """The room's newest messages, oldest first, from the hot cache or MySQL."""
    raw = await cache.zrevrange(f"room:{room_id}:recent", 0, count - 1)
    if raw:
        return [json.loads(item) for item in reversed(raw)]
    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute(
            "SELECT m.member_id, mem.display_name, m.content FROM messages m "
            "JOIN members mem ON mem.id = m.member_id "
            "WHERE m.room_id = %s ORDER BY m.seq DESC LIMIT %s",
            (room_id, count),
        )
        rows = await cursor.fetchall()
    return [{"member": {"id": row[0], "name": row[1]}, "content": row[2]} for row in reversed(rows)]
