"""
超跌择时回测 — 验证7因子评分系统的历史胜率（市场状态感知版）
================================================================
相比静态版本，此版本在每个历史日期检测当时的市场状态，
动态调整评分曲线和阈值，更真实地模拟实盘环境。

方法：
  1. 预先计算整个回测期内每天的市场状态（基于沪深300）
  2. 对每只中报预增股取过去2年日线数据
  3. 在每个交易日滚动计算超跌评分（无未来函数）
  4. 使用当日市场状态的动态参数评分
  5. 跟踪信号后5/10/20个交易日收益
  6. ATR止损/止盈触发检测

输出：
  - 终端打印胜率、月度统计、市场状态分布
  - backtest_oversold_dynamic_{trade_date}.csv 详细信号记录
  - 与静态参数版本（静态≥80分）的对比
"""
import sys, os, pandas as pd, numpy as np
import argparse
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import DataFetcher
from loguru import logger
from oversold_timing_scorer import calc_oversold_factors, score_oversold
from market_regime import detect_market_regime
from enhanced_timing_analysis import _calc_atr

logger.remove()
logger.add(sys.stderr, level="WARNING")

# ── 配置 ──
FORWARD_DAYS = [5, 10, 20]
STOP_LOSS_ATR = 1.5
TAKE_PROFIT_ATR = 2.5
BACKTEST_MONTHS = 12


def precompute_regimes(hs300_full: pd.DataFrame) -> dict:
    """
    预先计算每天的市场状态参数

    Parameters
    ----------
    hs300_full : DataFrame
        沪深300完整日线（按日期升序）

    Returns
    -------
    dict: {date_str: {'min_score': float, 'regime_name': str, 'params': dict}}
    """
    if hs300_full is None or len(hs300_full) < 100:
        return {}

    df = hs300_full.sort_values('trade_date').reset_index(drop=True)
    n = len(df)
    regime_map = {}

    for t in range(60, n):
        date_str = str(df.iloc[t]['trade_date'])
        subset = df.iloc[:t+1].copy()
        info = detect_market_regime(subset)
        regime_map[date_str] = {
            'min_score': info['params'].get('min_score', 80),
            'regime_name': info['regime_name'],
            'params': info['params'],
        }

    logger.info(f"预计算市场状态: {len(regime_map)}个交易日")
    return regime_map


def main():
    parser = argparse.ArgumentParser(description='超跌择时回测（市场状态感知版）')
    parser.add_argument('--date', type=str, default=None, help='指定交易日 YYYYMMDD（默认：最新交易日）')
    args = parser.parse_args()

    report_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily'
    )
    bull_csv = os.path.join(report_dir, 'bull_stocks_all.csv')
    if not os.path.exists(bull_csv):
        logger.error(f"未找到 {bull_csv}")
        return

    # ── 加载配置和数据 ──
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "main_config",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"),
    )
    main_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_mod)
    config = main_mod.load_config()
    token = main_mod.get_token(config)
    fetcher = DataFetcher(token, config)
    if args.date:
        trade_date = args.date
        logger.info(f"指定交易日: {trade_date}")
    else:
        trade_date = fetcher.get_last_trade_date()
    logger.info(f"交易日: {trade_date}")

    df = pd.read_csv(bull_csv, encoding='utf-8-sig')
    total_stocks = len(df)
    logger.info(f"股票池: {total_stocks} 只")

    # ── 确定回测时间范围 ──
    end_dt = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=BACKTEST_MONTHS * 30)
    start_str = start_dt.strftime('%Y%m%d')
    end_str = end_dt.strftime('%Y%m%d')

    # ═══════════════════════════════════════════
    # 预计算市场状态（整个回测期，每天一次）
    # ═══════════════════════════════════════════
    hs300_full = fetcher.get_index_daily(
        '000300.SH',
        start_date=(end_dt - timedelta(days=800)).strftime('%Y%m%d'),
        end_date=end_str,
    )
    regime_map = precompute_regimes(hs300_full)
    if not regime_map:
        logger.error("市场状态计算失败，使用静态参数")
        # 降级为静态: min_score=80, params=None
        regime_map = None

    # ── 状态统计 ──
    regime_counts = {}
    if regime_map:
        for date_str in sorted(regime_map.keys()):
            if start_str <= date_str <= end_str:
                name = regime_map[date_str]['regime_name']
                regime_counts[name] = regime_counts.get(name, 0) + 1

    # ═══════════════════════════════════════════
    # 逐只股票回测
    # ═══════════════════════════════════════════
    all_signals = []
    stats_per_stock = []

    for i, (_, row) in enumerate(df.iterrows(), 1):
        code_raw = str(row['code']).strip().lstrip('0')
        name = str(row['name'])
        forecast_profit_yoy = float(row.get('利润同比', 0)) if pd.notna(row.get('利润同比', 0)) else 0

        if len(code_raw) == 5:
            code_padded = '0' + code_raw
        elif len(code_raw) == 4:
            code_padded = '00' + code_raw
        else:
            code_padded = code_raw.zfill(6)
        ts_code = code_padded + (
            '.SH' if code_padded.startswith('6') or code_padded.startswith('9') else '.SZ'
        )

        if i % 50 == 1 or i <= 3 or i == total_stocks:
            logger.info(f"[{i}/{total_stocks}] {name} ({ts_code})")

        # 获取2年日线
        data_start = (end_dt - timedelta(days=720)).strftime('%Y%m%d')
        daily = fetcher.get_daily_by_code(ts_code, start_date=data_start, end_date=end_str)
        if daily is None or len(daily) < 100:
            continue

        daily = daily.sort_values('trade_date').reset_index(drop=True)
        closes = daily['close'].values.astype(float)
        highs = daily['high'].values.astype(float)
        lows = daily['low'].values.astype(float)
        n = len(closes)
        stock_signals = []

        # ── 滚动计算超跌评分（使用当日市场状态） ──
        for t in range(60, n - max(FORWARD_DAYS)):
            trade_date_str = str(daily.iloc[t]['trade_date'])
            if trade_date_str < start_str or trade_date_str > end_str:
                continue

            # 获取当日市场状态参数
            if regime_map and trade_date_str in regime_map:
                reg = regime_map[trade_date_str]
                market_params = reg['params']
                min_score = reg['min_score']
            else:
                market_params = None
                min_score = 80

            subset = daily.iloc[:t+1].copy()
            factors = calc_oversold_factors(subset, forecast_profit_yoy)
            if factors is None:
                continue

            score, sub = score_oversold(factors, market_params)
            if score < min_score:
                continue

            # ── 记录信号 ──
            entry_price = float(closes[t])

            fwd_returns = {}
            for fd in FORWARD_DAYS:
                if t + fd < n:
                    fwd_returns[f'{fd}d_return'] = (float(closes[t+fd]) - entry_price) / entry_price * 100
                else:
                    fwd_returns[f'{fd}d_return'] = None

            atr = _calc_atr(subset, 14)
            tp_price = entry_price + TAKE_PROFIT_ATR * (atr or 0)
            sl_price = entry_price - STOP_LOSS_ATR * (atr or 0)

            hit_take_profit = False
            hit_stop_loss = False
            max_ret = None
            min_ret = None

            if atr and atr > 0:
                for offset in range(1, min(20, n - t)):
                    day_high = float(highs[t + offset])
                    day_low = float(lows[t + offset])
                    day_ret = (float(closes[t + offset]) - entry_price) / entry_price * 100

                    if max_ret is None:
                        max_ret = day_ret
                        min_ret = day_ret
                    else:
                        if day_ret > max_ret:
                            max_ret = day_ret
                        if day_ret < min_ret:
                            min_ret = day_ret

                    if not hit_take_profit and day_high >= tp_price:
                        hit_take_profit = True
                    if not hit_stop_loss and day_low <= sl_price:
                        hit_stop_loss = True

            max_ret = max_ret or 0
            min_ret = min_ret or 0

            # 综合判定
            if hit_take_profit and not hit_stop_loss:
                outcome = '止盈'
            elif hit_stop_loss and not hit_take_profit:
                outcome = '止损'
            elif hit_take_profit and hit_stop_loss:
                outcome = '止盈' if max_ret > abs(min_ret) else '止损'
            else:
                fwd_20 = fwd_returns.get('20d_return', 0) or 0
                outcome = '盈利' if fwd_20 > 0 else '亏损'

            win = outcome in ('止盈', '盈利')

            # 记录市场状态对应的 min_score 和 regime_name
            regime_name = regime_map[trade_date_str]['regime_name'] if regime_map and trade_date_str in regime_map else '未知'

            record = {
                '股票': name,
                '代码': ts_code,
                '信号日期': trade_date_str,
                '市场状态': regime_name,
                '动态阈值': min_score,
                '超跌分': score,
                '入场价': round(entry_price, 2),
                '止盈价': round(tp_price, 2) if atr else None,
                '止损价': round(sl_price, 2) if atr else None,
                '5日收益%': round(fwd_returns.get('5d_return'), 2) if fwd_returns.get('5d_return') is not None else None,
                '10日收益%': round(fwd_returns.get('10d_return'), 2) if fwd_returns.get('10d_return') is not None else None,
                '20日收益%': round(fwd_returns.get('20d_return'), 2) if fwd_returns.get('20d_return') is not None else None,
                '最大浮盈%': round(max_ret, 2),
                '最大浮亏%': round(min_ret, 2),
                '触发结果': outcome,
                '是否盈利': '是' if win else '否',
                'F1回撤深度': round(sub.get('F1回撤深度', 0)),
                'F2缩量程度': round(sub.get('F2缩量程度', 0)),
                'F3支撑强度': round(sub.get('F3支撑强度', 0)),
                'F4_RSI超卖': round(sub.get('F4_RSI超卖', 0)),
                'F5_K线止跌': round(sub.get('F5_K线止跌', 0)),
                'F6基本面锚定': round(sub.get('F6基本面锚定', 0)),
                'F7趋势保护': round(sub.get('F7趋势保护', 0)),
                '中报增速%': round(forecast_profit_yoy, 1),
            }
            stock_signals.append(record)

        if stock_signals:
            # 连续信号去重（同一股票5个自然日内只保留分最高的信号）
            stock_signals.sort(key=lambda x: (int(x['信号日期']), -x['超跌分']))
            deduped = []
            for sig in stock_signals:
                if deduped:
                    last_date = int(deduped[-1]['信号日期'])
                    curr_date = int(sig['信号日期'])
                    if curr_date - last_date <= 5:  # 约3个交易日
                        if sig['超跌分'] > deduped[-1]['超跌分']:
                            deduped[-1] = sig
                    else:
                        deduped.append(sig)
                else:
                    deduped.append(sig)
            stock_signals = deduped

            all_signals.extend(stock_signals)
            sdf = pd.DataFrame(stock_signals)
            wins_s = sdf['是否盈利'].value_counts().get('是', 0)
            total_s = len(sdf)
            avg5 = sdf['5日收益%'].mean()
            avg20 = sdf['20日收益%'].mean()
            stats_per_stock.append({
                '股票': name, '代码': ts_code,
                '信号次数': total_s,
                '胜率%': round(wins_s / total_s * 100, 1) if total_s > 0 else 0,
                '平均5日收益%': round(avg5, 2) if pd.notna(avg5) else 0,
                '平均20日收益%': round(avg20, 2) if pd.notna(avg20) else 0,
            })

    if len(all_signals) == 0:
        logger.error("回测期内无超跌信号")
        return

    result_df = pd.DataFrame(all_signals)

    # ═══════════════════════════════════════════
    # 汇总统计
    # ═══════════════════════════════════════════
    total_signals = len(result_df)
    win_count = (result_df['是否盈利'] == '是').sum()
    loss_count = total_signals - win_count
    win_rate = win_count / total_signals * 100

    avg_5d = result_df['5日收益%'].mean()
    avg_10d = result_df['10日收益%'].mean()
    avg_20d = result_df['20日收益%'].mean()
    median_20d = result_df['20日收益%'].median()
    std_20d = result_df['20日收益%'].std()
    sharpe_20d = (avg_20d / std_20d * np.sqrt(12)) if (std_20d and not np.isnan(std_20d) and std_20d > 0) else 0

    outcome_dist = result_df['触发结果'].value_counts().to_dict()
    tp_count = outcome_dist.get('止盈', 0)
    sl_count = outcome_dist.get('止损', 0)
    profit_count = outcome_dist.get('盈利', 0)

    # 市场状态分布
    regime_dist = result_df['市场状态'].value_counts().to_dict()

    # 按市场状态统计胜率
    regime_stats = result_df.groupby('市场状态').agg(
        信号数=('是否盈利', 'count'),
        胜率=('是否盈利', lambda x: (x == '是').mean() * 100),
        平均20日收益=('20日收益%', 'mean'),
    ).round(2)

    # 保存
    out_path = os.path.join(report_dir, f'backtest_oversold_dynamic_{trade_date}.csv')
    result_df.to_csv(out_path, index=False, encoding='utf-8-sig')

    # ═══════════════════════════════════════════
    # 打印报告
    # ═══════════════════════════════════════════
    sep = '═'
    dash = '─'

    print(f'\n{sep*120}')
    print(f'  超跌择时 回测报告（市场状态感知版）')
    print(f'  回测区间: {start_str} ~ {end_str}  ({BACKTEST_MONTHS}个月)')
    print(f'  股票池: {total_stocks} 只中报预增股')
    print(f'  参数: 每日根据沪深300动态调整评分曲线和阈值')
    print(f'{sep*120}')
    print()

    print(f'  ┌{"市场状态分布":-^78}┐')
    print(f'  │ {"状态":<20} {"交易日数":<16} {"说明":<40} │')
    print(f'  ├{dash*78}┤')
    for name, cnt in sorted(regime_counts.items(), key=lambda x: -x[1]):
        print(f'  │ {name:<20} {cnt:<16} {"占回测期":<4}{cnt/sum(regime_counts.values())*100:.0f}%{"":<28} │')
    print(f'  └{dash*78}┘')
    print()

    print(f'  ┌{"整体表现（动态参数）":-^78}┐')
    print(f'  │ {"指标":<20} {"数值":<20} {"说明":<36} │')
    print(f'  ├{dash*78}┤')
    print(f'  │ {"总信号数":<20} {total_signals:<20} {"市场状态感知后全部信号":<36} │')
    print(f'  │ {"覆盖股票数":<20} {result_df["股票"].nunique():<20} {"发出过信号的股票":<36} │')
    print(f'  │ {"盈利次数":<20} {win_count:<20} {"止盈+20日收涨":<36} │')
    print(f'  │ {"亏损次数":<20} {loss_count:<20} {"止损+20日收跌":<36} │')
    print(f'  │ {"胜率":<20} {f"{win_rate:.1f}%":<20} {"动态参数":<36} │')
    if not np.isnan(avg_5d):
        print(f'  │ {"平均5日收益":<20} {f"{avg_5d:+.2f}%":<20} {"":<36} │')
    if not np.isnan(avg_10d):
        print(f'  │ {"平均10日收益":<20} {f"{avg_10d:+.2f}%":<20} {"":<36} │')
    if not np.isnan(avg_20d):
        print(f'  │ {"平均20日收益":<20} {f"{avg_20d:+.2f}%":<20} {"":<36} │')
    if not np.isnan(median_20d):
        print(f'  │ {"中位数20日收益":<20} {f"{median_20d:+.2f}%":<20} {"":<36} │')
    if sharpe_20d != 0:
        print(f'  │ {"年化夏普比率":<20} {f"{sharpe_20d:.2f}":<20} {"动态参数":<36} │')
    print(f'  └{dash*78}┘')
    print()

    print(f'  ┌{"触发分布":-^78}┐')
    print(f'  │ {"结果":<20} {"次数":<20} {"占比":<36} │')
    print(f'  ├{dash*78}┤')
    for k in ['止盈', '止损', '盈利', '亏损']:
        v = outcome_dist.get(k, 0)
        print(f'  │ {k:<20} {v:<20} {f"{v/total_signals*100:.1f}%":<36} │')
    print(f'  ├{dash*78}┤')
    print(f'  │ {"止盈/止损比":<20} {f"{tp_count}:{sl_count}":<20} {f"={tp_count/max(sl_count,1):.2f}":<36} │')
    print(f'  └{dash*78}┘')
    print()

    # 按市场状态展示胜率
    if len(regime_stats) > 0:
        print(f'  ┌{"各市场状态下的胜率":-^78}┐')
        print(f'  │ {"市场状态":<16} {"信号数":<10} {"胜率%":<12} {"平均20日收益%":<18} {"":<22} │')
        print(f'  ├{dash*78}┤')
        for idx, row in regime_stats.iterrows():
            print(f'  │ {idx:<16} {int(row["信号数"]):<10} {row["胜率"]:<12.1f} {row["平均20日收益"]:<+18.2f} {"":<22} │')
        print(f'  └{dash*78}┘')
        print()

    # 月度统计
    result_df['月份'] = result_df['信号日期'].astype(str).str[:6]
    monthly = result_df.groupby('月份').agg(
        信号数=('是否盈利', 'count'),
        胜率=('是否盈利', lambda x: (x == '是').mean() * 100),
        平均20日收益=('20日收益%', 'mean'),
    ).round(2)
    print(f'  ┌{"月度表现":-^78}┐')
    print(f'  │ {"月份":<12} {"信号数":<10} {"胜率%":<12} {"平均20日收益%":<18} {"":<26} │')
    print(f'  ├{dash*78}┤')
    for idx, row in monthly.iterrows():
        print(f'  │ {idx:<12} {int(row["信号数"]):<10} {row["胜率"]:<12.1f} {row["平均20日收益"]:<+18.2f} {"":<26} │')
    print(f'  └{dash*78}┘')
    print()

    # 信号最多的股票
    if stats_per_stock:
        sdf = pd.DataFrame(stats_per_stock)
        top10 = sdf.sort_values('信号次数', ascending=False).head(10)
        print(f'  ┌{"信号次数最多股票 TOP10":-^78}┐')
        print(f'  │ {"股票":<12} {"信号次数":<10} {"胜率%":<10} {"平均5日收益%":<16} {"平均20日收益%":<16} {"":<14} │')
        print(f'  ├{dash*78}┤')
        for _, r in top10.iterrows():
            print(f'  │ {r["股票"]:<12} {int(r["信号次数"]):<10} {r["胜率%"]:<10.1f} '
                  f'{r["平均5日收益%"]:<+16.2f} {r["平均20日收益%"]:<+16.2f} {"":<14} │')
        print(f'  └{dash*78}┘')
        print()

    print(f'\n详细记录已保存: {out_path}')
    print(f'{sep*120}')
    print(f'  与静态版本(≥80分)对比:')
    print(f'  动态参数版本胜率: {win_rate:.1f}%  |  动态信号数: {total_signals}')
    print(f'  （静态版本参见 backtest_oversold_{trade_date}.csv）')
    print(f'{sep*120}')

    return result_df


if __name__ == '__main__':
    main()
