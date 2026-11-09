from __future__ import annotations

import json
from typing import Any

from .config import Config
from .ports import Http

COMMANDS = [
    {"command": "status", "description": "Today's plan and results"},
    {"command": "week", "description": "This week's plan (tap to change)"},
    {"command": "campus", "description": "On campus: /campus tomorrow, /campus 12/11"},
    {"command": "home", "description": "At home: /home fri, /home nextweek"},
    {"command": "off", "description": "No check-in: /off 23/12-03/01"},
    {"command": "reset", "description": "Back to default: /reset tomorrow"},
    {"command": "ask", "description": "Evening question on/off: /ask on"},
    {"command": "times", "description": "Show / change times: /time am_in 08:58"},
    {"command": "pause", "description": "Pause all automatic actions"},
    {"command": "resume", "description": "Resume automatic actions"},
    {"command": "checkin", "description": "Check in now (asks to confirm)"},
    {"command": "checkout", "description": "Check out now (asks to confirm)"},
    {"command": "test", "description": "Test Moodle login and read today's sessions"},
    {"command": "help", "description": "How this bot works"},
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Telegram:
    def __init__(self, http: Http, cfg: Config):
        self.http = http
        self.cfg = cfg

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.telegram_enabled:  # automation-only mode: nothing to send to
            return {"ok": False, "description": "Telegram not configured"}
        url = f"{self.cfg.telegram_api_base}/bot{self.cfg.telegram_token}/{method}"
        res = await self.http.request("POST", url, {"content-type": "application/json"}, json.dumps(payload))
        try:
            data = json.loads(res.text)
        except ValueError:
            data = {"ok": False, "description": f"HTTP {res.status}"}
        if not data.get("ok"):
            print(f"Telegram {method} failed: {data.get('description')}")
        return data

    async def send(self, text: str, chat_id: str | int | None = None, **extra: Any) -> dict[str, Any]:
        return await self.call(
            "sendMessage",
            {
                "chat_id": chat_id if chat_id is not None else self.cfg.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                **extra,
            },
        )


def ask_tomorrow_markup(ymd: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "🏫 Campus", "callback_data": f"a|{ymd}|campus"},
                {"text": "🏠 Home", "callback_data": f"a|{ymd}|home"},
                {"text": "❌ Off", "callback_data": f"a|{ymd}|off"},
            ]
        ]
    }
