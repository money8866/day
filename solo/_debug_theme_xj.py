import sqlite3

db_path = 'd:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 回溯日期: 20260616
TARGET_DATE = '20260616'

# 获取过去60天交易日(截至20260616)
cursor.execute(f"""
    SELECT DISTINCT trade_date FROM theme_scores 
    WHERE trade_date <= '{TARGET_DATE}' 
    ORDER BY trade_date ASC LIMIT 60
""")
all_dates = [r[0] for r in cursor.fetchall()]
total_days = len(all_dates)
print(f"分析最近 {total_days} 个交易日（截至{TARGET_DATE}）")

# 模拟每日TOP5逻辑: 按composite_score排序取前5
recent_window = min(10, total_days // 3)
recent_dates = set(all_dates[-recent_window:])
early_dates = set(all_dates[:-recent_window])

# 对每个交易日取TOP5，统计小金属/能源金属上榜情况
theme_stats = {}
for day_idx, td in enumerate(all_dates):
    cursor.execute(f"""
        SELECT theme, trend_score, sentiment_score, composite_score 
        FROM theme_scores WHERE trade_date = '{td}'
        ORDER BY composite_score DESC LIMIT 5
    """)
    top5 = cursor.fetchall()
    
    for row in top5:
        theme = row[0]
        if theme not in theme_stats:
            theme_stats[theme] = {
                'total_top5_count': 0,
                'recent_top5_count': 0,
                'early_top5_count': 0,
                'latest_trend': 0,
                'latest_sentiment': 0,
                'latest_composite': 0,
            }
        theme_stats[theme]['total_top5_count'] += 1
        if td in recent_dates:
            theme_stats[theme]['recent_top5_count'] += 1
        else:
            theme_stats[theme]['early_top5_count'] += 1
        
        # 最新数据
        theme_stats[theme]['latest_trend'] = float(row[1] or 0)
        theme_stats[theme]['latest_sentiment'] = float(row[2] or 0)
        theme_stats[theme]['latest_composite'] = float(row[3] or 0)

# 按综合分排序，关注金属类
sorted_themes = sorted(theme_stats.items(), key=lambda x: -x[1]['total_top5_count'])
print("\n各主题 TOP5 上榜次数（前20）:")
for theme, stats in sorted_themes[:20]:
    print(f"  {theme}: 总{stats['total_top5_count']}次 (早期{stats['early_top5_count']} 近期{stats['recent_top5_count']}) | 最新趋势{stats['latest_trend']:.0f}/情绪{stats['latest_sentiment']:.0f}")

# 小金属的质量分计算
print("\n=== 小金属 各维度评分 ===")
xj = theme_stats.get('小金属', {})
if not xj:
    print('未进入过TOP5')
else:
    presence_score = min(100, xj['total_top5_count'] * 8.3)
    print(f"  存在感(presence): {xj['total_top5_count']} * 8.3 = {presence_score:.1f}")
    
    if xj['total_top5_count'] >= 3:
        if xj['recent_top5_count'] == 0:
            consistency_score = 10
        else:
            recent_ratio = xj['recent_top5_count'] / xj['total_top5_count']
            consistency_score = min(100, recent_ratio * 200 + xj['recent_top5_count'] * 5)
    else:
        consistency_score = 0
    print(f"  持续性(consistency): {consistency_score:.1f} (近期{stats['recent_top5_count']}/总{xj['total_top5_count']})")
    
    latest_trend = xj['latest_trend']
    if latest_trend >= 75:
        trend_vitality = 90
    elif latest_trend >= 65:
        trend_vitality = 80
    elif latest_trend >= 55:
        trend_vitality = 65
    elif latest_trend >= 40:
        trend_vitality = 45
    else:
        trend_vitality = 25
    print(f"  趋势活力(trend_vitality): {trend_vitality:.1f} (最新趋势分{latest_trend:.0f})")
    
    # 质量分计算（旧版含情绪惩罚）
    quality_score_old = presence_score * 0.35 + consistency_score * 0.15 + trend_vitality * 0.25
    print(f"  基础质量分(不含风险+脉冲): {quality_score_old:.1f}")

conn.close()
