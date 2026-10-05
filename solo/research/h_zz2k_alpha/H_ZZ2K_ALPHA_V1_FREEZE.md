# H-ZZ2K-A1 FREEZE — 预注册冻结记录

Research ID: `H-ZZ2K-A1`
Title: 中证2000增强 Alpha V1 —— 五类 Alpha 分组正交化 · IC · 分层 · OOS · Walk-Forward · 成本 · Regime · 独立增量筛选
Frozen at: `2026-10-04 12:21:37`（Asia/Shanghai，即 SPEC mtime）
Owner: research 自动化流程（无人工挑选样本、无人工挑选因子、无人工翻转方向）
关联研究: `H-SLG-02`（整理完成度连续因子，已归档；本研究不修改其任何产物与结论）

本记录在**任何结果被观察之前**写下。`H_ZZ2K_ALPHA_V1_SPEC.md` 一经冻结，其全部阈值、
窗口、因子定义、方向、分组、判定规则即不可再修改。任何后续修改必须走
`V1 冻结 → 输出结果 → 提出新 Hypothesis → V2`，禁止回填优化（SPEC §18 / §19）。

──────────────────────────────────────────────
## 1. 被冻结文件

| 项 | 值 |
|---|---|
| 文件 | `H_ZZ2K_ALPHA_V1_SPEC.md` |
| 大小 | `39075` bytes |
| SHA256 | `427E6F9F1E145606C27064ECC0D07938E0EC614D687E113D958AE86FFD64A69D` |
| mtime（本地） | `2026-10-04 12:21:37` |
| 版本 | `1.1` |

运行期由 `zz2k_common.spec_check()` 逐次重算该哈希：不一致 → `EXIT 2`，实验作废
（`zz2k_run.py` 与 `zz2k_report.py` 启动时均校验；日志首行打印 `SPEC_SHA256 ... (MATCH)`）。

──────────────────────────────────────────────
## 2. 研究问题（冻结）

> **在中证2000 股票池内，把五类经典 Alpha 分别正交化后，哪些因子对未来
> T+5 / T+10 / T+20 的横截面超额收益具有稳定、单调、成本后仍存活、
> 且相对其余四类的【独立增量】？**

* **G1**：每类 Alpha 内部，哪些因子在类内正交化后仍保留 IC（类内去冗余）？
* **G2**：五类合成因子相对「规模 + 市场Beta + 行业」基线是否有增量 IC？
* **G3**：对「其余四类 + 基线」正交化后，哪一类的残差仍显著（真正独立增量）？

**明确不研究**：择时信号、买入/卖出规则、仓位与执行、行业轮动策略、事件驱动、
机器学习黑箱打分。本研究**不产出任何可交易信号**（`trading_authorization = NO`）。

──────────────────────────────────────────────
## 3. 数据边界（冻结）

| 用途 | 来源 | 覆盖 |
|---|---|---|
| 日线面板 OHLC(qfq)/vol/amount/turnover/pct_chg/traded/st/delist/涨跌停/board | `hve_common.build_grid()` | `S=5816 × N=2120`（`20180102..20260924`） |
| 估值/规模/换手 | `fs_common.DBASIC_COLS` → `basic_panel.parquet` | `20230103..20260924` |
| 财务三表 + 指标 | `fs_build_fund.py` 的 `INC_WANT/BAL_WANT/CF_WANT/FI_WANT` | 由 `ann_date` PIT 决定 |
| 行业（PIT 申万一级） | `sw_industry_map.csv` → `hef_common.industry_at_fast` | — |
| 交易日历 | `calendar.parquet` → `hef_common.load_calendar` | — |
| 市场因子（回归用） | `000300.SH` ← `index_panel.parquet`（**该文件只含 000300.SH**） | `20180102..20260924` |
| 基准 / 超额 / Regime | **`932000.CSI` ← `stock_data.db` 表 `index_daily_cache`** | `20210104..20260924` |
| 基准稳健对照 | `000852.SH`（中证1000）← 同表 `index_daily_cache` | `20210104..20260924` |
| 中证2000 成分（PIT） | `data/zz2k_members.parquet` ← `zz2k_fetch_members.py`（**已落盘**） | `20230831..20260831`（37 个月度快照） |
| 对照宽基成分（PIT） | `data/bench_members.parquet`（`000300.SH / 000905.SH / 000852.SH`，**已落盘**） | `20210129..20260831` |

* **禁止**临时下载其他行情源（TDX / AkShare / Wind / 同花顺 / 人工挑股）。
* **禁止**修改 `hve_common.py` / `fs_common.py` / `mce_common.py` / `hef_common.py`（只读 import）。
* 复权口径 `qfq`；字段缺失必须**如实标注**，不得静默替代或插补。
* `index_panel.parquet` **不含** `932000.CSI`，故必须在 `zz2k_common.py` 内自建 `index_daily_cache` 读取函数。
* 取数脚本 `zz2k_fetch_members.py` 走 `index_weight` **按月分片**（单次上限 7000 行）；
  **不可使用 `index_member_all`**（该接口为申万行业，传 `932000.CSI` 返回 0 行）。

──────────────────────────────────────────────
## 4. 时间分段（冻结，**时间切分，禁止随机切分**）

共同边界：`IS 终点 = 20250630`，`VALID = 20250701..20251231`，`OOS = 20260101..20260924`。

| 口径 | 烧入 PRE（**不进结论**） | IS | VALID | OOS |
|---|---|---|---|---|
| **`U-ZZ2K`（主口径）** | `20230901..20240131` | `20240201..20250630` | `20250701..20251231` | `20260101..20260924` |
| **`U-PROXY`（对照口径）** | `20210104..20211231` | `20220101..20250630` | `20250701..20251231` | `20260101..20260924` |

* 两口径**各自独立**划分 IS / VALID / OOS；**跨口径不得混用**系数或阈值。
* 烧入**不是一刀切**：因子有效起点由各因子自身窗口决定（最短 20 日；最长 250 日滚动 / `beta60`），
  每个因子必须报出自己的有效起点 `us_start` 与有效 `n`；上表 PRE 仅为段划分保守下界。
* `burn_in_quarters = 8`（财务因子另需 8 个财报季历史以计算增长率）。
* OOS 两口径均为约 8 个月 → 报告必须显式标注 **`OOS_SHORT`**，**不得**因样本小而放宽门槛。

Walk-Forward（冻结）：`train 3 年 → test 1 年`，`step = 0.5 年`；
预期 test 窗口 = `2024H1 / 2024H2 / 2025H1 / 2025H2 / 2026H1`。
`U-ZZ2K` 可用历史不足 3 年 → train 自适应降为可用最大历史（最短 1 年），逐 fold 标注 **`WF_TRAIN_SHORT`**。
每个 fold 内部：正交化方向、基线系数、合成权重**只在该 fold 的 train 段拟合**。两口径分别执行，不得合并。

──────────────────────────────────────────────
## 5. 股票池（冻结）

```
U-ZZ2K(t)  = 中证2000 成分(PIT) ∩ traded ∩ ~st ∩ ~delist
             ∩ (上市 >= 250 交易日) ∩ (board != 'BSE') ∩ (t 日有有效 close)

U-PROXY(t) = 全A(t) 剔除 沪深300 / 中证500 / 中证1000 成分(PIT)
             ∩ 按 circ_mv 升序取第 1801 .. 3800 名（≈2000 只）
             ∩ 同 U-ZZ2K 的可交易 / 非ST / 非退市 / 上市龄 / 非BSE 约束
```

* 成分快照为月频，`t` 落在两快照之间时用**前一个快照**做 PIT 前向填充。
* **北交所（BSE）一律排除**；退市前历史打印保留；**不施加 80 亿市值硬门槛**（中证2000 成分市值多集中 20–100 亿）。
* 报告必须披露：中证2000 成分仅 37 个月度快照、发布日 `2023-08-11` 之前无回溯 → 标注 **`MEMBER_HISTORY_SHORT`**。
* 若本地成分文件缺失 → **禁止**静默替代；必须报 `MEMBER_DATA_MISSING` 并停止 `U-ZZ2K` 口径。
* **双口径并列是强制项**：每个因子 / 每个 `C_k` / 每个 `C_k^⊥` 必须**同时**给出两口径的 IC、t 值、判决位；
  结论不一致 → 标注 **`POOL_SENSITIVE`**，**以 `U-ZZ2K` 为准**，但必须并列呈现。

──────────────────────────────────────────────
## 6. 预注册参数（冻结主口径）

```
HORIZONS    = (5, 10, 20)                 h* = T+5
LABELS      = (y_ex_zz, y_ex_sec, y_ex_mkt)   主 label = y_ex_zz（932000.CSI 超额）
Q_GROUPS    = (5, 10)                     主口径 Q=5
COST_BP     = (0, 15, 30, 50)             主门槛 30bp
IC_MEAN_MIN = 0.01     IC_T_MIN = 2.0     (单因子 IS 显著性门槛: |IC_mean|>=0.01 且 |t_NW|>=2.0)
VALID_T_MIN = 1.5      MONO_MIN = 0.80
NW_LAG      = 5        DEDUP_IC_MIN = 0.005   IND_IC_MIN = 0.005   WF_IC_MIN = 0.005
DEDUP_GAP   = 5        IC_MIN_N = 30      DEDUP 某日 n<5 不计入 IC
ORTH_COV_MIN = 0.30    (覆盖度门禁)
PERT_STABLE = 0.60     PERT_FRAGILE = 0.40
NULL_B = 2000   NULL_ROUNDS = 8   min_resolution >= 0.99
MIN_LIST_DAYS = 250   LONG_SUSP_DAYS = 60
SEED        = 20261004
GROUPS      = (A1, A2, A3, A4, A5)        因子总数 = 38
trading_authorization = NO
```

### 6.1 因子清单与预注册方向（冻结，**禁止看到结果后翻转**）

`+` = 因子值越大预期未来超额越高；`−` = 越大预期越低。

| ID | 组 | 定义 | 预期 |
|---|---|---|---|
| `A1_V_EP` | A1 | `1 / pe_ttm` | + |
| `A1_V_BP` | A1 | `1 / pb` | + |
| `A1_V_SP` | A1 | `1 / ps_ttm` | + |
| `A1_V_DP` | A1 | `dv_ttm` | + |
| `A1_Q_ROE` | A1 | `roe` | + |
| `A1_Q_ROIC` | A1 | `roic` | + |
| `A1_Q_GM` | A1 | `grossprofit_margin` | + |
| `A1_Q_OCF` | A1 | `ocf_to_sales` | + |
| `A1_Q_LEV` | A1 | `- debt_to_assets` | − |
| `A1_G_NPY` | A1 | `netprofit_yoy` | + |
| `A1_G_OR` | A1 | `or_yoy` | + |
| `A1_G_TR` | A1 | `tr_yoy` | + |
| `A2_REV_1` | A2 | `- (close[t]/close[t-1] - 1)` | + |
| `A2_REV_5` | A2 | `- (close[t]/close[t-5] - 1)` | + |
| `A2_REV_10` | A2 | `- (close[t]/close[t-10] - 1)` | + |
| `A2_REV_20` | A2 | `- (close[t]/close[t-20] - 1)` | + |
| `A2_ON_5` | A2 | `- (∏_{u=t-4..t} open[u]/prev_close[u] - 1)` | + |
| `A2_ID_5` | A2 | `- (∏_{u=t-4..t} close[u]/open[u] - 1)` | + |
| `A2_MAX20` | A2 | `- max_{u∈t-19..t}(close[u]/close[u-1] - 1)` | + |
| `A3_RM_20` | A3 | `Σ_{t-19..t} resid / std(resid,20)` | + |
| `A3_RM_60` | A3 | `Σ_{t-59..t} resid / std(resid,60)` | + |
| `A3_RM_120` | A3 | `Σ_{t-119..t} resid / std(resid,120)` | + |
| `A3_RM_SHARPE60` | A3 | `mean(resid,60)/std(resid,60)*sqrt(243)` | + |
| `A4_TURN_L` | A4 | `mean(turnover_rate,5)` | − |
| `A4_TURN_CHG` | A4 | `mean(turnover_rate,5)/mean(turnover_rate,20) - 1` | + |
| `A4_VR5` | A4 | `mean(vol,5)/mean(vol,20)` | + |
| `A4_VR20` | A4 | `mean(vol,20)/mean(vol,60)` | + |
| `A4_AMT20` | A4 | `mean(amount,20)/mean(amount,60)` | + |
| `A4_MA20_DEV` | A4 | `close/ma20 - 1` | + |
| `A4_MA60_DEV` | A4 | `close/ma60 - 1` | + |
| `A4_MA20_SLOPE` | A4 | `ma20[t]/ma20[t-20] - 1` | + |
| `A4_ATR_R` | A4 | `ATR14 / close` | − |
| `A4_AMP20` | A4 | `mean((high-low)/prev_close, 20)` | − |
| `A5_RVOL_20` | A5 | `std(resid,20)` | − |
| `A5_RVOL_60` | A5 | `std(resid,60)` | − |
| `A5_RVOL_120` | A5 | `std(resid,120)` | − |
| `A5_IVOL_SHARE60` | A5 | `1 - R²(60日回归)` | − |
| `A5_DRVOL_60` | A5 | `std(resid | resid<0, 60)` | − |

* 全部因子先做逐日横截面 **rank 百分位化** `rank/(n+1) ∈ (0,1)`；缺失不插补。
* A3 残差：`r_ex = close/close[t-1]-1 - index_ret(000300.SH)` 对 **PIT 申万一级行业等权收益** 做 250 日滚动 OLS（≥120 有效点）取残差；
  **市场因子固定用 `000300.SH`**（全历史），不用 `932000.CSI`，以保证 A3/A5 窗口长度一致。
* A5 残差：对三因子基线 `r_mkt + r_smb + r_hml` 做滚动 60/120 日 OLS；`r_smb`/`r_hml` 由**池内** `circ_mv` / `bp` 三分位自建；
  若池内任一三分位有效只数 < 30，则该日风格因子置缺失（报告须披露构造细节）。
* 量价类一律用**相对放大**（换手率 / 量比 / 额比），**不使用成交量/成交额绝对值**。

### 6.2 正交化（冻结）

* **类内（within-group）主口径**：每日取组内因子截面相关矩阵 `S`，**对称正交化（Löwdin）** `Z = F · S^(-1/2)`；
  `S` 特征值 `< 1e-6` 的方向截断（该日该方向置缺失并计数）。
  对照口径 = 顺序 Gram-Schmidt（按 §6 表内 ID 正序）→ 正/逆序结论反转记 `ORDER_SENSITIVE`。
* **类间（cross-group）**：先构造五组等权合成 `C_k = mean(A_k_*_orth)`（组内等权，禁止优化），
  再逐日横截面 OLS 残差化 `C_k^⊥(t) = resid(C_k ~ 1 + C_{-k} + M0)`（`C_{-k}` = 其余四组合成）。
* **基线 M0** `= rank(ln circ_mv) + beta60(000300.SH) + 申万一级行业哑变量`；
  `beta60` = 250 日滚动对 `000300.SH` 的斜率（≥120 有效点）；行业只数 < 3 并入「其他」组。
  **M0 不含动量、反转、量价、波动率**（它们都是待检验的 Alpha 本体）。
* 正交化是**逐日横截面操作**，天然 PIT；**禁止用 VALID/OOS 段估计任何正交化系数**。

### 6.3 增量回归（冻结）

```
因变量 = rank(label)，h ∈ {5, 10}；逐日横截面 OLS
M0    = rank(ln circ_mv) + beta60 + 行业哑变量
M1_k  = M0 + rank(C_k)                          k 逐一
M2a   = M0 + rank(C_A1..C_A5)                   五组等权 COMP_EQ
M2b   = M0 + rank(C_k) 按 COMP_PRIOR 预注册权重
M3    = M0 + 全部 38 个正交化因子
M4_k  = M0 + rank(C_k^⊥)                        ← 独立增量主检验
```

**独立增量判定（I1∧I2∧I3）**：

```
I1  ΔR²(M1_k - M0) > 0  且  β(C_k) 的 t_NW >= 2.0
I2  ΔR²(M4_k - M0) > 0  且  β(C_k^⊥) 的 t_NW >= 2.0
I3  C_k^⊥ 的 |IC_mean| >= 0.005 且与 C_k 同号
全中 → INDEPENDENT；部分 → PARTIAL；全不满足 → REDUNDANT。
```

**预注册权重（**声明在先，非数据优化**）**：

| 合成 | 权重 |
|---|---|
| `COMP_EQ` | 五组各 `1/5` |
| `COMP_PRIOR`（主口径） | `A2=0.30, A3=0.25, A4=0.20, A5=0.15, A1=0.10` |

两口径必须同时报告；结论冲突 → 标注 **`WEIGHT_SENSITIVE`**。**禁止**把权重当可优化参数、禁止网格搜索最优权重。

**单因子级独立增量**：对每个 `A*_*_orth`，在 `M0 + 其余 37 个正交因子 + C_{-k}` 中回归，
取 `β / t_NW / ΔR²`；满足 `t_NW >= 2.0` 且 `ΔR² > 0` 者进入 `zz2k_independent_factors.csv`。
筛选规则**预先写死在本 SPEC**，禁止运行期调整。

### 6.4 Null（冻结）

| 编号 | 定义 | 检验什么 |
|---|---|---|
| **N1**（主判定） | **同一交易日内**随机打乱 `C_k` 值，保持当日截面分布与日期不变 | 截面排序信息 |
| N2 | 同一股票内，把其 IS 段 `C_k` 值随机重排到它出现的日期上 | 个股内时序信息 |
| N3 | 每日从同期 38 个正交因子中随机抽 1 个作为对照因子 | 是否「任意因子都行」 |

单边 `p = P(null 统计量 >= obs)`；`B = 2000`，`rounds = 8`，`min_resolution >= 0.99`。
三套 Null 必须同时报告；主判定用 N1；必须报 `resolution` 以证明对照池非空。

### 6.5 参数扰动格点（冻结，star 设计，**禁止寻找单一最优参数**）

```
h*            : 5 / 10 / 20
q_groups      : 5 / 10
roll_window   : 120 / 250 / 500
vol_window    : 20 / 60 / 120
pool          : U-ZZ2K / U-PROXY
composite     : COMP_EQ / COMP_PRIOR
positive_ratio = 格点中「IC 与基线同号 且 |IC| >= 0.005」的比例
判定           : >= 0.60 → stable ; <= 0.40 → FRAGILE ; 其余 mixed
每个格点内部独立重算正交化与合成（不得跨格复用系数）。
```

──────────────────────────────────────────────
## 7. 判决框架（冻结，`h* = T+5`，主 label `y_ex_zz`）

对 5 个 `C_k`、5 个 `C_k^⊥`、`COMP_EQ`、`COMP_PRIOR` **在两口径下分别**输出四值之一：

```
g1  IS IC 符号符合 §6 预注册方向        g7  参数扰动 stable (positive_ratio >= 0.60)
g2  IS |t_NW| >= 2.0                    g8  三种市场 Regime IC 同号
g3  VALID IC 同号 且 |t| >= 1.5         g9  N1 null p < 0.05
g4  OOS IC 同号                         g10 R-DEDUP 口径 IC 同号 且 |IC| >= 0.005
g5  分层单调性 >= 0.80                  g11 类间正交后 β(C_k^⊥) t_NW >= 2.0
g6  30bp 成本后 Q5-Q1 > 0               g12 Walk-Forward 多数 fold 同号 且 |IC| >= 0.005
```

`ROBUST = 12/12`；`PROMISING = 9..11`；`FRAGILE = 5..8`（或参数位为 FRAGILE）；`NO EDGE <= 4`。

**标注集（冻结）**：`SIGN_CONFLICT` / `UNSTABLE` / `EPISODE_SENSITIVE` / `ORDER_SENSITIVE` /
`WEIGHT_SENSITIVE` / `POOL_SENSITIVE` / `MEMBER_HISTORY_SHORT` / `WF_TRAIN_SHORT` / `OOS_SHORT`。

──────────────────────────────────────────────
## 8. 运行前置不变量（冻结）

1. `SPEC_SHA256` 必须与本文档一致，否则 `EXIT 2`。
2. 全部阈值只在 `zz2k_common.py` 的常量段定义，禁止散落硬编码。
3. `future_column_scan`（4 项）任一失败 → `FATAL` → `EXIT 3`：
   `truncation_invariance` / `pit_fundamental_proof` / `forward_window_isolation` / `label_columns_marked`。
4. 运行顺序：`python zz2k_run.py` → `python zz2k_report.py`（后者必须 `EXIT 0`）。
5. 交付形态：`zz2k_common.py` + `zz2k_run.py` + `zz2k_report.py`。
6. **禁止修改** `hve_common.py` / `fs_common.py` / `mce_common.py` / `hef_common.py`
   以及任何已完成研究的产物与结论、现有交易系统核心逻辑。

──────────────────────────────────────────────
## 9. 研究纪律（冻结，违反即实验无效）

* 禁止为提高 IC 反复调整参数、窗口、因子定义或方向。
* 禁止用 VALID / OOS 段筛选因子、确定方向、估计正交化系数或设权重。
* 禁止对合成做权重优化（`COMP_EQ` / `COMP_PRIOR` 为预注册先验）。
* 禁止只展示显著因子；38 个因子必须**全部报告**，含 IC 为零、覆盖度为 0 者。
* 禁止只报 IC 均值而不报 `t_NW` 与 R-DEDUP 口径。
* 禁止把「放量」直接解释为「资金进入」，禁止把「缩量」直接解释为「卖压衰减」。
* 禁止因某一只股票、某一年、某一层表现好就得出结论；禁止因样本量小而放宽门槛或人为扩池。
* 禁止把因子的截面预测力直接等同于可交易收益（成本与容量未计）。
* 禁止使用未来数据确认历史结构。
* 必须区分**因子本身有效** 与 **因子只是 Momentum / Size / Industry 的代理变量**。

最终目标不是找到「IC 最高的因子」，而是回答：
> 五类 Alpha 中，哪些在控制「规模 + Beta + 行业 + 其余四类」后仍提供**真实且稳定**的独立增量，
> 以及这份增量是否足以支撑第二阶段（组合与执行）继续投入。

──────────────────────────────────────────────
## 10. 产物清单（冻结，SPEC §16.2）

**成分（2）**：`data/zz2k_members.parquet`、`data/bench_members.parquet`。

**必产 CSV（12）**：`zz2k_factor_ic.csv`、`zz2k_orthogonal.csv`、`zz2k_factor_quantile.csv`、
`zz2k_group_composite.csv`、`zz2k_incremental.csv`、`zz2k_independent_factors.csv`、
`zz2k_oos.csv`、`zz2k_walkforward.csv`、`zz2k_cost.csv`、`zz2k_regime.csv`、
`zz2k_null.csv`、`zz2k_funnel.csv`。
除两个 parquet 外，**所有 CSV 必须含 `pool` 列**（`U-ZZ2K` / `U-PROXY`），双口径写在同一文件内。

**报告（1）**：`H_ZZ2K_ALPHA_V1_REPORT.md`。

**补充产物**：`H_ZZ2K_ALPHA_V1_RESULTS.json`、`H_ZZ2K_ALPHA_V1_SUMMARY.json`、
`H_ZZ2K_ALPHA_V1_BIAS_AUDIT.csv`、`H_ZZ2K_ALPHA_V1_FUTURE_SCAN.json`、
`H_ZZ2K_ALPHA_V1_SPEC.md`、`H_ZZ2K_ALPHA_V1_FREEZE.md`。

──────────────────────────────────────────────
## 11. 冻结声明

本文件所列全部阈值、分层、窗口、因子、方向、判据与输出清单，均在**任何结果被观察之前**写定。
运行后修改 = 作弊。若主假设被证伪，按 §14 判决树归档，**不得继续调参**。
`trading_authorization = NO`：本阶段禁止把任何因子转化为实盘 BUY 信号；
Entry / Exit / Position sizing / Execution 属独立第二阶段。
