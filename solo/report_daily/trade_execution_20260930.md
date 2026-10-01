# Trade Execution Engine V3.1（20260930）次日可执行交易指令

输入：W7 候选池 1 只　|　完成结构补算 1 只　|　市场 regime=1
分级：PRIMARY BUY=0　CONDITIONAL BUY=0　WAIT=0　WATCH=1　AVOID=0
V3.1 门控：0 只被降级（G1 极端换手剔除 / G2 状态×质量 / G3 量比过热 / G4 环境风险）；BUY 仅保留 P1 结构（SECOND_WAVE/DRYUP/BREAKOUT_RETEST 回踩低吸、T0_CONFIRM 天量确认）或 P2 且 Exec≥85 且 volr≤2.2 的标的

## TOP EXECUTION（5只）

| Rank | 股票 | Alpha | Exec | Action | Trigger | Buy Zone | Stop |
| ---- | --- | ----: | ---: | --------------- | ------: | ----------- | ----: |
| - | _今日无通过 Entry/Structure/Risk 三门的可执行标的_ | - | - | NO TRADE | - | - | - |

> 排序依据=Execution（买点质量，Alpha 权重 0）；同 Action 内按 Execution 降序。Exec 由 Entry30%+Structure20%+Retest20%+Volume15%+Risk10%+Lifecycle5% 构成。Stop 列=结构失效位（长线持仓退出基准，预警线见全候选明细）。

## 全候选分层明细

| # | 股票 | Alpha | Exec | 现价 | 触发价 | 距触发价 | 量比 | 状态 | Entry | Retest | Stop | 仓位 | Action | 门控 |
| -- | -- | --: | --: | --: | --: | --: | --: | -- | -- | --: | --: | --: | -- | -- |
| 1 | 继峰股份(603997.SH) | 79.4 | 66 | 13.65 | 13.83 | -1.3% | ×0.6 | MIDLINE_HOLD | OBSERVE | 55 | 12.86 | 0% | WATCH | - |

## 最终交易指令（BUY）

_今日没有 PRIMARY BUY。_（宁可输出 0 个，也不把 WAIT 强行变成 BUY）

## 三个问题

### ① 明天买谁

**明天没有 PRIMARY BUY。**

不能为凑数量强行推荐 PRIMARY。

### ② 什么价格买

无 PRIMARY → 无固定买价；条件单以对应 Buy Zone 为触发参考（见全候选明细）。

### ③ 什么情况不买

1. 高开 >5% → NO CHASE
2. 高开 2%–5% → 等回踩，不追
3. 跌破 Trigger → 不买
4. 放量滞涨（volr>2.2）→ 不追，等回踩
5. 长上影冲高回落 → 不买
6. 回踩放量下跌 → 不买
7. EXTREME_CHURN 未完成回踩 → 不追（G1）
8. 非 P1/P2 结构或 P2 执行质量不足 Exec<85 → 不直接执行（G2）
9. 市场 regime0（大盘回撤>5% 或破长均线）→ 不开新仓（G4）
10. 收盘跌破结构失效位 → 交易失效

---
*风险提示：本引擎输出为盘后执行预案，非投资建议。全部价格基于 20260930 收盘数据；次日需以实际开盘走势复核。*
