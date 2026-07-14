# -*- coding: utf-8 -*-
"""收盘分析 - Tushare获取今日K线"""
import os, sys, time
sys.path.insert(0, r'D:\mystock\solo')

try:
    import tushare as ts
    pro = ts.pro_api(os.getenv('TUSHARE_TOKEN', '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'))
    TS = True
except:
    TS = False
    print('Tushare不可用')

import realtime_monitor_tdx as m

def get_index_kline(code, days=10):
    """获取指数K线"""
    if not TS:
        return None
    # 判断交易所
    if code.startswith('000') and not code.startswith('000300'):
        ts_code = code + '.SH'
    elif code.startswith('399'):
        ts_code = code + '.SZ'
    else:
        ts_code = code + '.SH' if code.startswith('0') else code + '.SZ'
    
    end = '20260714'
    start = '20260601'
    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is not None and len(df) > 0:
            return df.head(days).to_dict('records')
    except Exception as e:
        print('Error:', e)
    return None

# 获取主要指数数据
indices = [
    ('上证指数', '000001'),
    ('深证成指', '399001'),
    ('创业板指', '399006'),
    ('科创50', '000688'),
    ('沪深300', '000300'),
    ('中证500', '000905'),
]

print('=== 主要指数 (最近10日收盘) ===')
index_data = {}
for name, code in indices:
    if not TS:
        break
    try:
        if code.startswith('399'):
            ts_code = code + '.SZ'
        else:
            ts_code = code + '.SH'
        df = pro.index_daily(ts_code=ts_code, start_date='20260620', end_date='20260714')
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date')
            index_data[name] = df
            last5 = df.tail(5)
            print('\n[%s]' % name)
            for _, row in last5.iterrows():
                date = str(row['trade_date'])
                close = row['close']
                pct = row.get('pct_chg', 0)
                high = row.get('high', close)
                low = row.get('low', close)
                open_p = row.get('open', close)
                sign = '+' if pct >= 0 else ''
                print('  %s  收=%.2f 开=%.2f 高=%.2f 低=%.2f %s%.2f%%' % (
                    date, close, open_p, high, low, sign, pct))
            
            if len(df) >= 2:
                vr = row['vol'] / df.iloc[-2]['vol'] if df.iloc[-2]['vol'] else 0
                print('  量比: %.1fx (今日/昨量)' % vr)
    except Exception as e:
        print('%s: %s' % (name, e))
    time.sleep(0.3)

print()
print('=== 今日涨跌停统计 ===')
if TS:
    try:
        df_limit = pro.daily(trade_date='20260714')
        if df_limit is not None and len(df_limit) > 0:
            zt = df_limit[df_limit['pct_chg'] >= 9.5]
            dt = df_limit[df_limit['pct_chg'] <= -9.5]
            zt_5 = df_limit[(df_limit['pct_chg'] >= 4.9) & (df_limit['pct_chg'] < 9.5)]
            dt_5 = df_limit[(df_limit['pct_chg'] <= -4.9) & (df_limit['pct_chg'] > -9.5)]
            print('涨停(>=9.5%%): %d只' % len(zt))
            print('接近涨停(4.9~9.4%%): %d只' % len(zt_5))
            print('跌停(<=9.5%%): %d只' % len(dt))
            print('接近跌停(-4.9~-9.4%%): %d只' % len(dt_5))
            print('总交易股票: %d只' % len(df_limit))
            
            # 上涨/下跌家数
            up = len(df_limit[df_limit['pct_chg'] > 0])
            down = len(df_limit[df_limit['pct_chg'] < 0])
            flat = len(df_limit[df_limit['pct_chg'] == 0])
            print('上涨: %d只 下跌: %d只 平盘: %d只' % (up, down, flat))
    except Exception as e:
        print('涨跌停统计失败:', e)

print()
print('=== 持仓分析 ===')
mon = m.RealtimeMonitor()
mon.fetch_all()
for code, pos in mon.positions.items():
    q = mon.quotes.get(code)
    if q:
        entry = pos.get('entry', 0)
        cur = q.get('price', 0)
        pct_total = (cur - entry) / entry * 100 if entry else 0
        pct_today = q.get('pct_chg', 0)
        high_today = q.get('high', 0)
        low_today = q.get('low', 0)
        name = q.get('name', code)
        amount = q.get('amount', 0)
        vol = q.get('vol', 0)
        print('%s (%s):' % (name, code))
        print('  收盘=%.3f 持仓盈亏=%.1f%% 今日涨跌=%.2f%%' % (cur, pct_total, pct_today))
        print('  今日区间: 低=%.3f 高=%.3f' % (low_today, high_today))
        print('  成交额=%.0f亿 成交量=%d万手' % (amount/1e8, vol/10000))

print()
print('=== 主题强弱与持仓对比 ===')
theme_data = mon.analyze_themes()
for theme, data in sorted(theme_data.items(), key=lambda x: x[1]['avg_pct'], reverse=True):
    stocks_str = ' '.join(['%s(%+.1f%%)' % (c[-6:], p) for c, p in sorted(data['stocks'], key=lambda x: x[1], reverse=True)])
    print('[%s] 均=%.1f%% 高=%.1f%% 低=%.1f%% %d/%d上涨' % (theme, data['avg_pct'], data['max_pct'], data['min_pct'], data['up_count'], data['total']))
    print('  %s' % stocks_str)

score, status, pos = mon.calc_market_score()
print()
print('市场评分: %.1f %s 建议仓位%d%%' % (score, status, pos))
