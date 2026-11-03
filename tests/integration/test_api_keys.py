import pytest

from ai_helpers import MARKED_KEY, add_ai, store_key
from conftest import error_code


def test_store_and_list_shows_only_last4(make_user):
    user = make_user()
    response = store_key(user, "anthropic", MARKED_KEY)
    assert response.status_code == 200
    assert response.json() == {"provider": "anthropic", "last4": "7f3a"}
    keys = user.get("/api/keys").json()["keys"]
    assert [(k["provider"], k["last4"]) for k in keys] == [("anthropic", "7f3a")]
    assert MARKED_KEY not in user.get("/api/keys").text


def test_rejected_key_is_not_stored(make_user):
    user = make_user()
    response = store_key(user, "openai", "fake-reject")
    assert response.status_code == 400
    assert error_code(response) == "api_key_invalid"
    assert user.get("/api/keys").json()["keys"] == []


def test_replace_key(make_user):
    user = make_user()
    store_key(user, "openai", "fake-old-1111")
    store_key(user, "openai", "fake-new-2222")
    assert [k["last4"] for k in user.get("/api/keys").json()["keys"]] == ["2222"]


@pytest.mark.parametrize("provider", ["mistral", "OPENAI"])
def test_unknown_provider(make_user, provider):
    response = store_key(make_user(), provider)
    assert response.status_code == 400
    assert error_code(response) == "invalid_argument"


def test_database_holds_no_plaintext(make_user, db):
    user = make_user()
    store_key(user, "gemini", MARKED_KEY)
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT ciphertext, nonce, last4 FROM user_api_keys k "
            "JOIN users u ON u.id = k.user_id WHERE u.username = %s",
            (user.username,),
        )
        ciphertext, nonce, last4 = cursor.fetchone()
    assert MARKED_KEY.encode() not in bytes(ciphertext)
    assert last4 == "7f3a" and len(nonce) == 12


def test_keys_are_private(make_user):
    alice, bob = make_user(), make_user()
    store_key(alice, "anthropic")
    assert bob.get("/api/keys").json()["keys"] == []


def test_delete_key_keeps_ai_in_room(make_user, make_room):
    user = make_user()
    room_id = make_room(user)
    store_key(user, "anthropic")
    assert add_ai(user, room_id, "claude").json()["status"] == "joined"
    assert user.request("DELETE", "/api/keys/anthropic").status_code == 200
    assert user.get("/api/keys").json()["keys"] == []
    names = [m["name"] for m in user.get(f"/api/rooms/{room_id}/members").json()["members"]]
    assert "Claude" in names


def test_keys_need_login(anon):
    assert anon.get("/api/keys").status_code == 401
