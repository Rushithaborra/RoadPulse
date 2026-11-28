"""RoadPulse Telegram bot (no extra libraries; long polling with httpx).

Reporting: citizens send a LOCATION (paperclip → Location), then pick what they see. The bot posts to /api/reports.
/alerts lists verified disruptions. Replies in English and Telugu.

Commute alerts (new): /start explains, /agree signs up (consent), then:
    /home and /work   the next location you share sets that place
    /depart 08:30     your usual departure time (pre-trip alert goes out 15 minutes before)
    /go               start a trip to work; then share your LIVE location (paperclip → Location → Share Live Location)
    /forget           delete your data
Alerts are SENT BY THE SERVER, not by this process: set TELEGRAM_BOT_TOKEN on the server too. This bot only registers
you and forwards your location. Telegram live location keeps updating in the background, unlike a web page.
NOT yet tested with a live bot.

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
subs: dict[int, dict] = {}        # chat id -> {"id", "token"}  (in memory: /agree again after a restart)
setting: dict[int, str] = {}      # chat id -> "home" | "work": the next location shared sets that place


def send(chat_id: int, text: str, keyboard: list | None = None):
    body = {"chat_id": chat_id, "text": text}
    if keyboard:
        body["reply_markup"] = {"inline_keyboard": keyboard}
    httpx.post(f"{TG}/sendMessage", json=body, timeout=15)


def sub_call(api: httpx.Client, chat: int, method: str, path: str, **kw):
    sub = subs.get(chat)
    if not sub:
        return None
    return api.request(method, path.format(id=sub["id"]), headers={"X-Subscriber-Token": sub["token"]}, **kw)


def commute_command(chat: int, text: str, api: httpx.Client) -> bool:
    """Returns True if the message was a commute command."""
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start":
        send(chat, "RoadPulse warns you ONLY if a road incident is on your route. To get alerts we use your location "
                   "(shared by you) and keep your saved home and work. Send /agree to consent; /forget deletes everything.\n"
                   "మీ మార్గంలో ప్రమాదం ఉంటేనే హెచ్చరిక వస్తుంది. అంగీకరించడానికి /agree పంపండి.")
    elif cmd == "/agree":
        r = api.post("/api/subscribers", json={"name": f"tg-{chat}", "vehicle": "two_wheeler", "consent_location": True,
                                               "telegram_chat": str(chat)})
        if r.is_success:
            subs[chat] = {"id": r.json()["id"], "token": r.json()["token"]}
            send(chat, "Done. Now send /home, then share your home location; then /work and your work location. "
                       "Optional: /depart 08:30. When you leave: /go and share your LIVE location.")
        else:
            send(chat, "Could not sign you up right now. Try again in a minute.")
    elif cmd in ("/home", "/work"):
        if chat not in subs:
            send(chat, "Send /agree first.")
        else:
            setting[chat] = cmd[1:]
            send(chat, f"Share your {cmd[1:]} location now (paperclip → Location).")
    elif cmd == "/depart":
        r = sub_call(api, chat, "POST", "/api/subscribers/{id}/places", json={"depart_hhmm": arg.strip()})
        send(chat, "Saved." if r is not None and r.is_success else "Use the form /depart 08:30 (after /agree).")
    elif cmd == "/go":
        r = sub_call(api, chat, "POST", "/api/subscribers/{id}/trip", json={"dest": "work"})
        if r is None:
            send(chat, "Send /agree first.")
        elif r.is_success:
            send(chat, "Trip started. Share your LIVE location (paperclip → Location → Share Live Location).")
        else:
            send(chat, "Set your work location first (/work).")
    elif cmd == "/forget":
        r = sub_call(api, chat, "DELETE", "/api/subscribers/{id}")
        subs.pop(chat, None)
        send(chat, "Deleted." if r is not None and r.is_success else "Nothing to delete.")
    else:
        return False
    return True


def handle_location(chat: int, lat: float, lon: float, api: httpx.Client) -> bool:
    """A shared place, or a live-location update during a trip. Returns True if it was used for commute alerts."""
    if chat in setting:
        kind = setting.pop(chat)
        r = sub_call(api, chat, "POST", "/api/subscribers/{id}/places", json={f"{kind}_lat": lat, f"{kind}_lon": lon})
        send(chat, f"{kind.title()} saved." if r is not None and r.is_success
             else "That place is not on the mapped roads of the pilot area, so it was not saved.")
        return True
    if chat in subs:
        r = sub_call(api, chat, "POST", "/api/subscribers/{id}/position", json={"lat": lat, "lon": lon})
        return bool(r is not None and r.is_success)
    return False


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

    if "edited_message" in update and "location" in update["edited_message"]:   # live location keeps arriving here
        m = update["edited_message"]
        handle_location(m["chat"]["id"], m["location"]["latitude"], m["location"]["longitude"], api)
        return
    msg = update.get("message") or {}
    chat = msg.get("chat", {}).get("id")
    if not chat:
        return
    if msg.get("text", "").startswith("/") and commute_command(chat, msg["text"], api):
        return
    if "location" in msg:
        loc = msg["location"]
        if chat in setting or (chat in subs and "live_period" in loc):   # a place to save, or live location on a trip
            handle_location(chat, loc["latitude"], loc["longitude"], api)
            return
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
        send(chat, "Share your 📍 location to report a road problem, send /alerts, or /start for commute alerts.\n"
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
