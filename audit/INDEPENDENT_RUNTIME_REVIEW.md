# Delta Fibonacci runtime review

The strategy stayed profitable across the tested six-month historical execution scenarios. The live $40 per-trade sizing cap allowed an account-rule breach in the slower-trailing replay. A $12 cap with a budget check before every fill cut drawdown and stayed profitable in the three configurations tested. Forward evidence consists of five completed broker positions, −$20.15 net. That sample leaves the live edge unproven.

## Six-month replay

The portfolio starts flat at $2,500 on May 17, 2026 and uses the 23 configured assets through Nov 17, 2026, end exclusive. P&L is in USD. The sizing logic targets $5 and can fall back to the broker minimum lot. The cap applies to planned risk. Drawdown uses sampled equity including open positions.

| Scenario | Trades | Net P&L | Profit factor | Max equity drawdown |
| --- | ---: | ---: | ---: | ---: |
| Current $40 cap, candle-direction path | 560 | +$1,142.87 | 1.36 | $209.24 |
| Current $40 cap, both fixed high/low orderings | 545–569 | +$927.37 to +$1,384.97 | 1.30–1.44 | $214.61–$237.62 |
| Current $40 cap, cost stress | 570 | +$787.13 | 1.24 | $292.46 |
| Current $40 cap, slower trail | 293 | +$499.81 at breach | 1.28 | $164.61 |
| $12 cap and per-fill budget check | 475 | +$590.44 | 1.36 | $107.01 |
| $12 cap and per-fill check, cost stress | 496 | +$396.13 | 1.22 | $130.42 |
| $12 cap and per-fill check, slower trail | 445 | +$383.69 | 1.23 | $118.87 |

The current profile sized trades up to $39.12 and permitted one to three fills above its intended combined $90 budget, depending on the scenario. The slower-trail scenario crossed the modeled daily loss limit at $104.26 on Aug 26 and the replay stopped there. With the $12 cap, the worst sampled daily loss among the three tested scenarios was $42.44. The replay applies the documented $100 daily and $250 static account limits. Other strategies on the account can contribute to both limits. [Blueberry Funded Prime drawdown rules](https://help.blueberryfunded.com/en/articles/12136586-what-are-the-drawdown-limits-on-a-prime-account)

The 2024 replay shows why positive recent results are insufficient. With the current cap and the candle-direction path, equity touched the $2,250 static floor and the run stopped at −$250.82. The same year's fixed low-first path returned +$418.82 without touching either floor. The current cap also touched the daily floor in the 2025 low-first run and both early-2026 runs. The $12 cap with the per-fill check returned +$541.65 on 1,079 trades in the 2024 candle-direction replay. Its worst sampled daily loss was $56.36, and equity stayed above both floors. The tested $12 ceiling restores an earlier repository setting.

## Recorded quotes and live fills

Broker Bid/Ask quotes covered every archived H1 trading hour for NAS100, XAGUSD, BTCUSD, EURUSD, USDCHF and USDJPY from Nov 10 through Nov 17, end exclusive. The six-symbol tick replay returned **+$43.35 on six trades**. A matching bar replay using the same symbols and dates returned **+$51.10 on six trades**. This is a narrow execution check. The sampled portfolio excludes the other 17 champions.

The read-only MT5 deal check found **five fully closed Delta positions, −$20.15 net** including commissions, fees and swaps. The earliest entry was after the end of the archived bars. At the separate positions check, two tagged Delta positions were open, both had stops, and there were no tagged pending orders.

## Findings in the project

- [MT5_Backtest.py](../MT5_Backtest.py) reproduced 6,511 trades and +1,399.06R. It replays positions independently, fills at the next bar's Bid open, assigns $5 to every 1R and omits broker and portfolio constraints. Its published P&L measures a different execution model.
- [mt5_alpha_trader_live.py](../mt5_alpha_trader_live.py) applies the spread gate, broker lot steps and minimum lot size, one active position per cluster, four portfolio slots, cooldowns and the account governor. Its budget check uses the $5 target before the scan. A later order can size above $5, and the live sweep does not recheck the budget after each fill. The stricter replay uses the candidate's actual planned risk and checks every order against the $90 combined allowance.
- [optimize_blueberry_basket.py](../optimize_blueberry_basket.py) ranks model, timeframe, EMA and exit variants using each asset's full archive. Running the resulting basket on the later portion of that archive is a retrospective date split. The earlier [CRO review](institutional_cro_review/Delta_Fibonacci_MT5_Institutional_Audit.pdf) independently raised basket selection and multiple testing as risks.

## Method and reproduction

The [independent replay](../independent_runtime_replay.py) calls the live scanner, lot sizing, governor, portfolio sweep and trailing manager from commit `2bbfe94` through a paper broker. In 1,609 spot checks across the basket, all 240 executable signals passed the preliminary historical screen and the live sizing invariants. The replay uses H1 paths for execution on both signal timeframes. Synthetic paths vary the extremes' order and whether management observes the intrabar extremes. The tick check evaluates recorded stop and target crossings between 10-second client polls. Order rejection due to liquidity, latency, and account margin is outside the simulation. The replay assesses Delta in isolation from the other bots. [MetaQuotes tick-testing documentation](https://www.mql5.com/en/docs/runtime/testing)

Recorded archive timestamps serve as the broker clock for the primary daily-anchor replay. A read-only check of the connected terminal showed the tick timestamp approximately two hours ahead of wall-clock UTC. The results include a two-hour anchor-shift sensitivity. The model uses current captured contract specifications, archived spread, closed hourly currency conversions, and an estimated $7 per round-trip FX or gold lot. The cost stress doubles the archived spread, adds one adverse tick at entry and stop exits and estimates carry at 0.02 initial risk per calendar day crossed. [Blueberry commission schedule](https://helpcentre.blueberrymarkets.com/en/trading-accounts-fees-trading-fees)

```powershell
python independent_runtime_replay.py
python independent_runtime_replay.py --tick-cache ../FVG_Strategy/.fvg-replay/ticks
```

The [results JSON](independent_runtime_replay.json) contains the earlier date windows, fixed intrabar orderings, risk-budget counts, per-symbol P&L, quote coverage and the paired bar/tick comparison. MT5 was queried read-only. All replay orders were simulated.
