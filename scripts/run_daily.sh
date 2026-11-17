#!/bin/zsh
# Daily runner for launchd. Builds the brief at most once per day, from 07:00 Zurich on,
# then opens it and shows a notification. Safe to trigger many times (wake, login, timer).
set -u
cd "$(dirname "$0")" || exit 1   # app dir with sources.yaml (see install_launchd.sh)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
unset ANTHROPIC_API_KEY   # always use the Claude subscription via the claude CLI

today=$(TZ=Europe/Zurich date +%F)
hour=$(TZ=Europe/Zurich date +%H)
force=${1:-}
if [[ "$force" != "--force" ]]; then
  [[ $hour -lt 7 ]] && exit 0
  [[ -f "site/archive/$today.html" ]] && exit 0
fi

# Wait up to ~2 min for the network after wake.
for i in {1..24}; do
  curl -s -m 5 -o /dev/null https://tldr.tech && break
  sleep 5
done

echo "=== $(date) building brief for $today"
if daily-brief -c sources.yaml; then
  open "site/index.html"
  osascript -e "display notification \"$(python3 -c 'import json;print(json.load(open("site/latest.json"))["text"].replace("\"","'"'"'"))')\" with title \"Daily Brief\""
else
  osascript -e 'display notification "Build failed, see ~/Library/Logs/daily-brief.log" with title "Daily Brief"'
  exit 1
fi
