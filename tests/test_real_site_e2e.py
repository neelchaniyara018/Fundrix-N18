"""With real data the site must carry no 'Demo dataset' banner; with generated demo data it must still say so (safeguard)."""
import os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
import pytest
pw = pytest.importorskip("playwright.sync_api")
import test_pipeline as tp                                   # synthetic files in AMFI's layout
from pipeline import build

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def serve(directory):
    port = free_port(); p = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "-d", directory], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html"); break
        except Exception: time.sleep(.1)
    return p, f"http://127.0.0.1:{port}"

def site(tmp, mode):
    dst = os.path.join(tmp, mode); shutil.copytree(os.path.join(ROOT, "frontend"), dst, ignore=shutil.ignore_patterns("data"))
    if mode == "real":
        (open(os.path.join(tmp, "n.txt"), "w")).write(tp.navall(tp.UNI, tp.PATHS, tp.TODAY))
        build.run_real(os.path.join(dst, "data"), os.path.join(tmp, "state"), from_file=os.path.join(tmp, "n.txt"), backfill_weeks=530, pause=0, min_records=10, fetch_day=tp.fake_fetch_day)
    else:
        build.run_demo(os.path.join(dst, "data"))
    return dst

@pytest.fixture(scope="module")
def sites():
    tmp = tempfile.mkdtemp(); procs = []; urls = {}
    for mode in ("real", "demo"):
        p, url = serve(site(tmp, mode)); procs.append(p); urls[mode] = url
    yield urls
    for p in procs: p.terminate()

@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()

def open_page(browser, url, path="/", size=None):
    pg = browser.new_page(viewport=size or {"width": 1280, "height": 800}); pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e))); pg.goto(url + path); pg.wait_for_selector("table.t tbody tr, h1"); return pg

def test_real_data_has_no_demo_banner_anywhere(browser, sites):
    pg = open_page(browser, sites["real"])
    assert not pg.locator("#demo").is_visible() and pg.inner_text("#demo") == ""
    assert "demo" not in pg.inner_text("body").lower() and "generated for illustration" not in pg.content()
    assert pg.locator(".top").bounding_box()["y"] < 2                                                # the header is now the very first thing on the page
    for route in ("/#/fund/100001", "/#/login", "/#/privacy"):
        pg.goto(sites["real"] + route); pg.wait_for_timeout(500); assert "demo dataset" not in pg.inner_text("body").lower(), route
    assert pg.errors == []; pg.close()

def test_real_data_shows_source_and_disclaimer_in_a_footer(browser, sites):
    pg = open_page(browser, sites["real"]); f = pg.inner_text("#foot")
    assert pg.locator("#foot").is_visible() and "AMFI India" in f and "03 Nov 2026" in f and "not investment advice" in f.lower()
    assert "AUM, expense ratio and holdings are not included yet" in f                                # honest about what is missing
    assert pg.locator("#foot").bounding_box()["y"] > pg.locator("table.t").bounding_box()["y"]       # it sits below the content, not at the top
    pg.close()

def test_demo_data_is_still_labelled(browser, sites):                                              # safeguard: generated numbers must never look real
    pg = open_page(browser, sites["demo"]); assert pg.locator("#demo").is_visible() and "Demo dataset" in pg.inner_text("#demo")
    assert "Demo data" in pg.inner_text("#foot"); pg.close()

def test_real_site_works_end_to_end_without_aum(browser, sites):
    pg = open_page(browser, sites["real"]); assert pg.evaluate("S.sort_key") == "cagr_3y" and "AUM" not in pg.inner_text("table.t thead")
    pg.locator("td.name a").first.click(); pg.wait_for_selector("#navchart svg"); pg.wait_for_selector("#cres .strip")
    assert "Not available" in pg.inner_text("#app") and pg.errors == []; pg.close()

def test_footer_does_not_overflow_on_phones(browser, sites):
    pg = open_page(browser, sites["real"], size={"width": 390, "height": 800}); assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"); pg.close()
