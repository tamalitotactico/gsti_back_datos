"""Bitácora de auditoría (solo anexar). Registra accesos sensibles del personal."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def audit(
    session: AsyncSession,
    staff_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    **detail,
) -> None:
    session.add(AuditLog(staff_id=staff_id, action=action, target_type=target_type,
                         target_id=target_id, detail=detail))
