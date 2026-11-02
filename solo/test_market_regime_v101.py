# -*- coding: utf-8 -*-
"""
V10.1 动态仓位管理 —— 单元测试
=========================================
覆盖任务要求的 8 项测试：
  测试1 极端下跌（20261030 真实数据）
  测试2 弱势反弹（指数涨但广度/赚钱效应未改善）
  测试3 卖压衰减
  测试4 市场修复
  测试5 强势主升
  测试6 机会不足
  测试7 组合风险超限
  测试8 市场突然转弱（收回满仓权限）

运行：python -m unittest test_market_regime_v101 -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_regime_v101 as V101

TD_PANIC = "20261030"      # 极端下跌（真实数据）
TD_STRONG = "20261023"     # 强势主升候选（真实数据，机器人 91.0 情绪+趋势共振）


class _Fake:
    """构造测试用的模块结果。"""

    @staticmethod
    def module(passed=True, missing=None, name="测试模块"):
        return V101.ModuleResult(
            name=name, passed=passed, confidence="高" if passed else "低",
            evidence=[f"{name} 证据"], missing=missing or [])


class _FakeOpp:
    """构造测试用的机会集。

    ★ 默认给**分散**的行业/主题与**低**止损距离 —— 这样构造的组合是合格的，
      用于验证「十项全过 → ALLOWED」。集中度/风险超限的场景由专门用例构造。
    """

    def __init__(self, qualified=0, observe=0, available=True, note="",
                 industries=None, concepts=None,
                 stop_distance=0.03, risk_reward=4.0):
        inds = industries or [f"行业{i}" for i in range(qualified)]
        cons = concepts or [f"主题{i}" for i in range(qualified)]
        self.qualified = [V101.StockCandidate(
            ts_code=f"00000{i:02d}.SZ", name=f"测试股{i}",
            close=10.0, stop_price=10.0 * (1 - stop_distance),
            target_price=10.0 + (10.0 * (1 - stop_distance)) * risk_reward,
            industry=inds[i] if i < len(inds) else f"行业{i}",
            concept=cons[i] if i < len(cons) else f"主题{i}",
            stop_distance=stop_distance, risk_reward=risk_reward)
            for i in range(qualified)]
        self.observe = [V101.StockCandidate(ts_code="", name="") for _ in range(observe)]
        self.rejected = []
        self.data_available = available
        self.note = note

    @property
    def qualified_count(self):
        return len(self.qualified)


class TestV101StrongUptrendModules(unittest.TestCase):
    """S6 四模块：各自独立判定，不合并成单一总分。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def test_four_modules_independent(self):
        """四个模块必须分别有结论，不能只输出一个总分"""
        r = V101.detect_strong_uptrend(TD_PANIC, self.cfg)
        self.assertEqual(len(r.modules), 4, "必须有四个独立模块")
        for name, m in r.modules.items():
            self.assertIsInstance(m.passed, bool, f"{name} 必须有独立结论")
            self.assertTrue(m.evidence, f"{name} 必须输出证据")

    def test_panic_day_not_strong(self):
        """测试1：极端下跌不得判为强势主升"""
        r = V101.detect_strong_uptrend(TD_PANIC, self.cfg)
        self.assertFalse(r.is_strong, "10/30 普跌不得判为强势主升")

    def test_panic_day_all_modules_reject(self):
        """测试1：极端下跌时各模块都应否决（至少广度与赚钱效应）"""
        r = V101.detect_strong_uptrend(TD_PANIC, self.cfg)
        self.assertFalse(r.modules["市场广度"].passed, "广度 16% 不得通过")
        self.assertFalse(r.modules["短线赚钱效应"].passed, "跌停 95 不得通过")
        self.assertFalse(r.modules["指数趋势"].passed, "全线跌破 MA20 不得通过")

    def test_single_day_breadth_not_enough(self):
        """测试2：单日广度高不得直接进 S6。

        ★ V10.4：连续性要求由「连续 2 日达标」改为「**广度改善方向**」
          （5日均广度 ≥ 20日均广度）+ 5日均底线。
          原「连续2日≥55%」实测边际价值 -0.6pp（6格中5格为负），
          因为它把「单日脉冲」也当噪音，而 2026 广度日均摆动 25pp，
          连续 2 日站上 55% 几乎只出现在行情末端。
        """
        cfg = self.cfg
        b = cfg["strong_uptrend"]["breadth"]
        # 单日达标不再是充分条件 —— 必须有改善方向 + 均线底线
        self.assertIn("breadth_ma5_min", b, "必须校验 5 日均底线")
        self.assertIn("breadth_ma20_min", b, "必须校验 20 日均")
        self.assertLessEqual(b["breadth_ma5_min"], 0.50,
                             "5日均底线不应高于 50%（实测 45% 更稳）")
        # 改进方向是代码内的硬条件（5日均 ≥ 20日均）
        import inspect
        src = inspect.getsource(V101.check_breadth)
        self.assertIn("ma5 >= ma20", src,
                      "广度模块必须包含「5日均 ≥ 20日均」的改善方向判定")
        self.assertIn("ma5 >= ma20", src, "改善方向判定必须真实存在")

    def test_index_trend_requires_multiple_indexes(self):
        """指数趋势不能用单一指数代表全市场"""
        cfg = self.cfg
        self.assertGreaterEqual(cfg["strong_uptrend"]["index_trend"]["min_index_count"], 3,
                                "至少要求 3 个指数")

    def test_mainline_requires_days_strong(self):
        """主线持续性必须要求连续强态天数，不能只看热度分"""
        mc = self.cfg["strong_uptrend"]["mainline"]
        self.assertGreaterEqual(mc["min_days_strong"], 2, "主线必须连续多日")
        self.assertIn("趋势主线", mc["allowed_mainline_type"])
        self.assertIn("情绪+趋势共振", mc["allowed_mainline_type"])


class TestV101PositionAuthority(unittest.TestCase):
    """仓位权限：市场环境允许的最高仓位。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def test_authority_by_state(self):
        # ★ V10.2：S5(修复确认) 权限由 60% 调整为 40%，S4(卖压衰减) 由 10% 调整为 20%，
        #   形成 S3(5)→S4(20)→S2(30)→S5(40)→S1(60)→S6(100) 的连续梯度。
        expected = {"S1": 60, "S2": 30, "S3": 5, "S4": 20, "S5": 40, "S6": 100}
        for st, mx in expected.items():
            pa = V101.position_authority(st, self.cfg)
            self.assertEqual(pa.max_position, mx, f"{st} 仓位权限应为 {mx}%")

    def test_panic_denies_full(self):
        """S3 恐慌加速：权限 5%，不允许进入 S6"""
        pa = V101.position_authority(V101.STATE_PANIC, self.cfg)
        self.assertEqual(pa.max_position, 5)
        self.assertFalse(pa.allow_enter_strong)

    def test_strong_allows_100_but_not_automatic(self):
        """S6 权限可到 100%，但 allow_enter_strong 不等于自动买入"""
        pa = V101.position_authority(V101.STATE_STRONG, self.cfg)
        self.assertEqual(pa.max_position, 100)
        self.assertTrue(pa.allow_enter_strong)
        self.assertIn("仍需满仓授权", pa.note)


class TestV101FullAuthorization(unittest.TestCase):
    """满仓授权：独立十项检查。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def _auth(self, state, opp=None, portfolio=None, missing=None):
        opp = opp if opp is not None else _FakeOpp(qualified=5)
        portfolio = portfolio if portfolio is not None else V101.check_portfolio_risk(
            opp.qualified, 100, self.cfg)
        return V101.full_position_authorization(
            state,
            _Fake.module(True), _Fake.module(True), _Fake.module(True),
            _Fake.module(True, missing=missing),
            opp, portfolio, self.cfg)

    def test_requires_s6(self):
        """测试4/测试1：非 S6 一律不得授权满仓"""
        for st in [V101.STATE_NORMAL, V101.STATE_DRAWDOWN, V101.STATE_PANIC,
                   V101.STATE_EASING, V101.STATE_REPAIR]:
            fa = self._auth(st)
            self.assertEqual(fa.result, V101.FULL_DENIED,
                             f"{st} 不得授权满仓")
            self.assertFalse(fa.checks["市场状态为S6"])

    def test_all_conditions_pass_allows(self):
        """测试5：全部条件过才 ALLOWED"""
        fa = self._auth(V101.STATE_STRONG)
        self.assertEqual(fa.result, V101.FULL_ALLOWED)
        self.assertTrue(all(fa.checks.values()))
        # ★ V10.2：第 6 项（合格个股数量）、第 7 项（个股买点质量）已移出授权，
        #   止损距离的合理性改由组合风险预算把关 → 检查项由 11 减为 8，
        #   且剩余 8 项全部是市场环境层面（不含任何选股数据依赖）
        self.assertEqual(len(fa.checks), 8, "必须检查八项环境条件")
        self.assertNotIn("合格个股数量充足", fa.checks)
        self.assertNotIn("个股止损距离合规", fa.checks)
        for key in fa.checks:
            self.assertNotIn("个股", key)
            self.assertNotIn("合格", key)

    def test_missing_data_not_allowed(self):
        """测试：数据缺失不得默认通过"""
        fa = self._auth(V101.STATE_STRONG, missing=["主线数据缺失"])
        self.assertNotEqual(fa.result, V101.FULL_ALLOWED,
                            "数据缺失时不得授权满仓")
        self.assertIn(fa.result, (V101.FULL_REVIEW, V101.FULL_DENIED))

    def test_opportunity_count_no_longer_gates_authorization(self):
        """★ V10.2：合格个股数量不再否决满仓授权（属执行层面，非环境层面）。"""
        fa = self._auth(V101.STATE_STRONG, opp=_FakeOpp(qualified=1))
        self.assertEqual(fa.result, V101.FULL_ALLOWED)
        self.assertIn("不参与满仓授权判定", fa.info)

    def test_portfolio_risk_overflow_denied(self):
        """测试7：组合风险超限不得授权满仓

        ★ V10.2 语义变更：组合风险改用**代理口径**（ETF 波动率代理的典型止损距离），
        不再依赖候选股的止损价 —— 否则授权结果会随「今天有几只票」变化。
        """
        # 构造超限：代理止损距离极大
        opp = _FakeOpp(qualified=5, stop_distance=0.5, risk_reward=1.0)
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg)
        # 手工把代理止损距离改成极大值，模拟市场波动率极高
        pr.proxy_stop_distance = 0.9
        pr.used_proxy = True
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        self.assertEqual(fa.result, V101.FULL_DENIED)
        self.assertFalse(fa.checks["组合风险检查通过"])

    def test_allowed_does_not_mean_auto_buy(self):
        """满仓授权通过 ≠ 自动满仓：仍要计算实际建议仓位"""
        opp = _FakeOpp(qualified=5)
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg)
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        self.assertEqual(fa.result, V101.FULL_ALLOWED)
        # 授权通过，但目标仓位仍受机会数与组合风险约束
        auth = V101.position_authority(V101.STATE_STRONG, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_STRONG, auth, opp, pr, fa, None, self.cfg)
        self.assertLessEqual(tp.suggested, 100)
        self.assertIsNone(tp.add_room, "未提供实际仓位时加仓空间应为 UNKNOWN")


class TestV101TargetPosition(unittest.TestCase):
    """实际建议仓位：不得超过权限；机会不足不强行买入。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def test_never_exceeds_authority(self):
        """验收标准5：实际仓位不能超过市场仓位权限"""
        for st in [V101.STATE_NORMAL, V101.STATE_DRAWDOWN, V101.STATE_PANIC,
                   V101.STATE_EASING, V101.STATE_REPAIR, V101.STATE_STRONG]:
            auth = V101.position_authority(st, self.cfg)
            opp = _FakeOpp(qualified=10)
            pr = V101.check_portfolio_risk(opp.qualified, auth.max_position, self.cfg)
            fa = V101.full_position_authorization(
                st, _Fake.module(True), _Fake.module(True), _Fake.module(True),
                _Fake.module(True), opp, pr, self.cfg)
            tp = V101.calculate_target_position(st, auth, opp, pr, fa, None, self.cfg)
            self.assertLessEqual(tp.suggested, auth.max_position,
                                 f"{st} 实际仓位不得超过权限")

    def test_no_opportunity_does_not_zero_position(self):
        """★ V10.2 语义变更：没有合格买点时，环境仓位建议**不再被压到 0**。

        选股数据已移出仓位计算 —— 环境模块回答「这市场该拿多少仓」，
        选股模块回答「买哪几只」，两者不再互相绑架。
        （旧行为：stock_picks.db 只覆盖 20260601 起 → 1~5 月 132 天建议仓位恒为 0）
        """
        auth = V101.position_authority(V101.STATE_STRONG, self.cfg)
        opp = _FakeOpp(qualified=0)
        pr = V101.check_portfolio_risk([], 100, self.cfg)
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_STRONG, auth, opp, pr, fa, None, self.cfg)
        self.assertGreater(tp.suggested, 0,
                           "无合格买点不应把环境仓位建议压到 0")
        self.assertLessEqual(tp.suggested, auth.max_position)
        self.assertTrue(any("无合格买入信号" in x for x in tp.limited_by),
                        "仍需在限制原因中提示无合格信号")

    def test_opportunity_count_not_in_limited_by(self):
        """★ V10.2：选股数量不再作为仓位上限的调节因子。"""
        auth = V101.position_authority(V101.STATE_NORMAL, self.cfg)
        opp5 = _FakeOpp(qualified=5)
        opp1 = _FakeOpp(qualified=1)
        pr5 = V101.check_portfolio_risk(opp5.qualified, 60, self.cfg)
        pr1 = V101.check_portfolio_risk(opp1.qualified, 60, self.cfg)
        fa5 = V101.full_position_authorization(
            V101.STATE_NORMAL, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp5, pr5, self.cfg)
        fa1 = V101.full_position_authorization(
            V101.STATE_NORMAL, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp1, pr1, self.cfg)
        t5 = V101.calculate_target_position(
            V101.STATE_NORMAL, auth, opp5, pr5, fa5, None, self.cfg)
        t1 = V101.calculate_target_position(
            V101.STATE_NORMAL, auth, opp1, pr1, fa1, None, self.cfg)
        self.assertEqual(t5.suggested, t1.suggested,
                         "选股数量（5 vs 1）不应改变环境仓位建议")

    def test_add_room_unknown_without_current(self):
        """未提供实际仓位时加仓空间为 UNKNOWN（不是 0）"""
        auth = V101.position_authority(V101.STATE_NORMAL, self.cfg)
        opp = _FakeOpp(qualified=5)
        pr = V101.check_portfolio_risk(opp.qualified, 60, self.cfg)
        fa = V101.full_position_authorization(
            V101.STATE_NORMAL, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_NORMAL, auth, opp, pr, fa, None, self.cfg)
        self.assertIsNone(tp.add_room)


class TestV101Ladder(unittest.TestCase):
    """分级加仓：不允许一次性跳到 100%。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def test_ladder_steps_configured(self):
        steps = self.cfg["ladder"]["steps"]
        self.assertEqual(steps, [20, 40, 60, 80, 100], "阶梯必须配置化")

    def test_single_step_capped(self):
        """单次加仓幅度不得超过上限"""
        auth = V101.position_authority(V101.STATE_STRONG, self.cfg)
        opp = _FakeOpp(qualified=10)
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg)
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_STRONG, auth, opp, pr, fa, 0, self.cfg)
        lad = V101.plan_ladder_add(0, tp, V101.STATE_STRONG, fa, pr, opp, self.cfg)
        self.assertIsNotNone(lad.next_step)
        self.assertLessEqual(lad.next_step, self.cfg["ladder"]["max_single_step_pct"])

    def test_no_add_without_current_position(self):
        """未提供实际仓位时不得给出加仓建议"""
        auth = V101.position_authority(V101.STATE_STRONG, self.cfg)
        opp = _FakeOpp(qualified=10)
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg)
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_STRONG, auth, opp, pr, fa, None, self.cfg)
        lad = V101.plan_ladder_add(None, tp, V101.STATE_STRONG, fa, pr, opp, self.cfg)
        self.assertFalse(lad.can_add)


class TestV101ExitCheck(unittest.TestCase):
    """满仓退出检查：市场转弱时收回满仓权限。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def test_no_trigger_holds(self):
        r = V101.full_position_exit_check(
            state=V101.STATE_STRONG, prev_state=V101.STATE_STRONG,
            breadth_ratio=0.6, prev_breadth_ratio=0.62,
            dt_count=20, prev_dt_count=20,
            broken_rate=20.0, prev_broken_rate=20.0,
            mainline_ok=True, portfolio_ok=True, cfg=self.cfg)
        self.assertEqual(r.result, V101.EXIT_HOLD)
        self.assertEqual(r.triggered, [])

    def test_state_downgrade_reduces(self):
        """测试8：状态由 S6 退回 → 收回满仓权限"""
        r = V101.full_position_exit_check(
            state=V101.STATE_REPAIR, prev_state=V101.STATE_STRONG,
            breadth_ratio=0.5, prev_breadth_ratio=0.6,
            dt_count=30, prev_dt_count=25,
            broken_rate=25.0, prev_broken_rate=20.0,
            mainline_ok=True, portfolio_ok=True, cfg=self.cfg)
        self.assertEqual(r.result, V101.EXIT_REDUCE)
        self.assertTrue(any("退回" in t for t in r.triggered))

    def test_breadth_drop_reduces(self):
        """广度持续恶化 → 降低仓位"""
        r = V101.full_position_exit_check(
            state=V101.STATE_STRONG, prev_state=V101.STATE_STRONG,
            breadth_ratio=0.40, prev_breadth_ratio=0.60,
            dt_count=20, prev_dt_count=20,
            broken_rate=20.0, prev_broken_rate=20.0,
            mainline_ok=True, portfolio_ok=True, cfg=self.cfg)
        self.assertEqual(r.result, V101.EXIT_REDUCE)
        self.assertTrue(any("广度下降" in t for t in r.triggered))

    def test_dt_surge_reduces(self):
        """跌停显著增加 → 降低仓位"""
        r = V101.full_position_exit_check(
            state=V101.STATE_STRONG, prev_state=V101.STATE_STRONG,
            breadth_ratio=0.6, prev_breadth_ratio=0.6,
            dt_count=60, prev_dt_count=30,
            broken_rate=20.0, prev_broken_rate=20.0,
            mainline_ok=True, portfolio_ok=True, cfg=self.cfg)
        self.assertEqual(r.result, V101.EXIT_REDUCE)
        self.assertTrue(any("跌停" in t for t in r.triggered))

    def test_core_holdings_risk_triggers_invalid(self):
        """多只核心持仓同时失效 → 处理失效持仓"""
        r = V101.full_position_exit_check(
            state=V101.STATE_STRONG, prev_state=V101.STATE_STRONG,
            breadth_ratio=0.6, prev_breadth_ratio=0.6,
            dt_count=20, prev_dt_count=20,
            broken_rate=20.0, prev_broken_rate=20.0,
            mainline_ok=True, core_holdings_risk_count=5,
            portfolio_ok=True, cfg=self.cfg)
        self.assertEqual(r.result, V101.EXIT_INVALID)

    def test_data_missing_holds_but_stops_adding(self):
        """数据缺失 → 保持但停止加仓，不盲动"""
        r = V101.full_position_exit_check(
            state=V101.STATE_STRONG, prev_state=V101.STATE_STRONG,
            breadth_ratio=None, prev_breadth_ratio=None,
            dt_count=None, prev_dt_count=None,
            broken_rate=None, prev_broken_rate=None,
            mainline_ok=False, data_missing=True, cfg=self.cfg)
        self.assertEqual(r.result, V101.EXIT_HOLD)
        self.assertIn("停止加仓", r.suggestion)


class TestV101PortfolioRisk(unittest.TestCase):
    """组合风险预算。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def test_concentration_detected(self):
        """测试7：持仓集中在同一主题 → 违规"""
        opp = _FakeOpp(qualified=6,
                       industries=["同一行业"] * 6,
                       concepts=["同一主题"] * 6)
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg)
        self.assertFalse(pr.ok, "高度集中必须判定违规")
        self.assertGreater(pr.max_theme_pct, self.cfg["portfolio_risk"]["max_single_theme_pct"])

    def test_cash_floor_enforced(self):
        """现金最低保留比例必须生效。

        ★ 注意语义：现金约束**不否决组合风险本身**（否则满仓永远不可能通过），
          而是体现在「实际建议仓位上限」上 —— 90% 上限。
        """
        opp = _FakeOpp(qualified=5)
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg)
        self.assertFalse(pr.cash_floor_ok, "仓位 100% 违反现金最低保留")
        self.assertEqual(pr.cash_floor_max, 100 - self.cfg["portfolio_risk"]["min_cash_pct"])
        self.assertTrue(any("现金最低保留" in v for v in pr.violations))

    def test_cash_floor_caps_target_position(self):
        """现金约束必须真实压低实际建议仓位"""
        opp = _FakeOpp(qualified=8)
        auth = V101.position_authority(V101.STATE_STRONG, self.cfg)
        # 传入 state 与 authority，使风险预算按 S6 档位计算
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg,
                                       state=V101.STATE_STRONG, authority=auth)
        # 构造「满仓授权通过」的情形，让仓位只被现金约束压制
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_STRONG, auth, opp, pr, fa, None, self.cfg)
        self.assertLessEqual(tp.suggested, pr.cash_floor_max,
                             "实际建议仓位不得超过现金约束上限")
        self.assertTrue(any("现金最低保留" in x for x in tp.limited_by),
                        f"应说明现金约束，limited_by={tp.limited_by}")

    def test_risk_budget_respected(self):
        """组合风险超预算 → 目标仓位按比例缩减

        ★ V10.2：改用代理口径构造超限（与候选股无关）。
        """
        opp = _FakeOpp(qualified=5, stop_distance=0.5, risk_reward=1.0)
        pr = V101.check_portfolio_risk(opp.qualified, 100, self.cfg)
        pr.proxy_stop_distance = 0.9      # 代理止损极大 → 风险敞口必然超预算
        pr.used_proxy = True
        auth = V101.position_authority(V101.STATE_STRONG, self.cfg)
        fa = V101.full_position_authorization(
            V101.STATE_STRONG, _Fake.module(True), _Fake.module(True),
            _Fake.module(True), _Fake.module(True), opp, pr, self.cfg)
        tp = V101.calculate_target_position(
            V101.STATE_STRONG, auth, opp, pr, fa, None, self.cfg)
        self.assertLess(tp.suggested, 100, "组合风险超限时不得满仓")
        self.assertTrue(any("风险" in x for x in tp.limited_by),
                        f"应说明风险约束，limited_by={tp.limited_by}")


class TestV101RealData(unittest.TestCase):
    """真实历史数据回放。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V101.load_config()

    def test_panic_day_real(self):
        """测试1：20261030 真实数据 —— 普跌、小盘弱、广度极差、不确认底部、不授权满仓"""
        r = V101.analyze_trade_date_v101(TD_PANIC, self.cfg, None)
        self.assertEqual(r.state, V101.STATE_PANIC,
                         f"10/30 应为恐慌加速，实际 {r.state}")
        # 小盘显著弱于大盘
        gap = r.snapshot.small_vs_large_gap
        self.assertIsNotNone(gap)
        self.assertLess(gap, -1.0, f"小盘应显著弱于大盘，实际差 {gap:.2f}pp")
        # 广度极差
        self.assertLess(r.snapshot.breadth.breadth_ratio, 0.25,
                        "广度应极差")
        # 不授权满仓
        self.assertNotEqual(r.authorization.result, V101.FULL_ALLOWED)
        # 不因极端下跌而提高仓位
        self.assertLessEqual(r.target.suggested, 5,
                             "恐慌加速的仓位建议不得超过 5%")

    def test_report_has_all_sections(self):
        """报告必须包含四个新段落"""
        r = V101.analyze_trade_date_v101(TD_PANIC, self.cfg, None)
        txt = V101.render_v101_report(r, self.cfg)
        for sec in ["【市场状态】", "【仓位权限】", "【仓位计算】", "【操作建议】"]:
            self.assertIn(sec, txt, f"报告缺少 {sec}")

    def test_state_machine_six_exists(self):
        """S6 必须已注册到状态表"""
        self.assertIn(V101.STATE_STRONG, V101.STATE_CN)
        self.assertEqual(V101.STATE_CN[V101.STATE_STRONG], "强势主升")


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestV104DirectionCriteria(unittest.TestCase):
    """★ V10.4：S6 四模块改为「方向/相对口径」的回归测试。

    起因（2026 全年 198 个交易日 × 3 个事实标签 × 前后半段分割验证）：
      · 指数趋势旧口径（4 个绝对门槛）在「未来20日基准>3%」标签下
        事后强市率 **0.0%**（51 样本无一命中）
      · 「晋级率 ≥25%」作为必要条件 = 「必须市场情绪高潮才敢满仓」，
        实测晋级率越高未来越差（情绪高潮即短期顶部）
      · 「5日均广度 ≥ 20日均广度」（改善方向）是全部候选条件中
        边际价值最高的一个
    """

    def setUp(self):
        self.cfg = V101.load_config()

    def test_index_trend_uses_direction_not_absolute(self):
        """指数趋势必须用方向条件，不该再依赖绝对水位门槛"""
        import inspect
        src = inspect.getsource(V101.check_index_trend)
        self.assertIn("ratio20 >= ratio60", src,
                      "必须包含「站上MA20占比 ≥ 站上MA60占比」的方向判定")
        self.assertIn("ratio5 >= ratio20d", src,
                      "必须包含「近5日正占比 ≥ 近20日正占比」的方向判定")
        # 旧的绝对门槛不应再作为必要条件
        self.assertNotIn('c1 = ratio20 >= it["required_above_ma20_ratio"]', src,
                         "绝对水位门槛不应再参与判定")

    def test_breadth_uses_improvement_direction(self):
        """市场广度必须包含改善方向判定"""
        import inspect
        src = inspect.getsource(V101.check_breadth)
        self.assertIn("ma5 >= ma20", src,
                      "必须包含「5日均 ≥ 20日均」的改善方向判定")

    def test_promotion_rate_is_reverse_gate(self):
        """★ 晋级率必须是反向闸门（过热=顶部），不是正向门槛"""
        m = self.cfg["strong_uptrend"]["money_effect"]
        self.assertIn("promotion_rate_max", m,
                      "必须配置晋级率上限（反向闸门）")
        self.assertIsNotNone(m["promotion_rate_max"])
        import inspect
        src = inspect.getsource(V101.check_money_effect)
        self.assertIn("promotion_rate_max", src,
                      "赚钱效应模块必须使用晋级率上限做反向闸门")
        self.assertNotIn('c4 = lim.promotion_rate is None or lim.promotion_rate >= m["promotion_rate_min"]',
                         src, "晋级率不应再作为正向门槛")

    def test_six_state_reachable_in_semiconductor_rally(self):
        """★ 关键回归：半导体主升段必须能进入 S6。

        2026-05-13 是这波行情的核心日：电子行业连续强态 22 日、
        超额20日 25%、广度 60.1%、涨停 112 家 → 四模块应全过 → S6。
        """
        import os
        import market_regime_v101 as V
        r = V.analyze_trade_date_v101("20260513", self.cfg, None, persist=False)
        self.assertTrue(r.strong.is_strong,
                        f"20260513 四模块应全过："
                        f"{ {n: m.passed for n, m in r.strong.modules.items()} }")
        self.assertEqual(r.state, V.STATE_STRONG,
                         "20260513 应进入 S6 强势主升")
        self.assertEqual(r.authority.max_position, 100,
                         "S6 仓位权限应为 100%")

    def test_six_days_mainline_outperformance(self):
        """★ S6 日必须真的选中了跑赢的行业。

        2026 全年 9 个 S6 日，其主线行业在未来 20 日相对等权基准
        超额均值 +10.3%、胜率 100%。
        """
        import etf_mainline as em
        panel = em.load_etf_panel()
        if panel is None or panel.empty:
            self.skipTest("ETF 缓存不可用")
        ecfg = em.load_config()
        series = em.compute_industry_series(panel, ecfg)
        idx_all = sorted(panel["trade_date"].unique())
        pos = {d: i for i, d in enumerate(idx_all)}
        nav = {i: s.set_index("trade_date")["nav"].reindex(idx_all).values
               for i, s in series.items()}
        bench = __import__("numpy").mean(list(nav.values()), axis=0)

        # 复用回放结果里的 S6 日期
        import csv
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_bt_out", "position_2026.csv")
        if not os.path.exists(path):
            self.skipTest("回放结果不存在")
        s6_days = []
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("state") == "S6":
                    s6_days.append(row["trade_date"])
        if not s6_days:
            self.skipTest("无 S6 样本")

        wins = 0; total = 0
        for d in s6_days:
            r = em.detect_mainlines(d, ecfg, panel)
            inds = r.get("industries") or []
            i = pos.get(d)
            if not inds or i is None or i + 20 >= len(bench):
                continue
            bw = (bench[i + 20] / bench[i] - 1) * 100
            for ind in inds[:2]:
                iv = nav[ind]
                iw = (iv[i + 20] / iv[i] - 1) * 100
                total += 1
                if iw - bw > 0:
                    wins += 1
        if total == 0:
            self.skipTest("无可评估样本")
        self.assertGreaterEqual(100 * wins / total, 80,
                                f"S6 主线行业相对基准胜率应 ≥80%，实得 {100*wins/total:.0f}%")
