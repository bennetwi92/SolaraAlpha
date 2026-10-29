from __future__ import annotations

import pandas as pd
import pytest

from solaraalpha.data import leavers
from solaraalpha.data.leavers import (
    CONTINUING,
    classify,
    is_leaver_move,
    offer_price,
    parse_notice,
    takeover_price,
)

T = pd.Timestamp("2024-01-18 07:00", tz="UTC")

CANCEL = """RNS Number : 1234A
AIM
18 January 2024
NOTICE OF CANCELLATION OF ADMISSION TO TRADING ON AIM
Pursuant to AIM Rule 1 the following securities have been cancelled from trading on AIM with \
effect from the time and date of this notice.
ROTALA PLC
213800ABCDEFGHIJKL12
ORDINARY SHARES OF 25P EACH FULLY PAID | (0123456)(GB0001234567) | ROTALA/PAR VTG FPD 0.25
If you have any queries or require further information, please contact the company's nominated \
adviser.
"""
MOVE = """NOTICE OF CANCELLATION OF ADMISSION TO TRADING ON AIM
Following a Transfer to the Main Market the following securities have been cancelled from \
trading on AIM with effect from the time and date of this notice.
GB GROUP PLC
213800RBAFZIBCV7XR29
ORDINARY SHARES OF 2.5P EACH | (0399857)(GB0006870611) | GB GROUP/PAR VTG FPD 0.025
"""


def _filings(heads: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "published": [pd.Timestamp(d, tz="UTC") for d, _ in heads],
            "headline": [h for _, h in heads],
            "id": [f"id{i}" for i in range(len(heads))],
            "url": [f"https://x/{i}" for i in range(len(heads))],
        }
    )


def test_parse_notice_cancellation_and_move() -> None:
    n = parse_notice(CANCEL, T, "d1")
    assert n is not None
    assert (n.company, n.lei, n.isins, n.rule, n.reason) == (
        "ROTALA PLC",
        "213800ABCDEFGHIJKL12",
        ("GB0001234567",),
        "1",
        "",
    )
    m = parse_notice(MOVE, T, "d2")
    assert m is not None and m.reason == "Transfer to the Main Market" and m.rule == ""
    assert is_leaver_move(m.reason)
    assert is_leaver_move("transfer from AIM to MAIN")
    assert is_leaver_move("Transfer to Specialist Fund Segment")
    assert not is_leaver_move("Reverse Takeover") and not is_leaver_move("Redomicile")
    assert parse_notice("no lei here", T, "d3") is None


@pytest.mark.parametrize(
    ("heads", "rule", "how"),
    [
        (
            [("2023-11-01", "Recommended Cash Offer"), ("2024-01-17", "Scheme Effective")],
            "",
            "takeover",
        ),
        ([("2023-12-01", "Form 8 (OPD) - X plc"), ("2024-01-10", "Result of GM")], "", "takeover"),
        ([("2023-12-01", "Intention to Appoint Administrators")], "1", "administration"),
        (
            [("2023-12-01", "Proposed Cancellation of AIM"), ("2024-01-02", "Result of GM")],
            "",
            "cancellation",
        ),
        ([("2023-12-01", "Last Day of Dealings")], "", "cancellation"),
        ([("2023-12-01", "Cancellation of Treasury Shares")], "", "other"),
        ([("2023-12-01", "Update")], "1", "suspension"),
        (
            [("2023-08-01", "Suspension of trading"), ("2023-12-01", "Resignation of Nomad")],
            "",
            "suspension",
        ),
        ([("2023-12-01", "Final Results")], "", "other"),
        ([("2024-01-20", "Admission to AIM and First Day of Dealings")], "", CONTINUING),
        ([("2024-01-20", "Cancellation of admission to trading on AIM")], "", "other"),
        ([("2022-06-01", "Recommended Cash Offer")], "", "other"),  # over a year before
    ],
)
def test_classify(heads: list[tuple[str, str]], rule: str, how: str) -> None:
    n = parse_notice(CANCEL.replace("AIM Rule 1", f"AIM Rule {rule}" if rule else "x"), T, "d")
    assert n is not None
    assert classify(n, _filings(heads))[0] == how


def test_offer_price_reads_the_targets_cash_price() -> None:
    assert offer_price("entitled to receive 1,230 pence in cash for each Scheme Share") == 1230
    assert offer_price("will receive 401 pence in cash for each Scheme Share.") == 401
    assert offer_price("an offer of £2.15 per Windward Share", "Windward Ltd") == 215
    assert offer_price("Closing Price of 11.625 pence per finnCap Share", "Cenkos") is None
    assert offer_price("0.25p per ordinary share") == 0.25
    assert offer_price("Final Results") is None


def test_takeover_price_latest_offer_and_all_share(monkeypatch: pytest.MonkeyPatch) -> None:
    texts = {
        "https://x/0": "Recommended cash offer: 300 pence per Scheme Share",
        "https://x/1": "Increased offer: 330 pence in cash for each Scheme Share",
        "https://x/2": "Trading update",
    }
    monkeypatch.setattr(leavers, "fetch_text", lambda u: texts[u])
    n = parse_notice(CANCEL, T, "d")
    assert n is not None
    f = _filings(
        [
            ("2023-10-01", "Recommended Cash Offer"),
            ("2023-12-01", "Increased Final Offer"),
            ("2024-01-01", "Trading Update"),
        ]
    )
    assert takeover_price(f, n) == (330.0, "id1")
    allshare = _filings([("2023-10-01", "Recommended All-Share Merger")])
    assert takeover_price(allshare, n) == (None, "")
