import json, os, time, random
import w7_second_wave_engine as w7

# 1) 主题池规模
p = os.path.join('..', 'cache_daily', 'theme_stock_map_latest.json')
if not os.path.exists(p):
    p = r'D:\mystock\cache_daily\theme_stock_map_latest.json'
with open(p, 'r', encoding='utf-8') as f:
    data = json.load(f)
themes = data.get('themes', {})
codes = []
for name, stocks in themes.items():
    for s in stocks:
        c = s.get('code')
        if c and c.endswith(('.SZ', '.SH')) and c not in codes:
            codes.append(c)
print(f"themes={len(themes)} unique_codes={len(codes)}", flush=True)

# 2) 单次扫描耗时估算(与 scan_fox_t0 相同的数据准备 + analyze 全量)
reader = w7.CacheReader()
end_db = reader.latest_date()
print("end_db=", end_db, flush=True)
random.seed(7)
sample = random.sample(codes, min(300, len(codes)))
t0 = time.time()
reader.load_all(end_db, codes=sample, min_date=w7.DATA_START)
print(f"load_all({len(sample)})  {time.time()-t0:.1f}s", flush=True)
reader.load_fina()
mkt = w7.MarketCtx(*reader.market_curve(end_db))

anchors = {}
for label, (code, adate) in w7.ANCHORS.items():
    try:
        adf = reader.bars_sql(code, end_db)
        anchors[label] = w7.anchor_features(adf, adate)
    except Exception:
        anchors[label] = None

n_hit, n_ok = 0, 0
t1 = time.time()
for i, code in enumerate(sample):
    df0 = reader.bars(code, end_db)
    if df0 is None or len(df0) < w7.MIN_BARS:
        continue
    # 模拟 fox: 追加一个合成今日K线(用最后一根近似)
    prev = df0.iloc[-1]
    row = dict(prev)
    row.update({'ts_code': code, 'trade_date': '20990101', 'open': prev.open,
                'high': prev.high, 'low': prev.low, 'close': prev.close,
                'pct_chg': 0.0, 'vol': prev.vol, 'amount': prev.amount})
    import pandas as pd
    df = pd.concat([df0, pd.DataFrame([row])], ignore_index=True)
    df = w7._fill_ma_columns(df)
    for col in df.columns:
        if col not in ('ts_code', 'trade_date'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['close', 'high', 'low', 'vol']).reset_index(drop=True)
    n_ok += 1
    try:
        sig = w7.analyze(code, 'x', 'x', df, anchors, reader=reader, mkt=mkt,
                         sector_strength={}, sector_growth={})
        if sig:
            n_hit += 1
    except Exception:
        pass
dt = time.time() - t1
print(f"analyze 已跑{n_ok}只 命中{sig and 'T0_CONFIRM' or ''} 耗时{dt:.1f}s "
      f"-> 单只均价 {dt/n_ok*1000:.1f}ms; 全池{n_ok and round(dt/n_ok*len(codes)) or 0}s")
reader.close()
