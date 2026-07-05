#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
历史日期量能爆发·强买信号回填验证
对 20260629、20260630、20260701、20260702、20260703 五个交易日
重新按新规则（4组强买信号组合）计算历史候选股的强买信号触发情况。

输入：从 Final_Self_*.md 解析出的历史候选股列表
输出：每个日期的强买信号判定结果
"""
import sys
import os
import re
import pandas as pd
import numpy as np
from datetime import datetime

# Windows GBK 控制台输出修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r"d:\mystock\cache_daily"

# =========================
# 历史候选股（从 Final_Self_*.md 解析）
# 格式: {date: [(code, name, raw_score), ...]}
# =========================
HISTORY_CANDIDATES = {
    '20260629': [
        ('300259.SZ', '新天科技', 100),
        ('300488.SZ', '恒锋工具', 84),
    ],
    '20260630': [
        ('300259.SZ', '新天科技', 100),
        ('300263.SZ', '隆华科技', 87),
    ],
    '20260701': [
        ('300715.SZ', '凯伦股份', 94),
        ('688508.SH', '芯朋微', 86),
        ('002979.SZ', '雷赛智能', 84),
        ('301603.SZ', '乔锋智能', 83),
        ('300657.SZ', '弘信电子', 77),
    ],
    '20260702': [
        ('600063.SH', '皖维高新', 92),
        ('688508.SH', '芯朋微', 85),
        ('603733.SH', '仙鹤股份', 81),
        ('603638.SH', '艾迪精密', 56),
    ],
    '20260703': [
        ('601609.SH', '金田股份', 87),  # 已确认强买
        ('603268.SH', '松发股份', 59),
    ],
}


def load_kline_to_date(ts_code, end_date):
    """读取本地缓存K线，截断到指定日期"""
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
    if not os.path.exists(cache_file):
        print(f"  [Warn] 缓存不存在: {ts_code}")
        return None
    try:
        df = pd.read_csv(cache_file)
        df['trade_date'] = df['trade_date'].astype(str)
        df = df[df['trade_date'] <= end_date].sort_values('trade_date').reset_index(drop=True)
        if len(df) < 80:
            print(f"  [Warn] {ts_code} 数据不足80条: {len(df)}")
            return None
        return df
    except Exception as e:
        print(f"  [Error] {ts_code} 读取失败: {e}")
        return None


def detect_strong_buy(df):
    """
    复用 tushare_quant.py 中 detect_volume_surge_swing 的强买信号判定逻辑
    但使用传入的 df（已截断到测试日期）代替全局 TRADE_DATE
    """
    if df is None or len(df) < 80:
        return None
    recent = df.tail(60)
    if len(recent) < 20:
        return None
    vol_arr = recent['vol'].values.astype(float)
    high_arr = recent['high'].values.astype(float)
    low_arr = recent['low'].values.astype(float)
    close_arr = recent['close'].values.astype(float)
    pre_close_arr = recent['pre_close'].values.astype(float)

    vol_ma20 = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    vol_ratio = vol_arr / np.maximum(vol_ma20, 1)
    max_vol_ratio = float(np.max(vol_ratio))
    vol_ratio_gt2 = int(np.sum(vol_ratio > 2.0))
    vol_ratio_gt3 = int(np.sum(vol_ratio > 3.0))

    hist_vol_max = float(np.max(df['vol'].values.astype(float)))
    recent_vol_max = float(np.max(vol_arr))
    vol_vs_hist_pct = (recent_vol_max / hist_vol_max * 100) if hist_vol_max > 0 else 0

    amplitude = (high_arr - low_arr) / np.maximum(pre_close_arr, 0.01) * 100
    avg_amplitude = float(np.mean(amplitude))
    amp_gt8_count = int(np.sum(amplitude > 8))

    range_high = float(np.max(high_arr))
    range_low = float(np.min(low_arr))
    range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0

    price_change = (close_arr[-1] / close_arr[0] - 1) * 100 if close_arr[0] > 0 else 0

    # 硬条件
    if max_vol_ratio < 2.6: return None
    if vol_ratio_gt2 < 3: return None
    if avg_amplitude < 4.5: return None
    if range_swing < 35: return None
    if price_change < -10: return None
    if price_change > 100: return None
    if len(df) < 180: return None
    if vol_vs_hist_pct < 50: return None

    # 量能活跃度检查
    _df200 = df.tail(200) if len(df) >= 200 else df
    _vol200 = _df200['vol'].values.astype(float)
    _high200 = _df200['high'].values.astype(float)
    _low200 = _df200['low'].values.astype(float)

    _peak_vol_idx = int(np.argmax(_vol200))
    _peak_vol_price = float(_high200[_peak_vol_idx])
    _pre_peak_start = max(0, _peak_vol_idx - 20)
    _pre_peak_end = max(0, _peak_vol_idx - 3)
    _base_vol = float(np.mean(_vol200[_pre_peak_start:_pre_peak_end])) if _pre_peak_end > _pre_peak_start else float(np.mean(_vol200[:_peak_vol_idx]))
    _base_vol = max(_base_vol, 1)
    _recent_vol = float(np.mean(_vol200[-20:])) if len(_vol200) >= 20 else float(np.mean(_vol200))
    _vol_vs_base = _recent_vol / _base_vol
    if _vol_vs_base < 1.3: return None

    _peak_vol_start = max(0, _peak_vol_idx - 5)
    _peak_vol_end = min(len(_vol200), _peak_vol_idx + 6)
    _peak_5d_vol = float(np.mean(_vol200[_peak_vol_start:_peak_vol_end])) if _peak_vol_end > _peak_vol_start else _recent_vol
    _peak_5d_vol = max(_peak_5d_vol, 1)
    _vol_vs_peak = _recent_vol / _peak_5d_vol
    if _vol_vs_peak < 0.5: return None

    # ABC结构
    _a_low = float(np.min(_low200[:_peak_vol_idx+1]))
    _a_gain = (_peak_vol_price / _a_low - 1) * 100 if _a_low > 0 else 0
    if _a_gain < 15: return None

    if _peak_vol_idx < len(_low200) - 3:
        _b_low = float(np.min(_low200[_peak_vol_idx:]))
        _b_drop = (1 - _b_low / _peak_vol_price) * 100
        _retrace_ratio = _b_drop / _a_gain * 100 if _a_gain > 0 else 0
    else:
        _b_low = close_arr[-1]
        _b_drop = 0
        _retrace_ratio = 0

    _fib_786 = _peak_vol_price - (_peak_vol_price - _a_low) * 0.786
    if _b_low < _fib_786 * 0.92: return None
    if _retrace_ratio > 80: return None

    _peak_idx = int(np.argmax(high_arr))
    _peak_price = float(high_arr[_peak_idx])
    _pre_peak_low = float(np.min(low_arr[:_peak_idx+1])) if _peak_idx > 0 else float(low_arr[0])
    _pre_peak_gain = (_peak_price / _pre_peak_low - 1) * 100 if _pre_peak_low > 0 else 0
    _dist_from_peak = (1 - close_arr[-1] / _peak_price) * 100

    if _peak_idx < len(high_arr) - 10:
        _post_peak_low = float(np.min(low_arr[_peak_idx:]))
        if _post_peak_low > 0:
            _bounce = (close_arr[-1] / _post_peak_low - 1) * 100
        else:
            _bounce = 0
    else:
        _bounce = 0

    if _pre_peak_gain > 70 and _dist_from_peak > 15 and _bounce < 10: return None

    # 评分
    vol_score = min(max_vol_ratio / 5.0, 1) * 30
    freq_score = min(vol_ratio_gt2 / 7, 1) * 20
    amp_score = min(avg_amplitude / 7, 1) * 20
    big_amp_score = min(amp_gt8_count / 15, 1) * 15
    swing_score = min(range_swing / 60, 1) * 15
    total_score = vol_score + freq_score + amp_score + big_amp_score + swing_score
    if total_score < 55: return None

    # MACD
    close_full = df['close'].values.astype(float)
    ema12 = pd.Series(close_full).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close_full).ewm(span=26, adjust=False).mean().values
    macd_dif = ema12 - ema26
    macd_dea = pd.Series(macd_dif).ewm(span=9, adjust=False).mean().values
    macd_bar = 2 * (macd_dif - macd_dea)

    cur_bar = float(macd_bar[-1])
    prev_bar = float(macd_bar[-2]) if len(macd_bar) >= 2 else cur_bar
    prev2_bar = float(macd_bar[-3]) if len(macd_bar) >= 3 else prev_bar

    macd_status = ''
    macd_pass = False
    if prev_bar < 0 < cur_bar:
        macd_status = '刚刚红柱 ✅'
        macd_pass = True
    elif cur_bar < 0 and cur_bar > prev_bar > prev2_bar:
        macd_status = '即将红柱（绿柱连续缩短）'
        macd_pass = True
    if not macd_pass: return None

    today_vol_ratio = float(vol_ratio[-1]) if len(vol_ratio) > 0 else 0

    # 回撤类型
    if _retrace_ratio < 30:
        retrace_type = '浅回调'
    elif _retrace_ratio < 50:
        retrace_type = '中回调'
    else:
        retrace_type = '深回调'

    # 距MA20
    close_latest = float(close_arr[-1])
    ma20_latest = pd.Series(close_arr).rolling(20).mean().values[-1]
    pos_ma20 = (close_latest / ma20_latest - 1) * 100 if not np.isnan(ma20_latest) and ma20_latest > 0 else 0

    is_fresh_red = (macd_status == '刚刚红柱 ✅')

    # === 强买信号判定（4组组合）===
    strong_buy = False
    strong_buy_reason = ''
    if pos_ma20 < 0 and is_fresh_red:
        strong_buy = True
        strong_buy_reason = '①回踩MA20下方+MACD刚红柱(回测100%胜率)'
    elif retrace_type == '中回调' and is_fresh_red:
        strong_buy = True
        strong_buy_reason = '②中回调+MACD刚红柱(回测79%胜率)'
    elif retrace_type == '浅回调' and is_fresh_red and total_score >= 70:
        strong_buy = True
        strong_buy_reason = '③浅回调+刚红柱+高评分(回测74%胜率)'
    elif 65 <= total_score < 80 and 1.0 <= today_vol_ratio < 1.5 and -3 <= pos_ma20 < 0:
        strong_buy = True
        strong_buy_reason = '④评分65-80+量比1.0-1.5+回踩MA20(回测76%胜率)'

    # === 观察信号判定（即将红柱，等待确认）===
    watch = False
    watch_reason = ''
    if not strong_buy and not is_fresh_red:
        watch = True
        watch_reason = '观察·等待红柱（MACD绿柱连续缩短，即将金叉，可关注翻红确认）'

    return {
        '评分': round(total_score, 1),
        'MACD状态': macd_status,
        '回撤类型': retrace_type,
        '距MA20': round(pos_ma20, 1),
        '今日量比': round(today_vol_ratio, 2),
        '回撤比例': round(_retrace_ratio, 1),
        '区间涨幅': round(price_change, 1),
        '区间振幅': round(range_swing, 1),
        '强买信号': strong_buy,
        '强买原因': strong_buy_reason,
        '观察信号': watch,
        '观察原因': watch_reason,
    }


def main():
    print("=" * 80)
    print("历史日期量能爆发·强买信号回填验证")
    print("=" * 80)
    print(f"强买信号4组组合：")
    print(f"  ①距MA20<0% + MACD刚红柱 (100%胜率)")
    print(f"  ②中回调 + MACD刚红柱 (79%胜率)")
    print(f"  ③浅回调 + 刚红柱 + 评分>=70 (74%胜率)")
    print(f"  ④评分65-80 + 量比1.0-1.5 + 距MA20 -3~0% (76%胜率)")
    print()

    summary_rows = []
    for test_date, candidates in sorted(HISTORY_CANDIDATES.items()):
        print(f"\n{'─' * 80}")
        print(f"【{test_date}】候选 {len(candidates)} 只")
        print(f"{'─' * 80}")
        triggered = []
        watched = []
        for code, name, raw_score in candidates:
            df = load_kline_to_date(code, test_date)
            if df is None:
                print(f"  ✗ {name}({code}) 数据缺失")
                continue
            result = detect_strong_buy(df)
            if result is None:
                print(f"  ✗ {name}({code}) 原评分{raw_score} → 未通过硬条件/未触发强买")
                summary_rows.append({
                    '日期': test_date, '代码': code, '名称': name, '原评分': raw_score,
                    '新评分': '-', 'MACD': '-', '回撤': '-', '距MA20': '-',
                    '强买': False, '原因': '未通过'
                })
                continue
            sb = result['强买信号']
            wt = result.get('观察信号', False)
            if sb:
                tag = '✅强买'
            elif wt:
                tag = '👀观察'
            else:
                tag = '✗'
            print(f"  {tag} {name}({code}) 原评分{raw_score} → 新评分{result['评分']} | {result['MACD状态']} | {result['回撤类型']} | 距MA20={result['距MA20']:+.1f}% | 回撤比{result['回撤比例']}%")
            if sb:
                print(f"     触发: {result['强买原因']}")
                triggered.append((name, code, result))
            elif wt:
                print(f"     观察: {result['观察原因']}")
                watched.append((name, code, result))
            summary_rows.append({
                '日期': test_date, '代码': code, '名称': name, '原评分': raw_score,
                '新评分': result['评分'], 'MACD': result['MACD状态'],
                '回撤': result['回撤类型'], '距MA20': f"{result['距MA20']:+.1f}%",
                '强买': sb, '观察': wt,
                '原因': result['强买原因'] if sb else (result['观察原因'] if wt else '-')
            })

        if triggered:
            print(f"\n  >> {test_date} 强买信号 {len(triggered)} 只: {', '.join([n+'('+c+')' for n,c,_ in triggered])}")
        if watched:
            print(f"  >> {test_date} 观察信号 {len(watched)} 只: {', '.join([n+'('+c+')' for n,c,_ in watched])}")
        if not triggered and not watched:
            print(f"\n  >> {test_date} 无强买/观察信号")

    # 保存CSV
    out_csv = os.path.join(BASE_DIR, 'history_vol_surge_strong_buy.csv')
    pd.DataFrame(summary_rows).to_csv(out_csv, index=False, encoding='utf-8-sig')
    print(f"\n{'=' * 80}")
    print(f"汇总表已保存: {out_csv}")
    print(f"{'=' * 80}")
    print("\n【触发汇总】")
    sb_rows = [r for r in summary_rows if r['强买']]
    if sb_rows:
        print(f"{'日期':<10}{'名称':<10}{'代码':<12}{'新评分':<8}{'MACD':<25}{'回撤':<8}{'距MA20':<10}{'原因'}")
        for r in sb_rows:
            print(f"{r['日期']:<10}{r['名称']:<10}{r['代码']:<12}{r['新评分']:<8}{r['MACD']:<25}{r['回撤']:<8}{r['距MA20']:<10}{r['原因']}")
    else:
        print("所有日期均无强买信号触发")


if __name__ == '__main__':
    main()
