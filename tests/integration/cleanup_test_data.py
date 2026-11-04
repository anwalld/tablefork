"""Removes the accounts, rooms and messages that tests leave behind.

    docker compose --profile test run --rm tests python cleanup_test_data.py [--apply]
        [--prefix owner_ --prefix guest_ ...] [--exclude realtest_]

Matches integration test users (u followed by 12 hex digits, or 用户u and 5
hex digits) and any user
whose name starts with a --prefix, except names starting with an --exclude
(realtest_ by default). Without --apply it only reports what it would
delete, then rolls back. Everything happens in one transaction.
"""

import argparse
import os
import re
import sys

import pymysql
import redis as redis_client

# Names the integration tests generate: unique_name(), and the one that checks
# names are counted in characters ("用户" plus part of a unique name).
TEST_USER = re.compile(r"^(u[0-9a-f]{12}|用户u[0-9a-f]{5})$")


def matches(username, prefixes, excludes, test_users=True):
    if any(username.startswith(e) for e in excludes):
        return False
    if test_users and TEST_USER.match(username):
        return True
    return any(username.startswith(p) for p in prefixes)


def in_list(values):
    return ", ".join(["%s"] * len(values))


def cleanup(db, prefixes, excludes, apply, test_users=True):
    """Returns (report, room ids whose cache must go, deleted user ids)."""
    report = {"bans_deleted": 0}
    # An explicit transaction, so that a dry run rolls back even on a
    # connection that autocommits.
    db.begin()
    with db.cursor() as cursor:
        cursor.execute("SELECT u.id, u.username, m.id FROM users u JOIN members m ON m.user_id = u.id")
        doomed = [(uid, mid) for uid, name, mid in cursor.fetchall() if matches(name, prefixes, excludes, test_users)]
        report["users"] = len(doomed)
        if not doomed:
            db.rollback()
            return report, [], []
        users = [uid for uid, _ in doomed]
        members = [mid for _, mid in doomed]

        # Rooms whose humans are all doomed go entirely.
        cursor.execute(
            "SELECT r.id FROM rooms r WHERE NOT EXISTS ("
            "  SELECT 1 FROM room_members rm JOIN members m ON m.id = rm.member_id"
            f"  WHERE rm.room_id = r.id AND m.type = 'human' AND m.id NOT IN ({in_list(members)}))",
            members,
        )
        rooms = [row[0] for row in cursor.fetchall()]
        report["rooms_deleted"] = len(rooms)
        # 先显式删除并统计会由房间/用户级联移除的封禁；执行者被删只置空，不删其他人的封禁。
        condition = f"user_id IN ({in_list(users)})"
        values = list(users)
        if rooms:
            condition += f" OR room_id IN ({in_list(rooms)})"
            values += rooms
        report["bans_deleted"] = cursor.execute("DELETE FROM room_bans WHERE " + condition, values)
        if rooms:
            cursor.execute(f"DELETE FROM rooms WHERE id IN ({in_list(rooms)})", rooms)

        # Elsewhere, hand ownership to the earliest remaining human first.
        cursor.execute(
            f"SELECT room_id FROM room_members WHERE role = 'owner' AND member_id IN ({in_list(members)})",
            members,
        )
        owned = [row[0] for row in cursor.fetchall()]
        for room_id in owned:
            cursor.execute(
                "SELECT rm.member_id FROM room_members rm JOIN members m ON m.id = rm.member_id "
                f"WHERE rm.room_id = %s AND m.type = 'human' AND rm.member_id NOT IN ({in_list(members)}) "
                "ORDER BY rm.joined_at, rm.member_id LIMIT 1",
                [room_id, *members],
            )
            heir = cursor.fetchone()[0]
            cursor.execute(
                "UPDATE room_members SET role = 'owner', role_changed_at = NOW(3) "
                "WHERE room_id = %s AND member_id = %s",
                (room_id, heir),
            )
        report["ownership_transferred"] = len(owned)

        # Rooms that keep going but lose messages: their hot cache is stale.
        cursor.execute(
            f"SELECT DISTINCT room_id FROM messages WHERE member_id IN ({in_list(members)})", members
        )
        touched = [row[0] for row in cursor.fetchall()]

        for label, sql, values in [
            ("messages_deleted", f"DELETE FROM messages WHERE member_id IN ({in_list(members)})", members),
            ("ai_requests_deleted", f"DELETE FROM ai_member_requests WHERE requested_by IN ({in_list(members)})", members),
            ("memberships_deleted", f"DELETE FROM room_members WHERE member_id IN ({in_list(members)})", members),
            ("invites_deleted", f"DELETE FROM room_invites WHERE user_id IN ({in_list(users)}) OR invited_by IN ({in_list(members)})", users + members),
            ("members_deleted", f"DELETE FROM members WHERE id IN ({in_list(members)})", members),
            ("users_deleted", f"DELETE FROM users WHERE id IN ({in_list(users)})", users),
        ]:
            report[label] = cursor.execute(sql, values)

    if apply:
        db.commit()
    else:
        db.rollback()
    return report, rooms + touched, users


def clear_redis(cache, rooms, users):
    """Drops cached state of the given rooms and users; history then falls
    back to MySQL and refills the cache."""
    keys = [f"room:{r}:recent" for r in rooms] + [f"ai:room:{r}" for r in rooms]
    keys += [f"trusted:{u}" for u in users] + [f"user_sessions:{u}" for u in users]
    for start in range(0, len(keys), 500):
        cache.delete(*keys[start:start + 500])
    if rooms:
        cache.zrem("ai:timers", *rooms)
        cache.srem("ai:work", *rooms)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete for real")
    parser.add_argument("--prefix", action="append", default=[], help="also delete users whose name starts with this")
    parser.add_argument("--exclude", action="append", default=None, help="never delete users whose name starts with this")
    parser.add_argument("--only-prefixes", action="store_true", help="skip the default test user pattern")
    args = parser.parse_args(argv)
    excludes = args.exclude if args.exclude is not None else ["realtest_"]

    db = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "mysql"), user="chat",
        password=os.environ["MYSQL_PASSWORD"], database="chat", autocommit=False,
    )
    try:
        report, rooms, users = cleanup(db, args.prefix, excludes, args.apply, not args.only_prefixes)
    finally:
        db.close()
    for key, value in report.items():
        print(f"{key}: {value}")
    if args.apply:
        clear_redis(redis_client.Redis(host=os.environ.get("REDIS_HOST", "redis-proxy"),
                                       password=os.environ["REDIS_PASSWORD"]), rooms, users)
        print("applied")
    else:
        print("dry run: nothing was deleted; add --apply to delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
