# 159531（南方中证2000ETF）每日盘后动态仓位管理

一套面向单只 ETF 的、可运行 / 可回测 / 可定时执行的 A 股动态仓位管理系统。
**只生成交易建议，不连接券商、不自动下单。**

- 标的：`159531.SZ`（南方中证2000ETF）
- 跟踪指数：`932000.CSI`（中证2000，已通过 `index_basic` 校验，发布日 2023-08-11）
- 时区：`Asia/Shanghai`
- 数据源：TuShare Pro（`fund_daily` / `fund_adj` / `index_daily` / `trade_cal` / `fund_basic`）

---

## 一、系统架构与核心参数

```
TuShare Pro
   │  fund_daily / fund_adj / index_daily / trade_cal / fund_basic
   ▼
data_provider.py ── 增量同步、复权因子校验、数据质量检查、就绪判断
   │
   ▼
SQLite (data/etf159531.db)  etf_daily / index_daily / trade_calendar
   │                        runs / signals / oversold_events / crisis_events
   ▼
indicators.py ── MA / RSI(Wilder) / 年化波动率 / 累计收益 / 偏离度
   │
   ▼
strategy.py ── 五层模型
   │   ① 趋势基础仓位（MA20/60/120 两日确认 → 0/20/40/65%）
   │   ② 波动率上限（目标 12%，vol_cap = min(0.65, 0.12/effective_vol)）
   │   ③ 超卖状态机（NORMAL / OVERSOLD_WATCH / RECOVERY_1 / RECOVERY_2 / EXPIRED）
   │   ④ 危机防御开关（跌破 MA20 8% 且 vol20>35% → 上限 0%，允许最多 10% 试探）
   │   ⑤ 账户回撤风控（8%/12%/16%/18% 分层 + 18% 熔断恢复流程）
   ▼
risk_manager.py ── 单位净值(TWR)、回撤、分层上限、熔断状态机
   ▼
portfolio.py ── 账户快照、当前仓位、目标份额、100 份取整、现金约束、周度再平衡
   ▼
report.py ── Markdown / JSON / HTML（可选图）
   │
   ├── main.py        CLI：daily / backtest / report / schedule / init-account
   ├── scheduler.py   APScheduler 每个工作日 18:30
   └── backtest.py    A~E 策略对比 + 敏感性 + 压力测试 + 滚动窗口
```

**最终仓位（全部为 0~1 小数）**

```
final_target = min(trend_weight + oversold_weight,
                   vol_cap,
                   drawdown_cap,
                   crisis_cap,
                   breaker_cap,   # 熔断恢复期额外上限
                   0.65)
```

报告会逐项列出上限，并指出最终由哪一项约束决定。

### 核心参数（`config.yaml`）

| 模块 | 参数 | 默认 | 说明 |
|---|---|---|---|
| 趋势 | `trend.layers` | 0→0%, 1→20%, 2→40%, 3→65% | MA20/60/120 连续 2 日同侧确认后计数 |
| 波动率 | `volatility.target` | 0.12 | 目标年化波动率（小数，不是 12） |
| 波动率 | `volatility.floor_effective_vol` | 0.18 | `effective_vol = max(vol20, vol60, 0.18)` |
| 超卖 | `oversold.rsi_trigger` / `dev_ma20` | 20 / -0.08 | RSI6≤20 且低于 MA20 8% 触发事件 |
| 超卖 | `weight_step1` / `weight_step2` | 0.05 / 0.10 | 初步确认 +5pp，进一步确认累计 10pp |
| 超卖 | `exit_hold_days` / `cooldown_days` | 10 / 20 | 持有 10 日退出；退出后冷却 20 日 |
| 危机 | `crisis.dev_ma20` / `vol20_threshold` | -0.08 / 0.35 | 同时满足才进入 CRISIS |
| 危机 | `crisis.probe_max` | 0.10 | 危机中反弹确认后的最大试探仓 |
| 账户 | `risk.drawdown_tiers` | 8%→40%, 12%→25%, 16%→10%, 18%→0% | 按单位净值回撤取最严格档 |
| 熔断 | `risk.breaker` | 冷静 10 日；重启≤10%；10 日后≤25%；回撤≤12% 解除 | 指数连续 3 日站上 MA20/MA60 且 vol 门控 |
| 交易 | `trading.lot_size` | 100 | ETF 最小交易单位 |
| 交易 | `trading.weekly_rebalance` | true | 仅每周最后一个交易日再平衡 |
| 交易 | `trading.min_trade_gap` | 0.05 | 仓位变动 <5pp 不交易；降风险指令优先 |
| 回测 | `backtest.commission/slippage` | 各 0.05% | 单边成本，可配置并做敏感性测试 |

---

## 二、安装与配置

```bash
cd etf159531
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env      # Linux: cp .env.example .env
```

编辑 `.env`：

```ini
TUSHARE_TOKEN=你的TuShare_Pro_Token
```

Token 读取顺序：`TUSHARE_TOKEN` 环境变量 → 本机 `tushare` 已保存的 token。
**Token 不会写入日志、报告或数据库。** 若两者都没有，程序会明确报权限错误，不会伪造行情。

---

## 三、常用命令

```bash
# 每日决策（自动增量同步 + 数据就绪重试 3 次 + 报告 + 落库）
python main.py --mode daily

# 指定历史交易日重算（可补跑）
python main.py --mode daily --date 20261120

# 只用本地库、不访问 TuShare
python main.py --mode daily --date 20261120 --no-fetch

# 用数据库中的信号重新生成报告
python main.py --mode report --date 20261120

# 完整回测（默认 159531 上市日至最新数据）
python main.py --mode backtest
python main.py --mode backtest --start 20240101 --end 20261120

# 常驻调度（每个工作日 18:30，Asia/Shanghai）
python main.py --mode schedule

# 生成账户 CSV 模板
python main.py --mode init-account
```

退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 成功，或非交易日跳过（不生成新信号） |
| 2 | `DATA_NOT_READY`：目标日行情未就绪，仅展示最近有效数据，不写新信号 |
| 3 | 错误：Token/权限、数据质量、运行异常等 |

**幂等性**：同一交易日的信号、超卖事件、危机事件均为确定性全量重算并覆盖写入，
重复运行不会重复修改事件状态，也不会产生重复信号。

**数据故障处理**：

- 接口故障 / 数据未发布 → 重试 3 次（15s、30s、60s 指数退避），仍失败则 `DATA_NOT_READY`，
  使用最后有效数据生成「仅供展示」报告，`meta.executable=false`，不写入可执行信号。
- 权限不足 / 复权因子缺失 / 价格异常 / 零成交 → 明确报错，不用模拟数据填补。
- 当日行情未更新时，不会把上一交易日数据冒充当天。

---

## 四、账户录入（可选，但回撤风控必须）

未提供 `account/snapshot.csv` 时，程序进入**仅市场信号模式**：只计算市场模型仓位，
隐藏交易金额与账户回撤，并明确声明「未计算真实账户回撤、未给出账户风控通过结论」。

```bash
python main.py --mode init-account
copy account\template_snapshot.csv account\snapshot.csv
copy account\template_cashflows.csv account\cashflows.csv
copy account\template_trades.csv account\trades.csv
```

### 1. `snapshot.csv`（必需，启用账户模式）

```csv
trade_date,cash,etf_shares,other_assets
20260101,100000.00,0,0.00
20260105,20000.00,70000,0.00
```

- `trade_date`：快照日期（YYYYMMDD），可稀疏；程序按交易日 as-of 对齐并前向填充。
- `cash`：现金余额。
- `etf_shares`：159531 持仓份额。
- `other_assets`：其他已登记账户资产（可选，默认 0）。
- 若最后一条快照早于目标交易日，会按「持仓不变」顺延估值，并在报告中提示。

### 2. `cashflows.csv`（强烈建议）

```csv
trade_date,amount,note
20260101,100000.00,初始入金
20260301,-20000.00,部分出金
```

- `amount` 正数=入金，负数=出金。
- 程序用外部现金流调整后的**单位净值（TWR）**计算历史峰值与回撤，
  避免出入金造成虚假的回撤/新高：

```
unit_nav_t = unit_nav_{t-1} × (NAV_t − flow_t) / NAV_{t-1}
drawdown_t = unit_nav_t / max(unit_nav_≤t) − 1
```

### 3. `trades.csv`（可选，一致性校验）

```csv
trade_date,action,shares,price,fee,note
20260105,BUY,70000,1.010,35.35,首次建仓
```

用于校验交易记录与快照/现金是否自洽；不参与净值计算。

> 账户净值口径：`NAV = 现金 + ETF份额 × 当日未复权收盘价 + 其他资产`。
> 技术指标用复权收盘，成交与估值用未复权价格。

---

## 五、每日报告

输出目录：`reports/YYYYMMDD/`

- `report_YYYYMMDD.md`：按固定章节（市场行情 / 趋势 / 超卖 / 风险管理 / 最终交易建议 / 信号解释 / 风险提示）
- `report_YYYYMMDD.json`：机器可读的完整决策
- `report_YYYYMMDD.html`：卡片式报告，可选内嵌走势图

报告中的「最早执行日期」为下一交易日：盘后信号只能次日执行，不以信号日收盘价成交。

交易建议还会遵守：

- 仅每周最后一个交易日再平衡；
- 目标仓位变动不足 5pp 不交易；
- 账户熔断、危机触发等**降低风险**的指令不受周度规则限制，下一可交易时点优先执行；
- 买入份额按 `lot_size`（默认 100 份）向下取整，并受现金余额约束；
- 实际成交价变化时需重新计算可交易份额与现金约束。

---

## 六、回测

```bash
python main.py --mode backtest
```

输出：`reports/backtest/<时间戳>/`

- `summary.md` / `summary.json`：完整结果与限制说明
- `comparison.csv`：A~E 策略指标对比
- `equity_curves.png`：净值与回撤曲线
- `curve_A.csv ... curve_E.csv`：逐日净值
- `trades_*.csv`：成交明细
- `rolling_windows.csv`：60 交易日滚动窗口稳定性

### 回测口径

- 信号在 t 日收盘后生成，最早 t+1 交易日**开盘价**成交；不使用未来数据。
- 初始资金 100 万；佣金、滑点默认各 0.05%（单边），另测 0.10% / 0.20%。
- ETF 交易单位 100 份；现金余额约束；停牌/无法成交跳过；次日跳空按开盘价成交。
- 分红/拆分按复权因子变化折算为现金分配（本 ETF 当前复权因子恒为 1.0）。
- 指标：CAGR、MDD、Calmar、Sharpe、Sortino、波动率、最差单年、正收益月占比、
  年度收益、交易次数、年化换手、平均仓位、最长回撤修复时间。
- 对比：A 买入持有、B 固定 30%、C 仅趋势、D 趋势+波动率、E 完整五层。
- 额外实验：波动率目标 10/12/14/16%、均线组合、超卖模块开关、成本敏感性、
  2024 小微盘冲击、2025 上下半年、2026 样本外、60 日滚动窗口。
- **不使用 ETF 上市前的中证2000指数拼接成可交易回测**；指数仅用于熔断恢复门控。

### 真实数据演示结果（2026-11-20，仅市场信号模式）

程序已用真实 TuShare 数据完成端到端验证：

- ETF 收盘 1.124，当日 -5.15%；MA20 1.2623，偏离 -10.96%；RSI6 15.62；VOL20 40.92%。
- 趋势：MA20/60/120 全部向下，0 条确认 → 基础仓位 0%。
- 危机：偏离 ≤ -8% 且 vol20 > 35% → 危机开关开启，危机上限 0%。
- 超卖：RSI6≤20 且偏离≤-8%，但上一事件退出后仍在 20 日冷却期内，不新建事件。
- 最终目标仓位 **0%**，由「策略组合(趋势+超卖)」决定；最早执行日 20261123。
- 未提供账户快照 → 报告中账户回撤与交易金额显示为「需账户数据」。

回测（真实 ETF 区间 2023-09-18 ~ 2026-11-20，策略统一起点 2024-03-21）：

| 策略 | 年化 | 最大回撤 | Calmar | Sharpe | 平均仓位 |
|---|---|---|---|---|---|
| A 买入持有 | 7.21% | -31.79% | 0.23 | 0.39 | 100% |
| B 固定 30% | 3.27% | -9.24% | 0.35 | 0.41 | 29% |
| C 仅趋势 | -1.08% | -13.26% | -0.08 | -0.03 | 33% |
| D 趋势+波动率 | 0.26% | -10.26% | 0.03 | 0.08 | 25% |
| E 完整五层 | 0.26% | -10.26% | 0.03 | 0.08 | 25% |

2026 样本外（2026-01-05 ~ 2026-11-20）：买入持有 -23.42% / 最大回撤 -31.80%；
完整策略（MA20/60 版） -2.65% / 最大回撤 -7.05%。

**回测限制（必须阅读）**

- 真实 ETF 历史仅约 3.2 年，年度统计显著性低。
- 2024 年初压力测试受上市时间限制只能用 MA20/60 版本；2 个月短窗口叠加「周度再平衡 + 5pp 阈值」，
  可能出现 0 笔交易，0% 回撤只代表未建仓，不代表长期也能避开下跌。
- 本样本内超卖事件均未完成反弹确认，超卖模块未贡献增益（开关结果一致）。
- 未建模盘中冲击、涨跌停无法成交、ETF 折溢价与流动性分层；成本敏感性表用于评估该不确定性。
- 历史回测不代表未来收益，最大回撤可能突破 20%。

---

## 七、每天 18:30 自动执行

### 方式 A：内置调度器（跨平台）

```bash
python main.py --mode schedule
```

- APScheduler `BlockingScheduler`，`mon-fri 18:30 Asia/Shanghai`；
- 工作日触发后仍会用 `trade_cal` 判断是否交易日，非交易日跳过；
- `max_instances=1`、`coalesce=True`、`misfire_grace_time=3600`；
- 单次失败只记录日志，不终止调度。

建议用 systemd / Windows 服务 / 任务计划守护该进程。

### 方式 B：Windows 任务计划程序

```bat
schtasks /Create /TN "ETF159531_Daily" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:30 ^
  /TR "cmd /c cd /d D:\mystock\solo\etf159531 && C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe main.py --mode daily >> logs\task.log 2>&1"
```

要点：

- 把 Python 路径替换为 `where python` 的实际结果；
- 在任务属性中按需选择「不管用户是否登录都要运行」；
- 建议勾选「如果任务失败，每 5 分钟重试 3 次」（对应程序内部重试之外的第二层保障）；
- 手动补跑：`python main.py --mode daily --date YYYYMMDD`。

### 方式 C：Linux cron

```cron
CRON_TZ=Asia/Shanghai
30 18 * * 1-5 cd /opt/etf159531 && /usr/bin/python3 main.py --mode daily >> logs/cron.log 2>&1
```

日志：`logs/`；运行记录：`runs` 表（运行时间、数据日期、退出状态、程序版本、备注）。

---

## 八、项目结构

```
etf159531/
├─ config.yaml          # 全部策略/交易/回测参数
├─ .env.example         # Token 模板（真实 Token 只放 .env，且已被 .gitignore 忽略）
├─ requirements.txt
├─ main.py              # CLI 与每日决策编排
├─ settings.py          # 配置加载、Token 读取、日志、时区
├─ database.py          # SQLite 表结构与读写（upsert / 事件覆盖）
├─ data_provider.py     # TuShare 同步、限流、重试、质量校验、就绪判断
├─ indicators.py        # SMA / Wilder RSI / 年化波动率 / 回撤 / 特征表
├─ strategy.py          # 趋势、波动率、超卖状态机、危机开关、仓位优先级
├─ risk_manager.py      # 单位净值、账户回撤、分层上限、熔断状态机
├─ portfolio.py         # 账户 CSV、当前仓位、交易计划、lot 取整、周度规则
├─ report.py            # Markdown / JSON / HTML 报告
├─ backtest.py          # A~E 回测、敏感性、压力测试、滚动窗口
├─ scheduler.py         # APScheduler 18:30
├─ tests/               # 64 个单元测试
├─ account/             # 账户 CSV 与模板
├─ data/                # SQLite 数据库
├─ reports/             # 每日报告与回测结果
└─ logs/                # 运行日志
```

---

## 九、测试

```bash
python -m pytest tests -q
# 64 passed
```

覆盖：RSI（连续涨/跌/无变化）、均线趋势确认、波动率、超卖状态机、危机进入/解除、
回撤熔断恢复、仓位优先级、无账户模式、交易日历、无未来函数、重复运行幂等、
模拟成交与 ETF 交易单位。

测试可使用合成数据；**正式报告只使用 TuShare 真实行情**，不会把模拟数据标成真实历史。

---

## 十、风险与免责声明

- 本系统仅用于决策辅助，不连接券商、不自动提交订单。
- 数据完整性、接口延迟、复权因子异常都会影响信号；程序会报错或降级为展示模式。
- 回测未完全建模滑点、冲击成本、涨跌停、ETF 折溢价与流动性风险。
- 账户回撤风控基于快照与公开收盘价估算，未登记资产/负债不会自动纳入。
- 历史回测与 20% 回撤容忍度不构成未来收益或回撤保证；极端行情下回撤可能突破 20%。
