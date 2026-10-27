"""Research 0007: how fast AIM prices absorb news, against the FTSE 100.

uv run python scripts/research/r7_reaction_speed.py OUT_DIR collect   # prices + NSM filings
uv run python scripts/research/r7_reaction_speed.py OUT_DIR events    # one row per news day
uv run python scripts/research/r7_reaction_speed.py OUT_DIR report    # the tables in 0007

Two measurements per news filing (types in NEWS; the earliest per session):
- intraday: 5-minute Yahoo bars (the last ~60 days only) for pre-market filings: how much of the
  day's close-to-close move the price had made by 08:05, 08:15, ... 15:00;
- daily: market-adjusted (^FTAI / ^FTSE) returns 1-20 sessions after the reaction session,
  signed by its direction, over 5 years. In-sample only (ADR 0029): forward windows end by
  2025-10-17. A survivorship baseline (any day's forward return, same stocks) is subtracted,
  because the watchlist is today's constituents.
Research only; writes parquet into OUT_DIR, never into data/.
"""

from __future__ import annotations

import argparse
import time
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.analysis.events import reaction_session
from solaraalpha.data.announcements import fetch_announcements, known_issuers
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.data.http import session
from solaraalpha.data.prices import CHART_URL, fetch_daily

LDN = "Europe/London"
# Filings that can carry news about the business (not holdings, dealings, buybacks, notices).
NEWS = frozenset(
    {"TST", "UPD", "IR", "FR", "QRT", "ACQ", "DIS", "CNT", "AGR", "REA", "DRL", "MSCL", "STR"}
    | {"RTT", "AGM", "IOE", "MER", "OFF", "SUS", "PDI", "FUR", "TAB", "SOA", "OUP", "PLC", "CAR"}
)
LAST_SESSION = pd.Timestamp("2025-09-18", tz="UTC")  # +20 sessions ends by 2025-10-17 (ADR 0029)
BIG_Z = 2.0
HORIZONS = (1, 2, 5, 10, 20)
TIMES = ("08:05", "08:15", "08:30", "09:00", "10:00", "12:00", "15:00")
INDEX = {"AIM": "^FTAI", "FTSE100": "^FTSE"}
# FTSE 100 comparison: same exchange, same RNS timing; LEIs from the NSM name search.
# Barclays left out: its filings pass the NSM's 10,000-result cap.
FTSE100 = {
    "AZN.L": "PY6ZZQWO2IZFZC3IOL08",
    "SHEL.L": "21380068P1DRHMJ8KU70",
    "HSBA.L": "MLU0ZO3ML4LN2LL2TL39",
    "ULVR.L": "549300MKFYEKVRWML317",
    "BP.L": "213800LH1BZH3DI6G760",
    "RIO.L": "213800YOEO5OQ72G2R82",
    "GSK.L": "5493000HZTVUYLO1D793",
    "REL.L": "549300WSX3VBUFFJOO66",
    "DGE.L": "213800ZVIELEA55JMJ32",
    "LSEG.L": "213800QAUUUP6I445N30",
    "BATS.L": "213800FKA5MF17RJKT63",
    "NG.L": "8R95QZMKZLJX5Q2XR704",
    "LLOY.L": "549300PPXHEU2JF0AM85",
    "TSCO.L": "2138002P5RNKC5W2JZ46",
    "PRU.L": "5493001Z3ZE83NGK8Y12",
    "CPG.L": "2138008M6MH9OZ6U2T68",
    "EXPN.L": "635400RXXHHYT1JTSZ26",
    "BA.L": "8SVCSVKSGDWMW2QHOH83",
    "RR.L": "213800EC7997ZBLZJH69",
    "IMB.L": "549300DFVPOB67JL3A42",
    "SGE.L": "2138005RN5XYLTF8G138",
    "AAL.L": "549300S9XF92D1X8ME43",
    "WPP.L": "549300LSGBXPYHXGDT93",
    "KGF.L": "213800KBMEV7I92FY281",
    "MKS.L": "213800EJP14A79ZG1X44",
    "SBRY.L": "213800VGZAAJIKJ9Y484",
    "AUTO.L": "213800QLK9BZILB1DI86",
    "III.L": "35GDVHRBMFE7NWATNM84",
}


def universe() -> dict[str, tuple[str, str | None]]:
    with open("config/watchlist.toml", "rb") as f:
        aim = tomllib.load(f)["tickers"]
    issuers = known_issuers()
    return {
        **{t: ("AIM", issuers.get(t, {}).get("lei")) for t in aim},
        **{t: ("FTSE100", lei) for t, lei in FTSE100.items()},
    }


def fetch_5m(ticker: str) -> pd.DataFrame:
    resp = session().get(
        CHART_URL.format(ticker=ticker),
        params={"interval": "5m", "range": "60d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    res = resp.json()["chart"]["result"][0]
    ts = pd.to_datetime(res.get("timestamp", []), unit="s", utc=True)
    return pd.DataFrame(res["indicators"]["quote"][0], index=ts)


def collect(out: Path) -> None:
    """Daily (5y), 5-minute (60d) and NSM filings (5y) per stock; skips files already there."""
    out.mkdir(parents=True, exist_ok=True)
    for name, ticker in INDEX.items():
        fetch_daily(ticker).to_parquet(out / f"{name}.index.parquet")
    for ticker, (_, lei) in universe().items():
        jobs = {
            "daily": lambda t=ticker: fetch_daily(t),
            "m5": lambda t=ticker: fetch_5m(t),
            "nsm": lambda lei=lei: fetch_announcements(lei) if lei else None,
        }
        for kind, fetch in jobs.items():
            path = out / f"{ticker}.{kind}.parquet"
            if path.exists():
                continue
            try:
                df = fetch()
            except Exception as e:
                print(ticker, kind, "failed:", e)
                continue
            if df is not None:
                df.to_parquet(path)
            time.sleep(0.3)  # the NSM answers 403 when hit too fast; re-run to fill the gaps


def daily_prices(path: Path) -> pd.DataFrame:
    d = clean_prices(pd.read_parquet(path))
    return trim_to_consistent(d, check_prices(d))


def events(out: Path) -> None:
    """One row per (stock, news session): daily drift rows and, for pre-market news, intraday."""
    market = {
        g: clean_prices(pd.read_parquet(out / f"{g}.index.parquet"))["adj_close"] for g in INDEX
    }
    day_rows, intra_rows = [], []
    for ticker, (group, _) in universe().items():
        paths = [out / f"{ticker}.{k}.parquet" for k in ("daily", "nsm", "m5")]
        if not all(p.exists() for p in paths):
            continue
        d = daily_prices(paths[0])
        if len(d) < 80:
            continue
        ann = pd.read_parquet(paths[1])
        ann = ann[ann["type_code"].isin(NEWS)]
        adj, close = d["adj_close"], d["close"]
        sigma = np.log(adj).diff().rolling(60).std().shift(1)
        adv = (close * d["volume"] / 100).rolling(60).median().shift(1)  # GBP, pence prices
        mkt = market[group].reindex(d.index).ffill()
        sessions = pd.DatetimeIndex(d.index)
        ann = ann.assign(session=[reaction_session(p, sessions) for p in ann["published"]])
        ann = ann.dropna(subset=["session"])
        local = ann["published"].dt.tz_convert(LDN)
        clock = local - local.dt.normalize()
        ann["premarket"] = (clock < pd.Timedelta(hours=8)) | (
            clock >= pd.Timedelta(hours=16, minutes=30)
        )
        ev = ann.sort_values("published").groupby("session").first()
        m5 = pd.read_parquet(paths[2]).dropna(subset=["close"])
        m5.index = m5.index.tz_convert(LDN)
        for s, e in ev.iterrows():
            i = d.index.get_loc(s)
            if not isinstance(i, int) or i < 61:
                continue
            ret = adj.iloc[i] / adj.iloc[i - 1] - 1
            excess = ret - (mkt.iloc[i] / mkt.iloc[i - 1] - 1)
            base = dict(ticker=ticker, group=group, session=s, type=e["type_code"], adv=adv.iloc[i])
            if i + HORIZONS[-1] < len(d):
                row = base | dict(
                    premarket=e["premarket"],
                    r0=excess,
                    z=np.log1p(excess) / sigma.iloc[i],
                    raw0=close.iloc[i] / close.iloc[i - 1] - 1,
                    gap=d["open"].iloc[i] / close.iloc[i - 1] - 1,
                )
                for k in HORIZONS:
                    row[f"c{k}"] = adj.iloc[i + k] / adj.iloc[i] - mkt.iloc[i + k] / mkt.iloc[i]
                day_rows.append(row)
            bars = m5[m5.index.date == s.date()]
            if not e["premarket"] or bars.empty:
                continue
            prev = close.iloc[i - 1]
            row = base | dict(
                r_close=close.iloc[i] / prev - 1,
                z=np.log(close.iloc[i] / prev) / sigma.iloc[i],
                r_open=d["open"].iloc[i] / prev - 1,
            )
            stamps = bars.index.strftime("%H:%M")
            for label in TIMES:
                # a 5-minute bar is stamped with its start: the price at 08:15 is the 08:10 bar's
                end = (pd.Timestamp(f"2000-01-01 {label}") - pd.Timedelta(minutes=5)).strftime(
                    "%H:%M"
                )
                upto = bars[stamps <= end]
                row[label] = upto["close"].iloc[-1] / prev - 1 if len(upto) else np.nan
            for k in (1, 2, 5):
                row[f"d{k}"] = close.iloc[i + k] / close.iloc[i] - 1 if i + k < len(d) else np.nan
            intra_rows.append(row)
    pd.DataFrame(day_rows).to_parquet(out / "daily_events.parquet")
    pd.DataFrame(intra_rows).to_parquet(out / "intraday_events.parquet")
    print("daily", len(day_rows), "intraday", len(intra_rows))


def baseline(out: Path) -> dict[tuple[str, int], float]:
    """Mean market-adjusted forward return from any in-sample day: the survivorship drift."""
    market = {
        g: clean_prices(pd.read_parquet(out / f"{g}.index.parquet"))["adj_close"] for g in INDEX
    }
    parts: dict[tuple[str, int], list[pd.Series]] = {}
    for ticker, (group, _) in universe().items():
        path = out / f"{ticker}.daily.parquet"
        if not path.exists():
            continue
        d = daily_prices(path)
        if len(d) < 80:
            continue
        a, m = d["adj_close"], market[group].reindex(d.index).ffill()
        for k in HORIZONS:
            x = (a.shift(-k) / a - m.shift(-k) / m).iloc[61:]
            parts.setdefault((group, k), []).append(x[x.index <= LAST_SESSION].dropna())
    return {key: float(pd.concat(v).mean()) for key, v in parts.items()}


def report(out: Path) -> None:
    intra = pd.read_parquet(out / "intraday_events.parquet")
    print("Intraday, pre-market news, |z| >= 2: share of the day's move made by each time")
    for group, x in intra[intra["z"].abs() >= BIG_Z].groupby("group"):
        sign = np.sign(x["r_close"])
        shares = {c: (sign * x[c]).sum() / x["r_close"].abs().sum() for c in ("r_open", *TIMES)}
        after = {k: (sign * x[f"d{k}"]).median() for k in (1, 2, 5)}
        print(f"  {group} n={len(x)} median |move| {x['r_close'].abs().median():.1%}")
        print("   ", "  ".join(f"{k} {v:.0%}" for k, v in shares.items()))
        print(
            "    median signed after close:", "  ".join(f"+{k}d {v:.2%}" for k, v in after.items())
        )

    base = baseline(out)
    day = pd.read_parquet(out / "daily_events.parquet")
    day = day[(day["session"] <= LAST_SESSION) & (day["z"].abs() >= BIG_Z)]
    print("\nDaily, |z| >= 2, market-adjusted, minus the survivorship baseline, signed by day 0")
    print("  baseline:", {f"{g} +{k}d": f"{v:.2%}" for (g, k), v in base.items()})

    def line(label: str, x: pd.DataFrame, group: str) -> None:
        sign = np.sign(x["r0"])
        cells = []
        for k in (5, 10, 20):
            ex = sign * (x[f"c{k}"] - base[(group, k)])
            by_day = ex.groupby(x["session"]).mean()  # cluster by date
            t = by_day.mean() / by_day.std() * np.sqrt(len(by_day))
            cells.append(f"+{k}d {ex.mean():+.2%} (t {t:.1f})")
        print(f"  {label:<28} n={len(x):<4}", "  ".join(cells))

    for group, x in day.groupby("group"):
        line(f"{group} up", x[x["r0"] > 0], group)
        line(f"{group} down", x[x["r0"] < 0], group)
    aim = day[day["group"] == "AIM"].dropna(subset=["adv"]).copy()
    aim["liq"] = pd.qcut(aim["adv"], 3, labels=["low", "mid", "high"])
    for liq, x in aim.groupby("liq", observed=True):
        line(f"AIM {liq} ADV up (£{x['adv'].median():,.0f})", x[x["r0"] > 0], "AIM")
    for kind in ("FR", "IR", "TST"):
        line(f"AIM {kind} up", aim[(aim["type"] == kind) & (aim["r0"] > 0)], "AIM")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out", type=Path)
    p.add_argument("step", choices=["collect", "events", "report"])
    a = p.parse_args()
    {"collect": collect, "events": events, "report": report}[a.step](a.out)


if __name__ == "__main__":
    main()
