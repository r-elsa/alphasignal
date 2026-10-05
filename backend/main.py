import json
import queue
import threading
import time
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pipeline import run_pipeline

app = FastAPI()

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
