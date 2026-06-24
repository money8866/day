import json
with open(r'd:\mystock\solo\multi_factor_picker\output\wave2_theme_filtered_20260623.json','r',encoding='utf-8') as f:
    data = json.load(f)
r = [x for x in data if x['ts_code']=='688270.SH'][0]
print(f"688270 {r.get('name','')} score={r['score']} pattern={r['pattern']}")
print(f"  theme={r.get('best_theme','')} composite={r.get('best_theme_composite',0)}")
print(f"  wave1_gain={r.get('wave1_gain',0)} pullback={r.get('pullback_pct',0)} rsi={r.get('rsi',0)}")
