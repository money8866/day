"""
华天科技 量能爆发信号 历史检测
检测20260625~20260708每天的量能爆发信号
"""
import sys
sys.path.insert(0, r'd:\mystock\solo')
import _backtest_vol_surge as bvs

df = bvs.load_stock_df('002185.SZ')
if df is None:
    print('no cache data for 002185.SZ')
    sys.exit(1)

print(f'华天科技 缓存K线条数: {len(df)}')
print(f'日期范围: {df.trade_date.iloc[0]} ~ {df.trade_date.iloc[-1]}')
print('=' * 90)

test_dates = [
    '20260622','20260623','20260624','20260625','20260626',
    '20260629','20260630',
    '20260701','20260702','20260703',
    '20260706','20260707','20260708',
]

for td in test_dates:
    r = bvs.detect_vol_surge_swing(df, td)
    if r:
        print(f'{td}: 评分={r["score"]:>5} MACD={r["macd_status"]:<20} 回撤={r["retrace_type"]:<8} 今日量比={r["today_vol_ratio"]:>5} 距MA20={r["pos_ma20"]:>+6}%')
    else:
        print(f'{td}: 未命中')

print('=' * 90)
print('说明：量能爆发策略在每天收盘后检测，用截至当日的K线数据')
print('20260707大涨9.98%当天收盘后即可检测到信号')
