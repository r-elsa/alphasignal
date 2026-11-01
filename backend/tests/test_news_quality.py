from news_quality import date_looks_wrong, is_blocked, is_preferred, rank_sources

EARNINGS = ["2026-08-26", "2026-05-20"]


def test_old_earnings_story_is_flagged():
    assert date_looks_wrong("Nvidia stock surges as revenue beats forecasts", "2026-10-30", EARNINGS)


def test_earnings_story_near_a_report_is_fine():
    assert not date_looks_wrong("Nvidia stock surges as revenue beats forecasts", "2026-08-27", EARNINGS)


def test_previews_and_other_news_are_fine():
    assert not date_looks_wrong("Nvidia Q3 preview: what to expect from earnings", "2026-10-30", EARNINGS)
    assert not date_looks_wrong("Indian AI firm seeks $3 billion", "2026-10-30", EARNINGS)


def test_unknown_earnings_dates_flag_nothing():
    assert not date_looks_wrong("Revenue beats forecasts", "2026-10-30", [])


def test_preferred_and_blocked_sources():
    assert all(is_preferred(s) for s in ["Bloomberg.com", "FT", "The New York Times", "E24", "di.se", "TheStreet"])
    assert not is_preferred("LiteFinance")
    assert is_blocked("Moomoo") and not is_blocked("Reuters")


def test_preferred_sources_rank_first_then_newest():
    ranked = rank_sources([
        {"source": "Unknown", "published": "2026-10-30"},
        {"source": "Reuters", "published": "2026-10-20"},
        {"source": "Bloomberg", "published": "2026-10-25"},
    ])
    assert [a["source"] for a in ranked] == ["Bloomberg", "Reuters", "Unknown"]
