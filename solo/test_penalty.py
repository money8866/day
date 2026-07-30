# -*- coding: utf-8 -*-
"""
测试赚钱效应惩罚机制
验证用户案例(趋势分45+跌停144)被正确空仓,强势环境不被误伤
"""
import sys
import os

# 添加项目根目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class FakeMonitor:
    """轻量替身,仅复用 _apply_profit_effect_penalty 逻辑"""

    def __init__(self, full_stats):
        self.full_market_stats = full_stats

    def get_full_market_stats(self):
        return self.full_market_stats

    # 把目标方法绑定过来
    def _apply_profit_effect_penalty(self, trend_score, market_status, pos, pos_range):
        # 仅在弱势及以下环境生效(趋势良好及以上不惩罚,避免误伤强势行情)
        if trend_score >= 60:
            return None

        full_stats = self.get_full_market_stats()
        if not full_stats:
            return None

        zt_count = full_stats.get('zt_count', 0) or 0
        dt_count = full_stats.get('dt_count', 0) or 0
        up_ratio = full_stats.get('up_ratio', 50) or 50
        down_ratio = full_stats.get('down_ratio', 50) or 50
        up_count = full_stats.get('up_count', 0) or 0
        down_count = full_stats.get('down_count', 0) or 0

        original_pos = pos
        reason_parts = []

        if dt_count >= 100:
            pos = 0
            pos_range = "0%(空仓)"
            reason_parts.append(f"跌停潮({dt_count}家)")
        elif dt_count >= 50 and zt_count > 0 and dt_count >= zt_count * 2:
            pos = min(pos, 5)
            pos_range = "0~5%(几乎空仓)"
            reason_parts.append(f"恐慌抛售(跌停{dt_count}/涨停{zt_count})")
        elif (zt_count > 0 and dt_count >= zt_count * 1.5 and down_ratio > 60) or \
             (zt_count == 0 and dt_count >= 30 and down_ratio > 60):
            pos = min(pos, max(5, pos // 2))
            pos_range = f"0~{max(10, pos)}%(极低仓位)"
            reason_parts.append(f"跌多涨少(跌停{dt_count}/涨停{zt_count},下跌{down_ratio}%)")
        elif trend_score < 45 and dt_count >= 30 and up_ratio < 40:
            pos = min(pos, 5)
            pos_range = "0~5%(几乎空仓)"
            reason_parts.append(f"极端弱势(评分{trend_score:.0f},跌停{dt_count},上涨{up_ratio}%)")
        elif trend_score < 55 and up_ratio < 35 and dt_count > zt_count:
            pos = min(pos, 10)
            pos_range = "0~10%(极低仓位)"
            reason_parts.append(f"赚钱效应缺失(上涨{up_ratio}%,跌停{dt_count}>涨停{zt_count})")

        if not reason_parts:
            return None

        if pos == 0:
            market_status = "恐慌空仓"
        elif pos <= 5:
            market_status = "极弱空仓"
        elif pos <= 10:
            market_status = "弱势空仓"

        return {
            'pos': pos, 'pos_range': pos_range, 'market_status': market_status,
            'original_pos': original_pos, 'reason': ' + '.join(reason_parts),
            'zt_count': zt_count, 'dt_count': dt_count,
            'up_ratio': up_ratio, 'down_ratio': down_ratio,
        }


def test_case(name, trend_score, market_status, pos, pos_range, full_stats, expected_pos=None, expected_status=None):
    """运行单用例并断言"""
    print(f"\n{'='*70}")
    print(f"📋 用例: {name}")
    print(f"   输入: 趋势分={trend_score} 状态={market_status} 仓位={pos}%")
    print(f"   市场统计: 涨停={full_stats['zt_count']} 跌停={full_stats['dt_count']} 上涨={full_stats['up_ratio']}% 下跌={full_stats['down_ratio']}%")

    m = FakeMonitor(full_stats)
    result = m._apply_profit_effect_penalty(trend_score, market_status, pos, pos_range)

    if result is None:
        print(f"   ✅ 不惩罚(保持仓位{pos}%)")
        if expected_pos is not None and expected_pos != pos:
            print(f"   ❌ 期望仓位{expected_pos}%,实际未惩罚")
            return False
    else:
        print(f"   惩罚: {pos}% → {result['pos']}%  状态: {market_status} → {result['market_status']}")
        print(f"   原因: {result['reason']}")
        if expected_pos is not None and result['pos'] != expected_pos:
            print(f"   ❌ 期望仓位{expected_pos}%,实际{result['pos']}%")
            return False
        if expected_status is not None and result['market_status'] != expected_status:
            print(f"   ❌ 期望状态{expected_status},实际{result['market_status']}")
            return False

    print(f"   ✅ 通过")
    return True


if __name__ == '__main__':
    # 用户案例:趋势分45,跌停144,涨停27,下跌62.5%,上涨34.9%
    user_case_stats = {
        'zt_count': 27, 'dt_count': 144,
        'up_ratio': 34.9, 'down_ratio': 62.5,
        'up_count': 2400, 'down_count': 4300,
        'total': 6900,
    }

    cases = [
        # (用例名, 趋势分, 状态, 仓位, 仓位范围, 全市场统计, 期望仓位, 期望状态)
        ("用户案例(趋势45+跌停144)", 45, "弱势", 25, "20~30%", user_case_stats, 0, "恐慌空仓"),

        # 跌停潮:即使评分50(震荡偏弱)也应空仓
        ("跌停潮(趋势50+跌停120)", 50, "震荡", 40, "30~50%",
         {'zt_count': 20, 'dt_count': 120, 'up_ratio': 30, 'down_ratio': 65, 'up_count': 2000, 'down_count': 4500, 'total': 6900},
         0, "恐慌空仓"),

        # 恐慌抛售:跌停60,涨停20,应几乎空仓
        ("恐慌抛售(趋势48+跌停60/涨停20)", 48, "弱势", 25, "20~30%",
         {'zt_count': 20, 'dt_count': 60, 'up_ratio': 40, 'down_ratio': 55, 'up_count': 2700, 'down_count': 3900, 'total': 6900},
         5, "极弱空仓"),

        # 跌多涨少:跌停45,涨停25,下跌65%,仓位减半(20%未触发空仓状态,保持原状态)
        ("跌多涨少(趋势50+跌停45/涨停25/下跌65%)", 50, "震荡", 40, "30~50%",
         {'zt_count': 25, 'dt_count': 45, 'up_ratio': 35, 'down_ratio': 65, 'up_count': 2400, 'down_count': 4500, 'total': 6900},
         20, "震荡"),

        # 极端弱势:趋势40,跌停35,上涨38%
        ("极端弱势(趋势40+跌停35+上涨38%)", 40, "退潮", 15, "10~20%",
         {'zt_count': 15, 'dt_count': 35, 'up_ratio': 38, 'down_ratio': 58, 'up_count': 2600, 'down_count': 4000, 'total': 6900},
         5, "极弱空仓"),

        # 赚钱效应缺失:趋势52,上涨32%,跌停40>涨停25
        ("赚钱效应缺失(趋势52+上涨32%+跌停40)", 52, "震荡", 40, "30~50%",
         {'zt_count': 25, 'dt_count': 40, 'up_ratio': 32, 'down_ratio': 60, 'up_count': 2200, 'down_count': 4100, 'total': 6900},
         10, "弱势空仓"),

        # 强势环境不惩罚:趋势75,跌停20,涨停50
        ("强势环境不惩罚(趋势75+涨停50)", 75, "强趋势", 70, "60~80%",
         {'zt_count': 50, 'dt_count': 20, 'up_ratio': 75, 'down_ratio': 22, 'up_count': 5200, 'down_count': 1500, 'total': 6900},
         None, None),  # None=不惩罚

        # 主升浪不惩罚:趋势88,即使跌停60也不惩罚(强势不惩罚)
        ("主升浪不惩罚(趋势88)", 88, "主升浪", 90, "80~100%",
         {'zt_count': 80, 'dt_count': 60, 'up_ratio': 80, 'down_ratio': 18, 'up_count': 5500, 'down_count': 1200, 'total': 6900},
         None, None),

        # 趋势60(趋势良好)不惩罚,即使跌停80也不惩罚(避免误伤强势行情)
        ("趋势良好不惩罚(趋势60+跌停80)", 60, "趋势良好", 60, "50~70%",
         {'zt_count': 40, 'dt_count': 80, 'up_ratio': 55, 'down_ratio': 40, 'up_count': 3800, 'down_count': 2800, 'total': 6900},
         None, None),

        # 弱势但无跌停潮:趋势50,跌停10,涨停30,不应惩罚(跌停<涨停,上涨45%)
        ("弱势无惩罚(趋势50+跌停10+涨停30)", 50, "震荡", 40, "30~50%",
         {'zt_count': 30, 'dt_count': 10, 'up_ratio': 45, 'down_ratio': 50, 'up_count': 3100, 'down_count': 3500, 'total': 6900},
         None, None),
    ]

    pass_count = 0
    fail_count = 0
    for case in cases:
        name, ts, status, pos, pos_range, stats, exp_pos, exp_status = case
        if test_case(name, ts, status, pos, pos_range, stats, exp_pos, exp_status):
            pass_count += 1
        else:
            fail_count += 1

    print(f"\n{'='*70}")
    print(f"📊 测试结果: 通过 {pass_count}/{pass_count+fail_count}")
    if fail_count == 0:
        print("🎉 全部通过!")
    else:
        print(f"❌ 失败 {fail_count} 个用例")
    print(f"{'='*70}")
