import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tushare_quant as tq
themes = tq._load_mainline_rotation_themes('20260904')
print('解析主题数:', len(themes))
from collections import Counter
print('kind分布:', Counter(v['kind'] for v in themes.values()))
for t, v in list(themes.items())[:5]:
    print(t, v['kind'], v['stage'], '综合', v.get('composite_score'), '迁移', v.get('migration_score'))
