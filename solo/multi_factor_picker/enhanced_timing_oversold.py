"""
中报预增股超跌择时分析 — 左侧/左侧偏右买入时机（市场状态感知）
================================================================
基于7因子评分系统 + 沪深300市场状态动态参数调整：
  F1: 回撤深度(20%)  F2: 缩量程度(15%)  F3: 支撑强度(15%)
  F4: RSI超卖(15%)   F5: K线止跌(10%)   F6: 基本面锚定(10%)
  F7: 趋势保护(15%)  ← 权重从5%提升至15%（回测最强区分度因子）

市场状态感知：根据沪深300指数趋势/波动率/回撤动态调整各因子评分曲线和阈值

输出:
  - enhanced_timing_oversold_{trade_date}.csv
  - 终端打印超跌信号汇总 + 当前市场状态
"""
import sys, os, pandas as pd, numpy as np
import argparse
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import DataFetcher
from loguru import logger
from oversold_timing_scorer import calc_oversold_factors, score_oversold, classify_oversold_signal
from market_regime import detect_market_regime

logger.remove()
logger.add(sys.stderr, level="WARNING")


def main():
    parser = argparse.ArgumentParser(description='中报预增股超跌择时分析')
    parser.add_argument('--date', type=str, default=None, help='指定交易日 YYYYMMDD（默认：最新交易日）')
    parser.add_argument('--replenish', action='store_true', help='补全stk_factor_pro缓存数据到最新')
    args = parser.parse_args()

    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
    bull_csv = os.path.join(report_dir, 'bull_stocks_all.csv')
    if not os.path.exists(bull_csv):
        logger.error(f"未找到 {bull_csv}")
        return
    logger.info(f"读取: {bull_csv}")
    df = pd.read_csv(bull_csv, encoding='utf-8-sig')
    total = len(df)
    logger.info(f"共 {total} 只股票, 超跌择时分析开始...")

    # ── 导入配置 ──
    import importlib.util
    spec = importlib.util.spec_from_file_location("main_config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"))
    main_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_mod)
    load_config = main_mod.load_config
    get_token = main_mod.get_token

    config = load_config()
    token = get_token(config)
    fetcher = DataFetcher(token, config)

    if args.date:
        trade_date = args.date
        logger.info(f"指定交易日: {trade_date}")
    else:
        trade_date = fetcher.get_last_trade_date()
    logger.info(f"交易日: {trade_date}")

    ref_date = datetime.strptime(trade_date, '%Y%m%d')

    # ═══════════════════════════════════════════
    # 可选：补全 stk_factor_pro 缓存
    # ═══════════════════════════════════════════
    if args.replenish:
        logger.info("开始补全 stk_factor_pro 缓存数据...")
        ts_codes = []
        for _, row in df.iterrows():
            code_raw = str(row['code']).strip().lstrip('0')
            if len(code_raw) == 5:
                code_padded = '0' + code_raw
            elif len(code_raw) == 4:
                code_padded = '00' + code_raw
            else:
                code_padded = code_raw.zfill(6)
            ts_code = code_padded + ('.SH' if code_padded.startswith('6') or code_padded.startswith('9') else '.SZ')
            # 跳过北交所
            if ts_code.startswith('8') or ts_code.startswith('4'):
                continue
            ts_codes.append(ts_code)

        result = fetcher.replenish_stk_factor_pro_batch(ts_codes, end_date=trade_date)
        ok_count = sum(1 for v in result.values() if v == 'ok')
        err_count = sum(1 for v in result.values() if v == 'error')
        logger.info(f"补数据完成: {ok_count}成功 / {err_count}失败 / {len(result)}总")

    # ═══════════════════════════════════════════
    # 市场状态检测
    # ═══════════════════════════════════════════
    hs300 = fetcher.get_index_daily('000300.SH',
                                       start_date=(ref_date - timedelta(days=400)).strftime('%Y%m%d'),
                                       end_date=trade_date)
    regime_info = detect_market_regime(hs300)
    market_params = regime_info['params']
    min_score = market_params.get('min_score', 80)
    logger.info(f"市场状态: {regime_info['regime_name']} | 动态阈值: ≥{min_score}分")
    logger.info(f"参数说明: {market_params.get('description', '')}")

    # ── 批量计算超跌因子 ──
    results = []
    for i, (_, row) in enumerate(df.iterrows(), 1):
        code_raw = str(row['code']).strip().lstrip('0')
        name = str(row['name'])
        industry = str(row.get('industry', ''))
        theme = str(row.get('theme', ''))
        forecast_profit_yoy = float(row.get('利润同比', 0)) if pd.notna(row.get('利润同比', 0)) else 0

        if len(code_raw) == 5:
            code_padded = '0' + code_raw
        elif len(code_raw) == 4:
            code_padded = '00' + code_raw
        else:
            code_padded = code_raw.zfill(6)
        ts_code = code_padded + ('.SH' if code_padded.startswith('6') or code_padded.startswith('9') else '.SZ')

        if i % 50 == 1 or i <= 3 or i == total:
            logger.info(f"[{i}/{total}] {name} ({ts_code})")

        # ── 获取 stk_factor_pro 技术因子数据 ──
        end_date = trade_date
        start_date = (ref_date - timedelta(days=200)).strftime('%Y%m%d')
        factor_df = fetcher.get_stk_factor_pro_range(ts_code, start_date=start_date, end_date=end_date)

        # 同时获取日线数据作为后备（以及volume_ratio等补充字段）
        daily = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)

        # 判断数据是否充足
        if factor_df is None or len(factor_df) < 40:
            if daily is None or len(daily) < 40:
                continue
            # 降级：仅用 daily 数据
            factors = calc_oversold_factors(daily, forecast_profit_yoy, factor_df=None)
        else:
            # 优先使用 stk_factor_pro
            factors = calc_oversold_factors(daily, forecast_profit_yoy, factor_df=factor_df)

        if factors is None:
            continue

        total_score, sub_scores = score_oversold(factors, market_params)
        signal_level, signal_desc = classify_oversold_signal(total_score, min_score_strong=min_score)

        # 辅助价格数据
        closes = daily['close'].values.astype(float)
        price = float(closes[-1])
        ma20_val = factors.get('ma20')
        ma60_val = factors.get('ma60')
        drawdown = factors.get('drawdown_pct', 0)
        vol_ratio = factors.get('vol_ratio', 0)
        rsi_6 = factors.get('rsi_6', 0)

        # ATR止损
        from enhanced_timing_analysis import _calc_atr
        atr = _calc_atr(daily, 14)
        if atr and atr > 0:
            stop_loss = price - 1.5 * atr
            target = price + 2.5 * atr
            risk_reward = 2.5 / 1.5
            risk_pct = (price - stop_loss) / price * 100 if price > 0 else 0
        else:
            stop_loss = price * 0.95
            target = price * 1.08
            risk_reward = 1.6
            risk_pct = 5.0

        # 综合操作建议（使用动态阈值）
        if total_score >= min_score:
            action = '重点关注' if drawdown >= market_params.get('f1_peak_start', 8) else '轻仓试探'
            risk_level = '低'
        elif total_score >= min_score * 0.8:
            action = '轻仓试探'
            risk_level = '中'
        else:
            action = '等待观望'
            risk_level = '高'

        # 子分的详细输出
        sub_detail = ' | '.join(f'{k}={v:.0f}' for k, v in sorted(sub_scores.items()))

        results.append({
            '代码': ts_code,
            '名称': name,
            '行业': industry,
            '主题': theme,
            '中报增速%': round(forecast_profit_yoy, 1),
            '超跌择时分': total_score,
            '信号等级': signal_level,
            '操作建议': action,
            '风险等级': risk_level,
            '现价': round(price, 2),
            'MA20': round(ma20_val, 2) if ma20_val else None,
            'MA60': round(ma60_val, 2) if ma60_val else None,
            '回撤幅度%': round(drawdown, 1),
            '量比': round(vol_ratio, 2),
            'RSI(6)': round(rsi_6, 1),
            '止损价': round(stop_loss, 2),
            '目标价': round(target, 2),
            '盈亏比': round(risk_reward, 2),
            '最大亏损%': round(risk_pct, 1),
            'F1回撤深度': round(sub_scores.get('F1回撤深度', 0)),
            'F2缩量程度': round(sub_scores.get('F2缩量程度', 0)),
            'F3支撑强度': round(sub_scores.get('F3支撑强度', 0)),
            'F4_RSI超卖': round(sub_scores.get('F4_RSI超卖', 0)),
            'F5_K线止跌': round(sub_scores.get('F5_K线止跌', 0)),
            'F6基本面锚定': round(sub_scores.get('F6基本面锚定', 0)),
            'F7趋势保护': round(sub_scores.get('F7趋势保护', 0)),
        })

    if len(results) == 0:
        logger.error("没有有效数据")
        return

    # ── 排序 + 保存 ──
    out_df = pd.DataFrame(results)
    out_df = out_df.sort_values('超跌择时分', ascending=False).reset_index(drop=True)
    out_path = os.path.join(report_dir, f'enhanced_timing_oversold_{trade_date}.csv')
    out_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"结果已保存: {out_path}")

    # ============================================================
    # 打印报告
    # ============================================================
    sep_char = '═'
    market_stats = market_params.get('market_stats', {})

    print(f'\n{sep_char*140}')
    print(f'  中报预增股 超跌择时分析 (7因子评分 + 市场状态感知)')
    print(f'  分析日期: {datetime.now().strftime("%Y-%m-%d %H:%M")}  交易日: {trade_date}')
    print(f'  市场状态: {regime_info["regime_name"]} (阈值: ≥{min_score}分)')
    adj = market_params.get('adjustment', 0)
    if adj != 0:
        print(f'  大盘调整: {adj:+d}分（连续阴线保护）')
    if market_stats:
        print(f'  沪深300: {market_stats.get("hs300_price", "N/A")}  '
              f'MA20斜率: {market_stats.get("ma20_slope%", "N/A")}%  '
              f'60日回撤: {market_stats.get("max_drawdown60%", "N/A")}%  '
              f'20日上涨: {market_stats.get("up_days_20d", "N/A")}/20天')
    print(f'  参数: {market_params.get("description", "")}')
    print(f'  成功分析: {len(results)} 只')
    print(f'{sep_char*140}')

    # 信号分布（使用动态阈值）
    strong = sum(1 for r in results if r['超跌择时分'] >= min_score)
    moderate = sum(1 for r in results if min_score * 0.8 <= r['超跌择时分'] < min_score)
    wait = sum(1 for r in results if r['超跌择时分'] < min_score * 0.8)
    print(f'\n  信号分布(阈值≥{min_score}): 强烈反弹 {strong}只 | 一般反弹 {moderate}只 | 等待 {wait}只')
    print(f'{sep_char*140}')

    # 强烈超跌反弹信号
    strong_list = [r for r in results if r['超跌择时分'] >= min_score]
    if strong_list:
        print(f'\n{sep_char*140}')
        print(f'  ⭐⭐⭐ 强烈超跌反弹信号 (≥{min_score}分)')
        print(f'{sep_char*140}')
        for r in strong_list[:15]:
            print(f'  [{r["信号等级"]}] {r["名称"]:8s} ({r["代码"]}) '
                  f'超跌分={r["超跌择时分"]:.1f} 回撤={r["回撤幅度%"]:.1f}% '
                  f'量比={r["量比"]:.2f} RSI={r["RSI(6)"]:.0f} 止损={r["止损价"]} 目标={r["目标价"]}')

    # 一般超跌反弹信号
    moderate_list = [r for r in results if min_score * 0.8 <= r['超跌择时分'] < min_score]
    if moderate_list:
        print(f'\n{sep_char*140}')
        print(f'  ⭐⭐ 一般超跌反弹信号 ({int(min_score*0.8)}-{int(min_score-1)}分)')
        print(f'{sep_char*140}')
        for r in moderate_list[:20]:
            print(f'  {r["名称"]:8s} ({r["代码"]}) '
                  f'超跌分={r["超跌择时分"]:.1f} 回撤={r["回撤幅度%"]:.1f}% '
                  f'量比={r["量比"]:.2f} RSI={r["RSI(6)"]:.0f}')

    if wait > 0:
        print(f'\n{sep_char*140}')
        print(f'  等待观望: {wait}只（超跌分<{int(min_score*0.8)}，回撤不到位或卖压未衰竭）')
        print(f'{sep_char*140}')

    print(f'\n结果已保存: {out_path}')
    print(f'{sep_char*140}')
    print(f'  信号解读:')
    print(f'  ⭐⭐⭐ 强烈超跌反弹(≥{min_score}分): 回撤充分+缩量止跌+RSI超卖共振，左侧买入时机')
    print(f'  ⭐⭐  一般超跌反弹({int(min_score*0.8)}-{int(min_score-1)}分): 回调后缩量止跌，可轻仓试探，等放量确认')
    print(f'  ⭐   等待(<{int(min_score*0.8)}分): 回调未到位或卖压未衰竭，继续观察')
    print(f'{sep_char*140}')


if __name__ == '__main__':
    main()
