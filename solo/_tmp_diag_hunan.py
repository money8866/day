# -*- coding: utf-8 -*-
import sys, os
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
import theme_score_v2 as tv
from theme_trend_sentiment_score import get_daily_kline, get_daily_basic

TRADE_DATE = tv.TRADE_DATE
START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=tv.N_DAYS + 30)).strftime("%Y%m%d")

theme_stock_map, stocks = tv.load_v2_mapping(TRADE_DATE)
matched = theme_stock_map.get('小金属', {})
print(f"小金属成份股 {len(matched)} 只")

daily_basic = get_daily_basic(TRADE_DATE)
mcap_dict = {r['ts_code']: r for _, r in daily_basic.iterrows()} if daily_basic is not None else {}

kline_df = get_daily_kline(list(matched.keys()), START_DATE, TRADE_DATE)
kline_groups = {}
if kline_df is not None and not kline_df.empty:
    for code, sub in kline_df.groupby('ts_code'):
        kline_groups[code] = sub

rows = []
for code, meta in matched.items():
    kdf = kline_groups.get(code)
    if kdf is None or len(kdf) < 6:
        continue
    feat = tv.per_stock_features_v2(kdf)
    if feat is None:
        continue
    feat['ts_code'] = code
    feat['name'] = meta.get('name', code)
    feat['total_mv'] = mcap_dict.get(code, {}).get('total_mv', 0) or 0
    rows.append(feat)
print(f"有效成份股 {len(rows)} 只\n")

subtheme_map = tv.load_subtheme_map()
pen = tv.analyze_mainline_penetration('小金属', rows, theme_stock_map, subtheme_map, {})

print(f"最佳子主题: {pen['best_subtheme']} | 涨停{pen['zt_count']}家 最高{pen['lb_max']}连板 | 理由: {pen['reason']}")
ld, eg = pen.get('leader'), pen.get('engine')
print(f"当选龙头: {ld['ts_code']} {ld['name']} 市值{ld['total_mv']/1e4:.0f}亿 连板{ld['lb_height']} 涨停{ld['zt_flag']} 涨幅{ld['pct_chg']}")
print(f"当选中军: {eg['ts_code']} {eg['name']} 市值{eg['total_mv']/1e4:.0f}亿 成交{eg.get('amount_latest',0)}亿")

# 复现 Step3 候选与排序
def leader_key(x):
    return (x.get('lb_height', 0), x.get('zt_flag', 0), abs(x.get('pct_chg', 0) or 0), -x.get('total_mv', 0))

best_stocks = [x for x in rows]  # 小金属 fallback_only 全部归入
print("\n=== 全部小金属成份股按龙头排序键 (lb, zt, |pct|, -mv) ===")
for x in sorted(best_stocks, key=leader_key, reverse=True)[:15]:
    print(f"  {x['ts_code']} {x['name']:<8} 市值{x['total_mv']/1e4:>6.0f}亿 连板{x['lb_height']} "
          f"涨停{x['zt_flag']} 涨幅{x['pct_chg']:>6.2f}% 成交{x.get('amount_latest',0):>6.1f}亿")

cands_50_300 = [x for x in best_stocks if 5e5 <= x.get('total_mv', 0) <= 3e6]
print(f"\n=== 市值50~300亿候选 {len(cands_50_300)} 只（当前逻辑仅从这些中选龙头）===")
for x in sorted(cands_50_300, key=leader_key, reverse=True)[:15]:
    print(f"  {x['ts_code']} {x['name']:<8} 市值{x['total_mv']/1e4:>6.0f}亿 连板{x['lb_height']} "
          f"涨停{x['zt_flag']} 涨幅{x['pct_chg']:>6.2f}% 成交{x.get('amount_latest',0):>6.1f}亿")
