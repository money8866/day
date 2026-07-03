# Theme Alpha Engine V3.0

A股主题轮动与主线识别系统

## 目标

寻找未来5~20个交易日最可能成为市场主线的主题

## 项目结构

```
theme_alpha_v3/
├── config.py           # 配置文件
├── cache.py            # 缓存模块
├── data_loader.py      # 数据加载
├── theme_builder.py    # 主题池构建
├── trend.py            # 趋势评分
├── capital.py          # 资金评分
├── sentiment.py        # 情绪评分
├── persistence.py      # 持续性评分
├── lifecycle.py        # 生命周期识别
├── leader.py           # 龙头识别
├── risk.py             # 风险评分
├── composite.py        # 综合评分和信号
└── main.py             # 主程序
```

## 评分维度

- **TrendScore**: 趋势评分 (0-100)
  - Relative Momentum (5/10/20/40日)
  - MA Breadth (站上MA5/MA10/MA20/MA60比例)
  - Trend Persistence (连续新高/EMA20向上/上涨天数)
  - Drawdown Quality (最大回撤/恢复速度)

- **CapitalScore**: 资金评分 (0-100)
  - 成交额占比
  - 成交额趋势
  - 资金流 (主买/超大单/大单/净流入)

- **SentimentScore**: 情绪评分 (0-100)
  - Breadth (上涨家数占比)
  - Strong Breadth (>3%/5%/8%)
  - Limit Up (涨停/炸板/封板成功率)
  - Heat (热度)
  - Relative Strength (相对市场强度)

- **PersistenceScore**: 持续性评分 (0-100)
  - 连续上涨天数
  - EMA20持续向上
  - 相对排名保持
  - 龙头连续强势

- **LifecycleScore**: 生命周期加分
  - Birth: +20
  - Expansion: +15
  - MainTrend: +10
  - Climax: -10
  - Decline: -30

- **RiskScore**: 风险评分 (0-100, 越高风险越大)
  - 近10日波动率
  - 振幅
  - 换手率
  - 连续大涨
  - 热度过高

- **LeaderScore**: 龙头评分 (0-100)

## 综合评分

```
CompositeScore = 
  0.25 * TrendScore +
  0.20 * CapitalScore +
  0.15 * SentimentScore +
  0.15 * PersistenceScore +
  0.10 * (50 + LifecycleBonus) +
  0.10 * LeaderScore +
  0.05 * (100 - RiskScore)
```

## 交易信号

- **Strong Buy**: 综合>80, 资金>70, 趋势>70, 阶段Birth/Expansion
- **Watch**: 综合>65
- **Hold**: 综合>55
- **Avoid**: 其他

## 运行方式

```bash
cd d:\mystock\solo\theme_alpha_v3
python main.py
```

## 输出结果

- `theme_alpha_result.json`: JSON格式结果
- `theme_alpha_result.csv`: CSV格式结果

## 缓存机制

- SQLite数据库缓存: 默认缓存24小时
- Parquet文件缓存: 日线数据
