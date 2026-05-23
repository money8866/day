# -*- coding: utf-8 -*-
import tushare as ts
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

code = '688612.SH'
print(f'=== WEIMAISI {code} ===')

# 1. Basic info
df_basic = pro.stock_basic(ts_code=code, fields='ts_code,name,industry,market,list_date')
if len(df_basic) > 0:
    print(f'NAME:{df_basic.iloc[0]["name"]}')
    print(f'INDUSTRY:{df_basic.iloc[0]["industry"]}')
    print(f'MARKET:{df_basic.iloc[0]["market"]}')
    print(f'LIST_DATE:{df_basic.iloc[0]["list_date"]}')

# 2. Valuation today
df_qt = pro.daily_basic(ts_code=code, trade_date='20260522')
if len(df_qt) > 0:
    r = df_qt.iloc[0]
    print(f'\nQUOTE_DATE:20260522')
    print(f'CLOSE:{r["close"]:.2f}')
    print(f'PE:{r["pe_ttm"]:.2f}')
    print(f'PB:{r["pb"]:.2f}')
    print(f'PS:{r["ps_ttm"]:.2f}')
    print(f'MKTCAP:{r["total_mv"]/10000:.2f}')
    print(f'CIRC_MV:{r["circ_mv"]/10000:.2f}')
    print(f'TURNOVER:{r["turnover_rate"]:.2f}')

# 3. Daily data last 60 days for technical
df = pro.daily(ts_code=code, start_date='20260401', end_date='20260522')
if len(df) > 0:
    df = df.sort_values('trade_date')
    closes = df['close'].values
    vols = df['vol'].values
    highs = df['high'].values
    lows = df['low'].values

    ma5 = closes[-5:].mean() if len(closes) >= 5 else closes.mean()
    ma10 = closes[-10:].mean() if len(closes) >= 10 else closes.mean()
    ma20 = closes[-20:].mean() if len(closes) >= 20 else closes.mean()
    ma60 = closes[-60:].mean() if len(closes) >= 60 else closes.mean()
    price = closes[-1]
    vol_now = vols[-1]
    vol_avg5 = vols[-5:].mean()
    pct = df.iloc[-1]['pct_chg']

    print(f'\nTECHNICAL:')
    print(f'CLOSE:{price:.2f} CHG:{pct:.2f}%')
    print(f'MA5:{ma5:.2f} MA10:{ma10:.2f} MA20:{ma20:.2f} MA60:{ma60:.2f}')
    print(f'VOL:{vol_now:.0f} VOL_AVG5:{vol_avg5:.0f} VOL_RATIO:{vol_now/vol_avg5:.2f}')

    # 52w
    df52 = pro.daily(ts_code=code, start_date='20250501', end_date='20260522')
    if len(df52) > 0:
        h52 = df52['high'].max()
        l52 = df52['low'].min()
        print(f'52W_HIGH:{h52:.2f} 52W_LOW:{l52:.2f}')
        print(f'52W_POSITION:{(price-l52)/(h52-l52)*100:.1f}%')

# 4. Financials - quarterly
df_q = pro.fina_indicator(ts_code=code, start_date='20240101')
if len(df_q) > 0:
    print(f'\nQUARTERLY_FINANCIALS:')
    for _, row in df_q.head(4).iterrows():
        print(f'Q:{row["end_date"]} EPS:{row["eps"]:.4f} ROE:{row["roe"]:.2f} GROSS:{row["grossprofit_margin"]:.2f} NET:{row["netprofit_margin"]:.2f} DEBT:{row["debt_to_assets"]:.2f} NP_YOY:{row["netprofit_yoy"]:.2f} OR_YOY:{row["or_yoy"]:.2f}')

# 5. Concept data from cache
import sqlite3
conn = sqlite3.connect('cache_db/tdx_concept.db')
cur = conn.cursor()
cur.execute("""
    SELECT cc.concept_code, i.name, d.pct_change, d.rise
    FROM dc_concept_cons cc
    JOIN dc_concept c ON cc.concept_code=c.theme_code AND cc.trade_date=c.trade_date
    JOIN tdx_daily d ON cc.concept_code=d.ts_code AND cc.trade_date=d.trade_date
    JOIN tdx_index i ON cc.concept_code=i.ts_code AND cc.trade_date=i.trade_date
    WHERE cc.ts_code=? AND cc.trade_date='20260521'
    ORDER BY d.pct_change DESC
    LIMIT 10
""", (code,))
rows = cur.fetchall()
print(f'\nCONCEPTS ({len(rows)}):')
for r in rows:
    print(f'  CON|{r[0]}|{r[1]}|{r[2]:.2f}%')

# Also check kpl_concept
import pickle
with open('cache_kpl/kpl_concept_20260521.pkl', 'rb') as f:
    df_kpl = pickle.load(f)
stk_con = df_kpl[df_kpl['ts_code'] == code]
print(f'\nKPL_CONCEPTS: {len(stk_con)}')
for _, r in stk_con.iterrows():
    print(f'  KPL|{r["con_name"]}|HOT:{r["hot_num"]}')

conn.close()