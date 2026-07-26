"""
空头陷阱择时策略 — 今年回测程序
=================================
验证 Bear Trap 策略在2026年历史数据上的表现。

方法:
  1. 使用 bull_stocks_all 股池作为候选池
  2. 滚动回测每个交易日，运行 Layer2+Layer3 检测
  3. 评估T+1~T+10的反弹表现
  4. 输出胜率、盈亏比、最大回撤等指标

用法:
    python bear_trap_backtest.py                         # 全量回测
    python bear_trap_backtest.py --quick                 # 快速抽样回测（每5天）
    python bear_trap_backtest.py --start 20260601        # 指定起始日期
"""

import os
import sys
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bear_trap_timing import (
    DB_PATH, CACHE_DIR, OUTPUT_DIR, _safe, _resolve_trade_date,
    PriceVolumeTrapDetector, BearTrapScorer,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('bear_trap_bt')

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_daily')
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ════════════════════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════════════════════

def get_trading_dates(start_date: str = '20260101', end_date: str = None) -> List[str]:
    """获取交易日期列表"""
    conn = sqlite3.connect(DB_PATH)
    if end_date is None:
        cursor = conn.execute("SELECT MAX(trade_date) FROM stk_factor_pro")
        end_date = cursor.fetchone()[0]
    sql = "SELECT DISTINCT trade_date FROM stk_factor_pro WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date"
    df = pd.read_sql(sql, conn, params=(start_date, end_date))
    conn.close()
    return df['trade_date'].tolist()


def load_stock_pool() -> List[Dict]:
    """加载候选股池（同 bear_trap_timing.py 逻辑）"""
    path = os.path.join(OUTPUT_DIR, 'bull_stocks_all.csv')
    if os.path.exists(path):
        df = pd.read_csv(path)
        codes = []
        for _, row in df.iterrows():
            code = str(row['code']).strip().zfill(6)
            if code.startswith(('8', '4', '9')):
                continue
            codes.append({
                'code': code,
                'name': str(row.get('name', '')),
                'theme': str(row.get('theme', '')),
                'industry': str(row.get('industry', '')),
            })
        logger.info(f"加载股池 {path}: {len(codes)}只")
        return codes
    logger.warning("股池文件不存在，使用全市场")
    return []


def load_all_daily_data(dates: List[str], pool_codes: List[str]) -> pd.DataFrame:
    """批量加载所有必要的日线数据"""
    if not pool_codes:
        return pd.DataFrame()

    ts_codes = []
    for item in pool_codes:
        c = item['code']
        if c.startswith('6'):
            ts_codes.append(f"{c}.SH")
        else:
            ts_codes.append(f"{c}.SZ")

    # 需要加载的信号列
    cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pct_chg',
            'vol', 'amount', 'volume_ratio', 'turnover_rate',
            'ma_bfq_5', 'ma_bfq_10', 'ma_bfq_20', 'ma_bfq_60', 'ma_bfq_90',
            'macd_dif_bfq', 'macd_dea_bfq', 'macd_bfq',
            'rsi_bfq_6', 'rsi_bfq_12', 'rsi_bfq_24',
            'kdj_k_bfq', 'kdj_d_bfq', 'kdj_bfq',
            'boll_mid_bfq', 'boll_upper_bfq', 'boll_lower_bfq',
            'atr_bfq', 'total_mv', 'circ_mv', 'pe_ttm', 'pb']

    col_str = ', '.join(cols)
    placeholders = ','.join(['?'] * len(ts_codes))

    conn = sqlite3.connect(DB_PATH)
    # 分批加载避免 SQL 过长
    chunk_size = 200
    chunks = []
    for i in range(0, len(ts_codes), chunk_size):
        chunk_codes = ts_codes[i:i + chunk_size]
        chunk_ph = ','.join(['?'] * len(chunk_codes))
        sql = f"SELECT {col_str} FROM stk_factor_pro WHERE ts_code IN ({chunk_ph}) AND trade_date >= ?"
        params = chunk_codes + [dates[0]]
        df_chunk = pd.read_sql(sql, conn, params=params)
        if not df_chunk.empty:
            chunks.append(df_chunk)
    conn.close()

    if not chunks:
        return pd.DataFrame()
    df = pd.concat(chunks, ignore_index=True)
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    return df


# ════════════════════════════════════════════════════════════
# 回测核心
# ════════════════════════════════════════════════════════════

def run_backtest(
    start_date: str = '20260101',
    end_date: str = None,
    max_signal_per_day: int = 10,
    quick_mode: bool = False,
):
    """执行空头陷阱策略回测"""

    # ── 1. 准备数据 ──
    all_dates = get_trading_dates(start_date, end_date)
    if len(all_dates) < 30:
        logger.error("交易日数据不足")
        return

    logger.info(f"回测期间: {all_dates[0]} ~ {all_dates[-1]}, 共{len(all_dates)}个交易日")

    pool = load_stock_pool()
    if not pool:
        return

    # 快速模式：每5天采样一次
    if quick_mode:
        all_dates = all_dates[::5]
        logger.info(f"快速模式: 采样{len(all_dates)}个交易日")

    # ── 2. 批量加载日线数据 ──
    lookback_start = (datetime.strptime(all_dates[0], '%Y%m%d') - timedelta(days=250)).strftime('%Y%m%d')
    logger.info(f"加载日线数据 ({lookback_start} ~ {all_dates[-1]})...")
    daily_df = load_all_daily_data([lookback_start] + all_dates, pool)

    if daily_df.empty:
        logger.error("无日线数据")
        return
    logger.info(f"日线数据: {len(daily_df)}条")

    # ── 3. 初始化检测器 ──
    detector = PriceVolumeTrapDetector()
    code_to_info = {f"{item['code']}.{'SH' if item['code'].startswith('6') else 'SZ'}": item for item in pool}

    # 结果收集
    all_signals = []       # 所有信号
    results_detail = []    # 详细结果（含后续表现）

    # 将日线数据按股票分组方便查询
    logger.info("预处理日线数据...")
    stock_groups = {}
    for ts_code, grp in daily_df.groupby('ts_code'):
        grp = grp.sort_values('trade_date').reset_index(drop=True)
        stock_groups[ts_code] = grp
    logger.info(f"共{len(stock_groups)}只股票有数据")

    # 日期索引映射 (trade_date -> row idx)
    date_to_idx = {}
    all_date_list = sorted(daily_df['trade_date'].unique())
    for i, d in enumerate(all_date_list):
        date_to_idx[d] = i

    # ── 4. 滚动回测 ──
    logger.info("开始滚动回测...")
    total_days = len(all_dates)

    for day_idx, trade_date in enumerate(all_dates):
        if (day_idx + 1) % 10 == 0:
            logger.info(f"  进度: {day_idx+1}/{total_days} ({trade_date})")

        day_signals = []

        for ts_code, grp in stock_groups.items():
            # 找到该股票在交易日当天的数据
            mask = grp['trade_date'] == trade_date
            if not mask.any():
                continue
            row_idx = grp[mask].index[0]
            row_pos = grp.index.get_loc(row_idx)

            # 需要至少120天的历史数据
            if row_pos < 119:
                continue

            # 提取120天历史
            hist = grp.iloc[row_pos - 119:row_pos + 1].copy()
            if len(hist) < 30:
                continue

            # Layer 2: 量价诱空识别
            pv_result = detector.detect_bear_trap(hist)

            if not pv_result.get('detected', False):
                continue

            # 简化 Layer 3: 使用量价得分作为综合分
            l2_score = pv_result.get('score', 0)
            n_core = 0
            if pv_result.get('support_fake_break', {}).get('detected'):
                n_core += 1
            if pv_result.get('volume_decay', {}).get('decay_detected'):
                n_core += 1
            if pv_result.get('divergence_score', 0) > 0.40:
                n_core += 1
            if pv_result.get('confirmation', {}).get('confirmed'):
                n_core += 1

            # 信号条件
            if l2_score >= 50 and n_core >= 2:
                info = code_to_info.get(ts_code, {})
                name = info.get('name', '')
                theme = info.get('theme', '')

                # 当日数据
                latest = hist.iloc[-1]
                close = _safe(latest['close'])

                day_signals.append({
                    'ts_code': ts_code,
                    'name': name,
                    'theme': theme,
                    'trade_date': trade_date,
                    'close': close,
                    'pct_chg': _safe(latest.get('pct_chg', 0)),
                    'pv_score': l2_score,
                    'divergence': pv_result.get('divergence_score', 0),
                    'n_core_signals': n_core,
                    'volume_ratio': _safe(latest.get('volume_ratio', 0)),
                    'row_idx_global': row_idx,
                })

        # 每日信号数量限制
        day_signals.sort(key=lambda x: x['pv_score'], reverse=True)
        day_signals = day_signals[:max_signal_per_day]

        all_signals.extend(day_signals)

    logger.info(f"回测完成: 共产生{len(all_signals)}个信号")

    if len(all_signals) == 0:
        print("\n无任何信号产生，策略过于严格或数据不足。")
        return

    # ── 5. 评估后续表现 ──
    logger.info("评估信号后续表现...")
    signal_df = pd.DataFrame(all_signals)

    # 为每个信号计算 T+1 ~ T+10 的收益率
    forward_results = []
    for _, sig in signal_df.iterrows():
        ts_code = sig['ts_code']
        sig_date = sig['trade_date']
        entry_price = sig['close']

        grp = stock_groups.get(ts_code)
        if grp is None:
            continue

        # 找到信号日之后的数据
        sig_pos = None
        for i in range(len(grp)):
            if grp.iloc[i]['trade_date'] == sig_date:
                sig_pos = i
                break
        if sig_pos is None:
            continue

        result = {
            'ts_code': ts_code,
            'name': sig['name'],
            'theme': sig['theme'],
            'trade_date': sig_date,
            'entry_price': entry_price,
            'pv_score': sig['pv_score'],
            'divergence': sig['divergence'],
            'n_signals': sig['n_core_signals'],
            'pct_chg_signal_day': sig['pct_chg'],
        }

        # 计算 T+1~T+10 收益率
        max_forward = 0.0
        min_forward = 0.0
        best_recovery = 0.0

        for fwd in range(1, 11):
            fwd_pos = sig_pos + fwd
            if fwd_pos >= len(grp):
                break
            fwd_close = _safe(grp.iloc[fwd_pos]['close'])
            ret = (fwd_close / entry_price - 1) * 100

            result[f'T+{fwd}_ret'] = round(ret, 2)
            if fwd == 1:
                result['T+1_ret'] = round(ret, 2)
            elif fwd == 5:
                result['T+5_ret'] = round(ret, 2)
            elif fwd == 10:
                result['T+10_ret'] = round(ret, 2)

            if ret > max_forward:
                max_forward = ret
            if ret < min_forward:
                min_forward = ret
            if ret > best_recovery:
                best_recovery = ret

        result['max_forward_ret'] = round(max_forward, 2)
        result['min_forward_ret'] = round(min_forward, 2)
        result['best_recovery'] = round(best_recovery, 2)

        # 判定：10日内最高反弹≥6% = 正样本（诱空确认）
        result['is_bear_trap'] = best_recovery >= 6.0
        # 判定：10日内继续下跌≥5% = 负样本（真下跌）
        result['is_false_signal'] = min_forward <= -5.0

        forward_results.append(result)

    if not forward_results:
        logger.warning("无有效评估结果")
        return

    result_df = pd.DataFrame(forward_results)

    # ── 6. 统计指标 ──
    _print_statistics(result_df)

    # ── 7. 保存结果 ──
    out_path = os.path.join(OUTPUT_DIR, 'bear_trap_backtest_results.csv')
    result_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"回测结果已保存: {out_path} ({len(result_df)}条)")

    # 按月份汇总
    result_df['month'] = result_df['trade_date'].str[:6]
    monthly = result_df.groupby('month').agg(
        信号数=('ts_code', 'count'),
        命中诱空=('is_bear_trap', 'sum'),
        误判=('is_false_signal', 'sum'),
        平均最大反弹=('best_recovery', 'mean'),
    ).round(2)
    monthly['命中率'] = (monthly['命中诱空'] / monthly['信号数'] * 100).round(1)
    print("\n  月度统计:")
    print(monthly.to_string())


def _print_statistics(df: pd.DataFrame):
    """打印回测统计"""
    total = len(df)
    n_hit = df['is_bear_trap'].sum()
    n_false = df['is_false_signal'].sum()
    hit_rate = n_hit / total * 100 if total > 0 else 0
    false_rate = n_false / total * 100 if total > 0 else 0
    uncertain = total - n_hit - n_false

    # 收益统计
    avg_max_recovery = df['best_recovery'].mean()
    avg_max_drawdown = df['min_forward_ret'].mean()
    avg_t1 = df['T+1_ret'].mean() if 'T+1_ret' in df.columns else 0
    avg_t5 = df['T+5_ret'].mean() if 'T+5_ret' in df.columns else 0
    avg_t10 = df['T+10_ret'].mean() if 'T+10_ret' in df.columns else 0

    # 信号等级分布
    level_counts = df['n_signals'].value_counts().sort_index()
    pv_bins = pd.cut(df['pv_score'], bins=[0, 55, 65, 80, 100], labels=['50-55', '55-65', '65-80', '80+'])

    print()
    print("━" * 90)
    print("  空头陷阱 (Bear Trap) 择时策略回测报告")
    print("━" * 90)
    print(f"  信号总数:          {total}")
    print(f"  命中诱空(反弹≥6%): {n_hit} ({hit_rate:.1f}%)")
    print(f"  误判(继续下跌≥5%): {n_false} ({false_rate:.1f}%)")
    print(f"  不确定(中性走势):  {uncertain} ({100-hit_rate-false_rate:.1f}%)")
    print(f"  盈亏比(胜/败):     {n_hit/max(n_false,1):.2f}")
    print("─" * 90)
    print(f"  平均T+1收益:       {avg_t1:+.2f}%")
    print(f"  平均T+5收益:       {avg_t5:+.2f}%")
    print(f"  平均T+10收益:      {avg_t10:+.2f}%")
    print(f"  平均最大反弹:      {avg_max_recovery:+.2f}%")
    print(f"  平均最大回撤:      {avg_max_drawdown:+.2f}%")
    print("─" * 90)

    # 按信号强度分层胜率
    print("  ── 按信号核心数分层 ──")
    for n in sorted(df['n_signals'].unique()):
        sub = df[df['n_signals'] == n]
        if len(sub) > 0:
            sub_hit = sub['is_bear_trap'].sum()
            print(f"    核心信号{n}个: {len(sub)}次, 命中{sub_hit}次({sub_hit/len(sub)*100:.0f}%)")

    # 按量价分分层
    print("  ── 按量价分(PV Score)分层 ──")
    for label, lo, hi in [('50-60', 50, 60), ('60-70', 60, 70), ('70-80', 70, 80), ('80+', 80, 999)]:
        sub = df[(df['pv_score'] >= lo) & (df['pv_score'] < hi)]
        if len(sub) > 0:
            sub_hit = sub['is_bear_trap'].sum()
            sub_false = sub['is_false_signal'].sum()
            print(f"    PV{label}: {len(sub)}次, 命中{sub_hit}次({sub_hit/len(sub)*100:.0f}%), 误判{sub_false}次({sub_false/len(sub)*100:.0f}%)")

    # 展示胜率高的信号特征
    print()
    print("  ☆ 高命中信号特征榜 (T+10反弹Top10):")
    top_hits = df.nlargest(10, 'best_recovery')
    for _, r in top_hits.iterrows():
        print(f"    {r['ts_code']:>10} {str(r.get('name',''))[:8]:>8} "
              f"| {r['trade_date']} | PV={r['pv_score']:.0f} "
              f"| T+10={r.get('T+10_ret',0):+.1f}% "
              f"| 最大反弹={r['best_recovery']:+.1f}%")

    print()


# ════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='空头陷阱择时策略回测')
    parser.add_argument('--start', type=str, default='20260101', help='回测起始日期')
    parser.add_argument('--end', type=str, default=None, help='回测截止日期')
    parser.add_argument('--quick', action='store_true', help='快速模式(每5天采样)')
    parser.add_argument('--max-per-day', type=int, default=15, help='每日最大信号数')
    args = parser.parse_args()

    run_backtest(
        start_date=args.start,
        end_date=args.end,
        max_signal_per_day=args.max_per_day,
        quick_mode=args.quick,
    )
