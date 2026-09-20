#!/usr/bin/env bash
# Remove even-g2-agent-adapter (macOS).
set -euo pipefail
LABEL="com.eveng2.agent-adapter"
SHARE_DIR="$HOME/.local/share/even-g2-adapter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "==> stopping service"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi
[ -f "$PLIST" ] && rm -f "$PLIST" && echo "==> removed $PLIST"

if [ -d "$SHARE_DIR" ]; then
  echo "==> $SHARE_DIR left in place (contains your logs)."
  echo "    remove it with: rm -rf \"$SHARE_DIR\""
fi
echo "done. Remember to remove the agent in the Even Realities app too."
