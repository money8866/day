"""
B浪策略真正历史回测脚本
=================================
模拟每个历史交易日发出信号，跟踪未来N天收益。

用法:
  python bwave_backtest.py --start 2025-01-01 --end 2026-06-30
  python bwave_backtest.py --pool qualified --start 2025-06-01
"""

import os, sys, argparse, sqlite3
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_data(ts_code: str) -> pd.DataFrame | None:
    """获取股票数据（复用bwave_strategy的逻辑）"""
    conn = sqlite3.connect(DB)
    try:
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, volume_ratio,
                        ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_30, ma_bfq_60, ma_bfq_90,
                        macd_dif_bfq, macd_dea_bfq, macd_bfq,
                        rsi_bfq_6
                 FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date"""
        df = pd.read_sql(sql, conn, params=(ts_code,))
        if df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.fillna(0)

        # 计算缺失字段
        df['ma_120'] = df['close'].rolling(120).mean().fillna(0)
        df['ma_250'] = df['close'].rolling(250).mean().fillna(0)

        df['prev_close'] = df['close'].shift(1).fillna(0)
        df['tr'] = df.apply(
            lambda r: max(r['high'] - r['low'],
                          abs(r['high'] - r['prev_close']) if r['prev_close'] > 0 else 0,
                          abs(r['low'] - r['prev_close']) if r['prev_close'] > 0 else 0),
            axis=1
        )
        df['atr'] = df['tr'].rolling(14).mean().fillna(0)
        df = df.drop(columns=['prev_close', 'tr'])

        return df
    except Exception as e:
        return None
    finally:
        conn.close()


def detect_awave(df: pd.DataFrame, end_idx: int) -> dict | None:
    """识别A浪（主升浪）— 限制在end_idx之前的数据"""
    if end_idx < 130:
        return None

    lookback = min(120, end_idx - 10)
    start_idx = end_idx - lookback

    # 找局部低点和局部高点
    lows = []
    highs = []
    for i in range(start_idx, end_idx - 1):
        if df.iloc[i]['close'] <= df.iloc[i - 1]['close'] and df.iloc[i]['close'] <= df.iloc[i + 1]['close']:
            lows.append(i)
        if df.iloc[i]['close'] >= df.iloc[i - 1]['close'] and df.iloc[i]['close'] >= df.iloc[i + 1]['close']:
            highs.append(i)

    best = None

    for a_start in lows:
        if a_start < start_idx:
            continue
        for a_end in highs:
            if a_end <= a_start + 20 or a_end > min(a_start + 60, end_idx - 5):
                continue

            start_price = df.iloc[a_start]['close']
            end_price = df.iloc[a_end]['close']
            if start_price <= 0:
                continue

            gain = (end_price / start_price - 1) * 100
            if gain < 60:
                continue

            # 检查MA20上行和价格在MA20之上（简化版）
            ma20_up = sum(1 for i in range(a_start, a_end + 1)
                         if df.iloc[i]['close'] > df.iloc[i]['ma_bfq_20'] > 0)
            if ma20_up / max(a_end - a_start, 1) < 0.6:
                continue

            if best is None or gain > best.get('gain', 0):
                best = {
                    'start_idx': a_start,
                    'end_idx': a_end,
                    'gain': gain,
                    'duration': a_end - a_start,
                }

    return best


def detect_bwave(df: pd.DataFrame, awave: dict, end_idx: int) -> dict | None:
    """检测B浪（回调浪）— 限制在end_idx之前的数据"""
    a_end = awave['end_idx']
    a_high = df.iloc[a_end]['close']
    a_duration = awave['duration']

    best = None
    search_end = min(a_end + a_duration * 2 + 10, end_idx - 5)

    for b_low in range(a_end + int(a_duration * 0.8), search_end + 1):
        if b_low >= len(df) or b_low >= end_idx:
            break

        # 找B浪最低点
        seg = df.iloc[a_end:min(b_low + 10, len(df))]
        real_low_idx = seg['close'].idxmin()
        low_price = df.loc[real_low_idx, 'close']

        drop = (a_high - low_price) / a_high * 100
        if drop < 20 or drop > 40:
            continue

        b_duration = real_low_idx - a_end
        if b_duration < a_duration * 0.8:
            continue

        # 简化版：只要找到B浪低点就返回
        if best is None or drop < best.get('drop', 100):
            best = {
                'low_idx': real_low_idx,
                'drop': drop,
                'duration': b_duration,
            }

    return best


def check_launch_signal(df: pd.DataFrame, awave: dict, bwave: dict, end_idx: int) -> dict | None:
    """检测启动信号 — 限制在end_idx之前的数据"""
    low_idx = bwave['low_idx']
    b_low = bwave['low_price'] if 'low_price' in bwave else df.iloc[low_idx]['close']
    a_high = df.iloc[awave['end_idx']]['close']

    # 从B浪低点后扫描，找启动信号
    scan_end = min(low_idx + 41, end_idx)
    for launch in range(scan_end - 1, low_idx - 1, -1):
        if launch >= end_idx:
            continue

        close = df.iloc[launch]['close']

        # 价格已从B浪低点恢复
        if close < b_low * 1.02:
            continue

        # 未突破A浪高点
        if close > a_high * 1.05:
            continue

        # 简化版：只要放量+MACD改善就返回
        vol = df.iloc[launch]['vol']
        avg_vol_20 = df.iloc[max(0, launch - 20):launch]['vol'].mean()
        vol_surge = vol > avg_vol_20 * 1.1 if avg_vol_20 > 0 else False

        dif = df.iloc[launch]['macd_dif_bfq']
        dea = df.iloc[launch]['macd_dea_bfq']
        macd_golden = dif > dea

        if vol_surge and macd_golden:
            return {
                'launch_idx': launch,
                'launch_price': close,
            }

    return None


def run_backtest(ts_code: str, start_date: str, end_date: str) -> list:
    """对单只股票运行历史回测"""
    df = get_data(ts_code)
    if df is None or len(df) < 250:
        return []

    # 找到起始和结束日期的索引
    date_list = df['trade_date'].tolist()
    if start_date not in date_list or end_date not in date_list:
        return []

    start_idx = date_list.index(start_date)
    end_idx = date_list.index(end_date)

    signals = []

    # 遍历每个交易日
    for idx in range(start_idx + 250, end_idx + 1):
        awave = detect_awave(df, idx)
        if awave is None:
            continue

        bwave = detect_bwave(df, awave, idx)
        if bwave is None:
            continue

        # 检查启动信号
        launch = check_launch_signal(df, awave, bwave, idx)
        if launch is None:
            continue

        # 计算未来收益
        entry_price = launch['launch_price']
        future_rets = {}
        for w in [1, 5, 10, 20]:
            fi = min(launch['launch_idx'] + w, len(df) - 1)
            future_rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0

        signals.append({
            'ts_code': ts_code,
            'signal_date': df.iloc[launch['launch_idx']]['trade_date'],
            'entry_price': entry_price,
            'a_gain': awave['gain'],
            'b_drop': bwave['drop'],
            'return_1d': future_rets[1],
            'return_5d': future_rets[5],
            'return_10d': future_rets[10],
            'return_20d': future_rets[20],
        })

    return signals


def main():
    parser = argparse.ArgumentParser(description='B浪策略历史回测')
    parser.add_argument('--pool', choices=['qualified', 'all'], default='qualified')
    parser.add_argument('--start', type=str, default='2025-06-01')
    parser.add_argument('--end', type=str, default='2026-06-30')
    args = parser.parse_args()

    # 加载股票池
    if args.pool == 'qualified':
        candidates = [
            r"D:\mystock\solo\multi_factor_picker\output",
        ]
        csv_path = None
        for base_dir in candidates:
            if not os.path.isdir(base_dir):
                continue
            files = sorted([f for f in os.listdir(base_dir)
                            if f.startswith('bull_stocks_') and f.endswith('.csv')],
                           reverse=True)
            if files:
                csv_path = os.path.join(base_dir, files[0])
                break
        if csv_path is None:
            print('错误：未找到股票池CSV')
            return
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
        stock_codes = [str(c).strip().upper() for c in df['code'].tolist()]
    else:
        stock_codes = []  # TODO: 全市场

    print(f'历史回测 — {args.start} 至 {args.end}')
    print(f'股票池: {args.pool} ({len(stock_codes)}只)')
    print()

    all_signals = []
    total = len(stock_codes)

    for i, ts_code in enumerate(stock_codes):
        if (i + 1) % 50 == 0:
            print(f'[{i+1}/{total}] 回测中...已发现{len(all_signals)}个信号')

        signals = run_backtest(ts_code, args.start, args.end)
        all_signals.extend(signals)

    print(f'\n回测完成！共 {len(all_signals)} 个信号')

    if not all_signals:
        print('无信号')
        return

    # 保存CSV
    df_out = pd.DataFrame(all_signals)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(OUTPUT_DIR, f'bwave_backtest_{timestamp}.csv')
    df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'CSV: {csv_path}')

    # 统计
    print(f'\n统计:')
    for w in [1, 5, 10, 20]:
        r = df_out[f'return_{w}d'].dropna()
        wins = r[r > 0]
        print(f'  +{w}d: 均={r.mean():.2f}%  胜率={len(wins)/len(r)*100:.0f}%  盈亏比={r[r>0].mean()/abs(r[r<0].mean()):.2f}')


if __name__ == '__main__':
    main()
