# -*- coding: utf-8 -*-
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
with open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', encoding='utf-8') as f:
    data = json.load(f)
themes = data.get('themes', {})
print("主题总数:", len(themes))
for tname, slist in themes.items():
    if any(s.get('code') == '603039.SH' for s in slist if isinstance(s, dict)):
        hit = [s for s in slist if isinstance(s, dict) and s.get('code') == '603039.SH'][0]
        print(f"主题[{tname}] 包含泛微网络: via={hit.get('via')} score={hit.get('score')} industry={hit.get('industry')}")
print("---检查所有主题里 score 最高的匹配---")
for tname, slist in themes.items():
    if any(s.get('code') == '603039.SH' for s in slist if isinstance(s, dict)):
        hit = [s for s in slist if isinstance(s, dict) and s.get('code') == '603039.SH'][0]
        print(f"  {tname}: score={hit.get('score')}")
