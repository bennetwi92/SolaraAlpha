"""Configuration management for execution system."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class BrokerConfig:
    """Broker API configuration.

    Attributes:
        api_key: Broker API key
        secret_key: Broker secret key
        paper_trading: Use paper trading environment
        base_url: Broker API base URL (optional override)
    """

    api_key: str
    secret_key: str
    paper_trading: bool = True
    base_url: Optional[str] = None


def load_alpaca_config() -> BrokerConfig:
    """Load Alpaca configuration from environment variables.

    Returns:
        BrokerConfig with Alpaca credentials

    Raises:
        ValueError: If required environment variables not set
    """
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    paper_trading = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    if not api_key or not secret_key:
        raise ValueError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment"
        )

    base_url = None
    if paper_trading:
        base_url = "https://paper-api.alpaca.markets"

    return BrokerConfig(
        api_key=api_key,
        secret_key=secret_key,
        paper_trading=paper_trading,
        base_url=base_url,
    )
