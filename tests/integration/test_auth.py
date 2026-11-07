import hashlib
import os
import secrets
import time

import pytest

from conftest import PASSWORD, error_code, unique_name


def register(client, username, password=PASSWORD):
    return client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )


def login(client, username, password=PASSWORD):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def test_register_success(anon):
    assert register(anon, unique_name()).status_code == 201


def test_register_duplicate_username(anon):
    name = unique_name()
    assert register(anon, name).status_code == 201
    response = register(anon, name)
    assert response.status_code == 409
    assert error_code(response) == "username_taken"


def test_register_invalid_format(anon):
    for username, password in [
        ("ab", PASSWORD),
        ("x" * 33, PASSWORD),
        (unique_name(), "short"),
        (unique_name(), "nine-char"),
    ]:
        response = register(anon, username, password)
        assert response.status_code == 400
        assert error_code(response) == "invalid_argument"


def test_register_rejects_common_passwords(anon):
    for password in ["password123", "QwertyUiop"]:
        response = register(anon, unique_name(), password)
        assert response.status_code == 400
        assert error_code(response) == "weak_password"


def test_register_rejects_the_username_as_password(anon):
    name = unique_name() + "Ab"
    response = register(anon, name, name.lower())
    assert response.status_code == 400
    assert error_code(response) == "weak_password"


def scrypt_hash(password):
    """The server's stored format: scrypt$<salt hex>$<key hex>."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=1 << 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32
    )
    return f"scrypt${salt.hex().upper()}${key.hex().upper()}"


def test_accounts_with_older_short_passwords_still_log_in(anon, db):
    name = unique_name()
    assert register(anon, name).status_code == 201
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE users SET password_hash=%s WHERE username=%s",
            (scrypt_hash("8chars!!"), name),
        )
    assert login(anon, name, "8chars!!").status_code == 200


def test_register_counts_characters_not_bytes(anon):
    assert register(anon, "用户" + unique_name()[:6]).status_code == 201


def test_password_is_not_stored_in_plaintext(anon, db):
    name = unique_name()
    register(anon, name)
    with db.cursor() as cursor:
        cursor.execute("SELECT password_hash FROM users WHERE username=%s", (name,))
        stored = cursor.fetchone()[0]
    assert PASSWORD not in stored
    assert stored.startswith("scrypt$")


def test_login_sets_http_only_cookie(anon):
    name = unique_name()
    register(anon, name)
    response = login(anon, name)
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert "chat_session=" in cookie
    assert "httponly" in cookie.lower()
    assert PASSWORD not in response.text


def test_login_failures_are_indistinguishable(anon):
    name = unique_name()
    register(anon, name)
    wrong_password = login(anon, name, "wrong-password")
    unknown_user = login(anon, unique_name())
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_valid_session_is_accepted(make_user):
    user = make_user()
    response = user.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == user.username


def test_missing_or_invalid_session_is_rejected(anon):
    response = anon.get("/api/auth/me")
    assert response.status_code == 401
    assert error_code(response) == "unauthenticated"
    response = anon.get("/api/auth/me", headers={"Cookie": "chat_session=forged"})
    assert response.status_code == 401


def test_expired_session_is_rejected(make_user, redis):
    user = make_user()
    for key in redis.scan_iter("session:*"):
        value = redis.get(key)
        if value.endswith("|" + user.username):
            redis.pexpire(key, 1)
    time.sleep(0.05)
    assert user.get("/api/auth/me").status_code == 401


def test_session_has_a_ttl(make_user, redis):
    user = make_user()
    ttls = [
        redis.ttl(key)
        for key in redis.scan_iter("session:*")
        if redis.get(key).endswith("|" + user.username)
    ]
    assert len(ttls) == 1 and ttls[0] > 0


def test_token_is_stored_hashed(make_user, redis):
    user = make_user()
    token = user.session.cookies["chat_session"]
    assert not any(token in key for key in redis.scan_iter("session:*"))


def test_logout_invalidates_session(make_user):
    user = make_user()
    cookies = user.cookie_header()
    assert user.post("/api/auth/logout").status_code == 200
    response = user.get("/api/auth/me", headers={"Cookie": cookies})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "username", ["abc\u0000def", "abc\ndef", "abc\u007fdef", "abc\u0085def"]
)
def test_username_rejects_control_characters(anon, username):
    response = register(anon, username)
    assert response.status_code == 400
    assert error_code(response) == "invalid_argument"


def test_login_with_control_characters_looks_like_unknown_user(anon):
    response = login(anon, unique_name() + "\u0000x")
    assert response.status_code == 401
    assert response.json() == login(anon, unique_name()).json()


@pytest.mark.skipif(
    not os.environ.get("STRICT_AUTH_RATE"),
    reason="needs the default AUTH_RATE and AUTH_BURST on Nginx",
)
def test_auth_attempts_are_rate_limited(anon):
    statuses = [login(anon, unique_name(), "wrong-password").status_code for _ in range(30)]
    assert statuses[0] == 401
    assert 429 in statuses
    limited = login(anon, unique_name(), "wrong-password")
    assert limited.status_code == 429
    assert error_code(limited) == "rate_limited"
    error = limited.json()["error"]
    assert error["retryable"]
    assert error["retry_after"] >= 1
    assert limited.headers["Retry-After"] == str(error["retry_after"])
    # Password recovery shares the same per-address budget: each reset check
    # costs a slow hash, and answers must not be sprayed across usernames.
    for path, body in (
        ("/api/auth/password-recovery/question", {"username": unique_name()}),
        ("/api/auth/password-recovery/reset",
         {"username": unique_name(), "answer": "x", "password": "irrelevant-password-1"}),
    ):
        recovery = anon.post(path, json=body)
        assert recovery.status_code == 429
        assert error_code(recovery) == "rate_limited"
    # Only the auth endpoints are limited.
    assert anon.get("/health").status_code == 200
    assert anon.get("/api/auth/me").status_code == 401
