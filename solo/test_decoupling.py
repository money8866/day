# -*- coding: utf-8 -*-
"""V10.2 选股数据解耦回归测试

核心保证：**仓位建议与满仓授权不得受选股数据影响**。
这不只是「某天没选出票」的问题 ——
stock_picks.db 只覆盖 20260601 起，若选股数据参与计算，
1~5 月 132 天的环境判断会全部失真为 0。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_regime_v101 as V101


class _Cand:
    """最小候选股。"""
    def __init__(self, stop=0.08, rr=2.0):
        self.ts_code = "000001.SZ"
        self.name = "测试股"
        self.strategy_id = "s"
        self.signal = "s"
        self.action = "BUY"
        self.score = 1.0
        self.close = 10.0
        self.stop_price = 9.2
        self.target_price = 11.8
        self.industry = "测试行业"
        self.concept = "测试主题"
        self.stop_distance = stop
        self.risk_reward = rr
        self.liquidity_amount = 1e8


class _Opp:
    def __init__(self, n=0, available=True):
        self.qualified = [_Cand() for _ in range(n)]
        self.observe = []
        self.rejected = []
        self.data_available = available
        self.note = ""
        self._n = n

    @property
    def qualified_count(self):
        return len(self.qualified)


class _Mod:
    def __init__(self, passed=True, missing=None):
        self.passed = passed
        self.missing = missing or []
        self.evidence = []
        self.confidence = "高"
        self.details = {}


class TestStockDataDecoupling(unittest.TestCase):
    """选股数据不得影响仓位计算"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def _target(self, state, n_qualified, available=True, pr=None):
        opp = _Opp(n_qualified, available)
        if pr is None:
            pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg,
                                          state=state,
                                          authority=V101.position_authority(state, self.cfg))
        fa = V101.full_position_authorization(
            state, _Mod(True), _Mod(True), _Mod(True), _Mod(True), opp, pr, self.cfg)
        auth = V101.position_authority(state, self.cfg)
        return V101.calculate_target_position(
            state, auth, opp, pr, fa, None, self.cfg), fa

    def test_target_position_independent_of_qualified_count(self):
        """★ 不同合格标的数 → 相同的环境仓位建议"""
        for state in (V101.STATE_NORMAL, V101.STATE_REPAIR, V101.STATE_STRONG):
            t0, _ = self._target(state, 0)
            t1, _ = self._target(state, 1)
            t5, _ = self._target(state, 5)
            t20, _ = self._target(state, 20)
            self.assertEqual(t0.suggested, t5.suggested,
                             f"{state}: 0 只与 5 只的仓位建议应一致")
            self.assertEqual(t1.suggested, t20.suggested,
                             f"{state}: 1 只与 20 只的仓位建议应一致")

    def test_target_position_not_zero_without_stock_data(self):
        """★ 选股数据完全不可用时，仓位建议也不得为 0"""
        t, _ = self._target(V101.STATE_NORMAL, 0, available=False)
        self.assertGreater(t.suggested, 0,
                           "选股数据不可用不应把环境仓位建议压到 0")

    def test_target_position_capped_by_authority(self):
        """环境仓位建议不得超过仓位权限"""
        for state in (V101.STATE_NORMAL, V101.STATE_REPAIR, V101.STATE_STRONG):
            auth = V101.position_authority(state, self.cfg)
            t, _ = self._target(state, 0)
            self.assertLessEqual(t.suggested, auth.max_position)

    def test_authorization_not_gated_by_stock_count(self):
        """★ 合格标的不足不应否决满仓授权"""
        fa1 = V101.full_position_authorization(
            V101.STATE_STRONG, _Mod(True), _Mod(True), _Mod(True), _Mod(True),
            _Opp(1), V101.check_portfolio_risk([], 100, self.cfg), self.cfg)
        fa0 = V101.full_position_authorization(
            V101.STATE_STRONG, _Mod(True), _Mod(True), _Mod(True), _Mod(True),
            _Opp(0), V101.check_portfolio_risk([], 100, self.cfg), self.cfg)
        self.assertEqual(fa1.result, V101.FULL_ALLOWED)
        self.assertEqual(fa0.result, V101.FULL_ALLOWED)
        self.assertIn("不参与满仓授权判定", fa0.info)

    def test_authorization_checks_are_market_level_only(self):
        """授权的每一项都必须与环境相关，不得含个股/选股语义"""
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Mod(True), _Mod(True), _Mod(True), _Mod(True),
            _Opp(3), V101.check_portfolio_risk([], 100, self.cfg), self.cfg)
        for key in fa.checks:
            self.assertNotIn("个股", key, f"授权项「{key}」含个股语义")
            self.assertNotIn("合格", key, f"授权项「{key}」含选股语义")

    def test_ladder_not_gated_by_stock_count(self):
        """★ 无合格买点时，加仓阶梯不应被判为「停止」"""
        opp = _Opp(0)
        pr = V101.check_portfolio_risk([], 100, self.cfg,
                                      state=V101.STATE_NORMAL,
                                      authority=V101.position_authority(V101.STATE_NORMAL, self.cfg))
        fa = V101.full_position_authorization(
            V101.STATE_NORMAL, _Mod(True), _Mod(True), _Mod(True), _Mod(True),
            opp, pr, self.cfg)
        auth = V101.position_authority(V101.STATE_NORMAL, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_NORMAL, auth, opp, pr, fa, 20, self.cfg)
        lad = V101.plan_ladder_add(20, tp, V101.STATE_NORMAL, fa, pr, opp, self.cfg)
        self.assertTrue(lad.can_add,
                        "选股数量不应成为加仓闸门")
        self.assertIn("不影响加仓阶梯判定", lad.info)

    def test_portfolio_risk_works_without_candidates(self):
        """★ 无候选标的时，组合风险预算仍可计算（用 ETF 波动率代理）"""
        pr = V101.check_portfolio_risk([], 60, self.cfg,
                                      state=V101.STATE_NORMAL,
                                      authority=V101.position_authority(V101.STATE_NORMAL, self.cfg),
                                      fallback_stop_distance=0.03)
        self.assertGreater(pr.total_risk_pct, 0, "代理口径应算出风险敞口")
        self.assertTrue(pr.used_proxy)
        self.assertAlmostEqual(pr.proxy_stop_distance, 0.03, places=4)

    def test_stock_info_still_reported(self):
        """选股信息仍应出现在报告里（供建仓参考），只是不参与计算"""
        t, fa = self._target(V101.STATE_NORMAL, 7)
        self.assertEqual(t.qualified_count, 7, "选股数量仍应记录")
        self.assertTrue(any("合格买点" in x for x in t.limited_by),
                        "限制原因中应保留选股提示")


class TestVolatilityProxy(unittest.TestCase):
    """ETF 波动率代理"""

    def test_proxy_produces_stop_distance(self):
        try:
            import etf_mainline as em
        except ImportError:
            self.skipTest("无 etf_mainline")
        if em.load_etf_panel().empty:
            self.skipTest("无 ETF 数据")
        v = em.estimate_market_volatility()
        self.assertIsNotNone(v.get("stop_distance"))
        sd = v["stop_distance"]
        self.assertGreater(sd, 0)
        self.assertLessEqual(sd, 0.12, "代理止损距离应受上限约束")
        self.assertGreaterEqual(sd, 0.03, "代理止损距离应受下限约束")
        self.assertGreater(v["sample"], 30, "样本量应足够")


if __name__ == "__main__":
    unittest.main()
