import json
with open(r'D:\mystock\solo\report_daily\h1_predict_v1_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)
for code, p in d['predictions'].items():
    print(f"\n{'='*60}")
    print(f"{p['name']}({code}) | {p['theme']}")
    print(f"Q1已知: 营收{p['q1_rev_yi']}亿 净利{p['q1_ni_yi']}亿 | 同比: 营收+{p['q1_rev_yoy']}% 净利+{p['q1_ni_yoy']}%")
    print(f"因子:")
    for k, v in p['factors'].items():
        print(f"  {k}: {v}")
    hp = p.get('h1_predict', {})
    if hp:
        print(f"2025H1基准: 营收{hp.get('h1_2025_rev','N/A')}亿 净利{hp.get('h1_2025_ni','N/A')}亿")
        h1r = hp.get('h1_rev', {})
        h1n = hp.get('h1_ni', {})
        h1ry = hp.get('h1_rev_yoy', {})
        h1ny = hp.get('h1_ni_yoy', {})
        print(f"H1营收预测: {h1r.get('low','N/A')}~{h1r.get('high','N/A')}亿(中值{h1r.get('mid','N/A')}) | 同比: {h1ry.get('low','N/A')}~{h1ry.get('high','N/A')}%(中值{h1ry.get('mid','N/A')}%)")
        print(f"H1净利预测: {h1n.get('low','N/A')}~{h1n.get('high','N/A')}亿(中值{h1n.get('mid','N/A')}) | 同比: {h1ny.get('low','N/A')}~{h1ny.get('high','N/A')}%(中值{h1ny.get('mid','N/A')}%)")
    print(f"置信度: {p['confidence']} ({p.get('confidence_pct',0)*100:.0f}%)")
