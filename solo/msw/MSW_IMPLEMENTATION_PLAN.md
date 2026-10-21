# MSW 1.0 实施计划与现有数据资产清单

> 依据任务书 §2.1：编写代码前先扫描现有代码库。本文档为扫描结论 + 实施计划 + 数据缺口报告。
> 生成时间：2026-10-20。扫描方式：只读检查文件系统与 SQLite 缓存，未修改任何现有文件。

---

## 一、现有项目结构（扫描结论）

### 1.1 项目布局

| 路径 | 内容 | 与 MSW 的关系 |
|---|---|---|
| `D:\mystock\solo\` | 量化研究主包（无 `__init__.py`，以目录内脚本 + 子包混合组织） | MSW 落点：`solo/msw/` |
| `solo/hve_v1/` | **同构模板**：高量事件策略，含 config/数据层/指标/事件/状态机/回测/报告/测试 | MSW 模块组织参照此目录约定 |
| `solo/hvt_bull/` | HVT-BULL 策略，`data_loader.py` 是共享数据加载器 | 只读复用其 `DB_PATH` 常量，不 import 其业务逻辑 |
| `solo/e08_scanner.py` | E08 跳空缺口策略（任务书 §6 允许作为可选子策略参照，**不得修改**） | MSW 不 import E08，仅作为对照基线思路 |
| `solo/position_gate/` | 仓位门控（市场状态过滤参考） | 只读参考 |
| `solo/inst_pullback_v2/`、`solo/mainline_pullback/`、`solo/timing_trading_system/`、`solo/multi_factor_picker/` | 既有回踩/主线/择时/多因子模块 | 只读参考，不修改 |
| `run_all.bat` | 每日全流程调度入口 | **不接入**（MSW 为独立研究模块，未经授权不改调度） |
| `config/`、`solo/config/` | 既有配置 | 不修改；MSW 用自带 `msw_config.json` |

### 1.2 现有模块调用关系（与隔离原则的冲突检查）

- 每日流程：`run_all.bat` → theme_score_v2 / market_analysis / position_gate / etf_mainline / volume_surge_select / tushare_quant / l3_resonance_scanner / genindex / push。
- MSW 为**纯新增独立目录**，不 import、不修改任何上述模块；仅以**只读 SQLite** 方式复用数据缓存。
- 唯一跨模块引用：读取 `solo/hvt_bull/data_loader.py` 中的 `DB_PATH` 常量约定（实际在 MSW 内部自行定义，零 import，彻底隔离）。

---

## 二、数据资产清单（唯一数据源：`D:\mystock\cache_daily\stock_data.db`）

### 2.1 核心行情表

| 表 | 行数 | 日期范围 | 股票数 | 字段 | 用途 |
|---|---|---|---|---|---|
| `daily_cache` | 7,169,675 | 2021-01-04 ~ **2026-10-20** | 5,789 | open/high/low/close/pre_close/pct_chg/vol/amount（**不复权**） | MSW 主价格源 |
| `adj_factor_cache` | 4,948,859 | 2023-01-03 ~ 2026-10-20 | 5,736 | adj_factor | 复权因子（已验证 `close_hfq = close × adj_factor`，与 stk_factor_pro.close_hfq 完全一致） |
| `daily_basic_cache` | 7,146,353 | 2021-01-04 ~ 2026-10-20 | 5,789 | turnover_rate/volume_ratio/total_mv/circ_mv/pe/pe_ttm/pb/dv_ttm/float_share/total_share | 流动性、市值、估值 |
| `index_daily_cache` | 16,259 | 2018-01-02 ~ 2026-10-20 | 9 个指数 | 同 daily_cache | 宽基相对强度基准 |
| `index_member_cache` | 76,000 | — | 仅 `932000.CSI` | con_code/weight | 中证2000 成份（可用于小盘代理） |
| `fina_indicator_cache` | 76,773 | ann_date 2023-04-07 ~ 2026-08-29 | 5,546 | grossprofit_margin/netprofit_yoy/or_yoy/netprofit_2yoy/ocf_to_or/ocf_yoy/roe/debt_to_assets，**含 ann_date（公告日）** | 基本面分层（PIT 可用） |
| `stk_factor_pro` | 4,896,190 | 2023-01-03 ~ 2026-09-16（全口径）；1990 起仅 104 只老股票 | 5,957 | 含 MA/ATR/BOLL/RSI/MACD 等 300+ 预计算因子（bfq/hfq/qfq） | **仅作交叉校验**，不作为 MSW 输入（截止日旧 1 个月，避免混源） |

### 2.2 可用指数（相对强度基准）

`000001.SH`（上证）、`000300.SH`（沪深300）、`000905.SH`（中证500）、`000852.SH`（中证1000）、`932000.CSI`（中证2000）、`000688.SH`（科创50）、`399006.SZ`（创业板指）、`399001.SZ`（深成指）、`000985.CSI`（中证全指）。

### 2.3 股票基础信息

`D:\mystock\cache_daily\stock_basic.csv`：5,530 只，字段 `ts_code/name/industry/list_date`。
- 行业字段为通达信/同花顺风格细分行业（如「银行」「全国地产」），可直接用作行业代理与行业集中度限制。

### 2.4 幸存者偏差覆盖（§4.1 硬性要求）

`daily_cache` 含 **204 只已退市股票**（最后交易日早于 2025-01-01，如 `*ST` 退市整理样本），回测按历史日期还原股票池，天然保留退市样本。另 17 只于 2025-10 ~ 2026-10 间停止交易（退市或长期停牌）。

---

## 三、数据缺口报告（§2.2-4/5：不得编造数据）

| 缺口 | 影响 | MSW 处理 |
|---|---|---|
| `adj_factor_cache` 起于 2023-01-03；`daily_cache` 起于 2021-01-04 | 2021-2022 无复权因子，无法精确复权；MA250 需 250 根调整后K线 | **信号区间起点定为 2024-01-02**（2023-01-03 起 250 个交易日后）；2021-2022 仅用于热身，不产生信号 |
| 无 PIT 股票名称历史（stock_basic.csv 仅当前名称） | 回测早期 ST 股无法按当时状态剔除 | 已知局限：按当前名称剔除 ST（与 hve_v1 同口径），在报告中显式披露；204 只退市股保留以缓解幸存者偏差 |
| `fina_indicator_cache` 仅 8 个财务字段，无单季度营收/扣非利润明细 | 基本面分层只能用现有 8 字段 | A/B 类分层用 or_yoy/netprofit_yoy/grossprofit_margin/ocf_to_or/roe/debt_to_assets + ann_date PIT 规则近似；报告中声明局限 |
| 无行业指数日线（仅个股 industry 文本字段） | 行业相对强度用行业代理 | 用个股 industry 字段构建等权行业代理指数（每日行业内个股中位数收益） |
| 无逐笔/分钟线、无盘口 | 无法精细建模滑点与竞价 | 滑点用可配置 bp 参数 + 涨跌停不可成交假设（§8.1） |
| `stk_factor_pro` 截止 2026-09-16 | 不能作为回测末端数据源 | 不用；MSW 全部指标自算（数据可截断至 2026-10-20） |

---

## 四、MSW 架构与落点（§三/§十一）

路径：**`D:\mystock\solo\msw\`**（遵循 hve_v1 包约定，替代任务书建议的 `strategies/msw/`——任务书明确「具体路径以现有项目规范为准」）。

| 任务书模块 | MSW 文件 | 职责 |
|---|---|---|
| — | `__init__.py` / `msw_config.json` | 版本、策略ID、集中配置（全部标注 V1_INITIAL_HYPOTHESIS） |
| — | `experiment_preregistration.json` | 看测试结果前冻结的实验设计（§9.1） |
| data_adapter | `data_adapter.py` | 只读 stock_data.db：hfq 序列、截面、指数、PIT 财务、股票池掩码、退市处理 |
| feature_engine | `feature_engine.py` | 趋势/相对强度/回撤/波动/量价特征（全部 PIT） |
| swing_analyzer | `swing_analyzer.py` | PIT ZigZag 已确认转折点 + 250/500 日波段统计 |
| scanner | `scanner.py` | MSW-A 长期趋势筛选 + MSW-B 六子评分 + 标签 → 每日候选池 |
| signal_engine | `signal_engine.py` | C1/C2/C3 买点、止盈、趋势失效退出、再入场、七态状态机 |
| portfolio_engine | `portfolio_engine.py` | 资金分配（等权/ATR 波动率）、集中度限制 |
| backtest | `backtest.py` | 事件驱动回测：T+1、涨跌停、停牌、滑点、成本、净值曲线 |
| validation | `validation.py` | 5 类基线 + 匹配对照 + 消融 + 成本/参数敏感性 + 尾部依赖 + 市场状态切片 |
| report | `report.py` | §11 全部输出文件 + run manifest |
| tests | `tests/` | 无未来函数（核心）、信号逻辑、T+1/成本一致性 |

运行环境：`C:\Users\kongx\AppData\Local\Python\bin\python.exe`（Python 3.14.4 + pandas 3.0.2 + numpy 2.4.4，本机项目实际运行环境；WorkBuddy 托管 Python 3.13 无 pandas）。

---

## 五、关键设计决策（写入预注册，测试前冻结）

1. **价格基准**：后复权（hfq）= close × adj_factor（已验证与 stk_factor_pro.close_hfq 数值一致）。2023-01-03 前 adj 缺失的日期用不复权价（仅热身期）。
2. **信号区间**：2024-01-02 ~ 2026-10-20；IS（假设形成）= 2024-01 ~ 2025-12，OOS = 2026-01 ~ 2026-10-20。
3. **股票池**：沪深 A 股（剔北交所 8/4/9）；剔除当前名称 ST/退；上市 ≥250 交易日；20 日成交额中位数 ≥ 阈值；总市值 ≥ 阈值；保留历史退市股。
4. **无未来函数铁律**：ZigZag 转折点必须已被后续价格确认（确认滞后期 ≥1 日）才进入统计；所有特征截断可复现（测试保证）；信号日 T 只用 ≤T 数据，T+1 开盘价成交。
5. **成本**：基础往返 30bp（单边 15bp）；敏感性 20/50/80bp。
6. **执行约束**：T+1；涨停不可买、跌停不可卖（主板 ±10%、双创 ±20%、ST ±5%）；停牌/无成交不可成交；滑点可配置（基础 5bp）。
7. **评分权重冻结**：长期趋势 25% / 波段空间 20% / 修复能力 20% / 频率 15% / 量价 10% / 流动性 10%。
8. **判定纪律**：达不到 §10.3 六条即输出 `UNPROVEN` 或 `FAIL`，禁止调参至通过。

---

## 六、实施顺序

1. 骨架 + 配置 + 预注册（本文档同步冻结）
2. data_adapter → feature_engine（先跑通单股数据冒烟）
3. swing_analyzer（PIT ZigZag + 统计）
4. scanner（MSW-A/B，产出质量评分分布冒烟）
5. signal_engine（C1/C2/C3 + 退出状态机）
6. backtest 引擎（T+1/涨跌停/成本）
7. validation（基线 + 消融 + 敏感性）
8. tests（无未来函数优先）
9. 全量回测 + 报告 + 判定

---

## 七、测试套件与工程修复记录（2026-10-20/21）

测试套件：`msw/tests/`（pytest，58 项全过）：
- `test_pit.py`（核心）：截断一致性、未来变异不变性、恢复统计 PIT、确认滞后期
- `test_signals.py`：C1/C2/C3 规则边界、退出规则（含 C2 不被 MA120 误杀的回归）、
  RR 门槛、质量分门槛、C1>C3>C2 优先级（monkeypatch 集成测试）
- `test_backtest.py`：T+1 成交、涨停不可买/跌停不可卖、停牌顺延/超时、成本会计
  一致性（往返 40bp = 30bp 佣金 + 2×5bp 滑点）、分批止盈按剩余份额结算、单股一仓

编写测试过程中发现并修复的 4 个引擎缺陷（均有对应回归测试）：

1. **滑点双重计算**（portfolio_engine._cost_rate）：成本率里含 slippage_bp，而成交价
   又按 op×(1±slip) 调整 → 实际往返 50bp，超出配置意图（40bp）。修复：成本率只含
   佣金（单边 15bp），滑点仅在价格建模一次。
2. **gross/net 会计语义颠倒**（backtest._close_round）：原 net_return 完全不含佣金，
   gross_return 反而加回退出佣金。修复：net = 现金流入/现金流出 - 1（全成本，与组合
   权益曲线严格一致）；gross = 理论价（theo）收益，无滑点无佣金。
3. **跌停卖出检查方向写反**（backtest.simulate 卖出分支）：误用涨停条件
   `op_raw >= pc×(1+lim)`，导致跌停开盘误判可卖、涨停开盘反而顺延。修复：
   `op_raw <= pc×(1-lim)` 才顺延。
4. **deadline_bar 存在性依赖未来数据**（swing_analyzer.build_swings）：原实现仅在
   全量数据未找到修复时才记 deadline_bar，其存在性取决于未来价格 → 截断/全量运行的
   stats(T) 对「deadline 后恢复」的腿结论不一致（未来函数泄漏）。修复：down 段无条件
   记录 deadline_bar，stats(T) 统一按「T 前未见修复且 deadline 已过 → 失败」判定。

另修复状态机不一致（signal_engine.step）：C1/C3 信号就绪但 RR 不足时原先进 WATCH，
而 C2 同样情况进 BUY_SETUP。统一为 BUY_SETUP（结构就绪、等待 RR 达标）。

性能：StockWalker 行访问重构为 numpy 行字典（_row(t)），单股行走 ~0.6s；
walk_universe 逐股驱逐数据缓存控制内存。

---

## 八、全量运行结果与验证缺陷修复（2026-10-21）

### 8.1 首次全量运行（5789 股 → 4730 只入池，3445 只产生信号，227 轮）

| 指标 | IS（2024-2025） | OOS（2026） |
|---|---|---|
| 轮数 | 170 | 119 |
| 每轮净均值 | +2.92% | **-0.21%** |
| 区间总收益 | +57.4% | **-5.7%** |

组合层面：总收益 +1.6%，Sharpe 0.185，MaxDD -44.7%（峰值 171.9 万 → 期末 101.6 万）。
基线：B&H +20.2%（Sharpe 0.437）、MA趋势 +33.5%（0.543）、动量 -19.8%、
纯均值回归 +2.6%。成本敏感性：20bp +0.27%/轮、30bp +0.42%、50bp +0.23%、
80bp -0.01%（策略在 80bp 往返成本下消亡）。

### 8.2 首次运行暴露的验证层缺陷（全部修复，均有回归测试）

1. **参数扰动静默失效**（validation.parameter_stability）：网格用裸字段名
   （pullback_min_pct），_split_key 对无点号键返回空节名 → 覆盖写进 cfg[''] →
   9 组扰动全部跑出与子集基线逐位相同的结果（354 轮 / +1.71%），标准⑤形同虚设。
   修复：PARAM_SECTION 显式节映射 + 未映射参数告警跳过 + walk_universe 对
   不存在节告警（apply_cfg_overrides 提取为可测函数）。
2. **matched_control 零交易**：原实现把 MSW 选中股票的事件流限制到不相交的对照
   代码上模拟 → 全部事件被过滤 → 总收益 0.0。且"对对照股跑同一策略"在设计上空洞：
   对照股正是因未产生任何入场信号才入选，同策略必然零交易。修复为**安慰剂计时**
   （placebo timing）：把每个选中股的完整交易序列（同一信号日/规则/分批决策）
   移植到其行业+市值+流动性最接近的未选中对照股上，成交价/涨跌停/停牌/ATR 仓位
   全部用对照股自身数据重新模拟（load_bars_for_codes + build_placebo_events）。
   冒烟验证：32 轮 / 每轮 -2.27% / 总收益 -10.7%（MSW 本体 -1.5%），显著跑输。
3. **结束年份静默截断**（第三处 YYYYMMDD vs 带横杠比较）：_momentum_events 的
   调仓日过滤与指数区间过滤把 2026 年全部丢弃（'20261020' <= '2026-10-20' 为
   False）。修复：norm_date 归一化后比较。
4. **liquid_codes 窗口失效**：起算日期用带横杠格式与 YYYYMMDD 文本比较 →
   "近一年"实际变成全历史。修复：compact 日期 + 显式 end 上界。
5. **缓存驱逐从未生效**（backtest.walk_universe）：load_stock 的缓存键是调用时
   传入的原始字符串（带横杠），驱逐代码却用紧凑格式 → 4730 只股票的 DataFrame
   在全量运行中全部驻留内存（~700MB 潜在 OOM）。修复：键格式一致 + 新增
   load_bars_for_codes 同样逐股驱逐。
6. 新增行走缓存（--cache-walk/--use-cached-walk，含配置哈希校验），
   避免每次迭代重跑 45 分钟全市场行走。

### 8.3 修复后重跑（v2，2026-10-21 07:24 完成，判定以本结果为准）

全市场 4730 只入池 / 3445 只产生信号 / 227 轮，与 v1 本体数字一致（事件流未变），
但验证层全部有效化：

- **参数扰动 6 组真正生效**：pullback_min 0.04/0.08 = +1.689%（pct 分支在流动性子集
  不绑定），pullback_max 0.15/0.22 = +1.448%/+1.985%，zigzag 0.08/0.12 =
  +1.720%/+2.064%，符号一致率 6/6（标准⑤首次成为有效证据）
- **matched_control（安慰剂计时，1110 对）**：-6.47% vs MSW +1.59% → 选股 alpha
  约 +8pp；对照组每轮净均值 +0.05% ≈ 计时本身盈亏平衡
- **动量基线修正为 -59.5%**（v1 因日期格式坑丢失 2026 年，误报 -19.8%）；
  指数基线 +22.6%（沪深300，全区间）
- **消融关键发现**：no_vol_position 是唯一使每轮净均值转负的消融（-0.39%）
  → ATR 风险预算仓位是承重组件；no_trend_filter 轮数 +73% 但均值降至 1.02%
  → 趋势过滤以量换质；no_quality_score / no_fundamental 在流动性子集上不绑定
- **最终判定 FAIL**（2/6 通过：⑤参数一致、⑥30bp 成本档为正；①②③④ 全败）：
  OOS 每轮期望 -0.21%、IS/OOS 严重分裂（+2.92% → -0.21%）、Sharpe 0.185 全面
  劣于 B&H 0.437 / MA 趋势 0.543、剔除 Top5% 股票后期望 -0.59%（Top5% 贡献
  439% 净收益，极端集中）
- 新增 msw/regenerate_report.py：行走缓存 + 汇总 CSV → 2 分钟再生增强版验证报告
  （Q1/Q2/Q3 均为数据驱动结论），免重跑 3 小时全流程

结论：MSW 1.0 未通过预注册验收。唯一稳健的正贡献是选股维度（vs 匹配对照 +8pp），
但被样本外转负、顶部股票集中依赖与高换手成本（80bp 即消亡）抵消。V1 不作为
可实盘策略交付；后续方向见验证报告 Q2/Q3 与消融结论。
