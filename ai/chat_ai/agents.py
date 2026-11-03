"""The preset AIs: model and persona per slug, from agents.toml."""

import tomllib
from dataclasses import dataclass

PROVIDERS = ("deepseek", "openai", "anthropic", "gemini")


@dataclass(frozen=True)
class Agent:
    slug: str
    name: str
    provider: str
    model: str
    persona: str


def load_agents(path: str) -> dict[str, Agent]:
    with open(path, "rb") as file:
        data = tomllib.load(file)
    agents = {}
    for slug, entry in data.get("agents", {}).items():
        agent = Agent(
            slug=slug,
            name=entry["name"],
            provider=entry["provider"],
            model=entry["model"],
            persona=entry["persona"].strip(),
        )
        if agent.provider not in PROVIDERS:
            raise ValueError(f"agent {slug}: unknown provider {agent.provider}")
        agents[slug] = agent
    return agents


def check_against_database(agents: dict[str, Agent], profiles: dict[str, str]) -> None:
    """Fails unless agents.toml and ai_profiles describe the same AIs.

    profiles maps slug to provider, as stored in ai_profiles.
    """
    if set(agents) != set(profiles):
        raise ValueError(
            f"agents.toml has {sorted(agents)}, ai_profiles has {sorted(profiles)}"
        )
    for slug, provider in profiles.items():
        if agents[slug].provider != provider:
            raise ValueError(
                f"agent {slug}: agents.toml says {agents[slug].provider}, "
                f"ai_profiles says {provider}"
            )
