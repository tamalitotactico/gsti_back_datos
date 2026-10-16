"""Configuración central leída de variables de entorno (.env)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "GSTI Backend"
    environment: str = "development"

    # Conexión de la API (rol gsti_api). Formato SQLAlchemy async.
    database_url: str = "postgresql+asyncpg://gsti_api:gsti_api@localhost:5432/gsti"
    db_null_pool: bool = False  # True en pruebas: sin pool compartido entre event loops

    # Firma de JWT del personal (dashboard). Cambiar en producción.
    jwt_secret: str = Field(default="dev-only-change-me-dev-only-change-me", min_length=32)
    jwt_expire_minutes: int = 8 * 60

    # Clave Fernet (base64 urlsafe, 32 bytes) para cifrar datos de identidad.
    # NUNCA se guarda en la base de datos. Generar con:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    identity_encryption_key: str = ""
    # Secreto para el HMAC del código de estudiante (búsqueda sin descifrar).
    identity_hmac_key: str = "dev-only-hmac-key-change-me"

    # Zona horaria del estudio: define el inicio de la persona-semana.
    study_timezone: str = "America/Lima"

    # --- Reglas de seguridad (protocolo de derivación) ---
    # Los puntos de corte los define el equipo clínico; estos valores son conservadores.
    bss_alert_cutoff: int = 1  # BSS total (ítems 1-19) >= cutoff -> alerta
    phq9_item9_alert_min: int = 1  # PHQ-9 ítem 9 >= 1 -> alerta
    flag_adversity_disclosure: bool = True  # adversidades > 0 -> revisión profesional

    # Límites de ingesta
    max_observations_per_batch: int = 5000
    max_clock_skew_minutes: int = 10  # tolerancia para timestamps "en el futuro"

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
