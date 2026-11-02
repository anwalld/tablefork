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


def test_deleting_a_room_cascades(room_and_member, db):
    room_id, member_id = room_and_member
    with db.cursor() as cursor:
        insert_message(cursor, room_id, 1, member_id, "a")
        cursor.execute("DELETE FROM rooms WHERE id=%s", (room_id,))
        for table in ("messages", "room_members", "room_invites"):
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE room_id=%s", (room_id,))
            assert cursor.fetchone()[0] == 0
