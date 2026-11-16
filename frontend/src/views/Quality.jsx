import { api } from "../lib/api.js";
import { BASIS, DISCLOSURE, formatNumber, fy, unitLabel, unitTitle } from "../lib/format.js";
import { useAsync } from "../lib/useAsync.js";
import { AsyncView, Badge, Empty, SectionTitle, Spacer } from "../components/common.jsx";

function Counts({ obj, table }) {
  return (
    <div className="badges">
      {Object.entries(obj).map(([k, n]) => (
        <span key={k}><Badge table={table} code={k} /><span className="small" style={{ margin: "0 10px 0 4px" }}>{String(n)}</span></span>
      ))}
    </div>
  );
}

export function Quality({ state }) {
  const { company, year } = state;
  const id = company.company.company_id;
  const dq = useAsync(() => api.quality(id, year.fiscal_year), `${id}|${year.fiscal_year}`);
  return (
    <>
      <SectionTitle title={`Data quality · ${fy(year.fiscal_year)}`} />
      <div>
        <AsyncView state={dq}>{(d) => (
          <>
            <div className="grid grid-2">
              <section className="panel">
                <h2>Reported values by status</h2>
                <Counts obj={d.value_disclosure_status} table={DISCLOSURE} />
                <h3>By value basis</h3>
                <Counts obj={d.value_basis} table={BASIS} />
              </section>
              <section className="panel">
                <h2>Metrics without a value</h2>
                <Counts obj={d.missing_metrics} table={DISCLOSURE} />
              </section>
            </div>
            <Spacer />
            <section className="panel">
              <h2>Values without a normalised value</h2>
              {d.not_normalised_values.length ? (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Metric</th><th>Breakdown</th><th className="num">Filed value</th><th>Filed unit</th><th>Reason</th></tr></thead>
                    <tbody>
                      {d.not_normalised_values.map((v, i) => (
                        <tr key={i}>
                          <td>{state.catalog.get(v.requirement_id)?.metric_name || v.requirement_id}</td>
                          <td className="small">{v.breakdown || "—"}</td>
                          <td className="num">{formatNumber(v.reported_value) ?? v.reported_value}</td>
                          <td title={unitTitle(v.reported_unit) || undefined}>{v.reported_unit ? unitLabel(v.reported_unit, true) : "not stated"}</td>
                          <td className="small muted">{v.note || v.normalisation_status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>All reported values are normalised.</Empty>}
            </section>
            <Spacer />
            <section className="panel">
              <h2>Open review items</h2>
              <p className="small muted">Recommendations from the review workflow. Recommendations are not approvals; nothing here changes a value.</p>
              {d.open_review_items.length ? (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>ID</th><th>Category</th><th>Metric</th><th>Status</th><th>Severity</th><th>Reason</th></tr></thead>
                    <tbody>
                      {d.open_review_items.map((r) => (
                        <tr key={r.recommendation_id}>
                          <td className="small">{r.recommendation_id}</td><td>{r.category}</td><td>{r.requirement_id || "—"}</td>
                          <td><span className="badge b-warn">{r.status.replace(/_/g, " ").toLowerCase()}</span></td>
                          <td>{r.severity}</td><td className="small muted">{r.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>No open review items.</Empty>}
            </section>
            <Spacer />
            <section className="panel">
              <h2>Deferred data-quality issues</h2>
              {d.deferred_issues.length ? (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Issue</th><th>Metric</th><th>Item</th><th>Status</th><th>Reason</th></tr></thead>
                    <tbody>
                      {d.deferred_issues.map((x) => (
                        <tr key={x.issue_id}>
                          <td>{x.issue_id}</td><td>{x.metric}</td><td className="small">{x.item}</td>
                          <td><span className="badge b-warn">{x.status.replace(/_/g, " ").toLowerCase()}</span></td>
                          <td className="small muted">{x.reason_for_deferral}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>No deferred issues for this company-year.</Empty>}
            </section>
          </>
        )}</AsyncView>
      </div>
    </>
  );
}
