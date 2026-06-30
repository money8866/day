"""验证烽火通信在合格池中的评分"""
import sys
import importlib

for mod_name in list(sys.modules.keys()):
    if 'trend_picker' in mod_name:
        del sys.modules[mod_name]

sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher
from trend_picker_v2_draft import detect_wave2_pattern, score_technical_v2

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 烽火通信
ts_code = '600498.SH'

print(f'检查股票: {ts_code}\n')

# 获取数据
daily = fetcher.pro.daily(ts_code=ts_code, start_date='20260301', end_date='20260611')
basic = fetcher.pro.daily_basic(ts_code=ts_code, start_date='20260301', end_date='20260611')
daily_merged = daily.merge(basic[['trade_date', 'turnover_rate', 'circ_mv']], on='trade_date', how='left')

print(f'数据条数: {len(daily_merged)}')
print(f'今日涨幅: {float(daily_merged.iloc[0]["pct_chg"]):.1f}%')
print(f'今日换手率: {float(daily_merged.iloc[0].get("turnover_rate", 0) or 0):.2f}%')

# 二波检测
is_wave2, wave_detail = detect_wave2_pattern(daily_merged, lookback_days=90)

print(f'\n【二波检测】')
print(f'确认: {"✓成功" if is_wave2 else "✗失败"}')
print(f'详情: {wave_detail}')

# 技术面评分
tech_score, tech_detail = score_technical_v2(daily_merged, is_wave2)

print(f'\n【技术面评分】')
print(f'总分: {tech_score:.1f}')
print(f'F6换手率因子: {tech_detail.get("F6", {})}')
print(f'F8成交量因子: {tech_detail.get("F8", {})}')
print(f'WAVE2二波加分: {tech_detail.get("WAVE2", {})}')

# 计算最终标准化得分
if tech_score > 0:
    normalized_score = min(100, (tech_score / 22) * 100)
    print(f'\n标准化得分: {normalized_score:.1f}/100')
    print(f'趋势强度: {"强趋势" if normalized_score >= 60 else "中等" if normalized_score >= 40 else "弱趋势"}')
