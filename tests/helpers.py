from datetime import datetime
from zoneinfo import ZoneInfo

from fakes import FakeKV, FakeWorld

from checkin.app import App
from checkin.config import Config

ENV = {
    "MOODLE_URL": "https://moodle.test",
    "TELEGRAM_API_BASE": "https://tg.test",
    "ATTENDANCE_CMID": "1491",
    "TIMEZONE": "Europe/Brussels",
    "WEEKLY_PATTERN": "mon:home,tue:campus,wed:home,thu:campus,fri:home",
    "RETRY_MINUTES": "15",
    "MOODLE_USERNAME": "me",
    "MOODLE_PASSWORD": "secret",
    "TELEGRAM_BOT_TOKEN": "T",
    "TELEGRAM_CHAT_ID": "42",
    "TELEGRAM_WEBHOOK_SECRET": "S",
}


def bxl(s: str) -> datetime:
    """'2026-11-05 09:00' in Brussels time -> aware datetime"""
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("Europe/Brussels"))


class Clock:
    def __init__(self, s: str):
        self.t = bxl(s)

    def set(self, s: str):
        self.t = bxl(s)

    def __call__(self):
        return self.t


def make_app(world: FakeWorld | None = None, now: str = "2026-11-05 09:00", **env):
    world = world or FakeWorld()
    kv = FakeKV()
    clock = Clock(now)
    cfg = Config.from_env(lambda n: {**ENV, **env}.get(n))
    return App(cfg, world, kv, clock), world, kv, clock
