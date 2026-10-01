# HVE V1 实施计划

状态：**勘察完成，未写任何代码**（依据任务书 §四「不要先写代码」）
依据：《HVE V1 开发任务》共 54 节
产出：本文件即 §四 指定交付物 `HVE_IMPLEMENTATION_PLAN.md`

---

## 0. 勘察结论摘要（先读这段）

四项事实决定整个实施路径，全部为本次实测（2026-09-30）：

1. **数据入口唯一**：`D:\mystock\cache_daily\stock_data.db`（SQLite，23.5 GB）。现有 HVT-BULL 已通过
   [data_loader.py](file:///d:/mystock/solo/hvt_bull/data_loader.py) 稳定读取该库，HVE 直接复用，**不新建任何下载通道**（§二/§三满足）。

2. **可复用面充足**：股票池、涨停判定、复权/MA/ATR、落库、报告模板均已有现成实现（见 §2）。**无需抽取或修改任何公共函数**，因此 §一「如果必须修改公共函数」的前置条件不成立，零改动可达成。

3. **研究层已有先导资产**：`research/hve/hve_common.py` 是上一轮 HVE 研究的公共层，已构建 468 MB 面板缓存
   `research/hve/data/hve_grid.npz`（S=5816 × N=2120 天，20180102~20260924，含 qfq OHLC / vol / amount / turnover /
   PIT ST·退市标记 / 板块化涨停·跌停·一字板标记）。自检日志给出关键先导证据：
   **裸 HVE（VR20≥2）聚类后 297,988 个事件，T+10 中位收益 -1.29%，PF 0.9738**。
   即「高量事件本身没有 Alpha」——这正是任务书 §15「HVE 是事件不是买点」的实证依据。

4. **两处口径冲突必须显式裁决**（详见 §7.1、§7.2）：研究层 PREREG 的簇锚点是 `max_amount`，任务书 §9 要求 `first`；
   研究层状态规则用 `HVE_close` 比例带，任务书 §17/§23 用 `MA20 - 1×ATR20`。HVE V1 **按任务书实现**，研究层仅作方法学参照。

---

## 1. 数据入口

### 1.1 唯一数据源（实测）

| 表 | 来源 | 行数 | 日期区间 | 交易日数 | HVE 用途 |
|---|---|---|---|---|---|
| `daily_cache` | pro.daily | 7,119,610 | 20210104 ~ 20260930 | 1,393 | OHLC / vol / amount / pre_close / pct_chg（**不复权原值**） |
| `daily_basic_cache` | pro.daily_basic | 4,862,749 | 20230103 ~ 20260930 | 908 | turnover_rate / total_mv / circ_mv（§7 流动性） |
| `adj_factor_cache` | pro.adj_factor | 4,898,686 | 全区间 | — | 复权因子（收益统计） |
| `index_daily_cache` | pro.index_daily | 11,365 | 20200102 ~ 20260930 | — | §31 市场环境（000300.SH / 000001.SH / 399006.SZ / 000688.SH / 000852.SH） |

`index_daily_cache` 字段：`ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount`。

### 1.2 读取入口（复用，不新建）

统一走 [data_loader.py](file:///d:/mystock/solo/hvt_bull/data_loader.py) 的 `HvtDataLoader`（**只读调用，不修改该文件**）：

| 方法 | 签名 | 用途 |
|---|---|---|
| `load` | `(ts_code, start_date, end_date) -> DataFrame \| None` | 单股时序（三表 JOIN，含 adj_factor），进程内缓存 |
| `query_cross_section` | `(trade_date, fields=...) -> DataFrame` | 当日截面（股票池、breadth） |
| `trade_dates` | `(start_date, end_date) -> list[str]` | 交易日历（以 000001.SZ 为准） |
| `get_name` | `(ts_code) -> str` | 股票名称 |

补充读取（同样只读）：
- 股票基础信息：`stock_cache.load_stock_basic()` → `ts_code / name / list_date`
- 复权因子：`stock_cache.cached_adj_factor(ts_code, start, end)`
- MA / ATR / 复权：`stock_cache.compute_factor_indicators(df)`
- 指数序列：`SELECT ... FROM index_daily_cache WHERE ts_code=? AND trade_date<=?`（HVE 自带轻量查询，见 §7.2）

### 1.3 价格与收益口径

- **信号判定用不复权原值**（`daily_cache` 原值）。理由：HVE 全部条件（close>open、CLV、VR20、突破幅度）都是当日/近期的原始价格关系，且必须与实盘行情对齐。
- **收益统计必须用复权**：`ret = (close_t2 × adj_t2) / (close_t1 × adj_t1) − 1`。用区间两端复权因子比值，**不引入未来函数**（不使用任何 t2 之后的 adj 基准）。
- 除权日可能污染原始价差，故 §45 边界测试需覆盖「复权 vs 不复权」差异样本。

### 1.4 回测区间可行性（关键约束）

`daily_cache` 起始于 20210104。扣除 §6 要求的 `VR20` 与 MA60 预热（约 60 日）及 §8「上市 ≥120 日」，
**HVE V1 在 `stock_data.db` 上可用的信号区间 ≈ 2022Q1 ~ 2026-09-30，约 4.6 年**。

- `daily_basic_cache` 仅从 20230103 起 → **turnover / 市值**相关分层（§51 大中小盘）只能覆盖 2023+。
  但 HVE V1 核心不依赖 turnover（只用 `vol`），故不影响主结论；分层统计需标注样本区间。
- 若要更长历史（2018 起），走 §1.5 研究层面板（qfq + PIT ST），用于研究而不用于每日流程。

### 1.5 研究层面板（已有缓存，只读复用）

`research/hve/hve_common.py::build_grid(use_cache=True)` 直接加载 `research/hve/data/hve_grid.npz`：

- S=5816 / N=2120（20180102~20260924），返回 dict 含
  `open/high/low/close`(**qfq 复权**)、`vol/amount/turnover`、`pct_chg`、`traded`、`st`、`delist`、
  `limup/limdn/oneword`（板块化）、`board`、`d2i/s2i`。
- **注意**：`build_grid(use_cache=False)` 存在既有 bug（`hve_common.py:394` `rclose` 未绑定，UnboundLocalError）。
  HVE V1 **只调用 `use_cache=True` 分支**，不修该文件（§一零改动）。

---

## 2. 可复用函数

按「复用方式」分三档：**A=直接 import 调用**（只读）；**B=参照仿写**（不 import，避免耦合与导入期副作用）；**C=仅方法学借鉴**。

| 档 | 函数 / 常量 | 位置 | HVE 用途 |
|---|---|---|---|
| A | `HvtDataLoader.load / query_cross_section / trade_dates / get_name` | [data_loader.py](file:///d:/mystock/solo/hvt_bull/data_loader.py) | 全部数据读取（§1.2） |
| A | `_universe(loader, trade_date, cfg)` | [daily.py](file:///d:/mystock/solo/hvt_bull/daily.py#L40-L102) | §8 股票池（沪深A股 / 剔 ST·退 / 剔北交所 / 上市≥120日 / 市值30亿 / 20日均额3000万） |
| A | `_is_limit_up_close(close, pre_close, ts_code)`、`_limit_up_ratio(ts_code)` | [engine.py](file:///d:/mystock/solo/hvt_bull/engine.py#L48-L59) | §46 `tradability_flag` 涨停判定（Decimal 交易所口径，创业板/科创板 20cm） |
| A | `stock_cache.load_stock_basic()` | [stock_cache.py](file:///d:/mystock/solo/stock_cache.py#L654) | 名称 / 上市日（universe 依赖） |
| A | `stock_cache.cached_adj_factor()`、`compute_factor_indicators()` | [stock_cache.py](file:///d:/mystock/solo/stock_cache.py#L509) · [#L1482](file:///d:/mystock/solo/stock_cache.py#L1482) | §1.3 复权收益、MA/ATR（§45 ATR20 缺失测试） |
| A | `stock_pick_db.record_picks / update_tracking` | [stock_pick_db.py](file:///d:/mystock/solo/stock_pick_db.py#L227) · [#L367](file:///d:/mystock/solo/stock_pick_db.py#L367) | 可选：落库跟踪（`strategy_id='hve_v1'`，幂等 upsert） |
| A | `research/hve/hve_common.py`：`build_grid`(cache) / `build_indicators` / `eligibility` / `cluster_events` / `cluster_events_ranked` / `events_from_mask` / `fwd_ret` / `path_metrics` / `perf_stats` / `perf_row` / `ic_stats` / `bh_fdr` / `xsz` / `winsor_xs` / `spearman_with_p` / `_board_of` / `save_csv` / `save_json` / `Log` | [hve_common.py](file:///d:/mystock/solo/research/hve/hve_common.py) | **回测/研究侧的矩阵计算与统计内核**（T+3/5/10/20、MFE/MAE/maxdd/vol 比、PF/期望/t 值、FDR、尾部占比）。只 import，不改文件 |
| B | `_md_to_email_html(md, title, subtitle)` + `_MAIL_CSS` + `send_email(text, trade_date)` | [push.py](file:///d:/mystock/solo/hvt_bull/push.py) | §47/§49 邮件推送：**仿写一份**（22px 卡片式移动端 HTML、Agent Mail CLI、`--body-file` 限 cwd 内）。不 import，理由见 `project_memory`：hvt_bull 有意零外部依赖 |
| B | `run_daily_push.py` 编排范式 | [run_daily_push.py](file:///d:/mystock/solo/hvt_bull/run_daily_push.py) | 每日「扫描 → 落盘 → 推送」调用链骨架 |
| B | `backtest.py` 事件研究法骨架（按股票遍历、事件去重、T+N 收益、分组统计） | [backtest.py](file:///d:/mystock/solo/hvt_bull/backtest.py) | §37 回测主循环结构参照 |
| C | W7 二波纯函数：`breakout_day` / `breakout_retest` / `state_and_features` / `w7_stop_price` | [w7_second_wave_engine.py](file:///d:/mystock/solo/w7_second_wave_engine.py#L615-L682) | §21/§30 语义对齐参照（「突破触发价不破、收盘跌回离场」）。**不 import**：W7 无 config、常量硬编码，import 会带来无谓耦合 |
| C | `EXIT_IF_CLOSE_BELOW_TRIG` | [w7_t1_gate.py](file:///d:/mystock/solo/w7_t1_gate.py#L43) | `invalid_price` 规则参照（§30 HVE-2ND） |
| C | `research/hve/hve_common.PREREG` | [hve_common.py](file:///d:/mystock/solo/research/hve/hve_common.py#L49-L185) | 预注册参数表范式：`cost_bp=(0,10,20,30,50)`、`horizons=(3,5,10,20)`、`tail_flags=(0.05,0.10)`、`cluster_gap_primary=5`、WF 划分 `(2019-2022 IS / 2023-2024 VALID / 2025-2026 OOS)` — `hve_config.json` 的默认值直接对齐 |

**无需修改任何既有文件**，故 §一 的「备份 + 单测 + 原输出 100% 不变」前置条件不触发。

---

## 3. 新增文件

全部落在新目录 `hve_v1/`，与既有模块零交叉。

```text
hve_v1/
  __init__.py
  hve_config.json         # §41 参数集中配置，每项标注 V1_INITIAL_HYPOTHESIS
  data.py                 # 数据层：HvtDataLoader 封装 + 指数序列 + 复权收益
  indicators.py           # VR20 / CLV / MA20·60 / ATR20 / 区间高·低点 / 回撤（严格 PIT）
  event.py                # §6 HVE 事件定义 + §9 事件聚簇去重（cluster_id / primary / max_vol）
  universe.py             # §7/§8 股票池（调用 hvt_bull.daily._universe）
  state.py                # §25 状态机 NORMAL→HVE_EVENT→{HVE_BULL_WATCH|DIGESTION}→FAIL
  signals.py              # §14 HVE_BULL / §21 HVE_2ND / §22 WATCH / §23 FAIL + §29·§30 entry/invalid
  tradability.py          # §46 tradability_flag（LIQUID / LIMIT_UP_RISK / ONE_PRICE_BOARD）
  market.py               # §31/§32 市场环境三态 + §32 CONDITIONAL 降级
  scanner.py              # 每日扫描 → hve_daily_YYYYMMDD.json/.md（§27/§47/§48/§49）
  backtest.py             # §37/§38/§39/§40 T+3·5·10·20 + 尾部依赖 + 成本梯度 + WF 接口
  cross_check.py          # §35/§36 与 HVT-BULL / W7 交集与四类 Case 落盘
  report.py               # §51 HVE_V1_RESEARCH.md + §53 验收输出块
  push.py                 # 邮件推送（仿 hvt_bull/push.py，22px HTML 卡片式）
  run_daily.py            # 每日入口（argparse，§42 参数扰动开关）
  backtest_run.py         # 回测入口
  pytest.ini              # 对齐 theme_engine 范式：testpaths = tests
  tests/
    __init__.py
    test_hve_event.py       # §43
    test_hve_bull.py        # §43
    test_hve_2nd.py         # §43
    test_hve_state.py       # §43
    test_hve_no_lookahead.py# §43/§44 future_data_mutation
    test_hve_edge_cases.py  # §45 high==low / vol==0 / MA·ATR 缺失 / 一字板 / 极端量
```

产物（`report_daily/`，新命名空间，不覆盖任何现有文件）：

```text
report_daily/hve_daily_YYYYMMDD.json      # §47 每日四态
report_daily/hve_daily_YYYYMMDD.md        # §49 展示（排序见 §48）
report_daily/hve_backtest_YYYYMMDD.json   # §37/§38/§39 统计
report_daily/hve_cross_YYYYMMDD.json      # §35/§36 交集与 Case
HVE_V1_RESEARCH.md                        # §51（落在 hve_v1/ 下）
```

CLI（§42）：

```text
python hve_v1/run_daily.py --date 20260930
                           [--hve-vr20 2.0] [--hve-clv 0.65] [--hve-drawdown 0.08]
                           [--hve-volume-decay 0.60] [--hve-digest-days 3]
                           [--hve-breakout-vr 1.20] [--push]
python hve_v1/backtest_run.py --start 20220101 --end 20260930 [--cost-bp 30]
```

---

## 4. 新增字段

### 4.1 事件表（§10 + §11）

```text
ts_code, trade_date, hve_flag
volume, vol_ma20, vr20            # VR20 = volume / 前20日均量（不含当日）
amount, amount_ma20
open, high, low, close
daily_return, clv                 # clv = (close-low)/(high-low)，high==low → 缺失、不产生事件
ma20, ma60
event_cluster_id, event_date      # §9 聚类：间隔 ≤5 交易日 → 同簇；primary = 簇内第一个（见 §7.1）
distance_to_20d_high, distance_to_60d_high   # 仅用事件日「之前」的数据
```
簇内附加（研究用）：`cluster_max_volume_date`、`cluster_max_volume_ratio`

### 4.2 状态窗口（§11）

```text
return_t1 / t3 / t5 / t10 / t20
max_return_5d / 10d / 20d
max_drawdown_5d / 10d / 20d
volume_t1 / t3 / t5 / t10
volume_ratio_to_hve
```

### 4.3 每日信号（§27 + §29 + §46 + §33）

```text
trade_date, ts_code, name
signal_type, state                 # HVE_BULL / HVE_2ND / HVE_WATCH / HVE_FAIL / NO_TRADE
event_date, days_since_hve
vr20, hve_return, hve_clv
current_return, current_volume_ratio
ma20, ma60
drawdown_from_hve, volume_decay_5d
consolidation_high, breakout_distance
entry_price, trigger_price         # §29：entry=当日收盘；trigger=整理区突破价（另存）
invalid_price                      # §30：明确「哪个条件先触发」，不用裸 max/min
market_regime                      # MARKET_OK / MARKET_NEUTRAL / MARKET_RISK
reason                             # §28 机器可读码
tradability_flag                   # §46 LIQUID / LIMIT_UP_RISK / ONE_PRICE_BOARD
theme, theme_heat, theme_rank      # §33 仅输出字段，不参与核心算法
```

原因码（§28，机器可读，禁止自然语言替代）：

```text
HVE_BULL : "HVE + MA20_UP + STRUCTURE_OK + RE_EXPANSION"
HVE_2ND  : "HVE + DIGESTION + VOLUME_CONTRACTION + STRUCTURE_OK + BREAKOUT"
WATCH    : "HVE + STRUCTURE_OK + WAIT_RE_EXPANSION"
FAIL     : "HVE + STRUCTURE_BREAK"
```

### 4.4 配置字段（§41，`hve_v1/hve_config.json`）

```json
{
  "_meta": { "status": "V1_INITIAL_HYPOTHESIS", "note": "非 OPTIMAL_PARAMETER，禁止为提升历史胜率而调整" },
  "vr20_min": 2.0,
  "return_min": 0.02,
  "clv_min": 0.65,
  "bull_max_drawdown": 0.08,
  "second_max_drawdown": 0.10,
  "structure_atr_k": 1.0,
  "atr_window": 20,
  "volume_decay_max": 0.60,
  "min_digest_days": 3,
  "reexpansion_volume_ratio": 1.20,
  "breakout_volume_ratio": 1.20,
  "breakout_lookback_high_days": 3,
  "fail_cooldown_days": 10,
  "cluster_gap_days": 5,
  "cluster_anchor": "first",
  "post_hve_window": 20,
  "universe": { "min_market_cap": 30.0, "min_avg_amount_20": 3000.0,
                "min_listed_days": 120, "exclude_st": true, "exclude_bj": true },
  "market": { "index": "000300.SH", "ma_fast": 20, "ma_slow": 60 },
  "backtest": { "horizons": [3, 5, 10, 20], "cost_bp": [0, 10, 20, 30, 50],
                "primary_cost_bp": 30, "tail_flags": [0.05, 0.10],
                "wf": { "is": [2019, 2022], "valid": [2023, 2024], "oos": [2025, 2026] } }
}
```

---

## 5. 与现有模块接口

### 5.1 只读依赖（import 清单）

```text
hvt_bull.data_loader.HvtDataLoader        # 数据读取
hvt_bull.daily._universe                  # 股票池（需实测导入无副作用，见 §7.4）
hvt_bull.engine._is_limit_up_close / _limit_up_ratio   # 涨停判定
stock_cache                               # load_stock_basic / cached_adj_factor / compute_factor_indicators
research.hve.hve_common                   # 回测内核（仅 use_cache=True）
stock_pick_db                             # 可选落库
```
全部为**函数级只读调用**，不 monkey-patch、不写入上述任何模块的缓存或状态。

### 5.2 只写产物（命名空间隔离）

HVE 只写 `report_daily/hve_*_{date}.{json,md}` 与 `hve_v1/HVE_V1_RESEARCH.md`。
已核验：`report_daily/` 内**不存在** `hve_*` 文件，零覆盖风险。

### 5.3 交叉验证输入（§35/§36）

| 来源 | 文件 | 结构 | 取用 |
|---|---|---|---|
| HVT-BULL | `report_daily/hvt_bull_{date}.json` | 顶层 `events[]`（含 `ts_code`/`state`）、`te_buy_pool`、`first_echelon_pool` | `HVE_BULL ∩ HVT-BULL`、`HVE_2ND ∩ HVT-BULL` |
| W7 二波 | `report_daily/w7_today_action_{date}.json` | `{trade_date, count, signals[]}`，signal 含 `code`/`state`/`type`/`level`/`entry`/`reason` | `HVE_2ND ∩ W7`、`HVE_BULL ∩ W7` |

输出 `overlap_count` / `overlap_ratio`，并按 §36 落盘四类样本：
Case1（HVT 有 HVE 无）、Case2（W7 有 HVE 无）、Case3（HVE 有 HVT/W7 均无）、Case4（同时信号）。
注：HVT-BULL 与 W7 的「信号」定义不同（HVT 是结构层池，W7 是五态可操作榜），交集判定的口径须在报告中标明，避免误读。

### 5.4 调度（§47「不要修改现有日报」）

`run_all.bat` 位于工作区外 `d:\mystock\`，且**本任务不修改它**。
HVE 自带 `hve_v1/run_hve_daily.bat`，由用户决定是否挂 Task Scheduler（参照 `eld/run_eld_daily.bat` 的独立任务模式）。
在用户明确要求之前，HVE 不进主流程、不产生定时任务。

---

## 6. 不修改的模块

### 6.1 零改动清单

```text
hvt_bull/ 全部 15 个文件（data_loader / engine / config.yaml / daily / models / context /
          trade_execution / future_expansion / expectancy / similarity / backtest /
          te_backtest / te_rocket_filter / push / run_daily_push / __init__）
w7_second_wave_engine.py / w7_t1_gate.py / w7_t20_right_tail_engine.py / w7_*.py
tushare_quant.py
theme_* 全部（theme_heat_v22.py / theme_score_v2.py / theme_config.json / build_theme_stock_map_v2.py …）
market_regime_v3/*          # 全部模块
etf_* / sli/ / ige/ / eld/ / pbp/ / prb/ / rib/ / nd2_* / er20_*
stock_cache.py / cache_config.py / stock_pick_db.py
research/hve/hve_common.py  # 只 import，不修（含其 build_grid 既有 bug）
d:\mystock\run_all.bat      # 工作区外，不触碰
```

### 6.2 验证手段（对应 §52 G2）

1. 实施前对上述文件生成 SHA-256 快照（`hve_v1/_audit/baseline_sha256.txt`）。
2. 实施后重新计算并比对，输出 `HVT = UNCHANGED / POTENTIALLY_CHANGED`（§53 验收块）。
3. 行为侧验证：改动前后各跑一次 HVT-BULL 与 W7 的当日输出，比对 `hvt_bull_{date}.json` 与
   `w7_today_action_{date}.json` 逐字节一致（比哈希更强，能捕捉非确定性）。

---

## 7. 关键设计决策与冲突裁决

### 7.1 §9 簇锚点：`first` vs 研究层 `max_amount`（冲突）

- 任务书 §9 明文：「以第一次符合条件的 HVE 作为 primary event」。
- 既有 `research/hve/hve_common.PREREG` 冻结的是 `cluster_anchor_primary='max_amount'`。
- **裁决**：HVE V1 **按任务书 = `first`**（`hve_config.cluster_anchor="first"`），同时输出
  `cluster_max_volume_date` / `cluster_max_volume_ratio` 供研究；回测中把 `max_amount` 作敏感性对照项报告。
- 依据 §9 附注「V1 不根据未来收益选择 primary event」——`first` 满足该约束（`max_amount` 亦不违反，但以任务书为准）。

### 7.2 §31 市场环境：轻量自算 vs `market_regime_v3`

- `market_regime_v3.MarketRegimeV3.run()` 是 7 层 Pipeline（指数强度 / 宽度 / 情绪 / 风格 / 风险偏好 / 主题共振 /
  评分 / 状态机 / 热度 + Alpha 引擎），依赖多个 parquet 与 15+ 引擎，单次运行开销大，且会反向耦合 HVE。
- **裁决**：V1 用 `index_daily_cache` **自算**轻量三态（§31 明确「至少参考」的正是这四项）：

```text
MARKET_OK      : 指数 close > MA20 且 MA20 > MA60（且 breadth ≥ 阈值）
MARKET_NEUTRAL : 介于两者之间
MARKET_RISK    : 指数 close < MA60（或 breadth 破位）
```
  breadth 由 `daily_cache` 当日截面 `pct_chg` 计算（上涨家数 / 总家数）；成交额取截面 `amount` 合计。
- §32 关系：`OK` 正常出 BUY；`NEUTRAL` 照出 BUY 但标 `regime=NEUTRAL`；`RISK` **不删信号**，改 `signal=CONDITIONAL`（避免污染 Alpha 研究）。
- 预留升级接口：`market.py` 暴露 `MARKET_PROVIDER` 钩子，日后可切到 `market_regime_v3`，不改调用方。

### 7.3 §26 无综合评分 / §15 HVE 当日不 BUY

- HVE 当日状态严格为 `HVE_EVENT`，**不产生 `signal=BUY`**（TDD 用例 `test_hve_event.py` 显式断言）。
- 全模块禁止 0~100 综合分；§48 的展示排序用单维原始量（HVE-BULL 按再扩张强度、HVE-2ND 按突破幅度+量能），
  字段名不得含 `score` / `alpha`。

### 7.4 `from hvt_bull.daily import _universe` 的副作用核查（实施第一步）

`hvt_bull/daily.py` 顶层 import 了 engine / context / future_expansion / trade_execution / yaml，并插入 BASE_DIR 到 `sys.path`。
需实测：导入该模块是否触发任何网络 / 数据库写 / 缓存写。
- 若**无副作用** → 直接复用 `_universe`。
- 若**有副作用** → 在 `hve_v1/universe.py` 内**复制同口径逻辑**（不改原文件），并在计划回执中说明。
两条路径都不违反 §一。

### 7.5 §18 措辞纪律

代码、`reason` 码、报告全文统一使用 `Volume Contraction`，
**禁止**出现 `selling pressure reduced` / `卖压减少` 等未经证实的因果表述。此条进 `test_hve_state.py` 的文本断言。

### 7.6 §三 不建第二套行情系统

HVE **不新增任何下载器**：`hve_v1/` 内不含 requests / tushare client / TDX / AKShare / 爬虫调用。
数据缺失一律 fail-soft（如 IGE 因子的既有做法），不自动补数。

---

## 8. 验收对照（§52 G1~G12 → 验证方式）

| 项 | 判据 | 验证手段 |
|---|---|---|
| G1 | 完全复用现有 Tushare 缓存 | 代码扫描：`hve_v1/` 无任何新数据源；`data.py` 唯一入口为 `HvtDataLoader` |
| G2 | 未修改 HVT/W7 行为 | §6.2 哈希快照 + 当日 JSON 逐字节比对 |
| G3 | HVE 当日未被误判为 BUY | `test_hve_event.py` 断言 + 每日产物中 `hve_flag=True` 行 `signal != BUY` |
| G4 | 正确识别 HVE-BULL | `test_hve_bull.py`（§14 七条件逐条构造） |
| G5 | 正确识别 HVE-2ND | `test_hve_2nd.py`（§21 九条件逐条构造） |
| G6 | 正确识别 HVE-FAIL | `test_hve_state.py`（回撤>10% / close<MA20−ATR20 / 结构跌破三路径） |
| G7 | 无 look-ahead | §44 `future_data_mutation`：扰动 T+1 之后数据，断言 T 之前的信号逐字节不变 |
| G8 | 连续高量正确去重 | `test_hve_event.py`：构造 gap≤5 的连续高量，断言 1 个 cluster、1 个 primary |
| G9 | 未来数据修改不改过去信号 | 同 G7，独立用例（含跨年、跨财报期样本） |
| G10 | 参数全部集中配置 | 扫描 `hve_v1/*.py` 无魔法数字；§42 六个 CLI 开关全部生效 |
| G11 | 支持 T+3/5/10/20 回测 | `backtest.py` 输出四档 × 各 signal_type 的 N/胜率/均值/中位/PF/期望/MFE/MAE/最大回撤 |
| G12 | 可与 HVT/W7 交叉验证 | `cross_check.py` 输出 `overlap_count` / `overlap_ratio` + 四类 Case 落盘 |

§53 验收块（`HVE V1 IMPLEMENTATION` 文本）由 `report.py` 直接生成，不手写。

---

## 9. 待确认事项（需用户裁决后再进入编码）

1. **模块落位**：本计划将代码放在 `d:\mystock\solo\hve_v1\`，研究报告放在 `hve_v1/HVE_V1_RESEARCH.md`。
   是否同意？还是希望与既有研究层合并到 `research/hve/`（注意该目录现仅含 `hve_common.py` + 面板缓存，未作为生产模块使用）。
2. **回测区间**：受 `daily_cache` 起始 20210104 限制，可回测信号区间约 **2022Q1~2026-09**（4.6 年）。
   若必须覆盖 2018 起，则回测需走研究层 qfq 面板（`hve_grid.npz`），但该面板每日不更新，且与每日流程的数据口径不一致。
   建议：**回测用研究层面板（长历史、qfq），每日流程用 `stock_data.db`（保持一致性与可更新性）**，并在报告中显式标注两套口径。是否同意？
3. **是否落库**：HVE 信号是否写入 `stock_pick_db`（`strategy_id='hve_v1'`）参与既有跟踪/胜率回填？默认为「是」（成本低、便于复盘），可关。
4. **是否接调度**：是否生成 `hve_v1/run_hve_daily.bat` 并注册 Task Scheduler（不改 `run_all.bat`）？默认为「仅生成 bat，不注册任务」。
5. **§9 簇锚点**：确认按任务书用 `first`（本计划默认），而非研究层的 `max_amount`。

---

## 10. 结论

四项事实全部指向同一结论：HVE V1 **可以在完全不修改任何现有文件的前提下落地**，
且研究层已提供可复用的长历史面板与统计内核，先导证据（裸 HVE 事件 PF 0.9738）恰好印证任务书
「HVE 是事件、不是买点」的核心判断。

下一步：等待 §9 五项确认后，按 §3 文件清单开始编码（顺序：`hve_config.json` → `data.py` → `indicators.py`
→ `event.py` → `tests/test_hve_event.py` → `state.py`/`signals.py` → `backtest.py` → `cross_check.py` → `report.py`）。
