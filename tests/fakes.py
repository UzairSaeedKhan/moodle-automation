"""Fakes for tests: an in-memory KV and a fake 'world' that plays both Moodle and the Telegram API.

FakeWorld implements the `Http` port directly (unit tests) and is also served over real HTTP
by tests/e2e/mock_server.py (end-to-end test against the real Workers runtime).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from checkin.ports import HttpResponse

DEFAULT_FORM = """<form action="{base}/mod/attendance/attendance.php" method="post">
  <input type="hidden" name="sessid" value="{sessid}"><input type="hidden" name="sesskey" value="K">
  <select name="location"><option value="oncampus">On campus</option><option value="athome">At home</option></select>
  <input type="submit" name="submitbutton" value="Save changes"><input type="submit" name="cancel" value="Cancel"></form>"""


class FakeKV:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    async def get(self, key):
        return self.data.get(key)

    async def put(self, key, value, ttl=None):
        self.data[key] = value
        self.ttl[key] = ttl

    async def delete(self, key):
        self.data.pop(key, None)


@dataclass
class FakeWorld:
    moodle_base: str = "https://moodle.test"
    telegram_base: str = "https://tg.test"
    day_label: str = "Thu 5 Nov 2026"
    password: str = "secret"
    form_html: str = DEFAULT_FORM
    # per half: "?" = not checked in, "in" = checked in, "out" = checked out
    state: dict[str, str] = field(default_factory=lambda: {"am": "?", "pm": "?"})
    checkin_open: dict[str, bool] = field(default_factory=lambda: {"am": True, "pm": True})
    submitted: list[dict[str, str]] = field(default_factory=list)
    telegram: list[tuple[str, dict]] = field(default_factory=list)
    moodle_requests: int = 0

    # --- Http port -------------------------------------------------------
    async def request(self, method, url, headers, body):
        return self.handle(method, url, headers, body)

    def handle(self, method: str, url: str, headers: dict[str, str], body: str | None) -> HttpResponse:
        parts = urlsplit(url)
        if url.startswith(self.telegram_base) and parts.path.startswith("/bot"):
            return self._telegram(parts.path, body)
        self.moodle_requests += 1
        return self._moodle(method, parts, {k.lower(): v for k, v in headers.items()}, body)

    # --- Telegram ----------------------------------------------------------
    def _telegram(self, path: str, body: str | None) -> HttpResponse:
        method = path.rsplit("/", 1)[-1]
        self.telegram.append((method, json.loads(body or "{}")))
        return HttpResponse(200, json.dumps({"ok": True, "result": {}}))

    def sent_texts(self) -> list[str]:
        return [p.get("text", "") for m, p in self.telegram if m == "sendMessage"]

    # --- Moodle ------------------------------------------------------------
    def _html(self, body: str, cookie: str | None = None) -> HttpResponse:
        return HttpResponse(200, body, set_cookies=[cookie] if cookie else [])

    def _redirect(self, to: str, cookie: str | None = None) -> HttpResponse:
        return HttpResponse(303, "", location=to, set_cookies=[cookie] if cookie else [])

    def _row(self, half: str) -> str:
        sessid = "1" if half == "am" else "2"
        time = "9AM - 12:30PM" if half == "am" else "1:30PM - 5PM"
        s = self.state[half]
        if s == "?":
            cell = (
                f'<a class="btn" href="{self.moodle_base}/mod/attendance/attendance.php?sessid={sessid}&amp;sesskey=K">Check in</a>'
                if self.checkin_open[half]
                else "?"
            )
        elif s == "in":
            cell = f'<a class="btn" href="{self.moodle_base}/mod/attendance/attendance.php?sessid={sessid}&amp;sesskey=K&amp;action=checkout">Check out</a>'
        else:
            cell = "Checked out"
        return (
            f'<tr><td class="datecol cell c0"><nobr>{self.day_label}</nobr> <nobr>{time}</nobr></td>'
            f'<td class="statuscol cell c2">{cell}</td></tr>'
        )

    def _moodle(self, method: str, parts, headers: dict[str, str], body: str | None) -> HttpResponse:
        base = self.moodle_base
        path, q = parts.path, parse_qs(parts.query)
        form = {k: v[0] for k, v in parse_qs(body or "").items()}
        authed = "MoodleSession=auth" in headers.get("cookie", "")

        if path == "/login/index.php" and method == "POST":
            if form.get("logintoken") != "LT" or form.get("password") != self.password:
                return self._html(f'<body id="page-login-index"><form action="{base}/login/index.php"><input name="logintoken" value="LT"></form>Invalid login</body>')
            return self._redirect(f"{base}/login/index.php?testsession=7", "MoodleSession=auth; path=/; HttpOnly")
        if path == "/login/index.php" and "testsession" in q:
            return self._redirect(f"{base}/my/")
        if path == "/login/index.php":
            return self._html(
                f'<body id="page-login-index"><form action="{base}/login/index.php" method="post">'
                '<input type="hidden" name="anchor" value=""><input type="hidden" name="logintoken" value="LT">'
                '<input type="text" name="username"><input type="password" name="password"></form></body>',
                "MoodleSession=anon; path=/",
            )
        if not authed:
            return self._redirect(f"{base}/login/index.php")
        if path == "/my/":
            return self._html("<body id='page-my-index'>Dashboard</body>")
        if path == "/mod/attendance/view.php":
            return self._html(f'<body id="page-mod-attendance-view"><table>{self._row("am")}{self._row("pm")}</table></body>')
        if path == "/mod/attendance/attendance.php":
            half = "am" if q.get("sessid", [form.get("sessid")])[0] == "1" else "pm"
            if q.get("action") == ["checkout"]:
                self.state[half] = "out"
                return self._redirect(f"{base}/mod/attendance/view.php?id=1491")
            if method == "POST":
                self.submitted.append(form)
                if form.get("location") in ("oncampus", "athome"):
                    self.state[half] = "in"
                return self._redirect(f"{base}/mod/attendance/view.php?id=1491")
            return self._html(self.form_html.format(base=base, sessid="1" if half == "am" else "2"))
        return HttpResponse(404, "not found")
