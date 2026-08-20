# ER20 V2.0 重构交付报告 — 20260820

## 一、目标与结论

| 项目 | V1（er20_strategy.py） | V2（er20_v2.py） |
|---|---|---|
| nan 污染评分 | 存在：B_REVERSAL 大量 expectation_gap=nan 直接入加权 | `safe_score()` 统一拦截，缺失记入 missing + 扣置信，绝不默认分 |
| 固定默认分 | gap 50/75/60/55/40/25、ARS/PRR 50 基线、tech 40/50/88 | 全部删除；数据不足返 None 进 missing |
| Alpha / Entry | 混在一个总分 | 完全分离：ALPHA(值不值得持有) ≠ ENTRY(今天能不能买) |
| 事件分类 | 无 | A_CONFIRMATION / B_REVERSAL / C_EVENT_SPEC / D_FALSE_SIGNAL |
| 参数管理 | 字典散落 | 全部集中 `ER20Config`，零 magic numbers |

**20260820 实测**：池 899 → 候选 466 → 评分 461；事件 A=264 / B=64 / D=133；等级 REJECT=198、WATCH=152、WAIT_CONFIRM=89、WAIT_PULLBACK=10、TEST_BUY=12、**CORE_BUY=0**（牛市不强给、符合"宁可 0~5 只"规格）。

## 二、V2 架构

```
load_pool_v2(S4 fin_ind_full 主源 + S1/S2/S3 补漏 + Q1 缓存)
  → 粗筛(公告窗口[2,10]交易日, 非北交所)
  → classify_event(A/B/C/D)
  → 公共因子: FQ 基本面 / GAP 预期差 / ARS 公告反应 / RISK / OVERHEAT / CONF / THEME
  → 策略专属: B→RQS(七维)+TQS(六维)；A→PQS+Trend
  → 策略内加权(W_A/W_B) → 策略内 percentile 归一化
  → ER20_ALPHA = norm × (Conf/100) × (1−Risk×0.40) × Market + Theme(±5)
  → grade_v2 分级 → SQLite 落库 → 4 榜单 + 个股报告
```

## 三、关键模块

- **classify_event**：D1 扣非负归母大增 / D2 营收降利润暴增 / D3 低基数虚高见顶 / D4 现金流恶化 / D5 公告后暴涨>30% 透支 → 全部硬剔除（grade=REJECT）；C 事件股隔离（仓位≤3%）。
- **RQS**（B 类核心，W=0.25利润反转+0.20收入加速+0.15毛利率+0.15现金流+0.10资产负债+0.10多季连续+0.05行业周期）；缺>25% 权重维度返 None。
- **TQS**（0.25趋势+0.20量能+0.20回踩+0.15突破+0.10动量+0.10支撑）。
- **ARS**：T0 相对上证收益 + 量能 + 收盘位置 + 高开低走 + T+3/T+5 缩量横盘 + sell_the_news(−25)。
- **Entry Engine**：0.30位置+0.25触发+0.20量能+0.15盈亏比+0.10市场；触发=放量突破前高/回踩MA20阳线/放量破MA60。
- **风险**：ATR 波动 + 透支(overheat 0~40) + 偏离 MA20。
- **数据置信度**：金融 0.35/技术 0.20/公告 0.25/历史 0.10/新鲜 0.10；<50 只 WATCH、<70 不可 CORE_BUY。
- **市场 6 档**：strong 1.15 / bull 1.05 / neutral 1.00 / recovery 0.95 / weak 0.85 / bear 0.70（20260820=bull x1.05）。
- **SQLite**：`report_daily/er20_v2_scores.db` 因子全量落库，供 Forward 5/10/20D 回测。

## 四、10 只重点股核验（旧 vs 新）

| 股票 | 旧 V1 | 新 V2 | 变化解读 |
|---|---|---|---|
| 恒誉环保(现名恒誉科技) | 77.5 WATCH | **86.5 WAIT_CONFIRM** | ↑ FQ88/GAP65/ARS84；公告后涨幅大未回踩，Entry 38.8 无触发，正确 |
| 移远通信 | 74.5 WATCH | 67.5 WAIT_CONFIRM | ↓ ARS42(公告反应弱)+Risk50(波动大)，正确降权 |
| 芯联集成 | 73.2 WATCH | **89.2 WAIT_CONFIRM** | ↑ FQ75+主题+5(半导体)；等待突破触发 |
| 九号公司 | 73.1 WAIT_PULLBACK | 61.4 WAIT_CONFIRM | ↓ Risk49.6，波动风险压制 |
| 盛美上海 | 71.5 WATCH | 89.8 **REJECT** | 扣非−15%但归母+42% → D1 命中"一次性收益"，正确剔除（盈利质量差） |
| 卫星化学 | 70.5 WAIT_PULLBACK | **89.8 WAIT_CONFIRM** | ↑ FQ88/趋势健康 |
| 江波龙 | 64.5 WATCH | 83.3 **REJECT** | 利润+71528% 但 OCF 同比−555% → D4 命中；⚠️高景气周期股 H1 现金流季节性/低基数导致**假阳性**，见"局限" |
| 潜能恒信 | 63.8 WATCH | 58.1 WATCH | 猎手第 1 但 V2 正确降权：公告已 10 天、公告前大涨 → GAP=18 预期差耗尽 |
| 中望软件 | 70.1 WAIT_PULLBACK | **90.9 WAIT_CONFIRM** | ↑ FQ73+主题+5；V2 榜单第 3 |
| 生益科技 | 64.6 WAIT_PULLBACK | **82.0 WAIT_CONFIRM** | ↑ |

## 五、DATA QUALITY REPORT（20260820）

```
样本: 461 只
核心因子缺失率: fq 0.0% / gap_s 0.0% / ars 0.0% / rqs 86.1%(非B类不适用) / tqs 86.1%
Confidence: 均值100 中位100（S4 全字段缓存覆盖完整，90%+ 满分）
事件分类: A=264  D=133  B=64
高频缺失因子: dt_profit×136  roe×2
等级分布: REJECT=198  WATCH=152  WAIT_CONFIRM=89  WAIT_PULLBACK=10  TEST_BUY=12
防默认分审计: ARS None=0只  GAP None=0只（规格: 缺失不得给默认分 → 已达标）
```

## 六、已知局限（如实标注）

1. **D4 现金流假阳性**：江波龙类高景气周期股（利润暴增 + OCF 同比极负）会被 D4 误杀——H1 OCF 季节性与低基数效应。当前按规格忠实执行；后续可加"周期景气豁免"或改为置信度扣分而非硬剔除。
2. **Confidence 区分度不足**：S4 全字段缓存使 conf≈100，门槛作用弱。若未来池来源变多（S2/S3 仅少数字段），区分度自动恢复。
3. **恒誉环保改名**：stock_basic 现名"恒誉科技"，旧报告用旧名，对比时按 ts_code 匹配。
4. **主题白名单为静态 12 个**（EGPT 同款）；±5 分封顶符合规格，不主导排名。
5. **无实时资金流**：规格禁止引入，未实现。
6. **ST/*ST 未过滤**：榜单出现 *ST八钢/ST嘉澳/ST沈化，风险高，若需可加 ST 排除开关。

## 七、产物清单

- `er20_v2.py`（新程序，不动旧 er20_strategy.py）
- `report_daily/er20_v2_report_20260820.md`（4 榜单 + 个股报告）
- `report_daily/er20_v2_scores.db`（因子落库，Forward 回测用）
- 运行：`python -X utf8 er20_v2.py --date 20260820 [--compare] [--validate]`
