#!/usr/bin/env bash
# Install even-g2-agent-adapter as a macOS LaunchAgent.
#
#   ./install.sh
#   UPSTREAM_URL=http://127.0.0.1:11434/v1/chat/completions CHAR_BUDGET=300 ./install.sh
set -euo pipefail

LABEL="com.eveng2.agent-adapter"
SHARE_DIR="$HOME/.local/share/even-g2-adapter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

ADAPTER_PORT="${ADAPTER_PORT:-8646}"
UPSTREAM_URL="${UPSTREAM_URL:-http://127.0.0.1:8642/v1/chat/completions}"
CHAR_BUDGET="${CHAR_BUDGET:-350}"
SYSTEM_EXTRA="${SYSTEM_EXTRA:-}"

[ "$(uname)" = "Darwin" ] || { echo "install.sh is for macOS; use systemd/ on Linux." >&2; exit 1; }

PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || { echo "python3 not found in PATH." >&2; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' \
  || { echo "python3 >= 3.8 required (found $("$PYTHON" -V 2>&1))." >&2; exit 1; }

echo "==> installing to $SHARE_DIR"
mkdir -p "$SHARE_DIR" "$HOME/Library/LaunchAgents"
install -m 0755 "$(dirname "$0")/glasses_adapter.py" "$SHARE_DIR/glasses_adapter.py"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "==> stopping existing service"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sleep 1
fi

echo "==> writing $PLIST"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SHARE_DIR/glasses_adapter.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ADAPTER_PORT</key><string>$ADAPTER_PORT</string>
        <key>UPSTREAM_URL</key><string>$UPSTREAM_URL</string>
        <key>CHAR_BUDGET</key><string>$CHAR_BUDGET</string>
        <key>SYSTEM_EXTRA</key><string>$SYSTEM_EXTRA</string>
        <key>ADAPTER_LOG</key><string>$SHARE_DIR/glasses-adapter.log</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>$SHARE_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$SHARE_DIR/stdout.log</string>
    <key>StandardErrorPath</key><string>$SHARE_DIR/stderr.log</string>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null

echo "==> starting"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 2

if curl -fsS --max-time 5 "http://127.0.0.1:$ADAPTER_PORT/health" >/dev/null 2>&1; then
  echo
  echo "installed and healthy."
  curl -s "http://127.0.0.1:$ADAPTER_PORT/health"; echo
  echo
  echo "Now in the Even Realities app:"
  echo "  Settings -> Even AI -> Agent configuration -> Add agent"
  echo "  URL:   http://<this-machine>:$ADAPTER_PORT/v1/chat/completions"
  echo "  Token: whatever your backend expects"
  echo "  Then TAP the agent name so the checkmark moves to it."
  echo
  echo "Log: $SHARE_DIR/glasses-adapter.log"
else
  echo "service did not answer on :$ADAPTER_PORT" >&2
  echo "check $SHARE_DIR/stderr.log" >&2
  exit 1
fi
