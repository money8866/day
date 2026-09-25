# -*- coding: utf-8 -*-
"""fs_build_price: 价格/估值/指数面板
- 本地 stock_data.db 提供 2021-01-04 之后的全市场日线、2023-01-03 之后的估值
- 2018-01-01 ~ 2022-12-31 的 daily / daily_basic 通过 Tushare API 补齐，
  只写入本研究的隔离目录 data/，不触碰生产缓存（§1）
"""
import os
import sys
import time
import glob
import sqlite3
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import (DATA, DB, HORIZONS, PRICE_COLS, DBASIC_COLS,
                       add_qfq, get_pro, Log)

log = Log('_fs_build_price.txt')
BACKFILL_DAILY = ('20180101', '20201231')
BACKFILL_BASIC = ('20180101', '20221231')


def fetch_range(pro, method, start, end, cols, tag, log):
    """按交易日逐日拉取；按年份落盘做断点续传"""
    y0, y1 = int(start[:4]), int(end[:4])
    out = []
    for y in range(y0, y1 + 1):
        fp = os.path.join(DATA, '_ext_%s_%d.parquet' % (tag, y))
        if os.path.exists(fp):
            d = pd.read_parquet(fp)
            log('  [cache] %s %d rows=%d' % (tag, y, len(d)))
            out.append(d)
            continue
        s = max(start, '%d0101' % y)
        e = min(end, '%d1231' % y)
        cal = pro.trade_cal(exchange='SSE', start_date=s, end_date=e, is_open='1')
        dates = sorted(cal['cal_date'].astype(str).tolist())
        frames = []
        t0 = time.time()
        for d in dates:
            df = None
            for _ in range(3):
                try:
                    df = getattr(pro, method)(trade_date=d)
                    break
                except Exception as ex:
                    log('    retry %s %s: %s' % (method, d, str(ex)[:90]))
                    time.sleep(1.5)
            if df is None:
                log('    FAIL %s %s' % (method, d))
                continue
            if len(df):
                frames.append(df[[c for c in cols if c in df.columns]])
            time.sleep(0.13)
        dd = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
        dd.to_parquet(fp, index=False)
        log('  [fetch] %s %d rows=%d days=%d %.1fs' % (tag, y, len(dd), len(dates), time.time() - t0))
        out.append(dd)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=cols)


def main():
    pro = get_pro()

    # ---------- 1) 日线 ----------
    log('=' * 60)
    log('1) 日线面板')
    ext = fetch_range(pro, 'daily', BACKFILL_DAILY[0], BACKFILL_DAILY[1],
                      PRICE_COLS, 'daily', log)
    log('  扩展段 daily: %d 行  %s ~ %s' % (len(ext), ext['trade_date'].min(), ext['trade_date'].max()))

    c = sqlite3.connect(DB, timeout=300)
    loc = pd.read_sql_query(
        "SELECT %s FROM daily_cache WHERE trade_date >= '20210101'" % ','.join(PRICE_COLS), c)
    c.close()
    log('  本地段 daily: %d 行  %s ~ %s' % (len(loc), loc['trade_date'].min(), loc['trade_date'].max()))

    px = pd.concat([ext, loc], ignore_index=True)
    for c_ in PRICE_COLS:
        px[c_] = pd.to_numeric(px[c_], errors='coerce') if c_ not in ('ts_code', 'trade_date') else px[c_]
    px = px.drop_duplicates(['ts_code', 'trade_date']).sort_values(['ts_code', 'trade_date'])
    log('  合并去重: %d 行  股票 %d  交易日 %d' % (len(px), px['ts_code'].nunique(), px['trade_date'].nunique()))

    px = add_qfq(px)
    px.to_parquet(os.path.join(DATA, 'price_panel.parquet'), index=False)
    log('  已存 data/price_panel.parquet cols=%s' % list(px.columns))

    # 交易日历
    cal = pd.DataFrame({'trade_date': sorted(px['trade_date'].unique())})
    cal['d'] = pd.to_datetime(cal['trade_date'], format='%Y%m%d')
    cal = cal.sort_values('trade_date').reset_index(drop=True)
    cal['idx'] = np.arange(len(cal))
    cal.to_parquet(os.path.join(DATA, 'calendar.parquet'), index=False)
    log('  交易日历: %d 天  %s ~ %s' % (len(cal), cal['trade_date'].iloc[0], cal['trade_date'].iloc[-1]))

    # ---------- 2) 估值 ----------
    log('=' * 60)
    log('2) 估值面板 daily_basic')
    extb = fetch_range(pro, 'daily_basic', BACKFILL_BASIC[0], BACKFILL_BASIC[1],
                       DBASIC_COLS, 'basic', log)
    c = sqlite3.connect(DB, timeout=300)
    have = [r[1] for r in c.execute("PRAGMA table_info(daily_basic_cache)")]
    bcols = [x for x in DBASIC_COLS if x in have]
    locb = pd.read_sql_query(
        "SELECT %s FROM daily_basic_cache WHERE trade_date >= '20210101'" % ','.join(bcols), c)
    c.close()
    log('  本地段 daily_basic: %d 行 cols=%s' % (len(locb), bcols))
    for c_ in extb.columns:
        if c_ not in locb.columns:
            locb[c_] = np.nan
    locb = locb[[x for x in DBASIC_COLS if x in locb.columns]]
    bas = pd.concat([extb, locb], ignore_index=True)
    for c_ in bas.columns:
        if c_ not in ('ts_code', 'trade_date'):
            bas[c_] = pd.to_numeric(bas[c_], errors='coerce')
    bas = bas.drop_duplicates(['ts_code', 'trade_date']).sort_values(['ts_code', 'trade_date'])
    bas.to_parquet(os.path.join(DATA, 'basic_panel.parquet'), index=False)
    log('  合并估值: %d 行  %s ~ %s  cols=%s' % (
        len(bas), bas['trade_date'].min(), bas['trade_date'].max(), list(bas.columns)))

    # ---------- 3) 指数 ----------
    log('=' * 60)
    log('3) 指数面板')
    c = sqlite3.connect(DB, timeout=300)
    idx = pd.read_sql_query(
        "SELECT ts_code,trade_date,open,high,low,close,pre_close,vol,amount "
        "FROM index_daily_cache WHERE ts_code='000300.SH'", c)
    c.close()
    log('  本地 000300.SH: %d 行 %s ~ %s' % (len(idx), idx['trade_date'].min(), idx['trade_date'].max()))
    if idx['trade_date'].min() > '20180101':
        try:
            add = pro.index_daily(ts_code='000300.SH', start_date='20171201', end_date='20200102')
            add = add[[c for c in ('ts_code', 'trade_date', 'open', 'high', 'low', 'close',
                                   'pre_close', 'vol', 'amount') if c in add.columns]]
            for c_ in ('vol', 'amount', 'pre_close'):
                if c_ not in add.columns:
                    add[c_] = np.nan
            log('  API 补齐 000300.SH: %d 行' % len(add))
            idx = pd.concat([add, idx], ignore_index=True)
        except Exception as ex:
            log('  指数补齐失败: %s' % str(ex)[:150])
    idx = idx.drop_duplicates(['ts_code', 'trade_date']).sort_values('trade_date')
    for c_ in ('open', 'high', 'low', 'close', 'pre_close', 'vol', 'amount'):
        idx[c_] = pd.to_numeric(idx[c_], errors='coerce')
    idx.to_parquet(os.path.join(DATA, 'index_panel.parquet'), index=False)
    log('  指数: %d 行 %s ~ %s' % (len(idx), idx['trade_date'].min(), idx['trade_date'].max()))

    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
