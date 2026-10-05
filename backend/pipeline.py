import os
import re
import calendar
import datetime
import feedparser
import requests
import statistics
from urllib.parse import quote_plus
import yfinance as yf
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from typing import Callable, List, Literal, Optional, TypedDict
import concurrent.futures

load_dotenv()

# GPT-5 models only support the default temperature; low reasoning effort keeps it fast
llm = ChatOpenAI(model="gpt-5.5", reasoning_effort="low")


# ── Shared state ──────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    ticker: str
    articles: list
    stock_info: dict
    peers: dict
    brief: dict


# ── Brief schema (the LLM must return exactly this) ──────────────────────────
class Development(BaseModel):
    headline: str = Field(description="What happened, in one short sentence. A concrete event, not an opinion piece.")
    why_it_matters: str = Field(description="One sentence on the impact for this company specifically.")
    source_ids: List[int] = Field(description="IDs of the 1-3 best articles this comes from, strongest first. Must not be empty.")


class Point(BaseModel):
    text: str = Field(description="One specific sentence about this company.")
    source_ids: List[int] = Field(description="Supporting article IDs. Empty only if this is general background knowledge.")


class ArticleSentiment(BaseModel):
    id: int
    sentiment: Literal["positive", "negative", "neutral"] = Field(description="Tone of the article towards this stock.")


class Brief(BaseModel):
    company_description: str = Field(description="One plain sentence a beginner understands: what the company does and how it makes money. No jargon.")
    tldr: str = Field(description="One or two plain sentences summing up the current situation for a beginner investor.")
    developments: List[Development] = Field(description="3-5 most important recent events, most impactful first.")
    bull_case: List[Point] = Field(description="2-3 specific reasons the stock could go up.")
    bear_case: List[Point] = Field(description="2-3 specific risks for this company. No generic market risks.")
    watch: List[str] = Field(description="2-4 upcoming catalysts or open questions to watch, with dates when known.")
    article_sentiment: List[ArticleSentiment] = Field(description="One entry per article provided.")
    sentiment_summary: str = Field(description="One sentence on the tone of the news, and whether it agrees with the analyst consensus.")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _num(value, digits=2):
    """Round numbers; turn missing or non-numeric values into None."""
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return None


def _published(entry) -> Optional[str]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.datetime.fromtimestamp(calendar.timegm(parsed), tz=datetime.timezone.utc).isoformat()


# ── Agent 1: Scraper ──────────────────────────────────────────────────────────
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_ARTICLES = 20
MAX_PER_PUBLISHER = 3
GENERIC_WORDS = {"bank", "first", "general", "american", "united", "international", "global", "national", "new", "the"}
QUOTE_PAGE = re.compile(r"stock price, news, quote|stock quote|price, news, quote", re.I)


def _short_name(name: str) -> str:
    """'NVIDIA Corporation' -> 'NVIDIA', 'Apple Inc.' -> 'Apple'."""
    short = re.sub(r"\b(corporation|corp|incorporated|inc|ltd|limited|plc|holdings?|group|company|co|n\.v|s\.a|ag|se)\b\.?", "", name, flags=re.I)
    return re.sub(r"[,\s]+$", "", short).strip() or name


def scraper_agent(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    company = _short_name(state["stock_info"].get("name") or ticker)
    print(f"[Scraper Agent] Fetching news for {ticker} ({company})...")

    # Short tickers like "F" or "GM" are too ambiguous to search or match on their own
    short_ticker = len(ticker) <= 2
    first_word = company.split()[0]
    names = [company]
    if first_word != company and len(first_word) >= 4 and first_word.lower() not in GENERIC_WORDS:
        names.append(first_word)  # "Ford Motor" -> also match "Ford"
    if short_ticker:
        ticker_pattern = rf"(\(|\$|NYSE:\s?|NASDAQ:\s?){re.escape(ticker)}\b"
        google_query = quote_plus(" OR ".join(f'"{n}"' for n in names) + " stock when:7d")
    else:
        ticker_pattern = rf"\b{re.escape(ticker)}\b"
        google_query = quote_plus(" OR ".join(f'"{n}"' for n in names) + f" OR {ticker} stock when:7d")
    feeds = [
        # Google News aggregates many publishers (Reuters, CNBC, WSJ, Bloomberg, FT, Barron's...)
        (f"https://news.google.com/rss/search?q={google_query}&hl=en-US&gl=US&ceid=US:en", "Google News"),
        (f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US", "Yahoo Finance"),
        (f"https://seekingalpha.com/api/sa/combined/{ticker}.xml", "Seeking Alpha"),
        (f"https://www.nasdaq.com/feed/rssoutbound?symbol={ticker}", "Nasdaq"),
    ]

    def fetch(url, default_source):
        items = []
        try:
            feed = feedparser.parse(url, agent=BROWSER_UA)
            print(f"[Scraper Agent] {default_source}: {len(feed.entries)} entries")
            for entry in feed.entries[:40]:
                title = entry.get("title", "")
                if not title:
                    continue
                source = entry.get("source", {}).get("title") or entry.get("publisher") or default_source
                summary = entry.get("summary", "")
                if default_source == "Google News":
                    # Google appends " - Publisher" to titles and its summaries are just HTML links
                    title = re.sub(r"\s+-\s+[^-]+$", "", title)
                    summary = ""
                items.append({
                    "title": title,
                    "summary": re.sub(r"<[^>]+>", "", summary)[:300],
                    "link": entry.get("link", ""),
                    "source": source,
                    "published": _published(entry),
                })
        except Exception as e:
            print(f"[Scraper Agent] Failed {default_source}: {e}")
        return items

    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = executor.map(lambda f: fetch(*f), feeds)
    articles = [a for items in results for a in items]

    # Keep only articles that actually mention the company
    name_pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)
    ticker_re = re.compile(ticker_pattern)
    articles = [
        a for a in articles
        if (name_pattern.search(a["title"] + " " + a["summary"]) or ticker_re.search(a["title"] + " " + a["summary"]))
        and not QUOTE_PAGE.search(a["title"])
    ]

    # Deduplicate by title, newest first, and cap each publisher so no single one dominates
    articles.sort(key=lambda a: a["published"] or "", reverse=True)
    seen = set()
    per_publisher = {}
    unique_articles = []
    for a in articles:
        key = re.sub(r"\W+", "", a["title"].lower())
        publisher = a["source"].lower().replace(".com", "")
        if "yahoo" in publisher:
            publisher = "yahoo"  # Yahoo Finance UK/Canada/Singapore count as one publisher
        if key in seen or per_publisher.get(publisher, 0) >= MAX_PER_PUBLISHER:
            continue
        seen.add(key)
        per_publisher[publisher] = per_publisher.get(publisher, 0) + 1
        unique_articles.append(a)
        if len(unique_articles) >= MAX_ARTICLES:
            break

    for i, a in enumerate(unique_articles, start=1):
        a["id"] = i

    print(f"[Scraper Agent] Found {len(unique_articles)} articles from {len(per_publisher)} publishers")
    return {"articles": unique_articles}


# ── Agent 2: Consensus ────────────────────────────────────────────────────────
def consensus_agent(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    print(f"[Consensus Agent] Fetching analyst data for {ticker}...")
    stock = yf.Ticker(ticker)
    info = stock.info

    price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    prev_close = _num(info.get("previousClose") or info.get("regularMarketPreviousClose"))
    target = _num(info.get("targetMeanPrice"))
    as_of = info.get("regularMarketTime")

    ratings = None
    try:
        rec = stock.recommendations
        if rec is not None and not rec.empty:
            row = rec.iloc[0]
            ratings = {k: int(row[k]) for k in ["strongBuy", "buy", "hold", "sell", "strongSell"]}
    except Exception as e:
        print(f"[Consensus Agent] No rating breakdown: {e}")

    next_earnings = None
    try:
        dates = (stock.calendar or {}).get("Earnings Date") or []
        if dates:
            next_earnings = dates[0].isoformat()
    except Exception as e:
        print(f"[Consensus Agent] No earnings date: {e}")

    earnings_history = []
    try:
        eh = stock.earnings_history
        if eh is not None and not eh.empty:
            for quarter, row in eh.tail(4).iterrows():
                actual, estimate = _num(row.get("epsActual")), _num(row.get("epsEstimate"))
                if actual is None or estimate is None:
                    continue
                earnings_history.append({
                    "quarter_end": quarter.date().isoformat() if hasattr(quarter, "date") else str(quarter)[:10],
                    "eps_actual": actual,
                    "eps_estimate": estimate,
                    "surprise_pct": _num((row.get("surprisePercent") or 0) * 100, 1),
                })
    except Exception as e:
        print(f"[Consensus Agent] No earnings history: {e}")

    def _pct(key):
        value = _num(info.get(key), 4)
        return round(value * 100, 1) if value is not None else None

    financials = {
        "revenue": info.get("totalRevenue"),
        "revenue_growth_pct": _pct("revenueGrowth"),
        "earnings_growth_pct": _pct("earningsGrowth"),
        "profit_margin_pct": _pct("profitMargins"),
        "gross_margin_pct": _pct("grossMargins"),
        "total_cash": info.get("totalCash"),
        "total_debt": info.get("totalDebt"),
        "free_cash_flow": info.get("freeCashflow"),
        "dividend_yield_pct": _num(info.get("dividendYield")),  # yfinance already gives this in percent
        "payout_ratio_pct": _pct("payoutRatio"),
        "beta": _num(info.get("beta")),
    }

    links = {
        "income_statement": f"https://finance.yahoo.com/quote/{ticker}/financials",
        "balance_sheet": f"https://finance.yahoo.com/quote/{ticker}/balance-sheet",
        "cash_flow": f"https://finance.yahoo.com/quote/{ticker}/cash-flow",
        "sec_filings": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=10-&dateb=&owner=include&count=40",
        "company_website": info.get("website"),
    }

    stock_info = {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "currency": info.get("currency", "USD"),
        "price": price,
        "change": _num(price - prev_close) if price and prev_close else None,
        "change_pct": _num((price - prev_close) / prev_close * 100) if price and prev_close else None,
        "as_of": datetime.datetime.fromtimestamp(as_of, tz=datetime.timezone.utc).isoformat() if as_of else None,
        "market_cap": info.get("marketCap"),
        "week52_low": _num(info.get("fiftyTwoWeekLow")),
        "week52_high": _num(info.get("fiftyTwoWeekHigh")),
        "trailing_pe": _num(info.get("trailingPE"), 1),
        "forward_pe": _num(info.get("forwardPE"), 1),
        "target_mean": target,
        "target_low": _num(info.get("targetLowPrice")),
        "target_high": _num(info.get("targetHighPrice")),
        "upside_pct": _num((target - price) / price * 100, 1) if price and target else None,
        "recommendation": info.get("recommendationKey"),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "ratings": ratings,
        "next_earnings": next_earnings,
        "business_summary": (info.get("longBusinessSummary") or "")[:1200],
        "financials": financials,
        "earnings_history": earnings_history,
        "links": links,
    }
    print(f"[Consensus Agent] Got data: {stock_info['recommendation']} from {stock_info['analyst_count']} analysts")
    return {"stock_info": stock_info}


# ── Agent 3: Peers ────────────────────────────────────────────────────────────
MAX_PEERS = 4


class PeerList(BaseModel):
    tickers: List[str] = Field(description="US-listed ticker symbols of the company's closest publicly traded competitors.")


def _peer_candidates_finnhub(ticker: str) -> List[str]:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/stock/peers",
            params={"symbol": ticker},
            headers={"X-Finnhub-Token": key},  # header keeps the key out of URLs and logs
            timeout=8,
        )
        r.raise_for_status()
        return [t for t in r.json() if isinstance(t, str)]
    except Exception as e:
        print(f"[Peers Agent] Finnhub failed: {e}")
        return []


def _peer_candidates_llm(ticker: str, name: str, sector: Optional[str]) -> List[str]:
    try:
        result: PeerList = llm.with_structured_output(PeerList).invoke([HumanMessage(content=(
            f"List the 6 closest publicly traded competitors of {name} ({ticker}), sector: {sector or 'unknown'}. "
            "Prefer large, well-known US-listed companies in the same line of business. Return ticker symbols only."
        ))])
        return result.tickers
    except Exception as e:
        print(f"[Peers Agent] LLM fallback failed: {e}")
        return []


def _peer_metrics(ticker: str) -> Optional[dict]:
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return None
    if not info.get("marketCap"):
        return None
    pct = lambda v: round(v * 100, 1) if isinstance(v, (int, float)) else None
    return {
        "ticker": ticker,
        "name": info.get("shortName") or info.get("longName") or ticker,
        "market_cap": info.get("marketCap"),
        "change_1y_pct": pct(info.get("52WeekChange")),
        "trailing_pe": _num(info.get("trailingPE"), 1),
        "forward_pe": _num(info.get("forwardPE"), 1),
        "revenue_growth_pct": pct(info.get("revenueGrowth")),
        "profit_margin_pct": pct(info.get("profitMargins")),
    }


def peers_agent(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    s = state["stock_info"]
    print(f"[Peers Agent] Finding competitors for {ticker}...")

    candidates, source = _peer_candidates_finnhub(ticker), "finnhub"
    if len(candidates) <= 1:
        candidates, source = _peer_candidates_llm(ticker, s["name"], s.get("sector")), "ai"

    candidates = [t.upper().strip() for t in candidates if t.upper().strip() != ticker][: MAX_PEERS + 3]
    with concurrent.futures.ThreadPoolExecutor() as executor:
        metrics = [m for m in executor.map(_peer_metrics, candidates) if m]
    # Keep the biggest, most recognisable competitors
    metrics.sort(key=lambda m: m["market_cap"], reverse=True)
    peers = metrics[:MAX_PEERS]

    def median(key):
        values = [p[key] for p in peers if p[key] is not None and (key not in ("trailing_pe", "forward_pe") or p[key] > 0)]
        return round(statistics.median(values), 1) if values else None

    medians = {k: median(k) for k in ["forward_pe", "trailing_pe", "revenue_growth_pct", "profit_margin_pct", "change_1y_pct"]}
    print(f"[Peers Agent] {len(peers)} peers via {source}: {[p['ticker'] for p in peers]}")
    return {"peers": {"source": source, "companies": peers, "medians": medians}}


# ── Agent 4: Report ───────────────────────────────────────────────────────────
def report_agent(state: AgentState) -> AgentState:
    print(f"[Report Agent] Generating brief...")
    s = state["stock_info"]
    articles = state["articles"]

    article_list = "\n".join([
        f"[{a['id']}] ({a['published'][:10] if a['published'] else 'undated'}, {a['source']}) {a['title']}"
        + (f" — {a['summary'][:200]}" if a.get("summary") else "")
        for a in articles
    ]) or "(no articles found)"

    prompt = f"""You are a senior equity analyst writing a short, specific brief on {s['name']} ({state['ticker']}).
Today is {datetime.date.today().isoformat()}.

The reader can ALREADY SEE these numbers on screen. Use them for reasoning, but do NOT restate them:
- Price ${s['price']} ({s['change_pct']}% today), 52-week range ${s['week52_low']}–${s['week52_high']}
- Trailing P/E {s['trailing_pe']}, forward P/E {s['forward_pe']}
- Analyst consensus {s['recommendation']} from {s['analyst_count']} analysts, mean target ${s['target_mean']} (range ${s['target_low']}–${s['target_high']})
- Next earnings date: {s['next_earnings'] or 'unknown'}
- Financials: revenue growth {s['financials']['revenue_growth_pct']}% (year over year), profit margin {s['financials']['profit_margin_pct']}%, cash ${s['financials']['total_cash']}, debt ${s['financials']['total_debt']}, free cash flow ${s['financials']['free_cash_flow']}
- Competitors ({', '.join(p['ticker'] for p in state['peers']['companies']) or 'unknown'}) median: forward P/E {state['peers']['medians'].get('forward_pe')}, revenue growth {state['peers']['medians'].get('revenue_growth_pct')}%, profit margin {state['peers']['medians'].get('profit_margin_pct')}%
- Last quarters' earnings surprise vs. estimates (%): {[q['surprise_pct'] for q in s['earnings_history']] or 'unknown'}

COMPANY BUSINESS SUMMARY (for the company description only):
{s['business_summary'] or '(not available)'}

RECENT ARTICLES (cite by ID):
{article_list}

Rules:
- Developments must be concrete events (deals, earnings, guidance, lawsuits, regulation, products, management changes). Skip opinion pieces, "is it a buy?" articles and long-range price predictions.
- Every development must cite at least one article ID. Never cite an ID that is not in the list.
- Bull and bear points must be specific to this company. Do not write generic risks like "market volatility" or "economic uncertainty". Prefer points supported by the articles; if you use general background knowledge, leave source_ids empty.
- Classify the sentiment of every article towards the stock.
- Write for a beginner investor: short sentences, plain English. If a finance or industry term is unavoidable, explain it in a few words in parentheses, e.g. "hyperscalers (the big cloud companies like Microsoft and Amazon)".
- Be concise. No filler, no introductions."""

    structured_llm = llm.with_structured_output(Brief)
    brief: Brief = structured_llm.invoke([HumanMessage(content=prompt)])

    # Drop citations to articles that don't exist
    valid_ids = {a["id"] for a in articles}
    for item in brief.developments + brief.bull_case + brief.bear_case:
        item.source_ids = [i for i in item.source_ids if i in valid_ids][:3]  # a few citations read better than a wall of them
    brief.developments = [d for d in brief.developments if d.source_ids]

    # Count sentiment ourselves rather than trusting the model's arithmetic
    labels = {a.id: a.sentiment for a in brief.article_sentiment if a.id in valid_ids}
    for a in articles:
        a["sentiment"] = labels.get(a["id"], "neutral")
    counts = {k: sum(1 for a in articles if a["sentiment"] == k) for k in ["positive", "negative", "neutral"]}

    print(f"[Report Agent] Brief generated")
    return {"brief": {
        "company_description": brief.company_description,
        "tldr": brief.tldr,
        "developments": [d.model_dump() for d in brief.developments],
        "bull_case": [p.model_dump() for p in brief.bull_case],
        "bear_case": [p.model_dump() for p in brief.bear_case],
        "watch": brief.watch,
        "sentiment": {**counts, "summary": brief.sentiment_summary},
    }}


# ── Run pipeline ──────────────────────────────────────────────────────────────
def run_pipeline(ticker: str, on_step: Optional[Callable[[str, str, str], None]] = None) -> dict:
    ticker = ticker.upper().strip()
    print(f"\n{'='*50}")
    print(f"AlphaSignal Pipeline — {ticker}")
    print(f"{'='*50}")

    initial_state: AgentState = {
        "ticker": ticker,
        "articles": [],
        "stock_info": {},
        "peers": {},
        "brief": {},
    }

    # Progress updates for the UI: step id, status ("running" / "done"), detail text
    step = on_step or (lambda *args: None)

    # Consensus first: the scraper needs the company name to search the news
    step("market", "running", "")
    consensus_result = consensus_agent(initial_state)
    stock_info = consensus_result["stock_info"]
    if stock_info["price"] is None:
        raise ValueError(f"No market data found for {ticker}")
    step("market", "done", stock_info["name"])

    # News and competitors don't depend on each other, so fetch them in parallel
    def run_news():
        step("news", "running", "")
        result = scraper_agent({**initial_state, "stock_info": stock_info})
        publishers = {a["source"] for a in result["articles"]}
        step("news", "done", f"{len(result['articles'])} articles from {len(publishers)} publishers")
        return result

    def run_peers():
        step("peers", "running", "")
        result = peers_agent({**initial_state, "stock_info": stock_info})
        step("peers", "done", ", ".join(p["ticker"] for p in result["peers"]["companies"]))
        return result

    with concurrent.futures.ThreadPoolExecutor() as executor:
        news_future, peers_future = executor.submit(run_news), executor.submit(run_peers)
        scraper_result, peers_result = news_future.result(), peers_future.result()

    print("[Orchestrator] Data collected — running report agent")

    merged_state: AgentState = {
        "ticker": ticker,
        "articles": scraper_result["articles"],
        "stock_info": stock_info,
        "peers": peers_result["peers"],
        "brief": {},
    }

    step("brief", "running", "")
    report_result = report_agent(merged_state)
    step("brief", "done", "")

    return {
        "ticker": ticker,
        "stock": stock_info,
        "brief": report_result["brief"],
        "peers": peers_result["peers"],
        "articles": merged_state["articles"],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json
    result = run_pipeline("NVDA")
    print("\n" + "="*50)
    print(json.dumps(result["brief"], indent=2))
