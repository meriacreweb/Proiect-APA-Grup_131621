"""Cryptographic correctness and independent interoperability checks."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.app.constants import PBKDF2_ITERATIONS
from backend.app.crypto_engine import (
    EncryptedPayload,
    decrypt_message,
    derive_key,
    encrypt_message,
)
from backend.app.errors import AppError, AuthenticationError


def test_pbkdf2_matches_independent_standard_library_vector():
    password = "Parolă cu diacritice 🔑"
    salt = bytes(range(16))
    expected = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 600_000, dklen=32
    )
    assert PBKDF2_ITERATIONS == 600_000
    assert derive_key(password, salt) == expected


def test_independent_aes_gcm_fixture_is_accepted():
    """The fixture does not call the implementation's key/AAD/packet helpers."""
    password = "Parolă de interoperabilitate 🔐"
    plaintext = "UTM • SI-252\nCriptarea mesajelor în imagini 🌍"
    salt = bytes(range(16))
    iv = bytes(range(32, 44))
    clear = plaintext.encode("utf-8")
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000, 32)
    aad = b"STEG" + salt + iv + struct.pack(">I", len(clear))
    ciphertext_and_tag = AESGCM(key).encrypt(iv, clear, aad)
    payload = EncryptedPayload(
        salt=salt,
        iv=iv,
        tag=ciphertext_and_tag[-16:],
        ciphertext=ciphertext_and_tag[:-16],
    )
    assert decrypt_message(payload, password) == plaintext


@pytest.mark.parametrize("message", ["", "hello", "Română: șțâîă; 日本語; 🔐\n<svg onload=alert(1)>"])
def test_unicode_and_empty_plaintext_round_trip(message, password):
    payload = encrypt_message(message, password)
    assert len(payload.salt) == 16
    assert len(payload.iv) == 12
    assert len(payload.tag) == 16
    assert len(payload.ciphertext) == len(message.encode("utf-8"))
    assert decrypt_message(payload, password) == message


def test_each_encryption_uses_new_salt_and_nonce(password):
    first = encrypt_message("same plaintext", password)
    second = encrypt_message("same plaintext", password)
    assert first.salt != second.salt
    assert first.iv != second.iv
    assert first.ciphertext != second.ciphertext


@pytest.mark.parametrize("field", ["salt", "iv", "tag", "ciphertext"])
def test_authenticated_fields_detect_single_bit_modification(field, password):
    payload = encrypt_message("Mesaj autenticat", password)
    value = getattr(payload, field)
    modified = bytes([value[0] ^ 1]) + value[1:]
    tampered = replace(payload, **{field: modified})
    with pytest.raises(AuthenticationError):
        decrypt_message(tampered, password)


def test_ciphertext_length_is_authenticated(password):
    payload = encrypt_message("Mesaj cu lungime autentificată", password)
    with pytest.raises(AuthenticationError):
        decrypt_message(replace(payload, ciphertext=payload.ciphertext[:-1]), password)


def test_wrong_password_has_explicit_authentication_error(password):
    payload = encrypt_message("secret", password)
    with pytest.raises(AuthenticationError) as captured:
        decrypt_message(payload, "O parolă diferită, greșită")
    assert captured.value.status_code == 400
    assert "secret" not in captured.value.message
    assert password not in captured.value.message


@pytest.mark.parametrize("password", ["", "🔐" * 257])
def test_password_policy_is_bounded_by_utf8_bytes(password):
    with pytest.raises(AppError):
        derive_key(password, bytes(range(16)))


@pytest.mark.parametrize("salt", [b"", b"x" * 15, b"x" * 17])
def test_pbkdf2_rejects_invalid_salt_size(salt, password):
    with pytest.raises(AppError):
        derive_key(password, salt)
