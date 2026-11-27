"""RoadPulse Telegram bot (no extra libraries; long polling with httpx).

Citizens send a LOCATION (paperclip → Location), then pick what they see. The bot posts to /api/reports.
/alerts lists verified disruptions. Replies in English and Telugu.

Run:  TELEGRAM_BOT_TOKEN=... ROADPULSE_API=http://localhost:8000 python bot/telegram_bot.py
Create the bot token with @BotFather in Telegram.
"""
from __future__ import annotations

import os
import time

import httpx

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = os.environ.get("ROADPULSE_API", "http://localhost:8000")
TG = f"https://api.telegram.org/bot{TOKEN}"

TYPES = {"flood": "🌊 Waterlogging / నీరు నిలిచింది", "accident": "🚑 Accident / ప్రమాదం",
         "blockage": "🚧 Road blocked / రోడ్డు మూసుకుపోయింది", "construction": "🏗 Construction / నిర్మాణ పనులు"}
pending_location: dict[int, tuple[float, float]] = {}


def send(chat_id: int, text: str, keyboard: list | None = None):
    body = {"chat_id": chat_id, "text": text}
    if keyboard:
        body["reply_markup"] = {"inline_keyboard": keyboard}
    httpx.post(f"{TG}/sendMessage", json=body, timeout=15)


def handle(update: dict, api: httpx.Client):
    if "callback_query" in update:
        q = update["callback_query"]
        chat = q["message"]["chat"]["id"]
        kind = q["data"]
        loc = pending_location.pop(chat, None)
        httpx.post(f"{TG}/answerCallbackQuery", json={"callback_query_id": q["id"]}, timeout=15)
        if not loc:
            send(chat, "Please share your location first. / ముందుగా మీ లొకేషన్ పంపండి.")
            return
        r = api.post("/api/reports", json={"type": kind, "lat": loc[0], "lon": loc[1], "reporter_id": f"tg-{chat}"})
        if r.status_code == 429:
            send(chat, "Too many reports, please wait a minute. / దయచేసి ఒక నిమిషం ఆగండి.")
        elif r.is_success:
            send(chat, f"Thanks! {r.json()['message']}.\nధన్యవాదాలు! మీ సమాచారం అందింది.")
        else:
            send(chat, "Sorry, the report could not be saved. / క్షమించండి, మళ్లీ ప్రయత్నించండి.")
        return

    msg = update.get("message") or {}
    chat = msg.get("chat", {}).get("id")
    if not chat:
        return
    if "location" in msg:
        pending_location[chat] = (msg["location"]["latitude"], msg["location"]["longitude"])
        send(chat, "What do you see? / మీరు ఏమి చూస్తున్నారు?",
             [[{"text": label, "callback_data": k}] for k, label in TYPES.items()])
    elif msg.get("text", "").startswith("/alerts"):
        items = api.get("/api/incidents").json()["incidents"]
        if not items:
            send(chat, "No verified disruptions right now. / ప్రస్తుతం ఎలాంటి అంతరాయాలు లేవు.")
        else:
            lines = [f"• {i['title']} ({i['severity']})" for i in items[:10]]
            send(chat, "Live alerts / ప్రత్యక్ష హెచ్చరికలు:\n" + "\n".join(lines))
    else:
        send(chat, "Share your 📍 location to report a road problem, or send /alerts.\n"
                   "రోడ్డు సమస్యను తెలియజేయడానికి మీ 📍 లొకేషన్ పంపండి, లేదా /alerts పంపండి.")


def main():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (from @BotFather)")
    api = httpx.Client(base_url=API, timeout=15)
    offset = 0
    print("RoadPulse bot running…")
    while True:
        try:
            r = httpx.get(f"{TG}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    handle(upd, api)
                except Exception as e:  # one bad update must not stop the bot
                    print("update failed:", e)
        except httpx.HTTPError as e:
            print("network error, retrying:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
