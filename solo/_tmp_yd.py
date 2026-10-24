"""临时：依顿电子 603328.SH 20261022 结构诊断（T0 / 长阳 / 回踩量能 / 右底几何）。"""
import w7_second_wave_engine as W

CODE, DATE = "603328.SH", "20261022"
r = W.CacheReader()
df = r.bars_sql(CODE, DATE)
end = len(df) - 1
anc = W.find_event_anchor(df)
print(f"bars={len(df)} last={df.iloc[end].trade_date} close={float(df.iloc[end].close):.2f}")
if anc:
    ei, ep = anc
    e = df.iloc[ei]
    print(f"T0={e.trade_date} 分位={ep:.2f} 开{float(e.open):.2f} 收{float(e.close):.2f} "
          f"涨{float(e.pct_chg):+.2f}% 量{float(e.vol):.0f}，距今{end - ei}根")
else:
    print("T0=None")

print("\n最近 22 根：日期 收盘 涨幅 量 量/前20均量 长阳日量比")
v = df.vol.to_numpy(dtype=float)
for i in range(end - 21, end + 1):
    row = df.iloc[i]
    v20 = v[max(0, i - 20):i].mean()
    print(f"  {row.trade_date} {float(row.close):7.2f} {float(row.pct_chg):+7.2f}% "
          f"{float(row.vol):10.0f} {v[i]/v20:6.2f}")

ok, bar = W.shakeout_hold(df, end)
print(f"\nshakeout_hold -> {ok} bar={bar}")
ok2, info = W.right_bottom(df, end)
print(f"right_bottom -> {ok2}")
if info:
    for k, val in info.items():
        print(f"  {k}={val}")
