from chat_ai.context import to_chat


def entry(member_id, name, content):
    return {"member": {"id": member_id, "name": name}, "content": content}


def test_roles_and_names():
    entries = [entry(1, "alice", "hi"), entry(100, "Claude", "hello"), entry(101, "GPT", "hey")]
    chat = to_chat(entries, 100, 30, 10_000)
    assert [(m.role, m.content) for m in chat] == [
        ("user", "alice：hi"),
        ("assistant", "hello"),
        ("user", "GPT：hey"),
    ]


def test_keeps_the_newest_that_fit():
    entries = [entry(1, "a", str(i) * 10) for i in range(10)]
    by_count = to_chat(entries, 100, 3, 10_000)
    assert [m.content for m in by_count] == ["a：" + str(i) * 10 for i in (7, 8, 9)]
    by_chars = to_chat(entries, 100, 30, 30)
    assert [m.content for m in by_chars] == ["a：" + str(i) * 10 for i in (8, 9)]


def test_a_single_long_message_is_kept():
    chat = to_chat([entry(1, "a", "x" * 100)], 100, 30, 10)
    assert len(chat) == 1


def test_failed_entries_are_excluded_before_applying_count_limit():
    entries = [entry(1, 'alice', 'older'), entry(100, 'Claude', 'complete'),
               {**entry(100, 'Claude', 'failed text'), 'generation_outcome':'failed'},
               {**entry(100, 'Claude', ''), 'generation_outcome':'failed'}]
    assert [message.content for message in to_chat(entries,100,2,1000)] == ['alice：older','complete']


def seq_entries(count, start=1, size=1):
    return [{**entry(1, "a", "x" * size), "seq": seq} for seq in range(start, start + count)]


def test_estimate_tokens_counts_wide_characters_as_one():
    from chat_ai.context import estimate_tokens
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("hello world") == 4  # 11 * 0.3 rounded up
    assert estimate_tokens("") == 0


def test_token_budget_replaces_the_character_budget():
    entries = [entry(1, "a", "你好" * 10) for _ in range(5)]  # 23 tokens each
    assert len(to_chat(entries, 100, 30, 10_000, max_tokens=50)) == 2
    assert len(to_chat(entries, 100, 30, 10_000)) == 5


def test_block_window_keeps_its_start_until_a_block_fills():
    first = to_chat(seq_entries(45), 100, 30, 10_000, block=10)
    assert len(first) == 36  # seqs 10..45
    for newest in range(46, 49):
        window = to_chat(seq_entries(newest), 100, 30, 10_000, block=10)
        assert window[0] == first[0] and window[: len(first)] == first
    moved = to_chat(seq_entries(49), 100, 30, 10_000, block=10)
    assert len(moved) == 30  # seqs 20..49
    for newest in range(30, 200):
        assert 30 <= len(to_chat(seq_entries(newest), 100, 30, 10_000, block=10)) <= 39


def test_block_window_drops_whole_blocks_when_over_budget():
    entries = seq_entries(45, size=10)  # each message is 12 characters
    window = to_chat(entries, 100, 30, 200, block=10)
    assert len(window) == 16  # seqs 30..45 fit, 20..45 do not
    assert len(to_chat(seq_entries(3, size=500), 100, 30, 10, block=10)) == 1


def test_block_window_falls_back_without_sequence_numbers():
    entries = [entry(1, "a", str(i)) for i in range(40)]
    assert to_chat(entries, 100, 30, 10_000, block=10) == to_chat(entries, 100, 30, 10_000)


def test_search_terms():
    from chat_ai.context import search_terms
    assert search_terms("上周说的那家火锅店 in Chengdu, ok?") == ["上周说的那家火锅店", "Chengdu"]
    assert search_terms("a b 我") == []
    assert search_terms(" ".join(f"word{i}" for i in range(20)))[-1] == "word7"
    assert search_terms("Hello hello") == ["Hello"]


def test_related_rows_are_deduplicated_budgeted_and_ordered_by_time():
    from chat_ai.context import format_related, pick_related, related_note
    rows = [(9, "bob", "火锅店在春熙路", 0), (3, "amy", "火锅店在春熙路", 0),
            (5, "amy", "那家火锅很辣" + "!" * 400, 0), (7, "bob", "再去火锅", 0)]
    picked = pick_related(rows, 5, 10_000)
    assert [p["seq"] for p in picked] == [5, 7, 9]
    assert len(picked[0]["content"]) == 300
    assert [p["seq"] for p in pick_related(rows, 2, 10_000)] == [5, 9]
    assert [p["seq"] for p in pick_related(rows, 5, 40)] == [9]
    note = related_note(picked)
    assert note.role == "user" and note.content.splitlines()[1] == format_related(picked[0])
    assert format_related(picked[1]) == "[1970-01-01] bob：再去火锅"
    assert related_note([]) is None


async def test_related_lookup_failures_return_nothing():
    from chat_ai.context import related_messages

    class Broken:
        def acquire(self):
            raise RuntimeError("down")

    assert await related_messages(Broken(), 1, 50, "火锅店", 5, 1500) == []
    assert await related_messages(None, 1, 50, "火锅店", 5, 1500) == []
