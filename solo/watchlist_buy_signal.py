# -*- coding: utf-8 -*-
"""
两阶段策略：选股 + 择时买点

第一阶段：从 theme_pattern_stock_picker 的选股结果中读取观察池
第二阶段：每日监控观察池，检测回调买点信号

买点信号（差异化入场）：
- 双创（300/301/688/689）：等待15天严格回踩，共振阈值≥2个指标，MA10回踩容忍度±4%/±3%
- 主板（600/601/603/605/000/001/002）：等待10天快速回踩，共振阈值≥1个指标，MA10回踩容忍度±5%

买点条件：
1. 趋势未破坏：MA20 > MA60
2. 回调幅度：从高点回落5%-15%
3. 回调至关键均线：MA10/MA20附近
4. 缩量回调：量比<0.8
5. 企稳迹象：KDJ低位/长下影/不创新低
6. 共振信号数 >= 阈值（双创≥2，主板≥1）
"""
import os
import sys
import time
import datetime
import glob
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = os.path.dirname(BASE_DIR)
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

load_dotenv(os.path.join(STOCK_DATA_DIR, "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")


def get_last_trade_date():
    now = datetime.datetime.now()
    if now.hour < 15:
        query_date = (now - datetime.timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='', start_date='20260101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


TRADE_DATE = get_last_trade_date()


def is_shuangchuang(code):
    """判断是否为双创股票"""
    pure = code.replace('.SH', '').replace('.SZ', '')
    return pure.startswith(('300', '301', '688', '689'))


def get_board_params(code):
    """获取板块差异化参数"""
    if is_shuangchuang(code):
        return {
            'max_wait_days': 15,
            'min_resonance': 2,
            'ma10_tolerance': 4.0,  # 上方容忍
            'ma10_tolerance_down': 3.0,  # 下方容忍
        }
    else:
        return {
            'max_wait_days': 10,
            'min_resonance': 1,
            'ma10_tolerance': 5.0,
            'ma10_tolerance_down': 5.0,
        }


def load_watchlist(days_back=7):
    """
    加载观察池：从最近N天的theme_pattern选股结果中读取
    去重，保留最高分
    """
    watchlist = {}

    # 查找最近的theme_pattern_stocks文件
    pattern = os.path.join(REPORT_DIR, "theme_pattern_stocks_*.csv")
    files = sorted(glob.glob(pattern), reverse=True)

    for f in files[:days_back]:
        try:
            df = pd.read_csv(f)
            date_str = os.path.basename(f).split('_')[-1].replace('.csv', '')
            for _, row in df.iterrows():
                code = row['code']
                score = float(row.get('final_score', 0))
                if code not in watchlist or score > watchlist[code]['final_score']:
                    watchlist[code] = {
                        'code': code,
                        'name': row['name'],
                        'final_score': score,
                        'buy_type': row.get('buy_type', ''),
                        'theme_name': row.get('theme_name', ''),
                        'pick_date': date_str,
                        'pick_close': float(row.get('close', 0)),
                    }
        except Exception as e:
            print(f"  [WARN] 读取{f}失败: {e}")

    return list(watchlist.values())


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


def calc_buy_signal(df, code, pick_date=None):
    """
    计算回调买点信号

    返回:
        signal: 'BUY' / 'WAIT' / 'AVOID'
        score: 0-100
        details: dict
        reasons: list
    """
    if df is None or len(df) < 40:
        return 'WAIT', 0, {}, ['数据不足']

    params = get_board_params(code)

    df = df.copy()
    df = df.sort_values('trade_date').reset_index(drop=True)
    n = len(df)

    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    open_ = df['open'].values
    vol = df['vol'].values
    pct_chg = df['pct_chg'].values

    # 均线
    ma5 = pd.Series(close).rolling(5).mean().values
    ma10 = pd.Series(close).rolling(10).mean().values
    ma20 = pd.Series(close).rolling(20).mean().values
    ma60 = pd.Series(close).rolling(60).mean().values

    # 量均线
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

    # 近20日高点
    recent_high_idx = n - 20 + np.argmax(close[n-20:n])
    recent_high = close[recent_high_idx]
    days_from_high = latest - recent_high_idx
    pullback_pct = (close[latest] / recent_high - 1) * 100

    # 位置
    pos_ma5 = (close[latest] / ma5[latest] - 1) * 100 if not np.isnan(ma5[latest]) else 0
    pos_ma10 = (close[latest] / ma10[latest] - 1) * 100 if not np.isnan(ma10[latest]) else 0
    pos_ma20 = (close[latest] / ma20[latest] - 1) * 100 if not np.isnan(ma20[latest]) else 0
    pos_ma60 = (close[latest] / ma60[latest] - 1) * 100 if not np.isnan(ma60[latest]) else 0

    vol_ratio = vol5[latest] / (vol20[latest] + 0.0001) if not np.isnan(vol5[latest]) and not np.isnan(vol20[latest]) else 1.0
    high_vol = vol[recent_high_idx]
    shrink_from_high = vol[latest] / (high_vol + 0.0001)

    # K线形态
    body = abs(close[latest] - open_[latest])
    lower_shadow = min(close[latest], open_[latest]) - low[latest]
    atr = pd.Series(high - low).rolling(14).mean().values[latest]
    if np.isnan(atr):
        atr = high[latest] - low[latest] + 0.0001

    current_rsi = rsi[latest] if not np.isnan(rsi[latest]) else 50
    current_j = j[latest] if not np.isnan(j[latest]) else 50

    # === 核心条件检测 ===
    reasons = []
    resonance = 0  # 共振信号计数
    score = 0

    # 条件0: 趋势未破坏（必须满足）
    trend_ok = ma20[latest] > ma60[latest] if not np.isnan(ma60[latest]) else False
    if not trend_ok:
        return 'AVOID', 0, {'pullback_pct': pullback_pct}, ['趋势已破坏(MA20<MA60)']

    ma60_slope = (ma60[latest] / ma60[latest-10] - 1) * 100 if not np.isnan(ma60[latest-10]) else 0
    if ma60_slope > 0.5:
        score += 10
        reasons.append(f"MA60向上({ma60_slope:+.1f}%)")

    # 条件1: 回调幅度5%-15%（最佳区间）
    if -15 <= pullback_pct <= -5:
        score += 20
        resonance += 1
        reasons.append(f"回调{abs(pullback_pct):.1f}%(黄金区间)")
    elif -20 <= pullback_pct < -15:
        score += 10
        reasons.append(f"回调{abs(pullback_pct):.1f}%(偏深)")
    elif -5 < pullback_pct < 0:
        score += 8
        reasons.append(f"回调{abs(pullback_pct):.1f}%(刚启动)")
    elif pullback_pct >= 0:
        score += 3
        reasons.append("新高(未回调)")

    # 条件2: 回调至MA10（差异化容忍度）
    if -params['ma10_tolerance_down'] <= pos_ma10 <= params['ma10_tolerance']:
        score += 20
        resonance += 1
        reasons.append(f"回踩MA10({pos_ma10:+.1f}%)")
    elif -8 <= pos_ma10 < -params['ma10_tolerance_down']:
        score += 8
        reasons.append(f"跌破MA10({pos_ma10:+.1f}%)")

    # 条件3: 回调至MA20（±3%）
    if -3 <= pos_ma20 <= 3:
        score += 15
        resonance += 1
        reasons.append(f"回踩MA20({pos_ma20:+.1f}%)")
    elif -5 <= pos_ma20 < -3:
        score += 8
        reasons.append(f"跌破MA20({pos_ma20:+.1f}%)")

    # 条件4: 缩量回调
    if vol_ratio < 0.7:
        score += 15
        resonance += 1
        reasons.append(f"缩量回调({vol_ratio:.1f}倍)")
    elif vol_ratio < 0.85:
        score += 10
        reasons.append(f"温和缩量({vol_ratio:.1f}倍)")
    elif vol_ratio < 1.0:
        score += 5
        reasons.append(f"量能平稳({vol_ratio:.1f}倍)")

    # 较高点缩量
    if shrink_from_high < 0.5:
        score += 5
        reasons.append(f"较高点缩量{shrink_from_high:.0%}")

    # 条件5: 企稳迹象（共振项）
    # 5a. KDJ低位
    if current_j < 20:
        score += 10
        resonance += 1
        reasons.append(f"KDJ超卖(J={current_j:.0f})")
    elif current_j < 35:
        score += 5
        reasons.append(f"KDJ偏低(J={current_j:.0f})")

    # 5b. 长下影线
    if lower_shadow > body * 1.5 and lower_shadow > atr * 0.5:
        score += 8
        resonance += 1
        reasons.append("长下影(支撑强)")

    # 5c. 近2日不创新低
    if latest >= 2 and low[latest] >= low[latest-1] and low[latest-1] >= low[latest-2]:
        score += 8
        resonance += 1
        reasons.append("不再创新低")

    # 5d. 窄幅震荡企稳
    if abs(pct_chg[latest]) < 2:
        score += 5
        reasons.append(f"窄幅震荡({pct_chg[latest]:+.1f}%)")

    # 5e. RSI低位
    if 30 <= current_rsi <= 50:
        score += 5
        reasons.append(f"RSI偏低({current_rsi:.0f})")

    # 条件6: 回调天数在合理区间
    if 3 <= days_from_high <= params['max_wait_days']:
        score += 10
        reasons.append(f"回调{days_from_high}天(节奏好)")
    elif days_from_high > params['max_wait_days']:
        score -= 5
        reasons.append(f"回调{days_from_high}天(偏久)")

    # === 信号判定 ===
    score = min(100, max(0, score))

    details = {
        'pullback_pct': pullback_pct,
        'days_from_high': days_from_high,
        'pos_ma10': pos_ma10,
        'pos_ma20': pos_ma20,
        'vol_ratio': vol_ratio,
        'rsi': current_rsi,
        'kdj_j': current_j,
        'resonance': resonance,
        'trend_ok': trend_ok,
    }

    # 买点判定：共振信号数 >= 阈值
    if resonance >= params['min_resonance'] and score >= 60:
        signal = 'BUY'
    elif score >= 40:
        signal = 'WAIT'
    else:
        signal = 'WAIT'

    return signal, score, details, reasons


def main():
    print("\n" + "=" * 70)
    print(f"两阶段策略 - 选股观察池 + 回调买点检测")
    print(f"当前交易日: {TRADE_DATE}")
    print("=" * 70)

    # Step 1: 加载观察池
    print("\n[Step 1] 加载观察池（最近7天theme_pattern选股结果）...")
    watchlist = load_watchlist(days_back=7)
    print(f"  观察池股票数: {len(watchlist)} 只")

    # 按评分排序
    watchlist.sort(key=lambda x: x['final_score'], reverse=True)
    print(f"  TOP 10:")
    for i, s in enumerate(watchlist[:10], 1):
        print(f"    {i}. {s['code']} {s['name']} | 评分{s['final_score']:.0f} | 选出日:{s['pick_date']} | {s['theme_name']}")

    # Step 2: 检测买点信号
    print(f"\n[Step 2] 检测回调买点信号...")
    buy_signals = []
    wait_signals = []
    avoid_signals = []

    for idx, stock in enumerate(watchlist, 1):
        code = stock['code']
        df = get_stock_daily(code)
        signal, score, details, reasons = calc_buy_signal(df, code, stock['pick_date'])

        stock['signal'] = signal
        stock['buy_score'] = score
        stock['reasons'] = '; '.join(reasons)
        stock.update(details)

        if signal == 'BUY':
            buy_signals.append(stock)
        elif signal == 'AVOID':
            avoid_signals.append(stock)
        else:
            wait_signals.append(stock)

        if idx % 10 == 0:
            print(f"  已检测 {idx}/{len(watchlist)}")

    # Step 3: 输出结果
    print(f"\n[Step 3] 检测完成")
    print(f"  BUY信号: {len(buy_signals)} 只")
    print(f"  WAIT等待: {len(wait_signals)} 只")
    print(f"  AVOID回避: {len(avoid_signals)} 只")

    # BUY信号排序
    buy_signals.sort(key=lambda x: x['buy_score'], reverse=True)

    # 打印BUY信号
    if buy_signals:
        print(f"\n{'='*70}")
        print(f"=== BUY 信号（可买入）TOP {min(20, len(buy_signals))} ===")
        print(f"{'='*70}")
        for i, s in enumerate(buy_signals[:20], 1):
            board = '双创' if is_shuangchuang(s['code']) else '主板'
            print(f"  {i}. {s['code']} {s['name']} | 买分{s['buy_score']:.0f} | {board} | "
                  f"回调{abs(s['pullback_pct']):.1f}% {s['days_from_high']}天 | "
                  f"MA10={s['pos_ma10']:+.1f}% 量比={s['vol_ratio']:.1f} | "
                  f"共振{s['resonance']}个")
            print(f"     信号: {s['reasons']}")
    else:
        print("\n  当前无BUY信号，所有股票仍在回调中或趋势已破坏")

    # 打印WAIT信号TOP10
    if wait_signals:
        wait_signals.sort(key=lambda x: x['buy_score'], reverse=True)
        print(f"\n{'='*70}")
        print(f"=== WAIT 等待中 TOP 10（接近买点）===")
        print(f"{'='*70}")
        for i, s in enumerate(wait_signals[:10], 1):
            board = '双创' if is_shuangchuang(s['code']) else '主板'
            print(f"  {i}. {s['code']} {s['name']} | 买分{s['buy_score']:.0f} | {board} | "
                  f"回调{abs(s['pullback_pct']):.1f}% | "
                  f"MA10={s['pos_ma10']:+.1f}% 量比={s['vol_ratio']:.1f} | "
                  f"共振{s['resonance']}个")

    # 打印AVOID
    if avoid_signals:
        print(f"\n{'='*70}")
        print(f"=== AVOID 趋势破坏 ({len(avoid_signals)}只) ===")
        print(f"{'='*70}")
        for s in avoid_signals[:10]:
            print(f"  {s['code']} {s['name']} | {s['reasons']}")

    # 保存结果
    all_results = buy_signals + wait_signals + avoid_signals
    df_out = pd.DataFrame(all_results)

    output_file = os.path.join(REPORT_DIR, f"watchlist_buy_signal_{TRADE_DATE}.csv")
    df_out.to_csv(output_file, index=False, encoding='utf-8-sig')

    print(f"\n{'='*70}")
    print(f"结果已保存: {output_file}")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
