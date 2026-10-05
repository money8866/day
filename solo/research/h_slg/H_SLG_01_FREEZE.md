# H-SLG-01 FREEZE — 预注册冻结记录

Research ID: `H-SLG-01`
Frozen at: `2026-10-03`
Owner: research 自动化流程（无人工挑选样本）

本记录在**任何结果被观察之前**写下。`H_SLG_01_SPEC.md` 一经冻结，
其全部阈值、窗口、分组、判定规则即不可再修改。任何后续修改必须走
`V1 冻结 → 输出结果 → 提出新 Hypothesis → V2`，禁止回填优化（SPEC §24）。

──────────────────────────────────────────────
## 1. 被冻结文件

| 项 | 值 |
|---|---|
| 文件 | `H_SLG_01_SPEC.md` |
| 大小 | `21260` bytes |
| SHA256 | `387CD3B79191C9FB541DD74AA5DC93805FDEBA0F924FB1BEBADC290480DC9FB3` |
| mtime（本地） | `2026-10-03 22:43:09` |
| 版本 | `1.0` |

运行期由 `slg_common.verify_spec()` 逐次重算该哈希：不一致 → `EXIT 2`，
实验作废。日志 `tmb`/`slg` 的 mtime 必须晚于 SPEC 的 mtime，
由 `H_SLG_01_BIAS_AUDIT.csv` 的 `parameter_leakage` 项核验。

──────────────────────────────────────────────
## 2. 研究问题（冻结）

> **第一波主升 → 高位二次整理 → 整理完成 → 二次突破 → 后续收益**

* **H1**：二次整理是否已经从「调整过程」进入「可重新启动的结构状态」？
* **H2**：在控制第一波涨幅、趋势强度、市场环境、行业强度、个股动量之后，
  「二次突破事件」是否仍能显著预测 `T+3 / T+5 / T+10 / T+20` 收益（增量信息）？

不研究低位 W 底、普通超跌反弹、单纯动量。禁止把五阶段压缩成单一黑盒评分。

──────────────────────────────────────────────
## 3. 数据边界（冻结）

只使用现有 Tushare 本地缓存：

| 用途 | 来源 |
|---|---|
| 日线 OHLC / 量额 / 换手 / ST / 退市 / 涨跌停 / 板块 | `hve_common.build_grid()` |
| 总市值 | `basic_panel.parquet` → `mce_common.load_size_mv` |
| 指数（市场环境 / 市场收益） | `index_panel.parquet` `000300.SH` → `mce_common.regime_ma`/`index_ret` |
| 行业（PIT 申万一级） | `sw_industry_map.csv` → `hef_common.load_industry_map` |
| 交易日历 | `calendar.parquet` → `hef_common.load_calendar` |

禁止：临时下载其他行情源、TDX、AkShare、Wind、同花顺、人工挑选股票、未来数据。
侦察结论（`_slg_probe.py`，2026-10-03）：全部字段齐备
（grid `S=5816 × N=2120`，`20180102..20260924`；CSI300 指数；PIT 申万一级 31 类；
`total_mv`/`turnover_rate` 可用）。**无缺失字段。**

──────────────────────────────────────────────
## 4. 时间分段（冻结）

| 段 | 区间 | 用途 |
|---|---|---|
| PRE | `.. 20191231` | 仅描述性统计，不进 IS/OOS 结论 |
| IS | `20200101 .. 20241231` | 结构定义与参数稳定性主口径 |
| VALID | `20250101 .. 20251231` | 时间验证段 |
| OOS | `20260101 .. 20260924` | 最终样本外段，禁止反向调参 |

`study_start = 20190101`，`burn_in_sessions = 250`。
Walk-Forward：`train 3 年 → test 1 年`，滚动 `2021..2026`。

──────────────────────────────────────────────
## 5. 预注册参数（冻结主口径）

```
LEG_W = 40, CONS_MIN = 5, CONS_MAX = 40, PEAK_LO = 5, PEAK_HI = 45
first_rise_pct >= 0.25, first_rise_days >= 5, retracement_pct >= 0.05
neckline = high[peak_day]
break_b   ∈ {0.00, 0.01, 0.02, 0.03}        主口径 0.00
break_vol ∈ {0.8, 1.0, 1.2, 1.5, 2.0}       主口径 1.0
cost_bp   ∈ {0, 15, 30, 50}                 主口径 30bp
HORIZONS  = (1, 3, 5, 10, 20)               H* = T+5
param_stable_thr = 0.60, param_fragile_thr = 0.40
null_B = 2000, null_rounds = 8, min_resolution = 0.99
N_SUB = 400000, seed = 20261101
trading_authorization = NO
```

READY 六位：`R1 RB_MIN_DAYS=2` / `R2 RB_AGE_MIN=3, NEW_LOW_TOL=0.005` /
`R3 VOL_DOWN_MAX=1.00` / `R4 STRUCT_TOL=0.03, MA60_TOL=0.05, MA60_SLOPE_MIN=-0.01` /
`R5 NEAR_TOL=0.05` / `R6 RS_MIN=-0.05`。
FAILED 五位：`F1 BREAK_LEFT_BOTTOM` / `F2 MA60_SUSTAINED_BREAK(>=5/10)` /
`F3 MA60_TURN_DOWN(<-0.02)` / `F4 VOL_DOWNTREND` / `F5 RS_PERSIST_WEAK`。

CAS（因果结构）规则（SPEC §5）：
`peak_off = argmax(high[t-45..t-5])`（同高取更早）→ `peak_day = t - peak_off`；
`leg_start = argmin(low[peak_day-40..peak_day])`（同低取更早）；
`bounce_day = t - argmax(high[peak_day+1..t])`；
`left_bottom = min(low[peak_day+1..bounce_day])`；
`right_bottom = min(low[bounce_day+1..t])`（要求 `t-bounce_day>=2`）。

──────────────────────────────────────────────
## 6. 运行前置不变量（冻结）

1. `SPEC_SHA256` 必须与本文档一致，否则 `EXIT 2`。
2. 全部阈值只在 `PREREG_S` 中定义，禁止散落硬编码。
3. `future_column_scan`（4 项）任一失败 → `FATAL` → `EXIT 3`。
4. 运行顺序：`python slg_run.py` → `python slg_report.py`（后者必须 `EXIT 0`）。
5. 必产 10 个文件（SPEC §21）+ 7 个补充产物。

──────────────────────────────────────────────
## 7. 研究纪律（冻结，违反即实验无效）

* 禁止为提高收益率反复调整参数。
* 禁止根据 OOS 结果修改规则。
* 禁止只保留成功 W 底 / 只研究突破成功股票 / 只看胜率。
* 禁止把「缩量」直接解释成「卖压减少」。
* 禁止把 W 底形态直接等同于买入信号。
* 禁止把 Momentum 与 W 底混成一个总分。
* 禁止使用未来数据确认历史右底 / 用未来最高价最低价定义历史结构。
* 禁止因某一只股票表现很好就得出结论。
* 必须输出完整漏斗，不得只保存最终成功突破的股票（防幸存者偏差）。
* 禁止只展示毛收益 / 忽略成本。

最终目标不是找到「最漂亮的 W 底参数」，而是回答：
> 「主升后的二次整理完成」本身是否具有预测价值，以及
> 「二次突破」在已有趋势/动量信息之外到底增加了多少信息。

──────────────────────────────────────────────
## 8. 产物清单（冻结）

**必产（10）**：`second_leg_candidates.csv`、`second_leg_events.csv`、
`second_leg_consolidation.csv`、`second_leg_breakout.csv`、
`second_leg_event_study.csv`、`second_leg_oos.csv`、
`second_leg_parameter_stability.csv`、`second_leg_subgroup.csv`、
`second_leg_incremental_info.csv`、`second_leg_report.md`。

**补充**：`H_SLG_01_RESULTS.json`、`H_SLG_01_SUMMARY.json`、`H_SLG_01_FUNNEL.csv`、
`H_SLG_01_NULL.csv`、`H_SLG_01_WALKFORWARD.csv`、`H_SLG_01_BIAS_AUDIT.csv`、
`H_SLG_01_SURVIVOR.csv`。
