"""主线第一次回调策略 20260804-0814 信号跟踪分析

对区间内所有 rally_pullback 信号标的，计算信号后 T+1 买入的收益表现
（T+1/T+2/T+3/T+5/最新 收盘收益、区间最高最低、触止损/止盈），并做主题归类。
"""
import os
import sys
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml
import pandas as pd
import tushare as ts

import stock_cache as sc

TRACK_START = '20260804'
TRACK_END = '20260814'


def get_token(cfg: dict) -> str:
    token = os.environ.get('TUSHARE_TOKEN')
    if token:
        return token
    env_files = [
        'config/.env', '../config/.env',
        'multi_factor_picker/config/.env',
        os.path.expanduser('~/.env'),
    ]
    for f in env_files:
        if os.path.exists(f):
            with open(f, encoding='utf-8') as fp:
                for line in fp:
                    line = line.strip()
                    if line.startswith('TUSHARE_TOKEN'):
                        return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise ValueError('未找到 TUSHARE_TOKEN')


def load_theme_map():
    path = os.path.join('..', 'cache_daily', 'theme_stock_map_v2_20260814.json')
    if not os.path.exists(path):
        path = os.path.join('cache_daily', 'theme_stock_map_v2_20260814.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    inv = {}
    for theme, stocks in data.get('themes', {}).items():
        for s in stocks:
            inv.setdefault(s['code'], []).append(theme)
    return inv


def collect_signals():
    """读取区间内全部信号"""
    signals = []
    for f in sorted(glob.glob(os.path.join('report_daily', 'rally_pullback_*.json'))):
        d = os.path.basename(f).replace('rally_pullback_', '').replace('.json', '')
        if not (TRACK_START <= d <= TRACK_END):
            continue
        with open(f, encoding='utf-8') as fp:
            data = json.load(fp)
        for s in data.get('signals', []):
            signals.append({
                'signal_date': d,
                'ts_code': s['ts_code'],
                'name': s['name'],
                'total_score': s.get('total_score', 0),
                'rally_amplitude': s.get('rally_amplitude', 0),
                'rally_limit_up_count': s.get('rally_limit_up_count', 0),
                'drawdown': s.get('drawdown', 0),
                'ref_price': s.get('ref_price'),
                'stop_loss': s.get('stop_loss'),
                'take_profit': s.get('take_profit'),
                'pullback_days': s.get('pullback_days'),
            })
    return signals


def main():
    cfg = yaml.safe_load(open(os.path.join('multi_factor_picker', 'config.yaml'), encoding='utf-8'))
    pro = ts.pro_api(get_token(cfg))
    theme_map = load_theme_map()
    signals = collect_signals()
    print(f"信号总数: {len(signals)}")
    if not signals:
        print('无信号')
        return

    # 拉取每只股票日线（一次）
    daily_cache = {}
    for sig in signals:
        code = sig['ts_code']
        if code in daily_cache:
            continue
        df = pro.daily(ts_code=code, start_date=TRACK_START, end_date=TRACK_END)
        if df is None or df.empty:
            daily_cache[code] = pd.DataFrame()
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        daily_cache[code] = df

    rows = []
    for sig in signals:
        code = sig['ts_code']
        df = daily_cache.get(code, pd.DataFrame())
        dates = df['trade_date'].tolist() if not df.empty else []
        if sig['signal_date'] not in dates:
            # 信号日可能是前复权/停牌差异，找最近一个 <= 信号日
            prev = [d for d in dates if d <= sig['signal_date']]
            sig_date = prev[-1] if prev else None
        else:
            sig_date = sig['signal_date']
        if sig_date is None or sig_date not in dates:
            rows.append({**sig, 'note': '无行情', 'buy': None})
            continue
        idx = dates.index(sig_date)
        after = df.iloc[idx + 1:]
        if after.empty:
            rows.append({**sig, 'note': '信号后无交易日', 'buy': None})
            continue
        buy_price = after.iloc[0]['open']
        rets = {}
        for n in (1, 2, 3, 5):
            if len(after) >= n:
                rets[f'ret_{n}d'] = (after.iloc[n - 1]['close'] / buy_price - 1) * 100
            else:
                rets[f'ret_{n}d'] = None
        rets['ret_latest'] = (after.iloc[-1]['close'] / buy_price - 1) * 100
        rets['max_ret'] = (after['high'].max() / buy_price - 1) * 100
        rets['min_ret'] = (after['low'].min() / buy_price - 1) * 100
        sl, tp = sig.get('stop_loss'), sig.get('take_profit')
        rets['hit_sl'] = bool(sl and after['low'].min() <= sl)
        rets['hit_tp'] = bool(tp and after['high'].max() >= tp)
        rets['buy_price'] = buy_price
        rets['note'] = 'OK'
        rows.append({**sig, **rets})

    # 汇总输出
    lines = []
    lines.append(f"# 主线第一次回调策略信号跟踪（{TRACK_START}~{TRACK_END}）")
    lines.append("")
    lines.append(f"- 信号总数: {len(signals)} 个（信号日去重标的: {len(set(s['ts_code'] for s in signals))} 只）")
    lines.append("- 统计口径: 信号日收盘确认 → 次日(T+1)开盘价买入 → 收盘收益")
    lines.append("")

    valid = [r for r in rows if r.get('note') == 'OK' and r.get('buy') is not None]
    if valid:
        latest_rets = [r['ret_latest'] for r in valid]
        win = [r for r in valid if r['ret_latest'] > 0]
        print(f"可交易信号: {len(valid)} 个，最新(截至{TRACK_END})平均收益 {sum(latest_rets)/len(latest_rets):.1f}%，"
              f"正收益 {len(win)}/{len(valid)} ({len(win)/len(valid)*100:.0f}%)")

    lines.append("| 信号日 | 标的 | 主题 | 总分 | 拉升 | 涨停 | 回撤 | 入场 | 止损 | T+1 | T+3 | T+5 | 最新 | 最高 | 最低 | 触止损 | 触止盈 |")
    lines.append("|--------|------|------|------|------|------|------|------|------|-----|-----|-----|------|------|------|--------|--------|")
    for r in sorted(rows, key=lambda x: x['signal_date']):
        th = '、'.join(theme_map.get(r['ts_code'], [])[:2]) or '-'
        fmt = lambda v: f"{v:+.1f}%" if v is not None else '-'
        sl_mark = '是' if r.get('hit_sl') else ''
        tp_mark = '是' if r.get('hit_tp') else ''
        if r.get('note') != 'OK':
            lines.append(f"| {r['signal_date']} | {r['name']}({r['ts_code']}) | {th} | {r['total_score']:.0f} | "
                         f"- | - | - | - | - | {r['note']} |")
            continue
        lines.append(
            f"| {r['signal_date']} | {r['name']}({r['ts_code']}) | {th} | {r['total_score']:.0f} | "
            f"+{r['rally_amplitude']*100:.0f}% | ×{r['rally_limit_up_count']} | {r['drawdown']*100:.0f}% | "
            f"{r['buy_price']:.2f} | {r.get('stop_loss','-')} | {fmt(r.get('ret_1d'))} | {fmt(r.get('ret_3d'))} | "
            f"{fmt(r.get('ret_5d'))} | {fmt(r.get('ret_latest'))} | {fmt(r.get('max_ret'))} | {fmt(r.get('min_ret'))} | "
            f"{sl_mark} | {tp_mark} |")

    lines.append("")
    # 分标的汇总
    lines.append("## 分标的汇总")
    lines.append("")
    for code in sorted(set(s['ts_code'] for s in signals)):
        sub = [r for r in rows if r['ts_code'] == code and r.get('note') == 'OK']
        if not sub:
            continue
        name = sub[0]['name']
        lines.append(f"### {name}({code})")
        lines.append(f"- 信号次数: {len([s for s in signals if s['ts_code']==code])} 次 "
                     f"({', '.join(s['signal_date'] for s in signals if s['ts_code']==code)})")
        for r in sub:
            lines.append(f"- {r['signal_date']} 信号: 总分{r['total_score']:.0f}, T+1={r.get('ret_1d', '-')}, "
                         f"最新={r.get('ret_latest', '-')}, 最高={r.get('max_ret', '-')}, 最低={r.get('min_ret', '-')}")
        lines.append("")

    out = '\n'.join(lines)
    out_path = os.path.join('report_daily', 'rp_track_0804_0814.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(out)
    print(out)
    print(f"\n✅ 跟踪报告: {out_path}")


if __name__ == '__main__':
    main()
