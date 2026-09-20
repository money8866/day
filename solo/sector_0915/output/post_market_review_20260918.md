# Step 8 盘后复盘 · 20260918

- signal_date（被复盘信号日）：**20260917**；review_date（复盘日）：**20260918**
- 模式：**READ ONLY**（不修改 Step 1-7 的任何落盘结果；不重新选股；不产生 BUY）
- 最终状态：**POST_MARKET_REVIEW_READY**

## 核心摘要
- **MARKET** — 市场：高波动（健康环境）；breadth 0.76，较前一日改善 29.86%；涨停 82 / 跌停 1；成交额 20929 亿。
- **THEME** — 主线：T36 创新药、T38 CXO、T35 消费服务；强化：T36 创新药（breadth/health 改善）、T42 生物制品（breadth/health 改善）；退潮：无
- **SIGNAL** — 昨日信号：侯选 1410 / 结构合格 317 / 执行合格 110；Step7：BUY 4 / 未交易 1343
- **TRADE** — 昨日 BUY：1 触发、0 走强、0 跌破 Stop、3 未成交；未交易中潜在漏掉 331
- **FEEDBACK** — 复盘：执行层增量价值 中性（BUY 减结构合格 T+1 = 0.10%）；过滤增量价值 低；无实质性模型错误；待独立验证的模型改动候选 2 项（长期指标 §五十二）；明日保留 30 个条件观察对象（WATCH ≠ BUY）

## 1. 市场

高波动（健康环境）；breadth 0.76，较前一日改善 29.86%；涨停 82 / 跌停 1；成交额 20929 亿。

| 项目 | 数值 | 项目 | 数值 |
| --- | --- | --- | --- |
| 上证 | 0.94% | 沪深300 | 1.06% |
| 中证1000 | 1.84% | 中证2000 | 1.77% |
| 成交额 | 20929 亿 | 成交额/20日均 | 1.09 |
| 上涨/下跌/平盘 | 4234/1152/167 | Breadth | 0.7625 |
| 涨停/跌停 | 82/1 | 炸板 | 27 |
| 最高连板 | 4 | 赚钱效应 | 赚钱效应中性 |
| 结构标签 | HIGH_VOLATILITY | 次级标签 | BROAD_RALLY |
| 背离标记 | - | Regime | HEALTHY（NEUTRAL→HEALTHY） |

Regime 变化依据：breadth 0.4639→0.7625；成交额变化 13.96%；涨停 82 / 跌停 1；指数结构 SSE 0.94%（依据 breadth / volume / amount / limit-up-down / index structure，§六）
Regime 覆盖：已观测 HEALTHY|NEUTRAL|RISK_OFF|WEAK；缺档（如实为 0）STRONG

## 2. 主题（最多 5 个）

| 主题 | Phase（前→后） | SEOS | Health | Breadth | Core Breadth | Amount Share | 今日变化 | 分组 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T36 创新药 | DORMANT→DORMANT | 62.22 | 69.03 | 0.526 | 0.583 | 0.0405 | health 11.570 / breadth 0.113 | IMPROVING|FAILED_STARTUP |
| T38 CXO | DORMANT→DORMANT | 61.61 | 69.80 | 0.542 | - | 0.0222 | health 1.840 / breadth -0.208 | - |
| T35 消费服务 | DORMANT→DORMANT | 59.80 | 56.06 | 0.136 | 1.000 | 0.0314 | health -4.760 / breadth -0.398 | - |
| T15 汽车及零部件 | DORMANT→EMERGING | 66.37 | 59.96 | 0.070 | 0.571 | 0.2494 | health -7.120 / breadth -0.563 | - |
| T42 生物制品 | DETERIORATING→EARLY | 57.18 | 67.92 | 0.536 | 0.583 | 0.0107 | health 14.080 / breadth 0.255 | IMPROVING|FAILED_STARTUP |

### 主题轮动（最多展示 5 个）

- T39 医疗服务：ROTATION_IN（参照窗 Breadth -0.1872 偏弱，近5日 Breadth 1.2282、Core 0.7857、RS turn 0.0274 同步改善）
- T24 券商：ROTATION_OUT（参照窗 Breadth 0.2382 偏强，近3日 Breadth -0.6129、Core -0.6336、RS turn -0.0214 转弱）

扩散复盘：T36 NO_DIFFUSION→FULL_BREADTH；T38 NO_DIFFUSION→FAILED_DIFFUSION；T35 EARLY_DIFFUSION→HEALTHY_EXPANSION；T15 NO_DIFFUSION→FULL_BREADTH；T42 NO_DIFFUSION→CORE_ONLY

## 3. 今日结构池（最多 12 只）

| 股票 | 主题 | structure_class | hvt_state | T+1 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 002516.SZ 旷达科技 | T15 | HVT_LOCKING | HVT_LOCKING | 0.17% | failed |
| 301086.SZ 鸿富瀚 | T05 | HVT_LOCKING | HVT_LOCKING | 6.40% | failed |
| 301234.SZ 五洲医疗 | T37 | HVT_LOCKING | HVT_LOCKING | 2.65% | confirmed |
| 688002.SH 睿创微纳 | T01 | HVT_LOCKING | HVT_LOCKING | 1.25% | confirmed |
| 688601.SH 力芯微 | T01 | HVT_LOCKING | HVT_LOCKING | 3.63% | failed |
| 000980.SZ 众泰汽车 | T15 | HVT_EVENT | HVT_EVENT | -3.38% | failed |
| 600609.SH 金杯汽车 | T15 | HVT_EVENT | HVT_EVENT | 0.28% | failed |
| 603042.SH 华脉科技 | T03 | HVT_EVENT | HVT_EVENT | -2.20% | failed |
| 603236.SH 移远通信 | T03 | HVT_EVENT | HVT_EVENT | -1.52% | failed |
| 603266.SH 天龙股份 | T15 | HVT_EVENT | HVT_EVENT | -4.19% | failed |
| 605088.SH 冠盛股份 | T15 | HVT_EVENT | HVT_EVENT | 0.12% | failed |
| 001319.SZ 铭科精技 | T15 | HVT_ADJUSTING | HVT_ADJUSTING | 0.82% | failed |

## 4. 昨日交易复盘

### BUY 复盘（4 只）

| 股票 | Entry | Trigger | 今日 O/H/L/C | MAE(T+1) | MFE(T+1) | 结果 | 原因 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 300183.SZ 东软载波 | 11.79~11.98 | 11.79 | 10.87/11.11/10.80/10.91 | -9.85% | -7.26% | 未进入 Entry 区间 | entry_zone 11.79~11.98；today low 10.8 / high 11.11；touch=False；close 10. |
| 300563.SZ 神宇股份 | 29.98~30.84 | 29.98 | 26.65/27.85/26.23/27.02 | -14.95% | -9.70% | 未进入 Entry 区间 | entry_zone 29.98~30.84；today low 26.23 / high 27.85；touch=False；close 27 |
| 600104.SH 上汽集团 | 11.89~12.01 | 11.89 | 11.16/11.22/11.04/11.10 | -8.08% | -6.58% | 未进入 Entry 区间 | entry_zone 11.89~12.01；today low 11.04 / high 11.22；touch=False；close 11 |
| 688395.SH 正弦电气 | 24.13~24.51 | 24.13 | 23.64/24.24/23.58/24.06 | -3.79% | -1.10% | 已触发、表现平淡 | entry_zone 24.13~24.51；today low 23.58 / high 24.24；touch=True；close 24. |

### NO TRADE 复盘（1343 条）

- 分类：NO_TRADE_DUE_TO_STRUCTURE 1326；UNMAPPED 12；NO_TRADE_DUE_TO_RR 5
- 结果：NEUTRAL_OUTCOME 691；MISSED_OPPORTUNITY 331；CORRECT_NO_TRADE 242；WAITING_CORRECTLY 79
- 正确规避：603266.SH 天龙股份（T+1 -4.19%）；000980.SZ 众泰汽车（T+1 -3.38%）；688218.SH 江苏北人（T+1 -2.97%）；603205.SH 健尔康（T+1 -2.91%）；603868.SH 飞科电器（T+1 -2.67%）
- 值得继续观察：600397.SH 江钨装备（T+1 2.87%）；601689.SH 拓普集团（T+1 2.83%）；301234.SZ 五洲医疗（T+1 2.65%）；002031.SZ 巨轮智能（T+1 2.54%）；688377.SH 迪威尔（T+1 2.47%）
- 潜在漏掉：688292.SH 浩瀚深度（T+1 20.02%）；300836.SZ 佰奥智能（T+1 19.42%）；688261.SH 东微半导（T+1 15.16%）；301072.SZ 中捷精工（T+1 14.97%）；688790.SH 昂瑞微（T+1 14.41%）

## 5. 模型表现（层层过滤）

| Layer | N | T+1 Win | T+1 Mean | T+1 Median | T+5 Mean | T+20 Mean |
| --- | --- | --- | --- | --- | --- | --- |
| CANDIDATE | 1410 | 0.8014 | 0.0189 | 0.0131 | - | - |
| STRUCTURE_QUALIFIED | 317 | 0.6940 | 0.0135 | 0.0077 | - | - |
| EXECUTION_QUALIFIED | 110 | 0.8091 | 0.0183 | 0.0135 | - | - |
| BUY | 4 | 0.7500 | 0.0144 | 0.0164 | - | - |
| NO_TRADE | 1343 | 0.8049 | 0.0190 | 0.0131 | - | - |

- 执行层增量价值：**中性**（BUY 减结构合格 T+1 = 0.10%）
- 过滤增量价值：**低**（baseline STRUCTURE_QUALIFIED vs target BUY）

### Regime 分层（§三十三，累计 78 个交易日 20260522~20260917）

| Regime | N | BUY | T+1 Win | T+1 Mean | T+5 Mean | T+20 Mean |
| --- | --- | --- | --- | --- | --- | --- |
| STRONG | 0 | 0 | - | - | - | - |
| HEALTHY | 7500 | 13 | 0.4849 | 0.0027 | 0.0081 | 0.0160 |
| NEUTRAL | 20336 | 21 | 0.4647 | -0.0019 | -0.0189 | -0.0151 |
| WEAK | 2260 | 5 | 0.4042 | 0.0015 | 0.0150 | 0.0044 |
| RISK_OFF | 7163 | 0 | 0.4565 | -0.0013 | 0.0093 | -0.0049 |

### 长期过滤增量价值（§五十二，累计 78 个交易日 20260522~20260917）

| Layer | 指标 | 对照 | N(A/B) | Dates | Regimes | T+1 差 | T+5 差 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| STEP5 | 候选过滤 | 接受(WATCH/CANDIDATE) vs 排除(EXCLUDE) | 38452/135147 | 78 | 4 | -0.10% | 0.31% | 低 |
| STEP6 | 结构过滤 | 结构合格 vs REJECTED | 7543/17002 | 78 | 4 | -0.08% | -0.31% | 低 |
| STEP7 | 执行过滤 | BUY vs 结构合格但未 BUY | 39/7504 | 22 | 3 | -0.25% | 0.37% | 低 |
| STEP6 | HVT 增量 | HVT 活跃 vs HVT_FAILED/HVT_NONE | 1689/35570 | 76 | 4 | -0.18% | -0.27% | 低 |
| STEP6 | Retest 增量 | RETEST_SUCCESS vs NO_RETEST | 148/30480 | 51 | 4 | -0.89% | -2.89% | 负向 |
| STEP7 | Extension 过滤 | extension_risk=LOW vs extension_risk=MEDIUM/EXTREME | 37094/165 | 78 | 4 | 0.36% | -0.26% | 低 |
| STEP7 | 市场 Regime 过滤 | execution_mode=NORMAL vs execution_mode=WAIT/DEFENSIVE | 27836/9423 | 48 | 2 | -0.00% | -2.18% | 负向 |

### 主题 Phase × Structure State（§三十四，累计 78 个交易日 20260522~20260917）

| Phase | Structure | N | BUY | T+5 Mean | T+20 Mean |
| --- | --- | --- | --- | --- | --- |
| CONFIRMING | ADJUSTING | 8 | 0 | 2.79% | -1.86% |
| CONFIRMING | BASE | 76 | 0 | 2.52% | 6.71% |
| CONFIRMING | BREAKOUT | 4 | 0 | 33.80% | 12.78% |
| CONFIRMING | BREAKOUT_READY | 4 | 0 | 0.17% | -1.09% |
| CONFIRMING | FAILED | 480 | 0 | 2.96% | 5.99% |
| CONFIRMING | HVT | 6 | 0 | 4.29% | -1.97% |
| CONFIRMING | LOCKING | 4 | 0 | 10.11% | 5.14% |
| CONFIRMING | RETEST | 4 | 0 | -4.89% | -0.99% |
| CONFIRMING | WATCH | 430 | 0 | 2.13% | 4.94% |
| COOLING | ADJUSTING | 13 | 0 | 2.74% | 2.03% |
| COOLING | BASE | 49 | 0 | 2.31% | 5.24% |
| COOLING | BREAKOUT_READY | 8 | 0 | -2.33% | -1.00% |

### 主题 Phase × Structure Class（§三十五，累计 78 个交易日 20260522~20260917）

| Phase | Structure | N | BUY | T+5 Mean | T+20 Mean |
| --- | --- | --- | --- | --- | --- |
| CONFIRMING | BREAKOUT_CONFIRMED | 4 | 0 | 33.80% | 12.78% |
| CONFIRMING | BREAKOUT_READY | 4 | 0 | 0.17% | -1.09% |
| CONFIRMING | FAILED | 480 | 0 | 2.96% | 5.99% |
| CONFIRMING | HVT_ADJUSTING | 3 | 0 | 5.92% | -1.72% |
| CONFIRMING | HVT_EVENT | 6 | 0 | 4.29% | -1.97% |
| CONFIRMING | HVT_LOCKING | 4 | 0 | 10.11% | 5.14% |
| CONFIRMING | PULLBACK_HEALTHY | 5 | 0 | 0.91% | -1.94% |
| CONFIRMING | RETEST_SUCCESS | 4 | 0 | -4.89% | -0.99% |
| CONFIRMING | STRUCTURE_BASE | 76 | 0 | 2.52% | 6.71% |
| CONFIRMING | WATCH | 430 | 0 | 2.13% | 4.94% |
| COOLING | BREAKOUT_READY | 8 | 0 | -2.33% | -1.00% |
| COOLING | FAILED | 262 | 0 | 2.67% | 3.96% |

## 6. 模型错误（最多 5 条 P0/P1）

**NO MATERIAL MODEL ERROR**（不强行找问题，§四十-9）

## 7. 明日执行观察（WATCH，非 BUY 清单）

> WATCH ≠ BUY。观察对象只有在下一交易日由 Step 7 重新确认后才可能产生 BUY（§四十二）。
> watch basis = Step 7 快照日；所有观察对象必须在下一交易日由 Step 7 重新确认后才可能产生 BUY（WATCH != BUY，§四十二）。

| 股票 | 主题/Phase | watch_type | Watch（触发参考） | Condition | Invalid | extension_risk |
| --- | --- | --- | --- | --- | --- | --- |
| 002516.SZ 旷达科技 | T15/CONFIRMING | HVT_LOCKING | 触发参考 6.25（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 6.25~6.34；entry_state=WAIT_CONFIRMATION 需重新确认 | 收盘跌破 5.34 | LOW |
| 301086.SZ 鸿富瀚 | T05/CONFIRMING | HVT_LOCKING | 触发参考 168.49（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 168.49~174.49；entry_state=NO_ENTRY 需重新确认 | 收盘跌破 135.10 | LOW |
| 301234.SZ 五洲医疗 | T37/CONFIRMING | HVT_LOCKING | 触发参考 115.00（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 115.00~118.64；entry_state=WAIT_CONFIRMATION 需重新确认 | 收盘跌破 93.06 | LOW |
| 301552.SZ 科力装备 | T15/CONFIRMING | HVT_LOCKING | 触发参考 27.66（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 27.66~28.15；entry_state=NO_ENTRY 需重新确认 | 收盘跌破 22.77 | LOW |
| 603997.SH 继峰股份 | T15/CONFIRMING | HVT_LOCKING | 触发参考 12.60（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 12.60~12.79；entry_state=WAIT_CONFIRMATION 需重新确认 | 收盘跌破 11.16 | LOW |
| 688002.SH 睿创微纳 | T01/EMERGING | HVT_LOCKING | 触发参考 189.39（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 189.39~193.26；entry_state=WAIT_CONFIRMATION 需重新确认 | 收盘跌破 167.77 | LOW |
| 688601.SH 力芯微 | T01/EMERGING | HVT_LOCKING | 触发参考 65.50（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 65.50~67.30；entry_state=NO_ENTRY 需重新确认 | 收盘跌破 52.20 | LOW |
| 001288.SZ 运机集团 | T14/CONFIRMING | REBREAKOUT | 触发参考 31.08（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 31.08~31.76；entry_state=FAILED_NO_ENTRY 需重新确认 | 收盘跌破 26.96 | LOW |
| 300411.SZ 金盾股份 | T14/CONFIRMING | REBREAKOUT | 触发参考 9.17（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 9.17~9.35；entry_state=FAILED_NO_ENTRY 需重新确认 | 收盘跌破 8.15 | LOW |
| 301122.SZ 采纳股份 | T37/CONFIRMING | REBREAKOUT | 触发参考 50.30（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 50.30~51.65；entry_state=REBREAKOUT_ENTRY 需重新确认 | 收盘跌破 41.01 | LOW |
| 603012.SH 创力集团 | T14/CONFIRMING | REBREAKOUT | 触发参考 10.88（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 10.88~11.10；entry_state=RETEST_ENTRY 需重新确认 | 收盘跌破 8.60 | LOW |
| 603338.SH 浙江鼎力 | T14/CONFIRMING | REBREAKOUT | 触发参考 61.39（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 61.39~62.47；entry_state=FAILED_NO_ENTRY 需重新确认 | 收盘跌破 52.57 | LOW |

## 附录：Validation（§四十九）

- 最终状态：**POST_MARKET_REVIEW_READY**
- [PASS] FUTURE_LEAKAGE：feature 日期均 <= 对应 base date（theme 20260917 / candidate 20260917 <= signal 20260917 < review 20260918）；未来收益仅作 outcome
- [PASS] STEP_DEPENDENCY：Step5 / Step6 / Step7 落盘文件与当日子集均存在，未绕过
- [PASS] BUY_REVIEW：4 只 BUY 均具备 trigger / entry / stop / target / RR
- [PASS] NO_TRADE_REVIEW：1343 条 NO TRADE 均带 no_trade_reason
- [PASS] HVT_REVIEW：1347 行结构记录的 hvt_state 全部存在且落在声明域内
- [PASS] UPSTREAM_STATE_CONFLICT：1347 行结构记录中 hvt_state 与 retest_state 无自相矛盾（§二十七）
- [PASS] THEME_REVIEW：20260917 全部主题均来自 theme_master（50 个），无未知主题
- [WARNING] REGIME_TIER_COVERAGE：声明 5 档，Step 7 历史（20260522~20260917）实际产出 4 档 健康、中性、风险规避、偏弱；未覆盖 强势（对应代码 STRONG）—— 如实缺档，不臆断、不为凑覆盖率下调阈值（阈值与成因见 config/execution_config.json::market_regime）
- [PASS] REPORT_LANGUAGE：报告未使用禁用夸张词（§四十四）
- [PASS] REPORT_SECTIONS：正文核心章节 8 节（要求 5-8）
- LIMITATION：T+3, T+5, T+10, T+20 结果尚未发生，如实留空（不以前视数据填充）；regime 档位未覆盖：强势（如实缺档，§三十三）

---

Step 8 只反馈、不修改模型；模型修改必须经过独立 Validation / Research 流程（§五十七）。
