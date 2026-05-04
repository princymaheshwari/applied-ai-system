"""Locale and timezone detection.

These are common sources of "works on my machine" bugs:
- LANG=C vs LANG=en_US.UTF-8 affects string encoding
- TZ differences cause timestamp mismatches in tests
- LC_* variables affect sorting, date formatting, etc.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from config_detective.snapshot.models import LocaleInfo, TimezoneInfo


def _read_file_safely(path: str | Path) -> str | None:
    """Read a file's contents, returning None on any error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _readlink_safely(path: str | Path) -> str | None:
    """Read a symlink target, returning None on any error."""
    try:
        target = Path(path).resolve()
        return str(target)
    except OSError:
        return None


def capture_locale() -> LocaleInfo:
    """Capture locale-related environment settings.

    These variables affect:
    - LANG: Default locale for all LC_* categories
    - LC_ALL: Overrides all LC_* variables
    - LC_CTYPE: Character classification and case conversion
    - LANGUAGE: GNU gettext language preference
    """
    return LocaleInfo(
        lang=os.environ.get("LANG"),
        lc_all=os.environ.get("LC_ALL"),
        lc_ctype=os.environ.get("LC_CTYPE"),
        language=os.environ.get("LANGUAGE"),
    )


def capture_timezone() -> TimezoneInfo:
    """Capture timezone configuration.

    Sources:
    - TZ environment variable (highest priority)
    - /etc/timezone (Debian/Ubuntu)
    - /etc/localtime symlink target (RHEL/macOS)
    """
    tz_env = os.environ.get("TZ")

    etc_timezone: str | None = None
    etc_localtime_link: str | None = None

    system = platform.system().lower()

    if system != "windows":
        # Read /etc/timezone (Debian/Ubuntu)
        etc_timezone = _read_file_safely("/etc/timezone")

        # Read /etc/localtime symlink target
        localtime_path = Path("/etc/localtime")
        if localtime_path.exists():
            if localtime_path.is_symlink():
                target = _readlink_safely(localtime_path)
                if target:
                    # Extract timezone from path like /usr/share/zoneinfo/America/New_York
                    if "zoneinfo/" in target:
                        etc_localtime_link = target.split("zoneinfo/")[-1]
                    else:
                        etc_localtime_link = target
            else:
                # If it's not a symlink, try to identify timezone by comparing
                # with zoneinfo files. This is expensive, so we just note it exists.
                etc_localtime_link = "(file, not symlink)"

    return TimezoneInfo(
        tz_env=tz_env,
        etc_timezone=etc_timezone,
        etc_localtime_link=etc_localtime_link,
    )


def get_effective_timezone() -> str:
    """Get the effective timezone that Python would use.

    This is what datetime.now().tzinfo would see (if aware).
    """
    try:
        # Python 3.9+ has zoneinfo
        import time

        # time.timezone is seconds west of UTC (negative for east)
        # time.daylight indicates if DST is observed
        # time.tzname is a tuple of (standard, dst) names

        tz_name = time.tzname[0] if time.tzname else "Unknown"

        # Also try to get the IANA timezone name
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            # Try common methods to detect timezone
            tz_env = os.environ.get("TZ")
            if tz_env:
                return tz_env

            # On Linux, read /etc/timezone
            etc_tz = _read_file_safely("/etc/timezone")
            if etc_tz:
                return etc_tz

            # Fall back to abbreviation
            return tz_name

        except ImportError:
            return tz_name

    except Exception:
        return "Unknown"


def get_effective_locale() -> str:
    """Get the effective locale that Python would use.

    This is what locale.getlocale() would return.
    """
    try:
        import locale

        # Get current locale
        current = locale.getlocale()
        if current and current[0]:
            return f"{current[0]}.{current[1]}" if current[1] else current[0]

        # Fall back to environment
        lang = os.environ.get("LC_ALL") or os.environ.get("LANG") or "C"
        return lang

    except Exception:
        return "Unknown"
