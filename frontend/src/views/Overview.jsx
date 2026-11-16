import { api } from "../lib/api.js";
import { DISCLOSURE, fy } from "../lib/format.js";
import { useAsync } from "../lib/useAsync.js";
import { AsyncView, Badge, Empty, SafeLink, SectionTitle } from "../components/common.jsx";
import { GenderGrid, MetricCard, MissingTable } from "../components/metrics.jsx";

const CFG = window.BRSR_CONFIG || {};

function Identity({ company }) {
  const c = company.company;
  const ids = company.identifiers.filter((i) => i.identifier_type !== "ISIN" || i.value !== c.isin);
  const rows = [
    ["NSE symbol", c.nse_symbol], ["BSE scrip code", c.bse_scrip_code], ["ISIN", c.isin],
    ["CIN", c.cin ? `${c.cin}${c.cin_status ? ` (${c.cin_status.toLowerCase().replace(/_/g, " ")})` : ""}` : ""],
    ["Website", c.website ? <SafeLink url={/^https?:/.test(c.website) ? c.website : `https://${c.website}`}>{c.website}</SafeLink> : ""],
    ["Registered office", c.registered_office],
  ];
  return (
    <section className="panel identity">
      <h1>{c.legal_name}</h1>
      <div className="muted">{[c.industry, c.company_status].filter(Boolean).join(" · ")}</div>
      <dl className="kv">
        {rows.map(([k, v]) => [<dt key={`${k}t`}>{k}</dt>,
          <dd key={`${k}d`}>{v || <span className="muted">Not available</span>}</dd>])}
      </dl>
      {ids.length ? <p className="small muted">{`${ids.length} historical / alternative identifiers on record`}</p> : null}
    </section>
  );
}

function Filing({ year }) {
  const f = year.filing;
  return (
    <section className="panel">
      <h2>{`BRSR filing · ${fy(year.fiscal_year)}`}</h2>
      {f ? (
        <dl className="kv">
          {[
            ["Status", <span className="badge b-ok">{year.status.replace(/_/g, " ").toLowerCase()}</span>],
            ["Filing ID", f.filing_id],
            ["Reporting period", `${f.reporting_period_start} to ${f.reporting_period_end}`],
            ["Submitted to NSE", f.submission_date],
            ["Taxonomy version", f.taxonomy_version || "—"],
            ["Version", `${f.filing_version_status}${year.versions > 1 ? ` (${year.versions} versions in this year)` : ""}`],
            ["XBRL", <SafeLink url={f.xbrl_url}>XBRL instance</SafeLink>],
            ["PDF", f.pdf_url ? <SafeLink url={f.pdf_url}>BRSR PDF on NSE</SafeLink> : "—"],
          ].map(([k, v]) => [<dt key={`${k}t`}>{k}</dt>, <dd key={`${k}d`}>{v}</dd>])}
        </dl>
      ) : (
        <div>
          <Badge table={DISCLOSURE} code={year.status === "EXTRACTION_ERROR" ? "EXTRACTION_ERROR" : "NO_FILING_FOR_YEAR"} />
          {year.failed_filings.map((x) => <p className="small muted" key={x.filing_id}>{`${x.filing_id}: ${x.error}`}</p>)}
        </div>
      )}
    </section>
  );
}

export function Overview({ state }) {
  const { company, year } = state;
  const id = company.company.company_id;
  const key = `${id}|${year.fiscal_year}`;
  const metrics = useAsync(() => api.yearMetrics(id, year.fiscal_year), key);
  const workforce = useAsync(() => api.workforce(id, year.fiscal_year), key);
  return (
    <>
      <div className="grid grid-2">
        <Identity company={company} />
        <Filing year={year} />
      </div>
      <SectionTitle title="Key metrics" />
      <div className="cards">
        <AsyncView state={metrics}>{(data) => {
          const byKey = new Map(data.metrics.map((g) => [g.metric_key, g]));
          const missByKey = new Map(data.missing.map((m) => [m.metric_key, m]));
          const keys = (CFG.summaryMetricKeys || []).filter((k) => byKey.has(k) || missByKey.has(k));
          if (!keys.length) return <Empty>No metrics available for this year.</Empty>;
          return keys.map((k) => <MetricCard key={k} group={byKey.get(k)} missing={missByKey.get(k)} />);
        }}</AsyncView>
      </div>
      <SectionTitle title="Workforce at a glance" cls="soc" />
      <div>
        <AsyncView state={workforce}>{(data) => {
          const g = data.metrics.find((x) => x.gender_grid && x.gender_grid.length);
          if (!g) return data.missing.length ? <MissingTable missing={data.missing} /> : <Empty>No workforce data.</Empty>;
          return <div className="panel"><h2>{g.metric_name}</h2><GenderGrid group={g} /></div>;
        }}</AsyncView>
      </div>
    </>
  );
}
