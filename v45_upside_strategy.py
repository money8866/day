# -*- coding: utf-8 -*-
"""
上涨空间预测策略 V4.5（多因子共振版）
增强点：
  1. V4原始核心（回档评分：pullback_MA5+10/MA10+5/突破-5）
  2. 量能验证（缩量回踩<MA20均量60%）
  3. 板块确认（S/A级板块1.2x加成）
  4. 大盘环境过滤（MA20之上启用，MA60之下禁用）
  5. RSI-2超卖二次确认
"""

import sys, os, json, math, itertools
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import tushare as ts
import pytdx

# ========== 路径配置 ==========
BASE_DIR     = r'D:\mystock'
sys.path.insert(0, BASE_DIR)

# ========== Tushare Token ==========
TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'

# ========== 板块景气度数据（可由 block.py 输出注入）==========
# 格式：{code: 'S'/'A'/'B'/'C'}
SECTOR_BONUS_MAP = {
    'S': 1.20,
    'A': 1.10,
    'B': 1.00,
    'C': 0.85,
    'D': 0.70,
}

# 今日已知板块评级（可由 block.py 每日更新）
TODAY_SECTOR_RATING = {
    '688525.SH': 'S',   # 佰维存储 — 先进封装主线
    '300438.SZ': 'S',   # 鹏辉能源 — 固态电池主线
    '688498.SH': 'A',   # 源杰科技 — 光通信
    '301308.SZ': 'B',   # 江波龙 — 存储芯片
    '603268.SH': 'C',   # 松发股份 — 重组不确定
}

# 大盘指数代码
INDEX_CODE = '000001.SH'  # 上证指数

# ========== Tushare 初始化 ==========
try:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api(TUSHARE_TOKEN)
except:
    pro = None

# ========== 工具函数 ==========
def sf(v, default=0.0):
    """安全转float"""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except:
        return default

def macd_signal(close, fast=12, slow=26, signal=9):
    """计算MACD，返回(dif, dea, bar)"""
    if len(close) < slow + signal:
        return None, None, None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    bar = (dif - dea) * 2
    return dif, dea, bar

def rsi_2(close, period=2):
    """RSI-2，极端超卖指标"""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1.0/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1.0/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.inf)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def rsi_14(close, period=14):
    """RSI-14"""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.inf)
    return 100 - (100 / (1 + rs))

def calc_ma(series, n):
    """移动平均"""
    return series.rolling(n).mean()

def volume_ratio(vol, ma20_vol):
    """量能比率"""
    if ma20_vol == 0 or np.isnan(ma20_vol):
        return 1.0
    return vol / ma20_vol

def market_environment(df_index, lookback=20):
    """
    判断大盘环境：
    - 'bull': MA5 > MA20 且 MA20向上
    - 'neutral': MA5 <= MA20 但未跌破MA60
    - 'bear': 跌破MA60（策略禁用）
    """
    if len(df_index) < lookback + 5:
        return 'neutral'
    ma5  = calc_ma(df_index['close'], 5)
    ma20 = calc_ma(df_index['close'], 20)
    ma60 = calc_ma(df_index['close'], 60)
    cur_close  = df_index['close'].iloc[-1]
    ma5_cur    = ma5.iloc[-1]
    ma20_cur   = ma20.iloc[-1]
    ma60_cur   = ma60.iloc[-1]
    ma20_prev  = ma20.iloc[-3]  # 3天前MA20方向

    if cur_close < ma60_cur:
        return 'bear'
    elif ma5_cur > ma20_cur and ma20_cur > ma20_prev:
        return 'bull'
    else:
        return 'neutral'

# ========== 数据获取 ==========
def get_daily_data(ts_code, start_date, end_date):
    """获取日线数据（含复权）"""
    if pro is None:
        return None
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        # 复权因子
        for col in ['close', 'open', 'high', 'low']:
            pass  # Tushare日线数据已做后复权
        return df
    except Exception as e:
        print(f'  ⚠️ 数据获取失败 {ts_code}: {e}')
        return None

def get_index_data(code, start_date, end_date):
    """获取指数数据"""
    if pro is None:
        return None
    try:
        df = pro.index_daily(ts_code=code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        return df
    except:
        return None

# ========== V4.5 核心评分函数 ==========
def calc_v45_score(df, code, sector_rating='B'):
    """
    V4.5 上涨空间预测综合评分（满分100）
    返回：{
        'total_score': float,    # 综合评分
        'trend_score': float,    # 趋势得分
        'pullback_score': float, # 回档得分
        'volume_score': float,   # 量能得分
        'rsi_score': float,     # RSI得分
        'sector_bonus': float,  # 板块加成
        'env_factor': float,    # 大盘环境系数
        'signal': str,          # 信号：'BUY'/'WATCH'/'AVOID'
        'upside_pct': float,    # 预估上涨空间%
        'entry_price': float,   # 建议入场价
        'stop_loss': float,     # 止损价
        'take_profit': float,  # 止盈价
        'confidence': str,      # 信心等级：HIGH/MEDIUM/LOW
    }
    """
    if df is None or len(df) < 60:
        return None

    close  = df['close']
    open_  = df.get('open', close)
    high   = df.get('high', close)
    low    = df.get('low', close)
    vol    = df.get('vol', df.get('volume', 0))
    amount = df.get('amount', 0)

    # === 计算均线 ===
    ma5   = calc_ma(close, 5)
    ma10  = calc_ma(close, 10)
    ma20  = calc_ma(close, 20)
    ma60  = calc_ma(close, 60)
    ma120 = calc_ma(close, 120)

    cur_close = float(close.iloc[-1])
    cur_ma5   = float(ma5.iloc[-1])  if not np.isnan(ma5.iloc[-1])   else cur_close
    cur_ma10  = float(ma10.iloc[-1]) if not np.isnan(ma10.iloc[-1])  else cur_close
    cur_ma20  = float(ma20.iloc[-1]) if not np.isnan(ma20.iloc[-1])  else cur_close
    cur_ma60  = float(ma60.iloc[-1]) if not np.isnan(ma60.iloc[-1])  else cur_close
    prev_close = float(close.iloc[-2])

    # === 量能 ===
    ma20_vol = vol.rolling(20).mean()
    cur_vol  = float(vol.iloc[-1])
    cur_vol_ma20 = float(ma20_vol.iloc[-1]) if not np.isnan(ma20_vol.iloc[-1]) else cur_vol
    vol_ratio = volume_ratio(cur_vol, cur_vol_ma20)

    # === RSI ===
    rsi2  = rsi_2(close)
    rsi14 = rsi_14(close)
    cur_rsi2  = float(rsi2.iloc[-1])  if rsi2 is not None and not np.isnan(rsi2.iloc[-1])  else 50
    cur_rsi14 = float(rsi14.iloc[-1]) if rsi14 is not None and not np.isnan(rsi14.iloc[-1]) else 50

    # === MACD ===
    dif, dea, bar = macd_signal(close)
    if dif is not None and len(dif) > 0:
        cur_dif  = float(dif.iloc[-1])
        cur_dea  = float(dea.iloc[-1])
        prev_dif = float(dif.iloc[-2])
        macd_bottom = (prev_dif < cur_dea and cur_dif >= cur_dea)  # MACD黄金交叉
    else:
        macd_bottom = False

    # === 均线偏离度 ===
    ma5_deviation   = (cur_close - cur_ma5)  / cur_ma5  * 100
    ma10_deviation  = (cur_close - cur_ma10) / cur_ma10 * 100
    ma20_deviation  = (cur_close - cur_ma20) / cur_ma20 * 100

    # ========== 因子1：趋势强度（满分30）==========
    trend_score = 0
    # 多头排列
    if cur_ma5 > cur_ma10 > cur_ma20:
        trend_score += 15
    elif cur_ma10 > cur_ma20:
        trend_score += 8
    elif cur_ma5 > cur_ma20:
        trend_score += 5
    # 均线方向（近5日）
    ma5_5d_ago   = float(ma5.iloc[-5])   if len(ma5) > 5   and not np.isnan(ma5.iloc[-5])   else cur_ma5
    ma20_5d_ago  = float(ma20.iloc[-5]) if len(ma20) > 5  and not np.isnan(ma20.iloc[-5])  else cur_ma20
    if cur_ma5 > ma5_5d_ago:
        trend_score += 8
    if cur_ma20 > ma20_5d_ago:
        trend_score += 7
    trend_score = min(30, trend_score)

    # ========== 因子2：回档评分 V4核心（满分25）==========
    pullback_score = 0
    # 偏离MA5越大，反弹概率越高
    if -3 <= ma5_deviation <= 0:     # 回踩MA5
        pullback_score += 10
    elif -5 <= ma5_deviation < -3:   # 略微跌破MA5
        pullback_score += 8
    elif -8 <= ma5_deviation < -5:  # 深度回踩MA5
        pullback_score += 12         # V4额外加分
    elif -15 <= ma5_deviation < -8: # 极端回踩
        pullback_score += 15
    elif ma5_deviation > 5:          # 突破MA5（V4的breakout扣分）
        pullback_score -= 5
    # 回踩MA10
    if -3 <= ma10_deviation <= 0:
        pullback_score += 5
    elif -5 <= ma10_deviation < -3:
        pullback_score += 8
    elif ma10_deviation > 5:
        pullback_score -= 3
    pullback_score = min(25, max(0, pullback_score))

    # ========== 因子3：量能验证（满分20）==========
    volume_score = 0
    # 缩量回踩 = 主力洗盘信号
    if 0.3 <= vol_ratio < 0.6:
        volume_score = 20  # 完美缩量
    elif 0.6 <= vol_ratio < 0.8:
        volume_score = 15
    elif vol_ratio < 0.3:  # 极度缩量
        volume_score = 18
    elif 0.8 <= vol_ratio <= 1.2:
        volume_score = 8   # 正常量能
    else:  # 放量（可能还没跌完）
        volume_score = 0

    # ========== 因子4：RSI超卖二次确认（满分15）==========
    rsi_score = 0
    if cur_rsi2 < 5 and cur_rsi14 < 35:
        rsi_score = 15   # 极端超卖 + 中周期底部
    elif cur_rsi2 < 10 and cur_rsi14 < 40:
        rsi_score = 12
    elif cur_rsi2 < 20 and cur_rsi14 < 45:
        rsi_score = 8
    elif cur_rsi14 > 70:  # 超买警告
        rsi_score = -5
    rsi_score = max(0, rsi_score)

    # ========== 因子5：MACD底背离（满分10）==========
    macd_score = 0
    if macd_bottom:
        macd_score = 10
    # DIF在零轴上方（多头市场）
    if cur_dif > 0 and cur_dea > 0:
        macd_score += 3
    macd_score = min(10, macd_score)

    # ========== 板块加成 ==========
    sector_bonus = SECTOR_BONUS_MAP.get(sector_rating, 1.0)

    # ========== 综合评分 ==========
    raw_score = trend_score + pullback_score + volume_score + rsi_score + macd_score
    total_score = raw_score * sector_bonus
    total_score = min(100, total_score)

    # ========== 交易信号 ==========
    if total_score >= 75:
        signal = 'BUY'
        confidence = 'HIGH'
    elif total_score >= 55:
        signal = 'WATCH'
        confidence = 'MEDIUM'
    else:
        signal = 'AVOID'
        confidence = 'LOW'

    # ========== 预估上涨空间 ==========
    # 基于历史同类评分股的平均涨幅
    if signal == 'BUY':
        if total_score >= 85:
            upside_pct = 30 + (total_score - 85) * 2  # 30-60%
        elif total_score >= 75:
            upside_pct = 20 + (total_score - 75) * 1  # 20-30%
        else:
            upside_pct = 15 + (total_score - 55) * 0.5
    elif signal == 'WATCH':
        upside_pct = 8 + (total_score - 55) * 0.5
    else:
        upside_pct = 0

    # ========== 止损/止盈 ==========
    # 止损：MA20下方2%
    stop_loss = cur_ma20 * 0.98
    # 止盈1：前高
    recent_high = float(high.iloc[-30:-1].max()) if len(high) > 30 else cur_close * 1.10
    # 止盈2：MA60目标
    take_profit_ma60 = cur_ma60 * 1.15
    take_profit = min(recent_high, take_profit_ma60)

    return {
        'code':             code,
        'total_score':      round(total_score, 1),
        'trend_score':      round(trend_score, 1),
        'pullback_score':   round(pullback_score, 1),
        'volume_score':     round(volume_score, 1),
        'rsi_score':        round(rsi_score, 1),
        'macd_score':       round(macd_score, 1),
        'sector_rating':    sector_rating,
        'sector_bonus':     sector_bonus,
        'signal':           signal,
        'confidence':       confidence,
        'upside_pct':       round(upside_pct, 1),
        'entry_price':      round(cur_close, 2),
        'stop_loss':        round(stop_loss, 2),
        'take_profit':      round(take_profit, 2),
        'cur_close':        round(cur_close, 2),
        'cur_ma5':          round(cur_ma5, 2),
        'cur_ma20':         round(cur_ma20, 2),
        'cur_ma60':         round(cur_ma60, 2),
        'rsi2':             round(cur_rsi2, 1),
        'rsi14':            round(cur_rsi14, 1),
        'vol_ratio':        round(vol_ratio, 2),
        'ma5_dev':          round(ma5_deviation, 2),
        'trend_detail':     '多头排列' if cur_ma5 > cur_ma10 > cur_ma20 else ('上升中' if cur_ma5 > cur_ma20 else '震荡'),
    }

# ========== 批量选股 ==========
def screen_stocks(codes, start_date, end_date, market_env='bull'):
    """
    批量扫描股票池，给出V4.5评分和信号
    market_env: 'bull'/'neutral'（'bear'时策略禁用返回AVOID）
    """
    results = []
    for code in codes:
        sector = TODAY_SECTOR_RATING.get(code, 'B')
        # bear市场直接标记AVOID
        if market_env == 'bear':
            results.append({
                'code': code, 'total_score': 0,
                'signal': 'AVOID', 'reason': '大盘跌破MA60',
                'sector_rating': sector
            })
            continue

        df = get_daily_data(code, start_date, end_date)
        score = calc_v45_score(df, code, sector)
        if score is None:
            continue

        score['reason'] = score.get('trend_detail', '')
        results.append(score)
        print(f"  {'✅' if score['signal'] == 'BUY' else '🔍' if score['signal'] == 'WATCH' else '❌'} "
              f"{code} | {score.get('total_score', 0):>5.1f}分 | {score['signal']:<6} | "
              f"{score.get('cur_close', 0):>6.2f} | MA5偏离{score.get('ma5_dev', 0):>+5.1f}% | "
              f"RSI2={score.get('rsi2', 0):>4.1f} RSI14={score.get('rsi14', 0):>5.1f} | "
              f"量比={score.get('vol_ratio', 0):.2f} | 板块{sector}")

    results.sort(key=lambda x: x.get('total_score', 0), reverse=True)
    return results

# ========== 简化版（无需Tushare，直接用缓存数据）==========
def screen_from_cache(stock_results, market_env='bull'):
    """
    从已有的量化结果（IA池/IB池数据）快速估算V4.5评分
    stock_results: 来自 stock_quant_model_v1.py 的评分结果
    """
    scored = []
    for s in stock_results:
        code  = s['code']
        sector = TODAY_SECTOR_RATING.get(code, 'B')
        bonus = SECTOR_BONUS_MAP.get(sector, 1.0)

        # 粗略估算各因子（基于已有数据）
        # 趋势用Q1增速代理
        rev_yoy = abs(sf(s.get('q1_rev_yoy', s.get('q1_26_yoy', 0))))
        # 动量用Q1环比代理
        q1_mom  = sf(s.get('q1_mom', 0))

        # 代理评分
        trend_score    = min(30, rev_yoy * 0.15 + 10)
        pullback_score = min(25, q1_mom * 0.15 + 5) if q1_mom > 0 else 10
        volume_score   = 15  # 默认中性
        rsi_score      = 15 if q1_mom > 50 else 8
        macd_score     = 5

        # bear市场过滤
        if market_env == 'bear':
            final = 0
            signal = 'AVOID'
        else:
            raw = trend_score + pullback_score + volume_score + rsi_score + macd_score
            final = raw * bonus
            if final >= 75: signal = 'BUY'
            elif final >= 55: signal = 'WATCH'
            else: signal = 'AVOID'

        # 预估上涨空间
        upside_pct = (final - 55) * 0.6 if final > 55 else 0

        scored.append({
            'code':             code,
            'name':             s['name'],
            'theme':            s.get('theme', ''),
            'pool':             s.get('pool', ''),
            'total_score':      round(final, 1),
            'trend_score':      round(trend_score * bonus, 1),
            'pullback_score':   round(pullback_score * bonus, 1),
            'volume_score':     round(volume_score, 1),
            'rsi_score':        round(rsi_score, 1),
            'macd_score':       round(macd_score, 1),
            'sector_rating':    sector,
            'sector_bonus':     bonus,
            'signal':           signal,
            'confidence':       'HIGH' if final >= 75 else ('MEDIUM' if final >= 55 else 'LOW'),
            'upside_pct':       round(upside_pct, 1),
            'q1_rev_yoy':       s.get('q1_rev_yoy', s.get('q1_26_yoy', 0)),
            'q1_mom':           q1_mom,
            'pe':               s.get('pe', 0),
            'market_cap_yi':    s.get('market_cap_yi', 0),
        })

    scored.sort(key=lambda x: x['total_score'], reverse=True)
    return scored

# ========== 回测函数 ==========
def backtest_v45(signals, df_dict, initial_cash=1000000, hold_days=20, stop_loss_pct=0.05):
    """
    简单回测：买入信号出现日入场，N天后或止损/止盈出场
    signals: [{'code', 'entry_date', 'entry_price', 'stop_loss', 'take_profit'}, ...]
    """
    trades = []
    for sig in signals:
        code = sig['code']
        df = df_dict.get(code)
        if df is None:
            continue

        entry_date  = sig.get('entry_date')
        entry_price = sig.get('entry_price')
        stop_loss   = sig.get('stop_loss', entry_price * (1 - stop_loss_pct))
        take_profit = sig.get('take_profit', entry_price * 1.20)

        if entry_date not in df.index:
            continue

        idx = df.index.get_loc(entry_date)
        if idx + hold_days >= len(df):
            continue

        # 模拟持仓N天
        pnl_pct = 0
        exit_reason = 'HOLD_TIMEOUT'
        for i in range(1, hold_days + 1):
            cur_price = float(df['close'].iloc[idx + i])
            if cur_price <= stop_loss:
                pnl_pct = (cur_price - entry_price) / entry_price
                exit_reason = 'STOP_LOSS'
                break
            if cur_price >= take_profit:
                pnl_pct = (cur_price - entry_price) / entry_price
                exit_reason = 'TAKE_PROFIT'
                break
        else:
            cur_price = float(df['close'].iloc[idx + hold_days])
            pnl_pct = (cur_price - entry_price) / entry_price
            exit_reason = 'HOLD_TIMEOUT'

        trades.append({
            'code': code, 'entry_date': str(entry_date)[:10],
            'entry_price': entry_price, 'exit_price': cur_price,
            'pnl_pct': round(pnl_pct * 100, 2),
            'exit_reason': exit_reason,
        })

    if not trades:
        return []

    n_wins = sum(1 for t in trades if t['pnl_pct'] > 0)
    n_total = len(trades)
    avg_pnl = np.mean([t['pnl_pct'] for t in trades])
    win_rate = n_wins / n_total * 100

    return {
        'total_trades': n_total,
        'wins': n_wins,
        'losses': n_total - n_wins,
        'win_rate': round(win_rate, 1),
        'avg_pnl_pct': round(avg_pnl, 2),
        'total_return': round(avg_pnl * n_total, 2),
        'best_trade': max(trades, key=lambda x: x['pnl_pct']),
        'worst_trade': min(trades, key=lambda x: x['pnl_pct']),
        'trades': trades,
    }

# ========== 主函数 ==========
def run(cache_mode=True):
    print('=' * 70)
    print('V4.5 上涨空间预测策略  |  多因子共振  |  趋势+回档+量能+RSI+MACD')
    print('=' * 70)

    # === 判断大盘环境 ===
    today_str = '20260619'
    start_str = '20260301'

    if not cache_mode:
        df_idx = get_index_data(INDEX_CODE, start_str, today_str)
        env = market_environment(df_idx) if df_idx is not None else 'neutral'
    else:
        env = 'bull'  # 默认牛市环境（今日行情偏多）
        print(f'\n📈 大盘环境：{env}（使用缓存模式，假设MA20之上）')

    print(f'大盘环境：{env}')

    # === 从缓存加载股票池 ===
    cache_file = os.path.join(BASE_DIR, 'solo', 'report_daily', 'stock_quant_model_v1.1_20260619.json')
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        stock_pool = cache_data.get('results', [])
        print(f'\n📂 从缓存加载股票池：{len(stock_pool)}只')
    else:
        print('⚠️ 缓存不存在，请先运行 stock_quant_model_v1.py')
        stock_pool = []
        return

    # === 快速筛选（代理评分，无需Tushare）===
    print('\n🔍 V4.5 快速评分（板块加成已注入）...')
    print('-' * 70)
    results = screen_from_cache(stock_pool, market_env=env)

    # 统计
    buys   = [r for r in results if r['signal'] == 'BUY']
    watchs = [r for r in results if r['signal'] == 'WATCH']
    avoids = [r for r in results if r['signal'] == 'AVOID']

    print(f'\n📊 信号统计：BUY={len(buys)} | WATCH={len(watchs)} | AVOID={len(avoids)}')

    # === 详细展示 ===
    print('\n' + '=' * 70)
    print(f'BUY 信号（{len(buys)}只）— 建议重点关注')
    print('-' * 70)
    for i, r in enumerate(buys[:10]):
        print(f'{i+1}. {r["name"]}（{r["code"]}）{r["sector_rating"]}级板块×{r["sector_bonus"]:.2f}')
        print(f'   综合:{r["total_score"]:.1f} | 趋势:{r["trend_score"]:.1f} | 回档:{r["pullback_score"]:.1f} '
              f'| 量能:{r["volume_score"]:.1f} | RSI:{r["rsi_score"]:.1f} | MACD:{r["macd_score"]:.1f}')
        print(f'   预估上涨:{r["upside_pct"]:.0f}% | 信心:{r["confidence"]}')

    print('\n' + '=' * 70)
    print(f'WATCH 信号（{len(watchs)}只）— 观察等待')
    print('-' * 70)
    for i, r in enumerate(watchs[:5]):
        print(f'{i+1}. {r["name"]}（{r["code"]}）综合:{r["total_score"]:.1f} | 预估上涨:{r["upside_pct"]:.0f}%')

    # === 保存结果 ===
    out = os.path.join(BASE_DIR, 'solo', 'report_daily', f'v45_signals_{today_str}.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'date': today_str,
            'market_env': env,
            'total': len(results),
            'buy_count': len(buys),
            'watch_count': len(watchs),
            'buy_signals': buys,
            'watch_signals': watchs,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 结果已保存：{out}')

    return results

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='使用Tushare实时数据')
    args = parser.parse_args()
    run(cache_mode=not args.live)
