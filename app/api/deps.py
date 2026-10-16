"""Dependencias de autenticación para personal (JWT) y dispositivos (token opaco)."""
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Device, Participant, StaffUser
from app.services.security import decode_staff_token, sha256_hex

bearer = HTTPBearer(auto_error=False)

_UNAUTH = HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas",
                        headers={"WWW-Authenticate": "Bearer"})


async def staff_from_token(session: AsyncSession, token: str) -> StaffUser | None:
    try:
        payload = decode_staff_token(token)
    except jwt.PyJWTError:
        return None
    staff = await session.get(StaffUser, payload.get("sub"))
    if staff is None or not staff.is_active:
        return None
    return staff


async def current_staff(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> StaffUser:
    if creds is None or creds.credentials.startswith("dev_"):
        raise _UNAUTH
    staff = await staff_from_token(session, creds.credentials)
    if staff is None:
        raise _UNAUTH
    return staff


def require_roles(*roles: str) -> Callable:
    async def _dep(staff: StaffUser = Depends(current_staff)) -> StaffUser:
        if staff.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Rol sin permiso para esta acción")
        return staff

    return _dep


@dataclass
class DeviceContext:
    device: Device
    participant: Participant


async def device_from_token(session: AsyncSession, token: str) -> DeviceContext | None:
    if not token.startswith("dev_"):
        return None
    row = (
        await session.execute(
            select(Device, Participant)
            .join(Participant, Participant.id == Device.participant_id)
            .where(Device.token_hash == sha256_hex(token), Device.revoked_at.is_(None))
        )
    ).first()
    if row is None:
        return None
    return DeviceContext(device=row[0], participant=row[1])


async def current_device(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> DeviceContext:
    if creds is None:
        raise _UNAUTH
    ctx = await device_from_token(session, creds.credentials)
    if ctx is None:
        raise _UNAUTH
    if ctx.participant.status == "withdrawn":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Participante retirado del estudio")
    return ctx
