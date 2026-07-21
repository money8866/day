"""
57只DoubleScore通过股票的增强择时分析
基于四大核心优化规则：
1. 业绩预告"兑现冲击"事件过滤器（防利好诱多接盘）
2. VWAP + 筹码密集区突破机制（真突破判定）
3. ATR动态波动率止损与移动止盈
4. 行业/大盘Beta环境变量调节
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

        # 检查条件1：跳空>5% 且 量比>1.5（近似换手率>15%）
        if gap_pct > 5 and turnover > 2.0:
            return {
                'impact': True,
                'detail': f'T+{offset} 跳空{gap_pct:.1f}%+巨量({turnover:.1f}x)',
                'ann_date': ann_date,
                'impact_date': trade_date_str,
            }
        # 条件2：收阴线且成交量>20日均量2倍
        if is_negative and turnover > 2.0:
            return {
                'impact': True,
                'detail': f'T+{offset} 阴线放量({turnover:.1f}x)',
                'ann_date': ann_date,
                'impact_date': trade_date_str,
            }

    return {'impact': False, 'detail': '无明显兑现冲击'}


def analyze_enhanced_timing(ts_code: str, name: str, industry: str,
                            forecast_ann_date: str, forecast_profit_yoy: float,
                            fetcher: DataFetcher) -> dict:
    """增强择时分析主函数"""
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')

    daily = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)
    if daily is None or len(daily) < 30:
        return None

    daily = daily.sort_values('trade_date').reset_index(drop=True)
    closes = daily['close'].values.astype(float)
    highs = daily['high'].values.astype(float)
    lows = daily['low'].values.astype(float)
    opens = daily['open'].values.astype(float)
    vols = daily['vol'].values.astype(float)
    amounts = daily['amount'].values.astype(float)
    price = float(closes[-1])

    # ─── 规则1：兑现冲击过滤器 ───
    impact = _check_forecast_impact(ts_code, forecast_ann_date, fetcher)
    impact_blocked = impact['impact']

    # ─── 规则2：VWAP + 筹码峰突破 ───
    vwap = _calc_vwap(daily, 20)
    peak_low, peak_high, peak_ratio = _calc_chip_concentration_peak(daily, 60)

    # 突破状态判断
    vwap_breakthrough = vwap is not None and price > vwap
    chip_breakthrough = peak_low is not None and price > peak_high
    true_breakthrough = vwap_breakthrough and chip_breakthrough

    # 回踩确认判断（买点2）：股价曾突破后缩量回踩VWAP不破
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
    if atr and atr > 0:
        dynamic_stop = price - 2.0 * atr
        trail_stop = price - 3.0 * atr
    else:
        dynamic_stop = None
        trail_stop = None

    # ─── 规则4：行业Beta环境 ───
    beta = _calc_market_beta(industry, fetcher)
    K = 1.2 if beta['above_ma20'] else 0.6

    # ─── 原始技术评分（复用基础timing评分） ───
    # 基础均线趋势
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

    # 资金流向
    try:
        mf = fetcher.get_moneyflow_by_code(ts_code, start_date=start_date, end_date=end_date)
        mf_score = 0
        if mf is not None and len(mf) > 0:
            mf = mf.sort_values('trade_date').reset_index(drop=True)
            net_cols = [c for c in ['net_mf_amount', 'net_mf_vol', 'buy_elg_vol', 'sell_elg_vol'] if c in mf.columns]
            if net_cols:
                if 'net_mf_amount' in mf.columns:
                    net_amount = float(mf['net_mf_amount'].iloc[-5:].sum())
                    mf_score = 15 if net_amount > 0 else -15
                elif 'net_mf_vol' in mf.columns:
                    net_vol = float(mf['net_mf_vol'].iloc[-5:].sum())
                    mf_score = 15 if net_vol > 0 else -15
    except Exception:
        mf_score = 0

    # 近5日涨跌
    ret_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    ret_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0
    if ret_5d > 5:
        base_score += 5
    elif ret_5d < -5:
        base_score -= 5
    base_score += mf_score

    raw_timing_score = max(0, min(100, base_score))

    # ─── 修正后TimingScore ───
    if impact_blocked:
        # 兑现冲击：强制降级，无论原始评分多高
        corrected_score = min(raw_timing_score * 0.3, 35)
        grade = 'E'
        trade_decision = '观望-利好兑现风险'
    else:
        corrected_score = raw_timing_score * K
        corrected_score = max(0, min(100, corrected_score))

        # 胜率分级
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

    # 买点类型
    if true_breakthrough and pullback_confirm:
        buy_point = '买点2(缩量回踩VWAP确认)'
    elif true_breakthrough:
        buy_point = '买点1(放量突破VWAP+筹码峰)'
    else:
        buy_point = '未突破'

    # 趋势标签
    if raw_timing_score >= 75:
        trend = '多头趋势'
    elif raw_timing_score >= 55:
        trend = '震荡偏多'
    elif raw_timing_score >= 40:
        trend = '震荡整理'
    else:
        trend = '空头趋势'

    return {
        'ts_code': ts_code,
        'name': name,
        'industry': industry,
        'forecast_ann_date': forecast_ann_date,
        'forecast_profit_yoy': forecast_profit_yoy,
        'raw_timing_score': raw_timing_score,
        'corrected_score': round(corrected_score, 1),
        'grade': grade,
        'trend': trend,
        'impact_blocked': impact_blocked,
        'impact_detail': impact['detail'],
        'vwap': round(vwap, 2) if vwap else None,
        'price': round(price, 2),
        'ma20': round(ma20_, 2) if ma20_ else None,
        'ma60': round(ma60, 2) if ma60 else None,
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
        'market_ma20': round(beta['ma20'], 2) if beta['ma20'] else None,
        'buy_point': buy_point,
        'trade_decision': trade_decision,
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

    # 读取bull_stocks_all.csv获取行业和预告日期
    bull_csv = os.path.join(report_dir, 'bull_stocks_all.csv')
    bull_df = None
    if os.path.exists(bull_csv):
        bull_df = pd.read_csv(bull_csv, encoding='utf-8-sig')

    config = load_config()
    token = get_token(config)
    fetcher = DataFetcher(token, config)

    logger.info(f"开始对 {len(ds_df)} 只股票进行增强择时分析...")
    results = []
    for i, (_, row) in enumerate(ds_df.iterrows(), 1):
        code_raw = str(row['代码']).strip()
        name = row['名称']
        code_padded = code_raw.zfill(6)
        ts_code = code_padded + ('.SH' if code_padded.startswith('6') or code_padded.startswith('9') else '.SZ')

        # 从bull_df获取行业和预告日期
        industry = ''
        forecast_ann_date = ''
        forecast_profit_yoy = 0
        if bull_df is not None:
            match = bull_df[bull_df['code'].astype(str).str.strip() == code_raw]
            if len(match) > 0:
                m = match.iloc[0]
                industry = str(m.get('industry', ''))
                # 预告发布日期来自 forecast_vip，但bull_csv里没有存储
                # 我们通过forecast_ann_date字段查找，但bull_csv没有这个列
                # 从预告变动%反推预告日期（用forecast_vip API单独获取）
                forecast_profit_yoy = float(m.get('利润同比', 0))

        # 获取预告发布日期（从forecast_vip API）
        try:
            forecast_vip = fetcher.get_forecast_vip('20260630')
            if forecast_vip is not None and len(forecast_vip) > 0:
                fv = forecast_vip[forecast_vip['ts_code'] == ts_code]
                if len(fv) > 0:
                    latest_f = fv.sort_values('ann_date', ascending=False).iloc[0]
                    forecast_ann_date = str(latest_f.get('ann_date', ''))
        except Exception:
            pass

        logger.info(f"[{i}/{len(ds_df)}] {name} ({ts_code}) 行业={industry}")
        result = analyze_enhanced_timing(ts_code, name, industry, forecast_ann_date, forecast_profit_yoy, fetcher)
        if result is None:
            logger.warning(f"  {name}: 数据不足，跳过")
            continue
        result['DoubleScore'] = row.get('DoubleScore', 0)
        result['核心逻辑'] = row.get('核心逻辑', '')
        results.append(result)

        grade_str = result['grade']
        impact_str = '⚠️ 兑现冲击' if result['impact_blocked'] else ''
        bt_str = '✅ 真突破' if result['true_breakthrough'] else ''
        pc_str = '✅ 回踩确认' if result['pullback_confirm'] else ''
        logger.info(f"  → 原始分={result['raw_timing_score']} K={result['beta_K']} 修正分={result['corrected_score']}/{grade_str} {impact_str} {bt_str} {pc_str} 决策={result['trade_decision']}")

    if not results:
        logger.error("没有分析出有效结果")
        return

    # 构建输出DataFrame
    out_rows = []
    for r in results:
        out_rows.append({
            '代码': r['ts_code'],
            '名称': r['name'],
            '行业': r['industry'],
            '中报业绩亮点': f"{r['forecast_profit_yoy']:.1f}%",
            '原始Timing分': r['raw_timing_score'],
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

    # 按修正后评分排序
    grade_order = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
    out_df['_grade_order'] = out_df['修正后胜率分级'].map(grade_order)
    out_df = out_df.sort_values(['_grade_order', '原始Timing分'], ascending=[True, False]).reset_index(drop=True)
    out_df = out_df.drop(columns=['_grade_order'])

    # ─── 打印输出 ───
    print('\n' + '='*160)
    print(f'  57只黑马股增强择时分析 (基于 {latest_csv})')
    print('='*160)
    print(f'  {"排名":>4} {"代码":<12} {"名称":<8} {"行业":<8} {"原始分":>5} {"修正分":>5} {"评级":>3} {"冲击":>4} {"真突破":>6} {"回踩":>4} {"决策":<22} {"止损价":>8}')
    print('  ' + '-'*150)
    for i, r in enumerate(results, 1):
        g = r['grade']
        imp = '⚠️' if r['impact_blocked'] else '--'
        bt = '✅' if r['true_breakthrough'] else '--'
        pc = '✅' if r['pullback_confirm'] else '--'
        stop = f'{r["dynamic_stop"]:.2f}' if r['dynamic_stop'] else 'N/A'
        print(f'  {i:>4} {r["ts_code"]:<12} {r["name"]:<8} {r["industry"]:<8} {r["raw_timing_score"]:>5} {r["corrected_score"]:>5} {g:>3} {imp:>4} {bt:>6} {pc:>4} {r["trade_decision"]:<22} {stop:>8}')

    # ─── 评级汇总 ───
    print('\n' + '='*160)
    print(f'  评级分布:')
    for g in ['S', 'A', 'B', 'C', 'D', 'E']:
        cnt = sum(1 for r in results if r['grade'] == g)
        if cnt > 0:
            print(f'    {g}: {cnt} 家')
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
            print(f'  [{r["grade"]}] {r["name"]:8s} ({r["ts_code"]}) 原始分={r["raw_timing_score"]} 修正分={r["corrected_score"]} K={r["beta_K"]}')
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
            print(f'  {r["name"]:8s} ({r["ts_code"]}) 原始分={r["raw_timing_score"]} 冲击详情={r["impact_detail"]} 原决策={r["trade_decision"]}')

    # ─── 保存CSV ───
    csv_out = os.path.join(report_dir, f'enhanced_timing_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
    out_df.to_csv(csv_out, encoding='utf-8-sig', index=False)
    print(f'\n结果已保存: {csv_out}')


if __name__ == '__main__':
    main()