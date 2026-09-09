# TE + T20_ROCKET 多源候选 → 量价二次筛选与最高胜率交易排序（20260908）

> 原则：TE_BUY 不是自动 BUY；T20_ROCKET 不是短线 BUY（ES/XP 仅作参考）；量价二筛决定"现在该不该买"。宁可 PRIMARY BUY = 0，不为凑数制造 BUY。

## 一、多源候选 → 量价二筛汇总

- 候选总数 **8**：TE_BUY 3 只 / T20_ROCKET 5 只 / BOTH 0 只
- 决策分布：PRIMARY BUY **0** / CONDITIONAL BUY **0** / WAIT_RETEST **2** / WAIT_BREAKOUT **0** / WATCH_ROCKET **3** / WATCH_HIGH_EXTENSION **3** / AVOID **0**
- FINAL EXECUTION：A级 **0** / B级 **2** / C级 **6** / D级 **0**

| 代码 | 名称 | SOURCE | VPQ | 状态 | 形态 | T1 | T3 | Conf | Entry | Risk | R/R | Readiness | 决策 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 002095.SZ | 生意宝 | T20_ROCKET | 73 | NEUTRAL | CONFIRMED_BREAKOUT | 54 | 54 | CONFIRMED | CHASE | HIGH | 0.4% | EXTENDED | **WATCH_HIGH_EXTENSION** |
| 002436.SZ | 兴森科技 | T20_ROCKET | 57 | HEALTHY_ADVANCE | CONFIRMED_BREAKOUT | 72 | 70 | CONFIRMED | READY | ACCEPTABLE | 0.9% | EXPANSION | **WATCH_ROCKET** |
| 600150.SH | 中国船舶 | TE_BUY | 54 | NEUTRAL | HIGH_EXTENSION | 50 | 50 | CONFIRMED | NEAR | HIGH | 0.9% | — | **WATCH_HIGH_EXTENSION** |
| 600161.SH | 天坛生物 | T20_ROCKET | 78 | NEUTRAL | — | 59 | 64 | PENDING | NEAR | ACCEPTABLE | 0.7% | PRE_BREAKOUT | **WATCH_ROCKET** |
| 600663.SH | 陆家嘴 | TE_BUY | 45 | NEUTRAL | — | 54 | 59 | CONFIRMED | NEAR | ACCEPTABLE | 2.0% | — | **WAIT_RETEST** |
| 600869.SH | 远东股份 | T20_ROCKET | 62 | NEUTRAL | HIGH_EXTENSION | 50 | 45 | CONFIRMED | READY | ELEVATED | 0.7% | EXTENDED | **WATCH_HIGH_EXTENSION** |
| 603259.SH | 药明康德 | TE_BUY | 71 | HEALTHY_ADVANCE | — | 66 | 69 | CONFIRMED | NEAR | ACCEPTABLE | 1.6% | — | **WAIT_RETEST** |
| 688001.SH | 华兴源创 | T20_ROCKET | 58 | HEALTHY_ADVANCE | CONFIRMED_BREAKOUT | 72 | 70 | CONFIRMED | READY | ACCEPTABLE | 1.0% | EXPANSION | **WATCH_ROCKET** |

## 二、TOP TRADE（按 ShortTradeRank：未来 1~3 天最值得交易）

| 排名 | 代码 | 名称 | SOURCE | 评分 | VPQ | T1 | T3 | 决策 | 一句话理由 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 688001.SH | 华兴源创 | T20_ROCKET | 72.3 | HEALTHY_ADVANCE | 72 | 70 | **WATCH_ROCKET** | 火箭观察（EXPANSION），等突破确认或回踩低吸 |
| 2 | 002436.SZ | 兴森科技 | T20_ROCKET | 72.1 | HEALTHY_ADVANCE | 72 | 70 | **WATCH_ROCKET** | 火箭观察（EXPANSION），等突破确认或回踩低吸 |
| 3 | 603259.SH | 药明康德 | TE_BUY | 65.7 | HEALTHY_ADVANCE | 66 | 69 | **WAIT_RETEST** | 已突破触发位但脱离买区，等回踩买区确认 |
| 4 | 600663.SH | 陆家嘴 | TE_BUY | 54.8 | NEUTRAL | 54 | 59 | **WAIT_RETEST** | 已突破触发位但脱离买区，等回踩买区确认 |
| 5 | 600161.SH | 天坛生物 | T20_ROCKET | 53.9 | NEUTRAL | 59 | 64 | **WATCH_ROCKET** | 火箭观察（PRE_BREAKOUT），等突破确认或回踩低吸 |
| 6 | 600869.SH | 远东股份 | T20_ROCKET | 51.6 | NEUTRAL | 50 | 45 | **WATCH_HIGH_EXTENSION** | 扩张过度，宁可错过也不追 |
| 7 | 600150.SH | 中国船舶 | TE_BUY | 43.8 | NEUTRAL | 50 | 50 | **WATCH_HIGH_EXTENSION** | 高位扩张，不追，等回踩或放量突破确认 |
| 8 | 002095.SZ | 生意宝 | T20_ROCKET | 36.6 | NEUTRAL | 54 | 54 | **WATCH_HIGH_EXTENSION** | 扩张过度，宁可错过也不追 |

## 三、次日最高胜率 TOP3

### TOP1 · 688001.SH 华兴源创（T20_ROCKET）
- **类型**：HEALTHY_ADVANCE / CONFIRMED_BREAKOUT / Readiness=EXPANSION
- **T1 延续**：72　**VPQ**：58（HEALTHY_ADVANCE）
- **结构**：距前20日高 1.6%，偏离MA20 1.79% ATR，量比 2.37（现价/5日均量）
- **Confirmation**：CONFIRMED　**Entry**：READY（触发位 60.12）
- **STOP**：52.18（距离 14.5%）　**R/R**：1.02　**Risk**：ACCEPTABLE
- **决策**：WATCH_ROCKET —— 火箭观察（EXPANSION），等突破确认或回踩低吸
- **为什么排第1**：ShortTradeRank 第 1（T1 72 / 量价状态 HEALTHY_ADVANCE / 入场 READY / 确认 CONFIRMED）

### TOP2 · 002436.SZ 兴森科技（T20_ROCKET）
- **类型**：HEALTHY_ADVANCE / CONFIRMED_BREAKOUT / Readiness=EXPANSION
- **T1 延续**：72　**VPQ**：57（HEALTHY_ADVANCE）
- **结构**：距前20日高 1.9%，偏离MA20 1.97% ATR，量比 2.93（现价/5日均量）
- **Confirmation**：CONFIRMED　**Entry**：READY（触发位 39.38）
- **STOP**：34.71（距离 13.5%）　**R/R**：0.91　**Risk**：ACCEPTABLE
- **决策**：WATCH_ROCKET —— 火箭观察（EXPANSION），等突破确认或回踩低吸
- **为什么排第2**：ShortTradeRank 第 2（T1 72 / 量价状态 HEALTHY_ADVANCE / 入场 READY / 确认 CONFIRMED）

### TOP3 · 603259.SH 药明康德（TE_BUY）
- **类型**：HEALTHY_ADVANCE
- **T1 延续**：66　**VPQ**：71（HEALTHY_ADVANCE）
- **结构**：距前20日高 -9.9%，偏离MA20 -0.55% ATR，量比 1.20（现价/5日均量）
- **Confirmation**：CONFIRMED　**Entry**：NEAR（触发位 151.50）
- **STOP**：141.47（距离 9.5%）　**R/R**：1.59　**Risk**：ACCEPTABLE
- **决策**：WAIT_RETEST —— 已突破触发位但脱离买区，等回踩买区确认
- **为什么排第3**：ShortTradeRank 第 3（T1 66 / 量价状态 HEALTHY_ADVANCE / 入场 NEAR / 确认 CONFIRMED）

## 四、原模型 vs 二筛变化表

| 代码 | 名称 | SOURCE | 原结论 | 二筛结论 | 变化 |
|---|---|---|---|---|---|
| 002095.SZ | 生意宝 | T20_ROCKET | T20_ROCKET_WATCH | **WATCH_HIGH_EXTENSION** | ↓ 降级 |
| 002436.SZ | 兴森科技 | T20_ROCKET | T20_ROCKET_WATCH | **WATCH_ROCKET** | → 维持 |
| 600150.SH | 中国船舶 | TE_BUY | BUY_ON_CONFIRM | **WATCH_HIGH_EXTENSION** | ↓ 降级 |
| 600161.SH | 天坛生物 | T20_ROCKET | T20_ROCKET_WATCH | **WATCH_ROCKET** | → 维持 |
| 600663.SH | 陆家嘴 | TE_BUY | BUY | **WAIT_RETEST** | ↓ 降级 |
| 600869.SH | 远东股份 | T20_ROCKET | T20_ROCKET_WATCH | **WATCH_HIGH_EXTENSION** | ↓ 降级 |
| 603259.SH | 药明康德 | TE_BUY | BUY | **WAIT_RETEST** | ↓ 降级 |
| 688001.SH | 华兴源创 | T20_ROCKET | T20_ROCKET_WATCH | **WATCH_ROCKET** | → 维持 |

## 五、FINAL EXECUTION

### A级 —— 明日直接执行（Confirmation=TRUE / Entry=READY / Risk=ACCEPTABLE / R/R≥2）（0 只）
- 无

### B级 —— 确认后执行（2 只）
- **603259.SH 药明康德**（TE_BUY）：WAIT_RETEST —— 已突破触发位但脱离买区，等回踩买区确认｜触发 151.50｜止损 141.47｜R/R 1.59
- **600663.SH 陆家嘴**（TE_BUY）：WAIT_RETEST —— 已突破触发位但脱离买区，等回踩买区确认｜触发 8.82｜止损 7.98｜R/R 1.98

### C级 —— T20_ROCKET 重点观察（6 只）
- **600150.SH 中国船舶**（TE_BUY）：WATCH_HIGH_EXTENSION —— 高位扩张，不追，等回踩或放量突破确认｜触发 38.13｜止损 34.50｜R/R 0.87
- **002436.SZ 兴森科技**（T20_ROCKET）：WATCH_ROCKET —— 火箭观察（EXPANSION），等突破确认或回踩低吸｜触发 39.38｜止损 34.71｜R/R 0.91
- **600869.SH 远东股份**（T20_ROCKET）：WATCH_HIGH_EXTENSION —— 扩张过度，宁可错过也不追｜触发 21.97｜止损 17.74｜R/R 0.66
- **688001.SH 华兴源创**（T20_ROCKET）：WATCH_ROCKET —— 火箭观察（EXPANSION），等突破确认或回踩低吸｜触发 60.12｜止损 52.18｜R/R 1.02
- **002095.SZ 生意宝**（T20_ROCKET）：WATCH_HIGH_EXTENSION —— 扩张过度，宁可错过也不追｜触发 15.12｜止损 14.32｜R/R 0.43
- **600161.SH 天坛生物**（T20_ROCKET）：WATCH_ROCKET —— 火箭观察（PRE_BREAKOUT），等突破确认或回踩低吸｜触发 12.95｜止损 12.00｜R/R 0.66

### D级 —— 禁止交易（0 只）
- 无

---

*生成时间：2026-09-08 23:11:04　数据：8 只候选，量价二筛 V1.0*