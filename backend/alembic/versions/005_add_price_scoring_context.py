"""Add price scoring context columns to properties

Revision ID: 005
Revises: 004
Create Date: 2026-03-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("score_median_per_m2", sa.Float(), nullable=True))
    op.add_column("properties", sa.Column("score_num_comparables", sa.Integer(), nullable=True))
    op.add_column("properties", sa.Column("score_comparison_scope", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "score_comparison_scope")
    op.drop_column("properties", "score_num_comparables")
    op.drop_column("properties", "score_median_per_m2")
