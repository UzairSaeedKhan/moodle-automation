"""Dates are handled as 'YYYY-MM-DD' strings in the user's timezone."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # date.weekday() order
_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class LocalNow:
    ymd: str
    minutes: int  # minutes since local midnight
    hhmm: str


def local_now(dt: datetime, tz: str) -> LocalNow:
    """dt must be timezone-aware."""
    loc = dt.astimezone(ZoneInfo(tz))
    return LocalNow(loc.strftime("%Y-%m-%d"), loc.hour * 60 + loc.minute, loc.strftime("%H:%M"))


def _d(ymd: str) -> date:
    return date.fromisoformat(ymd)


def add_days(ymd: str, n: int) -> str:
    return (_d(ymd) + timedelta(days=n)).isoformat()


def weekday_key(ymd: str) -> str:
    return WEEKDAY_KEYS[_d(ymd).weekday()]


def is_weekday(ymd: str) -> bool:
    return _d(ymd).weekday() < 5


def monday_of(ymd: str) -> str:
    return add_days(ymd, -_d(ymd).weekday())


def current_work_week_monday(today: str) -> str:
    """This week's Monday on weekdays, next week's Monday on weekends."""
    return monday_of(today) if is_weekday(today) else monday_of(add_days(today, 2))


def moodle_date_label(ymd: str) -> str:
    """Format used by Moodle's attendance table, e.g. 'Thu 5 Nov 2026'."""
    d = _d(ymd)
    return f"{_WD[d.weekday()]} {d.day} {_MONTHS[d.month - 1]} {d.year}"


def short_label(ymd: str) -> str:
    d = _d(ymd)
    return f"{_WD[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def hhmm_to_minutes(s: str) -> int:
    m = re.fullmatch(r"(\d{1,2})[:h.](\d{2})", s.strip())
    if not m:
        raise ValueError(f'Invalid time "{s}" (expected HH:MM)')
    h, mi = int(m[1]), int(m[2])
    if h > 23 or mi > 59:
        raise ValueError(f'Invalid time "{s}"')
    return h * 60 + mi


def normalize_hhmm(s: str) -> str:
    t = hhmm_to_minutes(s)
    return f"{t // 60:02d}:{t % 60:02d}"


# ---------------------------------------------------------------------------
# "When" parsing for /home, /campus, /off, /reset
# ---------------------------------------------------------------------------


def parse_when(text: str, today: str) -> list[str] | None:
    """Accepts (space/comma separated): today, tomorrow, mon..fri, week, nextweek,
    12/11, 12/11/2026, 23/12-03/01. Returns sorted unique weekdays, or None if not understood."""
    norm = re.sub(r"\s*-\s*", "-", text.lower())
    tokens = [t for t in re.split(r"[\s,]+", norm) if t] or ["today"]
    out: set[str] = set()
    for t in tokens:
        days = _parse_token(t, today)
        if days is None:
            return None
        out.update(d for d in days if is_weekday(d))
    return sorted(out)


def _parse_token(t: str, today: str) -> list[str] | None:
    if t == "today":
        return [today]
    if t in ("tomorrow", "tmr"):
        return [add_days(today, 1)]
    if t in ("week", "thisweek"):
        mon = current_work_week_monday(today)
        return [d for d in _range(mon, add_days(mon, 4)) if d >= today]
    if t == "nextweek":
        mon = add_days(monday_of(today), 7)
        return _range(mon, add_days(mon, 4))
    m = re.fullmatch(r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*", t)
    if m:
        target = WEEKDAY_KEYS.index(m[1])
        return [add_days(today, (target - _d(today).weekday()) % 7)]
    m = re.fullmatch(r"([\d/.]+)-([\d/.]+)", t)
    if m:
        a = _parse_date(m[1], today)
        b = _parse_date(m[2], today)
        if not a or not b:
            return None
        if b < a:  # e.g. 23/12-03/01 crosses the year
            b = _parse_date(m[2], a, strictly_after=True)
        if not b or b < a:
            return None
        return _range(a, b)
    single = _parse_date(t, today)
    return [single] if single else None


def _parse_date(s: str, ref: str, strictly_after: bool = False) -> str | None:
    """dd/mm or dd/mm/yyyy. Without a year: the next occurrence on/after ref."""
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{4}))?", s)
    if not m:
        return None
    day, month = int(m[1]), int(m[2])

    def build(y: int) -> str | None:
        try:
            return date(y, month, day).isoformat()
        except ValueError:
            return None

    if m[3]:
        return build(int(m[3]))
    year = int(ref[:4])
    ymd = build(year)
    if ymd is None:
        return None
    if ymd < ref or (strictly_after and ymd <= ref):
        ymd = build(year + 1)
    return ymd


def _range(a: str, b: str) -> list[str]:
    out: list[str] = []
    d = a
    while d <= b and len(out) < 400:
        out.append(d)
        d = add_days(d, 1)
    return out
