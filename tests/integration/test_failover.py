"""由 scripts/test.sh 分阶段运行的 Redis 主节点故障回归。"""
import json
import os
import socket
import time

import pytest
import redis as redis_client
from websockets.exceptions import ConnectionClosed

from ai_helpers import add_ai, ai_reply, store_key
from conftest import BASE_URL, PASSWORD, Client

PHASE = os.environ.get("FAILOVER_PHASE")
STATE_KEY = "test:redis-failover:state"


def node(host, port=6379):
    return redis_client.Redis(host=host, port=port, password=os.environ["REDIS_PASSWORD"],
        decode_responses=True, socket_connect_timeout=2, socket_timeout=2)


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (redis_client.RedisError, OSError) as error:
            last_error = type(error).__name__
        time.sleep(0.1)
    raise AssertionError(f"故障切换未在 {timeout}s 内完成，最后错误类型：{last_error}")


def proxy_state():
    with socket.create_connection(("redis-proxy", 9999), timeout=2) as connection:
        connection.sendall(b"show servers state\n")
        chunks = []
        while data := connection.recv(65536):
            chunks.append(data)
    columns = None
    servers = []
    for line in b"".join(chunks).decode().splitlines():
        if line.startswith("# "):
            columns = line[2:].split()
        elif columns and line.strip():
            row = dict(zip(columns, line.split()))
            if row.get("be_name") == "redis_master":
                servers.append(row)
    assert len(servers) == 1 and servers[0]["srv_name"] == "master", servers
    return servers[0]


def proxy_routes_to(host):
    state = proxy_state()
    return state["srv_addr"] == socket.gethostbyname(host) and state["srv_op_state"] == "2"


def proxy_clients(cache):
    address = socket.gethostbyname("redis-proxy")
    return [client for client in cache.client_list()
        if client["addr"].rsplit(":", 1)[0].strip("[]") == address]


def observe_controlled_switch():
    old = node("redis-2")
    switched_at = None
    drained = False
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            state = proxy_state()
            if state["srv_addr"] == socket.gethostbyname("redis-1"):
                switched_at = switched_at or time.monotonic()
            clients = proxy_clients(old)
            if switched_at is not None:
                if not clients:
                    drained = True
                if drained or time.monotonic() - switched_at >= 1:
                    assert not clients, "受控切换后旧主仍有代理连接"
                if drained and old.info("replication")["role"] == "slave":
                    print("controlled switch: old proxy connections drained within 1s and stayed at zero")
                    return
            time.sleep(0.2)
        raise AssertionError("受控切换的代理路由/旧连接检查未完成")
    finally:
        old.close()


def restore_user(data):
    client = Client()
    client.username = data["username"]
    for part in data["cookie"].split("; "):
        name, value = part.split("=", 1)
        client.session.cookies.set(name, value)
    return client


@pytest.mark.skipif(PHASE != "prepare", reason="Redis 故障阶段 prepare")
def test_prepare_redis_failover(make_user, make_room, make_socket, redis):
    primary = node("redis-1")
    sentinel = node("sentinel-1", 26379)
    try:
        # 只延长测试准备等待；实际主故障后的 30 秒恢复要求保持不变。
        # 时钟跳变后 Sentinel 会安全地暂停选举，不能把 TILT 当作服务逻辑失败。
        observers = [node(f"sentinel-{index}", 26379) for index in (1, 2, 3)]
        try:
            wait_for(lambda: all(observer.info("sentinel")["sentinel_tilt"] == 0
                for observer in observers) and "failover-in-progress" not in
                sentinel.sentinel_master("chat")["flags"], timeout=120)
        finally:
            for observer in observers:
                observer.close()
        # 上一次运行可能已提升 redis-2。通过 Sentinel 恢复同一测试起点，
        # 确保每次 stop redis-1 都是真正停止主节点，而不是停止一个从节点。
        if primary.info("replication")["role"] != "master":
            wait_for(lambda: primary.info("replication").get("master_link_status") == "up")
            redis.set("test:redis-failover:barrier", "1", ex=60)
            assert redis.wait(1, 3000) == 1
            sentinel.execute_command("SENTINEL", "FAILOVER", "chat")
            observe_controlled_switch()
        run_id = primary.info("server")["run_id"]
        wait_for(lambda: primary.info("replication")["role"] == "master" and
            redis.info("server")["run_id"] == run_id)
        replica = node("redis-2")
        try:
            wait_for(lambda: replica.info("replication")["role"] == "slave" and
                replica.info("replication").get("master_link_status") == "up")
        finally:
            replica.close()
        wait_for(lambda: len(sentinel.execute_command("SENTINEL", "SENTINELS", "chat")) == 2)
        wait_for(lambda: proxy_routes_to("redis-1"))
    finally:
        primary.close()
        sentinel.close()

    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    assert store_key(owner, "anthropic").status_code == 200
    assert add_ai(owner, room_id, "claude").status_code == 200
    socket = make_socket(owner)
    assert socket.post(room_id, "confirmed before failover")["seq"] == 1
    redis.set(STATE_KEY, json.dumps({"room": room_id,
        "owner": {"username": owner.username, "cookie": owner.cookie_header()},
        "guest": {"username": guest.username, "cookie": guest.cookie_header()}}), ex=1800)
    # 仅测试同步夹具状态，产品不引入 WAIT 或强一致承诺。
    assert redis.wait(1, 3000) == 1
    print("baseline primary: redis-1")


@pytest.mark.skipif(PHASE != "after", reason="Redis 故障阶段 after")
def test_redis_primary_failure_recovers_without_restarts(redis, make_socket):
    import requests
    started = float(os.environ.get("FAILOVER_STOPPED_AT", time.time()))
    client = Client()
    deadline = time.monotonic() + 30
    # 切换期间请求必须有界结束，而不是挂起；恢复后 health 返回 200。
    while True:
        try:
            response = client.session.get(BASE_URL + "/health", timeout=5)
            assert response.status_code in (200, 502, 503)
            if response.status_code == 200:
                break
        except requests.ConnectionError:
            pass
        assert time.monotonic() < deadline
        time.sleep(0.1)
    client.session.close()
    state = json.loads(wait_for(lambda: redis.get(STATE_KEY)))
    owner, guest = restore_user(state["owner"]), restore_user(state["guest"])
    room_id = state["room"]
    promoted = node("redis-2")
    try:
        assert promoted.info("replication")["role"] == "master"
        assert redis.info("server")["run_id"] == promoted.info("server")["run_id"]
        primary_ready = time.time() - started
        assert primary_ready < 30
        print(f"primary changed redis-1 -> redis-2, ready in {primary_ready:.2f}s")
    finally:
        promoted.close()

    assert owner.post("/api/auth/login", json={"username": owner.username, "password": PASSWORD}).status_code == 200
    before = guest.get(f"/api/rooms/{room_id}/messages").json()["messages"]
    assert before[0]["content"] == "confirmed before failover"
    reader = make_socket(owner, instance=1)
    writer = make_socket(guest, instance=2)
    assert reader.subscribe(room_id)["type"] == "subscribed"
    assert writer.subscribe(room_id)["type"] == "subscribed"
    ack = writer.post(room_id, "cross instance after promotion")
    assert ack["type"] == "ack"
    assert reader.expect("message", timeout=2)["seq"] == ack["seq"]
    assert reader.post(room_id, "@Claude after Redis failover")["type"] == "ack"
    assert ai_reply(reader)["content"].endswith("after Redis failover")
    client = owner.at_instance(2)
    try:
        assert client.post("/api/auth/logout").status_code == 200
    finally:
        client.session.close()
    with pytest.raises(ConnectionClosed) as ended:
        # 可能有正常片段或消息已缓冲，继续读取直到会话断开。
        while True:
            reader.ws.recv(timeout=2)
    assert ended.value.rcvd.code == 4401
    assert time.time() - started < 30


@pytest.mark.skipif(PHASE != "rejoined", reason="Redis 故障阶段 rejoined")
def test_old_primary_rejoins_as_replica(redis):
    returned = node("redis-1")
    ready = f"test:redis-rejoin:{os.environ['FAULT_TEST_ID']}:ready"
    try:
        redis.set(ready, "1", ex=60)
        deadline = time.monotonic() + 30
        samples = 0
        while time.monotonic() < deadline:
            try:
                clients = proxy_clients(returned)
                samples += 1
                assert not clients, "重新加入的旧主收到了代理连接"
                if returned.info("replication")["role"] == "slave":
                    break
            except (redis_client.ConnectionError, redis_client.TimeoutError, OSError):
                # Sampler is ready before the stopped container is started.
                pass
            time.sleep(0.2)
        else:
            raise AssertionError("原主未在 30 秒内重新加入为从节点")
        assert samples > 0
        print(f"old primary rejoin: {samples} samples, zero proxy connections")
        promoted = node("redis-2")
        try:
            run_id = promoted.info("server")["run_id"]
        finally:
            promoted.close()
        wait_for(lambda: returned.info("replication").get("master_link_status") == "up" and
            proxy_routes_to("redis-2") and redis.info("server")["run_id"] == run_id)
        redis.set("test:redis-failover:rejoined", "ok", ex=60)
        assert redis.get("test:redis-failover:rejoined") == "ok"
        print("redis-1 rejoined as replica; proxy still targets redis-2")
    finally:
        redis.delete(ready)
        returned.close()


@pytest.mark.skipif(PHASE != "app-down", reason="应用实例停机阶段")
def test_app_failure_keeps_session_and_backfills(make_user, make_room, make_socket, redis):
    from test_messaging import history, seqs
    user = make_user()
    room_id = make_room(user)
    writer = make_socket(user, instance=2)
    assert writer.post(room_id, "before app failure")["seq"] == 1
    reader = make_socket(user, instance=1)
    assert reader.subscribe(room_id)["type"] == "subscribed"
    ready = f"test:app-fault:{os.environ['FAULT_TEST_ID']}:ready"
    try:
        redis.set(ready, "1", ex=90)
        with pytest.raises(ConnectionClosed):
            while True:
                reader.ws.recv(timeout=20)
        client = user.at_instance(2)
        try:
            assert client.get("/api/auth/me").status_code == 200
        finally:
            client.session.close()
        assert writer.post(room_id, "while app-1 is down")["seq"] == 2
        reconnected = make_socket(user)
        assert reconnected.subscribe(room_id)["type"] == "subscribed"
        assert seqs(history(user, room_id, after=1)) == [2]
        assert writer.post(room_id, "live after reconnect")["seq"] == 3
        while True:
            message = reconnected.expect("message")
            if message["seq"] == 3:
                break
        assert message["content"] == "live after reconnect"
    finally:
        redis.delete(ready)


@pytest.mark.skipif(PHASE != "web-down", reason="L7 实例停机阶段")
def test_new_connections_survive_a_web_instance_failure(make_user):
    from conftest import CA_BUNDLE
    import requests
    user = make_user()
    for _ in range(16):
        with requests.Session() as session:
            response = session.get(BASE_URL + "/api/auth/me", verify=CA_BUNDLE,
                headers={"Cookie": user.cookie_header()}, timeout=5)
            assert response.status_code == 200
            assert response.json()["username"] == user.username
