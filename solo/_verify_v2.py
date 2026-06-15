import json

data = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

print("="*72)
print(f"  V11 爆发潜力分 V2（相对排名版） 验证")
print(f"  交易日: {data.get('trade_date')}  主线: {data.get('primary_mainline')}")
print("="*72)

bt = data.get('breakout_themes_top5', [])

# 先展示全排名
print(f"\n📊 全主题爆发分排名（共{len(bt)}个）:\n")
print(f"  {'排名':<4} {'主题':<12} {'爆发分':>8} {'等级':<10} "
      f"{'站稳MA5':>8} {'高趋势':>8} {'5日涨幅':>8} {'MA10斜率':>9} "
      f"{'均趋势':>8} {'大票占比':>8}")
print(f"  {'-'*4} {'-'*12} {'-'*8} {'-'*10} "
      f"{'-'*8} {'-'*8} {'-'*8} {'-'*9} "
      f"{'-'*8} {'-'*8}")

for i, t in enumerate(bt):
    print(f"  {i+1:<4} {t['top_category']:<12} {t['breakout_score']:>8.1f} {t['level']:<10} "
          f"{t['above_ma5_ratio']:>7.0f}% {t['high_trend_ratio']:>7.0f}% "
          f"{t['avg_change5_pct']:>+7.1f}% {t['avg_ma10_slope_pct']:>+8.2f}% "
          f"{t['avg_trend_score']:>7.1f} {t.get('large_cap_ratio',t.get('positive_ma10_ratio',0)):>7.0f}%")

# 分项明细
print(f"\n{'='*72}")
print(f"  Top5 爆发分分项明细（百分位排名 × 权重）:\n")
for i, t in enumerate(bt[:5]):
    comp = t['score_components']
    w = {
        "above_ratio": 0.20,
        "high_trend_ratio": 0.18,
        "avg_chg5": 0.18,
        "avg_ma10": 0.14,
        "avg_trend": 0.10,
        "up_ratio": 0.08,
        "reversal_diff": 0.07,
        "large_cap_ratio": 0.05,
    }
    names = {
        "above_ratio": "站稳MA5",
        "high_trend_ratio": "高趋势",
        "avg_chg5": "5日涨幅",
        "avg_ma10": "MA10斜率",
        "avg_trend": "均趋势",
        "up_ratio": "上涨比例",
        "reversal_diff": "资金回流",
        "large_cap_ratio": "大票集中",
    }
    print(f"  [{i+1}] 【{t['top_category']}】总分={t['breakout_score']:.1f}")
    for k, v in comp.items():
        name = names.get(k, k)
        weight = w.get(k, 0)
        pct = v / weight if weight > 0 else 0  # 反推百分位排名
        bar_len = int(v / t['breakout_score'] * 20) if t['breakout_score'] > 0 else 0
        bar = '█' * bar_len
        print(f"      {name:<8} 百分位排名={pct:>5.1f}% × {weight:.0%}权重 = {v:>5.1f}分 {bar}")
    print()

# Top个股
print(f"{'='*72}")
print(f"  Top5 主题对应的最强爆发个股:\n")
for i, t in enumerate(bt[:5]):
    stocks = t.get('top_breakout_stocks', [])
    print(f"  [{i+1}] 【{t['top_category']}】爆发分={t['breakout_score']:.1f}  Top个股:")
    for j, s in enumerate(stocks[:4]):
        ma5 = "✅" if s['close_above_ma5'] else "❌"
        print(f"      {j+1}. {s['name']}({s['ts_code']}) trend={s['trend_score']:>3} "
              f"5日={s['change_5d_pct']:>+6.1f}% MA10={s['ma10_slope_pct']:>+6.2f}% "
              f"成交={s['amount_yi']:>5.1f}亿 {ma5}")
    print()

print(f"{'='*72}")
print(f"  ✅ V2算法特点:")
print(f"     - 所有主题按相对排名分配分项得分（排名越高得分越高）")
print(f"     - 满分100分，真正区分出 高/中/低/不推荐 四个等级")
print(f"     - 不再是所有主题都满分或接近满分")
print(f"     - 权重加权求和，保留核心维度的区分力")
print(f"{'='*72}")
