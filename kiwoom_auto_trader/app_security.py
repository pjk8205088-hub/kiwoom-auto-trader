from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


HASH_SCHEME = "pbkdf2_sha256"
HASH_ITERATIONS = 260_000


def hash_secret(secret: str, *, salt: bytes | None = None) -> str:
    value = str(secret or "")
    if not value:
        raise ValueError("비밀번호는 비워 둘 수 없습니다.")
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        salt_bytes,
        HASH_ITERATIONS,
    )
    encoded_salt = base64.urlsafe_b64encode(salt_bytes).decode("ascii")
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"{HASH_SCHEME}${HASH_ITERATIONS}${encoded_salt}${encoded_digest}"


def verify_secret(secret: str, encoded: str) -> bool:
    try:
        scheme, iteration_text, salt_text, digest_text = str(encoded or "").split("$", 3)
        if scheme != HASH_SCHEME:
            return False
        iterations = int(iteration_text)
        if iterations <= 0:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (TypeError, ValueError, UnicodeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        str(secret or "").encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def is_valid_pin(value: str) -> bool:
    text = str(value or "")
    return len(text) == 6 and text.isdigit()


def is_valid_recovery_password(value: str) -> bool:
    text = str(value or "")
    return bool(
        len(text) >= 8
        and any(character.isalpha() for character in text)
        and any(character.isdigit() for character in text)
        and any(not character.isalnum() for character in text)
    )


def mask_account_except_last_two(account: str) -> str:
    raw = str(account or "")
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        return ""
    visible = digits[-2:] if len(digits) > 2 else digits
    masked_digits = "*" * max(0, len(digits) - len(visible)) + visible
    if len(masked_digits) == 8:
        return f"{masked_digits[:4]}-{masked_digits[4:]}"
    return masked_digits


def personalized_message(nickname: str, message: str) -> str:
    name = str(nickname or "").strip()
    body = str(message or "").strip()
    return f"{name}님, {body}" if name else body
