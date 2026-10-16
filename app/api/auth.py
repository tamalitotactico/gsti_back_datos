"""Autenticación y gestión del personal (dashboard)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_staff, require_roles
from app.config import get_settings
from app.db import get_session
from app.models import StaffUser
from app.schemas import LoginIn, StaffCreateIn, StaffOut, TokenOut
from app.services.audit import audit
from app.services.security import create_staff_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash fijo para igualar el tiempo de respuesta cuando el correo no existe
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    staff = await session.scalar(select(StaffUser).where(StaffUser.email == body.email.lower()))
    ok = verify_password(body.password, staff.password_hash if staff else _DUMMY_HASH)
    if staff is None or not ok or not staff.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo o contraseña incorrectos")
    audit(session, staff.id, "login", "staff", str(staff.id))
    await session.commit()
    s = get_settings()
    return TokenOut(access_token=create_staff_token(str(staff.id), staff.role), role=staff.role,
                    expires_in_minutes=s.jwt_expire_minutes)


@router.get("/me", response_model=StaffOut)
async def me(staff: StaffUser = Depends(current_staff)) -> StaffUser:
    return staff


@router.post("/staff", response_model=StaffOut, status_code=201)
async def create_staff(
    body: StaffCreateIn,
    admin: StaffUser = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> StaffUser:
    user = StaffUser(email=body.email.lower(), full_name=body.full_name, role=body.role,
                     password_hash=hash_password(body.password))
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un usuario con ese correo") from None
    audit(session, admin.id, "staff.create", "staff", str(user.id), role=body.role)
    await session.commit()
    return user
