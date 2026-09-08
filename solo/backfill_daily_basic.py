# -*- coding: utf-8 -*-
"""daily_basic_cache / adj_factor_cache 历史回填工具（普通日线数据方案，不再依赖 stk_factor_pro）

从 2023-01-01（含）至今的每个交易日，逐日调用 Tushare API 全市场拉取
pro.daily_basic / pro.adj_factor 并写回窄表；已完整(>=MIN_ROWS)或已确认空的
日期自动跳过，支持断点续跑（重跑本脚本即继续），单日失败自动重试。

用法:
  python backfill_daily_basic.py                       # 全量: daily_basic + adj_factor, 20230103~今天
  python backfill_daily_basic.py daily_basic           # 仅回填 daily_basic
  python backfill_daily_basic.py adj_factor            # 仅回填 adj_factor
  python backfill_daily_basic.py all 20230101 20260930 # 自定义区间
"""
import sys
import time
from datetime import datetime, timedelta

import stock_cache as sc

DEFAULT_START = '20230103'
MIN_ROWS = sc.UDC_MARKET_MIN_COUNT
MIN_INTERVAL = 0.12
RETRY_TIMES = 4
RETRY_BASE_SLEEP = 5

_KINDS = ('all', 'daily_basic', 'adj_factor')


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


def get_trade_dates(pro, start, end):
    """交易日历（失败时退化为工作日）"""
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


def is_complete(kind, d):
    if kind == 'daily_basic':
        return sc.get_daily_basic_valuation_count(d) >= MIN_ROWS
    return sc.get_adj_factor_by_date_count(d) >= MIN_ROWS


def is_empty_marked(kind, d):
    key = f'db_market_empty_{d}' if kind == 'daily_basic' else f'af_market_empty_{d}'
    return sc.get_meta(key, '') == '1'


def fill_one(kind, d, pro):
    """单日填充。返回 (ok, changed): changed=True 表示本日发生 API 拉取写回。"""
    if is_complete(kind, d) or is_empty_marked(kind, d):
        return True, False
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            if kind == 'daily_basic':
                sc.daily_basic_market(d, pro=pro, silent=True)
            else:
                sc.adj_factor_market(d, pro=pro, silent=True)
            time.sleep(MIN_INTERVAL)
            return True, True
        except Exception as e:
            wait = RETRY_BASE_SLEEP * attempt
            log(f'{kind} {d} 第{attempt}/{RETRY_TIMES}次失败: {e}，{wait}s 后重试')
            time.sleep(wait)
    return False, False


def run(kind, start, end):
    if kind not in _KINDS:
        raise ValueError(f'kind 必须是 {_KINDS} 之一')
    targets = ('daily_basic', 'adj_factor') if kind == 'all' else (kind,)
    sc._ensure_daily_basic_table()
    sc._ensure_adj_factor_table()

    pro = sc._get_pro()
    todo = get_trade_dates(pro, start, end)
    log(f'交易日 {len(todo)} 天 [{start}~{end}]，目标表 {list(targets)}')

    summary = {}
    for k in targets:
        pending = [d for d in todo if not (is_complete(k, d) or is_empty_marked(k, d))]
        log(f'[{k}] 待补齐 {len(pending)} 天')
        if not pending:
            summary[k] = (0, 0)
            continue
        done, changed, failed, t0 = 0, 0, [], time.time()
        for d in pending:
            ok, ch = fill_one(k, d, pro)
            if not ok:
                failed.append(d)
                continue
            done += 1
            changed += int(ch)
            if done % 10 == 0:
                speed = done / max(time.time() - t0, 1e-6)
                remain = (len(pending) - done) / max(speed, 1e-6) / 60
                log(f'[{k}] 进度 {done}/{len(pending)} | {d} | 剩余约 {remain:.1f} 分钟')
        summary[k] = (changed, len(failed))
        log(f'[{k}] 完成: 新写 {changed} 天，失败 {len(failed)} 天{failed[:20] if failed else ""}')

    # 汇总抽查
    for k in targets:
        if k == 'daily_basic':
            with sc.get_conn() as conn:
                row = conn.execute(f'SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {sc.DAILY_BASIC_CACHE_TABLE}').fetchone()
        else:
            with sc.get_conn() as conn:
                row = conn.execute(f'SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {sc.ADJ_FACTOR_CACHE_TABLE}').fetchone()
        log(f'[{k}] 共 {row[0]:,} 行，{row[1]}~{row[2]}')
    for d in (start, todo[-1]):
        log(f'  抽查 {d}: daily_basic={sc.get_daily_basic_by_date_count(d)} adj_factor={sc.get_adj_factor_by_date_count(d)}')
    return summary


if __name__ == '__main__':
    args = [a for a in sys.argv[1:]]
    kind = args[0] if args and args[0] in _KINDS else 'all'
    if args and args[0] not in _KINDS:
        kind = 'all'
    rest = [a for a in args if a not in _KINDS]
    start = rest[0] if len(rest) >= 1 else DEFAULT_START
    end = rest[1] if len(rest) >= 2 else datetime.now().strftime('%Y%m%d')
    run(kind, start, end)
