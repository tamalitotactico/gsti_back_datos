"""Ingesta de lotes de bioseñales e indicadores del smartphone.

Compartido por el endpoint REST y el WebSocket del dispositivo. Garantías:
- Idempotencia por lote (batch_id) y por bloque (participante, métrica, fuente, inicio).
- Validación contra el catálogo de métricas: rango, tipo y duración de la ventana.
- Rechazo parcial: un dato inválido no tumba el lote; se informa su índice y motivo.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Device, IngestBatch, MetricDefinition, Participant, SensorObservation
from app.schemas import IngestResultOut, ObservationIn, RejectedObservation, SensorBatchIn
from app.services.realtime import bus

MAX_BACKFILL = timedelta(days=60)  # el teléfono puede acumular días sin red (Doze, sin datos)
MAX_REPORTED_REJECTIONS = 50

# Duración permitida de la ventana según granularidad
_WINDOW_RULES: dict[str, tuple[timedelta, timedelta]] = {
    "30min": (timedelta(minutes=30), timedelta(minutes=30)),
    "night": (timedelta(hours=1), timedelta(hours=18)),
    "day": (timedelta(hours=23), timedelta(hours=25)),
    "sample": (timedelta(seconds=1), timedelta(minutes=30)),
}


class BatchConflict(Exception):
    """El batch_id ya existe pero pertenece a otro dispositivo."""


async def _metric_catalog(session: AsyncSession) -> dict[str, MetricDefinition]:
    rows = (await session.scalars(select(MetricDefinition))).all()
    return {m.code: m for m in rows}


def _validate(obs: ObservationIn, m: MetricDefinition | None, now: datetime, skew: timedelta) -> str | None:
    if m is None:
        return f"métrica desconocida: {obs.metric}"
    if obs.window_end <= obs.window_start:
        return "window_end debe ser posterior a window_start"
    lo, hi = _WINDOW_RULES[m.granularity]
    dur = obs.window_end - obs.window_start
    if not lo <= dur <= hi:
        return f"duración {dur} inválida para granularidad {m.granularity}"
    if m.granularity == "30min" and (obs.window_start.minute % 30 or obs.window_start.second):
        return "los bloques de 30 min deben empezar en :00 o :30"
    if obs.window_end > now + skew:
        return "ventana en el futuro (revisar reloj del teléfono)"
    if obs.window_start < now - MAX_BACKFILL:
        return "ventana más antigua que el máximo permitido (60 días)"
    if not m.min_value <= obs.value <= m.max_value:
        return f"valor {obs.value} fuera de rango [{m.min_value}, {m.max_value}]"
    if m.value_type == "binary" and obs.value not in (0, 1):
        return "valor binario debe ser 0 o 1"
    if m.value_type == "count" and not float(obs.value).is_integer():
        return "un conteo debe ser entero"
    return None


async def ingest_batch(
    session: AsyncSession,
    device: Device,
    participant: Participant,
    batch: SensorBatchIn,
    channel: str,
    received_at: datetime | None = None,
) -> IngestResultOut:
    s = get_settings()
    received_at = received_at or datetime.now(UTC)

    existing = await session.get(IngestBatch, batch.batch_id)
    if existing is not None:
        if existing.device_id != device.id:
            raise BatchConflict(str(batch.batch_id))
        return IngestResultOut(
            batch_id=existing.id, received=existing.n_received, accepted=existing.n_accepted,
            duplicate=existing.n_duplicate, rejected=existing.n_rejected, replayed=True,
        )

    if len(batch.observations) > s.max_observations_per_batch:
        raise ValueError(f"Máximo {s.max_observations_per_batch} observaciones por lote")

    catalog = await _metric_catalog(session)
    skew = timedelta(minutes=s.max_clock_skew_minutes)
    rows: list[dict] = []
    rejections: list[RejectedObservation] = []
    n_rejected = 0
    seen: set[tuple] = set()
    n_in_batch_dupes = 0

    for i, obs in enumerate(batch.observations):
        reason = _validate(obs, catalog.get(obs.metric), received_at, skew)
        if reason:
            n_rejected += 1
            if len(rejections) < MAX_REPORTED_REJECTIONS:
                rejections.append(RejectedObservation(index=i, reason=reason))
            continue
        key = (obs.metric, obs.source, obs.window_start)
        if key in seen:
            n_in_batch_dupes += 1
            continue
        seen.add(key)
        rows.append({
            "participant_id": participant.id, "device_id": device.id, "batch_id": batch.batch_id,
            "metric": obs.metric, "source": obs.source, "window_start": obs.window_start,
            "window_end": obs.window_end, "value": obs.value, "quality": obs.quality,
            "received_at": received_at,
        })

    record = IngestBatch(
        id=batch.batch_id, device_id=device.id, participant_id=participant.id, channel=channel,
        client_sent_at=batch.sent_at, received_at=received_at, n_received=len(batch.observations),
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError:
        # Reintento concurrente del mismo lote: el otro request ya lo registró
        await session.rollback()
        existing = await session.get(IngestBatch, batch.batch_id)
        if existing is None or existing.device_id != device.id:
            raise BatchConflict(str(batch.batch_id)) from None
        return IngestResultOut(
            batch_id=existing.id, received=existing.n_received, accepted=existing.n_accepted,
            duplicate=existing.n_duplicate, rejected=existing.n_rejected, replayed=True,
        )

    n_accepted = 0
    if rows:
        stmt = (
            insert(SensorObservation)
            .values(rows)
            .on_conflict_do_nothing(constraint="sensor_obs_unique_window")
            .returning(SensorObservation.id)
        )
        n_accepted = len((await session.execute(stmt)).all())

    record.n_accepted = n_accepted
    record.n_duplicate = len(rows) - n_accepted + n_in_batch_dupes
    record.n_rejected = n_rejected
    record.processed_at = datetime.now(UTC)

    device.last_seen_at = received_at
    if participant.status == "enrolled" and n_accepted:
        await session.execute(
            update(Participant).where(Participant.id == participant.id, Participant.status == "enrolled")
            .values(status="active")
        )
    await session.commit()

    bus.publish("ingest.batch", {
        "participant_code": participant.code, "channel": channel, "accepted": n_accepted,
        "rejected": n_rejected, "latency_ms": round((record.processed_at - received_at).total_seconds() * 1000, 1),
    })
    return IngestResultOut(
        batch_id=batch.batch_id, received=len(batch.observations), accepted=n_accepted,
        duplicate=record.n_duplicate, rejected=n_rejected, rejections=rejections,
    )
