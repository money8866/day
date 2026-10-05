# -*- coding: utf-8 -*-
"""
H-ZZ2K-A1  中证2000 与对照宽基成分（PIT）取数 —— 数据构建阶段，一次性

为什么必须另取：
    cache_daily 中无任何指数成分文件（已核实）；
    stock_data.db 仅有 index_daily_cache（指数行情），无成分表。
    => 中证2000 成分股名单必须从 Tushare 取一次并落盘到本课题 data/。

接口选择（已探针验证）：
    index_member_all  = 申万行业成分（传入 ts_code='932000.CSI' 返回 0 行）-> 不可用
    index_weight      = 指数成分与权重（月度），932000.CSI 可用              -> 采用

关键坑：
    index_weight 单次返回上限 7000 行；932000 每月约 2000 只，
    大区间调用会被静默截断为最近若干月 => 必须按月分片调用。

纪律：
    不重复下载（文件存在即跳过，--force 才重取）；
    产物只落本课题 data/，不写入共享 cache_daily。

用法
    python -u zz2k_fetch_members.py            # 已有则跳过
    python -u zz2k_fetch_members.py --force    # 强制重取
"""
import os
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
FP_MAIN = os.path.join(DATA, 'zz2k_members.parquet')
FP_BENCH = os.path.join(DATA, 'bench_members.parquet')
ENV = r'D:\mystock\config\.env'

START = '20210101'
END = '20260924'

MAIN_CODE = '932000.CSI'
MAIN_NAME = '中证2000'
BENCH = {'000300.SH': '沪深300', '000905.SH': '中证500', '000852.SH': '中证1000'}


def token():
    if os.path.exists(ENV):
        for line in open(ENV, encoding='utf-8'):
            line = line.strip()
            if line.startswith('TUSHARE_TOKEN='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    return os.environ.get('TUSHARE_TOKEN', '')


def month_chunks(start, end):
    """'20210101'..'20260924' -> [(20210101,20210131), ...]"""
    a = pd.Timestamp(start)
    b = pd.Timestamp(end)
    out = []
    cur = a.replace(day=1)
    while cur <= b:
        nxt = (cur + pd.offsets.MonthBegin(1))
        lo = max(cur, a)
        hi = min(nxt - pd.Timedelta(days=1), b)
        out.append((lo.strftime('%Y%m%d'), hi.strftime('%Y%m%d')))
        cur = nxt
    return out


def fetch_one(pro, code, log):
    """按月分片拉取单只指数的成分与权重"""
    frames, empty, fail = [], 0, 0
    chunks = month_chunks(START, END)
    for i, (lo, hi) in enumerate(chunks):
        for attempt in range(4):
            try:
                d = pro.index_weight(index_code=code, start_date=lo, end_date=hi)
                break
            except Exception as ex:
                if attempt == 3:
                    fail += 1
                    log('    [WARN] %s %s~%s 失败: %s' % (code, lo, hi, str(ex)[:120]))
                    d = None
                else:
                    time.sleep(2.0 * (attempt + 1))
        if d is None or len(d) == 0:
            empty += 1
        else:
            frames.append(d)
        time.sleep(0.12)
        if (i + 1) % 20 == 0:
            log('    ... %d/%d 月已处理' % (i + 1, len(chunks)))
    if not frames:
        return pd.DataFrame(columns=['index_code', 'con_code', 'trade_date', 'weight'])
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(['index_code', 'con_code', 'trade_date'])
    out = out.sort_values(['trade_date', 'con_code']).reset_index(drop=True)
    log('  [%s] 完成: %d 行 / 唯一快照日 %d / 覆盖股票 %d / 空月 %d / 失败月 %d'
        % (code, len(out), out['trade_date'].nunique(),
           out['con_code'].nunique(), empty, fail))
    return out


def coverage(d, log, tag):
    log('  [%s] 逐年快照数与月均成分数:' % tag)
    if len(d) == 0:
        log('    (空)')
        return
    g = d.copy()
    g['yr'] = g['trade_date'].str[:4]
    for yr, sub in g.groupby('yr'):
        nsnap = sub['trade_date'].nunique()
        per = sub.groupby('trade_date')['con_code'].size().mean()
        log('    %s: 快照 %2d 个, 月均成分 %.0f 只' % (yr, nsnap, per))


def main():
    force = '--force' in sys.argv
    os.makedirs(DATA, exist_ok=True)

    if os.path.exists(FP_MAIN) and not force:
        m = pd.read_parquet(FP_MAIN)
        print('[SKIP] 已存在 %s（%d 行 / %d 快照日）。--force 可重取。'
              % (FP_MAIN, len(m), m['trade_date'].nunique()))
        return

    import tushare as ts
    pro = ts.pro_api(token())

    def log(*a):
        print(' '.join(str(x) for x in a), flush=True)

    log('=' * 70)
    log('H-ZZ2K-A1 成分取数  %s ~ %s' % (START, END))
    log('=' * 70)

    log('\n1) 中证2000（%s / %s）' % (MAIN_CODE, MAIN_NAME))
    main_df = fetch_one(pro, MAIN_CODE, log)
    coverage(main_df, log, 'MEMBERS')
    main_df.to_parquet(FP_MAIN, index=False)
    log('  [SAVE] %s  %d 行' % (FP_MAIN, len(main_df)))

    log('\n2) 对照宽基（U-PROXY 剔除用）')
    bframes = []
    for code, name in BENCH.items():
        log('  -- %s %s' % (code, name))
        b = fetch_one(pro, code, log)
        if len(b):
            bframes.append(b)
    if bframes:
        bench_df = pd.concat(bframes, ignore_index=True)
        bench_df = bench_df.drop_duplicates(['index_code', 'con_code', 'trade_date'])
        bench_df = bench_df.sort_values(['index_code', 'trade_date', 'con_code'])
        bench_df = bench_df.reset_index(drop=True)
        bench_df.to_parquet(FP_BENCH, index=False)
        log('  [SAVE] %s  %d 行 / 指数 %d'
            % (FP_BENCH, len(bench_df), bench_df['index_code'].nunique()))
    else:
        log('  [WARN] 对照宽基无数据，U-PROXY 将不可用')

    log('\n' + '=' * 70)
    log('DONE')


if __name__ == '__main__':
    main()
