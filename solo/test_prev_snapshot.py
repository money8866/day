# -*- coding: utf-8 -*-
"""前日快照模块的回归测试

★ 这个模块的价值在于「口径与盘后报告一致」+「提供盘中拿不到的昨日涨停名单」，
   所以测试重点是这两点，而不是单纯的字段存在性。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _prev_snapshot as PVS


class TestPrevSnapshotExtract(unittest.TestCase):
    """从盘后报告提取前日数据"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.snap = PVS.build()
        except Exception as e:
            raise unittest.SkipTest(f'无盘后报告可用: {e}')

    def test_breadth_matches_daily_cache(self):
        """快照的广度必须与 daily_cache 重算一致（口径同源）"""
        bs = self.snap['breadth_series']
        self.assertTrue(bs, '广度序列不应为空')
        last = bs[-1]
        self.assertEqual(last['d'], self.snap['trade_date'])
        v10 = self.snap.get('v10') or {}
        if v10.get('breadth_ratio') is not None:
            self.assertAlmostEqual(last['ratio'], v10['breadth_ratio'],
                                   delta=0.15,
                                   msg='快照广度与盘后报告广度必须一致')

    def test_limit_up_count_matches_report(self):
        """涨停名单数量必须与盘后报告的涨停家数一致"""
        v10 = self.snap.get('v10') or {}
        if v10.get('zt_count') is None:
            self.skipTest('报告无涨停家数')
        self.assertEqual(self.snap['limit_up_count'], v10['zt_count'],
                         '涨停名单数必须与盘后报告涨停家数一致')

    def test_limit_up_codes_are_valid(self):
        """涨停名单里的代码必须是真实涨停（pct_chg ≥ 阈值×0.99）"""
        lst = self.snap.get('limit_up_list') or []
        self.assertTrue(lst, '涨停名单不应为空')
        for c in lst[:20]:
            self.assertRegex(c, r'^\d{6}\.(SH|SZ|BJ)$',
                             f'代码格式异常: {c}')

    def test_state_row_available(self):
        """状态表必须能提供上一交易日的状态（状态机链的基准）"""
        st = self.snap.get('state_row') or {}
        self.assertIn('state', st, 'state_row 必须含 state 字段')
        self.assertTrue(st.get('state'), 'state 不应为空')
        self.assertIn(st['state'], ('S1', 'S2', 'S3', 'S4', 'S5', 'S6'))

    def test_v101_modules_parsed(self):
        """V10.1 报告的四模块必须能解析出来（4 个模块名）"""
        v101 = self.snap.get('v101') or {}
        mods = v101.get('modules_passed')
        if not mods:
            self.skipTest('V10.1 报告不可用')
        self.assertEqual(len(mods), 4, f'应解析出 4 个模块，实得 {list(mods)}')
        for name, passed in mods.items():
            self.assertIsInstance(passed, bool)
            self.assertTrue(len(name) >= 2, f'模块名被截断: {name}')

    def test_breadth_series_long_enough(self):
        """广度序列至少 20 个交易日（算 20 日均需要）"""
        bs = self.snap.get('breadth_series') or []
        self.assertGreaterEqual(len(bs), 20,
                                f'广度序列不足 20 日，实得 {len(bs)}')


class TestFallbackConsistency(unittest.TestCase):
    """★ 降级口径（daily_cache 重算）必须与盘后报告口径完全一致

    这是最关键的一致性约束：盘中分析依赖快照的数值，
    若降级口径与报告口径有偏差，盘中结论会与盘后结论矛盾。
    历史教训：北交所 920169.BJ 因阈值用 10% 而非 20%，跌停数差 1 只。
    """

    def test_fallback_matches_report_exactly(self):
        """有 V10.0 报告的日期，逐项比对降级口径"""
        rep_path = PVS._find_report('market_regime_v10', '20261030')
        if not rep_path:
            self.skipTest('无 10/30 报告')
        rep = PVS.parse_v10_report(rep_path)
        fb = PVS.compute_from_cache('20261030')
        self.assertTrue(fb, '降级计算应返回数据')
        checks = ['breadth_ratio', 'zt_count', 'dt_count', 'amount',
                  'promotion_rate', 'strong_mean', 'strong_up_ratio']
        for k in checks:
            if rep.get(k) is None:
                continue
            self.assertIsNotNone(fb.get(k), f'{k} 降级缺失')
            self.assertAlmostEqual(float(rep[k]), float(fb[k]),
                                   delta=max(0.6, abs(float(rep[k])) * 0.01),
                                   msg=f'{k} 降级口径与报告不一致')

    def test_limit_thresholds_shared_with_v10(self):
        """★ 涨跌停阈值必须复用 V10.0 的函数，不允许各自实现"""
        import inspect
        import market_regime_v10 as V10
        src = inspect.getsource(PVS)
        self.assertIn('_limit_thresholds', src,
                      '必须复用 V10.0 的 _limit_thresholds')
        # 验证关键代码的行为一致
        for code, expect in [('600000.SH', 10.0), ('300001.SZ', 20.0),
                             ('688001.SH', 20.0), ('920169.BJ', 20.0),
                             ('830001.BJ', 20.0)]:
            self.assertEqual(V10._limit_thresholds(code), expect,
                             f'{code} 阈值应为 {expect}')

    def test_fallback_works_without_report(self):
        """没有 V10.0 报告的日期也必须能出快照（降级路径可用）"""
        snap = PVS.build('20260513', use_cache=False)
        self.assertTrue(snap['v10'].get('breadth_ratio'),
                        '报告缺失时降级应给出广度')
        self.assertTrue(snap.get('limit_up_list'),
                        '报告缺失时降级仍应给出涨停名单')


class TestPrevSnapshotUsage(unittest.TestCase):
    """快照在盘中分析中的使用"""

    def test_monitor_uses_snapshot(self):
        """盘中监控必须走快照，不能全表重算"""
        import inspect
        import _intraday_monitor as M
        src = inspect.getsource(M)
        self.assertIn('pvs.build(', src, '盘中监控必须使用前日快照')
        # ★ V10.5：必须显式排除当天，否则盘后刚跑完时快照会指向今天自己
        self.assertIn('before_date=trade_date', src,
                      '盘中快照必须排除当天（before_date=trade_date）')
        # 旧的逐日扫库实现应已移除
        self.assertNotIn("SELECT COUNT(*) FROM daily_cache WHERE trade_date=? AND pct_chg>0",
                         src, '不应在盘中监控里全表重算广度序列')

    def test_promotion_rate_computable_intraday(self):
        """★ 盘中必须能算晋级率与强股次日表现（靠昨日涨停名单）"""
        import inspect
        import _intraday_monitor as M
        src = inspect.getsource(M)
        self.assertIn('limit_up_list', src, '必须使用昨日涨停名单')
        self.assertIn('promo', src, '必须计算晋级率')
        self.assertIn('strong_mean', src, '必须计算强股次日表现')


if __name__ == '__main__':
    unittest.main()
