# Sector State Base 审计报告（Step 3）

- 生成时间：2026-09-16 22:02:29
- 基准日：20260916｜最近回放日：20260916
- Canonical 真源：sector_master.json（version=2.0，sectors=50）
- 成员口径：membership_confidence >= 0.7（不限 membership_type，用户决策 B）
- 成员模式：snapshot（STATIC_SNAPSHOT：Step 2 为单一快照，按静态产业定义回放，非时点成员回放）
- 回放窗口：20260513 ~ 20260916（90 个交易日；行情面板 20260402~20260916）
- 基准：000300.SH 沪深300（vs_market_*）、000852.SH 中证1000（vs_midcap_*）
- Sector Health 权重：breadth=0.25，weighted_breadth=0.20，relative_strength=0.15，volume_structure=0.15，core_primary=0.15，consistency=0.10；core_primary 内部 core_breadth=0.5，primary_breadth=0.3，core_rs=0.2
- return 驱动权重合计：0.28（需求 §十六 要求 <= 0.30：满足）
- LEGACY 配置（只报告存在性、未读取内容）：theme.json、theme_config.json、bak0615\theme.json、multi_factor_picker\cache\theme.json、theme_kg_v3\theme_kg_v3\config\subtheme_map.json、theme_kg_v3\theme_kg_v3\config\theme_config.json

## 1. 数据覆盖

- 回放交易日：90 个
- Sector 数：master 50 个；输出涉及 50 个
- 最近回放日有效 Sector（valid_member_count>0）：50 个
- membership 行数：21079（口径 confidence>=0.7；源文件 23908 行，低置信过滤 2829 行）；股票数：4970 只；唯一键 (ts_code, sector_id, effective_date) 重复 0
- 最近回放日有效成员合计：21038；缺失 41
- 最近回放日：缺失价格成员 41、缺失成交额 41、成交额=0（疑似停牌）0
- 全市场日线记录数：中位数 5511，最小 5456，最大 5550
- 数据质量门槛：data_quality_score < 0.6 或 valid_member_count < 5 判 DATA_INVALID

## 2. Sector Quality（复用 Step 2 sector_quality.csv）

| Tier | 数量 | master 规则 |
| --- | --- | --- |
| TIER_1_CORE | 33 | min_members=15, min_purity=0.7, min_stability=0.7 |
| TIER_2_ROTATION | 8 | min_members=10, min_purity=0.6, min_stability=0.6 |
| TIER_3_OBSERVATION | 9 | min_members=5, min_purity=-, min_stability=- |
| RAW_ONLY | 0 | min_members=0, min_purity=-, min_stability=- |

- sector_purity：中位数 0.8607，最低 0.3197，最高 0.9887
- theme_purity 直接复用 Step 2 的 sector_purity（0.30×行业一致性 + 0.25×产品一致性 + 0.20×核心占比 + 0.15×概念纯度 + 0.10×成员稳定性），未另造第二套质量算法。

## 3. Sector State 分布

| 状态 | 最近回放日 | 回放窗口内累计 |
| --- | --- | --- |
| STRONG | 0 | 12 |
| HEALTHY | 3 | 890 |
| NEUTRAL | 13 | 1746 |
| WEAK | 24 | 1289 |
| DETERIORATING | 10 | 563 |
| DATA_INVALID | 0 | 0 |

- 判定阈值：strong_breadth=0.55，strong_weighted_breadth=0.55，strong_core_breadth=0.5，strong_volume_ratio_5=1.1，strong_top5_concentration_max=0.7，healthy_min_positive_indicators=4，weak_breadth=-0.2，weak_breadth_deep=-0.35，deteriorating_breadth_drop=-0.35，anomaly_high_concentration=0.7，anomaly_low_breadth=-0.3，anomaly_core_weak=-0.1，anomaly_low_coverage_ratio=0.6，anomaly_membership_stability=0.6
- 状态确认平滑：原始判定为新状态且连续 2 个交易日成立，才写入 sector_state_base；双向对称（升级与降级同规则），用于抑制日频阈值抖动（原始判定在 72.5% 的观测日发生切换，平滑后降至 12.7%，平均状态持续期由 1.36 日升至 7.2 日）；对称规则不产生单向下偏。两条例外：(1) 已确认状态为 STRONG 而当日核心层广度 <0.50（STRONG 的核心层硬条件当日不成立）时立即降级为当日原始判定，保证【核心成员转弱时不得继续标注 STRONG】（需求 §三十二 H）；(2) DATA_INVALID 双向立即生效。当日原始判定保留在 sector_state_history.csv 的 state_raw 字段，未确认的原始判定在 state_reason 中显式说明。该规则只用当日及以前数据，不引入未来信息。

| 原始判定（平滑前） | 最近回放日 | 回放窗口内累计 |
| --- | --- | --- |
| STRONG | 7 | 167 |
| HEALTHY | 12 | 974 |
| NEUTRAL | 30 | 1522 |
| WEAK | 1 | 1191 |
| DETERIORATING | 0 | 646 |
| DATA_INVALID | 0 | 0 |
- core_breadth 退化规则：若某 Sector 无 CORE 层成员（core_valid_count=0），core_breadth 退化为 PRIMARY 层广度，并用 core_layer 字段标注实际使用的层，避免把【没有核心成员】误判为【核心成员弱】。

## 4. 状态变化

- 窗口内状态变化次数：602

| 迁移 | 次数 |
| --- | --- |
| NEUTRAL → WEAK | 107  |
| WEAK → NEUTRAL | 87  |
| HEALTHY → NEUTRAL | 61  |
| WEAK → HEALTHY | 60  |
| HEALTHY → WEAK | 60 （非白名单，需人工复核） |
| NEUTRAL → HEALTHY | 50  |
| NEUTRAL → DETERIORATING | 33 （非白名单，需人工复核） |
| DETERIORATING → NEUTRAL | 30 （非白名单，需人工复核） |
| HEALTHY → DETERIORATING | 27  |
| WEAK → DETERIORATING | 25 （非白名单，需人工复核） |
| DETERIORATING → HEALTHY | 25  |
| DETERIORATING → WEAK | 19  |
| STRONG → WEAK | 6 （非白名单，需人工复核） |
| NEUTRAL → STRONG | 3 （非白名单，需人工复核） |
| HEALTHY → STRONG | 3  |
| WEAK → STRONG | 2 （非白名单，需人工复核） |
| STRONG → HEALTHY | 2  |
| DETERIORATING → STRONG | 1 （非白名单，需人工复核） |
| STRONG → NEUTRAL | 1 （非白名单，需人工复核） |

- 最近回放日发生变化：1 个
  - T23 银行：NEUTRAL → WEAK；广度 -0.79（上涨 4/下跌 37）；加权广度 -0.80；核心层 CORE 广度 -0.93；5日相对沪深300 +1.22pp；量能比(vs 过去5日均值) 0.96；成交额占比 1.35%（较5日前 +0.32pp）；Top5集中度 0.27；有效成员 42/42；数据质量 1.00；标记=LOW_BREADTH;CORE_WEAK

## 5. 异常主题（最近回放日）

- **DATA_INVALID**（0）：无
- **LOW_COVERAGE**（0）：无
- **HIGH_CONCENTRATION**（0）：无
- **LOW_BREADTH**（1）：T23 银行
- **CORE_WEAK**（7）：T10 风电产业链、T19 钢铁、T23 银行、T25 保险、T34 旅游酒店餐饮、T40 中药、T45 交通运输
- **MEMBERSHIP_UNSTABLE**（0）：无

## 6. 样例验证（最近回放日）

| Sector | 名称 | Tier | 有效成员 | 广度 | 加权广度 | 核心广度 | ew_ret_5 | vs沪深300(5日) | 成交额占比 | 量能比5 | Top5集中度 | Health | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 | 半导体 | TIER_1_CORE | 1029/1031 | +0.80 | +0.82 | +1.00 | +1.33% | +3.35pp | 48.80% | 1.19 | 0.04 | 83.1 | WEAK |
| T02 | AI算力 | TIER_2_ROTATION | 816/816 | +0.72 | +0.72 | +0.72 | +0.39% | +2.41pp | 38.49% | 1.13 | 0.05 | 74.8 | NEUTRAL |
| T03 | 通信 | TIER_1_CORE | 462/462 | +0.80 | +0.78 | +0.66 | +1.25% | +3.27pp | 28.03% | 1.14 | 0.07 | 78.5 | WEAK |
| T07 | 新能源汽车 | TIER_3_OBSERVATION | 1178/1180 | +0.64 | +0.63 | NA | -1.45% | +0.57pp | 31.69% | 1.11 | 0.03 | 65.2 | WEAK |
| T08 | 锂电产业链 | TIER_2_ROTATION | 790/792 | +0.71 | +0.71 | +0.63 | -1.13% | +0.89pp | 21.42% | 1.16 | 0.04 | 71.2 | NEUTRAL |
| T10 | 风电产业链 | TIER_3_OBSERVATION | 560/561 | +0.51 | +0.49 | -0.12 | -1.56% | +0.46pp | 12.86% | 1.03 | 0.07 | 61.9 | WEAK |
| T23 | 银行 | TIER_1_CORE | 42/42 | -0.79 | -0.80 | -0.93 | -0.80% | +1.22pp | 1.35% | 0.96 | 0.27 | 29.3 | WEAK |
| T24 | 券商 | TIER_1_CORE | 52/55 | +0.73 | +0.72 | +0.70 | -3.01% | -0.99pp | 1.14% | 0.93 | 0.24 | 65.3 | DETERIORATING |
| T29 | 食品饮料 | TIER_1_CORE | 174/174 | +0.11 | +0.12 | -0.05 | -6.27% | -4.25pp | 1.82% | 0.81 | 0.09 | 40.4 | WEAK |
| T36 | 创新药 | TIER_1_CORE | 327/327 | +0.41 | +0.42 | +0.40 | -3.00% | -0.98pp | 3.57% | 0.92 | 0.05 | 57.5 | DETERIORATING |
| T37 | 医疗器械 | TIER_1_CORE | 427/427 | +0.55 | +0.54 | +0.48 | -2.29% | -0.27pp | 6.20% | 1.02 | 0.05 | 63.6 | DETERIORATING |
| T43 | 电力 | TIER_1_CORE | 728/729 | +0.47 | +0.44 | +0.14 | -2.00% | +0.02pp | 13.44% | 1.04 | 0.06 | 60.9 | WEAK |
| T50 | 央国企基建 | TIER_3_OBSERVATION | 1425/1432 | +0.39 | +0.39 | NA | -3.47% | -1.45pp | 26.90% | 0.95 | 0.03 | 53.8 | WEAK |

成员/广度/核心成员/主题收益/成交量/状态的逐项核对依据（均由可验证字段拼装，不含主观判断）：

- **T01 半导体**（WEAK）：广度 +0.80（上涨 921/下跌 97）；加权广度 +0.82；核心层 CORE 广度 +1.00；5日相对沪深300 +3.35pp；量能比(vs 过去5日均值) 1.19；成交额占比 48.80%（较5日前 +7.30pp）；Top5集中度 0.04；有效成员 1029/1031；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T02 AI算力**（NEUTRAL）：广度 +0.72（上涨 694/下跌 107）；加权广度 +0.72；核心层 PRIMARY 广度 +0.72；5日相对沪深300 +2.41pp；量能比(vs 过去5日均值) 1.13；成交额占比 38.49%（较5日前 +2.90pp）；Top5集中度 0.05；有效成员 816/816；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 NEUTRAL（原始判定见 state_raw 字段）
- **T03 通信**（WEAK）：广度 +0.80（上涨 414/下跌 43）；加权广度 +0.78；核心层 PRIMARY 广度 +0.66；5日相对沪深300 +3.27pp；量能比(vs 过去5日均值) 1.14；成交额占比 28.03%（较5日前 +2.07pp）；Top5集中度 0.07；有效成员 462/462；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T07 新能源汽车**（WEAK）：广度 +0.64（上涨 953/下跌 197）；加权广度 +0.63；核心层 NA 广度 NA（该 Sector 无 CORE/PRIMARY 层成员）；5日相对沪深300 +0.57pp；量能比(vs 过去5日均值) 1.11；成交额占比 31.69%（较5日前 +1.67pp）；Top5集中度 0.03；有效成员 1178/1180；数据质量 1.00；平滑：当日原始判定为 NEUTRAL，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T08 锂电产业链**（NEUTRAL）：广度 +0.71（上涨 666/下跌 107）；加权广度 +0.71；核心层 CORE 广度 +0.63；5日相对沪深300 +0.89pp；量能比(vs 过去5日均值) 1.16；成交额占比 21.42%（较5日前 +2.00pp）；Top5集中度 0.04；有效成员 790/792；数据质量 1.00；平滑：当日原始判定为 STRONG，尚未连续 2 个交易日成立，暂维持 NEUTRAL（原始判定见 state_raw 字段）
- **T10 风电产业链**（WEAK）：广度 +0.51（上涨 414/下跌 128）；加权广度 +0.49；核心层 CORE 广度 -0.12；5日相对沪深300 +0.46pp；量能比(vs 过去5日均值) 1.03；成交额占比 12.86%（较5日前 +0.37pp）；Top5集中度 0.07；有效成员 560/561；数据质量 1.00；标记=CORE_WEAK；平滑：当日原始判定为 NEUTRAL，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T23 银行**（WEAK）：广度 -0.79（上涨 4/下跌 37）；加权广度 -0.80；核心层 CORE 广度 -0.93；5日相对沪深300 +1.22pp；量能比(vs 过去5日均值) 0.96；成交额占比 1.35%（较5日前 +0.32pp）；Top5集中度 0.27；有效成员 42/42；数据质量 1.00；标记=LOW_BREADTH;CORE_WEAK
- **T24 券商**（DETERIORATING）：广度 +0.73（上涨 43/下跌 5）；加权广度 +0.72；核心层 CORE 广度 +0.70；5日相对沪深300 -0.99pp；量能比(vs 过去5日均值) 0.93；成交额占比 1.14%（较5日前 +0.04pp）；Top5集中度 0.24；有效成员 52/55；数据质量 0.96；平滑：当日原始判定为 NEUTRAL，尚未连续 2 个交易日成立，暂维持 DETERIORATING（原始判定见 state_raw 字段）
- **T29 食品饮料**（WEAK）：广度 +0.11（上涨 94/下跌 74）；加权广度 +0.12；核心层 CORE 广度 -0.05；5日相对沪深300 -4.25pp；量能比(vs 过去5日均值) 0.81；成交额占比 1.82%（较5日前 -0.94pp）；Top5集中度 0.09；有效成员 174/174；数据质量 1.00；平滑：当日原始判定为 NEUTRAL，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T36 创新药**（DETERIORATING）：广度 +0.41（上涨 224/下跌 89）；加权广度 +0.42；核心层 CORE 广度 +0.40；5日相对沪深300 -0.98pp；量能比(vs 过去5日均值) 0.92；成交额占比 3.57%（较5日前 -0.77pp）；Top5集中度 0.05；有效成员 327/327；数据质量 1.00；平滑：当日原始判定为 NEUTRAL，尚未连续 2 个交易日成立，暂维持 DETERIORATING（原始判定见 state_raw 字段）
- **T37 医疗器械**（DETERIORATING）：广度 +0.55（上涨 324/下跌 88）；加权广度 +0.54；核心层 CORE 广度 +0.48；5日相对沪深300 -0.27pp；量能比(vs 过去5日均值) 1.02；成交额占比 6.20%（较5日前 -0.18pp）；Top5集中度 0.05；有效成员 427/427；数据质量 1.00；平滑：当日原始判定为 HEALTHY，尚未连续 2 个交易日成立，暂维持 DETERIORATING（原始判定见 state_raw 字段）
- **T43 电力**（WEAK）：广度 +0.47（上涨 522/下跌 177）；加权广度 +0.44；核心层 PRIMARY 广度 +0.14；5日相对沪深300 +0.02pp；量能比(vs 过去5日均值) 1.04；成交额占比 13.44%（较5日前 +0.57pp）；Top5集中度 0.06；有效成员 728/729；数据质量 1.00；平滑：当日原始判定为 HEALTHY，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）
- **T50 央国企基建**（WEAK）：广度 +0.39（上涨 950/下跌 395）；加权广度 +0.39；核心层 NA 广度 NA（该 Sector 无 CORE/PRIMARY 层成员）；5日相对沪深300 -1.45pp；量能比(vs 过去5日均值) 0.95；成交额占比 26.90%（较5日前 -3.12pp）；Top5集中度 0.03；有效成员 1425/1432；数据质量 1.00；平滑：当日原始判定为 NEUTRAL，尚未连续 2 个交易日成立，暂维持 WEAK（原始判定见 state_raw 字段）

## 7. Validation（需求 §三十二 A~H）

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| A_SECTOR_ID | PASS | 非 master 声明的 sector_id：无（master sectors=50，输出涉及 50 个） |
| B_MEMBERSHIP_UNIQUE | PASS | (ts_code, sector_id, effective_date) 重复行数=0 |
| C_EFFECTIVE_DATE | PASS | effective_date > 基准日(20260916) 行数=0；strict 模式按 effective_date<=D 过滤；snapshot 模式按静态定义回放并在输出中标注 STATIC_SNAPSHOT |
| D_TRADE_DATE | PASS | 输出日期 90 个，全部来自交易日序列；非法日期无 |
| E_NO_LOOKAHEAD | PASS | 对 2 个中间日期（20260625, 20260806）把行情面板物理截断到当日为止（各保留 30 个交易日，D+1 及以后数据不参与计算），重算后共比对 100 个 (日期 × Sector) 组合的 ew_ret_5/ew_ret_20/breadth/weighted_breadth/volume_ratio_5/sector_amount_share/sector_health，与完整回放完全一致，未发现使用 D+1 及以后数据的路径 |
| F_PRICE_INDEPENDENCE | PASS | membership 列未包含任何价格/涨停/热度字段（命中：无）；本阶段只读 membership，不增删任何成员 |
| G_CONCENTRATION | PASS | top5_concentration>0.7 行数=1，已标记 HIGH_CONCENTRATION 行数=1，集中度缺失=0 |
| H_CORE_NOT_WEAK_STRONG | PASS | STRONG 且 core_breadth<0.5 行数=0 |

- 汇总：8/8 项通过。

## 8. 未来函数与实现说明

- 截断重算检验：通过 — 对 2 个中间日期（20260625, 20260806）把行情面板物理截断到当日为止（各保留 30 个交易日，D+1 及以后数据不参与计算），重算后共比对 100 个 (日期 × Sector) 组合的 ew_ret_5/ew_ret_20/breadth/weighted_breadth/volume_ratio_5/sector_amount_share/sector_health，与完整回放完全一致，未发现使用 D+1 及以后数据的路径
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
