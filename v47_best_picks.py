# -*- coding: utf-8 -*-
"""
V4.7 高胜率低回撤精选（纯技术面版）
从238只突破股中筛选：
  1. RSI14<75（未极端超买）
  2. 量比<3（未放巨量）
  3. 近5日涨幅<20%（未暴涨）
  4. 回撤<-35%（有安全边际）
"""

import sys, os, json, math
import pandas as pd
import numpy as np

BASE_DIR = r'D:\mystock'
CACHE_DIR = os.path.join(BASE_DIR, 'cache_daily')
SCAN_FILE = os.path.join(BASE_DIR, 'solo', 'report_daily', 'v46_full_scan_20260619.json')

def sf(v, d=0.0):
    try: return float(v) if math.isfinite(float(v)) else d
    except: return d

def calc_risk_metrics(csv_path):
    try:
        df = pd.read_csv(csv_path)
        if len(df) < 20: return None
        
        close = df['close'].astype(float)
        high  = df['high'].astype(float) if 'high' in df else close
        
        # 回撤
        cummax = close.expanding().max()
        dd = (close - cummax) / cummax
        max_dd = float(dd.iloc[-20:].min())
        
        # 波动率
        ret = close.pct_change()
        vol = float(ret.iloc[-20:].std()) * math.sqrt(252)
        
        # 涨幅
        pct_5d  = sf((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100)
        pct_20d = sf((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100)
        
        # 位置
        high_20d = float(high.iloc[-20:].max())
        position = sf((close.iloc[-1] - high_20d) / high_20d * 100)
        
        return {
            'max_dd': max_dd,
            'volatility': vol,
            'pct_5d': pct_5d,
            'pct_20d': pct_20d,
            'position': position,
        }
    except Exception as e:
        return None

def screen_best():
    print('=' * 80)
    print('V4.7 高胜率低回撤精选（纯技术面）')
    print('=' * 80)
    
    with open(SCAN_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    all_results = data.get('all_results', [])
    breakout = [r for r in all_results if r.get('mode') == 'BREAKOUT']
    
    print(f'\n📊 突破股总数：{len(breakout)}只')
    
    candidates = []
    for r in breakout:
        code = r['code']
        
        # 基础过滤
        if r['rsi14'] > 75: continue
        if r['vol_ratio'] > 3: continue
        
        # 计算风险指标
        csv_path = os.path.join(CACHE_DIR, f'{code}.csv')
        metrics = calc_risk_metrics(csv_path)
        if not metrics: continue
        
        if metrics['pct_5d'] > 20: continue
        if metrics['max_dd'] < -35: continue
        
        # 综合评分
        dd_score = max(0, 20 + metrics['max_dd'])
        vol_score = max(0, 15 - abs(metrics['volatility'] - 50) * 0.3)
        rsi_score = max(0, 15 - (r['rsi14'] - 50) * 0.5)
        
        total = r['total'] + dd_score + vol_score + rsi_score
        
        candidates.append({
            'code': code,
            'close': r['close'],
            'ma5_dev': r['ma5_dev'],
            'rsi2': r['rsi2'],
            'rsi14': r['rsi14'],
            'vol_ratio': r['vol_ratio'],
            'base_score': r['total'],
            'max_dd': round(metrics['max_dd'], 1),
            'pct_5d': round(metrics['pct_5d'], 1),
            'pct_20d': round(metrics['pct_20d'], 1),
            'position': round(metrics['position'], 1),
            'quality_score': round(total, 1),
        })
    
    candidates.sort(key=lambda x: x['quality_score'], reverse=True)
    
    print(f'✅ 过滤后：{len(candidates)}只')
    
    if candidates:
        print('\n' + '=' * 100)
        print(f'{"排名":^4} {"代码":<12} {"收盘":>7} {"RSI14":>5} {"量比":>5} {"5日涨":>6} {"回撤":>6} {"质量分":>7}')
        print('-' * 100)
        for i, c in enumerate(candidates[:15]):
            sig = '✅' if c['quality_score'] > 70 else ('🔍' if c['quality_score'] > 60 else '⚠️')
            print(f'{i+1:^4} {c["code"]:<12} {c["close"]:>7.2f} {c["rsi14"]:>5.1f} '
                  f'{c["vol_ratio"]:>5.2f} {c["pct_5d"]:>+5.1f}% {c["max_dd"]:>+5.1f}% '
                  f'{c["quality_score"]:>7.1f} {sig}')
        
        # TOP5详细
        print('\n' + '=' * 100)
        print('🏅 TOP5 交易计划')
        print('=' * 100)
        for i, c in enumerate(candidates[:5]):
            entry = c['close'] * 0.98  # 回调2%入场
            stop = c['close'] * 0.93   # 止损-7%
            target = c['close'] * 1.15  # 止盈+15%
            
            print(f'\n{i+1}. {c["code"]} | 收盘{c["close"]:.2f} | 质量分{c["quality_score"]:.1f}')
            print(f'   技术：RSI14={c["rsi14"]:.1f} 量比={c["vol_ratio"]:.2f} '
                  f'5日{c["pct_5d"]:+.1f}% 回撤{c["max_dd"]:+.1f}%')
            print(f'   交易：入场{entry:.2f} | 止损{stop:.2f}(-7%) | 止盈{target:.2f}(+15%)')
            print(f'   风险比：盈亏比1:2.1 | 建议仓位：{min(10, max(3, c["quality_score"]-55))}%')
    
    # 保存
    out_file = os.path.join(BASE_DIR, 'solo', 'report_daily', 'v47_best_picks_20260619.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': '2026-06-19',
            'breakout_total': len(breakout),
            'filtered': len(candidates),
            'top15': candidates[:15],
            'all_candidates': candidates,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{out_file}')
    
    return candidates

if __name__ == '__main__':
    screen_best()
