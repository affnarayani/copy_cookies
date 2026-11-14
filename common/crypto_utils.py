"""Shared cookie encryption helpers."""

import base64
from secrets import token_bytes

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def derive_key(password: bytes, salt: bytes, iterations: int, key_length: int) -> bytes:
    """Derive a symmetric key from a password using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=key_length,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password)


def encrypt_bytes(
    data: bytes,
    password: str,
    iterations: int,
    key_length: int,
    salt_bytes: int,
    nonce_bytes: int,
) -> dict[str, str | int]:
    """Encrypt bytes with AES-GCM and return a JSON-serializable payload."""
    salt = token_bytes(salt_bytes)
    nonce = token_bytes(nonce_bytes)
    key = derive_key(password.encode("utf-8"), salt, iterations, key_length)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)

    return {
        "v": 1,
        "s": base64.b64encode(salt).decode("ascii"),
        "n": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ciphertext).decode("ascii"),
    }