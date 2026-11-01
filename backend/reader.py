"""Reader agent: gets each article's text where the site allows it. No AI.

It identifies itself, respects robots.txt and doesn't try to get past paywalls. Anything it
can't read stays "headline only", and the page says so.
"""
import concurrent.futures
import datetime
import hashlib
import re
import urllib.robotparser
from urllib.parse import urlparse

import requests
import trafilatura
from googlenewsdecoder import gnewsdecoder

USER_AGENT = "AlphaSignal-student-research/0.1 (university course project)"
TEXT_LIMIT = 2500       # characters per article, kept only while the agents work
MIN_WORDS = 120         # shorter than this is usually a teaser or a paywall notice
WRONG_DATE_DAYS = 3     # page date this much older than the feed's date: trust the page
OLD_STORY_DAYS = 14     # ...and this much older: it isn't recent news at all


def _robots_allow(url: str, cache: dict) -> bool:
    host = urlparse(url).netloc
    if host not in cache:
        parser = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=8)
            parser.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            parser.parse([])
        cache[host] = parser
    return cache[host].can_fetch(USER_AGENT, url)


def _real_url(link: str) -> str:
    """Google News links are redirects; find the article's own address."""
    if "news.google.com" not in link:
        return link
    try:
        decoded = gnewsdecoder(link)
        return decoded["decoded_url"] if decoded.get("status") else link
    except Exception:
        return link


def _read_one(article: dict, company: str, robots: dict) -> dict:
    article.update(read="headline", text="", date_old=False)
    url = _real_url(article.get("link", ""))
    article["link"] = url
    try:
        if "news.google.com" in url or not _robots_allow(url, robots):
            return article
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
        if r.status_code != 200:
            return article
        text = trafilatura.extract(r.text) or ""
        metadata = trafilatura.extract_metadata(r.text)
    except Exception as e:
        print(f"[Reader Agent] Couldn't read {urlparse(url).netloc}: {type(e).__name__}")
        return article

    page_date = getattr(metadata, "date", None)
    if page_date and article.get("published"):
        try:
            gap = (datetime.date.fromisoformat(article["published"][:10]) - datetime.date.fromisoformat(page_date[:10])).days
        except ValueError:
            gap = 0
        if gap > WRONG_DATE_DAYS:
            article["published"] = f"{page_date[:10]}T00:00:00+00:00"
            article["date_old"] = gap > OLD_STORY_DAYS

    # Cookie and paywall notices don't mention the company, or are too short to be an article
    if len(text.split()) >= MIN_WORDS and re.search(re.escape(company), text, re.I):
        article["text"] = re.sub(r"\s+", " ", text)[:TEXT_LIMIT]
        article["read"] = "full"
    return article


def reader_agent(state: dict) -> dict:
    from pipeline import _short_name

    articles = state["articles"]
    company = _short_name(state["stock_info"].get("name") or state["ticker"]).split()[0]
    robots = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        articles = list(executor.map(lambda a: _read_one(a, company, robots), articles))

    # The same "text" on several pages is a site notice, not an article
    seen = {}
    for a in articles:
        if a["text"]:
            seen.setdefault(hashlib.md5(a["text"][:300].encode()).hexdigest(), []).append(a)
    for same in seen.values():
        if len(same) > 1:
            for a in same:
                a.update(read="headline", text="")

    print(f"[Reader Agent] Read {sum(a['read'] == 'full' for a in articles)} of {len(articles)} articles")
    return {"articles": articles}
