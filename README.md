# Filings·Desk — automated SMS alerts

Texts you when one of your tickers files something material with the SEC, or when a
post about it starts trending on Reddit. Runs itself on GitHub Actions (free) — no
server, nothing to keep open. You only add your Twilio keys + phone number as
GitHub **secrets**; they never live in the code.

## What it watches
- **SEC EDGAR** — new material filings (8-K, 10-Q, 10-K, 6-K, 20-F, S-1, 424B, SC 13D/G, DEF 14A…). Insider Form 4s and 13Fs are filtered out so you're not spammed. US-listed names only.
- **Reddit** — posts about `$TICKER` that cross an upvote threshold (default 80) in the last day.

Non-US names (VEQT, ATZ), ETFs, and crypto skip EDGAR automatically and are covered by the Reddit check only.

Two message types land on your phone:
- **Intraday** — the moment a new material filing or trending Reddit post appears (checked every 30 min).
- **Daily 8:00 AM recap** — one text each morning summarizing the last 24h of filings and the day's top Reddit posts across the whole list. Sent at **08:00 America/Toronto (Eastern)** and stays correct through daylight-saving changes automatically. Change the time/zone with the `SUMMARY_HOUR` / `SUMMARY_TZ` env values in the workflow; set `SUMMARY_ENABLED` to `"false"` to turn it off, or `SUMMARY_SKIP_IF_EMPTY` to `"true"` to skip quiet days.

---

## Setup (about 10 minutes)

### 1. Get a Twilio number
- Sign up at twilio.com, get a phone number that can send SMS.
- From the console copy your **Account SID** and **Auth Token**.
- ⚠️ On a **trial** account, Twilio can only text *verified* numbers and prefixes a trial banner. Verify your own cell in the console, or upgrade (a few dollars) to text freely.

### 2. Put these files in a new GitHub repo
Create a repo (public = unlimited free Actions minutes; private also works within the free monthly minutes), then add every file here, keeping the folder layout:

```
watchlist.json
alerts.py
requirements.txt
state.json
README.md
.github/workflows/alerts.yml
```

### 3. Add your secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Secret name          | Value                                             |
|----------------------|---------------------------------------------------|
| `TWILIO_ACCOUNT_SID` | your Twilio Account SID (starts `AC…`)            |
| `TWILIO_AUTH_TOKEN`  | your Twilio Auth Token                            |
| `TWILIO_FROM`        | your Twilio number, e.g. `+15555550123`           |
| `ALERT_TO`           | your cell, e.g. `+15145550199`                    |
| `SEC_USER_AGENT`     | `Your Name your@email.com` (SEC requires a real contact) |

### 4. Turn it on
- Repo → **Actions** tab → enable workflows if prompted.
- Click **watchlist-alerts → Run workflow** to test immediately.
- The **first run "arms" the system**: it records what already exists and texts you a one-line confirmation, so you don't get blasted with filing history. After that, you only get texts for *new* items.

That's it. It now runs every 30 minutes on weekdays on its own.

---

## Tuning

- **Add/remove tickers:** edit `watchlist.json`. `edgar: true` = US company with SEC filings; `false` = ETF/crypto/foreign (Reddit-only). `rq` overrides the Reddit search term.
- **Quieter/louder:** in `.github/workflows/alerts.yml` change the `cron` lines. GitHub's minimum is 5 min and runs can be delayed 5–15 min. Higher `REDDIT_MIN_SCORE` = fewer Reddit texts. Set `REDDIT_ENABLED` to `"false"` to drop Reddit entirely.
- **Which filings count:** edit `MATERIAL_FORMS` in `alerts.py`. Add `"4"` if you *want* insider-trade pings.

## Costs & limits
- **GitHub Actions:** free for public repos; private repos get 2,000 free min/month (this uses well under that at 30-min cadence).
- **Twilio:** ~US$0.0079 per SMS segment in North America, plus a small monthly number fee. Long digests may span 2–3 segments.
- **SEC:** free; the required `SEC_USER_AGENT` contact is enforced — set a real email or requests get blocked.
- **Reddit:** the public JSON endpoint is unauthenticated and best-effort; occasional `429` rate-limits are skipped silently. If it gets flaky, tell me and I'll switch it to the authenticated Reddit API (two more secrets).

## How "no duplicates" works
`state.json` remembers the IDs already sent. The workflow commits it back to the repo after each run, so the next run knows what's new. You'll see small automated "update alert state" commits — that's expected.

## Not financial advice
This surfaces information. Reddit/social items are unverified — verify before acting.
