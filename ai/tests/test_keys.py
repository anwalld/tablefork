import os

import pytest
from cryptography.exceptions import InvalidTag

from chat_ai.keys import KeyCipher


def test_round_trip():
    cipher = KeyCipher(os.urandom(32))
    ciphertext, nonce = cipher.encrypt(7, "anthropic", "sk-ant-secret-1234")
    assert b"secret" not in ciphertext
    assert cipher.decrypt(7, "anthropic", ciphertext, nonce) == "sk-ant-secret-1234"


@pytest.mark.parametrize("user,provider", [(8, "anthropic"), (7, "openai")])
def test_ciphertext_is_bound_to_user_and_provider(user, provider):
    cipher = KeyCipher(os.urandom(32))
    ciphertext, nonce = cipher.encrypt(7, "anthropic", "sk-ant-secret-1234")
    with pytest.raises(InvalidTag):
        cipher.decrypt(user, provider, ciphertext, nonce)


def test_other_master_key_cannot_decrypt():
    ciphertext, nonce = KeyCipher(os.urandom(32)).encrypt(7, "openai", "sk-1")
    with pytest.raises(InvalidTag):
        KeyCipher(os.urandom(32)).decrypt(7, "openai", ciphertext, nonce)
