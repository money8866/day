# Step 6 — 结构 / HVT 分层回测（结构层研究，不构成买入建议）

- 样本区间：20260522 → 20260916（35910 个候选日观测）
- 持有期：T+1, T+3, T+5, T+10, T+20, T+60
- 结构信号完全由 D 及以前数据定义（§三十七）；未来价格仅用于 outcome（§三十六）。
- candidate_at_signal_date = Step 5 当日候选状态，未做任何 future winner labeling。
- BACKTEST_MEMBERSHIP_LIMITED = true（membership_version_status = STATIC_ONLY，历史成员关系不可完整复现）

## 1. 结构分组 × 持有期（§三十四 / §四十八）

| group | horizon | n | win_rate | mean_return | median_return | positive_ratio | max_return | max_drawdown |
|---|---|---|---|---|---|---|---|---|
| ALL_CANDIDATES | T+1 | 35910 | 45.07% | -0.14% | -0.26% | 45.07% | 20.04% | -20.00% |
| ALL_CANDIDATES | T+3 | 35722 | 42.17% | -0.54% | -0.76% | 42.17% | 72.79% | -31.81% |
| ALL_CANDIDATES | T+5 | 35704 | 42.49% | -0.55% | -0.87% | 42.49% | 128.68% | -39.23% |
| ALL_CANDIDATES | T+10 | 34587 | 42.35% | -1.38% | -1.49% | 42.35% | 104.79% | -55.29% |
| ALL_CANDIDATES | T+20 | 29915 | 50.87% | -0.69% | 0.32% | 50.87% | 179.41% | -62.58% |
| ALL_CANDIDATES | T+60 | 8272 | 44.96% | -2.39% | -1.66% | 44.96% | 183.14% | -63.81% |
| HVT_EVENT | T+1 | 229 | 38.43% | -0.47% | -0.76% | 38.43% | 13.97% | -14.89% |
| HVT_EVENT | T+3 | 229 | 41.92% | -0.92% | -1.04% | 41.92% | 33.15% | -27.07% |
| HVT_EVENT | T+5 | 229 | 38.86% | -1.13% | -2.02% | 38.86% | 61.11% | -34.61% |
| HVT_EVENT | T+10 | 218 | 42.66% | -1.90% | -2.18% | 42.66% | 43.11% | -38.72% |
| HVT_EVENT | T+20 | 197 | 29.44% | -7.11% | -5.89% | 29.44% | 34.90% | -54.81% |
| HVT_EVENT | T+60 | 78 | 16.67% | -12.19% | -13.18% | 16.67% | 106.21% | -63.77% |
| HVT_ADJUSTING | T+1 | 975 | 43.69% | -0.22% | -0.47% | 43.69% | 20.00% | -20.00% |
| HVT_ADJUSTING | T+3 | 974 | 43.22% | -0.44% | -0.96% | 43.22% | 44.57% | -27.12% |
| HVT_ADJUSTING | T+5 | 972 | 41.05% | -0.70% | -1.69% | 41.05% | 49.62% | -33.70% |
| HVT_ADJUSTING | T+10 | 933 | 35.16% | -3.47% | -4.32% | 35.16% | 85.55% | -55.29% |
| HVT_ADJUSTING | T+20 | 785 | 34.52% | -5.96% | -6.22% | 34.52% | 140.49% | -62.58% |
| HVT_ADJUSTING | T+60 | 181 | 15.47% | -13.98% | -15.50% | 15.47% | 120.52% | -63.77% |
| HVT_LOCKING | T+1 | 78 | 39.74% | -0.70% | -0.65% | 39.74% | 10.10% | -9.96% |
| HVT_LOCKING | T+3 | 76 | 43.42% | -0.55% | -1.24% | 43.42% | 11.99% | -14.23% |
| HVT_LOCKING | T+5 | 76 | 42.11% | -0.90% | -2.00% | 42.11% | 21.49% | -17.08% |
| HVT_LOCKING | T+10 | 71 | 23.94% | -5.13% | -5.22% | 23.94% | 19.64% | -50.63% |
| HVT_LOCKING | T+20 | 63 | 34.92% | -6.81% | -3.82% | 34.92% | 34.32% | -60.42% |
| HVT_LOCKING | T+60 | 16 | 12.50% | -14.14% | -10.79% | 12.50% | 9.03% | -59.26% |
| HVT_REBREAKOUT | T+1 | 0 | NA | NA | NA | NA | NA | NA |
| HVT_REBREAKOUT | T+3 | 0 | NA | NA | NA | NA | NA | NA |
| HVT_REBREAKOUT | T+5 | 0 | NA | NA | NA | NA | NA | NA |
| HVT_REBREAKOUT | T+10 | 0 | NA | NA | NA | NA | NA | NA |
| HVT_REBREAKOUT | T+20 | 0 | NA | NA | NA | NA | NA | NA |
| HVT_REBREAKOUT | T+60 | 0 | NA | NA | NA | NA | NA | NA |
| RETEST_SUCCESS | T+1 | 197 | 37.06% | -0.95% | -1.15% | 37.06% | 13.32% | -16.28% |
| RETEST_SUCCESS | T+3 | 197 | 26.90% | -2.68% | -3.60% | 26.90% | 25.09% | -19.68% |
| RETEST_SUCCESS | T+5 | 197 | 27.41% | -3.52% | -4.20% | 27.41% | 52.24% | -27.00% |
| RETEST_SUCCESS | T+10 | 186 | 20.43% | -8.14% | -8.15% | 20.43% | 72.62% | -43.89% |
| RETEST_SUCCESS | T+20 | 160 | 20.00% | -13.30% | -11.61% | 20.00% | 24.96% | -47.20% |
| RETEST_SUCCESS | T+60 | 69 | 24.64% | -16.32% | -21.13% | 24.64% | 30.46% | -63.23% |
| BREAKOUT_CONFIRMED | T+1 | 609 | 44.83% | -0.21% | -0.50% | 44.83% | 20.00% | -16.28% |
| BREAKOUT_CONFIRMED | T+3 | 609 | 42.36% | -0.18% | -1.16% | 42.36% | 51.77% | -26.09% |
| BREAKOUT_CONFIRMED | T+5 | 609 | 41.71% | -0.28% | -1.73% | 41.71% | 52.24% | -31.16% |
| BREAKOUT_CONFIRMED | T+10 | 592 | 34.12% | -4.57% | -5.66% | 34.12% | 72.62% | -43.89% |
| BREAKOUT_CONFIRMED | T+20 | 515 | 25.24% | -12.30% | -11.93% | 25.24% | 50.87% | -57.38% |
| BREAKOUT_CONFIRMED | T+60 | 193 | 7.77% | -22.57% | -23.54% | 7.77% | 15.04% | -63.23% |

## 2. HVT 核心假设对照（§三十五）

| group_a | group_b | horizon | n_a | n_b | mean_a | mean_b | mean_a - mean_b |
|---|---|---|---|---|---|---|---|
| HVT_EVENT | ALL_CANDIDATES | T+1 | 229 | 35910 | -0.47% | -0.14% | -0.33% |
| HVT_EVENT | ALL_CANDIDATES | T+3 | 229 | 35722 | -0.92% | -0.54% | -0.37% |
| HVT_EVENT | ALL_CANDIDATES | T+5 | 229 | 35704 | -1.13% | -0.55% | -0.58% |
| HVT_EVENT | ALL_CANDIDATES | T+10 | 218 | 34587 | -1.90% | -1.38% | -0.51% |
| HVT_EVENT | ALL_CANDIDATES | T+20 | 197 | 29915 | -7.11% | -0.69% | -6.42% |
| HVT_EVENT | ALL_CANDIDATES | T+60 | 78 | 8272 | -12.19% | -2.39% | -9.80% |
| HVT_LOCKING | HVT_EVENT | T+1 | 78 | 229 | -0.70% | -0.47% | -0.23% |
| HVT_LOCKING | HVT_EVENT | T+3 | 76 | 229 | -0.55% | -0.92% | 0.37% |
| HVT_LOCKING | HVT_EVENT | T+5 | 76 | 229 | -0.90% | -1.13% | 0.23% |
| HVT_LOCKING | HVT_EVENT | T+10 | 71 | 218 | -5.13% | -1.90% | -3.23% |
| HVT_LOCKING | HVT_EVENT | T+20 | 63 | 197 | -6.81% | -7.11% | 0.30% |
| HVT_LOCKING | HVT_EVENT | T+60 | 16 | 78 | -14.14% | -12.19% | -1.95% |
| HVT_REBREAKOUT | HVT_LOCKING | T+1 | 0 | 78 | NA | -0.70% | NA |
| HVT_REBREAKOUT | HVT_LOCKING | T+3 | 0 | 76 | NA | -0.55% | NA |
| HVT_REBREAKOUT | HVT_LOCKING | T+5 | 0 | 76 | NA | -0.90% | NA |
| HVT_REBREAKOUT | HVT_LOCKING | T+10 | 0 | 71 | NA | -5.13% | NA |
| HVT_REBREAKOUT | HVT_LOCKING | T+20 | 0 | 63 | NA | -6.81% | NA |
| HVT_REBREAKOUT | HVT_LOCKING | T+60 | 0 | 16 | NA | -14.14% | NA |
| RETEST_SUCCESS | BREAKOUT_CONFIRMED | T+1 | 197 | 609 | -0.95% | -0.21% | -0.73% |
| RETEST_SUCCESS | BREAKOUT_CONFIRMED | T+3 | 197 | 609 | -2.68% | -0.18% | -2.49% |
| RETEST_SUCCESS | BREAKOUT_CONFIRMED | T+5 | 197 | 609 | -3.52% | -0.28% | -3.24% |
| RETEST_SUCCESS | BREAKOUT_CONFIRMED | T+10 | 186 | 592 | -8.14% | -4.57% | -3.58% |
| RETEST_SUCCESS | BREAKOUT_CONFIRMED | T+20 | 160 | 515 | -13.30% | -12.30% | -1.00% |
| RETEST_SUCCESS | BREAKOUT_CONFIRMED | T+60 | 69 | 193 | -16.32% | -22.57% | 6.24% |

> 目标不是证明某个结构一定上涨，而是检查结构逐步完成后未来收益分布是否发生稳定、可重复的变化。

## 3. 结构质量分层（§四十九）

| structure_quality | horizon | n | mean_return | median_return | win_rate |
|---|---|---|---|---|---|
| 90-100 | T+5 | 0 | NA | NA | NA |
| 90-100 | T+10 | 0 | NA | NA | NA |
| 90-100 | T+20 | 0 | NA | NA | NA |
| 80-90 | T+5 | 762 | -0.37% | -0.56% | 42.91% |
| 80-90 | T+10 | 679 | -2.05% | -2.10% | 34.17% |
| 80-90 | T+20 | 280 | -7.03% | -4.25% | 29.64% |
| 70-80 | T+5 | 7226 | -1.04% | -0.99% | 39.79% |
| 70-80 | T+10 | 6696 | -3.48% | -2.90% | 31.50% |
| 70-80 | T+20 | 4383 | -5.01% | -2.28% | 39.49% |
| 60-70 | T+5 | 6969 | -1.06% | -1.10% | 40.58% |
| 60-70 | T+10 | 6695 | -2.83% | -2.43% | 37.92% |
| 60-70 | T+20 | 5276 | -1.88% | -0.57% | 47.01% |
| 0-60 | T+5 | 21213 | -0.24% | -0.76% | 43.94% |
| 0-60 | T+10 | 20961 | -0.33% | -0.56% | 47.16% |
| 0-60 | T+20 | 20316 | 0.47% | 1.28% | 54.19% |

> 分层区间只用于观察，不得事后选择最有效区间作为正式阈值。

## 4. Robustness（§五十）

- 抽样 300 只股票；观察对象 HVT_POOL(HVT_LOCKING,HVT_REBREAKOUT)，持有期 T+20；最小样本量 20
- 基准池均收益 -18.85%（n=8）；全候选基准均收益 -1.39%（n=2601）
- 判定：轻微扰动后池均收益方向反转 → OVERFIT_RISK = NONE（方向反转 0 / 12）

| perturbation | path | n_base | n_pert | mean_base | mean_pert | diff | sign_flip | status |
|---|---|---|---|---|---|---|---|---|
| structure_threshold_-5 | qualification.qualified_structure_quality_min | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| structure_threshold_+5 | qualification.qualified_structure_quality_min | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| conditional_threshold_-5 | qualification.conditional_structure_quality_min | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| conditional_threshold_+5 | qualification.conditional_structure_quality_min | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| hvt_volume_-10pct | hvt.prefilter_tratio_min | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| hvt_volume_+10pct | hvt.prefilter_tratio_min | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| consolidation_window_-3 | consolidation.max_days | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| consolidation_window_+3 | consolidation.max_days | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| breakout_window_-3 | breakout.lookback_days | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| breakout_window_+3 | breakout.lookback_days | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| extension_threshold_-5pct | extension.dist_ma20_ref | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |
| extension_threshold_+5pct | extension.dist_ma20_ref | 8 | 8 | -18.85% | -18.85% | 0.00% | NO | STABLE |

- 方向反转项：0 / 12
- **OVERFIT_RISK = NONE**
