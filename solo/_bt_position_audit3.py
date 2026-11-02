#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
仓位算法审计 第三版：全样本复现打分函数 + 关键结论验证
"""
import os
import sys
import sqlite3
import numpy as np
import pandas as pd
from scipy import stats as sps

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, 'cache_backbone_tushare')
OUT = os.path.join(BASE, '_bt_out')
sys.path.insert(0, BASE)

IDX = {'上证指数': '000001.SH', '沪深300': '000300.SH', '中证2000': '932000.CSI'}


# ============ 复现 market_analysis.py 的打分函数（保持口径一致）============
def calc_trend_score(df, up_count=0, total_count=3000):
    if df is None or len(df) < 20:
        return 50.0, "无数据", {}
    latest = df.iloc[-1]
    ma5 = df['close'].rolling(5).mean().iloc[-1]
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    ma5_series = df['close'].rolling(5).mean()
    ma20_series = df['close'].rolling(20).mean()
    ma5_slope = ma20_slope = 0.0
    if len(ma5_series) >= 6 and not pd.isna(ma5_series.iloc[-6]) and ma5_series.iloc[-6] > 0:
        ma5_slope = (ma5 - ma5_series.iloc[-6]) / ma5_series.iloc[-6] * 100
    if len(ma20_series) >= 21 and not pd.isna(ma20_series.iloc[-21]) and ma20_series.iloc[-21] > 0:
        ma20_slope = (ma20 - ma20_series.iloc[-21]) / ma20_series.iloc[-21] * 100
    ma_score = 0
    if ma5 > ma10 > ma20:
        if ma5_slope > 2 and ma20_slope > 0.5:
            ma_score = 50
        elif ma5_slope < 0.5:
            ma_score = 40
        else:
            ma_score = 45
    elif ma5 > ma10:
        ma_score = 38
    elif ma5 > ma20:
        ma_score = 25
    elif ma5 < ma10 < ma20:
        if ma5_slope > 0 and len(ma5_series) >= 4 and ma5 > ma5_series.iloc[-4]:
            ma_score = 22
        else:
            ma_score = 12
    else:
        ma_score = 18
    index_score = 30 if latest['close'] > ma20 else (20 if latest['close'] > ma10 else
                        (10 if latest['close'] > ma5 else 0))
    breadth_score = 0
    if total_count > 0:
        up_ratio = up_count / total_count * 100
        breadth_score = (20 if up_ratio > 70 else 17 if up_ratio >= 60 else 14 if up_ratio >= 50
                         else 8 if up_ratio >= 40 else 4 if up_ratio >= 30 else 0)
    trend_score = max(0, min(100, ma_score + index_score + breadth_score))
    if trend_score >= 75 and ma5 > ma10 > ma20 and latest['close'] > ma5:
        trend_status = "上升趋势"
    elif trend_score >= 65 and ma5 > ma10:
        trend_status = "上升趋势"
    elif trend_score >= 60:
        trend_status = "震荡偏强"
    elif trend_score >= 45:
        trend_status = "震荡整理"
    elif trend_score >= 30:
        trend_status = "震荡偏弱"
    else:
        trend_status = "下降趋势"
    return trend_score, trend_status, {'ma_score': ma_score, 'index_score': index_score,
                                       'breadth_score': breadth_score,
                                       'ma5_slope': round(ma5_slope, 2),
                                       'ma20_slope': round(ma20_slope, 2)}


def calc_sentiment_score(df, zt_count=0, zhaban_rate=0.0):
    if df is None or len(df) < 20:
        return 50.0, "无数据", {}
    latest = df.iloc[-1]
    pct_chg = latest['pct_chg'] if 'pct_chg' in df.columns else 0
    direction_score = (25 if pct_chg >= 2 else 18 if pct_chg >= 1 else 10 if pct_chg >= 0
                       else 5 if pct_chg >= -1 else 0 if pct_chg >= -2 else -15)
    vol5 = df['vol'].tail(5).mean()
    vol20 = df['vol'].tail(20).mean()
    vol_ratio = vol5 / vol20 if vol20 > 0 else 1
    vol_score = 10 + (vol_ratio - 1) * 15
    if pct_chg < -1 and vol_ratio > 1.2:
        vol_score -= 8
    elif pct_chg > 1 and vol_ratio > 1.2:
        vol_score += 4
    vol_score = min(20, max(0, vol_score))
    amplitude = (latest['high'] - latest['low']) / latest['low'] * 100
    amp_score = max(0, 7.5 - amplitude * 0.5) if pct_chg < 0 else min(15, 7.5 + amplitude * 0.4)
    up_streak = down_streak = 0
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] > 0: up_streak += 1
            else: break
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] < 0: down_streak += 1
            else: break
    streak_score = min(20, max(0, 10 + up_streak * 2.5 - down_streak * 3))
    zt_quality_score = 5
    if zt_count > 0:
        zt_quality_score = (10 if zhaban_rate < 10 else 8 if zhaban_rate < 20
                            else 5 if zhaban_rate < 35 else 2)
    elif zhaban_rate > 50:
        zt_quality_score = 0
    amount_trend_score = 5
    if 'amount' in df.columns and len(df) >= 20:
        amt_5d = df['amount'].tail(5).mean()
        amt_20d = df['amount'].tail(20).mean()
        if amt_20d > 0:
            amt_ratio = amt_5d / amt_20d
            if amt_ratio > 1.5 and pct_chg > 0: amount_trend_score = 10
            elif amt_ratio > 1.2 and pct_chg > 0: amount_trend_score = 7
            elif amt_ratio > 1.2 and pct_chg < 0: amount_trend_score = 3
            elif amt_ratio < 0.7: amount_trend_score = 2
            elif amt_ratio < 0.9: amount_trend_score = 4
    vol20_std = df['pct_chg'].tail(20).std()
    volatility = vol20_std if not pd.isna(vol20_std) else 1.5
    vol_penalty = (volatility - 2.5) * 3 if volatility > 2.5 else 0
    s = (direction_score + vol_score + amp_score + streak_score
         + zt_quality_score + amount_trend_score - vol_penalty)
    s = max(0, min(100, s))
    return s, '', {'direction': direction_score, 'vol': round(vol_score, 1),
                   'amp': round(amp_score, 1), 'streak': streak_score,
                   'ztq': zt_quality_score, 'amt': amount_trend_score,
                   'pen': round(vol_penalty, 1)}


def sp(a, b):
    a = pd.Series(a).astype(float); b = pd.Series(b).astype(float)
    m = a.notna() & b.notna()
    if m.sum() < 10: return np.nan, np.nan, int(m.sum())
    r = a[m].rank().corr(b[m].rank())
    try: p = sps.spearmanr(a[m], b[m]).pvalue
    except Exception: p = np.nan
    return r, p, int(m.sum())


def main():
    print('=' * 78)
    print('仓位算法审计 第三版：全样本复现 + 关键结论验证')
    print('=' * 78)

    panel = pd.read_csv(os.path.join(OUT, 'panel.csv'), dtype={'trade_date': str})
    conn = sqlite3.connect(os.path.join(CACHE, 'market_analysis.db'))
    ls = pd.read_sql_query("SELECT * FROM limit_stats", conn)
    oa = pd.read_sql_query("SELECT * FROM overall_analysis", conn)
    conn.close()

    # ---------- 1. 全样本复现打分 ----------
    print('\n[1] 全样本复现 calc_trend_score / calc_sentiment_score（198 个交易日）')
    idxdfs = {}
    for nm, code in IDX.items():
        f = os.path.join(CACHE, f'bt_idx_{code}_20260101.csv')
        d = pd.read_csv(f, dtype={'trade_date': str}).sort_values('trade_date').reset_index(drop=True)
        idxdfs[nm] = d

    bmap = panel.set_index('trade_date')
    repro = []
    for nm, d in idxdfs.items():
        for i in range(len(d)):
            if i < 25: continue
            dt = d['trade_date'].iloc[i]
            sub = d.iloc[:i + 1].copy()
            up = bmap.loc[dt, 'up'] if dt in bmap.index else None
            dn = bmap.loc[dt, 'down'] if dt in bmap.index else None
            if up is None: continue
            total = up + dn
            ts, st, td = calc_trend_score(sub, up, total)
            ss, _, sd = calc_sentiment_score(sub, 0, 0)
            repro.append({'trade_date': dt, 'name': nm, 'ts': ts, 'ts_status': st, 'ss': ss,
                          **td, **{f'sd_{k}': v for k, v in sd.items()}})
    rp = pd.DataFrame(repro)
    print(f'  复现样本: {len(rp)} 行 ({rp["trade_date"].nunique()} 个交易日 × 指数)')

    # 指数趋势分 → 合成（与 calculate_market_trend_score 的 index_trend 口径一致）
    piv = rp.pivot_table(index='trade_date', columns='name', values='ts')
    piv = piv.rename(columns={'上证指数': 'r_ts_sh', '沪深300': 'r_ts_hs300', '中证2000': 'r_ts_zz2000'})
    piv['r_index_trend'] = piv['r_ts_sh'] * 0.5 + piv['r_ts_hs300'] * 0.3 + piv['r_ts_zz2000'] * 0.2
    piv_s = rp.pivot_table(index='trade_date', columns='name', values='ss')
    piv_s = piv_s.rename(columns={'上证指数': 'r_ss_sh', '沪深300': 'r_ss_hs300', '中证2000': 'r_ss_zz2000'})
    piv_s['r_sent'] = piv_s.mean(axis=1)

    full = panel.merge(piv.reset_index(), on='trade_date', how='left')
    full = full.merge(piv_s.reset_index(), on='trade_date', how='left')

    # 主题趋势分（用引擎的值，缺失时用 0.8*index_trend 近似）
    full = full.merge(oa[['trade_date', 'theme_trend', 'trend_score']].rename(
        columns={'theme_trend': 'db_theme', 'trend_score': 'db_trend'}), on='trade_date', how='left')

    print('\n  复现的 index_trend 与引擎入库值一致性检查:')
    both = full.dropna(subset=['r_index_trend', 'eng_index_trend'])
    if len(both):
        diff = (both['r_index_trend'] - both['eng_index_trend']).abs()
        print(f'    N={len(both)}  平均绝对差={diff.mean():.2f}  中位={diff.median():.2f}  '
              f'完全一致(±1)={(diff<1).mean()*100:.0f}%')

    # ---------- 2. 全样本信号有效性 ----------
    print('\n[2] 全样本信号有效性（198 天，含未入库区间）')
    print(f'  {"信号":<22} {"fwd1":>9} {"fwd3":>9} {"fwd5":>9} {"fwd10":>9} {"N":>5}')
    for c in ['r_ts_sh', 'r_ts_hs300', 'r_ts_zz2000', 'r_index_trend', 'r_sent',
              'r_ss_zz2000']:
        line = f'  {c:<22}'
        for h in (1, 3, 5, 10):
            r, p, n = sp(full[c], full[f'中证2000_fwd{h}'])
            line += f'{r:>9.3f}' if not np.isnan(r) else f'{"NA":>9}'
        _, _, n = sp(full[c], full['中证2000_fwd5'])
        print(line + f'{n:>5}')

    # ---------- 3. 分解：情绪分各分项的信息含量 ----------
    print('\n[3] 情绪分分项的信息含量（中证2000 复现，N=全样本）')
    z = rp[rp['name'] == '中证2000'].merge(
        panel[['trade_date', '中证2000_fwd1', '中证2000_fwd5', '中证2000_ret1']], on='trade_date')
    for c in ['sd_direction', 'sd_vol', 'sd_amp', 'sd_streak', 'sd_ztq', 'sd_amt', 'sd_pen', 'ss']:
        if c not in z.columns: continue
        r1, p1, n1 = sp(z[c], z['中证2000_fwd1'])
        r5, p5, _ = sp(z[c], z['中证2000_fwd5'])
        # 与当日涨跌的相关（衡量是否只是"当日涨跌的复读"）
        rd, pd_, _ = sp(z[c], z['中证2000_ret1'])
        print(f'  {c:<14} ρ(当日涨跌)={rd:>6.3f}   ρ(fwd1)={r1:>6.3f}(p={p1:.2f})   '
              f'ρ(fwd5)={r5:>6.3f}(p={p5:.2f})   N={n1}')

    # ---------- 4. 关键低点闸门：逐日核对 ----------
    print('\n[4] 关键低点跌破闸门：逐日核对（DB position=0 且广度未否决）')
    ls2 = ls.rename(columns={'broken_rate': 'ls2_broken', 'zt_count': 'ls2_zt',
                              'dt_count': 'ls2_dt', 'up_ratio': 'ls2_up_ratio'})
    m = full.merge(ls2[['trade_date', 'ls2_broken', 'ls2_zt', 'ls2_dt', 'ls2_up_ratio']],
                   on='trade_date', how='left')
    for c in ['ls_up_ratio', 'zb_rate', 'ls_broken']:
        if c in full.columns and c not in m.columns:
            m[c] = full[c]
    bd = m[(m['eng_position'] == 0) & (m['ls_up_ratio'] >= 35)]
    if len(bd):
        print(f'  {"日期":<10} {"up%":>6} {"zb%":>6} {"趋势":>7} {"fwd1":>7} {"fwd3":>7} {"fwd5":>7} {"fwd10":>7}')
        for _, r in bd.iterrows():
            print(f'  {r["trade_date"]:<10} {r["ls_up_ratio"]:>6.1f} {r["zb_rate"]:>6.1f} '
                  f'{r["eng_trend"]:>7.1f} {r["中证2000_fwd1"]:>7.2f} {r["中证2000_fwd3"]:>7.2f} '
                  f'{r["中证2000_fwd5"]:>7.2f} {r["中证2000_fwd10"]:>7.2f}')
        print(f'  → 合计 N={len(bd)}  fwd5均值={bd["中证2000_fwd5"].mean():.2f}%  '
              f'胜率={(bd["中证2000_fwd5"]>0).mean()*100:.0f}%')

    # ---------- 5. 10月中旬空仓期到底对不对 ----------
    print('\n[5] 20261015 之后空仓期复盘（引擎仓位全为 0）')
    late = m[m['trade_date'] >= '20261015'].copy()
    print(f'  区间: {late["trade_date"].min()} ~ {late["trade_date"].max()}  N={len(late)}')
    print(f'  引擎仓位取值: {sorted(late["eng_position"].dropna().unique())}')
    zz = late['中证2000_fwd5'].dropna()
    print(f'  区间内 中证2000 fwd5 均值={zz.mean():.2f}%  中位={zz.median():.2f}%  '
          f'胜率={(zz>0).mean()*100:.0f}%')
    sh = late['上证指数_fwd5'].dropna()
    print(f'  区间内 上证指数 fwd5 均值={sh.mean():.2f}%  中位={sh.median():.2f}%  '
          f'胜率={(sh>0).mean()*100:.0f}%')
    # 空仓的机会成本
    missed = late[["中证2000_fwd5"]].copy()
    missed["missed_ret"] = (missed["中证2000_fwd5"] / 100) * (late['eng_position'].fillna(0) / 100)
    print(f'  空仓累计放弃收益(按0仓位 vs 满仓): {missed["中证2000_fwd5"].sum():.2f} 个百分点(等权加总)')

    # ---------- 6. DB 交易日连续性（滞回链条是否断裂）----------
    print('\n[6] overall_analysis / limit_stats 交易日连续性检查')
    cal = pd.read_csv(os.path.join(CACHE, 'bt_breadth.csv'), dtype={'trade_date': str})
    alldays = cal['trade_date'].tolist()
    for name, df in [('overall_analysis', oa), ('limit_stats', ls)]:
        ds = sorted(df['trade_date'].unique())
        missing = [d for d in alldays if d not in set(ds)]
        print(f'  {name}: 有 {len(ds)} 天，缺失 {len(missing)} 天')
        if missing:
            print(f'    缺失日期: {missing[:12]}{"..." if len(missing)>12 else ""}')
    # 连续性：相邻两行间隔
    ds = sorted(oa['trade_date'].unique())
    idx_map = {d: i for i, d in enumerate(alldays)}
    gaps = []
    for i in range(1, len(ds)):
        a, b = ds[i - 1], ds[i]
        if a in idx_map and b in idx_map:
            g = idx_map[b] - idx_map[a]
            if g > 1:
                gaps.append((a, b, g))
    print(f'  overall_analysis 间隔>1 交易日的断点: {len(gaps)} 处')
    for g in gaps[:10]:
        print(f'    {g[0]} → {g[1]}  跳过 {g[2]-1} 个交易日')
    # DB 中非交易日记录
    nontrade = [d for d in ds if d not in idx_map]
    if nontrade:
        print(f'  ⚠ overall_analysis 含非交易日记录 {len(nontrade)} 条: {nontrade[:6]}')

    # ---------- 7. 引擎 position 与 V9.9 目标一致性 ----------
    print('\n[7] overall_analysis.total_position 语义一致性')
    # V9.9 回写会覆盖老引擎仓位；market_status 仍是老引擎的
    print(f'  DB 中 market_status 取值分布（老引擎中文状态）:')
    print('   ', oa['market_status'].value_counts().head(8).to_dict())
    print(f'  DB 中 total_position=0 的天数: {(oa["total_position"]==0).sum()} / {len(oa)}')

    # ---------- 8. 广度口径一致性 ----------
    print('\n[8] 广度口径一致性（overview 用 up+down，limit_stats 用 total）')
    m2 = m.copy()
    m2['up_over_ud'] = m2['up'] / (m2['up'] + m2['down']) * 100
    m2['up_over_tot'] = m2['up'] / m2['total'] * 100
    d = (m2['up_over_ud'] - m2['up_over_tot']).abs()
    print(f'  两种口径 up_ratio 平均差={d.mean():.2f}pp  最大差={d.max():.2f}pp')
    thr = m2['up_over_ud'].apply(lambda x: '否决' if x < 35 else ('钝化区' if x < 55 else '通过'))
    thr2 = m2['up_over_tot'].apply(lambda x: '否决' if x < 35 else ('钝化区' if x < 55 else '通过'))
    flip = (thr != thr2).sum()
    print(f'  35%/55% 阈值判定翻转的天数: {flip} / {len(m2)} ({flip/len(m2)*100:.1f}%)')

    full.to_csv(os.path.join(OUT, 'panel_full.csv'), index=False)
    print(f'\n  → {OUT}/panel_full.csv')


if __name__ == '__main__':
    main()
