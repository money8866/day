# -*- coding: utf-8 -*-
"""决定性帧对比：old vs new loader 对同一股票同一窗口的返回是否逐值一致"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = r'd:\mystock\solo'
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

import numpy as np
import pandas as pd
from hvt_bull.data_loader import HvtDataLoader, _TS_COLS, _NUM_COLS

# 旧 loader 复刻（同 _hvt_run_loader.py）
class HvtDataLoaderOld(HvtDataLoader):
    def load(self, ts_code, start_date, end_date):
        key = (ts_code, start_date, end_date)
        if key in self._cache:
            return self._cache[key]
        sql = ("SELECT {cols} FROM stk_factor_pro WHERE ts_code=? "
               "AND trade_date>=? AND trade_date<=? ORDER BY trade_date"
               ).format(cols=','.join(_TS_COLS))
        df = self._read(sql, (ts_code, start_date, end_date))
        if df is None or df.empty:
            self._cache[key] = None
            return None
        df = df.dropna(subset=['close', 'vol'])
        for c in _NUM_COLS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.reset_index(drop=True)
        self._cache[key] = df
        return df

    def query_cross_section(self, trade_date, fields=('ts_code', 'turnover_rate', 'amount', 'total_mv')):
        cols = ','.join(fields)
        sql = f"SELECT {cols} FROM stk_factor_pro WHERE trade_date=?"
        df = self._read(sql, (trade_date,))
        return df if df is not None else pd.DataFrame(columns=list(fields))


old = HvtDataLoaderOld()
new = HvtDataLoader()

T0 = '20260904'
HIST = '20240801'
CODES = ['002081.SZ', '300628.SZ', '600298.SH', '002142.SZ', '688239.SH', '601319.SH', '000019.SZ']

print('=== 1) 全窗口帧逐值对比 [%s, %s] ===' % (HIST, T0))
for code in CODES:
    a = old.load(code, HIST, T0)
    b = new.load(code, HIST, T0)
    if a is None or b is None:
        print(f'{code}: 任一为 None old={a is not None} new={b is not None}')
        continue
    a2 = a.set_index('trade_date')
    b2 = b.set_index('trade_date')
    only_a = a2.index.difference(b2.index)
    only_b = b2.index.difference(a2.index)
    common = a2.index.intersection(b2.index)
    print(f'\n{code}: old_rows={len(a2)} new_rows={len(b2)} only_old={len(only_a)}{list(only_a[:4])} only_new={len(only_b)}{list(only_b[:4])}')
    mism_total = 0
    examples = []
    for col in a2.columns:
        if col in ('ts_code',):
            continue
        ca = pd.to_numeric(a2.loc[common, col], errors='coerce')
        cb = pd.to_numeric(b2.loc[common, col], errors='coerce')
        # 相对容差：1e-4
        diff = (ca - cb).abs()
        tol = ca.abs() * 1e-4 + 1e-6
        nm = int((diff > tol).sum())
        if nm:
            mism_total += nm
            # 找首个不一致位置
            bad = common[diff.values > tol.values]
            if len(examples) < 3 and len(bad):
                r0 = bad[0]
                examples.append((col, r0, ca.get(r0), cb.get(r0)))
    print(f'  共同行={len(common)} 相对容差1e-4下不一致单元格数={mism_total}')
    for ex in examples[:3]:
        print(f'    首例 {ex[0]} @ {ex[1]}: old={ex[2]} new={ex[3]}')

print()
print('=== 2) 20260904 截面字段对比（turnover_rate/amount/total_mv/vol/close）===')
flds = ('ts_code', 'turnover_rate', 'amount', 'total_mv', 'vol', 'close')
xa = old.query_cross_section(T0, fields=flds).set_index('ts_code')
xb = new.query_cross_section(T0, fields=flds).set_index('ts_code')
print(f'old_cs={len(xa)} new_cs={len(xb)}')
for code in CODES:
    if code in xa.index and code in xb.index:
        ra, rb = xa.loc[code], xb.loc[code]
        print(f'  {code}:')
        for f in flds[1:]:
            va, vb = ra.get(f), rb.get(f)
            same = (va == vb) or (pd.isna(va) and pd.isna(vb)) or (
                isinstance(va, (int, float)) and isinstance(vb, (int, float))
                and abs(va - vb) <= (abs(va) * 1e-4 + 1e-6))
            if not same:
                print(f'     {f}: old={va} new={vb}')
    elif code not in xa.index:
        print(f'  {code}: old 截面缺失!')
    else:
        print(f'  {code}: new 截面缺失!')
print('DONE')
