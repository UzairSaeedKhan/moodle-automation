# Moodle attendance bot

An automation for **Moodle attendance**: it checks you in and out of BeCode's Moodle on schedule.

> **About this project.** I built this as a side project to learn about automation and the tools behind it: Cloudflare Python Workers, scheduled jobs, KV storage, Telegram bots and testing. It is **not open source** and not an official tool: whether it may be used depends entirely on the coaches and teachers. Please ask them first.

Written in **Python**, it runs on **your own free Cloudflare account** (Python Workers), so your computer doesn't need to be on and nobody else ever sees your password. A **Telegram bot** to control it is optional.

- Weekdays at 09:00 / 12:30 / 13:30 / 17:00 (you can change these)
- Picks **On-Campus** or **At Home** from your weekly pattern (default for GEBRU-1: Tue & Thu on campus)
- With Telegram: change any day by tapping buttons in `/week` or typing `/home tomorrow`, get ✅ after each action and ⚠️ with a link if something fails, and an optional evening question ("Where will you be tomorrow?")
- Without Telegram: plan holidays and one-off days with the `DAY_OVERRIDES` setting

> ⚠️ **Only use it on days you really attend.** The bot checks you in even if you're sick or overslept, unless you mark the day `off` or pause it. Attendance can be linked to your training contract or to VDAB / Actiris / Le Forem support, so a wrong record is your responsibility. Ask your coach whether automated check-in is allowed before using it. See [Security & responsible use](#security--responsible-use).

## Choose your mode

| | **Automation only** | **With Telegram** (recommended) |
|---|---|---|
| Checks in/out on schedule | ✅ | ✅ |
| Change a day | Edit `DAY_OVERRIDES` (Cloudflare dashboard or `wrangler.toml`) | Tap a button / type `/home fri` |
| Tells you when something fails | ❌ check Moodle yourself | ✅ ⚠️ message with a link |
| Check in/out on demand | ❌ | ✅ `/checkin`, `/checkout` |
| Setup | Steps 1, 3, 4, 5 (Moodle secrets only), 6 | All steps |

You can add Telegram later: set its secrets (step 5), deploy again, and do step 7.

## Setup (≈10 minutes, once). Works on macOS and Windows

You need:
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python tooling). macOS: `brew install uv`. Windows: `winget install astral-sh.uv`.
- [Node.js 20+](https://nodejs.org) (Cloudflare's CLI uses it behind the scenes)
- A free [Cloudflare account](https://dash.cloudflare.com/sign-up) and Telegram

### 1. Get the code

```bash
git clone <this-repo-url> moodle-checkin-bot
cd moodle-checkin-bot
uv sync                              # installs Python deps (incl. pywrangler)
uv run pywrangler login              # opens the browser to connect your Cloudflare account
```

### 2. Create your Telegram bot *(Telegram mode only)*

In Telegram, open **@BotFather** and send `/newbot`. Pick a name and copy the **token** it gives you.

### 3. Create the storage

```bash
uv run pywrangler kv namespace create STATE
```

Copy the `id` it prints into `wrangler.toml` (replace `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`).

### 4. Adjust `wrangler.toml` for your class (if needed)

| Setting | What it is |
|---|---|
| `ATTENDANCE_CMID` | The number after `?id=` in your class's Attendance page URL (`.../mod/attendance/view.php?id=1491` → `1491`) |
| `WEEKLY_PATTERN` | Your default week, e.g. `mon:home,tue:campus,wed:home,thu:campus,fri:home` |
| `TIME_*` | Default times (can also be changed later with `/time`) |

### 5. Add your secrets

Each command asks you to paste a value. Paste it at the prompt; don't put it on the command line, where it would end up in your shell history. Values are stored encrypted in **your** Cloudflare account and are never in the code.

For `TELEGRAM_WEBHOOK_SECRET`, make up a long random string (letters, digits, `_` and `-` only), for example with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

```bash
uv run pywrangler secret put MOODLE_USERNAME
uv run pywrangler secret put MOODLE_PASSWORD
# Telegram mode only:
uv run pywrangler secret put TELEGRAM_BOT_TOKEN
uv run pywrangler secret put TELEGRAM_WEBHOOK_SECRET   # the random string from above
uv run pywrangler secret put TELEGRAM_CHAT_ID          # type 0 for now, we'll fix it in step 7
```

### 6. Deploy

```bash
uv run pywrangler deploy
```

It prints your Worker URL, like `https://moodle-checkin-bot.<you>.workers.dev`. Opening it shows whether it runs *with Telegram* or *automation-only*.

**Automation only: you're done.** Skip to [Without Telegram](#without-telegram-changing-your-plan).

**Telegram mode:** open this in your browser once:

```
https://moodle-checkin-bot.<you>.workers.dev/setup?secret=<your TELEGRAM_WEBHOOK_SECRET>
```

You should see `"ok": true` twice. That connects Telegram to your bot.

### 7. Link your Telegram account *(Telegram mode only)*

Send `/start` to your bot. It replies with **your chat id** (it only does this while `TELEGRAM_CHAT_ID` is `0`; afterwards it ignores everyone but you). Save it:

```bash
uv run pywrangler secret put TELEGRAM_CHAT_ID    # paste the number
```

Now send `/test`. The bot logs into Moodle and shows today's sessions. If you see "Login OK", you're done 🎉

## Commands

| Command | Example |
|---|---|
| `/status` | Today's plan and what has happened so far |
| `/week` | This week, with buttons to change each day (`/week next` for next week) |
| `/campus`, `/home`, `/off` | `/home tomorrow`, `/campus 12/11`, `/off 23/12-03/01`, `/home mon wed`, `/off nextweek` |
| `/reset` | Back to the default pattern: `/reset fri` |
| `/ask on` / `/ask off` | Evening question the day before (at `ASK_TIME`) |
| `/times`, `/time` | `/time am_in 08:58`, `/time am_in default` |
| `/pause`, `/resume` | Stop/restart everything (holidays, sick days) |
| `/checkin`, `/checkout` | Do it now. Asks you to confirm with a button first (pick 🏫/🏠 for check-in). Typing `checkin` or `check out` also works |
| `/test` | Check login and read today's sessions |

**Which mode is used for a day:** your Telegram change for that date (buttons, commands or your evening answer) → otherwise `DAY_OVERRIDES` → otherwise your `WEEKLY_PATTERN`.

## Without Telegram: changing your plan

Two optional settings (they work in Telegram mode too):

| Setting | Example | Meaning |
|---|---|---|
| `DAY_OVERRIDES` | `12/11:home, 23/12-03/01:off, 01/12/2026:campus` | One-off days and ranges (`dd/mm`, or `dd/mm/yyyy` for one specific year). Modes: `campus`, `home`, `off` |
| `PAUSED` | `true` | Stops everything until set back to `false` |

Change them **without a terminal** (works from a phone):
Cloudflare dashboard → Workers & Pages → `moodle-checkin-bot` → Settings → Variables and Secrets → Add / Edit → type **Text** → Deploy. It takes effect within a minute.

Or add them under `[vars]` in `wrangler.toml` and run `uv run pywrangler deploy`. Pick one place: a value in `wrangler.toml` overwrites the dashboard on the next deploy. (`keep_vars = true` keeps dashboard-only variables.)

> Days set to `off` don't report an absence in Moodle; do that yourself if needed.

## How it works

- A Cloudflare **cron trigger runs every minute**. It converts the time to `TIMEZONE` (so summer and winter time are handled) and checks whether a check-in or check-out is due.
- When one is due, it logs into Moodle with normal HTTP requests (no browser), opens the Attendance page and finds today's morning or afternoon row:
  - **Check in:** opens the check-in link, picks the dropdown option containing `campus` or `home`, and submits.
  - **Check out:** follows the *Check out* link.
- It then reloads the page to **verify** the change. If the button isn't there yet or Moodle errors, it retries every minute for `RETRY_MINUTES`, then sends ⚠️.
- If you already did it by hand, it notices ("Already checked in") and does nothing.

### Code layout

```
src/entry.py          Cloudflare glue (fetch, KV, cron, webhook). The only runtime-specific file
src/checkin/
  app.py              the scheduler: what happens each minute, retries, notifications
  bot.py              Telegram commands and buttons
  moodle.py           Moodle client: login, read attendance table, check in/out
  html.py             HTML/form parsing tailored to Moodle
  store.py            settings, per-day overrides, run state (in KV)
  timeutil.py         timezone + date parsing ("fri", "23/12-03/01", …)
  config.py, telegram.py, ports.py
tests/                pytest unit tests with a fake Moodle + fake Telegram
tests/e2e/            end-to-end test against the real local Workers runtime
```

## Development

```bash
uv run pytest                             # unit tests (fast, no network)
uv run python tests/e2e/run_e2e.py        # runs the real Worker locally against a fake Moodle/Telegram
cp .dev.vars.example .dev.vars            # fill in, then:
uv run pywrangler dev --test-scheduled    # local worker; trigger the cron with:
curl "http://localhost:8787/__scheduled?cron=*+*+*+*+*"
```

## Security & responsible use

**Where your data lives**

| What | Where | In this repo? |
|---|---|---|
| Moodle username and password, Telegram token, chat id, webhook secret | Cloudflare secrets in **your** account, encrypted, readable only by your Worker | Never |
| Your plan, times and run history | Your own KV storage in your Cloudflare account | Never |
| `.dev.vars` (only for local testing) | Your computer, ignored by git | Never |
| Settings in `wrangler.toml` (URL, course id, times, pattern) | The repo | Yes, nothing secret |

**Built-in protections**

- Your password and Moodle session are only ever sent to `MOODLE_URL`, and only over `https://`. Redirects or forms pointing anywhere else are refused.
- Telegram messages are only accepted with your webhook secret, and only from your own chat id. Everyone else gets no reply.
- Secrets are never logged or printed.
- On the free Cloudflare plan nothing can cost you money: the limits stop requests, they never bill you. The bot uses about 1,450 of the 100,000 daily requests.

**Your part**

1. Turn on two-factor login (2FA) for your **Cloudflare** account, and set a cloud password in Telegram (Settings → Privacy and Security).
2. Don't reuse your Moodle password anywhere else.
3. Never commit `.dev.vars`. Run `git status` before your first push.
4. If a secret leaks: change your Moodle password, or `/revoke` the token in @BotFather, then `uv run pywrangler secret put …` with the new value.
5. If your URL is ever flooded with requests, change your `workers.dev` subdomain in the Cloudflare dashboard and open `/setup` again.

**Privacy (GDPR)**

Everyone runs their **own** copy with their own data. The maintainer never receives anyone's password or attendance. Please don't host a shared copy for other people: you would then be responsible for their personal data under GDPR. Your data is stored with Cloudflare and, in Telegram mode, Telegram, under their privacy terms.

The bot follows fixed rules (times and your plan). It doesn't use AI to make decisions, so it isn't an "AI system" under the EU AI Act.

**Responsible use**

You are responsible for your own attendance record. Use `/off`, `/pause` or `DAY_OVERRIDES` for days you don't attend, and check with your school that automated check-in is allowed. This project is not affiliated with BeCode or Moodle.

## Troubleshooting

- **"Login failed"**: check `MOODLE_USERNAME` / `MOODLE_PASSWORD` (`uv run pywrangler secret put …` again).
- **`DAY_OVERRIDES` doesn't apply**: with Telegram, `/status` lists entries it couldn't read. Use `dd/mm:mode`, separated by commas.
- **"No option containing … in the check-in form"**: the message lists the options it found. Set `CAMPUS_OPTION_MATCH` / `HOME_OPTION_MATCH` in `wrangler.toml` to part of the right option text, then deploy again.
- **"Couldn't read the attendance table"**: `ATTENDANCE_CMID` is probably wrong.
- **"Exceeded CPU time limit"** in the logs: the free plan allows 10 ms of CPU per run. If you hit it, tell the maintainer.
- **Logs**: `uv run pywrangler tail` shows live logs from your Worker.

## License

© 2026 Uzair Saeed Khan. All rights reserved. This code is shared for viewing and learning only. It is not open source: please ask before copying, modifying, or using it.

## Author

Built by Uzair Saeed Khan as a side project to learn automation. Questions, ideas, or just want to talk automation? Let's connect on [LinkedIn](https://www.linkedin.com/in/uzairsaeedkhan/).
