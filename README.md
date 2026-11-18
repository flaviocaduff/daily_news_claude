# Daily Brief

Builds a one-page HTML news brief each day at 06:30 Europe/Zurich. It covers all 14 TLDR newsletters, Handelsblatt, Inside Paradeplatz, SRF, Swiss federal press releases, SCMP and BelTA, with one sentence per story from Claude and a Top 3 across all sources.

## Run it (manual, current setup)

Summaries come from **Claude Code in headless mode** (`claude -p`) on your Claude subscription. There is no API key and no per-token billing (`llm_backend: claude-cli` in `sources.yaml`).

```bash
uv run daily-brief --open     # ~2 min; writes site/index.html + site/archive/YYYY-MM-DD.html and opens it
```

Useful flags: `--no-llm` (use the source teasers), `--no-images`, `--only tldr-ai,handelsblatt`, `--notify`, `-v`.

Tests: `uv run pytest -q`

### Later: daily on the Mac (launchd, not installed)

`./scripts/install_launchd.sh` installs a job that runs at 07:00, or on wake/login if the Mac was asleep, once per day. macOS blocks background jobs from reading `~/Documents`, so it deploys a snapshot to `~/Library/Application Support/daily-brief/`. Re-run it after changes. Uninstall: `launchctl bootout gui/$(id -u)/local.daily-brief; rm ~/Library/LaunchAgents/local.daily-brief.plist`.

## Pipeline

1. **Fetch** (`fetchers.py`). For TLDR, take the newest issue dated today or yesterday (Zurich time). A day with no issue redirects with a 307 and is skipped. Sponsored, "Together With", form and job items are dropped. RSS items are filtered to the last `window_hours`.
2. **Deduplicate** (`dedup.py`). Match on the canonical URL (no `utm_*`, no `www`, no fragment) or a fuzzy headline match (rapidfuzz). The page notes which other sources also carried the story.
3. **Summarise** (`summarize.py`). Items go to `models.summarize`, through `claude -p` or the API depending on `llm_backend`, in batches of 25. Each comes back with a headline, one sentence and an importance score from 1 to 5, in `settings.language`. The best `max_items` per source are kept.
4. **Rank**. One call to `models.rank` picks the Top N and flags the same story across languages, for example Handelsblatt and TLDR.
5. **Thumbnails** (`images.py`). og:image or twitter:image is fetched concurrently. If it fails, the item has no image.
6. **Full text + key points** (`articles.py`). For every story on the page, the article is read from each source that carried it (up to 3) with trafilatura, and Claude (`models.article`) writes 3–6 key points combining them. Tapping a card opens these points on the page; the original articles are linked at the bottom. Paywalled or blocked pages (e.g. SCMP returns 403) fall back to the teaser and say so. Turn off with `fetch_articles: false`.
7. **Render** (`templates/brief.html.j2`). A single HTML file with inline CSS and no scripts beyond a tiny `onerror` that hides broken thumbnails. It works on mobile and supports light and dark mode.

## Configure

Everything lives in `sources.yaml`: language, models, reader profile, sources and page sections.

- **Sections:** sources with the same `section:` share one section on the page. For example, all 14 TLDR newsletters appear under "TLDR". Each section in `settings.sections` sets `max_items`, the number of cards not counting the Top 3. It can also set `max_per_source`, which keeps a mix of newsletters.
- **Images:** stories with a thumbnail win ties. YouTube links use the video thumbnail. Sites that block automated requests get a colored tile showing the site name.

To add a feed:

```yaml
  - id: nzz
    name: NZZ
    type: rss
    url: https://www.nzz.ch/recent.rss
    max_items: 5
```

For a site without a feed, use `type: html`. It collects article links from a list page, then reads each article's meta tags (`og:title`, `og:description`, `og:image`, `article:published_time`):

```yaml
  - id: belta
    name: BelTA
    type: html
    list_url: https://eng.belta.by/all_news/
    link_pattern: '^https://eng\.belta\.by/[a-z_]+/view/[^/]+-\d+-\d{4}/$'
    timezone: Europe/Minsk   # for published times without a zone
```

## Cloud (GitHub Actions + Pages + ntfy)

The workflow installs Claude Code and runs on your Claude subscription: run `claude setup-token` and save the token as the repo secret `CLAUDE_CODE_OAUTH_TOKEN`. The push notification lists the Top 3 headlines; tapping it opens the page.

Test without Claude, publishing or notifying: `gh workflow run daily-brief.yml -f dry_run=true`, then compare the per-source counts in the log with a local run.


`.github/workflows/daily-brief.yml` runs on GitHub's own cron at 06:30 Zurich (two UTC lines for summer/winter; a gate skips the wrong one and anything already published). Backup: a small LaunchAgent on the Mac (`./scripts/install_trigger.sh`) calls `gh workflow run` at 07:15, or right after wake/login, only if today's brief is not published yet and no build is running. Log: `~/Library/Logs/daily-brief-trigger.log`. The workflow builds the site, pushes it to the `gh-pages` branch (keeping the archive) and then sends the notification.

Setup:
1. Push this folder to a GitHub repo. Pages on a private repo needs a paid plan.
2. Settings → Secrets → Actions: add `CLAUDE_CODE_OAUTH_TOKEN` and one notification channel (below).
3. Run the workflow once by hand (Actions → Daily Brief → Run workflow). Then go to Settings → Pages → Source: *Deploy from branch* → `gh-pages` / root.
4. The stable URL is `https://<user>.github.io/<repo>/`. You can override it with the repo variable `SITE_URL`.

Notes: GitHub cron can start 5–20 minutes late. On public repos, GitHub pauses scheduled workflows after 60 days without commits to the default branch.

## Notification channels

Each channel is used only when its secrets are set:

| Channel | Secrets | Notes |
|---|---|---|
| **ntfy.sh** (recommended) | `NTFY_TOPIC` | No account needed. Install the ntfy app and subscribe to a long random topic name. Tapping the push opens the brief. |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Create a bot with @BotFather and message it once to get the chat id. |
| Email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` (`EMAIL_FROM`) | For example, a Gmail app password with smtp.gmail.com:587. |
