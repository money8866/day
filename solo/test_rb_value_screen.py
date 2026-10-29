#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
rb_value_screen 单元测试（交付项第 8 项）
════════════════════════════════════════════════════════════

覆盖用户第十一节里「可被单测固定」的几项：
  1. 摆动低点识别无未来函数（前缀稳定性）
  2. 重复低点去重（min_gap）
  3. 无有效反弹的低点被排除（min_rebound）
  4. 财务报告只取 ann_date <= 判断日（防数据泄漏）
  5. 累计值 → 单季值转换
  6. scale / rev_scale 边界
  7. 风险收益比与「目标位待确认」
  8. 主标签 / 交易状态的门槛优先级（含 CORE_WATCH ≠ BUY）

运行：
  python -m unittest -v test_rb_value_screen
  python test_rb_value_screen.py
"""
import os
import sys
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rb_value_screen as rb  # noqa: E402


def _frame(low, high=None, start="20250101"):
    n = len(np.asarray(low))
    dates = pd.bdate_range(start=start, periods=n).strftime("%Y%m%d").tolist()
    low = np.asarray(low, dtype=float)
    high = np.asarray(high, dtype=float) if high is not None else low * 1.10
    return pd.DataFrame({"trade_date": dates, "low": low, "high": high})


class TestSwingLows(unittest.TestCase):
    """B1 有效底部识别：无未来函数、去重、最小反弹。"""

    def setUp(self):
        self.p = dict(rb.P_PATTERN)

    def test_prefix_stability_no_lookahead(self):
        """只用截至当日的数据：把序列截短后识别出的低点，必须原样出现在长序列结果里。"""
        rng = np.random.default_rng(7)
        n = 260
        i = np.arange(n)
        close = 10 + 2.0 * np.sin(i / 14.0) + np.cumsum(rng.normal(0, 0.004, n))
        close = np.maximum(close, 1.0)
        g = _frame(close * 0.99, close * 1.01)

        full = rb.find_swing_lows(g, self.p)
        self.assertTrue(full, "随机序列应至少识别出一个摆动低点")
        for cut in (8, 20, 40):
            part = rb.find_swing_lows(g.iloc[:-cut].reset_index(drop=True), self.p)
            fmap = {(c["idx"], round(c["price"], 6)) for c in full}
            for c in part:
                self.assertIn((c["idx"], round(c["price"], 6)), fmap,
                              "截短 %d 根后出现的低点 %s 在长序列中不存在 → 存在未来函数" % (cut, c))

    def test_confirm_index_within_available_bars(self):
        """任何低点的确认时刻都不得超过最后一根 K 线（不能用未来 K 线确认）。"""
        rng = np.random.default_rng(11)
        close = 10 + np.cumsum(rng.normal(0, 0.015, 200))
        g = _frame(np.maximum(close, 1.0) * 0.99, np.maximum(close, 1.0) * 1.01)
        lows = rb.find_swing_lows(g, self.p)
        for c in lows:
            self.assertLessEqual(c["confirm_idx"], len(g) - 1)
            self.assertLessEqual(c["idx"] + self.p["pivot_k"] + self.p["confirm_lag"], len(g) - 1)

    def test_duplicate_lows_deduped(self):
        """间隔小于 min_gap 的两个低点只保留更低的那个。"""
        low = np.full(60, 10.0)
        high = np.full(60, 10.0)
        low[20], high[20] = 9.00, 10.0
        low[27], high[27] = 8.80, 9.60
        g = _frame(low, high)
        lows = rb.find_swing_lows(g, self.p)
        self.assertEqual(len(lows), 1, "相邻 7 根的两个低点应被去重为一个")
        self.assertEqual(lows[0]["idx"], 27)

    def test_min_rebound_filters_tiny_swing(self):
        """低点后反弹不足 min_rebound 的微小波动不计为有效底部。"""
        low = np.full(60, 10.0)
        low[20] = 9.0
        g_weak = _frame(low, np.full(60, 9.2))            # 之后最高仅 +2%
        self.assertEqual(rb.find_swing_lows(g_weak, self.p), [])

        high = np.full(60, 10.0)                          # 之后最高 +11%
        g_ok = _frame(low, high)
        lows = rb.find_swing_lows(g_ok, self.p)
        self.assertEqual(len(lows), 1)
        self.assertGreaterEqual(lows[0]["rebound"], self.p["min_rebound"])


class TestFinancials(unittest.TestCase):
    """财务序列加工与公告日过滤。"""

    def test_single_quarter_conversion(self):
        """累计营收 → 单季营收，且跨年重置。"""
        df = pd.DataFrame({
            "end_date": ["20250331", "20250630", "20250930", "20251231", "20260331"],
            "revenue": [100.0, 220.0, 340.0, 480.0, 120.0],
        })
        out = rb._single_quarter(df, ["revenue"])
        got = [round(x, 4) for x in out["revenue_q"].tolist()]
        self.assertEqual(got, [100.0, 120.0, 120.0, 140.0, 120.0])

    def test_ann_date_filter_blocks_future_reports(self):
        """公告日晚于判断日的报告必须被丢弃（防未来数据泄漏）。"""
        tmp = tempfile.mkdtemp(prefix="rbv_pq_")
        old = rb.PARQUET_DIR
        try:
            rb.PARQUET_DIR = tmp
            inc = pd.DataFrame({
                "ts_code": ["TST001.SZ", "TST001.SZ", "TST001.SZ"],
                "end_date": ["20250331", "20250630", "20250930"],
                # 0630 在判断日之后才公告 → 不可用；0930 无公告日 → 必须丢弃
                "ann_date": ["20250425", "20260820", None],
                "report_type": [1, 1, 1],
                "revenue": [100.0, 230.0, 380.0],
                "n_income_attr_p": [10.0, 25.0, 40.0],
            })
            inc.to_parquet(os.path.join(tmp, "income_TST001.SZ_2025.parquet"), index=False)

            fin = rb.load_financials("TST001.SZ", "20260630", {})
            self.assertIsNotNone(fin["income"])
            self.assertEqual(fin["income"]["end_date"].tolist(), ["20250331"])
            self.assertTrue(any("现金流量表" in g for g in fin["gaps"]))

            # 判断日推进到 20260901 后，0630 变为可用
            fin2 = rb.load_financials("TST001.SZ", "20260901", {})
            self.assertEqual(fin2["income"]["end_date"].tolist(), ["20250331", "20250630"])
        finally:
            rb.PARQUET_DIR = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestScoringHelpers(unittest.TestCase):
    """评分辅助函数与风险收益比。"""

    def test_scale_bounds(self):
        self.assertEqual(rb.scale(None, 0, 10, 5), 0.0)
        self.assertEqual(rb.scale(0, 0, 10, 5), 0.0)
        self.assertEqual(rb.scale(10, 0, 10, 5), 5.0)
        self.assertEqual(rb.scale(99, 0, 10, 5), 5.0)
        self.assertEqual(rb.scale(-99, 0, 10, 5), 0.0)

    def test_rev_scale_bounds(self):
        self.assertEqual(rb.rev_scale(None, 0, 10, 4), 0.0)
        self.assertEqual(rb.rev_scale(0, 0, 10, 4), 4.0)
        self.assertEqual(rb.rev_scale(10, 0, 10, 4), 0.0)
        self.assertEqual(rb.rev_scale(20, 0, 10, 4), 0.0)

    def test_entry_rr_rejects_bad_payoff(self):
        """潜在收益不足覆盖风险时，目标价必须置空并标注待确认。"""
        m = {"close": 10.0, "support_price": 9.5, "invalidation_price": 9.2,
             "platform_high": 10.05, "atr": 0.3, "state": "底部抬高但未突破",
             "ma20_slope": 2.0, "bias20": 8.0, "shrink_ratio": 0.9, "ma20": 9.9,
             "platform_days": 30, "dist_platform": 0.5,
             "ma60": 9.4, "dist_ma60": 6.4, "amt20": 1e5}
        e = rb.analyze_entry(m)
        self.assertEqual(e["setup"], "BREAKOUT")
        self.assertIsNone(e["target"])
        self.assertIsNone(e["rr"])
        self.assertIn("待确认", e["target_gap"])

    def test_entry_rr_kept_when_payoff_ok(self):
        m = {"close": 11.0, "support_price": 9.0, "invalidation_price": 9.6,
             "platform_high": 11.0, "atr": 0.3, "state": "底部抬高但未突破",
             "ma20_slope": 2.0, "bias20": 8.0, "shrink_ratio": 0.9, "ma20": 10.8,
             "platform_days": 30, "dist_platform": 0.5,
             "ma60": 10.0, "dist_ma60": 10.0, "amt20": 1e5}
        e = rb.analyze_entry(m)
        self.assertIsNotNone(e["target"])
        self.assertIsNotNone(e["rr"])
        self.assertGreater(e["rr"], 1.0)

    def test_rebreakout_target_from_structure_not_risk_multiple(self):
        """二次启动的目标必须来自「平台上沿 + 平台高度」；空间不足时置空。

        禁止用「现价 + 2×风险距离」反推目标 —— 那样 RR 恒等于 2，属于自证。
        """
        m = {"close": 117.20, "support_price": 89.00, "invalidation_price": 82.77,
             "platform_high": 104.98, "atr": 5.0, "state": "突破后回踩确认",
             "ma20_slope": 12.0, "bias20": 15.0, "shrink_ratio": 0.9, "ma20": 101.7,
             "platform_days": 30, "dist_platform": 12.0,
             "ma60": 90.3, "dist_ma60": 29.7, "amt20": 1e6}
        e = rb.analyze_entry(m)
        self.assertEqual(e["setup"], "REBREAKOUT")
        # 结构目标 = 104.98 + (104.98 - 89.00) = 120.96，相对入场仅 +3%，覆盖不了风险 → 置空
        self.assertIsNone(e["target"])
        self.assertIsNone(e["rr"])


class TestDecisionGates(unittest.TestCase):
    """主标签与交易状态：综合分不得越过任何一道门槛。"""

    def _row(self, **kw):
        r = {"_is_st": False, "_bars": 400, "_amt20": 1e5, "_loss_years": 0,
             "_ocf_neg": False, "_debt": 50.0, "_pe": 20.0, "_pe_pct": 0.40,
             "_pb_pct": 0.40, "_np_yoy": 20.0, "data_completeness": 0.85,
             "stock_quality": 70.0, "pattern_quality": 70.0,
             "pattern_state": "底部抬高但未突破", "entry_quality": 70.0,
             "risk_reward": 2.5, "_setup": "PULLBACK", "_setup_confirm": 0.80}
        r.update(kw)
        return r

    def test_hard_risk_excluded(self):
        self.assertEqual(rb.decide(self._row(_is_st=True))[0:2], ("RISK_EXCLUDE", "NO_TRADE"))
        self.assertEqual(rb.decide(self._row(_bars=120))[0:2], ("RISK_EXCLUDE", "NO_TRADE"))
        self.assertEqual(rb.decide(self._row(_amt20=1e3))[0:2], ("RISK_EXCLUDE", "NO_TRADE"))
        # 高负债 + 经营现金流为负 = 重大财务风险
        self.assertEqual(rb.decide(self._row(_debt=85.0, _ocf_neg=True))[0:2],
                         ("RISK_EXCLUDE", "NO_TRADE"))
        # 连续亏损 + 经营现金流为负
        self.assertEqual(rb.decide(self._row(_loss_years=2, _ocf_neg=True))[0:2],
                         ("RISK_EXCLUDE", "NO_TRADE"))

    def test_data_gap_forces_verify(self):
        label, status, _ = rb.decide(self._row(data_completeness=0.40))
        self.assertEqual((label, status), ("FUNDAMENTAL_VERIFY", "WAIT"))

    def test_low_quality_forces_verify(self):
        """形态/买点再好，股票质量不达标也只能先核验基本面。"""
        label, status, _ = rb.decide(self._row(stock_quality=40.0, entry_quality=80.0))
        self.assertEqual((label, status), ("FUNDAMENTAL_VERIFY", "WAIT"))

    def test_pattern_weak(self):
        self.assertEqual(rb.decide(self._row(pattern_quality=50.0))[0], "PATTERN_WEAK")
        self.assertEqual(rb.decide(self._row(pattern_state="突破失败"))[0], "PATTERN_WEAK")
        self.assertEqual(rb.decide(self._row(pattern_state="跌破失效位"))[1], "NO_TRADE")

    def test_valuation_risk(self):
        self.assertEqual(rb.decide(self._row(_pe_pct=0.95))[0], "VALUATION_RISK")
        self.assertEqual(rb.decide(self._row(_pe=None, _pe_pct=None, _pb_pct=0.90))[0],
                         "VALUATION_RISK")
        self.assertEqual(rb.decide(self._row(_pe=100.0, _np_yoy=10.0))[0], "VALUATION_RISK")

    def test_buy_requires_all_conditions(self):
        self.assertEqual(rb.decide(self._row())[0:2], ("PULLBACK_WATCH", "BUY"))
        # 买点分不足 → 只能等待
        self.assertEqual(rb.decide(self._row(entry_quality=60.0))[1], "WAIT")
        # 风险收益比不足 → 只能等待
        self.assertEqual(rb.decide(self._row(risk_reward=1.2))[1], "WAIT")
        # 完整度够但不达 BUY 门槛 → 只能等待
        self.assertEqual(rb.decide(self._row(data_completeness=0.70))[1], "WAIT")
        # 确认度不足 → 只能等待
        self.assertEqual(rb.decide(self._row(_setup_confirm=0.50))[1], "WAIT")

    def test_core_watch_is_not_buy(self):
        """没有合格买点时输出 CORE_WATCH，但绝不能是 BUY。"""
        label, status, _ = rb.decide(self._row(_setup="NONE", entry_quality=40.0))
        self.assertEqual((label, status), ("CORE_WATCH", "WAIT"))

    def test_setup_labels(self):
        self.assertEqual(rb.decide(self._row(_setup="BREAKOUT"))[0], "BREAKOUT_WATCH")
        self.assertEqual(rb.decide(self._row(_setup="REBREAKOUT", entry_quality=60.0))[0],
                         "REBREAKOUT_WATCH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
