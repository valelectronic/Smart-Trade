"""
Per-user envelope encryption for Cantex credentials.

Master key:
    CANTEX_MASTER_KEY=<URL-safe-base64-encoded 32-byte key>

Generate a key with:
    python -c "from crypto import generate_master_key; print(generate_master_key())"

Keep the master key outside PostgreSQL and never commit it to source control.
"""

import base64
import json
import os
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KEY_VERSION = "v1"
MASTER_KEY_ENV = "CANTEX_MASTER_KEY"
_AES_KEY_BYTES = 32
_NONCE_BYTES = 12


def generate_master_key() -> str:
    """Generate a URL-safe base64 master key suitable for the environment."""
    return base64.urlsafe_b64encode(
        secrets.token_bytes(_AES_KEY_BYTES)
    ).decode("ascii")


def _load_master_key() -> bytes:
    """Load and validate the 256-bit master key from the environment."""
    encoded = os.getenv(MASTER_KEY_ENV, "").strip()
    if not encoded:
        raise RuntimeError(
            f"{MASTER_KEY_ENV} is not configured in the backend environment."
        )

    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError(
            f"{MASTER_KEY_ENV} must be URL-safe base64."
        ) from exc

    if len(key) != _AES_KEY_BYTES:
        raise RuntimeError(
            f"{MASTER_KEY_ENV} must decode to exactly 32 bytes."
        )
    return key


def _aad(user_id: str, purpose: str, key_version: str) -> bytes:
    """Bind ciphertext to its user, purpose and key version."""
    return (
        f"cantex-credentials:{key_version}:{purpose}:{user_id}"
    ).encode("utf-8")


def encrypt_user_credentials(
    user_id: str,
    operator_key: str,
    trading_key: str,
) -> dict[str, Any]:
    """Encrypt one user's two Cantex keys using a per-user DEK.

    The returned mapping matches postgres_database.save_user_credentials().
    Plaintext credentials are never written to disk or returned.
    """
    user_id = str(user_id or "").strip()
    operator_key = str(operator_key or "").strip()
    trading_key = str(trading_key or "").strip()

    if not user_id:
        raise ValueError("user_id is required.")
    if not operator_key or not trading_key:
        raise ValueError("Both Cantex credentials are required.")

    key_version = KEY_VERSION
    master_key = _load_master_key()

    dek = AESGCM.generate_key(bit_length=256)
    credentials_nonce = secrets.token_bytes(_NONCE_BYTES)
    wrapped_dek_nonce = secrets.token_bytes(_NONCE_BYTES)

    plaintext = json.dumps(
        {
            "operator_key": operator_key,
            "trading_key": trading_key,
        },
        separators=(",", ":"),
    ).encode("utf-8")

    try:
        credentials_ciphertext = AESGCM(dek).encrypt(
            credentials_nonce,
            plaintext,
            _aad(user_id, "credentials", key_version),
        )
        wrapped_dek = AESGCM(master_key).encrypt(
            wrapped_dek_nonce,
            dek,
            _aad(user_id, "dek", key_version),
        )
    finally:
        # Best-effort reference release. Python immutable bytes cannot be
        # reliably zeroised in memory.
        del plaintext

    return {
        "credentials_ciphertext": credentials_ciphertext,
        "credentials_nonce": credentials_nonce,
        "wrapped_dek": wrapped_dek,
        "wrapped_dek_nonce": wrapped_dek_nonce,
        "key_version": key_version,
    }


def decrypt_user_credentials(
    user_id: str,
    encrypted_record: dict[str, Any],
) -> dict[str, str]:
    """Decrypt a credential record returned by get_user_credentials_ciphertext.

    Raises a generic RuntimeError for authentication/decryption failures;
    callers must not log encrypted records, keys or exception internals.
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required.")
    if not isinstance(encrypted_record, dict):
        raise TypeError("encrypted_record must be a dictionary.")

    required = (
        "credentials_ciphertext",
        "credentials_nonce",
        "wrapped_dek",
        "wrapped_dek_nonce",
        "key_version",
    )
    missing = [
        field for field in required
        if encrypted_record.get(field) is None
    ]
    if missing:
        raise ValueError(
            "Encrypted credential record is incomplete: "
            + ", ".join(missing)
        )

    key_version = str(encrypted_record["key_version"]).strip()
    if key_version != KEY_VERSION:
        raise RuntimeError(
            f"Unsupported credential key version: {key_version!r}."
        )

    try:
        master_key = _load_master_key()
        wrapped_dek = bytes(encrypted_record["wrapped_dek"])
        wrapped_dek_nonce = bytes(encrypted_record["wrapped_dek_nonce"])
        credentials_ciphertext = bytes(
            encrypted_record["credentials_ciphertext"]
        )
        credentials_nonce = bytes(encrypted_record["credentials_nonce"])

        dek = AESGCM(master_key).decrypt(
            wrapped_dek_nonce,
            wrapped_dek,
            _aad(user_id, "dek", key_version),
        )
        plaintext = AESGCM(dek).decrypt(
            credentials_nonce,
            credentials_ciphertext,
            _aad(user_id, "credentials", key_version),
        )
        credentials = json.loads(plaintext.decode("utf-8"))
    except (InvalidTag, ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "Could not decrypt this user's Cantex credentials. "
            "Check the master key and stored credential record."
        ) from None

    if not isinstance(credentials, dict):
        raise RuntimeError("Decrypted credential payload has an invalid format.")

    operator_key = credentials.get("operator_key")
    trading_key = credentials.get("trading_key")
    if not isinstance(operator_key, str) or not operator_key:
        raise RuntimeError("Decrypted operator credential is missing.")
    if not isinstance(trading_key, str) or not trading_key:
        raise RuntimeError("Decrypted trading credential is missing.")

    return {
        "operator_key": operator_key,
        "trading_key": trading_key,
    }



def decrypt_credentials(
    envelope: dict[str, Any],
    user_id: str,
) -> dict[str, str]:
    """Compatibility wrapper for the database credential-record shape.

    get_user_credentials_ciphertext() already returns a mapping containing
    the five binary database fields. There is no JSON/base64 envelope to
    deserialise; the existing decrypt_user_credentials() validates and
    decrypts those fields directly.
    """
    return decrypt_user_credentials(user_id, envelope)
