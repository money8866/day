"""
信立泰式量能爆发形态选股公式 V2 - 回踩低吸版
============================================
核心改进：信号日不追高，等待T+2缩量回踩均线时低吸买入

策略逻辑：
1. T日（信号日）：量能爆发形态→加入观察池（不买）
2. T+1~T+3日：监控回踩
3. T+2日（最优）：缩量回踩MA5/MA10，小阴小阳，收盘站稳→买入

回测验证（2026.6-7月，87个信号）：
- 追高买入：均亏-5.88%，T+5盈利概率25.3%
- T+2回踩收盘买：6月止盈胜率100%，均亏最小(-2.87%)
- 大盘弱势时全部策略都亏，必须靠过滤器避开
- 动态止盈3%是唯一盈利退出方式

操作规则：
- 大盘弱势（上证跌破MA20）→ 全部停止
- 买入：T+2缩量回踩MA收盘买入
- 止盈：T+5内涨幅≥3%卖出
- 止损：跌破MA20或亏损≥5%
- 超时：T+5未达止盈则卖出
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

WATCHLIST_FILE = r"d:\mystock\cache_daily\VolMaSync_WatchList.json"


def get_market_regime():
    """大盘环境过滤器"""
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
            except Exception as e:
                if idx_df is None or len(idx_df) < 25:
                    return {'allow_trade': True, 'regime': 'unknown', 'reason': f'指数异常，默认放行'}
        
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


def detect_vol_ma_sync_surge(df, target_idx=None):
    """T日量能爆发信号检测（观察信号）"""
    if df is None or len(df) < 80:
        return None
    
    if target_idx is None:
        target_idx = len(df) - 1
    
    start_i = max(0, target_idx - 59)
    seg = df.iloc[start_i:target_idx + 1].copy().reset_index(drop=True)
    if len(seg) < 40:
        return None
    
    close_arr = seg['close'].values.astype(float)
    high_arr = seg['high'].values.astype(float)
    low_arr = seg['low'].values.astype(float)
    vol_arr = seg['vol'].values.astype(float)
    pre_close_arr = seg['pre_close'].values.astype(float)
    
    if len(vol_arr) < 40:
        return None
    pre_vol_20 = float(np.mean(vol_arr[:20]))
    post_vol_20 = float(np.mean(vol_arr[-20:]))
    if pre_vol_20 <= 0:
        return None
    vol_surge_ratio = post_vol_20 / pre_vol_20
    if vol_surge_ratio < 1.5:
        return None
    
    ma20 = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    if len(ma20) < 25:
        return None
    ma20_5ago = ma20[-6] if not np.isnan(ma20[-6]) else None
    ma20_20ago = ma20[-21] if not np.isnan(ma20[-21]) else None
    ma20_now = ma20[-1]
    if ma20_5ago is None or ma20_20ago is None or ma20_5ago <= 0 or ma20_20ago <= 0:
        return None
    ma20_slope_5d = (ma20_now / ma20_5ago - 1) * 100
    ma20_slope_pre = (ma20_5ago / ma20_20ago - 1) * 100
    if not (-3 <= ma20_slope_pre <= 3):
        return None
    if ma20_slope_5d < 2.0:
        return None
    if ma20_slope_5d <= ma20_slope_pre:
        return None
    
    exp12 = pd.Series(close_arr).ewm(span=12, adjust=False).mean().values
    exp26 = pd.Series(close_arr).ewm(span=26, adjust=False).mean().values
    dif = exp12 - exp26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    macd = (dif - dea) * 2
    last_macd = macd[-1]
    prev_macd = macd[-2] if len(macd) > 1 else 0
    last_dif = dif[-1]
    if last_macd <= 0:
        return None
    if last_macd > 0 and prev_macd <= 0:
        macd_status = "刚刚红柱"
    elif last_macd > 0 and prev_macd > 0 and last_macd > prev_macd:
        macd_status = "红柱放大"
    else:
        return None
    
    ma5 = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10 = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    last_close = close_arr[-1]
    if last_close < ma5[-1] or last_close < ma10[-1]:
        return None
    
    dist_ma20 = (last_close / ma20_now - 1) * 100
    if dist_ma20 < 5 or dist_ma20 > 15:
        return None
    
    gains = close_arr[-20:] > pre_close_arr[-20:]
    up_vol = np.mean(vol_arr[-20:][gains]) if gains.sum() > 0 else 0
    down_vol = np.mean(vol_arr[-20:][~gains]) if (~gains).sum() > 0 else 0
    vol_price_coord = up_vol / down_vol if down_vol > 0 else 2.0
    if vol_price_coord < 0.95:
        return None
    
    vol_ma20_arr = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    last_vol_ratio = vol_arr[-1] / max(vol_ma20_arr[-1], 1)
    if last_vol_ratio < 1.0:
        return None
    
    last_chg = (last_close / pre_close_arr[-1] - 1) * 100
    if last_chg < 0:
        return None
    
    score = 0
    if vol_surge_ratio >= 2.5: score += 20
    elif vol_surge_ratio >= 2.0: score += 15
    elif vol_surge_ratio >= 1.8: score += 10
    elif vol_surge_ratio >= 1.5: score += 8
    
    consolidation_quality = max(0, 3 - abs(ma20_slope_pre))
    if ma20_slope_5d < 3: breakout_score = 12
    elif ma20_slope_5d < 5: breakout_score = 6
    else: breakout_score = 3
    score += int(consolidation_quality * 3 + breakout_score)
    
    if macd_status == "刚刚红柱": score += 15
    elif macd_status == "红柱放大": score += 12
    if last_dif > 0: score += 5
    
    if 1.5 <= vol_price_coord < 2.0: score += 15
    elif vol_price_coord >= 2.0: score += 12
    elif vol_price_coord >= 1.2: score += 8
    else: score += 10
    
    if 12 <= dist_ma20 <= 14: score += 15
    elif 10 <= dist_ma20 < 12: score += 12
    elif 14 < dist_ma20 <= 15: score += 8
    elif 5 <= dist_ma20 < 8: score += 7
    else: score += 5
    
    if 5 <= last_chg <= 7: score += 12
    elif 7 < last_chg <= 10: score += 10
    elif 3 <= last_chg < 5: score += 7
    elif 1 <= last_chg < 3: score += 8
    else: score += 6
    
    return {
        'vol_surge_ratio': round(float(vol_surge_ratio), 2),
        'ma20_slope_5d': round(float(ma20_slope_5d), 2),
        'ma20_slope_pre': round(float(ma20_slope_pre), 2),
        'macd_status': macd_status,
        'dif_above_zero': bool(last_dif > 0),
        'vol_price_coord': round(float(vol_price_coord), 2),
        'last_vol_ratio': round(float(last_vol_ratio), 2),
        'dist_ma20': round(float(dist_ma20), 2),
        'last_chg': round(float(last_chg), 2),
        'score': int(score),
        'close': round(float(last_close), 2),
        'signal_vol': float(vol_arr[-1]),
        'ma5': float(ma5[-1]),
        'ma10': float(ma10[-1]),
        'ma20': float(ma20_now),
    }


def detect_pullback_buy(df, signal_info, signal_date_str, today_str):
    """检测今天是否是T+N回踩低吸买点
    
    回测最优条件：
    - 缩量：今天量 < 信号日量 * 0.7
    - 最低价触及MA5或MA10（误差±2-3%）
    - 不跌破MA20（最低价 > MA20*0.98）
    - 小阴小阳/十字星（涨跌幅-3.5%~+2.5%）
    - 收盘站稳均线
    
    T+2回踩是最优买点（6月100%止盈胜率）
    """
    if df is None or len(df) < 80:
        return None
    
    df = df.reset_index(drop=True)
    df['trade_date'] = df['trade_date'].astype(str)
    
    mask_sig = df['trade_date'] == signal_date_str
    mask_today = df['trade_date'] == today_str
    if not mask_sig.any() or not mask_today.any():
        return None
    
    sig_idx = df.index[mask_sig][0]
    today_idx = df.index[mask_today][0]
    
    offset = today_idx - sig_idx
    if offset < 1 or offset > 3:
        return None
    
    close_arr = df['close'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    vol_arr = df['vol'].values.astype(float)
    pre_close_arr = df['pre_close'].values.astype(float)
    
    ma5_arr = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10_arr = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    ma20_arr = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    
    signal_vol = signal_info.get('signal_vol', vol_arr[sig_idx])
    c = close_arr[today_idx]
    l = low_arr[today_idx]
    v = vol_arr[today_idx]
    pct = (c / pre_close_arr[today_idx] - 1) * 100
    
    ma5 = ma5_arr[today_idx]
    ma10 = ma10_arr[today_idx]
    ma20 = ma20_arr[today_idx]
    
    # 条件1：缩量
    vol_shrink = v < signal_vol * 0.7
    if not vol_shrink:
        return None
    
    # 条件2：回踩到MA5或MA10附近
    touch_ma5 = l <= ma5 * 1.02 and l >= ma5 * 0.97
    touch_ma10 = l <= ma10 * 1.03 and l >= ma10 * 0.96
    if not (touch_ma5 or touch_ma10):
        return None
    
    # 条件3：不跌破MA20
    if l <= ma20 * 0.98:
        return None
    
    # 条件4：小阴小阳/十字星（不能是大阳线追高，不能是大阴线破位）
    if not (-3.5 <= pct <= 2.5):
        return None
    
    # 条件5：收盘站稳（收盘价在MA5或MA10上方附近）
    close_support = c >= ma5 * 0.98 or c >= ma10 * 0.98
    if not close_support:
        return None
    
    ma_level = 'MA5' if touch_ma5 else 'MA10'
    
    # 评分：T+2最优
    timing_score = {1: 70, 2: 100, 3: 80}.get(offset, 50)
    
    # 回踩质量评分
    quality = 0
    if ma_level == 'MA5': quality += 30  # 回踩MA5更强
    elif ma_level == 'MA10': quality += 20
    vol_ratio = v / signal_vol
    if vol_ratio < 0.5: quality += 25  # 极度缩量最优
    elif vol_ratio < 0.6: quality += 20
    elif vol_ratio < 0.7: quality += 10
    if -1 <= pct <= 1: quality += 20  # 十字星最优
    elif -2 <= pct <= 2: quality += 10
    dist_ma5_pct = (c / ma5 - 1) * 100
    if abs(dist_ma5_pct) < 1: quality += 25  # 紧贴MA5最优
    elif abs(dist_ma5_pct) < 2: quality += 15
    
    buy_score = int(timing_score * 0.4 + quality * 0.6)
    
    return {
        'buy_offset': offset,
        'buy_date': today_str,
        'buy_price': round(c, 2),
        'pct_on_buy_day': round(pct, 2),
        'vol_ratio': round(vol_ratio, 2),
        'touch_ma': ma_level,
        'dist_ma5': round((c / ma5 - 1) * 100, 2),
        'dist_ma10': round((c / ma10 - 1) * 100, 2),
        'buy_score': buy_score,
    }


def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_watchlist(watchlist):
    with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)


def daily_scan(observe_threshold=75, buy_threshold=60):
    """日常选股扫描 - V2双池版
    
    Returns:
        tuple: (观察池列表, 买入池列表)
    """
    today = str(tq.TRADE_DATE)
    
    print("=" * 65)
    print("信立泰式量能爆发选股 V2（T日观察+T+2回踩低吸）")
    print("=" * 65)
    print(f"交易日: {today}")
    print("策略：信号日观察不追高，等待T+2缩量回踩MA买入")
    print("=" * 65)
    
    # 大盘过滤
    print("\n【大盘环境】")
    regime = get_market_regime()
    print(f"  {regime['regime']} | {regime['reason']}")
    
    if not regime['allow_trade']:
        print(f"\n🛑 弱势市场停止选股（回测7月均亏-12%）")
        print("   建议空仓等待上证重新站上MA20")
        return [], []
    
    pos_pct = {'bull': '6成', '震荡': '3成'}.get(regime['regime'], '3成')
    if regime['regime'] == '震荡':
        observe_threshold = max(observe_threshold, 80)
        buy_threshold = max(buy_threshold, 70)
    
    # 加载观察池
    watchlist = load_watchlist()
    print(f"\n【观察池】已加载 {len(watchlist)} 只待回踩股票")
    
    all_stocks = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
    all_stocks = [c for c in all_stocks if not (c.startswith('8') or c.startswith('4') or c.startswith('9'))]
    print(f"待扫描股票: {len(all_stocks)}")
    print()
    
    new_observations = []  # 今日新信号
    buy_signals = []       # 今日回踩买点
    scanned = 0
    t0 = time.time()
    
    for ts_code in all_stocks:
        scanned += 1
        if scanned % 1000 == 0:
            elapsed = time.time() - t0
            print(f"  进度: {scanned}/{len(all_stocks)}, 新观察{len(new_observations)}只, 买点{len(buy_signals)}只, {elapsed:.0f}s")
        
        try:
            stock_df = tq.get_hist_data(ts_code)
            if stock_df is None or len(stock_df) < 80:
                continue
            
            name = tq.get_stock_name(ts_code) if hasattr(tq, 'get_stock_name') else ts_code
            
            # 1. 检查观察池中的股票今天是否出现回踩买点
            if ts_code in watchlist:
                for sig_date, sig_info in list(watchlist[ts_code].items()):
                    pb = detect_pullback_buy(stock_df, sig_info, sig_date, today)
                    if pb:
                        pb['code'] = ts_code
                        pb['name'] = name
                        pb['signal_date'] = sig_date
                        pb['signal_score'] = sig_info.get('score', 0)
                        pb['signal_close'] = sig_info.get('close', 0)
                        buy_signals.append(pb)
            
            # 2. 检测今日是否有新的量能爆发信号（加入观察池）
            result = detect_vol_ma_sync_surge(stock_df)
            if result and result['score'] >= observe_threshold:
                # 检查观察池中是否已有今天的记录
                if ts_code not in watchlist:
                    watchlist[ts_code] = {}
                watchlist[ts_code][today] = result
                new_observations.append({
                    'code': ts_code, 'name': name,
                    'score': result['score'],
                    'vol_surge': result['vol_surge_ratio'],
                    'dist_ma20': result['dist_ma20'],
                    'ma20_slope_5d': result['ma20_slope_5d'],
                    'last_chg': result['last_chg'],
                    'close': result['close'],
                    'macd': result['macd_status'],
                })
        
        except Exception:
            pass
    
    # 清理观察池：移除超过5天未触发买点的信号
    cleaned = {}
    for code, dates in watchlist.items():
        kept = {}
        for d, info in dates.items():
            try:
                from datetime import datetime
                d_dt = datetime.strptime(d, '%Y%m%d')
                t_dt = datetime.strptime(today, '%Y%m%d')
                if (t_dt - d_dt).days <= 5:
                    kept[d] = info
            except Exception:
                kept[d] = info
        if kept:
            cleaned[code] = kept
    watchlist = cleaned
    save_watchlist(watchlist)
    
    elapsed = time.time() - t0
    print(f"\n扫描完成: {scanned}只, 耗时{elapsed:.0f}s")
    
    # ========== 输出买点（优先展示）==========
    buy_signals.sort(key=lambda x: -x['buy_score'])
    
    print("\n" + "=" * 65)
    print(f"🟢 【今日回踩买点】{len(buy_signals)}只（T+N缩量回踩MA低吸）")
    print("=" * 65)
    
    if buy_signals:
        print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'买点评分':<8}{'信号日':<10}{'距T':<5}{'回踩':<5}{'买价':<8}{'量缩':<7}{'当日涨跌':<9}")
        print("-" * 80)
        for i, s in enumerate(buy_signals[:15], 1):
            tag = "⭐" if s['buy_score'] >= 80 else ("✓" if s['buy_score'] >= 65 else "△")
            print(f"{i:<4}{s['code']:<12}{str(s['name'])[:8]:<10}{s['buy_score']:<8}{s['signal_date']:<10}"
                  f"T+{s['buy_offset']:<4}{s['touch_ma']:<5}{s['buy_price']:<8.2f}{s['vol_ratio']:<7.2f}"
                  f"{s['pct_on_buy_day']:<+7.2f}% {tag}")
    else:
        print("  今日无回踩买点")
    
    # ========== 输出观察池（今日新信号）==========
    new_observations.sort(key=lambda x: -x['score'])
    
    print("\n" + "=" * 65)
    print(f"👁  【今日新观察信号】{len(new_observations)}只（等待T+2回踩，不追高）")
    print("=" * 65)
    
    if new_observations:
        print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'评分':<6}{'量能':<7}{'MA20斜率':<10}{'距MA20':<9}{'MACD':<10}{'当日涨幅':<8}")
        print("-" * 85)
        for i, s in enumerate(new_observations[:15], 1):
            tag = "⭐" if s['score'] >= 85 else ("✓" if s['score'] >= 80 else "△")
            print(f"{i:<4}{s['code']:<12}{str(s['name'])[:8]:<10}{s['score']:<6}{s['vol_surge']:<7.2f}"
                  f"{s['ma20_slope_5d']:<+6.2f}%   {s['dist_ma20']:<+7.2f}%  "
                  f"{s['macd']:<10}{s['last_chg']:<+6.2f}% {tag}")
    else:
        print("  今日无新量能爆发信号")
    
    # 观察池待回踩统计
    pending_count = sum(len(dates) for dates in watchlist.values())
    print(f"\n📋 观察池总计: {pending_count}只股票等待回踩（含今日新增）")
    
    # ========== 操作建议 ==========
    print("\n" + "=" * 65)
    print("【操作建议】")
    print("=" * 65)
    print(f"1. 市场环境: {regime['regime']}（{regime['reason']}），建议仓位{pos_pct}")
    print("2. 🟢买点池股票：今日收盘前可低吸，单只不超过1.5成")
    print("3. 👁观察池股票：不追高，等T+2缩量回踩MA5/MA10再买")
    print("4. 止盈：T+5内涨幅≥3%卖出（动态止盈）")
    print("5. 止损：跌破MA20或亏损≥5%立即卖出")
    print("6. 超时：T+5未达止盈目标则收盘卖出")
    print("7. ⭐为最优标的（评分>=80），✓为次优，△为观察")
    
    # 保存结果
    if buy_signals:
        pd.DataFrame(buy_signals).to_csv(
            rf"d:\mystock\cache_daily\VolMaSync_BuySignals_{today}.csv",
            index=False, encoding='utf-8-sig')
    if new_observations:
        pd.DataFrame(new_observations).to_csv(
            rf"d:\mystock\cache_daily\VolMaSync_NewObservations_{today}.csv",
            index=False, encoding='utf-8-sig')
    
    print(f"\n✅ 观察池已更新: {WATCHLIST_FILE}")
    return new_observations, buy_signals


if __name__ == "__main__":
    daily_scan()
