"""Morning email: each holding's price move and new headlines, with a short AI summary on top.

Sent through Resend (RESEND_API_KEY in .env). A background thread in the backend sends
the emails that are due; it only runs while the backend is running.
"""
import concurrent.futures
import datetime
import html
import os
import threading
import time
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

import db
import portfolio as pf
from agents_client import ensure_agents_running, run_agent
from pipeline import llm

RESEND_URL = "https://api.resend.com/emails"
# Without a verified domain, Resend only delivers from this sender to your own signup address
DIGEST_FROM = os.getenv("DIGEST_FROM", "AlphaSignal <onboarding@resend.dev>")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
HEADLINES_PER_STOCK = 3
CATCH_UP_HOURS = 6   # if the backend wasn't running at send time, still send within this window
RETRY_MINUTES = 30   # wait this long before retrying a failed send


class DigestSummary(BaseModel):
    overview: str = Field(description="Two or three plain-English sentences on what matters most for this portfolio today.")
    highlights: List[str] = Field(description="At most one short bullet per stock, each starting with the ticker, e.g. 'NVDA: ...'. Only stocks with something notable; empty if nothing is.")


# ── Building the email ──
def _default_since() -> datetime.datetime:
    """Without a previous email: the last day, or since Friday on a Monday."""
    now = datetime.datetime.now(datetime.timezone.utc)
    monday = now.astimezone(ZoneInfo("Europe/Helsinki")).weekday() == 0
    return now - datetime.timedelta(hours=72 if monday else 24)



def _news_since(holding: dict, since: datetime.datetime) -> Optional[List[dict]]:
    """New articles for one holding, fetched by a news worker. None if that failed."""
    try:
        output, _ = run_agent("news", {"ticker": holding["ticker"], "stock_info": {"name": holding["name"]}})
        articles = output["articles"]
    except Exception as e:
        print(f"[Digest] News failed for {holding['ticker']}: {e}")
        return None
    fresh = [a for a in articles if a.get("published") and datetime.datetime.fromisoformat(a["published"]) >= since]
    return fresh[:HEADLINES_PER_STOCK]  # already newest first


def _summarise(name: str, holdings: List[dict]) -> DigestSummary:
    if not any(h["news"] for h in holdings):
        return DigestSummary(overview="No new headlines for your holdings since the last email.", highlights=[])
    lines = []
    for h in holdings:
        c = h["day_change_pct"]
        move = "price unavailable" if c is None else "unchanged today" if round(c, 1) == 0 else f"{'up' if c > 0 else 'down'} {abs(c):.1f}% today"
        weight = f"{h['weight_pct']:.0f}% of portfolio" if h["weight_pct"] is not None else ""
        lines.append(f"{h['ticker']} ({h['name']}), {move}, {weight}")
        lines += [f"  - {a['title']} ({a['source']})" for a in h["news"]]
    prompt = f"""You write a short morning email for a beginner investor about their portfolio "{name}".

HOLDINGS, TODAY'S MOVES AND NEW HEADLINES:
{chr(10).join(lines)}

Rules:
- Use only the information above. Copy names and numbers exactly; never add facts from memory.
- Focus on what is most important for this portfolio, weighting bigger holdings more.
- Plain English, short sentences. Explain any jargon in a few words.
- At most one highlight per stock, and only when its headlines or move are worth knowing. Don't repeat the overview.
- Describe, don't advise: never tell the reader to buy, sell or hold."""
    try:
        return llm.with_structured_output(DigestSummary).invoke([HumanMessage(content=prompt)])
    except Exception as e:
        print(f"[Digest] Summary failed: {e}")
        return DigestSummary(overview="The AI summary isn't available today; the headlines are below.", highlights=[])


def build_digest(portfolio_id: int, since: Optional[datetime.datetime] = None) -> Optional[dict]:
    """Subject, HTML and plain-text versions of the email. None if the portfolio doesn't exist."""
    data = pf.compute_portfolio(portfolio_id)
    if data is None:
        return None
    since = since or _default_since()

    holdings = sorted(data["holdings"], key=lambda h: h["weight_pct"] or 0, reverse=True)
    ensure_agents_running(["news"])
    # One queue job per holding; however many news workers are running share them
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        news = list(executor.map(lambda h: _news_since(h, since), holdings))
    if holdings and all(n is None for n in news):
        # Don't email "no new headlines" when the truth is that the fetch failed
        raise RuntimeError("Couldn't fetch news for any holding.")
    for h, n in zip(holdings, news):
        h["news"] = n or []
    summary = _summarise(data["name"], holdings)

    t = data["totals"]
    headline_count = sum(len(h["news"]) for h in holdings)
    day = f"{t['day_change_pct']:+.1f}%" if t["day_change_pct"] is not None else "no price change"
    subject = f"{data['name']}: {day} · {headline_count} new headline{'s' if headline_count != 1 else ''}"
    app_link = f"{FRONTEND_URL}/?view=portfolio&p={portfolio_id}"
    return {
        "subject": subject,
        "html": _html(data, holdings, summary, app_link),
        "text": _text(data, holdings, summary, app_link),
        "headline_count": headline_count,
    }


def _money(n: Optional[float]) -> str:
    return "—" if n is None else f"€{n:,.2f}"


def _signed_money(n: float) -> str:
    return ("+" if n > 0 else "−" if n < 0 else "") + _money(abs(n))


def _pct(n: Optional[float]) -> str:
    return "" if n is None else f"{n:+.1f}%"


def _colour(n: Optional[float]) -> str:
    return "#16a34a" if n and n > 0 else "#dc2626" if n and n < 0 else "#6b7280"


def _when(iso: str) -> str:
    return datetime.datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Helsinki")).strftime("%b %-d, %H:%M")


def _html(data: dict, holdings: List[dict], summary: DigestSummary, app_link: str) -> str:
    e = html.escape
    t = data["totals"]
    highlights = "".join(f"<li style='margin:0 0 6px'>{e(b)}</li>" for b in summary.highlights)
    rows = []
    for h in holdings:
        news = "".join(
            f"<li style='margin:0 0 6px'><a href='{e(a['link'])}' style='color:#2563eb;text-decoration:none'>{e(a['title'])}</a>"
            f"<br><span style='color:#9ca3af;font-size:12px'>{e(a['source'])} · {_when(a['published'])}</span></li>"
            for a in h["news"]
        ) or "<li style='color:#9ca3af'>No new headlines</li>"
        rows.append(f"""
        <tr><td style="padding:16px 0;border-top:1px solid #e5e7eb">
          <div style="font-size:15px"><strong>{e(h['ticker'])}</strong> <span style="color:#6b7280">{e(h['name'])}</span></div>
          <div style="font-size:13px;margin:2px 0 8px">
            <span style="color:{_colour(h['day_change_pct'])}">{_pct(h['day_change_pct']) or 'no price'} today</span>
            <span style="color:#9ca3af"> · {_money(h['value'])} · {h['weight_pct'] or 0:.0f}% of portfolio</span>
          </div>
          <ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.45">{news}</ul>
        </td></tr>""")
    return f"""<!doctype html>
<html><body style="margin:0;background:#f9fafb;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px">
<tr><td style="padding:24px 24px 8px">
  <div style="font-size:13px;color:#6b7280">⚡ AlphaSignal morning brief · {datetime.date.today().strftime('%A %b %-d')}</div>
  <h1 style="font-size:22px;margin:6px 0 4px">{e(data['name'])}</h1>
  <div style="font-size:15px">{_money(t['value'])}
    <span style="color:{_colour(t['day_change'])}">&nbsp;{_signed_money(t['day_change'])} ({_pct(t['day_change_pct'])}) today</span></div>
</td></tr>
<tr><td style="padding:12px 24px">
  <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px">
    <div style="font-size:11px;font-weight:700;letter-spacing:.06em;color:#6b7280;text-transform:uppercase">In short</div>
    <p style="font-size:15px;line-height:1.55;margin:6px 0 0">{e(summary.overview)}</p>
    {f"<ul style='margin:10px 0 0;padding-left:18px;font-size:14px;line-height:1.5'>{highlights}</ul>" if highlights else ""}
  </div>
</td></tr>
<tr><td style="padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table></td></tr>
<tr><td style="padding:16px 24px 24px;font-size:12px;color:#9ca3af;line-height:1.5;border-top:1px solid #e5e7eb">
  <a href="{e(app_link)}" style="color:#2563eb">Open the portfolio in AlphaSignal</a><br>
  AI-generated summary of public news. May contain errors. Not financial advice.
  Turn this email off on the portfolio page in AlphaSignal.
</td></tr>
</table></td></tr></table></body></html>"""


def _text(data: dict, holdings: List[dict], summary: DigestSummary, app_link: str) -> str:
    t = data["totals"]
    out = [f"AlphaSignal morning brief: {data['name']}",
           f"{_money(t['value'])}, {_signed_money(t['day_change'])} ({_pct(t['day_change_pct'])}) today", "",
           "IN SHORT", summary.overview] + [f"- {b}" for b in summary.highlights] + [""]
    for h in holdings:
        out.append(f"{h['ticker']} ({h['name']}): {_pct(h['day_change_pct']) or 'no price'} today, {h['weight_pct'] or 0:.0f}% of portfolio")
        out += [f"  - {a['title']} ({a['source']}) {a['link']}" for a in h["news"]] or ["  - No new headlines"]
    out += ["", f"Open in AlphaSignal: {app_link}", "Not financial advice."]
    return "\n".join(out)


# ── Sending ──
class EmailNotConfigured(Exception):
    pass


def send_email(to: str, subject: str, html_body: str, text_body: str) -> None:
    key = os.getenv("RESEND_API_KEY")
    if not key:
        raise EmailNotConfigured("Email isn't set up yet: add RESEND_API_KEY to backend/.env and restart the backend.")
    r = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {key}"},  # in a header, so the key never appears in URLs or logs
        json={"from": DIGEST_FROM, "to": [to], "subject": subject, "html": html_body, "text": text_body},
        timeout=20,
    )
    if r.status_code >= 300:
        try:
            message = r.json().get("message") or r.text
        except ValueError:
            message = r.text
        raise RuntimeError(f"Resend refused the email ({r.status_code}): {message[:300]}")


def send_digest(portfolio_id: int) -> str:
    """Build and send one portfolio's email now. Returns a status message; raises on failure."""
    settings = db.get_digest(portfolio_id)
    if not settings:
        raise ValueError("Set an email address for this portfolio first.")
    now = datetime.datetime.now(datetime.timezone.utc)
    last = settings.get("last_sent_at")
    last = datetime.datetime.fromisoformat(last) if last else None
    # News since the previous email (at most three days back, e.g. over a weekend)
    since = max(last, now - datetime.timedelta(hours=72)) if last else _default_since()
    digest = build_digest(portfolio_id, since)
    if digest is None:
        raise ValueError("Portfolio not found.")
    try:
        send_email(settings["email"], digest["subject"], digest["html"], digest["text"])
    except Exception as e:
        db.record_digest_attempt(portfolio_id, f"Failed: {e}", sent=False)
        raise
    status = f"Sent to {settings['email']}: {digest['subject']}"
    db.record_digest_attempt(portfolio_id, status, sent=True)
    return status


# ── Scheduler ──
def _due(d: dict, now_utc: datetime.datetime) -> bool:
    try:
        tz = ZoneInfo(d["timezone"])
    except Exception:
        tz = ZoneInfo("Europe/Helsinki")
    local_now = now_utc.astimezone(tz)
    scheduled = datetime.datetime.combine(local_now.date(), d["send_time"], tzinfo=tz)
    if not (scheduled <= local_now <= scheduled + datetime.timedelta(hours=CATCH_UP_HOURS)):
        return False
    if d["last_sent_at"] and d["last_sent_at"].astimezone(tz).date() == local_now.date():
        return False  # already sent today
    if d["last_attempt_at"] and now_utc - d["last_attempt_at"] < datetime.timedelta(minutes=RETRY_MINUTES):
        return False  # failed recently; try again later
    return True


def _scheduler_loop() -> None:
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            for d in db.list_enabled_digests():
                if _due(d, now):
                    try:
                        print(f"[Digest] {send_digest(d['portfolio_id'])}")
                    except Exception as e:
                        print(f"[Digest] Sending '{d['portfolio_name']}' failed: {e}")
        except Exception as e:  # e.g. database not running; keep the loop alive
            print(f"[Digest] Scheduler check failed: {e}")
        time.sleep(60)


_started = False


def start_scheduler() -> None:
    global _started
    if _started or os.getenv("DIGEST_SCHEDULER", "on").lower() == "off":
        return
    _started = True
    threading.Thread(target=_scheduler_loop, name="digest-scheduler", daemon=True).start()
    print("[Digest] Scheduler started: checks for due morning emails every minute")
