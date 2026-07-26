# TERE V1 — Theme & ETF Resonance Engine

主题与 ETF 共振引擎，用于 A 股每日主线主题识别与评分排名。

## 项目简介

TERE (Theme & ETF Resonance Engine) 是一个量化主题投资框架，通过多因子模型对 A 股概念主题进行每日评分和排名。核心思路是**共振**——当主题的 ETF、成分股扩散度、龙头股三位一体共振时，该主题成为市场主线的概率最高。

### 核心能力

- 每日 100+ 主题的自动评分排名
- 8 层因子打分体系（ETF强度、扩散度、龙头强度、纯度、共振、资金流、阶段、轮动）
- 6 阶段生命周期自动判定（萌芽→成长→扩散→主升浪→派发→消亡）
- 主线轮动概率预测（3日/5日/10日）
- 可解释 AI 输出——每个评分的理由一目了然
- 异步架构，支持 5000+ 股票和 100+ 主题并行计算

---

## 架构图

```
┌──────────────────────────────────────────────────────────────┐
│                         main.py                              │
│                       CLI 入口                                │
└─────────────────────┬────────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────────┐
│                    TERE Engine (api/engine.py)                │
│  主流水线: 加载配置 → 逐主题计算 → 排序排名 → 校验 → 存储   │
├──────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ETFService│ │StockSvc  │ │ThemeSvc  │ │  FactorRegistry  │ │
│  │数据获取  │ │数据获取  │ │配置加载  │ │  因子注册中心    │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┬─────────┘ │
│       │            │            │                │           │
│  ┌────▼────────────▼────────────▼────────────────▼─────────┐ │
│  │              因子流水线 (Layer-wise)                      │ │
│  │                                                          │ │
│  │  ETF强度层 → 扩散度层 → 龙头层 → 纯度层 → 共振层       │ │
│  │  → 资金流层 → 阶段判定 → 轮动预测 → 信号生成           │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │ StageStateMachine│  │ RotationPredictor│  │ScoreCalculator││
│  │ 生命周期状态机   │  │ 轮动概率预测器   │  │ 评分计算器   ││
│  └─────────────────┘  └──────────────────┘  └──────────────┘ │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  Repository (数据库持久化层)                             │ │
│  │  8张评分表: etf / leader / breadth / resonance / stage  │ │
│  │            / rotation / signal / daily_score              │ │
│  └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

## 安装步骤

### 环境要求

- Python 3.11+
- pip

### 安装

```bash
# 克隆项目后，进入目录
cd d:/mystock/solo

# 安装依赖
pip install -e .
# 或者手动安装核心依赖：
pip install pandas numpy sqlalchemy aiosqlite pyyaml

# 可选：Tushare 数据源
pip install tushare
```

### 依赖清单

| 包 | 版本 | 用途 |
|---|---|---|
| pandas | >=1.5 | 数据处理 |
| numpy | >=1.23 | 数值计算 |
| sqlalchemy | >=2.0 | ORM 数据库 |
| aiosqlite | >=0.19 | 异步 SQLite |
| pyyaml | >=6.0 | 权重配置 |
| tushare | (可选) | 行情数据源 |

## 配置说明

### 权重配置 (config/weights.yaml)

所有权重通过 `config/weights.yaml` 管理，修改无需改动代码。

```yaml
# 层级权重（总分100）
layer_weights:
  etf_strength: 30    # ETF强度
  breadth: 20          # 扩散度
  leader: 20           # 龙头强度
  purity: 10           # 纯度
  resonance: 10        # 共振
  flow: 5              # 资金流
  rotation: 5          # 轮动概率

# ETF强度子因子权重
etf_strength:
  trend: 0.20
  momentum: 0.15
  alpha: 0.10
  volume: 0.10
  money_flow: 0.10
  # ... 其他子因子

# 阈值
thresholds:
  strong_buy: 85
  buy: 70
  watch: 50
  reduce: 35
  exit: 20
```

### 数据库配置 (config/settings.py)

```python
# SQLite（开发，默认）
DATABASE_URL = "sqlite:///theme_engine/data/tere.db"

# PostgreSQL（生产）
# DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/tere"
```

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| TERE_DATABASE_URL | sqlite:///... | 数据库连接 |
| TUSHARE_TOKEN | "" | Tushare API Token |
| TERE_ECHO_SQL | "0" | 是否打印 SQL 日志 |

## 使用方法

### 命令行

```bash
# 计算指定日期全部主题
python -m theme_engine.main --date 20260724

# 仅计算，不保存数据库
python -m theme_engine.main --date 20260724 --dry-run

# 仅计算单个主题
python -m theme_engine.main --date 20260724 --single AI_COMPUTE

# 跳过特定层级
python -m theme_engine.main --date 20260724 --skip-flow --skip-rotation

# 详细日志
python -m theme_engine.main --date 20260724 --verbose

# 导出排行榜到 CSV
python -m theme_engine.main --date 20260724 --export ranking.csv
```

### Python API

```python
import asyncio
from theme_engine.api.engine import TERE

async def main():
    engine = TERE()
    result = await engine.run(trade_date="20260724", dry_run=True)

    for theme in result.ranking[:5]:
        print(f"#{theme.rank} {theme.theme_name} "
              f"总分:{theme.total_score:.1f} "
              f"信号:{theme.signal}")

asyncio.run(main())
```

### 数据库初始化

```bash
# SQLite 自动创建（首次运行时自动建表）
python -c "from theme_engine.repository.repository import Repository; import asyncio; asyncio.run(Repository().initialize())"

# PostgreSQL 手动建表
psql -d tere -f theme_engine/sql/schema.sql
```

## 因子说明

### 8 层评分体系

| 层级 | 权重 | 说明 | 数据来源 |
|---|---|---|---|
| ETF强度 | 30% | ETF价格趋势、动量、成交量、资金流 | ETF日线 + 基金数据 |
| 扩散度 | 20% | 成分股上涨比例、涨停数、均线位置 | 个股日线 |
| 龙头强度 | 20% | 龙头股趋势、alpha、资金流入 | 个股日线 + 资金流 |
| 纯度 | 10% | 成分股与主题的平均关联度 | 主题映射CSV |
| 共振 | 10% | ETF、扩散、龙头的一致性 | 前3层结果 |
| 资金流 | 5% | ETF净流入、主题成交额变化 | 资金流数据 |
| 阶段 | (参考) | 生命周期6阶段判定 | 前6层指标 |
| 轮动概率 | 5% | 未来3/5/10日延续主线的概率 | 历史动量 |

### 生命周期阶段

```
birth(萌芽) → growth(成长) → expansion(扩散) → main_trend(主升浪) → distribution(派发) → death(消亡)
```

- 使用动态评分而非固定阈值
- 禁止阶段逆序（如 main_trend 不能回到 growth）
- 允许跳级（如直接进入 death）

## 输出格式示例

### 排行榜输出

```
============================================================
  TERE V1 排行榜 - 20260724
============================================================
 排名 主题名称               总分   ETF    扩散    龙头   信号
------------------------------------------------------------
   1 AI算力                 82.3   85.0   78.0   82.0   STRONG_BUY
   2 半导体                 75.1   72.0   80.0   70.0   BUY
   3 低空经济               68.5   65.0   72.0   65.0   BUY
   4 机器人                 62.0   60.0   58.0   68.0   WATCH
   5 新能源                 55.3   50.0   52.0   60.0   WATCH
```

### 可解释 AI 示例

```json
{
  "theme_code": "AI_COMPUTE",
  "theme_name": "AI算力",
  "total_score": 82.3,
  "explanations": [
    {
      "reason": "ETF强度良好 (85分, 权重30%)",
      "score": 85.0,
      "weight": 30
    },
    {
      "reason": "扩散度良好 (78分, 权重20%)",
      "score": 78.0,
      "weight": 20
    },
    {
      "reason": "龙头强度强势 (82分, 权重20%)",
      "score": 82.0,
      "weight": 20
    },
    {
      "reason": "处于主升浪阶段",
      "score": 0,
      "weight": 0
    },
    {
      "reason": "强烈买入信号",
      "score": 0,
      "weight": 0
    }
  ],
  "summary": "今日最强主线为AI算力，共振强度85.0，处于主升浪阶段，主线延续概率82%，综合评分82.3"
}
```

### CSV 导出格式

```csv
rank,theme_code,theme_name,total_score,etf_strength,breadth,leader,purity,resonance,flow,stage,signal,main_etf
1,AI_COMPUTE,AI算力,82.3,85.0,78.0,82.0,75.0,80.0,70.0,main_trend,STRONG_BUY,159995.SZ
2,SEMICONDUCTOR,半导体,75.1,72.0,80.0,70.0,68.0,65.0,60.0,expansion,BUY,512480.SH
```

## 可解释 AI 示例

TERE V1 在设计上强调可解释性。每个评分结果都附带了详细的解释链：

```
综合评分: 82.3/100
  - ETF强度良好 (85分, 权重30%)
  - 扩散度良好 (78分, 权重20%)
  - 龙头强度强势 (82分, 权重20%)
  - 纯度一般 (60分, 权重10%)
  - 共振强度良好 (80分, 权重10%)
  - 资金流一般 (55分, 权重5%)
  - 轮动概率较高 (82分, 权重5%)
  - 处于主升浪阶段
  - 强烈买入信号
```

这使得用户可以清晰地理解每个评分的来源，而不仅仅是得到一个数值。

## 错误处理

- 单因子失败不会影响其他因子
- 单主题失败不会影响其他主题
- 数据库写入失败不会影响计算
- 所有外部数据源都有 fallback 策略

## 开发指南

### 添加新因子

1. 继承 `BaseFactor` 并实现 `calculate()` 方法
2. 在 `weights.yaml` 中添加权重配置
3. 注册到 `FactorRegistry`

```python
from theme_engine.factor.base import BaseFactor
from theme_engine.factor.registry import get_registry

class MyCustomFactor(BaseFactor):
    name = "my_factor"
    version = "1.0.0"
    weight_key = "my_layer"

    async def calculate(self, theme_code, trade_date, **kwargs):
        # 计算逻辑...
        return FactorResult(...)

# 注册
registry = get_registry()
registry.register(MyCustomFactor(), layer="my_layer")
```

### 运行测试

```bash
# 运行全部测试
pytest theme_engine/tests/ -v

# 运行特定测试
pytest theme_engine/tests/test_engine.py -v -k "test_score_calculator"
```

## 项目结构

```
theme_engine/
├── __init__.py
├── main.py                    # CLI 入口
├── README.md                  # 本文件
├── config/
│   ├── __init__.py
│   ├── settings.py            # 路径与数据库配置
│   └── weights.yaml           # 权重配置（核心）
├── models/
│   ├── __init__.py
│   ├── dataclasses.py         # Pydantic 数据模型
│   ├── orm.py                 # SQLAlchemy ORM 模型
│   └── schemas.py             # JSON Schema
├── factor/
│   ├── __init__.py
│   ├── base.py                # BaseFactor 抽象基类
│   └── registry.py            # FactorRegistry 注册中心
├── services/
│   ├── __init__.py
│   ├── etf_service.py         # ETF 数据服务
│   ├── stock_service.py       # 股票数据服务
│   └── theme_service.py       # 主题数据服务
├── repository/
│   ├── __init__.py
│   └── repository.py          # 数据库持久化层
├── stage/
│   ├── __init__.py
│   └── state_machine.py       # 生命周期状态机
├── rotation/
│   ├── __init__.py
│   └── predictor.py           # 轮动概率预测器
├── score/
│   ├── __init__.py
│   └── calculator.py          # 综合评分计算器
├── validator/
│   ├── __init__.py
│   └── validator.py           # 自动校验器
├── api/
│   ├── __init__.py
│   └── engine.py              # 主引擎
├── tests/
│   ├── __init__.py
│   └── test_engine.py         # 单元测试
├── sql/
│   └── schema.sql             # 数据库 DDL
└── data/
    └── (运行时生成)
```
