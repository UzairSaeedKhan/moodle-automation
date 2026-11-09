"""The per-minute scheduler, against a fake Moodle + fake Telegram."""

import json

from conftest import run
from fakes import FakeWorld
from helpers import bxl, make_app


def minute(app, when):
    run(app.run_minute(bxl(when)))


def run_state(kv, ymd, slot):
    raw = kv.data.get(f"run:{ymd}:{slot}")
    return json.loads(raw) if raw else None


def test_thursday_morning_checks_in_on_campus_once():
    app, world, kv, _ = make_app()
    minute(app, "2026-11-05 08:59")
    assert world.moodle_requests == 0  # not yet

    minute(app, "2026-11-05 09:00")
    assert world.state["am"] == "in"
    assert world.submitted[-1]["location"] == "oncampus"  # Thursday = campus
    assert len(world.sent_texts()) == 1
    assert "✅" in world.sent_texts()[0] and "On campus" in world.sent_texts()[0]
    assert run_state(kv, "2026-11-05", "am_in")["result"] == "ok"

    before = world.moodle_requests
    minute(app, "2026-11-05 09:01")
    assert world.moodle_requests == before  # done -> nothing more


def test_full_day():
    app, world, kv, _ = make_app()
    for t in ("09:00", "12:30", "13:30", "17:00"):
        minute(app, f"2026-11-05 {t}")
    assert world.state == {"am": "out", "pm": "out"}
    assert len(world.sent_texts()) == 4
    assert all(t.startswith("✅") for t in world.sent_texts())


def test_home_day_uses_home_option():
    app, world, _, _ = make_app(world=FakeWorld(day_label="Fri 6 Nov 2026"))
    minute(app, "2026-11-06 09:00")
    assert world.submitted[-1]["location"] == "athome"


def test_override_off_does_nothing():
    app, world, kv, _ = make_app()
    run(app.store.set_override("2026-11-05", "off"))
    minute(app, "2026-11-05 09:00")
    assert world.moodle_requests == 0
    assert world.telegram == []
    assert run_state(kv, "2026-11-05", "am_in")["result"] == "off"


def test_paused_and_weekend_do_nothing():
    app, world, _, _ = make_app()
    s = run(app.store.get_settings())
    s.paused = True
    run(app.store.save_settings(s))
    minute(app, "2026-11-05 09:00")
    s.paused = False
    run(app.store.save_settings(s))
    minute(app, "2026-11-07 09:00")  # Saturday
    assert world.moodle_requests == 0


def test_retries_until_button_appears():
    world = FakeWorld(checkin_open={"am": False, "pm": True})
    app, world, kv, _ = make_app(world=world)
    minute(app, "2026-11-05 09:00")
    minute(app, "2026-11-05 09:01")
    assert world.state["am"] == "?"
    assert world.telegram == []  # no noise while retrying
    world.checkin_open["am"] = True
    minute(app, "2026-11-05 09:02")
    assert world.state["am"] == "in"
    assert run_state(kv, "2026-11-05", "am_in")["attempts"] == 3
    assert len(world.sent_texts()) == 1


def test_reports_once_when_button_never_appears():
    world = FakeWorld(checkin_open={"am": False, "pm": True})
    app, world, kv, _ = make_app(world=world)
    for m in range(0, 21):
        minute(app, f"2026-11-05 09:{m:02d}")
    texts = world.sent_texts()
    assert len(texts) == 1
    assert texts[0].startswith("⚠️") and "Please do it manually" in texts[0]
    assert run_state(kv, "2026-11-05", "am_in")["result"] == "failed"


def test_wrong_password_reports_immediately_once():
    app, world, kv, _ = make_app(world=FakeWorld(password="changed"))
    minute(app, "2026-11-05 09:00")
    minute(app, "2026-11-05 09:01")
    texts = world.sent_texts()
    assert len(texts) == 1 and "login failed" in texts[0].lower()


def test_already_checked_in_manually():
    world = FakeWorld()
    world.state["am"] = "in"
    app, world, _, _ = make_app(world=world)
    minute(app, "2026-11-05 09:00")
    assert world.sent_texts()[0].startswith("☑️")


def test_custom_time():
    app, world, _, _ = make_app()
    s = run(app.store.get_settings())
    s.times["am_in"] = "08:58"
    run(app.store.save_settings(s))
    minute(app, "2026-11-05 08:58")
    assert world.state["am"] == "in"


def test_evening_question_once_and_only_when_enabled():
    app, world, _, _ = make_app()
    minute(app, "2026-11-04 20:00")
    assert world.telegram == []  # /ask is off by default

    s = run(app.store.get_settings())
    s.ask = True
    run(app.store.save_settings(s))
    minute(app, "2026-11-04 20:00")  # Wednesday evening -> asks about Thursday
    minute(app, "2026-11-04 20:01")
    assert len(world.telegram) == 1
    method, payload = world.telegram[0]
    assert "Thu 5 Nov" in payload["text"] and "On campus" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["a|2026-11-05|campus", "a|2026-11-05|home", "a|2026-11-05|off"]

    minute(app, "2026-11-06 20:00")  # Friday -> tomorrow is Saturday -> no question
    assert len(world.telegram) == 1
