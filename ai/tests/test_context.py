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
