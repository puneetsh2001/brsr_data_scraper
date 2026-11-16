import { useEffect, useRef } from "react";
import { BASIS, DISCLOSURE, PUBLICATION, breakdownLabel, displayValue, formatNumber, fy, unitLabel, unitTitle } from "../lib/format.js";
import { Badge, SafeLink, ValueBadges } from "./common.jsx";

function Kv({ pairs }) {
  return (
    <dl className="kv">
      {pairs.filter(([, v]) => v !== undefined).map(([k, v]) => [
        <dt key={`${k}-t`}>{k}</dt>,
        <dd key={`${k}-d`}>{v === null || v === "" ? <span className="muted">—</span> : v}</dd>,
      ])}
    </dl>
  );
}

function DrawerBody({ v, onClose, closeRef }) {
  const d = displayValue(v);
  const s = v.source || {};
  const r = v.review || {};
  const reportedUnit = v.reported_unit !== undefined
    ? (v.reported_unit
      ? `${v.reported_unit}${unitTitle(v.reported_unit) !== v.reported_unit ? ` — ${unitTitle(v.reported_unit).split(" — ")[1]}` : ""}`
      : "no unit in the filing")
    : undefined;
  return (
    <>
      <button className="close" onClick={onClose} aria-label="Close" ref={closeRef}>×</button>
      <h2>{v.metric_name}</h2>
      <div className="muted">{[v.requirement_id, fy(v.fiscal_year), breakdownLabel(v.dimensions)].filter(Boolean).join(" · ")}</div>
      <div style={{ marginTop: 10 }}><ValueBadges v={v} /></div>
      <h3>Value</h3>
      <Kv pairs={[
        ["Normalised value", v.value !== null && v.value !== undefined ? (v.value_numeric !== null ? formatNumber(v.value) : v.value) : null],
        ["Normalised unit", v.unit ? `${v.unit}${unitLabel(v.unit) !== v.unit ? ` (shown as ${unitLabel(v.unit)})` : ""}` : null],
        ["Reported value", v.reported_value !== undefined ? (formatNumber(v.reported_value) ?? v.reported_value) : undefined],
        ["Reported unit (exact, as filed)", reportedUnit],
        ["Disclosure status", <Badge table={DISCLOSURE} code={v.disclosure_status} />],
        ["Value basis", v.value_basis ? <Badge table={BASIS} code={v.value_basis} /> : undefined],
        ["Normalisation status", v.normalisation_status],
        ["Note", v.normalisation_note],
        ["Period", v.period ? [v.period.start, v.period.end].filter(Boolean).join(" to ") || v.period.instant : undefined],
      ]} />
      <h3>Publication</h3>
      <Kv pairs={[
        ["Publication status", v.publication_status ? <Badge table={PUBLICATION} code={v.publication_status} /> : undefined],
        ["Used in charts and comparisons", v.comparable === undefined ? undefined : (v.comparable ? "Yes" : "No")],
        ["Withheld", v.withheld ? [v.withheld.value !== undefined && v.withheld.value !== null
          ? `${formatNumber(v.withheld.value) ?? v.withheld.value} ${v.withheld.unit ? unitLabel(v.withheld.unit) : ""} — ` : "", v.withheld.reason].join("") : undefined],
        ["Scale review", v.scale_review ? [v.scale_review.record_id, v.scale_review.status,
          v.scale_review.recommended_decision ? `recommended ${v.scale_review.recommended_decision}` : null,
          v.scale_review.proposed_value ? `proposed value ${v.scale_review.proposed_value}` : null,
          v.scale_review.note].filter(Boolean).join(" · ") : undefined],
      ]} />
      {v.calculation ? (
        <>
          <h3>Calculation</h3>
          <div className="formula">{v.calculation.formula || "—"}</div>
          <Kv pairs={[["Factor", v.calculation.factor], ["Basis", v.calculation.multiplier_basis], ["Decision", v.calculation.decision]]} />
          <div className="small muted" style={{ marginTop: 6 }}>Inputs:</div>
          {v.calculation.inputs.map((i, n) => (
            <div className="small" key={n}>
              {`${i.concept}: ${formatNumber(i.value) ?? i.value} ${i.unit || ""} (context ${i.context_reference})`}
            </div>
          ))}
        </>
      ) : null}
      <h3>Source filing</h3>
      <Kv pairs={[
        ["Filing ID", s.filing_id], ["XBRL concept", s.concept], ["Context reference", s.context_reference],
        ["Source file", s.source_file], ["Taxonomy version", s.taxonomy_version], ["Submitted", s.submission_date],
        ["Version", s.filing_version_status], ["XBRL", <SafeLink url={s.xbrl_url}>Open XBRL instance</SafeLink>],
        ["PDF", s.pdf_url ? <SafeLink url={s.pdf_url}>Open BRSR PDF on NSE</SafeLink> : null],
      ]} />
      <h3>Review flags</h3>
      <Kv pairs={[
        ["Pending review", r.pending_review], ["Deferred issues", (r.deferred_issue_ids || []).join(", ")],
        ["Crosswalk status", r.crosswalk_status], ["Unit decision", r.unit_decision],
      ]} />
      {d.asFiled ? (
        <p className="note">No normalised value: the filed unit is missing, ambiguous or awaiting an owner decision.
          {" "}The filed value is shown as it appears in the filing.</p>
      ) : null}
    </>
  );
}

export function Drawer({ value, onClose }) {
  const closeRef = useRef(null);
  useEffect(() => {
    if (value && closeRef.current) closeRef.current.focus();
  }, [value]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const open = !!value;
  return (
    <>
      <aside id="drawer" className={`drawer${open ? " open" : ""}`} aria-hidden={open ? "false" : "true"}
        aria-label="Value details">
        {open ? <DrawerBody v={value} onClose={onClose} closeRef={closeRef} /> : null}
      </aside>
      <div id="drawer-backdrop" className={`drawer-backdrop${open ? " open" : ""}`} onClick={onClose} />
    </>
  );
}
