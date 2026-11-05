# -*- coding: utf-8 -*-
"""V10.7 · P1/P2 仓位优化测试（纯本地，不发起任何网络请求）

运行： python -m pytest test_position_optimizer.py -q
慢测（真实链路）： RUN_SLOW_V107_SMOKE=1 python -m pytest test_position_optimizer.py -q
"""
import os
import copy
import json
import types
import hashlib
import datetime
import unittest

import market_regime_v101 as v101
import market_regime_v107 as v107
import position_optimizer as opt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = v107.load_config()
SD = 0.0306

V10_SHA256 = "1fa7b5780c5ea2c85d7ede676a9686bfa1a3f8713d25d121e1d9c05be9ff166e"
V101_SHA256 = "18e0fab27a441101da9fa87314c3db46b5f62958b6257339a21821c17f4ba0d5"


def _digest(name):
    with open(os.path.join(BASE_DIR, name), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _mk_r(state="S5", target=40, hard_cap=40, easing="PASS", rebound="PASS",
          mainline=True, ladder_next=40, next_checks=None, auth_result=None,
          p0_cap=100, blocked=None):
    """构造最小 V10.7 结果桩（鸭子类型，不跑完整链路）。"""
    auth = v101.position_authority(state, CFG)
    pf = v101.PortfolioRisk(
        total_risk_pct=0.652, risk_budget_pct=3.5, budget_usage=0.19, ok=True,
        used_proxy=True, proxy_stop_distance=SD, cash_floor_max=90.0)
    tgt = v101.TargetPosition(suggested=target, state=state, portfolio=pf)
    checks = {"市场状态维持": True, "满仓授权条件未恶化": True,
              "选股信号可用（提示项）": True, "组合风险仍合格": True,
              "主线未发生变化": True, "目标仓位支持本级": True}
    if next_checks:
        checks.update(next_checks)
    ladder = v101.LadderResult(
        current_step=0, next_step=ladder_next, can_add=True,
        checks=checks, max_single_step_pct=20, reason="原阶梯")
    mainline_obj = (None if mainline is None else
                    types.SimpleNamespace(passed=bool(mainline)))
    base = types.SimpleNamespace(
        ladder=ladder,
        strong=types.SimpleNamespace(modules={"主线持续性": mainline_obj}),
        portfolio=pf, target=tgt)
    plan = types.SimpleNamespace(
        state=state, target_position=target, suggested_low=20,
        suggested_high=target, hard_cap=hard_cap, allow_active_add=False,
        active_add_conditions=[], full_authorization="", actual_hint=target,
        actual_note="", target_is_mandatory=False, cash_floor_note="",
        limits=[])
    return types.SimpleNamespace(
        trade_date="20261103", state=state, state_cn=v107.STATE_CN.get(state, ""),
        authority=auth, target=tgt, portfolio=pf, plan=plan, base=base,
        authorization=types.SimpleNamespace(
            result=auth_result or v101.FULL_DENIED),
        p0=types.SimpleNamespace(risk_cap=p0_cap),
        easing=types.SimpleNamespace(verdict=easing),
        rebound=types.SimpleNamespace(verdict=rebound),
        gate=types.SimpleNamespace(blocked=list(blocked or []), applied=[],
                                   base_state=state, effective_state=state),
        opt=None, ladder_patch=None, current_position=10,
        switch_conditions={}, state_days=1)


def _mk_series(n=120, tail_big=False, future_big=False, last_spike=False,
               trade_date="20261103"):
    """构造指数日涨跌序列；日期为连续字符串，最后一日 = trade_date。"""
    end = datetime.datetime.strptime(trade_date, "%Y%m%d").date()
    rows = []
    for i in range(n):
        d = (end - datetime.timedelta(days=n - 1 - i)).strftime("%Y%m%d")
        amp = 0.3
        if tail_big and i >= n - 20:
            amp = 3.0
        v = amp if i % 2 == 0 else -amp
        rows.append({"trade_date": d, "index_chg": v, "up_ratio": 0.5})
    if last_spike:
        rows[-1]["index_chg"] = 50.0
    if future_big:
        for i in range(1, 31):
            d = (end + datetime.timedelta(days=i)).strftime("%Y%m%d")
            rows.append({"trade_date": d,
                         "index_chg": 30.0 if i % 2 else -30.0,
                         "up_ratio": 0.5})
    return rows


def _cfg_with(**overrides):
    c = copy.deepcopy(CFG)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(c["position_optimizer"].get(k), dict):
            c["position_optimizer"][k].update(v)
        else:
            c["position_optimizer"][k] = v
    return c


# ============================================================
class TestIncrementalContract(unittest.TestCase):
    """增量约束：v10/v101 源码未改、A/B 开关、配置完整。"""

    def test_v10_v101_source_unchanged(self):
        self.assertEqual(_digest("market_regime_v10.py"), V10_SHA256)
        self.assertEqual(_digest("market_regime_v101.py"), V101_SHA256)

    def test_config_has_all_p1p2_sections(self):
        o = json.load(open(os.path.join(BASE_DIR, "config_v107_refine.json"),
                           encoding="utf-8"))
        # 10.7.2 条目必须保留（10.7.3+ 允许在其后继续追加增量版本）
        self.assertIn("10.7.2", [x["version"] for x in o["_version_history"]])
        for k in ("smoother", "confirm_gates", "volatility_budget",
                  "range_ratio", "ladder_patch", "cash_floor"):
            self.assertIn(k, o["position_optimizer"])
        self.assertTrue(o["position_optimizer"]["persist_history_without_state"])

    def test_disabled_is_pure_passthrough(self):
        r = _mk_r()
        c = _cfg_with(enabled=False)
        adj = opt.compute(r, c, history={"trade_date": "20261102", "target": 10},
                          series=_mk_series())
        self.assertFalse(adj.enabled)
        self.assertIsNone(adj.target_override)
        self.assertIsNone(adj.extra_cap)
        self.assertIsNone(adj.lo_ratio)
        self.assertIsNone(adj.ladder_patch)
        self.assertFalse(adj.deny_active_add)
        self.assertEqual(adj.risk_cap, 100)
        self.assertIn("A/B", "".join(adj.audit.notes))

    def test_base_decision_record_untouched(self):
        r = _mk_r()
        before = copy.deepcopy(r.base.ladder)
        opt.compute(r, CFG, history=None, series=_mk_series(),
                    current_position=10)
        self.assertEqual(r.base.ladder.next_step, before.next_step)
        self.assertIs(r.base.ladder.checks["主线未发生变化"], True)
        self.assertEqual(r.base.ladder.reason, "原阶梯")

    def test_summary_contract_extended_not_broken(self):
        r = _mk_r()
        r.amount = r.easing = r.rebound = r.structural = None
        r.opt = opt.OptimizerAudit()
        r.p0 = None
        s = v107.render_v107_summary(r)
        for k in ("市场状态", "卖压衰减确认", "反弹确认", "结构性机会",
                  "最强行业", "行业持续性", "行业内部广度", "主动试探权限",
                  "建议仓位", "仓位硬上限", "满仓授权", "切换关键条件"):
            self.assertIn(k, s)
        self.assertIn("P1P2仓位优化", s)
        txt = v107.render_v107_report(r)
        self.assertIn("【P1/P2 仓位优化】", txt)


# ============================================================
class TestSmoother(unittest.TestCase):
    """P1-③ 升仓限速 / 降仓立即 / 冷却 / 状态升级复用确认。"""

    def test_cold_start_no_smoothing(self):
        sm = opt.smooth_target(80, "S5", None, CFG, "PASS", "PASS", [])
        self.assertEqual(sm["action"], "COLD_START")
        self.assertEqual(sm["smoothed"], 80)

    def test_up_limited_to_10pp(self):
        h = {"trade_date": "20261102", "state": "S5", "target": 30,
             "cooldown_left": 0}
        sm = opt.smooth_target(80, "S5", h, CFG, "PASS", "PASS", [])
        self.assertEqual(sm["action"], "UP_LIMITED")
        self.assertEqual(sm["smoothed"], 40)

    def test_down_is_immediate_and_sets_cooldown(self):
        h = {"trade_date": "20261102", "state": "S1", "target": 80,
             "cooldown_left": 0}
        sm = opt.smooth_target(20, "S3", h, CFG, "FAIL", "FAIL", [])
        self.assertEqual(sm["action"], "DOWN")
        self.assertEqual(sm["smoothed"], 20)
        self.assertEqual(sm["cooldown_left"], 2)

    def test_cooldown_blocks_up(self):
        h = {"trade_date": "20261102", "state": "S4", "target": 20,
             "cooldown_left": 2}
        sm = opt.smooth_target(60, "S5", h, CFG, "PASS", "PASS", [])
        self.assertEqual(sm["action"], "UP_COOLDOWN")
        self.assertEqual(sm["smoothed"], 20)

    def test_state_upgrade_requires_easing_for_s4(self):
        h = {"trade_date": "20261102", "state": "S3", "target": 5,
             "cooldown_left": 0}
        sm = opt.smooth_target(20, "S4", h, CFG, "FAIL", "FAIL", [])
        self.assertEqual(sm["action"], "UP_BLOCKED")
        self.assertEqual(sm["smoothed"], 5)

    def test_state_upgrade_requires_rebound_for_s5(self):
        h = {"trade_date": "20261102", "state": "S4", "target": 20,
             "cooldown_left": 0}
        sm = opt.smooth_target(40, "S5", h, CFG, "PASS", "UNKNOWN", [])
        self.assertEqual(sm["action"], "UP_BLOCKED")

    def test_confirmed_upgrade_allows_20pp(self):
        h = {"trade_date": "20261102", "state": "S4", "target": 20,
             "cooldown_left": 0}
        sm = opt.smooth_target(60, "S5", h, CFG, "PASS", "PASS", [])
        self.assertEqual(sm["step"], 20)
        self.assertEqual(sm["smoothed"], 40)

    def test_gate_block_prevents_up(self):
        h = {"trade_date": "20261102", "state": "S4", "target": 20,
             "cooldown_left": 0}
        sm = opt.smooth_target(60, "S5", h, CFG, "PASS", "PASS",
                               ["S5 升级被否决"])
        self.assertEqual(sm["action"], "UP_BLOCKED")
        self.assertEqual(sm["smoothed"], 20)

    def test_history_from_future_is_ignored(self):
        h = {"trade_date": "20261104", "state": "S5", "target": 90,
             "cooldown_left": 0}
        adj = opt.compute(_mk_r(target=80, hard_cap=90), CFG, history=h,
                          series=_mk_series(), current_position=10)
        self.assertEqual(adj.audit.smoother["action"], "COLD_START")
        self.assertEqual(adj.target_override, 80)

    def test_stale_history_ignored(self):
        h = {"trade_date": "20260101", "state": "S5", "target": 10,
             "cooldown_left": 0}
        adj = opt.compute(_mk_r(target=80), CFG, history=h,
                          series=_mk_series(), current_position=10)
        self.assertEqual(adj.audit.smoother["action"], "COLD_START")

    def test_smoothed_target_never_exceeds_hard_cap(self):
        h = {"trade_date": "20261102", "state": "S5", "target": 20,
             "cooldown_left": 0}
        r = _mk_r(state="S5", target=60, hard_cap=25)
        adj = opt.compute(r, CFG, history=h, series=_mk_series(),
                          current_position=10)
        self.assertLessEqual(adj.target_override, 25)


# ============================================================
class TestConfirmGates(unittest.TestCase):
    """P1-④ 三项确认进入仓位约束。"""

    def test_easing_fail_denies_active_add(self):
        adj = opt.compute(_mk_r(easing="FAIL"), CFG, series=_mk_series())
        self.assertTrue(adj.deny_active_add)
        self.assertTrue(any("卖压衰减" in x for x in adj.deny_reasons))

    def test_easing_unknown_does_not_pass(self):
        adj = opt.compute(_mk_r(easing="UNKNOWN"), CFG, series=_mk_series())
        self.assertTrue(adj.deny_active_add)

    def test_rebound_fail_s5_caps_to_s4(self):
        adj = opt.compute(_mk_r(state="S5", rebound="FAIL"), CFG,
                          series=_mk_series())
        self.assertEqual(adj.extra_cap, 20)
        self.assertTrue(any("S4 权限" in x for x in adj.deny_reasons))

    def test_rebound_unknown_s5_caps_to_s4(self):
        adj = opt.compute(_mk_r(state="S5", rebound="UNKNOWN"), CFG,
                          series=_mk_series())
        self.assertEqual(adj.extra_cap, 20)

    def test_rebound_fail_s4_no_extra_cap(self):
        adj = opt.compute(_mk_r(state="S4", rebound="FAIL"), CFG,
                          series=_mk_series())
        self.assertIsNone(adj.extra_cap)

    def test_unknown_waiver_config(self):
        c = _cfg_with(confirm_gates={"unknown_does_not_pass": False})
        adj = opt.compute(_mk_r(state="S5", easing="UNKNOWN",
                                rebound="UNKNOWN"), c, series=_mk_series())
        self.assertFalse(adj.deny_active_add)
        self.assertIsNone(adj.extra_cap)

    def test_extra_cap_reaches_real_plan(self):
        r = _mk_r(state="S5", target=40, hard_cap=40, rebound="FAIL")
        adj = opt.compute(r, CFG, series=_mk_series())
        plan = v107.build_position_plan(
            r.state, r.target, r.authority, r.portfolio, r.authorization,
            types.SimpleNamespace(qualified_count=3, data_available=True),
            10, CFG, risk_cap=adj.risk_cap, extra_cap=adj.extra_cap,
            lo_ratio=adj.lo_ratio)
        self.assertEqual(plan.hard_cap, 20)


# ============================================================
class TestVolatilityBudget(unittest.TestCase):
    """P2 独立波动率预算：只收紧不放宽、无未来数据、样本不足 UNKNOWN。"""

    def test_normal_vol_no_tighten(self):
        v = opt.volatility_factor("20261103", CFG, series=_mk_series())
        self.assertEqual(v["status"], "OK")
        self.assertEqual(v["factor"], 1.0)

    def test_high_vol_tightens_budget(self):
        v = opt.volatility_factor("20261103", CFG,
                                  series=_mk_series(tail_big=True))
        self.assertEqual(v["status"], "TIGHTEN")
        self.assertLess(v["factor"], 1.0)
        self.assertEqual(v["factor"], 0.5)

    def test_factor_never_above_one(self):
        v = opt.volatility_factor("20261103", CFG,
                                  series=_mk_series(n=120, tail_big=False))
        self.assertLessEqual(v["factor"], 1.0)

    def test_insufficient_samples_is_unknown(self):
        v = opt.volatility_factor("20261103", CFG, series=_mk_series(n=12))
        self.assertEqual(v["status"], "UNKNOWN")
        self.assertEqual(v["factor"], 1.0)

    def test_no_future_data_in_vol(self):
        a = opt.volatility_factor("20261103", CFG,
                                  series=_mk_series(tail_big=True))
        b = opt.volatility_factor("20261103", CFG,
                                  series=_mk_series(tail_big=True,
                                                    future_big=True))
        for k in ("vol_today", "baseline_vol", "factor", "status"):
            self.assertEqual(a[k], b[k], k)

    def test_baseline_excludes_today(self):
        a = opt.volatility_factor("20261103", CFG, series=_mk_series())
        b = opt.volatility_factor("20261103", CFG,
                                  series=_mk_series(last_spike=True))
        self.assertEqual(a["baseline_vol"], b["baseline_vol"])
        self.assertNotEqual(a["vol_today"], b["vol_today"])

    def test_tighten_reduces_risk_cap_in_compute(self):
        r = _mk_r(state="S5", target=40, p0_cap=100)
        adj = opt.compute(r, CFG, series=_mk_series(tail_big=True))
        self.assertLess(adj.risk_cap, 100)
        self.assertIn("budget_effective", adj.audit.volatility)
        self.assertLess(adj.audit.volatility["budget_effective"],
                        adj.audit.volatility["budget_base"])


# ============================================================
class TestRangeAndLadder(unittest.TestCase):
    """P2 动态建议区间 / 阶梯 off-by-one / c5 恒真占位符。"""

    def test_range_ratio_rises_with_confirmations(self):
        adj = opt.compute(_mk_r(easing="PASS", rebound="PASS"), CFG,
                          series=_mk_series())
        self.assertAlmostEqual(adj.lo_ratio, 0.6, places=2)

    def test_range_ratio_falls_with_failures(self):
        adj = opt.compute(_mk_r(easing="FAIL", rebound="FAIL"), CFG,
                          series=_mk_series())
        self.assertAlmostEqual(adj.lo_ratio, 0.4, places=2)

    def test_range_ratio_clamped(self):
        c = _cfg_with(range_ratio={"confirm_bonus": 5.0,
                                   "confirm_penalty": 5.0})
        adj = opt.compute(_mk_r(easing="FAIL", rebound="FAIL"), c,
                          series=_mk_series())
        self.assertGreaterEqual(adj.lo_ratio, 0.3)
        self.assertLessEqual(adj.lo_ratio, 0.7)

    def test_ladder_off_by_one_fixed(self):
        r = _mk_r(ladder_next=30)
        h = {"trade_date": "20261102", "state": "S5", "target": 10,
             "cooldown_left": 0, "mainline_ok": True}
        patch, info = opt.build_ladder_patch(r, CFG, 10, h, final_target=40)
        self.assertEqual(patch.next_step, 20)         # 旧口径为 30
        self.assertEqual(info["next_step_before"], 30)
        self.assertEqual(patch.current_step, 0)
        self.assertIn("0", str(patch.reason))

    def test_ladder_zero_and_19(self):
        r = _mk_r()
        h = {"trade_date": "20261102", "state": "S5", "target": 10,
             "cooldown_left": 0, "mainline_ok": True}
        for cur in (0, 19):
            patch, _ = opt.build_ladder_patch(r, CFG, cur, h, final_target=40)
            self.assertEqual(patch.next_step, 20)

    def test_c5_real_true_allows_add(self):
        r = _mk_r(mainline=True)
        h = {"trade_date": "20261102", "state": "S5", "target": 10,
             "cooldown_left": 0, "mainline_ok": True}
        patch, info = opt.build_ladder_patch(r, CFG, 10, h, final_target=40)
        self.assertEqual(info["c5"], "PASS")
        self.assertTrue(patch.can_add)

    def test_c5_unknown_does_not_pass(self):
        r = _mk_r(mainline=None)
        h = {"trade_date": "20261102", "state": "S5", "target": 10,
             "cooldown_left": 0, "mainline_ok": True}
        patch, info = opt.build_ladder_patch(r, CFG, 10, h, final_target=40)
        self.assertEqual(info["c5"], "UNKNOWN")
        self.assertFalse(patch.can_add)
        self.assertIn("UNKNOWN", patch.reason)

    def test_c5_no_history_is_unknown(self):
        r = _mk_r(mainline=True)
        patch, info = opt.build_ladder_patch(r, CFG, 10, None, final_target=40)
        self.assertEqual(info["c5"], "UNKNOWN")
        self.assertFalse(patch.can_add)

    def test_c5_mainline_deterioration_fails(self):
        r = _mk_r(mainline=False)
        h = {"trade_date": "20261102", "state": "S5", "target": 10,
             "cooldown_left": 0, "mainline_ok": True}
        patch, info = opt.build_ladder_patch(r, CFG, 10, h, final_target=40)
        self.assertEqual(info["c5"], "FAIL")
        self.assertFalse(patch.can_add)

    def test_base_ladder_check_c5_still_true(self):
        """V10.1 原决策记录保持原样（恒真占位符留在原记录里供对比）。"""
        r = _mk_r()
        opt.build_ladder_patch(r, CFG, 10, None, final_target=40)
        self.assertIs(r.base.ladder.checks["主线未发生变化"], True)


# ============================================================
class TestCashFloor(unittest.TestCase):
    """P2 现金地板与满仓授权语义。"""

    def test_s6_full_allowed_gets_explanation(self):
        r = _mk_r(state="S6", target=90, hard_cap=90,
                  auth_result=v101.FULL_ALLOWED)
        adj = opt.compute(r, CFG, series=_mk_series())
        self.assertIn("结算备付金", adj.cash_floor_note)
        self.assertFalse(adj.audit.cash_floor["waived"])

    def test_non_s6_no_explanation(self):
        r = _mk_r(state="S5", target=40, hard_cap=40,
                  auth_result=v101.FULL_ALLOWED)
        adj = opt.compute(r, CFG, series=_mk_series())
        self.assertEqual(adj.cash_floor_note, "")

    def test_waive_switch_changes_only_when_enabled(self):
        r = _mk_r(state="S6", target=90, hard_cap=90,
                  auth_result=v101.FULL_ALLOWED)
        c = _cfg_with(cash_floor={"waive_for_s6": True})
        adj = opt.compute(r, c, series=_mk_series())
        self.assertTrue(adj.audit.cash_floor["waived"])
        self.assertEqual(CFG["position_optimizer"]["cash_floor"]["waive_for_s6"],
                         False)

    def test_waive_requires_all_confirmations(self):
        r = _mk_r(state="S6", target=90, hard_cap=90,
                  auth_result=v101.FULL_ALLOWED, rebound="FAIL")
        c = _cfg_with(cash_floor={"waive_for_s6": True})
        adj = opt.compute(r, c, series=_mk_series())
        self.assertFalse(adj.audit.cash_floor["waived"])


# ============================================================
class TestRealChain(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_SLOW_V107_SMOKE") == "1",
                         "真实链路慢测：需 RUN_SLOW_V107_SMOKE=1")
    def test_real_1103_report_and_summary(self):
        r = v107.analyze_trade_date_v107("20261103", current_position=20,
                                         persist=False)
        s = v107.render_v107_summary(r)
        self.assertIn("P1P2仓位优化", s)
        self.assertIsNotNone(r.opt)
        self.assertIn("【P1/P2 仓位优化】", v107.render_v107_report(r))
        self.assertLessEqual(r.plan.target_position, r.plan.hard_cap)


if __name__ == "__main__":
    unittest.main()
