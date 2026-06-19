import json

with open('D:/mystock/solo/report_daily/mainboard_v4_scan_20260618.json','r',encoding='utf-8') as f:
    data=json.load(f)

stocks=data['data']

# 字段清单
print('=== 字段清单 ===')
for k,v in stocks[0].items():
    print(f'  {k}: {repr(v)[:80]}')

# TOP10详情
print()
print('=== TOP10 ===')
for s in stocks[:10]:
    fields = [f"{s['name']}({s['ts_code']})"]
    fields.append(f"C={s.get('score_c_value_health',0):.1f}")
    fields.append(f"MA20={s.get('bias_ma20',0):+.1f}%")
    fields.append(f"回撤={s.get('drawdown_from_high_120',0):.1f}%")
    fields.append(f"120日={s.get('ret_120',0):+.1f}%")
    fields.append(f"市值={s.get('market_cap_yi',0):.0f}亿")
    fields.append(f"阶段={s.get('stage','')}")
    print(' | '.join(fields))
