"""Live portfolio valuation: prices, currency conversion and totals.

Used by the portfolio page (main.py) and the morning email (digest.py).
"""
import concurrent.futures
import time
from typing import Optional

import yfinance as yf

import db
from pipeline import _usd_rate

PORTFOLIO_CURRENCY = "EUR"
QUOTE_SECONDS = 60
_quotes = {}


def quote(ticker: str) -> Optional[dict]:
    """Last price, previous close and currency; cached briefly so the portfolio page stays fast."""
    hit = _quotes.get(ticker)
    if hit and time.time() - hit[0] < QUOTE_SECONDS:
        return hit[1]
    try:
        fi = yf.Ticker(ticker).fast_info
        q = {"price": float(fi["last_price"]), "prev_close": float(fi["previous_close"]), "currency": fi["currency"]}
    except Exception as e:
        print(f"[Portfolio] No quote for {ticker}: {e}")
        return None
    _quotes[ticker] = (time.time(), q)
    return q


def to_portfolio_currency(amount: float, currency: str) -> Optional[float]:
    """Convert at today's rate (London prices are in pence, which _usd_rate handles)."""
    from_usd, target_usd = _usd_rate(currency), _usd_rate(PORTFOLIO_CURRENCY)
    return amount * from_usd / target_usd if from_usd and target_usd else None


def compute_portfolio(portfolio_id: int) -> Optional[dict]:
    """The portfolio with every holding valued at live prices, plus totals. None if it doesn't exist."""
    portfolio = db.get_portfolio(portfolio_id)
    if not portfolio:
        return None
    holdings = db.list_holdings(portfolio_id)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        quotes = list(executor.map(lambda h: quote(h["ticker"]), holdings))

    rows = []
    for h, q in zip(holdings, quotes):
        row = {**h, "price": None, "day_change_pct": None, "value": None, "cost": None,
               "gain": None, "gain_pct": None, "day_change": None, "weight_pct": None}
        if q:
            value_local = h["shares"] * q["price"]
            cost_local = h["shares"] * h["avg_price"]
            value = to_portfolio_currency(value_local, h["currency"])
            cost = to_portfolio_currency(cost_local, h["currency"])
            prev_value = to_portfolio_currency(h["shares"] * q["prev_close"], h["currency"])
            row.update({
                "price": round(q["price"], 4),
                "day_change_pct": round((q["price"] / q["prev_close"] - 1) * 100, 2) if q["prev_close"] else None,
                "value": value,
                "cost": cost,
                "gain": value - cost if value is not None and cost is not None else None,
                "gain_pct": (value_local / cost_local - 1) * 100 if cost_local else None,
                "day_change": value - prev_value if value is not None and prev_value is not None else None,
            })
        rows.append(row)

    priced = [r for r in rows if r["value"] is not None]
    total_value = sum(r["value"] for r in priced)
    total_cost = sum(r["cost"] for r in priced)
    day_change = sum(r["day_change"] or 0 for r in priced)
    for r in priced:
        r["weight_pct"] = r["value"] / total_value * 100 if total_value else None

    return {
        **portfolio,
        "currency": PORTFOLIO_CURRENCY,
        "holdings": rows,
        "totals": {
            "value": total_value,
            "cost": total_cost,
            "gain": total_value - total_cost,
            "gain_pct": (total_value / total_cost - 1) * 100 if total_cost else None,
            "day_change": day_change,
            "day_change_pct": day_change / (total_value - day_change) * 100 if total_value - day_change else None,
            "missing_prices": len(rows) - len(priced),
        },
    }
