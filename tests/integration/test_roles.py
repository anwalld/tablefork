from conftest import CONFIRM_DELETE, error_code


def members(client, room_id):
    return client.get(f"/api/rooms/{room_id}/members").json()["members"]


def role_of(client, room_id, user):
    return next(m["role"] for m in members(client, room_id) if m["name"] == user.username)


def transfer(owner, room_id, user):
    return owner.post(f"/api/rooms/{room_id}/owner", json={"username": user.username})


def appoint(owner, room_id, user):
    return owner.request("PUT", f"/api/rooms/{room_id}/admins/{user.username}")


def dismiss(owner, room_id, user):
    return owner.request("DELETE", f"/api/rooms/{room_id}/admins/{user.username}")


def test_creator_is_owner(make_user, make_room):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    assert role_of(alice, room_id, alice) == "owner"
    assert role_of(alice, room_id, bob) == "member"


def test_owner_cannot_leave_without_transferring(make_user, make_room):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    response = alice.post(f"/api/rooms/{room_id}/leave", json=CONFIRM_DELETE)
    assert response.status_code == 409
    assert error_code(response) == "owner_must_transfer"
    assert role_of(bob, room_id, alice) == "owner"


def test_last_human_owner_may_leave_and_delete(make_user, make_room):
    alice = make_user()
    room_id = make_room(alice)
    response = alice.post(f"/api/rooms/{room_id}/leave", json=CONFIRM_DELETE)
    assert response.status_code == 200
    assert response.json()["room_deleted"] is True


def test_transfer_then_leave(make_user, make_room, make_socket):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    socket = make_socket(bob)
    socket.subscribe(room_id)
    assert transfer(alice, room_id, bob).status_code == 200
    changes = {}
    for _ in range(2):
        event = socket.expect("role_changed")
        changes[event["member_id"]] = event["role"]
    assert sorted(changes.values()) == ["member", "owner"]
    assert role_of(alice, room_id, bob) == "owner"
    assert role_of(alice, room_id, alice) == "member"
    assert alice.post(f"/api/rooms/{room_id}/leave").status_code == 200


def test_transfer_to_non_member_is_rejected(make_user, make_room):
    alice, stranger = make_user(), make_user()
    room_id = make_room(alice)
    response = transfer(alice, room_id, stranger)
    assert response.status_code == 400
    assert role_of(alice, room_id, alice) == "owner"


def test_only_the_owner_transfers(make_user, make_room):
    alice, bob, carol = make_user(), make_user(), make_user()
    room_id = make_room(alice, bob, carol)
    response = transfer(bob, room_id, carol)
    assert response.status_code == 403
    assert error_code(response) == "forbidden"


def test_appoint_and_dismiss_admin(make_user, make_room, make_socket):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    socket = make_socket(bob)
    socket.subscribe(room_id)
    assert appoint(alice, room_id, bob).status_code == 200
    assert socket.expect("role_changed")["role"] == "admin"
    assert role_of(alice, room_id, bob) == "admin"
    assert dismiss(alice, room_id, bob).status_code == 200
    assert role_of(alice, room_id, bob) == "member"


def test_only_the_owner_appoints(make_user, make_room):
    alice, bob, carol = make_user(), make_user(), make_user()
    room_id = make_room(alice, bob, carol)
    assert appoint(alice, room_id, bob).status_code == 200
    response = appoint(bob, room_id, carol)
    assert response.status_code == 403
    assert error_code(response) == "forbidden"


def test_admin_role_ends_on_leaving(make_user, make_room):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    appoint(alice, room_id, bob)
    assert bob.post(f"/api/rooms/{room_id}/leave").status_code == 200
    alice.post(f"/api/rooms/{room_id}/invites", json={"username": bob.username})
    bob.post(f"/api/rooms/{room_id}/join")
    assert role_of(alice, room_id, bob) == "member"


def test_ai_cannot_be_appointed(make_user, make_room):
    alice = make_user()
    room_id = make_room(alice)
    # AI members have no user name, so any AI's display name is not a human
    # member of the room.
    response = alice.request("PUT", f"/api/rooms/{room_id}/admins/Claude")
    assert response.status_code == 400


def test_non_member_gets_room_not_found(make_user, make_room):
    alice, stranger = make_user(), make_user()
    room_id = make_room(alice)
    response = transfer(stranger, room_id, alice)
    assert response.status_code == 404
