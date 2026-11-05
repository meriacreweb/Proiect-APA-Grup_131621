"""PBKDF2-HMAC-SHA256 and AES-256-GCM, with no handwritten cipher primitives.

Wire profile STEG/1 uses 600000 iterations, UTF-8 without normalization,
128-bit random salt, 96-bit random IV and a full 128-bit tag. See docs/PROTOCOL.md.
"""

import secrets
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .constants import (
    IV_SIZE,
    KEY_SIZE,
    MAGIC,
    MAX_MESSAGE_BYTES,
    MAX_PASSWORD_BYTES,
    PBKDF2_ITERATIONS,
    SALT_SIZE,
    TAG_SIZE,
)
from .errors import AppError, AuthenticationError


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedPayload:
    salt: bytes
    iv: bytes
    tag: bytes
    ciphertext: bytes

    def __repr__(self) -> str:
        return f"EncryptedPayload(ciphertext_length={len(self.ciphertext)})"


def password_bytes(password: str) -> bytes:
    if not isinstance(password, str):
        raise AppError("INVALID_PASSWORD", "Parola trebuie să fie text.", 422)
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AppError("INVALID_PASSWORD", "Parola nu este UTF-8 valid.", 422) from exc
    if not encoded or len(encoded) > MAX_PASSWORD_BYTES:
        raise AppError("INVALID_PASSWORD", "Parola trebuie să aibă între 1 și 1024 de octeți UTF-8.", 422)
    return encoded


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive exactly 256 bits. Never trim or normalize a password."""
    if not isinstance(salt, bytes) or len(salt) != SALT_SIZE:
        raise AppError("INVALID_SALT", "Saltul trebuie să conțină exact 16 octeți.")
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=KEY_SIZE,
        salt=salt, iterations=PBKDF2_ITERATIONS,
    ).derive(password_bytes(password))


def validate_payload(payload: EncryptedPayload) -> None:
    if not isinstance(payload, EncryptedPayload):
        raise AppError("INVALID_PAYLOAD", "Structura criptografică este invalidă.")
    for name, size in (("salt", SALT_SIZE), ("iv", IV_SIZE), ("tag", TAG_SIZE)):
        value = getattr(payload, name)
        if not isinstance(value, bytes) or len(value) != size:
            raise AppError("INVALID_PAYLOAD", "Lungimile câmpurilor criptografice sunt invalide.")
    if not isinstance(payload.ciphertext, bytes) or len(payload.ciphertext) > MAX_MESSAGE_BYTES:
        raise AppError("INVALID_PAYLOAD", "Lungimea mesajului criptat este invalidă.")


def associated_data(salt: bytes, iv: bytes, length: int) -> bytes:
    """Authenticate all non-tag header fields; the tag cannot authenticate itself."""
    return struct.pack(">4s16s12sI", MAGIC, salt, iv, length)


def encrypt_message(message: str, password: str) -> EncryptedPayload:
    if not isinstance(message, str):
        raise AppError("INVALID_MESSAGE", "Mesajul trebuie să fie text.", 422)
    try:
        plaintext = message.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AppError("INVALID_MESSAGE", "Mesajul nu este UTF-8 valid.", 422) from exc
    if len(plaintext) > MAX_MESSAGE_BYTES:
        raise AppError("MESSAGE_TOO_LARGE", "Mesajul depășește limita aplicației.", 422)
    salt, iv = secrets.token_bytes(SALT_SIZE), secrets.token_bytes(IV_SIZE)
    key = derive_key(password, salt)
    sealed = AESGCM(key).encrypt(iv, plaintext, associated_data(salt, iv, len(plaintext)))
    return EncryptedPayload(salt, iv, sealed[-TAG_SIZE:], sealed[:-TAG_SIZE])


def decrypt_message(payload: EncryptedPayload, password: str) -> str:
    validate_payload(payload)
    key = derive_key(password, payload.salt)
    try:
        plaintext = AESGCM(key).decrypt(
            payload.iv, payload.ciphertext + payload.tag,
            associated_data(payload.salt, payload.iv, len(payload.ciphertext)),
        )
    except InvalidTag as exc:
        # A wrong key and corrupted authenticated data intentionally share one error.
        raise AuthenticationError() from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError("INVALID_TEXT", "Mesajul autentificat nu este text UTF-8 valid.") from exc

