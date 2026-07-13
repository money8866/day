# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

stocks = [
    ('SEM_ETF(159516)', 0.91, 0.874),
    ('CMBC(600036)', 36.88, 37.320),
    ('HengRui(600276)', 55.75, 56.010),
    ('InnoPharm_ETF(159992)', 0.85, 0.848),
    ('ChuangShiJi(300083)', 13.31, 11.590),
    ('YingXin(000620)', 3.63, 3.350),
    ('XiangXin(002965)', 51.41, 49.530),
]

idx = [
    ('ShangZheng', 3996.16, 3925.63, 3902.61, 3983.05),
    ('ShenZheng', 15046.67, 14610.02, 14496.20, 14997.07),
    ('ChuangYe', 3842.73, 3752.62, 3708.94, 3855.75),
    ('HS300', 4780.79, 4713.00, 4676.64, 4775.24),
]

sh_now = 3925.63
sh_am = -1.54

print("=== 14:00 MARKET ANALYSIS ===\n")
print("--- INDEX (14:00) ---")
for name, y, now, low, high in idx:
    pct = (now - y) / y * 100
    amp = (high - low) / y * 100
    pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
    amp_str = '%.2f' % amp
    vs_sh = pct - (sh_now - 3996.16) / 3996.16 * 100
    vs_str = '+%.1f' % vs_sh if vs_sh >= 0 else '%.1f'
    print("%-12s  %+.2f%%  amp=%s%%  vsSH=%s  L=%.2f  H=%.2f" % (
        name, pct, amp_str, vs_str, low, now))

print("\n--- STOCKS (14:00) ---")
sh_pct_now = (sh_now - 3996.16) / 3996.16 * 100
for name, y, now in stocks:
    pct = (now - y) / y * 100
    vs_sh = pct - sh_pct_now
    pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
    vs_str = '+%.1f' % vs_sh if vs_sh >= 0 else '%.1f'
    print("%-20s  %s%%  vsSH=%s" % (name, pct_str, vs_str))

print("\n--- AM vs PM ---")
for name, y, now, low, high in idx:
    am = {'ShangZheng': -1.54, 'ShenZheng': -2.61, 'ChuangYe': -2.38, 'HS300': -1.34}.get(name, 0)
    pm = (now - y) / y * 100
    diff = pm - am
    d_str = '+%.2f' % diff if diff >= 0 else '%.2f'
    if diff < -0.15: lbl = 'ACCELERATING'
    elif diff > 0.15: lbl = 'RECOVERING'
    else: lbl = 'STABLE'
    print("  %-12s  AM=%+5.2f -> PM=%+6.2f  delta=%s  [%s]" % (name, am, pm, d_str, lbl))
