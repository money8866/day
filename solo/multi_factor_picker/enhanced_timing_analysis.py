"""
57只DoubleScore通过股票的增强择时分析 (幻方风格多因子量化版)
============================================================
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec = importlib.util.spec_from_file_location("main_config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"))
main_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main_mod)
load_config = main_mod.load_config
get_token = main_mod.get_token

from quant_timing_scorer import compute_raw_factors, cross_sectional_score

logger.remove()
logger.add(sys.stderr, level="INFO")


def _calc_vwap(df: pd.DataFrame, n: int = 20) -> float:
    """计算近N日的VWAP（成交量加权平均价）"""
    if len(df) < n:
        return None
    recent = df.iloc[-n:]
    total_vol = recent['vol'].sum()
    if total_vol == 0:
        return None
    # amount是千元，vol是手(100股)
    # VWAP(元/股) = (amount*1000) / (vol*100) = amount/vol * 10
    vwap_price = recent['amount'].sum() / recent['vol'].sum() * 10
    return vwap_price


def _calc_atr(df: pd.DataFrame, n: int = 14) -> float:
    """计算14日ATR（平均真实波幅）"""
    if len(df) < n + 1:
        return None
    tr_list = []
    for i in range(-n, 0):
        high = float(df.iloc[i]['high'])
        low = float(df.iloc[i]['low'])
        prev_close = float(df.iloc[i-1]['close'])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    return float(np.mean(tr_list))


def _calc_chip_concentration_peak(df: pd.DataFrame, n: int = 60) -> tuple:
    """
    计算近N日筹码峰顶（密集区）
    将价格区间等分10格，统计每格成交量占比，返回占比最高的区间
    Returns: (peak_low, peak_high, peak_ratio)
    """
    if len(df) < n:
        return None, None, None
    recent = df.iloc[-n:]
    price_high = float(recent['high'].max())
    price_low = float(recent['low'].min())
    bins = 10
    bin_width = (price_high - price_low) / bins
    if bin_width == 0:
        return price_low, price_high, 1.0

    vol_by_bin = [0.0] * bins
    for _, row in recent.iterrows():
        low = float(row['low'])
        high = float(row['high'])
        vol = float(row['vol'])
        # 将成交量按比例分配到各价格区间
        left = low
        for b in range(bins):
            bin_left = price_low + b * bin_width
            bin_right = bin_left + bin_width
            if high <= bin_left or low >= bin_right:
                continue
            overlap_low = max(low, bin_left)
            overlap_high = min(high, bin_right)
            if overlap_high > overlap_low:
                ratio = (overlap_high - overlap_low) / (high - low) if high > low else 0
                vol_by_bin[b] += vol * ratio

    total_vol = sum(vol_by_bin)
    if total_vol == 0:
        return price_low, price_high, 0.0
    peak_idx = int(np.argmax(vol_by_bin))
    peak_low = price_low + peak_idx * bin_width
    peak_high = peak_low + bin_width
    peak_ratio = vol_by_bin[peak_idx] / total_vol
    return peak_low, peak_high, peak_ratio


def _calc_market_beta(industry: str, fetcher: DataFetcher) -> dict:
    """计算行业Beta环境，返回板块状态"""
    # 用上证指数作为市场基准
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
    index_df = fetcher.get_index_daily('000001.SH', start_date=start_date, end_date=end_date)
    if index_df is None or len(index_df) < 30:
        return {'ma20': None, 'above_ma20': None, 'ma20_price': None, 'trend': '未知'}

    index_df = index_df.sort_values('trade_date').reset_index(drop=True)
    closes = index_df['close'].values.astype(float)
    ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
    current = float(closes[-1])
    above_ma20 = current > ma20 if ma20 else None
    ret_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0
    ret_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0

    if above_ma20:
        trend = f'大盘多头（MA20={ma20:.0f}，{industry}板块）'
    else:
        trend = f'大盘空头（MA20={ma20:.0f}，{industry}板块）'
    return {
        'ma20': ma20,
        'above_ma20': above_ma20,
        'current': current,
        'ret_20d': round(ret_20d, 2),
        'ret_5d': round(ret_5d, 2),
        'trend': trend,
    }


def _check_forecast_impact(ts_code: str, ann_date: str, fetcher: DataFetcher) -> dict:
    """
    检查业绩预告发布后的兑现冲击
    规则：预告发布后T0~T2，若：
      - 开盘跳空>5% 且 换手率>15%；或
      - 收阴线且成交量>20日均量2倍
    → 强制降级
    """
    if not ann_date or ann_date == 'nan' or ann_date == '':
        return {'impact': False, 'detail': '无预告日期'}

    ann_dt = datetime.strptime(ann_date, '%Y%m%d')
    start = (ann_dt - timedelta(days=30)).strftime('%Y%m%d')
    end = (ann_dt + timedelta(days=5)).strftime('%Y%m%d')

    daily = fetcher.get_daily_by_code(ts_code, start_date=start, end_date=end)
    if daily is None or len(daily) < 5:
        return {'impact': False, 'detail': '日线数据不足'}

    daily = daily.sort_values('trade_date').reset_index(drop=True)

    # 找到预告日对应的交易日
    ann_idx = None
    for i, row in daily.iterrows():
        if str(row['trade_date']) >= ann_date:
            ann_idx = i
            break
    if ann_idx is None:
        return {'impact': False, 'detail': '预告日未找到匹配交易日'}

    # 计算20日均量
    vol_series = daily['vol'].values.astype(float)
    ma20_vol = float(np.mean(vol_series[-20:])) if len(vol_series) >= 20 else float(np.mean(vol_series))

    # 检查T0~T2
    for offset in range(3):
        idx = ann_idx + offset
        if idx >= len(daily):
            break
        row = daily.iloc[idx]
        open_p = float(row['open'])
        close_p = float(row['close'])
        pre_close = float(row['pre_close']) if 'pre_close' in daily.columns and pd.notna(row['pre_close']) else open_p
        vol = float(row['vol'])
        turnover = vol / ma20_vol if ma20_vol > 0 else 0  # 量比

        gap_pct = (open_p - pre_close) / pre_close * 100
        is_negative = close_p < open_p
        # daily_basic拿换手率
        trade_date_str = str(row['trade_date'])

        # 检查条件1：跳空>5% 且 量比>2.0 → 仅当收阴线时判定为兑现冲击
        # 收阳线则是资金持续买入，不是兑现冲击
        if gap_pct > 5 and turnover > 2.0 and is_negative:
            return {
                'impact': True,
                'detail': f'T+{offset} 跳空{gap_pct:.1f}%+巨量({turnover:.1f}x)+阴线',
                'ann_date': ann_date,
                'impact_date': trade_date_str,
            }
        # 条件2：收阴线且成交量>20日均量2倍 → 兑现冲击
        if is_negative and turnover > 2.0:
            return {
                'impact': True,
                'detail': f'T+{offset} 阴线放量({turnover:.1f}x)',
                'ann_date': ann_date,
                'impact_date': trade_date_str,
            }

    return {'impact': False, 'detail': '无明显兑现冲击'}


def collect_ds_raw_factors(ts_code: str, name: str, industry: str,
                           forecast_ann_date: str, forecast_profit_yoy: float,
                           fetcher: DataFetcher) -> dict:
    """Phase 1: 收集单只股票的原始因子值和基础数据"""
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')

    daily = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)
    if daily is None or len(daily) < 30:
        return None

    daily = daily.sort_values('trade_date').reset_index(drop=True)
    closes = daily['close'].values.astype(float)
    vols = daily['vol'].values.astype(float)
    price = float(closes[-1])

    # 资金流向
    moneyflow = None
    try:
        mf = fetcher.get_moneyflow_by_code(ts_code, start_date=start_date, end_date=end_date)
        if mf is not None and len(mf) > 0:
            moneyflow = mf.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass

    # 6大因子原始值
    factors = compute_raw_factors(daily, moneyflow)

    # 近5日/20日收益
    ret_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    ret_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0

    # ─── 规则1：兑现冲击过滤器 ───
    impact = _check_forecast_impact(ts_code, forecast_ann_date, fetcher)

    # ─── 规则2：VWAP + 筹码峰突破 ───
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

    # ─── 规则3：ATR动态止损 ───
    atr = _calc_atr(daily, 14)

    # ─── 规则4：行业Beta环境 ───
    beta = _calc_market_beta(industry, fetcher)

    return {
        'ts_code': ts_code,
        'name': name,
        'industry': industry,
        'forecast_ann_date': forecast_ann_date,
        'forecast_profit_yoy': forecast_profit_yoy,
        'factors': factors,
        'price': price,
        'vwap': vwap,
        'peak_low': peak_low,
        'peak_high': peak_high,
        'peak_ratio': peak_ratio,
        'vwap_breakthrough': vwap_breakthrough,
        'chip_breakthrough': chip_breakthrough,
        'true_breakthrough': true_breakthrough,
        'pullback_confirm': pullback_confirm,
        'ma20': ma20,
        'atr': atr,
        'ret_5d': ret_5d,
        'ret_20d': ret_20d,
        'impact': impact,
        'beta': beta,
    }


def main():
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
    csv_files = [f for f in os.listdir(report_dir) if f.startswith('double_score_') and f.endswith('.csv') and f != 'double_score_top.csv']
    if not csv_files:
        logger.error("未找到 double_score CSV 文件")
        return
    latest_csv = sorted(csv_files)[-1]
    csv_path = os.path.join(report_dir, latest_csv)
    logger.info(f"读取: {csv_path}")
    ds_df = pd.read_csv(csv_path, encoding='utf-8-sig')

    bull_csv = os.path.join(report_dir, 'bull_stocks_all.csv')
    bull_df = None
    if os.path.exists(bull_csv):
        bull_df = pd.read_csv(bull_csv, encoding='utf-8-sig')

    config = load_config()
    token = get_token(config)
    fetcher = DataFetcher(token, config)

    # 预获取 forecast_vip
    logger.info("预获取 forecast_vip 数据...")
    forecast_vip_all = None
    try:
        forecast_vip_all = fetcher.get_forecast_vip('20260630')
    except Exception:
        pass

    # ============================================================
    # Phase 1: 批量计算6大因子原始值
    # ============================================================
    logger.info(f"Phase 1: 对 {len(ds_df)} 只股票计算6大因子原始值...")
    raw_data_list = []
    factor_rows = []

    for i, (_, row) in enumerate(ds_df.iterrows(), 1):
        code_raw = str(row['代码']).strip()
        name = row['名称']
        code_padded = code_raw.zfill(6)
        ts_code = code_padded + ('.SH' if code_padded.startswith('6') or code_padded.startswith('9') else '.SZ')

        industry = ''
        forecast_ann_date = ''
        forecast_profit_yoy = 0
        if bull_df is not None:
            match = bull_df[bull_df['code'].astype(str).str.strip() == code_raw]
            if len(match) > 0:
                m = match.iloc[0]
                industry = str(m.get('industry', ''))
                forecast_profit_yoy = float(m.get('利润同比', 0))

        # 预告发布日期
        if forecast_vip_all is not None and len(forecast_vip_all) > 0:
            fv = forecast_vip_all[forecast_vip_all['ts_code'] == ts_code]
            if len(fv) > 0:
                latest_f = fv.sort_values('ann_date', ascending=False).iloc[0]
                forecast_ann_date = str(latest_f.get('ann_date', ''))

        logger.info(f"[{i}/{len(ds_df)}] {name} ({ts_code})")
        rd = collect_ds_raw_factors(ts_code, name, industry, forecast_ann_date, forecast_profit_yoy, fetcher)
        if rd is None:
            logger.warning(f"  {name}: 数据不足，跳过")
            continue

        rd['DoubleScore'] = row.get('DoubleScore', 0)
        rd['核心逻辑'] = row.get('核心逻辑', '')
        raw_data_list.append(rd)
        factor_rows.append({
            'ts_code': ts_code,
            **rd['factors'],
        })

    n_success = len(factor_rows)
    n_fail = len(ds_df) - n_success
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

    logger.info(f"量化择时分分布: min={quant_scores.min():.1f} Q25={quant_scores.quantile(0.25):.1f} "
                f"median={quant_scores.median():.1f} Q75={quant_scores.quantile(0.75):.1f} max={quant_scores.max():.1f}")

    # ============================================================
    # Phase 3: 四大规则修正 + 输出决策表
    # ============================================================
    logger.info("Phase 3: 四大规则修正 + 生成决策表...")

    results = []
    for rd in raw_data_list:
        ts_code = rd['ts_code']
        name = rd['name']
        industry = rd['industry']
        forecast_profit_yoy = rd['forecast_profit_yoy']
        price = rd['price']
        vwap = rd['vwap']
        peak_high = rd['peak_high']
        peak_ratio = rd['peak_ratio']
        atr = rd['atr']
        ma20_val = rd['ma20']
        vwap_breakthrough = rd['vwap_breakthrough']
        chip_breakthrough = rd['chip_breakthrough']
        true_breakthrough = rd['true_breakthrough']
        pullback_confirm = rd['pullback_confirm']
        ret_5d = rd['ret_5d']
        ret_20d = rd['ret_20d']
        impact = rd['impact']
        beta = rd['beta']
        DoubleScore = rd['DoubleScore']
        core_logic = rd['核心逻辑']

        # 量化择时分
        raw_score = float(quant_scores[ts_code])

        impact_blocked = impact['impact']
        K = 1.2 if beta['above_ma20'] else 0.6

        # ATR止损
        if atr and atr > 0:
            dynamic_stop = price - 2.0 * atr
            trail_stop = price - 3.0 * atr
        else:
            dynamic_stop = None
            trail_stop = None

        # 修正评分 + 评级 (评级基于原始量化分，K因子影响修正分和决策)
        if impact_blocked:
            corrected_score = min(raw_score * 0.3, 30)
            grade = 'E'
            trade_decision = '观望-利好兑现风险'
        else:
            corrected_score = raw_score * K
            corrected_score = max(0, min(100, corrected_score))

            if true_breakthrough and pullback_confirm and raw_score >= 85:
                grade = 'S'
                trade_decision = '极高胜率重仓买入'
            elif true_breakthrough and raw_score >= 75:
                grade = 'A'
                trade_decision = '回踩VWAP确认加仓'
            elif vwap_breakthrough and raw_score >= 60:
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
                trade_decision = '低胜率规避'

            if forecast_profit_yoy < -30 and grade in ('S', 'A', 'B'):
                old_grade = grade
                grade = 'C'
                trade_decision = f'⚠️ 业绩背离(利润{forecast_profit_yoy:.1f}%)→{old_grade}→C降级'
            elif forecast_profit_yoy < 0 and grade in ('S', 'A'):
                trade_decision = f'⚠️ 业绩下滑(利润{forecast_profit_yoy:.1f}%)-{trade_decision}'

            if K < 1.0 and grade in ('S', 'A', 'B'):
                if grade == 'S':
                    trade_decision = '极高胜率轻仓关注'
                elif grade == 'A':
                    trade_decision = 'VWAP确认谨慎关注'
                elif grade == 'B':
                    trade_decision = '关注-放量突破VWAP(轻仓)'

        if true_breakthrough and pullback_confirm:
            buy_point = '买点2(缩量回踩VWAP确认)'
        elif true_breakthrough:
            buy_point = '买点1(放量突破VWAP+筹码峰)'
        else:
            buy_point = '未突破'

        # 趋势标签
        if raw_score >= 75:
            trend = '多头趋势'
        elif raw_score >= 55:
            trend = '震荡偏多'
        elif raw_score >= 40:
            trend = '震荡整理'
        else:
            trend = '空头趋势'

        results.append({
            'ts_code': ts_code,
            'name': name,
            'industry': industry,
            'forecast_profit_yoy': forecast_profit_yoy,
            'raw_timing_score': round(raw_score, 1),
            'corrected_score': round(corrected_score, 1),
            'grade': grade,
            'trend': trend,
            'impact_blocked': impact_blocked,
            'impact_detail': impact['detail'],
            'vwap': round(vwap, 2) if vwap else None,
            'price': round(price, 2),
            'ma20': round(ma20_val, 2) if ma20_val else None,
            'vwap_breakthrough': vwap_breakthrough,
            'chip_breakthrough': chip_breakthrough,
            'true_breakthrough': true_breakthrough,
            'pullback_confirm': pullback_confirm,
            'chip_peak_high': round(peak_high, 2) if peak_high else None,
            'chip_peak_ratio': round(peak_ratio * 100, 1) if peak_ratio else None,
            'atr': round(atr, 3) if atr else None,
            'dynamic_stop': round(dynamic_stop, 2) if dynamic_stop else None,
            'trail_stop': round(trail_stop, 2) if trail_stop else None,
            'ret_5d': round(ret_5d, 2),
            'ret_20d': round(ret_20d, 2),
            'beta_K': K,
            'market_trend': beta['trend'],
            'buy_point': buy_point,
            'trade_decision': trade_decision,
            'DoubleScore': DoubleScore,
            '核心逻辑': core_logic,
        })

        grade_str = grade
        impact_str = '⚠️ 兑现冲击' if impact_blocked else ''
        bt_str = '✅ 真突破' if true_breakthrough else ''
        pc_str = '✅ 回踩确认' if pullback_confirm else ''
        logger.info(f"  → 量化分={raw_score:.1f} K={K} 修正分={corrected_score:.1f}/{grade_str} {impact_str} {bt_str} {pc_str}")

    if not results:
        logger.error("没有分析出有效结果")
        return

    # ─── 构建输出DataFrame ───
    out_rows = []
    for r in results:
        out_rows.append({
            '代码': r['ts_code'],
            '名称': r['name'],
            '行业': r['industry'],
            '中报业绩亮点': f"{r['forecast_profit_yoy']:.1f}%",
            '量化择时分': r['raw_timing_score'],
            '修正后评分': r['corrected_score'],
            '兑现冲击过滤': '⚠️ 是' if r['impact_blocked'] else '✅ 否',
            '冲击详情': r['impact_detail'],
            'VWAP': r['vwap'],
            '现价': r['price'],
            'MA20': r['ma20'],
            '筹码峰顶': r['chip_peak_high'],
            '筹码集中度%': r['chip_peak_ratio'],
            'VWAP突破': '是' if r['vwap_breakthrough'] else '否',
            '筹码峰突破': '是' if r['chip_breakthrough'] else '否',
            '真突破判定': '✅ 真突破' if r['true_breakthrough'] else '❌ 未突破',
            '回踩确认': '✅ 是' if r['pullback_confirm'] else '否',
            '修正后胜率分级': r['grade'],
            '大盘Beta系数K': r['beta_K'],
            '大盘状态': r['market_trend'],
            '推荐买点类型': r['buy_point'],
            'ATR': r['atr'],
            'ATR动态止损价': r['dynamic_stop'],
            'ATR跟踪止盈价': r['trail_stop'],
            '近5日%': r['ret_5d'],
            '近20日%': r['ret_20d'],
            'DoubleScore': r['DoubleScore'],
            '交易决策': r['trade_decision'],
            '核心逻辑': r['核心逻辑'],
        })

    out_df = pd.DataFrame(out_rows)

    grade_order = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
    out_df['_grade_order'] = out_df['修正后胜率分级'].map(grade_order)
    out_df = out_df.sort_values(['_grade_order', '量化择时分'], ascending=[True, False]).reset_index(drop=True)
    out_df = out_df.drop(columns=['_grade_order'])

    # ─── 打印输出 ───
    print('\n' + '='*160)
    print(f'  黑马股增强择时分析 (幻方量化多因子版) - 基于 {latest_csv}')
    print('='*160)
    print(f'  {"排名":>4} {"代码":<12} {"名称":<8} {"行业":<8} {"量化分":>6} {"修正分":>6} {"评级":>3} {"冲击":>4} {"真突破":>6} {"回踩":>4} {"决策":<22} {"止损价":>8}')
    print('  ' + '-'*150)
    for i, r in enumerate(results, 1):
        g = r['grade']
        imp = '⚠️' if r['impact_blocked'] else '--'
        bt = '✅' if r['true_breakthrough'] else '--'
        pc = '✅' if r['pullback_confirm'] else '--'
        stop = f'{r["dynamic_stop"]:.2f}' if r['dynamic_stop'] else 'N/A'
        print(f'  {i:>4} {r["ts_code"]:<12} {r["name"]:<8} {r["industry"]:<8} {r["raw_timing_score"]:>6.1f} {r["corrected_score"]:>6.1f} {g:>3} {imp:>4} {bt:>6} {pc:>4} {r["trade_decision"]:<22} {stop:>8}')

    # ─── 评级汇总 ───
    print('\n' + '='*160)
    print(f'  评级分布:')
    for g in ['S', 'A', 'B', 'C', 'D', 'E']:
        cnt = sum(1 for r in results if r['grade'] == g)
        if cnt > 0:
            print(f'    {g}: {cnt} 家 ({cnt/len(results)*100:.1f}%)')
    print(f'  平均修正分: {np.mean([r["corrected_score"] for r in results]):.1f}')
    print(f'  兑现冲击触发: {sum(1 for r in results if r["impact_blocked"])} 家')
    print(f'  真突破: {sum(1 for r in results if r["true_breakthrough"])} 家')
    print(f'  回踩确认: {sum(1 for r in results if r["pullback_confirm"])} 家')
    print('='*160)

    # ─── S/A级详细 ───
    print('\n' + '='*160)
    print(f'  ★ 极高胜率 - S/A级')
    print('='*160)
    for r in results:
        if r['grade'] in ('S', 'A'):
            print(f'  [{r["grade"]}] {r["name"]:8s} ({r["ts_code"]}) 量化分={r["raw_timing_score"]} 修正分={r["corrected_score"]} K={r["beta_K"]}')
            print(f'        VWAP={r["vwap"]} 现价={r["price"]} 筹码峰顶={r["chip_peak_high"]} MA20={r["ma20"]}')
            print(f'        真突破={r["true_breakthrough"]} 回踩确认={r["pullback_confirm"]} 止损={r["dynamic_stop"]} 决策={r["trade_decision"]}')
            print(f'        逻辑: {r["核心逻辑"]}')
            print()

    # ─── 兑现冲击列表 ───
    impact_stocks = [r for r in results if r['impact_blocked']]
    if impact_stocks:
        print('\n' + '='*160)
        print(f'  ⚠ 兑现冲击触发（强制降级为E）')
        print('='*160)
        for r in impact_stocks:
            print(f'  {r["name"]:8s} ({r["ts_code"]}) 量化分={r["raw_timing_score"]} 冲击详情={r["impact_detail"]}')

    # ─── 保存CSV ───
    csv_out = os.path.join(report_dir, f'enhanced_timing_ds_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
    out_df.to_csv(csv_out, encoding='utf-8-sig', index=False)
    print(f'\n结果已保存: {csv_out}')


if __name__ == '__main__':
    main()