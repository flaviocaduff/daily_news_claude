#!/bin/zsh
# Installs a LaunchAgent that starts the cloud build at 07:15 (or on wake/login if missed), once a day.
# The script is copied out of ~/Documents because macOS blocks background jobs from reading it.
# Uninstall: launchctl bootout gui/$(id -u)/local.daily-brief-trigger; rm ~/Library/LaunchAgents/local.daily-brief-trigger.plist
set -e
repo="$(cd "$(dirname "$0")/.." && pwd)"
app="$HOME/Library/Application Support/daily-brief"
label=local.daily-brief-trigger
plist="$HOME/Library/LaunchAgents/$label.plist"

mkdir -p "$app" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$repo/scripts/trigger_cloud.sh" "$app/trigger_cloud.sh"
chmod +x "$app/trigger_cloud.sh"

cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$app/trigger_cloud.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>15</integer></dict>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/daily-brief-trigger.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/daily-brief-trigger.log</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "installed: $plist"
echo "log:       $HOME/Library/Logs/daily-brief-trigger.log"
