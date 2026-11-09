"""Compulsory sign-in: 30 s free look, then a centered pop-up that cannot be dismissed; features locked for guests meanwhile."""
import base64, json, time, urllib.request
import pytest
from test_auth_e2e import servers, browser, new_page, register, logout, login, mock_get, otp   # noqa: F401

def reset(sv): urllib.request.urlopen(sv["mock"] + "/__mock/reset")

def guest(browser, sv, wall=120, path="/", **kw):
    reset(sv); pg = new_page(browser, sv, gate=True, wall=wall, **kw); pg.goto(sv["app"] + path); pg.wait_for_selector("table.t tbody tr, .auth-card"); return pg

def prompt_title(pg):
    pg.wait_for_selector("#gate .gate-card"); return pg.inner_text("#gate h2")

def close_prompt(pg): pg.keyboard.press("Escape"); pg.wait_for_selector("#gate", state="detached")

def done(pg):
    assert pg.errors == [], pg.errors; pg.ctx.close()

def settle(pg): pg.wait_for_timeout(330); pg.wait_for_function("!document.querySelector('#tbl.loading')")


# ---------------------------------------------------------------- locked features (before the wall)
def test_guest_cannot_edit_filters(browser, servers):
    pg = guest(browser, servers); before = pg.inner_text("#count"); n = pg.evaluate("S.filters.length")
    assert pg.locator("#panel.locked").count() == 1 and "to edit filters" in pg.inner_text(".lockbar")
    assert pg.locator("#panel .fcard:not([inert])").count() == 0 and pg.locator("#panel [data-a=add]").count() == 1
    pg.click("#panel", position={"x": 150, "y": 300}); assert "edit filters" in prompt_title(pg)                 # touching the panel asks to sign in
    assert pg.locator("#gate [data-gate-close]").count() >= 1                                                    # a feature prompt can be dismissed ...
    close_prompt(pg)
    pg.evaluate("need('filters'); need('filters'); need('compare')"); assert pg.locator("#gate").count() == 1                                    # ... and never stacks
    pg.mouse.click(5, 5); pg.wait_for_selector("#gate", state="detached")                                                                       # clicking outside also dismisses it
    pg.focus(".nmin"); assert not pg.evaluate("document.activeElement && document.activeElement.classList.contains('nmin')")   # inert: cannot even focus
    pg.keyboard.type("9999"); settle(pg)
    assert pg.inner_text("#count") == before and pg.evaluate("S.filters.length") == n and pg.evaluate("S.filters.every(f => f.min === undefined && f.max === undefined)")
    done(pg)

def test_guest_cannot_use_watchlist_compare_or_save(browser, servers):
    pg = guest(browser, servers)
    assert pg.locator("input.cmp").count() == 0 and pg.locator("[data-lock=compare]").count() >= 20 and pg.inner_text("#cmpBadge") == "0"
    pg.locator("[data-star]").first.click(); assert "watchlist" in prompt_title(pg); close_prompt(pg)
    assert pg.locator("[data-star]").first.get_attribute("aria-pressed") == "false" and pg.evaluate("localStorage.getItem('fx_watch')") is None
    pg.locator("[data-lock=compare]").first.click(); assert "compare" in prompt_title(pg); close_prompt(pg)
    pg.click("#bSave"); assert "save screens" in prompt_title(pg); close_prompt(pg)
    assert pg.evaluate("localStorage.getItem('fx_screens')") is None                                              # nothing was stored
    pg.goto(servers["app"] + "/#/fund/160"); pg.wait_for_selector("#wbtn")
    pg.click("#wbtn"); assert "watchlist" in prompt_title(pg); close_prompt(pg)
    pg.click("#cbtn"); assert "compare" in prompt_title(pg); close_prompt(pg)
    assert pg.evaluate("localStorage.getItem('fx_cmp')") is None and pg.inner_text("#cmpBadge") == "0"
    done(pg)

@pytest.mark.parametrize("route,word", [("#/screens", "saved screens"), ("#/watchlist", "watchlist"), ("#/compare/160,161", "compare"),
                                        ("#/screen/preset-elss", "saved screens"), ("#/s/abcdef", "saved screens")])
def test_gated_pages_bounce_guests_to_the_screener(browser, servers, route, word):
    pg = guest(browser, servers); pg.goto(servers["app"] + "/" + route)
    assert word in prompt_title(pg); assert pg.url.endswith("/#/") and pg.locator("table.t").count() == 1
    close_prompt(pg); done(pg)

def test_guest_can_still_look_around(browser, servers):
    """Browsing (not editing filters) stays open during the free look: sort, page, search, columns, fund pages, calculator, export."""
    pg = guest(browser, servers)
    pg.click('th button[data-sort="name"]'); settle(pg); pg.click('#pager [data-p="2"]'); settle(pg); assert pg.inner_text("#count").startswith("Showing 21 - 40")
    pg.fill("#search", "mid"); settle(pg); assert pg.locator("tbody tr").count() > 0
    pg.locator("td.name a").first.click(); pg.wait_for_selector("#navchart svg"); pg.wait_for_selector("#cres .strip")
    pg.goto(servers["app"] + "/"); pg.wait_for_selector("table.t tbody tr")
    with pg.expect_download(): pg.click("#bExport")
    assert pg.locator("#gate").count() == 0; done(pg)

def test_locked_pages_are_marked_in_the_nav(browser, servers):
    pg = guest(browser, servers); assert pg.locator("#nav a.locked").count() == 3 and pg.locator('#nav a[data-r="screener"].locked').count() == 0; done(pg)


# ---------------------------------------------------------------- the wall
def test_wall_appears_after_the_free_look_and_cannot_be_dismissed(browser, servers):
    pg = guest(browser, servers, wall=3)
    assert pg.locator("#gate").count() == 0                                           # still inside the free look
    pg.wait_for_selector("#gate .gate-card", timeout=6000)
    assert prompt_title(pg) == "Sign in to continue" and pg.locator("#gate [data-gate-close]").count() == 0 and pg.locator("#gate a[href='#/login']").count() == 1
    box, vp = pg.locator(".gate-card").bounding_box(), pg.viewport_size                                   # centered in the middle of the screen
    assert abs((box["x"] + box["width"] / 2) - vp["width"] / 2) < 3 and abs((box["y"] + box["height"] / 2) - vp["height"] / 2) < 3
    pg.keyboard.press("Escape"); pg.mouse.click(5, 5); pg.mouse.click(vp["width"] - 5, vp["height"] - 5); assert pg.locator("#gate .gate-card").count() == 1
    assert pg.evaluate("document.querySelector('main').inert && document.querySelector('.top').inert")      # the site behind it cannot be used
    assert pg.evaluate("getComputedStyle(document.body).overflow") == "hidden"
    for _ in range(6): pg.keyboard.press("Tab"); assert pg.evaluate("!!document.activeElement.closest('#gate')")  # focus stays inside
    pg.keyboard.press("Shift+Tab"); assert pg.evaluate("!!document.activeElement.closest('#gate')")
    assert pg.evaluate("document.querySelector('#gate').getAttribute('id')") == "gate" and pg.locator("[role=dialog][aria-modal=true]").count() == 1
    done(pg)

def test_wall_survives_reload_navigation_and_wins_over_a_prompt(browser, servers):
    pg = guest(browser, servers, wall=3); pg.wait_for_selector("#gate .gate-card", timeout=6000)
    pg.reload(); pg.wait_for_selector("#gate .gate-card", timeout=2500)                                    # reloading does not reset the clock
    pg.evaluate("location.hash = '#/fund/160'"); pg.wait_for_selector("#gate .gate-card"); assert pg.locator("#gate .gate-card").count() == 1
    done(pg)
    p2 = guest(browser, servers, wall=3); p2.click("#panel", position={"x": 150, "y": 300}); p2.wait_for_selector("#gate [data-gate-close]")   # a prompt first ...
    p2.wait_for_selector("#gate:not(:has([data-gate-close]))", timeout=6000)                                                                # ... replaced by the wall
    assert prompt_title(p2) == "Sign in to continue"; done(p2)

def test_default_free_look_is_exactly_30_seconds(browser, servers):
    reset(servers); pg = new_page(browser, servers, gate=True, wall=None); pg.clock.install()
    pg.goto(servers["app"] + "/"); pg.wait_for_selector("table.t tbody tr"); assert pg.evaluate("Gate.limitMs") == 30000
    pg.clock.fast_forward(29000); pg.wait_for_timeout(300); assert pg.locator("#gate").count() == 0
    pg.clock.fast_forward(1500); pg.wait_for_selector("#gate .gate-card"); assert prompt_title(pg) == "Sign in to continue"
    done(pg)

def test_wall_fits_a_phone_screen(browser, servers):
    pg = guest(browser, servers, wall=1, viewport={"width": 390, "height": 700}); pg.wait_for_selector("#gate .gate-card", timeout=5000)
    b = pg.locator(".gate-card").bounding_box(); assert b["x"] >= 0 and b["y"] >= 0 and b["x"] + b["width"] <= 390 and b["y"] + b["height"] <= 700
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"); done(pg)


# ---------------------------------------------------------------- signing in
def sign_up_from_wall(pg, email="asha@example.com", phone="98765 43210"):
    pg.click("#gate a[href='#/login']"); pg.wait_for_selector("#fLogin"); assert pg.locator("#gate").count() == 0           # the wall steps aside on login pages
    pg.click("a[href='#/signup']"); pg.wait_for_selector("#fSign")
    pg.fill("#sph", phone); pg.fill("#sem", email); pg.fill("#spw", "secret123"); pg.fill("#spw2", "secret123"); pg.check("#sconsent"); pg.click("#sbtn")
    pg.wait_for_selector("#fVer"); assert pg.locator("#gate").count() == 0
    pg.fill("#vcode", otp(pg.servers, email)); pg.click("#vbtn"); pg.wait_for_selector("#logoutBtn")

def test_signing_in_removes_the_wall_and_returns_to_the_page_you_wanted(browser, servers):
    pg = guest(browser, servers, wall=2, path="/#/fund/160"); pg.wait_for_selector("#gate .gate-card", timeout=5000)
    sign_up_from_wall(pg)
    pg.wait_for_function("location.hash === '#/fund/160'"); assert pg.locator("#gate").count() == 0 and pg.inner_text("h1").strip()
    assert not pg.evaluate("document.querySelector('main').inert")
    pg.wait_for_timeout(2500); assert pg.locator("#gate").count() == 0                                                      # the timer does not fire for signed-in users
    done(pg)

def test_signed_in_users_are_never_locked(browser, servers):
    pg = guest(browser, servers, wall=2); register(pg); pg.wait_for_selector("table.t tbody tr"); pg.wait_for_timeout(3200)
    assert pg.locator("#gate").count() == 0 and pg.locator("#panel.locked").count() == 0 and pg.locator("#nav a.locked").count() == 0
    pg.fill('.fcard[data-key="cagr_3y"] .nmin', "20"); settle(pg); assert pg.evaluate("S.filters.find(f => f.key === 'cagr_3y').min") == 20
    pg.locator("[data-star]").first.click(); pg.wait_for_function("document.querySelector('[data-star]').getAttribute('aria-pressed') === 'true'")
    assert pg.locator("input.cmp").count() > 0 and pg.locator("[data-lock]").count() == 0
    pg.goto(servers["app"] + "/#/screens"); pg.wait_for_selector(".scard"); pg.goto(servers["app"] + "/#/compare"); pg.wait_for_selector("text=Pick at least two funds")
    done(pg)

def test_everything_unlocks_the_moment_you_sign_in(browser, servers):
    pg = guest(browser, servers, wall=120); assert pg.locator("#panel.locked").count() == 1
    register(pg); pg.wait_for_selector("input.cmp"); assert pg.locator("#panel.locked").count() == 0 and pg.locator("#nav a.locked").count() == 0
    done(pg)

def test_logging_out_after_the_free_look_walls_immediately(browser, servers):
    pg = guest(browser, servers, wall=2); register(pg); pg.wait_for_timeout(2600)
    logout(pg); pg.wait_for_selector("#gate .gate-card", timeout=3000); assert prompt_title(pg) == "Sign in to continue"
    done(pg)

def test_logging_in_with_the_wall_up_works_with_phone_and_email(browser, servers):
    pg = guest(browser, servers, wall=2); register(pg); logout(pg)
    pg.wait_for_selector("#gate .gate-card"); pg.click("#gate a[href='#/login']"); pg.wait_for_selector("#fLogin")
    pg.fill("#lid", "9876543210"); pg.fill("#lpw", "secret123"); pg.click("#lbtn"); pg.wait_for_selector("#logoutBtn"); assert pg.locator("#gate").count() == 0
    done(pg)

def test_share_link_opened_by_a_guest_is_opened_after_login(browser, servers):
    reset(servers); owner = new_page(browser, servers); owner.goto(servers["app"] + "/"); owner.wait_for_selector("table.t tbody tr")
    register(owner); owner.fill("#sname", "Shared one"); owner.click("#bSave"); owner.wait_for_selector("#bShare"); link = owner.url; logout(owner)
    pg = new_page(browser, servers, gate=True, wall=120); pg.goto(link); pg.wait_for_selector("#gate .gate-card"); assert "saved screens" in prompt_title(pg); close_prompt(pg)
    login(pg, "asha@example.com", "secret123"); pg.wait_for_selector("#logoutBtn")
    pg.wait_for_function("u => location.href === u", arg=link); pg.wait_for_function("document.querySelector('#sname') && document.querySelector('#sname').value === 'Shared one'")
    done(pg); owner.ctx.close()


# ---------------------------------------------------------------- when the gate must stay off
def test_gate_is_off_when_require_login_is_false(browser, servers):
    reset(servers); pg = new_page(browser, servers, gate=False, wall=1); pg.goto(servers["app"] + "/"); pg.wait_for_selector("table.t tbody tr"); pg.wait_for_timeout(2200)
    assert pg.locator("#gate").count() == 0 and pg.locator("#panel.locked").count() == 0 and pg.locator("input.cmp").count() > 0; done(pg)

def test_gate_is_off_when_accounts_are_not_configured(browser, servers):
    reset(servers); pg = new_page(browser, servers, enabled=False, gate=True, wall=1); pg.goto(servers["app"] + "/"); pg.wait_for_selector("table.t tbody tr"); pg.wait_for_timeout(2200)
    assert pg.locator("#gate").count() == 0 and pg.locator("#panel.locked").count() == 0; done(pg)             # otherwise nobody could ever sign in
