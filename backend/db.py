"""Portfolio storage in PostgreSQL (see docker-compose.yml), via SQLAlchemy.

The connection comes from DATABASE_URL in backend/.env, so switching to a hosted
database later only means changing that URL. The schema is managed by Alembic
migrations in migrations/versions; init_db() applies any that are missing.
"""
import datetime
import os
from decimal import Decimal
from typing import List, Optional

from dotenv import load_dotenv
from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, MetaData, Numeric, String,
                        Table, Time, UniqueConstraint, create_engine, delete, false, func, inspect, insert, select,
                        update)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://alphasignal:alphasignal@localhost:5432/alphasignal")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
metadata = MetaData()

portfolios = Table(
    "portfolios",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

holdings = Table(
    "holdings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("portfolio_id", Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
    Column("ticker", String(40), nullable=False),
    Column("name", String(255), nullable=False),
    Column("shares", Numeric(20, 6), nullable=False),
    Column("avg_price", Numeric(20, 6), nullable=False),  # in the stock's trading currency
    Column("currency", String(8), nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("shares > 0", name="shares_positive"),
    CheckConstraint("avg_price >= 0", name="avg_price_not_negative"),
    UniqueConstraint("portfolio_id", "ticker", name="holdings_portfolio_ticker_key"),
)


digests = Table(
    "digests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("portfolio_id", Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("email", String(320), nullable=False),
    Column("send_time", Time, nullable=False),  # local time in `timezone`
    Column("timezone", String(64), nullable=False, server_default="Europe/Helsinki"),
    Column("enabled", Boolean, nullable=False, server_default=false()),
    Column("last_sent_at", DateTime(timezone=True), nullable=True),
    Column("last_attempt_at", DateTime(timezone=True), nullable=True),
    Column("last_status", String(500), nullable=True),
)


def init_db() -> None:
    """Bring the database schema up to date by applying Alembic migrations."""
    from alembic import command
    from alembic.config import Config

    config = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    config.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "migrations"))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        tables = inspect(conn).get_table_names()
        if "holdings" in tables and "alembic_version" not in tables:
            # Created before migrations existed (same shape as revision 0001)
            command.stamp(config, "0001")
        command.upgrade(config, "head")


def _row(row) -> dict:
    d = dict(row._mapping)
    for key, value in d.items():
        if isinstance(value, Decimal):
            d[key] = float(value)
        elif isinstance(value, datetime.datetime):
            d[key] = value.isoformat()
        elif isinstance(value, datetime.time):
            d[key] = value.strftime("%H:%M")
    return d


# ── Portfolios ──
def list_portfolios() -> List[dict]:
    count = func.count(holdings.c.id).label("holdings_count")
    query = (
        select(portfolios, count)
        .select_from(portfolios.outerjoin(holdings))
        .group_by(portfolios.c.id)
        .order_by(portfolios.c.created_at)
    )
    with engine.connect() as conn:
        return [_row(r) for r in conn.execute(query)]


def get_portfolio(portfolio_id: int) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(select(portfolios).where(portfolios.c.id == portfolio_id)).first()
        return _row(row) if row else None


def portfolio_name_taken(name: str, except_id: Optional[int] = None) -> bool:
    query = select(portfolios.c.id).where(func.lower(portfolios.c.name) == name.lower())
    if except_id is not None:
        query = query.where(portfolios.c.id != except_id)
    with engine.connect() as conn:
        return conn.execute(query).first() is not None


def create_portfolio(name: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(insert(portfolios).values(name=name).returning(*portfolios.c)).first()
    return {**_row(row), "holdings_count": 0}


def rename_portfolio(portfolio_id: int, name: str) -> Optional[dict]:
    with engine.begin() as conn:
        row = conn.execute(
            update(portfolios).where(portfolios.c.id == portfolio_id).values(name=name).returning(*portfolios.c)
        ).first()
    return _row(row) if row else None


def delete_portfolio(portfolio_id: int) -> bool:
    """Also deletes the portfolio's holdings (ON DELETE CASCADE)."""
    with engine.begin() as conn:
        return conn.execute(delete(portfolios).where(portfolios.c.id == portfolio_id)).rowcount > 0


# ── Holdings (always within one portfolio) ──
def list_holdings(portfolio_id: int) -> List[dict]:
    query = select(holdings).where(holdings.c.portfolio_id == portfolio_id).order_by(holdings.c.added_at)
    with engine.connect() as conn:
        return [_row(r) for r in conn.execute(query)]


def get_holding_by_ticker(portfolio_id: int, ticker: str) -> Optional[dict]:
    query = select(holdings).where(holdings.c.portfolio_id == portfolio_id, holdings.c.ticker == ticker)
    with engine.connect() as conn:
        row = conn.execute(query).first()
        return _row(row) if row else None


def add_holding(portfolio_id: int, ticker: str, name: str, shares: float, avg_price: float, currency: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            insert(holdings)
            .values(portfolio_id=portfolio_id, ticker=ticker, name=name, shares=shares, avg_price=avg_price,
                    currency=currency, added_at=datetime.datetime.now(datetime.timezone.utc))
            .returning(*holdings.c)
        ).first()
    return _row(row)


def update_holding(portfolio_id: int, holding_id: int, shares: float, avg_price: float) -> Optional[dict]:
    with engine.begin() as conn:
        row = conn.execute(
            update(holdings)
            .where(holdings.c.id == holding_id, holdings.c.portfolio_id == portfolio_id)
            .values(shares=shares, avg_price=avg_price)
            .returning(*holdings.c)
        ).first()
    return _row(row) if row else None


def delete_holding(portfolio_id: int, holding_id: int) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(holdings).where(holdings.c.id == holding_id, holdings.c.portfolio_id == portfolio_id)
        )
        return result.rowcount > 0


# ── Morning email settings (one per portfolio) ──
def get_digest(portfolio_id: int) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(select(digests).where(digests.c.portfolio_id == portfolio_id)).first()
        return _row(row) if row else None


def save_digest(portfolio_id: int, email: str, send_time: datetime.time, timezone: str, enabled: bool) -> dict:
    values = dict(email=email, send_time=send_time, timezone=timezone, enabled=enabled)
    with engine.begin() as conn:
        updated = conn.execute(
            update(digests).where(digests.c.portfolio_id == portfolio_id).values(**values).returning(*digests.c)
        ).first()
        row = updated or conn.execute(
            insert(digests).values(portfolio_id=portfolio_id, **values).returning(*digests.c)
        ).first()
    return _row(row)


def list_enabled_digests() -> List[dict]:
    query = (
        select(digests, portfolios.c.name.label("portfolio_name"))
        .join(portfolios, portfolios.c.id == digests.c.portfolio_id)
        .where(digests.c.enabled)
    )
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(query)]  # raw types: the scheduler compares times


def record_digest_attempt(portfolio_id: int, status: str, sent: bool) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    values = {"last_attempt_at": now, "last_status": status[:500]}
    if sent:
        values["last_sent_at"] = now
    with engine.begin() as conn:
        conn.execute(update(digests).where(digests.c.portfolio_id == portfolio_id).values(**values))
