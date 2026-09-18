# Step 7 Execution 回测报告

未来收益仅作为 outcome；signal_date = D 的 BUY 只使用 <= D 的数据。

## 1. 分组样本量

| group | n |
|---|---|
| ALL_STEP5_CANDIDATES | 37257 |
| STEP6_QUALIFIED | 399 |
| BREAKOUT_BUY | 0 |
| RETEST_BUY | 0 |
| PULLBACK_BUY | 39 |

## 2. 各持有期表现（按组）

### T+1

| group | n | win_rate | mean | median | max_return | max_drawdown |
|---|---|---|---|---|---|---|
| ALL_STEP5_CANDIDATES | 35910 | 45.07% | -0.14% | -0.26% | 20.04% | NA |
| STEP6_QUALIFIED | 387 | 43.41% | -0.27% | -0.51% | 20.00% | NA |
| BREAKOUT_BUY | 0 | NA | NA | NA | NA | NA |
| RETEST_BUY | 0 | NA | NA | NA | NA | NA |
| PULLBACK_BUY | 35 | 34.29% | -0.59% | -1.19% | 10.05% | NA |

### T+3

| group | n | win_rate | mean | median | max_return | max_drawdown |
|---|---|---|---|---|---|---|
| ALL_STEP5_CANDIDATES | 35722 | 42.17% | -0.54% | -0.76% | 72.79% | NA |
| STEP6_QUALIFIED | 384 | 42.97% | -0.43% | -1.06% | 44.57% | NA |
| BREAKOUT_BUY | 0 | NA | NA | NA | NA | NA |
| RETEST_BUY | 0 | NA | NA | NA | NA | NA |
| PULLBACK_BUY | 35 | 45.71% | -0.26% | -0.18% | 21.40% | NA |

### T+5

| group | n | win_rate | mean | median | max_return | max_drawdown |
|---|---|---|---|---|---|---|
| ALL_STEP5_CANDIDATES | 35704 | 42.49% | -0.55% | -0.87% | 128.68% | -39.23% |
| STEP6_QUALIFIED | 384 | 39.32% | -1.38% | -1.99% | 48.82% | -27.37% |
| BREAKOUT_BUY | 0 | NA | NA | NA | NA | NA |
| RETEST_BUY | 0 | NA | NA | NA | NA | NA |
| PULLBACK_BUY | 35 | 34.29% | -0.48% | -4.10% | 33.61% | -15.33% |

### T+10

| group | n | win_rate | mean | median | max_return | max_drawdown |
|---|---|---|---|---|---|---|
| ALL_STEP5_CANDIDATES | 34587 | 42.35% | -1.38% | -1.49% | 104.79% | NA |
| STEP6_QUALIFIED | 360 | 28.33% | -5.05% | -5.25% | 46.62% | NA |
| BREAKOUT_BUY | 0 | NA | NA | NA | NA | NA |
| RETEST_BUY | 0 | NA | NA | NA | NA | NA |
| PULLBACK_BUY | 29 | 34.48% | -2.49% | -3.66% | 18.09% | NA |

### T+20

| group | n | win_rate | mean | median | max_return | max_drawdown |
|---|---|---|---|---|---|---|
| ALL_STEP5_CANDIDATES | 29915 | 50.87% | -0.69% | 0.32% | 179.41% | -62.58% |
| STEP6_QUALIFIED | 285 | 31.58% | -8.73% | -7.78% | 51.45% | -62.58% |
| BREAKOUT_BUY | 0 | NA | NA | NA | NA | NA |
| RETEST_BUY | 0 | NA | NA | NA | NA | NA |
| PULLBACK_BUY | 14 | 50.00% | -1.99% | -1.82% | 10.81% | -35.62% |

## 3. 组间对照（mean 收益差）

| group | baseline | T+1 | T+3 | T+5 | T+10 | T+20 |
|---|---|---|---|---|---|---|
| STEP6_QUALIFIED | ALL_STEP5_CANDIDATES | -0.13% | 0.12% | -0.83% | -3.67% | -8.04% |
| BREAKOUT_BUY | STEP6_QUALIFIED | NA | NA | NA | NA | NA |
| RETEST_BUY | BREAKOUT_BUY | NA | NA | NA | NA | NA |
| PULLBACK_BUY | BREAKOUT_BUY | NA | NA | NA | NA | NA |

重点持有期：[5, 20]

## 4. 按 market_regime 分解

| regime | n | T+1 | T+3 | T+5 | T+10 | T+20 |
|---|---|---|---|---|---|---|
| HEALTHY | 7500 | 0.27% | 0.27% | 0.81% | -1.49% | 1.60% |
| NEUTRAL | 20334 | -0.34% | -1.42% | -1.87% | -1.52% | -1.48% |
| RISK_OFF | 7163 | -0.12% | 0.56% | 0.94% | -1.44% | -0.45% |
| WEAK | 2260 | 0.15% | 0.67% | 1.50% | 0.38% | 0.43% |

## 5. Calibration

- transform = IDENTITY
- apply_to_decision = False
- minimum_sample = 20
- BUY 行数 = 39
- 历史 outcome 样本 = 35
- 达到 CALIBRATED 的行数 = 5269

样本不足时输出 CALIBRATION_STATUS = INSUFFICIENT，绝不伪造稳定胜率。

## 6. Robustness（参数扰动）

- base BUY = 39
- buy_count_change_max = 50.00%
- OVERFIT RISK = NONE
- 触发阈值 []

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
