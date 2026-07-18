# -*- coding: utf-8 -*-
"""
回测验证 (Backtest)

评估 Theme Persistence Score 对未来20/40/60日主题收益的预测能力。

指标:
  - IC (Pearson相关性)
  - Rank IC (Spearman秩相关)
  - Win Rate (Top5主题跑赢市场概率)
  - Top5 Theme Excess Return (Top5主题超额收益)
  - Maximum Drawdown (最大回撤)

对比基准:
  - Momentum only model (仅用20日动量)
  - Random baseline
"""
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def rolling_backtest(engine,
                     themes: list,
                     etf_data_history: dict,
                     benchmark_history: pd.DataFrame,
                     stock_data_history: dict = None,
                     start_date: str = '20240101',
                     end_date: str = '20260716',
                     horizons: list = [20, 40, 60],
                     freq: int = 5) -> dict:
    """
    滚动回测

    Args:
        engine: ThemePersistenceEngine 实例
        themes: [(theme_name, etf_code), ...]
        etf_data_history: {etf_code: DataFrame} 完整历史日线 (含start_date~end_date)
        benchmark_history: 沪深300完整历史日线
        stock_data_history: {etf_code: {ts_code: DataFrame}} 成份股历史 (可选)
        start_date: 回测起始日
        end_date: 回测结束日
        horizons: 预测窗口 [20, 40, 60]
        freq: 评分频率 (每freq个交易日评分一次)

    Returns:
        {'ic': {horizon: value}, 'rank_ic': {...}, 'win_rate': {...},
         'top5_excess_return': {...}, 'max_drawdown': {...},
         'scores_df': DataFrame, 'returns_df': DataFrame}
    """
    # 获取交易日历
    all_dates = set()
    for etf_code, df in etf_data_history.items():
        if df is not None and not df.empty:
            df['trade_date'] = pd.to_datetime(df['trade_date'], format="%Y%m%d")
            all_dates.update(df['trade_date'].dt.strftime('%Y%m%d').tolist())

    trade_dates = sorted([d for d in all_dates if start_date <= d <= end_date])
    if len(trade_dates) < max(horizons) + 60:
        print(f"[WARN] 交易日不足: {len(trade_dates)} < {max(horizons) + 60}")
        return {}

    # 每freq个交易日评分一次
    eval_dates = trade_dates[::freq]

    all_scores = []
    all_returns = []

    print(f"[Backtest] 评估 {len(eval_dates)} 个日期, horizons={horizons}")

    for i, eval_date in enumerate(eval_dates):
        if i % 20 == 0:
            print(f"  进度: {i}/{len(eval_dates)} ({eval_date})")

        # 截止到 eval_date 的数据
        etf_data_cutoff = {}
        for etf_code, df in etf_data_history.items():
            if df is None:
                continue
            cutoff = df[df['trade_date'] <= pd.to_datetime(eval_date, format="%Y%m%d")]
            if len(cutoff) >= 60:
                etf_data_cutoff[etf_code] = cutoff

        bm_cutoff = None
        if benchmark_history is not None:
            bm_cutoff = benchmark_history[
                benchmark_history['trade_date'] <= pd.to_datetime(eval_date, format="%Y%m%d")
            ]

        # 成份股数据截取
        stock_data_cutoff = {}
        if stock_data_history:
            for etf_code, stocks in stock_data_history.items():
                stock_data_cutoff[etf_code] = {}
                for ts_code, sdf in stocks.items():
                    if sdf is None:
                        continue
                    sc = sdf[sdf['trade_date'] <= pd.to_datetime(eval_date, format="%Y%m%d")]
                    if len(sc) >= 25:
                        stock_data_cutoff[etf_code][ts_code] = sc

        # 批量评分
        valid_themes = [(name, code) for name, code in themes if code in etf_data_cutoff]
        if not valid_themes:
            continue

        score_df = engine.score_themes_batch(
            themes=valid_themes,
            etf_data=etf_data_cutoff,
            benchmark_df=bm_cutoff,
            stock_data_map=stock_data_cutoff,
            trade_date=eval_date
        )

        if score_df.empty:
            continue

        # 计算未来收益
        for _, row in score_df.iterrows():
            etf_code = row['etf_code']
            etf_df = etf_data_cutoff.get(etf_code)
            if etf_df is None:
                continue

            etf_close = etf_df['close'].astype(float).reset_index(drop=True)
            current_idx = len(etf_close) - 1

            for h in horizons:
                future_idx = current_idx + h
                if future_idx >= len(etf_data_history[etf_code]):
                    continue

                full_close = etf_data_history[etf_code]['close'].astype(float).reset_index(drop=True)
                if future_idx >= len(full_close):
                    continue

                future_ret = (full_close.iloc[future_idx] / full_close.iloc[current_idx] - 1) * 100

                # 基准收益
                bm_ret = 0
                if benchmark_history is not None:
                    bm_close = benchmark_history['close'].astype(float).reset_index(drop=True)
                    bm_current_idx = len(bm_cutoff) - 1
                    bm_future_idx = bm_current_idx + h
                    if bm_future_idx < len(bm_close):
                        bm_ret = (bm_close.iloc[bm_future_idx] / bm_close.iloc[bm_current_idx] - 1) * 100

                excess_ret = future_ret - bm_ret

                all_scores.append({
                    'date': eval_date,
                    'theme': row['theme'],
                    'etf_code': etf_code,
                    'persistence_score': row['persistence_score'],
                    'momentum_20d': row.get('trend_stability', 50),
                    'horizon': h,
                    'future_return': future_ret,
                    'benchmark_return': bm_ret,
                    'excess_return': excess_ret,
                })

    if not all_scores:
        print("[WARN] 无有效回测数据")
        return {}

    df = pd.DataFrame(all_scores)

    # === 计算指标 ===
    results = {
        'ic': {},
        'rank_ic': {},
        'win_rate': {},
        'top5_excess_return': {},
        'max_drawdown': {},
        'scores_df': df,
    }

    for h in horizons:
        df_h = df[df['horizon'] == h]
        if len(df_h) < 10:
            continue

        # IC: Pearson相关性
        ic = df_h['persistence_score'].corr(df_h['future_return'])
        results['ic'][h] = round(float(ic), 4)

        # Rank IC: Spearman秩相关
        rank_ic = df_h['persistence_score'].corr(df_h['future_return'], method='spearman')
        results['rank_ic'][h] = round(float(rank_ic), 4)

        # Win Rate: Top5主题跑赢市场概率
        top5_per_date = df_h.groupby('date').apply(
            lambda x: x.nlargest(5, 'persistence_score')
        )
        win_rate = (top5_per_date['excess_return'] > 0).mean()
        results['win_rate'][h] = round(float(win_rate), 4)

        # Top5超额收益
        avg_excess = top5_per_date['excess_return'].mean()
        results['top5_excess_return'][h] = round(float(avg_excess), 2)

        # 最大回撤 (Top5组合)
        top5_per_date = top5_per_date.reset_index(drop=True)
        cum_returns = top5_per_date.groupby('date')['future_return'].mean()
        cum = (1 + cum_returns / 100).cumprod()
        running_max = cum.cummax()
        drawdown = (cum / running_max - 1) * 100
        results['max_drawdown'][h] = round(float(drawdown.min()), 2)

    # === 对比基准: Momentum only ===
    results['benchmark_momentum'] = {}
    for h in horizons:
        df_h = df[df['horizon'] == h]
        if len(df_h) < 10:
            continue
        # 用 trend_stability 作为 momentum proxy
        ic_mom = df_h['momentum_20d'].corr(df_h['future_return'])
        results['benchmark_momentum'][h] = round(float(ic_mom), 4)

    # === 对比基准: Random ===
    results['benchmark_random'] = {h: 0.0 for h in horizons}

    return results


def print_backtest_report(results: dict):
    """打印回测报告"""
    if not results:
        print("无回测结果")
        return

    print(f"\n{'═'*60}")
    print(f"  Theme Persistence Score — 回测报告")
    print(f"{'═'*60}")

    for h in [20, 40, 60]:
        if h not in results.get('ic', {}):
            continue
        print(f"\n  预测窗口: {h} 交易日")
        print(f"  {'─'*40}")
        print(f"  IC:              {results['ic'].get(h, 'N/A')}")
        print(f"  Rank IC:         {results['rank_ic'].get(h, 'N/A')}")
        print(f"  Win Rate:        {results['win_rate'].get(h, 'N/A')}")
        print(f"  Top5超额收益:    {results['top5_excess_return'].get(h, 'N/A')}%")
        print(f"  最大回撤:        {results['max_drawdown'].get(h, 'N/A')}%")
        print(f"  Momentum IC:     {results.get('benchmark_momentum', {}).get(h, 'N/A')}")
        print(f"  Random IC:       {results.get('benchmark_random', {}).get(h, 'N/A')}")

    print(f"\n{'═'*60}\n")
