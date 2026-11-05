# -*- coding: utf-8 -*-
"""V10.7.3 · 波段闸门 swing_gate 测试（纯本地，不发起任何网络请求）

运行： python -m pytest test_swing_gate.py -q
"""
import os
import copy
import json
import datetime
import hashlib
import types
import unittest

import market_regime_v101 as v101
import market_regime_v107 as v107
import swing_gate as sg
from test_position_optimizer import _mk_r

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = v107.load_config()

V10_SHA256 = "1fa7b5780c5ea2c85d7ede676a9686bfa1a3f8713d25d121e1d9c05be9ff166e"
V101_SHA256 = "18e0fab27a441101da9fa87314c3db46b5f62958b6257339a21821c17f4ba0d5"
TD = "20261103"


def _digest(name):
    with open(os.path.join(BASE_DIR, name), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _cfg(profile=None, enabled=True):
    c = copy.deepcopy(CFG)
    c["swing_gate"]["enabled"] = bool(enabled)
    if profile is not None:
        c["swing_gate"]["profile"] = profile
    return c


def _series(n=80, slope=-0.5, base=100.0, future_rows=0):
    """构造 <= TD 的指数收盘序列；slope<0 下跌（触发 MA/回撤），>0 上涨。"""
    out = []
    d0 = datetime.date(2026, 1, 1)
    for i in range(n):
        d = d0 + datetime.timedelta(days=i * 2)
        out.append({"trade_date": d.strftime("%Y%m%d"),
                    "close": base + slope * i})
    for j in range(future_rows):
        out.append({"trade_date": "202612%02d" % (j + 1), "close": 9999.0})
    return out


class TestConfig(unittest.TestCase):
    def test_01_default_profile_only_state_filter(self):
        g = CFG["swing_gate"]
        self.assertTrue(g["enabled"])
        self.assertEqual(g["profile"], "default")
        self.assertEqual(g["profiles"]["default"],
                         {"state_filter": True, "ma_gate": False,
                          "drawdown_brake": False})
        self.assertEqual(g["profiles"]["swing3"],
                         {"state_filter": True, "ma_gate": True,
                          "drawdown_brake": True})
        self.assertEqual(g["layers"]["state_filter"]["factor"], 0.3)
        self.assertEqual(g["layers"]["state_filter"]["states"], ["S2", "S5"])
        self.assertEqual(g["execution"]["up_step_pct"], 10)

    def test_02_version_history(self):
        c = json.load(open(os.path.join(BASE_DIR, "config_v107_refine.json"),
                           encoding="utf-8"))
        # 10.7.3 条目必须保留（10.7.4+ 允许在其后继续追加增量版本）
        self.assertIn("10.7.3", [x["version"] for x in c["_version_history"]])

    def test_03_v10_v101_untouched(self):
        self.assertEqual(_digest("market_regime_v10.py"), V10_SHA256)
        self.assertEqual(_digest("market_regime_v101.py"), V101_SHA256)


class TestStateFilter(unittest.TestCase):
    """默认档：只依赖状态机，不依赖指数数据。"""

    def test_10_s2_and_s5_tighten_30pct(self):
        for state in ("S2", "S5"):
            r = _mk_r(state=state, target=30, hard_cap=30)
            a = sg.compute(r, _cfg(), base_target=30, hard_cap=30,
                           series=[], history={})
            self.assertEqual(a.audit.layers["state_filter"], sg.TIGHTEN)
            self.assertAlmostEqual(a.audit.effective_target_exact, 9.0, places=4)
            self.assertEqual(a.effective_target, 9)
            self.assertEqual(a.audit.layers["ma_gate"], sg.OFF)
            self.assertEqual(a.audit.layers["drawdown_brake"], sg.OFF)

    def test_11_other_states_untouched(self):
        for state in ("S1", "S3", "S4", "S6"):
            r = _mk_r(state=state, target=40, hard_cap=40)
            a = sg.compute(r, _cfg(), base_target=40, hard_cap=40,
                           series=[], history={})
            self.assertEqual(a.audit.layers["state_filter"], sg.OK)
            self.assertEqual(a.effective_target, 40)
            self.assertFalse(a.deny_active_add)

    def test_12_unknown_state_does_not_pass(self):
        r = _mk_r(state="", target=40, hard_cap=40)
        a = sg.compute(r, _cfg(), base_target=40, hard_cap=40,
                       series=[], history={})
        self.assertEqual(a.audit.layers["state_filter"], sg.UNKNOWN)
        self.assertTrue(a.deny_active_add)
        self.assertTrue(a.audit.missing)

    def test_13_ab_disabled_is_noop(self):
        r = _mk_r(state="S2", target=30, hard_cap=30)
        a = sg.compute(r, _cfg(enabled=False), base_target=30, hard_cap=30,
                       series=[], history={})
        self.assertFalse(a.enabled)
        self.assertEqual(a.effective_target, 30)
        self.assertEqual(a.audit.layers, {})
        self.assertTrue(any("A/B" in x for x in a.audit.notes))

    def test_14_profile_off_no_tighten(self):
        r = _mk_r(state="S2", target=30, hard_cap=30)
        a = sg.compute(r, _cfg(profile="off"), base_target=30, hard_cap=30,
                       series=[], history={})
        self.assertEqual(a.effective_target, 30)
        self.assertTrue(all(v == sg.OFF for v in a.audit.layers.values()))


class TestOptionalLayers(unittest.TestCase):
    def test_20_swing3_multiplies_all_factors(self):
        r = _mk_r(state="S2", target=100, hard_cap=100)
        a = sg.compute(r, _cfg(profile="swing3"), base_target=100,
                       hard_cap=100, series=_series(slope=-0.5), history={})
        self.assertEqual(a.audit.layers["state_filter"], sg.TIGHTEN)
        self.assertEqual(a.audit.layers["ma_gate"], sg.TIGHTEN)
        self.assertEqual(a.audit.layers["drawdown_brake"], sg.TIGHTEN)
        # 0.3 × 0.25 × 0.3 = 0.0225
        self.assertAlmostEqual(a.audit.effective_target_exact, 2.25, places=4)
        self.assertEqual(a.effective_target, 2)

    def test_21_uptrend_does_not_trigger_ma_or_dd(self):
        r = _mk_r(state="S1", target=60, hard_cap=60)
        a = sg.compute(r, _cfg(profile="swing3"), base_target=60,
                       hard_cap=60, series=_series(slope=+0.5), history={})
        self.assertEqual(a.audit.layers["ma_gate"], sg.OK)
        self.assertEqual(a.audit.layers["drawdown_brake"], sg.OK)
        self.assertEqual(a.effective_target, 60)

    def test_22_ma_only_profile(self):
        r = _mk_r(state="S2", target=40, hard_cap=40)
        a = sg.compute(r, _cfg(profile="ma_only"), base_target=40,
                       hard_cap=40, series=_series(slope=-0.5), history={})
        self.assertEqual(a.audit.layers["state_filter"], sg.OFF)
        self.assertEqual(a.audit.layers["ma_gate"], sg.TIGHTEN)
        self.assertAlmostEqual(a.audit.effective_target_exact, 10.0, places=4)

    def test_23_unknown_signal_denies_active_add(self):
        r = _mk_r(state="S1", target=40, hard_cap=40)
        a = sg.compute(r, _cfg(profile="swing3"), base_target=40,
                       hard_cap=40, series=_series(n=5), history={})
        self.assertEqual(a.audit.layers["ma_gate"], sg.UNKNOWN)
        self.assertTrue(a.deny_active_add)
        self.assertTrue(any("MA" in x for x in a.audit.missing))

    def test_24_missing_index_does_not_break_default_profile(self):
        """指数库不可用时默认档仍可用（S2/S5 不依赖指数）。"""
        r = _mk_r(state="S2", target=30, hard_cap=30)
        a = sg.compute(r, _cfg(), base_target=30, hard_cap=30,
                       series=[], history={})
        self.assertEqual(a.effective_target, 9)
        self.assertEqual(a.audit.signals["samples"], 0)
        self.assertFalse(a.deny_active_add)


class TestNoLookaheadAndBounds(unittest.TestCase):
    def test_30_future_rows_are_dropped(self):
        r = _mk_r(state="S1", target=40, hard_cap=40)
        s = _series(slope=+0.5, future_rows=3)
        a = sg.compute(r, _cfg(profile="swing3"), base_target=40,
                       hard_cap=40, series=s, history={})
        self.assertEqual(a.audit.signals["samples"], 80)
        self.assertLessEqual(a.audit.signals["last_date"], TD)

    def test_31_hard_cap_never_exceeded(self):
        r = _mk_r(state="S1", target=100, hard_cap=25)
        a = sg.compute(r, _cfg(), base_target=100, hard_cap=25,
                       series=[], history={})
        self.assertLessEqual(a.effective_target, 25)

    def test_32_pure_tighten_nevers_above_base(self):
        for prof in ("default", "swing3", "ma_only", "off"):
            r = _mk_r(state="S4", target=37, hard_cap=60)
            a = sg.compute(r, _cfg(profile=prof), base_target=37,
                           hard_cap=60, series=_series(slope=-0.2), history={})
            self.assertLessEqual(a.effective_target, 37, prof)

    def test_33_history_must_be_strictly_earlier(self):
        r = _mk_r(state="S1", target=40, hard_cap=40)
        h = {"trade_date": TD, "effective_exact": 5.0}
        a = sg.compute(r, _cfg(), base_target=40, hard_cap=40,
                       series=_series(), history=h)
        self.assertEqual(a.audit.execution["action"], "COLD_START")


class TestExecution(unittest.TestCase):
    def test_40_down_immediate(self):
        r = _mk_r(state="S1", target=20, hard_cap=20)
        h = {"trade_date": "20261102", "effective_exact": 80.0}
        a = sg.compute(r, _cfg(), base_target=20, hard_cap=20,
                       series=_series(), history=h)
        self.assertEqual(a.audit.execution["action"], "DOWN_IMMEDIATE")
        self.assertAlmostEqual(a.audit.effective_target_exact, 20.0, places=4)

    def test_41_up_limited_10pp(self):
        r = _mk_r(state="S1", target=100, hard_cap=100)
        h = {"trade_date": "20261102", "effective_exact": 10.0}
        a = sg.compute(r, _cfg(profile="off"), base_target=100,
                       hard_cap=100, series=_series(), history=h)
        self.assertEqual(a.audit.execution["action"], "UP_LIMITED")
        self.assertAlmostEqual(a.audit.effective_target_exact, 20.0, places=4)

    def test_42_up_within_step_ok(self):
        r = _mk_r(state="S1", target=18, hard_cap=18)
        h = {"trade_date": "20261102", "effective_exact": 10.0}
        a = sg.compute(r, _cfg(profile="off"), base_target=18,
                       hard_cap=18, series=_series(), history=h)
        self.assertEqual(a.audit.execution["action"], "UP_OK")
        self.assertAlmostEqual(a.audit.effective_target_exact, 18.0, places=4)

    def test_43_stale_history_is_cold_start(self):
        r = _mk_r(state="S1", target=40, hard_cap=40)
        h = {"trade_date": "20260101", "effective_exact": 5.0}
        a = sg.compute(r, _cfg(), base_target=40, hard_cap=40,
                       series=_series(), history=h)
        self.assertEqual(a.audit.execution["action"], "COLD_START")

    def test_44_history_record_fields(self):
        r = _mk_r(state="S2", target=30, hard_cap=30)
        a = sg.compute(r, _cfg(), base_target=30, hard_cap=30,
                       series=[], history={})
        rec = a.history_record
        self.assertEqual(rec["trade_date"], TD)
        self.assertEqual(rec["base_target"], 30)
        self.assertEqual(rec["effective_target"], 9)
        self.assertAlmostEqual(rec["factor"], 0.3, places=4)


class TestRenderAndIntegration(unittest.TestCase):
    def test_50_render_section(self):
        r = _mk_r(state="S2", target=30, hard_cap=30)
        a = sg.compute(r, _cfg(), base_target=30, hard_cap=30,
                       series=[], history={})
        L = sg.render_gate_section(a.audit)
        self.assertTrue(L[1].startswith("【波段闸门 swing_gate】"))
        self.assertTrue(any("30% → 9%" in x for x in L))
        self.assertTrue(any("S2/S5 状态过滤" in x for x in L))

    def test_51_render_disabled(self):
        r = _mk_r(state="S2", target=30, hard_cap=30)
        a = sg.compute(r, _cfg(enabled=False), base_target=30, hard_cap=30,
                       series=[], history={})
        self.assertTrue(any("A/B 对照" in x for x in
                            sg.render_gate_section(a.audit)))

    def test_52_as_dict_roundtrip(self):
        r = _mk_r(state="S5", target=20, hard_cap=20)
        a = sg.compute(r, _cfg(), base_target=20, hard_cap=20,
                       series=[], history={})
        d = a.audit.as_dict()
        self.assertEqual(d["profile"], "default")
        self.assertEqual(d["effective_target"], 6)
        json.dumps(d, ensure_ascii=False)  # 可 JSON 序列化

    def _report_stub(self):
        """只渲染到仓位/闸门段所需的最小桩（其余增量段置空）。"""
        r = _mk_r(state="S2", target=30, hard_cap=30)
        r.structural = None
        r.amount = None
        r.easing = None
        r.rebound = None
        r.p0 = None
        r.opt = None
        r.base = None
        return r

    def test_53_summary_has_gate_key(self):
        r = self._report_stub()
        a = sg.compute(r, _cfg(), base_target=30, hard_cap=30,
                       series=[], history={})
        r.swing = a.audit
        s = v107.render_v107_summary(r)
        self.assertIn("波段闸门", s)
        self.assertEqual(s["波段闸门"]["effective_target"], 9)
        # 原 12 字段契约仍在
        for k in ("市场状态", "卖压衰减确认", "反弹确认", "结构性机会",
                  "建议仓位", "仓位硬上限", "满仓授权"):
            self.assertIn(k, s)

    def test_54_report_has_gate_section(self):
        r = self._report_stub()
        a = sg.compute(r, _cfg(), base_target=30, hard_cap=30,
                       series=[], history={})
        r.swing = a.audit
        txt = v107.render_v107_report(r)
        self.assertIn("【波段闸门 swing_gate】", txt)
        self.assertIn("有效目标：30% → 9%", txt)

    def test_55_v107result_has_swing_field(self):
        self.assertIsNone(v107.V107Result().swing)


if __name__ == "__main__":
    unittest.main()
