#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
仓位算法合理性回测（基于真实大盘指数 + 全市场情绪数据）

检验三个问题：
1. 趋势分 / 情绪分 与「未来N日实际收益」有没有相关性？（信号有效性）
2. 仓位分档与后续收益是否单调？（仓位档位是否有效）
3. 各风控闸门（广度一票否决 / 关键低点跌破 / 炸板率）拦下的日子，后续是否真的更差？
   如果被拦下的日子后续反而涨了 → 闸门过度防守，损失收益。
"""
import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, 'cache_backbone_tushare')
sys.path.insert(0, BASE)

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(BASE), 'config', '.env'))

import tushare as ts
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

OUT = os.path.join(BASE, '_bt_out')
os.makedirs(OUT, exist_ok=True)

INDEXES = {
    '上证指数': '000001.SH',
    '沪深300': '000300.SH',
    '中证2000': '932000.CSI',
    '创业板指': '399006.SZ',
    '科创50': '000688.SH',
    '中证1000': '000852.SH',
}


def load_index(code, start='20260101', end='20261031'):
    """拉指数日线，带本地缓存"""
    cp = os.path.join(CACHE, f'bt_idx_{code}_{start}.csv')
    if os.path.exists(cp):
        df = pd.read_csv(cp, dtype={'trade_date': str})
        return df.sort_values('trade_date').reset_index(drop=True)
    df = pro.index_daily(ts_code=code, start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame()
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date').reset_index(drop=True)
    df.to_csv(cp, index=False)
    import time
    time.sleep(0.4)
    return df


def load_market_breadth(start='20260101', end='20261031'):
    """全市场每日涨跌家数 / 涨停跌停 —— 用于验证广度闸门"""
    cp = os.path.join(CACHE, 'bt_breadth.csv')
    if os.path.exists(cp):
        df = pd.read_csv(cp, dtype={'trade_date': str})
        return df.sort_values('trade_date').reset_index(drop=True)

    cal = pro.trade_cal(exchange='', start_date=start, end_date=end)
    days = sorted(cal[cal['is_open'] == 1]['cal_date'].tolist())
    rows = []
    for d in days:
        try:
            df = pro.daily(trade_date=d)
        except Exception:
            df = None
        if df is None or df.empty:
            continue
        up = int((df['pct_chg'] > 0).sum())
        down = int((df['pct_chg'] < 0).sum())
        total = len(df)
        # 涨跌停（收盘口径）
        is20 = df['ts_code'].str.startswith(('688', '301', '300'))
        lu = df['limit_up'] = np.where(is20, 19.8, 9.8)
        zt = int(((df['pct_chg'] >= lu) & (df['pct_chg'] < lu + 0.3)).sum())
        dt = int(((df['pct_chg'] <= -lu) & (df['pct_chg'] > -lu - 0.3)).sum())
        rows.append({
            'trade_date': d, 'total': total, 'up': up, 'down': down,
            'up_ratio': round(up / total * 100, 1) if total else 0,
            'zt': zt, 'dt': dt,
            'amount_yi': round(df['amount'].sum() / 100000, 0),
            'mean_pct': round(df['pct_chg'].mean(), 3),
            'median_pct': round(df['pct_chg'].median(), 3),
        })
        import time
        time.sleep(0.35)
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(cp, index=False)
    return df


def fwd_returns(idx_df, horizons=(1, 3, 5, 10)):
    """计算未来N日收益（以中证2000为小盘代表，另给出上证）"""
    d = idx_df.sort_values('trade_date').reset_index(drop=True)
    close = d['close'].astype(float)
    out = {}
    for h in horizons:
        out[f'fwd{h}'] = (close.shift(-h) / close - 1) * 100
    out['trade_date'] = d['trade_date']
    return pd.DataFrame(out)


def spearman(a, b):
    a = pd.Series(a).astype(float)
    b = pd.Series(b).astype(float)
    m = a.notna() & b.notna()
    if m.sum() < 8:
        return np.nan, np.nan
    ra = a[m].rank()
    rb = b[m].rank()
    r = ra.corr(rb)
    return r, m.sum()


def main():
    print('=' * 78)
    print('仓位算法合理性回测 — 基于真实指数与全市场情绪数据')
    print('=' * 78)

    # ---------- 1. 加载数据 ----------
    idx = {}
    for name, code in INDEXES.items():
        df = load_index(code)
        if df.empty:
            print(f'  [WARN] {name} 无数据')
            continue
        idx[name] = df
        print(f'  指数 {name}: {len(df)} 根K线  {df["trade_date"].min()}~{df["trade_date"].max()}')

    print('\n  拉取全市场每日广度（涨跌家数/涨跌停/成交额）...')
    breadth = load_market_breadth()
    print(f'  全市场: {len(breadth)} 个交易日  '
          f'{breadth["trade_date"].min()}~{breadth["trade_date"].max()}')

    # ---------- 2. 组装主面板 ----------
    panel = breadth.copy()
    for name, df in idx.items():
        fr = fwd_returns(df)
        fr = fr.rename(columns={c: f'{name}_{c}' for c in
                                ['fwd1', 'fwd3', 'fwd5', 'fwd10']})
        panel = panel.merge(fr[['trade_date'] + [c for c in fr.columns if c != 'trade_date']],
                            on='trade_date', how='left')
    # 当日指数涨跌
    for name, df in idx.items():
        d = df[['trade_date', 'pct_chg']].rename(columns={'pct_chg': f'{name}_ret1'})
        panel = panel.merge(d, on='trade_date', how='left')

    # ---------- 3. 读取引擎历史输出 ----------
    conn = sqlite3.connect(os.path.join(CACHE, 'market_analysis.db'))
    ia = pd.read_sql_query(
        "SELECT trade_date,index_name,trend_score,trend_status,sentiment_score,"
        "sentiment_status,pct_change FROM index_analysis", conn)
    oa = pd.read_sql_query(
        "SELECT trade_date,total_position,trend_score,index_trend,theme_trend,"
        "market_status FROM overall_analysis", conn)
    ls = pd.read_sql_query(
        "SELECT trade_date,zt_count,dt_count,broken_rate,zhaban_count,up_ratio,"
        "down_ratio,max_limit_height FROM limit_stats", conn)
    conn.close()

    # 指数趋势分：取三指数均值（与 calculate_market_trend_score 的加权不同，这里做稳健性检查）
    piv = ia.pivot_table(index='trade_date', columns='index_name',
                          values='trend_score')
    piv.columns = [f'ts_{c}' for c in piv.columns]
    panel = panel.merge(piv.reset_index(), on='trade_date', how='left')

    piv_s = ia.pivot_table(index='trade_date', columns='index_name',
                           values='sentiment_score')
    piv_s.columns = [f'ss_{c}' for c in piv_s.columns]
    panel = panel.merge(piv_s.reset_index(), on='trade_date', how='left')

    panel = panel.merge(oa.rename(columns={
        'trend_score': 'eng_trend', 'total_position': 'eng_position',
        'index_trend': 'eng_index_trend', 'theme_trend': 'eng_theme_trend',
        'market_status': 'eng_status'}), on='trade_date', how='left')
    panel = panel.merge(ls.rename(columns={
        'broken_rate': 'zb_rate', 'zt_count': 'zt2', 'dt_count': 'dt2',
        'up_ratio': 'ls_up_ratio'}), on='trade_date', how='left')

    # 主基准：中证2000 fwd 收益（短线小盘）
    panel['fwd_ret'] = panel.get('中证2000_fwd5')
    panel.to_csv(os.path.join(OUT, 'panel.csv'), index=False)
    print(f'\n  主面板: {len(panel)} 行 × {len(panel.columns)} 列 → _bt_out/panel.csv')

    # ---------- 4. 检验 1：趋势分/情绪分的信号有效性 ----------
    print('\n' + '=' * 78)
    print('检验 1：趋势分 / 情绪分 与未来收益的相关性（Spearman）')
    print('=' * 78)
    sub = panel[panel['trade_date'] >= '20260520'].copy()
    print(f'  样本: {len(sub)} 个交易日 (20260520 起，引擎有输出区间)\n')

    sig_cols = [c for c in sub.columns
                if c.startswith(('ts_', 'ss_', 'eng_'))
                and pd.api.types.is_numeric_dtype(sub[c])]
    print(f'  {"信号":<28} {"fwd1":>9} {"fwd3":>9} {"fwd5":>9} {"fwd10":>9} {"N":>5}')
    corr_rows = []
    for c in sorted(sig_cols):
        line = f'  {c:<28}'
        vals = []
        for h in (1, 3, 5, 10):
            r, n = spearman(sub[c], sub[f'中证2000_fwd{h}'])
            vals.append(r)
            line += f'{r:>9.3f}' if not np.isnan(r) else f'{"NA":>9}'
        line += f'{n:>5}'
        print(line)
        corr_rows.append({'signal': c, **{f'fwd{h}': vals[i] for i, h in enumerate((1, 3, 5, 10))}})

    # ---------- 5. 检验 2：仓位分档单调性 ----------
    print('\n' + '=' * 78)
    print('检验 2：建议仓位分档 vs 后续 5 日实际收益（单调性）')
    print('=' * 78)
    ps = sub[sub['eng_position'].notna()].copy()
    if len(ps):
        bins = [(-1, 0, '0%(空仓)'), (0, 5, '1-5%'), (5, 15, '6-15%'),
                (15, 30, '16-30%'), (30, 50, '31-50%'), (50, 101, '>50%')]
        print(f'  {"仓位档":<12} {"天数":>5} {"平均fwd5":>10} {"中位fwd5":>10} {"平均fwd1":>10} {"胜率":>7}')
        mono = []
        for lo, hi, label in bins:
            g = ps[(ps['eng_position'] > lo) & (ps['eng_position'] <= hi)]
            if len(g) == 0:
                print(f'  {label:<12} {0:>5} {"-":>10} {"-":>10} {"-":>10} {"-":>7}')
                continue
            f5 = g['中证2000_fwd5'].dropna()
            f1 = g['中证2000_fwd1'].dropna()
            win = (f5 > 0).mean() * 100 if len(f5) else np.nan
            print(f'  {label:<12} {len(g):>5} {f5.mean():>10.2f} {f5.median():>10.2f} '
                  f'{f1.mean():>10.2f} {win:>6.1f}%')
            mono.append({'bin': label, 'n': len(g),
                         'mean_fwd5': round(f5.mean(), 2) if len(f5) else None,
                         'median_fwd5': round(f5.median(), 2) if len(f5) else None,
                         'win_rate': round(win, 1) if len(f5) else None})
        json.dump(mono, open(os.path.join(OUT, 'position_bins.json'), 'w'),
                  ensure_ascii=False, indent=2)

    # ---------- 6. 检验 3：各风控闸门的「拦对了吗」 ----------
    print('\n' + '=' * 78)
    print('检验 3：风控闸门有效性 —— 被拦下的日子，后续是否真的更差？')
    print('=' * 78)

    gates = [
        ('广度一票否决 (up_ratio<35%)', lambda d: d['up_ratio'] < 35),
        ('关键低点跌破 (engine position=0 且非广度否决)',
         lambda d: (d['eng_position'] == 0) & (d['up_ratio'] >= 35)),
        ('炸板率>35%', lambda d: d['zb_rate'] > 35),
        ('高位分歧 (trend>=60 但 sentiment<40)',
         lambda d: (d['eng_trend'] >= 60) & (d['eng_trend'] < 75)),
    ]
    print(f'  {"闸门":<42} {"触发天":>6} {"fwd5均值":>10} {"fwd5中位":>10} {"胜率":>7} {"未触发fwd5":>11}')
    gate_rows = []
    for label, fn in gates:
        trig = sub[fn(sub)]
        notr = sub[~fn(sub)]
        if len(trig) == 0:
            print(f'  {label:<42} {0:>6}  {"未触发":>28}')
            continue
        f5t = trig['中证2000_fwd5'].dropna()
        f5n = notr['中证2000_fwd5'].dropna()
        win = (f5t > 0).mean() * 100 if len(f5t) else np.nan
        print(f'  {label:<42} {len(trig):>6} {f5t.mean():>10.2f} {f5t.median():>10.2f} '
              f'{win:>6.1f}% {f5n.mean():>11.2f}')
        gate_rows.append({'gate': label, 'n': len(trig),
                          'mean_fwd5': round(f5t.mean(), 2),
                          'median_fwd5': round(f5t.median(), 2),
                          'win_rate': round(win, 1),
                          'notrig_mean_fwd5': round(f5n.mean(), 2)})
    json.dump(gate_rows, open(os.path.join(OUT, 'gates.json'), 'w'),
              ensure_ascii=False, indent=2)

    # ---------- 7. 检验 4：仓位策略模拟 vs 买入持有 ----------
    print('\n' + '=' * 78)
    print('检验 4：按引擎仓位信号模拟（中证2000，仓位×次日收益）')
    print('=' * 78)
    sim = sub[sub['eng_position'].notna()].copy()
    sim = sim.sort_values('trade_date')
    # 信号用当日收盘给出，作用于次日收益
    sim['pos_frac'] = sim['eng_position'] / 100.0
    sim['strat_ret'] = sim['pos_frac'] * (sim['中证2000_fwd1'] / 100.0)
    sim['bh_ret'] = sim['中证2000_fwd1'] / 100.0
    # 仓位滞后一日（T+1 执行）
    sim['pos_frac_lag'] = sim['pos_frac'].shift(1).fillna(0)
    sim['strat_ret_lag'] = sim['pos_frac_lag'] * (sim['中证2000_fwd1'] / 100.0)

    def stats(r, label):
        r = r.dropna()
        if len(r) == 0:
            return
        cum = (1 + r / 100).prod() - 1
        vol = r.std() * np.sqrt(252)
        sharpe = (r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
        mdd = ((1 + r / 100).cumprod() - 1).min()
        print(f'  {label:<26} 累计{cum*100:>8.2f}%  年化波动{vol:>6.2f}%  '
              f'Sharpe{sharpe:>6.2f}  最大回撤{mdd*100:>7.2f}%  胜率{(r>0).mean()*100:>5.1f}%')

    stats(sim['strat_ret'], '引擎仓位(当日生效)')
    stats(sim['strat_ret_lag'], '引擎仓位(T+1执行)')
    stats(sim['bh_ret'], '满仓买入持有')

    # 分年度
    print('\n  分月表现（引擎仓位 T+1 执行 vs 买入持有）:')
    sim['ym'] = sim['trade_date'].str[:6]
    for ym, g in sim.groupby('ym'):
        s = (1 + g['strat_ret_lag'].fillna(0) / 100).prod() - 1
        b = (1 + g['bh_ret'].fillna(0) / 100).prod() - 1
        avgpos = g['pos_frac_lag'].mean() * 100
        print(f'    {ym}  策略{s*100:>7.2f}%  买持{b*100:>7.2f}%  超额{(s-b)*100:>7.2f}%  平均仓位{avgpos:>5.1f}%')

    # ---------- 8. 检验 5：全市场情绪口径问题 ----------
    print('\n' + '=' * 78)
    print('检验 5：情绪数据口径体检')
    print('=' * 78)
    # 中位数收益 vs 均值收益（判断个股分布是否右偏）
    recent = sub.dropna(subset=['mean_pct', 'median_pct'])
    if len(recent):
        skew_days = (recent['mean_pct'] > recent['median_pct']).sum()
        print(f'  均值 > 中位 的天数: {skew_days}/{len(recent)} '
              f'({skew_days/len(recent)*100:.0f}%)  —— 极端股拉高均值的程度')
        print(f'  平均 均值-中位 差: {(recent["mean_pct"]-recent["median_pct"]).mean():.3f} 个百分点')
        print(f'  平均 成交额: {recent["amount_yi"].mean():.0f}亿  '
              f'区间 {recent["amount_yi"].min():.0f}~{recent["amount_yi"].max():.0f}亿')
    # 上涨家数占比的分布
    ur = sub['up_ratio'].dropna()
    if len(ur):
        print(f'\n  up_ratio 分布: 均值{ur.mean():.1f}%  中位{ur.median():.1f}%  '
              f'min {ur.min():.1f}%  max {ur.max():.1f}%')
        print(f'  <35% 的天数: {(ur<35).sum()}  ({(ur<35).mean()*100:.0f}%)')
        print(f'  <40% 的天数: {(ur<40).sum()}  ({(ur<40).mean()*100:.0f}%)')

    # ---------- 9. 落盘 ----------
    corr_df = pd.DataFrame(corr_rows)
    corr_df.to_csv(os.path.join(OUT, 'signal_corr.csv'), index=False)
    print(f'\n  结果已保存至 {OUT}')
    return panel


if __name__ == '__main__':
    main()
