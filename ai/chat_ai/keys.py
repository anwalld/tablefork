"""Users' model API keys: AES-256-GCM at rest, plaintext only in memory.

The associated data binds a ciphertext to its user and provider, so a stored
key cannot be moved to another account or provider and still decrypt.
"""

import os

import aiomysql
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .agents import PROVIDERS


class KeyCipher:
    def __init__(self, master_key: bytes):
        self._aead = AESGCM(master_key)

    @staticmethod
    def _aad(user_id: int, provider: str) -> bytes:
        return f"{user_id}:{provider}".encode()

    def encrypt(self, user_id: int, provider: str, api_key: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return self._aead.encrypt(nonce, api_key.encode(), self._aad(user_id, provider)), nonce

    def decrypt(self, user_id: int, provider: str, ciphertext: bytes, nonce: bytes) -> str:
        return self._aead.decrypt(nonce, ciphertext, self._aad(user_id, provider)).decode()


class KeyStore:
    def __init__(self, pool: aiomysql.Pool, cipher: KeyCipher):
        self.pool = pool
        self.cipher = cipher

    async def providers(self, user_id: int) -> set[str]:
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute("SELECT provider FROM user_api_keys WHERE user_id = %s", (user_id,))
            return {row[0] for row in await cursor.fetchall()}

    async def get(self, user_id: int, provider: str) -> str | None:
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "SELECT ciphertext, nonce FROM user_api_keys WHERE user_id = %s AND provider = %s",
                (user_id, provider),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self.cipher.decrypt(user_id, provider, bytes(row[0]), bytes(row[1]))

    async def save(self, user_id: int, provider: str, api_key: str) -> str:
        if provider not in PROVIDERS:
            raise ValueError(provider)
        ciphertext, nonce = self.cipher.encrypt(user_id, provider, api_key)
        last4 = api_key[-4:]
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                # Binary values go as hex: aiomysql 0.3 cannot escape bytes.
                "INSERT INTO user_api_keys (user_id, provider, ciphertext, nonce, last4) "
                "VALUES (%s, %s, UNHEX(%s), UNHEX(%s), %s) "
                "ON DUPLICATE KEY UPDATE ciphertext = VALUES(ciphertext), "
                "nonce = VALUES(nonce), last4 = VALUES(last4)",
                (user_id, provider, ciphertext.hex(), nonce.hex(), last4),
            )
            await connection.commit()
        return last4
