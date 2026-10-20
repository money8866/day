# -*- coding: utf-8 -*-
"""临时：小熊电器 002959.SZ 全面数据画像"""
import sys, sqlite3
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:\mystock\solo')
import volume_surge_select as V

CODE = '002959.SZ'
con = sqlite3.connect(r'd:\mystock\cache_daily\stock_data.db')
g = pd.read_sql("SELECT trade_date, open, high, low, close, vol, amount, pct_chg, pre_close "
                "FROM daily_cache WHERE ts_code=? ORDER BY trade_date", con, params=(CODE,))
con.close()
g['trade_date'] = g['trade_date'].astype(str)
print(f"=== {CODE} 数据 {len(g)} 行，区间 {g['trade_date'].iloc[0]} ~ {g['trade_date'].iloc[-1]} ===")

d = g.tail(45).copy()
d['ma5'] = g['close'].rolling(5).mean().tail(45)
d['ma10'] = g['close'].rolling(10).mean().tail(45)
d['ma20'] = g['close'].rolling(20).mean().tail(45)
d['ma60'] = g['close'].rolling(60).mean().tail(45)
d['vma5'] = g['vol'].rolling(5).mean().tail(45)
d['vma20'] = g['vol'].rolling(20).mean().tail(45)
d['volr'] = d['vol'] / d['vma20']
d['d5'] = (d['close'] / d['ma5'] - 1) * 100
d['d20'] = (d['close'] / d['ma20'] - 1) * 100
d['amp'] = (d['high'] - d['low']) / d['pre_close'] * 100

print("\n=== 近 25 个交易日 ===")
show = d.tail(25)
print(f"{'日期':<10}{'收盘':>8}{'涨幅':>8}{'量(万手)':>10}{'量比':>7}{'距MA5':>8}{'距MA20':>8}{'振幅':>7}{'MA20':>8}")
for _, r in show.iterrows():
    print(f"{r['trade_date'][4:]:<10}{r['close']:>8.2f}{r['pct_chg']:>+8.2f}%{r['vol']/1e4:>10.1f}"
          f"{r['volr']:>7.2f}{r['d5']:>+8.1f}%{r['d20']:>+8.1f}%{r['amp']:>6.1f}%{r['ma20']:>8.2f}")

# 关键位置
last = g.iloc[-1]
c = float(last['close'])
print(f"\n=== 关键位置（收盘 {c:.2f}，{last['trade_date']}）===")
for n in (5, 10, 20, 60, 120, 250):
    ma = float(g['close'].rolling(n).mean().iloc[-1])
    print(f"  MA{n:<4} = {ma:>8.2f}   距现价 {(c/ma-1)*100:>+7.2f}%")

for n in (20, 60, 120, 250):
    hi = float(g['high'].tail(n).max()); lo = float(g['low'].tail(n).min())
    print(f"  {n}日高/低 = {hi:.2f} / {lo:.2f}   距高点 {(c/hi-1)*100:+.2f}%  距低点 {(c/lo-1)*100:+.2f}%")

# 量能结构
v = g['vol'].values.astype(float)
c_arr = g['close'].values.astype(float)
o_arr = g['open'].values.astype(float)
p_arr = g['pre_close'].values.astype(float)
print(f"\n=== 量能结构 ===")
print(f"  近5日均量 = {np.mean(v[-5:]):>10.0f}")
print(f"  近20日均量 = {np.mean(v[-20:]):>10.0f}   近5日/20日 = {np.mean(v[-5:])/np.mean(v[-20:]):.2f}")
print(f"  近60日均量 = {np.mean(v[-60:]):>10.0f}   近20日/60日 = {np.mean(v[-20:])/np.mean(v[-60:]):.2f}")
print(f"  近250日最大量 = {np.max(v[-250:]):>10.0f}   最新量/历史峰 = {v[-1]/np.max(v[-250:])*100:.0f}%")
print(f"  放量日(量比>2)近60日 = {int(np.sum((v[-60:]/pd.Series(v).rolling(20).mean().values[-60:])>2))} 天")
print(f"  今日量比 = {v[-1]/np.mean(v[-20:]):.2f}")

# T0 / VSW
res = V.detect_volume_surge_swing(CODE, '小熊电器')
print(f"\n=== VSW 检测结果 ===")
if res is None:
    print("  ❌ 未入池（被硬门槛剔除）")
    # 逐门诊断
    recent = g.tail(200)
    vv = recent['vol'].values.astype(float)
    vm20 = pd.Series(vv).rolling(20, min_periods=1).mean().values
    vr = vv / np.maximum(vm20, 1)
    hi = recent['high'].values.astype(float); lo = recent['low'].values.astype(float)
    pc = recent['pre_close'].values.astype(float)
    amp = (hi - lo) / np.maximum(pc, 0.01) * 100
    print(f"     量比峰值={np.nanmax(vr):.2f}(需≥2.6)  量比>2天数={int(np.sum(vr>2))}(需≥5)")
    print(f"     200日振幅={(hi.max()/lo.min()-1)*100:.1f}%(需≥35)  近历史峰={vv.max()/np.max(v)*100:.0f}%(需≥50)")
    _r10 = float(np.mean(vv[-10:])); _b = max(float(np.mean(vv[-40:-10])), 1)
    print(f"     近10日/前30日均量={_r10/_b:.2f}(需≥1.1)")
    zt = 19.5 if CODE.split('.')[0].startswith(('300','301','688')) else 9.8
    st = V._vol_step_days(vv, recent['close'].values.astype(float),
                          recent['open'].values.astype(float), pc, zt)
    print(f"     T0识别(严格)={'成功' if st else '失败'}")
    if st is None:
        st = V._vol_step_days(vv, recent['close'].values.astype(float),
                              recent['open'].values.astype(float), pc, zt, allow_zt=True)
        print(f"     T0识别(涨停兜底)={'成功' if st else '失败'}")
    if st:
        t0i = len(vv)-1-st[0]
        print(f"     T0={recent['trade_date'].iloc[t0i]}  距今{len(vv)-1-t0i}日")
        b20 = float(np.mean(vv[t0i-20:t0i])); b60 = float(np.mean(vv[t0i-60:t0i]))
        print(f"     量能活跃度 累计×20={np.mean(vv[t0i:])/b20:.2f} 累计×60={np.mean(vv[t0i:])/b60:.2f} "
              f"5日×20={np.mean(vv[-5:])/b20:.2f} 5日×60={np.mean(vv[-5:])/b60:.2f}")
        print(f"     放量后日均振幅={np.mean(amp[t0i:]):.2f}%(需≥4.5)")
else:
    for k, val in res.items():
        print(f"  {k} = {val}")
