"""
540只全量股票的增强择时分析
基于 bull_stocks_all.csv 做全量择时
"""
import sys, os, pandas as pd, numpy as np
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import DataFetcher
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

import importlib.util
spec = importlib.util.spec_from_file_location("main_config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"))
main_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main_mod)
load_config = main_mod.load_config
get_token = main_mod.get_token

# 从增强择时导入所有辅助函数
from enhanced_timing_analysis import _calc_vwap, _calc_atr, _calc_chip_concentration_peak, _calc_market_beta, _check_forecast_impact


def analyze_one(ts_code: str, name: str, industry: str,
                forecast_ann_date: str, forecast_profit_yoy: float,
                fetcher: DataFetcher) -> dict:
    """单只股票增强择时分析"""
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')

    daily = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)
    if daily is None or len(daily) < 30:
        return None

    daily = daily.sort_values('trade_date').reset_index(drop=True)
    closes = daily['close'].values.astype(float)
    highs = daily['high'].values.astype(float)
    lows = daily['low'].values.astype(float)
    opens = daily['open'].values.astype(float)
    vols = daily['vol'].values.astype(float)
    price = float(closes[-1])

    impact = _check_forecast_impact(ts_code, forecast_ann_date, fetcher)
    impact_blocked = impact['impact']

    vwap = _calc_vwap(daily, 20)
    peak_low, peak_high, peak_ratio = _calc_chip_concentration_peak(daily, 60)

    vwap_breakthrough = vwap is not None and price > vwap
    chip_breakthrough = peak_low is not None and price > peak_high
    true_breakthrough = vwap_breakthrough and chip_breakthrough

    ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
    pullback_confirm = False
    if true_breakthrough and ma20:
        above_ma20 = price > ma20
        recent_vol_ratio = float(np.mean(vols[-5:])) / float(np.mean(vols[-20:])) if float(np.mean(vols[-20:])) > 0 else 99
        has_dipped = any(closes[-i] <= vwap * 1.02 for i in range(1, min(11, len(closes)+1)))
        if above_ma20 and recent_vol_ratio < 1.2 and has_dipped:
            pullback_confirm = True

    atr = _calc_atr(daily, 14)
    if atr and atr > 0:
        dynamic_stop = price - 2.0 * atr
        trail_stop = price - 3.0 * atr
    else:
        dynamic_stop = None
        trail_stop = None

    beta = _calc_market_beta(industry, fetcher)
    K = 1.2 if beta['above_ma20'] else 0.6

    ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else None
    ma10 = float(np.mean(closes[-10:])) if len(closes) >= 10 else None
    ma20_ = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
    ma60 = float(np.mean(closes[-60:])) if len(closes) >= 60 else None

    base_score = 50
    if ma5 and ma10 and ma20_ and ma60:
        if ma5 > ma10 > ma20_ > ma60 and price > ma5:
            base_score = 90
        elif ma5 > ma10 > ma20_ > ma60:
            base_score = 80
        elif ma5 > ma10 > ma20_:
            base_score = 70
        elif ma5 < ma10 < ma20_ < ma60:
            base_score = 20
        elif ma5 < ma10 < ma20_:
            base_score = 35
        else:
            base_score = 50

    try:
        mf = fetcher.get_moneyflow_by_code(ts_code, start_date=start_date, end_date=end_date)
        mf_score = 0
        if mf is not None and len(mf) > 0:
            mf = mf.sort_values('trade_date').reset_index(drop=True)
            if 'net_mf_amount' in mf.columns:
                net_amount = float(mf['net_mf_amount'].iloc[-5:].sum())
                mf_score = 15 if net_amount > 0 else -15
    except Exception:
        mf_score = 0

    ret_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    ret_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0
    if ret_5d > 5:
        base_score += 5
    elif ret_5d < -5:
        base_score -= 5
    base_score += mf_score
    raw_timing_score = max(0, min(100, base_score))

    if impact_blocked:
        corrected_score = min(raw_timing_score * 0.3, 35)
        grade = 'E'
        trade_decision = '观望-利好兑现风险'
    else:
        corrected_score = raw_timing_score * K
        corrected_score = max(0, min(100, corrected_score))
        if true_breakthrough and pullback_confirm and corrected_score >= 60:
            grade = 'S'
            trade_decision = '极高胜率重仓买入'
        elif true_breakthrough and corrected_score >= 60:
            grade = 'A'
            trade_decision = '回踩VWAP确认加仓'
        elif vwap_breakthrough and corrected_score >= 50:
            grade = 'B'
            trade_decision = '关注-放量突破VWAP'
        elif corrected_score >= 50:
            grade = 'C'
            trade_decision = '观望-等待VWAP突破'
        elif corrected_score >= 35:
            grade = 'D'
            trade_decision = '低胜率规避'
        else:
            grade = 'E'
            trade_decision = '低胜率规避'

    if true_breakthrough and pullback_confirm:
        buy_point = '买点2(缩量回踩VWAP确认)'
    elif true_breakthrough:
        buy_point = '买点1(放量突破VWAP+筹码峰)'
    else:
        buy_point = '未突破'

    return {
        '代码': ts_code,
        '名称': name,
        '行业': industry,
        '中报业绩亮点': f"{forecast_profit_yoy:.1f}%" if forecast_profit_yoy else '',
        '原始Timing分': raw_timing_score,
        '兑现冲击过滤': '⚠️ 是' if impact_blocked else '✅ 否',
        '冲击详情': impact['detail'],
        'VWAP': round(vwap, 2) if vwap else None,
        '现价': round(price, 2),
        'MA20': round(ma20_, 2) if ma20_ else None,
        '筹码峰顶': round(peak_high, 2) if peak_high else None,
        '筹码集中度%': round(peak_ratio * 100, 1) if peak_ratio else None,
        'VWAP突破': '是' if vwap_breakthrough else '否',
        '筹码峰突破': '是' if chip_breakthrough else '否',
        '真突破判定': '✅ 真突破' if true_breakthrough else '❌ 未突破',
        '回踩确认': '✅ 是' if pullback_confirm else '否',
        '修正后胜率分级': grade,
        '大盘Beta系数K': K,
        '大盘状态': beta['trend'],
        '推荐买点类型': buy_point,
        'ATR': round(atr, 3) if atr else None,
        'ATR动态止损价': round(dynamic_stop, 2) if dynamic_stop else None,
        'ATR跟踪止盈价': round(trail_stop, 2) if trail_stop else None,
        '近5日%': round(ret_5d, 2),
        '近20日%': round(ret_20d, 2),
        '交易决策': trade_decision,
    }


def main():
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
    bull_csv = os.path.join(report_dir, 'bull_stocks_all.csv')
    if not os.path.exists(bull_csv):
        logger.error(f"未找到 {bull_csv}")
        return
    logger.info(f"读取: {bull_csv}")
    df = pd.read_csv(bull_csv, encoding='utf-8-sig')
    total = len(df)
    logger.info(f"共 {total} 只股票, 开始全量增强择时分析...")

    config = load_config()
    token = get_token(config)
    fetcher = DataFetcher(token, config)

    # 预获取一次forecast_vip，避免每只股票都重复请求
    logger.info("预获取 forecast_vip 数据...")
    try:
        forecast_vip_all = fetcher.get_forecast_vip('20260630')
    except Exception:
        forecast_vip_all = None
    logger.info("开始逐只分析...")

    results = []
    errors = 0
    for i, (_, row) in enumerate(df.iterrows(), 1):
        code_raw = str(row['code']).strip().lstrip('0')
        name = str(row['name'])
        industry = str(row.get('industry', ''))
        forecast_profit_yoy = float(row.get('利润同比', 0)) if pd.notna(row.get('利润同比', 0)) else 0

        # 补齐ts_code
        if len(code_raw) == 5:
            code_padded = '0' + code_raw
        elif len(code_raw) == 4:
            code_padded = '00' + code_raw
        else:
            code_padded = code_raw.zfill(6)
        ts_code = code_padded + ('.SH' if code_padded.startswith('6') or code_padded.startswith('9') else '.SZ')

        # 获取预告发布日期
        forecast_ann_date = ''
        if forecast_vip_all is not None and len(forecast_vip_all) > 0:
            fv = forecast_vip_all[forecast_vip_all['ts_code'] == ts_code]
            if len(fv) > 0:
                latest_f = fv.sort_values('ann_date', ascending=False).iloc[0]
                forecast_ann_date = str(latest_f.get('ann_date', ''))

        if i % 50 == 1 or i <= 3 or i == total:
            logger.info(f"[{i}/{total}] {name} ({ts_code})")
        result = analyze_one(ts_code, name, industry, forecast_ann_date, forecast_profit_yoy, fetcher)
        if result is None:
            errors += 1
            continue
        results.append(result)

    logger.info(f"分析完成: 成功 {len(results)} 只, 失败 {errors} 只")

    if not results:
        logger.error("没有有效结果")
        return

    out_df = pd.DataFrame(results)
    grade_order = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
    out_df['_grade_order'] = out_df['修正后胜率分级'].map(grade_order)
    out_df = out_df.sort_values(['_grade_order', '原始Timing分'], ascending=[True, False]).reset_index(drop=True)
    out_df = out_df.drop(columns=['_grade_order'])

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(report_dir, f'enhanced_timing_bull_all_{timestamp}.csv')
    out_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"结果已保存: {out_path}")

    # ─── 打印输出 ───
    print(f'\n{"="*160}')
    print(f'  540只全量增强择时分析')
    print(f'  分析日期: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  成功: {len(results)} 只, 失败: {errors} 只')
    print(f'{"="*160}')

    # 评级分布
    print(f'\n{"="*80}')
    print(f'  评级分布:')
    for g in ['S', 'A', 'B', 'C', 'D', 'E']:
        cnt = sum(1 for r in results if r['修正后胜率分级'] == g)
        if cnt > 0:
            print(f'    {g}: {cnt} 家 ({cnt/len(results)*100:.1f}%)')
    avg_raw = np.mean([r['原始Timing分'] for r in results])
    avg_corr = np.mean([r['修正后胜率分级'] in ('S', 'A', 'B', 'C') and r['修正后胜率分级'] not in ('D', 'E') for r in results])
    impact_cnt = sum(1 for r in results if '⚠️' in str(r['兑现冲击过滤']))
    bt_cnt = sum(1 for r in results if r['真突破判定'] == '✅ 真突破')
    pc_cnt = sum(1 for r in results if r['回踩确认'] == '✅ 是')
    print(f'  平均原始分: {avg_raw:.1f}')
    print(f'  兑现冲击触发: {impact_cnt} 家')
    print(f'  真突破: {bt_cnt} 家')
    print(f'  回踩确认: {pc_cnt} 家')
    print(f'{"="*80}')

    # S/A级详细
    print(f'\n{"="*160}')
    print(f'  ★ 极高胜率 - S/A级')
    print(f'{"="*160}')
    for r in results:
        if r['修正后胜率分级'] in ('S', 'A'):
            print(f'  [{r["修正后胜率分级"]}] {r["名称"]:8s} ({r["代码"]}) 原始分={r["原始Timing分"]} K={r["大盘Beta系数K"]} 止损={r["ATR动态止损价"]} 决策={r["交易决策"]}')

    # B级
    print(f'\n{"="*160}')
    print(f'  ★ B级 - 关注放量突破VWAP')
    print(f'{"="*160}')
    for r in results:
        if r['修正后胜率分级'] == 'B':
            print(f'  [B] {r["名称"]:8s} ({r["代码"]}) 原始分={r["原始Timing分"]} VWAP={r["VWAP"]} 现价={r["现价"]} 止损={r["ATR动态止损价"]}')

    # 兑现冲击列表
    impact_stocks = [r for r in results if '⚠️' in str(r['兑现冲击过滤'])]
    if impact_stocks:
        print(f'\n{"="*160}')
        print(f'  ⚠️ 兑现冲击触发列表')
        print(f'{"="*160}')
        for r in impact_stocks:
            print(f'  {r["名称"]:8s} ({r["代码"]}) 原始分={r["原始Timing分"]} 冲击详情={r["冲击详情"]}')

    print(f'\n结果已保存: {out_path}')
    print(f'{"="*160}')


if __name__ == '__main__':
    main()