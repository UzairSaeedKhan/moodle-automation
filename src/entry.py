"""Cloudflare Python Worker entry point.

The only file that touches the Workers runtime (JS fetch, KV, Request/Response).
Everything else lives in the pure-Python `checkin` package so it can be unit-tested.
"""

import hmac
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from js import Object
from js import fetch as js_fetch
from pyodide.ffi import to_js as _to_js
from workers import Response, WorkerEntrypoint

from checkin.app import App
from checkin.bot import handle_update
from checkin.config import Config
from checkin.ports import HttpResponse
from checkin.telegram import COMMANDS


def to_js(obj):
    return _to_js(obj, dict_converter=Object.fromEntries)


def _str(v):
    """JS null/undefined -> None; strings pass through."""
    return v if isinstance(v, str) else None


class JsHttp:
    """Outgoing HTTP via the runtime's fetch, without following redirects."""

    async def request(self, method, url, headers, body):
        init = {"method": method, "headers": headers, "redirect": "manual"}
        if body is not None:
            init["body"] = body
        res = await js_fetch(url, to_js(init))
        text = await res.text()
        try:
            cookies = [str(c) for c in res.headers.getSetCookie()]
        except Exception:  # noqa: BLE001 - older runtimes
            one = _str(res.headers.get("set-cookie"))
            cookies = [one] if one else []
        return HttpResponse(int(res.status), str(text), _str(res.headers.get("location")), cookies)


class JsKV:
    def __init__(self, namespace):
        self.ns = namespace

    async def get(self, key):
        return _str(await self.ns.get(key))

    async def put(self, key, value, ttl=None):
        if ttl:
            await self.ns.put(key, value, to_js({"expirationTtl": max(int(ttl), 60)}))
        else:
            await self.ns.put(key, value)

    async def delete(self, key):
        await self.ns.delete(key)


def make_app(env) -> App:
    def get(name):
        try:
            return _str(getattr(env, name))
        except AttributeError:
            return None

    return App(Config.from_env(get), JsHttp(), JsKV(env.STATE), lambda: datetime.now(timezone.utc))


PAGE_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}

SETUP_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot setup</title>
<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;line-height:1.5}
input,button{font:inherit;padding:.5rem;width:100%;box-sizing:border-box;margin:.25rem 0}</style></head>
<body><h1>Connect Telegram</h1>
<p>Paste your <code>TELEGRAM_WEBHOOK_SECRET</code> and press the button.
You should then see <code>"ok": true</code> twice.</p>
<form method="post" autocomplete="off">
<input type="password" name="secret" placeholder="TELEGRAM_WEBHOOK_SECRET" required autofocus>
<button type="submit">Connect</button>
</form></body></html>
"""


def _same(given, secret):
    """Constant-time secret check; an empty secret never matches."""
    return bool(secret) and bool(given) and hmac.compare_digest(str(given).encode(), str(secret).encode())


def _to_py(v):
    return v.to_py() if hasattr(v, "to_py") else v


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        app = make_app(self.env)
        secret = app.cfg.webhook_secret

        if url.path in ("/telegram", "/setup") and not app.cfg.telegram_enabled:
            return Response("Telegram is not configured: running in automation-only mode.", status=404)

        # Telegram sends every message / button press here.
        if url.path == "/telegram" and request.method == "POST":
            if not _same(_str(request.headers.get("x-telegram-bot-api-secret-token")), app.cfg.telegram_header_secret):
                return Response("forbidden", status=403)
            update = _to_py(await request.json())
            try:
                await handle_update(app, update)
            except Exception as e:  # noqa: BLE001 - always 200, or Telegram keeps re-sending
                print("update failed:", repr(e))
            return Response("ok")

        # One-time setup: open https://<your-worker>/setup, paste TELEGRAM_WEBHOOK_SECRET, press the button.
        # The secret travels in the POST body, never in the URL (so not in browser history or logs).
        if url.path == "/setup" and request.method != "POST":
            return Response(SETUP_PAGE, headers=PAGE_HEADERS)
        if url.path == "/setup":
            form = parse_qs(str(await request.text()))
            if not _same(form.get("secret", [None])[0], secret):
                return Response("forbidden", status=403, headers=PAGE_HEADERS)
            origin = f"{url.scheme}://{url.netloc}"
            webhook = await app.tg.call(
                "setWebhook",
                {
                    "url": f"{origin}/telegram",
                    # Not the URL secret itself: see Config.telegram_header_secret.
                    "secret_token": app.cfg.telegram_header_secret,
                    "allowed_updates": ["message", "callback_query"],
                },
            )
            commands = await app.tg.call("setMyCommands", {"commands": COMMANDS})
            return Response.json({"webhook": webhook, "commands": commands}, headers={"cache-control": "no-store"})

        mode = "with Telegram" if app.cfg.telegram_enabled else "automation-only (no Telegram)"
        return Response(f"moodle-checkin-bot is running, {mode}.")

    async def scheduled(self, controller, env=None, ctx=None):
        # Use self.env: depending on the SDK version the `env` argument can be None.
        when = datetime.fromtimestamp(controller.scheduledTime / 1000, tz=timezone.utc)
        await make_app(self.env).run_minute(when)
