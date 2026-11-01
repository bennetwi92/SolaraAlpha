"""ICB sectors for the universe stocks config/benchmarks.toml has none for (#310).

The weekly scan runs every AIM stock Trading212 offers (ADR 0038), but `sector` (the sector cap,
the macro agent's groups) was on file for the watchlist only. This looks up each missing stock's
ICB sector on the LSE (the same source as config/benchmarks.toml: the `sectorcode` behind the
LSE company page), one request a second, and prints them; --write merges them into
config/sectors.toml. Run by hand, never in CI: the LSE endpoint answers 403 to cloud runners.

uv run python scripts/sectors.py            # print what would be added
uv run python scripts/sectors.py --write    # merge into config/sectors.toml
"""

from __future__ import annotations

import argparse
import sys
import time
import tomllib

from universe import ICB_SECTORS, INSTRUMENT_URL

from solaraalpha.data.http import get_json
from solaraalpha.pipeline import universe
from solaraalpha.portfolio.risk import BENCHMARKS, SECTORS, UNKNOWN_SECTOR, sector_of

GAP = 1.0  # seconds between LSE requests


def lse_sector(tidm: str) -> str | None:
    """The LSE's ICB sector name for a TIDM, or None when its code is not in ICB_SECTORS."""
    data = get_json(INSTRUMENT_URL.format(tidm=tidm))
    code = str(data.get("sectorcode")) if isinstance(data, dict) else ""
    return ICB_SECTORS.get(code)


def render(sectors: dict[str, str], header: str) -> str:
    """config/sectors.toml: the ``header`` comment, then one ``"T.L" = "Sector"`` per stock."""
    body = "".join(f'"{t}" = "{s}"\n' for t, s in sorted(sectors.items()))
    return f"{header}[sectors]\n{body}"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--write", action="store_true", help="merge into config/sectors.toml")
    args = p.parse_args(argv)
    missing = [t for t in universe() if sector_of(t, BENCHMARKS, SECTORS) == UNKNOWN_SECTOR]
    found: dict[str, str] = {}
    for i, t in enumerate(missing):
        if i:
            time.sleep(GAP)
        try:
            name = lse_sector(t.removesuffix(".L"))
        except Exception as e:
            print(f"{t}: LSE lookup failed ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        if name is None:
            print(f"{t}: no known ICB sector on the LSE", file=sys.stderr)
            continue
        found[t] = name
        print(f"{t:<10} {name}")
    print(f"{len(found)} of {len(missing)} stocks without a sector found", file=sys.stderr)
    if args.write and found:
        text = SECTORS.read_text()
        header = text.split("[sectors]")[0]
        with SECTORS.open("rb") as f:
            current = tomllib.load(f).get("sectors", {})
        SECTORS.write_text(render(current | found, header))
        print(f"wrote {SECTORS}", file=sys.stderr)


if __name__ == "__main__":
    main()
