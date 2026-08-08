"""
540只全量股票的增强择时分析 (幻方风格多因子量化版)
=====================================================
两阶段交叉截面排名:
  Phase 1: 批量计算6大因子原始值
  Phase 2: 交叉截面百分位排名 → 0-100量化择时分
  Phase 3: VWAP/筹码峰/ATR/Beta/兑现冲击 四大规则修正
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

from quant_timing_scorer import compute_raw_factors, cross_sectional_score
from enhanced_timing_analysis import _calc_vwap, _calc_atr, _calc_chip_concentration_peak, _calc_market_beta, _check_forecast_impact

# 回踩买点形态检测器（pullback_buy.py 已合并为本脚本的被调用模块；
# 日常只跑本脚本即同时输出 洗盘修复评分 + 回踩形态阶段 + 次日操作，不再单独跑 pullback_buy.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pullback_buy import analyze_shape


def collect_raw_factors(ts_code: str, fetcher: DataFetcher) -> dict:
    """Phase 1: 收集单只股票的原始因子值和基础数据"""
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')

    daily = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)
    if daily is None or len(daily) < 30:
        return None

    daily = daily.sort_values('trade_date').reset_index(drop=True)

    # 资金流向
    moneyflow = None
    try:
        mf = fetcher.get_moneyflow_by_code(ts_code, start_date=start_date, end_date=end_date)
        if mf is not None and len(mf) > 0:
            moneyflow = mf.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass

    # 计算6大因子原始值
    factors = compute_raw_factors(daily, moneyflow)

    # 基础价格数据
    closes = daily['close'].values.astype(float)
    vols = daily['vol'].values.astype(float)
    price = float(closes[-1])

    # VWAP
    vwap = _calc_vwap(daily, 20)

    # 筹码峰
    peak_low, peak_high, peak_ratio = _calc_chip_concentration_peak(daily, 60)

    # ATR
    atr = _calc_atr(daily, 14)

    # 均线
    ma20_val = float(np.mean(closes[-20:])) if len(closes) >= 20 else None

    # 回踩确认
    pullback_confirm = False
    if vwap and peak_low and price > vwap and price > peak_high and ma20_val:
        above_ma20 = price > ma20_val
        recent_vol_ratio = float(np.mean(vols[-5:])) / float(np.mean(vols[-20:])) if float(np.mean(vols[-20:])) > 0 else 99
        has_dipped = any(closes[-i] <= vwap * 1.02 for i in range(1, min(11, len(closes)+1)))
        if above_ma20 and recent_vol_ratio < 1.2 and has_dipped:
            pullback_confirm = True

    return {
        'daily': daily,
        'factors': factors,
        'price': price,
        'vwap': vwap,
        'peak_low': peak_low,
        'peak_high': peak_high,
        'peak_ratio': peak_ratio,
        'atr': atr,
        'ma20': ma20_val,
        'pullback_confirm': pullback_confirm,
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
    logger.info(f"共 {total} 只股票, 幻方量化择时开始...")

    config = load_config()
    token = get_token(config)
    fetcher = DataFetcher(token, config)

    logger.info("预获取 forecast_vip 数据...")
    try:
        forecast_vip_all = fetcher.get_forecast_vip('20260630')
    except Exception:
        forecast_vip_all = None

    # ============================================================
    # Phase 1: 批量计算所有原始因子
    # ============================================================
    logger.info("Phase 1: 批量计算6大因子原始值...")
    raw_data = {}       # ts_code -> raw_data dict
    factor_rows = []    # 用于构建因子DataFrame
    stock_meta = []     # 股票元数据

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

        rd = collect_raw_factors(ts_code, fetcher)
        if rd is None:
            continue

        raw_data[ts_code] = rd
        factor_rows.append({
            'ts_code': ts_code,
            **rd['factors'],
        })
        stock_meta.append({
            'ts_code': ts_code,
            'name': name,
            'industry': industry,
            'theme': theme,
            'forecast_profit_yoy': forecast_profit_yoy,
            'price': rd['price'],
            'vwap': rd['vwap'],
            'peak_low': rd['peak_low'],
            'peak_high': rd['peak_high'],
            'peak_ratio': rd['peak_ratio'],
            'atr': rd['atr'],
            'ma20': rd['ma20'],
            'pullback_confirm': rd['pullback_confirm'],
        })

    n_success = len(factor_rows)
    n_fail = total - n_success
    logger.info(f"Phase 1 完成: 成功 {n_success} 只, 失败 {n_fail} 只")

    if n_success == 0:
        logger.error("没有有效数据")
        return

    # ============================================================
    # Phase 2: 交叉截面百分位排名 → 量化择时分
    # ============================================================
    logger.info("Phase 2: 交叉截面百分位排名...")
    factor_df = pd.DataFrame(factor_rows)
    quant_scores = cross_sectional_score(factor_df)

    # 分数分布统计
    logger.info(f"量化择时分分布: min={quant_scores.min():.1f} Q25={quant_scores.quantile(0.25):.1f} "
                f"median={quant_scores.median():.1f} Q75={quant_scores.quantile(0.75):.1f} max={quant_scores.max():.1f}")

    # ============================================================
    # Phase 3: 四大规则修正 + 输出决策表
    # ============================================================
    logger.info("Phase 3: 四大规则修正 + 生成决策表...")

    meta_df = pd.DataFrame(stock_meta)
    meta_df['quant_score'] = quant_scores.values

    # 提取洗盘修复因子值 (用于输出列)
    washout_recovery_map = {}
    if 'washout_recovery' in factor_df.columns:
        for i, code in enumerate(factor_df['ts_code']):
            washout_recovery_map[code] = factor_df.iloc[i]['washout_recovery']

    # 洗盘修复因子的截面排名分 (0-100)
    if 'washout_recovery' in factor_df.columns:
        wr_rank = factor_df['washout_recovery'].rank(pct=True) * 100
        wr_rank_map = dict(zip(factor_df['ts_code'], wr_rank))
    else:
        wr_rank_map = {}

    results = []

    for _, m in meta_df.iterrows():
        ts_code = m['ts_code']
        name = m['name']
        industry = m['industry']
        theme = m['theme']
        forecast_profit_yoy = m['forecast_profit_yoy']
        price = m['price']
        vwap = m['vwap']
        peak_low = m['peak_low']
        peak_high = m['peak_high']
        peak_ratio = m['peak_ratio']
        atr = m['atr']
        ma20_val = m['ma20']
        pullback_confirm = m['pullback_confirm']
        raw_score = m['quant_score']

        # 洗盘修复因子
        wr_raw = washout_recovery_map.get(ts_code, 0.0)
        wr_score = wr_rank_map.get(ts_code, 0.0)

        # 兑现冲击
        forecast_ann_date = ''
        if forecast_vip_all is not None and len(forecast_vip_all) > 0:
            fv = forecast_vip_all[forecast_vip_all['ts_code'] == ts_code]
            if len(fv) > 0:
                latest_f = fv.sort_values('ann_date', ascending=False).iloc[0]
                forecast_ann_date = str(latest_f.get('ann_date', ''))

        impact = _check_forecast_impact(ts_code, forecast_ann_date, fetcher)
        impact_blocked = impact['impact']

        # Beta环境
        beta = _calc_market_beta(industry, fetcher)
        K = 1.2 if beta['above_ma20'] else 0.6

        # VWAP/筹码峰突破
        vwap_bt = vwap is not None and price > vwap
        chip_bt = peak_low is not None and price > peak_high
        true_bt = vwap_bt and chip_bt

        # ATR止损和止盈
        if atr and atr > 0:
            dynamic_stop = price - 2.0 * atr
            trail_stop = price + 3.0 * atr
        else:
            dynamic_stop = None
            trail_stop = None

        # 修正评分 + 评级 (评级基于原始量化分，K因子影响修正分和决策)
        corrected_score = raw_score * K
        corrected_score = max(0, min(100, corrected_score))

        # 评级基于原始量化分（交叉截面排名，不受K因子影响）
        # S级修复分硬门槛: 洗盘修复分<70 的"伪强势"（结构差但动量高）降为A级
        if true_bt and pullback_confirm and raw_score >= 85:
            if wr_score >= 70:
                grade = 'S'
                trade_decision = '极高胜率重仓买入'
            else:
                grade = 'A'
                trade_decision = f'回踩VWAP确认加仓 ⚠️修复分{wr_score:.0f}<70,S→A降级'
        elif true_bt and raw_score >= 75:
            grade = 'A'
            trade_decision = '回踩VWAP确认加仓'
        elif vwap_bt and raw_score >= 60:
            grade = 'B'
            trade_decision = '关注-放量突破VWAP'
        elif raw_score >= 55:
            grade = 'C'
            trade_decision = '观望-等待VWAP突破'
        elif raw_score >= 40:
            grade = 'D'
            trade_decision = '低胜率规避'
        else:
            grade = 'E'
            trade_decision = '规避'

        # 兑现冲击：不强制降级，仅加入风险提示
        if impact_blocked:
            trade_decision = f'{trade_decision} ⚠️ 利好兑现风险'
            corrected_score = min(corrected_score, 50)  # 修正分不超过50

        # 业绩背离检测：技术面好但基本面崩塌，强制降级
        if forecast_profit_yoy < -30 and grade in ('S', 'A', 'B'):
            old_grade = grade
            grade = 'C'
            trade_decision = f'⚠️ 业绩背离(利润{forecast_profit_yoy:.1f}%)→{old_grade}→C降级'
        elif forecast_profit_yoy < 0 and grade in ('S', 'A'):
            trade_decision = f'⚠️ 业绩下滑(利润{forecast_profit_yoy:.1f}%)-{trade_decision}'

        # 大盘空头环境：K=0.6时，决策附加谨慎提示
        if K < 1.0 and grade in ('S', 'A', 'B'):
            if grade == 'S':
                trade_decision = '极高胜率轻仓关注'
            elif grade == 'A':
                trade_decision = 'VWAP确认谨慎关注'
            elif grade == 'B':
                trade_decision = '关注-放量突破VWAP(轻仓)'

        if true_bt and pullback_confirm:
            buy_point = '买点2(缩量回踩VWAP确认)'
        elif true_bt:
            buy_point = '买点1(放量突破VWAP+筹码峰)'
        else:
            buy_point = '未突破'

        # ─── 结构增强分 (经验1-4融合) ───
        # 左侧结构: 洗盘修复分(抛压衰竭+承接转强形态完整度)
        # 右侧确认: 买点质量(买点2回踩VWAP确认 > 买点1放量突破 > 未突破)
        # 动量基础: 量化择时分
        buy_quality = {'买点2(缩量回踩VWAP确认)': 100, '买点1(放量突破VWAP+筹码峰)': 70, '未突破': 40}
        structure_boost = (
            wr_score * 0.30 +          # 洗盘修复形态
            raw_score * 0.50 +         # 量化动量
            buy_quality.get(buy_point, 40) * 0.20  # 买点质量(双确认+回踩)
        )

        # 洗盘修复标签 (仅对高分股票标注)
        washout_tag = ''
        if wr_score >= 90:
            washout_tag = '★★★ 洗盘修复完美'
        elif wr_score >= 80:
            washout_tag = '★★ 洗盘修复充分'
        elif wr_score >= 70:
            washout_tag = '★ 洗盘修复中'

        # ─── 回踩买点形态（合并自 pullback_buy.analyze_shape，直接回答"次日是否可买入"）───
        rd = raw_data.get(ts_code, {})
        shape = analyze_shape(rd['daily']) if rd.get('daily') is not None else None
        shape_stage = shape.get('stage', '') if shape else ''
        shape_decision = shape.get('decision', '') if shape else ''
        shape_score = shape.get('pullback_score', 0) if shape else 0
        shape_fyd = shape.get('first_yang_date', '') if shape else ''
        shape_pdays = shape.get('pullback_days', 0) if shape else 0

        results.append({
            '代码': ts_code,
            '名称': name,
            '行业': industry,
            '主题': theme,
            '中报业绩亮点': f"{forecast_profit_yoy:.1f}%" if forecast_profit_yoy else '',
            '量化择时分': round(raw_score, 1),
            '修正后评分': round(corrected_score, 1) if not impact_blocked else 0,
            '洗盘修复分': round(wr_score, 1),
            '结构增强分': round(structure_boost, 1),
            '洗盘修复标签': washout_tag,
            '形态阶段': shape_stage,
            '次日操作': shape_decision,
            '回踩买点分': round(shape_score, 1) if shape_score else '',
            '首阳日期': str(shape_fyd) if shape_fyd else '',
            '回踩天数': shape_pdays,
            '兑现冲击过滤': '⚠️ 是' if impact_blocked else '✅ 否',
            '冲击详情': impact['detail'],
            'VWAP': round(vwap, 2) if vwap else None,
            '现价': round(price, 2),
            'MA20': round(ma20_val, 2) if ma20_val else None,
            '筹码峰顶': round(peak_high, 2) if peak_high else None,
            '筹码集中度%': round(peak_ratio * 100, 1) if peak_ratio else None,
            'VWAP突破': '是' if vwap_bt else '否',
            '筹码峰突破': '是' if chip_bt else '否',
            '真突破判定': '✅ 真突破' if true_bt else '❌ 未突破',
            '回踩确认': '✅ 是' if pullback_confirm else '否',
            '修正后胜率分级': grade,
            '大盘Beta系数K': K,
            '大盘状态': beta['trend'],
            '推荐买点类型': buy_point,
            'ATR': round(atr, 3) if atr else None,
            'ATR动态止损价': round(dynamic_stop, 2) if dynamic_stop else None,
            'ATR跟踪止盈价': round(trail_stop, 2) if trail_stop else None,
            '交易决策': trade_decision,
        })

    # ─── 排序 + 保存 (次日操作可买入优先 → 评级 → 结构增强分) ───
    out_df = pd.DataFrame(results)
    grade_order = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
    op_order = {'✅ 次日可买入': 0, '⚠️ 次日观察等回踩': 1, '⚠️ 观察': 1, '❌ 仅观察不买入': 2, '❌ 等待首阳': 2, '': 3}
    out_df['_op_order'] = out_df['次日操作'].map(op_order).fillna(3)
    out_df['_grade_order'] = out_df['修正后胜率分级'].map(grade_order)
    out_df = out_df.sort_values(['_op_order', '_grade_order', '结构增强分'], ascending=[True, True, False]).reset_index(drop=True)
    out_df = out_df.drop(columns=['_op_order', '_grade_order'])

    trade_date = fetcher.get_last_trade_date()
    out_path = os.path.join(report_dir, f'enhanced_timing_bull_all_{trade_date}.csv')
    out_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"结果已保存: {out_path}")

    # ============================================================
    # 打印报告
    # ============================================================
    print(f'\n{"="*160}')
    print(f'  幻方量化多因子择时分析 (6因子×交叉截面排名)')
    print(f'  分析日期: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  成功: {n_success} 只, 失败: {n_fail} 只')
    print(f'{"="*160}')

    print(f'\n{"="*80}')
    print(f'  量化择时分分布:')
    print(f'    min={quant_scores.min():.1f}  Q25={quant_scores.quantile(0.25):.1f}  '
          f'median={quant_scores.median():.1f}  Q75={quant_scores.quantile(0.75):.1f}  max={quant_scores.max():.1f}')
    print(f'{"="*80}')

    print(f'\n{"="*80}')
    print(f'  最终评级分布:')
    for g in ['S', 'A', 'B', 'C', 'D', 'E']:
        cnt = sum(1 for r in results if r['修正后胜率分级'] == g)
        if cnt > 0:
            print(f'    {g}: {cnt} 家 ({cnt/len(results)*100:.1f}%)')
    impact_cnt = sum(1 for r in results if '⚠️' in str(r['兑现冲击过滤']))
    bt_cnt = sum(1 for r in results if r['真突破判定'] == '✅ 真突破')
    pc_cnt = sum(1 for r in results if r['回踩确认'] == '✅ 是')
    print(f'  兑现冲击: {impact_cnt} 家')
    print(f'  真突破: {bt_cnt} 家')
    print(f'  回踩确认: {pc_cnt} 家')
    print(f'{"="*80}')

    # S/A级
    print(f'\n{"="*160}')
    print(f'  ★ 极高胜率 - S/A级 (标注洗盘修复标签)')
    print(f'{"="*160}')
    for r in results:
        if r['修正后胜率分级'] in ('S', 'A'):
            tag = r.get('洗盘修复标签', '')
            tag_str = f' [{tag}]' if tag else ''
            print(f'  [{r["修正后胜率分级"]}] {r["名称"]:8s} ({r["代码"]}) '
                  f'主题={r["主题"]:12s} 量化分={r["量化择时分"]:.1f} 修正={r["修正后评分"]:.1f} '
                  f'洗盘修复={r["洗盘修复分"]:.1f}{tag_str} '
                  f'止损={r["ATR动态止损价"]} 决策={r["交易决策"]}')

    # B级 (前20)
    print(f'\n{"="*160}')
    print(f'  ★ B级 - 关注放量突破VWAP (前20)')
    print(f'{"="*160}')
    b_count = 0
    for r in results:
        if r['修正后胜率分级'] == 'B' and b_count < 20:
            print(f'  [B] {r["名称"]:8s} ({r["代码"]}) '
                  f'主题={r["主题"]:12s} 量化分={r["量化择时分"]:.1f} VWAP={r["VWAP"]} 现价={r["现价"]} 止损={r["ATR动态止损价"]}')
            b_count += 1

    # 兑现冲击 (前20)
    impact_stocks = [r for r in results if '⚠️' in str(r['兑现冲击过滤'])]
    if impact_stocks:
        print(f'\n{"="*160}')
        print(f'  ⚠️ 兑现冲击 (前20)')
        print(f'{"="*160}')
        for r in sorted(impact_stocks, key=lambda x: x['量化择时分'], reverse=True)[:20]:
            print(f'  {r["名称"]:8s} ({r["代码"]}) 量化分={r["量化择时分"]:.1f} 冲击={r["冲击详情"]}')

    # 洗盘修复专题 (调整充分形态, 前20)
    wr_stocks = [r for r in results if r.get('洗盘修复分', 0) >= 70]
    if wr_stocks:
        print(f'\n{"="*160}')
        print(f'  ★ 洗盘修复专题 — 调整充分、二波潜力股 (前20)')
        print(f'{"="*160}')
        for r in sorted(wr_stocks, key=lambda x: x['洗盘修复分'], reverse=True)[:20]:
            tag = r.get('洗盘修复标签', '')
            print(f'  {tag} {r["名称"]:8s} ({r["代码"]}) '
                  f'洗盘修复分={r["洗盘修复分"]:.1f} 量化分={r["量化择时分"]:.1f} '
                  f'评级={r["修正后胜率分级"]} 主题={r["主题"]:12s} '
                  f'现价={r["现价"]} 止损={r["ATR动态止损价"]} 决策={r["交易决策"]}')

    print(f'\n结果已保存: {out_path}')
    print(f'{"="*160}')


if __name__ == '__main__':
    main()