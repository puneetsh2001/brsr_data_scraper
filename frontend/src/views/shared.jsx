import { api } from "../lib/api.js";
import { breakdownLabel } from "../lib/format.js";

// metric <select> options grouped by pillar (metrics in BRSR XBRL only)
export function MetricOptions({ catalog }) {
  const byPillar = {};
  [...catalog.values()].filter((m) => m.in_brsr_xbrl).forEach((m) => (byPillar[m.pillar] ||= []).push(m));
  return Object.entries(byPillar).map(([p, ms]) => (
    <optgroup label={p} key={p}>
      {ms.map((m) => <option value={m.requirement_id} key={m.requirement_id}>{`${m.metric_name} (${m.requirement_id})`}</option>)}
    </optgroup>
  ));
}

// Breakdowns actually reported for a metric. Metrics without breakdown axes
// in the registry only have the total value (breakdown ""), so no request is
// needed; otherwise one small metric-filtered request per company-year is
// made in parallel (cached for the session).
export const TOTAL = ["", "Total (no breakdown)"];

export async function breakdownOptions(catalog, metric, companyYears) {
  const meta = catalog.get(metric);
  if (!meta || !meta.breakdown_axes.length) return [TOTAL];
  const results = await Promise.all(companyYears.map(([id, fyLabel]) =>
    api.yearMetrics(id, fyLabel, [metric]).catch(() => null)));
  const seen = new Map();
  for (const data of results) {
    const g = data && data.metrics.find((x) => x.requirement_id === metric);
    (g ? g.values : []).forEach((v) => {
      if (!seen.has(v.breakdown)) seen.set(v.breakdown, breakdownLabel(v.dimensions) || TOTAL[1]);
    });
  }
  return [...seen.entries()].sort((x, y) => (x[0] !== "") - (y[0] !== "") || x[1].localeCompare(y[1]));
}

export function BreakdownSelect({ options, value, onChange }) {
  if (!options) {
    return <select className="wide" disabled><option>Loading…</option></select>;
  }
  if (options.length === 1 && options[0][0] === "") {
    return <select className="wide" disabled title="This metric has no breakdown"><option value="">No breakdown (single value)</option></select>;
  }
  if (!options.length) {
    return <select className="wide" disabled><option>No reported breakdowns</option></select>;
  }
  return (
    <select className="wide" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map(([v, l]) => <option value={v} key={v}>{l}</option>)}
    </select>
  );
}
