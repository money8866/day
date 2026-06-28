"""
趋势交易系统 v2 — 4规则 + 强趋势评分
====================================
规则1: 趋势启动条件
规则2: 趋势延续（持股逻辑）
规则3: 二波机会识别
规则4: 强趋势股评分系统

Usage:
  python trend_system_v2.py --pool qualified --recent 80
  python trend_system_v2.py 600460 002409
"""

import os, sys, argparse, sqlite3
from datetime import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_data(ts_code: str) -> pd.DataFrame | None:
    conn = sqlite3.connect(DB)
    try:
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, amount, volume_ratio,
                        ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_30, ma_bfq_60, ma_bfq_90,
                        macd_dif_bfq, macd_dea_bfq, macd_bfq,
                        rsi_bfq_6, kdj_bfq, kdj_k_bfq
                 FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date"""
        df = pd.read_sql(sql, conn, params=(ts_code,))
        if df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.fillna(0)
        return df
    except Exception as e:
        return None
    finally:
        conn.close()


def ma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def vol_ma(df: pd.DataFrame, idx: int, window: int = 20) -> float:
    """计算前 window 日的日均成交量（手）"""
    start = max(0, idx - window)
    seg = df.iloc[start:idx]
    if len(seg) < 5:
        return 0
    return seg['vol'].mean()


# ============================================================
# 规则1: 趋势启动条件
# ============================================================
def check_trend_launch(df: pd.DataFrame, idx: int) -> dict | None:
    """检测第 idx 天是否满足趋势启动条件"""
    if idx < 60:
        return None

    row = df.iloc[idx]
    close = row['close']
    ma20 = row['ma_bfq_20']
    ma60 = row['ma_bfq_60']

    if ma20 <= 0 or ma60 <= 0:
        return None

    # 条件1: MA20走平或上拐（5天前MA20 vs 今天MA20）
    before = max(0, idx - 5)
    ma20_ago = df.iloc[before]['ma_bfq_20'] if df.iloc[before]['ma_bfq_20'] > 0 else ma20
    ma20_trend = (ma20 / ma20_ago - 1) * 100
    ma20_flat_or_up = ma20_trend > -2.0  # 5天内跌幅不超过2%即视为走平或上拐

    # 条件2: 突破60日均线 or 前60日高点
    seg_high = df.iloc[max(0, idx - 60):idx]['high'].max()
    break_ma60 = close > ma60 * 1.01
    break_60d_high = close > seg_high * 1.01
    breakout = break_ma60 or break_60d_high

    # 条件3: 成交量 > 20日均量 × 1.5
    avg_vol_20 = vol_ma(df, idx, 20)
    current_vol = row['vol']
    vol_condition = current_vol > avg_vol_20 * 1.5 if avg_vol_20 > 0 else False

    # 条件4: MACD零轴附近或金叉
    dif = row.get('macd_dif_bfq', 0)
    dea = row.get('macd_dea_bfq', 0)
    macd_near_zero = abs(dif) < 1.0  # 零轴附近
    golden_cross = dif > dea  # DIF > DEA 视为金叉或金叉后
    macd_ok = macd_near_zero or (golden_cross and dif > -0.5)

    if not (ma20_flat_or_up and breakout and vol_condition and macd_ok):
        return None

    # 评分简单评分
    score = 0
    if ma20_trend > 0: score += 10
    if close > ma60 * 1.03: score += 15
    if current_vol > avg_vol_20 * 2: score += 10
    if dif > dea: score += 10
    if dif > 0: score += 10

    above_ma20 = (close / ma20 - 1) * 100
    above_ma60 = (close / ma60 - 1) * 100

    return {
        'type': '趋势启动',
        'signal_date': str(row['trade_date']),
        'signal_close': round(close, 2),
        'signal_score': score,
        'pct_chg': round(row.get('pct_chg', 0), 2),
        'vol_ratio': round(row.get('volume_ratio', 0), 2),
        'ma20': round(ma20, 2),
        'ma60': round(ma60, 2),
        'above_ma20_pct': round(above_ma20, 2),
        'above_ma60_pct': round(above_ma60, 2),
        'ma20_trend': round(ma20_trend, 2),
        'vol_surge': round(current_vol / avg_vol_20 if avg_vol_20 > 0 else 0, 2),
        'macd_dif': round(dif, 2),
        'macd_dea': round(dea, 2),
        'macd_golden': 1 if dif > dea else 0,
    }


# ============================================================
# 规则2: 趋势延续检测
# ============================================================
def check_trend_continuation(df: pd.DataFrame, launch_idx: int) -> list:
    """
    从启动日之后，检测是否符合趋势延续条件
    返回 [{'date':, 'hold': True/False, 'reason':}, ...]
    """
    results = []
    for idx in range(launch_idx + 1, len(df)):
        row = df.iloc[idx]
        close = row['close']
        ma20 = row['ma_bfq_20']
        if ma20 <= 0:
            results.append({'date': str(row['trade_date']), 'hold': False, 'reason': '无MA20'})
            continue

        # 条件1: 股价不破20日线
        above_ma20 = (close / ma20 - 1) * 100
        if above_ma20 < -3.0:
            results.append({'date': str(row['trade_date']), 'hold': False, 'reason': f'跌破MA20({above_ma20:.1f}%)'})
            continue

        # 条件2: 回撤幅度 < 8%（从启动日high算）
        high_since_launch = df.iloc[launch_idx:idx + 1]['high'].max()
        drawdown = (high_since_launch - close) / high_since_launch * 100
        if drawdown > 8.0:
            results.append({'date': str(row['trade_date']), 'hold': False, 'reason': f'回撤{drawn:.1f}%>8%'})
            continue

        # 条件3: 调整时缩量(<5日均量)
        avg_vol_5 = vol_ma(df, idx, 5)
        current_vol = row['vol']
        vol_shrink = current_vol < avg_vol_5 * 0.8 if avg_vol_5 > 0 else False

        # 条件4: 高低点抬升（5天内低点逐步抬高）
        recent = df.iloc[max(0, idx - 5):idx + 1]
        hh_hl = recent['low'].is_monotonic_increasing if len(recent) >= 3 else False

        hold = True
        reasons = []
        if not vol_shrink:
            reasons.append('量未缩')
        if not hh_hl:
            reasons.append('低点未抬')
        reason_str = ', '.join(reasons) if reasons else '正常持股'

        results.append({
            'date': str(row['trade_date']),
            'hold': hold,
            'reason': reason_str,
            'above_ma20': round(above_ma20, 2),
            'drawdown': round(drawdown, 2),
            'vol_ratio': round(current_vol / avg_vol_5 if avg_vol_5 > 0 else 0, 2),
        })

    return results


# ============================================================
# 规则3: 二波机会识别
# ============================================================
def check_second_wave(df: pd.DataFrame, idx: int) -> dict | None:
    """
    检测二波机会：
    1. 前60天内有一波涨幅≥25%的上升
    2. 之后横盘5~20天，成交量萎缩
    3. 当天再次放量突破前高
    """
    if idx < 60:
        return None

    row = df.iloc[idx]
    close = row['close']
    pct_chg = row.get('pct_chg', 0)

    # 先找前60日内的最低点和最高点
    seg = df.iloc[max(0, idx - 60):idx]
    seg_low_idx = seg['close'].idxmin()
    seg_high_idx = seg['close'].idxmax()

    low_price = df.loc[seg_low_idx, 'close']
    high_price = df.loc[seg_high_idx, 'close']

    if low_price <= 0:
        return None

    # 条件1: 第一波涨幅≥25%
    first_wave_gain = (high_price / low_price - 1) * 100
    if first_wave_gain < 25.0:
        return None

    # 高点到当前的天数 = 横盘时间
    high_pos = df.index.get_loc(seg_high_idx)
    consolidation_days = idx - high_pos

    # 条件2: 横盘5~20天
    if consolidation_days < 5 or consolidation_days > 20:
        return None

    # 横盘区间（高点之后）
    consol = df.iloc[high_pos:idx]

    # 条件3: 成交量萎缩(横盘期均量 < 前一波放量期均量的60%)
    vol_before = df.iloc[max(0, high_pos - 20):high_pos]['vol'].mean()
    vol_consol = consol['vol'].mean()
    vol_shrink = vol_consol < vol_before * 0.6 if vol_before > 0 else False

    # 条件4: 当天再次放量突破前高
    current_vol = row['vol']
    avg_vol_20 = vol_ma(df, idx, 20)
    vol_surge = current_vol > avg_vol_20 * 1.5 if avg_vol_20 > 0 else False
    break_high = close > high_price * 1.01

    if not (vol_shrink and vol_surge and break_high):
        return None

    # 评分
    score = 0
    if pct_chg >= 7: score += 20
    if current_vol > avg_vol_20 * 2: score += 15
    if consolidation_days >= 10: score += 10
    if first_wave_gain >= 40: score += 15

    above_ma20 = (close / row['ma_bfq_20'] - 1) * 100 if row['ma_bfq_20'] > 0 else 0

    return {
        'type': '二波启动',
        'signal_date': str(row['trade_date']),
        'signal_close': round(close, 2),
        'signal_score': score,
        'pct_chg': round(pct_chg, 2),
        'vol_ratio': round(row.get('volume_ratio', 0), 2),
        'first_wave_gain': round(first_wave_gain, 1),
        'consolidation_days': consolidation_days,
        'above_ma20_pct': round(above_ma20, 2),
        'vol_shrink_ratio': round(vol_consol / vol_before if vol_before > 0 else 0, 2),
        'vol_surge_ratio': round(current_vol / avg_vol_20 if avg_vol_20 > 0 else 0, 2),
    }


# ============================================================
# 规则4: 强趋势评分系统
# ============================================================
def calc_trend_score(df: pd.DataFrame, idx: int) -> dict:
    """
    Trend Score = 
      均线多头排列（+20）
      回撤不破20日线（+20）
      放量突破（+25）
      横盘缩量（+15）
      行业β强（+20）  → 用相对强度替代
    
    >80 = 强趋势中线股
    """
    if idx < 60:
        return {'score': 0}

    row = df.iloc[idx]
    close = row['close']
    ma5 = float(row['ma_bfq_5']) if pd.notna(row['ma_bfq_5']) else 0
    ma10 = float(row['ma_bfq_10']) if pd.notna(row['ma_bfq_10']) else 0
    ma20 = float(row['ma_bfq_20']) if pd.notna(row['ma_bfq_20']) else 0
    ma60 = float(row['ma_bfq_60']) if pd.notna(row['ma_bfq_60']) else 0

    if ma20 <= 0 or ma60 <= 0:
        return {'score': 0}

    details = {}
    total = 0

    # 1. 均线多头排列（+20）
    bull_arrange = (ma5 > ma10 > ma20 > ma60)
    partial_arrange = (ma10 > ma20 > ma60)
    both_arrange = ma20 > ma60

    if bull_arrange:
        ma_score = 20
    elif partial_arrange and both_arrange:
        ma_score = 12
    elif both_arrange:
        ma_score = 6
    else:
        ma_score = 0
    total += ma_score
    details['均线多头'] = ma_score

    # 2. 回撤不破20日线（+20）
    # 检查前20天内是否跌破MA20超3%
    recent = df.iloc[max(0, idx - 20):idx + 1]
    ma20_dist = (recent['close'] / recent['ma_bfq_20'].replace(0, pd.NA) - 1) * 100
    broke_ma20 = (ma20_dist < -3.0).any()

    if not broke_ma20:
        pullback_score = 20
    else:
        # 跌破但已收回
        if (close / ma20 - 1) * 100 > 3:
            pullback_score = 10
        else:
            pullback_score = 0
    total += pullback_score
    details['回撤支撑'] = pullback_score

    # 3. 放量突破（+25）
    avg_vol_20 = vol_ma(df, idx, 20)
    current_vol = row['vol']
    vol_ratio = current_vol / avg_vol_20 if avg_vol_20 > 0 else 0

    if vol_ratio > 2.0:
        vol_score = 25
    elif vol_ratio > 1.5:
        vol_score = 18
    elif vol_ratio > 1.2:
        vol_score = 10
    else:
        vol_score = 0
    total += vol_score
    details['放量突破'] = vol_score

    # 4. 横盘缩量（+15）
    # 前20天量 vs 前40天量
    vol_20 = df.iloc[max(0, idx - 20):idx]['vol'].mean()
    vol_40 = df.iloc[max(0, idx - 40):max(0, idx - 20)]['vol'].mean()
    vol_shrink = vol_20 < vol_40 * 0.8 if vol_40 > 0 else False
    consol_score = 15 if vol_shrink else 0
    total += consol_score
    details['横盘缩量'] = consol_score

    # 5. 相对强度（+20）— 用最近20日涨幅替代行业β
    gain_20d = (close / df.iloc[max(0, idx - 20)]['close'] - 1) * 100 if idx >= 20 else 0
    if gain_20d > 20:
        rs_score = 20
    elif gain_20d > 10:
        rs_score = 14
    elif gain_20d > 5:
        rs_score = 8
    elif gain_20d > 0:
        rs_score = 4
    else:
        rs_score = 0
    total += rs_score
    details['相对强度'] = rs_score

    return {
        'score': total,
        'details': details,
        'ma_arrange': '多头' if bull_arrange else ('部分' if partial_arrange else '空头'),
        'gain_20d': round(gain_20d, 1),
        'vol_ratio_20': round(vol_ratio, 2),
    }


# ============================================================
# 主程序
# ============================================================

def normalize_ts_code(code: str) -> str:
    code = code.strip().upper()
    if '.' in code:
        return code
    if code.startswith(('6', '9')):
        return code + '.SH'
    return code + '.SZ'


def load_qualified_pool() -> list:
    candidates = [
        r"D:\mystock\solo\multi_factor_picker\output",
        r"D:\mystock\report_daily",
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
        fixed = os.path.join(base_dir, "bull_stocks_qualified.csv")
        if os.path.exists(fixed):
            csv_path = fixed
            break
    if csv_path is None:
        return []
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    return [normalize_ts_code(str(c)) for c in df['code'].tolist()]


def log(msg: str):
    print(f"  {msg}")


def main():
    parser = argparse.ArgumentParser(description='趋势交易系统 v2')
    parser.add_argument('codes', nargs='*')
    parser.add_argument('--pool', choices=['default', 'qualified'], default='default')
    parser.add_argument('--recent', type=int, default=80)
    parser.add_argument('--score', type=int, default=0,
                        help='最低评分过滤 (默认0=全部)')
    args = parser.parse_args()

    if args.codes:
        stock_codes = [normalize_ts_code(c) for c in args.codes]
    elif args.pool == 'qualified':
        stock_codes = load_qualified_pool()
        if not stock_codes:
            log("[错误] 股票池为空")
            return
    else:
        stock_codes = [
            '600460.SH', '002409.SZ', '002747.SZ', '300179.SZ', '300319.SZ',
            '600160.SH', '600309.SH', '688551.SH', '688668.SH', '688268.SH',
            '300054.SZ', '002821.SZ', '688003.SH', '000725.SZ',
        ]

    log(f"趋势交易系统 v2 — 4规则+评分")
    log(f"股票池: {args.pool} ({len(stock_codes)}只)")
    log(f"分析最近: {args.recent} 天")
    log(f"最低评分过滤: {args.score}")
    log("")

    all_signals = []
    total = len(stock_codes)

    for i, ts_code in enumerate(stock_codes):
        if (i + 1) % 100 == 0:
            log(f"[{i+1}/{total}] 扫描中... 已发现 {len(all_signals)} 个信号")

        df = get_data(ts_code)
        if df is None or len(df) < 90:
            continue

        if args.recent and args.recent < len(df):
            df = df.iloc[-args.recent:].reset_index(drop=True)

        for idx in range(20, len(df)):
            row = df.iloc[idx]

            # 规则1: 趋势启动
            r1 = check_trend_launch(df, idx)
            if r1:
                # 计算未来收益
                entry_price = row['close']
                rets = {}
                for w in [1, 5, 10, 20]:
                    fi = min(idx + w, len(df) - 1)
                    rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2)

                # 评分
                ts_result = calc_trend_score(df, idx)
                trend_score = ts_result['score']
                if args.score > 0 and trend_score < args.score:
                    continue

                all_signals.append({
                    'ts_code': ts_code,
                    'signal_date': r1['signal_date'],
                    'signal_type': '趋势启动',
                    'signal_score': r1['signal_score'],
                    'trend_score': trend_score,
                    'trend_detail': str(ts_result['details']),
                    'pct_chg': r1['pct_chg'],
                    'vol_ratio': r1['vol_ratio'],
                    'above_ma20_pct': r1['above_ma20_pct'],
                    'above_ma60_pct': r1['above_ma60_pct'],
                    'ma20_trend': r1['ma20_trend'],
                    'vol_surge': r1['vol_surge'],
                    'macd_dif': r1['macd_dif'],
                    'macd_golden': r1['macd_golden'],
                    'return_1d': rets[1],
                    'return_5d': rets[5],
                    'return_10d': rets[10],
                    'return_20d': rets[20],
                })

            # 规则3: 二波机会
            r3 = check_second_wave(df, idx)
            if r3:
                entry_price = row['close']
                rets = {}
                for w in [1, 5, 10, 20]:
                    fi = min(idx + w, len(df) - 1)
                    rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2)

                ts_result = calc_trend_score(df, idx)
                trend_score = ts_result['score']
                if args.score > 0 and trend_score < args.score:
                    continue

                all_signals.append({
                    'ts_code': ts_code,
                    'signal_date': r3['signal_date'],
                    'signal_type': '二波启动',
                    'signal_score': r3['signal_score'],
                    'trend_score': trend_score,
                    'trend_detail': str(ts_result['details']),
                    'pct_chg': r3['pct_chg'],
                    'vol_ratio': r3['vol_ratio'],
                    'above_ma20_pct': r3['above_ma20_pct'],
                    'above_ma60_pct': 0,
                    'ma20_trend': 0,
                    'vol_surge': r3['vol_surge_ratio'],
                    'macd_dif': 0,
                    'macd_golden': 0,
                    'return_1d': rets[1],
                    'return_5d': rets[5],
                    'return_10d': rets[10],
                    'return_20d': rets[20],
                })

    log(f"\n扫描完成！共 {len(all_signals)} 个信号")

    if not all_signals:
        log("  无任何信号")
        return

    df_out = pd.DataFrame(all_signals)
    cols = ['signal_date', 'ts_code', 'signal_type', 'signal_score', 'trend_score',
            'pct_chg', 'vol_ratio', 'above_ma20_pct', 'above_ma60_pct',
            'vol_surge', 'macd_dif', 'macd_golden',
            'return_1d', 'return_5d', 'return_10d', 'return_20d']
    df_out = df_out[[c for c in cols if c in df_out.columns]]
    df_out = df_out.sort_values(['signal_date', 'ts_code']).reset_index(drop=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(OUTPUT_DIR, f"trend_system_v2_{timestamp}_{args.pool}.csv")
    df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    log(f"  CSV: {csv_path}")

    # 统计
    sub = df_out.copy()
    sub['signal_date'] = sub['signal_date'].astype(str)
    sub = sub[(sub['signal_date'] >= '20260501') & (sub['signal_date'] <= '20260625')]
    log(f"  20260501~20260625: {len(sub)} 信号")

    if len(sub) > 0:
        for label in ['趋势启动', '二波启动']:
            s2 = sub[sub['signal_type'] == label]
            if len(s2) == 0:
                continue
            print(f"\n  {label} {len(s2)}个:")
            for w in [1, 5, 10, 20]:
                r = s2[f'return_{w}d'].dropna()
                wins = r[r > 0]
                print(f"    +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>5.1f}%  亏>15%={(r<-15).sum()}")

        # 按trend_score分组
        print(f"\n  按趋势评分分组:")
        for bucket, label in [(80, '≥80分'), (60, '60~80分'), (40, '40~60分'), (0, '<40分')]:
            if bucket >= 80:
                s3 = sub[sub['trend_score'] >= 80]
            elif bucket >= 60:
                s3 = sub[(sub['trend_score'] >= 60) & (sub['trend_score'] < 80)]
            elif bucket >= 40:
                s3 = sub[(sub['trend_score'] >= 40) & (sub['trend_score'] < 60)]
            else:
                s3 = sub[sub['trend_score'] < 40]
            if len(s3) == 0:
                continue
            r = s3['return_10d'].dropna()
            wins = r[r > 0]
            print(f"    {label}: {len(s3)}个  均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>5.1f}%")


if __name__ == '__main__':
    main()
