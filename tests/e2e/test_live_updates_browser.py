"""Live updates in a real browser: the public stream pushes changes without a refresh, and both the stream and the
polling apps recover by themselves when the server restarts. (These tests restart the server, so they live alone.)"""
from playwright.sync_api import expect

from .conftest import OFFICER


def test_the_public_stream_pushes_a_new_incident_without_a_refresh(live, pages):
    live.clear_everything()
    page = pages.open("/classic", label="classic")
    expect(page.locator("#conn")).to_contain_text("live", timeout=15000)
    expect(page.locator("#public-incidents")).to_contain_text("No verified disruptions")
    live.sensor([18, 25, 31, 35])
    expect(page.locator("#public-incidents")).to_contain_text("Waterlogging", timeout=15000)         # arrived by server push
    expect(page.locator("#toast")).to_contain_text("New verified alert", timeout=15000)
    live.clear_everything()
    expect(page.locator("#public-incidents")).to_contain_text("No verified disruptions", timeout=15000)
    assert not pages.problems(), pages.problems()


def test_the_officer_stream_reconnects_after_the_server_restarts(live, pages):
    page = pages.open("/classic", label="classic")
    page.locator('.tab[data-view="authority"]').click()
    page.locator("#pass").fill(OFFICER[1])
    page.locator("#btn-login").click()
    expect(page.locator("#dash")).to_be_visible()
    expect(page.locator("#conn")).to_contain_text("live", timeout=15000)

    live.restart(pause=6)                                                                             # the server is gone for a while
    # while it is down the page must say so, and never claim to be live
    # (the banner needs >4 s of silence; by the time the server is back it has been shown and then cleared)
    expect(page.locator("#conn")).to_contain_text("live", timeout=45000)                              # ...and it comes back by itself
    expect(page.locator("#banner")).to_be_hidden(timeout=15000)
    assert page.locator("#dash").is_visible(), "the officer is still signed in"
    live.sensor([18, 25, 31, 35])                                                                     # and updates flow again
    expect(page.locator("#officer-active")).to_contain_text("Waterlogging", timeout=20000)
    live.clear_everything()


def test_the_down_state_is_visible_while_the_server_is_away(live, pages):
    page = pages.open("/classic", label="classic")
    expect(page.locator("#conn")).to_contain_text("live", timeout=15000)
    live.stop()
    try:
        expect(page.locator("#banner")).to_contain_text("Live updates lost", timeout=30000)
        expect(page.locator("#conn")).not_to_contain_text("live ·")
    finally:
        live._spawn()                                                                                  # bring it back for the next test
    expect(page.locator("#banner")).to_be_hidden(timeout=45000)


def test_the_commuter_app_recovers_after_a_restart(live, pages):
    live.clear_everything()
    page = pages.open("/", label="commuter")
    expect(page.locator(".hero.ok")).to_be_visible()
    live.stop()
    try:
        expect(page.locator("#offline")).to_be_visible(timeout=20000)
    finally:
        live._spawn()
    expect(page.locator("#offline")).to_be_hidden(timeout=30000)
    live.sensor([18, 25, 31, 35])
    expect(page.locator(".hero")).to_contain_text("road problem", timeout=15000)                      # live again, no reload
    live.clear_everything()
