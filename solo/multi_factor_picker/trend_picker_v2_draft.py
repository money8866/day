"""
趋势性上涨选股模型 v2.0（二波确认版）
优化重点：识别二波启动、中小市值偏好、涨停突破不惩罚

基于雅克科技20260610案例的优化：
1. 二波确认机制：首波拉升后回踩确认再启动
2. 市值区间放宽：50-300亿得满分，300-800亿得1.5分
3. 涨停突破加分：涨停日额外+1分（趋势强度信号）
4. RSI惩罚豁免：涨停日豁免RSI过热惩罚
5. 均线支撑：MA20上方+1分，MA60上方+0.5分

数据来源（全部可获取）：
- Tushare daily/daily_basic：价格、成交量、换手率、市值
- Tushare moneyflow：超大单/大单/中单/小单净流入
- Tushare stk_factor_pro：MACD/RSI/KDJ（或本地计算）
- Tushare income：营收/利润增速（财报）
- Tushare top10_holders：机构持股比例
"""

from typing import Tuple, Dict
import pandas as pd
import numpy as np
from loguru import logger

# ════════════════════════════════════════════════════════
# 主线赛道定义
# ════════════════════════════════════════════════════════

TREND_THEMES = {
    "半导体设备": ["北方华创", "中微公司", "拓荆科技", "华海清科", "芯源微"],
    "半导体材料": ["雅克科技", "华特气体", "安集科技", "鼎龙股份", "江丰电子"],
    "存储芯片": ["兆易创新", "北京君正", "澜起科技", "聚辰股份", "普冉股份"],
    "AI算力": ["中科曙光", "浪潮信息", "工业富联", "寒武纪", "海光信息"],
}

STRATEGIC_INDUSTRIES = [
    "半导体", "集成电路", "芯片", "存储", "GPU", "CPU",
    "AI算力", "人工智能", "机器学习", "深度学习",
    "高端制造", "机器人", "工业母机", "数控系统",
    "商业航天", "低空经济", "卫星互联网",
    "国产替代", "自主可控", "信创"
]

# ════════════════════════════════════════════════════════
# 二波确认核心逻辑
# ════════════════════════════════════════════════════════

def detect_wave2_pattern(daily: pd.DataFrame, lookback_days: int = 60) -> Tuple[bool, Dict]:
    """
    二波确认检测（修复版 - 烽火通信案例）
    
    识别逻辑（修复版）：
    1. 首波：过去60天内曾出现涨幅≥8%的启动日（涨停优先）
    2. 回踩：首波后回踩至首波收盘价的85%-95%区间
    3. 二波启动：再次涨停或涨幅≥5%，收盘价突破首波收盘价
    
    烽火通信案例：
    - 首波：5/8涨停（+10%）
    - 回踩：5/15-6/10回踩至47.55（首波收盘57的83.4%）
    - 二波：6/11涨停突破首波高点
    """
    if len(daily) < lookback_days:
        return False, {}
    
    detail = {}
    
    # 找首波启动点（过去60天最大涨幅日，排除最近5天）
    recent = daily.head(lookback_days).iloc[5:]  # 取最近60天，排除最近5天（数据倒序，最新在前）
    if len(recent) == 0:
        return False, detail
    
    # 优先找涨停日作为首波
    limit_up_days = recent[recent['pct_chg'] >= 9.5]
    if len(limit_up_days) > 0:
        wave1_idx = limit_up_days['pct_chg'].idxmax()
    else:
        wave1_idx = recent['pct_chg'].idxmax()
    
    wave1_row = recent.loc[wave1_idx]
    wave1_date = str(wave1_row['trade_date'])
    wave1_pct = float(wave1_row['pct_chg'])
    wave1_close = float(wave1_row['close'])
    
    if wave1_pct < 8:  # 首波不明显
        return False, {'wave1_pct': wave1_pct}
    
    detail['wave1_date'] = wave1_date
    detail['wave1_pct'] = round(wave1_pct, 1)
    detail['wave1_close'] = round(wave1_close, 2)
    
    # 找首波后的回踩最低点（数据倒序，首波后的数据在首波索引之前）
    after_wave1 = daily.loc[:wave1_idx-1]  # 从开头到首波索引之前
    if len(after_wave1) == 0:
        return False, detail
    
    pullback_low = float(after_wave1['low'].min())
    pullback_low_date = str(after_wave1.loc[after_wave1['low'].idxmin(), 'trade_date'])
    
    detail['pullback_low'] = round(pullback_low, 2)
    detail['pullback_low_date'] = pullback_low_date
    
    # 检查回踩幅度（未破首波支撑85%即为有效）
    pullback_ratio = pullback_low / wave1_close
    detail['pullback_ratio'] = round(pullback_ratio, 3)
    
    # 当前是否为二波启动（数据倒序，最新的在第一个）
    latest = daily.iloc[0]
    latest_pct = float(latest['pct_chg'])
    latest_close = float(latest['close'])
    latest_vol = float(latest['vol'])
    latest_date = str(latest['trade_date'])
    
    # 二波启动条件（修复版）
    # 1. 今日涨幅≥5%（涨停最佳）
    if latest_pct < 5:
        detail['note'] = f'涨幅不足5%（{latest_pct:.1f}%）'
        return False, detail
    
    # 2. 收盘价突破首波收盘价（关键修复）
    if latest_close < wave1_close:
        detail['note'] = f'未突破首波（当前{latest_close:.2f} < 首波{wave1_close:.2f}）'
        return False, detail
    
    detail['breakout'] = True
    detail['latest_close'] = round(latest_close, 2)
    
    # 3. 回踩确认（放宽至80%，首波后整理即可）
    if pullback_ratio < 0.80:  # 跌破首波支撑，趋势失效
        detail['trend_broken'] = True
        detail['note'] = '回踩跌破首波支撑80%'
        return False, detail
    
    detail['pullback_valid'] = True
    
    # 4. 放量判断（今日成交量 vs 首波日后5日均值）
    after_wave1_vol = daily.loc[wave1_idx:wave1_idx+5, 'vol']
    if len(after_wave1_vol) > 0:
        wave1_vol_ma = after_wave1_vol.mean()
        vol_ratio = latest_vol / wave1_vol_ma if wave1_vol_ma > 0 else 1.0
        detail['vol_ratio'] = round(vol_ratio, 2)
    else:
        vol_ratio = 1.0
    
    # 二波确认信号
    is_wave2 = (
        latest_pct >= 5 and  # 今日大涨
        latest_close >= wave1_close * 0.98 and  # 突破首波（放宽至98%）
        pullback_ratio >= 0.80  # 回踩未破首波支撑（放宽至80%）
    )
    
    detail['latest_pct'] = round(latest_pct, 1)
    detail['is_wave2'] = is_wave2
    
    return is_wave2, detail


def calc_wave2_score(daily: pd.DataFrame, is_wave2: bool, detail: Dict) -> float:
    """
    二波确认加分
    
    雅克科技6月10日案例：
    - 首波：5月某日 +8%
    - 回踩：回调至首波收盘价的90%
    - 二波：6月10日 +10%，量比2.1
    
    加分规则：
    - 二波确认基础分：+2分
    - 涨停二波：+1分（涨停=强趋势）
    - 缩量回踩后放量：+0.5分
    """
    if not is_wave2:
        return 0.0
    
    score = 2.0  # 基础分
    
    # 涨停二波加分
    if detail.get('latest_pct', 0) >= 9.5:
        score += 1.0
    
    # 缩量回踩后放量
    vol_ratio = detail.get('vol_ratio', 0)
    if vol_ratio >= 2.0:  # 放量2倍以上
        score += 0.5
    
    return min(score, 3.0)  # 上限3分


# ════════════════════════════════════════════════════════
# 优化后的因子评分
# ════════════════════════════════════════════════════════

def score_fundamental_v2(fetcher, ts_code, industry, income, daily_basic, daily) -> Tuple[float, Dict]:
    """
    基本面因子 v2（优化市值区间）
    
    F3市值区间：
    - 50-300亿：2分（满分，最佳趋势启动区间）
    - 300-800亿：1.5分（次选，雅克科技属于此类）
    - 800-2000亿：1分
    - <50亿或>2000亿：0分
    
    雅克科技（390亿）：
    - v1得分：1分
    - v2得分：1.5分（提升0.5分）
    """
    score = 0.0
    detail = {'F1': {}, 'F2': {}, 'F3': {}}
    
    # F1: 赛道属性（不变）
    f1_score = 0.0
    is_strategic = any(si in industry for si in STRATEGIC_INDUSTRIES)
    if is_strategic:
        f1_score += 1.0
        detail['F1']['strategic_industry'] = True
    
    for theme, members in TREND_THEMES.items():
        if ts_code in [m + '.SH' if m.startswith('6') else m + '.SZ' for m in members]:
            f1_score += 0.5
            detail['F1']['theme'] = theme
            break
    
    f1_score = min(f1_score, 2.0)
    score += f1_score * 0.75
    detail['F1']['score'] = round(f1_score, 2)
    
    # F2: 业绩拐点（不变）
    f2_score = 0.0
    if len(income) >= 2:
        curr = income.iloc[0]
        prev = income.iloc[1]
        
        curr_rev = curr.get('revenue', 0) or 0
        prev_rev = prev.get('revenue', 0) or 0
        if prev_rev > 0:
            rev_yoy = (curr_rev - prev_rev) / prev_rev
            if rev_yoy > 0.2:
                f2_score += 1.0
                detail['F2']['revenue_yoy'] = round(rev_yoy * 100, 1)
        
        curr_gp = curr.get('gross_profit', 0) or 0
        if curr_rev > 0 and prev_rev > 0:
            curr_gm = curr_gp / curr_rev
            prev_gp = prev.get('gross_profit', 0) or 0
            prev_gm = prev_gp / prev_rev
            if curr_gm >= prev_gm:
                f2_score += 0.5
                detail['F2']['gross_margin'] = round(curr_gm * 100, 1)
        
        if len(daily_basic) > 0:
            pe = daily_basic.iloc[0].get('pe', 0) or 0
            if 0 < pe < 50:
                f2_score += 0.5
                detail['F2']['pe'] = round(pe, 1)
    
    f2_score = min(f2_score, 2.0)
    score += f2_score * 0.75
    detail['F2']['score'] = round(f2_score, 2)
    
    # F3: 市值区间（优化）
    f3_score = 0.0
    if len(daily_basic) > 0:
        circ_mv = daily_basic.iloc[0].get('circ_mv', 0) or 0
        if circ_mv > 0:
            circ_mv_yi = circ_mv / 10000
            detail['F3']['circ_mv'] = round(circ_mv_yi, 1)
            
            if 50 <= circ_mv_yi <= 300:  # 最佳区间
                f3_score = 2.0
            elif 300 < circ_mv_yi <= 800:  # 次选区间（雅克科技）
                f3_score = 1.5
            elif 800 < circ_mv_yi <= 2000:
                f3_score = 1.0
            # 其他情况0分
    
    score += f3_score * 0.5
    detail['F3']['score'] = round(f3_score, 2)
    
    return round(score, 2), detail


def score_technical_v2(daily: pd.DataFrame, is_wave2: bool = False) -> Tuple[float, Dict]:
    """
    技术面因子 v2（修复版 - 涨停日换手率豁免）
    
    修复点：
    1. 涨停日豁免RSI过热惩罚
    2. 涨停日豁免换手率过热惩罚（F6）
    3. 新增二波确认加分（最高3分）
    """
    score = 0.0
    detail = {'F6': {}, 'F7': {}, 'F8': {}, 'F9': {}, 'WAVE2': {}}
    
    if len(daily) < 30:
        return 0.0, detail
    
    close = daily['close']
    latest_close = float(close.iloc[-1])
    latest_pct = float(daily.iloc[-1]['pct_chg'])
    latest_turnover = float(daily.iloc[-1].get('turnover_rate', 0) or 0)
    is_limit_up = latest_pct >= 9.4  # 涨停判断（修复：覆盖涨停和接近涨停）
    
    # F6: 换手率（新增，修复涨停日误判）
    f6_score = 0.0
    if is_limit_up:
        # 涨停日：高换手率是正常的（主力资金进场）
        if latest_turnover >= 8:
            f6_score = 2.0
            detail['F6']['turnover_rate'] = round(latest_turnover, 1)
            detail['F6']['note'] = '涨停启动日充分换手'
        elif latest_turnover >= 5:
            f6_score = 1.5
            detail['F6']['turnover_rate'] = round(latest_turnover, 1)
            detail['F6']['note'] = '涨停启动日适中换手'
        else:
            f6_score = 1.0
            detail['F6']['turnover_rate'] = round(latest_turnover, 1)
            detail['F6']['note'] = '缩量涨停（锁仓）'
    else:
        # 非涨停日：正常换手率判断
        if latest_turnover > 10:
            f6_score = 0.5
            detail['F6']['turnover_rate'] = round(latest_turnover, 1)
            detail['F6']['note'] = '过热警告'
        elif 5 <= latest_turnover <= 10:
            f6_score = 1.0
            detail['F6']['turnover_rate'] = round(latest_turnover, 1)
            detail['F6']['note'] = '换手适中'
        else:
            f6_score = 0.5
            detail['F6']['turnover_rate'] = round(latest_turnover, 1)
            detail['F6']['note'] = '换手偏低'
    
    score += f6_score * 0.5  # 权重10%
    detail['F6']['score'] = round(f6_score, 2)
    
    # F7-F9沿用原逻辑
    
    # F7: 均线系统（不变）
    f7_score = 0.0
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    
    latest_ma5 = float(ma5.iloc[-1])
    latest_ma10 = float(ma10.iloc[-1])
    latest_ma20 = float(ma20.iloc[-1])
    latest_ma60 = float(ma60.iloc[-1]) if len(ma60) > 0 and not pd.isna(ma60.iloc[-1]) else 0
    
    # 多头排列
    if latest_ma5 > latest_ma10 > latest_ma20:
        f7_score += 2.0
        detail['F7']['alignment'] = 'bullish'
    elif latest_close > latest_ma20:
        f7_score += 1.0
        detail['F7']['alignment'] = 'above_ma20'
    
    # 均线支撑加分
    if latest_close > latest_ma20 * 0.98:  # MA20支撑
        f7_score += 0.5
        detail['F7']['ma20_support'] = True
    
    if latest_ma60 > 0 and latest_close > latest_ma60:  # MA60支撑
        f7_score += 0.5
        detail['F7']['ma60_support'] = True
    
    f7_score = min(f7_score, 3.0)  # 上限提升至3分
    score += f7_score * 0.4  # 权重调整
    detail['F7']['score'] = round(f7_score, 2)
    
    # F8: 成交量（修复版 - 涨停日特殊处理）
    f8_score = 0.0
    vol = daily['vol']
    vol_ma5 = vol.rolling(5).mean()
    
    latest_vol = float(vol.iloc[-1])
    latest_vol_ma5 = float(vol_ma5.iloc[-1])
    
    # 获取换手率
    latest_turnover = float(daily.iloc[-1].get('turnover_rate', 0) or 0)
    
    if is_limit_up:
        # 涨停日：用换手率判断（量比失真）
        if latest_turnover >= 8:
            f8_score = 2.0
            detail['F8']['turnover_rate'] = round(latest_turnover, 1)
            detail['F8']['note'] = '涨停换手率达标（≥8%）'
        elif latest_turnover >= 5:
            f8_score = 1.5
            detail['F8']['turnover_rate'] = round(latest_turnover, 1)
            detail['F8']['note'] = '涨停换手率适中（5-8%）'
        else:
            f8_score = 1.0
            detail['F8']['turnover_rate'] = round(latest_turnover, 1)
            detail['F8']['note'] = '缩量涨停（锁仓）'
    else:
        # 非涨停日：用量比判断
        if latest_vol_ma5 > 0:
            vol_ratio = latest_vol / latest_vol_ma5
            if vol_ratio >= 2.0:
                f8_score = 2.0
                detail['F8']['volume_ratio'] = round(vol_ratio, 2)
            elif vol_ratio >= 1.5:
                f8_score = 1.0
                detail['F8']['volume_ratio'] = round(vol_ratio, 2)
    
    f8_score = min(f8_score, 2.0)
    score += f8_score * 0.25
    detail['F8']['score'] = round(f8_score, 2)
    
    # F9: 技术指标（涨停豁免惩罚）
    f9_score = 0.0
    
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(6).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
    rs = gain / loss.replace(0, 0.0001)
    rsi6 = 100 - (100 / (1 + rs))
    latest_rsi6 = float(rsi6.iloc[-1])
    
    detail['F9']['rsi6'] = round(latest_rsi6, 1)
    
    if not is_limit_up:  # 非涨停才检查RSI过热
        if latest_rsi6 > 80:
            detail['F9']['overbought'] = True
            f9_score = 0.0
        elif 50 <= latest_rsi6 <= 70:
            f9_score += 1.0
    else:  # 涨停日豁免惩罚
        f9_score += 1.0
        detail['F9']['limit_up_exempt'] = True
    
    # MACD金叉
    if len(ma5) > 10:
        if latest_ma5 > latest_ma10 and float(ma5.iloc[-2]) <= float(ma10.iloc[-2]):
            f9_score += 1.0
            detail['F9']['macd_cross'] = 'golden'
    
    f9_score = min(f9_score, 2.0)
    score += f9_score * 0.25
    detail['F9']['score'] = round(f9_score, 2)
    
    # WAVE2: 二波确认加分
    wave2_score = 0.0
    if is_wave2:
        wave2_score = 2.0
        if is_limit_up:
            wave2_score += 1.0
        detail['WAVE2']['confirmed'] = True
        detail['WAVE2']['score'] = round(wave2_score, 2)
    
    score += wave2_score
    detail['WAVE2']['score'] = round(wave2_score, 2)
    
    return round(score, 2), detail


# ════════════════════════════════════════════════════════
# v2 评分主函数
# ════════════════════════════════════════════════════════

def trend_scan_v2(fetcher, stocks, start_date, end_date):
    """
    趋势扫描 v2（二波确认版）
    
    总分上限：22分
    - 基本面：7.2分
    - 资金面：8.1分
    - 技术面：4.0分（含二波加分）
    """
    results = []
    
    for idx, row in stocks.iterrows():
        ts_code = row['ts_code']
        name = row.get('name', '')
        industry = row.get('industry', '')
        
        try:
            daily = get_daily_data(fetcher, ts_code, start_date, end_date)
            if len(daily) < 30:
                continue
            
            # 二波模式检测
            is_wave2, wave2_detail = detect_wave2_pattern(daily)
            
            moneyflow = get_moneyflow_data(fetcher, ts_code, start_date, end_date)
            daily_basic = get_daily_basic(fetcher, ts_code, end_date)
            income = fetcher.get_income(ts_code)
            
            # 三因子评分（v2）
            fund_score, fund_detail = score_fundamental_v2(fetcher, ts_code, industry, income, daily_basic, daily)
            cap_score, cap_detail = score_capital(fetcher, ts_code, moneyflow, daily)
            tech_score, tech_detail = score_technical_v2(daily, is_wave2)
            
            total_score = fund_score + cap_score + tech_score
            normalized = round(total_score / 22.0 * 100, 1)  # 上限22分
            
            # 趋势强度判断
            if total_score >= 14:
                trend_status = 'strong'
            elif total_score >= 10:
                trend_status = 'moderate'
            elif total_score >= 7:
                trend_status = 'weak'
            else:
                trend_status = 'terminated'
            
            # 买点识别（二波优先）
            buy_signal, stop_loss = identify_buy_signal_v2(daily, is_wave2, total_score)
            
            result = TrendResult(
                ts_code=ts_code,
                name=name,
                industry=industry,
                fundamental_score=fund_score,
                capital_score=cap_score,
                technical_score=tech_score,
                total_score=total_score,
                normalized_score=normalized,
                trend_status=trend_status,
                buy_signal=buy_signal,
                stop_loss_price=stop_loss,
                factors={**fund_detail, **cap_detail, **tech_detail},
                raw_data={'latest_close': float(daily.iloc[-1]['close']), 'latest_pct': float(daily.iloc[-1]['pct_chg'])}
            )
            
            if total_score >= 7:
                results.append(result)
        
        except Exception as e:
            logger.debug(f"扫描失败 {ts_code}: {e}")
        
        time.sleep(0.05)
    
    results.sort(key=lambda x: x.total_score, reverse=True)
    return results


def identify_buy_signal_v2(daily, is_wave2, total_score):
    """
    买点识别 v2（二波优先）
    
    A点：二波确认买点（最强）
    B点：均线回踩买点
    C点：突破前高买点
    """
    if len(daily) < 20 or total_score < 10:
        return "", 0.0
    
    close = daily['close']
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    
    latest_close = float(close.iloc[-1])
    latest_ma10 = float(ma10.iloc[-1])
    latest_ma20 = float(ma20.iloc[-1])
    
    # A点：二波确认买点
    if is_wave2 and total_score >= 14:
        stop_loss = round(latest_ma10 * 0.95, 2)
        return "A", stop_loss
    
    # B点：均线回踩
    if latest_close <= latest_ma10 * 1.02 and latest_close > latest_ma20:
        vol = daily['vol']
        recent_vol = vol.iloc[-3:].mean()
        launch_vol = vol.iloc[-10:-5].mean()
        if recent_vol < launch_vol * 0.7:
            stop_loss = round(latest_ma20 * 0.98, 2)
            return "B", stop_loss
    
    # C点：突破前高
    prev_high = close.iloc[-30:-5].max()
    if latest_close > prev_high and total_score >= 12:
        stop_loss = round(latest_ma10 * 0.95, 2)
        return "C", stop_loss
    
    return "", 0.0
