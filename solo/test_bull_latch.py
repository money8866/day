# -*- coding: utf-8 -*-
"""V10.7.4 · 主升段锁存 bull_latch 测试（纯本地，不发起任何网络请求）

运行： python -m pytest test_bull_latch.py -q
"""
import os
import copy
import json
import types
import datetime
import hashlib
import tempfile
import unittest

import market_regime_v101 as v101
import market_regime_v107 as v107
import bull_latch as bl
from test_position_optimizer import _mk_r

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = v107.load_config()

V10_SHA256 = "1fa7b5780c5ea2c85d7ede676a9686bfa1a3f8713d25d121e1d9c05be9ff166e"
V101_SHA256 = "18e0fab27a441101da9fa87314c3db46b5f62958b6257339a21821c17f4ba0d5"
TD = "20261103"


def _digest(name):
    with open(os.path.join(BASE_DIR, name), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _cfg(enabled=True, **kw):
    c = copy.deepcopy(CFG)
    c["bull_latch"]["enabled"] = bool(enabled)
    for k, v in kw.items():
        c["bull_latch"][k] = v
    return c


def _rows(n=80, slope=1.0, breadth=0.70, tail_drop_pct=None,
          missing_breadth_last=0, future_rows=0, last_date=TD):
    """构造 <= last_date 的指数/广度序列（升序）。"""
    end = datetime.datetime.strptime(last_date, "%Y%m%d").date()
    out = []
    for i in range(n):
        d = (end - datetime.timedelta(days=n - 1 - i)).strftime("%Y%m%d")
        c = 100.0 + slope * i
        if tail_drop_pct is not None and i == n - 1:
            c = c * (1.0 - tail_drop_pct)
        b = breadth(i, n) if callable(breadth) else breadth
        out.append({"trade_date": d, "close": c, "up_ratio": b})
    for i in range(missing_breadth_last):
        out[-1 - i]["up_ratio"] = None
    for j in range(future_rows):
        out.append({"trade_date": "202612%02d" % (j + 1),
                    "close": 99999.0, "up_ratio": 1.0})
    return out


def _r(state="S6", target=20, hard_cap=20, auth_result=None, p0_cap=100,
       portfolio_ok=True):
    r = _mk_r(state=state, target=target, hard_cap=hard_cap,
              auth_result=auth_result or v101.FULL_ALLOWED, p0_cap=p0_cap)
    r.portfolio.ok = portfolio_ok
    return r


def _compute(r, cfg, rows, base=20, cap=20, history=None):
    return bl.compute(r, cfg, base_target=base, hard_cap=cap,
                      series=rows, history=history if history is not None else {})


# ============================================================
class TestConfig(unittest.TestCase):
    def test_01_default_is_disabled_ab(self):
        b = CFG["bull_latch"]
        self.assertFalse(b["enabled"])
        self.assertEqual(b["profile"], "recommended")
        self.assertTrue(b["break_hard_cap"])
        self.assertFalse(b["waive_cash_floor_on_latch"])
        self.assertEqual(b["profiles"]["recommended"]["position"]["target_pct"], 100)
        self.assertEqual(b["profiles"]["recommended"]["position"]["max_pct"], 100)
        self.assertEqual(b["profiles"]["recommended"]["position"]["up_step_latch_pct"], 50)
        self.assertEqual(b["profiles"]["recommended"]["exit"]["trail_pct"], 0.04)
        self.assertEqual(b["profiles"]["recommended"]["exit"]["hold_days"], 10)
        self.assertFalse(b["profiles"]["recommended"]["exit"]["cooldown_days"])

    def test_02_version_history_1074(self):
        c = json.load(open(os.path.join(BASE_DIR, "config_v107_refine.json"),
                           encoding="utf-8"))
        vs = [x["version"] for x in c["_version_history"]]
        # 10.7.4 条目必须保留（10.7.5+ 允许在其后继续追加增量版本）
        self.assertIn("10.7.4", vs)
        self.assertIn("10.7.3", vs)

    def test_03_v10_v101_untouched(self):
        self.assertEqual(_digest("market_regime_v10.py"), V10_SHA256)
        self.assertEqual(_digest("market_regime_v101.py"), V101_SHA256)

    def test_04_profile_override(self):
        cfg = _cfg(profile="steady")
        self.assertEqual(bl.resolve_cfg(cfg)["signal"]["ma_window"], 10)
        self.assertEqual(bl.resolve_cfg(cfg)["exit"]["mode"], "ma10")
        self.assertEqual(bl.resolve_cfg(cfg)["signal"]["breadth_threshold"], 0.60)
        # 原配置不被污染
        self.assertEqual(CFG["bull_latch"]["profile"], "recommended")


class TestABDisabled(unittest.TestCase):
    def test_10_disabled_is_complete_noop(self):
        a = _compute(_r(), _cfg(enabled=False), _rows())
        self.assertFalse(a.enabled)
        self.assertEqual(a.effective_target, 20)
        self.assertIsNone(a.authorized_hard_cap)
        self.assertEqual(a.history_record, {})
        self.assertFalse(a.deny_active_add)
        self.assertTrue(any("A/B" in x for x in a.audit.notes))
        self.assertEqual(a.audit.signals, {})

    def test_11_disabled_ignores_unknown_data(self):
        a = _compute(_r(), _cfg(enabled=False), _rows(n=5), base=37, cap=37)
        self.assertEqual(a.effective_target, 37)
        self.assertEqual(a.audit.missing, [])


class TestSignals(unittest.TestCase):
    def test_20_uptrend_and_breadth_triggers(self):
        a = _compute(_r(), _cfg(), _rows())
        s = a.audit.signals
        self.assertTrue(s["signal"])
        self.assertEqual(s["samples"], 80)
        self.assertAlmostEqual(s["ma"], 169.5, places=4)
        self.assertAlmostEqual(s["ma_prev5"], 164.5, places=4)
        self.assertAlmostEqual(s["breadth5"], 0.70, places=4)
        self.assertEqual(a.audit.missing, [])

    def test_21_weak_breadth_no_trigger(self):
        a = _compute(_r(), _cfg(), _rows(breadth=0.50))
        self.assertFalse(a.audit.signals["signal"])
        self.assertFalse(a.audit.latch["locked"])
        self.assertEqual(a.effective_target, 20)

    def test_22_downtrend_no_trigger(self):
        a = _compute(_r(), _cfg(), _rows(slope=-1.0))
        self.assertFalse(a.audit.signals["signal"])

    def test_23_insufficient_samples_unknown(self):
        a = _compute(_r(), _cfg(), _rows(n=20))
        self.assertIsNone(a.audit.signals["signal"])
        self.assertTrue(a.audit.missing)
        self.assertTrue(a.deny_active_add)
        self.assertFalse(a.audit.latch["position_active"])
        self.assertEqual(a.effective_target, 20)
        self.assertIsNone(a.authorized_hard_cap)

    def test_24_missing_breadth_unknown(self):
        a = _compute(_r(), _cfg(), _rows(missing_breadth_last=1))
        self.assertIsNone(a.audit.signals["breadth5"])
        self.assertIsNone(a.audit.signals["signal"])
        self.assertTrue(a.deny_active_add)

    def test_25_no_future_data(self):
        rows = _rows()
        future = [{"trade_date": "202612%02d" % (j + 1),
                   "close": 99999.0, "up_ratio": 1.0} for j in range(3)]
        a1 = _compute(_r(), _cfg(), rows)
        a2 = _compute(_r(), _cfg(), rows + future)
        # 未来行必须被完全裁掉（样本数、信号、结果都不变）
        self.assertEqual(a1.audit.signals["samples"], 80)
        self.assertEqual(a2.audit.signals["samples"], 80)
        self.assertEqual(a1.effective_target, a2.effective_target)
        self.assertEqual(a1.history_record["left"], a2.history_record["left"])

    def test_26_load_features_truncates_future(self):
        idx = [{"trade_date": r["trade_date"], "close": r["close"]}
               for r in _rows(future_rows=2)]
        mic = [{"trade_date": r["trade_date"], "up_count": 70, "total": 100}
               for r in _rows(future_rows=2)]
        rows = bl.load_features(TD, index_series=idx, micro_series=mic)
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(r["trade_date"] <= TD for r in rows))
        self.assertAlmostEqual(rows[-1]["up_ratio"], 0.70, places=4)


class TestLatchStateMachine(unittest.TestCase):
    def test_30_first_trigger_latch_10_days(self):
        a = _compute(_r(), _cfg(), _rows())
        self.assertTrue(a.audit.latch["locked"])
        self.assertEqual(a.audit.latch["left_after"], 9)
        self.assertEqual(a.history_record["left"], 9)
        self.assertIsNotNone(a.history_record["peak"])
        self.assertEqual(a.history_record["signal"], "PASS")

    def test_31_signal_gone_keeps_latch_9_more_days(self):
        cfg, r = _cfg(), _r()
        base = datetime.datetime.strptime(TD, "%Y%m%d").date()
        a = _compute(r, cfg, _rows())
        self.assertEqual(a.history_record["left"], 9)
        hist = dict(a.history_record)
        locked_days = 0
        # 信号消失（广度 0.50）后逐日推进，锁存应再持续 9 天
        for i in range(1, 11):
            d = (base + datetime.timedelta(days=i)).strftime("%Y%m%d")
            r.trade_date = d
            rows = _rows(breadth=0.50, last_date=d)
            hist["trade_date"] = (base + datetime.timedelta(
                days=i - 1)).strftime("%Y%m%d")
            a = _compute(r, cfg, rows, history=hist)
            if a.audit.latch["locked"]:
                locked_days += 1
            hist = dict(a.history_record)
        self.assertEqual(locked_days, 9)

    def test_32_trail_4pct_exit(self):
        cfg, r = _cfg(), _r()
        rows = _rows(tail_drop_pct=0.05, breadth=0.50)
        hist = {"trade_date": "20261102", "left": 8, "peak": 180.0,
                "cool": 0, "effective_exact": 90.0, "effective_target": 90,
                "base_target": 20, "locked": 1, "signal": "PASS"}
        a = _compute(r, cfg, rows, history=hist)
        self.assertTrue(a.audit.latch["exit_triggered"])
        self.assertFalse(a.audit.latch["locked"])
        self.assertEqual(a.effective_target, 20)   # 降仓立即
        self.assertEqual(a.audit.execution["action"], "DOWN_IMMEDIATE")

    def test_33_down_immediate(self):
        hist = {"trade_date": "20261102", "left": 0, "peak": None,
                "cool": 0, "effective_exact": 80.0, "effective_target": 80,
                "base_target": 20, "locked": 0, "signal": "FAIL"}
        a = _compute(_r(), _cfg(), _rows(breadth=0.50), history=hist)
        self.assertEqual(a.audit.execution["action"], "DOWN_IMMEDIATE")
        self.assertEqual(a.effective_target, 20)

    def test_34_cold_start_no_limit_then_50pp(self):
        a0 = _compute(_r(), _cfg(), _rows(), history={})
        self.assertEqual(a0.audit.execution["action"], "COLD_START")
        self.assertEqual(a0.effective_target, 100)
        hist = {"trade_date": "20261102", "left": 8, "peak": 178.0,
                "cool": 0, "effective_exact": 0.0, "effective_target": 0,
                "base_target": 20, "locked": 1, "signal": "PASS"}
        a1 = _compute(_r(), _cfg(), _rows(), history=hist)
        self.assertEqual(a1.audit.execution["action"], "UP_LIMITED")
        self.assertEqual(a1.effective_target, 50)

    def test_35_normal_period_10pp_limit(self):
        hist = {"trade_date": "20261102", "left": 0, "peak": None,
                "cool": 0, "effective_exact": 0.0, "effective_target": 0,
                "base_target": 20, "locked": 0, "signal": "FAIL"}
        a = _compute(_r(), _cfg(), _rows(breadth=0.50), base=20, history=hist)
        self.assertEqual(a.audit.execution["step_pct"], 10)
        self.assertEqual(a.effective_target, 10)

    def test_36_unknown_does_not_raise_position(self):
        hist = {"trade_date": "20261102", "left": 8, "peak": 178.0,
                "cool": 0, "effective_exact": 20.0, "effective_target": 20,
                "base_target": 20, "locked": 1, "signal": "PASS"}
        a = _compute(_r(), _cfg(), _rows(missing_breadth_last=1), history=hist)
        self.assertTrue(a.audit.latch["locked"])          # 状态链仍在
        self.assertFalse(a.audit.latch["position_active"])  # 但不升仓
        self.assertEqual(a.effective_target, 20)
        self.assertIsNone(a.authorized_hard_cap)
        self.assertTrue(a.deny_active_add)


class TestAuthorization(unittest.TestCase):
    def test_40_full_allowed_break_authorized(self):
        a = _compute(_r(), _cfg(), _rows())
        au = a.audit.authorization
        self.assertEqual(au["mode"], bl.BREAK_ALLOWED)
        self.assertEqual(au["full_authorization"], v101.FULL_ALLOWED)
        self.assertIsNotNone(au["risk_cap"])
        self.assertGreaterEqual(au["risk_cap"], 100)
        self.assertEqual(a.authorized_hard_cap, 100)

    def test_41_full_denied_blocks_break(self):
        a = _compute(_r(auth_result=v101.FULL_DENIED), _cfg(), _rows())
        self.assertEqual(a.audit.authorization["mode"], bl.BLOCKED_AUTH)
        self.assertIsNone(a.authorized_hard_cap)
        self.assertLessEqual(a.effective_target, 20)

    def test_42_portfolio_not_ok_blocks_break(self):
        a = _compute(_r(portfolio_ok=False), _cfg(), _rows())
        self.assertEqual(a.audit.authorization["mode"], bl.BLOCKED_RISK)
        self.assertIsNone(a.authorized_hard_cap)

    def test_43_p0_risk_cap_below_target_blocks_break(self):
        r = _r()
        r.portfolio.proxy_stop_distance = 0.20   # sd 20% → risk_cap 远低于 100
        a = _compute(r, _cfg(), _rows())
        self.assertEqual(a.audit.authorization["mode"], bl.BLOCKED_RISK)
        self.assertIsNone(a.authorized_hard_cap)
        self.assertLessEqual(a.effective_target, 20)

    def test_44_break_hard_cap_false_is_cap_only(self):
        a = _compute(_r(), _cfg(break_hard_cap=False), _rows())
        self.assertEqual(a.audit.authorization["mode"], bl.BLOCKED_BY_CONFIG)
        self.assertEqual(a.effective_target, 20)   # max(base 20, hard_cap 20)
        self.assertIsNone(a.authorized_hard_cap)

    def test_45_no_leverage_even_if_configured(self):
        cfg = _cfg()
        cfg["bull_latch"]["profiles"]["recommended"]["position"].update(
            {"target_pct": 150, "max_pct": 200})
        a = _compute(_r(), cfg, _rows())
        self.assertLessEqual(a.effective_target, 100)
        self.assertTrue(a.authorized_hard_cap is None
                        or a.authorized_hard_cap <= 100)
        self.assertTrue(any("杠杆" in x for x in a.audit.notes))

    def test_46_unknown_signal_never_authorizes(self):
        a = _compute(_r(), _cfg(), _rows(n=10))
        self.assertEqual(a.audit.authorization["mode"], bl.NOT_NEEDED)
        self.assertIsNone(a.authorized_hard_cap)

    def test_47_build_plan_authorized_cap_still_keeps_cash_floor(self):
        r = _r(state="S3", target=5, hard_cap=5)
        p0 = v107.build_position_plan("S3", r.target, r.authority, r.portfolio,
                                      r.authorization, None, 0, CFG, risk_cap=100)
        p1 = v107.build_position_plan("S3", r.target, r.authority, r.portfolio,
                                      r.authorization, None, 0, CFG,
                                      risk_cap=100, authorized_cap=100)
        self.assertEqual(p0.hard_cap, 5)
        self.assertEqual(p1.hard_cap, 90)   # 现金地板 90 仍生效，不是 100
        self.assertTrue(any("主升段锁存授权" in x for x in p1.limits))


class TestHistoryAndReport(unittest.TestCase):
    def test_50_history_strictly_before_trade_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.db")
            orig = bl.v101_state_db
            bl.v101_state_db = lambda: path
            try:
                bl.save_history("20261103", {
                    "trade_date": "20261103", "left": 9, "peak": 178.0,
                    "cool": 0, "effective_exact": 50.0,
                    "effective_target": 50, "base_target": 20,
                    "locked": True, "signal": "PASS"})
                self.assertIsNone(bl.load_history("20261103"))  # 不读当日
                h = bl.load_history("20261104")
                self.assertIsNotNone(h)
                self.assertEqual(h["left"], 9)
                self.assertAlmostEqual(h["peak"], 178.0)
            finally:
                bl.v101_state_db = orig

    def test_51_render_section(self):
        a = _compute(_r(), _cfg(), _rows())
        L = bl.render_latch_section(a.audit)
        self.assertTrue(L[1].startswith("【主升段锁存 bull_latch】"))
        self.assertTrue(any("授权" in x for x in L))
        self.assertTrue(any("不碰杠杆" in x for x in L))
        off = _compute(_r(), _cfg(enabled=False), _rows())
        self.assertTrue(any("A/B" in x for x in bl.render_latch_section(off.audit)))
        self.assertEqual(bl.render_latch_section(None), [])

    def test_52_summary_has_new_key_without_touching_12_fields(self):
        r = _mk_r(state="S6", target=20, hard_cap=20)
        r.structural = None
        r.amount = None
        r.easing = None
        r.rebound = None
        r.p0 = None
        r.opt = None
        r.base = None
        r.bull = _compute(_r(), _cfg(), _rows()).audit
        s = v107.render_v107_summary(r)
        self.assertIn("主升段锁存", s)
        for k in ("市场状态", "卖压衰减确认", "反弹确认", "结构性机会",
                  "最强行业", "行业持续性", "行业内部广度", "主动试探权限",
                  "建议仓位", "仓位硬上限", "满仓授权", "切换关键条件"):
            self.assertIn(k, s)
        self.assertEqual(s["主升段锁存"]["effective_target"], 100)

    def test_53_v107_result_and_signature(self):
        import inspect
        self.assertIn("bull", v107.V107Result.__dataclass_fields__)
        sig = inspect.signature(v107.analyze_trade_date_v107)
        self.assertIn("bull_latch_history", sig.parameters)
        src = inspect.getsource(v107)
        self.assertIn("7e)", src)
        self.assertIn("bull_latch.compute", src)

    def test_54_downstream_block_and_template(self):
        src = open(os.path.join(BASE_DIR, "tushare_quant.py"),
                   encoding="utf-8").read()
        self.assertIn("主升段锁存: 档位", src)
        self.assertIn("主升段锁存-UNKNOWN", src)
        self.assertIn("若数据块有「主升段锁存」行且非「已关闭（A/B 对照）」", src)


if __name__ == "__main__":
    unittest.main()
