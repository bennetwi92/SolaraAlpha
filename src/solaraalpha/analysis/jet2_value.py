"""Jet2 fair-value model: driver-based earnings for FY27 / FY28, valued at the trade horizon.

Jet2-specific by design (not reusable). Built for long trades of ~2-16 weeks, so the question
is "where should the price be in N weeks, and what does this piece of news do to it?", not a
long-term DCF. See ``docs/decisions/0009-jet2-fair-value.md`` for the reasoning and sources.

**Earnings.** A driver P&L per fiscal year (April-March) that reproduces the FY26 accounts line
by line. Seats x load factor give passenger sectors, split into package customers and
flight-only. Revenue comes from package price, flight-only yield and non-ticket spend. Costs are
per customer, sector, seat or tonne of fuel (hotels, airports, crews, carbon...), and fuel is
tonnes x hedged and market prices in USD. Interest on the ~£3.4bn cash pile (mostly customer
deposits) is earned at Bank Rate. Every driver is a lever a piece of news can move.

**Priced today.** The base years are calibrated so EPS equals analyst consensus on the date it
was taken (``CONSENSUS``), by solving one selling-price factor (``yield_adj``) per year. Market
inputs on that date (fuel curve, FX, Bank Rate) are stored in ``REFERENCE``. A run moves the
base to live inputs, so it is "what the market expects, updated for what has changed since".
Market fuel and euro moves are partly passed through to selling prices (``pass_through``)
because every operator faces them, while Jet2's own cost moves only on unhedged volumes.

**Value at the horizon.** FY27 is mostly flown and hedged, so the price is set by FY28 EPS
(Summer 2027). Fair value = P/E x FY28 EPS + the change in FY27 EPS against consensus (a
one-off, worth its cash, not a multiple). The P/E starts at what the market pays today for
consensus FY28 and reverts toward ``FAIR_PE`` with a one-year half-life, so a 12-week hold
expects only a sixth of any re-rating.

**News.** :func:`apply` takes shock strings (``"seats=+2%"``, ``"fy28.fuel_hedged=0.7"``,
``"extra_revenue=+20"``). With no prefix, a change works going forward: in full on FY28 and on
FY27 only for the share of the year still to fly. ``fy27.`` hits all of FY27 (news about the
past, e.g. H1 results); ``fy28.`` only FY28.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

import numpy as np
import pandas as pd

from solaraalpha.log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Year:
    """Drivers of one fiscal year. Counts in millions, money in £m, unit prices in £ or $."""

    name: str
    # Volume
    seats: float  # m seats flown
    load_factor: float
    package_mix: float  # package passengers / all passenger sectors (each customer = 2 sectors)
    # Revenue per unit
    package_price: float  # £ revenue per package customer
    flight_only_yield: float  # £ ticket revenue per flight-only sector
    yield_adj: float  # multiplies package price and flight-only yield (calibrated to consensus)
    non_ticket: float  # £ per sector (bags, seats, in-flight retail)
    other_revenue: float  # £m
    extra_revenue: float  # £m, for news (a new product, a partnership)
    # Variable costs
    hotel_cost: float  # £ per package customer, at euro rate ``eur_ref``
    transfer_cost: float  # £ per package customer
    commission_rate: float  # travel-agent commission / package revenue
    airport_cost: float  # £ per sector: landing, navigation, handling
    inflight_cost: float  # £ per sector
    maintenance: float  # £ per seat
    staff_cost: float  # £ per seat
    marketing: float  # £ per seat
    depreciation: float  # £ per seat
    other_costs: float  # £ per seat: short-term aircraft rentals, other direct and operating
    extra_costs: float  # £m, for news
    # Fuel (excluding the SAF premium)
    fuel_burn: float  # tonnes per seat
    fuel_hedged: float  # share of tonnes hedged
    fuel_hedge_usd: float  # $/t swap price on hedged tonnes
    fuel_market_usd: float  # $/t paid on unhedged tonnes (CIF NWE jet)
    fuel_ref_usd: float  # $/t market price the base selling prices reflect
    saf_cost: float  # £ per tonne of fuel (mandated SAF premium)
    carbon_cost: float  # £ per tonne of fuel (UK / EU ETS)
    # Currency
    usd_hedged: float  # GBP/USD on hedged fuel
    usd_market: float  # GBP/USD on unhedged fuel
    usd_ref: float
    eur_market: float  # GBP/EUR
    eur_ref: float
    eur_hedged: float  # share of euro hotel costs hedged
    hotel_eur_share: float  # share of hotel costs in euros
    pass_through: float  # share of market fuel / euro hotel cost changes recovered in prices
    # Below operating profit
    avg_cash: float  # £m average total cash incl. money-market deposits
    base_rate: float  # Bank of England Bank Rate, year average
    cash_spread: float  # deposit yield minus Bank Rate
    finance_expense: float  # £m
    other_income: float  # £m: disposal gains, FX revaluation
    tax_rate: float
    shares: float  # m diluted weighted-average shares


REVENUE_LINES = ("package", "flight_only", "non_ticket", "other_revenue", "cost_recovery")
COST_LINES = (
    "hotels",
    "fuel",
    "saf_carbon",
    "airports",
    "maintenance",
    "commission",
    "inflight",
    "transfers",
    "staff",
    "marketing",
    "depreciation",
    "other_costs",
)


def pnl(y: Year) -> dict[str, float]:
    """Income statement lines (£m; ``eps`` in pence) for one year of drivers."""
    sectors = y.seats * y.load_factor
    customers = sectors * y.package_mix / 2
    tonnes = y.seats * y.fuel_burn
    hotels = customers * y.hotel_cost
    eur_move = (y.eur_ref / y.eur_market - 1) * y.hotel_eur_share  # + = euros dearer
    fuel_move = tonnes * (y.fuel_market_usd / y.usd_market - y.fuel_ref_usd / y.usd_ref)
    package = customers * y.package_price * (1 + y.yield_adj)
    hedged = y.fuel_hedged * y.fuel_hedge_usd / y.usd_hedged
    lines = {
        "package": package,
        "flight_only": sectors * (1 - y.package_mix) * y.flight_only_yield * (1 + y.yield_adj),
        "non_ticket": sectors * y.non_ticket,
        "other_revenue": y.other_revenue + y.extra_revenue,
        "cost_recovery": y.pass_through * (fuel_move + hotels * eur_move),
        "hotels": hotels * (1 + eur_move * (1 - y.eur_hedged)),
        "fuel": tonnes * (hedged + (1 - y.fuel_hedged) * y.fuel_market_usd / y.usd_market),
        "saf_carbon": tonnes * (y.saf_cost + y.carbon_cost),
        "airports": sectors * y.airport_cost,
        "maintenance": y.seats * y.maintenance,
        "commission": package * y.commission_rate,
        "inflight": sectors * y.inflight_cost,
        "transfers": customers * y.transfer_cost,
        "staff": y.seats * y.staff_cost,
        "marketing": y.seats * y.marketing,
        "depreciation": y.seats * y.depreciation,
        "other_costs": y.seats * y.other_costs + y.extra_costs,
    }
    revenue = sum(lines[k] for k in REVENUE_LINES)
    costs = sum(lines[k] for k in COST_LINES)
    finance = y.avg_cash * (y.base_rate + y.cash_spread) - y.finance_expense
    pbt = revenue - costs + finance + y.other_income
    net = pbt * (1 - y.tax_rate)
    return lines | {
        "revenue": revenue,
        "operating_costs": costs,
        "ebit": revenue - costs,
        "net_finance": finance,
        "pbt": pbt,
        "net_income": net,
        "passengers": sectors,
        "fuel_tonnes": tonnes,
        "eps": 100 * net / y.shares,
    }


# --- FY26 actuals (year to 31 Mar 2026, preliminary results 8 Jul 2026) ---------------------
# Reproduces revenue £7,482.1m, operating profit £439.6m, PBT £551.0m and diluted EPS 208.2p.
# Sectors 20.83m (24.00m seats x 86.8%), package customers = 63.3% of sectors / 2, revenue
# split and cost lines from notes 3-4, finance from the income statement.
_SEATS_26 = 24.00
_SECTORS_26 = _SEATS_26 * 0.868
_CUSTOMERS_26 = _SECTORS_26 * 0.633 / 2
# Fuel tonnes are not disclosed: ~1.15m t is derived from £696.9m fuel ex SAF at roughly $790/t
# and $1.30 (FY26 hedges); 123k flights x typical burn gives 1.0-1.2m t.
_TONNES_26 = 1.15

FY26 = Year(
    name="FY26",
    seats=_SEATS_26,
    load_factor=0.868,
    package_mix=0.633,
    package_price=5962.7 / _CUSTOMERS_26,  # £904 (reported "average price" £900)
    flight_only_yield=844.2 / (_SECTORS_26 * (1 - 0.633)),  # ~£110.4 (reported £110.92)
    yield_adj=0.0,
    non_ticket=553.0 / _SECTORS_26,  # £26.56
    other_revenue=122.2,
    extra_revenue=0.0,
    hotel_cost=3166.5 / _CUSTOMERS_26,  # £480
    transfer_cost=125.7 / _CUSTOMERS_26,
    commission_rate=187.4 / 5962.7,
    airport_cost=600.5 / _SECTORS_26,
    inflight_cost=133.8 / _SECTORS_26,
    maintenance=192.8 / _SEATS_26,
    staff_cost=913.6 / _SEATS_26,
    marketing=259.0 / _SEATS_26,
    depreciation=292.9 / _SEATS_26,
    other_costs=(57.7 + 122.4 + 169.5) / _SEATS_26,
    extra_costs=0.0,
    fuel_burn=_TONNES_26 / _SEATS_26,
    fuel_hedged=1.0,
    fuel_hedge_usd=(728.9 - 32.0) / _TONNES_26 * 1.30,
    fuel_market_usd=790.0,
    fuel_ref_usd=790.0,
    saf_cost=32.0 / _TONNES_26,
    carbon_cost=91.8 / _TONNES_26,
    usd_hedged=1.30,
    usd_market=1.30,
    usd_ref=1.30,
    eur_market=1.17,
    eur_ref=1.17,
    eur_hedged=1.0,
    hotel_eur_share=0.85,
    pass_through=0.0,
    avg_cash=3475.0,  # finance income £140.7m at ~4.05% average Bank Rate
    base_rate=140.7 / 3475.0,
    cash_spread=0.0,
    finance_expense=49.5,
    other_income=13.8 + 6.4,
    tax_rate=140.5 / 551.0,
    shares=197.2,
)

# --- Market inputs on the consensus date ----------------------------------------------------
# Jet fuel (CIF NWE, $/t) is proxied from NYMEX heating-oil futures ($/gal): 330 gal per tonne,
# plus JET_PREMIUM (CIF NWE jet $1,630.5/t on 15 Oct 2026 vs Nov-26 heating oil $4.762/gal).
GAL_PER_TONNE = 330.0
JET_PREMIUM = 0.04
# Seat profile: Summer (Apr-Oct) 19.9m seats over 7 months, Winter (Nov-Mar) 5.9m over 5.
SUMMER_MONTHS = (4, 5, 6, 7, 8, 9, 10)
SUMMER_SEATS, WINTER_SEATS = 19.9, 5.9


@dataclass(frozen=True)
class Market:
    """Market inputs at a date. Fuel in $/t, FX as GBP/USD and GBP/EUR."""

    date: pd.Timestamp
    price: float  # Jet2 share price, pence
    fuel_fy27_rest: float  # seat-weighted forward jet price for the rest of FY27 (to Mar 2027)
    fuel_fy28: float  # seat-weighted forward jet price for FY28 (Apr 2027-Mar 2028)
    usd: float
    eur: float
    base_rate: float


REFERENCE = Market(
    date=pd.Timestamp("2026-10-16", tz="UTC"),
    price=1413.0,
    fuel_fy27_rest=1545.0,  # Nov-26..Mar-27 heating oil avg $4.503/gal
    fuel_fy28=1296.0,  # Apr-27..Mar-28 seat-weighted heating oil $3.777/gal
    usd=1.334,
    eur=1.186,
    base_rate=0.0375,  # held 17 Sep 2026 (6-3; three voted for 4%)
)

# Analyst consensus diluted EPS (pence): Yahoo earningsTrend, 14 analysts, 16 Oct 2026.
# FY27 range 116-160p (130p 90 days earlier); FY28 range 129-243p (191p 90 days earlier).
CONSENSUS_DATE = "2026-10-16"
CONSENSUS: dict[str, float] = {"FY27": 134.0, "FY28": 186.0}

# --- Shares ---------------------------------------------------------------------------------
# 185.75m in issue on 12 Oct 2026 (NSM). The Employee Benefit Trust holds ~6.6m (192.6m in issue
# at 31 Mar 2026 vs 186.0m outstanding); options add ~2.8m (note 5). The £250m buyback launched
# 8 Jul 2026 runs to 31 May 2027; ~5.2m shares (~£77m) were bought by 9 Oct 2026.
SHARES_IN_ISSUE, EBT_SHARES, DILUTIVE = 185.75, 6.6, 2.8
BUYBACK_LEFT, BUYBACK_END = 173.0, pd.Timestamp("2027-05-31", tz="UTC")
FY27_ISSUED_TO_DATE = 190.3  # average in issue Apr-mid Oct 2026 (192.6 -> 190.9 -> 185.75)
_FY27_END = pd.Timestamp("2027-03-31", tz="UTC")


def diluted_shares(price: float) -> tuple[float, float]:
    """Diluted weighted-average shares (m) in FY27 and FY28, with the rest of the buyback spent
    evenly from ``REFERENCE.date`` to ``BUYBACK_END`` at ``price`` (pence)."""
    months = (BUYBACK_END - REFERENCE.date).days / 30.4
    per_month = BUYBACK_LEFT / (price / 100) / months
    rest27 = (_FY27_END - REFERENCE.date).days / 30.4
    end27 = SHARES_IN_ISSUE - per_month * rest27
    final = SHARES_IN_ISSUE - per_month * months  # 31 May 2027
    avg27 = (FY27_ISSUED_TO_DATE * (12 - rest27) + (SHARES_IN_ISSUE + end27) / 2 * rest27) / 12
    avg28 = ((end27 + final) / 2 * 2 + final * 10) / 12
    return avg27 + DILUTIVE - EBT_SHARES, avg28 + DILUTIVE - EBT_SHARES


_SH27, _SH28 = diluted_shares(REFERENCE.price)

# --- FY27 base (year to 31 Mar 2027) --------------------------------------------------------
# Facts: Summer 26 19.9m seats (+7.6%), Winter 26/27 5.9m (+8.0%); summer load factor +1.5pts to
# end-August (AGM 3 Sep 2026); 93% of fuel hedged at $753/t, >90% of FX; 4% pay award; Gatwick
# start-up incl. three wet-leased aircraft. Unit-cost inflation is assumed (FY26 actuals: hotels
# +6% supply-led, airports +4%, maintenance +7%). Selling prices are whatever consensus implies:
# ``yield_adj`` is solved by :func:`calibrate`.
_FY27_FUEL = (SUMMER_SEATS * 1310.0 + WINTER_SEATS * REFERENCE.fuel_fy27_rest) / 25.8
FY27 = replace(
    FY26,
    name="FY27",
    seats=SUMMER_SEATS + WINTER_SEATS,
    load_factor=0.881,
    package_mix=0.630,
    non_ticket=FY26.non_ticket * 1.03,
    other_revenue=FY26.other_revenue * 1.075,
    hotel_cost=FY26.hotel_cost * 1.04,
    transfer_cost=FY26.transfer_cost * 1.04,
    airport_cost=FY26.airport_cost * 1.045,
    inflight_cost=FY26.inflight_cost * 1.05,
    maintenance=FY26.maintenance * 1.06,
    staff_cost=FY26.staff_cost * 1.04 * 0.975,  # pay +4%, headcount ~+5% on seats +7.5%
    marketing=FY26.marketing * 0.95,
    depreciation=FY26.depreciation * 0.99,
    other_costs=FY26.other_costs * 1.025,  # wet-leased aircraft at Gatwick
    fuel_burn=FY26.fuel_burn * 0.99,  # A321neo share 17% -> 22%, 20% less fuel per seat
    fuel_hedged=0.93,
    fuel_hedge_usd=753.0,
    # Unhedged: summer at realised CIF NWE (Apr-Sep avg ~$1,310/t), winter at the curve.
    fuel_market_usd=_FY27_FUEL,
    fuel_ref_usd=_FY27_FUEL,
    saf_cost=FY26.saf_cost * 1.45,  # UK mandate 2% -> 3.6% (2026), EU 2%
    carbon_cost=FY26.carbon_cost * 1.12,  # EU ETS free allowances end in 2026
    usd_hedged=1.32,  # assumption: hedged rate not disclosed
    usd_market=REFERENCE.usd,
    usd_ref=REFERENCE.usd,
    eur_market=REFERENCE.eur,
    eur_ref=REFERENCE.eur,
    eur_hedged=0.90,
    pass_through=0.10,  # winter largely priced; little room to recover cost moves
    avg_cash=3450.0,
    base_rate=REFERENCE.base_rate,
    finance_expense=55.0,
    other_income=5.0,
    tax_rate=0.25,
    shares=_SH27,
)

# --- FY28 base (year to 31 Mar 2028): Summer 2027 + Winter 2027/28 --------------------------
# Not yet guided. Assumptions: seats +5% (Gatwick 6 -> 7 aircraft, A321neo deliveries), load
# factor 87.5%, unit costs +3-4%. FY28 fuel hedging is NOT disclosed (next update: H1 results
# 18 Nov 2026); 50% at $950/t is a placeholder (pre-war layers ~$720, post-war ~$1,200).
FY28 = replace(
    FY27,
    name="FY28",
    seats=FY27.seats * 1.05,
    load_factor=0.875,
    package_price=FY27.package_price * 1.03,
    flight_only_yield=FY27.flight_only_yield * 1.02,
    non_ticket=FY27.non_ticket * 1.03,
    other_revenue=FY27.other_revenue * 1.05,
    hotel_cost=FY27.hotel_cost * 1.035,
    transfer_cost=FY27.transfer_cost * 1.03,
    airport_cost=FY27.airport_cost * 1.04,
    inflight_cost=FY27.inflight_cost * 1.04,
    maintenance=FY27.maintenance * 1.04,
    staff_cost=FY27.staff_cost * 1.035,
    marketing=FY27.marketing * 1.0,
    depreciation=FY27.depreciation * 1.02,
    other_costs=FY27.other_costs * 1.02,
    fuel_burn=FY27.fuel_burn * 0.985,
    fuel_hedged=0.50,
    fuel_hedge_usd=950.0,
    fuel_market_usd=REFERENCE.fuel_fy28,
    fuel_ref_usd=REFERENCE.fuel_fy28,
    saf_cost=FY27.saf_cost * 1.3,  # UK mandate 5.2% in 2027
    carbon_cost=FY27.carbon_cost * 1.05,
    usd_hedged=1.33,
    eur_hedged=0.50,
    pass_through=0.30,
    avg_cash=3400.0,
    finance_expense=60.0,
    shares=_SH28,
)


def calibrate(y: Year, eps: float) -> Year:
    """``y`` with ``yield_adj`` set so EPS equals ``eps`` (EPS is linear in the yield)."""
    e0 = pnl(replace(y, yield_adj=0.0))["eps"]
    e1 = pnl(replace(y, yield_adj=0.01))["eps"]
    return replace(y, yield_adj=0.01 * (eps - e0) / (e1 - e0))


def base_years() -> tuple[Year, Year]:
    """FY27 and FY28 calibrated to consensus EPS at ``REFERENCE`` market inputs."""
    return calibrate(FY27, CONSENSUS["FY27"]), calibrate(FY28, CONSENSUS["FY28"])


def _seat_weight(month: pd.Timestamp) -> float:
    return SUMMER_SEATS / 7 if month.month in SUMMER_MONTHS else WINTER_SEATS / 5


def market_from(
    date: pd.Timestamp,
    price: float,
    heating_oil: pd.Series,
    usd: float,
    eur: float,
    base_rate: float = REFERENCE.base_rate,
) -> Market:
    """Market inputs from a heating-oil futures curve ($/gal by delivery month; see
    :func:`solaraalpha.data.futures.fetch_curve`), converted to jet $/t and seat-weighted over
    the rest of FY27 and over FY28. A period with no contract data keeps its reference price."""
    jet = heating_oil * GAL_PER_TONNE * (1 + JET_PREMIUM)
    jet.index = pd.DatetimeIndex(jet.index).tz_localize(None)
    month = date.tz_localize(None).to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)

    def avg(start: str | pd.Timestamp, end: str, ref: float) -> float:
        s = jet[(jet.index >= pd.Timestamp(start)) & (jet.index <= pd.Timestamp(end))]
        if s.empty:
            return ref
        w = np.array([_seat_weight(m) for m in s.index])
        return float((s.to_numpy() * w).sum() / w.sum())

    return Market(
        date=date,
        price=price,
        fuel_fy27_rest=avg(
            max(month, pd.Timestamp("2026-11-01")), "2027-03-01", REFERENCE.fuel_fy27_rest
        ),
        fuel_fy28=avg("2027-04-01", "2028-03-01", REFERENCE.fuel_fy28),
        usd=usd,
        eur=eur,
        base_rate=base_rate,
    )


def priced_in(y27: Year, y28: Year) -> dict[str, float]:
    """Selling prices the consensus-calibrated years imply: package price per customer and
    flight-only yield, and their change on the year before."""
    p26, f26 = FY26.package_price, FY26.flight_only_yield
    p27, f27 = y27.package_price * (1 + y27.yield_adj), y27.flight_only_yield * (1 + y27.yield_adj)
    p28, f28 = y28.package_price * (1 + y28.yield_adj), y28.flight_only_yield * (1 + y28.yield_adj)
    return {
        "package_price_fy27": p27,
        "package_price_fy28": p28,
        "package_growth_fy27": p27 / p26 - 1,
        "package_growth_fy28": p28 / p27 - 1,
        "flight_only_growth_fy27": f27 / f26 - 1,
        "flight_only_growth_fy28": f28 / f27 - 1,
    }


# Dated catalysts that can move FY27 / FY28 expectations (est. = projected from history).
CATALYSTS = [
    ("2026-11-05", "Bank of England decision (markets priced ~88% odds of a hike, 13 Oct)"),
    ("2026-11-18", "H1 FY27 results: summer yields, FY28 fuel hedge cover, Summer 2027 capacity"),
    ("2026-12-17", "Bank of England decision (est.)"),
    ("2026-12-31", "Buyback tranche 1 (£125m) due to end; tranche 2 to be announced"),
    ("2027-02-24", "Trading update (est.; 25 Feb last year): FY27 outturn vs consensus, Summer 27"),
    ("2027-03-31", "Main Market admission targeted by this date (FTSE 250 eligibility)"),
    ("2027-04-28", "Year-end trading update (est.; 29 Apr last year)"),
]


def catalysts(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[str, str]]:
    """``CATALYSTS`` dated within [start, end]."""
    s, e = start.tz_localize(None), end.tz_localize(None)
    return [(d, t) for d, t in CATALYSTS if s <= pd.Timestamp(d) <= e]


def fy27_remaining(as_of: pd.Timestamp) -> float:
    """Share of FY27 seats still to fly after ``as_of`` (summer months carry more seats)."""
    months = pd.date_range("2026-04-01", "2027-03-01", freq="MS", tz="UTC")
    seats = np.array(
        [SUMMER_SEATS / 7 if m.month in SUMMER_MONTHS else WINTER_SEATS / 5 for m in months]
    )
    ends = months + pd.offsets.MonthBegin(1)
    left = np.asarray((ends - as_of).days) / np.asarray((ends - months).days)
    return float((seats * np.clip(left, 0, 1)).sum() / seats.sum())


# --- Shocks ---------------------------------------------------------------------------------

YEAR_FIELDS = {f.name for f in fields(Year)} - {"name"}


def _target(base: float, spec: str) -> float:
    """New value from ``spec``: ``+5%``/``-5%`` relative, ``+10``/``-10`` added, ``*1.1``
    scaled, a bare number set."""
    s = spec.strip()
    if s.endswith("%"):
        return base * (1 + float(s[:-1]) / 100)
    if s.startswith("*"):
        return base * float(s[1:])
    if s[0] in "+-":
        return base + float(s)
    return float(s)


def apply(
    years: tuple[Year, Year], shocks: list[str], fy27_weight: float
) -> tuple[Year, Year, float]:
    """Apply shock strings ``[fy27.|fy28.]field=spec`` to (FY27, FY28).

    No prefix: a going-forward change, in full on FY28 and on ``fy27_weight`` (the share of FY27
    still to fly) of FY27. ``fy27.``: all of FY27 (news about the past). ``fy28.``: FY28 only.
    The special field ``pe`` shifts the horizon P/E; it is returned as the third element.
    """
    y27, y28 = years
    pe_shift = 0.0
    for shock in shocks:
        key, _, spec = shock.partition("=")
        scope, _, field = key.strip().rpartition(".")
        if field == "pe" and spec:
            pe_shift = _target(pe_shift, spec)
            continue
        if field not in YEAR_FIELDS or scope not in ("", "fy27", "fy28") or not spec.strip():
            raise ValueError(f"bad shock {shock!r}: use [fy27.|fy28.]<field>=<spec>")
        if scope in ("", "fy28"):
            new: dict[str, Any] = {field: _target(getattr(y28, field), spec)}
            y28 = replace(y28, **new)
        if scope in ("", "fy27"):
            old = getattr(y27, field)
            w = 1.0 if scope == "fy27" else fy27_weight
            new = {field: old + w * (_target(old, spec) - old)}
            y27 = replace(y27, **new)
    return y27, y28, pe_shift


def market_shocks(market: Market) -> list[str]:
    """Shocks taking the reference-date base to ``market``: FY28 fuel to the live curve, the
    rest of FY27's unhedged fuel by its change (weighted to winter seats), FX and Bank Rate."""
    d27 = (market.fuel_fy27_rest - REFERENCE.fuel_fy27_rest) * WINTER_SEATS / FY27.seats
    return [
        f"fy28.fuel_market_usd={market.fuel_fy28}",
        f"fy27.fuel_market_usd={d27:+}",
        f"usd_market={market.usd}",
        f"eur_market={market.eur}",
        f"base_rate={market.base_rate}",
    ]


def scenario(market: Market, shocks: list[str], fy27_weight: float) -> tuple[Year, Year, float]:
    """Base years moved to live ``market`` inputs, then ``shocks`` applied."""
    return apply(base_years(), market_shocks(market) + shocks, fy27_weight)


# Named scenarios, sized to a typical surprise. Each is (description, shocks).
SCENARIOS: dict[str, tuple[str, list[str]]] = {
    "fuel_spike": ("Jet fuel +25% (conflict escalates)", ["fuel_market_usd=+25%"]),
    "fuel_relief": ("Jet fuel -25% (ceasefire)", ["fuel_market_usd=-25%"]),
    "hedged_more": (
        "H1 results show FY28 fuel 70% hedged at $1,000/t",
        ["fy28.fuel_hedged=0.70", "fy28.fuel_hedge_usd=1000"],
    ),
    "h1_beat": (
        "H1 shows summer yields 1% above what consensus implied; half carries into FY28",
        ["fy27.yield_adj=+0.01", "fy28.yield_adj=+0.005"],
    ),
    "h1_miss": (
        "H1 shows summer yields 1% below what consensus implied; half carries into FY28",
        ["fy27.yield_adj=-0.01", "fy28.yield_adj=-0.005"],
    ),
    "price_war": (
        "Summer 2027 yields -1.5% (easyJet / TUI capacity, more price investment)",
        ["fy28.yield_adj=-0.015"],
    ),
    "strong_summer27": (
        "Summer 2027 books well: yields +1%, load factor +0.5pt",
        ["fy28.yield_adj=+0.01", "fy28.load_factor=+0.005"],
    ),
    "demand_shock": (
        "Geopolitical / consumer shock: load factor -3pts, yields -2%",
        ["load_factor=-0.03", "yield_adj=-0.02"],
    ),
    "gbp_weak": ("Sterling -5% vs USD and EUR", ["usd_market=-5%", "eur_market=-5%"]),
    "rates_up": ("Bank Rate +50bp", ["base_rate=+0.005"]),
    "rates_down": ("Bank Rate -50bp", ["base_rate=-0.005"]),
    "carbon_up": ("SAF and carbon costs +25%", ["saf_cost=+25%", "carbon_cost=+25%"]),
    "gatwick_plus": (
        "Gatwick +3 aircraft for Summer 2027 (~+0.6m seats at 2pts lower load factor, "
        "£10m start-up)",
        ["fy28.seats=+0.6", "fy28.load_factor=-0.0005", "fy28.extra_costs=+10"],
    ),
    "main_market": ("Main Market move -> FTSE 250 inclusion re-rates +0.5x", ["pe=+0.5"]),
}

# --- Valuation ------------------------------------------------------------------------------

PE_HALF_LIFE_WEEKS = 52  # the P/E gap to fair halves in a year
FAIR_PE = 7.5  # on the next full year; Jet2's own 2023-26 weekly trailing P/E median was 7.2x
# Stress floor: half of FY26 book value excluding the £356.6m hedging reserve, per share.
FLOOR = 0.5 * (2060.8 - 356.6) / (SHARES_IN_ISSUE - EBT_SHARES) * 100  # ~476p


@dataclass(frozen=True)
class Valuation:
    eps27: float
    eps28: float
    implied_pe: float  # what the market pays today for consensus FY28 EPS
    horizon_pe: float
    fair_value: float  # pence, at the horizon
    upside: float  # fair value / price - 1


def horizon_pe(price: float, weeks: float, fair_pe: float = FAIR_PE, shift: float = 0.0) -> float:
    """P/E on FY28 at the horizon: today's implied P/E partly reverted toward ``fair_pe``."""
    implied = price / CONSENSUS["FY28"]
    return implied + (fair_pe - implied) * (1 - 0.5 ** (weeks / PE_HALF_LIFE_WEEKS)) + shift


def fair_value(eps27: float, eps28: float, pe: float) -> float:
    """P/E x FY28 EPS + FY27 EPS change vs consensus, floored at ``FLOOR``."""
    return max(pe * eps28 + (eps27 - CONSENSUS["FY27"]), FLOOR)


def value(
    y27: Year,
    y28: Year,
    price: float,
    weeks: float,
    fair_pe: float = FAIR_PE,
    pe_shift: float = 0.0,
) -> Valuation:
    """Fair value at the horizon for the years' drivers."""
    e27, e28 = pnl(y27)["eps"], pnl(y28)["eps"]
    pe = horizon_pe(price, weeks, fair_pe, pe_shift)
    fv = fair_value(e27, e28, pe)
    return Valuation(e27, e28, price / CONSENSUS["FY28"], pe, fv, fv / price - 1)


# --- Monte Carlo ----------------------------------------------------------------------------

# One-year one-sigma uncertainties (judgement, see ADR 0009). Scaled by sqrt(weeks / 52): the
# spread is of what will be known by the horizon, not of the final FY28 outcome.
MC_SIGMA = {
    "fuel": 0.35,  # jet price, lognormal
    "usd": 0.08,
    "eur": 0.06,
    "yield": 0.015,  # FY28 selling prices vs consensus (consensus EPS range ~±30%)
    "load_factor": 0.02,
    "hotel": 0.03,
    "base_rate": 0.01,
    "pe": 0.20,  # lognormal on the horizon P/E
}
PASS_THROUGH_RANGE = (0.10, 0.50)  # FY28


def monte_carlo(
    y27: Year,
    y28: Year,
    price: float,
    weeks: float,
    fy27_weight: float,
    fair_pe: float = FAIR_PE,
    pe_shift: float = 0.0,
    n: int = 2000,
    seed: int = 0,
) -> np.ndarray:
    """Fair values (pence) at the horizon under random draws of what is learned by then."""
    rng = np.random.default_rng(seed)
    s = {k: v * np.sqrt(weeks / 52) for k, v in MC_SIGMA.items()}
    pe0 = horizon_pe(price, weeks, fair_pe, pe_shift)
    out = np.empty(n)
    for i in range(n):
        z = rng.standard_normal(8)
        fuel = np.exp(s["fuel"] * z[0] - s["fuel"] ** 2 / 2)
        usd, eur = 1 + s["usd"] * z[1], 1 + s["eur"] * z[2]
        rate = s["base_rate"] * z[6]
        d28 = replace(
            y28,
            fuel_market_usd=y28.fuel_market_usd * fuel,
            usd_market=y28.usd_market * usd,
            eur_market=y28.eur_market * eur,
            yield_adj=y28.yield_adj + s["yield"] * z[3],
            load_factor=y28.load_factor + s["load_factor"] * z[4],
            hotel_cost=y28.hotel_cost * (1 + s["hotel"] * z[5]),
            base_rate=max(y28.base_rate + rate, 0.0),
            pass_through=rng.uniform(*PASS_THROUGH_RANGE),
        )
        d27 = replace(
            y27,
            fuel_market_usd=y27.fuel_market_usd * (1 + fy27_weight * (fuel - 1)),
            usd_market=y27.usd_market * (1 + fy27_weight * (usd - 1)),
            base_rate=max(y27.base_rate + fy27_weight * rate, 0.0),
        )
        pe = pe0 * np.exp(s["pe"] * z[7] - s["pe"] ** 2 / 2)
        out[i] = fair_value(pnl(d27)["eps"], pnl(d28)["eps"], pe)
    return out


# --- Card -----------------------------------------------------------------------------------

FULL_UPSIDE = 0.25  # +25% upside at the horizon scores +1


@dataclass(frozen=True)
class Jet2ValueCard:
    """Jet2 fair value at the horizon, for the scorecard."""

    date: pd.Timestamp  # market inputs as of
    horizon: pd.Timestamp
    price: float  # pence
    eps27: float  # model EPS (pence) after live inputs and shocks
    eps28: float
    consensus27: float
    consensus28: float
    implied_pe: float  # price / consensus FY28 EPS
    horizon_pe: float
    fair_value: float  # pence, at the horizon
    upside: float
    p_up: float  # share of Monte Carlo fair values above the price
    fv_p10: float
    fv_p50: float
    fv_p90: float
    score: float  # [-1, 1]


def card(
    market: Market,
    shocks: list[str] | None = None,
    weeks: float = 12,
    fair_pe: float = FAIR_PE,
    sims: int = 2000,
) -> tuple[Jet2ValueCard, tuple[Year, Year]]:
    """The card for live ``market`` inputs plus news ``shocks``; also returns (FY27, FY28)."""
    w = fy27_remaining(market.date)
    y27, y28, pe_shift = scenario(market, shocks or [], w)
    v = value(y27, y28, market.price, weeks, fair_pe, pe_shift)
    mc = monte_carlo(y27, y28, market.price, weeks, w, fair_pe, pe_shift, n=sims)
    p10, p50, p90 = np.percentile(mc, [10, 50, 90])
    return Jet2ValueCard(
        date=market.date,
        horizon=market.date + pd.Timedelta(weeks=weeks),
        price=market.price,
        eps27=v.eps27,
        eps28=v.eps28,
        consensus27=CONSENSUS["FY27"],
        consensus28=CONSENSUS["FY28"],
        implied_pe=v.implied_pe,
        horizon_pe=v.horizon_pe,
        fair_value=v.fair_value,
        upside=v.upside,
        p_up=float((mc > market.price).mean()),
        fv_p10=float(p10),
        fv_p50=float(p50),
        fv_p90=float(p90),
        score=float(np.clip(v.upside / FULL_UPSIDE, -1, 1)),
    ), (y27, y28)
