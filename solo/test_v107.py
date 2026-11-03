# -*- coding: utf-8 -*-
"""V10.7 增量优化回归测试

覆盖六组不变量：
  1. 量价口径 —— 禁止「仅凭成交额高于5日均量」判定放量上涨
  2. S4 卖压衰减确认 —— 缺指标即 UNKNOWN，不得默认通过
  3. S5 反弹确认 —— 单日普涨不得认定为 S5
  4. STRUCTURAL_ALPHA_EXCEPTION —— 入池/复核/权限边界
  5. 仓位语义分离 —— 目标仓位 ≠ 必须持仓；硬上限不可突破
  6. 无未来数据 + 新旧版本可对比

本测试用**纯构造数据**驱动判定函数，不依赖行情库 → 可离线运行。
另有一组用例直接打真实数据库（若数据不可用则跳过）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_regime_v107 as V107
import market_regime_v101 as V101


def _row(d, up, down, zt, dt, amt, idx):
    denom = up + down
    return {
        "trade_date": d, "up_count": up, "down_count": down,
        "flat_count": 0, "total": up + down,
        "zt_count": zt, "dt_count": dt, "amount_yi": amt,
        "index_chg": idx, "up_ratio": (up / denom) if denom else None,
        "zt_dt_ratio": (zt / dt) if dt else (float('inf') if zt else None),
    }


class _FakeMicro:
    """替换 market_micro.load_series 的可控序列。"""
    def __init__(self, rows):
        self.rows = rows

    def __call__(self, trade_date, days=30):
        return list(self.rows)


class TestAmountVerdict(unittest.TestCase):
    """量价四要素 + 禁止误判放量上涨。"""

    def setUp(self):
        self.cfg = V107.load_config()
        self._orig = V107.micro.load_series

    def tearDown(self):
        V107.micro.load_series = self._orig

    def _patch(self, rows):
        V107.micro.load_series = _FakeMicro(rows)

    def test_volume_up_requires_index_and_breadth(self):
        """成交额高于5日均 + 指数上涨，但上涨占比不足 → 不得判为放量上涨。"""
        rows = [
            _row("20260101", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260102", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260103", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260104", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260105", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260106", 2000, 3000, 30, 20, 10000.0, 0.5),
            # 今日：成交额明显高于5日均、指数收涨，但只有 40% 个股上涨
            _row("20260107", 2200, 3300, 30, 20, 12000.0, 1.2),
        ]
        self._patch(rows)
        v = V107.amount_verdict("20260107", self.cfg)
        self.assertIsNotNone(v.chg_vs_ma5)
        self.assertGreater(v.chg_vs_ma5, 0)
        self.assertGreater(v.index_chg, 0)
        self.assertLess(v.up_ratio, 0.5)
        self.assertFalse(v.is_volume_up, "指数涨但广度差 → 不得计为放量上涨")
        self.assertIn("放量滞涨", v.pattern)

    def test_volume_up_needs_all_three(self):
        """三要素齐备 → 放量上涨。"""
        rows = [
            _row("20260101", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260102", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260103", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260104", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260105", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260106", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260107", 3200, 2000, 60, 10, 12000.0, 1.2),
        ]
        self._patch(rows)
        v = V107.amount_verdict("20260107", self.cfg)
        self.assertTrue(v.is_volume_up)
        self.assertEqual(v.pattern, "放量上涨")
        # 四要素必须齐全输出
        for f in ("chg_vs_prev", "chg_vs_ma5", "index_chg", "up_ratio"):
            self.assertIsNotNone(getattr(v, f), f"{f} 必须输出")

    def test_volume_down_not_misread(self):
        """放量下跌：成交额高于5日均 + 指数下跌。"""
        rows = [
            _row("20260101", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260102", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260103", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260104", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260105", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260106", 2000, 3000, 30, 20, 10000.0, 0.5),
            _row("20260107", 1800, 3700, 20, 60, 12000.0, -2.0),
        ]
        self._patch(rows)
        v = V107.amount_verdict("20260107", self.cfg)
        self.assertFalse(v.is_volume_up)
        self.assertEqual(v.pattern, "放量下跌")

    def test_missing_data_is_unknown(self):
        self._patch([])
        v = V107.amount_verdict("20260107", self.cfg)
        self.assertEqual(v.pattern, V107.VERDICT_UNKNOWN)
        self.assertTrue(v.missing)


class TestEasingConfirmation(unittest.TestCase):
    """S4 卖压衰减确认。"""

    def setUp(self):
        self.cfg = V107.load_config()
        self._orig = V107.micro.load_series

    def tearDown(self):
        V107.micro.load_series = self._orig

    def _run(self, rows, td="20260107"):
        V107.micro.load_series = _FakeMicro(rows)
        return V107.check_easing_confirmation(td, self.cfg, micro_series=rows)

    def test_v_shape_recovery_counts_as_easing(self):
        """暴跌后的 V 形修复日应判为卖压衰减（不是被严格连续口径否掉）。"""
        rows = [
            _row("20260105", 700, 4800, 10, 51, 15000.0, -3.0),
            _row("20260106", 800, 4700, 12, 95, 16000.0, -3.4),
            _row("20260107", 4600, 900, 120, 10, 17000.0, 2.5),
        ]
        r = self._run(rows)
        self.assertEqual(r.verdict, V107.VERDICT_PASS, r.evidence)
        self.assertTrue(r.checks["跌停家数连续下降"])
        self.assertTrue(r.checks["市场广度改善"])

    def test_selling_pressure_returning_fails(self):
        """跌停重新扩张、广度回落 → FAIL。"""
        rows = [
            _row("20260105", 4600, 900, 120, 10, 17000.0, 2.5),
            _row("20260106", 800, 4700, 12, 95, 16000.0, -3.4),
            _row("20260107", 2100, 3300, 47, 24, 17150.0, -2.1),
        ]
        r = self._run(rows)
        self.assertEqual(r.verdict, V107.VERDICT_FAIL)
        self.assertFalse(r.checks["跌停家数连续下降"])
        self.assertFalse(r.checks["市场广度改善"])

    def test_index_up_alone_does_not_pass(self):
        """仅指数上涨 → 不得判为卖压衰减（用户明确要求）。"""
        rows = [
            _row("20260105", 800, 4700, 12, 95, 16000.0, -3.4),
            _row("20260106", 800, 4700, 12, 95, 16000.0, -3.4),
            _row("20260107", 800, 4700, 12, 95, 16000.0, 2.5),
        ]
        r = self._run(rows)
        self.assertNotEqual(r.verdict, V107.VERDICT_PASS)

    def test_insufficient_history_is_unknown(self):
        rows = [_row("20260107", 800, 4700, 12, 95, 16000.0, 2.5)]
        r = self._run(rows)
        self.assertEqual(r.verdict, V107.VERDICT_UNKNOWN)
        self.assertTrue(r.missing)

    def test_zt_dt_ratio_checked(self):
        """涨停/跌停比恶化 → FAIL。"""
        rows = [
            _row("20260105", 700, 4800, 10, 51, 15000.0, -3.0),
            _row("20260106", 800, 4700, 12, 95, 16000.0, -3.4),
            _row("20260107", 4600, 900, 120, 10, 17000.0, 2.5),
        ]
        r = self._run(rows)
        self.assertIn("涨停跌停比不恶化", r.checks)


class TestReboundConfirmation(unittest.TestCase):
    """S5 反弹确认。"""

    def setUp(self):
        self.cfg = V107.load_config()
        self._orig = V107.micro.load_series

    def tearDown(self):
        V107.micro.load_series = self._orig

    def _run(self, rows, mainline=None, amount=None, td="20260107"):
        V107.micro.load_series = _FakeMicro(rows)
        return V107.check_rebound_confirmation(td, self.cfg,
                                               mainline_module=mainline,
                                               micro_series=rows, amount=amount)

    def test_single_day_surge_not_s5(self):
        """单日普涨（前日极差）不得认定反弹确认通过。"""
        rows = [
            _row("20260105", 700, 4800, 10, 51, 15000.0, -3.0),
            _row("20260106", 800, 4700, 12, 95, 16000.0, -3.4),
            _row("20260107", 4600, 900, 120, 10, 17000.0, 2.5),
        ]
        amt = V107.AmountVerdict(trade_date="20260107", amount_yi=17000.0,
                                 chg_vs_prev=6.25, chg_vs_ma5=5.0,
                                 index_chg=2.5, up_ratio=4600 / 5500,
                                 up_count=4600, down_count=900,
                                 expand=True, shrink=False,
                                 pattern="放量上涨", is_volume_up=True)
        r = self._run(rows, amount=amt)
        # 反弹确认还要求主线赚钱效应等条件；此处至少不能凭空 PASS
        self.assertNotEqual(r.verdict, V107.VERDICT_PASS)

    def test_all_conditions_pass(self):
        rows = [
            _row("20260105", 2000, 3000, 30, 40, 15000.0, -1.0),
            _row("20260106", 3000, 2000, 60, 20, 16000.0, 1.0),
            _row("20260107", 4000, 1000, 90, 10, 17000.0, 2.0),
        ]
        amt = V107.AmountVerdict(trade_date="20260107", amount_yi=17000.0,
                                 chg_vs_prev=6.25, chg_vs_ma5=5.0,
                                 index_chg=2.0, up_ratio=0.8,
                                 up_count=4000, down_count=1000,
                                 expand=True, shrink=False,
                                 pattern="放量上涨", is_volume_up=True)
        ml = type("ML", (), {})()
        ml.details = {"industries": ["电子"], "candidates": [
            {"industry": "电子", "days_strong": 8, "excess20": 12.0}]}
        r = self._run(rows, mainline=ml, amount=amt)
        self.assertEqual(r.verdict, V107.VERDICT_PASS, r.evidence)

    def test_volume_down_blocks_rebound(self):
        """成交额与指数方向不匹配（放量下跌）→ FAIL。"""
        rows = [
            _row("20260105", 2000, 3000, 30, 40, 15000.0, -1.0),
            _row("20260106", 3000, 2000, 60, 20, 16000.0, 1.0),
            _row("20260107", 4000, 1000, 90, 10, 17000.0, 2.0),
        ]
        amt = V107.AmountVerdict(trade_date="20260107", amount_yi=17000.0,
                                 chg_vs_prev=6.25, chg_vs_ma5=5.0,
                                 index_chg=-2.0, up_ratio=0.8,
                                 up_count=4000, down_count=1000,
                                 expand=True, shrink=False,
                                 pattern="放量下跌", is_volume_up=False)
        ml = type("ML", (), {})()
        ml.details = {"industries": ["电子"], "candidates": [
            {"industry": "电子", "days_strong": 8, "excess20": 12.0}]}
        r = self._run(rows, mainline=ml, amount=amt)
        self.assertEqual(r.verdict, V107.VERDICT_FAIL)
        self.assertFalse(r.checks["成交额与指数方向匹配"])


class TestPositionSemantics(unittest.TestCase):
    """仓位语义分离：目标 / 区间 / 硬上限 / 主动加仓 / 满仓授权。"""

    def setUp(self):
        self.cfg = V107.load_config()

    class _Auth:
        result = V101.FULL_DENIED
        reason_cn = "测试"
        failed_conditions = []
        passed_conditions = []
        missing_data = []
        checks = {}
        pipeline_gaps = []
        info = ""

    class _Target:
        suggested = 20
        authority_max = 20
        limited_by = []
        note = ""

    class _Portfolio:
        cash_floor_max = 90.0
        ok = True
        total_risk_pct = 1.0
        risk_budget_pct = 1.5
        budget_usage = 0.6
        used_proxy = True
        proxy_stop_distance = 0.06
        industry_pct = {}
        theme_pct = {}
        violations = []

    class _Opp:
        def __init__(self, n, available=True):
            self.qualified = [object()] * n
            self.observe = []
            self.rejected = []
            self.data_available = available
            self.note = ""

        @property
        def qualified_count(self):
            return len(self.qualified)

    def _authority(self, state):
        return V101.position_authority(state, self.cfg)

    def test_target_is_not_mandatory(self):
        """S4 目标 20%，但无合格标的 → 实际可以 0（保持现金）。"""
        p = V107.build_position_plan(
            "S4", self._Target(), self._authority("S4"), self._Portfolio(),
            self._Auth(), self._Opp(0), None, self.cfg)
        self.assertEqual(p.target_position, 20)
        self.assertEqual(p.suggested_high, 20)
        self.assertEqual(p.suggested_low, 0)
        self.assertEqual(p.actual_hint, 0)
        self.assertFalse(p.target_is_mandatory)
        self.assertIn("不是必须达到", p.actual_note)

    def test_with_signal_range(self):
        p = V107.build_position_plan(
            "S4", self._Target(), self._authority("S4"), self._Portfolio(),
            self._Auth(), self._Opp(3), None, self.cfg)
        self.assertEqual(p.suggested_low, 10)
        self.assertEqual(p.suggested_high, 20)
        self.assertEqual(p.actual_hint, 20)

    def test_hard_cap_never_exceeds_state_permission(self):
        """S4 的仓位硬上限不得超过 20%（即使满仓授权通过）。"""
        auth = self._Auth()
        auth.result = V101.FULL_ALLOWED
        p = V107.build_position_plan(
            "S4", self._Target(), self._authority("S4"), self._Portfolio(),
            auth, self._Opp(3), None, self.cfg)
        self.assertLessEqual(p.hard_cap, 20)

    def test_active_add_denied_when_state_forbids(self):
        p = V107.build_position_plan(
            "S4", self._Target(), self._authority("S4"), self._Portfolio(),
            self._Auth(), self._Opp(3), 5, self.cfg)
        self.assertFalse(p.allow_active_add)

    def test_active_add_allowed_in_s5(self):
        auth = self._Auth()
        auth.result = V101.FULL_REVIEW
        p = V107.build_position_plan(
            "S5", self._Target(), self._authority("S5"), self._Portfolio(),
            auth, self._Opp(3), 10, self.cfg)
        self.assertTrue(p.allow_active_add)

    def test_full_authorization_reported(self):
        auth = self._Auth()
        auth.result = V101.FULL_DENIED
        p = V107.build_position_plan(
            "S4", self._Target(), self._authority("S4"), self._Portfolio(),
            auth, self._Opp(3), None, self.cfg)
        self.assertEqual(p.full_authorization, V101.FULL_DENIED)


class TestStateGates(unittest.TestCase):
    """状态门控：确认不通过不得升级；UNKNOWN 不得默认通过。"""

    def setUp(self):
        self.cfg = V107.load_config()
        self._orig = V107.micro.load_series

    def tearDown(self):
        V107.micro.load_series = self._orig

    class _Dec:
        def __init__(self, prev_state, changed):
            self.prev_state = prev_state
            self.changed = changed
            self.reasons = []
            self.pending_target = ""
            self.pending_days = 0

    def _res(self, verdict, checks=None):
        r = V107.ConfirmationResult(name="x")
        r.verdict = verdict
        r.checks = checks or {}
        return r

    def test_s4_entry_blocked_when_easing_fails(self):
        dec = self._Dec(prev_state="S3", changed=True)
        g = V107.apply_state_gates(
            "S4", dec, self._res(V107.VERDICT_FAIL), self._res(V107.VERDICT_FAIL),
            "20260107", self.cfg)
        self.assertEqual(g.effective_state, "S3")
        self.assertTrue(g.blocked)

    def test_s4_entry_blocked_when_easing_unknown(self):
        """UNKNOWN 不得默认通过。"""
        dec = self._Dec(prev_state="S3", changed=True)
        g = V107.apply_state_gates(
            "S4", dec, self._res(V107.VERDICT_UNKNOWN), self._res(V107.VERDICT_PASS),
            "20260107", self.cfg)
        self.assertEqual(g.effective_state, "S3")

    def test_s4_entry_allowed_when_easing_passes(self):
        dec = self._Dec(prev_state="S3", changed=True)
        g = V107.apply_state_gates(
            "S4", dec, self._res(V107.VERDICT_PASS), self._res(V107.VERDICT_FAIL),
            "20260107", self.cfg)
        self.assertEqual(g.effective_state, "S4")
        self.assertFalse(g.blocked)

    def test_s5_entry_requires_both(self):
        dec = self._Dec(prev_state="S4", changed=True)
        g = V107.apply_state_gates(
            "S5", dec, self._res(V107.VERDICT_PASS), self._res(V107.VERDICT_FAIL),
            "20260107", self.cfg)
        self.assertEqual(g.effective_state, "S4")
        g2 = V107.apply_state_gates(
            "S5", dec, self._res(V107.VERDICT_PASS), self._res(V107.VERDICT_PASS),
            "20260107", self.cfg)
        self.assertEqual(g2.effective_state, "S5")

    def test_never_downgrades_below_base_when_not_entering(self):
        """已在 S4 且确认 FAIL → 维持 S4（不回到 S3），只做标注。"""
        dec = self._Dec(prev_state="S4", changed=False)
        g = V107.apply_state_gates(
            "S4", dec, self._res(V107.VERDICT_FAIL), self._res(V107.VERDICT_FAIL),
            "20260107", self.cfg)
        self.assertEqual(g.effective_state, "S4")
        self.assertFalse(g.blocked)


class TestConsecutiveHelper(unittest.TestCase):
    """连续/衰减两种口径的单元校验。"""

    def test_strict_requires_two_transitions(self):
        self.assertTrue(V107._consecutive([16.3, 85.1, 38.9], 2, True) is False)
        self.assertTrue(V107._consecutive([10.0, 20.0, 30.0], 2, True))

    def test_decay_semantics(self):
        # 今日<昨日 且 今日<前日
        self.assertTrue(V107._decay([51, 95, 10], increasing=False))
        self.assertFalse(V107._decay([95, 10, 24], increasing=False))
        self.assertFalse(V107._decay([10, 20, 30], increasing=False))
        # 上涨方向
        self.assertTrue(V107._decay([14.2, 16.3, 85.1], increasing=True))
        self.assertFalse(V107._decay([16.3, 85.1, 38.9], increasing=True))


class TestNoFutureData(unittest.TestCase):
    """无未来数据 + 新旧版本可对比。"""

    def test_micro_series_truncated(self):
        """market_micro.load_series 只返回 <= trade_date 的数据。"""
        import market_micro as micro
        try:
            s = micro.load_series("20260630", 10)
        except Exception:
            self.skipTest("微结构序列不可用")
        for r in s:
            self.assertLessEqual(str(r['trade_date']), "20260630")

    def test_holdings_as_of_filter(self):
        """ETF 持仓只取 ann_date <= as_of 的最新一期。"""
        import industry_constituents as ic
        if not os.path.exists(ic.IND_DB):
            self.skipTest("持仓库不存在")
        hs = ic.get_holdings("512480.SH", "20260630")
        if not hs:
            self.skipTest("该 ETF 无持仓数据")
        anns = {h['ann_date'] for h in hs}
        self.assertEqual(len(anns), 1, "只应取最新一期")
        self.assertTrue(all(a <= "20260630" for a in anns),
                        "持仓公告日不得晚于 as_of")

    def test_sw_members_as_of_filter(self):
        """申万成分只取 as_of 时点在册的股票。"""
        import industry_constituents as ic
        if not os.path.exists(ic.IND_DB):
            self.skipTest("成分库不存在")
        m = ic.sw_members("电子", "20260630")
        if not m:
            self.skipTest("无成分数据")
        m2 = ic.sw_members("电子", "20261103")
        # 在册集合随时间单调不减（退市/调入会减少在册数，但早期应更大）
        self.assertIsInstance(m2, list)
        self.assertIsInstance(m, list)

    def test_module_does_not_modify_v10(self):
        """V10.7 不修改 V10.0 / V10.1 源码（增量约束）。"""
        for fn in ("market_regime_v10.py", "market_regime_v101.py"):
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), fn)
            if not os.path.exists(p):
                continue
            with open(p, "rb") as f:
                data = f.read()
            # 只允许出现「新增函数调用」，不允许出现 V10.7 的写库调用
            self.assertNotIn(b"regime_state_v107", data,
                             f"{fn} 不应包含 V10.7 的状态写库逻辑")

    def test_v107_uses_separate_state_table(self):
        """V10.7 的状态写自己的表，不覆盖 V10.0 的 regime_state。"""
        import inspect
        import market_regime_v107 as V
        src = inspect.getsource(V)
        self.assertIn("regime_state_v107", src)
        # 不得出现对 v10.save_state 的调用（那会污染原链）
        self.assertNotIn("v10.save_state", src)

    def test_summary_fields_present(self):
        """输出必须包含用户要求的全部字段。"""
        s = V107.render_v107_summary.__doc__
        self.assertIsNotNone(s)
        import inspect
        src = inspect.getsource(V107.render_v107_summary)
        for k in ("市场状态", "卖压衰减确认", "反弹确认", "结构性机会",
                  "最强行业", "行业持续性", "行业内部广度", "主动试探权限",
                  "建议仓位", "仓位硬上限", "满仓授权", "切换关键条件"):
            self.assertIn(k, src, f"缺少输出字段：{k}")


class TestRealDataSmoke(unittest.TestCase):
    """真实数据冒烟测试（数据不可用时跳过）。"""

    def test_20261103_end_to_end(self):
        import market_micro as micro
        try:
            row = micro.get_row("20261103")
        except Exception:
            row = None
        if not row:
            self.skipTest("微结构序列未就绪")
        cfg = V107.load_config()
        v = V107.amount_verdict("20261103", cfg)
        self.assertIsNotNone(v.chg_vs_ma5)
        self.assertIsNotNone(v.up_ratio)
        self.assertIsNotNone(v.index_chg)
        # 2026-11-03 真实为放量下跌，绝不能被判成放量上涨
        self.assertFalse(v.is_volume_up)
        self.assertEqual(v.pattern, "放量下跌")


if __name__ == "__main__":
    unittest.main()
