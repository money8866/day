"""临时：右底低吸候选的「回踩段持续放量」体检（H0→右底）。"""
import numpy as np
import w7_second_wave_engine as W

SAMPLES = [("603328.SH", "20261022", "依顿电子"),
           ("301080.SZ", "20260911", "百普赛斯(设计样本)"),
           ("300765.SZ", "20261015", "石药创新"),
           ("600657.SH", "20261015", "信达地产"),
           ("600828.SH", "20261015", "茂业商业")]
r = W.CacheReader()
for code, date, nm in SAMPLES:
    df = r.bars_sql(code, date)
    end = len(df) - 1
    ok, info = W.right_bottom(df, end)
    if not ok:
        print(f"{code} {nm} {date}: right_bottom=False")
        continue
    h0 = int(df.index[df.trade_date.astype(str) == info["rb_h0_date"]][0])
    seg = df.iloc[h0 + 1:end + 1]
    v = df.vol.to_numpy(dtype=float)
    vr = []
    for i in range(h0 + 1, end + 1):
        v20 = v[max(0, i - 20):i].mean()
        vr.append(v[i] / v20 if v20 > 0 else 0.0)
    vr = np.array(vr)
    print(f"{code} {nm} {date}: 回踩跨度={end - h0}根 平均量比={vr.mean():.2f} "
          f"≥1.2根数={(vr >= 1.2).sum()} ≥1.5根数={(vr >= 1.5).sum()} "
          f"最大量比={vr.max():.2f} 右底当日量比={info['rb_volr20']:.2f} 回撤={info['rb_retrace']:.2f}")
