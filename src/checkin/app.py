"""The scheduler: what happens every minute."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .config import MODE_LABEL, SLOTS, Config, Slot
from .html import FormMismatchError
from .moodle import Moodle, MoodleError, Outcome
from .ports import KV, Http
from .store import RunState, Settings, Store
from .telegram import Telegram, ask_tomorrow_markup, esc
from .timeutil import LocalNow, add_days, hhmm_to_minutes, is_weekday, local_now, short_label


class App:
    def __init__(self, cfg: Config, http: Http, kv: KV, clock: Callable[[], datetime]):
        self.cfg = cfg
        self.http = http
        self.store = Store(kv, cfg)
        self.tg = Telegram(http, cfg)
        self.clock = clock

    def now(self, when: datetime | None = None) -> LocalNow:
        return local_now(when or self.clock(), self.cfg.timezone)

    def moodle(self) -> Moodle:
        return Moodle(self.cfg.moodle_url, self.http)

    # ------------------------------------------------------------------
    async def run_minute(self, when: datetime) -> None:
        """Called by the cron trigger every minute."""
        now = self.now(when)
        settings = await self.store.get_settings()

        await self._maybe_ask_tomorrow(now, settings)

        if settings.paused or self.cfg.paused or not is_weekday(now.ymd):
            return

        retry = self.cfg.retry_minutes
        for slot in SLOTS:
            t = hhmm_to_minutes(self.store.slot_time(settings, slot.id))
            # Attempt window [t, t+retry); 5 more minutes of grace to report a final failure.
            if not (t <= now.minutes < t + retry + 5):
                continue
            state = await self.store.get_run(now.ymd, slot.id)
            if state and state.final:
                continue

            mode, _ = await self.store.resolve_mode(now.ymd)
            if mode == "off":
                await self.store.put_run(now.ymd, slot.id, RunState(True, "off", 0, at=now.hhmm))
                continue

            if now.minutes >= t + retry:
                await self._final_failure(now, slot, state)
                continue

            await self.attempt_slot(now, slot, mode, state, last_chance=now.minutes >= t + retry - 1)

    # ------------------------------------------------------------------
    async def attempt_slot(
        self, now: LocalNow, slot: Slot, mode: str, prev: RunState | None, last_chance: bool, manual: bool = False
    ) -> RunState:
        """One check-in/out attempt. Also used by the manual /checkin and /checkout commands."""
        attempts = (prev.attempts if prev else 0) + 1
        try:
            outcome = await self._perform(now.ymd, slot, mode)
            state = _outcome_state(outcome, attempts, now.hhmm)
            if state.final or manual:
                await self.tg.send(_format_outcome(now.ymd, slot, mode, outcome))
        except Exception as e:  # noqa: BLE001 - every failure must reach the user
            permanent = (isinstance(e, MoodleError) and not e.retryable) or isinstance(e, FormMismatchError)
            message = self.cfg.redact(str(e) or e.__class__.__name__)
            state = RunState(False, "pending", attempts, message, now.hhmm)
            if permanent or last_chance or manual:
                state = RunState(not manual, "failed", attempts, message, now.hhmm)
                await self.tg.send(self._failure_text(now.ymd, slot, message, manual))
        if not manual or state.final:
            await self.store.put_run(now.ymd, slot.id, state)
        return state

    async def _perform(self, ymd: str, slot: Slot, mode: str) -> Outcome:
        m = self.moodle()
        await m.login(self.cfg.moodle_username, self.cfg.moodle_password)
        if slot.action == "checkin":
            return await m.check_in(self.cfg.attendance_cmid, ymd, slot.half, self.cfg.option_match(mode))
        return await m.check_out(self.cfg.attendance_cmid, ymd, slot.half)

    def _failure_text(self, ymd: str, slot: Slot, message: str, manual: bool) -> str:
        text = f"⚠️ <b>{slot.label} failed · {short_label(ymd)}</b>\n{esc(message)}"
        if not manual:
            text += f"\n\n👉 Please do it manually: {self.cfg.attendance_url}"
        return text

    async def _final_failure(self, now: LocalNow, slot: Slot, state: RunState | None) -> None:
        detail = (state.detail if state else None) or "The bot didn't get a chance to run (no attempt recorded)."
        await self.store.put_run(now.ymd, slot.id, RunState(True, "failed", state.attempts if state else 0, detail, now.hhmm))
        await self.tg.send(self._failure_text(now.ymd, slot, detail, False))

    async def _maybe_ask_tomorrow(self, now: LocalNow, settings: Settings) -> None:
        """Optional evening question (only with /ask on)."""
        if not settings.ask or settings.paused or self.cfg.paused or not self.cfg.telegram_enabled:
            return
        t = hhmm_to_minutes(self.cfg.ask_time)
        if not (t <= now.minutes < t + 30):
            return
        tomorrow = add_days(now.ymd, 1)
        if not is_weekday(tomorrow) or await self.store.flag(f"asked:{tomorrow}"):
            return
        await self.store.set_flag(f"asked:{tomorrow}")
        mode, source = await self.store.resolve_mode(tomorrow)
        await self.tg.send(
            f"🗓 <b>Tomorrow, {short_label(tomorrow)}</b>\n"
            f"Currently planned: {MODE_LABEL[mode]} {_source_text(source)}\n\n"
            "Where will you be? If you don't answer, I'll keep this plan.",
            reply_markup=ask_tomorrow_markup(tomorrow),
        )


def _source_text(source: str) -> str:
    return {"override": "(set by you)", "config": "(from DAY_OVERRIDES)"}.get(source, "(default)")


def _outcome_state(o: Outcome, attempts: int, at: str) -> RunState:
    result = {"done": "ok", "already": "already", "no_session": "no_session"}.get(o.kind)
    if result:
        return RunState(True, result, attempts, o.detail, at)
    return RunState(False, "pending", attempts, o.detail, at)  # not_open: retry next minute


def _format_outcome(ymd: str, slot: Slot, mode: str, o: Outcome) -> str:
    what = f"{slot.label} · {short_label(ymd)}"
    if o.kind == "done":
        where = f" — {MODE_LABEL[mode]}" if slot.action == "checkin" else ""
        return f"✅ <b>{what}</b>{where}\n{esc(o.detail)}"
    if o.kind == "already":
        return f"☑️ <b>{what}</b>: {esc(o.detail)}"
    if o.kind == "no_session":
        return f"ℹ️ <b>{what}</b>: {esc(o.detail)} — nothing to do."
    return f"⏳ <b>{what}</b>: {esc(o.detail)}"
