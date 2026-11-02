"""Login delay per user name and per known device.

Assumes the default thresholds: 5 free attempts, then waits of 1, 2, 4, ...
seconds.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import PASSWORD, Client, error_code, unique_name

FREE_ATTEMPTS = 5


def register(client, username):
    response = client.post(
        "/api/auth/register", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 201


def login(client, username, password=PASSWORD):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def use_up_free_attempts(client, username):
    for _ in range(FREE_ATTEMPTS):
        assert login(client, username, "wrong-password").status_code == 401


def assert_too_many_attempts(response):
    assert response.status_code == 429
    assert error_code(response) == "too_many_attempts"
    error = response.json()["error"]
    assert error["retryable"]
    assert error["retry_after"] >= 1
    assert response.headers["Retry-After"] == str(error["retry_after"])


@pytest.fixture
def name(anon):
    username = unique_name()
    register(anon, username)
    return username


def test_free_attempts_check_the_password(anon, name):
    use_up_free_attempts(anon, name)


def test_early_retry_is_refused_even_with_the_right_password(anon, name):
    use_up_free_attempts(anon, name)
    assert_too_many_attempts(login(anon, name))


def test_retry_after_the_wait_succeeds_and_resets_the_count(anon, name):
    use_up_free_attempts(anon, name)
    wait = login(anon, name).json()["error"]["retry_after"]
    time.sleep(wait + 0.2)
    assert login(anon, name).status_code == 200
    # The count starts over, so wrong passwords get checked again.
    stranger = Client()
    use_up_free_attempts(stranger, name)


def test_waits_grow(anon, name):
    use_up_free_attempts(anon, name)
    first = login(anon, name).json()["error"]["retry_after"]
    time.sleep(first + 0.2)
    assert login(anon, name, "wrong-password").status_code == 401
    second = login(anon, name).json()["error"]["retry_after"]
    assert second > first


def test_other_addresses_share_the_count(anon, name):
    # Every client here comes from the same address, so this only shows that
    # the count is keyed by name: fresh cookie jars do not reset it.
    for _ in range(FREE_ATTEMPTS):
        assert login(Client(), name, "wrong-password").status_code == 401
    assert_too_many_attempts(login(Client(), name))


def test_unknown_names_behave_like_existing_ones(anon, name):
    unknown = unique_name()
    use_up_free_attempts(anon, unknown)
    known = Client()
    use_up_free_attempts(known, name)
    a, b = login(anon, unknown), login(known, name)
    assert a.status_code == b.status_code == 429
    assert error_code(a) == error_code(b)


def test_concurrent_attempts_cannot_slip_through(name):
    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(
            pool.map(lambda _: login(Client(), name, "wrong-password").status_code, range(20))
        )
    assert statuses.count(401) == FREE_ATTEMPTS
    assert statuses.count(429) == 20 - FREE_ATTEMPTS


def test_known_device_is_not_slowed_by_others(name):
    owner = Client()
    assert login(owner, name).status_code == 200
    attacker = Client()
    use_up_free_attempts(attacker, name)
    assert_too_many_attempts(login(attacker, name))
    assert login(owner, name).status_code == 200


def test_device_cookie_is_scoped_and_long_lived(anon, name):
    response = login(anon, name)
    cookie = next(
        value
        for key, value in response.raw.headers.items()
        if key.lower() == "set-cookie" and value.startswith("chat_device=")
    )
    attributes = [part.strip().lower() for part in cookie.split(";")]
    assert "httponly" in attributes
    assert "path=/api/auth" in attributes
    assert f"max-age={180 * 86400}" in attributes
    # The device cookie is not sent anywhere else.
    assert anon.session.cookies.get("chat_device", path="/api/auth")


def test_device_of_another_user_counts_as_unknown(anon, name):
    device = Client()
    other = unique_name()
    register(device, other)
    assert login(device, other).status_code == 200
    use_up_free_attempts(anon, name)
    assert_too_many_attempts(login(device, name))


def test_failures_on_a_device_do_not_touch_the_name(name):
    device = Client()
    assert login(device, name).status_code == 200
    use_up_free_attempts(device, name)
    assert_too_many_attempts(login(device, name))
    assert login(Client(), name).status_code == 200


def test_invalid_device_token_counts_as_unknown(anon, name):
    use_up_free_attempts(anon, name)
    forged = Client()
    forged.session.cookies.set("chat_device", "not-a-real-token", path="/api/auth")
    assert_too_many_attempts(login(forged, name))


@pytest.mark.skipif(
    not os.environ.get("SHORT_LOGIN_TTL"),
    reason="needs LOGIN_FAIL_TTL_SECONDS set to a few seconds on the app",
)
def test_count_expires_instead_of_locking(anon, name):
    use_up_free_attempts(anon, name)
    assert_too_many_attempts(login(anon, name, "wrong-password"))
    time.sleep(int(os.environ["SHORT_LOGIN_TTL"]) + 1)
    # A fresh count: the free attempts are back.
    use_up_free_attempts(anon, name)
