# -*- coding: utf-8 -*-
"""盘中实时快照抓取器（新浪接口）

用途：盘中日线接口（tushare pro.daily）尚未落库时，用实时行情做午盘/盘中分析。

数据源说明：
  · tushare pro.daily  → 收盘后才有当日数据，盘中返回 0 行
  · 新浪 hq.sinajs.cn  → 盘中实时，含 pre_close / price / amount

用法：
    python _realtime_snapshot.py                 # 抓全市场 + ETF
    python _realtime_snapshot.py --date 20261102 # 指定日期标签

产物：
    _bt_out/realtime_<date>.csv       全市场（ts_code,name,open,pre_close,price,high,low,volume,amount,pct_chg）
    _bt_out/etf_realtime_<date>.csv    行业 ETF（同字段）
"""
import os
import sys
import time
import sqlite3
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(BASE), 'cache_daily', 'stock_data.db')
OUT = os.path.join(BASE, '_bt_out')

HEADERS = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}


def _fetch(codes, batch=800):
    """分批拉取新浪实时行情。返回 DataFrame。"""
    out = []
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = 'http://hq.sinajs.cn/list=' + ','.join(chunk)
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            raw = urllib.request.urlopen(req, timeout=20).read().decode('gbk', errors='ignore')
        except Exception as e:
            print(f'  批次 {i} 失败: {str(e)[:60]}', flush=True)
            continue
        for line in raw.strip().split('\n'):
            if '="' not in line:
                continue
            var, val = line.split('="', 1)
            val = val.rstrip('";')
            code = var.replace('var hq_str_', '')
            f = val.split(',')
            if len(f) < 10:
                continue
            try:
                out.append({
                    'sina': code, 'name': f[0],
                    'open': float(f[1] or 0), 'pre_close': float(f[2] or 0),
                    'price': float(f[3] or 0), 'high': float(f[4] or 0),
                    'low': float(f[5] or 0), 'volume': float(f[8] or 0),
                    'amount': float(f[9] or 0),
                })
            except ValueError:
                continue
        time.sleep(0.15)
    return pd.DataFrame(out)


def _to_ts(sina_code):
    """sh600000 -> 600000.SH"""
    if sina_code.startswith('sh'):
        return sina_code[2:] + '.SH'
    if sina_code.startswith('sz'):
        return sina_code[2:] + '.SZ'
    if sina_code.startswith('bj'):
        return sina_code[2:] + '.BJ'
    return sina_code


def _to_sina(ts_code):
    """600000.SH -> sh600000"""
    num, mk = ts_code.split('.')
    return {'SH': 'sh', 'SZ': 'sz', 'BJ': 'bj'}.get(mk, 'sh') + num


def fetch_market(trade_date='20261102'):
    """抓全市场实时快照。以最新交易日的成分股列表为股票池。"""
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    rows = conn.execute(
        "SELECT DISTINCT ts_code FROM daily_cache "
        "WHERE trade_date=(SELECT MAX(trade_date) FROM daily_cache)").fetchall()
    conn.close()
    ts_codes = sorted(r[0] for r in rows)
    print(f'股票池 {len(ts_codes)} 只', flush=True)
    df = _fetch([_to_sina(c) for c in ts_codes])
    if df.empty:
        return df
    df['ts_code'] = df['sina'].map(_to_ts)
    df = df[df['pre_close'] > 0].copy()
    # 停牌股 price=0 → 按 0% 处理（否则会被算成 -100% 虚增跌停）
    n_susp = int((df['price'] <= 0).sum())
    if n_susp:
        df.loc[df['price'] <= 0, 'price'] = df.loc[df['price'] <= 0, 'pre_close']
    df['pct_chg'] = (df['price'] / df['pre_close'] - 1) * 100
    df['trade_date'] = trade_date
    df = df.drop(columns=['sina'])
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, f'realtime_{trade_date}.csv')
    df.to_csv(p, index=False, encoding='utf-8')
    up = int((df.pct_chg > 0).sum()); dn = int((df.pct_chg < 0).sum())
    print(f'✓ {p}  {len(df)} 只（停牌 {n_susp}）  '
          f'上涨 {up} 下跌 {dn} 广度 {up/(up+dn)*100:.1f}%  '
          f'半日成交 {df.amount.sum()/1e8:.0f} 亿')
    return df


def fetch_etf(trade_date='20261102'):
    """抓行业 ETF 实时快照（主线判定用）。"""
    import etf_mainline as em
    codes = sorted(set(c for v in em.ETF_POOL.values() for c in v))
    print(f'ETF {len(codes)} 只', flush=True)
    df = _fetch([_to_sina(c) for c in codes], batch=400)
    if df.empty:
        return df
    df['ts_code'] = df['sina'].map(_to_ts)
    df = df[(df['pre_close'] > 0) & (df['price'] > 0)].copy()
    df['pct_chg'] = (df['price'] / df['pre_close'] - 1) * 100
    df['trade_date'] = trade_date
    df = df.drop(columns=['sina'])
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, f'etf_realtime_{trade_date}.csv')
    df.to_csv(p, index=False, encoding='utf-8')
    print(f'✓ {p}  {len(df)} 只')
    top = df.nlargest(8, 'pct_chg')
    print('   涨幅前 8: ' + ' '.join(
        f"{r.ts_code.split('.')[0]}{r.pct_chg:+.1f}%" for _, r in top.iterrows()))
    return df


def main():
    trade_date = '20261102'
    if '--date' in sys.argv:
        trade_date = sys.argv[sys.argv.index('--date') + 1]
    print(f'=== 实时快照 {trade_date} ===')
    fetch_market(trade_date)
    print()
    fetch_etf(trade_date)


if __name__ == '__main__':
    main()
