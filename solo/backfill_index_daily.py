# -*- coding: utf-8 -*-
"""指数日线缓存回填工具（index_daily_cache）

指数历史一次性拉取（pro.index_daily 单次可返回整段），无需按日整市场补数。
幂等：已有数据自动从缓存末日起增量补，重跑即可续填。

用法:
  python backfill_index_daily.py                       # 全部宽基指数，20210104 ~ 最近交易日
  python backfill_index_daily.py 20210104 20260914
  python backfill_index_daily.py 20210104 20260914 000300.SH,000001.SH
"""
import sys
import time
from datetime import datetime

import stock_cache as sc


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


if __name__ == '__main__':
    start = sys.argv[1] if len(sys.argv) > 1 else sc.INDEX_HISTORY_START
    end = sys.argv[2] if len(sys.argv) > 2 else sc.get_effective_date()
    codes = sys.argv[3].split(',') if len(sys.argv) > 3 else None

    log(f'回填指数缓存 {start}~{end}，标的 {codes or sc.INDEX_CODES}')
    t0 = time.time()
    res = sc.backfill_index_daily(start, end, codes=codes)
    log(f'完成，耗时 {time.time() - t0:.1f}s，共 {len(res)} 个指数')
    st = sc.index_cache_status()
    for c in st['codes']:
        log(f"  {c['ts_code']:<12} {c['min']} ~ {c['max']}  {c['rows']} 行")
