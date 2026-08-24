# -*- coding: utf-8 -*-
"""多进程验证：800只股票回测（窗口参数化，默认 20250901~20260315 多头窗口）"""
import sys, time, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'd:\mystock\solo')


class _Tee:
    """同时输出到终端与日志文件（避免 PowerShell 重定向丢输出）"""
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, 'w', encoding='utf-8')
        self.console = sys.stdout
    def write(self, s):
        self.console.write(s)
        self.f.write(s)
    def flush(self):
        self.console.flush()
        self.f.flush()
    def close(self):
        self.f.close()


from pbp.data import get_stock_pool
from pbp.scanner import _backtest_one
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
import numpy as np


def main():
    import sys as _sys
    start = _sys.argv[1] if len(_sys.argv) > 1 else '20250901'
    end = _sys.argv[2] if len(_sys.argv) > 2 else '20260315'
    tee = _Tee('output/pbp/_bt_live_out.txt')
    sys.stdout = tee
    t0 = time.time()
    os.makedirs('output/pbp', exist_ok=True)
    pool = get_stock_pool()
    sample = pool.sample(800, random_state=7)
    print(f'抽样 {len(sample)} 只, 8进程并行, 窗口 {start}~{end}...', flush=True)

    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), start, end)
             for _, r in sample.iterrows()]
    rows = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        for i, res in enumerate(ex.map(_backtest_one, tasks, chunksize=8)):
            if res:
                rows.extend(res)
            if (i + 1) % 200 == 0:
                print(f'  {i+1}只 信号{len(rows)} 耗时{time.time()-t0:.0f}s', flush=True)

    df = pd.DataFrame(rows)
    print(f'\n总耗时 {time.time()-t0:.0f}s, 信号 {len(df)}', flush=True)
    if df.empty:
        print('无信号')
        return
    raw_n = len(df)
    df = (df.sort_values(['date', 'final_score'], ascending=[True, False])
            .drop_duplicates(subset=['ts_code', 'breakout_date'], keep='first'))
    print(f'去重 {raw_n} -> {len(df)}', flush=True)
    print('action 分布:', flush=True)
    print(df['action'].value_counts().to_string(), flush=True)
    print()
    for act in ('PRIMARY_BUY', 'CONFIRMED_BUY', 'EARLY_BUY', 'WAIT_REACCELERATION'):
        g = df[df['action'] == act]
        if len(g):
            s3 = g['fut3'].dropna(); s5 = g['fut5'].dropna(); s10 = g['fut10'].dropna()
            print(f'[{act}] {len(g)}笔 | 3日 {s3.mean():+.2f}%(胜{(s3>0).mean()*100:.0f}%) '
                  f'5日 {s5.mean():+.2f}%(胜{(s5>0).mean()*100:.0f}%) '
                  f'10日 {s10.mean():+.2f}%(胜{(s10>0).mean()*100:.0f}%)', flush=True)
    print()
    print('[最终分分档] 5日均值:')
    for lo, hi in ((90, 101), (85, 90), (78, 85), (70, 78), (0, 70)):
        g = df[(df['final_score'] >= lo) & (df['final_score'] < hi)]
        if len(g):
            s5 = g['fut5'].dropna()
            print(f'  [{lo},{hi}): {len(g)}笔 | 5日 {s5.mean():+.2f}% 胜率{(s5>0).mean()*100:.0f}%', flush=True)
    # EARLY_BUY 门槛失败分解
    pb_g = df[df['action'] == 'WAIT_REACCELERATION']
    if len(pb_g):
        print(f'\n[EARLY_BUY 门槛诊断] WAIT_REACCELERATION {len(pb_g)} 条:')
        for col, label in (('eb_depth_ok', '深度20%~80%'), ('eb_vol_ok', '踩量/突量<=0.80'),
                           ('eb_broke_ok', '未跌破突破位'), ('eb_c5_ok', '收盘<=MA5(低吸区)'),
                           ('eb_low_ok', '低点已确认'), ('eb_nl_ok', '当日未创新低')):
            print(f'  {label}: 通过 {pb_g[col].sum()}/{len(pb_g)}')
        print(f'  全部通过: {pb_g["eb_all_ok"].sum()}/{len(pb_g)}')
    df.to_csv('output/pbp/bt_debug.csv', index=False, encoding='utf-8-sig')
    print(f'\n[调试CSV] output/pbp/bt_debug.csv', flush=True)
    g = df[df['action'].isin(('PRIMARY_BUY', 'CONFIRMED_BUY'))]
    if len(g):
        print('\nPRIMARY/CONFIRMED 明细:', flush=True)
        print(g[['ts_code','name','date','action','final_score','breakout_date','pullback_vol_ratio','fut5','fut10']].to_string(), flush=True)
    tee.close()


if __name__ == '__main__':
    main()
