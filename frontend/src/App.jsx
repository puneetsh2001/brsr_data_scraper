// App shell: hash router, company search, FY selector, tabs and drawer.
// Route: #/c/<company_id>/<fy>/<tab>?<params>
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./lib/api.js";
import { fy } from "./lib/format.js";
import { DrawerContext } from "./lib/drawer.js";
import { routeHash, routeTo, useHashRoute } from "./lib/router.js";
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

const TABS = [
  ["overview", "Overview", Overview],
  ["environmental", "Environmental", Environmental],
  ["social", "Social", Social],
  ["governance", "Governance", Governance],
  ["trends", "Trends", Trends],
  ["compare", "Compare", Compare],
  ["quality", "Data quality", Quality],
];

function Header({ company, year, route }) {
  const years = company ? company.years : [];
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <div>
          <div className="brand-title">BRSR Analytics</div>
          <div className="brand-sub">SEBI BRSR disclosures from NSE XBRL filings</div>
        </div>
      </div>
      <div className="selectors">
        <SearchBox id="company-search" placeholder="Search company, NSE symbol, ISIN, BSE code or CIN…"
          ariaLabel="Search company" limit={15} delay={180}
          renderMeta={(c) => [c.nse_symbol, c.bse_scrip_code].filter(Boolean).join(" · ")}
          onSelect={(c) => routeTo(c.company_id, "", "overview")} />
        <label className="fy-select">
          <span>Financial year</span>
          <select id="fy-select" disabled={!years.length} value={year ? year.fiscal_year : ""}
            onChange={(e) => routeTo(company.company.company_id, e.target.value, route.tab, Object.fromEntries(route.params))}>
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

function Tabs({ company, year, route }) {
  const enabled = !!company;
  return (
    <nav className="tabs" id="tabs" aria-label="Sections">
      {TABS.map(([key, label]) => (
        <a key={key} data-tab={key}
          href={enabled ? routeHash(company.company.company_id, year?.fiscal_year || "", key) : "#"}
          className={`${route.tab === key && enabled ? "active" : ""} ${enabled ? "" : "disabled"}`}>{label}</a>
      ))}
    </nav>
  );
}

export function App() {
  const { hash, route } = useHashRoute();
  const isCompany = route.kind === "c" && !!route.id;
  const [drawerValue, setDrawerValue] = useState(null);
  const closeDrawer = useCallback(() => setDrawerValue(null), []);
  const drawer = useMemo(() => ({ open: setDrawerValue, close: closeDrawer }), [closeDrawer]);
  useEffect(() => { setDrawerValue(null); }, [hash]);

  const catalog = useAsync(async () => {
    const cat = await api.catalog();
    return new Map(cat.metrics.map((m) => [m.requirement_id, m]));
  }, "catalog", { enabled: isCompany });
  const company = useAsync(() => api.company(route.id), route.id || "", { enabled: isCompany });

  // resolve the financial year from the route
  let year = null, yearError = null, redirect = null;
  const co = isCompany ? company.data : null;
  if (co && co.years.length) {
    year = co.years.find((y) => y.fiscal_year === route.fy) || null;
    if (!year) {
      const processed = co.years.filter((y) => y.status === "PROCESSED");
      const fallback = processed[processed.length - 1] || co.years[co.years.length - 1];
      if (route.fy) { yearError = fallback; year = fallback; }
      else redirect = fallback;
    }
  }
  useEffect(() => {
    if (redirect) routeTo(co.company.company_id, redirect.fiscal_year, route.tab, Object.fromEntries(route.params));
  }, [redirect]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    document.title = co && year && !yearError
      ? `${co.company.legal_name} · ${fy(year.fiscal_year)} · BRSR Analytics` : "BRSR Analytics";
  }, [co, year, yearError]);

  let body;
  if (!isCompany) {
    body = <Landing />;
  } else if (catalog.error || company.error) {
    const err = company.error || catalog.error;
    body = <ErrorState error={err} onRetry={() => { if (company.error) company.reload(); if (catalog.error) catalog.reload(); }} />;
  } else if (catalog.loading || company.loading || !catalog.data || !co || redirect) {
    body = <Loading />;
  } else if (!co.years.length) {
    body = <Empty>No in-scope BRSR filings are recorded for this company.</Empty>;
  } else if (yearError) {
    body = (
      <>
        <ErrorState error={{ status: 404, message: `${fy(route.fy)} is not available for this company.` }} />
        <p className="state"><a href={routeHash(co.company.company_id, yearError.fiscal_year, route.tab)}>{`Open ${fy(yearError.fiscal_year)}`}</a></p>
      </>
    );
  } else {
    const [tabKey, , View] = TABS.find(([k]) => k === route.tab) || TABS[0];
    const state = { catalog: catalog.data, company: co, year };
    body = <View key={hash} state={state} params={route.params}
      navigate={(params) => routeTo(co.company.company_id, year.fiscal_year, tabKey, params)} />;
  }

  const headerCompany = isCompany && co && catalog.data ? co : null;
  return (
    <DrawerContext.Provider value={drawer}>
      <Header company={headerCompany} year={year} route={route} />
      <Tabs company={headerCompany} year={year} route={route} />
      <main id="view" className="view" tabIndex={-1}>{body}</main>
      <Drawer value={drawerValue} onClose={closeDrawer} />
      <footer className="footer">
        Values are shown as filed in the BRSR XBRL filing. Missing values are never shown as zero.
        {" "}Normalised values follow the platform&apos;s approved unit rules; unit-ambiguous values are shown as filed, without a normalised value.
      </footer>
    </DrawerContext.Provider>
  );
}
