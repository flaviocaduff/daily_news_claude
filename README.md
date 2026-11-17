# Daily Brief

Builds a one-page HTML news brief each day at 07:00 Europe/Zurich. It covers all 14 TLDR newsletters, Handelsblatt and Inside Paradeplatz, with one sentence per story from Claude and a Top 3 across all sources.

## Run locally (current setup)

Summaries come from **Claude Code in headless mode** (`claude -p`) on your Claude subscription. There is no API key and no per-token billing (`llm_backend: claude-cli` in `sources.yaml`).

```bash
uv run daily-brief            # writes site/index.html + site/archive/YYYY-MM-DD.html
open site/index.html
```

Useful flags: `--no-llm` (use the source teasers), `--no-images`, `--only tldr-ai,handelsblatt`, `--notify`, `-v`.

Tests: `uv run pytest -q`

### Daily on the Mac (launchd)

```bash
./scripts/install_launchd.sh
```

- The job runs at 07:00. If the Mac is asleep or off at 07:00, it runs as soon as you wake it or log in. It also re-checks every 30 minutes.
- It builds at most once per day and never before 07:00 Zurich time. It then opens the page and shows a notification.
- macOS doesn't let background jobs read `~/Documents`, so the installer deploys a snapshot:
  - code: a uv tool
  - `sources.yaml` and the runner: `~/Library/Application Support/daily-brief/`
  - pages: `~/Library/Application Support/daily-brief/site/`
- **Re-run the installer after changing code or `sources.yaml`.**
- Log: `~/Library/Logs/daily-brief.log`. Force a rebuild: `~/Library/Application\ Support/daily-brief/run_daily.sh --force`.
- Uninstall: `launchctl bootout gui/$(id -u)/local.daily-brief; rm ~/Library/LaunchAgents/local.daily-brief.plist`

## Pipeline

1. **Fetch** (`fetchers.py`). For TLDR, take the newest issue dated today or yesterday (Zurich time). A day with no issue redirects with a 307 and is skipped. Sponsored, "Together With", form and job items are dropped. RSS items are filtered to the last `window_hours`.
2. **Deduplicate** (`dedup.py`). Match on the canonical URL (no `utm_*`, no `www`, no fragment) or a fuzzy headline match (rapidfuzz). The page notes which other sources also carried the story.
3. **Summarise** (`summarize.py`). Items go to `models.summarize`, through `claude -p` or the API depending on `llm_backend`, in batches of 25. Each comes back with a headline, one sentence and an importance score from 1 to 5, in `settings.language`. The best `max_items` per source are kept.
4. **Rank**. One call to `models.rank` picks the Top N and flags the same story across languages, for example Handelsblatt and TLDR.
5. **Thumbnails** (`images.py`). og:image or twitter:image is fetched concurrently. If it fails, the item has no image.
6. **Render** (`templates/brief.html.j2`). A single HTML file with inline CSS and no scripts beyond a tiny `onerror` that hides broken thumbnails. It works on mobile and supports light and dark mode.

## Configure

Everything lives in `sources.yaml`: language, models, items per source, reader profile and sources. To add a feed:

```yaml
  - id: nzz
    name: NZZ
    type: rss
    url: https://www.nzz.ch/recent.rss
    max_items: 5
```

## Cloud (GitHub Actions + Pages): currently disabled

The workflow is disabled in the repo (`gh workflow enable daily-brief.yml` to turn it back on). To run it in the cloud, either set `llm_backend: api` and add an `ANTHROPIC_API_KEY` secret (billed per token), or install Claude Code in the job with a subscription token from `claude setup-token`.


`.github/workflows/daily-brief.yml` runs at 05:00 and 06:00 UTC. A gate step lets exactly one of the two runs through, whichever matches 07:00 in Zurich (CET or CEST). The workflow builds the site, pushes it to the `gh-pages` branch (keeping the archive) and then sends the notification.

Setup:
1. Push this folder to a GitHub repo. Pages on a private repo needs a paid plan.
2. Settings → Secrets → Actions: add `ANTHROPIC_API_KEY` and one notification channel (below).
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
