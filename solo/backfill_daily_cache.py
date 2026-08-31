# -*- coding: utf-8 -*-
"""daily_cache 历史回填工具

策略: 按"交易日"整市场抓取(pro.daily(trade_date=x))，每天仅 1 次 API 调用，
比逐只股票抓取(5550+ 次)快 5 倍以上，且能顺带收录已退市股票(无幸存者偏差)。

特性:
  - 断点续跑: 已完整的日期(>= MIN_ROWS)自动跳过，中断后重跑即可继续
  - 节假日标记: 空数据日期写入 udc_market_empty_YYYYMMDD，不再重复调用
  - 失败重试: 单日失败自动重试(指数退避)，结尾对失败日期二轮补抓

用法:
  python backfill_daily_cache.py                     # 默认 20210101 ~ 20250101
  python backfill_daily_cache.py 20200101 20201231   # 自定义区间
"""
import sys
import time
from datetime import datetime, timedelta

import stock_cache as sc

DEFAULT_START = '20210101'
DEFAULT_END = '20250101'
MIN_ROWS = sc.UDC_MARKET_MIN_COUNT   # 单日完整性阈值(与 UDC 一致)
MIN_INTERVAL = 0.12                  # API 调用最小间隔(秒)
RETRY_TIMES = 4                      # 单日最大重试次数
RETRY_BASE_SLEEP = 5                 # 重试基础等待(秒)，按次数翻倍


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


def get_existing_counts(start, end):
    """一次 GROUP BY 拿到区间内每个交易日已有行数(避免逐日 COUNT 触发全表扫描)"""
    sql = (f"SELECT trade_date, COUNT(*) AS cnt FROM {sc.DAILY_CACHE_TABLE} "
           f"WHERE trade_date >= ? AND trade_date <= ? GROUP BY trade_date")
    with sc.get_conn() as conn:
        rows = conn.execute(sql, (str(start), str(end))).fetchall()
    return {r[0]: r[1] for r in rows}


def get_trade_dates(pro, start, end):
    """优先用交易日历；失败则退化为'仅工作日'候选(节假日靠空标记跳过)"""
    try:
        cal = pro.trade_cal(exchange='SSE', start_date=str(start), end_date=str(end), is_open='1')
        return sorted(str(x) for x in cal['cal_date'].tolist())
    except Exception as e:
        log(f'交易日历获取失败({e})，退化为工作日候选')
        dates, cur = [], datetime.strptime(str(start), '%Y%m%d')
        end_d = datetime.strptime(str(end), '%Y%m%d')
        while cur <= end_d:
            if cur.weekday() < 5:
                dates.append(cur.strftime('%Y%m%d'))
            cur += timedelta(days=1)
        return dates


def backfill(start, end):
    log(f'回填前缓存全貌: {sc.udc_stats()}')
    pro = sc._get_pro()
    trade_dates = get_trade_dates(pro, start, end)
    log(f'回填区间 {start}~{end}，交易日历共 {len(trade_dates)} 天')

    existing = get_existing_counts(start, end)
    todo = []
    for d in trade_dates:
        if existing.get(d, 0) >= MIN_ROWS:
            continue
        if sc.get_meta(f'udc_market_empty_{d}', '') == '1':
            continue
        todo.append(d)
    log(f'待回填 {len(todo)} 天（其余 {len(trade_dates) - len(todo)} 天已完整或已标记节假日）')
    if not todo:
        log('无需回填')
        log(f'回填后缓存全貌: {sc.udc_stats()}')
        return

    failed, done, total_rows = [], 0, 0
    t0 = time.time()
    try:
        for d in todo:
            ok = False
            for attempt in range(1, RETRY_TIMES + 1):
                try:
                    df = pro.daily(trade_date=d)
                    time.sleep(MIN_INTERVAL)
                    if df is None or df.empty:
                        sc.set_meta(f'udc_market_empty_{d}', '1')
                        log(f'{d} 空数据(疑似非交易日)，已标记跳过')
                    else:
                        sc.batch_insert_daily_cache(df)
                        done += 1
                        total_rows += len(df)
                        if done % 10 == 0 or done == len(todo):
                            speed = done / max(time.time() - t0, 1e-6)
                            remain = (len(todo) - done) / max(speed, 1e-6) / 60
                            log(f'进度 {done}/{len(todo)} | {d} 新增 {len(df)} 行 | '
                                f'累计 {total_rows} 行 | 剩余约 {remain:.1f} 分钟')
                    ok = True
                    break
                except Exception as e:
                    wait = RETRY_BASE_SLEEP * attempt
                    log(f'{d} 第{attempt}/{RETRY_TIMES}次失败: {e}，{wait}s 后重试')
                    time.sleep(wait)
            if not ok:
                failed.append(d)
    except KeyboardInterrupt:
        log('收到中断，已完成部分已落库；重跑本脚本可断点续跑')

    if failed:
        log(f'二轮补抓 {len(failed)} 个失败日期...')
        still = []
        for d in failed:
            try:
                df = pro.daily(trade_date=d)
                time.sleep(MIN_INTERVAL)
                if df is None or df.empty:
                    sc.set_meta(f'udc_market_empty_{d}', '1')
                else:
                    sc.batch_insert_daily_cache(df)
                    total_rows += len(df)
            except Exception as e:
                still.append(d)
                log(f'{d} 二轮仍失败: {e}')
        failed = still

    log(f'回填结束: 本次成功 {done} 天 / 新增 {total_rows} 行 / 失败 {len(failed)} 天')
    if failed:
        log(f'失败日期(重跑本脚本可续补): {failed}')

    min_d, max_d = sc.get_daily_cache_range('000001.SZ')
    log(f'抽查 000001.SZ 缓存范围: {min_d} ~ {max_d}')
    log(f'回填后缓存全貌: {sc.udc_stats()}')


if __name__ == '__main__':
    start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    end = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_END
    backfill(start, end)
