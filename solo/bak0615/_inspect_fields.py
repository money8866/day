import json
with open('cache_backbone_tushare/theme3_constituents_20260612.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

for theme in data['themes'][:3]:
    print('\n=== %s (%s) ===' % (theme.get('theme_name'), theme.get('top_category')))
    print('  主题字段: %s' % list(theme.keys()))
    if theme.get('stocks'):
        s = theme['stocks'][0]
        for k, v in s.items():
            print('    %s: %s' % (k, v))
    # 统计主题概要
    stocks = theme.get('stocks', [])
    if stocks:
        total_mv = sum((s.get('total_mv_wan') or 0) for s in stocks) / 10000
        avg_amt = sum((s.get('avg_amount_5d') or 0) for s in stocks) / 1e8
        limit_up = sum(1 for s in stocks if (s.get('limit_up_days') or 0) >= 1)
        roles = {}
        for s in stocks:
            r = s.get('role', '补涨')
            roles[r] = roles.get(r, 0) + 1
        print('\n  统计: 股票数=%d, 总市值=%.1f亿, 日均成交=%.1f亿, 涨停=%d, 角色=%s' % (len(stocks), total_mv, avg_amt, limit_up, roles))
