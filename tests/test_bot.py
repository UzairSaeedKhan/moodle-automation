"""Telegram commands and buttons."""

from conftest import run
from helpers import make_app

from checkin.bot import handle_update


def msg(text, chat=42):
    return {"message": {"chat": {"id": chat, "type": "private"}, "from": {"id": chat}, "text": text}}


def cb(data, chat=42):
    return {
        "callback_query": {
            "id": "q1",
            "data": data,
            "from": {"id": chat},
            "message": {"chat": {"id": chat, "type": "private"}, "message_id": 7},
        }
    }


def send(app, update):
    run(handle_update(app, update))


def test_stranger_start_gets_chat_id_only_during_setup():
    app, world, _, _ = make_app(TELEGRAM_CHAT_ID="0")
    send(app, msg("/start", chat=999))
    send(app, msg("/status", chat=999))
    assert len(world.telegram) == 1
    method, payload = world.telegram[0]
    assert payload["chat_id"] == "999" and "999" in payload["text"]


def test_strangers_are_ignored_once_chat_id_is_set():
    app, world, _, _ = make_app()  # TELEGRAM_CHAT_ID=42
    for text in ("/start", "/status", "checkin", "/checkout"):
        send(app, msg(text, chat=999))
    send(app, cb("co|2026-11-05|am", chat=999))
    assert world.telegram == [] and world.submitted == []


def test_set_override_and_reset():
    app, world, kv, _ = make_app()  # Thu 5 Nov
    send(app, msg("/campus fri"))
    assert kv.data["ov:2026-11-06"] == "campus"
    assert "On campus" in world.sent_texts()[-1] and "Fri 6 Nov" in world.sent_texts()[-1]
    send(app, msg("/reset fri"))
    assert "ov:2026-11-06" not in kv.data


def test_setting_default_mode_stores_nothing():
    app, world, kv, _ = make_app()
    send(app, msg("/home tomorrow"))  # Friday is already "home"
    assert "ov:2026-11-06" not in kv.data


def test_holiday_range():
    app, world, kv, _ = make_app()
    send(app, msg("/off 23/12-04/01"))
    assert kv.data["ov:2026-12-23"] == "off" and kv.data["ov:2027-01-04"] == "off"
    assert "(9 days)" in world.sent_texts()[-1]
    assert "report the absence" in world.sent_texts()[-1]


def test_bad_date():
    app, world, _, _ = make_app()
    send(app, msg("/home someday"))
    assert "didn't understand" in world.sent_texts()[-1]


def test_week_view_and_buttons():
    app, world, kv, _ = make_app()
    send(app, msg("/week"))
    _, payload = world.telegram[-1]
    rows = payload["reply_markup"]["inline_keyboard"]
    assert len(rows) == 6  # 5 days + navigation
    assert rows[1][0]["text"] == "Tue 3 Nov" and rows[1][1]["text"] == "[🏫]"  # Tue = campus

    send(app, cb("m|2026-11-06|campus|2026-11-02"))
    assert kv.data["ov:2026-11-06"] == "campus"
    methods = [m for m, _ in world.telegram[-2:]]
    assert methods == ["editMessageText", "answerCallbackQuery"]
    edited = world.telegram[-2][1]
    assert "Fri 6 Nov — 🏫 On campus ✎" in edited["text"]


def test_evening_answer_button():
    app, world, kv, _ = make_app()
    send(app, cb("a|2026-11-06|off"))
    assert kv.data["ov:2026-11-06"] == "off"
    assert world.telegram[0][0] == "editMessageText" and "👍" in world.telegram[0][1]["text"]


def test_buttons_from_other_chats_are_ignored():
    app, world, kv, _ = make_app()
    send(app, cb("a|2026-11-06|off", chat=999))
    assert kv.data == {} and world.telegram == []


def test_times():
    app, world, _, _ = make_app()
    send(app, msg("/time am_in 8:58"))
    assert run(app.store.get_settings()).times["am_in"] == "08:58"
    assert "08:58" in world.sent_texts()[-1]
    send(app, msg("/time lunch 12:00"))
    assert "Slot must be one of" in world.sent_texts()[-1]
    send(app, msg("/time am_in 25:00"))
    assert "Invalid time" in world.sent_texts()[-1]


def test_pause_and_status():
    app, world, _, _ = make_app()
    send(app, msg("/pause"))
    send(app, msg("/status"))
    assert "PAUSED" in world.sent_texts()[-1]
    assert "Thu 5 Nov" in world.sent_texts()[-1]


def buttons(world):
    """All buttons (text, callback_data) of the last message sent."""
    markup = world.telegram[-1][1].get("reply_markup", {})
    return [(b["text"], b["callback_data"]) for row in markup.get("inline_keyboard", []) for b in row]


def last_edit(world):
    return next(p["text"] for m, p in reversed(world.telegram) if m == "editMessageText")


def test_checkin_asks_first_then_acts_on_yes():
    app, world, kv, _ = make_app(now="2026-11-05 09:30")  # Thursday = campus day
    send(app, msg("/checkin"))
    assert world.moodle_requests == 0  # nothing happened yet
    assert "Morning check-in" in world.sent_texts()[-1]
    btns = buttons(world)
    assert ("✅ 🏫 On campus", "ci|2026-11-05|am|campus") in btns  # planned mode pre-selected
    assert ("🏠 At home", "ci|2026-11-05|am|home") in btns
    assert ("✖️ Cancel", "cx") in btns

    send(app, cb("ci|2026-11-05|am|home"))  # pick home instead
    assert "working on it" in last_edit(world)
    assert world.state["am"] == "in" and world.submitted[-1]["location"] == "athome"
    assert world.sent_texts()[-1].startswith("✅")
    assert kv.data.get("run:2026-11-05:am_in")  # the cron won't do it again


def test_checkin_with_mode_preselects_it():
    app, world, _, _ = make_app(now="2026-11-05 09:30")
    send(app, msg("/checkin home"))
    assert ("✅ 🏠 At home", "ci|2026-11-05|am|home") in buttons(world)


def test_checkout_asks_first_and_cancel_does_nothing():
    app, world, _, _ = make_app(now="2026-11-05 09:30")
    world.state["am"] = "in"
    send(app, msg("checkout"))  # plain text works too
    assert world.moodle_requests == 0
    assert buttons(world) == [("✅ Yes, check out", "co|2026-11-05|am"), ("✖️ Cancel", "cx")]
    send(app, cb("cx"))
    assert world.moodle_requests == 0 and world.state["am"] == "in"
    assert "Cancelled" in last_edit(world)


def test_checkout_yes_checks_out():
    app, world, _, _ = make_app(now="2026-11-05 12:20")
    world.state["am"] = "in"
    send(app, msg("Check out"))
    send(app, cb("co|2026-11-05|am"))
    assert world.state["am"] == "out"
    assert world.sent_texts()[-1].startswith("✅")


def test_old_confirmation_button_expires():
    app, world, _, clock = make_app(now="2026-11-05 09:30")
    world.state["am"] = "in"
    send(app, msg("/checkout"))
    clock.set("2026-11-05 14:00")  # afternoon now: the morning button must not act
    send(app, cb("co|2026-11-05|am"))
    assert world.state["am"] == "in" and "expired" in last_edit(world)
    clock.set("2026-11-06 09:30")  # next day
    send(app, cb("co|2026-11-05|am"))
    assert "expired" in last_edit(world)


def test_plain_text_aliases():
    app, world, _, _ = make_app(now="2026-11-05 14:00")
    for text in ("checkin", "check in", "Check-in campus", "/checkin@my_bot"):
        send(app, msg(text))
        assert "Afternoon check-in" in world.sent_texts()[-1], text


def test_test_command():
    app, world, _, _ = make_app()
    send(app, msg("/test"))
    last = world.sent_texts()[-1]
    assert "Login OK" in last and "9AM - 12:30PM" in last and "Check in" in last


def test_errors_are_reported_not_raised():
    app, world, _, _ = make_app(MOODLE_PASSWORD="")
    send(app, msg("/test"))
    assert world.sent_texts()[-1].startswith("⚠️")


def test_group_chats_and_other_members_are_ignored():
    app, world, _, _ = make_app()  # owner = 42
    group = {"message": {"chat": {"id": 42, "type": "group"}, "from": {"id": 42}, "text": "/pause"}}
    other_member = {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 7}, "text": "/pause"}}
    other_button = {
        "callback_query": {"id": "q", "data": "co|2026-11-05|am", "from": {"id": 7},
                           "message": {"chat": {"id": 42, "type": "private"}, "message_id": 1}}
    }
    for u in (group, other_member, other_button):
        send(app, u)
    assert world.telegram == [] and world.submitted == []


def test_telegram_header_secret_is_not_the_setup_secret():
    from checkin.config import Config

    cfg = Config(webhook_secret="S", telegram_token="T")
    assert cfg.telegram_header_secret and cfg.telegram_header_secret != "S"
    assert Config(webhook_secret="S", telegram_token="").telegram_header_secret == ""
