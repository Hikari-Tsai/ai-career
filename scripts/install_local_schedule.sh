#!/bin/zsh
# Install (or update) the daily local crawl on this Mac.
#   scripts/install_local_schedule.sh            # install, runs daily at 04:00
#   scripts/install_local_schedule.sh uninstall  # remove the schedule (keeps the clone)
set -euo pipefail
CLONE="$HOME/.ai-career-crawler"
LABEL="tw.ai-career.local-crawl"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/ai-career-crawl.log"

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL (clone left at $CLONE)"
  exit 0
fi

if [[ ! -d "$CLONE/.git" ]]; then
  git clone -q https://github.com/Hikari-Tsai/ai-career.git "$CLONE"
fi
# push over HTTPS with the gh CLI's stored login, so no SSH agent is needed under launchd
git -C "$CLONE" config credential.helper '!/opt/homebrew/bin/gh auth git-credential'
git -C "$CLONE" config user.name "$(git config --global user.name || echo ai-career-bot)"
git -C "$CLONE" config user.email "$(git config --global user.email || echo ai-career-bot@users.noreply.github.com)"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$CLONE/scripts/local_crawl.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL: daily 04:00, log $LOG"
echo "run now: launchctl kickstart gui/$(id -u)/$LABEL"
