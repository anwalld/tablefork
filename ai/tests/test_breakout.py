from chat_ai.breakout import deduct, exhausted


def test_deduct_consumes_every_level():
    assert deduct([3, 20, 20]) == [2, 19, 19]


def test_deduct_refuses_when_any_level_is_empty():
    chain = [3, 0, 20]
    assert deduct(chain) == chain  # nothing consumed anywhere


def test_deduct_consumes_the_last_child_reply():
    # Child has exactly one left: the reply succeeds and drains the child.
    assert deduct([1, 20]) == [0, 19]
    # The next reply is refused at the child.
    assert deduct([0, 19]) == [0, 19]


def test_exhausted_detects_a_child_who_spent_a_parent_out():
    assert exhausted([0, 12]) is True
    assert exhausted([2, 2]) is False
    assert exhausted([]) is True


def test_child_spend_counts_against_the_parent():
    chain = [20, 20]
    for _ in range(8):
        chain = deduct(chain)
    assert chain == [12, 12]


def test_empty_parent_blocks_the_child():
    chain = [5, 0]
    assert deduct(chain) == chain
    assert exhausted(chain) is True

from chat_ai.breakout import conclusion_content


def test_conclusion_content_lists_topic_participants_and_summary():
    content = conclusion_content("核对日志", ["分析者", "审查者"], "14:02 的报错是空指针")
    assert "核对日志" in content
    assert "分析者" in content and "审查者" in content
    assert "空指针" in content


def test_conclusion_content_allows_an_empty_summary():
    content = conclusion_content("回顾", ["分析者"], "")
    assert "【小会】回顾" in content
    assert "结论：" not in content
