"""胜率校准回测：验证 enhanced_timing_bull_all 评级体系（S/A/B）的真实胜率

思路：
1. 读取 report_daily/enhanced_timing_bull_all_{date}.csv 历史信号（无时间戳后缀的正式版）
2. 用 DataFetcher.get_daily_history 批量取全市场日线（走按日 parquet 缓存，首次约1分钟）
3. 信号次日开盘买入，计算 5/10/20 交易日后的收益（收盘价口径，停牌用可得最近收盘）
4. 按评级 × 持有期统计：胜率/平均收益/样本数
5. 追溯模拟"稀缺性治理"（S限量Top10+主题≤2，A限量Top20+主题≤3），对比治理前后胜率

用法: python backtest_timing_grades.py [--days 25] [--fwd 5,10,20]
"""
import os
import sys
import glob
import argparse
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_fetcher import DataFetcher

REPORT_DIR = r'd:\mystock\solo\report_daily'
BASE_NAME = 'enhanced_timing_bull_all_{}'


def load_signal_files(max_days):
    """加载最近 max_days 个正式信号 CSV（排除带时间戳后缀的中间版）"""
    pat = os.path.join(REPORT_DIR, BASE_NAME.format('????????.csv'))
    files = sorted(glob.glob(pat))[-max_days:]
    frames = []
    for f in files:
        date = os.path.basename(f).replace('.csv', '').split('_')[-1]
        try:
            df = pd.read_csv(f, encoding='utf-8-sig')
        except Exception as e:
            print(f'  跳过 {date}: {e}')
            continue
        if '修正后胜率分级' not in df.columns:
            print(f'  跳过 {date}: 无评级列')
            continue
        df['signal_date'] = date
        want = ['代码', '名称', '主题', '量化择时分', '修正后胜率分级',
                '结构增强分', '洗盘修复分', '真突破判定', '回踩确认',
                '兑现冲击过滤', 'signal_date']
        frames.append(df[[c for c in want if c in df.columns]])
    if not frames:
        raise SystemExit('没有可用的历史信号文件')
    sig = pd.concat(frames, ignore_index=True)
    print(f'信号: {len(frames)} 天, {len(sig)} 条 ({frames[0]["signal_date"].iloc[0]} ~ {frames[-1]["signal_date"].iloc[0]})')
    return sig


def fetch_price_panel(fetcher, sig):
    """取覆盖全部信号日+前看窗口的全市场日线，返回 (trade_dates, {code: df})"""
    last = max(sig['signal_date'])
    panel = fetcher.get_daily_history(end_date=last, days=70)
    if panel is None or len(panel) == 0:
        raise SystemExit('日线预取失败')
    trade_dates = sorted(panel['trade_date'].unique().tolist())
    by_code = {c: g.set_index('trade_date').sort_index()
               for c, g in panel.groupby('ts_code')}
    print(f'价格面板: {len(by_code)} 只 x {len(trade_dates)} 交易日 '
          f'({trade_dates[0]} ~ {trade_dates[-1]})')
    return trade_dates, by_code


def forward_return(code, signal_date, by_code, trade_dates, horizon):
    """信号次日开盘买入，horizon 交易日后收盘卖出；数据不足返回 None(pending)"""
    df = by_code.get(code)
    if df is None:
        return None
    later = [d for d in trade_dates if d > signal_date]
    if len(later) < 1:
        return None
    entry_date = later[0]
    if entry_date in df.index:
        entry = float(df.loc[entry_date, 'open'])
    else:
        sub = df[df.index >= entry_date]  # 上市晚于信号/停牌，取复牌后首个开盘
        if len(sub) == 0:
            return None
        entry = float(sub['open'].iloc[0])
    if not entry or entry <= 0 or np.isnan(entry):
        return None
    exit_idx = later.index(entry_date) + horizon  # later[horizon] 即买入后第horizon个交易日
    if exit_idx >= len(later):
        return None  # 未到期
    exit_date = later[exit_idx]
    if exit_date in df.index:
        exit_close = float(df.loc[exit_date, 'close'])
    else:
        sub = df[df.index < exit_date]
        if len(sub) == 0:
            return None
        exit_close = float(sub['close'].iloc[-1])
    if np.isnan(exit_close):
        return None
    return (exit_close / entry - 1) * 100


def simulate_governance(df):
    """追溯应用稀缺性治理，返回治理后评级列"""
    S_MAX, S_THEME_MAX = 10, 2
    A_MAX, A_THEME_MAX = 20, 3
    out = df.copy()
    out['governed'] = out['修正后胜率分级']

    def theme_of(t):
        return t if isinstance(t, str) and t.strip() and t != 'nan' else '未分类'

    for d, day in out.groupby('signal_date'):
        s_mask = day['修正后胜率分级'] == 'S'
        s_rows = day[s_mask].sort_values('量化择时分', ascending=False)
        kept, cnt = 0, {}
        for idx, r in s_rows.iterrows():
            th = theme_of(r['主题'])
            if kept >= S_MAX or cnt.get(th, 0) >= S_THEME_MAX:
                out.at[idx, 'governed'] = 'A'
            else:
                cnt[th] = cnt.get(th, 0) + 1
                kept += 1
        a_mask = out.loc[day.index, 'governed'] == 'A'
        a_rows = out.loc[day.index][a_mask].sort_values('结构增强分', ascending=False)
        kept, cnt = 0, {}
        for idx, r in a_rows.iterrows():
            th = theme_of(r['主题'])
            if kept >= A_MAX or cnt.get(th, 0) >= A_THEME_MAX:
                out.at[idx, 'governed'] = 'B'
            else:
                cnt[th] = cnt.get(th, 0) + 1
                kept += 1
    return out


def report(sig, horizons, col):
    print(f'\n{"="*72}')
    print(f'  评级胜率（评级列: {col}）  买入=信号次日开盘')
    print(f'{"="*72}')
    print(f'{"评级":<4}{"持有":<6}{"样本":>6}{"胜率":>8}{"均值":>8}{"中位":>8}{">5%":>7}')
    for g in ['S', 'A', 'B', 'C']:
        sub = sig[sig[col] == g]
        if len(sub) == 0:
            continue
        for h in horizons:
            rets = sub[f'fwd{h}'].dropna()
            if len(rets) == 0:
                continue
            print(f'{g:<4}{h:<3}日 {len(rets):>6} {(rets > 0).mean() * 100:>7.1f}% '
                  f'{rets.mean():>7.2f}% {rets.median():>7.2f}% {(rets > 5).mean() * 100:>6.1f}%')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=25)
    ap.add_argument('--fwd', type=str, default='5,10,20')
    args = ap.parse_args()
    horizons = [int(x) for x in args.fwd.split(',')]

    sig = load_signal_files(args.days)
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "main_config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    config = _mod.load_config()
    fetcher = DataFetcher(_mod.get_token(config), config)
    trade_dates, by_code = fetch_price_panel(fetcher, sig)

    print('计算前向收益...')
    for h in horizons:
        sig[f'fwd{h}'] = [
            forward_return(r['代码'], r['signal_date'], by_code, trade_dates, h)
            for _, r in sig.iterrows()]
        n_ok = sig[f'fwd{h}'].notna().sum()
        print(f'  {h}日: 有效样本 {n_ok}/{len(sig)}')

    report(sig, horizons, '修正后胜率分级')

    gov = simulate_governance(sig)
    report(gov, horizons, 'governed')

    # 治理前后同口径对比（只比 S 级）
    print(f'\n{"="*72}\n  稀缺性治理效果对比（S级）\n{"="*72}')
    for h in horizons:
        old = sig[(sig['修正后胜率分级'] == 'S')][f'fwd{h}'].dropna()
        new = gov[(gov['governed'] == 'S')][f'fwd{h}'].dropna()
        if len(old) and len(new):
            print(f'  {h}日: 治理前 S({len(old)}样本) 胜率{(old > 0).mean() * 100:.1f}% 均值{old.mean():.2f}%'
                  f'  →  治理后 S({len(new)}样本) 胜率{(new > 0).mean() * 100:.1f}% 均值{new.mean():.2f}%')

    # 洗盘修复分门槛敏感性（S级原始条件内，按洗盘分分桶）
    print(f'\n{"="*72}\n  洗盘修复分敏感性（真突破+回踩确认股，5日持有）\n{"="*72}')
    sig.to_csv(os.path.join(REPORT_DIR, 'backtest_timing_grades_detail.csv'),
               index=False, encoding='utf-8-sig')
    print('明细已保存: backtest_timing_grades_detail.csv')


if __name__ == '__main__':
    main()
