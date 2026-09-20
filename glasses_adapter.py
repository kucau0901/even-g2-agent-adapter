#!/usr/bin/env python3
"""
even-g2-agent-adapter
=====================

A tiny stdlib-only proxy that makes an OpenAI-compatible LLM endpoint usable as
a custom agent on Even Realities G2 smart glasses.

Why this is needed
------------------
The Even Realities app ("EvenCore") can point Even AI at your own agent, but its
glasses renderer only draws roughly one screen of text (~400-500 characters) and
then gives up with "Struggling to render more...". Agent backends happily return
several paragraphs, so most real answers get cut off.

This adapter sits between the app and your backend and:

  1. Injects a system message telling the model it is writing for a 576x288
     heads-up display: plain text, no markdown, lead with the answer.
  2. Strips markdown that slips through anyway (the firmware font silently drops
     unsupported glyphs, which garbles text).
  3. Truncates on a sentence boundary as a backstop, so the renderer never hits
     its wall.

It is deliberately dependency-free: Python 3.8+ standard library only.

Configuration (all via environment variables)
---------------------------------------------
  ADAPTER_PORT   port to listen on                  (default 8646)
  UPSTREAM_URL   full chat-completions URL          (default http://127.0.0.1:8642/v1/chat/completions)
  CHAR_BUDGET    max characters in a reply          (default 350)
  ADAPTER_LOG    log file path                      (default ./glasses-adapter.log)
  SYSTEM_EXTRA   extra text appended to the system message (optional)

The Authorization header from the app is forwarded upstream untouched, so your
API key lives only in the Even Realities app and never in this file.

MIT licensed. See README.md for install instructions.
"""

import http.client
import json
import os
import re
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

PORT = int(os.environ.get("ADAPTER_PORT", "8646"))
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "http://127.0.0.1:8642/v1/chat/completions")
CHAR_BUDGET = int(os.environ.get("CHAR_BUDGET", "350"))
LOG_PATH = os.environ.get("ADAPTER_LOG", os.path.join(os.getcwd(), "glasses-adapter.log"))
SYSTEM_EXTRA = os.environ.get("SYSTEM_EXTRA", "").strip()

_up = urlparse(UPSTREAM_URL)
UP_SCHEME = _up.scheme or "http"
UP_HOST = _up.hostname or "127.0.0.1"
UP_PORT = _up.port or (443 if UP_SCHEME == "https" else 80)
UP_PATH = _up.path or "/v1/chat/completions"

# Hop-by-hop headers that must not be forwarded (RFC 7230 s6.1).
HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

SYSTEM_PROMPT = (
    "You are replying on Even Realities G2 smart glasses: a 576x288 monochrome "
    "heads-up display that the user reads while walking around. Follow these rules "
    "strictly.\n"
    f"1. Keep the ENTIRE reply under {CHAR_BUDGET} characters. This is a hard limit. "
    "Longer replies are cut off mid-sentence and the user sees a render error.\n"
    "2. Plain text ONLY. No markdown, no **bold**, no #headings, no bullet glyphs, "
    "no tables, no code fences, no emoji. Unsupported characters are silently "
    "dropped by the firmware font and corrupt the line.\n"
    "3. Lead with the answer in the first sentence. No preamble, no sign-off, no "
    "restating the question.\n"
    "4. For multiple items, put each on its own line prefixed with '- ', at most 5 "
    "items, and state how many were omitted.\n"
    "5. Summarise aggressively. The user can ask a follow-up for more detail."
)
if SYSTEM_EXTRA:
    SYSTEM_PROMPT += "\n" + SYSTEM_EXTRA

_log_lock = threading.Lock()


def log(message):
    line = "%s %s" % (time.strftime("[%Y-%m-%d %H:%M:%S]"), message)
    try:
        with _log_lock:
            with open(LOG_PATH, "a") as handle:
                handle.write(line + "\n")
    except OSError:
        pass  # never let logging take the service down
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------- #
# text shaping
# --------------------------------------------------------------------------- #

def strip_markdown(text):
    """Remove markup the glasses font cannot render."""
    text = re.sub(r"```[\s\S]*?```", "", text)          # fenced code
    text = re.sub(r"`([^`]*)`", r"\1", text)            # inline code
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links / images
    text = re.sub(r"[*_~#>]+", "", text)                # emphasis, headings, quotes
    text = re.sub(r"^\s*[-•*+]\s*", "- ", text, flags=re.M)  # normalise bullets
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def shorten(text, limit):
    """Truncate at the last sentence boundary that keeps most of the budget."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("\n"), cut.rfind("! "), cut.rfind("? "))
    if boundary > limit * 0.6:
        return cut[:boundary + 1].rstrip()
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "..."


def shape(text):
    return shorten(strip_markdown(text), CHAR_BUDGET)


# --------------------------------------------------------------------------- #
# http handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "even-g2-agent-adapter"

    def log_message(self, *args):
        pass  # we do our own logging

    def _send(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            log("   client disconnected before the reply was written")

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", ""):
            payload = json.dumps({
                "status": "ok",
                "upstream": UPSTREAM_URL,
                "char_budget": CHAR_BUDGET,
            }).encode()
            self._send(200, payload)
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        started = time.time()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"

        try:
            payload = json.loads(raw)
        except ValueError:
            log("   malformed JSON from client")
            self._send(400, b'{"error":"invalid JSON"}')
            return

        messages = payload.get("messages") or []
        if not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        payload["messages"] = messages
        payload.pop("stream", None)  # EvenCore cannot read SSE
        body = json.dumps(payload).encode()

        question = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                question = str(message.get("content"))[:70]
                break
        log("-> %r" % question)

        try:
            if UP_SCHEME == "https":
                conn = http.client.HTTPSConnection(
                    UP_HOST, UP_PORT, timeout=310, context=ssl.create_default_context()
                )
            else:
                conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=310)

            headers = {
                k: v for k, v in self.headers.items()
                # NOTE: compared case-insensitively on purpose. EvenCore sends a
                # lowercase "host:"; letting it through alongside our own "Host"
                # produces two Host headers and many servers answer 400.
                if k.lower() not in HOP and k.lower() not in ("host", "content-length")
            }
            headers["Host"] = "%s:%d" % (UP_HOST, UP_PORT)
            headers["Content-Length"] = str(len(body))
            headers["Content-Type"] = "application/json"

            conn.request("POST", UP_PATH, body=body, headers=headers)
            response = conn.getresponse()
            data = response.read()
            status = response.status
            upstream_type = response.getheader("Content-Type", "application/json")
            conn.close()
        except Exception as exc:                      # noqa: BLE001 - report anything
            log("   !! upstream error: %s" % exc)
            self._send(502, json.dumps({"error": "upstream unreachable",
                                        "detail": str(exc)}).encode())
            return

        if status != 200:
            log("   upstream %d: %r" % (status, data[:200]))
            self._send(status, data, upstream_type)
            return

        try:
            parsed = json.loads(data)
            original = parsed["choices"][0]["message"]["content"]
            shaped = shape(original)
            parsed["choices"][0]["message"]["content"] = shaped
            data = json.dumps(parsed).encode()
            log("   %d -> %d chars in %.1fs%s" % (
                len(original), len(shaped), time.time() - started,
                "  [truncated]" if len(shaped) < len(original) else "  [fit]"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            log("   passthrough, could not shape reply: %s" % exc)

        self._send(200, data)


def main():
    log("### even-g2-agent-adapter listening on :%d -> %s (budget %d chars)"
        % (PORT, UPSTREAM_URL, CHAR_BUDGET))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
