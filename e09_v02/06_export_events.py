# -*- coding: utf-8 -*-
"""E09-V02 Step 6：导出 e09_v02_events.csv（主事件集的可读子集）
完整列（621 列）保留在 data/events_e09.parquet
"""
import os
import numpy as np
import pandas as pd
import e09_lib as E

KEEP = ['ts_code', 'trade_date', 'td_idx', 'year', 'segment', 'regime', 'size_lbl',
        'pct_chg', 'vol_ma20r', 'vr20', 'amt_r20i', 'amt20i',
        'mom5', 'mom10', 'mom20', 'ma20_dist', 'ma60_dist',
        'body_pct', 'upper_shadow_pct', 'lower_shadow_pct', 'close_position',
        'atr14', 'range_pct', 'ret3_prior', 'ret5_prior', 'ret10_prior',
        'meanvr_2', 'meanvr_3', 'meanvr_4',
        'minlow_2', 'minlow_3', 'minlow_4', 'maxdd_close_3', 'maxdd_high_3',
        'brk_ma20_3', 'brk_Slow_3', 'brk_Smid_3', 'brk_Sclose_3',
        'vol_decay_slope_3', 'range_mean_3', 'range_ratio_3',
        'vr1_of_S', 'vr2_of_S', 'vr3_of_S', 'vr4_of_S',
        'dB_A_main3', 'dB_B_main3', 'dB_C_main3',
        'brvol_A_main3', 'brvol_B_main3', 'brvol_C_main3',
        'B_gap_B_main3', 'B_limitup_B_main3',
        'S_ret_1', 'S_ret_3', 'S_ret_5', 'S_ret_10', 'S_ret_20',
        'B_ret_1_A_main3', 'B_ret_3_A_main3', 'B_ret_5_A_main3', 'B_ret_10_A_main3',
        'B_ret_1_B_main3', 'B_ret_3_B_main3', 'B_ret_5_B_main3', 'B_ret_10_B_main3',
        'B_ret_1_C_main3', 'B_ret_3_C_main3', 'B_ret_5_C_main3', 'B_ret_10_C_main3',
        'B_mfe_5_B_main3', 'B_mae_5_B_main3',
        'gap_prev']


def main():
    ev = pd.read_parquet(os.path.join(E.DATA, 'events_e09.parquet'))
    cols = [c for c in KEEP if c in ev.columns]
    miss = [c for c in KEEP if c not in ev.columns]
    if miss:
        print('缺失列：', miss)
    out = ev[cols].copy()
    for c in out.columns:
        if out[c].dtype.kind == 'f':
            out[c] = out[c].astype(np.float32).round(6)
    out.to_csv(os.path.join(E.OUT, 'e09_v02_events.csv'), index=False, encoding='utf-8-sig')
    print(f'e09_v02_events.csv: {len(out):,} 行 × {out.shape[1]} 列')
    print('大小 MB: %.1f' % (os.path.getsize(os.path.join(E.OUT, 'e09_v02_events.csv')) / 1e6))


if __name__ == '__main__':
    main()
