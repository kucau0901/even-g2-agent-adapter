#!/usr/bin/env python3
"""
ha_assist_adapter — Home Assistant Assist as an Even Realities G2 agent.

The Even Realities app speaks the OpenAI chat-completions shape. Home Assistant's
Assist pipeline does not: it exposes POST /api/conversation/process and answers
with its own envelope. This adapter translates between the two, so you can pick
Home Assistant Assist as a custom agent on the glasses.

Assist is *local intent matching*, so it answers device questions in tens of
milliseconds rather than the seconds an LLM backend needs. It is the right agent
for "turn on the lights" and "what is the temperature"; it will not answer
general knowledge questions unless you point HA_AGENT_ID at an LLM-backed
conversation entity.

  EvenCore -> :8649/v1/chat/completions -> HA /api/conversation/process

Configuration (environment variables)
-------------------------------------
  ADAPTER_PORT   port to listen on               (default 8649)
  HA_URL         Home Assistant base URL         (default http://homeassistant.local:8123)
  HA_AGENT_ID    conversation entity to use      (default conversation.home_assistant)
  HA_LANGUAGE    language code                   (default en)
  CHAR_BUDGET    max characters in a reply       (default 350)
  CONTEXT_TTL    seconds a conversation_id is reused (default 300, 0 disables)
  ADAPTER_LOG    log file path                   (default ./ha-assist-adapter.log)

The Authorization header from the app is forwarded to Home Assistant untouched,
so put a Home Assistant *long-lived access token* in the app's Token field. The
token never lives in this file or its config.

MIT licensed. Part of https://github.com/kucau0901/even-g2-agent-adapter
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

PORT = int(os.environ.get("ADAPTER_PORT", "8649"))
HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_AGENT_ID = os.environ.get("HA_AGENT_ID", "conversation.home_assistant")
HA_LANGUAGE = os.environ.get("HA_LANGUAGE", "en")
CHAR_BUDGET = int(os.environ.get("CHAR_BUDGET", "350"))
CONTEXT_TTL = int(os.environ.get("CONTEXT_TTL", "300"))
LOG_PATH = os.environ.get("ADAPTER_LOG", os.path.join(os.getcwd(), "ha-assist-adapter.log"))

_ha = urlparse(HA_URL)
HA_SCHEME = _ha.scheme or "http"
HA_HOST = _ha.hostname or "homeassistant.local"
HA_PORT = _ha.port or (443 if HA_SCHEME == "https" else 8123)
HA_PATH = (_ha.path.rstrip("/") if _ha.path else "") + "/api/conversation/process"

_log_lock = threading.Lock()
_ctx_lock = threading.Lock()
_conversation = {"id": None, "at": 0.0}


def log(message):
    line = "%s %s" % (time.strftime("[%Y-%m-%d %H:%M:%S]"), message)
    try:
        with _log_lock:
            with open(LOG_PATH, "a") as handle:
                handle.write(line + "\n")
    except OSError:
        pass
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def strip_markdown(text):
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~#>]+", "", text)
    text = re.sub(r"^\s*[-•*+]\s*", "- ", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def shorten(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("\n"), cut.rfind("! "), cut.rfind("? "))
    if boundary > limit * 0.6:
        return cut[:boundary + 1].rstrip()
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "..."


def get_conversation_id():
    if CONTEXT_TTL <= 0:
        return None
    with _ctx_lock:
        if _conversation["id"] and (time.time() - _conversation["at"]) < CONTEXT_TTL:
            return _conversation["id"]
    return None


def remember_conversation_id(conversation_id):
    if CONTEXT_TTL <= 0 or not conversation_id:
        return
    with _ctx_lock:
        _conversation["id"] = conversation_id
        _conversation["at"] = time.time()


def extract_speech(envelope):
    """Pull the spoken text out of Home Assistant's conversation envelope."""
    response = (envelope or {}).get("response") or {}
    speech = ((response.get("speech") or {}).get("plain") or {}).get("speech")
    if speech:
        return speech.strip(), response.get("response_type", "")

    # Assist answered with no speech (common for successful actions on some versions).
    data = response.get("data") or {}
    if response.get("response_type") == "action_done":
        done = len(data.get("success") or [])
        failed = len(data.get("failed") or [])
        if failed:
            return "%d done, %d failed." % (done, failed), "action_done"
        return ("Done." if done else "Done."), "action_done"
    return "", response.get("response_type", "")


def chat_completion(text):
    return {
        "id": "chatcmpl-ha-assist",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": HA_AGENT_ID,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ha-assist-adapter"

    def log_message(self, *args):
        pass

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
            self._send(200, json.dumps({
                "status": "ok",
                "home_assistant": HA_URL,
                "agent_id": HA_AGENT_ID,
                "char_budget": CHAR_BUDGET,
            }).encode())
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        started = time.time()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"

        try:
            payload = json.loads(raw)
        except ValueError:
            self._send(400, b'{"error":"invalid JSON"}')
            return

        question = ""
        for message in reversed(payload.get("messages") or []):
            if message.get("role") == "user":
                question = str(message.get("content") or "").strip()
                break
        if not question:
            self._send(400, b'{"error":"no user message"}')
            return

        log("-> %r" % question[:70])

        body = {"text": question, "language": HA_LANGUAGE, "agent_id": HA_AGENT_ID}
        existing = get_conversation_id()
        if existing:
            body["conversation_id"] = existing
        encoded = json.dumps(body).encode()

        auth = self.headers.get("Authorization")
        if not auth:
            log("   !! no Authorization header from the app")
            self._send(401, json.dumps({"error": "missing Authorization header; put a "
                                                 "Home Assistant long-lived token in the "
                                                 "app's Token field"}).encode())
            return

        try:
            if HA_SCHEME == "https":
                conn = http.client.HTTPSConnection(HA_HOST, HA_PORT, timeout=60,
                                                   context=ssl.create_default_context())
            else:
                conn = http.client.HTTPConnection(HA_HOST, HA_PORT, timeout=60)
            conn.request("POST", HA_PATH, body=encoded, headers={
                "Host": "%s:%d" % (HA_HOST, HA_PORT),
                "Authorization": auth,
                "Content-Type": "application/json",
                "Content-Length": str(len(encoded)),
            })
            response = conn.getresponse()
            data = response.read()
            status = response.status
            conn.close()
        except Exception as exc:                      # noqa: BLE001
            log("   !! Home Assistant unreachable: %s" % exc)
            self._send(502, json.dumps({"error": "home assistant unreachable",
                                        "detail": str(exc)}).encode())
            return

        if status != 200:
            log("   HA returned %d: %r" % (status, data[:200]))
            hint = ("check the token in the app's Token field"
                    if status in (401, 403) else "check HA_AGENT_ID and HA_URL")
            self._send(200, json.dumps(chat_completion(
                "Home Assistant error %d. %s." % (status, hint))).encode())
            return

        try:
            envelope = json.loads(data)
        except ValueError:
            log("   !! could not parse HA response")
            self._send(200, json.dumps(chat_completion(
                "Home Assistant sent a reply I could not read.")).encode())
            return

        remember_conversation_id(envelope.get("conversation_id"))
        speech, kind = extract_speech(envelope)
        if not speech:
            speech = "Home Assistant had no answer for that."

        shaped = shorten(strip_markdown(speech), CHAR_BUDGET)
        log("   [%s] %d chars in %.2fs" % (kind or "?", len(shaped), time.time() - started))
        self._send(200, json.dumps(chat_completion(shaped)).encode())


def main():
    log("### ha-assist-adapter on :%d -> %s (%s, budget %d)"
        % (PORT, HA_URL, HA_AGENT_ID, CHAR_BUDGET))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
