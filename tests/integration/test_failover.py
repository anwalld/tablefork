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


def observe_controlled_switch(new_host="redis-1", old_host="redis-2"):
    old = node(old_host)
    switched_at = None
    drained = False
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            state = proxy_state()
            if state["srv_addr"] == socket.gethostbyname(new_host):
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


def wait_for_sentinel_election(sentinel):
    observers = [node(f"sentinel-{index}", 26379) for index in (1, 2, 3)]
    try:
        wait_for(lambda: all(observer.info("sentinel")["sentinel_tilt"] == 0
            for observer in observers) and "failover-in-progress" not in
            sentinel.sentinel_master("chat")["flags"], timeout=120)
    finally:
        for observer in observers:
            observer.close()


@pytest.mark.skipif(PHASE != "controlled", reason="controlled Redis switch phase")
def test_controlled_switch_drains_existing_connections(redis):
    sentinel = node("sentinel-1", 26379)
    try:
        wait_for_sentinel_election(sentinel)
        # End on redis-2 so prepare always exercises the return switch on CI,
        # including on a fresh stack whose initial primary is redis-1.
        for new_host in ("redis-1", "redis-2"):
            following = node(new_host)
            old_host = "redis-2" if new_host == "redis-1" else "redis-1"
            previous = node(old_host)
            try:
                if following.info("replication")["role"] == "master":
                    continue
                wait_for(lambda: following.info("replication").get("master_link_status") == "up"
                    and proxy_routes_to(old_host))
                redis.set("test:controlled-switch:barrier", "1", ex=60)
                assert redis.wait(1, 3000) == 1
                assert proxy_clients(previous), "no old connections to exercise"
                sentinel.execute_command("SENTINEL", "FAILOVER", "chat")
                observe_controlled_switch(new_host, old_host)
                wait_for(lambda: following.info("replication")["role"] == "master"
                    and proxy_routes_to(new_host))
            finally:
                previous.close()
                following.close()
    finally:
        sentinel.close()


@pytest.mark.skipif(PHASE != "prepare", reason="Redis 故障阶段 prepare")
def test_prepare_redis_failover(make_user, make_room, make_socket, redis):
    primary = node("redis-1")
    sentinel = node("sentinel-1", 26379)
    try:
        # 只延长测试准备等待；实际主故障后的 30 秒恢复要求保持不变。
        # 时钟跳变后 Sentinel 会安全地暂停选举，不能把 TILT 当作服务逻辑失败。
        wait_for_sentinel_election(sentinel)
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


@pytest.mark.skipif(PHASE != "window", reason="Redis business window phase")
def test_business_requests_during_redis_failover(make_user, make_room, make_socket, redis):
    # Start and warm this client before the shell stops the primary.
    assert proxy_routes_to("redis-1")
    user = make_user()
    room_id = make_room(user)
    writer = make_socket(user)
    assert writer.subscribe(room_id)["type"] == "subscribed"
    assert writer.ws.socket.version() == "TLSv1.3"
    # A write on this connection makes WAIT cover all preceding fixture writes.
    # This is test synchronization, not a production consistency promise.
    prefix = f"test:redis-window:{os.environ['FAULT_TEST_ID']}"
    redis.set(prefix + ":fixture-barrier", "1", ex=120)
    assert redis.wait(1, 3000) == 1
    promoted = node("redis-2")
    confirmed, sent, late_errors, samples = {}, set(), [], []
    pending_messages, pending_logins = {}, set()
    login_unknown = []
    try:
        redis.set(prefix + ":ready", "1", ex=120)
        wait_for(lambda: redis.get(prefix + ":begin"))
        started = time.monotonic()
        deadline = started + 30
        redis.set(prefix + ":started", "1", ex=120)

        def send_result(message_id, request_deadline):
            while True:
                remaining = request_deadline - time.monotonic()
                assert remaining > 0, "message response exceeded its deadline"
                if writer.buffer:
                    frame = writer.buffer.pop(0)
                else:
                    frame = json.loads(writer.ws.recv(timeout=remaining))
                if frame["type"] not in ("ack", "error", "queued"):
                    continue
                response_id = frame.get("client_msg_id")
                assert response_id in sent, "response has unknown client_msg_id"
                if response_id != message_id:
                    # Post-ack side effects may report an error later. Attribute
                    # it to that request, never to the next send.
                    assert response_id in confirmed and frame["type"] == "error"
                    assert frame["error"]["retryable"] is True
                    late_errors.append(response_id)
                    continue
                return frame

        def collect_tasks():
            for task_id in list(pending_logins) + list(pending_messages):
                response = user.session.get(BASE_URL + "/api/tasks/" + task_id,
                    timeout=min(5, max(.01, deadline-time.monotonic())))
                if response.status_code != 200:
                    assert response.status_code == 503
                    continue
                task = response.json()
                if task["state"] in ("waiting", "running", "recovering"):
                    continue
                if task_id in pending_logins:
                    # Cross-Redis work already started when a fault is detected
                    # can be unknown. The intent fence forbids blind replay.
                    if task["state"] == "unknown":
                        login_unknown.append(task_id)
                    else:
                        assert task["state"] == "succeeded", task
                        assert task["response"]["status"] == 200, task
                    pending_logins.remove(task_id)
                else:
                    assert task["state"] == "succeeded", task
                    assert task["response"]["status"] == 200, task
                    ack = json.loads(task["response"]["body"])
                    message_id = pending_messages.pop(task_id)
                    assert ack["type"] == "ack" and ack["client_msg_id"] == message_id
                    confirmed[message_id] = ack["seq"]

        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "business recovery exceeded 30 seconds"
            before = time.monotonic()
            response = user.session.post(BASE_URL + "/api/auth/login",
                json={"username": user.username, "password": PASSWORD},
                timeout=min(5, remaining))
            login_seconds = time.monotonic() - before
            assert login_seconds < 5
            assert response.status_code in (200, 202, 503), response.status_code
            if response.status_code == 202:
                assert "Set-Cookie" not in response.headers
                pending_logins.add(response.json()["task_id"])
            if response.status_code == 503:
                assert response.json()["error"]["retryable"] is True
            samples.append(("login", response.status_code == 200, login_seconds))

            before = time.monotonic()
            message_id = writer.send_message(room_id, "Redis window probe")
            sent.add(message_id)
            frame = send_result(message_id, min(before + 5, deadline))
            message_seconds = time.monotonic() - before
            assert message_seconds < 5
            if frame["type"] == "ack":
                assert frame["room_id"] == room_id
                confirmed[message_id] = frame["seq"]
            elif frame["type"] == "queued":
                pending_messages[frame["task_id"]] = message_id
            else:
                assert frame["error"]["retryable"] is True
            samples.append(("message", frame["type"] == "ack", message_seconds))

            collect_tasks()
            if (promoted.info("replication")["role"] == "master"
                    and proxy_routes_to("redis-2")):
                # 202 is only acceptance. Stop adding more probe work once the
                # route is restored, then require every accepted outcome and
                # a fresh direct login/ACK inside the ORIGINAL 30s deadline.
                # Continuing to add work until an initial response is 200
                # confuses a correctly draining bucket with a failed service.
                while pending_messages or pending_logins:
                    assert time.monotonic() < deadline, "queued outcomes exceeded 30-second recovery"
                    collect_tasks()
                    time.sleep(.05)
                remaining=deadline-time.monotonic()
                assert remaining > 0, "business recovery exceeded 30 seconds"
                response=user.session.post(BASE_URL + "/api/auth/login",
                    json={"username":user.username,"password":PASSWORD},timeout=min(5,remaining))
                assert response.status_code == 200, response.status_code
                before=time.monotonic()
                message_id=writer.send_message(room_id,"Redis restored direct probe")
                sent.add(message_id)
                frame=send_result(message_id,min(before+5,deadline))
                assert frame["type"] == "ack", frame
                assert frame["room_id"] == room_id
                confirmed[message_id]=frame["seq"]
                recovered = time.monotonic() - started
                assert recovered < 30
                break

        collect_tasks()
        assert not pending_messages and not pending_logins, "queued outcomes exceeded 30-second recovery"
        # Every acknowledged message remains exactly once in paginated history.
        stored, cursor = {}, 0
        while True:
            response = user.session.get(BASE_URL + f"/api/rooms/{room_id}/messages",
                params={"after": cursor, "limit": 100}, timeout=5)
            assert response.status_code == 200
            page = response.json()["messages"]
            if not page:
                break
            next_cursor = max(message["seq"] for message in page)
            assert next_cursor > cursor
            for message in page:
                stored.setdefault(message["client_msg_id"], []).append(message["seq"])
            cursor = next_cursor
        assert confirmed, "no message was acknowledged during observation"
        for message_id, seq in confirmed.items():
            assert stored.get(message_id) == [seq]

        for kind in ("login", "message"):
            rows = [sample for sample in samples if sample[0] == kind]
            ok = sum(sample[1] for sample in rows)
            print(f"business window: {kind} samples={len(rows)} ok={ok} "
                f"retryable={len(rows) - ok} max={max(row[2] for row in rows):.3f}s")
        print(f"business window: recovered in {recovered:.3f}s; "
            f"confirmed={len(confirmed)} all present once; "
            f"late post-ack retryable errors={len(late_errors)}; "
            f"explicit cross-Redis unknown={len(login_unknown)}")
    except ConnectionClosed as error:
        print("business window disconnected:",
            "received_close=", getattr(error.rcvd, "code", None),
            "sent_close=", getattr(error.sent, "code", None),
            "receive_error=", repr(writer.ws.recv_exc),
            "completed_samples=", [(kind, ok, round(seconds, 3)) for kind, ok, seconds in samples],
            flush=True)
        raise
    finally:
        if pending_messages or pending_logins:
            print(f"business window diagnostics: samples={len(samples)} "
                f"confirmed={len(confirmed)} pending_messages={len(pending_messages)} "
                f"pending_logins={len(pending_logins)}", flush=True)
            # Safe protocol fields only; never print credentials/cookies/tasks.
            import requests
            for host in ("app-1", "app-2"):
                try:
                    status=requests.post(f"http://{host}:8000/internal/buffer/status",json={},
                        headers={"Authorization":"Bearer "+os.environ["BUFFER_CONTROL_TOKEN"]},timeout=2)
                    print(f"business window {host}: {status.text}",flush=True)
                except requests.RequestException as error:
                    print(f"business window {host}: {type(error).__name__}",flush=True)
        promoted.close()
        try:
            redis.delete(*(prefix + ":" + suffix for suffix in ("fixture-barrier", "ready", "begin", "started")))
        except redis_client.RedisError:
            # Short TTL bounds cleanup if the assertion failed during outage.
            pass
