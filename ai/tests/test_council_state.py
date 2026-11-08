from chat_ai.council_state import Ballot


def ballot():
    return Ballot(round=3, topic="采用方案 A", proposer=1, lead_slug="a",
                  chairs=["a", "b", "c"], deadline=9999.0)


def test_a_chair_votes_once_only():
    b = ballot()
    assert b.record("a", "pass", "ok")
    assert not b.record("a", "reject", "changed my mind")
    assert b.votes["a"]["verdict"] == "pass"


def test_unknown_chair_or_bad_verdict_cannot_vote():
    b = ballot()
    assert not b.record("x", "pass", "no")
    assert not b.record("a", "maybe", "no")


def test_abstain_does_not_count_but_completes_the_round():
    b = ballot()
    b.record("a", "abstain", "no opinion")
    assert b.effective() == []
    b.record("b", "pass", "yes")
    b.record("c", "reject", "no")
    # a 弃权不计票：b(2) vs c(2) = 2:2，不通过（不到 3 有效票）
    assert b.tally().outcome == "invalid"


def test_three_pass_and_tally_marks_the_lead():
    b = ballot()
    b.record("a", "pass", "lead yes")   # lead, weight 3
    b.record("b", "pass", "yes")        # weight 2
    b.record("c", "reject", "no")       # weight 2
    assert b.complete()
    result = b.tally()
    assert result.outcome == "pass"
    assert (result.pass_weight, result.total_weight) == (5, 7)


def test_round_trips_through_json():
    b = ballot()
    b.record("a", "pass", "yes")
    assert Ballot.from_json(b.to_json()) == b