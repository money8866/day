# -*- coding: utf-8 -*-
"""修复 volume_surge_strategy.py 信号数组 dtype bug (幂等):
signals = np.zeros(n, dtype=bool) -> dtype=np.float64
原因: signals[i] = total_score 把float评分赋给bool数组会截断为True(=1.0),
导致回测中"按评分取Top5"实际是稳定插入序(评分排序从未生效).
"""
path = r"d:\mystock\tdx_backtest\volume_surge_strategy.py"
src = open(path, encoding="utf-8").read()
old = "signals = np.zeros(n, dtype=bool)"
new = "signals = np.zeros(n, dtype=np.float64)"
if old in src:
    src = src.replace(old, new)
    open(path, "w", encoding="utf-8").write(src)
    print("PATCHED: dtype=bool -> dtype=np.float64")
elif "signals = np.zeros(n, dtype=np.float64)" in src:
    print("ALREADY PATCHED (float64)")
else:
    print("ERROR: pattern not found, check manually")
