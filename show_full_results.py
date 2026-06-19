import json
with open(r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json','r',encoding='utf-8') as f:
    d = json.load(f)
print(f"IA核心池: {d['IA_count']}只")
print(f"IB观察池: {d['IB_count']}只")
print(f"IC跟踪池: {d['IC_count']}只")
print()
print("=== IA 机构核心池 ===")
for r in d['IA_data'][:20]:
    pe = f"PE={r['pe']:.0f}" if r.get('pe') else 'PE=N/A'
    peg = f" PEG={r['peg']:.1f}" if r.get('peg') else ''
    roe = f"ROE={r['roe_waa']:.1f}%" if r.get('roe_waa') else 'ROE=N/A'
    dv = f" 股息{r['dv_ratio']:.1f}%" if r.get('dv_ratio') else ''
    cg = ' 连续增长' if r.get('consecutive_growth') else ''
    print(f"  [{r['inst_personal_score']:.0f}分] {r['name']}({r['ts_code'][:6]}): 净利+{r['np_yoy']:.0f}% | 营收+{r['rev_yoy']:.0f}% | {roe} | {pe}{peg} | 市值{r['market_cap_yi']:.0f}亿 | 成交{r['avg_amount_20d_yi']:.1f}亿 | {r['theme']} {r['theme_prosperity']} {r['theme_capacity']}{dv}{cg}")
print()
print("=== 行业景气度TOP10 ===")
for t in d['theme_ranking'][:10]:
    rev = f" 营收+{t['rev_yoy_median']:.0f}%" if t.get('rev_yoy_median') else ''
    print(f"  {t['prosperity']} #{t['ranking']} {t['theme']}({t['count']}只): 净利+{t['np_yoy_median']:.0f}%{rev} | {t['capacity']} 均市值{t['avg_cap']:.0f}亿")
