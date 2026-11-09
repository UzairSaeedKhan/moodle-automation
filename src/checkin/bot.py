"""Telegram commands and button presses."""

from __future__ import annotations

from typing import Any

from .app import App
from .config import MODE_EMOJI, MODE_LABEL, MODES, SLOT_IDS, SLOTS, is_mode
from .html import find_row
from .telegram import esc
from .timeutil import (
    add_days,
    current_work_week_monday,
    is_weekday,
    moodle_date_label,
    normalize_hhmm,
    parse_when,
    short_label,
)

DATE_HELP = "Examples: <code>tomorrow</code>, <code>fri</code>, <code>12/11</code>, <code>23/12-03/01</code>, <code>nextweek</code>"
ABSENCE_HINT = "\n\nℹ️ Remember to report the absence in Moodle if needed."


async def handle_update(app: App, update: dict[str, Any]) -> None:
    cfg = app.cfg
    if "callback_query" in update:
        q = update["callback_query"]
        if str(q.get("message", {}).get("chat", {}).get("id")) != str(cfg.telegram_chat_id):
            return
        await _handle_callback(app, q)
        return

    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return
    chat_id = str(msg.get("chat", {}).get("id"))
    if chat_id != str(cfg.telegram_chat_id):
        # Only during setup (TELEGRAM_CHAT_ID not set yet, or 0): tell the owner which id to use.
        # Once it's set, strangers get no reply at all.
        if text.startswith("/start") and str(cfg.telegram_chat_id or "0").strip() in ("", "0"):
            await app.tg.send(f"Your chat id is {chat_id}.\nSet it as the TELEGRAM_CHAT_ID secret to use this bot.", chat_id=chat_id)
        return

    cmd, *args = _normalize(text).split()
    cmd = cmd.lower().split("@", 1)[0]
    try:
        await _command(app, cmd, args)
    except Exception as e:  # noqa: BLE001
        await app.tg.send(f"⚠️ {esc(str(e) or e.__class__.__name__)}")


_ALIASES = {"checkin": "/checkin", "check-in": "/checkin", "checkout": "/checkout", "check-out": "/checkout"}


def _normalize(text: str) -> str:
    """Lets you type 'checkout', 'check out', 'Check in home' without the slash."""
    words = text.split()
    low = [w.lower() for w in words]
    if len(low) >= 2 and low[0] == "check" and low[1] in ("in", "out"):
        return " ".join([f"/check{low[1]}"] + words[2:])
    if low[0] in _ALIASES:
        return " ".join([_ALIASES[low[0]]] + words[1:])
    return text


def _current_half(app: App) -> str:
    return "am" if app.now().minutes < 12 * 60 + 45 else "pm"


def _confirm_markup(action: str, ymd: str, half: str, mode: str) -> dict[str, Any]:
    if action == "checkin":
        row = [
            {"text": ("✅ " if m == mode else "") + MODE_LABEL[m], "callback_data": f"ci|{ymd}|{half}|{m}"}
            for m in ("campus", "home")
        ]
    else:
        row = [{"text": "✅ Yes, check out", "callback_data": f"co|{ymd}|{half}"}]
    return {"inline_keyboard": [row, [{"text": "✖️ Cancel", "callback_data": "cx"}]]}


async def _command(app: App, cmd: str, args: list[str]) -> None:
    store, send = app.store, app.tg.send
    today = app.now().ymd

    if cmd in ("/start", "/help"):
        await send(_help_text(app))

    elif cmd == "/status":
        await send(await _status_text(app))

    elif cmd == "/week":
        mon = current_work_week_monday(today)
        if args and args[0].lower() == "next":
            mon = add_days(mon, 7)
        text, markup = await _week_view(app, mon)
        await send(text, reply_markup=markup)

    elif cmd in ("/campus", "/home", "/off", "/reset"):
        days = parse_when(" ".join(args), today)
        if days is None:
            await send(f"I didn't understand the date. {DATE_HELP}")
            return
        if not days:
            await send("No weekdays in that range.")
            return
        mode = None if cmd == "/reset" else cmd[1:]
        for d in days:
            await store.set_override(d, mode)
        summary = (
            ", ".join(short_label(d) for d in days)
            if len(days) <= 7
            else f"{short_label(days[0])} → {short_label(days[-1])} ({len(days)} days)"
        )
        head = MODE_LABEL[mode] if mode else "↩️ Back to default"
        await send(f"{head}: {summary}{ABSENCE_HINT if mode == 'off' else ''}")

    elif cmd == "/ask":
        s = await store.get_settings()
        if args and args[0].lower() in ("on", "off"):
            s.ask = args[0].lower() == "on"
            await store.save_settings(s)
        await send(
            f"Evening question is <b>{'ON' if s.ask else 'OFF'}</b> ({app.cfg.ask_time}, the day before each weekday). "
            "If you don't answer, the plan stays as it is.\nUse <code>/ask on</code> or <code>/ask off</code>."
        )

    elif cmd in ("/pause", "/resume"):
        s = await store.get_settings()
        s.paused = cmd == "/pause"
        await store.save_settings(s)
        await send("⏸ Paused. Nothing will happen until /resume." if s.paused else "▶️ Resumed.")

    elif cmd in ("/times", "/time"):
        s = await store.get_settings()
        if len(args) >= 2:
            slot_id, value = args[0].lower(), args[1]
            if slot_id not in SLOT_IDS:
                await send("Slot must be one of: am_in, am_out, pm_in, pm_out")
                return
            if value.lower() == "default":
                s.times.pop(slot_id, None)
            else:
                s.times[slot_id] = normalize_hhmm(value)
            await store.save_settings(s)
        lines = [
            f"<code>{sl.id}</code>  {store.slot_time(s, sl.id)}  {sl.label}{' ✎' if sl.id in s.times else ''}" for sl in SLOTS
        ]
        await send(
            "⏰ <b>Times</b>\n" + "\n".join(lines) + "\n\nChange: <code>/time am_in 08:58</code> · reset: <code>/time am_in default</code>"
        )

    elif cmd in ("/checkin", "/checkout"):
        # Never act straight away: ask first, act on the button press.
        now = app.now()
        half = _current_half(app)
        action = cmd[1:]
        slot = next(sl for sl in SLOTS if sl.half == half and sl.action == action)
        if action == "checkin":
            wanted = args[0].lower() if args else ""
            mode = wanted if wanted in ("campus", "home") else (await store.resolve_mode(now.ymd))[0]
            mode = "home" if mode == "off" else mode
            question = f"❓ <b>{slot.label}</b> now ({short_label(now.ymd)})?\nPick where you are:"
        else:
            mode = ""
            question = f"❓ <b>{slot.label}</b> now ({short_label(now.ymd)})?"
        await send(question, reply_markup=_confirm_markup(action, now.ymd, half, mode))

    elif cmd == "/test":
        await send("🔌 Logging in to Moodle…")
        m = app.moodle()
        await m.login(app.cfg.moodle_username, app.cfg.moodle_password)
        rows = await m.sessions(app.cfg.attendance_cmid)
        label = moodle_date_label(today)
        todays = [r for r in (find_row(rows, label, h) for h in ("am", "pm")) if r]
        lines = [
            f"• {esc(r.time)} — status: {esc(r.status or '?')} — buttons: {esc(', '.join(l.text for l in r.links) or 'none')}"
            for r in todays
        ]
        await send(f"✅ Login OK. Read {len(rows)} sessions.\n<b>Today ({label})</b>\n" + ("\n".join(lines) or "No sessions today."))

    else:
        await send("Unknown command. Try /help")


async def _handle_callback(app: App, q: dict[str, Any]) -> None:
    parts = str(q.get("data") or "").split("|")
    kind = parts[0]
    ymd = parts[1] if len(parts) > 1 else ""
    mode = parts[2] if len(parts) > 2 else ""
    monday = parts[3] if len(parts) > 3 else ""
    chat_id = q["message"]["chat"]["id"]
    message_id = q["message"]["message_id"]

    async def ack(text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": q["id"]}
        if text:
            payload["text"] = text
        await app.tg.call("answerCallbackQuery", payload)

    async def edit(text: str, markup: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        await app.tg.call("editMessageText", payload)

    if kind in ("ci", "co"):
        await _confirmed(app, kind, ymd, mode, parts[3] if len(parts) > 3 else "", edit, ack)
    elif kind == "cx":
        await edit("✖️ Cancelled. Nothing was changed in Moodle.")
        await ack("Cancelled")
    elif kind == "m" and is_mode(mode):
        await app.store.set_override(ymd, mode)
        await edit(*await _week_view(app, monday))
        await ack(f"{short_label(ymd)}: {MODE_LABEL[mode]}")
    elif kind == "w" and ymd:
        await edit(*await _week_view(app, ymd))
        await ack()
    elif kind == "a" and is_mode(mode):
        await app.store.set_override(ymd, mode)
        await edit(f"👍 <b>{short_label(ymd)}</b>: {MODE_LABEL[mode]}{ABSENCE_HINT if mode == 'off' else ''}")
        await ack("Saved")
    else:
        await ack()


async def _confirmed(app: App, kind: str, ymd: str, half: str, mode: str, edit, ack) -> None:
    """The Yes button of a /checkin or /checkout confirmation."""
    now = app.now()
    if ymd != now.ymd or half != _current_half(app):
        await edit("⌛ This button has expired. Send /checkin or /checkout again.")
        await ack("Expired")
        return
    action = "checkin" if kind == "ci" else "checkout"
    if action == "checkin" and mode not in ("campus", "home"):
        await ack()
        return
    slot = next(sl for sl in SLOTS if sl.half == half and sl.action == action)
    where = f" ({MODE_LABEL[mode]})" if action == "checkin" else ""
    await edit(f"⏳ {slot.label}{where}: working on it…")
    await ack()  # answer the button quickly; the result comes as a new message
    await app.attempt_slot(now, slot, mode or "home", await app.store.get_run(now.ymd, slot.id), last_chance=False, manual=True)


async def _week_view(app: App, monday: str) -> tuple[str, dict[str, Any]]:
    lines = [f"🗓 <b>Week of {short_label(monday)}</b>", ""]
    keyboard: list[list[dict[str, str]]] = []
    for i in range(5):
        ymd = add_days(monday, i)
        mode, source = await app.store.resolve_mode(ymd)
        mark = {"override": " ✎", "config": " ⚙"}.get(source, "")
        lines.append(f"{short_label(ymd)} — {MODE_LABEL[mode]}{mark}")
        keyboard.append(
            [{"text": short_label(ymd), "callback_data": "x"}]
            + [
                {"text": f"[{MODE_EMOJI[m]}]" if m == mode else MODE_EMOJI[m], "callback_data": f"m|{ymd}|{m}|{monday}"}
                for m in MODES
            ]
        )
    lines += ["", "✎ = changed by you · ⚙ = from DAY_OVERRIDES. Tap an icon to change a day."]
    keyboard.append(
        [
            {"text": "« Prev week", "callback_data": f"w|{add_days(monday, -7)}"},
            {"text": "Next week »", "callback_data": f"w|{add_days(monday, 7)}"},
        ]
    )
    return "\n".join(lines), {"inline_keyboard": keyboard}


_SOURCE = {"override": "(set by you)", "config": "(from DAY_OVERRIDES)"}


async def _status_text(app: App) -> str:
    now = app.now()
    s = await app.store.get_settings()
    lines: list[str] = []
    if not is_weekday(now.ymd):
        lines.append(f"📅 {short_label(now.ymd)} — weekend, nothing scheduled.")
    else:
        mode, source = await app.store.resolve_mode(now.ymd)
        lines.append(f"📅 <b>{short_label(now.ymd)}</b> — {MODE_LABEL[mode]} {_SOURCE.get(source, '(default)')}")
        icon = {"ok": "✅", "already": "☑️", "failed": "⚠️", "off": "❌", "no_session": "ℹ️", "pending": "⏳"}
        for slot in SLOTS:
            r = await app.store.get_run(now.ymd, slot.id)
            st = f"{icon.get(r.result, '')} {r.result}{f' at {r.at}' if r.at else ''}" if r else "· not yet"
            lines.append(f"{app.store.slot_time(s, slot.id)}  {slot.label}: {st}")
    tomorrow = add_days(now.ymd, 1)
    if is_weekday(tomorrow):
        t_mode, _ = await app.store.resolve_mode(tomorrow)
        lines += ["", f"Tomorrow ({short_label(tomorrow)}): {MODE_LABEL[t_mode]}"]
    state = "⏸ PAUSED (PAUSED setting)" if app.cfg.paused else ("⏸ PAUSED" if s.paused else "▶️ Active")
    lines += ["", f"{state} · evening question: {'on' if s.ask else 'off'}"]
    if app.cfg.day_rules_invalid:
        lines.append("⚠️ Ignored DAY_OVERRIDES entries: " + esc(", ".join(app.cfg.day_rules_invalid)))
    return "\n".join(lines)


def _help_text(app: App) -> str:
    return "\n".join(
        [
            "🤖 <b>Moodle check-in bot</b>",
            "",
            "I check you in and out automatically on weekdays, using your weekly pattern unless you change a day.",
            "",
            "<b>Plan</b>",
            "/week — see &amp; change this week (tap icons)",
            "/campus, /home, /off, /reset + when",
            "  when = today, tomorrow, fri, 12/11, 23/12-03/01, nextweek",
            "/ask on|off — ask me the evening before",
            "",
            "<b>Control</b>",
            "/status — today's plan and results",
            "/times — show/change check-in/out times",
            "/pause, /resume",
            "/checkin, /checkout — do it now (asks to confirm first)",
            "  or just type <code>checkin</code> / <code>checkout</code>",
            "/test — test login &amp; read today's sessions",
            "",
            f"Default week: <code>{esc(app.cfg.weekly_pattern_raw)}</code>",
        ]
    )
