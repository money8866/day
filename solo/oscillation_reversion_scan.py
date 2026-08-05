"""
震荡市均值回归策略 — 布林带 + RSI 高抛低吸
============================================
适用环境：大盘震荡盘整期（猴市）。此时趋势策略会被来回打脸，
改用"高抛低吸"的均值回归策略，赚取 3%-5% 的波段利润。

策略逻辑：
1. 环境过滤：仅在大盘『震荡』状态下运行（牛市→趋势策略，熊市→空仓）
2. 买点：股价触及/跌破布林下轨 + RSI超卖(<35) + 缩量企稳
3. 卖点：触上轨 / RSI>70 / 盈利≥5% 分批止盈
4. 止损：亏损≥5% 或 跌破布林下轨3%仍加速下跌

与趋势策略(v2)配合方式：
- 大盘bull → 跑 vol_ma_sync_surge_scan.py（趋势跟随）
- 大盘震荡 → 跑本脚本（均值回归）
- 大盘bear → 双策略同时停止，空仓等待

历史经验：
- 震荡市均值回归单次胜率65%-70%，单笔盈利3%-5%，积少成多
- 致命陷阱：震荡市用趋势策略=反复高买低卖；趋势市用本策略=卖飞大牛
"""
import sys, os, time, json
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

import pandas as pd
import numpy as np

RESULT_DIR = r"d:\mystock\cache_daily"


def get_market_regime():
    """大盘环境过滤器（与趋势策略同一套逻辑，保证双策略切换一致）
    
    - bull：上证站上MA20且近5日涨幅>1% → 趋势策略主攻
    - 震荡：上证在MA20附近 → 本策略（均值回归）运行
    - bear：上证跌破MA20 → 双策略停止，空仓
    """
    try:
        idx_code = '000001.SH'
        cache_file = os.path.join(tq.CACHE_DIR, f"{idx_code}.csv")
        idx_df = None
        need_refresh = False
        if os.path.exists(cache_file):
            try:
                idx_df = pd.read_csv(cache_file)
                idx_df['trade_date'] = idx_df['trade_date'].astype(str)
                idx_df = idx_df[idx_df['trade_date'] <= tq.TRADE_DATE].sort_values('trade_date')
                if len(idx_df) > 0:
                    if idx_df['trade_date'].iloc[-1] < str(tq.TRADE_DATE):
                        need_refresh = True
                else:
                    need_refresh = True
            except Exception:
                idx_df = None
                need_refresh = True

        if idx_df is None or len(idx_df) < 25 or need_refresh:
            try:
                idx_df = tq.pro.index_daily(ts_code=idx_code, start_date='20250101', end_date=tq.TRADE_DATE)
                if idx_df is None or len(idx_df) == 0:
                    return {'allow_trade': True, 'regime': 'unknown', 'reason': '指数数据获取失败，默认放行'}
                idx_df['trade_date'] = idx_df['trade_date'].astype(str)
                idx_df = idx_df.sort_values('trade_date')
                idx_df.to_csv(cache_file, index=False)
            except Exception:
                if idx_df is None or len(idx_df) < 25:
                    return {'allow_trade': True, 'regime': 'unknown', 'reason': '指数异常，默认放行'}

        if len(idx_df) < 25:
            return {'allow_trade': True, 'regime': 'unknown', 'reason': '指数数据不足，默认放行'}

        close_arr = idx_df['close'].values.astype(float)
        last_close = close_arr[-1]
        ma20 = pd.Series(close_arr).rolling(20, min_periods=1).mean().values[-1]
        close_5d_ago = close_arr[-6] if len(close_arr) >= 6 else close_arr[0]
        sh_chg_5d = (last_close / close_5d_ago - 1) * 100
        sh_above_ma20 = last_close > ma20

        if not sh_above_ma20:
            regime = 'bear'
            allow = False
            reason = f'上证({last_close:.0f})跌破MA20({ma20:.0f})'
        elif sh_chg_5d < -3:
            regime = 'bear'
            allow = False
            reason = f'上证近5日{sh_chg_5d:+.2f}%急跌'
        elif sh_above_ma20 and sh_chg_5d > 1:
            regime = 'bull'
            allow = True
            reason = f'上证站上MA20，近5日{sh_chg_5d:+.2f}%'
        else:
            regime = '震荡'
            allow = True
            reason = f'上证({last_close:.0f})在MA20({ma20:.0f})附近震荡'

        return {
            'allow_trade': allow, 'regime': regime,
            'sh_close': round(last_close, 2), 'sh_ma20': round(ma20, 2),
            'sh_chg_5d': round(sh_chg_5d, 2), 'reason': reason,
        }
    except Exception as e:
        return {'allow_trade': True, 'regime': 'unknown', 'reason': f'异常，默认放行'}


def calc_rsi(close_arr, period=14):
    """RSI指标（Wilder平滑），返回与输入等长的数组"""
    if len(close_arr) < period + 1:
        return None
    s = pd.Series(close_arr)
    delta = s.diff()  # 首值为NaN，长度与输入一致
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(100.0).values  # 持续上涨(avg_loss=0)时RSI=100


def calc_bollinger(close_arr, period=20, num_std=2):
    """布林带：中轨=MA20，上下轨=±2倍标准差"""
    ma = pd.Series(close_arr).rolling(period, min_periods=1).mean().values
    std = pd.Series(close_arr).rolling(period, min_periods=1).std().values
    upper = ma + num_std * std
    lower = ma - num_std * std
    return upper, ma, lower


def detect_reversion_buy(df, target_idx=None):
    """检测震荡市均值回归买点（布林下轨+RSI超卖）

    买点条件：
    1. RSI14 < 35（超卖）
    2. 最低价触及/跌破布林下轨（low <= lower）
    3. 非单边下跌：MA20斜率 > -2.5%，且收盘未跌破下轨3%（不接飞刀）
    4. 缩量企稳：量比 < 1.6（今日量 / 20日均量）
    5. 布林带未过度张口（避免崩盘形态）
    """
    if df is None or len(df) < 80:
        return None

    if target_idx is None:
        target_idx = len(df) - 1

    close_arr = df['close'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    vol_arr = df['vol'].values.astype(float)
    pre_close_arr = df['pre_close'].values.astype(float)

    if target_idx < 40:
        return None

    # --- RSI ---
    rsi_arr = calc_rsi(close_arr[:target_idx + 1])
    if rsi_arr is None or len(rsi_arr) <= target_idx:
        return None
    rsi_now = rsi_arr[target_idx]
    if rsi_now >= 35:
        return None

    # --- 布林带 ---
    upper, mid, lower = calc_bollinger(close_arr[:target_idx + 1])
    last_close = close_arr[target_idx]
    last_low = low_arr[target_idx]
    last_high = high_arr[target_idx]

    # 条件：最低价触及/跌破下轨
    if last_low > lower[target_idx]:
        return None

    # 条件：收盘不深跌于下轨（-3%以内，避免接飞刀）
    if last_close < lower[target_idx] * 0.97:
        return None

    # --- 非单边下跌判断：MA20斜率 ---
    ma20 = mid
    ma20_5ago = ma20[target_idx - 5] if target_idx - 5 >= 0 else ma20[target_idx]
    ma20_slope = (ma20[target_idx] / ma20_5ago - 1) * 100 if ma20_5ago > 0 else 0
    if ma20_slope < -2.5:
        return None  # MA20加速下行=下跌趋势中，不做均值回归

    # --- 布林带宽度：不张口过大（崩盘形态） ---
    bw = (upper[target_idx] - lower[target_idx]) / mid[target_idx] * 100 if mid[target_idx] > 0 else 0
    if bw > 35:
        return None  # 极度张口=恐慌盘，不接

    # --- 缩量企稳 ---
    vol_ma20 = float(np.mean(vol_arr[max(0, target_idx - 20):target_idx]))
    last_vol_ratio = vol_arr[target_idx] / max(vol_ma20, 1)
    if last_vol_ratio >= 1.6:
        return None

    # --- 近20日涨幅（不能是刚从高位大跌50%的票） ---
    chg_20d = (last_close / close_arr[max(0, target_idx - 20)] - 1) * 100
    if chg_20d < -25:
        return None

    # --- 当日涨跌 ---
    last_chg = (last_close / pre_close_arr[target_idx] - 1) * 100

    # ========== 评分（100分制） ==========
    score = 0

    # RSI超卖深度 (40分)
    if rsi_now <= 15: score += 40
    elif rsi_now <= 20: score += 35
    elif rsi_now <= 25: score += 28
    elif rsi_now <= 30: score += 20
    elif rsi_now <= 35: score += 12

    # 触及下轨深度 (25分)：跌破越多越低吸越划算，但过深警惕
    dist_lower = (last_low / lower[target_idx] - 1) * 100  # 负数=跌破
    if -2 <= dist_lower <= 0: score += 25
    elif -3 < dist_lower < -2: score += 18
    elif 0 < dist_lower <= 1: score += 15

    # 震荡确认：MA20走平 (15分)
    if abs(ma20_slope) <= 1.5: score += 15
    elif ma20_slope <= 2.5: score += 10

    # 缩量程度 (10分)
    if last_vol_ratio < 0.6: score += 10
    elif last_vol_ratio < 0.8: score += 8
    elif last_vol_ratio < 1.0: score += 6
    elif last_vol_ratio < 1.3: score += 3

    # 布林带宽度适中=震荡确认 (10分)
    if 5 <= bw <= 20: score += 10
    elif bw <= 25: score += 6

    return {
        'rsi': round(rsi_now, 1),
        'dist_lower': round(dist_lower, 2),
        'ma20_slope': round(ma20_slope, 2),
        'bandwidth': round(bw, 2),
        'vol_ratio': round(last_vol_ratio, 2),
        'chg_20d': round(chg_20d, 2),
        'last_chg': round(last_chg, 2),
        'score': int(score),
        'close': round(float(last_close), 2),
        'low': round(float(last_low), 2),
        'boll_lower': round(float(lower[target_idx]), 2),
        'boll_upper': round(float(upper[target_idx]), 2),
    }


def daily_scan(threshold=65):
    """震荡市选股主函数"""
    today = str(tq.TRADE_DATE)

    print("=" * 65)
    print("震荡市均值回归策略（布林带+RSI 高抛低吸）")
    print("=" * 65)
    print(f"交易日: {today}")

    # 大盘环境判断
    print("\n【大盘环境】")
    regime = get_market_regime()
    print(f"  {regime['regime']} | {regime['reason']}")

    if regime['regime'] == 'bull':
        print("\n🟢 牛市/主升浪：请使用趋势策略（vol_ma_sync_surge_scan.py）")
        print("   ⚠ 震荡策略在趋势市会卖飞大牛股，禁止使用！")
        return []
    if regime['regime'] == 'bear':
        print("\n🛑 熊市：建议空仓，双策略同时停止")
        return []

    # 震荡市：提高门槛（震荡市假信号多）
    if regime['regime'] == '震荡':
        threshold = max(threshold, 70)
        print(f"  震荡市确认，买点阈值提升至 {threshold} 分")

    all_stocks = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
    all_stocks = [c for c in all_stocks if not (c.startswith('8') or c.startswith('4') or c.startswith('9'))]
    print(f"待扫描股票: {len(all_stocks)}")
    print()

    signals = []
    scanned = 0
    t0 = time.time()

    for ts_code in all_stocks:
        scanned += 1
        if scanned % 1000 == 0:
            print(f"  进度: {scanned}/{len(all_stocks)}, 买点{len(signals)}只, {time.time() - t0:.0f}s")

        try:
            stock_df = tq.get_hist_data(ts_code)
            if stock_df is None or len(stock_df) < 80:
                continue

            name = tq.get_stock_name(ts_code) if hasattr(tq, 'get_stock_name') else ts_code

            result = detect_reversion_buy(stock_df)
            if result and result['score'] >= threshold:
                result['code'] = ts_code
                result['name'] = name
                signals.append(result)

        except Exception:
            pass

    elapsed = time.time() - t0
    print(f"\n扫描完成: {scanned}只, 耗时{elapsed:.0f}s")

    # ========== 输出 ==========
    signals.sort(key=lambda x: -x['score'])

    print("\n" + "=" * 65)
    print(f"🟢 【今日布林下轨低吸买点】{len(signals)}只")
    print("=" * 65)

    if signals:
        print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'评分':<6}{'现价':<8}{'布林下轨':<10}{'RSI':<7}{'距下轨':<9}{'量比':<7}{'20日涨跌':<9}")
        print("-" * 90)
        for i, s in enumerate(signals[:20], 1):
            tag = "⭐" if s['score'] >= 85 else ("✓" if s['score'] >= 75 else "△")
            print(f"{i:<4}{s['code']:<12}{str(s['name'])[:8]:<10}{s['score']:<6}{s['close']:<8.2f}"
                  f"{s['boll_lower']:<10.2f}{s['rsi']:<7.1f}{s['dist_lower']:<+7.2f}%  "
                  f"{s['vol_ratio']:<7.2f}{s['chg_20d']:<+7.2f}% {tag}")
    else:
        print("  今日无符合条件的低吸买点")

    # ========== 操作规则 ==========
    print("\n" + "=" * 65)
    print("【操作规则】")
    print("=" * 65)
    print("1. 环境：仅震荡市运行；牛市切趋势策略，熊市空仓")
    print("2. 买入：触及布林下轨 + RSI<35 时低吸，单只不超过2成")
    print("3. 止盈（三选一，先到先卖）：")
    print("   · 收盘触及布林上轨")
    print("   · RSI > 70")
    print("   · 盈利 ≥ 5%（积少成多，不要贪）")
    print("4. 止损：亏损 ≥ 5% 立即卖出（均值回归不扛单）")
    print("5. 持仓周期：3-8个交易日，赚3%-5%就兑现")
    print("6. ⭐为最优（评分>=85），✓为次优，△为观察")

    # 保存结果
    if signals:
        pd.DataFrame(signals).to_csv(
            rf"{RESULT_DIR}\ReversionBuySignals_{today}.csv",
            index=False, encoding='utf-8-sig')
        print(f"\n✅ 结果已保存: {RESULT_DIR}\\ReversionBuySignals_{today}.csv")

    return signals


if __name__ == "__main__":
    daily_scan()
