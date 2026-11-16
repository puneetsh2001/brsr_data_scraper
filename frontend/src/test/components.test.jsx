import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DrawerContext } from "../lib/drawer.js";
import { BarChart } from "../components/BarChart.jsx";
import { GenderGrid, MetricCard, MetricTable, MissingTable } from "../components/metrics.jsx";
import { BreakdownSelect } from "../views/shared.jsx";

const value = (over = {}) => ({
  requirement_id: "E07", metric_name: "Total energy consumption", concept: "C", fiscal_year: "2024-25",
  breakdown: "", dimensions: [], value: "100", value_numeric: 100, unit: "GJ", reported_value: "100",
  reported_unit: "Non-SI:GJ", disclosure_status: "REPORTED", value_basis: "REPORTED_AS_FILED",
  review: { pending_review: null, deferred_issue_ids: [], crosswalk_status: "UNCHANGED" },
  source: { filing_id: "F-1", context_reference: "CY", xbrl_url: "https://x/a.xml" }, ...over });

const withDrawer = (ui, open = vi.fn()) => render(<DrawerContext.Provider value={{ open, close: vi.fn() }}>{ui}</DrawerContext.Provider>);

describe("MetricTable", () => {
  it("shows value, display unit and status; a click opens the drawer", async () => {
    const open = vi.fn();
    withDrawer(<MetricTable groups={[{ requirement_id: "E07", metric_name: "Total energy consumption", values: [value()] }]} />, open);
    const row = screen.getByText("Total energy consumption").closest("tr");
    expect(within(row).getByText("100")).toBeInTheDocument();
    expect(within(row).getByText("GJ")).toBeInTheDocument();
    expect(within(row).getByText("Reported")).toBeInTheDocument();
    await userEvent.click(row);
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ requirement_id: "E07" }));
  });
  it("shows an unnormalised value as filed, never as a normalised number", () => {
    withDrawer(<MetricTable groups={[{ requirement_id: "E07", metric_name: "Energy", values: [value({
      value: null, value_numeric: null, unit: null, reported_value: "473590585", reported_unit: "xbrli:pure",
      value_basis: "REPORTED_NOT_NORMALISED" })] }]} />);
    const row = screen.getByText("Energy").closest("tr");
    expect(within(row).getByText("as filed")).toBeInTheDocument();
    expect(within(row).getByText("no unit stated")).toBeInTheDocument();
    expect(within(row).getByText("Not normalised")).toBeInTheDocument();
  });
  it("labels a filed zero as reported zero", () => {
    withDrawer(<MetricTable groups={[{ requirement_id: "S19", metric_name: "Complaints filed", values: [value({
      value: "0", value_numeric: 0, unit: "count", disclosure_status: "REPORTED_ZERO" })] }]} />);
    expect(screen.getByText("Reported zero")).toBeInTheDocument();
  });
});

describe("MissingTable and MetricCard", () => {
  it("shows the reason and never a number", () => {
    render(<MissingTable missing={[{ requirement_id: "E20", metric_name: "Scope 3 emissions", disclosure_status: "NOT_APPLICABLE", missing_reason: "applicability = false" }]} />);
    expect(screen.getByText("Not applicable")).toBeInTheDocument();
    expect(screen.getByText("applicability = false")).toBeInTheDocument();
  });
  it("collapses metrics missing for the same reason into one row", () => {
    const m = (id) => ({ requirement_id: id, metric_name: `M ${id}`, disclosure_status: "EXTRACTION_ERROR", missing_reason: "404" });
    render(<MissingTable missing={[m("E01"), m("E02"), m("E03")]} />);
    expect(screen.getByText("3 metrics")).toBeInTheDocument();
    expect(screen.getAllByText("Extraction error")).toHaveLength(1);
  });
  it("missing card shows 'Not available', not zero", () => {
    withDrawer(<MetricCard missing={{ metric_name: "Scope 3 emissions", disclosure_status: "NOT_APPLICABLE" }} />);
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(screen.queryByText("0")).toBeNull();
  });
});

describe("BarChart", () => {
  it("draws bars only for numeric values and labels gaps", () => {
    const { container } = render(<BarChart unit="GJ" items={[
      { label: "FY 2023-24", value: 5, text: "5" }, { label: "FY 2022-23", value: null, note: "not normalised" }]} />);
    expect(container.querySelectorAll("rect.bar")).toHaveLength(1);
    expect(container.querySelector(".gap-label").textContent).toBe("not normalised");
  });
});

describe("GenderGrid", () => {
  it("renders a table and narrow-screen cards with the total first", () => {
    const v = (g, n) => value({ requirement_id: "S01", breakdown: `A=EmployeesMember | GenderAxis=${g}Member`,
      dimensions: [{ axis: "A", member: "EmployeesMember", label: "Employees" }, { axis: "GenderAxis", member: `${g}Member`, label: g }],
      value: n, value_numeric: Number(n), unit: "count", source: { context_reference: `C_${g}` } });
    const group = { requirement_id: "S01", values: [v("Male", "6"), v("Female", "4"), v("Gender", "10")],
      gender_grid: [{ category: "A=EmployeesMember", category_label: "Employees", cells: {
        Male: { value: "6", disclosure_status: "REPORTED", context_reference: "C_Male" },
        Female: { value: "4", disclosure_status: "REPORTED", context_reference: "C_Female" },
        Total: { value: "10", disclosure_status: "REPORTED", context_reference: "C_Gender" } } }] };
    const { container } = withDrawer(<GenderGrid group={group} />);
    const head = [...container.querySelectorAll(".gender-table thead th")].map((t) => t.textContent);
    expect(head).toEqual(["Category", "Male", "Female", "Total"]);
    expect([...container.querySelectorAll(".gender-card dt")].map((t) => t.textContent)[0]).toBe("Total");
  });
});

describe("BreakdownSelect", () => {
  it("is disabled for a metric without breakdowns", () => {
    render(<BreakdownSelect options={[["", "Total (no breakdown)"]]} value="" onChange={() => {}} />);
    expect(screen.getByRole("combobox")).toBeDisabled();
    expect(screen.getByText("No breakdown (single value)")).toBeInTheDocument();
  });
  it("lists reported breakdowns", () => {
    render(<BreakdownSelect options={[["a", "Employees / Male"], ["b", "Employees / Female"]]} value="b" onChange={() => {}} />);
    expect(screen.getByRole("combobox")).toHaveValue("b");
  });
});
