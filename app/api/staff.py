"""Tablero: alertas, predicciones del modelo y métricas del sistema (H4, H5)."""
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.config import get_settings
from app.db import get_session
from app.models import Alert, ModelPrediction, Participant, StaffUser
from app.schemas import (
    AdherenceRow, AlertOut, AlertResolveIn, LatencyStats, PersonWeekFeatures, PredictionBatchIn, PredictionBatchOut,
)
from app.services.audit import audit
from app.services.questionnaires import alert_event, create_alerts
from app.services.realtime import bus
from app.services.safety import evaluate_prediction, is_bypass, risk_from_prediction

router = APIRouter(tags=["dashboard"])
clinical = require_roles("admin", "clinician")
any_staff = require_roles("admin", "clinician", "researcher")
model_writers = require_roles("admin", "researcher", "service")  # incluye el microservicio de inferencia


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
    staff: StaffUser = Depends(model_writers),
    session: AsyncSession = Depends(get_session),
) -> PredictionBatchOut:
    """Recibe las predicciones persona-semana del microservicio de inferencia (RF / XGBoost).

    El servidor vuelve a aplicar el gatillo clínico como cláusula de guarda: si en esa semana
    el ítem 9 del PHQ-9 > 0 o la C-SSRS >= 3, el nivel es "alto" sin importar la probabilidad.
    Una semana que pasa a "alto" por el modelo genera una alerta para revisión profesional.
    """
    s = get_settings()
    codes = {p.participant_code.upper() for p in body.predictions}
    found = {p.code: p for p in (await session.scalars(select(Participant).where(Participant.code.in_(codes)))).all()}
    missing = sorted(codes - set(found))
    if missing:
        raise HTTPException(422, f"Participantes inexistentes: {missing}")

    stored = updated = bypassed = 0
    new_alerts: list[tuple[Alert, str]] = []
    for pr in body.predictions:
        part = found[pr.participant_code.upper()]
        week = (await session.execute(text(
            "SELECT phq9_item9, cssrs_level FROM analytics.v_person_week_features "
            "WHERE participant_code = :c AND week_start = :w"), {"c": part.code, "w": pr.week_start})).first()
        bypass = bool(week) and is_bypass(week[0], week[1], s)
        risk = "alto" if bypass else risk_from_prediction(pr.score, pr.threshold, pr.risk_level)
        bypassed += bypass
        values = dict(score=pr.score, threshold=pr.threshold, predicted_positive=pr.score >= pr.threshold,
                      risk_level=risk, clinical_bypass=bypass)
        key = dict(participant_id=part.id, week_start=pr.week_start, model_name=pr.model_name,
                   model_version=pr.model_version)
        prev = await session.scalar(select(ModelPrediction).filter_by(**key).with_for_update())
        if prev is None:
            pred = ModelPrediction(**key, **values)
            session.add(pred)
            await session.flush()
            stored += 1
            becomes_high = risk == "alto"
        else:
            pred = prev
            becomes_high = risk == "alto" and prev.risk_level != "alto"
            for k, v in values.items():
                setattr(prev, k, v)
            updated += 1
        # Con gatillo clínico la alerta ya se emitió al recibir el cuestionario: no se duplica
        if becomes_high and not bypass and part.status != "withdrawn":
            for a in await create_alerts(session, part,
                                         evaluate_prediction(pr.score, pr.threshold, risk, pr.model_name),
                                         "model", prediction_id=pred.id):
                new_alerts.append((a, part.code))
    audit(session, staff.id, "predictions.store", "model", body.predictions[0].model_name,
          stored=stored, updated=updated, bypassed=bypassed)
    await session.commit()
    for a, code in new_alerts:
        bus.publish("alert.created", alert_event(a, code))
    return PredictionBatchOut(stored=stored, updated=updated, alerts_created=len(new_alerts),
                              clinical_bypass_applied=bypassed)


@router.get("/features/person-week", response_model=list[PersonWeekFeatures])
async def person_week_features(
    since: date | None = None,
    labeled_only: bool = False,
    _: StaffUser = Depends(model_writers), session: AsyncSession = Depends(get_session),
) -> list[PersonWeekFeatures]:
    """Vector de 8 indicadores por persona-semana para entrenamiento e inferencia (solo seudónimos)."""
    sql = "SELECT * FROM analytics.v_person_week_features WHERE true"
    params: dict = {}
    if since:
        sql += " AND week_start >= :since"
        params["since"] = since
    if labeled_only:
        sql += " AND label_high_risk IS NOT NULL"
    sql += " ORDER BY participant_code, week_start"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [PersonWeekFeatures(**r) for r in rows]


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
