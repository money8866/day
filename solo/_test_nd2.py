#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
「猎尾V5」ND2 单元测试
验证: 形态分类 / TailFlow / ND2概率 / 风险引擎 / 主评分器 / S-A-B分级 / 快照回填
"""
import os
import sys
import tempfile
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nd2_pattern import PatternClassifier, PULLBACK_GAP, BREAKOUT_TAIL, STEALTH_ACCUMULATION, OTHER
from nd2_tailflow import TailFlowEngine
from nd2_engine import ND2Engine, nd2_rule_score
from nd2_risk import RiskEngine
from nd2_alpha import ND2AlphaEngine
from nd2_store import ND2SnapshotStore
from nd2_report import format_console_report, format_wechat_message

PASS = 0
FAIL = 0


def check(name, cond, info=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {info}")


def make_kline(prices, vols=None, pcts=None):
    """构造K线DataFrame"""
    n = len(prices)
    if vols is None:
        vols = [100000] * n
    if pcts is None:
        pcts = [0] * n
    df = pd.DataFrame({
        'trade_date': [f'202607{i+1:02d}' for i in range(n)],
        'open': [p * 0.99 for p in prices],
        'high': [p * 1.02 for p in prices],
        'low': [p * 0.98 for p in prices],
        'close': prices,
        'vol': vols,
        'pct_chg': pcts,
    })
    return df


def make_quote(price=10.0, pct=2.0, open_p=None, high=None, low=None, last_close=None, vol=200000, name='测试股'):
    lc = last_close if last_close else price / (1 + pct / 100)
    return {
        'price': price, 'pct_chg': pct,
        'open': open_p if open_p else lc * 0.995,
        'high': high if high else max(price, lc) * 1.005,
        'low': low if low else min(price, lc) * 0.995,
        'last_close': lc, 'vol': vol, 'amount': vol * price,
        'name': name,
    }


def make_snap(noon_vol=100000, tail_base_vol=130000, tail_base_price=9.95, morning_vol=150000):
    return {
        'noon_vol': noon_vol,
        'tail_base_vol': tail_base_vol,
        'tail_base_price': tail_base_price,
        'morning_vol': morning_vol,
        'noon_pct': 1.5,
        'tail_base_pct': 1.8,
    }


# ═══════════════════════════════════════════
print("\n[1] PatternClassifier 形态分类")
# ═══════════════════════════════════════════

# 1.1 PULLBACK_GAP: 强势基因(2次涨停) + 回调5%+ + 缩量 + MA10企稳
# 数据结构: 12天平台填充在前, 形态在后(涨停->上涨->回调->企稳)
prices = [9.8] * 12 + [8.0, 8.5, 9.2, 9.8, 10.5, 11.2, 10.8, 10.3, 10.0, 9.8]
pcts = [0] * 12 + [0, 0, 9.8, 9.9, 5, 3, -3, -4, -2, -1.5]
vols = [80000] * 12 + [80000, 90000, 250000, 300000, 200000, 150000, 120000, 100000, 90000, 85000]
kl = make_kline(prices, vols, pcts)
q = make_quote(price=9.85, pct=0.5, last_close=9.8, vol=90000)
snap = make_snap()
pat, f, d = PatternClassifier.classify(q, kl, snap, '600000.SH')
check("PULLBACK_GAP识别(涨停基因+回调+缩量)", pat == PULLBACK_GAP, f"got {pat} detail={d}")

# 1.2 BREAKOUT_TAIL: 平台 + 尾盘放量突破
prices = [10.0, 10.1, 9.9, 10.0, 10.05, 10.1, 9.95, 10.0, 10.02, 10.08]
prices = prices + [10.0] * 11
pcts = [0] * 21
vols = [100000] * 21
kl2 = make_kline(prices, vols, pcts)
q2 = make_quote(price=10.35, pct=3.0, last_close=10.05, high=10.4, vol=260000)
snap2 = make_snap(noon_vol=100000, tail_base_vol=110000, tail_base_price=10.1)
pat2, f2, d2 = PatternClassifier.classify(q2, kl2, snap2, '600000.SH')
check("BREAKOUT_TAIL识别(平台+尾盘突破)", pat2 == BREAKOUT_TAIL, f"got {pat2} detail={d2}")

# 1.3 STEALTH_ACCUMULATION: 全天+1.5% 尾盘量增价抬 (远离20日高,不构成突破)
prices = [10.0, 10.0, 9.98, 10.0, 10.0, 9.99, 10.0, 10.0, 10.0, 9.99]
prices = prices + [9.99, 9.98, 10.0, 9.99, 10.0, 10.0, 9.98, 9.99, 10.0, 9.99, 9.52]
pcts = [0] * 20 + [1.5]
vols = [100000] * 21
kl3 = make_kline(prices, vols, pcts)
q3 = make_quote(price=9.67, pct=1.6, last_close=9.52, high=9.68, low=9.55, vol=150000)
snap3 = make_snap(noon_vol=80000, tail_base_vol=100000, tail_base_price=9.60)
pat3, f3, d3 = PatternClassifier.classify(q3, kl3, snap3, '000001.SZ')
check("STEALTH_ACCUMULATION识别(隐蔽吸筹)", pat3 == STEALTH_ACCUMULATION, f"got {pat3} detail={d3}")

# ═══════════════════════════════════════════
print("\n[2] TailFlowEngine 尾盘资金引擎")
# ═══════════════════════════════════════════
# 2.1 强尾盘: 量比2.0 + 涨0.8% + 收盘位0.95 + 买压高
f_strong = {
    'tail_vs_noon_ratio': 2.0, 'tail_base_price': 9.9, 'price': 9.98,
    'close_position': 0.95, 'vol_yesterday': 100000, 'cur_vol': 200000,
}
s1, d1 = TailFlowEngine.score(f_strong)
check("强尾盘得分>=18", s1 >= 18, f"got {s1} detail={d1}")

# 2.2 弱尾盘: 无量比 + 收盘位低
f_weak = {'tail_vs_noon_ratio': 0.5, 'tail_base_price': 9.9, 'price': 9.91, 'close_position': 0.5}
s2, d2 = TailFlowEngine.score(f_weak)
check("弱尾盘得分<10", s2 < 10, f"got {s2}")

# 2.3 无效放量识别: 量比2.0但价滞
f_dist = {'tail_vs_noon_ratio': 2.0, 'tail_base_price': 9.9, 'price': 9.90, 'close_position': 0.6}
s3, d3 = TailFlowEngine.score(f_dist)
check("无效放量被标记", d3.get('invalid_volume') is True, f"detail={d3}")

# ═══════════════════════════════════════════
print("\n[3] RiskEngine 风险引擎")
# ═══════════════════════════════════════════
# 3.1 高位+连续上涨
f_high = {'drawdown_20d': 0.5, 'gain_5d': 14, 'gain_20d': 25, 'ma20': 8.0, 'price': 10.0,
          'tail_base_price': 9.95, 'tail_vs_noon_ratio': 1.0, 'open': 9.9, 'high': 10.02,
          'close_position': 0.8, 'pct': 2.0}
p1, rd1 = RiskEngine.score(f_high, 'OTHER', turnover=5, theme_up_ratio=60, theme_limit_count=2)
check("高位风险扣分>0", p1 > 0, f"penalty={p1} detail={rd1}")

# 3.2 尾盘诱多: 量暴增价滞(涨0.05%) + 长上影(2%) + 收盘位0.6
f_trap = {'drawdown_20d': 5, 'gain_5d': 3, 'gain_20d': 8, 'ma20': 9.5, 'price': 9.8,
          'tail_base_price': 9.795, 'tail_vs_noon_ratio': 2.2, 'open': 9.75, 'high': 10.0,
          'close_position': 0.6, 'pct': 0.5}
p2, rd2 = RiskEngine.score(f_trap, 'OTHER', turnover=5, theme_up_ratio=60, theme_limit_count=2)
check("尾盘诱多被扣分", rd2.get('tail_distribution') is not None or rd2.get('upper_shadow_trap') is not None,
      f"penalty={p2} detail={rd2}")
check("尾盘诱多扣分>=6", p2 >= 6, f"penalty={p2}")

# 3.3 干净信号低风险
f_clean = {'drawdown_20d': 6, 'gain_5d': 2, 'gain_20d': 5, 'ma20': 9.6, 'price': 10.0,
           'tail_base_price': 9.92, 'tail_vs_noon_ratio': 1.5, 'open': 9.9, 'high': 10.02,
           'close_position': 0.88, 'pct': 1.2}
p3, rd3 = RiskEngine.score(f_clean, 'OTHER', turnover=3, theme_up_ratio=65, theme_limit_count=3)
check("干净信号风险<=5", p3 <= 5, f"penalty={p3} detail={rd3}")

# ═══════════════════════════════════════════
print("\n[4] ND2Engine 概率引擎")
# ═══════════════════════════════════════════
f_nd2 = {
    'drawdown_20d': 5, 'gain_20d': 8, 'gain_5d': 3, 'pct': 2.5,
    'tail_base_price': 9.9, 'price': 10.0, 'tail_vs_noon_ratio': 1.6,
    'close_position': 0.9, 'limit_up_20d': 2, 'ma5': 9.8, 'vol_shrink_ratio_3d': 0.7,
    'kline_ok': True,
}
s4, d4 = nd2_rule_score(f_nd2, PULLBACK_GAP)
check("ND2规则分0~15", 0 <= s4 <= 15, f"got {s4}")
check("ND2分档输出", 'grade' in d4, f"detail keys={list(d4.keys())[:5]}")

# 无历史DB时使用先验概率
eng = ND2Engine(db_path=r'D:\mystock\cache_daily\_nonexistent_nd2_test.db')
s5, d5 = eng.score(f_nd2, PULLBACK_GAP)
check("无历史时用先验概率", d5.get('p_up_2') == 0.45, f"p_up={d5.get('p_up_2')}")

# ═══════════════════════════════════════════
print("\n[5] ND2AlphaEngine 主评分器 + S/A/B分级")
# ═══════════════════════════════════════════
engine = ND2AlphaEngine(db_path=r'D:\mystock\cache_daily\_nonexistent_nd2_test.db')

# 5.1 强信号(理想PULLBACK_GAP): 强势基因+回调到位+尾盘强回流(量比2.0+涨0.8%+收盘位0.95)
sig = engine.evaluate(
    ts_code='600000.SH',
    q=make_quote(price=9.90, pct=1.1, last_close=9.79, high=9.92, low=9.78, open_p=9.80, vol=140000, name='测试股A'),
    kline=kl, snap=make_snap(noon_vol=60000, tail_base_vol=80000, tail_base_price=9.82, morning_vol=100000),
    turnover=3.0, total_mv=200000, theme_name='测试主题', theme_strength=6,
    theme_up_ratio=65, theme_limit_count=3, theme_leader_pct=5.0,
    trend_score=72, market_status='强趋势', index_pct=0.5,
)
check("强信号非None", sig is not None, "被过滤")
if sig:
    print(f"    [debug] 形态={sig['pattern']} 分项: 趋势{sig['trend_structure']} 形态{sig['pattern_quality']} "
          f"尾流{sig['tail_flow']} 基因{sig['strong_gene']} ND2 {sig['nd2_potential']} "
          f"主题{sig['theme_alpha']} 市场{sig['market_alpha']} bonus{sig['bonus']} 风险{sig['risk_penalty']}")
    check("强信号FinalScore>=70", sig['final_score'] >= 70, f"got {sig['final_score']}")
    check("信号含概率字段", 'p_up_2' in sig and 'rank_score' in sig and 'final_alpha' in sig)
    check("信号含形态", sig['pattern'] in (PULLBACK_GAP, BREAKOUT_TAIL, STEALTH_ACCUMULATION, OTHER))

# 5.2 弱市场乘数
mm = ND2AlphaEngine.market_multiplier(30)
check("极弱市场乘数0.5", abs(mm - 0.50) < 0.01, f"got {mm}")
mm2 = ND2AlphaEngine.market_multiplier(85)
check("极强市场乘数1.15", abs(mm2 - 1.15) < 0.01, f"got {mm2}")

# 5.3 S级门槛校验(构造各维度)
g, reason = ND2AlphaEngine.grade(88, 20, 12, 12, 3, 1.0, {})
check("多因子满足S级", g == 'S', f"got {g} reason={reason}")
g2, _ = ND2AlphaEngine.grade(88, 14, 12, 12, 3, 1.0, {})  # tailflow不足
check("尾流不足降A/B", g2 in ('A', 'B'), f"got {g2}")
g3, _ = ND2AlphaEngine.grade(88, 20, 12, 12, 3, 1.0, {'invalid_volume': True})  # 尾盘诱多
check("尾盘诱多禁S级", g3 != 'S', f"got {g3}")

# 5.4 硬过滤分层
# 涨幅<0.5%且尾盘弱 -> 过滤
f_weak_pct = {'pct': 0.3, 'price': 10.0, 'close_position': 0.6, 'tail_vs_noon_ratio': 0.5,
              'last_close': 9.97, 'high': 10.05, 'low': 9.9, 'ma20': 9.5, 'gain_5d': 2,
              'kline_ok': False}
passed, reason = ND2AlphaEngine.hard_filter('600000.SH', {}, None, f_weak_pct, 3, 200000, 5,
                                            tail_flow_score=8)
check("涨幅<0.5%且尾盘弱被过滤", not passed, f"reason={reason}")

# 涨幅<0.5%但尾盘强 -> 保留
passed2, reason2 = ND2AlphaEngine.hard_filter('600000.SH', {}, None, dict(f_weak_pct, close_position=0.85),
                                              3, 200000, 5, tail_flow_score=18)
check("涨幅<0.5%但尾盘强保留", passed2, f"reason={reason2}")

# 涨幅>8% -> 过滤
f_high_pct = dict(f_weak_pct, pct=9.0)
passed3, reason3 = ND2AlphaEngine.hard_filter('600000.SH', {}, None, f_high_pct, 3, 200000, 5)
check("涨幅>8%被过滤", not passed3, f"reason={reason3}")

# ═══════════════════════════════════════════
print("\n[6] ND2SnapshotStore 快照与标签回填")
# ═══════════════════════════════════════════
test_db = os.path.join(tempfile.gettempdir(), 'nd2_test_store.db')
if os.path.exists(test_db):
    os.remove(test_db)
store = ND2SnapshotStore(db_path=test_db)

if sig:
    test_signals = [sig]
    n_saved = store.save_snapshot('20260807', test_signals, market_status='强趋势')
    check("快照保存1只", n_saved == 1, f"got {n_saved}")

    # 模拟次日行情回填 (entry=9.90: 高10.20=+3.03%, 收10.15=+2.53%)
    def mock_fetch(ts_code, start, end):
        return pd.DataFrame({
            'trade_date': [end], 'open': [9.95], 'high': [10.20],
            'low': [9.75], 'close': [10.15],
        })

    filled = store.backfill_labels(mock_fetch, '20260807', '20260810')
    check("标签回填1只", filled == 1, f"got {filled}")

    # 验证标签: entry=9.90, next_high=10.20 -> +3.03% >= 2% -> Y_UP_2=1
    import sqlite3
    conn = sqlite3.connect(test_db)
    row = conn.execute("SELECT next_high_return, Y_UP_2, Y_CLOSE_2, Y_DD_2 FROM nd2_label WHERE signal_date='20260807'").fetchone()
    conn.close()
    if row:
        check("Y_UP_2标签正确(高+3.03%)", row[1] == 1, f"high_ret={row[0]:.4f}")
        check("Y_CLOSE_2标签正确(收+2.53%)", row[2] == 1, f"Y_CLOSE_2={row[2]}")
    else:
        check("标签已写入", False, "查无记录")

    pending = store.pending_backfill_dates('20260811')
    check("已回填日期不再pending", '20260807' not in pending, f"pending={pending}")

# ═══════════════════════════════════════════
print("\n[7] 报告输出")
# ═══════════════════════════════════════════
if sig:
    sig['grade'] = 'S'
    rpt = format_console_report([sig])
    check("控制台报告含关键信息", 'FinalScore' in rpt and 'P(高≥2%)' in rpt and '尾流' in rpt)
    msg = format_wechat_message([sig])
    check("微信消息含S级", msg and 'S级' in msg)

# ═══════════════════════════════════════════
print(f"\n{'='*50}")
print(f"测试结果: {PASS}通过 / {FAIL}失败")
print(f"{'='*50}")
sys.exit(1 if FAIL else 0)
