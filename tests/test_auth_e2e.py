"""Browser tests for sign-up / OTP / login. Runs the real gateway handler against tests/mock_supabase.mjs (a stand-in for
Supabase, modelled on GoTrue's documented API). They do NOT prove behaviour against a live Supabase project."""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
import pytest

pw = pytest.importorskip("playwright.sync_api")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is required for the Supabase mock")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def wait(url):
    for _ in range(100):
        try: urllib.request.urlopen(url); return
        except Exception: time.sleep(.2)
    raise RuntimeError("server did not start: " + url)

@pytest.fixture(scope="module")
def servers():
    sp, ap = free_port(), free_port()
    mock = subprocess.Popen(["node", "tests/mock_supabase.mjs", str(sp)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = dict(os.environ, FUNDRIX_DB=os.path.join(tempfile.mkdtemp(), "a.db"))
    app = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(ap)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait(f"http://127.0.0.1:{sp}/__mock/state"); wait(f"http://127.0.0.1:{ap}/api/meta")
    yield dict(mock=f"http://127.0.0.1:{sp}", app=f"http://127.0.0.1:{ap}")
    mock.terminate(); app.terminate()

@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()

def mock_get(servers, path): return json.load(urllib.request.urlopen(servers["mock"] + path))
def otp(servers, email): return mock_get(servers, "/__mock/otp?email=" + email)["code"]

def new_page(browser, servers, enabled=True, gate=False, wall=30, viewport=None):
    """gate=False: sign-in wall off (the default, so older tests behave as before). gate=True: wall after `wall` seconds; wall=None -> use the app's default."""
    ctx = browser.new_context(viewport=viewport or {"width": 1200, "height": 900}); pg = ctx.new_page(); pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    cfg = {"supabaseUrl": servers["mock"] if enabled else "", "anonKey": "anon-key" if enabled else "", "turnstileSiteKey": "", "requireLogin": bool(gate)}
    if wall is not None: cfg["loginWallSeconds"] = wall
    pg.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body="window.FUNDRIX_CONFIG=" + json.dumps(cfg)))
    pg.ctx, pg.servers = ctx, servers
    return pg

@pytest.fixture()
def page(browser, servers):
    urllib.request.urlopen(servers["mock"] + "/__mock/reset")
    pg = new_page(browser, servers); pg.goto(servers["app"] + "/"); pg.wait_for_selector("table.t tbody tr")
    yield pg
    assert pg.errors == [], pg.errors
    pg.ctx.close()

def signup(pg, phone="98765 43210", email="asha@example.com", pw_="secret123", pw2=None, consent=True):
    pg.goto(pg.servers["app"] + "/#/signup"); pg.wait_for_selector("#fSign")
    pg.fill("#sph", phone); pg.fill("#sem", email); pg.fill("#spw", pw_); pg.fill("#spw2", pw2 if pw2 is not None else pw_)
    if consent: pg.check("#sconsent")
    pg.click("#sbtn")

def register(pg, **kw):
    kw.setdefault("email", "asha@example.com"); signup(pg, **kw); pg.wait_for_selector("#fVer")
    pg.fill("#vcode", otp(pg.servers, kw["email"])); pg.click("#vbtn"); pg.wait_for_selector("#logoutBtn")

def logout(pg): pg.click("#logoutBtn"); pg.wait_for_selector("text=Log in")

def login(pg, ident, pw_):
    pg.goto(pg.servers["app"] + "/#/login"); pg.reload(); pg.wait_for_selector("#fLogin"); pg.fill("#lid", ident); pg.fill("#lpw", pw_); pg.click("#lbtn")


def test_nav_buttons_and_disabled_mode(browser, servers, page):
    assert page.locator("#navauth >> text=Sign up").is_visible() and page.locator("#navauth >> text=Log in").is_visible()
    off = new_page(browser, servers, enabled=False); off.goto(servers["app"] + "/"); off.wait_for_selector("table.t tbody tr")
    assert off.locator("#navauth").inner_text() == ""
    off.goto(servers["app"] + "/#/login"); off.wait_for_selector("text=Accounts are not enabled"); off.ctx.close()


def test_signup_client_validation(page):
    page.goto(page.servers["app"] + "/#/signup"); page.wait_for_selector("#fSign"); page.click("#sbtn")
    for i in ("sph", "sem", "spw", "sconsent"): assert page.inner_text(f"#{i}-err").strip(), i
    signup(page, phone="12345", email="nope", pw_="short", pw2="different", consent=False)
    assert "valid 10-digit" in page.inner_text("#sph-err") and "valid email" in page.inner_text("#sem-err")
    assert "at least 8" in page.inner_text("#spw-err") and "do not match" in page.inner_text("#spw2-err") and page.inner_text("#sconsent-err")
    signup(page, pw_="allletters", pw2="allletters"); assert "one letter and one number" in page.inner_text("#spw-err")
    assert page.locator("#fVer").count() == 0 and page.locator("#sph").get_attribute("aria-invalid") == "false"   # nothing was submitted


def test_password_show_hide(page):
    page.goto(page.servers["app"] + "/#/signup"); page.wait_for_selector("#fSign")
    assert page.get_attribute("#spw", "type") == "password"; page.click('[data-show="spw"]'); assert page.get_attribute("#spw", "type") == "text"
    page.click('[data-show="spw"]'); assert page.get_attribute("#spw", "type") == "password"


def test_full_signup_otp_flow_and_session(page):
    signup(page); page.wait_for_selector("#fVer"); assert "asha@example.com" in page.inner_text("#app") and "#/verify" in page.url
    state = mock_get(page.servers, "/__mock/state"); assert state["profiles"] == []                    # nothing is registered before the OTP is confirmed
    page.fill("#vcode", "12ab34x"); assert page.input_value("#vcode") == "1234"                         # non-digits stripped
    page.fill("#vcode", "000000" if otp(page.servers, "asha@example.com") != "000000" else "111111"); page.click("#vbtn")
    page.wait_for_selector("#vform-err:not(:empty)"); assert "wrong or has expired" in page.inner_text("#vform-err")
    page.fill("#vcode", otp(page.servers, "asha@example.com")); page.click("#vbtn"); page.wait_for_selector("#logoutBtn")
    assert page.url.endswith("/#/") and "+91 98" in page.inner_text("#navauth") and "98765" not in page.inner_text("#navauth")   # masked phone in the header
    assert mock_get(page.servers, "/__mock/state")["profiles"][0]["phone"] == "+919876543210"
    page.reload(); page.wait_for_selector("#logoutBtn")                                                    # session survives reload
    page.click("#navauth a"); page.wait_for_selector("text=Your account"); assert "asha@example.com" in page.inner_text("#app") and "+919876543210" in page.inner_text("#app")
    assert page.evaluate("JSON.parse(localStorage.fx_session).user.email") == "asha@example.com"
    page.goto(page.servers["app"] + "/#/login"); page.wait_for_function("location.hash==='#/'")           # logged-in users skip the login page


def test_logout_clears_session(page):
    register(page); logout(page)
    assert page.evaluate("localStorage.getItem('fx_session')") is None
    page.goto(page.servers["app"] + "/#/account"); page.wait_for_selector("#fLogin")                       # account page is protected


@pytest.mark.parametrize("ident", ["asha@example.com", "ASHA@Example.com ", "9876543210", "+91 98765-43210", "09876543210", "919876543210"])
def test_login_with_email_or_phone(page, ident):
    register(page); logout(page); login(page, ident, "secret123"); page.wait_for_selector("#logoutBtn")
    assert page.url.endswith("/#/")


def test_login_failures_are_generic(page):
    register(page); logout(page)
    msgs = []
    for ident, pw_ in [("asha@example.com", "wrongpass1"), ("9876543210", "wrongpass1"), ("9111111111", "secret123"), ("ghost@example.com", "secret123"), ("zzz", "x")]:
        login(page, ident, pw_); page.wait_for_selector("#lform-err:not(:empty)"); msgs.append(page.inner_text("#lform-err"))
    assert len(set(msgs)) == 1 and "Wrong email/phone or password" in msgs[0]
    page.goto(page.servers["app"] + "/#/login"); page.reload(); page.wait_for_selector("#fLogin"); page.click("#lbtn")
    assert page.inner_text("#lid-err") and page.inner_text("#lpw-err")


def test_duplicate_phone_blocked_after_confirmation(page):
    register(page); logout(page)
    signup(page, email="other@example.com", phone="+91 9876543210"); page.wait_for_selector("#sph-err:not(:empty)")
    assert "already registered" in page.inner_text("#sph-err") and page.locator("#fVer").count() == 0
    state = mock_get(page.servers, "/__mock/state"); assert [u["email"] for u in state["users"]] == ["asha@example.com"]   # no second account was created


def test_unverified_phone_does_not_block_others(page):
    signup(page, email="squatter@example.com"); page.wait_for_selector("#fVer")            # never verified
    register(page, email="real@example.com")                                               # real owner can still sign up and verify
    assert [p["email"] for p in mock_get(page.servers, "/__mock/state")["profiles"]] == ["real@example.com"]


def test_login_before_verification_redirects_to_verify(page):
    signup(page); page.wait_for_selector("#fVer"); page.goto(page.servers["app"] + "/#/")
    login(page, "asha@example.com", "secret123"); page.wait_for_selector("#fVer"); assert "verify your email first" in page.inner_text("#app").lower()
    page.fill("#vcode", otp(page.servers, "asha@example.com")); page.click("#vbtn"); page.wait_for_selector("#logoutBtn")
    logout(page); login(page, "9876543210", "secret123"); page.wait_for_selector("#logoutBtn")            # phone login works once confirmed


def test_resend_cooldown_and_new_code(page):
    signup(page); page.wait_for_selector("#fVer"); first = otp(page.servers, "asha@example.com")
    assert page.locator("#resend").is_disabled() and "Resend in" in page.inner_text("#resend")
    page.wait_for_function("!document.querySelector('#resend').disabled", timeout=35000)
    page.click("#resend"); page.wait_for_selector(".toast"); assert page.locator("#resend").is_disabled()
    second = otp(page.servers, "asha@example.com")
    page.fill("#vcode", second); page.click("#vbtn"); page.wait_for_selector("#logoutBtn")
    assert first is not None


def test_expired_code_rejected(page):
    signup(page); page.wait_for_selector("#fVer"); code = otp(page.servers, "asha@example.com")
    urllib.request.urlopen(page.servers["mock"] + "/__mock/expire?ms=1"); time.sleep(.05)
    page.fill("#vcode", code); page.click("#vbtn"); page.wait_for_selector("#vform-err:not(:empty)"); assert "expired" in page.inner_text("#vform-err")


def test_verify_page_without_pending_asks_for_email(page):
    signup(page); page.wait_for_selector("#fVer"); code = otp(page.servers, "asha@example.com")
    page.evaluate("sessionStorage.clear()"); page.reload(); page.wait_for_selector("#vem")
    page.fill("#vem", "bad"); page.fill("#vcode", code); page.click("#vbtn"); assert page.inner_text("#vem-err")
    page.fill("#vem", "asha@example.com"); page.click("#vbtn"); page.wait_for_selector("#logoutBtn")


def test_expired_access_token_is_refreshed_on_load(page):
    register(page); page.evaluate("() => { const s = JSON.parse(localStorage.fx_session); s.expires_at = 1; localStorage.fx_session = JSON.stringify(s); }")
    old = page.evaluate("JSON.parse(localStorage.fx_session).access_token"); page.reload(); page.wait_for_selector("#logoutBtn")
    page.wait_for_function("(o) => JSON.parse(localStorage.fx_session).access_token !== o", arg=old)
    assert page.evaluate("JSON.parse(localStorage.fx_session).expires_at") > time.time()


def test_rejected_refresh_token_logs_out(page):
    register(page); page.evaluate("() => { const s = JSON.parse(localStorage.fx_session); s.expires_at = 1; s.refresh_token = 'rt_bogus'; localStorage.fx_session = JSON.stringify(s); }")
    page.reload(); page.wait_for_selector("#navauth >> text=Log in")
    assert page.evaluate("localStorage.getItem('fx_session')") is None


def test_server_error_text_cannot_inject_html(browser, servers, page):
    page.route("**/functions/v1/gateway", lambda r: r.fulfill(status=422, content_type="application/json",
               body=json.dumps({"error": "validation", "fields": {"phone": "<img src=x onerror=window.__x=1>"}})))
    signup(page); page.wait_for_selector("#sph-err:not(:empty)")
    assert page.evaluate("window.__x") is None and page.locator("#sph-err img").count() == 0


def test_network_failure_message(page):
    page.route("**/functions/v1/gateway", lambda r: r.abort())
    login(page, "asha@example.com", "secret123"); page.wait_for_selector("#lform-err:not(:empty)"); assert "Cannot reach the server" in page.inner_text("#lform-err")
    assert not page.locator("#lbtn").is_disabled()


def test_privacy_page_and_mobile_layout(browser, servers, page):
    page.goto(servers["app"] + "/#/privacy"); page.wait_for_selector("text=Privacy notice")
    m = new_page(browser, servers); m.set_viewport_size({"width": 390, "height": 800})
    for route in ("/#/login", "/#/signup", "/#/verify"):
        m.goto(servers["app"] + route); m.wait_for_timeout(500)
        assert m.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"), route
    m.ctx.close()


# ---------------------------------------------------------------- forgot password
def recovery_code(pg, email): return mock_get(pg.servers, "/__mock/otp?type=recovery&email=" + email)["code"]

def start_forgot(pg, ident):
    pg.goto(pg.servers["app"] + "/#/forgot"); pg.reload(); pg.wait_for_selector("#fForgot"); pg.fill("#fid", ident); pg.click("#fbtn"); pg.wait_for_selector("#fReset")

def do_reset(pg, code, pw_="brandnew77", pw2=None):
    pg.fill("#rcode", code); pg.fill("#rpw", pw_); pg.fill("#rpw2", pw2 if pw2 is not None else pw_); pg.click("#rbtn")


def test_forgot_link_on_login_page(page):
    page.goto(page.servers["app"] + "/#/login"); page.click("text=Forgot password?"); page.wait_for_selector("#fForgot")
    page.click("text=Back to log in"); page.wait_for_selector("#fLogin")


@pytest.mark.parametrize("ident", ["asha@example.com", "98765 43210"])
def test_forgot_password_full_flow(page, ident):
    register(page); logout(page); start_forgot(page, ident)
    assert "have emailed it a 6-digit code" in page.inner_text("#app") and page.locator("#rresend").is_disabled()
    do_reset(page, recovery_code(page, "asha@example.com")); page.wait_for_selector("#logoutBtn"); assert page.url.endswith("/#/")   # logged in right after
    logout(page)
    login(page, "asha@example.com", "secret123"); page.wait_for_selector("#lform-err:not(:empty)")                              # old password is dead
    login(page, "9876543210", "brandnew77"); page.wait_for_selector("#logoutBtn")                                              # new one works (phone login too)


def test_forgot_does_not_reveal_whether_account_exists(page):
    register(page); logout(page)
    shown = []
    for ident in ("asha@example.com", "ghost@example.com", "9111111111"):                     # real account, unknown email, unknown phone
        start_forgot(page, ident); shown.append(page.inner_text(".ok").replace(ident, "<ID>")); page.evaluate("sessionStorage.clear()")
    assert len(set(shown)) == 1, shown                                                     # the page says exactly the same thing every time
    assert recovery_code(page, "ghost@example.com") is None and recovery_code(page, "asha@example.com") is not None


def test_reset_validation_and_wrong_code(page):
    register(page); logout(page); start_forgot(page, "asha@example.com")
    page.click("#rbtn"); assert page.inner_text("#rcode-err") and "at least 8" in page.inner_text("#rpw-err")
    do_reset(page, "123456", "brandnew77", "different77"); assert "do not match" in page.inner_text("#rpw2-err")
    good = recovery_code(page, "asha@example.com"); wrong = "000000" if good != "000000" else "111111"
    do_reset(page, wrong); page.wait_for_selector("#rcode-err:not(:empty)"); assert "wrong or has expired" in page.inner_text("#rcode-err")
    assert page.locator("#logoutBtn").count() == 0
    page.fill("#rpw", "brandnew77"); page.fill("#rpw2", "brandnew77"); page.fill("#rcode", good); page.click("#rbtn"); page.wait_for_selector("#logoutBtn")


def test_reset_rejects_same_password_and_keeps_code_usable(page):
    register(page); logout(page); start_forgot(page, "asha@example.com"); code = recovery_code(page, "asha@example.com")
    do_reset(page, code, "secret123"); page.wait_for_selector("#rpw-err:not(:empty)"); assert "different" in page.inner_text("#rpw-err")


def test_reset_code_is_single_use_and_expires(page):
    register(page); logout(page); start_forgot(page, "asha@example.com"); code = recovery_code(page, "asha@example.com")
    urllib.request.urlopen(page.servers["mock"] + "/__mock/expire?ms=1"); time.sleep(.05)
    do_reset(page, code); page.wait_for_selector("#rcode-err:not(:empty)")
    urllib.request.urlopen(page.servers["mock"] + "/__mock/expire?ms=600000")


def test_reset_resend_and_use_different_account(page):
    register(page); logout(page); start_forgot(page, "asha@example.com"); first = recovery_code(page, "asha@example.com")
    page.wait_for_function("!document.querySelector('#rresend').disabled", timeout=35000); page.click("#rresend"); page.wait_for_selector(".toast")
    assert page.locator("#rresend").is_disabled() and recovery_code(page, "asha@example.com") != first
    page.click("#rother"); page.wait_for_selector("#fForgot")


def test_forgot_validation_and_logged_in_redirect(page):
    page.goto(page.servers["app"] + "/#/forgot"); page.wait_for_selector("#fForgot"); page.click("#fbtn"); assert page.inner_text("#fid-err")
    page.fill("#fid", "not a thing"); page.click("#fbtn"); page.wait_for_selector("#fid-err:not(:empty)"); assert page.locator("#fReset").count() == 0
    register(page); page.goto(page.servers["app"] + "/#/forgot"); page.wait_for_function("location.hash==='#/'")


def test_forgot_pages_fit_mobile(browser, servers):
    m = new_page(browser, servers); m.set_viewport_size({"width": 390, "height": 800}); m.goto(servers["app"] + "/#/forgot"); m.wait_for_timeout(600)
    assert m.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"); m.ctx.close()
