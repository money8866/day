"""
信立泰式量能爆发形态选股公式 - 日常选股版
=====================================
基于002294.SZ在20260715的形态特征：
- 量能明显放大（近20日均量/前20日均量>=1.5倍）
- 股价和均线同步上升（MA20横盘后突破）
- MACD红柱放大
- 站上MA5和MA10
- 距MA20在5%-15%

近一个月回测（87个信号，2026.6-7月）：
- 总体T+5动态止盈胜率67.6%（最大涨幅>=3%）
- 6月（震荡市）：止盈胜率76.2%，平均收益-0.83%
- 7月（弱势市）：止盈胜率53.8%，平均收益-12.35%
- 最优组合：排除弱势市场+距MA20[10-14] 止盈胜率82.8%

改进措施：
- 增加大盘环境过滤器（上证MA20择时），弱势市场停止选股
- 修正评分权重倒挂：距MA20[10-14]权重提升至12分
- 震荡市场自动提高评分阈值至80分

操作建议：
- 必须动态止盈（T+5内涨幅达3%即卖出）
- 弱势市场停止选股
- 评分>=75分（震荡市>=80）才入选
"""
import sys, os, time
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


def get_market_regime():
    """大盘环境过滤器：基于上证指数判断市场状态
    
    返回:
        dict: {
            'allow_trade': bool,  # 是否允许选股
            'regime': str,        # 'bull'|'震荡'|'bear'
            'sh_above_ma20': bool,
            'sh_chg_5d': float,  # 上证近5日累计涨幅
            'reason': str
        }
    """
    try:
        idx_code = '000001.SH'
        cache_file = os.path.join(tq.CACHE_DIR, f"{idx_code}.csv")
        idx_df = None
        if os.path.exists(cache_file):
            try:
                idx_df = pd.read_csv(cache_file)
                idx_df['trade_date'] = idx_df['trade_date'].astype(str)
                idx_df = idx_df[idx_df['trade_date'] <= tq.TRADE_DATE].sort_values('trade_date')
            except Exception:
                idx_df = None
        
        if idx_df is None or len(idx_df) < 25:
            try:
                idx_df = tq.pro.index_daily(ts_code=idx_code, start_date='20250101', end_date=tq.TRADE_DATE)
                if idx_df is None or len(idx_df) == 0:
                    return {'allow_trade': True, 'regime': 'unknown', 'sh_above_ma20': True, 'sh_chg_5d': 0, 'reason': '指数数据获取失败，默认放行'}
                idx_df['trade_date'] = idx_df['trade_date'].astype(str)
                idx_df = idx_df.sort_values('trade_date')
                idx_df.to_csv(cache_file, index=False)
            except Exception as e:
                return {'allow_trade': True, 'regime': 'unknown', 'sh_above_ma20': True, 'sh_chg_5d': 0, 'reason': f'指数接口异常({e})，默认放行'}
        
        if len(idx_df) < 25:
            return {'allow_trade': True, 'regime': 'unknown', 'sh_above_ma20': True, 'sh_chg_5d': 0, 'reason': '指数数据不足，默认放行'}
        
        close_arr = idx_df['close'].values.astype(float)
        last_close = close_arr[-1]
        ma20 = pd.Series(close_arr).rolling(20, min_periods=1).mean().values[-1]
        close_5d_ago = close_arr[-6] if len(close_arr) >= 6 else close_arr[0]
        sh_chg_5d = (last_close / close_5d_ago - 1) * 100
        sh_above_ma20 = last_close > ma20
        
        if not sh_above_ma20:
            regime = 'bear'
            allow = False
            reason = f'上证({last_close:.0f})跌破MA20({ma20:.0f})，弱势市场停止选股'
        elif sh_chg_5d < -3:
            regime = 'bear'
            allow = False
            reason = f'上证近5日累计{sh_chg_5d:+.2f}%，急跌市场停止选股'
        elif sh_above_ma20 and sh_chg_5d > 1:
            regime = 'bull'
            allow = True
            reason = f'上证站上MA20且近5日{sh_chg_5d:+.2f}%，强势市场'
        else:
            regime = '震荡'
            allow = True
            reason = f'上证({last_close:.0f})在MA20({ma20:.0f})附近，震荡市场谨慎选股'
        
        return {
            'allow_trade': allow,
            'regime': regime,
            'sh_above_ma20': sh_above_ma20,
            'sh_chg_5d': round(sh_chg_5d, 2),
            'sh_close': round(last_close, 2),
            'sh_ma20': round(ma20, 2),
            'reason': reason,
        }
    except Exception as e:
        return {'allow_trade': True, 'regime': 'unknown', 'sh_above_ma20': True, 'sh_chg_5d': 0, 'reason': f'过滤器异常({e})，默认放行'}


def detect_vol_ma_sync_surge(df, target_idx=None):
    """信立泰式量能爆发形态检测
    
    核心特征：横盘整理后量能突破
    8个硬条件 + 评分系统（满分100）
    """
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
    
    # 硬条件1: 量能放大 >=1.5倍
    if len(vol_arr) < 40:
        return None
    pre_vol_20 = float(np.mean(vol_arr[:20]))
    post_vol_20 = float(np.mean(vol_arr[-20:]))
    if pre_vol_20 <= 0:
        return None
    vol_surge_ratio = post_vol_20 / pre_vol_20
    if vol_surge_ratio < 1.5:
        return None
    
    # 硬条件2: 横盘整理后突破（MA20前段斜率在[-3%,+3%]，后段>2%且>前段）
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
    
    # 硬条件3: MACD红柱放大（排除红柱缩短）
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
        return None  # 排除红柱缩短
    
    # 硬条件4: 站上MA5和MA10
    ma5 = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10 = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    last_close = close_arr[-1]
    if last_close < ma5[-1] or last_close < ma10[-1]:
        return None
    
    # 硬条件5: 距MA20在5%-15%
    dist_ma20 = (last_close / ma20_now - 1) * 100
    if dist_ma20 < 5 or dist_ma20 > 15:
        return None
    
    # 硬条件6: 量价配合度>=0.95（上涨日量能 vs 下跌日量能）
    gains = close_arr[-20:] > pre_close_arr[-20:]
    up_vol = np.mean(vol_arr[-20:][gains]) if gains.sum() > 0 else 0
    down_vol = np.mean(vol_arr[-20:][~gains]) if (~gains).sum() > 0 else 0
    if down_vol > 0:
        vol_price_coord = up_vol / down_vol
    else:
        vol_price_coord = 2.0
    if vol_price_coord < 0.95:
        return None
    
    # 硬条件7: 当日量比>=1.0
    vol_ma20_arr = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    last_vol_ratio = vol_arr[-1] / max(vol_ma20_arr[-1], 1)
    if last_vol_ratio < 1.0:
        return None
    
    # 硬条件8: 当日涨幅>0
    last_chg = (last_close / pre_close_arr[-1] - 1) * 100
    if last_chg < 0:
        return None
    
    # 评分系统（基于回测分箱胜率校准）
    # 数据来源：近一个月87信号，T+5动态止盈胜率
    score = 0
    
    # 1. 量能放大倍数（正向因子，[2.5,5.0]胜率76.7%）
    if vol_surge_ratio >= 2.5: score += 20
    elif vol_surge_ratio >= 2.0: score += 15
    elif vol_surge_ratio >= 1.8: score += 10
    elif vol_surge_ratio >= 1.5: score += 8
    
    # 2. MA20前段横盘质量 + 后段突破斜率
    # 关键发现：ma20_slope_5d 是反向因子！[2,3)胜率75%, [3,5)降到63.4%
    # 斜率过大反而差，应奖励温和突破而非剧烈突破
    consolidation_quality = max(0, 3 - abs(ma20_slope_pre))  # 横盘越平越好
    if ma20_slope_5d < 3:
        breakout_score = 12  # 温和突破，最优区间
    elif ma20_slope_5d < 5:
        breakout_score = 6   # 中等
    else:
        breakout_score = 3   # 斜率过大，反向减分
    score += int(consolidation_quality * 3 + breakout_score)
    
    # 3. MACD状态
    if macd_status == "刚刚红柱": score += 15
    elif macd_status == "红柱放大": score += 12
    if last_dif > 0: score += 5
    
    # 4. 量价配合度（[1.5,2.0]胜率73.1%最优，[1.2,1.5]最差62.1%）
    if 1.5 <= vol_price_coord < 2.0: score += 15
    elif vol_price_coord >= 2.0: score += 12
    elif vol_price_coord >= 1.2: score += 8
    else: score += 10  # [0.95,1.2]胜率66.7%反而比[1.2,1.5]高
    
    # 5. 距MA20（[12,14]胜率79.2%最优，[8,10]最差52.9%）
    if 12 <= dist_ma20 <= 14: score += 15
    elif 10 <= dist_ma20 < 12: score += 12
    elif 14 < dist_ma20 <= 15: score += 8
    elif 5 <= dist_ma20 < 8: score += 7
    else: score += 5  # [8,10]区间最差
    
    # 6. 当日涨幅（[5,7]胜率75%最优）
    if 5 <= last_chg <= 7: score += 12
    elif 7 < last_chg <= 10: score += 10
    elif 3 <= last_chg < 5: score += 7
    elif 1 <= last_chg < 3: score += 8
    else: score += 6
    
    return {
        'vol_surge_ratio': round(vol_surge_ratio, 2),
        'ma20_slope_5d': round(ma20_slope_5d, 2),
        'ma20_slope_pre': round(ma20_slope_pre, 2),
        'macd_status': macd_status,
        'dif_above_zero': last_dif > 0,
        'vol_price_coord': round(vol_price_coord, 2),
        'last_vol_ratio': round(last_vol_ratio, 2),
        'dist_ma20': round(dist_ma20, 2),
        'last_chg': round(last_chg, 2),
        'score': score,
        'close': round(last_close, 2),
    }


def daily_scan(score_threshold=75, top_n=20):
    """日常选股扫描
    
    Args:
        score_threshold: 评分阈值（默认75）
        top_n: 输出前N只
    """
    print("=" * 60)
    print("信立泰式量能爆发形态选股（横盘后突破）")
    print("=" * 60)
    print("核心特征：量能放大+均线同步上升+MACD红柱放大")
    print("回测胜率：T+5动态止盈67.6%（最大涨幅>=3%）")
    print("最优组合：排除弱势市场+距MA20[10-14] 止盈胜率82.8%")
    print("=" * 60)
    
    # 大盘环境过滤
    print("\n【大盘环境检测】")
    regime = get_market_regime()
    print(f"  状态: {regime['regime']}")
    print(f"  上证收盘: {regime.get('sh_close', '-')}")
    print(f"  上证MA20: {regime.get('sh_ma20', '-')}")
    print(f"  近5日涨幅: {regime['sh_chg_5d']:+.2f}%")
    print(f"  判定: {regime['reason']}")
    
    if not regime['allow_trade']:
        print(f"\n⚠️  {regime['reason']}")
        print("🛑 根据风控规则，今日停止选股（弱势市场回测平均亏损-12.35%）")
        print("    建议空仓等待大盘企稳（上证重新站上MA20）")
        return []
    
    if regime['regime'] == '震荡':
        print("\n⚠️  震荡市场，建议降低仓位至3成以内，提高评分阈值至80分以上")
        score_threshold = max(score_threshold, 80)
    
    # 加载股票池
    all_stocks = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
    print(f"\n待扫描股票数: {len(all_stocks)}")
    print(f"评分阈值: >={score_threshold}")
    print()
    
    signals = []
    scanned = 0
    t0 = time.time()
    
    for ts_code in all_stocks:
        # 跳过北交所
        if ts_code.startswith('8') or ts_code.startswith('4') or ts_code.startswith('9'):
            continue
        
        scanned += 1
        if scanned % 500 == 0:
            elapsed = time.time() - t0
            print(f"  扫描进度: {scanned}/{len(all_stocks)}, 命中{len(signals)}只, 耗时{elapsed:.0f}s")
        
        try:
            stock_df = tq.get_hist_data(ts_code)
            if stock_df is None or len(stock_df) < 80:
                continue
            
            result = detect_vol_ma_sync_surge(stock_df)
            if result and result['score'] >= score_threshold:
                name = tq.get_stock_name(ts_code) if hasattr(tq, 'get_stock_name') else ts_code
                signals.append({
                    'code': ts_code,
                    'name': name,
                    'score': result['score'],
                    'vol_surge': result['vol_surge_ratio'],
                    'ma20_slope_5d': result['ma20_slope_5d'],
                    'ma20_slope_pre': result['ma20_slope_pre'],
                    'macd_status': result['macd_status'],
                    'dist_ma20': result['dist_ma20'],
                    'vol_price_coord': result['vol_price_coord'],
                    'last_vol_ratio': result['last_vol_ratio'],
                    'last_chg': result['last_chg'],
                    'close': result['close'],
                    'dif_above_zero': result['dif_above_zero'],
                })
        except Exception:
            pass
    
    elapsed = time.time() - t0
    print(f"\n扫描完成: {scanned}只, 命中{len(signals)}只, 耗时{elapsed:.0f}s")
    
    if not signals:
        print("\n❌ 今日无符合条件的股票")
        return
    
    # 按评分排序
    signals.sort(key=lambda x: -x['score'])
    
    # 输出结果
    print("\n" + "=" * 60)
    print(f"🔥 信立泰式量能爆发形态选股结果（前{min(top_n, len(signals))}只）")
    print("=" * 60)
    print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'评分':<6}{'量能放大':<8}{'MA20斜率':<10}{'MACD':<8}{'距MA20':<8}{'当日涨幅':<8}")
    print("-" * 80)
    
    for i, s in enumerate(signals[:top_n], 1):
        print(f"{i:<4}{s['code']:<12}{s['name']:<10}{s['score']:<6}{s['vol_surge']:<8.2f}"
              f"{s['ma20_slope_5d']:<+6.2f}/{s['ma20_slope_pre']:<+5.2f}  "
              f"{s['macd_status']:<8}{s['dist_ma20']:<+6.2f}%  {s['last_chg']:<+6.2f}%")
    
    # 输出操作建议
    print("\n" + "=" * 60)
    print("【操作建议】")
    print("=" * 60)
    print(f"1. 当前市场: {regime['regime']}（{regime['reason']}）")
    if regime['regime'] == 'bull':
        print("   强势市场：仓位可至6成，单只3成")
    elif regime['regime'] == '震荡':
        print("   震荡市场：仓位3成以内，单只1.5成，评分>=80")
    print("2. 评分>=85优先（回测止盈胜率90%），80-85次选（胜率87.5%）")
    print("3. T+5内涨幅达3%即卖出（动态止盈）")
    print("4. 优先选 距MA20在12-14% + 温和突破(斜率2-3) 的标的")
    print("5. 止损：跌破MA10或T+3不创新高则止损")
    print("6. 弱势市场立即停止选股（回测平均亏损-12.35%）")
    
    # 保存结果
    sig_df = pd.DataFrame(signals)
    today = tq.get_last_trade_date() if hasattr(tq, 'get_last_trade_date') else time.strftime("%Y%m%d")
    out_path = rf"d:\mystock\cache_daily\VolMaSync_Stocks_{today}.csv"
    sig_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n✅ 结果已保存: {out_path}")
    
    return signals


if __name__ == "__main__":
    daily_scan(score_threshold=75, top_n=20)
