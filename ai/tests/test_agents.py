import pytest

from chat_ai.agents import check_against_database, load_agents

PROFILES = {"deepseek": "deepseek", "gpt": "openai", "claude": "anthropic", "gemini": "gemini"}


def test_bundled_agents_match_the_migration():
    check_against_database(load_agents("agents.toml"), PROFILES)


def test_missing_agent_is_refused():
    agents = load_agents("agents.toml")
    del agents["gemini"]
    with pytest.raises(ValueError):
        check_against_database(agents, PROFILES)


def test_wrong_provider_is_refused():
    with pytest.raises(ValueError):
        check_against_database(load_agents("agents.toml"), {**PROFILES, "gpt": "anthropic"})
