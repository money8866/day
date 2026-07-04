# -*- coding: utf-8 -*-
"""
二波强势横盘形态（detect_sideways_pattern）历史回测
- 逐日切片，无未来函数（detect_sideways_pattern内置target_date参数）
- 统计不同评分区间、不同板块、不同回调幅度的5/10/20日胜率
- 止损止盈模拟（基于ATR止损+30%目标）
- 样本：bull合格股池
"""
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 路径
SOLO_DIR = r'D:\mystock\solo'
MFP_DIR = os.path.join(SOLO_DIR, 'multi_factor_picker')
sys.path.insert(0, SOLO_DIR)
sys.path.insert(0, MFP_DIR)

import tushare as ts
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
os.environ['TS_TOKEN'] = os.environ['TUSHARE_TOKEN']
pro = ts.pro_api()

import stock_cache as sc
from wave2_pattern_scanner import WavePatternDetector

# =========================
# 配置（支持命令行参数 -n 股票数 -d 回测天数）
# =========================
import argparse
_parser = argparse.ArgumentParser()
_parser.add_argument('-n', '--stocks', type=int, default=100, help='抽样股票数')
_parser.add_argument('-d', '--days', type=int, default=60, help='回测交易日数')
_args, _ = _parser.parse_known_args()

BACKTEST_DAYS = _args.days
SAMPLE_STOCKS = _args.stocks
SCORE_BINS = [(0, 20, '<20'), (20, 25, '20-24'), (25, 30, '25-29'),
              (30, 35, '30-34'), (35, 40, '35-39'), (40, 200, '>=40')]
PULLBACK_BINS = [(0, 0.05, '<5%'), (0.05, 0.10, '5-10%'), (0.10, 0.15, '10-15%'), (0.15, 0.26, '15-25%')]
WAVE1_BINS = [(0.20, 0.30, '20-30%'), (0.30, 0.50, '30-50%'), (0.50, 0.80, '50-80%')]
OUTPUT_DIR = os.path.join(SOLO_DIR, 'trend_feature_output')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def normalize_code(code):
    """补全6位代码并添加后缀"""
    code = str(code).strip()
    if '.' in code:
        return code
    code = code.zfill(6)
    if code.startswith(('60', '68', '11', '13')):
        return f'{code}.SH'
    elif code.startswith(('0', '3', '12')):
        return f'{code}.SZ'
    return None


def load_sample_stocks():
    """从bull合格股池加载抽样股票"""
    pool_path = os.path.join(SOLO_DIR, 'report_daily', 'bull_stocks_qualified.csv')
    if os.path.exists(pool_path):
        df = pd.read_csv(pool_path)
        col = 'code' if 'code' in df.columns else ('ts_code' if 'ts_code' in df.columns else df.columns[0])
        raw_codes = df[col].tolist()
        codes = [normalize_code(c) for c in raw_codes]
        codes = [c for c in codes if c is not None]
        print(f"[回测] 从bull合格股池加载 {len(codes)} 只股票")
        return codes[:SAMPLE_STOCKS]
    print("[回测] 未找到合格股池文件")
    return []


def get_trade_dates(end_date, n_days):
    """获取最近n个交易日"""
    start = (pd.Timestamp(end_date) - pd.Timedelta(days=n_days * 2)).strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='SSE', start_date=start, end_date=end_date)
    cal = cal[cal['is_open'] == 1].sort_values('cal_date')
    dates = cal['cal_date'].tolist()
    return dates[-n_days:]


def get_board(ts_code):
    """返回板块类型: '主板' / '双创'"""
    if ts_code.startswith(('688', '300', '301')):
        return '双创'
    if ts_code.startswith(('600', '601', '603', '605', '000', '002')):
        return '主板'
    return '其他'


def run_backtest():
    """主回测函数"""
    # 获取当前交易日
    end_date = sc.get_effective_date()
    trade_dates = get_trade_dates(end_date, BACKTEST_DAYS)
    print(f"[回测] 交易日: {trade_dates[0]} ~ {trade_dates[-1]}, 共{len(trade_dates)}天")

    stocks = load_sample_stocks()
    print(f"[回测] 抽样股票: {len(stocks)} 只")
    if not stocks:
        return

    # 共享detector实例（每只股票各自load_data，不会串扰）
    detector = WavePatternDetector()
    all_results = []
    processed = 0

    for ts_code in stocks:
        processed += 1
        if processed % 10 == 0:
            print(f"[回测] 进度: {processed}/{len(stocks)}, 已收集 {len(all_results)} 个信号")

        try:
            # 预取完整数据一次，避免每个交易日重复加载
            # detect_sideways_pattern 内部会重新load_data，但缓存命中应该很快
            for td in trade_dates:
                # 调用强势横盘检测，用target_date切片
                # 注意：每次调用都会重新load_data，建议force_date保证缓存稳定
                result = detector.detect_sideways_pattern(ts_code, today_only=False, target_date=td)
                if result is None:
                    continue

                # 只记录评分≥20的信号（SCORE_SIDWAYS_MIN阈值）
                if result.get('score', 0) < 20:
                    continue

                entry_price = result.get('entry_price', 0)
                stop_loss = result.get('stop_loss', 0)
                target = result.get('target', 0)
                entry_date = result.get('entry_date', td)

                # 获取入场日之后的真实价格序列（用于胜率计算）
                # 用 cached_daily 获取数据
                from wave2_pattern_scanner import cached_daily
                future_end = (pd.Timestamp(td) + pd.Timedelta(days=45)).strftime('%Y%m%d')
                df_fut = cached_daily(ts_code, td, future_end)
                if df_fut is None or df_fut.empty:
                    continue
                df_fut['trade_date'] = df_fut['trade_date'].astype(str)
                df_fut = df_fut.sort_values('trade_date').reset_index(drop=True)
                # 入场日是 entry_date（可能是td前某天），过滤出之后的数据
                mask = df_fut['trade_date'] > str(entry_date)
                df_after = df_fut[mask].reset_index(drop=True)
                if df_after.empty:
                    continue

                closes_after = df_after['close'].values

                # 计算5/10/20日收益
                gains = {}
                for period in [5, 10, 20]:
                    if len(closes_after) >= period:
                        close_fut = float(closes_after[period - 1])
                        gains[f'gain_{period}d'] = (close_fut - entry_price) / entry_price * 100
                        gains[f'win_{period}d'] = 1 if close_fut > entry_price else 0
                    else:
                        gains[f'gain_{period}d'] = np.nan
                        gains[f'win_{period}d'] = np.nan

                # 最大涨幅（20日内）
                if len(closes_after) >= 20:
                    gains['max_gain_20d'] = (closes_after[:20].max() - entry_price) / entry_price * 100
                    gains['max_drop_20d'] = (closes_after[:20].min() - entry_price) / entry_price * 100
                else:
                    gains['max_gain_20d'] = np.nan
                    gains['max_drop_20d'] = np.nan

                # 止损止盈模拟：扫描20日内是否触及止损或目标
                stop_hit = False
                target_hit = False
                stop_hit_days = np.nan
                target_hit_days = np.nan
                for i, day_close in enumerate(closes_after[:20]):
                    # 用日线最低价判断止损
                    day_low = float(df_after.iloc[i]['low']) if i < len(df_after) else day_close
                    day_high = float(df_after.iloc[i]['high']) if i < len(df_after) else day_close
                    if not stop_hit and day_low <= stop_loss:
                        stop_hit = True
                        stop_hit_days = i + 1
                    if not target_hit and day_high >= target:
                        target_hit = True
                        target_hit_days = i + 1
                    if stop_hit and target_hit:
                        break
                gains['stop_hit'] = int(stop_hit)
                gains['target_hit'] = int(target_hit)
                gains['stop_days'] = stop_hit_days
                gains['target_days'] = target_hit_days
                # 止盈胜率（先触止盈算胜，先触止损算败，都没触算平）
                if stop_hit and target_hit:
                    gains['sl_tp_result'] = 1 if target_hit_days < stop_hit_days else -1
                elif target_hit:
                    gains['sl_tp_result'] = 1
                elif stop_hit:
                    gains['sl_tp_result'] = -1
                else:
                    gains['sl_tp_result'] = 0

                all_results.append({
                    'ts_code': ts_code,
                    'board': get_board(ts_code),
                    'trade_date': td,
                    'entry_date': entry_date,
                    'score': result.get('score', 0),
                    'wave1_gain': result.get('wave1_gain', 0),
                    'pullback_pct': result.get('pullback_pct', 0),
                    'adjust_days': result.get('adjust_days', 0),
                    'rsi': result.get('rsi', 0),
                    'vol_ratio': result.get('vol_ratio', 0),
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'target': target,
                    'rr': result.get('rr', 0),
                    'wave2_confirmed': int(result.get('wave2_confirmed', False)),
                    'dmi_confirmed': int(result.get('dmi_confirmed', False)),
                    **gains,
                })

        except Exception as e:
            print(f"[回测] {ts_code} 失败: {e}")
            continue

    if not all_results:
        print("[回测] 无信号结果！")
        return

    df_results = pd.DataFrame(all_results)

    # 去重：同一股票同一entry_date只保留一次（避免不同td切片产生相同信号）
    before_dedup = len(df_results)
    df_results = df_results.drop_duplicates(subset=['ts_code', 'entry_date'], keep='first').reset_index(drop=True)
    if len(df_results) < before_dedup:
        print(f"[回测] 信号去重: {before_dedup} -> {len(df_results)}")

    # 保存CSV
    csv_path = os.path.join(OUTPUT_DIR, f'wave2_sideways_backtest_{end_date}.csv')
    df_results.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n[回测] 结果已保存: {csv_path}")
    print(f"[回测] 总信号数: {len(df_results)}")

    # =========================
    # 统计输出
    # =========================
    print(f"\n{'='*90}")
    print(f"  detect_sideways_pattern 强势横盘形态回测结果")
    print(f"  回测区间: {trade_dates[0]} ~ {trade_dates[-1]} | 股票数: {len(stocks)} | 信号数: {len(df_results)}")
    print(f"{'='*90}")

    def _stats(subset, label):
        """打印一行统计"""
        if len(subset) == 0:
            print(f"{label:<18} {0:>6}")
            return
        n = len(subset)
        w5 = subset['win_5d'].mean() * 100 if subset['win_5d'].notna().sum() > 0 else 0
        g5 = subset['gain_5d'].mean()
        w10 = subset['win_10d'].mean() * 100 if subset['win_10d'].notna().sum() > 0 else 0
        g10 = subset['gain_10d'].mean()
        w20 = subset['win_20d'].mean() * 100 if subset['win_20d'].notna().sum() > 0 else 0
        g20 = subset['gain_20d'].mean()
        mg = subset['max_gain_20d'].mean()
        sl = subset['stop_hit'].mean() * 100
        tp = subset['target_hit'].mean() * 100
        print(f"{label:<18} {n:>6} {w5:>7.1f}% {g5:>7.2f}% {w10:>7.1f}% {g10:>7.2f}% {w20:>7.1f}% {g20:>7.2f}% {mg:>7.2f}% {sl:>6.1f}% {tp:>6.1f}%")

    # 1. 按评分区间统计
    print(f"\n--- 按评分区间统计 ---")
    print(f"{'区间':<18} {'信号数':>6} {'5日胜率':>8} {'5日均涨':>8} {'10日胜率':>8} {'10日均涨':>8} {'20日胜率':>8} {'20日均涨':>8} {'最大涨幅':>8} {'止损率':>7} {'止盈率':>7}")
    print("-" * 110)
    for lo, hi, label in SCORE_BINS:
        subset = df_results[(df_results['score'] >= lo) & (df_results['score'] < hi)]
        _stats(subset, label)

    # 2. 按板块对比（强势横盘据称是主板最优）
    print(f"\n--- 按板块对比 ---")
    print(f"{'板块':<18} {'信号数':>6} {'5日胜率':>8} {'5日均涨':>8} {'10日胜率':>8} {'10日均涨':>8} {'20日胜率':>8} {'20日均涨':>8} {'最大涨幅':>8} {'止损率':>7} {'止盈率':>7}")
    print("-" * 110)
    for board in ['主板', '双创', '其他']:
        subset = df_results[df_results['board'] == board]
        _stats(subset, board)

    # 3. 按回调幅度统计
    print(f"\n--- 按回调幅度统计 ---")
    print(f"{'回调区间':<18} {'信号数':>6} {'5日胜率':>8} {'10日胜率':>8} {'20日胜率':>8} {'20日均涨':>8} {'最大涨幅':>8} {'止损率':>7} {'止盈率':>7}")
    print("-" * 100)
    for lo, hi, label in PULLBACK_BINS:
        # pullback_pct 字段是百分比数值（如 5.2 表示5.2%）
        lo_p = lo * 100
        hi_p = hi * 100
        subset = df_results[(df_results['pullback_pct'] >= lo_p) & (df_results['pullback_pct'] < hi_p)]
        if len(subset) == 0:
            print(f"{label:<18} {0:>6}")
            continue
        n = len(subset)
        w5 = subset['win_5d'].mean() * 100 if subset['win_5d'].notna().sum() > 0 else 0
        w10 = subset['win_10d'].mean() * 100 if subset['win_10d'].notna().sum() > 0 else 0
        w20 = subset['win_20d'].mean() * 100 if subset['win_20d'].notna().sum() > 0 else 0
        g20 = subset['gain_20d'].mean()
        mg = subset['max_gain_20d'].mean()
        sl = subset['stop_hit'].mean() * 100
        tp = subset['target_hit'].mean() * 100
        print(f"{label:<18} {n:>6} {w5:>7.1f}% {w10:>7.1f}% {w20:>7.1f}% {g20:>7.2f}% {mg:>7.2f}% {sl:>6.1f}% {tp:>6.1f}%")

    # 4. 按一波涨幅统计
    print(f"\n--- 按一波涨幅统计 ---")
    print(f"{'一波涨幅':<18} {'信号数':>6} {'5日胜率':>8} {'10日胜率':>8} {'20日胜率':>8} {'20日均涨':>8} {'最大涨幅':>8}")
    print("-" * 80)
    for lo, hi, label in WAVE1_BINS:
        lo_p = lo * 100
        hi_p = hi * 100
        subset = df_results[(df_results['wave1_gain'] >= lo_p) & (df_results['wave1_gain'] < hi_p)]
        if len(subset) == 0:
            print(f"{label:<18} {0:>6}")
            continue
        n = len(subset)
        w5 = subset['win_5d'].mean() * 100 if subset['win_5d'].notna().sum() > 0 else 0
        w10 = subset['win_10d'].mean() * 100 if subset['win_10d'].notna().sum() > 0 else 0
        w20 = subset['win_20d'].mean() * 100 if subset['win_20d'].notna().sum() > 0 else 0
        g20 = subset['gain_20d'].mean()
        mg = subset['max_gain_20d'].mean()
        print(f"{label:<18} {n:>6} {w5:>7.1f}% {w10:>7.1f}% {w20:>7.1f}% {g20:>7.2f}% {mg:>7.2f}%")

    # 5. 二波确认信号对比
    print(f"\n--- 二波确认 vs 未确认对比 ---")
    print(f"{'类型':<18} {'信号数':>6} {'5日胜率':>8} {'10日胜率':>8} {'20日胜率':>8} {'20日均涨':>8}")
    print("-" * 70)
    for label, mask in [('wave2确认', df_results['wave2_confirmed']==1),
                        ('DMI确认', df_results['dmi_confirmed']==1),
                        ('无确认', (df_results['wave2_confirmed']==0) & (df_results['dmi_confirmed']==0))]:
        subset = df_results[mask]
        if len(subset) == 0:
            print(f"{label:<18} {0:>6}")
            continue
        n = len(subset)
        w5 = subset['win_5d'].mean() * 100 if subset['win_5d'].notna().sum() > 0 else 0
        w10 = subset['win_10d'].mean() * 100 if subset['win_10d'].notna().sum() > 0 else 0
        w20 = subset['win_20d'].mean() * 100 if subset['win_20d'].notna().sum() > 0 else 0
        g20 = subset['gain_20d'].mean()
        print(f"{label:<18} {n:>6} {w5:>7.1f}% {w10:>7.1f}% {w20:>7.1f}% {g20:>7.2f}%")

    # 6. 整体表现汇总
    print(f"\n--- 整体表现 ---")
    if len(df_results) > 0:
        all_w5 = df_results['win_5d'].mean() * 100
        all_w10 = df_results['win_10d'].mean() * 100
        all_w20 = df_results['win_20d'].mean() * 100
        all_g10 = df_results['gain_10d'].mean()
        all_g20 = df_results['gain_20d'].mean()
        all_mg = df_results['max_gain_20d'].mean()
        all_sl = df_results['stop_hit'].mean() * 100
        all_tp = df_results['target_hit'].mean() * 100
        sl_tp_win = (df_results['sl_tp_result'] == 1).mean() * 100
        sl_tp_loss = (df_results['sl_tp_result'] == -1).mean() * 100
        print(f"  总信号: {len(df_results)}")
        print(f"  5日胜率: {all_w5:.1f}% | 10日胜率: {all_w10:.1f}% | 20日胜率: {all_w20:.1f}%")
        print(f"  10日均涨: {all_g10:.2f}% | 20日均涨: {all_g20:.2f}% | 20日最大涨幅: {all_mg:.2f}%")
        print(f"  止损触发率: {all_sl:.1f}% | 止盈触发率: {all_tp:.1f}%")
        print(f"  止盈胜率(先止盈): {sl_tp_win:.1f}% | 止损败率(先止损): {sl_tp_loss:.1f}%")

    print(f"\n{'='*90}")


if __name__ == '__main__':
    run_backtest()
