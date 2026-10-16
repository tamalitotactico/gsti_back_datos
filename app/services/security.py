"""Credenciales: contraseñas del personal, JWT, tokens de dispositivo y códigos de enrolamiento."""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.config import get_settings

_ph = PasswordHasher()
JWT_ALG = "HS256"
STAFF_ROLES = {"admin", "clinician", "researcher"}

# Alfabeto sin caracteres confundibles (0/O, 1/I/L) para dictar el código en persona
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerificationError:
        return False


def create_staff_token(staff_id: str, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": staff_id, "role": role, "iat": now, "exp": now + timedelta(minutes=s.jwt_expire_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm=JWT_ALG)


def decode_staff_token(token: str) -> dict:
    """Lanza jwt.PyJWTError si el token es inválido o expiró."""
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG])


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_device_token() -> str:
    # 256 bits de entropía: un SHA-256 simple basta para guardarlo (no es una contraseña humana)
    return "dev_" + secrets.token_urlsafe(32)


def new_enrollment_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_enrollment_code(code: str) -> str:
    c = code.strip().upper().replace(" ", "").replace("-", "")
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c
