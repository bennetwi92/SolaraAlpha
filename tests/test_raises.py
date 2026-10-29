from __future__ import annotations

from solaraalpha.data.raises import directors_in, issue_price, use_class


def test_issue_price() -> None:
    assert issue_price("a placing of 10m new shares at 4.5 pence per new ordinary share") == 4.5
    assert issue_price("at an issue price of 12p per Placing Share") == 12.0
    assert issue_price("raise £5m at £1.25 per share") == 125.0
    assert issue_price("1,230 pence in cash for each Scheme Share") is None  # not a share issue word
    assert issue_price("shares of 1p each; at 30p per new Ordinary Share") == 30.0
    assert issue_price("no price here") is None


def test_use_class() -> None:
    wc = "The net proceeds will be used for general working capital and to repay the loan."
    assert use_class(wc) == "working_capital"
    mixed = "The proceeds will fund the acquisition and provide working capital."
    assert use_class(mixed) == "other"
    assert use_class("Trading was strong. Working capital improved.") == "other"  # not proceeds


def test_directors_in() -> None:
    assert directors_in("Certain Directors have subscribed for 1m Subscription Shares.")
    assert directors_in("subscriptions by certain of the Directors")
    assert directors_in("The Board intends to participate in the Placing.")
    assert not directors_in("The Placing was oversubscribed.")
