"""Breakouts (visibility, search, cascade) and memory management."""

import time

from conftest import error_code


def _collaborate(owner, room):
    assert owner.request("PUT", f"/api/rooms/{room}/collaboration", json={"enabled": True}).status_code == 200


def _breakout(socket, db, room, topic):
    frame = socket.post(room, f"/小会 {topic}")
    assert frame["type"] == "ack", frame
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM rooms WHERE parent_room_id=%s AND topic=%s", (room, topic))
        row = cursor.fetchone()
    assert row, "breakout was not created"
    return row[0]


def test_breakout_needs_collaboration(make_user, make_room, make_socket, db):
    owner = make_user()
    room = make_room(owner)
    socket = make_socket(owner)
    assert socket.post(room, "/小会 不该创建")["type"] == "ack"
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM rooms WHERE parent_room_id=%s", (room,))
        assert cursor.fetchone()[0] == 0


def test_parent_members_can_read_and_search_but_not_speak(make_user, make_room, make_socket, db):
    owner, member = make_user(), make_user()
    room = make_room(owner, member)
    _collaborate(owner, room)
    owner_socket = make_socket(owner)
    child = _breakout(owner_socket, db, room, "核对日志")
    assert owner_socket.post(child, "只在小会里出现的词zq9")["type"] == "ack"

    # The non-participant sees the breakout read-only in the room list.
    listed = {r["id"]: r for r in member.get("/api/rooms").json()["rooms"]}
    assert listed[child]["status"] == "readonly"
    history = member.get(f"/api/rooms/{child}/messages").json()["messages"]
    assert any("zq9" in m["content"] for m in history)
    found = member.get(f"/api/rooms/{room}/search", params={"q": "zq9"}).json()["messages"]
    assert [m["room_id"] for m in found] == [child]

    member_socket = make_socket(member)
    frame = member_socket.post(child, "我能插话吗")
    assert frame["type"] == "error"


def test_closing_collaboration_ends_the_whole_breakout_tree(make_user, make_room, make_socket, db):
    owner = make_user()
    room = make_room(owner)
    _collaborate(owner, room)
    socket = make_socket(owner)
    child = _breakout(socket, db, room, "一级")
    with db.cursor() as cursor:
        cursor.execute("UPDATE rooms SET collaboration_enabled=1 WHERE id=%s", (child,))
    grandchild = _breakout(socket, db, child, "二级")

    assert owner.request("PUT", f"/api/rooms/{room}/collaboration", json={"enabled": False}).status_code == 200
    with db.cursor() as cursor:
        cursor.execute("SELECT status FROM rooms WHERE id IN (%s,%s)", (child, grandchild))
        assert {row[0] for row in cursor.fetchall()} == {"closed"}
    frame = socket.post(grandchild, "还能说话吗")
    assert frame["type"] == "error"


def _room_memory(db, room, content):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO room_memories (room_id, layer, content, created_at_ms) "
                       "VALUES (%s,'conclusion',%s,%s)", (room, content, int(time.time() * 1000)))
        return cursor.lastrowid


def test_room_memories_listed_by_members_deleted_by_admins(make_user, make_room, db):
    owner, member = make_user(), make_user()
    room = make_room(owner, member)
    memory = _room_memory(db, room, "数据库选用 MySQL")
    listed = member.get(f"/api/rooms/{room}/memories").json()["memories"]
    assert [m["content"] for m in listed] == ["数据库选用 MySQL"]

    response = member.request("DELETE", f"/api/rooms/{room}/memories/{memory}")
    assert response.status_code == 403 and error_code(response) == "forbidden"
    assert owner.request("DELETE", f"/api/rooms/{room}/memories/{memory}").status_code == 200
    assert member.get(f"/api/rooms/{room}/memories").json()["memories"] == []


def test_user_memories_are_private_to_their_user(make_user, db):
    alice, bob = make_user(), make_user()
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM users WHERE username=%s", (alice.username,))
        alice_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO user_memories (user_id, layer, content, created_at_ms) "
                       "VALUES (%s,'preference','回答尽量简短',%s)", (alice_id, int(time.time() * 1000)))
        memory = cursor.lastrowid
    assert bob.get("/api/me/memories").json()["memories"] == []
    assert bob.request("DELETE", f"/api/me/memories/{memory}").status_code == 200   # no effect
    assert [m["content"] for m in alice.get("/api/me/memories").json()["memories"]] == ["回答尽量简短"]
    assert alice.request("DELETE", f"/api/me/memories/{memory}").status_code == 200
    assert alice.get("/api/me/memories").json()["memories"] == []
