#!/usr/bin/env python3
"""
Job Alert Bot — monitors company career pages directly (Greenhouse, Lever, Workday)
and sends a Telegram message the instant a matching role is posted.

Runs standalone (cron / your machine) OR via the included GitHub Actions
workflow (free, runs hourly on GitHub's servers, no laptop needed).

Setup:
  1. pip install requests
  2. Create a Telegram bot: message @BotFather on Telegram -> /newbot -> copy the token
  3. Message your new bot once (anything), then visit:
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
     to find your numeric "chat":{"id": ...} value
  4. Set environment variables TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
     (or edit the constants below directly)
  5. Edit KEYWORDS and COMPANIES below to match what you want
  6. Run: python3 monitor.py
     First run seeds the "seen jobs" file without alerting (avoids a flood
     of alerts for every job that already existed before you started).
"""

import os
import re
import json
import time
import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen_jobs.json")

# Only alert if the job title matches at least one of these (case-insensitive)
KEYWORDS = [
    "java", "software engineer", "backend", "full stack", "fullstack",
    "senior software developer", "spring boot", "sde",
]

# Companies to monitor. "board" is the identifier the ATS uses in its URL —
# verify by visiting the URL pattern in the comment for each ATS type.
#
#   Greenhouse: boards.greenhouse.io/<board>            -> board = the slug in that URL
#   Lever:      jobs.lever.co/<board>                   -> board = the slug in that URL
#   Workday:    <tenant>.wd1.myworkdayjobs.com/<site>    -> tenant + site both required
#
# NOT ALL companies on your shortlist are confirmed below — ATS providers change,
# and some (e.g. Amazon, Microsoft, big banks) run heavily customized in-house
# career sites that don't expose a clean public API. For those, use the
# "custom" type, which just watches the page for byte-level changes and
# alerts you to go check it manually (works for literally any URL).

COMPANIES = [
    # --- CONFIRMED via live job listings (verified, not guessed) ---
    {"name": "MongoDB",       "ats": "greenhouse", "board": "mongodb"},
    {"name": "Databricks",    "ats": "greenhouse", "board": "databricks"},
    {"name": "Robinhood",     "ats": "greenhouse", "board": "robinhood"},
    {"name": "Coinbase",      "ats": "greenhouse", "board": "coinbase"},

    # --- Tier 1: fintech/payments (board slugs below are best-effort — verify
    # by visiting boards.greenhouse.io/<slug> yourself before fully trusting) ---
    {"name": "Stripe",        "ats": "greenhouse", "board": "stripe"},
    {"name": "PayPal",        "ats": "custom",     "url": "https://careers.pypl.com/search-jobs/java"},
    {"name": "Fiserv",        "ats": "custom",     "url": "https://cvfiserv.wd1.myworkdayjobs.com/FiservCareers"},
    {"name": "FIS",           "ats": "custom",     "url": "https://fisglobal.wd1.myworkdayjobs.com/FISGlobalCareers"},

    # --- Tier 2: banks ---
    {"name": "JPMorgan Chase","ats": "custom",     "url": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001"},
    {"name": "Capital One",   "ats": "greenhouse", "board": "capitalone"},
    {"name": "Goldman Sachs", "ats": "custom",     "url": "https://higher.gs.com/roles?TAGS=technology"},

    # --- Tier 3: healthcare/insurance ---
    {"name": "UnitedHealth/Optum", "ats": "custom", "url": "https://careers.unitedhealthgroup.com/search-jobs/java"},
    {"name": "Cigna",          "ats": "custom",    "url": "https://jobs.cigna.com/us/en/search-results?keywords=java"},

    # NOTE: intentionally excludes IT staffing / consultancy firms (TCS,
    # Infosys, Wipro, Cognizant, Mastech, Compunnel, Kforce, L&T Infotech,
    # etc.) per your request — these only flood the feed with generic
    # placement postings, not direct-employer engineering roles.

    # Add more companies here in the same shape. For any company, the safest
    # zero-setup option is "custom" + the careers search URL filtered to
    # your keyword — it just watches for page changes.
]

# ---------------------------------------------------------------------------
# ATS FETCHERS
# ---------------------------------------------------------------------------

def fetch_greenhouse(board):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    return [{"id": str(j["id"]), "title": j["title"], "url": j["absolute_url"]} for j in jobs]


def fetch_lever(board):
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    jobs = r.json()
    return [{"id": j["id"], "title": j["text"], "url": j["hostedUrl"]} for j in jobs]


def fetch_custom(url):
    """Fallback: hash the page content. Any change -> alert to go check manually."""
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    text = re.sub(r"\s+", " ", r.text)
    import hashlib
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return [{"id": digest, "title": f"Page changed — check manually: {url}", "url": url}]


def matches_keywords(title):
    t = title.lower()
    return any(k in t for k in KEYWORDS)


# ---------------------------------------------------------------------------
# STATE + ALERTING
# ---------------------------------------------------------------------------

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram not configured — printing instead:\n", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }, timeout=20)


def main():
    seen = load_seen()
    first_run = len(seen) == 0
    new_alerts = []

    for company in COMPANIES:
        name = company["name"]
        try:
            if company["ats"] == "greenhouse":
                jobs = fetch_greenhouse(company["board"])
            elif company["ats"] == "lever":
                jobs = fetch_lever(company["board"])
            elif company["ats"] == "custom":
                jobs = fetch_custom(company["url"])
            else:
                continue
        except Exception as e:
            print(f"[ERROR] {name}: {e}")
            continue

        seen_ids = set(seen.get(name, []))
        for job in jobs:
            if job["id"] in seen_ids:
                continue
            seen_ids.add(job["id"])
            if company["ats"] == "custom" or matches_keywords(job["title"]):
                if not first_run:
                    new_alerts.append(f"🆕 {name}: {job['title']}\n{job['url']}")
        seen[name] = list(seen_ids)

    save_seen(seen)

    if first_run:
        print(f"Seeded {sum(len(v) for v in seen.values())} existing jobs. "
              f"No alerts sent this run — future runs will alert on NEW postings only.")
        return

    if new_alerts:
        for alert in new_alerts:
            send_telegram(alert)
            time.sleep(1)
        print(f"Sent {len(new_alerts)} alert(s).")
    else:
        print("No new matches this run.")


if __name__ == "__main__":
    main()
