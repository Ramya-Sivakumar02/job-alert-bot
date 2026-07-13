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

# Only alert if the job title matches at least one of these (case-insensitive).
# Widened to catch more real postings — many companies phrase the same role
# differently (e.g. "Backend Developer" vs "Software Engineer, Backend").
KEYWORDS = [
    "java", "software engineer", "software developer", "backend",
    "back-end", "back end", "full stack", "fullstack", "full-stack",
    "senior software developer", "senior developer", "spring boot",
    "sde", "platform engineer", "cloud engineer", "distributed systems",
    "microservices", "api engineer", "systems engineer", "applications engineer",
]

# Job titles containing these are SKIPPED even if keywords match — these
# signal a seniority level outside 8-12 years (too junior or too senior).
TITLE_EXCLUDE = [
    # too junior (0-4 yrs)
    "intern", "internship", "new grad", "new graduate", "entry level",
    "entry-level", "university grad", "associate software engineer",
    "software engineer i,", "software engineer i -", "swe i ",
    "early career", "apprentice", "graduate program", "co-op",
    # too senior (12+ yrs / management track)
    "staff engineer", "staff software", "principal", "distinguished",
    "director", "vp ", "vice president", "head of", "chief ", "fellow",
    "engineering manager", "manager,", "manager -",
]

# Regex patterns to pull an explicit years-of-experience requirement out of
# the job description body (Greenhouse gives us this via content=true).
# If the posting states a minimum ABOVE this or a maximum BELOW this,
# it's skipped. If no explicit number is found, the title-based filter
# above is the only gate (keeps recall reasonable since many postings
# don't state a number at all).
MIN_ACCEPTABLE_YEARS = 8
MAX_ACCEPTABLE_YEARS = 12

YEARS_PATTERN = re.compile(
    r"(\d{1,2})\+?\s*(?:-|to|–)?\s*(\d{0,2})?\+?\s*years?\s*(?:of\s+)?(?:professional\s+)?experience",
    re.IGNORECASE,
)

# US states + DC, for matching Greenhouse's "City, ST" location format.
US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC",
}
# Canadian provinces — Greenhouse formats these the same way ("City, ON"),
# so without this explicit check they'd false-positive as US matches.
NON_US_PROVINCE_CODES = {"ON","QC","BC","AB","MB","SK","NS","NB","NL","PE","YT","NT","NU"}

NON_US_KEYWORDS = [
    "india", "canada", "uk", "united kingdom", "ireland", "germany",
    "poland", "philippines", "singapore", "australia", "mexico", "brazil",
    "argentina", "japan", "china", "vietnam", "ukraine", "romania", "spain",
    "france", "netherlands", "bangalore", "hyderabad", "pune", "chennai",
    "mumbai", "delhi", "toronto", "vancouver", "montreal", "london",
    "dublin", "berlin", "remote - emea", "remote - apac", "remote - canada",
    "remote, canada", "emea", "apac",
]
US_KEYWORDS = [
    "united states", "usa", "u.s.", "remote - us", "remote - usa",
    "remote (us)", "remote, us", "remote, usa", "us remote",
]

# Alerts for these locations get a priority flag and are sent first in
# each batch, per your preference.
PRIORITY_LOCATION_KEYWORDS = ["remote", "austin"]

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
# "custom" type, which watches the page for changes.
#
# IMPORTANT LIMITATION on "custom" sources: they can't be filtered by
# location or experience level (we only see "the page changed", not
# individual job details), and they're inherently noisier for dedup since
# unrelated page changes (ads, counters) can look like "new" content.
# Prefer "greenhouse"/"lever" entries wherever possible — those give exact
# job data and reliable per-job dedup. Treat "custom" alerts as "go check
# manually", not a guaranteed real posting.

COMPANIES = [
    # --- CONFIRMED via live job listings (verified, not guessed) ---
    {"name": "MongoDB",       "ats": "greenhouse", "board": "mongodb"},
    {"name": "Databricks",    "ats": "greenhouse", "board": "databricks"},
    {"name": "Robinhood",     "ats": "greenhouse", "board": "robinhood"},
    {"name": "Coinbase",      "ats": "greenhouse", "board": "coinbase"},
    {"name": "DoorDash",      "ats": "greenhouse", "board": "doordashusa"},
    {"name": "Twilio",        "ats": "greenhouse", "board": "twilio"},
    {"name": "Instacart",     "ats": "greenhouse", "board": "instacart"},

    # --- Confirmed NOT on Greenhouse — using their real career-search pages instead ---
    {"name": "Plaid",         "ats": "custom",     "url": "https://plaid.com/careers/openings/"},
    {"name": "Datadog",       "ats": "custom",     "url": "https://careers.datadoghq.com/all-jobs/"},
    {"name": "Snowflake",     "ats": "custom",     "url": "https://careers.snowflake.com/us/en/search-results?keywords=java"},

    # --- Tier 1: fintech/payments (board slugs below are best-effort — verify
    # by visiting boards.greenhouse.io/<slug> yourself before fully trusting) ---
    {"name": "Stripe",        "ats": "greenhouse", "board": "stripe"},
    {"name": "PayPal",        "ats": "custom",     "url": "https://careers.pypl.com/search-jobs/java"},

    # --- Workday-hosted companies (CONFIRMED tenants — verified via live
    # listings, my earlier guesses for these two were actually wrong and
    # have been corrected here) ---
    {"name": "Fiserv", "ats": "workday", "tenant": "fiserv", "datacenter": "wd5", "site": "EXT"},
    {"name": "FIS",    "ats": "workday", "tenant": "fis",    "datacenter": "wd5", "site": "SearchJobs"},

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
    return [
        {
            "id": str(j["id"]),
            "title": j["title"],
            "url": j["absolute_url"],
            "location": (j.get("location") or {}).get("name", ""),
            "content": j.get("content", ""),
        }
        for j in jobs
    ]


def fetch_lever(board):
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    jobs = r.json()
    return [
        {
            "id": j["id"],
            "title": j["text"],
            "url": j["hostedUrl"],
            "location": (j.get("categories") or {}).get("location", ""),
            "content": j.get("descriptionPlain", "") or j.get("description", ""),
        }
        for j in jobs
    ]


def fetch_workday(tenant, datacenter, site):
    """Workday's CXS API (used by most large enterprises: banks, healthcare,
    fintechs). Unlike a generic 'custom' page-hash, this returns real
    structured job data — title, location, ID — so it supports the same
    location/experience filtering as Greenhouse/Lever.

    tenant/datacenter/site come from the company's careers URL, e.g.:
    https://fiserv.wd5.myworkdayjobs.com/en-US/EXT
                ^tenant  ^datacenter        ^site
    """
    url = f"https://{tenant}.{datacenter}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    r = requests.post(url, json=body, timeout=20, headers={"Content-Type": "application/json"})
    r.raise_for_status()
    postings = r.json().get("jobPostings", [])
    base = f"https://{tenant}.{datacenter}.myworkdayjobs.com/{site}"
    return [
        {
            "id": p.get("bulletFields", [p.get("externalPath", "")])[0] or p.get("externalPath", ""),
            "title": p.get("title", ""),
            "url": base + p.get("externalPath", ""),
            "location": p.get("locationsText", ""),
            "content": "",  # Workday's list endpoint doesn't include full description text
        }
        for p in postings
    ]


def fetch_custom(url):
    """Fallback: hash the page content. Any change -> flagged for manual check.
    Cannot be filtered by location/experience — no per-job data available."""
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    text = re.sub(r"\s+", " ", r.text)
    import hashlib
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return [{"id": digest, "title": f"Page changed — check manually: {url}", "url": url,
             "location": "", "content": ""}]


def matches_keywords(title):
    t = title.lower()
    if any(bad in t for bad in TITLE_EXCLUDE):
        return False
    return any(k in t for k in KEYWORDS)


def is_us_location(location):
    """USA only. Unknown/blank locations are excluded to stay strict."""
    if not location:
        return False
    loc_lower = location.lower()

    # Explicit "City, XX" pattern (Greenhouse/Lever's common format)
    m = re.search(r",\s*([A-Za-z]{2})\b", location)
    if m:
        code = m.group(1).upper()
        if code in NON_US_PROVINCE_CODES:
            return False
        if code in US_STATES:
            return True

    if any(k in loc_lower for k in NON_US_KEYWORDS):
        return False
    if any(k in loc_lower for k in US_KEYWORDS):
        return True

    return False  # unknown format -> exclude rather than risk a non-US match


def matches_experience(content):
    """Check for an explicit years-of-experience requirement in the job
    description. If found, it must overlap the 8-12 year band. If no
    explicit number is found, this check passes (title filter is the gate)."""
    if not content:
        return True
    text = re.sub("<[^<]+?>", " ", content)  # strip HTML tags
    matches = YEARS_PATTERN.findall(text)
    if not matches:
        return True

    for low, high in matches:
        low = int(low)
        high = int(high) if high else low
        # Reject only if the stated range clearly falls outside 8-12
        # (e.g. "3-5 years" or "15+ years"); allow anything that overlaps.
        if high < MIN_ACCEPTABLE_YEARS or low > MAX_ACCEPTABLE_YEARS:
            return False
    return True


def passes_filters(company, job):
    if company["ats"] == "custom":
        return True  # no structured data available to filter on
    if not matches_keywords(job["title"]):
        return False
    if not is_us_location(job["location"]):
        return False
    if not matches_experience(job["content"]):
        return False
    return True


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
            elif company["ats"] == "workday":
                jobs = fetch_workday(company["tenant"], company["datacenter"], company["site"])
            elif company["ats"] == "custom":
                jobs = fetch_custom(company["url"])
            else:
                continue
        except Exception as e:
            print(f"[ERROR] {name}: {e}")
            continue

        record = seen.get(name, {"ids": [], "pending": {}})
        if isinstance(record, list):  # backward-compat with pre-update seen_jobs.json
            record = {"ids": record, "pending": {}}
        seen_ids = set(record.get("ids", []))
        pending = record.get("pending", {})
        new_pending = {}

        for job in jobs:
            if job["id"] in seen_ids:
                continue

            if company["ats"] == "custom":
                # Debounce: a hash must show up on two consecutive runs
                # before we trust it as a real change (filters out one-off
                # dynamic page noise like ad slots, counters, timestamps).
                if job["id"] in pending:
                    seen_ids.add(job["id"])
                    if not first_run:
                        new_alerts.append((False, f"🆕 {name}: {job['title']}\n{job['url']}"))
                else:
                    new_pending[job["id"]] = True
                continue

            seen_ids.add(job["id"])
            if passes_filters(company, job):
                if not first_run:
                    loc = job.get("location", "")
                    is_priority = any(k in loc.lower() for k in PRIORITY_LOCATION_KEYWORDS)
                    tag = "⭐ " if is_priority else ""
                    loc_suffix = f" [{loc}]" if loc else ""
                    msg = f"{tag}🆕 {name}: {job['title']}{loc_suffix}\n{job['url']}"
                    new_alerts.append((is_priority, msg))

        seen[name] = {"ids": list(seen_ids), "pending": new_pending}

    save_seen(seen)

    if first_run:
        total = sum(len(v.get("ids", [])) for v in seen.values())
        print(f"Seeded {total} existing jobs. "
              f"No alerts sent this run — future runs will alert on NEW postings only.")
        return

    if new_alerts:
        new_alerts.sort(key=lambda pair: not pair[0])  # priority (remote/Austin) first
        for is_priority, alert in new_alerts:
            send_telegram(alert)
            time.sleep(1)
        print(f"Sent {len(new_alerts)} alert(s).")
    else:
        print("No new matches this run.")


if __name__ == "__main__":
    main()
