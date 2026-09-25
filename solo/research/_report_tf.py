# -*- coding: utf-8 -*-
"""三花聚顶研究 · 最终报告生成（第八阶段）

只读取本科研模块自身产物（research/out/*），不触碰 HVT/DLG/F120/主题/突破/实盘任何代码。
输出目录: report_daily/research/
"""
import os
import json
import glob
import datetime

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'out')
RD = r'd:\mystock\solo\report_daily'
RES = os.path.join(RD, 'research')
DATE = '20260925'
os.makedirs(RES, exist_ok=True)


# ────────────────────────── 读取证据 ──────────────────────────
def _rp(name):
    return pd.read_csv(os.path.join(OUT, name), encoding='utf-8-sig')


META = json.load(open(os.path.join(OUT, 'tf_meta.json'), encoding='utf-8'))
G = _rp('tf_groups.csv')          # 四组对照
D = _rp('tf_defs.csv')            # H1-H6 定义比较
E = _rp('tf_entries.csv')         # Entry 比较
L = _rp('tf_layers.csv')          # 分层
LI = _rp('tf_layers_industry.csv') if os.path.exists(os.path.join(OUT, 'tf_layers_industry.csv')) else pd.DataFrame()
X = _rp('tf_experiments.csv')     # 参数搜索
R = _rp('tf_robust.csv')          # 稳健区间
Y = _rp('tf_yearly.csv')          # 逐年
W = _rp('tf_walkforward.csv')     # Walk-Forward
H = _rp('tf_hvt_orth.csv')        # HVT 正交
FP = _rp('tf_failure_patterns.csv')
FB = _rp('tf_failures_breakout.csv')
FL = _rp('tf_failures.csv')
dim = pd.read_parquet(os.path.join(OUT, 'tf_events_dim.parquet')).reset_index(drop=True)


def gv(df, tag, hor, col='adv'):
    m = (df['tag'] == tag) & (df['horizon'] == hor)
    if not m.any():
        return None
    return float(df.loc[m, col].iloc[0])


def gm(df, tag, hor, col):
    m = (df['tag'] == tag) & (df['horizon'] == hor)
    return float(df.loc[m, col].iloc[0]) if m.any() else None


def pct(x, d=2):
    return 'n/a' if x is None or (isinstance(x, float) and not np.isfinite(x)) else ('%+.*f%%' % (d, 100 * x))


def sgn(x, d=2):
    return 'n/a' if x is None or (isinstance(x, float) and not np.isfinite(x)) else ('%+.*f' % (d, x))


def num(x, d=2):
    return 'n/a' if x is None or (isinstance(x, float) and not np.isfinite(x)) else ('%.*f' % (d, x))


def pct0(x):
    return '样本不足' if x is None or not np.isfinite(x) else '%.0f%%' % (100 * x)


def pct1(x):
    return '样本不足' if x is None or not np.isfinite(x) else '%.1f%%' % (100 * x)


# ────────────────────────── 1) 事件明细 CSV ──────────────────────────
STRUCT_COLS = ['f1_off', 'f2_off', 'f3_off', 'k_off', 'span', 'P1', 'P2', 'P3', 'V0', 'V1', 'V2', 'V3',
               'volume_decay', 'price_progression', 'vol_vs_t0', 'struct_dd', 'ma_conv', 'ma5_ma10',
               'ma10_ma20', 'ma_stack_up', 'min_close_vs_t0', 'hi_lvl', 'n_flower', 'k_A', 'k_B']
RET_COLS = ['A_k', 'A_r1', 'A_r3', 'A_r5', 'A_r10', 'A_r20', 'A_mfe', 'A_mae', 'A_path_dd',
            'Cb0_k', 'Cb0_r1', 'Cb0_r3', 'Cb0_r5', 'Cb0_r10', 'Cb0_r20', 'Cb0_mfe', 'Cb0_mae', 'Cb0_path_dd',
            'brk_b0_vr', 'brk_b0_abv', 'brk_b0_gap', 'brk_b0_idc', 'brk_b0_pos']
T0_COLS = ['ts_code', 'name', 'trade_date', 'close', 'pct_chg', 't0_vr20', 't0_amount', 'one_word',
           'opened_board', 't0_vs_ma20', 't0_vs_ma60', 't0_ret20prev', 't0_quality', 'd_H1', 'd_H2',
           'd_H3', 'd_H4', 'd_H5', 'd_H6']

ALL_COLS = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet')).columns
want = [c for c in (T0_COLS + STRUCT_COLS + RET_COLS) if c in ALL_COLS]
base = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet'), columns=want)
extra = [c for c in dim.columns if c not in base.columns]
ev = base.join(dim[extra]).copy()
ev['is_bse'] = dim['board'].values == 'BSE'
ev.to_csv(os.path.join(RES, 'three_flower_events_%s.csv' % DATE), index=False, encoding='utf-8-sig')

# ────────────────────────── 2) 实验记录 CSV ──────────────────────────
X.to_csv(os.path.join(RES, 'three_flower_experiments_%s.csv' % DATE), index=False, encoding='utf-8-sig')

# ────────────────────────── 3) 失败案例 CSV ──────────────────────────
cc = ['section', 'key', 'n', 'share', 'fail_5', 'fail_10', 'fail_20', 'lift',
      'med_5', 'med_10', 'med_20', 'win_5', 'win_10', 'win_20', 'med_mfe', 'med_mae', 'fast_break']


def _mk(d, section, keycol, extra_key=None):
    o = pd.DataFrame(index=d.index, columns=cc)
    o['section'] = section
    o['key'] = d[keycol].astype(str) if extra_key is None else (d[extra_key].astype(str) + '=' + d[keycol].astype(str))
    for c in cc:
        if c in ('section', 'key'):
            continue
        if c in d.columns:
            o[c] = d[c].values
    return o


failrows = [
    _mk(FL, 'A_H1_确认日收盘_特征分箱', 'level', 'feature'),
    _mk(FP[FP['group'].astype(str).str.startswith('A')], 'A_H1_确认日收盘_失败模式', 'pattern'),
    _mk(FP[FP['group'].astype(str).str.startswith('C')], 'C_H1_突破收盘_失败模式', 'pattern'),
    _mk(FB, 'C_H1_突破收盘_突破量能分档', 'band'),
]
FD = pd.concat(failrows, ignore_index=True)
FD.to_csv(os.path.join(RES, 'three_flower_failures_%s.csv' % DATE), index=False, encoding='utf-8-sig')

# ────────────────────────── 4) 结论性汇总 CSV ──────────────────────────
STATUS = 'CONDITIONAL'
NAME = 'ThreeFlower_Conditional_V1'

h1_oos10 = gv(D.assign(tag='H1', horizon=10), 'H1', 10)
h1_oos = Y[(Y['def'] == 'H1') & (Y['year'] == 2026)]
h1_oos_10 = float(h1_oos['adv10'].iloc[0]) if len(h1_oos) else None
h1_oos_20 = float(h1_oos['adv20'].iloc[0]) if len(h1_oos) else None
h1_oos_n = int(h1_oos['n'].iloc[0]) if len(h1_oos) else 0

hdr = []
for tag, note in [('A_全体首板(offset10)', '基准组 BM1'),
                  ('B_三花形成', '核心三花（无突破）'),
                  ('C_三花+突破', '核心三花 + 二次突破'),
                  ('D_三花+放量突破(VR>=1.2)', '突破且放量'),
                  ('D2_三花+缩量突破(VR<1.2)', '突破且缩量'),
                  ('F_首板后无三花(BM3)', '首板后未形成三花')]:
    r = {'item': tag, 'note': note, 'n': gm(G, tag, 10, 'n')}
    for N in (5, 10, 20):
        r['adv_%d' % N] = gv(G, tag, N)
        r['med_%d' % N] = gm(G, tag, N, 'med')
        r['win_%d' % N] = gm(G, tag, N, 'win')
        r['pf_%d' % N] = gm(G, tag, N, 'pf')
    hdr.append(r)
for tag, note in [('H1', '价格不降+量递减'), ('H2', '三次缩量调整'), ('H3', '价格重心抬升'),
                  ('H4', '均线聚合'), ('H5', '量价+均线复合'), ('H6', '完整三花结构')]:
    r = {'item': 'DEF_' + tag, 'note': note, 'n': gm(D, tag, 10, 'n')}
    for N in (5, 10, 20):
        r['adv_%d' % N] = gv(D, tag, N)
        r['med_%d' % N] = gm(D, tag, N, 'med')
        r['win_%d' % N] = gm(D, tag, N, 'win')
        r['pf_%d' % N] = gm(D, tag, N, 'pf')
    yy = Y[(Y['def'] == tag) & (Y['year'] == 2026)]
    r['oos_adv10'] = float(yy['adv10'].iloc[0]) if len(yy) else None
    r['oos_adv20'] = float(yy['adv20'].iloc[0]) if len(yy) else None
    ww = W[W['def'] == tag]
    r['wf_ok_rate'] = float(ww['ok'].mean()) if len(ww) else None
    hdr.append(r)
HD = pd.DataFrame(hdr)
HD.to_csv(os.path.join(RES, 'three_flower_research_%s.csv' % DATE), index=False, encoding='utf-8-sig')

# ────────────────────────── 5) JSON ──────────────────────────
def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(float(o)) else float(o)
    if isinstance(o, float):
        return None if not np.isfinite(o) else o
    return o


SUMMARY = {
    'research': '首板涨停后三花聚顶量价结构研究',
    'version': 'V1.0',
    'report_date': DATE,
    'status': STATUS,
    'final_product': NAME,
    'data': {
        'source': '项目现有 Tushare 缓存 D:/mystock/cache_daily/stock_data.db（未新增下载）',
        'daily_cache': '20210104~20260924 / 7102222 行 / 5781 只代码',
        'panel_actual': '2021-01-04 ~ 2026-09-24（原计划 2018 起，缓存不含 2018-2020，已按任务书第3节自动调整并声明）',
        'daily_basic_from': '2023-01-03（市值/换手分层仅覆盖 2023 起）',
        'survivorship': '股票池取自缓存全量 5781 只代码，未按当前在市清单过滤；2021-2023 出现但 2026-09-24 已不在市的代码 223 只仍保留在样本中',
        'cost': '双边滑点 0.10% + 双边佣金 0.025% + 印花税 0.05% = 0.30%/次',
    },
    'sample': {
        'first_board_events': int(META['n_first_board']),
        'core_three_flower': int(META['n_core_tf']),
        'three_flower_with_breakout': 4580,
        'definition_hits': {'H1': 1740, 'H2': 380, 'H3': 1152, 'H4': 9971, 'H5': 222, 'H6': 178},
    },
    'four_group_contrast': _clean(hdr[:6]),
    'definition_compare': _clean(hdr[6:]),
    'verdict': {
        'three_flower_structure_itself': 'POSITIVE（H1 族：跨年 6/6 全正、Walk-Forward 一致率 100%、OOS T+10 +1.72%）',
        'three_flower_plus_breakout': 'NO ALPHA（C 组 T+10 -0.04%；放量突破 D 组 -0.88%；仅缩量突破 VR<1.2 有 +0.97% 但不稳健）',
        'alpha_source': '贡献 Alpha 的是「三花结构识别」本身，不是「二次突破」',
        'absolute_return_caveat': 'H1 T+10 中位 +0.06%、胜率 50%、失败率 49.9% —— 优势是相对首板基准的（+1.10pp），绝对收益接近 0，宜作排序/优选因子而非独立择时策略',
    },
}
json.dump(_clean(SUMMARY), open(os.path.join(RES, 'three_flower_research_%s.json' % DATE), 'w',
                               encoding='utf-8'), ensure_ascii=False, indent=2)

# ────────────────────────── 6) 最终策略 JSON ──────────────────────────
STRATEGY = {
    'strategy_name': NAME,
    'status': STATUS,
    'base_research': 'ThreeFlower V1.0 / 首板涨停后三花聚顶量价结构研究',
    'verdict_one_line': '三花结构（价格不降+量递减）本身具备相对首板基准的正超额且跨年稳健；但「三花+二次突破」无 Alpha。'
                        '结论为条件有效：结构有效、突破无效，且优势集中在低换手/小市值/弱势或熊市环境。',
    'event': {
        'type': 'FIRST_LIMIT_UP',
        'lookback_no_limit_up': 20,
        'limit_up_recognition': '按板块分别判定（主板10% / 创业板·科创板20% / 北交所30% / ST主板5%），'
                                '并对首板质量按一字板、炸板、量比等分级',
    },
    'structure': {
        'observation_window': 'T+0 ~ T+15（W_OBS=15 交易日）',
        'flower_definition': '摆动低点（半径 R=1）作为「花」，最少 3 花；结构选择与定义筛选分离，'
                             '以确认日最早的三元组为准（SEL_RULE=earliest）',
        'family': 'H1（价格重心不下降 + 成交量递减）',
        'volume_decay': 'V2<=V1*(1+10%) 且 V3<=V2*(1+10%)；稳健区间 volume_tolerance 0%~20%',
        'price_progression': 'P2>=P1*(1-1%) 且 P3>=P2*(1-1%)；稳健区间 price_tolerance 0%~3%',
        'max_drawdown': '结构内回撤 CORE_DD<=25%（H1 不额外约束；额外收紧至 8%~12% 无稳健增益）',
        'ma_convergence': 'H1 不使用均线收敛条件（实测 MA 收敛无增量贡献，仅 H4/H5/H6 使用）',
        'no_lookahead': '花在 d 日成立、d+3 日方可确认（c3=f3+R）；所有条件仅用 offset<=确认日 的数据',
    },
    'entry': {
        'type': 'CONFIRM_CLOSE',
        'primary': '三花确认日收盘买入（Entry A）',
        'alternative': '三花确认日次日开盘买入（Entry B，超额与 A 等价）',
        'breakout_buffer': '不建议——突破类买点（buffer 0/0.5%/1%/2%）全期超额均 <= 0，且 buffer 越大越差',
        'volume_confirmation': '不建议放量确认——突破 VR>=1.2 显著为负；仅 VR<1.2 缩量突破在 T+10 有 +0.97% 但 Walk-Forward 仅 33% 一致',
    },
    'filters': {
        'turnover': '优先 Low 换手（T+10 +3.60pp）；回避 High 换手（+0.12pp，失败率 55.3%）',
        'market_cap': '优先 Small（+1.27pp），Mid 最弱（+0.17pp）；注意市值分层仅覆盖 2023 起',
        'regime': 'bear（+3.14pp, n=184）与 weak（+1.22pp）最优；strong（+1.10pp）中性偏差；neutral 最弱（+0.61pp）',
        'first_board_quality': '优先 Strong（+2.04pp）；Weak 需回避（失败率 52.8%）',
        'board': '主板为主（+1.07pp, n=1574）；创业板 +1.28pp 但样本 130；科创板 +6.60pp 仅 n=34 不足采信；北交所样本 2 只得出',
    },
    'risk': {
        'structure_failure': '结构内跌破确认日实体或首板收盘（min_close_vs_t0 < -6%）',
        'max_hold': 'T+20（超额在 T+10~T+20 见顶，T+20 之后未测）',
        'failure_base_rate': '49.9%（H1 + 确认日收盘，T+10 口径）',
        'worst_patterns': ['高换手 + 确认过早（59.4%）', '高换手前 1/3 分位（55.3%）', '高换手 + 花数<=3（54.2%）'],
    },
    'expected_horizon': ['T+3', 'T+5', 'T+10', 'T+20'],
    'performance': {
        'H1_all': {'n': 1738, 'adv5': 0.00556, 'adv10': 0.0110, 'adv20': 0.0152,
                   'med10': 0.0006, 'win10': 0.50, 'pf10': 1.28, 't10': 5.7},
        'H1_yearly_adv10': {'2021': 0.0143, '2022': 0.0246, '2023': 0.0118, '2024': 0.0047,
                            '2025': 0.0053, '2026': 0.0172},
        'H1_oos_2026': {'n': h1_oos_n, 'adv10': h1_oos_10, 'adv20': h1_oos_20},
        'benchmark_BM1_first_board': {'med10': -0.0131, 'med20': -0.0208, 'win10': 0.44},
        'walk_forward_ok_rate': 1.0,
    },
    'validation': {
        'train': '2021-01-04 ~ 2023-12-31（H1 adv10 +1.43%/+2.46%/+1.18%）',
        'validation': '2024-01-01 ~ 2025-12-31（H1 adv10 +0.47%/+0.53%）',
        'oos': '2026-01-01 ~ 2026-09-24（H1 adv10 +1.72%%，adv20 +1.44%%，n=%d）' % h1_oos_n,
        'robustness': 'price_tolerance 0~3% / volume_tolerance 0~20% / 观测窗口 10~15 日均保持正超额；'
                      '突破 buffer 与突破 VR 维度单调恶化为负',
        'walk_forward': 'H1 一致率 100%（2024/2025/2026 三次逐年滚动全部 OK）；H4 33%；H5/H6 0%',
    },
    'hvt_orthogonality': {
        'same_day_overlap': '0.0%（0/552，HVT 窗口内三花确认日命中）',
        'near3_overlap': '0.7%（4/552）',
        'same_first_board_overlap': '10.1%（56/552，随机基准 0.23%）',
        'conclusion': '三花确认时点与 HVT 触发时点完全不同日（重合 0%）→ 在时序上正交；'
                      '仅首板层面有部分重合。重合样本（n=56）无额外收益（T+10 差 +0.01%，T+20 差 -2.73%）。',
    },
    'failure_conditions': [
        '三花确认日换手率处于市场前 1/3 分位（失败率 55.3%，lift 1.11）',
        '确认日过早（<=T+8，失败率 51.9%，中位 T+20 -1.16%）',
        '花数 <=3（失败率 51.6%）',
        '首板质量 Weak（失败率 52.8%，中位 T+10 -0.45%）',
        '介入方式选择「突破买入」（C 组失败率 56.3%，且突破量能越大失败率越高）',
        '结构内跌破首板收盘 -6% 以上且未收复',
    ],
    'integration': {
        'auto_trade_integration': False,
        'note': '本阶段仅产出科研结论与因子定义，是否接入实盘由用户决定；未修改 HVT/DLG/F120/主题/突破/实盘任何模块。',
    },
}
json.dump(_clean(STRATEGY), open(os.path.join(RES, 'three_flower_strategy_v1.json'), 'w',
                                 encoding='utf-8'), ensure_ascii=False, indent=2)

# ────────────────────────── 7) Markdown 报告 ──────────────────────────
SEP = '\u2550' * 40
SUB = '\u2500' * 40


def trow(cells):
    return '| ' + ' | '.join(str(c) for c in cells) + ' |'


L_ = []
A = L_.append
A('# 首板涨停后三花聚顶量价结构研究 V1.0')
A('')
A('报告日期：%s ｜ 数据区间：2021-01-04 ~ 2026-09-24 ｜ 状态：**%s** ｜ 产出：`%s`' % (DATE, STATUS, NAME))
A('')
A('数据来源：项目现有 Tushare 缓存（未新增下载）｜ 成本口径：滑点 0.10% + 佣金 0.025% + 印花税 0.05% = 0.30%/次')
A('')
A(SEP)
A('')

# ── 1
A('## 1. 研究结论')
A('')
A('**研究状态：%s**' % STATUS)
A('')
A('**核心发现**')
A('')
A('在 2021-2026 年样本中，共有首板事件 33401 次，其中形成核心三花结构 13269 次（39.7%）。'
  '符合 H1 定义（价格重心不下降 + 成交量递减）的首板后三花事件共 1738 次。'
  'H1 三花形成后 T+5 的配对超额中位数为 +0.56%，T+10 为 +1.10%，T+20 为 +1.52%；'
  '同期全体首板基准组（BM1）T+10 中位收益为 -1.31%，即 H1 相对基准高出约 1.1 个百分点。')
A('')
A('但必须同时报告一个反向事实：**「三花 + 二次突破」不贡献 Alpha**。'
  '三花 + 突破（C 组，n=4580）T+10 超额 -0.04%；'
  '三花 + 放量突破（VR>=1.2，n=3267）T+10 超额 -0.88%、T+20 -1.04%，且 5 个分桶全负；'
  '仅「三花 + 缩量突破」（VR<1.2，n=1313）在 T+10 有 +0.97%，但其 Walk-Forward 一致率仅 33%、'
  'OOS 无有效分桶，不能视为稳健。')
A('')
A('**统计有效性**')
A('')
A('- H1 全样本 T+10：配对超额 +1.10%，t = 5.7，n = 1738，正桶占比 90%（T+20 为 100%）')
A('- H4（均线聚合，n=9971）：+0.39%，t = 2.8 —— 样本最大但强度明显弱于 H1')
A('- H2/H3：+0.41% / +0.30%，t 值 1.2 / 1.1，未达显著')
A('- H5/H6：样本仅 220 / 176，T+20 超额 +2.92% / +3.55% 但 OOS 无数据，不作为结论依据')
A('')
A('**样本外（2026-01-01 ~ 2026-09-24）**')
A('')
A('- H1：T+10 超额 +1.72%%、T+20 +1.44%%，样本 n=%d（有效分桶 9/9）' % h1_oos_n)
A('- H4：T+10 +1.20%、T+20 +0.19%')
A('- 全部三花 + 突破：T+10 -0.24%、T+20 +0.64%')
A('')
A('**稳健性**')
A('')
A('- 逐年（H1，T+10）：2021 +1.43% ｜ 2022 +2.46% ｜ 2023 +1.18% ｜ 2024 +0.47% ｜ 2025 +0.53% ｜ 2026 +1.72% —— **6/6 全正**')
A('- Walk-Forward（3 年训练 → 次年测试）：H1 一致率 **100%**（2024/2025/2026 三次全部 OK）；H4 33%；H5/H6 0%')
A('- 参数扰动：price_tolerance 0~3%、volume_tolerance 0~20%、观测窗口 10~15 日均保持正超额（详见第 9 节）')
A('')
A('**结论定性与纪律声明**')
A('')
A('本研究的结论是「三花结构本身有效、二次突破无效、优势集中在特定条件」，'
  '因此按任务书第 32/40 节判定为 **CONDITIONAL**，产出 `%s`，而非 `ThreeFlower_Breakout_V1`。' % NAME)
A('')
A('必须指出：H1 的绝对收益中位数仅为 T+10 +0.06%、T+20 -0.71%，胜率 50%，失败率 49.9%。'
  '其「有效」是相对首板基准的相对优势，而非可直接盈利的绝对收益。若作为独立择时策略，绝对吸引力有限；'
  '更合理的定位是「相对排序 / 优选因子」。')
A('')
A(SEP)
A('')

# ── 2
A('## 2. 样本规模')
A('')
A(trow(['项目', '数量', '占比']))
A(trow(['---', '---', '---']))
A(trow(['首板事件（T0 涨停且 T-20~T-1 无涨停）', META['n_first_board'], '100%']))
A(trow(['核心三花结构（CORE，结构内回撤<=25%）', META['n_core_tf'], '39.7%']))
A(trow(['三花 + 突破', 4580, '13.7%']))
A(trow(['H1 定义命中（最终候选族）', 1740, '5.2%']))
A(trow(['H1 有效样本（确认日可解析）', 1738, '5.2%']))
A('')
A('**数据区间说明（重要）**：任务书默认 2018-01-01 起，但项目缓存 `daily_cache` 实际仅覆盖 '
  '2021-01-04 ~ 2026-09-24；`daily_basic_cache`（市值/换手）仅覆盖 2023-01-03 起。'
  '按任务书第 3 节「历史数据不足可自动调整但必须声明」，本研究区间调整为：')
A('')
A('- TRAIN：2021-01-04 ~ 2023-12-31')
A('- VALIDATION：2024-01-01 ~ 2025-12-31')
A('- OUT-OF-SAMPLE：2026-01-01 ~ 2026-09-24')
A('')
A('**幸存者偏差处理**：股票池取自缓存全量 5781 只代码，未按当前在市清单筛选。'
  '其中 223 只在 2021-2023 期间出现过但截至 2026-09-24 已不在市场，这些代码仍保留在历史样本中。'
  '排除了 ST/*ST 与上市交易不足 60 日的股票，未剔除后来退市的股票。')
A('')
A(SEP)
A('')

# ── 3
A('## 3. 三花定义比较')
A('')
A('口径：配对超额（时点中性）——按买入 offset 分桶，组内与同 offset 参照组中位数相减后按桶样本量加权。')
A('')
A(trow(['定义', '含义', '样本(T+10)', 'T+3', 'T+5', 'T+10', 'T+20', 'OOS(T+10)', 'WF一致率', '稳健性']))
A(trow(['---'] * 10))
_defnote = {'H1': '价格不降+量递减', 'H2': '三次缩量调整', 'H3': '价格重心抬升', 'H4': '均线聚合',
            'H5': '量价+均线复合', 'H6': '完整三花结构'}
for tag in ['H1', 'H2', 'H3', 'H4', 'H5', 'H6']:
    yy = Y[(Y['def'] == tag) & (Y['year'] == 2026)]
    oos = float(yy['adv10'].iloc[0]) if len(yy) else None
    ww = W[W['def'] == tag]
    wfr = float(ww['ok'].mean()) if len(ww) else None
    rob = '稳定' if tag == 'H1' else ('样本不足' if tag in ('H5', 'H6') else '一般')
    A(trow([tag, _defnote[tag], int(gm(D, tag, 10, 'n')), pct(gv(D, tag, 3)), pct(gv(D, tag, 5)),
            pct(gv(D, tag, 10)), pct(gv(D, tag, 20)), pct(oos), ('%d%%' % round(100 * wfr)) if wfr is not None else 'n/a', rob]))
A('')
A('**结论**：H1 是唯一「大样本 + 高显著性 + 跨年稳健 + OOS 有效」的定义族。'
  'H4（均线聚合）样本最大但超额仅约 H1 的 1/3；'
  'H2/H3 不显著；H5/H6 虽超额最高但样本不足 250 且无 OOS，不可采信。'
  '特别地，H5（H1+价格重心+MA 收敛）与 H6（H5+时间跨度）的全期 T+10 超额（+0.61% / +0.53%）'
  '反而低于 H1（+1.10%），说明附加的均线收敛与结构约束**没有增量贡献**。')
A('')
A(SEP)
A('')

# ── 4
A('## 4. 买点比较')
A('')
for N in (5, 10, 20):
    A('**T+%d**' % N)
    A('')
    A(trow(['Entry', '样本', '中位', '胜率', 'PF', '配对超额']))
    A(trow(['---'] * 6))
    for tag in ['A_确认日收盘', 'B_确认日次开', 'C_突破收盘', 'C_突破0.5%', 'C_突破1%', 'C_突破2%',
                'D_突破放量1.2', 'E2_突破+MA20上']:
        A(trow([tag, int(gm(E, tag, N, 'n')), pct(gm(E, tag, N, 'med')), '%.0f%%' % (100 * gm(E, tag, N, 'win')),
                num(gm(E, tag, N, 'pf')), pct(gv(E, tag, N))]))
    A('')
A('**结论**：三花确认日收盘（Entry A）与次日开盘（Entry B）超额等价（T+10 均 +0.58%），是唯一正超额的两类买点。'
  '所有突破类买点（Entry C / D / E）全期超额均为负，且突破缓冲越大越差（0% → -0.14%，2% → -0.61%）。'
  '因此「三花突破」并不比「三花形成直接买入」更有效——恰恰相反。')
A('')
A(SEP)
A('')

# ── 5
A('## 5. 市场环境（Regime）')
A('')
rg = L[L['layer'] == 'regime']
A(trow(['Regime', '首板样本', '三花形成率', '三花 T+5', '三花 T+10', '三花 T+20', 'H1 T+10', '突破 T+10']))
A(trow(['---'] * 8))
for _, r in rg.iterrows():
    A(trow([r['level'], int(r['n_firstboard']), '%.0f%%' % (100 * r['tf_rate']), pct(r['tf_adv_5']),
            pct(r['tf_adv_10']), pct(r['tf_adv_20']), pct(r['h1_adv_10']), pct(r['brk_adv_10'])]))
A('')
A('**结论**：三花结构的超额在 **bear（H1 +3.14%，n=184）与 weak（+1.22%）** 环境最强，'
  'strong 环境 +1.10%，neutral 最弱（+0.61%）。'
  '这与直觉相反——弱势/熊市中的首板三花反而更有效，可能反映「弱市中能走出完整缩量三花结构的标的更稀缺、筹码更干净」。'
  '但 bear 样本仅 184 且 OOS 为负（-1.38%），需谨慎。突破在各 Regime 下均无正超额（strong 下 -2.16% 最差）。')
A('')
A(SEP)
A('')

# ── 6
A('## 6. 首板质量')
A('')
q = L[L['layer'] == 't0_quality']
A(trow(['首板质量', '首板样本', '三花成功率', '三花 T+5', '三花 T+20', 'H1 T+10', 'H1 失败率']))
A(trow(['---'] * 7))
_failmap = {'Weak': 0.528, 'Normal': 0.503, 'Strong': 0.442}
for _, r in q.iterrows():
    A(trow([r['level'], int(r['n_firstboard']), '%.0f%%' % (100 * r['tf_rate']), pct(r['tf_adv_5']),
            pct(r['tf_adv_20']), pct(r['h1_adv_10']), '%.1f%%' % (100 * _failmap.get(r['level'], np.nan))]))
A('')
A('**结论**：首板封板质量对三花成功率影响方向明确但幅度温和——'
  'Strong 首板的三花 T+10 超额 +2.04%、失败率 44.2%；'
  'Weak 首板 +0.65%、失败率 52.8%。'
  '注意三花「形成率」反而与质量负相关（Strong 36% < Weak 42%），'
  '即强封板首板更容易直接连板拉升而较少形成横盘三花结构。')
A('')
A('**其他分层（T+10 配对超额）**')
A('')
for lay, nm in [('board', '板块'), ('mv_grp', '市值'), ('to_grp', '换手')]:
    sub = L[L['layer'] == lay]
    s = ' ｜ '.join('%s %s(n=%d)' % (r['level'], pct(r['h1_adv_10']), int(r['n_firstboard'])) for _, r in sub.iterrows())
    A('- **%s**：%s' % (nm, s))
A('')
A('注：市值/换手分层依赖 `daily_basic_cache`，该表仅自 2023-01-03 起可用，故 mv_grp / to_grp 分层实际只覆盖 2023 年及以后样本。')
A('')
A(SEP)
A('')

# ── 7
A('## 7. 量能分析')
A('')
A('**首板量比（首板成交量 / MA20 成交量）分层**（源：`_analyze_tf.py` 首板量比分层输出）')
A('')
A(trow(['首板 VR', '三花 n', '三花 T+5', '三花 T+20', '突破 n', '突破 T+5', '突破 T+20']))
A(trow(['---'] * 7))
A(trow(['<1.0', 820, '+0.74%', '-0.07%', 322, '-0.14%', '-0.59%']))
A(trow(['1.0-1.5', 1849, '+0.28%', '+0.89%', 731, '+0.06%', '-0.55%']))
A(trow(['1.5-2.0', 2349, '-0.04%', '-0.04%', 857, '-0.37%', '-0.87%']))
A(trow(['2.0-3.0', 3849, '+0.12%', '+0.13%', 1350, '-0.74%', '-0.54%']))
A(trow(['3.0-5.0', 3372, '+0.29%', '+0.13%', 1042, '-0.88%', '-1.19%']))
A(trow(['>5.0', 1030, '+0.23%', '+0.38%', 278, '-0.53%', '-1.22%']))
A('')
A('**突破量能（突破日 VR = 当日量 / MA20）与未来收益** —— 本研究最稳定的单调关系之一')
A('')
A(trow(['突破 VR 分档', '样本', 'T+10 失败率', '3 日内跌破 -5%', 'T+10 中位', 'T+20 中位']))
A(trow(['---'] * 6))
for _, r in FB.iterrows():
    A(trow([r['band'], int(r['n']), '%.1f%%' % (100 * r['fail_10']), '%.1f%%' % (100 * r['fast_break']),
            pct(r['med_10']), pct(r['med_20'])]))
A('')
A('**结论**：存在明确的「温和缩量突破优于天量突破」关系，且这是单调的：')
A('')
A('- 突破 VR < 1.0：T+10 超额 +1.35%、失败率 50.0%、3 日内破 -5% 仅 11.5%')
A('- 突破 VR 1.5-2.0：T+10 超额 -1.63%、失败率 62.7%、3 日内破 -5% 达 27.5%')
A('- 突破 VR > 2.0：T+10 超额 -2.02%、失败率 62.1%、MAE -10.49%')
A('')
A('即：**放量突破是短线兑现信号而非启动信号**。'
  '但需注意，即便是最优的缩量突破档（VR<1.0，n=219），其绝对 T+10 中位也仅 -0.03%，'
  '且该档在 2024 年、OOS 均无有效样本 —— 因此不能把「缩量突破」升级为独立买点，'
  '只能作为「回避放量突破」的排除条件。')
A('')
A(SEP)
A('')

# ── 8
A('## 8. 失败案例')
A('')
A('口径：A 组 = H1 + 确认日收盘买入，失败 = T+10 净收益 <= 0，基线失败率 49.9%（n=1728）。')
A('')
A('**A 组主要失败模式**')
A('')
A(trow(['失败模式', '样本', '占比', 'T+10 失败率', 'Lift', 'T+10 中位', 'T+20 中位']))
A(trow(['---'] * 7))
for _, r in FP[FP['group'].astype(str).str.startswith('A')].head(8).iterrows():
    A(trow([r['pattern'], int(r['n']), pct0(r['share']), pct1(r['fail_10']),
            num(r['lift']), pct(r['med_10']), pct(r['med_20'])]))
A('')
A('**C 组（三花 + 突破收盘）失败模式** —— 基线失败率 56.3%（n=872），突破后 3 日内跌破 -5% 比例 18.1%')
A('')
A(trow(['失败模式', '样本', '占比', 'T+10 失败率', 'Lift', 'T+10 中位', 'T+20 中位']))
A(trow(['---'] * 7))
for _, r in FP[FP['group'].astype(str).str.startswith('C')].head(8).iterrows():
    A(trow([r['pattern'], int(r['n']), pct0(r['share']), pct1(r['fail_10']),
            num(r['lift']), pct(r['med_10']), pct(r['med_20'])]))
A('')
A('**什么样的三花最容易失败（Failure Pattern 归纳）**')
A('')
A('1. **Failure Pattern 1 — 高换手 + 确认过早**：确认日换手率处于前 1/3 分位且确认日 <= T+8。'
  'n=335（占比 19%），T+10 失败率 59.4%（lift 1.19），T+10 中位 -2.10%、T+20 -3.51%。'
  '机制：筹码尚未沉淀就匆忙成形，结构是「换手换出来的」而非「缩量磨出来的」。')
A('2. **Failure Pattern 2 — 高换手（整体）**：确认日换手前 1/3 分位。n=865（占比 50%），'
  '失败率 55.3%（lift 1.11），T+20 中位 -1.98%。这是覆盖样本最广的失败因子，'
  '也解释了为何低换手组（+3.60pp）显著优于高换手组（+0.12pp）。')
A('3. **Failure Pattern 3 — 突破天量 + 高换手（C 组）**：突破 VR >= 1.5 且换手前 1/3 分位。'
  'n=149（占比 17%），T+10 失败率 64.4%（lift 1.14），T+20 中位 -4.48%，'
  '突破后 3 日内跌破 -5% 比例 27%。')
A('')
A('**成功条件（对照组）**：bear regime 失败率仅 37.5%（T+10 中位 +2.14%）；'
  '低换手 42.5%（+1.86%）；首板质量 Strong 44.2%（+0.97%）；花数 >=6 时失败率 31.2%。')
A('')
A(SEP)
A('')

# ── 9
A('## 9. 稳健参数区间')
A('')
A('按任务书第 24 节要求，只输出**稳健区间**，不输出历史最优参数。')
A('')
A(trow(['参数', 'H1 稳健区间', '区间内表现', '结论']))
A(trow(['---'] * 4))
A(trow(['price_tolerance（价格容差）', '0% ~ 3%', 'T+10 超额 +0.77% ~ +1.15%', '完全不敏感，取中值 1%']))
A(trow(['volume_tolerance（量能容差）', '0% ~ 20%', 'T+10 超额 +1.04% ~ +1.18%', '完全不敏感，取中值 10%']))
A(trow(['观测窗口 W_OBS', '10 ~ 15 交易日', 'T+10 超额 +0.89%(n709) / +1.10%(n1739)', '越长样本越多且更好，取 15']))
A(trow(['花数下限 n_flower', '>=3 ~ >=4', '+1.10%(n1739) / +1.25%(n1034)', '>=4 略优但样本减半，两档均稳']))
A(trow(['结构回撤上限 ddmax', '8% ~ 15%', '+0.76% ~ +1.27%，非单调', '无稳健增益，维持 CORE_DD=25%(+1.10%)']))
A(trow(['MA 收敛阈值 ma_conv', '2% ~ 8%', 'H1 恒定 +1.10%（H1 不使用该条件）', '对 H1 无影响；H4/H5/H6 中亦无单调性']))
A(trow(['突破 buffer', '0% ~ 2%', '全部为负：-0.14% ~ -0.61%', '单调恶化，建议不使用突破买点']))
A(trow(['突破 VR', '<1.0 ~ >2.0', '+1.35% ~ -2.02%，单调恶化', '单调恶化，只能作排除条件']))
A('')
A('**稳健性检验汇总**（`tf_walkforward.csv`，训练窗 3 年 → 次年测试，看超额符号一致性）')
A('')
A(trow(['策略', '一致率', '2024 测试', '2025 测试', '2026 测试']))
A(trow(['---'] * 5))
for defname in ['CORE(have_tf)', 'H1', 'H4', 'H5', 'H6', 'H1+缩量突破(VR<1.2)', 'H1+突破(全体)', '全部三花+突破']:
    ww = W[W['def'] == defname]
    if not len(ww):
        continue
    te = {str(int(r['test'])): r['adv_te'] for _, r in ww.iterrows()}
    A(trow([defname, '%d%%' % round(100 * ww['ok'].mean()), pct(te.get('2024')), pct(te.get('2025')), pct(te.get('2026'))]))
A('')
A('**逐年明细（H1，T+10 配对超额）**')
A('')
h1y = Y[Y['def'] == 'H1']
A(trow(['年份', '样本', 'T+5', 'T+10', 'T+20', 'T+10 中位', 'T+10 胜率', 'T+10 PF']))
A(trow(['---'] * 8))
for _, r in h1y.iterrows():
    A(trow([int(r['year']), int(r['n']), pct(r['adv5']), pct(r['adv10']), pct(r['adv20']),
            pct(r['med10']), '%.0f%%' % (100 * r['win10']), num(r['pf10'])]))
A('')
A(SEP)
A('')

# ── 10
A('## 10. HVT 正交验证')
A('')
A('**只读** HVT 产物，未修改 HVT 任何代码与逻辑。HVT 事件源：`report_daily/hvt_bull_backtest_events_20250101_20260828.csv`'
  '（4439 条事件，实际覆盖 2025-02-05 ~ 2026-08-28）。'
  '三花确认日落在该窗口内的 H1 事件共 552 个。')
A('')
A(trow(['对齐口径', '命中数', '命中率', '随机基准', '交集 T+10 中位', '非HVT T+10 中位', '差']))
A(trow(['---'] * 7))
_hl = {'A_同日同股': 'HVT t0_date == 三花确认日', 'B_近邻±3日': 'HVT t0_date 与确认日相隔<=3 日',
       'C_同一首板日': 'HVT t0_date == 三花首板日 T0'}
for _, r in H.iterrows():
    if r['align'] not in _hl:
        continue
    A(trow([_hl[r['align']], int(r['n_hit']), '%.1f%%' % (100 * r['hit_rate']),
            '%.2f%%' % (100 * r['random_baseline']), pct(r['hit_med_10']), pct(r['rest_med_10']), pct(r['diff_10'])]))
A('')
A('**结论：三花与 HVT 在时序上正交，不是同一 Alpha。**')
A('')
A('- **同日重合率 0.0%**（0/552）——HVT 的触发日与三花确认日从不重合。'
  '原因是两者锚点不同：HVT 的 t0_date 多为涨停/突破当日，而三花确认日中位落在首板后 T+11。')
A('- 近邻 ±3 日重合仅 0.7%（4/552），且这 4 例收益畸高（T+20 中位 +23.78%），'
  '样本量过小不构成证据。')
A('- 同一首板日重合 10.1%（56/552，随机基准 0.23%）—— 说明两者在「首板/涨停」这一层确实共享信息源，'
  '但重合样本相对三花-非HVT 组并无额外收益（T+10 差 +0.01%，T+20 差 -2.73%）。')
A('- 日快照口径（`hvt_bull_*.json` 的 Top20 事件 + 执行池）在三花确认日命中 0 只，'
  '因快照仅含每日前 20 名，命中概率本身极低，该口径不构成否证。')
A('')
A('**综合判断**：三花提供的是 HVT 未覆盖的**不同时点**上的信息，'
  '可作为独立因子存在；但两者共享首板事件源，若接入同一组合需做相关性约束。')
A('')
A('注：HVT 事件数据仅覆盖 2025-02 起，正交验证仅在重叠窗口（552 个事件）内有效，'
  '不能外推至 2021-2024 区间。')
A('')
A(SEP)
A('')

# ── 11
A('## 11. 最终策略')
A('')
A('研究通过（条件有效），输出 `%s`。' % NAME)
A('')
A('```json')
A(json.dumps({'strategy_name': NAME, 'status': STATUS,
              'event': {'type': 'FIRST_LIMIT_UP', 'lookback_no_limit_up': 20},
              'structure': {'observation_window': 'T+0 ~ T+15',
                            'flower_definition': '摆动低点 R=1，最少 3 花，确认日最早三元组',
                            'family': 'H1 价格重心不下降 + 成交量递减',
                            'volume_decay': 'V2<=V1*(1+10%) 且 V3<=V2*(1+10%)',
                            'price_progression': 'P2>=P1*(1-1%) 且 P3>=P2*(1-1%)',
                            'max_drawdown': 'CORE_DD<=25%',
                            'ma_convergence': '不适用（H1 不含均线条件）'},
              'entry': {'type': 'CONFIRM_CLOSE',
                        'note': '确认日收盘或次日开盘买入；不使用突破买点'},
              'risk': {'structure_failure': '结构内跌破首板收盘 -6% 以上',
                       'max_hold': 'T+20',
                       'failure_base_rate': 0.499},
              'expected_horizon': ['T+3', 'T+5', 'T+10', 'T+20'],
              'validation': {'train': '2021-2023', 'validation': '2024-2025',
                             'oos': '2026 (adv10 +1.72%)', 'walk_forward_ok_rate': 1.0},
              'failure_conditions': STRATEGY['failure_conditions']},
             ensure_ascii=False, indent=2))
A('```')
A('')
A('完整字段见 `three_flower_strategy_v1.json`。')
A('')
A('**接入声明**：本阶段仅产出科研结论与因子定义，**未接入实盘**，'
  '**未修改** HVT / DLG / F120 / 主题量化 / 突破策略 / 实盘执行 / te_buy_pool 任何模块。'
  '是否接入由用户决定。')
A('')
A(SEP)
A('')
A('## 附：产物清单')
A('')
A('- `three_flower_research_%s.md` — 本报告' % DATE)
A('- `three_flower_research_%s.json` — 结构化研究结论' % DATE)
A('- `three_flower_research_%s.csv` — 结论性汇总表' % DATE)
A('- `three_flower_events_%s.csv` — 全量首板事件明细（含三花结构特征与前瞻收益）' % DATE)
A('- `three_flower_experiments_%s.csv` — 参数搜索实验记录（%d 组）' % (DATE, len(X)))
A('- `three_flower_failures_%s.csv` — 失败案例分箱与失败模式' % DATE)
A('- `three_flower_strategy_v1.json` — 最终策略定义（无日期后缀）')
A('')
A('实验脚本（可复现）：`research/_build_panel.py` → `_build_first_board.py` → `_build_three_flower.py`'
  ' → `_analyze_tf.py` → `_search_tf.py` → `_walkforward_tf.py` → `_fail_tf.py` → `_hvt_orth_tf.py` → `_report_tf.py`')

md = '\n'.join(L_)
open(os.path.join(RES, 'three_flower_research_%s.md' % DATE), 'w', encoding='utf-8').write(md)

print('已生成 report_daily/research/ 下全部产物')
for f in sorted(os.listdir(RES)):
    print('  %-46s %8.1f KB' % (f, os.path.getsize(os.path.join(RES, f)) / 1024))
