# Step 1–5 核心质量问题修复审计报告

- **文件**：`output/step1_5_core_fix_audit.md`
- **范围**：仅 Step 1–5 质量修复（不进入 Step 6）
- **主改文件**：`sector_stock_candidate_build.py`（2615 行）、`config/sector_stock_candidate_config.json`（version 1.5 → **1.6**）
- **审计基线**：`output/theme_step1_5_full_audit.md`（上一轮只读审计，FINAL = CONDITIONAL_READY）
- **修改前 baseline 快照**：`_fix_baseline/{daily_before.csv, diffusion_before.csv, pollution_before.csv}`

---

## 0. 元信息与约束遵守

### 0.1 约束遵守对照（§二 / §二十六）

| 约束 | 遵守 | 证据 |
|---|---|---|
| 不进入 Step 6 | ✅ | 未创建任何 Step 6 文件；输出仍止于 `sector_stock_candidate_*` |
| 不新增 BUY / NO TRADE | ✅ | `CHECK_NO_TRADING_TERMS` PASS：候选内容层禁用词命中 `[]`；`not_a_buy_list=True` |
| 不改变 50 个 canonical themes | ✅ | `sector_master.json` 本任务未触碰；`data/sector_membership.csv` 仍为 50 主题 / 4995 股票 / 23908 行 |
| 不恢复或引用 legacy（theme_config / subtheme_map / theme） | ✅ | `CHECK_LEGACY_ISOLATION` PASS，`legacy 读取=[]`；详见 §7.3 |
| 不改变 Step 3 / Step 4 已通过的核心权重 | ✅ | Step 3/4 源码零改动；`sector_state_validation.csv` 8/8、`seos_validation.csv` 12/12 PASS |
| 不通过降低阈值解决覆盖率 | ✅ | 本次**只提高**门槛：`fundamental_min` 40→45、新增 `min_calibrated_opportunity_base=55`、新增污染封顶；无任何阈值下调 |
| 不用近期股价上涨定义主题机会 | ✅ | `Opportunity Calibration` 的乘子只由 `phase` 的 **T+5/T+10/T+20 PIT 滚动 alpha** 决定；`pullback_rules.rel_strength_stable_min_rel_ret_20` 仅作稳定性下限过滤，不产生加分 |
| 不通过扩大主题归属解决候选不足 | ✅ | membership 未增删一行；候选数 6541 → **5499**（下降，未扩张） |
| 不删除真实多主题 membership | ✅ | 京东方A 仍 18 主题、`>=6 主题` 仍 1717 只（与审计基线完全相同） |
| 不用未来收益参与当前 opportunity | ✅ | `CHECK_NO_FUTURE_PRICE` PASS；校准窗口强制 `signal_pos + gap <= D`（`min_gap_days=0`，前瞻终点严格 ≤ D） |
| 不针对单一历史阶段调参提高胜率 | ✅ | 新增阈值一次性固定，`change_log` v1.6 明确记载；回测仅用于验证 |

### 0.2 编号对照（本 Prompt 正文标题 ↔ 上一轮审计编号）

Prompt §一 的清单编号与正文标题编号存在 off-by-one，本报告**一律采用正文标题编号**，并给出对照，避免误读：

| 本报告编号（Prompt 正文标题） | 问题 | 上一轮审计编号 |
|---|---|---|
| **P0-01** | Step 5 主题层等权收益聚合缺 PIT membership 过滤 | P0-01 |
| **P0-02** | EMERGING 负 alpha 仍获 85 分机会基础分 | P0-02 |
| **P1-03** | `core_pattern` 无法识别「第二梯队刚开始改善」 | P1-04 |
| **P1-04** | THEME_PULLBACK 必须经过 Theme Opportunity Gate | P1-05 |
| **P1-05** | 解决跨主题污染 | P1-06 |
| （§十九 附带项） | `fundamental_min` 40 → 45 | P1-05 附带项 |

### 0.3 交付物

| 类型 | 路径 |
|---|---|
| 修改后代码 | `sector_stock_candidate_build.py`、`config/sector_stock_candidate_config.json` |
| 测试结果 | `output/sector_stock_candidate_validation.csv`（27/27 PASS）；`_fix_audit_bt.py` 的 A1/A3/E 单元测试 |
| 回测结果 | `output/_fix_audit_phase.csv`、`output/_fix_audit_diffusion.csv`、`output/_fix_audit_bt_rows.csv` |
| 本报告 | `output/step1_5_core_fix_audit.md` |
| 复核脚本 | `_fix_audit_bt.py`（A/B/C/D/E）、`_fix_regress.py`（反追涨 / 晚期伪启动）、`_fix_attrib.py`（候选变化归因） |

---

## 1. 修复清单

| 项 | 结论 |
|---|---|
| **P0-01** | **PASS** |
| **P0-02** | **PASS** |
| **P1-03** | **PASS** |
| **P1-04** | **PASS** |
| **P1-05** | **PASS** |
| §十九 `fundamental_min` 40→45 | **PASS** |

### 1.1 P0-01 — 主题层等权收益聚合 PIT 化 —— **PASS**

**修改点**：建立唯一 PIT 实现与唯一 accessor，Step 5 内两处「成员 → 主题层」聚合全部改经该层。

| 位置 | 函数 | 作用 |
|---|---|---|
| [L653-668](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L653-L668) | `pit_membership_mask(df, dates)` | PIT 判定**唯一实现**：`is_static=True` 或 `effective_date <= D`；若存在 `membership_end_date` 追加 `D < end` |
| [L671-673](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L671-L673) | `get_pit_membership(membership, date)` | 按 as-of D 取快照（供测试 / 审计） |
| [L676-678](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L676-L678) | `apply_pit_membership(pairs, date_col)` | 对含日期列的成员对批量过滤 |
| [L884-900](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L884-L900) | `compute_theme_layer` | `theme_ret_1/3/5/10/20`、`theme_member_valid` 全部由 `apply_pit_membership(panel_dates × 全量成员)` 产生 |
| [L1692-1696](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L1692-L1696) | `build_pipeline` | 个股层 `pairs` 同样经 `apply_pit_membership` |

**绕过路径扫描**（§三 特别检查，代码级）：
- `apply_pit_membership(` 调用点 = **2**（`compute_theme_layer` L890、`build_pipeline` L1696），扣掉函数定义后无第三处；
- `merge(membership` / `merge(mb` 直接绕过 accessor 的调用点 = **0**；
- `groupby(["trade_date","sector_id"])` 的全部主题层聚合均为上述 PIT 结果的**下游**；
- `pit_membership_mask` 定义 = 1 处（不存在多版本重复实现）。

**验证**：见 §3。

**残余风险（必须记录）**：当前 `data/sector_membership.csv` 为 **100% `is_static=true`、`effective_date` 全等于 `20260916`**（23908/23908），故 PIT 过滤对现有历史收益为**空转**（实测 `theme_ret_*` 变化 nz = 0）。本项是**口径正确性修复 + 未来污染守卫**，其数值生效依赖 P1-01（成员表版本序列）—— 见 §2.1。

### 1.2 P0-02 — EMERGING 不再天然获得 85 分 —— **PASS**

**修改点**：新增 Opportunity Calibration Layer，Step 4 SEOS 完全不动。

| 位置 | 函数 | 作用 |
|---|---|---|
| [L743-827](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L743-L827) | `compute_opportunity_calibration` | 按 (D, phase) 计算 **PIT 滚动 alpha**：`fwd_h(s) = ew_ret_h(s+h)`、`ex_h(s) = fwd_h − 当日全主题均值`、`alpha(D,p) = mean{ex_h(s) : phase(s)=p, pos(s)+gap ≤ pos(D), pos(s) ≥ pos(D)−lookback}` |
| [L830-852](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L830-L852) | `apply_opportunity_calibration` | `calibrated_opportunity_base = min(raw × mult, cap)`；CONFIRMING/STRONG 另加 breadth/core breadth/RS/health 支持门 |
| [L868-879](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L868-L879) | `compute_theme_layer` | `raw_opportunity_base` ← 配置 `raw_phase_base_scores`；`theme_opportunity_score = _opp(calibrated_opportunity_base)` |

**硬编码扫描（§七）**：源码中含 `EMERGING` 且含 `85` 的行 = **1**，且为注释
`# 用 signal_date < D 的滚动历史统计各 phase 的相对 alpha，禁用「EMERGING 天然 85」。`
即 **不存在 `EMERGING → 85` 的固定映射**；`85` 仅存在于配置 `raw_phase_base_scores.EMERGING`，作为**校准输入**，不再直接进入 `theme_opportunity_score`。

**落盘字段（§八-7）**：`calibrated_opportunity_base`、`calibration_status` 已写入候选明细；`calibration_alpha` / `calibration_sample_size` / `calibration_t_stat` / `phase_quality_multiplier` / `calibration_base_cap` 在主题层校准表内。

**验证**：见 §4（含 5 条合成单元测试全 PASS）。

### 1.3 P1-03 — 扩散形态新增 SECOND_LINE_EMERGING —— **PASS**

**修改点**：新增 `diffusion_pattern` / `diffusion_stage` 双字段，与既有绝对水平口径 `diffusion_type` **并存**（不改动原字段语义，避免破坏 Step 3/4 一致性）。

| 位置 | 内容 |
|---|---|
| [L937-947](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L937-L947) | 输入：`core_breadth_delta_3`、`primary_breadth_delta_3`、`secondary_breadth_delta_3`、`secondary_breadth_delta_5`、`core_breadth_delta_5` |
| [L949-955](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L949-L955) | 5 类 pattern 判定（LEADER_ONLY / CORE_EXPANSION / SECOND_LINE_EMERGING / FULL_BREADTH_EXPANSION / FAILED_DIFFUSION） |
| [L957-966](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L957-L966) | 掩码覆盖顺序 = 晚期/具体优先，`NO_DIFFUSION` 兜底；`diffusion_stage` 由 `pattern_stage_map` 映射 |

**§十 关键逻辑**：`SECOND_LINE_EMERGING = (core_up | core_stable) & (sec_emerging | prim_emerging) & (~p_full)`，
即「上层已稳定或改善 + 下层 delta_3 转正但 delta_5 尚未确认」，并被 `pattern_stage_map` 明确映射到 **`EARLY_DIFFUSION`**（而非 `FULL_DIFFUSION`）。实测 `SECOND_LINE_EMERGING` 100% 落在 `EARLY_DIFFUSION`（见 §5）。

**接线**：`classify_candidate_type` 的 `cond_second` 已并入 `diffusion_pattern`（[L1355-1360](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L1355-L1360)），使「第二梯队刚开始改善」不再被漏判。

**未改动项**：`diffusion_type`（旧枚举）与 `CHECK_DIFFUSION_IDENTIFIABLE` 语义保持不变；新增 `CHECK_DIFFUSION_PATTERN_ENUM` 校验新枚举合法性。

### 1.4 P1-04 — THEME_PULLBACK 接入 Theme Opportunity Gate —— **PASS**

**修改点**：[L1312-1376](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L1312-L1376) 的 `cond_pullback` 新增三项硬条件：

1. `calibrated_opportunity_base >= 55.0`（`pullback_rules.theme_opportunity_gate.min_calibrated_opportunity_base`）
2. `calibration_status ∈ {CALIBRATION_POSITIVE, CALIBRATION_INSUFFICIENT}`（`NONPOSITIVE` 不得进入）
3. `is_active_bucket == True`（`require_active_bucket`）

原链条要求的 `theme_phase ∈ {EARLY, EMERGING, CONFIRMING, STRONG}` 保留。

**验证**：
- AFTER `THEME_PULLBACK` 共 **988** 行，其中 `calibrated_opportunity_base < 55` 的行数 = **0**；
- AFTER `THEME_PULLBACK` 处于 risk 相位的行数 = **0**（与 BEFORE 同口径 0）；
- 全表 active 相位 124551 行中，`calibrated_base >= 55` 为 121200 行；`EARLY/EMERGING` 的 119053 行**全部 >= 55**（0 行被门槛拦下）。

**未放宽**：门槛值 55 为新增约束，无任何原有阈值下调；`fundamental_min` 同步由 40 提到 45（§十九）。

### 1.5 P1-05 — 跨主题污染：从「报告」升级为「执行约束」 —— **PASS**

**设计原则（严格按 §十四 / §十五 / §十六 / §十七）**：**membership ≠ candidate_theme**。不删除任何真实产业关系，只在候选层收敛「谁参与计分」。

| 位置 | 字段 / 逻辑 |
|---|---|
| [L1379-1452](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L1379-L1452) | `primary_theme_id`（定序：`membership_type > membership_confidence > membership_weight > industry_role > business_relevance > sector_id`）、`theme_rank_in_stock`、`candidate_theme_count`、`is_context_theme`、`primary_theme_confidence`、`cross_theme_pollution_flag`、`cross_theme_score_penalty`、`multi_theme_crowding_flag` |
| 配置 `cross_theme_bonus.primary_theme_selection` | `level_order=[CORE,PRIMARY,SECONDARY,THEMATIC,OBSERVATION]`；`forbidden_drivers=[recent_return, theme_strength, theme_heat]` |
| 配置 `context_theme_policy` | `PRIMARY_THEME=1`、`SECONDARY_THEME<=2`，rank > 3 的只作 context（`max_status=WATCH`） |
| [L1548-1558](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L1548-L1558) | 污染分级 ↔ 候选状态上限（执行约束） |

**扣分口径与状态口径分离**（避免过度惩罚）：
- **扣分**只由 `candidate_theme_count > max_active_candidate_themes(3)` 触发 → `cross_theme_score_penalty = 4.0`，并登记 `MULTI_THEME_POLLUTION`；全样本均值 **0.3993**、max 4.0、命中 16608 行。
- **状态上限**由 `pollution_levels` 决定，**不扣分**：`HIGH`（≥6 主题 或 `primary_theme_confidence < 0.80`）→ 封顶 `WATCH`；`MEDIUM`（4–5 主题 且 `confidence < 0.90`）→ 封顶 `WATCH`；`is_context_theme` → 封顶 `WATCH`。

**关键验证**：
- `HIGH` 污染股票×日 63082 个，其中 **CANDIDATE = 0**；
- CANDIDATE 行 5499 → 去重股票×日 5492，**压缩比 1.001**，单股票×日最多 2 行（5485 个为 1 行、7 个为 2 行）；
- CANDIDATE 中 `is_context_theme=True` = **0**；
- membership **未被修改**：`>=2 主题` 仍 4675 只（93.59%）、`>=6 主题` 仍 1717 只（34.37%）、max 仍 18（京东方A / 华工科技），与审计基线**逐位相同**。

### 1.6 §十九 fundamental_min 40 → 45 —— **PASS**

`config/sector_stock_candidate_config.json` 的 `pullback_rules.fundamental_min` 由 `40.0` 改为 **`45.0`**（与 `status_thresholds.candidate_fundamental_min=45.0` 对齐）。
验证：AFTER CANDIDATE 行 `fundamental_quality` 最小值 = **45.0000**。

---

## 2. 未修复问题（明确保留，不隐藏）

### 2.1 P1-01 · membership 为单份静态快照，无 as-of 版本序列 —— **未修复**

- **现状**：`data/sector_membership.csv` 23908 行 **全部** `is_static=true`、`effective_date=20260916`；无 `membership_end_date` 列。Step 3/4 仍为 `snapshot` 模式。
- **影响**：历史日成员由当期名单回推，已剔除 / 未纳入的股票被当作当日成员。
- **与本次修复的关系**：这正是 P0-01 数值空转（`theme_ret_*` 变化 nz=0）的根因。P0-01 已把**代码口径**改为 PIT 并加了守卫（合成泄漏测试 PASS），但**数据层仍无版本序列**，故守卫在当前数据上不会咬合。
- **修复建议**：建立按 `effective_date` 的版本序列；Step 3/4 切到 PIT 模式。

### 2.2 P1-06 · 污染检测只报告不回写 —— **已实质解决（本报告仍保留列出）**

- 按 Prompt §十六 要求，污染分级已从「报告」变为「执行约束」：`cross_theme_pollution_flag` 已直接参与 `assign_status` 的状态上限判定（[L1548-1558](file:///d:/mystock/solo/sector_0915/sector_stock_candidate_build.py#L1548-L1558)），并有 `CHECK_POLLUTION_ENFORCED`（PASS：污染行泄漏进 CANDIDATE = 0）与 `CHECK_POLLUTION_LOGGED`（PASS）双重校验。
- **但**：审计原始诉求是「在 **Step 3/4** 纳入数据质量扣分」，本次只落在 **Step 5**；Step 3/4 未改动（受「不改变 Step 3/4 已通过的核心权重」约束）。按用户指令，该项不作为本次阻塞项，**保留列出**。

### 2.3 P1-07 · 文档与实现不一致：THS 概念实际参与映射却被声明未采用 —— **未修复**

- **证据（本次复核）**：`sector_mapping_build.py` 中 `load_ths` 出现 **2** 次、`ths` 出现 **16** 次；该文件与 `output/sector_mapping_audit.md` 正文仍声称未采用 THS 概念。
- **影响**：数据来源真实性存疑。
- **修复建议**：如实声明使用范围，或删除 `load_ths`。

### 2.4 P1-08 · 存在死配置键 —— **未修复**

- **证据（本次复核，4 个脚本合计引用次数）**：

| 配置键 | step2 | step3 | step4 | step5 | 合计 |
|---|---|---|---|---|---|
| `precheck_min` | 0 | 0 | 0 | 0 | **0** |
| `conflict_role_cap` | 0 | 0 | 0 | 0 | **0** |
| `watch_crowding_max` | 0 | 0 | 0 | 0 | **0** |
| `candidate_max_crowding` | 0 | 0 | 0 | 0 | **0** |
| `early_theme_core` | 0 | 0 | 0 | 0 | **0** |
| `turnover_ratio_5_vs_20_max` | 0 | 0 | 0 | 0 | **0** |
| `exclude_status_rules` | 0 | 0 | 0 | 0 | **0** |

（`load_ths` = 2 / `ths` = 16 均只在 step2，属 P1-07。）
- **影响**：改配置不改行为，调参无效且误导维护。
- **修复建议**：逐项确认意图，接线或删除。

### 2.5 附：P1-09（审计编号 P1-10）· Step 5 声明的输入文件实际未读取 —— **未修复**

- `theme_seos_daily.csv` / `sector_opportunity_pool.json` 已在配置与文档中声明，实际数据主要来自 `data/` 面板与 `output/` 单日 JSON 组合。本次未改动输入接线（受最小改动约束）。保留列出。

---

## 3. PIT 验证（P0-01）

### 3.1 accessor 单元测试（合成动态成员）—— **PASS**

合成 4 名成员：`A`(static) / `B`(eff=20260701) / `C`(eff=20260601, end=20260701) / `D`(eff=20260101)

| D | 返回集合 | 期望 | 结果 |
|---|---|---|---|
| 20260630 | `{A, C, D}` | `{A, C, D}` | PASS |
| 20260715 | `{A, B, D}` | `{A, B, D}` | PASS |
| 20260601 | `{A, C, D}` | `{A, C, D}` | PASS |

即：**未来才生效的成员被排除**（B 在 20260630 不可见）、**已到期成员被排除**（C 在 20260715 不可见）、**静态成员恒可见**（A/D）。

### 3.2 泄漏测试 —— **PASS**

将真实成员表的 10% 改为「`is_static=False` 且 `effective_date=20260901`」，在 `D=20260630` 比较「全表口径」与「PIT 口径」的主题等权收益：

| 指标 | 值 |
|---|---|
| 受影响主题数 | 48 |
| `|diff| > 1e-9` 的主题数 | **32** |
| `mean|diff|` | 0.004763 |
| `max|diff|` | 0.047306 |

→ 过滤**不是空转**：一旦成员表出现「D 之后才生效」的条目，PIT 会真实改变历史主题收益。

### 3.3 before / after 历史一致性

| 指标 | BEFORE | AFTER | 差异 |
|---|---|---|---|
| `theme_ret_1` nz | — | — | **0**（max\|d\|=0.000e+00） |
| `theme_ret_5` nz | — | — | **0** |
| `breadth` nz | — | — | **0** |
| `theme_member_valid` 独立重算不一致 | — | — | **0 / 300** |

**结论**：在**当前数据**下历史主题收益**零变化**（因 membership 100% 静态，见 §2.1）；`CHECK_THEME_RETURN_PIT` 通过「独立重算」证明**同日个股层与主题层成员 universe 逻辑一致**（不一致 = 0/300）。

### 3.4 结构性扫描 —— **PASS**

`apply_pit_membership` 调用点 = 2（唯一 accessor 入口）；绕过 accessor 的 `merge(membership/mb)` = 0。

---

## 4. Opportunity Calibration（P0-02）

### 4.1 P2: 校准表（as-of D = 20260916）

| phase | raw_base | alpha | sample_size | t | status | mult | cap |
|---|---|---|---|---|---|---|---|
| EMERGING | 85 | **+0.01106** | 444 | +5.16 | `CALIBRATION_POSITIVE` | 1.00 | 100.0 |
| DORMANT | 0 | **−0.00112** | 10532 | −3.22 | **`CALIBRATION_NONPOSITIVE`** | 0.80 | **60.0** |
| DETERIORATING | 25 | +0.01537 | 253 | +5.59 | `CALIBRATION_POSITIVE` | 1.00 | 100.0 |

- **负 alpha 的相位（DORMANT）被封顶 60** —— 这是 §六「`phase_alpha <= 0` 则 `opportunity_base <= 60`」的现场生效证据。
- EMERGING 的 PIT 滚动 alpha 为 **正**（+1.106%，t=+5.16），故校准后乘子 1.00 —— 说明本项修复是**用 PIT 滚动质量替换硬编码**，而非无条件压分；若未来滚动 alpha 转负，同一代码路径会自动把它压到 ≤60。

### 4.2 落盘分布（候选明细，500 个有效主题日）

| 指标 | 值 |
|---|---|
| `calibrated_opportunity_base` count | 500 |
| mean / std | 68.548 / 26.947 |
| min / p25 / p50 / p75 / max | 10.5 / 30.0 / 85.0 / 90.0 / 90.0 |
| `calibration_status` | `CALIBRATION_POSITIVE` 456 / `CALIBRATION_INSUFFICIENT` 44 |

### 4.3 合成单元测试（§六 / §八 规则逐条断言）—— **5/5 PASS**

| 用例 | 期望 | 实测 | 结果 |
|---|---|---|---|
| alpha>0 且 mult=1.0 → 保持 raw | 85.0 | 85.0000 | PASS |
| alpha<=0（cap=60）→ base<=60 | 60.0 | 60.0000 | PASS |
| 样本不足（mult=0.7, cap=60）→ base<=60 | 59.5 | 59.5000 | PASS |
| STRONG 缺 breadth/RS/health 支持 → cap 65 | 65.0 | 65.0000 | PASS |
| CONFIRMING 缺支持 → cap 65 | 65.0 | 65.0000 | PASS |

### 4.4 相位前向收益（独立复权口径，PIT 成员，主题×日等权）

| phase | h | n | mean | median | win_rate | alpha_vs_random | rand_pct |
|---|---|---|---|---|---|---|---|
| EARLY | 3 | 195 | −0.0022 | −0.0018 | 0.4718 | −0.0012 | 0.4305 |
| EARLY | 20 | 152 | +0.0026 | +0.0186 | 0.5658 | +0.0156 | 0.4983 |
| **EMERGING** | 3 | 156 | −0.0063 | −0.0070 | 0.4359 | **+0.0026** | **0.5538** |
| **EMERGING** | 5 | 156 | −0.0036 | −0.0015 | 0.4936 | +0.0061 | 0.5895 |
| **EMERGING** | 10 | 156 | −0.0072 | +0.0043 | 0.5192 | +0.0192 | 0.6364 |
| **EMERGING** | 20 | 132 | +0.0089 | +0.0274 | 0.5909 | **+0.0259** | **0.6061** |
| CONFIRMING | 20 | 18 | +0.0223 | −0.0048 | 0.4444 | +0.0460 | 0.9357 |
| STRONG | 20 | 4 | −0.0340 | −0.0328 | 0.0000 | +0.0472 | 1.0000 |
| `CALIB>=55` | 20 | 294 | +0.0073 | +0.0224 | 0.5850 | +0.0211 | 0.5598 |

**EMERGING 一致性检查**：不再出现「alpha ≈ −X% 而 opportunity ≈ 85」的组合 —— 本样本 EMERGING 的 T+3 横截面超额 alpha 为 **正**（+0.26%），随机分位 **0.5538**（≈中位偏上），T+20 随机分位 0.6061。
⚠️ **口径差异声明（不掩饰）**：上一轮审计报告引用的「EMERGING T+3 随机分位 1.4%」来自 `output/seos_backtest.md` 的另一套样本与窗口；本报告采用**独立复权 + PIT 成员 + 主题×日等权**口径，两者不可直接相加比较。本报告的结论仅对本口径成立。

---

## 5. Diffusion（P1-03）

### 5.1 `diffusion_pattern` × `diffusion_stage` 交叉表（候选明细 166366 行）

| pattern | EARLY_DIFFUSION | FULL_DIFFUSION | FAILED_DIFFUSION | NO_DIFFUSION |
|---|---|---|---|---|
| LEADER_ONLY | 1327 | 0 | 0 | 0 |
| CORE_EXPANSION | 15852 | 0 | 0 | 0 |
| **SECOND_LINE_EMERGING** | **15767** | **0** | **0** | **0** |
| FULL_BREADTH_EXPANSION | 0 | 11247 | 0 | 0 |
| FAILED_DIFFUSION | 0 | 0 | 695 | 0 |
| NO_DIFFUSION | 0 | 0 | 0 | 121478 |

**§十 断言成立**：`SECOND_LINE_EMERGING` **100% 落在 `EARLY_DIFFUSION`**，与 `FULL_BREADTH_EXPANSION`（100% `FULL_DIFFUSION`）完全分离；`FAILED_DIFFUSION` 自成一档。

### 5.2 各 pattern 后续收益（主题×日，独立复权 + PIT）

| pattern | h | n | mean | median | win_rate | alpha_vs_random | rand_pct |
|---|---|---|---|---|---|---|---|
| LEADER_ONLY | 10 | 5 | +0.0258 | +0.0583 | 0.8000 | +0.0170 | 0.7028 |
| LEADER_ONLY | 20 | 5 | +0.0216 | +0.0442 | 0.8000 | +0.0189 | 0.3542 |
| CORE_EXPANSION | 20 | 53 | +0.0146 | +0.0297 | 0.6604 | +0.0318 | 0.7213 |
| **SECOND_LINE_EMERGING** | 20 | 30 | **−0.0087** | −0.0143 | 0.4667 | +0.0139 | 0.4723 |
| FULL_BREADTH_EXPANSION | 20 | 21 | +0.0006 | +0.0186 | 0.6190 | +0.0293 | 0.6773 |
| FAILED_DIFFUSION | 20 | 3 | −0.0369 | −0.0295 | 0.3333 | +0.0378 | 0.6667 |

**未做调参**：以上结果**未**用于反向调整任何阈值；`SECOND_LINE_EMERGING` 表现弱于 `CORE_EXPANSION`，但按 §二十-3「不要为了让某一个 pattern 胜率更高而调整 threshold」，阈值保持不动，如实记录。样本量（尤其 LEADER_ONLY n=5、FAILED_DIFFUSION n=3）不足以支撑统计结论，仅作结构验证。

---

## 6. Cross-theme（P1-05）

### 6.1 股票主题数分布（区分两个口径，避免误读）

| 口径 | n | `>=2 主题` | `>=6 主题` | max | 京东方A(000725.SZ) |
|---|---|---|---|---|---|
| **membership 口径**（`data/sector_membership.csv`，after） | 4995 只 | 4675（93.59%） | 1717（34.37%） | 18 | 18 主题 |
| membership 口径（上一轮审计基线） | 4995 只 | 4675（93.6%） | 1717（34.4%） | 18 | 18 主题 |
| **候选明细覆盖股票**（after） | 4929 只 | 4635（94.0%） | 1717（34.8%） | 18 | 12 主题（仅统计出现在候选层的 77 日中） |

→ **membership 层零变化**（符合 §十七「不得删除真实多主题 membership」）。污染治理发生在**候选层**，而非 membership 层。

### 6.2 候选层治理结果

| 指标 | 值 |
|---|---|
| `candidate_theme_count`（股票×日）mean / p50 / max | 1.4148 / 1 / 5 |
| `primary_theme_confidence` mean / p25 / p50 / p75 / max | 0.8767 / 0.80 / 0.90 / 0.99 / 0.99 |
| `is_context_theme=True` 且进入 CANDIDATE | **0** |
| CANDIDATE 行 / 去重股票×日 | 5499 / 5492（压缩比 **1.001**） |
| 单股票×日 CANDIDATE 行数分布 | 1 行：5485；2 行：7；≥3 行：**0** |
| `cross_theme_score_penalty` mean / max / 命中行 | 0.3993 / 4.0 / 16608 |
| `multi_theme_crowding_flag` 命中行 | 16608 |

### 6.3 污染分级 × 候选状态（执行约束生效证据）

| `cross_theme_pollution_flag` | CANDIDATE | WATCH | EXCLUDE |
|---|---|---|---|
| **HIGH**（≥6 主题 或 conf<0.80） | **0** | 5270 | 57812 |
| MEDIUM（4–5 主题 且 conf<0.90） | 977 | 5848 | 17585 |
| LOW（≤3 主题） | 3112 | 11538 | 15445 |

**§十六 断言成立**：`>=6 主题` 的股票×日**没有任何一个进入高等级 Candidate**（HIGH → CANDIDATE = 0）。

### 6.4 候选数量变化归因（6541 → 5499，−1042，−15.9%）

BEFORE CANDIDATE 的 6541 行逐行回溯 AFTER 状态：**全部降级为 WATCH**（无一行降为 EXCLUDE）。互斥归因（优先级 A>B>C>D>E>Z）：

| 归因 | 行数 | 占比 | before score 均值 | after score 均值 |
|---|---|---|---|---|
| **B. HIGH 污染封顶**（P1-05） | **997** | **95.7%** | 72.58 | 71.39 |
| E. 多主题重复计分惩罚（P1-05 扣分口径） | 2 | 0.2% | 62.93 | 58.48 |
| Z. 其他评分漂移（机会分校准 + bonus/penalty 变化） | 43 | 4.1% | 61.63 | 59.07 |
| A. context theme 封顶 | 0 | 0.0% | — | — |
| C. `calibrated_base < 55` 门槛 | 0 | 0.0% | — | — |
| D. `fundamental_min` 40→45 | 0 | 0.0% | — | — |

**结论**：候选减少**几乎全部来自跨主题污染封顶**（95.7%），**不是**通过门槛/提升 fundamental 硬性削减；且降级目标全部为 WATCH（进入观察，未丢弃）。**未通过降低任何阈值来扩大 coverage**（§二-6、§二十六）。

AFTER CANDIDATE 准入画像（证明未靠放宽扩量）：`calibrated_base min=45.5`、`fundamental_quality min=45.0000`、`candidate_theme_count max=5`、`is_context_theme=True` 0 行、污染分级仅 LOW(3850)/MEDIUM(1649)、`calibration_status` 仅 POSITIVE(5152)/INSUFFICIENT(347)。

---

## 7. 回归测试

### 7.1 Test A · 反追涨（§二十，口径已与审计基线对齐）

审计报告 §6.5 的「强主题」口径经反解确认为 `theme_phase ∈ {EMERGING, CONFIRMING, STRONG}`（该口径下 BEFORE `nA=221 / nB=681`，与审计报告完全一致），故对照使用同一口径。

| 样本 | 定义 | n | score 均值 | 中位 | CANDIDATE 占比 | ext_pen | 结构分 |
|---|---|---|---|---|---|---|---|
| BEFORE A | 强主题 + `ret_5≥30%` + `dist_ma20≥30%` | 221 | **44.38** | 41.47 | 0.0% | 19.85 | 92.26 |
| BEFORE B | 强主题 + CORE + `ret_5∈[2%,8%]` + `dist_ma20∈[0,10%]` + 量比∈[1,2] | 681 | **76.72** | 77.05 | 49.9% | 1.06 | 73.19 |
| **AFTER A** | 同上 | 221 | **43.63** | 40.47 | **0.0%** | 19.85 | 92.26 |
| **AFTER B** | 同上 | 681 | **76.15** | 76.51 | **45.4%** | 1.06 | 73.19 |

**差 (A − B)：BEFORE −32.34 → AFTER −32.51**（防线未弱化，略扩大）。
极端样本拦截：`ret_5≥25%` 共行中 CANDIDATE = **0**；`dist_ma20≥30%` 行中 CANDIDATE = **0**；Sample A 中 CANDIDATE = **0**。
→ **NO OBVIOUS MOMENTUM CHASING = YES**（未因本次修复回升）。

### 7.2 Test B · 晚期伪启动（§十四）

样本：`theme_phase ∈ {EARLY, EMERGING}` 主题日 `n = 352`（与审计基线 **完全一致**）。

| 条件 | 命中数 | 上一轮基线 |
|---|---|---|
| 5D ≥ 15% 且 20D ≥ 25% | 0 | 0 |
| `top5_concentration ≥ 0.60` | 0 | 0 |
| `core_breadth_delta_5 ≤ 0` | 169 | 169 |
| **三条件同时满足** | **0** | **0** |
| **四条件同时满足** | **0** | 0 |

→ **未因 Opportunity Calibration / Diffusion 修复重新引入晚期伪启动。**
补充：`EARLY/EMERGING` 个股×主题行 119053，`calibrated_opportunity_base` 非空 119053，`< 55` 的行数 = **0**；`calibration_status` = POSITIVE 113577 / INSUFFICIENT 5476。

### 7.3 Test C · Legacy 隔离（§二十二）

| 检查 | 结果 |
|---|---|
| `CHECK_LEGACY_ISOLATION` | **PASS**，`legacy 读取=[]` |
| `config.legacy_config_used` | `false`（不符则程序 `SystemExit`，L2516-2517） |
| `config.legacy_files_ignored` | `["theme_config.json","subtheme_map.json","theme.json","theme_master.json","theme_mapping.json"]` |
| 全项目 `.py` 中三个 legacy 文件名的出现上下文 | 全部为**注释**、**`LEGACY_NAMES` 常量**、**忽略清单**、**审计报告文案** —— **无一处 read / parse / import / open** |
| `theme_stock_candidate_build.py` | 文件不存在（映射为 `sector_stock_candidate_build.py`），无遗留 |
| `theme_seos_build.py` / `theme_state_build.py` / `sector_mapping_build.py` | 三处 legacy 名单均仅用于「存在性报告」，无读取 |

→ **FOUND_ONLY / WARNING_ONLY**，**NO LEGACY CONTAMINATION = YES**。

### 7.4 Test D · 未来泄漏

| 检查 | 结果 |
|---|---|
| `CHECK_NO_FUTURE_PRICE` | **PASS**（前向列 = []） |
| `CHECK_NO_FUTURE_FUNDAMENTAL` | **PASS**（`ann_date > trade_date` 行数 = 0） |
| `CHECK_MEMBERSHIP_PIT` | **PASS**（`effective_date > trade_date` 行数 = 0） |
| `CHECK_THEME_RETURN_PIT` | **PASS**（泄漏测试通过；独立重算不一致 0/300） |
| Opportunity Calibration 窗口 | `pos(s) + gap ≤ pos(D)` 且 `pos(s) ≥ pos(D) − lookback`，**前瞻终点严格 ≤ D** |
| Diffusion 特征 | `delta` 由 `shift(+n)` 构造，全部为 t 日及之前面板值 |

→ **NO FUTURE LEAKAGE = YES**。

### 7.5 全链路验证（§二十五-13）

| 阶段 | 验证文件 | 结果 |
|---|---|---|
| Step 2 | `output/sector_validation.csv` | **10 / 10 PASS** |
| Step 3 | `output/sector_state_validation.csv` | **8 / 8 PASS** |
| Step 4 | `output/seos_validation.csv` | **12 / 12 PASS** |
| Step 5 | `output/sector_stock_candidate_validation.csv` | **27 / 27 PASS** |
| 合计 | — | **57 / 57 PASS** |

Step 5 关键检查：`CHECK_THEME_IN_MASTER`（未知主题 []）、`CHECK_MEMBERSHIP_PIT`、`CHECK_THEME_RETURN_PIT`、`CHECK_DIFFUSION_PATTERN_ENUM`（非法形态/阶段 []）、`CHECK_POLLUTION_ENFORCED`（污染行泄漏进 CANDIDATE = 0）、`CHECK_CROSS_THEME_BONUS_CAP`（越界 0 / cap 5.0）、`CHECK_NO_DORMANT_CANDIDATE`（DORMANT/INVALID 入选 0）、`CHECK_NO_TRADING_TERMS`（`not_a_buy_list=True`）。

---

## 8. 最终状态

### 8.1 进入条件核对（§二十四）

| 条件 | 结论 | 依据 |
|---|---|---|
| P0-01 = PASS | ✅ | §1.1 / §3 |
| P0-02 = PASS | ✅ | §1.2 / §4 |
| P1-03 = PASS | ✅ | §1.3 / §5 |
| P1-04 = PASS | ✅ | §1.4 |
| P1-05 = PASS | ✅ | §1.5 / §6 |
| NO FUTURE LEAKAGE = YES | ✅ | §7.4 |
| NO LEGACY CONTAMINATION = YES | ✅ | §7.3 |
| NO SERIOUS THEME POLLUTION = YES | ✅ | §6.3（HIGH → CANDIDATE = 0；压缩比 1.001；单股票×日 ≤2 行） |
| NO OBVIOUS MOMENTUM CHASING = YES | ✅ | §7.1（A−B = −32.51） |

### 8.2 候选数量说明（禁止因数量减少放宽条件）

`CANDIDATE 6541 → 5499`（−15.9%）：
- 95.7% 来自跨主题污染封顶（§6.4），4.1% 来自评分漂移，0.2% 来自重复计分惩罚；
- 降级目标 **100% 为 WATCH**（未丢弃、未误判归属）；
- **未下调任何阈值**，反而新增 `calibrated_base ≥ 55` 门槛并把 `fundamental_min` 由 40 提到 45。

### 8.3 残余风险（不阻塞，但必须在 Step 6 设计时携带）

1. **P1-01 未修复** ⇒ P0-01 的 PIT 守卫在当前数据上空转（`theme_ret_*` 数值零变化）。Step 6 若使用历史主题收益做任何回归 / 归因，必须先补 membership 版本序列。
2. **P1-06 的 Step 3/4 部分未落地** ⇒ 污染只在 Step 5 被硬约束，Step 3/4 的主题强度仍可能被重复成员关系轻微放大。
3. **P1-07 / P1-08 / P1-09 未修复** ⇒ 数据来源声明与配置可维护性仍存瑕疵。
4. **样本量**：`LEADER_ONLY`（n=5）、`FAILED_DIFFUSION`（n=3）、`STRONG`（n=4）统计不可用；Diffusion 与 Calibration 的结论需更长窗口验证。
5. **拦截单点依赖**：反追涨仍依赖 `extension_penalty`（审计 P2-16 未修复），若 `max_penalty_points` 被下调，防线立即失效。

---

# **FINAL STATUS = `READY_FOR_STEP_6`**

六项修复（P0-01 / P0-02 / P1-03 / P1-04 / P1-05 + `fundamental_min` 45）全部 PASS，四项 YES/NO 门（未来泄漏 / Legacy 污染 / 主题污染 / 追涨）全部 YES，全链路验证 57/57 PASS。§8.3 列的 5 项残余风险均属**已知、已定位、非本次阻塞项**，建议作为 Step 6 的输入约束携带。

**未开始 Step 6。**
