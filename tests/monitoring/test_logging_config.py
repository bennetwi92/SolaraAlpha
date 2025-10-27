"""Tests for centralized logging configuration."""

import json
import logging
import tempfile
from pathlib import Path

import pytest

from src.monitoring.logging_config import JSONFormatter, get_logger, setup_logging


class TestJSONFormatter:
    """Test JSON log formatter."""

    def test_basic_formatting(self):
        """Test basic log record formatting."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.module",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)
        data = json.loads(result)

        assert data["level"] == "INFO"
        assert data["component"] == "test.module"
        assert data["message"] == "Test message"
        assert "timestamp" in data

    def test_formatting_with_context(self):
        """Test log formatting with context dict."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.module",
            level=logging.WARNING,
            pathname="test.py",
            lineno=10,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        record.context = {"user_id": 123, "action": "login"}

        result = formatter.format(record)
        data = json.loads(result)

        assert data["context"]["user_id"] == 123
        assert data["context"]["action"] == "login"

    def test_formatting_with_exception(self):
        """Test log formatting with exception info."""
        formatter = JSONFormatter()

        try:
            raise ValueError("Test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test.module",
            level=logging.ERROR,
            pathname="test.py",
            lineno=10,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        result = formatter.format(record)
        data = json.loads(result)

        assert data["level"] == "ERROR"
        assert "exception" in data
        assert "ValueError: Test error" in data["exception"]

    def test_timestamp_format(self):
        """Test timestamp is ISO format with timezone."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)
        data = json.loads(result)

        # Should be ISO format with timezone
        timestamp = data["timestamp"]
        assert "T" in timestamp
        assert timestamp.endswith("Z") or "+" in timestamp


class TestSetupLogging:
    """Test logging setup function."""

    def test_creates_log_directory(self):
        """Test that log directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "test_logs"
            assert not log_dir.exists()

            setup_logging(log_dir=str(log_dir))

            assert log_dir.exists()

    def test_creates_log_file(self):
        """Test that log file is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            setup_logging(log_dir=str(log_dir))

            log_file = log_dir / "app.log"
            assert log_file.exists()

    def test_log_level_configuration(self):
        """Test that log level is properly configured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=str(tmpdir), log_level="DEBUG")

            root_logger = logging.getLogger()
            assert root_logger.level == logging.DEBUG

    def test_logging_writes_json(self):
        """Test that logs are written in JSON format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            setup_logging(log_dir=str(log_dir), log_level="INFO")

            logger = logging.getLogger("test.component")
            logger.info("Test message", extra={"context": {"key": "value"}})

            # Read log file
            log_file = log_dir / "app.log"
            content = log_file.read_text()

            # Should contain JSON
            lines = [line for line in content.split("\n") if line.strip()]
            assert len(lines) >= 1

            # Parse first JSON line
            data = json.loads(lines[0])
            assert data["level"] == "INFO"
            assert "timestamp" in data

    def test_multiple_handlers(self):
        """Test that both file and console handlers are configured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=str(tmpdir))

            root_logger = logging.getLogger()
            assert len(root_logger.handlers) >= 2  # File + console

    def test_log_rotation_config(self):
        """Test that rotating file handler is configured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            max_bytes = 5 * 1024 * 1024  # 5MB
            backup_count = 3

            setup_logging(
                log_dir=str(tmpdir), max_bytes=max_bytes, backup_count=backup_count
            )

            root_logger = logging.getLogger()
            file_handlers = [
                h for h in root_logger.handlers if hasattr(h, "maxBytes")
            ]

            assert len(file_handlers) >= 1
            handler = file_handlers[0]
            assert handler.maxBytes == max_bytes
            assert handler.backupCount == backup_count


class TestGetLogger:
    """Test logger retrieval function."""

    def test_returns_logger(self):
        """Test that get_logger returns a logger instance."""
        logger = get_logger("test.module")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_logger_inherits_config(self):
        """Test that logger uses centralized configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=str(tmpdir), log_level="DEBUG")

            logger = get_logger("test.module")

            # Should be able to log at DEBUG level
            logger.debug("Debug message")

            # Check log file
            log_file = Path(tmpdir) / "app.log"
            content = log_file.read_text()

            assert "Debug message" in content

    def test_multiple_loggers(self):
        """Test that multiple loggers can be created."""
        logger1 = get_logger("module1")
        logger2 = get_logger("module2")

        assert logger1.name == "module1"
        assert logger2.name == "module2"
        assert logger1 is not logger2


class TestIntegration:
    """Integration tests for logging system."""

    def test_end_to_end_logging(self):
        """Test complete logging flow from setup to write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            setup_logging(log_dir=str(log_dir), log_level="INFO")

            # Create logger and log messages
            logger = get_logger("test.integration")
            logger.info("Info message", extra={"context": {"user": "test"}})
            logger.warning("Warning message")
            logger.error("Error message")

            # Read and verify log file
            log_file = log_dir / "app.log"
            content = log_file.read_text()
            lines = [line for line in content.split("\n") if line.strip()]

            # Should have at least 4 lines (setup message + 3 test messages)
            assert len(lines) >= 4

            # Verify JSON format
            for line in lines:
                data = json.loads(line)
                assert "timestamp" in data
                assert "level" in data
                assert "component" in data
                assert "message" in data

    def test_context_propagation(self):
        """Test that context is properly propagated through logging."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=str(tmpdir))

            logger = get_logger("test.context")
            context_data = {
                "symbol": "AAPL",
                "quantity": 100,
                "price": 150.0,
            }
            logger.info("Trade executed", extra={"context": context_data})

            # Read log
            log_file = Path(tmpdir) / "app.log"
            content = log_file.read_text()
            lines = [line for line in content.split("\n") if line.strip()]

            # Find the trade executed message
            for line in lines:
                data = json.loads(line)
                if data.get("message") == "Trade executed":
                    assert data["context"]["symbol"] == "AAPL"
                    assert data["context"]["quantity"] == 100
                    assert data["context"]["price"] == 150.0
                    break
            else:
                pytest.fail("Trade executed message not found in logs")
