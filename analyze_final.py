# -*- coding: utf-8 -*-
import tushare as ts
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

stocks = {
    '300489.SZ': {'name': '光智科技', 'score': 2066.97, 'line': '电子-光学材料', 'buy': '132-136', 'stop': 127.50, 'target': 215},
    '688008.SH': {'name': '澜起科技', 'score': 1995.00, 'line': '电子-AI算力', 'buy': '265-272', 'stop': 255, 'target': 370},
    '301489.SZ': {'name': '维峰电子', 'score': 1798.11, 'line': '电子-连接器', 'buy': '?          ', 'stop': '?', 'target': '?'},
    '688433.SH': {'name': '华曙高科', 'score': 1789.80, 'line': '机械设备-3D打印', 'buy': '?', 'stop': '?', 'target': '?'},
    '688059.SH': {'name': '华锐精密', 'score': 1720.87, 'line': '机械设备-刀具', 'buy': '152-156', 'stop': 146, 'target': 250},
    '300586.SZ': {'name': '美联新材', 'score': 1615.97, 'line': '基础化工-新材料', 'buy': '?', 'stop': '?', 'target': '?'},
    '301027.SZ': {'name': '中熔电气', 'score': 1539.97, 'line': '电力设备-熔断器', 'buy': '?', 'stop': '?', 'target': '?'},
    '301199.SZ': {'name': '翰宇药业', 'score': 1045.95, 'line': '医药-多肽', 'buy': '?', 'stop': '?', 'target': '?'},
}

print('=== 0522复盘报告个股基本面分析 ===\n')

for code, info in stocks.items():
    print(f'--- {info["name"]} ({code}) ---')
    print(f'量化评分: {info["score"]}')
    print(f'主线: {info["line"]}')

    # Basic info
    df_basic = pro.stock_basic(ts_code=code, fields='ts_code,name,industry,market,list_date')
    if len(df_basic) > 0:
        print(f'全称: {df_basic.iloc[0]["name"]} | 行业: {df_basic.iloc[0]["industry"]} | 上市: {df_basic.iloc[0]["list_date"]}')

    # Today's quote
    df_q = pro.daily_basic(ts_code=code, trade_date='20260522')
    if len(df_q) > 0:
        r = df_q.iloc[0]
        pe = r.get('pe_ttm') or 0
        pb = r.get('pb') or 0
        ps = r.get('ps_ttm') or 0
        mkt = r.get('total_mv') or 0
        circ = r.get('circ_mv') or 0
        turnover = r.get('turnover_rate') or 0
        print(f'现价: {r["close"]:.2f} | PE: {pe:.1f} | PB: {pb:.2f} | PS: {ps:.2f}')
        print(f'市值: {mkt/10000:.1f}亿 | 流通: {circ/10000:.1f}亿 | 换手: {turnover:.1f}%')
    else:
        print('今日行情: 无数据')

    # Recent daily
    df_d = pro.daily(ts_code=code, start_date='20260401', end_date='20260522')
    if len(df_d) > 0:
        df_d = df_d.sort_values('trade_date')
        closes = df_d['close'].values
        vols = df_d['vol'].values
        price = closes[-1]
        chg = df_d.iloc[-1]['pct_chg']
        ma5 = closes[-5:].mean() if len(closes) >= 5 else price
        ma10 = closes[-10:].mean() if len(closes) >= 10 else price
        ma20 = closes[-20:].mean() if len(closes) >= 20 else price
        ma60 = closes[-60:].mean() if len(closes) >= 60 else price
        h52 = df_d['high'].max()
        l52 = df_d['low'].min()
        pos52 = (price - l52) / (h52 - l52) * 100 if (h52 - l52) > 0 else 0
        vol_now = vols[-1]
        vol_avg5 = vols[-5:].mean()
        vr = vol_now / vol_avg5 if vol_avg5 > 0 else 0
        print(f'今日: {chg:+.2f}% | MA5:{ma5:.1f} MA10:{ma10:.1f} MA20:{ma20:.1f} MA60:{ma60:.1f}')
        print(f'52W: 高={h52:.2f} 低={l52:.2f} 分位={pos52:.0f}% | 量比={vr:.2f}')

    # Financials
    df_f = pro.fina_indicator(ts_code=code, start_date='20250101')
    if len(df_f) > 0:
        rows = df_f.head(2)
        for _, r in rows.iterrows():
            np_yoy = r.get('netprofit_yoy', 0) or 0
            or_yoy = r.get('or_yoy', 0) or 0
            gross = r.get('grossprofit_margin', 0) or 0
            net_m = r.get('netprofit_margin', 0) or 0
            roe = r.get('roe', 0) or 0
            eps = r.get('eps', 0) or 0
            debt = r.get('debt_to_assets', 0) or 0
            if isinstance(np_yoy, float) and np_yoy != np_yoy:  # NaN check
                np_yoy = 0
            if isinstance(or_yoy, float) and or_yoy != or_yoy:
                or_yoy = 0
            print(f'财务 {r["end_date"]}: EPS={eps:.3f} ROE={roe:.2f}% 毛利率={gross:.1f}% 净利率={net_m:.1f}% 负债={debt:.1f}% 净利YOY={np_yoy:.1f}% 营收YOY={or_yoy:.1f}%')

    print()