# -*- coding: utf-8 -*-
"""
BBS 100 因子回测 - TDX 本地框架
================================================
基于通达信本地 .day 文件，对 BBS（底部确认+右侧突破买点）做历史回测验证。

回测设计:
  1. 数据源: TDX .day 文件 (C:/new_tdx/vipdoc/sh|sz/lday/*.day) 一次性读入
  2. 每只股票滑动截断到调仓时点, 调用生产逻辑 BBSEngine.score_one(df=截断)
     —— 回测与生产使用同一套评分代码, 杜绝回测偏差
  3. 统计未来 T+5/T+10/T+20/T+30 收益
  4. 验证:
     - BBS 总分 IC + 五分位单调性
     - 等级 S/A/B/C/D 分组表现
     - Stage 1~7 分组 (核心: Stage5 缩量回踩再转强 是否优于 Stage3 首次突破)
     - 买点信号 BREAKOUT_BUY / PULLBACK_BUY / WATCH / NO_BUY 分组表现
     - 8 模块分 IC
     - 硬规则验证: 底部确认<10 / MA20-30-60全向下 是否真能过滤差样本
     - 量比区间分组 (验证 1.3~2.5 倍理想突破量的假设)

市场环境: 回测统一使用"震荡+中主线"(中性), 隔离大盘影响, 只检验 BBS
技术结构本身的预测力。大盘弱势等硬禁止不参与(避免用当日环境污染历史)。

用法:
  python bbs_backtest_tdx.py
  python bbs_backtest_tdx.py --start 20260101 --end 20260807 --hold 30 --step 5
"""
import os
import sys
import argparse
import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file  # noqa: E402
from bbs_engine import BBSEngine  # noqa: E402
from bbs_engine import STAGE_CN, LEVEL_CN, BUY_CN  # noqa: E402

# 中性市场环境：隔离大盘, 只检验技术结构
NEUTRAL_ENV = {'regime': '震荡', 'theme': '中', 'mainline': '中'}


def get_index_calendar(start, end, ts_code='000001.SH'):
    """用上证指数 .day 构建交易日历（更快，避免遍历全池）"""
    fpath = ts_code_to_tdx_file(ts_code)
    if not fpath or not os.path.exists(fpath):
        return []
    idx_df = parse_tdx_day_file(fpath)
    if idx_df is None:
        return []
    dates = [d for d in idx_df['trade_date'].tolist() if start <= d <= end]
    return sorted(dates)


def enumerate_all_stocks():
    """枚举全市场 A 股 .day 文件（排除指数/基金/可转债/北交所）"""
    from tail_backtest_tdx import TDX_PATH
    stocks = []
    for mkt, ok in (('sh', lambda c: c.startswith(('600', '601', '603', '605', '688'))),
                    ('sz', lambda c: c.startswith(('000', '001', '002', '003', '300', '301')))):
        d = os.path.join(TDX_PATH, 'vipdoc', mkt, 'lday')
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith('.day'):
                continue
            code6 = fn[2:8]
            if ok(code6):
                stocks.append({'code': code6, 'name': ''})
    return stocks


def future_returns(df_k, bt_idx, hold_days):
    """从 bt_idx 之后计算未来 hold_days 日的收益序列（与 mbs 框架一致）"""
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
    horizon = min(hold_days, len(rets))
    def _at(d):
        if d <= 0:
            return 0.0
        return float(rets[min(d - 1, horizon - 1)]) * 100 if horizon >= 1 else 0.0
    peak = np.maximum.accumulate(closes)
    max_dd = (closes / peak - 1) * 100
    return {
        'fut_ret_5': _at(5),
        'fut_ret_10': _at(10),
        'fut_ret_20': _at(20),
        'fut_ret_30': _at(30),
        'fut_max_dd': float(np.min(max_dd)) if len(max_dd) > 0 else 0,
        'fut_max_gain': (float(np.max(closes)) / buy - 1) * 100,
        'hold_days': horizon,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20260101')
    ap.add_argument('--end', default='20260807')
    ap.add_argument('--hold', type=int, default=30)
    ap.add_argument('--step', type=int, default=5, help='调仓间隔(交易日)')
    ap.add_argument('--csv', default=r'd:\mystock\solo\report_daily\bull_stocks_all.csv')
    ap.add_argument('--all', action='store_true', help='全市场模式(枚举TDX全部A股)')
    ap.add_argument('--limit', type=int, default=0, help='只回测前 N 只(0=全部, 调试用)')
    args = ap.parse_args()

    if args.all:
        pool = enumerate_all_stocks()
        print(f'[全市场模式] 枚举 A 股 {len(pool)} 只')
    else:
        pool = []
        pdf = pd.read_csv(args.csv, dtype={'code': str})
        code_col = 'code' if 'code' in pdf.columns else ('代码' if '代码' in pdf.columns else None)
        name_col = 'name' if 'name' in pdf.columns else ('名称' if '名称' in pdf.columns else None)
        if code_col is None:
            print('候选池缺少 code/代码 列')
            return
        for _, row in pdf.iterrows():
            code = str(row[code_col]).strip()
            if not code or code == 'nan':
                continue
            code6 = code.split('.')[0].zfill(6)
            if code6.startswith(('4', '8')):
                continue  # 排除北交所
            pool.append({'code': code6, 'name': str(row[name_col]) if name_col else ''})
            if args.limit and len(pool) >= args.limit:
                break

    print(f'股票池: {len(pool)} 只')
    print(f'回测区间: {args.start} ~ {args.end}, 持有 {args.hold} 天, 每 {args.step} 天调仓')
    print(f'市场环境: 中性(震荡+中主线) —— 隔离大盘, 只检验 BBS 技术结构')

    calendar = get_index_calendar(args.start, args.end)
    if len(calendar) < 30:
        print(f'交易日历不足: {len(calendar)} 天')
        return
    print(f'交易日: {len(calendar)} 天, {calendar[0]} ~ {calendar[-1]}')

    # 调仓时点：每 step 个交易日一次（2026 年内）
    rebalance_dates = calendar[::args.step]
    if len(rebalance_dates) > 1 and rebalance_dates[-1] != calendar[-1]:
        rebalance_dates = rebalance_dates[:-1] + [calendar[-1]]
    print(f'调仓时点: {len(rebalance_dates)} 个')

    engine = BBSEngine()
    all_results = []
    n_stocks = len(pool)

    for si, item in enumerate(pool, 1):
        code6, name = item['code'], item['name']
        # 市场推断：600/601/603/605/688/51 → SH
        fpath = ts_code_to_tdx_file(code6 + ('.SH' if code6.startswith(('60', '68', '51', '50', '90', '58')) else '.SZ'))
        if not fpath or not os.path.exists(fpath):
            continue
        df_k = parse_tdx_day_file(fpath)
        if df_k is None or len(df_k) < 130:
            continue
        date_to_idx = {d: i for i, d in enumerate(df_k['trade_date'].tolist())}

        for bt_date in rebalance_dates:
            if bt_date not in date_to_idx:
                continue
            di = date_to_idx[bt_date]
            if di < 130:
                continue
            try:
                r = engine.score_one(code6, name, df=df_k.iloc[:di + 1], market_env=NEUTRAL_ENV)
            except Exception:
                continue
            fr = future_returns(df_k, di, args.hold)
            if fr is None:
                continue
            rec = {
                'code': code6, 'name': name, 'date': bt_date,
                'bbs': r.bbs, 'level': r.level, 'stage': r.stage,
                'stage_cn': r.stage_cn, 'buy_signal': r.buy_signal,
                'confidence': r.confidence, 'failure_reason': r.failure_reason,
                'bottom': r.bottom_score, 'platform': r.platform_score,
                'breakout': r.breakout_score, 'volume': r.volume_score,
                'ma': r.ma_score, 'pullback': r.pullback_score,
                'rsi_macd': r.rsi_macd_score, 'market': r.market_score,
                'vol_ratio': r.vol_ratio, 'breakout_pct': r.breakout_pct,
                'platform_days': r.platform_days, 'platform_width': r.platform_width,
                'bottom_score': r.bottom_score,
            }
            rec.update(fr)
            all_results.append(rec)

        if si % 200 == 0:
            print(f'  进度 {si}/{n_stocks}, 已收集 {len(all_results)} 条')

    if len(all_results) == 0:
        print('无回测结果')
        return

    df = pd.DataFrame(all_results)
    tag = 'all' if args.all else 'pool'
    df.to_csv(os.path.join(BASE_DIR, 'report_daily', f'bbs_backtest_tdx_{tag}.csv'),
              index=False, encoding='utf-8-sig')
    print(f'\n回测完成: {len(df)} 条, 覆盖 {df["code"].nunique()} 只, {df["date"].nunique()} 个时点')

    def grp(cond=None, label='全部', key='fut_ret_20'):
        sub = df if cond is None else df[cond]
        if len(sub) == 0:
            print(f'  {label:34s}: 无样本')
            return None
        avg = sub[key].mean()
        win = (sub[key] > 0).mean() * 100
        dd = sub['fut_max_dd'].mean()
        gain = sub['fut_max_gain'].mean()
        print(f'  {label:34s}: 20日收益{avg:+6.2f}%  胜率{win:5.1f}%  回撤{dd:6.1f}%  最高{gain:6.1f}%  (n={len(sub)})')
        return {'n': len(sub), 'avg': avg, 'win': win, 'dd': dd, 'gain': gain}

    # ══ 1. 总体 ══
    print(f'\n{"="*70}')
    print('0. 总体基准')
    print('=' * 70)
    base = grp(key='fut_ret_20')
    grp(key='fut_ret_10')
    grp(key='fut_ret_30')

    # ══ 2. BBS 总分 IC ══
    print(f'\n{"="*70}')
    print('1. BBS 总分与未来收益相关性 (IC)')
    print('=' * 70)
    for h in ('fut_ret_5', 'fut_ret_10', 'fut_ret_20', 'fut_ret_30'):
        ic = df['bbs'].corr(df[h])
        print(f'  IC({h[9:]}日) = {ic:+.4f}  (n={len(df)})')

    # ══ 3. BBS 五分位 ══
    print(f'\n{"="*70}')
    print('2. BBS 五分位收益 (20日, 验证单调性)')
    print('=' * 70)
    try:
        df['q'] = pd.qcut(df['bbs'], 5, labels=[1, 2, 3, 4, 5], duplicates='drop')
        g = df.groupby('q').agg(n=('fut_ret_20', 'count'), avg=('fut_ret_20', 'mean'),
                                win=('fut_ret_20', lambda s: (s > 0).mean() * 100)).round(2)
        print(g.to_string())
        top = g.loc[g.index.max(), 'avg']; bot = g.loc[g.index.min(), 'avg']
        print(f'  多空价差(Q5-Q1) = {top - bot:+.2f}%  {"✓单调有效" if top > bot else "✗不单调"}')
    except Exception as e:
        print(f'  分位计算失败: {e}')

    # ══ 4. 等级分组 ══
    print(f'\n{"="*70}')
    print('3. 等级 S/A/B/C/D 分组 (20日)')
    print('=' * 70)
    for lv in ['S', 'A', 'B', 'C', 'D']:
        grp(df['level'] == lv, f'{lv}级')

    # ══ 5. Stage 分组 (核心) ══
    print(f'\n{"="*70}')
    print('4. Stage 1~7 分组 (核心: Stage5 vs Stage3)')
    print('=' * 70)
    for st in sorted(df['stage'].unique()):
        grp(df['stage'] == st, f'Stage{st} {STAGE_CN.get(st, "")}')

    # ══ 6. 买点信号分组 (核心) ══
    print(f'\n{"="*70}')
    print('5. 买点信号分组 (20日)')
    print('=' * 70)
    for sig in ['ADD_POSITION', 'PULLBACK_BUY', 'BREAKOUT_BUY', 'WATCH', 'NO_BUY']:
        grp(df['buy_signal'] == sig, f'{sig}')

    # ══ 7. 8 模块 IC ══
    print(f'\n{"="*70}')
    print('6. 8 模块分 IC (对未来20日收益)')
    print('=' * 70)
    for f in ['bottom', 'platform', 'breakout', 'volume', 'ma', 'pullback', 'rsi_macd', 'market']:
        ic = df[f].corr(df['fut_ret_20'])
        print(f'  {f:10s}: IC(20日) = {ic:+.4f}')

    # ══ 8. 硬规则验证 ══
    print(f'\n{"="*70}')
    print('7. 硬规则有效性验证 (20日)')
    print('=' * 70)
    grp(df['bottom'] < 10, '底部确认<10分')
    grp(df['bottom'] >= 10, '底部确认>=10分')
    grp(df['bottom'] >= 16, '底部确认>=16(强底部)')

    # MA20/30/60 全向下: 用 bbs<=74 且非其余原因? 直接按 ma 分低分判断
    grp(df['ma'] <= 6, '均线分<=6(弱)')
    grp(df['ma'] >= 12, '均线分>=12(强)')

    # ══ 9. 量比分组 ══
    print(f'\n{"="*70}')
    print('8. 突破量比分组 (20日, 验证1.3~2.5理想区间)')
    print('=' * 70)
    for lo, hi, lab in [(0, 1.0, '<1.0(缩量)'), (1.0, 1.3, '1.0~1.3'),
                        (1.3, 1.8, '1.3~1.8'), (1.8, 2.5, '1.8~2.5(理想)'),
                        (2.5, 3.0, '2.5~3.0'), (3.0, 99, '>3.0(巨量)')]:
        cond = (df['vol_ratio'] >= lo) & (df['vol_ratio'] < hi)
        if cond.any():
            grp(cond, lab, key='fut_ret_20')

    # ══ 10. 组合信号验证 ══
    print(f'\n{"="*70}')
    print('9. 组合信号验证 (规格买点定义)')
    print('=' * 70)
    grp((df['buy_signal'] == 'BREAKOUT_BUY'), 'BREAKOUT_BUY (BBS>=75,Stage3,量比>=1.3)')
    grp((df['buy_signal'] == 'PULLBACK_BUY'), 'PULLBACK_BUY (BBS>=80,Stage5)')
    grp((df['buy_signal'] == 'ADD_POSITION'), 'ADD_POSITION (BBS>=85,Stage5)')
    grp((df['bbs'] >= 75) & (df['stage'].isin([3, 5])), 'BBS>=75 且 Stage3/5')
    grp((df['bbs'] >= 80) & (df['stage'] == 5), 'BBS>=80 且 Stage5 (最佳买点)')
    grp((df['bbs'] >= 65) & (df['stage'] == 5), 'BBS>=65 且 Stage5 (观察+)')

    # ══ 11. 阶段内高分对比 ══
    print(f'\n{"="*70}')
    print('10. 关键对比: Stage5 vs Stage3 (同分数段)')
    print('=' * 70)
    for bbs_lo, bbs_hi, lab in [(65, 75, '65~75'), (75, 85, '75~85'), (85, 101, '85+')]:
        s3 = df[(df['stage'] == 3) & (df['bbs'] >= bbs_lo) & (df['bbs'] < bbs_hi)]
        s5 = df[(df['stage'] == 5) & (df['bbs'] >= bbs_lo) & (df['bbs'] < bbs_hi)]
        r3 = (s3['fut_ret_20'].mean(), (s3['fut_ret_20'] > 0).mean() * 100, len(s3)) if len(s3) else (0, 0, 0)
        r5 = (s5['fut_ret_20'].mean(), (s5['fut_ret_20'] > 0).mean() * 100, len(s5)) if len(s5) else (0, 0, 0)
        print(f'  BBS {lab}: Stage3 收益{r3[0]:+.2f}% 胜率{r3[1]:.0f}% (n={r3[2]}) | '
              f'Stage5 收益{r5[0]:+.2f}% 胜率{r5[1]:.0f}% (n={r5[2]}) | '
              f'差值 {r5[0] - r3[0]:+.2f}%')


if __name__ == '__main__':
    main()
