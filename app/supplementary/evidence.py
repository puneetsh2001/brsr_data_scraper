"""
Supplementary-source evidence register (Phase 13).

Links supplementary documents (annual reports, CSR reports, BRSR PDF
reports, media releases, presentations) to the BRSR requirements. XBRL
stays the primary source; nothing found here changes a production value.

Documents
  - local PDFs already in the project (read only; hashed, never modified)
  - owner-supplied PDFs in data/raw/supplementary/manual/<ISIN>/ (manual
    import path; empty until the owner adds files)
  - NSE BRSR PDF URLs from the filing registry: recorded, NOT downloaded
    (owner decision: no PDF downloads)

Evidence (all CANDIDATE_UNVERIFIED - for review, never applied)
  - external requirements not in BRSR XBRL (S27 CSR spend, G04 independent
    directors, G05 board meetings, G06 remuneration ratio, G20 whistle-
    blower complaints, S28 turnover by age): page, snippet, matched rule,
    value text and unit text exactly as printed (no conversion; a missing
    unit stays UNKNOWN)
  - XBRL <-> BRSR PDF cross-check: whether the value filed in XBRL appears
    verbatim in the same year's BRSR PDF (page references)
  - assurance statement evidence: Phase 12 providers and assurance /
    assessment wording found in the documents (kept as separate phrase
    types; nothing is reclassified)

Text is extracted with PyMuPDF from the text layer only; pages without a
text layer are reported (no OCR, no guessing).

Outputs: data/processed/nifty100/supplementary/

Run:
    python -m app.supplementary.evidence
"""

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger

log = get_logger(__name__, "supplementary_evidence")

RAW = settings.raw_dir
N = settings.processed_dir / "nifty100"
LOCAL_SOURCES = {           # folder -> ISIN (folders named for one company)
    RAW / "Reliance": "INE002A01018",
    RAW / "Reliance Industries Limited": "INE002A01018",
}
MANUAL_DIR = RAW / "supplementary" / "manual"
MANIFEST = settings.processed_dir / "registry" / "filing_manifest.csv"
MASTER = settings.processed_dir / "registry" / "company_master.csv"
REQ_MAP = settings.reference_dir / "brsr_requirement_concept_map.csv"
PRODUCTION_CY = N / "normalised" / "brsr_requirement_values_normalised.csv"
PROVIDERS = N / "assurance" / "assurance_providers.csv"
ENGAGEMENTS = N / "assurance" / "assurance_engagements.csv"
OUTPUT_DIR = N / "supplementary"
TEXT_DIR = OUTPUT_DIR / "page_text"
FY_RANGE = ("2021-22", "2025-26")

DOC_TYPES = [            # first match wins (file name)
    ("BRSR_PDF", r"brsr"),
    ("CSR_REPORT", r"^csr|[^a-z]csr[^a-z]"),
    ("ANNUAL_REPORT", r"annual[-_ ]?report|(^|[-_ ])ar[-_ ]|ar_\d"),
    ("MEDIA_RELEASE", r"media[-_ ]?release|mediarelease"),
    ("ANALYST_PRESENTATION", r"analyst|presentation|perspective"),
]
EXTERNAL = {
    "S27": ("CSR_REPORT", "ANNUAL_REPORT"),
    "G04": ("ANNUAL_REPORT",),
    "G05": ("ANNUAL_REPORT",),
    "G06": ("ANNUAL_REPORT",),
    "G20": ("ANNUAL_REPORT", "BRSR_PDF"),
    "S28": ("ANNUAL_REPORT", "BRSR_PDF"),
}
AMOUNT = (r"(?P<cur>₹|`|\bRs\.?|\bINR\b)\s*(?P<val>\d[\d,]*(?:\.\d+)?)\s*"
          r"(?P<unit>crore|cr\b|lakh|million|billion)?")
RULES = {
    "S27": [("CSR_AMOUNT_SPENT",
             r"(total\s+)?(amount|expenditure)\s+(spent|incurred)[^.]{0,80}?"
             r"(csr|corporate social responsibility|financial year)",
             AMOUNT)],
    "G04": [("INDEPENDENT_DIRECTOR_COUNT",
             r"(?P<val>\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
             r"eleven|twelve)\s+(are\s+|were\s+)?independent\s+directors?",
             None)],
    "G05": [("BOARD_MEETINGS_HELD",
             r"(?P<val>\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
             r"eleven|twelve)\s+(\(\w+\)\s+)?(board\s+)?meetings\s+of\s+the\s+"
             r"board|(?P<val2>\d+|five|six|seven|eight|nine|ten)\s+board\s+"
             r"meetings\s+were\s+held|board\s+met\s+(?P<val3>\d+|five|six|"
             r"seven|eight|nine|ten)\s+times", None)],
    "G06": [("REMUNERATION_TO_MEDIAN_RATIO",
             r"ratio\s+of\s+(the\s+)?remuneration\s+of\s+each\s+director\s+"
             r"to\s+the\s+median", r"(?P<val>\d+(?:\.\d+)?)\s*:\s*1")],
    "G20": [("WHISTLEBLOWER_COMPLAINTS",
             r"whistle[\s-]?blower[^.]{0,120}?complaints?", None)],
    "S28": [("TURNOVER_BY_AGE",
             r"(turnover|attrition)[^.]{0,120}?(age|<\s*30|under\s+30|"
             r"30\s*-\s*50|>\s*50)", None)],
}
ASSURANCE_PHRASES = {
    "REASONABLE_ASSURANCE": r"reasonable\s+assurance",
    "LIMITED_ASSURANCE": r"limited\s+assurance",
    "ASSESSMENT": r"independent\s+assessment|assessment\s+of\s+(the\s+)?brsr",
}
MIN_DIGITS_FOR_CROSSCHECK = 4


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ==========================================================
# documents
# ==========================================================

def doc_type(name):
    low = name.lower()
    for t, pattern in DOC_TYPES:
        if re.search(pattern, low):
            return t
    return "OTHER"


def fy_from_name(name):
    """FY from explicit patterns only: 2021-22, 202122, FY2024-25."""
    m = re.search(r"(20\d\d)\s*[-_]\s*(20)?(\d\d)(?!\d)", name)
    if m and int(m.group(3)) == (int(m.group(1)) + 1) % 100:
        return f"{m.group(1)}-{m.group(3)}", "FILE_NAME"
    m = re.search(r"(?<!\d)(20\d\d)(\d\d)(?!\d)", name)
    if m and int(m.group(2)) == (int(m.group(1)) + 1) % 100:
        return f"{m.group(1)}-{m.group(2)}", "FILE_NAME"
    return "", ""


def fy_from_text(pages):
    """Most frequent 'YYYY-YY' on the first three pages, if unambiguous."""
    text = " ".join(pages[:3])
    found = Counter(f"{a}-{b}" for a, b in re.findall(
        r"(20\d\d)\s*[-–]\s*(\d\d)(?!\d)", text)
        if int(b) == (int(a) + 1) % 100)
    if not found:
        return "", ""
    top = found.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        return "", "TEXT_AMBIGUOUS"
    return top[0][0], "DOCUMENT_TEXT"


def extract_pages(path):
    import fitz
    try:
        doc = fitz.open(path)
    except Exception as error:
        return None, f"UNREADABLE: {error}"
    try:
        if doc.page_count == 0:
            return None, "UNREADABLE: no pages"
        return [page.get_text("text") for page in doc], ""
    finally:
        doc.close()


def local_documents():
    rows = []
    sources = dict(LOCAL_SOURCES)
    if MANUAL_DIR.exists():
        for d in MANUAL_DIR.iterdir():
            if d.is_dir():
                sources[d] = d.name
    for folder, isin in sources.items():
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.pdf")):
            rows.append({"path": p, "isin": isin,
                         "source_kind": "MANUAL_IMPORT"
                         if MANUAL_DIR in p.parents else "LOCAL_PROJECT"})
    return rows


def build_inventory(master):
    name_of = dict(zip(master["isin"], master["legal_name"]))
    sym_of = dict(zip(master["isin"], master["nse_symbol"]))
    docs, texts = [], {}
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    for d in local_documents():
        p = d["path"]
        digest = sha256(p)
        pages, error = extract_pages(p)
        fy, basis = fy_from_name(p.name)
        if not fy and pages:
            fy, basis = fy_from_text(pages)
        empty = sum(1 for t in (pages or []) if len(t.strip()) < 20)
        status = ("UNREADABLE" if error else
                  "NO_TEXT_LAYER" if pages and empty == len(pages) else
                  "TEXT_EXTRACTED_PARTIAL" if empty else "TEXT_EXTRACTED")
        if pages:
            texts[digest] = pages
            out = TEXT_DIR / f"{digest[:16]}.jsonl"
            if not out.exists():
                with open(out, "w", encoding="utf-8") as fh:
                    for i, t in enumerate(pages, 1):
                        fh.write(json.dumps({"page": i, "text": t},
                                            ensure_ascii=False) + "\n")
        docs.append({
            "document_id": f"DOC-{digest[:12]}", "sha256": digest,
            "master_isin": d["isin"], "company_name": name_of.get(d["isin"]),
            "nse_symbol": sym_of.get(d["isin"]),
            "source_kind": d["source_kind"],
            "file_name": p.name,
            "local_path": p.relative_to(settings.project_root).as_posix(),
            "bytes": p.stat().st_size, "doc_type": doc_type(p.name),
            "fy_slot": fy, "fy_basis": basis or "NOT_DETERMINED",
            "in_fy_scope": FY_RANGE[0] <= fy <= FY_RANGE[1] if fy else False,
            "pages": len(pages) if pages else 0,
            "pages_without_text": empty, "text_status": status,
            "error": error, "source_url": "", "download_status":
                "LOCAL_FILE"})
    inv = pd.DataFrame(docs)
    # one annual / CSR / BRSR report per company-year is expected;
    # several quarterly releases or presentations per year are normal
    inv["duplicate_of_type_fy"] = inv.duplicated(
        ["master_isin", "doc_type", "fy_slot"], keep=False) & \
        (inv["fy_slot"] != "") & inv["doc_type"].isin(
            ["ANNUAL_REPORT", "CSR_REPORT", "BRSR_PDF"])
    return inv, texts


def remote_documents(master):
    m = read(MANIFEST)
    m = m[(m["in_processing_scope"] == "True") & (m["pdf_url"] != "")]
    m = m.merge(master[["company_id", "isin", "legal_name", "nse_symbol"]],
                on="company_id", how="left")
    return pd.DataFrame({
        "document_id": "URL-" + m["filing_id"], "sha256": "",
        "master_isin": m["isin"], "company_name": m["legal_name"],
        "nse_symbol": m["nse_symbol"], "source_kind": "NSE_BRSR_PDF_URL",
        "file_name": m["pdf_url"].str.split("/").str[-1], "local_path": "",
        "bytes": "", "doc_type": "BRSR_PDF", "fy_slot": m["fy_slot"],
        "fy_basis": "FILING_REGISTRY", "in_fy_scope": True, "pages": "",
        "pages_without_text": "", "text_status": "NOT_DOWNLOADED",
        "error": "", "source_url": m["pdf_url"],
        "download_status": "NOT_DOWNLOADED_OWNER_DECISION",
        "duplicate_of_type_fy": False})


# ==========================================================
# evidence
# ==========================================================

def snippet(text, start, end, width=220):
    s = text[max(0, start - width): min(len(text), end + width)]
    return " ".join(s.split())


def find_candidates(text, rule):
    """[(matched text, value text, unit text, currency, start, end)]"""
    name, pattern, value_pattern = rule
    out = []
    for m in re.finditer(pattern, text, re.I | re.S):
        val = unit = cur = ""
        groups = m.groupdict()
        for g in ("val", "val2", "val3"):
            if groups.get(g):
                val = groups[g]
                break
        if value_pattern:
            window = text[m.end(): m.end() + 400]
            v = re.search(value_pattern, window, re.I)
            if v:
                gd = v.groupdict()
                val, unit, cur = gd.get("val") or "", gd.get("unit") or "", \
                    gd.get("cur") or ""
        out.append((m.group(0), val, unit, cur, m.start(), m.end()))
    return out


def external_candidates(inv, texts):
    rows = []
    docs = inv[inv["text_status"].str.startswith("TEXT_EXTRACTED")]
    for req, types in EXTERNAL.items():
        for d in docs[docs["doc_type"].isin(types)].to_dict("records"):
            for page_no, text in enumerate(texts[d["sha256"]], 1):
                for rule in RULES[req]:
                    for matched, val, unit, cur, s, e in find_candidates(
                            text, rule):
                        rows.append({
                            "requirement_id": req, "rule": rule[0],
                            "master_isin": d["master_isin"],
                            "nse_symbol": d["nse_symbol"],
                            "fy_slot": d["fy_slot"],
                            "document_id": d["document_id"],
                            "doc_type": d["doc_type"],
                            "file_name": d["file_name"],
                            "sha256": d["sha256"], "page": page_no,
                            "matched_text": " ".join(matched.split())[:200],
                            "candidate_value_text": val,
                            "candidate_currency_text": cur,
                            "candidate_unit_text": unit or (
                                "UNKNOWN" if val and req == "S27" else ""),
                            "snippet": snippet(text, s, e),
                            # a board of more than 30 directors or more
                            # than 30 meetings is not plausible
                            "status": "CANDIDATE_IMPLAUSIBLE"
                            if req in ("G04", "G05") and val.isdigit()
                            and int(val) > 30 else "CANDIDATE_UNVERIFIED"})
    return pd.DataFrame(rows, columns=[
        "requirement_id", "rule", "master_isin", "nse_symbol", "fy_slot",
        "document_id", "doc_type", "file_name", "sha256", "page",
        "matched_text", "candidate_value_text", "candidate_currency_text",
        "candidate_unit_text", "snippet", "status"])


def coverage(filings, inv, cands, req_map):
    ext = req_map[req_map["source"] != "BRSR_XBRL"].drop_duplicates(
        "requirement_id")
    local = inv[inv["text_status"].str.startswith("TEXT_EXTRACTED")]
    rows = []
    for f in filings.to_dict("records"):
        for r in ext.to_dict("records"):
            rid = r["requirement_id"]
            have = local[(local["master_isin"] == f["isin"])
                         & (local["fy_slot"] == f["fy_slot"])
                         & local["doc_type"].isin(EXTERNAL.get(rid, ()))]
            c = cands[(cands["master_isin"] == f["isin"])
                      & (cands["fy_slot"] == f["fy_slot"])
                      & (cands["requirement_id"] == rid)]
            if r["source"] == "NOT_AVAILABLE_IN_BRSR" and not len(c):
                status = "NOT_AVAILABLE_IN_BRSR_NO_SUPPLEMENTARY_EVIDENCE"
            elif len(c):
                status = "CANDIDATE_EVIDENCE_FOUND"
            elif len(have):
                status = "DOCUMENT_AVAILABLE_NO_CANDIDATE"
            else:
                status = "NO_LOCAL_DOCUMENT"
            rows.append({
                "master_isin": f["isin"], "nse_symbol": f["nse_symbol"],
                "fy_slot": f["fy_slot"], "requirement_id": rid,
                "requirement": r["requirement"], "source": r["source"],
                "status": status, "candidates": len(c),
                "candidate_values": " | ".join(sorted(set(
                    c["candidate_value_text"]) - {""}))[:200],
                "documents_checked": " | ".join(have["file_name"]),
                "brsr_pdf_url": f["pdf_url"]})
    return pd.DataFrame(rows)


def number_variants(value):
    """Verbatim, western and Indian digit grouping of the filed value."""
    v = value.strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", v):
        return []
    sign = "-" if v.startswith("-") else ""
    whole, _, frac = v.lstrip("-").partition(".")
    west = f"{int(whole):,}"
    s = whole
    indian = s[-3:]
    s = s[:-3]
    while s:
        indian = s[-2:] + "," + indian
        s = s[:-2]
    tail = f".{frac}" if frac else ""
    return sorted({v, sign + west + tail, sign + indian + tail})


def xbrl_pdf_crosscheck(inv, texts):
    prod = read(PRODUCTION_CY)
    brsr = inv[(inv["doc_type"] == "BRSR_PDF")
               & inv["text_status"].str.startswith("TEXT_EXTRACTED")]
    rows = []
    for d in brsr.to_dict("records"):
        vals = prod[(prod["master_isin"] == d["master_isin"])
                    & (prod["fy_slot"] == d["fy_slot"])]
        pages = texts[d["sha256"]]
        for r in vals.to_dict("records"):
            variants = number_variants(r["original_value"])
            digits = len(re.sub(r"\D", "", r["original_value"]))
            if not variants:
                result, found = "NOT_NUMERIC", []
            elif digits < MIN_DIGITS_FOR_CROSSCHECK:
                result, found = "NOT_INFORMATIVE_SHORT_NUMBER", []
            else:
                found = [i for i, t in enumerate(pages, 1)
                         if any(re.search(rf"(?<![\d.,]){re.escape(x)}"
                                          rf"(?![\d])", t) for x in variants)]
                result = "FOUND_VERBATIM" if found else "NOT_FOUND_VERBATIM"
            rows.append({
                "master_isin": r["master_isin"],
                "nse_symbol": r["master_nse_symbol"], "fy_slot": r["fy_slot"],
                "requirement_id": r["requirement_id"],
                "breakdown": r["breakdown"],
                "original_value": r["original_value"],
                "original_unit": r["original_unit"],
                "xbrl_source_file": r["source_file"],
                "xbrl_context": r["context_reference"],
                "document_id": d["document_id"], "file_name": d["file_name"],
                "result": result,
                "pages": " | ".join(map(str, found[:10]))})
    return pd.DataFrame(rows)


def assurance_evidence(inv, texts):
    rows = []
    if not PROVIDERS.exists():
        return pd.DataFrame()
    prov = read(PROVIDERS)
    eng = read(ENGAGEMENTS)
    docs = inv[inv["text_status"].str.startswith("TEXT_EXTRACTED")
               & inv["doc_type"].isin(["BRSR_PDF", "ANNUAL_REPORT"])]
    for d in docs.to_dict("records"):
        pages = texts[d["sha256"]]
        e = eng[(eng["master_isin"] == d["master_isin"])
                & (eng["fy_slot"] == d["fy_slot"])]
        for p in prov[(prov["master_isin"] == d["master_isin"])
                      & (prov["fy_slot"] == d["fy_slot"])].to_dict("records"):
            if not p["provider_name"]:
                continue
            core = re.sub(r"\b(private|pvt|limited|ltd|llp)\b\.?", "",
                          p["provider_name"], flags=re.I).strip(" ,.")
            hits = [i for i, t in enumerate(pages, 1)
                    if re.search(re.escape(core), t, re.I)]
            rows.append({"master_isin": d["master_isin"],
                         "fy_slot": d["fy_slot"], "evidence": "PROVIDER_NAME",
                         "phrase_type": "", "text_searched": core,
                         "document_id": d["document_id"],
                         "file_name": d["file_name"],
                         "found": bool(hits),
                         "pages": " | ".join(map(str, hits[:10])),
                         "phase12_engagement_type":
                             e["engagement_type"].iloc[0] if len(e) else "",
                         "snippet": ""})
        for phrase, pattern in ASSURANCE_PHRASES.items():
            hits = [(i, m) for i, t in enumerate(pages, 1)
                    for m in [re.search(pattern, t, re.I)] if m]
            rows.append({"master_isin": d["master_isin"],
                         "fy_slot": d["fy_slot"], "evidence": "PHRASE",
                         "phrase_type": phrase, "text_searched": pattern,
                         "document_id": d["document_id"],
                         "file_name": d["file_name"], "found": bool(hits),
                         "pages": " | ".join(str(i) for i, _ in hits[:10]),
                         "phase12_engagement_type":
                             e["engagement_type"].iloc[0] if len(e) else "",
                         "snippet": snippet(pages[hits[0][0] - 1],
                                            hits[0][1].start(),
                                            hits[0][1].end(), 150)
                         if hits else ""})
    return pd.DataFrame(rows)


# ==========================================================
# build
# ==========================================================

def build(write=True):
    master = read(MASTER)
    m = read(MANIFEST)
    filings = m[(m["in_processing_scope"] == "True")
                & (m["local_file_path"] != "")].merge(
        master[["company_id", "isin", "nse_symbol"]], on="company_id",
        how="left")
    inv, texts = build_inventory(master)
    remote = remote_documents(master)
    cands = external_candidates(inv, texts)
    cov = coverage(filings, inv, cands, read(REQ_MAP))
    cross = xbrl_pdf_crosscheck(inv, texts)
    assur = assurance_evidence(inv, texts)
    inventory = pd.concat([inv.assign(local_path=inv["local_path"]),
                           remote], ignore_index=True)
    summary = pd.DataFrame([
        {"measure": "local documents", "value": len(inv)},
        {"measure": "local documents with text",
         "value": int(inv["text_status"].str.startswith(
             "TEXT_EXTRACTED").sum())},
        {"measure": "local documents in FY scope",
         "value": int(inv["in_fy_scope"].sum())},
        {"measure": "manual-import documents",
         "value": int((inv["source_kind"] == "MANUAL_IMPORT").sum())},
        {"measure": "NSE BRSR PDF URLs recorded (not downloaded)",
         "value": len(remote)},
        {"measure": "companies with local documents",
         "value": inv["master_isin"].nunique()},
        {"measure": "external-requirement candidates", "value": len(cands)},
        *[{"measure": f"coverage {k}", "value": n}
          for k, n in cov["status"].value_counts().items()],
        *[{"measure": f"xbrl-pdf {k}", "value": n}
          for k, n in cross["result"].value_counts().items()]])
    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        for name, df in (("document_inventory.csv", inventory),
                         ("external_requirement_candidates.csv", cands),
                         ("external_requirement_coverage.csv", cov),
                         ("xbrl_pdf_crosscheck.csv", cross),
                         ("assurance_statement_evidence.csv", assur),
                         ("supplementary_summary.csv", summary)):
            df.to_csv(OUTPUT_DIR / name, index=False, encoding="utf-8-sig")
    log.info("supplementary evidence built", extra={"fields": {
        "documents": len(inv), "candidates": len(cands)}})
    return {"inventory": inventory, "local": inv, "texts": texts,
            "candidates": cands, "coverage": cov, "crosscheck": cross,
            "assurance": assur, "summary": summary}


def main():
    out = build()
    pd.set_option("display.width", 200)
    print(out["summary"].to_string(index=False))
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
