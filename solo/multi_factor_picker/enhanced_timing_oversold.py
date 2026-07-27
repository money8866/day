"""
中报预增股震荡缩量择时分析 — 震荡缩量到尾声 + MACD波动收窄
================================================================
核心理念：中报预增股经过前期上涨后进入震荡整理，当
  1) 震荡缩量到尾声 — 价格横盘 + 成交量持续萎缩至地量
  2) MACD波动收窄 — DIF-DEA间距收敛 + MACD柱缩短
  两者共振时，浮动筹码清洗干净，多空平衡即将打破，
  是二次拉升或反弹的前兆信号。

评分体系（总分100分）:
  震荡缩量(50分) = 震荡区间(15) + 量比缩量(20) + 量能趋势(15)
  MACD收窄(50分) = DIF-DEA收敛(25) + MACD柱收缩(15) + MACD位置(10)

输出:
  - enhanced_timing_oversold_{trade_date}.csv
  - 终端打印信号汇总
"""
import sys, os, json, pandas as pd, numpy as np
import argparse
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import DataFetcher
from loguru import logger
from oversold_timing_scorer import calc_consolidation_factors, score_consolidation, classify_consolidation_signal, calc_entry_timing, score_entry_timing
from theme_market_integration import (
    calc_theme_scores, calc_etf_trend_score, load_theme_etf_map,
    market_state_adjustment, compute_boosted_score
)
from market_regime import detect_market_regime

logger.remove()
logger.add(sys.stderr, level="WARNING")


def load_theme_stock_json() -> dict:
    """加载 build_theme_stock_map_v2.py 生成的 JSON

    Returns:
        {'stocks': {ts_code: {name, industry, themes, subtheme, ...}},
         'themes': {主题名: [{code, name, score, ...}, ...]}}
    """
    json_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'report_daily', 'theme_stock_map_latest_v2.json'
    )
    if not os.path.exists(json_path):
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'cache_daily', 'theme_stock_map_latest.json'
        )
    if not os.path.exists(json_path):
        logger.warning(f"未找到主题映射JSON")
        return {'stocks': {}, 'themes': {}}

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    stocks_map = data.get('stocks', {})
    themes_map = data.get('themes', {})
    logger.info(f"加载主题映射JSON: {os.path.basename(json_path)} ({len(stocks_map)}只股票, {len(themes_map)}个主题)")
    return {'stocks': stocks_map, 'themes': themes_map}


def main():
    parser = argparse.ArgumentParser(description='中报预增股震荡缩量择时分析')
    parser.add_argument('--date', type=str, default=None, help='指定交易日 YYYYMMDD（默认：最新交易日）')
    parser.add_argument('--replenish', action='store_true', help='补全stk_factor_pro缓存数据到最新')
    parser.add_argument('--simple', action='store_true', help='简化模式（跳过非核心功能）')
    args = parser.parse_args()

    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
    bull_csv = os.path.join(report_dir, 'bull_stocks_all.csv')
    if not os.path.exists(bull_csv):
        logger.error(f"未找到 {bull_csv}")
        return
    logger.info(f"读取: {bull_csv}")
    df = pd.read_csv(bull_csv, encoding='utf-8-sig')
    total = len(df)
    logger.info(f"共 {total} 只股票, 震荡缩量择时分析开始...")

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
    # 加载主题映射数据（build_theme_stock_map_v2.py 输出的JSON）
    # ═══════════════════════════════════════════
    json_data = load_theme_stock_json()
    theme_stock_map = json_data.get('stocks', {})   # 个股→主题查表
    theme_stock_list = json_data.get('themes', {})   # 主题→个股列表

    # ═══════════════════════════════════════════
    # 计算主题强度评分 (多维因子)
    # ═══════════════════════════════════════════
    logger.info("计算主题多因子强度评分...")
    theme_scores = calc_theme_scores(fetcher, trade_date, theme_stock_list)
    theme_etf_map = load_theme_etf_map()
    logger.info(f"  主题评分完成: {len(theme_scores)} 个主题有有效数据")

    # 主题排名
    theme_ranking = sorted(theme_scores.items(), key=lambda x: -x[1])
    logger.info(f"  前三强主题: {theme_ranking[:3] if len(theme_ranking)>=3 else theme_ranking}")

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
            if ts_code.startswith('8') or ts_code.startswith('4'):
                continue
            ts_codes.append(ts_code)

        result = fetcher.replenish_stk_factor_pro_batch(ts_codes, end_date=trade_date)
        ok_count = sum(1 for v in result.values() if v == 'ok')
        err_count = sum(1 for v in result.values() if v == 'error')
        logger.info(f"补数据完成: {ok_count}成功 / {err_count}失败 / {len(result)}总")

    # ═══════════════════════════════════════════
    # 市场状态检测（仅用于大盘环境参考，不参与个股评分）
    # ═══════════════════════════════════════════
    hs300 = fetcher.get_index_daily('000300.SH',
                                       start_date=(ref_date - timedelta(days=400)).strftime('%Y%m%d'),
                                       end_date=trade_date)
    regime_info = detect_market_regime(hs300)
    market_params = regime_info['params']
    logger.info(f"市场状态: {regime_info['regime_name']} (仅供参考)")

    # ═══════════════════════════════════════════
    # 市场状态门控调整
    # ═══════════════════════════════════════════
    market_adj = market_state_adjustment(regime_info['regime_name'])
    logger.info(f"市场门控: {market_adj['description']} (乘数={market_adj['multiplier']}, 降级={market_adj['grade_downgrade']}档)")

    # ETF评分缓存（同一ETF只计算一次）
    _etf_score_cache = {}

    def _get_etf_score(etf_code: str) -> float:
        if not etf_code:
            return 0.5
        if etf_code not in _etf_score_cache:
            _etf_score_cache[etf_code] = calc_etf_trend_score(fetcher, etf_code, trade_date)
        return _etf_score_cache[etf_code]

    # ── 批量计算 ──
    results = []
    for i, (_, row) in enumerate(df.iterrows(), 1):
        code_raw = str(row['code']).strip().lstrip('0')
        name = str(row['name'])
        industry = str(row.get('industry', ''))
        # 主题从JSON获取，bull_csv的theme列作为后备
        forecast_profit_yoy = float(row.get('利润同比', 0)) if pd.notna(row.get('利润同比', 0)) else 0

        if len(code_raw) == 5:
            code_padded = '0' + code_raw
        elif len(code_raw) == 4:
            code_padded = '00' + code_raw
        else:
            code_padded = code_raw.zfill(6)
        ts_code = code_padded + ('.SH' if code_padded.startswith('6') or code_padded.startswith('9') else '.SZ')

        # ── 从JSON获取主题和子主题 ──
        json_info = theme_stock_map.get(ts_code, {})
        themes = json_info.get('themes', [])
        theme_str = ';'.join(themes) if themes else str(row.get('theme', ''))
        subtheme = json_info.get('subtheme', '')

        # ── 主题强度分：取第一个主题的评分 ──
        stock_theme_score = max([theme_scores.get(t, 0.5) for t in themes]) if themes else 0.5

        # ── ETF共振：取第一个主题的ETF ──
        first_theme = themes[0] if themes else ''
        etf_code = theme_etf_map.get(first_theme, '') if first_theme else ''
        etf_res_score = _get_etf_score(etf_code)

        if i % 50 == 1 or i <= 3 or i == total:
            logger.info(f"[{i}/{total}] {name} ({ts_code})")

        # ── 获取 stk_factor_pro 技术因子数据（需含MACD字段） ──
        end_date = trade_date
        start_date = (ref_date - timedelta(days=200)).strftime('%Y%m%d')
        factor_df = fetcher.get_stk_factor_pro_range(ts_code, start_date=start_date, end_date=end_date)

        if factor_df is None or len(factor_df) < 40:
            continue

        # 确保有MACD字段
        if not all(c in factor_df.columns for c in ['macd_dif_hfq', 'macd_dea_hfq', 'macd_hfq']):
            continue

        factors = calc_consolidation_factors(factor_df)
        if factors is None:
            continue

        total_score, sub_scores = score_consolidation(factors)
        signal_level, signal_desc = classify_consolidation_signal(total_score)

        # ── 入场时机信号 ──
        entry_info = calc_entry_timing(factors, factor_df)
        entry_final_score, entry_grade, entry_advice = score_entry_timing(entry_info)

        # ── 三维融合：主题强度 + ETF共振 + 市场状态 ──
        # 仅作为辅助参考，不改变评分和评级
        boosted_total, boost_detail = compute_boosted_score(
            total_score, stock_theme_score, etf_res_score, market_adj
        )
        # 保留原始总分和入场评级
        final_score = total_score
        final_grade = entry_grade

        # 辅助价格数据
        price = factors.get('close', 0)
        vol_ratio = factors.get('vol_ratio', 0)
        osc_range = factors.get('osc_range_pct', 0)
        gap_ratio = factors.get('macd_gap_ratio', 0)
        macd_norm = factors.get('macd_norm', 0)
        macd_pos = factors.get('macd_position', '')
        macd_cross = factors.get('macd_cross', '无')

        # ── ATR止损价（用日线数据计算） ──
        daily = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)
        if daily is not None and len(daily) > 10:
            from enhanced_timing_analysis import _calc_atr
            atr = _calc_atr(daily, 14)
            if atr and atr > 0:
                close_price = float(daily['close'].values.astype(float)[-1])
                stop_loss = close_price - 1.5 * atr
                target = close_price + 2.5 * atr
                risk_reward = 2.5 / 1.5
                risk_pct = (close_price - stop_loss) / close_price * 100 if close_price > 0 else 0
            else:
                stop_loss = price * 0.95 if price > 0 else 0
                target = price * 1.08 if price > 0 else 0
                risk_reward = 1.6
                risk_pct = 5.0
        else:
            stop_loss = price * 0.95 if price > 0 else 0
            target = price * 1.08 if price > 0 else 0
            risk_reward = 1.6
            risk_pct = 5.0

        # 操作建议
        if total_score >= 80:
            action = '重点关注'
            risk_level = '低'
        elif total_score >= 65:
            action = '轻仓试探'
            risk_level = '中'
        elif total_score >= 50:
            action = '观望'
            risk_level = '中高'
        else:
            action = '等待'
            risk_level = '高'

        results.append({
            '代码': ts_code,
            '名称': name,
            '行业': industry,
            '主题': theme_str,
            '子主题': subtheme,
            '中报增速%': round(forecast_profit_yoy, 1),
            '择时分': final_score,
            '信号等级': signal_level,
            '入场类型': entry_info.get('entry_type_name', ''),
            '入场评分': entry_final_score,
            '入场评级': final_grade,
            '主题强度': round(stock_theme_score, 3),
            'ETF共振': round(etf_res_score, 3),
            '市场状态': regime_info['regime_name'],
            '现价': round(price, 2),
            '震荡区间%': round(osc_range, 1),
            '量比': round(vol_ratio, 2),
            'MACD%': round(macd_norm, 2),
            'DIF-DEA收敛比': round(gap_ratio, 2),
            'MACD位置': macd_pos,
            'MACD交叉': macd_cross,
            '入场价': round(entry_info.get('close', price), 2),
            '止损价': round(entry_info.get('stop_loss', 0), 2),
            '目标价': round(entry_info.get('target', 0), 2),
            '盈亏比': round(entry_info.get('risk_reward', 0), 2),
            '震荡区间': round(sub_scores.get('震荡区间', 0)),
            '量比缩量': round(sub_scores.get('量比缩量', 0)),
            '量能趋势': round(sub_scores.get('量能趋势', 0)),
            '震荡缩量': round(sub_scores.get('震荡缩量', 0)),
            'DIF-DEA收敛': round(sub_scores.get('DIF-DEA收敛', 0)),
            'MACD柱收缩': round(sub_scores.get('MACD柱收缩', 0)),
            'MACD位置分': round(sub_scores.get('MACD位置', 0)),
            'MACD收窄': round(sub_scores.get('MACD收窄', 0)),
        })

    if len(results) == 0:
        logger.error("没有有效数据")
        return

    # ── 排序 + 保存 ──
    out_df = pd.DataFrame(results)
    out_df = out_df.sort_values('择时分', ascending=False).reset_index(drop=True)
    out_path = os.path.join(report_dir, f'enhanced_timing_oversold_{trade_date}.csv')
    out_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"结果已保存: {out_path}")

    # ============================================================
    # 打印报告
    # ============================================================
    sep_char = '═'
    # 入场信号分布统计(使用市场调整后的评级)
    a_top = sum(1 for r in results if r['入场评级'] == 'A级最佳买点')
    b_top = sum(1 for r in results if r['入场评级'] == 'B级可入场')
    c_top = sum(1 for r in results if r['入场评级'] == 'C级观望')
    d_top = sum(1 for r in results if r['入场评级'] == 'D级等待')

    print(f'\n{sep_char*140}')
    print(f'  中报预增股 震荡缩量择时分析 (震荡缩量+MACD收窄 + 三级入场信号)')
    print(f'  分析日期: {datetime.now().strftime("%Y-%m-%d %H:%M")}  交易日: {trade_date}')
    print(f'  市场状态: {regime_info["regime_name"]} (仅供大盘环境参考)')
    print(f'  成功分析: {len(results)} 只 (需stk_factor_pro含MACD字段)')
    print(f'  入场信号: A级最佳买点={a_top}  B级可入场={b_top}  C级观望={c_top}  D级等待={d_top}')
    print(f'{sep_char*140}')

    # 信号分布
    strong = sum(1 for r in results if r['择时分'] >= 80)
    moderate = sum(1 for r in results if 65 <= r['择时分'] < 80)
    weak = sum(1 for r in results if 50 <= r['择时分'] < 65)
    no_signal = sum(1 for r in results if r['择时分'] < 50)
    print(f'\n  形态分布: 强烈 {strong}只 | 一般 {moderate}只 | 弱 {weak}只 | 无 {no_signal}只')
    print(f'{sep_char*140}')

    # 强烈信号
    strong_list = [r for r in results if r['择时分'] >= 80]
    if strong_list:
        print(f'\n{sep_char*140}')
        print(f'  ⭐⭐⭐ 强烈信号 (≥80分) — 震荡缩量+MACD收敛双重共振')
        print(f'{sep_char*140}')
        for r in strong_list[:15]:
            grade_short = r['入场评级'][:2] if r['入场评级'] else '  '
            print(f'  {grade_short} {r["名称"]:8s} ({r["代码"]}) '
                  f'总分={r["择时分"]:.0f} 入场评={r["入场评分"]:.0f} '
                  f'主题={r["主题强度"]:.2f} ETF={r["ETF共振"]:.2f} '
                  f'震荡{r["震荡区间%"]:.1f}% 量比={r["量比"]:.2f} '
                  f'MACD%={r["MACD%"]:.2f} {r["入场类型"]}')

    # 一般信号
    moderate_list = [r for r in results if 65 <= r['择时分'] < 80]
    if moderate_list:
        print(f'\n{sep_char*140}')
        print(f'  ⭐⭐ 一般信号 (65-79分) — 缩量震荡+MACD趋向收敛')
        print(f'{sep_char*140}')
        for r in moderate_list[:20]:
            print(f'  {r["名称"]:8s} ({r["代码"]}) '
                  f'总分={r["择时分"]:.0f} 震荡{r["震荡区间%"]:.1f}% '
                  f'量比={r["量比"]:.2f} MACD%={r["MACD%"]:.2f} {r["MACD交叉"]}')

    print(f'\n结果已保存: {out_path}')
    print(f'{sep_char*140}')
    print(f'  信号解读:')
    print(f'  ⭐⭐⭐ 强烈信号(≥80): 震荡缩量充分+MACD高度收敛，双重共振，变盘前兆')
    print(f'  ⭐⭐  一般信号(65-79): 缩量震荡中+MACD趋向收敛，关注后续确认')
    print(f'  ⭐   弱信号(50-64): 震荡或MACD收敛一方不足，继续观察')
    print(f'  -    无信号(<50): 未满足震荡缩量或MACD收敛条件')
    print(f'{sep_char*140}')


if __name__ == '__main__':
    main()
