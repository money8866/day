# Step 8 盘后复盘 · 20260917

- signal_date（被复盘信号日）：**20260916**；review_date（复盘日）：**20260917**
- 模式：**READ ONLY**（不修改 Step 1-7 的任何落盘结果；不重新选股；不产生 BUY）
- 最终状态：**POST_MARKET_REVIEW_READY**

## 核心摘要
- **MARKET** — 市场：结构中性（中性环境）；breadth 0.46，较前一日回落 28.75%；涨停 51 / 跌停 2；成交额 18365 亿。
- **THEME** — 主线：T01 半导体、T05 消费电子、T09 光伏产业链；强化：T01 半导体（breadth/health 改善）、T05 消费电子（breadth/health 改善）、T09 光伏产业链（breadth/health 改善）；退潮：T42 生物制品
- **SIGNAL** — 昨日信号：侯选 178 / 结构合格 25 / 执行合格 13；Step7：BUY 0 / 未交易 178
- **TRADE** — 昨日 BUY：0 触发、0 走强、0 跌破 Stop、0 未成交；未交易中潜在漏掉 19
- **FEEDBACK** — 复盘：执行层增量价值 样本不足（BUY 减结构合格 T+1 = -）；过滤增量价值 样本不足；无实质性模型错误；待独立验证的模型改动候选 2 项（长期指标 §五十二）；明日保留 30 个条件观察对象（WATCH ≠ BUY）

## 1. 市场

结构中性（中性环境）；breadth 0.46，较前一日回落 28.75%；涨停 51 / 跌停 2；成交额 18365 亿。

| 项目 | 数值 | 项目 | 数值 |
| --- | --- | --- | --- |
| 上证 | -0.41% | 沪深300 | -0.45% |
| 中证1000 | -0.51% | 中证2000 | 0.12% |
| 成交额 | 18365 亿 | 成交额/20日均 | 0.96 |
| 上涨/下跌/平盘 | 2576/2820/157 | Breadth | 0.4639 |
| 涨停/跌停 | 51/2 | 炸板 | 23 |
| 最高连板 | 5 | 赚钱效应 | 赚钱效应中性 |
| 结构标签 | NO_TAG | 次级标签 | - |
| 背离标记 | - | Regime | NEUTRAL（WEAK→NEUTRAL） |

Regime 变化依据：breadth 0.7514→0.4639；成交额变化 -0.86%；涨停 51 / 跌停 2；指数结构 SSE -0.41%（依据 breadth / volume / amount / limit-up-down / index structure，§六）
Regime 覆盖：已观测 HEALTHY|NEUTRAL|RISK_OFF|WEAK；缺档（如实为 0）STRONG

## 2. 主题（最多 5 个）

| 主题 | Phase（前→后） | SEOS | Health | Breadth | Core Breadth | Amount Share | 今日变化 | 分组 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 半导体 | DORMANT→EMERGING | 78.47 | 83.07 | 0.801 | 1.000 | 0.4880 | health 34.020 / breadth 0.991 | IMPROVING |
| T05 消费电子 | DORMANT→DORMANT | 77.22 | 80.66 | 0.839 | 0.865 | 0.2245 | health 43.600 / breadth 1.140 | IMPROVING |
| T09 光伏产业链 | DORMANT→DORMANT | 75.09 | 73.09 | 0.648 | 1.000 | 0.2183 | health 40.180 / breadth 1.076 | IMPROVING |
| T21 化工新材料 | DORMANT→DORMANT | 74.11 | 77.71 | 0.734 | 0.906 | 0.0728 | health 32.040 / breadth 1.003 | IMPROVING |
| T03 通信 | DORMANT→DORMANT | 73.33 | 78.53 | 0.803 | 0.661 | 0.2803 | health 42.700 / breadth 1.206 | IMPROVING |

### 主题轮动（最多展示 5 个）

- 当日无 ROTATION_IN / ROTATION_OUT 记录。

扩散复盘：T01 EARLY_DIFFUSION→HEALTHY_EXPANSION；T05 EARLY_DIFFUSION→HEALTHY_EXPANSION；T09 FULL_DIFFUSION→FULL_BREADTH；T21 FULL_DIFFUSION→FULL_BREADTH；T03 EARLY_DIFFUSION→HEALTHY_EXPANSION

## 3. 今日结构池（最多 12 只）

| 股票 | 主题 | structure_class | hvt_state | T+1 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 688002.SH 睿创微纳 | T01 | HVT_LOCKING | HVT_LOCKING | -0.59% | continued |
| 688601.SH 力芯微 | T01 | HVT_LOCKING | HVT_LOCKING | -1.77% | failed |
| 688536.SH 思瑞浦 | T01 | BREAKOUT_READY | HVT_ADJUSTING | 1.30% | failed |
| 688209.SH 英集芯 | T01 | REBREAKOUT_CONFIRMED | HVT_ADJUSTING | 0.27% | confirmed |
| 603068.SH 博通集成 | T01 | FAILED | HVT_FAILED | -0.39% | failed |
| 603690.SH 至纯科技 | T01 | FAILED | HVT_FAILED | -1.16% | failed |
| 603991.SH 领先股份 | T01 | FAILED | HVT_FAILED | -1.76% | failed |
| 688082.SH 盛美上海 | T01 | FAILED | HVT_FAILED | -1.30% | failed |
| 688259.SH 创耀科技 | T01 | FAILED | HVT_FAILED | -0.16% | failed |
| 688381.SH 帝奥微 | T01 | FAILED | HVT_FAILED | -2.11% | failed |
| 688396.SH 华润微 | T01 | FAILED | HVT_FAILED | 0.81% | failed |
| 688508.SH 芯朋微 | T01 | FAILED | HVT_FAILED | 0.40% | failed |

## 4. 昨日交易复盘

### BUY 复盘（0 只）

| 股票 | Entry | Trigger | 今日 O/H/L/C | MAE(T+1) | MFE(T+1) | 结果 | 原因 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| - | - | - | - | - | - | - | - |

### NO TRADE 复盘（178 条）

- 分类：NO_TRADE_DUE_TO_STRUCTURE 177；UNMAPPED 1
- 结果：CORRECT_NO_TRADE 107；NEUTRAL_OUTCOME 39；MISSED_OPPORTUNITY 19；WAITING_CORRECTLY 13
- 正确规避：688061.SH 灿瑞科技（T+1 -4.71%）；600360.SH 华微电子（T+1 -4.42%）；688146.SH 中船特气（T+1 -4.11%）；688368.SH 晶丰明源（T+1 -3.94%）；002077.SZ 大港股份（T+1 -3.83%）
- 值得继续观察：688536.SH 思瑞浦（T+1 1.30%）；688396.SH 华润微（T+1 0.81%）；688508.SH 芯朋微（T+1 0.40%）；688209.SH 英集芯（T+1 0.27%）；688259.SH 创耀科技（T+1 -0.16%）
- 潜在漏掉：688137.SH 近岸蛋白（T+1 18.75%）；688802.SH 沐曦股份（T+1 14.44%）；600641.SH 先导基电（T+1 10.01%）；003026.SZ 中晶科技（T+1 9.99%）；688279.SH 峰岹科技（T+1 9.64%）

## 5. 模型表现（层层过滤）

| Layer | N | T+1 Win | T+1 Mean | T+1 Median | T+5 Mean | T+20 Mean |
| --- | --- | --- | --- | --- | --- | --- |
| CANDIDATE | 178 | 0.3483 | 0.0009 | -0.0055 | - | - |
| STRUCTURE_QUALIFIED | 25 | 0.4400 | 0.0021 | -0.0033 | - | - |
| EXECUTION_QUALIFIED | 13 | 0.3077 | -0.0073 | -0.0059 | - | - |
| BUY | 0 | - | - | - | - | - |
| NO_TRADE | 178 | 0.3483 | 0.0009 | -0.0055 | - | - |

- 执行层增量价值：**样本不足**（BUY 减结构合格 T+1 = -）
- 过滤增量价值：**样本不足**（baseline STRUCTURE_QUALIFIED vs target BUY）

### Regime 分层（§三十三，累计 77 个交易日 20260522~20260916）

| Regime | N | BUY | T+1 Win | T+1 Mean | T+5 Mean | T+20 Mean |
| --- | --- | --- | --- | --- | --- | --- |
| STRONG | 0 | 0 | - | - | - | - |
| HEALTHY | 7500 | 13 | 0.4849 | 0.0027 | 0.0081 | 0.0160 |
| NEUTRAL | 18987 | 17 | 0.4406 | -0.0033 | -0.0189 | -0.0151 |
| WEAK | 2260 | 5 | 0.4042 | 0.0015 | 0.0150 | 0.0044 |
| RISK_OFF | 7163 | 0 | 0.4565 | -0.0013 | 0.0093 | -0.0049 |

### 长期过滤增量价值（§五十二，累计 77 个交易日 20260522~20260916）

| Layer | 指标 | 对照 | N(A/B) | Dates | Regimes | T+1 差 | T+5 差 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| STEP5 | 候选过滤 | 接受(WATCH/CANDIDATE) vs 排除(EXCLUDE) | 37042/129324 | 77 | 4 | -0.09% | 0.31% | 低 |
| STEP6 | 结构过滤 | 结构合格 vs REJECTED | 7225/16422 | 77 | 4 | -0.07% | -0.31% | 低 |
| STEP7 | 执行过滤 | BUY vs 结构合格但未 BUY | 35/7190 | 21 | 3 | -0.39% | 0.37% | 低 |
| STEP6 | HVT 增量 | HVT 活跃 vs HVT_FAILED/HVT_NONE | 1644/34266 | 75 | 4 | -0.15% | -0.27% | 低 |
| STEP6 | Retest 增量 | RETEST_SUCCESS vs NO_RETEST | 139/29380 | 50 | 4 | -0.95% | -2.89% | 负向 |
| STEP7 | Extension 过滤 | extension_risk=LOW vs extension_risk=MEDIUM/EXTREME | 35747/163 | 77 | 4 | 0.28% | -0.26% | 低 |
| STEP7 | 市场 Regime 过滤 | execution_mode=NORMAL vs execution_mode=WAIT/DEFENSIVE | 26487/9423 | 47 | 2 | -0.10% | -2.18% | 负向 |

### 主题 Phase × Structure State（§三十四，累计 77 个交易日 20260522~20260916）

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

### 主题 Phase × Structure Class（§三十五，累计 77 个交易日 20260522~20260916）

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
| 002516.SZ 旷达科技 | T15/EMERGING | HVT_LOCKING | 触发参考 6.25（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 6.25~6.34；entry_state=WAIT_CONFIRMATION 需重新确认 | 收盘跌破 5.29 | LOW |
| 301086.SZ 鸿富瀚 | T05/EMERGING | HVT_LOCKING | 触发参考 166.58（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 166.58~172.49；entry_state=NO_ENTRY 需重新确认 | 收盘跌破 135.19 | LOW |
| 301234.SZ 五洲医疗 | T37/EARLY | HVT_LOCKING | 触发参考 115.00（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 115.00~118.76；entry_state=WAIT_CONFIRMATION 需重新确认 | 收盘跌破 92.94 | LOW |
| 688002.SH 睿创微纳 | T01/EMERGING | HVT_LOCKING | 触发参考 189.91（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 189.91~193.84；entry_state=WAIT_CONFIRMATION 需重新确认 | 收盘跌破 168.18 | LOW |
| 688601.SH 力芯微 | T01/EMERGING | HVT_LOCKING | 触发参考 65.50（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 65.50~67.32；entry_state=NO_ENTRY 需重新确认 | 收盘跌破 52.18 | LOW |
| 001288.SZ 运机集团 | T14/EARLY | REBREAKOUT | 触发参考 31.08（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 31.08~31.81；entry_state=FAILED_NO_ENTRY 需重新确认 | 收盘跌破 26.91 | LOW |
| 300411.SZ 金盾股份 | T14/EARLY | REBREAKOUT | 触发参考 9.17（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 9.17~9.35；entry_state=FAILED_NO_ENTRY 需重新确认 | 收盘跌破 7.99 | LOW |
| 301122.SZ 采纳股份 | T37/EARLY | REBREAKOUT | 触发参考 48.49（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 48.49~49.83；entry_state=REBREAKOUT_ENTRY 需重新确认 | 收盘跌破 41.02 | LOW |
| 603012.SH 创力集团 | T14/EARLY | REBREAKOUT | 触发参考 10.88（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 10.88~11.10；entry_state=RETEST_ENTRY 需重新确认 | 收盘跌破 8.60 | LOW |
| 603338.SH 浙江鼎力 | T14/EARLY | REBREAKOUT | 触发参考 61.39（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 61.39~62.48；entry_state=FAILED_NO_ENTRY 需重新确认 | 收盘跌破 52.61 | LOW |
| 688209.SH 英集芯 | T01/EMERGING | REBREAKOUT | 触发参考 38.29（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 38.29~39.56；entry_state=REBREAKOUT_ENTRY 需重新确认 | 收盘跌破 29.89 | LOW |
| 601038.SH 一拖股份 | T14/EARLY | RETEST | 触发参考 15.10（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY） | Entry 区间 15.10~15.34；entry_state=BREAKOUT_ENTRY 需重新确认 | 收盘跌破 13.86 | LOW |

## 附录：Validation（§四十九）

- 最终状态：**POST_MARKET_REVIEW_READY**
- [PASS] FUTURE_LEAKAGE：feature 日期均 <= 对应 base date（theme 20260916 / candidate 20260916 <= signal 20260916 < review 20260917）；未来收益仅作 outcome
- [PASS] STEP_DEPENDENCY：Step5 / Step6 / Step7 落盘文件与当日子集均存在，未绕过
- [PASS] BUY_REVIEW：0 只 BUY 均具备 trigger / entry / stop / target / RR（当日 BUY=0，允许，不为凑数量降低门槛）
- [PASS] NO_TRADE_REVIEW：178 条 NO TRADE 均带 no_trade_reason
- [PASS] HVT_REVIEW：178 行结构记录的 hvt_state 全部存在且落在声明域内
- [PASS] UPSTREAM_STATE_CONFLICT：178 行结构记录中 hvt_state 与 retest_state 无自相矛盾（§二十七）
- [PASS] THEME_REVIEW：20260916 全部主题均来自 theme_master（50 个），无未知主题
- [WARNING] REGIME_TIER_COVERAGE：声明 5 档，Step 7 历史（20260522~20260916）实际产出 4 档 健康、中性、风险规避、偏弱；未覆盖 强势（对应代码 STRONG）—— 如实缺档，不臆断、不为凑覆盖率下调阈值（阈值与成因见 config/execution_config.json::market_regime）
- [PASS] REPORT_LANGUAGE：报告未使用禁用夸张词（§四十四）
- [PASS] REPORT_SECTIONS：正文核心章节 8 节（要求 5-8）
- LIMITATION：T+3, T+5, T+10, T+20 结果尚未发生，如实留空（不以前视数据填充）；regime 档位未覆盖：强势（如实缺档，§三十三）

---

Step 8 只反馈、不修改模型；模型修改必须经过独立 Validation / Research 流程（§五十七）。
