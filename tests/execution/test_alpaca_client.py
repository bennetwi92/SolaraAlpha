"""Tests for Alpaca client (mocked, no actual API calls)."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

from src.execution.clients.alpaca_client import AlpacaClient
from src.execution.clients.base import Order, OrderSide, OrderStatus, OrderType
from src.execution.config import BrokerConfig


class TestAlpacaClient:
    """Test suite for AlpacaClient (mocked)."""

    @pytest.fixture
    def mock_config(self):
        """Create mock broker config."""
        return BrokerConfig(
            api_key="test_key",
            secret_key="test_secret",
            paper_trading=True,
            base_url="https://paper-api.alpaca.markets",
        )

    @pytest.fixture
    def mock_trading_client(self):
        """Create mock TradingClient."""
        with patch("src.execution.clients.alpaca_client.TradingClient") as mock:
            yield mock

    @pytest.fixture
    def mock_trading_stream(self):
        """Create mock TradingStream."""
        with patch("src.execution.clients.alpaca_client.TradingStream") as mock:
            yield mock

    def test_connect_success(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test successful connection."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()
        mock_trading_client.return_value = mock_client_instance

        client = AlpacaClient(mock_config)
        client.connect()

        assert client._connected is True
        mock_trading_client.assert_called_once()
        mock_client_instance.get_account.assert_called_once()

    def test_connect_failure(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test connection failure."""
        mock_trading_client.side_effect = Exception("Connection failed")

        client = AlpacaClient(mock_config)

        with pytest.raises(ConnectionError, match="Failed to connect"):
            client.connect()

    def test_disconnect(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test disconnection."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()
        mock_trading_client.return_value = mock_client_instance

        mock_stream_instance = Mock()
        mock_trading_stream.return_value = mock_stream_instance

        client = AlpacaClient(mock_config)
        client.connect()
        client.disconnect()

        assert client._connected is False

    def test_submit_market_order(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test submitting market order."""
        # Setup mocks
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()

        mock_alpaca_order = Mock()
        mock_alpaca_order.id = "alpaca-123"
        mock_alpaca_order.status = "new"
        mock_client_instance.submit_order.return_value = mock_alpaca_order

        mock_trading_client.return_value = mock_client_instance

        # Create client and connect
        client = AlpacaClient(mock_config)
        client.connect()

        # Submit order
        order = Order(
            order_id=None,
            client_order_id="test-123",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)

        assert submitted.order_id == "alpaca-123"
        assert submitted.status == OrderStatus.SUBMITTED
        assert submitted.broker == "alpaca"

    def test_submit_limit_order(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test submitting limit order."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()

        mock_alpaca_order = Mock()
        mock_alpaca_order.id = "alpaca-456"
        mock_alpaca_order.status = "new"
        mock_client_instance.submit_order.return_value = mock_alpaca_order

        mock_trading_client.return_value = mock_client_instance

        client = AlpacaClient(mock_config)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-456",
            symbol="MSFT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=50.0,
            limit_price=300.0,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)

        assert submitted.order_id == "alpaca-456"
        assert submitted.broker == "alpaca"

    def test_submit_order_invalid_quantity(
        self, mock_config, mock_trading_client, mock_trading_stream
    ):
        """Test that invalid quantity raises error."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()
        mock_trading_client.return_value = mock_client_instance

        client = AlpacaClient(mock_config)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-invalid",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        with pytest.raises(ValueError, match="Invalid quantity"):
            client.submit_order(order)

    def test_submit_limit_order_without_price(
        self, mock_config, mock_trading_client, mock_trading_stream
    ):
        """Test that limit order without price raises error."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()
        mock_trading_client.return_value = mock_client_instance

        client = AlpacaClient(mock_config)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-no-price",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        with pytest.raises(ValueError, match="Limit price required"):
            client.submit_order(order)

    def test_cancel_order(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test cancelling order."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()

        mock_alpaca_order = Mock()
        mock_alpaca_order.id = "alpaca-123"
        mock_alpaca_order.client_order_id = "test-123"
        mock_alpaca_order.symbol = "AAPL"
        mock_alpaca_order.side = "buy"
        mock_alpaca_order.type = "market"
        mock_alpaca_order.qty = 100.0
        mock_alpaca_order.limit_price = None
        mock_alpaca_order.status = "canceled"
        mock_alpaca_order.filled_qty = 0
        mock_alpaca_order.filled_avg_price = None
        mock_alpaca_order.submitted_at = datetime.now(timezone.utc)
        mock_alpaca_order.filled_at = None

        mock_client_instance.get_order_by_id.return_value = mock_alpaca_order
        mock_trading_client.return_value = mock_client_instance

        client = AlpacaClient(mock_config)
        client.connect()

        cancelled = client.cancel_order("alpaca-123")

        assert cancelled.status == OrderStatus.CANCELLED
        mock_client_instance.cancel_order_by_id.assert_called_once_with("alpaca-123")

    def test_get_order_status(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test getting order status."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()

        mock_alpaca_order = Mock()
        mock_alpaca_order.id = "alpaca-123"
        mock_alpaca_order.client_order_id = "test-123"
        mock_alpaca_order.symbol = "AAPL"
        mock_alpaca_order.side = "buy"
        mock_alpaca_order.type = "market"
        mock_alpaca_order.qty = 100.0
        mock_alpaca_order.limit_price = None
        mock_alpaca_order.status = "filled"
        mock_alpaca_order.filled_qty = 100.0
        mock_alpaca_order.filled_avg_price = 150.0
        mock_alpaca_order.submitted_at = datetime.now(timezone.utc)
        mock_alpaca_order.filled_at = datetime.now(timezone.utc)

        mock_client_instance.get_order_by_id.return_value = mock_alpaca_order
        mock_trading_client.return_value = mock_client_instance

        client = AlpacaClient(mock_config)
        client.connect()

        order = client.get_order_status("alpaca-123")

        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 100.0
        assert order.avg_fill_price == 150.0

    def test_get_positions(self, mock_config, mock_trading_client, mock_trading_stream):
        """Test getting positions."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()

        mock_pos1 = Mock()
        mock_pos1.symbol = "AAPL"
        mock_pos1.qty = 100.0

        mock_pos2 = Mock()
        mock_pos2.symbol = "MSFT"
        mock_pos2.qty = 50.0

        mock_client_instance.get_all_positions.return_value = [mock_pos1, mock_pos2]
        mock_trading_client.return_value = mock_client_instance

        client = AlpacaClient(mock_config)
        client.connect()

        positions = client.get_positions()

        assert positions["AAPL"] == 100.0
        assert positions["MSFT"] == 50.0

    def test_not_connected_raises_error(self, mock_config):
        """Test that operations without connection raise error."""
        client = AlpacaClient(mock_config)

        order = Order(
            order_id=None,
            client_order_id="test",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        with pytest.raises(ConnectionError, match="Not connected"):
            client.submit_order(order)

    def test_convert_status(self, mock_config):
        """Test Alpaca status conversion."""
        client = AlpacaClient(mock_config)

        assert client._convert_status("pending_new") == OrderStatus.PENDING
        assert client._convert_status("accepted") == OrderStatus.SUBMITTED
        assert client._convert_status("new") == OrderStatus.SUBMITTED
        assert client._convert_status("partially_filled") == OrderStatus.PARTIALLY_FILLED
        assert client._convert_status("filled") == OrderStatus.FILLED
        assert client._convert_status("canceled") == OrderStatus.CANCELLED
        assert client._convert_status("rejected") == OrderStatus.REJECTED
        assert client._convert_status("expired") == OrderStatus.CANCELLED
        assert client._convert_status("unknown") == OrderStatus.PENDING

    def test_register_fill_callback(
        self, mock_config, mock_trading_client, mock_trading_stream
    ):
        """Test registering fill callback."""
        mock_client_instance = Mock()
        mock_client_instance.get_account.return_value = Mock()
        mock_trading_client.return_value = mock_client_instance

        mock_stream_instance = Mock()
        mock_trading_stream.return_value = mock_stream_instance

        client = AlpacaClient(mock_config)
        client.connect()

        callback = Mock()
        client.register_fill_callback(callback)

        assert client._fill_callback == callback
