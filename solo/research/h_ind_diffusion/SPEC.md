# H-IND-DIFFUSION-01

**Industry Strength Diffusion → Stock Continuation**

**行业强度扩散驱动的个股短线延续 Alpha 自动科研智能体 V1.0**

> 本文件为用户提交规格的逐字存档（§0–§61），作为 G1–G20 判据、PASS / CONDITIONAL / FAIL 定义与全部阈值纪律的**权威来源**。
> 落盘目的：上一课题 H-TREND-PULLBACK-01 因规格未落盘，导致 19 个 Gate 编号无法判定、最终状态存在不确定性。本课题不再重复该缺陷。
> 落盘时间：2026-09-27。落盘后规格**冻结**，试验过程中不得修改。

---

## §0. 研究注册

建立全新 Hypothesis ID：

H-IND-DIFFUSION-01

研究主题：

当一个行业从少数强势股票开始向更多成分股扩散时，这种"行业内部强度扩散"是否能够预测行业内个股未来 T+3 / T+5 / T+10 的相对收益？

核心研究链：

```
行业开始变强
      ↓
行业 Breadth 扩大
      ↓
强势股票占比提高
      ↓
行业内部强度扩散
      ↓
个股 Relative Strength
      ↓
未来 T+3 / T+5 / T+10
```

---

## §1. 为什么研究这个方向

前期趋势研究已经得到：

TL-01 — Trend State → FAIL
H-TREND-PULLBACK — Pullback Quality → FAIL

主要失败原因包括：

Momentum overlap
OOS discrimination
Tail dependence
Transaction cost

因此本研究不再研究：

单只股票的技术形态

而转向：

Industry × Cross-sectional Structure

研究单位从：

Stock Pattern

升级为：

Industry State → Within-industry Diffusion → Stock Return

---

## §2. 文献先验，但禁止直接假设成立

研究智能体必须在正式回测前建立 Literature Prior。

至少参考：

1. Moskowitz & Grinblatt：行业动量。
2. Market Breadth / Cross-sectional Breadth 相关研究。
3. Momentum implementation / transaction-cost 研究。
4. Time-series momentum 与 cross-sectional momentum 的区别。

行业动量研究显示，行业收益的持续性可能解释部分个股 Momentum，同时行业 Momentum 在控制个股 Momentum 后仍有研究证据。(Wiley Online Library)

Breadth 研究也报告过行业/市场内部上涨股票比例对未来收益的预测关系，并测试过对 Momentum、Volatility 等变量的控制。(科学直通车)

但这些文献只能作为：

Prior

不能作为：

A-share Alpha 已被证明

尤其禁止把海外长期研究直接外推到 A 股短线 T+3/T+5。

---

## §3. 核心 Hypothesis

**H0**

在控制：

Individual Momentum
Industry Momentum
Market Momentum
Size
Volatility
Liquidity

以后：

Industry Diffusion 对个股未来收益没有稳定增量预测能力。

**H1**

存在一个行业内部扩散状态：

Industry Breadth
+
Breadth Change
+
Leader Breadth
+
Cross-sectional Dispersion
+
Relative Strength

能够在控制 Momentum 后预测：

T+3
T+5
T+10

且该关系：

OOS
+
Walk Forward
+
Cross Year
+
Cross Regime
+
Cost

均稳定。

---

## §4. 最核心的定义

不要研究：

"今天哪个行业最强？"

这很容易退化成：

Industry Momentum

真正研究：

行业内部的强势股票是否正在从"少数"扩散到"多数"？

即：

Intensity
+
Breadth
+
Breadth Change
+
Dispersion

---

## §5. 数据源

优先使用现有 Tushare Cache。

不得重复下载已经存在的数据。

主要数据：

daily
daily_basic
stk_factor

行业：

申万 L1
申万 L2

如果已有：

theme_config.json
subtheme_map.json

只能作为辅助研究，不得替代正式行业映射。

必须记录：

industry_mapping_coverage
UNKNOWN_count

---

## §6. Universe

默认：

A-share common stocks

排除：

ST
退市整理
上市 < 60 trading days
长期停牌
关键数据缺失

但不得静默删除。

输出：

Universe
Excluded
UNKNOWN Industry

---

## §7. Industry Cohort

以每日：

Industry × Date

建立横截面。

每个行业至少要求：

N >= 20

低于：

N < 20

标记：

LOW_SAMPLE

禁止让只有 3~10 只股票的行业轻易冲到排名第一。

---

## §8. 基础股票信号

对每只股票计算：

RET1
RET3
RET5
RET10
RET20
RET60

以及：

Close / MA5
Close / MA10
Close / MA20
Close / MA60

但这些首先作为：

Control Variables

而不是直接 Alpha。

---

## §9. Industry Momentum

计算：

Industry_RET5
Industry_RET10
Industry_RET20
Industry_RET60

使用：

Equal Weight
Value Weight
Median Stock Return

三种口径。

研究：

行业收益本身是否已经解释全部结果。

---

## §10. Industry Breadth

核心变量：

Breadth20

定义：

上涨股票数 / 有效股票数

至少计算：

Breadth1
Breadth3
Breadth5
Breadth10
Breadth20

---

## §11. Breadth Change

核心假设不是 Breadth 高，而是：

Breadth 是否正在扩散。

计算：

BreadthChange1
BreadthChange3
BreadthChange5
BreadthChange10

例如：

BreadthChange5
=
Breadth_t
-
Breadth_t-5

---

## §12. Breadth Acceleration

继续计算：

BreadthAcceleration
=
ΔBreadth_short
-
ΔBreadth_long

例如：

BreadthAcceleration
=
(Breadth_t - Breadth_t-3)
-
(Breadth_t-5 - Breadth_t-10)

但必须预注册，不允许事后选择最好窗口。

---

## §13. Strong Stock Breadth

不要只统计：

上涨

建立多层 Breadth：

AboveMA20
AboveMA60
RET5 > 0
RET20 > 0
RS > 0

例如：

Breadth_MA20
Breadth_MA60
Breadth_RET5
Breadth_RET20

---

## §14. Strong Leader Breadth

定义行业内部：

Top20%
Top10%
Top5%

股票。

统计：

LeaderBreadth

研究：

行业强势是否只集中在 1~2 个龙头，还是已经开始扩散？

这是本课题与普通 Industry Momentum 的关键区别之一。

---

## §15. Leader Concentration

计算：

Top5ReturnContribution
Top10ReturnContribution

以及：

LeaderConcentration

如果行业上涨主要依靠：

1~2只股票

而 Breadth 没有扩散：

标记：

NARROW_LEADERSHIP

---

## §16. Diffusion Gap

核心变量：

DiffusionGap

例如：

LeaderBreadth
-
OverallBreadth

以及：

Top20% Breadth
-
Bottom80% Breadth

研究：

"龙头先动、随后扩散"是否具有预测能力。

---

## §17. Cross-sectional Dispersion

计算行业内部：

Std(RET1)
Std(RET3)
Std(RET5)
Std(RET10)

以及：

P90 - P10

研究：

行业强势时，内部收益分化是否具有预测价值？

不要预设：

低 Dispersion = 好
高 Dispersion = 坏

---

## §18. Relative Strength Breadth

对每只股票计算：

Stock Return - Industry Return

建立：

RS_Breadth

例如：

行业内跑赢行业中位数的股票比例

以及：

RS_BreadthChange

---

## §19. 最重要的变量：扩散状态

建立有限、预注册的状态：

D1 — Low Breadth → Rising Breadth
D2 — High Breadth → Rising Breadth
D3 — High Breadth → Falling Breadth
D4 — Low Breadth → Falling Breadth

不要超过 4 个基本状态。

研究：

State
→
Future Stock Return

---

## §20. 更重要的四象限

构建：

Industry Momentum
×
Industry Breadth Change

四象限：

A: Momentum ↑  Breadth ↑
B: Momentum ↑  Breadth ↓
C: Momentum ↓  Breadth ↑
D: Momentum ↓  Breadth ↓

这一步用于回答：

"行业涨"与"行业正在扩散"是不是两件不同的事情？

---

## §21. 股票层面的条件研究

只研究行业内部股票。

对于每个：

Industry × Date

预测：

Stock T+3
Stock T+5
Stock T+10

核心模型：

```
FutureReturn
~
IndustryDiffusion
+
StockMomentum
+
IndustryMomentum
+
Volatility
+
Size
+
Liquidity
```

---

## §22. Momentum Control：第一核心 Gate

这是整个课题最重要的 Gate。

建立：

M0 = Stock Momentum
M1 = Stock Momentum + Industry Momentum
M2 = M1 + Industry Breadth
M3 = M2 + Diffusion Variables

比较：

M2 - M1
M3 - M2

如果：

M3 ≈ M2 ≈ M1

说明所谓 Diffusion 只是：

Industry Momentum

的重新表达。

直接：

NO_INCREMENTAL_ALPHA

---

## §23. Industry Momentum Neutralization

必须进行：

Within-industry ranking

例如：

股票收益
-
行业收益

以及：

industry-demeaned return

测试：

在行业已经涨的情况下，Breadth 是否还能解释行业内部谁继续上涨？

---

## §24. Market Neutralization

控制：

Market Return
Market Breadth
Market Momentum

避免：

行业 Breadth

只是：

全市场风险偏好

---

## §25. Size / Liquidity Control

至少控制：

Market Cap
Turnover
20D Average Amount
Volatility

防止结果其实是：

Small-cap effect
Liquidity effect
Volatility effect

---

## §26. 核心 Null Model

必须建立：

N1 Random Industry Date — 随机行业日期。

N2 Random Within-industry Stock — 随机抽取行业股票。

N3 Momentum Matched — 匹配 Stock Momentum / Industry Momentum。

N4 Industry Momentum Matched — 行业 Momentum 相同，但 Breadth 不同。

N5 Breadth Shuffled — 打乱 Breadth 时间顺序。

---

## §27. 最重要的反事实

核心比较：

Strong Industry Momentum + Breadth Expansion

vs

Strong Industry Momentum + No Breadth Expansion

如果两者没有明显差异：

Breadth adds no information.

这比单纯：

Breadth > 60%

更重要。

---

## §28. Cross-sectional Ranking

每天在行业内部排序：

DiffusionExposure

形成：

Q1
Q2
Q3
Q4
Q5

观察：

Q1 → Q5

未来：

T+3
T+5
T+10

是否单调。

必须重点检查：

Monotonicity

---

## §29. 不允许只看 Top Decile

如果：

Top10%

表现很好，但：

Q1-Q10

不单调：

标记：

LOW_DISCRIMINATION

不能称为稳定排序 Alpha。

---

## §30. AUC / Discrimination

预测：

FutureReturn > 0

以及：

FutureReturn > industry median

计算：

AUC
Precision@10
Recall@10

特别关注：

AUC - 0.5

---

## §31. IC

计算：

Rank IC
Pearson IC

至少：

T+3
T+5
T+10

输出：

Mean IC
ICIR
Positive %

---

## §32. Fama-MacBeth

至少：

```
FutureReturn
~
Diffusion
+
StockMomentum
+
IndustryMomentum
+
Size
+
Liquidity
+
Volatility
```

输出：

Beta
t-stat
Newey-West t

关键：

Diffusion beta

必须在控制 Momentum 后仍然显著。

---

## §33. OOS

必须：

TRAIN
VALID
OOS

所有：

Feature selection
Threshold selection
Model selection

只能使用 TRAIN。

VALID 用于模型确认。

OOS 最后一次使用。

---

## §34. Walk Forward

至少：

6 windows

每个窗口：

Train → Validate → OOS

不能使用未来数据。

输出：

IC
AUC
Q5-Q1
T+3
T+5
T+10

---

## §35. Cross-Year

至少：

2021
2022
2023
2024
2025
2026

实际数据不足则如实报告。

PASS 不要求每一年都正。

但要求：

Positive Year Ratio >= 60%

并且：

不得由单一年份贡献绝大部分 Alpha。

---

## §36. Regime

定义：

BULL
NORMAL
BEAR

分别计算：

IC
Q5-Q1
AUC
T+5

禁止：

只选择有效 Regime

---

## §37. Parameter Perturbation

预注册：

Breadth Window: 3 / 5 / 10 / 20
Momentum: 10 / 20 / 40
Industry N: 15 / 20 / 30
Leader: 10% / 20%

重点：

找稳定平台，不找最优参数。

---

## §38. Parameter Surface

最终生成：

Breadth Window × Momentum Window

热力图。

要求：

不是单点峰值

如果：

只有一个参数组合显著

标记：

PARAMETER_FRAGILE

---

## §39. Tail Test

必须：

Leave Top1%
Leave Top5%
Leave Top10%

检查：

Alpha

如果：

Alpha → 删除 Top5% → 翻负

则：

EXTREME_TAIL_DEPENDENT

---

## §40. Breadth Sample Size

行业必须：

N >= 20

另外测试：

N >= 30
N >= 50

如果只有：

N < 20

产生 Alpha：

标记：

LOW_SAMPLE

---

## §41. Cost

这是致命 Gate。

至少测试：

0bp
10bp
20bp
30bp
50bp

计算：

Turnover
Average Holding
Gross Return
Net Return

重点：

30bp round-trip

如果：

Net Alpha <= 0

直接：

COST_FAIL

---

## §42. Signal Density

统计：

Signal Days
Signal Count
Exposure
Average Holding
Turnover

不要用：

低暴露策略

与：

满仓指数

做不公平比较。

---

## §43. Portfolio Construction

只有科研通过以后才允许构造策略。

候选：

行业 Top3
行业内部 Top20%

权重：

Equal Weight

暂不允许：

复杂优化

避免重新进入参数挖掘。

---

## §44. Portfolio Benchmark

至少：

B1 Market
B2 Industry Momentum
B3 Stock Momentum
B4 Industry Momentum + Stock Momentum

最终比较：

Diffusion Strategy
-
B4

这才是真正的：

Incremental Alpha

---

## §45. Permutation

对：

Diffusion Signal

做：

1000 permutations

保持：

股票
日期
样本量

不变。

计算：

Empirical p-value
Z-score

---

## §46. Multiple Testing

必须记录所有测试：

Feature
Window
Horizon
Quantile
Model
Regime

不得只报告最好的结果。

使用：

FDR / Benjamini-Hochberg

报告：

Raw p
Adjusted p

---

## §47. Leader Concentration Test

必须单独测试：

NARROW LEADERSHIP

与

BROAD DIFFUSION

比较。

核心问题：

行业上涨时，究竟是"龙头行情"还是"行业扩散行情"更容易持续？

不能预设答案。

---

## §48. Diffusion Sequence Test

研究：

Leader First → Breadth Expansion → Stock Continuation

与：

Breadth First → Leader Follow

比较。

但最多测试：

2种 sequence

防止组合爆炸。

---

## §49. Failure Analysis

将失败案例分类：

F1 Industry Momentum Reversal
F2 Breadth Fake Expansion
F3 Leader Collapse
F4 Market Regime Reversal
F5 Industry Rotation
F6 Narrow Leadership
F7 Stock-specific Shock
F8 Liquidity Failure

统计：

Failure %

目的不是事后修策略。

而是回答：

为什么这个信号会失败？

---

## §50. 核心 Gate

建立：

G1 — PIT / Data Integrity
G2 — Industry Mapping
G3 — Tradability
G4 — Momentum Control
G5 — Industry Momentum Control
G6 — Market Control
G7 — Random Industry-Date Null
G8 — Momentum-Matched Null
G9 — OOS
G10 — Walk Forward
G11 — Cross-Year
G12 — Regime
G13 — OOS Discrimination
G14 — Parameter Stability
G15 — Tail Stability
G16 — Cost
G17 — Permutation
G18 — Multiple Testing
G19 — Economic Significance
G20 — Incremental Alpha

---

## §51. 致命 Gate

以下任何一项 FAIL：

G1
G4
G5
G8
G9
G13
G16
G20

则：

TRADING_AUTHORIZATION = NO

---

## §52. PASS

必须同时满足：

Momentum Control PASS
Industry Momentum Control PASS
OOS PASS
WF PASS
OOS AUC / IC 有效
Q5-Q1 稳定
Cross-Year PASS
Regime PASS
Parameter Surface PASS
Leave-Top5% PASS
Permutation PASS
30bp Cost PASS
Incremental Alpha PASS

最终：

PASS — ROBUST INDUSTRY DIFFUSION INCREMENTAL ALPHA CONFIRMED

---

## §53. CONDITIONAL

如果：

Industry Diffusion

能预测：

Failure Risk

但不能产生：

Price Alpha

则：

CONDITIONAL — INDUSTRY DIFFUSION RISK INFORMATION CONFIRMED, NO ROBUST PRICE ALPHA

不得授权交易。

---

## §54. FAIL

出现以下任何结构：

Diffusion ≈ Industry Momentum
Diffusion ≈ Stock Momentum
OOS discrimination FAIL
Random null survives
Cost FAIL
Tail FAIL
Regime unstable
Parameter isolated peak
Permutation FAIL

则：

FAIL — NO ROBUST INCREMENTAL INDUSTRY DIFFUSION ALPHA FOUND

禁止：

继续调阈值
挑行业
挑年份
挑 Regime
降低成本假设
改变 Benchmark

---

## §55. 最终必须回答的 15 个问题

1. 行业 Breadth 是否预测未来行业收益？
2. Breadth Change 是否比 Breadth Level 更有信息？
3. 行业 Momentum 是否已经解释 Breadth？
4. Leader Breadth 是否提供增量信息？
5. Narrow Leadership 与 Broad Diffusion 是否存在差异？
6. Diffusion 是否能预测行业内部股票？
7. 控制 Stock Momentum 后是否仍然有效？
8. 控制 Industry Momentum 后是否仍然有效？
9. 控制 Market Momentum 后是否仍然有效？
10. Random Industry-Date 是否能够复制？
11. Momentum-Matched Null 是否能够复制？
12. OOS 是否有效？
13. 是否跨年份、跨 Regime 稳定？
14. 30bp 成本后是否仍有经济意义？
15. Industry Diffusion 是否最终形成独立于既有 20D Momentum 的 Incremental Alpha？

---

## §56. 与现有系统的接口

如果科研 PASS：

```
Industry Diffusion
        ↓
Theme / Industry Gate
        ↓
Existing 20D Momentum
        ↓
Stock Selection
```

注意：

Diffusion 只能作为经过独立验证的增量模块，不得直接替换现有 Momentum。

如果 FAIL：

ARCHIVE

不得污染现有生产策略。

---

## §57. 最终输出文件

生成：

research/h_ind_diffusion/

至少：

```
industry_daily_state.parquet
industry_diffusion_features.parquet
stock_diffusion_exposure.parquet
ic_results.csv
auc_results.csv
quantile_results.csv
fama_macbeth.csv
oos_results.csv
walkforward.csv
year_results.csv
regime_results.csv
parameter_surface.csv
null_models.csv
permutation.csv
cost_analysis.csv
tail_analysis.csv
failure_analysis.csv
gate_results.csv
h_ind_diffusion.json
report.md
```

---

## §58. 自动科研智能体执行顺序

严格执行：

```
STEP 1   读取现有 Tushare Cache
STEP 2   检查数据 PIT / Mapping
STEP 3   建立 Industry Cohort
STEP 4   计算 Industry Momentum
STEP 5   计算 Breadth
STEP 6   计算 Breadth Change
STEP 7   计算 Leader Breadth
STEP 8   计算 Diffusion
STEP 9   建立 Baseline
STEP 10  单变量 IC / Quantile
STEP 11  Momentum Neutralization
STEP 12  Industry Momentum Neutralization
STEP 13  Null Model
STEP 14  OOS
STEP 15  Walk Forward
STEP 16  Cross-Year
STEP 17  Regime
STEP 18  Parameter Perturbation
STEP 19  Tail
STEP 20  Cost
STEP 21  Permutation
STEP 22  Multiple Testing
STEP 23  Gate G1-G20
STEP 24  Final Decision
```

---

## §59. 绝对禁止

禁止：

```
看到 Breadth 有效          → 直接做策略
看到行业排名有效           → 直接交易
看到某个窗口最好           → 固定该窗口
看到某行业有效             → 限定该行业
看到牛市有效               → 只做牛市
看到 T+5 最好              → 固定 T+5
看到 Top10 有效            → 忽略全分位
看到 Permutation PASS      → 自动认定 Trading Alpha
看到 IC 正                 → 自动认定 Price Alpha
```

---

## §60. 最终科研哲学

本研究真正要证明的不是：

"行业扩散策略赚钱。"

而是：

在已经知道行业 Momentum 和股票 Momentum 的情况下，行业内部"强度从少数股票向多数股票扩散"是否仍然包含新的、可事前识别、可跨期重复、成本后存在经济意义的信息？

如果答案是 YES：

```
Industry Momentum
        +
Stock Momentum
        +
Industry Diffusion
```

才可能形成真正的新 Alpha 层。

如果答案是 NO：

Industry Diffusion 只是 Momentum 的另一种描述。

此时必须 ARCHIVE，不再优化。

---

## §61. 最终交易授权

默认：

TRADING_AUTHORIZATION = NO

只有：

G1-G20

全部达到预注册标准，并且：

Incremental Alpha
+
OOS
+
Cost
+
Null Model
+
Tail

全部通过后，才允许进入下一独立课题：

H-IND-DIFFUSION-TRADE-01

本研究本身不产生任何具体股票买入授权。
