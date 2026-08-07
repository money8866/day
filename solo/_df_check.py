# -*- coding: utf-8 -*-
import json, pandas as pd
out = []
df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_all.csv', encoding='utf-8-sig')
out.append('=== bull_stocks_all.csv 全部列 (%d) ===' % df.shape[1])
out.append('|'.join(str(c) for c in df.columns))
out.append('\n=== 交易面候选列 ===')
keys = [c for c in df.columns if any(k in str(c) for k in
        ['量比', '换手', '成交', '振幅', '涨跌', '乖离', '均线', '技术', '资金', '主力', '净流入',
         '连板', '涨停', '买点', '强度', '收益', '60日', '20日', '5日', '10日', '周转', '阳线', '放量'])]
out.append('|'.join(keys))
# 样例值
sample = [c for c in keys if c in df.columns]
if sample:
    out.append('\n=== 样例值（宝丰能源 600989）===')
    r = df[df['code'].astype(str).str.startswith('600989')]
    if len(r):
        out.append(r[sample].to_string(index=False))
# via 分布
with open(r'd:\mystock\cache_daily\theme_stock_map_v2_20260806.json', encoding='utf-8') as f:
    m = json.load(f)
themes = m.get('themes', {})
via_cnt = {}
leader_ex = {}
for tn, sl in themes.items():
    for s in sl:
        v = s.get('via', '')
        via_cnt[v] = via_cnt.get(v, 0) + 1
        if v in ('leader_company', 'core_company') and tn not in leader_ex:
            leader_ex[tn] = f"{s['code']} {s['name']} via:{v}"
out.append('\n=== themes via 分布 ===')
out.append('|'.join(f'{k}:{v}' for k, v in via_cnt.items()))
out.append('\n=== 各主题 leader/core 龙头 ===')
out.append('|'.join(f'{k}:{v}' for k, v in leader_ex.items()))
with open(r'd:\mystock\solo\_df_check.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('ok')
