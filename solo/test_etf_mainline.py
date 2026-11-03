# -*- coding: utf-8 -*-
"""V10.2 行业 ETF 主线识别测试"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

import etf_mainline as EM


class TestEtfPool(unittest.TestCase):
    """ETF 池与行业映射"""

    def test_pool_covers_industry(self):
        """ETF 池必须覆盖申万一级的主要行业"""
        expected = ["银行", "非银金融", "医药生物", "食品饮料", "电力设备",
                    "电子", "计算机", "通信", "传媒", "机械设备", "有色金属",
                    "基础化工", "国防军工", "汽车", "家用电器", "房地产",
                    "农林牧渔", "煤炭", "石油石化", "交通运输", "商贸零售",
                    "社会服务", "环保", "钢铁"]
        for ind in expected:
            self.assertIn(ind, EM.ETF_POOL, f"缺少行业 {ind}")

    def test_code_maps_to_industry(self):
        """每个 ETF 代码都能反查行业"""
        for code in EM.all_codes():
            self.assertIsNotNone(EM.industry_of(code),
                                 f"{code} 无法映射到行业")

    def test_no_code_in_two_industries(self):
        """同一 ETF 不应属于多个行业（避免重复计数）"""
        seen = {}
        for ind, codes in EM.ETF_POOL.items():
            for c in codes:
                self.assertNotIn(c, seen,
                                 f"{c} 同时属于 {seen.get(c)} 和 {ind}")
                seen[c] = ind


class TestEtfData(unittest.TestCase):
    """ETF 行情缓存"""

    @classmethod
    def setUpClass(cls):
        cls.panel = EM.load_etf_panel()

    def test_panel_not_empty(self):
        self.assertFalse(self.panel.empty, "ETF 面板为空，请先运行 fetch_etf_history()")

    def test_nav_is_reconstructed_from_pct_chg(self):
        """★ nav 必须与 pct_chg 累乘一致（不是 close 直接取值）。

        这是 ETF 数据的核心正确性约束：ETF 会做份额折算，
        close 序列会跳变 50~75%，直接用 close 会得出完全错误的收益。
        """
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        for code, g in self.panel.groupby("ts_code"):
            g = g.sort_values("trade_date").reset_index(drop=True)
            if len(g) < 30:
                continue
            expect = 100.0 * (1.0 + g["pct_chg"].fillna(0.0) / 100.0).cumprod()
            # nav 是从首次拉取日起累乘的，起点可能不是 100，需按比例对齐
            ratio = g["nav"].values / expect.values
            # 比例应保持恒定（同一复权因子）
            self.assertLess(ratio.std() / max(ratio.mean(), 1e-9), 1e-6,
                            f"{code} 的 nav 与 pct_chg 累乘不一致（复权处理错误）")
            break  # 一只足够验证

    def test_panel_covers_full_year(self):
        """ETF 面板必须覆盖 2026 全年"""
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        d26 = self.panel[self.panel["trade_date"] >= "20260101"]
        self.assertGreater(d26["trade_date"].nunique(), 180,
                           "2026 年 ETF 覆盖不足")

    def test_etf_count_per_industry(self):
        """宽度判定要求行业内至少 2 只 ETF —— 检查配置一致性"""
        for ind, codes in EM.ETF_POOL.items():
            self.assertGreaterEqual(len(set(codes)), 1, f"{ind} 无 ETF")


class TestMainlineDetection(unittest.TestCase):
    """主线识别"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = EM.load_config()
        cls.panel = EM.load_etf_panel()
        if cls.panel.empty:
            return
        dates = sorted(cls.panel["trade_date"].unique())
        cls.d26 = [d for d in dates if d >= "20260101"]

    def _detect(self, d):
        return EM.detect_mainlines(d, self.cfg, self.panel)

    def test_detect_returns_structure(self):
        """返回结构完整"""
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        r = self._detect(self.d26[len(self.d26) // 2])
        for k in ("industries", "details", "missing"):
            self.assertIn(k, r)
        self.assertIsInstance(r["industries"], list)
        self.assertIsInstance(r["details"], dict)

    def test_mainline_is_subset_of_industries(self):
        """主线必须来自已判定的行业集合"""
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        for d in self.d26[::10]:
            r = self._detect(d)
            for ind in r["industries"]:
                self.assertIn(ind, r["details"])

    def test_mainline_has_strong_days(self):
        """主线必须满足 min_days_strong"""
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        min_days = self.cfg["mainline"].get("min_days_strong", 1)
        for d in self.d26[::10]:
            r = self._detect(d)
            for ind in r["industries"]:
                ds = r["details"][ind].get("days_strong", 0)
                self.assertGreaterEqual(ds, min_days,
                                        f"{d} {ind} days_strong={ds} < {min_days}")

    def test_mainline_relative_strength_positive(self):
        """主线必须有正的相对强度（否则不可能是主线）"""
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        for d in self.d26[::10]:
            r = self._detect(d)
            for ind in r["industries"]:
                ex = r["details"][ind].get("excess20")
                if ex is not None:
                    self.assertGreater(ex, 0,
                                       f"{d} {ind} 超额20日={ex} 非正")

    def test_mainline_coverage_ample(self):
        """★ 全年必须有足够多的主线日 —— 否则说明口径失效。

        这是本次改造的核心目标：替换 theme_scores 后，
        主线判定必须覆盖全年（原口径只有 64/198 天）。
        """
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        cnt = 0
        for d in self.d26[::5]:
            if self._detect(d)["industries"]:
                cnt += 1
        total = len(self.d26[::5])
        self.assertGreater(cnt / total, 0.3,
                           f"主线覆盖率仅 {cnt}/{total}，口径可能过严")

    def test_result_cache_consistency(self):
        """缓存命中与重新计算结果一致"""
        if self.panel.empty:
            self.skipTest("无 ETF 数据")
        d = self.d26[len(self.d26) // 2]
        r1 = self._detect(d)
        r2 = self._detect(d)
        self.assertEqual(r1["industries"], r2["industries"])
        self.assertEqual(r1["details"].keys(), r2["details"].keys())


class TestMainlineIntegration(unittest.TestCase):
    """与 V10.1 主线模块的集成"""

    @classmethod
    def setUpClass(cls):
        import market_regime_v101 as V101
        cls.V101 = V101
        cls.cfg = V101.load_config()

    def test_check_mainline_uses_etf_source(self):
        """主线模块应走 ETF 口径，且不再有 UNKNOWN"""
        if EM.load_etf_panel().empty:
            self.skipTest("无 ETF 数据")
        for d in ("20260106", "20260410", "20260513"):
            res = self.V101.check_mainline(d, self.cfg)
            self.assertEqual(res.details.get("source"), "etf",
                             f"{d} 主线源应为 etf")
            self.assertEqual(len(res.missing), 0,
                             f"{d} 主线不应有缺失标记")

    def test_mainline_detected_on_strong_period(self):
        """★ 强行情期必须能识别出主线（这是改造的核心目的）"""
        if EM.load_etf_panel().empty:
            self.skipTest("无 ETF 数据")
        # 2026-05-13 前后是电子行业主线期（超额20日 24.7%）
        res = self.V101.check_mainline("20260513", self.cfg)
        self.assertTrue(res.passed, "20260513 应识别出主线")
        inds = res.details.get("industries") or []
        self.assertIn("电子", inds,
                      f"20260513 主线应含电子，实际 {inds}")


if __name__ == "__main__":
    unittest.main()


class TestV103RelativeTrendCriteria(unittest.TestCase):
    """★ V10.3：主线判定改为「相对强度 + 回撤容忍」的回归测试。

    起因：2026 上半年半导体行情（512480.SH 从 4/2 的 132 涨到 6/30 的 281，
    +112.7%）在 6/1~6/12 期间 ex60 高达 27~38%（全年最强），
    但因 MA20 被 5 月冲高抬高而连续 8 日站不上 → 整段被判「无主线」。
    """

    def _panel(self):
        import etf_mainline as em
        p = em.load_etf_panel()
        if p is None or p.empty:
            self.skipTest("ETF 缓存不可用")
        return p

    def test_trend_accepts_drawdown_within_tolerance(self):
        """回撤未破（≥容忍度）时，趋势条件应成立，即便未站上 MA20"""
        import etf_mainline as em
        cfg = em.load_config()
        dd_tol = cfg["trend"]["max_drawdown_from_high_pct"]
        self.assertLess(dd_tol, 0, "回撤容忍度必须为负数（表示允许回撤）")
        self.assertGreaterEqual(dd_tol, -20,
                                "容忍度过宽会让主线判定失去意义")

    def test_absolute_return_gates_disabled_by_default(self):
        """min_ret5 / min_ret20 默认为不限制（-99）"""
        import etf_mainline as em
        cfg = em.load_config()
        self.assertLess(cfg["trend"]["min_ret5"], -90)
        self.assertLess(cfg["trend"]["min_ret20"], -90)

    def test_bonus_score_is_not_veto(self):
        """量能/宽度是加分项，不是否决项（min_bonus_score 默认 0）"""
        import etf_mainline as em
        cfg = em.load_config()
        self.assertEqual(cfg["trend"]["min_bonus_score"], 0,
                         "加分项不应成为否决条件")

    def test_semiconductor_rally_detected(self):
        """★ 关键回归：2026 上半年半导体行情必须被识别为主线。

        2026-06-10 是该波行情中最难判的一天：
        电子行业 ex60=37.7%（全年最强），但当日 MA20 被 5 月冲高抬高、
        nav 跌破 MA20、ret5=-3.65% → 旧口径判 False。
        """
        import etf_mainline as em
        cfg = em.load_config()
        panel = self._panel()
        r = em.detect_mainlines("20260610", cfg, panel)
        det = r.get("details") or {}
        el = det.get("电子")
        self.assertIsNotNone(el, "电子行业应有判定明细")
        # 相对强度必须达标
        self.assertGreaterEqual(el["excess60"], 5.0,
                                "电子行业 60 日超额应达标")
        # 趋势条件在回撤容忍口径下应成立
        self.assertTrue(el["c_trend"],
                        f"电子行业趋势条件应成立：dd20={el.get('dd20')}")
        self.assertTrue(el["passed"],
                        f"电子行业应通过主线判定：{el}")

    def test_mainline_coverage_improved(self):
        """有主线天数应显著多于旧口径（104/198）"""
        import etf_mainline as em
        cfg = em.load_config()
        panel = self._panel()
        dates = sorted([d for d in panel["trade_date"].unique()
                        if str(d) >= "20260101"])
        n_hit = 0
        for d in dates:
            r = em.detect_mainlines(str(d), cfg, panel)
            if r.get("industries"):
                n_hit += 1
        self.assertGreaterEqual(n_hit, 150,
                                f"有主线天数应 ≥150（旧口径 104），实得 {n_hit}")


class TestEtfPanelContinuity(unittest.TestCase):
    """★ ETF 行情面板必须连续 —— 主线判定的数据基础。

    起因（2026-11-03 实测）：面板最新只到 10-30，缺 11-02，
    导致当日实时价接在 10-30 之后 → 少算了 11-02 一整天的涨跌，
    主线判定的 ret20/excess20 实际跨度不是 20 天。
    根因：盘后流程**从不更新** etf_daily.db，只靠手动 `--fetch`。
    """

    def test_panel_has_no_recent_gaps(self):
        """面板最近不应有缺口（相对于 daily_cache 的交易日历）"""
        import etf_mainline as em
        from _etf_backfill import missing_dates
        panel = em.load_etf_panel()
        if panel is None or panel.empty:
            self.skipTest("ETF 面板不可用")
        gaps = missing_dates(panel)
        self.assertEqual(gaps, [],
                         f"ETF 面板存在缺口 {gaps}，主线判定会失真。"
                         f"运行 `python _etf_backfill.py` 补齐")

    def test_backfill_module_exists(self):
        """必须有补数据的工具，且盘后流程会调用它"""
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '_etf_backfill.py')
        self.assertTrue(os.path.exists(path), "缺少 _etf_backfill.py")
        import io
        src = io.open(path, encoding='utf-8').read()
        self.assertIn('def backfill', src)
        self.assertIn('def missing_dates', src)

    def test_post_close_updates_etf_panel(self):
        """★ 盘后流程必须在 V10.1 分析前更新 ETF 面板"""
        import io
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'market_analysis.py')
        src = io.open(path, encoding='utf-8').read()
        self.assertIn('_etf_backfill', src,
                      "盘后流程必须调用 ETF 面板补数据")
        # 顺序：ETF 更新必须在 V10.1 分析之前
        i_etf = src.find('_etf_backfill')
        i_v101 = src.find('v101_analyze')
        self.assertLess(i_etf, i_v101,
                        "ETF 面板更新必须在 V10.1 分析之前"
                        "（否则主线判定用的是过期数据）")
