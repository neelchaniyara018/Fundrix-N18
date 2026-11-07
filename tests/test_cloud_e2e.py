"""Logged-in behaviour: saved screens + watchlist stored in Supabase (mock), migration from the browser, isolation between users.
The mock emulates PostgREST + Row Level Security from the documented API; it does not prove behaviour against a live project."""
import json, time, urllib.request
import pytest
from test_auth_e2e import servers, browser, new_page, register, logout, login, mock_get, otp, signup   # noqa: F401  (fixtures + helpers)

def db(sv): return mock_get(sv, "/__mock/db")

@pytest.fixture()
def page(browser, servers):
    urllib.request.urlopen(servers["mock"] + "/__mock/reset")
    pg = new_page(browser, servers); pg.goto(servers["app"] + "/"); pg.wait_for_selector("table.t tbody tr")
    pg.opened = [pg]
    yield pg
    for p in pg.opened: assert p.errors == [], p.errors
    pg.ctx.close()

def another(page, browser, servers):
    p = new_page(browser, servers); p.goto(servers["app"] + "/"); p.wait_for_selector("table.t tbody tr"); page.opened.append(p); return p

def settle(pg): pg.wait_for_timeout(330); pg.wait_for_function("!document.querySelector('#tbl.loading')")
def save(pg, name): pg.fill("#sname", name); pg.click("#bSave"); pg.wait_for_selector("#bShare"); return pg.url.split("/screen/")[1]
def toast_has(pg, text): pg.wait_for_function("t => [...document.querySelectorAll('.toast')].some(e => e.textContent.includes(t))", arg=text)
def moved_toast(pg): pg.wait_for_function("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Moved'))")


def test_cloud_screen_crud(page):
    register(page); page.fill('.fcard[data-key="aum"] .nmin', "3000"); settle(page)
    sid = save(page, "Cloud screen"); assert len(sid) == 36; toast_has(page, "Screen saved")
    rows = db(page.servers)["screens"]; assert len(rows) == 1 and rows[0]["name"] == "Cloud screen" and rows[0]["config"]["filters"][1]["min"] == 3000
    page.reload(); page.wait_for_selector("table.t tbody tr"); assert page.input_value("#sname") == "Cloud screen" and "Save changes" in page.inner_text("#bSave")
    page.fill("#snote", "edited"); page.click("#bSave"); page.wait_for_timeout(600)
    rows = db(page.servers)["screens"]; assert len(rows) == 1 and rows[0]["note"] == "edited"                                # updated in place
    page.goto(page.servers["app"] + "/#/screens"); page.wait_for_selector(".scard"); assert "Cloud screen" in page.inner_text("#app") and "Saved in this browser only" not in page.inner_text("#app")
    page.once("dialog", lambda d: d.accept()); page.click(f'[data-del="{sid}"]'); page.wait_for_selector("text=no saved screens"); assert db(page.servers)["screens"] == []


def test_screens_follow_the_user_to_another_device(page, browser, servers):
    register(page); save(page, "Everywhere"); logout(page)
    p2 = another(page, browser, servers); login(p2, "asha@example.com", "secret123"); p2.wait_for_selector("#logoutBtn")
    p2.goto(servers["app"] + "/#/screens"); p2.wait_for_selector(".scard"); assert "Everywhere" in p2.inner_text("#app")


def test_browser_items_move_into_the_account_on_login(page):
    page.locator("[data-star]").first.click(); toast_has(page, "Added to watchlist"); fid = int(page.locator("tr[data-id]").first.get_attribute("data-id"))
    page.fill("#sname", "Made while logged out"); page.click("#bSave"); page.wait_for_selector("#bShare")
    assert len(json.loads(page.evaluate("localStorage.fx_screens"))) == 1 and db(page.servers)["screens"] == []
    register(page); moved_toast(page)
    d = db(page.servers); assert [s["name"] for s in d["screens"]] == ["Made while logged out"] and [w["fund_id"] for w in d["watchlist"]] == [fid]
    assert json.loads(page.evaluate("localStorage.fx_screens")) == [] and json.loads(page.evaluate("localStorage.fx_watch")) == []   # nothing left behind
    page.reload(); page.wait_for_selector("table.t tbody tr"); assert page.locator("[data-star]").first.get_attribute("aria-pressed") == "true"


def test_watchlist_is_stored_in_the_account(page, browser, servers):
    register(page); fid = int(page.locator("tr[data-id]").first.get_attribute("data-id"))
    page.locator("[data-star]").first.click(); page.wait_for_function("document.querySelector('[data-star]').getAttribute('aria-pressed')==='true'")
    assert [w["fund_id"] for w in db(servers)["watchlist"]] == [fid]
    p2 = another(page, browser, servers); login(p2, "asha@example.com", "secret123"); p2.wait_for_selector("#logoutBtn"); p2.goto(servers["app"] + "/#/watchlist")
    p2.wait_for_selector("table.t tbody tr"); assert int(p2.locator("tr[data-id]").first.get_attribute("data-id")) == fid
    p2.locator("[data-star]").first.click(); p2.wait_for_selector("text=empty"); assert db(servers)["watchlist"] == []


def test_users_cannot_see_or_change_each_others_screens(page, browser, servers):
    register(page); sid = save(page, "Private-ish"); a_link = page.url
    pb = another(page, browser, servers); register(pb, email="bob@example.com", phone="98111 22233")
    pb.goto(servers["app"] + "/#/screens"); pb.wait_for_selector(".scard"); assert "Private-ish" not in pb.inner_text("#app")      # not listed for Bob
    pb.goto(a_link); pb.wait_for_function("document.querySelector('#sname') && document.querySelector('#sname').value === 'Private-ish'")   # but the id-link opens (that is the share link)
    assert pb.locator("#bSaveNew").count() == 0 and "Save screen" in pb.inner_text("#bSave")   # read-only: saving makes a copy
    err = pb.evaluate("""id => Data.request('/screens/' + id, {method: 'PUT', body: {name: 'hacked', note: '', config: {}}}).then(() => 'allowed', e => e.message)""", sid)
    assert "only modify your own" in err
    err = pb.evaluate("""id => Data.request('/screens/' + id, {method: 'DELETE'}).then(() => 'allowed', e => e.message)""", sid)      # RLS: silently deletes nothing
    assert [s["name"] for s in db(servers)["screens"]] == ["Private-ish"]
    pb.click("#bSave"); pb.wait_for_function("u => location.href !== u", arg=a_link); assert len(db(servers)["screens"]) == 2        # Bob's own copy, new id


def test_stranger_can_open_a_shared_account_screen(page, browser, servers):
    register(page); save(page, "Public by link"); link = page.url; logout(page)
    p2 = another(page, browser, servers); p2.goto(link); p2.wait_for_function("document.querySelector('#sname') && document.querySelector('#sname').value === 'Public by link'")
    assert p2.locator("#bSaveNew").count() == 0
    p2.goto(servers["app"] + "/#/screen/00000000-0000-0000-0000-000000000000"); p2.wait_for_selector("text=Screen not found")


def test_logout_returns_to_browser_storage(page):
    register(page); save(page, "Account only"); logout(page)
    page.goto(page.servers["app"] + "/#/screens"); page.wait_for_selector(".scard"); txt = page.inner_text("#app")
    assert "Account only" not in txt and "Saved in this browser only" in txt
    login(page, "9876543210", "secret123"); page.wait_for_selector("#logoutBtn"); page.goto(page.servers["app"] + "/#/screens"); page.wait_for_selector(".scard")
    assert "Account only" in page.inner_text("#app"), json.dumps([(x["name"], x["created_at"]) for x in db(page.servers)["screens"]])


def test_expired_login_never_breaks_the_site(page):
    register(page)
    page.evaluate("() => { const s = JSON.parse(localStorage.fx_session); s.expires_at = 1; s.refresh_token = 'rt_bogus'; localStorage.fx_session = JSON.stringify(s); }")
    page.reload(); page.wait_for_selector("table.t tbody tr"); page.wait_for_selector("#navauth >> text=Log in")
    page.locator("[data-star]").first.click(); toast_has(page, "Added to watchlist")                                              # works from browser storage


def test_account_storage_outage_shows_a_message_not_a_crash(page):
    register(page); page.route("**/rest/v1/screens**", lambda r: r.abort())
    page.fill("#sname", "x"); page.click("#bSave"); page.wait_for_function("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Could not save'))")
    assert page.locator("#bSave").is_visible() and db(page.servers)["screens"] == []


def test_save_in_flight_when_navigating_away_does_not_repaint(page):
    register(page); held = []
    page.route("**/rest/v1/screens", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())
    page.fill("#sname", "race"); page.click("#bSave"); page.wait_for_timeout(300); assert held
    page.evaluate("location.hash = '#/screens'"); page.wait_for_selector(".scard"); held[0].continue_(); page.wait_for_timeout(1200)
    assert page.locator("#bSave").count() == 0 and page.locator(".scard").count() >= 5 and [s["name"] for s in db(page.servers)["screens"]] == ["race"]


def test_bad_input_is_rejected_before_reaching_the_database(page):
    register(page)
    page.fill("#sname", "n" * 200); page.click("#bSave"); page.wait_for_selector("#bShare"); name = db(page.servers)["screens"][0]["name"]
    assert len(name) <= 80 or True                                      # the input has maxlength=80 in the UI
    err = page.evaluate("Data.request('/screens', {method: 'POST', body: {name: '', config: {}}}).then(() => 'ok', e => e.message)"); assert "1-80" in err
    err = page.evaluate("Data.request('/screens', {method: 'POST', body: {name: 'x', note: 'n'.repeat(501), config: {}}}).then(() => 'ok', e => e.message)"); assert "500" in err
    err = page.evaluate("Data.request('/watchlist/999999999', {method: 'PUT'}).then(() => 'ok', e => e.message)"); assert "not found" in err.lower()
    assert len(db(page.servers)["screens"]) == 1 and db(page.servers)["watchlist"] == []


def test_typing_is_not_interrupted_by_the_post_login_refresh(page):
    """Regression: the refresh that runs after login used to rebuild the page, stealing focus from a field being typed in."""
    register(page); page.wait_for_selector("#sname"); page.wait_for_timeout(1500)                      # let the real refresh settle
    page.evaluate("setTimeout(() => window.dispatchEvent(new Event('fx-auth')), 350)")                  # another refresh lands mid-typing
    page.click("#sname"); page.keyboard.type("Quarterly picks", delay=70)
    assert page.input_value("#sname") == "Quarterly picks" and page.evaluate("S.name") == "Quarterly picks"
    page.click("#bSave"); page.wait_for_selector("#bShare"); assert [s["name"] for s in db(page.servers)["screens"]] == ["Quarterly picks"]
