import { Fragment } from "react";
import { DISCLOSURE, breakdownLabel, displayValue, formatNumber, unitLabel, unitTitle } from "../lib/format.js";
import { useDrawer } from "../lib/drawer.js";
import { Badge, ValueBadges } from "./common.jsx";

function ValueCell({ v }) {
  const d = displayValue(v);
  if (d.missing) return <td className="num missing">Not available</td>;
  return (
    <td className="num">
      {d.text}
      {d.asFiled ? <div className="metric-sub" title="Shown as filed: no normalised value">as filed</div> : null}
    </td>
  );
}

function UnitCell({ v }) {
  const d = displayValue(v);
  return <td title={unitTitle(d.rawUnit) || undefined}>{d.unit || <span className="muted">—</span>}</td>;
}

// one table for a list of metric groups (each with values)
export function MetricTable({ groups, showBreakdown = true }) {
  const { open } = useDrawer();
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Metric</th><th className="num">Value</th><th>Unit</th><th>Status</th><th /></tr>
        </thead>
        <tbody>
          {groups.flatMap((g) => g.values.map((v, i) => {
            const sub = breakdownLabel(v.dimensions);
            return (
              <tr key={`${g.requirement_id}|${v.breakdown}|${v.concept}|${i}`} className="clickable" tabIndex={0}
                title="Show source and calculation" onClick={() => open(v)}
                onKeyDown={(e) => { if (e.key === "Enter") open(v); }}>
                <td>
                  {i === 0 ? <div className="metric-name">{g.metric_name}</div> : null}
                  {showBreakdown && sub ? <div className="metric-sub">{sub}</div> : null}
                  {g.values.length > 1 && !sub ? <div className="metric-sub">{v.concept}</div> : null}
                </td>
                <ValueCell v={v} />
                <UnitCell v={v} />
                <td><ValueBadges v={v} /></td>
                <td><button className="link-btn" onClick={(e) => { e.stopPropagation(); open(v); }}>Details</button></td>
              </tr>
            );
          }))}
        </tbody>
      </table>
    </div>
  );
}

export function MissingTable({ missing }) {
  if (!missing || !missing.length) return null;
  // one row when every metric is missing for the same reason (e.g. no
  // filing or a failed filing for the year)
  const same = missing.length > 2 && missing.every((m) => m.disclosure_status === missing[0].disclosure_status
    && (m.missing_reason || "") === (missing[0].missing_reason || ""));
  if (same) {
    return (
      <div className="table-wrap">
        <table>
          <thead><tr><th>Metrics</th><th>Status</th><th>Reason</th></tr></thead>
          <tbody>
            <tr>
              <td>
                <div className="metric-name">{`${missing.length} metrics`}</div>
                <div className="metric-sub">{missing.map((m) => m.metric_name).join(", ")}</div>
              </td>
              <td><Badge table={DISCLOSURE} code={missing[0].disclosure_status} /></td>
              <td className="small muted">{missing[0].missing_reason || ""}</td>
            </tr>
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Metric</th><th>Status</th><th>Reason</th></tr></thead>
        <tbody>
          {missing.map((m) => (
            <tr key={m.requirement_id}>
              <td><div className="metric-name">{m.metric_name}</div><div className="metric-sub">{m.requirement_id}</div></td>
              <td><Badge table={DISCLOSURE} code={m.disclosure_status} /></td>
              <td className="small muted">{m.missing_reason || ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// summary card for one metric group (first total value)
export function MetricCard({ group, missing }) {
  const { open } = useDrawer();
  if (!group) {
    return (
      <div className="card" title={missing.missing_reason || ""}>
        <div className="label">{missing.metric_name}</div>
        <div className="value missing">Not available</div>
        <div className="badges"><Badge table={DISCLOSURE} code={missing.disclosure_status} /></div>
      </div>
    );
  }
  const v = group.values.find((x) => !x.breakdown) || group.values[0];
  const d = displayValue(v);
  return (
    <button className="card" onClick={() => open(v)} title="Show source and calculation">
      <div className="label">{group.metric_name}</div>
      <div className={`value${d.missing ? " missing" : ""}`}>
        {d.text}
        {d.unit ? <span className="unit" title={unitTitle(d.rawUnit) || undefined}>{d.unit}</span> : null}
      </div>
      {d.asFiled ? <div className="metric-sub">As filed: no normalised value</div> : null}
      <ValueBadges v={v} />
    </button>
  );
}

// category x gender table built from the API's gender_grid; on narrow
// screens one card per category (total first) replaces the table
export function GenderGrid({ group }) {
  const { open } = useDrawer();
  const genders = [];
  for (const row of group.gender_grid) for (const g of Object.keys(row.cells)) if (!genders.includes(g)) genders.push(g);
  genders.sort((a, b) => (a === "Total") - (b === "Total"));
  const cardGenders = [...genders].sort((a, b) => (b === "Total") - (a === "Total"));
  const byKey = new Map(group.values.map((v) => [`${v.breakdown}`, v]));
  const units = [...new Set(group.values.map((v) => (v.unit ? unitLabel(v.unit) : unitLabel(v.reported_unit, true))).filter(Boolean))];
  const valueFor = (row, g) => {
    const c = row.cells[g];
    if (!c) return { c: null, v: null };
    const v = group.values.find((x) => x.source.context_reference === c.context_reference
      && x.dimensions.some((d) => d.axis === "GenderAxis" && d.label === g)) || byKey.get("");
    return { c, v };
  };
  const cellButton = ({ c, v }) => {
    if (!c) return <span className="muted">Not available</span>;
    const zero = c.disclosure_status === "REPORTED_ZERO";
    return (
      <button className="link-btn" style={zero ? { color: "var(--zero-fg)" } : undefined}
        title={zero ? "Reported zero — open details" : "Open details"} onClick={() => v && open(v)}>
        {c.value === null ? "Not available" : formatNumber(c.value)}
      </button>
    );
  };
  return (
    <div>
      <div className="small muted" style={{ margin: "-4px 0 8px" }}>
        {`Unit: ${units.join(", ") || "not stated"} · select a value for its source`}
      </div>
      <div className="gender-cards">
        {group.gender_grid.map((row) => (
          <div className="gender-card" key={row.category}>
            <div className="metric-name">{row.category_label}</div>
            <dl>
              {cardGenders.map((g) => (
                <Fragment key={g}><dt>{g}</dt><dd>{cellButton(valueFor(row, g))}</dd></Fragment>
              ))}
            </dl>
          </div>
        ))}
      </div>
      <div className="table-wrap gender-table">
        <table>
          <thead>
            <tr><th>Category</th>{genders.map((g) => <th className="num" key={g}>{g}</th>)}</tr>
          </thead>
          <tbody>
            {group.gender_grid.map((row) => (
              <tr key={row.category}>
                <td className="metric-name">{row.category_label}</td>
                {genders.map((g) => {
                  const cv = valueFor(row, g);
                  return cv.c ? <td className="num" key={g}>{cellButton(cv)}</td>
                    : <td className="num missing" key={g}>Not available</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
