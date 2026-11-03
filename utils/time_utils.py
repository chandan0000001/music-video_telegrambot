"""
Time and Date Formatting Utilities
Provides conversion of UTC database timestamps to Indian Standard Time (GMT+5:30 / IST).
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, Union

# GMT+5:30 (Indian Standard Time)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def format_ist(dt_or_str: Optional[Union[datetime, str]], fmt: str = "%Y-%m-%d %I:%M %p IST") -> str:
    """Convert UTC datetime or ISO/SQL timestamp string to GMT+5:30 (IST).

    Examples:
        format_ist("2026-11-03 04:30:00") -> "2026-11-03 10:00 AM IST"
    """
    if not dt_or_str:
        return "N/A"

    if isinstance(dt_or_str, datetime):
        if dt_or_str.tzinfo is None:
            dt_or_str = dt_or_str.replace(tzinfo=timezone.utc)
        return dt_or_str.astimezone(IST).strftime(fmt)

    raw = str(dt_or_str).strip()
    try:
        clean_str = raw.replace("Z", "").replace("T", " ").split(".")[0]
        if " " in clean_str:
            parsed = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        else:
            parsed = datetime.strptime(clean_str, "%Y-%m-%d")
        utc_dt = parsed.replace(tzinfo=timezone.utc)
        return utc_dt.astimezone(IST).strftime(fmt)
    except Exception:
        return raw


def get_current_ist_str(fmt: str = "%Y-%m-%d %I:%M %p IST") -> str:
    """Return the current time formatted in GMT+5:30."""
    return datetime.now(IST).strftime(fmt)
