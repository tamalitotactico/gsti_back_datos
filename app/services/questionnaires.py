"""Registro de respuestas a cuestionarios y EMA, puntuación y reglas de seguridad."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import INSTRUMENTS_BY_CODE
from app.config import get_settings
from app.models import Alert, Device, EmaPrompt, ItemResponse, Participant, QuestionnaireResponse
from app.schemas import QuestionnaireAckOut, QuestionnaireIn
from app.services.realtime import bus
from app.services.safety import AlertSpec, evaluate_questionnaire
from app.services.scoring import ScoringError, validate_and_score


class ResponseConflict(Exception):
    pass


def alert_event(a: Alert, participant_code: str) -> dict:
    return {
        "id": str(a.id), "participant_code": participant_code, "rule_code": a.rule_code,
        "severity": a.severity, "status": a.status, "source_type": a.source_type,
    }


async def create_alerts(
    session: AsyncSession, participant: Participant, specs: list[AlertSpec], source_type: str,
    response_id=None, prediction_id=None,
) -> list[Alert]:
    created = []
    for spec in specs:
        a = Alert(participant_id=participant.id, source_type=source_type, response_id=response_id,
                  prediction_id=prediction_id, rule_code=spec.rule_code, severity=spec.severity,
                  detail=spec.detail, status="open")
        session.add(a)
        created.append(a)
    return created


async def submit_response(
    session: AsyncSession, device: Device, participant: Participant, body: QuestionnaireIn,
) -> QuestionnaireAckOut:
    received_at = datetime.now(UTC)

    prior = await session.scalar(
        select(QuestionnaireResponse).where(QuestionnaireResponse.client_response_id == body.client_response_id)
    )
    if prior is not None:
        if prior.participant_id != participant.id:
            raise ResponseConflict("client_response_id ya usado por otro participante")
        has_alert = await session.scalar(
            select(Alert.id).where(Alert.response_id == prior.id, Alert.severity.in_(["moderate", "high"])).limit(1)
        )
        return QuestionnaireAckOut(response_id=prior.id, replayed=True, support_resources=has_alert is not None)

    ins = INSTRUMENTS_BY_CODE.get(body.instrument)
    if ins is None:
        raise ScoringError(f"Instrumento desconocido: {body.instrument}")
    if (ins.purpose == "ema") != (body.context == "ema"):
        raise ScoringError("El contexto 'ema' solo aplica a instrumentos EMA y viceversa")
    if body.completed_at > received_at + timedelta(minutes=get_settings().max_clock_skew_minutes):
        raise ScoringError("completed_at en el futuro (revisar reloj del teléfono)")

    result = validate_and_score(ins, {a.item: a.value for a in body.answers})

    prompt: EmaPrompt | None = None
    if body.ema_prompt_id is not None:
        prompt = await session.get(EmaPrompt, body.ema_prompt_id, with_for_update=True)
        if prompt is None or prompt.participant_id != participant.id:
            raise ScoringError("ema_prompt_id no corresponde a este participante")
        if prompt.instrument_code != ins.code:
            raise ScoringError("El instrumento no coincide con el del aviso EMA")
        if prompt.status in ("answered", "answered_late"):
            raise ResponseConflict("Este aviso EMA ya fue respondido")
        if prompt.status == "cancelled":
            raise ScoringError("Este aviso EMA fue cancelado")
        if body.completed_at < prompt.scheduled_at:
            raise ScoringError("La respuesta es anterior a la hora programada del aviso")
        prompt.status = "answered" if body.completed_at <= prompt.expires_at else "answered_late"

    resp = QuestionnaireResponse(
        client_response_id=body.client_response_id, participant_id=participant.id, device_id=device.id,
        instrument_code=ins.code, context=body.context, ema_prompt_id=body.ema_prompt_id,
        started_at=body.started_at, completed_at=body.completed_at, received_at=received_at,
        total_score=result.total, subscores=result.subscores,
    )
    session.add(resp)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ResponseConflict("Respuesta duplicada o aviso EMA ya respondido (reintentar para confirmar)") from None
    # Solo se guardan los ítems efectivamente respondidos (los omitidos por salto no)
    for a in body.answers:
        session.add(ItemResponse(response_id=resp.id, item_no=a.item, value=a.value))

    outcome = evaluate_questionnaire(ins.code, result, get_settings())
    alerts = await create_alerts(session, participant, outcome.alerts, "questionnaire", response_id=resp.id)

    resp.processed_at = datetime.now(UTC)
    device.last_seen_at = received_at
    await session.commit()

    for a in alerts:
        bus.publish("alert.created", alert_event(a, participant.code))
    return QuestionnaireAckOut(response_id=resp.id, support_resources=outcome.support_resources)

