import random

import pytest

from chat_ai import rules
from chat_ai.rules import Human, RoomAi, RoomState

ALICE = Human(10, 1)
BOB = Human(11, 2)
CLAUDE = RoomAi(100, "claude", "anthropic", "Claude")
GPT = RoomAi(101, "gpt", "openai", "GPT")
ALL_KEYS = {"anthropic", "openai", "deepseek", "gemini"}


class FixedRandom(random.Random):
    """random() returns a fixed value; choice() picks the first."""

    def __init__(self, value=0.0):
        super().__init__()
        self.value = value

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[0]


def one_human(*ais):
    return RoomState(humans=[ALICE], ais=list(ais))


def slugs(decision):
    return [r.slug for r in decision.requests]


def test_mentions_are_case_insensitive_and_ordered():
    ais = [CLAUDE, GPT]
    assert rules.mentions("hi @gpt and @CLAUDE", ais) == ["gpt", "claude"]
    assert rules.mentions("hi @gemini", ais) == []


def test_multi_human_room_replies_only_to_mentions():
    state = RoomState(humans=[ALICE, BOB], ais=[CLAUDE, GPT])
    assert slugs(rules.on_human_message(state, ALICE, "hello", ALL_KEYS, FixedRandom())) == []
    assert slugs(rules.on_human_message(state, ALICE, "@claude hi", ALL_KEYS, FixedRandom())) == ["claude"]
    both = rules.on_human_message(state, ALICE, "@claude @gpt hi", ALL_KEYS, FixedRandom())
    assert slugs(both) == ["claude", "gpt"]
    assert all(r.payer == ALICE for r in both.requests)


def test_one_human_room_picks_and_keeps_a_partner():
    state = one_human(CLAUDE, GPT)
    first = rules.on_human_message(state, ALICE, "hi", ALL_KEYS, FixedRandom())
    assert slugs(first) == ["claude"]
    assert state.target == "claude"
    # Later messages stay with the partner even if the random pick would differ.
    rng = random.Random(1)
    for _ in range(10):
        assert slugs(rules.on_human_message(state, ALICE, "more", ALL_KEYS, rng)) == ["claude"]


def test_mention_switches_partner():
    state = one_human(CLAUDE, GPT)
    rules.on_human_message(state, ALICE, "hi", ALL_KEYS, FixedRandom())
    assert slugs(rules.on_human_message(state, ALICE, "@gpt you?", ALL_KEYS, FixedRandom())) == ["gpt"]
    assert slugs(rules.on_human_message(state, ALICE, "and now", ALL_KEYS, FixedRandom())) == ["gpt"]


def test_random_pick_only_among_ais_with_keys():
    state = one_human(CLAUDE, GPT)
    decision = rules.on_human_message(state, ALICE, "hi", {"openai"}, FixedRandom())
    assert slugs(decision) == ["gpt"]


def test_no_usable_ai_tells_the_human():
    state = one_human(CLAUDE)
    decision = rules.on_human_message(state, ALICE, "hi", set(), FixedRandom())
    assert decision.requests == []
    assert decision.missing == [rules.MissingKey(ALICE, "")]


def test_mentioned_ai_without_key_tells_the_payer():
    state = RoomState(humans=[ALICE, BOB], ais=[CLAUDE])
    decision = rules.on_human_message(state, BOB, "@claude hi", {"openai"}, FixedRandom())
    assert decision.requests == []
    assert decision.missing == [rules.MissingKey(BOB, "anthropic")]


def test_human_message_resets_proactive_count_and_bumps_epoch():
    state = one_human(CLAUDE)
    state.proactive_mask = 3
    rules.on_human_message(state, ALICE, "back", ALL_KEYS, FixedRandom())
    assert state.proactive_mask == 0
    assert state.epoch == 1


@pytest.mark.parametrize("hop,expected", [(2, 1.0), (3, 0.5), (4, 0.25)])
def test_chain_probability_halves_per_hop(hop, expected):
    assert rules.chain_probability(1.0, hop) == expected


def test_chain_with_p_one_picks_another_ai():
    state = one_human(CLAUDE, GPT)
    decision = rules.on_ai_message(state, "claude", "hey", ALICE, 1, ALL_KEYS, 1.0, FixedRandom(0.99))
    assert slugs(decision) == ["gpt"]
    assert decision.requests[0].hop == 2
    assert decision.requests[0].payer == ALICE


def test_chain_with_p_zero_stops():
    state = one_human(CLAUDE, GPT)
    assert slugs(rules.on_ai_message(state, "claude", "hey", ALICE, 1, ALL_KEYS, 0.0, FixedRandom(0.0))) == []


def test_chain_stops_when_the_draw_misses():
    state = one_human(CLAUDE, GPT)
    # Hop 3 has probability 0.5 with p = 1; a draw of 0.6 misses.
    assert slugs(rules.on_ai_message(state, "gpt", "hey", ALICE, 2, ALL_KEYS, 1.0, FixedRandom(0.6))) == []
    assert slugs(rules.on_ai_message(state, "gpt", "hey", ALICE, 2, ALL_KEYS, 1.0, FixedRandom(0.4))) == ["claude"]


def test_chain_in_multi_human_room_needs_a_mention():
    state = RoomState(humans=[ALICE, BOB], ais=[CLAUDE, GPT])
    assert slugs(rules.on_ai_message(state, "claude", "hey", ALICE, 1, ALL_KEYS, 1.0, FixedRandom())) == []
    assert slugs(rules.on_ai_message(state, "claude", "@GPT thoughts?", ALICE, 1, ALL_KEYS, 1.0, FixedRandom())) == ["gpt"]


def test_proactive_probabilities():
    assert rules.proactive_probability(0.5, 1) == 1.0
    assert rules.proactive_probability(0.5, 2) == 0.5
    assert rules.proactive_probability(0.5, 3) == 0.25


def test_proactive_messages_then_goodbye_ends_conversation():
    state = one_human(CLAUDE)
    state.target = "claude"
    kinds = []
    for _ in range(3):
        decision, again = rules.on_idle_timer(state, ALL_KEYS, 1.0, FixedRandom(0.0))
        kinds.append(decision.requests[0].kind)
        assert state.proactive_mask != 7
        rules.on_proactive_saved(state, state.epoch, len(kinds) - 1)
    assert kinds == ["proactive", "proactive", "goodbye"]
    assert state.proactive_mask == 7
    assert state.target is None


def test_missed_draw_keeps_the_count():
    state = one_human(CLAUDE)
    state.target = "claude"
    state.proactive_mask = 1
    decision, again = rules.on_idle_timer(state, ALL_KEYS, 0.5, FixedRandom(0.9))
    assert decision.requests == [] and again is True
    assert state.proactive_mask == 1


def test_no_proactive_messages_in_multi_human_rooms():
    state = RoomState(humans=[ALICE, BOB], ais=[CLAUDE], target="claude")
    decision, again = rules.on_idle_timer(state, ALL_KEYS, 1.0, FixedRandom(0.0))
    assert decision.requests == [] and again is False


def test_after_goodbye_mention_continues_and_silence_restarts():
    state = one_human(CLAUDE, GPT)
    state.target = None  # the conversation ended
    assert slugs(rules.on_human_message(state, ALICE, "@claude let's go on", ALL_KEYS, FixedRandom())) == ["claude"]


def test_state_round_trips_through_json():
    state = RoomState(humans=[ALICE], ais=[CLAUDE], target="claude", proactive_mask=1, epoch=3)
    assert RoomState.from_json(state.to_json()) == state


def test_state_round_trips_collaboration_flags():
    state = RoomState(humans=[ALICE], ais=[CLAUDE], collaboration_enabled=True, room_name="作战室")
    assert RoomState.from_json(state.to_json()) == state
    # The human name travels too, for the collaboration room-and-members injector.
    named = RoomState(humans=[Human(10, 1, "alice")])
    assert named.humans[0].name == "alice"


def test_success_mask_is_idempotent_and_old_rounds_do_not_count():
    state = one_human(CLAUDE)
    state.target = 'claude'
    for slot, expected in [(0, 1), (0, 1), (1, 3), (2, 7)]:
        rules.on_proactive_saved(state, state.epoch, slot)
        assert state.proactive_mask == expected
    assert state.target is None
    rules.on_human_message(state, ALICE, 'hello', ALL_KEYS, FixedRandom())
    rules.on_proactive_saved(state, state.epoch - 1, 2)
    assert state.proactive_mask == 0


def test_legacy_proactive_counter_migrates_to_mask():
    assert RoomState.from_json({'proactive_sent': 2}).proactive_mask == 3


def two_ais():
    return RoomState(humans=[ALICE], ais=[CLAUDE, GPT])


def test_host_decision_maps_names_and_mentions_lead():
    state = two_ais()
    decision = rules.on_host_decision(state, ["GPT"], False, ALICE, ALL_KEYS, ["claude"], 5)
    assert [r.slug for r in decision.requests] == ["claude", "gpt"]


def test_host_decision_drops_unknown_names():
    state = two_ais()
    decision = rules.on_host_decision(state, ["不存在的AI", "Claude"], False, ALICE, ALL_KEYS, [], 5)
    assert [r.slug for r in decision.requests] == ["claude"]


def test_host_decision_respects_the_round_limit():
    state = RoomState(humans=[ALICE], ais=[
        CLAUDE, GPT, RoomAi(102, "d", "deepseek", "DeepSeek"), RoomAi(103, "g", "gemini", "Gemini")])
    decision = rules.on_host_decision(
        state, ["Claude", "GPT", "DeepSeek", "Gemini"], False, ALICE, ALL_KEYS, [], 2)
    assert len(decision.requests) == 2


def test_host_decision_end_stops_the_round():
    decision = rules.on_host_decision(two_ais(), ["GPT"], True, ALICE, ALL_KEYS, [], 5)
    assert decision.requests == []


def test_host_decision_reports_missing_keys():
    decision = rules.on_host_decision(two_ais(), ["GPT"], False, ALICE, {"anthropic"}, [], 5)
    assert decision.requests == []
    assert [m.provider for m in decision.missing] == ["openai"]


def test_pending_step_round_trips():
    state = RoomState(pending_step={"id": "x", "kind": "tool", "risk": "side_effect",
                                    "initiator": 10, "deadline": 9999.0, "status": "pending"})
    assert RoomState.from_json(state.to_json()).pending_step == state.pending_step


def test_confirmation_text_names_the_action_and_deadline():
    import time
    step = {"kind": "council", "risk": "discuss", "description": "按方案 A 继续",
            "initiator": 10, "deadline": time.time() + 180}
    text = rules.confirmation_text(step)
    assert "你还在吗" in text and "3 分钟" in text
    assert "方案 A" in text
    assert "继续" in text and "否决" in text


def test_side_effect_confirmation_does_not_promise_to_run_on_timeout():
    import time
    step = {"kind": "tool", "risk": "side_effect", "description": "执行方案 A",
            "initiator": 10, "deadline": time.time() + 180}
    text = rules.confirmation_text(step)
    assert "方案 A" in text and "不回复不会执行" in text
    assert "我就" not in text


def test_deadline_auto_continues_only_discuss_and_readonly():
    assert rules.deadline_auto_continues("discuss")
    assert rules.deadline_auto_continues("readonly")
    assert not rules.deadline_auto_continues("side_effect")
