# Job Alert Bot — Setup Guide

Monitors company career pages directly and texts you on Telegram the moment
a matching role is posted — faster and less crowded than LinkedIn.

## 1. Get a Telegram bot (2 minutes)

1. Open Telegram, search **@BotFather**, send `/newbot`, follow the prompts.
2. Copy the token it gives you (looks like `123456789:AAExxxxxxxxxxxxxxxxxxxx`).
3. Send your new bot any message (e.g. "hi") so it can message you back.
4. Visit this URL in your browser (replace the token):
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
5. Find `"chat":{"id":123456789,...}` in the response — that number is your chat ID.

## 2. Run it locally first (recommended, to verify it works)

```bash
pip install requests
export TELEGRAM_BOT_TOKEN="your-token-here"
export TELEGRAM_CHAT_ID="your-chat-id-here"
python3 monitor.py
```

First run just records existing jobs (no alerts — avoids a flood).
Run it again — any *new* posting since the first run will trigger a Telegram message.

## 3. Automate it for free with GitHub Actions

1. Create a new **GitHub repo** (public is easiest — free unlimited Actions minutes;
   private repos get 2,000 free min/month which still comfortably covers hourly runs).
2. Push this folder to that repo (includes `.github/workflows/monitor.yml`).
3. In the repo: **Settings → Secrets and variables → Actions → New repository secret**
   - Add `TELEGRAM_BOT_TOKEN`
   - Add `TELEGRAM_CHAT_ID`
4. Done — it now runs every hour automatically, forever, for free.
   You can also trigger it manually from the **Actions** tab any time.

## 4. Customize your target list

Open `monitor.py` and edit:
- `KEYWORDS` — job title keywords to match (currently tuned to your background:
  java, backend, spring boot, senior software developer, etc.)
- `COMPANIES` — add/remove companies. Three types:
  - `greenhouse` — needs the board slug from `boards.greenhouse.io/<slug>`
  - `lever` — needs the board slug from `jobs.lever.co/<slug>`
  - `custom` — needs any careers-search URL; alerts on ANY page change
    (works for every company, including Workday-based sites like most
    big banks, even without a clean public API)

**Important:** the `board`/`url` values I pre-filled are my best guess based
on how each company's ATS is typically structured — verify each one by
visiting the URL yourself before relying on it, since companies migrate
ATS providers periodically.

## 5. A note on strategy, not just speed

This system solves the "I found out too late" problem. It does not solve
"the ATS auto-rejects sponsorship-required candidates" or "no one refers me."
Pair this with:
- Tailoring your resume/keywords per posting
- Reaching out to a current employee at the company (even a cold, polite
  LinkedIn message asking for a referral converts far better than a blind
  application)
