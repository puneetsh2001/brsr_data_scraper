"""
Reviewer approval workflow (Phase 11).

Statuses
  DETECTED        anomaly without a proposed correction
  RECOMMENDED     the engine proposes a correction or interpretation
  PENDING_REVIEW  an existing review record awaits a reviewer decision
                  (filer scale-error records, MtCO2e unit decisions,
                  crosswalk candidates)
  APPROVED        a reviewer approved it (decision log or source decision)
  REJECTED        a reviewer rejected it (false positive / not accepted)
  DEFERRED        deferred by the owner (deferred-issues register) or a
                  reviewer
  APPLIED         approved AND the production normalisation reflects it

A detection never becomes APPROVED by itself. Reviewer decisions are
recorded only in the append-only log data/reference/review_decisions.csv
(entry_no, recommendation_id, decision APPROVE|REJECT|DEFER, reviewer,
decided_at ISO timestamp, audit_reference, comment). Every entry is
validated; refused entries change nothing.

Applying: only APPROVED filer scale-error records have an apply path - the
factor-model engine reads approvals from this log and still runs its own
guards (stale value, partial block, inconsistent record). MtCO2e decisions
stay in their own owner-maintained file. Other approvals stay APPROVED
until an apply path exists for them (recorded in apply_note).

Run:
    python -m app.review.workflow
"""

from datetime import datetime

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger

log = get_logger(__name__, "review_workflow")

DECISIONS_LOG = settings.reference_dir / "review_decisions.csv"
LOG_COLUMNS = ["entry_no", "recommendation_id", "decision", "reviewer",
               "decided_at", "audit_reference", "comment"]
OUTPUT_DIR = settings.processed_dir / "nifty100" / "review_workflow"

STATUSES = ["DETECTED", "RECOMMENDED", "PENDING_REVIEW", "APPROVED",
            "REJECTED", "DEFERRED", "APPLIED"]
DECISION_TO_STATUS = {"APPROVE": "APPROVED", "REJECT": "REJECTED",
                      "DEFER": "DEFERRED"}
ALLOWED_FROM = {
    "APPROVE": {"RECOMMENDED", "PENDING_REVIEW", "DEFERRED"},
    "REJECT": {"DETECTED", "RECOMMENDED", "PENDING_REVIEW", "DEFERRED"},
    "DEFER": {"DETECTED", "RECOMMENDED", "PENDING_REVIEW"},
}
SOURCE_RULES = {"A40_FILER_SCALE_ERROR_REVIEW", "A43_AMBIGUOUS_GHG_UNIT",
                "A52_CROSSWALK_CANDIDATE", "A60_DEFERRED_ISSUE"}
SEVERITY_ORDER = ["INFO", "WARNING", "ERROR", "CRITICAL"]


# ==========================================================
# decision log
# ==========================================================

def read_log(path=DECISIONS_LOG):
    if not path.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in LOG_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    return df


def valid_timestamp(text):
    try:
        datetime.fromisoformat(text.strip())
        return True
    except ValueError:
        return False


def apply_log(entries, states, has_proposal, locked=None):
    """
    Validate log entries in order against current states.
    states: {recommendation_id: status} (updated in place)
    has_proposal: {recommendation_id: bool}
    locked: {recommendation_id: reason} - decided in a source file
    Returns (validation rows, {rec_id: accepted entry}).
    """
    locked = locked or {}
    results, accepted = [], {}
    for e in entries.to_dict("records"):
        rid, decision = e["recommendation_id"].strip(), \
            e["decision"].strip().upper()
        reason = ""
        if rid not in states:
            reason = "REFUSED_UNKNOWN_RECOMMENDATION"
        elif decision not in DECISION_TO_STATUS:
            reason = "REFUSED_INVALID_DECISION"
        elif not e["reviewer"].strip():
            reason = "REFUSED_NO_REVIEWER"
        elif not valid_timestamp(e["decided_at"]):
            reason = "REFUSED_INVALID_TIMESTAMP"
        elif not e["audit_reference"].strip():
            reason = "REFUSED_NO_AUDIT_REFERENCE"
        elif rid in locked:
            reason = f"REFUSED_DECIDED_IN_SOURCE_FILE ({locked[rid]})"
        elif states[rid] == "APPLIED":
            reason = "REFUSED_ALREADY_APPLIED"
        elif states[rid] not in ALLOWED_FROM[decision]:
            reason = f"REFUSED_TRANSITION_{states[rid]}_TO_{decision}"
        elif decision == "APPROVE" and not has_proposal.get(rid):
            reason = "REFUSED_NOTHING_TO_APPROVE"
        outcome = reason or "ACCEPTED"
        if not reason:
            states[rid] = DECISION_TO_STATUS[decision]
            accepted[rid] = {**e, "decision": decision}
        results.append({**e, "outcome": outcome})
    return pd.DataFrame(results, columns=LOG_COLUMNS + ["outcome"]), accepted


def fse_overrides(path=DECISIONS_LOG, fse=None):
    """
    Reviewer decisions on filer scale-error records (FSE-*) from the log,
    as {approval_id: {approval_decision, approved_by, approval_date}}.
    Used by the factor-model engine; records already decided in the source
    file keep the source decision.
    """
    from app.normalisation import factor_model as fm
    log_df = read_log(path)
    if not len(log_df):
        return {}
    fse = fse if fse is not None else pd.read_csv(
        fm.SCALE_DECISIONS, dtype=str, keep_default_na=False)
    states, locked = {}, {}
    for r in fse.to_dict("records"):
        if r["approval_decision"].strip():
            locked[r["approval_id"]] = r["approval_decision"]
            states[r["approval_id"]] = "LOCKED"
        else:
            states[r["approval_id"]] = "PENDING_REVIEW"
    entries = log_df[log_df["recommendation_id"].str.startswith("FSE-")]
    _, accepted = apply_log(entries, states,
                            {k: True for k in states}, locked)
    return {rid: {"approval_decision": e["decision"],
                  "approved_by": e["reviewer"],
                  "approval_date": e["decided_at"],
                  "reviewer_comments": f"{e['audit_reference']}: "
                                       f"{e['comment']}"}
            for rid, e in accepted.items()}


# ==========================================================
# queue
# ==========================================================

def recommendation_id(row):
    if row["detection_rule"] in SOURCE_RULES and row["linked_ref"]:
        return row["linked_ref"]
    return row["anomaly_id"]


def initial_status(row, production_status):
    rule, source = row["detection_rule"], row["source_decision"]
    if rule == "A60_DEFERRED_ISSUE":
        return "DEFERRED" if source == "DEFERRED_PENDING_REVIEW" else \
            "PENDING_REVIEW"
    if rule == "A40_FILER_SCALE_ERROR_REVIEW":
        return {"NO_DECISION": "PENDING_REVIEW", "APPROVE": "APPROVED",
                "REJECT": "REJECTED", "DEFER": "DEFERRED"}.get(
                    source, "PENDING_REVIEW")
    if rule == "A43_AMBIGUOUS_GHG_UNIT":
        if source.startswith("APPROVED"):
            return "APPLIED" if production_status == \
                "CONVERTED_APPROVED_UNIT_ASSUMPTION" else "APPROVED"
        return "PENDING_REVIEW"
    if rule == "A52_CROSSWALK_CANDIDATE":
        return "PENDING_REVIEW"
    has = bool(row["proposed_value"] or row["proposal"])
    return "RECOMMENDED" if has else "DETECTED"


def apply_path(row):
    rule = row["detection_rule"]
    if rule == "A40_FILER_SCALE_ERROR_REVIEW":
        return "FACTOR_MODEL_SCALE_CORRECTION"
    if rule == "A43_AMBIGUOUS_GHG_UNIT":
        return "MTCO2E_DECISION_FILE (owner-maintained)"
    return "NOT_IMPLEMENTED (needs a later phase)"


def build_queue(anomalies, production, log_df):
    prod = {(r["source_file"], r["context_reference"], r["requirement_id"],
             r["breakdown"]): r for r in production.to_dict("records")}
    anomalies = anomalies.assign(
        recommendation_id=anomalies.apply(recommendation_id, axis=1))
    rows = []
    for rid, g in anomalies.groupby("recommendation_id", sort=False):
        primary = g[g["detection_rule"].isin(SOURCE_RULES)]
        r = (primary if len(primary) else g).iloc[0].to_dict()
        fact = prod.get((r["source_file"], r["context_reference"],
                         r["requirement_id"], r["breakdown"]), {})
        sev = max(g["severity"], key=SEVERITY_ORDER.index)
        rows.append({
            **{k: r[k] for k in (
                "recommendation_id", "category", "company_name",
                "master_isin", "nse_symbol", "fy_slot", "requirement_id",
                "metric_name", "breakdown", "original_value",
                "original_unit", "normalised_value", "normalised_unit",
                "proposed_value", "proposed_unit",
                "proposed_multiplier_on_original", "proposal", "evidence",
                "evidence_files", "source_file", "context_reference",
                "source_url", "source_decision", "recommended_decision",
                "confidence")},
            "severity": sev,
            "detection_rules": " | ".join(sorted(set(g["detection_rule"]))),
            "reason": " || ".join(dict.fromkeys(g["reason"]))[:1000],
            "anomaly_ids": " | ".join(g["anomaly_id"]),
            "linked_refs": " | ".join(sorted(set(g["linked_ref"]) - {""})),
            "status": initial_status(r, fact.get("final_status", "")),
            "production_final_status": fact.get("final_status", ""),
            "production_scale_outcome": fact.get("scale_decision_outcome",
                                                 ""),
            "apply_path": apply_path(r)})
    q = pd.DataFrame(rows)

    states = dict(zip(q["recommendation_id"], q["status"]))
    has = dict(zip(q["recommendation_id"],
                   (q["proposed_value"] != "") | (q["proposal"] != "")))
    locked = {rid: src for rid, src in zip(q["recommendation_id"],
                                           q["source_decision"])
              if rid.startswith("FSE-") and src not in ("", "NO_DECISION")}
    validation, accepted = apply_log(log_df, states, has, locked)
    q["status"] = q["recommendation_id"].map(states)
    for col, key in (("reviewer_decision", "decision"),
                     ("reviewer", "reviewer"), ("decided_at", "decided_at"),
                     ("audit_reference", "audit_reference"),
                     ("reviewer_comment", "comment")):
        q[col] = q["recommendation_id"].map(
            lambda rid: accepted.get(rid, {}).get(key, ""))

    def applied(row):
        if row["status"] != "APPROVED":
            return row["status"], ""
        if row["apply_path"] == "FACTOR_MODEL_SCALE_CORRECTION":
            if row["production_final_status"] == \
                    "CONVERTED_APPROVED_SCALE_CORRECTION":
                return "APPLIED", ""
            note = ("approved; not yet in production - re-run "
                    "python -m app.normalisation.factor_model")
            if row["production_scale_outcome"].startswith("REFUSED"):
                note = f"approved but refused by the engine: " \
                       f"{row['production_scale_outcome']}"
            return "APPROVED", note
        return "APPROVED", f"apply path: {row['apply_path']}"

    res = q.apply(applied, axis=1, result_type="expand")
    q["status"], q["apply_note"] = res[0], res[1]
    return q, validation


def build(write=True, log_path=DECISIONS_LOG):
    from app.normalisation import factor_model as fm
    from app.review import anomaly_engine as ae
    anomalies = ae.detect()
    production = fm.read_production(fm.PRODUCTION["CY"])
    queue, validation = build_queue(anomalies, production,
                                    read_log(log_path))
    ev = pd.read_csv(ae.SOURCES["fse_evidence"], dtype=str,
                     keep_default_na=False)
    false_positives = pd.concat([
        ev[ev["classification"] == "NO_ERROR_FOUND"][
            ["master_nse_symbol", "fy_slot", "requirement_id", "breakdown",
             "original_value", "original_unit", "classification_reason",
             "source_file"]].assign(
            basis="cleared by the filer scale-error investigation"),
        queue[queue["status"] == "REJECTED"][
            ["nse_symbol", "fy_slot", "requirement_id", "breakdown",
             "original_value", "original_unit", "reason", "source_file"]]
        .rename(columns={"nse_symbol": "master_nse_symbol",
                         "reason": "classification_reason"})
        .assign(basis="rejected by a reviewer")], ignore_index=True)
    # known false-positive candidates: values the scale-error
    # investigation already cleared (status stays DETECTED - only a
    # reviewer can reject)
    cleared = set(zip(ev.loc[ev["classification"] == "NO_ERROR_FOUND",
                             "source_file"],
                      ev.loc[ev["classification"] == "NO_ERROR_FOUND",
                             "requirement_id"]))
    queue["previously_cleared_by_investigation"] = [
        (f, r) in cleared for f, r in zip(queue["source_file"],
                                          queue["requirement_id"])]
    summary = (queue.groupby(["category", "severity", "status"]).size()
               .reset_index(name="recommendations"))
    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        anomalies.to_csv(OUTPUT_DIR / "anomalies.csv", index=False,
                         encoding="utf-8-sig")
        queue.to_csv(OUTPUT_DIR / "review_queue.csv", index=False,
                     encoding="utf-8-sig")
        validation.to_csv(OUTPUT_DIR / "decision_log_validation.csv",
                          index=False, encoding="utf-8-sig")
        false_positives.to_csv(OUTPUT_DIR / "false_positive_register.csv",
                               index=False, encoding="utf-8-sig")
        summary.to_csv(OUTPUT_DIR / "workflow_summary.csv", index=False,
                       encoding="utf-8-sig")
    log.info("review workflow built", extra={"fields": {
        "anomalies": len(anomalies), "queue": len(queue)}})
    return {"anomalies": anomalies, "queue": queue,
            "validation": validation, "false_positives": false_positives,
            "summary": summary}


def main():
    out = build()
    q = out["queue"]
    pd.set_option("display.width", 200)
    print(f"anomalies: {len(out['anomalies'])}  recommendations: {len(q)}")
    print(pd.crosstab(q["category"], q["status"]).to_string())
    print("\nseverity:", q["severity"].value_counts().to_dict())
    print("decision log entries:", len(out["validation"]))
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
