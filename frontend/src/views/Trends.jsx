import { useEffect } from "react";
import { api } from "../lib/api.js";
import { DISCLOSURE, breakdownLabel, displayValue, formatNumber, fy, unitLabel, unitTitle } from "../lib/format.js";
import { useAsync } from "../lib/useAsync.js";
import { useDrawer } from "../lib/drawer.js";
import { AsyncView, Badge, ErrorState, Loading, SectionTitle, ValueBadges } from "../components/common.jsx";
import { BarChart } from "../components/BarChart.jsx";
import { BreakdownSelect, MetricOptions, breakdownOptions } from "./shared.jsx";

function HistoryPanel({ data }) {
  const { open } = useDrawer();
  const rows = data.series.map((s) => ({ fy: s.fiscal_year, vals: s.values, missing: s.missing }));
  const numeric = rows.filter((r) => r.vals.length === 1 && r.vals[0].value_numeric !== null);
  const multi = rows.some((r) => r.vals.length > 1);
  let chart;
  if (!data.comparable) {
    chart = <p className="note">{`Chart not shown: normalised units differ across years (${data.units.join(", ")}).`}</p>;
  } else if (multi) {
    chart = <p className="note">Chart not shown: some years report more than one value for this metric; see the table.</p>;
  } else if (numeric.length) {
    const unit = data.units[0];
    chart = (
      <>
        <div className="small muted">{`Normalised values (${unitLabel(unit)}); years without a comparable value are labelled, not drawn.`}</div>
        <BarChart title="History" unit={unitLabel(unit)} items={rows.map((r) => {
          const v = r.vals[0];
          if (v && v.value_numeric !== null) return { label: fy(r.fy), value: v.value_numeric, text: v.value, calc: v.value_basis.startsWith("CALCULATED") };
          if (v) return { label: fy(r.fy), value: null, note: `not normalised (filed ${formatNumber(v.reported_value) ?? v.reported_value} ${v.reported_unit ? unitLabel(v.reported_unit, true) : "without unit"})` };
          return { label: fy(r.fy), value: null, note: (DISCLOSURE[r.missing?.disclosure_status] || ["not available"])[0].toLowerCase() };
        })} />
      </>
    );
  } else {
    chart = <p className="note">No normalised numeric values to chart.</p>;
  }
  return (
    <section className="panel">
      <h2>{`${data.metric.metric_name} (${data.metric.requirement_id})${data.dimensions.length ? ` · ${breakdownLabel(data.dimensions)}` : ""}`}</h2>
      {chart}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Financial year</th><th className="num">Value</th><th>Unit</th><th>Status</th><th>Filed as</th><th /></tr>
          </thead>
          <tbody>
            {rows.flatMap((r) => (r.vals.length ? r.vals.map((v, i) => {
              const d = displayValue(v);
              return (
                <tr className="clickable" key={`${r.fy}-${i}`} onClick={() => open(v)}>
                  <td>{fy(r.fy)}</td>
                  <td className="num">{d.asFiled ? "—" : d.text}</td>
                  <td>{d.asFiled ? "—" : d.unit}</td>
                  <td><ValueBadges v={v} /></td>
                  <td className="small" title={unitTitle(v.reported_unit) || undefined}>
                    {`${formatNumber(v.reported_value) ?? v.reported_value} ${v.reported_unit ? unitLabel(v.reported_unit, true) : "(no unit)"}`}
                  </td>
                  <td><button className="link-btn">Details</button></td>
                </tr>
              );
            }) : [
              <tr key={r.fy}>
                <td>{fy(r.fy)}</td>
                <td className="num missing">Not available</td>
                <td>—</td>
                <td>{r.missing ? <Badge table={DISCLOSURE} code={r.missing.disclosure_status} /> : "—"}</td>
                <td className="small muted" colSpan={2}>{r.missing?.missing_reason || ""}</td>
              </tr>,
            ]))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Trends({ state, params, navigate }) {
  const metric = params.get("metric") || "E07";
  const breakdown = params.get("breakdown") || "";
  const id = state.company.company.company_id;
  const years = state.company.years.filter((y) => y.filing).map((y) => [id, y.fiscal_year]);
  const opts = useAsync(() => breakdownOptions(state.catalog, metric, years), `${id}|${metric}`);
  const mismatch = opts.data && opts.data.length > 0 && !opts.data.some(([v]) => v === breakdown);
  useEffect(() => {
    // a metric with breakdowns has no total value: default to the first one
    if (mismatch) navigate({ metric, breakdown: opts.data[0][0] });
  }, [mismatch]); // eslint-disable-line react-hooks/exhaustive-deps
  const ready = !!opts.data && !mismatch;
  const history = useAsync(() => api.history(id, metric, breakdown), `${id}|${metric}|${breakdown}`, { enabled: ready });
  return (
    <>
      <SectionTitle title={`Historical trend · ${state.company.company.legal_name}`} />
      <div className="controls">
        <label>Metric
          <select className="wide" value={metric} onChange={(e) => navigate({ metric: e.target.value, breakdown: "" })}>
            <MetricOptions catalog={state.catalog} />
          </select>
        </label>
        <label>Breakdown
          <BreakdownSelect options={ready ? opts.data : null} value={breakdown}
            onChange={(v) => navigate({ metric, breakdown: v })} />
        </label>
      </div>
      <div>
        {opts.error ? <ErrorState error={opts.error} onRetry={opts.reload} />
          : !ready ? <Loading />
            : <AsyncView state={history}>{(data) => <HistoryPanel data={data} />}</AsyncView>}
      </div>
    </>
  );
}
