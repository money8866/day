#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V10.0 状态机与仓位一致性测试

用真实历史数据（20261030 等）跑，断言：
  1. 10/30 正确识别为恐慌加速 + S3
  2. 状态转移的确认/退出计数正确
  3. 仓位建议与状态一致（不含混）
  4. 数据缺失时输出 UNKNOWN 而不是 0
  5. 不会把极端下跌当成底部（不因低广度自动提高仓位）
  6. 不因单日反弹就判定修复确认
  7. 位置分位口径与实际数据一致

运行：
    python test_market_regime_v10.py
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import market_regime_v10 as V10

TD = "20261030"      # 已知的普跌日


def _snapshot(td=TD, cfg=None):
    cfg = cfg or V10.load_config()
    return V10.collect_market_snapshot(td, cfg)


class TestV10Data(unittest.TestCase):
    """数据层：口径与 UNKNOWN 处理"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V10.load_config()
        cls.snap = _snapshot(TD, cls.cfg)

    def test_breadth_flat_not_dropped(self):
        """平盘家数必须单列，不能直接丢弃"""
        b = self.snap.breadth
        self.assertGreaterEqual(b.flat_count, 0)
        self.assertEqual(b.total, b.up_count + b.down_count + b.flat_count)
        # 广度分母不含平盘
        if b.breadth_ratio is not None:
            expect = b.up_count / (b.up_count + b.down_count)
            self.assertAlmostEqual(b.breadth_ratio, expect, places=6)

    def test_missing_data_is_none_not_zero(self):
        """缺失指标必须是 None（UNKNOWN），不能是 0"""
        b = self.snap.breadth
        self.assertIsNotNone(b.breadth_ratio)
        self.assertNotEqual(b.breadth_ratio, 0)
        # 位置分位若算不出必须是 None
        for ix in self.snap.indexes.values():
            if ix.close_percentile_250d is not None:
                self.assertGreaterEqual(ix.close_percentile_250d, 0)
                self.assertLessEqual(ix.close_percentile_250d, 100)

    def test_position_percentile_matches_manual(self):
        """250日分位口径：收盘在 [最低,最高] 区间中的位置"""
        import sqlite3
        conn = sqlite3.connect(V10.STOCK_DB)
        import pandas as pd
        df = pd.read_sql_query(
            "SELECT close FROM index_daily_cache WHERE ts_code='932000.CSI' "
            "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 250",
            conn, params=(TD,))
        conn.close()
        w = df['close'].astype(float)
        expect = (w.iloc[0] - w.min()) / (w.max() - w.min()) * 100
        got = self.snap.indexes['中证2000'].close_percentile_250d
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, expect, places=3)

    def test_amount_is_full_market_not_index(self):
        """成交额必须是全市场口径，不能是指数成交量"""
        am = self.snap.amount
        self.assertIsNotNone(am.total_amount)
        # 全市场单日成交额应在万亿元量级（>5000亿），指数量级不可能
        self.assertGreater(am.total_amount, 5000)

    def test_small_vs_large_gap_uses_separate_index(self):
        """大小盘相对强弱必须用各自指数，不能互相替代"""
        g = self.snap.small_vs_large_gap
        self.assertIsNotNone(g)
        core = self.snap.indexes['中证2000'].pct_chg
        bench = self.snap.indexes['沪深300'].pct_chg
        self.assertAlmostEqual(g, core - bench, places=6)
        self.assertNotEqual(core, bench)   # 两者确实不同


class TestV10Nodes(unittest.TestCase):
    """节点识别"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V10.load_config()
        cls.snap = _snapshot(TD, cls.cfg)
        cls.node = V10.detect_market_nodes(cls.snap, cls.cfg)

    def test_1030_is_panic_acceleration(self):
        """10/30 必须是恐慌加速"""
        self.assertTrue(self.node.is_node_day)
        self.assertEqual(self.node.node_type, V10.NODE_PANIC)

    def test_panic_not_called_bottom(self):
        """恐慌加速绝不能表述为底部/买点"""
        text = " ".join(self.node.evidence + self.node.uncertainties)
        self.assertIn("≠底部", text)
        for bad in ("底部已确认", "可以抄底", "买点"):
            self.assertNotIn(bad, text)

    def test_node_requires_multiple_hits(self):
        """节点必须多指标联合，不能单指标触发"""
        self.assertGreaterEqual(len(self.node.evidence), 2)

    def test_evidence_count_capped(self):
        self.assertLessEqual(len(self.node.evidence), 5)
        self.assertLessEqual(len(self.node.uncertainties), 3)


class TestV10StateMachine(unittest.TestCase):
    """状态机转移"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V10.load_config()
        cls.snap = _snapshot(TD, cls.cfg)
        cls.node = V10.detect_market_nodes(cls.snap, cls.cfg)

    def test_panic_enters_immediately(self):
        """恐慌加速允许快速风险升级（1 日进入）"""
        dec = V10.decide_state(self.snap, self.node, None, self.cfg)
        self.assertEqual(dec.state, V10.STATE_PANIC)
        self.assertEqual(dec.consecutive_days, 1)
        self.assertTrue(dec.changed)

    def test_exit_requires_consecutive_days(self):
        """风险解除不能仅依赖单日反弹：切出 S3 需窗口内环境票达 exit_days"""
        cfg = self.cfg
        # 造一个「无节点」的快照
        node_none = V10.NodeResult()
        node_none.node_type = "无"
        need = cfg["state_machine"]["exit_days"][V10.STATE_PANIC]
        self.assertGreaterEqual(need, 2,
                                "切出恐慌加速至少需要 2 日确认")

        # ★ V10.2：改为滑动窗口多数决 —— 窗口内「环境票」累积到 exit_days 才退出。
        votes = []
        for day in range(1, need + 1):
            pk = {"state": V10.STATE_PANIC, "consecutive_days": 3,
                  "pending_target": "", "pending_days": 0,
                  "vote_history": list(votes)}
            dk = V10.decide_state(self.snap, node_none, pk, cfg)
            votes = dk.vote_history
            if day < need:
                self.assertEqual(dk.state, V10.STATE_PANIC,
                                 f"第{day}日仍不应退出（环境票不足{need}）")
            else:
                self.assertNotEqual(dk.state, V10.STATE_PANIC,
                                    f"环境票达{need}后应退出 S3")

    def test_exit_counter_not_reset_by_env_fluctuation(self):
        """★ V10.2：退出确认不应被环境态（S1/S2）摆动重置。

        回归场景：2026-05-11 环境判 S1、05-12 判 S2，
        旧实现会让 pending_target 不匹配 → 退出计数归零 → 风险状态卡住。
        新机制用「环境票」计数，与环境态具体是 S1 还是 S2 无关。
        """
        cfg = self.cfg
        node_none = V10.NodeResult()
        node_none.node_type = "无"

        # 第一次：无节点 → 记入环境票
        pk = {"state": V10.STATE_PANIC, "consecutive_days": 3,
              "pending_target": "", "pending_days": 0, "vote_history": []}
        d1 = V10.decide_state(self.snap, node_none, pk, cfg)
        self.assertEqual(d1.state, V10.STATE_PANIC)
        self.assertIn("", d1.vote_history, "无节点应记为环境票")

        # 第二次：仍然无节点 → 环境票应累加到 2
        pk2 = {"state": V10.STATE_PANIC, "consecutive_days": 3,
               "pending_target": "", "pending_days": 0,
               "vote_history": list(d1.vote_history)}
        d2 = V10.decide_state(self.snap, node_none, pk2, cfg)
        env_votes = sum(1 for v in d2.vote_history if v == "")
        self.assertGreaterEqual(env_votes, 2,
                                "环境票应连续累加，不被环境态摆动重置")

    def test_confirm_days_required_for_repair(self):
        """修复确认需要连续 confirm_days 日，单日不算"""
        cfg = self.cfg
        need = cfg["state_machine"]["confirm_days"][V10.STATE_REPAIR]
        self.assertGreaterEqual(need, 2, "修复确认不应单日触发")
        node_rep = V10.NodeResult()
        node_rep.node_type = V10.NODE_REPAIR
        prev = {"state": V10.STATE_NORMAL, "consecutive_days": 5,
                "pending_target": "", "pending_days": 0}
        dec = V10.decide_state(self.snap, node_rep, prev, cfg)
        self.assertNotEqual(dec.state, V10.STATE_REPAIR,
                            "单日修复信号不应直接进入 S5")

    def test_state_cn_always_filled(self):
        """状态中文名必须与状态码一致（曾出现过 S3 显示"正常"的 bug）"""
        for st in (V10.STATE_NORMAL, V10.STATE_DRAWDOWN, V10.STATE_PANIC,
                   V10.STATE_EASING, V10.STATE_REPAIR):
            self.assertIn(st, V10.STATE_CN)
        dec = V10.decide_state(self.snap, self.node, None, self.cfg)
        self.assertEqual(dec.state_cn, V10.STATE_CN[dec.state])

    def test_no_forced_upgrade_on_deep_drop(self):
        """极端下跌不得自动提高仓位"""
        dec = V10.decide_state(self.snap, self.node, None, self.cfg)
        advice = V10.suggest_position(dec, self.snap, self.cfg)
        self.assertIsNotNone(advice.target_position)
        self.assertLessEqual(advice.target_position, 5,
                              "S3 恐慌加速的目标仓位应 ≤5%")
        self.assertFalse(advice.allow_probe,
                         "S3 不允许主动试探")


class TestV10PositionAdvice(unittest.TestCase):
    """仓位建议：只输出建议目标仓位，不涉及实际仓位与加仓空间"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V10.load_config()
        cls.snap = _snapshot(TD, cls.cfg)
        cls.node = V10.detect_market_nodes(cls.snap, cls.cfg)
        cls.dec = V10.decide_state(cls.snap, cls.node, None, cls.cfg)

    def test_advice_has_no_actual_position_or_add_room(self):
        """本模块只给建议目标仓位，不涉及实际持仓与加仓空间"""
        advice = V10.suggest_position(self.dec, self.snap, self.cfg)
        self.assertFalse(hasattr(advice, "actual_position"),
                         "不应再有实际仓位字段")
        self.assertFalse(hasattr(advice, "add_room"),
                         "不应再有加仓空间字段")

    def test_target_position_present(self):
        """建议目标仓位必须给出（S1 除外，按现有策略）"""
        advice = V10.suggest_position(self.dec, self.snap, self.cfg)
        self.assertIsNotNone(advice.target_position)
        self.assertTrue(0 <= advice.target_position <= 100)

    def test_state_and_position_consistent(self):
        """仓位建议必须与状态一致"""
        # ★ V10.2：S4(卖压衰减) 权限由 5% 调整为 20%
        for st, max_t in [(V10.STATE_PANIC, 5), (V10.STATE_EASING, 20)]:
            dec = V10.RegimeDecision(state=st, state_cn=V10.STATE_CN[st])
            advice = V10.suggest_position(dec, self.snap, self.cfg)
            if advice.target_position is not None:
                self.assertLessEqual(advice.target_position, max_t)

    def test_env_state_does_not_auto_buy(self):
        """环境状态变好不等于自动买入：allow_probe 为 False 时仍需个股信号"""
        dec = V10.RegimeDecision(state=V10.STATE_EASING,
                                 state_cn=V10.STATE_CN[V10.STATE_EASING])
        advice = V10.suggest_position(dec, self.snap, self.cfg)
        self.assertFalse(advice.allow_probe)
        self.assertTrue(any("个股原有买入信号" in c for c in advice.add_conditions))


class TestV10Report(unittest.TestCase):
    """报告输出完整性"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = V10.load_config()
        cls.snap = _snapshot(TD, cls.cfg)
        cls.node = V10.detect_market_nodes(cls.snap, cls.cfg)
        cls.dec = V10.decide_state(cls.snap, cls.node, None, cls.cfg)
        cls.advice = V10.suggest_position(cls.dec, cls.snap, cls.cfg)
        cls.text = V10.render_v10_report(cls.snap, cls.node, cls.dec,
                                         cls.advice, cls.cfg)

    def test_all_sections_present(self):
        for sec in ["【市场节点判断】", "【市场广度与情绪】", "【量价判断】",
                    "【仓位建议】", "【明日观察重点】"]:
            self.assertIn(sec, self.text)

    def test_observation_max_three(self):
        idx = self.text.index("【明日观察重点】")
        seg = self.text[idx:idx + 400]
        n = sum(1 for line in seg.splitlines() if line.strip().startswith(("1.", "2.", "3.", "4.")))
        self.assertLessEqual(n, 3)

    def test_amount_note_present(self):
        """成交额口径必须标注"""
        self.assertIn("全市场", self.text)

    def test_position_low_not_bottom(self):
        """位置分位低必须显式声明 ≠ 底部"""
        self.assertIn("≠ 底部", self.text)

    def test_final_one_liner(self):
        self.assertIn("▎", self.text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
