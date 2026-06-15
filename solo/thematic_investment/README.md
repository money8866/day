# 主题投资系统 (Thematic Investment System)

> 一个面向 A 股市场的量化主题投资框架，整合**多数据库连接**、**LLM 主题识别**、**行情数据采集**和**策略回测**能力。

---

## 1. 项目目录结构

```
thematic_investment/
├── config/                     # 配置文件目录
│   └── config.yaml             # 全局配置 (数据库、API、回测参数)
├── data/                       # 数据目录
│   ├── cache/                  # 行情缓存、临时文件
│   └── output/                 # 回测结果、报表输出
├── modules/                    # 核心 Python 模块
│   ├── db_connector.py         # 数据库连接管理 (MongoDB/PostgreSQL/Milvus)
│   └── utils.py                # 通用工具 (日志、交易日历、装饰器)
├── backtest/                   # 回测策略模块 (后续扩展)
│   └── __init__.py
├── logs/                       # 日志文件目录 (运行时自动创建)
├── main.py                     # 主入口程序 (演示各模块)
└── README.md                   # 项目说明 (本文件)
```

### 目录职责
- **`config/`** — 所有运行时参数的集中地，便于维护。
- **`data/`** — 原始数据、缓存、输出分离子目录，便于 `.gitignore`。
- **`modules/`** — 基础设施模块 (数据库、工具)，被 `main.py` 与 `backtest/` 共享。
- **`backtest/`** — 策略回测逻辑，将来可在此实现 `BacktestEngine`。
- **`main.py`** — 系统入口，负责配置加载、资源初始化、流程编排。

---

## 2. 快速开始

### 2.1 环境要求
- Python **3.10+**
- 操作系统：Windows / macOS / Linux

### 2.2 安装依赖

```bash
cd thematic_investment
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 安装核心依赖
pip install --upgrade pip
pip install pyyaml python-dotenv pymongo sqlalchemy psycopg2-binary \
            pymilvus pandas akshare tushare python-dateutil
```

> 若不需要 Milvus，可省略 `pymilvus`。
> 若不需要 tushare，可省略 `tushare` (交易日历默认使用 akshare)。

### 2.3 配置环境变量

在项目根目录创建 `.env` 文件，避免密码硬编码到 `config.yaml`：

```dotenv
# .env
MONGO_PASSWORD=your_mongo_password
PG_PASSWORD=your_postgres_password
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
TUSHARE_TOKEN=your_tushare_token_here
OPENAI_API_KEY=sk-xxxx (可选)
```

### 2.4 启动必要数据库 (Docker 推荐)

```bash
# MongoDB
docker run -d --name mongo -p 27017:27017 \
    -e MONGO_INITDB_ROOT_USERNAME=admin \
    -e MONGO_INITDB_ROOT_PASSWORD=your_mongo_password \
    mongo:6.0

# PostgreSQL
docker run -d --name postgres -p 5432:5432 \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=your_postgres_password \
    -e POSTGRES_DB=thematic_investment \
    postgres:15

# Milvus (单机 Standalone)
wget https://github.com/milvus-io/milvus/releases/download/v2.3.5/milvus-standalone-docker-compose.yml
docker compose up -d
```

> 若暂时不启动某数据库也无妨，`main.py` 会打印警告并跳过。

### 2.5 运行主程序

```bash
cd thematic_investment
python main.py
```

预期输出样例：

```
2026-06-15 09:00:00 - thematic_main - INFO - ============================================================
2026-06-15 09:00:00 - thematic_main - INFO -   主题投资系统 - 初始化完成
2026-06-15 09:00:00 - thematic_main - INFO -   数据源: tushare_pro
2026-06-15 09:00:00 - thematic_main - INFO -   回测区间: 2024-01-01 ~ 2026-06-01
2026-06-15 09:00:00 - thematic_main - INFO -   初始资金: 1000000.00 元
2026-06-15 09:00:00 - thematic_main - INFO - ============================================================
2026-06-15 09:00:01 - thematic_main - INFO - === MongoDB 演示 ===
2026-06-15 09:00:01 - thematic_main - INFO - 写入文档 ID: 66...
...
2026-06-15 09:00:02 - thematic_main - INFO - === 交易日历演示 ===
2026-06-15 09:00:02 - thematic_main - INFO - 2026-06-01 ~ 2026-06-15 共 10 个交易日
...
2026-06-15 09:00:03 - thematic_main - INFO - 资源清理完成，退出。
```

---

## 3. 关键模块说明

### 3.1 `modules/db_connector.py` — 数据库连接管理器

三个核心类：

| 类名 | 技术栈 | 适用场景 |
|------|--------|----------|
| `MongoConnector` | pymongo + 连接池 | 非结构化数据：主题成份股、原始 JSON、日志 |
| `PgConnector` | SQLAlchemy + psycopg2 | 结构化数据：交易日历、财务指标、回测结果 |
| `MilvusConnector` | pymilvus 2.x | 向量检索：主题语义、公司业务语义匹配 |

**所有连接器均实现：**
- **单例 + 连接池**：全局复用，避免每调用都建连接。
- **`with` 上下文管理器**：自动管理连接生命周期。
- **断线重连**：每次调用前 `ping`，自动重建失效连接。
- **密码从环境变量注入**：`config.yaml` 中 `$VAR_NAME` 占位符在启动时自动替换。

使用示例：

```python
from modules.db_connector import MongoConnector, PgConnector, MilvusConnector

# MongoDB
with MongoConnector() as db:
    db["themes"].insert_one({"name": "资源主题", "score": 85.5})

# PostgreSQL
from modules.db_connector import PgConnector
pg = PgConnector()
with pg.session_scope() as session:
    rows = session.execute("SELECT * FROM trade_calendar LIMIT 5")
    for r in rows:
        print(r)

# Milvus 向量检索
with MilvusConnector() as mc:
    results = mc.search(collection_name="theme_vectors",
                        vectors=[[0.1] * 1024], top_k=5)
```

---

### 3.2 `modules/utils.py` — 通用工具

| 组件 | 功能 |
|------|------|
| `setup_logger()` | 配置日志（文件滚动 + 控制台），返回 `logging.Logger` |
| `@retry` | 失败自动重试，支持指数退避 |
| `@handle_exception` | 捕获异常、记录日志、返回默认值 |
| `@timing` | 统计函数耗时 |
| `TradeCalendar` | A 股交易日历，支持 `tushare` / `akshare` 双源 |

交易日历示例：

```python
from modules.utils import TradeCalendar
from datetime import date

# 获取区间交易日
dates = TradeCalendar.get_trade_dates("2026-06-01", "2026-06-30", source="akshare")

# 判断某一天是否交易日
TradeCalendar.is_trade_day(date(2026, 6, 1))

# 上一个 / 下一个交易日
TradeCalendar.prev_trade_day("2026-06-15")
TradeCalendar.next_trade_day("2026-06-15")
```

---

### 3.3 `config/config.yaml` — 全局配置

支持分段：
- **`database.*`** — 三类数据库连接参数。
- **`api_keys.*`** — DeepSeek / OpenAI 等 LLM Token。
- **`data_sources.*`** — tushare_pro、akshare、新浪财经、东方财富。
- **`backtest.*`** — 回测时间、资金、手续费、滑点、风控参数。
- **`logging.*`** — 日志文件、级别、滚动策略。
- **`paths.*`** — 各数据子目录路径。
- **`thematic_system.*`** — 主题系统专属参数（一级主题列表、评分权重、阈值等）。

所有形如 `${VAR_NAME}` 的值会被同名环境变量替换，推荐与 `.env` 配合使用。

---

## 4. 后续扩展建议

1. **回测引擎 (`backtest/`)**
   - 新建 `backtest/engine.py`：实现事件驱动回测 `BacktestEngine`。
   - 新建 `backtest/strategies.py`：主题轮动、动量策略等。
   - 新建 `backtest/metrics.py`：收益、夏普、最大回撤等指标计算。

2. **主题识别 (`modules/theme_scanner.py`)**
   - 调用 DeepSeek LLM，基于行业 + 概念 + 资金流识别主线。
   - 将结果写入 MongoDB `themes` 集合。

3. **行情数据采集 (`modules/data_fetcher.py`)**
   - 日线、分钟级数据抓取，缓存至 PostgreSQL 或本地 parquet。
   - 支持增量更新。

4. **向量语义检索 (`modules/semantic_search.py`)**
   - 使用 Milvus 对行业 / 公司公告 / 研报做向量化。
   - 支持"给定主题 -> 找到最匹配成份股"。

---

## 5. 常见问题

| 问题 | 解决方式 |
|------|---------|
| `ValueError: Invalid utf8mb4 character` | 在 `config.yaml` 中确认数据库编码为 UTF-8 |
| `ModuleNotFoundError: No module named 'pymongo'` | 回到 **2.2** 安装依赖 |
| `MongoDB 不可用` | 确认 MongoDB 服务已启动并监听 `27017` 端口，且用户名密码匹配 |
| `交易日历返回空` | 检查网络是否能访问 akshare / tushare 数据接口 |
| `pymilvus 版本冲突` | `pip install --upgrade pymilvus`，建议 2.3.x |

---

## 6. 代码规范

- **类型注解**：所有公共函数签名均包含 `typing` 注解。
- **PEP 8**：使用 4 空格缩进，行宽 ≤ 120 字符。
- **日志分级**：`DEBUG` / `INFO` / `WARNING` / `ERROR` 合理使用。
- **异常处理**：对外接口使用 `@handle_exception`，内部异常向上抛出。

```python
# 推荐代码风格
from __future__ import annotations

from typing import Dict, List, Optional

def my_function(items: List[int], limit: Optional[int] = None) -> Dict[str, int]:
    """单行描述 + 详细参数/返回值说明。"""
    ...
```

---

**License**: 本项目为内部使用示例，请勿将真实密钥提交到版本控制。
