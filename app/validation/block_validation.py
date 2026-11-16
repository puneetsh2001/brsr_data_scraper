"""
Block validation (Phase 10).

A block is a group of related metrics from one report that share a unit /
scale or an arithmetic relationship (components, totals, intensity). A
block is only usable when all its members are present, normalised,
internally consistent and free of pending decisions. Partial corrections
of a block are never allowed (owner rule from the scale-error review).

Per company x FY slot x block:
  CONSISTENT                 all expected members present and normalised,
                             every block rule evaluated and passed
  INCOMPLETE                 a member is missing (not disclosed), not
                             normalised, or a block rule could not be
                             evaluated (missing values are never zero)
  INCONSISTENT               a block rule failed (Phase 9)
  BLOCKED_PENDING_DECISION   a pending scale / unit / crosswalk decision or
                             an open deferred issue touches the block
  NOT_REPORTED               no member reported (or no report for the slot)
Precedence: BLOCKED > INCONSISTENT > INCOMPLETE > CONSISTENT.

Also writes block review packages: every block containing a pending
filer scale-error record, with all members of the block, so a reviewer can
decide the block as a whole (Phase 11 workflow).

Read only over all inputs. Outputs: data/processed/nifty100/blocks/

Run:
    python -m app.validation.block_validation
"""

from collections import defaultdict

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger
from app.normalisation.factor_model import read_production

log = get_logger(__name__, "block_validation")

N = settings.processed_dir / "nifty100"
CY_FILE = N / "normalised" / "brsr_requirement_values_normalised.csv"
RESULTS = N / "validation" / "validation_results.csv"
COVERAGE = N / "brsr_requirement_coverage.csv"
FSE = (N / "filer_scale_error_review" / "approval" / "final_review" /
       "strong_recommendations_final_review.csv")
FSE_EVIDENCE = N / "filer_scale_error_review" / "filer_scale_error_evidence.csv"
OUTPUT_DIR = N / "blocks"

BLOCKS = {
    "ENERGY": ("Environmental", ["E01", "E02", "E03", "E04", "E05", "E06",
                                 "E07", "E08"]),
    "WATER": ("Environmental", ["E09", "E10", "E11", "E12", "E13", "E14",
                                "E15", "E16"]),
    "EMISSIONS": ("Environmental", ["E17", "E18", "E19", "E20"]),
    "WASTE": ("Environmental", [f"E{i}" for i in range(21, 36)]),
    "WORKFORCE": ("Social", ["S01", "S02"]),
    "WAGES": ("Social", ["S03", "S04", "S05", "S06", "S07", "S08", "S09"]),
    "SAFETY_TRAINING": ("Social", ["S11", "S12", "S13", "S14", "S15",
                                   "S16"]),
    "POSH": ("Social", ["S17", "S18", "S19", "S20"]),
    "BOARD": ("Governance", ["G01", "G02", "G03"]),
    "STAKEHOLDER_COMPLAINTS": ("Governance", ["G16", "G17"]),
    "CONSUMER_COMPLAINTS": ("Governance", ["G18", "G19"]),
}
BLOCK_OF = {m: b for b, (_, members) in BLOCKS.items() for m in members}

# Phase 9 rules evaluated per block; RST01 counts only at ERROR severity
# (scale-like mismatch with the next report); INFO restatements are normal
BLOCK_RULES = {"ENV01", "ENV02", "ENV03", "ENV04", "ENV05", "ENV06", "ENV07",
               "INT01", "INT02", "SOC01", "SOC02", "SOC03", "SOC04", "GOV01",
               "GOV02", "GOV03", "GOV04", "STR05", "STR08", "RST01"}
PRESENT = {"REPORTED", "REPORTED_ZERO"}
NOT_EXPECTED = {"NOT_APPLICABLE", "NOT_IN_TAXONOMY_VERSION",
                "EXTERNAL_SOURCE_REQUIRED"}
NORMALISED = {"NO_CONVERSION_NEEDED", "CONVERTED",
              "CONVERTED_FRACTION_TO_PERCENT",
              "CONVERTED_APPROVED_UNIT_ASSUMPTION",
              "CONVERTED_APPROVED_SCALE_CORRECTION", "NOT_NUMERIC"}


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def first_requirement(text):
    return (text or "").split(" ")[0]


def build(write=True):
    cy = read_production(CY_FILE)
    res = read(RESULTS)
    cov = read(COVERAGE)

    # ---- members ---------------------------------------------------------
    cov = cov[cov["requirement_id"].isin(BLOCK_OF)]
    status_of = {(r["isin"], r["fy_slot"], r["requirement_id"]): r
                 for r in cov.to_dict("records")}
    values = defaultdict(list)
    for r in cy[cy["requirement_id"].isin(BLOCK_OF)].to_dict("records"):
        values[(r["master_isin"], r["fy_slot"], r["requirement_id"])].append(r)

    # ---- rule outcomes per block ------------------------------------------
    rule_out = defaultdict(lambda: defaultdict(list))
    for r in res[res["rule_id"].isin(BLOCK_RULES)].to_dict("records"):
        block = BLOCK_OF.get(first_requirement(r["requirement_id"]))
        if not block:
            continue
        if r["rule_id"] == "RST01" and r["severity"] != "ERROR":
            continue
        rule_out[(r["master_isin"], r["fy_slot"], block)][r["status"]].append(
            f"{r['rule_id']}:{r['requirement_id']}"
            + (f"[{r['breakdown']}]" if r["breakdown"] else ""))

    members, blocks = [], []
    keys = sorted({(r["isin"], r["fy_slot"]) for r in cov.to_dict("records")})
    names = {(r["isin"], r["fy_slot"]): r for r in cov.to_dict("records")}
    for isin, slot in keys:
        info = names[(isin, slot)]
        for block, (pillar, reqs) in BLOCKS.items():
            present, missing, not_expected, not_norm, pending = \
                [], [], [], [], []
            no_report = True
            unit_tags = set()
            for req in reqs:
                c = status_of.get((isin, slot, req))
                status = c["status"] if c else "NOT_IN_COVERAGE"
                if status != "NO_REPORT":
                    no_report = False
                rows = values.get((isin, slot, req), [])
                pend = sorted({x for r in rows for x in
                               ([r["pending_review"]] if r["pending_review"]
                                else []) +
                               ([r["deferred_issue_ids"]]
                                if r["deferred_issue_ids"] else [])})
                if status == "REPORTED_PENDING_CROSSWALK_REVIEW":
                    pend.append("CROSSWALK_CANDIDATE_REVIEW")
                bad = sorted({r["final_status"] for r in rows
                              if r["final_status"] not in NORMALISED})
                for r in rows:
                    if r["original_unit"]:
                        unit_tags.add(r["original_unit"].split(" / ")[0])
                if status in PRESENT:
                    present.append(req)
                elif status in NOT_EXPECTED:
                    not_expected.append(req)
                elif status not in ("NO_REPORT",
                                    "REPORTED_PENDING_CROSSWALK_REVIEW"):
                    missing.append(req)
                if bad:
                    not_norm.append(f"{req}({'/'.join(bad)})")
                if pend:
                    pending.append(f"{req}({'; '.join(pend)})")
                members.append({
                    "master_isin": isin, "nse_symbol": info["nse_symbol"],
                    "fy_slot": slot, "block": block, "requirement_id": req,
                    "coverage_status": status, "values": len(rows),
                    "not_normalised_statuses": " | ".join(bad),
                    "pending": " | ".join(pend)})
            outcomes = rule_out.get((isin, slot, block), {})
            fails, unevaluated = outcomes.get("FAIL", []), \
                outcomes.get("NOT_EVALUATED", [])
            if no_report or not present and not pending:
                status = "NOT_REPORTED"
                reason = "no report for the slot" if no_report else \
                    "no member reported"
            elif pending:
                status, reason = "BLOCKED_PENDING_DECISION", \
                    "; ".join(pending)
            elif fails:
                status, reason = "INCONSISTENT", " | ".join(fails)
            elif missing or not_norm or unevaluated:
                status = "INCOMPLETE"
                reason = " | ".join(
                    ([f"missing {missing}"] if missing else []) +
                    ([f"not normalised {not_norm}"] if not_norm else []) +
                    ([f"rules not evaluated {unevaluated}"]
                     if unevaluated else []))
            else:
                status, reason = "CONSISTENT", ""
            blocks.append({
                "master_isin": isin, "nse_symbol": info["nse_symbol"],
                "company_name": info["company_name"], "fy_slot": slot,
                "block": block, "pillar": pillar,
                "members_expected": len(reqs) - len(not_expected),
                "members_present": len(present),
                "members_missing": " ".join(missing),
                "members_not_expected": " ".join(not_expected),
                "members_not_normalised": " ".join(not_norm),
                "pending_or_deferred": "; ".join(pending),
                "source_unit_tags": " | ".join(sorted(unit_tags)),
                "mixed_unit_tags": len({t for t in unit_tags
                                        if t not in ("iso4217:INR",
                                                     "xbrli:pure")}) > 1,
                "rules_passed": len(outcomes.get("PASS", [])),
                "rules_failed": len(fails),
                "rules_not_evaluated": len(unevaluated),
                "block_status": status, "reason": reason})
    blocks_df, members_df = pd.DataFrame(blocks), pd.DataFrame(members)

    # ---- review packages for pending scale-error records ----------------
    fse = read(FSE)
    evidence = read(FSE_EVIDENCE)
    ev = {(r["source_file"], r["requirement_id"], r["breakdown"]): r
          for r in evidence.to_dict("records")}
    fse_by = {(r["source_file"], r["requirement_id"], r["breakdown"]): r
              for r in fse.to_dict("records")}
    packages = []
    for (src, sym, slot, block), _ in fse.assign(
            block=fse["requirement_id"].map(BLOCK_OF)).groupby(
            ["source_file", "master_nse_symbol", "fy_slot", "block"]):
        pkg = f"BLK-{sym}-{slot}-{block}"
        records = fse[(fse["source_file"] == src)
                      & (fse["requirement_id"].map(BLOCK_OF) == block)]
        dependency = "BLOCK_PARTIAL" if (
            records["block_dependency"] == "BLOCK_PARTIAL").any() else \
            records["block_dependency"].iloc[0]
        rows = cy[(cy["source_file"] == src)
                  & (cy["requirement_id"].map(BLOCK_OF) == block)]
        for r in rows.to_dict("records"):
            k = (r["source_file"], r["requirement_id"], r["breakdown"])
            f, e = fse_by.get(k), ev.get(k)
            packages.append({
                "package_id": pkg, "nse_symbol": sym, "fy_slot": slot,
                "block": block, "package_dependency": dependency,
                "requirement_id": r["requirement_id"],
                "breakdown": r["breakdown"],
                "original_value": r["original_value"],
                "original_unit": r["original_unit"],
                "current_final_value": r["final_value"],
                "current_final_unit": r["final_unit"],
                "scale_record": f["approval_id"] if f else "",
                "recommended_decision": f["recommended_decision"] if f
                else "",
                "proposed_corrected_value": f["proposed_corrected_value"]
                if f else "",
                "proposed_corrected_unit": f["proposed_corrected_unit"]
                if f else "",
                "evidence_classification": e["classification"]
                if e is not None and "classification" in e else "",
                "needs_decision": bool(f) or (dependency == "BLOCK_PARTIAL"
                                              and r["original_value"] not in
                                              ("", "0")),
                "source_file": r["source_file"],
                "context_reference": r["context_reference"]})
    packages_df = pd.DataFrame(packages)

    definitions = pd.DataFrame([
        {"block": b, "pillar": p, "members": " ".join(m),
         "rules": " ".join(sorted(
             {"ENERGY": "ENV01 ENV02 ENV03 INT01 INT02 RST01",
              "WATER": "ENV01 ENV04 INT01 INT02 RST01",
              "EMISSIONS": "ENV07 INT01 INT02 RST01",
              "WASTE": "ENV01 ENV05 ENV06 RST01",
              "WORKFORCE": "SOC01 STR08 RST01",
              "WAGES": "SOC02 RST01",
              "SAFETY_TRAINING": "SOC01 STR08 RST01",
              "POSH": "SOC03 SOC04 STR08 RST01",
              "BOARD": "GOV01 GOV02 STR08 RST01",
              "STAKEHOLDER_COMPLAINTS": "GOV03 STR08 RST01",
              "CONSUMER_COMPLAINTS": "GOV04 STR08 RST01"}[b].split()))}
        for b, (p, m) in BLOCKS.items()])
    summary = (blocks_df.groupby(["block", "block_status"]).size()
               .unstack(fill_value=0).reset_index())
    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        definitions.to_csv(OUTPUT_DIR / "block_definitions.csv", index=False,
                           encoding="utf-8-sig")
        blocks_df.to_csv(OUTPUT_DIR / "block_status.csv", index=False,
                         encoding="utf-8-sig")
        members_df.to_csv(OUTPUT_DIR / "block_members.csv", index=False,
                          encoding="utf-8-sig")
        packages_df.to_csv(OUTPUT_DIR / "block_review_packages.csv",
                           index=False, encoding="utf-8-sig")
        summary.to_csv(OUTPUT_DIR / "block_summary.csv", index=False,
                       encoding="utf-8-sig")
    log.info("blocks built", extra={"fields": {"blocks": len(blocks_df)}})
    return {"blocks": blocks_df, "members": members_df,
            "packages": packages_df, "summary": summary,
            "definitions": definitions}


def main():
    out = build()
    pd.set_option("display.width", 200)
    print(out["summary"].to_string(index=False))
    p = out["packages"]
    print(f"\nreview packages: {p['package_id'].nunique()} "
          f"({p.groupby('package_dependency')['package_id'].nunique().to_dict()})")
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
