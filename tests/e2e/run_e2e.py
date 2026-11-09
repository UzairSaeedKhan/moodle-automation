"""End-to-end test: the real Worker (Cloudflare's local Python runtime via `pywrangler dev`)
talking to a local fake Moodle + fake Telegram API over real HTTP.

Run:  uv run python tests/e2e/run_e2e.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from fakes import FakeWorld  # noqa: E402

MOCK_PORT = 8790
WORKER_PORT = 8787
MOCK = f"http://127.0.0.1:{MOCK_PORT}"
WORKER = f"http://127.0.0.1:{WORKER_PORT}"
SECRET = "e2e-secret"

world = FakeWorld(moodle_base=MOCK, telegram_base=MOCK, day_label="Thu 5 Nov 2026")
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length).decode() if length else None
        with lock:
            res = world.handle(self.command, MOCK + self.path, dict(self.headers.items()), body)
        self.send_response(res.status)
        if res.location:
            self.send_header("Location", res.location)
        for c in res.set_cookies:
            self.send_header("Set-Cookie", c)
        data = res.text.encode()
        self.send_header("Content-Type", "application/json" if self.path.startswith("/bot") else "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _handle

    def log_message(self, *args):
        pass


def http(method, url, body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=body.encode() if body else None, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def telegram_update(text, chat=42):
    return http(
        "POST",
        f"{WORKER}/telegram",
        json.dumps({"message": {"chat": {"id": chat}, "text": text}}),
        {"content-type": "application/json", "x-telegram-bot-api-secret-token": SECRET},
    )


def cron(local: str):
    """Fire the scheduled handler as if it were `local` Brussels time."""
    # scheduledTime is in milliseconds; the local tool passes `time` through as-is.
    ts = int(datetime.fromisoformat(local).replace(tzinfo=ZoneInfo("Europe/Brussels")).timestamp() * 1000)
    status, body = http("GET", f"{WORKER}/cdn-cgi/handler/scheduled?cron=*+*+*+*+*&time={ts}")
    if status == 404:
        status, body = http("GET", f"{WORKER}/__scheduled?cron=*+*+*+*+*&time={ts}")
    assert status == 200, (status, body)


results: list[tuple[str, bool, str]] = []


def check(name, cond, info=""):
    results.append((name, bool(cond), info))
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  -> {info}"))


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env_vars = {
        "MOODLE_URL": MOCK,
        "TELEGRAM_API_BASE": MOCK,
        "MOODLE_USERNAME": "me",
        "MOODLE_PASSWORD": "secret",
        "TELEGRAM_BOT_TOKEN": "T",
        "TELEGRAM_CHAT_ID": "42",
        "TELEGRAM_WEBHOOK_SECRET": SECRET,
    }
    # Fresh, throw-away local KV for every run (otherwise state from the last run leaks in).
    import tempfile

    state_dir = tempfile.mkdtemp(prefix="moodle-e2e-")
    cmd = ["uv", "run", "pywrangler", "dev", "--port", str(WORKER_PORT), "--ip", "127.0.0.1", "--test-scheduled",
           "--persist-to", state_dir]
    for k, v in env_vars.items():
        cmd += ["--var", f"{k}:{v}"]
    log = open(ROOT / "tests/e2e/worker.log", "w")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        print("Starting worker (first start downloads the Python runtime, can take a minute)…")
        deadline = time.time() + 300
        while time.time() < deadline:
            if proc.poll() is not None:
                print("worker exited early; see tests/e2e/worker.log")
                return 1
            try:
                status, body = http("GET", WORKER + "/", timeout=5)
                if status == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        else:
            print("worker did not start in time; see tests/e2e/worker.log")
            return 1

        print("Worker is up. Running checks:")
        status, body = http("GET", WORKER + "/")
        check("GET / responds", status == 200 and "running" in body, body)

        status, _ = http("POST", f"{WORKER}/telegram", "{}", {"x-telegram-bot-api-secret-token": "wrong"})
        check("webhook rejects a wrong secret", status == 403, status)

        status, body = http("GET", f"{WORKER}/setup?secret={SECRET}")
        sent = [m for m, _ in world.telegram]
        check("/setup registers webhook + commands", status == 200 and "setWebhook" in sent and "setMyCommands" in sent, (status, body, sent))
        webhook_url = next((p["url"] for m, p in world.telegram if m == "setWebhook"), "")
        check("/setup uses the worker's own URL", webhook_url.endswith("/telegram"), webhook_url)

        world.telegram.clear()
        telegram_update("/start", chat=999)
        telegram_update("checkout", chat=999)
        check("strangers get no reply once the chat id is set", world.telegram == [], world.telegram)

        telegram_update("/campus 12/11")
        check("/campus 12/11 saved (KV write)", world.sent_texts() and "On campus" in world.sent_texts()[-1], world.sent_texts())

        telegram_update("/week next")
        markup = world.telegram[-1][1].get("reply_markup", {})
        rows = markup.get("inline_keyboard", [])
        check("/week next shows 12/11 as campus (KV read)", len(rows) == 6 and rows[3][1]["text"] == "[🏫]", rows[3] if len(rows) > 3 else rows)

        # --- the real thing: cron at Thursday 09:00 -> campus check-in
        world.telegram.clear()
        cron("2026-11-05 09:00")
        check("cron 09:00 checked in", world.state["am"] == "in", world.state)
        check("picked On-Campus (Thursday)", world.submitted and world.submitted[-1].get("location") == "oncampus", world.submitted)
        check("sent ✅ to Telegram", world.sent_texts() and world.sent_texts()[-1].startswith("✅"), world.sent_texts())

        n = len(world.telegram)
        cron("2026-11-05 09:01")
        check("09:01 does nothing more (run state persisted)", len(world.telegram) == n, world.sent_texts())

        cron("2026-11-05 12:30")
        check("cron 12:30 checked out", world.state["am"] == "out", world.state)

        telegram_update("/time pm_in 13:31")
        cron("2026-11-05 13:30")
        check("custom time respected (nothing at 13:30)", world.state["pm"] == "?", world.state)
        cron("2026-11-05 13:31")
        check("checked in at custom time 13:31", world.state["pm"] == "in", world.state)

        telegram_update("/test")
        check("/test logs in and reads sessions", "Login OK" in world.sent_texts()[-1], world.sent_texts()[-1:])

        # Manual checkout asks for confirmation; Cancel changes nothing.
        before = dict(world.state)
        telegram_update("checkout")
        kb = world.telegram[-1][1].get("reply_markup", {}).get("inline_keyboard", [])
        datas = [b["callback_data"] for row in kb for b in row]
        check("'checkout' asks Yes/Cancel first", any(d.startswith("co|") for d in datas) and "cx" in datas, kb)
        http(
            "POST",
            f"{WORKER}/telegram",
            json.dumps({"callback_query": {"id": "q", "data": "cx", "message": {"chat": {"id": 42}, "message_id": 5}}}),
            {"content-type": "application/json", "x-telegram-bot-api-secret-token": SECRET},
        )
        edits = [p["text"] for m, p in world.telegram if m == "editMessageText"]
        check("Cancel button: nothing changed", edits and "Cancelled" in edits[-1] and world.state == before, (edits, world.state))
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        server.shutdown()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} end-to-end checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
