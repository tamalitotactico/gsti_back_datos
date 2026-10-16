"""Vistas analíticas persona-día/persona-semana y permisos por rol.

Revision ID: 0003
Revises: 0002
"""
from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0003_analytics_and_grants.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP VIEW IF EXISTS analytics.v_processing_latency, analytics.v_ema_adherence, "
        "analytics.v_person_week_questionnaires, analytics.v_person_week_ema, "
        "analytics.v_person_week_sensor, analytics.v_daily_sensor;"
    )
