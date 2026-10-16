"""Carga el catálogo de métricas e instrumentos (app/catalog.py).

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

from app.catalog import INSTRUMENTS, METRICS

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for m in METRICS:
        bind.execute(
            sa.text(
                "INSERT INTO core.metric_definitions "
                "(code, description, unit, value_type, granularity, min_value, max_value, daily_agg) "
                "VALUES (:code, :description, :unit, :value_type, :granularity, :min_value, :max_value, :daily_agg) "
                "ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description, unit = EXCLUDED.unit, "
                "value_type = EXCLUDED.value_type, granularity = EXCLUDED.granularity, "
                "min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value, daily_agg = EXCLUDED.daily_agg"
            ),
            m.__dict__,
        )
    for ins in INSTRUMENTS:
        bind.execute(
            sa.text(
                "INSERT INTO core.instruments (code, name, version, n_items, scoring, purpose, administration) "
                "VALUES (:code, :name, :version, :n_items, :scoring, :purpose, :administration) "
                "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, version = EXCLUDED.version, "
                "n_items = EXCLUDED.n_items, scoring = EXCLUDED.scoring, purpose = EXCLUDED.purpose, "
                "administration = EXCLUDED.administration"
            ),
            {
                "code": ins.code, "name": ins.name, "version": ins.version, "n_items": ins.n_items,
                "scoring": ins.scoring, "purpose": ins.purpose, "administration": ins.administration,
            },
        )
        for it in ins.items:
            bind.execute(
                sa.text(
                    "INSERT INTO core.instrument_items "
                    "(instrument_code, item_no, code, min_value, max_value, scored, is_safety_item) "
                    "VALUES (:ic, :item_no, :code, :min_value, :max_value, :scored, :is_safety_item) "
                    "ON CONFLICT (instrument_code, item_no) DO UPDATE SET code = EXCLUDED.code, "
                    "min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value, "
                    "scored = EXCLUDED.scored, is_safety_item = EXCLUDED.is_safety_item"
                ),
                {"ic": ins.code, **it.__dict__},
            )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DELETE FROM core.instrument_items; DELETE FROM core.instruments; DELETE FROM core.metric_definitions;"
    )
