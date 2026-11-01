from verification import NO_REASON, check_quote, scorecard, verify_brief

ARTICLES = [
    {"id": 1, "title": "Fortum completes Elmera takeover", "summary": "",
     "text": "The company said it had “secured 91.61 percent” of the shares and would buy the rest soon."},
    {"id": 2, "title": "Bank raises target on Fortum shares", "summary": "", "text": ""},
]


def claim(article_id, quote):
    return {"evidence": [{"article_id": article_id, "quote": quote}]}


def brief(**overrides):
    return {"developments": [], "bull_case": [], "bear_case": [], "move": None, **overrides}


def test_quote_levels():
    assert check_quote('secured 91.61 percent', ARTICLES[0]) == "article"
    assert check_quote("completes Elmera takeover", ARTICLES[0]) == "headline"
    assert check_quote("the board resigned today", ARTICLES[0]) == ""


def test_quotes_tolerate_typography_but_not_short_fragments():
    assert check_quote('"SECURED   91.61 percent".', ARTICLES[0]) == "article"
    assert check_quote("the company", ARTICLES[0]) == ""


def test_invented_quote_is_rejected_then_removed_on_final_round():
    b = brief(developments=[{"headline": "Made up", **claim(1, "the board resigned today")}])
    b, rejected, removed = verify_brief(b, ARTICLES, final=False)
    assert rejected and not removed and b["developments"]
    b, rejected, removed = verify_brief(brief(developments=[{"headline": "Made up", **claim(1, "the board resigned today")}]), ARTICLES, final=True)
    assert rejected and removed and not b["developments"]


def test_quote_from_a_missing_article_is_rejected():
    b = brief(developments=[{"headline": "X", **claim(99, "secured 91.61 percent")}])
    _, rejected, _ = verify_brief(b, ARTICLES, final=True)
    assert "not in article 99" in rejected[0]["reason"]


def test_good_claim_gets_sources_and_level():
    b = brief(developments=[{"headline": "Takeover", **claim(1, "secured 91.61 percent")}])
    b, rejected, _ = verify_brief(b, ARTICLES, final=True)
    assert not rejected
    assert b["developments"][0]["source_ids"] == [1]
    assert b["developments"][0]["evidence"][0]["level"] == "article"


def test_data_based_points_pass_but_a_failed_quote_does_not_become_one():
    b = brief(bull_case=[{"text": "Sales growth is strong", "evidence": []},
                         {"text": "Invented", **claim(1, "the board resigned today")}])
    b, _, removed = verify_brief(b, ARTICLES, final=True)
    assert [p["text"] for p in b["bull_case"]] == ["Sales growth is strong"]
    assert removed[0]["claim"] == "Invented"


def test_unsupported_move_becomes_no_clear_reason():
    b = brief(move={"change_pct": -4, "text": "It fell on fraud claims", **claim(1, "the board resigned today")})
    b, _, _ = verify_brief(b, ARTICLES, final=True)
    assert b["move"]["text"] == NO_REASON and b["move"]["source_ids"] == []


def test_scorecard_counts():
    b = brief(developments=[{"headline": "A", "evidence": [{"article_id": 1, "quote": "q", "level": "article"}]},
                            {"headline": "B", "evidence": [{"article_id": 2, "quote": "q", "level": "headline"}]}],
              bull_case=[{"text": "Data point", "evidence": []}])
    s = scorecard(b, rejected_at_first=2, removed=[{"claim": "C"}], rounds=2)
    assert (s["from_article_text"], s["headline_only"], s["not_from_news"], s["corrected"]) == (1, 1, 1, 1)
