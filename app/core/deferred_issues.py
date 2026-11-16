"""
Deferred data-quality issues register (data/reference/deferred_issues_register.csv).

Issues deferred by the owner stay DEFERRED_PENDING_REVIEW until a reviewer
records a resolution. Every later phase can ask which issues touch its
data, and the database loader (Phase 14) must call assert_none_open()
so that nothing deferred is ever loaded silently.
"""

import pandas as pd

from app.config import settings

REGISTER = settings.reference_dir / "deferred_issues_register.csv"
OPEN = "DEFERRED_PENDING_REVIEW"
REQUIRED_COLUMNS = [
    "issue_id", "issue_group", "company", "master_isin", "fy_slot",
    "period_role", "metric", "item", "original_value",
    "proposed_interpretation_or_correction", "evidence_file",
    "evidence_reference", "reason_for_deferral", "risk_impact", "status",
    "revisit_phase", "must_resolve_before", "raised_in", "raised_on",
    "resolution", "resolved_by", "resolved_on"]


class DeferredIssuesOpen(RuntimeError):
    pass


def load():
    if not REGISTER.exists():
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    return pd.read_csv(REGISTER, dtype=str, keep_default_na=False)


def open_issues(metric=None, isin=None, fy_slot=None):
    reg = load()
    reg = reg[reg["status"] == OPEN]
    if metric:
        reg = reg[reg["metric"].str.contains(metric, regex=False)]
    if isin:
        reg = reg[reg["master_isin"].isin([isin, ""])]
    if fy_slot:
        reg = reg[reg["fy_slot"].isin([fy_slot, "ALL"])]
    return reg


def issue_index():
    """{(master_isin, fy_slot, metric): [issue ids]} for open issues."""
    out = {}
    for r in open_issues().to_dict("records"):
        metric = r["metric"].split(" ")[0]
        out.setdefault((r["master_isin"], r["fy_slot"], metric), []).append(
            r["issue_id"])
    return out


def assert_none_open(context):
    reg = open_issues()
    if len(reg):
        raise DeferredIssuesOpen(
            f"{context}: {len(reg)} deferred issues are still open "
            f"({', '.join(reg['issue_id'].head(5))}...); resolve them in "
            f"{REGISTER} first")
