#!/usr/bin/env python3
"""
Tests for orchestrator session limit handling.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrator.orchestrator import (
    detect_session_limit,
    parse_reset_time,
)


def test_detect_session_limit():
    """Test session limit detection from various output formats."""
    # Test case 1: Standard format
    output1 = "Some error\nSession limit reached ∙ resets 2pm\nMore output"
    result1 = detect_session_limit(output1)
    assert result1 is not None, "Should detect session limit in standard format"
    assert "Session limit reached" in result1
    print(f"✓ Test 1 passed: {result1}")

    # Test case 2: Case insensitive
    output2 = "session limit reached - resets 3:30pm"
    result2 = detect_session_limit(output2)
    assert result2 is not None, "Should detect session limit case-insensitively"
    print(f"✓ Test 2 passed: {result2}")

    # Test case 3: Rate limit variant
    output3 = "rate limit exceeded, resets 11am"
    result3 = detect_session_limit(output3)
    assert result3 is not None, "Should detect rate limit variant"
    print(f"✓ Test 3 passed: {result3}")

    # Test case 4: No session limit
    output4 = "Normal output without any limit message"
    result4 = detect_session_limit(output4)
    assert result4 is None, "Should not detect session limit when not present"
    print("✓ Test 4 passed: No false positives")


def test_parse_reset_time():
    """Test parsing reset time from session limit messages."""
    now = datetime.now()

    # Test case 1: PM time
    msg1 = "Session limit reached ∙ resets 2pm"
    reset1 = parse_reset_time(msg1)
    assert reset1 is not None, "Should parse PM time"
    assert reset1.hour == 14, f"Expected hour 14, got {reset1.hour}"
    assert reset1.minute == 0, f"Expected minute 0, got {reset1.minute}"
    print(f"✓ Test 1 passed: Parsed {msg1} -> {reset1}")

    # Test case 2: AM time
    msg2 = "resets 11am"
    reset2 = parse_reset_time(msg2)
    assert reset2 is not None, "Should parse AM time"
    assert reset2.hour == 11, f"Expected hour 11, got {reset2.hour}"
    print(f"✓ Test 2 passed: Parsed {msg2} -> {reset2}")

    # Test case 3: Time with minutes
    msg3 = "resets 3:30pm"
    reset3 = parse_reset_time(msg3)
    assert reset3 is not None, "Should parse time with minutes"
    assert reset3.hour == 15, f"Expected hour 15, got {reset3.hour}"
    assert reset3.minute == 30, f"Expected minute 30, got {reset3.minute}"
    print(f"✓ Test 3 passed: Parsed {msg3} -> {reset3}")

    # Test case 4: 12pm (noon)
    msg4 = "resets 12pm"
    reset4 = parse_reset_time(msg4)
    assert reset4 is not None, "Should parse 12pm"
    assert reset4.hour == 12, f"Expected hour 12, got {reset4.hour}"
    print(f"✓ Test 4 passed: Parsed {msg4} -> {reset4}")

    # Test case 5: 12am (midnight)
    msg5 = "resets 12am"
    reset5 = parse_reset_time(msg5)
    assert reset5 is not None, "Should parse 12am"
    assert reset5.hour == 0, f"Expected hour 0, got {reset5.hour}"
    print(f"✓ Test 5 passed: Parsed {msg5} -> {reset5}")

    # Test case 6: Invalid message
    msg6 = "No time here"
    reset6 = parse_reset_time(msg6)
    assert reset6 is None, "Should return None for invalid message"
    print("✓ Test 6 passed: Handles invalid message")

    # Test case 7: Future time check (reset time should be in future or tomorrow)
    msg7 = f"resets {now.hour}:{now.minute}am"
    reset7 = parse_reset_time(msg7)
    if reset7 is not None:
        # If parsed time is in the past, it should be set to tomorrow
        if reset7 <= now:
            time_diff = (reset7 - now).total_seconds()
            # Should be approximately 24 hours in the future (within 1 hour margin)
            assert time_diff > 23 * 3600, "Past times should be scheduled for tomorrow"
            print("✓ Test 7 passed: Past times scheduled for tomorrow")
        else:
            print("✓ Test 7 passed: Future time parsed correctly")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Session Limit Detection")
    print("=" * 60)
    test_detect_session_limit()

    print("\n" + "=" * 60)
    print("Testing Reset Time Parsing")
    print("=" * 60)
    test_parse_reset_time()

    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)
