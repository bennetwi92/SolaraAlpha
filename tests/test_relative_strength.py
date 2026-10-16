from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store, toolkits
from solaraalpha.analysis.relative_strength import (
    Comparisons,
    basket,
    compare,
    comparisons,
    in_currency,
    relative_strength,
    rs_card,
    weekly_close,
)
from solaraalpha.data.clean import to_weekly
from solaraalpha.pipeline import run_toolkit

END = "2026-10-09"


def _daily(ticker: str, drift: float, seed: int, currency: str = "GBp", n: int = 900):
    """Daily bars: a common market factor plus the stock's own drift and noise."""
    common = np.random.default_rng(0).standard_normal(n)
    own = np.random.default_rng(seed).standard_normal(n)
    close = 100 * np.exp(np.cumsum(0.0003 + drift + 0.01 * common + 0.01 * own))
    idx = pd.bdate_range(end=END, periods=n, tz="UTC", name="date")
    df = pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close}, index=idx
    )
    df["adj_close"], df["volume"] = close, 1e6
    df.attrs = {"ticker": ticker, "currency": currency}
    return df


def _fetcher(frames: dict[str, pd.DataFrame], calls: list[str] | None = None):
    def fetch(ticker: str) -> pd.DataFrame:
        if calls is not None:
            calls.append(ticker)
        if ticker not in frames:
            raise ConnectionError("no such ticker")
        return frames[ticker]

    return fetch


def _weekly(df: pd.DataFrame) -> pd.DataFrame:
    return to_weekly(df, as_of=pd.Timestamp("2026-10-10", tz="UTC"))


def test_comparisons_from_config(tmp_path: Path) -> None:
    cfg = tmp_path / "b.toml"
    cfg.write_text(
        '[market]\n".L" = "^FTAS"\n'
        '[stocks."A.L"]\nindustry = "SECT.L"\npeers = ["P.L", "Q.DE"]\n'
        '[stocks."B.L"]\nmarket = "^FTMC"\n'
    )
    assert comparisons("A.L", cfg) == Comparisons("^FTAS", "SECT.L", ("P.L", "Q.DE"))
    assert comparisons("B.L", cfg) == Comparisons("^FTMC")
    assert comparisons("C.L", cfg) == Comparisons("^FTAS")  # no entry: market only
    assert comparisons("AAPL", cfg) == Comparisons(None)  # no default for the suffix
    jet2 = comparisons("JET2.L")  # the checked-in config loads
    assert jet2.market and jet2.industry and len(jet2.peers) >= 4


def test_outperformer_scores_positive_and_underperformer_negative() -> None:
    bench = _weekly(_daily("B", 0.0, 1))["adj_close"]
    up = relative_strength(_weekly(_daily("U", 0.002, 2))["adj_close"], bench)
    down = relative_strength(_weekly(_daily("D", -0.002, 3))["adj_close"], bench)
    assert up["score"].iloc[-1] > 0.5 and down["score"].iloc[-1] < -0.5
    assert up["score"].dropna().between(-1, 1).all()
    assert up["score"].iloc[:52].isna().all()  # warm-up: tracking error needs 52 weeks
    assert up["rs"].iloc[-1] > 1 > down["rs"].iloc[-1]


def test_no_look_ahead() -> None:
    stock = _weekly(_daily("S", 0.001, 2))["adj_close"]
    bench = _weekly(_daily("B", 0.0, 1))["adj_close"]
    full = relative_strength(stock, bench)
    cut = relative_strength(stock.iloc[:120], bench.iloc[:130])
    pd.testing.assert_frame_equal(cut, full.iloc[:120])


def test_rs_high_flags_a_new_relative_high() -> None:
    idx = pd.date_range(end=END, periods=80, freq="W-FRI", tz="UTC")
    bench = pd.Series(100.0, index=idx)
    stock = pd.Series(100 * np.exp(0.01 * np.sin(np.arange(80))), index=idx)
    stock.iloc[-1] = stock.max() * 1.05
    rs = relative_strength(stock, bench)
    assert rs["rs_high"].iloc[-1] and not rs["rs_high"].iloc[-2]


def test_basket_is_equal_weight_and_admits_late_members() -> None:
    idx = pd.date_range(end=END, periods=4, freq="W-FRI", tz="UTC")
    a = pd.Series([100, 110, 110, 121], index=idx, dtype=float)  # +10%, 0, +10%
    b = pd.Series([np.nan, 50, 40, 40], index=idx, dtype=float)  # joins week 2: -20%, 0
    got = basket([a, b])
    expected = [1.0, 1.10, 1.10 * (1 + (0.0 - 0.2) / 2), 1.10 * 0.9 * (1 + 0.05)]
    np.testing.assert_allclose(got.to_numpy(), expected)


def test_comparison_series_ignore_volume_issues() -> None:
    idx = _daily("IX", 0.0, 1).index
    ix = _daily("IX", 0.0, 1).assign(volume=0.0)  # indices / FX: no volume at all
    ix.loc[idx[-3], ["close", "adj_close"]] = np.nan  # one missing day
    assert len(weekly_close(ix)) == len(_weekly(ix))  # nothing trimmed away


def test_currency_conversion() -> None:
    eur = _daily("E.DE", 0.0, 4, currency="EUR")
    fx = _daily("EURGBP=X", 0.0, 5, currency="GBP")
    calls: list[str] = []
    got = in_currency("E.DE", _fetcher({"E.DE": eur, "EURGBP=X": fx}, calls), "GBP")
    expected = (weekly_close(eur) * weekly_close(fx)).dropna()
    np.testing.assert_allclose(got.to_numpy(), expected.to_numpy())
    calls.clear()
    in_currency("E.DE", _fetcher({"E.DE": eur.assign()}, calls), "EUR")  # same: no FX fetch
    gbp = _daily("P.L", 0.0, 6, currency="GBp")
    in_currency("P.L", _fetcher({"P.L": gbp}, calls), "GBP")  # pence vs pounds: same currency
    assert calls == ["E.DE", "P.L"]


def test_card_skips_unavailable_comparisons_and_ranks_peers() -> None:
    frames = {
        "^MKT": _daily("^MKT", 0.0, 1, currency="GBP"),
        "P1.L": _daily("P1.L", -0.01, 7),
        "P2.L": _daily("P2.L", 0.01, 8),  # the only peer doing better
    }
    stock = _weekly(_daily("S.L", 0.001, 2))
    comp = Comparisons("^MKT", "GONE", ("P1.L", "P2.L", "GONE2"))
    card = rs_card(compare(stock, comp, _fetcher(frames)))
    assert card.date == stock.index[-1]
    assert np.isnan(card.industry) and not np.isnan(card.market) and not np.isnan(card.peers)
    assert card.rs == pytest.approx((card.market + card.peers) / 2)
    assert card.compared == "market=^MKT peers=P1.L,P2.L"
    assert (card.peer_rank, card.peer_count) == (2, 2) and type(card.peer_rank) is int


def test_card_without_comparisons_is_nan() -> None:
    card = rs_card(compare(_weekly(_daily("S.L", 0.0, 2)), Comparisons(None), _fetcher({})))
    assert np.isnan(card.rs) and card.peer_rank is None and card.compared == ""


def test_toolkit_fetches_comparisons_once_and_snapshots_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames = {"S.L": _daily("S.L", 0.001, 2), "^MKT": _daily("^MKT", 0.0, 1, currency="GBP")}
    frames["T.L"] = _daily("T.L", -0.001, 9)
    calls: list[str] = []
    monkeypatch.setattr(toolkits, "fetch_daily", _fetcher(frames, calls))
    monkeypatch.setattr(toolkits, "_OTHERS", {})
    monkeypatch.setattr(toolkits, "comparisons", lambda t: Comparisons("^MKT"))
    s, t = toolkits.Inputs("S.L"), toolkits.Inputs("T.L")
    up, down = run_toolkit("relative_strength", s), run_toolkit("relative_strength", t)
    assert (up.status, down.status) == ("pass", "fail")
    assert calls == ["S.L", "^MKT", "T.L"]  # the market index fetched once for both
    db = tmp_path / "r.db"
    store.save_run("scan", {}, "S.L", up.cards, daily=s.daily, path=db, others=s.others)
    assert len(store.load_prices("^MKT", path=db)) == len(frames["^MKT"])
