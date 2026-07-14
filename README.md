# LSE News Digest

Pulls every announcement from the LSE news explorer once a day and commits a
one-line-per-announcement digest to this repo. Runs entirely on GitHub's
servers. Nothing runs on your machine.

## How it works

- `lse_news_digest.py` calls the LSE site's internal component API, pages
  through all results, and verifies the collected count against the server's
  own `totalElements` figure so nothing is missed.
- The workflow in `.github/workflows/digest.yml` runs every weekday at 07:20
  UK time (handles GMT/BST automatically) and commits everything timestamped
  07:00 onwards to `digests/`. The 7am slot is when RNS releases its main
  burst of results and trading statements.
- `digests/latest.md` always holds the most recent morning.
- `digests/YYYY-MM-DD-morning.md` and `.csv` build up a permanent archive,
  one file per day, which you can later load into pandas for analysis.

## Setup (one time, ~2 minutes)

1. Create a new GitHub repo (private is fine).
2. Upload the contents of this folder to it, keeping the folder structure
   (the `.github/workflows/` path matters).
3. Go to the repo's Actions tab and enable workflows if prompted.

That's it. To test immediately without waiting for the schedule: Actions tab
-> "Daily LSE digest" -> "Run workflow".

## Reading the digest from anywhere

Open `digests/latest.md` on github.com from your phone or any browser. Each
line is: time | company | category | headline.

## Notes

- Want it later in the morning (say 08:00 to catch stragglers)? Change the
  two cron minutes/hours and the guard hour in digest.yml.
- A manual run from the Actions tab works any time of day and captures
  everything from 07:00 up to that moment.
- GitHub disables schedules on repos with no activity for 60 days; any commit
  (including the bot's own) resets that clock, so in practice it keeps itself
  alive on weekdays.
- The script pulls "today" as the explorer defines it. If a run ever fails
  (site change, outage), trigger it manually the same day or the announcements
  roll out of the default view.
