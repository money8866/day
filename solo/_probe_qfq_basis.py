# -*- coding: utf-8 -*-
"""临时探针2：验证 adj_factor 缓存 + 前复权折算有效，且 detect_breakout 正常"""
import sys
sys.path.insert(0, r'd:\mystock\solo')
import pandas as pd
import stock_cache as sc
import tushare_quant as tq

END = '20260904'
codes = ['600519.SH', '601398.SH', '000001.SZ', '601988.SH']
for code in codes:
    try:
        day = sc.get_daily_cache(code, '20250601', END)
        adj = sc.cached_adj_factor(code, '20250601', END)
        day = day.sort_values('trade_date').reset_index(drop=True)
        if adj is None or adj.empty:
            print(code, 'adj empty'); continue
        a = adj.set_index('trade_date')['adj_factor'].reindex(day['trade_date']).ffill().bfill()
        anchor = float(a.iloc[-1])
        qfq_close = pd.Series((day['close'].to_numpy() * a.to_numpy() / anchor).round(4), index=day.index)
        ma20_raw = day['close'].rolling(20).mean().iloc[-1]
        ma20_qfq = qfq_close.rolling(20).mean().iloc[-1]
        ma60_qfq = qfq_close.rolling(60).mean().iloc[-1]
        n_div = int((pd.Series(a.to_numpy()).diff().fillna(0) != 0).sum())  # 区间内除权变更次数
        r = tq.detect_breakout(code, None, trade_date=END)
        print(f'{code} | div_changes={n_div} | ma20_raw={ma20_raw:.4f} ma20_qfq={ma20_qfq:.4f} ma60_qfq={ma60_qfq:.4f} | breakout={r["breakout_type"]} score={r["breakout_score"]} | adj_rows={len(adj)}')
    except Exception as e:
        print(code, 'ERR', repr(e))
