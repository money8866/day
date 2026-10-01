# H_RBP_01_RESEARCH_SPEC

> Research ID: **H-RBP-01**
> Title: High-Volume Event 后回撤买入（Retracement-Based Entry）
> Version: **1.0**
> Created: 2026-09-30
> Status: **FROZEN —— 在任何结果被观察之前写定**

---

## 0. 冻结声明（Pre-registration）

本文件在跑出任何统计量之前写成。以下每一条都冻结：

* 事件定义、回撤定义、结构定义、转强定义、入场/出场定义；
* 分组 A/B/C/D 的构造方式；
* Null Model、Counterfactual、参数矩阵、OOS、Walk-Forward 的口径；
* 通过/失败判据。

**唯一允许的修改**：发现"实现与本文不符"时的 bug 修复，且必须在 `H_RBP_01_RESULTS.md` 中逐条登记（原写法 / 实际写法 / 修正时间 / 是否影响已观察结果）。

**禁止**：在看到 Test / OOS 数字之后回改阈值、改窗口、改分组、增删条件。

---

## 1. 与前序研究的关系

HVE-Research V1 已完成严格验证并被 **ARCHIVE**：

| 项目 | 结果 |
|---|---|
| 裸 HVE T+10 | **−2.46%** |
| 随机事件基准 T+10 | **+0.63%** |
| W1/W2/W3 条件链 | 无单调增益（+0.68% → +0.31% → +0.19%） |
| 四种突破定义 | 均无稳健净 Alpha（PF 0.93~1.01） |
| Entry-5/−3/−1 | **selection bias**，不可作交易证据 |

本研究**不是** HVE V1.1，**不是** W7 优化。

> HVE 在本研究中**降级为事件锚点（observation anchor）**，不作为 Alpha 来源，也不假设它有效。

**一致性约束**：

* 不修改 `research/hve/` 下的任何既有文件（`hve_common.py` / `hve_events.py` / `hve_path.py` / `hve_hvt.py` / `hve_w7.py`）；
* 不修改 `research/fundamental_surprise_alpha/data/` 下的任何 cache；
* 本模块以**只读**方式 import `hve_common`，复用它已验证的数据层与统计原语；
* 本研究的全部产物落在 `research/h_rbp/` 目录内。

---

## 2. Hypothesis / Null Hypothesis

### H1（主假设）

> 一个股票经历异常放量事件后，若其价格发生**可实时识别**的回撤，且回撤过程中结构未被破坏，随后出现**可实时识别**的重新转强信号，则该信号包含**独立于 HVE 本身**的可交易 Alpha。

### H0（零假设）

> 该信号序列的 T+3/T+5/T+10/T+20 平均收益与"同股票同市场时期的随机入场"无差别（差值的 cluster-bootstrap 95% CI 覆盖 0）。

### 增量分解（真正的检验问题）

不是"谁收益高"，而是**每增加一个信息条件是否产生稳定增量**：

```
A  Random Event
B  HVE → Random Entry
C  HVE → Retracement
D  HVE → Retracement → Structure → Re-strength

A vs B  : HVE 本身有信息吗？
B vs C  : 回撤本身有信息吗？
C vs D  : 结构 + 转强有信息吗？
```

---

## 3. 样本范围（Universe & Period）

| 项 | 值 | 说明 |
|---|---|---|
| 日历 | 复用 `hve_common.load_calendar()` 的交易日历 | N = 2120，20180102 ~ 20260924 |
| 价格面板 | 复用 `hve_common.build_grid()` 的缓存（qfq 复权 OHLC / vol / amount / turnover） | **不重建** |
| 技术指标 | 复用 `hve_common.build_indicators()` 的缓存 | **不重建** |
| 样本起点 | `20190101`（事件日） | 与 HVE V1 `study_start` 一致 |
| 最小上市天数 | 250 个交易日 | `hve_common.eligibility()` |
| 剔除 | ST、退市名、≥60 日停牌洞、无真实成交 | `hve_common.eligibility()` |
| 板块 | MAIN / GEM / STAR | **剔除 BSE（北交所）**：按预注册交易约束不可交易 |
| 分期 | IS 2019–2022 / VALID 2023–2024 / OOS 2025–2026 | 与 HVE V1 `Q_IS/Q_VALID/Q_OOS` 一致 |

**右端截断规则（预注册）**：一笔交易必须满足 `entry_idx + 20 <= N-1`，即 T+20 完整可测，才进入主样本。该规则只依赖日历位置，不依赖任何结果。被丢弃的事件数必须如实报告（`n_drop_right_edge`）。

---

## 4. Event Definition（t0）

沿用任务书给定的基础事件（**HVE 仅作锚点**）：

```
VR20_excl = Volume[t] / mean(Volume[t-20 : t-1])      # 分母不含当日
CLV       = (Close[t] - Low[t]) / (High[t] - Low[t])

基础 HVE（H_RBP_EVENT）:
    VR20_excl      >= 2.00
AND daily_return   >= 0.02         # r1 = Close[t]/Close[t-1] - 1
AND Close[t]        > Open[t]
AND CLV            >= 0.65
```

**口径说明（必须登记）**：

* 分母 `mean(Volume[t-20 : t-1])` 按任务书字面实现，即**前 20 日成交量均值，不含当日**；这与 HVE V1 的 `ind['VR20']`（分母含当日）不同，属本研究的独立定义，不作为对 HVE V1 的复现。
* 该事件定义比 HVE V1 的 `HVE_C`（pct≥3%、CLV≥0.70、body≥0.30、Close>MA20）更宽松，**故意如此**：本研究需要足够的事件锚点密度来支撑回撤分组。

**事件去重（独立性）**：

```
同一股票，相邻事件间隔 <= 5 个交易日视为同一簇
簇内代表 = amount 最大者（anchor = max_amount）
```

与 HVE V1 的 `cluster_gap_primary=5 / cluster_anchor_primary=max_amount` 保持一致，目的是让"一个 event"是一个**独立事件**（§十二 One Event / One Trade 的前提）。

**event_date = t0**；观察从 **t0+1** 开始。事件日当天及之前的任何数据都不允许出现在信号选择里（只允许作为滚动统计的输入）。

---

## 5. Observation Window

```
W = 60        # t0 之后用于观察回撤 + 转强的会话数
观察区间： t in [t0+1, t0+60]
```

与 HVE V1 `w7_confirm_max_wait=60` 一致。窗口内没有出现完整信号 → 该事件作废（no-show），**不持有现金、不递延**。

---

## 6. Retracement Definition

### 6.1 事件后最高价（严格 PIT）

```
event_high_to_date(t) = max( High[t0 .. t] )      # 只用到 t 为止的数据
drawdown(t)           = Close[t] / event_high_to_date(t) - 1
```

不使用任何未来 pivot、不使用事件后最高价的最终值。

### 6.2 预注册分组（研究分组，非优化目标）

```
R1 : drawdown ∈ [-5%, -3%)
R2 : drawdown ∈ [-8%, -5%)
R3 : drawdown ∈ [-12%, -8%)
R4 : drawdown ∈ [-15%, -12%)
```

`retrace_date` = 窗口内**第一个**满足所属区间条件的交易日（first touch，不挑最优）。

### 6.3 参数矩阵用的单向阈值

```
R(thr) : drawdown(t) <= -thr ,  thr ∈ {3, 5, 8, 10, 12, 15} (%)
```

只用于 §二十 参数稳定性矩阵，**不参与主结果**。

---

## 7. Structure Definition（结构保持）

只允许实时可观察状态。全部在**判定当日收盘**计算。

| 代号 | 定义 | 名称 |
|---|---|---|
| S1 | `Close[t] >= MA20[t]` | `MA20` |
| S2 | `Low[t] >= Low[t0]` | `EVENT_LOW` |
| S3 | `Close[t] >= MA20[t]` **and** `Low[t] >= Low[t0]` | `MA20+EVENT_LOW` |
| S4 | `Close[t] >= struct_low(t)` | `RECENT_SWING_LOW` |

**S4 的 `struct_low(t)` —— 最近结构低点，PIT 定义**：

```
k = 3
low_j 是 pivot low  ⇔  Low[j] == min( Low[j-3 .. j+3] )
pivot 在 j 日形成，但直到 j+3 日才能确认
struct_low(t) = max{ Low[j] : t0 <= j <= t-3 , low_j 是 pivot low }
                若不存在，则取 Low[t0]
```

* 只用 t-3 及之前的数据 → **无未来函数**；
* 不使用任何"事后才成立的 pivot"。

---

## 8. Re-strength Definition（重新转强）

预注册三版，全部只用 signal_date 当日及以前的数据：

| 代号 | 定义 | 名称 |
|---|---|---|
| RS1 | `Close[t] > max( High[t-3 .. t-1] )` | `3D` |
| RS2 | `Close[t] > max( High[t-5 .. t-1] )` | `5D` |
| RS3 | `Close[t] > MA20[t]` **and** `Close[t] > Close[t-1]` | `MA20` |

**量能确认（独立实验，不进入主模型）**：

```
VOLCONF : Volume[t] / mean(Volume[t-20 : t-1]) >= 1.0
```

只作为附加门控单独报告"加 / 不加"的差异。

---

## 9. Signal / Entry / Execution / Exit

### 9.1 Signal（收盘后才成立）

```
Group C 信号：
    signal_date = retrace_date                     （回撤当晚收盘确认）

Group D 信号（主口径）：
    structure 门控在 retrace_date 生效  → structure_ok(retrace_date) == True
    signal_date = min{ t : retrace_date <= t <= t0+W , restrength_ok(t) }
    要求 t 存在，否则该事件作废

D 变体 D_STRUCT_AT_SIGNAL（矩阵维度之一）：
    structure 门控改为在 signal_date 生效
```

**关键纪律**：`signal_date` 只能在当日收盘后才知道 → 一律 T+1 开盘执行。

### 9.2 Entry

```
entry_idx   = signal_date + 1
entry_price = Open[entry_idx]           # qfq 复权开盘
```

* **禁止**用 signal 当日收盘价作为主结果的无摩擦成交价（只允许作为敏感性对照列出现在 trade log）。
* A 股 T+1：`entry_idx >= t0+2`，因此不存在"信号当日买入"的可能。

### 9.3 Execution Audit（每笔逐条记录）

| 标志 | 规则 | 处理 |
|---|---|---|
| `entry_not_traded` | `traded[s, entry_idx] == False` | 标记，**剔除出主账本**，单独计数 |
| `entry_oneword_limit` | `oneword[s, entry_idx] == True`（一字板，无法成交） | 同上 |
| `entry_limit_up_close` | `limup[s, entry_idx] == True`（收盘涨停，记录用） | 仅标记，不剔除 |
| `entry_beyond_panel` | `entry_idx > N-1` | 事件作废 |

剔除后的成交率必须报告。

### 9.4 Exit

```
固定持有，无止损：
    T+h 出场 = Close[entry_idx + h]     h ∈ {3, 5, 10, 20}

主口径收益：
    r_h = Close[entry_idx + h] / Open[entry_idx] - 1
对照列（仅记录，不进主统计）：
    r_h_ec = Close[entry_idx + h] / Close[entry_idx] - 1
```

主周期 = **T+10**。所有周期统一报告，不允许事后只报最好的周期。

---

## 10. 实验组（Groups）

| 组 | 构造 | 含义 |
|---|---|---|
| **A** Random Event | 对每个事件，在**同一股票、同一自然年**内均匀抽取一个合格交易日作对照事件的 t0*，其余流程与该组要求完全一致（→ 入场 = t0*+1 开盘） | Null：一切是否只是"随机某天的漂移" |
| **B** HVE → Random Entry | 事件成立后，不要求回撤；在 `[t0+1, t0+59]` 均匀抽一个合格交易日 u，入场 = `Open[u+1]` | HVE 后是否存在简单时间效应 |
| **C** HVE → Retracement | 满足 R1/R2/R3/R4 之一 → `Open[retrace_date+1]` | 回撤本身是否提供增量 Alpha |
| **D** HVE → Retracement → Structure → Re-strength | §9.1 主口径 → `Open[signal_date+1]` | 结构 + 转强是否相对"单纯回撤"提供增量 |

**组间同质性约束**：A/B/C/D 使用同一股票池、同一时期、同一流动性约束、同一交易成本、同一持有期、同一右端截断规则。

---

## 11. One Event / One Trade

```
一个 event 生命周期内最多产生一笔交易。
若出现多个候选 retracement / 多个候选 signal_date：
    取第一个满足完整信号条件的日期（first qualifying）。
交易后该 event 生命周期结束。
同一股票必须等到新的独立 Event（§4 去重后）才能重新进入研究。
```

---

## 12. Null Models（三组）

| ID | 定义 |
|---|---|
| **Null 1** | 随机事件日：从合格面板中随机抽 (股票, 日期)，抽样后**按年匹配**事件年份分布 |
| **Null 2** | HVE 后随机入场：= Group B 的构造 |
| **Null 3** | 保持 `stock + month + holding period` 相同的随机入场（同月末粒度配对） |

统一报告：`Observed / Null Mean / Null Median / 95% CI / Excess Return`。

重复次数：`n_perm = 1000`，随机种子 `seed = 20261101`（冻结）。

---

## 13. Counterfactual（必须完成）

对每个真实 Signal 构造两个对照：

```
CF-1  同一 Event 内的随机日期                 （回答："是回撤，还是只是过了一段时间？"）
CF-2  retrace_date 之后的窗口内随机日期        （回答："是转强，还是回撤后随便哪天？"）
```

两者均可实时构造（只使用 t0..t0+W 内部信息），结果与真实 Signal 的 T+3/5/10/20 逐一比较。

---

## 14. Benchmark / Costs

* 主基准：**Group A**（配对随机事件）。
* 辅基准：沪深 300 买入持有（`FS_DATA/index_panel.parquet`，000300.SH，只读）。
* 成本阶梯（round trip，总双边）：

```
0bp / 10bp / 20bp / 30bp / 50bp
主结果 = 30bp 净额（Net30）
```

成本口径与 HVE V1 一致（佣金 2.5bp/边 + 印花 5bp/卖 + 滑点冲击 7.5bp/边 ≈ 30bp），**不因结果不好而下调**。

---

## 15. 统计量

每个组、每个参数格、每个周期 `h ∈ {3,5,10,20}`：

```
N, Mean, Median, Win Rate, Profit Factor,
Mean Win, Mean Loss, Payoff Ratio, Max Drawdown(组合口径),
Gross / Net10 / Net30 / Net50
```

**尾部检查**：`Full / Leave Top 1% / Leave Top 5% / Leave Top 10%` 的平均收益对比 + `tail_share`（Top 5% 正收益占总盈利比）。若去掉极少数赢家后 Alpha 消失 → 标记 `TAIL_DEPENDENT`。

**统计检验**：

* 按**自然月做 cluster bootstrap**（重抽样月份块，B = 1000，seed 冻结），给出均值 95% 分位 CI；
* 差值检验（同一 bootstrap 重抽框架）：`C−B`、`D−C`、`C−A`、`D−A`；
* 另做 stationary block bootstrap（block = 20 会话）作为自相关稳健性对照；
* 多重比较：BH-FDR（q=0.05）横跨参数矩阵的 `D−C` 检验；记录 `number_of_tests`。

---

## 16. Robustness

### 16.1 年份 OOS

按年份逐年报告 `N / Mean T+5 / Mean T+10 / Win Rate / PF / Net30`。观察是否存在**单一年份贡献绝大部分收益**。

### 16.2 分期

IS 2019–2022 / VALID 2023–2024 / OOS 2025–2026，分别报告。

### 16.3 Walk-Forward

```
滚动窗口： Train 3 年 → Validation 1 年 → Test 1 年，逐年推进（2019 → 2026）
参数只能在 Train/Validation 决定（在 54 格矩阵上选 D−C 平均净收益最高者）
Test 完全冻结，禁止看 Test 后回改
```

### 16.4 参数稳定性矩阵（预注册全因子）

```
Retracement : R(3) R(5) R(8) R(10) R(12) R(15)      # 6
Structure   : S1(MA20)  S2(EVENT_LOW)  S3(两者)      # 3
             × struct_at ∈ {RETRACE, SIGNAL}         # 2
Re-strength : RS1(3D)  RS2(5D)  RS3(MA20)            # 3
```

寻找的是**平台（plateau）**而非最优点。若只有孤立的某一格为正、邻域≈0 → 标记 `PARAMETER_FRAGILE`。

### 16.5 Regime 分解

按 HVE V1 预注册口径（只读 `hve_common.PREREG['regime_rule']`）：

```
BULL  : 事件日 CSI300 close > MA60 且 60 日收益 > +5%
BEAR  : 事件日 CSI300 close < MA60 且 60 日收益 < -5%
RANGE : 其余
```

报告 `N / T+5 / T+10 / Win Rate / PF`。**Regime 只作异质性分析，不允许事后把某个 regime 加入筛选条件。**

### 16.6 禁止优化规则

任何"表现最好"的参数，必须先完成：全矩阵 → OOS → Walk-Forward → 年度稳定 → Regime 稳定 → Leave-top-tail → 成本敏感性，**然后**才允许判断。

---

## 17. Selection Bias Audit（8 项，逐笔 + 逐项）

生成 `H_RBP_01_SELECTION_AUDIT.csv`（逐笔）与 `selection_bias_audit.md`（8 项 PASS/FAIL）：

| # | 检查项 | PASS 条件 |
|---|---|---|
| A1 | 入场是否依赖未来突破 | `signal_date` 的所有条件只用 ≤ signal_date 数据；`signal_date < future_breakout_date`（若该 event 后续存在 breakout） |
| A2 | 入场是否依赖未来最高价 | `event_high_to_date` 为 expanding max，逐笔核验 |
| A3 | 入场是否依赖未来最低价 | 同上（S4 pivot 的确认滞后 ≥3 日） |
| A4 | 参数是否根据 Test 结果调整 | 参数矩阵与阈值来自本 spec，Test 冻结 |
| A5 | 是否只保留成功案例 | `n_event_total / n_trade` 覆盖率如实体检，no-show 计数必须报告 |
| A6 | 是否因为结果不好而增加条件 | 条件集合在 spec 中冻结，运行后不得新增 |
| A7 | 是否存在 overlapping event | 去重后同一股票的事件间隔 > 5 会话；同组内不出现重叠持仓（如出现，逐笔列出） |
| A8 | 是否使用未来 pivot | S4 使用 `j <= t-3` 的已确认 pivot |

`signal_audit` 字段（`selection_audit.csv`）：

```
ts_code, event_date, retrace_date, structure_date, signal_date, entry_date,
entry_open, signal_to_entry_lag, future_breakout_date(only as label),
future_max_return(only as label), future_T10_return(only as label)
```

> `future_*` 字段**只允许作为 outcome label 落盘**，禁止出现在任何 signal 选择表达式中。代码中以 `_LABEL_ONLY` 后缀命名并在审计中显式断言未被用于选择。

---

## 18. Gates（通过判据）与 Failure Criteria

全部 10 条同时满足才 **PASS**：

| Gate | 判据 |
|---|---|
| G1 | 相对 Null Model 存在稳定差异（cluster-bootstrap 95% CI 不覆盖 0） |
| G2 | 30bp 净额后仍然存在 |
| G3 | OOS 仍然存在（2025–2026 方向一致且不劣化到 0 以下） |
| G4 | 至少多数年份/市场阶段得到支持（≥ 半数年份同向） |
| G5 | 不是 Top 1%/5% 极端收益驱动（Leave-top-5% 后 Alpha 不消失） |
| G6 | 参数存在合理稳定区间（存在连续 ≥ 3 格邻域同向为正的平台） |
| G7 | 无 selection bias（§17 全部 PASS） |
| G8 | 无未来函数（§17 A1–A3 全 PASS） |
| G9 | C 相对 B 有明确增量（Retracement Alpha） |
| G10 | 若保留 Re-strength，则 D 相对 C 必须证明增量；否则**删除 Re-strength 条件** |

**Failure Criteria（任一成立即 FAIL）**：

```
C ≈ B 且 D ≈ C                （整个 H-RBP 假设未获支持）
OOS 后 Alpha 消失
30bp 后 Alpha 消失
仅 Top 1%/5% 驱动
仅孤立参数格为正（PARAMETER_FRAGILE）
OOS 与 IS 符号相反
```

## 19. 结论分类（只允许三选一）

* **PASS**：稳健、可交易、OOS 可重复的增量 Alpha → 允许进入下一阶段。
* **CONDITIONAL**：部分成立（如 Retracement 有效、Re-strength 无增量）→ 保留有效部分，删除无效部分。
* **FAIL**：→ **H-RBP-01 ARCHIVE**，禁止围绕同一 Hypothesis 继续调参；如需继续，必须启用新的 Hypothesis ID。

---

## 20. 交付物

```
H_RBP_01_RESEARCH_SPEC.md      本文
H_RBP_01_RESULTS.md            结果报告（§31 五段式）
H_RBP_01_PARAMETER_GRID.csv    参数矩阵（含 struct_at 维度）
H_RBP_01_OOS.csv               分期 / 年度
H_RBP_01_WALK_FORWARD.csv      Walk-Forward
H_RBP_01_NULL_MODEL.csv        三组 Null + Counterfactual
H_RBP_01_SELECTION_AUDIT.csv   逐笔审计
H_RBP_01_TRADE_LOG.csv         全部交易明细
H_RBP_01_SUMMARY.json          机器可读汇总 + Gates
```

外加内部中间件（不属交付物，落在 `research/h_rbp/data/`）。

---

## 21. 研究纪律（重申）

> **本研究的任务是尽可能公平地尝试证伪 H-RBP-01。**

* 不修改假设直到它变正；
* 不把极窄参数包装成策略；
* 若 Retracement 有效而 Re-strength 无效 → **删掉 Re-strength**；
* 若全部无效 → **ARCHIVE**；
* 最终要回答的是：**"事件后的回撤是否真的包含可交易的增量信息？"**
