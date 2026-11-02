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


def test_flyway_applied_every_migration(db):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT version, success FROM flyway_schema_history "
            "WHERE version IS NOT NULL ORDER BY installed_rank"
        )
        rows = cursor.fetchall()
    # V1 is either applied or recorded as the baseline of an older database.
    assert [version for version, _ in rows] == ["1", "2"]
    assert all(success for _, success in rows)


def test_legacy_migration_table_is_gone(db):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'schema_migrations'"
        )
        assert cursor.fetchone()[0] == 0
