"""Disk cache for functions that return DataFrames.

Free data sources are rate-limited and slow; wrap any fetch function so repeated
calls during research hit local parquet instead of the network::

    @parquet_cache(ttl=timedelta(days=1))
    def fetch_something(name: str, start: str) -> pd.DataFrame: ...

    fetch_something("x", "2020-01-01")               # network, then cached
    fetch_something("x", "2020-01-01")               # from disk
    fetch_something("x", "2020-01-01", refresh=True) # force re-fetch

The cache key is the function's qualified name plus its arguments, so arguments
must have a stable ``repr`` (str, int, float, date, tuples of those, ...).
"""

from __future__ import annotations

import functools
import hashlib
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import ParamSpec

import pandas as pd

from solaraalpha.config import settings
from solaraalpha.log import get_logger

P = ParamSpec("P")
log = get_logger(__name__)


def _key(args: tuple[object, ...], kwargs: dict[str, object]) -> str:
    raw = repr((args, sorted(kwargs.items())))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def parquet_cache(
    ttl: timedelta | None = None,
    cache_dir: Path | None = None,
) -> Callable[[Callable[P, pd.DataFrame]], Callable[P, pd.DataFrame]]:
    """Cache a DataFrame-returning function to parquet. ``ttl=None`` means never expire.

    The wrapped function accepts an extra keyword ``refresh=True`` to bypass the cache.
    """

    def decorator(func: Callable[P, pd.DataFrame]) -> Callable[P, pd.DataFrame]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> pd.DataFrame:
            refresh = bool(kwargs.pop("refresh", False))
            base = (cache_dir or settings.cache_dir) / f"{func.__module__}.{func.__qualname__}"
            path = base / f"{_key(args, kwargs)}.parquet"

            if not refresh and path.exists():
                age = time.time() - path.stat().st_mtime
                if ttl is None or age < ttl.total_seconds():
                    log.debug("cache hit %s", path)
                    return pd.read_parquet(path)

            df = func(*args, **kwargs)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path)
            return df

        return wrapper

    return decorator
