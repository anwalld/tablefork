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