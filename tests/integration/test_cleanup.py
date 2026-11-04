import uuid

import cleanup_test_data
from conftest import PASSWORD, Client


def account(name):
    client = Client()
    client.username = name
    body = {"username": name, "password": PASSWORD}
    assert client.post("/api/auth/register", json=body).status_code == 201
    assert client.post("/api/auth/login", json=body).status_code == 200
    return client


def post(client, room_id, text):
    from conftest import Socket

    socket = Socket(client)
    assert socket.post(room_id, text)["type"] == "ack"
    socket.close()


def count(db, sql, args):
    with db.cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.fetchone()[0]


def test_cleanup_removes_only_matching_users_and_keeps_shared_rooms(db, redis):
    prefix = f"zz{uuid.uuid4().hex[:6]}_"
    # Named like other test users, so that a later default cleanup removes it.
    keep = account(f"u{uuid.uuid4().hex[:12]}")
    lonely, sharer = account(prefix + "a"), account(prefix + "b")

    alone = lonely.post("/api/rooms", json={"name": "alone"}).json()["id"]
    post(lonely, alone, "only test users here")
    shared = sharer.post("/api/rooms", json={"name": "shared"}).json()["id"]
    sharer.post(f"/api/rooms/{shared}/invites", json={"username": keep.username})
    keep.post(f"/api/rooms/{shared}/join")
    post(sharer, shared, "from the test user")
    post(keep, shared, "from the real user")

    # A dry run reports but deletes nothing.
    report, _, _ = cleanup_test_data.cleanup(db, [prefix], ["realtest_"], False, test_users=False)
    assert report["users"] == 2 and report["rooms_deleted"] == 1
    assert report["ownership_transferred"] == 1
    assert count(db, "SELECT COUNT(*) FROM users WHERE username LIKE %s", (prefix + "%",)) == 2

    report, rooms, users = cleanup_test_data.cleanup(db, [prefix], ["realtest_"], True, test_users=False)
    assert sorted(rooms) == sorted([alone, shared])
    cleanup_test_data.clear_redis(redis, rooms, users)
    assert count(db, "SELECT COUNT(*) FROM users WHERE username LIKE %s", (prefix + "%",)) == 0
    assert count(db, "SELECT COUNT(*) FROM rooms WHERE id = %s", (alone,)) == 0
    # The shared room stays, with the real user's message and as its owner.
    contents = [m["content"] for m in keep.get(f"/api/rooms/{shared}/messages").json()["messages"]]
    assert contents == ["from the real user"]
    roles = {m["name"]: m["role"] for m in keep.get(f"/api/rooms/{shared}/members").json()["members"]}
    assert roles == {keep.username: "owner"}


def test_excluded_names_are_never_matched():
    assert not cleanup_test_data.matches("realtest_abc", [], ["realtest_"])
    assert cleanup_test_data.matches("u0123456789ab", [], ["realtest_"])
    assert cleanup_test_data.matches("用户u01234", [], ["realtest_"])
    assert not cleanup_test_data.matches("用户u012345", [], ["realtest_"])
    assert not cleanup_test_data.matches("u0123456789ab", [], [], test_users=False)
    assert cleanup_test_data.matches("owner_x", ["owner_"], [])


def test_cleanup_counts_bans_and_keeps_other_bans_with_null_actor(db, make_user, make_room):
    prefix = f"zz{uuid.uuid4().hex[:6]}_"
    owner, outside = make_user(), make_user()
    actor, target = account(prefix + "actor"), account(prefix + "target")
    room = make_room(owner, actor)
    assert owner.request("PUT", f"/api/rooms/{room}/admins/{actor.username}").status_code == 200
    for user in (outside, target):
        assert actor.request("PUT", f"/api/rooms/{room}/bans/{user.username}").status_code == 200
    report, _, _ = cleanup_test_data.cleanup(db, [prefix], [], False, test_users=False)
    assert report["bans_deleted"] == 1
    assert count(db, "SELECT COUNT(*) FROM room_bans WHERE room_id=%s", (room,)) == 2
    report, _, _ = cleanup_test_data.cleanup(db, [prefix], [], True, test_users=False)
    assert report["bans_deleted"] == 1
    remaining = owner.get(f"/api/rooms/{room}/bans").json()["bans"]
    assert len(remaining) == 1 and remaining[0]["username"] == outside.username
    assert remaining[0]["banned_by"] is None
