from conftest import PASSWORD, error_code, unique_name


def test_collaboration_switch_requires_owner_or_admin(make_user, make_room):
    owner = make_user()
    member = make_user()
    admin = make_user()
    room = make_room(owner, member, admin)
    assert owner.request("PUT", f"/api/rooms/{room}/admins/{admin.username}").status_code == 200

    # 普通成员无权切换
    response = member.request("PUT", f"/api/rooms/{room}/collaboration", json={"enabled": True})
    assert response.status_code == 403
    assert error_code(response) == "forbidden"

    # 群主和管理员可以切换，且可以关回
    assert owner.request("PUT", f"/api/rooms/{room}/collaboration", json={"enabled": True}).status_code == 200
    assert admin.request("PUT", f"/api/rooms/{room}/collaboration", json={"enabled": False}).status_code == 200


def test_collaboration_switch_broadcasts_to_room(make_user, make_room, make_socket):
    owner = make_user()
    member = make_user()
    room = make_room(owner, member)
    socket = make_socket(member)
    socket.subscribe(room)

    assert owner.request("PUT", f"/api/rooms/{room}/collaboration", json={"enabled": True}).status_code == 200
    event = socket.expect("collaboration_changed", timeout=5)
    assert event["enabled"] is True
    assert event["room_id"] == room

    assert owner.request("PUT", f"/api/rooms/{room}/collaboration", json={"enabled": False}).status_code == 200
    event = socket.expect("collaboration_changed", timeout=5)
    assert event["enabled"] is False


def test_new_rooms_collaborate_by_default(make_user, db):
    owner = make_user()
    room = owner.post("/api/rooms", json={"name": "协作"}).json()["id"]
    with db.cursor() as cursor:
        cursor.execute("SELECT collaboration_enabled FROM rooms WHERE id=%s", (room,))
        assert cursor.fetchone()[0] == 1