"""Browser end-to-end tests (Playwright + Chromium). Skipped automatically if Playwright is not installed.
Run:  pip install playwright && playwright install chromium && pytest tests/test_e2e.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
import pytest

pw = pytest.importorskip("playwright.sync_api")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def base():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, FUNDRIX_DB=os.path.join(tempfile.mkdtemp(), "e2e.db"))
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port)], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try: urllib.request.urlopen(url + "/api/meta"); break
        except Exception: time.sleep(.3)
    else: p.kill(); raise RuntimeError("server did not start")
    yield url
    p.terminate()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()


@pytest.fixture()
def page(browser, base):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    pg = ctx.new_page(); pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)  # 4xx are expected API validation errors, surfaced in the UI
    pg.base = base
    pg.goto(base + "/"); pg.wait_for_selector("table.t tbody tr")
    yield pg
    assert pg.errors == [], pg.errors
    ctx.close()


def total(pg):
    t = pg.inner_text("#count").replace(",", "")
    return int(t.split("of")[1].split()[0]) if "of" in t else 0

def settle(pg):
    pg.wait_for_timeout(330)                      # UI debounces filter input by 250 ms
    pg.wait_for_function("!document.querySelector('#tbl.loading')"); pg.wait_for_timeout(60)

def col(pg, header):
    return pg.evaluate("""h => { const ths=[...document.querySelectorAll('table.t thead th')]; const i=ths.findIndex(t=>t.innerText.includes(h));
        return [...document.querySelectorAll('table.t tbody tr')].map(r=>r.children[i].innerText.trim()); }""", header)

def num(s): return float(s.replace(",", ""))

def card(pg, key): return pg.locator(f'.fcard[data-key="{key}"]')


def test_initial_state(page):
    assert page.inner_text("#count").startswith("Showing 1 - 20 of")
    assert "4 filters applied" in page.inner_text(".filters-head")
    heads = page.inner_text("table.t thead")
    for h in ("Sub Category", "Plan", "AUM", "CAGR 3Y", "Expense Ratio"): assert h in heads
    aums = [num(x) for x in col(page, "AUM")]
    assert aums == sorted(aums, reverse=True)
    assert "Demo dataset" in page.inner_text("#demo")


def test_numeric_filter_via_inputs(page):
    t0 = total(page)
    page.fill('.fcard[data-key="cagr_3y"] .nmin', "20"); settle(page)
    t1 = total(page); assert 0 < t1 < t0
    assert all(num(v) >= 20 for v in col(page, "CAGR 3Y"))
    page.fill('.fcard[data-key="cagr_3y"] .nmax', "25"); settle(page)
    assert all(20 <= num(v) <= 25 for v in col(page, "CAGR 3Y"))
    page.click('.fcard[data-key="cagr_3y"] [data-a=reset]'); settle(page)
    assert total(page) == t0


def test_slider_drag_updates_results(page):
    t0 = total(page)
    lo = page.locator('.fcard[data-key="expense_ratio"] input.hi')
    lo.evaluate("el => { el.value = 0.8; el.dispatchEvent(new Event('input', {bubbles:true})); }"); settle(page)
    assert total(page) < t0 and all(num(v) <= 0.8 for v in col(page, "Expense Ratio"))
    assert page.input_value('.fcard[data-key="expense_ratio"] .nmax') == "0.8"


def test_empty_state_and_invalid_range(page):
    page.fill('.fcard[data-key="cagr_3y"] .nmin', "1000"); settle(page)
    assert "No funds match" in page.inner_text("#tbl") and page.inner_text("#count") == "No results"
    # typing a min above the max pushes the max up (never an error state)
    assert page.input_value('.fcard[data-key="cagr_3y"] .nmax') == "1000"
    page.fill('.fcard[data-key="cagr_3y"] .nmin', "30"); page.fill('.fcard[data-key="cagr_3y"] .nmax', "10"); settle(page)
    assert page.input_value('.fcard[data-key="cagr_3y"] .nmin') == "10" and "Could not load" not in page.inner_text("#tbl")


def test_categorical_filters(page):
    page.uncheck('.fcard[data-key="plan"] input[value=Growth]'); page.check('.fcard[data-key="plan"] input[value=IDCW]'); settle(page)
    assert set(col(page, "Plan")) == {"IDCW"}
    page.click('.fcard[data-key="plan"] [data-a=remove]'); settle(page)
    assert "3 filters applied" in page.inner_text(".filters-head") and page.locator('.fcard[data-key="plan"]').count() == 0
    assert set(col(page, "Plan")) <= {"Growth", "IDCW"}


def test_add_category_and_subcategory_filter(page):
    page.click("[data-a=add]"); page.fill("#msearch", "sub category"); page.click('.mitem[data-k="sub_category"]'); settle(page)
    c = card(page, "sub_category"); assert c.count() == 1
    c.locator(".csearch").fill("mid cap")
    assert c.locator("label:not(.hidden)").count() < 10
    c.locator('input[value="Mid Cap Fund"]').check(); settle(page)
    assert set(col(page, "Sub Category")) == {"Mid Cap Fund"}


def test_add_and_remove_metric_filter(page):
    page.click("[data-a=add]"); page.fill("#msearch", "sharpe"); page.click('.mitem[data-k="sharpe"]'); settle(page)
    assert card(page, "sharpe").count() == 1 and "Sharpe Ratio" in page.inner_text("table.t thead")
    page.click("[data-a=add]"); assert page.locator('.mitem[data-k="sharpe"]').is_disabled(); page.keyboard.press("Escape")
    page.fill('.fcard[data-key="sharpe"] .nmin', "0.5"); settle(page)
    assert all(num(v) >= .5 for v in col(page, "Sharpe"))
    page.click('.fcard[data-key="sharpe"] [data-a=remove]'); settle(page)
    assert "Sharpe Ratio" not in page.inner_text("table.t thead")


def test_custom_filter(page):
    page.click("[data-a=custom]")
    page.fill("#cname", "Ret per risk"); page.fill("#cexpr", "cagr_3y / std_dev")
    page.click("#cval"); page.wait_for_selector("#cmsg.ok"); assert "Valid" in page.inner_text("#cmsg")
    page.click("#cadd"); settle(page)
    assert card(page, "c:Ret per risk").count() == 1 and "ƒ Ret per risk" in page.inner_text("table.t thead")
    assert col(page, "Ret per risk")
    page.fill('.fcard[data-key="c:Ret per risk"] .nmin', "1.2"); settle(page)
    assert all(num(v) >= 1.2 for v in col(page, "Ret per risk"))
    page.click('.fcard[data-key="c:Ret per risk"] [data-a=remove]'); settle(page)
    assert "Ret per risk" not in page.inner_text("table.t thead")


@pytest.mark.parametrize("expr,msg", [("__import__('os')", "unexpected"), ("nope + 1", "Unknown metric"), ("cagr_3y +", "Syntax"), ("", "empty")])
def test_custom_filter_errors(page, expr, msg):
    page.click("[data-a=custom]"); page.fill("#cname", "bad"); page.fill("#cexpr", expr); page.click("#cadd")
    page.wait_for_selector("#cmsg.err"); assert msg.lower() in page.inner_text("#cmsg").lower()
    page.keyboard.press("Escape"); assert card(page, "c:bad").count() == 0


def test_sorting_and_pagination(page):
    page.click('th button[data-sort="name"]'); settle(page)
    names = [n.split(".", 1)[-1].strip().lower() for n in page.eval_on_selector_all("td.name a", "e=>e.map(x=>x.innerText)")]
    assert names == sorted(names)
    page.click('th button[data-sort="name"]'); settle(page)
    names = page.eval_on_selector_all("td.name a", "e=>e.map(x=>x.innerText.toLowerCase())"); assert names == sorted(names, reverse=True)
    page.click('th button[data-sort="cagr_3y"]'); settle(page)
    v = [num(x) for x in col(page, "CAGR 3Y") if x != "–"]; assert v == sorted(v, reverse=True)
    page.click('#pager [data-p="2"]'); settle(page)
    assert page.inner_text("#count").startswith("Showing 21 - 40")
    page.select_option("#psize", "50"); settle(page)
    assert page.inner_text("#count").startswith("Showing 1 - 50") and page.locator("table.t tbody tr").count() == 50
    last = page.locator("#pager button[data-p]").nth(-2); last.click(); settle(page)   # last numbered page
    assert page.locator("table.t tbody tr").count() <= 50 and page.locator("#pager button[aria-label='Next page']").is_disabled()


def test_search(page):
    page.fill("#search", "liquid"); settle(page); page.wait_for_timeout(350); settle(page)
    names = page.eval_on_selector_all("td.name a", "e=>e.map(x=>x.innerText.toLowerCase())")
    assert names and all("liquid" in n for n in names)
    page.fill("#search", "qqqqzzzz"); page.wait_for_timeout(400); settle(page)
    assert "No funds match" in page.inner_text("#tbl")


def test_columns_popover(page):
    page.click("#bCols"); page.check('.pop input[value="beta"]'); settle(page)
    assert "Beta" in page.inner_text("table.t thead")
    assert page.locator('.pop input[value="aum"]').is_disabled()
    page.uncheck('.pop input[value="beta"]'); settle(page); assert "Beta" not in page.inner_text("table.t thead")


def test_reset_all(page):
    page.fill('.fcard[data-key="aum"] .nmin', "5000"); settle(page); t = total(page)
    page.click("[data-a=resetall]"); settle(page); assert total(page) > t and "4 filters applied" in page.inner_text(".filters-head")


def test_watchlist(page):
    first = page.locator("td.name a").first.inner_text()
    page.locator("[data-star]").first.click(); page.wait_for_selector(".toast")
    assert page.locator("[data-star]").first.get_attribute("aria-pressed") == "true"
    page.select_option("#univ", "watchlist"); settle(page)
    assert total(page) == 1 and page.locator("td.name a").first.inner_text() == first
    page.goto(page.base + "/#/watchlist"); page.wait_for_selector("table.t tbody tr"); assert total(page) == 1
    page.locator("[data-star]").first.click(); settle(page)
    assert "watchlist is empty" in page.inner_text("#tbl")
    page.goto(page.base + "/#/"); page.wait_for_selector("table.t tbody tr"); assert total(page) > 100


def test_compare_flow(page):
    boxes = page.locator("input.cmp"); boxes.nth(0).check()
    assert page.locator("#tray [data-go]").is_disabled()
    boxes.nth(1).check(); boxes.nth(2).check(); boxes.nth(3).check()
    assert page.inner_text("#cmpBadge") == "4"
    boxes.nth(4).click(); page.wait_for_selector(".toast"); assert not boxes.nth(4).is_checked()
    page.click("#tray [data-go]"); page.wait_for_selector(".cmp-table table")
    assert page.locator(".cmp-table thead th").count() == 5 and page.locator("#cchart polyline").count() == 4
    assert page.locator(".cmp-table td.best").count() > 5
    page.click("[data-rm]"); page.wait_for_function("document.querySelectorAll('.cmp-table thead th').length===4")
    page.click("#cclear"); page.wait_for_selector("text=Pick at least two funds")


def test_fund_page(page):
    page.locator("td.name a").first.click(); page.wait_for_selector("#navchart svg"); page.wait_for_selector("#cres .strip")
    assert page.locator("h1").inner_text()
    before = page.inner_html("#navchart")
    page.click('#ranges [data-r="1Y"]'); assert page.inner_html("#navchart") != before and "over 1Y" in page.inner_text("#navchart")
    page.hover("#navchart svg", position={"x": 400, "y": 100}); assert "2026" in page.eval_on_selector("#navchart .tip", "e=>e.textContent") or "202" in page.eval_on_selector("#navchart .tip", "e=>e.textContent")
    page.click('.seg [data-m="lumpsum"]'); page.wait_for_function("document.querySelector('#cres').innerText.includes('CAGR')")
    page.fill("#camt", "-5"); page.wait_for_selector("#cres .err")
    page.fill("#camt", "50000"); page.wait_for_selector("#cres .strip")
    assert "₹50,000" in page.inner_text("#cres")
    page.click("#wbtn"); page.wait_for_selector("text=In watchlist"); page.click("#cbtn"); assert "Added to compare" in page.inner_text("#cbtn")
    page.goto(page.base + "/#/fund/999999"); page.wait_for_selector("text=Something went wrong")
    page.goto(page.base + "/#/fund/abc"); page.wait_for_selector("text=Something went wrong")


def test_save_share_and_xss(page, browser):
    """Logged-out users: screens live in this browser; the share link carries the whole screen inside the URL."""
    evil = '<img src=x onerror="window.__xss=1">'
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.fill("#sname", evil); page.fill("#snote", "<script>window.__xss=2</script>")
    page.fill('.fcard[data-key="aum"] .nmin', "2000"); settle(page); t = total(page)
    page.click("#bSave"); page.wait_for_selector("#bShare"); assert "#/screen/l_" in page.url
    sid = page.url.split("/screen/")[1]; assert page.input_value("#sname") == evil
    assert "this browser" in page.inner_text(".toast")
    page.fill("#snote", "updated"); page.click("#bSave"); page.wait_for_selector(".toast")                       # update in place, not a duplicate
    saved = json.loads(page.evaluate("localStorage.fx_screens")); assert len(saved) == 1 and saved[0]["note"] == "updated" and saved[0]["id"] == sid
    page.goto(page.base + "/#/screens"); page.wait_for_selector(".scard")
    assert page.evaluate("window.__xss") is None and page.locator(".scard img").count() == 0 and evil in page.inner_text("#app")
    page.locator("[data-copy]").first.click(); link = page.evaluate("navigator.clipboard.readText()"); assert "#/s/" in link
    ctx2 = browser.new_context(); p2 = ctx2.new_page(); p2.goto(link); p2.wait_for_selector("table.t tbody tr")      # a stranger's browser, nothing stored there
    assert total(p2) == t and p2.input_value("#sname") == evil and p2.locator("#bSaveNew").count() == 0 and p2.evaluate("window.__xss") is None
    ctx2.close()
    page.once("dialog", lambda d: d.accept()); page.click(f'[data-del="{sid}"]'); page.wait_for_selector("text=no saved screens")
    assert json.loads(page.evaluate("localStorage.fx_screens")) == []


def test_share_link_is_untrusted_input(page):
    import base64
    def link(obj): return page.base + "/#/s/" + base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    cfg = {"filters": [{"key": "__proto__", "min": 1}, {"key": "nope", "values": ["x"]}, {"key": "cagr_3y", "min": 10}, {"key": "aum", "min": "evil"}],
           "custom": [{"name": "<b>", "expr": "aum"}, {"name": "ok", "expr": "__import__('os')"}], "sort_key": "zzz", "extra_cols": ["beta", "nope"], "universe": "ids", "search": "x" * 500}
    page.goto(link({"n": "N" * 200, "t": "<script>window.__xss=1</script>", "c": cfg})); page.wait_for_selector("#tbl .empty, table.t tbody tr")      # the 100-x search matches nothing: fine
    assert len(page.input_value("#sname")) == 80 and page.evaluate("window.__xss") is None
    assert "2 filters applied" in page.inner_text(".filters-head")                                                   # only cagr_3y and aum (range dropped) survived
    assert page.locator('.fcard[data-key="cagr_3y"]').count() == 1 and page.locator('.fcard[data-key="nope"]').count() == 0
    assert len(page.input_value("#search")) <= 100
    page.goto(page.base + "/#/s/%%%not-base64"); page.wait_for_selector("text=Something went wrong")
    page.goto(page.base + "/#/s/" + "A" * 13000); page.wait_for_selector("text=too long")


def test_whole_site_works_without_any_server_api(page):
    """The site is static: browsing, searching, fund pages, compare and export never call an /api server."""
    calls = []; page.on("request", lambda r: calls.append(r.url) if "/api/" in r.url else None)
    page.fill("#search", "mid"); settle(page); page.locator("td.name a").first.click(); page.wait_for_selector("#navchart svg"); page.wait_for_selector("#cres .strip")
    page.goto(page.base + "/#/compare/160,161"); page.wait_for_selector(".cmp-table table")
    page.goto(page.base + "/#/"); page.wait_for_selector("table.t tbody tr")
    with page.expect_download(): page.click("#bExport")
    assert calls == [], calls


def test_watchlist_and_compare_persist_in_the_browser(page):
    first = page.locator("td.name a").first.inner_text(); page.locator("[data-star]").first.click(); page.wait_for_selector(".toast")
    page.locator("input.cmp").nth(0).check(); page.locator("input.cmp").nth(1).check()
    page.reload(); page.wait_for_selector("table.t tbody tr")
    assert page.locator("[data-star]").first.get_attribute("aria-pressed") == "true" and page.inner_text("#cmpBadge") == "2"
    page.select_option("#univ", "watchlist"); settle(page); assert page.locator("td.name a").first.inner_text() == first


def test_navigating_away_while_fund_page_loads_is_safe(page):
    held = []
    page.route("**/data/nav/*", lambda r: held.append(r))                                  # keep the NAV download pending
    page.evaluate("location.hash = '#/fund/160'"); page.wait_for_timeout(300); assert held
    page.evaluate("location.hash = '#/screens'"); page.wait_for_selector(".scard")
    for r in held: r.continue_()
    page.wait_for_timeout(800)
    assert page.locator(".scard").count() >= 5 and page.locator("#navchart").count() == 0   # screens page not overwritten, no JS error (fixture checks)


def test_presets_open(page):
    page.goto(page.base + "/#/screens"); page.wait_for_selector(".scard")
    page.locator('.scard a:has-text("Open")').first.click(); page.wait_for_selector("table.t tbody tr")
    assert "filters applied" in page.inner_text(".filters-head") and total(page) > 0


def test_export_csv(page):
    t = total(page)
    with page.expect_download() as d: page.click("#bExport")
    lines = open(d.value.path(), encoding="utf-8").read().strip().splitlines()
    assert len(lines) == t + 1 and lines[0].startswith("Name,Sub Category,Plan,AUM (Cr)")


def test_theme_toggle(page):
    page.click("#theme"); a = page.evaluate("document.documentElement.dataset.theme")
    bg1 = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.click("#theme"); assert page.evaluate("document.documentElement.dataset.theme") != a
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") != bg1


def test_mobile_layout(browser, base):
    ctx = browser.new_context(viewport={"width": 390, "height": 800}); pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    for route in ("/", "/#/fund/160", "/#/screens", "/#/compare/160,161"):
        pg.goto(base + route); pg.wait_for_timeout(900)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"), route
    assert errs == []; ctx.close()


def test_mobile_filter_toggle(browser, base):
    ctx = browser.new_context(viewport={"width": 390, "height": 800}); pg = ctx.new_page(); pg.goto(base + "/"); pg.wait_for_selector("table.t tbody tr")
    assert not pg.locator("#panel").is_visible() and pg.locator("#bFilt").is_visible()
    pg.click("#bFilt"); assert pg.locator("#panel").is_visible()
    pg.click("#bFilt"); assert not pg.locator("#panel").is_visible()
    ctx.close()


def test_add_filter_modal_footer_per_tab(page):
    page.click("[data-a=add]"); assert not page.locator("#cadd").is_visible()
    page.click('[data-t="custom"]'); assert page.locator("#cadd").is_visible() and page.locator("#cval").is_visible()
    page.click('[data-t="metrics"]'); assert not page.locator("#cadd").is_visible()


