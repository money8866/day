# -*- coding: utf-8 -*-
"""
EOD-RB 消融实验（规范 9.3）：A 仅前期强势 / B +缩量回踩 / C +尾盘再启动 / D +板块确认。
按时间切分，不做随机打乱。对比加入每个条件后净收益/胜率/回撤是否改善。
"""
from . import backtest as B
from .config import MINUTE_START, MINUTE_END


def run_ablation(cfg, exit_mode="NEXT_CLOSE"):
    """返回 dict[实验键] = {"metrics":..., "n":..., "note":...}"""
    res = {}
    split = cfg["split"]

    # 四组在同一「分钟数据窗口」内对比（同日期、同股票池、同成本、同退出）
    for key in ("A", "B", "C", "D"):
        req = cfg["ablation"][key]
        if req["tail"]:
            trades, meta = B.run_full(MINUTE_START, MINUTE_END, cfg,
                                      require_sector=req["sector"], exit_mode=exit_mode)
            note = "T日尾盘入场(分钟级)"
        else:
            trades, meta = B.run_daily(MINUTE_START, MINUTE_END, cfg,
                                       require_pullback=req["pullback"], exit_mode=exit_mode)
            note = "T+1开盘入场(日线级,无分钟数据)"
        res[f"{key}_minute"] = {
            "metrics": B.compute_metrics(trades, cfg), "n": len(trades), "note": note,
            "meta": meta,
        }

    # 日线层 A/B 在长历史上三段（训练/验证/样本外）
    for key in ("A", "B"):
        req = cfg["ablation"][key]
        for seg in ("train", "val", "oos"):
            s, e = split[f"{seg}_start"], split[f"{seg}_end"]
            trades, meta = B.run_daily(s, e, cfg, require_pullback=req["pullback"], exit_mode=exit_mode)
            res[f"{key}_{seg}"] = {
                "metrics": B.compute_metrics(trades, cfg), "n": len(trades),
                "note": "日线层", "meta": meta,
            }
    return res