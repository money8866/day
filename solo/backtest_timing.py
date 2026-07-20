"""
择时信号回测框架 — 参数优化 + 信号质量分析

读取已保存的日线缓存数据，对 412 只合格标的进行历史信号回溯，
评估各信号因子的预测能力，找到最优参数组合。

用法:
    python backtest_timing.py                              # 默认参数扫描
    python backtest_timing.py --quick                       # 快速模式（少参数组合）
    python backtest_timing.py --date 20260717              # 指定交易日
"""

import os, sys, argparse, json, logging, time
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# ── 技术指标计算（同 daily_timing.py） ──
def _ma(s, n):
    if len(s) < n: return float(s.iloc[-1]) if len(s) > 0 else 0.0
    return float(s.tail(n).mean())

def _vr(vol):
    if len(vol) < 2: return 1.0
    return float(vol.iloc[-1]) / max(float(vol.tail(min(6, len(vol))).iloc[:-1].mean()), 1)

def _macd(c):
    if len(c) < 26: return 0, 0, 0
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    d = e12 - e26
    de = d.ewm(span=9, adjust=False).mean()
    return float(d.iloc[-1]), float(de.iloc[-1]), float((d - de).iloc[-1])

def _rsi(c, n=14):
    if len(c) < n+1: return 50.0
    d = c.diff()
    g = d.clip(lower=0); l = (-d.clip(upper=0))
    return 100 - 100 / (1 + max(float(g.tail(n+1).mean()), 0.001) / max(float(l.tail(n+1).mean()), 0.001))

def _kdj(h, l, c, n=9):
    if len(c) < n: return 50, 50, 50
    hn, ln = float(h.tail(n).max()), float(l.tail(n).min())
    if hn == ln: return 50, 50, 50
    rsv = (float(c.iloc[-1]) - ln) / (hn - ln) * 100
    k = 2/3*50 + 1/3*rsv; d = 2/3*50 + 1/3*k; j = 3*k - 2*d
    return k, d, j


def compute_scores(df: pd.DataFrame, params: dict = None, index_pct: float = None) -> dict:
    """
    计算择时三信号分（使用可调参数）
    返回 {trend_score, dip_score, breakout_score, composite_score, signal_type, signal_level}
    index_pct: 当日大盘涨跌幅（用于相对强弱计算）
    """
    p = params or {}
    TREND_MA_STRONG = p.get('trend_ma_strong', 25)
    TREND_MA_WEAK = p.get('trend_ma_weak', 12)
    TREND_MACD_STRONG = p.get('trend_macd_strong', 20)
    TREND_MACD_WEAK = p.get('trend_macd_weak', 10)
    TREND_MACD_BOTTOM = p.get('trend_macd_bottom', 15)
    TREND_MA_BREAK = p.get('trend_ma_break', 15)
    TREND_REL_STRONG = p.get('trend_rel_strong', 15)
    TREND_REL_MID = p.get('trend_rel_mid', 8)
    TREND_VOL_CONT = p.get('trend_vol_cont', 10)
    TREND_ABOVE_MA = p.get('trend_above_ma', 10)
    TREND_VOL_STRONG = p.get('trend_vol_strong', 20)
    TREND_VOL_WEAK = p.get('trend_vol_weak', 10)
    TREND_CHG20 = p.get('trend_chg20', 15)
    TREND_KDJ = p.get('trend_kdj', 15)
    DIP_MA10 = p.get('dip_ma10', 20)
    DIP_MA20 = p.get('dip_ma20', 15)
    DIP_MA20_WEAK = p.get('dip_ma20_weak', 8)
    DIP_VOL_LOW = p.get('dip_vol_low', 20)
    DIP_VOL_MID = p.get('dip_vol_mid', 8)
    DIP_SHADOW = p.get('dip_shadow', 15)
    DIP_RSI_OVER = p.get('dip_rsi_over', 25)
    DIP_RSI_LOW = p.get('dip_rsi_low', 15)
    DIP_RSI_MID = p.get('dip_rsi_mid', 5)
    DIP_DD = p.get('dip_dd', 10)
    BRK_VOL_STRONG = p.get('brk_vol_strong', 30)
    BRK_VOL_MID = p.get('brk_vol_mid', 15)
    BRK_HIGH20 = p.get('brk_high20', 20)
    BRK_GOLDEN = p.get('brk_golden', 15)
    BRK_BOX = p.get('brk_box', 15)
    BRK_ZT = p.get('brk_zt', 10)
    THRESHOLD_STRONG = p.get('threshold_strong', 80)
    THRESHOLD_MED = p.get('threshold_med', 60)
    THRESHOLD_LOW = p.get('threshold_low', 40)
    DIP_MA10_PCT = p.get('dip_ma10_pct', 3.0)
    DIP_MA20_PCT = p.get('dip_ma20_pct', 5.0)
    DIP_MA20_WEAK_PCT = p.get('dip_ma20_weak_pct', 8.0)
    DIP_VOL_LOW_PCT = p.get('dip_vol_low_pct', 0.8)
    DIP_VOL_MID_PCT = p.get('dip_vol_mid_pct', 1.0)
    DIP_RSI_OVER_VAL = p.get('dip_rsi_over_val', 35)
    DIP_RSI_LOW_VAL = p.get('dip_rsi_low_val', 45)
    DIP_RSI_MID_VAL = p.get('dip_rsi_mid_val', 55)
    DIP_DD_VAL = p.get('dip_dd_val', 10.0)
    BRK_VOL_STRONG_CHG = p.get('brk_vol_strong_chg', 3.0)
    BRK_VOL_STRONG_RATIO = p.get('brk_vol_strong_ratio', 1.5)
    BRK_VOL_MID_CHG = p.get('brk_vol_mid_chg', 2.0)
    BRK_VOL_MID_RATIO = p.get('brk_vol_mid_ratio', 1.3)
    BRK_BOX_AMP = p.get('brk_box_amp', 15.0)

    if df is None or len(df) < 30:
        return {'trend_score': 0, 'dip_score': 0, 'breakout_score': 0,
                'composite_score': 0, 'signal_type': 'none', 'signal_level': 'none'}

    df = df.sort_values('trade_date').reset_index(drop=True)
    c, v, h, l = df['close'], df['vol'], df['high'], df['low']
    last = df.iloc[-1]
    cur = float(c.iloc[-1])
    pct = float(last.get('pct_chg', 0) or 0)

    # compute indicators
    ma5, ma10, ma20, ma60 = _ma(c, 5), _ma(c, 10), _ma(c, 20), _ma(c, 60)
    vr_val = _vr(v)
    dif, dea, bar = _macd(c)
    rsi = _rsi(c)
    k, d, j = _kdj(h, l, c)
    dd = (float(c.tail(60).max()) - cur) / max(float(c.tail(60).max()), 1) * 100 if len(c) >= 2 else 0
    shadow = (min(float(last['open']), cur) - float(last['low'])) / max(float(last['high'] - last['low']), 0.01)
    chg20 = ((cur / float(c.iloc[-min(21, len(c))])) - 1) * 100 if len(c) >= 21 else 0
    amp20 = (float(h.tail(20).max()) - float(l.tail(20).min())) / cur * 100 if len(c) >= 20 else 0
    high20 = float(h.tail(20).max())
    zt60 = int((df.tail(60)['pct_chg'].fillna(0) >= 9.5).sum()) if 'pct_chg' in df.columns else 0

    signals = []

    # ── Trend ──
    ts = 0
    if ma5 > ma10 > ma20: ts += TREND_MA_STRONG; signals.append("MA多头")
    elif ma5 > ma20: ts += TREND_MA_WEAK; signals.append("MA偏多")
    # 股价站上MA10/MA20独立加分（需配合放量或涨幅）
    if cur > ma20 and (vr_val >= 1.2 or abs(pct) >= 2):
        ts += TREND_ABOVE_MA
        signals.append("站上MA20")
    if cur > ma10 and (vr_val >= 1.2 or abs(pct) >= 2):
        ts += max(TREND_ABOVE_MA - 3, 5)
    if cur > ma20 and vr_val >= 1.3:
        ts += TREND_VOL_STRONG; signals.append("站MA20+放量")
    elif cur > ma20: ts += TREND_VOL_WEAK
    if dif > dea > 0: ts += TREND_MACD_STRONG; signals.append("MACD多头")
    elif dif > dea: ts += TREND_MACD_WEAK
    # MACD底部刚启动（DIF上穿0轴附近，适合底部反转股）
    if dif > dea > 0 and dif < 0.3 and dea > -0.05:
        ts += TREND_MACD_BOTTOM; signals.append("MACD底部刚启动")
    # 放量突破均线压制（大阳线突破MA10/MA20但MA尚未多头排列）
    if abs(pct) >= 3 and vr_val >= 1.3 and cur > max(ma10, ma20) and not (ma5 > ma10 > ma20):
        ts += TREND_MA_BREAK; signals.append("放量突破均线压制")
    # 量能持续放大（今日量比>1.2且昨日量比>1.0）
    if len(v) >= 3:
        vr_yesterday = _vr(v.iloc[:-1])
        if vr_val >= 1.2 and vr_yesterday >= 1.0:
            ts += TREND_VOL_CONT; signals.append("量能持续放大")
    if 5 <= chg20 <= 20: ts += TREND_CHG20
    elif chg20 > 0: ts += 5
    if j > k > 50: ts += TREND_KDJ; signals.append("KDJ多头")
    # 大盘相对强弱（回测时默认不传，留接口）
    if index_pct is not None:
        rel = pct - index_pct
        if rel >= 3:
            ts += TREND_REL_STRONG; signals.append(f"相对强势(+{rel:.1f}%)")
        elif rel >= 1:
            ts += TREND_REL_MID
    ts = min(ts, 100)

    # ── Dip ──
    ds = 0
    d10 = abs(cur - ma10) / max(ma10, 1) * 100
    d20_v = abs(cur - ma20) / max(ma20, 1) * 100
    if d10 <= DIP_MA10_PCT: ds += DIP_MA10; signals.append(f"回踩MA10({d10:.1f}%)")
    elif d20_v <= DIP_MA20_PCT: ds += DIP_MA20; signals.append(f"回踩MA20({d20_v:.1f}%)")
    elif d20_v <= DIP_MA20_WEAK_PCT: ds += DIP_MA20_WEAK; signals.append(f"近MA20({d20_v:.1f}%)")
    if vr_val < DIP_VOL_LOW_PCT: ds += DIP_VOL_LOW; signals.append(f"缩量(vr={vr_val:.2f})")
    elif vr_val < DIP_VOL_MID_PCT: ds += DIP_VOL_MID
    if shadow > 0.5: ds += DIP_SHADOW; signals.append(f"下影({shadow:.0%})")
    if rsi < DIP_RSI_OVER_VAL: ds += DIP_RSI_OVER; signals.append(f"RSI超卖({rsi:.0f})")
    elif rsi < DIP_RSI_LOW_VAL: ds += DIP_RSI_LOW; signals.append(f"RSI偏低({rsi:.0f})")
    elif rsi < DIP_RSI_MID_VAL: ds += DIP_RSI_MID
    if dd >= DIP_DD_VAL: ds += DIP_DD; signals.append(f"回撤{dd:.0f}%")
    ds = min(ds, 100)

    # ── Breakout ──
    bs = 0
    if abs(pct) >= BRK_VOL_STRONG_CHG and vr_val >= BRK_VOL_STRONG_RATIO:
        bs += BRK_VOL_STRONG; signals.append(f"放量突破(+{pct:.1f}%)")
    elif abs(pct) >= BRK_VOL_MID_CHG and vr_val >= BRK_VOL_MID_RATIO:
        bs += BRK_VOL_MID; signals.append("放量上涨")
    if cur >= high20: bs += BRK_HIGH20; signals.append("突破20日高")
    if len(c) >= 11:
        if _ma(c.iloc[:-1], 5) <= _ma(c.iloc[:-1], 10) and ma5 > ma10:
            bs += BRK_GOLDEN; signals.append("MA5金叉MA10")
    if amp20 < BRK_BOX_AMP and cur >= high20 * 0.98:
        bs += BRK_BOX; signals.append("箱体突破")
    if zt60 >= 1: bs += BRK_ZT; signals.append(f"{zt60}次涨停")
    bs = min(bs, 100)

    # ── Composite ──
    scores = [('trend', ts), ('dip', ds), ('breakout', bs)]
    best_type, best_val = max(scores, key=lambda x: x[1])
    n_signals = len(signals)
    MIN_STRONG_SIGNALS = p.get('min_strong_signals', 3)
    strong_signal_ok = n_signals >= MIN_STRONG_SIGNALS
    if best_val >= THRESHOLD_STRONG and strong_signal_ok: level = 'strong'
    elif best_val >= THRESHOLD_STRONG: level = 'medium'  # 分数够但信号不足，降为中等
    elif best_val >= THRESHOLD_MED: level = 'medium'
    elif best_val >= THRESHOLD_LOW: level = 'weak'
    else: level = 'none'

    return dict(trend_score=ts, dip_score=ds, breakout_score=bs,
                composite_score=best_val, signal_type=best_type, signal_level=level)


# ── 回测核心 ──

def backtest_stock(df: pd.DataFrame, params: dict = None,
                   min_history: int = 60) -> List[dict]:
    """
    对单只股票进行逐日回测。
    在每个交易日 T（足够回看数据），计算信号并记录未来 N 日收益。
    """
    if df is None or len(df) < min_history + 20:
        return []

    df = df.sort_values('trade_date').reset_index(drop=True)
    rows = []
    for t in range(min_history, len(df) - 20):
        window = df.iloc[:t+1].copy()
        scores = compute_scores(window, params)
        if scores['signal_level'] == 'none':
            continue
        close_t = float(df.iloc[t]['close'])
        fwd = {}
        for n in [1, 3, 5, 10, 20]:
            if t + n < len(df):
                fwd[f'ret_{n}d'] = (float(df.iloc[t+n]['close']) / close_t - 1) * 100
            else:
                fwd[f'ret_{n}d'] = None
        rows.append({
            'trade_date': df.iloc[t]['trade_date'],
            'ts_code': df.iloc[t].get('ts_code', ''),
            **scores,
            **fwd,
        })
    return rows


# ── 参数网格扫描 ──

def default_params() -> dict:
    return {
        'trend_ma_strong': 30, 'trend_ma_weak': 15,
        'trend_macd_strong': 20, 'trend_macd_weak': 10,
        'trend_vol_strong': 20, 'trend_vol_weak': 10,
        'trend_chg20': 15, 'trend_kdj': 15,
        'dip_ma10': 20, 'dip_ma10_pct': 3.0,
        'dip_ma20': 15, 'dip_ma20_pct': 5.0,
        'dip_ma20_weak': 8, 'dip_ma20_weak_pct': 8.0,
        'dip_vol_low': 20, 'dip_vol_low_pct': 0.8,
        'dip_vol_mid': 8, 'dip_vol_mid_pct': 1.0,
        'dip_shadow': 15,
        'dip_rsi_over': 25, 'dip_rsi_over_val': 35,
        'dip_rsi_low': 15, 'dip_rsi_low_val': 45,
        'dip_rsi_mid': 5, 'dip_rsi_mid_val': 55,
        'dip_dd': 10, 'dip_dd_val': 10.0,
        'brk_vol_strong': 30, 'brk_vol_strong_chg': 3.0, 'brk_vol_strong_ratio': 1.5,
        'brk_vol_mid': 15, 'brk_vol_mid_chg': 2.0, 'brk_vol_mid_ratio': 1.3,
        'brk_high20': 20,
        'brk_golden': 15,
        'brk_box': 15, 'brk_box_amp': 15.0,
        'brk_zt': 10,
        'threshold_strong': 80, 'threshold_med': 60, 'threshold_low': 40,
    }


def generate_param_grid(quick: bool = False) -> List[Tuple[str, dict]]:
    """生成参数组合。每个组合包含名称和参数字典。"""
    base = default_params()
    grids = []

    if quick:
        # 快速模式：只测试核心阈值
        for dip_ma10_pct in [2.0, 3.0, 5.0]:
            p = base.copy()
            p['dip_ma10_pct'] = dip_ma10_pct
            grids.append((f"dip_ma10_pct={dip_ma10_pct}", p))

        for dip_rsi_over_val in [30, 35, 40]:
            p = base.copy()
            p['dip_rsi_over_val'] = dip_rsi_over_val
            grids.append((f"dip_rsi_over={dip_rsi_over_val}", p))

        for brk_vol_strong_chg in [2.0, 3.0, 5.0]:
            p = base.copy()
            p['brk_vol_strong_chg'] = brk_vol_strong_chg
            grids.append((f"brk_chg={brk_vol_strong_chg}", p))

        for threshold_strong in [70, 80, 85]:
            p = base.copy()
            p['threshold_strong'] = threshold_strong
            grids.append((f"strong_th={threshold_strong}", p))

        for trend_ma_split in [(30, 15), (25, 10), (35, 20)]:
            p = base.copy()
            p['trend_ma_strong'], p['trend_ma_weak'] = trend_ma_split
            grids.append((f"ma_{trend_ma_split[0]}_{trend_ma_split[1]}", p))

    else:
        # 全量模式
        # 趋势权重
        for ma_s in [25, 30, 35]:
            for vol_s in [15, 20, 25]:
                p = base.copy()
                p['trend_ma_strong'] = ma_s
                p['trend_vol_strong'] = vol_s
                grids.append((f"trend_ma{ma_s}_vol{vol_s}", p))

        # 低吸MA偏离
        for ma10_pct in [2.0, 3.0, 4.0, 5.0]:
            p = base.copy()
            p['dip_ma10_pct'] = ma10_pct
            p['dip_ma20_pct'] = ma10_pct + 2.0
            grids.append((f"dip_ma_pct={ma10_pct}", p))

        # 低吸RSI
        for rsi_over in [30, 35, 40]:
            p = base.copy()
            p['dip_rsi_over_val'] = rsi_over
            p['dip_rsi_low_val'] = rsi_over + 10
            grids.append((f"dip_rsi={rsi_over}", p))

        # 缩量阈值
        for vol_low in [0.7, 0.8, 0.9]:
            p = base.copy()
            p['dip_vol_low_pct'] = vol_low
            grids.append((f"dip_vol={vol_low}", p))

        # 突破涨幅
        for chg in [2.0, 3.0, 4.0, 5.0]:
            p = base.copy()
            p['brk_vol_strong_chg'] = chg
            grids.append((f"brk_chg={chg}", p))

        # 突破量比
        for ratio in [1.3, 1.5, 2.0]:
            p = base.copy()
            p['brk_vol_strong_ratio'] = ratio
            grids.append((f"brk_vr={ratio}", p))

        # 信号阈值
        for strong_th in [70, 75, 80, 85]:
            p = base.copy()
            p['threshold_strong'] = strong_th
            grids.append((f"strong_th={strong_th}", p))

        # 回撤深度
        for dd_val in [8, 10, 12, 15]:
            p = base.copy()
            p['dip_dd_val'] = float(dd_val)
            grids.append((f"dip_dd={dd_val}", p))

    return grids


def evaluate_params(all_results: List[dict]) -> dict:
    """
    对回测结果汇总统计。
    按信号等级分组（不再拆分信号类型），计算胜率、平均收益、信号数量。
    """
    df = pd.DataFrame(all_results)
    if df.empty:
        return {}

    groups = []
    ret_cols = [c for c in df.columns if c.startswith('ret_')]

    # 按 level 聚合（所有类型合并）
    for level, grp in df.groupby('signal_level'):
        row = {'level': level, 'type': 'all', 'count': len(grp)}
        for rc in ret_cols:
            valid = grp[rc].dropna()
            if len(valid) > 0:
                row[f'{rc}_winrate'] = (valid > 0).mean() * 100
                row[f'{rc}_avgret'] = valid.mean()
                row[f'{rc}_medret'] = valid.median()
                row[f'{rc}_p90'] = valid.quantile(0.9)
                row[f'{rc}_p10'] = valid.quantile(0.1)
                row[f'{rc}_sharpe'] = valid.mean() / max(valid.std(), 0.1)
            else:
                for suffix in ['winrate', 'avgret', 'medret', 'p90', 'p10', 'sharpe']:
                    row[f'{rc}_{suffix}'] = 0
        groups.append(row)

    # 同时按 level + type 细分（用于明细）
    for (level, stype), grp in df.groupby(['signal_level', 'signal_type']):
        row = {'level': level, 'type': stype, 'count': len(grp)}
        for rc in ret_cols:
            valid = grp[rc].dropna()
            if len(valid) > 0:
                row[f'{rc}_winrate'] = (valid > 0).mean() * 100
                row[f'{rc}_avgret'] = valid.mean()
            else:
                row[f'{rc}_winrate'] = 0
                row[f'{rc}_avgret'] = 0
        groups.append(row)

    result_df = pd.DataFrame(groups)
    level_order = {'strong': 0, 'medium': 1, 'weak': 2, 'none': 3}
    result_df['_sort'] = result_df['level'].map(level_order)
    result_df['_type_order'] = result_df['type'].map({'all': 0, 'trend': 1, 'dip': 2, 'breakout': 3, 'none': 4})
    result_df = result_df.sort_values(['_sort', '_type_order', 'count'], ascending=[True, True, False])
    result_df = result_df.drop(columns=['_sort', '_type_order'], errors='ignore')
    return result_df


def run_backtest(daily_data: pd.DataFrame, params: dict,
                 name: str = "default", max_stocks: int = None) -> dict:
    """
    在全部股票上跑回测。
    """
    all_rows = []
    codes = daily_data['ts_code'].unique()
    if max_stocks:
        np.random.seed(42)
        codes = np.random.choice(codes, min(max_stocks, len(codes)), replace=False)
    logger.info(f"  回测参数[{name}]: {len(codes)} 只股票")
    t0 = time.time()
    for i, code in enumerate(codes):
        stock_df = daily_data[daily_data['ts_code'] == code]
        rows = backtest_stock(stock_df, params)
        all_rows.extend(rows)
        if (i + 1) % 100 == 0:
            logger.info(f"    {name}: {i+1}/{len(codes)} 只, {len(all_rows)} 条信号")
    elapsed = time.time() - t0
    eval_df = evaluate_params(all_rows)
    logger.info(f"    {name} 完成: {len(all_rows)} 条信号, 耗时{elapsed:.0f}s")
    return {
        'name': name,
        'params': params,
        'signals': len(all_rows),
        'elapsed': elapsed,
        'evaluation': eval_df,
        'raw_data': all_rows,
    }


def print_param_results(results: List[dict], hold_days: int = 10):
    """打印参数优化结果对比。"""
    ret_key = f'ret_{hold_days}d'
    print()
    print("━" * 120)
    print(f"  参数优化结果对比（持有{hold_days}天）")
    print("━" * 120)
    header = (f"  {'参数名称':>24} {'信号数':>8} {'strong_win':>10} {'med_win':>10} "
              f"{'strong_ret':>10} {'med_ret':>10} {'strong_sharp':>10} {'strong_pct':>8}")
    print(header)
    print("─" * 120)

    for r in results:
        ev = r['evaluation']
        if ev is None or ev.empty:
            continue
        # 只取 type=all 的汇总行
        strong_row = ev[(ev['level'] == 'strong') & (ev['type'] == 'all')]
        medium_row = ev[(ev['level'] == 'medium') & (ev['type'] == 'all')]
        strong_pct = strong_row['count'].sum() / r['signals'] * 100 if r['signals'] > 0 else 0
        sw = strong_row[f'{ret_key}_winrate'].iloc[0] if not strong_row.empty else 0
        mw = medium_row[f'{ret_key}_winrate'].iloc[0] if not medium_row.empty else 0
        sr = strong_row[f'{ret_key}_avgret'].iloc[0] if not strong_row.empty else 0
        mr = medium_row[f'{ret_key}_avgret'].iloc[0] if not medium_row.empty else 0
        ss = strong_row[f'{ret_key}_sharpe'].iloc[0] if not strong_row.empty else 0
        print(f"  {r['name']:>24} {r['signals']:>8} "
              f"{sw:>9.1f}% {mw:>9.1f}% {sr:>9.2f}% {mr:>9.2f}% {ss:>9.2f} {strong_pct:>7.1f}%")
    print("─" * 120)


def print_detail_report(result: dict, hold_days: int = 10):
    """打印单组参数详细报告。"""
    ret_key = f'ret_{hold_days}d'
    ev = result['evaluation']
    print()
    print("━" * 100)
    print(f"  详细报告: {result['name']}  |  信号总数: {result['signals']}")
    print("━" * 100)
    if ev is None or ev.empty:
        print("  (无信号)")
        return
    cols = ['level', 'type', 'count', f'{ret_key}_winrate', f'{ret_key}_avgret',
            f'{ret_key}_medret', f'{ret_key}_p90', f'{ret_key}_p10', f'{ret_key}_sharpe']
    cols = [c for c in cols if c in ev.columns]
    display = ev[cols].copy()
    display.columns = ['等级', '类型', '信号数', '胜率%', '平均收益%', '中位收益%', 'P90%', 'P10%', 'Sharpe']
    # Replace level codes
    display['等级'] = display['等级'].map({'strong': '强烈', 'medium': '中等', 'weak': '一般', 'none': '无'})
    print(display.to_string(index=False))
    print()


def main():
    parser = argparse.ArgumentParser(description='择时信号回测')
    parser.add_argument('--quick', action='store_true', help='快速模式')
    parser.add_argument('--date', default=None, help='交易日')
    parser.add_argument('--hold', type=int, default=10, help='持有天数(默认10)')
    parser.add_argument('--stocks', type=int, default=None, help='限制股票数(快速测试)')
    parser.add_argument('--input', default=None, help='股池路径')
    args = parser.parse_args()

    trade_date = args.date or datetime.now().strftime('%Y%m%d')

    # ── 1. 加载日线数据 ──
    logger.info("加载日线数据...")
    from daily_timing import load_qualified, _load_token, fetch_daily_data
    qualified = load_qualified(args.input)
    token = _load_token()
    from multi_factor_picker.data_fetcher import DataFetcher
    config = {'cache': {'enabled': True, 'dir': 'cache'}, 'tushare': {'max_retry': 3, 'retry_delay': 5}}
    fetcher = DataFetcher(token, config)
    daily = fetcher.get_daily_history(trade_date, days=180)  # 需要更多历史数据
    # 限幅到合格标的
    codes = set(str(c).strip().zfill(6) for c in qualified['code'])
    ts_codes = set(f"{c}.SH" if c.startswith('6') else f"{c}.SZ" for c in codes)
    daily = daily[daily['ts_code'].isin(ts_codes)].copy()
    logger.info(f"日线: {len(daily)} 条, {daily['ts_code'].nunique()} 只标的")

    # ── 2. 先跑基准参数 ──
    base_p = default_params()
    logger.info("\n跑基准参数回测...")
    base_result = run_backtest(daily, base_p, "基准参数", args.stocks)
    print_detail_report(base_result, args.hold)

    # ── 3. 参数网格扫描 ──
    logger.info("\n参数网格扫描...")
    grid = generate_param_grid(args.quick)
    all_results = [base_result]
    for name, params in grid:
        r = run_backtest(daily, params, name, args.stocks)
        all_results.append(r)

    # ── 4. 对比输出 ──
    print_param_results(all_results, args.hold)

    # ── 5. 找最优 ──
    ret_key = f'ret_{args.hold}d'
    best_strong = max(all_results, key=lambda r:
        (r['evaluation'].loc[(r['evaluation']['level'] == 'strong') &
                            (r['evaluation']['type'] == 'all'), f'{ret_key}_winrate'].iloc[0]
         if not r['evaluation'].empty and
         ((r['evaluation']['level'] == 'strong') & (r['evaluation']['type'] == 'all')).any()
         else 0))
    best_sharpe = max(all_results, key=lambda r:
        (r['evaluation'].loc[(r['evaluation']['level'] == 'strong') &
                            (r['evaluation']['type'] == 'all'), f'{ret_key}_sharpe'].iloc[0]
         if not r['evaluation'].empty and
         ((r['evaluation']['level'] == 'strong') & (r['evaluation']['type'] == 'all')).any()
         else 0))
    best_signal = max(all_results, key=lambda r:
        int(r['evaluation'].loc[(r['evaluation']['level'] == 'strong') &
                               (r['evaluation']['type'] == 'all'), 'count'].iloc[0])
        if not r['evaluation'].empty and
        ((r['evaluation']['level'] == 'strong') & (r['evaluation']['type'] == 'all')).any()
        else 0)

    print()
    print("━" * 80)
    print("  最优参数总结")
    print("━" * 80)
    print(f"  最高胜率: {best_strong['name']}")
    print(f"  最高Sharpe: {best_sharpe['name']}")
    print(f"  最多信号: {best_signal['name']}")
    print()

    # 保存最优参数
    if best_strong and best_strong['name'] != '基准参数':
        opt_params = best_strong['params']
        out_path = os.path.join(os.path.dirname(__file__), 'config', 'optimal_timing_params.json')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w') as f:
            json.dump({k: v for k, v in opt_params.items()}, f, indent=2, ensure_ascii=False)
        logger.info(f"最优参数已保存: {out_path}")

    # 保存完整回测结果
    out_path = os.path.join(os.path.dirname(__file__), 'report_daily', f'backtest_{trade_date}.json')
    summary = []
    for r in all_results:
        ev = r['evaluation']
        summary.append({
            'name': r['name'],
            'signals': r['signals'],
            'elapsed': round(r['elapsed'], 1),
            'strong_signals': int(ev[ev['level'] == 'strong']['count'].sum()) if ev is not None and not ev.empty else 0,
            'medium_signals': int(ev[ev['level'] == 'medium']['count'].sum()) if ev is not None and not ev.empty else 0,
            'strong_winrate': round(float(ev[ev['level'] == 'strong'][f'{ret_key}_winrate'].sum()), 1)
                if ev is not None and not ev.empty and 'strong' in ev['level'].values else 0,
            'medium_winrate': round(float(ev[ev['level'] == 'medium'][f'{ret_key}_winrate'].sum()), 1)
                if ev is not None and not ev.empty and 'medium' in ev['level'].values else 0,
        })
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info(f"回测摘要已保存: {out_path}")


if __name__ == '__main__':
    main()
