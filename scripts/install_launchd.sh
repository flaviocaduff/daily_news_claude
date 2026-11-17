#!/bin/zsh
# Installs the daily brief as a LaunchAgent.
#
# macOS blocks background jobs from reading ~/Documents, so this deploys a snapshot:
#   - the package as a uv tool   (command `daily-brief` in ~/.local/bin)
#   - sources.yaml + runner      -> ~/Library/Application Support/daily-brief/
#   - pages are written to       ~/Library/Application Support/daily-brief/site/
# Re-run this script after changing code or sources.yaml.
#
# Schedule: 07:00 daily; if the Mac was asleep/off it runs on wake/login, and it re-checks
# every 30 min. The runner builds at most once per day, never before 07:00 Zurich.
# Uninstall: launchctl bootout gui/$(id -u)/local.daily-brief; rm ~/Library/LaunchAgents/local.daily-brief.plist
set -e
repo="$(cd "$(dirname "$0")/.." && pwd)"
app="$HOME/Library/Application Support/daily-brief"
label=local.daily-brief
plist="$HOME/Library/LaunchAgents/$label.plist"

mkdir -p "$app" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
uv tool install --force --reinstall "$repo" >/dev/null
cp "$repo/sources.yaml" "$app/sources.yaml"
cp "$repo/scripts/run_daily.sh" "$app/run_daily.sh"
chmod +x "$app/run_daily.sh"

cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$app/run_daily.sh</string></array>
  <key>WorkingDirectory</key><string>$app</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/daily-brief.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/daily-brief.log</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "installed: $plist"
echo "pages:     $app/site/index.html"
echo "log:       $HOME/Library/Logs/daily-brief.log"
