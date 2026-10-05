# H-ZZ2K-A1 SPEC v1.1 — 中证2000增强 Alpha V1 科研 Prompt / SPEC
## 五类 Alpha 分组正交化 · IC · 分层 · OOS · Walk-Forward · 成本 · Regime · 独立增量筛选

Research ID: `H-ZZ2K-A1`　Version: `1.1`　Created: `2026-10-04`　Revised: `2026-10-04`
Hypothesis ID space: `H-ZZ2K-A1-*`
范式参照: `H-SLG-02`（`h_slg/H_SLG_02_SPEC.md`）、`fundamental_surprise_alpha/fs_common.py`、`hve/hve_common.py`
修订说明: V1.1 由**数据现实**驱动（中证2000 成分仅 2023-09 起），修订 §2.1 / §4 / §5 / §14 / §16 / §19。
          研究**尚未执行、未产生任何结果**，故不构成回填优化。详见 §20 修订记录。

本文件是**唯一口径来源**，同时作为可执行的「科研 Prompt」。任何代码不得偏离本文件；
如需修改必须执行 `V1 冻结 → 输出结果 → 提出新 Hypothesis → V2`，**禁止回填优化**。

---

## §0 执行指令块（Prompt 正文，供执行方逐条遵守）

```
角色    : 量化因子研究员
任务    : 对中证2000 股票池，把 5 类 Alpha（Value-Quality-Growth / 短期反转 /
          Residual Momentum / 技术量价 / Residual Volatility）拆开，
          逐类完成「正交化 → IC → 分层 → OOS → Walk-Forward → 成本 → Regime」全链路检验，
          最后自动筛出真正具备【独立增量】的因子。
硬约束  : 1) 只用本地 Tushare 缓存，禁止引入新数据源
          2) 因子只用 <= t 信息，PIT 财报锚点 = ann_date
          3) 禁止权重优化；组间合成只用预注册权重
          4) 禁止用 VALID/OOS 段筛选因子、定方向、调门槛
          5) 全部因子必须报告（含预测力为零的），禁止只报显著因子
          6) 本阶段 trading_authorization = NO，不产出任何 BUY 信号
判定    : 每因子/每组合输出 11/12 位 g1..g12，映射 ROBUST / PROMISING / FRAGILE / NO EDGE
交付    : zz2k_common.py + zz2k_run.py + zz2k_report.py，`python zz2k_report.py` 必须 EXIT 0
```

---

## §1 研究定位与目标

### 1.1 研究问题

> **在中证2000 股票池内，把五类经典 Alpha 分别正交化后，
> 哪些因子对未来 T+5 / T+10 / T+20 的横截面超额收益具有
> 稳定、单调、成本后仍存活、且相对其余四类的【独立增量】？**

三个子问题：

* **G1**：每类 Alpha 内部，哪些因子在类内正交化后仍保留 IC（类内去冗余）？
* **G2**：五类合成因子相对「规模 + 市场Beta + 行业」基线是否有增量 IC？
* **G3**：对「其余四类 + 基线」正交化后，哪一类的残差仍显著（真正独立增量）？

### 1.2 为什么是「分开做」

单一「多因子大合成」会把两个问题混淆：

| 混淆 | 后果 | 本 SPEC 的处理 |
|---|---|---|
| 类内共线 | 同族因子（EP/BP/SP）互相稀释，等权后被高方差成员绑架 | §6.2 类内**对称正交化**后再合成 |
| 类间共线 | 反转与残差动量、技术量价与残差波动高度相关，合成后无法归因 | §6.3 类间正交化 + §11 增量回归 |
| 风格漂移 | 财务类因子在短周期失效，被量价类掩盖 | §10 Regime 分层 + §11 逐类 ΔR² |

### 1.3 明确不做什么

本研究**不研究**：择时信号、买入/卖出规则、仓位与执行、行业轮动策略、
事件驱动、机器学习黑箱打分。本研究**不产出任何可交易信号**。

---

## §2 数据原则

### 2.1 数据来源（全部为本地缓存，路径固定，**已核实可用**）

| 用途 | 来源 | 覆盖 | 状态 |
|---|---|---|---|
| 日线面板 OHLC(qfq)/vol/amount/turnover/pct_chg/traded/st/delist/涨跌停/board | `hve_common.build_grid()` | S=5816 × N=2120（`20180102..20260924`） | ✅ |
| 估值/规模/换手 | `fs_common.DBASIC_COLS` → `basic_panel.parquet`（经 `mce_common.load_size_mv`） | `20230103..20260924`（DB `daily_basic_cache`） | ✅ |
| 财务三表 + 指标 | `fs_build_fund.py` 的 `INC_WANT` / `BAL_WANT` / `CF_WANT` / `FI_WANT`（parquet 前缀族） | 由 `ann_date` PIT 决定 | ✅ |
| 行业（PIT 申万一级） | `D:\mystock\cache_daily\industry\sw_industry_map.csv` → `hef_common.industry_at_fast` | — | ✅ |
| 交易日历 | `calendar.parquet` → `hef_common.load_calendar` | — | ✅ |
| **市场因子**（回归用） | `000300.SH` ← `FS_DATA/index_panel.parquet`（**该文件只含 000300.SH**） | `20180102..20260924` | ✅ |
| **基准/超额/Regime** | **`932000.CSI` ← `stock_data.db` 表 `index_daily_cache`** | `20210104..20260924`（1390 行） | ✅ |
| 基准稳健对照 | `000852.SH`（中证1000）← 同表 `index_daily_cache` | `20210104..20260924` | ✅ |
| **中证2000 成分（PIT）** | `data/zz2k_members.parquet` ← `zz2k_fetch_members.py` 取 `index_weight`（**已落盘**） | `20230831..20260831`（37 个月度快照） | ⚠ 受限 |
| 对照宽基成分（PIT） | `data/bench_members.parquet`（`000300.SH / 000905.SH / 000852.SH`，**已落盘**） | `20210129..20260831`（每月 1 快照） | ✅ |

> **成分数据硬约束（本次已核实）**：`932000.CSI` 的**发布日期为 2023-08-11**，
> Tushare 不提供发布日之前的成分回溯 → 中证2000 成分 PIT **仅覆盖 2023-09 起（37 个月度快照）**。
> 这会把 `U-ZZ2K` 主口径的研究区间压缩到约 730 交易日，因此本研究采用**双口径并行**（§4.2 / §5）。
> 取数脚本 `zz2k_fetch_members.py` 走 `index_weight` 按月分片（单次返回上限 7000 行，必须绕开），
> **不可使用 `index_member_all`**（已核实它是申万行业接口，传入 `932000.CSI` 返回 0 行）。

> **关键口径提示（必须照做）**：`index_panel.parquet` **不包含** `932000.CSI`；
> 因此 `mce_common.index_ret` / `mce_common.regime_ma` **不能**直接用于中证2000 基准，
> 必须直读 `stock_data.db` 的 `index_daily_cache`。禁止改写 `mce_common.py`，请在 `zz2k_common.py` 内自建读取函数。

### 2.2 禁止事项

1. **禁止**临时下载其他行情源（TDX / AkShare / Wind / 同花顺 / 人工挑股）。
2. **禁止**修改 `hve_common.py`、`fs_common.py`、`mce_common.py`、`hef_common.py`（只读 import）。
3. 字段缺失必须**如实标注**，不得悄悄替代数据源或插补。
4. 复权口径 `qfq`；`prev_close := close[t-1]`（面板内 `qfq_close` 派生）。
5. 本研究为独立科研模块，**禁止**修改现有交易系统核心逻辑。

### 2.3 财务字段清单（Value/Quality/Growth 原料，全部来自 `FI_WANT` / 三表）

```
FI_WANT = profit_dedt, roe, roe_dt, grossprofit_margin, netprofit_margin,
          ocf_to_sales, roic, debt_to_assets, ebitda, netdebt, ocfps, bps, eps,
          assets_yoy, eqt_yoy, op_yoy, netprofit_yoy, dt_netprofit_yoy,
          tr_yoy, or_yoy, q_roe, q_dt_roe, q_npta, q_ocf_to_sales
INC_WANT= revenue, total_revenue, n_income, n_income_attr_p, total_cogs,
          operate_profit, rd_exp
BAL_WANT= total_assets, inventories, accounts_receiv, goodwill, fix_assets,
          intan_assets, total_hldr_eqy_exc_min_int
CF_WANT = n_cashflow_act, c_pay_acq_const_fiolta, n_cash_flows_fnc_act,
          n_cashflow_inv_act
```

---

## §3 严格防止未来函数

3.1 **因子**只在 `t` 日及之前的信息上计算；`t` 日收盘后即可得值，`t+1` 日开盘方可交易。
本研究只做**统计检验**，不做交易执行。

3.2 财务因子的 **PIT 锚点 = `ann_date`**，且仅取 `report_type == 1`（合并报表）：
`factor(t) = 最近一条 ann_date <= t 的报告期数值`。禁止用 `end_date` 做锚点。
增长率类（`*_yoy`）滞后项必须满足 `ann_date_lag < ann_date_current`。

3.3 所有滚动窗口（MA / 量比 / 残差回归 / 波动率 / 正交化）一律为**向后窗口**，
窗口内最后一根 = `t`。

3.4 特别禁止：用未来 N 日最高/最低价确认结构；用未来收益决定样本是否入池；
用未来突破结果定义因子。

3.5 只有 label 列允许消费 `t > T` 信息，且必须登记在 `LABEL_COLUMNS`。

3.6 `future_column_scan`（§16.3）四项须全部通过，任一失败 → `FATAL` → `EXIT 3`。

---

## §4 研究时间与样本

### 4.1 面板与数据可得性（已逐项核实）

* 面板：`S = 5816` 只 × `N = 2120` 交易日（`20180102 .. 20260924`）。
* 价格 `price_panel`（9,785,065 行）与市值/估值 `basic_panel`
  （`total_mv / circ_mv / pe_ttm / pb / ps_ttm / dv_ttm`，9,729,120 行）**均自 2018-01 起密集覆盖**
  （2018 年 817,637 行 / 243 日）→ 无缺口。
* 财务三表 + `fina_indicator`：由 `ann_date` PIT 决定，历史充足。
* 基准行情：`932000.CSI` 自 `20210104` 起（1390 行）；`000300.SH` 自 `20180102` 起。
* **中证2000 成分 PIT 自 `20230831` 起**（发布日 2023-08-11 限制，37 个月度快照）
  → `U-ZZ2K` 主口径的研究区间被**压缩至约 730 交易日**。
* 对照宽基成分 PIT 自 `20210129` 起 → `U-PROXY` 口径可用 2021-01 起（约 1390 日）。
* **结论**：采用**双口径并行** —— 主口径 `U-ZZ2K`（语义正确、样本短），
  对照口径 `U-PROXY`（样本量约 3 倍）。两者**各自独立**划分 IS / VALID / OOS。

### 4.2 时间切分（**时间切分，禁止随机切分**）

**共同边界**：`IS 终点 = 20250630`，`VALID = 20250701..20251231`，`OOS = 20260101..20260924`。
`IS 起点`由各口径的烧入决定：

| 口径 | 烧入 PRE（**不进结论**） | IS | VALID | OOS |
|---|---|---|---|---|
| **`U-ZZ2K`（主口径）** | `20230901 .. 20240131` | `20240201 .. 20250630`（约 340 日） | `20250701 .. 20251231` | `20260101 .. 20260924` |
| **`U-PROXY`（对照口径）** | `20210104 .. 20211231` | `20220101 .. 20250630`（约 850 日） | `20250701 .. 20251231` | `20260101 .. 20260924` |

* 烧入**不是一刀切**：因子有效起点由**各因子自身窗口**决定（最短 20 日；最长 250 日滚动回归 / `beta60`），
  每个因子必须报出**自己的有效起点与有效 `n`**；上表 PRE 只是段划分用的保守下界。
* `burn_in_quarters = 8`：财务因子另需 8 个财报季历史以计算增长率。
* **同一口径内**全部段划分、正交化、合成权重严格按上表；**跨口径不得混用**系数或阈值。
* OOS 两口径均为 `20260101..20260924`（约 8 个月）→ 报告必须显式标注 **`OOS_SHORT`**，
  且**不得**因 OOS 样本小而放宽门槛以求通过。
* 实际区间若与上表不符，报告必须写明实际值。

### 4.3 Walk-Forward

* 主设计：`train 3 年 → test 1 年`，**step = 0.5 年**（半年度滚动）；
  预期 test 窗口 = `2024H1 / 2024H2 / 2025H1 / 2025H2 / 2026H1`。
* `U-ZZ2K` 口径可用历史不足 3 年 → train 长度**自适应降为可用最大历史**（最短 1 年），
  并逐 fold 标注 **`WF_TRAIN_SHORT`**。
* 补充设计：`train 2 年 → test 0.5 年`。
* 每个 fold 内部：正交化方向、基线系数、合成权重**只在该 fold 的 train 段拟合**。
* 两口径的 Walk-Forward **分别**执行，不得跨口径合并。

---

## §5 股票池

### 5.1 U-ZZ2K（主口径，中证2000 成分 PIT）

```
U-ZZ2K(t) = 中证2000 成分(PIT) ∩ traded ∩ ~st ∩ ~delist
            ∩ (上市 >= 250 交易日) ∩ (board != 'BSE') ∩ (t 日有有效 close)
```

* 成分来源：Tushare `pro.index_weight(index_code='932000.CSI', ...)`，**按月分片**拉取
  （单次返回上限 7000 行；932000 每月约 2000 只，大区间调用会被静默截断为最近若干月）。
* **已一次性落盘**为 `data/zz2k_members.parquet`（74,000 行 / 37 个快照 / 每月 2,242~2,381 只）。
  研究阶段**只读本地文件**，不再调用 API。取数脚本 `zz2k_fetch_members.py`（幂等，`--force` 重取）。
* **成分区间 = `20230901 .. 20260924`**：快照为月频（锚点日 `20230831` 起），
  `t` 落在两快照之间时用**前一个快照**做 PIT 前向填充。
* 报告必须披露：中证2000 成分仅 37 个月度快照、发布日 `2023-08-11` 之前无回溯 →
  标注 **`MEMBER_HISTORY_SHORT`**。
* 若本地成分文件缺失 → **禁止**静默替代；必须报 `MEMBER_DATA_MISSING` 并停止 `U-ZZ2K` 口径。

### 5.2 U-PROXY（稳健口径，小盘代理池）

```
U-PROXY(t) = 全A(t) 剔除 沪深300 / 中证500 / 中证1000 成分(PIT)
             ∩ 按 circ_mv 升序排名取第 1801 .. 3800 名（≈2000 只）
             ∩ 同 §5.1 的可交易/非ST/非退市/上市龄/非BSE 约束
```
剔除用成分来自 `data/bench_members.parquet`
（`000300.SH / 000905.SH / 000852.SH`，`20210129` 起，月度快照，PIT 前向填充）。
市值分位用 `basic_panel.parquet` 的 `circ_mv`（PIT，逐日；自 2018-01 起密集覆盖）。

### 5.3 附加口径与披露

1. **北交所（BSE）一律排除**。
2. 退市前历史打印保留；`DELIST_PIT_UNAVAILABLE` 如实标注。
3. **不施加 80 亿市值硬门槛**——中证2000 成分市值多集中于 20–100 亿，
   施加硬门槛会使样本崩塌。市值改为**分层维度**（§10.3 Size 层）呈现，供使用者自行取舍。
4. 报告必须给出：`U-ZZ2K` 与 `U-PROXY` 的每日截面只数、平均只数、两池重合度、
   以及 U1 vs U2 的结论差异（幸存者偏差影响）。
5. **双口径并列是强制项**：每个因子 / 每个 `C_k` / 每个 `C_k^⊥` 必须**同时**给出
   `U-ZZ2K` 与 `U-PROXY` 的 IC、t 值、判决位。
   两者结论不一致 → 标注 **`POOL_SENSITIVE`**，**以 `U-ZZ2K` 为准**（语义正确），
   但必须在报告中并列呈现，不得只报对结论有利的那一个口径。
6. `U-ZZ2K` 的判决位若因样本短而系统性偏低（如 `g7` 参数位、`g12` Walk-Forward 位），
   允许在报告中同时给出「按 `U-PROXY` 同规则计算」的对照判决位，并标注 `WF_TRAIN_SHORT`。

---

## §6 五类 Alpha 因子定义（预注册方向）

**规则**：`+` 表示因子值越大预期未来超额越高；`-` 表示越大预期越低。
**方向在实现前登记，不得在看到结果后翻转。** 实测与预注册冲突必须逐因子标注 `SIGN_CONFLICT`。

全部因子先做 §7.1 的逐日横截面 rank 百分位化（`rank/(n+1) ∈ (0,1)`），再进入后续检验。

### 6.1 组 A1 — Value / Quality / Growth（12 个）

| ID | 因子 | 定义 | 来源 | 预期 |
|---|---|---|---|---|
| A1_V_EP | 盈利收益率 | `1 / pe_ttm` | DBASIC | + |
| A1_V_BP | 账面市值比 | `1 / pb` | DBASIC | + |
| A1_V_SP | 营收市值比 | `1 / ps_ttm` | DBASIC | + |
| A1_V_DP | 股息率 | `dv_ttm` | DBASIC | + |
| A1_Q_ROE | 净资产收益率 | `roe` | FI_WANT | + |
| A1_Q_ROIC | 投入资本回报率 | `roic` | FI_WANT | + |
| A1_Q_GM | 毛利率 | `grossprofit_margin` | FI_WANT | + |
| A1_Q_OCF | 经营现金流/营收 | `ocf_to_sales` | FI_WANT | + |
| A1_Q_LEV | 低杠杆 | `- debt_to_assets` | FI_WANT | − |
| A1_G_NPY | 净利润增速 | `netprofit_yoy` | FI_WANT | + |
| A1_G_OR | 营收增速 | `or_yoy` | FI_WANT | + |
| A1_G_TR | 营业总收入增速 | `tr_yoy` | FI_WANT | + |

> **预注册声明**：`A1_G_*` 为**过去指标**（用户已声明其权重应降低）；
> `A1_Q_ROE` 等财务指标在**短周期**语境下权重应降低。
> 二者仍必须完整报告，但在 §11 中作为独立增量的主候选需通过 §12 的 `g11` 严格位。

### 6.2 组 A2 — 短期反转（7 个）

| ID | 因子 | 定义 | 预期 |
|---|---|---|---|
| A2_REV_1 | 1 日反转 | `- (close[t]/close[t-1] - 1)` | + |
| A2_REV_5 | 5 日反转 | `- (close[t]/close[t-5] - 1)` | + |
| A2_REV_10 | 10 日反转 | `- (close[t]/close[t-10] - 1)` | + |
| A2_REV_20 | 20 日反转 | `- (close[t]/close[t-20] - 1)` | + |
| A2_ON_5 | 隔夜收益反转 | `- (∏_{u=t-4..t} open[u]/prev_close[u] - 1)` | + |
| A2_ID_5 | 日内收益反转 | `- (∏_{u=t-4..t} close[u]/open[u] - 1)` | + |
| A2_MAX20 | 彩票效应规避 | `- max_{u in t-19..t}(close[u]/close[u-1] - 1)` | + |

### 6.3 组 A3 — Residual Momentum（4 个）

**残差定义**（逐股、向后滚动 250 日、每日可获得）：

```
r_ex(s,u)   = close[s,u]/close[s,u-1] - 1 - index_ret(000300.SH, u)
r_ind(s,u)  = 申万一级行业等权收益(u)          # PIT 行业
resid(s,u)  = OLS 残差: r_ex = a + b * r_ind + e      # 250 日滚动窗口（>=120 有效点）
```

| ID | 因子 | 定义 | 预期 |
|---|---|---|---|
| A3_RM_20 | 残差动量 20 日 | `Σ_{u=t-19..t} resid / std(resid, 20)` | + |
| A3_RM_60 | 残差动量 60 日 | `Σ_{u=t-59..t} resid / std(resid, 60)` | + |
| A3_RM_120 | 残差动量 120 日 | `Σ_{u=t-119..t} resid / std(resid, 120)` | + |
| A3_RM_SHARPE60 | 残差夏普 | `mean(resid,60) / std(resid,60) * sqrt(243)` | + |

> **市场因子固定用 `000300.SH`**（全历史），**不用** `932000.CSI`（2021 起），
> 以保证 A3/A5 的滚动窗口长度一致、不因基准可用性缩短样本。

### 6.4 组 A4 — 技术量价（10 个）

| ID | 因子 | 定义 | 预期 |
|---|---|---|---|
| A4_TURN_L | 换手水平 | `mean(turnover_rate, 5)` | − |
| A4_TURN_CHG | 换手放大 | `mean(turnover_rate,5)/mean(turnover_rate,20) - 1` | + |
| A4_VR5 | 量比放大 5/20 | `mean(vol,5)/mean(vol,20)` | + |
| A4_VR20 | 量比放大 20/60 | `mean(vol,20)/mean(vol,60)` | + |
| A4_AMT20 | 额比放大 20/60 | `mean(amount,20)/mean(amount,60)` | + |
| A4_MA20_DEV | MA20 偏离 | `close/ma20 - 1` | + |
| A4_MA60_DEV | MA60 偏离 | `close/ma60 - 1` | + |
| A4_MA20_SLOPE | MA20 斜率 | `ma20[t]/ma20[t-20] - 1` | + |
| A4_ATR_R | ATR 占比 | `ATR14 / close` | − |
| A4_AMP20 | 振幅 | `mean((high-low)/prev_close, 20)` | − |

> **预注册声明**：量价类一律用**相对放大**（换手率、量比、额比），
> **不使用成交量/成交额绝对值**；`A4_MA20_SLOPE` 体现「MA20 走平或向上」的前提，
> 若 MA20 向下则该因子取低分，符合既有研究偏好。
> 量能只作为**待验证变量**，禁止预设「放量 = 资金进入」。

### 6.5 组 A5 — Residual Volatility（5 个）

**残差定义**同 §6.3，但**不对行业回归**，改为对三因子基线回归：

```
r_ex(s,u) = a + b1 * r_mkt(u) + b2 * r_smb(u) + b3 * r_hml(u) + e     # 滚动 60 / 120 日
r_smb(u)  = 小盘组等权收益 - 大盘组等权收益     # 池内 circ_mv 三分位
r_hml(u)  = 高 BP 组等权收益 - 低 BP 组等权收益  # 池内 bp 三分位
```

| ID | 因子 | 定义 | 预期 |
|---|---|---|---|
| A5_RVOL_20 | 残差波动 20 日 | `std(resid, 20)` | − |
| A5_RVOL_60 | 残差波动 60 日 | `std(resid, 60)` | − |
| A5_RVOL_120 | 残差波动 120 日 | `std(resid, 120)` | − |
| A5_IVOL_SHARE60 | 特质波动占比 | `1 - R²(60日回归)` | − |
| A5_DRVOL_60 | 下行残差波动 | `std(resid | resid < 0, 60)` | − |

> **风险**：`r_smb` / `r_hml` 由**池内**分组构造，属于「自建风格因子」，
> 必须在报告披露构造细节，并做 §10 的 `U-ZZ2K vs U-PROXY` 稳健性对照。
> 若池内三分位任一组有效只数 < 30，则该日风格因子置缺失（不参与回归）。

**因子总数 = 12 + 7 + 4 + 10 + 5 = 38。**

---

## §7 标准化与 IC 口径

### 7.1 因子标准化

每个交易日在当期股票池有效样本内做 **rank 百分位化**：`rank / (n + 1) ∈ (0,1)`。
缺失值剔除，**不做插补**。每个因子独立计算有效 `n`，报告必须给出。

### 7.2 Label

```
y_h(s,t)  = close[t+h] / close[t] - 1                     h ∈ {5, 10, 20}
y_ex_zz  = y_h - index_ret(932000.CSI, t, t+h)            # 主口径（中证2000 增强语义）
y_ex_sec = y_h - 同期同行业(申万一级)等权平均 y_h            # 行业中性口径
y_ex_mkt = y_h - index_ret(000300.SH, t, t+h)             # 稳健对照
```

* **主口径 `h* = T+5`**，主 label = `y_ex_zz`。
* 所有 IC 必须同时报 `y_ex_zz / y_ex_sec / y_ex_mkt` 三种 label 的结果。

### 7.3 IC 统计量

```
IC = 每日 Spearman(rank(factor), rank(label))
IC_mean / IC_std / ICIR = IC_mean/IC_std / t_NW (Newey-West, lag = 5)
正值占比 / 平均每日截面 n / 有效交易日数
```

**显著性门槛（预注册）**：`|IC_mean| >= 0.01` **且** `|t_NW| >= 2.0`。

### 7.4 三段一致

`IS / VALID / OOS` 三段 IC 必须**同号**才可判为稳定；
仅 IS 显著而 VALID/OOS 反号 → `UNSTABLE`。

### 7.5 双口径（重叠处理）

| 口径 | 定义 | 用途 |
|---|---|---|
| **R-ALL**（主） | 池内全部单元格，逐日横截面 IC | 最大化截面样本 |
| **R-DEDUP**（稳健） | 逐股按 `cluster_events(gap=5)` 对「因子是否处于高分区(Q5)」聚类，每簇只保留**首次**进入日 | 消除时序重叠导致的 t 值虚高 |

* R-ALL 的 `t` 必须做 Newey-West(lag=5) 修正；R-DEDUP 报普通 `t`。
* 两口径符号不一致或显著性反转 → 标注 `EPISODE_SENSITIVE`，该因子**不得**判 PASS。
* R-DEDUP 某日 `n < 5` 则该日不参与 IC。

### 7.6 强制输出

禁止只报 IC 均值；每个「因子 × horizon × label × 口径」都必须给出
`IC_mean / IC_std / ICIR / t_NW / 正值占比 / n / 有效天数`。

---

## §8 正交化（本 SPEC 核心技术环节）

正交化是**逐日横截面操作**，天然 PIT（只用当日截面），**不涉及跨段拟合**。
禁止用 VALID/OOS 段估计任何正交化系数。

### 8.1 类内正交化（within-group）

| 项 | 规定 |
|---|---|
| **主口径** | **对称正交化（Löwdin）**：每日取组内因子截面相关矩阵 `S`，变换 `Z = F · S^(-1/2)`；`S` 特征值 `< 1e-6` 的方向截断（该日该方向置缺失并计数） |
| 对照口径 | 顺序 Gram-Schmidt（按 §6 表内 ID 正序），用于检验顺序依赖 |
| 输出 | `A*_*_orth`（正交化后因子），参与后续全部检验 |
| 顺序敏感 | 若正序/逆序 Gram-Schmidt 结论反转 → 标注 `ORDER_SENSITIVE` |

**类内正交化后必须报告**：组内平均绝对相关（正交前 vs 正交后）、
被截断方向的比例、正交化前后各因子 IC 的变化。

### 8.2 类间正交化（cross-group）

先构造**五组等权合成因子**（正交化后成员等权）：

```
C_k = mean( A_k_*_orth )        k ∈ {A1, A2, A3, A4, A5}     # 组内等权，禁止优化
```

再对每组做**残差化**：

```
C_k^⊥(t) = 每日横截面 OLS 残差:
           C_k  ~  1 + C_{-k} + M0
           C_{-k} = 其余四组合成(4 列) ; M0 = 基线(见 §8.3)
```

`C_k^⊥` 即「去掉其余四类与基线后，第 k 类剩余的独立信息」——**§11 独立增量的核心输入**。

### 8.3 基线 M0（正交化与增量回归共用）

```
M0 = rank(ln circ_mv) + beta60(000300.SH) + 申万一级行业哑变量
```

* `beta60` = 250 日滚动窗口内对 `000300.SH` 的回归斜率（>=120 有效点）。
* **M0 不含动量、不含反转、不含量价、不含波动率**——因为它们都是待检验的 Alpha 本体。
* 行业哑变量在当日截面内若某行业只数 < 3，则并入「其他」组。

### 8.4 报告要求

必须输出正交化诊断表：各组「正交前 IC / 类内正交后 IC / 类间正交后 IC(即 C_k^⊥)」三段并列，
使读者能看出「增量被谁吃掉了」。

---

## §9 分层（分位）检验

9.1 每日在池内按因子（或 `C_k` / `C_k^⊥`）分 `Q = 5` 组（主口径），补充 `Q = 10`。
9.2 报每组 `n / gross / net15 / net30 / net50 / win30 / median / max_drawdown / profit_factor`，
   以及 `Q5 - Q1` 价差。成本四档：`0 / 15 / 30 / 50 bp`。
9.3 **单调性**：`Spearman(Q_rank, mean(label))`，门槛 `>= 0.80`（预注册）。
9.4 **禁止只看胜率**；必须同时给 `gross` 与 `net`。
9.5 分层维度（条件 IC，§10.3 复用）：

| 维度 | 层 |
|---|---|
| 市值（Size） | 小 / 中 / 大（池内三分位） |
| 板块 | `MAIN` / `GEM` / `STAR` |
| 行业强度 | 强 / 普通 / 弱（行业 20 日相对强度三分位） |
| 换手水平 | 低 / 中 / 高 |
| 财报季 | 披露期 / 非披露期 |

目标：找到**稳定有效的区域**，而非 IC 最高的某一层。

---

## §10 OOS · Walk-Forward · 成本 · Regime

### 10.1 OOS（样本外）

1. 因子方向、正交化结构、合成权重、门槛**全部在 IS 段固定**。
2. 用 IS 冻结的基线/正交化系数在 VALID / OOS 段外推，报 `OOS_IC / OOS_t_NW / OOS_ΔR²`。
3. **禁止**用 OOS 结果反向调整任何因子、方向、权重、门槛。

### 10.2 Walk-Forward

按 §4.3 的 fold 滚动，逐 fold 报 `n / IC / t_NW / net30(Q5-Q1) / 单调性`。
判定：多数 fold 同号 且 `|IC| >= 0.005` 记为 `WF_STABLE`。

### 10.3 Regime 检验

| Regime 轴 | 定义（全部逐日 PIT） | 层 |
|---|---|---|
| 市场趋势 | `932000.CSI` vs MA20 vs MA60 | `BULL / NEUTRAL / BEAR`（MA 多空排列） |
| 风格 | `932000.CSI` 20 日收益 − `000300.SH` 20 日收益 的符号 | `小盘占优 / 大盘占优` |
| 波动 | `932000.CSI` 20 日已实现波动在**过去 250 日**内的分位 | `低波 / 中波 / 高波` |
| 财报季 | `ann_date` 密集月（4/8/10 月） | `是 / 否` |

判定：三档市场趋势 IC **同号** 记 `REGIME_STABLE`；
任一轴出现符号翻转必须逐轴列出失效环境。

### 10.4 成本

固定四档单边成本 `0 / 15 / 30 / 50 bp`（round-trip 按双边计）。
所有分层与 OOS 结果必须同时报 `gross` 与 `net30`。
**若 `net30` 后 `Q5-Q1 <= 0`，该因子不得判 PASS。**

---

## §11 独立增量筛选（本研究最终核心）

### 11.1 增量回归（逐日横截面，因变量 = `rank(label)`，`h ∈ {5, 10}`）

| 模型 | 自变量 |
|---|---|
| `M0` | `rank(ln circ_mv) / beta60 / 行业哑变量` |
| `M1_k` | `M0 + rank(C_k)`，`k` 逐一 |
| `M2a` | `M0 + rank(C_A1..C_A5)`（五组等权 `COMP_EQ`） |
| `M2b` | `M0 + rank(C_k)` 按 **预注册先验权重** `COMP_PRIOR`（见 §11.3） |
| `M3` | `M0 + 全部 38 个正交化因子` |
| `M4_k` | `M0 + rank(C_k^⊥)`（类间正交后残差）**← 独立增量主检验** |

输出：`β_mean / t_NW(β) / 正值占比 / 每日截面 R² 均值 / ΔR²`。

### 11.2 独立增量的判定（三条同时满足）

```
I1  ΔR²(M1_k - M0) > 0  且  β(C_k) 的 t_NW >= 2.0
I2  ΔR²(M4_k - M0) > 0  且  β(C_k^⊥) 的 t_NW >= 2.0
I3  C_k^⊥ 的 |IC_mean| >= 0.005  且  与 C_k 同号
```

三条全中 → 该类为 **`INDEPENDENT`**；部分满足 → `PARTIAL`；全不满足 → `REDUNDANT`。

### 11.3 预注册权重（**声明在先，非数据优化**）

| 合成 | 权重 | 说明 |
|---|---|---|
| `COMP_EQ` | 五组各 `1/5` | 等权对照 |
| `COMP_PRIOR`（主口径） | `A2=0.30, A3=0.25, A4=0.20, A5=0.15, A1=0.10` | 体现**用户先验**：短周期侧重反转/残差动量/量价，**降低财务与成长（A1）权重** |

* 两口径必须同时报告；结论冲突 → 标注 `WEIGHT_SENSITIVE`。
* **禁止**把权重当作可优化参数；禁止网格搜索最优权重。

### 11.4 单因子级独立增量

对每个因子 `A*_*_orth`，在 `M0 + 其余 37 个正交因子 + C_{-k}` 中回归，
取 `β / t_NW / ΔR²`；满足 `t_NW >= 2.0` 且 `ΔR² > 0` 者进入 `zz2k_independent_factors.csv`。
**自动筛出**即由本步骤脚本完成，筛选规则**预先写死在本 SPEC**，禁止运行期调整。

---

## §12 Null Model / 随机对照

`B = 2000`，`rounds = 8`，`min_resolution >= 0.99`；单边 `p = P(null 统计量 >= obs)`。

| 编号 | 定义 | 检验什么 |
|---|---|---|
| **N1**（主 null） | **同一交易日内**随机打乱 `C_k` 值，保持当日截面分布与日期不变 | 截面**排序**信息 |
| N2 | 同一股票内，把其 IS 段 `C_k` 值随机重排到它出现的日期上 | 个股内**时序**信息 |
| N3 | 每日从同期 38 个正交因子中随机抽 1 个作为对照因子 | 是否「任意因子都行」 |

三套 Null 必须同时报告；主判定用 **N1**；必须报 `resolution` 以证明对照池非空。

---

## §13 参数扰动（预注册格点）

| 轴 | 格点（首项为主口径） |
|---|---|
| `h*` | `5 / 10 / 20` |
| `q_groups` | `5 / 10` |
| `roll_window`（残差回归） | `120 / 250 / 500` |
| `vol_window`（残差波动） | `20 / 60 / 120` |
| `pool` | `U-ZZ2K / U-PROXY` |
| `composite` | `COMP_EQ / COMP_PRIOR` |

设计：基线 + 单轴扰动（star 设计）。
`positive_ratio = 格点中「IC 与基线同号 且 |IC| >= 0.005」的比例`。
判定：`>= 0.60 → stable`；`<= 0.40 → FRAGILE`；其余 `mixed`。
**禁止寻找单一最优参数**；报告只呈现邻近参数的稳定性。
每个格点内部**独立**重算正交化与合成（不得跨格复用系数）。

---

## §14 判决框架

对 5 个 `C_k`、`C_k^⊥`、`COMP_EQ`、`COMP_PRIOR` **在 `U-ZZ2K` 与 `U-PROXY` 两个口径下分别**输出
四值之一：`ROBUST / PROMISING / FRAGILE / NO EDGE`。12 个布尔位（`h* = T+5`，主 label `y_ex_zz`）：

```
g1   IS IC 符号符合 §6 预注册方向          g7  参数扰动 stable (positive_ratio >= 0.60)
g2   IS |t_NW| >= 2.0                     g8  三种市场 Regime IC 同号
g3   VALID IC 同号 且 |t| >= 1.5           g9  N1 null p < 0.05
g4   OOS IC 同号                          g10 R-DEDUP 口径 IC 同号 且 |IC| >= 0.005
g5   分层单调性 >= 0.80                   g11 类间正交后 β(C_k^⊥) t_NW >= 2.0
g6   30bp 成本后 Q5-Q1 > 0                g12 Walk-Forward 多数 fold 同号 且 |IC| >= 0.005
```

`ROBUST = 12/12`；`PROMISING = 9..11`；`FRAGILE = 5..8`（或参数位为 FRAGILE）；`NO EDGE <= 4`。

**单因子判决**（38 个）：用前 10 位（`g11/g12` 不适用），以
`g10b = R-DEDUP 显著性` 与 `g12b = 单因子级 ΔR² > 0` 替代，并额外标注：

```
SIGN_CONFLICT     实测方向与 §6 预注册相反
UNSTABLE          IS 显著但 VALID/OOS 反号
EPISODE_SENSITIVE R-ALL 与 R-DEDUP 不一致
ORDER_SENSITIVE   正交化顺序导致结论反转
WEIGHT_SENSITIVE  COMP_EQ 与 COMP_PRIOR 结论冲突
POOL_SENSITIVE    U-ZZ2K 与 U-PROXY 结论冲突（以 U-ZZ2K 为准，必须并列呈现）
MEMBER_HISTORY_SHORT  中证2000 成分仅 37 个月度快照（发布日 2023-08-11 限制）
WF_TRAIN_SHORT    Walk-Forward 的 train 长度因可用历史不足而降级
OOS_SHORT         OOS 仅约 8 个月
```

---

## §15 最终报告必须回答的 12 个问题

```
Q1  五类因子池能否完整构造？各因子覆盖度、有效 n、缺失模式如何？
Q2  类内正交化后，各类还剩几个因子保持显著（类内去冗余结果）？
Q3  Value/Quality/Growth 类是否提供独立增量？（是否只是规模/行业的代理）
Q4  短期反转类是否提供独立增量？隔夜/日内分解是否改变结论？
Q5  Residual Momentum 相对原始动量是否有增量？
Q6  技术量价类是否提供独立增量？量比/换手放大是否为真信号？
Q7  Residual Volatility 类是否提供独立增量？低波异象在中证2000 是否成立？
Q8  COMP_EQ 与 COMP_PRIOR 哪个更稳？两者结论是否冲突？
Q9  控制其余四类 + 基线后，哪一类/哪个因子的残差仍显著（真正独立增量）？
Q10 成本 0/15/30/50bp 后，哪些因子仍存活？
Q11 Regime：哪些市场/风格/波动状态下有效，哪些失效？
Q12 最终：自动筛出的独立增量因子清单及其判决位。
```

---

## §16 运行与产物

### 16.1 脚本

| 文件 | 职责 |
|---|---|
| `zz2k_common.py` | 公共层：只读复用 `hve_common` / `fs_common` / `hef_common`；自建 932000 读取、正交化、IC、NW、Null、Regime |
| `zz2k_run.py` | 主运行：全部计算与 CSV |
| `zz2k_report.py` | 报告渲染 + 交付校验（必须 `EXIT 0`） |

运行顺序：`python zz2k_run.py` → `python zz2k_report.py`

### 16.2 必产产物（13）

```
zz2k_members.parquet              中证2000 PIT 成分（已落盘，37 月度快照，2023-08-31 起）
bench_members.parquet             对照宽基 PIT 成分（000300/000905/000852，已落盘，2021-01 起）
zz2k_factor_ic.csv               38 因子 × horizon × label × 口径 的 IC 全表
zz2k_orthogonal.csv               类内/类间正交化诊断（正交前后 IC、相关、截断比例）
zz2k_factor_quantile.csv          分层全表（Q5/Q10、gross/net 四档、单调性）
zz2k_group_composite.csv          五组 C_k 与 COMP_EQ / COMP_PRIOR 的 IC 与权重明细
zz2k_incremental.csv              M0..M4 增量回归（ΔR²、β、t_NW）
zz2k_independent_factors.csv      ★自动筛出的独立增量因子清单（§11.4 规则）
zz2k_oos.csv                      IS/VALID/OOS 三段 + OOS 外推
zz2k_walkforward.csv              逐 fold 结果
zz2k_cost.csv                     成本四档净收益
zz2k_regime.csv                   四 Regime 轴分层 IC
zz2k_null.csv                     N1/N2/N3 置换结果
zz2k_funnel.csv                   样本漏斗
H_ZZ2K_ALPHA_V1_REPORT.md         最终报告
```

> 除 `zz2k_members.parquet` / `bench_members.parquet` 外，**所有 CSV 必须含 `pool` 列**
> （取值 `U-ZZ2K` / `U-PROXY`），双口径结果写在同一文件内，禁止只落一个口径。

补充产物：`H_ZZ2K_ALPHA_V1_RESULTS.json`、`H_ZZ2K_ALPHA_V1_SUMMARY.json`、
`H_ZZ2K_ALPHA_V1_SPEC.md`、`H_ZZ2K_ALPHA_V1_FREEZE.md`。

### 16.3 `future_column_scan`（任一失败 → `FATAL` → `EXIT 3`）

1. `truncation_invariance`：面板截断 120 会话后，因子列在不受前向窗口影响的日期上逐位一致。
2. `pit_fundamental_proof`：每个财务因子值可追溯到 `ann_date <= t` 且 `report_type == 1` 的记录。
3. `forward_window_isolation`：因子列集合与 label 列集合不相交。
4. `label_columns_marked`：所有消费 `t > T` 信息的列登记在 `LABEL_COLUMNS`。

### 16.4 样本漏斗（强制，不得只留显著/有效样本）

```
全A → U-ZZ2K 成分(PIT) → 可交易/非ST/非退市/上市龄达标 → 因子有效截面 →
      类内正交化可用 → 类间正交化可用 → 独立增量筛选入池
```

并额外输出：

```
U-ZZ2K 每日截面只数 / 平均只数 / 有效交易日数
U-PROXY 同口径指标 / 两池平均重合度
每口径**各自**的 IS/VALID/OOS 实际区间与有效交易日数（两口径 IS 起点不同）
每段(IS/VALID/OOS)的池内单元格数与有效交易日数
每因子有效 n（含为 0 的因子，必须保留在表中）
每因子在两个口径下的有效起点（us_start）与有效 n（必须成对给出）
两口径在同一日期上的 IC 差异（`POOL_SENSITIVE` 判定依据）
```

### 16.5 退出码

`verify_spec` 不一致 → `EXIT 2`；`future_column_scan` 失败 → `EXIT 3`；正常 → `EXIT 0`。

---

## §17 最终结论格式（**必须逐字采用**）

```
【研究结论】

1. 五类因子池构造完整性：            PASS / FAIL / MIXED
2. 类内正交化去冗余结果：            保留因子数 / 总因子数，逐类列出
3. Value/Quality/Growth 独立增量：   PASS / FAIL / MIXED
4. 短期反转 独立增量：               PASS / FAIL / MIXED
5. Residual Momentum 独立增量：      PASS / FAIL / MIXED
6. 技术量价 独立增量：               PASS / FAIL / MIXED
7. Residual Volatility 独立增量：    PASS / FAIL / MIXED
8. 最稳定的因子与区间：              仅报告 IS/VALID/OOS 三段同号区间
9. 主要失效环境：                    按 Regime 轴逐条列出
10. 成本后：                         0/15/30/50bp 下是否仍成立
11. OOS：                            是否通过（并标注 OOS_SHORT 若适用）
12. 参数稳定性 / 年度稳定性：        是否通过
13. 自动筛出的独立增量因子清单：     factor_id, 类别, 方向, IC, t_NW, ΔR², 判决
14. 最终状态：                       RESEARCH_VALIDATED / PARTIALLY_VALIDATED / FAILED
```

禁止输出「策略有效」「稳赚」「胜率神器」「推荐买入」等表述。
必须区分**因子本身有效** 与 **因子只是 Momentum / Size / Industry 的代理变量**。

---

## §18 不变量

1. `SPEC_SHA256` 必须与冻结记录一致，否则 `EXIT 2`。
2. 所有阈值只在 `PREREG` 中定义，禁止散落硬编码。
3. **禁止权重优化**：`COMP_EQ` / `COMP_PRIOR` 权重为预注册先验，不得网格搜索。
4. **禁止用 VALID/OOS 段筛选因子、确定方向、估计正交化系数**。
5. 禁止只展示显著因子；38 个因子必须全部报告，包括 IC 为零的。
6. 禁止把「放量」直接解释为「资金进入」，禁止把「缩量」直接解释为「卖压衰减」。
7. 禁止因样本量小而放宽门槛或人为扩池；禁止为得到 PASS 而反复调参。
8. 禁止修改 `hve_common.py` / `fs_common.py` / `mce_common.py` / `hef_common.py`
   以及任何 `H-SLG-*` / `H-*` 已完成研究的产物与结论。
9. 禁止修改现有交易系统核心逻辑。
10. `trading_authorization = NO`：本阶段禁止把任何因子转化为实盘 BUY 信号；
    Entry / Exit / Position sizing / Execution 属独立第二阶段。

---

## §19 冻结记录（FREEZE）

```
Research ID     : H-ZZ2K-A1
Version         : 1.1
Created         : 2026-10-04
Revised         : 2026-10-04
SPEC path       : research/h_zz2k_alpha/H_ZZ2K_ALPHA_V1_SPEC.md
Factor count    : 38  (A1=12, A2=7, A3=4, A4=10, A5=5)
Groups          : 5
Primary h*      : T+5
Primary label   : y_ex_zz (932000.CSI 超额)
Primary pool    : U-ZZ2K (932000.CSI PIT 成分, 2023-09-01 起)
Control pool    : U-PROXY (小盘代理池, 2021-01-04 起)
Common boundary : IS 终点 20250630 | VALID 20250701-20251231 | OOS 20260101-20260924
IS start        : U-ZZ2K 20240201 | U-PROXY 20220101
Members source  : data/zz2k_members.parquet (37 快照) / data/bench_members.parquet
Composite       : COMP_PRIOR (主) / COMP_EQ (对照)
Cost tiers      : 0 / 15 / 30 / 50 bp
Null            : B=2000, rounds=8
Seed            : 20261004
trading_authorization : NO
```

> 冻结后任何修改必须走 `V2` 流程，**不得**回填 `V1` 结果。

---

## §20 修订记录

| 版本 | 日期 | 改动 | 原因 |
|---|---|---|---|
| 1.0 | 2026-10-04 | 初稿 | — |
| 1.1 | 2026-10-04 | §2.1 补成分数据行与硬约束；§4 重写时间切分为**双口径**；§5.1 改为 `index_weight` 并落盘；§5.2 补剔除成分来源；§5.3 增双口径强制项；§14 增 4 个标注；§16.2 增 `bench_members.parquet` 与 `pool` 列要求；§16.4 增双口径披露项；§19 更新冻结 | 取数时核实：**中证2000（932000.CSI）发布日期 2023-08-11，Tushare 不提供更早成分回溯**，成分 PIT 仅 37 个月度快照，会把 `U-ZZ2K` 样本压缩到约 730 交易日 |

**修订性质声明**：V1.1 仅因**数据现实**修订，研究**尚未执行、未产生任何结果**，
所有改动发生在见到任何 IC / 分层 / 判决数字之前，**不构成回填优化**。
