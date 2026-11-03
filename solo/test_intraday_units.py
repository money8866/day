# -*- coding: utf-8 -*-
"""★ 盘中监控的单位一致性回归测试

起因：新浪 hq.sinajs.cn 的成交额单位是「元」，而
     - etf_daily 库内存 tushare 的「千元」
     - daily_cache 库内存 tushare 的「千元」
不归一会让 ETF 量能比虚高约 1000 倍（实测传媒 amt_ratio 显示 19.71 倍，
实际约 1.3 倍），进而让「资金放大」这条加分项失去意义。
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)


class TestIntradayUnitConsistency(unittest.TestCase):

    def test_etf_amount_converted_to_thousands(self):
        """★ ETF 实时成交额必须除以 1000 归一到库内单位（千元）"""
        import inspect
        import _intraday_monitor as M
        src = inspect.getsource(M)
        self.assertIn('/ 1000.0', src,
                      "ETF 实时成交额必须除以 1000（元 → 千元）")
        self.assertIn('元 → 千元', src,
                      "必须注明单位换算方向")

    def test_market_amount_in_yi(self):
        """全市场成交额：新浪是元，÷1e8 得亿；库内是千元，÷1e5 得亿"""
        import inspect
        import _intraday_monitor as M
        src = inspect.getsource(M)
        self.assertIn('/ 1e8', src, "新浪实时成交额 ÷1e8 得亿元")

    def test_previous_amount_uses_cache_thousands(self):
        """前日成交额来自库内（千元）→ 报告里是亿元，前后可比"""
        import inspect
        import _prev_snapshot as PVS
        src = inspect.getsource(PVS)
        self.assertIn('/ 1e5', src, "库内成交额 ÷1e5 得亿元")

    def test_no_double_conversion(self):
        """不能既除 1000 又除 1e5（会差 100 倍）"""
        import inspect
        import _intraday_monitor as M
        src = inspect.getsource(M)
        # 同一行里不能同时出现 /1000 和 /1e5
        for line in src.split('\n'):
            if '/ 1000' in line and '1e5' in line:
                self.fail(f"同一行出现双重单位换算: {line.strip()}")


class TestEtfPanelUnitScale(unittest.TestCase):
    """用实际数据验证：库内与新浪的 ETF 成交额比值确实在 1000 附近"""

    def test_sina_vs_panel_amount_ratio(self):
        import glob
        import os as _os
        import pandas as pd
        import etf_mainline as em

        rt_files = glob.glob(_os.path.join(BASE, '_bt_out', 'etf_realtime_*.csv'))
        if not rt_files:
            self.skipTest('无实时 ETF 快照')
        rt = pd.read_csv(sorted(rt_files)[-1])
        panel = em.load_etf_panel()
        if panel is None or panel.empty:
            self.skipTest('ETF 面板不可用')

        last_date = panel.trade_date.max()
        hist = panel[panel.trade_date == last_date].set_index('ts_code')
        cur = rt.set_index('ts_code')
        common = [c for c in cur.index if c in hist.index]
        if len(common) < 5:
            self.skipTest('共同样本不足')

        ratios = []
        for c in common:
            pa = float(hist.loc[c, 'amount'])
            sa = float(cur.loc[c, 'amount'])
            if pa > 0 and sa > 0:
                ratios.append(sa / pa)
        if not ratios:
            self.skipTest('无有效比值')

        import statistics
        med = statistics.median(ratios)
        self.assertGreater(med, 500,
                           f'新浪/库内 成交额比值中位 {med:.0f}，'
                           f'应接近 1000（新浪=元，库内=千元）')
        self.assertLess(med, 2000,
                        f'比值中位 {med:.0f} 异常偏离 1000')


if __name__ == '__main__':
    unittest.main()


class TestIntradayPostCloseConsistency(unittest.TestCase):
    """★ V10.5：盘中与盘后必须给出**同一个仓位建议**

    起因（2026-11-02 真实事故）：
      · 盘中 14:50 给 20%
      · 盘后跑完给 5%
      · 用户问「为什么盘后又是 5%，和盘中不一致」

    两个独立根因：
      1. **投票链断链**：V10.1 保存状态时漏传 vote_history → 链每天被清空
         → 窗口内永远只有今日 1 票 → min_votes=2 永不达标 → 状态机冻结在 S3
      2. **两套逻辑**：盘中用自定义启发式（自己判 S3/S4/S5、自己定档位），
         盘后用 V10.1 状态机+权限表 → 同一天必然可能分叉

    本类锁死修复后的行为。
    """

    def test_v101_saves_vote_history(self):
        """★ V10.1 持久化状态时必须传 vote_history"""
        import inspect
        import market_regime_v101 as V101
        src = inspect.getsource(V101.analyze_trade_date_v101)
        self.assertIn('vote_history', src,
                      'V10.1 必须把 vote_history 传给 save_state')

    def test_vote_chain_self_heal_exists(self):
        """状态机必须有投票链自愈机制"""
        import inspect
        import market_regime_v10 as V10
        self.assertTrue(hasattr(V10, 'rebuild_vote_history'),
                        '必须提供 rebuild_vote_history（投票链自愈）')
        src = inspect.getsource(V10._decide_state_inner)
        self.assertIn('rebuild_vote_history', src,
                      'decide_state 必须在链断时调用自愈')

    def test_intraday_uses_real_state_machine(self):
        """★ 盘中必须调用真实状态机，不能用自定义启发式"""
        import inspect
        import _intraday_monitor as M
        src = inspect.getsource(M)
        self.assertIn('_state_via_v10', src,
                      '盘中必须通过 _state_via_v10 调用真实状态机')
        self.assertIn('position_authority', src,
                      '盘中必须用 V10.1 的权限表')
        # 旧的启发式分支不应还在
        self.assertNotIn("state_guess = 'S5'", src,
                         '旧的启发式状态判定应已移除')

    def test_same_day_same_position(self):
        """★ 核心：同一天，盘中与盘后的仓位建议必须一致

        2026-11-02 是真实事故日：盘中 20% vs 盘后 5%。
        """
        import market_regime_v10 as V10
        import market_regime_v101 as V101

        # 盘后口径
        cfg = V101.load_config()
        r = V101.analyze_trade_date_v101('20261102', cfg, None, persist=False)
        post_state = r.state
        post_cap = int(r.authority.max_position)

        # 盘中口径（用收盘数据构造等价快照 → 应得同一结论）
        import _intraday_monitor as M
        import pandas as pd
        snap = M.pvs.build(before_date='20261102')
        v10cfg = V10.load_config()
        base = V10.collect_market_snapshot(snap['trade_date'], v10cfg)

        import copy
        s2 = copy.deepcopy(base)
        s2.trade_date = '20261102'
        b = s2.breadth
        hist = list(b._hist or [])
        b.breadth_ratio = 85.12456810329151 / 100
        b.delta_pp = 85.12456810329151 - (snap.get('v10') or {}).get('breadth_ratio', 16.25)
        if hist:
            hist = hist[:-1] + [85.12456810329151]
        b._hist = hist
        s2.limit.zt_count = 125
        s2.limit.dt_count = 10

        node = V10.detect_market_nodes(s2, v10cfg)
        prev = V10.load_prev_state('20261102')
        dec = V10.decide_state(s2, node, prev, v10cfg)
        intra_state = dec.state
        intra_cap = int(V101.position_authority(intra_state, cfg).max_position)

        self.assertEqual(intra_state, post_state,
                         f'盘中状态 {intra_state} 应与盘后 {post_state} 一致')
        self.assertEqual(intra_cap, post_cap,
                         f'盘中仓位 {intra_cap}% 应与盘后 {post_cap}% 一致')

    def test_post_close_not_frozen_in_panic(self):
        """★ 盘后不应再冻结在 S3 —— 极端反转日必须能升到 S4

        回归场景：2026-11-02 广度 16.3%→85.1%、涨停 46→125、跌停 95→10，
        盘后却给 5%（S3），与盘中 20% 矛盾。
        """
        import market_regime_v101 as V101
        cfg = V101.load_config()
        r = V101.analyze_trade_date_v101('20261102', cfg, None, persist=False)
        self.assertNotEqual(r.state, 'S3',
                            '20261102 应脱离 S3（广度 85% 的极端反转日）')
        self.assertEqual(r.state, 'S4')
        self.assertEqual(r.authority.max_position, 20)


class TestIndexDataFreshness(unittest.TestCase):
    """★ V10.6：指数数据不得静默沿用旧值

    起因（2026-11-03 真实事故）：
      盘后报告写「上证指数 3638.03 +1.08%、中证2000 2681.29 +2.47%」
      —— 那是 **11-02** 的数据；11-03 真实为 上证 3608.10 **-0.82%**、
      中证2000 2623.84 **-2.14%**、创业板指 -2.73%、科创50 -3.81%（全线下跌）。
      报告据此写「量价形态：放量上涨」，**完全颠倒了当日方向**。

    根因：`_query_index_hist` 只查 index_daily_cache，该表缺当日时
    返回的序列末行是上一交易日，下游直接取末行当「当日」。
    """

    def test_query_index_hist_rejects_stale_data(self):
        """★ 返回的序列末行必须等于目标交易日，否则返回 None"""
        import inspect
        import market_regime_v10 as V10
        src = inspect.getsource(V10._query_index_hist)
        self.assertIn('last_dt != str(end_date)', src,
                      "必须校验序列末行等于目标交易日")
        self.assertIn('_INDEX_STALE_FLAGS', src,
                      "过期必须记录标记，不能静默")

    def test_stale_index_flagged_in_quality_flags(self):
        """★ 指数数据过期必须出现在快照 quality_flags 里"""
        import inspect
        import market_regime_v10 as V10
        src = inspect.getsource(V10.collect_market_snapshot)
        self.assertIn('_INDEX_STALE_FLAGS', src,
                      "过期指数必须写入 quality_flags")

    def test_index_backfill_tool_exists(self):
        """必须有指数补数据工具，且盘后流程会调用"""
        import io
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '_index_backfill.py')
        self.assertTrue(os.path.exists(path), "缺少 _index_backfill.py")
        src = io.open(path, encoding='utf-8').read()
        self.assertIn('def backfill', src)
        self.assertIn('def missing_index_dates', src)

    def test_post_close_updates_index_cache(self):
        """★ 盘后流程必须在 V10.0/V10.1 分析前补齐指数数据"""
        import io
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'market_analysis.py')
        src = io.open(path, encoding='utf-8').read()
        self.assertIn('_index_backfill', src,
                      "盘后流程必须调用指数补数据")
        i_idx = src.find('_index_backfill')
        i_v10 = src.find('analyze_trade_date as v10_analyze')
        i_v101 = src.find('analyze_trade_date_v101 as v101_analyze')
        self.assertLess(i_idx, i_v10,
                        "指数补齐必须在 V10.0 分析之前")
        self.assertLess(i_idx, i_v101,
                        "指数补齐必须在 V10.1 分析之前")

    def test_index_cache_no_recent_gaps(self):
        """index_daily_cache 最近不应有缺口"""
        from _index_backfill import missing_index_dates
        gaps = missing_index_dates()
        self.assertEqual(gaps, [],
                         f"指数缓存存在缺口 {gaps}，报告会沿用旧数据。"
                         f"运行 `python _index_backfill.py` 补齐")
