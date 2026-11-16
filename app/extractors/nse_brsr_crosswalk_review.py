"""
Review decisions for taxonomy crosswalk links that need review.

Approves ONLY unambiguous spelling / letter-case / formatting corrections:
the old and new concept names must split into the same number of words,
and every differing word must differ only by letter case or by a small
typo (<= 2 character edits, similarity >= 0.75). Any added, removed or
reworded word keeps the link as CANDIDATE_REVIEW.

Decisions (data/reference/brsr_crosswalk_review_decisions.csv):
  APPROVED_SPELLING_CORRECTION     concept identity unambiguous
  IDENTITY_CONFIRMED_TYPE_CHANGED  same concept name in both versions;
                                   only the data type changed (values may
                                   need translation - flag kept)
  CANDIDATE_REVIEW                 ambiguous - left for manual review

Existing rows in the decisions file are never overwritten, so manual
decisions survive re-runs. Source data is not modified.

Run:
    python -m app.extractors.nse_brsr_crosswalk_review
"""

import difflib
import re
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
ADJACENT_FILE = (
    BASE_DIR / "data" / "processed" / "taxonomy_versions"
    / "nse_brsr_concept_crosswalk_adjacent.csv"
)
DECISIONS_FILE = (
    BASE_DIR / "data" / "reference" / "brsr_crosswalk_review_decisions.csv"
)

KEY = ["source_version", "source_concept", "target_concept"]


def split_words(name):
    """CamelCase -> words, keeping acronyms (NOx -> ['N', 'Ox']?) simple:
    split on lower->upper and letter->digit boundaries only."""

    return re.findall(r"[A-Z]+[a-z0-9]*|[a-z0-9]+", name)


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1, current[j - 1] + 1,
                previous[j - 1] + (ca != cb),
            ))
        previous = current
    return previous[-1]


def spelling_correction(old, new):
    """Return (is_correction, explanation)."""

    if old.lower() == new.lower():
        return True, f"letter-case only: {old} -> {new}"

    old_words, new_words = split_words(old), split_words(new)

    if len(old_words) != len(new_words):
        # Case changes can shift word boundaries (e.g. 'forPerformanceand'
        # vs 'ForPerformanceAnd'); compare case-insensitively on the
        # whole name in that case.
        if edit_distance(old.lower(), new.lower()) <= 2:
            return True, (
                f"formatting/typo: {edit_distance(old.lower(), new.lower())}"
                " character edit(s) ignoring case"
            )
        return False, (
            f"word count differs ({len(old_words)} vs {len(new_words)}): "
            "words added or removed"
        )

    diffs = []
    for a, b in zip(old_words, new_words):
        if a == b:
            continue
        if a.lower() == b.lower():
            diffs.append(f"{a}->{b} (case)")
            continue
        distance = edit_distance(a.lower(), b.lower())
        ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
        if distance <= 2 and ratio >= 0.75:
            diffs.append(f"{a}->{b} (typo)")
        else:
            return False, f"word changed: {a} -> {b}"

    if not diffs:
        return True, "identical words"
    return True, "spelling correction: " + ", ".join(diffs)


def decide(row):
    method = row["match_method"]
    flags = row["change_flags"]

    if row["value_translation"]:
        return ("CANDIDATE_REVIEW",
                "value domain changed; translation needs approval: "
                + row["value_translation"])

    if method == "EXACT_NAME":
        return ("IDENTITY_CONFIRMED_TYPE_CHANGED",
                f"same concept name; {flags}: "
                f"{row['source_data_type']} -> {row['target_data_type']}")

    if method in ("NAME_SIMILARITY", "CASE_INSENSITIVE", "SAME_LABEL",
                  "RENAME_RULE"):
        ok, why = spelling_correction(
            row["source_concept"], row["target_concept"]
        )
        if ok:
            reason = why
            if flags:
                reason += f"; note {flags}"
            return "APPROVED_SPELLING_CORRECTION", reason
        return "CANDIDATE_REVIEW", f"not a pure spelling fix - {why}"

    return "CANDIDATE_REVIEW", f"unhandled method {method}"


def main():
    adjacent = pd.read_csv(ADJACENT_FILE, dtype=str).fillna("")
    review = adjacent[adjacent["needs_review"] == "True"].copy()

    decisions = []
    for row in review.to_dict("records"):
        decision, reason = decide(row)
        decisions.append({
            "source_version": row["source_version"],
            "target_version": row["target_version"],
            "source_concept": row["source_concept"],
            "target_concept": row["target_concept"],
            "match_method": row["match_method"],
            "change_flags": row["change_flags"],
            "decision": decision,
            "reason": reason,
            "decided_by": "rule:nse_brsr_crosswalk_review",
        })

    new = pd.DataFrame(decisions)

    if DECISIONS_FILE.exists():
        existing = pd.read_csv(DECISIONS_FILE, dtype=str).fillna("")
        keep_keys = set(map(tuple, existing[KEY].values))
        new = new[[tuple(k) not in keep_keys for k in new[KEY].values]]
        combined = pd.concat([existing, new], ignore_index=True)
        print("Existing decisions kept:", len(existing))
    else:
        combined = new

    DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(DECISIONS_FILE, index=False, encoding="utf-8-sig")

    print("New decisions added:", len(new))
    print(combined["decision"].value_counts().to_string())
    print("\nDecisions file:", DECISIONS_FILE)


if __name__ == "__main__":
    main()
