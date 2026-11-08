"""The council's weighted voting, per the ai-council spec.

Weights are doubled so the lead's 1.5 votes stay integer: lead counts 3, every
other chair 2. A verdict is "objective pass" only when the pass weight exceeds
half of the effective total, and only when at least three votes are effective.
Abstention, timeout and failed votes are filtered out before this module sees
them, so they simply do not count.
"""

import json

from dataclasses import dataclass


@dataclass(frozen=True)
class Tally:
    effective: int
    pass_weight: int
    total_weight: int
    outcome: str  # "pass" | "reject" | "invalid"


def weight(is_lead: bool) -> int:
    return 3 if is_lead else 2


def tally(votes: list[tuple[str, bool]]) -> Tally:
    """votes: (verdict, is_lead) for every chair whose vote is effective.

    verdict is "pass" or "reject". Callers drop abstain/timeout/failed first.
    """
    effective = len(votes)
    if effective < 3:
        return Tally(effective, 0, 0, "invalid")
    total = sum(weight(lead) for _, lead in votes)
    passed = sum(weight(lead) for verdict, lead in votes if verdict == "pass")
    if passed * 2 > total:
        return Tally(effective, passed, total, "pass")
    return Tally(effective, passed, total, "reject")


def rotate_lead(chairs: list[str], proposer: str, previous: str | None) -> str | None:
    """The next lead chair: rotate in order, skipping the proposer.

    A chair never presents their own topic, and the lead rotates each round, so
    the previous lead sits out when there is someone else to choose from.
    """
    eligible = [slug for slug in chairs if slug != proposer]
    if not eligible:
        return None
    if previous is None or previous not in eligible:
        return eligible[0]
    return eligible[(eligible.index(previous) + 1) % len(eligible)]


# Each chair judges the topic independently. No other chair's verdict is ever
# passed in, so independence holds by construction.
VOTE_PROMPT = """你是本房间主席团的一名主席，正在独立审议一个议题。

议题：
{topic}

请给出你的判断。只输出一个 JSON 对象，不要输出别的内容：
{{"verdict": "pass" | "reject" | "abstain", "reason": "一句话理由"}}"""


def _extract_json(text: str) -> dict | None:
    """The first balanced-looking JSON object in `text`, or None."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_vote_json(text: str) -> dict | None:
    """Extracts a chair's {verdict, reason} from the model output."""
    parsed = _extract_json(text)
    if not isinstance(parsed, dict) or parsed.get("verdict") not in ("pass", "reject", "abstain"):
        return None
    reason = parsed.get("reason")
    return {"verdict": parsed["verdict"], "reason": reason if isinstance(reason, str) else ""}