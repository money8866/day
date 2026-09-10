import json, os, random
import numpy as np
import pandas as pd
import w7_second_wave_engine as w7

p = os.path.join('..', 'cache_daily', 'theme_stock_map_latest.json')
if not os.path.exists(p):
    p = r'D:\mystock\cache_daily\theme_stock_map_latest.json'
with open(p, 'r', encoding='utf-8') as f:
    themes = json.load(f).get('themes', {})
codes = []
for name, stocks in themes.items():
    for s in stocks:
        c = s.get('code')
        if c and c.endswith(('.SZ', '.SH')) and c not in codes:
            codes.append(c)

reader = w7.CacheReader()
end_db = reader.latest_date()
random.seed(11)
sample = random.sample(codes, min(200, len(codes)))
reader.load_all(end_db, codes=sample, min_date=w7.DATA_START)
reader.load_fina()
mkt = w7.MarketCtx(*reader.market_curve(end_db))
anchors = {}
for label, (code, adate) in w7.ANCHORS.items():
    try:
        anchors[label] = w7.anchor_features(reader.bars_sql(code, end_db), adate)
    except Exception:
        anchors[label] = None

def build_df(code):
    df0 = reader.bars(code, end_db)
    prev = df0.iloc[-1]
    row = dict(prev)
    row.update({'ts_code': code, 'trade_date': '20990101', 'open': prev.open,
                'high': prev.high, 'low': prev.low, 'close': prev.close,
                'pct_chg': 0.0, 'vol': prev.vol, 'amount': prev.amount})
    df = pd.concat([df0, pd.DataFrame([row])], ignore_index=True)
    df = w7._fill_ma_columns(df)
    for col in df.columns:
        if col not in ('ts_code', 'trade_date'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['close', 'high', 'low', 'vol']).reset_index(drop=True)

mism = 0
n_sig = 0
for code in sample:
    try:
        df = build_df(code)
        a0 = w7.find_event_anchor(df)
        s0 = w7.analyze(code, 'x', 'x', df, anchors, reader=reader, mkt=mkt,
                        sector_strength={}, sector_growth={})
        if a0:
            s1 = w7.analyze(code, 'x', 'x', df, anchors, reader=reader, mkt=mkt,
                            sector_strength={}, sector_growth={}, event_hint=a0)
        else:
            s1 = None
        # 只比较会决定猎狐推送的字段
        key = lambda s: None if s is None else (s.get('state'), s.get('event_date'), s.get('score'), s.get('type'), s.get('buy'))
        if key(s0) != key(s1):
            mism += 1
            print(f"MISMATCH {code}: plain={key(s0)} hint={key(s1)}")
        elif s0:
            n_sig += 1
    except Exception as e:
        print(f"ERR {code}: {e}")
print(f"checked={len(sample)} with_signal={n_sig} mismatches={mism}")
reader.close()
