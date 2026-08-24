# -*- coding: utf-8 -*-
"""
PBP Markdown 报告：严格按第十七节输出格式生成
"""
import os
import datetime

import pandas as pd

from .config import REPORT_DIR

ACTION_CN = {
    'WAIT_PLATFORM': '等待平台形成',
    'WAIT_BREAKOUT': '等待有效突破',
    'WAIT_PULLBACK': '等待首次回踩',
    'WAIT_REACCELERATION': '等待重新转强',
    'EARLY_BUY': 'A级·轻仓试探(20%~30%)',
    'PRIMARY_BUY': '★S级·最优买点',
    'CONFIRMED_BUY': 'B级·二次突破确认',
    'NO_TRADE': '不交易',
    'BREAKOUT_FAILED': '突破失败',
    'PULLBACK_FAILED': '回踩失败',
}

STATE_CN = {
    'PLATFORM_BUILDING': 'PLATFORM_BUILDING（平台构建中）',
    'PLATFORM_CONFIRMED': 'PLATFORM_CONFIRMED（平台已确认）',
    'NEAR_BREAKOUT': 'NEAR_BREAKOUT（接近突破）',
    'BREAKOUT_PENDING': 'BREAKOUT_PENDING（突破待确认）',
    'BREAKOUT_CONFIRMED': 'BREAKOUT_CONFIRMED（突破已确认）',
    'FIRST_PULLBACK': 'FIRST_PULLBACK（首次回踩中）',
    'PULLBACK_SUPPORT': 'PULLBACK_SUPPORT（关键位承接）',
    'RE_ACCELERATION': 'RE_ACCELERATION（重新转强）',
    'PRIMARY_BUY': 'PRIMARY_BUY（★最优买点）',
    'HOLD': 'HOLD（持仓/远离买点）',
    'EXIT': 'EXIT（离场）',
    'INVALIDATED': 'INVALIDATED（结构破坏）',
    'BREAKOUT_FAILED': 'BREAKOUT_FAILED（突破失败）',
    'PULLBACK_FAILED': 'PULLBACK_FAILED（回踩失败）',
}


def _ensure_dir():
    os.makedirs(REPORT_DIR, exist_ok=True)


def _fmt_pct(v, digits=1):
    try:
        return f'{float(v) * 100:.{digits}f}%'
    except Exception:
        return '-'


def _stock_block(r: pd.Series) -> list:
    """第十七节：单只股票的完整输出块"""
    lines = []
    lines.append(f"### {r.get('name', '')}（{r.get('ts_code', '')}）")
    lines.append('')
    lines.append('**平台**')
    lines.append(f"- 平台开始日期：{r.get('platform_start', '-') or '-'}")
    lines.append(f"- 平台结束日期：{r.get('platform_end', '-') or '-'}")
    lines.append(f"- 平台高点：{r.get('platform_high', 0):.2f}")
    lines.append(f"- 平台低点：{r.get('platform_low', 0):.2f}")
    lines.append(f"- 平台幅度：{_fmt_pct(r.get('platform_range', 0))}")
    lines.append(f"- 平台持续天数：{int(r.get('platform_days', 0) or 0)}")
    lines.append(f"- 平台测试次数：{int(r.get('res_tests', 0) or 0)}（阻力）/ {int(r.get('sup_tests', 0) or 0)}（支撑）")
    lines.append(f"- Platform Score：{r.get('platform_score', 0):.1f}（{r.get('platform_grade', '')}级）")
    lines.append('')
    lines.append('**突破**')
    if r.get('breakout_date'):
        lines.append(f"- 突破日期：{r.get('breakout_date')}")
        lines.append(f"- 突破价格：{r.get('breakout_price', 0):.2f}（突破位 {r.get('breakout_level', 0):.2f}）")
        lines.append(f"- 突破幅度：{_fmt_pct(r.get('breakout_pct', 0), 2)}")
        lines.append(f"- 突破量比：{r.get('breakout_vol_ratio', 0):.2f}")
        lines.append(f"- 收盘位置：{r.get('breakout_close_loc', 0):.2f}")
        lines.append(f"- Breakout Score：{r.get('breakout_score', 0):.1f}（{r.get('breakout_grade', '')}）")
    else:
        lines.append('- 尚无有效突破')
    lines.append('')
    lines.append('**回踩**')
    if r.get('pullback_started'):
        lines.append(f"- 回踩开始：{r.get('pullback_start', '-')}")
        lines.append(f"- 回踩最低：{r.get('pullback_low', 0):.2f}（{r.get('pullback_low_date', '')}）")
        lines.append(f"- 回踩幅度：{_fmt_pct(r.get('pullback_depth', 0), 0)}")
        lines.append(f"- 回踩天数：{int(r.get('pullback_days', 0) or 0)}")
        lines.append(f"- 回踩量/突破量：{r.get('pullback_vol_ratio', 0):.2f}")
        lines.append(f"- 是否跌破突破位：{'是' if r.get('broke_level') else '否'}")
        lines.append(f"- Pullback Score：{r.get('pullback_score', 0):.1f}")
        lines.append(f"- 止跌证据：{int(r.get('n_evidence', 0) or 0)}项（{r.get('evidences', '') or '-'}）")
    else:
        lines.append('- 尚未进入回踩阶段')
    lines.append('')
    lines.append('**重新转强**')
    if r.get('reacc_date'):
        lines.append(f"- 重新转强日期：{r.get('reacc_date')}")
        lines.append(f"- 重新转强价格：{r.get('reacc_price', 0):.2f}")
        lines.append(f"- 重新转强量比：{r.get('reacc_vol_ratio', 0):.2f}")
        lines.append(f"- 收盘位置：{r.get('reacc_close_loc', 0):.2f}")
    else:
        lines.append('- 未触发')
    lines.append('')
    lines.append('**最终**')
    stars = int(r.get('stars', 0) or 0)
    lines.append(f"- 最终评分：{r.get('final_score', 0):.1f}（平台 {r.get('score_platform', 0):.1f}/30 + "
                 f"突破 {r.get('score_breakout', 0):.1f}/25 + 回踩 {r.get('score_pullback', 0):.1f}/25 + "
                 f"转强 {r.get('score_reacc', 0):.1f}/20）")
    lines.append(f"- 评级：{'★' * stars}{'☆' * (5 - stars)}" if stars else '- 评级：不交易')
    lines.append(f"- 状态：{STATE_CN.get(r.get('state', ''), r.get('state', ''))}")
    act = r.get('action', '')
    lines.append(f"- 交易结论：**{act}**（{ACTION_CN.get(act, '')}）")
    if r.get('reasons'):
        lines.append(f"- 判定过程：{r.get('reasons')}")
    lines.append('')
    return lines


def write_daily_report(df: pd.DataFrame, date: str, regime: str = 'neutral'):
    _ensure_dir()
    path = os.path.join(REPORT_DIR, f'pbp_report_{date}.md')
    lines = []
    lines.append(f'# PBP 平台突破回踩买点报告 {date}')
    lines.append('')
    lines.append(f'> 生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> 市场环境：{regime}')
    lines.append('')
    lines.append('> 交易路径：平台形成 → 平台确认 → 有效突破 → 突破确认 → 首次健康回踩 → '
                 '关键位承接 → 回踩结束 → 重新转强 → ★ PRIMARY BUY')
    lines.append('')
    if df.empty:
        lines.append('当日无有效结构信号。')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f'[报告] 已写入 {path}')
        return

    # 按 action 分组
    primary = df[df['action'] == 'PRIMARY_BUY']
    confirmed = df[df['action'] == 'CONFIRMED_BUY']
    early = df[df['action'] == 'EARLY_BUY']
    waiting = df[df['action'].isin(('WAIT_REACCELERATION', 'WAIT_PULLBACK', 'WAIT_BREAKOUT'))]
    failed = df[df['action'].isin(('BREAKOUT_FAILED', 'PULLBACK_FAILED'))]

    lines.append('## ★ S级 PRIMARY BUY（最优买点）')
    lines.append('')
    if primary.empty:
        lines.append('无。')
    else:
        for _, r in primary.iterrows():
            lines.extend(_stock_block(r))
    lines.append('')

    lines.append('## B级 CONFIRMED BUY（二次突破确认）')
    lines.append('')
    if confirmed.empty:
        lines.append('无。')
    else:
        for _, r in confirmed.iterrows():
            lines.extend(_stock_block(r))
    lines.append('')

    lines.append('## A级 EARLY BUY（轻仓试探 20%~30%）')
    lines.append('')
    if early.empty:
        lines.append('无。')
    else:
        for _, r in early.iterrows():
            lines.extend(_stock_block(r))
    lines.append('')

    lines.append('## 等待确认（观察池）')
    lines.append('')
    if waiting.empty:
        lines.append('无。')
    else:
        lines.append('| 代码 | 名称 | 状态 | 总分 | 突破日 | 后N日 | 回踩日 | 踩量/突量 | 结论 |')
        lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for _, r in waiting.head(30).iterrows():
            lines.append(f"| {r['ts_code']} | {r['name']} | {r['state']} | {r['final_score']:.1f} | "
                         f"{r.get('breakout_date', '')} | {int(r.get('days_after', -1) or -1)} | "
                         f"{int(r.get('pullback_days', 0) or 0)} | {r.get('pullback_vol_ratio', 0):.2f} | "
                         f"{r['action']} |")
    lines.append('')

    lines.append('## 失败/失效信号（严禁买入）')
    lines.append('')
    if failed.empty:
        lines.append('无。')
    else:
        lines.append('| 代码 | 名称 | 结论 | 原因 |')
        lines.append('| --- | --- | --- | --- |')
        for _, r in failed.head(20).iterrows():
            lines.append(f"| {r['ts_code']} | {r['name']} | {r['action']} | {r.get('reasons', '')} |")
    lines.append('')

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'[报告] 已写入 {path}')
