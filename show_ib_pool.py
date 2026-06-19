import json

with open(r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

ib = d.get('IB_data', [])
print(f"IB观察池共 {len(ib)} 只")
print()
for i, r in enumerate(ib):
    np_yoy = r.get('np_yoy', 0)
    rev_yoy = r.get('rev_yoy', 0)
    cap = r.get('market_cap_yi', 0)
    roe = r.get('roe_waa', 'N/A')
    pe = r.get('pe', 'N/A')
    pros = r.get('theme_prosperity', '')
    theme = r.get('theme', '')
    name = r['name']
    code = r['ts_code']
    rev = r.get('latest_revenue_yi', 0)
    ni = r.get('latest_nincome_yi', 0)
    period = r.get('latest_period', '')
    qoq_rev = r.get('qoq_rev', 0)   # Q1环比Q4
    qoq_ni = r.get('qoq_ni', 0)
    print(f"{i+1}. {name}|{code}|{theme[:6]}|Q1营收+{rev_yoy:.0f}%|Q1净利+{np_yoy:.0f}%|Q1环比{rev}亿|ROE={roe}|市值{cap:.0f}亿|PE={pe}")
