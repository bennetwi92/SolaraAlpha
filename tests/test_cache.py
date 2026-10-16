from datetime import timedelta
from pathlib import Path

import pandas as pd

from solaraalpha.data.cache import parquet_cache


def test_parquet_cache_hits_and_refresh(tmp_cache: Path) -> None:
    calls: list[int] = []

    @parquet_cache(cache_dir=tmp_cache)
    def fetch(n: int) -> pd.DataFrame:
        calls.append(n)
        return pd.DataFrame({"x": range(n)})

    a = fetch(3)
    b = fetch(3)
    pd.testing.assert_frame_equal(a, b)
    assert calls == [3]

    fetch(4)
    assert calls == [3, 4]

    fetch(3, refresh=True)  # type: ignore[call-arg]
    assert calls == [3, 4, 3]


def test_parquet_cache_ttl_expiry(tmp_cache: Path) -> None:
    calls: list[int] = []

    @parquet_cache(ttl=timedelta(seconds=0), cache_dir=tmp_cache)
    def fetch() -> pd.DataFrame:
        calls.append(1)
        return pd.DataFrame({"x": [1]})

    fetch()
    fetch()
    assert len(calls) == 2
