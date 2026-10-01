# -*- coding: utf-8 -*-
"""HVE V1 股票池（§7 成交额过滤 / §8 股票排除）

§8 明确「如果现有系统已经有标准 universe，直接复用现有 universe，不要创建新的股票池」。
因此每日流程**直接调用** hvt_bull.daily._universe（只读），不复制逻辑。

回测路径（需要对 2022~2026 每个交易日逐日判定）不适合每日 21 次截面查询，
故提供 passes_mask()：用同一条阈值（universe 配置段）在单股时序上**向量化**判定。
两者阈值与单位换算完全一致：
  市值下限（万元） = min_market_cap(亿) × 10000
  流动性下限（万元）= min_avg_amount_20(万元)   [daily_cache.amount 单位千元 → ÷10 = 万元]

已知局限（V1）：ST 判定用 stock_basic 的**当前**名称，没有 PIT 名称表可用
（stock_data.db 内无股票名称历史），故回测中早期 ST 样本不会被剔除。
"""
import numpy as np
import pandas as pd

BJ_PREFIX = ('8', '4', '9')


def daily_universe(loader, trade_date: str, cfg: dict) -> list:
    """每日股票池：直接复用现有 universe（§8），返回 [{ts_code, name, ...}]"""
    from hvt_bull.daily import _universe
    return _universe(loader, trade_date, cfg)


def passes_mask(d: pd.DataFrame, ts_code: str, name: str,
                list_date: str, cfg: dict) -> np.ndarray:
    """回测用股票池判定（逐行向量化，阈值与 _universe 完全一致）

    返回与 d 等长的 bool 数组；不满足条件的交易日为 False。
    """
    uni = cfg.get('universe', {})
    n = len(d)
    ok = np.zeros(n, dtype=bool)
    code = str(ts_code)
    if not code.endswith(('.SH', '.SZ')):
        return ok
    if uni.get('exclude_bj', True) and code.startswith(BJ_PREFIX):
        return ok
    if uni.get('exclude_st', True) and ('ST' in str(name).upper() or '退' in str(name)):
        return ok
    if 'total_mv' not in d.columns or 'amount' not in d.columns:
        return ok

    listed = np.ones(n, dtype=bool)
    if list_date:
        try:
            ld = pd.to_datetime(str(list_date), format='%Y%m%d')
            dates = pd.to_datetime(d['trade_date'].astype(str), format='%Y%m%d')
            listed = ((dates - ld).dt.days >= int(uni.get('min_listed_days', 120))).to_numpy()
        except Exception:
            pass

    min_mv = float(uni.get('min_market_cap', 30.0)) * 10000.0
    min_amt = float(uni.get('min_avg_amount_20', 3000.0))

    mv = pd.to_numeric(d['total_mv'], errors='coerce').to_numpy(dtype=float)
    mv_w = np.where(mv > 1e6, mv / 10.0, mv)          # → 万元
    amt = pd.to_numeric(d['amount'], errors='coerce')
    avg_amt_w = amt.rolling(21, min_periods=1).mean().to_numpy(dtype=float) / 10.0

    amt_ok = np.isfinite(avg_amt_w)
    small_cap = mv_w < min_mv
    # 小盘股需成交额显著放大才保留；且任何情况下成交额不得低于下限
    keep = (np.isfinite(mv) & (~small_cap | (amt_ok & (avg_amt_w > min_amt * 3)))
            & (~amt_ok | (avg_amt_w >= min_amt)))
    return keep & listed


def basic_maps() -> tuple:
    """返回 (code→name, code→list_date) 映射（来自 stock_basic，只读）"""
    import stock_cache as sc
    sb = sc.load_stock_basic()
    if sb is None or sb.empty:
        return {}, {}
    names = dict(zip(sb['ts_code'], sb.get('name', pd.Series(dtype=str))))
    lds = dict(zip(sb['ts_code'], sb.get('list_date', pd.Series(dtype=str)))) \
        if 'list_date' in sb.columns else {}
    return names, lds
