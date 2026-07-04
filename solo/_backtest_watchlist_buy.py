# -*- coding: utf-8 -*-
"""
两阶段策略历史回测
对历史theme_pattern选股结果，模拟在选出后1-10天内检测回调买点
计算买点触发后的T+1/T+3/T+5收益
"""
import os
import sys
import time
import glob
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import tushare as ts

# 复用主程序的函数
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watchlist_buy_signal import (
    get_stock_daily, calc_buy_signal, load_watchlist,
    is_shuangchuang, get_board_params, CACHE_DIR, BASE_DIR, REPORT_DIR
)

load_dotenv(os.path.join(os.path.dirname(BASE_DIR), "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TS_TOKEN)
pro = ts.pro_api()


def get_trade_dates(start, end):
    cal = pro.trade_cal(exchange='', start_date=start, end_date=end)
    cal = cal[cal['is_open'] == 1].sort_values('cal_date').reset_index(drop=True)
    return cal['cal_date'].tolist()


def backtest_single_pick_date(pick_date, trade_dates_all, max_scan_days=10):
    """
    对单个选股日进行回测
    1. 读取选股结果
    2. 在选出后1~max_scan_days天内，检测是否触发BUY信号
    3. 如果触发，计算T+1/T+3/T+5收益
    """
    pick_file = os.path.join(REPORT_DIR, f"theme_pattern_stocks_{pick_date}.csv")
    if not os.path.exists(pick_file):
        return []

    df_pick = pd.read_csv(pick_file)
    df_pick = df_pick.drop_duplicates(subset=['code'])[['code', 'name', 'close', 'final_score', 'buy_type']].copy()
    df_pick['close'] = pd.to_numeric(df_pick['close'], errors='coerce')
    df_pick['final_score'] = pd.to_numeric(df_pick['final_score'], errors='coerce')

    # 选股日后的交易日序列
    if pick_date not in trade_dates_all:
        return []
    pick_idx = trade_dates_all.index(pick_date)
    future_dates = trade_dates_all[pick_idx + 1: pick_idx + 1 + max_scan_days + 5]

    if len(future_dates) < 5:
        return []

    results = []

    for _, stock in df_pick.iterrows():
        code = stock['code']
        df = get_stock_daily(code)
        if df is None or len(df) < 40:
            continue

        df = df.copy()
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 在选股日之后的max_scan_days天内，逐日检测买点
        buy_triggered = False
        buy_date = None
        buy_price = None
        buy_score = 0
        buy_reasons = ''
        days_to_buy = 0

        scan_dates = future_dates[:max_scan_days]

        for d_idx, scan_date in enumerate(scan_dates):
            # 截取到该日的数据
            df_slice = df[df['trade_date'] <= scan_date].copy()
            if len(df_slice) < 40:
                continue

            signal, score, details, reasons = calc_buy_signal(df_slice, code, pick_date)

            if signal == 'BUY':
                buy_triggered = True
                buy_date = scan_date
                days_to_buy = d_idx + 1
                # 买入价 = 当日收盘价
                buy_row = df_slice[df_slice['trade_date'] == scan_date]
                if not buy_row.empty:
                    buy_price = float(buy_row.iloc[0]['close'])
                else:
                    buy_price = float(df_slice.iloc[-1]['close'])
                buy_score = score
                buy_reasons = '; '.join(reasons[:3])  # 只取前3个原因
                break

        if not buy_triggered:
            # 记录未触发的
            results.append({
                'pick_date': pick_date,
                'code': code,
                'name': stock['name'],
                'final_score': stock['final_score'],
                'buy_type': stock['buy_type'],
                'signal': 'NO_BUY',
                'days_to_buy': 0,
                'buy_date': '',
                'buy_price': 0,
                'T+1_pct': None,
                'T+3_pct': None,
                'T+5_pct': None,
                'buy_score': 0,
                'reasons': '',
            })
            continue

        # 计算买入后的收益
        buy_idx_in_future = future_dates.index(buy_date)

        result = {
            'pick_date': pick_date,
            'code': code,
            'name': stock['name'],
            'final_score': stock['final_score'],
            'buy_type': stock['buy_type'],
            'signal': 'BUY',
            'days_to_buy': days_to_buy,
            'buy_date': buy_date,
            'buy_price': buy_price,
            'buy_score': buy_score,
            'reasons': buy_reasons,
        }

        for offset, label in [(1, 'T+1'), (3, 'T+3'), (5, 'T+5')]:
            target_idx = buy_idx_in_future + offset
            if target_idx < len(future_dates):
                target_date = future_dates[target_idx]
                future_row = df[df['trade_date'] == target_date]
                if not future_row.empty:
                    future_close = float(future_row.iloc[0]['close'])
                    result[f'{label}_pct'] = (future_close / buy_price - 1) * 100
                else:
                    result[f'{label}_pct'] = None
            else:
                result[f'{label}_pct'] = None

        results.append(result)

    return results


def main():
    print("\n" + "=" * 70)
    print("两阶段策略历史回测")
    print("=" * 70)

    # 回测区间
    start_date = '20260601'
    end_date = '20260625'  # 选股日截止6/25，确保有足够后续数据
    trade_dates_all = get_trade_dates(start_date, '20260715')

    # 获取所有选股日
    pick_files = sorted(glob.glob(os.path.join(REPORT_DIR, "theme_pattern_stocks_*.csv")))
    pick_dates = []
    for f in pick_files:
        date_str = os.path.basename(f).split('_')[-1].replace('.csv', '')
        if start_date <= date_str <= end_date:
            pick_dates.append(date_str)

    print(f"回测选股日: {pick_dates[0]} ~ {pick_dates[-1]} ({len(pick_dates)}个)")
    print(f"扫描窗口: 选出后最多10个交易日")
    print()

    all_results = []

    for p_idx, pick_date in enumerate(pick_dates):
        print(f"[{p_idx+1}/{len(pick_dates)}] 回测选股日 {pick_date}...", end=' ')
        results = backtest_single_pick_date(pick_date, trade_dates_all)
        buy_count = sum(1 for r in results if r['signal'] == 'BUY')
        print(f"{len(results)}只 | BUY触发: {buy_count}只")
        all_results.extend(results)

    if not all_results:
        print("无回测数据！")
        return

    df_all = pd.DataFrame(all_results)
    df_buy = df_all[df_all['signal'] == 'BUY'].copy()

    # === 汇总统计 ===
    print("\n" + "=" * 70)
    print("历史回测汇总")
    print("=" * 70)

    print(f"\n总选股记录: {len(df_all)}")
    print(f"BUY触发: {len(df_buy)} ({len(df_buy)/len(df_all)*100:.1f}%)")
    print(f"未触发: {len(df_all) - len(df_buy)}")

    if len(df_buy) == 0:
        print("无BUY信号，无法统计收益")
        return

    # 平均等买天数
    print(f"\n平均等待买入天数: {df_buy['days_to_buy'].mean():.1f}天")

    # 收益统计
    print("\n" + "=" * 70)
    print("BUY信号后收益统计")
    print("=" * 70)

    for label in ['T+1', 'T+3', 'T+5']:
        col = f'{label}_pct'
        valid = df_buy[col].dropna()
        if len(valid) == 0:
            continue

        win = (valid > 0).sum()
        loss = (valid <= 0).sum()
        win_rate = win / len(valid) * 100
        avg_ret = valid.mean()
        median_ret = valid.median()
        max_ret = valid.max()
        min_ret = valid.min()

        print(f"\n--- {label} ---")
        print(f"  样本数: {len(valid)}")
        print(f"  胜率: {win_rate:.1f}% ({win}涨 / {loss}跌)")
        print(f"  平均收益: {avg_ret:+.2f}%")
        print(f"  中位数: {median_ret:+.2f}%")
        print(f"  最大: {max_ret:+.2f}%  最小: {min_ret:+.2f}%")

    # 按板块分组
    print("\n" + "=" * 70)
    print("按板块分组（T+3收益）")
    print("=" * 70)
    df_buy['board'] = df_buy['code'].apply(lambda x: '双创' if is_shuangchuang(x) else '主板')
    for board in ['双创', '主板']:
        group = df_buy[df_buy['board'] == board]
        valid = group['T+3_pct'].dropna()
        if len(valid) > 0:
            win_rate = (valid > 0).sum() / len(valid) * 100
            avg_ret = valid.mean()
            print(f"  {board}: {len(valid)}只 | 胜率{win_rate:.0f}% | 平均{avg_ret:+.2f}%")

    # 按买分分组
    print("\n" + "=" * 70)
    print("按买分分组（T+3收益）")
    print("=" * 70)
    for lo, hi, label in [(80, 100, '买分>=80'), (60, 80, '买分60-80'), (0, 60, '买分<60')]:
        group = df_buy[(df_buy['buy_score'] >= lo) & (df_buy['buy_score'] < hi)]
        valid = group['T+3_pct'].dropna()
        if len(valid) > 0:
            win_rate = (valid > 0).sum() / len(valid) * 100
            avg_ret = valid.mean()
            print(f"  {label}: {len(valid)}只 | 胜率{win_rate:.0f}% | 平均{avg_ret:+.2f}%")

    # 按等买天数分组
    print("\n" + "=" * 70)
    print("按等待天数分组（T+3收益）")
    print("=" * 70)
    for lo, hi, label in [(1, 3, '1-2天'), (3, 6, '3-5天'), (6, 11, '6-10天')]:
        group = df_buy[(df_buy['days_to_buy'] >= lo) & (df_buy['days_to_buy'] < hi)]
        valid = group['T+3_pct'].dropna()
        if len(valid) > 0:
            win_rate = (valid > 0).sum() / len(valid) * 100
            avg_ret = valid.mean()
            print(f"  {label}: {len(valid)}只 | 胜率{win_rate:.0f}% | 平均{avg_ret:+.2f}%")

    # 对比：追高买入 vs 回调买入
    print("\n" + "=" * 70)
    print("对比：选股日追高买入 vs 回调买点买入（T+3收益）")
    print("=" * 70)

    # 追高：选股日当天买入
    chase_valid = []
    for _, r in df_all.iterrows():
        if pd.notna(r.get('final_score')):
            chase_valid.append(r)
    # 用选股日收盘价到T+3的收益

    print(f"  回调买点: {len(df_buy['T+3_pct'].dropna())}只 | "
          f"胜率{(df_buy['T+3_pct'].dropna() > 0).sum()/len(df_buy['T+3_pct'].dropna())*100:.0f}% | "
          f"平均{df_buy['T+3_pct'].dropna().mean():+.2f}%")
    print(f"  （追高买入的对比数据见theme_pattern回测结果）")

    # 按选股日分组
    print("\n" + "=" * 70)
    print("按选股日分组（T+3收益）")
    print("=" * 70)
    for pick_date in sorted(df_buy['pick_date'].unique()):
        group = df_buy[df_buy['pick_date'] == pick_date]
        valid = group['T+3_pct'].dropna()
        if len(valid) > 0:
            win_rate = (valid > 0).sum() / len(valid) * 100
            avg_ret = valid.mean()
            print(f"  {pick_date}: BUY {len(group)}只 | 有效{len(valid)}只 | 胜率{win_rate:.0f}% | 平均{avg_ret:+.2f}%")

    # 保存
    output_file = os.path.join(REPORT_DIR, "watchlist_buy_signal_backtest.csv")
    df_all.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n详细结果已保存: {output_file}")


if __name__ == '__main__':
    main()
