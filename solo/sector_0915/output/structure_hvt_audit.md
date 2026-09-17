# Step 6 — Stock Structure / HVT Qualification Layer 审计报告

- 生成时间：2026-09-17 21:00:22
- 结构基准日：20260916
- legacy_config_used = false
- adapter 模式：REUSE_DEFINITION_TRANSCRIBE_VECTORIZED
- membership_version_status = STATIC_ONLY；BACKTEST_MEMBERSHIP_LIMITED = true
- **FINAL STATUS = CONDITIONAL_READY**（membership 为 STATIC_ONLY（BACKTEST_MEMBERSHIP_LIMITED=true），历史候选池不可完整复现）

## A. 数据覆盖

| item | value |
|---|---|
| candidate count（Step 5） | 37042 |
| stock count（Step 5） | 3557 |
| structure rows | 35910 |
| structure stock count | 3557 |
| date range | 20260522 → 20260916 |
| trading days covered | 77 |
| daily coverage（mean rows/day） | 466.4 |
| daily coverage（min/max rows/day） | 8 / 2201 |
| data_quality 非 VALID 行数 | 0 |
| HVT 事件数 | 20185 |

| data_quality | count | share |
|---|---|---|
| VALID | 35910 | 100.00% |

## B. Structure 状态

| structure_state | count | share |
|---|---|---|
| FAILED | 16784 | 46.74% |
| WATCH | 14737 | 41.04% |
| BASE | 3107 | 8.65% |
| ADJUSTING | 707 | 1.97% |
| BREAKOUT_READY | 268 | 0.75% |
| HVT | 229 | 0.64% |
| LOCKING | 78 | 0.22% |

| structure_class | count | share |
|---|---|---|
| FAILED | 16784 | 46.74% |
| WATCH | 14737 | 41.04% |
| STRUCTURE_BASE | 3107 | 8.65% |
| HVT_ADJUSTING | 393 | 1.09% |
| PULLBACK_HEALTHY | 314 | 0.87% |
| BREAKOUT_READY | 268 | 0.75% |
| HVT_EVENT | 229 | 0.64% |
| HVT_LOCKING | 78 | 0.22% |

| trend_state | count | share |
|---|---|---|
| DOWNTREND | 17658 | 49.17% |
| UPTREND_EARLY | 6179 | 17.21% |
| UPTREND | 6057 | 16.87% |
| CORRECTION | 4401 | 12.26% |
| SIDEWAYS | 1615 | 4.50% |

| price_zone | count | share |
|---|---|---|
| MID_RANGE | 33871 | 94.32% |
| BREAKOUT_ZONE | 1035 | 2.88% |
| NEAR_HIGH | 907 | 2.53% |
| EXTENDED | 97 | 0.27% |

| volume_state | count | share |
|---|---|---|
| HEALTHY_VOLUME | 24838 | 69.17% |
| VOLUME_CONTRACTION | 5245 | 14.61% |
| VOLUME_EXPANSION | 4135 | 11.51% |
| VOLUME_DIVERGENCE | 1608 | 4.48% |
| ABNORMAL_VOLUME | 84 | 0.23% |

| platform_quality | count | share |
|---|---|---|
| NO_PLATFORM | 29703 | 82.72% |
| PLATFORM_HEALTHY | 6024 | 16.78% |
| PLATFORM_STRONG | 183 | 0.51% |

## C. HVT 状态分布

| hvt_state | count | share |
|---|---|---|
| HVT_NONE | 17844 | 49.69% |
| HVT_FAILED | 16784 | 46.74% |
| HVT_ADJUSTING | 975 | 2.72% |
| HVT_EVENT | 229 | 0.64% |
| HVT_LOCKING | 78 | 0.22% |

| hvt_stage | count | share |
|---|---|---|
| HVT_NONE | 17844 | 49.69% |
| HVT_FAILED | 16784 | 46.74% |
| HVT_ADJUSTING | 944 | 2.63% |
| HVT_EVENT | 229 | 0.64% |
| HVT_REACCUMULATION | 78 | 0.22% |
| HVT_CONSOLIDATING | 31 | 0.09% |

| adjustment_status | count | share |
|---|---|---|
| NO_ADJUSTMENT | 18191 | 50.66% |
| FAILED_ADJUSTMENT | 14004 | 39.00% |
| DEEP_ADJUSTMENT | 1993 | 5.55% |
| HEALTHY_ADJUSTMENT | 1033 | 2.88% |
| SHALLOW_ADJUSTMENT | 689 | 1.92% |

| breakout_state | count | share |
|---|---|---|
| NO_BREAKOUT | 26437 | 73.62% |
| BREAKOUT_FAILED | 5984 | 16.66% |
| BREAKOUT_READY | 2746 | 7.65% |
| BREAKOUT_CONFIRMED | 609 | 1.70% |
| REBREAKOUT_CONFIRMED | 134 | 0.37% |

| retest_state | count | share |
|---|---|---|
| NO_RETEST | 29304 | 81.60% |
| RETEST_FAILED | 6346 | 17.67% |
| RETEST_SUCCESS | 197 | 0.55% |
| RETEST_PENDING | 63 | 0.18% |

| legacy_hvt_state | count | share |
|---|---|---|
| FAILED | 17213 | 47.93% |
| DISTRIBUTION | 12076 | 33.63% |
| HVT_STRONG | 2554 | 7.11% |
|  | 1586 | 4.42% |
| BREAKOUT_READY | 1045 | 2.91% |
| EXIT | 760 | 2.12% |
| HVT_DETECTED | 229 | 0.64% |
| LOCKING | 136 | 0.38% |
| LOCKED | 114 | 0.32% |
| EVENT_SPIKE | 88 | 0.25% |
| CONFIRMED | 74 | 0.21% |
| PRIMARY_BUY | 35 | 0.10% |

| legacy_hvt_state_normalized（adapter） | count | share |
|---|---|---|
| HVT_FAILED | 30049 | 83.68% |
| HVT_EVENT | 2871 | 7.99% |
|  | 1586 | 4.42% |
| HVT_REBREAKOUT | 1154 | 3.21% |
| HVT_LOCKING | 250 | 0.70% |

## D. Qualification

| structure_qualification | count | share |
|---|---|---|
| REJECTED | 16784 | 46.74% |
| WATCH | 12235 | 34.07% |
| CONDITIONAL | 6504 | 18.11% |
| QUALIFIED | 387 | 1.08% |

- QUALIFIED 行数：387
- 基准日 20260916 行数：178（结构池：HVT 0，Rebreakout 0，Retest 0）

reason 示例（结构化事实，不含任何主观判断词）：

- `000011.SZ` 20260805：结构状态 BREAKOUT_READY；结构分类 BREAKOUT_READY；HVT 后经历 5 个交易日演化；当前 HVT 状态 HVT_ADJUSTING；调整充分度 SHALLOW_ADJUSTMENT；近段成交量降至 HVT 日的 40%；锁筹分 66；支撑位置 above_s1；突破状态 BREAKOUT_READY；扩张风险 LOW（惩罚 0.7）；结构质量 72.0。
- `000011.SZ` 20260826：结构状态 BREAKOUT_READY；结构分类 BREAKOUT_READY；HVT 后经历 20 个交易日演化；当前 HVT 状态 HVT_ADJUSTING；调整充分度 SHALLOW_ADJUSTMENT；近段成交量降至 HVT 日的 26%；锁筹分 68；形成 15 日缩量平台（PLATFORM_HEALTHY）；支撑位置 above_s1_near；突破状态 BREAKOUT_READY；扩张风险 LOW（惩罚 0.0）；结构质量 85.0。
- `000011.SZ` 20260827：结构状态 BREAKOUT_READY；结构分类 BREAKOUT_READY；HVT 后经历 21 个交易日演化；当前 HVT 状态 HVT_ADJUSTING；调整充分度 SHALLOW_ADJUSTMENT；近段成交量降至 HVT 日的 26%；锁筹分 78；形成 20 日缩量平台（PLATFORM_HEALTHY）；支撑位置 above_s1_near；突破状态 BREAKOUT_READY；扩张风险 LOW（惩罚 0.0）；结构质量 85.0。
- `000011.SZ` 20260828：结构状态 LOCKING；结构分类 HVT_LOCKING；HVT 后经历 22 个交易日演化；当前 HVT 状态 HVT_LOCKING；调整充分度 SHALLOW_ADJUSTMENT；近段成交量降至 HVT 日的 26%；锁筹分 64；支撑位置 above_s1；突破状态 BREAKOUT_READY；扩张风险 LOW（惩罚 1.1）；结构质量 80.5。
- `000011.SZ` 20260904：结构状态 BREAKOUT_READY；结构分类 BREAKOUT_READY；HVT 后经历 27 个交易日演化；当前 HVT 状态 HVT_ADJUSTING；调整充分度 SHALLOW_ADJUSTMENT；近段成交量降至 HVT 日的 36%；锁筹分 58；支撑位置 above_s1_near；突破状态 BREAKOUT_READY；扩张风险 LOW（惩罚 0.0）；结构质量 75.5。

## E. Extension

| extension_risk | count | share |
|---|---|---|
| LOW | 35747 | 99.55% |
| MEDIUM | 158 | 0.44% |
| EXTREME | 5 | 0.01% |

| item | value |
|---|---|
| extension_penalty mean | 0.23 |
| extension_penalty max | 13.93 |
| extension_penalty = 0 行占比 | 78.27% |
| penalty 上限（config） | 20.0 |

| check | item | value | expected | status |
|---|---|---|---|---|
| CHECK_MOMENTUM_CHASING | extreme_momentum_n | 1 | >0 | PASS |
| CHECK_MOMENTUM_CHASING | healthy_structure_n | 4354 | >0 | PASS |
| CHECK_MOMENTUM_CHASING | structure_quality_gap | -0.4971 | <= 10.0 | PASS |
| CHECK_MOMENTUM_CHASING | extension_penalty_gap | 13.9055 | > 0 | PASS |

## F. Backtest（T+1 / T+3 / T+5 / T+10 / T+20 / T+60）

- 样本区间 20260522 → 20260916，观测 35910 行；重点持有期 T+20
- BACKTEST_MEMBERSHIP_LIMITED = true

单元格 = n / mean_return / win_rate

| group | T+1 | T+3 | T+5 | T+10 | T+20 | T+60 |
|---|---|---|---|---|---|---|
| ALL_CANDIDATES | 35910 / -0.14% / 45.07% | 35722 / -0.54% / 42.17% | 35704 / -0.55% / 42.49% | 34587 / -1.38% / 42.35% | 29915 / -0.69% / 50.87% | 8272 / -2.39% / 44.96% |
| HVT_EVENT | 229 / -0.47% / 38.43% | 229 / -0.92% / 41.92% | 229 / -1.13% / 38.86% | 218 / -1.90% / 42.66% | 197 / -7.11% / 29.44% | 78 / -12.19% / 16.67% |
| HVT_ADJUSTING | 975 / -0.22% / 43.69% | 974 / -0.44% / 43.22% | 972 / -0.70% / 41.05% | 933 / -3.47% / 35.16% | 785 / -5.96% / 34.52% | 181 / -13.98% / 15.47% |
| HVT_LOCKING | 78 / -0.70% / 39.74% | 76 / -0.55% / 43.42% | 76 / -0.90% / 42.11% | 71 / -5.13% / 23.94% | 63 / -6.81% / 34.92% | 16 / -14.14% / 12.50% |
| HVT_REBREAKOUT | 0 / NA / NA | 0 / NA / NA | 0 / NA / NA | 0 / NA / NA | 0 / NA / NA | 0 / NA / NA |
| RETEST_SUCCESS | 197 / -0.95% / 37.06% | 197 / -2.68% / 26.90% | 197 / -3.52% / 27.41% | 186 / -8.14% / 20.43% | 160 / -13.30% / 20.00% | 69 / -16.32% / 24.64% |
| BREAKOUT_CONFIRMED | 609 / -0.21% / 44.83% | 609 / -0.18% / 42.36% | 609 / -0.28% / 41.71% | 592 / -4.57% / 34.12% | 515 / -12.30% / 25.24% | 193 / -22.57% / 7.77% |

（完整明细见 output/structure_hvt_backtest.md，Robustness 见同文件第 4 节）

## G. Anti-Chasing（§二十一 / §二十八）


| item | value | expected | status |
|---|---|---|---|
| extreme_momentum_n | 1 | >0 | PASS |
| healthy_structure_n | 4354 | >0 | PASS |
| structure_quality_gap | -0.4971 | <= 10.0 | PASS |
| extension_penalty_gap | 13.9055 | > 0 | PASS |

| item | value | expected | status |
|---|---|---|---|
| extreme_extension_qualified | 0 | 0 | PASS |
| extreme_extension_capped_qualification | REJECTED,WATCH | WATCH / CONDITIONAL / REJECTED | PASS |
| heavy_breakout_qualified | 0 | 0 | PASS |

## H. Future Leakage


**PASS**

| item | value | expected | status |
|---|---|---|---|
| forward_return used in feature | 0 | 0 | PASS |
| future_high used in feature | 0 | 0 | PASS |
| future_low used in feature | 0 | 0 | PASS |
| future_volume used in feature | 0 | 0 | PASS |
| future_fundamental used | 0 | 0 | PASS |
| truncation_fields_compared | 1868 | >0 | PASS |
| truncation_mismatch | 0 | 0 | PASS |

## I. Legacy


**PASS**

| item | value | expected | status |
|---|---|---|---|
| legacy_reads | [] | [] | PASS |
| legacy_config_used | false | false | PASS |
| files_opened | 7 | >0 | PASS |

## J. Membership PIT


**LIMITED**

- membership_version_status = `STATIC_ONLY`
- BACKTEST_MEMBERSHIP_LIMITED = `true`

| item | value | expected | status |
|---|---|---|---|
| membership effective_date > trade_date | 0 | 0 | PASS |
| membership_version_status | STATIC_ONLY | VERSIONED / PARTIAL / STATIC_ONLY(+BACKTEST_MEMBERSHIP_LIMITED=true) | PASS |
| hvt_signal_uses_future_data | 0 | 0 | PASS |

## K. 验证项汇总（§四十七）

| check_id | check_name | items | fail | result |
|---|---|---|---|---|
| CHECK_DEPENDENCY | 上游依赖完整 | 6 | 0 | PASS |
| CHECK_LEGACY | legacy 主题配置隔离 | 3 | 0 | PASS |
| CHECK_FUTURE_LEAKAGE | 未来数据泄漏 | 7 | 0 | PASS |
| CHECK_PIT | PIT 一致性 | 3 | 0 | PASS |
| CHECK_MOMENTUM_CHASING | 结构质量与涨幅解耦 | 4 | 0 | PASS |
| CHECK_HVT_ADAPTER_EQUIVALENCE | legacy HVT adapter 等价性 | 5 | 0 | PASS |
| CHECK_HVT_TRANSITIONS | HVT 状态机完整性 | 2 | 0 | PASS |
| CHECK_BREAKOUT_ANTI_CHASING | 突破不等于追涨 | 3 | 0 | PASS |
| CHECK_FAILED_BREAKOUT | 失败结构识别 | 3 | 0 | PASS |

## L. 交易原则保留（§五十四）

- 巨量日不追，只等回踩。
- 回踩触发位不破。
- 收盘跌回关键突破位 / 结构位后，结构失效。

> trigger / stop / position / BUY 属于 Step 7；Step 6 不输出最终交易指令。

## M. 禁止事项自检（§五十八）

| item | value | status |
|---|---|---|
| 结构 CSV 交易指令词命中（Step 6 自有列） | [] | PASS |
| legacy_* 透传列命中（§五十三 逐字转写白名单，不计入判定） | [] | INFO |
| BUY / NO TRADE 输出 | none | PASS |
| 仓位 / 买入金额 / 止损执行 / 交易指令 | none | PASS |
| 重新做主题映射 / SEOS / candidate selection | none | PASS |
| legacy theme config | none（legacy_config_used=false） | PASS |
| 未来数据进入 signal / score / qualification | none | PASS |

## N. 最终状态（§五十七）


```text
FINAL STATUS = CONDITIONAL_READY
membership 为 STATIC_ONLY（BACKTEST_MEMBERSHIP_LIMITED=true），历史候选池不可完整复现
```


```text
Step 6 completed.
No BUY / NO TRADE decision generated.
No position sizing / stop execution / trade instruction generated.
No legacy theme configuration used.
Step 7 Execution NOT implemented.
```
