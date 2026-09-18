# Step 7 Execution / Trade Decision Layer 审计报告

- 最新交易日：20260917
- 决策行数：37257
- BUY：39　NO TRADE：37218
- FINAL STATUS：**CONDITIONAL_READY**
- 判定原因：LIMITATION：上游 Step 6 落盘数据使 RETEST_ENTRY / BREAKOUT_ENTRY+REBREAKOUT_ENTRY 结构性不可达 BUY（其行全部 qualification ∉ ['CONDITIONAL', 'QUALIFIED']），本层不得绕过 Step 6 资格门（§六十二 Step6 bypass = NOT_READY），当前 BUY 仅来自 PULLBACK_ENTRY

> 三条永久交易原则（§五十二 / §四）：

> 1. 巨量日不追，只等回踩。
> 2. 回踩触发价不破低吸。
> 3. 收盘跌回关键结构位，交易假设失效（BREAKOUT ≠ BUY）。

---

## 1. 输入

| 输入 | 路径 | 存在 |
|---|---|---|
| Step 5 候选 | data\sector_stock_candidate_daily.csv | True |
| Step 6 结构骨架 | data\stock_structure_daily.csv | True |
| Step 6 结构评分 | data\stock_structure_score.csv | True |
| Step 6 平台 | data\stock_consolidation.csv | True |
| Step 6 突破 | data\stock_breakout.csv | True |
| Step 6 回踩 | data\stock_retest.csv | True |
| Step 6 HVT 历史 | data\stock_hvt_history.csv | True |
| 板块层状态历史 | data\sector_state_history.csv | True |
| 板块层日统计 | data\sector_daily_stats.csv | True |
| Tushare 缓存库 | D:\mystock\cache_daily\stock_data.db | True |

- 历史锚点：20240801（来源：structure_hvt_config.json::input.hist_start_date（与 Step 6 保持同一锚点，避免重复建设数据层））
- ATR 口径：SMA（窗口 20） —— SMA = TR 的 20 日简单均值，与 Step 6 stock_structure_hvt_build.py::_atr 完全一致（已核对源码，Step 6 使用的并非 Wilder）。WILDER 为可选替代口径（tr.ewm(alpha=1/20)），切换后与 Step 6 的 atr_dist / extension 口径产生差异，必须在 output/execution_audit.md 中记录。
- 摆动点定义：ROLLING_WINDOW_EXTREME（高 20 日 / 低 10 日）
- 价格口径：内部结构计算沿用 Step 6 的后复权口径（adj = close × adj_factor）；对外输出（trigger / entry / stop / target / close / ma20 / atr20）一律折算为不复权价（RAW = adj ÷ adj_factor(D)），保证可直接下单。字段 price_basis 与 adj_factor 同步落盘。

### 1.1 只读补算量（Step 6 未落盘）

| 结构量 | 口径 | 说明 |
|---|---|---|
| atr20 | SMA ATR（含当日） | 与 Step 6 `_atr` 逐字一致，保证 extension / atr_dist 可比 |
| high_20/60/120/250 | 滚动最高后复权价（含当日） | 阻力位与 PRIOR_HIGH 来源 |
| recent_swing_high | 20 日最高（含当日） | trigger 的 SWING_HIGH 来源 |
| recent_swing_low | 10 日最低（含当日） | stop 的 SWING_LOW 来源 |
| trigger_date / stop_date | 极值发生日回算（窗口含当日） | 严格 PIT |

---

## 2. 市场状态

| trade_date | regime | execution_mode | regime_score | sector_n | STRONG+HEALTHY 占比 | WEAK+DETERIORATING 占比 | breadth | hard_risk |
|---|---|---|---|---|---|---|---|---|
| 20260814 | NEUTRAL | NORMAL | 65.0 | 50 | 40.00% | 14.00% | 41.54% | False |
| 20260817 | HEALTHY | NORMAL | 85.0 | 50 | 30.00% | 12.00% | 75.65% | False |
| 20260818 | NEUTRAL | NORMAL | 65.0 | 50 | 32.00% | 12.00% | 33.89% | False |
| 20260819 | RISK_OFF | WAIT | 0.0 | 50 | 26.00% | 26.00% | 9.24% | True |
| 20260820 | NEUTRAL | NORMAL | 65.0 | 50 | 28.00% | 26.00% | 76.37% | False |
| 20260821 | NEUTRAL | NORMAL | 65.0 | 50 | 18.00% | 20.00% | 42.21% | False |
| 20260824 | NEUTRAL | NORMAL | 65.0 | 50 | 18.00% | 24.00% | 27.26% | False |
| 20260825 | NEUTRAL | NORMAL | 65.0 | 50 | 20.00% | 24.00% | 77.45% | False |
| 20260826 | HEALTHY | NORMAL | 85.0 | 50 | 40.00% | 10.00% | 55.66% | False |
| 20260827 | HEALTHY | NORMAL | 85.0 | 50 | 44.00% | 8.00% | 61.15% | False |
| 20260828 | HEALTHY | NORMAL | 85.0 | 50 | 42.00% | 10.00% | 53.61% | False |
| 20260831 | HEALTHY | NORMAL | 85.0 | 50 | 40.00% | 14.00% | 53.95% | False |
| 20260901 | HEALTHY | NORMAL | 85.0 | 50 | 40.00% | 14.00% | 63.83% | False |
| 20260902 | RISK_OFF | WAIT | 0.0 | 50 | 34.00% | 20.00% | 21.48% | True |
| 20260903 | NEUTRAL | NORMAL | 65.0 | 50 | 16.00% | 44.00% | 34.13% | False |
| 20260904 | NEUTRAL | NORMAL | 65.0 | 50 | 8.00% | 44.00% | 47.12% | False |
| 20260907 | NEUTRAL | NORMAL | 65.0 | 50 | 14.00% | 28.00% | 54.52% | False |
| 20260908 | NEUTRAL | NORMAL | 65.0 | 50 | 24.00% | 24.00% | 67.02% | False |
| 20260909 | NEUTRAL | NORMAL | 65.0 | 50 | 30.00% | 20.00% | 33.00% | False |
| 20260910 | RISK_OFF | WAIT | 0.0 | 50 | 18.00% | 44.00% | 18.71% | True |
| 20260911 | RISK_OFF | WAIT | 0.0 | 50 | 6.00% | 68.00% | 10.59% | True |
| 20260914 | WEAK | DEFENSIVE | 35.0 | 50 | 6.00% | 68.00% | 58.40% | False |
| 20260915 | RISK_OFF | WAIT | 0.0 | 50 | 6.00% | 66.00% | 17.58% | True |
| 20260916 | WEAK | DEFENSIVE | 35.0 | 50 | 6.00% | 68.00% | 72.40% | False |
| 20260917 | NEUTRAL | NORMAL | 65.0 | 50 | 20.00% | 18.00% | 45.92% | False |

- 状态枚举：['STRONG', 'HEALTHY', 'NEUTRAL', 'WEAK', 'RISK_OFF']
- 映射：{'STRONG': 'STRONG', 'HEALTHY': 'HEALTHY', 'NEUTRAL': 'NEUTRAL', 'WEAK': 'WEAK', 'DETERIORATING': 'RISK_OFF', 'DATA_INVALID': 'NEUTRAL'}
- 数据源：STEP2_3_4_SECTOR_LAYER（['data/sector_state_history.csv', 'data/sector_daily_stats.csv', 'output/sector_state_today.json']）
- hard-risk 档：['RISK_OFF']

---

## 3. BUY 数量

- BUY = **39**；NO TRADE = 37218；决策行数 = 37257

| trade_date | ts_code | name | entry_state | entry_type | execution_score | execution_grade | trigger_price | entry_low | entry_high | stop_price | target_1 | target_2 | risk_reward | extension_risk | market_regime |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20260825 | 000426.SZ | 兴业银锡 | PULLBACK_ENTRY | PULLBACK | 78.9 | CONDITIONAL_BUY | 43.020 | 43.020 | 44.230 | 34.370 | 74.800 | 75.550 | 3.10 | LOW | NEUTRAL |
| 20260826 | 000426.SZ | 兴业银锡 | PULLBACK_ENTRY | PULLBACK | 81.8 | CONDITIONAL_BUY | 43.020 | 43.020 | 44.240 | 34.370 | 74.800 | 75.550 | 3.10 | LOW | HEALTHY |
| 20260827 | 000426.SZ | 兴业银锡 | PULLBACK_ENTRY | PULLBACK | 82.2 | CONDITIONAL_BUY | 42.900 | 42.900 | 44.100 | 34.290 | 74.600 | 75.340 | 3.11 | LOW | HEALTHY |
| 20260828 | 000426.SZ | 兴业银锡 | PULLBACK_ENTRY | PULLBACK | 82.3 | CONDITIONAL_BUY | 42.900 | 42.900 | 44.100 | 34.280 | 74.600 | 75.340 | 3.11 | LOW | HEALTHY |
| 20260831 | 000426.SZ | 兴业银锡 | PULLBACK_ENTRY | PULLBACK | 81.6 | CONDITIONAL_BUY | 42.900 | 42.900 | 44.170 | 34.210 | 74.600 | 75.340 | 3.06 | LOW | HEALTHY |
| 20260723 | 000568.SZ | 泸州老窖 | PULLBACK_ENTRY | PULLBACK | 76.4 | CONDITIONAL_BUY | 88.460 | 88.460 | 90.050 | 72.440 | 144.280 | 145.730 | 3.08 | LOW | WEAK |
| 20260722 | 000596.SZ | 古井贡酒 | PULLBACK_ENTRY | PULLBACK | 71.2 | CONDITIONAL_BUY | 98.610 | 98.610 | 100.690 | 69.260 | 169.080 | 170.770 | 2.18 | LOW | WEAK |
| 20260903 | 000965.SZ | 天保基建 | PULLBACK_ENTRY | PULLBACK | 76.6 | CONDITIONAL_BUY | 3.620 | 3.620 | 3.680 | 3.030 | 4.860 | 4.910 | 1.82 | LOW | NEUTRAL |
| 20260904 | 000965.SZ | 天保基建 | PULLBACK_ENTRY | PULLBACK | 76.7 | CONDITIONAL_BUY | 3.620 | 3.620 | 3.680 | 3.040 | 4.860 | 4.910 | 1.84 | LOW | NEUTRAL |
| 20260831 | 002155.SZ | 湖南黄金 | PULLBACK_ENTRY | PULLBACK | 74.9 | CONDITIONAL_BUY | 29.540 | 29.540 | 30.190 | 23.500 | 43.100 | 43.530 | 1.93 | LOW | HEALTHY |
| 20260831 | 002295.SZ | 精艺股份 | PULLBACK_ENTRY | PULLBACK | 77.0 | CONDITIONAL_BUY | 10.980 | 10.980 | 11.170 | 8.490 | 17.440 | 17.610 | 2.34 | LOW | HEALTHY |
| 20260814 | 002365.SZ | 永安药业 | PULLBACK_ENTRY | PULLBACK | 80.7 | CONDITIONAL_BUY | 14.200 | 14.200 | 14.550 | 12.030 | 22.510 | 22.740 | 3.16 | LOW | NEUTRAL |
| 20260917 | 300183.SZ | 东软载波 | PULLBACK_ENTRY | PULLBACK | 78.9 | CONDITIONAL_BUY | 11.790 | 11.790 | 11.980 | 10.040 | 18.280 | 18.460 | 3.26 | LOW | NEUTRAL |
| 20260702 | 300203.SZ | 聚光科技 | PULLBACK_ENTRY | PULLBACK | 76.1 | CONDITIONAL_BUY | 15.450 | 15.450 | 15.900 | 12.440 | 22.590 | 22.820 | 1.94 | LOW | NEUTRAL |
| 20260706 | 300347.SZ | 泰格医药 | PULLBACK_ENTRY | PULLBACK | 73.3 | CONDITIONAL_BUY | 50.660 | 50.660 | 52.090 | 39.140 | 71.900 | 72.620 | 1.53 | LOW | NEUTRAL |
| 20260917 | 300563.SZ | 神宇股份 | PULLBACK_ENTRY | PULLBACK | 79.1 | CONDITIONAL_BUY | 29.980 | 29.980 | 30.840 | 21.950 | 51.160 | 51.670 | 2.28 | LOW | NEUTRAL |
| 20260720 | 300683.SZ | 海特生物 | PULLBACK_ENTRY | PULLBACK | 77.9 | CONDITIONAL_BUY | 31.680 | 31.680 | 32.840 | 22.400 | 61.760 | 62.380 | 2.77 | LOW | NEUTRAL |
| 20260522 | 301000.SZ | 肇民科技 | PULLBACK_ENTRY | PULLBACK | 75.3 | CONDITIONAL_BUY | 40.500 | 40.500 | 41.620 | 33.690 | 58.400 | 58.980 | 2.11 | LOW | WEAK |
| 20260525 | 301000.SZ | 肇民科技 | PULLBACK_ENTRY | PULLBACK | 78.4 | CONDITIONAL_BUY | 40.500 | 40.500 | 41.650 | 33.790 | 58.400 | 58.980 | 2.13 | LOW | NEUTRAL |
| 20260526 | 301000.SZ | 肇民科技 | PULLBACK_ENTRY | PULLBACK | 75.7 | CONDITIONAL_BUY | 40.500 | 40.500 | 41.700 | 33.740 | 58.400 | 58.980 | 2.10 | LOW | NEUTRAL |
| 20260917 | 600104.SH | 上汽集团 | PULLBACK_ENTRY | PULLBACK | 80.7 | CONDITIONAL_BUY | 11.890 | 11.890 | 12.010 | 10.240 | 18.870 | 19.060 | 3.88 | LOW | NEUTRAL |
| 20260723 | 600199.SH | 金种子酒 | PULLBACK_ENTRY | PULLBACK | 74.9 | CONDITIONAL_BUY | 7.410 | 7.410 | 7.610 | 5.830 | 11.750 | 11.870 | 2.33 | LOW | WEAK |
| 20260831 | 600362.SH | 江西铜业 | PULLBACK_ENTRY | PULLBACK | 79.9 | CONDITIONAL_BUY | 49.980 | 49.980 | 51.170 | 42.400 | 69.690 | 70.390 | 2.11 | LOW | HEALTHY |
| 20260709 | 600521.SH | 华海药业 | PULLBACK_ENTRY | PULLBACK | 75.7 | CONDITIONAL_BUY | 16.950 | 16.950 | 17.260 | 13.420 | 27.770 | 28.050 | 2.74 | LOW | WEAK |
| 20260710 | 600521.SH | 华海药业 | PULLBACK_ENTRY | PULLBACK | 76.5 | CONDITIONAL_BUY | 17.070 | 17.070 | 17.400 | 13.400 | 27.770 | 28.050 | 2.60 | LOW | NEUTRAL |
| 20260904 | 600604.SH | 市北高新 | PULLBACK_ENTRY | PULLBACK | 75.4 | CONDITIONAL_BUY | 5.350 | 5.350 | 5.500 | 4.400 | 7.180 | 7.250 | 1.52 | LOW | NEUTRAL |
| 20260908 | 600604.SH | 市北高新 | PULLBACK_ENTRY | PULLBACK | 77.6 | CONDITIONAL_BUY | 5.350 | 5.350 | 5.500 | 4.410 | 7.180 | 7.250 | 1.56 | LOW | NEUTRAL |
| 20260909 | 600604.SH | 市北高新 | PULLBACK_ENTRY | PULLBACK | 76.7 | CONDITIONAL_BUY | 5.350 | 5.350 | 5.490 | 4.550 | 7.180 | 7.250 | 1.81 | LOW | NEUTRAL |
| 20260826 | 600649.SH | 城投控股 | PULLBACK_ENTRY | PULLBACK | 81.4 | CONDITIONAL_BUY | 4.160 | 4.160 | 4.220 | 3.610 | 5.710 | 5.770 | 2.42 | LOW | HEALTHY |
| 20260827 | 600649.SH | 城投控股 | PULLBACK_ENTRY | PULLBACK | 81.4 | CONDITIONAL_BUY | 4.160 | 4.160 | 4.220 | 3.610 | 5.710 | 5.770 | 2.42 | LOW | HEALTHY |
| 20260828 | 600649.SH | 城投控股 | PULLBACK_ENTRY | PULLBACK | 77.6 | CONDITIONAL_BUY | 4.160 | 4.160 | 4.230 | 3.600 | 5.710 | 5.770 | 2.39 | LOW | HEALTHY |
| 20260831 | 600649.SH | 城投控股 | PULLBACK_ENTRY | PULLBACK | 76.3 | CONDITIONAL_BUY | 4.180 | 4.180 | 4.250 | 3.620 | 5.710 | 5.770 | 2.30 | LOW | HEALTHY |
| 20260831 | 601212.SH | 白银有色 | PULLBACK_ENTRY | PULLBACK | 81.5 | CONDITIONAL_BUY | 7.680 | 7.680 | 7.900 | 5.160 | 15.190 | 15.340 | 2.66 | LOW | HEALTHY |
| 20260703 | 603313.SH | 梦百合 | PULLBACK_ENTRY | PULLBACK | 78.6 | CONDITIONAL_BUY | 6.320 | 6.320 | 6.480 | 4.860 | 10.770 | 10.880 | 2.65 | LOW | NEUTRAL |
| 20260706 | 603313.SH | 梦百合 | PULLBACK_ENTRY | PULLBACK | 78.6 | CONDITIONAL_BUY | 6.320 | 6.320 | 6.480 | 4.860 | 10.770 | 10.880 | 2.65 | LOW | NEUTRAL |
| 20260831 | 603737.SH | 三棵树 | PULLBACK_ENTRY | PULLBACK | 78.8 | CONDITIONAL_BUY | 27.290 | 27.290 | 27.810 | 23.210 | 48.560 | 49.040 | 4.52 | LOW | HEALTHY |
| 20260908 | 688091.SH | 上海谊众 | PULLBACK_ENTRY | PULLBACK | 79.3 | CONDITIONAL_BUY | 43.980 | 43.980 | 45.150 | 35.340 | 74.100 | 74.840 | 2.95 | LOW | NEUTRAL |
| 20260909 | 688091.SH | 上海谊众 | PULLBACK_ENTRY | PULLBACK | 79.3 | CONDITIONAL_BUY | 43.980 | 43.980 | 45.140 | 35.350 | 74.100 | 74.840 | 2.96 | LOW | NEUTRAL |
| 20260917 | 688395.SH | 正弦电气 | PULLBACK_ENTRY | PULLBACK | 81.5 | CONDITIONAL_BUY | 24.130 | 24.130 | 24.510 | 21.530 | 33.140 | 33.470 | 2.89 | LOW | NEUTRAL |

### 3.1 BUY 来源分布

| entry_state | count |
|---|---|
| PULLBACK_ENTRY | 39 |

| execution_grade | count |
|---|---|
| CONDITIONAL_BUY | 39 |

| stop_source | count |
|---|---|
| SWING_LOW | 39 |

---

## 4. NO TRADE 原因

| primary_no_trade_reason | count |
|---|---|
| CANDIDATE_INVALID | 30061 |
| STRUCTURE_WEAK | 5523 |
| MARKET_RISK | 956 |
| HVT_NOT_MATURE | 502 |
| RR_TOO_LOW | 137 |
| BREAKOUT_NOT_CONFIRMED | 39 |

### 4.1 硬门命中次数（一行可命中多道门）

| gate | count |
|---|---|
| NO_ENTRY_STATE | 36576 |
| STRUCTURE_WEAK | 36332 |
| CANDIDATE_INVALID | 30061 |
| HVT_NOT_MATURE | 18288 |
| RR_TOO_LOW | 16716 |
| MARKET_RISK | 7163 |
| FAILED_STRUCTURE | 6584 |
| BREAKOUT_NOT_CONFIRMED | 2835 |
| TRIGGER_BROKEN | 389 |
| VOLUME_ABNORMAL | 86 |
| EXTENSION_TOO_HIGH | 7 |

### 4.2 entry_state 分布（全量）

| entry_state | count |
|---|---|
| NO_ENTRY | 27121 |
| FAILED_NO_ENTRY | 6584 |
| WAIT_CONFIRMATION | 2835 |
| PULLBACK_ENTRY | 323 |
| RETEST_ENTRY | 207 |
| BREAKOUT_ENTRY | 120 |
| REBREAKOUT_ENTRY | 67 |

---

## 5. HVT

| hvt_state | count |
|---|---|
| HVT_NONE | 18567 |
| HVT_FAILED | 17375 |
| HVT_ADJUSTING | 997 |
| HVT_EVENT | 235 |
| HVT_LOCKING | 83 |

- HVT 门规则：{"HVT_LOCKING": 80.0, "HVT_ADJUSTING": 60.0, "HVT_EVENT": 30.0, "HVT_NONE": 20.0, "HVT_FAILED": 0.0}
- HVT_EVENT 不可直接 BUY：event_state_buyable = False
- HVT_LOCKING 质量下限：60.0
- HVT_ADJUSTING 需突破确认：['BREAKOUT_CONFIRMED', 'REBREAKOUT_CONFIRMED']
- 豁免 entry_state：['PULLBACK_ENTRY']
  - 规格 §十六 / §二十八-3 对 PULLBACK BUY 列出的必要条件为「PULLBACK_HEALTHY + support defense + volume contraction + theme opportunity + extension acceptable + RR acceptable」，其中不含 HVT 成熟度。Step 6 落盘数据中 PULLBACK_HEALTHY 行 100% 为 hvt_state = HVT_ADJUSTING 且 breakout_state = NO_BREAKOUT，若对 PULLBACK_ENTRY 施加 require_breakout_states 会使其全量被拦（该路径将永久为 0 BUY）。因此 PULLBACK_ENTRY 豁免 HVT 成熟度门，HVT 仍以 state_score 参与 execution_score 打分。

---

## 6. Backtest

详见 [execution_backtest.md](execution_backtest.md)。

| group | n | T+1 mean | T+3 mean | T+5 mean | T+10 mean | T+20 mean |
|---|---|---|---|---|---|---|
| ALL_STEP5_CANDIDATES | 37257 | -0.14% | -0.54% | -0.55% | -1.38% | -0.69% |
| STEP6_QUALIFIED | 399 | -0.27% | -0.43% | -1.38% | -5.05% | -8.73% |
| BREAKOUT_BUY | 0 | NA | NA | NA | NA | NA |
| RETEST_BUY | 0 | NA | NA | NA | NA | NA |
| PULLBACK_BUY | 39 | -0.59% | -0.26% | -0.48% | -2.49% | -1.99% |

- Calibration：transform=IDENTITY，apply_to_decision=False，BUY=39，CALIBRATED 行=5269
- Robustness：OVERFIT RISK = NONE，base BUY = 39，触发阈值 = []

---

## 7. Anti-Chasing（§五十五）

| item | value | expected | status |
|---|---|---|---|
| extreme_buy_count | 0 | 0 | PASS |
| extreme_buy_rate | 0.0 | <=0.02 | PASS |
| extreme_vs_all_rate_ratio | 0.0 | <=0.5 | PASS |
| extreme_vs_healthy_rate_ratio | 0.0 | <=0.5 | PASS |
| healthy_buy_count | 2 | >=1 | PASS |
| healthy_buy_rate | 0.000449 | 信息项 | PASS |
| no_chase_volume_rows | 0 | >=0 | PASS |

---

## 8. Future Leakage（§五十四 C）

| item | value | expected | status | detail |
|---|---|---|---|---|
| sample_size | 60 | <=60 | PASS | 随机抽样（seed 固定，可复现） |
| forward_feature_columns | [] | [] | PASS | 决策输入列全部来自 signal_date <= D 的滚动窗口，无任何前向列参与 |
| future_price_used | 0 | 0 | PASS | 行情序列仅使用 trade_date <= D 的行；T+1/3/5/10/20 仅出现在 execution_backtest.md 的 outcome 段 |
| future_fundamental_used | 0 | 0 | PASS | 本层不读取任何财务数据 |
| future_volume_used | 0 | 0 | PASS | 成交量只取 <= D 的日线量 |
| non_finite_core_fields | 0 | 0 | PASS | 核心结构量在样本中必须全部有限；异常： |

---

## 9. Legacy 隔离（§六十六）

- legacy_reads = []
- legacy_config_used = False
- 读取文件数 = 11
- 策略：不 import、不调用、不覆盖 hvt_bull / w7_second_wave_engine / trade_execution_engine / market_regime_v3；仅在本文件与 output/execution_audit.md 中记录口径借鉴来源。

---

## 10. PIT（§五十四 D）

| item | value | expected | status | detail |
|---|---|---|---|---|
| membership_pit | NOT_APPLICABLE | NOT_APPLICABLE | PASS | Step 7 不读取成员关系表；成分股口径完全继承 Step 5 落盘结果 |
| rows_after_signal_date | 0 | 0 | PASS | 决策行必须全部 <= 最新交易日 20260917 |
| market_rows_positive | 0 | 0 | PASS | 每行必须有 >0 的历史行情行数（含当日），保证窗口只回看 |
| calibration_history_before_signal | ok | ok | PASS | calibration transform=IDENTITY，apply_to_decision=False，historical_execution_quality 只用 signal_date < D 的 BUY 样本 |

---

## 11. Robustness（§四十五）

| perturbation | base_buy | perturbed_buy | change_pct | status |
|---|---|---|---|---|
| execution_threshold_-5 | 39 | 39 | 0.00% | OK |
| execution_threshold_+5 | 39 | 39 | 0.00% | OK |
| rr_threshold_-0.25 | 39 | 53 | 35.90% | OK |
| rr_threshold_+0.25 | 39 | 36 | 7.69% | OK |
| extension_threshold_-5pct | 39 | 39 | 0.00% | OK |
| extension_threshold_+5pct | 39 | 39 | 0.00% | OK |
| trigger_buffer_-0.5atr | 39 | 48 | 23.08% | OK |
| trigger_buffer_+0.5atr | 39 | 36 | 7.69% | OK |

- 判定阈值：BUY 数量变化 > 50.00% → OVERFIT_RISK
- 结论：OVERFIT RISK = **NONE**

---

## 12. 验证汇总（§五十四）

| check_id | PASS | FAIL |
|---|---|---|
| CHECK_ANTI_CHASING | 7 | 0 |
| CHECK_BREAKOUT | 4 | 0 |
| CHECK_BUY_GATE | 9 | 0 |
| CHECK_DEPENDENCY | 12 | 0 |
| CHECK_FUTURE_LEAKAGE | 6 | 0 |
| CHECK_LEGACY | 3 | 0 |
| CHECK_PIT | 4 | 0 |
| CHECK_RETEST | 5 | 0 |
| CHECK_RR | 5 | 0 |

---

## 13. 层级边界与冲突记录（§六十六）

| # | CONFLICT | OLD LOGIC | NEW LOGIC | RECOMMENDED ADAPTER |
|---|---|---|---|---|
| 01 | 执行层三套并存 | `hvt_bull/trade_execution.py`（trigger=ev.entry or pbl，invalidation=max(t0_high×0.95, trigger−1.2×ATR14)）；`trade_execution_engine.py`（STOP_K=0.97 / STOP_ATR=2.2 / BUY_BAND=0.985~1.015）；`w7_second_wave_engine.py` | Step 7 独立执行层：trigger 取真实结构位，stop = 结构支撑 − 0.5×ATR20 | 不合并旧引擎；旧引擎只作口径借鉴，不 import / 不调用 / 不覆盖 |
| 02 | Market Regime 四套枚举 | risk_on/neutral/weak/risk_off/panic；Bear/Recovery/Neutral/Bull/Euphoria；BULL/RECOVERY/BEAR/RANGE；数值 0/1/2 | STRONG/HEALTHY/NEUTRAL/WEAK/RISK_OFF，由 Step 2/3/4 板块层状态分布与广度聚合 | 不新增市场状态模块；只读板块层落盘产物 |
| 03 | ATR 口径三套 | ATR14 简单均值（trade_execution_engine）；1.2×ATR14 / 2.2×ATR14 止损 k 值 | SMA ATR20（TR 的 20 日简单均值），与 Step 6 `_atr` 逐字一致；缓冲 0.5×ATR20 写入 config | 以 Step 6 为唯一基准，保证两层结构量可比；WILDER 作为可选口径保留在 config |
| 04 | 确认态终局归属（**新增·实测**） | Step 6 的 HVT 生命周期在突破/回踩确认后进入 HVT_FAILED → structure_state=FAILED → qualification=REJECTED | Step 7 要求BREAKOUT_ENTRY / RETEST_ENTRY / REBREAKOUT_ENTRY 才可 BUY | 不绕过 Step 6 资格门（§六十二 Step6 bypass = NOT_READY）；如实记录为 LIMITATION，BUY 仅来自 PULLBACK_ENTRY（实测：RETEST_ENTRY 207 行、BREAKOUT_ENTRY 120 行、REBREAKOUT_ENTRY 67 行 **全部** structure_qualification=REJECTED，故 0 行能进入 BUY；PULLBACK_ENTRY 323 行中 39 行通过全部门）；FINAL STATUS 因此为 CONDITIONAL_READY（§六十二）而非 READY_FOR_LIVE_EXECUTION；**需上游决策**：是否在 Step 6 为确认态保留独立资格档 |
| 05 | 止损宽度上限（**新增·实测·本层已裁决**） | 规格 §十八 / §十九 只规定 stop 的算法（结构支撑 − ATR buffer），**全文未出现止损宽度上限**；旧执行引擎存在固定百分比止损习惯 | 首版曾把 `max_stop_distance_pct=0.12` 接成硬门 STOP_TOO_WIDE，导致 78 个交易日 BUY 恒为 0（PULLBACK 的 stop 锚点只能是 10 日摆动低点，宽度中位 22%） | 已改为 `warn_stop_distance_pct`，只输出风险提示（risk_note 中的 WIDE_WARN）与审计计数，不参与 BUY 判定；止损一侧质量把关交回§二十一 Risk/ Reward 的 minimum。改动后 BUY 由 0 → 39（仍属少而精）。**若需恢复硬门**：把该键改回硬门并同步 config.no_trade_reason 的 reasons / precedence / gate_to_reason 三处，且须接受 BUY 恒为 0 的后果 |
| 06 | Anti-Chasing 健康结构下限（**新增·实测·本层已裁决**） | 规格 §四十一 只给出三组区间（极端 = ret_5>=30% 且 dist_ma20>=30%；健康 = ret_5∈[2%,8%] 且 dist_ma20∈[0%,10%] 且 volume_ratio∈[1,2]），§五十五 只给出相对判定「极端 BUY 率必须显著低于健康 BUY 率」，健康一侧只要求「不能因为 anti-chasing 过强而全部被过滤」，**全文未出现任何绝对下限** | 首版有两处偏离：① 用 Step 6 的 `extension_risk=LOW`（37092/37257 = 99.6% 的样本）充当健康组，把健康 BUY 率稀释成 0.001 量级的伪低值；② 自设绝对下限 `anti_chasing_min_healthy_buy_rate=0.02` 并接成 FAIL 项 | 已按规格区间重写：极端/健康两组改用 config.validation 已声明的 §四十一 区间（ret_5 / dist_ma20 / volume_ratio），绝对下限改为规格原文的「不得被全部过滤」（`anti_chasing_min_healthy_buy_count=1`），并新增 `extreme_vs_healthy_rate_ratio <= anti_chasing_max_rate_ratio` 承担「显著低于」这条相对判定；`healthy_buy_rate` 降为信息项。实测：极端组 1 行 / BUY 0；健康组 4455 行 / BUY 2；极端组 BUY 率 0.0 <= 健康组 0.000449。**若需恢复绝对下限**：把 `anti_chasing_min_healthy_buy_count` 改回 rate 阈值并接受健康组被误判为 FAIL 的后果 |

### 13.1 层级边界变化

`output/sector_state_today.json` 的 `not_in_scope` 明确排除了「个股BUY/NO TRADE」与「交易执行」。Step 7 首次跨越该边界：板块层（Step 2/3/4）仍只输出状态，个股执行决策全部落在本层产物中，不改写板块层任何文件。

### 13.2 未落盘量映射

| 规格字段 | 实际来源 | 说明 |
|---|---|---|
| retest_low | stock_retest.retest_level | Step 6 未落盘 retest_low，按其唯一回踩价位锚点显式映射（config.stop.field_map_note） |
| support_1 / resistance_1 | Step 6 `support_resistance` 口径重算 | Step 6 未落盘，按同一公式（fmax(platform_low, ma20) / fmax(platform_high, high_20)）重算 |
| support_2 / resistance_2 | 未使用 | §十八 / §二十 的优先级只到 MA20 与 ATR buffer |

---

## 14. 收尾声明

- Step 7 completed.
- No position sizing / portfolio optimization / auto order / broker API generated.
- No legacy execution engine used (hvt_bull / w7 / trade_execution_engine / market_regime_v3).
- Step 8 NOT implemented.

- FINAL STATUS = **CONDITIONAL_READY**
- 原因：LIMITATION：上游 Step 6 落盘数据使 RETEST_ENTRY / BREAKOUT_ENTRY+REBREAKOUT_ENTRY 结构性不可达 BUY（其行全部 qualification ∉ ['CONDITIONAL', 'QUALIFIED']），本层不得绕过 Step 6 资格门（§六十二 Step6 bypass = NOT_READY），当前 BUY 仅来自 PULLBACK_ENTRY
