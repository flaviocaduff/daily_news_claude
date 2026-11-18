#!/bin/zsh
# Starts the cloud build (GitHub Actions) once a day from this Mac, from 07:15 Zurich on (backup: GitHub's own cron runs at 06:30).
# launchd runs it at 07:15, or right after wake/login if the Mac was asleep then.
# Skips if today's brief is already published or a build is already running.
# --force: start a build regardless (for testing).
set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
repo=flaviocaduff/daily_news_claude
site=https://flaviocaduff.github.io/daily_news_claude
today=$(TZ=Europe/Zurich date +%F)
now=$(TZ=Europe/Zurich date +%H%M)
echo "=== $(date) trigger check ($today)"

if [[ "${1:-}" != "--force" ]]; then
  [[ $now -lt 715 ]] && { echo "before 07:15, nothing to do"; exit 0; }
  if curl -sfI -m 15 "$site/archive/$today.html" >/dev/null; then echo "already published"; exit 0; fi
  if [[ -n "$(gh run list -R $repo --workflow daily-brief.yml --status in_progress --json databaseId --jq '.[0].databaseId' 2>/dev/null)" ]]; then
    echo "build already running"; exit 0
  fi
fi

# Wait up to ~2 min for the network after wake.
for i in {1..24}; do curl -s -m 5 -o /dev/null https://api.github.com && break; sleep 5; done
gh workflow run daily-brief.yml -R $repo && echo "cloud build started"
