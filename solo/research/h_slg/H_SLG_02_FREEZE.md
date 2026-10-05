# H-SLG-02 FREEZE — 预注册冻结记录

Research ID: `H-SLG-02`
Frozen at: `2026-10-04`
Owner: research 自动化流程（无人工挑选样本、无人工挑选因子）
前置研究: `H-SLG-01`（最终状态 `FAILED`，结论与产物不得修改）

本记录在**任何结果被观察之前**写下。`H_SLG_02_SPEC.md` 一经冻结，其全部阈值、
窗口、因子定义、方向、分组、判定规则即不可再修改。任何后续修改必须走
`V1 冻结 → 输出结果 → 提出新 Hypothesis → V2`，禁止回填优化（SPEC §22）。

──────────────────────────────────────────────
## 1. 被冻结文件

| 项 | 值 |
|---|---|
| 文件 | `H_SLG_02_SPEC.md` |
| 大小 | `21579` bytes |
| SHA256 | `DEFAF8D88E69810B96E8EB5419E7727C22C5A17B2C32A39AFC0BB6D16826410E` |
| mtime（本地） | `2026-10-04 07:58:36` |
| 版本 | `1.0` |

运行期由 `slg2_common.verify_spec2()` 逐次重算该哈希：不一致 → `EXIT 2`，实验作废。
日志 mtime 必须晚于 SPEC 的 mtime；由 `parameter_leakage` 审计项核验。

──────────────────────────────────────────────
## 2. 研究问题（冻结）

> **在主升后进入整理的股票池内，「整理完成度」作为连续因子，是否对未来
> T+3 / T+5 / T+10 的横截面超额收益具有稳定、单调、且超越动量的预测力？**

* **G1**：单个完成度因子是否具有非零横截面 IC（符号、量级、显著性与单调性）？
* **G2**：等权合成因子 `COMP` 是否优于单因子与动量基准？
* **G3**：在控制 `MOM20 / MOM60 / RS / Volume / Size` 后，`COMP` 是否仍有增量 IC？

不研究低位 W 底、普通超跌反弹、单纯动量、突破择时、买入时机与仓位。

### 2.1 为什么开这个新研究（冻结的理由）

`H-SLG-01` 冻结结论：`verdict = FRAGILE`（2/12），A 组 71 事件，`T+5 net30 = -1.65%`，
OOS 弱于普通突破组，参数 `positive_ratio = 0.074`，`N1 null p = 0.9925`，
`Model5−Model4` 的 `W = 0.286 / 0.163`（不显著）。**该结论已作废其事件口径。**

唯一存活线索：`Model4−Model3` 的 `W = 15.17 / 40.83`（1% 显著，OOS_IC 上升，
但 `ΔR²` 仅约 `7e-5`）。本 SPEC 把该线索从**事件口径**改为**横截面因子口径**，
理由有二：主口径硬门槛使 A 组仅 71 例（OOS 仅 7 例），样本量不足；
二值 `READY` 覆盖仅约 1.4%，损失连续信息。**本次不预设该线索必然成立。**

──────────────────────────────────────────────
## 3. 数据边界（冻结）

| 用途 | 来源 |
|---|---|
| 日线 OHLC / 量额 / 换手 / ST / 退市 / 涨跌停 / 板块 | `hve_common.build_grid()` |
| 总市值 / 换手率 | `basic_panel.parquet` → `mce_common.load_size_mv` |
| 指数（市场收益 / 市场状态） | `index_panel.parquet` `000300.SH` → `mce_common.index_ret`/`regime_ma` |
| 行业（PIT 申万一级） | `sw_industry_map.csv` → `hef_common.load_industry_map` |
| 交易日历 | `calendar.parquet` → `hef_common.load_calendar` |
| 结构 / 完成度变量 | `slg_common`（**只读复用，禁止修改**） |

禁止：临时下载其他行情源、TDX、AkShare、Wind、同花顺、人工挑选股票、未来数据。
侦察结论（继承 `H-SLG-01`，2026-10-03）：全部字段齐备
（grid `S=5816 × N=2120`，`20180102..20260924`；CSI300；PIT 申万一级 31 类；
`total_mv`/`turnover_rate` 可用）。**无缺失字段。**

──────────────────────────────────────────────
## 4. 时间分段（冻结）

| 段 | 区间 | 用途 |
|---|---|---|
| PRE | `.. 20191231` | 仅描述性统计，不进结论 |
| IS | `20200101 .. 20241231` | 因子筛选与主口径（`COMP_SIG` 只在此段选因子与定方向） |
| VALID | `20250101 .. 20251231` | 时间验证段 |
| OOS | `20260101 .. 20260924` | 最终样本外段，禁止反向调参 |

`study_start = 20190101`，`burn_in_sessions = 250`。
Walk-Forward：`train 3 年 → test 1 年`，滚动 `2021..2026`。

──────────────────────────────────────────────
## 5. 预注册参数（冻结主口径）

```
LEG_W = 40, CONS_MIN = 5, CONS_MAX = 40, PEAK_LO = 5, PEAK_HI = 45
first_rise_pct >= 0.25, first_rise_days >= 5, retracement_pct >= 0.05
neckline    = high[peak_day]
U_FACTOR    = CTX & R1                       (禁止用 READY / FAILED 筛选)
h_orizons   = (3, 5, 10, 20)                 h* = T+5
Q_GROUPS    = 5                              q_groups 扰动点 (5, 10)
IC_MIN      = 0.01        T_MIN    = 2.0     (单因子 IS 显著性门槛)
MONO_MIN    = 0.80        T_WF_MIN = 1.5
NW_LAG      = 5           DEDUP_GAP = 5      (R-DEDUP 整合理期聚类)
PARAM_IC_FLOOR      = 0.005                  (同号且 |IC|>=floor 记为正例)
param_stable_thr    = 0.60
param_fragile_thr   = 0.40
RETAIN_THR  = 0.70                           (g12 去掉单因子后 IC 保留比例)
cost_bp     ∈ {0, 15, 30, 50}                主口径 30bp
null_B = 2000, null_rounds = 8, null_min_resolution = 0.99
seed = 20261103
trading_authorization = NO
```

### 5.1 因子清单与预注册方向（冻结，禁止看到结果后翻转）

| # | 因子 | 定义 | 预期 |
|---|---|---|---|
| P1 | `vol_contract_rb` | `rb_vol_over_ma20` | `-` |
| P2 | `vol_contract_consol` | `consol_vol_over_leg` | `-` |
| P3 | `vol_ratio_5_leg` | `std(5日收益)/std(主升20日收益)` | `-` |
| P4 | `atr_ratio` | 整理末期 5 日 `ATR/Close` | `-` |
| P5 | `ma20_pos` | `close/ma20 - 1` | `+` |
| P6 | `ma20_slope` | `ma20_slope20` | `+` |
| P7 | `ma20_hold` | `1 - frac_below_ma20` | `+` |
| P8 | `ma60_pos` | `close/ma60 - 1` | `+` |
| P9 | `ma60_slope` | `ma60_slope20` | `+` |
| P10 | `rb_quality` | `right_bottom/left_bottom - 1` | `+` |
| P11 | `rb_above_ma20` | `right_bottom/ma20 - 1` | `+` |
| P12 | `near_neckline` | `close/neckline - 1` | `+` |
| P13 | `rb_hold` | `(t-right_bottom_day)/consol_days` | `+` |
| P14 | `rs_sector` | `rs_sector_20` | `+` |
| P15 | `ready_count` | `R1+..+R6`（0..6，对照） | `+` |
| P16 | `ready_dummy` | `READY`（0/1，对照） | `+` |

合成：`COMP_ALL = 等权平均(P1..P14 按上表方向)`；
`COMP_SIG = 等权平均(仅 IS 段 |t_NW|>=2 且 |IC|>=0.01 的因子，按 IS 实测方向)`。
**权重禁止优化；`COMP_SIG` 的筛选与方向只允许用 IS 段。**

### 5.2 增量回归（冻结）

```
M0 = rank(MOM20) + rank(MOM60) + rank(RS_sec) + rank(vol_ratio_20) + rank(ln_size)
M1 = M0 + rank(COMP)
M2 = M0 + rank(COMP_ALL) + rank(COMP_SIG)
M3 = M0 + rank(P1..P14)
因变量 = rank(y_ex_sec),  h ∈ {5, 10};  每日横截面 OLS
核心比较 : M1 - M0  与  M3 - M1
```

### 5.3 Null（冻结）

| 编号 | 定义 |
|---|---|
| **N1**（主） | 同日横截面内随机打乱 `COMP`，保持当日分布与日期 |
| N2 | 同一股票内把 IS 段 `COMP` 值随机重排到其出现日期 |
| N3 | 每日从当日 `U_FACTOR` 中随机抽取一个 `P1..P14` 因子值作对照 |

单边 `p = P(null 统计量 >= obs)`，`B = 2000`，`rounds = 8`，`min_resolution = 0.99`。

### 5.4 参数扰动格点（冻结）

```
leg_w          : 30 / 40 / 50
consol_max     : 20 / 30 / 40 / 50
retrace_min    : 0.05 / 0.10 / 0.15
first_rise_pct : 0.20 / 0.25 / 0.30
q_groups       : 5 / 10
设计           : 基线 + 单轴扰动（star），共 11 格
```

──────────────────────────────────────────────
## 6. 运行前置不变量（冻结）

1. `SPEC_SHA256` 必须与本文档一致，否则 `EXIT 2`。
2. 全部阈值只在 `PREREG2` 中定义，禁止散落硬编码。
3. `future_column_scan`（4 项）任一失败 → `FATAL` → `EXIT 3`。
4. 运行顺序：`python slg2_run.py` → `python slg2_report.py`（后者必须 `EXIT 0`）。
5. 必产 10 个文件（SPEC §21）+ 4 个补充产物。
6. **禁止修改** `slg_common.py`、`H_SLG_01_*` 任何文件、现有交易系统核心逻辑。

──────────────────────────────────────────────
## 7. 研究纪律（冻结，违反即实验无效）

* 禁止为提高 IC 反复调整参数、窗口、因子定义或方向。
* 禁止用 VALID / OOS 段筛选因子、确定方向或设权重。
* 禁止对 `COMP` 做权重优化（只能等权）。
* 禁止只展示显著因子；`P1..P16` 必须全部报告，含预测力为零者。
* 禁止只报 IC 均值而不报 `t_NW` 与 R-DEDUP 口径。
* 禁止把「缩量」直接解释为「卖压衰减」。
* 禁止把 K 线形态直接解释为主力行为。
* 禁止因某一只股票、某一年、某一层表现好就得出结论。
* 禁止把因子的截面预测力直接等同于可交易收益（成本与容量未计）。
* 禁止使用未来数据确认历史结构。

最终目标不是找到「IC 最高的因子」，而是回答：
> 「主升后的整理完成度」作为连续因子，在动量之外是否提供了**真实且稳定**的信息，
> 以及这份信息是否足以支撑第二阶段（组合与执行）继续投入。

──────────────────────────────────────────────
## 8. 产物清单（冻结）

**必产（10）**：`second_leg2_factor_ic.csv`、`second_leg2_factor_quantile.csv`、
`second_leg2_factor_composite.csv`、`second_leg2_factor_incremental.csv`、
`second_leg2_factor_oos.csv`、`second_leg2_factor_param.csv`、
`second_leg2_factor_null.csv`、`second_leg2_factor_subgroup.csv`、
`second_leg2_factor_funnel.csv`、`H_SLG_02_REPORT.md`。

**补充（4）**：`H_SLG_02_RESULTS.json`、`H_SLG_02_SUMMARY.json`、
`H_SLG_02_SPEC.md`、`H_SLG_02_FREEZE.md`。
