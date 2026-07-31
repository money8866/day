#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题精华报告生成器
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
从 cache_daily/theme_stock_map_v2_{trade_date}.json 提取
主题→子主题→个股 的精华内容，生成结构化报告。

报告输出格式：
  - 纯文本 (.txt) → report_daily/theme_essence_{trade_date}.txt
  - HTML (.html)   → report_daily/theme_essence_{trade_date}.html
  - Markdown (.md) → report_daily/theme_essence_{trade_date}.md

输出位置：
  d:\\mystock\\report_daily\\theme_essence_{trade_date}.*

完全独立模块，通过 tushare_quant.py 单入口调用。
"""

import sys
import os
import json
import csv
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

# ── 生命周期阶段显示符号 ──
STAGE_SYMBOL = {
    '主升': '↑↑', '升温': '↑', '分歧': '〜',
    '潜伏': '↓', '退潮': '↓↓', '弱势': '×',
}

# ── 信号显示符号 ──
SIGNAL_SYMBOL = {
    'BREAKOUT BUY': '◆', 'PULLBACK BUY': '▼', 'PRE_ROTATE BUY': '◈',
    'HOLD': '●', 'WATCH': '○', 'REDUCE': '▽', 'SELL': '✕',
}

STAGE_ORDER = {'主升': 0, '升温': 1, '分歧': 2, '潜伏': 3, '退潮': 4, '弱势': 5}
STAGE_CN = {'main_up': '主升', 'warming': '升温', 'divergence': '分歧',
            'ambush': '潜伏', 'ebbing': '退潮', 'weak': '弱势'}


def load_v2_data(trade_date: str = None) -> Optional[Dict]:
    """从 JSON 缓存加载 V2 数据"""
    # 1. 精确匹配
    json_files = []
    for fname in os.listdir(CACHE_DIR):
        if fname.startswith('theme_stock_map_v2_') and fname.endswith('.json'):
            json_files.append(fname)
    if not json_files:
        print(f"[报告] 未找到 theme_stock_map_v2 JSON 文件")
        return None
    json_files.sort(reverse=True)

    if trade_date:
        target = f'theme_stock_map_v2_{trade_date}.json'
        if target in json_files:
            json_files = [target]
        else:
            print(f"[报告] 未找到 {trade_date} 的数据, 使用最新: {json_files[0]}")
            json_files = [json_files[0]]

    path = os.path.join(CACHE_DIR, json_files[0])
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(f"[报告] 加载: {json_files[0]}")
    return data


class ThemeEssenceReport:
    """
    主题精华报告

    从完整 V2 数据中提取精华内容，生成多格式报告。
    """

    SEPARATOR_H = '══════════════════════════════════════════════════'
    SEPARATOR_L = '──────────────────────────────────────────────────'

    def __init__(self, data: Dict):
        self.data = data
        self.stocks = data.get('stocks', {})
        self.themes = data.get('themes', {})
        self.subtheme_report = data.get('subtheme_report', {})
        self.entry_timing = data.get('entry_timing', {})
        self.top_picks = data.get('top_picks', [])
        self.trade_date = str(data.get('trade_date', ''))
        self.update_time = data.get('update_time', '')

        # 解析子主题矩阵
        self._subtheme_matrix = self._parse_subtheme_matrix()

    def _parse_subtheme_matrix(self) -> Dict[str, List[Dict]]:
        matrix = self.subtheme_report.get('subtheme_matrix', {})
        result = {}
        for parent, subs in matrix.items():
            items = []
            for s in subs:
                stage = s.get('stage', '潜伏')
                score = s.get('score', 0)
                items.append({
                    'name': s.get('name', ''),
                    'stage': stage if stage in STAGE_ORDER else '潜伏',
                    'score': score,
                    'signal': s.get('signal', ''),
                    'pre_rotate': s.get('pre_rotate', False),
                    'n_stocks': s.get('n_stocks', 0),
                    'top_contributors': s.get('top_contributors', []),
                })
            items.sort(key=lambda x: (-x['score'], STAGE_ORDER.get(x['stage'], 99)))
            result[parent] = items
        return result

    # ═══════════════════════════════════════════════════════════
    # 报告核心内容提取
    # ═══════════════════════════════════════════════════════════

    def _get_market_overview(self) -> Dict:
        """市场总览"""
        n_themes = len([k for k in self.data.get('themes', {}).keys()])
        n_subthemes_total = sum(len(subs) for subs in self._subtheme_matrix.values())
        n_stocks_with_role = sum(1 for s in self.stocks.values() if s.get('role'))
        n_top_picks = len(self.top_picks)

        # 市场状态
        regimes = set()
        for parent, subs in self.entry_timing.items():
            for sub_name, sub_data in subs.items():
                mr = sub_data.get('market_regime', '')
                if mr:
                    regimes.add(mr)
        market_regime = ', '.join(sorted(regimes)) if regimes else '未知'

        # 活跃子主题（升温+主升）
        n_active = 0
        for subs in self._subtheme_matrix.values():
            for s in subs:
                if s['stage'] in ('升温', '主升'):
                    n_active += 1

        # 买入信号统计
        buy_signals = Counter()
        for s in self.stocks.values():
            sig = s.get('entry_signal', '')
            if sig in ('BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY'):
                buy_signals[sig] += 1

        return {
            'trade_date': self.trade_date,
            'market_regime': market_regime,
            'n_themes': n_themes,
            'n_subthemes': n_subthemes_total,
            'n_active_subthemes': n_active,
            'n_stocks': len(self.stocks),
            'n_stocks_with_role': n_stocks_with_role,
            'n_top_picks': n_top_picks,
            'buy_signals': dict(buy_signals),
        }

    def _get_theme_summary(self, theme_name: str) -> Dict:
        """单主题摘要"""
        subs = self._subtheme_matrix.get(theme_name, [])
        if not subs:
            return None

        # 主题内所有股票
        theme_codes = [code for code, info in self.stocks.items()
                       if theme_name in info.get('themes', [])]

        # 平均评分
        alphas = [self.stocks[c].get('stock_alpha', 0) for c in theme_codes
                  if self.stocks[c].get('stock_alpha')]
        finals = [self.stocks[c].get('final_score', 0) for c in theme_codes
                  if self.stocks[c].get('final_score')]
        trades = [self.stocks[c].get('trade_score', 0) for c in theme_codes
                  if self.stocks[c].get('trade_score')]
        invests = [self.stocks[c].get('investment_score', 0) for c in theme_codes
                   if self.stocks[c].get('investment_score')]
        avg_alpha = round(sum(alphas) / len(alphas), 1) if alphas else 0
        avg_final = round(sum(finals) / len(finals), 1) if finals else 0
        avg_trade = round(sum(trades) / len(trades), 1) if trades else 0
        avg_invest = round(sum(invests) / len(invests), 1) if invests else 0

        # 买入信号数量
        n_buy = sum(1 for c in theme_codes
                    if self.stocks[c].get('entry_signal') in
                    ('BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY'))

        # Top Picks 统计
        theme_picks = [p for p in self.top_picks if p.get('theme') == theme_name]

        # 角色分布
        role_counts = Counter()
        for c in theme_codes:
            role = self.stocks[c].get('role', '')
            if role:
                role_counts[role] += 1

        # 主导阶段
        stage_counter = Counter(s['stage'] for s in subs)
        dominant_stage = max(stage_counter, key=stage_counter.get) if stage_counter else ''

        return {
            'name': theme_name,
            'n_subthemes': len(subs),
            'n_stocks': len(theme_codes),
            'avg_alpha': avg_alpha,
            'avg_final': avg_final,
            'avg_trade': avg_trade,
            'avg_invest': avg_invest,
            'n_buy_signals': n_buy,
            'n_top_picks': len(theme_picks),
            'top_picks': theme_picks[:3],  # 前3
            'dominant_stage': dominant_stage,
            'role_distribution': dict(role_counts.most_common(5)),
            'subthemes': subs,
        }

    def _get_subtheme_stock_detail(self, parent: str, sub_name: str, top_n: int = 5) -> List[Dict]:
        """子主题内 Top 股票详情"""
        stocks_in = []
        for code, info in self.stocks.items():
            if info.get('subtheme') != sub_name:
                continue
            if parent not in info.get('themes', []):
                continue

            entry = self.entry_timing.get(parent, {}).get(sub_name, {}).get('stocks', {}).get(code, {})
            stocks_in.append({
                'code': code,
                'name': info.get('name', ''),
                'role': info.get('role', ''),
                'stock_alpha': info.get('stock_alpha', 0),
                'final_score': info.get('final_score', 0),
                'entry_signal': entry.get('entry_signal', info.get('entry_signal', '')),
                'entry_score': entry.get('entry_score', info.get('entry_score', 0)),
                'trade_score': entry.get('trade_score', info.get('trade_score', 0)),
                'investment_score': entry.get('investment_score', info.get('investment_score', 0)),
                'risk_level': entry.get('risk_level', info.get('risk_level', '')),
                'holding_priority': entry.get('holding_priority', info.get('holding_priority', 0)),
                'leader_similarity': info.get('leader_similarity', 0),
                'role_score': info.get('role_score', 0),
            })

        stocks_in.sort(key=lambda x: (
            - (SIGNAL_PRIORITY_TXT.get(x['entry_signal'], 0)),
            - x['final_score']
        ))
        return stocks_in[:top_n]

    def _get_theme_orphan_stocks(self, parent: str, top_n: int = 5) -> List[Dict]:
        """获取属于主题但子主题未匹配任何本主题子主题的股票（兜底展示）"""
        sub_names = {s['name'] for s in self._subtheme_matrix.get(parent, [])}
        if not sub_names:
            return []

        orphans = []
        for code, info in self.stocks.items():
            if parent not in info.get('themes', []):
                continue
            st = info.get('subtheme', '')
            if st and st in sub_names:
                continue  # 已有匹配子主题，跳过

            entry = {}
            if st:
                entry = self.entry_timing.get(parent, {}).get(st, {}).get('stocks', {}).get(code, {})
            orphans.append({
                'code': code,
                'name': info.get('name', ''),
                'subtheme': st if st else '(无)',
                'role': info.get('role', ''),
                'stock_alpha': info.get('stock_alpha', 0),
                'final_score': info.get('final_score', 0),
                'entry_signal': entry.get('entry_signal', info.get('entry_signal', '')),
                'trade_score': entry.get('trade_score', info.get('trade_score', 0)),
                'investment_score': entry.get('investment_score', info.get('investment_score', 0)),
                'holding_priority': entry.get('holding_priority', info.get('holding_priority', 0)),
            })

        orphans.sort(key=lambda x: -x['final_score'])
        return orphans[:top_n]

    # ═══════════════════════════════════════════════════════════
    # 文本报告
    # ═══════════════════════════════════════════════════════════

    def to_text(self) -> str:
        """生成纯文本精华报告"""
        lines = []
        overview = self._get_market_overview()

        # ── 标题 ──
        lines.append(self.SEPARATOR_H)
        lines.append(f"  主题精华报告 - {overview['trade_date']}")
        lines.append(self.SEPARATOR_H)
        lines.append("")

        # ── 市场总览 ──
        lines.append("【市场总览】")
        lines.append(f"  市场状态     : {overview['market_regime']}")
        lines.append(f"  活跃主题     : {overview['n_themes']} 个")
        lines.append(f"  子主题数量   : {overview['n_subthemes']} 个")
        lines.append(f"  活跃子主题   : {overview['n_active_subthemes']} 个（升温+主升）")
        lines.append(f"  覆盖股票     : {overview['n_stocks']} 只")
        lines.append(f"  Top Picks   : {overview['n_top_picks']} 个")
        lines.append("")
        lines.append(f"  买入信号统计:")
        for sig, cnt in overview['buy_signals'].items():
            lines.append(f"    {SIGNAL_SYMBOL.get(sig, '')} {sig:<18}: {cnt}")
        lines.append("")

        # ── 主题状态总结 ──
        # 按子主题强势度分组，上升的在前
        stage_order_rising = {'主升': 0, '升温': 1, '分歧': 2}
        rising_groups = defaultdict(list)
        for tn in self._subtheme_matrix:
            subs = self._subtheme_matrix[tn]
            summary = self._get_theme_summary(tn)
            if not summary:
                continue
            best_stage = None
            for s in subs:
                if s['stage'] in stage_order_rising:
                    if best_stage is None or stage_order_rising.get(s['stage'], 99) < stage_order_rising.get(best_stage, 99):
                        best_stage = s['stage']
            if best_stage:
                rising_groups[best_stage].append((tn, summary, subs))
        lines.append("")
        lines.append("【主题状态总结 — 按子主题热度排序】")
        for stage in ('主升', '升温', '分歧'):
            items = rising_groups.get(stage, [])
            if not items:
                continue
            lines.append(f"  {STAGE_SYMBOL.get(stage,'')} {stage}:")
            for tn, summary, subs in items:
                hot_subs = [s for s in subs if s['stage'] in stage_order_rising and 
                           stage_order_rising.get(s['stage'], 99) <= stage_order_rising.get(stage, 99)]
                sub_detail = '、'.join([f"{s['name']}({s['score']:.0f}分)" for s in hot_subs[:3]])
                lines.append(f"    {tn:<12} 子主题: {sub_detail}  "
                             f"股票{summary['n_stocks']}只 α均{summary['avg_alpha']}")
            lines.append("")

        # ── 各主题 ──
        theme_names = list(self._subtheme_matrix.keys())
        for idx, tn in enumerate(theme_names, 1):
            summary = self._get_theme_summary(tn)
            if not summary:
                continue

            lines.append(self.SEPARATOR_L)
            stage_mark = STAGE_SYMBOL.get(summary['dominant_stage'], '')
            lines.append(f"  {idx:2d}. {tn} {stage_mark}")
            lines.append(f"      子主题:{summary['n_subthemes']}个 | "
                         f"股票:{summary['n_stocks']}只 | "
                         f"α均:{summary['avg_alpha']} | "
                         f"F均:{summary['avg_final']} | "
                         f"T均:{summary['avg_trade']} I均:{summary['avg_invest']} | "
                         f"买入:{summary['n_buy_signals']}个")
            lines.append(f"      角色分布: {summary['role_distribution']}")
            lines.append("")

            # 子主题明细
            for s in summary['subthemes']:
                stage_sym = STAGE_SYMBOL.get(s['stage'], '')
                flag = '★' if s['pre_rotate'] else ''
                lines.append(f"    ├─ {s['name']:<12} {stage_sym} {s['stage']} "
                             f"[{s['score']:.0f}分] {flag}")

                # Top 股票
                top_stocks = self._get_subtheme_stock_detail(tn, s['name'], top_n=4)
                for stk in top_stocks:
                    sig_sym = SIGNAL_SYMBOL.get(stk['entry_signal'], ' ')
                    prio = '★' * stk['holding_priority'] if stk['holding_priority'] else ''
                    lines.append(f"    │   {sig_sym} {stk['name']:<6}({stk['code']:.9s}) "
                                 f"{stk['role']:<10} α{stk['stock_alpha']:.0f} "
                                 f"F{stk['final_score']:.0f} "
                                 f"T{stk['trade_score']:.0f} I{stk['investment_score']:.0f} "
                                 f"[{stk['entry_signal']}] {prio}")

                # Top Picks 标注
                theme_picks = summary.get('top_picks', [])
                for pick in theme_picks:
                    if pick.get('subtheme') == s['name']:
                        ts = pick.get('trade_score', pick.get('final_score', 0))
                        lines.append(f"    │   >>> TOP PICK: {pick['name']} {pick['role']} "
                                     f"T={ts:.0f} F={pick['final_score']:.0f} α={pick['stock_alpha']:.0f}")
                lines.append("")

            # 主题 Top Picks 汇总（按 Trade Score）
            if summary['top_picks']:
                lines.append(f"    主题 Top Picks:")
                for pick in sorted(summary['top_picks'], 
                                   key=lambda x: -(x.get('trade_score', x.get('final_score', 0))))[:3]:
                    ts = pick.get('trade_score', pick.get('final_score', 0))
                    # 优先用已丰富的 entry_signal，再回退到 stock 查找，最后用合成信号
                    actual_signal = (pick.get('entry_signal') or 
                                     self.stocks.get(pick.get('code', ''), {}).get('entry_signal') or
                                     pick.get('signal', ''))
                    lines.append(f"      → {pick['name']:<6} {pick['role']:<10} "
                                 f"α={pick['stock_alpha']:.0f} T={ts:.0f} "
                                 f"{SIGNAL_SYMBOL.get(actual_signal,'')} {actual_signal}")
                lines.append("")

            # ── 兜底：跨主题未分配子主题的股票 ──
            orphans = self._get_theme_orphan_stocks(tn, top_n=5)
            if orphans:
                lines.append(f"    ├─ 其他(跨主题)     [兜底]")
                for stk in orphans:
                    sig_sym = SIGNAL_SYMBOL.get(stk['entry_signal'], ' ')
                    prio = '★' * stk['holding_priority'] if stk['holding_priority'] else ''
                    lines.append(f"    │   {sig_sym} {stk['name']:<6}({stk['code']:.9s}) "
                                 f"{stk['role']:<10} α{stk['stock_alpha']:.0f} "
                                 f"F{stk['final_score']:.0f} "
                                 f"[{stk['entry_signal']}] {prio} "
                                 f"子主题={stk['subtheme']}")
                lines.append("")

        # ── 全市场 Top Picks（按 Trade Score 排序） ──
        lines.append(self.SEPARATOR_H)
        lines.append("【全市场 Top Picks - 按 Trade Score 排序】")
        lines.append(self.SEPARATOR_H)
        sorted_picks = sorted(self.top_picks, key=lambda x: -(x.get('trade_score', x.get('final_score', 0))))
        for idx, pick in enumerate(sorted_picks[:20], 1):
            # 优先用已丰富的 entry_signal，再回退到 stock 查找
            actual_signal = (pick.get('entry_signal') or 
                             self.stocks.get(pick.get('code', ''), {}).get('entry_signal') or
                             pick.get('signal', ''))
            sig_sym = SIGNAL_SYMBOL.get(actual_signal, '')
            prio = '★' * (pick.get('holding_priority', 0) or
                          self.stocks.get(pick.get('code', ''), {}).get('holding_priority', 0) or 0)
            ts = pick.get('trade_score', pick.get('final_score', 0))
            lines.append(f"  {idx:2d}. {pick['name']:<6} "
                         f"T={ts:.0f} α={pick['stock_alpha']:.0f} "
                         f"{pick['role']:<10} {pick['subtheme']:<10} "
                         f"[{actual_signal}] {prio}")

        # ── 买入信号 Top 20（按 Trade Score 排序，与 Top Picks 天然一致） ──
        lines.append("")
        lines.append(self.SEPARATOR_H)
        lines.append("【买入信号 Top 20 - 按 Trade Score 排序】")
        lines.append(self.SEPARATOR_H)
        buy_list = []
        for code, info in self.stocks.items():
            sig = info.get('entry_signal', '')
            if sig in ('BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY'):
                ts = info.get('trade_score', info.get('final_score', 0))
                buy_list.append((code, info, ts))
        buy_list.sort(key=lambda x: -x[2])  # 按 Trade Score
        for idx, (code, info, ts) in enumerate(buy_list[:20], 1):
            sig_sym = SIGNAL_SYMBOL.get(info.get('entry_signal', ''), '')
            prio = '★' * info.get('holding_priority', 0)
            lines.append(f"  {idx:2d}. {sig_sym} {info.get('name',''):<6}({code:.9s}) "
                         f"T={ts:.0f} I={info.get('investment_score',0):.0f} "
                         f"α={info.get('stock_alpha',0):.0f} "
                         f"{info.get('role',''):<10} {info.get('subtheme',''):<10} "
                         f"[{info.get('entry_signal','')}] {prio}")

        lines.append("")
        lines.append(self.SEPARATOR_H)
        lines.append(f"  报告生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(self.SEPARATOR_H)

        return '\n'.join(lines)

    # ═══════════════════════════════════════════════════════════
    # Markdown 报告（适合飞书等平台）
    # ═══════════════════════════════════════════════════════════

    def to_markdown(self) -> str:
        """生成 Markdown 格式报告"""
        lines = []
        overview = self._get_market_overview()

        lines.append(f"# 主题精华报告 - {overview['trade_date']}\n")

        lines.append("## 市场总览\n")
        lines.append(f"- 市场状态: **{overview['market_regime']}**")
        lines.append(f"- 活跃主题: {overview['n_themes']} 个")
        lines.append(f"- 子主题: {overview['n_subthemes']} 个（活跃: {overview['n_active_subthemes']} 个）")
        lines.append(f"- 覆盖股票: {overview['n_stocks']} 只")
        lines.append(f"- Top Picks: {overview['n_top_picks']} 个\n")

        lines.append("### 买入信号分布\n")
        lines.append("| 信号 | 数量 |")
        lines.append("|------|------|")
        for sig, cnt in sorted(overview['buy_signals'].items(), key=lambda x: -x[1]):
            lines.append(f"| {sig} | {cnt} |")
        lines.append("")

        # ── Markdown 主题状态总结 ──
        stage_order_rising = {'主升': 0, '升温': 1, '分歧': 2}
        rising_groups = defaultdict(list)
        for tn in self._subtheme_matrix:
            subs = self._subtheme_matrix[tn]
            summary = self._get_theme_summary(tn)
            if not summary:
                continue
            best_stage = None
            for s in subs:
                if s['stage'] in stage_order_rising:
                    if best_stage is None or stage_order_rising.get(s['stage'], 99) < stage_order_rising.get(best_stage, 99):
                        best_stage = s['stage']
            if best_stage:
                rising_groups[best_stage].append((tn, summary, subs))
        lines.append("## 主题状态总结 — 按子主题热度排序\n")
        for stage in ('主升', '升温', '分歧'):
            items = rising_groups.get(stage, [])
            if not items:
                continue
            lines.append(f"### {STAGE_SYMBOL.get(stage,'')} {stage}\n")
            for tn, summary, subs in items:
                hot_subs = [s for s in subs if s['stage'] in stage_order_rising and 
                           stage_order_rising.get(s['stage'], 99) <= stage_order_rising.get(stage, 99)]
                sub_detail = '、'.join([f"`{s['name']}`({s['score']:.0f}分)" for s in hot_subs[:3]])
                lines.append(f"- **{tn}**: {sub_detail}  "
                             f"（股票{summary['n_stocks']}只, α均{summary['avg_alpha']}）")
            lines.append("")

        for tn in self._subtheme_matrix.keys():
            summary = self._get_theme_summary(tn)
            if not summary:
                continue

            stage_mark = STAGE_SYMBOL.get(summary['dominant_stage'], '')
            lines.append(f"## {tn} {stage_mark}\n")
            lines.append(f"子主题 {summary['n_subthemes']} 个 | 股票 {summary['n_stocks']} 只 | "
                         f"α均{summary['avg_alpha']} | F均{summary['avg_final']} | "
                         f"T均{summary['avg_trade']} I均{summary['avg_invest']} | "
                         f"买入 {summary['n_buy_signals']} 个\n")

            for s in summary['subthemes']:
                stage_sym = STAGE_SYMBOL.get(s['stage'], '')
                flag = '★' if s['pre_rotate'] else ''
                lines.append(f"### {stage_sym} {s['name']} `{s['stage']}` [{s['score']:.0f}分] {flag}\n")

                top_stocks = self._get_subtheme_stock_detail(tn, s['name'], top_n=5)
                lines.append("| 代码 | 名称 | 角色 | α | F | T | I | 信号 |")
                lines.append("|------|------|------|----|----|----|----|------|")
                for stk in top_stocks:
                    lines.append(f"| {stk['code']:.9s} | {stk['name']} | {stk['role']} | "
                                 f"{stk['stock_alpha']:.0f} | {stk['final_score']:.0f} | "
                                 f"{stk['trade_score']:.0f} | {stk['investment_score']:.0f} | "
                                 f"{stk['entry_signal']} |")
                lines.append("")

            # Top Picks
            if summary['top_picks']:
                lines.append("**主题 Top Picks:**\n")
                for pick in sorted(summary['top_picks'], 
                                   key=lambda x: -(x.get('trade_score', x.get('final_score', 0))))[:3]:
                    ts = pick.get('trade_score', pick.get('final_score', 0))
                    actual_signal = (pick.get('entry_signal') or 
                                     self.stocks.get(pick.get('code', ''), {}).get('entry_signal') or
                                     pick.get('signal', ''))
                    lines.append(f"- {pick['name']} `{pick['role']}` "
                                 f"α={pick['stock_alpha']:.0f} T={ts:.0f} "
                                 f"Signal={actual_signal}")
                lines.append("")

            # 跨主题兜底股票
            orphans = self._get_theme_orphan_stocks(tn, top_n=5)
            if orphans:
                lines.append("**跨主题股票（子主题非本主题）:**\n")
                lines.append("| 代码 | 名称 | 角色 | α | F | 子主题 | 信号 |")
                lines.append("|------|------|------|----|----|--------|------|")
                for stk in orphans:
                    lines.append(f"| {stk['code']:.9s} | {stk['name']} | {stk['role']} | "
                                 f"{stk['stock_alpha']:.0f} | {stk['final_score']:.0f} | "
                                 f"{stk['subtheme']} | {stk['entry_signal']} |")
                lines.append("")

        lines.append("---\n")
        lines.append(f"_报告生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")

        return '\n'.join(lines)

    # ═══════════════════════════════════════════════════════════
    # 保存报告
    # ═══════════════════════════════════════════════════════════

    def save_to_file(self, prefix: str = None) -> Dict[str, str]:
        """保存多格式报告

        Returns:
          {format: filepath} 字典
        """
        td = self.trade_date
        f_base = f"theme_essence_{td}"
        if prefix:
            f_base = f"{prefix}_{f_base}"

        txt_path = os.path.join(REPORT_DIR, f"{f_base}.txt")
        md_path = os.path.join(REPORT_DIR, f"{f_base}.md")

        # 文本
        txt = self.to_text()
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(txt)
        print(f"[报告] 文本报告 → {txt_path}")

        # Markdown
        md = self.to_markdown()
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md)
        print(f"[报告] Markdown报告 → {md_path}")

        return {'txt': txt_path, 'md': md_path}


# ── 信号优先级查找表 ──
SIGNAL_PRIORITY_TXT = {
    'BREAKOUT BUY': 95, 'PULLBACK BUY': 85, 'PRE_ROTATE BUY': 75,
    'HOLD': 60, 'WATCH': 35, 'REDUCE': 20, 'SELL': 5, '': 0,
}


# ═══════════════════════════════════════════════════════════
# 统一入口函数（由 tushare_quant.py 调用）
# ═══════════════════════════════════════════════════════════

def generate_theme_essence_report(trade_date: str = None, prefix: str = None) -> Optional[Dict]:
    """
    生成主题精华报告 统一入口

    用法:
      from tushare_quant import generate_theme_essence_report
      report = generate_theme_essence_report('20260724')

    参数:
      trade_date: 日期 YYYYMMDD，None=使用最新
      prefix: 文件名前缀

    返回:
      {'txt': 文件路径, 'md': 文件路径} 或 None
    """
    print(f"\n{'='*50}")
    print(f"  生成主题精华报告")
    print(f"{'='*50}")

    data = load_v2_data(trade_date)
    if data is None:
        print("[报告] 无数据，跳过")
        return None
    trade_date = data.get('trade_date', trade_date)

    reporter = ThemeEssenceReport(data)
    paths = reporter.save_to_file(prefix=prefix)

    # 打印简短预览
    overview = reporter._get_market_overview()
    print(f"\n  报告摘要:")
    print(f"    日期: {overview['trade_date']}")
    print(f"    市场: {overview['market_regime']}")
    print(f"    主题: {overview['n_themes']} 个 / 子主题: {overview['n_subthemes']} 个")
    print(f"    买入信号: {sum(overview['buy_signals'].values())} 个")
    print(f"    Top Picks: {overview['n_top_picks']} 个")
    print(f"  文件: {paths['txt']}")
    print(f"        {paths['md']}")

    return paths


def print_essence_preview(trade_date: str = None, top_picks: int = 20):
    """直接打印精华报告预览到控制台"""
    data = load_v2_data(trade_date)
    if data is None:
        return

    reporter = ThemeEssenceReport(data)
    lines = reporter.to_text().split('\n')

    # 只打印前几段 + Top Picks
    print('\n'.join(lines[:5]))  # 标题
    for i, line in enumerate(lines):
        if '【全市场 Top Picks' in line:
            print('\n'.join(lines[5:i+1]))
            print('\n'.join(lines[i:i+top_picks+3]))
            break


if __name__ == '__main__':
    generate_theme_essence_report()
