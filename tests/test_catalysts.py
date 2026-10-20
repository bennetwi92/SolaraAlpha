from __future__ import annotations

import pandas as pd
import pytest

from solaraalpha import catalysts
from solaraalpha.data.announcements import COLUMNS, Issuer
from solaraalpha.data.news import COLUMNS as NEWS_COLUMNS

NOW = pd.Timestamp("2026-10-18 12:00", tz="UTC")
SINCE = NOW - pd.Timedelta(days=7)


def _ann(*rows: tuple[str, str, str, str]) -> pd.DataFrame:
    """Filings as (published, type_code, category, headline)."""
    df = pd.DataFrame(
        [
            (pub, code, "", cat, head, "CO PLC", "RNS", f"id{i}", f"u{i}")
            for i, (pub, code, cat, head) in enumerate(rows)
        ],
        columns=COLUMNS,
    )
    df["published"] = pd.to_datetime(df["published"], utc=True)
    return df


# Half-year results each mid-November for three years: the next is expected ~2026-11-18.
HISTORY = [
    ("2023-11-17 07:00", "IR", "Financial Results", "Half-year Report"),
    ("2024-11-19 07:00", "IR", "Financial Results", "Half-year Report"),
    ("2025-11-18 07:00", "IR", "Financial Results", "Half-year Report"),
]


def _news(*rows: tuple[str, str]) -> pd.DataFrame:
    df = pd.DataFrame([(p, "Src", h, "u") for p, h in rows], columns=NEWS_COLUMNS)
    df["published"] = pd.to_datetime(df["published"], utc=True)
    return df


def test_filing_parts_counts_material_and_shows_the_best_tier() -> None:
    ann = _ann(
        ("2026-10-01 07:00", "TST", "Additional regulated information", "Old update"),
        ("2026-10-14 07:00", "TST", "Additional regulated information", "Trading Update"),
        ("2026-10-15 07:00", "HOL", "Holdings", "Holding(s) in Company"),
        ("2026-10-16 07:00", "TVR", "Holdings", "Total Voting Rights"),  # routine, dropped
    )
    assert catalysts.filing_parts(ann, SINCE, NOW) == [
        (1, "RNS 2026-10-14: Trading Update (+1 more)")  # a TR-1 doesn't hide it
    ]


def test_filing_parts_negative_is_its_own_part() -> None:
    ann = _ann(
        ("2026-10-13 07:00", "CNT", "Operational updates", "Contract Win"),
        ("2026-10-14 16:40", "IOE", "Corporate actions", "Proposed Placing and Subscription"),
        ("2026-10-15 07:00", "ROI", "Corporate actions", "Result of Placing"),
    )
    assert catalysts.filing_parts(ann, SINCE, NOW) == [
        (1, "RNS 2026-10-13: Contract Win"),
        (4, "RNS 2026-10-15: Result of Placing"),
    ]


def test_classify_and_tier_of() -> None:
    assert catalysts.classify("TST", "Trading Update") == 1
    assert catalysts.classify("DSH", "Director/PDMR Shareholding") == 3  # mostly grants
    assert catalysts.classify("HOL", "Holding(s) in Company") == 3
    assert catalysts.classify("MSCL", "Fundraise of £7.6m") == 4
    assert catalysts.classify("APP", "Change of Nominated Adviser") == 4
    assert catalysts.classify("NOR", "Delay to Final Results") == 4
    assert catalysts.classify("SUS", "Temporary Suspension") == 4
    assert catalysts.tier_of("tier 2: event due now") == 2
    assert catalysts.tier_of("negative: warning: RNS x") == 4
    assert catalysts.tier_of("untagged") == 3
    assert catalysts.join_parts([(4, "a"), (3, "b"), (1, "c")]) == "tier 1: b; c; warning: a"
    assert catalysts.join_parts([(4, "a")]) == "negative: warning: a"
    assert catalysts.join_parts([]) is None


def test_filing_parts_holdings_only_and_none() -> None:
    hol = _ann(("2026-10-15 07:00", "HOL", "Holdings", "Holding(s) in Company"))
    assert catalysts.filing_parts(hol, SINCE, NOW) == [(3, "RNS 2026-10-15: Holding(s) in Company")]
    old = _ann(("2026-10-01 07:00", "TST", "Additional regulated information", "Old"))
    assert catalysts.filing_parts(old, SINCE, NOW) == []
    tvr = _ann(("2026-10-16 07:00", "TVR", "Holdings", "Total Voting Rights"))
    assert catalysts.filing_parts(tvr, SINCE, NOW) == []
    assert catalysts.filing_parts(_ann(), SINCE, NOW) == []


def test_filing_parts_ignores_filings_after_now() -> None:
    ann = _ann(("2026-10-19 07:00", "TST", "Additional regulated information", "Future"))
    assert catalysts.filing_parts(ann, SINCE, NOW) == []


def test_event_parts_within_horizon_only() -> None:
    ann = _ann(*HISTORY)
    assert catalysts.event_parts(ann, NOW, horizon_weeks=10) == [
        (2, "event expected ~2026-11-18 (last: Half-year Report)")  # ~4 weeks out: scheduled
    ]
    soon = pd.Timestamp("2026-11-10 12:00", tz="UTC")  # ~1 week out: discovery tier
    assert catalysts.event_parts(ann, soon, horizon_weeks=10)[0][0] == 3
    assert catalysts.event_parts(ann, NOW, horizon_weeks=2) == []
    assert catalysts.event_parts(_ann(), NOW, horizon_weeks=10) == []


def test_event_parts_overdue_is_due_now() -> None:
    late = pd.Timestamp("2026-11-30 12:00", tz="UTC")
    assert catalysts.event_parts(_ann(*HISTORY), late, horizon_weeks=10) == [
        (2, "event due now (last: Half-year Report)")
    ]


def test_news_parts() -> None:
    news = _news(("2026-10-05 08:00", "Old"), ("2026-10-12 08:00", "A"), ("2026-10-16 08:00", "B"))
    assert catalysts.news_parts(news, SINCE) == [(3, "2 headlines, latest 2026-10-16: B")]
    assert catalysts.news_parts(news.iloc[[0]], SINCE) == []


@pytest.fixture
def fake_sources(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """AAA: a new filing; BBB: results due; CCC: nothing; DDD: the issuer lookup fails."""
    calls: dict[str, list] = {"ann": [], "news": []}
    filings = {
        "LEI_AAA": _ann(("2026-10-15 07:00", "TST", "Additional regulated information", "TU")),
        "LEI_BBB": _ann(*HISTORY),
        "LEI_CCC": _ann(("2026-09-01 07:00", "MSCL", "Miscellaneous", "Old")),
    }

    def issuer(ticker: str) -> Issuer:
        if ticker == "DDD.L":
            raise RuntimeError("LSE down")
        tidm = ticker.removesuffix(".L")
        return Issuer(ticker, "", f"LEI_{tidm}", f"{tidm} PLC", "AIM")

    def announcements(lei: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        calls["ann"].append((lei, end))
        return filings[lei]

    def news(query: str, days: int = 7) -> pd.DataFrame:
        calls["news"].append((query, days))
        if query == '"DDD"':
            return _news(("2026-10-17 09:00", "DDD wins contract"))
        return _news()

    monkeypatch.setattr(catalysts, "resolve_issuer", issuer)
    monkeypatch.setattr(catalysts, "fetch_announcements", announcements)
    monkeypatch.setattr(catalysts, "fetch_news", news)
    return calls


def test_fresh_catalysts(fake_sources: dict[str, list]) -> None:
    out = catalysts.fresh_catalysts(["AAA.L", "BBB.L", "CCC.L", "DDD.L"], now=NOW)
    assert out == {
        "AAA.L": "tier 1: RNS 2026-10-15: TU",
        "BBB.L": "tier 2: event expected ~2026-11-18 (last: Half-year Report)",
        "DDD.L": "tier 3: 1 headline, latest 2026-10-17: DDD wins contract",  # filings failed
    }
    assert list(out) == ["AAA.L", "BBB.L", "DDD.L"]  # input order
    # one NSM search per resolved stock, history up to now; news by short name over the window
    assert [lei for lei, _ in fake_sources["ann"]] == ["LEI_AAA", "LEI_BBB", "LEI_CCC"]
    assert fake_sources["ann"][0][1] == "2026-10-18T12:00:00"
    assert fake_sources["news"][0] == ('"AAA"', 7)
    assert fake_sources["news"][-1] == ('"DDD"', 7)  # falls back to the TIDM


def test_fresh_catalysts_since_horizon_and_no_news(fake_sources: dict[str, list]) -> None:
    out = catalysts.fresh_catalysts(
        ["AAA.L", "BBB.L", "DDD.L"],
        since=pd.Timestamp("2026-10-16"),  # naive = UTC
        horizon_weeks=2,
        now=NOW,
        news=False,
    )
    assert out == {}
    assert fake_sources["news"] == []


def test_fresh_catalysts_news_failure_is_skipped(
    fake_sources: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(query: str, days: int = 7) -> pd.DataFrame:
        raise ValueError("bad feed")

    monkeypatch.setattr(catalysts, "fetch_news", broken)
    out = catalysts.fresh_catalysts(["AAA.L", "DDD.L"], now=NOW)
    assert out == {"AAA.L": "tier 1: RNS 2026-10-15: TU"}


@pytest.mark.network
def test_fresh_catalysts_live() -> None:
    out = catalysts.fresh_catalysts(
        ["JET2.L"], since=pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=60)
    )
    assert set(out) <= {"JET2.L"}
    assert all(isinstance(v, str) and v for v in out.values())
