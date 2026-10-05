# H-SLG-02 SPEC v1.0 — A股主升后「整理完成度」连续因子横截面预测力研究

Research ID: `H-SLG-02`　Version: `1.0`　Created: `2026-10-03`
Hypothesis ID space: `H-SLG-02-*`
前置研究: `H-SLG-01`（最终状态 `FAILED`，见 `H_SLG_01_SPEC.md` / `second_leg_report.md`）

本文件是**唯一口径来源**。任何代码不得偏离本文件；如需修改，必须执行
`V1 冻结 → 输出结果 → 提出新 Hypothesis → V2`，禁止回填优化。

---

## 0. 研究定位与目标

### 0.1 为什么需要这份新 SPEC

`H-SLG-01` 已证否其核心假设，结论不得修改、不得复用其参数：

```
H-SLG-01 结论（冻结事实）
  §18 判决         : FRAGILE（2/12 位）
  A 组事件数       : 71，T+5 net30 = -1.65%，胜率 33.8%
  OOS net30 (A)    : -0.49%，弱于普通突破组 B（-0.24%）
  参数 positive_ratio : 0.074
  N1 null p        : 0.9925
  增量检验 Model5-Model4 : W = 0.286 / 0.163（不显著，OOS_IC 下降）
  增量检验 Model4-Model3 : W = 15.17 / 40.83（1% 显著，OOS_IC 上升）
```

其中**唯一站得住的线索**是最后一条：把「整理完成度」当作变量（而非事件触发器）时，
它在动量/趋势之外仍有统计显著的增量信息（尽管 ΔR² 仅约 7e-5，经济意义很弱）。

`H-SLG-01` 的失败还有两个**技术原因**，本次必须直接修复：

| 失败原因 | H-SLG-01 表现 | H-SLG-02 处理 |
|---|---|---|
| 样本量不足 | 主口径要求 `break_vol>=1.0` 等硬门槛，A 组仅 71 个事件，OOS 仅 7 个 | **放弃事件口径**，改用全 `CTX` 池的**横截面因子**口径，样本量提升 4 个数量级 |
| 二值状态损失信息 | `READY` 是 0/1 状态，覆盖 1.4% 单元格 | **放弃二值筛选**，把 §7 的完成度变量还原为**连续量** |

### 0.2 本次研究的问题

> **在主升后进入整理的股票池内，「整理完成度」作为连续因子，是否对未来
> T+3 / T+5 / T+10 的横截面超额收益具有稳定、单调、且超越动量的预测力？**

三个子问题：

* **G1**：单个完成度因子是否具有非零横截面 IC（符号、量级、显著性与单调性）？
* **G2**：等权合成因子 `COMP` 是否优于单因子与动量基准？
* **G3**：在控制 `MOM20 / MOM60 / RS / Volume / Size` 后，`COMP` 是否仍有增量 IC？

### 0.3 明确不做什么

本研究**不研究**：低位 W 底、普通超跌反弹、单纯动量、突破事件择时、
买入时机与仓位（属独立第二阶段）。本研究**不产出任何买入信号**。

### 0.4 与 H-SLG-01 的边界

1. 本 SPEC **重新冻结**参数，**不继承** `H-SLG-01` 的任何结论性阈值。
2. `H-SLG-01` §5 的因果结构定义**原样复用**（只读引用），不重新定义结构。
3. `H-SLG-01` 的 10 个产物与结论**保持原样**，不得回填修改。

---

## 1. 数据原则

1.1 只使用现有 Tushare 本地缓存，读取路径固定：

| 用途 | 来源 |
|---|---|
| 日线 OHLC / 成交量额 / 换手 / ST / 退市 / 涨跌停 / 板块 | `hve_common.build_grid()` |
| 总市值 / 换手率 | `basic_panel.parquet`（经 `mce_common.load_size_mv`） |
| 指数（市场收益 / 市场状态） | `index_panel.parquet`，`000300.SH` |
| 行业（PIT 申万一级） | `D:\mystock\cache_daily\industry\sw_industry_map.csv` |
| 交易日历 | `calendar.parquet` |
| 结构 / 完成度变量 | `slg_common`（只读复用，禁止修改） |

1.2 **禁止**：临时下载其他行情源、TDX、AkShare、Wind、同花顺、人工挑选股票。
1.3 字段缺失必须如实标注，不得悄悄替代数据源。
1.4 复权口径 `qfq`；`prev_close := close[t-1]`。
1.5 本研究为独立科研模块，禁止修改现有交易系统核心逻辑。

---

## 2. 严格防止未来函数

2.1 **因子**只在 `t` 日及之前的信息上计算；`t` 日收盘即可得，故 `t` 日收盘后即可
形成因子值，`t+1` 日开盘可交易 —— 本研究只做**统计检验**，不做交易执行。
2.2 `t` 之后的任何价格/成交量**只允许**出现在 label 列（`LABEL_COLUMNS`）。
2.3 特别禁止：用未来 N 日最高价确认左高；用未来 N 日最低价确认右底；
用未来突破结果定义完成度；用未来收益决定样本是否入池。
2.4 结构量一律由「截至 t 的向后窗口」计算，`candidate_date = confirmation_date = event_date`。
2.5 `future_column_scan`（§21）复用于本模块，任一失败 → `FATAL` → `EXIT 3`。

---

## 3. 研究时间与样本

3.1 面板：`S = 5816` 只 × `N = 2120` 交易日（`20180102 .. 20260924`）。
3.2 烧入：`study_start = 20190101`，`burn_in_sessions = 250`。
3.3 时间分段（**时间切分，禁止随机切分**）：

| 段 | 区间 | 用途 |
|---|---|---|
| PRE | `.. 20191231` | 仅描述性统计，**不进入**结论 |
| IS | `20200101 .. 20241231` | 因子筛选与主口径（`COMP_SIG` 只在此段选因子） |
| VALID | `20250101 .. 20251231` | 时间验证段 |
| OOS | `20260101 .. 20260924` | 最终样本外段，**禁止**反向调整 |

3.4 Walk-Forward：`train 3 年 → test 1 年`，滚动 `2021..2026`（§14）。
3.5 实际区间若与 3.1 不符，报告必须写明实际值。

---

## 4. 股票池

4.1 **U1（主口径）**：`hve_common.eligibility(g) & (board != 'BSE')`。
4.2 **U2（幸存者偏差对照）**：`traded & ~st & (board != 'BSE')`。
4.3 北交所（`BSE`）排除。
4.4 退市前历史打印保留；`DELIST_PIT_UNAVAILABLE` 如实标注。
4.5 报告必须写明股票池数量、是否含退市、幸存者偏差影响（U1 vs U2）。

---

## 5. 因果结构定义（复用 H-SLG-01 §5，不重新定义）

固定窗口：

```
LEG_W    = 40    # 主升长度上限
CONS_MIN = 5     # 整理最短
CONS_MAX = 40    # 整理最长（扰动轴）
PEAK_LO  = 5
PEAK_HI  = 45
```

核心结构量（全部只用 `<= t` 数据，与 `H-SLG-01` 逐字一致）：

```
peak_off     = argmax(high[t-45 .. t-5])      同高取更早
leg_start    = argmin(low[peak_day-40 .. peak_day])
first_rise_pct  = high[peak_day] / low[leg_start] - 1
first_rise_days = peak_day - leg_start
bounce_off   = argmax(high[peak_day+1 .. t])
left_bottom  = min(low[peak_day+1 .. bounce_day])
right_bottom = min(low[bounce_day+1 .. t])    要求 t - bounce_day >= 2
neckline     = high[peak_day]
```

门槛（主口径）：`first_rise_pct >= 0.25`、`first_rise_days >= 5`、
`retracement_pct >= 0.05`、`CONS_MIN <= consol_days <= CONS_MAX`。

`CTX = first_rise_ok & retrace_ok & consol_ok`（即 `H-SLG-01` 的 strict CTX）。

---

## 6. 因子池（本研究核心改动）

### 6.1 定义

```
U_FACTOR = CTX & R1
```

其中 `R1 = RIGHT_BOTTOM_FORMED = (t - bounce_day >= 2)`。

### 6.2 三条硬约束

1. **禁止**把 `READY` 用于筛选。`READY` 只是 `U_FACTOR` 内的一个诊断子集；
   若用 `READY` 筛选，本研究就退化成 `H-SLG-01` 的事件研究，样本量重新崩塌。
2. **禁止**把 `FAILED` 用于筛选。`FAILED` 样本必须在池内，否则无法检验
   「完成度低 → 收益低」的另一半证据。
3. `U_FACTOR` 内**允许**字段缺失的单元格存在，但每个因子的有效样本独立计算，
   报告必须给出每个因子的有效 `n`。

### 6.3 重叠问题（必须显式处理）

同一只股票在一个整合理期内最多重复出现 `CONS_MAX = 40` 次（连续交易日），
因此 `U_FACTOR` 的单元格之间存在强重叠。本 SPEC 的处理见 §9。

### 6.4 重复度报告（强制）

报告必须给出：

```
U_FACTOR 单元格数 / 平均每日截面只数 / 唯一 (股票, 整合理期) 数 / 平均每期出现次数
```

---

## 7. 因子定义（14 个连续 + 2 个计数，全部来自 §5 与 H-SLG-01 §6 变量）

每个因子给出**预注册预期方向**（`+` 表示因子值越大越"完成"）。
**预期方向在实现前登记，不得在看到结果后翻转。**
实测符号与预期冲突时必须**逐因子标注 `SIGN_CONFLICT`**。

### 7.1 量能收缩（2）

| # | 因子 | 定义 | 预期 |
|---|---|---|---|
| P1 | `vol_contract_rb` | `rb_vol_over_ma20` | `-` |
| P2 | `vol_contract_consol` | `consol_vol_over_leg` | `-` |

> 量能只作为**待验证变量**。禁止预设「缩量 = 卖压减少」。

### 7.2 波动收敛（2）

| # | 因子 | 定义 | 预期 |
|---|---|---|---|
| P3 | `vol_ratio_5_leg` | `std(5日收益)/std(主升20日收益)` | `-` |
| P4 | `atr_ratio` | 整理末期 5 日 `ATR/Close` | `-` |

### 7.3 均线结构（5）

| # | 因子 | 定义 | 预期 |
|---|---|---|---|
| P5 | `ma20_pos` | `close / ma20 - 1` | `+` |
| P6 | `ma20_slope` | `ma20_slope20` | `+` |
| P7 | `ma20_hold` | `1 - frac_below_ma20` | `+` |
| P8 | `ma60_pos` | `close / ma60 - 1` | `+` |
| P9 | `ma60_slope` | `ma60_slope20` | `+` |

### 7.4 结构 / 相对强度（5）

| # | 因子 | 定义 | 预期 |
|---|---|---|---|
| P10 | `rb_quality` | `right_bottom / left_bottom - 1` | `+` |
| P11 | `rb_above_ma20` | `right_bottom / ma20 - 1` | `+` |
| P12 | `near_neckline` | `close / neckline - 1` | `+` |
| P13 | `rb_hold` | `(t - right_bottom_day) / consol_days` | `+` |
| P14 | `rs_sector` | `rs_sector_20` | `+` |

### 7.5 计数型对照（2）

| # | 因子 | 定义 |
|---|---|---|
| P15 | `ready_count` | `R1+R2+R3+R4+R5+R6`（0..6 的整数） |
| P16 | `ready_dummy` | `READY`（0/1） |

`P15 / P16` 是**对照组**：它们代表 `H-SLG-01` 原有的二值/计数口径。
若 `P15/P16` 的 IC 显著优于连续因子，说明"完成度"的价值来自二值化事件；
若相反，则说明连续化确实增益。**两种结果都必须如实报告。**

---

## 8. 横截面标准化与 IC 口径

8.1 **因子标准化**：每个交易日在 `U_FACTOR` 有效样本内做 **rank 百分位化**
（`rank / (n+1) ∈ (0,1)`）。缺失值剔除，不做插补。

8.2 **label**：

```
y_h(s,t)   = close[t+h] / close[t] - 1                    h ∈ {3, 5, 10, 20}
y_ex_mkt   = y_h - index_ret(000300.SH, t, t+h)
y_ex_sec   = y_h - 同期同行业等权平均 y_h
```

8.3 **主口径**：`IC = 每日 Spearman(rank(factor), rank(y_ex_sec))`，主 horizon `h* = 5`。
同时报告 `IC(absolute)` 与 `IC(y_ex_mkt)`。

8.4 **IC 统计量**（每个因子 × 每个 horizon）：

```
IC_mean / IC_std / ICIR = IC_mean / IC_std / t_NW（Newey-West, lag = 5）
正值占比 / 平均每日截面 n / 有效交易日数
```

8.5 **显著性门槛（预注册）**：`|IC_mean| >= 0.01` **且** `|t_NW| >= 2.0`。

8.6 **三段一致**：`IS / VALID / OOS` 三段 IC 必须**同号**才可判为稳定；
仅 IS 显著但 VALID/OOS 反号 → `UNSTABLE`。

8.7 全部 IC 必须同时报**绝对 IC 与 t 值**，**禁止只报 IC 均值**。

---

## 9. 重叠处理（强制双口径）

| 口径 | 定义 | 用途 |
|---|---|---|
| **R-ALL**（主口径） | 使用 `U_FACTOR` 全部单元格，每日横截面 IC | 最大化截面样本 |
| **R-DEDUP**（稳健口径） | 同一 `(股票, 整合理期)` 只保留**首次**进入 `U_FACTOR` 的日期 | 消除重叠导致的 t 值虚高 |

9.1 整合理期用 `cluster_events(gap = 5)` 在 `CTX & R1` 上聚类确定。
9.2 R-ALL 的 `t_NW` 必须做 Newey-West 修正（`lag = 5`）；R-DEDUP 报普通 `t`。
9.3 **两个口径必须同时报告**。若符号不一致或显著性反转 → 标记
`EPISODE_SENSITIVE`，该因子**不得**判 PASS。
9.4 R-DEDUP 的 IC 每日截面样本必然变小，报告必须给出其平均每日 `n`；
若某日 `n < 5` 则该日不参与 IC。

---

## 10. 分层（分位）检验

10.1 每日在 `U_FACTOR` 内按因子分 `Q = 5` 组（`Q1..Q5`），等权持有 `h` 日。
10.2 报每组的 `n / gross / net15 / net30 / net50 / win30 / median /
max_drawdown / profit_factor`，以及 `Q5 - Q1` 价差。
10.3 **单调性**：`Spearman(Q_rank, mean_ex_sec_ret)`，门槛 `>= 0.80`（预注册）。
10.4 **禁止只看胜率**；必须同时给 `gross` 与 `net`，成本四档 `0/15/30/50bp`。

---

## 11. 合成因子 `COMP`

11.1 **权重禁止优化**。`COMP` 一律为**等权平均**。

两个版本：

| 版本 | 组成 | 方向 |
|---|---|---|
| `COMP_ALL` | `P1..P14` 全部 | **按 §7 预注册预期方向**（`-` 因子取负） |
| `COMP_SIG` | 仅 `IS` 段满足 §8.5 显著性的因子 | 按 **IS 段实测方向** |

11.2 `COMP_SIG` 的因子筛选**只允许使用 IS 段**（`2020..2024`）。
VALID / OOS 段**不得**参与筛选、排序或方向确定。
11.3 `COMP` 的构造公式必须在报告中逐字给出（哪个因子、什么方向、等权）。
11.4 因子等权合成前，各因子先做 §8.1 的 rank 标准化。

---

## 12. 增量信息检验（本研究最终核心）

12.1 **每日横截面回归**，因变量 `rank(y_ex_sec)`，h ∈ {5, 10}：

| 模型 | 自变量 |
|---|---|
| M0 | `rank(MOM20) / rank(MOM60) / rank(RS_sec) / rank(vol_ratio_20) / rank(ln_size)` |
| M1 | M0 + `rank(COMP)` |
| M2 | M0 + `rank(COMP_ALL)` + `rank(COMP_SIG)` |
| M3 | M0 + 全部 `P1..P14`（体现"分散加因子"的收益） |

12.2 输出：`β_mean(COMP) / t_NW(β) / 正值占比 / 每日截面 R² 均值 / ΔR²(M1-M0)`。
12.3 **核心问题**：`M1 - M0` 的 `ΔR²` 与 `β(COMP)` 的 `t_NW` 是否显著。
若 `M0`（纯动量）已解释几乎全部截面变异 → 必须**如实报告
「整理完成度只是动量的另一种表达」**。
12.4 `M3 - M1`：把因子拆开比合成更好还是更差。
12.5 模型的 OOS 评估：在 `VALID / OOS` 段用 IS 段系数外推，报
`OOS_IC / OOS_ΔR²`。

---

## 13. Null Model / 随机对照

`B = 2000`，`rounds = 8`，`min_resolution >= 0.99`。单边 `p = P(null 统计量 >= obs)`。

| 编号 | 定义 | 检验什么 |
|---|---|---|
| **N1**（主 null） | **同一交易日内**随机打乱 `COMP` 值，保持当日截面分布与日期不变 | 截面**排序**信息 |
| N2 | 同一股票内，把其在 IS 段的 `COMP` 值随机重排到它出现的日期上 | 个股内**时序**信息 |
| N3 | 每日从当日 `U_FACTOR` 中随机抽取一个 `P1..P14` 因子值作为对照因子 | 是否只是"任意因子都行" |

13.1 三套 Null 必须同时报告；主判定用 **N1**。
13.2 `resolution`（命中率）必须报告以证明对照池非空。

---

## 14. OOS / Walk-Forward

14.1 时间切分 §3.3：IS / VALID / OOS，禁止随机切分。
14.2 每个因子与 `COMP` 必须报告三段 IC、`t_NW`、符号一致性。
14.3 Walk-Forward：`train 3 年 → test 1 年`，滚动 `2021..2026`，
逐段报 `n / IC / t / net30(Q5-Q1)`。
14.4 **不允许**用 OOS 结果反向调整因子、方向、权重或门槛。

---

## 15. 参数扰动（预注册格点）

| 轴 | 格点（首项为主口径） |
|---|---|
| `leg_w` | `30 / 40 / 50` |
| `consol_max` | `20 / 30 / 40 / 50` |
| `retrace_min` | `0.05 / 0.10 / 0.15` |
| `first_rise_pct` | `0.20 / 0.25 / 0.30` |
| `q_groups` | `5 / 10` |

15.1 设计：基线 + 单轴扰动（star 设计），合计 `1 + 2 + 3 + 2 + 2 + 1 = 11` 格。
15.2 每格输出 `COMP_SIG` 与 `COMP_ALL` 的 `IC_mean(h*=5)` 与 `t_NW`。
15.3 `positive_ratio = 格点中「IC 与基线同号 且 |IC| >= 0.005」的比例`。
15.4 判定：`>= 0.60 → stable`；`<= 0.40 → FRAGILE`；其余 `mixed`。
15.5 **禁止寻找单一最优参数**；报告只呈现邻近参数的稳定性。
15.6 `COMP_SIG` 在每个格点内部**独立**用该格点的 IS 段筛选因子（不得跨格复用）。

---

## 16. 分层研究（条件 IC）

对 `COMP_ALL` 与 `COMP_SIG` 分别计算各分层内的 `IC_mean(h*=5)` 与 `t_NW`：

| 维度 | 层 |
|---|---|
| 第一波强度 | `25-40%` / `40-60%` / `>60%` |
| 回撤深度 | `<10%` / `10-20%` / `20-30%` / `>30%` |
| 右底质量 | `右底>左底` / `右底≈左底` / `右底<左底` |
| 量能 | `缩量` / `正常` / `放量` |
| 行业强度 | `强行业` / `普通行业` / `弱行业` |
| 市场 | `上涨` / `震荡` / `下跌` |
| 板块 | `MAIN` / `GEM` / `STAR` |

目标：**稳定有效的区域**，不是 IC 最高的某一层。

---

## 17. 样本选择偏差防护（漏斗）

必须完整输出，**不得只保留显著或有效样本**：

```
主升样本 → 进入整理(CTX) → 右底已形成(R1) → U_FACTOR → READY → 二次突破
```

并额外输出：

```
U_FACTOR 单元格数 / 唯一整合理期数 / 平均每期出现次数 / 平均每日截面只数
READY 占 U_FACTOR 比例 / FAILED 占 U_FACTOR 比例
每段(IS/VALID/OOS)的 U_FACTOR 单元格数与有效交易日数
```

---

## 18. 判决框架

对 `COMP_ALL` 与 `COMP_SIG` 各输出四值之一：`ROBUST / PROMISING / FRAGILE / NO EDGE`。

12 个布尔位（`h* = T+5`）：

```
g1  IS IC 符号符合 §7 预期方向        g7  参数扰动 stable (positive_ratio >= 0.60)
g2  IS |t_NW| >= 2.0                 g8  三种市场状态 IC 同号
g3  VALID IC 同号 且 |t_WF| >= 1.5   g9  N1 null p < 0.05
g4  OOS IC 同号                      g10 R-DEDUP 口径 IC 同号且 |IC| >= 0.005
g5  分层单调性 >= 0.80               g11 控制动量后 β(COMP) 的 t_NW >= 2.0
g6  30bp 成本后 Q5-Q1 > 0            g12 去掉任一单因子后 COMP IC 保留 >= 0.70
```

`ROBUST = 12/12`；`PROMISING = 9..11`；`FRAGILE = 5..8` 或参数位为 FRAGILE；
`NO EDGE <= 4`。

单因子判决（`P1..P14`）用同一框架的前 10 位（`g11/g12` 不适用于单因子，
以 `g10b = R-DEDUP 显著性` 替代），并额外标注：

```
SIGN_CONFLICT     实测方向与 §7 预期相反
UNSTABLE          IS 显著但 VALID/OOS 反号
EPISODE_SENSITIVE R-ALL 与 R-DEDUP 不一致
```

---

## 19. 最终报告必须回答的 10 个问题

```
Q1  整理完成度能否被量化为连续因子？（覆盖度、分布、重复度）
Q2  哪些单因子在 IS/VALID/OOS 三段同号且显著？
Q3  量能收缩因子是否提供增量信息？（是否只是"缩量=卖压减少"的错觉）
Q4  波动收敛因子是否提供增量信息？
Q5  MA20/MA60 结构因子是否提供增量信息？
Q6  相对行业强度因子是否提供增量信息？
Q7  右底质量 / 接近阻力因子是否提供增量信息？
Q8  等权合成 COMP 是否优于最强单因子与动量基准？
Q9  控制动量后 COMP 的增量 IC / ΔR² 是多少？是否显著？
Q10 COMP 的预测力在哪些分层与市场状态下稳定，在哪些环境下失效？
```

---

## 20. 最终结论格式（**必须逐字采用**）

```
【研究结论】

1. 整理完成度可否量化为连续因子：   PASS / FAIL / MIXED
2. 单因子横截面预测力：             PASS / FAIL / MIXED
3. 合成因子 COMP 相对动量基准：     PASS / FAIL / MIXED
4. 最稳定的因子与区间：             仅报告 IS/VALID/OOS 三段同号区间
5. 主要失效环境：                   明确列出
6. 成本后：                         0/15/30/50bp 下是否仍成立
7. OOS：                            是否通过
8. 参数稳定性：                     是否通过
9. 年度稳定性：                     是否通过
10. 最终状态：                      RESEARCH_VALIDATED / PARTIALLY_VALIDATED / FAILED
```

禁止输出「策略有效」「最强形态」「推荐买入」「稳赚」「胜率神器」等表述。
必须区分**因子本身有效**与**因子只是 Momentum / Volume 的代理变量**。

---

## 21. 运行与产物

脚本：

| 文件 | 职责 |
|---|---|
| `slg2_common.py` | 公共层：只读复用 `slg_common` + 因子层 / IC 层 / NW 修正 / Null |
| `slg2_run.py` | 主运行：全部计算与 CSV |
| `slg2_report.py` | 报告 + 交付校验（必须 `EXIT 0`） |

运行顺序：`python slg2_run.py` → `python slg2_report.py`

必产产物（10）：

```
second_leg2_factor_ic.csv
second_leg2_factor_quantile.csv
second_leg2_factor_composite.csv
second_leg2_factor_incremental.csv
second_leg2_factor_oos.csv
second_leg2_factor_param.csv
second_leg2_factor_null.csv
second_leg2_factor_subgroup.csv
second_leg2_factor_funnel.csv
H_SLG_02_REPORT.md
```

补充产物：`H_SLG_02_RESULTS.json`、`H_SLG_02_SUMMARY.json`、
`H_SLG_02_SPEC.md`、`H_SLG_02_FREEZE.md`。

`future_column_scan` 检查项（任一失败 → `FATAL` → `EXIT 3`）：

1. `truncation_invariance`：面板截断 120 会话后，因子列在不受前向窗口影响的日期上逐位一致。
2. `backward_window_proof`：`neckline = high[peak_day]` 且 `peak_off >= CONS_MIN`。
3. `forward_window_isolation`：因子列与 label 列集合不相交。
4. `label_columns_marked`：所有消费 `t > T` 信息的列登记在 `LABEL_COLUMNS`。

退出码：`verify_spec` 不一致 → `EXIT 2`；`future_column_scan` 失败 → `EXIT 3`；正常 → `EXIT 0`。

---

## 22. 不变量

1. `SPEC_SHA256` 必须与冻结记录一致，否则 `EXIT 2`。
2. 所有阈值只在 `PREREG` 中定义，禁止散落硬编码。
3. **禁止权重优化**：`COMP` 只能等权。
4. **禁止用 VALID/OOS 段筛选因子或确定方向**。
5. 禁止只展示显著因子；`P1..P16` 全部必须报告，包括预测力为零的。
6. 禁止把「缩量」直接解释为「卖压衰减」。
7. 禁止因样本量小而人为扩池，禁止为得到 PASS 而反复调参。
8. 禁止修改 `slg_common.py`、禁止修改 `H-SLG-01` 的产物与结论。
9. 禁止修改现有交易系统核心逻辑。
10. `trading_authorization = NO`：本阶段禁止把任何因子转化为实盘 BUY 信号；
    Entry / Exit / Position sizing / Execution 属独立第二阶段。
