import { BASIS, DISCLOSURE, info } from "../lib/format.js";

export function Badge({ table, code }) {
  const [label, cls, title] = info(table, code);
  return <span className={`badge ${cls}`} title={title}>{label}</span>;
}

export function ReviewBadges({ v }) {
  const r = v.review || {};
  return (
    <>
      {r.pending_review ? <span className="badge b-warn" title={r.pending_review}>Pending review</span> : null}
      {r.deferred_issue_ids && r.deferred_issue_ids.length
        ? <span className="badge b-warn" title={r.deferred_issue_ids.join(", ")}>Deferred issue</span> : null}
    </>
  );
}

export function ValueBadges({ v }) {
  return (
    <div className="badges">
      <Badge table={DISCLOSURE} code={v.disclosure_status} />
      {v.value_basis ? <Badge table={BASIS} code={v.value_basis} /> : null}
      <ReviewBadges v={v} />
    </div>
  );
}

export function Loading({ text = "Loading…" }) {
  return <div className="state"><div className="spinner" />{text}</div>;
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="state error" role="alert">
      <div className="metric-name">{error && error.status === 404 ? "Not found" : "Could not load data"}</div>
      <div>{(error && error.message) || String(error)}</div>
      {onRetry ? <p><button className="btn secondary" onClick={onRetry}>Retry</button></p> : null}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="state">{children}</div>;
}

// Only http(s) links are rendered as links.
export function SafeLink({ url, children }) {
  if (!url || !/^https?:\/\//i.test(url)) return <span className="muted">—</span>;
  return <a href={url} target="_blank" rel="noopener noreferrer">{children || url}</a>;
}

// Render `children(data)` for an async result, with loading and error states.
export function AsyncView({ state, children }) {
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  if (state.data === null || state.data === undefined) return null;
  return children(state.data);
}

export function SectionTitle({ title, cls = "", children }) {
  return <div className={`section-title ${cls}`}><h2>{title}</h2>{children}</div>;
}

export function Spacer() {
  return <div style={{ height: 14 }} />;
}
