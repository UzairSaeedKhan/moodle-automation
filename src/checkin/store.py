from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from .config import Config, is_mode
from .ports import KV
from .timeutil import weekday_key


@dataclass
class Settings:
    paused: bool = False
    ask: bool = False  # send the "where are you tomorrow?" question
    times: dict[str, str] = field(default_factory=dict)


@dataclass
class RunState:
    final: bool
    result: str  # ok | already | failed | off | no_session | pending
    attempts: int
    detail: str | None = None
    at: str | None = None  # local HH:MM of the last attempt


class Store:
    def __init__(self, kv: KV, cfg: Config):
        self.kv = kv
        self.cfg = cfg

    # --- settings -------------------------------------------------------
    async def get_settings(self) -> Settings:
        raw = await self.kv.get("settings")
        try:
            d = json.loads(raw) if raw else {}
        except ValueError:
            d = {}
        return Settings(paused=bool(d.get("paused")), ask=bool(d.get("ask")), times=dict(d.get("times") or {}))

    async def save_settings(self, s: Settings) -> None:
        await self.kv.put("settings", json.dumps(asdict(s)))

    def slot_time(self, s: Settings, slot_id: str) -> str:
        return s.times.get(slot_id) or self.cfg.default_times[slot_id]

    # --- day plan: Telegram change > DAY_OVERRIDES > WEEKLY_PATTERN ------
    def pattern_mode(self, ymd: str) -> str:
        return self.cfg.weekly_pattern.get(weekday_key(ymd), "off")

    def base_mode(self, ymd: str) -> tuple[str, str]:
        """The plan from settings alone: DAY_OVERRIDES, else WEEKLY_PATTERN."""
        c = self.cfg.config_mode(ymd)
        return (c, "config") if c else (self.pattern_mode(ymd), "pattern")

    async def resolve_mode(self, ymd: str) -> tuple[str, str]:
        """-> (mode, source) where source is 'override' (Telegram), 'config' (DAY_OVERRIDES) or 'pattern'"""
        o = await self.kv.get(f"ov:{ymd}")
        if is_mode(o):
            return o, "override"  # type: ignore[return-value]
        return self.base_mode(ymd)

    async def set_override(self, ymd: str, mode: str | None) -> None:
        """Setting a day to its default (or None) just removes the override."""
        if mode is None or mode == self.base_mode(ymd)[0]:
            await self.kv.delete(f"ov:{ymd}")
            return
        day = datetime.combine(date.fromisoformat(ymd), datetime.min.time(), tzinfo=timezone.utc).timestamp()
        ttl = max(int(day + 14 * 86400 - time.time()), 3600)
        await self.kv.put(f"ov:{ymd}", mode, ttl)

    # --- run state per date+slot ----------------------------------------
    async def get_run(self, ymd: str, slot_id: str) -> RunState | None:
        raw = await self.kv.get(f"run:{ymd}:{slot_id}")
        if not raw:
            return None
        try:
            return RunState(**json.loads(raw))
        except (ValueError, TypeError):
            return None

    async def put_run(self, ymd: str, slot_id: str, state: RunState) -> None:
        await self.kv.put(f"run:{ymd}:{slot_id}", json.dumps(asdict(state)), 8 * 86400)

    # --- one-off flags --------------------------------------------------
    async def flag(self, key: str) -> bool:
        return bool(await self.kv.get(f"flag:{key}"))

    async def set_flag(self, key: str, ttl: int = 3 * 86400) -> None:
        await self.kv.put(f"flag:{key}", "1", ttl)
