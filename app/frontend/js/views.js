import { api } from "./api.js";
import { barChart } from "./charts.js";
import { badge, empty, errorState, genderGridTable, into, loading, metricCard, metricTable, missingTable, openDrawer, valueBadges } from "./components.js";
import { h, safeLink } from "./dom.js";
import { BASIS, DISCLOSURE, breakdownLabel, displayValue, formatNumber, fy, unitLabel, unitTitle } from "./format.js";

const CFG = window.BRSR_CONFIG;

function section(title, cls, ...children) {
  return [h("div", { class: `section-title ${cls || ""}` }, h("h2", {}, title)), ...children];
}

function groupsWithTheme(data, theme) {
  return data.metrics.filter((g) => g.themes.includes(theme));
}

function missingWithIds(data, ids) {
  return data.missing.filter((m) => ids.has(m.requirement_id));
}

// ---------------------------------------------------------------- overview
export function overview(root, state) {
  const { company, year } = state;
  const c = company.company;
  const ids = company.identifiers.filter((i) => i.identifier_type !== "ISIN" || i.value !== c.isin);
  const f = year.filing;
  root.append(h("div", { class: "grid grid-2" },
    h("section", { class: "panel identity" },
      h("h1", {}, c.legal_name),
      h("div", { class: "muted" }, [c.industry, c.company_status].filter(Boolean).join(" · ")),
      h("dl", { class: "kv" },
        [["NSE symbol", c.nse_symbol], ["BSE scrip code", c.bse_scrip_code], ["ISIN", c.isin],
         ["CIN", c.cin ? `${c.cin}${c.cin_status ? ` (${c.cin_status.toLowerCase().replace(/_/g, " ")})` : ""}` : ""],
         ["Website", c.website ? safeLink(/^https?:/.test(c.website) ? c.website : `https://${c.website}`, c.website) : ""],
         ["Registered office", c.registered_office]].map(([k, v]) => [h("dt", {}, k),
          h("dd", {}, v || h("span", { class: "muted" }, "Not available"))])),
      ids.length ? h("p", { class: "small muted" }, `${ids.length} historical / alternative identifiers on record`) : null),
    h("section", { class: "panel" },
      h("h2", {}, `BRSR filing · ${fy(year.fiscal_year)}`),
      f ? h("dl", { class: "kv" }, [
        ["Status", h("span", { class: "badge b-ok" }, year.status.replace(/_/g, " ").toLowerCase())],
        ["Filing ID", f.filing_id],
        ["Reporting period", `${f.reporting_period_start} to ${f.reporting_period_end}`],
        ["Submitted to NSE", f.submission_date],
        ["Taxonomy version", f.taxonomy_version || "—"],
        ["Version", `${f.filing_version_status}${year.versions > 1 ? ` (${year.versions} versions in this year)` : ""}`],
        ["XBRL", safeLink(f.xbrl_url, "XBRL instance")],
        ["PDF", f.pdf_url ? safeLink(f.pdf_url, "BRSR PDF on NSE") : "—"]].map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))
        : h("div", {}, badge(DISCLOSURE, year.status === "EXTRACTION_ERROR" ? "EXTRACTION_ERROR" : "NO_FILING_FOR_YEAR"),
          year.failed_filings.map((x) => h("p", { class: "small muted" }, `${x.filing_id}: ${x.error}`))))));

  const cards = h("div", { class: "cards" });
  const wf = h("div", {});
  root.append(...section("Key metrics", "", cards), ...section("Workforce at a glance", "soc", wf));
  into(cards, () => api.yearMetrics(c.company_id, year.fiscal_year), (data) => {
    const byKey = new Map(data.metrics.map((g) => [g.metric_key, g]));
    const missByKey = new Map(data.missing.map((m) => [m.metric_key, m]));
    for (const key of CFG.summaryMetricKeys) {
      if (byKey.has(key) || missByKey.has(key)) cards.append(metricCard(byKey.get(key), missByKey.get(key)));
    }
    if (!cards.children.length) cards.append(empty("No metrics available for this year."));
  });
  into(wf, () => api.workforce(c.company_id, year.fiscal_year), (data) => {
    const g = data.metrics.find((x) => x.gender_grid && x.gender_grid.length);
    if (!g) { wf.append(missingTable(data.missing) || empty("No workforce data.")); return; }
    wf.append(h("div", { class: "panel" }, h("h2", {}, g.metric_name), genderGridTable(g)));
  });
}

// ---------------------------------------------------------------- pillars
const ENV_THEMES = [["energy", "Energy"], ["emissions", "GHG emissions"], ["water", "Water"], ["waste", "Waste"]];

export function environmental(root, state) {
  const { company, year } = state;
  const box = h("div", {});
  root.append(...section(`Environmental · ${fy(year.fiscal_year)}`, "env"), box);
  into(box, () => api.pillar(company.company.company_id, year.fiscal_year, "environmental"), (data) => {
    const used = new Set();
    for (const [theme, title] of ENV_THEMES) {
      const groups = groupsWithTheme(data, theme);
      groups.forEach((g) => used.add(g.requirement_id));
      const themeIds = new Set(data.missing.filter((m) => theme === themeOf(m, state)).map((m) => m.requirement_id));
      themeIds.forEach((x) => used.add(x));
      const p = h("section", { class: "panel" }, h("h2", {}, title));
      if (groups.length) {
        if (theme === "energy") p.append(energyChart(groups));
        p.append(metricTable(groups));
      }
      const miss = missingTable(missingWithIds(data, themeIds));
      if (miss) p.append(h("h3", {}, "Not available"), miss);
      if (!groups.length && !miss) p.append(empty("No metrics in this group."));
      box.append(p, h("div", { style: "height:14px" }));
    }
    const rest = data.metrics.filter((g) => !used.has(g.requirement_id));
    const restMiss = data.missing.filter((m) => !used.has(m.requirement_id));
    if (rest.length || restMiss.length) {
      box.append(h("section", { class: "panel" }, h("h2", {}, "Other environmental metrics"),
        rest.length ? metricTable(rest) : null, missingTable(restMiss)));
    }
  });
}

function themeOf(m, state) {
  const meta = state.catalog.get(m.requirement_id);
  return meta ? meta.themes[0] : null;
}

// energy mix chart: only when every bar is a normalised value in one unit
function energyChart(groups) {
  // energy quantities only (intensities have another unit family)
  const pick = groups.filter((g) => g.expected_unit === "energy");
  const items = pick.map((g) => {
    const v = g.values.find((x) => !x.breakdown) || g.values[0];
    return { label: g.metric_name, v };
  });
  const units = new Set(items.filter((i) => i.v.value_numeric !== null).map((i) => i.v.unit));
  const allNormalised = items.every((i) => i.v.value_numeric !== null);
  if (!items.length) return null;
  if (units.size !== 1 || !allNormalised) {
    return h("p", { class: "note" }, "Energy chart not shown: one or more energy values are not normalised to a common unit "
      + "(see the table). Values are never combined across different or unknown units.");
  }
  const unit = [...units][0];
  return h("div", {}, h("div", { class: "small muted" }, `Energy consumption by source (${unitLabel(unit)})`),
    barChart(items.map((i) => ({ label: i.label, value: i.v.value_numeric, text: i.v.value,
      calc: i.v.value_basis.startsWith("CALCULATED") })), unitLabel(unit), { title: "Energy by source" }));
}

export function social(root, state) {
  const { company, year } = state;
  const id = company.company.company_id;
  const wf = h("div", {}), posh = h("div", {}), rest = h("div", {});
  root.append(...section(`Social · ${fy(year.fiscal_year)}`, "soc"),
    h("h3", { class: "muted" }, "Workforce"), wf,
    h("div", { class: "section-title soc" }, h("h2", {}, "POSH (prevention of sexual harassment)")), posh,
    h("div", { class: "section-title soc" }, h("h2", {}, "Other social metrics")), rest);
  const shown = new Set();
  into(wf, () => api.workforce(id, year.fiscal_year), (data) => {
    for (const g of data.metrics) {
      shown.add(g.requirement_id);
      const p = h("section", { class: "panel" }, h("h2", {}, g.metric_name));
      if (g.gender_grid && g.gender_grid.length) p.append(genderGridTable(g));
      else p.append(metricTable([g]));
      wf.append(p, h("div", { style: "height:14px" }));
    }
    data.missing.forEach((m) => shown.add(m.requirement_id));
    const miss = missingTable(data.missing);
    if (miss) wf.append(h("section", { class: "panel" }, h("h2", {}, "Workforce metrics not available"), miss));
  });
  into(posh, () => api.posh(id, year.fiscal_year), (data) => {
    [...data.metrics, ...data.missing].forEach((m) => shown.add(m.requirement_id));
    posh.append(h("section", { class: "panel" },
      data.metrics.length ? metricTable(data.metrics, { showBreakdown: false }) : null,
      missingTable(data.missing),
      h("p", { class: "note" }, "BRSR XBRL reports complaints filed, pending at year end, reported and upheld. "
        + "A 'resolved' count is not reported in the filing and is not derived here.")));
  });
  into(rest, () => api.pillar(id, year.fiscal_year, "social"), (data) => {
    const groups = data.metrics.filter((g) => !g.themes.some((t) => t === "workforce" || t === "posh"));
    const miss = data.missing.filter((m) => { const t = state.catalog.get(m.requirement_id)?.themes || []; return !t.includes("workforce") && !t.includes("posh"); });
    rest.append(h("section", { class: "panel" }, groups.length ? metricTable(groups) : empty("No other social metrics."),
      miss.length ? [h("h3", {}, "Not available"), missingTable(miss)] : null));
  });
}

export function governance(root, state) {
  const { company, year } = state;
  const box = h("div", {});
  root.append(...section(`Governance · ${fy(year.fiscal_year)}`, "gov"), box);
  into(box, () => api.pillar(company.company.company_id, year.fiscal_year, "governance"), (data) => {
    const board = data.metrics.filter((g) => g.themes.includes("board"));
    const others = data.metrics.filter((g) => !g.themes.includes("board"));
    const cards = h("div", { class: "cards" }, board.map((g) => metricCard(g)));
    box.append(h("section", { class: "panel" }, h("h2", {}, "Board composition"),
      board.length ? cards : empty("Board composition not available."),
      missingTable(data.missing.filter((m) => (state.catalog.get(m.requirement_id)?.themes || []).includes("board")))),
    h("div", { style: "height:14px" }),
    h("section", { class: "panel" }, h("h2", {}, "Ethics, complaints and other governance metrics"),
      others.length ? metricTable(others) : empty("No other governance metrics."),
      (() => { const m = data.missing.filter((x) => !(state.catalog.get(x.requirement_id)?.themes || []).includes("board"));
        return m.length ? [h("h3", {}, "Not available"), missingTable(m)] : null; })()));
  });
}

// ---------------------------------------------------------------- trends
function metricOptions(state, selected) {
  const list = [...state.catalog.values()].filter((m) => m.in_brsr_xbrl);
  const byPillar = {};
  list.forEach((m) => (byPillar[m.pillar] ||= []).push(m));
  return Object.entries(byPillar).map(([p, ms]) => h("optgroup", { label: p },
    ms.map((m) => h("option", { value: m.requirement_id, selected: m.requirement_id === selected }, `${m.metric_name} (${m.requirement_id})`))));
}

// Breakdowns actually reported for a metric. Metrics without breakdown axes
// in the registry only have the total value (breakdown ""), so no request
// is needed; otherwise one small metric-filtered request per company-year
// is made in parallel (cached for the session).
const TOTAL = ["", "Total (no breakdown)"];

export async function breakdownOptions(state, metric, companyYears) {
  const meta = state.catalog.get(metric);
  if (!meta || !meta.breakdown_axes.length) return [TOTAL];
  const results = await Promise.all(companyYears.map(([id, fyLabel]) =>
    api.yearMetrics(id, fyLabel, [metric]).catch(() => null)));
  const seen = new Map();
  for (const data of results) {
    const g = data && data.metrics.find((x) => x.requirement_id === metric);
    (g ? g.values : []).forEach((v) => {
      if (!seen.has(v.breakdown)) seen.set(v.breakdown, breakdownLabel(v.dimensions) || TOTAL[1]);
    });
  }
  return [...seen.entries()].sort((x, y) => (x[0] !== "") - (y[0] !== "") || x[1].localeCompare(y[1]));
}

function breakdownSelect(options, current, onchange) {
  if (options.length === 1 && options[0][0] === "") {
    return h("select", { class: "wide", disabled: true, title: "This metric has no breakdown" },
      h("option", { value: "" }, "No breakdown (single value)"));
  }
  if (!options.length) {
    return h("select", { class: "wide", disabled: true }, h("option", {}, "No reported breakdowns"));
  }
  return h("select", { class: "wide", onchange: (e) => onchange(e.target.value) },
    options.map(([v, l]) => h("option", { value: v, selected: v === current }, l)));
}

export function trends(root, state, params, navigate) {
  const metric = params.get("metric") || "E07";
  const breakdown = params.get("breakdown") || "";
  const out = h("div", {});
  const blabel = h("label", {}, "Breakdown", h("select", { class: "wide", disabled: true }, h("option", {}, "Loading…")));
  root.append(...section(`Historical trend · ${state.company.company.legal_name}`, ""),
    h("div", { class: "controls" },
      h("label", {}, "Metric", h("select", { class: "wide", onchange: (e) => navigate({ metric: e.target.value, breakdown: "" }) },
        metricOptions(state, metric))),
      blabel), out);
  const id = state.company.company.company_id;
  const years = state.company.years.filter((y) => y.filing).map((y) => [id, y.fiscal_year]);
  out.append(loading());
  breakdownOptions(state, metric, years).then((opts) => {
    // a metric with breakdowns has no total value: default to the first one
    if (opts.length && !opts.some(([v]) => v === breakdown)) {
      return navigate({ metric, breakdown: opts[0][0] });
    }
    blabel.replaceChildren("Breakdown", breakdownSelect(opts, breakdown, (v) => navigate({ metric, breakdown: v })));
    into(out, () => api.history(id, metric, breakdown), (data) => renderHistory(out, data));
  }).catch((e) => { out.replaceChildren(errorState(e, () => navigate({ metric, breakdown }))); });
}

function renderHistory(out, data) {
  const rows = data.series.map((s) => {
    const vals = s.values;
    return { fy: s.fiscal_year, vals, missing: s.missing };
  });
  const numeric = rows.filter((r) => r.vals.length === 1 && r.vals[0].value_numeric !== null);
  const multi = rows.some((r) => r.vals.length > 1);
  const panel = h("section", { class: "panel" }, h("h2", {}, `${data.metric.metric_name} (${data.metric.requirement_id})`
    + (data.dimensions.length ? ` · ${breakdownLabel(data.dimensions)}` : "")));
  if (!data.comparable) {
    panel.append(h("p", { class: "note" }, `Chart not shown: normalised units differ across years (${data.units.join(", ")}).`));
  } else if (multi) {
    panel.append(h("p", { class: "note" }, "Chart not shown: some years report more than one value for this metric; see the table."));
  } else if (numeric.length) {
    const unit = data.units[0];
    panel.append(h("div", { class: "small muted" }, `Normalised values (${unitLabel(unit)}); years without a comparable value are labelled, not drawn.`),
      barChart(rows.map((r) => {
        const v = r.vals[0];
        if (v && v.value_numeric !== null) return { label: fy(r.fy), value: v.value_numeric, text: v.value, calc: v.value_basis.startsWith("CALCULATED") };
        if (v) return { label: fy(r.fy), value: null, note: `not normalised (filed ${formatNumber(v.reported_value) ?? v.reported_value} ${v.reported_unit ? unitLabel(v.reported_unit, true) : "without unit"})` };
        return { label: fy(r.fy), value: null, note: (DISCLOSURE[r.missing?.disclosure_status] || ["not available"])[0].toLowerCase() };
      }), unitLabel(unit), { title: "History" }));
  } else {
    panel.append(h("p", { class: "note" }, "No normalised numeric values to chart."));
  }
  panel.append(h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Financial year"), h("th", { class: "num" }, "Value"), h("th", {}, "Unit"),
      h("th", {}, "Status"), h("th", {}, "Filed as"), h("th", {}, ""))),
    h("tbody", {}, rows.flatMap((r) => r.vals.length ? r.vals.map((v) => {
      const d = displayValue(v);
      return h("tr", { class: "clickable", onclick: () => openDrawer(v) }, h("td", {}, fy(r.fy)),
        h("td", { class: "num" }, d.asFiled ? "—" : d.text), h("td", {}, d.asFiled ? "—" : d.unit),
        h("td", {}, valueBadges(v)), h("td", { class: "small", title: unitTitle(v.reported_unit) || null }, `${formatNumber(v.reported_value) ?? v.reported_value} ${v.reported_unit ? unitLabel(v.reported_unit, true) : "(no unit)"}`),
        h("td", {}, h("button", { class: "link-btn" }, "Details")));
    }) : [h("tr", {}, h("td", {}, fy(r.fy)), h("td", { class: "num missing" }, "Not available"), h("td", {}, "—"),
      h("td", {}, r.missing ? badge(DISCLOSURE, r.missing.disclosure_status) : "—"),
      h("td", { class: "small muted", colspan: 2 }, r.missing?.missing_reason || ""))])))));
  out.append(panel);
}

// ---------------------------------------------------------------- compare
export function compare(root, state, params, navigate) {
  const metric = params.get("metric") || "E07";
  const breakdown = params.get("breakdown") || "";
  const selected = (params.get("companies") || state.company.company.company_id).split(",").filter(Boolean);
  const fyRequested = params.get("fy");
  const go = (patch) => navigate({ metric, breakdown, fy: fyRequested ?? state.year.fiscal_year,
    companies: selected.join(","), ...patch });
  const out = h("div", {});
  const add = h("input", { type: "search", placeholder: "Add a company (name, symbol, ISIN)…", autocomplete: "off",
    "aria-label": "Add a company to the comparison" });
  const results = h("div", { class: "search-results", hidden: true });
  let timer;
  add.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = add.value.trim();
      if (!q) { results.hidden = true; return; }
      try {
        const r = await api.search(q, 10);
        results.replaceChildren(...r.companies.filter((c) => !selected.includes(c.company_id)).map((c) =>
          h("button", { onclick: () => go({ companies: [...selected, c.company_id].join(",") }) },
            h("span", {}, c.legal_name), h("span", { class: "sym" }, c.nse_symbol))));
        results.hidden = !results.children.length;
      } catch (e) { results.hidden = true; }
    }, 200);
  });
  const fyLabel = h("label", {}, "Financial year", h("select", { disabled: true }, h("option", {}, "Loading…")));
  const bLabel = h("label", {}, "Breakdown", h("select", { class: "wide", disabled: true }, h("option", {}, "Loading…")));
  const chips = h("div", { class: "chips" });
  const coverage = h("div", {});
  root.append(...section("Company comparison", ""),
    h("div", { class: "controls" },
      h("label", {}, "Metric", h("select", { class: "wide", onchange: (e) => go({ metric: e.target.value, breakdown: "" }) }, metricOptions(state, metric))),
      fyLabel, bLabel,
      h("div", { class: "search", style: "width:min(340px,90vw)" }, add, results)),
    chips, coverage, out);
  if (!selected.length) {
    fyLabel.replaceChildren("Financial year", h("select", { disabled: true }, h("option", {}, "—")));
    bLabel.replaceChildren("Breakdown", h("select", { disabled: true }, h("option", {}, "—")));
    out.append(empty("Add at least one company to compare."));
    return;
  }
  out.append(loading());
  // years of every selected company (cached company records)
  Promise.all(selected.map((id) => api.company(id).catch((e) => ({ error: e, id })))).then(async (cos) => {
    const failed = cos.filter((c) => c.error);
    if (failed.length) {
      out.replaceChildren(errorState(failed[0].error, () => go({})));
      return;
    }
    chips.replaceChildren(...cos.map((c) => h("span", { class: "chip", title: c.company.legal_name },
      c.company.nse_symbol || c.company.legal_name,
      h("button", { title: `Remove ${c.company.legal_name}`, "aria-label": `Remove ${c.company.legal_name}`,
        onclick: () => go({ companies: selected.filter((x) => x !== c.company.company_id).join(",") }) }, "×"))));
    // financial years with a filing record for at least one selected company
    const byFy = new Map();
    cos.forEach((c) => c.years.forEach((y) => {
      if (!byFy.has(y.fiscal_year)) byFy.set(y.fiscal_year, []);
      if (y.filing) byFy.get(y.fiscal_year).push(c.company.company_id);
    }));
    const fys = [...byFy.keys()].sort();
    let fySel = fyRequested ?? state.year.fiscal_year;
    if (fySel && !byFy.has(fySel)) fySel = fys[fys.length - 1] || "";
    if (fyRequested !== null && fySel !== fyRequested) return go({ fy: fySel });
    fyLabel.replaceChildren("Financial year", h("select", { onchange: (e) => go({ fy: e.target.value, breakdown: "" }) },
      h("option", { value: "", selected: fySel === "" }, "All years"),
      fys.map((y) => h("option", { value: y, selected: y === fySel },
        `${fy(y)} · data for ${byFy.get(y).length}/${cos.length} companies`))));
    // breakdowns reported by the selected companies for the metric
    const pairs = cos.flatMap((c) => c.years.filter((y) => y.filing && (!fySel || y.fiscal_year === fySel))
      .map((y) => [c.company.company_id, y.fiscal_year]));
    const opts = await breakdownOptions(state, metric, pairs);
    if (opts.length && !opts.some(([v]) => v === breakdown)) return go({ fy: fySel, breakdown: opts[0][0] });
    bLabel.replaceChildren("Breakdown", breakdownSelect(opts, breakdown, (v) => go({ fy: fySel, breakdown: v })));
    if (fySel) {
      const without = cos.filter((c) => !byFy.get(fySel).includes(c.company.company_id));
      if (without.length) {
        coverage.replaceChildren(h("p", { class: "note" }, `No processed ${fy(fySel)} filing for: `
          + without.map((c) => c.company.nse_symbol || c.company.legal_name).join(", ")
          + ". These companies are listed with their status and are not drawn in the chart."));
      }
    }
    into(out, () => api.compare(metric, selected, fySel || undefined, breakdown), (data) => renderCompare(out, data));
  }).catch((e) => out.replaceChildren(errorState(e, () => go({}))));
}

function renderCompare(out, data) {
  const rows = [];
  for (const c of data.companies) {
    for (const y of c.years) {
      if (y.values.length) y.values.forEach((v) => rows.push({ c: c.company, fy: y.fiscal_year, v }));
      else rows.push({ c: c.company, fy: y.fiscal_year, missing: y.missing });
    }
  }
  const panel = h("section", { class: "panel" }, h("h2", {}, `${data.metric.metric_name} (${data.metric.requirement_id})`
    + (data.dimensions.length ? ` · ${breakdownLabel(data.dimensions)}` : "")
    + (data.fiscal_year ? ` · ${fy(data.fiscal_year)}` : " · all years")));
  const chartable = data.fiscal_year && data.comparable && data.units.length === 1;
  if (chartable) {
    const unit = data.units[0];
    panel.append(h("div", { class: "small muted" }, `Normalised values in ${unitLabel(unit)}. Companies without a comparable value are labelled, not drawn.`),
      barChart(rows.map((r) => {
        const label = r.c.nse_symbol || r.c.legal_name;
        if (r.v && r.v.value_numeric !== null) return { label: r.v.concept && rows.filter((x) => x.c === r.c).length > 1 ? `${label} · ${r.v.concept}` : label,
          value: r.v.value_numeric, text: r.v.value, calc: r.v.value_basis.startsWith("CALCULATED") };
        if (r.v) return { label, value: null, note: "not normalised (unit missing or ambiguous)" };
        return { label, value: null, note: (DISCLOSURE[r.missing?.disclosure_status] || ["not available"])[0].toLowerCase() };
      }), unitLabel(unit), { title: "Comparison" }));
  } else if (!data.comparable) {
    panel.append(h("p", { class: "note" }, `Chart not shown: normalised units differ (${data.units.join(", ")}); values are not compared as equivalent.`));
  } else if (!data.fiscal_year) {
    panel.append(h("p", { class: "note" }, "Select one financial year to see a chart."));
  }
  panel.append(h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Company"), h("th", {}, "FY"), h("th", { class: "num" }, "Value"),
      h("th", {}, "Unit"), h("th", {}, "Status"), h("th", {}, "Source"))),
    h("tbody", {}, rows.map((r) => {
      if (!r.v) return h("tr", {}, h("td", {}, h("div", { class: "metric-name" }, r.c.legal_name), h("div", { class: "metric-sub" }, r.c.nse_symbol)),
        h("td", {}, fy(r.fy)), h("td", { class: "num missing" }, "Not available"), h("td", {}, "—"),
        h("td", {}, r.missing ? badge(DISCLOSURE, r.missing.disclosure_status) : "—"), h("td", { class: "small muted" }, r.missing?.missing_reason || ""));
      const d = displayValue(r.v);
      return h("tr", { class: "clickable", onclick: () => openDrawer(r.v) },
        h("td", {}, h("div", { class: "metric-name" }, r.c.legal_name), h("div", { class: "metric-sub" }, r.c.nse_symbol)),
        h("td", {}, fy(r.fy)), h("td", { class: "num" }, d.text, d.asFiled ? h("div", { class: "metric-sub" }, "as filed") : null),
        h("td", {}, d.unit), h("td", {}, valueBadges(r.v)), h("td", { class: "small" }, r.v.source.filing_id));
    })))));
  out.append(panel);
}

// ---------------------------------------------------------------- quality
export function quality(root, state) {
  const { company, year } = state;
  const box = h("div", {});
  root.append(...section(`Data quality · ${fy(year.fiscal_year)}`, ""), box);
  into(box, () => api.quality(company.company.company_id, year.fiscal_year), (dq) => {
    const counts = (obj, table) => h("div", { class: "badges" }, Object.entries(obj).map(([k, n]) =>
      h("span", {}, badge(table, k), h("span", { class: "small", style: "margin:0 10px 0 4px" }, String(n)))));
    box.append(h("div", { class: "grid grid-2" },
      h("section", { class: "panel" }, h("h2", {}, "Reported values by status"), counts(dq.value_disclosure_status, DISCLOSURE),
        h("h3", {}, "By value basis"), counts(dq.value_basis, BASIS)),
      h("section", { class: "panel" }, h("h2", {}, "Metrics without a value"), counts(dq.missing_metrics, DISCLOSURE))),
    h("div", { style: "height:14px" }),
    h("section", { class: "panel" }, h("h2", {}, "Values without a normalised value"),
      dq.not_normalised_values.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Metric"), h("th", {}, "Breakdown"), h("th", { class: "num" }, "Filed value"), h("th", {}, "Filed unit"), h("th", {}, "Reason"))),
        h("tbody", {}, dq.not_normalised_values.map((v) => h("tr", {},
          h("td", {}, (state.catalog.get(v.requirement_id)?.metric_name || v.requirement_id)), h("td", { class: "small" }, v.breakdown || "—"),
          h("td", { class: "num" }, formatNumber(v.reported_value) ?? v.reported_value), h("td", { title: unitTitle(v.reported_unit) || null }, v.reported_unit ? unitLabel(v.reported_unit, true) : "not stated"),
          h("td", { class: "small muted" }, v.note || v.normalisation_status))))))
        : empty("All reported values are normalised.")),
    h("div", { style: "height:14px" }),
    h("section", { class: "panel" }, h("h2", {}, "Open review items"),
      h("p", { class: "small muted" }, "Recommendations from the review workflow. Recommendations are not approvals; nothing here changes a value."),
      dq.open_review_items.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "ID"), h("th", {}, "Category"), h("th", {}, "Metric"), h("th", {}, "Status"), h("th", {}, "Severity"), h("th", {}, "Reason"))),
        h("tbody", {}, dq.open_review_items.map((r) => h("tr", {}, h("td", { class: "small" }, r.recommendation_id),
          h("td", {}, r.category), h("td", {}, r.requirement_id || "—"),
          h("td", {}, h("span", { class: "badge b-warn" }, r.status.replace(/_/g, " ").toLowerCase())),
          h("td", {}, r.severity), h("td", { class: "small muted" }, r.reason))))))
        : empty("No open review items.")),
    h("div", { style: "height:14px" }),
    h("section", { class: "panel" }, h("h2", {}, "Deferred data-quality issues"),
      dq.deferred_issues.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Issue"), h("th", {}, "Metric"), h("th", {}, "Item"), h("th", {}, "Status"), h("th", {}, "Reason"))),
        h("tbody", {}, dq.deferred_issues.map((d) => h("tr", {}, h("td", {}, d.issue_id), h("td", {}, d.metric), h("td", { class: "small" }, d.item),
          h("td", {}, h("span", { class: "badge b-warn" }, d.status.replace(/_/g, " ").toLowerCase())), h("td", { class: "small muted" }, d.reason_for_deferral))))))
        : empty("No deferred issues for this company-year.")));
  });
}
