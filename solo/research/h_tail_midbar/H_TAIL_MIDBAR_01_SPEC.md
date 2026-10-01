# H-TAIL-MIDBAR-01 — 预注册规范（PRE-REGISTRATION）

    Research ID : H-TAIL-MIDBAR-01
    Version     : 1.0
    Created     : 2026-10-01
    Parent      : H-ALPHA-SOURCE-01 (No robust Alpha, unchanged)
    Status      : FROZEN BEFORE ANY RESULT WAS OBSERVED

本文件是本次实验**唯一的口径来源**。所有阈值、分层、判据与输出清单在
运行任何一行统计代码之前写定。运行后再修改本文件即视为作弊。

---

## §0 研究问题

> 如果一只股票在交易日**尾盘首次**出现一根涨幅 ≥3% 的中阳线，且 MA5 / MA20
> 结构良好、量能满足预先注册的阀门条件，那么在**收盘买入、次日卖出**，
> 是否存在稳定、可重复、扣除交易成本后的正向 Alpha？

本实验是**解释性/验证性科研**，不是策略优化。

---

## §1 研究边界【最高优先级】

**只研究**：尾盘第一根 ≥3% 中阳线 → 收盘确认 → 次日卖出。

**禁止**扩展为：T+3 / T+5 / T+10 / T+20、HVT、W7、HVE、二次突破、回踩、
趋势波段、基本面、主题、ML、RSI、MACD、KDJ、情绪评分、综合打分模型。

MA5 / MA20 与 Volume **只允许**作为预先定义的离散过滤器（Gate），
**不允许**进入任何连续评分或加权打分。

本实验唯一目标：验证「尾盘第一根 ≥3% 中阳线」的**次日惯性**是否存在。

---

## §2 数据要求

- 只允许使用现有 Tushare cache（`cache_daily/` + `price_panel.parquet` +
  `basic_panel.parquet` + `index_panel.parquet`）。
- 禁止 TDX / AkShare / Baostock / Yahoo / JoinQuant / Ricequant / 外部行情 API。
- 复用既有研究面板 `research/hve/hve_common.py::build_grid`（qfq OHLC +
  vol + amount + turnover + traded + ST/delist + board + 交易日历），
  该面板是**只读**依赖，本实验不修改它。

### §2.1 分钟/尾盘数据可用性结论（运行前已核实）

对 `cache_daily/` 全目录做文件名模式扫描（`*min*`、`*5m*`、`*15m*`、
`*60m*`、`*1min*`）：**零命中**。现有 Tushare cache 只有日线。

因此本实验属于任务书 **§2 情形 A：日线代理实验**：

> `ret_t = close_t / close_{t-1} - 1` 的日线收盘涨幅 ≥3%，
> 作为「尾盘中阳线」的**日线代理**。

最终报告必须逐字声明：

> **本研究验证的是「收盘中阳线 → 次日收益」，而不是严格意义上的「尾盘惯性」。**
> 日线数据无法还原 14:30–14:57 的真实尾盘时点，本项目**不做**这种伪装。

### §2.2 宇宙（Universe，运行前冻结）

与姊妹实验保持同一可交易宇宙，避免样本口径漂移：

    eligible = hve_common.eligibility(g)          # traded & ~ST & ~delist
                                                  # & 上市满 250 交易日
                                                  # & 无 >=60 日连续停牌空洞
               AND (board != 'BSE')

北交所剔除。这是预注册选择，不是结果驱动。

### §2.3 价格口径

| 用途 | 口径 | 说明 |
|---|---|---|
| 事件日涨幅 `ret_t` | `g['pct_chg']`（原始 close/pre_close − 1） | 交易所官方「涨幅」，停牌复牌日仍有定义 |
| 形态 `body` / `clv` | qfq（比值对同一日内共同因子不变） | 与原始口径等价 |
| 所有收益 | qfq `close` / `open` / `high` / `low` | 与姊妹实验一致，跨除权可比 |

---

## §3 核心事件定义

```text
ret_t  = close_t / close_{t-1} - 1        (实现: g['pct_chg'])
body_t = (close_t - open_t) / open_t
clv_t  = (close_t - low_t) / (high_t - low_t)
```

核心事件（记为 `E0(thr)`，thr 为涨幅阈值）：

```text
eligible(t)
AND ret_t >= thr
AND close_t > open_t
AND body_t >= 0.03
AND clv_t  >= 0.65
```

`clv_t >= 0.65` 用于剔除长上影冲高日；`body_t >= 0.03` 保证是实体中阳线
（注意：任务书 §3 写 `body_t = (close-open)/open`，**本实验采用该定义**，
与 hve_common 的 `px['body'] = (close-open)/(high-low)` **不同**，
不复用后者）。

主阈值 `thr = 0.03`。

---

## §4 「第一根」定义

对每只股票按时间排序，向前看窗口 **不含当日**：

```text
F1 : 过去  5 个交易日 [t-5, t-1] 内没有 E0 事件
F2 : 过去 10 个交易日 [t-10, t-1] 内没有 E0 事件      <-- PRIMARY
F3 : 过去 20 个交易日 [t-20, t-1] 内没有 E0 事件
```

「第一根」`First_w = E0 & ~any_prev(E0, w)`。

> **F2 为 Primary Definition。** F1 / F3 仅用于稳定性检验。
> 禁止看到结果后选择表现最好的 F1/F2/F3。

---

## §5 MA5 / MA20 形态过滤（离散结构组，非评分）

```text
MA5        = mean(close, t-4 .. t)     min_periods = 5
MA20       = mean(close, t-19 .. t)    min_periods = 20
MA5_slope5 = (MA5[t] - MA5[t-5]) / 5
MA20_slope5= (MA20[t] - MA20[t-5]) / 5
```

预注册四种结构（互不排斥，可同时成立）：

```text
M1 短强     : close > MA5 > MA20
M2 短线转强 : close > MA5 AND MA5 >= MA20 AND MA5_slope5 > 0
M3 均线共振 : close > MA5 AND close > MA20 AND MA5 > MA20 AND MA20_slope5 >= 0
M4 低位启动 : close > MA5 AND close > MA20 AND MA5 > MA5[t-1] AND MA20_slope5 <= 0
```

`MApresent = M1 | M2 | M3 | M4`（作为 §15 增量阶梯里「MA 结构层」的定义，
运行前冻结：一个结构都不成立的样本不算「结构良好」）。

M4 **不允许事后删除**。必须同时输出 `ALL / M1 / M2 / M3 / M4`，
并与无 MA 过滤的 Baseline 比较。

研究目标不是寻找「最佳均线组合」，而是回答：
**MA5/MA20 结构是否对次日惯性具有稳定的增量信息？**

---

## §6 量能阀门（Gate，禁止进入连续评分）

```text
VR5  = vol_t / mean(vol, t-5 .. t-1)
VR20 = vol_t / mean(vol, t-20 .. t-1)      <-- 分母不含当日（shift 1）
```

`vol` 使用面板 `traded` 掩码，停牌日置 NaN，滚动窗口 `min_periods=w`。

预注册阀门：

```text
V0 不使用量能
V1 VR20 >= 1.20
V2 VR20 >= 1.50
V3 VR20 >= 2.00
V4 VR20 >= 3.00
V5 1.20 <= VR20 <= 3.00        <-- PRIMARY
```

Primary 取 V5 的**理由不是假设 V5 最优**，而是研究「有效放量但排除极端巨量」
是否存在差异。严禁结果出来后自由寻找 1.37 / 1.62 / 2.18 之类最优阈值。

---

## §7 核心实验矩阵

```text
B        = First(F2)                                    （Baseline）
B+MA     = First(F2) & MApresent
B+VOL    = First(F2) & V5
B+MA+VOL = First(F2) & MApresent & V5                   <-- PRIMARY ARM
```

**PRIMARY ARM = `First(10) & MApresent & V5`**，用于 §23 的 G1–G4、G6。

必须全部报告，不得只报最终过滤后的结果。

---

## §8 交易执行（严格 T+1）

信号在 **T 日收盘** 才完全确定。

```text
Entry (primary)   : T 日收盘价            close[t]
Entry (second)    : T+1 开盘价            open[t+1]      （第二执行模型）
```

严禁「用 T 日收盘确定信号，却用 T 日盘中更低价格成交」。
入口价格只有上面两种，**不允许**使用 `low[t]`、`open[t]` 或任何
T 日盘中价作为成交价。

---

## §9 Exit

```text
T+1 Close   <-- 唯一正式策略结果
```

同时报告（仅作诊断，**不得作为策略收益**）：

```text
T+1 Open / T+1 High / T+1 Low / T+1 Close
```

其中 `T+1 High` 仅用于「潜在盘中收益空间分析」。

---

## §10 评价指标

每个分层必须输出：

```text
N / Mean / Median / Win Rate / Profit Factor / Std / P10 / P25 / P75 / P90 / Max / Min
```

净成本阶梯（在毛收益上线性扣减，从不因看到结果而下调）：

```text
0 / 10 / 20 / 30 / 50 bp        30bp = Primary Net Cost
```

最终策略必须回答：`Mean Net30` / `Win Rate` / `PF30`。

---

## §11 Leave-Tail Test（最重要）

```text
Full Sample / Leave Top 1% / Leave Top 5% / Leave Top 10%
```

若 `Full > 0` 而 `Leave Top 5% <= 0`，则 `TAIL_DEPENDENT = TRUE`，
**不得称为 Robust Alpha**。

另须报告 `Median`、`Mean`、`Top 1% contribution`、`Top 5% contribution`
（分子＝最大 k% 收益之和中的正贡献，分母＝全样本正收益之和）。

---

## §12 横截面分层（全部基于预注册定义）

| 维度 | 层级 | 基底 |
|---|---|---|
| MA Structure | ALL / M1 / M2 / M3 / M4 | `First(F2)` |
| Volume | V0 / V1 / V2 / V3 / V4 / V5 | `First(F2)` |
| First-event | F1 / F2 / F3 | `E0(3%)` |
| 当日涨幅 | 3–4% / 4–5% / 5–7% / >7% | `First(F2)` |

当日涨幅分层**仅用于描述收益曲线**，不得通过结果选择某个区间作为正式策略。

---

## §13 「3% 是否真的有意义」

预注册阈值 `2 / 3 / 4 / 5 / 6 / 7 %`，**3% 为 Primary Hypothesis**。

对两套基底分别输出（`E0` 裸事件；`E0 & F2 & MApresent & V5` 全栈），
并计算阈值轴上的 Spearman 单调性。若单调性不稳定，必须报告
`NO_MONOTONICITY`。**禁止选择表现最佳阈值。**

---

## §14 Null Model（四个）

| 家族 | 构造 | 键 / 池 |
|---|---|---|
| N1 随机股票日 | 同一交易日随机抽 eligible 股票 | key = 交易日；池 = 当日全部 eligible cell |
| N2 同股随机日 | 同一股票随机抽 eligible 交易日 | key = 股票；池 = 该股全部 eligible cell |
| N3 同股同状态随机日 | 同一股票 + 匹配状态随机抽日 | key = (股票, 状态)；状态 = MA 结构码 × 涨幅桶 × VR20 桶 |
| N4 随机 ≥3% 事件 | 同交易日随机抽 **上涨日**（close>open）并控涨幅区间 | key = (交易日, 涨幅桶)；池 = 当日 eligible 且 close>open 且同桶 |

**预注册匹配变量（N3）**：

```text
MA 结构码   : 0=none, 1=M1, 2=M2, 3=M3, 4=M4   （优先级 M1>M2>M3>M4，确定性）
涨幅桶      : 3–4% / 4–5% / 5–7% / >7%
VR20 桶     : <1.2 / 1.2–1.5 / 1.5–2.0 / >=2.0
```

**N4 匹配变量（预注册）**：当日涨幅桶（同上 4 桶），并要求 `close>open`。

抽样的**唯一禁区**是「抽回自己的那一格 (s, t)」（会造成循环比较）；
允许抽到相邻交易日。每个事件每个 replicate 抽一次，`rounds=8` 重抽，
`resolution`（8 轮内解决比例）必须 ≥ 0.99 才采纳该家族。

每个家族 `B = 1000`，报告 `obs / null mean / null 2.5% / null 97.5% / excess /
单侧 p`，收益口径与观测一致（`close[t] → close[t+1]`，并给 net30）。

---

## §15 增量 Alpha 分解

阶梯（逐层叠加，父→子为单变量增量）：

```text
RAW      = E0(3%)                             裸事件
+F       = E0 & F2                            加「第一根」
+F+MA    = E0 & F2 & MApresent                加 MA 结构
+F+V     = E0 & F2 & V5                        加 量能阀门（独立于 MA）
+F+MA+V  = E0 & F2 & MApresent & V5            全栈 = PRIMARY
```

分别计算 **Incremental Return / Win Rate / PF / Net30**（子 − 父，配对在同
事件集上，用 `cluster_boot_diff` 做月度聚类 bootstrap）。

必须回答 Q1–Q7（见 §25 报告模板）。

---

## §16 市场状态（外部描述，不做择时优化）

只做描述，不使用状态优化任何参数：

```text
regime      : hrbp_common.regime_by_day（CSI300 vs MA60 & 60日收益）
               BULL / RANGE / BEAR
index_up    : CSI300 当日收盘涨跌方向
advance_ratio: 当日 eligible 股票中 pct_chg>0 的比例
mkt_amount  : 当日 eligible 股票 amount 合计
```

目的只是回答「该模式是否只在某一种市场环境下有效」。

---

## §17 OOS / Walk-Forward

样本区间（严格按交易日切分，**禁止随机 Train/Test、禁止打乱时间**）：

```text
样本起点 = 2022-01-01      （任务书 §17 指定；面板实际始于 2018-01-02）
IS        = 2022 – 2024
Validation= 2025
OOS       = 2026
```

**所有参数在实验开始前已注册，本实验不拟合任何参数**，因此
Walk-Forward 的解释是：在扩展窗口下，冻结配置在**严格样本外**区间的表现。

```text
fold1  train 2022–2023  -> test 2024
fold2  train 2022–2024  -> test 2025
fold3  train 2022–2025  -> test 2026
```

---

## §18 年度稳定性

逐年（2022 / 2023 / 2024 / 2025 / 2026）输出：
`N / Mean / Median / Win Rate / PF30 / Net30`。

要求：不能由单一年份贡献全部收益（写入 G5）。

---

## §19 Bootstrap

**Monthly Cluster Bootstrap**（同一股票连续信号、同一市场环境下信号高度相关，
不能把每日信号当独立样本）。`B = 1000`，按日历月重抽整簇，
报告 `Mean / 95% CI / P-value`（单侧，净 30bp 口径）。

另附 `block_boot_mean`（block=20 会话）作为稳健性对照，不用于判决。

---

## §20 Overlap / Independence Audit

```text
same_stock_adjacent_signal : 同股相邻交易日(isuess gap == 1) 的信号对比例
same_stock_5d_overlap      : 同股 5 日内信号对比例
same_stock_20d_overlap     : 同股 20 日内信号对比例
```

存在大量连续信号时，**不得把 N 当作独立样本数**。报告
`n_eff_note = N / (1 + mean_overlap_pairs_per_event)` 作为参考。

---

## §21 Selection Bias Audit（8 项，逐项 PASS / FAIL / FLAG）

| # | 项目 | 机器化检验 |
|---|---|---|
| 1 | look_ahead | 把面板尾部截断 K=120 会话重算全部掩码，与完整面板在保留区的掩码做**逐位比对**，必须完全一致 |
| 2 | selection_bias | 报告主配置在 §22 参数格点中的排名，证明它不是 argmax |
| 3 | survivorship_bias | `g['gone']` 提前停止报价的股票数与样本内出现数 |
| 4 | execution_bias | 校验 entry/exit 索引与价格字段；确认未使用 T 日盘中价 |
| 5 | overlapping_sample | §20 的重叠率与 n_eff |
| 6 | future_information | `First_w` 的窗口方向检验：用**前向**窗口重算得到的 N 必须与后向不同（证明用的是后向） |
| 7 | parameter_leakage | SPEC mtime 早于首个 run 产物 mtime；主配置非格点最优 |
| 8 | randomization_leakage | 200 次随机抽样的池外率；null 分布非常数且 spread 收敛 |

---

## §22 参数稳定性

预注册扰动网格（**只做预注册扰动，不做无限优化**）：

```text
涨幅阈值  : 2 / 3 / 4 / 5 / 6 / 7 %          (6)
第一根窗口: 5 / 10 / 20                       (3)
VR 阀门   : None / 1.2 / 1.5 / 2.0 / 3.0      (5)
MA 结构   : ALL / M1 / M2 / M3 / M4           (5)
```

共 450 格，每格输出 `n / mean / net30 / win / pf30`。

```text
positive_ratio  = net30 > 0 的格占比
monotonicity    = 各轴上 net30 的 Spearman 相关
```

若只有某一个参数组合赚钱 → `PARAMETER_FRAGILE = TRUE`；
若附近参数普遍有效 → `PARAMETER_STABLE = TRUE`。

---

## §23 核心 PASS / FAIL Gate

只有 **G1–G12 全部满足** 才能进入 `PROVISIONAL_ALPHA`。

```text
G1  Primary Arm 的 T+1 Mean Net30 > 0
G2  Primary Arm 的 PF30 > 1
G3  Primary Arm 的月度聚类 bootstrap 95% CI（net30）不跨 0
G4  Primary Arm 的 OOS(2026) Net30 > 0
G5  至少 3 个年份 Net30 > 0
G6  Primary Arm 的 Leave Top 5% 后仍 > 0
G7  不是单一 MA 组合贡献全部 Alpha（M1–M4 中 >=2 个在同一基底下达 net30 > 0）
G8  不是单一 Volume 阀门贡献全部 Alpha（V1–V5 中 >=2 个在同一基底下达 net30 > 0）
G9  3% Primary 附近参数稳定（扰动邻域 positive_ratio >= 0.5）
G10 Null Model 有显著区分度（4 个家族中 >=3 个 obs > null 97.5%）
G11 Bias Audit 全部 PASS（无 FAIL；FLAG 允许但必须披露）
G12 不存在明显的单一 Regime 依赖（BULL/RANGE/BEAR 中 >=2 个 net30 > 0）
```

否则 `UNPROVEN`，或 `FAIL → ARCHIVE`。

---

## §24 最终结论分类

只允许以下取值：

```text
ROBUST_ALPHA / WEAK_SIGNAL / TAIL_DEPENDENT / REGIME_DEPENDENT /
PARAMETER_FRAGILE / NO_ROBUST_ALPHA / FAIL → ARCHIVE
```

禁止输出「强烈推荐 / 胜率很高 / 最佳策略 / 最优参数 / 稳赚 / 高确定性」。

**判决树（运行前冻结）**：

```text
if G1..G12 全部满足:
        ROBUST_ALPHA   if  G3 且 bootstrap p < 0.01 且 G4 且 G6 且 G9
        WEAK_SIGNAL    otherwise
elif G1 不满足:                       FAIL -> ARCHIVE   （明确负数，归档）
elif Full > 0 and Leave5 <= 0:        TAIL_DEPENDENT
elif G12 不满足 and G1 满足:          REGIME_DEPENDENT
elif G9  不满足:                      PARAMETER_FRAGILE
elif not G3 and not G10:              NO_ROBUST_ALPHA
else:                                 UNPROVEN
```

`trading_authorization = NO`（无论结论如何，本实验不授权实盘）。

---

## §25 必须生成的文件（全部位于本目录）

```text
H_TAIL_MIDBAR_01_FREEZE.md
H_TAIL_MIDBAR_01_SPEC.md
H_TAIL_MIDBAR_01_EVENTS.csv
H_TAIL_MIDBAR_01_MA.csv
H_TAIL_MIDBAR_01_VOLUME.csv
H_TAIL_MIDBAR_01_THRESHOLD.csv
H_TAIL_MIDBAR_01_NULL.csv
H_TAIL_MIDBAR_01_BOOTSTRAP.csv
H_TAIL_MIDBAR_01_OOS.csv
H_TAIL_MIDBAR_01_WALK_FORWARD.csv
H_TAIL_MIDBAR_01_BIAS_AUDIT.csv
H_TAIL_MIDBAR_01_TAIL.csv
H_TAIL_MIDBAR_01_SUMMARY.json
H_TAIL_MIDBAR_01_REPORT.md
```

报告生成脚本：`has_report.py`，最终必须以 `EXIT 0` 结束。

---

## §26 不变量（运行期不得违反）

1. 本目录之外**只读**：不修改 `research/hve/`、`research/h_rbp/`、
   `research/h_alpha/`、`cache_daily/`、`report_daily/` 及任何生产脚本。
2. 不使用任何 T 日的盘中价格作为成交价（唯一例外是作为诊断列报告）。
3. 不在看到结果后修改阈值、分层、窗口或判据。
4. 不使用 T+3 及以上的持有期。
5. 不引入 §1 禁止清单中的任何因子。
