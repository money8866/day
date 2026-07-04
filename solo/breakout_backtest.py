"""
detect_breakout 突破算法历史回测
- 逐日切片，无未来函数
- 统计不同评分区间的5/10/20日胜率
- 对比有效突破(>=75)、临近突破(60-74)、假突破的胜率
"""
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 确保能导入tushare_quant
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tushare_quant as tq

# =========================
# 配置
# =========================
BACKTEST_DAYS = 60        # 回测最近60个交易日
SAMPLE_STOCKS = 100       # 抽样股票数量（从合格股池取前N只，避免太慢）
SCORE_BINS = [(0, 40, '<40'), (40, 50, '40-49'), (50, 60, '50-59'),
              (60, 70, '60-69'), (70, 75, '70-74'), (75, 200, '>=75有效突破')]
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'

def normalize_code(code):
    """补全6位代码并添加后缀"""
    code = str(code).strip()
    # 如果已经是xxx.SZ/xxx.SH格式，直接返回
    if '.' in code:
        return code
    # 补零到6位
    code = code.zfill(6)
    # 添加后缀
    if code.startswith(('60', '68', '11', '13')):
        return f'{code}.SH'
    elif code.startswith(('0', '3', '12')):
        return f'{code}.SZ'
    return None  # 跳过北交所等


def load_sample_stocks():
    """加载抽样股票池"""
    # 优先从合格股池加载
    pool_path = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
    if os.path.exists(pool_path):
        df = pd.read_csv(pool_path)
        col = 'code' if 'code' in df.columns else ('ts_code' if 'ts_code' in df.columns else df.columns[0])
        raw_codes = df[col].tolist()
        codes = [normalize_code(c) for c in raw_codes]
        codes = [c for c in codes if c is not None]
        print(f"[回测] 从合格股池加载 {len(codes)} 只股票")
        return codes[:SAMPLE_STOCKS]

    # 降级：从multi_factor_picker输出加载
    mfp_dir = r'D:\mystock\solo\multi_factor_picker\output'
    if os.path.exists(mfp_dir):
        files = [f for f in os.listdir(mfp_dir) if f.startswith('bull_stocks_') and f.endswith('.csv')]
        if files:
            files.sort(reverse=True)
            df = pd.read_csv(os.path.join(mfp_dir, files[0]))
            col = 'code' if 'code' in df.columns else ('ts_code' if 'ts_code' in df.columns else df.columns[0])
            raw_codes = df[col].tolist()
            codes = [normalize_code(c) for c in raw_codes]
            codes = [c for c in codes if c is not None]
            print(f"[回测] 从multi_factor_picker加载 {len(codes)} 只股票: {files[0]}")
            return codes[:SAMPLE_STOCKS]

    print("[回测] 未找到股池文件，使用默认股票列表")
    return ['603662.SH', '300446.SZ', '002600.SZ', '688387.SH', '300762.SZ']


def get_trade_dates(end_date, n_days):
    """获取最近n个交易日"""
    import tushare as ts
    pro = ts.pro_api()
    start = (pd.Timestamp(end_date) - pd.Timedelta(days=n_days * 2)).strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='SSE', start_date=start, end_date=end_date)
    cal = cal[cal['is_open'] == 1].sort_values('cal_date')
    dates = cal['cal_date'].tolist()
    return dates[-n_days:]


def run_backtest():
    """主回测函数"""
    end_date = str(tq.TRADE_DATE)
    trade_dates = get_trade_dates(end_date, BACKTEST_DAYS)
    print(f"[回测] 交易日: {trade_dates[0]} ~ {trade_dates[-1]}, 共{len(trade_dates)}天")

    stocks = load_sample_stocks()
    print(f"[回测] 抽样股票: {len(stocks)} 只")

    all_results = []
    processed = 0

    for ts_code in stocks:
        processed += 1
        if processed % 10 == 0:
            print(f"[回测] 进度: {processed}/{len(stocks)}, 已收集 {len(all_results)} 个信号")

        try:
            # 获取历史数据（400天前到当前交易日，不获取未来数据避免API空返回）
            start = (pd.Timestamp(trade_dates[0]) - pd.Timedelta(days=400)).strftime('%Y%m%d')
            df = tq.cached_stk_factor_pro(ts_code, start, end_date)
            if df is None or df.empty:
                continue

            df['trade_date'] = df['trade_date'].astype(str)
            df = df.sort_values('trade_date').reset_index(drop=True)

            # 对每个交易日切片检测突破
            for td in trade_dates:
                mask = df['trade_date'] == td
                if not mask.any():
                    continue
                idx = mask.idxmax()

                # 需要至少60天历史
                if idx < 60:
                    continue

                # 切片到当日
                df_slice = df.iloc[:idx + 1]

                # 调用detect_breakout（用当日数据）
                result = tq.detect_breakout(ts_code, None, trade_date=td)
                score = result.get('breakout_score', 0)
                signal = result.get('signal', '')
                is_false = result.get('is_false_breakout', False)
                is_valid = result.get('is_valid_breakout', False)
                is_imminent = result.get('is_imminent_breakout', False)

                # 只记录有意义的信号（评分>=50 或假突破）
                if score < 50 and not is_false:
                    continue

                # 计算未来收益
                close_now = float(df.iloc[idx]['close'])
                gains = {}
                for period in [5, 10, 20]:
                    if idx + period < len(df):
                        close_future = float(df.iloc[idx + period]['close'])
                        gains[f'gain_{period}d'] = (close_future - close_now) / close_now * 100
                        gains[f'win_{period}d'] = 1 if close_future > close_now else 0
                    else:
                        gains[f'gain_{period}d'] = np.nan
                        gains[f'win_{period}d'] = np.nan

                # 最大涨幅
                if idx + 20 < len(df):
                    max_close = float(df.iloc[idx+1:idx+21]['close'].max())
                    gains['max_gain_20d'] = (max_close - close_now) / close_now * 100
                else:
                    gains['max_gain_20d'] = np.nan

                all_results.append({
                    'ts_code': ts_code,
                    'trade_date': td,
                    'breakout_score': score,
                    'signal': signal,
                    'is_false': is_false,
                    'is_valid': is_valid,
                    'is_imminent': is_imminent,
                    'close': close_now,
                    **gains,
                })

        except Exception as e:
            print(f"[回测] {ts_code} 失败: {e}")
            continue

    if not all_results:
        print("[回测] 无信号结果！")
        return

    df_results = pd.DataFrame(all_results)

    # 保存CSV
    csv_path = os.path.join(OUTPUT_DIR, f'breakout_backtest_{end_date}.csv')
    df_results.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n[回测] 结果已保存: {csv_path}")
    print(f"[回测] 总信号数: {len(df_results)}")

    # =========================
    # 统计输出
    # =========================
    print(f"\n{'='*80}")
    print(f"  detect_breakout 突破算法回测结果")
    print(f"  回测区间: {trade_dates[0]} ~ {trade_dates[-1]} | 股票数: {len(stocks)} | 信号数: {len(df_results)}")
    print(f"{'='*80}")

    # 1. 按评分区间统计
    print(f"\n--- 按突破评分区间统计 ---")
    print(f"{'区间':<16} {'信号数':>6} {'5日胜率':>8} {'5日均涨':>8} {'10日胜率':>8} {'10日均涨':>8} {'20日胜率':>8} {'20日均涨':>8} {'最大涨幅':>8}")
    print("-" * 90)

    for lo, hi, label in SCORE_BINS:
        subset = df_results[(df_results['breakout_score'] >= lo) & (df_results['breakout_score'] < hi)]
        if len(subset) == 0:
            print(f"{label:<16} {0:>6}")
            continue
        n = len(subset)
        w5 = subset['win_5d'].mean() * 100 if subset['win_5d'].notna().sum() > 0 else 0
        g5 = subset['gain_5d'].mean()
        w10 = subset['win_10d'].mean() * 100 if subset['win_10d'].notna().sum() > 0 else 0
        g10 = subset['gain_10d'].mean()
        w20 = subset['win_20d'].mean() * 100 if subset['win_20d'].notna().sum() > 0 else 0
        g20 = subset['gain_20d'].mean()
        mg = subset['max_gain_20d'].mean()
        print(f"{label:<16} {n:>6} {w5:>7.1f}% {g5:>7.2f}% {w10:>7.1f}% {g10:>7.2f}% {w20:>7.1f}% {g20:>7.2f}% {mg:>7.2f}%")

    # 2. 按信号类型统计
    print(f"\n--- 按信号类型统计 ---")
    print(f"{'类型':<20} {'信号数':>6} {'5日胜率':>8} {'10日胜率':>8} {'20日胜率':>8} {'10日均涨':>8} {'20日均涨':>8}")
    print("-" * 80)

    categories = [
        ('有效突破', df_results[df_results['is_valid'] == True]),
        ('即将突破', df_results[df_results['is_imminent'] == True]),
        ('假突破', df_results[df_results['is_false'] == True]),
        ('非突破(>=60)', df_results[(df_results['is_false'] == False) & (df_results['is_valid'] == False) & (df_results['is_imminent'] == False) & (df_results['breakout_score'] >= 60)]),
        ('非突破(50-59)', df_results[(df_results['is_false'] == False) & (df_results['is_valid'] == False) & (df_results['is_imminent'] == False) & (df_results['breakout_score'] >= 50) & (df_results['breakout_score'] < 60)]),
    ]

    for label, subset in categories:
        if len(subset) == 0:
            print(f"{label:<20} {0:>6}")
            continue
        n = len(subset)
        w5 = subset['win_5d'].mean() * 100 if subset['win_5d'].notna().sum() > 0 else 0
        w10 = subset['win_10d'].mean() * 100 if subset['win_10d'].notna().sum() > 0 else 0
        w20 = subset['win_20d'].mean() * 100 if subset['win_20d'].notna().sum() > 0 else 0
        g10 = subset['gain_10d'].mean()
        g20 = subset['gain_20d'].mean()
        print(f"{label:<20} {n:>6} {w5:>7.1f}% {w10:>7.1f}% {w20:>7.1f}% {g10:>7.2f}% {g20:>7.2f}%")

    # 3. 有效突破 vs 假突破 对比
    print(f"\n--- 有效突破 vs 假突破 对比 ---")
    valid = df_results[df_results['is_valid'] == True]
    false = df_results[df_results['is_false'] == True]
    print(f"  有效突破(>=75): {len(valid)}个信号, 10日胜率={valid['win_10d'].mean()*100:.1f}%, 20日胜率={valid['win_20d'].mean()*100:.1f}%, 20日均涨={valid['gain_20d'].mean():.2f}%" if len(valid) > 0 else "  有效突破: 0个信号")
    print(f"  假突破:         {len(false)}个信号, 10日胜率={false['win_10d'].mean()*100:.1f}%, 20日胜率={false['win_20d'].mean()*100:.1f}%, 20日均涨={false['gain_20d'].mean():.2f}%" if len(false) > 0 else "  假突破: 0个信号")

    if len(valid) > 0 and len(false) > 0:
        diff_10d = valid['win_10d'].mean() - false['win_10d'].mean()
        diff_20d = valid['win_20d'].mean() - false['win_20d'].mean()
        print(f"  胜率差: 10日={diff_10d*100:.1f}%, 20日={diff_20d*100:.1f}%")

    print(f"\n{'='*80}")


if __name__ == '__main__':
    run_backtest()
