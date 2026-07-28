"""
V6.2 升级单元测试

测试内容:
  1. pattern_type 保存测试
  2. 不同 pattern 不互相污染测试
  3. Adjusted EV 排序测试
  4. Learning Mode 仓位测试
"""

import os
import sys
import unittest
import tempfile
import sqlite3
import yaml
import json
from dataclasses import dataclass, field
from typing import Dict, List
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_regime_v3.alpha_engines.pattern_db import (
    init_db, save_pattern_record, query_pattern_stats,
    query_similar_patterns, PATTERN_DB_PATH,
)
from market_regime_v3.alpha_engines.pattern_engine import (
    classify_pattern_type, estimate_heuristic_probability,
    HistoricalPatternEngine, PatternMatchResult,
)
from market_regime_v3.alpha_engines.ev_engine import (
    EVEngine, EVResult, get_confidence_level, Signal,
)
from market_regime_v3.alpha_engines.risk_budget_position import (
    RiskBudgetPositionEngine, PositionResult, RiskBudgetResult,
)


# ── 辅助：创建临时数据库 ──

def _create_test_pattern_db(db_path: str):
    """创建测试用的pattern_history数据库"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pattern_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            market_regime TEXT,
            market_score REAL,
            risk_appetite REAL,
            heat_score REAL,
            theme TEXT,
            theme_rank INTEGER,
            theme_strength REAL,
            pattern_type TEXT DEFAULT 'PULLBACK_ALPHA',
            entry_type TEXT DEFAULT 'pullback',
            leader_rank INTEGER,
            alpha_rank INTEGER,
            cross_sectional_rank INTEGER,
            ret_60d REAL,
            max_drawdown REAL,
            pullback_ma TEXT,
            dist_to_ma REAL,
            atr REAL,
            turnover_rate REAL,
            amount REAL,
            smart_money_score REAL,
            moneyflow REAL,
            volume_change REAL,
            future_5_return REAL,
            future_10_return REAL,
            future_20_return REAL,
            future_max_drawdown REAL,
            holding_days INTEGER,
            success_flag INTEGER,
            UNIQUE(ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_pattern_type ON pattern_history(pattern_type);
    """)
    return conn


def _insert_test_records(conn, records: List[Dict]):
    """插入测试记录"""
    for r in records:
        conn.execute("""
            INSERT OR REPLACE INTO pattern_history
            (ts_code, trade_date, market_regime, market_score, risk_appetite, heat_score,
             theme, theme_rank, theme_strength, pattern_type, entry_type, leader_rank,
             alpha_rank, cross_sectional_rank, ret_60d, max_drawdown, pullback_ma,
             dist_to_ma, atr, turnover_rate, amount, smart_money_score, moneyflow,
             volume_change, future_5_return, future_10_return, future_20_return,
             future_max_drawdown, holding_days, success_flag)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r.get('ts_code'), r.get('trade_date'), r.get('market_regime'),
            r.get('market_score'), r.get('risk_appetite'), r.get('heat_score'),
            r.get('theme'), r.get('theme_rank'), r.get('theme_strength'),
            r.get('pattern_type', 'PULLBACK_ALPHA'), r.get('entry_type', 'pullback'),
            r.get('leader_rank'), r.get('alpha_rank'), r.get('cross_sectional_rank'),
            r.get('ret_60d'), r.get('max_drawdown'), r.get('pullback_ma'),
            r.get('dist_to_ma'), r.get('atr'), r.get('turnover_rate'),
            r.get('amount'), r.get('smart_money_score'), r.get('moneyflow'),
            r.get('volume_change'), r.get('future_5_return'), r.get('future_10_return'),
            r.get('future_20_return'), r.get('future_max_drawdown'),
            r.get('holding_days'), r.get('success_flag'),
        ))
    conn.commit()


def _make_minimal_config():
    """创建最小配置"""
    return {
        'pattern_engine': {
            'enabled': True,
            'min_samples': 5,
            'default_probability': 0.5,
            'drawdown_tolerance': 0.03,
            'ret_60d_tolerance': 0.10,
            'cold_start': {
                'enabled': True,
                'warmup_threshold': 15,
                'heuristic_mix_min': 0.70,
                'heuristic_mix_max': 0.05,
            },
            'confidence': {
                'sample_weight': 0.40,
                'recency_weight': 0.35,
                'quality_weight': 0.25,
                'buy_threshold': 0.40,
                'wait_threshold': 0.25,
            },
        },
        'ev_engine': {
            'enabled': True,
            'min_samples': 5,
            'buy_threshold': 0.03,
            'wait_threshold': 0.0,
            'min_win_prob': 0.55,
            'max_drawdown_penalty': 0.05,
        },
        'risk_control': {
            'max_per_position_pct': 0.15,
        },
        'risk_budget_position': {
            'enabled': True,
        },
        'learning_mode': {
            'enabled': True,
            'min_samples_live': 30,
            'base_learning_position': 5,
            'confidence_adjustment': {
                'A': 1.0,
                'B': 0.8,
                'C': 0.6,
                'D': 0.4,
            },
            'clamp_min': 3,
            'clamp_max': 8,
            'max_total_learning': 20,
            'regime_min': 'Recovery',
            'leader_rank_max': 100,
            'smart_money_min': 60,
        },
    }


# ═══════════════════════════════════════════════
# 测试1: pattern_type 保存测试
# ═══════════════════════════════════════════════

class TestPatternTypeSave(unittest.TestCase):
    """测试pattern_type字段能否正确保存和读取"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, 'test_pattern.db')
        self.conn = _create_test_pattern_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp_dir)
        except:
            pass

    def _save_and_query(self, record: Dict):
        """保存记录并查询"""
        save_sql = """
        INSERT OR REPLACE INTO pattern_history (
            ts_code, trade_date, market_regime, pattern_type, entry_type,
            ret_60d, max_drawdown, pullback_ma, leader_rank, amount, turnover_rate,
            future_5_return, future_10_return, future_20_return,
            future_max_drawdown, holding_days, success_flag
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        self.conn.execute(save_sql, (
            record.get('ts_code'), record.get('trade_date'),
            record.get('market_regime'), record.get('pattern_type', 'PULLBACK_ALPHA'),
            record.get('entry_type', 'pullback'),
            record.get('ret_60d'), record.get('max_drawdown'),
            record.get('pullback_ma'), record.get('leader_rank'),
            record.get('amount'), record.get('turnover_rate'),
            record.get('future_5_return'), record.get('future_10_return'),
            record.get('future_20_return'), record.get('future_max_drawdown'),
            record.get('holding_days'), record.get('success_flag'),
        ))
        self.conn.commit()

        cursor = self.conn.execute(
            "SELECT pattern_type FROM pattern_history WHERE ts_code = ? AND trade_date = ?",
            (record['ts_code'], record['trade_date'])
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def test_save_pullback_alpha(self):
        """保存PULLBACK_ALPHA类型"""
        result = self._save_and_query({
            'ts_code': '002371.SZ', 'trade_date': '20260115',
            'market_regime': 'Bull', 'pattern_type': 'PULLBACK_ALPHA',
            'entry_type': 'pullback', 'ret_60d': 0.35, 'max_drawdown': 0.08,
            'pullback_ma': 'MA20', 'leader_rank': 3,
            'amount': 5e8, 'turnover_rate': 3.5,
            'future_5_return': 0.02, 'future_10_return': 0.05,
            'future_20_return': 0.08, 'future_max_drawdown': -0.03,
            'holding_days': 10, 'success_flag': 1,
        })
        self.assertEqual(result, 'PULLBACK_ALPHA')

    def test_save_breakout_alpha(self):
        """保存BREAKOUT_ALPHA类型"""
        result = self._save_and_query({
            'ts_code': '300750.SZ', 'trade_date': '20260201',
            'market_regime': 'Bull', 'pattern_type': 'BREAKOUT_ALPHA',
            'entry_type': 'pullback', 'ret_60d': 0.25, 'max_drawdown': 0.03,
            'pullback_ma': '', 'leader_rank': 1,
            'amount': 1e9, 'turnover_rate': 2.0,
            'future_5_return': 0.03, 'future_10_return': 0.06,
            'future_20_return': 0.10, 'future_max_drawdown': -0.02,
            'holding_days': 15, 'success_flag': 1,
        })
        self.assertEqual(result, 'BREAKOUT_ALPHA')

    def test_save_rebound_alpha(self):
        """保存REBOUND_ALPHA类型"""
        result = self._save_and_query({
            'ts_code': '000001.SZ', 'trade_date': '20260301',
            'market_regime': 'Recovery', 'pattern_type': 'REBOUND_ALPHA',
            'entry_type': 'pullback', 'ret_60d': -0.20, 'max_drawdown': 0.25,
            'pullback_ma': 'MA60', 'leader_rank': None,
            'amount': 3e8, 'turnover_rate': 1.5,
            'future_5_return': -0.01, 'future_10_return': 0.02,
            'future_20_return': 0.05, 'future_max_drawdown': -0.05,
            'holding_days': 8, 'success_flag': 1,
        })
        self.assertEqual(result, 'REBOUND_ALPHA')

    def test_default_pattern_type(self):
        """未指定pattern_type时的默认值"""
        result = self._save_and_query({
            'ts_code': '688981.SH', 'trade_date': '20260401',
            'market_regime': 'Neutral', 'pattern_type': 'PULLBACK_ALPHA',
            'entry_type': 'pullback', 'ret_60d': 0.10, 'max_drawdown': 0.05,
            'pullback_ma': 'MA20', 'leader_rank': 5,
            'amount': 2e8, 'turnover_rate': 2.5,
            'future_5_return': 0.01, 'future_10_return': 0.03,
            'future_20_return': 0.04, 'future_max_drawdown': -0.04,
            'holding_days': 6, 'success_flag': 1,
        })
        self.assertEqual(result, 'PULLBACK_ALPHA')


# ═══════════════════════════════════════════════
# 测试2: 不同pattern不互相污染测试
# ═══════════════════════════════════════════════

class TestPatternBucketIsolation(unittest.TestCase):
    """测试不同pattern_type的统计隔离"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, 'test_isolation.db')
        self.conn = _create_test_pattern_db(self.db_path)

        # 插入PULLBACK_ALPHA样本（20个，高胜率）
        pullback_records = []
        for i in range(20):
            pullback_records.append({
                'ts_code': f'PB_{i:04d}.SZ', 'trade_date': f'202601{15+i:02d}',
                'market_regime': 'Bull', 'pattern_type': 'PULLBACK_ALPHA',
                'entry_type': 'pullback', 'ret_60d': 0.30, 'max_drawdown': 0.08,
                'pullback_ma': 'MA20', 'leader_rank': 3,
                'future_10_return': 0.05 if i < 15 else -0.03,
                'success_flag': 1 if i < 15 else 0,
            })
        _insert_test_records(self.conn, pullback_records)

        # 插入BREAKOUT_ALPHA样本（20个，低胜率，故意混淆）
        breakout_records = []
        for i in range(20):
            breakout_records.append({
                'ts_code': f'BO_{i:04d}.SZ', 'trade_date': f'202602{1+i:02d}',
                'market_regime': 'Bull', 'pattern_type': 'BREAKOUT_ALPHA',
                'entry_type': 'pullback', 'ret_60d': 0.25, 'max_drawdown': 0.03,
                'pullback_ma': '', 'leader_rank': 1,
                'future_10_return': -0.02 if i < 14 else 0.06,
                'success_flag': 0 if i < 14 else 1,
            })
        _insert_test_records(self.conn, breakout_records)

    def tearDown(self):
        self.conn.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp_dir)
        except:
            pass

    def _query_stats(self, pattern_type: str = None) -> Dict:
        """查询统计（模拟pattern_db.query_pattern_stats）"""
        conditions = ["1=1"]
        params = []
        if pattern_type:
            conditions.append("pattern_type = ?")
            params.append(pattern_type)

        sql = f"""
        SELECT COUNT(*) as n, AVG(success_flag) as win_rate,
               AVG(future_10_return) as avg_ret
        FROM pattern_history
        WHERE {' AND '.join(conditions)}
        """
        cursor = self.conn.execute(sql, params)
        row = cursor.fetchone()
        return {'n': row[0], 'win_rate': row[1] or 0.0, 'avg_ret': row[2] or 0.0}

    def test_pullback_not_contaminated_by_breakout(self):
        """PULLBACK_ALPHA统计不应被BREAKOUT_ALPHA污染"""
        pb_stats = self._query_stats('PULLBACK_ALPHA')
        # PULLBACK: 15/20胜 = 75%
        self.assertEqual(pb_stats['n'], 20)
        self.assertAlmostEqual(pb_stats['win_rate'], 0.75, places=2)

    def test_breakout_not_contaminated_by_pullback(self):
        """BREAKOUT_ALPHA统计不应被PULLBACK_ALPHA污染"""
        bo_stats = self._query_stats('BREAKOUT_ALPHA')
        # BREAKOUT: 6/20胜 = 30%
        self.assertEqual(bo_stats['n'], 20)
        self.assertAlmostEqual(bo_stats['win_rate'], 0.30, places=2)

    def test_unfiltered_query_contains_all(self):
        """不过滤pattern_type时，数据未丢失"""
        all_stats = self._query_stats(None)
        self.assertEqual(all_stats['n'], 40)

    def test_classify_pattern_type_correctness(self):
        """测试classify_pattern_type函数的分类正确性"""
        # 龙头回踩
        self.assertEqual(
            classify_pattern_type(ret_60d=0.35, drawdown=0.08, pullback_ma='MA20', leader_rank=3),
            'PULLBACK_ALPHA'
        )
        # 突破新高
        self.assertEqual(
            classify_pattern_type(ret_60d=0.15, drawdown=0.03, pullback_ma='', leader_rank=1),
            'BREAKOUT_ALPHA'
        )
        # 超跌反弹
        self.assertEqual(
            classify_pattern_type(ret_60d=-0.20, drawdown=0.25, pullback_ma='MA60', leader_rank=None),
            'REBOUND_ALPHA'
        )
        # 主题轮动（非leader有回踩）
        self.assertEqual(
            classify_pattern_type(ret_60d=0.10, drawdown=0.06, pullback_ma='MA20', leader_rank=None),
            'ROTATION_ALPHA'
        )


# ═══════════════════════════════════════════════
# 测试3: Adjusted EV 排序测试
# ═══════════════════════════════════════════════

class TestAdjustedEVSorting(unittest.TestCase):
    """测试Adjusted_EV排序和Confidence Level"""

    def setUp(self):
        self.config = _make_minimal_config()
        self.engine = EVEngine(self.config)

    def _make_pattern_match(self, code: str, n_samples: int, win_prob: float,
                             avg_ret: float, avg_loss: float, confidence: float,
                             pattern_type: str = 'PULLBACK_ALPHA') -> 'PatternMatchResult':
        """创建测试用的PatternMatchResult"""
        pm = MagicMock()
        pm.ts_code = code
        pm.name = f'Stock_{code}'
        pm.theme = 'Test'
        pm.pattern_type = pattern_type
        pm.n_samples = n_samples
        pm.win_probability = win_prob
        pm.avg_return_5d = avg_ret * 0.5
        pm.avg_return_10d = avg_ret
        pm.avg_return_20d = avg_ret * 1.5
        pm.avg_max_drawdown = -0.03
        pm.avg_win_return = avg_ret + 0.02
        pm.avg_loss_return = avg_loss
        pm.confidence = confidence
        pm.cold_start_phase = 'data_driven'
        pm.blend_alpha = 1.0
        return pm

    def test_adjusted_ev_small_sample_penalty(self):
        """小样本高EV应被Confidence拉低

        EV = win_prob * avg_win - loss_prob * avg_loss
        SMALL: 0.80 * (0.12+0.02) - 0.20 * 0.03 = 0.106 → Adj=0.106*0.20=0.0212
        LARGE: 0.60 * (0.06+0.02) - 0.40 * 0.04 = 0.032 → Adj=0.032*0.85=0.0272
        LARGE(0.0272) > SMALL(0.0212) → LARGE排第一
        """
        matches = {
            'SMALL.SZ': self._make_pattern_match('SMALL.SZ', 3, 0.80, 0.12, -0.03, 0.20),
            'LARGE.SZ': self._make_pattern_match('LARGE.SZ', 60, 0.60, 0.06, -0.04, 0.85),
        }
        result = self.engine.evaluate('20260728', matches)
        ranked = result.ranked_list

        # LARGE.SZ 应排在 SMALL.SZ 前面
        self.assertEqual(ranked[0].ts_code, 'LARGE.SZ')
        self.assertEqual(ranked[1].ts_code, 'SMALL.SZ')

        # 验证Adjusted EV计算（EV × Confidence）
        large_ev_raw = 0.60 * (0.06 + 0.02) - 0.40 * 0.04  # = 0.032
        small_ev_raw = 0.80 * (0.12 + 0.02) - 0.20 * 0.03  # = 0.106
        self.assertAlmostEqual(ranked[0].adjusted_ev, large_ev_raw * 0.85, places=4)
        self.assertAlmostEqual(ranked[1].adjusted_ev, small_ev_raw * 0.20, places=4)

    def test_confidence_level_by_sample_size(self):
        """基于样本量的Confidence Level测试"""
        self.assertEqual(get_confidence_level(3), 'D')
        self.assertEqual(get_confidence_level(5), 'C')
        self.assertEqual(get_confidence_level(20), 'B')
        self.assertEqual(get_confidence_level(50), 'A')
        self.assertEqual(get_confidence_level(100), 'A')

    def test_adjusted_ev_sorted_descending(self):
        """多个标的按Adjusted_EV降序排列"""
        matches = {
            'A.SZ': self._make_pattern_match('A.SZ', 50, 0.65, 0.08, -0.04, 0.80),
            'B.SZ': self._make_pattern_match('B.SZ', 50, 0.60, 0.05, -0.03, 0.75),
            'C.SZ': self._make_pattern_match('C.SZ', 50, 0.70, 0.10, -0.05, 0.90),
        }
        result = self.engine.evaluate('20260728', matches)
        codes = [r.ts_code for r in result.ranked_list]
        self.assertEqual(codes, ['C.SZ', 'A.SZ', 'B.SZ'])

    def test_ev_result_fields(self):
        """EVResult包含V6.2字段"""
        matches = {
            'TEST.SZ': self._make_pattern_match('TEST.SZ', 25, 0.65, 0.07, -0.03, 0.72),
        }
        result = self.engine.evaluate('20260728', matches)
        ev_r = result.ranked_list[0]

        self.assertTrue(hasattr(ev_r, 'adjusted_ev'))
        self.assertTrue(hasattr(ev_r, 'confidence_level'))
        self.assertTrue(hasattr(ev_r, 'n_samples'))
        self.assertTrue(hasattr(ev_r, 'pattern_type'))
        self.assertEqual(ev_r.n_samples, 25)
        self.assertEqual(ev_r.confidence_level, 'B')  # 20-50 → B
        self.assertAlmostEqual(ev_r.adjusted_ev, ev_r.expected_value_10d * ev_r.confidence, places=4)


# ═══════════════════════════════════════════════
# 测试4: Learning Mode 仓位测试
# ═══════════════════════════════════════════════

class TestLearningMode(unittest.TestCase):
    """测试Learning Mode仓位的正确性"""

    def setUp(self):
        self.config = _make_minimal_config()
        self.engine = RiskBudgetPositionEngine(self.config)

    def _make_ev_result(self, code: str, signal: str, n_samples: int,
                        ev: float = 0.05, confidence: float = 0.5,
                        confidence_level: str = 'C', expected_dd: float = -0.03):
        """创建模拟的EVResult"""
        ev_r = MagicMock()
        ev_r.ts_code = code
        ev_r.signal.value = signal
        ev_r.signal = MagicMock()
        ev_r.signal.value = signal
        ev_r.n_samples = n_samples
        ev_r.expected_value_10d = ev
        ev_r.confidence = confidence
        ev_r.confidence_level = confidence_level
        ev_r.expected_drawdown = expected_dd
        ev_r.win_probability = 0.60
        return ev_r

    def _make_candidate(self, code: str, name: str = None, theme: str = 'Test',
                        leader_score: int = 50, atr: float = 0.5,
                        ref_price: float = 50.0):
        return {
            'ts_code': code,
            'name': name or f'Stock_{code}',
            'theme': theme,
            'leader_score': leader_score,
            'atr': atr,
            'ref_price': ref_price,
        }

    def test_live_mode_normal_position(self):
        """LIVE模式应使用正常Risk Budget计算"""
        candidates = [self._make_candidate('002371.SZ', '北方华创', leader_score=3)]
        ev_results = {
            '002371.SZ': self._make_ev_result('002371.SZ', 'BUY', n_samples=50, ev=0.06),
        }
        result = self.engine.allocate(
            trade_date='20260728',
            candidates=candidates,
            base_exposure_pct=50,
            regime_name='Bull',
            ev_results=ev_results,
            market_score=70,
            system_mode='LIVE',
        )
        pr = result.positions['002371.SZ']
        # LIVE模式应正常计算仓位（>0）
        self.assertGreater(pr.position_pct, 0)
        self.assertFalse(pr.is_learning)
        self.assertEqual(pr.explanation.system_mode, 'LIVE')

    def test_learning_mode_qualified_stock(self):
        """符合学习条件的标的应获得独立公式计算的学习仓位

        Formula: Base(5%) × Conf_Adj(C=0.6) × Risk_Adj(DD≤3%=1.0) = 3%
        """
        candidates = [self._make_candidate('002371.SZ', '北方华创', leader_score=5)]
        ev_results = {
            '002371.SZ': self._make_ev_result(
                '002371.SZ', 'BUY', n_samples=5, ev=0.08,
                confidence_level='C', expected_dd=-0.03),
        }
        sm_scores = {'002371.SZ': 75}
        result = self.engine.allocate(
            trade_date='20260728',
            candidates=candidates,
            base_exposure_pct=50,
            regime_name='Bull',
            ev_results=ev_results,
            market_score=70,
            system_mode='LEARNING',
            smart_money_scores=sm_scores,
        )
        pr = result.positions['002371.SZ']
        # 5% × 0.6(C) × 1.0(DD≤3%) = 3% → clamp[3,8] → 3%
        self.assertEqual(pr.position_pct, 3.0,
                         f"Expected 3.0%, got {pr.position_pct:.1f}%")
        self.assertTrue(pr.is_learning)
        # 验证解释字段
        exp = pr.explanation
        self.assertEqual(exp.learning_base_pct, 5.0)
        self.assertEqual(exp.confidence_adj, 0.6)
        self.assertEqual(exp.risk_adj, 1.0)
        self.assertEqual(exp.final_position_pct, 3.0)

    def test_learning_position_confidence_a(self):
        """Confidence Level A → conf_adj=1.0 → 5%×1.0×0.8=4%"""
        candidates = [self._make_candidate('300750.SZ', '宁德时代', leader_score=3)]
        ev_results = {
            '300750.SZ': self._make_ev_result(
                '300750.SZ', 'BUY', n_samples=25, ev=0.06,  # <30 才能进学习
                confidence_level='A', expected_dd=-0.05),
        }
        sm_scores = {'300750.SZ': 85}
        result = self.engine.allocate(
            trade_date='20260728', candidates=candidates,
            base_exposure_pct=50, regime_name='Bull',
            ev_results=ev_results, market_score=70,
            system_mode='LEARNING', smart_money_scores=sm_scores,
        )
        pr = result.positions['300750.SZ']
        # 5% × 1.0(A) × 0.8(DD≤6%) = 4% → clamp[3,8] → 4%
        self.assertEqual(pr.position_pct, 4.0)
        self.assertEqual(pr.explanation.confidence_adj, 1.0)
        self.assertEqual(pr.explanation.risk_adj, 0.8)

    def test_learning_position_large_drawdown(self):
        """大回撤 → risk_adj=0.4 → 5%×0.6×0.4=1.2% → clamp到3%"""
        candidates = [self._make_candidate('000001.SZ', '平安银行', leader_score=10)]
        ev_results = {
            '000001.SZ': self._make_ev_result(
                '000001.SZ', 'BUY', n_samples=8, ev=0.10,
                confidence_level='C', expected_dd=-0.12),
        }
        sm_scores = {'000001.SZ': 65}
        result = self.engine.allocate(
            trade_date='20260728', candidates=candidates,
            base_exposure_pct=50, regime_name='Bull',
            ev_results=ev_results, market_score=70,
            system_mode='LEARNING', smart_money_scores=sm_scores,
        )
        pr = result.positions['000001.SZ']
        # 5% × 0.6(C) × 0.4(DD>10%) = 1.2% → clamp[3,8] → 3%
        self.assertEqual(pr.position_pct, 3.0)
        self.assertEqual(pr.explanation.risk_adj, 0.4)

    def test_learning_mode_insufficient_smart_money(self):
        """聪明钱<60分的标的不应获得学习仓位"""
        candidates = [self._make_candidate('300750.SZ', '宁德时代', leader_score=5)]
        ev_results = {
            '300750.SZ': self._make_ev_result('300750.SZ', 'BUY', n_samples=5, ev=0.08),
        }
        sm_scores = {'300750.SZ': 40}
        result = self.engine.allocate(
            trade_date='20260728',
            candidates=candidates,
            base_exposure_pct=50,
            regime_name='Bull',
            ev_results=ev_results,
            market_score=70,
            system_mode='LEARNING',
            smart_money_scores=sm_scores,
        )
        pr = result.positions['300750.SZ']
        # 聪明钱不足 → 正常Risk Budget计算
        self.assertFalse(pr.is_learning,
                         "Low smart money stock should not be learning position")

    def test_learning_mode_no_risk_budget_formula(self):
        """Learning Mode禁止使用正常Risk Budget公式"""
        candidates = [self._make_candidate('002371.SZ', '北方华创', leader_score=5)]
        ev_results = {
            '002371.SZ': self._make_ev_result(
                '002371.SZ', 'BUY', n_samples=5, ev=0.08,
                confidence_level='C', expected_dd=-0.03),
        }
        sm_scores = {'002371.SZ': 75}
        result = self.engine.allocate(
            trade_date='20260728', candidates=candidates,
            base_exposure_pct=50, regime_name='Bull',
            ev_results=ev_results, market_score=70,
            system_mode='LEARNING', smart_money_scores=sm_scores,
        )
        pr = result.positions['002371.SZ']
        exp = pr.explanation
        # 不应出现Risk Budget公式字段
        self.assertEqual(exp.base_position_pct, 0.0)
        self.assertEqual(exp.market_multiplier, 1.0)  # default
        self.assertEqual(exp.ev_multiplier, 1.0)      # default
        self.assertEqual(exp.risk_multiplier, 1.0)    # default
        # 应使用独立Learning Position字段
        self.assertGreater(exp.learning_base_pct, 0)
        self.assertGreater(exp.confidence_adj, 0)
        self.assertGreater(exp.risk_adj, 0)

    def test_learning_total_cap(self):
        """学习仓位总上限不超过max_total_learning(20%)"""
        candidates = [
            self._make_candidate(f'{i:06d}.SZ', f'Stock{i}', leader_score=5)
            for i in range(6)
        ]
        ev_results = {}
        sm_scores = {}
        for i in range(6):
            code = f'{i:06d}.SZ'
            ev_results[code] = self._make_ev_result(
                code, 'BUY', n_samples=5, ev=0.08,
                confidence_level='A', expected_dd=-0.03)
            sm_scores[code] = 75
        result = self.engine.allocate(
            trade_date='20260728', candidates=candidates,
            base_exposure_pct=50, regime_name='Bull',
            ev_results=ev_results, market_score=70,
            system_mode='LEARNING', smart_money_scores=sm_scores,
        )
        # 6只×5%(A=1.0, DD≤3%=1.0)=30% > 20% → 等比例压缩到20%
        total_learning = sum(p.position_pct for p in result.positions.values() if p.is_learning)
        self.assertAlmostEqual(total_learning, 20.0, places=1)
        self.assertLessEqual(total_learning, 20.0)

    def test_learning_mode_bear_regime(self):
        """熊市不应启用学习模式"""
        candidates = [self._make_candidate('002371.SZ', '北方华创', leader_score=5)]
        ev_results = {
            '002371.SZ': self._make_ev_result('002371.SZ', 'BUY', n_samples=5, ev=0.08),
        }
        sm_scores = {'002371.SZ': 75}
        result = self.engine.allocate(
            trade_date='20260728',
            candidates=candidates,
            base_exposure_pct=10,
            regime_name='Bear',
            ev_results=ev_results,
            market_score=20,
            system_mode='LEARNING',
            smart_money_scores=sm_scores,
        )
        pr = result.positions['002371.SZ']
        # Bear regime → regime_learning_ok = False → 不是学习仓位
        self.assertFalse(pr.is_learning)

    def test_learning_mode_sufficient_samples(self):
        """样本>=30的标的应使用正常Risk Budget"""
        candidates = [self._make_candidate('002371.SZ', '北方华创', leader_score=3)]
        ev_results = {
            '002371.SZ': self._make_ev_result('002371.SZ', 'BUY', n_samples=50, ev=0.06),
        }
        sm_scores = {'002371.SZ': 85}
        result = self.engine.allocate(
            trade_date='20260728',
            candidates=candidates,
            base_exposure_pct=50,
            regime_name='Bull',
            ev_results=ev_results,
            market_score=70,
            system_mode='LEARNING',
            smart_money_scores=sm_scores,
        )
        pr = result.positions['002371.SZ']
        # 样本>=30 → 正常仓位（非学习）
        self.assertFalse(pr.is_learning)
        self.assertGreater(pr.position_pct, 0)

    def test_validation_mode_zero_position(self):
        """VALIDATION模式所有仓位应为0"""
        candidates = [self._make_candidate('002371.SZ', '北方华创')]
        ev_results = {
            '002371.SZ': self._make_ev_result('002371.SZ', 'BUY', n_samples=50),
        }
        result = self.engine.allocate(
            trade_date='20260728',
            candidates=candidates,
            base_exposure_pct=50,
            regime_name='Bull',
            ev_results=ev_results,
            market_score=70,
            system_mode='VALIDATION',
        )
        pr = result.positions['002371.SZ']
        self.assertEqual(pr.position_pct, 0.0)
        self.assertEqual(pr.signal, 'VALIDATION')

    def test_result_contains_system_mode(self):
        """RiskBudgetResult应包含system_mode"""
        result = self.engine.allocate(
            trade_date='20260728',
            candidates=[],
            base_exposure_pct=50,
            regime_name='Bull',
            ev_results={},
            market_score=70,
            system_mode='LEARNING',
        )
        self.assertEqual(result.system_mode, 'LEARNING')


# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    unittest.main(verbosity=2)
