# H-TAIL-MIDBAR-01 — 最终报告

尾盘惯性「第一根 ≥3% 中阳线 → 次日卖出」科研验证  
Research ID: `H-TAIL-MIDBAR-01` · Version 1.0 · 2026-10-01  
SPEC SHA256: `396329014C3233DC7BB31AA0B981CD57`（运行前冻结，运行期逐次校验）  
样本区间 20211108 .. 20260924 · IS 2022–2024 · Validation 2025 · OOS 2026  
报告生成于 2026-10-01 08:58:08 · trading_authorization = **NO**

```
══════════════════════════════════════════════
H-TAIL-MIDBAR-01

Primary Event:
First ≥3% bullish mid-bar
+ MA5/MA20 structure
+ Volume Gate

Entry:
T Close

Exit:
T+1 Close

Cost:
30bp

──────────────────────────────────────────────
Mean Net30:
-0.008%

Win Rate:
41.778%

PF30:
0.993

Leave Top 5%:
-0.557%

OOS:
-0.373%

Bootstrap:
[-0.247%, 0.345%]  p(<=0)=0.5720

──────────────────────────────────────────────
MA Incremental Alpha:
UNPROVEN

Volume Incremental Alpha:
UNPROVEN

First-event Incremental Alpha:
UNPROVEN

Overall:
FAIL → ARCHIVE
══════════════════════════════════════════════
```

## §2 数据边界声明（必须先读）

> 本研究验证的是「收盘中阳线 → 次日收益」，而不是严格意义上的「尾盘惯性」。日线数据无法还原 14:30–14:57 的真实尾盘时点，本项目**不做**这种伪装。

对 `cache_daily/` 全目录做文件名模式扫描（`*min*` / `*5m*` / `*15m*` / `*60m*` / `*1min*`）：**零命中**（本次运行前重新核实）。
因此本实验属于任务书 §2 情形 A —— **日线代理实验**：

    ret_t = close_t / close_{t-1} - 1 >= 3%   作为「尾盘中阳线」的日线代理

`body_t = (close-open)/open`、`clv_t = (close-low)/(high-low)` 均为日线量。
本报告**不主张**任何 14:30–14:57 的真实尾盘结论。

## §3 样本与宇宙

| 项 | 值 |
|---|---|
| 面板 | S=5816 只股票 × N=1187 个交易日（20180102 .. 20260924） |
| 研究窗口 | 20211108 .. 20260924（含 40 会话 burn-in） |
| 宇宙 | `hve_common.eligibility(g) & board != 'BSE'` |
| 窗口内 eligible cell | 5,381,548 |
| 裸事件 E0(3%) | 409,695 |
| PRIMARY ARM 事件（含 T+1 可成交） | 49,090 |
| 价格口径 | 收益用 qfq OHLC；涨幅 `ret_t` 用 `pct_chg`（原始口径） |

## §4 核心实验矩阵（任务书 §7）

| arm | 定义 | N | Mean | Mean Net30 | Win30 | PF30 | Δnet30 vs 父 | 95% CI(Δ) |
|---|---|---|---|---|---|---|---|---|
| RAW | RAW (E0 3%) | 409,165 | 0.0038 | 0.077% | 44.239% | 1.049 | — | — |
| +F | B = First(F2) | 145,104 | 0.0027 | -0.030% | 42.776% | 0.976 | -0.0011 | [-0.0033, 0.0006] |
| +F+MA | B + MA | 77,332 | 0.0031 | 0.012% | 42.113% | 1.009 | 0.0004 | [-0.0014, 0.0023] |
| +F+V | B + VOL | 77,626 | 0.0033 | 0.026% | 43.196% | 1.021 | 0.0006 | [-0.0001, 0.0012] |
| +F+MA+V | B + MA + VOL  [PRIMARY] | 49,052 | 0.0029 | -0.008% | 41.778% | 0.993 | -0.0002 | [-0.0005, 0.0001] |
| +F+MA+V | B + MA + VOL  [PRIMARY]  [entry OPEN_T1] | 49,052 | 0.0009 | -0.207% | 41.894% | 0.830 | — | — |

`PRIMARY ARM = B + MA + VOL = First(10) & MApresent & V5`。非 PRIMARY 的臂全部照报，不做事后择优。

## §5 Exit 诊断与第二执行模型（任务书 §8 / §9）

只有 `T+1 Close` 是正式策略结果。T+1 Open / High / Low 仅作诊断：

| arm | Mean(T+1 Close) | Mean(T+1 Open) | Mean(T+1 High) | Mean(T+1 Low) | share(High>Close) |
|---|---|---|---|---|---|
| RAW | 0.377% | 0.236% | 3.376% | -2.417% | 85.369% |
| +F | 0.270% | 0.150% | 2.692% | -1.979% | 85.730% |
| +F+MA | 0.312% | 0.258% | 2.849% | -2.003% | 84.032% |
| +F+V | 0.326% | 0.152% | 2.679% | -1.895% | 85.902% |
| +F+MA+V | 0.292% | 0.202% | 2.681% | -1.897% | 85.076% |

第二执行模型（`Entry = open[t+1]`，`Exit = close[t+1]`，即 T+1 开盘买、T+1 收盘卖）：

| 模型 | N | Mean Net30 | Win30 | PF30 |
|---|---|---|---|---|
| Entry close[t] → Exit close[t+1]（PRIMARY） | 49,052 | -0.008% | 41.778% | 0.993 |
| Entry open[t+1] → Exit close[t+1]（第二模型） | 49,052 | -0.207% | 41.894% | 0.830 |

全程未使用 T 日任何盘中价作为成交价（见 §15 bias audit #4）。

## §6 净成本阶梯（任务书 §10，30bp 为 Primary）

| 成本 | 0bp | 10bp | 20bp | 30bp | 50bp |
|---|---|---|---|---|---|
| PRIMARY Mean | 0.292% | 0.192% | 0.092% | -0.008% | -0.208% |
| B (First only) Mean | 0.270% | 0.170% | 0.070% | -0.030% | -0.230% |
| RAW Mean | 0.377% | 0.277% | 0.177% | 0.077% | -0.123% |

PRIMARY：**Mean Net30 = -0.008% / Win Rate = 41.778% / PF30 = 0.993**

## §7 MA5 / MA20 结构分层（任务书 §5 / §12）

| 结构 | N | 占基底 | Mean Net30 | Win30 | PF30 | Δnet30 vs First(F2) | 95% CI |
|---|---|---|---|---|---|---|---|
| ALL | 145,104 | 99.910% | -0.030% | 42.776% | 0.976 | — | — |
| M1 | 52,233 | 35.965% | 0.022% | 40.999% | 1.016 | 0.0005 | [-0.0018, 0.0033] |
| M2 | 42,707 | 29.406% | 0.045% | 40.712% | 1.032 | 0.0008 | [-0.0021, 0.0044] |
| M3 | 40,996 | 28.228% | 0.036% | 40.809% | 1.025 | 0.0007 | [-0.0020, 0.0039] |
| M4 | 35,908 | 24.724% | -0.010% | 43.628% | 0.991 | 0.0002 | [-0.0011, 0.0013] |
| MApresent | 77,332 | 53.246% | 0.012% | 42.113% | 1.009 | 0.0004 | [-0.0014, 0.0023] |

M4（低位启动）按预注册保留、未删除。MA 只作离散 Gate，未进入任何评分。

## §8 量能阀门分层（任务书 §6 / §12）

| 阀门 | 规则 | N | 占基底 | Mean Net30 | Win30 | PF30 | Δnet30 vs First(F2) | 95% CI |
|---|---|---|---|---|---|---|---|---|
| V0 | none | 145,104 | 99.910% | -0.030% | 42.776% | 0.976 | — | — |
| V1 | VR20>=1.20 | 94,977 | 65.396% | 0.033% | 42.855% | 1.025 | 0.0006 | [-0.0002, 0.0016] |
| V2 | VR20>=1.50 | 69,504 | 47.857% | 0.075% | 42.763% | 1.056 | 0.0011 | [-0.0002, 0.0026] |
| V3 | VR20>=2.00 | 41,394 | 28.502% | 0.072% | 41.786% | 1.049 | 0.0010 | [-0.0008, 0.0034] |
| V4 | VR20>=3.00 | 17,351 | 11.947% | 0.062% | 41.329% | 1.037 | 0.0009 | [-0.0015, 0.0039] |
| V5 | 1.20<=VR20<=3.00 | 77,626 | 53.449% | 0.026% | 43.196% | 1.021 | 0.0006 | [-0.0001, 0.0012] |

VR20 分母不含当日（shift 1）；停牌日 NaN。V5 = `1.20 <= VR20 <= 3.00` 为 Primary（预注册，非结果驱动）。

## §9 阈值轴：3% 是否真的有意义（任务书 §13）

| 阈值 | 基底 | N | Mean | Mean Net30 | Win30 | PF30 |
|---|---|---|---|---|---|---|
| 2% | RAW | 462,511 | 0.0033 | 0.028% | 43.960% | 1.018 |
| 2% | FULL | 47,191 | 0.0028 | -0.017% | 41.828% | 0.986 |
| 3% | RAW | 409,165 | 0.0038 | 0.077% | 44.239% | 1.049 |
| 3% | FULL | 49,052 | 0.0029 | -0.008% | 41.778% | 0.993 |
| 4% | RAW | 298,744 | 0.0051 | 0.212% | 45.329% | 1.130 |
| 4% | FULL | 47,000 | 0.0044 | 0.139% | 43.238% | 1.104 |
| 5% | RAW | 213,271 | 0.0068 | 0.381% | 46.677% | 1.222 |
| 5% | FULL | 40,903 | 0.0062 | 0.316% | 44.806% | 1.219 |
| 6% | RAW | 156,365 | 0.0087 | 0.566% | 48.043% | 1.315 |
| 6% | FULL | 33,865 | 0.0081 | 0.511% | 46.381% | 1.334 |
| 7% | RAW | 118,546 | 0.0110 | 0.798% | 49.945% | 1.434 |
| 7% | FULL | 27,856 | 0.0104 | 0.735% | 48.474% | 1.466 |

Spearman(阈值, Net30)：裸事件 RAW = **1.000**，全栈 = **1.000** → `MONOTONIC`

单调性检验只用于回答「次日惯性是否随当日涨幅存在稳定关系」，**不用于挑选最优阈值**；3% 始终是 Primary Hypothesis。

## §10 Leave-Tail Test（任务书 §11，最重要）

| arm | Full(Net30) | Leave Top1% | Leave Top5% | Leave Top10% | Top1% 贡献 | Top5% 贡献 | Median(Net30) |
|---|---|---|---|---|---|---|---|
| RAW | 0.077% | -0.105% | -0.539% | -0.975% | 10.413% | 34.153% | -0.430% |
| +F | -0.030% | -0.178% | -0.561% | -0.872% | 10.829% | 37.727% | -0.442% |
| +F+MA | 0.012% | -0.151% | -0.565% | -0.915% | 11.226% | 38.514% | -0.478% |
| +F+V | 0.026% | -0.121% | -0.498% | -0.807% | 10.743% | 37.254% | -0.393% |
| +F+MA+V | -0.008% | -0.165% | -0.557% | -0.872% | 11.602% | 39.376% | -0.458% |

判定规则（预注册）：`Full > 0 且 Leave Top 5% <= 0` → `TAIL_DEPENDENT = TRUE`，不得称为 Robust Alpha。

PRIMARY：Full(Net30) = -0.008%，Leave Top5%(Net30) = -0.557% → `TAIL_DEPENDENT(net30) = FALSE`；毛口径 `TAIL_DEPENDENT = TRUE`。

Top 5% 利润贡献 = 39.376%（毛口径）。

## §11 Null Models（任务书 §14）

| 家族 | 观测 Mean | Null Mean | Null 2.5% | Null 97.5% | Excess | 单侧 p | resolution | 采纳 |
|---|---|---|---|---|---|---|---|---|
| N1_STOCK_DAY | 0.0029 | 0.0021 | 0.0019 | 0.0024 | 0.0008 | 0.0000 | 1.0000 | Y |
| N2_STOCK_RANDOM_DAY | 0.0029 | 0.0005 | 0.0002 | 0.0007 | 0.0024 | 0.0000 | 1.0000 | Y |
| N3_STOCK_STATE_DAY | 0.0029 | 0.0014 | 0.0011 | 0.0018 | 0.0015 | 0.0000 | 0.8292 | N |
| N4_RANDOM_UP_BAR | 0.0029 | 0.0022 | 0.0019 | 0.0026 | 0.0007 | 0.0000 | 1.0000 | Y |

未采纳家族：N3_STOCK_STATE_DAY（预注册规则：`resolution >= 0.99` 才采纳；这些家族在 `rounds=8` 内无法为每个事件找到「非自身」的同格供体，其原因与后果已在 bias audit #8 披露）。

## §12 Bootstrap（任务书 §19）

| arm | 方法 | N | 簇数 | Mean Net30 | 95% CI | 单侧 p(<=0) |
|---|---|---|---|---|---|---|
| RAW | monthly_cluster | 409,165 | 57 | 0.077% | [-0.237%, 0.498%] | 0.4010 |
| RAW | block_20_sessions | 409,582 | 20480 | 0.077% | [0.063%, 0.091%] | nan |
| +F | monthly_cluster | 145,104 | 57 | -0.030% | [-0.240%, 0.231%] | 0.6190 |
| +F | block_20_sessions | 145,234 | 7262 | -0.030% | [-0.048%, -0.012%] | nan |
| +F+MA | monthly_cluster | 77,332 | 57 | 0.012% | [-0.225%, 0.356%] | 0.5110 |
| +F+MA | block_20_sessions | 77,398 | 3870 | 0.012% | [-0.013%, 0.038%] | nan |
| +F+V | monthly_cluster | 77,626 | 57 | 0.026% | [-0.212%, 0.312%] | 0.4520 |
| +F+V | block_20_sessions | 77,698 | 3885 | 0.026% | [0.003%, 0.051%] | nan |
| +F+MA+V | monthly_cluster | 49,052 | 57 | -0.008% | [-0.247%, 0.345%] | 0.5720 |
| +F+MA+V | block_20_sessions | 49,090 | 2455 | -0.008% | [-0.040%, 0.025%] | nan |

主口径为 `monthly_cluster`（同一股票连续信号、同一市场环境下信号高度相关，不能把每日信号当独立样本）；`block_20_sessions` 仅作稳健性对照，不用于判决。

## §13 OOS / Walk-Forward / 年度稳定性（任务书 §17 / §18）

| arm | 区间 | N | Mean Net30 | Win30 | PF30 |
|---|---|---|---|---|---|
| RAW | FULL | 409,165 | 0.077% | 44.239% | 1.049 |
| RAW | IS | 251,566 | 0.218% | 45.584% | 1.147 |
| RAW | VALID | 80,572 | -0.007% | 42.324% | 0.995 |
| RAW | OOS | 77,027 | -0.297% | 41.853% | 0.837 |
| +F | FULL | 145,104 | -0.030% | 42.776% | 0.976 |
| +F | IS | 90,276 | 0.031% | 43.684% | 1.025 |
| +F | VALID | 31,064 | 0.106% | 43.208% | 1.091 |
| +F | OOS | 23,764 | -0.441% | 38.765% | 0.724 |
| +F+MA | FULL | 77,332 | 0.012% | 42.113% | 1.009 |
| +F+MA | IS | 46,083 | 0.083% | 42.959% | 1.065 |
| +F+MA | VALID | 19,314 | 0.062% | 41.788% | 1.050 |
| +F+MA | OOS | 11,935 | -0.340% | 39.372% | 0.786 |
| +F+V | FULL | 77,626 | 0.026% | 43.196% | 1.021 |
| +F+V | IS | 48,314 | 0.099% | 44.022% | 1.085 |
| +F+V | VALID | 16,899 | 0.133% | 43.659% | 1.119 |
| +F+V | OOS | 12,413 | -0.404% | 39.346% | 0.742 |
| +F+MA+V | FULL | 49,052 | -0.008% | 41.778% | 0.993 |
| +F+MA+V | IS | 28,993 | 0.065% | 42.638% | 1.055 |
| +F+MA+V | VALID | 12,293 | 0.050% | 41.349% | 1.043 |
| +F+MA+V | OOS | 7,766 | -0.373% | 39.248% | 0.760 |

PRIMARY ARM 逐年：

| 年份 | N | Mean | Median | Win Rate | PF30 | Mean Net30 |
|---|---|---|---|---|---|---|
| 2022 | 8,876 | 0.0008 | -0.0030 | 44.491% | 0.832 | -0.223% |
| 2023 | 9,633 | 0.0016 | -0.0009 | 47.265% | 0.878 | -0.136% |
| 2024 | 10,484 | 0.0079 | 0.0000 | 49.723% | 1.433 | 0.493% |
| 2025 | 12,293 | 0.0035 | -0.0016 | 46.132% | 1.043 | 0.050% |
| 2026 | 7,766 | -0.0007 | -0.0045 | 42.866% | 0.760 | -0.373% |

Walk-forward（冻结配置、严格时间切分、无参数拟合）：

| fold | N(test) | Mean Net30(test) | 95% CI |
|---|---|---|---|
| train_2022_2023__test_2024 | 10,484 | 0.493% | [-0.484%, 1.762%] |
| train_2022_2024__test_2025 | 12,293 | 0.050% | [-0.064%, 0.153%] |
| train_2022_2025__test_2026 | 7,766 | -0.373% | [-0.590%, -0.209%] |

样本切分严格按交易日：IS 2022–2024 / Validation 2025 / OOS 2026；无随机 Train-Test、无时间打乱、无未来数据选参。

## §14 市场状态描述（任务书 §16，不用于任何参数）

| Regime | 会话数 | PRIMARY 事件 | Mean Net30 | CSI300 上涨比例 | 市场上涨股比例 |
|---|---|---|---|---|---|
| ALL | 1147 | 49,052 | -0.008% | 48.82% | 47.65% |
| BULL | 277 | 13,739 | 0.198% | 57.76% | 50.53% |
| RANGE | 563 | 24,155 | -0.059% | 49.38% | 47.74% |
| BEAR | 307 | 11,158 | -0.153% | 39.74% | 44.89% |

该表只用于回答「该模式是否只在某一种市场环境下有效」，市场状态未参与任何过滤或参数选择。

## §15 Overlap / Independence Audit（任务书 §20）

| arm | N | 相邻日信号对比例 | <=5 日比例 | <=20 日比例 | 每事件平均重叠对数 | n_eff |
|---|---|---|---|---|---|---|
| RAW | 409,582 | 0.272% | 1.297% | 4.801% | 4.321 | 76971.9 |
| +F | 145,234 | 0.000% | 0.000% | 1.928% | 0.560 | 93092.6 |
| +F+MA+V | 49,090 | 0.000% | 0.000% | 2.027% | 0.200 | 40898.6 |

存在同股信号聚集时，**不得把 N 当作独立样本数**；n_eff 仅供参照，判决以月度聚类 bootstrap 为准。

## §16 参数稳定性（任务书 §22，仅预注册扰动）

| 项 | 值 |
|---|---|
| 格点数 | 450（有效 450） |
| 网格 positive_ratio（Net30 > 0） | 0.873 |
| 3% 邻域（窗口=10）positive_ratio | 0.760（25 格） |
| 网格 argmax | thr=7% / 窗口=20 / VR=3.0 / MA=M3（Net30 = 1.606%, n=12,475） |
| PRIMARY 在网格中的分位 | 0.122（PRIMARY 本身不是格点） |
| 各轴 Spearman | thr=1.000 / window=1.000 / vr=0.000 / ma=0.000 |
| 判定 | parameter_stable=TRUE / parameter_fragile=FALSE |

**注意**：网格 argmax 高于 PRIMARY，说明「从网格里挑一格」会显著改善结果 —— 这正是本实验拒绝做的事。PRIMARY 在任何结果出来之前就已冻结。

## §17 Selection Bias Audit（任务书 §21）

| # | 项目 | 判定 | 证据 |
|---|---|---|---|
| 1 | 1_look_ahead | PASS | none |
| 2 | 2_selection_bias | PASS | grid argmax = thr=7%/w=20/vr=3.0/ma=M3 |
| 3 | 3_survivorship_bias | FLAG | n_codes_gone=242, of which 183 carry a PRIMARY-arm event |
| 4 | 4_execution_bias | PASS | max /stored r - close[t+1]/close[t]-1/ = 5.96e-08 |
| 5 | 5_overlapping_sample | PASS | n=49090, adjacent_rate=0.0000, rate<=5d=0.0000, rate<=20d=0.0203, n_eff=40898.6 |
| 6 | 6_future_information | PASS | backward N=148531 vs forward N=150713 |
| 7 | 7_parameter_leakage | PASS | spec_sha256=396329014C3233DC, spec_mtime=1790815252, log_mtime=1790816277, primary_percentile=0.122 |
| 8 | 8_randomization_leakage | FLAG | pool-out rate=0.17084, null_std min=0.000120 |

- **1_look_ahead**（PASS）：masks recomputed on a panel truncated by 120 sessions are bit-identical over 1067 shared sessions (14 masks)
- **2_selection_bias**（PASS）：the PRIMARY configuration (thr=3%, First(10), V5 band, MApresent union) is not a point of the pre-registered 450-cell grid and was fixed in the SPEC before any result; its net30 sits at grid percentile 0.122
- **3_survivorship_bias**（FLAG）：the Tushare cache carries no point-in-time delisting flag (delist_status=DELIST_PIT_UNAVAILABLE); the universe is ST- and name-based only.  Stocks whose last print falls >60 sessions before the panel end are reported, not silently dropped
- **4_execution_bias**（PASS）：entry = close[t], exit = close[t+1]; no T-day intraday price is ever used as a fill (open[t], low[t], high[t], vwap[t] are unused).  The stored leg is float32, so the comparison is made on the float32 grid and the tolerance is one float32 ulp
- **5_overlapping_sample**（PASS）：same-stock signal clustering inside 20 sessions; N is NOT treated as the independent sample size
- **6_future_information**（PASS）：the "first" window is BACKWARD-looking only ([t-10, t-1]); the deliberately forward-looking variant produces a different event count, which proves the backward rule is the one in use
- **7_parameter_leakage**（PASS）：the SPEC was written and hashed before this run started (its mtime precedes the run log), the run re-verifies the SPEC hash at start-up, and the primary configuration is not the grid argmax
- **8_randomization_leakage**（FLAG）：matched resampling: the only forbidden cell is the event itself; each replicate draws once per event; the null spread is finite and non-degenerate

说明：`FLAG` 表示已识别但无法在现有数据条件下彻底消除的偏差，按预注册规则（G11）必须披露；`FAIL` 不存在（G11 要求）。

## §18 核心 PASS / FAIL Gate（任务书 §23）

| Gate | 判据 | 结果 | 实际值 |
|---|---|---|---|
| G1 | Primary Net30 > 0 | **FAIL** | Primary Arm mean Net30 = -0.008% |
| G2 | Primary PF30 > 1 | **FAIL** | Primary Arm PF30 = 0.993 |
| G3 | bootstrap 95% CI (net30) 不跨 0 | **FAIL** | monthly-cluster 95% CI (net30) = [-0.247%, 0.345%], p(<=0) = 0.5720 |
| G4 | OOS(2026) Net30 > 0 | **FAIL** | OOS(2026) Net30 = -0.373% |
| G5 | >= 3 个年份 Net30 > 0 | **FAIL** | years with Net30 > 0 = 2 of 5 (2022:-0.223%, 2023:-0.136%, 2024:0.493%, 2025:0.050%, 2026:-0.373%) |
| G6 | Leave Top 5% 后仍 > 0 | **FAIL** | Leave Top 5% Net30 = -0.557% |
| G7 | M1–M4 中 >= 2 个基底 Net30 > 0 | **PASS** | MA structures with Net30 > 0 = 3 of 4 (M1:0.022%, M2:0.045%, M3:0.036%, M4:-0.010%) |
| G8 | V1–V5 中 >= 2 个基底 Net30 > 0 | **PASS** | Volume gates with Net30 > 0 = 5 of 5 (V1:0.033%, V2:0.075%, V3:0.072%, V4:0.062%, V5:0.026%) |
| G9 | 3% 邻域 positive_ratio >= 0.5 | **PASS** | pre-registered neighbourhood (thr=3%, window=10) positive_ratio = 0.760 over 25 cells; overall grid positive_ratio = 0.873 |
| G10 | >= 3 个 Null 家族 obs > null 97.5% | **PASS** | null families adopted (resolution >= 0.99) = 3 of 4; adopted families with obs > null 97.5% = 3 |
| G11 | Bias Audit 无 FAIL | **PASS** | bias audit: 6 PASS / 2 FLAG / 0 FAIL (3_survivorship_bias, 8_randomization_leakage) |
| G12 | BULL/RANGE/BEAR 中 >= 2 个 Net30 > 0 | **FAIL** | regimes with Net30 > 0 = 1 of 3 (BULL:0.198%, RANGE:-0.059%, BEAR:-0.153%) |

全部满足 = **FALSE**；`PROVISIONAL_ALPHA` = **FALSE**。

## §19 最终结论（任务书 §24 判决树）

```
ROBUST_ALPHA / WEAK_SIGNAL / TAIL_DEPENDENT / REGIME_DEPENDENT /
PARAMETER_FRAGILE / NO_ROBUST_ALPHA / FAIL → ARCHIVE

判定结果: FAIL → ARCHIVE
trading_authorization = NO
```

判决依据（按冻结的判决树顺序逐步核对）：

1. `G1..G12` 是否全部满足：**FALSE**（未满足：G1, G2, G3, G4, G5, G6, G12）
2. `G1`（Primary Net30 > 0）：**FAIL**（Primary Arm mean Net30 = -0.008%）
3. → 命中 `elif G1 不满足` 分支：**FAIL → ARCHIVE**。该分支按预注册规则优先于尾部/regime/参数分支。

补充事实（不改变判决，仅披露）：

- 尾部依赖：PRIMARY 毛口径 `TAIL_DEPENDENT = TRUE`，净 30bp 口径 `TAIL_DEPENDENT = FALSE`。
- 裸事件 RAW（未加任何过滤）Net30 = 0.077%，PF30 = 1.049，但 Leave Top5% 后为 -0.539% —— 其微弱的正均值同样来自尾部。
- Regime：BULL Net30=0.198%，RANGE Net30=-0.059%，BEAR Net30=-0.153%。
- 阈值轴：Spearman(阈值, Net30) RAW=1.000 / 全栈=1.000（MONOTONIC）。

## §20 Q1–Q7 逐条回答（任务书 §15）

- **Q1　≥3% 中阳线本身是否有次日 Alpha？**　裸事件 RAW 毛均值 0.377%，扣 30bp 后 Net30 = 0.077%，PF30 = 1.049，回归月聚类 95% CI = [-0.237%, 0.498%]（跨 0），Leave Top5% 后 = -0.539%。→ 均值在净成本后**不能与 0 区分**；毛口径微幅为正但完全依赖尾部。
- **Q2　「第一根」是否增加 Alpha？**　Δ(First vs RAW) = -0.0011（95% CI [-0.0033, 0.0006]）→ **UNPROVEN**；净口径 First 单独出现时 Net30 = -0.030%，低于 RAW 的 0.077%。
- **Q3　MA5/MA20 是否增加 Alpha？**　Δ(+F+MA vs +F) = 0.0004（95% CI [-0.0014, 0.0023]）→ **UNPROVEN**；M1/M2/M3 微正、M4 微负，分散在 0 附近。
- **Q4　量能阀门是否增加 Alpha？**　Δ(+F+V vs +F) = 0.0006（95% CI [-0.0001, 0.0012]）→ **UNPROVEN**；V1–V4 净口径均在 +0.0003 ~ +0.0008 区间，随阈值升高无稳定改善。
- **Q5　MA + Volume 是否产生稳定增量 Alpha？**　全栈 Δ(+F+MA+V vs +F+MA) = -0.0002（95% CI [-0.0005, 0.0001]）→ **UNPROVEN**；PRIMARY 相对 First 的净增量不足以把它推过 0。
- **Q6　Alpha 是否依赖极少数尾部股票？**　**是**。PRIMARY 去掉最大 5% 后 Net30 = -0.557%（全样本 -0.008%）；Top5% 贡献了毛正收益的 39.376%。
- **Q7　3% 阈值是否具有稳定意义？**　阈值轴上 Net30 随涨幅单调上升（Spearman RAW=1.000 / 全栈=1.000，`MONOTONIC`），即「当日涨得越多、次日净收益越高」这一关系是稳定的；但把阈值抬到 5–7% 属于**事后择优**，本实验按预注册坚持 3%。

## §21 科研结论

> **“本实验验证的是'尾盘第一根≥3%中阳线后的次日惯性'，而不是一个经过优化的交易策略。”**

**结论：FAIL → ARCHIVE。**　按任务书 §26，**立即 ARCHIVE，不得继续调参**。

PRIMARY 定义（尾盘第一根 ≥3% 中阳线 + MA5/MA20 结构 + 预注册量能阀门，T 收盘买入、T+1 收盘卖出、30bp 成本）在净成本口径下均值不为正，PF30 < 1，OOS 为负，5 年中仅 2 年为正，去尾后为负。因此「尾盘第一根 ≥3% 中阳线的次日惯性」在本数据集上**不构成可重复的正向 Alpha**。

不允许的补救（明确禁止，已遵守）：放宽到 T+3/T+5、引入 HVT/W7/HVE、改用网格 argmax（thr=7% / 窗口=20 / VR=3.0 / MA=M3）、或自由搜索 VR20 阈值。这些都只会制造过拟合。

`trading_authorization = NO`（无论结论如何）。

## §22 产物清单（任务书 §25）

- [x] `H_TAIL_MIDBAR_01_FREEZE.md`（3,974 bytes）
- [x] `H_TAIL_MIDBAR_01_SPEC.md`（17,554 bytes）
- [x] `H_TAIL_MIDBAR_01_EVENTS.csv`（5,964 bytes）
- [x] `H_TAIL_MIDBAR_01_MA.csv`（4,964 bytes）
- [x] `H_TAIL_MIDBAR_01_VOLUME.csv`（5,047 bytes）
- [x] `H_TAIL_MIDBAR_01_THRESHOLD.csv`（7,446 bytes）
- [x] `H_TAIL_MIDBAR_01_NULL.csv`（1,326 bytes）
- [x] `H_TAIL_MIDBAR_01_BOOTSTRAP.csv`（1,102 bytes）
- [x] `H_TAIL_MIDBAR_01_OOS.csv`（28,545 bytes）
- [x] `H_TAIL_MIDBAR_01_WALK_FORWARD.csv`（748 bytes）
- [x] `H_TAIL_MIDBAR_01_BIAS_AUDIT.csv`（2,082 bytes）
- [x] `H_TAIL_MIDBAR_01_TAIL.csv`（1,815 bytes）
- [x] `H_TAIL_MIDBAR_01_SUMMARY.json`（9,317 bytes）
- [x] `H_TAIL_MIDBAR_01_REPORT.md`（23,668 bytes）
- [x] `H_TAIL_MIDBAR_01_REGIME.csv`（3,100 bytes）
- [x] `H_TAIL_MIDBAR_01_OVERLAP.csv`（559 bytes）
- [x] `H_TAIL_MIDBAR_01_PARAM_GRID.csv`（54,058 bytes）
- [x] `H_TAIL_MIDBAR_01_RESULTS.json`（255,151 bytes）

