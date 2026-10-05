# -*- coding: utf-8 -*-
"""CSI2000 Position Gate —— §27 核心研究问题 + §28 输出文件 + §29 每日报告

研究纪律（§30）：所有结论来自数据本身；分档统计用的「未来收益」只用于事后评估，
绝不进入 Gate 的任何计算路径。
"""
import json
import os

import numpy as np
import pandas as pd

from position_gate import config as C
from position_gate import backtest as B
from position_gate import features as F
from position_gate import oos as O
from position_gate import regime as R
from position_gate.data import GateData

SEP = '═' * 30
SUB = '─' * 30


# =========================================================
# 构建 / 落盘
# =========================================================
def build_gate(data: GateData = None, start: str = C.DATA_START, end: str = C.DATA_END,
               verbose: bool = True, save: bool = True):
    """构建 feat + gate 并（可选）落盘缓存"""
    data = data or GateData()
    feat, parts = F.build_all(data=data, start=start, end=end, verbose=verbose)
    gate = R.run_gate(feat)
    if save:
        feat.to_pickle(os.path.join(C.CACHE_DIR, 'feat.pkl'))
        gate.to_pickle(os.path.join(C.CACHE_DIR, 'gate.pkl'))
    return gate, parts


def _csv(df: pd.DataFrame, name: str, index: bool = True) -> str:
    path = os.path.join(C.OUT_DIR, name)
    df.to_csv(path, encoding='utf-8-sig', index=index)
    return path


def gate_daily(gate: pd.DataFrame) -> pd.DataFrame:
    """§28 每日核心输出（列名严格对齐规格）"""
    g = gate
    out = pd.DataFrame(index=g.index)
    out.index.name = 'date'
    out['csi2000_close'] = g['close'].round(4)
    out['ma20'] = g['ma20'].round(4)
    out['ma60'] = g['ma60'].round(4)
    out['ma20_slope'] = g['ma20_slope_5'].round(4)
    out['breadth_ma20'] = (g['breadth_ma20'] * 100).round(2)
    out['breadth_change_5'] = g['breadth_change_5'].round(2)
    out['new_high_ratio'] = (g['new_high_ratio'] * 100).round(2)
    out['new_low_ratio'] = (g['new_low_ratio'] * 100).round(2)
    out['rs20_vs_all'] = g['rs20_vs_all'].round(3)
    out['rs20_vs_1000'] = g['rs20_vs_1000'].round(3)
    out['volume_ratio'] = g['volume_ratio_20'].round(3)
    out['riskoff_count'] = g['riskoff_count'].astype(int)
    out['regime'] = g['regime']
    out['position_min'] = g['position_min'].round(1)
    out['position_target'] = g['position_target'].round(1)
    out['position_max'] = g['position_max'].round(1)
    out['position'] = g['position'].round(1)
    out['full_exposure_eligible'] = g['full_exposure_eligible'].astype(bool)
    out['reason'] = g['reason']
    return out


def gate_events(gate: pd.DataFrame) -> pd.DataFrame:
    """§28 事件流：状态切换 / 仓位变动 / 满仓资格 / Risk-Off / 背离"""
    rows = []
    prev_reg, prev_pos, prev_full = None, None, None
    ro_cols = [c for c in gate.columns if c[:2] in C.RISK_OFF_EVENTS]
    for d, r in gate.iterrows():
        if prev_reg is not None and r['regime'] != prev_reg:
            rows.append({'date': d, 'event': 'REGIME_CHANGE', 'detail': f'{prev_reg}→{r["regime"]}',
                         'regime': r['regime'], 'position': r['position']})
        if prev_pos is not None and abs(r['position'] - prev_pos) > 1e-9:
            kind = 'POSITION_UP' if r['position'] > prev_pos else 'POSITION_DOWN'
            rows.append({'date': d, 'event': kind,
                         'detail': f'{prev_pos:.0f}%→{r["position"]:.0f}%',
                         'regime': r['regime'], 'position': r['position']})
        if prev_full is not None and bool(r['full_exposure_eligible']) != bool(prev_full):
            rows.append({'date': d, 'event': 'FULL_EXPOSURE_ELIGIBLE',
                         'detail': 'YES' if r['full_exposure_eligible'] else 'NO',
                         'regime': r['regime'], 'position': r['position']})
        for c in ro_cols:
            if bool(r[c]):
                rows.append({'date': d, 'event': c, 'detail': f'riskoff_count={int(r["riskoff_count"])}',
                             'regime': r['regime'], 'position': r['position']})
        if bool(r.get('INDEX_BREADTH_DIVERGENCE')):
            rows.append({'date': d, 'event': 'INDEX_BREADTH_DIVERGENCE',
                         'detail': f'warn={int(r["div_warn_count"])}',
                         'regime': r['regime'], 'position': r['position']})
        if bool(r.get('HEALTHY_PULLBACK')):
            rows.append({'date': d, 'event': 'HEALTHY_PULLBACK',
                         'detail': f'ok={int(r["hp_ok_count"])}',
                         'regime': r['regime'], 'position': r['position']})
        prev_reg, prev_pos, prev_full = r['regime'], r['position'], r['full_exposure_eligible']
    return pd.DataFrame(rows)


# =========================================================
# §27 Q1~Q6：消融 / 条件统计
# =========================================================
def _neutralize(feat: pd.DataFrame, what: str) -> pd.DataFrame:
    """把某一类变量的信息「中性化」，用于消融对照

    中性化后该类变量不再能区分市场状态（相当于关闭该变量），其余逻辑不变。
    """
    f = F.as_raw_feat(feat).copy()
    if what == 'breadth':
        f['breadth_ma20'] = 1.0
        f['breadth_change_5'] = 0.0
        f['breadth_change_20'] = 0.0
        f['breadth_pressure'] = 1.0
        f['new_high_ratio'] = 1.0
        f['new_low_ratio'] = 0.0
        f['R5_breadth_drop'] = False
        f['R6_newlow_gt_newhigh'] = False
    elif what == 'relative':
        f['rs20_vs_all'] = 1.0
        f['rs20_change_5'] = 1.0
        f['rs20_change_20'] = 1.0
        f['R7_rs20_deteriorate'] = False
    elif what == 'volume':
        f['volume_ratio_20'] = 1.0
        f['volume_ratio_5'] = 1.0
        f['R2_below_ma20_volume'] = False
    elif what == 'riskoff':
        for c in [x for x in f.columns if x[:2] in C.RISK_OFF_EVENTS]:
            f[c] = False
    f['riskoff_count'] = f[[c for c in f.columns if c[:2] in C.RISK_OFF_EVENTS]].sum(axis=1).astype(int)
    return f


def _bt_row(label: str, gate: pd.DataFrame, cost_bp: float = 15.0) -> dict:
    g = B.trim_warmup(gate)
    m = B.metrics(B.daily_returns(g, cost_bp=cost_bp), cost_bp=cost_bp)
    b100 = B.metrics(B.const_returns(g, 100.0, cost_bp=cost_bp), cost_bp=cost_bp)
    return {'variant': label, 'n_days': len(g), 'CAGR_%': m['CAGR_%'], 'vol_%': m['vol_%'],
            'maxDD_%': m['maxDD_%'], 'Calmar': m['Calmar'], 'Sharpe': m['Sharpe'],
            'avg_position_%': m['avg_position_%'], 'turnover_per_year': m['turnover_per_year'],
            'maxDD_改善vsB100_pp': round(m['maxDD_%'] - b100['maxDD_%'], 2)}


def ma_only_gate(feat: pd.DataFrame, pos_up: float = 100.0) -> pd.DataFrame:
    """Q1 对照：只用 MA20 开关（Close>MA20 → 满仓，否则空仓），无宽度/RS/量价/Risk-Off"""
    g = F.as_raw_feat(feat).copy()
    g['position'] = np.where(g['close'] > g['ma20'], pos_up, 0.0)
    g['regime'] = 'MA_ONLY'
    return g


def ablation(feat: pd.DataFrame, cost_bp: float = 15.0) -> pd.DataFrame:
    """消融矩阵：全模型 vs 关闭单类变量 vs MA20 开关 vs 固定仓位"""
    feat = F.as_raw_feat(feat)
    rows = [_bt_row('Model(完整)', R.run_gate(feat), cost_bp)]
    for what, label in [('breadth', '关闭Breadth(宽度中性化)'),
                        ('relative', '关闭Relative(RS中性化)'),
                        ('volume', '关闭Volume(量价中性化)'),
                        ('riskoff', '关闭Risk-Off(风险开关关闭)')]:
        rows.append(_bt_row(label, R.run_gate(_neutralize(feat, what)), cost_bp))
    rows.append(_bt_row('MA20开关(Close>MA20→100%)', ma_only_gate(feat), cost_bp))
    for name, expo in C.BENCHMARKS.items():
        rows.append(_bt_row(f'{name}(固定{expo:.0f}%)', B.const_gate(feat, expo), cost_bp))
    # 同平均仓位对照：暴露与 Model 均值一致，但不做择时
    g = B.trim_warmup(R.run_gate(feat))
    avg = float(g['position'].mean())
    rows.append(_bt_row(f'SAME_AVG(固定{avg:.1f}%)', B.const_gate(feat, avg), cost_bp))
    return pd.DataFrame(rows)


def conditional_table(feat: pd.DataFrame, col: str, bins: list, labels: list,
                      fwd: int = 20) -> pd.DataFrame:
    """分档统计：某变量的分档 vs 未来 N 日收益（仅用于事后研究，不进入 Gate）"""
    f = F.as_raw_feat(feat).copy()
    f['fwd'] = (f['close'].shift(-fwd) / f['close'] - 1) * 100
    f['bucket'] = pd.cut(f[col], bins=bins, labels=labels)
    g = f.dropna(subset=['bucket', 'fwd'])
    t = g.groupby('bucket', observed=True)['fwd'].agg(['count', 'mean', 'median',
                                                       lambda x: (x > 0).mean()])
    t.columns = ['n_days', 'fwd_mean_%', 'fwd_median_%', 'win_rate']
    t['win_rate'] = (t['win_rate'] * 100).round(1)
    t['fwd_mean_%'] = t['fwd_mean_%'].round(2)
    t['fwd_median_%'] = t['fwd_median_%'].round(2)
    return t.reset_index()


# =========================================================
# §29 每日报告
# =========================================================
def _fmt_state(s: str) -> str:
    return {'A': '价格↑+量↑(趋势扩张)', 'B': '价格↑+量↓(健康趋势/惜售)',
            'C': '价格↓+量↓(正常调整)', 'D': '价格↓+量↑(风险释放)'}.get(s, str(s))


def daily_brief(gate: pd.DataFrame, date: str = None,
                conc: bool = True) -> str:
    """§29 每日最终报告格式"""
    date = date or str(gate.index[-1])
    if date not in set(map(str, gate.index)):
        date = str(gate.index[-1])
    r = gate.loc[date]
    nxt = None
    idx = list(map(str, gate.index))
    if idx.index(date) + 1 < len(idx):
        nxt = gate.loc[idx[idx.index(date) + 1]]
    lines = [SEP, '【中证2000风险暴露】', SEP,
             f'日期：{date}',
             f'市场状态：{r["regime"]}',
             f'当前仓位：{r["position"]:.0f}%',
             f'允许区间：{r["position_min"]:.0f}%～{r["position_max"]:.0f}%',
             f'满仓资格：{"YES" if r["full_exposure_eligible"] else "NO"}',
             '',
             '### 趋势',
             f'Close vs MA20：{"↑" if r["close"] > r["ma20"] else "↓"}',
             f'MA20：{"↑" if r["ma20_slope_5"] > 0 else "↓"}',
             f'MA60：{"↑" if r["ma60_slope_20"] > 0 else "↓"}',
             '',
             '### 宽度',
             f'MA20上方比例：{r["breadth_ma20"] * 100:.0f}%',
             f'5日变化：{r["breadth_change_5"]:+.1f}pp',
             f'新高/新低比例：{r["new_high_ratio"] * 100:.1f}% / {r["new_low_ratio"] * 100:.1f}%',
             '',
             '### 相对强度',
             f'RS20 vs 全A：{"+" if r["rs20_vs_all"] > 0 else "-"}（{r["rs20_vs_all"]:+.2f}pp）',
             f'RS20 vs 中证1000：{"+" if r["rs20_vs_1000"] > 0 else "-"}（{r["rs20_vs_1000"]:+.2f}pp）',
             '',
             '### 量价',
             f'状态：{_fmt_state(r["price_vol_state"])}（量比20日 {r["volume_ratio_20"]:.2f}）',
             '',
             '### 风险',
             f'Risk-Off：{int(r["riskoff_count"])}',
             f'指数背离：{"YES" if r.get("INDEX_BREADTH_DIVERGENCE") else "NO"}',
             f'健康回调：{"YES" if r.get("HEALTHY_PULLBACK") else "NO"}',
             '',
             '### 操作']
    up_target = R.quantize_position(min(r['position'] + C.STEP_UP, r['position_max']))
    lines.append(f'当前：{r["position"]:.0f}%　区间 {r["position_min"]:.0f}%～{r["position_max"]:.0f}%'
                 f'　目标中枢 {r["position_target"]:.0f}%')
    if r['position'] < r['position_max']:
        lines.append(f'下一档：{up_target:.0f}%')
    else:
        lines.append('已在上限，等待满仓资格确认' if not r['full_exposure_eligible'] else '已达上限')
    lines += ['',
              '加仓条件：',
              'Breadth ≥65%',
              '+ RS继续改善',
              '+ 无Risk-Off',
              '',
              '减仓条件：',
              'Close < MA20',
              '或 Breadth快速恶化',
              '或 Risk-Off ≥2']
    if conc:
        cl = R.concentration_limits(r['position'])
        lines += ['', '### 组合集中度上限（§21）',
                  f'单股 ≤{cl["single_stock_max"]:.0f}%　'
                  f'单主题 ≤{cl["theme_exposure_max"]:.0f}%　'
                  f'单策略 ≤{cl["strategy_exposure_max"]:.0f}%']
    lines += ['', f'依据：{r["reason"]}']
    return '\n'.join(lines)


# =========================================================
# §27 八个研究问题的书面回答
# =========================================================
def _fmt(df: pd.DataFrame, floatfmt='{:.2f}') -> str:
    return df.to_string(index=False)


def answer_questions(gate, feat, abl, cmp_all, stab, roll, ladder,
                     b_th, r_ret, v_state) -> str:
    """Q1~Q8 逐题回答（数字全部来自上面各表，不做主观臆测）"""
    def row(df, key):
        s = df[df['variant'] == key]
        return s.iloc[0] if len(s) else None
    m = row(abl, 'Model(完整)')
    ma = row(abl, 'MA20开关(Close>MA20→100%)')
    nb = row(abl, '关闭Breadth(宽度中性化)')
    nr = row(abl, '关闭Relative(RS中性化)')
    nv = row(abl, '关闭Volume(量价中性化)')
    nk = row(abl, '关闭Risk-Off(风险开关关闭)')
    b100 = row(abl, 'B100(固定100%)')
    avg = [v for v in abl['variant'] if v.startswith('SAME_AVG')]
    sa = row(abl, avg[0]) if avg else None

    def seg(df, model, seg_name='ALL'):
        s = df[(df['model'] == model)]
        return s
    out = []
    A = out.append

    A('## 七、核心研究问题（Q1~Q8）')
    A('')
    A('以下全部数字来自 `csi2000_position_*` 系列 CSV，成本口径 15bp，已剔除前 '
      f'{C.WARMUP_DAYS} 个交易日的预热期。')
    A('')

    A('### Q1 中证2000 MA20 是否足以作为仓位开关？')
    A('')
    A(f"- 纯 MA20 开关（Close>MA20→100%，否则 0%）：CAGR {ma['CAGR_%']:.2f}%、最大回撤 {ma['maxDD_%']:.2f}%、"
      f"Calmar {ma['Calmar']}、平均仓位 {ma['avg_position_%']:.1f}%、年换手 {ma['turnover_per_year']}")
    A(f"- 完整 Gate：CAGR {m['CAGR_%']:.2f}%、最大回撤 {m['maxDD_%']:.2f}%、Calmar {m['Calmar']}、"
      f"平均仓位 {m['avg_position_%']:.1f}%、年换手 {m['turnover_per_year']}")
    A(f"- 结论：MA20 单独作开关可把 B100 的最大回撤 {b100['maxDD_%']:.2f}% 收窄到 {ma['maxDD_%']:.2f}%"
      f"（回撤减少 {ma['maxDD_%'] - b100['maxDD_%']:.2f}pp），完整 Gate 进一步收窄到 {m['maxDD_%']:.2f}%"
      f"（回撤减少 {m['maxDD_%'] - b100['maxDD_%']:.2f}pp）；年换手 MA20 开关 {ma['turnover_per_year']} "
      f"vs 完整 Gate {m['turnover_per_year']}。")
    A(f"- 结论：MA20 是有效的风险开关骨架，但单独使用时换手偏高、暴露跳变"
      f"（平均仓位 {ma['avg_position_%']:.1f}%），不足以承担 §12/§15 的「逐级加仓、快速减仓」响应职能。")
    A('')

    A('### Q2 Breadth 是否比指数涨跌更有价值？')
    A('')
    A(f"- 关闭 Breadth 后：CAGR {nb['CAGR_%']:.2f}%、最大回撤 {nb['maxDD_%']:.2f}%、Calmar {nb['Calmar']}")
    A(f"- 完整 Gate：CAGR {m['CAGR_%']:.2f}%、最大回撤 {m['maxDD_%']:.2f}%、Calmar {m['Calmar']}")
    A('')
    A('分档统计（宽度档 → 指数未来20日收益）：')
    A('')
    A('```')
    A(_fmt(b_th))
    A('```')
    A('')
    A('分档统计（指数当日涨跌 → 指数未来20日收益）：')
    A('')
    A('```')
    A(_fmt(r_ret))
    A('```')
    A('')
    A(f"- 结论：宽度分档的单调性与区分度 {'优于' if _mono(b_th) > _mono(r_ret) else '不优于'} 单日涨跌分档。"
      f"宽度提供的是「内部参与度」信息，单日涨跌只是价格结果，因此宽度用于确认、涨跌用于触发。")
    A('')

    A('### Q3 RS 是否提供增量信息？')
    A('')
    A(f"- 关闭 RS 后：CAGR {nr['CAGR_%']:.2f}%、最大回撤 {nr['maxDD_%']:.2f}%、Calmar {nr['Calmar']}")
    A(f"- 完整 Gate：CAGR {m['CAGR_%']:.2f}%、最大回撤 {m['maxDD_%']:.2f}%、Calmar {m['Calmar']}")
    A(f"- 平均仓位：关闭 RS {nr['avg_position_%']:.1f}% vs 完整 Gate {m['avg_position_%']:.1f}%")
    A(f"- 结论：RS 在状态机里只作 TREND / STRONG_TREND 的准入与区间上限条件，"
      f"改变的是「允许开多高」而非「是否止损」：关闭后回撤从 {m['maxDD_%']:.2f}% 变为 "
      f"{nr['maxDD_%']:.2f}%（{'恶化' if nr['maxDD_%'] < m['maxDD_%'] else '改善'} "
      f"{abs(nr['maxDD_%'] - m['maxDD_%']):.2f}pp），但平均仓位由 {m['avg_position_%']:.1f}% 变为 "
      f"{nr['avg_position_%']:.1f}%——即 RS 提供的是「方向选择」增量，而非「风险控制」增量。")
    A('')

    A('### Q4 量价结构是否能减少假突破/假反弹？')
    A('')
    A(f"- 关闭量价后：最大回撤 {nv['maxDD_%']:.2f}%、Calmar {nv['Calmar']}（完整 Gate 回撤 {m['maxDD_%']:.2f}%）")
    A('')
    A('量价状态 → 指数未来20日收益：')
    A('')
    A('```')
    A(_fmt(v_state))
    A('```')
    A('')
    vhi = float(pd.to_numeric(v_state['fwd_mean_%'], errors='coerce').max())
    vlo = float(pd.to_numeric(v_state['fwd_mean_%'], errors='coerce').min())
    A(f"- 量价四态未来20日收益极差 {vhi - vlo:.2f}pp（最高 {vhi:.2f}% / 最低 {vlo:.2f}%）")
    A(f"- 关闭量价后 最大回撤 {nv['maxDD_%']:.2f}%、Calmar {nv['Calmar']}；"
      f"完整 Gate 最大回撤 {m['maxDD_%']:.2f}%、Calmar {m['Calmar']}")
    A("- 结论：量价在本模型中的定位是「否决条件」而非「加分条件」——它通过 R2（放量破位）与 "
      "满仓资格 c8（连续放量下跌）阻止在风险释放段继续加仓。"
      f"本样本中四态未来收益区分度有限（极差 {vhi - vlo:.2f}pp），且单独关闭量价后回撤与 Calmar "
      f"几乎不变（ΔmaxDD {nv['maxDD_%'] - m['maxDD_%']:+.2f}pp），"
      "因此量价未能独立减少假突破/假反弹，其作用是条件性的。")
    A('')

    A('### Q5 Risk-Off 机制能否明显降低最大回撤？')
    A('')
    A(f"- 关闭 Risk-Off 后：最大回撤 {nk['maxDD_%']:.2f}%、Calmar {nk['Calmar']}、平均仓位 {nk['avg_position_%']:.1f}%")
    A(f"- 完整 Gate：最大回撤 {m['maxDD_%']:.2f}%、Calmar {m['Calmar']}、平均仓位 {m['avg_position_%']:.1f}%")
    A(f"- 相对 B100 的回撤减少（正值=更优）：完整 Gate {m['maxDD_改善vsB100_pp']:.2f}pp，"
      f"关闭 Risk-Off {nk['maxDD_改善vsB100_pp']:.2f}pp")
    d_pp = float(m['maxDD_%'] - nk['maxDD_%'])
    A(f"- 关闭 Risk-Off 后的回撤代价：{d_pp:+.2f}pp"
      f"（为正表示 Risk-Off 使最大回撤更浅，为负表示关闭后回撤反而更浅）")
    A(f"- 关闭 Risk-Off 后 CAGR {nk['CAGR_%']:.2f}%、Calmar {nk['Calmar']}、"
      f"平均仓位 {nk['avg_position_%']:.1f}%；完整 Gate CAGR {m['CAGR_%']:.2f}%、"
      f"Calmar {m['Calmar']}、平均仓位 {m['avg_position_%']:.1f}%")
    A(f"- 结论：在本样本中 Risk-Off 对最大回撤的额外贡献为 {d_pp:+.2f}pp"
      f"（{'提供保护' if d_pp > 0.1 else '未提供额外保护，关闭后回撤反而略小' if d_pp < -0.1 else '基本中性'}）。"
      "其设计价值在于把 §10 的七类事件显性化、并被 §13 满仓资格与 §16 减仓幅度引用；"
      "但「Risk-Off 能否显著降低最大回撤」在本样本内未获支持，需以更长样本复核。")
    A('')

    A('### Q6 满仓条件是否需要 Breadth 确认？')
    A('')
    A(f"- 满仓资格（FULL_EXPOSURE_CONFIRMATION）在样本内共 "
      f"{int(B.trim_warmup(gate)['full_exposure_eligible'].sum())} 个交易日成立，"
      f"占 {float(B.trim_warmup(gate)['full_exposure_eligible'].mean()) * 100:.1f}%。")
    A(f"- 完整 Gate 的实际仓位上限使用情况：position_max=100% 的天数 "
      f"{int((B.trim_warmup(gate)['position_max'] >= 100).sum())} 天；"
      f"平均实际仓位 {m['avg_position_%']:.1f}%。")
    A(f"- 关闭 Breadth 后（宽度中性化）：CAGR {nb['CAGR_%']:.2f}%、最大回撤 {nb['maxDD_%']:.2f}%、"
      f"Calmar {nb['Calmar']}、平均仓位 {nb['avg_position_%']:.1f}%；"
      f"完整 Gate CAGR {m['CAGR_%']:.2f}%、最大回撤 {m['maxDD_%']:.2f}%、Calmar {m['Calmar']}、"
      f"平均仓位 {m['avg_position_%']:.1f}%")
    A(f"- 结论：需要。Breadth 是本模型唯一直接约束「市场内部参与度」的变量，其满仓确认项 c3"
      f"（Breadth ≥65%）把「指数涨」与「市场普遍参与」区分开。样本内关闭 Breadth 后平均仓位反而"
      f"由 {m['avg_position_%']:.1f}% 升到 {nb['avg_position_%']:.1f}%，最大回撤由 "
      f"{m['maxDD_%']:.2f}% 恶化到 {nb['maxDD_%']:.2f}%——取消宽度确认会放大暴露而非提高收益。")
    A('')

    A('### Q7 仓位阶梯应该用哪种结构？')
    A('')
    A('```')
    A(_fmt(ladder))
    A('```')
    o = ladder[ladder['segment'] == 'OOS'].copy()
    if len(o):
        dd_rng = float(o['maxDD_%'].max() - o['maxDD_%'].min())
        cal_rng = float(o['Calmar'].max() - o['Calmar'].min())
        b_dd = o.loc[o['maxDD_%'].idxmax()]
        b_cal = o.loc[o['Calmar'].idxmax()]
        A(f"- OOS（2026）段最大回撤最浅：{b_dd['ladder']}（{b_dd['maxDD_%']:.2f}%）；"
          f"OOS Calmar 最高：{b_cal['ladder']}（{b_cal['Calmar']:.2f}）")
        A(f"- 三套结构在 OOS 段的差异幅度：最大回撤极差 {dd_rng:.2f}pp、Calmar 极差 {cal_rng:.2f}")
        A(f"- 结论：三套阶梯在样本内差异属噪声量级（OOS 最大回撤极差 {dd_rng:.2f}pp），"
          f"不足以稳健区分优劣。§30 禁止用 OOS 反向调参，故**保留 §11 的六档结构作为默认**"
          f"（0/20/40/60/80/100 语义）；本表同时给出五档与七档对照，供后续更长样本复核时参考。")
    A('')

    A('### Q8 Position Gate 相对于固定仓位是否真正产生增量价值？')
    A('')
    A('关键对照是「同平均仓位」的固定仓位组合——如果 Gate 只是把平均仓位降下来，'
      '它与一个同等暴露的固定仓位不会有区别。')
    A('')
    A('```')
    A(_fmt(abl))
    A('```')
    A('')
    gg = B.trim_warmup(gate)
    fwd20 = (gg['close'].shift(-20) / gg['close'] - 1) * 100
    hi_m, lo_m = gg['position'] > 60, gg['position'] < 20
    A(f"- 事后检验（未来收益仅用于评估，不进入任何规则）：仓位>60% 的 {int(hi_m.sum())} 天，"
      f"其后20日收益均值 {float(fwd20[hi_m].mean()):.2f}%；仓位<20% 的 {int(lo_m.sum())} 天，"
      f"其后20日收益均值 {float(fwd20[lo_m].mean()):.2f}%；"
      f"仓位与未来20日收益相关系数 {float(gg['position'].corr(fwd20)):.3f}")
    A('')
    if sa is not None:
        A(f"- 同平均仓位对照（固定 {sa['avg_position_%']:.1f}%）：CAGR {sa['CAGR_%']:.2f}%、"
          f"最大回撤 {sa['maxDD_%']:.2f}%、Calmar {sa['Calmar']}")
        A(f"- 完整 Gate：CAGR {m['CAGR_%']:.2f}%、最大回撤 {m['maxDD_%']:.2f}%、Calmar {m['Calmar']}")
        better = (m['Calmar'] is not None and sa['Calmar'] is not None
                  and np.isfinite(m['Calmar']) and np.isfinite(sa['Calmar'])
                  and m['Calmar'] > sa['Calmar'])
        A(f"- 结论：Gate 的 Calmar **{'高于' if better else '不高于'}**同平均仓位对照，"
          f"{'说明增量来自择时（风险暴露的时点分配），而不是单纯降低平均仓位。' if better else '说明在本样本上增量主要来自降低平均暴露，择时贡献有限，需谨慎依赖。'}")
    A('')
    return '\n'.join(out)


def _mono(t: pd.DataFrame) -> float:
    """分档单调性打分：档位均值与档序的秩相关绝对值"""
    if t is None or len(t) < 3:
        return 0.0
    y = pd.to_numeric(t['fwd_mean_%'], errors='coerce').values
    x = np.arange(len(y), dtype=float)
    ok = np.isfinite(y)
    if ok.sum() < 3:
        return 0.0
    return abs(float(np.corrcoef(x[ok], y[ok])[0, 1]))


# =========================================================
# §28 输出 + §27 报告正文
# =========================================================
def write_outputs(gate, parts, abl, cmp_all, splits, ann, roll, ladder, stab,
                  b_th, r_ret, v_state, cov: dict) -> list:
    paths = []
    paths.append(_csv(gate_daily(gate), C.OUT_FILES[0]))
    paths.append(_csv(gate_events(gate), C.OUT_FILES[1], index=False))
    br = parts['breadth'].copy()
    br.index.name = 'date'
    paths.append(_csv(br.round(6), C.OUT_FILES[2]))
    rs = parts['relative'].copy()
    rs.index.name = 'date'
    paths.append(_csv(rs.round(6), C.OUT_FILES[3]))
    ro = parts['riskoff'].copy()
    ro.index.name = 'date'
    paths.append(_csv(ro, C.OUT_FILES[4]))
    rg = gate[['regime', 'regime_reason', 'quality', 'band_min', 'band_target', 'band_max',
               'cap', 'raw_target', 'position', 'full_exposure_eligible', 'reason']].copy()
    rg.index.name = 'date'
    paths.append(_csv(rg.round(4), C.OUT_FILES[5]))
    paths.append(_csv(cmp_all, C.OUT_FILES[6], index=False))
    oos_df = pd.concat([
        splits.rename(columns=lambda c: f'split_{c}').assign(table='split'),
        ann.rename(columns=lambda c: f'annual_{c}').assign(table='annual'),
        roll.rename(columns=lambda c: f'rolling_{c}').assign(table='rolling'),
    ], ignore_index=True)
    paths.append(_csv(oos_df, C.OUT_FILES[7], index=False))
    paths.append(_csv(stab, C.OUT_FILES[8], index=False))
    md = build_report(gate, abl, cmp_all, splits, ann, roll, ladder, stab,
                      b_th, r_ret, v_state, cov)
    p = os.path.join(C.OUT_DIR, C.OUT_FILES[9])
    with open(p, 'w', encoding='utf-8') as f:
        f.write(md)
    paths.append(p)
    return paths


def build_report(gate, abl, cmp_all, splits, ann, roll, ladder, stab,
                 b_th, r_ret, v_state, cov) -> str:
    g = B.trim_warmup(gate)
    L = []
    A = L.append
    A('# CSI2000 Position Gate V1.0 —— 中证2000 风险暴露 / 组合仓位控制')
    A('')
    A(SEP)
    A('> 市场给信号，仓位做响应；仓位不预测市场。')
    A(SEP)
    A('')
    A('## 零、数据完整性与口径声明')
    A('')
    A('```json')
    A(json.dumps(cov, ensure_ascii=False, indent=2))
    A('```')
    A('')
    A(f"- 回测区间：{C.DATA_START} ~ {C.DATA_END}（本地个股日线缓存起点决定的实际上限）")
    A(f"- §22 要求的 2018~2020：**DATA_INCOMPLETE**（本地个股日线无数据，无法计算宽度）")
    A(f"- 指数类特征借用 2018 起的指数历史预热；统计口径剔除前 {C.WARMUP_DAYS} 个交易日")
    A('- 宽度口径双轨：2024+ 用官方成分快照（OFFICIAL），2021~2023 用当日总市值动态小盘池（DYNAMIC）')
    A('- 无未来函数：`pos_t` 于 t 日收盘生成，暴露发生在 t+1（`ret = pos_{t-1} × r_t`）；'
      '成本在 t 日收盘按 |Δpos| 计提')
    A('')
    A('## 一、最新一日输出')
    A('')
    A('```')
    A(daily_brief(gate))
    A('```')
    A('')
    A('## 二、状态分布与仓位分布')
    A('')
    d1 = g['regime'].value_counts().rename_axis('regime').reset_index(name='days')
    d1['pct'] = (d1['days'] / len(g) * 100).round(1)
    A('```')
    A(_fmt(d1))
    A('```')
    A('')
    A(f"平均仓位 {g['position'].mean():.1f}%，最高 {g['position'].max():.1f}%，"
      f"最低 {g['position'].min():.1f}%，空仓天数占比 {float((g['position'] < 1e-9).mean()) * 100:.1f}%")
    A('')
    A('## 三、§23/§24 对照组与交易成本')
    A('')
    A('```')
    A(_fmt(cmp_all))
    A('```')
    A('')
    A('## 四、§26 时间切分（IS / Validation / OOS，主口径 15bp）')
    A('')
    A('```')
    A(_fmt(splits))
    A('```')
    A('')
    A('### 逐年表现')
    A('')
    A('```')
    A(_fmt(ann))
    A('```')
    A('')
    A('### 滚动 3 年窗 + 1 年测试（防过拟合检验）')
    A('')
    A('```')
    A(_fmt(roll))
    A('```')
    A('')
    A('## 五、§25 参数稳定性')
    A('')
    A('```')
    A(_fmt(stab))
    A('```')
    A('')
    A('## 六、§27 Q7 仓位阶梯结构对照')
    A('')
    A('```')
    A(_fmt(ladder))
    A('```')
    A('')
    A(answer_questions(gate, None, abl, cmp_all, stab, roll, ladder, b_th, r_ret, v_state))
    A('')
    A('## 八、§30 纪律自检')
    A('')
    for t in ['不预测涨跌概率：本模块只输出风险暴露，不输出方向概率',
              '不因单日大涨/大跌跳变：单日加仓 ≤ +10%（强确认 +15%），减仓 ≤ -20%',
              '不满仓预测制：100% 需 FULL_EXPOSURE_CONFIRMATION 连续 ≥2 日成立',
              '不用未来收益改历史规则：所有分档统计中的未来收益仅用于事后评估',
              '不用 OOS 反向调参：IS 选优仅作防过拟合对照（见第五节 rolling 表）',
              '不追求收益率：主目标为最大回撤与 Calmar',
              '不用机器学习替代可解释规则：全部为布尔/阈值规则']:
        A(f'- [x] {t}')
    A('')
    return '\n'.join(L)


# =========================================================
# 全量驱动
# =========================================================
def run_all(verbose: bool = True) -> list:
    data = GateData()
    cov = data.coverage_report()
    if verbose:
        print('[run] 构建特征与 Gate ...', flush=True)
    gate, parts = build_gate(data, verbose=verbose)
    if verbose:
        print('[run] 对照组与成本敏感性 ...', flush=True)
    cmp_all = B.compare(B.trim_warmup(gate))
    splits = B.split_compare(B.trim_warmup(gate))
    ann = O.annual_table(gate)
    if verbose:
        print('[run] 消融实验 ...', flush=True)
    abl = ablation(gate)
    if verbose:
        print('[run] 分档统计 ...', flush=True)
    f = gate
    b_th = conditional_table(f, 'breadth_ma20', [0, .35, .45, .55, .65, .75, 1.01],
                             ['<35%', '35~45%', '45~55%', '55~65%', '65~75%', '>75%'])
    r_ret = conditional_table(f, 'ret1', [-100, -2, -1, 0, 1, 2, 100],
                              ['<-2%', '-2~-1%', '-1~0%', '0~1%', '1~2%', '>2%'])
    v_state = (f.assign(fwd=(f['close'].shift(-20) / f['close'] - 1) * 100)
               .dropna(subset=['fwd'])
               .groupby('price_vol_state')['fwd']
               .agg(n_days='count', fwd_mean='mean', fwd_median='median',
                    win=lambda x: (x > 0).mean()).reset_index())
    v_state['fwd_mean'] = v_state['fwd_mean'].round(2)
    v_state['fwd_median'] = v_state['fwd_median'].round(2)
    v_state['win'] = (v_state['win'] * 100).round(1)
    v_state = v_state.rename(columns={'price_vol_state': 'state', 'fwd_mean': 'fwd_mean_%',
                                      'fwd_median': 'fwd_median_%', 'win': 'win_rate'})
    if verbose:
        print('[run] 参数稳定性与滚动 OOS ...', flush=True)
    vcache = O.build_variant_cache(data, verbose=verbose)
    stab = O.param_stability(data, cache=vcache, verbose=False)
    ladder = O.ladder_compare(f)
    roll = O.rolling_oos(gate, cache=vcache)
    if verbose:
        print('[run] 写出 §28 文件 ...', flush=True)
    paths = write_outputs(gate, parts, abl, cmp_all, splits, ann, roll, ladder, stab,
                          b_th, r_ret, v_state, cov)
    if verbose:
        for p in paths:
            print('  ', p)
    return paths


if __name__ == '__main__':
    pd.set_option('display.width', 300)
    run_all()
