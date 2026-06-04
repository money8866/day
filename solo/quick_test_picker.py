#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
快速测试 theme_pattern_stock_picker 的数据结构
只筛选满足以下条件的股票：
1. 主线容量中军
2. 趋势新高
3. 距离5日线不远
4. 成交额20亿+
"""

import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

print("=" * 70)
print("快速测试 theme_pattern_stock_picker - 中军选股策略")
print("=" * 70)

# 先运行一次回溯到20260602，但不发送微信通知
import theme_pattern_stock_picker

# 修改 save_results，只保存到 cache 目录
original_save = theme_pattern_stock_picker.save_results


def filter_mid_cap_stocks(candidates):
    """筛选满足中军条件的股票：
    1. 主线容量中军
    2. 趋势新高
    3. 距离5日线不远
    4. 成交额20亿+
    """
    if not candidates:
        return []

    filtered = []
    for c in candidates:
        # 条件1: 主线容量中军（buy_type 为中军或中军突破）
        buy_type = c.get('buy_type', '')
        if buy_type not in ['中军', '中军突破']:
            continue

        # 条件2: 趋势新高（检查是否有新高相关字段）
        has_new_high = False
        if c.get('reason', ''):
            if '新高' in c.get('reason', '') or '突破' in c.get('reason', ''):
                has_new_high = True
        # 或者检查价格是否接近近期高点
        if c.get('close', 0) > 0:
            # 假设 high_20d 字段存在
            high_20d = c.get('high_20d', c.get('close', 0))
            if high_20d > 0 and c['close'] / high_20d >= 0.95:
                has_new_high = True
        if not has_new_high:
            continue

        # 条件3: 距离5日线不远（假设 ma5_b 字段表示偏离百分比）
        ma5_b = c.get('ma5_b', 0)  # 偏离5日线百分比
        if abs(ma5_b) > 3:  # 偏离超过3%
            continue

        # 条件4: 成交额20亿+
        amount_latest = c.get('amount_latest', 0)  # 单位可能是万或亿
        # 处理不同单位
        if amount_latest > 0:
            if amount_latest < 2000:  # 小于2000，可能是亿为单位
                if amount_latest < 20:
                    continue
            else:  # 大于2000，可能是万为单位
                if amount_latest < 200000:  # 20亿 = 200000万
                    continue

        filtered.append(c)

    return filtered


def temp_save_results(candidates):
    """临时保存函数，只保存到 cache 目录"""
    if not candidates:
        return

    # 应用筛选条件
    filtered = filter_mid_cap_stocks(candidates)
    print(f"\n[筛选结果] 原候选 {len(candidates)} 只 → 筛选后 {len(filtered)} 只")

    if not filtered:
        print("  没有满足条件的股票")
        return

    import pandas as pd
    df = pd.DataFrame(filtered)

    # 只保存到 cache_backbone_tushare 目录
    output_file_cache = os.path.join(theme_pattern_stock_picker.safe_cache_dir, 'theme_pattern_stocks.csv')
    try:
        df.to_csv(output_file_cache, index=False, encoding='utf-8-sig')
        print(f"\n✅ 缓存已保存: {output_file_cache}")
        print(f"保存的列: {list(df.columns)}")
        print(f"保存的数据行数: {len(df)}")
        print("\n筛选后数据:")
        print(df[['name', 'ts_code', 'close', 'pct_chg', 'market_cap', 'theme', 'buy_type', 'reason']].to_string())
    except Exception as e:
        print(f"❌ 保存缓存失败: {e}")


# 替换 save_results
theme_pattern_stock_picker.save_results = temp_save_results

# 临时禁用微信通知
theme_pattern_stock_picker.SERVERCHAN_KEY = None

import theme_trend_sentiment_score as theme_score
from datetime import datetime, timedelta
theme_score.TRADE_DATE = '20260602'
theme_score.START_DATE = (datetime.strptime(theme_score.TRADE_DATE, "%Y%m%d") - timedelta(days=theme_score.N_DAYS + 30)).strftime("%Y%m%d")
print(f"\n[Backfill] 回溯模式: {theme_score.TRADE_DATE}")

candidates, good_themes = theme_pattern_stock_picker.select_stocks()

print("\n" + "=" * 70)
print(f"获取到 {len(candidates)} 个候选股票")
print("=" * 70)

if candidates:
    # 显示第一个股票的完整数据结构
    print("\n第一个股票数据:")
    print(candidates[0])
    print("\n数据字段:")
    print(list(candidates[0].keys()))

    # 检查 theme_type 字段
    print("\nTheme Type 分布:")
    type_counts = {}
    for c in candidates:
        t = c.get('theme_type', 'unknown')
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, cnt in type_counts.items():
        print(f"  {t}: {cnt} 只")

    # 检查 buy_type 分布
    print("\nBuy Type 分布:")
    buy_counts = {}
    for c in candidates:
        t = c.get('buy_type', 'unknown')
        buy_counts[t] = buy_counts.get(t, 0) + 1
    for t, cnt in buy_counts.items():
        print(f"  {t}: {cnt} 只")

    # 调用修改后的 save_results（包含筛选）
    temp_save_results(candidates)