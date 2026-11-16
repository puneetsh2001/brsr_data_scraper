"""
Publication and comparability rules for API values.

Every value record gets:

  publication_status   why the value may or may not be used beyond display
  comparable           True only when the value may appear in charts,
                       comparisons and summary calculations

Statuses, in precedence order (the first that applies wins):

  DEFERRED_DATA_QUALITY        linked to an open deferred issue (DQ-xxx);
                               shown as filed (FLAG) or withheld (WITHHOLD)
                               according to the publication policy
  PENDING_SCALE_REVIEW         a filer scale-error record (FSE-xxx) is not
                               decided; shown as filed, the rule-normalised
                               number is withheld from the headline
  PENDING_CROSSWALK_REVIEW     value reached the metric only through a
                               candidate concept mapping
  PENDING_UNIT_DECISION        ambiguous unit awaiting an owner decision
  FLAGGED_RESTATEMENT_DIFFERS  unit interpretation flagged (restatement
                               differs)
  UNDETERMINED_UNIT            unit could not be determined (manual review)
  NOT_NORMALISED               unit missing or ambiguous; shown as filed
  TEXT_VALUE                   text, not a number
  PUBLISHED_APPROVED_DECISION  normalised under an owner-approved decision
  PUBLISHED                    reported (or reported zero) and normalised

Only PUBLISHED and PUBLISHED_APPROVED_DECISION are comparable. Nothing here
approves, corrects or changes a filed value.
"""

import os

DEFERRED_TREATMENTS = ("FLAG", "WITHHOLD")

STATUSES = {
    "DEFERRED_DATA_QUALITY": False,
    "PENDING_SCALE_REVIEW": False,
    "PENDING_CROSSWALK_REVIEW": False,
    "PENDING_UNIT_DECISION": False,
    "FLAGGED_RESTATEMENT_DIFFERS": False,
    "UNDETERMINED_UNIT": False,
    "NOT_NORMALISED": False,
    "TEXT_VALUE": False,
    "PUBLISHED_APPROVED_DECISION": True,
    "PUBLISHED": True,
}

UNIT_DECISION_STATUS = {
    "PENDING_REVIEW": "PENDING_UNIT_DECISION",
    "FLAGGED_RESTATEMENT_DIFFERS": "FLAGGED_RESTATEMENT_DIFFERS",
    "UNDETERMINED_MANUAL_REVIEW": "UNDETERMINED_UNIT",
}


def policy():
    """The publication policy in force. The treatment of deferred-issue
    values is an owner decision; until it is recorded, values are flagged
    (shown as filed, never comparable) and the policy says so."""
    treatment = os.getenv("BRSR_DEFERRED_TREATMENT", "").strip().upper()
    approved = treatment in DEFERRED_TREATMENTS
    return {"deferred_issue_treatment": treatment if approved else "FLAG",
            "owner_approved": approved,
            "note": None if approved else
            "owner decision pending: deferred-issue values are flagged and excluded "
            "from charts and comparisons until a treatment is approved"}


def classify(r, basis):
    """publication_status for a database row (see module docstring)."""
    if (r.get("deferred_issue_ids") or "").strip():
        return "DEFERRED_DATA_QUALITY"
    if (r.get("scale_decision_id") or "").strip() and \
            r.get("scale_decision_outcome") != "APPROVED_APPLIED":
        return "PENDING_SCALE_REVIEW"
    if r.get("crosswalk_status") == "CANDIDATE_REVIEW":
        return "PENDING_CROSSWALK_REVIEW"
    unit_status = UNIT_DECISION_STATUS.get((r.get("unit_decision") or "").strip())
    if unit_status and basis != "CALCULATED_APPROVED_UNIT_DECISION":
        return unit_status
    if r.get("conversion_status") == "PENDING_APPROVAL_UNIT_ASSUMPTION":
        return "PENDING_UNIT_DECISION"
    if basis == "REPORTED_NOT_NORMALISED":
        return "NOT_NORMALISED"
    if r.get("conversion_status") == "NOT_NUMERIC":
        return "TEXT_VALUE"
    if basis == "CALCULATED_APPROVED_UNIT_DECISION":
        return "PUBLISHED_APPROVED_DECISION"
    return "PUBLISHED"


def apply(rec, r, pol):
    """Add publication fields to a value record; withhold numbers that must
    not be read as normalised or comparable."""
    status = classify(r, rec["value_basis"])
    rec["publication_status"] = status
    rec["comparable"] = STATUSES[status] and rec["value_numeric"] is not None
    rec["withheld"] = None
    if status == "PENDING_SCALE_REVIEW":
        # the headline shows the filed value; the rule-normalised number of a
        # suspected scale error is kept only as withheld detail
        if rec["value"] is not None:
            rec["withheld"] = {
                "value": rec["value"], "unit": rec["unit"],
                "reason": "rule-normalised value of a filed figure under scale "
                          "review; not comparable until the record is decided"}
        rec["value"] = rec["value_numeric"] = rec["unit"] = None
        rec["scale_review"] = {
            "record_id": r.get("scale_decision_id"),
            "status": r.get("scale_decision_outcome"),
            "recommended_decision": r.get("scale_recommended_decision") or None,
            "proposed_value": r.get("scale_proposed_value") or None,
            "note": "proposal only; not applied"}
    elif status == "DEFERRED_DATA_QUALITY" and pol["deferred_issue_treatment"] == "WITHHOLD":
        rec["withheld"] = {"reason": "withheld under the publication policy: open "
                                     "deferred data-quality issue " + ", ".join(rec["review"]["deferred_issue_ids"])}
        rec["value"] = rec["value_numeric"] = rec["unit"] = None
        rec["reported_value"] = None
    return rec
