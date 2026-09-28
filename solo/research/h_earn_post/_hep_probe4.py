# -*- coding: utf-8 -*-
"""H-EARN-POST 侦察 4：三大表 + fin_ind 可用字段（检查 accounts_receiv / money_cap 等）"""
import os
import glob
import collections
import pandas as pd

CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')

WANT = ['accounts_receiv', 'notes_receiv', 'money_cap', 'inventories', 'goodwill',
        'total_assets', 'total_hldr_eqy_exc_min_int', 'accounts_receiv_bill',
        'ocf_to_sales', 'ocf_to_or', 'profit_dedt', 'roe', 'roe_dt',
        'grossprofit_margin', 'netprofit_margin', 'debt_to_assets', 'ocfps',
        'tr_yoy', 'or_yoy', 'netprofit_yoy', 'dt_netprofit_yoy', 'assets_yoy',
        'q_roe', 'q_dt_roe', 'q_npta', 'q_ocf_to_sales', 'ocf_yoy']

for pre in ('income', 'balance', 'cashflow', 'treasure_fin_ind'):
    files = []
    for d in (CD, PD):
        files += glob.glob(os.path.join(d, pre + '_*.parquet'))
    print('=' * 70)
    print('%s  文件=%d' % (pre, len(files)))
    # 抽样 40 个不同前缀的文件，统计列出现频率
    seen = collections.Counter()
    nf = 0
    step = max(1, len(files) // 40)
    for fp in files[::step][:40]:
        try:
            x = pd.read_parquet(fp)
        except Exception:
            continue
        nf += 1
        for c in x.columns:
            seen[c] += 1
    print('  抽样文件=%d  列数(并集)=%d' % (nf, len(seen)))
    for w in WANT:
        if w in seen:
            print('    [有] %-28s 出现于 %d/%d' % (w, seen[w], nf))
    if pre != 'income':
        miss = [w for w in WANT if w not in seen]
        print('    [无] %s' % miss)
    if pre in ('balance', 'cashflow'):
        print('    全部列 =', sorted(seen.keys()))
