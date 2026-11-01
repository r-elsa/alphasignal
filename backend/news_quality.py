"""Rules for judging news sources and dates. No network or AI, so they're easy to test."""
import datetime
import re
from typing import Iterable, Optional

# The sources the portfolio's owners already read by hand, as (publisher-name pattern, website)
PREFERRED_SOURCES = [
    (r"reuters", "reuters.com"),
    (r"bloomberg", "bloomberg.com"),
    (r"financial times|^ft(\.com)?$", "ft.com"),
    (r"seeking ?alpha", "seekingalpha.com"),
    (r"zacks", "zacks.com"),
    (r"tradingview", "tradingview.com"),
    (r"yahoo", "finance.yahoo.com"),
    (r"forbes", "forbes.com"),
    (r"new york times|nytimes|ny times", "nytimes.com"),
    (r"the ?street", "thestreet.com"),
    (r"sure ?dividend", "suredividend.com"),
    (r"^e24(\.no)?$", "e24.no"),
    (r"dagens industri|^di(\.se)?$", "di.se"),
]
PREFERRED_SITES = [site for _, site in PREFERRED_SOURCES]

# Forums and personal posts, not reporting
BLOCKED_SOURCES = re.compile(r"moomoo|reddit", re.I)

# A headline reporting results that have come out, not a preview of them
_EARNINGS_RESULT = re.compile(
    r"\b(revenue|sales|earnings|eps|profit)\b.{0,40}\b(beats?|miss(es)?|tops?|surges?|jumps?|falls?)\b"
    r"|\b(beats?|miss(es)?|tops?)\b.{0,40}\b(revenue|sales|earnings|eps|estimates?|forecasts?|expectations?)\b"
    r"|\bquarterly (results|earnings)\b|\bq[1-4]\b.{0,20}\b(results|earnings)\b",
    re.I,
)
_LOOKING_AHEAD = re.compile(r"\b(preview|ahead of|expected|expects?|will|could|what to expect|before)\b", re.I)
EARNINGS_WINDOW_DAYS = 7


def is_preferred(source: str) -> bool:
    return any(re.search(pattern, source or "", re.I) for pattern, _ in PREFERRED_SOURCES)


def is_blocked(source: str) -> bool:
    return bool(BLOCKED_SOURCES.search(source or ""))


def rank_sources(articles: list) -> list:
    """Newest first within each group, with the preferred sources ahead of everyone else."""
    newest_first = sorted(articles, key=lambda a: a.get("published") or "", reverse=True)
    return sorted(newest_first, key=lambda a: not is_preferred(a.get("source", "")))


def date_looks_wrong(title: str, published: Optional[str], earnings_dates: Iterable[str]) -> bool:
    """True if a headline reports earnings results but no report came out near the article's date.

    Catches old stories that a feed shows with a recent date. It works on wording, so it can
    miss cases, and can flag a genuine story (e.g. one about another company's results).
    """
    dates = list(earnings_dates or [])
    if not published or not dates or not _EARNINGS_RESULT.search(title) or _LOOKING_AHEAD.search(title):
        return False
    day = datetime.date.fromisoformat(published[:10])
    return all(abs((day - datetime.date.fromisoformat(d[:10])).days) > EARNINGS_WINDOW_DAYS for d in dates)
