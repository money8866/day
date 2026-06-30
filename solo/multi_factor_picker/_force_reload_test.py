"""强制重新验证（清除缓存）"""
import sys
import importlib

# 删除缓存
for mod_name in list(sys.modules.keys()):
    if 'trend_picker' in mod_name:
        del sys.modules[mod_name]

sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher
from trend_picker_v2_draft import detect_wave2_pattern, score_technical_v2

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260301', end_date='20260611')
basic = fetcher.pro.daily_basic(ts_code='600498.SH', start_date='20260301', end_date='20260611')
daily_merged = daily.merge(basic[['trade_date', 'turnover_rate']], on='trade_date', how='left')

# 测试二波检测
is_wave2, detail = detect_wave2_pattern(daily_merged, lookback_days=90)

print(f'二波确认: {"✓" if is_wave2 else "✗"}')
print(f'详情: {detail}')

# 测试技术面评分
tech_score, tech_detail = score_technical_v2(daily_merged, is_wave2)
print(f'\n技术面得分: {tech_score}')
print(f'F6: {tech_detail.get("F6", {})}')
print(f'F8: {tech_detail.get("F8", {})}')
print(f'WAVE2: {tech_detail.get("WAVE2", {})}')
