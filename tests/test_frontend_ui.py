"""
Frontend tests in a real browser (headless Chrome via the DevTools
protocol) against the running API on the Phase 14 test database (read-only
sessions). Every check compares what the page shows with the API's own
response for the same company / year, so no value is hard-coded.

Skipped when Chrome, websocket-client or the test database is not
available.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT

websocket = pytest.importorskip("websocket")

CHROME = next((p for p in (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "")
    if p and Path(p).exists()), None)
DB = "brsr_phase14_test"
RELIANCE = "C-INE002A01018"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def server():
    if CHROME is None:
        pytest.skip("no Chrome / Edge")
    from tests.test_db_phase14 import pg_available
    if not pg_available():
        pytest.skip("PostgreSQL not reachable")
    port = free_port()
    env = dict(os.environ, BRSR_ANALYTICS_DB_NAME=DB)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn",
                             "app.api.main:app", "--port", str(port)],
                            cwd=PROJECT_ROOT, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if http_json(f"{base}/api/health", 2)["database"] == DB:
                break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.skip("API did not start")
    yield base
    proc.terminate()
    proc.wait(10)


class Browser:
    def __init__(self, tmp):
        self.port = free_port()
        self.proc = subprocess.Popen([
            CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={tmp}", "--window-size=1400,1000",
            "about:blank"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                pages = http_json(f"http://127.0.0.1:{self.port}/json/list")
                page = next(p for p in pages if p["type"] == "page")
                break
            except Exception:
                time.sleep(0.25)
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                              timeout=60)
        self.seq = 0
        self.errors, self.paused = [], []
        self.cmd("Runtime.enable")
        self.cmd("Page.enable")

    def cmd(self, method, params=None):
        self.seq += 1
        mine = self.seq
        self.ws.send(json.dumps({"id": mine, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(msg["params"]["exceptionDetails"])
            elif msg.get("method") == "Fetch.requestPaused":
                self.paused.append(msg["params"])
                self._handle_paused(msg["params"])
            if msg.get("id") == mine:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def _handle_paused(self, p):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": "Fetch.failRequest",
                                 "params": {"requestId": p["requestId"],
                                            "errorReason": "Failed"}}))

    def js(self, expr):
        r = self.cmd("Runtime.evaluate", {"expression": expr,
                                          "returnByValue": True,
                                          "awaitPromise": True})
        if "exceptionDetails" in r:
            raise AssertionError(r["exceptionDetails"])
        return r["result"].get("value")

    def wait(self, expr, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"!!({expr})"):
                return
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for {expr}")

    def go(self, url, ready="!document.querySelector('.spinner')"):
        # Page.navigate returns before the new document replaces the old one:
        # mark the old window and wait until a document without the mark is
        # loaded; the React app renders after load, so then wait for content
        self.js("window.__brsrOldDocument = true")
        self.cmd("Page.navigate", {"url": url})
        self.wait("!window.__brsrOldDocument && document.readyState === 'complete'")
        self.wait("document.getElementById('view') && document.getElementById('view').children.length > 0")
        self.wait(ready)
        # nested sections load independently; wait until none is loading
        self.wait("!document.querySelector('.spinner')")

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.kill()


@pytest.fixture(scope="module")
def browser(server, tmp_path_factory):
    b = Browser(tmp_path_factory.mktemp("chrome"))
    yield b
    b.close()


def digits(text):
    return (text or "").replace(",", "").strip()


def url(server, route):
    """React app URL for a route such as /c/<id>/<fy>/<tab>?<params>."""
    return f"{server}/app{route}"


ROWS = """[...document.querySelectorAll(%s)].map(tr =>
  [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))"""


# Python mirror of unitLabel() in the UI's format module (same table); the
# DOM is compared with it, and the table itself is checked below and in the
# React unit tests.
MEASURES = {
    "J": "J", "kJ": "kJ", "MJ": "MJ", "GJ": "GJ", "TJ": "TJ", "PJ": "PJ",
    "kWh": "kWh", "MWh": "MWh", "GWh": "GWh",
    "tCO2e": "tCO₂e", "ktCO2e": "ktCO₂e", "MtCO2e": "MtCO₂e", "GtCO2e": "GtCO₂e",
    "t": "tonnes", "kt": "kilotonnes", "kg": "kg",
    "kl": "kL", "kL": "kL", "m3": "m³", "l": "litres", "ML": "megalitres",
    "INR": "₹", "count": "count", "%": "%",
}


def _measure(code, filed):
    local = code.strip().split(":")[-1]
    if local == "pure":
        return "no unit stated" if filed else "number"
    return MEASURES.get(local, local)


def label(b, unit, filed=False):
    """Display label for a unit code (b is unused; kept for call sites)."""
    if not unit:
        return ""
    parts = [p.strip() for p in unit.split("/")]
    if len(parts) > 1 and parts[1]:
        return f"{_measure(parts[0], filed)} per {_measure(parts[1], filed)}"
    return _measure(parts[0], filed)


def panel_rows(b, heading):
    sel = json.dumps(f"section.panel")
    return b.js(f"""(() => {{
      const p = [...document.querySelectorAll({sel})]
        .find(s => s.querySelector('h2') && s.querySelector('h2').innerText.trim() === {json.dumps(heading)});
      return p ? [...p.querySelectorAll('tbody tr')].map(tr =>
        [...tr.querySelectorAll('td')].map(td => td.innerText.trim())) : null; }})()""")


# ---- 1. Reliance FY2024-25 energy ------------------------------------------------

def test_reliance_energy_matches_api(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/energy")
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/environmental"))
    rows = panel_rows(browser, "Energy")
    assert rows, "energy panel missing"
    shown = {r[0].split("\n")[0]: r for r in rows}
    for g in api["metrics"]:
        v = next(x for x in g["values"] if not x["breakdown"])
        row = shown[g["metric_name"]]
        if v["value"] is not None:
            assert digits(row[1]) == v["value"], g["requirement_id"]
            assert row[2] == label(browser, v["unit"])
        else:                                   # never a number, never 0
            assert row[1].startswith("—") or "as filed" in row[1]
    # chart drawn only because every energy value is normalised in GJ
    units = {next(x for x in g["values"] if not x["breakdown"])["unit"]
             for g in api["metrics"] if g["expected_unit"] == "energy"}
    assert units == {"GJ"}
    assert browser.js("document.querySelectorAll('.chart rect.bar').length") \
        == sum(1 for g in api["metrics"] if g["expected_unit"] == "energy")


# ---- 2. Reliance FY2024-25 workforce ----------------------------------------------

def test_reliance_workforce_grid_matches_api(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/"
                    "workforce")
    s01 = next(g for g in api["metrics"] if g["requirement_id"] == "S01")
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/social"))
    table = browser.js("""(() => { const p = [...document.querySelectorAll('section.panel')]
      .find(s => s.querySelector('h2').innerText.trim() === %s);
      const head = [...p.querySelectorAll('thead th')].map(t => t.innerText.trim());
      const rows = [...p.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()));
      return {head, rows}; })()""" % json.dumps(s01["metric_name"]))
    head = table["head"]
    assert head[0] == "Category" and "Total" in head
    by_label = {r[0]: r for r in table["rows"]}
    for row in s01["gender_grid"]:
        shown = by_label[row["category_label"]]
        for gender, cell in row["cells"].items():
            assert digits(shown[head.index(gender)]) == cell["value"], \
                (row["category_label"], gender)


# ---- 3. POSH ---------------------------------------------------------------------

def test_posh_section(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/posh")
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/social"))
    text = browser.js("""(() => { const t = [...document.querySelectorAll('.section-title')]
      .find(x => x.innerText.includes('POSH')); return t.nextElementSibling.innerText; })()""")
    for g in api["metrics"]:
        assert g["metric_name"] in text
        for v in g["values"]:
            if v["disclosure_status"] == "REPORTED_ZERO":
                assert "Reported zero" in text
    assert "not reported in the filing and is not derived" in text
    assert "resolved" not in [g["metric_key"] for g in api["metrics"]]


# ---- 4. missing / unavailable metrics ----------------------------------------------

def test_company_year_with_extraction_error(server, browser):
    cos = http_json(f"{server}/api/companies?limit=100")["companies"]
    target = None
    for c in cos:
        ys = http_json(f"{server}/api/companies/{c['company_id']}/years")
        bad = [y for y in ys["years"] if y["status"] == "EXTRACTION_ERROR"]
        if bad:
            target = (c["company_id"], bad[0]["fiscal_year"])
            break
    assert target
    browser.go(url(server, f"/c/{target[0]}/{target[1]}/environmental"))
    text = browser.js("document.getElementById('view').innerText")
    assert "Extraction error" in text
    # no value cell is shown for any metric (all metrics are missing)
    assert browser.js("document.querySelectorAll('td.num:not(.missing)').length") == 0


def test_missing_metrics_never_shown_as_zero(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/metrics")
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/environmental"))
    text = browser.js("document.getElementById('view').innerText")
    for m in api["missing"]:
        if m["pillar"] == "Environmental":
            assert m["metric_name"] in text
            assert m["missing_reason"] in text


# ---- 5. history with a non-normalised year --------------------------------------------

def test_history_skips_non_normalised_year(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/metrics/E07/history")
    normalised = [s for s in api["series"]
                  if s["values"] and s["values"][0]["value"] is not None]
    raw = [s for s in api["series"]
           if s["values"] and s["values"][0]["value"] is None]
    assert raw, "expected at least one non-normalised year"
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/trends?metric=E07&breakdown="))
    assert browser.js("document.querySelectorAll('.chart rect.bar').length") \
        == len(normalised)
    labels = browser.js("[...document.querySelectorAll('.chart .gap-label')].map(t => t.textContent)")
    assert len(labels) == len(api["series"]) - len(normalised)
    assert all("not normalised" in l for l in labels[:len(raw)])
    rows = browser.js(ROWS % json.dumps(".table-wrap tbody tr"))
    raw_row = next(r for r in rows if r[0] == f"FY {raw[0]['fiscal_year']}")
    assert raw_row[1] == "—" and "Not normalised" in raw_row[3]
    assert digits(raw_row[4].split()[0]) == raw[0]["values"][0]["reported_value"]


# ---- 6. comparison -----------------------------------------------------------------------

def test_multi_company_comparison(server, browser):
    cos = http_json(f"{server}/api/companies?limit=100")["companies"][:4]
    ids = ",".join(c["company_id"] for c in cos)
    api = http_json(f"{server}/api/compare?metric=E07&fy=2024-25&companies={ids}")
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/compare?metric=E07&fy=2024-25&companies={ids}"))
    chips = browser.js("[...document.querySelectorAll('.chip')].map(c => c.firstChild.textContent)")
    assert chips == [c["company"]["nse_symbol"] for c in api["companies"]]
    rows = browser.js(ROWS % json.dumps(".table-wrap tbody tr"))
    expected = [(c["company"]["legal_name"], v) for c in api["companies"]
                for y in c["years"] for v in (y["values"] or [None])]
    assert len(rows) == len(expected)
    for row, (name, v) in zip(rows, expected):
        assert row[0].startswith(name)
        if v is None or v["value"] is None:
            assert "Not available" in row[2] or "as filed" in row[2]
        else:
            assert digits(row[2]) == v["value"]
            assert row[3] == label(browser, v["unit"])
    bars = browser.js("document.querySelectorAll('.chart rect.bar').length")
    drawable = sum(1 for _, v in expected if v and v["value"] is not None)
    assert bars == (drawable if api["comparable"] else 0)


# ---- 7. details drawer, search, responsive layout, errors -------------------------------

def test_details_drawer_shows_source(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/metrics")
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/overview"))
    label = browser.js("document.querySelector('button.card .label').innerText")
    browser.js("document.querySelector('button.card').click()")
    browser.wait("document.getElementById('drawer').classList.contains('open')")
    text = browser.js("document.getElementById('drawer').innerText")
    g = next(g for g in api["metrics"] if g["metric_name"] == label)
    v = next(x for x in g["values"] if not x["breakdown"])
    for needle in (v["source"]["filing_id"], v["source"]["context_reference"],
                   v["source"]["concept"], "Value basis", "Disclosure status"):
        assert needle in text
    browser.js("document.querySelector('#drawer .close').click()")


def test_drawer_explains_a_calculated_value(server, browser):
    target = None
    for c in http_json(f"{server}/api/companies?limit=100")["companies"]:
        for y in http_json(f"{server}/api/companies/{c['company_id']}/years")["years"]:
            if y["status"] != "PROCESSED":
                continue
            e = http_json(f"{server}/api/companies/{c['company_id']}/years/"
                          f"{y['fiscal_year']}/energy")
            # the first row of the Energy table is the first value
            first = e["metrics"][0]["values"][0] if e["metrics"] else None
            v = first if first and first["value_basis"] == \
                "CALCULATED_UNIT_CONVERSION" else None
            if v:
                target = (c["company_id"], y["fiscal_year"], v)
                break
        if target:
            break
    assert target
    cid, fy, v = target
    browser.go(url(server, f"/c/{cid}/{fy}/environmental"))
    browser.js("""[...document.querySelectorAll('section.panel')].find(s => s.querySelector('h2').innerText === 'Energy')
      .querySelector(%s).click()""" % json.dumps("tr.clickable"))
    browser.wait("document.getElementById('drawer').classList.contains('open')")
    text = browser.js("document.getElementById('drawer').innerText")
    assert "CALCULATION" in text.upper() and v["calculation"]["formula"] in text
    assert "[object" not in text and "\nnull" not in text
    # navigating closes the drawer
    browser.js("document.querySelector('#tabs a[data-tab=\"overview\"]').click()")
    browser.wait("!document.getElementById('drawer').classList.contains('open')")


def test_search_and_select(server, browser):
    browser.go(url(server, "/"))
    target = http_json(f"{server}/api/companies?limit=100")["companies"][5]
    browser.js(f"""(() => {{ const i = document.querySelector('#company-search input');
      i.value = {json.dumps(target['nse_symbol'])}; i.dispatchEvent(new Event('input')); }})()""")
    browser.wait("document.querySelector('.search-results button')")
    browser.js("document.querySelector('.search-results button').click()")
    browser.wait(f"location.pathname.includes({json.dumps(target['company_id'])})")
    browser.wait("document.querySelector('.identity h1')")
    assert browser.js("document.querySelector('.identity h1').innerText") \
        == target["legal_name"]
    fys = browser.js("[...document.querySelectorAll('#fy-select option')].map(o => o.value)")
    years = http_json(f"{server}/api/companies/{target['company_id']}/years")
    assert sorted(fys) == [y["fiscal_year"] for y in years["years"]]


def test_responsive_layout_has_no_horizontal_overflow(server, browser):
    browser.cmd("Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 2,
                 "mobile": True})
    try:
        for tab in ("overview", "environmental", "social", "trends",
                    "compare"):
            browser.go(url(server, f"/c/{RELIANCE}/2024-25/{tab}"))
            width = browser.js("document.documentElement.scrollWidth")
            assert width <= 390, (tab, width)
    finally:
        browser.cmd("Emulation.clearDeviceMetricsOverride")


def test_invalid_company_and_year(server, browser):
    browser.go(url(server, "/c/NOT-A-COMPANY/2024-25/overview"))
    text = browser.js("document.getElementById('view').innerText")
    assert "Not found" in text and "NOT-A-COMPANY" in text
    browser.go(url(server, f"/c/{RELIANCE}/1999-00/overview"))
    assert "is not available for this company" in browser.js(
        "document.getElementById('view').innerText")


def test_api_failure_shows_error_and_retry(server, browser):
    browser.cmd("Fetch.enable", {"patterns": [{"urlPattern": "*/api/companies/*"}]})
    try:
        browser.go(url(server, f"/c/C-INE467B01029/2024-25/overview"),
                   ready="document.querySelector('.state.error')")
        text = browser.js("document.querySelector('.state.error').innerText")
        assert "not reachable" in text and "Retry" in text
    finally:
        browser.cmd("Fetch.disable")
    browser.js("document.querySelector('.state.error button').click()")
    browser.wait("document.querySelector('.identity h1')")


# ---- stabilisation pass ------------------------------------------------------------

@pytest.mark.parametrize("unit,filed,expected", [
    ("GJ", False, "GJ"), ("Non-SI:MJ", True, "MJ"), ("tCO2e", False, "tCO₂e"),
    ("Non-SI:MtCO2e", True, "MtCO₂e"), ("GJ/INR", False, "GJ per ₹"),
    ("Non-SI:GJ / iso4217:INR", True, "GJ per ₹"), ("INR", False, "₹"),
    ("iso4217:INR", True, "₹"), ("xbrli:pure", True, "no unit stated"),
    ("pure", False, "number"), ("kL", False, "kL"), ("%", False, "%"),
])
def test_unit_display_labels(server, browser, unit, filed, expected):
    assert label(browser, unit, filed) == expected


def test_units_are_displayed_with_their_labels(server, browser):
    """The page shows the display label and keeps the exact unit code in the
    tooltip (energy intensity: GJ/INR -> 'GJ per ₹')."""
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/energy")
    e08 = next(g for g in api["metrics"] if g["metric_key"] == "energy_intensity_per_rupee")
    v = e08["values"][0]
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/environmental"))
    cell = browser.js("""(() => { const tr = [...document.querySelectorAll('tr.clickable')]
      .find(t => t.innerText.startsWith(%s)); const td = tr.querySelectorAll('td');
      return {text: td[2].innerText, title: td[2].title}; })()""" % json.dumps(e08["metric_name"]))
    assert cell["text"] == "GJ per ₹" == label(browser, v["unit"])
    assert cell["title"].startswith(v["unit"])


def test_drawer_keeps_the_exact_filed_unit(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/metrics?metric=E17")
    v = api["metrics"][0]["values"][0]
    assert v["value"] is None and v["reported_unit"]          # not normalised
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/environmental"))
    cell = browser.js("""(() => { const tr = [...document.querySelectorAll('tr.clickable')]
      .find(t => t.innerText.startsWith(%s)); const td = tr.querySelectorAll('td');
      return {value: td[1].innerText, unit: td[2].innerText, title: td[2].title}; })()"""
                      % json.dumps(api["metrics"][0]["metric_name"]))
    assert digits(cell["value"].split("\n")[0]) == v["reported_value"]  # number unchanged
    assert cell["unit"] == label(browser, v["reported_unit"], True)
    assert cell["title"].startswith(v["reported_unit"])
    browser.js("""[...document.querySelectorAll('tr.clickable')]
      .find(t => t.innerText.startsWith(%s)).click()""" % json.dumps(api["metrics"][0]["metric_name"]))
    browser.wait("document.getElementById('drawer').classList.contains('open')")
    text = browser.js("document.getElementById('drawer').innerText")
    assert v["reported_unit"] in text and "Pending review" in text
    browser.js("document.querySelector('#drawer .close').click()")


def years_of(server, cid):
    return {y["fiscal_year"]: y for y in
            http_json(f"{server}/api/companies/{cid}/years")["years"]}


def test_compare_fy_options_cover_all_selected_companies(server, browser):
    cos = [c["company_id"] for c in http_json(f"{server}/api/companies?limit=100")["companies"]]
    yrs = {c: years_of(server, c) for c in cos}
    # a pair where the second company has a year the first one lacks
    pair = next((a, b) for a in cos for b in cos if a != b
                and set(yrs[b]) - set(yrs[a]))
    extra = sorted(set(yrs[pair[1]]) - set(yrs[pair[0]]))[0]
    browser.go(url(server, f"/c/{pair[0]}/{max(yrs[pair[0]])}/compare?metric=E07"
                           f"&fy={extra}&companies={pair[0]},{pair[1]}"))
    opts = browser.js("[...document.querySelectorAll('.controls label')].find(l => l.innerText.startsWith('Financial year'))"
                      ".querySelector('select').options.length")
    union = set(yrs[pair[0]]) | set(yrs[pair[1]])
    assert opts == len(union) + 1                              # + "All years"
    chosen = browser.js("[...document.querySelectorAll('.controls label')].find(l => l.innerText.startsWith('Financial year'))"
                        ".querySelector('select').value")
    assert chosen == extra                                     # not limited to the first company
    note = browser.js("document.querySelector('#view .note') && document.querySelector('#view .note').innerText") or ""
    first_sym = http_json(f"{server}/api/companies/{pair[0]}")["company"]["nse_symbol"]
    assert f"No processed FY {extra} filing for" in note and first_sym in note
    text = browser.js("document.querySelector('.table-wrap').innerText")
    assert "No filing for year" in text


def test_compare_breakdown_options_come_from_the_data(server, browser):
    cos = [c["company_id"] for c in http_json(f"{server}/api/companies?limit=100")["companies"][:3]]
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/compare?metric=S01&fy=2024-25&companies={','.join(cos)}"))
    browser.wait("document.querySelector('.table-wrap')")
    sel = "[...document.querySelectorAll('.controls label')].find(l => l.innerText.startsWith('Breakdown')).querySelector('select')"
    shown = set(browser.js(f"[...{sel}.options].map(o => o.value)"))
    expected = set()
    for c in cos:
        if "2024-25" in years_of(server, c):
            m = http_json(f"{server}/api/companies/{c}/years/2024-25/metrics?metric=S01")["metrics"]
            expected |= {v["breakdown"] for g in m for v in g["values"]}
    assert shown == expected and "" not in shown              # S01 has no total row
    assert browser.js(f"{sel}.value") in expected               # a real breakdown is selected
    # a metric without breakdowns: the selector is disabled
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/compare?metric=E07&fy=2024-25&companies={','.join(cos)}"))
    browser.wait("document.querySelector('.table-wrap')")
    assert browser.js(f"{sel}.disabled") is True
    assert browser.js(f"{sel}.options[0].text") == "No breakdown (single value)"
    assert browser.js("document.querySelector('.controls input[placeholder*=Breakdown]')") is None


def test_trends_breakdown_defaults_and_requests(server, browser):
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/trends?metric=S01"))
    browser.wait("location.search.includes('breakdown=')")
    browser.wait("document.querySelector('.table-wrap')")
    bd = browser.js("new URLSearchParams(location.search).get('breakdown')")
    assert bd != ""                                            # first real breakdown
    api = http_json(f"{server}/api/companies/{RELIANCE}/metrics/S01/history?breakdown="
                    + urllib.request.quote(bd))
    assert any(s["values"] for s in api["series"])
    rows = browser.js(ROWS % json.dumps(".table-wrap tbody tr"))
    assert len(rows) == sum(max(1, len(s["values"])) for s in api["series"])
    # options: one small metric-filtered request per processed year
    reqs = browser.js("performance.getEntriesByType('resource').map(e => e.name)"
                      ".filter(n => n.includes('/companies/%s/years/') && n.includes('/metrics?metric=S01'))" % RELIANCE)
    processed = [y for y in years_of(server, RELIANCE).values() if y["filing"]]
    assert len(set(reqs)) == len(processed)
    # a metric without breakdowns needs no option requests
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/trends?metric=E07"))
    assert browser.js("performance.getEntriesByType('resource').filter(e => e.name.includes('/metrics?metric=E07')).length") == 0


def test_mobile_gender_grid_uses_cards(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/workforce")
    s01 = next(g for g in api["metrics"] if g["requirement_id"] == "S01")
    browser.cmd("Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
    try:
        browser.go(url(server, f"/c/{RELIANCE}/2024-25/social"))
        state = browser.js("""(() => { const p = [...document.querySelectorAll('section.panel')]
          .find(s => s.querySelector('h2').innerText.trim() === %s);
          const table = p.querySelector('.gender-table'), cards = p.querySelector('.gender-cards');
          return {tableShown: getComputedStyle(table).display !== 'none',
                  cardsShown: getComputedStyle(cards).display !== 'none',
                  cards: [...cards.querySelectorAll('.gender-card')].map(c => ({
                    title: c.querySelector('.metric-name').innerText,
                    pairs: [...c.querySelectorAll('dt')].map(dt => [dt.innerText, dt.nextElementSibling.innerText])})),
                  scroll: document.documentElement.scrollWidth}; })()""" % json.dumps(s01["metric_name"]))
        assert state["cardsShown"] and not state["tableShown"]
        assert state["scroll"] <= 390
        cards = {c["title"]: dict(c["pairs"]) for c in state["cards"]}
        for row in s01["gender_grid"]:
            card = cards[row["category_label"]]
            assert list(card)[0] == "Total"                    # total first
            for gender, cell in row["cells"].items():
                assert digits(card[gender]) == cell["value"]
    finally:
        browser.cmd("Emulation.clearDeviceMetricsOverride")


def test_reported_zero_and_missing_are_distinct(server, browser):
    api = http_json(f"{server}/api/companies/{RELIANCE}/years/2024-25/metrics")
    zero = next(v for g in api["metrics"] for v in g["values"]
                if v["disclosure_status"] == "REPORTED_ZERO" and g["pillar"] == "Environmental")
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/environmental"))
    rows = browser.js(ROWS % json.dumps("tr.clickable"))
    row = next(r for r in rows if r[0].split("\n")[0] == zero["metric_name"])
    assert digits(row[1]) == zero["value"] and "Reported zero" in row[3]
    text = browser.js("document.getElementById('view').innerText")
    for m in api["missing"]:
        if m["pillar"] == "Environmental":
            assert m["missing_reason"] in text


# ---- React Router: history URLs, refresh, back/forward, deep links -------------

def http_status(u):
    try:
        with urllib.request.urlopen(u, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), ""


def test_server_returns_the_app_for_client_routes(server):
    for route in ("/", f"/c/{RELIANCE}", f"/c/{RELIANCE}/2024-25/social",
                  f"/c/{RELIANCE}/2024-25/compare?metric=E07&fy=2024-25", "/no/such/page"):
        status, ctype, body = http_status(f"{server}/app{route}")
        assert status == 200 and "text/html" in ctype and '<div id="root">' in body, route
    status, _, _ = http_status(f"{server}/app/assets/missing-file.js")
    assert status == 404                                   # assets never fall back
    status, _, body = http_status(f"{server}/")
    assert status == 200 and '<div id="root">' in body     # / redirects to /app/


def test_previous_ui_is_not_served(server):
    for path in ("/legacy/", "/legacy/js/app.js", "/app/js/app.js", "/app/js/views.js", "/app/styles.css"):
        status, _, _ = http_status(f"{server}{path}")
        assert status == 404, path
    _, _, html = http_status(f"{server}/app/")
    assert "js/app.js" not in html and "styles.css" not in html
    assert "/app/assets/" in html


def test_deep_link_and_refresh_keep_the_screen(server, browser):
    route = f"/c/{RELIANCE}/2024-25/trends?metric=E07&breakdown="
    browser.go(url(server, route))
    browser.wait("document.querySelector('.table-wrap')")
    before = browser.js("location.pathname + location.search")
    assert before == f"/app/c/{RELIANCE}/2024-25/trends?metric=E07&breakdown="
    browser.js("window.__brsrOldDocument = true")
    browser.cmd("Page.reload", {"ignoreCache": True})
    browser.wait("!window.__brsrOldDocument && document.readyState === 'complete' && document.querySelector('.table-wrap')")
    browser.wait("!document.querySelector('.spinner')")
    assert browser.js("location.pathname + location.search") == before
    assert browser.js("document.querySelector('#fy-select').value") == "2024-25"
    assert "Total energy consumption" in browser.js("document.getElementById('view').innerText")


def test_back_and_forward_navigation(server, browser):
    browser.go(url(server, f"/c/{RELIANCE}/2024-25/overview"))
    browser.js("document.querySelector('#tabs a[data-tab=\"social\"]').click()")
    browser.wait("location.pathname.endsWith('/social') && !document.querySelector('.spinner')")
    browser.js("document.querySelector('#fy-select').value = '2023-24';"
               "document.querySelector('#fy-select').dispatchEvent(new Event('change', {bubbles: true}))")
    browser.wait("location.pathname.endsWith('/2023-24/social') && !document.querySelector('.spinner')")
    browser.js("history.back()")
    browser.wait("location.pathname.endsWith('/2024-25/social') && !document.querySelector('.spinner')")
    assert browser.js("document.querySelector('#fy-select').value") == "2024-25"
    browser.js("history.back()")
    browser.wait("location.pathname.endsWith('/2024-25/overview') && document.querySelector('.identity h1')")
    browser.js("history.forward()")
    browser.wait("location.pathname.endsWith('/2024-25/social') && !document.querySelector('.spinner')")
    assert browser.js("document.querySelector('#tabs a.active').dataset.tab") == "social"


def test_company_link_resolves_latest_year(server, browser):
    years = [y for y in http_json(f"{server}/api/companies/{RELIANCE}/years")["years"] if y["status"] == "PROCESSED"]
    browser.go(url(server, f"/c/{RELIANCE}"))
    browser.wait("location.pathname.endsWith('/overview')")
    assert browser.js("location.pathname") == f"/app/c/{RELIANCE}/{years[-1]['fiscal_year']}/overview"


def test_unknown_routes_show_not_found(server, browser):
    for route in (f"/c/{RELIANCE}/2024-25/nonsense", "/no/such/page"):
        browser.go(url(server, route))
        assert "Page not found" in browser.js("document.getElementById('view').innerText"), route


def test_no_javascript_errors(browser):
    assert browser.errors == []
