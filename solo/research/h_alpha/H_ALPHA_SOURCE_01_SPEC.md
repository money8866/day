# H-ALPHA-SOURCE-01 -- SPEC (pre-registration)

**Research ID** H-ALPHA-SOURCE-01  
**Title** HVT / W7 alpha source attribution  
**Version** 1.0  
**Created** 2026-09-30  
**Trading authorisation** NO

This document was written after `H_ALPHA_SOURCE_01_FREEZE.md` and **before** any attribution number existed. Every constant below is read from `PREREG_A` in `has_common.py`, so the specification and the executable cannot diverge.

## 1. Question

Where do the recorded HVT-BULL / W7 returns actually come from? Four candidate sources, each tested with its own matched counterfactual:

1. **Candidate** -- is the stock itself better than a matched random stock?
2. **Event** -- does the HVT/W7 event date beat a random date on the same stock?
3. **Delay** -- does the system's waiting time between the event and the fill create return?
4. **Entry** -- does the specific fill day beat a fixed delay and a random fill day?

## 2. Hypotheses

**Null**  The recorded returns are explained by stock-pool characteristics, market environment, time effects and random entry; no component of the event / fill machinery carries a stable, independent, out-of-sample increment.

**Alternative**  At least one explicitly named component still carries a stable, repeatable, out-of-sample increment once the other factors are matched away.

No component is assumed to be the answer in advance.

## 3. Objects under study (frozen)

| system | ledger | own-trade set |
|---|---|---|
| HVT | `te_backtest_events_20250101_20260828.csv` | `BUY or BUY_ON_CONFIRM` |
| W7 | `te3_v31_events_20240101_20260828.csv` | `PRIMARY BUY or CONDITIONAL BUY` |

Neither system is modified, re-parameterised or re-run. See the FREEZE document for the full definition, hashes and the bug scan.

## 4. Research units (four levels)

| level | unit | observed leg | matched counterfactual |
|---|---|---|---|
| 1 Candidate | one candidate event | the stock the system bought | a random eligible stock, **same session**, same board, same market-cap tercile, same liquidity tercile, same SW-L1 industry preferred |
| 2 Event | one event anchor | event leg `open[ev+1]` | a random eligible session of the **same stock** in the **same calendar month**, excluding +-5 sessions around the event and the fill |
| 3 Delay | one event anchor | the frozen delay ladder `D0/D1/D3/D5/D10` | the system's own fill (Original) |
| 4 Entry | one event anchor | the system's own fill | a fixed delay fill and a random fill inside the event lifecycle |

## 5. Frozen measurement convention

* Entry `open[t_decision + 1]   (production convention, verified: actual_entry == raw open[decision_date+1] in 7396/7396)`
* Exit `close[entry + h] / open[entry] - 1, no stop`
* Horizons `[3, 5, 10, 20]`, primary `T+10`
* Delay ladder `[0, 1, 3, 5, 10]` -- fixed here, **never** chosen from results
* Overlap control: one observation per event lifecycle; a further guard requires `20` sessions between retained observations of the same stock.
* The production risk overlay (structural stop / right-tail DD / double stop) is NOT part of this experiment: the study attributes the SIGNAL, not the money-management layer.

## 6. Universe (identical for every arm)

| constraint | value |
|---|---|
| listed sessions | >= 250 |
| ST | excluded (`True`) |
| delisting | excluded (`True`) |
| BSE | excluded (`True`) |
| suspension hole | a run of >= 60 missing prints ends eligibility |

The observed leg is **not** re-filtered against this universe: it is whatever the frozen ledger recorded. The universe is what the counterfactual arms are drawn from.

## 7. Matching

Level 1 uses coarsened exact matching on `['trade_date', 'board', 'mv_bucket', 'liq_bucket']`, with the same SW-L1 industry preferred inside the cell and the coarsened cell used only when the industry subset is empty (the fallback rate is reported). The control may not be the query's own stock and may not be another treated observation on the same session.

Matching variables are exactly market cap, liquidity, board, industry and trade date. **No variable is added after seeing a result.**

## 8. Costs

Ladder `[0, 10, 20, 30, 50]`bp, primary **30bp**.

`observed leg only -- the counterfactual is a non-traded benchmark, so charging it would make the increment cost-invariant by construction.  Every level uses the same 30bp rule.`

Every level therefore reports both `delta_gross` and `delta_net30`, where `delta_net30 = delta_gross - 30bp`.

## 9. Statistics

| item | value |
|---|---|
| cluster bootstrap (by calendar month) | B = 1000 |
| randomised null replicates | B = 1000 |
| seed | 20261101 |
| tail levels | [0.01, 0.05, 0.1] |
| minimum n for a reportable cell | 30 |
| regime index | `000300.SH` |

## 10. Phase split

Driven by what the frozen ledgers actually cover: IS `(2024, 2024)`, VALID `(2025, 2025)`, OOS `(2026, 2026)`. Walk-forward folds are consecutive single-year pairs; they test whether the attribution direction is stable, **not** to select anything.

## 11. ROBUST criterion

A source is called `ROBUST` only if **all** of the following hold, otherwise it is `UNPROVEN`:

1. `Original > Counterfactual`
2. still true after 30bp net
3. holds out of sample
4. holds in more than one year
5. not confined to a single market regime
6. still visible after dropping the top 5% of observations
7. the cluster-bootstrap CI does not straddle the meaningful zero, or at minimum the direction is consistent
8. no bias-audit failure

## 12. Final reporting

Only an `ALPHA SOURCE` label is emitted: `Candidate-dominant`, `Event-dominant`, `Timing-dominant`, `Mixed`, `No robust Alpha`. These are attribution findings, not strategy ratings.

## 13. Explicitly forbidden

Parameter tuning; adding indicators; adding themes; adding market filters; machine learning; modifying HVT; modifying W7; searching for the best entry; searching for the best holding period; redefining the candidate pool, the event or the delay after seeing results; using future data; using future profitability to select the sample; using future breakouts to define a past signal; claiming a causal narrative in place of an observed matched difference.
