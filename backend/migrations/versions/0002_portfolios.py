"""Multiple portfolios: each holding belongs to a portfolio

Existing holdings are moved into a portfolio called "My portfolio".

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("holdings", sa.Column("portfolio_id", sa.Integer, nullable=True))

    conn = op.get_bind()
    if conn.execute(sa.text("SELECT COUNT(*) FROM holdings")).scalar():
        portfolio_id = conn.execute(
            sa.text("INSERT INTO portfolios (name) VALUES ('My portfolio') RETURNING id")
        ).scalar()
        conn.execute(sa.text("UPDATE holdings SET portfolio_id = :pid"), {"pid": portfolio_id})

    op.alter_column("holdings", "portfolio_id", nullable=False)
    op.create_foreign_key(
        "holdings_portfolio_id_fkey", "holdings", "portfolios", ["portfolio_id"], ["id"], ondelete="CASCADE"
    )
    # The same stock can now be in several portfolios, but only once per portfolio
    op.drop_constraint("holdings_ticker_key", "holdings", type_="unique")
    op.create_unique_constraint("holdings_portfolio_ticker_key", "holdings", ["portfolio_id", "ticker"])


def downgrade():
    op.drop_constraint("holdings_portfolio_ticker_key", "holdings", type_="unique")
    op.create_unique_constraint("holdings_ticker_key", "holdings", ["ticker"])  # fails if a ticker is in several portfolios
    op.drop_constraint("holdings_portfolio_id_fkey", "holdings", type_="foreignkey")
    op.drop_column("holdings", "portfolio_id")
    op.drop_table("portfolios")
