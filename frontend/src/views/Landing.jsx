import { api } from "../lib/api.js";
import { useGo } from "../lib/paths.js";
import { useAsync } from "../lib/useAsync.js";
import { ErrorState, Loading } from "../components/common.jsx";

export function Landing() {
  const go = useGo();
  const list = useAsync(() => api.search("", 100), "landing");
  if (list.loading) return <Loading text="Loading companies…" />;
  if (list.error) return <ErrorState error={list.error} onRetry={list.reload} />;
  const r = list.data;
  return (
    <section className="panel">
      <h2>Select a company</h2>
      <p className="muted">{`${r.total} companies in the processing universe. Search above, or choose one below.`}</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Company</th><th>NSE</th><th>BSE</th><th>ISIN</th><th>Industry</th></tr></thead>
          <tbody>
            {r.companies.map((c) => (
              <tr className="clickable" key={c.company_id} onClick={() => go(c.company_id)}>
                <td className="metric-name">{c.legal_name}</td><td>{c.nse_symbol}</td><td>{c.bse_scrip_code || "—"}</td>
                <td className="small">{c.isin}</td><td className="small muted">{c.industry || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
