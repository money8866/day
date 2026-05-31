# 涨停跟踪系统 - 历史数据回溯与复盘指南

## 📊 功能概述

系统新增**历史数据回溯功能**，自动采集过去20个交易日的涨停数据并存储到SQLite数据库，方便历史复盘分析。

## 🗄️ 数据库结构

### 数据库位置
```
d:\mystock\solo\cache_limit_track\limit_history.db
```

### 数据表

#### 1. limit_stocks（涨停数据表）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| trade_date | TEXT | 交易日期 (YYYYMMDD) |
| ts_code | TEXT | 股票代码 |
| name | TEXT | 股票名称 |
| industry | TEXT | 所属行业 |
| close_price | REAL | 收盘价 |
| pct_change | REAL | 涨跌幅 |
| vol_ratio | REAL | 量比 |
| is_first_board | INTEGER | 是否第一板 (1/0) |
| limit_type | TEXT | 涨停类型 |
| seal_time | TEXT | 封板时间 |
| amplitude | REAL | 振幅 |
| turnover_rate | REAL | 换手率 |
| market_cap | REAL | 市值 |
| create_time | TEXT | 创建时间 |

#### 2. stock_analysis（分析数据表）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| trade_date | TEXT | 交易日期 |
| ts_code | TEXT | 股票代码 |
| name | TEXT | 股票名称 |
| wave2_prob | REAL | 二波概率 |
| callback_score | REAL | 回调幅度得分 |
| ma_score | REAL | 均线多头得分 |
| volume_score | REAL | 量能稳定得分 |
| breakout_score | REAL | 突破前期高点得分 |
| zt_count_score | REAL | 近期涨停次数得分 |
| market_score | REAL | 市场情绪得分 |
| deepseek_analysis | TEXT | DeepSeek分析结果 |
| create_time | TEXT | 创建时间 |

## 🚀 快速开始

### 方式1：完整回溯（推荐首次使用）

```bash
# 回溯过去20个交易日的数据
python full_backtrack.py
```

### 方式2：命令行回溯

```bash
# 回溯最近5个交易日
python limit_track_review.py --backtrack --days 5

# 回溯最近20个交易日（默认）
python limit_track_review.py --backtrack

# 强制刷新所有数据
python limit_track_review.py --backtrack --force
```

## 📊 历史数据查询

### 基本查询

```bash
# 查询所有历史数据
python limit_track_review.py --query

# 查询指定日期范围
python limit_track_review.py --query --start-date 20260501 --end-date 20260529

# 查询二波概率 >= 60% 的股票
python limit_track_review.py --query --min-prob 60
```

### 导出数据

```bash
# 导出到CSV文件
python limit_track_review.py --query --export history.csv

# 导出指定日期范围的高概率股票
python limit_track_review.py --query --start-date 20260501 --end-date 20260529 --min-prob 50 --export high_prob.csv
```

## 📈 使用示例

### 示例1：分析近期热点行业

```bash
# 查询过去10天涨停数据
python limit_track_review.py --backtrack --days 10

# 验证数据库
python verify_db.py
```

### 示例2：寻找二次启动机会

```bash
# 查询高概率二波股票
python limit_track_review.py --query --min-prob 60

# 导出分析
python limit_track_review.py --query --min-prob 50 --export wave2_opportunities.csv
```

### 示例3：历史数据复盘

```bash
# 完整回溯20天数据
python full_backtrack.py

# 查看特定日期数据
python limit_track_review.py --query --start-date 20260520 --end-date 20260525
```

## 🔍 数据验证

### 验证数据库内容

```bash
python verify_db.py
```

输出示例：
```
================================================================================
📊 SQLite数据库验证
================================================================================

【涨停数据表 - limit_stocks】
总记录数: 134

每日涨停数量:
  20260529: 24 只
  20260528: 30 只
  20260527: 21 只
  20260526: 27 只
  20260525: 32 只
```

## 💡 高级使用

### Python脚本中使用

```python
import sys
sys.path.insert(0, r"d:\mystock\solo")

from limit_track_review import query_history, save_to_sqlite

# 查询历史数据
df = query_history(
    date_range=('20260501', '20260529'),
    min_prob=60
)

print(df.head())

# 保存新数据
save_to_sqlite('20260530', stocks_data, analysis_data)
```

### 数据分析示例

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect(r"d:\mystock\solo\cache_limit_track\limit_history.db")

# 按行业统计涨停次数
df = pd.read_sql_query("""
    SELECT industry, COUNT(*) as count 
    FROM limit_stocks 
    GROUP BY industry 
    ORDER BY count DESC
""", conn)

print("热门行业 TOP 10:")
print(df.head(10))

# 查找连续涨停股票
df = pd.read_sql_query("""
    SELECT ts_code, name, COUNT(*) as zt_days
    FROM limit_stocks
    GROUP BY ts_code
    HAVING zt_days > 2
    ORDER BY zt_days DESC
""", conn)

print("\n连续涨停股票:")
print(df)

conn.close()
```

## ⚙️ 配置说明

### 自动更新机制

- **每日运行**：系统在每日收盘后自动采集当天数据
- **缓存优先**：优先使用缓存数据，减少API调用
- **增量更新**：只采集缺失日期的数据

### 性能优化

- **API限制**：每0.5秒采集一天数据，避免限流
- **批量处理**：支持一次性回溯多天数据
- **索引优化**：数据库自动创建索引，加快查询速度

## 📋 文件清单

| 文件 | 说明 |
|------|------|
| `limit_track_review.py` | 主程序（含回溯和查询功能） |
| `full_backtrack.py` | 完整回溯脚本 |
| `verify_db.py` | 数据库验证脚本 |
| `limit_history.db` | SQLite数据库文件 |

## ⚠️ 注意事项

1. **首次回溯**：首次使用需要回溯历史数据，建议在非交易时间运行
2. **API限制**：Tushare有接口调用限制，回溯时间不宜过短
3. **数据更新**：如需最新数据，使用 `--force` 参数强制刷新
4. **存储空间**：SQLite数据库会自动增长，定期清理可节省空间

## 🎯 使用建议

### 日常使用
```bash
# 早上9点前运行（自动使用上一个交易日）
python limit_track_review.py

# 下午16点后运行（使用当天数据）
python limit_track_review.py
```

### 周末复盘
```bash
# 回溯过去20天数据
python full_backtrack.py

# 分析高概率机会
python limit_track_review.py --query --min-prob 60
```

### 月度总结
```bash
# 导出全月数据
python limit_track_review.py --query --start-date 20260501 --end-date 20260531 --export may_summary.csv
```

## 📞 技术支持

如有问题，请检查：
1. 配置文件是否正确（`d:\mystock\config\.env`）
2. Tushare API是否可用
3. 数据库文件是否可读写
4. 缓存目录是否正常

---

**版本**：v2.2
**新增功能**：历史数据回溯与SQLite存储
**数据库**：SQLite 3
**创建日期**：2026-05-30
