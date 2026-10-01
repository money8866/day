# H-W7-RANK-01 — W7 每日横截面相对排序的独立增量

预注册规范（Pre-registration）。**本文件在任何结果被观察之前冻结。**

- `hypothesis_id` = `H-W7-RANK-01`
- `version` = `1.0`
- `created` = `2026-09-30`
- `parent` = `H-ALPHA-SOURCE-01`（已归档：`No robust Alpha`，本实验**不修改**其任何结论）
- `trading_authorization` = `NO`

---

## §0 为什么这是一个新 Hypothesis

`H-ALPHA-SOURCE-01` 的纪律明确禁止在其中继续寻找 Alpha。本实验是该纪律要求的"下一步"：

> 下一步必须建立新的独立 Hypothesis ID 进行 OOS 验证。

触发本实验的事后诊断（`research/h_alpha/_diag_tail.py`，仅描述性、不进入其预注册设计）发现：

```
HVT top3=1  n= 444   mean(T+20) +0.0380   P(r>=+20%) 0.151
HVT top3=0  n=1927   mean(T+20) +0.0215   P(r>=+20%) 0.099
```

即 **HVT 自己的每日 Top-3 相对排序把 P(+20%) 提升 1.53×**，而该诊断同时证明：
所有 10 个通用事前特征（动量 / 波动 / 换手 / 量比 / 市值 / 价格 / 距前高 …）的
|rank-IC| 全部 < 0.09，且六项在 2024↔2025 之间符号翻转。

因此本实验**不引入任何新指标**，只检验"相对排序"这一机制本身。

---

## §1 机制事实（冻结取证，只读）

| 项 | HVT-BULL | W7 (trade_execution V3.1) |
| --- | --- | --- |
| 账本 | `report_daily/te_backtest_events_20250101_20260828.csv` | `report_daily/te3_v31_events_20240101_20260828.csv` |
| 可执行行 | 7402 | 5882 |
| 决策日数 | 414 | 441 |
| 每日可执行笔数 mean / median / max | 17.88 / 16 / 58 | **13.34 / 9 / 73** |
| 每日 Top-N 截断 | **有**，`max_buy_candidates = 3` | **无** |
| 排序键 | `execution_score` desc, `buyability` desc | 无 |

- HVT 截断代码：`hvt_bull/te_backtest.py:530-543`（`groupby('decision_date').head(max_buy)`），
  config 键 `max_buy_candidates` 读取于 `:437`。
- W7 无截断：`w7_te_v3_backtest.py` 全文不存在任何按 `signal_date` 的横截面 Top-N。
- **关键缺口声明**（原文引用 `w7_te_v3_backtest.py:426`）：

  > 注意：实盘 W7 另有 SLI 龙头池过滤与每日榜单截断，本回测样本为该口径的**近似超集**。

  即：实盘 W7 **本来就有每日榜单截断**，只是回测账本未建模。因此"给 W7 补 Top-N"
  不是发明新东西，而是**修补回测与实盘之间一个已知的、被记录在案的口径缺口**。

---

## §2 假设

**H0（Null）**
> W7 每日内部的可执行名单相对排序不含增量信息；只取前 N 名带来的任何改善，
> 完全等价于"随机少取 N 名"。

即：
```
Ranked - Base      ~ 0
Ranked - Random-N  ~ 0        <== 这是核心判决点
```

**H1（Alternative）**
> 在**控制笔数**（同日期、同 N）之后，按固定键排序取前 N 名，
> 相对随机抽取同日期同数量的 N 名，仍具有稳定增量。

---

## §3 冻结口径

### 3.1 研究对象
- W7 账本 `te3_v31_events_20240101_20260828.csv`，`action ∈ {PRIMARY BUY, CONDITIONAL BUY}`。
- **不修改** W7 的 `classify` / `v31_decision` / 任何门限 / 任何权重。
- **不修改** HVT 的任何定义。

### 3.2 收益口径（与父实验逐字一致）
```
event   = W7 的 event_date
decision= W7 的 signal_date
entry   = open[decision + 1]            # A 股 T+1，生产口径
exit    = close[entry + h]
horizons= (3, 5, 10, 20),  primary = 10
cost    = (0, 10, 20, 30, 50)bp, primary = 30bp
universe= has_common.eligibility()（traded / 非ST / 非退市 / 上市>=250日 / 非长停牌 / 排除北交所）
```
匹配与全部反事实腿共用同一 qfq 面板，与父实验完全一致。

### 3.3 sample pipeline（**各臂逐字相同**）
```
1. actionable 行（action ∈ {PRIMARY BUY, CONDITIONAL BUY}）
2. [仅 RANKED / RANDOM-N 臂] 按 signal_date 做 Top-N 截断 / 随机 N 抽取
3. dedup on (stock, event_date) keep first
4. overlap guard: 同股票相邻 entry_idx 间隔 > 20 会话
```

### 3.4 臂（Arms）
| 臂 | 定义 |
| --- | --- |
| `BASE` | 全部 actionable（= 冻结 W7 现状，无截断） |
| `RANKED` | 每个 `signal_date` 取前 N 名 |
| `COMPLEMENT` | `BASE` 中未进入 `RANKED` 的部分 |
| `RANDOM-N` | 每个 `signal_date` 从 actionable 池**无放回**均匀抽 N 名，`B = 1000` 次 |

`RANDOM-N` 保持 `日期分布` 与 `每日笔数` 不变，只破坏 `排序`。
它是唯一能区分"排序有效"与"少取就好"的对照。

### 3.5 排序键（**预先固定，全部报告，禁止事后挑选**）

**主配置 PRIMARY**
```
N = 3
key = (exec desc, eq desc)
```
理由（结构同构，非结果驱动）：
- `exec` 是 W7 的执行分（`trade_execution_engine.py:450-453`，权重 `WEIGHTS`，`clip[0,100]`），
  与 HVT 的 `execution_score` 是同一角色。
- `eq` 是入场质量分，与 HVT 的 `buyability` 是同一角色。
- HVT 的 `max_buy_candidates = 3` → N 取 3。

**次配置（全部报告，不用于判决）**
```
N    ∈ {1, 2, 3, 5, 10}
key  ∈ { (exec,eq), (exec,score), (score), (exec), (eq), (drisk asc then exec) }
```

所有键字段均在决策日已确定且已存在于账本，**不新造任何指标**：
- `exec` 由 `v31_decision()` 在决策日算出（`trade_execution_engine.py:450-453`）
- `score` 由 `hvt_v3_score()` 在决策日算出（`w7_second_wave_engine.py:1181-1188`）
- `eq` 由 `classify()` 在决策日给出
- `drisk` 由 `hvt_distribution_risk()` 在决策日给出
- 已逐项核验：**全部无未来函数**

### 3.6 正对照（Positive Control，强制）
同一套机器**必须**跑一遍 HVT 自己的账本（其 Top-3 已存在）：
```
HVT_RANKED = HVT 账本自身的 Top-3     (te_backtest.py:530-543 口径)
HVT_COMPLEMENT = HVT actionable 中未被 Top-3 选中的部分
HVT_RANDOM-3 = 同决策日随机 3 名
```
判决规则：
> 若 `HVT_RANKED - HVT_RANDOM-3` 不复现为正，
> 则说明**这套检验机器本身无法识别一个已知有效的排序**，W7 的结论随之作废。

### 3.7 统计
- 按**月份聚类 Bootstrap**，`B = 1000`，`seed = 20261101`（与父实验同种子）
- 输出 `mean difference / 95% CI / two-sided p`
- OOS 切分：`IS = 2024`，`VALID = 2025`，`OOS = 2026`（沿用父实验）
- Regime 分解：`hrbp_common.regime_by_day`（CSI300 vs MA60 + 60日涨幅 ±5%），**仅作解释变量**
- Tail：`Full / leave top 1% / 5% / 10%`

### 3.8 判决（ROBUST SOURCE，八条必须同时满足）
```
1. Ranked - Random-N > 0            (核心)
2. Ranked - Random-N 在 30bp Net 后 > 0
3. OOS 2026 > 0
4. 至少两个年份 > 0
5. 不是单一 regime
6. leave-top-5% 后仍 > 0
7. Bootstrap CI 不跨越 0（或方向完全一致）
8. Bias Audit 无 FAIL
```
否则 `UNPROVEN`。

---

## §4 明确的不做什么

```
X 不优化 N
X 不优化排序键
X 不加任何新技术指标
X 不修改 W7 / HVT 的任何门限与权重
X 不用结果反过来改候选池或事件定义
X 不使用未来数据
X 不因为某个 N 或某个键"看起来最好"而把它升级为主配置
X 不把本实验结论直接升级为生产策略
```

---

## §5 预先声明的方法学限制

1. **研究单位是 per-trade mean，不是组合收益。**
   取 Top-3 会**减少资金占用**（W7 由 3460 笔降至约 1/4）。即便 per-trade 均值上升，
   也**不能**据此宣称组合收益改善——本实验不建资金模型，不做此宣称。
2. **实盘截断规则未知。**
   `w7_te_v3_backtest.py:426` 只声明实盘"有 SLI 龙头池过滤与每日榜单截断"，
   未给出具体规则。本实验的 Top-N 是**独立假设**，不是对实盘规则的还原。
3. **`RANDOM-N` 保留日期与笔数，但破坏了个股分散度结构**（随机抽 3 名可能同行业聚集），
   该差异已在 Bias Audit 中记为已知残差。
4. **月聚类数约 30–33**；分年份 bootstrap 每年约 12 个聚类，置信区间会偏宽。
5. 生产风控层（结构止损 / 右尾回撤 / 双止损）**按设计排除**：本实验归因信号，不归因资金管理。

---

## §6 输出文件

```
data/H_W7_RANK_01_ARMS.csv          各臂描述统计（含 HVT 正对照）
data/H_W7_RANK_01_LATTICE.csv       N × key 全格点（预先固定，全部报告）
data/H_W7_RANK_01_NULL.csv          RANDOM-N 空分布与 bootstrap 对比
data/H_W7_RANK_01_OOS.csv           年度 / phase
data/H_W7_RANK_01_REGIME.csv        regime 分解
data/H_W7_RANK_01_TAIL.csv          leave-top-k%
data/H_W7_RANK_01_BIAS_AUDIT.csv    偏误审计
data/H_W7_RANK_01_SUMMARY.json      最终判决
H_W7_RANK_01_REPORT.md              报告
```

---

**冻结时间**：2026-09-30
**纪律**：与本文件不一致的任何事后调整，一律视为违反预注册，须在报告中披露。
