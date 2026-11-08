import json

import fakeredis

from chat_ai import recall
from chat_ai.context import allocate
from chat_ai.engine import Engine

from test_engine import MemoryKeys, settings

GATE_ALL_FALSE = json.dumps({layer: {"should_extract": False, "reasoning": "寒暄"} for layer in recall.LAYERS})


def test_gate_keeps_only_layers_marked_true():
    text = "```json\n" + json.dumps({
        "conclusion": {"should_extract": True, "reasoning": "定了方案"},
        "preference": {"should_extract": "yes", "reasoning": "not a bool"},
        "todo": {"should_extract": False, "reasoning": ""},
    }) + "\n```"
    assert recall.parse_gate(text) == ["conclusion"]
    assert recall.parse_gate("不是 JSON") == []
    assert recall.parse_gate(GATE_ALL_FALSE) == []


def test_extracted_memories_are_validated():
    text = json.dumps({"memories": [
        {"layer": "conclusion", "content": "数据库选用 MySQL"},
        {"layer": "conclusion", "content": "数据库选用 MySQL"},      # duplicate
        {"layer": "todo", "content": "不在本次允许的层里"},
        {"layer": "preference", "content": "喜欢简短回答", "user": "alice"},
        {"layer": "preference", "content": "陌生人的偏好", "user": "mallory"},  # not in room
        {"layer": "fact", "content": ""},
    ]})
    memories = recall.parse_memories(text, ["conclusion", "preference", "fact"], {"alice": 1})
    assert memories == [
        {"layer": "conclusion", "content": "数据库选用 MySQL", "user_id": None},
        {"layer": "preference", "content": "喜欢简短回答", "user_id": 1},
    ]


def test_memory_block_is_marked_as_reference_and_keeps_newest_in_budget():
    room = [{"layer": "fact", "content": "旧事实" * 20}, {"layer": "conclusion", "content": "数据库选用 MySQL"}]
    users = [{"layer": "preference", "content": "喜欢简短回答", "name": "alice"}]
    block = recall.memory_block(room, users, 120)
    assert "仅供参考" in block
    assert "[结论] 数据库选用 MySQL" in block
    assert "旧事实" not in block                      # the older, larger item did not fit
    assert "[alice的偏好] 喜欢简短回答" in block
    assert recall.memory_block([], [], 1000) == ""


def test_summary_input_rolls_the_previous_summary_forward():
    assert recall.summary_input("", "a：hi").startswith("a：hi")
    rolled = recall.summary_input("之前定了 MySQL", "a：改用 PG 吗")
    assert rolled.index("之前定了 MySQL") < rolled.index("a：改用 PG 吗")
    assert recall.summary_block("") == ""
    assert "<summary>摘要</summary>" in recall.summary_block("摘要")


def test_council_memory_keeps_minority_opinions():
    text = recall.council_memory("采用方案 A", "客观通过", ["B 更便宜"])
    assert "方案 A" in text and "客观通过" in text and "B 更便宜" in text


def test_budget_drops_retrieval_then_memory_then_history():
    # Plenty of room: everything stays.
    assert allocate(1000, 100, 100, 100) == (True, 100, 700)
    # Over: retrieval goes first, memory untouched.
    assert allocate(1000, 100, 300, 200) == (False, 200, 700)
    # Still over without retrieval: memory is cut down to keep half for history.
    assert allocate(1000, 100, 300, 600) == (False, 400, 500)
    # Even the summary alone exceeds the floor: memory goes entirely.
    keep, memory, history = allocate(1000, 800, 50, 50)
    assert (keep, memory) == (False, 0) and history == 200


# --- engine: extraction and segments with a scripted model -------------------

class Scripted:
    """A provider factory whose model answers from a queue."""

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def create(self, provider):
        outer = self

        class Provider:
            async def stream(self, *, model, system, messages, api_key, max_tokens, cache_prefix=False):
                outer.prompts.append((system, messages[-1].content))
                reply = outer.replies.pop(0)
                if reply is None:
                    from chat_ai.providers.base import ProviderError
                    raise ProviderError("down")
                yield reply
        return Provider()


class Store:
    """In-memory stand-ins for recall's SQL helpers."""

    def __init__(self, monkeypatch, messages, humans):
        self.summary, self.room, self.users, self.messages, self.humans = (0, ""), [], [], messages, humans

        async def load_summary(pool, room_id): return self.summary
        async def save_summary(pool, room_id, upto, content): self.summary = (upto, content)
        async def messages_between(pool, room_id, after, before, limit):
            return [m for m in self.messages if after < m["seq"] < before][:limit]
        async def load_room_memories(pool, ids, limit=40): return list(self.room)
        async def load_user_memories(pool, users, per_user=10): return list(self.users)
        async def save_memories(pool, room_id, seq, memories):
            for m in memories:
                (self.room if m["user_id"] is None else self.users).append(m)
        async def room_humans(pool, room_id): return dict(self.humans)

        for name, fn in list(locals().items()):
            if callable(fn) and name != "self":
                monkeypatch.setattr(recall, name, fn)


def engine_with(replies, **overrides):
    provider = Scripted(replies)
    engine = Engine(settings(summary_enabled=True, memory_enabled=True, summary_block=5, **overrides),
                    fakeredis.FakeAsyncRedis(decode_responses=True), object(),
                    MemoryKeys({(1, "deepseek"): "fake-d"}), {}, provider, lambda action: None)
    return engine, provider


async def test_small_talk_creates_no_memories(monkeypatch):
    store = Store(monkeypatch, [], {1: "alice"})
    engine, provider = engine_with([GATE_ALL_FALSE])
    assert await engine._extract_memories(1, "alice：早上好\nClaude：早！", 2, "fake-d") == []
    assert store.room == [] and store.users == []
    assert len(provider.prompts) == 1                     # no extraction call after the gate


async def test_decisions_become_room_and_user_memories(monkeypatch):
    store = Store(monkeypatch, [], {1: "alice"})
    gate = json.dumps({"conclusion": {"should_extract": True, "reasoning": ""},
                       "preference": {"should_extract": True, "reasoning": ""}})
    extract = json.dumps({"memories": [
        {"layer": "conclusion", "content": "数据库选用 MySQL"},
        {"layer": "preference", "content": "alice 希望回答尽量简短", "user": "alice"}]})
    engine, provider = engine_with([gate, extract])
    await engine._extract_memories(1, "alice：就用 MySQL，以后回答都简短点", 9, "fake-d")
    assert [m["content"] for m in store.room] == ["数据库选用 MySQL"]
    assert store.users[0]["user_id"] == 1
    assert "conclusion、preference" in provider.prompts[1][0]


async def test_segment_summarizes_aged_out_messages_and_moves_the_cursor(monkeypatch):
    messages = [{"seq": i, "name": "alice", "content": f"第{i}条"} for i in range(1, 31)]
    store = Store(monkeypatch, messages, {1: "alice"})
    engine, provider = engine_with(["摘要一", GATE_ALL_FALSE])
    await engine._process_segment(1, 1, window_start=21)
    assert store.summary == (20, "摘要一")
    assert "第1条" in provider.prompts[0][1] and "第21条" not in provider.prompts[0][1]
    assert json.loads(await engine.cache.get("ai:summary:1"))["content"] == "摘要一"
    # Too few new messages since the cursor: nothing to do.
    await engine._process_segment(1, 1, window_start=23)
    assert len(provider.prompts) == 2


async def test_failed_summary_keeps_the_cursor_for_next_time(monkeypatch):
    messages = [{"seq": i, "name": "alice", "content": "x"} for i in range(1, 31)]
    store = Store(monkeypatch, messages, {1: "alice"})
    engine, _ = engine_with([None])
    await engine._process_segment(1, 1, window_start=21)
    assert store.summary == (0, "")
    assert await engine.cache.get("ai:segment:lock:1") is None


async def test_recall_is_off_outside_collaboration(monkeypatch):
    Store(monkeypatch, [], {1: "alice"})
    engine, _ = engine_with([])
    from chat_ai.rules import RoomState
    assert await engine._recall(1, RoomState(collaboration_enabled=False)) == ("", [], [])


async def test_recall_injects_only_current_members_memories(monkeypatch):
    store = Store(monkeypatch, [], {1: "alice"})       # bob (user 2) is not in the room
    calls = []

    async def load_user_memories(pool, users, per_user=10):
        calls.append(dict(users))
        return []
    monkeypatch.setattr(recall, "load_user_memories", load_user_memories)
    engine, _ = engine_with([])
    from chat_ai.rules import RoomState
    await engine._recall(1, RoomState(collaboration_enabled=True))
    assert calls == [{1: "alice"}]
