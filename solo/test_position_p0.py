# -*- coding: utf-8 -*-
"""V10.7 · P0 修复测试（组合风险口径统一 + 决策/报告一致性）

运行： python -m pytest test_position_p0.py -q
慢测（真实链路）： RUN_SLOW_V107_SMOKE=1 python -m pytest test_position_p0.py -q

全部用例只读本地配置 / 本地 sqlite，不发起任何 tushare 请求。
"""
import os
import csv
import copy
import json
import types
import hashlib
import unittest
from unittest.mock import patch

import market_regime_v101 as v101
import market_regime_v107 as v107
import position_p0_fix as p0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = v107.load_config()

# 修复前记录的 V10.0 / V10.1 源码哈希 —— 用于锁定「一行未改」的增量约束
V10_SHA256 = "1fa7b5780c5ea2c85d7ede676a9686bfa1a3f8713d25d121e1d9c05be9ff166e"
V101_SHA256 = "18e0fab27a441101da9fa87314c3db46b5f62958b6257339a21821c17f4ba0d5"

SD = 0.0306          # 当前 ETF 波动率代理止损距离


def _mk_result(state="S5", suggested=40, total_risk=0.489, budget=3.5,
               stop_distance=SD):
    """构造一个最小的 V10.7 结果桩（鸭子类型，不跑完整链路）。"""
    auth = v101.position_authority(state, CFG)
    pf = v101.PortfolioRisk(
        total_risk_pct=total_risk, risk_budget_pct=budget,
        budget_usage=round(total_risk / budget, 3), ok=True,
        used_proxy=True, proxy_stop_distance=stop_distance, cash_floor_max=90.0)
    tgt = v101.TargetPosition(suggested=suggested, state=state, portfolio=pf)
    r = types.SimpleNamespace(
        state=state, authority=auth, target=tgt, portfolio=pf,
        plan=types.SimpleNamespace(hard_cap=auth.max_position),
        base=None, p0=None, trade_date="20261103")
    return r, pf


def _mk_renderable():
    """补齐 render_v107_report / render_v107_summary 需要的全部字段。"""
    r, _ = _mk_result()
    r.state_cn = v107.STATE_CN.get(r.state, "")
    r.state_days = 1
    r.gate = types.SimpleNamespace(base_state=r.state, effective_state=r.state,
                                   blocked=[], applied=[])
    r.amount = r.easing = r.rebound = r.structural = None
    r.authorization = types.SimpleNamespace(result=v101.FULL_DENIED)
    r.plan = v107.build_position_plan(
        r.state, r.target, r.authority, r.portfolio, r.authorization,
        types.SimpleNamespace(qualified_count=0, data_available=False),
        None, CFG)
    r.switch_conditions = {}
    r.base = types.SimpleNamespace()          # 由 patch 的 render_v101_report 接管
    r.p0 = p0.apply_p0_fix(r, CFG)
    return r


class TestRiskBasis(unittest.TestCase):
    """缺陷①：风险敞口必须与仓位挂钩。"""

    def test_old_basis_saturates_after_20pct(self):
        """V10.1 旧口径在仓位 ≥20% 后被 15% 单股上限封死（锁定缺陷本身）。"""
        auth = v101.position_authority("S4", CFG)
        vals = [v101.check_portfolio_risk([], p, CFG, state="S4",
                                          authority=auth,
                                          fallback_stop_distance=SD).total_risk_pct
                for p in (5, 20, 40, 60, 90)]
        self.assertAlmostEqual(vals[1], vals[2], places=3)
        self.assertAlmostEqual(vals[2], vals[4], places=3)

    def test_consistent_basis_is_monotonic_in_position(self):
        """统一口径 = 仓位 × (sd + extra)，严格随仓位单调增长。"""
        auth = v101.position_authority("S4", CFG)
        vals = [p0.compute_consistent_risk(p, "S4", auth, CFG,
                                           stop_distance=SD).total_risk_pct
                for p in (5, 20, 40, 60, 90)]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(len(set(vals)), len(vals))
        self.assertAlmostEqual(vals[0], 5 * (SD + 0.002), places=3)
        self.assertAlmostEqual(vals[-1], 90 * (SD + 0.002), places=3)

    def test_risk_cap_from_budget(self):
        """预算反解上限：budget / (sd + extra)。"""
        self.assertEqual(p0.risk_cap_from_budget(1.5, 0.0306, 0.002), 46)
        self.assertEqual(p0.risk_cap_from_budget(6.0, 0.08, 0.002), 73)
        # 极端：止损距离为 0 时不乱收紧
        self.assertEqual(p0.risk_cap_from_budget(6.0, 0.0, 0.0), 100)

    def test_all_states_risk_cap_not_below_authority(self):
        """当前 sd 下各状态 risk_cap 均 ≥ 权限 → 历史结论不变（纯修复）。"""
        for s in ("S3", "S4", "S2", "S5", "S1", "S6"):
            a = v101.position_authority(s, CFG)
            c = p0.compute_consistent_risk(a.max_position, s, a, CFG,
                                           stop_distance=SD)
            self.assertGreaterEqual(c.risk_cap, a.max_position,
                                    f"{s} 不应在当前波动率下被风险闸门收紧")

    def test_full_year_replay_unchanged(self):
        """全年 200 日回放：没有一天会因 P0 修复被收紧（不改变历史结论）。"""
        path = os.path.join(os.path.dirname(BASE_DIR), "report_daily",
                            "v107_replay_2026.csv")
        if not os.path.exists(path):
            self.skipTest("全年回放 CSV 不存在")
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        tightened = []
        for r in rows:
            st, pos = r["eff_state"], int(r["eff_target"])
            a = v101.position_authority(st, CFG)
            c = p0.compute_consistent_risk(pos, st, a, CFG, stop_distance=SD)
            if c.risk_cap < pos:
                tightened.append((r["trade_date"], st, pos, c.risk_cap))
        self.assertGreater(len(rows), 150)
        self.assertEqual(tightened, [], f"不应改变历史结论，实际收紧：{tightened[:5]}")


class TestObjectConsistency(unittest.TestCase):
    """缺陷②：决策依据与报告展示必须同源。"""

    def test_same_object_and_same_value(self):
        r, _ = _mk_result()
        rep = p0.apply_p0_fix(r, CFG)
        self.assertIs(r.portfolio, r.target.portfolio)
        self.assertTrue(rep.consistency_ok)
        self.assertAlmostEqual(r.portfolio.total_risk_pct,
                               rep.consistent.total_risk_pct, places=3)
        self.assertEqual(r.portfolio.risk_budget_pct,
                         rep.consistent.risk_budget_pct)

    def test_base_decision_record_not_polluted(self):
        """deepcopy 生效：V10.1 原决策记录对象一个字段都不能被改。"""
        r, pf = _mk_result()
        before = copy.deepcopy(pf)
        p0.apply_p0_fix(r, CFG)
        self.assertIsNot(r.portfolio, pf)
        self.assertEqual(pf.total_risk_pct, before.total_risk_pct)
        self.assertEqual(pf.ok, before.ok)
        self.assertEqual(pf.proxy_stop_distance, before.proxy_stop_distance)
        self.assertEqual(pf.violations, before.violations)

    def test_wrong_display_value_is_repaired(self):
        """故意传入 99.0% 超预算展示值，修复后展示与决策依据同源。"""
        r, _ = _mk_result(total_risk=99.0)
        rep = p0.apply_p0_fix(r, CFG)
        self.assertEqual(rep.original["total_risk_pct"], 99.0)
        self.assertAlmostEqual(r.portfolio.total_risk_pct,
                               40 * (SD + 0.002), places=2)
        self.assertTrue(r.portfolio.ok)
        self.assertIs(r.portfolio, r.target.portfolio)

    def test_original_snapshot_kept_for_comparison(self):
        """新旧版本可对比：原口径快照完整保留。"""
        r, _ = _mk_result()
        rep = p0.apply_p0_fix(r, CFG)
        for k in ("total_risk_pct", "risk_budget_pct", "budget_usage",
                  "ok", "used_proxy", "basis", "proxy_stop_distance", "violations"):
            self.assertIn(k, rep.original)
        self.assertEqual(rep.original["total_risk_pct"], 0.489)
        self.assertEqual(rep.original["basis"], "proxy_fallback")
        self.assertIsNotNone(rep.as_dict()["consistent"])

    def test_holdings_basis_is_labelled(self):
        """有候选标的的逐仓原口径必须被正确标注，避免与代理口径混淆。"""
        r, _ = _mk_result(total_risk=1.817)
        r.portfolio.used_proxy = False
        r.portfolio.ok = False        # 真实 11-03：逐仓口径超预算
        r.portfolio.violations = ["组合总风险 1.82% 超出预算 1.50%"]
        rep = p0.apply_p0_fix(r, CFG)
        self.assertEqual(rep.original["basis"], "holdings_per_stock")
        self.assertNotEqual(rep.original["ok"], rep.consistent.ok)
        text = "\n".join(p0.render_p0_section(rep))
        self.assertIn("逐仓口径", text)
        self.assertIn("相反的 ok 结论", text)
        self.assertIn("ETF 波动率代理", text)


class TestRiskGateActuallyWorks(unittest.TestCase):
    """修复后风险闸门必须在高波动环境下真正收紧。"""

    def test_high_volatility_tightens_suggested_and_hard_cap(self):
        r, pf = _mk_result(state="S6", suggested=100)
        with patch.object(p0, "resolve_stop_distance", return_value=0.08):
            rep = p0.apply_p0_fix(r, CFG)
        plan = v107.build_position_plan(
            "S6", r.target, r.authority, r.portfolio,
            types.SimpleNamespace(result=v101.FULL_DENIED),
            types.SimpleNamespace(qualified_count=0, data_available=False),
            None, CFG, risk_cap=rep.risk_cap)
        self.assertEqual(rep.risk_cap, 73)
        self.assertTrue(rep.changed)
        self.assertEqual(rep.suggested_before, 100)
        self.assertEqual(rep.suggested_after, 73)
        self.assertEqual(plan.hard_cap, 73)
        self.assertFalse(r.portfolio.ok)
        self.assertTrue(any("[P0]" in x for x in r.target.limited_by))
        self.assertEqual(pf.total_risk_pct, 0.489)   # 原对象未被污染

    def test_unknown_does_not_default_pass(self):
        """止损距离缺失 → UNKNOWN：ok=False、不收紧仓位、关闭主动加仓。"""
        r, _ = _mk_result()
        with patch.object(p0, "resolve_stop_distance", return_value=None):
            rep = p0.apply_p0_fix(r, CFG)
        self.assertIsNone(rep.consistent)
        self.assertIsNone(rep.risk_cap)
        self.assertIn("平均止损距离", "".join(rep.missing))
        self.assertFalse(r.portfolio.ok)
        self.assertIs(r.portfolio, r.target.portfolio)
        self.assertEqual(rep.suggested_before, rep.suggested_after)
        self.assertTrue(any("UNKNOWN" in v for v in r.portfolio.violations))

    def test_build_position_plan_risk_cap_optional(self):
        """不传 risk_cap 时行为与修复前一致（A/B 兼容）。"""
        r, _ = _mk_result(state="S6", suggested=100)
        kw = dict(state="S6", target=r.target, authority=r.authority,
                  portfolio=r.portfolio,
                  authorization=types.SimpleNamespace(result=v101.FULL_DENIED),
                  opportunities=types.SimpleNamespace(qualified_count=0,
                                                      data_available=False),
                  current_position=None, cfg=CFG)
        p_old = v107.build_position_plan(**kw)
        p_cap = v107.build_position_plan(risk_cap=73, **kw)
        self.assertGreater(p_old.hard_cap, p_cap.hard_cap)
        self.assertEqual(p_cap.hard_cap, 73)
        self.assertTrue(any("风险预算反解" in x for x in p_cap.limits))

    def test_disabled_is_noop(self):
        """enabled=false 时不修改任何字段（A/B 对照）。"""
        cfg = copy.deepcopy(CFG)
        cfg["p0_fixes"] = dict(cfg.get("p0_fixes") or {}, enabled=False)
        r, pf = _mk_result()
        rep = p0.apply_p0_fix(r, cfg)
        self.assertFalse(rep.enabled)
        self.assertIs(r.portfolio, pf)
        self.assertIs(r.target.portfolio, pf)
        self.assertIsNone(rep.consistent)


class TestRenderAndIntegration(unittest.TestCase):
    """报告 / 摘要 / 配置 / 增量约束。"""

    def test_report_section_present(self):
        r = _mk_renderable()
        with patch.object(v101, "render_v101_report", return_value="（V10.1 原文）"):
            text = v107.render_v107_report(r, CFG)
        self.assertIn("【P0 风险口径修复】", text)
        self.assertIn("V10.1 原口径", text)
        self.assertIn("统一口径", text)
        self.assertIn("决策/报告一致性：✓ 同一对象", text)
        self.assertIn("【V10.1 原决策记录", text)

    def test_summary_contract_unchanged_plus_p0(self):
        """用户要求的 12 字段一个不少，另加 P0 对比字段。"""
        r = _mk_renderable()
        s = v107.render_v107_summary(r)
        for k in ("市场状态", "卖压衰减确认", "反弹确认", "结构性机会",
                  "最强行业", "行业持续性", "行业内部广度", "主动试探权限",
                  "建议仓位", "仓位硬上限", "满仓授权", "切换关键条件"):
            self.assertIn(k, s)
        self.assertIn("P0风险口径修复", s)
        self.assertTrue(s["P0风险口径修复"]["consistency_ok"])

    def test_config_has_p0_section(self):
        p = os.path.join(BASE_DIR, "config_v107_refine.json")
        d = json.load(open(p, encoding="utf-8"))
        self.assertIn("p0_fixes", d)
        self.assertTrue(d["p0_fixes"]["enabled"])
        self.assertLessEqual(d["p0_fixes"]["max_risk_cap"], 100)

    def test_v10_v101_source_untouched(self):
        """增量约束：v10 / v101 源码一行未改。"""
        for fn, expect in (("market_regime_v10.py", V10_SHA256),
                           ("market_regime_v101.py", V101_SHA256)):
            p = os.path.join(BASE_DIR, fn)
            got = hashlib.sha256(open(p, "rb").read()).hexdigest()
            self.assertEqual(got, expect, f"{fn} 被修改，违反增量约束")


@unittest.skipUnless(os.environ.get("RUN_SLOW_V107_SMOKE"),
                     "真实链路冒烟较慢，默认跳过")
class TestRealDataSmoke(unittest.TestCase):
    """真实交易日完整链路（默认跳过，按需开启）。"""

    def test_20261103_end_to_end(self):
        r = v107.analyze_trade_date_v107("20261103", cfg=CFG, persist=False)
        self.assertIsNotNone(r.p0)
        self.assertIs(r.portfolio, r.target.portfolio)
        self.assertTrue(r.p0.consistency_ok)
        self.assertFalse(r.p0.changed)          # 纯修复，不改变历史结论
        self.assertIn("【P0 风险口径修复】", v107.render_v107_report(r, CFG))
        # V10.1 原决策记录仍在，且未被 P0 覆盖
        self.assertIsNotNone(r.base)
        self.assertIsNot(r.base.portfolio, r.portfolio)


if __name__ == "__main__":
    unittest.main()
