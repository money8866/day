# H-SLG-01 SPEC v1.0 — A股主升后「二次整理完成度」与「二次突破增量信息」研究

Research ID: `H-SLG-01`　Version: `1.0`　Created: `2026-10-02`
Hypothesis ID space: `H-SLG-01-*`

本文件是**唯一口径来源**。任何代码不得偏离本文件；如需修改，必须执行
`V1 冻结 → 输出结果 → 提出新 Hypothesis → V2`，禁止回填优化（§24 纪律）。

---

## 0. 研究目标

在现有 A 股 Tushare 本地缓存基础上，独立建立事件研究模块，检验：

> **第一波主升 → 高位二次整理 → 整理完成 → 二次突破 → 后续收益**

本研究**不研究**低位 W 底、普通超跌反弹、单纯动量。两个核心问题：

* **H1**：二次整理是否已经从「调整过程」进入「可重新启动的结构状态」（整理完成度）？
* **H2**：在控制第一波涨幅、趋势强度、市场环境、行业强度、个股动量之后，「二次突破事件」
  是否仍能显著预测 T+3 / T+5 / T+10 / T+20 收益（增量信息）？

必须严格区分五个阶段，**不得压缩成单一黑盒评分**：

```
主升 → 二次整理 → 整理完成 → 二次突破 → 后续收益
```

---

## 1. 数据原则

1.1 只使用现有 Tushare 本地缓存，读取路径与公共层固定为：

| 用途 | 来源 |
|---|---|
| 日线 OHLC / 成交量额 / 换手 / ST / 退市 / 涨跌停 / 板块 | `hve_common.build_grid()`（`price_panel.parquet` + `basic_panel.parquet` + `treasure_namechg*.parquet`） |
| 总市值 / 换手率 / PE | `basic_panel.parquet`（经 `mce_common.load_size_mv`） |
| 指数（市场环境 / 市场收益） | `index_panel.parquet`，`000300.SH`（经 `mce_common.regime_ma` / `index_ret`） |
| 行业（PIT 申万一级） | `D:\mystock\cache_daily\industry\sw_industry_map.csv`（经 `hef_common.load_industry_map`） |
| 交易日历 | `calendar.parquet`（经 `hef_common.load_calendar`） |

1.2 **禁止**：临时下载其他行情源、TDX、AkShare、Wind、同花顺、人工挑选股票。
1.3 若某字段缺失，必须在报告中**如实标注缺失字段**，不得悄悄替代数据源。
1.4 复权口径固定为 **qfq**（前复权）；`prev_close := close[t-1]`（前复权序列的上一交易日收盘）。
1.5 本研究为**独立科研模块**，禁止修改现有交易系统核心逻辑。

---

## 2. 严格防止未来函数

2.1 所有事件识别只使用 `T` 及 `T` 以前的数据。
2.2 特别禁止：用未来 N 日最高价确认「左高」；用未来 N 日最低价确认「右底」；
用未来突破结果反向定义整理完成；用未来收益决定样本是否属于有效 W 底。
2.3 本研究**不使用 pivot/swing** 的未来确认版本，改用**因果（PIT）窗口**规则：
结构量一律由「截至当日 t 的向后窗口」计算，因此 `candidate_date = confirmation_date = event_date`，
不存在「未来确认日」被伪装成当日信号的问题。所记录的结构日期（peak / left_bottom /
right_bottom / bounce）全部为**过去日期**，可逐行审计。
2.4 未来信息仅允许出现在 **label 列**（`LABEL_COLUMNS`）：用于事后诊断与「成功/失败突破」
分类，**永不**作为 T 日信号输入。
2.5 建立自动检查 `future_column_scan`（§21），任一项失败 → `FATAL` → 退出码 3。

---

## 3. 研究时间与样本

3.1 面板：`S = 5816` 只 × `N = 2120` 交易日（`20180102 .. 20260924`）。
3.2 烧入（burn-in）：`study_start = 20190101`，`burn_in_sessions = 250`。
   事件只允许出现在 `study_start` 之后；`study_start` 之前的会话仅用于构造向后窗口。
3.3 时间分段（**时间切分，禁止随机切分**）：

| 段 | 区间 | 用途 |
|---|---|---|
| PRE | `.. 20191231` | 仅描述性统计（漏斗 / 结构分布），**不进入** IS/OOS 结论 |
| IS | `20200101 .. 20241231` | 结构定义与参数稳定性主口径 |
| VALID | `20250101 .. 20251231` | 时间验证段 |
| OOS | `20260101 .. 20260924` | 最终样本外段，**禁止**用其反向调整规则 |

3.4 若实际数据范围与 3.1 不符，必须在报告中明确实际 IS/VALID/OOS 区间。
3.5 另有 Walk-Forward：`train 3 年 → test 1 年`，滚动起点 2021，终点 2026（§14）。

---

## 4. 股票池

4.1 **U1（主口径）**：`hve_common.eligibility(g) & (board != 'BSE')`
   —— 含：真实成交、非 ST、非退市、上市 ≥250 交易日、无 ≥60 日停牌洞。
4.2 **U2（幸存者偏差对照）**：`traded & ~st & (board != 'BSE')`（不做上市年限与停牌过滤）。
4.3 北交所（`BSE`，352 只）**排除**，理由：涨跌幅制度 30% 与其他板块不可比。
4.4 历史退市股票**保留其退市前的历史打印**（价格之后为 NaN）。缓存无交易所 `delist_date`，
   故退市点位标志记为 `DELIST_PIT_UNAVAILABLE`；以「末次打印显著早于面板末端」的 `gone` 标志
   作为近似（`gone = 242` 只）。报告必须写明**股票池数量、是否包含退市、是否存在幸存者偏差**，
   并用 U1 vs U2 对照量化其影响。

---

## 5. 因果结构定义（全部只用 t 及之前的数据）

### 5.1 固定窗口

```
LEG_W        = 40    # 第一波主升长度上限（交易日）
CONS_MIN     = 5     # 整理最短
CONS_MAX     = 40    # 整理最长（可扰动轴）
PEAK_LO      = CONS_MIN            = 5
PEAK_HI      = LEG_W + CONS_MIN    = 45
```

### 5.2 峰值（左高）`peak`

`peak_off = argmax(high[t-45 .. t-5])`（同高取**更早**者）；`peak_day = t - peak_off`。
要求 `CONS_MIN <= peak_off <= CONS_MAX`，否则当日不构成候选。

### 5.3 主升起点 `leg_start` 与第一波涨幅

`leg_start = argmin(low[peak_day-40 .. peak_day])`（同低取更早者）。

```
first_rise_pct  = high[peak_day] / low[leg_start] - 1
first_rise_days = peak_day - leg_start
```

门槛：`first_rise_pct >= 0.25`，`first_rise_days >= 5`（扰动轴见 §12）。

### 5.4 整理与左底 / 右底

整理窗口 = `(peak_day, t]`，`consol_days = t - peak_day`。
内部反弹高点 `bounce_off = argmax(high[peak_day+1 .. t])`，`bounce_day = t - bounce_off`。

```
left_bottom  = min(low[peak_day+1 .. bounce_day])     左底
right_bottom = min(low[bounce_day+1 .. t])            右底（要求 t-bounce_day >= 2）
```

→ 四种结构由数据自然落在 A/B/C/D 四类，**不预设哪一类更好**：

| 类型 | 定义 |
|---|---|
| A 标准W | `right_left_ratio ∈ [0.98, 1.05]` |
| B 强趋势W | `right_left_ratio > 1.05` |
| C 深回撤W | `right_left_ratio < 0.98` 且中期趋势未破坏 |
| D 高位平台型 | `retracement_pct < 0.08` |

### 5.5 派生结构变量（全部逐行保存）

```
retracement_pct   = (high[peak_day] - right_bottom) / high[peak_day]
right_left_ratio  = right_bottom / left_bottom
bottom_spacing    = right_bottom_day - left_bottom_day     （交易日）
consol_days       = t - peak_day
neckline          = high[peak_day]                          # 第一波左高阻力位（主口径）
bounce_high       = high[bounce_day]                        # 诊断用第二阻力位
```

分桶（仅用于分层，不用于选参）：

| 变量 | 分桶 |
|---|---|
| `retracement_pct` | `<8%` / `8-15%` / `15-20%` / `20-30%` / `>30%` |
| `right_left_ratio` | `<0.90` / `0.90-0.98` / `0.98-1.05` / `1.05-1.15` / `>1.15` |
| `bottom_spacing` | `3-5` / `6-10` / `11-20` / `21-40` |
| `consol_days` | `5-10` / `11-20` / `21-40` |

`retracement_pct >= 0.05` 为候选门槛（§5.5 的「研究区间 5%~35%」下界）。

---

## 6. 整理完成度变量（独立保存，禁止合成总分）

每条候选同时保存以下**连续变量**，且每个 READY 条件单独成为布尔列：

### 6.1 价格稳定性
`atr_ratio`（整理末期 5 日 ATR/Close）、`std20`、`std5`、`vol_ratio_5_leg =
std(5 日收益) / std(第一波 20 日收益)`。

### 6.2 量能收缩（**只作为待验证变量**，禁止预设「缩量=卖压减少」）
```
rb_vol_over_leg   = vol[right_bottom_day] / mean(vol[leg_start..peak_day])
rb_vol_over_ma20  = vol[right_bottom_day] / prev20mean(vol)
consol_vol_over_leg = mean(vol[peak_day+1..t]) / mean(vol[leg_start..peak_day])
```
分桶参考：`0.4 / 0.6 / 0.8 / 1.0` 附近。

### 6.3 MA20 结构
`close_vs_ma20`、`ma20_slope20`、`days_above_ma20`，分类：
始终在 MA20 上方 / 短暂跌破后收回 / 持续跌破 / MA20 转向。

### 6.4 MA60 结构
`close_vs_ma60`、`ma60_slope20`，分类：MA60 向上 / 走平 / 向下。

### 6.5 右底结构
`rb_drawdown_from_peak`、`rb_dist_ma20`、`rb_dist_ma60`、`rb_dist_left_bottom`。

---

## 7. 三个状态（不主观判断，全部布尔可审计）

```
CONSOLIDATING   第一波主升已完成 + 进入回撤/震荡 + 尚未出现明确再启动
READY           右底已形成 + 最近未创新低 + 波动率下降 + 结构未破坏
                + 价格重新接近短期阻力 + 相对行业强度未明显恶化
FAILED          结构破坏（跌破左底 / 持续跌破 MA60 / MA60 转向 /
                整理期持续放量下跌 / 相对行业持续弱势）
```

`CTX = first_rise_ok & (CONS_MIN <= consol_days <= CONS_MAX)`

### 7.1 READY 的六个独立条件（逐一保存，禁止 `READY_SCORE=100`）

| 位 | 名称 | 规则 | 预注册阈值 |
|---|---|---|---|
| R1 | `RIGHT_BOTTOM_FORMED` | 存在内部反弹且右段长度 ≥ `RB_MIN_DAYS` | 2 |
| R2 | `NO_NEW_LOW` | 右底年龄 ≥ `RB_AGE_MIN`；且右底后无新低于 `rb*(1-NEW_LOW_TOL)` | 3 / 0.005 |
| R3 | `VOL_DOWN` | `vol_ratio_5_leg <= VOL_DOWN_MAX` | 1.00 |
| R4 | `STRUCT_OK` | `min(low[peak+1..t]) >= lb*(1-STRUCT_TOL)` 且 `close >= ma60*(1-MA60_TOL)` 且 `ma60_slope20 >= MA60_SLOPE_MIN` | 0.03 / 0.05 / -0.01 |
| R5 | `NEAR_RES` | `close >= ma20*(1-NEAR_TOL)` 或 `close >= bounce_high*(1-NEAR_TOL)` | 0.05 |
| R6 | `RS_OK` | `rs_sector_20 >= RS_MIN` | -0.05 |

`READY = R1&R2&R3&R4&R5&R6`。**仅作为研究事件，不是买入信号。**

### 7.2 FAILED 的五个独立条件

| 位 | 名称 | 规则 |
|---|---|---|
| F1 | `BREAK_LEFT_BOTTOM` | `min(low[peak+1..t]) < lb*(1-STRUCT_TOL)` |
| F2 | `MA60_SUSTAINED_BREAK` | 最近 10 日中 `close < ma60*(1-MA60_TOL)` 的日数 ≥ 5 |
| F3 | `MA60_TURN_DOWN` | `ma60_slope20 < -0.02` |
| F4 | `VOL_DOWNTREND` | 整理期 `mean(vr20) >= 1.2` 且 `(下跌日 & vr20>=1.5)` 占比 ≥ 0.4 |
| F5 | `RS_PERSIST_WEAK` | 整理期 `mean(rs_sector) <= -0.05` |

`FAILED = F1|F2|F3|F4|F5`；`CONSOLIDATING = CTX & ~READY & ~FAILED`。

---

## 8. 二次突破

8.1 只在 READY 状态**当且仅当**在 `t` 日成立时寻找突破（`READY` 与突破同日，全部使用 ≤ t 信息）。

```
neckline = high[peak_day]                      # 第一波左高附近阻力位（主口径）
breakout(b) = READY & (close > neckline * (1 + b)),  b ∈ {0%, 1%, 2%, 3%}
```

8.2 主口径 `b = 0.00`；同时输出 1% / 2% / 3% 三档。**不得只用一个阈值**。
8.3 突破量能门槛 `v ∈ {0.8, 1.0, 1.2, 1.5, 2.0}`（`vr20 >= v`），主口径 `v = 1.0`。

### 8.4 突破量能变量（逐行保存）
```
brk_vol_over_ma20     = vr20[t]
brk_vol_over_consol   = vol[t] / mean(vol[peak+1..t])
brk_vol_over_leg      = vol[t] / mean(vol[leg_start..peak_day])
```
分桶：`<0.8 / 0.8-1.0 / 1.0-1.2 / 1.2-1.5 / 1.5-2.0 / >2.0`。
核心问题：**突破是否必须放量**（而不是预设「放量一定有效」）。

---

## 9. 四组对照（本研究最重要的比较）

| 组 | 定义 |
|---|---|
| **A** | `CTX & READY & breakout`　主升 → 整理 → 二次突破（主事件） |
| **B** | `~CTX & (close > rh20_prev) & (vr20 >= 1.0)`　普通突破，无「主升+二次整理」结构 |
| **C** | `CTX & READY & ~breakout`（C1）与 `CTX & CONSOLIDATING`（C2）　主升+整理但未突破 |
| **D** | `CTX & FAILED`　主升 → 深度调整 / 结构破坏 |

五组必须等权可比地输出 T+1 / T+3 / T+5 / T+10 / T+20，才能回答
「二次突破到底有没有额外的信息量」。

---

## 10. 未来收益与统一评价

10.1 持有期：`HORIZONS = (1, 3, 5, 10, 20)`，主 horizon `H* = T+5`。
10.2 价格腿：锚点 = 事件日收盘；`forward_return = close[T+h]/close[T] - 1`。
10.3 统一指标：`n / mean / median / win_rate / std / t / MAE / MFE / max_drawdown /
profit_factor / p05 / p95 / 月度聚类 Bootstrap 95% CI`。
10.4 三层收益：`absolute_return`、`excess_market_return`（减 `000300.SH`）、
`excess_sector_return`（减同期同行业等权均值）。

---

## 11. 交易成本

11.1 四档：`0 / 15 / 30 / 50 bp`，主口径 **30bp**（`net30 = gross - 0.0030`）。
11.2 报告必须同时给出 `gross return` 与 `net return`，并给出
`win_rate / median / mean / max_drawdown / profit_factor`。**禁止只展示毛收益，禁止只看胜率。**
11.3 成本在首次运行前登记，看到收益后**不得调低**。

---

## 12. 参数扰动（预注册格点）

轴（括号内为扰动点，首项为主口径）：

| 轴 | 格点 |
|---|---|
| `first_rise_pct` | `0.20 / 0.25 / 0.30` |
| `first_rise_days` | `5 / 10 / 15` |
| `consol_max` | `10 / 20 / 30 / 40` |
| `retrace_min` | `0.05 / 0.10 / 0.15 / 0.20 / 0.25 / 0.30` |
| `break_b` | `0.00 / 0.01 / 0.02 / 0.03` |
| `break_vol` | `0.8 / 1.0 / 1.2 / 1.5 / 2.0` |

12.1 设计：**基线 + 单轴扰动（star 设计）** + **三轴全交叉**
`(first_rise_pct × retrace_min × break_b)`。总计 20 + 72 − 重叠 ≈ 90 个格子。
12.2 输出每个格子的 `n / net30(T+5) / win`，以及
`positive_ratio = 格点中 net30(T+5) > 0 的比例`。
12.3 判定：`positive_ratio >= 0.60` → `stable`；`<= 0.40` → `FRAGILE`；其余 `mixed`。
12.4 若结果仅在单一精确阈值附近有效（相邻格点失效）→ 标记 `FRAGILE`，**不得**记为 PASS。
12.5 **禁止寻找单一最优参数**：报告只呈现邻近参数的稳定性，不呈现「哪个参数收益最高」。

---

## 13. Null Model / 随机对照

`B = 2000`，`rounds = 8`，`min_resolution = 0.99`。

| 编号 | 定义 |
|---|---|
| N1 | 从匹配池随机抽取「第一波涨幅桶 × 市值三分位 × 流动性三分位 × 行业 L1 × 市场状态」相同的替代 cell（**不得抽到自身**） |
| N2 | 同一股票内随机打乱突破日（在其整理窗口内随机取一日作为突破日） |
| N3 | 同一股票内随机取一交易日（仅匹配市场状态） |

输出以季度指数为对照的**单边** `p = P(null_mean >= obs)`；并给出 `resolution`
（替代池命中率）以证明匹配池非空。**三套 Null 必须同时报告。**

---

## 14. OOS / Walk-Forward

14.1 时间切分（§3.3）：IS / VALID / OOS。禁止随机切分。
14.2 Walk-Forward：`train 3 年 → test 1 年`，滚动 `2021..2026`，逐段报告
`n / net30(T+5) / win / IC`。
14.3 **不允许**用 OOS 结果反向调整规则。

---

## 15. 增量信息检验（本研究最终核心）

逐步 OLS，因变量 = `T+h` 前复权收益（h ∈ {5, 10}）：

| 模型 | 自变量 |
|---|---|
| Model 0 | `Market_5D / Market_20D / Sector_5D / Sector_20D` |
| Model 1 | + `MOM20 / MOM60 / Volume_ratio` |
| Model 2 | + `first_rise_pct / first_rise_days` |
| Model 3 | + `retracement_pct / consol_days` |
| Model 4 | + `right_left_ratio / rb_dist_ma20 / vol_ratio_5_leg` |
| Model 5 | + `Breakout`（二次突破哑变量） |

15.1 输出 `R² / ΔR² / IC / RankIC / AUC / OOS-R² / OOS-IC / OOS-RankIC`。
15.2 重点比较 **Model 5 − Model 4**（二次突破增加了多少新预测信息）与
**Model 4 − Model 3**（「整理完成」本身是否已有增量信息）。
15.3 全部自变量除 `Sector_*` / `Market_*` 外必须在**同日横截面**上标准化；
   模型只在 `N_SUB = 400000` 随机子样本上拟合（固定种子），并在时间后半段评估 OOS 指标。

---

## 16. 分层研究

| 维度 | 层 |
|---|---|
| 第一波强度 | `25-40%` / `40-60%` / `>60%` |
| 回撤深度 | `<10%` / `10-20%` / `20-30%` / `>30%` |
| 右底质量 | `右底>左底` / `右底≈左底` / `右底<左底` |
| 量能 | `缩量` / `正常` / `放量` |
| 行业强度 | `强行业` / `普通行业` / `弱行业` |
| 市场 | `上涨` / `震荡` / `下跌` |
| 板块（补充诊断） | `MAIN` / `GEM` / `STAR` |

输出各层的 `n / gross / net30 / win30`。找的是**稳定有效的区域**，不是收益最高的某一格。

---

## 17. 样本选择偏差防护（漏斗）

必须完整输出漏斗，**不得只保存最终成功突破的股票**：

```
主升样本 N → 进入整理 N → 形成右底 N → READY N → 突破 N → 成功突破 N → 失败突破 N
```

其中「成功 / 失败突破」为 **label（未来信息）**：
`FAIL_BRK = min(low[brk+1..brk+10]) < neckline*(1-0.03)`；
`SUC_BRK = ~FAIL_BRK & (max(close[brk+1..brk+10]) >= neckline*(1+0.05))`。

---

## 18. 判决框架

每个事件/分组只输出四值之一：`ROBUST` / `PROMISING` / `FRAGILE` / `NO EDGE`。

12 个布尔位（`H* = T+5`，`net30` 为主口径）：

```
b1  IS 净收益 > 0                b7  参数扰动 stable
b2  OOS 净收益 > 0               b8  三种市场状态非全失效
b3  FULL 净收益 > 0              b9  Null 无法解释 (p < 0.05)
b4  Bootstrap 95% CI 下界 > 0    b10 非少数极端股票驱动 (Top1% 贡献 < 0.5)
b5  BH-FDR q < 0.10              b11 胜过基准 B 与 C
b6  年份方向一致 (IS 内正值年份占比 >= 0.6)   b12 控制 MOM 后事件系数 > 0
```

`ROBUST = 全部 12 位`；`PROMISING = 9..11`；`FRAGILE = 5..8` 或参数位为 FRAGILE；
`NO EDGE = <= 4`。

---

## 19. 最终报告必须回答的 10 个问题

Q1 主升后的二次整理是否具有可重复识别的结构？
Q2 什么样的整理最容易完成？
Q3 右底高于/接近/低于左底，哪几类在 OOS 中表现稳定？
Q4 整理深度多少最稳定？　Q5 整理时间多少最稳定？
Q6 缩量是否真的提供增量信息？　Q7 MA20/MA60 结构是否提供增量信息？
Q8 相对行业强度是否显著提高二次突破质量？
Q9 二次突破相对于普通 Momentum 突破是否有增量信息？
Q10 二次突破之后的收益主要集中在 T+3 / T+5 / T+10 / T+20？

---

## 20. 最终结论格式（**必须逐字采用**）

```
【研究结论】

1. 主升后的二次整理：        PASS / FAIL / MIXED
2. 整理完成：                PASS / FAIL / MIXED
3. 二次突破相对Momentum：    PASS / FAIL / MIXED
4. 最稳定的结构区间：        仅报告 OOS 稳定区间，不寻找最优参数
5. 主要失效环境：            明确列出
6. 成本后：                  0/15/30/50bp 下是否仍成立
7. OOS：                     是否通过
8. 参数稳定性：              是否通过
9. 年度稳定性：              是否通过
10. 最终状态：               RESEARCH_VALIDATED / PARTIALLY_VALIDATED / FAILED
```

禁止输出「W底策略有效」「最强形态」「推荐买入」「稳赚」「胜率神器」等表述。
必须区分**形态本身有效**与**形态只是 Momentum / Volume 的代理变量**。

---

## 21. 运行与产物

脚本：

| 文件 | 职责 |
|---|---|
| `slg_common.py` | 公共层：面板 / 因果结构 / 状态 / 突破 / 统计 / Null / 行业 |
| `slg_run.py` | 主运行：全部计算与 CSV |
| `slg_report.py` | 报告 + 交付校验（必须 `EXIT 0`） |

运行顺序：`python slg_run.py` → `python slg_report.py`

必产产物（10）：

```
second_leg_candidates.csv
second_leg_events.csv
second_leg_consolidation.csv
second_leg_breakout.csv
second_leg_event_study.csv
second_leg_oos.csv
second_leg_parameter_stability.csv
second_leg_subgroup.csv
second_leg_incremental_info.csv
second_leg_report.md
```

补充产物：`H_SLG_01_RESULTS.json`、`H_SLG_01_SUMMARY.json`、`H_SLG_01_FUNNEL.csv`、
`H_SLG_01_NULL.csv`、`H_SLG_01_WALKFORWARD.csv`、`H_SLG_01_BIAS_AUDIT.csv`、
`H_SLG_01_SURVIVOR.csv`。

`future_column_scan` 检查项（任一失败 → `FATAL` → `EXIT 3`）：

1. `truncation_invariance`：面板截断 120 会话后，结构量在不受前向窗口影响的会话上逐位一致。
2. `backward_window_proof`：`neckline = high[peak_day]` 且 `peak_day <= t - CONS_MIN`；
   证明 `peak_off >= 5`（即峰值不含最近 5 日）。
3. `forward_window_isolation`：信号列（T 日及以前）与 label 列（`t > T`）集合不相交。
4. `label_columns_marked`：所有消费 `t > T` 信息的列都登记在 `LABEL_COLUMNS`。

---

## 22. 不变量

1. `SPEC_SHA256` 必须与冻结记录一致，否则 `EXIT 2`。
2. 所有阈值只在 `PREREG` 中定义，禁止散落硬编码。
3. 禁止「只展示成功案例」「只展示平均收益」「忽略成本」「忽略幸存者偏差」「使用未来数据」。
4. 禁止把「缩量」直接解释为「卖压衰减」，禁止把 K 线形态直接解释为主力行为。
5. 禁止因样本量小而人为扩大定义，禁止为得到 PASS 而反复调参。
6. 禁止修改现有交易系统核心逻辑。
7. `trading_authorization = NO`：本阶段**禁止**把任何形态转化为实盘 BUY 信号；
   Entry / Exit / Position sizing / Execution 属独立第二阶段。
