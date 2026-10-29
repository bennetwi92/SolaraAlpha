"""Shared HTTP session for free web APIs: retries, backoff and a polite User-Agent."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "solaraalpha-research/0.1"
DEFAULT_TIMEOUT = 30


@cache
def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers["User-Agent"] = USER_AGENT
    return s


def get_json(url: str, **params: str | int | float) -> object:
    resp = session().get(url, params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def patient[T](fn: Callable[..., T], *args: object, tries: int = 8, wait: float = 5.0) -> T:
    """``fn(*args)``, retried after an HTTP 403 / 429 with doubling waits. For bulk research
    reads of the FCA NSM, whose bot protection answers 403 to bursts (#290)."""
    for i in range(tries):
        try:
            return fn(*args)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code not in (403, 429) or i == tries - 1:
                raise
            time.sleep(wait * 2**i)
    raise AssertionError("unreachable")
