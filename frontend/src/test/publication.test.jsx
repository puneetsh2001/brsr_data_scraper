// Stage 2D: publication safeguards in the UI. Only comparable values are
// drawn in charts; others are shown as filed with their publication status.
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { DrawerContext } from "../lib/drawer.js";
import { displayValue, isComparable } from "../lib/format.js";
import { ValueBadges } from "../components/common.jsx";
import { Drawer } from "../components/Drawer.jsx";
import { MetricTable } from "../components/metrics.jsx";
import { ComparePanel } from "../views/Compare.jsx";

const value = (over = {}) => ({
  requirement_id: "E08", metric_name: "Energy intensity", concept: "C", fiscal_year: "2023-24",
  breakdown: "", dimensions: [], value: "0.0000005", value_numeric: 0.0000005, unit: "GJ/INR",
  reported_value: "0.0000005", reported_unit: "Non-SI:GJ / iso4217:INR", disclosure_status: "REPORTED",
  value_basis: "REPORTED_AS_FILED", publication_status: "PUBLISHED", comparable: true,
  review: { pending_review: null, deferred_issue_ids: [], crosswalk_status: "UNCHANGED" },
  source: { filing_id: "F-1", context_reference: "CY", xbrl_url: "https://x/a.xml" }, ...over });

const fse = () => value({
  value: null, value_numeric: null, unit: null, reported_value: "0.122", reported_unit: "Non-SI:TJ / iso4217:INR",
  publication_status: "PENDING_SCALE_REVIEW", comparable: false,
  withheld: { value: "122", unit: "GJ/INR", reason: "rule-normalised value of a filed figure under scale review" },
  scale_review: { record_id: "FSE-001", status: "PENDING_NOT_APPLIED", recommended_decision: "APPROVE",
    proposed_value: "0.0000122", note: "proposal only; not applied" } });

const deferred = () => value({ publication_status: "DEFERRED_DATA_QUALITY", comparable: false,
  review: { pending_review: null, deferred_issue_ids: ["DQ-007"], crosswalk_status: "UNCHANGED" } });

const withDrawer = (ui) => render(<DrawerContext.Provider value={{ open: vi.fn(), close: vi.fn() }}>{ui}</DrawerContext.Provider>);

describe("isComparable", () => {
  it("needs the API flag and a numeric value", () => {
    expect(isComparable(value())).toBe(true);
    expect(isComparable(value({ comparable: false }))).toBe(false);
    expect(isComparable(value({ value_numeric: null }))).toBe(false);
    expect(isComparable(deferred())).toBe(false);
    expect(isComparable(null)).toBe(false);
  });
});

describe("value display", () => {
  it("shows a suspected scale error as filed, not as the normalised number", () => {
    const d = displayValue(fse());
    expect(d).toMatchObject({ text: "0.122", asFiled: true, missing: false });
    expect(d.text).not.toBe("122");
  });
  it("a withheld deferred value says Withheld, never zero or Not available", () => {
    const d = displayValue(value({ value: null, value_numeric: null, reported_value: null, comparable: false,
      publication_status: "DEFERRED_DATA_QUALITY", withheld: { reason: "owner policy WITHHOLD" } }));
    expect(d).toMatchObject({ text: "Withheld", withheld: true, missing: false });
  });
  it("labels non-published statuses with a badge", () => {
    render(<ValueBadges v={fse()} />);
    expect(screen.getByText("Suspected scale error — pending decision")).toBeInTheDocument();
  });
  it("labels deferred values and does not show them as published", () => {
    render(<ValueBadges v={deferred()} />);
    expect(screen.getByText("Deferred data-quality issue")).toBeInTheDocument();
  });
  it("a reported zero stays distinct from a missing value", () => {
    withDrawer(<MetricTable groups={[{ requirement_id: "S19", metric_name: "Complaints", values: [value({
      value: "0", value_numeric: 0, unit: "count", disclosure_status: "REPORTED_ZERO" })] }]} />);
    expect(screen.getByText("Reported zero")).toBeInTheDocument();
    expect(screen.queryByText("Not available")).toBeNull();
  });
});

describe("drawer", () => {
  it("shows the publication status, chart eligibility and the scale-review proposal", () => {
    render(<Drawer value={fse()} onClose={vi.fn()} />);
    const drawer = document.getElementById("drawer");
    expect(within(drawer).getByText("Used in charts and comparisons").nextSibling).toHaveTextContent("No");
    expect(within(drawer).getByText(/FSE-001/)).toHaveTextContent("proposal only; not applied");
    expect(within(drawer).getByText(/rule-normalised value/)).toHaveTextContent("122");
  });
});

describe("compare chart", () => {
  const company = (sym) => ({ company_id: `C-${sym}`, nse_symbol: sym, legal_name: `${sym} Ltd` });
  const data = (vals) => ({
    metric: { requirement_id: "E08", metric_name: "Energy intensity" }, dimensions: [], fiscal_year: "2023-24",
    comparable: true, units: ["GJ/INR"],
    companies: vals.map(([sym, v]) => ({ company: company(sym), years: [{ fiscal_year: "2023-24", values: [v], missing: null }] })),
  });

  it("draws only comparable values; FSE and deferred values are labelled, not drawn", () => {
    withDrawer(<ComparePanel data={data([["ADANIPORTS", fse()], ["TCS", value()], ["POWERGRID", deferred()]])} />);
    const svg = document.querySelector(".chart svg");
    expect(svg.querySelectorAll("rect.bar")).toHaveLength(1);
    expect(svg).toHaveTextContent("not comparable: suspected scale error — pending decision");
    expect(svg).toHaveTextContent("not comparable: deferred data-quality issue");
  });

  it("shows no chart when no selected company has a comparable value", () => {
    withDrawer(<ComparePanel data={data([["ADANIPORTS", fse()]])} />);
    expect(document.querySelector(".chart")).toBeNull();
    expect(screen.getByText(/no selected company has a comparable value/)).toBeInTheDocument();
  });
});
