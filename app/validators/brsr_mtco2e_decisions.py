"""
Unit decisions for values reported in the ambiguous unit MtCO2e / GtCO2e.

Decision rules (project owner, 2026-11-11):
  * APPROVED_METRIC_TONNE   only when the next report restates the same
                            value in the unambiguous unit tCO2e
                            (restatement_evidence CONFIRMED_METRIC_TONNE)
  * APPROVED_MEGATONNE      only when the next report restates the value
                            x 1e6 in tCO2e (CONFIRMED_MEGATONNE)
  * FLAGGED_RESTATEMENT_DIFFERS   restatement exists but matches neither
                            reading - no conversion forced
  * UNDETERMINED_MANUAL_REVIEW    neither reading is plausible
  * PENDING_REVIEW          plausibility-only recommendation; NOT approved
                            (includes Tata Steel's megatonne
                            recommendations, which lack restatement
                            confirmation)

Inputs : data/processed/nifty100/normalised/brsr_mtco2e_assumption_evidence.csv
Output : data/reference/brsr_mtco2e_unit_decisions.csv
         (existing decisions are never overwritten)

Original values and units are not modified; the normaliser applies only
APPROVED_* decisions.

Run:
    python -m app.validators.brsr_mtco2e_decisions
"""

from datetime import date
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
EVIDENCE_FILE = (
    BASE_DIR / "data" / "processed" / "nifty100" / "normalised"
    / "brsr_mtco2e_assumption_evidence.csv"
)
DECISIONS_FILE = (
    BASE_DIR / "data" / "reference" / "brsr_mtco2e_unit_decisions.csv"
)

KEY = ["source_file", "context_reference", "requirement_id"]
DECIDED_BY = (
    f"project owner instruction {date.today().isoformat()}: approve only "
    "restatement-confirmed records"
)


def decide(row):
    evidence = row["restatement_evidence"]
    recommended = row["recommended_interpretation"]
    detail = (
        f"next report restatement: {row['next_report_restatement'] or 'none'};"
        f" implied tCO2e/GJ as tonne {row['implied_tco2e_per_gj_as_tonne']}"
        f", as megatonne {row['implied_tco2e_per_gj_as_megatonne']};"
        f" plausibility {row['plausibility']}"
    )

    if evidence == "CONFIRMED_METRIC_TONNE_BY_RESTATEMENT":
        return "APPROVED_METRIC_TONNE", "1", (
            "next report restates the same value in tCO2e - " + detail
        )
    if evidence == "CONFIRMED_MEGATONNE_BY_RESTATEMENT":
        return "APPROVED_MEGATONNE", "1000000", (
            "next report restates value x 1e6 in tCO2e - " + detail
        )
    if evidence == "ZERO_VALUE_UNINFORMATIVE":
        return "PENDING_REVIEW", "", (
            "zero value - restatement cannot confirm the unit; " + detail
        )
    if evidence == "RESTATEMENT_DIFFERS":
        return "FLAGGED_RESTATEMENT_DIFFERS", "", (
            "restatement matches neither reading; no conversion forced - "
            + detail
        )
    if recommended == "UNDETERMINED":
        return "UNDETERMINED_MANUAL_REVIEW", "", (
            "neither reading plausible; likely energy or emissions data "
            "issue - " + detail
        )

    note = ""
    if row["master_nse_symbol"] == "TATASTEEL":
        note = "Tata Steel held for further review (no restatement " \
               "confirmation); "
    return "PENDING_REVIEW", "", (
        f"{note}plausibility-only recommendation "
        f"({recommended}) - not approved; " + detail
    )


def main():
    evidence = pd.read_csv(EVIDENCE_FILE, dtype=str).fillna("")

    rows = []
    for row in evidence.to_dict("records"):
        decision, factor, why = decide(row)
        rows.append({
            "source_file": row["source_file"],
            "context_reference": row["context_reference"],
            "requirement_id": row["requirement_id"],
            "breakdown": row["breakdown"],
            "company_name": row["company_name"],
            "master_nse_symbol": row["master_nse_symbol"],
            "fy_slot": row["fy_slot"],
            "original_value": row["original_value"],
            "original_unit": row["original_unit"],
            "recommended_interpretation": row["recommended_interpretation"],
            "restatement_evidence": row["restatement_evidence"],
            "decision": decision,
            "approved_factor": factor,
            "approval_evidence": why,
            "decided_by": DECIDED_BY,
        })

    new = pd.DataFrame(rows)

    if DECISIONS_FILE.exists():
        existing = pd.read_csv(DECISIONS_FILE, dtype=str).fillna("")
        known = set(map(tuple, existing[KEY].values))
        new = new[[tuple(k) not in known for k in new[KEY].values]]
        combined = pd.concat([existing, new], ignore_index=True)
        print("Existing decisions kept:", len(existing))
    else:
        combined = new

    duplicates = combined.duplicated(KEY).sum()
    if duplicates:
        raise SystemExit(f"{duplicates} duplicate decision keys - stopping")

    combined.to_csv(DECISIONS_FILE, index=False, encoding="utf-8-sig")
    print("New decisions added:", len(new))
    print(combined["decision"].value_counts().to_string())
    print("\nDecisions file:", DECISIONS_FILE)


if __name__ == "__main__":
    main()
