# -*- coding: utf-8 -*-
"""
EOD-RB 参数稳定性（规范 10）：少量、事先定义的参数扰动，检查策略是否稳定。
禁止大规模参数搜索取历史最优。若仅单个精确参数组合盈利、邻近失效 → 稳定性不足。
由于分钟样本过短（~60交易日），尾盘层参数无法做稳定统计，这里仅对日线层(B)做扰动。
"""
import copy
from . import backtest as B


def run_stability(cfg, window=("20250102", "20251231"), exit_mode="NEXT_CLOSE"):
    s, e = window
    rows = []

    def _run(label, apply_fn):
        c = copy.deepcopy(cfg)
        if apply_fn:
            apply_fn(c)
        trades, meta = B.run_daily(s, e, c, require_pullback=True, exit_mode=exit_mode)
        m = B.compute_metrics(trades, c)
        rows.append({
            "label": label,
            "signals": m.get("signals", 0),
            "mean_net_bp": None if m.get("mean_net") is None else round(m["mean_net"] * 10000, 1),
            "win_rate": None if m.get("win_rate") is None else round(m["win_rate"], 3),
            "max_dd": None if m.get("max_drawdown") is None else round(m["max_drawdown"], 3),
        })

    _run("baseline", None)
    _run("回踩深度[1%,8%]", lambda c: c["pullback"].update(depth_min=0.01))
    _run("回踩深度[3%,10%]", lambda c: c["pullback"].update(depth_min=0.03, depth_max=0.10))
    _run("回踩窗口3日", lambda c: c["pullback"].update(lookback_window=3))
    _run("回踩窗口7日", lambda c: c["pullback"].update(lookback_window=7))
    _run("缩量比率0.70", lambda c: c["pullback"].update(shrink_ratio=0.70))
    _run("缩量比率0.90", lambda c: c["pullback"].update(shrink_ratio=0.90))
    _run("退出NEXT_OPEN", lambda c: c["exit"].update(mode="NEXT_OPEN"))
    _run("退出T2_CLOSE", lambda c: c["exit"].update(mode="T2_CLOSE"))

    return rows