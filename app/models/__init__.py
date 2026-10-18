"""Modelos ORM. La fuente de verdad del esquema es migrations/sql/*.sql."""
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, FetchedValue, Boolean, Date, Float, ForeignKey, Integer, LargeBinary, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

TS = TIMESTAMP(timezone=True)


# ------------------------------------------------------------------ admin
class StaffUser(Base):
    __tablename__ = "staff_users"
    __table_args__ = {"schema": "admin"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default="gen_random_uuid()")
    email: Mapped[str] = mapped_column(Text, unique=True)
    full_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "admin"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    staff_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("admin.staff_users.id"))
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, server_default="{}")


# ------------------------------------------------------------------ core
class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default="gen_random_uuid()")
    code: Mapped[str] = mapped_column(Text, unique=True, server_default=FetchedValue())  # lo asigna la BD (P0001...)
    status: Mapped[str] = mapped_column(Text, server_default="enrolled")
    cohort: Mapped[str | None] = mapped_column(Text)
    age_band: Mapped[str | None] = mapped_column(Text)
    sex: Mapped[str | None] = mapped_column(Text)
    semester: Mapped[int | None] = mapped_column(SmallInteger)
    consent_version: Mapped[str] = mapped_column(Text)
    enrolled_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    withdrawn_at: Mapped[datetime | None] = mapped_column(TS)
    enrollment_code_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    enrollment_code_expires_at: Mapped[datetime | None] = mapped_column(TS)
    assent_at: Mapped[datetime | None] = mapped_column(TS)
    guardian_consent_at: Mapped[datetime | None] = mapped_column(TS)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default="gen_random_uuid()")
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.participants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    __table_args__ = {"schema": "core"}

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(Text)
    granularity: Mapped[str] = mapped_column(Text)
    min_value: Mapped[float] = mapped_column(Float)
    max_value: Mapped[float] = mapped_column(Float)
    daily_agg: Mapped[str] = mapped_column(Text)


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.devices.id", ondelete="CASCADE"))
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.participants.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(Text)
    client_sent_at: Mapped[datetime | None] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS)
    processed_at: Mapped[datetime | None] = mapped_column(TS)
    n_received: Mapped[int] = mapped_column(Integer, default=0)
    n_accepted: Mapped[int] = mapped_column(Integer, default=0)
    n_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    n_rejected: Mapped[int] = mapped_column(Integer, default=0)


class SensorObservation(Base):
    __tablename__ = "sensor_observations"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.participants.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.devices.id", ondelete="CASCADE"))
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.ingest_batches.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(Text, ForeignKey("core.metric_definitions.code"))
    source: Mapped[str] = mapped_column(Text)
    window_start: Mapped[datetime] = mapped_column(TS)
    window_end: Mapped[datetime] = mapped_column(TS)
    value: Mapped[float] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(Text, server_default="ok")
    received_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = {"schema": "core"}

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    n_items: Mapped[int] = mapped_column(SmallInteger)
    scoring: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    administration: Mapped[str] = mapped_column(Text)


class InstrumentItem(Base):
    __tablename__ = "instrument_items"
    __table_args__ = {"schema": "core"}

    instrument_code: Mapped[str] = mapped_column(Text, ForeignKey("core.instruments.code"), primary_key=True)
    item_no: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text)
    min_value: Mapped[int] = mapped_column(SmallInteger)
    max_value: Mapped[int] = mapped_column(SmallInteger)
    scored: Mapped[bool] = mapped_column(Boolean)
    is_safety_item: Mapped[bool] = mapped_column(Boolean)


class EmaPrompt(Base):
    __tablename__ = "ema_prompts"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default="gen_random_uuid()")
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.participants.id", ondelete="CASCADE"))
    instrument_code: Mapped[str] = mapped_column(Text, ForeignKey("core.instruments.code"))
    scheduled_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    delivered_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(Text, server_default="scheduled")


class QuestionnaireResponse(Base):
    __tablename__ = "questionnaire_responses"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default="gen_random_uuid()")
    client_response_id: Mapped[uuid.UUID] = mapped_column(UUID, unique=True)
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.participants.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("core.devices.id", ondelete="SET NULL"))
    instrument_code: Mapped[str] = mapped_column(Text, ForeignKey("core.instruments.code"))
    context: Mapped[str] = mapped_column(Text)
    ema_prompt_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("core.ema_prompts.id"), unique=True)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS)
    processed_at: Mapped[datetime | None] = mapped_column(TS)
    total_score: Mapped[float | None] = mapped_column(Float)
    subscores: Mapped[dict] = mapped_column(JSONB, server_default="{}")


class ItemResponse(Base):
    __tablename__ = "item_responses"
    __table_args__ = {"schema": "core"}

    response_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("core.questionnaire_responses.id", ondelete="CASCADE"), primary_key=True
    )
    item_no: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    value: Mapped[int] = mapped_column(SmallInteger)


class ModelPrediction(Base):
    __tablename__ = "model_predictions"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default="gen_random_uuid()")
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.participants.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date)
    model_name: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    predicted_positive: Mapped[bool] = mapped_column(Boolean)
    risk_level: Mapped[str] = mapped_column(Text)
    clinical_bypass: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default="gen_random_uuid()")
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("core.participants.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(Text)
    response_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("core.questionnaire_responses.id"))
    prediction_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("core.model_predictions.id"))
    rule_code: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="open")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    acknowledged_at: Mapped[datetime | None] = mapped_column(TS)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("admin.staff_users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("admin.staff_users.id"))
    resolution_note: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ identity
class ParticipantIdentity(Base):
    __tablename__ = "participant_identity"
    __table_args__ = {"schema": "identity"}

    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("core.participants.id", ondelete="CASCADE"), primary_key=True
    )
    student_code_hmac: Mapped[str | None] = mapped_column(Text, unique=True)
    full_name_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    email_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
