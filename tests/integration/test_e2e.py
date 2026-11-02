from conftest import CONFIRM_DELETE
from test_messaging import history, seqs


def test_full_room_lifecycle(make_user, make_socket):
    alice, bob = make_user(), make_user()

    room_id = alice.post("/api/rooms", json={"name": "lifecycle"}).json()["id"]
    alice.post(f"/api/rooms/{room_id}/invites", json={"username": bob.username})
    assert bob.post(f"/api/rooms/{room_id}/join").status_code == 200

    alice_socket, bob_socket = make_socket(alice), make_socket(bob)
    alice_socket.subscribe(room_id)
    bob_socket.subscribe(room_id)

    alice_socket.post(room_id, "hi bob")
    assert bob_socket.expect("message")["content"] == "hi bob"
    assert alice_socket.expect("message")["content"] == "hi bob"
    bob_socket.post(room_id, "hi alice")
    assert alice_socket.expect("message")["content"] == "hi alice"

    # Bob misses a message while offline and backfills after reconnecting.
    bob_socket.close()
    missed = alice_socket.post(room_id, "while you were away")["seq"]
    bob_socket = make_socket(bob)
    bob_socket.subscribe(room_id)
    assert seqs(history(bob, room_id, after=missed - 1)) == [missed]

    # Export: every message is reachable a page at a time.
    assert seqs(history(bob, room_id, after=0, limit=100)) == [1, 2, 3]

    assert bob.post(f"/api/rooms/{room_id}/leave").json()["room_deleted"] is False
    assert history(alice, room_id)["last_seq"] == 3
    response = alice.post(f"/api/rooms/{room_id}/leave", json=CONFIRM_DELETE)
    assert response.json()["room_deleted"] is True
    assert alice.get(f"/api/rooms/{room_id}/messages").status_code == 404
