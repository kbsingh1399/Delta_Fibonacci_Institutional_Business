# Independent Peer-Review Package (Consolidated Edition)

This directory holds the consolidated institutional peer review of this
repository at commit 49d6a4f, plus the two crash fixes applied in the
accompanying commit(s).

## Deliverables

- `Institutional_Quant_Peer_Review.docx` / `.pdf` - the formal report
  (23 pages: execution-verified backtests, honest-protocol experiment,
  Monte Carlo / DSR audit, live-parity analysis, fill-model adjudication,
  and the claim-by-claim reconciliation with the second reviewer).
- `gemini_neon_static_review_original.html` - the second reviewer's report,
  as received (static code review without data execution), preserved verbatim.
- `peer_review_honest_results.csv` - per-cell results of the honest-selection
  protocol (92 cells: select on train 70% only, evaluate once on holdout 30%).
- `honest_results.json` - full experiment output behind the CSV.
- `risk_analysis.json` - true time-ordered portfolio metrics, IID vs
  stationary-block bootstrap, cost sensitivity.
- `fill_model_adjudication.json` - engine vs corrected fill model per champion
  (settles the cross-review phantom-fill dispute; portfolio delta -1.0%).
- `portfolio_trades.csv` - the 27,186-trade time-ordered portfolio ledger of
  the 25 committed champions (first true cross-asset equity curve).
- `charts/` - the three report figures as standalone PNGs.

## Re-running the analysis

The scripts in `scripts/peer_review/` re-derive every number in the report
from this repository's own data lake and engine, with no external inputs:

```bash
pip install numpy pandas numba scipy pyarrow
python scripts/peer_review/smoke_test_fixes.py          # pipeline sanity check
python scripts/peer_review/honest_oos_experiment.py     # ~6 min: honest protocol + champion reproduction
python scripts/peer_review/block_bootstrap_analysis.py  # ~3 min: portfolio risk, block bootstrap, costs
python scripts/peer_review/adjudicate_gemini_claims.py  # ~3 min: cross-review claim adjudication
python scripts/peer_review/make_report_charts.py        # ~3 min: regenerate the three figures
```

Outputs land in `audit/peer_review/output/`. Set `PEER_REVIEW_REPO` to run
against a different checkout. Note: the analysis was executed against the
engine exactly as committed at 49d6a4f (the two script fixes in the same push
do not touch `engine/`), so committed results and re-runs correspond.

## Headline findings (see the report for detail)

1. Champion selection ran on the full sample before the 70/30 "validation"
   split - the +1,225R OOS claim is leakage-contaminated; the honest protocol
   yields +692.0R with a median holdout PF near 1.0 and 18 gate-passing cells.
2. The live daemon implements fixed 2.5R targets for trail champions
   (no ratchet/breakeven): the deployable portfolio is +1,044.5R vs the
   backtested +3,008.1R (-65%).
3. The $0.05/trade cost assumption (0.01R) is 5-20x optimistic; portfolio
   breakeven is 0.111R/trade, so the OOS edge spans +827.6R to -395.3R across
   the realistic 0.05-0.20R cost band.
4. The DSR implementation is not Bailey & Lopez de Prado (0.40 haircut, no
   V[SR_trials], unit mismatch, OR backdoor admitting 6 zero-DSR champions).
5. Cross-review: every checkable claim of the second reviewer is confirmed
   (including both crash bugs, now fixed) except its headline phantom-fill
   claim, which is refuted by experiment (-1.0% portfolio delta, two-sided
   per-champion errors); its dead-pinbar-clause and 5bps-EMA-filter findings
   are confirmed.

Both reviews independently conclude: halt live trading until the parity,
validation, cost and margin defects are remediated.
