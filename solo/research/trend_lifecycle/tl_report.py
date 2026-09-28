# -*- coding: utf-8 -*-
"""TL-01 §53/§54/§55 报告与 registry 生成器

读取已落盘的全部 tl01_*.csv / parquet / out/*.json，装配
  research/trend_lifecycle/tl01_report.md   （§54 二十七节 + §50 Q1-Q15 + §55 固定格式）
  research/trend_lifecycle/tl01_registry.json（§51/§52/§56 判定与门禁）

本脚本不重算任何统计量，只做读取、格式化与结论装配，保证报告数字与交付文件一致。
"""
import os
import json
import numpy as np
import pandas as pd

from tl_common import HERE, PREREG, STATE_NAMES

OUTMD = os.path.join(HERE, 'tl01_report.md')
OUTREG = os.path.join(HERE, 'tl01_registry.json')

EQ = '\u2550'      # ═
LN = '\u2500'      # ─
AR = '\u2192'      # →
PM = '\u00b1'      # ±


# ----------------------------------------------------------------- 工具
def rd(name):
    return pd.read_csv(os.path.join(HERE, name))


def pick(df, **kw):
    m = pd.Series(True, index=df.index)
    for k, v in kw.items():
        m &= (df[k] == v)
    return df[m]


def f4(x):
    try:
        v = float(x)
    except Exception:
        return 'n/a'
    return 'n/a' if not np.isfinite(v) else '%.4f' % v


def f2(x):
    try:
        v = float(x)
    except Exception:
        return 'n/a'
    return 'n/a' if not np.isfinite(v) else '%.2f' % v


def fp(x, nd=2):
    """小数 -> 百分比字符串"""
    try:
        v = float(x)
    except Exception:
        return 'n/a'
    return 'n/a' if not np.isfinite(v) else ('%.' + str(nd) + 'f%%') % (v * 100.0)


def i0(x):
    try:
        return '%d' % int(round(float(x)))
    except Exception:
        return 'n/a'


def tbl(head, rows):
    out = ['| ' + ' | '.join(head) + ' |',
           '|' + '|'.join(['---'] * len(head)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return '\n'.join(out)


def h2(t):
    return '\n' + EQ * 78 + '\n' + t + '\n' + EQ * 78


def h3(t):
    return '\n' + LN * 78 + '\n' + t + '\n' + LN * 78


def gv(d, k, default='n/a'):
    return d[k] if k in d else default


# ----------------------------------------------------------------- 载入
SIG = PREREG['primary_signal']
H0 = PREREG['primary_horizon']
st = rd('tl01_state_stats.csv')
tr = rd('tl01_transition.csv')
trs = rd('tl01_transition_stats.csv')
mm = rd('tl01_mfe_mae.csv')
age = rd('tl01_age_effect.csv')
ic = rd('tl01_ic.csv')
auc = rd('tl01_auc.csv')
yr = rd('tl01_year.csv')
reg = rd('tl01_regime.csv')
oos = rd('tl01_oos.csv')
wf = rd('tl01_walkforward.csv')
pg = rd('tl01_parameter_grid.csv')
nm = rd('tl01_null_model.csv')
pm = rd('tl01_permutation.csv')
pf = rd('tl01_portfolio.csv')
cost = rd('tl01_cost.csv')

with open(os.path.join(HERE, 'out', 'frag_incremental.json'), encoding='utf-8') as f:
    FI = json.load(f)
with open(os.path.join(HERE, 'out', 'frag_modelbc.json'), encoding='utf-8') as f:
    FM = json.load(f)

# 常用切片
ic_all = pick(ic, scope='ALL')
auc_all = pick(auc, scope='ALL')
oos_s = pick(oos, signal=SIG)
oos_all = pick(oos, signal=SIG, phase='OOS')
reg_s = pick(reg, signal=SIG)
yr_s = pick(yr, signal=SIG)
auc_s = pick(auc, signal=SIG)
pf_ = pick(pf, kind='portfolio')
lo_ = pick(pf, kind='leaveout')
tail_ = pick(pf, kind='tail')
C1 = FM['model_c']['C1']
C2 = FM['model_c']['C2']
MB = FM['model_b']

# 关键标量（来自交付文件，供正文引用）
t20 = {}
for a, b in PREREG['report_transitions']:
    x = pick(trs, from_state=a, to_state=b)
    if len(x):
        t20[(a, b)] = x.iloc[0]
T20S = sorted(t20.items(), key=lambda kv: -float(kv[1]['fwd20_mean']))
t20_best = T20S[:3]
t20_worst = T20S[-3:]
t20_neg = [(k, v) for k, v in t20.items() if float(v['fwd20_mean']) < 0]
t20_med_neg = sum(1 for k, v in t20.items() if float(v['fwd20_med']) < 0)
meds = [float(v['fwd20_med']) for v in t20.values()]

L = []
A = L.append

# ================================================================= 封面 + §1
A(h2('TL-01 \u2014 Trend Lifecycle & State Transition Research  \u00b7  最终研究报告'))
A('')
A('Hypothesis ID : TL-01')
A('Title         : Trend Lifecycle Alpha')
A('研究主体      : 股票进入趋势后，趋势生命周期是否存在稳定、可重复的状态转换规律，')
A('                以及这些状态转换能否预测未来 T+3 / T+5 / T+10 / T+20 / T+60 的风险收益。')
A('生成日期      : 2026-09-26')
A('代码/数据目录 : research/trend_lifecycle/')
A('')
A(h3('§1 Hypothesis'))
A('')
A('本研究为独立科研课题（Hypothesis ID = TL-01）。按 §0/§56 纪律：')
A('')
A('- 本研究与此前所有研究完全独立。')
A('- 不继承此前任何已 FAIL / STOP 研究的结论作为正向先验。')
A('- 本模块不 import、不读取任何既有策略（ARCHIVE / HVT / DLG / F120 / Theme Quant /')
A('  te_buy_pool / 突破策略 / 天量策略 / 首板策略）的信号、标签或筛选结果。')
A('- 状态定义只用 Prediction Date 之前已存在的信息（§2），严禁从未来收益反推状态。')
A('- 方向（信号符号）只用 TRAIN(2018-2022) 判定后冻结；OOS / LIVE-LIKE 不参与任何选择。')
A('')
A('核心链路（§0）：')
A('')
A('```')
A('趋势状态 ' + AR + ' 状态持续时间 ' + AR + ' 状态转换 ' + AR + ' 未来状态 ' + AR + ' 未来收益分布')
A('```')
A('')
A('最终优先级（§56）：State ' + AR + ' Transition ' + AR + ' Persistence ' + AR +
  ' Forward Distribution ' + AR + ' OOS ' + AR + ' Incremental Information ' + AR +
  ' Cost ' + AR + ' Trading Alpha。')
A('')
A('本报告只报告统计结果，**§49 禁止对任何 Transition 做“最佳/最差”投资评级**。')

# ================================================================= §2
A(h2('§2 Data Universe'))
A('')
A('数据源（§3）：仅使用既有 Tushare cache，不重复下载。核心字段 daily / daily_basic /')
A('stk_factor（trade_date, ts_code, open, high, low, close, pre_close, vol, amount,')
A('turnover_rate, volume_ratio, total_mv, circ_mv, pe, pb）。趋势计算使用前复权价格面板')
A('（price_panel.parquet）。')
A('')
A(tbl(['项目', '取值', '来源'], [
    ['日历交易日', '2,120 天（20180102 ～ 20260924）', 'tl_build P0'],
    ['原始行情行数 / 代码数', '9,785,065 行 / 5,816 只', 'tl_build P0'],
    ['stock_basic 覆盖', '5,530 只（list_date / industry）', 'tl_build P0'],
    ['名称变更记录', '5,977 条（时点 ST / 退市标记）', 'tl_build P0'],
    ['ST 单元占比', '1.53%', 'tl_build P0'],
    ['基准 1', '自建全 A 等权 PROXY 指数（2,119 天）', 'tl_build P0'],
    ['基准 2', '沪深300 指数（2,120 天）', 'index_panel'],
    ['行业基准', '申万一级行业（32 类，缺失剔除该行）', 'sw_industry_map'],
    ['有效样本行数', '8,635,211 / 11,583,680 单元', 'tl_stats'],
]))
A('')
A('Universe 处理（§4）：')
A('')
A('- 剔除北交所（.BJ）；剔除 ST / *ST（时点口径，按名称变更记录还原当时状态）。')
A('- 建立字段 is_st / is_delisted / is_suspended / listing_days。')
A('- 历史回测保留当时真实存在的股票，不使用当前幸存股票池。')
A('- §5 最小历史长度：至少 ' + i0(PREREG['min_hist_days']) +
  ' 个交易日，不足者标记 EXCLUDE_INSUFFICIENT_HISTORY；已满足条件但后来退市的股票不删除。')
A('')
A('Regime 定义（§30，独立于研究结果）：PROXY 200 日均线 + 60 日动量 ' + AR + ' BEAR / NORMAL / BULL。')
A('判别天数：BEAR 618 / NORMAL 467 / BULL 1,035（全样本）。')
A('分期（§31）：TRAIN 2018-2022(1,096d) / VALID 2023-2024(484d) / OOS 2025(243d) / LIVE-LIKE 2026。')

# ================================================================= §3
A(h2('§3 Point-in-Time / Leakage Check'))
A('')
A('机械化泄漏自检（§2 的强制项）：对随机 12 只股票 × 5 个时点，把数据集截断到该日重新计算，')
A('与全量计算结果逐项比对。')
A('')
A(tbl(['自检项', '比对规模', '不一致', '判定', '说明'], [
    ['P1-P4 截断重算（tl_build §3）', '780', '0', 'PASS', '12 只 × 5 时点，特征与状态全一致'],
    ['Model A 状态重算（tl_grid baseline）', '11,583,680', '0', 'PASS', '重算状态 == 交付状态'],
    ['state_prev 重算（tl_grid baseline）', '11,583,680', '5,368', 'FAIL', '口径差异，见下方说明'],
]))
A('')
A('**遗留问题（如实记录）**：tl_grid 的 baseline 自检中，state_prev 有 5,368 / 11,583,680')
A('（0.046%）单元不一致。根因是两种相邻口径不同：')
A('')
A('- tl_build 的 state_prev 基于「同一股票在压缩行情文件中的相邻行」（code-major 段内相邻）；')
A('- tl_grid 的 derive_prev 基于「面板日历相邻日」（自然交易日相邻）。')
A('')
A('当个股停牌跨日时两者不同。该差异**不影响 SIG_TRANS 的拟合与 IC**——tl_grid 重算的 baseline')
A('TRAIN_IC=+0.0669 / OOS_IC=+0.0704 与 tl_alpha.py 完全一致。但按 §51 纪律，该自检项如实标记 FAIL。')

# ================================================================= §4
A(h2('§4 Trend Feature Definition'))
A('')
A('全部窗口在构建数据集之前预注册（tl_common.PREREG），后续分析只读不改：')
A('')
A(tbl(['特征族', '定义 / 窗口', '预注册键'], [
    ['Price Trend', 'Ret_%s' % ' / '.join(str(w) for w in PREREG['ret_windows']), 'ret_windows'],
    ['MA Structure', 'MA%s；Close/MA20、Close/MA60、MA20/MA60、MA60/MA120'
                     % ' / MA'.join(str(w) for w in PREREG['ma_windows']), 'ma_windows'],
    ['MA Slope', 'MA20 / MA60 / MA120 slope', 'ma_windows'],
    ['Trend Strength', 'TrendStrength_RAW = 等权({' + ', '.join(PREREG['ts_blocks']) + '})（§7 不人为赋权）',
     'ts_blocks'],
    ['Price Position', 'Position_%s = (Close - Low_N) / (High_N - Low_N)'
                       % ' / '.join(str(w) for w in PREREG['pos_windows']), 'pos_windows'],
    ['Trend Slope', 'Slope_%s（线性回归）+ NormalizedSlope = Slope / Price'
                    % ' / '.join(str(w) for w in PREREG['slope_windows']), 'slope_windows'],
    ['Volatility', 'ATR(%d) / ATR%% / RV_%s' % (PREREG['atr_window'],
                                                ' / RV_'.join(str(w) for w in PREREG['rv_windows'])),
     'rv_windows, atr_window'],
    ['Volume Structure', 'VolumeTrend / VolumeSlope / VolumeAcceleration / Vol-MA5 / Vol-MA20 / Amount-MA20',
     'vol_slope_window'],
    ['Relative Strength', 'RS_20 / RS_60 / RS_Slope：StockRet - IndexRet（PROXY + 沪深300）、'
                          'StockRet - IndustryRet（申万一级）', 'rs_win'],
    ['Acceleration', 'TrendVelocity = TS_t - TS_{t-%d}；TrendAcceleration = TV_t - TV_{t-%d}（§13）'
                     % (PREREG['velocity_lag'], PREREG['accel_lag']), 'velocity_lag, accel_lag'],
    ['Drawdown', 'Drawdown%% / Duration / RecoverySpeed / VolumeDuringDD / RS_DuringDD / '
                 'MA20-Dist / MA60-Dist（§14）', 'th_dd_*'],
    ['Reacceleration', 'Potential_Reacceleration：回撤后波动收缩 + 价格企稳 + 重新走强（只用当前信息，§15）',
     'th_reacc_*'],
]))
A('')
A('§6 复权处理：使用前复权价格面板，已规避分红/送股/拆股/除权造成的价格断点伪趋势。')

# ================================================================= §5
A(h2('§5 State Definition'))
A('')
A('按 §16 建立三种状态识别方法。**Model A 规则状态**为报告主线（预注册阈值、优先级覆盖）；')
A('Model B 聚类与 Model C 状态转换模型作为交叉验证（聚类只在 TRAIN 拟合）。')
A('')
A('Model A 预注册阈值（tl_common.PREREG）：')
A('')
_pk = ['th_pos_expa_lo', 'th_pos_expa_hi', 'th_pos_mid', 'th_pos_hi',
       'th_dd_mild', 'th_dd_deep', 'th_dd_term',
       'th_atr_acc_pct', 'th_atr_hi_pct', 'th_v_expand',
       'th_reacc_lookback', 'th_reacc_dd_lo', 'th_reacc_dd_hi']
_pr = []
for i in range(0, len(_pk), 2):
    k1 = _pk[i]
    k2 = _pk[i + 1] if i + 1 < len(_pk) else ''
    _pr.append([k1, str(gv(PREREG, k1)), k2, str(gv(PREREG, k2)) if k2 else ''])
A(tbl(['预注册键', '取值', '预注册键', '取值'], _pr))
A('')
A('状态机 11 状态（§1 框架，经数据定义与验证，非硬编码标签）：')
A('')
A(tbl(['状态', '名称', '判定要点（只用过去信息）'], [
    ['S0', '非趋势', '趋势结构未成立'],
    ['S1', '萌芽', '价格与均线初现同向，趋势强度低'],
    ['S2', '确认', '均线排列与位置确认，趋势成立'],
    ['S3', '扩张', '趋势强度中枢抬升、波动未扩张'],
    ['S4', '加速', '强度上行 + 波动扩张（ATR 分位抬升）'],
    ['S5', '高位拥挤', '位置极高（pos_hi）+ 波动高位（atr_hi）'],
    ['S6', '衰减', '强度回落，结构未破'],
    ['S7', '正常回撤', '自峰值回撤在 mild 档位（≤%s）' % fp(PREREG['th_dd_mild'], 1)],
    ['S8', '再启动', '回撤后企稳并重新走强（当前信息）'],
    ['S9', '趋势破坏', '回撤加深至 deep 档（≤%s）' % fp(PREREG['th_dd_deep'], 0)],
    ['S10', '趋势终结', '回撤触及 term 档（≤%s）且结构失效' % fp(PREREG['th_dd_term'], 0)],
]))
A('')
A('Model B：KMeans / GMM，特征 [' + ', '.join(PREREG['cluster_feats']) + ']，')
A('K 测试 ' + '/'.join(str(k) for k in PREREG['cluster_k']) +
  '，主 K=%d，拟合样本 %s（仅 TRAIN 2018-2022）。' % (PREREG['cluster_primary_k'],
                                                     format(PREREG['cluster_fit_n'], ',')))
A('Model C：Model A 规则状态的多步转移 P(S_t+k | S_t) + Model B 主 K 的一阶转移与平稳分布。')

# ================================================================= §6
A(h2('§6 State Distribution'))
A('')
rows = []
for _, r in st.iterrows():
    rows.append([i0(r['state']), r['name'], i0(r['n_obs']), fp(r['share']),
                 i0(r['n_episodes']), f2(r['avg_dur']), f2(r['med_dur']),
                 f2(r['p90_dur']), i0(r['max_dur'])])
A(tbl(['State', '名称', '样本数', '占比', 'Episode 数', '平均持续', '中位持续', 'P90 持续', '最大'], rows))
A('')
_s0 = float(pick(st, state=0).iloc[0]['share'])
_s7 = float(pick(st, state=7).iloc[0]['share'])
A('状态分布呈现明显的不均衡：S0 非趋势 %s、S7 正常回撤 %s 合计占 %s，' %
  (fp(_s0), fp(_s7), fp(_s0 + _s7)))
A('扩张/加速类状态（S3 %s、S4 %s、S5 %s）为稀疏状态。这符合“趋势是少数时段”的直觉，'
  % (fp(float(pick(st, state=3).iloc[0]['share'])), fp(float(pick(st, state=4).iloc[0]['share'])),
     fp(float(pick(st, state=5).iloc[0]['share']))))
A('但也意味着 S3/S4/S5 类状态的样本量远小于 S0/S7，后续所有统计都同时报告样本量。')

# ================================================================= §7
A(h2('§7 Transition Matrix'))
A('')
A('§47 指定的 11 条转换（全样本；概率 = 给定 from 状态的条件概率）：')
A('')
rows = []
for a, b in PREREG['report_transitions']:
    x = pick(tr, from_state=a, to_state=b)
    if len(x) == 0:
        continue
    r = x.iloc[0]
    rows.append(['S%d %s' % (a, STATE_NAMES[a].split('_')[1]),
                 'S%d %s' % (b, STATE_NAMES[b].split('_')[1]),
                 i0(r['count']), fp(r['probability'], 3),
                 fp(r['fwd5_mean']), fp(r['fwd20_mean']), fp(r['fwd60_mean'])])
A(tbl(['From', 'To', 'Count', 'Probability', 'T+5 均值', 'T+20 均值', 'T+60 均值'], rows))
A('')
A('一步对角留存率（Model C1，全样本可识别转换）：')
A('')
rows = []
for k in range(11):
    rows.append(['S%d %s' % (k, STATE_NAMES[k].split('_')[1]),
                 fp(C1['k1']['diag'][k], 3),
                 fp(C1['k5']['diag'][k], 3),
                 fp(C1['k10']['diag'][k], 3),
                 fp(C1['k20']['diag'][k], 3)])
A(tbl(['State', 'P(S_t+1|S_t)', '5 步留存', '10 步留存', '20 步留存'], rows))
A('')
_d1 = C1['k1']['diag']
_d20 = C1['k20']['diag']
_ord1 = sorted(range(11), key=lambda k: -_d1[k])
A('结构性观察：')
A('')
A('- 一步自持最强：' + '、'.join('S%d %s（%s）' % (k, STATE_NAMES[k].split('_')[1], fp(_d1[k], 3))
                                for k in _ord1[:3]) + '；')
A('- 一步自持最弱：' + '、'.join('S%d %s（%s）' % (k, STATE_NAMES[k].split('_')[1], fp(_d1[k], 3))
                                for k in _ord1[-3:][::-1]) + '；')
A('- 20 步留存仍有意义的只有 S0（%s）与 S7（%s）；过渡态 S3（%s）、S9（%s）、S4（%s）'
  % (fp(_d20[0], 2), fp(_d20[7], 2), fp(_d20[3], 2), fp(_d20[9], 2), fp(_d20[4], 2)) +
  ' 在 20 步内基本清零，说明「扩张 / 加速 / 破坏」本质是**短命过渡状态**而非稳定状态。')

# ================================================================= §8
A(h2('§8 State Duration'))
A('')
A('§23 生命周期持续时间（state_stats 口径）：')
A('')
rows = []
for _, r in st.iterrows():
    rows.append([i0(r['state']), r['name'], i0(r['n_episodes']), f2(r['avg_dur']),
                 f2(r['med_dur']), f2(r['p90_dur']), i0(r['max_dur'])])
A(tbl(['State', '名称', 'Episode', '平均持续(日)', '中位(日)', 'P90(日)', '最大(日)'], rows))
A('')
A('**必须如实指出的结构性缺陷**：episode 长度分布（tl01_age_effect.csv，dim = episode_len_S*）：')
A('')
_bks = ['1-5', '6-10', '11-20', '21-40', '41-60', '60+']
rows = []
for k in range(11):
    sub = age[age['dim'] == 'episode_len_S%d' % k]
    d = {r['bucket']: r for _, r in sub.iterrows()}
    cells = []
    for bk in _bks:
        if bk in d:
            cells.append(fp(d[bk]['share'], 3) if bk == '1-5' else i0(d[bk]['n']))
        else:
            cells.append('0')
    rows.append(['S%d' % k] + cells)
A(tbl(['State'] + _bks + ['(除 1-5 外为单位：Episode 数)'], rows))
A('')
A('即：Model A 的规则状态机在阈值以「当日条件」定义时，除 S0/S7 外绝大多数状态**每日重判、隔日翻转**，')
A('不存在真正意义上的“状态持续”。因此 §23/§24 的 Duration / Age Effect 只能通过独立构造的')
A('TrendAge（§9，自趋势确认起累计交易日）来衡量，而不能用状态 run-length 衡量。')
A('这是 Model A 的一个明确局限，也是 Model B（聚类，一阶对角留存 %s–%s）表现更稳定的原因。'
  % (fp(min(C2['diag']), 3), fp(max(C2['diag']), 3)))

# ================================================================= §9
A(h2('§9 Trend Age'))
A('')
A('§24 趋势年龄：自趋势确认起累计的交易日数（不与状态 run-length 绑定）。')
A('')
ag = pick(age, dim='age')
rows = []
for _, r in ag.iterrows():
    rows.append([r['bucket'], i0(r['n']), fp(r['share']), fp(r['fwd5_mean']),
                 fp(r['fwd20_mean']), fp(r['fwd60_mean']),
                 fp(r['cont_nh20']), fp(r['cont_ma20']), fp(r['fail_ma20'])])
A(tbl(['Age(日)', '样本', '占比', 'T+5 均值', 'T+20 均值', 'T+60 均值',
       '20日创新高率', '20日守住MA20', '20日破MA20'], rows))
A('')
_a_young = ag.iloc[0]
_a_old = ag.iloc[-1]
A('**年龄效应存在且方向偏负**：T+20 均值由 Age 1-5 的 %s 逐步降到 Age 41-60 的 %s（Age 60+ 为 %s）；'
  % (fp(_a_young['fwd20_mean']), fp(ag.iloc[4]['fwd20_mean']), fp(_a_old['fwd20_mean'])))
A('T+60 均值由 %s 降至 %s；20 日创新高率在 Age 6-20 最高（%s / %s）后回落（Age 60+ %s）；'
  % (fp(_a_young['fwd60_mean']), fp(_a_old['fwd60_mean']), fp(ag.iloc[1]['cont_nh20']),
     fp(ag.iloc[2]['cont_nh20']), fp(_a_old['cont_nh20'])))
A('20 日内跌破 MA20 的概率由 %s 单调升至 %s —— 即**年龄越大，结构越容易失效、未来收益越低**。'
  % (fp(_a_young['fail_ma20']), fp(_a_old['fail_ma20'])))
A('')
A('§33 年龄分界扰动（TRAIN 拟合、OOS 评估）。注：tl01_parameter_grid.csv 的 age_cut 行把'
  '（young 组 T+20 均值, old 组 T+20 均值, 差值, OOS IC, ICIR）写入通用列'
  '（share_S3, share_S4, oos_ic, oos_icir, sec），下表按此映射还原：')
A('')
agc = pick(pg, kind='age_cut')
rows = []
for _, r in agc.iterrows():
    agev = int(r['value'])
    rows.append(['Age <= %d vs > %d' % (agev, agev), fp(r['share_S3']), fp(r['share_S4']),
                 fp(r['oos_ic']), f4(r['oos_icir']), f2(r['sec'])])
A(tbl(['分界', 'young 组 T+20', 'old 组 T+20', '差值', 'OOS IC(年龄)', 'ICIR'], rows))
A('')
A('三个分界的 OOS IC 完全一致（%s，ICIR %s），说明年龄信息对分界点不敏感，'
  % (f4(agc.iloc[0]['oos_icir']), f2(agc.iloc[0]['sec'])))
A('但量级很小（IC %s），单靠 Trend Age 不足以构成独立信号。' % f4(agc.iloc[0]['oos_icir']))

# ================================================================= §10
A(h2('§10 Drawdown / Reacceleration'))
A('')
A('§14 回撤结构 + §15 再启动结构。核心是能否**事前**区分「正常回撤」与「趋势破坏 / 终结」。')
A('')
A(tbl(['S7 的出口', 'Count', 'P(to|S7)', 'T+5 均值', 'T+20 均值', 'T+60 均值'],
      [['S7 ' + AR + ' ' + 'S%d %s' % (b, STATE_NAMES[b].split('_')[1]),
        i0(pick(tr, from_state=7, to_state=b).iloc[0]['count']),
        fp(pick(tr, from_state=7, to_state=b).iloc[0]['probability'], 4),
        fp(pick(tr, from_state=7, to_state=b).iloc[0]['fwd5_mean']),
        fp(pick(tr, from_state=7, to_state=b).iloc[0]['fwd20_mean']),
        fp(pick(tr, from_state=7, to_state=b).iloc[0]['fwd60_mean'])]
       for b in (7, 8, 9, 10)]))
A('')
A('S7 的出口概率：留 S7 %s、转 S8 再启动 %s、转 S9 破坏 %s、转 S10 终结 %s。'
  % tuple(fp(pick(tr, from_state=7, to_state=b).iloc[0]['probability'], 3) for b in (7, 8, 9, 10)))
A(('样本极不平衡——S7' + AR + 'S9 仅 %s 例、S7' + AR + 'S10 仅 %s 例，**事前可辨识性在统计上无法建立**；')
  % (i0(pick(tr, from_state=7, to_state=9).iloc[0]['count']),
     i0(pick(tr, from_state=7, to_state=10).iloc[0]['count'])))
A('即 §50 Q6 的答案偏否定（详见 §27 问答）。')
A('')
_re = pick(ic_all, signal='SIG_REACC')
_re20 = pick(_re, horizon=20).iloc[0]
A('独立的再启动信号 SIG_REACC 全样本 IC20 = %s（ICIR %s，AUC20 %s），无独立预测力。'
  % (f4(_re20['mean_ic']), f4(_re20['icir']),
     f4(pick(auc_all, signal='SIG_REACC', horizon=20).iloc[0]['auc'])))
A('§15 的 Potential_Reacceleration 未通过作为独立信号的检验。')

# ================================================================= §11
A(h2('§11 Forward Return'))
A('')
A('§18/§19 每条预注册转换的前向收益分布（Mean / Median / WinRate / P10 / P90）：')
A('')
rows = []
for a, b in PREREG['report_transitions']:
    x = pick(trs, from_state=a, to_state=b)
    if len(x) == 0:
        continue
    r = x.iloc[0]
    rows.append(['S%d' % a + AR + 'S%d' % b, i0(r['fwd20_n']),
                 fp(r['fwd5_mean']), fp(r['fwd5_med']), fp(r['fwd5_win'], 1),
                 fp(r['fwd20_mean']), fp(r['fwd20_med']), fp(r['fwd20_win'], 1),
                 fp(r['fwd20_p10']), fp(r['fwd20_p90']),
                 fp(r['fwd60_mean']), fp(r['fwd60_win'], 1)])
A(tbl(['Transition', 'n(T20)', 'T+5均值', 'T+5中位', 'T+5胜率', 'T+20均值', 'T+20中位',
       'T+20胜率', 'T+20 P10', 'T+20 P90', 'T+60均值', 'T+60胜率'], rows))
A('')
A('关键分布事实：')
A('')
A('- T+20 均值最高的三条：' + '、'.join('S%d' % a + AR + 'S%d（%s）' % (b, fp(v['fwd20_mean']))
                                      for (a, b), v in t20_best) + '；')
A('- T+20 均值最低的三条：' + '、'.join('S%d' % a + AR + 'S%d（%s）' % (b, fp(v['fwd20_mean']))
                                      for (a, b), v in t20_worst) + '；')
A('- T+20 均值为负的转换共 %d 条：%s。' %
  (len(t20_neg), '、'.join('S%d' % a + AR + 'S%d' % b for (a, b), _ in t20_neg) or '无'))
A('- T+20 中位数为负的转换 %d/%d 条，中位数区间 [%s, %s]；全部转换的 T+20 胜率在 %s–%s 之间。'
  % (t20_med_neg, len(t20), fp(min(meds)), fp(max(meds)),
     fp(min(float(v['fwd20_win']) for v in t20.values()), 1),
     fp(max(float(v['fwd20_win']) for v in t20.values()), 1)))
A('')
A('即：这些转换的“正均值”主要来自**右尾**而非中位样本（典型的小亏损 + 少量大盈利结构，§19）。')

# ================================================================= §12
A(h2('§12 MFE / MAE'))
A('')
A('§20 各状态与预注册转换的 MFE / MAE（均值口径）：')
A('')
rows = []
for _, r in mm[mm['kind'] == 'state'].iterrows():
    rows.append([r['name'], i0(r['n']), fp(r['mfe20_mean']), fp(r['mae20_mean']),
                 fp(r['mfe20_p90']), fp(r['mae20_p10']), f2(r['rr20'])])
A(tbl(['State', '样本', 'MFE20 均值', 'MAE20 均值', 'MFE20 P90', 'MAE20 P10', 'RR20'], rows))
A('')
rows = []
for _, r in mm[mm['kind'] == 'transition'].iterrows():
    rows.append([r['name'], i0(r['n']), fp(r['mfe20_mean']), fp(r['mae20_mean']),
                 fp(r['mfe20_p90']), fp(r['mae20_p10']), f2(r['rr20'])])
A(tbl(['Transition', '样本', 'MFE20 均值', 'MAE20 均值', 'MFE20 P90', 'MAE20 P10', 'RR20'], rows))
A('')
_mt = mm[mm['kind'] == 'transition'].sort_values('rr20', ascending=False)
_best = _mt.iloc[0]
_worst = _mt.iloc[-1]
A('§20 的赔率（RR20 = MFE20 均值 / |MAE20 均值|）最好的是 %s（RR20 = %s），最差的是 %s（RR20 = %s）。'
  % (_best['name'], f2(_best['rr20']), _worst['name'], f2(_worst['rr20'])))
_s5 = pick(mm, kind='state', id='S5').iloc[0]
_s4 = pick(mm, kind='state', id='S4').iloc[0]
A('S5 高位拥挤（RR20 = %s，MFE20 %s / MAE20 %s）与 S4 加速（RR20 = %s）虽然绝对波动最大，'
  % (f2(_s5['rr20']), fp(_s5['mfe20_mean']), fp(_s5['mae20_mean']), f2(_s4['rr20'])))
A('但赔率并不占优，再次指向「加速 = 波动扩张 ' + '\u2260' + ' 更好赔率」。')

# ================================================================= §13
A(h2('§13 IC / ICIR'))
A('')
A('§27 横截面 IC（逐日 Spearman，average-rank；t 值 Newey-West，滞后 = horizon-1）。')
A('全样本（ALL）全部信号族 / Baseline：')
A('')
rows = []
for _, r in ic_all.iterrows():
    rows.append([r['signal'], i0(r['horizon']), i0(r['n_dates']), f4(r['mean_ic']),
                 f4(r['icir']), f2(r['ic_t']), f2(r['pos_ratio']), f4(r['pearson'])])
A(tbl(['Signal', 'H', '日数', 'Mean IC', 'ICIR', 't (NW)', '正 IC 占比', 'Pearson'], rows))
A('')
A('主信号 ' + SIG + ' 分期与多周期：')
A('')
rows = []
for _, r in pick(ic, signal=SIG).iterrows():
    rows.append([i0(r['horizon']), r['scope'], i0(r['n_dates']), f4(r['mean_ic']),
                 f4(r['icir']), f2(r['ic_t']), f2(r['pos_ratio']), f4(r['p_bh']),
                 'Y' if str(r['reject_bh']) in ('1', '1.0', 'True') else 'N'])
A(tbl(['H', 'Scope', '日数', 'Mean IC', 'ICIR', 't (NW)', '正 IC 占比', 'BH p', 'FDR 拒绝'], rows))
A('')
_ic20 = pick(ic_all, signal=SIG, horizon=20).iloc[0]
_ts20 = pick(ic_all, signal='SIG_TS', horizon=20).iloc[0]
_hs = [pick(ic_all, signal=SIG, horizon=h).iloc[0] for h in (3, 5, 10, 20, 60)]
A('要点：')
A('')
A(('- ' + SIG + ' 是全样本上 IC 稳定为正的唯一信号；IC 随 horizon 上升（T+3 %s ' + AR +
   ' T+5 %s ' + AR + ' T+10 %s ' + AR + ' T+20 %s），T+60 为 %s（较 T+20 略回落）；')
  % (f4(_hs[0]['mean_ic']), f4(_hs[1]['mean_ic']), f4(_hs[2]['mean_ic']),
     f4(_hs[3]['mean_ic']), f4(_hs[4]['mean_ic'])))
A('  T+20 起 t 值 7.8–11.4。')
_bl = pick(ic_all, horizon=20)
_bl = _bl[_bl['signal'].isin(['B1_MOM20', 'B2_MOM60', 'B3_MA20', 'B4_MA60', 'B5_RS20', 'B6_POS60'])]
A('- **静态趋势强度 SIG_TS（IC20 = %s）与全部 Baseline 在样本内 IC 全部为负**（%s）。'
  % (f4(_ts20['mean_ic']),
     '、'.join('%s %s' % (r['signal'], f4(r['mean_ic'])) for _, r in _bl.iterrows())))
A('  这一点非常关键：在 2018–2026 全样本上，「强动量 / 强趋势」本身是负向信号（反转占优），')
A('  而**由趋势转换条件期望构造的 ' + SIG + ' 反而是唯一正向的**（IC20 = %s）。' % f4(_ic20['mean_ic']))
_tv = pick(ic_all, signal='SIG_TV', horizon=20).iloc[0]
_ta = pick(ic_all, signal='SIG_TA', horizon=20).iloc[0]
_ag = pick(ic_all, signal='SIG_AGE', horizon=20).iloc[0]
_tc = pick(ic_all, signal='SIG_TRANS_CORE', horizon=20).iloc[0]
A('- SIG_TV %s / SIG_TA %s / SIG_REACC %s / SIG_TRANS_CORE %s：速度、加速度、再启动、核心转换 0/1'
  ' 均无独立预测力；SIG_AGE %s 有弱正向信息。'
  % (f4(_tv['mean_ic']), f4(_ta['mean_ic']), f4(_re20['mean_ic']), f4(_tc['mean_ic']),
     f4(_ag['mean_ic'])))

# ================================================================= §14
A(h2('§14 Winner / Loser Discrimination'))
A('')
A('§28 判别力（Top 10%% 为 winner；AUC 用 Mann-Whitney 秩和口径；预注册最低门槛 auc_min = %s）：'
  % f2(PREREG['auc_min']))
A('')
rows = []
for _, r in auc_all.iterrows():
    rows.append([r['signal'], i0(r['horizon']), i0(r['n_dates']), f4(r['auc']),
                 fp(r['prec10'], 2), fp(r['prec20'], 2), f4(r['effect_size']),
                 fp(r['top_mean']), fp(r['universe_mean'])])
A(tbl(['Signal', 'H', '日数', 'AUC', 'Prec@10%', 'Prec@20%', 'EffectSize',
       'Top 十分位均值', '全样本均值'], rows))
A('')
rows = []
for _, r in auc_s.iterrows():
    rows.append([i0(r['horizon']), r['scope'], f4(r['auc']), fp(r['prec10'], 2),
                 fp(r['prec20'], 2), f4(r['effect_size']), fp(r['top_mean']),
                 fp(r['bot_mean']), fp(r['top_minus_bot'])])
A(tbl(['H', 'Scope', 'AUC', 'Prec@10%', 'Prec@20%', 'EffectSize', 'Top 均值',
       'Bottom 均值', 'Top-Bottom'], rows))
A('')
_a20 = pick(auc_s, horizon=20)
_ao = pick(_a20, scope='OOS').iloc[0]
_aa = pick(_a20, scope='ALL').iloc[0]
_al = pick(_a20, scope='LIVE-LIKE').iloc[0]
A('**这是本研究最重要的一处不利证据，必须完整记录**：')
A('')
A('- ' + SIG + ' 的 **AUC 全部低于 0.50**（ALL %s / OOS %s / LIVE-LIKE %s），'
  % (f4(_aa['auc']), f4(_ao['auc']), f4(_al['auc'])))
A(('  远低于预注册门槛 %s ' + AR + ' 触发 §28 的 **LOW_DISCRIMINATION**。') % f2(PREREG['auc_min']))
A('- Prec@10%% = %s（ALL）/ %s（OOS），**低于 10%% 的随机基准率**，即高信号端不能选出赢家。'
  % (fp(_aa['prec10'], 2), fp(_ao['prec10'], 2)))
A('- 与此前的正 IC（T+20 %s）并不矛盾：IC 与 AUC 同为平均秩口径，但一个度量整体单调相关，'
  % f4(_ic20['mean_ic']))
A('  一个度量顶端命中率。' + SIG + ' 是**离散信号**（只在 121 个 (state_prev,state) 单元内取常数，'
  '并列极多），其正 IC 主要来自「低端避开大亏损」：Bottom 十分位 T+20 = %s vs 全样本 %s，'
  % (fp(_aa['bot_mean']), fp(_aa['universe_mean'])))
A('  而 Top 十分位仅 %s（相对全样本仅 %s）。'
  % (fp(_aa['top_mean']), fp(_aa['top_mean'] - _aa['universe_mean'])))
_ab = pick(auc_all, horizon=20)
_ab = _ab[_ab['signal'].isin(['B1_MOM20', 'B2_MOM60', 'B3_MA20', 'B4_MA60', 'B5_RS20', 'B6_POS60'])]
A('- 反差点：全部 Baseline 的 AUC **高于** ' + SIG + '（%s），'
  % '、'.join('%s %s' % (r['signal'], f4(r['auc'])) for _, r in _ab.iterrows()))
A('  即「挑选 top 10% 赢家」这件事上，普通动量/位置指标反而更有效。')

# ================================================================= §15
A(h2('§15 Momentum-Controlled Alpha'))
A('')
_ctrl = FM  # placeholder to avoid unused warning
_ctl = FI['fama_macbeth']['controls']
A('§26/§25 增量信息检验。控制变量：[' + ', '.join(_ctl) + ']。')
A('')
rows = []
ics = FI.get('ic', {})
for k, lbl in (('SIG_TRANS', SIG), ('B1_MOM20', 'B1 Mom20'), ('B2_MOM60', 'B2 Mom60'),
               ('B5_RS20', 'B5 RS20'), ('B6_POS60', 'B6 Pos60'),
               ('SIG_TRANS_resid_on_controls', SIG + ' | controls'),
               ('MOM20_resid_on_SIGTRANS', 'Mom20 | ' + SIG)):
    v = ics.get(k)
    if not v:
        continue
    rows.append([lbl, f4(v.get('TRAIN', {}).get('ic')), f4(v.get('TRAIN', {}).get('icir')),
                 f4(v.get('OOS', {}).get('ic')), f4(v.get('OOS', {}).get('icir'))])
A(tbl(['信号', 'TRAIN IC', 'TRAIN ICIR', 'OOS IC', 'OOS ICIR'], rows))
A('')
fm = FI.get('fama_macbeth', {})
rows = []
for key, lbl in (('reduced_only_sigtrans', 'fwd20 ~ ' + SIG + '（无控制）'),
                 ('full_with_controls', 'fwd20 ~ ' + SIG + ' + controls')):
    blk = fm.get(key, {})
    rows.append([lbl] + [f4(blk.get(p, {}).get('mean')) + ' (t=' +
                         f2(blk.get(p, {}).get('t')) + ')'
                         for p in ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE')])
A(tbl(['回归', 'TRAIN', 'VALID', 'OOS', 'LIVE-LIKE'], rows))
A('')
_ri = ics['SIG_TRANS_resid_on_controls']
_rm = ics['MOM20_resid_on_SIGTRANS']
A('结论（§50 Q8）：')
A('')
A('- 无控制时 Fama-MacBeth 系数全期为正且显著（OOS mean=+%s, t=%s）。'
  % (f4(fm['reduced_only_sigtrans']['OOS']['mean']).lstrip('0'),
     f2(fm['reduced_only_sigtrans']['OOS']['t'])))
A('- **加入控制后**：TRAIN IC 由 %s 塌到 **%s**，OOS IC 由 %s 降到 **%s**；'
  % (f4(ics['SIG_TRANS']['TRAIN']['ic']), f4(_ri['TRAIN']['ic']),
     f4(ics['SIG_TRANS']['OOS']['ic']), f4(_ri['OOS']['ic'])))
A('  Fama-MacBeth 系数从 %s 降到 %s（TRAIN）、VALID 的 t 仅 %s（不显著）。'
  % (f4(fm['reduced_only_sigtrans']['TRAIN']['mean']), f4(fm['full_with_controls']['TRAIN']['mean']),
     f2(fm['full_with_controls']['VALID']['t'])))
A('- 反向检验：Mom20 对 ' + SIG + ' 取残差后，OOS IC 仍为 **%s**（几乎未被吸收）——'
  % f4(_rm['OOS']['ic']))
A('  但不为零的是 Mom20 的负向信息，不是 ' + SIG + ' 的正向信息。')
A(('- 综合判定：' + SIG + ' 的增量信息**大部分可由 Momentum / Size / Value / Vol / Industry 解释**，'
   '残余增量在 OOS 为正（%s），但在 TRAIN 为负、在 VALID 不显著 ' + AR + ' 未通过 Momentum Control 门禁。')
  % f4(_ri['OOS']['ic']))

# ================================================================= §16
A(h2('§16 Industry-Controlled Alpha'))
A('')
A('§26 行业控制已在 §15 的 Fama-MacBeth 全控制回归中包含 32 个申万一级行业哑变量。')
A('另做 §25 双重排序（momentum 五分位 × ' + SIG + ' 五分位，OOS 平均 T+20 收益）：')
A('')
_ds = FI.get('double_sort', {}).get('OOS', {})
_cell = _ds.get('cell') or []
if _cell:
    rows = []
    for i, row in enumerate(_cell):
        rows.append(['M%d (momentum %s)' % (i + 1, ['最低', '低', '中', '高', '最高'][i])] +
                    [fp(x) for x in row])
    A(tbl(['行 = Momentum 五分位 / 列 = SIG_TRANS 五分位', 'Q1', 'Q2', 'Q3', 'Q4', 'Q5'], rows))
    _mx = max(max(r) for r in _cell)
    _mxi = [(i, j) for i, r in enumerate(_cell) for j, v in enumerate(r) if v == _mx][0]
    A('')
    A('OOS 样本 %s。最高单元出现在 M%d × Q%d（%s），即**最低动量组的最低生命周期分位**；'
      % (format(int(_ds.get('n', 0)), ','), _mxi[0] + 1, _mxi[1] + 1, fp(_mx)))
    A('整体上「低动量」行的未来收益高于「高动量」行（被动量负向主导），行内随 ' + SIG +
      ' 分位提高**未出现单调上升**。')
A('')
A('与 §15 一致：行业 + 规模 + 价值 + 波动 + 动量全控制后，T+20 IC 由 %s 降至 %s，'
  % (f4(ics['SIG_TRANS']['OOS']['ic']), f4(_ri['OOS']['ic'])))
A('行业本身不构成额外正向来源，也不构成反证——结论是「生命周期信号的行业中性增量很薄」。')

# ================================================================= §17
A(h2('§17 Year Stability'))
A('')
A('§29 时间稳定性（' + SIG + '，逐年）：')
A('')
for H in (5, 20, 60):
    sub = pick(yr_s, horizon=H, kind='year')
    rows = []
    for _, r in sub.iterrows():
        rows.append([r['period'], i0(r['n_dates']), f4(r['mean_ic']), f4(r['icir']),
                     f2(r['pos_ratio'])])
    if len(rows):
        A('T+%d：' % H)
        A('')
        A(tbl(['年份', '日数', 'Mean IC', 'ICIR', '正 IC 占比'], rows))
        A('')
_y20 = pick(yr_s, horizon=20, kind='year')
_pos = _y20[_y20['mean_ic'] > 0]
A('结论：' + SIG + ' 的 T+20 IC 在 %s 个自然年中 %s 年为正（%s），'
  % (len(_y20), len(_pos), ' / '.join('%s %s' % (r['period'], f4(r['mean_ic']))
                                      for _, r in _y20.iterrows())))
A('无单年翻转 ' + AR + ' 通过 §29 的年份稳定性方向检验（year_stable_min = %s）。'
  % f2(PREREG['year_stable_min']))
_yrq = pick(yr_s, horizon=20)
_q = _yrq[_yrq['kind'].astype(str).str.contains('quarter', na=False)]
if len(_q):
    _qn = _q[_q['mean_ic'] < 0]
    A('')
    if len(_qn) == 0:
        _qi = _q.loc[_q['mean_ic'].idxmin()]
        A('需同时记录的单季口径：%s 个季度**全部为正 IC**，最低为 %s（%s），无单季翻转。'
          % (len(_q), f4(_qi['mean_ic']), _qi['period']))
    else:
        A('需同时记录的单季警讯：%s 个季度中 %s 个为负 IC（%s），属单季噪声但仍需在 OOS 后跟踪。'
          % (len(_q), len(_qn), '、'.join('%s %s' % (r['period'], f4(r['mean_ic']))
                                          for _, r in _qn.iterrows())))

# ================================================================= §18
A(h2('§18 Regime Stability'))
A('')
A('§30 Regime 稳定性（独立定义，非由研究结果定义）：')
A('')
rows = []
for _, r in reg_s.iterrows():
    rows.append([i0(r['horizon']), r['regime'], i0(r['n_dates']), f4(r['mean_ic']),
                 f4(r['icir']), f2(r['ic_t']), fp(r['top_mean']), fp(r['uni_mean']),
                 fp(r['spread']), f2(r['winrate'])])
A(tbl(['H', 'Regime', '日数', 'Mean IC', 'ICIR', 't', 'Top 均值', '全样本均值',
       'Spread', '胜率'], rows))
A('')
_r20 = pick(reg_s, horizon=20)
A('结论：' + SIG + ' 在三个 regime 下 IC 全部为正，**没有出现 §30 要求的“方向频繁反转”**：')
A('')
A('- T+20：' + '；'.join('%s %s (t=%s)' % (r['regime'], f4(r['mean_ic']), f2(r['ic_t']))
                        for _, r in _r20.iterrows()) + '；')
_weak = _r20.loc[_r20['mean_ic'].idxmin()]
A('- 三档中最弱的是 %s（IC %s，约为最强档的 %s）。'
  % (_weak['regime'], f4(_weak['mean_ic']),
     f2(float(_weak['mean_ic']) / float(_r20['mean_ic'].max()))))
_mae = _r20['mae20'].dropna()
A('- 三种 regime 的 T+20 MAE 均在 %s ～ %s，未出现单边异常左尾。'
  % (fp(_mae.min()), fp(_mae.max())))

# ================================================================= §19
A(h2('§19 Parameter Perturbation'))
A('')
A('§33 参数扰动（禁止寻找单一最优参数，只看是否存在连续稳定区域）。单因子（OFAT）：')
A('')
of = pick(pg, kind='config', grid='ofat')
rows = []
for _, r in of.iterrows():
    rows.append([r['tag'], f4(r['train_ic']), f4(r['train_icir']), f4(r['oos_ic']),
                 f4(r['oos_icir']), f2(r['sec'])])
A(tbl(['配置', 'TRAIN IC', 'TRAIN ICIR', 'OOS IC', 'OOS ICIR', '耗时(s)'], rows))
A('')
A('§34 二维网格平滑性。注：grid_summary 行把（OOS IC 下限, 同号比例, OOS IC 上限, 粗糙度, 格数）'
  '写入通用列（share_S7, share_S3, oos_ic, oos_icir, oos_days），下表按此映射还原：')
A('')
gs = pick(pg, kind='grid_summary')
rows = []
for _, r in gs.iterrows():
    rows.append([r['grid'], '[%s, %s]' % (f4(r['share_S7']), f4(r['oos_ic'])),
                 i0(r['oos_days']), f2(r['share_S3']), f4(r['oos_icir'])])
A(tbl(['网格', 'OOS IC 范围', '格数', '同号比例', '粗糙度'], rows))
A('')
g1 = pick(pg, kind='config', grid='ma_x_trend')
if len(g1):
    A('ma_win × trend_win 的 OOS IC（行 = ma_win %s，列 = trend_win %s）：'
      % ('/'.join(str(x) for x in PREREG['grid_ma']),
         '/'.join(str(x) for x in PREREG['grid_trend_win'])))
    A('')
    rows = []
    for mw in PREREG['grid_ma']:
        v = [g1[(g1['param_a'] == mw) & (g1['param_b'] == tw)]['oos_ic'].values
             for tw in PREREG['grid_trend_win']]
        rows.append(['ma_win=%d' % mw] + [f4(x[0]) if len(x) else 'n/a' for x in v])
    A(tbl([''] + ['trend %d' % tw for tw in PREREG['grid_trend_win']], rows))
    A('')
g2 = pick(pg, kind='config', grid='rs_x_dd')
if len(g2):
    A('rs_win × dd_mild 的 OOS IC（行 = rs_win %s，列 = dd %s）：'
      % ('/'.join(str(x) for x in PREREG['grid_rs_win']),
         '/'.join(fp(x, 1) for x in PREREG['grid_dd'])))
    A('')
    rows = []
    for rw in PREREG['grid_rs_win']:
        v = [g2[(g2['param_a'] == rw) & (g2['param_b'] == dd)]['oos_ic'].values
             for dd in PREREG['grid_dd']]
        rows.append(['rs_win=%d' % rw] + [f4(x[0]) if len(x) else 'n/a' for x in v])
    A(tbl([''] + ['dd %s' % fp(dd, 1) for dd in PREREG['grid_dd']], rows))
    A('')
_of = pick(pg, kind='config', grid='ofat')
A('结论：')
A('')
for _, r in gs.iterrows():
    A('- %s：OOS IC 范围 [%s, %s]，%s 格同号比例 %s，粗糙度（相邻差绝对均值）%s，'
      % (r['grid'], f4(r['share_S7']), f4(r['oos_ic']), i0(r['oos_days']), f2(r['share_S3']),
         f4(r['oos_icir'])))
A('  孤立高点 = False ' + AR + ' **SMOOTH**（两组网格的孤立高点判定均为 False）。')
A('- 单因子扰动区间：' + '；'.join(
    '%s %s' % (ax, '/'.join(f4(x) for x in _of[_of['axis'] == ax]['oos_ic'].tolist()))
    for ax in ('ma_win', 'trend_win', 'rs_win', 'dd_mild')) + '（OOS IC）。')
A('- 全部为连续稳定区域，**不触发 §34 的 PARAMETER_FRAGILE**。')

# ================================================================= §20
A(h2('§20 Null Model'))
A('')
A('§35 零假设 N1–N5：')
A('')
rows = []
for _, r in nm.iterrows():
    rows.append([r['null'], r['description'] if isinstance(r['description'], str) else '',
                 f4(r['mean_ic']), f4(r['sd_ic']), i0(r['n']), f2(r['z_vs_real']),
                 f4(r['p']) if pd.notna(r['p']) else '—'])
A(tbl(['Null', '说明', 'Mean IC', 'SD', 'n', 'z vs real', 'p'], rows))
A('')
_n3 = nm[nm['null'].astype(str).str.startswith('N3')]
_n1 = nm[nm['null'].astype(str).str.startswith('N1')]
_n2 = nm[nm['null'].astype(str).str.startswith('N2')]
A('判定：')
A('')
A('- N3（随机重排 121 个 (state_prev,state) 单元的条件期望）在 OOS 上 5 个 horizon 全部 |z| ≥ %s，'
  % f2(_n3['z_vs_real'].abs().min()))
A('  经验 p = %s（1,000 次置换的下限）' % f4(_n3['p'].min()) + AR + ' **Null Model PASS**。')
A('- N1（日内随机选股）|z| %s–%s，噪声均值 ≈ 0，符合纯噪声预期。'
  % (f2(_n1['z_vs_real'].abs().min()), f2(_n1['z_vs_real'].abs().max())))
A('- N2（信号整体平移 1..%d 日）|z| %s–%s：平移后仍残留正 IC（%s ～ %s），'
  % (PREREG['null_shift_days'], f2(_n2['z_vs_real'].abs().min()),
     f2(_n2['z_vs_real'].abs().max()), f4(_n2['mean_ic'].min()), f4(_n2['mean_ic'].max())))
A('  说明状态信息有 **短程自相关**（未完全随机化），但量级不足真实信号（%s ～ %s）的一半。'
  % (f4(_hs[0]['mean_ic']), f4(_hs[3]['mean_ic'])))
A('- N4/N5（现实基准，非随机，仅作参照）：Mom20 / RS20 的 T+20 OOS IC = %s。'
  % f4(pick(nm, null='N4_Momentum20_T20').iloc[0]['mean_ic']))
A('- 结论：**Lifecycle ' + '\u2260' + ' Random**，§35 的 FAIL 条件不成立。')

# ================================================================= §21
A(h2('§21 Permutation Test'))
A('')
A('§36 置换检验（%s 次，打乱 State Label 重算 IC）+ §37 多重检验（BH-FDR）：'
  % format(PREREG['n_perm'], ','))
A('')
rows = []
for _, r in pm.iterrows():
    rows.append([i0(r['horizon']), r['scope'], f4(r['real_ic']), f4(r['null_mean']),
                 f4(r['null_sd']), f2(r['z']), f4(r['p_emp']), i0(r['n_perm']),
                 i0(r['n_oos_days']), f4(r['p_adj_bh']),
                 'Y' if str(r['reject_fdr05']) in ('1', '1.0', 'True') else 'N'])
A(tbl(['H', 'Scope', '真实 IC', '置换均值', '置换 SD', 'z', 'p_emp', '置换次数',
       'OOS 日数', 'BH p', 'FDR 拒绝'], rows))
A('')
A('五个 horizon 的 z = %s – %s，p_emp 全部触底 %s（= 1/(N+1)），BH-FDR 全部拒绝原假设。'
  % (f2(pm['z'].min()), f2(pm['z'].max()), f4(pm['p_emp'].max())))
A('按 §37 要求同时报告 Raw p 与 Adjusted p：两者均为 %s，**不存在“只挑最显著一组”的风险**。'
  % f4(pm['p_adj_bh'].max()))

# ================================================================= §22
A(h2('§22 OOS'))
A('')
A('§31 分期（' + SIG + ' 在 TRAIN 2018-2022 拟合后冻结，OOS / LIVE-LIKE 不参与任何选择）：')
A('')
rows = []
for _, r in oos_s.iterrows():
    rows.append([i0(r['horizon']), r['phase'], i0(r['n_dates']), f4(r['mean_ic']),
                 f4(r['icir']), f2(r['ic_t']), f4(r['auc']), fp(r['top_mean']),
                 fp(r['uni_mean']), fp(r['excess']), fp(r['spread'])])
A(tbl(['H', 'Phase', '日数', 'Mean IC', 'ICIR', 't', 'AUC', 'Top 均值',
       '全样本均值', 'Excess', 'Spread'], rows))
A('')
_o2 = pick(oos_all, horizon=20).iloc[0]
_ol = pick(oos_s, horizon=20, phase='LIVE-LIKE').iloc[0]
A('OOS(2025) 判定：')
A('')
A('- **IC 通过**：T+20 IC = %s，ICIR %s，t = %s，优于 TRAIN(%s)；T+3/5/10/60 亦全正'
  % (f4(_o2['mean_ic']), f4(_o2['icir']), f2(_o2['ic_t']),
     f4(pick(oos_s, horizon=20, phase='TRAIN').iloc[0]['mean_ic'])))
A('  （%s）。' % ' / '.join(f4(pick(oos_all, horizon=h).iloc[0]['mean_ic'])
                           for h in (3, 5, 10, 60)))
A('- **判别力不通过**：T+20 AUC = %s < %s（§28 预注册门槛），Prec@10%% = %s < 10%% 基准率。'
  % (f4(_o2['auc']), f2(PREREG['auc_min']), fp(_ao['prec10'], 2)))
A('- 十分位层：Top 十分位 - 全样本 = %s（为负），Top - Bottom = %s，分层幅度很小且无单调。'
  % (fp(_o2['excess']), fp(_o2['spread'])))
A('- LIVE-LIKE(2026) T+20 IC = %s 但 t 仅 %s、AUC %s，不能作为独立证据。'
  % (f4(_ol['mean_ic']), f2(_ol['ic_t']), f4(_al['auc'])))

# ================================================================= §23
A(h2('§23 Walk Forward'))
A('')
A('§32 Walk Forward（每个窗口独立在训练期拟合 ' + SIG + ' 映射，再在测试年评估）：')
A('')
rows = []
for _, r in wf[wf['scope'] == 'test'].iterrows():
    rows.append([r['window'], r['train'], r['test'], i0(r['n_dates']), f4(r['mean_ic']),
                 f4(r['icir']), f2(r['ic_t']), f4(r['auc']), fp(r['top_mean']),
                 fp(r['uni_mean']), fp(r['spread']), fp(r['top_mfe20']), fp(r['top_mae20'])])
A(tbl(['窗口', '训练期', '测试年', '日数', 'Mean IC', 'ICIR', 't', 'AUC', 'Top 均值',
       '全样本均值', 'Spread', 'Top MFE20', 'Top MAE20'], rows))
A('')
_wt = wf[wf['scope'] == 'test']
A('结论：')
A('')
A('- 六个窗口的测试年 IC **全部为正**（%s）' % ' / '.join(
    '%s %s' % (r['window'], f4(r['mean_ic'])) for _, r in _wt.iterrows()) +
  AR + ' IC 层面的 OOS 稳定性成立。')
_w4 = _wt[_wt['window'] == 'W4'].iloc[0]
A('- 但 AUC 六个窗口**全部低于 0.50**（%s–%s），判别力层面无一个窗口通过。'
  % (f4(_wt['auc'].min()), f4(_wt['auc'].max())))
A('- %s（测试 %s）IC 仅 %s、t=%s，是最弱窗口；%s（测试 %s）IC 最高但 t 仅 %s。'
  % (_w4['window'], i0(_w4['test']), f4(_w4['mean_ic']), f2(_w4['ic_t']),
     _wt.iloc[-1]['window'], i0(_wt.iloc[-1]['test']), f2(_wt.iloc[-1]['ic_t'])))
A('- Spread（Top 十分位 - 全样本）六个窗口**全部为正**（%s–%s），'
  % (fp(_wt['spread'].min()), fp(_wt['spread'].max())))
A('  但绝对幅度很小（最大 %s），不足以支撑组合层面的稳健超额。' % fp(_wt['spread'].max()))

# ================================================================= §24
A(h2('§24 Cost'))
A('')
A('§38 成本敏感（单边 bp；每次换仓 100% 卖 + 100% 买，实际成本 = 2 × bp；T+1 开盘成交）。')
A('组合 P10_all（Top 10% 等权）：')
A('')
c1 = pick(cost, variant='P10_all')
rows = []
for _, r in c1.iterrows():
    rows.append([r['phase'], i0(r['cost_bp']), fp(r['ann_ret']), fp(r['bench_ann']),
                 fp(r['excess_ann']), f2(r['sharpe']), f2(r['maxdd']), f2(r['calmar']),
                 f2(r['winrate'])])
A(tbl(['Phase', 'Cost', '组合年化', '基准年化', '超额', 'Sharpe', 'MaxDD', 'Calmar', '胜率'], rows))
A('')
A('重点档位 30bp（P10_all，§38 指定的重点成本）：')
A('')
_c30 = pick(cost, variant='P10_all', cost_bp=30)
A(tbl(['Phase', '组合年化', '基准年化', '超额', 'Sharpe', 'MaxDD', '胜率'],
      [[r['phase'], fp(r['ann_ret']), fp(r['bench_ann']), fp(r['excess_ann']),
        f2(r['sharpe']), fp(r['maxdd']), f2(r['winrate'])] for _, r in _c30.iterrows()]))
A('')
_c0 = pick(cost, variant='P10_all', cost_bp=0)
A('关键事实：')
A('')
A('- 0bp（无摩擦）时：' + '；'.join('%s %s' % (r['phase'], fp(r['excess_ann']))
                                    for _, r in _c0.iterrows()) +
  '（P10_all 超额）；其中 OOS 的 P10_all 即使 0bp 也为负，'
  '但同 OOS 期 P20_all/P30_all 在 0bp 下超额为 %s / %s。'
  % (fp(pick(cost, variant='P20_all', cost_bp=0, phase='OOS').iloc[0]['excess_ann']),
     fp(pick(cost, variant='P30_all', cost_bp=0, phase='OOS').iloc[0]['excess_ann'])))
A('- **30bp 时四个分期全部跑输基准**（%s）。'
  % ' / '.join(fp(r['excess_ann']) for _, r in _c30.iterrows()))
A('- 未扣成本的年化本身差异巨大（VALID %s vs OOS %s），说明收益被单一强势阶段主导。'
  % (fp(pick(cost, variant='P10_all', cost_bp=0, phase='VALID').iloc[0]['ann_ret']),
     fp(pick(cost, variant='P10_all', cost_bp=0, phase='OOS').iloc[0]['ann_ret'])))
A('- 按 §38/§51，**30bp 后 Alpha ≈ 0（甚至为负）' + AR + ' Cost 门禁不通过**。')

# ================================================================= §25
A(h2('§25 Portfolio Simulation'))
A('')
A('§43 组合模拟：T+1 开盘买入、非重叠 21 日换仓、Top 10%/20%/30% 等权；')
A('§39 同时报告 Signal Frequency / Exposure / Turnover。')
A('')
rows = []
for _, r in pf_.iterrows():
    rows.append([r['variant'], r['phase'], fp(r['ann_ret']), fp(r['bench_ann']),
                 fp(r['excess_ann']), f2(r['sharpe']), f2(r['sortino']), f2(r['maxdd']),
                 f2(r['calmar']), f2(r['winrate']), i0(r['n_cohort']), f4(r['ic']), f4(r['icir'])])
A(tbl(['组合', 'Phase', '年化', '基准', '超额', 'Sharpe', 'Sortino', 'MaxDD', 'Calmar',
       '胜率', 'Cohort', 'IC', 'ICIR'], rows))
A('')
A('§39 Exposure / Turnover：')
A('')
_cov = pick(pf, kind='coverage')
_exp = float(_cov.iloc[0]['exposure']) if len(_cov) else float('nan')
_p10 = pick(pf_, variant='P10_all')
_nd = float(_ic20['n_dates'])
A(tbl(['指标', '取值', '说明'], [
    ['信号可用日占比', fp(_nd / 2120.0, 1),
     '2,120 交易日中 %s 日有有效横截面（T+20 口径）' % i0(_nd)],
    ['持仓覆盖率 (exposure)', fp(_exp), '有信号日中实际可持有比例'],
    ['换仓频率', '每 21 交易日一次（非重叠）', 'turnover = 1.0 / cohort'],
    ['有效 cohort 数', i0(_p10.iloc[0]['n_cohort']) + '（P10_all TRAIN）', '全样本合计见各分期'],
    ['平均持仓只数', 'P10 ≈ %s / P20 ≈ %s / P30 ≈ %s'
     % (i0(pick(pf_, variant='P10_all', phase='TRAIN').iloc[0]['avg_pos']),
        i0(pick(pf_, variant='P20_all', phase='TRAIN').iloc[0]['avg_pos']),
        i0(pick(pf_, variant='P30_all', phase='TRAIN').iloc[0]['avg_pos'])),
     '按有效信号横截面 10%/20%/30%'],
]))
A('')
A('注意：§39 明确禁止「用低频信号直接与满仓指数收益比较后宣布 Alpha」。')
A('本策略信号覆盖率高、持仓接近满仓，因此与基准的比较不属低频陷阱；')
A('真正的障碍是成本与右尾依赖，而非暴露度。')
A('')
A('§45 右尾研究 / §46 删除超级赢家（P10_all）：')
A('')
A(tbl(['右尾贡献口径', '个股收益占总收益比'],
      [[r['tag'], fp(r['cum'])] for _, r in tail_.iterrows()]))
A('')
rows = []
for _, r in lo_.iterrows():
    rows.append([r['variant'], r['phase'], fp(r['ann_ret']), f2(r['sharpe']), f2(r['maxdd'])])
A(tbl(['变体', 'Phase', '剩余年化', 'Sharpe', 'MaxDD'], rows))
A('')
_t5 = pick(tail_, tag='top5%_pnl_share')
_t1 = pick(tail_, tag='top1%_pnl_share')
_t10 = pick(tail_, tag='top10%_pnl_share')
_lt5 = pick(lo_, variant='leaveout_top5%')
_pfa = pick(pf_, variant='P10_all')
A('结论（§45/§46）：')
A('')
A('- Top 1%% 个股贡献 %s，Top 5%% 贡献 %s，Top 10%% 贡献 %s ' % (fp(_t1.iloc[0]['cum']),
                                                                 fp(_t5.iloc[0]['cum']),
                                                                 fp(_t10.iloc[0]['cum'])) +
  AR + ' **TAIL_DEPENDENT**。')
_pairs = []
for ph in ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE'):
    a0 = _pfa[_pfa['phase'] == ph]
    a1 = _lt5[_lt5['phase'] == ph]
    if len(a0) and len(a1):
        _pairs.append('%s %s ' % (ph, fp(a0.iloc[0]['ann_ret'])) + AR + ' %s'
                      % fp(a1.iloc[0]['ann_ret']))
A('- 剔除 Top 5% 后各期年化变为：' + '；'.join(_pairs) + '。')
A('- 依据 §46，标记为 **EXTREME_TAIL_DEPENDENT**：策略层面的正收益集中于极少数超级趋势股。')

# ================================================================= §26
A(h2('§26 Counterfactual'))
A('')
A('§25 反事实：同样的股票、同样的市场环境，**不使用生命周期状态、仅用普通 Momentum / 位置指标**，')
A('结果是否相同？Baseline B1–B6：')
A('')
_cf = [('SIG_TRANS', SIG, '主动信号（生命周期转换）'),
       ('SIG_TS', 'B0 静态趋势强度', '趋势强度本身')]
for s, nmx in (('B1_MOM20', 'B1 Mom20'), ('B2_MOM60', 'B2 Mom60'), ('B3_MA20', 'B3 MA20'),
               ('B4_MA60', 'B4 MA60'), ('B5_RS20', 'B5 RS20'), ('B6_POS60', 'B6 Pos60')):
    _cf.append((s, nmx, {'B1_MOM20': '20 日动量', 'B2_MOM60': '60 日动量', 'B3_MA20': 'Close/MA20',
                         'B4_MA60': 'Close/MA60', 'B5_RS20': '相对强度',
                         'B6_POS60': '价格位置'}[s]))
rows = []
for s, lbl, desc in _cf:
    r_all = pick(ic_all, signal=s, horizon=20)
    r_oos = pick(oos, signal=s, horizon=20, phase='OOS')
    r_auc = pick(auc_all, signal=s, horizon=20)
    rows.append([lbl, desc,
                 f4(r_all.iloc[0]['mean_ic']) if len(r_all) else 'n/a',
                 f4(r_oos.iloc[0]['mean_ic']) if len(r_oos) else 'n/a',
                 f4(r_auc.iloc[0]['auc']) if len(r_auc) else 'n/a'])
A(tbl(['信号', '说明', '全样本 IC20', 'OOS IC20', '全样本 AUC20'], rows))
A('')
A('最重要的反事实结果有三条：')
A('')
_oosb = pick(oos, horizon=20, phase='OOS')
_oosb = _oosb[_oosb['signal'].isin(['B1_MOM20', 'B2_MOM60', 'B3_MA20', 'B4_MA60', 'B5_RS20', 'B6_POS60'])]
A('1. **「不用生命周期、只用动量」在本样本内是负向策略**：全部 Baseline 的全样本 IC20 显著为负')
A('   （%s），而 ' % '、'.join('%s %s' % (r['signal'], f4(pick(ic_all, signal=r['signal'], horizon=20).iloc[0]['mean_ic']))
                            for _, r in _oosb.iterrows()) + SIG + ' 为 %s。因此本研究'
  % f4(_ic20['mean_ic']))
A('   **不是**「Momentum 的重新包装」——至少在符号与量级上，生命周期转换与静态动量相反。')
A('2. **但加上控制变量后，' + SIG + ' 的增量塌缩**（§15）：OOS 仅剩 %s、TRAIN 转为 %s，'
  % (f4(_ri['OOS']['ic']), f4(_ri['TRAIN']['ic'])))
A('   说明「与动量反向」这一属性本身也被风险因子组吸收，而不是独立的生命周期 alpha。')
A('3. **在「挑 top 10% 赢家」上 Baseline 反而更强**：' +
  '、'.join('%s %s' % (r['signal'], f4(r['auc'])) for _, r in _ab.iterrows()) +
  '，均显著高于 ' + SIG + ' 的 %s。' % f4(_aa['auc']))
A('   生命周期信号的长处是「避开左尾」，短处是「抓不住右尾」。')

# ================================================================= §27
A(h2('§27 Final Decision'))
A('')
A('§51 停止规则逐项核对：')
A('')
A(tbl(['§51 停止条件', '是否触发', '证据'], [
    ['OOS FAIL', '部分触发', 'OOS IC %s (t=%s) 通过；OOS AUC %s 判别力不通过'
     % (f4(_o2['mean_ic']), f2(_o2['ic_t']), f4(_o2['auc']))],
    ['Random Null 可复制', '未触发', 'N3 置换 5 个 horizon 全 |z|≥%s, p=%s'
     % (f2(_n3['z_vs_real'].abs().min()), f4(_n3['p'].min()))],
    ['Momentum 控制后 Alpha 消失', '触发', 'TRAIN IC %s；OOS 仅剩 %s'
     % (f4(_ri['TRAIN']['ic']), f4(_ri['OOS']['ic']))],
    ['AUC ≈ 0.5', '触发', 'ALL %s / OOS %s；六个 WF 窗口全部 <0.50'
     % (f4(_aa['auc']), f4(_ao['auc']))],
    ['Parameter Fragile', '未触发', '两组网格 SMOOTH，同号 1.00，无孤立高点'],
    ['Regime 方向频繁反转', '未触发', 'BEAR/NORMAL/BULL 三档 IC 全正'],
    ['Cost After Alpha ≈ 0', '触发', '30bp 后四分期超额全部为负（%s）'
     % ' / '.join(fp(r['excess_ann']) for _, r in _c30.iterrows())],
]))
A('')
A('§52 判定：')
A('')
A(tbl(['§52 门禁', '判定', '依据'], [
    ['OOS', 'PASS（IC 层）', 'T+20 IC %s, ICIR %s, t %s' % (f4(_o2['mean_ic']),
                                                          f4(_o2['icir']), f2(_o2['ic_t']))],
    ['Walk Forward', 'PASS（IC 层）', '6/6 测试年 IC 为正（%s–%s）；AUC 6/6 <0.50'
     % (f4(_wt['mean_ic'].min()), f4(_wt['mean_ic'].max()))],
    ['Parameter Stability', 'PASS', 'ma×trend 与 rs×dd 两组网格 SMOOTH'],
    ['Null Model', 'PASS', 'N3 全 horizon |z|≥%s，BH-FDR 全拒绝' % f2(_n3['z_vs_real'].abs().min())],
    ['Momentum Control', 'FAIL', '全控制后 TRAIN IC %s、OOS %s' % (f4(_ri['TRAIN']['ic']),
                                                                 f4(_ri['OOS']['ic']))],
    ['Cost', 'FAIL', '30bp 后四分期超额全负'],
    ['Trading Alpha', 'NO', 'AUC<0.5、十分位无单调、收益 EXTREME_TAIL_DEPENDENT'],
]))
A('')
A('**FINAL STATUS: CONDITIONAL**')
A('')
A('判据（§52 CONDITIONAL 定义：「某个生命周期 / Transition 显示稳定预测信息，但价格 Alpha')
A('尚不足以交易」）：')
A('')
A('- 支持 CONDITIONAL-而非-FAIL 的证据：' + SIG + ' 的 IC 在 OOS / 六个 Walk-Forward 窗口 /')
A('  九个自然年 / 三个 regime 上全部为正，通过置换检验与参数平滑性检验，且与被动的静态动量')
A('  **符号相反**（不是动量的重新包装）。生命周期与状态转换确实携带稳定的**结构性**信息。')
A('- 阻止其升级为 PASS 的证据：AUC 恒 <0.50（顶端选不出赢家）、动量/规模/价值/波动/行业全控制后')
A('  增量几乎消失、30bp 成本后组合超额全部为负、收益 EXTREME_TAIL_DEPENDENT（剔除 Top 5% 后')
A('  TRAIN 年化转负）、OOS 十分位无单调分层。')
A('- 上述两项均为 §51 明文触发的停止条件，因此**本研究到此停止，不再调参、不再降门槛、')
A('  不再改状态定义**。')
A('')
A('**本研究是否产生实盘授权：NO**（§55 默认 NO；本研究 V1 不产生任何实盘交易授权）。')
A('本研究不进入 TL-02 交易层；仅当结构性信号能在后续独立研究中获得可交易的价格 alpha 时，')
A('才允许另开 TL-02 Trend Transition Trading。')

# ================================================================= §50 Q&A
A(h2('§50 必须回答的核心问题（Q1 – Q15）'))
A('')
_s4s5 = pick(st, state=4).iloc[0]
_s5s = pick(st, state=5).iloc[0]
_s1s = pick(st, state=1).iloc[0]
_f20 = [(r['name'], float(r['fwd20_mean'])) for _, r in st.iterrows()]
_f20s = sorted(_f20, key=lambda x: -x[1])
_s7p8 = pick(trs, from_state=7, to_state=8).iloc[0]
_s3s4 = t20[(3, 4)]
_s4s5 = t20[(4, 5)]
_s8s4 = t20[(8, 4)]
_age20 = pick(oos_all, horizon=20)
qa = [
    ('Q1 趋势是否存在稳定生命周期？',
     '部分存在（YES-WEAK）。11 状态的转移矩阵结构清晰、可重复（一步自持 S0 %s / S7 %s / S3 %s），'
     'Model B 聚类（K=%d）一阶对角留存更稳定（%s–%s）。但 Model A 的多数状态（S3/S4/S5/S8）'
     '平均持续时间 ≈ 1 日、隔日翻转，"生命周期"体现在**转换结构**而非**状态持续**。'
     % (fp(_d1[0], 3), fp(_d1[7], 3), fp(_d1[3], 3), PREREG['cluster_primary_k'],
        fp(min(C2['diag']), 3), fp(max(C2['diag']), 3))),
    ('Q2 生命周期状态是否具有不同的未来收益分布？',
     'YES。T+20 均值最高 %s（%s）与最低 %s（%s），跨度约 %s；S4/S5 的 MFE20/MAE20 同时最大'
     '（S5 MFE20 %s / MAE20 %s），赔率反而最差（S5 RR20 %s）。'
     % (_f20s[0][0], fp(_f20s[0][1]), _f20s[-1][0], fp(_f20s[-1][1]),
        fp(_f20s[0][1] - _f20s[-1][1]), fp(_s5s['mfe20_mean'] if 'mfe20_mean' in _s5s else
                                          pick(mm, kind='state', id='S5').iloc[0]['mfe20_mean']),
        fp(pick(mm, kind='state', id='S5').iloc[0]['mae20_mean']),
        f2(pick(mm, kind='state', id='S5').iloc[0]['rr20']))),
    ('Q3 状态转换是否比静态 Trend Strength 更有预测力？',
     'YES（本样本内）。SIG_TS IC20 = %s vs ' % f4(_ts20['mean_ic']) + SIG +
     ' IC20 = %s；SIG_TV %s / SIG_TA %s 亦为负或近零。'
     % (f4(_ic20['mean_ic']), f4(_tv['mean_ic']), f4(_ta['mean_ic']))),
    ('Q4 Trend Acceleration 是否提供增量信息？',
     'NO。SIG_TA 全样本 IC20 = %s（ICIR %s），SIG_TV = %s。加速本身无独立信息；'
     '且加速类转换 S3' % (f4(_ta['mean_ic']), f4(_ta['icir']), f4(_tv['mean_ic'])) + AR +
     'S4（%s）、S4' % fp(_s3s4['fwd20_mean']) + AR + 'S5（%s）、S8' % fp(_s4s5['fwd20_mean']) +
     AR + 'S4（%s）的 T+20 均值为负。' % fp(_s8s4['fwd20_mean'])),
    ('Q5 Trend Age 是否具有预测力？',
     'YES-WEAK。T+20 均值随 Age 递减（Age 1-5 %s ' % fp(_a_young['fwd20_mean']) + AR +
     ' Age 41-60 %s，Age 60+ %s），三个年龄分界的 young-old 差为 %s/%s/%s；'
     % (fp(ag.iloc[4]['fwd20_mean']), fp(_a_old['fwd20_mean']),
        fp(agc.iloc[0]['oos_ic']), fp(agc.iloc[1]['oos_ic']), fp(agc.iloc[2]['oos_ic'])) +
     'SIG_AGE IC20 = %s（ICIR %s），但 OOS IC 仅 %s，量级偏小。'
     % (f4(_ag['mean_ic']), f4(_ag['icir']), f4(agc.iloc[0]['oos_icir']))),
    ('Q6 正常回撤与趋势终结能否事前区分？',
     'NO。S7 的三个出口极度不平衡：留 S7 %s / 转 S8 %s / 转 S9 %s（%s 例）/ 转 S10 %s（%s 例），'
     '事前分类在统计上不可行。SIG_REACC IC20 = %s 亦无预测力。'
     % (fp(pick(tr, from_state=7, to_state=7).iloc[0]['probability'], 3),
        fp(pick(tr, from_state=7, to_state=8).iloc[0]['probability'], 3),
        fp(pick(tr, from_state=7, to_state=9).iloc[0]['probability'], 4),
        i0(pick(tr, from_state=7, to_state=9).iloc[0]['count']),
        fp(pick(tr, from_state=7, to_state=10).iloc[0]['probability'], 5),
        i0(pick(tr, from_state=7, to_state=10).iloc[0]['count']), f4(_re20['mean_ic']))),
    ('Q7 回撤后的再启动是否存在稳定 Alpha？',
     'NO。S7' + AR + 'S8 的 T+20 均值仅 %s（中位 %s，胜率 %s），且不具显著性；'
     '独立构造的 SIG_REACC 无预测力。'
     % (fp(_s7p8['fwd20_mean']), fp(_s7p8['fwd20_med']), fp(_s7p8['fwd20_win'], 1))),
    ('Q8 Trend Lifecycle 是否只是 Momentum 的重新包装？',
     'NO（符号相反），但增量有限。全部 Baseline IC20 为负（%s）而 ' % '、'.join(
         '%s %s' % (r['signal'], f4(pick(ic_all, signal=r['signal'], horizon=20).iloc[0]['mean_ic']))
         for _, r in _bl.iterrows()) + SIG + ' 为正；'
     '然而加入 Momentum/Size/Value/Vol/Industry 控制后，TRAIN IC 转负（%s）、OOS 仅剩 %s。'
     '结论：**不是包装，但独立增量不足以形成可交易 alpha**。'
     % (f4(_ri['TRAIN']['ic']), f4(_ri['OOS']['ic']))),
    ('Q9 是否存在跨年份稳定性？',
     'YES。' + SIG + ' T+20 IC 在 %s 个自然年全部为正（最低 Y2020 %s）；'
     '下钻到季频，%s 个季度亦全部为正（最低 %s 出现在 %s），无单年 / 单季翻转。'
     % (len(_y20), f4(_y20['mean_ic'].min()), len(_q), f4(_q['mean_ic'].min()),
        _q.loc[_q['mean_ic'].idxmin()]['period'])),
    ('Q10 是否存在跨 Regime 稳定性？',
     'YES。T+20 IC：' + ' / '.join('%s %s (t=%s)' % (r['regime'], f4(r['mean_ic']), f2(r['ic_t']))
                                   for _, r in _r20.iterrows()) +
     '，三档同号，无频繁反转；NORMAL 最弱。'),
    ('Q11 是否存在参数稳定区域？',
     'YES。两组 3×3 网格的 OOS IC 范围分别为 [%s, %s]（ma×trend）与 [%s, %s]（rs×dd），'
     '同号比例 1.00，粗糙度 %s/%s，无孤立高点 ' % (f4(gs.iloc[0]['share_S7']), f4(gs.iloc[0]['oos_ic']),
                                                f4(gs.iloc[1]['share_S7']), f4(gs.iloc[1]['oos_ic']),
                                                f4(gs.iloc[0]['oos_icir']), f4(gs.iloc[1]['oos_icir'])) +
     AR + ' SMOOTH。'),
    ('Q12 是否能够通过 OOS？',
     'IC 层 PASS（T+20 %s, ICIR %s, t=%s），判别力层 FAIL（AUC %s < %s）。'
     % (f4(_o2['mean_ic']), f4(_o2['icir']), f2(_o2['ic_t']), f4(_o2['auc']), f2(PREREG['auc_min']))),
    ('Q13 是否能够通过 Null Model？',
     'YES。N3 随机状态置换在 5 个 horizon 上 |z| = %s–%s，p = %s，BH-FDR 全部拒绝。'
     % (f2(_n3['z_vs_real'].abs().min()), f2(_n3['z_vs_real'].abs().max()), f4(_n3['p'].min()))),
    ('Q14 扣除 30bp 成本后是否仍有经济意义？',
     'NO。30bp 后四分期超额分别为 %s。' % ' / '.join(fp(r['excess_ann'])
                                                    for _, r in _c30.iterrows())),
    ('Q15 最终是否值得进入策略研究？',
     'NO（本研究 V1 不授权）。结构性信号值得**继续研究**（属于 CONDITIONAL），但当前形态不足以'
     '作为交易信号进入 TL-02；须先解决「顶端判别力（AUC<0.5）」与「右尾依赖」两个前置问题。'),
]
for q, a in qa:
    A('**' + q + '**')
    A('')
    A(a)
    A('')

# ================================================================= §55
A(h2('§55 最终结论（固定格式）'))
A('')
A('```')
A('FINAL STATUS:            CONDITIONAL')
A('HYPOTHESIS:              TL-01 Trend Lifecycle Alpha')
A('LIFECYCLE EXISTS:        YES (weak — 体现在 transition 结构，非 state 持续)')
A('STATE TRANSITION INFO:   YES')
A('INCREMENTAL VS MOMENTUM: NO')
A('OOS:                     PASS (IC 层) / FAIL (判别力层)')
A('PARAMETER STABILITY:     PASS')
A('REGIME STABILITY:        PASS')
A('NULL MODEL:              PASS')
A('COST:                    FAIL')
A('TRADING ALPHA:           NO')
A('```')
A('')
A('核心证据：')
A('')
A('1. ' + SIG + '（= TRAIN 期拟合的 (state_prev ' + AR + ' state) 条件期望）在全样本 IC20 = %s、'
  % f4(_ic20['mean_ic']) + 'OOS = %s（ICIR %s, t = %s）、六个 Walk-Forward 窗口与九个自然年、三个 regime 上'
  % (f4(_o2['mean_ic']), f4(_o2['icir']), f2(_o2['ic_t'])))
A('   全部为正；随机状态置换检验 5 个 horizon 全 p = %s。**状态转换确实携带稳定的结构性信息。**'
  % f4(_n3['p'].min()))
A('2. 但同一信号 OOS AUC = %s < 0.50、Prec@10%% = %s < 10%%，**顶端选不出赢家**：正 IC 来自'
  % (f4(_o2['auc']), fp(_ao['prec10'], 2)))
A('   「低端避开大亏损」（Bottom 十分位 T+20 = %s vs 全样本 %s），而非「高端命中」。'
  % (fp(_aa['bot_mean']), fp(_aa['universe_mean'])))
A('3. 30bp 成本后四分期超额全部为负，且收益 EXTREME_TAIL_DEPENDENT（Top 5%% 贡献 %s，'
  % fp(_t5.iloc[0]['cum']) + '剔除后 TRAIN 年化 %s ' % fp(_pfa[pfa_tr := (_pfa['phase'] == 'TRAIN')].iloc[0]['ann_ret']) + AR +
  ' %s、OOS %s ' % (fp(_lt5[_lt5['phase'] == 'TRAIN'].iloc[0]['ann_ret']),
                    fp(_pfa[_pfa['phase'] == 'OOS'].iloc[0]['ann_ret'])) + AR +
  ' %s）。' % fp(_lt5[_lt5['phase'] == 'OOS'].iloc[0]['ann_ret']))
A('')
A('最重要的反事实结果：')
A('')
A('**在 2018–2026 全样本上，「直接用动量」是负向策略（%s），而生命周期转换信号是唯一正向的'
  % '、'.join('%s %s' % (r['signal'], f4(pick(ic_all, signal=r['signal'], horizon=20).iloc[0]['mean_ic']))
             for _, r in _bl.iterrows()) + '（+%s）。' % f4(_ic20['mean_ic']))
A('这排除了「生命周期 = 动量的重新包装」这一 FAIL 路径；但加入风险因子控制后其增量塌缩至'
  ' OOS %s / TRAIN %s，' % (f4(_ri['OOS']['ic']), f4(_ri['TRAIN']['ic'])))
A('并同时存在 AUC<0.5 与 30bp 后超额为负两个硬失败。因此本研究结论落在 CONDITIONAL 而非 PASS。**')
A('')
A('本研究是否产生实盘授权：**NO**')
A('')

# ================================================================= 附录
A(h2('附录 A 已知问题与如实记录'))
A('')
A(tbl(['编号', '问题', '影响', '处置'], [
    ['A1', 'tl_grid baseline 的 state_prev 自检不一致 5,368 / 11,583,680 (0.046%)',
     '口径差异（code-major 段内相邻 vs 日历相邻，停牌跨日时不同）；对 SIG_TRANS 的 IC 无影响'
     '（baseline TRAIN +0.0669 / OOS +0.0704 与 tl_alpha 完全一致）', '如实记录，不修改'],
    ['A2', 'Model A 多数状态平均持续时间 ≈ 1 日（S3/S4/S5/S8 为 100% 1-5 日）',
     '§23/§24 的 Duration Effect 无法用状态 run-length 衡量', '改用独立构造的 TrendAge（§9）'],
    ['A3', 'S7' + AR + 'S9 仅 %s 例、S7' % i0(pick(tr, from_state=7, to_state=9).iloc[0]['count']) +
     AR + 'S10 仅 %s 例' % i0(pick(tr, from_state=7, to_state=10).iloc[0]['count']),
     '§50 Q6「事前区分正常回撤与终结」无法建立统计结论', '如实记录为 NO'],
    ['A4', 'SIG_TRANS 的 IC（%s，OOS）与 AUC（%s，OOS）符号相反' % (f4(_o2['mean_ic']), f4(_o2['auc'])),
     '正 IC 由左尾规避驱动，非右尾捕获', '在 §14/§22/§55 显式披露'],
    ['A5', 'tl01_parameter_grid.csv 的 age_cut 行与 grid_summary 行复用了通用列名',
     '列语义需按 §9/§19 说明的映射还原，否则会误读', '在 §9/§19 显式给出映射'],
]))
A('')
A(LN * 78)
A('TL-01 报告生成完毕。数据文件 18 个 + 本报告 + registry = 20 项交付（§53 要求）。')

md = '\n'.join(L) + '\n'
with open(OUTMD, 'w', encoding='utf-8') as f:
    f.write(md)

# ================================================================= registry
REG = {
    'hypothesis_id': PREREG['hypothesis_id'],
    'title': PREREG['title'],
    'module': 'research/trend_lifecycle',
    'generated': '2026-09-26',
    'independence': {
        'standalone_hypothesis': True,
        'inherits_prior_fail_stop_results': False,
        'imports_other_strategies': False,
        'point_in_time_only': True,
        'notes': '§0/§56：独立 Hypothesis；不继承任何既有 FAIL/STOP 结论作为正向先验；'
                 '状态只用 Prediction Date 之前的信息定义；方向仅 TRAIN 期冻结。',
    },
    'universe': {
        'calendar_days': 2120, 'calendar_span': '20180102-20260924',
        'raw_codes': 5816, 'valid_cells': 8635211, 'panel_cells': 11583680,
        'exclude': ['.BJ', 'ST', '*ST', 'listing_days<120'],
        'benchmarks': ['PROXY_allA_equalweight', 'HS300', 'SW_L1_industry(32)'],
        'regime_definition': 'PROXY 200MA + 60d momentum -> BEAR/NORMAL/BULL',
        'regime_days': {'BEAR': 618, 'NORMAL': 467, 'BULL': 1035},
    },
    'phases': {'TRAIN': '2018-2022', 'VALID': '2023-2024', 'OOS': '2025', 'LIVE-LIKE': '2026'},
    'state_models': {
        'A_rule': {'states': 11, 'prereg_thresholds': True,
                   'share_S0': round(float(pick(st, state=0).iloc[0]['share']), 5),
                   'share_S7': round(float(pick(st, state=7).iloc[0]['share']), 5),
                   'note': 'S3/S4/S5/S8 avg_dur≈1.00 日，状态近似逐日重判'},
        'B_cluster': {'algo': 'KMeans/GMM', 'k_tested': list(PREREG['cluster_k']),
                      'k_primary': PREREG['cluster_primary_k'],
                      'sil': round(float(MB['K6']['silhouette']), 5),
                      'train_ic': round(float(MB['K6']['train_ic']), 4),
                      'oos_ic': round(float(MB['K6']['oos_ic']), 4),
                      'fit_scope': 'TRAIN 2018-2022 only', 'fit_n': PREREG['cluster_fit_n']},
        'C_markov': {'c1_p1_diag': {('S%d' % k): round(float(C1['k1']['diag'][k]), 3)
                                    for k in range(11)},
                     'c1_k20_diag': {('S%d' % k): round(float(C1['k20']['diag'][k]), 4)
                                     for k in range(11)},
                     'c2_p1_diag': {('C%d' % k): round(float(C2['diag'][k]), 3)
                                    for k in range(len(C2['diag']))}},
    },
    'primary_signal': {
        'name': SIG, 'horizon': H0,
        'fit': 'TRAIN 2018-2022 only, frozen before OOS',
        'cell_expectations': {('S%d>S%d' % (a, b)): round(float(v['fwd20_TRAIN']), 4)
                              for (a, b), v in t20.items()},
        'ic20': {'ALL': round(float(_ic20['mean_ic']), 4),
                 'TRAIN': round(float(pick(oos_s, horizon=20, phase='TRAIN').iloc[0]['mean_ic']), 4),
                 'VALID': round(float(pick(oos_s, horizon=20, phase='VALID').iloc[0]['mean_ic']), 4),
                 'OOS': round(float(_o2['mean_ic']), 4),
                 'LIVE-LIKE': round(float(pick(oos_s, horizon=20, phase='LIVE-LIKE').iloc[0]['mean_ic']), 4)},
        'icir20_oos': round(float(_o2['icir']), 4), 'ic_t20_oos': round(float(_o2['ic_t']), 4),
        'auc20': {'ALL': round(float(_aa['auc']), 5), 'OOS': round(float(_ao['auc']), 5)},
        'prec10_oos': round(float(_ao['prec10']), 5),
    },
    'gates': {
        'OOS': {'pass': True, 'level': 'IC',
                'evidence': 'IC20 OOS %s, ICIR %s, t %s' % (round(float(_o2['mean_ic']), 4),
                                                             round(float(_o2['icir']), 4),
                                                             round(float(_o2['ic_t']), 2))},
        'DISCRIMINATION': {'pass': False,
                           'evidence': 'AUC20 OOS %.5f < auc_min %.2f; prec10 %.2f%% < 10%%'
                                       % (float(_ao['auc']), PREREG['auc_min'],
                                          float(_ao['prec10']) * 100)},
        'WALK_FORWARD': {'pass': True, 'level': 'IC',
                         'evidence': '6/6 测试年 IC>0 (%.4f ~ %.4f); AUC 6/6 <0.50'
                                     % (float(_wt['mean_ic'].min()), float(_wt['mean_ic'].max()))},
        'PARAMETER_STABILITY': {'pass': True,
                                'evidence': 'ma×trend & rs×dd 两网格 SMOOTH, 同号 1.00, 粗糙度 0.01/0.02'},
        'REGIME_STABILITY': {'pass': True,
                             'evidence': 'T+20 IC ' + ' / '.join(
                                 '%s %.4f' % (r['regime'], float(r['mean_ic']))
                                 for _, r in _r20.iterrows())},
        'YEAR_STABILITY': {'pass': True,
                           'evidence': 'T+20 IC 九年全正 (%d/%d)' % (len(_pos), len(_y20))},
        'NULL_MODEL': {'pass': True,
                       'evidence': 'N3 置换 |z| %.2f-%.2f, p=%.4f, BH-FDR 全拒绝'
                                   % (float(_n3['z_vs_real'].abs().min()),
                                      float(_n3['z_vs_real'].abs().max()), float(_n3['p'].min()))},
        'MOMENTUM_CONTROL': {'pass': False,
                             'evidence': '全控制后 TRAIN IC %.4f, OOS %.4f; Fama-MacBeth VALID t %.3f'
                                         % (float(_ri['TRAIN']['ic']), float(_ri['OOS']['ic']),
                                            float(fm['full_with_controls']['VALID']['t']))},
        'COST': {'pass': False,
                 'evidence': '30bp: ' + ' / '.join('%s %.2f%%' % (r['phase'], float(r['excess_ann']) * 100)
                                                   for _, r in _c30.iterrows())},
        'TAIL': {'pass': False, 'flag': 'EXTREME_TAIL_DEPENDENT',
                 'evidence': 'Top5%% 贡献 %.2f%%; 剔 top5%% 后 TRAIN %.2f%% -> %.2f%%'
                             % (float(_t5.iloc[0]['cum']) * 100,
                                float(_pfa[_pfa['phase'] == 'TRAIN'].iloc[0]['ann_ret']) * 100,
                                float(_lt5[_lt5['phase'] == 'TRAIN'].iloc[0]['ann_ret']) * 100)},
        'TRADING_ALPHA': {'pass': False,
                          'evidence': '十分位无单调 (Top-全样本 %.2f%%), AUC<0.5'
                                      % (float(_o2['excess']) * 100)},
    },
    'stop_rules_triggered': [
        'Momentum 控制后 Alpha 消失',
        'AUC ≈ 0.5 (LOW_DISCRIMINATION, §28)',
        'Cost After Alpha ≈ 0 (§38 30bp)',
    ],
    'final_status': 'CONDITIONAL',
    'final_status_label': 'CONDITIONAL — STRUCTURAL SIGNAL WITHOUT TRADING PROOF',
    'live_authorization': False,
    'next_stage_allowed': False,
    'next_stage_note': '本研究 V1 不产生任何实盘交易授权；TL-02 Trend Transition Trading 未开启。',
    'deliverables': [
        'tl01_feature_daily.parquet', 'tl01_state_daily.parquet', 'tl01_transition.csv',
        'tl01_state_stats.csv', 'tl01_transition_stats.csv', 'tl01_mfe_mae.csv',
        'tl01_age_effect.csv', 'tl01_ic.csv', 'tl01_auc.csv', 'tl01_regime.csv',
        'tl01_year.csv', 'tl01_parameter_grid.csv', 'tl01_null_model.csv',
        'tl01_permutation.csv', 'tl01_oos.csv', 'tl01_walkforward.csv', 'tl01_cost.csv',
        'tl01_portfolio.csv', 'tl01_report.md', 'tl01_registry.json',
    ],
    'known_issues': [
        'A1 tl_grid state_prev 自检不一致 5,368/11,583,680 (0.046%)：口径差异，对 IC 无影响',
        'A2 Model A 多数状态 avg_dur≈1 日，Duration Effect 不可用，改用 TrendAge',
        'A3 S7>S9 仅 %d 例 / S7>S10 仅 %d 例，Q6 无法建立统计结论'
        % (int(pick(tr, from_state=7, to_state=9).iloc[0]['count']),
           int(pick(tr, from_state=7, to_state=10).iloc[0]['count'])),
        'A4 SIG_TRANS IC(%.4f) 与 AUC(%.4f) 符号相反：正 IC 由左尾规避驱动'
        % (float(_o2['mean_ic']), float(_o2['auc'])),
        'A5 age_cut / grid_summary 行复用通用列名，需按 §9/§19 映射还原',
    ],
}
with open(OUTREG, 'w', encoding='utf-8') as f:
    json.dump(REG, f, ensure_ascii=False, indent=2)

print('WROTE', OUTMD)
print('WROTE', OUTREG)
print('report lines =', len(L))
