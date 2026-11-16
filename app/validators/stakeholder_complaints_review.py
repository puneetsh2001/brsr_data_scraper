"""
G16/G17 stakeholder-complaint reconciliation review.

Read-only over every existing output. Writes a review layer only:
    data/processed/nifty100/review/g16_g17_stakeholder_reconciliation/

Raw layer (unchanged): the 876 OtherStakeHolderAxis facts in
    review/g16_g17_other_stakeholder/g16_g17_new_facts_with_evidence.csv
plus the standard-group facts in brsr_requirement_values(_py).csv.

Analytical layer (this module):
  - resolution class per raw fact
      DUPLICATE_OF_STANDARD_GROUP      same group name and same value
      MANUAL_REVIEW_REQUIRED           same group name, different value
      AMBIGUOUS_STAKEHOLDER_MAPPING    name combines several standard groups
      GENUINE_OTHER_GROUP              different group
  - automated evidence for the different-value records (G16/G17 swap,
    row shift across standard members, prior / next report values)
  - name-based year-over-year comparison of "other" rows; typed members
    OtherStakeHolderDomainN are row positions, never identities
  - re-evaluation of the C08 restatement notes on "other" rows
  - per company x FY x metric reconciliation: a total is only labelled
    RELIABLE_TOTAL when nothing in it is unresolved, ambiguous or under
    filer-error investigation

Nothing is corrected, swapped or summed across overlapping records.

Run:
    python -m app.validators.stakeholder_complaints_review
"""

import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger

log = get_logger(__name__, "stakeholder_complaints_review")

N = settings.processed_dir / "nifty100"
RAW_FACTS = N / "review" / "g16_g17_other_stakeholder" / \
    "g16_g17_new_facts_with_evidence.csv"
VALUES = {"CY": N / "brsr_requirement_values.csv",
          "PY": N / "brsr_requirement_values_py.csv"}
VALIDATION_ISSUES = N / "normalised" / \
    "brsr_normalisation_validation_issues.csv"
OUTPUT_DIR = N / "review" / "g16_g17_stakeholder_reconciliation"

METRICS = ("G16", "G17")
OTHER_AXIS = "OtherStakeHolderAxis"
STANDARD_GROUPS = ["Communities", "Investors", "Shareholders",
                   "EmployeesAndWorkers", "Customers", "ValueChainPartners"]

# filings whose standard members are under filer-error investigation
# (see investigate()); their totals are never labelled reliable
INVESTIGATED = {("ADANIENSOL", "2024-25"), ("POWERGRID", "2023-24")}

RESOLUTION = {
    "DUPLICATES_STANDARD_GROUP": "DUPLICATE_OF_STANDARD_GROUP",
    "NAME_MATCHES_STANDARD_GROUP_VALUE_DIFFERS": "MANUAL_REVIEW_REQUIRED",
    "NAME_MATCHES_STANDARD_GROUP_NOT_REPORTED_THERE":
        "MANUAL_REVIEW_REQUIRED",
    "NAME_COMBINES_STANDARD_GROUPS": "AMBIGUOUS_STAKEHOLDER_MAPPING",
    "OTHER_GROUP": "GENUINE_OTHER_GROUP",
}
UNRESOLVED = {"MANUAL_REVIEW_REQUIRED", "AMBIGUOUS_STAKEHOLDER_MAPPING"}


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def num(value):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def fmt(d):
    return "" if d is None else format(d, "f")


# ==========================================================
# name normalisation (original names are always kept)
# ==========================================================

PLACEHOLDER_KEYS = {"", "0", "na", "nil", "none", "notreceived", "notapplicable"}
GENERIC_LEAD = re.compile(r"^\s*(others?|other\s+stakeholders?)\b[\s:\-–(]*",
                          re.I)
SPECIFY = re.compile(r"\(?\s*please\s+specify\s*\)?", re.I)


def clean_name(name):
    text = unicodedata.normalize("NFKC", name or "").replace("\ufffd", "'")
    return " ".join(text.split())


def exact_key(name):
    text = clean_name(name).lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", text)


def core_key(name):
    """Name without generic 'Other(s) (...)' wrapper and 'please specify'."""
    text = SPECIFY.sub("", clean_name(name))
    text = GENERIC_LEAD.sub("", text).strip(" ()")
    return exact_key(text)


def name_quality(name):
    if exact_key(name) in PLACEHOLDER_KEYS:
        return "PLACEHOLDER_NAME"
    if core_key(name) == "":
        return "GENERIC_OTHER_NAME"
    return "NAMED"


# ==========================================================
# inputs
# ==========================================================

def period_key(r):
    return (r["period_start"], r["period_end"], r["instant_date"])


def load_standard():
    """{(source_file, metric, period, group): row} + per-filing tables."""
    index, rows = {}, []
    for role, path in VALUES.items():
        v = read(path)
        v = v[v["requirement_id"].isin(METRICS)
              & ~v["dimensions"].str.contains(OTHER_AXIS)]
        for r in v.to_dict("records"):
            group = r["breakdown"].split("ComplaintReceivedFrom")[-1] \
                .replace("Member", "")
            r["group"], r["file_role"] = group, role
            key = (r["source_file"], r["requirement_id"], period_key(r),
                   group)
            if key in index:
                raise ValueError(f"duplicate standard fact {key}")
            index[key] = r
            rows.append(r)
    return index, pd.DataFrame(rows)


def load_raw():
    raw = read(RAW_FACTS)
    raw["file_role"] = raw["output_file"].map(
        lambda f: "PY" if f.endswith("_py.csv") else "CY")
    raw["domain"] = raw["breakdown"].str.split("=").str[-1]
    raw["name_clean"] = raw["stakeholder_group_name_in_xbrl"].map(clean_name)
    raw["name_exact_key"] = raw["stakeholder_group_name_in_xbrl"].map(
        exact_key)
    raw["name_core_key"] = raw["stakeholder_group_name_in_xbrl"].map(core_key)
    raw["name_quality"] = raw["stakeholder_group_name_in_xbrl"].map(
        name_quality)
    return raw


# ==========================================================
# 1. classification and evidence
# ==========================================================

def classify(raw, std):
    out = raw.copy()
    out["resolution_class"] = out["overlap_with_standard_group"].map(
        RESOLUTION)
    if out["resolution_class"].isna().any():
        raise ValueError("unknown overlap classification")
    out["matched_standard_groups"] = out[
        "standard_group_value_same_report"].map(
        lambda s: " | ".join(p.split("=")[0] for p in s.split(" | ") if p))

    evidence_cols = defaultdict(list)
    for r in out.to_dict("records"):
        ev = evidence_for(r, std)
        for k in ("standard_fact_context", "same_value_in_other_standard_group",
                  "other_metric_standard_value", "suspected_pattern",
                  "value_coincides_with_standard_group", "evidence_note"):
            evidence_cols[k].append(ev.get(k, ""))
    for k, values in evidence_cols.items():
        out[k] = values

    # remarks of the same group row anywhere in the filing (CY / PY / G16 /
    # G17 contexts share the row); only informative text counts
    remarks = defaultdict(set)
    for r in out.to_dict("records"):
        text = " ".join(r["remarks_in_xbrl"].split())
        if re.search(r"[A-Za-z]{3}", text):
            remarks[(r["source_file"], r["name_exact_key"])].add(text)
    patterns = []
    for r in out.to_dict("records"):
        if r["resolution_class"] != "MANUAL_REVIEW_REQUIRED":
            patterns.append(r["suspected_pattern"])
            continue
        if (r["nse_symbol"], r["fy_slot"]) in INVESTIGATED:
            label = ("ROW_SHIFT" if r["nse_symbol"] == "POWERGRID"
                     else "G16_G17_SWAP_IN_RELISTED_ROW")
            patterns.append(f"SEE_FILER_ERROR_INVESTIGATION ({label})")
            continue
        rem = remarks.get((r["source_file"], r["name_exact_key"]))
        patterns.append(r["suspected_pattern"] or (
            "NO_AUTOMATED_EXPLANATION (filer remarks: "
            + " | ".join(sorted(rem))[:200] + ")" if rem else
            "NO_AUTOMATED_EXPLANATION"))
    out["suspected_pattern"] = patterns
    return out


def evidence_for(r, std):
    period = (r["period_start"], r["period_end"], r["instant_date"])
    value = num(r["value"])
    groups = [g for g in r["matched_standard_groups"].split(" | ") if g]
    ev = {}
    ctx = [std.get((r["source_file"], r["requirement_id"], period, g))
           for g in groups]
    ev["standard_fact_context"] = " | ".join(
        f"{g}:{c['context_reference']}" for g, c in zip(groups, ctx) if c)

    # same value in a different standard group of the same table
    same = []
    for g in STANDARD_GROUPS:
        s = std.get((r["source_file"], r["requirement_id"], period, g))
        if s and g not in groups and value is not None and value != 0 \
                and num(s["value"]) == value:
            same.append(f"{g}={s['value']}")
    ev["same_value_in_other_standard_group"] = " | ".join(same)

    if r["resolution_class"] == "GENUINE_OTHER_GROUP":
        ev["value_coincides_with_standard_group"] = bool(same)
        if same:
            ev["evidence_note"] = ("value equals a standard group value in "
                                   "the same table; informational (small "
                                   "counts coincide often)")
        return ev

    if r["resolution_class"] != "MANUAL_REVIEW_REQUIRED":
        return ev

    # G16 <-> G17 swap: this "other" value equals the standard value of
    # the same group under the other metric, in the same period
    other_metric = "G17" if r["requirement_id"] == "G16" else "G16"
    o = std.get((r["source_file"], other_metric,
                 other_period(r, std, other_metric), groups[0])) \
        if groups else None
    ev["other_metric_standard_value"] = o["value"] if o else ""
    patterns = []
    if o and value is not None and num(o["value"]) == value and value != 0:
        patterns.append("G16_G17_SWAP_VS_STANDARD")
    if same:
        patterns.append("ROW_SHIFT_VS_STANDARD_MEMBERS")
    ev["suspected_pattern"] = " | ".join(patterns)
    return ev


def other_period(r, std, metric):
    """G16 uses durations, G17 instants: find the matching period."""
    end = r["period_end"] or r["instant_date"]
    for (f, m, p, _g) in std:
        if f == r["source_file"] and m == metric and \
                (p[1] == end or p[2] == end):
            return p
    return ("", "", "")


# ==========================================================
# 2. name-based year-over-year comparison
# ==========================================================

def match_rows(cur, nxt):
    """
    Match 'other' rows of report t (CY) with report t+1 (PY) by name.
    Returns [(cur_row | None, next_row | None, method)].
    """
    pairs, used_c, used_n = [], set(), set()
    for method, keyf in (("EXACT_NAME", "name_exact_key"),
                         ("CORE_NAME", "name_core_key")):
        ck = Counter(c[keyf] for i, c in enumerate(cur) if i not in used_c)
        nk = Counter(n[keyf] for j, n in enumerate(nxt) if j not in used_n)
        for i, c in enumerate(cur):
            if i in used_c or not c[keyf] or c["name_quality"] != "NAMED":
                continue
            js = [j for j, n in enumerate(nxt) if j not in used_n
                  and n[keyf] == c[keyf]]
            if ck[c[keyf]] == 1 and nk[c[keyf]] == 1 and len(js) == 1:
                pairs.append((c, nxt[js[0]], method))
                used_c.add(i)
                used_n.add(js[0])
    # one generic / placeholder name on each side: low-confidence pair
    gc = [i for i, c in enumerate(cur) if i not in used_c
          and c["name_quality"] != "NAMED"]
    gn = [j for j, n in enumerate(nxt) if j not in used_n
          and n["name_quality"] != "NAMED"]
    if len(gc) == 1 and len(gn) == 1:
        pairs.append((cur[gc[0]], nxt[gn[0]], "GENERIC_NAME_ONLY"))
        used_c.add(gc[0])
        used_n.add(gn[0])
    for i, c in enumerate(cur):
        if i not in used_c:
            dup = sum(1 for x in cur if x["name_core_key"] == c["name_core_key"]
                      and x["name_core_key"]) > 1 or c["name_quality"] != \
                "NAMED"
            pairs.append((c, None, "AMBIGUOUS_NAME" if dup else
                          "ONLY_IN_CURRENT_REPORT"))
    for j, n in enumerate(nxt):
        if j not in used_n:
            pairs.append((None, n, "ONLY_IN_NEXT_REPORT"))
    return pairs


def yoy(classified, std_df):
    std_py = {(r["master_isin"], r["fy_slot"], r["requirement_id"],
               r["group"]): r for r in std_df[std_df["file_role"] == "PY"]
              .to_dict("records")}
    cy = classified[classified["file_role"] == "CY"]
    py = classified[classified["file_role"] == "PY"]
    slots = sorted(classified["fy_slot"].unique())
    nxt_slot = {s: slots[i + 1] for i, s in enumerate(slots[:-1])}
    rows = []
    for (isin, slot, metric), group in cy.groupby(
            ["master_isin", "fy_slot", "requirement_id"]):
        if slot not in nxt_slot:
            continue
        later = py[(py["master_isin"] == isin)
                   & (py["fy_slot"] == nxt_slot[slot])
                   & (py["requirement_id"] == metric)]
        cur, nxt = group.to_dict("records"), later.to_dict("records")
        if not nxt:
            continue
        # supporting context (not identity): same multiset of values in
        # both reports, at least two rows, not all equal
        cv = sorted(num(x["value"]) or Decimal(0) for x in cur)
        nv = sorted(num(x["value"]) or Decimal(0) for x in nxt)
        values_preserved = len(cv) >= 2 and cv == nv and len(set(cv)) > 1
        for c, n, method in match_rows(cur, nxt):
            base = c or n
            row = {"master_isin": isin, "nse_symbol": base["nse_symbol"],
                   "fy_slot": slot, "next_report_fy_slot": nxt_slot[slot],
                   "requirement_id": metric, "match_method": method}
            for side, x in (("current", c), ("next_py", n)):
                row[f"{side}_name"] = x["stakeholder_group_name_in_xbrl"] \
                    if x else ""
                row[f"{side}_domain"] = x["domain"] if x else ""
                row[f"{side}_value"] = x["value"] if x else ""
                row[f"{side}_resolution"] = x["resolution_class"] if x \
                    else ""
                row[f"{side}_source_file"] = x["source_file"] if x else ""
                row[f"{side}_fact_line"] = x["xml_line"] if x else ""
            row["value_set_preserved_across_reports"] = values_preserved
            if c and not n and c["resolution_class"] ==                     "DUPLICATE_OF_STANDARD_GROUP":
                g = c["matched_standard_groups"]
                s = std_py.get((isin, nxt_slot[slot], metric, g))
                if s is not None:
                    row.update({"match_method": "STANDARD_GROUP_IDENTITY",
                                "next_py_name": f"standard member {g}",
                                "next_py_domain": "",
                                "next_py_value": s["value"],
                                "next_py_resolution": "STANDARD_MEMBER",
                                "next_py_source_file": s["source_file"],
                                "next_py_fact_line": ""})
                    n = {"value": s["value"], "domain": c["domain"],
                         "name_exact_key": c["name_exact_key"]}
                    method = "STANDARD_GROUP_IDENTITY"
            if c and n:
                same = num(c["value"]) == num(n["value"])
                moved = c["domain"] != n["domain"]
                row["comparison"] = ("SAME_VALUE" if same else
                                     "VALUE_RESTATED")
                row["row_order_changed"] = moved
                row["name_changed"] = c["name_exact_key"] != \
                    n["name_exact_key"]
                row["confidence"] = {"EXACT_NAME": "HIGH",
                                     "STANDARD_GROUP_IDENTITY": "HIGH",
                                     "CORE_NAME": "MEDIUM",
                                     "GENERIC_NAME_ONLY": "LOW"}[method]
            else:
                row.update({"comparison": "NOT_COMPARABLE",
                            "row_order_changed": "", "name_changed": "",
                            "confidence": ""})
            rows.append(row)
    return pd.DataFrame(rows)


def reevaluate_notes(yoy_df, classified):
    """C08 notes on OtherStakeHolderAxis rows, judged by name."""
    issues = read(VALIDATION_ISSUES)
    notes = issues[(issues["check"] == "C08_NEXT_REPORT_PY")
                   & issues["requirement_id"].isin(METRICS)
                   & issues["breakdown"].str.contains(OTHER_AXIS)]
    rows = []
    for r in notes.to_dict("records"):
        domain = r["breakdown"].split("=")[-1]
        m = re.match(r"\s*(\S+) count vs next report \((\S+)\) restates as "
                     r"(\S+)", r["detail"])
        cur_val, nxt_file, nxt_val = m.groups() if m else ("", "", "")
        hit = yoy_df[(yoy_df["master_isin"] == r["master_isin"])
                     & (yoy_df["fy_slot"] == r["fy_slot"])
                     & (yoy_df["requirement_id"] == r["requirement_id"])
                     & (yoy_df["current_domain"] == domain)]
        cur_name = classified[(classified["master_isin"] == r["master_isin"])
                              & (classified["fy_slot"] == r["fy_slot"])
                              & (classified["file_role"] == "CY")
                              & (classified["requirement_id"]
                                 == r["requirement_id"])
                              & (classified["domain"] == domain)]
        out = {"nse_symbol": r["master_nse_symbol"], "fy_slot": r["fy_slot"],
               "requirement_id": r["requirement_id"], "row_position": domain,
               "note_current_value": cur_val, "note_next_py_value": nxt_val,
               "note_next_report": nxt_file, "note_detail": r["detail"],
               "current_name": cur_name["stakeholder_group_name_in_xbrl"]
               .iloc[0] if len(cur_name) else ""}
        if hit.empty:
            out.update({"name_based_result": "NO_CURRENT_ROW_FOUND",
                        "re_evaluation": "NOT_EVALUATED"})
        else:
            h = hit.iloc[0]
            out.update({
                "matched_next_name": h["next_py_name"],
                "matched_next_position": h["next_py_domain"],
                "matched_next_value": h["next_py_value"],
                "match_method": h["match_method"],
                "value_set_preserved_across_reports":
                    h["value_set_preserved_across_reports"],
                "name_based_result": h["comparison"],
                "current_resolution": h["current_resolution"]})
            if h["comparison"] == "SAME_VALUE":
                out["re_evaluation"] = (
                    "FALSE_POSITIVE_RELISTED_ROW_MATCHES_STANDARD_MEMBER"
                    if h["match_method"] == "STANDARD_GROUP_IDENTITY" else
                    "FALSE_POSITIVE_ROW_ORDER_CHANGED"
                    if h["row_order_changed"] else
                    "FALSE_POSITIVE_SAME_VALUE_BY_NAME")
            elif h["comparison"] == "VALUE_RESTATED":
                out["re_evaluation"] = {
                    "EXACT_NAME": "GENUINE_RESTATEMENT_CONFIRMED_BY_NAME",
                    "STANDARD_GROUP_IDENTITY":
                        "GENUINE_RESTATEMENT_CONFIRMED_BY_STANDARD_MEMBER",
                    "CORE_NAME": "PROBABLE_RESTATEMENT_REVIEW_NAME_MATCH",
                    "GENERIC_NAME_ONLY":
                        "GENERIC_NAME_ONLY_MANUAL_REVIEW"}[h["match_method"]]
            elif str(h["value_set_preserved_across_reports"]) == "True":
                out["re_evaluation"] = (
                    "LIKELY_FALSE_POSITIVE_ROW_ORDER_VALUES_PRESERVED")
            elif h["match_method"] == "AMBIGUOUS_NAME":
                out["re_evaluation"] = "AMBIGUOUS_NAME_MANUAL_REVIEW"
            else:
                out["re_evaluation"] = "NOT_COMPARABLE_GROUP_RENAMED_OR_DROPPED"
        rows.append(out)
    return pd.DataFrame(rows)


# ==========================================================
# 3. reconciliation per company x FY x metric (CY)
# ==========================================================

def expected_groups(std_df):
    """Standard groups present per taxonomy version (observed)."""
    return std_df.groupby("taxonomy_version")["group"].agg(set).to_dict()


def reconcile(classified, std_df):
    expected = expected_groups(std_df)
    cy_std = std_df[std_df["file_role"] == "CY"]
    cy_raw = classified[classified["file_role"] == "CY"]
    keys = set(zip(cy_std["master_isin"], cy_std["fy_slot"],
                   cy_std["requirement_id"])) | set(
        zip(cy_raw["master_isin"], cy_raw["fy_slot"],
            cy_raw["requirement_id"]))
    std_g = {k: g for k, g in cy_std.groupby(
        ["master_isin", "fy_slot", "requirement_id"])}
    raw_g = {k: g for k, g in cy_raw.groupby(
        ["master_isin", "fy_slot", "requirement_id"])}
    rows = []
    for key in sorted(keys):
        s = std_g.get(key, pd.DataFrame(columns=cy_std.columns))
        o = raw_g.get(key, pd.DataFrame(columns=cy_raw.columns))
        sym = (s["master_nse_symbol"].iloc[0] if len(s)
               else o["nse_symbol"].iloc[0])
        version = s["taxonomy_version"].iloc[0] if len(s) else ""
        row = {"master_isin": key[0], "nse_symbol": sym, "fy_slot": key[1],
               "requirement_id": key[2], "taxonomy_version": version}
        values = {r["group"]: num(r["value"]) for r in s.to_dict("records")}
        for g in STANDARD_GROUPS:
            row[f"std_{g}"] = fmt(values.get(g)) if g in values else ""
        missing = sorted(expected.get(version, set()) - set(values))
        non_numeric = [g for g, v in values.items() if v is None]
        std_total = sum((v for v in values.values() if v is not None),
                        Decimal(0))

        def total(cls):
            part = o[o["resolution_class"] == cls]
            vals = [num(v) for v in part["value"]]
            return part, sum((v for v in vals if v is not None), Decimal(0))

        gen, gen_total = total("GENUINE_OTHER_GROUP")
        dup, dup_total = total("DUPLICATE_OF_STANDARD_GROUP")
        man, man_total = total("MANUAL_REVIEW_REQUIRED")
        amb, amb_total = total("AMBIGUOUS_STAKEHOLDER_MAPPING")
        gen_bad = [v for v in gen["value"] if num(v) is None]
        # same non-zero number in Investors and Shareholders while the
        # filer also reports a combined "Investors and Shareholders" row
        combined = len(amb) > 0 and values.get("Investors") is not None \
            and values.get("Investors") == values.get("Shareholders") \
            and values.get("Investors", 0) != 0
        row.update({
            "standard_groups_reported": len(values),
            "standard_groups_missing": " | ".join(missing),
            "standard_total": fmt(std_total),
            "genuine_other_rows": len(gen),
            "genuine_other_total": fmt(gen_total),
            "duplicate_rows_excluded": len(dup),
            "duplicate_value_excluded": fmt(dup_total),
            "manual_review_rows": len(man),
            "manual_review_value": fmt(man_total),
            "ambiguous_rows": len(amb),
            "ambiguous_value": fmt(amb_total),
            "under_filer_error_investigation":
                (sym, key[1]) in INVESTIGATED,
            "investors_equals_shareholders_with_combined_row": combined,
        })
        reasons = []
        if not len(s):
            reasons.append("NO_STANDARD_GROUP_FACTS")
        if missing:
            reasons.append("STANDARD_GROUPS_MISSING")
        if non_numeric or gen_bad:
            reasons.append("NON_NUMERIC_VALUES")
        if len(man):
            reasons.append("MANUAL_REVIEW_ROWS")
        if len(amb):
            reasons.append("AMBIGUOUS_ROWS")
        if combined:
            reasons.append("POSSIBLE_DOUBLE_COUNT_IN_STANDARD_MEMBERS")
        if row["under_filer_error_investigation"]:
            reasons.append("SUSPECTED_FILER_ERROR")
        base = std_total + gen_total
        if reasons:
            row.update({"total_status": "NO_RELIABLE_TOTAL",
                        "reliable_total": "",
                        "lower_bound_total": fmt(base) if len(s) else "",
                        "upper_bound_total": fmt(base + man_total + amb_total)
                        if len(s) else "",
                        "bounds_note": (
                            "bounds use values as filed, which are under "
                            "filer-error investigation; not meaningful "
                            "until resolved"
                            if row["under_filer_error_investigation"] else
                            "bounds only; missing standard groups are not "
                            "treated as zero; not a verified total"
                            if missing else
                            "bounds only; not a verified total"),
                        "reasons": " | ".join(reasons)})
        else:
            row.update({"total_status": "RELIABLE_TOTAL",
                        "reliable_total": fmt(base),
                        "lower_bound_total": "", "upper_bound_total": "",
                        "bounds_note": "", "reasons": ""})
        rows.append(row)
    return pd.DataFrame(rows)


# ==========================================================
# 4. filer-error investigations
# ==========================================================

def stakeholder_table(std_df, classified, symbol, slot):
    """Standard + other rows of one company's reports around a slot."""
    rows = []
    for r in std_df[std_df["master_nse_symbol"] == symbol].to_dict("records"):
        rows.append({"report_fy_slot": r["fy_slot"],
                     "period_role": r["period_role"],
                     "requirement_id": r["requirement_id"],
                     "row": f"STANDARD:{r['group']}", "name": "",
                     "value": r["value"],
                     "period_end_or_instant": r["period_end"]
                     or r["instant_date"],
                     "context_reference": r["context_reference"],
                     "source_file": r["source_file"]})
    for r in classified[classified["nse_symbol"] == symbol].to_dict(
            "records"):
        rows.append({"report_fy_slot": r["fy_slot"],
                     "period_role": r["period_role"],
                     "requirement_id": r["requirement_id"],
                     "row": f"OTHER:{r['domain']}",
                     "name": r["stakeholder_group_name_in_xbrl"],
                     "value": r["value"],
                     "period_end_or_instant": r["period_end"]
                     or r["instant_date"],
                     "context_reference": r["context_reference"],
                     "source_file": r["source_file"],
                     "xml_line": r["xml_line"],
                     "resolution_class": r["resolution_class"]})
    df = pd.DataFrame(rows)
    years = sorted(df["report_fy_slot"].unique())
    i = years.index(slot) if slot in years else 0
    keep = years[max(0, i - 1): i + 2]
    return df[df["report_fy_slot"].isin(keep)].sort_values(
        ["report_fy_slot", "period_role", "requirement_id", "row"])


def investigate(std_df, classified):
    """Evidence-based conclusions; recommendations only, nothing applied."""
    def val(t, slot, role, metric, row):
        x = t[(t["report_fy_slot"] == slot) & (t["period_role"] == role)
              & (t["requirement_id"] == metric) & (t["row"] == row)]
        return num(x["value"].iloc[0]) if len(x) else None

    out = []
    # ---- ADANIENSOL FY2024-25 -----------------------------------------
    t = stakeholder_table(std_df, classified, "ADANIENSOL", "2024-25")
    emp = "STANDARD:EmployeesAndWorkers"
    other = t[(t["report_fy_slot"] == "2024-25")
              & t["name"].map(exact_key).eq("employeesandworkers")]
    o = {(r["period_role"], r["requirement_id"]): num(r["value"])
         for r in other.to_dict("records")}
    s_filed, s_pend = val(t, "2024-25", "CY", "G16", emp), \
        val(t, "2024-25", "CY", "G17", emp)
    py_pend = val(t, "2024-25", "PY", "G17", emp)
    prior_pend = val(t, "2023-24", "CY", "G17", emp)
    o_filed, o_pend = o.get(("CY", "G16")), o.get(("CY", "G17"))
    checks = [
        ("standard CY filed / pending", f"{fmt(s_filed)} / {fmt(s_pend)}"),
        ("'Employees and workers' other row CY filed / pending",
         f"{fmt(o_filed)} / {fmt(o_pend)}"),
        ("pending at start of year (same report PY)", fmt(py_pend)),
        ("pending at end of FY2023-24 (prior report CY)", fmt(prior_pend)),
        ("other row is the standard pair swapped",
         o_filed == s_pend and o_pend == s_filed),
        ("other-row pending <= opening pending + filed",
         None if None in (o_pend, py_pend, o_filed)
         else o_pend <= py_pend + o_filed),
        ("standard pending <= opening pending + filed",
         None if None in (s_pend, py_pend, s_filed)
         else s_pend <= py_pend + s_filed),
        ("next report (FY2025-26) available for independent check",
         "2025-26" in set(t["report_fy_slot"])),
    ]
    supported = checks[4][1] is True and checks[5][1] is False \
        and checks[6][1] is True and py_pend == prior_pend
    out.append({
        "company": "ADANIENSOL", "fy_slot": "2024-25",
        "checks": checks, "evidence_supports_conclusion": supported,
        "conclusion": "EVIDENCE DOES NOT SUPPORT THE CONCLUSION; manual "
        "review" if not supported else (
            "The 'Employees and workers' row under OtherStakeHolderAxis "
            "repeats the standard group with G16 and G17 swapped (filed "
            f"{fmt(o_filed)}, pending {fmt(o_pend)} vs standard filed "
            f"{fmt(s_filed)}, pending {fmt(s_pend)}). The other-row pairing "
            "is arithmetically impossible: pending at year end "
            f"({fmt(o_pend)}) exceeds opening pending ({fmt(py_pend)}, "
            "confirmed by the prior report) plus complaints filed "
            f"({fmt(o_filed)}). The standard pairing is consistent. "
            "No independent next-year report is available (FY2025-26 "
            "filing not on NSE)."),
        "recommendation": (
            "No correction to the standard values. Treat the two swapped "
            "'other' rows as a filer error inside a duplicate re-listing; "
            "exclude them from totals. Owner approval needed to record "
            "this as resolved."),
        "table": t})

    # ---- POWERGRID FY2023-24 ------------------------------------------
    t = stakeholder_table(std_df, classified, "POWERGRID", "2023-24")
    names = {}
    for r in t[t["row"].str.startswith("OTHER")].to_dict("records"):
        names[(r["report_fy_slot"], r["period_role"], r["requirement_id"],
               core_key(r["name"]))] = num(r["value"])
    cmp_rows = []
    for metric in METRICS:
        for label, std_row, key in (
                ("Customers", "STANDARD:Customers", "customers"),
                ("Value chain partners", "STANDARD:ValueChainPartners",
                 "valuechainpartners"),
                ("Vigilance (other)", None, "vigilance")):
            cmp_rows.append({
                "metric": metric, "group": label,
                "FY23-24 report standard CY": fmt(val(
                    t, "2023-24", "CY", metric, std_row)) if std_row else "",
                "FY23-24 report other-row CY": fmt(names.get(
                    ("2023-24", "CY", metric, key))),
                "FY24-25 report standard PY": fmt(val(
                    t, "2024-25", "PY", metric, std_row)) if std_row else "",
                "FY24-25 report other-row PY": fmt(names.get(
                    ("2024-25", "PY", metric, key))),
                "FY22-23 report standard CY": fmt(val(
                    t, "2022-23", "CY", metric, std_row)) if std_row else "",
                "FY22-23 report other-row CY": fmt(names.get(
                    ("2022-23", "CY", metric, key))),
                "FY23-24 report standard PY": fmt(val(
                    t, "2023-24", "PY", metric, std_row)) if std_row else ""})

    # the one-row-shift conclusion is emitted only if every link holds
    cmp = {(r["metric"], r["group"]): r for r in cmp_rows}
    links = []
    for metric in METRICS:
        cu, vc, vg = (cmp[(metric, "Customers")],
                      cmp[(metric, "Value chain partners")],
                      cmp[(metric, "Vigilance (other)")])
        links += [
            (f"{metric}: standard Customers CY = other-row VCP CY",
             cu["FY23-24 report standard CY"]
             == vc["FY23-24 report other-row CY"]),
            (f"{metric}: standard VCP CY = other-row Vigilance CY",
             vc["FY23-24 report standard CY"]
             == vg["FY23-24 report other-row CY"]),
            (f"{metric}: next report standard VCP PY = other-row VCP CY",
             vc["FY24-25 report standard PY"]
             == vc["FY23-24 report other-row CY"]),
            (f"{metric}: next report standard Customers PY = other-row "
             "Customers CY",
             cu["FY24-25 report standard PY"]
             == cu["FY23-24 report other-row CY"]),
            (f"{metric}: next report Vigilance PY = other-row Vigilance CY",
             vg["FY24-25 report other-row PY"]
             == vg["FY23-24 report other-row CY"]),
        ]
    g16 = {k: cmp[("G16", k)] for k in ("Customers", "Value chain partners",
                                         "Vigilance (other)")}
    links += [
        ("G16 PY comparatives shifted too: standard Customers PY = prior "
         "report standard VCP CY, and standard VCP PY = prior report "
         "Vigilance CY",
         g16["Customers"]["FY23-24 report standard PY"]
         == g16["Value chain partners"]["FY22-23 report standard CY"]
         and g16["Value chain partners"]["FY23-24 report standard PY"]
         == g16["Vigilance (other)"]["FY22-23 report other-row CY"])]
    supported = all(ok for _, ok in links)
    out.append({
        "company": "POWERGRID", "fy_slot": "2023-24",
        "checks": links, "evidence_supports_conclusion": supported,
        "comparison": pd.DataFrame(cmp_rows),
        "conclusion": "EVIDENCE DOES NOT SUPPORT THE CONCLUSION; manual "
        "review" if not supported else (
            "In the FY2023-24 report the standard Customers and Value Chain "
            "Partners members hold the values that the same report's own "
            "named rows give to Value Chain Partners and 'Other "
            "(Vigilance)': a one-row shift. The FY2024-25 report restates "
            "FY2023-24 (PY) as Customers 0, Value Chain Partners 7 (pending "
            "5) and Vigilance 73 (pending 12), matching the named rows, and "
            "the FY2022-23 report shows the same structure (Vigilance as a "
            "separate 'other' group). The 7 is a legitimate value chain "
            "partner figure and 73 belongs to Vigilance; the standard "
            "members in the FY2023-24 report (CY and PY) are shifted."),
        "recommendation": "" if not supported else (
            "Proposed correction, NOT applied: FY2023-24 standard Customers "
            f"G16 {cmp[('G16', 'Customers')]['FY23-24 report standard CY']}"
            f"->{cmp[('G16', 'Customers')]['FY23-24 report other-row CY']}, "
            f"G17 {cmp[('G17', 'Customers')]['FY23-24 report standard CY']}"
            f"->{cmp[('G17', 'Customers')]['FY23-24 report other-row CY']}; "
            "Value Chain Partners G16 "
            f"{cmp[('G16', 'Value chain partners')]['FY23-24 report standard CY']}"
            f"->{cmp[('G16', 'Value chain partners')]['FY23-24 report other-row CY']}, "
            "G17 "
            f"{cmp[('G17', 'Value chain partners')]['FY23-24 report standard CY']}"
            f"->{cmp[('G17', 'Value chain partners')]['FY23-24 report other-row CY']}"
            "; G16 PY comparatives in the same report: Customers "
            f"{g16['Customers']['FY23-24 report standard PY']}->"
            f"{g16['Customers']['FY22-23 report standard CY']}, Value Chain "
            f"Partners {g16['Value chain partners']['FY23-24 report standard PY']}"
            f"->{g16['Value chain partners']['FY22-23 report standard CY']}. "
            "'Other (Vigilance)' is a genuine other group. Requires owner "
            "approval; original values stay preserved."),
        "table": t})
    return out


# ==========================================================
# build
# ==========================================================

def build():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    std_index, std_df = load_standard()
    raw = load_raw()
    classified = classify(raw, std_index)
    yoy_df = yoy(classified, std_df)
    notes = reevaluate_notes(yoy_df, classified)
    recon = reconcile(classified, std_df)
    inv = investigate(std_df, classified)

    raw_cols = [c for c in read(RAW_FACTS).columns]
    analytic = ["resolution_class", "matched_standard_groups",
                "standard_group_value_same_report", "standard_fact_context",
                "name_clean", "name_exact_key", "name_core_key",
                "name_quality", "same_value_in_other_standard_group",
                "other_metric_standard_value", "suspected_pattern",
                "value_coincides_with_standard_group", "evidence_note"]
    keep = raw_cols + [c for c in analytic if c not in raw_cols]

    def part(cls):
        return classified[classified["resolution_class"] == cls][keep]

    filer = classified[classified["resolution_class"]
                       == "MANUAL_REVIEW_REQUIRED"][keep]
    potential = pd.concat([
        filer.assign(error_basis="DIFFERENT_VALUE_SAME_NAME"),
        classified[classified.apply(
            lambda r: (r["nse_symbol"], r["fy_slot"]) in INVESTIGATED,
            axis=1)][keep].assign(error_basis="IN_INVESTIGATED_FILING"),
    ]).drop_duplicates("fact_id" if "fact_id" in keep else keep[:20])

    counts = (classified.groupby(["nse_symbol", "resolution_class"]).size()
              .unstack(fill_value=0).reset_index())
    outputs = {
        "01_raw_source_facts.csv": read(RAW_FACTS),
        "01b_classified_source_facts.csv": classified[keep],
        "02_genuine_other_stakeholder_facts.csv": part("GENUINE_OTHER_GROUP"),
        "03_duplicates_of_standard_groups.csv":
            part("DUPLICATE_OF_STANDARD_GROUP"),
        "04_same_name_different_value.csv": part("MANUAL_REVIEW_REQUIRED"),
        "05_ambiguous_stakeholder_mappings.csv":
            part("AMBIGUOUS_STAKEHOLDER_MAPPING"),
        "06_potential_filer_errors.csv": potential,
        "06a_adaniensol_fy2024_25_evidence.csv": inv[0]["table"],
        "06b_powergrid_fy2023_24_evidence.csv": inv[1]["table"],
        "06c_powergrid_fy2023_24_comparison.csv": inv[1]["comparison"],
        "06d_investigation_checks.csv": pd.DataFrame([
            {"company": i["company"], "fy_slot": i["fy_slot"],
             "check": k, "result": v,
             "evidence_supports_conclusion":
                 i["evidence_supports_conclusion"],
             "conclusion": i["conclusion"],
             "recommendation": i["recommendation"]}
            for i in inv for k, v in i["checks"]]),
        "07_name_based_yoy_comparison.csv": yoy_df,
        "08_restatement_note_reevaluation.csv": notes,
        "09_company_year_reconciliation.csv": recon,
        "10_company_classification_counts.csv": counts,
    }
    for name, df in outputs.items():
        df.to_csv(OUTPUT_DIR / name, index=False, encoding="utf-8-sig")
    log.info("stakeholder review built", extra={"fields": {
        "facts": len(classified), "yoy_rows": len(yoy_df),
        "notes": len(notes)}})
    return {"classified": classified, "yoy": yoy_df, "notes": notes,
            "recon": recon, "investigations": inv, "counts": counts}


def main():
    out = build()
    c = out["classified"]
    print("facts:", len(c))
    print(pd.crosstab(c["resolution_class"],
                      [c["requirement_id"], c["file_role"]]).to_string())
    print("\nname quality:", c["name_quality"].value_counts().to_dict())
    print("\nmanual-review patterns:",
          c[c["resolution_class"] == "MANUAL_REVIEW_REQUIRED"][
              "suspected_pattern"].value_counts().to_dict())
    y = out["yoy"]
    print("\nYoY:", y.groupby(["match_method", "comparison"]).size()
          .to_dict())
    print("\nC08 note re-evaluation:",
          out["notes"]["re_evaluation"].value_counts().to_dict())
    r = out["recon"]
    print("\nreconciliation:", r.groupby(["requirement_id", "total_status"])
          .size().to_dict())
    for i in out["investigations"]:
        print(f"\n{i['company']} {i['fy_slot']}: {i['conclusion']}")
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
