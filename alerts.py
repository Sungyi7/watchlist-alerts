#!/usr/bin/env python3
"""
Filings-Desk alerts.

Two kinds of text message:
  1. INTRADAY  — as soon as a new material SEC filing or a trending Reddit post
                 about one of your tickers appears (runs every 30 min).
  2. DAILY 8am — a once-a-day recap of the last 24h across the whole watchlist,
                 sent at 08:00 America/Toronto (Eastern), DST-safe.

Runs on GitHub Actions cron. State (already-sent items + last summary date) lives
in state.json, which the workflow commits back so nothing is sent twice.

Credentials come from environment variables (GitHub Actions secrets) — never code.
"""

import os
import sys
import json
import time
import html
import datetime as dt
from zoneinfo import ZoneInfo

import requests

# ----------------------------- config ---------------------------------------
STATE_FILE     = "state.json"
WATCHLIST_FILE = "watchlist.json"

SEC_UA = os.environ.get("SEC_USER_AGENT", "").strip() or "filings-desk-alerts contact@example.com"

# Material forms (prefix-matched: "8-K/A", "424B5", "10-K/A" all match). Edit to taste.
MATERIAL_FORMS = (
    "8-K", "10-Q", "10-K", "6-K", "20-F", "40-F", "S-1", "F-1",
    "424B", "425", "SC 13D", "SC 13G", "DEF 14A", "DEFA14A", "SC TO",
)

REDDIT_ENABLED   = os.environ.get("REDDIT_ENABLED", "true").lower() == "true"
REDDIT_MIN_SCORE = int(os.environ.get("REDDIT_MIN_SCORE", "80"))
REDDIT_UA        = os.environ.get("REDDIT_USER_AGENT", "").strip() or "filings-desk-alerts/1.0"

# Daily summary
SUMMARY_ENABLED       = os.environ.get("SUMMARY_ENABLED", "true").lower() == "true"
SUMMARY_HOUR          = int(os.environ.get("SUMMARY_HOUR", "8"))          # local hour
SUMMARY_TZ            = os.environ.get("SUMMARY_TZ", "America/Toronto")   # your timezone
SUMMARY_LOOKBACK_DAYS = int(os.environ.get("SUMMARY_LOOKBACK_DAYS", "1"))
SUMMARY_SKIP_IF_EMPTY = os.environ.get("SUMMARY_SKIP_IF_EMPTY", "false").lower() == "true"

STATE_CAP = 800
SMS_CAP   = 1400

_sub_cache = {}   # cik -> submissions json (per-run)


# ----------------------------- helpers --------------------------------------
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def sec_get(url):
    r = requests.get(url, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    r.raise_for_status()
    time.sleep(0.15)  # under SEC's 10 req/sec
    return r


def get_cik_map():
    data = sec_get("https://www.sec.gov/files/company_tickers.json").json()
    return {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}


def get_submissions(cik):
    if cik not in _sub_cache:
        _sub_cache[cik] = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    return _sub_cache[cik]


def is_material(form):
    form = (form or "").upper()
    return any(form.startswith(p.upper()) for p in MATERIAL_FORMS)


def filing_link(cik, acc, doc):
    acc_nodash = acc.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/"
    return base + doc if doc else base


def iter_filings(sym, cik):
    """Yield dicts for each recent filing of a ticker."""
    try:
        sub = get_submissions(cik)
    except Exception as e:
        print(f"[edgar] {sym}: {e}", file=sys.stderr)
        return
    recent = sub.get("filings", {}).get("recent", {})
    accs  = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    docs  = recent.get("primaryDocument", [])
    for i, acc in enumerate(accs):
        yield {
            "acc":  acc,
            "form": forms[i] if i < len(forms) else "",
            "date": dates[i] if i < len(dates) else "",
            "doc":  docs[i]  if i < len(docs)  else "",
            "link": filing_link(cik, acc, docs[i] if i < len(docs) else ""),
        }


# ----------------------------- Reddit ---------------------------------------
def fetch_reddit_day(sym, q):
    """Top posts of the day for a cashtag. Returns list of dicts, best-effort."""
    try:
        r = requests.get(
            "https://www.reddit.com/search.json",
            headers={"User-Agent": REDDIT_UA},
            params={"q": q, "sort": "top", "t": "day", "limit": 15, "type": "link"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[reddit] {sym}: HTTP {r.status_code}", file=sys.stderr)
            return []
        children = r.json().get("data", {}).get("children", [])
    except Exception as e:
        print(f"[reddit] {sym}: {e}", file=sys.stderr)
        return []
    posts = []
    for c in children:
        d = c.get("data", {})
        posts.append({
            "id":    d.get("id"),
            "score": d.get("score", 0),
            "title": html.unescape(d.get("title", ""))[:120],
            "sub":   d.get("subreddit", ""),
            "link":  "https://www.reddit.com" + d.get("permalink", ""),
        })
    posts.sort(key=lambda p: p["score"], reverse=True)
    return posts


# ----------------------------- intraday checks ------------------------------
def check_filings(tickers, cik_map, seen):
    lines, new_ids = [], []
    for t in tickers:
        if not t.get("edgar"):
            continue
        sym = t["s"].upper()
        cik = cik_map.get(sym)
        if not cik:
            continue
        for f in iter_filings(sym, cik):
            if not is_material(f["form"]):
                continue
            fid = f"{sym}:{f['acc']}"
            if fid in seen:
                continue
            new_ids.append(fid)
            lines.append(f"{sym} {f['form']} ({f['date']})\n{f['link']}")
    return lines, new_ids


def check_reddit(tickers, seen):
    lines, new_ids = [], []
    for t in tickers:
        sym = t["s"].upper()
        q = t.get("rq") or f"${sym}"
        for p in fetch_reddit_day(sym, q):
            if p["score"] < REDDIT_MIN_SCORE:
                continue
            pid = f"{sym}:{p['id']}"
            if pid in seen:
                continue
            new_ids.append(pid)
            lines.append(f"{sym} \u00b7 r/{p['sub']} ({p['score']}\u2191)\n{p['title']}\n{p['link']}")
        time.sleep(1.0)
    return lines, new_ids


# ----------------------------- daily summary --------------------------------
def build_summary(tickers, cik_map, local_now):
    cutoff = (local_now.date() - dt.timedelta(days=SUMMARY_LOOKBACK_DAYS)).isoformat()

    f_lines = []
    for t in tickers:
        if not t.get("edgar"):
            continue
        sym = t["s"].upper()
        cik = cik_map.get(sym)
        if not cik:
            continue
        for f in iter_filings(sym, cik):
            if is_material(f["form"]) and f["date"] and f["date"] >= cutoff:
                f_lines.append(f"{sym} {f['form']} ({f['date']})")
    f_lines = f_lines[:12]

    r_lines = []
    if REDDIT_ENABLED:
        for t in tickers:
            sym = t["s"].upper()
            q = t.get("rq") or f"${sym}"
            top = fetch_reddit_day(sym, q)
            if top and top[0]["score"] >= REDDIT_MIN_SCORE:
                p = top[0]
                r_lines.append(f"{sym} r/{p['sub']} ({p['score']}\u2191): {p['title'][:70]}")
            time.sleep(1.0)
        r_lines = r_lines[:6]

    if SUMMARY_SKIP_IF_EMPTY and not f_lines and not r_lines:
        return None

    day = local_now.strftime("%a %b %d")
    parts = [f"\U0001F4CB Daily watchlist \u2014 {day}"]
    parts.append("\nFILINGS (24h):\n" + ("\n".join(f_lines) if f_lines else "none"))
    if REDDIT_ENABLED:
        parts.append("\nREDDIT today:\n" + ("\n".join(r_lines) if r_lines else "quiet"))
    parts.append("\n\u2014 info only, verify before acting")
    return "\n".join(parts)


# ----------------------------- Twilio ---------------------------------------
def send_sms(body):
    sid   = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    frm   = os.environ["TWILIO_FROM"]
    to    = os.environ["ALERT_TO"]
    if len(body) > SMS_CAP:
        body = body[:SMS_CAP - 20] + "\n\u2026 (truncated)"
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, token),
        data={"From": frm, "To": to, "Body": body},
        timeout=30,
    )
    if r.status_code >= 300:
        print(f"[twilio] send failed {r.status_code}: {r.text}", file=sys.stderr)
        r.raise_for_status()
    print(f"[twilio] sent {len(body)} chars")


# ----------------------------- main -----------------------------------------
def main():
    wl = load_json(WATCHLIST_FILE, {}).get("tickers", [])
    if not wl:
        print("No tickers in watchlist.json", file=sys.stderr)
        sys.exit(1)

    state = load_json(STATE_FILE, {})
    first_run = not state.get("initialized")
    seen_f = set(state.get("seen_filings", []))
    seen_r = set(state.get("seen_reddit", []))

    try:
        cik_map = get_cik_map()
    except Exception as e:
        print(f"[edgar] cik map failed: {e}", file=sys.stderr)
        cik_map = {}

    # local time for the daily-summary window
    try:
        local_now = dt.datetime.now(ZoneInfo(SUMMARY_TZ))
    except Exception as e:
        print(f"[tz] {e}; falling back to UTC", file=sys.stderr)
        local_now = dt.datetime.now(dt.timezone.utc)
    today_str = local_now.strftime("%Y-%m-%d")

    # ---- intraday ----
    f_lines, f_new = check_filings(wl, cik_map, seen_f)
    r_lines, r_new = ([], [])
    if REDDIT_ENABLED:
        r_lines, r_new = check_reddit(wl, seen_r)
    seen_f.update(f_new)
    seen_r.update(r_new)

    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%b %d %H:%M UTC")

    if first_run:
        try:
            send_sms(f"Filings\u00b7Desk armed \u2705\nWatching {len(wl)} tickers. "
                     f"Intraday alerts + a daily {SUMMARY_HOUR}:00 recap are on.")
        except Exception as e:
            print(f"[twilio] arm msg failed: {e}", file=sys.stderr)
        state["last_summary_date"] = today_str   # don't also summarize on the arming run
        print(f"First run: baselined {len(f_new)} filings, {len(r_new)} posts.")
    else:
        blocks = []
        if f_lines:
            blocks.append("SEC FILINGS\n" + "\n\n".join(f_lines))
        if r_lines:
            blocks.append("REDDIT TRENDING (unverified)\n" + "\n\n".join(r_lines))
        if blocks:
            send_sms(f"\u26a1 Watchlist \u2014 {now_utc}\n\n" + "\n\n".join(blocks))
        else:
            print("No new intraday alerts this run.")

    # ---- daily 8am summary (once per local day, DST-safe) ----
    if (SUMMARY_ENABLED and not first_run
            and local_now.hour == SUMMARY_HOUR
            and state.get("last_summary_date") != today_str):
        summary = build_summary(wl, cik_map, local_now)
        if summary:
            try:
                send_sms(summary)
                state["last_summary_date"] = today_str
                print("Daily summary sent.")
            except Exception as e:
                print(f"[twilio] summary failed: {e}", file=sys.stderr)
        else:
            state["last_summary_date"] = today_str
            print("Daily summary empty; skipped.")

    # ---- persist ----
    state["initialized"]  = True
    state["seen_filings"] = list(seen_f)[-STATE_CAP:]
    state["seen_reddit"]  = list(seen_r)[-STATE_CAP:]
    state["last_run"]     = now_utc
    save_json(STATE_FILE, state)
    print("State saved.")


if __name__ == "__main__":
    main()
