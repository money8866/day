"""对比分析：涨停股vs接近涨停股"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 雅克科技6月10日（真实涨停）
yk_daily = fetcher.pro.daily(ts_code='002409.SZ', start_date='20260605', end_date='20260610')
yk_basic = fetcher.pro.daily_basic(ts_code='002409.SZ', trade_date='20260610')

print('【雅克科技 6月10日】真实涨停(+10%)')
print(f'收盘价: {float(yk_daily.iloc[-1]["close"]):.2f}')
print(f'涨跌幅: {float(yk_daily.iloc[-1]["pct_chg"]):.2f}%')
print(f'换手率: {float(yk_basic.iloc[0]["turnover_rate"]):.1f}%')
print(f'判断: 涨停启动日，换手率应得满分')

print('\n' + '='*50 + '\n')

# 烽火通信6月11日（接近涨停）
fh_daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260605', end_date='20260611')
fh_basic = fetcher.pro.daily_basic(ts_code='600498.SH', trade_date='20260611')

print('【烽火通信 6月11日】接近涨停(+9.5%)')
print(f'收盘价: {float(fh_daily.iloc[-1]["close"]):.2f}')
print(f'涨跌幅: {float(fh_daily.iloc[-1]["pct_chg"]):.2f}%')
print(f'换手率: {float(fh_basic.iloc[0]["turnover_rate"]):.1f}%')
print(f'判断: 大涨启动日，换手率不应惩罚')

print('\n' + '='*50)
print('\n【修复建议】')
print('阈值：>=9.4%（覆盖涨停和接近涨停的启动日）')
print('逻辑：启动日换手率>10%不惩罚，给予1.5-2分')
