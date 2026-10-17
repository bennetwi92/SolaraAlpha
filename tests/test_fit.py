from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd

from solaraalpha.portfolio.fit import fit, rank_score
from solaraalpha.portfolio.plan import TradePlan
from solaraalpha.portfolio.risk import HOLDING_COLS, Equity
from solaraalpha.portfolio.rules import Rules, load_rules

DATE = pd.Timestamp("2026-10-09", tz="UTC")
SECTORS = {"A.L": "Tech", "B.L": "Tech", "C.L": "Retail", "D.L": "Retail", "H.L": "Tech"}


def rules(**over: object) -> Rules:
    return Rules(**{**load_rules().__dict__, **over})


def plan(
    ticker: str, value: float = 1000.0, risk: float = 75.0, shares: int = 50, **over: object
) -> TradePlan:
    base = TradePlan(
        date=DATE,
        ticker=ticker,
        side=1,
        entry_type="limit",
        entry=2000.0,
        wait_for_break=False,
        valid_until=DATE + pd.Timedelta(days=7),
        stop=1850.0,
        stop_source="levels",
        target=2500.0,
        target_2=math.nan,
        reward_risk=3.3,
        risk_per_share=150.0,
        shares=shares,
        position_value=value,
        risk_amount=risk,
        capped_by="risk",
        horizon_weeks=(2, 16),
        trail_atr=3.0,
        review_dates=(),
        flags=(),
    )
    return replace(base, **over)  # type: ignore[arg-type]


def holdings(*rows: tuple[str, float, float]) -> pd.DataFrame:
    df = pd.DataFrame(columns=HOLDING_COLS)
    for i, (ticker, value, risk) in enumerate(rows, 1):
        df.loc[i - 1] = {
            "position_id": i,
            "ticker": ticker,
            "value": value,
            "risk": risk,
            "sector": SECTORS[ticker],
        }
    return df


EQ = Equity(
    cash=8_000.0,
    market_value=2_000.0,
    equity=10_000.0,
    realised_pnl=0,
    unrealised_pnl=0,
    deposits=10_000.0,
    withdrawals=0,
    dividends=0,
    open_cost=2_000.0,
    positions=1,
)
EMPTY = holdings()


def test_rank_score_ignores_nan_and_missing() -> None:
    cards = {
        "technical": {"trend": 0.5},
        "levels": {"setup": math.nan},
        "relative_strength": {"rs": 0.1},
    }
    assert rank_score(cards) == 0.3
    assert math.isnan(rank_score({}))


def test_slot_exhaustion_rejects_the_lowest_ranked() -> None:
    plans = [plan("A.L"), plan("B.L"), plan("C.L")]
    scores = {"A.L": 0.2, "B.L": 0.9, "C.L": 0.5}
    held = holdings(("H.L", 2000.0, 100.0))
    rep = fit(plans, held, EMPTY, EQ, rules(max_positions=3), scores, sectors=SECTORS)
    assert rep.ranked == ["B.L", "C.L", "A.L"]
    assert [p.ticker for p in rep.accepted] == ["B.L", "C.L"]
    assert [(p.ticker, r) for p, r in rep.rejected] == [
        ("A.L", "rejected_by_fit: no free slot: 1 open + 0 pending + 2 accepted >= max_positions 3")
    ]
    # pending positions take slots too
    pending = holdings(("D.L", 1000.0, 50.0))
    rep = fit(plans, held, pending, EQ, rules(max_positions=3), scores, sectors=SECTORS)
    assert [p.ticker for p in rep.accepted] == ["B.L"]


def test_sector_and_heat_caps_reject() -> None:
    held = holdings(("H.L", 2500.0, 100.0))  # Tech already 25%
    scores = {"A.L": 0.9, "C.L": 0.5}
    rep = fit(
        [plan("A.L", value=1100.0), plan("C.L", value=1100.0)],
        held,
        EMPTY,
        EQ,
        rules(),
        scores,
        sectors=SECTORS,
    )
    assert [p.ticker for p in rep.accepted] == ["C.L"]
    assert rep.rejected[0][1] == "rejected_by_fit: sector Tech would be 36% > max_sector_pct 35%"

    rep = fit(
        [plan("A.L", risk=550.0), plan("C.L", risk=100.0)],
        held,
        EMPTY,
        EQ,
        rules(),
        scores,
        sectors=SECTORS,
    )
    assert [p.ticker for p in rep.accepted] == ["C.L"]
    assert rep.rejected[0][
        1
    ] == "rejected_by_fit: heat would be 5.0% > max_heat 6.0%" or rep.rejected[0][1].startswith(
        "rejected_by_fit: heat"
    )
    # accepted plans count towards the heat of the next
    rep = fit(
        [plan("A.L", risk=300.0), plan("C.L", risk=250.0)],
        held,
        EMPTY,
        EQ,
        rules(),
        scores,
        sectors=SECTORS,
    )
    assert [p.ticker for p in rep.accepted] == ["A.L"]
    assert rep.rejected[0][1] == "rejected_by_fit: heat would be 6.5% > max_heat 6.0%"
    # zero-size plans are rejected, not drafted
    rep = fit(
        [plan("A.L", shares=0, value=0.0, risk=0.0)],
        EMPTY,
        EMPTY,
        EQ,
        rules(),
        scores,
        sectors=SECTORS,
    )
    assert rep.rejected[0][1] == "rejected_by_fit: size zero"


def test_correlation_and_event_clash_flag_without_rejecting() -> None:
    idx = pd.date_range(end=DATE, periods=40, freq="W-FRI", tz="UTC")
    rng = np.random.default_rng(0)
    base = rng.standard_normal(40)
    returns = {
        "H.L": pd.Series(base, index=idx),
        "A.L": pd.Series(base + 0.1 * rng.standard_normal(40), index=idx),  # moves with H.L
        "C.L": pd.Series(rng.standard_normal(40), index=idx),
    }
    held = holdings(("H.L", 1000.0, 50.0))
    plans = [plan("A.L", flags=("event within 5 days", "risk_pct > 15%")), plan("C.L")]
    rep = fit(plans, held, EMPTY, EQ, rules(), {"A.L": 0.5, "C.L": 0.5}, returns, SECTORS)
    assert [p.ticker for p in rep.accepted] == ["A.L", "C.L"]  # tie broken by ticker
    assert rep.rejected == []
    assert rep.flags["A.L"][0] == "event within 5 days"
    assert rep.flags["A.L"][1].startswith("correlated with H.L (0.9")
    assert "C.L" not in rep.flags


def test_ordering_is_deterministic_with_nan_scores() -> None:
    plans = [plan("C.L"), plan("A.L"), plan("B.L")]
    rep = fit(plans, EMPTY, EMPTY, EQ, rules(), {"B.L": math.nan, "C.L": 0.1}, sectors=SECTORS)
    assert rep.ranked == ["C.L", "A.L", "B.L"]  # scored first, then unscored by ticker


def test_circuit_breaker_off_below_and_above_the_limit() -> None:
    plans = [plan("A.L"), plan("B.L")]
    scores = {"A.L": 0.5, "B.L": 0.4}
    # off: a deep drawdown changes nothing
    rep = fit(
        plans,
        EMPTY,
        EMPTY,
        EQ,
        rules(circuit_breaker=False),
        scores,
        sectors=SECTORS,
        drawdown=-0.3,
    )
    assert (
        len(rep.accepted) == 2
        and rep.rejected == []
        and rep.breaker is False
        and rep.drawdown == -0.3
    )
    # on, inside the limit: nothing
    r = rules(circuit_breaker=True, max_drawdown_new_entries=0.10)
    rep = fit(plans, EMPTY, EMPTY, EQ, r, scores, sectors=SECTORS, drawdown=-0.099)
    assert len(rep.accepted) == 2 and rep.breaker is False
    rep = fit(
        plans, EMPTY, EMPTY, EQ, r, scores, sectors=SECTORS, drawdown=None
    )  # unknown: no trip
    assert len(rep.accepted) == 2
    # on, beyond the limit: every plan rejected with the reason, open positions untouched
    rep = fit(plans, EMPTY, EMPTY, EQ, r, scores, sectors=SECTORS, drawdown=-0.12)
    assert rep.accepted == [] and rep.breaker is True
    assert [(p.ticker, why) for p, why in rep.rejected] == [
        ("A.L", "rejected_by_fit: circuit breaker: drawdown 12% > 10%"),
        ("B.L", "rejected_by_fit: circuit breaker: drawdown 12% > 10%"),
    ]
