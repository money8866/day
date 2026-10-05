# -*- coding: utf-8 -*-
"""CSI2000 Position Gate —— 回测 / 对照 / 成本（§22~§24）

§23 对照组：固定 100% / 50% / 30% vs Model(CSI2000 Position Gate)
§24 成本：0 / 15 / 30 / 50 bp，按 |仓位变动| 计提

口径（无未来函数）：
  第 t 日收盘生成 Gate 仓位 pos_t，实际暴露发生在 t+1 日 → 收益 = pos_{t-1} × r_t
  调仓成本在 t 日收盘计提 = |pos_t − pos_{t-1}| × cost_bp
"""
import numpy as np
import pandas as pd

from position_gate import config as C


def trim_warmup(gate: pd.DataFrame, n: int = None) -> pd.DataFrame:
    """剔除预热期（§ 前 N 个交易日宽度/RS 尚未完整，不进统计口径）"""
    n = C.WARMUP_DAYS if n is None else int(n)
    return gate.iloc[n:] if n > 0 else gate


def daily_returns(gate: pd.DataFrame, cost_bp: float = 0.0,
                  pos_col: str = 'position') -> pd.DataFrame:
    """→ DataFrame[ret_index, pos_prev, turnover, cost, ret_gross, ret_net, nav]"""
    out = pd.DataFrame(index=gate.index)
    r = gate['ret1'].astype('float64') / 100.0
    pos = gate[pos_col].astype('float64') / 100.0
    out['ret_index'] = r
    out['pos_prev'] = pos.shift(1).fillna(0.0)
    out['pos'] = pos
    out['turnover'] = pos.diff().abs().fillna(pos.abs())
    out['cost'] = out['turnover'] * (cost_bp / 10000.0)
    # 收益缺失日（首日/停牌）按 0 计，避免 NaN 把当天成本一并抹掉
    out['ret_gross'] = out['pos_prev'] * r.fillna(0.0)
    out['ret_net'] = out['ret_gross'] - out['cost']
    out['nav'] = (1 + out['ret_net'].fillna(0)).cumprod()
    return out


def const_gate(gate: pd.DataFrame, expo_pct: float) -> pd.DataFrame:
    """构造固定仓位曲线（仅 ret1 + position 两列），与 Model 同口径比较"""
    g = gate[['ret1']].copy()
    g['position'] = float(expo_pct)
    return g


def const_returns(gate: pd.DataFrame, expo_pct: float, cost_bp: float = 0.0) -> pd.DataFrame:
    return daily_returns(const_gate(gate, expo_pct), cost_bp=cost_bp)


def metrics(bt: pd.DataFrame, expo_pct_series: pd.Series = None, cost_bp: float = 0.0) -> dict:
    ret = bt['ret_net'].fillna(0.0)
    n = len(ret)
    if n == 0:
        return {}
    nav = (1 + ret).cumprod()
    years = n / C.TRADING_DAYS
    total = nav.iloc[-1] - 1
    cagr = (nav.iloc[-1]) ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else np.nan
    vol = ret.std(ddof=0) * np.sqrt(C.TRADING_DAYS)
    sharpe = (ret.mean() * C.TRADING_DAYS) / vol if vol > 0 else np.nan
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan
    # 最长回撤修复时间（交易日）
    under = (nav < nav.cummax())
    longest, cur = 0, 0
    for u in under.values:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    pos = expo_pct_series if expo_pct_series is not None else bt['pos']
    return {
        'cost_bp': cost_bp,
        'total_return_%': round(total * 100, 2),
        'CAGR_%': round(cagr * 100, 2),
        'vol_%': round(vol * 100, 2),
        'maxDD_%': round(mdd * 100, 2),
        'Calmar': round(calmar, 2) if np.isfinite(calmar) else np.nan,
        'Sharpe': round(sharpe, 2) if np.isfinite(sharpe) else np.nan,
        'longest_DD_days': int(longest),
        'flat_days_%': round(float((pos.abs() < 1e-9).mean()) * 100, 1),
        'avg_position_%': round(float(pos.mean()) * 100, 1),
        'turnover_per_year': round(float(bt['turnover'].sum() / years), 1),
    }


def compare(gate: pd.DataFrame, costs=C.COST_BPS) -> pd.DataFrame:
    """Model vs 三个固定仓位基准，按 4 档成本对比（§23/§24）"""
    rows = []
    for cb in costs:
        bt = daily_returns(gate, cost_bp=cb)
        m = metrics(bt, cost_bp=cb)
        m['model'] = 'Model(PositionGate)'
        rows.append(m)
        for name, expo in C.BENCHMARKS.items():
            b = const_returns(gate, expo, cost_bp=cb)
            mb = metrics(b, cost_bp=cb)
            mb['model'] = f'{name}({expo:.0f}%)'
            rows.append(mb)
    order = {f'Model(PositionGate)': 0, 'B100(100%)': 1, 'B50(50%)': 2, 'B30(30%)': 3}
    df = pd.DataFrame(rows)
    df['_o'] = df['model'].map(order).fillna(9)
    df = df.sort_values(['cost_bp', '_o']).drop(columns='_o').reset_index(drop=True)
    return df[['cost_bp', 'model'] + [c for c in df.columns if c not in ('cost_bp', 'model')]]


def segment_metrics(gate: pd.DataFrame, start: str, end: str,
                    costs=C.COST_BPS) -> pd.DataFrame:
    sub = gate[(gate.index >= start) & (gate.index <= end)]
    if sub.empty:
        return pd.DataFrame()
    return compare(sub, costs=costs)


def split_compare(gate: pd.DataFrame, splits: dict = None) -> pd.DataFrame:
    """按 §26 时间切分逐段对比（主口径 cost=15bp）"""
    splits = splits or C.SPLITS
    frames = []
    for name, (s, e) in splits.items():
        df = segment_metrics(gate, s, e, costs=(15,))
        if df.empty:
            continue
        df['segment'] = name
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


if __name__ == '__main__':
    import sys
    feat = pd.read_pickle('position_gate/cache/gate.pkl')
    print(compare(feat).to_string(index=False))
