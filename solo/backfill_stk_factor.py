# -*- coding: utf-8 -*-
"""stk_factor_pro 历史回填工具

策略: 按"交易日"整市场抓取(pro.stk_factor_pro(trade_date=x, fields=_STK_FACTOR_FIELDS))，
每天 1 次 API 调用，把残缺的历史日期补全到全市场完整水平。

特性(与 backfill_daily_cache.py 一致):
  - 断点续跑: 已完整日期(>= MIN_ROWS)自动跳过，重跑即可继续
  - 节假日/未计算标记: 空数据日期写入 meta，不再重复调用
  - 失败重试: 指数退避 + 二轮补抓
  - 不动 batch_date 标记: 不干扰"最近批量缓存日期"语义

用法:
  python backfill_stk_factor.py                  # 默认 20240101 ~ 20241231
  python backfill_stk_factor.py 20220101 20221231
"""
import sys
import time
from datetime import datetime, timedelta

import stock_cache as sc

DEFAULT_START = '20240101'
DEFAULT_END = '20241231'
MIN_ROWS = 4500                 # stk_factor_pro 单日完整性阈值
MIN_INTERVAL = 0.12             # API 最小间隔(秒)
RETRY_TIMES = 4
RETRY_BASE_SLEEP = 5


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


def get_existing_counts(start, end):
    sql = (f'SELECT trade_date, COUNT(*) AS cnt FROM {sc.STK_FACTOR_TABLE} '
           f'WHERE trade_date >= ? AND trade_date <= ? GROUP BY trade_date')
    with sc.get_conn() as conn:
        rows = conn.execute(sql, (str(start), str(end))).fetchall()
    return {r[0]: r[1] for r in rows}


def get_trade_dates(pro, start, end):
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
    log(f'待回填 {len(todo)} 天（其余 {len(trade_dates) - len(todo)} 天已完整或已标记）')
    if not todo:
        log('无需回填')
        return

    failed, done, total_rows = [], 0, 0
    t0 = time.time()
    try:
        for d in todo:
            ok = False
            for attempt in range(1, RETRY_TIMES + 1):
                try:
                    df = pro.stk_factor_pro(trade_date=d, fields=sc._STK_FACTOR_FIELDS)
                    time.sleep(MIN_INTERVAL)
                    if df is None or df.empty:
                        sc.set_meta(f'udc_market_empty_{d}', '1')
                        log(f'{d} 无因子数据(疑似非交易日/未计算)，已标记跳过')
                    else:
                        df['trade_date'] = df['trade_date'].astype(str)
                        sc.batch_insert_stk_factor_pro(df)
                        done += 1
                        total_rows += len(df)
                        if done % 10 == 0 or done == len(todo):
                            speed = done / max(time.time() - t0, 1e-6)
                            remain = (len(todo) - done) / max(speed, 1e-6) / 60
                            log(f'进度 {done}/{len(todo)} | {d} 写入 {len(df)} 行 | '
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
                df = pro.stk_factor_pro(trade_date=d, fields=sc._STK_FACTOR_FIELDS)
                time.sleep(MIN_INTERVAL)
                if df is None or df.empty:
                    sc.set_meta(f'udc_market_empty_{d}', '1')
                else:
                    df['trade_date'] = df['trade_date'].astype(str)
                    sc.batch_insert_stk_factor_pro(df)
                    total_rows += len(df)
            except Exception as e:
                still.append(d)
                log(f'{d} 二轮仍失败: {e}')
        failed = still

    log(f'回填结束: 本次成功 {done} 天 / 新增 {total_rows} 行 / 失败 {len(failed)} 天')
    if failed:
        log(f'失败日期(重跑本脚本可续补): {failed}')


if __name__ == '__main__':
    start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    end = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_END
    backfill(start, end)
