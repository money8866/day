"""
长时间区间震荡后突破阳线信号检测

核心逻辑：
  股价在较长时间（30~60天）内窄幅震荡（高/低波动≤15%），
  随后出现放量大阳线突破震荡区间。

特征识别：
  1. 震荡期内价格上限/下限比值 ≤ 15%（窄幅）
  2. 震荡期内日均振幅 ≤ 4%（低波动）
  3. 震荡期内成交量逐步萎缩（量比趋势向下）
  4. 突破日：涨幅≥4%、量比≥1.3、收盘站上震荡区间上沿

用法：
  python consolidation_breakout.py --pool qualified --recent 80
  python consolidation_breakout.py 600460 002409 002747
"""

import os, sys, json, time, argparse, sqlite3
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 参数
CONSOLIDATE_MIN_DAYS = 30      # 最短震荡天数
CONSOLIDATE_MAX_DAYS = 40      # 最长震荡天数
BREAKOUT_PCT = 4.0             # 突破阳线最小涨幅(%)
BREAKOUT_VOL = 1.2             # 突破阳线最小量比
RECENT_DAYS = 80               # 默认只分析最近N天
LOOKBACK_DAYS = 90             # 默认回溯天数


def get_stock_data(ts_code: str) -> pd.DataFrame | None:
    """从数据库读取股票数据"""
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql(
            "SELECT trade_date, open, high, low, close, pct_chg, volume_ratio, "
            "ma_bfq_20, ma_bfq_10, kdj_bfq, kdj_k_bfq, rsi_bfq_6, "
            "ma_bfq_60, ma_bfq_30, ma_bfq_90 "
            "FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date",
            conn, params=(ts_code,)
        )
        if df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        return df
    except Exception as e:
        return None
    finally:
        conn.close()


def detect_consolidation(df: pd.DataFrame, signal_idx: int) -> dict | None:
    """
    检测 signal_idx 当天是否为震荡突破信号

    震荡特征（信号前30~40天）：
      - 价格围绕MA20上下波动，大多数日在MA20 ±15%以内
      - MA20走平或微升（20天内MA20涨幅 < 10%），无单边趋势
      - 震荡期内最大振幅（高/低）≤ 28%
      
    突破日：
      - 涨幅 ≥ 4%
      - 量比 ≥ 1.3
      - 收盘价突破震荡区间上沿
      - 收盘站上MA20 +5%以上
    """
    row = df.iloc[signal_idx]
    trade_date = str(row['trade_date'])
    close = row['close']
    pct_chg = row.get('pct_chg', 0) or 0
    vol_ratio = row.get('volume_ratio', 0) or 0
    ma20 = row.get('ma_bfq_20', 0) or 0

    if ma20 <= 0:
        return None

    # 突破必要条件
    if pct_chg < BREAKOUT_PCT:
        return None
    if vol_ratio < BREAKOUT_VOL:
        return None

    # 回溯震荡区间（往前最多 CONSOLIDATE_MAX_DAYS 天）
    start_idx = max(0, signal_idx - CONSOLIDATE_MAX_DAYS)
    seg = df.iloc[start_idx:signal_idx]

    if len(seg) < CONSOLIDATE_MIN_DAYS:
        return None

    # === 条件1：价格在MA20附近震荡（大多数日围绕MA20 ±15%）===
    seg['ma20_val'] = seg['ma_bfq_20'].fillna(0)
    ma20_dist = (seg['close'] / seg['ma20_val'].replace(0, pd.NA) - 1).abs()
    inside_band = (ma20_dist <= 0.15).sum()
    band_ratio = inside_band / len(seg)

    if band_ratio < 0.75:  # 至少75%的天数在MA20 ±15%以内
        return None

    # === 条件2：MA20走平（震荡期内MA20变化 < 15%）===
    ma20_start = seg.iloc[max(0, len(seg) - 20)]['ma_bfq_20'] if len(seg) >= 20 else seg.iloc[0]['ma_bfq_20']
    ma20_start = ma20_start or 1
    ma20_current = ma20 or 1
    ma20_change = (ma20_current / ma20_start - 1) * 100

    if ma20_change > 15.0:  # MA20涨太多=上升趋势，不是震荡
        return None

    # === 条件3：震荡期间总振幅（高/低）≤ 28% ===
    seg_high = seg['high'].max()
    seg_low = seg['low'].min()
    if seg_low <= 0:
        return None
    total_range = (seg_high / seg_low - 1) * 100
    if total_range > 30.0:
        return None

    # === 条件4：突破当日收盘站上MA20 +5% ===
    above_ma20 = (close / ma20 - 1) * 100
    if above_ma20 < 5.0:
        return None

    # 评分
    band_score = band_ratio * 25
    range_score = max(0, (1 - total_range / 28.0)) * 25
    breakout_score = min(pct_chg / BREAKOUT_PCT, 3.0) * 25
    vol_score = min(vol_ratio / BREAKOUT_VOL, 3.0) * 25

    return {
        'signal_date': trade_date,
        'signal_close': round(close, 2),
        'signal_score': round(band_score + range_score + breakout_score + vol_score, 0),
        'pct_chg': round(pct_chg, 2),
        'vol_ratio': round(vol_ratio, 2),
        'rsi6': round(row.get('rsi_bfq_6', 0) or 0, 1),
        'kdj_j': round(row.get('kdj_bfq', 0) or 0, 1),
        'ma20': round(ma20, 2),
        'ma10': round(row.get('ma_bfq_10', 0) or 0, 2),
        'consolidate_days': len(seg),
        'ma20_band_ratio': round(band_ratio * 100, 1),
        'total_range': round(total_range, 1),
        'ma20_change': round(ma20_change, 1),
        'above_ma20_pct': round(above_ma20, 2),
    }


def log(msg: str):
    print(f"  {msg}")


def normalize_ts_code(code: str) -> str:
    code = code.strip().upper()
    if '.' in code:
        return code
    if code.startswith('6') or code.startswith('9'):
        return code + '.SH'
    return code + '.SZ'


def load_qualified_pool() -> list:
    """从 bull_stocks 相关 CSV 读取合格股票池"""
    candidates = [
        r"D:\mystock\solo\multi_factor_picker\output",
        r"D:\mystock\report_daily",
    ]
    csv_path = None
    for base_dir in candidates:
        if not os.path.isdir(base_dir):
            continue
        files = sorted(
            [f for f in os.listdir(base_dir) if f.startswith('bull_stocks_') and f.endswith('.csv')],
            reverse=True
        )
        if files:
            csv_path = os.path.join(base_dir, files[0])
            break
        fixed = os.path.join(base_dir, "bull_stocks_qualified.csv")
        if os.path.exists(fixed):
            csv_path = fixed
            break

    if csv_path is None:
        log(f"[警告] 未找到 bull_stocks_*.csv")
        return []

    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    codes = [normalize_ts_code(str(c)) for c in df['code'].tolist()]
    log(f"[股票池] 从 {csv_path} 读取 {len(codes)} 只合格标的")
    return codes


def main():
    parser = argparse.ArgumentParser(description='区间震荡突破阳线信号检测')
    parser.add_argument('codes', nargs='*', help='股票代码')
    parser.add_argument('--pool', choices=['default', 'qualified'], default='default',
                        help='股票池: default(24只核心股) / qualified(bull_stocks_qualified.csv)')
    parser.add_argument('--recent', type=int, default=RECENT_DAYS,
                        help=f'只分析最近N天 (默认{RECENT_DAYS}天)')
    parser.add_argument('--lookback', type=int, default=LOOKBACK_DAYS,
                        help=f'回溯天数 (默认{LOOKBACK_DAYS}天)')
    args = parser.parse_args()

    # 确定股票池
    if args.codes:
        stock_codes = [normalize_ts_code(c) for c in args.codes]
    elif args.pool == 'qualified':
        stock_codes = load_qualified_pool()
        if not stock_codes:
            return
    else:
        stock_codes = [
            '600460.SH', '002409.SZ', '002747.SZ', '300179.SZ', '300319.SZ',
            '600160.SH', '600309.SH', '688551.SH', '688668.SH', '688268.SH',
            '300054.SZ', '002821.SZ', '688003.SH', '000725.SZ',
        ]

    log(f"股票池: {args.pool} ({len(stock_codes)} 只)")
    log(f"分析最近: {args.recent} 天, 回溯: {args.lookback} 天")
    log(f"震荡参数: MA20附近震荡, 振幅≤30%, 突破≥{BREAKOUT_PCT}%+量比≥{BREAKOUT_VOL}")
    log("")

    all_signals = []
    total = len(stock_codes)
    has_signal_count = 0

    for i, ts_code in enumerate(stock_codes):
        if (i + 1) % 50 == 0:
            log(f"[{i+1}/{total}] 已发现 {len(all_signals)} 个信号...")

        df = get_stock_data(ts_code)
        if df is None or len(df) < CONSOLIDATE_MIN_DAYS + 10:
            continue

        # 截取最近N天
        if args.recent and args.recent < len(df):
            df = df.iloc[-args.recent:].reset_index(drop=True)

        signals = []
        # 对每一天做震荡突破检测
        for idx in range(CONSOLIDATE_MIN_DAYS, len(df)):
            sig = detect_consolidation(df, idx)
            if sig is not None:
                signals.append(sig)

        if signals:
            has_signal_count += 1
            # 合并成最终格式
            for sig in signals:
                entry_idx = df[df['trade_date'] == sig['signal_date']].index[0]
                entry_price = df.iloc[entry_idx]['close']

                # 计算后续收益
                future_windows = [1, 5, 10, 20]
                rets = {}
                for w in future_windows:
                    future_idx = entry_idx + w
                    if future_idx >= len(df):
                        future_idx = len(df) - 1
                    rets[w] = round((df.iloc[future_idx]['close'] / entry_price - 1) * 100, 2)

                all_signals.append({
                    'ts_code': ts_code,
                    'signal_date': sig['signal_date'],
                    'entry_date': sig['signal_date'],
                    'entry_price': entry_price,
                    'signal_score': sig['signal_score'],
                    'signal_pct_chg': sig['pct_chg'],
                    'signal_vol_ratio': sig['vol_ratio'],
                    'above_ma20_pct': sig['above_ma20_pct'],
                    'rsi6': sig['rsi6'],
                    'ma20_band_ratio': sig['ma20_band_ratio'],
                    'total_range': sig['total_range'],
                    'ma20_change': sig['ma20_change'],
                    'return_1d': rets[1],
                    'return_5d': rets[5],
                    'return_10d': rets[10],
                    'return_20d': rets[20],
                    'entry_method': '震荡突破',
                })

    log(f"")
    log(f"扫描完成！{has_signal_count} 只有信号, 共 {len(all_signals)} 个信号")

    if all_signals:
        # 输出结果
        signal_df = pd.DataFrame(all_signals)
        cols = ['signal_date', 'entry_date', 'ts_code', 'entry_method', 'signal_score',
                'total_range', 'ma20_band_ratio', 'ma20_change', 'above_ma20_pct', 'rsi6',
                'signal_pct_chg', 'signal_vol_ratio',
                'return_1d', 'return_5d', 'return_10d', 'return_20d',
                'entry_price']
        try:
            signal_df = signal_df[cols]
        except:
            pass
        signal_df = signal_df.sort_values(['entry_date', 'ts_code']).reset_index(drop=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_path = os.path.join(OUTPUT_DIR, f"consolidation_breakout_{timestamp}_{args.pool}.csv")
        signal_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        log(f"  CSV: {csv_path}")

        # 打印汇总统计
        sub = signal_df.copy()
        sub['entry_date'] = sub['entry_date'].astype(str)
        sub = sub[(sub['entry_date'] >= '20260501') & (sub['entry_date'] <= '20260625')]
        print(f"\n  20260501~20260625 区间内: {len(sub)} 个信号")

        if len(sub) > 0:
            for w in [1, 5, 10, 20]:
                r = sub[f'return_{w}d'].dropna()
                wins = r[r > 0]
                print(f"    +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>5.1f}%  亏>15%={(r<-15).sum()}  最大={r.max():>6.2f}%  最小={r.min():>6.2f}%")

            # TOP5
            if len(sub) >= 5:
                top5 = sub.nlargest(5, 'return_10d')
                print(f"\n  TOP5 最好:")
                for _, r in top5.iterrows():
                    print(f"    {str(r['signal_date'])} {r['ts_code']} +10d={r['return_10d']:>6.2f}% +20d={r['return_20d']:>6.2f}%")
                bot5 = sub.nsmallest(5, 'return_10d')
                print(f"  TOP5 最差:")
                for _, r in bot5.iterrows():
                    print(f"    {str(r['signal_date'])} {r['ts_code']} +10d={r['return_10d']:>6.2f}% +20d={r['return_20d']:>6.2f}%")
    else:
        log(f"  无信号产生")


if __name__ == '__main__':
    main()
