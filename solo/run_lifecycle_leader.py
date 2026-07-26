"""运行生命周期和龙头识别分析"""
import csv
import json
import logging
import sys
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, r"d:\mystock\solo\theme_kg_v3")
sys.path.insert(0, r"d:\mystock\solo")

from theme_kg_v3.core.lifecycle import LifecycleAnalyzer
from theme_kg_v3.core.leader import LeaderIdentifier

TRADE_DATE = "20260724"
CSV_PATH = rf"d:\mystock\solo\report_daily\theme_stock_map_v2_{TRADE_DATE}.csv"
CONFIG_PATH = r"d:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json"

# 1. 加载主题配置
with open(CONFIG_PATH, encoding="utf-8") as f:
    theme_config = json.load(f)

# 2. 加载 CSV 并按主题分组
theme_stocks: dict[str, list[dict]] = defaultdict(list)
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        theme_name = row.get("主题英文KEY", "") or row.get("\ufeff主题", "")
        if not theme_name:
            continue
        theme_stocks[theme_name].append({
            "stock_code": row.get("股票代码", ""),
            "stock_name": row.get("股票名称", ""),
            "score": float(row.get("评分", 0) or 0),
        })

logger.info("加载 CSV: %d 只股票, %d 个主题", 
    sum(len(v) for v in theme_stocks.values()), len(theme_stocks))

# 3. 初始化分析器
lifecycle = LifecycleAnalyzer()
leader = LeaderIdentifier()

# 4. 遍历主题执行分析
for theme_code, cfg in theme_config.items():
    if not isinstance(cfg, dict):
        continue
    name_cn = cfg.get("name_cn", theme_code)
    
    # 获取该主题在 CSV 中的股票（按主题英文KEY匹配）
    stocks = theme_stocks.get(theme_code, [])
    if not stocks:
        continue
    
    logger.info("")
    logger.info("─" * 50)
    logger.info("主题: %s (%s) - %d 只股票", name_cn, theme_code, len(stocks))
    
    # ── 生命周期 ──
    # 构造简版 history（缺少的指标用默认值）
    history = [
        {
            "trade_date": TRADE_DATE,
            "momentum_5d": 0.0,
            "momentum_20d": 0.0,
            "volume_ratio": 1.0,
            "leader_count": len([s for s in stocks if s.get("score", 0) > 80]),
            "total_market_cap_billion": 0.0,
            "avg_return_5d": 0.0,
            "avg_return_20d": 0.0,
            "turnover_rate": 0.0,
            "sentiment_score": 50.0,
        }
    ]
    lc_result = lifecycle.analyze(theme_code, name_cn, history)
    logger.info("  生命周期: %s (置信度: %.1f)", lc_result.current_stage, lc_result.stage_confidence)
    
    # ── 龙头识别 ──
    # 构造简版 stocks 数据
    stock_list = [
        {
            "stock_code": s["stock_code"],
            "stock_name": s["stock_name"],
            "consecutive_limit_up": 0,
            "cumulative_return_20d": 0.0,
            "cumulative_return_60d": 0.0,
            "market_cap_billion": 0.0,
            "turnover_rate": 0.0,
            "is_leader": False,
            "leader_type": "",
            "volume_ratio": 1.0,
            "avg_return_5d": 0.0,
            "correlation_with_theme": 0.5,
        }
        for s in stocks[:50]
    ]
    ld_result = leader.identify(theme_code, name_cn, stock_list)
    logger.info("  龙头: %d | 核心: %d | 跟风: %d | 补涨: %d | 淘汰: %d",
        len(ld_result.leaders), len(ld_result.cores),
        len(ld_result.followers), len(ld_result.catch_up_candidates),
        len(ld_result.eliminated))

logger.info("")
logger.info("=" * 50)
logger.info("分析完成!")
