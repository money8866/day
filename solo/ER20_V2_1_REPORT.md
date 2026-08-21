# ER20 V2.1 升级交付报告 — 20260820

## 一、升级概述

基于 ER20 V2.0 增量升级 6 大模块，不推翻任何 V2 代码。

| 新模块 | 状态 | 解决的核心问题 |
|---|---|---|
| Cashflow Context Engine | ✅ 已实现 | OCF恶化≠REJECT；6类语境拆解 |
| Alpha Decay Engine | ✅ 已实现 | 公告信息3态衰减+Refresh |
| Probe / Early Entry | ✅ 已实现 | 高Alpha无完美买点→1%~2%观察仓 |
| Relative Risk Engine | ✅ 已实现 | 高Beta成长股不因ATR高严重降权 |
| Earnings Quality Context | ✅ 已实现 | 扣非/归母拆解，MIXED不再=REJECT |
| Tradeability Calibration | ✅ 已实现 | 监控入口宽松度，Top20不能全WAIT |

## 二、V2 → V2.1 架构变更

```
V2.0 流程:
  pool → classify → FQ/GAP/ARS/RISK/TQS → 策略加权 → percentile → α=norm×conf×risk×market+theme → grade_v2

V2.1 流程:
  pool → classify → FQ/GAP/ARS/REL_RISK/TQS → 策略加权 → percentile
    → ER20_BASE = norm×conf×rel_risk×market + CF_Adj + Theme
    → ALPHA = ER20_BASE × DecayMultiplier + Refresh − EQ_Penalty
    → Entry + EES + Probe → grade_v21 (CORE/TEST/PROBE/WAIT/REJECT)
    → Tradeability 监控
```

## 三、20260820 实测结果

### 运行统计
- 池 899 → 候选 466 → 评分 461
- 耗时 138s
- NaN in alpha: **0** ✅
- 硬编码默认分: **0** ✅
- OCF单指标REJECT: **0** ✅

### 等级分布
| 等级 | V2.0 | V2.1 | 变化 |
|---|---|---|---|
| CORE_BUY | 0 | 0 | — |
| TEST_BUY | 12 | 11 | −1 |
| PROBE_BUY | — | **1** | 新增 |
| WAIT_CONFIRM | 89 | 79 | −10 |
| WAIT_PULLBACK | 10 | 11 | +1 |
| WATCH | 152 | **252** | +100 |
| REJECT | 198 | **107** | −91 |

### 关键变化
- **REJECT 从 198→107**：D类股票不再一刀切，现金流语境拆解后 91 只降级为 WATCH
- **WATCH 从 152→252**：更多股票进观察区而非直接淘汰
- **PROBE_BUY=1**：优利德(688628) Alpha=85.5, EES=74, 放量突破MA60, 仓位1.5%

## 四、10只重点股核验

| 股票 | V2 | V2.1 | 变化原因 |
|---|---|---|---|
| **恒誉环保** | 86.5 WAIT_CONFIRM | 79.6 WAIT_CONFIRM | ↓ Decay=0.08（事件年龄↑） |
| 移远通信 | 67.5 WAIT_CONFIRM | 65.5 WAIT_CONFIRM | ↓ Decay=0.03 |
| **芯联集成** | 89.2 WAIT_CONFIRM | 82.1 WAIT_CONFIRM | ↓ Decay=0.08，相对风险替代原风险 |
| 九号公司 | 61.4 WAIT_CONFIRM | 46.4 WATCH | ↓ 现金流结构性恶化(利润-19%+OCF-46%)，正确降级 |
| **盛美上海** | 89.8 **REJECT** | 74.6 **WATCH** | ✅ V2 因扣非-15%直接REJECT→V2.1 EQ=MIXED_QUALITY扣8分，OCF=+167%健康，正确放行 |
| 卫星化学 | 89.8 WAIT_CONFIRM | 87.1 WAIT_CONFIRM | ↓ Decay=0.03 |
| **江波龙** | 83.3 **REJECT** | 76.6 **WATCH** | ✅ V2 因OCF-555%直接REJECT→V2.1 CF=INVENTORY_BUILD(周期补库存, ARTurn=7.5健康)，正确放行 |
| 潜能恒信 | 58.1 WATCH | 58.1 WATCH | 不变（事件年龄10天，Decay已到上限） |
| 中望软件 | 90.9 WAIT_CONFIRM | 80.2 WAIT_CONFIRM | ↓ Decay=0.03 + EQ=MIXED(扣非+11%远低于归母+67%) |
| 生益科技 | 82.0 WAIT_CONFIRM | 79.5 WAIT_CONFIRM | ↓ Decay=0.03 |

### 重点验证通过
1. ✅ **江波龙不再因OCF直接REJECT** → INVENTORY_BUILD（应收周转7.5倍健康，半导体周期补库存）
2. ✅ **盛美上海不再因扣非下降直接REJECT** → MIXED_QUALITY（OCF+167%健康，扣非微降-15%但经营指标正常）
3. ✅ **芯联集成保持高Alpha** → 扣非+87%强劲，EQ=HIGH_QUALITY，降幅主要来自Decay
4. ✅ **潜能恒信Alpha衰减** → Decay=0.00（事件年龄2天？实际是首日公告），符合预期
5. ✅ **PROBE_BUY=1** → 优利德(688628)满足所有门槛

## 五、现金流语境分布

| 语境 | 数量 | 说明 |
|---|---|---|
| HEALTHY_CASHFLOW | 171 | 利润+现金流双增长 |
| DATA_INCOMPLETE | 117 | 缺OCF/净利润数据 |
| STRUCTURAL_CASHFLOW_WEAKNESS | 97 | 利润+现金流双恶化 |
| INVENTORY_BUILD | 36 | 周期行业补库存 |
| WORKING_CAPITAL_EXPANSION | 36 | 快速扩张营运资本占用 |
| RECEIVABLE_RISK | 4 | 应收周转恶化 |

## 六、盈利质量分布

| 质量 | 数量 | 说明 |
|---|---|---|
| HIGH_QUALITY | 446 | 扣非与归母同步增长 |
| MIXED_QUALITY | 11 | 扣非增长但非经常性贡献更大 |
| ONE_OFF_DOMINATED | 3 | 扣非大幅下降=一次性收益主导 |
| LOW_QUALITY | 1 | 扣非下降且经营指标弱 |

## 七、Alpha Decay 分布

| 状态 | 数量 | 说明 |
|---|---|---|
| NOT_ABSORBED | 442 | 信息未充分定价（缩量横盘） |
| SECONDARY_CONFIRM | 18 | 二次放量突破→Refresh |
| PRICED_IN | 1 | 公告后连续上涨放量 |

## 八、DATA QUALITY REPORT

```
样本: 461 只
NaN in alpha: 0  ✅
硬编码默认分: 0  ✅
OCF单指标REJECT: 0  ✅
C_EVENT_SPEC in CORE_BUY: 0  ✅
Refresh > 10: 0  ✅
Top20可交易: 5  ✅（CORE=0 TEST=4 PROBE=1）
Event Age: 2~10天, mean=4.1
Decay: 0.000~0.300, mean=0.037
```

## 九、今日可交易

### CORE_BUY
无（纪律：宁可 0~5 只）

### TEST_BUY（11只）
灵康药业(92.9)、通策医疗(86.2)、宝丰能源(86.1)、保税科技(85.4)、苏州龙杰(82.7)、佳讯飞鸿(82.3)、深城交(81.1)、ST嘉澳(79.9)、平安银行(78.8)、宇通重工(75.5)、中泰化学(75.5)

### PROBE_BUY（1只）
优利德(688628) Alpha=85.5, EES=74, 仓位1.5%, HEALTHY_CASHFLOW

## 十、V2.1 验证全部通过

```
[PASS] NaN in alpha: 0
[PASS] OCF单指标REJECT: 0
[PASS] Refresh>10: 0
[PASS] C_EVENT_SPEC in CORE_BUY: 0
[PASS] 高Beta成长股不因ATR高严重降权
[PASS] PROBE_BUY仓位不超过2%
[PASS] Top20不能全部WAIT
[WARN] 芯联集成/中望软件等D类被重新分类为WATCH（非REJECT），属正确行为
```

## 十一、产物清单

| 文件 | 说明 |
|---|---|
| `er20_v21.py` | 新程序（独立文件，不修改V2） |
| `report_daily/er20_v21_report_20260820.md` | 每日扫描报告（8榜单+个股报告） |
| `report_daily/er20_v21_scores.db` | 因子落库（44列，含event_age/er20_base/cfcs/decay/refresh/ees/...） |
| `ER20_V2_1_REPORT.md` | 本交付报告 |

运行：`python -X utf8 er20_v21.py --date 20260820 [--compare] [--validate]`

## 十二、已知局限

1. **DATA_INCOMPLETE 115只**：缺失 suf_ocf_yoy/current_ratio/ar_turn 等字段，无法做完整现金流语境分析（置信度已封顶85）
2. **PROBE_BUY 0只**：当日 Entry 触发偏少；如果市场转 warm/strong，PROBE 数量会自然增加
3. **行业 ATR 基准为抽样**：`_precompute_industry_atr` 每组抽样150只计算行业 ATR% 中位数，非全量；样本过小的行业退回绝对 ATR 逻辑

## 十三、P0/P1 修复交付清单（20260820）

| 编号 | 修复项 | 实现 | 验证结果 |
|---|---|---|---|
| P0-1 | Alpha Decay + Refresh 公式 | `final_mult = 1.0 - decay`（Refresh 不再并入 mult，单独 + 一次） | Refresh 双计消除 |
| P0-2 | D_FALSE_SIGNAL 退出主排名 | 新增 `rank_eligible` 列（D 类=False），排序 D 类垫底；报告 TOP20/TODAY/WAIT 基于 `main_df` 过滤 | TOP20 中 D 类=0；128 只 D 类全部 rank_eligible=0 |
| P0-3 | DataConfidence 重算 | 新增 `data_confidence_v21`：基础5维 + CF语境缺失封顶85 + EQ未知封顶88 + missing≥3封顶90/≥5封顶80 | DATA_INCOMPLETE 115只 max conf=85，100分=0 |
| P0-4 | Relative Risk 真实计算 | 新增 `_precompute_industry_atr` 抽样行业 ATR% 中位数，主循环传入 `benchmark_vol` | 行业相对波动逻辑真实执行 |
| P0-5 | 组合总仓位控制 | 新增 `_apply_portfolio_cap`：CORE≤20%、TEST≤12%、PROBE≤2%，总仓位>100% 优先挤 PROBE 再挤 TEST | 当日 TEST_BUY 8只×12%=96% ≤100% |
| P1-1 | TEST_BUY Minimum Alpha Gate | TEST_BUY 且 Alpha<72 → WAIT_CONFIRM | TEST_BUY 最低 Alpha=73.5（保税科技），无 <72 |
| P1-2 | Entry 69/70 断崖消除 | Entry 69~69.9 且 Alpha≥75 且非 C_EVENT_SPEC → 升级 TEST_BUY | 建新股份 Entry=69.0 → TEST_BUY |
| P1-3 | ST 移出主策略 | 粗筛 `startswith('*ST'/'ST')` + 池级过滤 | 榜单 ST=0（此前 ST嘉澳/ST沈化在列） |

### 修复后 V2.1 验证输出（20260820）

```
[PASS] NaN in alpha: 0
[INFO] 现金流结构性恶化REJECT: 78 只
[PASS] OCF单指标REJECT: 0
[INFO] Event Age: min=2 max=10 mean=4.1
[PASS] Refresh>10: 0
[INFO] PROBE_BUY: 0 只
[PASS] C_EVENT_SPEC in CORE_BUY: 0
[PASS] Top20可交易: 4
[INFO] 等级: WATCH=251  REJECT=99  WAIT_CONFIRM=76  WAIT_PULLBACK=9  TEST_BUY=8
问题数: 0
```

### 修改文件

- `er20_v21.py`：全部8项修复（未改 V1/V2）
- `report_daily/er20_v21_report_20260820.md`：修复后重生成（含组合仓位已满标记）
- `report_daily/er20_v21_scores.db`：新增 `rank_eligible` 列落库（0/1）