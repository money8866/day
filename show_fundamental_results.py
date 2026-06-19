import json

with open(r'D:\mystock\solo\report_daily\fundamental_screen_20260618.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"T1 优质成长: {data['T1_count']} 只")
print(f"T2 稳健成长: {data['T2_count']} 只")
print(f"T3 业绩回暖: {data['T3_count']} 只")
print()

print("=== T1 优质成长 ===")
for r in data['T1_data'][:20]:
    pe_str = f"PE={r['pe']:.0f}" if r.get('pe') else "PE=N/A"
    roe_str = f"ROE={r['roe_waa']:.1f}%" if r.get('roe_waa') else "ROE=N/A"
    gm_str = f"毛利={r['gross_margin']:.1f}%" if r.get('gross_margin') else ""
    print(f"  {r['name']}({r['ts_code']}): 净利+{r['np_yoy']:.0f}% | 营收+{r['rev_yoy']:.0f}% | {roe_str} | {pe_str} | {gm_str} | {r['theme']} {r.get('theme_prosperity','')} | {r['stage']}")

print()
print("=== T2 稳健成长 ===")
for r in data['T2_data'][:20]:
    pe_str = f"PE={r['pe']:.0f}" if r.get('pe') else "PE=N/A"
    roe_str = f"ROE={r['roe_waa']:.1f}%" if r.get('roe_waa') else "ROE=N/A"
    print(f"  {r['name']}({r['ts_code']}): 净利+{r['np_yoy']:.0f}% | 营收+{r['rev_yoy']:.0f}% | {roe_str} | {pe_str} | {r['theme']} {r.get('theme_prosperity','')}")

print()
print("=== 行业景气度 TOP10 ===")
for t in data['theme_ranking'][:10]:
    rev_str = f"营收中位+{t['rev_yoy_median']:.0f}%" if t.get('rev_yoy_median') else ""
    print(f"  {t['prosperity']} {t['theme']}({t['count']}只): 净利中位+{t['np_yoy_median']:.0f}% | {rev_str}")
