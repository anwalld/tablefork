"""When AIs speak and who pays: pure decisions over a room's state.

Nothing here touches Redis or a model, so the rules can be tested directly.
The probabilities follow openspec/specs/ai-replies.
"""

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Human:
    member_id: int
    user_id: int


@dataclass(frozen=True)
class RoomAi:
    member_id: int
    slug: str
    provider: str
    name: str


@dataclass
class RoomState:
    """What is remembered about a room between events."""

    humans: list[Human] = field(default_factory=list)
    ais: list[RoomAi] = field(default_factory=list)
    # One-human rooms: the AI the human is talking to, if a conversation is on.
    target: str | None = None
    # Proactive messages sent in the current conversation since the human last spoke.
    proactive_mask: int = 0
    timer_due: float | None = None
    pending: list[dict] = field(default_factory=list)
    active: dict | None = None
    deleted: bool = False
    # Bumped on every human message, so that replies to older messages no
    # longer continue a chain.
    epoch: int = 0

    def ai(self, slug: str) -> RoomAi | None:
        return next((a for a in self.ais if a.slug == slug), None)

    def to_json(self) -> dict:
        return {
            "humans": [[h.member_id, h.user_id] for h in self.humans],
            "ais": [[a.member_id, a.slug, a.provider, a.name] for a in self.ais],
            "target": self.target,
            "proactive_mask": self.proactive_mask,
            "timer_due": self.timer_due, "pending": self.pending,
            "active": self.active, "deleted": self.deleted,
            "epoch": self.epoch,
        }

    @classmethod
    def from_json(cls, data: dict) -> "RoomState":
        return cls(
            humans=[Human(*h) for h in data.get("humans", [])],
            ais=[RoomAi(*a) for a in data.get("ais", [])],
            target=data.get("target"),
            proactive_mask=data.get("proactive_mask", (1 << min(data.get("proactive_sent", 0), 3)) - 1),
            timer_due=data.get("timer_due"), pending=data.get("pending", []),
            active=data.get("active"), deleted=data.get("deleted", False),
            epoch=data.get("epoch", 0),
        )


@dataclass(frozen=True)
class Request:
    """One reply to generate."""

    slug: str
    payer: Human
    kind: str  # "reply", "chain", "proactive", "goodbye"
    hop: int = 1


@dataclass(frozen=True)
class MissingKey:
    """Tell the payer they lack a key; provider is empty when no AI is usable."""

    payer: Human
    provider: str


@dataclass
class Decision:
    requests: list[Request] = field(default_factory=list)
    missing: list[MissingKey] = field(default_factory=list)


def mentions(content: str, ais: list[RoomAi]) -> list[str]:
    """Slugs of the room's AIs mentioned as @Name, in order of appearance."""
    lowered = content.lower()
    found = []
    for ai in ais:
        position = lowered.find("@" + ai.name.lower())
        if position >= 0:
            found.append((position, ai.slug))
    return [slug for _, slug in sorted(found)]


def chain_probability(p: float, hop: int) -> float:
    """Chance that hop `hop` (2 or more) of an AI-to-AI chain happens."""
    return p / 2 ** (hop - 2)


def proactive_probability(p1: float, n: int) -> float:
    """Chance that proactive message number n is sent when the timer fires."""
    return 1.0 if n == 1 else p1 / 2 ** (n - 2)


def on_human_message(
    state: RoomState,
    sender: Human,
    content: str,
    keys: set[str],
    rng: random.Random,
) -> Decision:
    """Updates state for a human's message and decides who replies.

    keys holds the providers the sender has API keys for.
    """
    state.epoch += 1
    state.proactive_mask = 0
    decision = Decision()
    mentioned = mentions(content, state.ais)

    if len(state.humans) != 1:
        state.target = None
        for slug in mentioned:
            _request(decision, state, Request(slug, sender, "reply"), keys)
        return decision

    if mentioned:
        state.target = mentioned[0]
    elif state.target is None or state.ai(state.target) is None:
        usable = [ai.slug for ai in state.ais if ai.provider in keys]
        if not usable:
            if state.ais:
                decision.missing.append(MissingKey(sender, ""))
            state.target = None
            return decision
        state.target = rng.choice(sorted(usable))
    _request(decision, state, Request(state.target, sender, "reply"), keys)
    return decision


def on_ai_message(
    state: RoomState,
    speaker: str,
    content: str,
    payer: Human,
    hop: int,
    keys: set[str],
    p: float,
    rng: random.Random,
) -> Decision:
    """Decides whether another AI picks up what `speaker` just said.

    hop is the hop of the message just saved; keys are the payer's providers.
    """
    decision = Decision()
    next_hop = hop + 1
    if rng.random() >= chain_probability(p, next_hop):
        return decision
    if len(state.humans) == 1:
        others = sorted(ai.slug for ai in state.ais if ai.slug != speaker and ai.provider in keys)
        if others:
            decision.requests.append(Request(rng.choice(others), payer, "chain", next_hop))
    else:
        for slug in mentions(content, state.ais):
            if slug != speaker:
                _request(decision, state, Request(slug, payer, "chain", next_hop), keys)
    return decision


def on_idle_timer(state: RoomState, keys: set[str], p1: float, rng: random.Random) -> tuple[Decision, bool]:
    """Decides whether the conversation partner speaks up after a silence.

    Returns the decision and whether the timer should run again.
    """
    decision = Decision()
    if len(state.humans) != 1 or state.target is None or state.ai(state.target) is None:
        state.target = None
        return decision, False
    n = state.proactive_mask.bit_count() + 1
    if rng.random() >= proactive_probability(p1, n):
        return decision, True
    human = state.humans[0]
    kind = "goodbye" if n >= 3 else "proactive"
    _request(decision, state, Request(state.target, human, kind), keys)
    return decision, True


def _request(decision: Decision, state: RoomState, request: Request, keys: set[str]) -> None:
    ai = state.ai(request.slug)
    if ai is None:
        return
    if ai.provider in keys:
        decision.requests.append(request)
    else:
        decision.missing.append(MissingKey(request.payer, ai.provider))


def on_proactive_saved(state: RoomState, epoch: int, slot: int) -> None:
    """Repeated storage confirmations set the same bit; old rounds cannot count."""
    if state.epoch != epoch or not 0 <= slot < 3:
        return
    state.proactive_mask |= 1 << slot
    if state.proactive_mask == 7:
        state.target = None
