#!/usr/bin/env bash
# List the conversation agents that exist in YOUR Home Assistant.
#
# Conversation entity IDs are not standard across installations. They depend on
# which integrations you have installed and what each config entry is named, so
# the correct HA_AGENT_ID for your setup can only be discovered, not assumed.
#
#   HA_URL=http://homeassistant.local:8123 ./list-ha-agents.sh
#   HA_URL=... HA_TOKEN=... ./list-ha-agents.sh     # non-interactive
set -euo pipefail

HA_URL="${HA_URL:-http://homeassistant.local:8123}"
HA_URL="${HA_URL%/}"

if [ -z "${HA_TOKEN:-}" ]; then
  printf 'Home Assistant long-lived access token for %s\n' "$HA_URL" >&2
  printf '(profile -> Security -> Long-lived access tokens; input is hidden): ' >&2
  read -rs HA_TOKEN
  printf '\n' >&2
fi
[ -n "$HA_TOKEN" ] || { echo "no token given" >&2; exit 1; }

response="$(curl -fsS -m 20 "$HA_URL/api/states" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H 'Content-Type: application/json' 2>/dev/null)" || {
    echo "Could not reach $HA_URL/api/states" >&2
    echo "Check HA_URL, that the token is valid, and that this machine can reach Home Assistant." >&2
    exit 1
  }

printf '%s' "$response" | python3 -c '
import json, sys

states = json.load(sys.stdin)
agents = [s for s in states if str(s.get("entity_id","")).startswith("conversation.")]
if not agents:
    print("No conversation entities found. Is the Assist/conversation integration set up?")
    raise SystemExit(1)

width = max(len(a["entity_id"]) for a in agents)
print()
print("Conversation agents in this Home Assistant:")
print()
for a in sorted(agents, key=lambda x: x["entity_id"]):
    name = a.get("attributes", {}).get("friendly_name", "")
    note = ""
    if a["entity_id"] == "conversation.home_assistant":
        note = "  <- local intent matching: fastest, house only"
    print("  %-*s  %s%s" % (width, a["entity_id"], name, note))
print()
print("Use one as HA_AGENT_ID, for example:")
print()
print("  INSTANCE=llm ADAPTER_PORT=8650 \\")
print("    HA_AGENT_ID=%s \\" % sorted(
    (a["entity_id"] for a in agents if a["entity_id"] != "conversation.home_assistant"),
    key=len)[0] if len(agents) > 1 else "conversation.home_assistant")
print("    ./install-ha.sh")
print()
'
