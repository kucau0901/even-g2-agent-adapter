#!/usr/bin/env bash
# Install the Home Assistant Assist adapter as a macOS LaunchAgent.
#
#   HA_URL=http://homeassistant.local:8123 ./install-ha.sh
set -euo pipefail

LABEL="com.eveng2.ha-assist"
SHARE_DIR="$HOME/.local/share/even-g2-adapter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

ADAPTER_PORT="${ADAPTER_PORT:-8649}"
HA_URL="${HA_URL:-http://homeassistant.local:8123}"
HA_AGENT_ID="${HA_AGENT_ID:-conversation.home_assistant}"
HA_LANGUAGE="${HA_LANGUAGE:-en}"
CHAR_BUDGET="${CHAR_BUDGET:-350}"

[ "$(uname)" = "Darwin" ] || { echo "install-ha.sh is for macOS; use systemd/ on Linux." >&2; exit 1; }
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || { echo "python3 not found." >&2; exit 1; }

echo "==> installing to $SHARE_DIR"
mkdir -p "$SHARE_DIR" "$HOME/Library/LaunchAgents"
install -m 0755 "$(dirname "$0")/ha_assist_adapter.py" "$SHARE_DIR/ha_assist_adapter.py"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
sleep 1

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SHARE_DIR/ha_assist_adapter.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ADAPTER_PORT</key><string>$ADAPTER_PORT</string>
        <key>HA_URL</key><string>$HA_URL</string>
        <key>HA_AGENT_ID</key><string>$HA_AGENT_ID</string>
        <key>HA_LANGUAGE</key><string>$HA_LANGUAGE</string>
        <key>CHAR_BUDGET</key><string>$CHAR_BUDGET</string>
        <key>ADAPTER_LOG</key><string>$SHARE_DIR/ha-assist-adapter.log</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
    <key>WorkingDirectory</key><string>$SHARE_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$SHARE_DIR/ha-assist.stdout.log</string>
    <key>StandardErrorPath</key><string>$SHARE_DIR/ha-assist.stderr.log</string>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 2

if curl -fsS --max-time 5 "http://127.0.0.1:$ADAPTER_PORT/health" >/dev/null 2>&1; then
  echo
  echo "installed and healthy."
  curl -s "http://127.0.0.1:$ADAPTER_PORT/health"; echo
  echo
  echo "In the Even Realities app, add a second agent:"
  echo "  Name:  Assist"
  echo "  URL:   http://<this-machine>:$ADAPTER_PORT/v1/chat/completions"
  echo "  Token: a Home Assistant LONG-LIVED ACCESS TOKEN"
  echo "         (HA -> your profile -> Security -> Long-lived access tokens)"
else
  echo "service did not answer on :$ADAPTER_PORT" >&2
  echo "check $SHARE_DIR/ha-assist.stderr.log" >&2
  exit 1
fi
