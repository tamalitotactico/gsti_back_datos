"""Tablero: alertas, predicciones del modelo y métricas del sistema (H4, H5)."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.db import get_session
from app.models import Alert, ModelPrediction, Participant, StaffUser
from app.schemas import AdherenceRow, AlertOut, AlertResolveIn, LatencyStats, PredictionBatchIn, PredictionBatchOut
from app.services.audit import audit
from app.services.questionnaires import alert_event, create_alerts
from app.services.realtime import bus
from app.services.safety import evaluate_prediction

router = APIRouter(tags=["dashboard"])
clinical = require_roles("admin", "clinician")
any_staff = require_roles("admin", "clinician", "researcher")


def _alert_out(a: Alert, code: str) -> AlertOut:
    return AlertOut(participant_code=code, **{k: getattr(a, k) for k in AlertOut.model_fields if k != "participant_code"})


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    status_filter: str | None = Query(default="open", alias="status"),
    severity: str | None = None,
    limit: int = Query(default=100, le=500),
    _: StaffUser = Depends(clinical), session: AsyncSession = Depends(get_session),
) -> list[AlertOut]:
    q = (select(Alert, Participant.code).join(Participant, Participant.id == Alert.participant_id)
         .order_by(Alert.created_at.desc()).limit(limit))
    if status_filter and status_filter != "all":
        q = q.where(Alert.status == status_filter)
    if severity:
        q = q.where(Alert.severity == severity)
    return [_alert_out(a, code) for a, code in (await session.execute(q)).all()]


async def _load_alert(session: AsyncSession, alert_id: str) -> tuple[Alert, str]:
    row = (await session.execute(
        select(Alert, Participant.code).join(Participant, Participant.id == Alert.participant_id)
        .where(Alert.id == alert_id).with_for_update(of=Alert)
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alerta no encontrada")
    return row[0], row[1]


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
async def acknowledge(
    alert_id: str, staff: StaffUser = Depends(clinical), session: AsyncSession = Depends(get_session),
) -> AlertOut:
    a, code = await _load_alert(session, alert_id)
    if a.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"La alerta ya está en estado {a.status}")
    a.status, a.acknowledged_at, a.acknowledged_by = "acknowledged", datetime.now(UTC), staff.id
    audit(session, staff.id, "alert.acknowledge", "alert", str(a.id))
    await session.commit()
    bus.publish("alert.updated", alert_event(a, code))
    return _alert_out(a, code)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
async def resolve(
    alert_id: str, body: AlertResolveIn,
    staff: StaffUser = Depends(clinical), session: AsyncSession = Depends(get_session),
) -> AlertOut:
    a, code = await _load_alert(session, alert_id)
    if a.status in ("referred", "closed"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"La alerta ya está en estado {a.status}")
    now = datetime.now(UTC)
    if a.acknowledged_at is None:
        a.acknowledged_at, a.acknowledged_by = now, staff.id
    a.status, a.resolved_at, a.resolved_by, a.resolution_note = body.status, now, staff.id, body.note
    audit(session, staff.id, f"alert.{body.status}", "alert", str(a.id))
    await session.commit()
    bus.publish("alert.updated", alert_event(a, code))
    return _alert_out(a, code)


@router.post("/predictions", response_model=PredictionBatchOut)
async def store_predictions(
    body: PredictionBatchIn,
    staff: StaffUser = Depends(require_roles("admin", "researcher")),
    session: AsyncSession = Depends(get_session),
) -> PredictionBatchOut:
    """Carga las predicciones persona-semana del modelo. Las positivas generan alerta para revisión."""
    codes = {p.participant_code.upper() for p in body.predictions}
    found = {p.code: p for p in (await session.scalars(select(Participant).where(Participant.code.in_(codes)))).all()}
    missing = sorted(codes - set(found))
    if missing:
        raise HTTPException(422, f"Participantes inexistentes: {missing}")

    stored = updated = 0
    new_alerts: list[tuple[Alert, str]] = []
    for pr in body.predictions:
        part = found[pr.participant_code.upper()]
        positive = pr.score >= pr.threshold
        key = dict(participant_id=part.id, week_start=pr.week_start, model_name=pr.model_name,
                   model_version=pr.model_version)
        prev = await session.scalar(select(ModelPrediction).filter_by(**key).with_for_update())
        if prev is None:
            pred = ModelPrediction(**key, score=pr.score, threshold=pr.threshold, predicted_positive=positive)
            session.add(pred)
            await session.flush()
            stored += 1
            becomes_positive = positive
        else:
            pred = prev
            becomes_positive = positive and not prev.predicted_positive  # negativo -> positivo también alerta
            prev.score, prev.threshold, prev.predicted_positive = pr.score, pr.threshold, positive
            updated += 1
        if becomes_positive and part.status != "withdrawn":
            for a in await create_alerts(session, part, evaluate_prediction(pr.score, pr.threshold, positive),
                                         "model", prediction_id=pred.id):
                new_alerts.append((a, part.code))
    audit(session, staff.id, "predictions.store", "model", body.predictions[0].model_name,
          stored=stored, updated=updated)
    await session.commit()
    for a, code in new_alerts:
        bus.publish("alert.created", alert_event(a, code))
    return PredictionBatchOut(stored=stored, updated=updated, alerts_created=len(new_alerts))


@router.get("/metrics/latency", response_model=list[LatencyStats])
async def latency(
    days: int = Query(default=7, ge=1, le=365),
    _: StaffUser = Depends(any_staff), session: AsyncSession = Depends(get_session),
) -> list[LatencyStats]:
    """H4: mediana y p95 del tiempo entre recepción y resultado."""
    rows = (await session.execute(text(
        "SELECT kind, count(*) AS n, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS median_ms, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms, max(latency_ms) AS max_ms "
        "FROM analytics.v_processing_latency WHERE received_at >= :since GROUP BY kind ORDER BY kind"),
        {"since": datetime.now(UTC) - timedelta(days=days)})).mappings().all()
    return [LatencyStats(**{k: (round(float(v), 2) if isinstance(v, float) else v) for k, v in r.items()})
            for r in rows]


@router.get("/metrics/adherence", response_model=list[AdherenceRow])
async def adherence(_: StaffUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """H5: adherencia a EMA por participante."""
    rows = (await session.execute(text("SELECT * FROM analytics.v_ema_adherence ORDER BY participant_code"))).mappings()
    return [AdherenceRow(**r) for r in rows]
