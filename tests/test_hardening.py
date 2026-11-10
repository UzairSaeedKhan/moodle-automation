"""Secrets never leak into logs/messages; Telegram only over HTTPS."""

from conftest import run
from helpers import make_app

from checkin.config import Config
from checkin.telegram import Telegram

TOKEN = "123456789:AAH-abcdefghijklmnopqrstuvwxyz012345"


def test_redact_removes_every_secret():
    cfg = Config(telegram_token=TOKEN, moodle_password="hunter2pw", webhook_secret="websecret")
    text = (f"fetch https://api.telegram.org/bot{TOKEN}/sendMessage failed; pw=hunter2pw; s=websecret; "
            f"{cfg.telegram_header_secret}; https://m/x.php?sessid=1&sesskey=AbC123xyz&a=1; bot987:{'Z' * 30}")
    out = cfg.redact(text)
    for secret in (TOKEN, "hunter2pw", "websecret", cfg.telegram_header_secret, "AbC123xyz", "Z" * 30):
        assert secret not in out
    assert "sesskey=***" in out and "sessid=1" in out


class _Boom:
    def __init__(self):
        self.calls = 0

    async def request(self, method, url, headers, body):
        self.calls += 1
        raise RuntimeError(f"network error while fetching {url}")


def test_network_error_is_logged_without_token(capsys):
    http = _Boom()
    res = run(Telegram(http, Config(telegram_token=TOKEN, telegram_chat_id="42")).send("hi"))
    assert res["ok"] is False and http.calls == 1
    assert TOKEN not in capsys.readouterr().out


def test_telegram_api_base_must_be_https():
    http = _Boom()
    cfg = Config(telegram_token=TOKEN, telegram_api_base="http://evil.example")
    assert run(Telegram(http, cfg).send("hi"))["ok"] is False
    assert http.calls == 0  # the token was never sent anywhere


def test_moodle_errors_sent_to_telegram_are_redacted():
    app, world, _, _ = make_app(MOODLE_PASSWORD="hunter2pw", RETRY_MINUTES="1")  # 09:00 = last try -> ⚠️ sent

    async def leaky(*a, **k):
        raise RuntimeError("login failed with password hunter2pw at ...?sesskey=SECRETKEY1")

    app._perform = leaky
    run(app.run_minute(app.clock()))
    sent = " ".join(world.sent_texts())
    assert "⚠️" in sent and "login failed" in sent
    assert "hunter2pw" not in sent and "SECRETKEY1" not in sent
