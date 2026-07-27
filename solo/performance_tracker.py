#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Performance Tracker V1.0 —— 策略自校准评估系统

功能：
  1. 从历史 JSON + K 线数据提取信号，计算 3/5/10 日收益率
  2. 按信号类型 / Role / 市场状态 / 子主题 聚合胜率/收益/回撤
  3. 输出自校准报告 → 动态调整各信号权重

设计目标：
  从"规则驱动"逐步演进到"数据驱动的自校准系统"
"""

import sys
import os
import json
import glob
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

CACHE_DIR = r"d:\mystock\cache_daily"
SIGNAL_HISTORY_FILE = os.path.join(CACHE_DIR, "signal_history.json")
TRACKER_STATS_FILE = os.path.join(CACHE_DIR, "tracker_stats.json")

# ── 信号定义 ──
BUY_SIGNALS = {'BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY'}
LOOKBACK_DAYS = [3, 5, 10]

# ── 信号模拟阈值（用于旧版 JSON 无法提取真实 entry_signal 时的近似） ──
SIMULATED_SIGNAL_RULES = {
    'BREAKOUT BUY':  {'final_min': 70, 'roles': {'Leader', 'Core', 'Momentum'}},
    'PULLBACK BUY':  {'final_min': 60, 'roles': {'Core', 'Momentum', 'Follower', 'Beta'}},
    'PRE_ROTATE BUY': {'final_min': 55, 'roles': {'Follower', 'Momentum', 'Beta', 'Defensive'}},
}

# ── 子主题阶段 → 推荐信号映射（与 EntryTimingEngine.STAGE_STRATEGY 一致） ──
STAGE_SIGNAL_MAP = {
    '潜伏':   ('WATCH', 'PULLBACK BUY'),
    '升温':   ('BREAKOUT BUY', 'PRE_ROTATE BUY'),
    '主升':   ('HOLD', 'PULLBACK BUY'),
    '分歧':   ('REDUCE', 'WATCH'),
    '退潮':   ('SELL', 'REDUCE'),
    '弱势':   ('WATCH', 'PULLBACK BUY'),
}

# ── Role → 推荐信号偏好（与 EntryTimingEngine.ROLE_SIGNAL_BIAS 一致） ──
ROLE_SIGNAL_BIAS = {
    'Leader': 'HOLD',
    'Core': 'PULLBACK BUY',
    'Momentum': 'BREAKOUT BUY',
    'Beta': 'BREAKOUT BUY',
    'Follower': 'PRE_ROTATE BUY',
    'Defensive': 'WATCH',
    'Weak': 'WATCH',
}

# ── 各信号的最低 Alpha 要求 ──
SIGNAL_ALPHA_MIN = {
    'BREAKOUT BUY': 55,
    'PULLBACK BUY': 50,
    'PRE_ROTATE BUY': 45,
    'HOLD': 40,
}


def load_json_or_empty(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class PerformanceTracker:
    """
    策略评估引擎

    核心数据流：
      JSON(主题/评分/信号) → Signal Records → 对接 K线 → Forward Returns → 聚合统计 → 校准报告
    """

    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        # 信号记录: [{date, code, name, signal, entry_score, trade_score,
        #             stock_alpha, final_score, role, theme, subtheme,
        #             return_3d, return_5d, return_10d, max_dd, ...}]
        self.signal_records: List[Dict] = []
        # K线缓存: {code: pd.DataFrame}
        self._kline_cache: Dict[str, pd.DataFrame] = {}
        # 聚合统计
        self.stats: Dict = {}
        # 权重建议
        self.weight_adj: Dict = {}

    # ──────────────────────────────────────────────
    # K线加载
    # ──────────────────────────────────────────────

    def _load_kline(self, code: str) -> Optional[pd.DataFrame]:
        """加载单只股票 K 线"""
        if code in self._kline_cache:
            return self._kline_cache[code]
        csv_path = os.path.join(self.cache_dir, f"{code}.csv")
        if not os.path.exists(csv_path):
            return None
        try:
            df = pd.read_csv(csv_path, dtype={'trade_date': str})
            df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d', errors='coerce')
            df = df.dropna(subset=['trade_date']).sort_values('trade_date')
            self._kline_cache[code] = df
            return df
        except Exception:
            return None

    def _get_future_returns(self, code: str, signal_date: str,
                            days: List[int] = None) -> Dict:
        """
        计算信号发出后的 N 日收益

        返回: {3: 收益率, 5: 收益率, 10: 收益率, 'max_dd': 最大回撤, 'close': 信号日收盘价}
        """
        if days is None:
            days = LOOKBACK_DAYS
        df = self._load_kline(code)
        if df is None or df.empty:
            return {d: None for d in days + ['max_dd', 'close']}

        sig_dt = pd.to_datetime(signal_date, format='%Y%m%d', errors='coerce')
        if pd.isna(sig_dt):
            return {d: None for d in days + ['max_dd', 'close']}

        # 找到信号日（或最近的前一个交易日）
        match = df[df['trade_date'] <= sig_dt]
        if match.empty:
            return {d: None for d in days + ['max_dd', 'close']}
        idx = match.index[-1]
        close = float(df.iloc[idx]['close'])

        result = {'close': close}
        for nd in days:
            future_idx = idx + nd
            if future_idx >= len(df):
                result[nd] = None
            else:
                future_close = float(df.iloc[future_idx]['close'])
                result[nd] = (future_close - close) / close

        # 最大回撤（持有期内）
        max_dd = 0.0
        peak = close
        for i in range(idx + 1, min(idx + max(days) + 1, len(df))):
            c = float(df.iloc[i]['close'])
            if c > peak:
                peak = c
            dd = (peak - c) / peak
            if dd > max_dd:
                max_dd = dd
        result['max_dd'] = max_dd

        return result

    # ──────────────────────────────────────────────
    # 信号提取
    # ──────────────────────────────────────────────

    def _build_subtheme_stage_map(self, json_data: Dict) -> Dict[str, str]:
        """从 entry_timing 构建 {母主题/子主题: 阶段} 映射"""
        stage_map = {}
        et = json_data.get('entry_timing', {})
        for parent, subs in et.items():
            for sub_name, s_data in subs.items():
                stage = s_data.get('subtheme_stage', '')
                if stage:
                    stage_map[f'{parent}/{sub_name}'] = stage
        return stage_map

    def _simulate_signal_for_stock(self, code: str, si: Dict,
                                   subtheme_stage: str) -> Optional[Dict]:
        """
        基于子主题阶段 + Role + Alpha 智能模拟信号

        规则（与 EntryTimingEngine 一致）:
          1. 子主题阶段决定策略方向
          2. Role 决定信号偏好
          3. Alpha 决定信号强度门槛
        """
        role = si.get('role', '')
        alpha = si.get('stock_alpha', 50) or 50
        fs = si.get('final_score', 50) or 50
        subtheme = si.get('subtheme', '')

        # 没有 Role 或 Alpha 太低 → 跳过
        if not role or role in ('Weak', 'Defensive', ''):
            return None
        if alpha < 45:
            return None

        # 从子主题阶段获取策略信号
        primary_sig, secondary_sig = STAGE_SIGNAL_MAP.get(subtheme_stage, ('WATCH', 'WATCH'))

        # Role 偏好信号
        role_bias = ROLE_SIGNAL_BIAS.get(role, 'WATCH')

        # 决策逻辑:
        candidate = 'WATCH'
        # 如果 Role 偏好是买入信号且 Alpha 达标 → 使用该信号
        if role_bias in BUY_SIGNALS and alpha >= SIGNAL_ALPHA_MIN.get(role_bias, 50):
            candidate = role_bias
        # 否则用阶段的 secondary 买入信号
        elif secondary_sig in BUY_SIGNALS and alpha >= SIGNAL_ALPHA_MIN.get(secondary_sig, 50):
            candidate = secondary_sig
        # 退一步用 primary
        elif primary_sig in BUY_SIGNALS and alpha >= SIGNAL_ALPHA_MIN.get(primary_sig, 50):
            candidate = primary_sig
        # 都不是买入信号 → 跳过
        else:
            return None

        # 计算 entry_score 近似值
        entry_score = min(100, alpha * 0.6 + fs * 0.4)
        trade_score = min(100, fs * 0.7 + entry_score * 0.3)

        return {
            'date': '',
            'code': code,
            'name': si.get('name', code),
            'signal': candidate,
            'entry_score': round(entry_score, 1),
            'trade_score': round(trade_score, 1),
            'stock_alpha': alpha,
            'final_score': fs,
            'investment_score': si.get('investment_score', fs),
            'role': role,
            'theme': '',
            'subtheme': subtheme,
            'risk_level': 'medium',
            'holding_priority': 3 if role in ('Leader', 'Core') else 2,
            'source': 'simulated',
        }

    def _extract_signals_from_json(self, json_data: Dict,
                                   source_date: str) -> List[Dict]:
        """
        从 theme_stock_map JSON 提取信号

        三种数据源（优先级）:
          1. stocks 字典中的 entry_signal 为 BUY 信号 → 直接使用（真实信号）
          2. 有 entry_timing 结构 + stocks 有 role → 基于子主题阶段智能模拟
          3. 旧版无信号字段 → 用 final_score+role 模拟
        """
        signals = []
        stocks = json_data.get('stocks', {})

        # ════════════════════════════════════════
        # 方法 1: 从 stocks 字典提取真实 BUY 信号
        # ════════════════════════════════════════
        real_buy_signals = []
        for code, si in stocks.items():
            sig = si.get('entry_signal', '')
            if sig in BUY_SIGNALS:
                real_buy_signals.append({
                    'date': source_date,
                    'code': code,
                    'name': si.get('name', code),
                    'signal': sig,
                    'entry_score': si.get('entry_score', 50),
                    'trade_score': si.get('trade_score', 50),
                    'stock_alpha': si.get('stock_alpha', 50),
                    'final_score': si.get('final_score', 50),
                    'investment_score': si.get('investment_score', 50),
                    'role': si.get('role', ''),
                    'theme': '',
                    'subtheme': si.get('subtheme', ''),
                    'risk_level': si.get('risk_level', 'medium'),
                    'holding_priority': si.get('holding_priority', 2),
                    'source': 'real',
                })
        if real_buy_signals:
            return real_buy_signals

        # ════════════════════════════════════════
        # 方法 2: 有 entry_timing → 构建阶段映射，模拟信号
        # ════════════════════════════════════════
        stage_map = self._build_subtheme_stage_map(json_data)
        if stage_map:
            # 先构建 子主题名 → 阶段 的映射（仅子主题名，不包含母主题）
            sub_only_stage = {}
            for key, stage in stage_map.items():
                parts = key.split('/')
                if len(parts) >= 2:
                    sub_only_stage[parts[-1]] = stage  # 只用子主题名匹配

            # 遍历 stocks，用它们自身的 subtheme 字段和 stage_map 匹配
            for code, si in stocks.items():
                subtheme = si.get('subtheme', '')
                if not subtheme:
                    continue
                # 查找阶段
                stage = sub_only_stage.get(subtheme, '')
                if not stage:
                    continue
                sim = self._simulate_signal_for_stock(code, si, stage)
                if sim:
                    sim['date'] = source_date
                    sim['theme'] = ''  # 从 stocks 不易确定母主题
                    signals.append(sim)
            if signals:
                return signals

        # ════════════════════════════════════════
        # 方法 3: 用 final_score+role 模拟（无任何额外数据）
        # ════════════════════════════════════════
        for code, si in stocks.items():
            fs = si.get('final_score', 0) or 0
            role = si.get('role', '')
            # 试用智能模拟（无阶段信息，降低阈值）
            sim = self._simulate_signal_for_stock(code, si, '')
            if sim:
                sim['date'] = source_date
                signals.append(sim)
                continue
            # 兼容旧规则兜底
            for sig_name, rule in SIMULATED_SIGNAL_RULES.items():
                if fs >= rule['final_min'] and role in rule['roles']:
                    signals.append({
                        'date': source_date,
                        'code': code,
                        'name': si.get('name', code),
                        'signal': sig_name,
                        'entry_score': min(100, fs * 1.2),
                        'trade_score': min(100, fs * 0.8 + 20),
                        'stock_alpha': si.get('stock_alpha', 50),
                        'final_score': fs,
                        'investment_score': si.get('investment_score', fs),
                        'role': role,
                        'theme': '',
                        'subtheme': si.get('subtheme', ''),
                        'risk_level': 'medium',
                        'holding_priority': 3 if role in ('Leader', 'Core') else 2,
                        'source': 'simulated',
                    })
                    break
        return signals

    def _deduplicate_signals(self, signals: List[Dict]) -> List[Dict]:
        """去重：同一只股票同一天只保留最强信号"""
        seen = set()
        deduped = []
        for s in sorted(signals, key=lambda x: -x.get('entry_score', 0)):
            key = (s['date'], s['code'])
            if key not in seen:
                seen.add(key)
                deduped.append(s)
        return deduped

    # ──────────────────────────────────────────────
    # 主流程
    # ──────────────────────────────────────────────

    def _build_role_cache(self) -> Dict[str, Dict]:
        """
        从最新的 V2 JSON 构建 Role/Alpha/Subtheme 缓存

        用于为旧版 JSON（无 role/alpha）补充缺失字段
        """
        # 找最新的有完整数据的 JSON
        enriched_files = sorted(glob.glob(os.path.join(self.cache_dir, "theme_stock_map_v2*.json")))
        if not enriched_files:
            # 回退到 latest
            latest = os.path.join(self.cache_dir, "theme_stock_map_latest.json")
            if os.path.exists(latest):
                enriched_files = [latest]

        cache = {}
        for fpath in enriched_files:
            data = load_json_or_empty(fpath)
            if not data:
                continue
            stocks = data.get('stocks', {})
            for code, si in stocks.items():
                role = si.get('role', '')
                alpha = si.get('stock_alpha', 0)
                subtheme = si.get('subtheme', '')
                if role or alpha:
                    cache[code] = {'role': role, 'stock_alpha': alpha, 'subtheme': subtheme}
        if cache:
            print(f"  [Tracker] Role 缓存已构建: {len(cache)} 只股票")
        return cache

    def _enrich_stock_with_cache(self, stocks: Dict[str, Dict],
                                  role_cache: Dict[str, Dict]) -> Dict[str, Dict]:
        """用缓存 Role 补充 stocks 的缺失字段"""
        enriched = dict(stocks)
        for code, si in enriched.items():
            # 只补充缺失的字段
            if not si.get('role'):
                rc = role_cache.get(code, {})
                if rc.get('role'):
                    si['role'] = rc['role']
                if rc.get('stock_alpha') and not si.get('stock_alpha'):
                    si['stock_alpha'] = rc['stock_alpha']
                if rc.get('subtheme') and not si.get('subtheme'):
                    si['subtheme'] = rc['subtheme']
        return enriched

    def load(self, json_files: List[str] = None):
        """加载多个历史 JSON 文件，提取所有信号"""
        if json_files is None:
            # 自动发现所有 cache 中的 JSON
            json_files = sorted(glob.glob(os.path.join(self.cache_dir, "theme_stock_map*.json")))

        # 预构建 Role 缓存
        role_cache = self._build_role_cache()

        all_signals = []
        for fpath in json_files:
            data = load_json_or_empty(fpath)
            if not data:
                continue
            # 使用 JSON 内的 trade_date 字段
            date_str = str(data.get('trade_date', ''))
            if len(date_str) != 8:
                continue

            sigs = self._extract_signals_from_json(data, date_str)
            if not sigs and role_cache:
                # 方法 3.5: 用 Role 缓存补充后重新提取
                stocks = data.get('stocks', {})
                enriched_stocks = self._enrich_stock_with_cache(stocks, role_cache)
                data['stocks'] = enriched_stocks
                sigs = self._extract_signals_from_json(data, date_str)

            all_signals.extend(sigs)

        self.signal_records = self._deduplicate_signals(all_signals)
        print(f"  [Tracker] 加载 {len(json_files)} 个 JSON，提取 {len(self.signal_records)} 条信号")
        return self

    def compute_returns(self):
        """为所有信号计算 N 日收益"""
        count_ok = 0
        for s in self.signal_records:
            rets = self._get_future_returns(s['code'], s['date'])
            s['return_3d'] = rets.get(3)
            s['return_5d'] = rets.get(5)
            s['return_10d'] = rets.get(10)
            s['max_drawdown'] = rets.get('max_dd', 0)
            s['signal_close'] = rets.get('close')
            if s['return_5d'] is not None:
                count_ok += 1
        print(f"  [Tracker] 正向收益计算完成: {count_ok}/{len(self.signal_records)} 条有完整数据")
        return self

    # ──────────────────────────────────────────────
    # 聚合统计
    # ──────────────────────────────────────────────

    def _win_rate(self, returns: List[float], threshold: float = 0) -> Tuple[float, int, int]:
        """胜率 = 正收益比例"""
        if not returns:
            return 0, 0, 0
        wins = sum(1 for r in returns if r is not None and r > threshold)
        total = len([r for r in returns if r is not None])
        return wins / total if total > 0 else 0, wins, total

    def _avg_return(self, returns: List[float]) -> Tuple[float, float]:
        """平均收益和标准差"""
        valid = [r for r in returns if r is not None]
        if not valid:
            return 0, 0
        return np.mean(valid), np.std(valid)

    def aggregate(self):
        """
        按多个维度聚合统计
        """
        stats = {}

        for s in self.signal_records:
            # 在聚合内标注
            pass

        sr = self.signal_records

        # ════════════════════ 1. 按信号类型 ════════════════════
        by_signal = defaultdict(list)
        for s in sr:
            by_signal[s['signal']].append(s)
        stats['by_signal'] = {}
        for sig, items in by_signal.items():
            r3 = [s['return_3d'] for s in items if s['return_3d'] is not None]
            r5 = [s['return_5d'] for s in items if s['return_5d'] is not None]
            r10 = [s['return_10d'] for s in items if s['return_10d'] is not None]
            dd = [s['max_drawdown'] for s in items if s['max_drawdown'] is not None]
            stats['by_signal'][sig] = {
                'count': len(items),
                'win_rate_3d': self._win_rate(r3)[0],
                'win_rate_5d': self._win_rate(r5)[0],
                'win_rate_10d': self._win_rate(r10)[0],
                'avg_return_3d': self._avg_return(r3)[0],
                'avg_return_5d': self._avg_return(r5)[0],
                'avg_return_10d': self._avg_return(r10)[0],
                'avg_max_dd': np.mean(dd) if dd else 0,
                'source_counts': dict(pd.Series([s['source'] for s in items]).value_counts()),
            }

        # ════════════════════ 2. 按 Role ════════════════════
        by_role = defaultdict(list)
        for s in sr:
            by_role[s['role']].append(s)
        stats['by_role'] = {}
        for role, items in by_role.items():
            r5 = [s['return_5d'] for s in items if s['return_5d'] is not None]
            stats['by_role'][role] = {
                'count': len(items),
                'win_rate_5d': self._win_rate(r5)[0],
                'avg_return_5d': self._avg_return(r5)[0],
            }

        # ════════════════════ 3. 按主题 ════════════════════
        by_theme = defaultdict(list)
        for s in sr:
            by_theme[s['theme']].append(s)
        stats['by_theme'] = {}
        for theme, items in by_theme.items():
            r5 = [s['return_5d'] for s in items if s['return_5d'] is not None]
            if len(items) >= 5:  # 只统计信号数 >=5 的主题
                stats['by_theme'][theme] = {
                    'count': len(items),
                    'win_rate_5d': self._win_rate(r5)[0],
                    'avg_return_5d': self._avg_return(r5)[0],
                }

        # ════════════════════ 4. 按子主题 ════════════════════
        by_subtheme = defaultdict(list)
        for s in sr:
            key = f"{s['theme']}/{s['subtheme']}" if s['subtheme'] else s['theme']
            by_subtheme[key].append(s)
        stats['by_subtheme'] = {}
        for key, items in by_subtheme.items():
            r5 = [s['return_5d'] for s in items if s['return_5d'] is not None]
            if len(items) >= 3:
                stats['by_subtheme'][key] = {
                    'count': len(items),
                    'win_rate_5d': self._win_rate(r5)[0],
                    'avg_return_5d': self._avg_return(r5)[0],
                }

        self.stats = stats
        print(f"  [Tracker] 聚合统计完成: "
              f"by_signal={len(stats.get('by_signal', {}))}, "
              f"by_role={len(stats.get('by_role', {}))}, "
              f"by_theme={len(stats.get('by_theme', {}))}, "
              f"by_subtheme={len(stats.get('by_subtheme', {}))}")
        return self

    # ──────────────────────────────────────────────
    # 权重校准
    # ──────────────────────────────────────────────

    def calibrate_weights(self):
        """基于历史表现计算权重调整建议"""
        adj = {}
        by_signal = self.stats.get('by_signal', {})

        # 当前信号的基础权重
        base_weights = {
            'BREAKOUT BUY': 70,
            'PULLBACK BUY': 65,
            'PRE_ROTATE BUY': 60,
        }

        for sig, st in by_signal.items():
            if st['count'] < 3:
                adj[sig] = {'weight': base_weights.get(sig, 60), 'adjustment': 0,
                            'reason': '样本不足'}
                continue

            wr5 = st['win_rate_5d']
            ar5 = st['avg_return_5d']
            orig_w = base_weights.get(sig, 60)

            # 校准逻辑：
            #   win_rate > 55% → +5 (表现良好)
            #   win_rate > 60% → +10 (表现优秀)
            #   win_rate < 45% → -5 (表现不佳)
            #   win_rate < 40% → -10 (表现差)
            #   avg_return > 3% → +5 (高收益弹性)
            #   avg_return < -1% → -5 (亏损风险)
            delta = 0
            reasons = []
            if wr5 >= 0.60:
                delta += 10
                reasons.append(f"胜率{wr5:.0%}>60%")
            elif wr5 >= 0.55:
                delta += 5
                reasons.append(f"胜率{wr5:.0%}>55%")
            elif wr5 < 0.40:
                delta -= 10
                reasons.append(f"胜率{wr5:.0%}<40%")
            elif wr5 < 0.45:
                delta -= 5
                reasons.append(f"胜率{wr5:.0%}<45%")

            if ar5 > 0.03:
                delta += 5
                reasons.append(f"5日收益{ar5:.1%}>3%")
            elif ar5 < -0.01:
                delta -= 5
                reasons.append(f"5日收益{ar5:.1%}<-1%")

            new_w = max(30, min(100, orig_w + delta))
            adj[sig] = {
                'weight': new_w,
                'adjustment': delta,
                'original_weight': orig_w,
                'reason': '; '.join(reasons) if reasons else '表现中性',
            }

        self.weight_adj = adj
        return self

    # ──────────────────────────────────────────────
    # 报告生成
    # ──────────────────────────────────────────────

    def generate_report(self) -> str:
        """生成完整评估报告"""
        sep = '─' * 60
        lines = [sep, f"  策略自校准评估报告", f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}", sep]

        if not self.signal_records:
            lines.append("  ⚠ 无历史信号数据，请先运行 load()")
            return '\n'.join(lines)

        lines.append(f"\n  总信号数: {len(self.signal_records)}")
        real_count = sum(1 for s in self.signal_records if s['source'] == 'real')
        sim_count = sum(1 for s in self.signal_records if s['source'] == 'simulated')
        lines.append(f"  真实信号: {real_count} | 模拟信号: {sim_count}")

        # ── 1. 按信号类型统计 ──
        lines.append(f"\n{'='*60}")
        lines.append("  【按信号类型统计】")
        lines.append(f"{'='*60}")
        by_signal = self.stats.get('by_signal', {})
        lines.append(f"  {'信号类型':<20} {'数量':<6} {'3D胜率':<10} {'5D胜率':<10} {'10D胜率':<10} "
                     f"{'5D均收益':<10} {'最大回撤':<10}")
        lines.append(f"  {'─'*70}")
        for sig in ['BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY']:
            st = by_signal.get(sig, {})
            if st:
                lines.append(f"  {sig:<20} {st['count']:<6} "
                             f"{st['win_rate_3d']:<10.1%} {st['win_rate_5d']:<10.1%} {st['win_rate_10d']:<10.1%} "
                             f"{st['avg_return_5d']:<10.2%} {st['avg_max_dd']:<10.2%}")

        # ── 2. 按 Role 统计 ──
        lines.append(f"\n{'='*60}")
        lines.append("  【按 Role 统计 - 5日胜率】")
        lines.append(f"{'='*60}")
        by_role = self.stats.get('by_role', {})
        lines.append(f"  {'Role':<15} {'数量':<6} {'5D胜率':<10} {'5D均收益':<10}")
        lines.append(f"  {'─'*40}")
        for role in ['Leader', 'Core', 'Momentum', 'Beta', 'Follower', 'Defensive', 'Weak']:
            st = by_role.get(role, {})
            if st:
                lines.append(f"  {role:<15} {st['count']:<6} {st['win_rate_5d']:<10.1%} {st['avg_return_5d']:<10.2%}")

        # ── 3. 权重校准建议 ──
        lines.append(f"\n{'='*60}")
        lines.append("  【权重校准建议】")
        lines.append(f"{'='*60}")
        for sig, adj in self.weight_adj.items():
            delta_str = f"+{adj['adjustment']}" if adj['adjustment'] > 0 else str(adj['adjustment'])
            lines.append(f"  {sig:<20} {adj['original_weight']} → {adj['weight']} ({delta_str}) 原因: {adj['reason']}")

        # ── 4. 最佳/最差子主题 Top 5 ──
        by_sub = self.stats.get('by_subtheme', {})
        if by_sub:
            sorted_sub = sorted(by_sub.items(), key=lambda x: -x[1]['win_rate_5d'])
            lines.append(f"\n{'='*60}")
            lines.append("  【子主题预测准确率 Top 5】")
            lines.append(f"{'='*60}")
            for key, st in sorted_sub[:5]:
                lines.append(f"  {key:<30} 信号{st['count']}次 胜率{st['win_rate_5d']:.0%} 收益{st['avg_return_5d']:.2%}")
            lines.append(f"\n  子主题预测准确率 Bottom 5:")
            for key, st in sorted_sub[-5:]:
                lines.append(f"  {key:<30} 信号{st['count']}次 胜率{st['win_rate_5d']:.0%} 收益{st['avg_return_5d']:.2%}")

        # ── 5. 信号日志（最新的 15 条） ──
        lines.append(f"\n{'='*60}")
        lines.append("  【最新信号回测日志（按 Trade Score 排序）】")
        lines.append(f"{'='*60}")
        lines.append(f"  {'日期':<10} {'代码':<10} {'名称':<8} {'信号':<18} {'T分':<5} "
                     f"{'5D收益':<8} {'10D收益':<8} {'最大回撤':<8}")
        lines.append(f"  {'─'*70}")
        sorted_log = sorted(self.signal_records, key=lambda x: -(x.get('trade_score', 0) or 0))
        for s in sorted_log[:15]:
            r5 = s.get('return_5d')
            r10 = s.get('return_10d')
            dd = s.get('max_drawdown', 0)
            lines.append(f"  {s['date']:<10} {s['code']:<10} {s['name']:<8} {s['signal']:<18} "
                         f"{s.get('trade_score', 0):<5.0f} "
                         f"{f'{r5:.2%}' if r5 is not None else 'N/A':<8} "
                         f"{f'{r10:.2%}' if r10 is not None else 'N/A':<8} "
                         f"{f'{dd:.2%}' if dd else 'N/A':<8}")

        lines.append(f"\n{sep}")
        lines.append(f"  {'> ' if self.weight_adj else ''}"
                     f"当前信号权重: BREAKOUT={self.weight_adj.get('BREAKOUT BUY', {}).get('weight', 70)} "
                     f"PULLBACK={self.weight_adj.get('PULLBACK BUY', {}).get('weight', 65)} "
                     f"PRE_ROTATE={self.weight_adj.get('PRE_ROTATE BUY', {}).get('weight', 60)}")
        lines.append(sep)

        return '\n'.join(lines)

    def save_state(self):
        """保存信号记录和统计到缓存"""
        # 将 numpy 类型转换为原生 Python 类型以便 JSON 序列化
        def convert(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            elif isinstance(obj, (np.floating,)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(i) for i in obj]
            return obj

        state = {
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'n_signals': len(self.signal_records),
            'signals': convert(self.signal_records),
            'stats': convert(self.stats),
            'weight_adj': convert(self.weight_adj),
        }
        with open(TRACKER_STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"  [Tracker] 状态已保存: {TRACKER_STATS_FILE}")
        return self

    def load_state(self):
        """从缓存恢复状态"""
        state = load_json_or_empty(TRACKER_STATS_FILE)
        if state:
            self.signal_records = state.get('signals', [])
            self.stats = state.get('stats', {})
            self.weight_adj = state.get('weight_adj', {})
            print(f"  [Tracker] 状态已恢复: {len(self.signal_records)} 条信号")
        return self


# ═══════════════════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════════════════

def run_tracker(json_files: List[str] = None) -> PerformanceTracker:
    """一键运行 Performance Tracker"""
    tracker = PerformanceTracker()
    tracker.load(json_files)
    tracker.compute_returns()
    tracker.aggregate()
    tracker.calibrate_weights()
    return tracker


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print(f"{'='*60}")
    print(f"  Performance Tracker V1.0")
    print(f"{'='*60}")

    tracker = run_tracker()

    print(tracker.generate_report())

    # 保存状态
    tracker.save_state()
