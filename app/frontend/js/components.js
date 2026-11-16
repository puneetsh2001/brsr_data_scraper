import { append, h, clear, safeLink } from "./dom.js";
import { BASIS, DISCLOSURE, breakdownLabel, displayValue, formatNumber, fy, info, unitLabel, unitTitle } from "./format.js";

// ---------------------------------------------------------------- badges
export function badge(table, code) {
  const [label, cls, title] = info(table, code);
  return h("span", { class: `badge ${cls}`, title }, label);
}

export function reviewBadges(v) {
  const out = [];
  const r = v.review || {};
  if (r.pending_review) out.push(h("span", { class: "badge b-warn", title: r.pending_review }, "Pending review"));
  if (r.deferred_issue_ids && r.deferred_issue_ids.length)
    out.push(h("span", { class: "badge b-warn", title: r.deferred_issue_ids.join(", ") }, "Deferred issue"));
  return out;
}

export function valueBadges(v) {
  return h("div", { class: "badges" }, badge(DISCLOSURE, v.disclosure_status),
    v.value_basis ? badge(BASIS, v.value_basis) : null, reviewBadges(v));
}

// ---------------------------------------------------------------- states
export function loading(text = "Loading…") {
  return h("div", { class: "state" }, h("div", { class: "spinner" }), text);
}

export function errorState(err, retry) {
  return h("div", { class: "state error", role: "alert" },
    h("div", { class: "metric-name" }, err.status === 404 ? "Not found" : "Could not load data"),
    h("div", {}, err.message || String(err)),
    retry ? h("p", {}, h("button", { class: "btn secondary", onclick: retry }, "Retry")) : null);
}

export function empty(text) {
  return h("div", { class: "state" }, text);
}

export async function into(el, promiseFn, render) {
  clear(el).appendChild(loading());
  try {
    const data = await promiseFn();
    clear(el);
    render(data);
  } catch (e) {
    clear(el).appendChild(errorState(e, () => into(el, promiseFn, render)));
  }
}

// ---------------------------------------------------------------- values
function valueCell(v) {
  const d = displayValue(v);
  if (d.missing) return h("td", { class: "num missing" }, "Not available");
  return h("td", { class: "num" }, d.text,
    d.asFiled ? h("div", { class: "metric-sub", title: "Shown as filed: no normalised value" }, "as filed") : null);
}

function unitCell(v) {
  const d = displayValue(v);
  return h("td", { title: unitTitle(d.rawUnit) || null }, d.unit || h("span", { class: "muted" }, "—"));
}

// one table for a list of metric groups (each with values)
export function metricTable(groups, { showBreakdown = true } = {}) {
  const rows = [];
  for (const g of groups) {
    g.values.forEach((v, i) => {
      const sub = breakdownLabel(v.dimensions);
      rows.push(h("tr", { class: "clickable", tabindex: "0", title: "Show source and calculation",
        onclick: () => openDrawer(v), onkeydown: (e) => { if (e.key === "Enter") openDrawer(v); } },
        h("td", {}, i === 0 ? h("div", { class: "metric-name" }, g.metric_name) : null,
          showBreakdown && sub ? h("div", { class: "metric-sub" }, sub) : null,
          g.values.length > 1 && !sub ? h("div", { class: "metric-sub" }, v.concept) : null),
        valueCell(v), unitCell(v), h("td", {}, valueBadges(v)),
        h("td", {}, h("button", { class: "link-btn", onclick: (e) => { e.stopPropagation(); openDrawer(v); } }, "Details"))));
    });
  }
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Metric"), h("th", { class: "num" }, "Value"),
      h("th", {}, "Unit"), h("th", {}, "Status"), h("th", {}, ""))),
    h("tbody", {}, rows)));
}

export function missingTable(missing) {
  if (!missing.length) return null;
  // one row when every metric is missing for the same reason (e.g. no
  // filing or a failed filing for the year)
  const same = missing.length > 2 && missing.every((m) => m.disclosure_status === missing[0].disclosure_status
    && (m.missing_reason || "") === (missing[0].missing_reason || ""));
  if (same) {
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Metrics"), h("th", {}, "Status"), h("th", {}, "Reason"))),
      h("tbody", {}, h("tr", {},
        h("td", {}, h("div", { class: "metric-name" }, `${missing.length} metrics`),
          h("div", { class: "metric-sub" }, missing.map((m) => m.metric_name).join(", "))),
        h("td", {}, badge(DISCLOSURE, missing[0].disclosure_status)),
        h("td", { class: "small muted" }, missing[0].missing_reason || "")))));
  }
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Metric"), h("th", {}, "Status"), h("th", {}, "Reason"))),
    h("tbody", {}, missing.map((m) => h("tr", {},
      h("td", {}, h("div", { class: "metric-name" }, m.metric_name), h("div", { class: "metric-sub" }, m.requirement_id)),
      h("td", {}, badge(DISCLOSURE, m.disclosure_status)),
      h("td", { class: "small muted" }, m.missing_reason || ""))))));
}

// summary card for one metric group (first total value)
export function metricCard(group, missingRec) {
  if (!group) {
    return h("div", { class: "card", title: missingRec.missing_reason || "" },
      h("div", { class: "label" }, missingRec.metric_name),
      h("div", { class: "value missing" }, "Not available"),
      h("div", { class: "badges" }, badge(DISCLOSURE, missingRec.disclosure_status)));
  }
  const v = group.values.find((x) => !x.breakdown) || group.values[0];
  const d = displayValue(v);
  return h("button", { class: "card", onclick: () => openDrawer(v), title: "Show source and calculation" },
    h("div", { class: "label" }, group.metric_name),
    h("div", { class: `value${d.missing ? " missing" : ""}` }, d.text, d.unit ? h("span", { class: "unit", title: unitTitle(d.rawUnit) || null }, d.unit) : null),
    d.asFiled ? h("div", { class: "metric-sub" }, "As filed: no normalised value") : null,
    valueBadges(v));
}

// category x gender table built from the API's gender_grid
export function genderGridTable(group) {
  const genders = [];
  for (const row of group.gender_grid) for (const g of Object.keys(row.cells)) if (!genders.includes(g)) genders.push(g);
  // the axis total last, other members in the order the filing uses
  genders.sort((a, b) => (a === "Total") - (b === "Total"));
  const byKey = new Map(group.values.map((v) => [`${v.breakdown}`, v]));
  const units = [...new Set(group.values.map((v) => v.unit ? unitLabel(v.unit) : unitLabel(v.reported_unit, true)).filter(Boolean))];
  const caption = h("div", { class: "small muted", style: "margin:-4px 0 8px" },
    `Unit: ${units.join(", ") || "not stated"} · select a value for its source`);
  const valueFor = (row, g) => {
    const c = row.cells[g];
    if (!c) return { c: null, v: null };
    const v = group.values.find((x) => x.source.context_reference === c.context_reference
      && x.dimensions.some((d) => d.axis === "GenderAxis" && d.label === g)) || byKey.get("");
    return { c, v };
  };
  const cellButton = ({ c, v }) => {
    if (!c) return h("span", { class: "muted" }, "Not available");
    const zero = c.disclosure_status === "REPORTED_ZERO";
    return h("button", { class: "link-btn", style: zero ? "color: var(--zero-fg)" : null,
      title: zero ? "Reported zero — open details" : "Open details", onclick: () => v && openDrawer(v) },
      c.value === null ? "Not available" : formatNumber(c.value));
  };
  // narrow screens: one card per category, total first, genders below
  const cardGenders = [...genders].sort((a, b) => (b === "Total") - (a === "Total"));
  const cards = h("div", { class: "gender-cards" }, group.gender_grid.map((row) =>
    h("div", { class: "gender-card" }, h("div", { class: "metric-name" }, row.category_label),
      h("dl", {}, cardGenders.map((g) => [h("dt", {}, g), h("dd", {}, cellButton(valueFor(row, g)))])))));
  return h("div", {}, caption, cards, h("div", { class: "table-wrap gender-table" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Category"), genders.map((g) => h("th", { class: "num" }, g)))),
    h("tbody", {}, group.gender_grid.map((row) => h("tr", {},
      h("td", { class: "metric-name" }, row.category_label),
      genders.map((g) => {
        const cv = valueFor(row, g);
        return cv.c ? h("td", { class: "num" }, cellButton(cv)) : h("td", { class: "num missing" }, "Not available");
      })))))));
}

// ---------------------------------------------------------------- drawer
const kv = (pairs) => h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== undefined).map(([k, v]) => [
  h("dt", {}, k), h("dd", {}, v === null || v === "" ? h("span", { class: "muted" }, "—") : v)]));

export function openDrawer(v) {
  const drawer = document.getElementById("drawer");
  const back = document.getElementById("drawer-backdrop");
  clear(drawer);
  const d = displayValue(v);
  const s = v.source || {};
  const r = v.review || {};
  const close = () => { drawer.classList.remove("open"); back.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); };
  back.onclick = close;
  append(drawer, [
    h("button", { class: "close", onclick: close, "aria-label": "Close" }, "×"),
    h("h2", {}, v.metric_name),
    h("div", { class: "muted" }, [v.requirement_id, fy(v.fiscal_year), breakdownLabel(v.dimensions)].filter(Boolean).join(" · ")),
    h("div", { style: "margin-top:10px" }, valueBadges(v)),
    h("h3", {}, "Value"),
    kv([["Normalised value", v.value !== null && v.value !== undefined ? (v.value_numeric !== null ? formatNumber(v.value) : v.value) : null],
        ["Normalised unit", v.unit ? `${v.unit}${unitLabel(v.unit) !== v.unit ? ` (shown as ${unitLabel(v.unit)})` : ""}` : null],
        ["Reported value", v.reported_value !== undefined ? (formatNumber(v.reported_value) ?? v.reported_value) : undefined],
        ["Reported unit (exact, as filed)", v.reported_unit !== undefined ? (v.reported_unit
          ? `${v.reported_unit}${unitTitle(v.reported_unit) !== v.reported_unit ? ` — ${unitTitle(v.reported_unit).split(" — ")[1]}` : ""}`
          : "no unit in the filing") : undefined],
        ["Disclosure status", badge(DISCLOSURE, v.disclosure_status)],
        ["Value basis", v.value_basis ? badge(BASIS, v.value_basis) : undefined],
        ["Normalisation status", v.normalisation_status],
        ["Note", v.normalisation_note],
        ["Period", v.period ? [v.period.start, v.period.end].filter(Boolean).join(" to ") || v.period.instant : undefined]]),
    v.calculation ? [
      h("h3", {}, "Calculation"),
      h("div", { class: "formula" }, v.calculation.formula || "—"),
      kv([["Factor", v.calculation.factor], ["Basis", v.calculation.multiplier_basis], ["Decision", v.calculation.decision]]),
      h("div", { class: "small muted", style: "margin-top:6px" }, "Inputs:"),
      v.calculation.inputs.map((i) => h("div", { class: "small" },
        `${i.concept}: ${formatNumber(i.value) ?? i.value} ${i.unit || ""} (context ${i.context_reference})`))] : null,
    h("h3", {}, "Source filing"),
    kv([["Filing ID", s.filing_id], ["XBRL concept", s.concept], ["Context reference", s.context_reference],
        ["Source file", s.source_file], ["Taxonomy version", s.taxonomy_version], ["Submitted", s.submission_date],
        ["Version", s.filing_version_status], ["XBRL", safeLink(s.xbrl_url, "Open XBRL instance")],
        ["PDF", s.pdf_url ? safeLink(s.pdf_url, "Open BRSR PDF on NSE") : null]]),
    h("h3", {}, "Review flags"),
    kv([["Pending review", r.pending_review], ["Deferred issues", (r.deferred_issue_ids || []).join(", ")],
        ["Crosswalk status", r.crosswalk_status], ["Unit decision", r.unit_decision]]),
    d.asFiled ? h("p", { class: "note" }, "No normalised value: the filed unit is missing, ambiguous or awaiting an owner decision. The filed value is shown as it appears in the filing.") : null]);
  drawer.classList.add("open"); back.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  drawer.querySelector(".close").focus();
}

export function closeDrawer() {
  document.getElementById("drawer")?.classList.remove("open");
  document.getElementById("drawer-backdrop")?.classList.remove("open");
  document.getElementById("drawer")?.setAttribute("aria-hidden", "true");
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
