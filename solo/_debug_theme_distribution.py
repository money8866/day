import sqlite3
from collections import defaultdict

# 读取主题评分
conn = sqlite3.connect("D:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db")
c = conn.cursor()

# 先看所有主题表有哪些
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables:", tables)

# 读取最新日期的主题评分
c.execute(f"SELECT theme, composite_score, trend_score, sentiment_score FROM theme_scores ORDER BY trade_date DESC LIMIT 100")
theme_scores = []
for r in c.fetchall():
    theme_scores.append({
        "theme": r[0],
        "composite": r[1] if r[1] else 0,
        "trend": r[2] if r[2] else 0,
        "sentiment": r[3] if r[3] else 0,
    })

# 按 composite 排序
theme_scores.sort(key=lambda x: x["composite"], reverse=True)

print("\n【Top 20 主题评分】")
print(f"{'排名':<4}{'主题':<20}{'综合分':<10}{'趋势分':<10}{'情绪分':<10}")
print("-" * 60)
for i, t in enumerate(theme_scores[:20], 1):
    print(f"{i:<4}{t['theme']:<20}{t['composite']:<10.1f}{t['trend']:<10.1f}{t['sentiment']:<10.1f}")

print("\n【Bottom 20 主题评分】")
for i, t in enumerate(theme_scores[-20:], 1):
    print(f"{i:<4}{t['theme']:<20}{t['composite']:<10.1f}{t['trend']:<10.1f}{t['sentiment']:<10.1f}")

# 看看主题的数量
print(f"\n共有 {len(theme_scores)} 个主题")
# 去重
unique_themes = {}
for t in theme_scores:
    if t["theme"] not in unique_themes:
        unique_themes[t["theme"]] = t["composite"]
unique_scores = [{"theme": k, "composite": v} for k, v in unique_themes.items()]
unique_scores.sort(key=lambda x: x["composite"], reverse=True)

print(f"\n【唯一主题共 {len(unique_scores)} 个，各主题评分分布】")
print(f"{'排名':<4}{'主题':<20}{'综合分':<10}")
print("-" * 40)
for i, t in enumerate(unique_scores, 1):
    print(f"{i:<4}{t['theme']:<20}{t['composite']:<10.1f}")

conn.close()

# 现在看产业强度的计算公式：industry_strength = 0.4*theme_score + 0.3*industry_demand + 0.3*order_explosion
# 在程序中 industry_demand 和 order_explosion 是怎么获取的？
# 让我检查一下程序中它们的定义
print("\n=== 产业强度计算逻辑检查 ===")
print("industry_strength = 0.4*theme_score + 0.3*industry_demand_score + 0.3*order_explosion_score")
print("产业强度门槛是 55")
print("如果 industry_demand 和 order_explosion 都是 0，那么：")
print(f"  需要 theme_score >= {55/0.4:.1f}")
print("如果 industry_demand 和 order_explosion 默认值很低（比如40），那么：")
print(f"  需要 theme_score >= {(55 - 0.3*40 - 0.3*40)/0.4:.1f}")
