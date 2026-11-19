#!/usr/bin/env python3
"""smoke_test_fixes.py: verifies the two crash fixes on a real run without
touching committed audit artifacts.
1. py_compile both patched scripts.
2. Import-run the validator module (no heavy work at import).
3. Run optimize_multidim_basket.run_full_basket_optimization() with
   CHAMPION_SYMBOLS reduced to one symbol and AUDIT_DIR redirected to a temp
   directory; assert the champions JSON + markdown report are produced.
"""
import importlib.util
import os
import sys
import tempfile

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPO = _os.environ.get("PEER_REVIEW_REPO", _os.path.dirname(_os.path.dirname(_HERE)))
OUT_DIR = _os.path.join(REPO, "audit", "peer_review", "output")
_os.makedirs(OUT_DIR, exist_ok=True)
sys.path.insert(0, REPO)

import py_compile

for f in ["scripts/run_quant_validation_and_monte_carlo.py", "scripts/optimize_multidim_basket.py"]:
    py_compile.compile(os.path.join(REPO, f), doraise=True)
    print("compile OK:", f)

# import validator module (module-level code only sets paths)
spec = importlib.util.spec_from_file_location(
    "rqv", os.path.join(REPO, "scripts", "run_quant_validation_and_monte_carlo.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print("validator module imports OK; has main guard:", hasattr(mod, "run_quant_anti_curve_fit_audit"))

# optimizer smoke run, outputs to temp
spec2 = importlib.util.spec_from_file_location(
    "omb", os.path.join(REPO, "scripts", "optimize_multidim_basket.py"))
ob = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(ob)
print("optimizer module imported OK")
tmp = tempfile.mkdtemp(prefix="optimizer_smoke_")
ob.AUDIT_DIR = tmp
ob.CHAMPION_SYMBOLS = ["USWTI.p"]
ob.run_full_basket_optimization()

out_json = os.path.join(tmp, "asset_multidim_champions.json")
out_md = os.path.join(tmp, "MULTIDIM_ALPHA_BASKET_REPORT.md")
assert os.path.exists(out_json), "champions JSON not written"
assert os.path.getsize(out_json) > 100, "champions JSON empty"
assert os.path.exists(out_md), "report not written"
import json
champs = json.load(open(out_json))
tfs = champs["USWTI.p"]["timeframes"]
print("\nSMOKE RESULT: champions JSON written with timeframes:", list(tfs.keys()))
for tf, d in tfs.items():
    print(f"  USWTI {tf}: {d['model']}/{d['param']}/{d['exit_mode']} net_r={d['net_r']}")
print("\nALL SMOKE CHECKS PASSED")
