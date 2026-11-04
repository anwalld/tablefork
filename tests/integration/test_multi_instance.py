"""固定实例，覆盖共享会话、跨实例移除以及 Redis 订阅线程存活。"""
import pytest


@pytest.mark.parametrize("instance", [1, 2])
def test_session_is_valid_on_each_instance(make_user, instance):
    user = make_user()
    client = user.at_instance(instance)
    try:
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["username"] == user.username
    finally:
        client.session.close()


@pytest.mark.parametrize("reader_instance", [1, 2])
def test_remote_leave_unsubscribes_without_deadlocking(make_user, make_room, make_socket, reader_instance):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    other_instance = 3 - reader_instance
    reader = make_socket(guest, instance=reader_instance)
    writer = make_socket(owner, instance=other_instance)
    assert reader.subscribe(room_id)["type"] == "subscribed"
    assert writer.subscribe(room_id)["type"] == "subscribed"

    # 发布方没有 guest 的本地连接，必须经 Redis 在接收实例移除最后一个连接。
    client = guest.at_instance(other_instance)
    try:
        assert client.post(f"/api/rooms/{room_id}/leave").status_code == 200
    finally:
        client.session.close()
    assert reader.expect("room_left", timeout=2)["room_id"] == room_id

    # 确认接收实例仍可处理 Redis 命令、再次订阅和消息推送。
    client = owner.at_instance(reader_instance)
    try:
        assert client.get("/health").status_code == 200
        assert client.get("/api/auth/me").status_code == 200
    finally:
        client.session.close()
    replacement = make_socket(owner, instance=reader_instance)
    assert replacement.subscribe(room_id)["type"] == "subscribed"
    ack = writer.post(room_id, "redis loop still alive")
    assert ack["type"] == "ack"
    assert replacement.expect("message")["seq"] == ack["seq"]
    reader.assert_silent()


@pytest.mark.parametrize("reader_instance", [1, 2])
def test_cross_instance_messages_have_consecutive_seq(make_user, make_room, make_socket, reader_instance):
    alice, bob = make_user(), make_user()
    room_id = make_room(alice, bob)
    reader = make_socket(alice, instance=reader_instance)
    writer = make_socket(bob, instance=3 - reader_instance)
    reader.subscribe(room_id)
    for seq in range(1, 4):
        assert writer.post(room_id, f"cross instance {seq}")["seq"] == seq
        assert reader.expect("message")["seq"] == seq


@pytest.mark.parametrize("reader_instance", [1, 2])
def test_logout_on_another_instance_closes_session(make_user, make_room, make_socket, reader_instance):
    from websockets.exceptions import ConnectionClosed
    user = make_user()
    room_id = make_room(user)
    reader = make_socket(user, instance=reader_instance)
    reader.subscribe(room_id)
    client = user.at_instance(3 - reader_instance)
    try:
        assert client.post("/api/auth/logout").status_code == 200
    finally:
        client.session.close()
    with pytest.raises(ConnectionClosed) as ended:
        reader.ws.recv(timeout=2)
    assert ended.value.rcvd.code == 4401


@pytest.mark.parametrize("reader_instance", [1, 2])
def test_kick_from_another_instance_carries_reason_and_keeps_redis_live(make_user, make_room, make_socket, reader_instance):
    owner, guest = make_user(), make_user()
    room = make_room(owner, guest)
    reader = make_socket(guest, instance=reader_instance)
    writer = make_socket(owner, instance=3 - reader_instance)
    reader.subscribe(room)
    writer.subscribe(room)
    client = owner.at_instance(3 - reader_instance)
    try:
        assert client.request("DELETE", f"/api/rooms/{room}/members/{guest.username}").status_code == 200
    finally:
        client.session.close()
    assert reader.expect("room_left")["reason"] == "kicked"
    assert writer.expect("members_changed")["room_id"] == room
    client = owner.at_instance(reader_instance)
    try:
        assert client.get("/health").status_code == 200
    finally:
        client.session.close()
    assert writer.post(room)["type"] == "ack"
    reader.assert_silent()
