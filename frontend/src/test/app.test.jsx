// Routing and app behaviour with a mocked API (synthetic test data only).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router";
import { AppRoutes } from "../App.jsx";

const filing = (fyLabel) => ({ filing_id: `F-${fyLabel}`, reporting_period_start: "2024-04-01", reporting_period_end: "2025-03-31",
  submission_date: "2025-06-01", taxonomy_version: "2025-05-31", filing_version_status: "ORIGINAL",
  xbrl_url: "https://example.org/x.xml", pdf_url: null });
const company = {
  company: { company_id: "C-TEST", legal_name: "Test Company Ltd", nse_symbol: "TEST", isin: "INE000T00000",
    bse_scrip_code: "500000", cin: "", industry: "Testing", company_status: "LISTED_NSE" },
  identifiers: [],
  years: ["2023-24", "2024-25"].map((y) => ({ fiscal_year: y, status: "PROCESSED", versions: 1, failed_filings: [], filing: filing(y) })),
};
const empty = { metrics: [], missing: [] };
const routes = {
  "/api/metrics": { metrics: [{ requirement_id: "E07", pillar: "Environmental", metric_name: "Total energy consumption",
    metric_key: "energy_total", in_brsr_xbrl: true, breakdown_axes: [], themes: ["energy"] }], themes: [], pillars: [] },
  "/api/companies/C-TEST": company,
  "/api/companies/C-TEST/years/2024-25/metrics": { metrics: [], missing: [
    { requirement_id: "E07", metric_key: "energy_total", metric_name: "Total energy consumption",
      disclosure_status: "NO_VALUE_IN_FILING", missing_reason: "no fact" }] },
  "/api/companies/C-TEST/years/2023-24/metrics": empty,
  "/api/companies/C-TEST/years/2024-25/workforce": empty,
  "/api/companies/C-TEST/years/2023-24/workforce": empty,
  "/api/companies/C-TEST/years/2024-25/pillars/environmental": empty,
  "/api/companies/C-TEST/metrics/E07/history": { metric: { requirement_id: "E07", metric_name: "Total energy consumption" },
    series: [], units: [], comparable: true, dimensions: [] },
};

function mockFetch(fail = false) {
  globalThis.fetch = vi.fn(async (url) => {
    const path = new URL(url, "http://localhost").pathname;
    if (fail) throw new TypeError("Failed to fetch");
    if (!(path in routes)) return { ok: false, status: 404, json: async () => ({ detail: `no route ${path}` }) };
    return { ok: true, status: 200, json: async () => routes[path] };
  });
}

let current = null;
function Probe() {
  const location = useLocation();
  const navigate = useNavigate();
  current = { location, navigate };
  return null;
}

function renderAt(...entries) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
      <AppRoutes />
      <Probe />
    </MemoryRouter>,
  );
}

const path = () => current.location.pathname + current.location.search;

describe("routing", () => {
  beforeEach(() => { mockFetch(); window.location.hash = ""; });
  afterEach(() => { vi.restoreAllMocks(); });

  it("opens a deep link to a company, year and tab", async () => {
    renderAt("/c/C-TEST/2024-25/overview");
    expect(await screen.findByRole("heading", { name: "Test Company Ltd" })).toBeInTheDocument();
    expect(await screen.findByText("No value in filing")).toBeInTheDocument();
    expect(document.querySelector("#fy-select").value).toBe("2024-25");
    expect(document.querySelector('#tabs a[data-tab="overview"]')).toHaveClass("active");
  });

  it("resolves /c/:companyId to the latest processed year (nested index route)", async () => {
    renderAt("/c/C-TEST");
    await waitFor(() => expect(path()).toBe("/c/C-TEST/2024-25/overview"));
    expect(await screen.findByRole("heading", { name: "Test Company Ltd" })).toBeInTheDocument();
  });

  it("resolves /c/:companyId/:fy to the overview tab and keeps query parameters", async () => {
    renderAt("/c/C-TEST/2023-24?metric=E07");
    await waitFor(() => expect(path()).toBe("/c/C-TEST/2023-24/overview?metric=E07"));
  });

  it("passes query parameters to the screen", async () => {
    renderAt("/c/C-TEST/2024-25/trends?metric=E07&breakdown=");
    expect(await screen.findByText("Historical trend · Test Company Ltd")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Metric" })).toHaveValue("E07"));
  });

  it("navigates between tabs and supports back and forward", async () => {
    renderAt("/c/C-TEST/2024-25/overview");
    await screen.findByRole("heading", { name: "Test Company Ltd" });
    await userEvent.click(document.querySelector('#tabs a[data-tab="environmental"]'));
    await waitFor(() => expect(path()).toBe("/c/C-TEST/2024-25/environmental"));
    expect(await screen.findByText("Environmental · FY 2024-25")).toBeInTheDocument();
    current.navigate(-1);
    await waitFor(() => expect(path()).toBe("/c/C-TEST/2024-25/overview"));
    current.navigate(1);
    await waitFor(() => expect(path()).toBe("/c/C-TEST/2024-25/environmental"));
  });

  it("changes the financial year from the selector, keeping the tab", async () => {
    renderAt("/c/C-TEST/2024-25/environmental");
    await screen.findByText("Environmental · FY 2024-25");
    await userEvent.selectOptions(document.querySelector("#fy-select"), "2023-24");
    await waitFor(() => expect(path()).toBe("/c/C-TEST/2023-24/environmental"));
  });

  it("shows 'page not found' for an unknown tab", async () => {
    renderAt("/c/C-TEST/2024-25/nonsense");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });

  it("shows 'page not found' for an unknown path", async () => {
    renderAt("/no/such/page");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });

  it("explains an unavailable financial year", async () => {
    renderAt("/c/C-TEST/1999-00/overview");
    expect(await screen.findByText("FY 1999-00 is not available for this company.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open FY 2024-25" })).toBeInTheDocument();
  });

  it("redirects bookmarks of the previous hash URLs", async () => {
    window.location.hash = "#/c/C-TEST/2024-25/overview";
    renderAt("/");
    await waitFor(() => expect(path()).toBe("/c/C-TEST/2024-25/overview"));
  });
});

describe("errors", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  it("shows an error with retry when the API is unreachable", async () => {
    mockFetch(true);
    // a company not requested before (the API client caches per session)
    renderAt("/c/C-UNREACHABLE/2024-25/overview");
    expect(await screen.findByRole("alert")).toHaveTextContent("not reachable");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
