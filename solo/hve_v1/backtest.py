# -*- coding: utf-8 -*-
"""HVE V1 回测（§37 四档收益 / §38 尾部依赖 / §39 成本梯度 / §40 Walk Forward 接口）

样本口径（与 §37 一致）：
  样本 = walk_stock 输出的「每日信号记录」，按 signal_type 分组
  （HVE_BULL / HVE_2ND / HVE_WATCH / HVE_FAIL），而非只看 HVE 事件当日。

收益口径（§1.3 / §9 裁决「回测统一走 stock_data.db」）：
  数据源 = 唯一缓存 stock_data.db（HveData → HvtDataLoader），不触研究层面板；
  入场 = 信号日收盘；出场 = T+N 交易日收盘；
  收益 = (close_t2×adj_t2)/(close_t1×adj_t1) − 1（区间两端复权因子，无未来函数）；
  成本 = 单边扣减 cost_bp/10000（与既有研究层 perf_stats 口径一致）。

硬约束：
  - 只用 <= 当日的行情；股票池逐行向量化判定（universe.passes_mask），不用当日截面统计；
  - 市场环境按日缓存（同一 MarketEnv，含 breadth），PIT 安全；
  - 不做参数寻优，只输出历史结果与 train/valid/OOS 切片（§40）；
  - 统计内核自带实现，不 import research/hve/hve_common（该模块 import 期会
    os.makedirs + 引入 hef_common 依赖链并全局 np.seterr，与「hve_v1 零外部耦合」冲突）。

产物：report_daily/hve_backtest_YYYYMMDD.json
"""
import json
import os
import sys
from array import array
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hve_v1 import load_config                        # noqa: E402
from hve_v1 import signals as S                       # noqa: E402
from hve_v1.data import shared                        # noqa: E402
from hve_v1.event import annotate_events, hve_mask    # noqa: E402
from hve_v1.indicators import compute_indicators      # noqa: E402
from hve_v1.market import MARKET_NEUTRAL, MarketEnv   # noqa: E402
from hve_v1.state import walk_stock                   # noqa: E402
from hve_v1.universe import basic_maps, passes_mask    # noqa: E402

OUT_DIR = os.path.join(BASE_DIR, 'report_daily')
WARMUP_DAYS = 250
MIN_BARS = 25
BJ_PREFIX = ('8', '4', '9')

GROUPS = (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL)

# §51-12/13 分层口径（写死在报告里，便于复核）
CAP_SMALL, CAP_MID = 100.0, 500.0          # 亿元：<100 小盘 / 100~500 中盘 / >=500 大盘


def _board(code: str) -> str:
    """§51-12 主板 / 创业板 / 科创板（北交所在股票池已被排除）"""
    c = str(code)[:3]
    if c in ('688', '689'):
        return '科创板'
    if c in ('300', '301'):
        return '创业板'
    return '主板'


def _cap_bucket(total_mv) -> str:
    """§51-13 大中小盘（total_mv 单位：万元，与 universe.passes_row 同一换算）"""
    try:
        v = float(total_mv)
    except (TypeError, ValueError):
        return '未知'
    if not np.isfinite(v) or v <= 0:
        return '未知'
    yi = (v / 10.0 if v > 1e6 else v) / 10000.0        # → 亿元
    if yi < CAP_SMALL:
        return f'小盘(<{CAP_SMALL:.0f}亿)'
    if yi < CAP_MID:
        return f'中盘({CAP_SMALL:.0f}~{CAP_MID:.0f}亿)'
    return f'大盘(≥{CAP_MID:.0f}亿)'


def _bump(counter: dict, key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


# ---------------------------------------------------------------- 统计内核

def _clean(r) -> np.ndarray:
    a = np.asarray(r, dtype=float)
    return a[np.isfinite(a)]


def _stats(r, cost_bp: float = 0.0) -> dict:
    """N / Win Rate / Mean / Median / PF / Expectancy（§37 前六项）"""
    r = _clean(r)
    n = int(r.size)
    out = {'n': n, 'win_rate': None, 'mean': None, 'median': None,
           'pf': None, 'pf_inf': False, 'expectancy': None}
    if n == 0:
        return out
    net = r - float(cost_bp) / 10000.0
    w = net[net > 0]
    l = net[net <= 0]
    gp, gl = float(w.sum()), float(-l.sum())
    out['win_rate'] = float(w.size) / n
    out['mean'] = float(net.mean())
    out['median'] = float(np.median(net))
    out['expectancy'] = float(net.mean())
    if gl > 0:
        out['pf'] = gp / gl
    else:
        out['pf_inf'] = True       # 无亏损样本：不用 inf 污染 JSON
    return out


def _path_stats(mfe, mae, mdd) -> dict:
    """§37 后三项：MFE / MAE / Max Drawdown（均为样本均值，单位=比例）"""
    f, a, d = _clean(mfe), _clean(mae), _clean(mdd)
    return {
        'mfe': float(f.mean()) if f.size else None,
        'mae': float(a.mean()) if a.size else None,
        'max_drawdown': float(d.mean()) if d.size else None,
    }


def _tail_stats(r, fracs=(0.05, 0.10)) -> dict:
    """§38 Top5%/Top10% 贡献 + 剔除后的均值（判断收益是否由极少数样本贡献）"""
    r = _clean(r)
    out = {'n': int(r.size), 'mean_full': float(r.mean()) if r.size else None}
    for f in fracs:
        key = f'ex_top{int(f * 100)}'
        if r.size == 0:
            out[key] = None
            continue
        k = max(1, int(r.size * f))
        s = np.sort(r)
        gp = float(s[s > 0].sum())
        out[key] = {
            'top_share_of_gross': (float(s[-k:].sum()) / gp) if gp > 0 else None,
            'mean': float(s[:-k].mean()) if r.size > k else None,
        }
    return out


def _wf_split(years, ranges: dict) -> dict:
    """§40 train / validation / OOS 区间掩码（忽略 _note 等非 [起,止] 项）"""
    y = np.asarray(years, dtype=int)
    out = {}
    for name, rng in (ranges or {}).items():
        if str(name).startswith('_') or not isinstance(rng, (list, tuple)) or len(rng) != 2:
            continue
        a, b = rng
        out[name] = (y >= int(a)) & (y <= int(b))
    return out


# ---------------------------------------------------------------- 主流程

def _codes(data, limit=None, stride=1):
    names, lds = basic_maps()
    if not names:
        return [], {}, {}
    codes = sorted(c for c in names
                   if str(c).endswith(('.SH', '.SZ')) and not str(c).startswith(BJ_PREFIX))
    if stride and stride > 1:
        codes = codes[::int(stride)]
    if limit:
        codes = codes[:int(limit)]
    return codes, names, lds


def _warmup_start(start: str) -> str:
    return (pd.to_datetime(str(start)) - pd.Timedelta(days=WARMUP_DAYS)).strftime('%Y%m%d')


def run(start: str, end: str, cfg: dict = None, data=None,
        limit: int = None, stride: int = 1, verbose: bool = True) -> dict:
    """区间回测：返回可直接 json.dump 的结果 dict"""
    cfg = cfg or load_config()
    data = data or shared()
    bt = cfg.get('backtest', {}) or {}
    horizons = [int(h) for h in bt.get('horizons', (3, 5, 10, 20))]
    costs = [float(c) for c in bt.get('cost_bp', (0, 10, 20, 30, 50))]
    primary_cost = float(bt.get('primary_cost_bp', 30.0))
    fracs = tuple(float(x) for x in bt.get('tail_flags', (0.05, 0.10)))
    wf_ranges = bt.get('wf', {}) or {}
    start, end = str(start), str(end)
    warm = _warmup_start(start)

    codes, names, lds = _codes(data, limit=limit, stride=stride)
    if verbose:
        print(f'[HVE-BT] 区间 {start}~{end} ｜ 待扫股票 {len(codes)} 只 '
              f'｜ 四档 {horizons} ｜ 成本 {costs}bp')

    env = MarketEnv(data, cfg)
    regime_cache = {}
    n_breadth = data.prefetch_breadth(start, end)      # 一次 GROUP BY 预取，替代逐日截面
    if verbose:
        print(f'[HVE-BT] breadth 预取 {n_breadth} 个交易日')

    def regime_fn(dt):
        if dt not in regime_cache:
            try:
                regime_cache[dt] = env.regime(dt)
            except Exception:
                regime_cache[dt] = MARKET_NEUTRAL
        return regime_cache[dt]

    # 累加器：group → horizon → 定长数组（array('d') 省内存）
    keys = [(g, h) for g in GROUPS for h in horizons]
    acc = {k: {'ret': array('d'), 'mfe': array('d'), 'mae': array('d'),
               'mdd': array('d'), 'year': array('i'),
               'board': {}, 'cap': {}, 'regime': {}} for k in keys}
    n_excluded = 0
    n_no_fwd = {h: 0 for h in horizons}
    n_stocks = n_scanned = 0
    n_hve_days = n_clusters = 0

    for ci, code in enumerate(codes, 1):
        if verbose and ci % 200 == 0:
            print(f'[HVE-BT]   {ci}/{len(codes)} ｜ 出事件股票 {n_stocks} ｜ 样本 '
                  f'{sum(len(acc[(g, h)]["ret"]) for g in GROUPS for h in horizons)}')
        try:
            df = data.load(code, warm, end)
        except Exception:
            continue
        if df is None or len(df) < MIN_BARS:
            continue
        n_scanned += 1
        name = names.get(code, '')
        ld = lds.get(code, '')
        d = compute_indicators(df, cfg)
        d, ev = annotate_events(d, code, cfg)
        dates = d['trade_date'].astype(str).to_numpy()
        in_win = (dates >= start) & (dates <= end)
        n_hve_days += int(np.sum(hve_mask(d, cfg) & in_win))       # §51-1 原始 HVE 日
        n_clusters += sum(1 for e in ev
                          if start <= str(e.get('event_date')) <= end)  # §9 去重后簇
        if not ev:
            continue
        n_stocks += 1
        didx = {dt: i for i, dt in enumerate(dates)}
        # 股票池：向量化一次算好（阈值同 universe._universe），再叠加上区间约束
        elig = passes_mask(d, code, name, ld, cfg) & in_win

        def eligible(t, _e=elig):
            return bool(_e[t])

        recs = walk_stock(d, code, cfg, ev, name=name,
                          regime_fn=regime_fn, eligible=eligible)
        if not recs:
            continue
        close = np.asarray(d['close'], dtype=float)
        high = np.asarray(d['high'], dtype=float)
        low = np.asarray(d['low'], dtype=float)
        adj = np.asarray(d['adj_factor'], dtype=float) if 'adj_factor' in d.columns \
            else np.ones(len(d))
        mv = np.asarray(d['total_mv'], dtype=float) if 'total_mv' in d.columns else None
        board = _board(code)
        n = len(d)

        for rec in recs:
            g = rec.get('signal_type')
            if g not in GROUPS:
                continue
            if not rec.get('executable'):
                n_excluded += 1
                continue
            t = didx.get(rec['trade_date'])
            if t is None or not np.isfinite(close[t]) or not (close[t] > 0) \
                    or not np.isfinite(adj[t]):
                continue
            base = close[t] * adj[t]
            yr = int(str(rec['trade_date'])[:4])
            regime = regime_fn(rec['trade_date'])
            cap = _cap_bucket(mv[t]) if mv is not None else '未知'
            for h in horizons:
                j = t + h
                if j >= n:
                    n_no_fwd[h] += 1
                    continue
                if not (np.isfinite(close[j]) and np.isfinite(adj[j])):
                    n_no_fwd[h] += 1
                    continue
                r = data.adjusted_return(close[t], adj[t], close[j], adj[j])
                if not np.isfinite(r):
                    continue
                seg_a = adj[t + 1:j + 1]
                hi = np.nanmax(high[t + 1:j + 1] * seg_a) if j > t else np.nan
                lo = np.nanmin(low[t + 1:j + 1] * seg_a) if j > t else np.nan
                p = close[t:j + 1] * adj[t:j + 1]
                cm = np.maximum.accumulate(p)
                with np.errstate(invalid='ignore', divide='ignore'):
                    dd = np.nanmax(1.0 - p / cm)
                a = acc[(g, h)]
                a['ret'].append(float(r))
                a['mfe'].append(float(hi / base - 1.0) if np.isfinite(hi) else np.nan)
                a['mae'].append(float(lo / base - 1.0) if np.isfinite(lo) else np.nan)
                a['mdd'].append(float(dd) if np.isfinite(dd) else np.nan)
                a['year'].append(yr)
                _bump(a['board'], board)
                _bump(a['cap'], cap)
                _bump(a['regime'], str(regime))

    # -------- 汇总 --------
    groups_out = {}
    for g in GROUPS:
        g_out = {}
        for h in horizons:
            a = acc[(g, h)]
            r = np.frombuffer(a['ret'], dtype=float)
            y = np.frombuffer(a['year'], dtype=np.int32)
            row = _stats(r)
            row.update(_path_stats(np.frombuffer(a['mfe'], dtype=float),
                                   np.frombuffer(a['mae'], dtype=float),
                                   np.frombuffer(a['mdd'], dtype=float)))
            row['tail'] = _tail_stats(r, fracs)
            row['tail_dependent'] = bool(
                r.size > 0 and row['tail'].get('ex_top5', {})
                and row['tail']['ex_top5'].get('mean') is not None
                and row['tail']['ex_top5']['mean'] <= 0)
            row['cost'] = {f'{int(c)}bp': _stats(r, cost_bp=c) for c in costs}
            pc = _stats(r, cost_bp=primary_cost)
            row['economic_edge'] = ('WEAK' if (pc['n'] == 0 or pc['mean'] is None
                                               or pc['mean'] <= 0
                                               or (pc['pf'] is not None and pc['pf'] <= 1.0))
                                    else 'OK')
            row['breakdown'] = {'board': dict(sorted(a['board'].items())),
                                'cap': dict(sorted(a['cap'].items())),
                                'regime': dict(sorted(a['regime'].items()))}
            g_out[f'T{h}'] = row
        groups_out[g] = g_out

    wf_out = {}
    for g in GROUPS:
        for h in horizons:
            a = acc[(g, h)]
            if not len(a['ret']):
                continue
            r = np.frombuffer(a['ret'], dtype=float)
            y = np.frombuffer(a['year'], dtype=np.int32)
            for name, m in _wf_split(y, wf_ranges).items():
                wf_out.setdefault(name, {}).setdefault(g, {})[f'T{h}'] = _stats(r[m])

    result = {
        'start': start,
        'end': end,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_source': 'stock_data.db (HveData / HvtDataLoader)',
        'universe_scanned': n_scanned,
        'stocks_with_events': n_stocks,
        'n_hve_days': n_hve_days,
        'n_event_clusters': n_clusters,
        'horizons': horizons,
        'cost_bp': costs,
        'primary_cost_bp': primary_cost,
        'n_excluded_untradable': n_excluded,
        'n_insufficient_forward': n_no_fwd,
        'groups': groups_out,
        'walk_forward': wf_out,
        'wf_ranges': wf_ranges,
        'params_status': (cfg.get('_meta') or {}).get('status', ''),
        'params': {k: v for k, v in cfg.items() if not str(k).startswith('_')},
        'note': ('收益为复权口径、成本单边扣减；样本=每日信号记录（非仅事件当日），'
                 '同一事件窗口内的相邻交易日样本高度重叠、不独立，统计量不可当作独立样本 t 检验使用；'
                 '不做参数寻优，仅输出历史结果与 WF 切片（§40/§50）'),
    }
    return result


def save(result: dict, out_dir: str = OUT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"hve_backtest_{result['end']}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    return path


def summary_lines(result: dict, group: str = None, horizon: str = None) -> list:
    """文本摘要（回测入口打印 / 研究报告引用）"""
    hz = f'T{horizon}' if horizon else f"T{result['horizons'][-1]}"
    lines = [f"区间 {result['start']}~{result['end']} ｜ 数据源 {result['data_source']}",
             f"扫描 {result['universe_scanned']} 只 / 有事件 {result['stocks_with_events']} 只 "
             f"｜ 剔除不可成交 {result['n_excluded_untradable']} 条"]
    for g in ([group] if group else GROUPS):
        row = (result['groups'].get(g) or {}).get(hz)
        if not row:
            continue
        pf = 'INF' if row['pf_inf'] else (f"{row['pf']:.3f}" if row['pf'] is not None else '—')
        lines.append(
            f"{g:9s} {hz}: N={row['n']:6d} 胜率={_p(row['win_rate'])} "
            f"均值={_p(row['mean'])} 中位={_p(row['median'])} PF={pf} "
            f"MFE={_p(row['mfe'])} MAE={_p(row['mae'])} MDD={_p(row['max_drawdown'])} "
            f"tail_dep={'Y' if row['tail_dependent'] else 'N'} edge={row['economic_edge']}")
    return lines


def _p(v):
    return '—' if v is None else f'{v * 100:.2f}%'


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='HVE V1 回测')
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--stride', type=int, default=1)
    ap.add_argument('--no-save', action='store_true')
    a = ap.parse_args()
    res = run(a.start, a.end, limit=a.limit, stride=a.stride)
    print('\n'.join(summary_lines(res)))
    if not a.no_save:
        print(save(res))
