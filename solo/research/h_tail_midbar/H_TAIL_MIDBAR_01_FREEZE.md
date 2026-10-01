# H-TAIL-MIDBAR-01 — 预注册冻结记录（FREEZE RECORD）

    Research ID  : H-TAIL-MIDBAR-01
    Title        : 尾盘惯性「第一根 >=3% 中阳线 -> 次日卖出」
    Frozen at    : 2026-10-01 08:40:52 (Asia/Shanghai)
    Frozen by    : 运行任何一行统计代码之前
    Parent       : H-ALPHA-SOURCE-01 (No robust Alpha, unchanged)

---

## 1. 被冻结的文件

| 文件 | SHA256 | mtime | bytes |
|---|---|---|---|
| `H_TAIL_MIDBAR_01_SPEC.md` | `396329014C3233DC7BB31AA0B981CD577E140ED31D57E880AC3D56695BB4914D` | 2026-10-01 08:40:52 | 17554 |

`H_TAIL_MIDBAR_01_SPEC.md` 是本次实验**唯一口径来源**。任何在本记录之后
对其的修改都会使实验失效：`tmb_run.py` 在启动时会重新计算该文件哈希并与
上表比对，不一致即中止（`EXIT 2`）。

---

## 2. 冻结的内容（不可事后修改）

| 类别 | 冻结取值 |
|---|---|
| 事件 | `ret_t >= thr` (g['pct_chg']) & `close_t > open_t` & `body_t >= 0.03` & `clv_t >= 0.65` |
| `body` 定义 | `(close-open)/open` — 任务书定义，**不复用** hve_common 的 `(close-open)/(high-low)` |
| 第一根 | `First_w = E0 & ~any_prev(E0, w)`，窗口 `[t-w, t-1]` **不含当日**；F1/F2/F3 = 5/10/20 |
| Primary 第一根 | **F2 (w=10)** |
| Primary 涨幅阈值 | **3%** |
| MA 结构 | M1 短强 / M2 短线转强 / M3 均线共振 / M4 低位启动（互不排斥）；`MApresent = M1|M2|M3|M4` |
| Primary 量能阀门 | **V5 = 1.20 <= VR20 <= 3.00** |
| `VR20` | `vol_t / mean(vol[t-20 : t-1])`，分母**不含当日**；停牌日 `vol = NaN` |
| PRIMARY ARM | **`First(10) & MApresent & V5`** |
| Entry | `close[t]`（primary）/ `open[t+1]`（第二执行模型）；**禁止任何 T 日盘中价成交** |
| Exit | `close[t+1]` 为唯一正式结果；`open/high/low[t+1]` 仅作诊断 |
| 成本阶梯 | 0 / 10 / 20 / 30 / 50 bp；**Primary = 30bp** |
| 尾部检验 | leave top 1% / 5% / 10% |
| 样本切分 | 起点 2022-01-01；IS 2022–2024 / Validation 2025 / OOS 2026 |
| 随机种子 | 20261101 |
| Bootstrap | 月度聚类，B = 1000 |
| 参数网格 | 6 阈值 × 3 窗口 × 5 VR × 5 MA = 450 格（仅扰动，不优化） |
| 判决 | 只允许 §24 的七个取值；`trading_authorization = NO` |

---

## 3. 数据可用性结论（冻结前已核实）

- 对 `D:\mystock\cache_daily\` 做文件名模式扫描（`*min*` / `*5m*` / `*15m*` /
  `*60m*` / `*1min*`）：**零命中**。
- 因此本实验属于任务书 §2 **情形 A：日线代理实验**，最终报告必须逐字声明：

> **本研究验证的是「收盘中阳线 → 次日收益」，而不是严格意义上的「尾盘惯性」。**

- 数据源：现有 Tushare cache 面板（`research/hve/hve_common.py::build_grid`，
  只读依赖）。不使用 TDX / AkShare / Baostock / Yahoo / JoinQuant / Ricequant
  或任何外部行情 API。

---

## 4. 下游产物（由本次冻结派生）

```text
H_TAIL_MIDBAR_01_EVENTS.csv        H_TAIL_MIDBAR_01_NULL.csv
H_TAIL_MIDBAR_01_MA.csv            H_TAIL_MIDBAR_01_BOOTSTRAP.csv
H_TAIL_MIDBAR_01_VOLUME.csv        H_TAIL_MIDBAR_01_OOS.csv
H_TAIL_MIDBAR_01_THRESHOLD.csv     H_TAIL_MIDBAR_01_WALK_FORWARD.csv
H_TAIL_MIDBAR_01_BIAS_AUDIT.csv    H_TAIL_MIDBAR_01_TAIL.csv
H_TAIL_MIDBAR_01_SUMMARY.json      H_TAIL_MIDBAR_01_REPORT.md
（附加补充表，任务书 §25 允许"至少"生成）
H_TAIL_MIDBAR_01_PARAM_GRID.csv    H_TAIL_MIDBAR_01_REGIME.csv
H_TAIL_MIDBAR_01_OVERLAP.csv       H_TAIL_MIDBAR_01_RESULTS.json
```

脚本：`tmb_common.py`（公共层）、`tmb_run.py`（主运行）、`has_report.py`（报告 + 交付校验）。

**运行顺序**：`python tmb_run.py` -> `python has_report.py`（后者必须 `EXIT 0`）。

---

## 5. 冻结声明

本文件所列全部阈值、分层、窗口、判据与输出清单，均在**任何结果被观察之前**
写定。运行后修改 = 作弊。若主假设被证伪，按 §24 判决树归档，**不得继续调参**。
