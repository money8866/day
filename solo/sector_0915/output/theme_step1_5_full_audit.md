# A股主题量化系统 Step 1–5 全链路质量审计报告

```text
READ ONLY
AUDIT ONLY
NO CODE MODIFICATION
NO NEW FEATURES
NO STEP 6
```

| 项 | 内容 |
|---|---|
| 审计对象 | A股主题量化系统 Step 1 – Step 5 |
| 工作目录 | `d:\mystock\solo\sector_0915` |
| 审计日期 | 2026-09-17 |
| 审计性质 | 只读质量审计（Full Architecture & Quant Quality Audit） |
| 修改文件 | 仅新增 4 份审计产物于 `output/`；**未修改任何生产代码、配置、真源或数据** |
| 数据快照 | membership `effective_date=20260916`；SEOS `20260512~20260916`（91 日）；候选池 `20260522~20260916`（77 日） |

**术语映射**：规格中的 `theme_*` 对应项目中的 `sector_*`（`theme_master.json ≡ sector_master.json`；`theme_membership.csv ≡ data/sector_membership.csv`；`theme_id/theme_name ≡ sector_id/sector_name`）。本报告统一使用项目实际字段名，并在引用规格时标注章节号。

---

## 一、执行摘要

**FINAL STATUS：`CONDITIONAL_READY`**

系统架构完整、权重全部合规、未来函数自检通过、反追涨机制真实有效。但存在 **2 项 P0 级问题**（Step 5 主题层聚合缺 PIT 过滤；EMERGING 状态负 alpha 传导）与 **8 项 P1 级问题**（membership 静态快照、core_pattern 无法识别第二梯队改善、THEME_PULLBACK 无主题机会门槛、跨主题污染严重、污染检测不回写、文档实现不一致、死配置、Step 5 声明输入未接线）。

结论是「有条件就绪」：**先完成 P0 与 P1 修复项，再决定是否进入 Step 6**。规格 §四十九 明确——若只是局部问题，先列出 P0/P1 修复项，再决定是否进入 Step 6。

**四项 YES/NO 速览**：

| 问题 | 结论 |
|---|---|
| NO LEGACY CONTAMINATION | **YES** —— 四层脚本均只扫描路径不读内容；`legacy_config_used=false` 恒成立 |
| NO FUTURE LEAKAGE | **YES（含 1 处例外）** —— Step 3/4/5 的 `shift(-n)` 仅存在于回测分支；例外为 Step 5 主题层等权收益聚合缺 PIT 过滤（P0-01） |
| NO SERIOUS THEME POLLUTION | **NO** —— 93.6% 股票归属 ≥2 主题，34.4% 归属 ≥6 主题 |
| NO OBVIOUS MOMENTUM CHASING | **YES** —— 极端动量样本比健康样本低 32.34 分，极端样本 100% 被排除 |

---

## 二、审计范围与方法

### 2.1 审计对象清单

| 层 | 脚本 | 行数 | 产物 |
|---|---|---|---|
| Step 1 | `sector_master.json` | 961 | 无（人工维护真源） |
| Step 2 | `sector_mapping_build.py` | 2488 | `data/sector_membership.csv`（23908 行） |
| Step 3 | `sector_state_build.py` | 1451 | `data/sector_state_history.csv` |
| Step 4 | `sector_seos_build.py` | 1893 | `data/sector_seos_daily.csv`（4550 行 / 84 列） |
| Step 5 | `sector_stock_candidate_build.py` | 2241 | `data/sector_stock_candidate_daily.csv`（166366 行 / 60 列） |
| 配置 | `config/seos_config.json` | 239 | Step 4 参数 |
| 配置 | `config/sector_stock_candidate_config.json` | 400（v1.5） | Step 5 参数 |

### 2.2 审计维度（规格 §二）

不满足于「代码能否运行」，逐项检查：Architecture / Data Integrity / Semantic Correctness / Quant Logic / Statistical Validity / Backtest Validity / Future Leakage / Legacy Pollution / Theme Pollution / Overfitting / Robustness / Output Consistency。

### 2.3 方法

1. **静态代码审计**：4 个生产脚本全文逐行阅读（2488 + 1451 + 1893 + 2241 = 8073 行），建立 DESIGNED vs ACTUAL 依赖对照。
2. **量化验证**：编写一次性只读脚本 `_audit_quant.py`（16 节），对已落地产物做统计验证，覆盖规格 §二十五 – §三十八。脚本与产物用完即删，不触碰系统文件。
3. **回测产物复核**：复核 `output/seos_backtest.md` 与 `output/sector_stock_candidate_backtest.md`。

---

## 三、架构完整性图（逐层评级）

```
┌─────────────────────────────────────────────────────────────────┐
│ STEP 1  主题真源  sector_master.json (v2.0 / 50 主题)            │  WARN
│   ├ 字段命名与规格不符（缺 core_industries/core_products/…）      │
│   ├ mapping_priority / mapping_confidence 为死配置               │
│   └ 主题边界重叠：T07 ≡ T15（近乎重复定义）                       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 读取 ✓
┌──────────────────────────▼──────────────────────────────────────┐
│ STEP 2  映射构建  sector_mapping_build.py                        │  WARN
│   ├ membership 单份静态快照（effective_date 全 20260916）        │
│   ├ THS 概念实际参与映射但文档声称未采用（不一致）                │
│   ├ 污染检测只报告不回写                                          │
│   └ exclude_keywords 4 条因长度门槛永久失效                       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ data/sector_membership.csv (23908 行)
┌──────────────────────────▼──────────────────────────────────────┐
│ STEP 3  主题质量/状态  sector_state_build.py                     │  PASS*  WARN
│   ├ HEALTH_WEIGHTS 逐项合规 ✓   return share 0.28 ≤ 0.30 ✓       │
│   ├ 无 shift(-n) / pct_change / cumsum ✓                          │
│   ├ 物理截断重算自检 PASS ✓                                       │
│   └ 仅采用 snapshot 模式，无 as-of PIT（继承 Step 2 缺陷）        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ data/sector_state_history.csv
┌──────────────────────────▼──────────────────────────────────────┐
│ STEP 4  SEOS  sector_seos_build.py                               │  WARN
│   ├ score_weights 7 项逐项合规 ✓                                  │
│   ├ extension_penalty 真实存在（乘性扣减，上限 0.45）✓            │
│   ├ core_pattern 无法识别「第二梯队刚开始改善」                    │
│   ├ startup_quality 枚举与规格不符                                │
│   └ EMERGING 阶段负 alpha（T+3 随机分位仅 1.4%）                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ data/sector_seos_daily.csv (4550 行)
┌──────────────────────────▼──────────────────────────────────────┐
│ STEP 5  个股候选池  sector_stock_candidate_build.py              │  WARN
│   ├ candidate_weights 8 项逐项合规 ✓                              │
│   ├ cross_theme_bonus ≤ 5.0 ✓   theme-first 顺序 ✓                │
│   ├ PIT 财务按 ann_date 合规 ✓                                    │
│   ├ 【P0】主题层等权收益聚合缺 PIT 过滤                            │
│   ├ THEME_PULLBACK 无主题机会门槛 → 回测超额 -0.0331               │
│   └ 3 个声明输入未接线；7+ 个死配置键                              │
└─────────────────────────────────────────────────────────────────┘
```

逐层评级汇总：

| 层 | 评级 | 依据 |
|---|---|---|
| Step 1 主题真源 | **WARN** | 字段不全、死配置、边界重叠 |
| Step 2 映射构建 | **WARN** | 静态快照、文档不一致、污染不治理 |
| Step 3 主题状态 | **PASS / WARN** | 权重与未来函数 PASS；snapshot 模式继承 WARN |
| Step 4 SEOS | **WARN** | 权重与 penalty PASS；语义识别缺失 + EMERGING 负 alpha |
| Step 5 候选池 | **WARN** | 权重与顺序 PASS；PIT 不一致 + PULLBACK 缺陷 |

---

## 四、DESIGNED vs ACTUAL 依赖图

完整结构化依赖图见 `output/theme_step1_5_dependency_map.json`。核心差异如下：

| 项 | DESIGNED | ACTUAL | 判定 |
|---|---|---|---|
| Step 2 数据源 | 申万行业 + 东财概念/行业 | 另加 **THS 同花顺概念**（L788/L829-832） | **ARCHITECTURE VIOLATION**（文档未声明） |
| Step 5 输入 | 声明 6 个（含 `sector_state_history.csv`、`sector_seos_today.json`、`sector_opportunity_pool.json`） | 实际只用 3 个，主题机会全部由 `sector_seos_daily.csv` 重算 | **DEAD DECLARATION** |
| membership PIT | 应有 as-of 版本序列 | 单份快照（全部 `effective_date=20260916`、`is_static=true`） | **ARCHITECTURE VIOLATION** |
| Step 5 成员口径 | 应全局一致 | 个股层 PIT（L1346-1350）与主题层全表（L651-663）不一致 | **ARCHITECTURE VIOLATION** |
| legacy 配置 | 不使用 | 未读取（仅报告路径），但上级目录存在 6 个同名文件 | 读取层面 PASS |

---

## 五、分项审计

### 5.1 Step 1：主题真源（`sector_master.json`）

- `version=2.0`；50 个主题 T01–T50；`sector_id` 唯一完整，无重复、缺失、孤儿。
- 分组完整：R01 科技成长（T01–T06）/ R02 先进制造（T07–T15）/ R03 资源周期（T16–T22）/ R04 金融地产（T23–T28）/ R05 大消费（T29–T35）/ R06 医药医疗（T36–T42）/ R07 公用防御（T43–T45）/ R08 政策基建（T46–T50）。
- **每个 sector 仅 11 个字段**：`sector_id / sector_name / rotation_group / sector_type / aliases / subsectors / eastmoney_industry_keywords / eastmoney_concept_keywords / keywords / exclude_keywords / thesis`。规格要求的 `core_industries / core_products / core_companies / description` 均不存在（`core_companies` 仅 T10 有，L203-216）。
- `exclude_keywords` 50 项中 **39 项为空数组**。
- 边界重叠：T07 ind_kw（L168）与 T15 ind_kw（L281）内容近乎完全重复；T02/T03 共用「通信设备」、T04/T06 共用「软件开发」、T38/T39 共用「医疗服务」、T36/T42 共用「生物制品/生物药」、T10/T11 共用「电网设备」。

**实测后果**：核心成员占比为 0.0% 的主题有 T50、T07、T06、T11、T26、T46、T38（共 7 个），其中 T07/T06/T11/T50 直接源于边界重叠导致只能判 SECONDARY。

### 5.2 Step 2：映射与成员构建

- `mapping_method` 6 值：`DIRECT_INDUSTRY / INDUSTRY_PRODUCT / INDUSTRY_CONCEPT / CONCEPT / KEYWORD / UNMAPPED`（L913-920）。
- `membership_type` 5 值：`CORE(4) > PRIMARY(3) > SECONDARY(2) > THEMATIC(1) > OBSERVATION(0)`；**实测 OBSERVATION 全 0**。
- `membership_confidence`（L1171-1191）：`KIND_BASE`（0.55~1.00）→ `exclude_hit −0.30` → `core_company_coverage≥0.50 +0.05` → `industry_consistency≥0.60 +0.05` / `<0.20 −0.15`。
- 实测 `mapping_method` 占比：`INDUSTRY_CONCEPT 76.88% / DIRECT_INDUSTRY 19.34% / INDUSTRY_PRODUCT 3.36% / CONCEPT 0.31% / KEYWORD 0.10%`；`UNMAPPED 0%`（无未映射残留，PASS）。
- 实测 `confidence`：min 0.600 / p50 0.820 / max 0.990；`<0.70` 占 11.8%。
- `membership_origin`：`RAW_BOARD 22654 / INDUSTRY_DIRECT 1249 / CORE_COMPANY 5`（`CORE_COMPANY` 仅 5 行，与 master 缺 `core_companies` 直接相关）。
- **PIT**：`effective_date` 为单份快照（全部 `20260916`，`is_static=true`），无 as-of 版本序列；`index_member_all(is_new="Y")`（L618）取最新一期归属。
- **THS 概念**：`load_ths()`（L788）实际加载并与东财概念 `concat` 合并（L829-832），但脚本 docstring 与 `output/sector_mapping_audit.md`（L2181-2184 对应正文）声称「未采用同花顺概念数据」→ 跨文件不一致。
- `detect_pollution`（L1655-1700）7 类污染：`KEYWORD_POLLUTION / CONCEPT_POLLUTION / INDUSTRY_CONFLICT / MULTI_THEME_POLLUTION / NON_CORE_CONCEPT / LOW_CONFIDENCE / DYNAMIC_ONLY`；仅写 CSV，**无回写修正 membership**。
- `exclude_keywords` 因 `len(ek) <= 6` 门槛（L1138），master 中 4/11 条排除规则永久失效。
- 该脚本**不加载任何行情/涨跌幅数据**（`CHECK 08` L1896-1899 自证）。

### 5.3 Step 3：主题质量与状态

**权重合规性（规格 §九 / §十五）—— PASS**

```python
HEALTH_WEIGHTS = {                       # L113-120，硬编码
    "breadth": 0.25, "weighted_breadth": 0.20, "relative_strength": 0.15,
    "volume_structure": 0.15, "core_primary": 0.15, "consistency": 0.10,
}
CORE_PRIMARY_INNER = {"core_breadth": 0.5, "primary_breadth": 0.3, "core_rs": 0.2}   # L121
RETURN_WEIGHT_SHARE = (
    HEALTH_WEIGHTS["relative_strength"] * 1.0
    + HEALTH_WEIGHTS["core_primary"] * CORE_PRIMARY_INNER["core_rs"]
    + HEALTH_WEIGHTS["consistency"] * 1.0)   # = 0.28
```

与规格逐项一致；`return contribution = 0.28 ≤ 0.30` **PASS**。

- `relative_strength` 实为 **5 日超额收益**（`vs_market_5 = ew_ret_5 − 沪深300 5日`，L447），非纯涨幅；20 日涨幅、涨停家数、热度、新闻**均未进入 health**。
- `STATES`（L152）= `STRONG / HEALTHY / NEUTRAL / WEAK / DETERIORATING / DATA_INVALID`。
- `STATE_RULES`（L135-150）含 `weak_breadth −0.20`、`weak_breadth_deep −0.35`、`deteriorating_breadth_drop −0.35`、`healthy_min_positive_indicators 4`。
- **hysteresis**：`streak_len >= 2`（L654-679）连续 2 日方写 `sector_state_base`；`DATA_INVALID` 双向即时。实测 `sector_state_base` 每主题平均切换 12.92 次。
- `breadth` = 当日涨跌家数差占比（L400-404）。`data_quality_score` 见 L470-477；`DATA_INVALID` 条件为 `dq<0.60` 或 `valid_member_count<5`；`LOW_COVERAGE` 只封杀 STRONG，不阻断信号。
- `--membership-mode` 默认 `snapshot`（L757、L1379-1380）。
- **未来函数**：全文无 `shift(-n)`、无 `pct_change`、无 `cumsum`；自检采用**物理截断重算比对**（L1006-1080，`LOOKAHEAD_WINDOW=30`），比对列不含 `sector_state_base` → **PASS**。

### 5.4 Step 4：SEOS

**权重合规性（规格 §十三）—— PASS**

```json
"score_weights": {"breadth_expansion": 0.25, "core_breadth": 0.20, "relative_strength": 0.15,
  "volume_participation": 0.15, "amount_share": 0.10, "health_momentum": 0.10, "consistency": 0.05}
```

与规格**逐项一致，差异为 0**。

**`extension penalty` 真实存在（规格 §十三 追问）—— PASS**

```python
df["extension_penalty"] = (pen / pen_w if pen_w > 0 else pen).clip(0.0, 1.0)   # L463-475
max_ded = float(ep.get("max_deduction", 1.0))   # 0.45
df["seos_score"] = df["seos_raw"] * (1.0 - np.minimum(df["extension_penalty"], max_ded))
```

内层权重 `ew_ret_5 0.40 / ew_ret_10 0.25 / ew_ret_20 0.15 / top5_concentration 0.20`。**累计涨幅只用于「抑制」EARLY，不用于加分**（`extension_penalty < 0.35` 才是 EARLY 准入门槛，L651）。

- `LADDER = [DORMANT, EARLY, EMERGING, CONFIRMING, STRONG]`（L106）；`ALL_PHASES` 另加 `COOLING/DETERIORATING/EXITING/DATA_INVALID`。
- `phase_thresholds = {dormant:35, early:55, emerging:65, confirming:72, strong:75}`；`phase_exit_thresholds = {early:48, emerging:58, confirming:65, strong:70}`；`confirm_days = 2`（**双阈值 + 连续确认**，与 Step 3 的连续 2 日实现不同）。
- `phase_raw`（L821，无防抖当日快照）与 `theme_phase`（L854，状态机）。
- `shift(-n)` **仅 1 处**：L1139 `g2["nav"].shift(-h)`（回测），`fwd_*` 未写入任何正式文件。三项自检 `CHECK_NO_LOOKAHEAD_SEOS / PHASE / ROTATION` 实测全 PASS，偏差 `0.000e+00`。`rotation_signal` 参照窗 `[t-19, t-10]` 纯历史（L601-603）。
- `startup_quality` 实际 6 值：`HEALTHY_EXPANSION / VOLUME_SPIKE / NARROW_LEADERSHIP / FAILED_EXPANSION / NEUTRAL / INSUFFICIENT_DATA`；规格列举的 `WEAK_EXPANSION / PRICE_ONLY / NO_EXPANSION` **不存在**。
- **`core_pattern`（L1004-1018）无法识别「第二梯队刚开始改善」**：仅用 `secondary_breadth` 绝对水平，特征层无 `secondary_breadth_delta_*`；L1009 `sdc` 为死变量。
- `phase_thresholds.dormant=35` 定义但代码未引用。

### 5.5 Step 5：个股候选池

**权重合规性（规格 §十九）—— PASS**

```json
{"theme_opportunity":0.20, "membership_quality":0.20, "industry_role":0.15,
 "fundamental_quality":0.15, "relative_theme_strength":0.10,
 "structure_quality":0.10, "diffusion_signal":0.05, "data_quality":0.05}
```

与规格**逐项一致**；`cross_theme_bonus` 上限 **5.0**（L1114-1119，实测取值仅 {0.0, 2.5, 5.0}，134k/22k/10k 行）**PASS**。

- `LEGACY_NAMES`（L121-122）含 `theme_master.json / theme_mapping.json` —— 是**文件名黑名单**（拦截旧命名），与「`sector_master.json` 为唯一真源」不矛盾。
- **处理顺序 theme-first，无「先按涨幅筛股」**：`L1338 compute_theme_layer` → `L1340-1341 elig`（`theme_phase NOT IN [DORMANT, DATA_INVALID]` & `warmup_ready` & `~data_invalid`）→ `L1346 pairs` 展开 → `L1354 merge stock_long` → 评分。**PASS**
- `candidate_score = raw − extension_penalty + cross_theme_bonus`（L1423-1426）。
- `extension_penalty` 加性扣减，config 上限 25.0 分，**实测 max 28.01**（存在 config 之外的额外扣减来源）。
- `crowding_score` **不进分数**，仅门槛。
- `extreme_rules` 硬 EXCLUDE：`ret_5≥0.25 OR dist_ma20≥0.30`；`volume_ratio_5≥3.5 OR turnover_5≥30`（config L197-203）。
- `structure_quality` v1.5（config L153-164）：weights `dist_ma20 0.30 / dist_ma60 0.25 / volume_health 0.25 / drawdown_control 0.20`；ramps `[-0.1226,0.1174] / [-0.2090,0.0910] / [0.5803,1.3303] / [-0.4368,-0.1668]`；`precheck_min = 45.0` **从未被代码读取**（实际用 `candidate_structure_min = 45.0`）。
- `candidate_type` 8 值：`THEME_CORE / THEME_DIFFUSION / THEME_SECOND_LINE / THEME_LEADER / THEME_RELATIVE_STRENGTH / THEME_PULLBACK / THEME_WATCH / EXCLUDE`。
- **`THEME_PULLBACK`（L1048-1058）**：phases `{EMERGING,CONFIRMING,STRONG}` + membership `{CORE,PRIMARY}` + `dist_ma60≥−0.06` + `dist_ma120≥−0.10` + `drawdown_20∈[−0.30,−0.06]` + `volume_ratio_5≤0.95` + `rel_ret_20≥−0.04` + `fundamental≥40`；**无主题机会/宽度门槛**，且 `fundamental_min 40 < candidate 45`；`turnover_ratio_5_vs_20_max` 配置存在但未引用。
- **PIT 财务合规**：按 `ann_date` 做 `merge_asof(direction="backward")`（L607-624）；`CHECK_NO_FUTURE_FUNDAMENTAL`（L1931-1934）通过；**未使用 `report_period`**。
- PE/PB 来自 `daily_basic_cache` 同日快照，不进任何评分/输出（「算出即弃」）。
- `shift(-n)` **仅 1 处**：L595 `out[f"_fwd_ret_{h}"] = adj.shift(-h)/adj - 1.0`（回测专用，被列裁剪阻断）。
- **未使用的已声明输入**：`data/sector_state_history.csv`、`output/sector_seos_today.json`、`output/sector_opportunity_pool.json`（L106-108 定义但全文无引用）。
- **未引用的 config 键**（部分）：`precheck_min`、`conflict_role_cap`、`watch_crowding_max`、`strong_phase_policy.candidate_max_crowding`、`crowding_thresholds.early_theme_core`、`pullback_rules.volume_contraction.turnover_ratio_5_vs_20_max`、`status_thresholds.exclude_status_rules`。

---

## 六、量化验证（规格 §二十五 – §三十八）

原始输出见附录日志（`_audit_quant.txt`，一次性脚本，已用完删除）。

### 6.1 数据完整性（§二十五）—— PASS

| 项 | 值 |
|---|---|
| membership | 23908 行 / 50 主题 / 4995 股票 |
| effective_date | 全部 `20260916`（唯一值） |
| is_static | 全部 `true` |
| mapping_method | INDUSTRY_CONCEPT 76.88% / DIRECT_INDUSTRY 19.34% / INDUSTRY_PRODUCT 3.36% / CONCEPT 0.31% / KEYWORD 0.10% |
| UNMAPPED | 0.00% |
| membership_type | THEMATIC 19558 / PRIMARY 2481 / CORE 979 / SECONDARY 890 / **OBSERVATION 0** |
| confidence | min 0.600 / p10 0.670 / p50 0.820 / p90 0.990 / max 0.990；`<0.70` 占 11.8% |
| SEOS | 4550 行 / 50 主题 / 91 日 / `20260512~20260916`，每日恒 50 主题 |
| 候选池 | 166366 行 / 40 主题 / 77 日 / `20260522~20260916` |
| 候选状态 | EXCLUDE 129283 / WATCH 30542 / CANDIDATE 6541 |
| 交易日对齐 | 行情库区间内**缺失 0 日**；SEOS 无越界日期 |

**各主题成员数极值**：T50 1445（核心占比 0.0%）/ T12 1432（7.7%）/ T07 1336（**0.0%**）/ T06 1236（**0.0%**）；核心占比最高 T32 100.0% / T44 98.4% / T41 96.9%。核心占比 0% 的主题共 7 个。

### 6.2 核心指标相关性（§三十三）—— SEOS MOMENTUM COLLAPSE：**不成立**

横截面 Spearman（按日）中位数：

| 指标对 | 中位 Spearman | 有效日 |
|---|---|---|
| `seos_score ~ ew_ret_5` | 0.573 | 91 |
| `seos_score ~ ew_ret_10` | 0.111 | 91 |
| `seos_score ~ ew_ret_20` | **0.023** | 91 |
| `seos_score ~ breadth` | 0.549 | 91 |
| `seos_score ~ breadth_delta_5` | **0.637** | 86 |
| `seos_score ~ core_breadth` | 0.575 | 91 |
| `seos_score ~ core_breadth_delta_5` | **0.639** | 86 |
| `seos_score ~ volume_ratio_5` | **0.690** | 85 |
| `seos_score ~ amount_share_delta_5` | 0.625 | 86 |
| `seos_score ~ theme_health` | 0.764 | 91 |
| `theme_health ~ breadth` | 0.914 | 91 |

判定：非相对强弱权重合计 0.85，`relative_strength` 仅 0.15。若广度类贡献虚弱而 5 日涨幅相关极高，才构成 MOMENTUM COLLAPSE。**实测广度类相关（0.637 / 0.639 / 0.690）高于 5 日涨幅相关（0.573），且 20 日涨幅相关仅 0.023** → **不成立，PASS**。

**观察**：`seos_score ~ breadth`（水平值）达 0.549，与 `breadth_delta_5`（0.637）接近，说明分数仍是「水平 + 变化」混合体，对已高位但不再扩张的主题存在残留认可。

### 6.3 候选分与涨幅相关性（§二十六）

| 指标对 | 中位 Spearman |
|---|---|
| `candidate_score ~ ret_5` | 0.296 |
| `candidate_score ~ ret_20` | 0.178 |
| `candidate_score ~ ret_1` | 0.184 |
| `candidate_score ~ distance_ma20` | 0.296 |
| `candidate_score ~ relative_theme_strength` | 0.287 |
| `candidate_score ~ fundamental_quality` | 0.351 |
| `candidate_score ~ structure_quality_precheck` | 0.342 |
| `candidate_score ~ theme_diffusion_score` | 0.431 |
| `candidate_score ~ extension_penalty` | 0.073 |

诊断：与短期涨幅相关仅 0.296（弱），与基本面/结构分发散相关（0.35/0.34），**分数由多维驱动而非单一动量**。

### 6.4 三档涨幅画像（§二十 追涨筛查）

| 状态 | n | ret_5 均值 | ret_5 中位 | ret_20 均值 | dist_ma20 中位 | 换手5 中位 |
|---|---|---|---|---|---|---|
| CANDIDATE | 6541 | +0.047 | +0.039 | +0.059 | +0.037 | 1.80 |
| WATCH | 30542 | +0.021 | +0.019 | −0.031 | −0.004 | 2.11 |
| EXCLUDE | 129283 | +0.015 | +0.009 | −0.014 | −0.005 | 2.44 |

候选档 5 日涨幅仅比观察档高 2.0 个百分点、比排除档高 3.0 个百分点，**差距温和，无极端追涨特征**。

各类 `candidate_type` 涨幅画像：

| 类型 | n | ret_5 中位 | ret_20 中位 | ext_pen 中位 |
|---|---|---|---|---|
| THEME_CORE | 9879 | +0.029 | +0.028 | 0.00 |
| THEME_DIFFUSION | 2266 | +0.044 | −0.056 | 0.52 |
| THEME_PULLBACK | 470 | +0.009 | +0.026 | 0.00 |
| THEME_RELATIVE_STRENGTH | 13730 | +0.025 | +0.032 | 0.37 |
| THEME_SECOND_LINE | 1003 | +0.052 | +0.012 | **5.00** |
| THEME_WATCH | 139018 | +0.009 | −0.037 | 0.00 |

### 6.5 §二十 反追涨专项（最重要测试之一）—— **PASS**

| 样本 | 定义 | n | score 均值 | score 中位 | CANDIDATE 占比 | ext_pen 均值 | 结构分均值 |
|---|---|---|---|---|---|---|---|
| **A** | 强主题 + `ret_5≥30%` + `dist_ma20≥30%` | 221 | **44.38** | 41.47 | **0.0%** | 19.85 | **92.3** |
| **B** | 强主题 + CORE + `ret_5∈[2%,8%]` + `dist_ma20∈[0,10%]` + 量比∈[1,2] | 681 | **76.72** | 77.05 | 49.9% | 1.06 | 73.2 |

**`A 均分 − B 均分 = −32.34`** → 极端动量样本远低于健康样本，**不存在 MOMENTUM CHASING BIAS**。

极端样本实际去向：`ret_5≥25%` 共 2983 行 → CANDIDATE **0** 行（EXCLUDE 2982）；`dist_ma20≥30%` 共 1132 行 → CANDIDATE **0** 行。**CANDIDATE 中同时满足极端延伸条件的行数 = 0**。

**重要观察（记为 P2-16）**：Sample A 的 `structure_quality_precheck` 均分（92.3）**反而高于** Sample B（73.2）。说明 `structure_quality` 本身对极端延伸**不做惩罚**，拦截完全依赖 `extension_penalty` 与 `extreme_rules` 单点防线。若 `max_penalty_points` 被调低，追涨防线将立即失效。

### 6.6 §十四 晚期伪启动测试（最重要测试）—— **PASS**

样本：`theme_phase ∈ {EARLY, EMERGING}`，n=352。

| 条件 | 命中数 | 占比 |
|---|---|---|
| 5D ≥ 15% 且 20D ≥ 25% | **0** | 0.0% |
| `top5_concentration ≥ 0.60` | **0** | 0.0% |
| `core_breadth_delta_5 ≤ 0` | 169 | 48.0% |
| **三条件同时满足** | **0** | **0.0%** |

`startup_quality` 分布：`FAILED_EXPANSION 151 / NEUTRAL 111 / HEALTHY_EXPANSION 84 / VOLUME_SPIKE 6`。

判定：**无任何晚期伪启动被误判为 EARLY/EMERGING**。48% 的早期相位核心广度 5 日未改善，属「广度尚未扩散」的正常早期形态。同时 `FAILED_EXPANSION` 占 43% 说明 Step 4 已能识别部分扩张失败样本。

### 6.7 跨主题污染（§十 / §三十六）—— **WARN**

股票归属主题数分布：

| 归属主题数 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 股票数 | 320 | 673 | 880 | 742 | 663 | 562 | 386 | 297 | 202 | 116 | 60 | 52 | 15 | 11 | 4 | 8 | 2 | 2 |

- **≥2 主题：4675 只（93.6%）**；≥3：4002（80.1%）；≥4：3122（62.5%）；≥5：2380（47.6%）；**≥6：1717（34.4%）**。
- 极端案例：京东方A 18 主题、华工科技 18 主题、远东股份 17 主题、苏州高新 17 主题。
- **CORE 身份互斥保持完好**：拥有 ≥2 个 CORE 身份的股票 **0 只**。
- 现有限制机制：`primary_theme` 行才保留 `cross_theme_bonus`、bonus 上限 5.0。

### 6.8 链路闭环（§三十五）—— WARN

- 有候选产出的主题：**39 / 50**。
- 每主题每日候选数：均值 18.58 / 中位 16 / P90 37 / max 79。
- **处于 EARLY~STRONG 却全历史 0 候选的主题：T48 城市更新（机会日 4）** → 需定位原因。
- 候选最多主题：T36 994 / T23 502 / T45 485 / T16 363 / T15 328。
- 20260916 当日仅评估 2 个主题（T01 半导体 1031 行、T42 112 行），当日候选仅 T01 半导体 53 个。

### 6.9 异常主题检查（§三十六）

已输出 `output/theme_step1_5_anomaly.csv`（10 行）：

| 规则 | 命中数 | 样例 |
|---|---|---|
| `STRONG_NO_CORE_CANDIDATE` | 6 | 20260703 T34 旅游酒店餐饮 |
| `STRONG_BUT_ZERO_CANDIDATE` | 2 | 20260703 T18 石油石化 |
| `SEOS_HIGH_COREBREADTH_LOW` | 1 | 20260702 T35 消费服务 |
| `STRONG_BUT_CORE_WEAK` | 1 | 20260706 T44 公用事业 |
| `SEOS_HIGH_BREADTH_LOW` | 0 | — |
| `EARLY_BUT_20D_ALREADY_HIGH` | 0 | — |
| `DORMANT_BUT_HAS_CANDIDATE` | 0 | — |

### 6.10 阈值敏感性（§三十二）

**SEOS `early` 阈值（55）**：

| 扰动 | 阈值 | 信号数 | 变化 |
|---|---|---|---|
| 基准 | 55.0 | 1135 | — |
| −10% | 49.5 | 1707 | **+50.4%** |
| −5% | 52.2 | 1394 | +22.8% |
| +5% | 57.8 | 903 | −20.4% |
| +10% | 60.5 | 710 | **−37.4%** |

邻域密度：±5% 内 10.79%；±10% 内 21.91%。→ **WARN（PARAMETER FRAGILITY 轻度触发）**：非「结果全变」，但敏感度不低。

**候选状态阈值（完整复现 `assign_status`，一致率 100.00%）**：

| 扰动 | CANDIDATE | 变化 | 状态改变率 |
|---|---|---|---|
| 基准（watch 42 / cand 60） | 6541 | — | — |
| `cand_min` −10% | 6642 | +101 | 0.06% |
| `cand_min` −5% | 6629 | +88 | 0.05% |
| `cand_min` +5% | 6237 | −304 | 0.18% |
| `cand_min` +10% | 5569 | **−972** | **0.58%** |
| `watch_min` ±10% | 6541 | 0 | 0.05%~0.31% |

`extension_penalty` 上限扰动（25.0）：±30% → CANDIDATE 变化仅 +30 / −53，状态改变率 0.03%~0.06%。→ **候选侧阈值 PASS（稳健）**。

### 6.11 分数可解释性与重复计算（§三十七）

`candidate_score` 与各分项的全域 Spearman：

| 分项 | 权重 | 与总分相关 |
|---|---|---|
| `data_quality` | 0.05 | **0.444** |
| `theme_diffusion_score` | 0.05 | 0.408 |
| `theme_opportunity_score` | 0.20 | 0.403 |
| `structure_quality_precheck` | 0.10 | 0.359 |
| `cross_theme_bonus` | 加分项（≤5） | 0.356 |
| `fundamental_quality` | 0.15 | 0.332 |
| `relative_theme_strength` | 0.10 | 0.269 |
| `extension_penalty` | 扣分项 | 0.063 |

**发现**：
1. `data_quality`（权重仅 0.05）与总分的相关（0.444）**高于** `theme_opportunity_score`（权重 0.20，相关 0.403）—— 存在「小权重高相关」异常，总分混入了数据质量的分层效应。
2. `extension_penalty` 与总分相关仅 0.063，作用面窄（p50=0、p90=5.0、max=28.01）。
3. `extension_penalty` **实测 max 28.01 > config 上限 25.0**，存在 config 之外的额外扣减来源。
4. `cross_theme_bonus` 相关 0.356，与最多 +5 分的量级不匹配，提示与其它高分量项共线。

**特征依赖图（跨层同一原始变量复用）**：

| 原始变量 | Theme Health | SEOS | Candidate | 判定 |
|---|---|---|---|---|
| 5 日涨幅 | `relative_strength` 0.15 | `extension_penalty` 内层 0.40（仅抑制） | `ret_5` ramp + 硬门槛 | 语义已改为超额收益与抑制项，方向正确 |
| 广度/家数 | 0.25 + 0.20 + 0.075 | `breadth_expansion` 0.25 + `core_breadth` 0.20 | `theme_opportunity` 0.20 + `diffusion` 0.05 | **跨层累积权重偏高**（Health 0.525 / SEOS 0.45 / Candidate 0.25） |
| 成交量 | `volume_structure` 0.15 | `volume_participation` 0.15 + `amount_share` 0.10 | 结构层 0.25×0.25 + 硬门槛 | 跨层复用 |
| `distance_ma20` | — | — | 结构层 0.30 + 硬门槛 + PULLBACK | 层内 2 次消费，方向互补 |
| `theme_phase` | — | — | `phase_base` 0.5 内层 + elig + 类型判定 + PULLBACK + risk_bucket | 结构性复用 |

**层内重复计算判定**：未发现同一层内对同一原始变量做两次**等向加分**（不存在层内 double counting）。跨层复用为设计选择，但 **breadth 类变量的跨层累积权重偏高，是主要过拟合面**。

### 6.12 类型 × 状态交叉表（§二十四）

| candidate_type | CANDIDATE | EXCLUDE | WATCH |
|---|---|---|---|
| THEME_CORE | 5903 | 2258 | 1718 |
| THEME_DIFFUSION | 322 | 479 | 1465 |
| THEME_PULLBACK | 316 | 9 | 145 |
| **THEME_RELATIVE_STRENGTH** | **0** | 10661 | 3069 |
| **THEME_SECOND_LINE** | **0** | 107 | 896 |
| THEME_WATCH | 0 | 115769 | 23249 |

→ **WARN**：`THEME_RELATIVE_STRENGTH` 与 `THEME_SECOND_LINE` 结构上**永不可能成为 CANDIDATE**；规格 §二十一 要求的 SECOND_LINE_EXPANSION 在最终输出层不可达。`THEME_SECOND_LINE` 的 `ext_pen` 中位 5.00，说明该类样本普遍携带延伸惩罚。

### 6.13 数据质量与无效数据（§十二 / §十三）

- 候选 `data_quality`：min 4.1 / p10 60.1 / p50 83.2。
- `risk_flags` 非空占比 **82.8%**；Top 组合：`LOW_BREADTH_SUPPORT|WEAK_CORE_SUPPORT`（23456）、`FUNDAMENTAL_WEAK`（21681）、`LOW_BREADTH_SUPPORT|WEAK_CORE_SUPPORT|THEME_COOLING`（11641）、`DATA_INCOMPLETE`（8996）、`THEME_COOLING`（8465）。
- SEOS `DATA_INVALID` 行数 **0**；`warmup_ready=False` 占 18.7%。

**相位分布失衡（关键统计限制）**：

| 相位 | `theme_phase` | `phase_raw` |
|---|---|---|
| DORMANT | **4050** | 3929 |
| EARLY | 195 | 289 |
| EMERGING | 157 | 189 |
| STRONG | **4** | 73 |
| CONFIRMING | **18** | 70 |
| DETERIORATING | 100 | — |
| COOLING | 20 | — |
| EXITING | 6 | — |

`sector_state_base`：`NEUTRAL 1752 / WEAK 1333 / HEALTHY 890 / DETERIORATING 563 / STRONG 12`。

`theme_phase` 与 `phase_raw` 不一致 **18.9%**；防抖把 STRONG 从 73 压制到 4、CONFIRMING 从 70 压制到 18。**DORMANT 占 89%，实际可用机会样本仅约 500 个主题日；STRONG 4 行、CONFIRMING 18 行，任何基于高阶阶段的统计均不可靠。**

### 6.14 Hysteresis 效果（§十一 / §十三）

| 指标 | `theme_phase` | `phase_raw` |
|---|---|---|
| 每主题切换次数均值 | 5.0 | 21.2 |
| 中位 | 6 | 24 |
| max | 14 | 35 |
| 占样本比例 | 5.5% | 23.3% |

**防抖把切换频率降低 76.6% → PASS**。`phase_duration`：p50=17 / p90=54 / max=91。

**副作用**：`phase_duration` 中位 17 日、且只在连续 2 日确认后写入，导致相位显著滞后 —— 这是 `THEME_PULLBACK` 误判（P1-05）的成因之一，也解释了 STRONG/CONFIRMING 样本枯竭（P2-15）。

### 6.15 回测可信度（§二十九 / §三十 / §三十一）

**Step 4（`output/seos_backtest.md`）**：

| 组 | 周期 | 均值 | 胜率 | 随机分位 |
|---|---|---|---|---|
| 基线 | T+3 | −0.36% | 43.9% | — |
| 基线 | T+20 | −1.40% | 45.3% | — |
| **ROTATION_IN** | T+10 | **+1.89%** | 58.3% | **100.0%** |
| **ROTATION_IN** | T+20 | **+1.35%** | 57.7% | **100.0%** |
| **EMERGING** | T+3 | **−1.31%** | 36.5% | **1.4%** |
| EMERGING | T+5 | −0.87% | 34.6% | 26.0% |
| EARLY | T+3 | 0.00% | 58.0% | 86.4% |
| EARLY | T+10 | −1.69% | 13.6% | — |
| EARLY | T+20 | +0.47% | 96.6% | — |
| CONFIRMING | — | 仅 7 个信号 | — | 样本不足 |

**Step 5（`output/sector_stock_candidate_backtest.md`）**：

| 组 | 周期 | 均值 | 胜率 | 超额 |
|---|---|---|---|---|
| **THEME_DIFFUSION** | T+20 | **+0.0322** | 65.3% | **+0.0425** |
| THEME_DIFFUSION | T+10 | +0.0236 | 64.2% | +0.0484 |
| THEME_CORE | T+20 | +0.0027 | 52.9% | +0.0130 |
| THEME_CORE | T+3 | −0.0037 | 41.2% | — |
| **THEME_PULLBACK** | T+20 | **−0.0434** | — | **−0.0331** |
| THEME_PULLBACK | T+10 | −0.0261 | — | — |
| THEME_SECOND_LINE | T+3 | −0.0162 | 38.0% | — |
| ALL_THEME_MEMBERS | T+20 | −0.0103 | 51.2% | — |
| RANDOM_MEMBERS | T+20 | −0.0085 | 51.5% | — |

正向超额组数 12 / 16。

**可信度限制（§三十一 未满足）**：
1. 样本区间仅 77 个交易日（20260522~20260916），单一市场环境。
2. **未按年份 / 牛熊 / 行业轮动阶段分段检验**。
3. `CONFIRMING` 仅 7 个信号，禁止据此下结论。
4. 两组回测均声明「不因回测结果调整参数」，参数未经样本外验证。
5. 存在系统性负 alpha 组：`EMERGING`（Step 4）与 `THEME_PULLBACK` / `THEME_SECOND_LINE`（Step 5）。

→ **WARN**。

---

## 七、Gate 评分（规格 §三十九）

| Gate | 名称 | 评级 | 依据 |
|---|---|---|---|
| **G0** | Architecture | **WARN** | Step 5 声明输入未接线（P1-10）；死配置 7+ 项（P1-09）；文档实现不一致（P1-08）；字段命名与规格不符（AV-01） |
| **G1** | Data Integrity | **PASS** | 无缺日（0 日）、无 UNMAPPED、无 OBSERVATION 残留、无重复 sector_id；confidence 分布正常 |
| **G2** | Theme Semantics | **WARN** | 跨主题重叠 93.6%/34.4%（P1-06）；边界重叠致 7 主题核心占比 0%（P2-11）；exclude_keywords 失效（P2-12）；污染不回写（P1-07） |
| **G3** | Point-in-Time | **WARN** | membership 单份快照无 as-of（P1-03）；Step 5 主题层聚合缺 PIT 过滤（P0-01）；PIT 财务按 ann_date 合规 ✓ |
| **G4** | Theme State | **WARN** | 权重合规 ✓、无未来函数 ✓；但 snapshot 模式、相位分布失衡（DORMANT 89%，STRONG 4 行）、LOW_COVERAGE 不阻断 |
| **G5** | SEOS | **WARN** | 权重与 extension_penalty 合规 ✓；`core_pattern` 无法识别第二梯队改善（P1-04）；startup_quality 枚举不符（P2-17）；EMERGING 负 alpha（P0-02） |
| **G6** | Theme-to-Stock | **WARN** | 权重合规 ✓、theme-first ✓、反追涨 PASS ✓、cross_theme_bonus ≤5 ✓；THEME_PULLBACK 缺主题门槛（P1-05）；两类类型永不 CANDIDATE（P2-14）；结构层不惩罚极端延伸（P2-16） |
| **G7** | Backtest | **WARN** | 局部有效（ROTATION_IN 100% 随机分位、THEME_DIFFUSION 正向超额）；未分段检验、样本 77 日、存在系统性负 alpha 组 |
| **G8** | Robustness | **PASS** | 候选侧阈值稳健（±10% 状态改变 ≤0.58%）；防抖效果显著（−76.6%）；仅 SEOS early 阈值轻度敏感（P2-13） |

---

## 八、TOP 10 问题（规格 §四十一）

完整 23 项见 `output/theme_step1_5_issue_list.csv`。TOP 10 如下。

### P0 级（必须优先修复）

**P0-01 · Step 5 主题等权收益聚合缺 PIT 过滤，与同脚本个股层口径不一致**
- **证据**：`sector_stock_candidate_build.py` L651-663 用全表 membership（含未来成员）聚合主题等权收益；而 L1346-1350 的个股展开做了 PIT 过滤。同一脚本内两套成员口径。
- **影响**：主题层特征可能含未来成员信息，构成未来函数渗透；破坏规格要求的 PIT 一致性。
- **涉及文件**：`sector_stock_candidate_build.py`（L651-663 与 L1346-1350）
- **修复建议**：将 L651-663 统一改为与 L1346-1350 相同的 PIT 过滤口径。
- **修复难度**：中

**P0-02 · EMERGING 阶段信号为显著负 alpha，且直接传导至 Step 5**
- **证据**：`output/seos_backtest.md` — EMERGING T+3 均值 −1.31%、胜率 36.5%、随机分位 **1.4%**（显著劣于随机）；T+5 −0.87% / 34.6% / 26.0%。CONFIRMING 仅 7 个信号。
- **影响**：Step 4 输出的 EMERGING 状态不可用；Step 5 `phase_base_scores` 却给 EMERGING **85 分**（仅低于 EARLY 的 90），负 alpha 状态被赋予高机会分。
- **涉及文件**：`output/seos_backtest.md`、`sector_seos_build.py`、`config/sector_stock_candidate_config.json`（`phase_base_scores`）
- **修复建议**：在 Step 6 前重新检验 EMERGING 的 `phase_base_score` 与准入门槛，或在 Step 5 中降低 EMERGING 权重。**本审计不现场修改参数**。
- **修复难度**：中

### P1 级

**P1-03 · membership 为单份静态快照，无 as-of 版本序列**
- **证据**：全部 23908 行 `effective_date=20260916`、`is_static=true`；`index_member_all(is_new="Y")`（L618）；Step 3/4 默认 `snapshot` 模式。
- **影响**：历史日成员由当期名单回推，已剔除/未纳入的股票被当作当日成员。
- **涉及文件**：`data/sector_membership.csv`、`sector_mapping_build.py`（L618）、`sector_state_build.py`（L757/L1379-1380）
- **修复建议**：建立按 `effective_date` 的版本序列，Step 3/4 切到 PIT 模式。**修复难度：高**

**P1-04 · `core_pattern` 无法识别「第二梯队刚开始改善」**
- **证据**：`sector_seos_build.py` L1004-1018 仅用 `secondary_breadth` 绝对水平；无 `secondary_breadth_delta_*`；L1009 `sdc` 为死变量。
- **影响**：规格 §二十一 要求的 SECOND_LINE_EXPANSION 识别能力缺失，信号滞后。
- **修复建议**：补充 delta 特征并纳入判定；清理死变量。**修复难度：中**

**P1-05 · THEME_PULLBACK 无主题机会门槛，基本面门槛低于候选门槛**
- **证据**：L1048-1058 无主题机会/宽度门槛；`fundamental_min 40 < candidate 45`；回测 T+20 超额 **−0.0331**（最差组）。
- **影响**：上游相位滞后时，转弱主题股票仍被判 PULLBACK，拖累候选池。
- **修复建议**：增加主题机会门槛；把 `fundamental_min` 提升到 ≥45。**修复难度：中**

**P1-06 · 跨主题污染严重（93.6% / 34.4%）**
- **证据**：≥2 主题 4675 只（93.6%）、≥6 主题 1717 只（34.4%）；京东方A / 华工科技各 18 主题。
- **影响**：主题归属失去区分度，同一股票反复贡献广度与成交额，虚增主题强度。
- **修复建议**：收敛边界、启用 exclude_keywords、对高归属股票设上限或降权。**修复难度：高**

**P1-07 · 污染检测只报告不回写**
- **证据**：`detect_pollution()`（L1655-1700）7 类标签仅写 CSV；Step 5 `pollution_hit` 占比 12.33%。
- **影响**：污染穿透 Step 3/4 未被消化，延迟到 Step 5 才硬排除。
- **修复建议**：在 Step 3/4 显式纳入数据质量扣分。**修复难度：中**

**P1-08 · 文档与实现不一致：THS 概念实际参与映射却被声明未采用**
- **证据**：L788 `load_ths()`、L829-832 `concat` 合并；同文件 L2181-2184 与 `sector_mapping_audit.md` 正文声称未采用。
- **影响**：数据来源真实性存疑，审计结论不可信。
- **修复建议**：如实声明使用范围，或删除 `load_ths`。**修复难度：低**

**P1-09 · 大量配置键定义但未接线（死配置）**
- **证据**：`precheck_min`、`conflict_role_cap`、`watch_crowding_max`、`candidate_max_crowding`、`early_theme_core`、`turnover_ratio_5_vs_20_max`、`exclude_status_rules`、`phase_thresholds.dormant` 均未被引用。
- **影响**：改配置不改行为，调参无效且误导维护。
- **修复建议**：逐项确认意图，接线或删除。**修复难度：低**

**P1-10 · Step 5 声明的输入文件实际未读取（DESIGNED ≠ ACTUAL）**
- **证据**：L106-108 声明 `sector_state_history.csv`、`sector_seos_today.json`、`sector_opportunity_pool.json`，全文无引用。
- **影响**：依赖图失真；Step 4 两个产物成为孤儿。
- **修复建议**：统一口径（真正消费或从声明中删除）。**修复难度：中**

### P2 级（摘要）

| ID | 问题 | 关键证据 |
|---|---|---|
| P2-11 | 主题边界重叠抑制核心成员认定 | T07 ≡ T15；7 个主题核心占比 0.0% |
| P2-12 | exclude_keywords 大面积失效 | 39/50 为空；`len<=6` 门槛使 4 条永久不可达 |
| P2-13 | SEOS early 阈值轻度敏感 | ±10% → 信号数 +50.4% / −37.4% |
| P2-14 | 两类 candidate_type 永不 CANDIDATE | `THEME_RELATIVE_STRENGTH` / `THEME_SECOND_LINE` 均 0 行 |
| P2-15 | 相位分布失衡，高阶样本不足 | DORMANT 89%；STRONG 4 行、CONFIRMING 18 行 |
| P2-16 | 结构层不惩罚极端延伸，防线单点 | Sample A 结构分 92.3 > Sample B 73.2 |
| P2-17 | startup_quality 枚举与规格不符 | 规格 3 个枚举值不存在 |

### P3 级（摘要）

`P3-18` 上级目录存在 6 个 legacy 同名文件；`P3-19` `phase_thresholds.dormant` 死配置；`P3-20` 无懒计算全量预计算；`P3-21` PE/PB 算出即弃；`P3-22` LOW_COVERAGE 不阻断；`P3-23` 工作目录遗留 15 个临时探针脚本。

---

## 九、必须修复项 / 可后优化项 / KEEP 保留项（规格 §四十二 / §四十三）

### 9.1 必须修复项（进入 Step 6 前）

```text
MUST FIX (P0)
  1. P0-01  Step 5 主题层等权收益聚合补 PIT 过滤，与个股层口径统一
  2. P0-02  复核 EMERGING 的 phase_base_score 与准入门槛（负 alpha 传导）

MUST FIX (P1)
  3. P1-03  建立 membership 的 as-of 版本序列，Step 3/4 切 PIT 模式
  4. P1-05  THEME_PULLBACK 增加主题机会门槛；fundamental_min 提升至 >=45
  5. P1-06  收敛跨主题污染（93.6% 股票归属 >=2 主题）
  6. P1-04  core_pattern 补充 secondary_breadth_delta 特征
  7. P1-07  污染标签在 Step 3/4 纳入数据质量扣分
  8. P1-08  修正 THS 概念的文档与实现不一致
  9. P1-09  死配置逐项接线或删除
 10. P1-10  Step 5 声明输入与实现对齐
```

### 9.2 可后优化项（不阻塞 Step 6）

```text
CAN OPTIMIZE
  - P2-11  主题边界重叠的优先级裁决
  - P2-12  补全 exclude_keywords 并修正长度门槛
  - P2-13  考虑 seos_score 分位化以降低阈值敏感度
  - P2-14  核查 THEME_RELATIVE_STRENGTH / THEME_SECOND_LINE 的门槛是否为设计意图
  - P2-16  在 structure_quality 增加极端延伸惩罚，形成双防线
  - P2-17  对齐 startup_quality 枚举命名
  - P3-18  legacy 同名文件归档
  - P3-20  引入懒计算降低 Step 5 计算量
  - P3-21  引入估值维度（PE/PB 当前算出即弃）
  - P3-23  清理 15 个临时探针脚本
  - 回测按年份 / 市场环境分段（§三十一）
  - 积累更长样本以改善 STRONG / CONFIRMING 样本枯竭
```

### 9.3 KEEP 保留项（审计确认不可动、不得修改）

```text
KEEP
  - Step 3  HEALTH_WEIGHTS 六项权重（breadth 25 / weighted 20 / RS 15 / volume 15 /
            core_primary 15 / consistency 10）—— 与规格逐项一致
  - Step 3  RETURN_WEIGHT_SHARE = 0.28 <= 0.30 —— 涨幅贡献约束达标
  - Step 4  score_weights 七项权重（25/20/15/15/10/10/5）—— 与规格逐项一致
  - Step 4  extension_penalty 机制（乘性扣减、max_deduction 0.45、
            仅抑制不加分）—— 真实存在且方向正确
  - Step 5  candidate_weights 八项权重（20/20/15/15/10/10/5/5）—— 与规格逐项一致
  - Step 5  cross_theme_bonus 上限 5.0 —— 合规
  - Step 5  extreme_rules 硬 EXCLUDE（ret_5>=25% 或 dist_ma20>=30%）—— 必须保留
  - Step 5  theme-first 处理顺序 —— 必须保留（禁止改为先按涨幅筛股）
  - Step 5  PIT 财务按 ann_date 的 merge_asof(backward) —— 合规，必须保留
  - 四层脚本的 shift(-n) 隔离在回测分支、被列裁剪阻断 —— 必须保留
  - 防抖机制（Step 3 连续 2 日 / Step 4 双阈值 + confirm_days=2）
            —— 把切换频率降低 76.6%，必须保留
  - 异常样本（output/theme_step1_5_anomaly.csv 10 行）—— 不得删除
```

---

## 十、四个 YES/NO（规格 §四十八）

| 问题 | 结论 | 依据 |
|---|---|---|
| **NO LEGACY CONTAMINATION** | **YES** | 四层脚本均只扫描路径不读内容（`sector_mapping_build.py` L416-424）；`legacy_config_used=false` 恒成立。残余风险：上级目录存在 6 个同名旧配置（P3-18） |
| **NO FUTURE LEAKAGE** | **YES（含 1 处例外）** | Step 3 无 `shift(-n)`/`pct_change`/`cumsum`；Step 4 仅 L1139、Step 5 仅 L595，均在回测分支且被列裁剪阻断；PIT 财务按 `ann_date` 合规。**例外：Step 5 主题层等权收益聚合缺 PIT 过滤（P0-01）** |
| **NO SERIOUS THEME POLLUTION** | **NO** | 93.6% 股票归属 ≥2 主题，34.4% 归属 ≥6 主题；极端案例 18 个主题。CORE 身份互斥保持完好（0 只拥有 ≥2 个 CORE） |
| **NO OBVIOUS MOMENTUM CHASING** | **YES** | Sample A（极端动量）均分 44.38 对 Sample B（健康）76.72，**差 −32.34**；`ret_5≥25%` 的 2983 行中 CANDIDATE 为 0；`dist_ma20≥30%` 的 1132 行中 CANDIDATE 为 0 |

---

## 十一、八个最终问题（规格 §四十五）

**Q1：系统架构是否完整？**
**基本完整，但有 3 处设计-实现偏离。** 四层链路数据流闭环（master → membership → state → seos → candidate），无断链。偏离项：Step 2 实际使用未声明的 THS 数据源；Step 5 声明 3 个输入却未读取；membership 无 as-of 版本序列。评级：WARN。

**Q2：数据是否完整可信？**
**是。** 交易日缺失 0 日；`UNMAPPED` 0%；`OBSERVATION` 0 行；`sector_id` 无重复/孤儿；confidence 分布合理（p50 0.820）。评级：PASS（G1）。

**Q3：Theme Health 权重是否符合规格？涨幅贡献是否受约束？**
**完全符合。** `HEALTH_WEIGHTS` 六项与规格逐项一致（硬编码 L113-120）；`RETURN_WEIGHT_SHARE = 0.28 ≤ 0.30`；5 日涨幅实为超额收益、20 日涨幅与涨停家数均未进入 health。评级：PASS。

**Q4：SEOS 权重是否符合规格？`extension penalty` 是否真实存在？**
**完全符合且存在。** `score_weights` 七项与规格逐项一致；`extension_penalty` 以乘性扣减实现（`seos_score = seos_raw × (1 − min(penalty, 0.45))`，L463-475），累计涨幅仅用于抑制 EARLY 门槛，不加分。评级：PASS（机制层面）；但 `core_pattern` 无法识别第二梯队改善、EMERGING 状态负 alpha，故 Gate G5 整体 WARN。

**Q5：能否避免晚期伪启动？**
**能。** 352 个 EARLY/EMERGING 观测中，同时满足「5D≥15% + 20D≥25% + 集中度≥0.60 + 核心广度未改善」的样本 **0 个**；`top5_concentration≥0.60` 的样本也是 0 个。规格 §十四 最重要的测试 **PASS**。

**Q6：是否存在追涨偏差？**
**不存在。** 极端动量样本（Sample A）均分 44.38，健康样本（Sample B）76.72，**A 反而低 32.34 分**；极端样本 100% 被 EXCLUDE；CANDIDATE 中极端延伸行数为 0。规格 §二十 最重要的反追涨测试 **PASS**。唯一隐忧：拦截完全依赖 `extension_penalty` 单点防线（P2-16）。

**Q7：能否识别第二梯队刚开始改善？**
**不能。** `core_pattern`（L1004-1018）只用 `secondary_breadth` 绝对水平，特征层无 `secondary_breadth_delta_*`；`THEME_SECOND_LINE` 类型在交叉表中 CANDIDATE 0 行。规格 §二十一 **未满足**（P1-04 / P2-14）。

**Q8：回测结果是否可信？**
**部分可信，需打折。** 局部显著有效：`ROTATION_IN` T+20 随机分位 100.0%；`THEME_DIFFUSION` T+20 超额 +0.0425、胜率 65.3%；16 组中 12 组正超额。但存在系统性缺陷：样本仅 77 个交易日、单一市场环境、**未按年份/市场环境分段（§三十一 未满足）**、`CONFIRMING` 仅 7 个信号、存在系统性负 alpha 组（`EMERGING` / `THEME_PULLBACK` / `THEME_SECOND_LINE`）。评级：WARN。

---

## 十二、最终结论（规格 §四十 / §四十八）

```text
================ STEP 1-5 FULL AUDIT · FINAL ================

G0 Architecture        WARN
G1 Data Integrity      PASS
G2 Theme Semantics     WARN
G3 Point-in-Time       WARN
G4 Theme State         WARN
G5 SEOS                WARN
G6 Theme-to-Stock      WARN
G7 Backtest            WARN
G8 Robustness          PASS    (1/9 PASS, 0 FAIL)

FINAL STATUS           CONDITIONAL_READY

P0 问题                2   (P0-01 Step5 主题层缺 PIT ; P0-02 EMERGING 负 alpha)
P1 问题                8
P2 问题                7
P3 问题                6
合计                  23

MUST FIX               P0-01 P0-02 P1-03 P1-04 P1-05 P1-06 P1-07 P1-08 P1-09 P1-10
CAN OPTIMIZE           P2-11 ~ P2-17 / P3-18 ~ P3-23 / 回测分段 / 样本积累
KEEP                   全部权重配置 / extension_penalty / extreme_rules /
                       theme-first 顺序 / PIT 财务 / 防抖机制 / 异常样本

NO LEGACY CONTAMINATION      YES
NO FUTURE LEAKAGE            YES（例外：P0-01）
NO SERIOUS THEME POLLUTION   NO
NO OBVIOUS MOMENTUM CHASING  YES
===========================================================
```

### 核心判断（规格 §四十九）

审计**没有发现系统性架构崩溃**：四层链路闭环、全部权重与规格逐项一致、未来函数被有效隔离、反追涨与晚期伪启动两道关键防线均真实有效（两项最重要的专项测试 PASS）。这是「可以进行局部修复后进入 Step 6」的形态，而非「必须推倒重来」。

但存在 **2 项 P0 + 8 项 P1**，其中 P0-01（Step 5 主题层聚合缺 PIT 过滤）直接触及规格 §二十七/§二十八 的硬红线，P0-02（EMERGING 负 alpha 传导）影响候选池质量来源。因此**不建议直接进入 Step 6**。

建议路径：**先修复 P0-01、P0-02、P1-03、P1-05 四项核心项，再决定是否进入 Step 6**。

### 本次审计行为声明

- 未修改 `sector_master.json`（theme_master）
- 未修改任何 mapping 逻辑
- 未修改 SEOS 权重
- 未修改 Candidate 权重
- 未调整任何阈值
- 未删除任何异常样本
- 未为提高回测结果修改任何规则
- 未新增指标、未新增主题
- 未开始 Step 6、未生成 BUY、未生成 NO TRADE
- 未修改任何生产脚本、配置或数据文件

**发现问题已记录，未现场修改。** 本次任务在审计报告完成后立即停止。

---

## 附录：审计产物清单

| 文件 | 说明 |
|---|---|
| `output/theme_step1_5_full_audit.md` | 本报告（主审计报告） |
| `output/theme_step1_5_issue_list.csv` | 23 项问题清单（P0–P3，含证据/影响/涉及文件/修复建议/修复难度） |
| `output/theme_step1_5_anomaly.csv` | 异常主题检查结果（10 行，4 类规则命中） |
| `output/theme_step1_5_dependency_map.json` | 依赖图（DESIGNED vs ACTUAL）、特征依赖图、量化证据、Gate 评级 |

一次性审计脚本 `_audit_quant.py` 与其日志 `_audit_quant.txt` 为审计过程物（用于采集 §六 的量化证据），不属于系统产物，已用完清理；其全部结论已转录进上述 4 份产物。
