# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  申万行业成员（PIT）取数  —— P1 前置

为什么必须另取：
    cache_daily/industry/sw_industry_map.csv 仅 3000 只（约 51.6% 代码），
    且 in_date / out_date 全为空 —— 无 PIT 区间，G2（SW L1 PIT 覆盖率 >= 0.90）必然 FAIL。
    Tushare index_member_all 提供 5914 只当前成员 + 2006 条历史变更（带 out_date），
    可重建 PIT 行业链。

§5 纪律：不重复下载已存在数据 —— 本文件存在即跳过（--force 才重取）。
隔离纪律（§0）：产物落在课题本地 data/，不写入共享 cache_daily。

用法
    python -u hid_fetch_sw.py            # 已有则跳过
    python -u hid_fetch_sw.py --force    # 强制重取
"""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FP = os.path.join(HERE, 'data', 'sw_member_all.csv')
ENV = r'D:\mystock\config\.env'

COLS = ['l1_code', 'l1_name', 'l2_code', 'l2_name', 'l3_code', 'l3_name',
        'ts_code', 'name', 'in_date', 'out_date', 'is_new']


def token():
    if os.path.exists(ENV):
        for line in open(ENV, encoding='utf-8'):
            line = line.strip()
            if line.startswith('TUSHARE_TOKEN='):
                return line.split('=', 1)[1].strip()
    return os.environ.get('TUSHARE_TOKEN', '')


def main():
    force = '--force' in sys.argv
    if os.path.exists(FP) and not force:
        d = pd.read_csv(FP, dtype=str)
        print('[SKIP] 已存在 %s（%d 行 / %d 代码）。--force 可重取。'
              % (FP, len(d), d['ts_code'].nunique()))
        return
    import tushare as ts
    pro = ts.pro_api(token())
    frames = []
    cur = pro.index_member_all()
    print('[FETCH] 当前成员 %d 行 / %d 代码' % (len(cur), cur['ts_code'].nunique()))
    frames.append(cur)
    his = pro.index_member_all(is_new='N')
    print('[FETCH] 历史变更 %d 行 / %d 代码（out_date 有效 %d）'
          % (len(his), his['ts_code'].nunique(), int(his['out_date'].notna().sum())))
    frames.append(his)
    d = pd.concat(frames, ignore_index=True)[COLS]
    d = d.drop_duplicates()
    os.makedirs(os.path.dirname(FP), exist_ok=True)
    d.to_csv(FP, index=False, encoding='utf-8-sig')
    print('[SAVE] %s  合计 %d 行 / %d 代码；L1 类 %d，L2 类 %d'
          % (FP, len(d), d['ts_code'].nunique(), d['l1_name'].nunique(),
             d['l2_name'].nunique()))
    print('       区间缺失：in_date %d 空 / out_date %d 空'
          % (int(d['in_date'].isna().sum()), int(d['out_date'].isna().sum())))


if __name__ == '__main__':
    main()
