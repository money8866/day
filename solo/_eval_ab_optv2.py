#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P0 位置修正 A/B 回测（旧引擎 vs 开启 OPT_V2 的近似重放）

旧序列：market_analysis.db overall_analysis.total_position（V9.9 实盘产出）
新序列：以旧 position 为「当日目标」代理，叠加位置修正（深跌+10/高位-10/赶顶封顶25），
        按 V9.9 限速规则（加+8/减-15/维持±5）链式递推。

边界声明（诚实清单）：
  1. DB position 是旧引擎「限速后」的输出，用作 target 代理会低估修正效果（一阶近似）
  2. 未重建 theme/limit_stats 等日度输入，action 用 diff 方向近似
  3. 样本仅 2026-06~10（89 天，唯一有完整 bundle 的窗口），上行段 19 天
  4. 收益按 T+1 收盘执行，未计成本
"""
import sqlite3
import pandas as pd
import numpy as np
from io import StringIO

CACHE_DB = 'D:/mystock/solo/cache_backbone_tushare/cache.db'
MA_DB = 'D:/mystock/solo/cache_backbone_tushare/market_analysis.db'

def load_series():
    """合并 hist_*（2024-2025）与 tsc_index_kline（2026）→ 完整日线"""
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_index_kline%'")
    kline = cur.fetchall()
    out = {}
    for code in ['000001.SH', '932000.CSI']:
        t = f'hist_{code.replace(".", "_")}'
        d = {}
        try:
            df = pd.read_sql_query(f"SELECT trade_date, close, high FROM [{t}]", conn)
            for _, r in df.iterrows():
                d[str(r['trade_date'])] = (float(r['close']), float(r['high']))
        except Exception:
            pass
        for key, data in kline:
            if f'ts_code_{code}_' not in key:
                continue
            df = pd.read_csv(StringIO(data))
            last = df.iloc[-1]
            d[str(last['trade_date'])] = (float(last['close']), float(last['high']))
        out[code] = pd.DataFrame(
            [(v[0], v[1]) for v in d.values()],
            index=pd.Index(sorted(d.keys()), name='trade_date'),
            columns=['close', 'high']).sort_index()
    conn.close()
    return out

def load_advice():
    conn = sqlite3.connect(MA_DB)
    df = pd.read_sql_query(
        "SELECT trade_date, total_position, trend_score FROM overall_analysis ORDER BY trade_date", conn)
    conn.close()
    df['trade_date'] = df['trade_date'].astype(str)
    return df

def drawdown_60(sh):
    closes = sh['close'].astype(float)
    highs = sh['high'].astype(float)
    dd = pd.Series(index=sh.index, dtype=float)
    for i in range(len(sh)):
        if i < 59:
            dd.iloc[i] = np.nan
        else:
            dd.iloc[i] = 1 - closes.iloc[i] / highs.iloc[max(0, i - 59):i + 1].max()
    return dd

def replay(advice, sh, opt):
    """链式重放新引擎 position 序列"""
    dd = drawdown_60(sh)
    new_pos = {}
    prev = None
    for _, row in advice.iterrows():
        d = row['trade_date']
        if d not in dd.index or pd.isna(dd.loc[d]):
            continue
        target = float(row['total_position'])
        d_dd = dd.loc[d]
        # 位置修正（apply_adj 部分；与 apply_opt_v2_gate 逻辑保持一致）
        if opt['enabled']:
            if (opt.get('deep_add_enabled', False)
                    and d_dd > opt['deep_dd']):
                target += opt['deep_bonus']
            elif d_dd < opt['high_dd']:
                target -= opt['high_penalty']
            # 赶顶封顶（trend_score>=80 且 dd<3% 代理 STRONG_TREND 条件）
            if row['trend_score'] is not None and pd.notna(row['trend_score']) \
                    and row['trend_score'] >= 80 and d_dd < opt['top_cap_dd']:
                target = min(target, opt['top_cap'])
        target = max(0, min(90, target))
        # 限速复刻
        if prev is None:
            p = target
        else:
            diff = target - prev
            if diff > 0:
                p = min(target, prev + 8)
            elif diff < -15:
                p = max(0, target)
            elif diff < 0:
                p = max(prev - 15, target)
            else:
                p = target
            if abs(diff) <= 5:
                p = max(prev - 5, min(prev + 5, target))
        new_pos[d] = p
        prev = p
    return pd.Series(new_pos)

def evaluate(pos_series, zz, advice_dates):
    """按 position 序列计算组合表现"""
    dates = list(zz.index)
    recs = []
    for i, d in enumerate(dates[:-1]):
        if d not in pos_series.index:
            continue
        ret = zz.iloc[i + 1] / zz.iloc[i] - 1
        recs.append({'d': d, 'pos': float(pos_series.loc[d]), 'ret': ret * 100})
    r = pd.DataFrame(recs)
    if r.empty:
        return None
    # 未来5日收益
    r5 = []
    for i, d in enumerate(dates[:-5]):
        if d in pos_series.index:
            r5.append((d, (zz.iloc[i + 5] / zz.iloc[i] - 1) * 100, float(pos_series.loc[d])))
    r5df = pd.DataFrame(r5, columns=['d', 'r5', 'pos'])
    r5df['env'] = pd.cut(r5df['r5'], [-100, -3, 3, 100], labels=['下行', '震荡', '上行'])
    strat = r['pos'] / 100 * r['ret'] / 100
    cum = (1 + strat).cumprod()
    bench = (1 + r['ret'] / 100).cumprod()
    return {
        'ret': (cum.iloc[-1] - 1) * 100,
        'bench_ret': (bench.iloc[-1] - 1) * 100,
        'dd': (cum / cum.cummax() - 1).min() * 100,
        'avg_pos': r['pos'].mean(),
        'ic5': r5df['pos'].corr(r5df['r5'], method='spearman'),
        'r5': r5df,
    }

BASE_OPT = {'enabled': False, 'deep_dd': 0.10, 'high_dd': 0.05, 'deep_bonus': 10,
            'high_penalty': 10, 'top_cap_dd': 0.03, 'top_cap': 25,
            'deep_add_enabled': False}
# 三态预设：与 position_engine_v95.OPT_V2_PRESETS 对齐
PRESETS = {
    'off': dict(BASE_OPT, enabled=False, deep_add_enabled=False),
    'A':   dict(BASE_OPT, enabled=True,  deep_add_enabled=False),  # 高位防守（默认）
    'B':   dict(BASE_OPT, enabled=True,  deep_add_enabled=True),   # 全开（含深跌加仓）
}


def main():
    series = load_series()
    zz = series['932000.CSI']['close']
    sh = series['000001.SH']
    advice = load_advice()

    old_pos = advice.set_index('trade_date')['total_position'].astype(float)
    old_e = evaluate(old_pos, zz, None)

    results = {}
    for name, opt in PRESETS.items():
        new_pos = replay(advice, sh, opt)
        results[name] = (new_pos, evaluate(new_pos, zz, None))

    new_pos = results['A'][0]
    new_e = results['A'][1]

    print("=" * 92)
    print("P0 位置修正 三态回测（2026-06-02 ~ 2026-10-16，中证2000，T+1收盘执行）")
    print("  off=旧引擎  A=高位防守(高位减仓+赶顶封顶)  B=A+深跌加仓")
    print("=" * 92)
    print(f"{'指标':<22}{'off(旧)':>12}{'A 高位防守':>14}{'B 全开':>12}{'A-off':>10}{'B-A':>10}")
    for label, key, fmt in [('累计收益', 'ret', '.2f'), ('最大回撤', 'dd', '.2f'),
                            ('平均仓位', 'avg_pos', '.1f')]:
        o = old_e[key]; a = results['A'][1][key]; b = results['B'][1][key]
        so = format(o, '11' + fmt) + '%'
        sa = format(a, '13' + fmt) + '%'
        sb = format(b, '11' + fmt) + '%'
        da = format(a - o, '+9' + fmt) + '%'
        db = format(b - a, '+9' + fmt) + '%'
        print(f"{label:<22}{so:>12}{sa:>14}{sb:>12}{da:>10}{db:>10}")
    o, a, b = old_e['ic5'], results['A'][1]['ic5'], results['B'][1]['ic5']
    print(f"{'未来5日IC(Spearman)':<22}{o:>12.4f}{a:>14.4f}{b:>12.4f}{a - o:>+10.4f}{b - a:>+10.4f}")
    bo, ba, bb = old_e['bench_ret'], results['A'][1]['bench_ret'], results['B'][1]['bench_ret']
    print(f"{'满仓基准收益':<22}{bo:>11.2f}%{ba:>13.2f}%{bb:>11.2f}%")

    print("\n" + "=" * 92)
    print("分环境（按未来5日中证2000涨跌）：仓位与收益贡献")
    print("=" * 92)
    print(f"{'环境':<8}{'off均仓':>9}{'A均仓':>9}{'B均仓':>9}{'off>=50%':>10}{'A>=50%':>9}{'B>=50%':>9}")
    for env in ('上行', '震荡', '下行'):
        cells = []
        for name in ('off', 'A', 'B'):
            r5 = results[name][1]['r5'] if name != 'off' else old_e['r5']
            s = r5[r5['env'] == env]
            cells.append((s['pos'].mean(), len(s[s['pos'] >= 50])))
        print(f"{env:<8}{cells[0][0]:>8.1f}%{cells[1][0]:>8.1f}%{cells[2][0]:>8.1f}%"
              f"{cells[0][1]:>8}天{cells[1][1]:>7}天{cells[2][1]:>7}天")

    print("\n" + "=" * 92)
    print("A 与 B 差异说明")
    print("=" * 92)
    dd = drawdown_60(sh)
    adv_dates = [d for d in advice['trade_date'] if d in dd.index and not pd.isna(dd.loc[d])]
    n_deep = sum(1 for d in adv_dates if dd.loc[d] > PRESETS['B']['deep_dd'])
    n_high = sum(1 for d in adv_dates if dd.loc[d] < PRESETS['B']['high_dd'])
    print(f"  89 个建议日中：高位触发 {n_high} 天（A/B 均生效），深跌触发 {n_deep} 天（仅 B 生效）")
    if n_deep == 0:
        print("  → 本样本深跌触发为 0，A 与 B 的仓位序列与回测结果完全相同；")
        print("    B 的增量依据是 575 日指数统计（深跌未来5日 +1.088%/胜率62.6%, p=0.0043），")
        print("    在本样本不可实测，属『样本外信念』。")

    print("\n" + "=" * 92)
    print("仓位调整明细对比（仅列出 A 与 off 差异>10pp的日期）")
    print("=" * 92)
    print(f"{'日期':<10}{'off仓位':>8}{'A仓位':>8}{'B仓位':>8}{'trend_score':>11}{'距60日高点回撤':>13}")
    b_pos = results['B'][0]
    for d in new_pos.index:
        if d not in old_pos.index:
            continue
        diff = new_pos.loc[d] - old_pos.loc[d]
        if abs(diff) > 10:
            ts = advice[advice['trade_date'] == d]['trend_score'].iloc[0]
            print(f"{d:<10}{old_pos.loc[d]:>7.0f}%{new_pos.loc[d]:>7.0f}%{b_pos.loc[d]:>7.0f}%"
                  f"{ts:>11.1f}{dd.loc[d] * 100:>12.1f}%")

    # 赶顶案例
    print("\n" + "=" * 92)
    print("赶顶案例 20260622（trend_score=91.3, 回撤≈0%）")
    print("=" * 92)
    if '20260622' in old_pos.index:
        print(f"  off 仓位: {old_pos.loc['20260622']:.0f}%   A 仓位: {new_pos.loc['20260622']:.0f}%"
              f"   B 仓位: {b_pos.loc['20260622']:.0f}%")
        zz_dates = list(zz.index)
        if '20260622' in zz_dates:
            i = zz_dates.index('20260622')
            r5 = (zz.iloc[i + 5] / zz.iloc[i] - 1) * 100
            print(f"  该位置未来5日中证2000实际收益: {r5:+.2f}%")

if __name__ == '__main__':
    main()
