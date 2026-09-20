# even-g2-agent-adapter

Use your own AI agent — [Hermes](https://github.com/NousResearch), a local LLM, or anything
speaking the OpenAI chat-completions API — as the **Even AI** assistant on
[Even Realities G2](https://www.evenrealities.com/) smart glasses.

The G2 app lets you point Even AI at a custom agent. In practice that rarely works
first try: the app posts a slightly unusual request shape, and its glasses renderer
quietly truncates anything longer than about one screen. This adapter is a small,
dependency-free proxy that sits in the middle and fixes both problems.

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
