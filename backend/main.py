import datetime
import json
import os
import queue
import threading
import time
import yfinance as yf
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, OperationalError
import db
import digest
import portfolio as pf
from pipeline import run_pipeline

app = FastAPI()

DB_DOWN = "The database isn't running. Start it from the project folder with: docker compose up -d"
try:
    db.init_db()
except OperationalError:
    # The analysis pages still work without the database; only the portfolio needs it
    print(f"[DB] {DB_DOWN}")


@app.on_event("startup")
def start_background_jobs():
    digest.start_scheduler()


@app.exception_handler(OperationalError)
def database_unavailable(request: Request, exc: OperationalError):
    return JSONResponse(status_code=503, content={"detail": DB_DOWN})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reuse results for a while so repeat lookups are instant and don't cost another LLM call
CACHE_SECONDS = 15 * 60
_cache = {}


def _cached(key):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    return None


@app.get("/analyze/{ticker}")
def analyze(ticker: str, refresh: bool = False):
    key = ticker.upper().strip()
    cached = None if refresh else _cached(("analyze", key))
    if cached:
        return cached
    try:
        result = run_pipeline(key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _cache[("analyze", key)] = (time.time(), result)
    return result


@app.get("/analyze/{ticker}/stream")
def analyze_stream(ticker: str, refresh: bool = False):
    """Same as /analyze, but sends progress events (Server-Sent Events) while the agents work."""
    key = ticker.upper().strip()
    events = queue.Queue()

    def sse(event, data):
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def worker():
        try:
            result = run_pipeline(key, on_step=lambda step, status, detail: events.put(
                ("step", {"step": step, "status": status, "detail": detail})))
            _cache[("analyze", key)] = (time.time(), result)
            events.put(("result", result))
        except ValueError as e:
            events.put(("failed", {"message": str(e), "not_found": True}))
        except Exception as e:
            print(f"[Stream] Pipeline failed for {key}: {e}")
            events.put(("failed", {"message": "Something went wrong. Try again in a moment.", "not_found": False}))

    def stream():
        cached = None if refresh else _cached(("analyze", key))
        if cached:
            yield sse("result", cached)
            return
        threading.Thread(target=worker, daemon=True).start()
        while True:
            event, data = events.get()
            yield sse(event, data)
            if event in ("result", "failed"):
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


HISTORY_RANGES = {"1mo": "1d", "6mo": "1d", "1y": "1d", "5y": "1wk"}


@app.get("/history/{ticker}")
def history(ticker: str, range: str = "1y"):
    if range not in HISTORY_RANGES:
        raise HTTPException(status_code=400, detail=f"range must be one of {list(HISTORY_RANGES)}")
    key = ("history", ticker.upper().strip(), range)
    cached = _cached(key)
    if cached:
        return cached
    df = yf.Ticker(key[1]).history(period=range, interval=HISTORY_RANGES[range])
    points = [{"date": idx.date().isoformat(), "close": round(float(close), 2)} for idx, close in df["Close"].dropna().items()]
    _cache[key] = (time.time(), points)
    return points


US_EXCHANGES = {"NASDAQ", "NYSE", "NYSEArca", "NYSE American", "BATS Trading", "Cboe US"}


@app.get("/search")
def search(q: str):
    q = q.strip()
    if not q:
        return []
    key = ("search", q.lower())
    cached = _cached(key)
    if cached is not None:
        return cached
    try:
        quotes = yf.Search(q, max_results=10, news_count=0).quotes
    except Exception as e:
        print(f"[Search] Failed for {q}: {e}")
        return []
    results = [
        {
            "symbol": x["symbol"],
            "name": x.get("longname") or x.get("shortname") or x["symbol"],
            "exchange": x.get("exchDisp", ""),
            "type": x.get("quoteType"),
        }
        for x in quotes
        if x.get("symbol") and x.get("quoteType") in ("EQUITY", "ETF")
    ]
    # US listings first, since the news and analyst data are US-focused
    results.sort(key=lambda r: r["exchange"] not in US_EXCHANGES)
    results = results[:6]
    _cache[key] = (time.time(), results)
    return results


# ── Portfolio ────────────────────────────────────────────────────────────────
class HoldingIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=40)
    shares: float = Field(gt=0)
    avg_price: float = Field(ge=0)  # in the stock's own trading currency


class HoldingUpdate(BaseModel):
    shares: float = Field(gt=0)
    avg_price: float = Field(ge=0)


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _require_portfolio(portfolio_id: int) -> dict:
    portfolio = db.get_portfolio(portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


def _clean_name(name: str, except_id=None) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(status_code=422, detail="Give the portfolio a name.")
    if db.portfolio_name_taken(name, except_id):
        raise HTTPException(status_code=409, detail=f"You already have a portfolio called '{name}'.")
    return name


@app.get("/portfolios")
def list_portfolios():
    return db.list_portfolios()


@app.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioIn):
    try:
        return db.create_portfolio(_clean_name(body.name))
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"You already have a portfolio called '{body.name}'.")


@app.put("/portfolios/{portfolio_id}")
def rename_portfolio(portfolio_id: int, body: PortfolioIn):
    _require_portfolio(portfolio_id)
    try:
        return db.rename_portfolio(portfolio_id, _clean_name(body.name, except_id=portfolio_id))
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"You already have a portfolio called '{body.name}'.")


@app.delete("/portfolios/{portfolio_id}", status_code=204)
def delete_portfolio(portfolio_id: int):
    if not db.delete_portfolio(portfolio_id):
        raise HTTPException(status_code=404, detail="Portfolio not found")


@app.get("/portfolios/{portfolio_id}")
def get_portfolio(portfolio_id: int):
    data = pf.compute_portfolio(portfolio_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return data


def _resolve_ticker(text: str) -> str:
    """Accept a ticker or a company name ("nvidia" -> NVDA). An exact ticker always wins."""
    typed = text.strip().upper()
    # Search first: it's fast, and a failed price lookup on a name like "NVIDIA" retries for several seconds
    matches = search(text)
    if any(m["symbol"] == typed for m in matches):
        return typed
    if matches:
        return matches[0]["symbol"]
    if pf.quote(typed):  # valid tickers the search doesn't list
        return typed
    raise HTTPException(status_code=404, detail=f"Couldn't find a stock for '{text}'")


@app.post("/portfolios/{portfolio_id}/holdings", status_code=201)
def add_holding(portfolio_id: int, body: HoldingIn):
    portfolio = _require_portfolio(portfolio_id)
    ticker = _resolve_ticker(body.ticker)
    already = f"{ticker} is already in '{portfolio['name']}'. Edit it instead."
    if db.get_holding_by_ticker(portfolio_id, ticker):
        raise HTTPException(status_code=409, detail=already)
    quote = pf.quote(ticker)
    if not quote:
        raise HTTPException(status_code=404, detail=f"No market data found for {ticker}")
    try:
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName") or ticker
    except Exception:
        name = ticker
    try:
        return db.add_holding(portfolio_id, ticker, name, body.shares, body.avg_price, quote["currency"])
    except IntegrityError:  # added twice at the same moment
        raise HTTPException(status_code=409, detail=already)


@app.put("/portfolios/{portfolio_id}/holdings/{holding_id}")
def update_holding(portfolio_id: int, holding_id: int, body: HoldingUpdate):
    holding = db.update_holding(portfolio_id, holding_id, body.shares, body.avg_price)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    return holding


@app.delete("/portfolios/{portfolio_id}/holdings/{holding_id}", status_code=204)
def delete_holding(portfolio_id: int, holding_id: int):
    if not db.delete_holding(portfolio_id, holding_id):
        raise HTTPException(status_code=404, detail="Holding not found")


# ── Morning email ────────────────────────────────────────────────────────────
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class DigestIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    send_time: datetime.time = datetime.time(7, 0)
    timezone: str = Field(default="Europe/Helsinki", max_length=64)
    enabled: bool = True


@app.get("/portfolios/{portfolio_id}/digest")
def get_digest_settings(portfolio_id: int):
    _require_portfolio(portfolio_id)
    settings = db.get_digest(portfolio_id) or {
        "portfolio_id": portfolio_id, "email": "", "send_time": "07:00", "timezone": "Europe/Helsinki",
        "enabled": False, "last_sent_at": None, "last_status": None,
    }
    return {**settings, "email_configured": bool(os.getenv("RESEND_API_KEY"))}


@app.put("/portfolios/{portfolio_id}/digest")
def save_digest_settings(portfolio_id: int, body: DigestIn):
    _require_portfolio(portfolio_id)
    try:
        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Unknown time zone '{body.timezone}'")
    saved = db.save_digest(portfolio_id, body.email.strip(), body.send_time, body.timezone, body.enabled)
    return {**saved, "email_configured": bool(os.getenv("RESEND_API_KEY"))}


@app.get("/portfolios/{portfolio_id}/digest/preview")
def preview_digest(portfolio_id: int):
    """The email as it would look now (news from the last day, or since Friday on a Monday), without sending it."""
    built = digest.build_digest(portfolio_id)
    if built is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return built


@app.post("/portfolios/{portfolio_id}/digest/send")
def send_digest_now(portfolio_id: int):
    _require_portfolio(portfolio_id)
    try:
        return {"status": digest.send_digest(portfolio_id)}
    except digest.EmailNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
