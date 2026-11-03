"""
Tests for Time and Date Formatting Utilities (GMT+5:30 / IST)
"""
from datetime import datetime, timezone
from utils.time_utils import format_ist, get_current_ist_str, IST


def test_format_ist_datetime_object():
    """Verify UTC datetime converts to GMT+5:30 IST accurately."""
    # 2026-11-03 04:30:00 UTC == 2026-11-03 10:00:00 IST
    utc_dt = datetime(2026, 11, 3, 4, 30, 0, tzinfo=timezone.utc)
    res = format_ist(utc_dt)
    assert "2026-11-03 10:00 AM IST" in res


def test_format_ist_sql_string():
    """Verify SQL timestamp string converts to GMT+5:30 IST accurately."""
    sql_str = "2026-11-03 04:30:00"
    res = format_ist(sql_str)
    assert "2026-11-03 10:00 AM IST" in res


def test_format_ist_iso_string():
    """Verify ISO string with T converts accurately."""
    iso_str = "2026-11-03T04:30:00.123456Z"
    res = format_ist(iso_str)
    assert "2026-11-03 10:00 AM IST" in res


def test_format_ist_empty_and_fallback():
    """Verify empty and invalid values fallback safely."""
    assert format_ist(None) == "N/A"
    assert format_ist("") == "N/A"
    assert format_ist("invalid-date-format") == "invalid-date-format"


def test_get_current_ist_str():
    """Verify current IST string has IST suffix."""
    current = get_current_ist_str()
    assert "IST" in current
