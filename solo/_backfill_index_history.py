#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
补齐 2024-2025 指数历史日线（供回测与位置门计算使用）
- 数据源：tushare pro.index_daily（备选 akshare）
- 输出：cache_backbone_tushare/index_history/{code}.csv
- 合并写入 cache.db（与现有 tsc_index_kline 缓存同库，便于复用）
"""
import os
import sys
import sqlite3
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, 'cache_backbone_tushare', 'index_history')
os.makedirs(OUT_DIR, exist_ok=True)

INDICES = {
    '000001.SH': '上证指数',
    '000300.SH': '沪深300',
    '932000.CSI': '中证2000',
}
START = '20240101'
END = '20251231'

def load_token():
    for env_path in [os.path.join(BASE, '.env'),
                     os.path.join(os.path.dirname(BASE), 'config', '.env')]:
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('TUSHARE_TOKEN='):
                        return line.split('=', 1)[1].strip()
    return None

def fetch_tushare(code):
    import tushare as ts
    token = load_token()
    if not token:
        return None
    ts.set_token(token)
    pro = ts.pro_api()
    df = pro.index_daily(ts_code=code, start_date=START, end_date=END)
    return df

def fetch_akshare(code):
    import akshare as ak
    sym_map = {'000001.SH': 'sh000001', '000300.SH': 'sh000300', '932000.CSI': 'sh932000'}
    sym = sym_map.get(code)
    if not sym:
        return None
    df = ak.stock_zh_index_daily(symbol=sym)
    if df is None or df.empty:
        return None
    df = df.reset_index()
    df.columns = [str(c) for c in df.columns]
    # 统一列名
    rename = {}
    for c in df.columns:
        cl = c.lower()
        if cl in ('date', 'trade_date'): rename[c] = 'trade_date'
        elif cl == 'open': rename[c] = 'open'
        elif cl == 'high': rename[c] = 'high'
        elif cl == 'low': rename[c] = 'low'
        elif cl == 'close': rename[c] = 'close'
        elif cl == 'volume': rename[c] = 'vol'
    df = df.rename(columns=rename)
    if 'trade_date' in df.columns:
        df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '', regex=False)
    df = df[(df['trade_date'] >= START) & (df['trade_date'] <= END)]
    return df

def main():
    for code, name in INDICES.items():
        df = None
        src = ''
        try:
            df = fetch_tushare(code)
            src = 'tushare'
        except Exception as e:
            print(f"[{name}] tushare 失败: {e}")
        if df is None or df.empty:
            try:
                df = fetch_akshare(code)
                src = 'akshare'
            except Exception as e:
                print(f"[{name}] akshare 失败: {e}")
        if df is None or df.empty:
            print(f"[{name}] ❌ 两个数据源都失败")
            continue
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date')
        out = os.path.join(OUT_DIR, f'{code}.csv')
        df.to_csv(out, index=False, encoding='utf-8-sig')
        print(f"[{name}] ✅ {src}: {len(df)} 天  {df['trade_date'].min()} ~ {df['trade_date'].max()}  -> {out}")

    # 合并写入 cache.db（tsc_index_kline_hist_{code} 表，供回测脚本直接 SQL 读取）
    db = os.path.join(BASE, 'cache_backbone_tushare', 'cache.db')
    conn = sqlite3.connect(db)
    for code in INDICES:
        f = os.path.join(OUT_DIR, f'{code}.csv')
        if not os.path.exists(f):
            continue
        df = pd.read_csv(f, dtype={'trade_date': str})
        df.to_sql(f'hist_{code.replace(".", "_")}', conn, if_exists='replace', index=False)
    conn.close()
    print("\n已写入 cache.db 的 hist_* 表")

if __name__ == '__main__':
    main()
