"""Esquema inicial: core, identity, admin.

Revision ID: 0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0001_schema.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP SCHEMA IF EXISTS analytics CASCADE; DROP SCHEMA IF EXISTS identity CASCADE; "
        "DROP SCHEMA IF EXISTS core CASCADE; DROP SCHEMA IF EXISTS admin CASCADE;"
    )
