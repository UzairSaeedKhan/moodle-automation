"""Automation-only mode (no Telegram) and the DAY_OVERRIDES / PAUSED settings."""

import json

from conftest import run
from helpers import bxl, make_app

from checkin.config import parse_day_overrides

NO_TELEGRAM = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "TELEGRAM_WEBHOOK_SECRET": ""}


def minute(app, when):
    run(app.run_minute(bxl(when)))


def test_full_day_without_telegram():
    app, world, kv, _ = make_app(**NO_TELEGRAM)
    assert not app.cfg.telegram_enabled
    for t in ("09:00", "09:01", "12:30", "13:30", "17:00"):
        minute(app, f"2026-11-05 {t}")
    assert world.state == {"am": "out", "pm": "out"}
    assert world.submitted[0]["location"] == "oncampus"  # Thursday pattern
    assert world.telegram == []  # never tried to talk to Telegram
    assert json.loads(kv.data["run:2026-11-05:pm_out"])["result"] == "ok"


def test_failure_without_telegram_is_recorded_silently():
    app, world, kv, _ = make_app(**NO_TELEGRAM)
    world.checkin_open = False
    for m in range(0, 21):
        minute(app, f"2026-11-05 09:{m:02d}")
    assert json.loads(kv.data["run:2026-11-05:am_in"])["result"] == "failed"
    assert world.telegram == []


def test_no_evening_question_without_telegram():
    app, world, kv, _ = make_app(**NO_TELEGRAM)
    run(app.store.save_settings(type(run(app.store.get_settings()))(ask=True)))
    minute(app, "2026-11-05 20:00")
    assert world.telegram == []


def test_day_overrides_change_the_plan():
    app, world, _, _ = make_app(DAY_OVERRIDES="05/11:home, 23/12-03/01:off", **NO_TELEGRAM)
    minute(app, "2026-11-05 09:00")  # Thursday would be campus
    assert world.submitted[-1]["location"] == "athome"
    assert run(app.store.resolve_mode("2026-12-28")) == ("off", "config")
    assert run(app.store.resolve_mode("2027-01-04")) == ("home", "pattern")


def test_day_off_from_settings_does_nothing():
    app, world, _, _ = make_app(DAY_OVERRIDES="5/11/2026:off", **NO_TELEGRAM)
    minute(app, "2026-11-05 09:00")
    assert world.moodle_requests == 0


def test_paused_setting_stops_everything():
    app, world, _, _ = make_app(PAUSED="true", **NO_TELEGRAM)
    minute(app, "2026-11-05 09:00")
    assert world.moodle_requests == 0


def test_telegram_change_beats_day_overrides():
    app, _, _, _ = make_app(DAY_OVERRIDES="06/11:campus")
    run(app.store.set_override("2026-11-06", "off"))
    assert run(app.store.resolve_mode("2026-11-06")) == ("off", "override")
    run(app.store.set_override("2026-11-06", "campus"))  # same as the settings plan: stored nothing
    assert run(app.store.resolve_mode("2026-11-06")) == ("campus", "config")


def test_parse_day_overrides():
    rules, bad = parse_day_overrides("12/11:home; 23/12-03/01 : off\n1/12/2026:campus, 31/02:home, xx:campus, 5/11:moon")
    assert [r.mode for r in rules] == ["home", "off", "campus", "home"]
    assert bad == ["xx:campus", "5/11:moon"]
    year_wrap = rules[1]
    assert year_wrap.matches("2026-12-23") and year_wrap.matches("2027-01-03") and not year_wrap.matches("2027-01-04")
    assert rules[2].matches("2026-12-01") and not rules[2].matches("2027-12-01")
    assert not rules[3].matches("2026-03-03")  # 31/02 never happens; harmless


def test_status_shows_settings_source_and_bad_entries():
    from checkin.bot import handle_update

    app, world, _, _ = make_app(DAY_OVERRIDES="05/11:off, oops")
    run(handle_update(app, {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": "/status"}}))
    text = world.sent_texts()[-1]
    assert "(from DAY_OVERRIDES)" in text and "oops" in text
