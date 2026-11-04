"""房间踢人、封禁、权限与跨实例通知。"""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import os
import pytest
from conftest import PASSWORD, Client, error_code, unique_name


def manage(client, room, username, action="ban"):
    path = "members" if action == "kick" else "bans"
    method = "DELETE" if action == "kick" else "PUT"
    return client.request(method, f"/api/rooms/{room}/{path}/{quote(username, safe='')}")


def bans(client, room):
    response = client.get(f"/api/rooms/{room}/bans")
    assert response.status_code == 200
    return response.json()["bans"]


@pytest.fixture
def community(make_user, make_room):
    users = {role: make_user() for role in ("owner", "admin", "peer", "member", "outside")}
    room = make_room(users["owner"], users["admin"], users["peer"], users["member"])
    for role in ("admin", "peer"):
        assert users["owner"].request("PUT", f"/api/rooms/{room}/admins/{users[role].username}").status_code == 200
    return users, room


@pytest.mark.parametrize("action", ["kick", "ban"])
@pytest.mark.parametrize("actor,target,status", [
    ("owner", "admin", 200), ("owner", "member", 200), ("admin", "member", 200),
    ("admin", "peer", 403), ("admin", "owner", 403), ("member", "owner", 403),
    ("member", "admin", 403), ("owner", "owner", 400), ("member", "member", 400),
    ("outside", "member", 404),
])
def test_management_permissions(community, actor, target, status, action):
    users, room = community
    response = manage(users[actor], room, users[target].username, action)
    assert response.status_code == status
    if status != 200:
        expected = {400: "invalid_argument", 403: "forbidden", 404: "room_not_found"}[status]
        assert error_code(response) == expected
        current = users["owner"].get(f"/api/rooms/{room}/members").json()["members"]
        assert users[target].username in {m["name"] for m in current}


@pytest.mark.parametrize("actor", ["owner", "admin"])
def test_outside_user_may_be_banned_but_not_kicked(community, actor):
    users, room = community
    assert manage(users[actor], room, users["outside"].username, "kick").status_code == 400
    assert manage(users[actor], room, users["outside"].username).status_code == 200
    assert [entry["username"] for entry in bans(users[actor], room)] == [users["outside"].username]


@pytest.mark.parametrize("action,reason", [("kick", "kicked"), ("ban", "banned")])
def test_removal_notifies_all_connections_and_stops_room_push(make_user, make_room, make_socket, action, reason):
    owner, guest = make_user(), make_user()
    room = make_room(owner, guest)
    readers = [make_socket(guest, instance=instance) for instance in (1, 2)]
    writer = make_socket(owner, instance=1)
    for socket in [*readers, writer]:
        assert socket.subscribe(room)["type"] == "subscribed"
    assert manage(owner, room, guest.username, action).status_code == 200
    for socket in readers:
        assert socket.expect("room_left") == {"type": "room_left", "room_id": room, "reason": reason}
        assert socket.post(room)["error"]["code"] == "room_not_found"
    assert writer.expect("members_changed")["room_id"] == room
    assert writer.post(room, "after removal")["type"] == "ack"
    for socket in readers:
        socket.assert_silent(seconds=0.2)
    assert guest.get(f"/api/rooms/{room}/messages").status_code == 404


def test_kick_preserves_history_and_reinvite_resets_role(make_user, make_room, make_socket):
    owner, guest = make_user(), make_user()
    room = make_room(owner, guest)
    assert owner.request("PUT", f"/api/rooms/{room}/admins/{guest.username}").status_code == 200
    socket = make_socket(guest)
    assert socket.post(room, "keep this message")["type"] == "ack"
    assert manage(owner, room, guest.username, "kick").status_code == 200
    assert socket.post(room)["error"]["code"] == "room_not_found"
    assert owner.get(f"/api/rooms/{room}/messages").json()["messages"][0]["content"] == "keep this message"
    assert owner.post(f"/api/rooms/{room}/invites", json={"username": guest.username}).status_code == 200
    assert guest.post(f"/api/rooms/{room}/join").status_code == 200
    current = guest.get(f"/api/rooms/{room}/members").json()["members"]
    assert next(m for m in current if m["name"] == guest.username)["role"] == "member"
    assert guest.get(f"/api/rooms/{room}/messages").json()["messages"][0]["content"] == "keep this message"


def test_ban_cancels_invite_and_unban_does_not_restore_it(make_user, make_room):
    owner, admin, guest = make_user(), make_user(), make_user()
    room = make_room(owner, admin)
    assert owner.request("PUT", f"/api/rooms/{room}/admins/{admin.username}").status_code == 200
    assert owner.post(f"/api/rooms/{room}/invites", json={"username": guest.username}).status_code == 200
    assert manage(admin, room, guest.username).status_code == 200
    first = bans(owner, room)
    assert first[0]["username"] == guest.username
    assert first[0]["banned_by"] == admin.username
    assert first[0]["created_at"] > 0
    assert manage(owner, room, guest.username).status_code == 200
    assert bans(owner, room) == first
    assert room not in {r["id"] for r in guest.get("/api/rooms").json()["rooms"]}
    assert guest.post(f"/api/rooms/{room}/join").status_code == 404
    response = owner.post(f"/api/rooms/{room}/invites", json={"username": guest.username})
    assert response.status_code == 409 and error_code(response) == "user_banned"
    assert admin.request("DELETE", f"/api/rooms/{room}/bans/{guest.username}").status_code == 200
    assert admin.request("DELETE", f"/api/rooms/{room}/bans/{guest.username}").status_code == 200
    assert guest.post(f"/api/rooms/{room}/join").status_code == 404
    assert owner.post(f"/api/rooms/{room}/invites", json={"username": guest.username}).status_code == 200
    assert guest.post(f"/api/rooms/{room}/join").status_code == 200


def test_ban_actor_name_survives_leaving_and_becomes_null_on_account_deletion(make_user, make_room, db):
    owner, admin, guest = make_user(), make_user(), make_user()
    room = make_room(owner, admin)
    assert owner.request("PUT", f"/api/rooms/{room}/admins/{admin.username}").status_code == 200
    assert manage(admin, room, guest.username).status_code == 200
    assert admin.post(f"/api/rooms/{room}/leave").status_code == 200
    assert bans(owner, room)[0]["banned_by"] == admin.username
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM users WHERE username=%s", (admin.username,))
        user_id = cursor.fetchone()[0]
        cursor.execute("DELETE FROM members WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))
    assert bans(owner, room)[0]["banned_by"] is None
    assert bans(owner, room)[0]["username"] == guest.username


def test_member_cannot_view_unban_or_ban_an_outside_user(community):
    users, room = community
    member = users["member"]
    assert member.get(f"/api/rooms/{room}/bans").status_code == 403
    assert member.request("DELETE", f"/api/rooms/{room}/bans/{users['outside'].username}").status_code == 403
    assert manage(member, room, users["outside"].username).status_code == 403
    assert bans(users["owner"], room) == []


@pytest.mark.parametrize("action", ["kick", "ban"])
def test_unknown_target_and_actor_who_left(make_user, make_room, action):
    owner, guest = make_user(), make_user()
    room = make_room(owner, guest)
    unknown = unique_name()
    response = manage(owner, room, unknown, action)
    assert response.status_code == 404 and error_code(response) == "user_not_found"
    assert guest.post(f"/api/rooms/{room}/leave").status_code == 200
    response = manage(guest, room, owner.username, action)
    assert response.status_code == 404 and error_code(response) == "room_not_found"


def test_ai_removal_uses_its_dedicated_endpoint(make_user, make_room):
    owner = make_user()
    room = make_room(owner)
    assert manage(owner, room, "Claude", "kick").status_code == 400


def test_concurrent_invitation_and_ban_cannot_leave_a_live_invite(make_user, make_room):
    owner, admin, guest = make_user(), make_user(), make_user()
    room = make_room(owner, admin)
    assert owner.request("PUT", f"/api/rooms/{room}/admins/{admin.username}").status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        invite = pool.submit(owner.post, f"/api/rooms/{room}/invites", json={"username": guest.username})
        ban = pool.submit(manage, admin, room, guest.username)
        assert invite.result().status_code in (200, 409)
        assert ban.result().status_code == 200
    assert guest.post(f"/api/rooms/{room}/join").status_code == 404
    assert room not in {r["id"] for r in guest.get("/api/rooms").json()["rooms"]}


def test_username_in_path_is_decoded(make_user, make_room):
    owner = make_user()
    guest = Client()
    guest.username = "测 试" + unique_name()
    body = {"username": guest.username, "password": PASSWORD}
    assert guest.post("/api/auth/register", json=body).status_code == 201
    assert guest.post("/api/auth/login", json=body).status_code == 200
    room = make_room(owner, guest)
    assert manage(owner, room, guest.username, "kick").status_code == 200


def test_ban_list_returns_all_entries_in_timestamp_order(make_user, make_room, db):
    owner, first, second = make_user(), make_user(), make_user()
    room = make_room(owner)
    assert manage(owner, room, first.username).status_code == 200
    assert manage(owner, room, second.username).status_code == 200
    with db.cursor() as cursor:
        cursor.execute("UPDATE room_bans SET created_at='2026-01-01 00:00:00.000' "
            "WHERE room_id=%s AND user_id=(SELECT id FROM users WHERE username=%s)",
            (room, second.username))
    records = bans(owner, room)
    assert [entry["username"] for entry in records] == [second.username, first.username]
    assert records[0]["created_at"] < records[1]["created_at"]


def test_nonmember_cannot_read_or_remove_bans(community):
    users, room = community
    assert users["outside"].get(f"/api/rooms/{room}/bans").status_code == 404
    assert users["outside"].request("DELETE", f"/api/rooms/{room}/bans/{users['member'].username}").status_code == 404


@pytest.mark.parametrize("inviter", ["owner", "admin", "member"])
def test_every_members_invitation_checks_bans(community, inviter):
    users, room = community
    assert manage(users["owner"], room, users["outside"].username).status_code == 200
    response = users[inviter].post(f"/api/rooms/{room}/invites", json={"username": users["outside"].username})
    assert response.status_code == 409 and error_code(response) == "user_banned"


@pytest.mark.parametrize("action", ["kick", "ban"])
def test_lost_removal_notification_cannot_leak_new_messages(make_user, make_room, make_socket, redis, action):
    owner, guest, other = make_user(), make_user(), make_user()
    room = make_room(owner, guest, other)
    reader = make_socket(guest, instance=2)
    writer = make_socket(owner, instance=1)
    reader.subscribe(room)
    writer.subscribe(room)
    client = owner.at_instance(1)
    redis.execute_command("ACL", "SETUSER", "default", "-publish")
    try:
        assert manage(client, room, guest.username, action).status_code == 200
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+publish")
        client.session.close()
    left = reader.expect("room_left", timeout=5)
    assert left["room_id"] == room
    assert left["reason"] == ("banned" if action == "ban" else "kicked")
    assert writer.post(room, "private after missed removal")["type"] == "ack"
    assert writer.expect("message")["content"] == "private after missed removal"
    assert manage(owner, room, other.username, "kick").status_code == 200
    assert writer.expect("members_changed")["room_id"] == room
    with pytest.raises(TimeoutError):
        reader.expect("message", "members_changed", "role_changed", timeout=0.5)


@pytest.mark.skipif(not os.environ.get("AI_TESTS"), reason="需要假模型")
@pytest.mark.parametrize("action", ["kick", "ban"])
def test_lost_removal_cannot_leak_ai_chunks(make_user, make_room, make_socket, redis, action):
    from ai_helpers import add_ai, ai_reply, store_key
    owner, guest = make_user(), make_user()
    room = make_room(owner, guest)
    assert store_key(owner, "anthropic").status_code == 200
    assert add_ai(owner, room, "claude").status_code == 200
    reader = make_socket(guest, instance=2)
    writer = make_socket(owner, instance=1)
    reader.subscribe(room)
    writer.subscribe(room)
    client = owner.at_instance(1)
    redis.execute_command("ACL", "SETUSER", "default", "-publish")
    try:
        assert manage(client, room, guest.username, action).status_code == 200
    finally:
        redis.execute_command("ACL", "SETUSER", "default", "+publish")
        client.session.close()
    left = reader.expect("room_left", timeout=5)
    assert left["room_id"] == room
    assert left["reason"] == ("banned" if action == "ban" else "kicked")
    assert writer.post(room, "@Claude private after missed removal")["type"] == "ack"
    assert ai_reply(writer)["content"].endswith("private after missed removal")
    with pytest.raises(TimeoutError):
        reader.expect("message", "ai_chunk", "ai_notice", timeout=0.5)


short_check = pytest.mark.skipif(
    not os.environ.get("SHORT_SESSION_CHECK"), reason="needs a short session check interval"
)


def reconcile_without_notification(make_user, make_room, make_socket, db, banned):
    owner, guest = make_user(), make_user()
    room = make_room(owner, guest)
    reader = make_socket(guest, instance=2)
    writer = make_socket(owner, instance=1)
    reader.subscribe(room)
    writer.subscribe(room)
    with db.cursor() as cursor:
        cursor.execute("SELECT u.id, m.id FROM users u JOIN members m ON m.user_id=u.id WHERE u.username=%s", (guest.username,))
        user_id, member_id = cursor.fetchone()
        if banned:
            cursor.execute("INSERT INTO room_bans (room_id, user_id) VALUES (%s, %s)", (room, user_id))
        cursor.execute("DELETE FROM room_members WHERE room_id=%s AND member_id=%s", (room, member_id))
    try:
        left = reader.expect("room_left", timeout=3)
        assert left["room_id"] == room
        assert left["reason"] == ("banned" if banned else "kicked")
        assert writer.post(room, "after periodic reconciliation")["type"] == "ack"
        with pytest.raises(TimeoutError):
            reader.expect("message", "members_changed", "role_changed", timeout=0.5)
    finally:
        # Use the normal locked API to dispose of the room and its cached data.
        owner.post(f"/api/rooms/{room}/leave", json={"confirm_delete": True})


@short_check
def test_reconcile_removes_subscription_without_notification(make_user, make_room, make_socket, db):
    reconcile_without_notification(make_user, make_room, make_socket, db, banned=False)


@short_check
def test_reconcile_reports_bans(make_user, make_room, make_socket, db):
    reconcile_without_notification(make_user, make_room, make_socket, db, banned=True)


def test_acknowledged_message_before_cross_instance_kick_remains_in_history(make_user, make_room, make_socket):
    owner, guest = make_user(), make_user()
    room = make_room(owner, guest)
    sender = make_socket(guest, instance=2)
    ack = sender.post(room, "saved immediately before removal")
    assert ack["type"] == "ack"
    client = owner.at_instance(1)
    try:
        assert manage(client, room, guest.username, "kick").status_code == 200
    finally:
        client.session.close()
    messages = owner.get(f"/api/rooms/{room}/messages").json()["messages"]
    stored = next(message for message in messages if message["seq"] == ack["seq"])
    assert stored["content"] == "saved immediately before removal"
    assert stored["member"]["name"] == guest.username
