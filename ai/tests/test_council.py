from chat_ai.council import rotate_lead, tally, weight


def test_lead_has_1_5_votes_others_1():
    assert weight(True) == 3
    assert weight(False) == 2


def test_three_votes_lead_plus_one_pass():
    # Lead (3) + one other (2) pass vs one reject (2): 5 > 3.5 -> pass.
    result = tally([("pass", True), ("pass", False), ("reject", False)])
    assert result.outcome == "pass"
    assert result.pass_weight == 5
    assert result.total_weight == 7


def test_lead_alone_cannot_decide():
    # Lead passes alone (3) vs two rejects (2+2): 3 is not more than half of 7.
    result = tally([("pass", True), ("reject", False), ("reject", False)])
    assert result.outcome == "reject"


def test_tie_of_four_is_decided_by_the_lead():
    # Lead + one pass (3+2=5) vs two reject (2+2=4): 5 > 4.5 -> pass.
    result = tally([("pass", True), ("pass", False), ("reject", False), ("reject", False)])
    assert result.outcome == "pass"


def test_lead_on_the_losing_side_loses_the_tie():
    result = tally([("reject", True), ("pass", False), ("pass", False), ("reject", False)])
    # Lead reject (3) + one reject (2) = 5 reject; two pass (2+2)=4. 5 > 4.5 -> reject.
    assert result.outcome == "reject"


def test_fewer_than_three_effective_votes_is_invalid():
    assert tally([("pass", True), ("pass", False)]).outcome == "invalid"
    assert tally([("pass", True)]).outcome == "invalid"


def test_rotate_lead_skips_the_proposer_and_walks_the_order():
    chairs = ["a", "b", "c"]
    assert rotate_lead(chairs, "a", None) == "b"
    assert rotate_lead(chairs, "b", None) == "a"
    assert rotate_lead(chairs, "a", "b") == "c"
    assert rotate_lead(chairs, "a", "c") == "b"
    # Proposer never leads, even if they were next.
    assert rotate_lead(["a", "b"], "a", None) == "b"
    assert rotate_lead(["a", "b", "c"], "c", None) == "a"