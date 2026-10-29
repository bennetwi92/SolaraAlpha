from __future__ import annotations

import math

import pytest

from solaraalpha.data.dealings import Dealing, is_open_market_buy, parse_dsh, value_gbp

INLINE = """Notification and public disclosure of transactions
1. | Details of the person discharging managerial responsibilities / person closely associated
a) | Name | Ben Hodges
2. | Reason for the Notification
a) | Position/status | Chief Financial Officer
3. | Details of the issuer
a) | Name | Arcontech Group Plc
b) | LEI | 213800O7PM9V79TP7523
4. | Details of the transaction(s)
b) | Nature of the transactions | Purchase of Ordinary Shares
c) | Price(s) and volume(s) | | Price(s) | Volume(s) | 77.25 | 6,472
d) | Aggregated information: · Aggregated volumes · Prices | See 4(c) above
e) | Date of the transaction | 6 February 2026
f) | Place of the transaction | London Stock Exchange
"""

TABLE = """1 | Details of the person discharging managerial responsibilities / person closely associated
a) | Name | Charles Bligh
2 | Reason for the notification
b) | Nature of the transaction | Purchase of ordinary shares of 5 pence each
c) | Price(s) and volume(s)
Price(s) | Volume(s)
314 pence | 3,178
d) | Aggregated information
- Price | 314 pence
e) | Date of the transaction | 22 March 2023
1 | Details of the person discharging managerial responsibilities / person closely associated
a) | Name | Mrs Bligh
b) | Nature of the transaction | Sale of ordinary shares
c) | Price(s) and volume(s) | £3.12 | 6,371
e) | Date of the transaction | 22 March 2023
"""


def test_parse_inline_notice() -> None:
    [d] = parse_dsh(INLINE)
    assert d.pdmr == "Ben Hodges"
    assert d.nature == "Purchase of Ordinary Shares"
    assert d.pairs == ((77.25, 6472.0),)
    assert d.unit == ""
    assert d.date == "6 February 2026"


def test_parse_table_two_pdmrs_units() -> None:
    a, b = parse_dsh(TABLE)
    assert (a.pdmr, a.pairs, a.unit) == ("Charles Bligh", ((314.0, 3178.0),), "GBp")
    assert (b.pdmr, b.pairs, b.unit) == ("Mrs Bligh", ((3.12, 6371.0),), "GBP")
    assert is_open_market_buy(a.nature) and not is_open_market_buy(b.nature)
    assert parse_dsh("no template here") == []


@pytest.mark.parametrize(
    ("nature", "buy"),
    [
        ("Purchase of Ordinary Shares", True),
        ("Market purchase", True),
        ("Acquisition of shares", True),
        ("Purchase by ISA", True),
        ("Purchase of shares under the Share Incentive Plan", False),
        ("Exercise of options and sale of shares", False),
        ("Grant of options", False),
        ("Subscription for shares in the Placing", False),
        ("Gift of shares to spouse", False),
        ("Sale of ordinary shares", False),
    ],
)
def test_open_market_buy_rules(nature: str, buy: bool) -> None:
    assert is_open_market_buy(nature) is buy


def test_value_gbp_units() -> None:
    pence = Dealing("x", "Purchase", ((77.25, 6472.0),), "GBp", "")
    assert value_gbp(pence, None) == pytest.approx(4999.62)
    pounds = Dealing("x", "Purchase", ((3.12, 1000.0),), "GBP", "")
    assert value_gbp(pounds, None) == pytest.approx(3120.0)
    unmarked = Dealing("x", "Purchase", ((0.7725, 6472.0),), "", "")
    assert value_gbp(unmarked, 77.0) == pytest.approx(4999.62)  # nearer £0.77 = 77p: pounds
    unmarked_p = Dealing("x", "Purchase", ((77.25, 6472.0),), "", "")
    assert value_gbp(unmarked_p, 77.0) == pytest.approx(4999.62)  # nearer 77p: pence
    usd = Dealing("x", "Purchase", ((2.0, 1000.0),), "USD", "")
    assert value_gbp(usd, None, {"USD": 0.8}) == pytest.approx(1600.0)
    assert math.isnan(value_gbp(usd, None, {}))
