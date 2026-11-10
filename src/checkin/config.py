from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Callable, Literal

Mode = Literal["campus", "home", "off"]
MODES: tuple[Mode, ...] = ("campus", "home", "off")

MODE_LABEL: dict[str, str] = {"campus": "🏫 On campus", "home": "🏠 At home", "off": "❌ Off"}
MODE_EMOJI: dict[str, str] = {"campus": "🏫", "home": "🏠", "off": "❌"}


@dataclass(frozen=True)
class Slot:
    id: str  # am_in | am_out | pm_in | pm_out
    action: Literal["checkin", "checkout"]
    half: Literal["am", "pm"]
    label: str


SLOTS: tuple[Slot, ...] = (
    Slot("am_in", "checkin", "am", "Morning check-in"),
    Slot("am_out", "checkout", "am", "Morning check-out"),
    Slot("pm_in", "checkin", "pm", "Afternoon check-in"),
    Slot("pm_out", "checkout", "pm", "Afternoon check-out"),
)
SLOT_IDS = tuple(s.id for s in SLOTS)
DEFAULT_TIMES = {"am_in": "09:00", "am_out": "12:30", "pm_in": "13:30", "pm_out": "17:00"}


def is_mode(v: object) -> bool:
    return v in MODES


def parse_pattern(s: str | None) -> dict[str, str]:
    """'mon:home,tue:campus' -> {'mon': 'home', 'tue': 'campus'}"""
    out: dict[str, str] = {}
    for part in (s or "").split(","):
        day, _, mode = part.partition(":")
        day, mode = day.strip().lower(), mode.strip().lower()
        if day and is_mode(mode):
            out[day[:3]] = mode
    return out


@dataclass(frozen=True)
class DayRule:
    """One DAY_OVERRIDES entry: a date or date range (with or without year) -> mode."""

    start: tuple[int, int, int]  # (year or 0, month, day)
    end: tuple[int, int, int]
    mode: str

    def matches(self, ymd: str) -> bool:
        y, m, d = (int(x) for x in ymd.split("-"))
        if self.start[0] and self.end[0]:
            return self.start <= (y, m, d) <= self.end
        a, b, k = self.start[1:], self.end[1:], (m, d)
        return a <= k <= b if a <= b else (k >= a or k <= b)  # e.g. 23/12-03/01 wraps the year


_DATE = r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{4}))?"


def parse_day_overrides(raw: str | None) -> tuple[list[DayRule], list[str]]:
    """'12/11:home, 23/12-03/01:off, 01/12/2026:campus' -> (rules, invalid entries)"""
    rules: list[DayRule] = []
    bad: list[str] = []
    for part in re.split(r"[,;\n]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(rf"{_DATE}(?:\s*-\s*{_DATE})?\s*[:=]\s*(\w+)", part)
        mode = m[7].lower() if m else ""
        if not m or not is_mode(mode):
            bad.append(part)
            continue
        start = (int(m[3] or 0), int(m[2]), int(m[1]))
        end = (int(m[6] or 0), int(m[5]), int(m[4])) if m[4] else start
        if not (1 <= start[1] <= 12 and 1 <= start[2] <= 31 and 1 <= end[1] <= 12 and 1 <= end[2] <= 31):
            bad.append(part)
            continue
        rules.append(DayRule(start, end, mode))
    return rules, bad


@dataclass
class Config:
    moodle_url: str = "https://moodle.becode.org"
    attendance_cmid: str = ""
    timezone: str = "Europe/Brussels"
    weekly_pattern: dict[str, str] = field(default_factory=dict)
    weekly_pattern_raw: str = ""
    default_times: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TIMES))
    retry_minutes: int = 15
    ask_time: str = "20:00"
    campus_match: str = "campus"
    home_match: str = "home"
    moodle_username: str = ""
    moodle_password: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    webhook_secret: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    day_rules: list[DayRule] = field(default_factory=list)
    day_rules_invalid: list[str] = field(default_factory=list)
    paused: bool = False  # PAUSED=true in settings / dashboard

    @property
    def telegram_header_secret(self) -> str:
        """What Telegram sends with each update. It's derived from the webhook secret AND the bot
        token, so the value you type into /setup can't be used to fake Telegram messages."""
        if not (self.webhook_secret and self.telegram_token):
            return ""
        return hmac.new(self.telegram_token.encode(), self.webhook_secret.encode(), hashlib.sha256).hexdigest()

    @property
    def telegram_enabled(self) -> bool:
        """Telegram is optional: without a bot token the bot runs in automation-only mode."""
        return bool(self.telegram_token)

    def config_mode(self, ymd: str) -> str | None:
        """Mode from DAY_OVERRIDES for this date (last matching entry wins), else None."""
        hit = None
        for rule in self.day_rules:
            if rule.matches(ymd):
                hit = rule.mode
        return hit

    @classmethod
    def from_env(cls, get: Callable[[str], str | None]) -> "Config":
        def g(name: str, default: str = "") -> str:
            v = get(name)
            return v.strip() if isinstance(v, str) and v.strip() else default

        pattern = g("WEEKLY_PATTERN", "mon:home,tue:campus,wed:home,thu:campus,fri:home")
        try:
            retry = int(g("RETRY_MINUTES", "15"))
        except ValueError:
            retry = 15
        rules, bad = parse_day_overrides(g("DAY_OVERRIDES"))
        return cls(
            day_rules=rules,
            day_rules_invalid=bad,
            paused=g("PAUSED", "false").lower() in ("1", "true", "yes", "on"),
            moodle_url=g("MOODLE_URL", "https://moodle.becode.org").rstrip("/"),
            attendance_cmid=g("ATTENDANCE_CMID"),
            timezone=g("TIMEZONE", "Europe/Brussels"),
            weekly_pattern=parse_pattern(pattern),
            weekly_pattern_raw=pattern,
            default_times={sid: g(f"TIME_{sid.upper()}", DEFAULT_TIMES[sid]) for sid in SLOT_IDS},
            retry_minutes=max(retry, 1),
            ask_time=g("ASK_TIME", "20:00"),
            campus_match=g("CAMPUS_OPTION_MATCH", "campus"),
            home_match=g("HOME_OPTION_MATCH", "home"),
            moodle_username=g("MOODLE_USERNAME"),
            moodle_password=g("MOODLE_PASSWORD"),
            telegram_token=g("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=g("TELEGRAM_CHAT_ID"),
            webhook_secret=g("TELEGRAM_WEBHOOK_SECRET"),
            telegram_api_base=g("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
        )

    def option_match(self, mode: str) -> str:
        return self.campus_match if mode == "campus" else self.home_match

    @property
    def attendance_url(self) -> str:
        return f"{self.moodle_url}/mod/attendance/view.php?id={self.attendance_cmid}"
