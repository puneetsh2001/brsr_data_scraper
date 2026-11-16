// App shell with React Router (browser history, basename /app).
//
// Routes (nested):
//   /                         LandingPage
//   /c/:companyId             CompanyLayout -> index: latest processed year
//     :fy                     YearLayout    -> index: overview tab
//       :tab                  TabRoute      (unknown tab -> not found)
//   *                         NotFoundPage
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BrowserRouter, Link, Navigate, Outlet, Route, Routes, useLocation, useMatch,
  useNavigate, useOutletContext, useParams, useSearchParams,
} from "react-router";
import { api } from "./lib/api.js";
import { fy } from "./lib/format.js";
import { DrawerContext } from "./lib/drawer.js";
import { TAB_KEYS, companyPath, useGo } from "./lib/paths.js";
import { useAsync } from "./lib/useAsync.js";
import { Empty, ErrorState, Loading } from "./components/common.jsx";
import { Drawer } from "./components/Drawer.jsx";
import { SearchBox } from "./components/SearchBox.jsx";
import { Landing } from "./views/Landing.jsx";
import { Overview } from "./views/Overview.jsx";
import { Environmental, Governance, Social } from "./views/Pillars.jsx";
import { Trends } from "./views/Trends.jsx";
import { Compare } from "./views/Compare.jsx";
import { Quality } from "./views/Quality.jsx";

const TABS = {
  overview: ["Overview", Overview],
  environmental: ["Environmental", Environmental],
  social: ["Social", Social],
  governance: ["Governance", Governance],
  trends: ["Trends", Trends],
  compare: ["Compare", Compare],
  quality: ["Data quality", Quality],
};

// ---------------------------------------------------------------- frame
function Header({ company, year, tab }) {
  const go = useGo();
  const [searchParams] = useSearchParams();
  const years = company ? company.years : [];
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <div>
          <div className="brand-title"><Link to="/" style={{ color: "inherit", textDecoration: "none" }}>BRSR Analytics</Link></div>
          <div className="brand-sub">SEBI BRSR disclosures from NSE XBRL filings</div>
        </div>
      </div>
      <div className="selectors">
        <SearchBox id="company-search" placeholder="Search company, NSE symbol, ISIN, BSE code or CIN…"
          ariaLabel="Search company" limit={15} delay={180}
          renderMeta={(c) => [c.nse_symbol, c.bse_scrip_code].filter(Boolean).join(" · ")}
          onSelect={(c) => go(c.company_id)} />
        <label className="fy-select">
          <span>Financial year</span>
          <select id="fy-select" disabled={!years.length} value={year ? year.fiscal_year : ""}
            onChange={(e) => go(company.company.company_id, e.target.value, tab || "overview",
              Object.fromEntries(searchParams))}>
            {years.length ? [...years].reverse().map((y) => (
              <option value={y.fiscal_year} key={y.fiscal_year}>
                {`${fy(y.fiscal_year)}${y.status === "PROCESSED" ? "" : ` (${y.status.replace(/_/g, " ").toLowerCase()})`}`}
              </option>
            )) : <option value="">—</option>}
          </select>
        </label>
      </div>
    </header>
  );
}

function Tabs({ company, year, tab }) {
  const enabled = !!(company && year);
  return (
    <nav className="tabs" id="tabs" aria-label="Sections">
      {TAB_KEYS.map((key) => (enabled ? (
        <Link key={key} data-tab={key} to={companyPath(company.company.company_id, year.fiscal_year, key)}
          className={tab === key ? "active" : ""} aria-current={tab === key ? "page" : undefined}>{TABS[key][0]}</Link>
      ) : (
        <a key={key} data-tab={key} href="#" className="disabled" aria-disabled="true"
          onClick={(e) => e.preventDefault()}>{TABS[key][0]}</a>
      )))}
    </nav>
  );
}

function Frame({ company = null, year = null, tab = null, children }) {
  return (
    <>
      <Header company={company} year={year} tab={tab} />
      <Tabs company={company} year={year} tab={tab} />
      <main id="view" className="view" tabIndex={-1}>{children}</main>
      <footer className="footer">
        Values are shown as filed in the BRSR XBRL filing. Missing values are never shown as zero.
        {" "}Normalised values follow the platform&apos;s approved unit rules; unit-ambiguous values are shown as filed, without a normalised value.
      </footer>
    </>
  );
}

// ---------------------------------------------------------------- pages
function LandingPage() {
  const navigate = useNavigate();
  useEffect(() => {
    // bookmarks of the previous hash-based URLs (#/c/...) open the same screen
    const h = window.location.hash;
    if (h.startsWith("#/c/")) navigate(h.slice(1).replace(/\/\/overview/, ""), { replace: true });
  }, [navigate]);
  useEffect(() => { document.title = "BRSR Analytics"; }, []);
  return <Frame><Landing /></Frame>;
}

function NotFoundContent({ company }) {
  return (
    <div className="state error" role="alert">
      <div className="metric-name">Page not found</div>
      <div>This address does not match a screen of the application.</div>
      <p>{company
        ? <Link to={companyPath(company.company.company_id)}>{`Open ${company.company.legal_name}`}</Link>
        : <Link to="/">Go to the company list</Link>}</p>
    </div>
  );
}

function NotFoundPage() {
  useEffect(() => { document.title = "Not found · BRSR Analytics"; }, []);
  return <Frame><NotFoundContent /></Frame>;
}

// /c/:companyId — loads the metric catalog and the company record
function CompanyLayout() {
  const { companyId } = useParams();
  const fyMatch = useMatch("/c/:companyId/:fy/*");
  const tabMatch = useMatch("/c/:companyId/:fy/:tab");
  const catalog = useAsync(async () => {
    const cat = await api.catalog();
    return new Map(cat.metrics.map((m) => [m.requirement_id, m]));
  }, "catalog");
  const company = useAsync(() => api.company(companyId), companyId);

  if (catalog.error || company.error) {
    const err = company.error || catalog.error;
    return <Frame><ErrorState error={err} onRetry={() => { if (company.error) company.reload(); if (catalog.error) catalog.reload(); }} /></Frame>;
  }
  if (!catalog.data || !company.data) return <Frame><Loading /></Frame>;
  const co = company.data;
  if (!co.years.length) {
    return <Frame company={co}><Empty>No in-scope BRSR filings are recorded for this company.</Empty></Frame>;
  }
  const fyParam = fyMatch ? fyMatch.params.fy : null;
  const year = fyParam ? co.years.find((y) => y.fiscal_year === fyParam) || null : null;
  const tab = tabMatch ? tabMatch.params.tab : null;
  return (
    <Frame company={co} year={year} tab={tab}>
      <Outlet context={{ catalog: catalog.data, company: co }} />
    </Frame>
  );
}

function latestYear(co) {
  const processed = co.years.filter((y) => y.status === "PROCESSED");
  return processed[processed.length - 1] || co.years[co.years.length - 1];
}

// /c/:companyId -> latest processed year, overview
function DefaultYear() {
  const { company } = useOutletContext();
  return <Navigate replace to={companyPath(company.company.company_id, latestYear(company).fiscal_year, "overview")} />;
}

// /c/:companyId/:fy — validates the financial year
function YearLayout() {
  const ctx = useOutletContext();
  const { fy: fyParam } = useParams();
  const year = ctx.company.years.find((y) => y.fiscal_year === fyParam);
  if (!year) {
    const fallback = latestYear(ctx.company);
    return (
      <>
        <ErrorState error={{ status: 404, message: `${fy(fyParam)} is not available for this company.` }} />
        <p className="state"><Link to={companyPath(ctx.company.company.company_id, fallback.fiscal_year, "overview")}>{`Open ${fy(fallback.fiscal_year)}`}</Link></p>
      </>
    );
  }
  return <Outlet context={{ ...ctx, year }} />;
}

// /c/:companyId/:fy -> overview (query parameters are kept)
function DefaultTab() {
  const { company, year } = useOutletContext();
  const { search } = useLocation();
  return <Navigate replace to={`${companyPath(company.company.company_id, year.fiscal_year, "overview")}${search}`} />;
}

// /c/:companyId/:fy/:tab
function TabRoute() {
  const { catalog, company, year } = useOutletContext();
  const { tab } = useParams();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const go = useGo();
  useEffect(() => {
    document.title = `${company.company.legal_name} · ${fy(year.fiscal_year)} · BRSR Analytics`;
  }, [company, year]);
  if (!TABS[tab]) return <NotFoundContent company={company} />;
  const View = TABS[tab][1];
  return (
    <View key={`${location.pathname}${location.search}`} state={{ catalog, company, year }} params={searchParams}
      navigate={(params, opts) => go(company.company.company_id, year.fiscal_year, tab, params, opts)} />
  );
}

// ---------------------------------------------------------------- drawer
function DrawerProvider({ children }) {
  const location = useLocation();
  const [value, setValue] = useState(null);
  const close = useCallback(() => setValue(null), []);
  const ctx = useMemo(() => ({ open: setValue, close }), [close]);
  useEffect(() => { setValue(null); }, [location.pathname, location.search]);
  return (
    <DrawerContext.Provider value={ctx}>
      {children}
      <Drawer value={value} onClose={close} />
    </DrawerContext.Provider>
  );
}

export function AppRoutes() {
  return (
    <DrawerProvider>
      <Routes>
        <Route index element={<LandingPage />} />
        <Route path="c/:companyId" element={<CompanyLayout />}>
          <Route index element={<DefaultYear />} />
          <Route path=":fy" element={<YearLayout />}>
            <Route index element={<DefaultTab />} />
            <Route path=":tab" element={<TabRoute />} />
          </Route>
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </DrawerProvider>
  );
}

export function App() {
  return (
    <BrowserRouter basename="/app">
      <AppRoutes />
    </BrowserRouter>
  );
}
