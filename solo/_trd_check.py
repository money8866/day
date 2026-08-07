# -*- coding: utf-8 -*-
import json, pandas as pd
out = []
df = pd.read_csv(r'd:\mystock\solo\report_daily\double_score_full.csv', encoding='utf-8-sig')
df['code_s'] = df['code'].astype(str)
picks = ['600989', '1309', '688525', '301308', '688578', '600961', '2648', '300475',
         '792', '2379', '301031', '807', '688308', '2448', '2039', '2611', '2440',
         '600150', '600346', '601899', '2432']
cols = ['code_s', 'name', 'theme', '涨停次数', '连板能力', '资金流入(亿)', '60日收益%', '日均成交额(亿)', '筹码面', '波段属性', '市值(亿)']
sub = df[df['code_s'].isin(picks)][cols]
out.append('=== 关键股票交易面 ===')
out.append(sub.to_string(index=False))

# via 龙头标识（通过主题映射 themes 拿到每只股票 via）
with open(r'd:\mystock\cache_daily\theme_stock_map_v2_20260806.json', encoding='utf-8') as f:
    m = json.load(f)
themes = m.get('themes', {})
code_via = {}
for tn, sl in themes.items():
    for s in sl:
        c = s['code'].split('.')[0].lstrip('0')
        if c not in code_via:
            code_via[c] = s.get('via', '')
out.append('\n=== 关键股票 via 龙头标识 ===')
for p in picks:
    out.append(f"{p}: {code_via.get(p, '无')}")
# 每个主题 leader 数量（唯一性）
leader_cnt = {}
for tn, sl in themes.items():
    leader_cnt[tn] = sum(1 for s in sl if s.get('via') in ('leader_company', 'core_company'))
out.append('\n=== 各主题 leader+core 数量（唯一性参考）===')
out.append('|'.join(f'{k}:{v}' for k, v in sorted(leader_cnt.items(), key=lambda x: x[1])))
with open(r'd:\mystock\solo\_trd_check.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('ok')
