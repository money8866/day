# -*- coding: utf-8 -*-
"""
PRB Markdown 报告生成：prb_report_YYYYMMDD.md
按 spec 第十七节输出格式：每只股票四段完整字段 + 状态 + 交易结论
"""
import os
import datetime

import pandas as pd

from .config import REPORT_DIR


def _ensure_dir():
    os.makedirs(REPORT_DIR, exist_ok=True)


def _fmt_date(s):
    s = str(s)
    if len(s) == 8:
        return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    return s


def write_daily_report(df: pd.DataFrame, date: str, regime: str = 'neutral'):
    _ensure_dir()
    path = os.path.join(REPORT_DIR, f'prb_report_{date}.md')
    lines = []
    lines.append(f'# PRB 平台突破回踩再启动 买点报告 {date}')
    lines.append('')
    lines.append(f'> 生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 市场环境：{regime}')
    lines.append('')
    lines.append('## 说明')
    lines.append('')
    lines.append('本报告由 PRB（Platform-Reacceleration Breakout）引擎生成。目标形态：')
    lines.append('有效平台(≥75分) -> 有效突破(≥75分) -> 第一次健康缩量回踩 -> 重新转强 -> PRIMARY BUY。')
    lines.append('**不是涨幅榜**：未回踩直接主升（BTS 引擎负责）、回踩放量、回踩过深、二次上涨的股票一律不给出 PRIMARY BUY。')
    lines.append('')
    if df.empty:
        lines.append('当日无有效信号。')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f'[报告] 已写入 {path}')
        return

    # ═══ PRIMARY BUY 买点池（第十七节完整输出格式）═══
    primary = df[df['action'] == 'PRIMARY_BUY'].sort_values('final_score', ascending=False)
    lines.append('## PRIMARY BUY（S级主买点）')
    lines.append('')
    if primary.empty:
        lines.append('无。')
    else:
        for _, r in primary.head(10).iterrows():
            lines.append(f'### {r["name"]} {r["ts_code"]}')
            lines.append('')
            lines.append(f'- 代码：{r["ts_code"]}')
            lines.append(f'- 名称：{r["name"]}')
            lines.append(f'- 平台：{_fmt_date(r["platform_start"])} ~ {_fmt_date(r["platform_end"])}')
            lines.append(f'- 平台高点：{r["platform_high"]:.2f}')
            lines.append(f'- 平台低点：{r["platform_low"]:.2f}')
            lines.append(f'- 平台幅度：{r["platform_range"] * 100:.1f}%')
            lines.append(f'- 平台持续天数：{r["platform_days"]}')
            lines.append(f'- 平台测试次数：{r["resistance_tests"]}次（上沿） / {r["support_tests"]}次（下沿）')
            lines.append(f'- Platform Score：{r["platform_score"]:.1f}（{r["platform_grade"]}级）')
            lines.append('')
            lines.append(f'- 突破日期：{_fmt_date(r["breakout_date"])}')
            lines.append(f'- 突破价格：{r["breakout_price"]:.2f}')
            lines.append(f'- 突破幅度：+{r["breakout_pct"] * 100:.1f}%')
            lines.append(f'- 突破量比：{r["breakout_vr"]:.2f}')
            lines.append(f'- 收盘位置：{r["breakout_candle_pos"] * 100:.0f}%')
            lines.append(f'- Breakout Score：{r["breakout_score"]:.1f}（{r["breakout_grade"]}）')
            lines.append('')
            lines.append(f'- 回踩开始：{_fmt_date(r["pullback_start"])}' if str(r.get('pullback_start', '')) else '- 回踩：未发生')
            lines.append(f'- 回踩最低：{r["pullback_low"]:.2f}')
            lines.append(f'- 回踩幅度：{r["pullback_depth"] * 100:.0f}%')
            lines.append(f'- 回踩天数：{r["pullback_days"]}')
            lines.append(f'- 回踩量/突破量：{r["pullback_vol_ratio"]:.2f}')
            lines.append(f'- 是否跌破突破位：{"是" if r.get("pullback_below_bl", False) else "否"}')
            lines.append(f'- Pullback Score：{r["pullback_score"]:.1f}')
            lines.append('')
            lines.append(f'- 重新转强日期：{_fmt_date(r.get("reaccel_date", r["date"]))}')
            lines.append(f'- 重新转强价格：{r["reaccel_price"]:.2f}')
            lines.append(f'- 重新转强量比：{r["reaccel_vol_ratio"]:.2f}')
            lines.append('')
            lines.append(f'- **最终评分：{r["final_score"]:.1f}**')
            lines.append(f'- **评级：{r["grade"]}（{r["grade_cn"]}）**')
            lines.append(f'- **状态：{r["state"]}**')
            lines.append(f'- **交易结论：{r["action"]}（{r["action_cn"]}）**')
            if r.get('forbidden'):
                lines.append(f'- ⚠️ 严禁买入检查：{r["forbidden"]}')
            lines.append('')
    lines.append('')

    # ═══ 状态机分布 ═══
    lines.append('## 状态机分布')
    lines.append('')
    if 'state_cn' in df.columns:
        dist = df.groupby(['state_cn', 'action_cn']).size().reset_index(name='n').sort_values('n', ascending=False)
        lines.append('| 状态 | 交易结论 | 数量 |')
        lines.append('| --- | --- | --- |')
        for _, r in dist.iterrows():
            lines.append(f'| {r["state_cn"]} | {r["action_cn"]} | {r["n"]} |')
    lines.append('')

    # ═══ 各阶段 TOP ═══
    lines.append('## 四段评分 TOP 20（按最终评分）')
    lines.append('')
    lines.append('| 代码 | 名称 | 平台分 | 突破分 | 回踩分 | 总分 | 级 | 状态 | 结论 | 突破日 | 回深 | 回/突量 |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for _, r in df.head(20).iterrows():
        lines.append(f'| {r["ts_code"]} | {r["name"]} | {r["platform_score"]:.1f} | {r["breakout_score"]:.1f} | '
                     f'{r["pullback_score"]:.1f} | {r["final_score"]:.1f} | {r["grade"]} | '
                     f'{r["state_cn"]} | {r["action_cn"]} | {r["breakout_date"]} | '
                     f'{r["pullback_depth"] * 100:.0f}% | {r["pullback_vol_ratio"]:.2f} |')
    lines.append('')

    # ═══ 待触发的关键阶段（接近买点）═══
    near = df[df['action'].isin(('WAIT_REACCELERATION', 'WAIT_PULLBACK')) & (df['final_score'] >= 70)]
    lines.append('## 接近买点（WAIT_REACCELERATION / WAIT_PULLBACK 且总分≥70）')
    lines.append('')
    if near.empty:
        lines.append('无。')
    else:
        for _, r in near.head(15).iterrows():
            lines.append(f'- **{r["name"]} {r["ts_code"]}** 总分 {r["final_score"]:.1f} | '
                         f'平台 {r["platform_score"]:.0f} 突破 {r["breakout_score"]:.0f} 回踩 {r["pullback_score"]:.0f} | '
                         f'{r["state_cn"]} | {r["action_reason"]}')
    lines.append('')

    lines.append('---')
    lines.append('> 风险提示：本报告仅为技术形态量化输出，不构成投资建议。股市有风险，入市需谨慎。')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'[报告] 已写入 {path}')


def write_backtest_report(df: pd.DataFrame, start: str, end: str):
    _ensure_dir()
    path = os.path.join(REPORT_DIR, f'prb_backtest_{start}_{end}.md')
    from .scanner import backtest_stats
    lines = [f'# PRB 历史回测报告 {start} ~ {end}', '']
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
    lines.append(f'- 信号数：{overall["n"]}')
    lines.append(f'- 未来5日：均值 {overall["fut5_mean"]:+.2f}% | 胜率 {overall["fut5_win"]:.1f}%')
    lines.append(f'- 未来10日：均值 {overall["fut10_mean"]:+.2f}% | 胜率 {overall["fut10_win"]:.1f}%')
    lines.append(f'- 未来20日：均值 {overall["fut20_mean"]:+.2f}% | 胜率 {overall["fut20_win"]:.1f}%')
    lines.append('')
    lines.append('## 按交易结论')
    lines.append('')
    lines.append('| 结论 | 数量 | 5日均值 | 5日胜率 | 10日均值 | 20日均值 |')
    lines.append('| --- | --- | --- | --- | --- | --- |')
    for act in ('PRIMARY_BUY', 'EARLY_BUY', 'CONFIRMED_BUY', 'WAIT_PULLBACK', 'WAIT_REACCELERATION'):
        g = df[df['action'] == act]
        if len(g):
            st = backtest_stats(g)
            lines.append(f'| {act} | {len(g)} | {st["fut5_mean"]:+.2f}% | {st["fut5_win"]:.1f}% | '
                         f'{st["fut10_mean"]:+.2f}% | {st["fut20_mean"]:+.2f}% |')
    lines.append('')
    lines.append('## 按最终评分分档')
    lines.append('')
    lines.append('| 总分区间 | 数量 | 5日均值 | 10日均值 | 20日均值 |')
    lines.append('| --- | --- | --- | --- | --- |')
    for lo, hi in ((90, 101), (85, 90), (78, 85), (70, 78), (0, 70)):
        g = df[(df['final_score'] >= lo) & (df['final_score'] < hi)]
        if len(g):
            st = backtest_stats(g)
            lines.append(f'| [{lo},{hi}) | {len(g)} | {st["fut5_mean"]:+.2f}% | '
                         f'{st["fut10_mean"]:+.2f}% | {st["fut20_mean"]:+.2f}% |')
    lines.append('')
    lines.append('> 防未来数据声明：所有信号仅使用 T 日及之前数据；未来收益仅作为 label 统计。')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'[报告] 已写入 {path}')
