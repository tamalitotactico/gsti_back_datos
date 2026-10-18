"""Alineación con el artículo: ULS-6, gatillo clínico, niveles de riesgo, población 16-35 y vista de 8 indicadores.

Revision ID: 0004
Revises: 0003
"""
import importlib
from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0004_article_alignment.sql"


def upgrade() -> None:
    # Re-sembrar el catálogo (agrega ULS-6 y actualiza propósitos) con la misma lógica idempotente de 0002
    importlib.import_module("migrations.versions.0002_seed_catalog").upgrade()
    op.get_bind().exec_driver_sql(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP VIEW IF EXISTS analytics.v_person_week_features;"
        "ALTER TABLE core.model_predictions DROP COLUMN IF EXISTS risk_level, DROP COLUMN IF EXISTS clinical_bypass;"
        "ALTER TABLE core.participants DROP CONSTRAINT IF EXISTS participants_minor_consent_check,"
        " DROP COLUMN IF EXISTS assent_at, DROP COLUMN IF EXISTS guardian_consent_at;"
    )
