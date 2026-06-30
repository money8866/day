"""
精准入场时机模块 — 趋势系统v2的进场过滤增强
=============================================
基于回测数据失败模式分析，提炼3个核心改进：

1. 首次信号过滤：同一股票60天内只取第一个入场信号
2. 首次放量确认：量比 > 前20日最高量比×0.8（首次放量特征）
3. 距MA20/M60合理区间：不追高不弱反

使用方式：
  python trend_entry_precision.py --pool qualified --recent 80
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
        sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, volume_ratio,
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


def _check_entry_precision(df: pd.DataFrame, idx: int) -> dict | None:
    """
    精准入场条件 — 在信号日检查是否适合入场
    返回进场信息或None
    """
    if idx < 61:
        return None

    row = df.iloc[idx]
    close = row['close']
    ma20 = row['ma_bfq_20']
    ma60 = row['ma_bfq_60']

    if ma20 <= 0 or ma60 <= 0:
        return None

    # ──────────────────────────────────────
    # 条件A: 基础趋势条件（来自规则1）
    # ──────────────────────────────────────
    # A1: MA20走平或上拐
    ma20_5_ago = df.iloc[idx - 5]['ma_bfq_20']
    if ma20_5_ago <= 0:
        return None
    ma20_trend = (ma20 / ma20_5_ago - 1) * 100
    if ma20_trend < -2.0:
        return None

    # A2: 突破60日线 or 前60日高点
    seg_high = df.iloc[max(0, idx - 60):idx]['high'].max()
    break_ma60 = close > ma60 * 1.01
    break_60d_high = close > seg_high * 1.01
    if not (break_ma60 or break_60d_high):
        return None

    # A3: MACD条件
    dif = row.get('macd_dif_bfq', 0)
    dea = row.get('macd_dea_bfq', 0)
    macd_near_zero = abs(dif) < 1.0
    golden_cross = dif > dea
    if not (macd_near_zero or (golden_cross and dif > -0.5)):
        return None

    # A4: 涨幅/阳线条件
    pct_chg = row.get('pct_chg', 0)
    close_above_open = close > row['open']
    if not (pct_chg >= 2.0 or (pct_chg >= 0.5 and close_above_open and break_60d_high)):
        return None

    # ──────────────────────────────────────
    # 条件B: 进场精确定位 — 核心改进
    # ──────────────────────────────────────

    # B1: 距MA20在合理范围（5%~20%）
    above_ma20 = (close / ma20 - 1) * 100
    if above_ma20 < 5.0:
        return None
    if above_ma20 > 20.0:
        return None

    # B2: 首次放量确认 — 当日成交量 > 前20日最高量×0.7
    vol_20d = df.iloc[max(0, idx - 21):idx]['vol']
    if len(vol_20d) < 10:
        return None
    max_vol_20d = vol_20d.max()
    current_vol = row['vol']
    if max_vol_20d <= 0:
        return None
    vol_ratio_vs_max = current_vol / max_vol_20d
    if vol_ratio_vs_max < 0.7:
        return None

    # B3: 距MA60不能过远（排除大幅拉升后的追高）
    above_ma60 = (close / ma60 - 1) * 100
    if above_ma60 > 30.0:
        return None

    # 计算各项指标
    avg_vol_20 = vol_20d.mean()

    # 数据驱动评分体系
    entry_score = 0

    # 1. 距MA20位置（+30分）— 最重要因子
    #    12~16% = 30分, 16~20% = 25分, 8~12% = 20分, 5~8% = 10分
    if 12 <= above_ma20 <= 16:
        entry_score += 30
    elif 16 < above_ma20 <= 20:
        entry_score += 25
    elif 8 <= above_ma20 < 12:
        entry_score += 20
    elif 5 <= above_ma20 < 8:
        entry_score += 10
    else:
        entry_score += 0

    # 2. 量比（+25分）— 1.2~1.5最佳，过高(>3)或过低(<1)扣分
    if 1.2 <= vol_ratio_vs_max <= 1.5:
        entry_score += 25
    elif 1.5 < vol_ratio_vs_max <= 2.0:
        entry_score += 18
    elif 2.0 < vol_ratio_vs_max <= 3.0:
        entry_score += 8
    elif 1.0 <= vol_ratio_vs_max < 1.2:
        entry_score += 15
    elif vol_ratio_vs_max > 3.0:
        entry_score -= 10
    else:
        entry_score -= 5

    # 3. 距MA60位置（+20分）— >20%表示趋势已确立
    if above_ma60 >= 20:
        entry_score += 20
    elif 15 <= above_ma60 < 20:
        entry_score += 15
    elif 10 <= above_ma60 < 15:
        entry_score += 10
    elif 5 <= above_ma60 < 10:
        entry_score += 5
    else:
        entry_score += 0

    # 4. MACD DIF（+15分）— >2为最强信号
    if dif >= 2:
        entry_score += 15
    elif 1 <= dif < 2:
        entry_score += 10
    elif 0 <= dif < 1:
        entry_score += 5
    else:
        entry_score += 0

    # 5. 涨幅（+10分）— 7~10%最佳区间
    if 7 <= pct_chg <= 10:
        entry_score += 10
    elif 10 < pct_chg:
        entry_score += 5
    elif 5 <= pct_chg < 7:
        entry_score += 5
    else:
        entry_score += 0

    # ──────────────────────────────────────
    # 条件C: 波段位置惩罚 — 连涨天数越多扣分越重
    # ──────────────────────────────────────
    consecutive_up = 1  # 包含信号日本身
    for i in range(idx - 1, max(0, idx - 10), -1):
        if df.iloc[i].get('pct_chg', 0) > 0:
            consecutive_up += 1
        else:
            break

    if consecutive_up == 1:
        entry_score += 5     # 当天第1天涨 → 加分（最佳位置）
    elif consecutive_up == 2:
        entry_score += 0     # 连涨第2天 → 中性
    elif consecutive_up == 3:
        entry_score -= 10    # 连涨第3天 → 警告
    else:
        entry_score -= 20    # 连涨第4天及以上 → 严重警告

    return {
        'signal_date': str(row['trade_date']),
        'signal_close': round(close, 2),
        'signal_type': '趋势启动',
        'entry_score': entry_score,
        'consecutive_up': consecutive_up,
        'pct_chg': round(pct_chg, 2),
        'vol_ratio': round(row.get('volume_ratio', 0), 2),
        'vol_surge_ratio': round(vol_ratio_vs_max, 2),
        'above_ma20_pct': round(above_ma20, 2),
        'above_ma60_pct': round(above_ma60, 2),
        'ma20_trend': round(ma20_trend, 2),
        'macd_dif': round(dif, 2),
        'macd_golden': 1 if dif > dea else 0,
        'rsi6': round(row.get('rsi_bfq_6', 0), 1),
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
    parser = argparse.ArgumentParser(description='精准入场时机')
    parser.add_argument('codes', nargs='*')
    parser.add_argument('--pool', choices=['default', 'qualified'], default='qualified')
    parser.add_argument('--recent', type=int, default=80,
                        help='分析最近N天数据 (盘后用 --today 更快)')
    parser.add_argument('--today', action='store_true',
                        help='盘后模式：只检测最新交易日信号')
    parser.add_argument('--min-score', type=int, default=50,
                        help='最低入场评分 (默认50)')
    # 新增过滤参数
    parser.add_argument('--filter-return1d', action='store_true',
                        help='过滤：信号日收涨（return_1d > 0）')
    parser.add_argument('--filter-rsi', type=str, default=None,
                        help='过滤：RSI6范围，如 "50,70"')
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

    mode = "盘后(Today)" if args.today else f"最近{args.recent}天"
    log(f"精准入场检测 v5 — {mode}")
    log(f"股票池: {args.pool} ({len(stock_codes)}只)")
    log(f"最低评分: {args.min_score}")
    if args.filter_return1d:
        log(f"过滤: return_1d > 0（信号日收涨）")
    if args.filter_rsi:
        log(f"过滤: RSI6 ∈ [{args.filter_rsi}]")
    log(f"评分权重: 距MA20位置(30) + 量比(25) + 距MA60(20) + MACD_DIF(15) + 涨幅(10) + 波段位置(±20)")
    log("")

    all_signals = []
    total = len(stock_codes)

    for i, ts_code in enumerate(stock_codes):
        if (i + 1) % 100 == 0:
            log(f"[{i+1}/{total}] 扫描中... 已发现 {len(all_signals)} 个信号")

        df = get_data(ts_code)
        if df is None or len(df) < 90:
            continue

        # --today 模式：读全部数据，只检测最后一行
        # 普通模式：用 --recent 截断加速
        if args.today:
            idx_range = [len(df) - 1]
        else:
            if args.recent and args.recent < len(df):
                df = df.iloc[-args.recent:].reset_index(drop=True)
            idx_range = range(20, len(df))

        for idx in idx_range:
            sig = _check_entry_precision(df, idx)
            if sig is None:
                continue

            if sig['entry_score'] < args.min_score:
                continue

            # 计算收益
            entry_price = df.iloc[idx]['close']
            rets = {}
            for w in [1, 5, 10, 20]:
                fi = min(idx + w, len(df) - 1)
                rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2)

            all_signals.append({
                'ts_code': ts_code,
                'signal_date': sig['signal_date'],
                'signal_type': sig['signal_type'],
                'entry_score': sig['entry_score'],
                'consecutive_up': sig['consecutive_up'],
                'pct_chg': sig['pct_chg'],
                'vol_ratio': sig['vol_ratio'],
                'vol_surge': sig['vol_surge_ratio'],
                'above_ma20_pct': sig['above_ma20_pct'],
                'above_ma60_pct': sig['above_ma60_pct'],
                'macd_dif': sig['macd_dif'],
                'macd_golden': sig['macd_golden'],
                'rsi6': sig['rsi6'],
                'return_1d': rets[1],
                'return_5d': rets[5],
                'return_10d': rets[10],
                'return_20d': rets[20],
            })

    log(f"\n扫描完成！共 {len(all_signals)} 个信号")

    if not all_signals:
        log("  无任何信号")
        return

    # === 新增：过滤条件（基于回测最优策略）===
    df_out = pd.DataFrame(all_signals)
    
    # 过滤1: return_1d > 0（信号日收涨）
    if args.filter_return1d:
        orig_cnt = len(df_out)
        df_out = df_out[df_out['return_1d'] > 0].copy()
        log(f"  过滤 return_1d>0: {orig_cnt} → {len(df_out)} 条")
    
    # 过滤2: RSI6 ∈ [50, 70]（未超买）
    if args.filter_rsi:
        lo, hi = map(float, args.filter_rsi.split(','))
        orig_cnt = len(df_out)
        df_out = df_out[(df_out['rsi6'] >= lo) & (df_out['rsi6'] <= hi)].copy()
        log(f"  过滤 RSI6∈[{lo},{hi}]: {orig_cnt} → {len(df_out)} 条")
    
    log(f"  最终信号数: {len(df_out)}")
    print()

    if len(df_out) == 0:
        log("  过滤后无信号")
        return

    # 保存CSV
    cols = ['signal_date', 'ts_code', 'signal_type', 'entry_score', 'consecutive_up',
            'pct_chg', 'vol_ratio', 'vol_surge', 'above_ma20_pct', 'above_ma60_pct',
            'macd_dif', 'macd_golden', 'rsi6',
            'return_1d', 'return_5d', 'return_10d', 'return_20d']
    df_out = df_out[[c for c in cols if c in df_out.columns]]
    df_out = df_out.sort_values(['signal_date', 'ts_code']).reset_index(drop=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(OUTPUT_DIR, f"entry_precision_{timestamp}_{args.pool}.csv")
    df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    log(f"  CSV: {csv_path}")

    # 统计
    sub = df_out.copy()
    sub['signal_date'] = sub['signal_date'].astype(str)
    sub = sub[(sub['signal_date'] >= '20260501') & (sub['signal_date'] <= '20260625')]
    log(f"  20260501~20260625: {len(sub)} 信号")
    print()

    if len(sub) > 0:
        print(f"  {'='*60}")
        print(f"  整体表现:")
        for w in [1, 5, 10, 20]:
            r = sub[f'return_{w}d'].dropna()
            wins = r[r > 0]
            print(f"    +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>5.1f}%  亏>10%={(r<-10).sum()}  亏>15%={(r<-15).sum()}  最大={r.max():>6.2f}%  最小={r.min():>6.2f}%")

        # 按入场评分分组
        print(f"\n  按入场评分分组:")
        for bucket, label in [(80, '≥80'), (70, '70~79'), (60, '60~69'), (50, '50~59')]:
            if bucket >= 80:
                s3 = sub[sub['entry_score'] >= 80]
            else:
                s3 = sub[(sub['entry_score'] >= bucket) & (sub['entry_score'] < bucket + 10)]
            if len(s3) == 0:
                continue
            r = s3['return_10d'].dropna()
            wins = r[r > 0]
            print(f"    {label}分: {len(s3)}个  均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>5.1f}%  亏>15%={(r<-15).sum()}")

        # 按连涨天数分组
        print(f"\n  按波段位置分组:")
        for cu, label in [(1, '第一天'), (2, '第二天'), (3, '第三天'), (4, '第四天+')]:
            if cu >= 4:
                s3 = sub[sub['consecutive_up'] >= 4]
            else:
                s3 = sub[sub['consecutive_up'] == cu]
            if len(s3) == 0:
                continue
            r = s3['return_10d'].dropna()
            wins = r[r > 0]
            print(f"    {label}: {len(s3)}个  均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>5.1f}%  亏>15%={(r<-15).sum()}")

        # 前5/后5
        if len(sub) >= 5:
            print(f"\n  TOP5:")
            for _, r in sub.nlargest(5, 'return_10d').iterrows():
                print(f"    {r['signal_date']} {r['ts_code']:<12} 评分={r['entry_score']:>3.0f} +10d={r['return_10d']:>6.2f}% +20d={r['return_20d']:>6.2f}%")
            print(f"  BOTTOM5:")
            for _, r in sub.nsmallest(5, 'return_10d').iterrows():
                print(f"    {r['signal_date']} {r['ts_code']:<12} 评分={r['entry_score']:>3.0f} +10d={r['return_10d']:>6.2f}% +20d={r['return_20d']:>6.2f}%")


if __name__ == '__main__':
    main()
