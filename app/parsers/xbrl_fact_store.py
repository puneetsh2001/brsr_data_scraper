"""
Parse every in-scope filing with app.parsers.xbrl_instance (Phase 6).

Scope: filing_manifest.csv rows with in_processing_scope = True and a
local XBRL file. Nothing upstream is modified; the current extraction
(multi_company_facts.csv) stays the production input until the
reconciliation below shows the new parser reproduces it.

Outputs  data/processed/nifty100/parsed/
  xbrl_facts.csv               one row per fact (fact_id = <filing_id>#<seq>)
  xbrl_contexts.csv
  xbrl_context_dimensions.csv  long table: context x dimension
  xbrl_units.csv
  xbrl_footnotes.csv
  xbrl_duplicate_facts.csv
  xbrl_parse_issues.csv
  xbrl_parse_summary.csv       per filing counts
  xbrl_reconciliation.csv      per filing: new parser vs current extraction

Run:
    python -m app.parsers.xbrl_fact_store [--limit N] [--no-reconcile]
"""

import argparse
import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger
from app.parsers.xbrl_instance import parse_instance

log = get_logger(__name__, "xbrl_fact_store")

MANIFEST = settings.processed_dir / "registry" / "filing_manifest.csv"
CURRENT_FACTS = settings.processed_dir / "nifty100" / "multi_company_facts.csv"
OUTPUT_DIR = settings.processed_dir / "nifty100" / "parsed"

FILING_COLUMNS = ["filing_id", "company_id", "fy_slot", "source_file"]
FACT_COLUMNS = FILING_COLUMNS + [
    "fact_id", "line", "xml_id", "concept", "namespace", "context_id",
    "context_key", "entity_identifier", "period_type", "period_start",
    "period_end", "instant", "dimension_key", "unit_id", "unit_measure",
    "data_type", "type_family", "value", "value_status", "numeric_value",
    "sign", "decimals", "precision", "value_decimal_places", "xml_lang",
    "footnotes", "duplicate_group", "duplicate_class", "issue_codes"]


class Writers:
    """Streaming CSV writers so ~1M facts never sit in memory at once."""

    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.files, self.writers = {}, {}
        self.folder = folder

    def write(self, name, rows, columns=None):
        if name not in self.writers:
            handle = open(self.folder / name, "w", newline="",
                          encoding="utf-8-sig")
            self.files[name] = handle
            self.writers[name] = csv.DictWriter(
                handle, fieldnames=columns or list(rows[0]) if rows
                else columns, extrasaction="ignore")
            self.writers[name].writeheader()
        if rows:
            self.writers[name].writerows(rows)

    def close(self):
        for handle in self.files.values():
            handle.close()


def scope():
    m = pd.read_csv(MANIFEST, dtype=str, keep_default_na=False)
    m = m[(m["in_processing_scope"] == "True") & (m["local_file_path"] != "")]
    return m[m["local_file_path"].map(lambda p: Path(p).exists())]


def key_hash(concept, context, value):
    return hashlib.sha1(f"{concept}\x1f{context}\x1f{value}".encode(
        "utf-8")).hexdigest()


def current_extraction_keys(files):
    """{source_file: Counter(key_hash)} from multi_company_facts.csv"""
    keys = defaultdict(Counter)
    if not CURRENT_FACTS.exists():
        return None
    for chunk in pd.read_csv(CURRENT_FACTS, dtype=str, keep_default_na=False,
                             usecols=["source_file", "metric_name",
                                      "context_reference", "value"],
                             chunksize=200_000):
        chunk = chunk[chunk["source_file"].isin(files)]
        for f, c, x, v in zip(chunk["source_file"], chunk["metric_name"],
                              chunk["context_reference"], chunk["value"]):
            keys[f][key_hash(c, x, v)] += 1
    return keys


def build(limit=None, reconcile=True):
    filings = scope()
    if limit:
        filings = filings.head(limit)
    writers = Writers(OUTPUT_DIR)
    summary, new_keys = [], {}
    for n, r in enumerate(filings.to_dict("records"), 1):
        path = Path(r["local_file_path"])
        base = {"filing_id": r["filing_id"], "company_id": r["company_id"],
                "fy_slot": r["fy_slot"], "source_file": path.name}
        parsed = parse_instance(path, r["filing_id"])
        for name, rows, cols in (
                ("xbrl_facts.csv", parsed["facts"], FACT_COLUMNS),
                ("xbrl_contexts.csv", parsed["contexts"], None),
                ("xbrl_context_dimensions.csv", parsed["dimensions"], None),
                ("xbrl_units.csv", parsed["units"], None),
                ("xbrl_footnotes.csv", parsed["footnotes"],
                 ["filing_id", "company_id", "fy_slot", "source_file",
                  "doc_key", "fact_xml_id", "footnote"]),
                ("xbrl_duplicate_facts.csv", parsed["duplicates"],
                 ["filing_id", "company_id", "fy_slot", "source_file",
                  "doc_key", "duplicate_group", "duplicate_class", "fact_id",
                  "line", "concept", "context_id", "dimension_key",
                  "unit_measure", "value", "decimals", "value_status"]),
                ("xbrl_parse_issues.csv", parsed["issues"],
                 ["filing_id", "company_id", "fy_slot", "source_file",
                  "doc_key", "line", "fact_id", "severity", "code",
                  "detail"])):
            rows = [{**base, **row} for row in rows]
            if cols is None and rows:
                cols = list(rows[0])
            if cols is not None:
                writers.write(name, rows, cols)
        sev = Counter(i["severity"] for i in parsed["issues"])
        facts = parsed["facts"]
        summary.append({
            **base,
            "taxonomy_version": parsed["document"].get("taxonomy_version",
                                                       ""),
            "manifest_taxonomy_version": r["taxonomy_version"],
            "declared_rounding": parsed["document"].get(
                "declared_rounding", ""),
            "facts": len(facts),
            "numeric_facts": sum(f["type_family"] in (
                "monetary", "shares", "pure", "numeric") for f in facts),
            "nil_facts": sum(f["value_status"] == "NIL" for f in facts),
            "empty_facts": sum(f["value_status"] == "EMPTY" for f in facts),
            "negative_values": sum(f["sign"] == "NEGATIVE" for f in facts),
            "contexts": len(parsed["contexts"]),
            "dimensioned_contexts": sum(c["dimension_count"] > 0
                                        for c in parsed["contexts"]),
            "units": len(parsed["units"]),
            "duplicate_groups": len({d["duplicate_group"]
                                     for d in parsed["duplicates"]}),
            "inconsistent_duplicate_groups": len({
                d["duplicate_group"] for d in parsed["duplicates"]
                if d["duplicate_class"] == "INCONSISTENT"}),
            "errors": sev.get("ERROR", 0), "warnings": sev.get("WARNING", 0),
            "info": sev.get("INFO", 0)})
        if reconcile:
            new_keys[path.name] = Counter(
                key_hash(f["concept"], f["context_id"], f["value"])
                for f in facts)
        if n % 50 == 0:
            log.info("parsed", extra={"fields": {"filings": n}})
    writers.close()

    summary = pd.DataFrame(summary)
    summary["taxonomy_version_agrees"] = (
        summary["taxonomy_version"] == summary["manifest_taxonomy_version"])
    summary.to_csv(OUTPUT_DIR / "xbrl_parse_summary.csv", index=False,
                   encoding="utf-8-sig")

    recon = None
    if reconcile:
        old = current_extraction_keys(set(new_keys))
        rows = []
        for f, new in new_keys.items():
            cur = old.get(f, Counter()) if old is not None else Counter()
            rows.append({
                "source_file": f, "new_parser_facts": sum(new.values()),
                "current_extraction_facts": sum(cur.values()),
                "matched": sum((new & cur).values()),
                "only_new_parser": sum((new - cur).values()),
                "only_current_extraction": sum((cur - new).values())})
        recon = pd.DataFrame(rows)
        recon["identical"] = (recon["only_new_parser"] == 0) & (
            recon["only_current_extraction"] == 0)
        recon.to_csv(OUTPUT_DIR / "xbrl_reconciliation.csv", index=False,
                     encoding="utf-8-sig")
    log.info("fact store built", extra={"fields": {
        "filings": len(summary), "facts": int(summary["facts"].sum())}})
    return summary, recon


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-reconcile", action="store_true")
    args = parser.parse_args()
    summary, recon = build(args.limit, not args.no_reconcile)
    print(f"filings parsed: {len(summary)}  facts: {summary['facts'].sum():,}")
    cols = ["facts", "numeric_facts", "nil_facts", "empty_facts",
            "negative_values", "contexts", "units", "duplicate_groups",
            "inconsistent_duplicate_groups", "errors", "warnings", "info"]
    print(summary[cols].sum().to_string())
    print("taxonomy version agrees with manifest:",
          int(summary["taxonomy_version_agrees"].sum()), "/", len(summary))
    issues = pd.read_csv(OUTPUT_DIR / "xbrl_parse_issues.csv", dtype=str,
                         keep_default_na=False)
    if len(issues):
        print("\nissues:")
        print(issues.groupby(["severity", "code"]).size().to_string())
    if recon is not None:
        print("\nreconciliation with current extraction:")
        print(recon[["new_parser_facts", "current_extraction_facts",
                     "matched", "only_new_parser",
                     "only_current_extraction"]].sum().to_string())
        print("identical filings:", int(recon["identical"].sum()), "/",
              len(recon))
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
