# Step 4 + Step 8：SEOS 与盘后主题排名校准审计报告

- **审计对象日期**：signal_date = `20260918`；Step 8 主题层 theme_date = `20260917`
- **审计模式**：`AUDIT_ONLY`（未修改任何生产代码 / 配置 / 权重）
- **审计执行体**：`audit_seos_step4_step8.py`（只读复算，落盘 JSON/CSV）
- **审计时间**：2026-09-20
- **机器可读结果**：`output/seos_step4_step8_audit_20260918.json`

---

## 1. Executive Summary

| 项 | 结论 |
|---|---|
| SEOS 公式一致性（§六 RECONCILIATION） | **SEOS_RECONCILIATION_PASS**（内存精确复算 `max_abs_raw_delta = 0`、`max_abs_score_delta = 0`、`max_abs_penalty_delta = 0`） |
| 落盘 CSV 展示层一致性 | PASS（因 2 位小数存储产生 < 0.01 差异，非公式问题） |
| SEOS 定义是否与设计一致（§四 / §五） | **基本 PASS**：七分项全部基于变化量；`extension_penalty` 为唯一绝对值项且方向为扣减 |
| Step 8 是否按 `SEOS DESC` 排序（§十二） | **否**（`is_seos_desc = False`）。排序键为 `theme_strength_score` 复合分 → **不构成 §十二 SEMANTIC_RANKING_ERROR** |
| 汽车 80.59 排第 1（§七） | **数学正确**，属正常 SEOS 结构排序（Root Cause = **D**）。无需改权重 |
| AI算力 76.33 排第 6（§八） | **数学正确**，差距 4.26 分中 2.58 来自 `core_breadth`、1.36 来自延伸扣减 |
| 是否发现真实缺陷 | **是，4 个**：`amount_share_delta_3` 键缺失（P0）、`volume_ratio` 数据缺口（P0）、`seos_change` 字段错标（P1）、7/50 主题 `core_breadth` 结构性缺失（P1） |
| 总体 Root Cause（§十六） | **G. MULTIPLE_ISSUES**（主因 D 正常行为 + C 聚合错误 + B 字段语义错误 + A 数据错误 + F 展示错误 叠加） |
| 最终判定（§十三 / §二十二） | `FIX_FIELD + FIX_AGGREGATION + FIX_STEP8_DISPLAY + FIX_PHASE`；**KEEP SEOS 模型本身，不予 RECONSIDER** |

**核心结论一句话**：汽车及零部件 SEOS = 80.59 排第 1 是七项结构变化分按权重聚合后的**正确结果**，不是公式错误、不是字段错位、更不是"当日涨幅排名"；用户感到"排名可疑"的真正来源是**展示层与字段层**——Step 8 报告中既有 `seos_change` 字段被错标成 health 变化（P1），又缺少"当日强弱"维度与排序键说明（P1），而 Step 8 主题排序本身用的是**复合分而非 SEOS**（P0 级聚合键缺失使该复合分实际只算了 4 项）。

---

## 2. SEOS Definition Audit（§四：设计 vs 实现）

`SEOS = Σ(weight_i × seos_i) × (1 − min(extension_penalty, 0.45))`

| # | 分项 | 设计权重 | 实现使用的字段（子项权重） | 语义 | 类型 | 判定 |
|---|---|---|---|---|---|---|
| 1 | `breadth_expansion` | 0.25 | `breadth_expansion_5`(.40) + `breadth_expansion_10`(.35) + `breadth_acceleration`(.25) | 短期广度均值相对中期基线的抬升 + 加速度 | 变化量（全部） | **PASS** |
| 2 | `core_breadth` | 0.20 | `core_breadth_delta_5`(.50) + `core_breadth_delta_3`(.30) + `core_lead_breadth`(.20) | CORE 层净广度的 5/3 日变化 + CORE 相对 PRIMARY 层间差 | 变化量（全部） | **PASS** |
| 3 | `relative_strength` | 0.15 | `rs_turn_5`(.40) + `rs_turn_10`(.25) + `relative_strength_5`(.25) + `rs_fresh_cross`(.10) | 相对强度一阶变化为主（65%）+ 绝对水平（25%）+ 上穿事件（10%） | 混合（权重已显式分离） | **PASS** |
| 4 | `volume_participation` | 0.15 | `volume_ratio_5_excess`(.60) + `volume_ratio_delta_5`(.40) | 量能比相对 1.0 的超出 + 量能比自身 5 日变化 | 变化量（全部） | **FAIL（当日数据缺）** |
| 5 | `amount_share` | 0.10 | `amount_share_delta_5`(.50) + `amount_share_delta_10`(.30) + `amount_share_delta_3`(.20) | 成交额占比的 5/10/3 日变化 | 变化量（全部） | **PASS** |
| 6 | `health_momentum` | 0.10 | `theme_health_delta_5`(.45) + `theme_health_delta_3`(.30) + `theme_health_delta_10`(.25) | Step 3 主题健康度的 5/3/10 日变化 | 变化量（全部） | **PASS** |
| 7 | `consistency` | 0.05 | `improving_dim_ratio`(.60) + `positive_contribution_ratio`(.25) + `concentration_inverse`(.15) | 改善维度占比 / 正贡献成员占比 / 低集中度 | 比率与绝对值（非 delta） | **PASS** |
| — | `extension_penalty` | 乘性扣减 | `ew_ret_5`(.40) + `ew_ret_10`(.25) + `ew_ret_20`(.15) + `top5_concentration`(.20)，上限 0.45 | 累计涨幅 + 集中度映射到 0~1 | 绝对值（方向为扣减） | **PASS** |

**审计结论**：
1. 七分项**没有任何一项直接使用当日（D 日）涨跌幅**。分项 1/2/5/6 为纯 delta；分项 3 的绝对水平部分 `relative_strength_5` 是 **5 日累计相对强度**（非单日）；分项 7 为无量纲比率。→ **SEOS 不是"当日主题强弱排名"**（§四 核心问题的答案是"否"）。
2. 唯一使用绝对收益水平的位置是 `extension_penalty`，方向为**扣减**，与"奖励结构变化、惩罚过度延伸"的设计方向一致。
3. 缺值处理为 `neutral_fill_score = 50.0` 中性填充，属合规但**会造成 15% 权重当日无区分度**（见 P0-1）。

---

## 3. Field Semantic Audit（§五）

| 字段 | 来源 | 实际公式 | 值域 | 语义类型 | 备注 |
|---|---|---|---|---|---|
| `breadth` | Step 3 `sector_daily_stats` | `(up − down) / valid_n` | [−1, 1] | **当日绝对值（净扩散）** | **不是**上涨家数占比；`up_ratio` 是另一字段 |
| `breadth_delta_k` | Step 4 `compute_features` | `breadth(D) − breadth(D−k)` | [−2, 2] | 变化量（pct 差） | — |
| `core_breadth` | Step 3 | CORE 层 `(up − down) / valid_n`；CORE 为空时退化为 PRIMARY，均空则为空 | [−1, 1] | **当日绝对值（净扩散）** | 可为负；20260918 汽车 = **0.389121** |
| `core_breadth_delta_k` | Step 4 | `core_breadth(D) − core_breadth(D−k)` | [−2, 2] | 变化量 | SEOS `core_breadth` 分项主输入；20260918 汽车 `delta_3 = 1.174836`、`delta_5 = 1.216126` |
| `relative_strength` | Step 3 `relative_strength_5`(=`vs_market_5`) | 主题等权 5 日收益 − 沪深300 5 日收益 | 约 [−0.3, 0.3] | **区间绝对值（5 日累计相对强度）** | 20260918 汽车 = **0.038144** |
| `rs_turn_5 / rs_turn_10` | Step 4 | `relative_strength(D) − relative_strength(D−5 / D−10)` | 约 [−0.3, 0.3] | 变化量 | `relative_strength` 分项 65% 权重来源 |
| `theme_health` | Step 3 `sector_health` | 6 项加权（breadth .25 / weighted_breadth .20 / rs .15 / volume .15 / core_primary .15 / consistency .10） | [0, 100] | **当日绝对值** | SEOS 只取其 delta |
| `theme_amount_share` | Step 3 | `sector_amount / market_amount` | [0, 1] | 当日绝对值 | SEOS 只取其 delta_3/5/10 |
| `volume_ratio_5` | Step 3 | `avg_member_amount(D) / mean(avg_member_amount, D−1..D−5)` | 约 [0, 5] | 区间绝对值（相对自身历史） | **20260918/20260917 为 NA** |

### 3.1 对审计书 §五 举例的更正（重要）

审计书 §五 给出的对照示例为「汽车核心广度 = 0.0381」。经复算：

- 20260918 汽车及零部件 **真实 `core_breadth` = 0.389121**（当日 CORE 层净扩散，绝对值）；
- 数值 **0.038144** 实际是 `relative_strength_5`（5 日累计相对强度）。

→ 该示例本身就**把 `relative_strength` 误标为 `core_breadth`**。这从旁印证了本次审计的 P1-3 结论：**Step 3 `Theme Health/State` 与 Step 4 `SEOS` / 板块层的语义在文档与展示中已被混用**，混用程度已高到审计输入本身都被污染。**字段实现本身无误**，问题在语义标注与展示。

### 3.2 `breadth` 与 `core_breadth` 的量级说明

20260918 汽车及零部件：`breadth = 0.564735`（PRIMARY 主导层净扩散）、`core_breadth = 0.389121`。两者均为 [−1, 1] 区间的**绝对值**，而 SEOS 使用的是它们的 **3/5 日变化量**（`core_breadth_delta_5 = 1.216126`），因此 `seos_core_breadth = 100.0`（ramp 饱和）是合理的——原因是 **5 日前 core_breadth 曾大幅为负**，而非"当日广度极高"。

---

## 4. SEOS Component Attribution（Top 15，§六）

`seos_score(D) = seos_raw × (1 − min(penalty, 0.45))`，`seos_raw = Σ(w_i × s_i)`。
复算基准为内存精确流水线（`sector_seos_build.compute_features/compute_scores`），非落盘四舍五入值。

| # | 主题 | Phase | SEOS | raw | 扣减 | 广度扩张<sub>.25</sub> | 核心广度<sub>.20</sub> | 相对强度<sub>.15</sub> | 量能参与<sub>.15</sub> | 成交占比<sub>.10</sub> | 健康动量<sub>.10</sub> | 一致性<sub>.05</sub> |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | T15 汽车及零部件 | CONFIRMING | **80.59** | 82.67 | 0.0251 | 79.76 | **100.00** | 69.73 | 50.00 | 98.57 | 100.00 | 98.18 |
| 2 | T12 机器人与自动化 | CONFIRMING | **79.32** | 80.25 | 0.0116 | 79.67 | 92.99 | 68.78 | 50.00 | 90.05 | 100.00 | 98.16 |
| 3 | T36 创新药 | EMERGING | **78.58** | 81.05 | 0.0305 | 88.12 | 84.10 | 79.77 | 50.00 | 79.32 | 100.00 | 96.07 |
| 4 | T37 医疗器械 | CONFIRMING | **78.48** | 79.41 | 0.0118 | 84.24 | 90.00 | 71.57 | 50.00 | 73.15 | 100.00 | 96.08 |
| 5 | T09 光伏产业链 | DORMANT | **77.19** | 77.19 | 0.0000 | 75.08 | 93.33 | 65.70 | 50.00 | 75.59 | 100.00 | 96.81 |
| 6 | T02 AI算力 | CONFIRMING | **76.33** | 79.77 | 0.0431 | 78.37 | 87.09 | 70.43 | 50.00 | 97.78 | 100.00 | 98.39 |
| 7 | T39 医疗服务 | EMERGING | **76.08** | 81.81 | 0.0701 | 86.90 | 90.00 | 79.76 | 50.00 | 81.39 | 98.46 | 92.79 |
| 8 | T33 美容护理 | EMERGING | **74.85** | 75.86 | 0.0133 | 83.22 | 90.00 | 64.35 | 50.00 | 60.49 | 93.04 | 90.94 |
| 9 | T03 通信 | CONFIRMING | **74.48** | 79.14 | 0.0589 | 75.52 | 88.70 | 67.77 | 50.00 | 100.00 | 100.00 | 97.08 |
| 10 | T05 消费电子 | CONFIRMING | **74.47** | 82.71 | 0.0996 | 81.12 | 90.00 | 79.95 | 50.00 | 100.00 | 100.00 | 98.66 |
| 11 | T14 高端装备 | CONFIRMING | **73.93** | 74.24 | 0.0042 | 76.55 | 87.00 | 67.16 | 50.00 | 53.66 | 99.89 | 95.53 |
| 12 | T08 锂电产业链 | DORMANT | **73.91** | 75.71 | 0.0237 | 78.48 | 84.41 | 73.44 | 50.00 | 58.04 | 100.00 | 97.83 |
| 13 | T04 软件与信创 | DORMANT | **73.49** | 73.49 | 0.0000 | 74.46 | 90.06 | 62.12 | 50.00 | 62.84 | 91.14 | 93.00 |
| 14 | T21 化工新材料 | DORMANT | **72.63** | 75.64 | 0.0399 | 76.87 | 86.17 | 75.18 | 50.00 | 55.47 | 100.00 | 97.25 |
| 15 | T30 家电 | CONFIRMING | **72.28** | 73.47 | 0.0162 | 76.02 | 90.00 | 61.98 | 50.00 | 52.43 | 99.11 | 90.24 |

**归因可读性（Top 15）**：
- **量能参与列 15/15 全为 50.00** → P0-1 数据缺口（`volume_ratio` NA 中性填充）使该 15% 维度在当日**完全无区分度**。
- **健康动量列 10/15 恰好为 100.00，全部 15 行 ≥ 91.14** → `health_momentum` ramp 上限偏低，在 Top 段接近饱和；该 10% 维度区分度弱。
- 真正在 Top 段产生区分度的是 **`core_breadth`（20%）** 与 **`amount_share`（10%）**。

**RECONCILIATION 明细**：
- 内存精确口径：`max_abs_raw_delta = 0`，`max_abs_score_delta = 0`，`max_abs_penalty_delta = 0` → **PASS**
- 落盘 CSV 展示口径：`max_abs_raw_delta = 5.5e-07`、`max_abs_score_delta = 4.05e-05`（2 位小数四舍五入）→ **PASS（误差范围内）**

完整 15 行逐分项 CSV：`data/seos_component_attribution_20260918.csv`（含全部子项原始特征值、`score_*`、`contrib_*`、`raw_delta`、`score_delta`、`pen_delta`）。

---

## 5. Why Auto（汽车及零部件）Ranked #1（§七）

### 5.1 汽车 vs 机器人（第 1 vs 第 2）逐项差值

| 分项 | 权重 | 汽车得分 | 机器人得分 | 加权贡献差（汽车−机器人） |
|---|---|---|---|---|
| 广度扩张 | 0.25 | 79.76 | 79.67 | +0.0226 |
| **核心广度** | 0.20 | **100.00** | 92.99 | **+1.4028** |
| 相对强度 | 0.15 | 69.73 | 68.78 | +0.1414 |
| 量能参与 | 0.15 | 50.00 | 50.00 | 0.0000（缺口致无效） |
| 成交占比 | 0.10 | 98.57 | 90.05 | +0.8522 |
| 健康动量 | 0.10 | 100.00 | 100.00 | 0.0000 |
| 一致性 | 0.05 | 98.18 | 98.16 | +0.0009 |
| **延伸扣减** | — | penalty 0.02510 | penalty 0.01156 | **−1.1474**（汽车的扣减更大） |
| **合计** | | **80.59** | **79.32** | **+1.2725** |

**校验**：`0.0226 + 1.4028 + 0.1414 + 0 + 0.8522 + 0 + 0.0009 − 1.1474 = +1.2725 = 80.59 − 79.32` ✅ 完全闭合。

**结论**：汽车领先机器人的 1.27 分中，**`core_breadth` 贡献 +1.40**（汽车 `core_breadth_delta_5 = 1.216` 已使 ramp 饱和到 100，机器人 92.99）、**`amount_share` 贡献 +0.85**；但同时汽车因 5/10/20 日累计涨幅更高（`ew_ret_5 = 3.75%`）**被多扣 1.15 分**——这正是 SEOS"非追涨"设计在起作用。汽车仍能第 1，说明其**结构改善强度足以覆盖延伸扣减**。

### 5.2 为什么说这是"正常行为"而非错误

1. 七项全部为结构变化量（见 §2），汽车第 1 **不代表**当日涨幅第 1：汽车当日等权收益 `+1.51%`，在 50 个主题中 `cur_strength` 仅排 **第 7**（AI算力 +2.05%、消费电子 +2.16%、通信 +2.04% 均高于汽车）。
2. 汽车之所以拿满分 `core_breadth`，是因为 `core_breadth` 从 5 日前的负值拉到当日 `0.389`（`delta_5 = +1.216`）——**是"变化大"，不是"水平高"**。
3. 因此：**汽车 80.59 排第 1 属于 D. NORMAL_SEOS_BEHAVIOR**；不需要、也禁止为使其"看起来更符合当天市场"而调权重（§十四）。

---

## 6. Why AI Computing（AI算力）Ranked #6（§八）

### 6.1 AI算力 vs 汽车及零部件 逐项差值

| 分项 | 权重 | 汽车得分 | AI算力得分 | 加权贡献差（AI−汽车） |
|---|---|---|---|---|
| 广度扩张 | 0.25 | 79.76 | 78.37 | −0.3480 |
| **核心广度** | 0.20 | 100.00 | 87.09 | **−2.5812** |
| 相对强度 | 0.15 | 69.73 | 70.43 | +0.1053 |
| 量能参与 | 0.15 | 50.00 | 50.00 | 0.0000（缺口致无效） |
| 成交占比 | 0.10 | 98.57 | 97.78 | −0.0792 |
| 健康动量 | 0.10 | 100.00 | 100.00 | 0.0000 |
| 一致性 | 0.05 | 98.18 | 98.39 | +0.0106 |
| **延伸扣减** | — | penalty 0.02510 | penalty 0.04311 | **−1.3637**（AI算力扣减更大） |
| **合计** | | **80.59** | **76.33** | **−4.2562** |

**校验**：`−0.3480 − 2.5812 + 0.1053 + 0 − 0.0792 + 0 + 0.0106 − 1.3637 = −4.2562 = 76.33 − 80.59` ✅ 完全闭合。

### 6.2 解读

AI算力当日**盘面极强**（`ew_ret_1 = +2.05%`、成员中涨幅 ≥3% 占比 **25.5%**、上涨占比 **84.7%**、`cur_strength = 89.34` 排第 **2**），但 SEOS 只排第 **6**，原因有两条且都是设计使然：

1. **核心广度 −2.58**：AI算力 `core_breadth = 0.782`（当日绝对值很高），但 5 日前已经在高位，`core_breadth_delta_5` 远小于汽车 → ramp 只到 87.09。**SEOS 奖励"变好"，不奖励"一直好"。**
2. **延伸扣减 −1.36**：AI算力 `ew_ret_5`（5 日累计涨幅）高于汽车，触发更重的追涨扣减 → 这是 SEOS 的**风险控制**，不是排错。

→ **AI算力第 6 属于 D. NORMAL_SEOS_BEHAVIOR**。AI算力的"强"应由 **Q3 当日强弱维度**（`cur_strength` 第 2）回答，而不应由 SEOS 回答（§二十二）。

---

## 7. Current-Day Strength Diagnostic（§九，`diagnostic_only`）

> **声明**：本节为**审计期新增的诊断维度**，**不接入生产**、**不参与任何排序**、**未写入任何配置文件**。权重与 ramp 为审计建议规格，可被否决。

**设计口径**：

```
cur_strength = 100 × Σ(w_k × ramp(x_k, lo_k, hi_k)) / Σw_k
w = { cur_ret .25, cur_breadth .20, cur_core .20, cur_amount .15, cur_rs .10, cur_strong .10 }
ramp: cur_ret[-3%,+3%] / cur_breadth[-0.5,0.5] / cur_core[-0.5,0.5]
      cur_amount[P10,P90 横截面] / cur_rs[-2%,+2%] / cur_strong[5%,40%]
x = { ew_ret_1(D), breadth(D), core_breadth(D), sector_amount_share(D),
      ew_ret_1(D) − 沪深300日收益, 成员涨幅≥3%占比 }
```

**当日事实**：沪深300 `bench_ret_1 = +1.0591%`；中位数 `median(seos) = 67.273`、`median(cur_strength) = 69.086`。

### 7.1 当日强弱 Top 12

| # | 主题 | cur_strength | 对应 SEOS 排名 | 象限 |
|---|---|---|---|---|
| 1 | 半导体 | 94.95 | #17 | A |
| 2 | AI算力 | 89.34 | #6 | A |
| 3 | 消费电子 | 89.24 | #10 | A |
| 4 | 通信 | 89.16 | #9 | A |
| 5 | 机器人与自动化 | 87.11 | #2 | A |
| 6 | 光伏产业链 | 82.22 | #5 | A |
| 7 | 汽车及零部件 | 81.58 | #1 | A |
| 8 | 锂电产业链 | 80.89 | #12 | A |
| 9 | 高端装备 | 79.39 | #11 | A |
| 10 | 软件与信创 | 78.88 | #13 | A |
| 11 | 军工 | 77.61 | #30 | B |
| 12 | 地产链 | 74.59 | #25 | A |

### 7.2 关键主题市场事实对照表（§十五）

| 主题 | SEOS # | SEOS | Phase | Cur # | cur_strength | ew_ret_1 | 超沪深300 | 涨幅≥3%占比 | 上涨占比 | 成员数 | 象限 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 汽车及零部件 | 1 | 80.59 | CONFIRMING | 7 | 81.58 | +1.51% | +0.45% | 19.5% | 77.4% | 811 | A |
| 机器人与自动化 | 2 | 79.32 | CONFIRMING | 5 | 87.11 | +1.90% | +0.84% | 21.1% | 81.4% | 1424 | A |
| 创新药 | 3 | 78.58 | EMERGING | **30** | 67.09 | +1.37% | +0.31% | 14.6% | 75.1% | 329 | **D** |
| 医疗器械 | 4 | 78.48 | CONFIRMING | 17 | 73.16 | +1.63% | +0.57% | 17.8% | 78.7% | 427 | A |
| 光伏产业链 | 5 | 77.19 | DORMANT | 6 | 82.22 | +1.75% | +0.69% | 19.8% | 81.0% | 769 | A |
| AI算力 | 6 | 76.33 | CONFIRMING | 2 | 89.34 | +2.05% | +0.99% | 25.5% | 84.7% | 817 | A |
| 通信 | 9 | 74.48 | CONFIRMING | 4 | 89.16 | +2.04% | +0.98% | 25.1% | 84.7% | 463 | A |
| 消费电子 | 10 | 74.47 | CONFIRMING | 3 | 89.24 | +2.16% | +1.10% | 28.1% | 84.4% | 570 | A |

**读法**：
- 汽车 / 机器人 / 医疗器械 / 光伏 / AI算力 / 通信 / 消费电子 均落在 **象限 A**（结构改善 + 当日共振）→ 结构分与当日分**方向一致**，不存在矛盾。
- **创新药**是唯一"结构分高、当日分低于中位数"的主题（SEOS #3 / Cur #30）→ 落在**象限 D（早期结构变化尚未反映在价格）**。这正是审计书 §二十二 强调的 Q1/Q3 分离价值：**SEOS 提前捕捉到了尚未在当日行情中体现的结构变化**。

完整 50 行诊断：`data/theme_current_strength_diagnostic_20260918.csv`（含 `diagnostic_only=True`、`weight_spec`、`ramp_spec`）；关键主题对照：`data/theme_key_marketfacts_20260918.csv`。

---

## 8. SEOS × Current Strength Quadrant（§十）

| 象限 | 定义 | 数量 | 含义 |
|---|---|---|---|
| **A** `STRUCTURAL_AND_CURRENT_RESONANCE` | SEOS ≥ 中位数 且 Cur ≥ 中位数 | **19** | 结构在改善，当日也在走强 → 共振 |
| **B** `CURRENT_STRONG_MATURE` | SEOS < 中位数 且 Cur ≥ 中位数 | **6** | 当日强，但结构改善已钝化（含 军工 #30/#11、电力 #34/#14、风电产业链 #43/#18） |
| **C** `DORMANT_WEAK` | SEOS < 中位数 且 Cur < 中位数 | **19** | 结构与当日双弱 |
| **D** `EARLY_STRUCTURAL_CHANGE` | SEOS ≥ 中位数 且 Cur < 中位数 | **6** | 结构已改善但当日未体现（含 创新药 #3/#30、美容护理 #8/#31、中药 #18/#43） |

**排名背离 Top 8**（|SEOS # − Cur #| 最大）：

| 主题 | SEOS # | Cur # | 背离 | SEOS | cur_strength | 象限 |
|---|---|---|---|---|---|---|
| 创新药 | 3 | 30 | +27 | 78.58 | 67.09 | D |
| 中药 | 18 | 43 | +25 | 71.16 | 57.14 | D |
| 风电产业链 | 43 | 18 | −25 | 59.77 | 72.80 | B |
| 美容护理 | 8 | 31 | +23 | 74.85 | 67.07 | D |
| 电力 | 34 | 14 | −20 | 63.03 | 73.72 | B |
| 军工 | 30 | 11 | −19 | 64.70 | 77.61 | B |
| 央国企基建 | 47 | 28 | −19 | 53.13 | 67.89 | C |
| 消费服务 | 29 | 46 | +17 | 65.12 | 53.69 | C |

**结论**：背离方向**双向存在**（既有"结构先于价格"的 D 象限，也有"价格先于结构"的 B 象限），说明两个维度**信息互补且不可互相替代**——这正是 §二十二 要求分离 Q1/Q3 的实证依据。

全量四象限清单：`data/theme_seos_current_quadrant_20260918.csv`。

---

## 9. Step 8 Ranking Semantic Audit（§十二 / §十三）

### 9.1 排序键是什么

**不是** `SEOS DESC`。`is_seos_desc = False`。

- 实际排序键：`theme_strength_score`（`post_market_review_build.build_theme`）
- 配置口径（`post_market_review_config.json` `strength_weights`）：

```json
{ "seos_score": 0.35, "core_breadth": 0.20, "breadth": 0.15,
  "relative_strength": 0.15, "amount_share_delta_3": 0.15 }
```

- 实现口径（`theme_strength()` 用 `row.get(col)` + `norm01`，缺值剔除该权重）：

| 主题 | 存储分 | 实现复算 | 设计复算 | 实现 wsum | 设计 wsum |
|---|---|---|---|---|---|
| 创新药 | 0.683347 | 0.683347 | 0.648467 | 0.85 | 1.00 |
| CXO | 0.682282 | 0.682282 | 0.647075 | **0.65** | 0.80 |
| 消费服务 | 0.674811 | 0.674811 | 0.649443 | 0.85 | 1.00 |
| 汽车及零部件 | 0.664602 | 0.664602 | 0.658215 | 0.85 | 1.00 |
| 生物制品 | 0.657661 | 0.657661 | 0.630376 | 0.85 | 1.00 |

**`impl_reproduce_ok = True`（`max_abs_delta = 0`）** → 实现口径已被精确复现，结论可信。

### 9.2 发现 1（P0）：`amount_share_delta_3` 键缺失 → 15% 权重被静默丢弃

- `build_theme` 写入 rows 的键为 `amount_share`（= `theme_amount_share`）与 `amount_share_change`（= `amount_share_delta_3`），**没有 `amount_share_delta_3` 键**。
- `theme_strength()` 用 `row.get("amount_share_delta_3")` → 恒为 `None` → `norm01` 返回 `None` → **该 15% 权重被剔除、`wsum` 从 1.00 缩到 0.85**。
- 证据：50 行中 `amount_share_delta_3` 全部为空（`dropped = True`），而 Step 4 同日该字段**有值**（如 汽车 `0.012202`、半导体 `0.035288`、AI算力 `0.033554`），四个共用键与 Step 4 同名字段**逐行完全一致（max delta = 0）**。
- 实际生效权重：`{seos .4375, core_breadth .25, breadth .1875, rs .1875}`（按 0.85 归一），**而非设计权重**。

**影响量化**（若补上该键，`theme_strength_score` 排名变化）：

| 主题 | 实现排名 | 设计排名 | 位移 |
|---|---|---|---|
| 半导体 | 29 | 18 | **−11** |
| AI算力 | 23 | 14 | **−9** |
| 农业周期 | 19 | 25 | +6 |
| 交通运输 | 20 | 24 | +4 |
| 汽车及零部件 | 4 | **1** | −3 |
| 通信 | 25 | 22 | −3 |

→ 该键缺失**显著改变主题排序**，属真实聚合缺陷，非无关紧要。

### 9.3 发现 2（P1）：`seos_change` 字段被错标

- `post_market_review_build.py:760`：`"seos_change": fnum(r.get("theme_health_delta_1"))`
- 审计校验：50 行 `seos_change == health_change` **全部成立**（`seos_change_equals_health_change = True`）。
- 报告主题表的「今日变化」列因此显示的是 **Health 1 日变化**，被读者当作 **SEOS 变化**。例：汽车 `seos_change = −7.12` 实为 health 日变化，而当日 SEOS 实际是 **+14.22**（66.37 → 80.59）。**这是用户判断"SEOS 排名可疑"的直接误导源之一。**

### 9.4 发现 3（P1）：报告未标注排序键、无当日强弱列

- `output/post_market_review_20260918.md` 主题表表头：
  `| 主题 | Phase（前→后） | SEOS | Health | Breadth | Core Breadth | Amount Share | 今日变化 | 分组 |`
- 表内 `SEOS` 列**不单调**（创新药 62.22、CXO 61.61、消费服务 59.80、汽车 66.37…），因为排序键是复合分。
- **读者默认会按 SEOS 读表** → 必然得出"系统按 SEOS 排名却排错了"的错误结论。
- 注意：该表的 `SEOS` 列取的是 **theme_date = 20260917** 的 SEOS（创新药 62.22、汽车 66.37），而审计对象 Step 4 的 20260918 SEOS（汽车 80.59）**不在该表中**；两个日期同日不同源，报告亦未标注 `theme_date`。

### 9.5 发现 4（P1）：7/50 主题 `core_breadth` 结构性缺失

- `data/sector_breadth.csv` 20260917：**CXO、新能源汽车、AI应用、储能与电力设备、建筑装饰、金融科技、央国企基建** 的 `core_valid_count = 0`、`primary_valid_count = 0`、`core_layer = ""` → `core_breadth` 为空。
- 后果 ①：Step 4 中 `seos_core_breadth`（20% 权重）对这 7 个主题退化为中性 **50.0**；
- 后果 ②：Step 8 中 `core_breadth = None` → `wsum` 由 0.85 进一步降到 **0.65**（`impl_wsum_values = [0.65, 0.85]`），再剔除 20% 权重。**CXO 因此以 `wsum = 0.65` 参与排序，却排到第 2 名**（其复算分 0.682282 是"仅用 65% 权重"的结果）。

### 9.6 §十二 SEMANTIC_RANKING_ERROR 判定

> **不成立。** Step 8 **没有**按 `SEOS DESC` 输出主题排名。排序键为 `theme_strength_score` 复合分，配置注释亦明示"禁止按单日涨幅排序（§十一）"。因此本项属 **§十三 展示/语义标注问题**，而非排序语义错误。

### 9.7 Phase 语义冲突审计（§十一 关联）

| 交易日 | STRONG | CONFIRMING | EMERGING | EARLY | DORMANT |
|---|---|---|---|---|---|
| 20260917 | 0 | 0 | 3 | 8 | 39 |
| 20260918 | **0** | 8 | 7 | 1 | 34 |

- **历史累计出现 STRONG 的天数 = 4**（`strong_lifetime_count = 4`）。
- 根因：`_ladder_target` 的 `lvl=4(STRONG)` 需同时满足 `seos ≥ 75`、`breadth ≥ 0.35`、`core_breadth ≥ 0.30`、`relative_strength ≥ 0`、**`volume_ratio_5 ≥ 1.00`**、`top5_concentration ≤ 0.60`。而 `volume_ratio_5` 在 20260917/20260918 **全为空**（P0-1）→ **该条件恒为 False** → **STRONG 态在近两日被数据缺口"锁死"**。
- 结论：Phase 状态机与 SEOS 存在**语义关联但不同源**（Phase 用 `seos + 绝对值广度 + volume_ratio`，SEOS 用 `delta`）；`volume_ratio` 缺口**同时**污染 SEOS 的 15%（量能参与）与 Phase 的最高一级，属 P0 数据/流程问题的双重影响。

---

## 10. Root Cause（§十六 A–G 分类）

| 代码 | 结论 | 证据 |
|---|---|---|
| A. DATA_ERROR | **成立** | ① `sector_volume.csv` 20260917/20260918 的 `volume_ratio_1/3/5` + `sector_amount_share_change_5d/20d` 两日 50/50 全空（Step 3 单日增量回放，`replay_dates = dates_all[-history_days:]` 仅 1 日 → `shift(1).rolling(N)` 结构性 NA）；② 7/50 主题 `core_layer` 为空致 `core_breadth` 缺失（Step 3 分层） |
| B. FIELD_SEMANTIC_ERROR | **成立** | `seos_change` 被赋为 `theme_health_delta_1`（`post_market_review_build.py:760`），50/50 行成立；另有 SEOS / Health / State 语义在展示与文档中混用（审计书 §五 举例本身即把 `relative_strength` 标为 `core_breadth`） |
| C. AGGREGATION_ERROR | **成立** | `build_theme` rows 缺 `amount_share_delta_3` 键 → Step 8 排序 15% 权重被静默剔除，`wsum` 0.85（或 0.65） |
| D. NORMAL_SEOS_BEHAVIOR | **成立（主因）** | 汽车 80.59 第 1、AI算力 76.33 第 6 均可由七分项加权**精确闭合复算**（差值校验 0 残差），且七分项无当日收益混入 |
| E. STEP4_SEMANTIC_DESIGN_PROBLEM | **部分成立（设计取舍，非缺陷）** | 单一 `seos_score` 被同时用于回答 Q1/Q2/Q3；`health_momentum`、`volume_participation` 在 Top 段接近饱和、区分度弱 |
| F. STEP8_RANKING_DISPLAY_ERROR | **成立** | 报告主题表未标注排序键、未提供当日强弱列、未标注 `theme_date`，致读者误读为"按 SEOS 排名" |
| G. MULTIPLE_ISSUES | **总体判定** | D 为主因 + C/B/F/A 叠加，**非单一问题** |

**关键负面结论（用于驳斥"改模型"的冲动）**：
- 汽车 / AI算力 / 机器人 / 创新药 的 SEOS 分数与排名**在数学上完全正确**（残差 0）；
- **不存在**公式错误、**不存在**字段取错分项、**不存在** SEOS DESC 排序错误；
- 因此**禁止**以"让排名更符合当天市场"为由修改 SEOS 权重、加入当日收益或加日期特判（§十四）。

---

## 11. Proposed Minimal Fix（§十七 / §十八：先语义 → 再数据 → 再接口 → 最后模型）

> 以下均为**建议**，本次审计**未实施任何修改**（`implemented = False`）。修复顺序遵循 §十八 优先级。

| 优先级 | ID | 类型 | 问题 | 最小修复方案 | 涉及文件 |
|---|---|---|---|---|---|
| **P0** | P0-2 | C 聚合 | Step 8 排序 `amount_share_delta_3` 键缺失，15% 权重静默丢弃 | 在 `build_theme` 的 rows 中**补写 `amount_share_delta_3`**（直接取 Step 4 同名字段；Step 4 已有该值，仅未透传）；同时当 `wsum < Σw` 时输出 WARN 日志，禁止静默剔除 | `post_market_review_build.py` |
| **P0** | P0-1 | A 数据/流程 | Step 3 单日增量回放致 `volume_ratio_*` 与 `amount_share_change_5d/20d` 全空 | Step 3 以 `--full` 重算足够回放窗口（≥ 6 日）后重跑 Step 4；或对 `shift(1).rolling(N)` 的窗口不足场景显式报错而非静默置空。**不改 SEOS 权重** | `sector_state_build.py`（运行方式），`sector_seos_build.py`（无需改） |
| **P1** | P1-1 | B 字段语义 | `seos_change` 被赋为 health 变化 | 改为 `seos_score(D) − seos_score(D−1)`；如需保留 health 变化，另起 `health_change_1` 字段名 | `post_market_review_build.py:760` |
| **P1** | P1-4 | A 数据 | 7/50 主题 `core_layer` 为空 | 核查这 7 个主题的 CORE/PRIMARY 分层成员（membership 置信度阈值 / 分层规则），修正分层或明确"无核心层"降级规则并标注 | `sector_state_build.py` + `sector_membership.csv` |
| **P1** | P1-2 | F 展示 | 报告主题表未标注排序键、无当日强弱列 | 表头显式标注「排序键：theme_strength_score（复合，见 §9.1）」+ 标注 `theme_date`；并列增加 `Cur Strength` 列（诊断维度） | `post_market_review_build.py`（报告渲染） |
| **P1** | P1-3 | B 语义 | SEOS / Theme Health / State 混用无说明 | 在报告表头或配置注释中明确三列定义与差异；文档中同步更正"核心广度 vs 相对强度"的举例错误 | 文档 + 配置注释 |
| **P2** | P2-1 | 展示 | 主题层 `theme_date=20260917` ≠ 报告日期 `20260918` | 报告表头标注 `theme_date`（属既有设计，不改取值逻辑） | `post_market_review_build.py` |
| **P3** | P3-1 | E 设计（长期） | 单一 SEOS 承担三问 | 保留 SEOS 回答 Q1；报告并列 **Theme Health**（Q2）与 **Current-Day Strength 诊断**（Q3）。本次仅提供 diagnostic CSV，**不接入生产** | 需产品决策 |

**明确禁止（§十四，本次审计确认未触碰）**：
- ❌ 未修改任何 SEOS 权重 / `component_weights` / `ramps`
- ❌ 未加入当日收益到 SEOS
- ❌ 未添加 `if date == 20260918` 类特判
- ❌ 未改 `theme_master`、未改 Step 1–3 代码
- ❌ 未人工降权任何主题

---

## 12. Regression Requirements（§十九）

任何 P0/P1 修复实施后，必须通过以下回归校验（本次审计脚本已内置前 4 项）：

| # | 回归项 | 通过标准 |
|---|---|---|
| R1 | SEOS 公式一致性 | `SEOS_RECONCILIATION_PASS`：`max_abs_raw_delta < 1e-9` 且 `max_abs_score_delta < 1e-9` 且 `max_abs_penalty_delta < 1e-9` |
| R2 | 排序键实现复现 | `impl_reproduce_ok = True`（`max_abs_delta = 0`），且 `wsum` 值域收敛为单一值（修复 P0-2、P1-4 后应为 `{1.0}`） |
| R3 | 字段语义 | `seos_change_equals_health_change = False`（修复 P1-1），且 `seos_change == seos_score(D) − seos_score(D−1)` 逐行成立 |
| R4 | 数据完整性 | `volume_ratio_5` / `sector_amount_share_change_5d/20d` 在 `D` 与 `D−1` 的缺失率 = 0（修复 P0-1）；`core_breadth` 缺失主题数 = 0（修复 P1-4） |
| R5 | SEOS 分数不得漂移（除 P0-1 修复日外） | P1（语义/展示）修复后，**同一交易日的 `seos_score` 必须逐位不变**；仅 P0-1（补 volume_ratio）可改变分数，且须给出全 50 主题新旧对照 |
| R6 | 排序稳定性 | P1 修复（不改排序逻辑）后，Step 8 主题顺序**必须逐位不变**；P0-2 修复后必须输出新旧排名位移清单供人工确认 |
| R7 | Phase 状态回归 | 修复 P0-1 后 `STRONG` 计数应从 0 恢复；`strong_lifetime_count` 应显著上升，且不得出现单日全主题 STRONG |
| R8 | 未来函数检验 | 修复后 Step 3 回放窗口 ≥ 6 日，日志不再出现"回放窗口过短"告警 |

---

## 13. Final Decision（§十三）

| 决策项 | 结论 |
|---|---|
| **KEEP**（保留 SEOS 模型） | ✅ **成立**。七分项定义与实现一致、量纲统一、无当日收益混入；汽车第 1 为数学正确结果 |
| **FIX_FIELD** | ✅ **需要**。`seos_change` → 真 SEOS 1 日变化（P1-1）；同步更正文档中 `core_breadth` / `relative_strength` 语义混用（P1-3） |
| **FIX_AGGREGATION** | ✅ **需要**。`build_theme` 补写 `amount_share_delta_3`（P0-2）；禁止 `wsum` 静默收缩 |
| **FIX_STEP8_DISPLAY** | ✅ **需要**。标注排序键与 `theme_date`；并列 Current-Day Strength（P1-2） |
| **FIX_PHASE** | ✅ **需要**（间接）。`volume_ratio_5` 缺口使 STRONG 锁死 → 修 P0-1 后回归（P0-1 / §11） |
| **RECONSIDER_SEOS** | ❌ **不需要**。无证据支持重构 SEOS 公式或调整权重 |
| **禁止事项确认** | 未改权重 / 未加当日收益 / 未加日期特判 / 未改 theme_master / 未改 Step 1–3 代码 / 未实施任何修复 |
| **阶段状态** | `AUDIT_ONLY` —— 等待用户确认后再进入修复阶段 |

### 13.1 对本系统三个问题的最终回答（§二十二）

系统必须明确区分并分别回答以下三个**不同**问题，**不得再用一个 SEOS 分数同时回答三者**：

| 问题 | 应由谁回答 | 20260918 取值示例 |
|---|---|---|
| **Q1 什么主题正在发生结构变化？** | **Step 4 `seos_score`** | 汽车及零部件 80.59（结构改善最强，非当日最强） |
| **Q2 什么主题当前健康强势？** | **Step 3 `theme_health` / Theme State** | 需按 `theme_health` 绝对水平排序（本次未展开，属 Q2 职责） |
| **Q3 今天市场实际交易了什么？** | **Step 8 报告中的当日强弱维度**（本次审计提供 `cur_strength` 诊断） | 半导体 94.95 / AI算力 89.34 / 消费电子 89.24 / 通信 89.16（当日真正被交易的算力+电子链） |

**本次审计对 Q3 的实证贡献**：汽车 SEOS 第 1 但当日强弱仅第 7；AI算力 SEOS 第 6 但当日强弱第 2；创新药 SEOS 第 3 但当日强弱第 30。**三者排名互不相同且各自正确** —— 这证明系统需要的不是"修 SEOS"，而是**把三个问题分开展示**。

---

## 附：本次审计产出文件清单（§二十）

| 文件 | 说明 |
|---|---|
| `output/seos_step4_step8_audit_20260918.md` | 本报告（13 节） |
| `output/seos_step4_step8_audit_20260918.json` | 机器可读审计结果（reconciliation / definition_audit / field_semantic_audit / attribution_top15 / auto_vs_robot / ai_vs_auto / key_themes_market_facts / member_level_stats / current_strength_diagnostic / step8_ranking_audit / seos_vs_current_rank_divergence / phase_audit / root_cause / final_decision / findings） |
| `data/seos_component_attribution_20260918.csv` | Top 15 分项归因（含全部子项原始特征与贡献分解） |
| `data/theme_current_strength_diagnostic_20260918.csv` | 50 主题当日强弱诊断（`diagnostic_only=True`） |
| `data/theme_seos_current_quadrant_20260918.csv` | 50 主题四象限分类 |
| `data/theme_key_marketfacts_20260918.csv` | 关键主题市场事实对照表（§十五，附加产物） |
| `audit_seos_step4_step8.py` | 审计执行脚本（只读复算，未修改任何生产文件） |

> 未生成 `output/seos_step4_step8_fix_plan_20260918.md`：修复方案已完整纳入本报告 §11 / §12，无需单独文件（如需拆分可另行生成）。
