"""Table templates: one call launches a group of role AIs."""

from ai_helpers import store_key
from conftest import error_code


def test_templates_are_listed(make_user):
    user = make_user()
    templates = {t["id"]: t for t in user.get("/api/table-templates").json()["templates"]}
    assert set(templates) == {"troubleshoot", "review", "brainstorm"}
    assert [a["name"] for a in templates["troubleshoot"]["ais"]] == ["收集者", "分析者", "审查者"]


def test_launch_skips_ais_without_a_key_and_turns_collaboration_on(make_user, make_room, db):
    owner = make_user()
    room = make_room(owner)
    store_key(owner, "deepseek")
    result = owner.post(f"/api/rooms/{room}/table-template", json={"template": "troubleshoot"}).json()
    assert [a["name"] for a in result["joined"]] == ["收集者"]
    assert {(s["name"], s["provider"]) for s in result["skipped"]} == {("分析者", "anthropic"), ("审查者", "openai")}
    assert result["collaboration_enabled"] is True
    names = [m["name"] for m in owner.get(f"/api/rooms/{room}/members").json()["members"]]
    assert "收集者" in names and "分析者" not in names
    with db.cursor() as cursor:
        cursor.execute("SELECT collaboration_enabled FROM rooms WHERE id=%s", (room,))
        assert cursor.fetchone()[0] == 1


def test_review_template_seats_its_council(make_user, make_room, db):
    owner = make_user()
    room = make_room(owner)
    for provider in ("deepseek", "openai", "anthropic"):
        store_key(owner, provider)
    result = owner.post(f"/api/rooms/{room}/table-template", json={"template": "review"}).json()
    assert result["council"] is True
    chairs = {a["id"] for a in result["joined"] if a["chair"]}
    with db.cursor() as cursor:
        cursor.execute("SELECT member_id FROM room_council WHERE room_id=%s", (room,))
        assert {row[0] for row in cursor.fetchall()} == chairs and len(chairs) == 3


def test_only_owners_and_admins_launch_templates(make_user, make_room):
    owner, member = make_user(), make_user()
    room = make_room(owner, member)
    store_key(member, "deepseek")
    response = member.post(f"/api/rooms/{room}/table-template", json={"template": "troubleshoot"})
    assert response.status_code == 403 and error_code(response) == "forbidden"
    unknown = owner.post(f"/api/rooms/{room}/table-template", json={"template": "nope"})
    assert unknown.status_code == 400
