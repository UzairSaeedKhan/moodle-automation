from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, parse_qsl

from .html import (
    ParsedForm,
    SessionRow,
    build_submission,
    describe_form,
    find_row,
    is_checkin_link,
    is_checkout_link,
    is_login_page,
    parse_forms,
    parse_session_rows,
)
from .ports import Http
from .timeutil import moodle_date_label

USER_AGENT = "Mozilla/5.0 (compatible; moodle-checkin-bot/0.2; self-hosted)"


class MoodleError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable  # False = retrying won't help (e.g. wrong password)


@dataclass
class Page:
    url: str
    status: int
    html: str


@dataclass
class Outcome:
    kind: str  # done | already | not_open | no_session
    detail: str


def _half_name(half: str) -> str:
    return "morning" if half == "am" else "afternoon"


class Moodle:
    def __init__(self, base: str, http: Http):
        self.base = base.rstrip("/") + "/"
        self.http = http
        self.cookies: dict[str, str] = {}
        parts = urlsplit(self.base)
        self._origin = (parts.scheme, parts.netloc.lower())
        local = parts.hostname in ("localhost", "127.0.0.1")
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise MoodleError("MOODLE_URL must start with https://", retryable=False)

    def _check_origin(self, url: str) -> None:
        """Your password and session cookie are only ever sent to MOODLE_URL itself."""
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc.lower()) != self._origin:
            raise MoodleError(f"Refusing to send your Moodle login to another site ({parts.netloc or url})", retryable=False)

    async def request(self, url: str, data: list[tuple[str, str]] | None = None) -> Page:
        """GET (or POST form data), keeping cookies and following redirects manually."""
        current = urljoin(self.base, url)
        method = "POST" if data is not None else "GET"
        body = urlencode(data) if data is not None else None

        for _ in range(10):
            self._check_origin(current)
            headers = {"user-agent": USER_AGENT, "accept": "text/html"}
            if self.cookies:
                headers["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            if body is not None:
                headers["content-type"] = "application/x-www-form-urlencoded"

            res = await self.http.request(method, current, headers, body)
            self._store_cookies(res.set_cookies)

            if 300 <= res.status < 400 and res.location:
                current = urljoin(current, res.location)
                method, body = "GET", None
                continue
            if res.status >= 500:
                raise MoodleError(f"Moodle returned HTTP {res.status} for {current}")
            return Page(current, res.status, res.text)
        raise MoodleError(f"Too many redirects starting at {url}")

    def _store_cookies(self, set_cookies: list[str]) -> None:
        for c in set_cookies:
            pair = c.split(";", 1)[0]
            if "=" not in pair:
                continue
            name, value = (x.strip() for x in pair.split("=", 1))
            if not value or value == "deleted" or re.search(r"max-age=0\b", c, re.I):
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    async def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise MoodleError("MOODLE_USERNAME / MOODLE_PASSWORD are not set", retryable=False)
        page = await self.request("login/index.php")
        if not is_login_page(page.html):
            return
        form = next(
            (f for f in parse_forms(page.html) if any(k == "logintoken" for k, _ in f.fields) or "login/index.php" in f.action),
            None,
        )
        params = [(k, v) for k, v in (form.fields if form else []) if k not in ("username", "password")]
        params += [("username", username), ("password", password)]
        result = await self.request(form.action if form and form.action else "login/index.php", params)
        if is_login_page(result.html):
            raise MoodleError("Moodle login failed. Check MOODLE_USERNAME / MOODLE_PASSWORD.", retryable=False)

    async def sessions(self, cmid: str) -> list[SessionRow]:
        # view=3 = "Months" view (current month), which always contains today's sessions.
        page = await self.request(f"mod/attendance/view.php?id={cmid}&view=3")
        if is_login_page(page.html):
            raise MoodleError("Not logged in when opening the attendance page")
        rows = parse_session_rows(page.html)
        if not rows:
            raise MoodleError(f"Couldn't read the attendance table at {page.url}. Is ATTENDANCE_CMID right?", retryable=False)
        return rows

    async def row(self, cmid: str, ymd: str, half: str) -> SessionRow | None:
        return find_row(await self.sessions(cmid), moodle_date_label(ymd), half)

    async def check_in(self, cmid: str, ymd: str, half: str, option_match: str) -> Outcome:
        label = f"{moodle_date_label(ymd)} ({_half_name(half)})"
        row = await self.row(cmid, ymd, half)
        if not row:
            return Outcome("no_session", f"No Moodle session for {label}")

        link = next((l for l in row.links if is_checkin_link(l)), None)
        if not link:
            if any(is_checkout_link(l) for l in row.links) or (row.status and row.status != "?"):
                return Outcome("already", f"Already checked in (status: {row.status or '?'})")
            return Outcome("not_open", f"Check-in button not available yet for {label}")

        form_page = await self.request(link.href)
        form = _pick_form(parse_forms(form_page.html), needs_choice=True)
        chosen = ""
        if form:
            params, chosen = build_submission(form, option_match)  # raises FormMismatchError with details
            await self._submit(form, form_page.url, params)

        row = await self.row(cmid, ymd, half)
        if row and not any(is_checkin_link(l) for l in row.links):
            status = "checked in" if any(is_checkout_link(l) for l in row.links) else f"status: {row.status or '?'}"
            return Outcome("done", f"{chosen} · {status}" if chosen else status)
        detail = f" Form was: {describe_form(form)}" if form else f" No form found on {form_page.url}"
        raise MoodleError("Submitted check-in but Moodle still shows the Check-in button." + detail)

    async def check_out(self, cmid: str, ymd: str, half: str) -> Outcome:
        label = f"{moodle_date_label(ymd)} ({_half_name(half)})"
        row = await self.row(cmid, ymd, half)
        if not row:
            return Outcome("no_session", f"No Moodle session for {label}")

        link = next((l for l in row.links if is_checkout_link(l)), None)
        if not link:
            if re.search(r"checked\s*out", row.status, re.I):
                return Outcome("already", "Already checked out")
            if any(is_checkin_link(l) for l in row.links) or row.status == "?":
                return Outcome("not_open", f"Can't check out: not checked in for {label} (status: {row.status or '?'})")
            return Outcome("not_open", f"Check-out button not available for {label} (status: {row.status})")

        page = await self.request(link.href)
        # If Moodle shows a confirmation form instead of checking out directly, submit it.
        confirm = _pick_form(parse_forms(page.html), needs_choice=False)
        if confirm:
            params = list(confirm.fields)
            submit = next((s for s in confirm.submits if s[0] != "cancel"), None)
            if submit:
                params.append(submit)
            await self._submit(confirm, page.url, params)

        row = await self.row(cmid, ymd, half)
        if row and not any(is_checkout_link(l) for l in row.links):
            return Outcome("done", f"status: {row.status or '?'}")
        raise MoodleError(f"Clicked Check out but Moodle still shows the button (status: {row.status if row else '?'})")

    async def _submit(self, form: ParsedForm, page_url: str, params: list[tuple[str, str]]) -> Page:
        action = urljoin(page_url, form.action or page_url)
        if form.method == "post":
            return await self.request(action, params)
        parts = urlsplit(action)
        query = dict(parse_qsl(parts.query))
        query.update(dict(params))
        return await self.request(urlunsplit(parts._replace(query=urlencode(query))))


def _pick_form(forms: list[ParsedForm], needs_choice: bool) -> ParsedForm | None:
    """The attendance form on a page (ignores Moodle's chrome like the date picker)."""
    candidates = [f for f in forms if "mod/attendance/attendance.php" in f.action or f.action == ""]
    pool = candidates or [f for f in forms if not re.search(r"view\.php|login|search", f.action)]
    if needs_choice:
        return next((f for f in pool if f.selects or f.radios), None)
    return next((f for f in pool if f.submits), None)
