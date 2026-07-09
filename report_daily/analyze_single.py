# -*- coding: utf-8 -*-
"""单只股票综合分析：湘潭电化 002125.SZ"""
import sys
import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

TS_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
TS_CODE = '002125.SZ'

def get_pro():
    import tushare as ts
    ts.set_token(TS_TOKEN)
    return ts.pro_api()

def get_daily(pro):
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
    df = pro.daily(ts_code=TS_CODE, start_date=start, end_date=end)
    if df is not None and len(df) > 0:
        df = df.sort_values('trade_date').reset_index(drop=True)
        df['pct_chg'] = df['pct_chg'].fillna(0).astype(float)
        df['close'] = df['close'].astype(float)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['vol'] = df['vol'].astype(float)
        return df
    return None

def get_fina(pro):
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=800)).strftime('%Y%m%d')
    df = pro.fina_indicator(ts_code=TS_CODE, start_date=start)
    if df is not None and len(df) > 0:
        df = df.sort_values('end_date', ascending=False).reset_index(drop=True)
        return df
    return None

def get_basic(pro):
    df = pro.stock_basic(ts_code=TS_CODE, fields='ts_code,name,area,industry,market,list_date')
    if df is not None and len(df) > 0:
        return df.iloc[0]
    return None

def get_em_announcements(code, market):
    results = []
    em_market = 1 if market == 'SH' else 0
    try:
        url = 'http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web&stock_list=%s,%s' % (code, em_market)
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://data.eastmoney.com/'}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('data'):
                for n in data['data'].get('list', [])[:8]:
                    results.append({
                        'title': n.get('title', ''),
                        'date': (n.get('notice_date') or '')[:10],
                        'type': n.get('art_type_str', '公告')
                    })
    except Exception as e:
        pass
    return results

def calc_rsi(prices, period=6):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def analyze(df, fina, basic, anns):
    close = df['close']
    vol = df['vol']
    pct = df['pct_chg']
    n = len(df)
    latest_date = df['trade_date'].iloc[-1]
    last_close = close.iloc[-1]
    prev_close = close.iloc[-2] if n > 1 else last_close

    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma120 = close.rolling(120).mean().iloc[-1] if n >= 120 else np.nan
    ma250 = close.rolling(250).mean().iloc[-1] if n >= 250 else np.nan

    rsi6 = calc_rsi(close, 6)
    rsi12 = calc_rsi(close, 12)

    mom20 = (last_close / close.iloc[-20] - 1) * 100 if n >= 20 else 0
    mom60 = (last_close / close.iloc[-60] - 1) * 100 if n >= 60 else 0
    mom250 = (last_close / close.iloc[-250] - 1) * 100 if n >= 250 else 0

    # 年内高低
    ytd = close.tail(250) if n >= 250 else close
    high_250 = ytd.max(); low_250 = ytd.min()
    pos_pct = (last_close - low_250) / (high_250 - low_250) * 100 if high_250 > low_250 else 50

    # 成交量比
    avg_vol_20 = vol.tail(20).mean()
    avg_vol_5 = vol.tail(5).mean()
    vol_ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1

    # 波动率
    vola20 = pct.tail(20).std()

    # 近期走势
    last10 = close.tail(10).values
    last10_chg = (last10[-1] / last10[0] - 1) * 100

    print('=' * 64)
    print('湘潭电化 (002125.SZ) 综合分析 | 数据日期 %s' % latest_date)
    print('=' * 64)
    print()
    print('【基本信息】')
    if basic is not None:
        print('  名称:%s 地区:%s 行业:%s 上市:%s' % (basic.get('name'), basic.get('area'), basic.get('industry'), basic.get('list_date')))
    print()
    print('【技术面】')
    print('  最新价: %.2f  涨跌(昨收): %.2f%%' % (last_close, (last_close/prev_close-1)*100))
    print('  MA5/10/20/60/120/250: %.2f / %.2f / %.2f / %.2f / %s / %s' % (
        ma5, ma10, ma20, ma60, ('%.2f' % ma120) if not pd.isna(ma120) else 'N/A', ('%.2f' % ma250) if not pd.isna(ma250) else 'N/A'))
    print('  多头排列(MA5>MA20>MA60): %s' % (ma5 > ma20 > ma60))
    print('  站上MA20: %s  站上MA60: %s' % (last_close > ma20, last_close > ma60))
    print('  RSI6/RSI12: %.1f / %.1f' % (rsi6, rsi12))
    print('  动量20/60/250日: %.1f%% / %.1f%% / %.1f%%' % (mom20, mom60, mom250))
    print('  量比(5日/20日均量): %.2f' % vol_ratio)
    print('  20日波动率(标准差): %.2f%%' % vola20)
    print('  近10日涨跌: %.1f%%' % last10_chg)
    print('  250日区间[%.2f, %.2f]，当前处 %.1f%% 分位' % (low_250, high_250, pos_pct))
    print()

    print('【基本面（最新财报）】')
    if fina is not None and len(fina) > 0:
        latest = fina.iloc[0]
        prev = fina.iloc[1] if len(fina) > 1 else None
        end_date = latest.get('end_date')
        print('  报告期: %s' % end_date)
        print('  营业总收入(万元): %s' % latest.get('revenue'))
        if latest.get('revenue') is not None and prev is not None and prev.get('revenue') is not None and prev.get('revenue') != 0:
            rev_yoy = (latest.get('revenue') / prev.get('revenue') - 1) * 100
            print('  营收同比: %.1f%%' % rev_yoy)
        print('  ROE: %s  毛利率: %s  净利率: %s' % (latest.get('roe'), latest.get('gross_profit_rate'), latest.get('netprofit_margin')))
        print('  资产负债率: %s' % latest.get('debt_to_assets'))
        print('  净利润(万元): %s' % latest.get('net_profit'))
        if latest.get('yoyoy') is not None:
            print('  净利润同比: %s%%' % latest.get('yoyoy'))
        print('  EPS: %s  BPS: %s' % (latest.get('eps'), latest.get('bps')))
    else:
        print('  无财报数据')
    print()

    print('【最新公告（近8条）】')
    if anns:
        for a in anns:
            print('  [%s] %s' % (a['date'], a['title']))
    else:
        print('  无')
    print()

    # 综合评分（技术面为主）
    print('【综合信号】')
    signals = []
    if ma5 > ma20 > ma60:
        signals.append('✅ 均线多头排列')
    else:
        signals.append('⚠️ 均线未多头排列')
    if last_close > ma20:
        signals.append('✅ 站上MA20')
    else:
        signals.append('⚠️ 跌破MA20')
    if 45 <= rsi6 <= 70:
        signals.append('✅ RSI处于强势区间')
    elif rsi6 > 80:
        signals.append('🔴 RSI超买')
    elif rsi6 < 30:
        signals.append('🟢 RSI超卖')
    if 1.2 <= vol_ratio <= 2.5:
        signals.append('✅ 温和放量')
    elif vol_ratio > 3:
        signals.append('⚠️ 异常放量')
    if mom20 > 0:
        signals.append('✅ 20日动量向上')
    else:
        signals.append('⚠️ 20日动量向下')
    for s in signals:
        print('  ' + s)

    return {
        'last_close': last_close, 'rsi6': rsi6, 'mom20': mom20, 'mom250': mom250,
        'vol_ratio': vol_ratio, 'pos_pct': pos_pct, 'ma5': ma5, 'ma20': ma20, 'ma60': ma60,
        'multiline': ma5 > ma20 > ma60
    }

def main():
    pro = get_pro()
    print('获取日线...')
    df = get_daily(pro)
    if df is None:
        print('日线获取失败')
        return
    print('获取财报...')
    fina = get_fina(pro)
    print('获取基本信息...')
    basic = get_basic(pro)
    print('获取公告...')
    anns = get_em_announcements('002125', 'SZ')
    time.sleep(0.2)
    analyze(df, fina, basic, anns)

if __name__ == '__main__':
    main()
