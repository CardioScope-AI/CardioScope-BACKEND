"""Password hashing shared by admin initialization and the future login service."""

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


hasher = PasswordHasher(type=Type.ID, memory_cost=65536, time_cost=3,
                        parallelism=4, salt_len=16, hash_len=32)


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False
