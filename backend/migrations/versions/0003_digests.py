"""Morning email (digest) settings per portfolio

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "digests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("send_time", sa.Time, nullable=False),  # local time in `timezone`
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Helsinki"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(500), nullable=True),
    )


def downgrade():
    op.drop_table("digests")
