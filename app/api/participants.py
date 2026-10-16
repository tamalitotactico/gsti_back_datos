"""Enrolamiento, consulta y retiro de participantes (solo personal)."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.catalog import INSTRUMENTS_BY_CODE
from app.config import get_settings
from app.db import get_session
from app.models import (
    Alert, Device, EmaPrompt, Participant, ParticipantIdentity, QuestionnaireResponse, SensorObservation, StaffUser,
)
from app.schemas import (
    DeviceOut, EmaPromptOut, EmaScheduleIn, EnrollmentOut, IdentityOut, ParticipantCreateIn, ParticipantOut,
    ParticipantSummary, QuestionnaireOut, WithdrawIn,
)
from app.services import crypto
from app.services.audit import audit
from app.services.ema import build_schedule
from app.services.security import new_enrollment_code, sha256_hex

router = APIRouter(prefix="/participants", tags=["participants"])

ENROLL_TTL = timedelta(days=7)
any_staff = require_roles("admin", "clinician", "researcher")
enrollers = require_roles("admin", "researcher")


async def get_participant(session: AsyncSession, code: str) -> Participant:
    p = await session.scalar(select(Participant).where(Participant.code == code.upper()))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Participante no encontrado")
    return p


def _issue_code(p: Participant) -> str:
    code = new_enrollment_code()
    p.enrollment_code_hash = sha256_hex(code)
    p.enrollment_code_expires_at = datetime.now(UTC) + ENROLL_TTL
    return code


@router.post("", response_model=EnrollmentOut, status_code=201)
async def enroll(
    body: ParticipantCreateIn,
    staff: StaffUser = Depends(enrollers),
    session: AsyncSession = Depends(get_session),
) -> EnrollmentOut:
    p = Participant(consent_version=body.consent_version, cohort=body.cohort, age_band=body.age_band,
                    sex=body.sex, semester=body.semester)
    code = _issue_code(p)
    session.add(p)
    await session.flush()

    if body.identity is not None:
        idn = body.identity
        try:
            vault = ParticipantIdentity(
                participant_id=p.id,
                student_code_hmac=crypto.student_code_hmac(idn.student_code) if idn.student_code else None,
                full_name_enc=crypto.encrypt(idn.full_name),
                email_enc=crypto.encrypt(str(idn.email) if idn.email else None),
                phone_enc=crypto.encrypt(idn.phone),
            )
        except crypto.IdentityKeyMissing:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Bóveda de identidad no configurada (IDENTITY_ENCRYPTION_KEY)") from None
        session.add(vault)
        try:
            await session.flush()
        except IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ese código de estudiante ya está inscrito") from None

    audit(session, staff.id, "participant.enroll", "participant", p.code, with_identity=body.identity is not None)
    await session.commit()
    await session.refresh(p)
    return EnrollmentOut(participant=ParticipantOut.model_validate(p), enrollment_code=code,
                         enrollment_code_expires_at=p.enrollment_code_expires_at)


@router.get("", response_model=list[ParticipantOut])
async def list_participants(
    status_filter: str | None = None,
    _: StaffUser = Depends(any_staff),
    session: AsyncSession = Depends(get_session),
) -> list[Participant]:
    q = select(Participant).order_by(Participant.code)
    if status_filter:
        q = q.where(Participant.status == status_filter)
    return list((await session.scalars(q)).all())


@router.get("/{code}", response_model=ParticipantSummary)
async def participant_summary(
    code: str, _: StaffUser = Depends(any_staff), session: AsyncSession = Depends(get_session),
) -> ParticipantSummary:
    p = await get_participant(session, code)
    since = datetime.now(UTC) - timedelta(days=7)
    devices, last_seen = (await session.execute(
        select(func.count(Device.id), func.max(Device.last_seen_at)).where(Device.participant_id == p.id)
    )).one()
    obs7 = await session.scalar(select(func.count(SensorObservation.id)).where(
        SensorObservation.participant_id == p.id, SensorObservation.window_start >= since))
    responses = await session.scalar(select(func.count(QuestionnaireResponse.id)).where(
        QuestionnaireResponse.participant_id == p.id))
    open_alerts = await session.scalar(select(func.count(Alert.id)).where(
        Alert.participant_id == p.id, Alert.status.in_(["open", "acknowledged"])))
    adherence = await session.scalar(
        text("SELECT adherence FROM analytics.v_ema_adherence WHERE participant_code = :c"), {"c": p.code})
    return ParticipantSummary(
        participant=ParticipantOut.model_validate(p), devices=devices, last_seen_at=last_seen,
        observations_last_7d=obs7 or 0, responses_total=responses or 0, open_alerts=open_alerts or 0,
        ema_adherence=adherence,
    )


@router.post("/{code}/enrollment-code", response_model=EnrollmentOut)
async def regenerate_enrollment_code(
    code: str, staff: StaffUser = Depends(enrollers), session: AsyncSession = Depends(get_session),
) -> EnrollmentOut:
    p = await get_participant(session, code)
    if p.status == "withdrawn":
        raise HTTPException(status.HTTP_409_CONFLICT, "Participante retirado")
    new_code = _issue_code(p)
    audit(session, staff.id, "participant.enrollment_code", "participant", p.code)
    await session.commit()
    return EnrollmentOut(participant=ParticipantOut.model_validate(p), enrollment_code=new_code,
                         enrollment_code_expires_at=p.enrollment_code_expires_at)


@router.get("/{code}/identity", response_model=IdentityOut)
async def reveal_identity(
    code: str,
    staff: StaffUser = Depends(require_roles("admin", "clinician")),
    session: AsyncSession = Depends(get_session),
) -> IdentityOut:
    """Solo para derivación. Cada consulta queda en la bitácora de auditoría."""
    p = await get_participant(session, code)
    vault = await session.get(ParticipantIdentity, p.id)
    if vault is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sin datos de contacto en la bóveda")
    try:
        out = IdentityOut(participant_code=p.code, full_name=crypto.decrypt(vault.full_name_enc),
                          email=crypto.decrypt(vault.email_enc), phone=crypto.decrypt(vault.phone_enc))
    except crypto.IdentityKeyMissing:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bóveda de identidad no configurada") from None
    audit(session, staff.id, "identity.reveal", "participant", p.code)
    await session.commit()
    return out


@router.get("/{code}/devices", response_model=list[DeviceOut])
async def list_devices(
    code: str, _: StaffUser = Depends(any_staff), session: AsyncSession = Depends(get_session),
) -> list[Device]:
    p = await get_participant(session, code)
    return list((await session.scalars(select(Device).where(Device.participant_id == p.id))).all())


@router.get("/{code}/responses", response_model=list[QuestionnaireOut])
async def list_responses(
    code: str, instrument: str | None = None,
    _: StaffUser = Depends(any_staff), session: AsyncSession = Depends(get_session),
) -> list[QuestionnaireResponse]:
    p = await get_participant(session, code)
    q = (select(QuestionnaireResponse).where(QuestionnaireResponse.participant_id == p.id)
         .order_by(QuestionnaireResponse.completed_at.desc()))
    if instrument:
        q = q.where(QuestionnaireResponse.instrument_code == instrument)
    return list((await session.scalars(q)).all())


@router.post("/{code}/ema-schedule", response_model=list[EmaPromptOut], status_code=201)
async def create_ema_schedule(
    code: str, body: EmaScheduleIn,
    staff: StaffUser = Depends(enrollers), session: AsyncSession = Depends(get_session),
) -> list[EmaPrompt]:
    p = await get_participant(session, code)
    if p.status == "withdrawn":
        raise HTTPException(status.HTTP_409_CONFLICT, "Participante retirado")
    ins = INSTRUMENTS_BY_CODE.get(body.instrument)
    if ins is None or ins.purpose != "ema":
        raise HTTPException(422, "El instrumento debe ser de tipo EMA")
    try:
        slots = build_schedule(body.start_date, body.days, body.prompts_per_day, body.window_start_hour,
                               body.window_end_hour, body.expiry_minutes, get_settings().study_timezone, body.seed)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    prompts = [EmaPrompt(participant_id=p.id, instrument_code=ins.code, scheduled_at=a, expires_at=b)
               for a, b in slots]
    session.add_all(prompts)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya hay avisos programados en esas horas") from None
    audit(session, staff.id, "ema.schedule", "participant", p.code, n=len(prompts))
    await session.commit()
    return prompts


@router.post("/{code}/withdraw", status_code=204)
async def withdraw(
    code: str, body: WithdrawIn,
    staff: StaffUser = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session),
) -> Response:
    """Retiro del consentimiento. purge_data borra todo lo del participante (irreversible)."""
    p = await get_participant(session, code)
    now = datetime.now(UTC)
    if body.purge_data:
        audit(session, staff.id, "participant.purge", "participant", p.code, reason=body.reason)
        await session.execute(delete(Participant).where(Participant.id == p.id))
    else:
        p.status = "withdrawn"
        p.withdrawn_at = now
        p.enrollment_code_hash = None
        await session.execute(update(Device).where(Device.participant_id == p.id, Device.revoked_at.is_(None))
                              .values(revoked_at=now))
        await session.execute(update(EmaPrompt).where(EmaPrompt.participant_id == p.id,
                                                      EmaPrompt.status.in_(["scheduled", "delivered"]),
                                                      EmaPrompt.scheduled_at > now)
                              .values(status="cancelled"))
        if body.purge_identity:
            await session.execute(delete(ParticipantIdentity).where(ParticipantIdentity.participant_id == p.id))
        audit(session, staff.id, "participant.withdraw", "participant", p.code,
              purge_identity=body.purge_identity, reason=body.reason)
    await session.commit()
    return Response(status_code=204)
