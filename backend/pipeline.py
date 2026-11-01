import math
import os
import re
import time
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
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from agents_client import ensure_agents_running, run_agent
from pydantic import BaseModel, Field
from typing import Callable, List, Literal, Optional, TypedDict
import concurrent.futures

load_dotenv()

# GPT-5 models only support the default temperature; low reasoning effort keeps it fast
llm = ChatOpenAI(model="gpt-5.5", reasoning_effort="low")


# ── Shared state ──────────────────────────────────────────────────────────────
class AgentState(TypedDict, total=False):
    """Shared state that flows through the agent graph. Each agent fills in its own key."""
    ticker: str        # input
    stock_info: dict   # market agent
    articles: list     # news agent (the report agent adds sentiment and story groups)
    peers: dict        # peers agent
    brief: dict        # report agent


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
    # Grouping comes first so the developments below can follow it (one item per story)
    article_sentiment: List[ArticleSentiment] = Field(description="One entry per article provided.")
    story_groups: List[List[int]] = Field(description="Groups of article IDs that report the same specific news item: the same announcement, the same analyst note, the same comments by the same person. Articles that only share a theme (e.g. two different iPhone stories) are NOT the same story. Only groups of 2 or more; each ID in at most one group. When in doubt, don't group.")
    company_description: str = Field(description="One plain sentence a beginner understands: what the company does and how it makes money. No jargon.")
    tldr: str = Field(description="One or two plain sentences summing up the current situation for a beginner investor.")
    developments: List[Development] = Field(description="Up to 5 most important recent events, most impactful first. Fewer (even zero) is fine when there is little real news.")
    bull_case: List[Point] = Field(description="2-3 specific reasons the stock could go up.")
    bear_case: List[Point] = Field(description="2-3 specific risks for this company. No generic market risks.")
    watch: List[str] = Field(description="2-4 upcoming catalysts or open questions to watch, with dates when known.")
    sentiment_summary: str = Field(description="One sentence on the tone of the news, and whether it agrees with the analyst consensus.")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _num(value, digits=2):
    """Round numbers; turn missing or non-numeric values into None."""
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return None


def _person_name(raw: str) -> str:
    """SEC filings list names as 'NEWSTEAD JENNIFER' or 'FORD WILLIAM CLAY JR' -> 'Jennifer Newstead', 'William Clay Ford Jr'."""
    parts = raw.split()
    if len(parts) < 2:
        return raw.title()
    suffix = [p for p in parts[1:] if p.rstrip(".").upper() in {"JR", "SR", "II", "III", "IV"}]
    rest = [p for p in parts[1:] if p not in suffix]
    return " ".join(rest + parts[:1] + suffix).title().replace("Iii", "III").replace("Ii", "II").replace("Iv", "IV")


def _published(entry) -> Optional[str]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.datetime.fromtimestamp(calendar.timegm(parsed), tz=datetime.timezone.utc).isoformat()


def _valuation_history(stock, price: Optional[float], price_scale: float = 1.0) -> Optional[dict]:
    """Trailing P/E over the last 5 years: price on each date / sum of the 4 quarterly EPS reported before it."""
    ed = stock.get_earnings_dates(limit=32)
    if ed is None or ed.empty or not price:
        return None
    eps = ed["Reported EPS"].dropna()
    reports = sorted((idx.tz_localize(None) if idx.tzinfo else idx, float(v)) for idx, v in eps.items())
    if len(reports) < 8:
        return None

    def ttm_eps(at):
        before = [v for d, v in reports if d <= at][-4:]
        return sum(before) if len(before) == 4 else None

    # Split-adjusted but not dividend-adjusted prices, so they match reported EPS
    prices = stock.history(period="5y", interval="1mo", auto_adjust=False)["Close"].dropna()
    series, skipped = [], 0
    for idx, close in prices.items():
        at = idx.tz_localize(None) if idx.tzinfo else idx
        e = ttm_eps(at)
        close = close * price_scale
        if e is None or e <= 0 or close / e > 300:
            skipped += 1  # losses or near-zero profit make P/E meaningless
            continue
        series.append({"date": at.date().isoformat(), "pe": round(float(close) / e, 1)})
    if len(series) < max(12, 0.6 * len(prices)):
        return None

    current_eps = ttm_eps(datetime.datetime.now())
    current = round(price / current_eps, 1) if current_eps and current_eps > 0 else None
    values = sorted(p["pe"] for p in series)
    return {
        "current": current,
        "average": round(statistics.mean(values), 1),
        "low": values[0],
        "high": values[-1],
        "series": series,
        "months_skipped": skipped,
    }


# ── Agent 1: Scraper ──────────────────────────────────────────────────────────
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_ARTICLES = 20
MAX_PER_PUBLISHER = 3
GENERIC_BUSINESS_WORDS = {
    "motor", "motors", "technologies", "technology", "systems", "industries", "international", "brands",
    "entertainment", "communications", "pharmaceuticals", "energy", "financial", "platforms", "networks",
    "semiconductor", "devices", "airlines", "foods", "resources", "therapeutics", "labs", "software",
}
GENERIC_WORDS = {"bank", "first", "general", "american", "united", "international", "global", "national", "new", "the"}
QUOTE_PAGE = re.compile(r"stock price, news, quote|stock quote|price, news, quote", re.I)
# Recurring low-value formats: fund holding changes, auto-generated "facts" posts, "$1,000 invested" bait
FILLER = re.compile(
    r"|".join([
        r"\b(stock|shares|stake|position|holdings?)\b.{0,40}\b(acquired|sold|bought|purchased|boosted|trimmed|lowered|raised|increased|decreased|cut|reduced) by\b",
        r"\bis (up|down) [\d.]+% today, on\b",
        r"how long would it take \$",
        r"if you (had )?invested \$",
        r"\$[\d,]+ invest(ed|ment).{0,40}(worth|become)",
        r"(exceeds|lags|trails|outpaces|beats) (the )?market returns",
        r"some facts to (consider|know)",
        r"facts to know before betting",
        r"is a trending stock",
        r"is attracting investor attention",
        r"here is what you should know",
        r"\bstock (jumps|falls|rises|drops|gaps (up|down)|trading (up|down)) [\d.]+%.{0,10}(what's next|here's why)",
        r"\bstock (gains|falls|rises|drops|climbs|slips|jumps) [\d.]+ ?(percent|%)",
        r"\b(pre-?market|after-?hours|intraday) at (EUR|USD|GBP|SEK|NOK|DKK|CHF|\$|€)",
        r"\b(minus|plus) [\d.]+ percent\b",
        r"earnings date and reports",
        r"price history for",
        r"institutional ownership",
        r"short interest",
    ]),
    re.I,
)
PUBLISHER_NAMES = {"fool": "The Motley Fool", "thestreet": "TheStreet", "investing": "Investing.com", "barchart": "Barchart", "cnbc": "CNBC"}


def _publisher(source: str) -> str:
    """Merge variants like 'Yahoo Finance Australia' or 'fool.com' into one display name."""
    s = source.lower()
    if "yahoo" in s:
        return "Yahoo Finance"
    for key, name in PUBLISHER_NAMES.items():
        if key in s:
            return name
    return source


def _title_words(title: str, ticker: str) -> set:
    t = re.sub(r"\([^)]*\)", " ", title.lower())  # drop "(AAPL)", "(NASDAQ:AAPL)"
    t = re.sub(rf"\$?\b{re.escape(ticker.lower())}\b", " ", t)
    return set(re.findall(r"[a-z0-9]+", t))


def _short_name(name: str) -> str:
    """'NVIDIA Corporation' -> 'NVIDIA', 'Apple Inc.' -> 'Apple', 'Stora Enso Oyj' -> 'Stora Enso'."""
    short = re.sub(r"\b(corporation|corp|incorporated|inc|ltd|limited|plc|holdings?|group|company|co|n\.v|s\.a|ag|se)\b\.?", "", name, flags=re.I)
    # European legal forms, only at the end so names like "AB InBev" survive
    short = re.sub(r"[,\s]+(oyj|abp|ab|asa|a/s|sa/nv|aktiengesellschaft|kgaa|s\.?p\.?a\.?|b\.?v\.?)\.?$", "", short.strip(), flags=re.I)
    return re.sub(r"[,\s]+$", "", short).strip() or name


# Exchange suffix -> Google News edition (language, country) for local-language coverage
HOME_MARKETS = {
    "HE": ("fi", "FI"), "ST": ("sv", "SE"), "OL": ("no", "NO"), "CO": ("da", "DK"), "IC": ("is", "IS"),
    "DE": ("de", "DE"), "F": ("de", "DE"), "VI": ("de", "AT"), "SW": ("de", "CH"),
    "PA": ("fr", "FR"), "BR": ("fr", "BE"), "AS": ("nl", "NL"), "MI": ("it", "IT"), "MC": ("es", "ES"),
    "LS": ("pt-PT", "PT"), "WA": ("pl", "PL"), "L": ("en-GB", "GB"), "IR": ("en-IE", "IE"),
    "TO": ("en-CA", "CA"), "V": ("en-CA", "CA"), "AX": ("en-AU", "AU"), "NZ": ("en-NZ", "NZ"),
    "T": ("ja", "JP"), "HK": ("zh-HK", "HK"), "KS": ("ko", "KR"), "SI": ("en-SG", "SG"), "NS": ("en-IN", "IN"),
}


def _google_news_url(query: str, lang: str = "en-US", country: str = "US") -> str:
    ceid_lang = lang.split("-")[0] if lang not in ("pt-PT", "zh-HK") else lang
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={lang}&gl={country}&ceid={country}:{ceid_lang}"


_fx_cache = {}


def _usd_rate(currency: Optional[str]) -> Optional[float]:
    """How many US dollars one unit of `currency` is worth. London quotes in pence (GBp)."""
    if not currency:
        return None
    if currency == "USD":
        return 1.0
    if currency == "GBp":
        rate = _usd_rate("GBP")
        return rate / 100 if rate else None
    hit = _fx_cache.get(currency)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        rate = float(yf.Ticker(f"{currency}USD=X").fast_info["last_price"])
    except Exception as e:
        print(f"[FX] No rate for {currency}: {e}")
        return None
    _fx_cache[currency] = (time.time(), rate)
    return rate


def scraper_agent(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    company = _short_name(state["stock_info"].get("name") or ticker)
    print(f"[Scraper Agent] Fetching news for {ticker} ({company})...")

    # Foreign listings ("STERV.HE"): US feeds don't know the ticker, so search by name, also in the home market
    base, _, suffix = ticker.partition(".")
    foreign = bool(suffix)
    home = HOME_MARKETS.get(suffix.upper()) if foreign else None

    # Short tickers like "F" or "GM" are too ambiguous to search or match on their own
    short_ticker = len(base) <= 2
    first_word, *rest = company.split()
    names = [company]
    # "Ford Motor" -> also match "Ford", but not "Stora Enso" -> "Stora" (which matches unrelated things)
    if rest and all(w.lower() in GENERIC_BUSINESS_WORDS for w in rest) and len(first_word) >= 4 and first_word.lower() not in GENERIC_WORDS:
        names.append(first_word)
    name_query = " OR ".join(f'"{n}"' for n in names)
    if short_ticker:
        ticker_pattern = rf"(\(|\$|NYSE:\s?|NASDAQ:\s?){re.escape(base)}\b"
        english_query = f"{name_query} stock"
    else:
        ticker_pattern = rf"\b{re.escape(base)}\b"
        english_query = f"{name_query} stock" if foreign else f"{name_query} OR {ticker} stock"

    def google_feeds(window):
        # Google News aggregates many publishers (Reuters, CNBC, WSJ, Bloomberg, FT, Barron's...)
        urls = [(_google_news_url(f"{english_query} when:{window}"), "Google News")]
        if home and home != ("en-US", "US"):
            # Local press writes about home companies by name, without "stock"
            urls.append((_google_news_url(f"{name_query} when:{window}", *home), "Google News"))
        return urls

    feeds = google_feeds("7d")
    if not foreign:
        feeds += [
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
                    # Google appends " - Publisher" to titles (publisher names can contain hyphens)
                    # and its summaries are just HTML links
                    publisher = entry.get("source", {}).get("title")
                    if publisher and title.endswith(f" - {publisher}"):
                        title = title[: -len(f" - {publisher}")]
                    else:
                        title = re.sub(r"\s+-\s+[^-]+$", "", title)
                    summary = ""
                items.append({
                    "title": title,
                    "summary": re.sub(r"<[^>]+>", "", summary)[:300],
                    "link": entry.get("link", ""),
                    "source": _publisher(source),
                    "published": _published(entry),
                })
        except Exception as e:
            print(f"[Scraper Agent] Failed {default_source}: {e}")
        return items

    def fetch_all(feed_list):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            results = executor.map(lambda f: fetch(*f), feed_list)
        return [a for items in results for a in items]

    # Keep only articles that actually mention the company
    name_pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)
    ticker_re = re.compile(ticker_pattern)

    def relevant(items):
        return [
            a for a in items
            if (name_pattern.search(a["title"] + " " + a["summary"]) or ticker_re.search(a["title"] + " " + a["summary"]))
            and not QUOTE_PAGE.search(a["title"])
            and not FILLER.search(a["title"])
        ]

    articles = relevant(fetch_all(feeds))
    if len(articles) < 8:
        # Less-covered companies: look back a month instead of a week
        print(f"[Scraper Agent] Only {len(articles)} articles in 7 days, widening to 30 days")
        articles += relevant(fetch_all(google_feeds("30d")))

    # Deduplicate by title, newest first, and cap each publisher so no single one dominates
    articles.sort(key=lambda a: a["published"] or "", reverse=True)
    seen = []
    per_publisher = {}
    unique_articles = []
    for a in articles:
        words = _title_words(a["title"], ticker)
        # Same story with a slightly different headline counts as a duplicate
        duplicate = any(len(words & w) / max(len(words | w), 1) >= 0.75 for w in seen)
        publisher = a["source"]
        if not words or duplicate or per_publisher.get(publisher, 0) >= MAX_PER_PUBLISHER:
            continue
        seen.append(words)
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

    shareholder_returns = None
    try:
        cf = stock.ttm_cashflow
        if cf is None or cf.empty:
            cf = stock.cashflow
        if cf is not None and not cf.empty:
            col = cf.iloc[:, 0]
            def _row(name):
                value = col.get(name)
                return abs(float(value)) if value is not None and value == value else 0.0
            buybacks, dividends = _row("Repurchase Of Capital Stock"), _row("Cash Dividends Paid")
            if buybacks or dividends:
                shareholder_returns = {"buybacks": buybacks, "dividends": dividends, "yield_pct": None}  # yield set below, in USD
    except Exception as e:
        print(f"[Consensus Agent] No cash flow data: {e}")

    insiders = None
    try:
        it = stock.insider_transactions
        if it is not None and not it.empty:
            cutoff = datetime.datetime.now() - datetime.timedelta(days=183)
            sold = bought = 0.0
            sales = purchases = 0
            recent = []
            for _, row in it.iterrows():
                text = str(row.get("Text") or "")
                started = row.get("Start Date")
                when = started.to_pydatetime() if hasattr(started, "to_pydatetime") else None
                if when is None or when.replace(tzinfo=None) < cutoff:
                    continue
                # Only open-market trades count; stock grants, gifts and option exercises are routine
                is_sale, is_buy = text.startswith("Sale at price"), text.startswith("Purchase at price")
                if not (is_sale or is_buy):
                    continue
                value = float(row.get("Value") or 0) if row.get("Value") == row.get("Value") else 0.0
                if is_sale:
                    sold, sales = sold + value, sales + 1
                else:
                    bought, purchases = bought + value, purchases + 1
                if len(recent) < 4:
                    recent.append({
                        "name": _person_name(str(row.get("Insider") or "")),
                        "position": str(row.get("Position") or ""),
                        "type": "sale" if is_sale else "purchase",
                        "value": value,
                        "date": when.date().isoformat(),
                    })
            insiders = {"sold": sold, "bought": bought, "sales": sales, "purchases": purchases, "recent": recent}
    except Exception as e:
        print(f"[Consensus Agent] No insider data: {e}")

    volatility_pct = None
    try:
        closes = stock.history(period="1y", interval="1d")["Close"].dropna()
        returns = (closes / closes.shift(1)).dropna().apply(math.log)
        if len(returns) > 100:
            volatility_pct = _num(float(returns.std()) * math.sqrt(252) * 100, 0)  # typical yearly swing
    except Exception as e:
        print(f"[Consensus Agent] No volatility: {e}")

    valuation_history = None
    try:
        valuation_history = _valuation_history(stock, price / 100 if price and info.get("currency") == "GBp" else price,
                                               0.01 if info.get("currency") == "GBp" else 1.0)
    except Exception as e:
        print(f"[Consensus Agent] No valuation history: {e}")

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

    # Prices and market cap are in the trading currency (London: pence); financial statements can differ
    # (Toyota trades in USD but reports in JPY)
    price_currency = info.get("currency") or "USD"
    market_cap_currency = "GBP" if price_currency == "GBp" else price_currency
    financial_currency = info.get("financialCurrency") or market_cap_currency
    market_cap_rate, financial_rate = _usd_rate(market_cap_currency), _usd_rate(financial_currency)
    market_cap_usd = info.get("marketCap") * market_cap_rate if info.get("marketCap") and market_cap_rate else None
    if shareholder_returns:
        total = shareholder_returns["buybacks"] + shareholder_returns["dividends"]
        shareholder_returns["yield_pct"] = (
            _num(total * financial_rate / market_cap_usd * 100, 1) if financial_rate and market_cap_usd else None
        )

    links = {
        "income_statement": f"https://finance.yahoo.com/quote/{ticker}/financials",
        "balance_sheet": f"https://finance.yahoo.com/quote/{ticker}/balance-sheet",
        "cash_flow": f"https://finance.yahoo.com/quote/{ticker}/cash-flow",
        # Only US listings (including foreign companies' US ADRs) file with the SEC
        "sec_filings": None if "." in ticker else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=10-&dateb=&owner=include&count=40",
        "company_website": info.get("website"),
    }

    stock_info = {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "currency": price_currency,
        "financial_currency": financial_currency,
        "market_cap_usd": market_cap_usd,
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
        "shareholder_returns": shareholder_returns,
        "insiders": insiders,
        "valuation_history": valuation_history,
        "volatility_pct": volatility_pct,
        "listed_in_us": "." not in ticker,
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


def _peer_candidates_llm(ticker: str, name: str, sector: Optional[str], description: str, suggested: List[str]) -> List[str]:
    if "." in ticker:
        # A European company's real rivals are often European and not US-listed (Fortum -> RWE, Verbund, not Vistra)
        peer_listing_rule = (
            "Pick the true competitors wherever they are listed. Use Yahoo Finance tickers for their main listing, "
            "with the exchange suffix (e.g. RWE.DE, VER.VI, ORSTED.CO, UPM.HE, VOLV-B.ST), or the plain ticker for US companies. "
        )
    else:
        peer_listing_rule = "They must trade in the US: use the US ticker, or the US ADR ticker for foreign companies (e.g. TM for Toyota). "
    review = (
        f"An industry database suggests these peers: {', '.join(suggested)}. Databases often group companies by "
        "a broad industry code, so some may not really compete. Keep the ones that do, drop the ones that don't, "
        "and add obvious major competitors that are missing.\n"
        if suggested else ""
    )
    try:
        result: PeerList = llm.with_structured_output(PeerList).invoke([HumanMessage(content=(
            f"Company: {name} ({ticker}), sector: {sector or 'unknown'}.\n"
            f"What it does: {description[:600] or 'unknown'}\n\n"
            f"{review}"
            "Return the 6 closest direct competitors that an investor would compare it with, most relevant first. "
            f"{peer_listing_rule}"
            "Return ticker symbols only."
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
    currency = info.get("currency") or "USD"
    rate = _usd_rate("GBP" if currency == "GBp" else currency)
    return {
        "ticker": ticker,
        "market_cap_usd": info["marketCap"] * rate if rate else None,
        "name": info.get("longName") or info.get("shortName") or ticker,
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

    # Finnhub groups by broad industry code (Apple -> Dell, Western Digital), so the AI reviews its list
    finnhub = [t for t in _peer_candidates_finnhub(ticker) if t.upper() != ticker]
    reviewed = _peer_candidates_llm(ticker, s["name"], s.get("sector"), s.get("business_summary", ""), finnhub[:10])
    if reviewed:
        candidates, source = reviewed, ("finnhub+ai" if finnhub else "ai")
    else:
        candidates, source = finnhub, "finnhub"

    candidates = list(dict.fromkeys(t.upper().strip() for t in candidates if t.upper().strip() != ticker))[: MAX_PEERS + 3]
    with concurrent.futures.ThreadPoolExecutor() as executor:
        metrics = [m for m in executor.map(_peer_metrics, candidates) if m]
    # Keep the reviewer's relevance order
    # Tiny companies (e.g. HP next to Apple) distort the comparison; keep the filter only if enough peers remain
    own_size = s.get("market_cap_usd")
    big_enough = [m for m in metrics if own_size and m["market_cap_usd"] and m["market_cap_usd"] >= 0.01 * own_size]
    peers = (big_enough if len(big_enough) >= 2 else metrics)[:MAX_PEERS]

    def median(key):
        values = [p[key] for p in peers if p[key] is not None and (key not in ("trailing_pe", "forward_pe") or p[key] > 0)]
        return round(statistics.median(values), 1) if values else None

    medians = {k: median(k) for k in ["forward_pe", "trailing_pe", "revenue_growth_pct", "profit_margin_pct", "change_1y_pct"]}
    summary = _peer_summary(s, medians)
    print(f"[Peers Agent] {len(peers)} peers via {source}: {[p['ticker'] for p in peers]}")
    return {"peers": {"source": source, "companies": peers, "medians": medians, "summary": summary}}


def _peer_summary(s: dict, m: dict) -> List[str]:
    """Plain verdicts vs. competitors. The page shows these and the brief is told to reuse them, so they never disagree."""
    lines = []
    fwd, f = s.get("forward_pe"), s.get("financials", {})
    if fwd is not None and fwd > 0 and m.get("forward_pe"):
        lines.append(
            "Priced cheaper than most competitors relative to expected profit." if fwd < m["forward_pe"] * 0.8
            else "Priced higher than most competitors relative to expected profit — investors expect more from it." if fwd > m["forward_pe"] * 1.2
            else "Priced roughly in line with competitors."
        )
    growth, mg = f.get("revenue_growth_pct"), m.get("revenue_growth_pct")
    if growth is not None and mg is not None:
        d = growth - mg
        lines.append("Growing sales faster than its competitors." if d > 5 else "Growing sales more slowly than its competitors." if d < -5 else "Growing sales at a similar pace to competitors.")
    margin, mm = f.get("profit_margin_pct"), m.get("profit_margin_pct")
    if margin is not None and mm is not None:
        d = margin - mm
        lines.append("More profitable than most competitors." if d > 5 else "Less profitable than most competitors." if d < -5 else "About as profitable as its competitors.")
    return lines


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
- Verdicts vs. competitors shown on the page (if you compare with competitors, use exactly these conclusions): {' '.join(state['peers'].get('summary', [])) or 'none'}
- Valuation vs. its own history: {(f"trailing P/E {s['valuation_history']['current']} now vs. 5-year average {s['valuation_history']['average']} (range {s['valuation_history']['low']}–{s['valuation_history']['high']})") if s.get('valuation_history') else 'unknown'}
- Returned to shareholders last 12 months: {s['shareholder_returns'] or 'unknown'}
- Insider open-market trades, last 6 months: {({k: s['insiders'][k] for k in ('sold', 'bought', 'sales', 'purchases')} if s['insiders'] else 'unknown')}
- Last quarters' earnings surprise vs. estimates (%): {[q['surprise_pct'] for q in s['earnings_history']] or 'unknown'}

COMPANY BUSINESS SUMMARY (for the company description only):
{s['business_summary'] or '(not available)'}

RECENT ARTICLES (cite by ID):
{article_list}

Rules:
- Developments must be concrete events (deals, earnings, guidance, lawsuits, regulation, products, management changes). Skip opinion pieces, "is it a buy?" articles, long-range price predictions and routine daily price moves (e.g. "shares indicated lower pre-market"). Combine several analyst target changes into at most one item. One item per story: everything about the same deal or event (e.g. a takeover's acceptance level and its offer extension) goes into a single item citing all relevant IDs. Never pad the list: if there are few real events, list fewer and say in the TL;DR that recent news is thin.
- Articles may be in other languages. Always write in English.
- Keep facts exact, but write natural English sentences rather than copying awkward headline phrasing (e.g. "a Fortum subsidiary extended the offer", not "Fortum extended the offer through a unit").
- Write dates like "Oct 28", never "2026-10-28".
- Amounts are in {s['currency']} (prices) and {s['financial_currency']} (financials). Use the right currency, never assume dollars.
- Every development must cite at least one article ID. Never cite an ID that is not in the list.
- Bull and bear points must be specific to this company. Do not write generic risks like "market volatility" or "economic uncertainty". Prefer points supported by the articles; if a point comes from the financial data above or general background knowledge, leave source_ids empty.
- Facts in the articles beat your background knowledge, which may be out of date. Copy names, job titles, numbers and dates exactly as the articles state them; never "correct" an article from memory. If you are unsure what an event is, say so plainly instead of writing around it.
- Classify the sentiment of every article towards the stock, and group articles that report the same story.
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

    # Same-story groups: the newest article (lowest ID) leads, the others are listed under it
    grouped = set()
    for group in brief.story_groups:
        ids = sorted(i for i in dict.fromkeys(group) if i in valid_ids and i not in grouped)
        if len(ids) < 2:
            continue
        grouped.update(ids)
        for a in articles:
            if a["id"] in ids[1:]:
                a["same_story_as"] = ids[0]

    print(f"[Report Agent] Brief generated")
    return {"articles": articles, "brief": {
        "company_description": brief.company_description,
        "tldr": brief.tldr,
        "developments": [d.model_dump() for d in brief.developments],
        "bull_case": [p.model_dump() for p in brief.bull_case],
        "bear_case": [p.model_dump() for p in brief.bear_case],
        "watch": brief.watch,
        "sentiment": {**counts, "summary": brief.sentiment_summary},
    }}


# ── Agent graph (LangGraph) ───────────────────────────────────────────────────
#
#            ┌─> news ──┐
#   market ──┤          ├──> report
#            └─> peers ─┘
#
# LangGraph orchestrates; each agent runs in its own worker container, reached over a
# Redis queue (see agents_client.py and tasks.py). The market agent runs first because
# the others need the company name. News and peers don't depend on each other, so they
# run in parallel; the report agent waits for both. Each node retries on network and API
# errors, and on timeouts, so a job lost with a dead worker goes to another worker.

def _progress(config: RunnableConfig, step: str, status: str, detail: str = "") -> None:
    """Report progress to the UI ("running" / "done"), if the caller asked for it."""
    on_step = (config or {}).get("configurable", {}).get("on_step")
    if on_step:
        on_step(step, status, detail)


def _on(worker: str) -> str:
    return f"worker {worker[:6]}"


def market_node(state: AgentState, config: RunnableConfig) -> AgentState:
    _progress(config, "market", "running")
    result, worker = run_agent("market", state)
    if result["stock_info"]["price"] is None:
        # ValueError is not retried: the ticker simply doesn't exist
        raise ValueError(f"No market data found for {state['ticker']}")
    _progress(config, "market", "done", f"{result['stock_info']['name']} · {_on(worker)}")
    return result


def news_node(state: AgentState, config: RunnableConfig) -> AgentState:
    _progress(config, "news", "running")
    result, worker = run_agent("news", state)
    publishers = {a["source"] for a in result["articles"]}
    _progress(config, "news", "done", f"{len(result['articles'])} articles from {len(publishers)} publishers · {_on(worker)}")
    return result


def peers_node(state: AgentState, config: RunnableConfig) -> AgentState:
    _progress(config, "peers", "running")
    result, worker = run_agent("peers", state)
    tickers = ", ".join(p["ticker"] for p in result["peers"]["companies"])
    _progress(config, "peers", "done", f"{tickers} · {_on(worker)}")
    return result


def report_node(state: AgentState, config: RunnableConfig) -> AgentState:
    _progress(config, "brief", "running")
    result, worker = run_agent("report", state)
    _progress(config, "brief", "done", _on(worker))
    return result


# Network hiccups (Yahoo, news feeds, OpenAI) are retried with backoff: 1s, then 2s
NETWORK_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("market", market_node, retry_policy=NETWORK_RETRY)
    graph.add_node("news", news_node, retry_policy=NETWORK_RETRY)
    graph.add_node("peers", peers_node, retry_policy=NETWORK_RETRY)
    graph.add_node("report", report_node, retry_policy=NETWORK_RETRY)

    graph.add_edge(START, "market")
    graph.add_edge("market", "news")      # fan out: news and peers run in parallel
    graph.add_edge("market", "peers")
    graph.add_edge(["news", "peers"], "report")  # fan in: report waits for both
    graph.add_edge("report", END)
    return graph.compile()


agent_graph = build_agent_graph()


# ── Run pipeline ──────────────────────────────────────────────────────────────
def run_pipeline(ticker: str, on_step: Optional[Callable[[str, str, str], None]] = None) -> dict:
    """Run the agent graph for one ticker. on_step(step, status, detail) receives progress updates."""
    ticker = ticker.upper().strip()
    print(f"\n{'='*50}")
    print(f"AlphaSignal Pipeline — {ticker}")
    print(f"{'='*50}")

    ensure_agents_running()
    state = agent_graph.invoke({"ticker": ticker}, config={"configurable": {"on_step": on_step}})

    return {
        "ticker": ticker,
        "stock": state["stock_info"],
        "brief": state["brief"],
        "peers": state["peers"],
        "articles": state["articles"],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json
    import sys
    if "--graph" in sys.argv:
        # Print the agent graph as a Mermaid diagram (paste into mermaid.live or a GitHub README)
        print(agent_graph.get_graph().draw_mermaid())
    else:
        result = run_pipeline(sys.argv[1] if len(sys.argv) > 1 else "NVDA")
        print("\n" + "="*50)
        print(json.dumps(result["brief"], indent=2))
