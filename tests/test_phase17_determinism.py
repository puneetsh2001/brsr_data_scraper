"""
Phase 17: determinism found by the full pipeline re-run.

The isolated full-corpus run reproduced every file byte for byte except 35
A31_SCOPE_UNIT_TAGS_DIFFER reasons, whose text listed the scope tags in set
iteration order (dependent on PYTHONHASHSEED). The engine now lists them in
sorted order, which is the order of the accepted output.
"""

import ast
import os
import subprocess
import sys

import pandas as pd

from tests.conftest import PROJECT_ROOT

ANOMALIES = PROJECT_ROOT / "data/processed/nifty100/review_workflow/" \
    "anomalies.csv"
PREFIX = "scope unit tags differ in one report: "

PROBE = """
import pandas as pd
from app.review import anomaly_engine as ae
rows = [{"master_isin": "INE1", "master_nse_symbol": "X", "company_name": "X",
         "fy_slot": "2024-25", "requirement_id": req, "breakdown": "",
         "final_value": "1", "final_unit": "tCO2e", "original_value": "1",
         "original_unit": unit, "source_file": "f.xml",
         "context_reference": "C" + req, "source_url": "u",
         "metric_name": "m"}
        for req, unit in (("E20", "Non-SI:MtCO2e"), ("E18", "Non-SI:tCO2e"),
                          ("E17", "Non-SI:tCO2e"))]
df = pd.DataFrame(rows)
a = ae.Anomalies(df)
ae.scope(df, a)
print(" || ".join(r["reason"] for r in a.rows
                 if r["detection_rule"] == "A31_SCOPE_UNIT_TAGS_DIFFER"))
"""


def test_accepted_scope_tag_reasons_are_sorted():
    a = pd.read_csv(ANOMALIES, dtype=str, keep_default_na=False)
    reasons = a.loc[a["reason"].str.startswith(PREFIX), "reason"]
    assert len(reasons) == 35
    for r in reasons:
        tags = ast.literal_eval(r[len(PREFIX):])
        assert list(tags) == sorted(tags), r


def test_scope_tag_order_does_not_depend_on_hash_seed():
    outs = set()
    for seed in ("0", "1", "4242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", PROBE], cwd=PROJECT_ROOT,
                           env=env, capture_output=True, text=True,
                           timeout=120)
        assert r.returncode == 0, r.stderr[-400:]
        outs.add(r.stdout.strip().splitlines()[-1])
    assert outs == {PREFIX + "{'E17': 'Non-SI:tCO2e', 'E18': "
                    "'Non-SI:tCO2e', 'E20': 'Non-SI:MtCO2e'}"}
