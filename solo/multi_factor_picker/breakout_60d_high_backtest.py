# -*- coding: utf-8 -*-
"""
双创板60日新高突破策略回测
条件：一波拉升>20% → 调整 → 突破创60日新高
验证：stk_factor_pro多指标共振对胜率的提升
"""
import os, sys, time, datetime, json
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import numpy as np
import tushare as ts
from typing import Optional, Literal

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 参数
SURGE_DAYS = 20
SURGE_MIN = 0.20
ADJUST_MAX = 60
LOOKBACK = 250
FORWARD = 20
MIN_FORWARD_DAYS = 5

# 回测区间
START_DATE = '20240101'
END_DATE = '20260620'

# ────────────────────────────────────────────────────────────────
def load_gem_kc_pool() -> list:
    """获取双创板股票池"""
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,list_date')
        cy = sb[sb['ts_code'].str.startswith(('300', '688'))].copy()
        # 过滤上市不足60天的
        cutoff = (datetime.date.today() - datetime.timedelta(days=60)).strftime('%Y%m%d')
        cy = cy[cy['list_date'] < cutoff]
        return cy['ts_code'].tolist()
    except Exception:
        return []


def get_price_data(ts_code: str) -> Optional[pd.DataFrame]:
    """获取stk_factor_pro数据"""
    try:
        df = pro.stk_factor_pro(ts_code=ts_code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 100:
            return None
        df = df.sort_values('trade_date').reset_index(drop=True)
        time.sleep(0.12)
        return df
    except Exception:
        return None


def calculate_resonance_score(row: pd.Series, prev_row: Optional[pd.Series] = None) -> dict:
    """计算多指标共振评分"""
    score = 0
    details = []

    def v(col, default=0.0):
        val = row.get(col, default)
        return float(val) if not pd.isna(val) else default

    # ── 动量类 ──
    rsi = v('rsi_qfq_6', 50)
    if rsi < 30: score += 3; details.append(f'RSI={rsi:.0f}<30超卖')
    elif rsi < 40: score += 2; details.append(f'RSI={rsi:.0f}<40偏低')
    elif rsi < 50: score += 1

    kdj_j = v('kdj_qfq', 50)
    if kdj_j < 0: score += 3; details.append(f'KDJ-J={kdj_j:.0f}<0极度超卖')
    elif kdj_j < 20: score += 2; details.append(f'KDJ-J={kdj_j:.0f}<20超卖')

    cci = v('cci_qfq', 0)
    if cci < -100: score += 2; details.append(f'CCI={cci:.0f}<-100超卖')

    wr = v('wr_qfq', 50)
    if wr > 80: score += 2; details.append(f'WR={wr:.0f}>80超卖')

    # ── 资金类 ──
    mfi = v('mfi_qfq', 50)
    if mfi < 30: score += 1; details.append(f'MFI={mfi:.0f}<30资金偏弱')

    vol_ratio = v('volume_ratio', 1.0)
    if vol_ratio > 1.5: score += 2; details.append(f'量比={vol_ratio:.2f}>1.5放量突破')
    elif vol_ratio > 1.2: score += 1; details.append(f'量比={vol_ratio:.2f}>1.2温和放量')

    # ── 趋势类 ──
    macd_dif = v('macd_dif_qfq', 0)
    macd_dea = v('macd_dea_qfq', 0)
    if macd_dif > macd_dea:
        score += 2; details.append('MACD金叉')
    # 检测MACD即将金叉（DIF上穿DEA前夜）
    if prev_row is not None:
        prev_dif = float(prev_row.get('macd_dif_qfq', 0))
        prev_dea = float(prev_row.get('macd_dea_qfq', 0))
        if prev_dif <= prev_dea and macd_dif > macd_dea:
            score += 2; details.append('MACD当日金叉!!')

    # DMI趋势
    pdi = v('dmi_pdi_qfq', 20)
    mdi = v('dmi_mdi_qfq', 20)
    adx = v('dmi_adx_qfq', 20)
    if pdi > mdi:
        score += 1; details.append(f'PDI({pdi:.0f})>MDI({mdi:.0f})多头')
    if adx > 25:
        score += 1; details.append(f'ADX={adx:.0f}>25强趋势')

    # MA位置
    close = v('close', 0)
    ma5 = v('ma_qfq_5', 0)
    ma20 = v('ma_qfq_20', 0)
    ma60 = v('ma_qfq_60', 0)
    if close > ma5 and ma5 > 0:
        score += 1
    if close > ma20 and ma20 > 0:
        score += 1; details.append('MA20上方')
    if close > ma60 and ma60 > 0:
        score += 1; details.append('MA60上方')

    # ── 情绪类 ──
    bias1 = v('bias1_qfq', 0)
    if bias1 < -5: score += 2; details.append(f'BIAS1={bias1:.1f}%<-5%超卖')

    return {'score': score, 'details': '; '.join(details)}


def detect_60d_high_breakout(ts_code: str) -> list:
    """
    检测60日新高突破信号
    返回：信号列表（每个信号包含评分、后续涨幅等）
    """
    df = get_price_data(ts_code)
    if df is None or len(df) < LOOKBACK:
        return []

    closes = df['close'].values
    n = len(df)
    signals = []

    # 从第60天开始扫描
    for i in range(60, n - MIN_FORWARD_DAYS):
        # 1. 检测60日新高突破
        window_60 = closes[i-60:i]
        if len(window_60) == 0:
            continue
        high_60 = window_60.max()
        if closes[i] <= high_60:
            continue  # 未突破60日高点

        # 2. 检测前期是否有一波拉升
        # 向前找wave1高点（3-150天前）
        wave1_found = False
        for lookback in range(3, min(150, i)):
            end_idx = i - lookback
            if end_idx < SURGE_DAYS:
                continue
            # 检测一波拉升
            window = closes[end_idx - SURGE_DAYS:end_idx + 1]
            low_in_win = np.argmin(window)
            high_in_win = np.argmax(window)
            if high_in_win <= low_in_win:
                continue
            surge_gain = (window[high_in_win] - window[low_in_win]) / window[low_in_win]
            if surge_gain < SURGE_MIN:
                continue

            wave1_high_idx = end_idx - SURGE_DAYS + high_in_win
            wave1_high = closes[wave1_high_idx]

            # 3. 检测调整期（wave1高点到当前突破）
            if i - wave1_high_idx > ADJUST_MAX or i - wave1_high_idx < 3:
                continue

            # 调整期最低点
            adjust_low = closes[wave1_high_idx:i].min()
            pullback_pct = (wave1_high - adjust_low) / wave1_high

            # 4. 验证：当前突破是否突破wave1高点
            if closes[i] <= wave1_high:
                continue  # 未突破wave1高点

            wave1_found = True

            # ── 计算多指标共振评分 ──
            prev_row = df.iloc[i-1] if i > 0 else None
            score_result = calculate_resonance_score(df.iloc[i], prev_row)

            # ── 计算后续涨幅 ──
            entry_price = closes[i]
            future_gains = {}
            for fwd in [5, 10, 20]:
                if i + fwd < n:
                    future_gains[f'gain_{fwd}d'] = (closes[i + fwd] - entry_price) / entry_price * 100
                else:
                    future_gains[f'gain_{fwd}d'] = None

            # 最大涨幅和最大回撤
            if i + FORWARD < n:
                future_window = closes[i:i+FORWARD]
                max_gain = (future_window.max() - entry_price) / entry_price * 100
                max_drawdown = (entry_price - future_window.min()) / entry_price * 100
            else:
                max_gain = max_drawdown = None

            # ATR止损距离
            atr = float(df.iloc[i].get('atr_qfq', 0))
            stop_atr = round((entry_price - 2 * atr) / entry_price * 100, 1) if atr > 0 else None

            signals.append({
                'ts_code': ts_code,
                'trade_date': df.iloc[i]['trade_date'],
                'entry_price': round(entry_price, 2),
                'wave1_gain': round(surge_gain * 100, 1),
                'pullback_pct': round(pullback_pct * 100, 1),
                'adjust_days': i - wave1_high_idx,
                'breakout_gain': round((closes[i] - wave1_high) / wave1_high * 100, 1),
                'score': score_result['score'],
                'score_details': score_result['details'],
                'rsi': round(float(df.iloc[i].get('rsi_qfq_6', 50)), 1),
                'volume_ratio': round(float(df.iloc[i].get('volume_ratio', 1.0)), 2),
                'macd_dif': round(float(df.iloc[i].get('macd_dif_qfq', 0)), 2),
                'macd_dea': round(float(df.iloc[i].get('macd_dea_qfq', 0)), 2),
                'adx': round(float(df.iloc[i].get('dmi_adx_qfq', 0)), 1),
                'atr_stop_pct': stop_atr,
                'gain_5d': round(future_gains.get('gain_5d'), 2) if future_gains.get('gain_5d') else None,
                'gain_10d': round(future_gains.get('gain_10d'), 2) if future_gains.get('gain_10d') else None,
                'gain_20d': round(future_gains.get('gain_20d'), 2) if future_gains.get('gain_20d') else None,
                'max_gain': round(max_gain, 2) if max_gain else None,
                'max_drawdown': round(max_drawdown, 2) if max_drawdown else None,
            })
            break  # 只记录最近的wave1

        if wave1_found:
            continue

    return signals


# ────────────────────────────────────────────────────────────────
def main():
    print('=' * 70)
    print('  双创板60日新高突破策略回测')
    print('  回测区间: 2024-01-01 ~ 2026-06-20')
    print('=' * 70)

    # 获取股票池
    pool = load_gem_kc_pool()
    print(f'\n双创板股票池: {len(pool)} 只')
    if not pool:
        print('股票池为空，退出')
        return

    # 批量扫描
    all_signals = []
    total = len(pool)
    t0 = time.time()

    print(f'\n开始扫描...')
    for idx, code in enumerate(pool):
        if (idx + 1) % 50 == 0 or idx == 0:
            eta = (time.time() - t0) / max(idx + 1, 1) * (total - idx - 1) if idx > 0 else 0
            print(f'  进度 {idx+1}/{total} ({code})  ETA {eta:.0f}s')

        signals = detect_60d_high_breakout(code)
        if signals:
            all_signals.extend(signals)

    elapsed = time.time() - t0
    print(f'\n扫描完成！耗时 {elapsed:.1f}s，找到 {len(all_signals)} 个信号')

    if not all_signals:
        print('未找到信号')
        return

    # 保存结果
    df = pd.DataFrame(all_signals)

    # ── 统计分析 ──
    print('\n' + '=' * 70)
    print('  回测统计结果')
    print('=' * 70)

    # 总体胜率
    df['win_5d'] = df['gain_5d'] > 0
    df['win_10d'] = df['gain_10d'] > 0
    df['win_20d'] = df['gain_20d'] > 0

    print(f'\n总体统计 (样本数: {len(df)})')
    print(f'  5日胜率: {df["win_5d"].mean()*100:.1f}%  均涨{df["gain_5d"].mean():.2f}%')
    print(f'  10日胜率: {df["win_10d"].mean()*100:.1f}%  均涨{df["gain_10d"].mean():.2f}%')
    print(f'  20日胜率: {df["win_20d"].mean()*100:.1f}%  均涨{df["gain_20d"].mean():.2f}%')
    print(f'  最大涨幅均值: {df["max_gain"].mean():.2f}%')
    print(f'  最大回撤均值: {df["max_drawdown"].mean():.2f}%')

    # ── 按评分分层统计 ──
    print('\n--- 按评分分层统计 ---')
    df['score_tier'] = pd.cut(df['score'], bins=[-1, 5, 10, 15, 100],
                               labels=['0-5分', '6-10分', '11-15分', '16+分'])

    tier_stats = df.groupby('score_tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate_10d=('win_10d', 'mean'),
        avg_gain_10d=('gain_10d', 'mean'),
        avg_gain_20d=('gain_20d', 'mean'),
        avg_max_gain=('max_gain', 'mean'),
        avg_drawdown=('max_drawdown', 'mean'),
    ).reset_index()

    tier_stats['win_rate_10d'] = (tier_stats['win_rate_10d'] * 100).round(1)
    tier_stats['avg_gain_10d'] = tier_stats['avg_gain_10d'].round(2)
    tier_stats['avg_gain_20d'] = tier_stats['avg_gain_20d'].round(2)
    tier_stats['avg_max_gain'] = tier_stats['avg_max_gain'].round(2)
    tier_stats['avg_drawdown'] = tier_stats['avg_drawdown'].round(2)

    print(tier_stats.to_string(index=False))

    # ── 按指标组合统计 ──
    print('\n--- 关键指标组合胜率 ---')

    # 组合1：MACD金叉 + ADX>25
    combo1 = df[(df['macd_dif'] > df['macd_dea']) & (df['adx'] > 25)]
    if len(combo1) > 0:
        print(f'\n组合1: MACD金叉 + ADX>25 ({len(combo1)}个)')
        print(f'  10日胜率: {combo1["win_10d"].mean()*100:.1f}%  均涨{combo1["gain_10d"].mean():.2f}%')

    # 组合2：量比>1.5 + MACD金叉
    combo2 = df[(df['volume_ratio'] > 1.5) & (df['macd_dif'] > df['macd_dea'])]
    if len(combo2) > 0:
        print(f'\n组合2: 量比>1.5 + MACD金叉 ({len(combo2)}个)')
        print(f'  10日胜率: {combo2["win_10d"].mean()*100:.1f}%  均涨{combo2["gain_10d"].mean():.2f}%')

    # 组合3：RSI<50 + MACD金叉 + ADX>25
    combo3 = df[(df['rsi'] < 50) & (df['macd_dif'] > df['macd_dea']) & (df['adx'] > 25)]
    if len(combo3) > 0:
        print(f'\n组合3: RSI<50 + MACD金叉 + ADX>25 ({len(combo3)}个)')
        print(f'  10日胜率: {combo3["win_10d"].mean()*100:.1f}%  均涨{combo3["gain_10d"].mean():.2f}%')

    # 组合4：评分>=15分
    combo4 = df[df['score'] >= 15]
    if len(combo4) > 0:
        print(f'\n组合4: 多指标共振>=15分 ({len(combo4)}个)')
        print(f'  10日胜率: {combo4["win_10d"].mean()*100:.1f}%  均涨{combo4["gain_10d"].mean():.2f}%')
        print(f'  20日胜率: {combo4["win_20d"].mean()*100:.1f}%  均涨{combo4["gain_20d"].mean():.2f}%')

    # ── 保存CSV ──
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = f'{out_dir}\\breakout_60d_high_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')

    # ── TOP30信号展示 ──
    print('\n--- TOP30信号（按评分排序）---')
    top30 = df.sort_values('score', ascending=False).head(30)
    for _, r in top30.iterrows():
        win = '✅' if r['win_10d'] else '❌'
        print(f"{r['ts_code']:<12} {r['trade_date']} 评分{r['score']:>2}分 "
              f"一波+{r['wave1_gain']:>5.1f}% 调整{r['adjust_days']:>2}天 "
              f"突破+{r['breakout_gain']:>4.1f}% 量比{r['volume_ratio']:>4.2f} "
              f"10日{r['gain_10d']:>6.2f}% {win}")


if __name__ == '__main__':
    main()
