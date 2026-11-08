import os
import sys

import pytest

from chat_ai import tools
from chat_ai.gen import ai_pb2

from test_engine import ALICE, CLAUDE, Harness

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mcp_server.py")


def write_registry(tmp_path, extra=""):
    path = tmp_path / "tools.toml"
    log = tmp_path / "mail.log"
    path.write_text(f'''
[[builtin]]
name = "current_time"
risk = "readonly"

[[mcp]]
server = "test"
command = "{sys.executable}"
args = ["{FIXTURE}"]
env = {{ MCP_LOG = "{log}" }}
[mcp.tools]
echo = "readonly"
slow = "readonly"
send_email = "side_effect"
{extra}
''')
    return str(path)


def test_registry_rejects_ambiguous_entries(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('[[builtin]]\nname = "current_time"\nrisk = "maybe"\n')
    with pytest.raises(ValueError):
        tools.load(str(bad))
    bad.write_text('[[builtin]]\nname = "rm_rf"\nrisk = "readonly"\n')
    with pytest.raises(ValueError):
        tools.load(str(bad))
    bad.write_text('[[mcp]]\nserver = "x"\n[mcp.tools]\na = "readonly"\n')
    with pytest.raises(ValueError):
        tools.load(str(bad))
    assert tools.load(str(tmp_path / "missing.toml")) == {}
    registry = tools.load(write_registry(tmp_path))
    assert {n: t.risk for n, t in registry.items()} == {
        "current_time": "readonly", "echo": "readonly", "slow": "readonly", "send_email": "side_effect"}


async def collab_says(h, content, room_id=1):
    await h.cache_message(ALICE[0], "alice", content, room_id=room_id)
    room = h.snapshot([ALICE], [CLAUDE], room_id=room_id)
    room.collaboration_enabled = True
    await h.send_event({"message_saved": ai_pb2.MessageSaved(
        room=room, seq=h.seq, content=content,
        sender=ai_pb2.Sender(member_id=ALICE[0], user_id=ALICE[1], name="alice"))})
    await h.settle()


def harness(tmp_path, **overrides):
    values = dict(tool_timeout=10, council_timeout_seconds=60)
    values.update(overrides)
    return Harness(tools_enabled=True, tools_file=write_registry(tmp_path), **values)


def mails(tmp_path):
    path = tmp_path / "mail.log"
    return path.read_text().splitlines() if path.exists() else []


async def test_readonly_tools_run_at_once_and_are_recorded(tmp_path):
    h = harness(tmp_path)
    await collab_says(h, '@Claude #tool:echo {"text": "日志"}')
    final = h.of("final")[-1].content
    assert final.startswith("〔工具 echo(")
    assert "echo:日志" in final                       # the model saw the result


async def test_builtin_tool(tmp_path):
    h = harness(tmp_path)
    await collab_says(h, "@Claude #tool:current_time")
    assert "当前时间：" in h.of("final")[-1].content


async def test_unregistered_tool_is_refused(tmp_path):
    h = harness(tmp_path)
    await collab_says(h, "@Claude #tool:rm_rf")
    assert "没有名为 rm_rf 的工具" in h.of("final")[-1].content


async def test_tool_timeout_comes_back_as_a_result(tmp_path):
    h = harness(tmp_path, generation_timeout=20, tool_timeout=1.0)
    await collab_says(h, "@Claude #tool:slow")
    assert "调用超时" in h.of("final")[-1].content


async def test_tools_are_off_outside_collaboration(tmp_path):
    h = harness(tmp_path)
    await h.human_says(ALICE, "#tool:echo {\"text\": \"x\"}", [ALICE], [CLAUDE])
    await h.settle()
    assert "〔工具" not in h.of("final")[-1].content


async def test_side_effect_waits_for_confirmation_and_runs_once(tmp_path):
    h = harness(tmp_path)
    await collab_says(h, '@Claude #tool:send_email {"to": "ops@example.com"}')
    asked = h.of("final")[-1].content
    assert "send_email" in asked and "继续" in asked
    assert mails(tmp_path) == []                      # not executed yet
    notices = [n.content for n in h.of("system_notice")]
    assert any("不回复不会执行" in n for n in notices)

    confirm = ai_pb2.ConfirmationInput(room_id=1, response="continue", member_id=ALICE[0])
    await h.send_event({"confirmation_input": confirm})
    await h.settle()
    assert mails(tmp_path) == ["ops@example.com"]
    final = h.of("final")[-1].content
    assert final.startswith("〔工具 send_email(") and "sent to ops@example.com" in final

    await h.send_event({"confirmation_input": confirm})   # a second "继续" does nothing
    await h.settle()
    assert mails(tmp_path) == ["ops@example.com"]


async def test_only_the_initiator_or_an_admin_can_confirm(tmp_path):
    h = harness(tmp_path)
    await collab_says(h, '@Claude #tool:send_email {"to": "a@example.com"}')
    await h.send_event({"confirmation_input": ai_pb2.ConfirmationInput(
        room_id=1, response="continue", member_id=999)})
    await h.settle()
    assert mails(tmp_path) == []
    await h.send_event({"confirmation_input": ai_pb2.ConfirmationInput(
        room_id=1, response="continue", member_id=999, privileged=True)})
    await h.settle()
    assert mails(tmp_path) == ["a@example.com"]


async def test_side_effect_never_runs_on_timeout_or_veto(tmp_path):
    h = harness(tmp_path)
    await collab_says(h, '@Claude #tool:send_email {"to": "b@example.com"}')
    h.clock.now += 120
    await h.engine.fire_due_timers()
    await h.settle()
    assert mails(tmp_path) == []
    state = await h.engine._load(1)
    assert state.pending_step["status"] == "paused"

    await h.send_event({"confirmation_input": ai_pb2.ConfirmationInput(
        room_id=1, response="cancel", member_id=ALICE[0])})
    await h.settle()
    assert mails(tmp_path) == []
    assert any("已取消" in n.content for n in h.of("system_notice"))


async def test_a_retried_continuation_does_not_run_the_tool_again(tmp_path):
    h = harness(tmp_path)
    job = {"id": "j1", "step_id": "tool:x", "payer": [ALICE[0], ALICE[1]],
           "tool": {"name": "send_email", "arguments": {"to": "c@example.com"}}}
    await h.engine._run_confirmed_tool(1, job)
    record, note = await h.engine._run_confirmed_tool(1, dict(job, id="j1-retry"))
    assert mails(tmp_path) == ["c@example.com"]
    assert "sent to c@example.com" in note
