"""Verifier agent: checks each claim's quote against what the article actually says. No AI.

Certain about one thing only: whether the quote is really there. It can't tell whether the
quote supports the claim, or whether the source is right.
"""
import re
import unicodedata

MIN_QUOTE_WORDS = 3
NO_REASON = "No clear reason found in the news."


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[‘’‚′`]", "'", text)
    text = re.sub(r"[“”„″]", '"', text)
    text = re.sub(r"[‐‑‒–—―]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def check_quote(quote: str, article: dict) -> str:
    """Where the quote appears: 'article', 'headline', or '' if it's not there."""
    q = _normalise(quote).strip(' "\'.,;:…')
    if len(q.split()) < MIN_QUOTE_WORDS:
        return ""
    if q in _normalise(article.get("text")):
        return "article"
    if q in _normalise(article.get("title")) or q in _normalise(article.get("summary")):
        return "headline"
    return ""


def _check_claim(claim: dict, by_id: dict) -> tuple:
    """The claim's quotes that hold up, and why any others failed."""
    good, problems = [], []
    for e in claim.get("evidence", []):
        article = by_id.get(e["article_id"])
        level = check_quote(e["quote"], article) if article else ""
        if level:
            good.append({**e, "level": level})
        else:
            problems.append(f'quote "{e["quote"][:80]}" is not in article {e["article_id"]}')
    return good, problems


def verify_brief(brief: dict, articles: list, final: bool) -> tuple:
    """Check every claim. Returns (brief, rejected, removed). On the final round, failures are removed."""
    by_id = {a["id"]: a for a in articles}
    rejected, removed = [], []

    def check(section: str, claim: dict, text: str, needs_evidence: bool) -> bool:
        had_evidence = bool(claim.get("evidence"))
        good, problems = _check_claim(claim, by_id)
        claim["evidence"] = good
        claim["source_ids"] = list(dict.fromkeys(e["article_id"] for e in good))[:3]
        if good or (not needs_evidence and not had_evidence):
            return True
        reason = "; ".join(problems) or "no evidence given"
        rejected.append({"section": section, "claim": text, "reason": reason})
        if final:
            removed.append({"section": section, "claim": text, "reason": reason})
        return not final

    brief["developments"] = [d for d in brief["developments"] if check("What happened", d, d["headline"], True)]
    # Bull and bear points may rest on the financial data instead of an article
    for section, label in (("bull_case", "Bull case"), ("bear_case", "Bear case")):
        brief[section] = [p for p in brief[section] if check(label, p, p["text"], False)]
    move = brief.get("move")
    if move and move["text"] != NO_REASON and not check("Today's move", move, move["text"], True) :
        move.update(text=NO_REASON, evidence=[], source_ids=[])
    return brief, rejected, removed


def scorecard(brief: dict, rejected_at_first: int, removed: list, rounds: int) -> dict:
    claims = list(brief["developments"]) + list(brief["bull_case"]) + list(brief["bear_case"])
    if brief.get("move") and brief["move"]["text"] != NO_REASON:
        claims.append(brief["move"])
    cited = [c for c in claims if c.get("evidence")]
    from_article = sum(any(e["level"] == "article" for e in c["evidence"]) for c in cited)
    return {
        "from_article_text": from_article,
        "headline_only": len(cited) - from_article,
        "not_from_news": len(claims) - len(cited),
        "corrected": max(rejected_at_first - len(removed), 0),
        "removed": removed,
        "rounds": rounds,
    }
