"""Phase 13: supplementary-source evidence (annual / CSR / BRSR PDF)."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from app.supplementary import evidence as ev
from tests.conftest import APPROVED_SWITCH_CHANGES, PROJECT_ROOT, read_csv

BACKUP = PROJECT_ROOT / "data/processed/nifty100/backups/20261112_pre_phase13"
REGENERATED = "data/processed/nifty100/review_workflow/"


@pytest.fixture(scope="module")
def built():
    return ev.build(write=False)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---- document classification ---------------------------------------------

@pytest.mark.parametrize("name,doc_type,fy", [
    ("CSR202223.pdf", "CSR_REPORT", "2022-23"),
    ("CSR2025-26.pdf", "CSR_REPORT", "2025-26"),
    ("AR_2021-22.pdf", "ANNUAL_REPORT", "2021-22"),
    ("RIL-Integrated-Annual-Report-2024-25.pdf", "ANNUAL_REPORT", "2024-25"),
    ("BRSR202526.pdf", "BRSR_PDF", "2025-26"),
    ("Media_Release_RIL_Q2_FY2025-26_Financial.pdf", "MEDIA_RELEASE",
     "2025-26"),
    ("RIL-AR-2021.pdf", "ANNUAL_REPORT", ""),      # year only: not guessed
    ("Transition to Ind AS.pdf", "OTHER", ""),
])
def test_doc_type_and_fy_from_name(name, doc_type, fy):
    assert ev.doc_type(name) == doc_type
    assert ev.fy_from_name(name)[0] == fy


def test_fy_from_text_never_guesses_ties():
    assert ev.fy_from_text(["Annual Report 2020-21 ... 2020-21"]) == \
        ("2020-21", "DOCUMENT_TEXT")
    assert ev.fy_from_text(["2019-20 and 2020-21"]) == ("", "TEXT_AMBIGUOUS")
    assert ev.fy_from_text(["no year here"]) == ("", "")


# ---- candidate extraction: values and units exactly as printed ------------------

def cands(req, text):
    return ev.find_candidates(text, ev.RULES[req][0])


def test_csr_amount_kept_as_printed():
    (m, val, unit, cur, *_), = cands(
        "S27", "Total amount spent for the Financial Year ₹ 1,186 crore")
    assert (val, unit, cur) == ("1,186", "crore", "₹")


def test_csr_amount_without_unit_stays_unknown_later():
    (m, val, unit, cur, *_), = cands(
        "S27", "amount spent towards CSR during the year Rs. 31")
    assert val == "31" and unit == ""


def test_rs_inside_words_is_not_a_currency():
    out = cands("S27", "amount spent for the financial year transfers 142")
    assert all(c[3] == "" for c in out)


def test_counts_and_ratio_patterns():
    (_, val, *_), = cands("G04", "of the 14 directors, seven are independent "
                                 "directors")
    assert val == "seven"
    assert cands("G05", "Five Meetings of the Board were held")[0][1] == \
        "Five"
    (_, val, *_), = cands("G06", "ratio of the remuneration of each director "
                                 "to the median remuneration: 512.3 : 1")
    assert val == "512.3"


def test_number_variants():
    assert set(ev.number_variants("3394505")) == {"3394505", "3,394,505",
                                                  "33,94,505"}
    assert ev.number_variants("abc") == []
    assert "1,234.50" in ev.number_variants("1234.50")


# ---- outputs ------------------------------------------------------------------------

def test_inventory_complete_and_hashed(built):
    local = built["local"]
    raw = [l for l in (BACKUP / "raw_pdfs_before.sha").read_text()
           .splitlines()]
    assert len(local) == len(raw) == 48
    for r in local.to_dict("records"):
        assert sha(PROJECT_ROOT / r["local_path"]) == r["sha256"]
    remote = built["inventory"][built["inventory"]["source_kind"]
                                == "NSE_BRSR_PDF_URL"]
    assert len(remote) == 417
    assert (remote["download_status"] == "NOT_DOWNLOADED_OWNER_DECISION").all()


def test_no_download_code_path():
    src = Path(ev.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "http_client", "urlopen", "download("):
        assert forbidden not in src


def test_unreadable_documents_reported_not_guessed(built):
    local = built["local"]
    bad = local[local["text_status"].isin(["UNREADABLE", "NO_TEXT_LAYER"])]
    assert set(bad["file_name"]) == {"Fianncial Highlights 10 years.pdf",
                                     "RIL_Integrated-Annual-Report-2019-"
                                     "2020.pdf"}


def test_candidates_traceable_to_page_text(built):
    c = built["candidates"]
    assert len(c) > 0
    assert c["status"].str.startswith("CANDIDATE_").all()
    texts = built["texts"]
    for r in c.sample(min(25, len(c)), random_state=1).to_dict("records"):
        page = " ".join(texts[r["sha256"]][int(r["page"]) - 1].split())
        assert r["matched_text"][:60] in page


def test_implausible_counts_marked(built):
    c = built["candidates"]
    bad = c[c["status"] == "CANDIDATE_IMPLAUSIBLE"]
    assert (bad["requirement_id"].isin(["G04", "G05"])).all()
    assert (bad["candidate_value_text"].astype(int) > 30).all()


def test_coverage_statuses(built):
    cov = built["coverage"]
    assert set(cov["status"]) <= {
        "CANDIDATE_EVIDENCE_FOUND", "DOCUMENT_AVAILABLE_NO_CANDIDATE",
        "NO_LOCAL_DOCUMENT",
        "NOT_AVAILABLE_IN_BRSR_NO_SUPPLEMENTARY_EVIDENCE"}
    ril = cov[cov["master_isin"] == "INE002A01018"]
    assert (ril.loc[ril["requirement_id"] == "S27", "status"]
            == "CANDIDATE_EVIDENCE_FOUND").all()
    other = cov[cov["master_isin"] != "INE002A01018"]
    assert not (other["status"] == "CANDIDATE_EVIDENCE_FOUND").any()


def test_crosscheck_is_evidence_only(built):
    x = built["crosscheck"]
    assert set(x["result"]) <= {"FOUND_VERBATIM", "NOT_FOUND_VERBATIM",
                                "NOT_INFORMATIVE_SHORT_NUMBER", "NOT_NUMERIC"}
    found = x[x["result"] == "FOUND_VERBATIM"]
    assert (found["pages"] != "").all()


def test_assurance_phrases_kept_separate(built):
    a = built["assurance"]
    phrases = a[a["evidence"] == "PHRASE"]
    assert set(phrases["phrase_type"]) == {"REASONABLE_ASSURANCE",
                                           "LIMITED_ASSURANCE", "ASSESSMENT"}


# ---- integration and pending decisions ---------------------------------------------

def test_workflow_integration_and_pending_decisions_unchanged():
    from tests.conftest import review_workflow_build
    out = review_workflow_build()           # wf.build(write=False)
    q = out["queue"]
    s = q[q["category"] == "SUPPLEMENTARY_EVIDENCE"]
    assert len(s) > 0 and set(s["status"]) <= {"DETECTED", "RECOMMENDED"}
    assert (q["reviewer_decision"] == "").all()
    old = read_csv(BACKUP / "review_workflow" / "review_queue.csv")
    merged = old.merge(q, on="recommendation_id", suffixes=("_old", ""))
    assert len(merged) == len(old)
    assert (merged["status_old"] == merged["status"]).all()
    fse = q[q["recommendation_id"].str.startswith("FSE-")]
    assert len(fse) == 39 and (fse["status"] == "PENDING_REVIEW").all()
    assert (q[q["recommendation_id"].str.startswith("DQ-")]["status"]
            == "DEFERRED").sum() == 39
    assurance = q[q["category"] == "ASSURANCE"]
    for rule, n in (("A70_AS02_ASSESSMENT_WITH_ASSURANCE_LEVEL", 9),
                    ("A70_AS08_SIGNED_BEFORE_PERIOD_END", 2),
                    ("A70_AS07_REASONABLE_FLAG_WITH_LIMITED_INDICATORS", 38)):
        rows = assurance[assurance["detection_rules"] == rule]
        assert len(rows) == n and (rows["status"] == "DETECTED").all()


def test_raw_pdfs_unchanged():
    for line in (BACKUP / "raw_pdfs_before.sha").read_text().splitlines():
        digest, path = line.split(None, 1)
        assert sha(PROJECT_ROOT / path.lstrip("*").strip()) == digest


def test_only_review_workflow_outputs_regenerated():
    changed = []
    for line in (BACKUP / "all_files_before.sha").read_text().splitlines():
        digest, path = line.split(None, 1)
        rel = path.lstrip("*").strip()
        if rel in APPROVED_SWITCH_CHANGES:
            continue
        p = PROJECT_ROOT / rel
        if not p.exists() or sha(p) != digest:
            changed.append(rel)
    assert all(c.startswith(REGENERATED) for c in changed), changed
