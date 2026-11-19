#!/usr/bin/env python3
"""make_report_charts.py: generates the 3 figures for the peer-review report
from the experiment outputs (honest_results.json, risk_analysis.json + a
re-simulation of the 25 committed champions for the equity curve).
Style: docx skill chart templates (IG-1 gold accent, no top/right spines,
light grid, 200 DPI).
"""
import os
import sys
import json

CHART_DIR = os.path.join(OUT_DIR, "charts")
os.makedirs(CHART_DIR, exist_ok=True)
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPO = _os.environ.get("PEER_REVIEW_REPO", _os.path.dirname(_os.path.dirname(_HERE)))
OUT_DIR = _os.path.join(REPO, "audit", "peer_review", "output")
_os.makedirs(OUT_DIR, exist_ok=True)
sys.path.insert(0, REPO)
sys.path.insert(0, OUT_DIR)

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ACCENT = "#C9A84C"      # IG-1 gold
DARK = "#1A1A1A"
GREY = "#9AA0A6"
RED = "#B0413E"
BLUE = "#4A6A8A"

plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.family"] = "DejaVu Sans"


def save_chart(fig, path):
    fig.savefig(path, dpi=200, bbox_inches="tight", pad_inches=0.1,
                facecolor="white", edgecolor="none")
    plt.close(fig)
    print("saved", path)


# ---------- Figure 1: IS vs TEST scatter (honest protocol) ----------
honest = json.load(open(os.path.join(OUT_DIR, "honest_results.json")))
cells = [c for c in honest["cells"] if "honest_is_net_r" in c]
fig, ax = plt.subplots(figsize=(10, 6.2))
for c in cells:
    passed = c.get("honest_passes", False)
    ax.scatter(c["honest_is_net_r"], c["honest_test_net_r"],
               s=34, color=ACCENT if passed else GREY,
               edgecolor="white", linewidth=0.5, zorder=3,
               alpha=0.95 if passed else 0.65)
lim = 270
ax.plot([-40, lim], [-40, lim], ls="--", lw=1, color="#BBBBBB", zorder=1)
ax.axhline(0, color="#CCCCCC", lw=0.8, zorder=1)
notes = [("XRPUSD 5M", 2.0, -90.0, (-60, -80)), ("USDCHF 15M", 96.0, -54.5, (110, -62)),
         ("GBPNOK 15M", 50.5, 129.5, (70, 133)), ("USWTI 5M", 254.9, 80.2, (258, 88))]
for label, x, y, (tx, ty) in notes:
    ax.annotate(label, xy=(x, y), xytext=(tx, ty), fontsize=8.5, color="#555555",
                arrowprops=dict(arrowstyle="-", color="#999999", lw=0.7))
ax.set_xlabel("In-Sample (train 70%) best-of-grid Net R", fontsize=11)
ax.set_ylabel("Out-of-Sample (test 30%) Net R", fontsize=11)
ax.set_title("Honest Protocol: In-Sample Selection vs Holdout Performance (92 cells, 23 assets x 4 TFs)",
             fontsize=12.5, pad=12)
ax.set_xlim(-20, lim)
ax.set_ylim(-110, 150)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(alpha=0.3, color="#E0E0E0")
from matplotlib.lines import Line2D
ax.legend(handles=[
    Line2D([], [], marker="o", ls="", color=ACCENT, label="passes all gates (18 cells)"),
    Line2D([], [], marker="o", ls="", color=GREY, label="rejected / borderline (74 cells)"),
    Line2D([], [], ls="--", color="#BBBBBB", label="IS = OOS (no degradation)")],
    frameon=False, fontsize=9, loc="upper left")
save_chart(fig, os.path.join(CHART_DIR, "fig1_is_vs_oos.png"))

# ---------- Figure 2: cost sensitivity ----------
ra = json.load(open(os.path.join(OUT_DIR, "risk_analysis.json")))
oos0 = ra["portfolio_oos_true"]["net_r"]
full0 = ra["portfolio_full"]["net_r"]
n_oos = ra["portfolio_oos_true"]["trades"]
n_full = ra["portfolio_full"]["trades"]
costs = np.array([0.0, 0.01, 0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20])
oos_line = oos0 - costs * n_oos
full_line = full0 - costs * n_full
fig, ax = plt.subplots(figsize=(10, 6))
ax.plot(costs * 100, oos_line, marker="o", ms=5, lw=2, color=ACCENT,
        label=f"OOS portfolio ({n_oos:,} trades)")
ax.plot(costs * 100, full_line, marker="s", ms=5, lw=2, color=BLUE,
        label=f"Full-sample portfolio ({n_full:,} trades)")
ax.axhline(0, color=RED, lw=1, ls=":")
be_oos = oos0 / n_oos
be_full = full0 / n_full
ax.annotate(f"breakeven cost = {be_oos:.3f}R/trade\n(~{be_oos*100:.1f}% of $5 risk)",
            xy=(be_oos * 100, 0), xytext=(be_oos * 100 + 3.5, 600),
            fontsize=9, color="#555555", arrowprops=dict(arrowstyle="->", color="#999999", lw=0.8))
ax.scatter([0.01 * 100], [oos0 - 0.01 * n_oos], s=90, facecolor="none",
           edgecolor=RED, lw=1.6, zorder=5)
ax.annotate("modeled cost\n($0.05 per trade = 0.01R)", xy=(1, oos0 - 0.01 * n_oos),
            xytext=(4.2, 1350), fontsize=9, color=RED,
            arrowprops=dict(arrowstyle="->", color=RED, lw=0.8))
ax.set_xlabel("Round-trip cost per trade (% of $5 risk unit)", fontsize=11)
ax.set_ylabel("Portfolio Net R", fontsize=11)
ax.set_title("Cost Sensitivity: the entire OOS edge is within the cost uncertainty band", fontsize=12.5, pad=12)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(alpha=0.3, color="#E0E0E0")
ax.legend(frameon=False, fontsize=9.5, loc="upper right")
save_chart(fig, os.path.join(CHART_DIR, "fig2_cost_sensitivity.png"))

# ---------- Figure 3: portfolio equity curve (re-simulate 25 champions) ----------
from honest_oos_experiment import load, signals_for, run_exit, CRYPTO_QUANTBUG
committed = pd.read_csv(os.path.join(REPO, "audit", "QUANT_ROBUSTNESS_COMPLETE.csv"))
champs = committed[committed["passes_all"] == True]
trades = []
for _, r in champs.iterrows():
    sym, tf = r["symbol"], r["tf"]
    anchor = "utc" if any(k in sym.upper() for k in CRYPTO_QUANTBUG) else "broker"
    df = load(sym, tf)
    period = int(str(r["param"]).split("_")[1]) if r["model"] == "EMA" else None
    arr = signals_for(df, r["model"], period, anchor)
    bt_tr, _ = run_exit(df, arr, r["model"], r["exit_mode"], sym, tf, arr[7])
    split_ts = df["time"].iloc[int(len(df) * 0.70)]
    for t in bt_tr:
        trades.append({"exit_time": pd.Timestamp(t["exit_time"]), "r": t["r_multiple"],
                       "oos": pd.Timestamp(t["exit_time"]) >= split_ts})
tdf = pd.DataFrame(trades).sort_values("exit_time").reset_index(drop=True)
tdf.to_csv(os.path.join(OUT_DIR, "portfolio_trades.csv"), index=False)
eq = tdf["r"].cumsum()
run_max = pd.concat([pd.Series([0.0]), eq]).cummax().iloc[1:].reset_index(drop=True)
dd = run_max - eq
fig, ax = plt.subplots(figsize=(10, 6))
ax.plot(tdf["exit_time"], eq, lw=1.4, color=DARK, label="Portfolio equity (25 champions, time-ordered)")
ax.fill_between(tdf["exit_time"], eq, run_max, color=RED, alpha=0.15, label="Drawdown")
first_oos = tdf[tdf["oos"]]["exit_time"].iloc[0]
ax.axvline(first_oos, color=ACCENT, lw=1.6, ls="--")
ax.annotate("OOS holdout begins\n(per-symbol 70/30 boundary)", xy=(first_oos, 2200),
            xytext=(pd.Timestamp("2024-04-01"), 2450), fontsize=9, color="#7A6430",
            arrowprops=dict(arrowstyle="->", color=ACCENT, lw=0.9))
mdd_i = dd.idxmax()
ax.annotate(f"max drawdown {dd.max():.1f}R", xy=(tdf["exit_time"].iloc[mdd_i], eq.iloc[mdd_i]),
            xytext=(pd.Timestamp("2024-01-15"), 1300), fontsize=9, color=RED,
            arrowprops=dict(arrowstyle="->", color=RED, lw=0.9))
ax.set_xlabel("Exit time (UTC)", fontsize=11)
ax.set_ylabel("Cumulative Net R", fontsize=11)
ax.set_title("True Portfolio Equity Curve, 2023-01 to 2026-11 (25 committed champions, gross of costs)",
             fontsize=12.5, pad=12)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
ax.spines[["top", "right"]].set_visible(False)
ax.grid(alpha=0.3, color="#E0E0E0")
ax.legend(frameon=False, fontsize=9.5, loc="upper left")
save_chart(fig, os.path.join(CHART_DIR, "fig3_equity_curve.png"))

print("all charts done")
