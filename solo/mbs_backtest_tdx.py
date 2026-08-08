# -*- coding: utf-8 -*-
"""
MBS 因子回测 - TDX 本地框架
================================================
基于通达信本地 .day 文件，对 MBS 引擎的因子做历史回测验证。

回测设计:
  1. 数据源: TDX .day 文件 (C:/new_tdx/vipdoc/sh|sz/lday/*.day) 一次性读入
  2. 每只股票滑动窗口, 在每个调仓时点(每隔10个交易日)计算 MBS 技术因子
  3. 统计未来 T+5/T+10/T+20/T+30 收益
  4. 验证:
     - 各因子 IC (与未来收益相关性)
     - 因子五分位收益 (单调性)
     - 双底/下跌模式/量价背离 分组表现
     - BQS/DQS 组合筛选效果

用法:
  python mbs_backtest_tdx.py
  python mbs_backtest_tdx.py --start 20260101 --end 20260807 --hold 30
"""
import os
import sys
import argparse
import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file  # noqa: E402
from mbs_engine import MBSEngine  # noqa: E402

engine = MBSEngine()
pool = engine.df  # 股票池


def build_ts_code(code6, market):
    """6位代码 + 市场 → 完整 ts_code (600/601/603/605/688→SH, 其余→SZ)"""
    code6 = str(code6).strip().zfill(6)
    if code6.startswith(('60', '68', '90', '51', '58')):
        return code6 + '.SH'
    return code6 + '.SZ'


def get_market_calendar(pool_df, start, end):
    """用股票池构建交易日历(取所有股票 trade_date 并集, 排序去重)"""
    all_dates = set()
    n = len(pool_df)
    for i, (_, row) in enumerate(pool_df.iterrows()):
        code6 = str(row.get('代码6', '')).strip()
        if not code6 or len(code6) != 6:
            continue
        market = str(row.get('市场', '')).strip()
        ts_code = build_ts_code(code6, market)
        fpath = ts_code_to_tdx_file(ts_code)
        if not fpath or not os.path.exists(fpath):
            continue
        df_k = parse_tdx_day_file(fpath)
        if df_k is None:
            continue
        dates = df_k['trade_date'].tolist()
        for d in dates:
            if start <= d <= end:
                all_dates.add(d)
    return sorted(all_dates)


def compute_factors_at(df_k, bt_idx):
    """在 bt_idx 时点计算因子, 返回 dict"""
    slice_df = df_k.iloc[:bt_idx + 1].copy()
    if len(slice_df) < 60:
        return None
    tech = engine.compute_technical(slice_df)
    if tech is None:
        return None
    entry_res = engine.entry_score(tech)
    entry = entry_res[0] if isinstance(entry_res, tuple) else entry_res
    pcs = engine.pcs_score(tech)
    state_code = engine.pullback_state_v4(tech)
    acs = engine.acs_score(tech, 70, 70, 60)
    bqs = engine.bottom_quality_score(tech, acs, pcs)
    dqs = engine.drop_quality_score(tech)
    return {
        'entry': entry,
        'pcs': pcs,
        'acs': acs,
        'bqs': bqs,
        'dqs': dqs,
        'state': state_code,
        'd_ma20': tech.get('dist_ma20'),
        'd_hi': tech.get('dist_hi'),
        'db_type': tech.get('db_type', ''),
        'fall_pattern': tech.get('fall_pattern', ''),
        'vpd_type': tech.get('vpd_type', ''),
        'ma_conv': tech.get('ma_conv_score'),
        'top_pattern': tech.get('top_pattern', ''),
    }


def future_returns(df_k, bt_idx, hold_days):
    """从 bt_idx 之后计算未来 hold_days 日的收益序列"""
    if bt_idx + 1 >= len(df_k):
        return None
    fut = df_k.iloc[bt_idx + 1:]
    if len(fut) < 3:
        return None
    buy = float(df_k['close'].iloc[bt_idx])
    if buy <= 0:
        return None
    closes = fut['close'].values.astype(float)
    rets = closes / buy - 1
    # 取持有期内的收益点
    horizon = min(hold_days, len(rets))
    ret_30 = (rets[horizon - 1] if horizon >= 1 else 0) * 100
    ret_10 = (rets[min(9, horizon - 1)] if horizon >= 10 else (rets[-1] if len(rets) else 0)) * 100
    ret_20 = (rets[min(19, horizon - 1)] if horizon >= 20 else (rets[-1] if len(rets) else 0)) * 100
    peak = np.maximum.accumulate(closes)
    max_dd = (closes / peak - 1) * 100
    max_gain = (np.max(closes) / buy - 1) * 100
    return {
        'fut_ret_10': ret_10,
        'fut_ret_20': ret_20,
        'fut_ret_30': ret_30,
        'fut_max_dd': float(np.min(max_dd)) if len(max_dd) > 0 else 0,
        'fut_max_gain': max_gain,
        'hold_days': horizon,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20260101', help='回测起始日')
    ap.add_argument('--end', default='20260807', help='回测结束日')
    ap.add_argument('--hold', type=int, default=30, help='持有期(交易日)')
    ap.add_argument('--step', type=int, default=10, help='调仓间隔(交易日)')
    args = ap.parse_args()

    print(f'股票池: {len(pool)} 只')
    print(f'回测区间: {args.start} ~ {args.end}, 持有 {args.hold} 天, 每 {args.step} 天调仓')

    # 构建交易日历 (往前覆盖足够的历史用于因子计算)
    print('构建交易日历...')
    cal_start = (datetime.datetime.strptime(args.start, '%Y%m%d') - datetime.timedelta(days=400)).strftime('%Y%m%d')
    calendar = get_market_calendar(pool, cal_start, args.end)
    if len(calendar) < 30:
        print(f'交易日历不足: {len(calendar)} 天')
        return
    print(f'交易日: {len(calendar)} 天, {calendar[0]} ~ {calendar[-1]}')

    # 需要至少120天历史计算因子, 所以回测起点从第120个交易日后开始
    # 调仓时点: 只在 args.start 之后的日期调仓
    rebalance_idx = [i for i in range(120, len(calendar) - args.hold - 1, args.step)
                     if calendar[i] >= args.start]
    if not rebalance_idx:
        print('回测窗口不足')
        return
    print(f'调仓时点: {len(rebalance_idx)} 个, 从 {calendar[rebalance_idx[0]]} 到 {calendar[rebalance_idx[-1]]}')

    all_results = []
    n_stocks = len(pool)

    for si, (_, row) in enumerate(pool.iterrows(), 1):
        code6 = str(row.get('代码6', '')).strip()
        name = str(row.get('名称', '')).strip()
        if not code6 or len(code6) != 6:
            continue
        market = str(row.get('市场', '')).strip()
        ts_code = build_ts_code(code6, market)
        fpath = ts_code_to_tdx_file(ts_code)
        if not fpath or not os.path.exists(fpath):
            continue
        df_k = parse_tdx_day_file(fpath)
        if df_k is None or len(df_k) < 130:
            continue

        # 日期 → 索引 映射
        date_to_idx = {d: i for i, d in enumerate(df_k['trade_date'].tolist())}

        for bt_idx_date in rebalance_idx:
            bt_date = calendar[bt_idx_date]
            if bt_date not in date_to_idx:
                continue
            di = date_to_idx[bt_date]
            if di < 119:
                continue
            # 计算因子
            f = compute_factors_at(df_k, di)
            if f is None:
                continue
            # 未来收益
            fr = future_returns(df_k, di, args.hold)
            if fr is None:
                continue
            f.update(fr)
            f['code'] = code6
            f['name'] = name
            f['date'] = bt_date
            all_results.append(f)

        if si % 200 == 0:
            print(f'  进度 {si}/{n_stocks}, 已收集 {len(all_results)} 条')

    if len(all_results) == 0:
        print('无回测结果')
        return

    df = pd.DataFrame(all_results)
    print(f'\n回测完成: {len(df)} 条记录, 覆盖 {df["code"].nunique()} 只股票, {df["date"].nunique()} 个时点')

    # ════════════════════════════════════════════════════════
    # 1. 因子 IC
    # ════════════════════════════════════════════════════════
    print(f'\n{"="*66}')
    print('1. 因子与未来收益相关性 (IC)')
    print(f'{"="*66}')
    factors = ['entry', 'pcs', 'acs', 'bqs', 'dqs', 'd_ma20', 'd_hi', 'ma_conv']
    for f in factors:
        valid = df[df[f].notna()]
        if len(valid) < 50:
            print(f'  {f:10s}: 样本不足({len(valid)})')
            continue
        ic10 = valid[f].corr(valid['fut_ret_10'])
        ic30 = valid[f].corr(valid['fut_ret_30'])
        print(f'  {f:10s}: IC(10日)={ic10:+.4f}  IC(30日)={ic30:+.4f}  (样本{len(valid)})')

    # ════════════════════════════════════════════════════════
    # 2. 因子五分位收益
    # ════════════════════════════════════════════════════════
    print(f'\n{"="*66}')
    print('2. 因子五分位收益 (30日, 验证单调性)')
    print(f'{"="*66}')
    for f in factors:
        valid = df[df[f].notna()].copy()
        if len(valid) < 100:
            continue
        try:
            valid['g'] = pd.qcut(valid[f], 5, labels=[1, 2, 3, 4, 5], duplicates='drop')
        except Exception:
            continue
        grp = valid.groupby('g')['fut_ret_30'].agg(['mean', 'count'])
        if len(grp) < 2:
            continue
        top = grp.loc[grp.index.max(), 'mean']
        bot = grp.loc[grp.index.min(), 'mean']
        mono = '✓' if top > bot else '✗'
        print(f'\n  [{f}] 多空价差 = {top-bot:+.2f}%  {mono}')
        print(grp.round(2).to_string())

    # ════════════════════════════════════════════════════════
    # 3. 形态信号分组表现
    # ════════════════════════════════════════════════════════
    print(f'\n{"="*66}')
    print('3. 形态信号分组表现 (30日)')
    print(f'{"="*66}')
    all_avg = df['fut_ret_30'].mean()
    all_win = (df['fut_ret_30'] > 0).mean() * 100
    print(f'  全部样本: 收益{all_avg:+.2f}%  胜率{all_win:.1f}%  (样本{len(df)})')

    print('\n  ── 双底形态 ──')
    for db in ['DOUBLE_BOTTOM', 'W_BOTTOM', 'POTENTIAL_DB']:
        sub = df[df['db_type'] == db]
        if len(sub) < 20:
            continue
        avg = sub['fut_ret_30'].mean()
        win = (sub['fut_ret_30'] > 0).mean() * 100
        dd = sub['fut_max_dd'].mean()
        gain = sub['fut_max_gain'].mean()
        print(f'  {db:18s}: 收益{avg:+.2f}% 胜率{win:.1f}% 回撤{dd:.1f}% 最高{gain:.1f}% (样本{len(sub)})')

    print('\n  ── 量价背离 ──')
    for v in df['vpd_type'].unique():
        if not v:
            continue
        sub = df[df['vpd_type'] == v]
        if len(sub) < 20:
            continue
        avg = sub['fut_ret_30'].mean()
        win = (sub['fut_ret_30'] > 0).mean() * 100
        print(f'  {v:25s}: 收益{avg:+.2f}% 胜率{win:.1f}% (样本{len(sub)})')

    print('\n  ── 头部形态 ──')
    for v in df['top_pattern'].unique():
        if not v:
            continue
        sub = df[df['top_pattern'] == v]
        if len(sub) < 20:
            continue
        avg = sub['fut_ret_30'].mean()
        win = (sub['fut_ret_30'] > 0).mean() * 100
        print(f'  {v:20s}: 收益{avg:+.2f}% 胜率{win:.1f}% (样本{len(sub)})')

    # ════════════════════════════════════════════════════════
    # 4. BQS / DQS 组合筛选
    # ════════════════════════════════════════════════════════
    print(f'\n{"="*66}')
    print('4. BQS/DQS 组合筛选效果 (30日)')
    print(f'{"="*66}')
    dfv = df[(df['bqs'].notna()) & (df['dqs'].notna())].copy()
    groups = {
        '全部': dfv,
        'BQS>=70': dfv[dfv['bqs'] >= 70],
        'BQS<50': dfv[dfv['bqs'] < 50],
        'DQS>=60': dfv[dfv['dqs'] >= 60],
        'DQS<40': dfv[dfv['dqs'] < 40],
        '双高(BQS>=70&DQS>=60)': dfv[(dfv['bqs'] >= 70) & (dfv['dqs'] >= 60)],
        '双低(BQS<50&DQS<40)': dfv[(dfv['bqs'] < 50) & (dfv['dqs'] < 40)],
        'BQS>=70且DQS<50(形态好但下跌差)': dfv[(dfv['bqs'] >= 70) & (dfv['dqs'] < 50)],
    }
    for name, sub in groups.items():
        if len(sub) < 10:
            continue
        avg = sub['fut_ret_30'].mean()
        win = (sub['fut_ret_30'] > 0).mean() * 100
        dd = sub['fut_max_dd'].mean()
        gain = sub['fut_max_gain'].mean()
        print(f'  {name:28s}: 收益{avg:+.2f}% 胜率{win:.1f}% 回撤{dd:.1f}% 最高{gain:.1f}% (样本{len(sub)})')

    # ════════════════════════════════════════════════════════
    # 5. 组合"黄金坑"逻辑 (BQS+DQS+ACS 三高)
    # ════════════════════════════════════════════════════════
    print(f'\n{"="*66}')
    print('5. 黄金坑逻辑验证 (BQS≥65 & DQS≥55 & ACS≥70)')
    print(f'{"="*66}')
    gp = df[(df['bqs'] >= 65) & (df['dqs'] >= 55) & (df['acs'] >= 70)].copy()
    if len(gp) > 0:
        avg = gp['fut_ret_30'].mean()
        win = (gp['fut_ret_30'] > 0).mean() * 100
        dd = gp['fut_max_dd'].mean()
        gain = gp['fut_max_gain'].mean()
        print(f'  黄金坑组合: 收益{avg:+.2f}% 胜率{win:.1f}% 回撤{dd:.1f}% 最高{gain:.1f}% (样本{len(gp)})')
        print('\n  黄金坑个股明细 (Top20):')
        show = gp.sort_values('fut_ret_30', ascending=False)[
            ['date', 'name', 'bqs', 'dqs', 'acs', 'pcs', 'entry',
             'db_type', 'fut_ret_10', 'fut_ret_30', 'fut_max_dd']].head(20)
        print(show.round(1).to_string(index=False))
    else:
        print('  无样本')

    # 保存结果
    out_csv = os.path.join(BASE_DIR, 'report_daily', 'mbs_backtest_tdx.csv')
    df.to_csv(out_csv, index=False, encoding='utf-8-sig')
    print(f'\n结果已保存: {out_csv}')


if __name__ == '__main__':
    main()
