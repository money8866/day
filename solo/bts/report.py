# -*- coding: utf-8 -*-
"""
BTS Markdown 报告生成：bts_report_YYYYMMDD.md
"""
import os
import datetime

import pandas as pd

from .config import REPORT_DIR


def _ensure_dir():
    os.makedirs(REPORT_DIR, exist_ok=True)


def write_daily_report(df: pd.DataFrame, date: str, regime: str = 'neutral'):
    _ensure_dir()
    path = os.path.join(REPORT_DIR, f'bts_report_{date}.md')
    lines = []
    lines.append(f'# BTS 趋势启动选股报告 {date}')
    lines.append('')
    lines.append(f'> 生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 市场环境：{regime}')
    lines.append('')
    lines.append('## 说明')
    lines.append('')
    lines.append('本报告由 BTS（Breakout Trend Start）引擎自动生成。目标形态：')
    lines.append('震荡平台 → 放量突破 → 突破后不跌回平台 → MA5 拐头向上 → 量能持续 → 股价沿 MA5 运行。')
    lines.append('**不是涨幅榜**：远离 MA5、已连续大涨的股票会被降级或禁止追高。')
    lines.append('')
    if df.empty:
        lines.append('当日无有效信号。')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f'[报告] 已写入 {path}')
        return

    # 分组
    # V1.2/V1.7 买入池：buy_point 为 BUY-A/B/C（已含 Day1 且 S/A/B 强过滤 + 持续确认买点）
    pool = df[df['buy_point'].isin(('BUY-A', 'BUY-B', 'BUY-C'))].sort_values('entry', ascending=False)
    top = df.head(20)
    pullbacks = df[df['signal'] == 'PULLBACK_BUY'].head(10)
    extended = df[df['signal'].isin(('TREND_EXTENDED', 'FAILED_BREAKOUT'))].head(10)

    lines.append('## TOP 20')
    lines.append('')
    for i, (_, r) in enumerate(top.iterrows(), 1):
        lines.append(f'{i}. **{r["name"]} {r["ts_code"]}**  BTS {r["bts"]:.0f} | Entry {r["entry"]:.0f} | '
                     f'`{r["signal_cn"]}` | {r["grade"]}级')
        lines.append(f'   - 突破日 {r["breakout_date"]}，突破后 {r["days_after"]} 日 | 距MA5 {r["dist_ma5"] * 100:+.1f}% | '
                     f'量比 {r["vol_ratio"]:.2f} | 量持续 {r["persist"]}/5 | 涨跌量比 {r["up_dn"]:.2f}')
        lines.append(f'   - 核心原因：{r["core"]}')
    lines.append('')

    lines.append('## 买入池（Day1 或 持续确认且 S/A/B）')
    lines.append('')
    if pool.empty:
        lines.append('无。')
    else:
        lines.append('| 代码 | 名称 | BTS | Entry | 等级 | 信号 | 买点 | 状态 | 突破日 | 后N日 | 距MA5 | 量比 | 量持续 |')
        lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for _, r in pool.head(30).iterrows():
            tag = ('主线' if r.get('is_mainline', False) else '') + ('持续' if r.get('sustained_ok', False) else '')
            lines.append(f'| {r["ts_code"]} | {r["name"]} | {r["bts"]:.1f} | {r["entry"]:.1f} | {r["grade"]} | '
                         f'{r["signal_cn"]}{tag} | {r["buy_point"]} | {r.get("status", "NEW")} | {r["breakout_date"]} | {r["days_after"]} | '
                         f'{r["dist_ma5"] * 100:+.1f}% | {r["vol_ratio"]:.2f} | {r["persist"]}/5 |')
    lines.append('')

    lines.append('## 重点新信号（NEW）')
    lines.append('')
    news = df[df.get("status", "NEW") == "NEW"]
    if news.empty:
        lines.append('（当日扫描中今日首次进入 BTS 的信号）')
        lines.append('无。')
    else:
        for _, r in news.head(15).iterrows():
            lines.append(f'- {r["name"]} {r["ts_code"]} BTS {r["bts"]:.1f} Entry {r["entry"]:.1f} '
                         f'`{r["signal_cn"]}` 突破后{r["days_after"]}日 距MA5 {r["dist_ma5"] * 100:+.1f}%')
    lines.append('')

    lines.append('## 升级/降级（UPGRADE / DOWNGRADE）')
    lines.append('')
    ups = df[df.get("status", "") == "UPGRADE"]
    downs = df[df.get("status", "") == "DOWNGRADE"]
    if ups.empty and downs.empty:
        lines.append('无。')
    else:
        for _, r in ups.head(8).iterrows():
            lines.append(f'- [↑升级] {r["name"]} {r["ts_code"]} Entry {r["entry"]:.1f} `{r["signal_cn"]}`')
        for _, r in downs.head(8).iterrows():
            lines.append(f'- [↓降级] {r["name"]} {r["ts_code"]} Entry {r["entry"]:.1f} `{r["signal_cn"]}`')
    lines.append('')

    lines.append('## 回踩买点（PULLBACK_BUY）')
    lines.append('')
    if pullbacks.empty:
        lines.append('无。')
    else:
        for _, r in pullbacks.iterrows():
            lines.append(f'- {r["name"]} {r["ts_code"]} BTS {r["bts"]:.1f} 突破后{r["days_after"]}日回踩MA5 距MA5 {r["dist_ma5"] * 100:+.1f}%')
    lines.append('')

    lines.append('## 过度扩张 / 禁止追高')
    lines.append('')
    if extended.empty:
        lines.append('无。')
    else:
        for _, r in extended.iterrows():
            lines.append(f'- {r["name"]} {r["ts_code"]} `{r["signal_cn"]}` BTS {r["bts"]:.1f} 距MA5 {r["dist_ma5"] * 100:+.1f}%')
    lines.append('')

    lines.append('---')
    lines.append('> 风险提示：本报告仅为技术形态量化输出，不构成投资建议。股市有风险，入市需谨慎。')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'[报告] 已写入 {path}')


def write_backtest_report(df: pd.DataFrame, start: str, end: str):
    _ensure_dir()
    path = os.path.join(REPORT_DIR, f'bts_backtest_{start}_{end}.md')
    from .scanner import backtest_stats
    lines = [f'# BTS 历史回测报告 {start} ~ {end}', '']
    lines.append(f'> 生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 信号总数：{len(df)}')
    lines.append('')
    if df.empty:
        lines.append('无信号。')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return
    overall = backtest_stats(df)
    lines.append('## 整体表现')
    lines.append('')
    lines.append('| 指标 | 3日 | 5日 | 10日 | 20日 |')
    lines.append('| --- | --- | --- | --- | --- |')
    for h in ('fut3', 'fut5', 'fut10', 'fut20'):
        lines.append(f'| {h.upper()} 平均收益 | {overall.get(h + "_mean", float("nan")):+.2f}% | {overall.get(h + "_mean", float("nan")):+.2f}% |'
                     f' {overall.get(h + "_mean", float("nan")):+.2f}% | {overall.get(h + "_mean", float("nan")):+.2f}% |')
    lines.append(f'| 5日胜率 | | {overall.get("fut5_win", float("nan")):.1f}% | | |')
    lines.append(f'| 盈亏比 | | {overall.get("profit_ratio", float("nan")):.2f} | | |')
    lines.append(f'| Profit Factor | | {overall.get("profit_factor", float("nan")):.2f} | | |')
    lines.append(f'| 最大回撤 | | {overall.get("max_dd", float("nan")):.2f}% | | |')
    lines.append('')
    lines.append('## 分等级表现')
    lines.append('')
    lines.append('| 等级 | 数量 | 5日均值 | 5日胜率 | 10日均值 | 20日均值 | 盈亏比 |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- |')
    for gr in ('S', 'A', 'B', 'C'):
        st = backtest_stats(df, gr)
        if st.get('n'):
            lines.append(f'| {gr} | {st["n"]} | {st.get("fut5_mean", float("nan")):+.2f}% | '
                         f'{st.get("fut5_win", float("nan")):.1f}% | {st.get("fut10_mean", float("nan")):+.2f}% | '
                         f'{st.get("fut20_mean", float("nan")):+.2f}% | {st.get("profit_ratio", float("nan")):.2f} |')
    lines.append('')
    lines.append('## BTS 分数分档单调性')
    lines.append('')
    lines.append('| BTS区间 | 数量 | 5日均值 | 10日均值 | 20日均值 |')
    lines.append('| --- | --- | --- | --- | --- |')
    for lo, hi in ((85, 101), (78, 85), (70, 78), (60, 70), (50, 60)):
        g = df[(df['bts'] >= lo) & (df['bts'] < hi)]
        if len(g):
            st = backtest_stats(g)
            lines.append(f'| [{lo},{hi}) | {len(g)} | {st.get("fut5_mean", float("nan")):+.2f}% | '
                         f'{st.get("fut10_mean", float("nan")):+.2f}% | {st.get("fut20_mean", float("nan")):+.2f}% |')
    lines.append('')
    lines.append('> 防未来数据声明：所有信号仅使用 T 日及之前数据；未来收益仅作为 label 统计。')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'[报告] 已写入 {path}')
