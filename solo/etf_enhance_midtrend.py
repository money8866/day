# -*- coding: utf-8 -*-
"""
强势ETF成份股增强策略 - 中线趋势版

核心逻辑：
1. 筛选中线趋势强势ETF（排除短线爆发）
2. 获取成份股并精选
3. 输出增强池

中线趋势判定条件：
- 20日均线 > 60日均线（均线多头）
- 60日均线向上（趋势持续）
- 20日涨幅 10%~50%（排除短线爆发，不追高）
- 60日涨幅 20%~150%（确认中线趋势）
- 价格偏离60日均线 < 40%（不追高）
- 量比 > 0.8（量价配合）
"""
import os
import sys
import time
import datetime
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = os.path.dirname(BASE_DIR)
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

load_dotenv(os.path.join(STOCK_DATA_DIR, "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
os.makedirs(CACHE_DIR, exist_ok=True)


def get_last_trade_date():
    now = datetime.datetime.now()
    if now.hour < 15:
        query_date = (now - datetime.timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)

# ETF池（行业主题ETF，排除指数型）
ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159883', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159611', '电网设备': '561380', '消费': '159928',
    '食品饮料': '159736', '酒': '512690', '家电': '159996',
    '证券': '512880', '银行': '512800', '红利': '515180',
    '黄金': '518880',
}


def get_etf_suffix(ts_code):
    if ts_code.startswith('1') or ts_code.startswith('15'):
        return ts_code + '.SZ'
    else:
        return ts_code + '.SH'


def get_etf_data(ts_code):
    full_code = get_etf_suffix(ts_code)
    cache_file = os.path.join(CACHE_DIR, f"etf_{ts_code}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if len(df) > 60 and (df['trade_date'] == TRADE_DATE).any():
                return df.sort_values('trade_date')
        except:
            pass
    try:
        time.sleep(0.12)
        df = pro.fund_daily(ts_code=full_code, start_date='20250101', end_date=TRADE_DATE)
        if not df.empty:
            df = df.sort_values('trade_date')
            df.to_csv(cache_file, index=False)
            return df
    except Exception as e:
        print(f"  [WARN] 获取{full_code}数据失败: {e}")
    return None


def get_etf_constituents(ts_code):
    full_code = get_etf_suffix(ts_code)
    prefix = ts_code[0]
    try:
        time.sleep(0.12)
        if prefix == '1':
            df = pro.etf_sz_cons(
                ts_code=full_code,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
        else:
            df = pro.etf_sh_cons(
                ts_code=full_code,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
        if df is None or df.empty:
            return []
        latest_date = df['trade_date'].max()
        df = df[df['trade_date'] == latest_date]
        return df.to_dict('records')
    except Exception as e:
        print(f"  [WARN] 获取{full_code}成份股失败: {e}")
        return []


def calc_etf_trend(df):
    """
    计算ETF中线趋势指标
    """
    if df is None or len(df) < 60:
        return None

    df = df.copy()
    df = df.sort_values('trade_date').tail(120)

    # 计算均线
    for ma in [5, 10, 20, 60]:
        df[f'ma{ma}'] = df['close'].rolling(ma).mean()

    # 计算涨幅
    df['pct5'] = (df['close'] / df['close'].shift(5) - 1) * 100
    df['pct10'] = (df['close'] / df['close'].shift(10) - 1) * 100
    df['pct20'] = (df['close'] / df['close'].shift(20) - 1) * 100
    df['pct60'] = (df['close'] / df['close'].shift(60) - 1) * 100

    # 计算均线斜率
    df['ma20_slope'] = (df['ma20'] / df['ma20'].shift(10) - 1) * 100
    df['ma60_slope'] = (df['ma60'] / df['ma60'].shift(10) - 1) * 100

    # 计算成交量均线
    df['vol5'] = df['vol'].rolling(5).mean()
    df['vol20'] = df['vol'].rolling(20).mean()
    df['vol_ratio'] = df['vol5'] / df['vol20']

    # 偏离度
    df['deviation60'] = (df['close'] / df['ma60'] - 1) * 100

    # 取最新数据
    latest = df.iloc[-1]

    return {
        'close': latest['close'],
        'ma5': latest['ma5'],
        'ma10': latest['ma10'],
        'ma20': latest['ma20'],
        'ma60': latest['ma60'],
        'pct5': latest['pct5'],
        'pct10': latest['pct10'],
        'pct20': latest['pct20'],
        'pct60': latest['pct60'],
        'ma20_slope': latest['ma20_slope'],
        'ma60_slope': latest['ma60_slope'],
        'vol_ratio': latest['vol_ratio'],
        'deviation60': latest['deviation60'],
        'volume': latest['vol'],
    }


def is_mid_trend(trend):
    """
    判断是否为中线趋势ETF（非短线爆发）
    """
    if trend is None:
        return False, "数据不足"

    reasons = []
    passed = True

    # 条件1: 均线多头排列
    if trend['ma20'] <= trend['ma60']:
        passed = False
        reasons.append(f"均线空头(MA20={trend['ma20']:.2f}<MA60={trend['ma60']:.2f})")

    # 条件2: 60日均线向上
    if trend['ma60_slope'] <= 0:
        passed = False
        reasons.append(f"MA60向下(斜率={trend['ma60_slope']:.2f}%)")

    # 条件3: 20日涨幅 10%~50%（排除短线爆发）
    pct20 = trend['pct20']
    if pct20 < 10:
        passed = False
        reasons.append(f"20日涨幅不足({pct20:.1f}%<10%)")
    elif pct20 > 50:
        passed = False
        reasons.append(f"短线爆发嫌疑(20日{+pct20:.1f}%>50%)")

    # 条件4: 60日涨幅 20%~150%
    pct60 = trend['pct60']
    if pct60 < 20:
        passed = False
        reasons.append(f"60日涨幅不足({pct60:.1f}%<20%)")
    elif pct60 > 150:
        passed = False
        reasons.append(f"60日涨幅过大({+pct60:.1f}%>150%)")

    # 条件5: 偏离度 < 40%
    dev60 = abs(trend['deviation60'])
    if dev60 > 40:
        passed = False
        reasons.append(f"偏离MA60过大({dev60:.1f}%>40%)")

    # 条件6: 量比 > 0.8
    if trend['vol_ratio'] < 0.8:
        passed = False
        reasons.append(f"量能萎缩(VOL比={trend['vol_ratio']:.2f}<0.8)")

    return passed, "; ".join(reasons) if reasons else "通过中线趋势检验"


def calculate_etf_score(trend):
    """
    计算ETF中线趋势评分 (0-100)
    """
    if trend is None:
        return 0

    score = 0

    # 均线多头程度 (0-25)
    ma_gap = (trend['ma20'] / trend['ma60'] - 1) * 100
    score += min(25, max(0, ma_gap * 2))

    # 趋势强度 - 60日涨幅 (0-25)
    pct60 = trend['pct60']
    score += min(25, max(0, (pct60 - 20) / 5))

    # 趋势持续性 - MA60斜率 (0-25)
    slope = trend['ma60_slope']
    score += min(25, max(0, slope * 10))

    # 稳定性 - 偏离度控制 (0-25)
    dev60 = abs(trend['deviation60'])
    if dev60 < 10:
        score += 25
    elif dev60 < 20:
        score += 20
    elif dev60 < 30:
        score += 15
    elif dev60 < 40:
        score += 10

    return min(100, max(0, score))


def get_stock_daily(ts_code):
    cache_file = os.path.join(CACHE_DIR, f"stock_{ts_code.replace('.', '_')}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if len(df) > 30 and (df['trade_date'] == TRADE_DATE).any():
                return df.sort_values('trade_date')
        except:
            pass
    try:
        time.sleep(0.12)
        df = pro.daily(ts_code=ts_code, start_date='20250101', end_date=TRADE_DATE)
        if not df.empty:
            df = df.sort_values('trade_date')
            df.to_csv(cache_file, index=False)
            return df
    except Exception as e:
        print(f"  [WARN] 获取{ts_code}日线失败: {e}")
    return None


def calc_uptrend_pullback(df):
    """
    计算上升趋势中的回调信号评分 (0-100)
    
    核心逻辑：
    1. 上升趋势确认：均线多头排列
    2. 回调幅度适中：从高点回落5%-15%
    3. 回调至关键均线：MA10/MA20附近
    4. 缩量回调：抛压较小
    5. 企稳迹象：下影线、小阳线、KDJ低位等
    """
    if df is None or len(df) < 40:
        return None

    df = df.copy()
    df = df.sort_values('trade_date').reset_index(drop=True)
    n = len(df)

    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    vol = df['vol'].values
    pct_chg = df['pct_chg'].values

    # 计算均线
    ma5 = pd.Series(close).rolling(5).mean().values
    ma10 = pd.Series(close).rolling(10).mean().values
    ma20 = pd.Series(close).rolling(20).mean().values
    ma60 = pd.Series(close).rolling(60).mean().values

    # 计算成交量均线
    vol5 = pd.Series(vol).rolling(5).mean().values
    vol20 = pd.Series(vol).rolling(20).mean().values

    # KDJ
    low9 = pd.Series(low).rolling(9).min().values
    high9 = pd.Series(high).rolling(9).max().values
    rsv = (close - low9) / (high9 - low9 + 0.0001) * 100
    k = pd.Series(rsv).ewm(com=2, adjust=False).mean().values
    d = pd.Series(k).ewm(com=2, adjust=False).mean().values
    j = 3 * k - 2 * d

    # RSI
    delta = pd.Series(pct_chg).values
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14).mean().values
    avg_loss = pd.Series(loss).rolling(14).mean().values
    rs = avg_gain / (avg_loss + 0.0001)
    rsi = 100 - (100 / (1 + rs))

    latest = n - 1

    # 近期高点（近20日最高收盘价）
    recent_high_idx = n - 20 + np.argmax(close[n-20:n])
    recent_high = close[recent_high_idx]
    days_from_high = latest - recent_high_idx

    score = 0
    signals = []

    # === 1. 上升趋势确认 (0-25分) ===
    # 均线多头排列
    if ma20[latest] > ma60[latest]:
        score += 10
        signals.append("MA20>MA60(趋势向上)")
    else:
        # 没有上升趋势直接低分
        return {
            'score': 10,
            'signals': ['非上升趋势'],
            'pullback_pct': 0,
            'days_from_high': 0,
            'pos_ma20': 0,
            'vol_ratio': 0,
            'rsi': rsi[latest] if not np.isnan(rsi[latest]) else 50,
            'kdj_j': j[latest],
            'trend_score': 0,
        }

    # MA10 > MA20
    if ma10[latest] > ma20[latest]:
        score += 8
        signals.append("MA10>MA20(短期强势)")

    # MA60向上
    ma60_slope = (ma60[latest] / ma60[latest-10] - 1) * 100
    if ma60_slope > 0.5:
        score += 7
        signals.append(f"MA60向上({ma60_slope:+.1f}%)")

    trend_score = score

    # === 2. 回调幅度适中 (0-25分) ===
    pullback_pct = (close[latest] / recent_high - 1) * 100

    if -15 <= pullback_pct <= -3:
        score += 15
        signals.append(f"回调{abs(pullback_pct):.1f}%(幅度适中)")
    elif -20 <= pullback_pct < -15:
        score += 8
        signals.append(f"回调{abs(pullback_pct):.1f}%(偏深)")
    elif -3 < pullback_pct < 0:
        score += 5
        signals.append(f"回调{abs(pullback_pct):.1f}%(刚启动)")
    elif pullback_pct < -20:
        score += 0
        signals.append(f"回调{abs(pullback_pct):.1f}%(过深)")
    elif pullback_pct >= 0:
        score += 3
        signals.append("创新高(未回调)")

    # 回调天数（3-10天最佳）
    if 3 <= days_from_high <= 10:
        score += 10
        signals.append(f"回调{days_from_high}天(节奏好)")
    elif 1 <= days_from_high < 3:
        score += 5
        signals.append(f"回调{days_from_high}天(刚开始)")
    elif 10 < days_from_high <= 20:
        score += 5
        signals.append(f"回调{days_from_high}天(偏久)")

    # === 3. 回调至关键均线 (0-20分) ===
    pos_ma20 = (close[latest] / ma20[latest] - 1) * 100
    pos_ma10 = (close[latest] / ma10[latest] - 1) * 100

    # 接近MA20（-3%~+3%）
    if -3 <= pos_ma20 <= 3:
        score += 12
        signals.append(f"靠近MA20({pos_ma20:+.1f}%)")
    elif -5 <= pos_ma20 < -3:
        score += 8
        signals.append(f"跌破MA20({pos_ma20:+.1f}%)")
    elif 3 < pos_ma20 <= 8:
        score += 6
        signals.append(f"MA20上方({pos_ma20:+.1f}%)")

    # 接近MA10
    if -2 <= pos_ma10 <= 2:
        score += 8
        signals.append(f"靠近MA10({pos_ma10:+.1f}%)")

    # === 4. 缩量回调 (0-15分) ===
    vol_ratio = vol5[latest] / (vol20[latest] + 0.0001)

    if vol_ratio < 0.7:
        score += 12
        signals.append(f"缩量回调({vol_ratio:.1f}倍)")
    elif vol_ratio < 0.85:
        score += 8
        signals.append(f"温和缩量({vol_ratio:.1f}倍)")
    elif vol_ratio < 1.0:
        score += 4
        signals.append(f"量能平稳({vol_ratio:.1f}倍)")
    else:
        score += 0
        signals.append(f"放量({vol_ratio:.1f}倍)")

    # 回调过程缩量（高点那天的量 vs 现在的量）
    high_vol = vol[recent_high_idx]
    shrink_from_high = vol[latest] / (high_vol + 0.0001)
    if shrink_from_high < 0.6:
        score += 3
        signals.append(f"较高点缩量{shrink_from_high:.0%}")

    # === 5. 企稳迹象 (0-15分) ===
    # 下影线（长下影表示支撑）
    body = abs(close[latest] - df['open'].values[latest])
    lower_shadow = min(close[latest], df['open'].values[latest]) - low[latest]
    upper_shadow = high[latest] - max(close[latest], df['open'].values[latest])
    atr = pd.Series(high - low).rolling(14).mean().values[latest]

    if lower_shadow > body * 1.5 and lower_shadow > atr * 0.5:
        score += 5
        signals.append("长下影(支撑强)")

    # 小阳线/十字星（企稳）
    if abs(pct_chg[latest]) < 2:
        score += 3
        signals.append(f"窄幅震荡({pct_chg[latest]:+.1f}%)")

    # KDJ低位（J<30超卖）
    if j[latest] < 20:
        score += 5
        signals.append(f"KDJ超卖(J={j[latest]:.0f})")
    elif j[latest] < 35:
        score += 3
        signals.append(f"KDJ偏低(J={j[latest]:.0f})")

    # RSI
    current_rsi = rsi[latest] if not np.isnan(rsi[latest]) else 50
    if 30 <= current_rsi <= 50:
        score += 2
        signals.append(f"RSI偏低({current_rsi:.0f})")

    # 近2日不创新低
    if low[latest] >= low[latest-1] and low[latest-1] >= low[latest-2]:
        score += 3
        signals.append("不再创新低")

    return {
        'score': min(100, max(0, score)),
        'signals': signals,
        'pullback_pct': pullback_pct,
        'days_from_high': days_from_high,
        'pos_ma20': pos_ma20,
        'vol_ratio': vol_ratio,
        'rsi': current_rsi,
        'kdj_j': j[latest],
        'trend_score': trend_score,
    }


def main():
    print("\n" + "=" * 70)
    print("强势ETF成份股增强策略 - 中线趋势版")
    print("=" * 70)

    # Step 1: 计算所有ETF的中线趋势
    print("\n[Step 1] 计算ETF中线趋势指标...")
    etf_trends = {}
    for idx, (etf_name, etf_code) in enumerate(ETF_POOL.items(), 1):
        df = get_etf_data(etf_code)
        trend = calc_etf_trend(df)
        if trend:
            etf_trends[etf_name] = {
                'code': get_etf_suffix(etf_code),
                'trend': trend,
                'passed': False,
                'reason': '',
                'score': 0,
            }
        print(f"  [{idx}/{len(ETF_POOL)}] {etf_name}({etf_code}): {'OK' if trend else 'FAIL'}")

    # Step 2: 筛选中线趋势ETF
    print("\n[Step 2] 筛选中线趋势ETF...")
    mid_trend_etfs = {}
    for etf_name, data in etf_trends.items():
        passed, reason = is_mid_trend(data['trend'])
        data['passed'] = passed
        data['reason'] = reason
        data['score'] = calculate_etf_score(data['trend'])
        if passed:
            mid_trend_etfs[etf_name] = data

    print(f"\n  通过中线趋势筛选: {len(mid_trend_etfs)}/{len(ETF_POOL)} ETF")

    # 按评分排序输出
    sorted_etfs = sorted(mid_trend_etfs.items(), key=lambda x: x[1]['score'], reverse=True)
    print("\n  === 中线趋势强势ETF TOP ===")
    for etf_name, data in sorted_etfs[:15]:
        t = data['trend']
        print(f"  {etf_name}({data['code']}): 评分={data['score']:.0f} | "
              f"20日={t['pct20']:+.1f}% 60日={t['pct60']:+.1f}% | "
              f"偏离MA60={t['deviation60']:+.1f}%")

    # Step 3: 获取成份股
    print("\n[Step 3] 获取中线趋势ETF的成份股...")
    all_constituents = []
    for etf_name, data in mid_trend_etfs.items():
        constituents = get_etf_constituents(data['code'].replace('.SZ', '').replace('.SH', ''))
        for c in constituents:
            c['etf_name'] = etf_name
            c['etf_code'] = data['code']
            c['etf_trend_score'] = data['score']
            all_constituents.append(c)
        print(f"  {etf_name}: {len(constituents)}只成份股")

    if not all_constituents:
        print("\n未获取到任何成份股数据！")
        return

    df_cons = pd.DataFrame(all_constituents)

    # Step 4: 去重统计
    print("\n[Step 4] 成份股统计...")
    unique_stocks = df_cons['con_code'].nunique()
    print(f"  总记录数: {len(df_cons)}")
    print(f"  去重后股票数: {unique_stocks}")

    # Step 5: 剔除已入选合格股池的股票
    print("\n[Step 5] 剔除已入选合格股池的股票...")
    bull_file = os.path.join(BASE_DIR, "report_daily", "bull_stocks_qualified.csv")
    if os.path.exists(bull_file):
        bull_df = pd.read_csv(bull_file)
        bull_codes = set()
        for code in bull_df['code'].dropna():
            code = str(code).zfill(6)
            if code.startswith('6') or code.startswith('5') or code.startswith('9'):
                code = code + '.SH'
            else:
                code = code + '.SZ'
            bull_codes.add(code)

        before_count = len(df_cons['con_code'].unique())
        df_cons = df_cons[~df_cons['con_code'].isin(bull_codes)]
        after_count = len(df_cons['con_code'].unique())
        print(f"  剔除前: {before_count} 只")
        print(f"  剔除后: {after_count} 只")
        print(f"  剔除掉: {before_count - after_count} 只")
    else:
        print("  合格股池文件不存在，跳过剔除")

    # Step 6: 计算个股上升趋势回调信号
    print("\n[Step 6] 计算个股上升趋势回调信号...")
    unique_codes = df_cons['con_code'].unique()
    pb_results = {}
    for idx, code in enumerate(unique_codes, 1):
        df_stock = get_stock_daily(code)
        pb = calc_uptrend_pullback(df_stock)
        if pb:
            pb_results[code] = pb
        if idx % 10 == 0:
            print(f"  已计算 {idx}/{len(unique_codes)} 只")
    print(f"  完成: {len(pb_results)}/{len(unique_codes)} 只有效数据")

    # Step 7: 综合排序（回调信号为主，ETF评分为辅）
    print("\n[Step 7] 综合排序生成增强池...")
    df_cons['cpr_num'] = pd.to_numeric(df_cons['cpr'], errors='coerce')

    # 匹配回调数据
    df_cons['pb_score'] = df_cons['con_code'].map(lambda x: pb_results.get(x, {}).get('score', 0))
    df_cons['pb_signals'] = df_cons['con_code'].map(lambda x: '; '.join(pb_results.get(x, {}).get('signals', [])))
    df_cons['pullback_pct'] = df_cons['con_code'].map(lambda x: round(pb_results.get(x, {}).get('pullback_pct', 0), 1))
    df_cons['days_from_high'] = df_cons['con_code'].map(lambda x: pb_results.get(x, {}).get('days_from_high', 0))
    df_cons['pos_ma20'] = df_cons['con_code'].map(lambda x: round(pb_results.get(x, {}).get('pos_ma20', 0), 1))
    df_cons['vol_ratio'] = df_cons['con_code'].map(lambda x: round(pb_results.get(x, {}).get('vol_ratio', 0), 2))
    df_cons['rsi'] = df_cons['con_code'].map(lambda x: round(pb_results.get(x, {}).get('rsi', 0), 1))
    df_cons['kdj_j'] = df_cons['con_code'].map(lambda x: round(pb_results.get(x, {}).get('kdj_j', 0), 1))
    df_cons['trend_score'] = df_cons['con_code'].map(lambda x: pb_results.get(x, {}).get('trend_score', 0))

    # 综合评分 = 回调信号分 * 0.7 + ETF趋势评分 * 0.3
    df_cons['final_score'] = df_cons['pb_score'] * 0.7 + df_cons['etf_trend_score'] * 0.3
    df_cons = df_cons.sort_values(['final_score', 'pb_score'], ascending=[False, False])

    # Step 8: 输出结果
    output_cols = [
        'con_code', 'con_name', 'etf_name', 'etf_code',
        'final_score', 'pb_score', 'etf_trend_score', 'trend_score',
        'pb_signals', 'pullback_pct', 'days_from_high',
        'pos_ma20', 'vol_ratio', 'rsi', 'kdj_j',
        'cpr', 'qty'
    ]
    df_output = df_cons[output_cols].drop_duplicates(subset=['con_code']).head(100)

    output_file = os.path.join(REPORT_DIR, f"etf_enhance_midtrend_{TRADE_DATE}.csv")
    df_output.to_csv(output_file, index=False, encoding='utf-8-sig')

    print(f"\n{'='*70}")
    print("增强池生成完成！")
    print(f"  中线趋势ETF数量: {len(mid_trend_etfs)}")
    print(f"  增强池股票数: {len(df_output)}")
    print(f"  输出文件: {output_file}")
    print(f"{'='*70}\n")

    # 打印增强池TOP 20
    print("\n=== 增强池 TOP 20（上升趋势回调）===")
    for idx, row in df_output.head(20).iterrows():
        print(f"  {row['con_code']} {row['con_name']} | "
              f"综合分={row['final_score']:.0f} 回调分={row['pb_score']:.0f} | "
              f"来自:{row['etf_name']} | "
              f"回调{abs(row['pullback_pct']):.1f}% {row['days_from_high']}天 "
              f"距MA20={row['pos_ma20']:+.1f}% 量比={row['vol_ratio']:.1f}")


if __name__ == '__main__':
    main()
