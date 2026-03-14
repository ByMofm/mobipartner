"""Add new source types for local Tucumán real estate sites

Revision ID: 004
Revises: 003
Create Date: 2026-03-14
"""
from typing import Sequence, Union
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE cannot run inside a transaction in PG < 12
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'inmoclick'")
        op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'guzman_guzman'")
        op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'tucumanpropiedades'")
        op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'garcia_pinto'")
        op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'lima_inmobiliaria'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
