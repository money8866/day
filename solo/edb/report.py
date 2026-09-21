# -*- coding: utf-8 -*-
"""
EDB 报告生成（Markdown + PDF）
================================================================================
  edb_report_<date>.md            每日候选报告（规范第 20 / 24 节）
  edb_backtest_<start>_<end>.md   历史回测报告（规范第 24 节，含 VR 四区间验证）

约定：
  · EDB_SCORE 不直接等于 BUY，报告只输出「候选池 + 分层 + 建议动作」；
  · 统计口径直接复用 scanner.print_stats / backtest.print_backtest_report，
    保证报告与终端输出完全一致（绝不两套口径）。
"""
import os
import io
import datetime
import contextlib

import numpy as np
import pandas as pd

from .config import REPORT_DIR, STATUS_CN, STATUS_PRIORITY, ACTION_CN
from .config import EDB_CONFIG as cfg


# ═══════════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════════
def _ensure_dir():
    os.makedirs(REPORT_DIR, exist_ok=True)


def _num(v, nd=2, plus=False, suffix=''):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return '—'
    if not np.isfinite(f):
        return '—'
    s = f'{f:+.{nd}f}' if plus else f'{f:.{nd}f}'
    return s + suffix


def _text(v, default='—'):
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _join(v, default='—'):
    if isinstance(v, (list, tuple)):
        v = '；'.join(str(x) for x in v if str(x).strip())
    s = str(v or '').strip()
    return s if s else default


def _capture(fn, *a, **kw) -> str:
    """捕获终端统计输出，保证报告与终端同口径"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn(*a, **kw)
    except Exception as e:
        return f'（统计输出失败：{e}）'
    return buf.getvalue().rstrip()


def _try_pdf(md_path: str, pdf_path: str):
    try:
        import sys
        solo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if solo not in sys.path:
            sys.path.insert(0, solo)
        from convert_md_to_pdf_v2 import markdown_to_pdf_simple
        markdown_to_pdf_simple(md_path, pdf_path)
    except Exception as e:
        print(f'[报告] PDF 生成失败（Markdown 已生成）：{e}')


def _pool_size(df: pd.DataFrame) -> int:
    n = df.attrs.get('n_pool')
    if n:
        return int(n)
    try:
        from .data import get_stock_pool
        return len(get_stock_pool(exclude_st=True))
    except Exception:
        return 0


def _counts(df: pd.DataFrame) -> dict:
    return {st: int((df['EDB状态'] == st).sum()) for st in STATUS_PRIORITY}


# ═══════════════════════════════════════════════════════════
# 每日报告（规范第 20 / 21 / 24 节）
# ═══════════════════════════════════════════════════════════
_CORE_COLS = [
    ('排名', 'No', 3, None),
    ('代码', '代码', 10, None),
    ('名称', '名称', 10, None),
    ('EDB状态', 'EDB状态', 14, None),
    ('EDB_SCORE_ADJ', 'SCORE', 5, '{:.1f}'),
    ('BaseDays', 'Base', 4, '{:.0f}'),
    ('DryUpRatio', '缩量比', 7, '{:.3f}'),
    ('BreakoutVR', '爆量VR', 7, '{:.2f}'),
    ('BreakoutDistance', '距平台%', 8, '{:+.2f}'),
    ('ClosePosition', '收盘位', 6, '{:.2f}'),
    ('HVT分档', 'HVT', 10, None),
    ('建议动作', '建议动作', 22, None),
]


def _md_table(df: pd.DataFrame, cols_spec) -> list:
    lines = []
    lines.append('| ' + ' | '.join(c[1] for c in cols_spec) + ' |')
    lines.append('| ' + ' | '.join('---' for _ in cols_spec) + ' |')
    for _, r in df.iterrows():
        cells = []
        for key, _lab, _w, fmt in cols_spec:
            v = r.get(key)
            if fmt:
                try:
                    fv = float(v)
                    cells.append(fmt.format(fv) if np.isfinite(fv) else '—')
                    continue
                except (TypeError, ValueError):
                    cells.append('—')
                    continue
            cells.append(_text(v))
        lines.append('| ' + ' | '.join(cells) + ' |')
    return lines


def _detail_block(r: pd.Series, idx: int) -> list:
    st = _text(r.get('EDB状态'))
    lines = [
        f'### {idx}. {_text(r.get("名称"))} {_text(r.get("代码"))} — {st}'
        f'（{STATUS_CN.get(str(r.get("EDB状态")), "")}）',
        '',
        f'- 行业：{_text(r.get("行业"))} | 板块：{_text(r.get("板块"))} | '
        f'EDB_SCORE {_num(r.get("EDB_SCORE"), 1)}（风险扣分 {_num(r.get("风险扣分"), 1)}）'
        f' → 调整后 {_num(r.get("EDB_SCORE_ADJ"), 1)}',
        f'- 横盘：BaseDays {_num(r.get("BaseDays"), 0)} | BaseRange {_num(r.get("BaseRange"), 2)}%'
        f' | Range60 {_num(r.get("Range60"), 2)}% | ATR20/ATR120 {_num(r.get("ATR20_ATR120"), 2)}',
        f'- 缩量：DryUpRatio {_num(r.get("DryUpRatio"), 3)}'
        f' | MinVOL10_Ratio {_num(r.get("MinVOL10_Ratio"), 3)}'
        f' | 持续缩量 {_num(r.get("持续缩量_lt050"), 0)} 日(<0.50) / '
        f'{_num(r.get("持续缩量_lt045"), 0)} 日(<0.45)',
        f'- 量能：MA20_VOL {_num(r.get("MA20_VOL"), 0)} | MA120_VOL {_num(r.get("MA120_VOL"), 0)}',
        f'- 平台：High {_num(r.get("PlatformHigh"))} | Low {_num(r.get("PlatformLow"))}'
        f' | 现价 {_num(r.get("现价"))} | 距平台 {_num(r.get("BreakoutDistance"), 2, plus=True)}%'
        f' | 距250日高 {_num(r.get("距250日高"), 2, plus=True)}%',
        f'- 突破：BreakoutVR {_num(r.get("BreakoutVR"), 2)}'
        f' | DayGain {_num(r.get("DayGain"), 2, plus=True)}%'
        f' | ClosePosition {_num(r.get("ClosePosition"), 2)}'
        f' | 突破日 {_text(r.get("突破日"))} (D+{_num(r.get("D+N"), 0)})',
        f'- 均线：MA20 {_num(r.get("MA20"))} | MA60 {_num(r.get("MA60"))} | MA120 {_num(r.get("MA120"))}',
        f'- 联动：HVT {_text(r.get("HVT状态"))}（{_text(r.get("HVT分档"))}）'
        f' | 基本面 {_text(r.get("基本面状态"))}'
        f' | 扣非同比 {_num(r.get("扣非同比"), 1)}% | 单季Q2同比 {_num(r.get("单季Q2同比"), 1)}%',
        f'- 事件：EVENT_DRIVEN {"是" if bool(r.get("EVENT_DRIVEN")) else "否"}'
        f' {_join(r.get("事件类型"), "")}',
        f'- 风险标签：{_join(r.get("风险标签"))}',
        f'- 建议动作：{_text(r.get("建议动作"))}（{ACTION_CN.get(str(r.get("建议动作")), "")}）',
    ]
    if st == 'EDB_PULLBACK':
        lines += _pullback_plan(r)
    lines.append('')
    return lines


def _pullback_plan(r: pd.Series) -> list:
    """EDB_PULLBACK 交易计划（入场 / 止损 / 失效 / 档位），参数取自 config 第八节"""
    try:
        ph = float(r.get('PlatformHigh'))
    except (TypeError, ValueError):
        ph = np.nan
    if not np.isfinite(ph) or ph <= 0:
        return []
    hold, floor = cfg['pullback_hold'], cfg['pullback_floor']
    entry_lo, entry_hi = ph * hold, ph
    stop_px = ph * floor
    dry = r.get('DryUpRatio')
    try:
        core = np.isfinite(float(dry)) and float(dry) <= cfg['pb_dry_core']
    except (TypeError, ValueError):
        core = False
    plan = (
        f'- 交易计划：入场参考 {_num(entry_lo)}~{_num(entry_hi)}（回踩守住平台高点）'
        f' | 止损参考 {_num(stop_px)}（平台下沿 {floor:.0%}）'
        f' | 失效：收盘跌破止损位，或 D+{cfg["pullback_window"]} 内未再启动放量'
        f' | 档位：{"核心回踩" if core else "普通回踩"}（DryUpRatio {_num(dry, 3)}）'
    )
    if str(r.get('建议动作')) == 'WAIT_DEEPER_PULLBACK':
        try:
            bd = float(r.get('BreakoutDistance'))
            plan += (f' | 突破日距平台 {_num(bd, plus=True)}% 未回到承接位，不追；'
                     f'等更深的回踩贴近入场区下沿，或后续再放量确认'
                     f'（EDB_REBREAKOUT 路径）')
        except (TypeError, ValueError):
            pass
    return [plan, '']


def write_daily_report(df: pd.DataFrame, date: str, print_stats=None):
    _ensure_dir()
    date = str(date)
    md_path = os.path.join(REPORT_DIR, f'edb_report_{date}.md')
    pdf_path = os.path.join(REPORT_DIR, f'edb_report_{date}.pdf')

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    L = []
    L.append(f'# EDB 极致缩量→爆量突破 选股报告 {date}')
    L.append('')
    L.append(f'> 生成时间：{now}')
    L.append('> 模型：EDB = Extreme Dry-up Breakout')
    L.append('> 结构：长期横盘 → 价格波动收缩 → 成交量持续下降 → DryUpRatio<=0.45'
             ' → BreakoutVR>=2.0 → 突破长期平台 → ClosePosition>=0.75')
    L.append('> **EDB_SCORE 不等于 BUY**：本报告仅输出标准化候选池，'
             '需与 HVT / TE / IGE / 基本面 / 主题强度 二筛。')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    if df is None or df.empty:
        L.append('## 当日无有效信号')
        L.append('')
        L.append('未发现满足「长期横盘 + 极致缩量 + 价格压缩 + 爆量突破/临界突破」的标的。')
        L.append('')
        L.append('---')
        L.append('> 风险提示：本报告仅为技术形态量化输出，不构成投资建议。')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(L))
        print(f'[报告] 已写入 {md_path}')
        _try_pdf(md_path, pdf_path)
        return md_path

    c = _counts(df)
    n_pool = _pool_size(df)
    n_ev = c.get('EVENT_ONLY', 0)

    # ── 一、核心结论 ──
    L.append('## 一、核心结论')
    L.append('')
    L.append(f'- 股票池 {n_pool} 只 → 有效候选 **{len(df)}** 只')
    L.append(f'- 突破后二次机会：EDB_REBREAKOUT **{c["EDB_REBREAKOUT"]}** / '
             f'EDB_PULLBACK **{c["EDB_PULLBACK"]}**（优先级高于单纯 EDB；'
             f'距平台 ≥{cfg["pb_bd_reject"]:.0%} 的回踩已降级为「等回踩至平台」，'
             f'dry≤{cfg["pb_dry_core"]:.0%} 标注「核心回踩」档）')
    L.append(f'- 爆量突破：EDB_STRONG **{c["EDB_STRONG"]}** / EDB **{c["EDB"]}** / '
             f'EDB_WATCH **{c["EDB_WATCH"]}**')
    L.append(f'- 临界待爆：PRE_EDB **{c["PRE_EDB"]}**')
    L.append(f'- 事件驱动（另分类）：**{int(df["EVENT_DRIVEN"].fillna(False).astype(bool).sum())}**')
    L.append(f'- 一字/连板（无法正常交易）：EVENT_ONLY **{n_ev}**')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 二、运行统计（规范第 24 节）──
    L.append('## 二、运行统计（规范第 24 节）')
    L.append('')
    L.append('```text')
    if print_stats is not None:
        L.append(_capture(print_stats, df, n_pool))
    else:
        L.append(f'股票池总数 {n_pool} / 有效候选 {len(df)}')
    L.append('```')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 三、TOP 30 ──
    top_n = min(30, len(df))
    L.append(f'## 三、TOP {top_n} 候选（按 EDB_SCORE_ADJ 排序）')
    L.append('')
    L.extend(_md_table(df.head(top_n), _CORE_COLS))
    L.append('')
    L.append('> 全字段（含 MA20_VOL / MA120_VOL / PlatformHigh / PlatformLow / MA20 / MA60 / MA120 等）'
             '见同目录 `edb_daily_<date>.csv` 与 `edb_full_<date>.csv`。')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 四、TOP 10 明细 ──
    L.append('## 四、TOP 10 明细')
    L.append('')
    for i, (_, r) in enumerate(df.head(10).iterrows(), 1):
        L.extend(_detail_block(r, i))
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 五、分层清单 ──
    L.append('## 五、分层清单')
    L.append('')
    for st in STATUS_PRIORITY:
        g = df[df['EDB状态'] == st]
        L.append(f'### {st}（{STATUS_CN.get(st, "")}）— {len(g)} 只')
        L.append('')
        if g.empty:
            L.append('无。')
        else:
            for _, r in g.iterrows():
                L.append(f'- {_text(r.get("代码"))} {_text(r.get("名称"))}'
                         f' | SCORE {_num(r.get("EDB_SCORE_ADJ"), 1)}'
                         f' | Base {_num(r.get("BaseDays"), 0)}'
                         f' | 缩量比 {_num(r.get("DryUpRatio"), 3)}'
                         f' | 爆量VR {_num(r.get("BreakoutVR"), 2)}'
                         f' | 距平台 {_num(r.get("BreakoutDistance"), 2, plus=True)}%'
                         f' | 收盘位 {_num(r.get("ClosePosition"), 2)}'
                         f' | {_text(r.get("建议动作"))}')
        L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 六、事件驱动专区 ──
    L.append('## 六、事件驱动（EDB_EVENT_DRIVEN，不剔除但单独分类）')
    L.append('')
    ev = df[df['EVENT_DRIVEN'].fillna(False).astype(bool)]
    if ev.empty:
        L.append('无（或事件数据不可用，见上方统计提示；绝不臆造事件标记）。')
    else:
        L.append('| 代码 | 名称 | EDB状态 | SCORE | 事件类型 | 次日/后续建议 |')
        L.append('| --- | --- | --- | --- | --- | --- |')
        for _, r in ev.iterrows():
            L.append(f'| {_text(r.get("代码"))} | {_text(r.get("名称"))} | {_text(r.get("EDB状态"))}'
                     f' | {_num(r.get("EDB_SCORE_ADJ"), 1)} | {_join(r.get("事件类型"), "")}'
                     f' | {_text(r.get("建议动作"))} |')
        L.append('')
        L.append('> 事件驱动爆量与「自然供给收缩后的爆量」不是同一种交易结构，'
                 '不得混为一类；事件型需单独复核事件性质与持续性。')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 七、风险标签 ──
    L.append('## 七、风险标签汇总')
    L.append('')
    tags = {}
    for s in df['风险标签'].fillna(''):
        for t in str(s).split('；'):
            t = t.strip()
            if t:
                tags[t] = tags.get(t, 0) + 1
    if not tags:
        L.append('无风险标签。')
    else:
        for t, n in sorted(tags.items(), key=lambda x: -x[1]):
            names = df[df['风险标签'].fillna('').str.contains(t, regex=False)]['名称']
            L.append(f'- **{t}** × {n}：{"、".join(str(x) for x in names.head(8))}'
                     + ('…' if n > 8 else ''))
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 八、EDB × HVT 交叉标签 ──
    L.append('## 八、EDB × HVT 交叉标签')
    L.append('')
    pairs = [('EDB_REBREAKOUT', 'HVT_STRONG'), ('EDB_PULLBACK', 'HVT_STRONG'),
             ('EDB_STRONG', 'HVT_STRONG'), ('EDB', 'HVT_STRONG')]
    hit = df[df['HVT分档'].isin(('HVT_STRONG',))]
    L.append(f'- EDB × HVT_STRONG（最高优先级候选）：**{len(hit)}** 只')
    if not hit.empty:
        for _, r in hit.head(10).iterrows():
            L.append(f'  - {_text(r.get("代码"))} {_text(r.get("名称"))}'
                     f' [{_text(r.get("EDB状态"))} × {_text(r.get("HVT状态"))}]'
                     f' SCORE {_num(r.get("EDB_SCORE_ADJ"), 1)}'
                     f' → {_text(r.get("建议动作"))}')
    normal = df[df['HVT分档'] == 'HVT_NORMAL']
    L.append(f'- EDB × HVT_NORMAL（正常候选）：**{len(normal)}** 只')
    weak = df[df['HVT分档'].isin(('HVT_WEAK', ''))]
    L.append(f'- EDB × HVT_WEAK / 无 HVT 截面（只观察）：**{len(weak)}** 只')
    L.append('')
    fc = df[df['基本面状态'] == 'FUNDAMENTAL_CONFIRM']
    cf = df[df['基本面状态'] == 'FUNDAMENTAL_CONFLICT']
    L.append(f'- 基本面改善 FUNDAMENTAL_CONFIRM：**{len(fc)}** 只')
    L.append(f'- 基本面恶化 FUNDAMENTAL_CONFLICT：**{len(cf)}** 只'
             f'（标记后不得仅凭技术面进入最终买入池）')
    L.append('')
    L.append('> 交叉优先级：EDB_REBREAKOUT / EDB_STRONG × HVT_STRONG > EDB × HVT_NORMAL '
             '> EDB × HVT_WEAK（仅观察）。EDB 不替代 HVT，二者独立打分后交叉。')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 九、口径声明 ──
    L.append('## 九、口径与声明')
    L.append('')
    L.append('- **无未来函数**：行情仅用 `trade_date <= 扫描日`；财报仅用 `ann_date <= 扫描日`；'
             '事件仅用 `ann_date <= 扫描日` 且落在回看窗口内。')
    L.append('- **均量不污染**：`BreakoutVR = VOL_today / MA20_VOL(不含今日)`；'
             '`DryUpRatio = MA20_VOL / MA120_VOL`，两者均严格排除当日成交量。')
    L.append('- **结构字段口径**：突破类（EDB / EDB_STRONG / EDB_WATCH / EDB_PULLBACK / '
             'EDB_REBREAKOUT）的 Base / 缩量 / 压缩 / 平台 / 突破质量取**突破日快照**；'
             '现价 / MA20 / MA60 / MA120 / 距250日高 取**当日值**。')
    L.append('- **评分边界**：VR > 5 不再加分并标记 CLIMAX_VOL；涨停不自动提分；'
             'EDB_SCORE 不直接等于 BUY。')
    L.append('')
    L.append('---')
    L.append('> 风险提示：本报告仅为技术形态量化输出，不构成投资建议。股市有风险，入市需谨慎。')

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'[报告] 已写入 {md_path}')
    _try_pdf(md_path, pdf_path)
    return md_path


# ═══════════════════════════════════════════════════════════
# 回测报告（规范第 24 节：T+3/5/10/20 与 VR 四区间验证）
# ═══════════════════════════════════════════════════════════
def _stat_row(label: str, st: dict) -> str:
    if not st or not st.get('n'):
        return f'| {label} | — | — | — | — | — | — | — | — | — | — | — | — | — |'
    return (f'| {label} | {st["n"]} '
            f'| {_num(st.get("fut3_mean"), 2, plus=True)} '
            f'| {_num(st.get("fut5_mean"), 2, plus=True)} '
            f'| {_num(st.get("fut10_mean"), 2, plus=True)} '
            f'| {_num(st.get("fut20_mean"), 2, plus=True)} '
            f'| {_num(st.get("fut20_med"), 2, plus=True)} '
            f'| {_num(st.get("fut20_win"), 1)} '
            f'| {_num(st.get("fut20_trim"), 2, plus=True)} '
            f'| {_num(st.get("fut20_out_pp"), 2, plus=True)} '
            f'| {_num(st.get("fut5_med"), 2, plus=True)} '
            f'| {_num(st.get("fut5_win"), 1)} '
            f'| {_num(st.get("max_dd"), 2)} '
            f'| {_num(st.get("profit_ratio"), 2)} |')


_STAT_HEAD = ('| 分组 | 样本 | T+3均值% | T+5均值% | T+10均值% | T+20均值% '
              '| T+20中位% | T+20胜率% | T+20剔前二% | 离群pp '
              '| T+5中位% | T+5胜率% | 最大回撤% | 盈亏比 |')
_STAT_SEP = ('| --- | --- | --- | --- | --- | --- | --- | --- | --- | '
             '--- | --- | --- | --- | --- |')


def write_backtest_report(df: pd.DataFrame, start: str, end: str, backtest_stats=None):
    _ensure_dir()
    md_path = os.path.join(REPORT_DIR, f'edb_backtest_{start}_{end}.md')
    pdf_path = os.path.join(REPORT_DIR, f'edb_backtest_{start}_{end}.pdf')

    if backtest_stats is None:
        from .backtest import backtest_stats as _bs
        backtest_stats = _bs

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    L = []
    L.append(f'# EDB 历史回测报告 {start} ~ {end}')
    L.append('')
    L.append(f'> 生成时间：{now}')
    L.append(f'> 信号样本：{0 if df is None else len(df)}')
    L.append('> 目的：验证 EDB / EDB_STRONG / EDB_PULLBACK / EDB_REBREAKOUT 的 T+3 / T+5 / T+10 / T+20 '
             '表现，并**特别验证 BreakoutVR 四区间收益差异**——不假设「量越大越好」。')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    if df is None or df.empty:
        L.append('## 回测区间内无信号')
        L.append('')
        L.append('---')
        L.append('> 防未来数据声明：信号仅使用 T 日及之前数据；未来收益仅作 label 统计。')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(L))
        print(f'[报告] 已写入 {md_path}')
        _try_pdf(md_path, pdf_path)
        return md_path

    # ── 一、整体表现 ──
    L.append('## 一、整体表现')
    L.append('')
    L.append(_STAT_HEAD)
    L.append(_STAT_SEP)
    L.append(_stat_row('全部信号', backtest_stats(df)))
    L.append('')
    st = backtest_stats(df)
    L.append(f'- 平均盈利 {_num(st.get("avg_win"), 2, plus=True)}% / '
             f'平均亏损 {_num(st.get("avg_loss"), 2, plus=True)}% / '
             f'ProfitFactor {_num(st.get("profit_factor"), 2)}')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 二、按 EDB 状态分组 ──
    L.append('## 二、按 EDB 状态分组')
    L.append('')
    L.append(_STAT_HEAD)
    L.append(_STAT_SEP)
    for name in ('EDB_STRONG', 'EDB', 'EDB_WATCH', 'PRE_EDB',
                 'EDB_PULLBACK', 'EDB_REBREAKOUT', 'EVENT_ONLY'):
        g = df[df['status'] == name]
        if len(g):
            L.append(_stat_row(name, backtest_stats(g)))
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 三、BreakoutVR 四区间（核心验证）──
    L.append('## 三、BreakoutVR 四区间验证（核心：是否「量越大越好」）')
    L.append('')
    bo = df[df['status'].isin(('EDB_STRONG', 'EDB', 'EDB_WATCH'))]
    if bo.empty:
        L.append('无突破类样本（EDB_STRONG / EDB / EDB_WATCH）。')
    else:
        vr = pd.to_numeric(bo['breakout_vr'], errors='coerce')
        L.append(_STAT_HEAD)
        L.append(_STAT_SEP)
        for lab, lo, hi in (('VR 2.0~2.5', 2.0, 2.5), ('VR 2.5~3.0', 2.5, 3.0),
                            ('VR 3.0~5.0', 3.0, 5.0), ('VR >5.0', 5.0, 1e9)):
            g = bo[(vr >= lo) & (vr < hi)]
            L.append(_stat_row(lab, backtest_stats(g)))
        L.append('')
        L.append('> 结论必须从本表读出：若 VR 3.0~5.0 与 VR >5.0 的 T+5/T+20 不优于 2.0~2.5，'
                 '则「爆量越大越好」不成立，VR >5 应视为高潮警戒（CLIMAX_VOL）。')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 四、DryUpRatio 分区间 ──
    L.append('## 四、DryUpRatio 分区间验证（缩量越极致越好？）')
    L.append('')
    dry = pd.to_numeric(df['dry_up_ratio'], errors='coerce')
    L.append(_STAT_HEAD)
    L.append(_STAT_SEP)
    for lab, lo, hi in (('<0.35 超极致', 0.0, 0.35), ('0.35~0.45 极致', 0.35, 0.45),
                        ('0.45~0.55 明显', 0.45, 0.55), ('0.55~0.70 普通', 0.55, 0.70),
                        ('>0.70 无缩量', 0.70, 9.0)):
        g = df[(dry >= lo) & (dry < hi)]
        if len(g):
            L.append(_stat_row(lab, backtest_stats(g)))
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 五、EDB_SCORE_ADJ 分档单调性 ──
    L.append('## 五、EDB_SCORE_ADJ 分档单调性验证')
    L.append('')
    sc = pd.to_numeric(df['edb_score_adj'], errors='coerce')
    L.append(_STAT_HEAD)
    L.append(_STAT_SEP)
    for lab, lo, hi in (('[80,100]', 80, 101), ('[70,80)', 70, 80),
                        ('[60,70)', 60, 70), ('[0,60)', 0, 60)):
        g = df[(sc >= lo) & (sc < hi)]
        if len(g):
            L.append(_stat_row(lab, backtest_stats(g)))
    L.append('')
    L.append('> 分数应具备单调性：若高分档不优于低分档，说明评分权重需要重标定。')
    L.append('')
    L.append('═══════════════════════════════════════════════════════════════════')
    L.append('')

    # ── 六、终端原始输出（同口径留档）──
    L.append('## 六、原始回测输出（留档）')
    L.append('')
    L.append('```text')
    try:
        from .backtest import print_backtest_report
        L.append(_capture(print_backtest_report, df))
    except Exception as e:
        L.append(f'（原始输出捕获失败：{e}）')
    L.append('```')
    L.append('')
    L.append('---')
    L.append('> 防未来数据声明：所有信号仅使用 T 日及之前数据；未来收益仅作为 label 统计。')
    L.append('> 回测含除权缺口样本剔除（未来 20 日相邻涨跌幅 >25% 视为不复权缺口污染）。')
    L.append('> 事件标注口径：ev_limit=信号后20根K线内连续>=2日涨幅>=9.5%或一字板'
             '（high==low 且涨幅>=5%）；ev_susp=窗口内「市场开市但个股无K线」累计>=2个交易日'
             '（全市场交易日历核对）。事件样本收益属事件驱动，不代表形态可复制；'
             '「T+20剔前二%」=剔除前二离群后均值，「离群pp」=全均值−剔前二均值，'
             '并同步给出含/剔事件双口径（终端原始输出内）。')

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'[报告] 已写入 {md_path}')
    _try_pdf(md_path, pdf_path)
    return md_path
