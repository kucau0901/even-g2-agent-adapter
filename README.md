# even-g2-agent-adapter

Use your own AI agent — [Hermes](https://github.com/NousResearch), a local LLM, or anything
speaking the OpenAI chat-completions API — as the **Even AI** assistant on
[Even Realities G2](https://www.evenrealities.com/) smart glasses.

The G2 app lets you point Even AI at a custom agent. In practice that rarely works
first try: the app posts a slightly unusual request shape, and its glasses renderer
quietly truncates anything longer than about one screen. This adapter is a small,
dependency-free proxy that sits in the middle and fixes both problems.

It also ships a second adapter for **Home Assistant**, so you can run a fast local Assist agent and a slower, smarter one side by side and tap between them — see [below](#running-several-agents-pick-your-speedcapability-tradeoff).

```
G2 glasses ──BLE──> Even Realities app ──HTTP──> [ this adapter ] ──HTTP──> your agent
```

---

## Why you need it

If you wire your agent up directly, you will hit one or more of these:

| Symptom | Cause |
|---|---|
| `AI server error` immediately | The app POSTs to *exactly* the URL you type — it appends nothing. A base URL like `.../v1` yields `POST /v1` → 404. |
| Reply ends with `Struggling to render more...` | The glasses renderer draws roughly **400–500 characters** — about one screen — then gives up. Normal LLM answers are far longer. |
| Garbled or missing characters | The firmware font silently drops unsupported glyphs. Markdown asterisks, emoji and box characters corrupt the line. |
| `AI server error` even though the server is fine | Your phone cannot reach your server. See [Networking](#networking-the-part-that-actually-bites). |

The adapter injects a system message telling your model it is writing for a
576×288 heads-up display, strips markdown that slips through, and truncates on a
sentence boundary as a backstop.

**Before**

> Kuala Lumpur's history begins in 1857 when Chinese tin miners… *(continues for
> 1,400 characters, glasses show the first screen then* `Struggling to render more...`*)*

**After**

> Kuala Lumpur began around 1857, when Chinese tin miners settled near the
> Klang-Gombak confluence. It became Selangor's capital in 1880.

---

## Measured behaviour of the Even Realities app

These numbers were measured against `EvenCore/1.0` by proxying a real device, not
taken from documentation. They may change with app updates.

**Request the app sends**

```http
POST <exactly the URL you configured>
User-Agent: EvenCore/1.0
Authorization: Bearer <the token you configured>
x-openclaw-agent-id: main
Content-Type: application/json

{"model": "openclaw", "messages": [{"role": "user", "content": "..."}]}
```

* It appends **nothing** to your URL. Configure the **full** endpoint path.
* It never sets `"stream"`. Server-sent events are not read, so streaming is useless here.
* `model` is hard-coded to `openclaw`. Most backends ignore an unknown model name; if
  yours rejects it, map it in the adapter.
* It sends no system message, which is why this adapter injects one.

**Limits**

| Property | Value | How it was measured |
|---|---|---|
| Client timeout | **300 s** | A server that accepted the POST and never replied; the client sent FIN at 299.1 s. |
| Render ceiling | **~400–500 characters** | A reply containing a marker every 100 characters; the display stopped at the 500 marker. |

The 300-second timeout is generous — a slow agentic backend taking 15–30 seconds is
never the problem. **Reply length is the real constraint.**

> The render measurement used `.` as filler, one of the narrowest glyphs in a
> non-monospaced font. Real prose hits the wall sooner, which is why the default
> budget is a conservative **350**.

---

## Requirements

* Python **3.8+** (standard library only — no `pip install`)
* An OpenAI-compatible chat-completions endpoint
* A network path from your **phone** to the machine running the adapter

---

## Install

### macOS (launchd)

```bash
git clone https://github.com/kucau0901/even-g2-agent-adapter.git
cd even-g2-agent-adapter
./install.sh
```

The installer copies the script to `~/.local/share/even-g2-adapter/`, writes a
LaunchAgent to `~/Library/LaunchAgents/com.eveng2.agent-adapter.plist`, and starts it
with `RunAtLoad` and `KeepAlive` so it survives reboots and crashes.

Override defaults at install time:

```bash
UPSTREAM_URL=http://127.0.0.1:11434/v1/chat/completions CHAR_BUDGET=300 ./install.sh
```

Check it:

```bash
curl -s http://127.0.0.1:8646/health
tail -f ~/.local/share/even-g2-adapter/glasses-adapter.log
```

Remove it:

```bash
./uninstall.sh
```

### Linux (systemd)

```bash
git clone https://github.com/kucau0901/even-g2-agent-adapter.git
cd even-g2-agent-adapter
mkdir -p ~/.config/systemd/user
cp systemd/even-g2-adapter.service ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/even-g2-adapter.service   # set paths + UPSTREAM_URL
systemctl --user daemon-reload
systemctl --user enable --now even-g2-adapter
```

### Manual

```bash
UPSTREAM_URL=http://127.0.0.1:8642/v1/chat/completions CHAR_BUDGET=350 \
  python3 glasses_adapter.py
```

---

## Configure the Even Realities app

**Settings → Even AI → Agent configuration → Add agent**

| Field | Value |
|---|---|
| **Name** | anything, e.g. `Hermes` |
| **URL** | `http://<adapter-host>:8646/v1/chat/completions` |
| **Token** | the bearer token your backend expects |

Then **tap the agent's name** so the checkmark moves to it — saving alone does not
select it.

> ⚠️ Use the **full path**, including `/v1/chat/completions`. A base URL will 404.

The adapter forwards the `Authorization` header upstream untouched, so your key
lives only in the app — never in this repo or its config.

To edit the agent later you may need to dismiss an *"Even AI is active"* dialog first.

---

## Running several agents: pick your speed/capability tradeoff

The Even Realities app holds a **list** of agents and you switch by tapping one.
That is worth exploiting, because agent backends trade speed against capability
very steeply. All figures below were measured end-to-end on real hardware.

| Agent | General knowledge | House state & control | Typical latency |
|---|---|---|---|
| Home Assistant **local Assist** (`conversation.home_assistant`) | ✗ | ✓ | **0.03 – 0.08 s** |
| Home Assistant + **LLM conversation entity** (`conversation.openai_conversation`) | ✓ | ✓ | **2.7 – 5.8 s** |
| A full **agentic backend** (Hermes, and similar tool-using agents) | ✓ | ✓ (via its own tools) | **3 – 19 s** |

Local Assist is intent matching, not inference — it answers "is the front door
locked" in under a tenth of a second, but it genuinely cannot tell you the capital
of France:

```
conversation.home_assistant   "what is the capital of France"
  -> "Sorry, I am not aware of any device called capital of France"
conversation.openai_conversation "what is the capital of France"
  -> "The capital of France is Paris."
```

A sensible setup is **all three**, named clearly, and you tap whichever suits the
question:

* **Assist** — "turn on the lights", "what is the temperature". Instant.
* **OpenAI** — anything, including the house. A few seconds.
* **Hermes** *(or your own agent)* — when you need real tools: email, calendar,
  shell, long-running work. Slowest, most capable.

### Home Assistant needs no extra code per agent

`ha_assist_adapter.py` takes the conversation entity as configuration, so a second
Home Assistant agent is just a second instance of the same script on another port:

```bash
# fast local Assist on :8649
HA_URL=http://homeassistant.local:8123 ./install-ha.sh

# LLM-backed conversation entity on :8650, same script
INSTANCE=openai ADAPTER_PORT=8650 \
  HA_AGENT_ID=conversation.openai_conversation \
  HA_URL=http://homeassistant.local:8123 ./install-ha.sh
```

`INSTANCE` names the launchd service and its log files so the instances do not
collide. Add each one in the app as its own agent, pointing at its own port. They
all use the **same** Home Assistant long-lived token.

List the conversation entities available to you in **Developer Tools → Template**:

```jinja
{{ states.conversation | map(attribute='entity_id') | list }}
```

Anything in that list is a valid `HA_AGENT_ID` — the OpenAI, Google and
Extended OpenAI integrations all register one.

> Routing house questions through an LLM entity spends API credits on every query
> and adds seconds of latency for something local Assist answers instantly. Keep
> the fast local agent in your list even if you mostly use the LLM one.

### Home Assistant adapter configuration

| Variable | Default | Meaning |
|---|---|---|
| `INSTANCE` | `assist` | Names the launchd service and log files |
| `ADAPTER_PORT` | `8649` | Port to listen on |
| `HA_URL` | `http://homeassistant.local:8123` | Home Assistant base URL |
| `HA_AGENT_ID` | `conversation.home_assistant` | Which conversation entity to use |
| `HA_LANGUAGE` | `en` | Language passed to Assist |
| `CHAR_BUDGET` | `350` | Hard cap on reply length |
| `CONTEXT_TTL` | `300` | Seconds a `conversation_id` is reused for follow-ups (`0` disables) |

Add each agent in the app with:

| Field | Value |
|---|---|
| **Name** | `Assist`, `OpenAI`, … |
| **URL** | `http://<adapter-host>:<port>/v1/chat/completions` |
| **Token** | a Home Assistant **long-lived access token** |

Create the token in Home Assistant: your profile (bottom-left) → **Security** →
**Long-lived access tokens** → **Create Token**. The adapter forwards it straight
through and never stores it.

Home Assistant's conversation API is not OpenAI-compatible — it is
`POST /api/conversation/process` with its own envelope — which is why it needs this
adapter rather than just a different `UPSTREAM_URL` on the main one.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ADAPTER_PORT` | `8646` | Port to listen on |
| `UPSTREAM_URL` | `http://127.0.0.1:8642/v1/chat/completions` | Full upstream endpoint (`http` or `https`) |
| `CHAR_BUDGET` | `350` | Hard cap on reply length |
| `ADAPTER_LOG` | `./glasses-adapter.log` | Log file path |
| `SYSTEM_EXTRA` | — | Extra text appended to the injected system message |

`SYSTEM_EXTRA` is useful for persona or locale, for example:

```bash
SYSTEM_EXTRA="Answer in British English. The user is in Kuala Lumpur."
```

### Tuning the budget

Start at 350. If replies still truncate on the display, drop to 280. If they feel
clipped and your display handles more, try 400. Restart after changing it.

---

## Networking: the part that actually bites

Your **phone** must reach the adapter. The glasses talk to the phone over Bluetooth;
all HTTP happens from the phone.

**Many home routers enable wireless client isolation**, which blocks phone→computer
traffic even when both sit on the same subnet and every firewall is off. Symptom:
100% packet loss between the two, in both directions, with nothing obviously wrong.

Reliable options, best first:

1. **Tailscale / WireGuard** — install on both phone and host, use the host's VPN
   address. Works on any network, not just home Wi-Fi, and needs no port forwarding.
   This is the recommended setup.
2. **A router that allows client-to-client traffic** — plain LAN IP, home only.
3. **A public HTTPS endpoint** — works, but note the app sends only a bearer token.
   Any reverse proxy requiring *extra* headers (for example Cloudflare Access service
   tokens) **cannot work**, because the app has no way to send them.

Quick check from the phone, with USB debugging on:

```bash
adb shell "echo | toybox nc -w 3 <adapter-host> 8646 && echo OPEN || echo BLOCKED"
```

---

## Troubleshooting

| What you see | Try this |
|---|---|
| `AI server error` instantly | Wrong URL. Use the full `/v1/chat/completions` path. |
| `AI server error` after a pause | Phone cannot reach the host — see [Networking](#networking-the-part-that-actually-bites). |
| `Struggling to render more...` | Lower `CHAR_BUDGET`. |
| Replies look garbled | Markdown or emoji reached the display; check the log for `[fit]` vs `[truncated]`. |
| Adapter logs `upstream 400` | Your backend rejected the request. Check the `model` name it expects. |
| Adapter logs `upstream 401` | Wrong token in the app's **Token** field. |
| Adapter logs `upstream unreachable` | Backend is down, or `UPSTREAM_URL` is wrong. |
| Nothing in the log at all | The request never arrived — a networking problem, not an adapter problem. |

Health check:

```bash
curl -s http://127.0.0.1:8646/health
```

---

## Security notes

* The adapter **listens on `0.0.0.0`** so your phone can reach it. On an untrusted
  network, bind it behind a VPN (Tailscale) rather than exposing the port.
* It **does not store or log the `Authorization` header**, and never writes your token
  to disk. It forwards it upstream and forgets it.
* It logs the first 70 characters of each question, so you can see what the glasses
  asked. Point `ADAPTER_LOG` at `/dev/null` if you would rather it kept nothing.

---

## Credits

Built while wiring a personal Hermes agent to a pair of G2s. The protocol and limit
figures were measured on real hardware; corrections and additions are welcome.

Useful community resources:

* [even-g2-notes](https://github.com/nickustinov/even-g2-notes) — architecture notes, Unicode glyph tables, SDK quirks
* [even-toolkit](https://github.com/fabioglimb/even-toolkit) — components and helpers for Even Hub apps

## License

MIT — see [LICENSE](LICENSE).
