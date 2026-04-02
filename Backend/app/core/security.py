import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


@dataclass(slots=True)
class EncryptedPayload:
    nonce: str
    ciphertext: str
    key_version: int = 1


class SecurityManager:
    """
    AES-256-GCM helper.
    - encrypted payload is safe to persist in DB
    - decrypted API keys should only live in memory during invocation
    """

    def __init__(self, master_key_b64: str) -> None:
        if not master_key_b64:
            raise ValueError("ENCRYPTION_MASTER_KEY is required.")
        key = _urlsafe_b64decode(master_key_b64)
        if len(key) != 32:
            raise ValueError("ENCRYPTION_MASTER_KEY must decode to 32 bytes.")
        self._key = key

    def encrypt_text(self, plaintext: str, aad: str | None = None) -> EncryptedPayload:
        aesgcm = AESGCM(self._key)
        nonce = os.urandom(12)
        aad_bytes = aad.encode("utf-8") if aad else None
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad_bytes)
        return EncryptedPayload(
            nonce=base64.urlsafe_b64encode(nonce).decode("utf-8"),
            ciphertext=base64.urlsafe_b64encode(ciphertext).decode("utf-8"),
        )

    def decrypt_text(self, payload: EncryptedPayload, aad: str | None = None) -> str:
        aesgcm = AESGCM(self._key)
        aad_bytes = aad.encode("utf-8") if aad else None
        plaintext = aesgcm.decrypt(
            _urlsafe_b64decode(payload.nonce),
            _urlsafe_b64decode(payload.ciphertext),
            aad_bytes,
        )
        return plaintext.decode("utf-8")

