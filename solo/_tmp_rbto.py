"""临时：右底样本「T0 后/前高后的累计换手与放量持续度」体检。"""
import numpy as np
import w7_second_wave_engine as W

SAMPLES = [("603328.SH", "20261022", "依顿电子"),
           ("300765.SZ", "20261015", "石药创新"),
           ("600657.SH", "20261015", "信达地产"),
           ("600828.SH", "20261015", "茂业商业"),
           ("301080.SZ", "20260911", "百普赛斯")]
r = W.CacheReader()
for code, date, nm in SAMPLES:
    df = r.bars_sql(code, date)
    end = len(df) - 1
    anc = W.find_event_anchor(df)
    ok, info = W.right_bottom(df, end)
    if not ok:
        print(f"{code} {nm}: RB=False")
        continue
    h0 = int(df.index[df.trade_date.astype(str) == info["rb_h0_date"]][0])
    to = df.iloc[h0 + 1:end + 1]
    tr = to.turnover_rate_f.fillna(to.turnover_rate).to_numpy(dtype=float)
    tr = tr[np.isfinite(tr)]
    cum = tr.sum()
    # T0 起算
    if anc:
        ei = anc[0]
        t0seg = df.iloc[ei:end + 1]
        tr0 = t0seg.turnover_rate_f.fillna(t0seg.turnover_rate).to_numpy(dtype=float)
        tr0 = tr0[np.isfinite(tr0)]
        cum0 = tr0.sum()
        t0info = f"T0={df.iloc[ei].trade_date}({end-ei}根) 累计换手={cum0:.1f}%"
    else:
        t0info = "T0=None"
    print(f"{code} {nm}: H0→右底={end-h0}根 累计换手={cum:.1f}% | {t0info} "
          f"| 前波涨幅={info['rb_prior_rise']*100:.0f}%")
