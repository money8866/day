# -*- coding: utf-8 -*-
"""V10.7.5 · 日内盘中 ↔ 盘后仓位锚点桥接测试（纯本地，不发网络请求）

运行： python -m pytest test_intraday_position_anchor.py -q

核心 invariants：
  · 严格 trade_date < 当日（禁止未来数据）
  · 只收紧不放大（cap_after <= cap_before 且 <= 盘后有效目标）
  · 不跨日继承满仓授权 / authorized_hard_cap
  · 缺失 / 过期 / 损坏 / 字段不全一律 fail-soft
"""
import datetime
import hashlib
import inspect
import json
import os
import tempfile
import unittest

import intraday_position_anchor as ipa
import _intraday_monitor as M

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
V10_SHA256 = "1fa7b5780c5ea2c85d7ede676a9686bfa1a3f8713d25d121e1d9c05be9ff166e"
V101_SHA256 = "18e0fab27a441101da9fa87314c3db46b5f62958b6257339a21821c17f4ba0d5"


def _digest(name):
    with open(os.path.join(BASE_DIR, name), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _cfg(enabled=True, max_age_days=7):
    return {"intraday_bridge": {"enabled": enabled,
                               "max_age_days": max_age_days}}


def _summary(date="20261103", eff=20.0, hard=20, *,
             p0=True, opt=True, swing=True, bull=False,
             bull_cap=100, extra=None):
    d = {
        "trade_date": date,
        "市场状态": "S4 卖压衰减",
        "建议仓位": "8%~20%",
        "仓位硬上限": hard,
        "P0风险口径修复": {"enabled": p0},
        "P1P2仓位优化": {"enabled": opt, "target_after": 20},
        "波段闸门": {
            "enabled": swing,
            "effective_target": eff,
            "effective_target_exact": eff,
        },
        "主升段锁存": {
            "enabled": bull,
            "effective_target": eff,
            "effective_target_exact": eff,
            "authorized_hard_cap": bull_cap if bull else None,
        },
    }
    if extra:
        d.update(extra)
    return d


def _write(d, date, obj=None):
    p = os.path.join(d, "market_regime_v107_summary_%s.json" % date)
    with open(p, "w", encoding="utf-8") as f:
        if obj is None:
            json.dump(_summary(date), f, ensure_ascii=False)
        elif isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, ensure_ascii=False)
    return p


class TestConfig(unittest.TestCase):

    def test_01_defaults(self):
        c = ipa.resolve_cfg({})
        self.assertTrue(c["enabled"])
        self.assertEqual(c["max_age_days"], 7)
        self.assertTrue(c["report_dir"].endswith("report_daily"))

    def test_02_switch_off(self):
        a = ipa.load_anchor("20261105", _cfg(enabled=False))
        self.assertEqual(a.status, "DISABLED")
        self.assertFalse(a.usable)
        self.assertEqual(ipa.apply_anchor_cap(60, a)[0], 60)

    def test_03_config_has_bridge_section(self):
        c = json.load(open(os.path.join(BASE_DIR, "config_v107_refine.json"),
                           encoding="utf-8"))
        self.assertIn("intraday_bridge", c)
        self.assertTrue(c["intraday_bridge"]["enabled"])
        vs = [x["version"] for x in c["_version_history"]]
        self.assertIn("10.7.5", vs)
        self.assertIn("10.7.4", vs)


class TestStrictDate(unittest.TestCase):

    def test_10_only_strictly_earlier(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103")
            _write(d, "20261105", _summary("20261105", eff=80.0, hard=100))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.status, "OK")
            self.assertEqual(a.basis_date, "20261103")
            self.assertEqual(a.anchor_cap, 20.0)

    def test_11_same_day_summary_forbidden(self):
        """当日盘后摘要即使已存在（回补/复跑）也不得用于盘中决策"""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261105", _summary("20261105", eff=60.0, hard=60))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.status, "NO_ANCHOR")
            self.assertFalse(a.usable)
            self.assertIsNone(a.basis_date)

    def test_12_picks_latest_valid(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261030", _summary("20261030", eff=5.0, hard=5))
            _write(d, "20261103", _summary("20261103", eff=20.0, hard=20))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.basis_date, "20261103")
            self.assertEqual(a.anchor_cap, 20.0)

    def test_13_stale_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20260101")
            a = ipa.load_anchor("20261105", _cfg(max_age_days=7), report_dir=d)
            self.assertEqual(a.status, "STALE")
            self.assertFalse(a.usable)

    def test_14_no_file_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.status, "NO_ANCHOR")
            self.assertEqual(ipa.apply_anchor_cap(60, a)[0], 60)
            self.assertIn("不可用", "\n".join(ipa.render_anchor_lines(a, 60, 60)))

    def test_15_bad_json_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", "{not json")
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.status, "BAD_JSON")
            self.assertFalse(a.usable)

    def test_16_missing_cap_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", {"trade_date": "20261103",
                                   "市场状态": "S3", "建议仓位": "无"})
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.status, "NO_CAP")
            self.assertFalse(a.usable)


class TestLayerPriority(unittest.TestCase):

    def test_20_bull_latch_wins(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", _summary(eff=20.0, bull=True, extra={
                "主升段锁存": {"enabled": True, "effective_target": 90.0,
                           "effective_target_exact": 90.0,
                           "authorized_hard_cap": 100}}))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.source, "bull_latch.effective_target")
            self.assertEqual(a.effective_target, 90.0)

    def test_21_swing_gate_exact_wins(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", _summary(eff=13.7, hard=20))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.source, "swing_gate.effective_target")
            self.assertAlmostEqual(a.effective_target, 13.7)
            self.assertAlmostEqual(a.anchor_cap, 13.7)

    def test_22_optimizer_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", _summary(eff=20.0, swing=False, extra={
                "P1P2仓位优化": {"enabled": True, "target_after": 35}}))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.source, "P1P2.target_after")
            self.assertEqual(a.effective_target, 35.0)

    def test_23_range_upper_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", _summary(eff=20.0, swing=False, opt=False,
                                           extra={"建议仓位": "8%~30%",
                                                  "仓位硬上限": 40}))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertIn("建议仓位上界", a.source)
            self.assertEqual(a.anchor_cap, 30.0)

    def test_24_layers_reported(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103")
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.layers, {"p0": True, "optimizer": True,
                                        "swing_gate": True, "bull_latch": False})
            self.assertEqual(a.state, "S4 卖压衰减")


class TestNoAuthorityInheritance(unittest.TestCase):

    def test_30_authorized_hard_cap_not_used(self):
        """盘后授权突破到 100% 也不得跨日继承；状态硬上限 20% 仍是锚点"""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", _summary(eff=90.0, hard=20, bull=True,
                                           bull_cap=100))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertEqual(a.hard_cap, 20.0)
            self.assertEqual(a.anchor_cap, 20.0)
            self.assertTrue(any("不继承跨日满仓授权" in n for n in a.notes))

    def test_31_no_recalc_note(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "20261103", _summary(bull=True))
            a = ipa.load_anchor("20261105", _cfg(), report_dir=d)
            self.assertTrue(any("不重新触发主升段锁存" in n for n in a.notes))
            self.assertTrue(any("不继承跨日满仓授权" in n for n in a.notes))
        src = inspect.getsource(ipa)
        self.assertIn("不跨日继承满仓授权", src)
        self.assertIn("d0 < td", src)   # 严格早于当日


class TestPureTightening(unittest.TestCase):

    def _anchor(self, cap=20.0):
        return ipa.PositionAnchor(trade_date="20261105", status="OK",
                                  basis_date="20261103", anchor_cap=cap,
                                  effective_target=cap, hard_cap=20.0,
                                  source="test", layers={},
                                  notes=["口径来源：test"])

    def test_40_monotone(self):
        for before in (5, 10, 15, 20, 25, 30, 40, 60, 100):
            for anchor_cap in (5.0, 13.7, 20.0, 47.3, 100.0):
                a = self._anchor(anchor_cap)
                new, note = ipa.apply_anchor_cap(before, a)
                self.assertLessEqual(new, before, (before, anchor_cap, note))
                self.assertLessEqual(new, anchor_cap + 1e-9,
                                    (before, anchor_cap, note))
                self.assertEqual(new % 5, 0)
                self.assertGreaterEqual(new, 5)

    def test_41_tighten_example(self):
        a = self._anchor(13.7)
        new, note = ipa.apply_anchor_cap(60, a)
        self.assertEqual(new, 10)
        self.assertIn("收紧", note)

    def test_42_round_never_overshoots(self):
        # 18.0 → round(3.6)*5 = 20 > 18，必须回落到 15
        new, _ = ipa.apply_anchor_cap(60, self._anchor(18.0))
        self.assertLessEqual(new, 18.0)

    def test_43_unusable_keeps_cap(self):
        a = ipa.PositionAnchor(trade_date="20261105", status="NO_ANCHOR")
        for before in (5, 20, 60, 100):
            self.assertEqual(ipa.apply_anchor_cap(before, a)[0], before)


class TestDictViewAndRender(unittest.TestCase):

    def test_50_as_dict_roundtrip(self):
        a = ipa.load_anchor("20261105", _cfg())
        if not a.usable:
            self.skipTest("仓库内无有效盘后摘要（CI 环境）")
        d = a.as_dict()
        obj_new, note1 = ipa.apply_anchor_cap(60, a)
        dict_new, note2 = ipa.apply_anchor_cap(60, d)
        self.assertEqual(obj_new, dict_new)
        self.assertEqual(note1, note2)
        L1 = ipa.render_anchor_lines(a, 60, obj_new, note1)
        L2 = ipa.render_anchor_lines(d, 60, dict_new, note2)
        self.assertEqual(L1, L2)

    def test_51_render_ok(self):
        a = ipa.PositionAnchor(trade_date="20261105", status="OK",
                               basis_date="20261103", anchor_cap=20.0,
                               effective_target=20.0, hard_cap=20.0,
                               state="S4", source="swing_gate.effective_target",
                               layers={"swing_gate": True, "bull_latch": False},
                               notes=["不继承跨日满仓授权"])
        L = "\n".join(ipa.render_anchor_lines(a, 40, 20, "由 40% 收紧到 20%"))
        self.assertIn("20261103", L)
        self.assertIn("严格早于", L)
        self.assertIn("已收紧", L)
        self.assertIn("波段闸门 ON", L)
        self.assertIn("主升段锁存 OFF", L)
        self.assertIn("不继承跨日满仓授权", L)

    def test_52_render_unusable(self):
        a = ipa.PositionAnchor(trade_date="20261105", status="STALE")
        L = "\n".join(ipa.render_anchor_lines(a, 20, 20))
        self.assertIn("不可用", L)

    def test_53_render_none(self):
        self.assertIn("未加载", "\n".join(ipa.render_anchor_lines(None, 20, 20)))


class TestIntradayWiring(unittest.TestCase):

    def test_60_module_imported_and_called(self):
        src = inspect.getsource(M)
        self.assertIn("import intraday_position_anchor as ipa", src)
        self.assertIn("ipa.load_anchor", src)
        self.assertIn("ipa.apply_anchor_cap", src)
        self.assertIn("ipa.render_anchor_lines", src)

    def test_61_discount_is_clamped(self):
        """时点折扣的四舍五入不得放大仓位"""
        src = inspect.getsource(M)
        self.assertIn("cap = min(cap, _pre_discount)", src)

    def test_62_anchor_uses_v107_config(self):
        """必须读 config_v107_refine.json（v101.load_config 不含 intraday_bridge）"""
        src = inspect.getsource(M)
        self.assertIn("market_regime_v107 as _v107mod", src)
        self.assertIn("_v107mod.load_config()", src)

    def test_63_try_except_guard(self):
        src = inspect.getsource(M)
        self.assertIn("except Exception as e:\n        anchor_note = ", src)

    def test_64_v10_v101_untouched(self):
        self.assertEqual(_digest("market_regime_v10.py"), V10_SHA256)
        self.assertEqual(_digest("market_regime_v101.py"), V101_SHA256)


if __name__ == "__main__":
    unittest.main(verbosity=2)
