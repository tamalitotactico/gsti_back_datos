"""Cifrado de la bóveda de identidad.

- Nombre, correo y teléfono: Fernet (AES-128-CBC + HMAC-SHA256). La clave vive solo
  en la variable de entorno IDENTITY_ENCRYPTION_KEY, nunca en la base de datos.
- Código de estudiante: HMAC-SHA256 con IDENTITY_HMAC_KEY, para detectar inscripciones
  duplicadas sin poder revertir el valor.
"""
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class IdentityKeyMissing(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().identity_encryption_key
    if not key:
        raise IdentityKeyMissing("IDENTITY_ENCRYPTION_KEY no está configurada")
    return Fernet(key.encode())


def encrypt(value: str | None) -> bytes | None:
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt(token: bytes | None) -> str | None:
    if token is None:
        return None
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken as exc:  # clave rotada o dato corrupto
        raise ValueError("No se pudo descifrar el dato de identidad") from exc


def student_code_hmac(student_code: str) -> str:
    key = get_settings().identity_hmac_key.encode()
    normalized = student_code.strip().upper()
    return hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
