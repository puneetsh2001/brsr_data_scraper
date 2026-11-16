// App shell: hash router, company search, FY selector and tabs.
// Route: #/c/<company_id>/<fy>/<tab>?<params>

import { api } from "./api.js";
import { closeDrawer, empty, errorState, loading } from "./components.js";
import { clear, h } from "./dom.js";
import { fy } from "./format.js";
import * as views from "./views.js";

const TABS = [
  ["overview", "Overview", views.overview],
  ["environmental", "Environmental", views.environmental],
  ["social", "Social", views.social],
  ["governance", "Governance", views.governance],
  ["trends", "Trends", views.trends],
  ["compare", "Compare", views.compare],
  ["quality", "Data quality", views.quality],
];

const view = document.getElementById("view");
const fySelect = document.getElementById("fy-select");
const tabsEl = document.getElementById("tabs");
const state = { catalog: null, company: null, year: null };

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const [kind, id, fyLabel, tab] = path.split("/").map(decodeURIComponent);
  return { kind, id, fy: fyLabel, tab: tab || "overview", params: new URLSearchParams(qs || "") };
}

function routeTo(id, fyLabel, tab, params) {
  const q = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null)).toString() : "";
  location.hash = `#/c/${encodeURIComponent(id)}/${encodeURIComponent(fyLabel)}/${tab}${q ? "?" + q : ""}`;
}

// ---------------------------------------------------------------- search
function buildSearch() {
  const box = document.getElementById("company-search");
  const input = h("input", { type: "search", placeholder: "Search company, NSE symbol, ISIN, BSE code or CIN…",
    "aria-label": "Search company", autocomplete: "off" });
  const results = h("div", { class: "search-results", hidden: true, role: "listbox" });
  box.append(input, results);
  let timer, seq = 0;
  const show = async (q) => {
    const mine = ++seq;
    try {
      const r = await api.search(q, 15);
      if (mine !== seq) return;
      results.replaceChildren(...(r.companies.length ? r.companies.map((c) =>
        h("button", { role: "option", onclick: () => select(c.company_id) },
          h("span", {}, c.legal_name), h("span", { class: "sym" }, [c.nse_symbol, c.bse_scrip_code].filter(Boolean).join(" · "))))
        : [h("div", { class: "state small" }, "No company matches.")]));
      results.hidden = false;
    } catch (e) {
      results.replaceChildren(h("div", { class: "state error small" }, e.message));
      results.hidden = false;
    }
  };
  const select = (id) => {
    results.hidden = true;
    input.value = "";
    routeTo(id, "", "overview");
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => show(input.value.trim()), 180); });
  input.addEventListener("focus", () => show(input.value.trim()));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") results.hidden = true;
    if (e.key === "Enter") { const first = results.querySelector("button"); if (first) first.click(); }
  });
  document.addEventListener("click", (e) => { if (!box.contains(e.target)) results.hidden = true; });
}

// ---------------------------------------------------------------- tabs
function renderTabs(route) {
  clear(tabsEl);
  for (const [key, label] of TABS) {
    const enabled = !!state.company;
    tabsEl.append(h("a", { href: enabled ? `#/c/${encodeURIComponent(state.company.company.company_id)}/${encodeURIComponent(state.year?.fiscal_year || "")}/${key}` : "#",
      class: `${route.tab === key && enabled ? "active" : ""} ${enabled ? "" : "disabled"}`, dataset: { tab: key } }, label));
  }
}

function renderYears() {
  clear(fySelect);
  const years = state.company.years;
  fySelect.disabled = !years.length;
  for (const y of [...years].reverse()) {
    const note = y.status === "PROCESSED" ? "" : ` (${y.status.replace(/_/g, " ").toLowerCase()})`;
    fySelect.append(h("option", { value: y.fiscal_year, selected: y.fiscal_year === state.year?.fiscal_year }, `${fy(y.fiscal_year)}${note}`));
  }
}

fySelect.addEventListener("change", () => {
  const r = parseRoute();
  routeTo(state.company.company.company_id, fySelect.value, r.tab, Object.fromEntries(r.params));
});

// ---------------------------------------------------------------- landing
async function landing() {
  clear(view).append(loading("Loading companies…"));
  try {
    const r = await api.search("", 100);
    clear(view).append(
      h("section", { class: "panel" },
        h("h2", {}, "Select a company"),
        h("p", { class: "muted" }, `${r.total} companies in the processing universe. Search above, or choose one below.`),
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Company"), h("th", {}, "NSE"), h("th", {}, "BSE"), h("th", {}, "ISIN"), h("th", {}, "Industry"))),
          h("tbody", {}, r.companies.map((c) => h("tr", { class: "clickable", onclick: () => routeTo(c.company_id, "", "overview") },
            h("td", { class: "metric-name" }, c.legal_name), h("td", {}, c.nse_symbol), h("td", {}, c.bse_scrip_code || "—"),
            h("td", { class: "small" }, c.isin), h("td", { class: "small muted" }, c.industry || "—"))))))));
  } catch (e) {
    clear(view).append(errorState(e, landing));
  }
}

// ---------------------------------------------------------------- router
async function render() {
  closeDrawer();
  const route = parseRoute();
  if (route.kind !== "c" || !route.id) {
    state.company = null; state.year = null;
    fySelect.disabled = true; clear(fySelect).append(h("option", {}, "—"));
    renderTabs(route);
    return landing();
  }
  clear(view).append(loading());
  renderTabs(route);
  try {
    if (!state.catalog) {
      const cat = await api.catalog();
      state.catalog = new Map(cat.metrics.map((m) => [m.requirement_id, m]));
    }
    if (!state.company || ![state.company.company.company_id, state.company.company.isin, state.company.company.nse_symbol].includes(route.id)) {
      state.company = await api.company(route.id);
    }
    const years = state.company.years;
    if (!years.length) {
      state.year = null; renderTabs(route); renderYears();
      clear(view).append(empty("No in-scope BRSR filings are recorded for this company."));
      return;
    }
    let year = years.find((y) => y.fiscal_year === route.fy);
    if (!year) {
      // default: the latest processed year
      const processed = years.filter((y) => y.status === "PROCESSED");
      year = processed[processed.length - 1] || years[years.length - 1];
      if (route.fy) {
        clear(view).append(errorState({ status: 404, message: `${fy(route.fy)} is not available for this company.` }));
        state.year = year; renderYears(); renderTabs(route);
        view.append(h("p", { class: "state" }, h("a", { href: `#/c/${encodeURIComponent(state.company.company.company_id)}/${year.fiscal_year}/${route.tab}` }, `Open ${fy(year.fiscal_year)}`)));
        return;
      }
      return routeTo(state.company.company.company_id, year.fiscal_year, route.tab, Object.fromEntries(route.params));
    }
    state.year = year;
    renderYears(); renderTabs(route);
    document.title = `${state.company.company.legal_name} · ${fy(year.fiscal_year)} · BRSR Analytics`;
    const entry = TABS.find(([k]) => k === route.tab) || TABS[0];
    clear(view);
    entry[2](view, state, route.params, (params) =>
      routeTo(state.company.company.company_id, year.fiscal_year, entry[0], params));
  } catch (e) {
    renderTabs(route);
    clear(view).append(errorState(e, render));
  }
}

buildSearch();
window.addEventListener("hashchange", render);
render();
