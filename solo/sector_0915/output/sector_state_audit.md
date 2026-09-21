# Sector State Base 审计报告（Step 3）

- 生成时间：2026-09-20 13:43:52
- 基准日：20260918｜最近回放日：20260918
- Canonical 真源：sector_master.json（version=2.0，sectors=50）
- 成员口径：membership_confidence >= 0.7（不限 membership_type，用户决策 B）
- 成员模式：snapshot（STATIC_SNAPSHOT：Step 2 为单一快照，按静态产业定义回放，非时点成员回放）
- 回放窗口：20260515 ~ 20260918（90 个交易日；行情面板 20260407~20260918）
- 基准：000300.SH 沪深300（vs_market_*）、000852.SH 中证1000（vs_midcap_*）
- Sector Health 权重：breadth=0.25，weighted_breadth=0.20，relative_strength=0.15，volume_structure=0.15，core_primary=0.15，consistency=0.10；core_primary 内部 core_breadth=0.5，primary_breadth=0.3，core_rs=0.2
- return 驱动权重合计：0.28（需求 §十六 要求 <= 0.30：满足）
- LEGACY 配置（只报告存在性、未读取内容）：theme.json、theme_config.json、bak0615\theme.json、multi_factor_picker\cache\theme.json、theme_kg_v3\theme_kg_v3\config\subtheme_map.json、theme_kg_v3\theme_kg_v3\config\theme_config.json

## 1. 数据覆盖

- 回放交易日：90 个
- Sector 数：master 50 个；输出涉及 50 个
- 最近回放日有效 Sector（valid_member_count>0）：50 个
- membership 行数：21096（口径 confidence>=0.7；源文件 23929 行，低置信过滤 2833 行）；股票数：4975 只；唯一键 (ts_code, sector_id, effective_date) 重复 0
- 最近回放日有效成员合计：21067；缺失 29
- 最近回放日：缺失价格成员 29、缺失成交额 29、成交额=0（疑似停牌）0
- 全市场日线记录数：中位数 5511，最小 5458，最大 5553
- 数据质量门槛：data_quality_score < 0.6 或 valid_member_count < 5 判 DATA_INVALID

## 2. Sector Quality（复用 Step 2 sector_quality.csv）

| Tier | 数量 | master 规则 |
| --- | --- | --- |
| TIER_1_CORE | 33 | min_members=15, min_purity=0.7, min_stability=0.7 |
| TIER_2_ROTATION | 8 | min_members=10, min_purity=0.6, min_stability=0.6 |
| TIER_3_OBSERVATION | 9 | min_members=5, min_purity=-, min_stability=- |
| RAW_ONLY | 0 | min_members=0, min_purity=-, min_stability=- |

- sector_purity：中位数 0.8607，最低 0.3196，最高 0.9887
- theme_purity 直接复用 Step 2 的 sector_purity（0.30×行业一致性 + 0.25×产品一致性 + 0.20×核心占比 + 0.15×概念纯度 + 0.10×成员稳定性），未另造第二套质量算法。

## 3. Sector State 分布

| 状态 | 最近回放日 | 回放窗口内累计 |
| --- | --- | --- |
| STRONG | 0 | 12 |
| HEALTHY | 11 | 863 |
| NEUTRAL | 27 | 1728 |
| WEAK | 8 | 1337 |
| DETERIORATING | 4 | 560 |
| DATA_INVALID | 0 | 0 |

- 判定阈值：strong_breadth=0.55，strong_weighted_breadth=0.55，strong_core_breadth=0.5，strong_volume_ratio_5=1.1，strong_top5_concentration_max=0.7，healthy_min_positive_indicators=4，weak_breadth=-0.2，weak_breadth_deep=-0.35，deteriorating_breadth_drop=-0.35，anomaly_high_concentration=0.7，anomaly_low_breadth=-0.3，anomaly_core_weak=-0.1，anomaly_low_coverage_ratio=0.6，anomaly_membership_stability=0.6
- 状态确认平滑：原始判定为新状态且连续 2 个交易日成立，才写入 sector_state_base；双向对称（升级与降级同规则），用于抑制日频阈值抖动（原始判定在 72.5% 的观测日发生切换，平滑后降至 12.7%，平均状态持续期由 1.36 日升至 7.2 日）；对称规则不产生单向下偏。两条例外：(1) 已确认状态为 STRONG 而当日核心层广度 <0.50（STRONG 的核心层硬条件当日不成立）时立即降级为当日原始判定，保证【核心成员转弱时不得继续标注 STRONG】（需求 §三十二 H）；(2) DATA_INVALID 双向立即生效。当日原始判定保留在 sector_state_history.csv 的 state_raw 字段，未确认的原始判定在 state_reason 中显式说明。该规则只用当日及以前数据，不引入未来信息。

| 原始判定（平滑前） | 最近回放日 | 回放窗口内累计 |
| --- | --- | --- |
| STRONG | 14 | 181 |
| HEALTHY | 22 | 991 |
| NEUTRAL | 13 | 1535 |
| WEAK | 1 | 1158 |
| DETERIORATING | 0 | 635 |
| DATA_INVALID | 0 | 0 |
- core_breadth 退化规则：若某 Sector 无 CORE 层成员（core_valid_count=0），core_breadth 退化为 PRIMARY 层广度，并用 core_layer 字段标注实际使用的层，避免把【没有核心成员】误判为【核心成员弱】。

## 4. 状态变化

- 窗口内状态变化次数：601

| 迁移 | 次数 |
| --- | --- |
| WEAK → NEUTRAL | 101  |
| NEUTRAL → WEAK | 91  |
| WEAK → HEALTHY | 64  |
| HEALTHY → NEUTRAL | 58  |
| NEUTRAL → HEALTHY | 55  |
| HEALTHY → WEAK | 51 （非白名单，需人工复核） |
| NEUTRAL → DETERIORATING | 33 （非白名单，需人工复核） |
| DETERIORATING → NEUTRAL | 31 （非白名单，需人工复核） |
| DETERIORATING → HEALTHY | 29  |
| HEALTHY → DETERIORATING | 27  |
| WEAK → DETERIORATING | 24 （非白名单，需人工复核） |
| DETERIORATING → WEAK | 19  |
| STRONG → WEAK | 6 （非白名单，需人工复核） |
| NEUTRAL → STRONG | 3 （非白名单，需人工复核） |
| HEALTHY → STRONG | 3  |
| WEAK → STRONG | 2 （非白名单，需人工复核） |
| STRONG → HEALTHY | 2  |
| DETERIORATING → STRONG | 1 （非白名单，需人工复核） |
| STRONG → NEUTRAL | 1 （非白名单，需人工复核） |

- 最近回放日发生变化：4 个
  - T35 消费服务：WEAK → HEALTHY；广度 +0.38（上涨 148/下跌 65）；加权广度 +0.30；核心层 CORE 广度 +0.00；5日相对沪深300 +1.30pp；量能比(vs 过去5日均值) 1.17；成交额占比 3.08%（较5日前 +0.01pp）；Top5集中度 0.22；有效成员 221/221；数据质量 1.00
  - T36 创新药：DETERIORATING → HEALTHY；广度 +0.54（上涨 249/下跌 70）；加权广度 +0.52；核心层 CORE 广度 +0.40；5日相对沪深300 +3.98pp；量能比(vs 过去5日均值) 1.36；成交额占比 4.52%（较5日前 +0.99pp）；Top5集中度 0.11；有效成员 329/329；数据质量 1.00
  - T40 中药：DETERIORATING → HEALTHY；广度 +0.39（上涨 89/下跌 37）；加权广度 +0.39；核心层 CORE 广度 +0.38；5日相对沪深300 +1.32pp；量能比(vs 过去5日均值) 1.24；成交额占比 1.36%（较5日前 +0.06pp）；Top5集中度 0.21；有效成员 132/132；数据质量 1.00
  - T42 生物制品：DETERIORATING → HEALTHY；广度 +0.53（上涨 83/下跌 24）；加权广度 +0.62；核心层 CORE 广度 +1.00；5日相对沪深300 +3.37pp；量能比(vs 过去5日均值) 1.38；成交额占比 1.31%（较5日前 +0.30pp）；Top5集中度 0.35；有效成员 111/111；数据质量 1.00

## 5. 异常主题（最近回放日）

- **DATA_INVALID**（0）：无
- **LOW_COVERAGE**（0）：无
- **HIGH_CONCENTRATION**（0）：无
- **LOW_BREADTH**（1）：T23 银行
- **CORE_WEAK**（2）：T17 煤炭、T45 交通运输
- **MEMBERSHIP_UNSTABLE**（0）：无

## 6. 样例验证（最近回放日）

| Sector | 名称 | Tier | 有效成员 | 广度 | 加权广度 | 核心广度 | ew_ret_5 | vs沪深300(5日) | 成交额占比 | 量能比5 | Top5集中度 | Health | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 | 半导体 | TIER_1_CORE | 1030/1032 | +0.77 | +0.80 | +0.92 | +6.14% | +6.20pp | 50.54% | 1.28 | 0.03 | 86.9 | WEAK |
| T02 | AI算力 | TIER_2_ROTATION | 817/817 | +0.71 | +0.72 | +0.78 | +4.29% | +4.35pp | 38.84% | 1.21 | 0.04 | 81.8 | NEUTRAL |
| T03 | 通信 | TIER_1_CORE | 463/463 | +0.70 | +0.69 | +0.63 | +4.38% | +4.44pp | 27.89% | 1.20 | 0.06 | 80.4 | WEAK |
| T07 | 新能源汽车 | TIER_3_OBSERVATION | 1180/1182 | +0.57 | +0.56 | NA | +3.43% | +3.49pp | 30.85% | 1.17 | 0.03 | 71.2 | NEUTRAL |
| T08 | 锂电产业链 | TIER_2_ROTATION | 790/792 | +0.62 | +0.64 | +0.70 | +3.71% | +3.77pp | 19.46% | 1.12 | 0.05 | 76.5 | NEUTRAL |
| T10 | 风电产业链 | TIER_3_OBSERVATION | 562/562 | +0.63 | +0.63 | +0.39 | +1.59% | +1.65pp | 12.44% | 1.08 | 0.10 | 71.1 | NEUTRAL |
| T23 | 银行 | TIER_1_CORE | 42/42 | -0.36 | -0.35 | -0.30 | -1.93% | -1.87pp | 1.16% | 0.92 | 0.24 | 34.9 | WEAK |
| T24 | 券商 | TIER_1_CORE | 52/55 | +1.00 | +1.00 | +1.00 | +0.38% | +0.45pp | 1.09% | 1.05 | 0.38 | 79.9 | DETERIORATING |
| T29 | 食品饮料 | TIER_1_CORE | 174/174 | +0.39 | +0.38 | +0.68 | +0.02% | +0.08pp | 1.75% | 1.03 | 0.23 | 61.6 | NEUTRAL |
| T36 | 创新药 | TIER_1_CORE | 329/329 | +0.54 | +0.52 | +0.40 | +3.92% | +3.98pp | 4.52% | 1.36 | 0.11 | 77.2 | HEALTHY |
| T37 | 医疗器械 | TIER_1_CORE | 427/427 | +0.62 | +0.63 | +0.68 | +3.35% | +3.41pp | 7.07% | 1.28 | 0.10 | 79.2 | HEALTHY |
| T43 | 电力 | TIER_1_CORE | 729/729 | +0.57 | +0.58 | +0.63 | +1.06% | +1.12pp | 12.44% | 1.06 | 0.07 | 69.2 | WEAK |
| T50 | 央国企基建 | TIER_3_OBSERVATION | 1425/1432 | +0.46 | +0.46 | NA | +0.08% | +0.14pp | 26.12% | 1.07 | 0.03 | 61.4 | NEUTRAL |

成员/广度/核心成员/主题收益/成交量/状态的逐项核对依据（均由可验证字段拼装，不含主观判断）：

- **T01 半导体**（WEAK）：广度 +0.77（上涨 910/下跌 114）；加权广度 +0.80；核心层 CORE 广度 +0.92；5日相对沪深300 +6.20pp；量能比(vs 过去5日均值) 1.28；成交额占比 50.54%（较5日前 +6.96pp）；Top5集中度 0.03；有效成员 1030/1032；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T02 AI算力**（NEUTRAL）：广度 +0.71（上涨 692/下跌 111）；加权广度 +0.72；核心层 PRIMARY 广度 +0.78；5日相对沪深300 +4.35pp；量能比(vs 过去5日均值) 1.21；成交额占比 38.84%（较5日前 +3.05pp）；Top5集中度 0.04；有效成员 817/817；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 NEUTRAL（原始判定见 state_raw 字段）
- **T03 通信**（WEAK）：广度 +0.70（上涨 392/下跌 69）；加权广度 +0.69；核心层 PRIMARY 广度 +0.63；5日相对沪深300 +4.44pp；量能比(vs 过去5日均值) 1.20；成交额占比 27.89%（较5日前 +1.25pp）；Top5集中度 0.06；有效成员 463/463；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T07 新能源汽车**（NEUTRAL）：广度 +0.57（上涨 916/下跌 241）；加权广度 +0.56；核心层 NA 广度 NA（该 Sector 无 CORE/PRIMARY 层成员）；5日相对沪深300 +3.49pp；量能比(vs 过去5日均值) 1.17；成交额占比 30.85%（较5日前 +0.83pp）；Top5集中度 0.03；有效成员 1180/1182；数据质量 1.00
- **T08 锂电产业链**（NEUTRAL）：广度 +0.62（上涨 634/下跌 143）；加权广度 +0.64；核心层 CORE 广度 +0.70；5日相对沪深300 +3.77pp；量能比(vs 过去5日均值) 1.12；成交额占比 19.46%（较5日前 +0.10pp）；Top5集中度 0.05；有效成员 790/792；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 NEUTRAL（原始判定见 state_raw 字段）
- **T10 风电产业链**（NEUTRAL）：广度 +0.63（上涨 450/下跌 98）；加权广度 +0.63；核心层 CORE 广度 +0.39；5日相对沪深300 +1.65pp；量能比(vs 过去5日均值) 1.08；成交额占比 12.44%（较5日前 -0.91pp）；Top5集中度 0.10；有效成员 562/562；数据质量 1.00；平滑：当日原始判定为 HEALTHY，尚未连续 2 个交易日成立，暂维持 NEUTRAL（原始判定见 state_raw 字段）
- **T23 银行**（WEAK）：广度 -0.36（上涨 12/下跌 27）；加权广度 -0.35；核心层 CORE 广度 -0.30；5日相对沪深300 -1.87pp；量能比(vs 过去5日均值) 0.92；成交额占比 1.16%（较5日前 -0.38pp）；Top5集中度 0.24；有效成员 42/42；数据质量 1.00；标记=LOW_BREADTH
- **T24 券商**（DETERIORATING）：广度 +1.00（上涨 52/下跌 0）；加权广度 +1.00；核心层 CORE 广度 +1.00；5日相对沪深300 +0.45pp；量能比(vs 过去5日均值) 1.05；成交额占比 1.09%（较5日前 -0.77pp）；Top5集中度 0.38；有效成员 52/55；数据质量 0.96；平滑：当日原始判定为 HEALTHY，尚未连续 2 个交易日成立，暂维持 DETERIORATING（原始判定见 state_raw 字段）
- **T29 食品饮料**（NEUTRAL）：广度 +0.39（上涨 118/下跌 50）；加权广度 +0.38；核心层 CORE 广度 +0.68；5日相对沪深300 +0.08pp；量能比(vs 过去5日均值) 1.03；成交额占比 1.75%（较5日前 -0.52pp）；Top5集中度 0.23；有效成员 174/174；数据质量 1.00；平滑：当日原始判定为 HEALTHY，尚未连续 2 个交易日成立，暂维持 NEUTRAL（原始判定见 state_raw 字段）
- **T36 创新药**（HEALTHY）：广度 +0.54（上涨 249/下跌 70）；加权广度 +0.52；核心层 CORE 广度 +0.40；5日相对沪深300 +3.98pp；量能比(vs 过去5日均值) 1.36；成交额占比 4.52%（较5日前 +0.99pp）；Top5集中度 0.11；有效成员 329/329；数据质量 1.00
- **T37 医疗器械**（HEALTHY）：广度 +0.62（上涨 340/下跌 76）；加权广度 +0.63；核心层 CORE 广度 +0.68；5日相对沪深300 +3.41pp；量能比(vs 过去5日均值) 1.28；成交额占比 7.07%（较5日前 +0.79pp）；Top5集中度 0.10；有效成员 427/427；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 HEALTHY（原始判定见 state_raw 字段）
- **T43 电力**（WEAK）：广度 +0.57（上涨 555/下跌 141）；加权广度 +0.58；核心层 PRIMARY 广度 +0.63；5日相对沪深300 +1.12pp；量能比(vs 过去5日均值) 1.06；成交额占比 12.44%（较5日前 -1.62pp）；Top5集中度 0.07；有效成员 729/729；数据质量 1.00；平滑：当日原始判定为 HEALTHY，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T50 央国企基建**（NEUTRAL）：广度 +0.46（上涨 1007/下跌 357）；加权广度 +0.46；核心层 NA 广度 NA（该 Sector 无 CORE/PRIMARY 层成员）；5日相对沪深300 +0.14pp；量能比(vs 过去5日均值) 1.07；成交额占比 26.12%（较5日前 -4.80pp）；Top5集中度 0.03；有效成员 1425/1432；数据质量 1.00

## 7. Validation（需求 §三十二 A~H）

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| A_SECTOR_ID | PASS | 非 master 声明的 sector_id：无（master sectors=50，输出涉及 50 个） |
| B_MEMBERSHIP_UNIQUE | PASS | (ts_code, sector_id, effective_date) 重复行数=0 |
| C_EFFECTIVE_DATE | PASS | effective_date > 基准日(20260918) 行数=0；strict 模式按 effective_date<=D 过滤；snapshot 模式按静态定义回放并在输出中标注 STATIC_SNAPSHOT |
| D_TRADE_DATE | PASS | 输出日期 90 个，全部来自交易日序列；非法日期无 |
| E_NO_LOOKAHEAD | PASS | 对 2 个中间日期（20260629, 20260810）把行情面板物理截断到当日为止（各保留 30 个交易日，D+1 及以后数据不参与计算），重算后共比对 100 个 (日期 × Sector) 组合的 ew_ret_5/ew_ret_20/breadth/weighted_breadth/volume_ratio_5/sector_amount_share/sector_health，与完整回放完全一致，未发现使用 D+1 及以后数据的路径 |
| F_PRICE_INDEPENDENCE | PASS | membership 列未包含任何价格/涨停/热度字段（命中：无）；本阶段只读 membership，不增删任何成员 |
| G_CONCENTRATION | PASS | top5_concentration>0.7 行数=1，已标记 HIGH_CONCENTRATION 行数=1，集中度缺失=0 |
| H_CORE_NOT_WEAK_STRONG | PASS | STRONG 且 core_breadth<0.5 行数=0 |

- 汇总：8/8 项通过。

## 8. 未来函数与实现说明

- 截断重算检验：通过 — 对 2 个中间日期（20260629, 20260810）把行情面板物理截断到当日为止（各保留 30 个交易日，D+1 及以后数据不参与计算），重算后共比对 100 个 (日期 × Sector) 组合的 ew_ret_5/ew_ret_20/breadth/weighted_breadth/volume_ratio_5/sector_amount_share/sector_health，与完整回放完全一致，未发现使用 D+1 及以后数据的路径
- 收益口径：Tushare daily.pct_chg 连乘累计（pre_close 已含除权除息调整），等效复权收益，未再叠加 adj_factor。
- 所有滚动窗（rolling / shift）均为后向窗；跨日派生量按 sector 自身时间序列生成，无前视窗口。
- membership 在 strict 模式按 effective_date <= D 过滤；snapshot 模式按静态定义回放并标注。
- 本阶段不读取 theme_config.json / subtheme_map.json / theme.json，不继承任何旧 theme_id / 成员 / 核心公司 / 排名 / 规则。

## 9. 本阶段边界（需求 §三十七）

只输出 Sector Quality / Breadth / Structure / Health / State Base 与 Tracking Pool；
不实现 SEOS、板块启动与轮动预测、生命周期预测、HVT、IGE、F120、个股 BUY/NO TRADE、交易执行。

## 10. 输出文件

- data/sector_daily_stats.csv
- data/sector_breadth.csv
- data/sector_strength.csv
- data/sector_volume.csv
- data/sector_state_history.csv
- output/sector_state_today.json
- output/sector_tracking_pool.json
- output/sector_quality_daily.csv
- output/sector_state_audit.md
- output/sector_state_validation.csv
