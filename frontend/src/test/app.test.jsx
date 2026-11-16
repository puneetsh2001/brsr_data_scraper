// App-level behaviour with a mocked API (synthetic test data only).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { App } from "../App.jsx";

const company = {
  company: { company_id: "C-TEST", legal_name: "Test Company Ltd", nse_symbol: "TEST", isin: "INE000T00000",
    bse_scrip_code: "500000", cin: "", industry: "Testing", company_status: "LISTED_NSE" },
  identifiers: [],
  years: [{ fiscal_year: "2024-25", status: "PROCESSED", versions: 1, failed_filings: [],
    filing: { filing_id: "F-T", reporting_period_start: "2024-04-01", reporting_period_end: "2025-03-31",
      submission_date: "2025-06-01", taxonomy_version: "2025-05-31", filing_version_status: "ORIGINAL",
      xbrl_url: "https://example.org/x.xml", pdf_url: null } }],
};
const routes = {
  "/api/metrics": { metrics: [], themes: [], pillars: [] },
  "/api/companies/C-TEST": company,
  "/api/companies/C-TEST/years/2024-25/metrics": { metrics: [], missing: [
    { requirement_id: "E07", metric_key: "energy_total", metric_name: "Total energy consumption",
      disclosure_status: "NO_VALUE_IN_FILING", missing_reason: "no fact" }] },
  "/api/companies/C-TEST/years/2024-25/workforce": { metrics: [], missing: [] },
};

function mockFetch(fail = false) {
  globalThis.fetch = vi.fn(async (url) => {
    const path = new URL(url, "http://localhost").pathname;
    if (fail) throw new TypeError("Failed to fetch");
    if (!(path in routes)) return { ok: false, status: 404, json: async () => ({ detail: `no route ${path}` }) };
    return { ok: true, status: 200, json: async () => routes[path] };
  });
}

describe("App", () => {
  beforeEach(() => { window.location.hash = ""; });
  afterEach(() => { vi.restoreAllMocks(); });

  it("renders a company-year overview from the API, missing values as 'Not available'", async () => {
    mockFetch();
    window.location.hash = "#/c/C-TEST/2024-25/overview";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Test Company Ltd" })).toBeInTheDocument();
    expect(await screen.findByText("Total energy consumption")).toBeInTheDocument();
    expect(screen.getAllByText("Not available").length).toBeGreaterThan(0);
    expect(screen.getByText("No value in filing")).toBeInTheDocument();
    expect(document.querySelector("#fy-select").value).toBe("2024-25");
  });

  it("redirects a company without a year to its latest processed year", async () => {
    mockFetch();
    window.location.hash = "#/c/C-TEST//overview";
    render(<App />);
    await waitFor(() => expect(window.location.hash).toBe("#/c/C-TEST/2024-25/overview"));
  });

  it("shows an error with retry when the API is unreachable", async () => {
    mockFetch(true);
    // a company not requested before (the API client caches per session)
    window.location.hash = "#/c/C-UNREACHABLE/2024-25/overview";
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not reachable");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
