# -*- coding: utf-8 -*-
"""
V10.1 历史回放验证
=========================================
★ 严格按时间顺序切分：参数只用样本内确定，样本外只跑不调。
★ 使用当时可获得的数据（不引入未来函数）。
★ 覆盖 2026-07-24 ~ 2026-10-30 的 64 个交易日（有主题数据的区间）。

输出：
  1) 状态分布与切换频率
  2) 各状态下的实际收益 / 最大回撤 / 仓位利用率 / 交易频率
  3) 与简单基准对比
  4) 样本内/样本外切分
  5) 满仓授权触发次数
"""
import os
import sys
import json
import sqlite3
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

import market_regime_v101 as V101
import market_regime_v10 as v10

PACK = None      # 预计算的历史序列，注入 V10.0 避免逐日重复扫库

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, '_bt_out')
os.makedirs(OUT_DIR, exist_ok=True)


def get_index_series(code: str, start: str, end: str) -> dict:
    """取指数收盘序列 {date: close}"""
    db = os.path.join(os.path.dirname(BASE_DIR), 'cache_daily', 'stock_data.db')
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache "
        "WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        conn, params=(code, start, end))
    conn.close()
    return dict(zip(df['trade_date'], df['close']))


def get_theme_dates() -> list:
    """主题数据覆盖的交易日（作为回放样本范围）"""
    db = os.path.join(BASE_DIR, 'report_daily', 'theme_scores.db')
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date").fetchall()
    conn.close()
    return [r[0] for r in rows]


def precompute_hist(dates: list) -> dict:
    """一次性预计算广度/成交额/涨跌停序列，避免逐日重复扫全表。

    ★ 口径与 V10.0 的 _collect_breadth_history / _collect_amount_history /
      _collect_limit_history 完全一致（同样是 daily_cache 日线聚合）。
    """
    db = os.path.join(os.path.dirname(BASE_DIR), 'cache_daily', 'stock_data.db')
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 30000")
    breadth, amount, dt = {}, {}, {}
    up_c, dn_c = {}, {}
    for d in dates:
        df = pd.read_sql_query(
            "SELECT ts_code, pct_chg, amount FROM daily_cache WHERE trade_date = ?",
            conn, params=(d,))
        if df.empty:
            continue
        up = int((df['pct_chg'] > 0).sum())
        dn = int((df['pct_chg'] < 0).sum())
        up_c[d], dn_c[d] = up, dn
        breadth[d] = up / (up + dn) * 100.0 if (up + dn) else 0.0
        amount[d] = float(df['amount'].sum()) / 100000.0    # 千元 → 亿元
        # 跌停（10%/20% 幅度，ST 5% 无法识别 → 记为下限）
        thr = df['ts_code'].map(
            lambda c: 20.0 if str(c)[:3] in ('300', '301', '688') else 10.0)
        dt[d] = int((df['pct_chg'] <= -thr * 0.99).sum())
    conn.close()
    return {"breadth": breadth, "amount": amount, "dt": dt,
            "up": up_c, "down": dn_c}


def main():
    cfg = V101.load_config()
    v10cfg = v10.load_config()

    dates = get_theme_dates()
    print(f"回放区间：{dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日")
    print("预计算历史序列（一次扫库）…")
    t0 = datetime.datetime.now()
    hist_pack = precompute_hist(dates)
    globals()['PACK'] = hist_pack
    print(f"  ✓ 完成，耗时 {(datetime.datetime.now()-t0).seconds}s\n")

    # 指数序列（用于计算后续收益）
    zz2000 = get_index_series('932000.CSI', dates[0], dates[-1])
    sh = get_index_series('000001.SH', dates[0], dates[-1])
    all_dates = sorted(zz2000.keys())

    def fwd(series, d, h):
        """d 之后第 h 个交易日的收益（%）"""
        if d not in series:
            return None
        try:
            i = all_dates.index(d)
        except ValueError:
            return None
        if i + h >= len(all_dates):
            return None
        a, b = series[d], series[all_dates[i + h]]
        if not a:
            return None
        return (b - a) / a * 100

    rows = []
    prev_state = None
    prev_snap = None

    # 让 V10.0 的 collect_market_snapshot 使用预计算的历史（避免重复扫库）
    _orig_collect = v10.collect_market_snapshot

    def _collect_fast(trade_date, c, prev_snapshot=None, hist_pack=None):
        return _orig_collect(trade_date, c, prev_snapshot, hist_pack or PACK)

    v10.collect_market_snapshot = _collect_fast
    # V101 内部通过 `import market_regime_v10 as v10` 取模块，
    # 直接改模块属性即可生效。

    # ★ 状态链：把前一交易日的状态/待确认计数传给状态机，
    #   否则 confirm_days 确认机制永远不生效（每日独立计算 → 切换率虚高）。
    chain = {"state": None, "consecutive_days": 0,
             "pending_target": "", "pending_days": 0}

    for d in dates:
        try:
            _orig_load = v10.load_prev_state

            def _load_prev(td, _c=chain):
                return dict(_c) if _c.get("state") else None

            v10.load_prev_state = _load_prev
            try:
                r = V101.analyze_trade_date_v101(d, cfg, None)
            finally:
                v10.load_prev_state = _orig_load

            # 更新状态链
            chain = {
                "state": r.state,
                "consecutive_days": r.state_days,
                "pending_target": getattr(r.v10_decision, "pending_target", "") or "",
                "pending_days": getattr(r.v10_decision, "pending_days", 0) or 0,
            }
        except Exception as e:
            print(f"  {d} 失败: {e}")
            continue

        s = r.snapshot
        rows.append({
            'trade_date': d,
            'state': r.state,
            'state_cn': r.state_cn,
            'authority_max': r.authority.max_position,
            'suggested': r.target.suggested,
            'qualified': r.target.qualified_count,
            'observe': r.target.observe_count,
            'auth': r.authorization.result,
            'portfolio_risk': r.portfolio.total_risk_pct if r.portfolio else None,
            'portfolio_ok': r.portfolio.ok if r.portfolio else None,
            'exit_check': r.exit_check.result if r.exit_check else None,
            'm_index': r.strong.modules['指数趋势'].passed,
            'm_breadth': r.strong.modules['市场广度'].passed,
            'm_money': r.strong.modules['短线赚钱效应'].passed,
            'm_mainline': r.strong.modules['主线持续性'].passed,
            'strong_passed': r.strong.passed_count,
            'breadth': s.breadth.breadth_ratio,
            'zt': s.limit.zt_count,
            'dt': s.limit.dt_count,
            'broken_rate': s.limit.broken_rate,
            'fwd1_zz': fwd(zz2000, d, 1),
            'fwd3_zz': fwd(zz2000, d, 3),
            'fwd5_zz': fwd(zz2000, d, 5),
            'fwd1_sh': fwd(sh, d, 1),
            'fwd5_sh': fwd(sh, d, 5),
        })
        prev_state = r.state
        if len(rows) % 10 == 0:
            print(f"  已回放 {len(rows)}/{len(dates)}")

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_DIR, 'v101_replay.csv'), index=False)
    print(f"\n✓ 回放数据已存 _bt_out/v101_replay.csv（{len(df)} 行）\n")

    # ---------- 1) 状态分布 ----------
    print("=" * 74)
    print("【1】状态分布")
    print("=" * 74)
    vc = df['state'].value_counts()
    for st in ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']:
        n = int(vc.get(st, 0))
        if n == 0 and st not in df['state'].values:
            print(f"  {st}: 0 天  ({n/len(df)*100:>5.1f}%)  ← 未触发")
        else:
            print(f"  {st}: {n:>3} 天  ({n/len(df)*100:>5.1f}%)")
    switches = (df['state'] != df['state'].shift()).sum() - 1
    print(f"\n  状态切换：{switches} 次 / {len(df)} 天 = {switches/len(df)*100:.1f}%")

    # ---------- 2) 各状态表现 ----------
    print("\n" + "=" * 74)
    print("【2】各状态下的后续表现（中证2000）")
    print("=" * 74)
    print(f"  {'状态':<6}{'N':>5}{'fwd1均':>9}{'fwd1胜':>8}{'fwd5均':>9}{'fwd5胜':>8}{'建议仓位均':>11}")
    for st in sorted(df['state'].unique()):
        g = df[df['state'] == st]
        f1 = g['fwd1_zz'].dropna()
        f5 = g['fwd5_zz'].dropna()
        print(f"  {st:<6}{len(g):>5}{f1.mean():>9.2f}{(f1>0).mean()*100:>7.1f}%"
              f"{f5.mean():>9.2f}{(f5>0).mean()*100:>7.1f}%{g['suggested'].mean():>10.1f}%")

    # ---------- 3) 仓位建议 vs 实际 ----------
    print("\n" + "=" * 74)
    print("【3】仓位建议的分布")
    print("=" * 74)
    print(f"  建议仓位：均值 {df['suggested'].mean():.1f}%  最大 {df['suggested'].max()}%  "
          f"最小 {df['suggested'].min()}%")
    print(f"  仓位利用率（建议/权限）：{(df['suggested']/df['authority_max'].replace(0,np.nan)).mean()*100:.1f}%")
    print(f"  平均合格标的数：{df['qualified'].mean():.1f} 只")
    print(f"  满仓授权：{df['auth'].value_counts().to_dict()}")
    print(f"  退出检查：{df['exit_check'].value_counts().to_dict()}")

    # ---------- 4) 策略模拟 ----------
    print("\n" + "=" * 74)
    print("【4】策略模拟（信号当日收盘 → 次日收益，仓位按建议比例）")
    print("=" * 74)

    def simulate(positions, label, bench=None):
        pos = positions / 100.0
        f1 = df['fwd1_zz'] / 100.0
        valid = ~np.isnan(pos) & ~np.isnan(f1)
        p = (pos[valid] * f1[valid]).values
        cum = (1 + p).prod() - 1
        vol = p.std() * np.sqrt(252) if len(p) > 1 else 0
        sharpe = (p.mean() / p.std() * np.sqrt(252)) if p.std() > 0 else 0
        eq = np.cumprod(1 + p)
        peak = np.maximum.accumulate(eq)
        dd = (eq / peak - 1).min()
        win = (p > 0).mean() * 100
        print(f"  {label:<26} 累计 {cum*100:>8.2f}%  年化波动 {vol*100:>6.2f}%  "
              f"Sharpe {sharpe:>6.2f}  回撤 {dd*100:>7.2f}%  胜率 {win:>5.1f}%  "
              f"交易{len(p)}天")
        return cum, sharpe, dd

    simulate(df['suggested'].values, "V10.1 建议仓位")
    simulate(df['authority_max'].values, "仅用仓位权限(上限)")
    simulate(np.full(len(df), 60.0), "固定60%仓位")
    simulate(np.full(len(df), 100.0), "满仓持有")
    simulate(np.full(len(df), 30.0), "半仓持有")
    # 基准：广度<35%空仓
    bench = np.where(df['breadth'] < 0.35, 0.0, 100.0)
    simulate(bench, "基准(广度<35%空仓)")

    # ---------- 5) 样本内/样本外切分 ----------
    print("\n" + "=" * 74)
    print("【5】时间顺序样本切分（前70%训练 / 后30%样本外）")
    print("=" * 74)
    split = int(len(df) * 0.7)
    is_oos = df['trade_date'].values > df['trade_date'].iloc[split]
    print(f"  样本内：{df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[split-1]}（{split} 天）")
    print(f"  样本外：{df['trade_date'].iloc[split]} ~ {df['trade_date'].iloc[-1]}（{len(df)-split} 天）")
    for name, mask in [('样本内', ~is_oos), ('样本外', is_oos)]:
        sub = df[mask]
        print(f"\n  --- {name} ---")
        vc2 = sub['state'].value_counts()
        print("    状态分布：" + "、".join(
            f"{k} {int(vc2.get(k,0))}天" for k in sorted(sub['state'].unique())))
        f1 = sub['fwd1_zz'].dropna()
        print(f"    建议仓位均值 {sub['suggested'].mean():.1f}%")
        if len(f1) > 3:
            pos = (sub['suggested'] / 100.0)[~np.isnan(sub['fwd1_zz'])]
            rets = (sub['fwd1_zz'] / 100.0)[~np.isnan(sub['fwd1_zz'])]
            strat = pos * rets
            cum = (1 + strat).prod() - 1
            bh = (1 + rets).prod() - 1
            print(f"    策略累计 {cum*100:.2f}%  基准(次日涨跌) {bh*100:.2f}%")

    # ---------- 6) 满仓授权触发 ----------
    print("\n" + "=" * 74)
    print("【6】满仓授权触发情况")
    print("=" * 74)
    allowed = df[df['auth'] == 'FULL_ALLOWED']
    print(f"  FULL_ALLOWED：{len(allowed)} 天")
    if len(allowed):
        print(f"    这些日期的 fwd5 均值 {allowed['fwd5_zz'].dropna().mean():.2f}%  "
              f"胜率 {(allowed['fwd5_zz'].dropna()>0).mean()*100:.1f}%")
    else:
        print("    ⚠ 全样本从未授权满仓 —— 说明十项条件在历史上从未同时满足")
        print("    （这本身是重要结论：满仓机制非常保守）")

    # ---------- 7) 四模块单独表现 ----------
    print("\n" + "=" * 74)
    print("【7】四模块单独通过时的后续表现（验证每个模块是否有区分度）")
    print("=" * 74)
    for m in ['m_index', 'm_breadth', 'm_money', 'm_mainline']:
        ok = df[df[m] == True]
        ng = df[df[m] == False]
        fo = ok['fwd5_zz'].dropna()
        fn = ng['fwd5_zz'].dropna()
        diff = f"{(fo>0).mean()*100:.1f}%" if len(fo) else "NA"
        diffn = f"{(fn>0).mean()*100:.1f}%" if len(fn) else "NA"
        print(f"  {m:<12} 通过 {len(ok):>3} 天 fwd5胜率 {diff:>7}  "
              f"未通过 {len(ng):>3} 天 fwd5胜率 {diffn:>7}  "
              f"差 {((fo>0).mean()-(fn>0).mean())*100 if len(fo) and len(fn) else 0:>6.1f}pp")

    # ---------- 8) 结论 ----------
    print("\n" + "=" * 74)
    print("【8】结论")
    print("=" * 74)
    print("  ★ 满仓授权机制：规则已实现，但**效果尚未验证**。")
    if len(allowed) == 0:
        print("    全样本 0 次授权 —— 无任何样本外证据支持其有效性，")
        print("    也无法评估其是否过严或过松。需要更长历史与真实账户回放。")
    else:
        print(f"    全样本授权 {len(allowed)} 次，样本量不足以做统计推断。")
    print("  ★ 状态机切换频率、仓位建议与后续收益的关系需按时间顺序切分后评估，")
    print("    不能用全样本调参后再宣称有效。")
    print("  ★ 本模块输出的是**建议**，不执行任何交易。")


if __name__ == '__main__':
    main()
