# H-MCE-01 — 预注册规范（PRE-REGISTRATION）

    Research ID : H-MCE-01
    Title       : A股多K线价格—成交量组合事件的增量信息检验
    Version     : 1.0
    Created     : 2026-10-02
    Parent      : H-TAIL-MIDBAR-01 (FAIL -> ARCHIVE, unchanged)
    Status      : FROZEN BEFORE ANY RESULT WAS OBSERVED

本文件是本实验**唯一的口径来源**。全部事件定义、阈值、窗口、基准、成本、
判据与输出清单在运行任何一行统计代码之前写定。运行后再修改本文件即视为作弊。

---

## §0 研究问题

> **A 股中，经过严格定义的多 K 线价格—成交量事件，是否能够在简单 Momentum、
> Volume 和 Market Regime 之外，提供稳定、可重复、OOS 有效的未来收益增量信息？**

本实验是**否定性优先**的科研检验：

1. 不预设任何形态有效。
2. 若全部失败，最终结论必须逐字为「**未发现稳定优势**」。
3. 禁止为了得到 PASS 而优化参数。
4. 所有特征只使用 T 日及此前信息，严禁未来数据泄漏。

---

## §1 研究边界与禁止事项

**只研究**：E01–E10 十个多 K 线价格—成交量事件的未来收益特征。

**禁止**：

```text
T+3 及以上的持有期                        -> 本实验允许 T+1/3/5/10/20（任务书 §7）
引入 HVT / W7 / HVE / 二次突破 / 主题 / 基本面 / ML / RSI / MACD / KDJ
参数优化（只允许预注册扰动，见 §10）
删除表现差的样本 / 只展示成功案例
只展示毛收益 / 忽略交易成本 / 忽略幸存者偏差
把「缩量」直接解释为「卖压衰减」或把 K 线形态直接解释为主力行为
修改现有交易系统的任何核心逻辑
输出「最强 / 最佳 / 推荐买入 / 稳赚 / 胜率神器」类结论
```

本实验是**独立科研模块**，位于 `research/h_mce/`，对目录外一切文件只读。

---

## §2 数据

### §2.1 数据源

只用现有 Tushare 本地缓存，复用统一数据读取模块：

| 用途 | 来源 |
|---|---|
| 行情面板（qfq OHLC / vol / amount / turnover / pct_chg / ST / 板块） | `research/hve/hve_common.py::build_grid`（**只读**依赖） |
| 可交易宇宙 | `research/hve/hve_common.py::eligibility`（**只读**依赖） |
| 总市值 | `research/fundamental_surprise_alpha/data/basic_panel.parquet` (`total_mv`, 万元) |
| 指数（regime） | `research/fundamental_surprise_alpha/data/index_panel.parquet`（含 `000300.SH`） |
| 尾部 / block bootstrap 原语 | `research/h_rbp/hrbp_common.py`（**只读**依赖） |

禁止 TDX / AkShare / Baostock / Yahoo / JoinQuant / Ricequant / 任何外部行情 API。

### §2.2 实际数据范围（运行前已核实，非结果驱动）

面板：`S=5816` 只 × `N=2120` 个交易日，`20180102 .. 20260924`。

对 2018 年逐日统计 eligible cell 数：**2018 全年 = 0**。原因：共享宇宙把
「上市满 250 交易日」锚定在**面板首个交易日 2018-01-02**，因此全部个股在
`idx-250` 之前都不可交易。**第一个存在 eligible cell 的交易日 = 2019-01-11**
（面板索引 250）。

因此：

```text
任务书请求    IS = 2018-01-01 .. 2023-12-31      OOS = 2024-01-01 .. 最新完整交易日
本实验实际    IS = 2019-01-11 .. 2023-12-31      OOS = 2024-01-02 .. 20260924
              （2018 年被自动剔除，零样本；**不得**为凑满 2018 而放宽宇宙）
```

`20180102 .. 20190110` 仅作为 MA20 / MA60 / rolling_high / 量比窗口的
**warm-up**（特征尚未成熟的日子不会有事件触发，因为掩码要求特征有限）。

### §2.3 宇宙（运行前冻结）

**U1（主口径，PRIMARY）**

```text
eligible = hve_common.eligibility(g)      # 已含 traded & ~ST & ~delist
                                          # & 上市满 250 会话 & 无 >=60 日连续停牌空洞
U1       = eligible AND (board != 'BSE')
```

剔除北交所（预注册选择，不是结果驱动）。

**U2（全样本版本，幸存者偏差对照）**

```text
U2 = traded & ~st & board != 'BSE'        # 去掉上市年限门槛与停牌空洞门槛
```

U2 不是用来替代 U1，而是用来回答「U1 的门槛是否驱动了结论」。

**幸存者偏差声明（已知事实，必须写入报告）**

```text
面板是否包含已退市股票 : 包含（面板自始至终保留其历史打印，之后为 NaN）
delist 点位标志        : 无   (delist_status = DELIST_PIT_UNAVAILABLE)
早停股票               : gone = 242 只（最后打印距面板末端 > 60 会话）
是否存在幸存者偏差     : 部分缓解，未彻底消除
```

`last_idx` / `gone` 早停股票**保留在面板中**，其前向窗口被截断时按 NaN 处理，
**不静默删除**。报告必须逐项列出上述四项。

### §2.4 价格口径（运行前冻结）

**全部基础几何量使用 qfq（前复权）OHLC 序列**，`prev_close := close[t-1]`：

```text
ret_1d = close[t] / close[t-1] - 1                  (qfq；非交易所 pct_chg)
```

理由：`gap_open` / `body` / `range` / `shadow` / `ma_distance` 必须与收益口径
自洽；qfq 剔除了除权除息造成的机械跳空，这正是收益型研究的正确口径。
`grid['pct_chg']`（原始 close/pre_close）与 `limup/limdn/oneword` 仅作
**诊断列**记录，不进入任何事件定义。

成交量口径：`vol` 为原始成交量（股），不除权；停牌日以 `traded` 掩码置 NaN
（**不是 0**）。

---

## §3 统一基础变量（任务书 §5，全部参数化）

```text
ret_1d[t]        = close[t] / close[t-1] - 1
body[t]          = abs(close[t] - open[t])
body_pct[t]      = abs(close[t] - open[t]) / close[t-1]
upper_shadow[t]  = high[t] - max(open[t], close[t])
lower_shadow[t]  = min(open[t], close[t]) - low[t]
gap_open[t]      = open[t] / close[t-1] - 1
range_pct[t]     = (high[t] - low[t]) / close[t-1]
close_position[t]= (close[t] - low[t]) / (high[t] - low[t])      # high==low -> NaN
ma20_distance[t] = close[t] / MA20[t] - 1
ma60_distance[t] = close[t] / MA60[t] - 1
```

**均线 / 均量约定（运行前冻结，必须逐字实现）**

```text
MA(w)[t]        = mean(close[t-w+1 .. t])        含当日；min_periods = w
MAvol(w)[t]     = mean(vol[t-w .. t-1])          不含当日（prev w 会话）；min_periods = w
volume_ratio_5  = vol[t] / MAvol(5)[t]
volume_ratio_20 = vol[t] / MAvol(20)[t]
amount_ratio_20 = amount[t] / mean(amount[t-20 .. t-1])
rolling_high_20 = max(high[t-20 .. t-1])         不含当日；min_periods = 20
```

> **量比分母不含当日**：这是本项目的既定口径。用户明确过「短线成交量远高于
> 此前时段」，若把当日纳入分母会系统性低估放量程度。任务书 §12 给出的
> `volume 1.5x / 2.0x / 2.5x` 扰动轴对主阈值为 2.0 的事件（E01/E02）逐字复现。

**全部数值阈值集中于 `mce_common.PREREG`；代码中不得散落硬编码。**

---

## §4 十个事件（任务书 §6，逐字冻结）

符号：`T` = 事件日（信号在 T 日收盘完全确定）；`E0x` = T 日掩码；
`E0x_CONF` = 确认掩码（**只作标签 / 确认事件，禁止进入 T 日信号**）。
所有掩码天然要求 `eligible ∧ 特征有限`。

### E01 巨量高开假阴线

```text
T:
  gap_open        >= GAP2            (2.0%)
  close           <  open
  close           >  close[t-1]
  volume_ratio_20 >= VR2             (2.0)
  close           >  MA20
记录: gap_open / volume_ratio_20 / body_pct / upper_shadow / close_position
      ret_5d / ret_10d / ret_20d     (T-5 / T-10 / T-20 涨幅)
```

### E02 巨量高开假阴 → 次日阳包阴

```text
E01 成立  且  T+1:
  close[t+1] > open[t+1]
  close[t+1] > high[t]
  open[t+1]  <= close[t]
E02 = E01 & 上式          （确认事件）
必须分别研究: E01 发生后的收益 / E02 确认后的收益
```

### E03 涨停/大阳 → 高开阴 → 再突破

```text
T-1:  ret_1d[t-1] >= RET7          (7%)      (不强制涨停，避免涨跌停制度偏差)
T  :  gap_open        >= GAP15     (1.5%)
      close           <  open
      volume_ratio_20 >= VR15      (1.5)
      close           >= close[t-1]
确认: 未来最多 5 个交易日 (T+1..T+5) 存在 close[j] > high[T]  -> E03_CONF
必须分别研究: T 日事件 / 突破确认日
```

### E04 大阳 → 缩量小阴 → 再突破

```text
T-1:  ret_1d[t-1] >= RET3          (3%)
T  :  close < open
      body_pct <= BODY15           (1.5%)
      vol[t] / vol[t-1] <= SHRINK  (0.7)
      volume_ratio_20 <= VRCAP     (1.0)
确认: 未来最多 5 个交易日 (T+1..T+5) 存在 close[j] > high[T]  -> E04_CONF
```

### E05 放量突破 → 缩量回踩 → 再突破

```text
T  :  close > rolling_high_20[t] 的 T-1 版本（即 rolling_high_20 只用到 T-1 以前数据）
      volume_ratio_20 >= VR15      (1.5)
回踩: T+1..T+5 内存在 j 使  volume_ratio_20[j] < VRCAP(1.0)
      且 low[j] >= breakout_level * (1 - PULL)      breakout_level = rolling_high_20[t], PULL=3%
确认: 该回踩成立后，未来最多 10 个交易日 (t_j+1 .. t_j+10) 存在 close > high[T] -> E05_CONF
必须分别研究: 突破后收益 / 回踩成功率 / 二次突破成功率
```

### E06 天量阴线 → 缩量 → 收复 50%

```text
T  :  close < open
      volume_ratio_20 >= VR25      (2.5)
midpoint = (high[T] + low[T]) / 2
确认: 未来最多 5 个交易日 (T+1..T+5) 存在 close > midpoint        -> E06_CONF
预注册子事件（阈值不得事后增加）:
  REC50  close > (high[t]+low[t])*0.50
  REC75  close > low[t] + (high[t]-low[t])*0.75
  RECHI  close > high[t]
统一研究 T+1 / T+3 / T+5 / T+10 收益
```

### E07 长下影 → 缩量 → 放量阳

```text
T  :  lower_shadow / (high-low) >= SR        (0.5)
      lower_shadow >= body * SHADOW_MULT     (1.5)
T+1:  vol[t+1] < vol[t]                      （缩量）
确认: T+1..T+3 内存在 阳线 (close>open) 且 volume_ratio_20 >= VR12 (1.2)  -> E07_CONF
诊断标签（未来信息，仅作标签，绝不作信号）:
  breached = min(low[t+1 .. t+10]) < low[t]     「T 日最低价是否被再次跌破」
必须研究: 长下影是否真正形成有效支撑（breached 两组的未来收益差异）
```

### E08 跳空 → 缩量回踩不补缺 → 再突破

```text
T  :  gap_open >= GAP2 (2.0%)
      缺口定义 low[t] > high[t-1]
保持: T+1..T+5 全部满足 low[j] >= high[t-1]     （缺口未被完全回补）
      且 mean(vol[t+1..t+5]) < vol[t]           （缩量）
确认: 保持成立后，未来最多 10 个交易日 (T+5+1..T+5+10) 存在 close > high[T] -> E08_CONF
对照组: 同 T 日跳空事件但 5 日内缺口被回补 -> E08_FILLED
必须研究: 缺口保持 vs 缺口回补 两组未来收益差异
```

### E09 大阳 → 2~4 日缩量 → 再放量阳

```text
T  :  ret_1d[t] >= RET3 (3%)
      volume_ratio_20 >= VR15 (1.5)
整理: 设 k = 最小整数 >= 2 使 T+1..T+k 全部满足
          vol[j] < vol[t] * SHRINK (0.7)  且  low[j] >= close[t] * (1 - PULL2)   PULL2 = 5%
      要求 k ∈ {2,3,4}
确认: T+k+1 .. T+k+3 内存在 阳线 且 volume_ratio_20 >= VR13 (1.3) 且 close > high[t]
      -> E09_CONF
```

### E10 天量 → 缩量横盘 → 二次突破

```text
T  :  volume_ratio_20 >= VR25 (2.5)
横盘: T+1..T+5 满足
        mean(volume_ratio_20[t+1..t+5]) <= VRCAP (1.0)
        区间最大回撤 <= DDM (5%)   ；回撤 = (max(close[t..t+5]) - min(close[t+1..t+5])) / max(close[t..t+5])
        全部 low[j] >= close[t] * (1 - PULL3)     PULL3 = 5%
确认: 未来最多 10 个交易日 (T+5+1..T+5+10) 存在 close > high[T] -> E10_CONF
注: 「筹码消化」只是研究假设，不得作为事实结论。
```

> **E10 / E06 的「天量」解释仅为假设**；报告禁止把缩量解释为卖压衰减、
> 禁止把形态解释为主力行为。

---

## §5 前向收益与锚点

```text
horizons H = {1, 3, 5, 10, 20}
forward_return(h, anchor) = close[anchor+h] / close[anchor] - 1        (qfq)
```

**锚点（运行前冻结）**

```text
anchor = T      对全部事件   （T 日事件收益）
anchor = C      仅对含确认阶段的事件（E02/E03/E04/E05/E06/E07/E08/E09/E10），
                C = 确认日（E0x_CONF 首次成立日）；E06 另报 REC50/75/HI 的首达日
```

**禁止**用 T 日盘中价作为成交价（`open[t] / low[t] / high[t] / vwap[t]` 一律不作为
入场价；第二执行模型只允许 `open[C+1]` 作为稳健性对照，不做正式结果）。

样本不足时（`close[anchor+h]` 为 NaN 或超出面板）该样本对该 horizon 记 NaN，
**不填充、不外推**。

---

## §6 交易成本（任务书 §8）

```text
0bp / 15bp / 30bp / 50bp       Primary = 30bp
Net(h, bp) = Gross(h) - bp/10000
```

必须同时输出 Gross 与 Net；**禁止只展示毛收益**。成本线性扣减，不因结果调整。

---

## §7 三个基准（任务书 §9，「最重要部分之一」）

对每个事件、每个 horizon 计算：

**Benchmark A —— 全 A 随机交易日**

```text
A(h) = mean forward_return(h) over ALL eligible cells (U1) in the same phase
       （即全体合格股票日的无条件均值）
```

**Benchmark B —— 相同前置条件、但无该 K 线组合**

```text
对每个事件预注册「前置条件」pre(mask)，仅含事件定义的**前置**部分：
  E01/E02:  E01 的 gap_open>=GAP2 且 close>close[t-1]（去掉巨量、假阴、MA20 部分）
  E03:      ret_1d[t-1] >= RET7
  E04:      ret_1d[t-1] >= RET3
  E05:      close > rolling_high_20[t]                    （去掉放量部分）
  E06:      close < open                                  （去掉天量部分）
  E07:      lower_shadow/range >= SR                      （去掉 body 倍数部分）
  E08:      low[t] > high[t-1]                            （跳空，去掉 gap 大小部分）
  E09:      ret_1d[t] >= RET3                             （去掉放量部分）
  E10:      true（无前置）  -> B 退化为 A，报告中显式标注
B(h) = mean over { pre ∧ ~E0x ∧ U1 ∧ 同 phase }
```

**Benchmark C —— 同股票、相近时间匹配样本**

```text
C(h) = mean over { 同一 stock ∧ |Δt| <= 20 会话 ∧ U1 ∧ ~E0x ∧ 同 phase }
```

**配对差（cluster bootstrap，按日历月聚类）**

```text
delta_B(h) = E0x(h) - B(h)      delta_C(h) = E0x(h) - C(h)
```

**必须报告三个基准；只和全 A 比较（Benchmark A）不足以证明形态有信息量。**

---

## §8 未来函数自动检查（`future_column_scan`）

运行开始时执行，**任一失败即 FATAL，停止实验（EXIT 3）**：

```text
check 1  truncation invariance
  把面板尾部截断 K=120 会话，重算全部掩码与特征，在保留区逐位比对；
  任何不一致 -> FATAL
check 2  backward window proof
  对每个「向前看」窗口（rolling_high_20、MAvol、prev_close、First 类）用
  **前向**版本重算，事件数必须与后向版本不同（证明用的是后向）
check 3  forward-window isolation
  逐个事件的确认阶段（E0x_CONF）必须只在 anchor = C 的收益里使用；
  构造 E0x(T 日信号) 时不得引用任何 t > T 的列（静态列清单审计）
check 4  label columns marked
  所有使用 t > T 信息的列（breached / REC50/75/HI / E0x_CONF / momentum-of-future）
  必须登记在 LABEL_COLUMNS 白名单，且只用于锚点 C 或诊断，不用于 T 日信号
```

---

## §9 IS / OOS（任务书 §11）

```text
IS  = 2019-01-11 .. 2023-12-31      （实际范围，见 §2.2）
OOS = 2024-01-02 .. 20260924        （最新完整交易日）
```

**禁止**：

```text
先看 OOS 结果再修改规则
用 OOS 数据参与任何阈值选择 / 形态定义
随机 Train/Test 或打乱时间
```

规则修改只能走：`V1 冻结 → 输出结果 → 提新 Hypothesis → V2`。

---

## §10 参数稳定性（任务书 §12，预注册扰动）

**只做预注册扰动，不做无限优化。** 扰动轴（运行前冻结）：

```text
gap_open 轴   : 主阈值 ± 0.5pp                        -> 与任务书 {1.5,2,2.5}% 一致
volume 轴     : 主阈值 ± 0.5（绝对）                  -> 与任务书 {1.5,2.0,2.5} 一致
缩量轴 shrink : {0.6, 0.7, 0.8}
回踩容差 pull : {3%, 5%, 7%}
```

每事件的预注册格点（其余阈值保持主值）：

| Event | axis-1 | axis-2 | axis-3 | cells |
|---|---|---|---|---|
| E01 | gap {1.5,2.0,2.5}% | vr {1.5,2.0,2.5} | — | 9 |
| E02 | gap {1.5,2.0,2.5}% | vr {1.5,2.0,2.5} | — | 9 |
| E03 | gap {1.0,1.5,2.0}% | vr {1.0,1.5,2.0} | ret_prev {6,7,8}% | 27 |
| E04 | shrink {0.6,0.7,0.8} | vr_cap {0.8,1.0,1.2} | ret_prev {2.5,3.0,3.5}% | 27 |
| E05 | vr {1.0,1.5,2.0} | pull {3,5,7}% | — | 9 |
| E06 | vr {2.0,2.5,3.0} | recover {50,60,75}% | — | 9 |
| E07 | shadow_ratio {0.4,0.5,0.6} | vr {0.9,1.2,1.5} | — | 9 |
| E08 | gap {1.5,2.0,2.5}% | hold_days {3,5,7} | — | 9 |
| E09 | shrink {0.6,0.7,0.8} | vr {0.8,1.3,1.8} | — | 9 |
| E10 | vr {2.0,2.5,3.0} | dd_max {3,5,7}% | — | 9 |

判定：

```text
positive_ratio = (格点 Net30(T+5) > 0) 的比例
parameter_stable = positive_ratio >= 0.60
parameter_fragile = positive_ratio <= 0.40
'2.0x 有效 / 1.9x 无效 / 2.1x 无效' -> FRAGILE，而不是 PASS
其余落 FRAGILE 与 stable 之间，按 §18 处置
```

---

## §11 市场状态（任务书 §13）

指数：`000300.SH`（CSI300）。**规则按任务书 §13 逐字实现**：

```text
BULL    : Index > MA20 > MA60
BEAR    : Index < MA20 < MA60
NEUTRAL : 其他
```

MA20 / MA60 为指数收盘价的 20 / 60 日简单均线（含当日）。

对每个事件分别输出 BULL / NEUTRAL / BEAR 的样本数、T+5 收益、T+5 胜率、T+10 收益。
**不得因为某一个状态表现好就宣布整体有效。**

---

## §12 分股票维度与尾部集中度（任务书 §14）

```text
市值分层: 每个交易日对 U1 按 total_mv 三分位 -> 大盘 / 中盘 / 小盘（当日横截面）
贡献度  : Top 1% / Top 5% / Top 10% 的（正）收益贡献占比
          贡献 = 该分位正收益之和 / 全样本正收益之和
```

若收益主要来自极少数股票 -> 标记 `CONCENTRATED`，并按 §18 处置。

---

## §13 Null Model（任务书 §15）

三个随机对照，每个事件分别执行：

```text
Null 1  随机股票×日期：在全样本 eligible cell 中，随机抽与事件同数量的 (stock, date)
Null 2  **保持 stock × 年份 × 市场状态 × 前置涨幅桶**，打乱 K 线组合标签
        （在匹配池内随机抽「非事件」cell 作为替代）
Null 3  随机打乱事件日：对每个事件保持 stock 不变，在同一 stock 的 eligible 日期里随机重排
```

统一以 `rounds=8` 重抽、`resolution >= 0.99` 才采纳；`B = 2000`。
报告 `obs / null mean / null 2.5% / null 97.5% / excess / 单侧 p / resolution`。

目的：判断结果是否只是「市场整体上涨 / 动量 / 小盘股效应 / 时间效应」，
而不是 K 线组合本身的信息。

---

## §14 统计检验（任务书 §16）

```text
Mean / Median / Win Rate / t-stat / Bootstrap 95% CI
Bootstrap B = 5000（>= 5000），**按月聚类（cluster bootstrap）**
  同一股票连续信号、同一市场环境下的信号高度相关，不得把每日信号当独立样本
```

另报 `n_eff`（重叠调整后的有效样本数）作为参考，不用于判决。

---

## §15 多重检验（任务书 §17）

一次测试 `10 事件 × 5 horizon = 50` 个主检验（子条件不计入主族）。必须输出：

```text
raw p-value          （单侧，H0: mean Net30 <= 0；cluster bootstrap 经验 p）
FDR adjusted p-value （Benjamini-Hochberg，m = 50）
```

**禁止看到某个 p < 0.05 就直接宣布有效。** `fdr_sig = (BH q < 0.10)`。

---

## §16 Momentum 基准比较（任务书 §20）

```text
MOM5[t]  = close[t] / close[t-5]  - 1
MOM10[t] = close[t] / close[t-10] - 1
MOM20[t] = close[t] / close[t-20] - 1
```

对每个事件、每个 horizon：把事件样本的 forward return 与**同日横截面上**
`MOM5/MOM10/MOM20` 各自 Top 十分位（Top decile）组合的 forward return 比较：

```text
delta_MOMk(h) = events(h) - top-decile-MOMk same-day forward return(h)
```

若某事件的优势在控制动量后消失，结论必须写成
「该形态可能主要是动量暴露，而不是独立的 K 线信息」。

---

## §17 增量信息实验（任务书 §21）

在同一 eligible 抽样池（每 horizon 固定种子抽 `n_sub = 400,000` 个 eligible cell）上，
对 `h ∈ {5, 10}` 分别拟合：

```text
Model A : fwd ~ MOM20
Model B : fwd ~ MOM20 + volume_ratio_20
Model C : fwd ~ MOM20 + volume_ratio_20 + event_dummy (10 个事件各自 0/1)
Model D : fwd ~ MOM20 + volume_ratio_20 + event_dummy + regime_dummies
```

输出每层的 `R²`、`delta R² (A->B, B->C, C->D)`，以及 Model C/D 中
`event_dummy` 的系数与按日历月聚类的稳健 t 值。

**重点看 T+5 / T+10 的增量，而不是只看拟合优度。**

---

## §18 评价框架与判决树（任务书 §18 / §24，运行前冻结）

每个事件只输出 `ROBUST / PROMISING / FRAGILE / NO EDGE`（**禁止单一综合评分**）。
主 horizon `H* = T+5`，并要求 T+10 方向一致才计入「一致」。

判据位（布尔）：

```text
b1 is_pos           IS(2019-2023)   Net30(H*) > 0
b2 oos_pos          OOS(2024-最新)  Net30(H*) > 0
b3 full_pos         FULL           Net30(H*) > 0
b4 boot_ci_pos      月度聚类 95% CI 下界 > 0
b5 fdr_sig          BH q < 0.10
b6 years_consist    IS 年内 Net30>0 的年份占比 >= 60%
b7 param_stable     §10 positive_ratio >= 0.60
b8 regime_ok        BULL/NEUTRAL/BEAR 中 >= 2 个 Net30(H*) > 0
b9 null_ok          Null 2（打乱标签）obs > null 97.5%
b10 not_tail        leave Top 5% 后 Net30(H*) > 0
b11 beats_bench     Net30(H*) > Benchmark B 与 C 的 Net30(H*)
b12 momentum_incr   控制 MOM20 后 event_dummy 系数 > 0（Model C, h=H*）
```

判决树（**自上而下，先命中先判**）：

```text
if  b1..b12 全真                                  -> ROBUST
elif b1, b2, b3, b4 为真 and (为真项数 >= 9)      -> PROMISING
elif b3 为真 and (b2 为假 or b7 为假 or b10 为假) -> FRAGILE
elif b3 为真                                       -> PROMISING
else                                               -> NO EDGE
```

整体结论（任务书 §26）：

```text
若 ROBUST 事件数 = 0 且 PROMISING 事件数 = 0
    -> 「未发现稳定优势」 (STOP；不继续优化这 10 个形态)
否则
    -> 为每个通过事件建立 Hypothesis ID: H-MCE-01, H-MCE-02, ...
       进入独立第二阶段（才允许研究 Entry / Exit / T+3 / T+5 / Position sizing / Execution）
```

`trading_authorization = NO`（无论结论如何）。**本阶段禁止直接转化为实盘 BUY 信号。**

---

## §19 必答问题 Q1–Q7（任务书 §19 / §22）

```text
Q1  10 个多K线组合中，哪些在 IS 有效？
Q2  哪些在 OOS 仍然有效？
Q3  哪些扣除 30bp 成本后仍然有效？
Q4  哪些只是传统技术分析看起来漂亮，但实际上没有增量信息？
Q5  哪些形态其实只是 Momentum 的另一种表达？
Q6  哪些形态在加入 MA20 / MA60 / Volume / Market Regime 以后才有效？
Q7  哪些形态真正提供了相对于简单动量策略的增量信息？
```

---

## §20 必须生成的产物（任务书 §22，全部位于本目录）

```text
multi_candle_event_definitions.json
multi_candle_event_samples.csv
multi_candle_event_results.csv
multi_candle_event_oos.csv
multi_candle_event_yearly.csv
multi_candle_event_regime.csv
multi_candle_event_parameter_stability.csv
multi_candle_event_null_model.csv
multi_candle_event_momentum_compare.csv
multi_candle_event_report.md
（附加，任务书 §22 允许「必须生成」之外的补充）
H_MCE_01_SUMMARY.json      H_MCE_01_RESULTS.json
H_MCE_01_BIAS_AUDIT.csv    H_MCE_01_BENCHMARK.csv
H_MCE_01_INCREMENTAL.csv   H_MCE_01_SIZE.csv     H_MCE_01_SURVIVOR.csv
```

脚本：`mce_common.py`（公共层）、`mce_run.py`（主运行）、`has_report.py`（报告 + 交付校验）。
运行顺序：`python mce_run.py` -> `python has_report.py`（后者必须 `EXIT 0`）。

---

## §21 不变量（运行期不得违反）

```text
1. 本目录之外只读：不修改 research/hve、research/h_rbp、research/h_tail_midbar、
   cache_daily、report_daily 及任何生产脚本。
2. 信号只用 T 及此前信息；确认阶段与 breached / REC 类列只作标签或锚点 C。
3. 不用 T 日盘中价作为成交价。
4. 不在看到结果后修改阈值、分层、窗口或判据。
5. 不引入 §1 禁止清单中的任何因子。
6. future_column_scan 任一失败 -> FATAL -> EXIT 3。
```

---

## §22 变更日志（CHANGELOG）

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-10-02 | 初版冻结。IS 起点按 §2.2 自动检测修正为 2019-01-11（2018 零样本）；量比 / 均量分母统一为「不含当日」；价格口径统一为 qfq；§18 判决树与 §10 扰动格点预注册。 |
