# LSE RNS Digest — full setup guide

This document takes you from nothing to a fully running digest: a web page
that lists every LSE RNS announcement from 07:00 UK onwards, with an AI
summary and comment for each, refreshing every 5 minutes through the trading
day. Total setup time is roughly 30 minutes. Expect API running costs of
about £1-2 per trading day.

## How the system fits together

Five parts, all free except the AI summaries:

1. **This repo** holds the code and the published digests.
2. **GitHub Actions** (the workflow in `.github/workflows/digest.yml`) runs
   the pipeline: pulls the announcement list from the LSE's internal API,
   fetches each announcement's full text from Investegate, follows document
   links inside them (PDFs included), asks Claude for a summary, a brief,
   a retail-investor comment and a sentiment, then commits the results to
   `digests/`.
3. **GitHub Pages** serves `index.html` as the public web page, which reads
   `digests/latest.csv`.
4. **The Anthropic API** does the summarising (the only paid part).
5. **cron-job.org** pings GitHub every 5 minutes to trigger runs, because
   GitHub's own scheduler throttles frequent schedules (in practice it fires
   every 1-2 hours, not every 5 minutes). The GitHub schedule is kept as a
   backup.

## Part 1 — repository

1. Create a new GitHub repository (public — Pages is free only on public
   repos, and the digest contains only public RNS information).
2. Upload the contents of this repo to it, keeping the folder structure.
   The `.github/workflows/` path matters.
3. In the repo's **Actions** tab, enable workflows if prompted.
4. If your repo name differs from `LSE-DIGESt`, update the two GitHub links
   in `index.html` (the "Run now" link) and the URL you use in Part 4.

## Part 2 — Anthropic API key (the summaries)

1. Sign in at https://console.anthropic.com (any email; this is separate
   from a claude.ai subscription — consumer plans do not include API use).
2. **Billing → Buy credits** — the $5 minimum funds the first few days.
3. **API Keys → Create key**, name it, copy it (shown once).
4. In the repo: **Settings → Secrets and variables → Actions → New
   repository secret**. Name: `ANTHROPIC_API_KEY` (exactly). Value: the key.

Without this secret everything still runs, but headline-only: no summaries,
briefs, comments or sentiment.

Model choice: the workflow uses Claude Haiku by default (cheap, fast). For
sharper comments at ~3x the cost, add an env line `SUMMARY_MODEL:
claude-sonnet-4-6` next to `ANTHROPIC_API_KEY` in the workflow.

## Part 3 — GitHub Pages (the web page)

The page is a single static file, `index.html`, served straight from the
repo by GitHub Pages. To switch it on:

1. The repo must be **public** (Settings → General → Danger Zone → Change
   visibility) — Pages is paid-only on private repos.
2. Go to **Settings → Pages** (left sidebar, near the bottom).
3. Under **Build and deployment**:
   - Source: **Deploy from a branch**
   - Branch: **main**, folder: **/ (root)**
   - **Save**
4. Wait 1-2 minutes. The green box at the top of the same settings page
   shows the live URL — always
   `https://<username>.github.io/<repo-name>/` — with a "Visit site"
   button. If the box hasn't appeared, refresh the settings page.
5. Every later push to `main` redeploys automatically within a minute or
   two; there is nothing to re-do. Deployment history is visible under the
   repo's Actions tab as "pages build and deployment" runs.
6. Optional but recommended: on the repo's front page, click the gear icon
   next to **About** (right-hand column), tick **"Use your GitHub Pages
   website"**, save — the live link then shows permanently at the top of
   the repo.

If the page loads but says "No digest published yet", the site works — it
just has no data. Run the workflow once (Actions tab → Run workflow) and
reload.

### How the page works and what's customisable

`index.html` is self-contained (no build step, no dependencies). On load it
fetches `digests/latest.csv` for the data and `books.csv` for the filter
buttons, then re-checks for new data every 60 seconds.

Things people commonly want to change, all near the top of `index.html`:

- **Title and attribution** — the `<h1>` line and the footer paragraph.
- **Auto-refresh interval** — the `setInterval(..., 60000)` line
  (milliseconds).
- **Copy line format** — the block inside the `copySel` click handler
  builds `- Company - Headline: one-liner. {TICK LN Equity}`; edit the
  template string there.
- **Bloomberg pill** — the `bbg()` function; it uppercases the ticker,
  turns a trailing `.` into `/` (LSE `TW.` → Bloomberg `TW/`), and wraps in
  `{... LN Equity}`.
- **Sentiment shading colours** — the `td.cm.pos` / `td.cm.neg` CSS rules.

Edits can be made directly on github.com (pencil icon on the file); each
commit redeploys the page automatically.

### Page controls, for whoever you send the link to

- **Book buttons** (MM2 / MM3 / MM4 / View all) — show only names in that
  book, driven by `books.csv`.
- **Categories ▾** — tick-box panel choosing which announcement categories
  are visible, with All / None shortcuts. New categories appearing during
  the day default to visible.
- **Search** — matches company, ticker, headline, summary and comment.
- **Checkboxes + Copy selected** — tick rows (header checkbox = all shown)
  and copy Bloomberg-ready lines; selections survive filtering.
- **Refresh** — fetch the newest published data immediately (the page also
  does this itself every minute).
- **Run now ↗** — opens the workflow on GitHub to force a fresh pull;
  needs repo access.
- The control bar stays pinned to the top while scrolling.

## Part 4 — reliable 5-minute scheduling (cron-job.org)

GitHub's cron is best-effort and heavily throttled for frequent schedules.
An external pinger fixes this.

First, a trigger token:

1. https://github.com/settings/personal-access-tokens/new
2. Repository access: only this repo. Permissions: **Actions — Read and
   write**. Nothing else. Set a long expiry and diarise it — when this token
   expires the pinger silently stops and the digest degrades to GitHub's
   slow backup schedule.
3. Generate and copy the token.

Then the pinger:

1. Sign up free at https://cron-job.org, confirm email.
2. Account **Settings → Timezone: Europe/London** (so the schedule follows
   UK clock changes).
3. **Create cronjob**:
   - URL: `https://api.github.com/repos/<username>/<repo>/actions/workflows/digest.yml/dispatches`
   - Schedule (crontab form): `*/5 7-18 * * 1-5`
   - **Advanced** tab: Request method **POST**; Headers:
     `Authorization` = `Bearer <your token>` and
     `Accept` = `application/vnd.github+json`; Request body: `{"ref":"main"}`.
   - Alternatively use "Import from curl" with:
     `curl -X POST "<the URL above>" -H "Authorization: Bearer <token>" -H "Accept: application/vnd.github+json" -d '{"ref":"main"}'`
4. **Test run** — status **204** means success (GitHub sends no body). A
   workflow run appears in the Actions tab within seconds.
5. Save. Runs now start every 5 minutes, 07:00-18:55 UK, weekdays.

Status codes if the test is not 204: 401 = Authorization header wrong
(check the `Bearer ` prefix); 404 = token not scoped to the repo or missing
the Actions permission; 422 = body is not exactly `{"ref":"main"}`.

## Part 5 — the book filters

The page shows filter buttons (e.g. MM2 / MM3 / MM4 / View all) driven by
`books.csv` in the repo root:

```
book,ticker
MM2,ACP
MM2,AEX
MM3,AAU
```

One row per company per book, using LSE tickers (drop any `.L` suffix; a
trailing dot like `TW.` is fine). Edit the file on github.com to add or
remove names; the page picks changes up on its next refresh. Delete the file
to hide the buttons entirely.

## Daily operation

- First run of the day starts at 07:05 UK and takes 10-15 minutes (it
  summarises the whole 07:00 burst). The page typically fills by ~07:20.
- Later runs take 1-3 minutes; summaries are cached per announcement in
  `digests/.cache/`, so each announcement is paid for exactly once.
- The page re-checks for new data every 60 seconds on its own; the Refresh
  button forces a check; "Run now" links to the manual trigger for anyone
  with repo access.
- Each day's digest is archived as `digests/YYYY-MM-DD-morning.md` and
  `.csv` — a growing dataset of announcements, summaries and sentiment.

## Using the page

- Tick rows (or the header checkbox for all shown) and **Copy selected** to
  get Bloomberg-chat-ready lines:
  `- Company - Headline: one-liner. {TICK LN Equity}` — the `{...}` resolves
  to a live security pill when pasted into a Bloomberg chat.
- Search, category and book filters combine; copying respects what's shown.
- Comment cells are shaded light green/red by the model's sentiment call.

## Troubleshooting

- **Page stale, no recent commits:** check cron-job.org's job history (all
  204s?) and the token expiry; then the repo's Actions tab for red runs.
- **Summaries missing on some rows:** Investegate republishes with a small
  lag; those items are retried on every later run and usually backfill
  within the hour.
- **`API key MISSING` in the run log:** the `ANTHROPIC_API_KEY` secret is
  absent or misnamed.
- **Summaries all failing:** check Anthropic credit balance in the console.
- **Everything empty / zero announcements:** the LSE's internal API or
  Investegate's page structure has changed. This is the system's main
  external dependency and needs a code fix, not a settings fix.

## Costs

- GitHub: free (public repo — unlimited Actions minutes and Pages).
- cron-job.org: free.
- Anthropic API: ~£1-2 per trading day at typical volumes (a few hundred
  announcements), spiking to ~£4 on heavy results mornings. Each
  announcement is summarised once ever, so run frequency does not affect
  cost. A live usage dashboard is in the Anthropic console.
