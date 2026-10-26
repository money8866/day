"""临时：右底样本「T0 之后量能是否枯竭」体检（后段/前段量能比、最长连续放量根数）。"""
import numpy as np
import w7_second_wave_engine as W

SAMPLES = [("603328.SH", "20261022", "依顿电子"),
           ("300765.SZ", "20261015", "石药创新"),
           ("600657.SH", "20261015", "信达地产"),
           ("600828.SH", "20261015", "茂业商业")]
r = W.CacheReader()


def max_run(flags):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


for code, date, nm in SAMPLES:
    df = r.bars_sql(code, date)
    end = len(df) - 1
    anc = W.find_event_anchor(df)
    if not anc:
        print(f"{code} {nm}: no T0")
        continue
    ei = anc[0]
    v = df.vol.to_numpy(dtype=float)
    n = end - ei
    pre = v[max(0, ei - n):ei].sum()
    post = v[ei + 1:end + 1].sum()
    ratios = [v[i] / v[max(0, i - 20):i].mean() for i in range(ei + 1, end + 1)]
    print(f"{code} {nm}: T0={df.iloc[ei].trade_date} 后{n}根 后段/前段量能={post/pre:.2f} "
          f"量比≥1.2根数={sum(1 for x in ratios if x >= 1.2)} 占比={sum(1 for x in ratios if x >= 1.2)/max(n,1)*100:.0f}% "
          f"最长连续≥1.2={max_run([x >= 1.2 for x in ratios])} 平均量比={np.mean(ratios):.2f}")
