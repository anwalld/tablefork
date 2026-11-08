"""The council ballot: one round of weighted voting.

The live round is a JSON blob in Redis under `council:{room}:{round}`; this
module models only the transitions (record a vote once, drop out-of-round
votes, tally when complete) so they can be unit-tested without Redis.
"""

from dataclasses import dataclass, field

from .council import Tally, tally


@dataclass
class Ballot:
    round: int
    topic: str
    proposer: int
    lead_slug: str
    chairs: list[str]
    deadline: float
    votes: dict = field(default_factory=dict)  # slug -> {"verdict", "reason"}

    def record(self, slug: str, verdict: str, reason: str) -> bool:
        """Records a chair's verdict; a chair votes only once per round."""
        if slug in self.votes or slug not in self.chairs:
            return False
        if verdict not in ("pass", "reject", "abstain"):
            return False
        self.votes[slug] = {"verdict": verdict, "reason": reason}
        return True

    def effective(self) -> list[tuple[str, bool]]:
        """The votes that count: pass/reject only, each marked lead or not."""
        return [(vote["verdict"], slug == self.lead_slug)
                for slug, vote in self.votes.items()
                if vote["verdict"] in ("pass", "reject")]

    def tally(self) -> Tally:
        return tally(self.effective())

    def complete(self) -> bool:
        return len(self.votes) == len(self.chairs)

    def to_json(self) -> dict:
        return {
            "round": self.round, "topic": self.topic, "proposer": self.proposer,
            "lead_slug": self.lead_slug, "chairs": self.chairs,
            "deadline": self.deadline, "votes": self.votes,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Ballot":
        return cls(
            round=data["round"], topic=data["topic"], proposer=data["proposer"],
            lead_slug=data["lead_slug"], chairs=data.get("chairs", []),
            deadline=data["deadline"], votes=data.get("votes", {}),
        )