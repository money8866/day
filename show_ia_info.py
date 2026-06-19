import json
with open(r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json','r',encoding='utf-8') as f:
    d = json.load(f)
for r in d['IA_data']:
    name = r['name']
    code = r['ts_code']
    theme = r['theme']
    np = r.get('np_yoy',0)
    rev = r.get('rev_yoy',0)
    cap = r.get('market_cap_yi',0)
    roe = r.get('roe_waa','N/A')
    pe = r.get('pe','N/A')
    pros = r.get('theme_prosperity','')
    latest = r.get('latest_period','')
    rev_yi = r.get('latest_revenue_yi',0)
    ni_yi = r.get('latest_nincome_yi',0)
    print(f"{name}|{code}|{theme}|Q1净利+{np:.0f}%|Q1营收+{rev:.0f}%|营收{rev_yi}亿|净利{ni_yi}亿|市值{cap:.0f}亿|ROE={roe}|PE={pe}|景气{pros}|报告期{latest}")
