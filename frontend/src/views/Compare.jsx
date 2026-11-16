import { useEffect } from "react";
import { api } from "../lib/api.js";
import { DISCLOSURE, PUBLICATION, breakdownLabel, displayValue, fy, isComparable, unitLabel } from "../lib/format.js";
import { useAsync } from "../lib/useAsync.js";
import { useDrawer } from "../lib/drawer.js";
import { AsyncView, Badge, Empty, ErrorState, Loading, SectionTitle, ValueBadges } from "../components/common.jsx";
import { BarChart } from "../components/BarChart.jsx";
import { SearchBox } from "../components/SearchBox.jsx";
import { BreakdownSelect, MetricOptions, breakdownOptions } from "./shared.jsx";

export function ComparePanel({ data }) {
  const { open } = useDrawer();
  const rows = [];
  for (const c of data.companies) {
    for (const y of c.years) {
      if (y.values.length) y.values.forEach((v) => rows.push({ c: c.company, fy: y.fiscal_year, v }));
      else rows.push({ c: c.company, fy: y.fiscal_year, missing: y.missing });
    }
  }
  const title = `${data.metric.metric_name} (${data.metric.requirement_id})`
    + (data.dimensions.length ? ` · ${breakdownLabel(data.dimensions)}` : "")
    + (data.fiscal_year ? ` · ${fy(data.fiscal_year)}` : " · all years");
  let chart = null;
  if (data.fiscal_year && data.comparable && data.units.length === 1 && rows.some((r) => isComparable(r.v))) {
    const unit = data.units[0];
    chart = (
      <>
        <div className="small muted">{`Normalised values in ${unitLabel(unit)}. Companies without a comparable value are labelled, not drawn.`}</div>
        <BarChart title="Comparison" unit={unitLabel(unit)} items={rows.map((r) => {
          const label = r.c.nse_symbol || r.c.legal_name;
          if (r.v && isComparable(r.v)) {
            return { label: r.v.concept && rows.filter((x) => x.c === r.c).length > 1 ? `${label} · ${r.v.concept}` : label,
              value: r.v.value_numeric, text: r.v.value, calc: r.v.value_basis.startsWith("CALCULATED") };
          }
          if (r.v) return { label, value: null, note: `not comparable: ${((PUBLICATION[r.v.publication_status] || [r.v.publication_status || "not normalised"])[0]).toLowerCase()}` };
          return { label, value: null, note: (DISCLOSURE[r.missing?.disclosure_status] || ["not available"])[0].toLowerCase() };
        })} />
      </>
    );
  } else if (data.fiscal_year && data.comparable && data.units.length <= 1) {
    chart = <p className="note">Chart not shown: no selected company has a comparable value for this metric (see the status column).</p>;
  } else if (!data.comparable) {
    chart = <p className="note">{`Chart not shown: normalised units differ (${data.units.join(", ")}); values are not compared as equivalent.`}</p>;
  } else if (!data.fiscal_year) {
    chart = <p className="note">Select one financial year to see a chart.</p>;
  }
  return (
    <section className="panel">
      <h2>{title}</h2>
      {chart}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Company</th><th>FY</th><th className="num">Value</th><th>Unit</th><th>Status</th><th>Source</th></tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const who = <td><div className="metric-name">{r.c.legal_name}</div><div className="metric-sub">{r.c.nse_symbol}</div></td>;
              if (!r.v) {
                return (
                  <tr key={i}>
                    {who}<td>{fy(r.fy)}</td><td className="num missing">Not available</td><td>—</td>
                    <td>{r.missing ? <Badge table={DISCLOSURE} code={r.missing.disclosure_status} /> : "—"}</td>
                    <td className="small muted">{r.missing?.missing_reason || ""}</td>
                  </tr>
                );
              }
              const d = displayValue(r.v);
              return (
                <tr className="clickable" key={i} onClick={() => open(r.v)}>
                  {who}<td>{fy(r.fy)}</td>
                  <td className="num">{d.text}{d.asFiled ? <div className="metric-sub">as filed</div> : null}</td>
                  <td>{d.unit}</td><td><ValueBadges v={r.v} /></td>
                  <td className="small">{r.v.source.filing_id}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Compare({ state, params, navigate }) {
  const metric = params.get("metric") || "E07";
  const breakdown = params.get("breakdown") || "";
  const selected = (params.get("companies") || state.company.company.company_id).split(",").filter(Boolean);
  const selKey = selected.join(",");
  const fyRequested = params.get("fy");
  const go = (patch, opts) => navigate({ metric, breakdown, fy: fyRequested ?? state.year.fiscal_year, companies: selKey, ...patch }, opts);

  // years of every selected company (cached company records)
  const cos = useAsync(() => Promise.all(selected.map((id) => api.company(id))), selKey, { enabled: selected.length > 0 });
  let byFy = null, fys = [], fySel = fyRequested ?? state.year.fiscal_year, pairs = [];
  if (cos.data) {
    byFy = new Map();
    cos.data.forEach((c) => c.years.forEach((y) => {
      if (!byFy.has(y.fiscal_year)) byFy.set(y.fiscal_year, []);
      if (y.filing) byFy.get(y.fiscal_year).push(c.company.company_id);
    }));
    fys = [...byFy.keys()].sort();
    if (fySel && !byFy.has(fySel)) fySel = fys[fys.length - 1] || "";
    pairs = cos.data.flatMap((c) => c.years.filter((y) => y.filing && (!fySel || y.fiscal_year === fySel))
      .map((y) => [c.company.company_id, y.fiscal_year]));
  }
  const fyRedirect = !!cos.data && fyRequested !== null && fySel !== fyRequested;
  useEffect(() => { if (fyRedirect) go({ fy: fySel }, { replace: true }); }, [fyRedirect]); // eslint-disable-line react-hooks/exhaustive-deps

  // breakdowns reported by the selected companies for the metric
  const opts = useAsync(() => breakdownOptions(state.catalog, metric, pairs), `${metric}|${fySel}|${selKey}`,
    { enabled: !!cos.data && !fyRedirect });
  const bdRedirect = !!opts.data && opts.data.length > 0 && !opts.data.some(([v]) => v === breakdown);
  useEffect(() => { if (bdRedirect) go({ fy: fySel, breakdown: opts.data[0][0] }, { replace: true }); }, [bdRedirect]); // eslint-disable-line react-hooks/exhaustive-deps
  const ready = !!opts.data && !bdRedirect;
  const result = useAsync(() => api.compare(metric, selected, fySel || undefined, breakdown),
    `${metric}|${selKey}|${fySel}|${breakdown}`, { enabled: ready });

  const without = cos.data && fySel ? cos.data.filter((c) => !byFy.get(fySel).includes(c.company.company_id)) : [];
  const none = selected.length === 0;
  return (
    <>
      <SectionTitle title="Company comparison" />
      <div className="controls">
        <label>Metric
          <select className="wide" value={metric} onChange={(e) => go({ metric: e.target.value, breakdown: "" })}>
            <MetricOptions catalog={state.catalog} />
          </select>
        </label>
        <label>Financial year
          {none ? <select disabled><option>—</option></select>
            : !cos.data ? <select disabled><option>Loading…</option></select>
              : (
                <select value={fySel} onChange={(e) => go({ fy: e.target.value, breakdown: "" })}>
                  <option value="">All years</option>
                  {fys.map((y) => <option value={y} key={y}>{`${fy(y)} · data for ${byFy.get(y).length}/${cos.data.length} companies`}</option>)}
                </select>
              )}
        </label>
        <label>Breakdown
          {none ? <select disabled><option>—</option></select>
            : <BreakdownSelect options={ready ? opts.data : null} value={breakdown}
              onChange={(v) => go({ fy: fySel, breakdown: v })} />}
        </label>
        <SearchBox placeholder="Add a company (name, symbol, ISIN)…" ariaLabel="Add a company to the comparison"
          limit={10} delay={200} showOnFocus={false} exclude={selected} style={{ width: "min(340px,90vw)" }}
          onSelect={(c) => go({ companies: [...selected, c.company_id].join(",") })} />
      </div>
      <div className="chips">
        {(cos.data || []).map((c) => (
          <span className="chip" title={c.company.legal_name} key={c.company.company_id}>
            {c.company.nse_symbol || c.company.legal_name}
            <button title={`Remove ${c.company.legal_name}`} aria-label={`Remove ${c.company.legal_name}`}
              onClick={() => go({ companies: selected.filter((x) => x !== c.company.company_id).join(",") })}>×</button>
          </span>
        ))}
      </div>
      <div>
        {without.length ? (
          <p className="note">{`No processed ${fy(fySel)} filing for: `
            + without.map((c) => c.company.nse_symbol || c.company.legal_name).join(", ")
            + ". These companies are listed with their status and are not drawn in the chart."}</p>
        ) : null}
      </div>
      <div>
        {none ? <Empty>Add at least one company to compare.</Empty>
          : cos.error ? <ErrorState error={cos.error} onRetry={cos.reload} />
            : opts.error ? <ErrorState error={opts.error} onRetry={opts.reload} />
              : !ready ? <Loading />
                : <AsyncView state={result}>{(data) => <ComparePanel data={data} />}</AsyncView>}
      </div>
    </>
  );
}
