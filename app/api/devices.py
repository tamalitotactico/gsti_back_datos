"""Endpoints que usa la app móvil (autenticados con token de dispositivo)."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DeviceContext, current_device, require_roles
from app.catalog import INSTRUMENTS, METRICS
from app.db import get_session
from app.models import Device, EmaPrompt, Participant, StaffUser
from app.schemas import (
    DeviceOut, DeviceRegisterIn, DeviceRegisterOut, EmaPromptOut, IngestResultOut, QuestionnaireAckOut,
    QuestionnaireIn, SensorBatchIn,
)
from app.services.audit import audit
from app.services.ingest import BatchConflict, ingest_batch
from app.services.questionnaires import ResponseConflict, submit_response
from app.services.scoring import ScoringError
from app.services.security import new_device_token, normalize_enrollment_code, sha256_hex

router = APIRouter(tags=["device"])
MAX_DEVICES_PER_PARTICIPANT = 3


@router.post("/devices/register", response_model=DeviceRegisterOut, status_code=201)
async def register_device(body: DeviceRegisterIn, session: AsyncSession = Depends(get_session)) -> DeviceRegisterOut:
    code_hash = sha256_hex(normalize_enrollment_code(body.enrollment_code))
    p = await session.scalar(select(Participant).where(Participant.enrollment_code_hash == code_hash)
                             .with_for_update())
    if p is None or p.enrollment_code_expires_at is None or p.enrollment_code_expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Código de enrolamiento inválido o vencido")
    if p.status == "withdrawn":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Participante retirado")
    active = (await session.scalars(select(Device.id).where(Device.participant_id == p.id,
                                                            Device.revoked_at.is_(None)))).all()
    if len(active) >= MAX_DEVICES_PER_PARTICIPANT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Límite de dispositivos alcanzado; pedir revocar uno")

    token = new_device_token()
    dev = Device(participant_id=p.id, kind=body.kind, platform=body.platform, model=body.model,
                 app_version=body.app_version, token_hash=sha256_hex(token))
    session.add(dev)
    await session.flush()
    audit(session, None, "device.register", "participant", p.code, device_id=str(dev.id), kind=body.kind)
    await session.commit()
    return DeviceRegisterOut(device_id=dev.id, participant_code=p.code, device_token=token)


@router.get("/devices/me", response_model=DeviceOut)
async def device_me(ctx: DeviceContext = Depends(current_device)) -> Device:
    return ctx.device


@router.post("/devices/{device_id}/revoke", response_model=DeviceOut)
async def revoke_device(
    device_id: str, staff: StaffUser = Depends(require_roles("admin", "researcher")),
    session: AsyncSession = Depends(get_session),
) -> Device:
    dev = await session.get(Device, device_id)
    if dev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no encontrado")
    if dev.revoked_at is None:
        dev.revoked_at = datetime.now(UTC)
        audit(session, staff.id, "device.revoke", "device", str(dev.id))
        await session.commit()
    return dev


# ------------------------------------------------------------------ catálogo
@router.get("/catalog/metrics")
async def catalog_metrics() -> list[dict]:
    return [m.__dict__ for m in METRICS]


@router.get("/catalog/instruments")
async def catalog_instruments() -> list[dict]:
    return [{"code": i.code, "name": i.name, "version": i.version, "scoring": i.scoring, "purpose": i.purpose,
             "administration": i.administration,
             "items": [{"item": it.item_no, "code": it.code, "min": it.min_value, "max": it.max_value}
                       for it in i.items]}
            for i in INSTRUMENTS]


# ------------------------------------------------------------------ ingesta
@router.post("/ingest/sensors", response_model=IngestResultOut, status_code=202)
async def ingest_sensors(
    batch: SensorBatchIn, ctx: DeviceContext = Depends(current_device), session: AsyncSession = Depends(get_session),
) -> IngestResultOut:
    received_at = datetime.now(UTC)
    try:
        return await ingest_batch(session, ctx.device, ctx.participant, batch, "rest", received_at)
    except BatchConflict:
        raise HTTPException(status.HTTP_409_CONFLICT, "batch_id ya usado por otro dispositivo") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/questionnaires/responses", response_model=QuestionnaireAckOut, status_code=201)
async def post_response(
    body: QuestionnaireIn, ctx: DeviceContext = Depends(current_device), session: AsyncSession = Depends(get_session),
) -> QuestionnaireAckOut:
    try:
        return await submit_response(session, ctx.device, ctx.participant, body)
    except ResponseConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except ScoringError as e:
        raise HTTPException(422, str(e)) from None


# ------------------------------------------------------------------ EMA
@router.get("/ema/prompts", response_model=list[EmaPromptOut])
async def upcoming_prompts(
    hours: int = Query(default=48, ge=1, le=168),
    ctx: DeviceContext = Depends(current_device), session: AsyncSession = Depends(get_session),
) -> list[EmaPrompt]:
    """Avisos vigentes y próximos: la app agenda notificaciones locales con esta lista."""
    now = datetime.now(UTC)
    q = (select(EmaPrompt).where(EmaPrompt.participant_id == ctx.participant.id,
                                 EmaPrompt.status.in_(["scheduled", "delivered"]),
                                 EmaPrompt.expires_at > now,
                                 EmaPrompt.scheduled_at <= now + timedelta(hours=hours))
         .order_by(EmaPrompt.scheduled_at))
    return list((await session.scalars(q)).all())


@router.post("/ema/prompts/{prompt_id}/delivered", response_model=EmaPromptOut)
async def mark_delivered(
    prompt_id: str, ctx: DeviceContext = Depends(current_device), session: AsyncSession = Depends(get_session),
) -> EmaPrompt:
    prompt = await session.get(EmaPrompt, prompt_id)
    if prompt is None or prompt.participant_id != ctx.participant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aviso no encontrado")
    if prompt.status == "scheduled":
        prompt.status = "delivered"
        prompt.delivered_at = datetime.now(UTC)
        await session.commit()
    return prompt
