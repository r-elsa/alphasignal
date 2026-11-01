"""Holdings table (single portfolio)

Revision ID: 0001
Revises:
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("avg_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("shares > 0", name="shares_positive"),
        sa.CheckConstraint("avg_price >= 0", name="avg_price_not_negative"),
    )


def downgrade():
    op.drop_table("holdings")
