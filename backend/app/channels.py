"""Delivery channels beyond the in-page inbox.

Telegram: real only when TELEGRAM_BOT_TOKEN is set on the SERVER and the subscriber has a chat id. It sends in a
background thread so a slow network never blocks an incident update. NOT yet tested against a live bot: try it with a
real token before relying on it. Everything else (phone page, spoken alerts, boards) is served by this app itself.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
import urllib.request

log = logging.getLogger("roadpulse.channels")


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _send(chat_id: str, text: str, timeout: float = 8.0) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception as e:  # network down, blocked, bad token: never let this break an alert
        log.warning("telegram send failed: %s", type(e).__name__)
        return False


def send_telegram_async(chat_id: str | None, text: str) -> bool:
    """Fire and forget. Returns True if a send was started (token and chat id both present)."""
    if not chat_id or not telegram_configured():
        return False
    threading.Thread(target=_send, args=(chat_id, text), daemon=True).start()
    return True
