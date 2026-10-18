from __future__ import annotations

import re
from pathlib import Path

import pytest

from solaraalpha.portfolio.rules import RULES, Rules, load_rules


def test_default_file_loads_as_placeholders() -> None:
    r = load_rules()
    assert isinstance(r, Rules)
    assert r.confirmed is False
    assert r.currency == "GBP"
    assert isinstance(r.capital, float) and r.capital > 0
    assert r.horizon_weeks == (2, 16)
    assert r.long_only is True
    assert (r.shortlist_size, r.shortlist_catalysts) == (7, 3) and r.min_adv_gbp > 0
    assert r.warning is not None and "placeholder" in r.warning


def _write(tmp_path: Path, **overrides: str) -> Path:
    text = RULES.read_text()
    for key, value in overrides.items():
        text, n = re.subn(rf"^{key} = .*$", f"{key} = {value}", text, flags=re.M)
        assert n == 1, key
    path = tmp_path / "portfolio.toml"
    path.write_text(text)
    return path


def test_override_file(tmp_path: Path) -> None:
    r = load_rules(_write(tmp_path, capital="25000", confirmed="true", max_positions="5"))
    assert r.capital == 25000.0
    assert r.confirmed is True and r.warning is None
    assert r.max_positions == 5


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("risk_per_trade", "0"),
        ("risk_per_trade", "0.06"),
        ("max_position_pct", "1.5"),
        ("max_sector_pct", "0"),
        ("max_heat", "-0.1"),
        ("max_adv_pct", "2"),
        ("cash_buffer", "1.01"),
        ("max_positions", "0"),
        ("horizon_weeks", "[16, 2]"),
        ("currency", '"USD"'),
        ("capital", '"lots"'),
        ("long_only", "1"),
        ("entry_validity_days", "0"),
        ("shortlist_size", "0"),
        ("shortlist_catalysts", "-1"),
        ("min_adv_gbp", "-5.0"),
    ],
)
def test_validation_names_the_field(tmp_path: Path, key: str, value: str) -> None:
    with pytest.raises(ValueError, match=key):
        load_rules(_write(tmp_path, **{key: value}))


def test_unknown_and_missing_keys_raise(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.toml"
    path.write_text(RULES.read_text() + "\nmax_postions = 3\n")
    with pytest.raises(ValueError, match=r"unknown keys.*max_postions"):
        load_rules(path)
    path.write_text(re.sub(r"^capital = .*$\n", "", RULES.read_text(), flags=re.M))
    with pytest.raises(ValueError, match=r"missing keys.*capital"):
        load_rules(path)
