# H-W7-RANK-01 — W7 日平均横截面相对排序的独立增量

- `hypothesis_id` = `H-W7-RANK-01`   ·   `version` = `1.0`   ·   `created` = `2026-09-30`
- `parent` = `H-ALPHA-SOURCE-01`（已归档 `No robust Alpha`，本实验未修改其任何结论）
- `trading_authorization` = `NO`
- 受控文件：`H_W7_RANK_01_SPEC.md`（2026-09-30 冻结，本报告与其一致，无事后调整）

> **问题**：HVT 的 Top-3 相对排序机制补到 W7 上，这个排序本身有没有独立增量？

---

## §1 结论

**判决：`UNPROVEN`**

在控制笔数（同日期、同 N）之后，W7 每日可执行名单的内部相对排序
**不带来任何可辨识的增量**：

| 对比 | T+10 差 | 对照区间 | p | 30bp 后 |
| --- | --- | --- | --- | --- |
| W7 RANKED − RANDOM-3 | -0.0004 | obs +0.0077 vs null 均值 +0.0081；null 95% 带 [+0.0042, +0.0123] | 0.560（单侧） | -0.0034 |
| W7 RANKED − BASE | -0.0001 | 95% CI [-0.0057, +0.0059] | 0.938（双侧） | -0.0031 |
| W7 RANKED − COMPLEMENT | -0.0011 | 95% CI [-0.0076, +0.0063] | 0.728（双侧） | -0.0041 |

第一行的区间是**随机空分布的 2.5%/97.5% 分位**（非差值的 CI）：
obs +0.0077 落在该带内部，故“取前 3 名”与“随机取 3 名”无法区分。

三个对比全部不显著且符号偏负。排序臂在每个预注册 N 上都落在随机空分布的
中心附近（excess 均为 0 或微负，p 均在 0.47–0.78）。将 W7 由 3460 笔截刷到 1039 笔，
本身并未改善单笔收益。

**正对照通过，因此本结论是信息性的：**同一套机器跑 HVT 自己的 Top-3，
显著超出同日随机 3 名（T+10 excess +0.0083，p = 0.005，且 obs 高于空分布
97.5% 上限）。说明本检验能识别一个已知有效的排序，W7 侧的阴性结果
不是“机器坏了”造成的。

---

## §2 为什么这是一个独立 Hypothesis

`H-ALPHA-SOURCE-01` 的纪律禁止在其内部继续寻找 Alpha，要求
其后续必须另立独立 Hypothesis ID。触发本实验的事后诊断
(`research/h_alpha/_diag_tail.py`，仅描述性) 发现：

```
HVT top3=1  n= 444   mean(T+20) +0.0380   P(r>=+20%) 0.151
HVT top3=0  n=1927   mean(T+20) +0.0215   P(r>=+20%) 0.099
```

同一诊断证明 10 个通用事前特征的 |rank-IC| 全部 < 0.09，且六项在
2024↔2025 之间符号翻转。因此本实验**不引入任何新指标**，只检验
“相对排序”这一机制本身。

机制动机：实盘 W7 **本来就有每日榜单截断**，只是回测账本未建模。
`w7_te_v3_backtest.py:426` 原文：

> 注意：实盘 W7 另有 SLI 龙头池过滤与每日榜单截断，本回测样本为该口径的近似超集。

因此“给 W7 补 Top-N”是**修补一个已被记录在案的口径缺口**，而非发明新机制。

---

## §3 实验设计（冻结要点）

- **研究对象**：W7 账本 `te3_v31_events_20240101_20260828.csv`，`action ∈ {PRIMARY BUY, CONDITIONAL BUY}`。
- **收益口径**：`entry = open[decision + 1]`，`exit = close[entry + h]`，`h ∈ (3,5,10,20)`，主轴 T+10，主成本 30bp。
- **三臂口径逐字相同**：actionable → Top-N 截断 / 随机 N 抽取 → dedup(stock, event_date) keep first → 20 会话 overlap guard。
- **四臂**：`BASE`（冻结 W7 现状，无截断）/ `RANKED` / `COMPLEMENT` / `RANDOM-N`（无放回，B = 1000）。
- **主配置（预先固定）**：`N = 3`，`key = (exec desc, eq desc)`——结构同构于 HVT 的
  `max_buy_candidates = 3` 与 `(execution_score, buyability)`，非结果驱动。
- **次配置**：`N ∈ {1,2,3,5,10}` × 6 组 key，**全部报告，不用于判决、禁止事后挑选**。
- **统计**：月聚类 Bootstrap B = 1000 seed = 20261101；OOS `IS=2024 / VALID=2025 / OOS=2026`。

### §3.1 正对照（强制，§3.6）

同一套机器跑 HVT 自己的三臂（`HVT RANKED` = 账本自身 Top-3；`COMPLEMENT`；
`RANDOM-3`）。判决规则：若 `HVT_RANKED − HVT_RANDOM-3` 不复现为正，则
本检验机器无法识别一个已知有效的排序，**W7 的结论随之作废**。

---

## §4 结果

### §4.1 各臂描述统计

| 臂 | n | 决策日 | T+3 | T+5 | T+10 | T+20 | T+10 胜率 | T+10 PF |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| W7 BASE | 3460 | 427 | +0.0054 | +0.0050 | +0.0078 | +0.0153 | 0.468 | 1.26 |
| W7 RANKED | 1039 | 436 | +0.0062 | +0.0066 | +0.0077 | +0.0131 | 0.475 | 1.26 |
| W7 COMPLEMENT | 2980 | 358 | +0.0056 | +0.0050 | +0.0088 | +0.0170 | 0.476 | 1.30 |
| HVT BASE | 2371 | 389 | +0.0078 | +0.0117 | +0.0148 | +0.0246 | 0.495 | 1.43 |
| HVT RANKED | 644 | 365 | +0.0061 | +0.0109 | +0.0219 | +0.0355 | 0.523 | 1.55 |
| HVT COMPLEMENT | 2070 | 368 | +0.0080 | +0.0114 | +0.0130 | +0.0222 | 0.485 | 1.39 |

读法：W7 的 `RANKED`（+0.0077）与 `BASE`（+0.0078）几乎相等，而
`COMPLEMENT`（+0.0088）反而略高。HVT 侧 `RANKED`（+0.0219）明显高于
`BASE`（+0.0148）与 `COMPLEMENT`（+0.0130）。

### §4.2 RANDOM-N 空分布（核心判决点）

同日期、同 N，只破坏排序。`excess = obs − null mean`。

| N | 排序臂 n | obs T+10 | null mean | null 95% 区间 | excess | 30bp 后 | p |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 396 | +0.0064 | +0.0084 | [+0.0006, +0.0176] | -0.0020 | -0.0050 | 0.683 |
| 2 | 729 | +0.0064 | +0.0084 | [+0.0031, +0.0135] | -0.0021 | -0.0051 | 0.776 |
| 3 | 1039 | +0.0077 | +0.0081 | [+0.0042, +0.0123] | -0.0004 | -0.0034 | 0.560 |
| 5 | 1527 | +0.0083 | +0.0082 | [+0.0051, +0.0113] | +0.0001 | -0.0029 | 0.469 |
| 10 | 2333 | +0.0073 | +0.0076 | [+0.0057, +0.0096] | -0.0003 | -0.0033 | 0.606 |

主配置 N=3：obs +0.0077 对 null mean +0.0081，excess -0.0004，p = 0.560。
obs 落在空分布区间内部：“取前 3 名”与“随机取 3 名”无法区分。
且 N=1/2/5/10 全部同样不显著，excess 均在 ±0.002 内。

### §4.3 N × key 全格点（预先固定，全部报告，未挑选）

T+10 delta（RANKED − BASE），共 30 个配置。主配置用 **粗体** 标出：

| N | key | delta T+10 | 30bp 后 | p |
| --- | --- | --- | --- | --- |
| 1 | drisk_asc+exec | +0.0033 | +0.0003 | 0.490 |
| 1 | eq | -0.0034 | -0.0064 | 0.470 |
| 1 | exec | -0.0014 | -0.0044 | 0.760 |
| 1 | exec+eq | -0.0014 | -0.0044 | 0.760 |
| 1 | exec+score | +0.0004 | -0.0026 | 0.942 |
| 1 | score | -0.0017 | -0.0047 | 0.758 |
| 2 | drisk_asc+exec | -0.0003 | -0.0033 | 0.976 |
| 2 | eq | +0.0004 | -0.0026 | 0.904 |
| 2 | exec | -0.0016 | -0.0046 | 0.646 |
| 2 | exec+eq | -0.0014 | -0.0044 | 0.674 |
| 2 | exec+score | -0.0023 | -0.0053 | 0.546 |
| 2 | score | +0.0018 | -0.0012 | 0.700 |
| 3 | drisk_asc+exec | -0.0009 | -0.0039 | 0.828 |
| 3 | eq | -0.0009 | -0.0039 | 0.748 |
| 3 | exec | -0.0005 | -0.0035 | 0.840 |
| **3** | **exec+eq** | -0.0001 | -0.0031 | 0.938 |
| 3 | exec+score | -0.0002 | -0.0032 | 0.908 |
| 3 | score | -0.0005 | -0.0035 | 0.898 |
| 5 | drisk_asc+exec | -0.0018 | -0.0048 | 0.566 |
| 5 | eq | -0.0007 | -0.0037 | 0.820 |
| 5 | exec | +0.0006 | -0.0024 | 0.826 |
| 5 | exec+eq | +0.0006 | -0.0024 | 0.826 |
| 5 | exec+score | +0.0001 | -0.0029 | 0.950 |
| 5 | score | -0.0003 | -0.0033 | 0.930 |
| 10 | drisk_asc+exec | -0.0009 | -0.0039 | 0.778 |
| 10 | eq | -0.0008 | -0.0038 | 0.794 |
| 10 | exec | -0.0006 | -0.0036 | 0.878 |
| 10 | exec+eq | -0.0005 | -0.0035 | 0.914 |
| 10 | exec+score | -0.0006 | -0.0036 | 0.888 |
| 10 | score | -0.0010 | -0.0040 | 0.768 |

最好单格为 `N=1, key=drisk_asc+exec`，delta T+10 +0.0033（但 30bp 后 +0.0003）。
这仅供完整性：**无任何配置在 30bp 后仍为正且显著**，
因此无从格点中“挑出”一个可用配置。主配置未被替换。

### §4.4 OOS（主配置，T+10）

| 对比 | 2024 (IS) | 2025 (VALID) | 2026 (OOS) |
| --- | --- | --- | --- |
| RANKED-BASE | +0.0023 (p 0.672) | -0.0012 (p 0.852) | -0.0038 (p 0.658) |
| RANKED-COMPLEMENT | +0.0015 (p 0.838) | -0.0013 (p 0.874) | -0.0077 (p 0.438) |

OOS 2026 为 -0.0038，为负；仅 2024 为正。第 3、4 条判据不满足。

### §4.5 regime 分解（进入会话，T+10，仅作解释变量）

| 对比 | BULL | RANGE | BEAR |
| --- | --- | --- | --- |
| RANKED-BASE | -0.0002 (n=479, p 0.976) | -0.0049 (n=488, p 0.406) | +0.0139 (n=72, p 0.108) |
| RANKED-COMPLEMENT | -0.0010 (n=479, p 0.870) | -0.0076 (n=488, p 0.312) | +0.0171 (n=72, p 0.216) |

BEAR 为正但 n 仅 72 笔且 p = 0.108，不具统计意义；BULL/RANGE 均为负。
第 5 条判据不满足。

### §4.6 尾部（leave-top-k%，配对子集，T+10）

| 对比 | 配对 n | full | leave 1% | leave 5% | leave 10% |
| --- | --- | --- | --- | --- | --- |
| RANKED-BASE | 1022 | +0.0077 | +0.0038 | -0.0067 | -0.0152 |
| RANKED-COMPLEMENT | 535 | +0.0143 | +0.0109 | +0.0009 | -0.0063 |

RANKED−BASE 在 leave-top-5% 后转为 -0.0067：差异完全由尾部几笔驱动。
第 6 条判据不满足。

### §4.7 正对照（HVT 自身 Top-3）

| 项 | T+3 | T+5 | T+10 | T+20 |
| --- | --- | --- | --- | --- |
| HVT RANKED obs | +0.0061 | +0.0109 | +0.0219 | +0.0355 |
| RANDOM-3 null mean | +0.0069 | +0.0107 | +0.0136 | +0.0229 |
| excess | -0.0008 | +0.0002 | +0.0083 | +0.0126 |
| p | 0.691 | 0.457 | 0.005 | 0.002 |

在主轴 T+10，排序臂 +0.0219 显著高于同日随机 3 名 +0.0136
(excess +0.0083, p = 0.005) 且高于空分布 97.5% 上限 +0.0199；T+20 同样显著
(excess +0.0126, p = 0.002)。T+3 与 T+5 不显著（这与 §2 诊断在 T+20
上观测到边际一致：该排序的优势在长期才体现）。

**结论：`HVT_RANKED − HVT_RANDOM-3` 复现为正且显著 → 检验机器有效。**
因此 W7 侧的阴性结果是“真阴”，不是工具失灵。

---

## §5 八条判据（SPEC §3.8）

| # | 判据 | 结果 |
| --- | --- | --- |
| 1 | Ranked − Random-N > 0（核心） | **FAIL** |
| 2 | Ranked − Random-N 在 30bp Net 后 > 0 | **FAIL** |
| 3 | OOS 2026 > 0 | **FAIL** |
| 4 | 至少两个年份 > 0 | **FAIL** |
| 5 | 不是单一 regime | **FAIL** |
| 6 | leave-top-5% 后仍 > 0 | **FAIL** |
| 7 | Bootstrap CI 不跨越 0 | **FAIL** |
| 8 | Bias Audit 无 FAIL | **PASS** |

通过 **1/8**。按 SPEC §3.8，不满足则为 `UNPROVEN`。

---

## §6 Bias Audit

| # | 项 | 判定 | 证据 |
| --- | --- | --- | --- |
| 1 | look_ahead | PASS | The ranking mask is a pure function of decision-day fields: destroying ev_idx and entry_idx in the input ledger leaves the selected set bit-identical (identical, 0 rows differ). All keys (exec / score / eq / drisk) are written by v31_decision() / hvt_v3_score() / classify() on the decision day; entry = open[decision + 1] for every arm and the exit is close[entry + h]. |
| 2 | selection_bias | PASS | The treated set is the frozen ledger's own actionable rows action in {PRIMARY BUY, CONDITIONAL BUY} (5882 rows, 441 decision days) and was not re-derived from outcomes. The daily cut is mechanical: min(N, day size) is taken, so per-day selected count has min 1 / median 3 / max 3 and equals the requested N on 401 of 441 days. The RANKED arm is therefore conditional on the frozen W7 candidate rule -- disclosed, not corrected. |
| 3 | survivorship_bias | PASS | panel 20180102..20260924, 5816 stocks; 247 stocks stop trading >=40 sessions before the panel end (0 of them appear in the W7 arms, 0 in the HVT control), so the price matrix retains names that later left the tape. |
| 4 | execution_bias | PASS | T+1 execution throughout and identical across arms: entry = open[decision_date + 1], exit = close[entry + h]. The ranked arm and its RANDOM-N null share the same price basis, so the comparison is like-for-like. Cost ladder 0/10/20/30/50bp, 30bp primary; the 30bp charge is applied once to the difference, not twice to both legs. Known residual: board limit-up / limit-down unfillability is not modelled on any arm. |
| 5 | overlapping_sample | PASS | One observation per event lifecycle, identical pipeline for every arm: dedup on (stock, event_date) keep first, then a 20-session guard per stock. Residual violations: 0 duplicate rows, 0 gap violations across the 6 arms (base/ranked/complement x W7/HVT). Residual serial correlation is handled by month-cluster resampling. |
| 6 | future_pivot | FLAG | No arm is defined with future data: the ranking keys are decision-day fields and the decision lag is strictly positive. FLAG (interpretability, not leakage): the W7 ledger starts 2024-01-01 while the HVT ledger starts 2025-01-01, so the treatment and the positive control are estimated on different windows (W7 20240109..20260828, HVT 20250106..20260922). The control therefore validates the ranking machine, not W7's sample; the two must not be read as one pooled estimate. |
| 7 | parameter_leakage | PASS | The primary configuration (N=3, key=exec+eq) was frozen in H_W7_RANK_01_SPEC.md by structural isomorphism with HVT's max_buy_candidates, before any result: spec mtime precedes the first run artefact (yes). The full lattice is reported -- 30 configurations, no selection -- and the frozen primary ranks 8 of 30 on T+10 delta, i.e. the reported configuration is not the best one and was not promoted from the grid. |
| 8 | randomization_leakage | PASS | The RANDOM-N null shares the ranked arm's days and daily size but destroys order only: it draws without replacement inside each decision day from that day's own actionable pool, so it can never return the ranked set (0 hits in 200 draws). Seeds are per-cell and disjoint -- base seed 20261101 + 1000 + N for W7, and the HVT control uses its own cell -- so no two arms share a draw. Null spread tightens monotonically in N (N=1 0.0170, N=2 0.0104, N=3 0.0081, N=5 0.0061, N=10 0.0039), the expected signature of a size-controlled randomisation rather than a leaked original. |

无 FAIL（一项 FLAG，来自两账本起始日不同）。第 8 条判据满足。

---

## §7 预先声明的限制

1. **研究单位是 per-trade mean，不是组合收益。**
   取 Top-3 会减少资金占用（W7 由 3460 笔降至 1039 笔）。
   即便单笔均值上升，也**不能**据此宣称组合收益改善。本实验不建资金模型。
   本次实验单笔均值甚至并未上升，此限制本轮未被触发。
2. **实盘截断规则未知。** `w7_te_v3_backtest.py:426` 只声明实盘“有 SLI
   龙头池过滤与每日榜单截断”，未给出具体规则。本实验的 Top-N 是
   **独立假设**，不是对实盘规则的还原。
3. **`RANDOM-N` 保留日期与笔数，但破坏了个股分散度结构**
   （随机抽 3 名可能同行业聚集），该差异已在 Bias Audit 中记为已知残差。
4. **月聚类数约 30–33**；分年份 bootstrap 每年约 12 个聚类，CI 会偏宽。
5. **生产风控层按设计排除**：本实验归因信号，不归因资金管理。
6. **正对照与处理臂样本窗口不同**（W7 自 2024-01-01，HVT 自 2025-01-01），
   两者不得合并为一个估计。

---

## §8 最终输出

```
HYPOTHESIS      H-W7-RANK-01
PARENT          H-ALPHA-SOURCE-01  (No robust Alpha, unchanged)
TREATMENT       W7 daily cross-sectional ranking, N = 3, key = exec+eq
CORE TEST       RANKED - RANDOM-N   (same days, same N, order destroyed)

  W7   excess T+10   -0.0004   (null mean +0.0081, p 0.560)
  W7   30bp net      -0.0034
  W7   OOS 2026      -0.0038
  HVT  excess T+10   +0.0083   (p 0.005)   <- positive control

POSITIVE CONTROL   REPRODUCED
CRITERIA PASSED    1/8

VERDICT            UNPROVEN
trading_authorization = NO
```

W7 的每日相对排序**不具独立增量**：在控制笔数后，排序臂与
随机臂不可区分（excess -0.0004，p 0.560），在 30bp 成本后转为 -0.0034，
在 OOS 2026 与 leave-top-5% 后均为负。而同一套机器能识别 HVT 自身 Top-3
(+0.0083, p 0.005)。因此：**该排序机制不可从 HVT 迁移到 W7**，
不应升级为生产策略。

---

报告生成：2026-10-01 08:16:16
