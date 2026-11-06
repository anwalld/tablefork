from ai_helpers import add_ai, ai_slugs, appoint, members, store_key, role_for
from conftest import error_code


def test_catalog_marks_my_keys(make_user):
    user = make_user()
    store_key(user, "anthropic")
    agents = {a["model_slug"]: a for a in user.get("/api/ai/models").json()["models"]}
    assert set(agents) == {"deepseek", "gpt", "claude", "gemini"}
    assert agents["claude"]["has_key"] is True
    assert agents["claude"]["provider"] == "anthropic"
    assert agents["gpt"]["has_key"] is False


def test_catalog_needs_login(anon):
    assert anon.get("/api/ai-agents").status_code == 401


def test_owner_adds_ai_at_once(make_user, make_room, make_socket):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    socket = make_socket(guest)
    socket.subscribe(room_id)
    store_key(owner, "anthropic")
    assert add_ai(owner, room_id, "claude").json() == {"status": "joined"}
    socket.expect("members_changed")
    claude = next(m for m in members(owner, room_id) if m["type"] == "ai")
    assert claude["id"] == role_for(owner,"claude")["id"] and claude["model_slug"] == "claude" and claude["provider"] == "anthropic"
    assert claude["role"] == "member"


def test_request_without_key(make_user, make_room):
    owner = make_user()
    room_id = make_room(owner)
    response = add_ai(owner, room_id, "claude")
    assert response.status_code == 400
    assert error_code(response) == "api_key_required"
    assert response.json()["error"]["provider"] == "anthropic"
    assert ai_slugs(owner, room_id) == []


def test_member_request_waits_for_approval(make_user, make_room, make_socket):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    owner_socket = make_socket(owner)
    owner_socket.subscribe(room_id)
    guest_socket = make_socket(guest)
    guest_socket.subscribe(room_id)
    store_key(guest, "openai")

    assert add_ai(guest, room_id, "gpt").json() == {"status": "pending"}
    notice = owner_socket.expect("ai_request")
    assert notice["slug"] == role_for(guest,"gpt")["slug"]
    assert "only_member_ids" not in notice
    assert ai_slugs(owner, room_id) == []
    pending = owner.get(f"/api/rooms/{room_id}/ai-requests").json()["requests"]
    assert [(r["slug"], r["requested_by"]["name"]) for r in pending] == [(role_for(guest,"gpt")["slug"], guest.username)]

    assert owner.post(f"/api/rooms/{room_id}/ai-requests/{role_for(guest, 'gpt')['id']}/approve").status_code == 200
    done = guest_socket.expect("ai_request_done")
    assert done["approved"] is True
    assert ai_slugs(owner, room_id) == ["gpt"]
    assert owner.get(f"/api/rooms/{room_id}/ai-requests").json()["requests"] == []


def test_guest_does_not_see_request_notice(make_user, make_room, make_socket):
    owner, guest, other = make_user(), make_user(), make_user()
    room_id = make_room(owner, guest, other)
    other_socket = make_socket(other)
    other_socket.subscribe(room_id)
    store_key(guest, "openai")
    add_ai(guest, room_id, "gpt")
    with __import__("pytest").raises(TimeoutError):
        other_socket.expect("ai_request", timeout=1)


def test_reject(make_user, make_room, make_socket):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    guest_socket = make_socket(guest)
    guest_socket.subscribe(room_id)
    store_key(guest, "openai")
    add_ai(guest, room_id, "gpt")
    assert owner.post(f"/api/rooms/{room_id}/ai-requests/{role_for(guest, 'gpt')['id']}/reject").status_code == 200
    assert guest_socket.expect("ai_request_done")["approved"] is False
    assert ai_slugs(owner, room_id) == []


def test_duplicate_requests_are_one(make_user, make_room):
    owner, a, b = make_user(), make_user(), make_user()
    room_id = make_room(owner, a, b)
    store_key(a, "openai")
    store_key(b, "openai")
    add_ai(a, room_id, "gpt")
    add_ai(a, room_id, "gpt")
    assert len(owner.get(f"/api/rooms/{room_id}/ai-requests").json()["requests"]) == 1


def test_admin_approves_and_second_review_finds_nothing(make_user, make_room):
    owner, admin, guest = make_user(), make_user(), make_user()
    room_id = make_room(owner, admin, guest)
    appoint(owner, room_id, admin)
    store_key(guest, "openai")
    add_ai(guest, room_id, "gpt")
    assert admin.post(f"/api/rooms/{room_id}/ai-requests/{role_for(guest, 'gpt')['id']}/approve").status_code == 200
    second = owner.post(f"/api/rooms/{room_id}/ai-requests/{role_for(guest, 'gpt')['id']}/approve")
    assert second.status_code == 404
    assert error_code(second) == "request_not_found"


def test_ordinary_member_cannot_review(make_user, make_room):
    owner, a, b = make_user(), make_user(), make_user()
    room_id = make_room(owner, a, b)
    store_key(a, "openai")
    add_ai(a, room_id, "gpt")
    for response in (
        b.get(f"/api/rooms/{room_id}/ai-requests"),
        b.post(f"/api/rooms/{room_id}/ai-requests/{role_for(a, 'gpt')['id']}/approve"),
        b.post(f"/api/rooms/{room_id}/ai-requests/{role_for(a, 'gpt')['id']}/reject"),
    ):
        assert response.status_code == 403
        assert error_code(response) == "forbidden"


def test_already_in_room_is_success(make_user, make_room):
    owner = make_user()
    room_id = make_room(owner)
    store_key(owner, "anthropic")
    add_ai(owner, room_id, "claude")
    assert add_ai(owner, room_id, "claude").json() == {"status": "joined"}
    assert ai_slugs(owner, room_id) == ["claude"]


def test_non_member_and_unknown_ai(make_user, make_room):
    owner, stranger = make_user(), make_user()
    room_id = make_room(owner)
    store_key(stranger, "anthropic")
    assert add_ai(stranger, room_id, "claude").status_code == 404
    store_key(owner, "anthropic")
    response = add_ai(owner, room_id, "hal")
    assert response.status_code == 404
    assert error_code(response) == "ai_not_found"


def test_remove_ai(make_user, make_room, make_socket):
    owner, admin, guest = make_user(), make_user(), make_user()
    room_id = make_room(owner, admin, guest)
    appoint(owner, room_id, admin)
    store_key(owner, "anthropic")
    add_ai(owner, room_id, "claude")
    socket = make_socket(owner)
    socket.subscribe(room_id)
    socket.post(room_id, "@claude 你好")
    reply_seq = None
    while reply_seq is None:
        message = socket.expect("message")
        if message["member"]["type"] == "ai":
            reply_seq = message["seq"]

    response = guest.request("DELETE", f"/api/rooms/{room_id}/ai-members/{role_for(owner, 'claude')['id']}")
    assert response.status_code == 403
    assert ai_slugs(owner, room_id) == ["claude"]

    assert admin.request("DELETE", f"/api/rooms/{room_id}/ai-members/{role_for(owner, 'claude')['id']}").status_code == 200
    assert ai_slugs(owner, room_id) == []
    history = owner.get(f"/api/rooms/{room_id}/messages").json()["messages"]
    assert any(m["seq"] == reply_seq and m["member"]["name"] == "Claude" for m in history)

    assert add_ai(owner, room_id, "claude").json() == {"status": "joined"}
