"""Contratos JSON de la API (validados con Pydantic v2)."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Role = Literal["admin", "clinician", "researcher", "service"]
AgeBand = Literal["16-17", "18-20", "21-23", "24-26", "27-29", "30-35"]
RiskLevel = Literal["bajo", "moderado", "alto"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    expires_in_minutes: int


class StaffCreateIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    role: Role
    password: str = Field(min_length=12)


class StaffOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool


# ------------------------------------------------------------------ participantes
class IdentityIn(BaseModel):
    """Datos de contacto para la derivación. Se cifran y van a la bóveda `identity`."""
    full_name: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    student_code: str | None = Field(default=None, max_length=30)


class ParticipantCreateIn(BaseModel):
    consent_version: str = Field(min_length=1, max_length=40)
    cohort: str | None = Field(default=None, max_length=60)
    age_band: AgeBand | None = None
    sex: Literal["F", "M", "X", "NR"] | None = None
    semester: int | None = Field(default=None, ge=1, le=14)
    assent_at: AwareDatetime | None = Field(default=None, description="Asentimiento del menor (16-17)")
    guardian_consent_at: AwareDatetime | None = Field(default=None, description="Autorización del representante legal")
    identity: IdentityIn | None = None

    @model_validator(mode="after")
    def _minor_consent(self):
        if self.age_band == "16-17" and (self.assent_at is None or self.guardian_consent_at is None):
            raise ValueError("Participantes de 16-17 años requieren asentimiento y autorización del representante legal")
        return self


class ParticipantOut(ORM):
    id: uuid.UUID
    code: str
    status: str
    cohort: str | None
    age_band: str | None
    sex: str | None
    semester: int | None
    consent_version: str
    enrolled_at: datetime
    withdrawn_at: datetime | None
    assent_at: datetime | None = None
    guardian_consent_at: datetime | None = None


class EnrollmentOut(BaseModel):
    participant: ParticipantOut
    enrollment_code: str = Field(description="Se muestra una sola vez. El estudiante lo ingresa en la app.")
    enrollment_code_expires_at: datetime


class ParticipantSummary(BaseModel):
    participant: ParticipantOut
    devices: int
    last_seen_at: datetime | None
    observations_last_7d: int
    responses_total: int
    open_alerts: int
    ema_adherence: float | None


class IdentityOut(BaseModel):
    participant_code: str
    full_name: str | None
    email: str | None
    phone: str | None


class WithdrawIn(BaseModel):
    purge_identity: bool = Field(default=True, description="Borra la bóveda de identidad (desvinculación irreversible)")
    purge_data: bool = Field(default=False, description="Borra además todos los datos de investigación")
    reason: str | None = Field(default=None, max_length=300)


# ------------------------------------------------------------------ dispositivos
class DeviceRegisterIn(BaseModel):
    enrollment_code: str
    kind: Literal["smartphone", "wearable", "gateway"] = "smartphone"
    platform: str | None = Field(default=None, max_length=40, examples=["android-14"])
    model: str | None = Field(default=None, max_length=80)
    app_version: str | None = Field(default=None, max_length=20)


class DeviceRegisterOut(BaseModel):
    device_id: uuid.UUID
    participant_code: str
    device_token: str = Field(description="Se muestra una sola vez. Guardar en almacenamiento seguro del teléfono.")


class DeviceOut(ORM):
    id: uuid.UUID
    kind: str
    platform: str | None
    model: str | None
    app_version: str | None
    created_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


# ------------------------------------------------------------------ ingesta de bioseñales
class ObservationIn(BaseModel):
    metric: str = Field(examples=["steps"])
    source: str = Field(pattern=r"^[a-z0-9_]{2,40}$", examples=["health_connect"])
    window_start: AwareDatetime
    window_end: AwareDatetime
    value: float
    quality: Literal["ok", "partial", "device_imputed"] = "ok"


class SensorBatchIn(BaseModel):
    batch_id: uuid.UUID = Field(description="UUID generado en el teléfono; reenviar el mismo si falla la red")
    sent_at: AwareDatetime | None = None
    observations: list[ObservationIn] = Field(min_length=1)


class RejectedObservation(BaseModel):
    index: int
    reason: str


class IngestResultOut(BaseModel):
    batch_id: uuid.UUID
    received: int
    accepted: int
    duplicate: int
    rejected: int
    rejections: list[RejectedObservation] = []
    replayed: bool = Field(default=False, description="True si el lote ya se había recibido (reintento)")


# ------------------------------------------------------------------ cuestionarios
class ItemAnswerIn(BaseModel):
    item: int = Field(ge=1)
    value: int


class QuestionnaireIn(BaseModel):
    client_response_id: uuid.UUID
    instrument: str = Field(examples=["PHQ9"])
    context: Literal["baseline", "weekly", "biweekly", "monthly", "ema", "followup", "final", "adhoc"]
    ema_prompt_id: uuid.UUID | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime
    answers: list[ItemAnswerIn] = Field(min_length=1)

    @field_validator("answers")
    @classmethod
    def _no_duplicates(cls, v: list[ItemAnswerIn]) -> list[ItemAnswerIn]:
        nums = [a.item for a in v]
        if len(nums) != len(set(nums)):
            raise ValueError("Ítems repetidos en la respuesta")
        return v


class QuestionnaireAckOut(BaseModel):
    """Lo que ve el teléfono: NO incluye puntajes ni alertas."""
    response_id: uuid.UUID
    replayed: bool = False
    support_resources: bool = Field(description="Mostrar recursos de ayuda al estudiante ahora")


class QuestionnaireOut(ORM):
    id: uuid.UUID
    instrument_code: str
    context: str
    completed_at: datetime
    total_score: float | None
    subscores: dict


# ------------------------------------------------------------------ EMA
class EmaScheduleIn(BaseModel):
    start_date: date
    days: int = Field(ge=1, le=120)
    prompts_per_day: int = Field(default=3, ge=1, le=10)
    window_start_hour: int = Field(default=9, ge=0, le=23)
    window_end_hour: int = Field(default=21, ge=1, le=24)
    expiry_minutes: int = Field(default=60, ge=10, le=240)
    instrument: str = "EMA_DAILY"
    seed: int | None = Field(default=None, description="Semilla para reproducir el calendario")


class EmaPromptOut(ORM):
    id: uuid.UUID
    instrument_code: str
    scheduled_at: datetime
    expires_at: datetime
    delivered_at: datetime | None
    status: str


# ------------------------------------------------------------------ alertas
class AlertOut(ORM):
    id: uuid.UUID
    participant_code: str
    source_type: str
    rule_code: str
    severity: str
    detail: dict
    status: str
    created_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    resolution_note: str | None


class AlertResolveIn(BaseModel):
    status: Literal["referred", "closed"]
    note: str = Field(min_length=3, max_length=2000)


# ------------------------------------------------------------------ predicciones
class PredictionIn(BaseModel):
    participant_code: str
    week_start: date
    model_name: str = Field(max_length=60, examples=["random_forest", "xgboost"])
    model_version: str = Field(max_length=40)
    score: float = Field(ge=0, le=1, description="Probabilidad de la clase Riesgo alto")
    threshold: float = Field(default=0.35, ge=0, le=1)
    risk_level: Literal["bajo", "moderado"] | None = Field(
        default=None, description="Nivel sugerido por el modelo cuando score < threshold")

    @field_validator("week_start")
    @classmethod
    def _monday(cls, v: date) -> date:
        if v.weekday() != 0:
            raise ValueError("week_start debe ser lunes (persona-semana)")
        return v


class PredictionBatchIn(BaseModel):
    predictions: list[PredictionIn] = Field(min_length=1, max_length=1000)


class PredictionBatchOut(BaseModel):
    stored: int
    updated: int
    alerts_created: int
    clinical_bypass_applied: int = 0


class PersonWeekFeatures(BaseModel):
    """Vector de 8 indicadores por persona-semana (artículo 3.10/3.11) y etiqueta de referencia."""
    participant_code: str
    week_start: date
    steps_daily_mean: float | None
    sleep_minutes_mean: float | None
    home_minutes_mean: float | None
    app_use_minutes_mean: float | None
    phq9_total: float | None
    phq9_total_excl_item9: float | None
    gad7_total: float | None
    uls6_total: float | None
    cssrs_level: float | None
    phq9_item9: int | None
    n_days_sensed: int
    label_high_risk: bool | None


# ------------------------------------------------------------------ métricas del sistema
class LatencyStats(BaseModel):
    kind: str
    n: int
    median_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class AdherenceRow(BaseModel):
    participant_code: str
    n_due: int
    n_answered_on_time: int
    n_answered_late: int
    adherence: float | None
