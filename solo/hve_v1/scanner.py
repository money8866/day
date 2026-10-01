# -*- coding: utf-8 -*-
"""HVE V1 每日扫描（§27 输出字段 / §47 日报 / §48 展示排序 / §49 输出格式）

产物（命名空间隔离，不覆盖任何现有文件）：
  report_daily/hve_daily_YYYYMMDD.json    §47 四态 + §10 当日 HVE 事件
  report_daily/hve_daily_YYYYMMDD.md      §49 展示（手机可读）

硬约束：
  - 只用 <= trade_date 的数据（regime_fn 常量化，事件窗口只向过去展开）
  - 无综合评分：排序只用单维原始量（§48），字段名不含 score / alpha
  - 主题字段（theme / theme_heat / theme_rank）仅作输出，不参与核心算法（§33）
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hve_v1 import load_config                       # noqa: E402
from hve_v1 import signals as S                      # noqa: E402
from hve_v1.data import shared                       # noqa: E402
from hve_v1.event import HVE_COLS, annotate_events   # noqa: E402
from hve_v1.indicators import compute_indicators     # noqa: E402
from hve_v1.market import MARKET_NEUTRAL, MarketEnv   # noqa: E402
from hve_v1.state import walk_stock                  # noqa: E402
from hve_v1.universe import daily_universe           # noqa: E402

OUT_DIR = os.path.join(BASE_DIR, 'report_daily')
WARMUP_DAYS = 250      # 日历日回溯（≈170 交易日，覆盖 MA60 / ATR20 / VR20 预热）
MIN_BARS = 25          # 少于此长度不可能有 VR20，直接跳过

BUCKETS = (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL)
EVENT_EXTRA = ('is_primary', 'cluster_max_volume_date', 'cluster_max_volume_ratio')


# ---------------------------------------------------------------- 工具

def _native(v):
    """numpy / pandas 标量 → Python 原生；NaN/Inf → None（JSON 安全）"""
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return f if np.isfinite(f) else None
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, float):
        return v if np.isfinite(v) else None
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        return str(v)
    return v


def _dict_native(d: dict) -> dict:
    return {k: _native(v) for k, v in d.items()}


def _warmup_start(trade_date: str) -> str:
    return (pd.to_datetime(str(trade_date)) - pd.Timedelta(days=WARMUP_DAYS)).strftime('%Y%m%d')


def _num(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except Exception:
        return None


def _pct(v, nd=1):
    f = _num(v)
    return '—' if f is None else f'{f * 100:.{nd}f}%'


def _f2(v):
    f = _num(v)
    return '—' if f is None else f'{f:.2f}'


# ---------------------------------------------------------------- 主题（§33 仅输出）

def _theme_enrich(codes, trade_date: str) -> dict:
    """{ts_code: (theme, theme_heat, theme_rank)}；数据缺失一律 ('', None, None)（fail-soft）"""
    try:
        from hvt_bull.context import load_stock_themes, _load_theme_heat
        smap = load_stock_themes(trade_date) or {}
        heat = _load_theme_heat() or {}
    except Exception:
        return {}
    days = [d for d in heat if d <= str(trade_date)] if trade_date else list(heat)
    day = heat[max(days)] if days else {}
    ranks = {t: i + 1 for i, (t, _) in enumerate(sorted(day.items(), key=lambda kv: -kv[1]))} \
        if day else {}
    out = {}
    for c in codes:
        best_t, best_h = '', None
        for t in (smap.get(c) or []):
            h = _num(day.get(t))
            if h is not None and (best_h is None or h > best_h):
                best_t, best_h = t, h
        out[c] = (best_t, best_h, ranks.get(best_t))
    return out


# ---------------------------------------------------------------- 排序（§48）

def _sort_bull(r: dict):
    """HVE-BULL：优先「再扩张强度」＝当日 VR20（单维原始量，不做综合评分）"""
    v = _num(r.get('current_volume_ratio'))
    return (-(v if v is not None else -1.0), r.get('ts_code') or '')


def _sort_2nd(r: dict):
    """HVE-2ND：优先「突破幅度 + 量能确认」"""
    bd = _num(r.get('breakout_distance'))
    vr = _num(r.get('current_volume_ratio'))
    return (-(bd if bd is not None else -9e9),
            -(vr if vr is not None else -1.0),
            r.get('ts_code') or '')


# ---------------------------------------------------------------- 扫描

def scan_day(trade_date: str, cfg: dict = None, data=None, market=None,
             verbose: bool = True) -> dict:
    """单日全市场扫描：返回 §47 结果 dict（同时给出 count / 排序后的四态）"""
    cfg = cfg or load_config()
    data = data or shared()
    trade_date = str(trade_date)
    start = _warmup_start(trade_date)

    uni = daily_universe(data.loader, trade_date, cfg)
    if verbose:
        print(f'[HVE] 股票池: {len(uni)} 只 ({trade_date})')

    regime, mkt_ctx = MARKET_NEUTRAL, {}
    try:
        env = market or MarketEnv(data, cfg)
        regime = env.regime(trade_date)
        mkt_ctx = env.describe(trade_date)
    except Exception as e:
        print(f'[HVE] 市场环境计算失败，按 {MARKET_NEUTRAL} 处理: {e}')
    regime_fn = lambda dt: regime       # noqa: E731  当日环境对整条历史取常数（PIT 安全）

    today_recs, today_events = [], []
    for u in uni:
        code, name = u['ts_code'], u.get('name', '')
        try:
            df = data.load(code, start, trade_date)
        except Exception:
            continue
        if df is None or len(df) < MIN_BARS:
            continue
        d = compute_indicators(df, cfg)
        d, ev = annotate_events(d, code, cfg)
        if not ev:
            continue
        # §10 当日 HVE 事件行
        mask = (d['hve_flag'].to_numpy(dtype=bool)
                & (d['trade_date'].astype(str) == trade_date).to_numpy())
        for _, row in d[mask].iterrows():
            row = dict(row)
            ev_row = {c: row.get(c) for c in list(HVE_COLS) + list(EVENT_EXTRA)}
            ev_row['ts_code'] = code          # 时序表无 ts_code 列，由循环变量补
            today_events.append(_dict_native(ev_row))
        recs = walk_stock(d, code, cfg, ev, name=name, regime_fn=regime_fn)
        rec = next((r for r in recs if r['trade_date'] == trade_date), None)
        if rec is not None:
            today_recs.append(rec)

    # 主题（§33 仅输出字段）
    tmap = _theme_enrich([r['ts_code'] for r in today_recs], trade_date)
    for r in today_recs:
        t, h, rk = tmap.get(r['ts_code'], ('', None, None))
        r['theme'], r['theme_heat'], r['theme_rank'] = t, h, rk

    buckets = {b: [] for b in BUCKETS}
    for r in today_recs:
        buckets.setdefault(r.get('signal_type'), []).append(_dict_native(r))
    buckets[S.HVE_BULL].sort(key=_sort_bull)
    buckets[S.HVE_2ND].sort(key=_sort_2nd)

    params = {k: v for k, v in cfg.items() if not str(k).startswith('_')}
    result = {
        'trade_date': trade_date,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'universe': len(uni),
        'market_regime': regime,
        'market_context': _dict_native(mkt_ctx or {}),
        'params_status': (cfg.get('_meta') or {}).get('status', ''),
        'params': _dict_native(params),
        'n_hve_today': len(today_events),
        'counts': {b: len(buckets[b]) for b in BUCKETS},
        'hve_events': today_events,
        'HVE_BULL': buckets[S.HVE_BULL],
        'HVE_2ND': buckets[S.HVE_2ND],
        'HVE_WATCH': buckets[S.HVE_WATCH],
        'HVE_FAIL': buckets[S.HVE_FAIL],
    }
    if verbose:
        c = result['counts']
        print(f"[HVE] 完成: 当日HVE {len(today_events)} / BULL {c[S.HVE_BULL]} / "
              f"2ND {c[S.HVE_2ND]} / WATCH {c[S.HVE_WATCH]} / FAIL {c[S.HVE_FAIL]}")
    return result


# ---------------------------------------------------------------- 落盘

def _exec_note(r: dict) -> str:
    """§32 / §46 执行口径说明：区分「市场风险降级」与「不可成交」"""
    sig = r.get('signal')
    if sig == S.SIG_CONDITIONAL:
        return '信号降级（市场 RISK，§32）：保留研究信号，不作为可执行买点'
    if sig != S.SIG_BUY:
        return '—'
    return '可执行' if r.get('executable') else '不可执行（涨停/一字板，§46）'


def _hve_item(r: dict, kind: str) -> list:
    """§49 单只展示块（无缩进，手机友好）"""
    up = 'UP' if r.get('ma20_up') else 'DOWN'
    if kind == S.HVE_2ND:
        lines = [
            f"### {r['ts_code']} {r.get('name') or ''}",
            f"- HVE日期：{r.get('event_date')} ｜ 距HVE：{r.get('days_since_hve')} 日",
            f"- VR20：{_f2(r.get('vr20'))} ｜ HVE涨幅：{_pct(r.get('hve_return'))} ｜ HVE CLV：{_f2(r.get('hve_clv'))}",
            f"- 消化：{r.get('days_since_hve')}D ｜ 最大回撤：{_pct(r.get('drawdown_from_hve'))} ｜ "
            f"Volume Decay：{_f2(r.get('volume_decay_5d'))} ｜ 结构：OK",
            f"- 整理区High：{_f2(r.get('consolidation_high'))} ｜ 当前Close：{_f2(r.get('close'))} ｜ "
            f"突破：{'YES' if r.get('signal') != S.SIG_WATCH else 'NO'}",
            f"- 突破VR：{_f2(r.get('current_volume_ratio'))}",
        ]
    else:
        lines = [
            f"### {r['ts_code']} {r.get('name') or ''}",
            f"- HVE日期：{r.get('event_date')} ｜ 距HVE：{r.get('days_since_hve')} 日",
            f"- VR20：{_f2(r.get('vr20'))} ｜ HVE涨幅：{_pct(r.get('hve_return'))} ｜ HVE CLV：{_f2(r.get('hve_clv'))}",
            f"- 当前状态：{r.get('state')} ｜ MA20：{up}",
            f"- HVE后最大回撤：{_pct(r.get('drawdown_from_hve'))} ｜ 当前VR20：{_f2(r.get('current_volume_ratio'))}",
            f"- 再扩张：{'YES' if r.get('signal') in (S.SIG_BUY, S.SIG_CONDITIONAL) else 'NO'} ｜ "
            f"触发价(前3日高)：{_f2(r.get('trigger_price'))}",
        ]
    lines += [
        f"- Entry：{_f2(r.get('entry_price'))} ｜ 失效价：{_f2(r.get('invalid_price'))}"
        f"（{r.get('invalid_rule') or '—'}）",
        f"- 市场环境：{r.get('market_regime')} ｜ 可交易性：{r.get('tradability_flag')} ｜ "
        f"{_exec_note(r)}",
    ]
    if r.get('theme'):
        lines.append(f"- 主题：{r['theme']}（热度 {_f2(r.get('theme_heat'))} / 排名 {r.get('theme_rank')}）"
                     f"〔仅输出字段，不参与算法〕")
    lines += [
        f"- reason：`{r.get('reason')}`",
        f"- **SIGNAL = {r.get('signal')}** ｜ signal_type = {r.get('signal_type')}",
        '',
    ]
    return lines


def render_md(result: dict) -> str:
    """§49 展示：可执行的两态逐只展开，观察/失效态用紧凑表"""
    c = result['counts']
    p = result['params']
    out = [
        f"# {result['trade_date']} HVE V1 高量事件日报",
        '',
        f"- 股票池：{result['universe']} 只",
        f"- 市场环境：**{result['market_regime']}**"
        + (f"（指数 {_f2((result.get('market_context') or {}).get('close'))} / "
           f"MA20 {_f2((result.get('market_context') or {}).get('ma_fast'))} / "
           f"MA60 {_f2((result.get('market_context') or {}).get('ma_slow'))}）"
           if (result.get('market_context') or {}).get('close') is not None else ''),
        f"- 参数状态：{result['params_status']}（非 OPTIMAL_PARAMETER，禁止为提升历史胜率而调整）",
        f"- 核心参数：VR20≥{p.get('vr20_min')} · 涨幅≥{_pct(p.get('return_min'), 0)} · "
        f"CLV≥{p.get('clv_min')} · 回撤≤{_pct(p.get('bull_max_drawdown'), 0)}"
        f"/{_pct(p.get('second_max_drawdown'), 0)} · Volume Decay≤{p.get('volume_decay_max')} · "
        f"整理≥{p.get('min_digest_days')}日 · 突破量比≥{p.get('breakout_volume_ratio')}",
        f"- 计数：HVE_BULL {c[S.HVE_BULL]} ｜ HVE_2ND {c[S.HVE_2ND]} ｜ "
        f"HVE_WATCH {c[S.HVE_WATCH]} ｜ HVE_FAIL {c[S.HVE_FAIL]} ｜ 当日新增 HVE 事件 {result['n_hve_today']}",
        '',
        '> HVE 是事件，不是买点：HVE 当日永不产生 BUY；BUY 只出现在 Re-expansion 或 Breakout。',
        '> 排序仅为展示（HVE-BULL 按再扩张强度、HVE-2ND 按突破幅度+量能），不是 Alpha 评分。',
        '',
        '---',
        '',
    ]

    out.append(f"## HVE-BULL（{c[S.HVE_BULL]}）")
    out += ([''] + sum((_hve_item(r, S.HVE_BULL) for r in result['HVE_BULL']), [])
            if result['HVE_BULL'] else ['', '无。', ''])
    out.append(f"## HVE-2ND（{c[S.HVE_2ND]}）")
    out += ([''] + sum((_hve_item(r, S.HVE_2ND) for r in result['HVE_2ND']), [])
            if result['HVE_2ND'] else ['', '无。', ''])

    for key, title, cols in (
        (S.HVE_WATCH, f"HVE-WATCH（{c[S.HVE_WATCH]}）",
         ('代码', '名称', '状态', 'HVE日期', '距HVE', 'VR20', '回撤', 'MA20')),
        (S.HVE_FAIL, f"HVE-FAIL（{c[S.HVE_FAIL]}）",
         ('代码', '名称', 'HVE日期', '距HVE', '回撤', '失效规则', 'reason')),
    ):
        rows = result[key]
        out += ['', f"## {title}", '']
        if not rows:
            out += ['无。', '']
            continue
        out.append('| ' + ' | '.join(cols) + ' |')
        out.append('|' + '---|' * len(cols))
        for r in rows:
            if key == S.HVE_WATCH:
                out.append('| ' + ' | '.join([
                    r['ts_code'], r.get('name') or '', r.get('state') or '',
                    r.get('event_date') or '', str(r.get('days_since_hve')),
                    _f2(r.get('vr20')), _pct(r.get('drawdown_from_hve')),
                    'UP' if r.get('ma20_up') else 'DOWN']) + ' |')
            else:
                out.append('| ' + ' | '.join([
                    r['ts_code'], r.get('name') or '', r.get('event_date') or '',
                    str(r.get('days_since_hve')), _pct(r.get('drawdown_from_hve')),
                    r.get('invalid_rule') or '—', r.get('reason') or '']) + ' |')
        out.append('')

    if result['hve_events']:
        out += ['## 当日新增 HVE 事件（研究口径，非买点）', '',
                '| 代码 | HVE日期 | primary | VR20 | 涨幅 | CLV | 距20日高 | 距60日高 |'
                ' 簇内最大量日 | 簇内最大量比 |',
                '|---|---|---|---|---|---|---|---|---|---|']
        for e in result['hve_events']:
            out.append('| ' + ' | '.join([
                str(e.get('ts_code')), str(e.get('trade_date')), 'Y' if e.get('is_primary') else 'N',
                _f2(e.get('vr20')), _pct(e.get('daily_return')), _f2(e.get('clv')),
                _pct(e.get('distance_to_20d_high')), _pct(e.get('distance_to_60d_high')),
                str(e.get('cluster_max_volume_date') or ''), _f2(e.get('cluster_max_volume_ratio'))]) + ' |')
        out.append('')

    out += ['---', '',
            '本报告由 HVE V1 量化系统自动生成，仅输出信号，不构成投资建议。', '']
    return '\n'.join(out)


def save(result: dict, out_dir: str = OUT_DIR) -> tuple:
    """落盘 JSON + MD，返回 (json_path, md_path)"""
    os.makedirs(out_dir, exist_ok=True)
    td = result['trade_date']
    jp = os.path.join(out_dir, f'hve_daily_{td}.json')
    mp = os.path.join(out_dir, f'hve_daily_{td}.md')
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(render_md(result))
    return jp, mp


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='HVE V1 每日扫描')
    ap.add_argument('--date', required=True)
    a = ap.parse_args()
    res = scan_day(a.date)
    print(save(res))
