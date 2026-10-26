# -*- coding: utf-8 -*-
"""极米科技 20260825~20261023 K线明细 + 缩量十字星诊断（临时脚本，用完删除）"""
import sys

sys.path.insert(0, r"d:\mystock\solo")
import w7_second_wave_engine as W

CODE = "688696.SH"
reader = W.CacheReader()
df = reader.bars_sql(CODE, "20261023")
df = df.reset_index(drop=True)
i_end = len(df) - 1
start = max(0, i_end - 34)

print(f"{'日期':<10}{'开':>7}{'高':>7}{'低':>7}{'收':>7}{'涨幅%':>8}{'量':>12}{'量比':>7}{'实体%':>7}{'上影%':>7}{'下影%':>7}{'MA10':>8}{'MA20':>8}{'MA60':>8}")
for i in range(start, i_end + 1):
    r = df.iloc[i]
    o, h, l, c = float(r.open), float(r.high), float(r.low), float(r.close)
    vol = float(r.vol)
    m20 = W.safe_mean(df.vol.iloc[max(0, i - 20):i])
    volr = vol / m20 if m20 > 0 else 0
    rng = h - l
    body = abs(c - o) / rng * 100 if rng > 0 else 0
    up = (h - max(o, c)) / rng * 100 if rng > 0 else 0
    dn = (min(o, c) - l) / rng * 100 if rng > 0 else 0
    print(f"{str(r.trade_date):<10}{o:>7.2f}{h:>7.2f}{l:>7.2f}{c:>7.2f}{float(r.pct_chg):>8.2f}{vol:>12.0f}{volr:>7.2f}"
          f"{body:>7.1f}{up:>7.1f}{dn:>7.1f}{float(r.ma_bfq_10):>8.2f}{float(r.ma_bfq_20):>8.2f}{float(r.ma_bfq_60):>8.2f}")

print("\n" + "=" * 100)
print("20260929 前后关键量能统计：")
idx = {str(df.iloc[i].trade_date): i for i in range(len(df))}
t = idx.get("20260929")
if t is not None:
    for label, a, b in [("前5日均量", t - 5, t), ("前10日均量", t - 10, t), ("前20日均量", t - 20, t), ("前60日均量", t - 60, t)]:
        print(f"  {label} = {W.safe_mean(df.vol.iloc[max(0,a):b]):.0f}")
    print(f"  09/29 当日量 = {float(df.iloc[t].vol):.0f}")
    print(f"  近60日最低量 = {float(df.vol.iloc[max(0,t-60):t+1].min()):.0f}（出现于 "
          f"{df.iloc[max(0,t-60)+int(df.vol.iloc[max(0,t-60):t+1].values.argmin())].trade_date}）")
    print(f"  09/29 量 / 60日最低量 = {float(df.iloc[t].vol)/float(df.vol.iloc[max(0,t-60):t+1].min()):.2f}")
    # 天量锚
    print(f"  T0 = 20260901, 其量 = {float(df.iloc[idx['20260901']].vol):.0f}，09/29量/T0量 = {float(df.iloc[t].vol)/float(df.iloc[idx['20260901']].vol)*100:.1f}%")
    print(f"  09/29 距T0 = {t - idx['20260901']} 根")
reader.close()