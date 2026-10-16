"""Print the plain text of one NSM announcement (``url`` from an event pack), for reading.

uv run python scripts/announcement.py https://data.fca.org.uk/artefacts/NSM/RNS/<id>.html
"""

from __future__ import annotations

import argparse

from solaraalpha.data.announcements import fetch_text


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", help="NSM announcement URL")
    print(fetch_text(p.parse_args().url))


if __name__ == "__main__":
    main()
