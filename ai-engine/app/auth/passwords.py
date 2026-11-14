from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.auth.common_passwords import is_common_password

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

_HASHER = PasswordHasher(
    type=Type.ID,
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError("Password length is not valid.")
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_format_errors(password: str) -> list[str]:
    errors: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(f"Use no more than {PASSWORD_MAX_LENGTH} characters.")
    if CONTROL_CHARACTERS.search(password):
        errors.append("Control characters are not allowed.")
    character_classes = sum(
        (
            bool(re.search(r"[a-z]", password)),
            bool(re.search(r"[A-Z]", password)),
            bool(re.search(r"\d", password)),
            bool(re.search(r"[^A-Za-z0-9]", password)),
        )
    )
    if character_classes < 3:
        errors.append(
            "Use at least three of: lowercase letters, uppercase letters, numbers, "
            "and symbols."
        )
    return errors


def password_strength_errors(password: str) -> list[str]:
    errors = password_format_errors(password)
    if is_common_password(password):
        errors.append("Choose a less common password.")
    return errors


DUMMY_PASSWORD_HASH = _HASHER.hash("not-a-real-user-password-for-timing-only")
