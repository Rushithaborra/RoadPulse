"""The Telegram bot's commute commands, driven against the real API (no network: Telegram sends are captured)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))


@pytest.fixture()
def bot(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    import telegram_bot
    sent = []
    monkeypatch.setattr(telegram_bot, "send", lambda chat, text, kb=None: sent.append((chat, text)))
    telegram_bot.subs.clear()
    telegram_bot.setting.clear()
    telegram_bot.pending_location.clear()
    return telegram_bot, sent


def say(bot_mod, client, chat, text):
    bot_mod.handle({"message": {"chat": {"id": chat}, "text": text}}, client)


def share(bot_mod, client, chat, node, live=False, edited=False):
    from app.main import S
    lat, lon = S.rg.node_latlon(node)
    loc = {"latitude": lat, "longitude": lon, **({"live_period": 3600} if live else {})}
    key = "edited_message" if edited else "message"
    bot_mod.handle({key: {"chat": {"id": chat}, "location": loc}}, client)


def test_full_commute_flow_over_telegram(bot, client, officer_headers):
    b, sent = bot
    say(b, client, 7, "/start")
    say(b, client, 7, "/home")
    assert sent[-1][1] == "Send /agree first."
    say(b, client, 7, "/agree")
    assert 7 in b.subs
    say(b, client, 7, "/home")
    share(b, client, 7, "n0_-4")
    say(b, client, 7, "/work")
    share(b, client, 7, "n0_4")
    say(b, client, 7, "/depart 08:30")
    assert sent[-1][1] == "Saved."
    say(b, client, 7, "/go")
    assert "Trip started" in sent[-1][1]
    # an accident on the route is verified, then LIVE location updates arrive as edited messages
    from app.main import S
    (alat, alon), (blat, blon) = S.rg.node_latlon("n0_0"), S.rg.node_latlon("n0_1")
    iid = client.post("/api/reports", json={"type": "accident", "lat": (alat + blat) / 2, "lon": (alon + blon) / 2,
                                            "reporter_id": "c"}).json()["incident_id"]
    client.post(f"/api/incidents/{iid}/verify", json={"severity": "Closed"}, headers=officer_headers)
    from conftest import field_login
    client.post(f"/api/field/incidents/{iid}/confirm", json={"severity": "Closed"}, headers=field_login(client))
    share(b, client, 7, "n0_-4", live=True, edited=True)
    sub = b.subs[7]
    alerts = client.get(f"/api/subscribers/{sub['id']}/alerts", headers={"X-Subscriber-Token": sub["token"]}).json()["alerts"]
    assert [a["kind"] for a in alerts] == ["in_trip"]
    say(b, client, 7, "/forget")
    assert sent[-1][1] == "Deleted." and 7 not in b.subs


def test_a_normal_location_still_starts_a_report_when_not_saving_a_place(bot, client):
    b, sent = bot
    share(b, client, 9, "n0_0")
    assert 9 in b.pending_location          # the original reporting flow is untouched
