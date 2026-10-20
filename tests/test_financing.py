from __future__ import annotations

import math

import pandas as pd

from solaraalpha.analysis.financing import change_over, financing_card, raises


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _fund(cash: float = 12.0, fcf: float = -8.0, eps: float = -0.05) -> pd.DataFrame:
    """A loss-maker: fiscal years to 31 Dec, shares 100 -> 110 -> 150 -> 165 (half-yearly)."""
    rows = [
        ("annualCashAndCashEquivalents", _ts("2025-12-31"), cash),
        ("annualCashAndCashEquivalents", _ts("2024-12-31"), 20.0),
        ("annualFreeCashFlow", _ts("2025-12-31"), fcf),
        ("annualDilutedEPS", _ts("2025-12-31"), eps),
        ("trailingDilutedAverageShares", _ts("2022-12-31"), 100.0),
        ("trailingDilutedAverageShares", _ts("2024-12-31"), 110.0),
        ("trailingDilutedAverageShares", _ts("2025-06-30"), 150.0),
        ("trailingDilutedAverageShares", _ts("2025-12-31"), 165.0),
    ]
    df = pd.DataFrame(rows, columns=["field", "date", "value"])
    df["currency"] = "GBP"
    return df.sort_values(["field", "date"], ignore_index=True)  # as parse_timeseries


def _ann(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame({"published": [_ts(d) for d, _ in rows], "headline": [h for _, h in rows]})


ANN = _ann(
    [
        ("2023-03-01", "Final Results"),
        ("2024-06-10", "Proposed Placing and Subscription"),
        ("2024-06-10", "REX Retail Offer"),
        ("2024-06-11", "Results of Oversubscribed Placing & Subscription"),
        ("2025-09-24", "Exercise of Broker Warrants and Issue of Equity"),
        ("2026-03-19", "Proposed Equity Fundraise"),
        ("2026-03-20", "Results of Placing"),
        ("2026-05-05", "Award of Options and Issue of Equity"),
    ]
)


def test_raises_group_filings_into_one_raise() -> None:
    got = raises(ANN)
    assert list(got["published"]) == [_ts("2024-06-10"), _ts("2026-03-19")]
    assert got["headline"].iloc[1] == "Proposed Equity Fundraise"


def test_change_over_needs_a_point_near_the_lag() -> None:
    s = pd.Series([100.0, 120.0], index=pd.DatetimeIndex([_ts("2025-01-01"), _ts("2026-01-01")]))
    assert math.isclose(change_over(s, 1), 0.2)
    assert math.isnan(change_over(s, 3))


def test_financing_card_loss_maker() -> None:
    c = financing_card(_fund(), ANN, _ts("2026-10-09"))
    assert c.loss_making is True
    assert math.isclose(c.runway_months, 18.0)  # 12 cash / 8 a year burn
    assert c.cash_period == _ts("2025-12-31")
    assert 8.5 < c.runway_now_months < 9.5  # ~9 months elapsed since the period end
    assert math.isclose(c.dilution_12m, 0.5)  # 165 vs 110
    assert math.isclose(c.dilution_36m, 0.65)  # 165 vs 100
    assert c.raises_3y == 2 and c.last_raise == _ts("2026-03-19")
    assert c.raised_since_period is True
    assert c.warrants_or_convertibles is True


def test_financing_card_cash_generative_has_no_runway() -> None:
    c = financing_card(_fund(fcf=5.0, eps=0.3), None, _ts("2026-10-09"))
    assert c.loss_making is False and math.isnan(c.runway_months)
    assert c.raises_3y is None and c.raised_since_period is None
    assert c.warrants_or_convertibles is None


def test_financing_card_point_in_time() -> None:
    # 2026-03-01: FY2025 not published yet (6-month lag) and the March raise not yet filed
    c = financing_card(_fund(), ANN, _ts("2026-02-27"), as_of="2026-03-01")
    assert c.cash_period == _ts("2024-12-31") and math.isnan(c.runway_months)
    assert c.raises_3y == 1 and c.last_raise == _ts("2024-06-10")
