# H-ALPHA-SOURCE-01 -- FREEZE

**Research ID** H-ALPHA-SOURCE-01  
**Title** HVT / W7 alpha source attribution  
**Frozen at** 2026-09-30 22:54:26  
**Mode** read-only forensic freeze. No frozen object was created, modified or re-run by this study.

| hash | value |
|---|---|
| `strategy_hash` | `85d0029671a3531485a725db72bd84267ded3e7983f31ee132d9e7310cc81111` |
| `config_hash` | `9bbde872f1bacc23a1b863d59d799f796d275cbc04591018f7bd24f1ea451af9` |
| `data_snapshot` | 3 artefacts, see below |

---

## 1. HVT-BULL definition (frozen)

| item | frozen value | source |
|---|---|---|
| Candidate pool | `total_mv >= 30 亿` AND `amount MA20 >= 3000 万` AND listed >= 120 sessions AND not ST AND not BJ | `hvt_bull/config.yaml`, `hvt_bull/daily.py:_universe()` |
| Event (T0) | HVT 天量日; percentile-120 >= 98 AND ratio-20 >= 2.0; grade A/B/C at pct 99/98/95 and ratio 3.0/2.0/1.8; strict union of the 20240801 anchor chain and a rolling 250-session chain | `hvt_bull/engine.py:detect_hvt()` |
| Event cooldown | 10 sessions | `hvt_bull/te_backtest.py:536` |
| Signal date | `signal_date` = T0 (rounded volume day) | ledger |
| Decision date | `decision_date`, walking `decision_lag` in `[0, 40]` | ledger, `te_backtest.py` |
| Entry (execution) | `open[decision_date + 1]` | `te_backtest.py:511` `ActualEntry=T+1开盘` |
| Exit | production uses a structural stop (`structural_loss_days=2`), a volume-down rule (`vol_down_ratio=1.3`) and a 15%% right-tail drawdown | `hvt_bull/config.yaml:83-84` |
| State machine | NORMAL / HVT_DETECTED / HVT_STRONG / WATCH / LOCKING / LOCKED / BREAKOUT_READY / PRIMARY_BUY / T20_ROCKET_WATCH / CONFIRMED / FAILED / DISTRIBUTION / EXIT / EVENT_SPIKE | `hvt_bull/models.py:8-12` |
| Own-trade set | `next_day_action in {BUY, BUY_ON_CONFIRM}` | ledger |
| Daily Top-N | <= 3 per `decision_date`, ranked by `execution_score` then `buyability` | `te_backtest.py:417-425` (`max_buy_candidates=3`) |

## 2. W7 definition (frozen)

| item | frozen value | source |
|---|---|---|
| Candidate pool | `circ_mv >= 50 亿`, >= 250 bars, data from 20230103, prefixes 43/83/87/88/92 excluded | `w7_second_wave_engine.py:19-21, 358-383` |
| Event anchor | 120-session rolling percentile with `min(p_turnover, p_volume) >= 99.0` (double-AND), the flag day must close as a yang line | `w7_second_wave_engine.py:525-544`; identical rule in `w7_te_v3_backtest.py:65-82` |
| Event age cap | 60 sessions | `MAX_EVENT_AGE` |
| States | DOWNTREND / BASE / IMPULSE / EXTREME_CHURN / ABSORPTION / DRYUP / RE_EXPANSION / BREAKOUT_CONFIRM / SECOND_WAVE / T0_CONFIRM / BREAKOUT_RETEST / MIDLINE_HOLD / DISTRIBUTION / FAILED | `w7_second_wave_engine.py:26` |
| Buy states | SECOND_WAVE / BREAKOUT_CONFIRM / RE_EXPANSION / T0_CONFIRM / BREAKOUT_RETEST / MIDLINE_HOLD | `w7_second_wave_engine.py:30` |
| Signal date | `signal_date` = the replay session inside the event lifecycle | ledger |
| Entry (execution) | decision-day close signal -> `open[j+1]`, "与 hvt_bull te_backtest 一致" | `w7_te_v3_backtest.py:11` |
| Gates | G1 EXTREME_CHURN no-chase; G2 `Exec >= 85`; G3 `volr <= 2.2`; G4 market regime >= 1 | `w7_te_v3_backtest.py:7-8` |
| Grading | PRIMARY BUY (Alpha>=60 & DRisk<=20 & price in buy zone) / CONDITIONAL BUY / WAIT / WATCH / AVOID | `w7_te_v3_backtest.py:9` |
| Own-trade set | `action in {PRIMARY BUY, CONDITIONAL BUY}` | ledger |

## 3. Data version / backtest period

| system | ledger | rows | period | signal->return convention |
|---|---|---|---|---|
| HVT | `te_backtest_events_20250101_20260828.csv` | 59,240 | 2025-01-02 .. 2026-09-23 (`signal_date` 2025-01-02 .. 2026-08-28) | `r*` = decision-day close basis; `er*` = T+1-open basis |
| W7 | `te3_v31_events_20240101_20260828.csv` | 193,497 | 2024-01-02 .. 2026-08-28 | `r*` = signal-day close basis; `er*` = T+1-open basis |

Shared price source: `research/fundamental_surprise_alpha/data/price_panel.parquet` (20180102 .. 20260924, qfq), `basic_panel.parquet` (total_mv / turnover_rate), PIT ST and delisting flags from `cache_daily/treasure_namechg*.parquet`, PIT SW-L1 industry from `cache_daily/industry/sw_industry_map.csv`.

## 4. Verification performed before freezing

1. **Entry convention.** `actual_entry` was recomputed for all 7,396 actionable HVT rows against the panel: `raw open[decision_date+1]` reproduces it in 100.0%% of rows (mean relative error 0.0000). The ledger header states `ActualEntry=T+1开盘`. Entry is therefore frozen as `open[t_decision + 1]` for both systems.
2. **Return convention.** The ledger `r*` columns were recomputed from the panel. HVT `r{h}` = `close[decision_date+h] / close[decision_date] - 1` (T+1: corr 0.9996, 99.2%% of rows within 0.05pp). W7 `r{h}` = `close[signal_date+h] / close[signal_date] - 1` (T+1: corr 0.9792, 99.7%% within 0.05pp). Both ledgers also carry `er*`, an entry-based basis, and the two differ on 84-87%% of the non-zero-lag rows. This is a **documented dual-basis reporting convention, not a defect**.
3. **Price basis.** The production ledger is computed on raw prices; this study recomputes every leg on the shared qfq panel so the observed leg and all counterfactual legs share one basis. The T+10 mean differs by less than 0.03pp between the two bases for HVT (see section 6), so the switch does not move the object of study.

## 5. Bug scan result

**No blocking bug found. The experiment proceeds.**

| finding | status | note |
|---|---|---|
| Dual return basis `r*` vs `er*` | BY DESIGN | both are written to the ledger and `actual_entry` is separately recorded; the summary JSON labels the entry basis explicitly |
| `w7_backtest.py:64` uses a *single-indicator* `ep >= 98` OR rule on a 250-session window | OUT OF SCOPE | that script did not produce the ledger under study; the in-scope `w7_te_v3_backtest.py:65-82` uses the same double-`>= P99` AND rule as the production engine. Recorded so the two are never mixed in one attribution |
| `hvt_bull/daily.py` has no explicit cooldown while the backtests use 10 sessions; `backtest.py:517-549` splits the cooldown per chain in `both` mode while `te_backtest.py` does not | SCOPE NOTE | the ledger under study comes from `te_backtest.py`; its own rule is used verbatim. Not repaired |
| `w7_backtest_v41_signals.csv` placeholder rows | OUT OF SCOPE | not part of either frozen ledger |

Per the task protocol, nothing above was repaired. Each item is a scope boundary, not a defect in the frozen object.

## 6. Frozen baseline (restated on the study convention)

Entry `open[decision+1]`, exit `close[entry+h]`, qfq, one observation per event lifecycle (overlap guard 20 sessions). This is the number the attribution has to explain.

| system | horizon | n | mean gross | mean net 30bp | win rate | PF | median |
|---|---|---:|---:|---:|---:|---:|---:|
| HVT | T+3 | 2368 | +0.0078 | +0.0048 | 50.9% | 1.41 | +0.0014 |
| HVT | T+5 | 2363 | +0.0117 | +0.0087 | 50.3% | 1.51 | +0.0010 |
| HVT | T+10 | 2359 | +0.0148 | +0.0118 | 49.5% | 1.43 | -0.0006 |
| HVT | T+20 | 2325 | +0.0246 | +0.0216 | 50.5% | 1.52 | +0.0018 |
| W7 | T+3 | 3455 | +0.0054 | +0.0024 | 46.9% | 1.29 | -0.0023 |
| W7 | T+5 | 3453 | +0.0050 | +0.0020 | 47.1% | 1.21 | -0.0037 |
| W7 | T+10 | 3452 | +0.0078 | +0.0048 | 46.8% | 1.26 | -0.0052 |
| W7 | T+20 | 3430 | +0.0153 | +0.0123 | 47.7% | 1.36 | -0.0056 |

Observation counts after dedup + overlap guard:

| system | 2024 | 2025 | 2026 | total |
|---|---:|---:|---:|---:|
| HVT | 0 | 1624 | 747 | 2371 |
| W7 | 1257 | 1589 | 614 | 3460 |

HVT covers only 2025-2026; W7 covers 2024-2026. This asymmetry is a property of the frozen artefacts and is reported as-is -- the study does not extend the backtest window, because that would mean running the strategy rather than explaining its recorded output.

## 7. Frozen source files

| file | bytes | mtime | sha256 (first 16) |
|---|---:|---|---|
| `hvt_bull/__init__.py` | 605 | 2026-09-07 14:19:03 | `a81f06e102d2a459` |
| `hvt_bull/models.py` | 10295 | 2026-09-08 13:12:17 | `02ad4793c0434d6d` |
| `hvt_bull/config.yaml` | 9503 | 2026-09-25 08:12:02 | `547141c07cf616b4` |
| `hvt_bull/data_loader.py` | 4844 | 2026-09-07 14:22:20 | `20d54a20a75c6517` |
| `hvt_bull/context.py` | 8018 | 2026-09-27 15:10:03 | `97114c393ba70dd2` |
| `hvt_bull/engine.py` | 56430 | 2026-09-24 00:41:18 | `b6095d862ba97156` |
| `hvt_bull/daily.py` | 71218 | 2026-09-25 08:12:22 | `3e43709d10041c2b` |
| `hvt_bull/trade_execution.py` | 34208 | 2026-09-25 08:12:43 | `28d0e403511d5563` |
| `hvt_bull/te_backtest.py` | 28189 | 2026-09-24 00:49:37 | `aedbf17ec8d42136` |
| `hvt_bull/backtest.py` | 34881 | 2026-09-24 00:42:35 | `3739ec0ce9c3d8cc` |
| `hvt_bull/expectancy.py` | 4196 | 2026-09-05 14:56:09 | `a30b2307eee712d3` |
| `hvt_bull/future_expansion.py` | 24986 | 2026-08-30 16:09:02 | `41ed0abb34c7a723` |
| `hvt_bull/te_rocket_filter.py` | 31778 | 2026-09-17 23:54:14 | `46a0b06fe50c7700` |
| `hvt_bull/push.py` | 14027 | 2026-09-30 10:39:38 | `7acf9cbb3af18425` |
| `hvt_bull/run_daily_push.py` | 8685 | 2026-09-30 08:11:54 | `982315be8918da7f` |
| `w7_second_wave_engine.py` | 101887 | 2026-09-30 14:03:56 | `c9d654262bbc259f` |
| `w7_te_v3_backtest.py` | 23810 | 2026-09-05 03:57:58 | `e25c994182ebc81b` |
| `trade_execution_engine.py` | 37418 | 2026-09-30 13:40:51 | `5bcf6ef352bf3e03` |
| `w7_t1_gate.py` | 20841 | 2026-09-07 15:47:14 | `824c1e781945130e` |
| `w7_t20_right_tail_engine.py` | 64450 | 2026-09-18 12:37:50 | `75afeaedd113b843` |
| `hvt_bull/config.yaml` | 9503 | 2026-09-25 08:12:02 | `547141c07cf616b4` |

Declared **out of scope** (not read, not used as evidence):

| file | exists | sha256 (first 16) |
|---|---|---|
| `w7_backtest.py` | True | `a86162c0992d5e55` |
| `w7_backtest_v5.py` | True | `fd450afc663ca8df` |
| `w7_backtest_v41.py` | True | `3c6ac5011a0edbfb` |
| `w7_backtest_signals.csv` | False | `` |
| `w7_backtest_v5_signals.csv` | False | `` |

## 8. Data snapshot

| system | artefact | rows | bytes | mtime | sha256 (first 16) |
|---|---|---:|---:|---|---|
| HVT | `te_backtest_events_20250101_20260828.csv` | 59240 | 98905927 | 2026-09-23 15:13:09 | `a0a162c76c0b3714` |
| HVT | `te_backtest_20250101_20260828_summary.json` | 2016 | 43692 | 2026-09-23 15:13:03 | `8d628cc9dc6e5f13` |
| W7 | `te3_v31_events_20240101_20260828.csv` | 193497 | 51963332 | 2026-09-05 05:31:53 | `d8a2764447cffe16` |

Every downstream script in this study re-verifies `has_freeze_snapshot.json` before use; a mismatch aborts the run.
