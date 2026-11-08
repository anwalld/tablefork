"""Breakout budgets: cgroup-style accounting along the parent chain.

A breakout's AI-reply budget is stored per room. Spending one reply consumes it
from the room and from every ancestor, so a child's spend always counts
against its parents. If any level is exhausted the whole request is refused and
nothing is deducted, mirroring how a frozen cgroup stops its subtree.
"""


def deduct(chain: list[int]) -> list[int]:
    """Consume one reply from each level, innermost first, or none at all.

    `chain` holds remaining budgets from the child room up to the root.
    Returns the updated chain, or the unchanged chain when any level is out of
    budget (the request is refused).
    """
    if not chain or any(budget <= 0 for budget in chain):
        return chain
    return [budget - 1 for budget in chain]


def exhausted(chain: list[int]) -> bool:
    """True when any level of the chain has run out of budget."""
    return not chain or any(budget <= 0 for budget in chain)


def conclusion_content(topic: str, participants: list[str], summary: str) -> str:
    """The message a finished breakout posts back to its parent room: topic,
    participants and the conclusion, so the parent can read the outcome."""
    lines = [f"【小会】{topic}"]
    if participants:
        lines.append("参加者：" + "、".join(participants))
    if summary:
        lines.append("结论：" + summary)
    return "\n".join(lines)

# Atomic charge along the chain. KEYS[1] marks the generation as charged, so a
# retried or recovered job is never charged twice; KEYS[2..] are the budgets of
# each breakout level, innermost first. Missing budgets start at ARGV[1].
CHARGE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 1 end
local values = {}
for i = 2, #KEYS do
  local v = tonumber(redis.call('GET', KEYS[i]) or ARGV[1])
  if v <= 0 then return 0 end
  values[i] = v
end
for i = 2, #KEYS do redis.call('SET', KEYS[i], values[i] - 1) end
redis.call('SET', KEYS[1], '1', 'EX', 86400)
return 1
"""

SUMMARY_PROMPT = (
    "你是这次小会的记录员。请根据下面的小会记录，用不超过 5 句话写出结论："
    "达成了什么共识、还有什么分歧、接下来要做什么。只写结论本身，不要寒暄。"
)


def conclusion_message_id(room_id: int) -> str:
    """The client_msg_id the chat server stores the conclusion under."""
    return f"breakout-conclusion-{room_id}"
