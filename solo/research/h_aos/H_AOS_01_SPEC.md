# H-AOS-01 — 预注册规范（PRE-REGISTRATION）

    Research ID : H-AOS-01
    Version     : 1.0
    Created     : 2026-10-02
    Parent      : H-ALPHA-SOURCE-01 / H-TAIL-MIDBAR-01 / H-RBP-01 / BREAKOUT-ALPHA-RESEARCH
    Status      : FROZEN BEFORE ANY RESULT WAS OBSERVED

本文件是本次实验**唯一的口径来源**。所有阈值、分层、判据与输出清单在运行任何
一行统计代码之前写定。运行后再修改本文件即视为作弊。

---

## §0 研究假设与最终问题

### §0.1 核心假设（H-AOS-01）

> A股不存在长期稳定、跨市场状态普遍有效的单一 Alpha；但如果先识别市场 Regime，
> 再根据各 Alpha 在当前 Regime 下的历史 OOS 健康度进行动态路由，并允许系统主动
> NO TRADE，则组合级风险收益表现可能显著优于任一固态 Alpha。

**不得预设该假设成立。** 本研究的目标不是证明 Alpha OS 有效，而是严格判断
「固态策略 → 动态策略路由」是否产生真实、稳定、可重复、扣除交易成本后仍存在的增益。
若实验不能证明，必须明确输出 `FAIL / REJECT`。

### §0.2 最终只回答一个问题

> 在 A 股非平稳环境中，动态选择"当前仍然有效的 Alpha"，并在没有足够优势时主动
> NO TRADE，是否能够在严格 OOS、交易成本、参数扰动、Null Model、Walk-Forward 和
> 反事实检验下，相对于固态量化策略获得稳定的增量风险收益？

### §0.3 研究优先级（不得跳级）

    P1 验证 Alpha 是否存在 Regime Dependence
     ↓
    P2 验证 Regime 是否可以 OOS 识别
     ↓
    P3 验证 Alpha Health 是否可以识别失效
     ↓
    P4 验证 Dynamic Router 是否优于固定策略
     ↓
    P5 验证 NO TRADE 是否增加收益 / 降低风险
     ↓
    P6 Walk-Forward + 成本 + 参数 + Null + 反事实
     ↓
    P7 只有全部通过后，才允许研究 ML Router

**本轮交付范围：P1 + P2（用户裁定"先 P1/P2 再汇报"）。**

---

## §1 研究边界【最高优先级】

### §1.1 第一阶段禁止事项

**禁止**使用：Transformer / LSTM / Deep Learning / 复杂 Reinforcement Learning / 任何
以「预测个股涨跌」为目标的监督学习。

第一阶段**只允许**：Rolling statistics / Regime rules / Simple score / Walk-forward。

### §1.2 禁止修改 Alpha

六个 Alpha 的定义在 §3 中逐字冻结。**不得**在研究过程中修改任何 Alpha 的参数、
入场规则、过滤条件或持有期。若某 Alpha 已有成熟结论（如 FAIL），**沿用不改**——
「某 Alpha 在多数 Regime 下无效」本身就是本研究要检验的对象。

### §1.3 禁止未来数据泄漏（§2.1）

任何时点 t 的决策只能使用 $Information_{\leq t}$。禁止：

- t+1 数据 / 未来收益 / 未来 Regime
- 全样本统计量（含全样本分位数）
- 未来股票池 / 后见之明筛选 / 未来策略表现
- 用完整样本计算阈值后回测历史

所有 rolling / expanding 参数必须严格按时间计算。

### §1.4 禁止事后挑选最优策略

不得在看到完整测试集结果后决定：哪个 Alpha 好、哪个 Regime 好、阈值是多少、
哪个策略应该关闭。这些规则必须在训练/验证阶段确定，测试集只用于最终评价。

### §1.5 禁止为提高结果反复调参

任何参数调整必须：① 提出明确 Hypothesis → ② 记录修改原因 → ③ 在训练/验证集完成
→ ④ 锁定参数 → ⑤ 再进入 OOS。禁止「OOS 不好 → 继续调参 → 直到 OOS 变好」。

**研究不是参数优化比赛。**

---

## §2 数据（Data）

### §2.1 数据来源

**只允许**使用现有 Tushare cache。禁止 TDX / AkShare / Baostock / Yahoo / JoinQuant /
Ricequant / 外部行情 API。**不重新建立数据体系。**

| 用途 | 来源 | 覆盖 |
|---|---|---|
| 个股日线 / 复权 / 指标 | `research/hve/hve_common.py::build_grid`（只读依赖，不修改） | 2018-01-02 ~ 2026-09-24，S=5816 × N=2120 |
| 个股基本面/换手 | `cache_daily/stock_data.db::daily_basic_cache` | 4,862,749 行 |
| 指数（Regime 用） | `cache_daily/stock_data.db::index_daily_cache`（000300.SH） | 11,365 行 |
| 个股日线（校验用） | `cache_daily/stock_data.db::daily_cache` | 7,119,610 行 |
| ETF 日线 | `cache_daily/etf_backtest_hist/{ts_code}.csv` | 2024-08-26 ~ 2026-09-22 |
| 主题热度 | `report_daily/theme_heat_series.csv` | 2026-07-08 ~ 2026-09-30 |
| 主题→个股映射 | `cache_daily/theme_stock_map_v2_{date}.json` / `theme_stock_map_{date}.json` | 最早 2026-06-22 |

### §2.2 数据可用性核实结论（运行前已核实，不可推翻）

本项目对六个 Alpha 的历史可重建窗口做了逐项核实，结论如下（详见 `_aos_probe.py`）：

| Alpha | 源码冻结研究窗口 | 受限原因 | 能否覆盖 2018 |
|---|---|---|---|
| A HVT | 2019-01-01 ~ 2026-09-24 | `study_start=20190101`（2018 仅 burn-in） | 否 |
| B Tail | 2022-01-01 ~ 2026-09-24 | `study_start=20220101`（+40 会话 burn-in） | 否 |
| C Breakout | 2021-01-04 ~ 2026-09-24 | 数据源 `research/out/panel.parquet` 自 2021 起 | 否 |
| D DIP/RBP | 2019-01-11 ~ 2026-09-24 | `study_start=20190101`（2018 仅 burn-in） | 否 |
| E ETF Momentum | 2025-01-02 ~ 2026-09-22 | `etf_backtest_hist` 起始 + `fund_daily` 拉取区间 | 否 |
| F Theme Alpha | 2026-07-08 ~ 2026-09-30 | `theme_heat_series.csv` 仅 60 交易日；`theme_scores_v2` 最早 20260724；`v15_theme_alpha.csv` 无日期列且为单快照覆盖 | 否 |

**六项 Alpha 无共同长窗口。** 据此冻结分层口径（§2.3）。

### §2.3 分层口径【冻结】

    CORE  C4 = {A HVT, B Tail, C Breakout, D DIP}
          共同可用窗口 = 2022-01-01 ~ 2026-09-24（约 1,150 交易日 / 4.7 年）
          → 跑完整 P1–P6（含 Walk-Forward、成本、参数扰动、Null、反事实、Top5%）

    EXT   X2 = {E ETF Momentum, F Theme Alpha}
          各自窗口 = E: 2025-01-02~2026-09-22（1.72 年）/ F: 2026-07-08~2026-09-30（60 交易日）
          → **只出 Alpha×Regime 矩阵与相对强弱证据**，
             标记 DATA_INSUFFICIENT_FOR_OOS，**不进入核心 Router、不进入主结论**

理由：Theme 仅 60 交易日、ETF 仅 1.7 年，若纳入核心则 §10 最小样本与 §14
Walk-Forward 均无法满足，等于预先宣判 FAIL。分层是**避免用数据不足伪装成研究结论**，
而不是回避任务书 §4 的 Alpha 名单——六项全部按 §3 冻结定义实现并报告。

### §2.4 宇宙（Universe，运行前冻结）

    eligible = hve_common.eligibility(g)     # traded & ~ST & ~delist
                                             # & 上市满 250 交易日
                                             # & 无 >=60 日连续停牌空洞
               AND (board != 'BSE')

三个股票池层级（§17 鲁棒性）：

| 层级 | 定义 |
|---|---|
| Universe A | 全 A 股可交易股票（`traded & ~ST & ~delist`，不设上市期与流动性门槛） |
| Universe B（主口径） | `eligible`（上表） |
| Universe C | Universe B + 当日 `amount >= 5000 万元`（高流动性） |

**主口径 = Universe B。** 所有主表、主结论基于 Universe B。

### §2.5 交易成本

- 成本档：`{0, 15, 30, 50} bp`（**round-trip，单边计费×2**）
- **主基准 = 30 bp**
- 成本直接从每笔交易的收益中扣减：`net = gross - cost_bp/10000`
- 同时报告换手率、交易次数、空仓比例
- 单 Alpha 原生成本口径差异（如 Breakout 原用 `2*slip+FEE`、ETF 原用 5bp/边）
  **不沿用**，统一为上述档位；差异记录在报告的 Alpha Definition 节

---

## §3 Alpha 定义（FROZEN — 不得修改）

所有 Alpha 均在共享面板上**重建事件掩码**（因原研究的逐事件明细多数未落盘），
重建时**逐字沿用原研究的 PREREG 参数**，只改「样本范围」以对齐 CORE 窗口。

### Alpha A：HVT（L6_REEXPANSION / HVT-BULL）

来源：`research/hve/hve_hvt.py` + `hve_common.py`（PREREG）。

    t0          = HVE 母事件日
                  VR20 = vol[t] / mean(vol[t-19..t])   # 分母含 t
                  gate: pct_chg>=3% & CLV>=0.70 & body>=0.30 & close>MA20
                  去重: gap=5, 代表=max(amount)
    L6 条件      close>MA20
                 & ma20_slope5 > 0                        # (MA20[t]-MA20[t-5])/5
                 & close > HVE_close
                 & close > HVE_high
                 & running_low(close/HVE_close) >= 0.90
                 & R_REEXPANSION: close>MA20 & vol/MA20(vol)>=1.0
                   & close > max(High[t-10..t-1]) & 前一日不处于 bull_hold
    买入日       [t0+1, t0+20] 内首个同时满足 L6 全部条件的交易日；买入价 = 该日 close
    持有期       h = 10（close[buy+h]/close[buy]-1）；诊断用 h ∈ {3,5,10,20}
    不确认       该事件从样本中丢弃（不占仓、不持币）

### Alpha B：尾盘惯性（Tail）

来源：`research/h_tail_midbar/tmb_run.py` + `tmb_common.py`（PREREG，PRIMARY ARM）。

    E0(T)       pct_chg[T] >= 3%  &  close>open  &  body>=0.03  &  CLV>=0.65
    First(10)   E0 在 [T-10, T-1] 内无同类（纯后视，不含当日）
    MApresent   M1|M2|M3|M4 任一：
                  M1 = cl>ma5 & ma5>ma20
                  M2 = cl>ma5 & ma5>=ma20 & s5>0
                  M3 = cl>ma5 & cl>ma20 & ma5>ma20 & s20>=0
                  M4 = cl>ma5 & cl>ma20 & ma5>prev5 & s20<=0
    V5          1.20 <= VR20 <= 3.00，  VR20 = vol[T]/mean(vol[T-20..T-1])   # 分母不含 T
    买入/卖出   T 收盘买入 → T+1 收盘卖出（h=1）
    日线代理声明 本研究验证的是「收盘中阳线 → 次日收益」，不是严格意义的「尾盘惯性」

### Alpha C：Breakout（突破）

来源：`research/_brk_build.py` / `_brk_alpha.py` / `_brk_deep.py`。

    压力位      R_N = max(High[t-N .. t-1])，rolling(N).max().shift(1)
    事件定义    B6_SimpleBreakoutFlag = Distance_to_R20 > 0，  Distance_to_R20 = close/R_20 - 1
                （即收盘价首次站上过去 20 日最高价）
    池条件      与原模型一致： 交易日序号>=60 & close/vol/amount 有限
                & ~ST & ~delist & board!='BSE' & amount >= 5.0e4（千元，即 5000 万元）
    买入/卖出   信号日 T → T+1 开盘买入 → T+1+3 收盘卖出（h=3）
                fwd = close[T+1+h]/open[T+1] - 1
    口径说明    原突破模型产出的是横截面「近压力位候选池」（Distance_to_R 最低 10% 分位，
                POOL_N=(10,20,40,60) 并集），并非布尔事件。本 SPEC 将其**第一性条件**
                B6_SimpleBreakoutFlag 作为可交易事件掩码；R_N 的压力位定义、池条件、
                买卖价口径、成本前口径**全部沿用原模型参数，未改动任何一个参数**。
                此为**口径裁定**，已登记于 §19。

### Alpha D：DIP / Rebound（回踩低吸）

来源：`research/h_rbp/`（PREREG_H，H-RBP-01，已判定 FAIL→ARCHIVE，沿用不改）。

    t0 锚点     VR20_excl >= 2.00 & r1 >= 0.02 & close>open & CLV >= 0.65
                VR20_excl = vol[t]/mean(vol[t-20..t-1])   # 分母不含 t（与原 HVE 的 VR20 不同）
                去重: gap=5, 代表=max(amount)
    观察窗      [t0+1, t0+60]，lookback=6
    回踩        drawdown(t) = close[t]/max(High[t0..t]) - 1；主 cell band = R1（3%,5%）
    结构        S3 = (close>=MA20) & (low >= event_low(t0))，struct_at=RETRACE
    转强        RS2_5D = close[t] > max(High[t-5..t-1])，volconf=False
    信号日      首个触碰带上、且结构成立、且转强成立的交易日
    买入/卖出   信号日+1 开盘买入（entry_price = open[entry_idx]）→ 买入日+10 收盘卖出
                fwd = close[entry_idx+10]/open[entry_idx] - 1
    无止损      exit_rule 原样沿用（no stop）

### Alpha E：ETF Momentum（扩展层）

来源：`etf_mainline_strategy_tushare.py`（方案 A 口径）。

    MOM_PERIOD = 20     REBAL_DAYS = 30     TOP_N = 1
    DYNAMIC_EXIT_TOP_PCT = 0.30              MIN_HOLD_DAYS = 5
    score        momentum_score = 20 日涨幅的截面百分位（0~100）
                 total = 0.80×momentum_score + 0.20×ige_score（IGE_ENABLED=True，权重 0.20）
                 无 IGE 数据时退化为纯动量
    调仓         ① 距上次调仓 >= 30 交易日；或 ② 持仓跌出 Top30% / 跌破 MA30 / IGE<40
                 （②需持仓满 5 日）
    资产池       35 只 ETF（ETF_POOL 静态 dict，含选择偏差，登记于 §19）

### Alpha F：Theme Alpha（扩展层）

来源：`theme_heat_v22.py` + `theme_alpha_engine.py`（不重新设计主题评分）。

    Heat        HeatRaw = 0.60×price_s + 0.30×bread_s + 0.10×act_s
                Reliability = min(1, sqrt(N/50))
                Heat = 0.50×HeatRaw + 0.50×(HeatRaw×Rel + MarketMedianHeat×(1-Rel))
    三口径      今日最强=TODAY(1) / 本周最强=WEEK(5) / 本月最强=MONTH(20)，按各窗口 Heat 排名
    个股层      ThemeAlpha     = 0.40×ThemeStrength + 0.25×ThemeStage + 0.20×StyleScore + 0.15×MoneyScore
                ThemeRankScore = 0.70×FinalScore + 0.30×ThemeAlpha
                TradeRankScore = 0.70×ThemeRankScore + 0.30×TradeScore
                TradeScore     = 0.40×MoneyAttack + 0.35×LeaderUniqueness + 0.25×BuyPointQuality
    成员映射    cache_daily/theme_stock_map_{date}.json（最早 20260622）

### §3.1 前向收益口径对照（不得混淆）

| Alpha | 事件/信号日 | 买入日 | 买入价 | 卖出 | 收益公式 | primary h |
|---|---|---|---|---|---|---|
| A HVT | t0(HVE) | 确认日 ∈ [t0+1,t0+20] | close | 买入日+h | `close[buy+h]/close[buy]-1` | 10 |
| B Tail | T | T | close | T+1 | `close[T+1]/close[T]-1` | 1 |
| C Breakout | T | T+1 | **open** | T+1+h | `close[T+1+h]/open[T+1]-1` | 3 |
| D DIP | t0→信号日 | 信号日+1 | **open** | 买入日+h | `close[buy+h]/open[buy]-1` | 10 |
| E ETF | 调仓日 | 调仓日 | close | 下次调仓 | 持有期累计 | — |
| F Theme | 快照日 | 次日 | close | +10 | 见 §3 扩展层说明 | 10 |

### §3.2 已知口径差异（必须显式登记，不得静默混合）

1. **VR20 分母不一致**：A 用分母含 t；B/C 用分母不含 t；D 用 `VR20_excl`（不含 t）。
   重建时**各自沿用自身定义**，不得统一。
2. **BSE 处理**：B/D/C 显式剔除；A 的代码路径未强制剔除。本 SPEC 统一按 §2.4
   Universe 剔除 BSE（对 A 属**收紧**，登记于 §19）。
3. **买卖价**：A/B 用收盘价进出；C/D 用次日开盘价进出。
4. **Alpha C 原模型非布尔事件**（见 §3 Alpha C 口径说明）。

---

## §4 Regime 定义（5 态 R1–R5）

### §4.1 特征（全部 PIT，只用 ≤t 信息）

标的：**沪深300（000300.SH）**，来自 `index_daily_cache`。

    ma20, ma60                 close 的 20/60 日均线（含当日）
    r20, r60                   close[t]/close[t-20]-1 ; close[t]/close[t-60]-1
    vol20                      std(daily_ret[t-19..t]) × sqrt(252)
    breadth                    breadth[t] = 当日上涨家数 / 当日有效家数（共享面板，≤t）
    ln_dn                      ln_dn[t] = 当日跌停家数 / 当日有效家数
    liq                        amount_HS300[t] / mean(amount_HS300[t-19..t])（仅记录，不入规则）

### §4.2 阈值（**只在 IS 训练集算一次，全域锁定**）

IS 训练集 = **2022-01-01 ~ 2023-12-31**。在该区间内计算：

    q_a = quantile(r20,     0.60)      # 强势分界
    q_b = quantile(r20,     0.25)      # 弱势分界
    q_d = quantile(r20,     0.05)      # 极端下杀分界
    q_v = quantile(vol20,   0.80)      # 高波动分界
    q_w = quantile(ln_dn,   0.95)      # 跌停潮分界
    q_f = quantile(breadth, 0.50)      # 广度中位

**严禁使用全样本分位数。** 阈值一经算出即写入 `aos_regime_series.csv` 头部与报告，
Walk-Forward 各折**不改动**这组阈值（WF 的一致性通过 §5.3 的滚动再校准变体验证）。

### §4.3 分类级联（首个命中即定，顺序固定）

    1) R5 极端风险   : (vol20 >= q_v AND r20 <= 0) OR (ln_dn >= q_w) OR (r20 <= q_d)
    2) R1 强趋势     : close>ma20 AND ma20>ma60 AND r20 >= q_a AND breadth >= q_f
    3) R4 结构性退潮 : close<ma20 AND close<ma60 AND r20 < 0 AND breadth < q_f
    4) R2 震荡偏强   : close>ma20 AND close>ma60 AND r20 >= 0
    5) R3 普通震荡   : 其余全部

若某状态在样本中占比 < 2%，报告必须显式标注（不得静默合并）。

---

## §5 分期与 Walk-Forward

### §5.1 主分期（固定，最终结论主要依据 OOS）

| 阶段 | 区间 | 允许用途 |
|---|---|---|
| IS（训练/校准） | 2022-01-01 ~ 2023-12-31 | 确定 Regime 阈值、Health 权重、状态阈值、Regime→Alpha 映射 |
| Validation | 2024-01-01 ~ 2024-12-31 | 只做**确认**，**不得**回头改参数 |
| **OOS** | **2025-01-01 ~ 2026-09-24** | **只用于最终评价** |

三阶段在报告中**必须分开呈现**，禁止混合。禁止把 IS 结果描述成 OOS。

### §5.2 Walk-Forward（滚动）

    Train 24m → Validation 6m → OOS 6m， Step = 3m，rolling（expanding 变体见 §11）

第一个折：Train 2022-01~2023-12 / Val 2024-01~2024-06 / OOS 2024-07~2024-12。
逐折前移 3 个月，末折 OOS 落在 2026-04~2026-09。

**每折内部重新执行全部校准**（Regime 阈值仅用该折 Train 区间重算，用于 WF 变体），
输出逐折 OOS 指标到 `aos_walkforward.csv`。

### §5.3 WF 的两个变体（都必须报告）

- **WF-frozen**：所有折沿用 §4.2 的固定阈值 → 检验「一次校准是否够用」
- **WF-rolling**：每折用自身 Train 重算阈值 → 检验「滚动再校准是否必要」

---

## §6 Alpha Health

### §6.1 输入（只用已完全平仓的交易，realized ≤ t）

对 Alpha i 在日 t 的评估：

    N_trail      trailing 250 交易日（或自该 Alpha 样本起点）内已完结交易数
    PF_trail     30bp 后  ∑正净收益 / |∑负净收益|
    WR_trail     胜率（30bp 后）
    RET_trail    平均净收益（30bp 后）
    DD_trail     该窗口净值曲线的最大回撤
    FitR_t       Alpha i 在**当前 Regime** 下的历史平均净收益
                 （≤t 且属该 Regime 的已完结交易；样本 < 20 时退化为全样本平均）

### §6.2 权重（**只在 IS 训练集网格确定，一次锁定**）

    Health_i = w1·z(PF_trail) + w2·z(RET_trail) + w3·z(FitR_t) − w4·z(DD_trail)
               z(·) = 用 IS 训练集的均值/标准差做标准化，参数随权重一起锁定

**预注册权重候选集（6 个，不得追加）：**

    W1 = (.40,.30,.20,.10)   W2 = (.30,.30,.30,.10)   W3 = (.40,.20,.20,.20)
    W4 = (.25,.25,.25,.25)   W5 = (.35,.25,.25,.15)   W6 = (.30,.35,.20,.15)

**选择规则**：在 IS 训练集上以 **Calmar@30bp** 为目标取 argmax；并列时取权重更均匀者
（离散度最小）。选定后写入报告并锁定。

### §6.3 最低样本闸门（先于任何权重生效）

    N_trail < 10          → 不更新状态（保持上一状态，标记 LOW_SAMPLE）
    10 <= N_trail < 20    → LOW CONFIDENCE；只允许 ACTIVE <-> WAIT，**不得触发 OFF**
    N_trail >= 20         → 正常评估

**严禁**因为「最近 3 笔赚了」就把 Alpha 从 OFF → ACTIVE。

---

## §7 Alpha 状态机（四态）

    ACTIVE     Health >= h_hi  AND  FitR 达标  AND  DD_trail 正常
    DEGRADED   Health ∈ [h_lo, h_hi)   → 资金权重按 0.5 折减
    OFF        Health < h_lo AND N_trail>=20 AND DD_trail > dd_off（需连续 2 个评估日成立）
    WAIT       N_trail < 10 / Regime 刚切换（<5 日）/ Health 不确定

**预注册阈值候选（IS 训练集确定并锁定，不得追加）：**

    h_hi   ∈ {60, 65, 70}
    h_lo   ∈ {35, 40, 45}
    dd_off ∈ {0.15, 0.20, 0.25}
    选择规则同 §6.2（IS Calmar@30bp argmax）

OFF 后必须满足**恢复确认**才可回到 ACTIVE：N_trail>=20 且 Health >= h_hi 连续 5 个
评估日（**禁止短期反弹立即 ACTIVE**；**禁止 OFF 后永久关闭**）。

---

## §8 Router 模型（五种组合）

| 编号 | 名称 | 规则 |
|---|---|---|
| B0 | **单 Alpha** | 分别测 A/B/C/D 单独运行（全仓该 Alpha 当日信号，信号内等权） |
| B1 | **Equal Weight** | 固定等权所有 Alpha，**不允许动态关闭**、不使用 Regime/Health |
| M1 | **Regime Router** | 只看 Regime：映射表 `regime → {Alpha 子集}`，由 IS 训练集内各 (Alpha, Regime) 净收益均值排序取 Top-2；R5 → Cash |
| M2 | **Alpha Health Router** | 完全不使用 Regime，仅按 Health 排序取 Top-2 |
| M3 | **Alpha OS** | `Regime 匹配 ∩ Health ACTIVE ∩ 风险约束 ∩ Selective Trading` |

**映射表必须由训练集决定，不得由测试集结果反推。** M1 的 Top-k 中 k 固定为 2。

---

## §9 Selective Trading（NO TRADE 实验）

| 模式 | 规则 |
|---|---|
| **Mode A** | 每天强制使用当前最优 Alpha（无 WAIT、无 NO TRADE） |
| **Mode B** | 允许 **WAIT**：当所有 Alpha 处于 LOW CONFIDENCE / 样本不足 / Regime 刚切换时，当日不动 |
| **Mode C** | 允许 **NO TRADE**：在 Mode B 基础上，当①Regime=R5，或②最优 Alpha 的 PIT 净收益期望估计 < 成本阈值（0 净收益）时，当日空仓 |

**NO TRADE 条件只允许使用当时可获得的信息。**

必须报告：Annual Return / Sharpe / Max DD / Calmar / PF / Turnover / 交易次数 /
空仓比例 / Cost-adjusted Return。

---

## §10 参数扰动（§18）

所有关键参数做 **±20%** 扰动，至少覆盖：

    Regime 阈值 q_a/q_b/q_d/q_v/q_w/q_f      ×0.8 / ×1.0 / ×1.2
    Regime 均线                                20 / 30 / 60
    Health 窗口（250 交易日）                  ×0.8 / ×1.0 / ×1.2
    最低样本闸门（10 / 20）                    ×0.8 / ×1.0 / ×1.2
    OFF 阈值 h_lo                              ×0.8 / ×1.0 / ×1.2
    DEGRADED 阈值 h_hi                         ×0.8 / ×1.0 / ×1.2

核心指标不得发生灾难性变化 → 输出 `aos_parameter_stability.csv`。

---

## §11 Regime 稳定性测试（§6）

    扰动：阈值 ×0.8 / ×1.0 / ×1.2  ×  均线 20 / 30 / 60

输出指标：**Regime Stability**（与基准标签的一致率）、**Transition Count**、
**Average Duration**、**Whipsaw Rate**（持续时间 ≤2 日的状态占比）。

频繁来回切换 → 标记 `WEAK`。

---

## §12 Null / Placebo Model（§19，5 个）

    Null 1  随机选择 Alpha（每日在 C4 内均匀随机取 1 个）
    Null 2  随机 Regime（打乱日期标签，保持各状态天数不变）
    Null 3  随机 Alpha 权重（Dirichlet(1,1,1,1)）
    Null 4  打乱 Alpha 与 Regime 的对应关系（置换 (Alpha, Regime) 单元格）
    Null 5  随机 NO TRADE（按真实 NO TRADE 的日频比例随机空仓）

每个 Null 至少 **1000 次**重抽样，seed 固定，报告真实模型在 Null 分布中的分位与 p 值。

**若 Alpha OS 的优势无法显著超过这些 Null → 不支持 H-AOS-01。**

---

## §13 反事实实验（§28，5 个）

    Counterfactual A  不做 Regime Router，只用固定 Alpha
    Counterfactual B  Alpha Health 延迟 5 天
    Counterfactual C  Alpha Health 完全随机
    Counterfactual D  不允许 NO TRADE
    Counterfactual E  Regime 判断错误 20%（随机翻转 20% 交易日标签）

---

## §14 其他强制检验

- **Top 5% 集中度（§20）**：全部交易 / 剔除最高 5% / 剔除最高 10%。若剔除 Top5% 后
  完全失效 → 标记 `FRAGILE`。
- **Regime Transition Test（§21）**：对 R1→R3、R2→R4、R3→R4、R4→R2 等切换，
  分别统计切换前 5 日 / 切换日 / 切换后 5 日 / 切换后 10 日，比较固态 Alpha vs Dynamic Router。
- **Alpha Death Test（§22）**：模拟 Alpha 突然失效，测 Loss Avoidance / Early Warning /
  False Shutdown / Recovery Delay。
- **Alpha Resurrection Test（§23）**：验证 OFF 后能否恢复，且不因短期反弹立即 ACTIVE。

---

## §15 评价指标（§26，不得只看收益率）

    CAGR  Sharpe  Sortino  Calmar  Max Drawdown  Profit Factor  Win Rate
    Average Trade  Turnover  Trade Count  Exposure  Cost-adjusted CAGR

    Worst Month  Worst Quarter  Maximum Consecutive Losses  95% VaR  95% CVaR

统计检验：月度聚类 bootstrap（B=1000），diff 的 95% CI 与 p 值。

---

## §16 判据（§27）

### PASS（须**同时**满足 10 条）

    1. OOS 收益优于主要固态基准
    2. Cost-adjusted 仍成立（30bp）
    3. Walk-forward 多数窗口成立
    4. 参数扰动后仍成立
    5. Top5% 剔除后仍有优势
    6. Null Model 明显弱于真实模型
    7. 不依赖单一 Regime
    8. 不依赖少数股票
    9. NO TRADE 具有增量价值
    10. Alpha Health 能降低失效期损失

### PARTIAL PASS

    收益没有显著提高，但 Max Drawdown 显著下降 + Calmar 改善 + 失效期损失减少

### FAIL（命中任意一条）

    OOS 失效 / 成本后失效 / 参数极敏感 / 只有 IS 有效 / 依赖 Top5% /
    Null Model 同样有效 / Regime 无法稳定识别 / Health 无法提前识别 Alpha 失效

---

## §17 产物清单（§29）

    必需（最终交付，13 个）
    ├── aos_research_report.md
    ├── aos_regime_series.csv
    ├── aos_alpha_regime_matrix.csv
    ├── aos_alpha_health.csv
    ├── aos_router_signals.csv
    ├── aos_backtest_results.csv
    ├── aos_walkforward.csv
    ├── aos_cost_test.csv
    ├── aos_parameter_stability.csv
    ├── aos_null_model.csv
    ├── aos_counterfactual.csv
    ├── aos_alpha_survival.csv
    └── aos_final_verdict.json

    分期加产物（P1/P2 本轮）
    ├── H_AOS_01_P1P2_REPORT.md
    └── H_AOS_01_RESULTS.json

    冻结件
    ├── H_AOS_01_SPEC.md（本文件）
    └── H_AOS_01_FREEZE.md（SPEC SHA256 + mtime）

全部落盘于 `d:\mystock\solo\research\h_aos\`。

---

## §18 强制声明句（报告须逐字包含）

> 本研究的结论仅适用于当前样本（Universe B，2022-01-01 ~ 2026-09-24）、
> 当前成本假设（主基准 30bp round-trip）与当前 OOS 设计。
> 动态 Alpha Router 相对于预设基准表现出/未表现出统计与经济意义上的增量优势；
> 该结论仍需继续进行前瞻验证。

**禁止出现**：「已经找到稳定盈利模型」/「以后可以持续赚钱」/ 把 IS 结果描述成 OOS /
因某个窗口表现优秀就宣布成功。

---

## §19 待复核的口径裁定（主动登记，不得静默）

1. **分层口径**：CORE C4 跑完整流程，EXT X2（ETF / Theme）只做描述性矩阵。
   原因：Theme 仅 60 交易日、ETF 仅 1.7 年，无法满足 §10 最小样本与 §14 Walk-Forward。
2. **Alpha C 事件化**：原突破模型为横截面候选池（近压力位最低 10% 分位），非布尔事件。
   本 SPEC 采用其第一性条件 `B6_SimpleBreakoutFlag`（close > R_20）为事件掩码，
   压力位定义与池条件全部沿用，未改任何参数。
3. **BSE 统一剔除**：对 Alpha A 属收紧（原代码路径未强制剔除）。
4. **成本统一**：不沿用各 Alpha 原生成本口径（Breakout 的 `2*slip+FEE`、ETF 的 5bp/边），
   统一为 {0,15,30,50} bp round-trip。
5. **ETF 资产池选择偏差**：`ETF_POOL` 为静态 dict（35 只），存在事后选择偏差，
   ETF 结论只在扩展层陈述，不进主结论。
6. **WEAK 判定阈值（P2 执行前登记）**：§11 只给出指标定义（Regime Stability 一致率、
   Transition Count、Average Duration、Whipsaw Rate = 持续时间 ≤2 日的状态占比），
   未给出数值切点。本 SPEC 登记执行切点为
   `agreement < 0.80  OR  whipsaw_rate > 0.30  →  WEAK`（`regime_quantiles` 无此键，
   属纯操作化定义，不参与任何收益计算）。登记时点：2026-10-02，**早于 P1/P2 结果观测**。
7. **CORE 共同窗口裁剪**：§2.3 的 2022-01-01 ~ 2026-09-24 由 `clip_core()` 施加于
   C4 合并交易表（按 `entry_date` 截取），使四个 Alpha 在同一窗口上可比。

---

## §20 泄漏防线 checklist（每段代码必须自查）

    [ ] 所有 threshold / quantile 只作用于 Train 区间
    [ ] 所有 rolling 统计只用 <= t 的数据
    [ ] Regime 标签在 t 日只用 <= t 的信息
    [ ] Alpha Health 只用已完全平仓（realized exit <= t）的交易
    [ ] 信号日 → 买入日 严格为 t 或 t+1，不得同日使用未来价
    [ ] Universe 按当日 PIT 判定，不得用未来成分
    [ ] 成本在每个周期扣减，不得事后一次性扣
    [ ] Null Model 使用同一套 PIT 约束

---

## §21 变更日志

| 日期 | 版本 | 变更 | 原因 |
|---|---|---|---|
| 2026-10-02 | 1.0 | 初版冻结 | — |
| 2026-10-02 | 1.01 | §19 增补第 6、7 条（WEAK 操作化切点；CORE 窗口裁剪函数） | Hypothesis：§11 的 WEAK 判定缺少数值切点、§2.3 的窗口未指明施加位置。修改在 P1/P2 结果观测**之前**完成，不改变任何冻结的 Alpha / Regime / 成本 / 分期定义，只补登记两处执行细节。 |

**本文件冻结后，任何修改都必须新增一行变更日志，并说明 Hypothesis 与修改原因。**
