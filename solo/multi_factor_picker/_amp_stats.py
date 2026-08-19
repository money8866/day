# -*- coding: utf-8 -*-
"""中报公告涨停样本: 振幅/次日量能方向 与 后续收益 的实证统计
数据源: 本地通达信 C:\\new_tdx\\vipdoc 全历史日线(32字节/条)
分组(主口径 valve_ok=True, 公告间隔≤2):
  A 涨停日振幅(high-low)/pre_close 分桶
  B 次日量能×方向四组合(放量阴/缩量阴/放量阳/缩量阳)
  C 次日振幅分桶
  D 次日收盘 守住涨停价 vs 回落
"""
import os
import struct
import pandas as pd
import numpy as np

TDX_PATH = r'C:\new_tdx\vipdoc'
REPORT_DIR = r'D:\mystock\solo\report_daily'


def read_tdx_day(ts_code):
    sym, mkt = ts_code.split('.')
    fp = os.path.join(TDX_PATH, 'sh' if mkt == 'SH' else 'sz', 'lday',
                      f"{'sh' if mkt == 'SH' else 'sz'}{sym}.day")
    if not os.path.exists(fp):
        return None
    rows = []
    with open(fp, 'rb') as f:
        data = f.read()
    for i in range(0, len(data), 32):
        (d, o, h, l, c, amt, vol, _rsv) = struct.unpack('<iiiiifii', data[i:i + 32])
        rows.append((str(d), o / 100.0, h / 100.0, l / 100.0, c / 100.0, vol / 100.0))
    df = pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'vol'])
    return df.sort_values('trade_date').reset_index(drop=True)


def main():
    df = pd.read_csv(os.path.join(REPORT_DIR, 'post_announce_limitup_samples.csv'), dtype={'ts_code': str, 'trade_date': str})
    df = df[df['收盘'].notna()].copy()
    rows = []
    for _, r in df.iterrows():
        tdx = read_tdx_day(r['ts_code'])
        if tdx is None:
            continue
        m = tdx[tdx['trade_date'] == r['trade_date']]
        if len(m) == 0:
            continue
        i0 = m.index[0]
        if i0 == 0:
            continue
        pre_close = tdx.loc[i0 - 1, 'close']
        row = {
            'name': r['name'], 'valve_ok': r['valve_ok'], 'dir': r['dir'],
            'limit_times': r['limit_times'],
            'amp': (tdx.loc[i0, 'high'] - tdx.loc[i0, 'low']) / pre_close,  # 涨停日振幅
        }
        if i0 + 1 < len(tdx):
            n = tdx.loc[i0 + 1]
            b = tdx.loc[i0]
            row['n_amp'] = (n['high'] - n['low']) / b['close']      # 次日振幅
            row['n_dir'] = '阳' if n['close'] >= n['open'] else '阴'
            row['n_vr'] = n['vol'] / b['vol'] if b['vol'] else np.nan  # 次日量/涨停量
            row['n_hold'] = '守住' if n['close'] >= b['close'] else '回落'  # 次日收盘vs涨停价
        for h in (5, 10, 20):
            row[f'追涨+{h}日'] = r.get(f'追涨+{h}日')
            row[f'峰值+{h}日'] = r.get(f'峰值+{h}日')
        rows.append(row)
    out = pd.DataFrame(rows)
    # 量能方向四组合
    def combo(r):
        if pd.isna(r.get('n_vr')):
            return None
        if r['n_vr'] >= 1.3 and r['n_dir'] == '阴':
            return '放量阴(出货)'
        if r['n_vr'] < 1.3 and r['n_dir'] == '阴':
            return '缩量阴(回踩)'
        if r['n_vr'] >= 1.3 and r['n_dir'] == '阳':
            return '放量阳(续攻)'
        return '缩量阳(弱攻)'
    out['combo'] = out.apply(combo, axis=1)
    out.to_csv(os.path.join(REPORT_DIR, 'pal_amp_stats.csv'), index=False, encoding='utf-8-sig')

    # ---------- 统计 ----------
    def stat(sub, name, cols=None):
        sub = sub.dropna(subset=['追涨+10日'])
        if len(sub) == 0:
            print(f'[{name}] 无样本'); return
        print(f'===== {name}  n={len(sub)} =====')
        for h in (5, 10, 20):
            s = sub[f'追涨+{h}日'].dropna()
            if len(s) == 0:
                continue
            win = (s > 0).mean() * 100
            pk = sub[f'峰值+{h}日'].dropna().mean()
            print(f'  +{h}日 均值{s.mean():+.2f}%  胜率{win:.0f}%  峰{pk:+.2f}%')
        print('')

    main_sub = out[out['valve_ok']].copy()  # 通过公告间隔阀门的主口径
    print(f'本地通达信可解析: {len(out)}/{len(df)} (主口径 {len(main_sub)})')

    # A 涨停日振幅分桶
    for lo, hi, lab in [(0, 0.08, '≤8%'), (0.08, 0.12, '8~12%'), (0.12, 0.16, '12~16%'), (0.16, 1, '>16%')]:
        stat(main_sub[(main_sub['amp'] >= lo) & (main_sub['amp'] < hi)], f'A 涨停日振幅{lab}')
    # 10cm vs 20cm 振幅分布
    ten = main_sub[main_sub['limit_times'] == 1]
    print(f'涨停日振幅中位数: 全样本{main_sub["amp"].median()*100:.1f}%  (20cm票样本n={(main_sub["name"].str.startswith(("3","68"))).sum() if False else ""})')
    print('')

    # B 次日量能×方向四组合
    for c in ['放量阴(出货)', '缩量阴(回踩)', '放量阳(续攻)', '缩量阳(弱攻)']:
        stat(main_sub[main_sub['combo'] == c], f'B {c}')

    # C 次日振幅分桶
    for lo, hi, lab in [(0, 0.06, '≤6%'), (0.06, 0.10, '6~10%'), (0.10, 0.15, '10~15%'), (0.15, 1, '>15%')]:
        stat(main_sub[(main_sub['n_amp'] >= lo) & (main_sub['n_amp'] < hi)], f'C 次日振幅{lab}')

    # D 次日收盘 守住涨停价 vs 回落
    for v in ['守住', '回落']:
        stat(main_sub[main_sub['n_hold'] == v], f'D 次日{v}涨停价')

    # E 涨停日振幅甜区识别(仅首板)
    first = main_sub[main_sub['limit_times'] == 1]
    print(f'== E 首板 n={len(first)} 涨停日振幅中位 {first["amp"].median()*100:.1f}% ==')
    q = first['amp'].quantile([0.33, 0.66]).tolist()
    stat(first[first['amp'] <= q[0]], f'E1 振幅低三分位 ≤{q[0]*100:.0f}%')
    stat(first[(first['amp'] > q[0]) & (first['amp'] <= q[1])], f'E2 振幅中三分位 {q[0]*100:.0f}~{q[1]*100:.0f}%')
    stat(first[first['amp'] > q[1]], f'E3 振幅高三分位 >{q[1]*100:.0f}%')


if __name__ == '__main__':
    main()
