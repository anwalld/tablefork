import os

import pymysql
import pytest


def insert_message(cursor, room_id, seq, member_id, client_msg_id):
    cursor.execute(
        "INSERT INTO messages "
        "(room_id, seq, member_id, client_msg_id, content, created_at_ms) "
        "VALUES (%s, %s, %s, %s, 'x', 0)",
        (room_id, seq, member_id, client_msg_id),
    )


@pytest.fixture
def room_and_member(make_user, make_room, db):
    user = make_user()
    room_id = make_room(user)
    return room_id, user.get("/api/auth/me").json()["member_id"]


def test_seq_is_unique_per_room(room_and_member, db):
    room_id, member_id = room_and_member
    with db.cursor() as cursor:
        insert_message(cursor, room_id, 1, member_id, "a")
        with pytest.raises(pymysql.err.IntegrityError):
            insert_message(cursor, room_id, 1, member_id, "b")


def test_client_msg_id_is_unique_per_member_and_room(room_and_member, db):
    room_id, member_id = room_and_member
    with db.cursor() as cursor:
        insert_message(cursor, room_id, 1, member_id, "a")
        with pytest.raises(pymysql.err.IntegrityError):
            insert_message(cursor, room_id, 2, member_id, "a")


def test_deleting_a_room_cascades(room_and_member, db, make_user):
    room_id, member_id = room_and_member
    target = make_user()
    with db.cursor() as cursor:
        insert_message(cursor, room_id, 1, member_id, "a")
        cursor.execute("INSERT INTO room_bans (room_id, user_id, banned_by) "
            "SELECT %s, id, %s FROM users WHERE username=%s", (room_id, member_id, target.username))
        cursor.execute("DELETE FROM rooms WHERE id=%s", (room_id,))
        for table in ("messages", "room_members", "room_invites", "room_bans"):
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE room_id=%s", (room_id,))
            assert cursor.fetchone()[0] == 0


def test_flyway_applied_every_migration(db):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT version, success FROM flyway_schema_history "
            "WHERE version IS NOT NULL ORDER BY installed_rank"
        )
        rows = cursor.fetchall()
    # V1 is either applied or recorded as the baseline of an older database.
    assert [version for version, _ in rows] == ["1", "2", "3", "4", "5", "6"]
    assert all(success for _, success in rows)


def test_legacy_migration_table_is_gone(db):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'schema_migrations'"
        )
        assert cursor.fetchone()[0] == 0


def test_preset_ai_members_exist(db):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT p.slug, p.provider, m.type, m.user_id FROM ai_profiles p "
            "JOIN members m ON m.id = p.member_id ORDER BY p.slug"
        )
        rows = cursor.fetchall()
    assert [(slug, provider) for slug, provider, _, _ in rows] == [
        ("claude", "anthropic"),
        ("deepseek", "deepseek"),
        ("gemini", "gemini"),
        ("gpt", "openai"),
    ]
    assert all(kind == "ai" and user_id is None for _, _, kind, user_id in rows)


def test_ai_tables_exist(db):
    with db.cursor() as cursor:
        for table in ("ai_member_requests", "user_api_keys"):
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            assert cursor.fetchone()[0] == 1


def test_every_room_with_humans_has_one_owner(db):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM rooms r WHERE "
            "(SELECT COUNT(*) FROM room_members rm WHERE rm.room_id = r.id AND rm.role = 'owner') <> 1"
        )
        assert cursor.fetchone()[0] == 0


@pytest.fixture
def ai_db():
    connection = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "mysql"),
        user="ai",
        password=os.environ["AI_MYSQL_PASSWORD"],
        database="chat",
        autocommit=True,
    )
    yield connection
    connection.close()


@pytest.mark.parametrize(
    "query",
    [
        "SELECT COUNT(*) FROM user_api_keys",
        "SELECT COUNT(*) FROM ai_profiles",
        "SELECT COUNT(*) FROM messages",
        "SELECT id, type, display_name FROM members LIMIT 1",
    ],
)
def test_ai_account_reads_what_it_needs(ai_db, query):
    with ai_db.cursor() as cursor:
        cursor.execute(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT COUNT(*) FROM users",
        "SELECT user_id FROM members LIMIT 1",
        "SELECT COUNT(*) FROM rooms",
        "DELETE FROM user_api_keys",
        "UPDATE messages SET content = content WHERE 1 = 0",
    ],
)
def test_ai_account_is_denied_everything_else(ai_db, query):
    with ai_db.cursor() as cursor, pytest.raises(pymysql.err.OperationalError):
        cursor.execute(query)


def test_committed_receipt_survives_resource_deletion(make_user, make_room, db):
    import uuid
    owner=make_user(); request_id=uuid.uuid4().hex
    body={"name":"receipt-resource"}
    first=owner.request("POST","/api/rooms",json=body,headers={"X-Request-Id":request_id})
    assert first.status_code==201
    room=first.json()["id"]
    assert owner.post(f"/api/rooms/{room}/leave",json={"confirm_delete":True}).status_code==200
    repeated=owner.request("POST","/api/rooms",json=body,headers={"X-Request-Id":request_id})
    assert repeated.status_code==201 and repeated.json()==first.json()
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM rooms WHERE id=%s",(room,))
        assert cursor.fetchone()[0]==0
        cursor.execute("SELECT COUNT(*) FROM business_request_results WHERE request_id=%s",(request_id,))
        assert cursor.fetchone()[0]==1


def test_replaying_old_kick_does_not_remove_a_rejoined_subscription(make_user,make_room,make_socket):
    import uuid
    owner,guest=make_user(),make_user(); room=make_room(owner)
    def join():
        assert owner.post(f"/api/rooms/{room}/invites",json={"username":guest.username}).status_code==200
        assert guest.post(f"/api/rooms/{room}/join").status_code==200
    join()
    original=make_socket(guest)
    assert original.subscribe(room)["type"]=="subscribed"
    request_id=uuid.uuid4().hex
    path=f"/api/rooms/{room}/members/{guest.username}"
    assert owner.request("DELETE",path,headers={"X-Request-Id":request_id}).status_code==200
    assert original.expect("room_left")["reason"]=="kicked"
    join()
    rejoined=make_socket(guest)
    assert rejoined.subscribe(room)["type"]=="subscribed"
    assert owner.request("DELETE",path,headers={"X-Request-Id":request_id}).status_code==200
    ack=rejoined.post(room,"still subscribed after historical receipt")
    assert ack["type"]=="ack"
    assert rejoined.expect("message")["seq"]==ack["seq"]
