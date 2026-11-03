import threading
import time

import pytest

from conftest import CONFIRM_DELETE, error_code, unique_name


def room_ids(user):
    return [room["id"] for room in user.get("/api/rooms").json()["rooms"]]


def status_of(user, room_id):
    return next(
        room["status"]
        for room in user.get("/api/rooms").json()["rooms"]
        if room["id"] == room_id
    )


def invite(owner, room_id, guest):
    return owner.post(f"/api/rooms/{room_id}/invites", json={"username": guest.username})


def test_rooms_require_login(anon):
    assert anon.get("/api/rooms").status_code == 401
    assert anon.post("/api/rooms", json={"name": "x"}).status_code == 401


def test_create_room(make_user):
    user = make_user()
    response = user.post("/api/rooms", json={"name": "我的房间"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "我的房间"
    members = user.get(f"/api/rooms/{body['id']}/members").json()["members"]
    assert [m["name"] for m in members] == [user.username]
    assert members[0]["type"] == "human"


def test_create_room_invalid_name(make_user):
    user = make_user()
    for name in ("", "x" * 65, "a\u0000b", "a\nb"):
        response = user.post("/api/rooms", json={"name": name})
        assert response.status_code == 400
        assert error_code(response) == "invalid_argument"


def test_invite_makes_room_visible(make_user, make_room):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner)
    assert room_id not in room_ids(guest)
    assert invite(owner, room_id, guest).status_code == 200
    assert status_of(guest, room_id) == "invited"


def test_invite_unknown_user(make_user, make_room):
    owner = make_user()
    room_id = make_room(owner)
    response = owner.post(
        f"/api/rooms/{room_id}/invites", json={"username": unique_name()}
    )
    assert response.status_code == 404
    assert error_code(response) == "user_not_found"


def test_invite_existing_member_is_a_noop(make_user, make_room, db):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    assert invite(owner, room_id, guest).status_code == 200
    assert status_of(guest, room_id) == "joined"
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM room_invites WHERE room_id=%s", (room_id,))
        assert cursor.fetchone()[0] == 0


def test_non_member_cannot_invite(make_user, make_room):
    owner, stranger, target = make_user(), make_user(), make_user()
    room_id = make_room(owner)
    response = invite(stranger, room_id, target)
    assert response.status_code == 404
    assert error_code(response) == "room_not_found"


def test_invited_user_can_join(make_user, make_room, db):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner)
    invite(owner, room_id, guest)
    assert guest.post(f"/api/rooms/{room_id}/join").status_code == 200
    assert status_of(guest, room_id) == "joined"
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM room_invites WHERE room_id=%s", (room_id,))
        assert cursor.fetchone()[0] == 0


def test_rejoin_is_idempotent(make_user, make_room):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    assert guest.post(f"/api/rooms/{room_id}/join").status_code == 200
    members = owner.get(f"/api/rooms/{room_id}/members").json()["members"]
    assert len(members) == 2


def test_uninvited_user_cannot_join(make_user, make_room):
    owner, stranger = make_user(), make_user()
    room_id = make_room(owner)
    response = stranger.post(f"/api/rooms/{room_id}/join")
    assert response.status_code == 404
    assert error_code(response) == "room_not_found"


def test_join_missing_room(make_user):
    response = make_user().post("/api/rooms/999999999/join")
    assert response.status_code == 404
    assert error_code(response) == "room_not_found"


def test_leave_keeps_room_for_others(make_user, make_room):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    response = guest.post(f"/api/rooms/{room_id}/leave")
    assert response.status_code == 200
    assert response.json()["room_deleted"] is False
    assert room_id not in room_ids(guest)
    assert owner.get(f"/api/rooms/{room_id}/members").status_code == 200


def test_rejoin_after_leaving_needs_new_invite(make_user, make_room):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    guest.post(f"/api/rooms/{room_id}/leave")
    assert guest.post(f"/api/rooms/{room_id}/join").status_code == 404
    invite(owner, room_id, guest)
    assert guest.post(f"/api/rooms/{room_id}/join").status_code == 200


def test_non_member_leave(make_user, make_room):
    owner, stranger = make_user(), make_user()
    room_id = make_room(owner)
    response = stranger.post(f"/api/rooms/{room_id}/leave")
    assert response.status_code == 404
    assert error_code(response) == "room_not_found"


def test_last_human_leaving_deletes_room_even_with_ai_member(
    make_user, make_room, make_socket, db, redis
):
    owner, invited = make_user(), make_user()
    room_id = make_room(owner)
    invite(owner, room_id, invited)
    socket = make_socket(owner)
    assert socket.post(room_id)["type"] == "ack"
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO members (type, display_name) VALUES ('ai', 'bot')"
        )
        cursor.execute(
            "INSERT INTO room_members (room_id, member_id) VALUES (%s, %s)",
            (room_id, cursor.lastrowid),
        )
    response = owner.post(f"/api/rooms/{room_id}/leave", json=CONFIRM_DELETE)
    assert response.json()["room_deleted"] is True
    with db.cursor() as cursor:
        for table in ("messages", "room_members", "room_invites"):
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE room_id=%s", (room_id,))
            assert cursor.fetchone()[0] == 0
    time.sleep(0.2)
    assert not redis.exists(f"room:{room_id}:recent")
    assert room_id not in room_ids(invited)
    assert invited.post(f"/api/rooms/{room_id}/join").status_code == 404


def test_concurrent_join_and_last_leave_is_consistent(make_user, make_room, db):
    for _ in range(10):
        owner, guest = make_user(), make_user()
        room_id = make_room(owner)
        invite(owner, room_id, guest)
        results = {}

        def run(name, client, path):
            results[name] = client.post(path, json=CONFIRM_DELETE)

        threads = [
            threading.Thread(
                target=run, args=("join", guest, f"/api/rooms/{room_id}/join")
            ),
            threading.Thread(
                target=run, args=("leave", owner, f"/api/rooms/{room_id}/leave")
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM rooms WHERE id=%s", (room_id,))
            exists = cursor.fetchone()[0] == 1
        if results["join"].status_code == 200:
            # Joined first: the owner was not the last human, so must transfer.
            assert exists
            assert results["leave"].status_code == 409
            assert error_code(results["leave"]) == "owner_must_transfer"
        else:
            assert not exists and results["leave"].json()["room_deleted"] is True


def test_last_human_must_confirm_deletion(make_user, make_room, make_socket):
    owner = make_user()
    room_id = make_room(owner)
    make_socket(owner).post(room_id)
    response = owner.post(f"/api/rooms/{room_id}/leave")
    assert response.status_code == 409
    assert error_code(response) == "confirmation_required"
    assert room_id in room_ids(owner)
    assert owner.get(f"/api/rooms/{room_id}/messages").json()["last_seq"] == 1


def test_stale_prompt_is_caught_when_the_other_human_left_first(make_user, make_room):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    # Alice's client saw two humans, so it would not warn or confirm.
    assert bob.post(f"/api/rooms/{room_id}/leave").status_code == 200
    response = alice.post(f"/api/rooms/{room_id}/leave")
    assert error_code(response) == "confirmation_required"
    assert room_id in room_ids(alice)


def test_room_list_pages_through_everything(make_user, make_room):
    user = make_user()
    created = {make_room(user) for _ in range(3)}
    first = user.get("/api/rooms", params={"limit": 2}).json()["rooms"]
    second = user.get("/api/rooms", params={"limit": 2, "offset": 2}).json()["rooms"]
    assert {room["id"] for room in first + second} == created


def test_hidden_room_returns_not_found_everywhere(make_user, make_room):
    owner, stranger, target = make_user(), make_user(), make_user()
    room_id = make_room(owner)
    responses = [
        stranger.get(f"/api/rooms/{room_id}/members"),
        stranger.get(f"/api/rooms/{room_id}/messages"),
        stranger.post(f"/api/rooms/{room_id}/join"),
        stranger.post(f"/api/rooms/{room_id}/leave"),
        invite(stranger, room_id, target),
    ]
    missing = stranger.get("/api/rooms/999999999/members")
    for response in responses:
        assert response.status_code == 404
        assert response.json() == missing.json()


def test_invited_user_cannot_read_before_joining(make_user, make_room):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner)
    invite(owner, room_id, guest)
    assert guest.get(f"/api/rooms/{room_id}/messages").status_code == 404


@pytest.mark.parametrize(
    "room_id", ["abc", "1.5", "12abc", "99999999999999999999", "-1", "0x10"]
)
def test_malformed_room_id_is_not_found(make_user, room_id):
    user = make_user()
    missing = user.get("/api/rooms/999999999/members")
    responses = [
        user.get(f"/api/rooms/{room_id}/members"),
        user.get(f"/api/rooms/{room_id}/messages"),
        user.post(f"/api/rooms/{room_id}/join"),
        user.post(f"/api/rooms/{room_id}/leave"),
        user.post(f"/api/rooms/{room_id}/invites", json={"username": "x"}),
    ]
    for response in responses:
        assert response.status_code == 404
        assert response.json() == missing.json()


def test_malformed_room_id_still_requires_login(anon):
    assert anon.get("/api/rooms/abc/members").status_code == 401
