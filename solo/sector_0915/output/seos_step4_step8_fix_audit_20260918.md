# Step 4 + Step 8 修复审计

> 复盘日 20260918 ｜ Step 4 落盘末日 20260918 ｜ Step 8 theme_date 20260917
> 范围：Step 4 数据完整性 + 字段语义；Step 8 展示/解释。**未修改任何 SEOS 权重**，未进入 Step 6。

## 1. 修复摘要

| 编号 | 问题 | 修复动作 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| P0-01 | volume_participation 数据缺口导致 50 个主题固定 50.00 | Step 3 `--date` 回放窗口下限（REPLAY_MIN_DAYS=21）+ Step 4 分项缺值按可用权重归一化、不再中性填充 | TEST_VOLUME_COVERAGE | PASS |
| P0-02 | amount_share_delta_3 字段缺失，Amount Share 组件未完全执行 | Step 8 `build_theme` 键名与 `strength_weights` / Step 4 上游字段对齐，15% 权重不再被静默丢弃 | TEST_AMOUNT_SHARE_DELTA_3 | PASS |
| P1-01 | 部分主题 core_breadth / core_breadth_delta 数据缺失 | 新增 `core_breadth_status` 四值域；无 CORE 成员时如实为空，不写 0/50 | TEST_CORE_BREADTH_SEMANTIC | PASS |
| P1-02 | seos_change 与主题展示字段语义混乱 | 废弃 `seos_change`，改为显式 `seos_score / seos_raw / seos_delta_1 / seos_delta_3 / seos_rank` | TEST_FIELD_LABEL_CONSISTENCY | PASS |
| P1-03 | Step 8 把 SEOS 误读成「当日主题强弱排名」 | §2 拆 A/B/C 三层 + `theme_interpretation` 六分类（Current Strength 仅诊断层） | TEST_STEP8_SEMANTIC | PASS |

附带确认：SEOS 权重冻结（PASS）、SEOS 可复算（PASS）、Step 5 回归（PASS）。

## 2. Volume 修复

**修复前**（`data/sector_seos_daily.csv.bak` @ 20260918）

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 有值主题数 | 50.000 | 50.000 |
| 缺值主题数 | 0.000 | 0.000 |
| 去重值个数 | 1.000 | 46.000 |
| 恰好=50.0 的主题数 | 50.000 | 0.000 |
| 最常见值占比 | 1.000 | 0.100 |
| p05 | 50.000 | 24.073 |
| 中位数 | 50.000 | 69.340 |
| p95 | 50.000 | 100.000 |
| min | 50.000 | 14.143 |
| max | 50.000 | 100.000 |

**上游量能链**（`data/sector_volume.csv`）

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| volume_ratio_1 缺值 | 50.000 | 0.000 |
| volume_ratio_3 缺值 | 50.000 | 0.000 |
| volume_ratio_5 缺值 | 50.000 | 0.000 |
| 成交额口径成员覆盖率 min | - | 0.946 |
| volume_data_status | "FIELD_ABSENT" | {"VALID": 50} |

**结论**：修复后 50 个主题 volume_participation 全部有值、去重值 46 个、无固定 50 fallback；修复前 50 个主题全部等于 50.0（唯一值 1 个）

## 3. Amount Share 修复

P0-02 的根因是 **Step 8 层的键名不匹配**：`build_theme` 写出 `amount_share_change`，而 `theme_strength` 查找 `amount_share_delta_3`，导致 15%% 权重被静默丢弃（而非上游缺字段）。

| 检查 | 结果 |
| --- | --- |
| component_weights.amount_share | {"amount_share_delta_5": 0.5, "amount_share_delta_10": 0.3, "amount_share_delta_3": 0.2} |
| 权重合计 | 1.0 |
| 独立重算行数 | 4500 |
| 最大偏差 | 0.00000100 |
| 超差行数 | 0 |
| Step 8 主题行有值 | 50/50 |
| Step 8 与 Step 4 不一致行 | 0 |
| 排序键定义 | 综合强度 theme_strength_score 降序（seos_score 0.35、core_breadth 0.2、breadth 0.15、relative_strength 0.15、amount_share_delta_3 0.15）；仅作内部排序，报告必须分三层展示 |

**结论**：amount_share_delta_3 可由 theme_amount_share 独立重算（最大偏差 0.00000100，超差 0 行）；Step 8 主题行透传 50/50；组件权重 {"amount_share_delta_5": 0.5, "amount_share_delta_10": 0.3, "amount_share_delta_3": 0.2}（合计 1.0，未被改动）

## 4. Core Breadth 修复

`core_breadth_status` 分布（20260918）：`{"CORE": 29, "PRIMARY_FALLBACK": 14, "NO_CORE_MEMBER": 7}`

**缺失主题及原因**（当期无 CORE 成员 → 结构缺失为上游事实，非计算缺陷）

| 主题 | sector_id | core_member_count | core_breadth |
| --- | --- | --- | --- |
| AI应用 | T06 | 0 | 如实为空 |
| 新能源汽车 | T07 | 0 | 如实为空 |
| 储能与电力设备 | T11 | 0 | 如实为空 |
| 金融科技 | T26 | 0 | 如实为空 |
| CXO | T38 | 0 | 如实为空 |
| 建筑装饰 | T46 | 0 | 如实为空 |
| 央国企基建 | T50 | 0 | 如实为空 |

| 分离性检查 | 结果 |
| --- | --- |
| 非法 status | 无 |
| NO_CORE_MEMBER 却写入 core_breadth | 0 行 |
| core_breadth_delta_3 独立重算行数 | 3870 |
| core_breadth_delta_3 最大偏差 | 0.00000100 |
| core_breadth_delta_3 超差行数 | 0 |
| 水平值 == 变化量 的行数（伪分离） | 1 |
| Step 4 与 Step 3 core_breadth 最大偏差 | 0.00000047 |

**结论**：core_breadth（水平，上游 sector_breadth 同源）与 core_breadth_delta_N（变化量）已分离：delta_3 独立重算最大偏差 0.00000100；NO_CORE_MEMBER 7 个主题的 core_breadth 如实为空（泄漏写入 0 行）

## 5. 字段语义修复

| 旧字段 | 问题 | 新字段 / 处理 |
| --- | --- | --- |
| `seos_change` | 实际被赋值为 `theme_health_delta_1`（语义错标） | 已移除；改为 `seos_score` / `seos_raw` / `seos_delta_1` / `seos_delta_3` / `seos_rank` |
| `amount_share_change` | 与 `strength_weights.amount_share_delta_3` 键名不一致，权重被丢弃 | 改为 `amount_share_delta_3`（与 Step 4 上游同名字段） |
| `rank`（Step 8 主题行） | 泛指「排名」，无法区分当日强弱 / 结构变化 | 改为 `theme_strength_rank`（综合）+ `current_strength_rank`（当日）+ `seos_rank`（结构，来自 Step 4） |
| 主题 row 的 health/breadth 变化 | 命名歧义 | 统一为 `*_delta_N` |

| 扫描目标 | 结果 |
| --- | --- |
| data/sector_seos_daily.csv（112 字段） | 已扫描 |
| data/sector_signal_flags.csv（19 字段） | 已扫描 |
| output/post_market_review_20260918.json :: theme.rows（70 字段） | 已扫描 |

- 命中 legacy / 歧义字段名：无
- Step 8 必需字段缺失：无
- `seos_score == theme_health_delta_1` 行数：0
- Markdown 残留 legacy 词：无；裸 `rank` 列：False

**结论**：CSV / JSON / Markdown 三层字段名与实际含义一致：无 seos_change / amount_share_change 等歧义字段，Step 8 主题行不再存在裸 rank；seos_score 不再等于 theme_health_delta_1；output/sector_seos_today.json 已带 seos_weights_frozen 声明

## 6. SEOS 权重

| 分项 | 设计权重 | config 权重 | 相等 |
| --- | --- | --- | --- |
| breadth_expansion | 0.25 | 0.25 | True |
| core_breadth | 0.20 | 0.2 | True |
| relative_strength | 0.15 | 0.15 | True |
| volume_participation | 0.15 | 0.15 | True |
| amount_share | 0.10 | 0.1 | True |
| health_momentum | 0.10 | 0.1 | True |
| consistency | 0.05 | 0.05 | True |

- `WEIGHT_IMPLEMENTATION_MISMATCH`：**否**（未出现）
- 冻结声明 `sector_seos_build.py::SEOS_WEIGHTS_FROZEN` = True
- 冻结声明 `output/sector_seos_today.json::seos_weights_frozen` = True
- 禁止模式扫描（按主题名调整 / `seos *=` / `seos +=` / `SEOS += current_strength` / `fillna(50)`）：
  - theme_named_adjust：未命中
  - seos_multiplicative：未命中
  - seos_additive：未命中
  - seos_plus_current_strength：未命中
  - neutral_fill_literal：未命中

**结论**：SEOS_WEIGHTS_FROZEN 声明存在（代码 + output/sector_seos_today.json）；7 项权重与设计值逐一相等；未发现按主题名强行调整 / seos *= / seos += / fillna(50) 等模式

## 7. 20260918 前后对比

### 7.1 Top 15（theme_date=20260917，按修复后 SEOS 降序）

| # | 主题 | SEOS 前 | SEOS 后 | Δ | SEOS rank | Health | Current Strength | CS rank | Phase | Interpretation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | T38 CXO | 61.61 | 72.95 | +11.34 | 1 | 73.25 | 74.23 | 2 | DORMANT→DORMANT | STRUCTURAL_CURRENT_RESONANCE |
| 2 | T05 消费电子 | 66.05 | 69.97 | +3.92 | 2 | 55.49 | 52.42 | 24 | DORMANT→EMERGING | STRUCTURAL_CURRENT_RESONANCE |
| 3 | T15 汽车及零部件 | 66.37 | 69.34 | +2.96 | 3 | 61.25 | 72.80 | 3 | DORMANT→EMERGING | STRUCTURAL_CURRENT_RESONANCE |
| 4 | T39 医疗服务 | 60.67 | 68.49 | +7.82 | 4 | 68.35 | 59.07 | 18 | DORMANT→DORMANT | STRUCTURAL_CURRENT_RESONANCE |
| 5 | T33 美容护理 | 60.87 | 68.25 | +7.37 | 5 | 64.69 | 67.54 | 7 | DORMANT→DORMANT | STRUCTURAL_CURRENT_RESONANCE |
| 6 | T37 医疗器械 | 61.74 | 65.73 | +3.99 | 6 | 62.33 | 60.82 | 17 | DORMANT→EMERGING | STRUCTURAL_CURRENT_RESONANCE |
| 7 | T35 消费服务 | 59.80 | 65.13 | +5.33 | 7 | 56.97 | 61.85 | 15 | DORMANT→EMERGING | STRUCTURAL_CURRENT_RESONANCE |
| 8 | T36 创新药 | 62.22 | 64.90 | +2.67 | 8 | 70.28 | 74.92 | 1 | DORMANT→DORMANT | STRUCTURAL_CURRENT_RESONANCE |
| 9 | T12 机器人与自动化 | 62.05 | 64.85 | +2.80 | 9 | 54.50 | 69.78 | 6 | DORMANT→EARLY | STRUCTURAL_CURRENT_RESONANCE |
| 10 | T03 通信 | 60.46 | 64.67 | +4.21 | 10 | 48.21 | 41.03 | 35 | DORMANT→EARLY | EARLY_STRUCTURAL_CHANGE |
| 11 | T32 纺织服饰 | 60.86 | 64.22 | +3.36 | 11 | 61.14 | 64.30 | 10 | DORMANT→EARLY | STRUCTURAL_CURRENT_RESONANCE |
| 12 | T02 AI算力 | 60.24 | 64.01 | +3.77 | 12 | 49.19 | 47.29 | 28 | DORMANT→EARLY | EARLY_STRUCTURAL_CHANGE |
| 13 | T07 新能源汽车 | 57.77 | 62.72 | +4.95 | 13 | 53.35 | 62.97 | 11 | DORMANT→DORMANT | STRUCTURAL_CURRENT_RESONANCE |
| 14 | T01 半导体 | 58.13 | 61.23 | +3.10 | 14 | 48.59 | 41.88 | 34 | EMERGING→EMERGING | EARLY_STRUCTURAL_CHANGE |
| 15 | T28 地产链 | 56.72 | 60.35 | +3.63 | 15 | 57.84 | 62.37 | 14 | DORMANT→DORMANT | STRUCTURAL_CURRENT_RESONANCE |

全 50 主题 SEOS 变化：50 个发生变化；Δ min -6.3034 / median 2.1864 / max 11.3402。

### 7.2 §二十 重点主题诊断表（10 个）

| 主题 | SEOS | SEOS rank | Health | Phase | Current Strength | CS rank | Breadth | Core Breadth | Rel Strength | Volume Partic. | volume_data_status | amt_share Δ3 | Δ5 | Δ10 | Interpretation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T15 汽车及零部件 | 69.34 | 3 | 61.25 | DORMANT→EMERGING | 72.80 | 3 | 0.073 | 0.573 | 0.027 | 68.82 | VALID | 0.0122 | 0.0242 | 0.0363 | STRUCTURAL_CURRENT_RESONANCE |
| T12 机器人与自动化 | 64.85 | 9 | 54.50 | DORMANT→EARLY | 69.78 | 6 | -0.011 | 0.333 | 0.017 | 65.90 | VALID | 0.0019 | 0.0120 | 0.0004 | STRUCTURAL_CURRENT_RESONANCE |
| T36 创新药 | 64.90 | 8 | 70.28 | DORMANT→DORMANT | 74.92 | 1 | 0.526 | 0.587 | 0.021 | 65.00 | VALID | -0.0043 | -0.0026 | -0.0030 | STRUCTURAL_CURRENT_RESONANCE |
| T37 医疗器械 | 65.73 | 6 | 62.33 | DORMANT→EMERGING | 60.82 | 17 | 0.237 | 0.205 | 0.017 | 70.15 | VALID | 0.0011 | 0.0041 | 0.0045 | STRUCTURAL_CURRENT_RESONANCE |
| T09 光伏产业链 | 49.53 | 32 | 40.36 | DORMANT→DORMANT | 34.20 | 39 | -0.320 | -1.000 | 0.016 | 60.20 | VALID | 0.0074 | 0.0181 | 0.0261 | WEAKENING |
| T02 AI算力 | 64.01 | 12 | 49.19 | DORMANT→EARLY | 47.29 | 28 | -0.250 | -0.179 | 0.029 | 73.40 | VALID | 0.0335 | 0.0457 | 0.0648 | EARLY_STRUCTURAL_CHANGE |
| T39 医疗服务 | 68.49 | 4 | 68.35 | DORMANT→DORMANT | 59.07 | 18 | 0.396 | -0.071 | 0.021 | 98.88 | VALID | 0.0033 | 0.0057 | 0.0064 | STRUCTURAL_CURRENT_RESONANCE |
| T33 美容护理 | 68.25 | 5 | 64.69 | DORMANT→DORMANT | 67.54 | 7 | 0.403 | 0.440 | 0.005 | 88.38 | VALID | 0.0037 | 0.0018 | 0.0038 | STRUCTURAL_CURRENT_RESONANCE |
| T03 通信 | 64.67 | 10 | 48.21 | DORMANT→EARLY | 41.03 | 35 | -0.365 | -0.304 | 0.036 | 74.37 | VALID | 0.0228 | 0.0386 | 0.0670 | EARLY_STRUCTURAL_CHANGE |
| T05 消费电子 | 69.97 | 2 | 55.49 | DORMANT→EMERGING | 52.42 | 24 | -0.207 | 0.112 | 0.043 | 69.40 | VALID | 0.0053 | 0.0224 | 0.0236 | STRUCTURAL_CURRENT_RESONANCE |

### 7.3 §二十一 关键事实验证（不要求任何主题被强行挪动）

- 中位 SEOS = 54.191171；中位 Current Strength = 49.30545
- **汽车**：{"seos_score": 69.336137, "seos_rank": 3.0, "seos_above_median": true, "verdict": "SEOS 仍高于中位 → 结构变化信号确实强；未按主题名做任何人工加权，也未要求其退出第一"}
  - SEOS 仍高于中位 → 结构变化信号确实强；未按主题名做任何人工加权，也未要求其退出第一
- **AI算力**：{"current_strength": 47.2885, "current_strength_rank": 28, "seos_score": 64.013558, "interpretation": "EARLY_STRUCTURAL_CHANGE", "current_above_median": false, "verdict": "Current Strength 未高于中位（rank 28）→ 当日并未走强，结构变化信号与当日强弱分离；SEOS 高于中位，说明当日强弱与结构变化不是同一问题"}
  - Current Strength 未高于中位（rank 28）→ 当日并未走强，结构变化信号与当日强弱分离；SEOS 高于中位，说明当日强弱与结构变化不是同一问题
- **创新药**：{"seos_score": 64.895148, "seos_above_median": true, "current_strength": 74.9184, "current_below_median": false, "interpretation": "STRUCTURAL_CURRENT_RESONANCE", "verdict": "SEOS 高于中位，当日强度不低于中位 → 归为 STRUCTURAL_CURRENT_RESONANCE；本次不满足 EARLY_STRUCTURAL_CHANGE 的三项前提（SEOS 高 + 当日强度相对低 + EARLY/EMERGING），故未使用该标签"}
  - SEOS 高于中位，当日强度不低于中位 → 归为 STRUCTURAL_CURRENT_RESONANCE；本次不满足 EARLY_STRUCTURAL_CHANGE 的三项前提（SEOS 高 + 当日强度相对低 + EARLY/EMERGING），故未使用该标签

结论：未使用任何 `if theme == ...: seos *= x` 形式的人工调整；主题顺序完全由冻结权重与上游数据决定。

## 8. Step 8 主题展示

| 层 | 排序口径 | 表内顺序校验 |
| --- | --- | --- |
| A. 当前主题表现 | current_strength DESC | 降序成立 |
| B. 结构变化 | SEOS DESC | 降序成立 |
| C. 主线状态 | theme_interpretation 分组 | 分组存在 |

- A 层 Top5：T36、T38、T15、T42、T22
- B 层 Top5：T38、T05、T15、T39、T33
- 两层 Top5 重合：T15、T38（说明「今天谁最强」与「谁在结构改善」不是同一个问题）
- A 层首行的 `seos_rank` = 8.0 → 当前主题表现排序 ≠ SEOS 排序
- 未标注 `diagnostic_only=true` 的行：无
- `theme_interpretation` 分布：{"STRUCTURAL_CURRENT_RESONANCE": 18, "CURRENT_STRONG_MATURE": 0, "EARLY_STRUCTURAL_CHANGE": 4, "DIVERGENCE": 10, "WEAKENING": 7, "DORMANT": 11, "UNDETERMINED": 0}
- 主题展示区 BUY 类措辞：无；EARLY_STRUCTURAL_CHANGE 免责声明：已存在

**结论**：A 层按 current_strength DESC（首行为 T36，其 seos_rank=8.0，说明当日表现排序 ≠ SEOS 排序）；B 层按 SEOS DESC；C 层按 theme_interpretation 分组；全部 50 行标注 diagnostic_only=true；主题展示区无 BUY 类措辞

## 9. Step 5 回归

> Step 5 依赖 Step 4。为保证「不因修复出现候选爆炸」，以 **非破坏性** 方式复算（`--no-write --validate`），不覆写落盘候选池（§二十六 / §三十二）。

| 指标 | 修复前 Step 4 | 修复后 Step 4 | 变化 |
| --- | --- | --- | --- |
| 评估行数 | 181885 | 184064 | 1.198% |
| 有效主题日 | [527, 40, 79] | [529, 42, 78] | - |
| candidate_score p50 | 61.4 | 61.3 | - |
| candidate_score p75 | 67.7 | 67.7 | - |
| candidate_score p90 | 73.0 | 73.1 | - |
| candidate_score max | 91.7 | 91.9 | - |
| CANDIDATE | 6082 | 5996 | -1.414% |
| WATCH | 34117 | 34050 | -67 |
| EXCLUDE | 141686 | 144018 | 2332 |
| 候选池 | 4964 | 4919 | -0.907% |
| 污染行 | 29784 | 30468 | - |

修复前 20260918 单日落盘切片（data/sector_stock_candidate_daily.csv @ trade_date=20260918（修复前口径，未重跑））：8301 行，其中 {"EXCLUDE": 6554, "WATCH": 1394, "CANDIDATE": 353}，候选类型 {"THEME_CORE": 242, "THEME_DIFFUSION": 75, "THEME_PULLBACK": 36}。

**结论**：修复后 Step 5 候选池 4964→4919（-0.907%）、CANDIDATE 6082→5996（-1.414%）、评估行 181885→184064（1.198%）；候选评分分位数基本持平（p50 61.4→61.3 / p90 73.0→73.1）；无候选爆炸

## 10. Validation

### 10.1 新增专项测试（§二十八）

| 测试 | 状态 | 结论 |
| --- | --- | --- |
| TEST_VOLUME_COVERAGE | PASS | 修复后 50 个主题 volume_participation 全部有值、去重值 46 个、无固定 50 fallback；修复前 50 个主题全部等于 50.0（唯一值 1 个） |
| TEST_AMOUNT_SHARE_DELTA_3 | PASS | amount_share_delta_3 可由 theme_amount_share 独立重算（最大偏差 0.00000100，超差 0 行）；Step 8 主题行透传 50/50；组件权重 {"amount_share_delta_5": 0.5, "amount_share_delta_10": 0.3, "amount_share_delta_3": 0.2}（合计 1.0，未被改动） |
| TEST_CORE_BREADTH_SEMANTIC | PASS | core_breadth（水平，上游 sector_breadth 同源）与 core_breadth_delta_N（变化量）已分离：delta_3 独立重算最大偏差 0.00000100；NO_CORE_MEMBER 7 个主题的 core_breadth 如实为空（泄漏写入 0 行） |
| TEST_FIELD_LABEL_CONSISTENCY | PASS | CSV / JSON / Markdown 三层字段名与实际含义一致：无 seos_change / amount_share_change 等歧义字段，Step 8 主题行不再存在裸 rank；seos_score 不再等于 theme_health_delta_1；output/sector_seos_today.json 已带 seos_weights_frozen 声明 |
| TEST_SEOS_RECONCILIATION | PASS | SEOS = Σ(wᵢ × seosᵢ) × (1 − min(penalty, 0.45)) 在浮点容差内成立：seos_raw 最大偏差 0.00000075，seos_score 最大偏差 0.00004442（4650 行） |
| TEST_STEP8_SEMANTIC | PASS | A 层按 current_strength DESC（首行为 T36，其 seos_rank=8.0，说明当日表现排序 ≠ SEOS 排序）；B 层按 SEOS DESC；C 层按 theme_interpretation 分组；全部 50 行标注 diagnostic_only=true；主题展示区无 BUY 类措辞 |

### 10.2 既有 regression（§二十七）

- Step 3 `sector_state_validation`：8/8 PASS
- Step 4 `seos_validation`：12/12 PASS
- Step 5 `sector_stock_candidate_validation`：27/27 PASS
- Step 8 `post_market_review validation`：10/11 PASS（1 WARNING）

| 复检项 | 对应检查 | 状态 |
| --- | --- | --- |
| NO FUTURE LEAKAGE | E_NO_LOOKAHEAD、CHECK_NO_LOOKAHEAD_SEOS、CHECK_NO_LOOKAHEAD_PHASE、CHECK_NO_LOOKAHEAD_ROTATION、CHECK_NO_FUTURE_PRICE、CHECK_NO_FUTURE_FUNDAMENTAL、FUTURE_LEAKAGE | PASS |
| NO LEGACY CONTAMINATION | CHECK_LEGACY_ISOLATION | PASS |
| PIT MEMBERSHIP | CHECK_MEMBERSHIP_PIT、B_MEMBERSHIP_UNIQUE、C_EFFECTIVE_DATE | PASS |
| PIT THEME RETURN | CHECK_THEME_RETURN_PIT | PASS |
| NO MOMENTUM CHASING | CHECK_EXTENSION_NOT_TOP1、CHECK_ROLE_NOT_BY_RETURN、CHECK_NO_DORMANT_CANDIDATE、CHECK_NO_TRADING_TERMS | PASS |
| POLLUTION ENFORCEMENT | CHECK_POLLUTION_ENFORCED、CHECK_POLLUTION_LOGGED | PASS |

### 10.3 关键测试指标

- `TEST_VOLUME_COVERAGE`：修复后 volume_participation 有值 50/50、去重值 46、恰好=50 命中 0
- `TEST_SEOS_RECONCILIATION`：seos_raw 最大偏差 0.00000075；seos_score 最大偏差 0.00004442（4650 行）
- `TEST_AMOUNT_SHARE_DELTA_3`：独立重算 4500 行，最大偏差 0.00000100
- `TEST_CORE_BREADTH_SEMANTIC`：NO_CORE_MEMBER 7 个主题如实为空

## 11. Residual Risks

| 编号 | 类型 | 说明 |
| --- | --- | --- |
| R1 | SCOPE | Step 6 / Step 7 / HVT / Execution 未随 Step 4 修复重跑（§二十六 禁止修改、§三十二 不进入 Step 6）。Step 5 的修复后复算以 --no-write 方式执行，落盘 data/sector_stock_candidate_daily.csv 仍为修复前口径；Step 8 报告中的结构池 / BUY / NO TRADE 片段因此来自修复前 Step 5→7 的落盘结果。 |
| R2 | DATA_COVERAGE | 7 个主题当期无 CORE 成员（core_breadth_status=NO_CORE_MEMBER），core_breadth 分项缺失，component_coverage<1 并标记 SEOS_DATA_PARTIAL。这是上游成员结构事实，非计算缺陷，未以 0/50 伪装。 |
| R3 | DATA_COVERAGE | volume_participation 依赖 Step 3 回放窗口长度（REPLAY_MIN_DAYS=21）。若以更短的 --date 窗口重跑 Step 3，仍可能复现 volume_ratio_* 缺值。当前 20260918 落盘为 VALID 50/50。 |
| R4 | OUTCOME | T+3 / T+5 / T+10 / T+20 尚未发生，Step 8 如实留空，不以前视数据填充。 |
| R5 | PRE_EXISTING | Step 8 REGIME_TIER_COVERAGE = WARNING（强势档在 Step 7 历史中未产出），与本次修复无关，未调整阈值。 |
| R6 | SEMANTIC | theme_interpretation 是解释标签，不是交易评分；EARLY_STRUCTURAL_CHANGE 只表示「结构变化值得继续观察」，报告已在 §2 末尾显式声明，仍存在被读者误读为 BUY 的可能（§十九）。 |

## 12. Final Status

| 通过条件 | 状态 |
| --- | --- |
| Volume 数据链 | PASS |
| Amount Share delta_3 | PASS |
| Core Breadth | PASS |
| Field Semantic | PASS |
| SEOS Reconciliation | PASS |
| SEOS 权重未改 | PASS |
| Step 8 Semantic | PASS |
| Step 5 Regression | PASS |
| Future Leakage | PASS |
| Legacy Isolation | PASS |

### SEOS_FIX_PASS

未满足项：无

> 本审计为只读结论；Step 8 输出仍为 Post-Market Review，不含 BUY / 推荐 / 目标价 / 仓位语义，Current Strength 仅作诊断层。执行到此停止，未进入 Step 6。
