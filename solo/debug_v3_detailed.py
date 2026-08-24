# -*- coding: utf-8 -*-
"""详细调试 RIB v3 测试 - 追踪每个阶段的值"""
import sys
sys.path.insert(0, '.')

import numpy as np
from test_rib_v3 import create_rib_pattern_v3
from rib.indicators import enrich
from rib.engine import RIBEngine
from rib.config import RIB_CONFIG


def main():
    df = create_rib_pattern_v3()
    df = enrich(df)
    n = len(df)
    
    print(f"Total bars: {n}")
    print()
    
    # 打印关键数据点
    print("=== 关键数据点 ===")
    for idx in [120, 130, 134, 135, 155, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170]:
        row = df.iloc[idx]
        print(f"  Day {idx:3d}: close={row['close']:7.2f}, high={row['high']:7.2f}, low={row['low']:7.2f}, "
              f"vol={row['vol']:12.0f}, vol_ma20={row['vol_ma20']:12.0f}, "
              f"atr20={row['atr20']:6.3f}, ma5={row['ma5']:7.2f}, ma10={row['ma10']:7.2f}, "
              f"ma20={row['ma20']:7.2f}")
    
    print()
    
    # 手动执行引擎的每个阶段
    engine = RIBEngine()
    end_idx = n - 1
    
    # ── 阶段 0: 扫描第一波 ──
    print("=" * 60)
    print("阶段 0: 扫描第一波 (_scan_for_impulse)")
    print("=" * 60)
    imp = engine._scan_for_impulse(df, end_idx)
    
    if imp is None:
        print("  结果: None (未检测到第一波)")
        return
    
    print(f"  is_impulse: {imp.is_impulse}")
    print(f"  impulse_low_idx: {imp.impulse_low_idx}")
    print(f"  impulse_low: {imp.impulse_low:.2f}")
    print(f"  impulse_high_idx: {imp.impulse_high_idx}")
    print(f"  impulse_high: {imp.impulse_high:.2f}")
    print(f"  impulse_days: {imp.impulse_days}")
    print(f"  impulse_return: {imp.impulse_return:.4f} ({imp.impulse_return*100:.1f}%)")
    print(f"  volume_ratio: {imp.volume_ratio:.2f}")
    print(f"  is_reversal_confirmed: {imp.is_reversal_confirmed}")
    print(f"  broke_trend_line: {imp.broke_trend_line}")
    print(f"  broke_ma20: {imp.broke_ma20}")
    print(f"  broke_ma60: {imp.broke_ma60}")
    print(f"  score: {imp.score:.1f}")
    
    # ── 阶段 1: 检测下跌 ──
    print()
    print("=" * 60)
    print("阶段 1: 检测长期下跌 (downtrend_detector)")
    print("=" * 60)
    dt = engine.downtrend_detector.detect(df, imp.impulse_low_idx)
    print(f"  is_downtrend: {dt.is_downtrend}")
    print(f"  score: {dt.score:.1f}")
    
    # ── 阶段 2: 检测第一波高点 ──
    print()
    print("=" * 60)
    print("阶段 2: 检测第一波高点 (peak_detector)")
    print("=" * 60)
    peak = engine.peak_detector.detect(df, imp, end_idx)
    print(f"  is_peak_valid: {peak.is_peak_valid}")
    print(f"  peak_idx: {peak.peak_idx}")
    print(f"  peak_price: {peak.peak_price:.2f}")
    print(f"  exhaustion_signals: {peak.exhaustion_signals}")
    
    # ── 阶段 3: 检测 POST_IMPULSE_BASE ──
    print()
    print("=" * 60)
    print("阶段 3: 检测 POST_IMPULSE_BASE (base_detector)")
    print("=" * 60)
    base = engine.base_detector.detect(df, imp, peak, end_idx)
    print(f"  is_base: {base.is_base}")
    print(f"  platform_start_idx: {base.platform_start_idx}")
    print(f"  platform_end_idx: {base.platform_end_idx}")
    print(f"  platform_days: {base.platform_days}")
    print(f"  base_high: {base.base_high:.2f}")
    print(f"  base_low: {base.base_low:.2f}")
    print(f"  pullback_depth: {base.pullback_depth:.4f}")
    print(f"  retain_ratio: {base.retain_ratio:.4f}")
    print(f"  volume_shrink_ratio: {base.volume_shrink_ratio:.4f}")
    print(f"  ma20_slope: {base.ma20_slope:.6f}")
    print(f"  base_type: {base.base_type}")
    print(f"  score: {base.score:.1f}")
    
    if not base.is_base:
        print("  ❌ 平台未形成，无法继续")
        return
    
    # ── 阶段 4: 检测预突破 ──
    print()
    print("=" * 60)
    print("阶段 4: 检测预突破 (pre_breakout_detector)")
    print("=" * 60)
    pre_bo = engine.pre_breakout_detector.detect(df, base, end_idx)
    if pre_bo:
        print(f"  预突破检测: {pre_bo}")
    else:
        print("  无预突破信号")
    
    # ── 阶段 5: 检测第二波突破 ──
    print()
    print("=" * 60)
    print("阶段 5: 检测第二波突破 (breakout_detector)")
    print("=" * 60)
    print(f"  搜索区间: [{base.platform_end_idx + 1}, {end_idx}]")
    print(f"  impulse_high: {imp.impulse_high:.2f}")
    
    # 手动遍历每个K线，看为什么被过滤
    closes = df["close"].values.astype(float)
    highs = df["high"].values.astype(float)
    vols = df["vol"].values.astype(float)
    search_start = base.platform_end_idx + 1
    
    print()
    print("  逐K线分析:")
    for i in range(search_start, end_idx + 1):
        close = closes[i]
        high = highs[i]
        vol = vols[i]
        
        atr_val = df["atr20"].values[i] if "atr20" in df.columns else 0
        vol_ma = df["vol_ma20"].values[i] if "vol_ma20" in df.columns else 0
        vol_ratio = vol / vol_ma if vol_ma > 0 else 0
        
        day_range = highs[i] - df["low"].values.astype(float)[i]
        close_loc = (close - df["low"].values.astype(float)[i]) / day_range if day_range > 0 else 0.5
        
        distance_atr = (close - imp.impulse_high) / atr_val if atr_val > 0 else 999
        
        checks = []
        checks.append(f"close>{imp.impulse_high:.2f}: {'✓' if close > imp.impulse_high else '✗'}")
        checks.append(f"close>{imp.impulse_high + 0.3*atr_val:.2f}: {'✓' if close > imp.impulse_high + 0.3*atr_val else '✗'}")
        checks.append(f"vol_ratio={vol_ratio:.2f}>=1.3: {'✓' if vol_ratio >= 1.3 else '✗'}")
        checks.append(f"close_loc={close_loc:.2f}>=0.75: {'✓' if close_loc >= 0.75 else '✗'}")
        checks.append(f"dist_atr={distance_atr:.2f}")
        
        print(f"    Day {i}: close={close:.2f}, vol_ratio={vol_ratio:.2f}, close_loc={close_loc:.2f}, dist_atr={distance_atr:.2f}")
        for c in checks:
            print(f"      {c}")
    
    print()
    bo = engine.breakout_detector.detect(df, imp, base, end_idx)
    print(f"  is_breakout: {bo.is_breakout}")
    print(f"  is_fake_breakout: {bo.is_fake_breakout}")
    if bo.is_breakout:
        print(f"  breakout_idx: {bo.breakout_idx}")
        print(f"  breakout_price: {bo.breakout_price:.2f}")
        print(f"  breakout_distance_atr: {bo.breakout_distance_atr:.2f}")
        print(f"  volume_ratio: {bo.volume_ratio:.2f}")
        print(f"  close_location: {bo.close_location:.2f}")
        print(f"  score: {bo.score:.1f}")
    else:
        print("  ❌ 突破未检测到！")
        print("  检查原因...")
        print(f"    volume_ratio_min: {engine.breakout_detector.cfg.get('volume_ratio_min', 'N/A')}")
        print(f"    close_location_min: {engine.breakout_detector.cfg.get('close_location_min', 'N/A')}")
    
    # ── 完整引擎分析 ──
    print()
    print("=" * 60)
    print("完整引擎分析结果")
    print("=" * 60)
    result = engine.analyze(df, ts_code='RIB.V3', name='RIBTestV3', industry='Test')
    print(f"  state: {result.state}")
    print(f"  is_valid: {result.is_valid}")
    print(f"  conclusion: {result.conclusion}")
    print()
    for attr in ['downtrend_score', 'impulse_score', 'base_score', 
                 'breakout_score', 'pullback_score', 'reacceleration_score',
                 'final_score', 'grade', 'primary_buy']:
        val = getattr(result, attr, None)
        if val is not None:
            print(f"  {attr}: {val}")


if __name__ == '__main__':
    main()