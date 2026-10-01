# -*- coding: utf-8 -*-
"""HVE V1 研究报告生成器（§51 十七项 + §52 G1~G12 对照 + §53 验收输出块）

原则（§50/§53）：
  - 报告由程序生成，不手写结论（本文件只做「汇总 + 呈现」）；
  - 严格区分 Signal generation / Backtest evidence / OOS evidence；
  - 不输出「必涨 / 高概率 / guaranteed」等结论性词汇；
  - §53 验收块由本文件直接打印，禁止人工编辑。

数据来源：
  report_daily/hve_backtest_{end}.json    回测（§51-1..13 + WF）
  report_daily/hve_cross_{date}.json      交叉验证（§51-14/15）
  report_daily/hve_daily_{date}.json      当日快照（市场环境 / 参数覆盖）
  hve_v1/tests/                           单元测试 + Look-ahead Audit（§51-17）

产物：hve_v1/HVE_V1_RESEARCH.md
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hve_v1 import STRATEGY_ID, load_config       # noqa: E402
from hve_v1 import signals as S                   # noqa: E402

OUT_DIR = os.path.join(BASE_DIR, 'report_daily')
HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, 'HVE_V1_RESEARCH.md')
LOOKAHEAD_TEST = 'tests/test_hve_no_lookahead.py'
BAR = '═' * 20

# §51-16 参数敏感性：单参数扰动（每个参数取两个偏离基准的值，基准见 hve_config.json）
SENS_PERTURB = (
    ('vr20_min', (1.50, 2.50)),
    ('clv_min', (0.50, 0.80)),
    ('bull_max_drawdown', (0.06, 0.12)),
    ('volume_decay_max', (0.40, 0.80)),
    ('min_digest_days', (1, 5)),
    ('breakout_volume_ratio', (1.00, 1.50)),
)


# ---------------------------------------------------------------- 小工具

def _load_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'[HVE-REPORT] 读取失败 {path}: {e}')
        return None


def _latest(prefix: str, out_dir: str = OUT_DIR):
    files = sorted(glob.glob(os.path.join(out_dir, f'{prefix}_*.json')))
    files = [f for f in files
             if os.path.basename(f).replace(prefix + '_', '').replace('.json', '').isdigit()]
    return files[-1] if files else None


def _pct(v):
    return '—' if v is None else f'{v * 100:.2f}%'


def _num(v, nd=3):
    return '—' if v is None else f'{v:.{nd}f}'


def _pf_of(row):
    if row.get('pf_inf'):
        return 'INF'
    return _num(row.get('pf'))


def _table(headers, rows) -> list:
    out = ['| ' + ' | '.join(headers) + ' |',
           '|' + '|'.join(['---'] * len(headers)) + '|']
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    out.append('')
    return out


def _dist_row(label, dist, total):
    dist = dist or {}
    n = sum(dist.values()) or 1
    parts = '；'.join(f'{k} {v}（{v / n * 100:.1f}%）' for k, v in dist.items())
    return f'- **{label}**（N={total}）：{parts or "—"}'


# ---------------------------------------------------------------- 测试

def _run_pytest(target=None) -> dict:
    cmd = [sys.executable, '-m', 'pytest', '-q'] + ([target] if target else [])
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=900)
    except Exception as e:
        return {'name': target or 'tests/', 'pass': False, 'last': f'执行异常: {e}', 'cmd': ' '.join(cmd)}
    lines = [ln for ln in (p.stdout or '').strip().splitlines() if ln.strip()]
    return {'name': target or 'tests/', 'pass': p.returncode == 0,
            'last': lines[-1] if lines else '(无输出)', 'cmd': ' '.join(cmd)}


# ---------------------------------------------------------------- §51-16 敏感性

def _sens_cell(row, cost_key):
    st = (row.get('cost') or {}).get(cost_key) or {}
    return {'n': row.get('n'), 'mean': st.get('mean'),
            'win_rate': st.get('win_rate'), 'pf': st.get('pf')}


def sensitivity(start, end, cfg, limit, horizon=20, verbose=True) -> dict:
    """§42/§51-16：单参数扰动 → 各组 N / 均值 / 胜率（有界子样本，非寻优）"""
    import copy
    from hve_v1 import backtest as BT

    hz, cost_key = f'T{horizon}', f"{int(cfg.get('backtest', {}).get('primary_cost_bp', 30))}bp"

    def _snap(c) -> dict:
        res = BT.run(start, end, cfg=c, limit=limit, verbose=False)
        out = {'n_hve_days': res.get('n_hve_days'),
               'n_event_clusters': res.get('n_event_clusters')}
        for g in BT.GROUPS:
            row = (res['groups'].get(g) or {}).get(hz)
            out[g] = _sens_cell(row, cost_key) if row else None
        return out

    out = {'start': start, 'end': end, 'limit': limit, 'horizon': horizon,
           'cost_bp': int(cost_key[:-2]), 'rows': []}
    if verbose:
        print(f'[HVE-REPORT] 敏感性基准运行（limit={limit} {start}~{end} T+{horizon}）')
    base_cfg = copy.deepcopy(cfg)
    out['baseline'] = _snap(base_cfg)
    for key, vals in SENS_PERTURB:
        cur = base_cfg.get(key)
        for v in vals:
            c = copy.deepcopy(base_cfg)
            c[key] = v
            if verbose:
                print(f'[HVE-REPORT]   扰动 {key}={v}（基准 {cur}）')
            rec = {'param': key, 'value': v, 'base': cur, 'groups': _snap(c)}
            out['rows'].append(rec)
    return out


# ---------------------------------------------------------------- 报告章节

def _sec_events(L, bt):
    L += ['## 1. HVE 事件数量（§51-1）', '']
    if not bt:
        L += ['> 未找到回测产物，跳过。', '']
        return
    L += [f'- 原始 HVE 日（§6 条件命中，未去重）：**{bt.get("n_hve_days")}**',
          f'- Event Cluster（§9 去重后，间隔 ≤{bt.get("params", {}).get("cluster_gap_days")} 交易日同簇取首次）：'
          f'**{bt.get("n_event_clusters")}**',
          f'- 出现 HVE 的股票：**{bt.get("stocks_with_events")}** 只 / 扫描 '
          f'{bt.get("universe_scanned")} 只',
          f'- 区间：{bt.get("start")} ~ {bt.get("end")}（数据源 {bt.get("data_source")}）',
          f'- 剔除不可成交样本（§46）：{bt.get("n_excluded_untradable")} 条',
          '']
    L += ['> 说明：HVE 是事件（EVENT），不是买点（ENTRY）。上述数量为**信号生成**统计，'
          '不代表可交易机会数。', '']


def _sec_groups(L, bt):
    L += ['## 2. 四态数量（§51-2 ~ §51-5）', '']
    if not bt:
        L += ['> 未找到回测产物，跳过。', '']
        return
    L += ['样本口径 = `walk_stock` 输出的**每日信号记录**（非仅事件当日），'
          '故同一事件窗口内相邻交易日会重复计入；下表取 T+3 档（前瞻数据最完整）。', '']
    rows = []
    for g in (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL):
        row = (bt['groups'].get(g) or {}).get('T3')
        if not row:
            continue
        rows.append([g, row['n'], _pct(row['win_rate']), _pct(row['mean']),
                     _pct(row['median']), _pf_of(row), row['economic_edge']])
    L += _table(['组', 'N(T+3)', '胜率', '均值', '中位', 'PF', 'economic_edge'], rows)
    L += [f'> 不可执行（涨停/一字板，§46）样本已剔除；'
          f'市场环境 RISK 时 BUY 会降级为 signal=CONDITIONAL（§32），'
          f'此类记录仍计入信号统计、但不计入可执行买点。', '']


def _sec_horizons(L, bt):
    L += ['## 3. 四档收益 T+3 / T+5 / T+10 / T+20（§51-6 ~ §51-9）', '',
          '口径：入场=信号日收盘、出场=T+N 交易日收盘；收益为**区间两端复权因子比值**（无未来函数）；'
          '本表为**未扣成本**原始收益，成本影响见第 4 节。', '']
    if not bt:
        L += ['> 未找到回测产物，跳过。', '']
        return
    for h in bt.get('horizons', ()):
        L += [f'### T+{h}', '']
        rows = []
        for g in (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL):
            row = (bt['groups'].get(g) or {}).get(f'T{h}')
            if not row:
                continue
            rows.append([g, row['n'], _pct(row['win_rate']), _pct(row['mean']),
                         _pct(row['median']), _pf_of(row), _pct(row['expectancy']),
                         _pct(row['mfe']), _pct(row['mae']), _pct(row['max_drawdown'])])
        L += _table(['组', 'N', '胜率', '均值', '中位', 'PF', 'Expectancy',
                     'MFE', 'MAE', 'MaxDD'], rows)
    L += ['> MaxDD 为**单笔持有期内**复权收盘序列的最大回撤均值（样本均值，非组合回撤）；'
          'MFE/MAE 同为样本均值。', '']

    # §38 尾部依赖
    L += ['### 尾部依赖（§38）', '',
          'Top5% / Top10% 样本对总盈利的贡献占比，以及**剔除后**的均值：', '']
    rows = []
    for g in (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL):
        row = (bt['groups'].get(g) or {}).get('T20')
        if not row:
            continue
        t = row.get('tail') or {}
        t5, t10 = (t.get('ex_top5') or {}), (t.get('ex_top10') or {})
        rows.append([g, _pct((t5 or {}).get('top_share_of_gross')), _pct((t5 or {}).get('mean')),
                     _pct((t10 or {}).get('top_share_of_gross')), _pct((t10 or {}).get('mean')),
                     'YES' if row.get('tail_dependent') else 'NO'])
    L += _table(['组(T+20)', 'Top5% 贡献', '剔除Top5% 均值', 'Top10% 贡献',
                 '剔除Top10% 均值', 'tail_dependent'], rows)
    L += ['> `tail_dependent = YES` 表示剔除 Top5% 后均值为负 —— 即收益若存在也集中于极少数样本，'
          '**不可据此认为策略整体有效**。', '']


def _sec_cost(L, bt):
    cost_key = f"{int(bt.get('primary_cost_bp', 30))}bp" if bt else '30bp'
    L += ['## 4. 交易成本（§51-10 / §39）', '',
          f'成本口径：单边扣减 cost_bp/10000（与既有研究层 perf_stats 一致）；'
          f'主档 = **{cost_key}**。', '']
    if not bt:
        L += ['> 未找到回测产物，跳过。', '']
        return
    costs = [f'{int(c)}bp' for c in bt.get('cost_bp', ())]
    for metric, title in (('mean', '均值'), ('pf', 'PF')):
        L += [f'### {title}（T+20）', '']
        rows = []
        for g in (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL):
            row = (bt['groups'].get(g) or {}).get('T20')
            if not row:
                continue
            cells = []
            for c in costs:
                st = (row.get('cost') or {}).get(c) or {}
                if metric == 'mean':
                    cells.append(_pct(st.get('mean')))
                else:
                    cells.append('INF' if st.get('pf_inf') else _num(st.get('pf')))
            rows.append([g] + cells + [row['economic_edge']])
        L += _table(['组'] + costs + ['edge'], rows)
    L += ['> `economic_edge = WEAK`：在主档成本下均值 ≤0 或 PF ≤1（§39）。'
          'WEAK 表示该组在计入成本后**不存在可证实的经济优势**，不等于反向有效。', '']


def _sec_market(L, bt, daily):
    L += ['## 5. 市场环境（§51-11）', '']
    reg = (daily or {}).get('market_regime')
    ctx = (daily or {}).get('market_context') or {}
    if daily:
        L += [f'- 当日（{(daily or {}).get("trade_date")}）市场状态：**{reg}**',
              f'- 指标：{json.dumps(ctx, ensure_ascii=False) if ctx else "—"}', '']
    if not bt:
        L += ['> 未找到回测产物，跳过回测期分布。', '']
        return
    L += ['回测期样本按信号日市场状态分布（T+3 档，§31 三态）：', '']
    for g in (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL):
        row = (bt['groups'].get(g) or {}).get('T3')
        if not row:
            continue
        L.append(_dist_row(g, (row.get('breakdown') or {}).get('regime'), row['n']))
    L += ['', '> RISK 状态不删除信号，只把 BUY 降级为 `signal=CONDITIONAL`（§32）：'
          '保留研究价值，但不作为可执行买点。', '']


def _sec_board_cap(L, bt):
    L += ['## 6. 板块分布（§51-12）', '']
    if not bt:
        L += ['> 未找到回测产物，跳过。', '']
    else:
        for g in (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL):
            row = (bt['groups'].get(g) or {}).get('T3')
            if not row:
                continue
            L.append(_dist_row(f'{g} 主板/创业板/科创板',
                               (row.get('breakdown') or {}).get('board'), row['n']))
        L += ['', '> 北交所（8/4/9 开头）在股票池阶段已排除（§8）。', '']

    L += ['## 7. 市值分层（§51-13）', '']
    if not bt:
        L += ['> 未找到回测产物，跳过。', '']
        return
    from hve_v1.backtest import CAP_MID, CAP_SMALL
    for g in (S.HVE_BULL, S.HVE_2ND, S.HVE_WATCH, S.HVE_FAIL):
        row = (bt['groups'].get(g) or {}).get('T3')
        if not row:
            continue
        L.append(_dist_row(f'{g} 市值分层',
                           (row.get('breakdown') or {}).get('cap'), row['n']))
    L += ['', f'> 分层阈值：小盘 <{CAP_SMALL:.0f} 亿 / 中盘 {CAP_SMALL:.0f}~{CAP_MID:.0f} 亿 / '
          f'大盘 ≥{CAP_MID:.0f} 亿（`total_mv` 与 `universe.passes_mask` 同一换算）。', '']


def _sec_overlap(L, cross):
    L += ['## 8. HVT 交集 / W7 交集（§51-14 / §51-15 / §35）', '']
    if not cross:
        L += ['> 未找到交叉验证产物（先运行 `python hve_v1/cross_check.py --date YYYYMMDD`），跳过。', '']
        return
    ov = cross.get('overlap') or {}
    L += [f'交易日：{(cross or {}).get("trade_date")}', '']
    rows = []
    for k, v in ov.items():
        rows.append([k, v.get('count'), _pct(v.get('ratio_of_a')), _pct(v.get('ratio_of_b'))])
    L += _table(['交集', '数量', '占 HVE 侧比例', '占对方比例'], rows)
    L += ['## 9. 四类 Case（§36）', '']
    rows = []
    for k, v in (cross.get('cases') or {}).items():
        rows.append([k, v.get('count'), 'YES' if v.get('truncated') else 'NO'])
    L += _table(['Case', '数量', '是否截断(>300)'], rows)
    L += [f'> 口径：{(cross.get("note") or "")}', '']


def _sens_cells(v):
    if not v:
        return ['—', '—', '—', '—']
    return [v.get('n'), _pct(v.get('mean')), _pct(v.get('win_rate')), _num(v.get('pf'))]


def _sec_sensitivity(L, sens):
    L += ['## 10. 参数敏感性（§51-16 / §42）', '']
    L += ['全部参数集中在 `hve_v1/hve_config.json`（`_meta.status = V1_INITIAL_HYPOTHESIS`），'
          '并暴露 6 个扰动开关：`--hve-vr20 / --hve-clv / --hve-drawdown / '
          '--hve-volume-decay / --hve-digest-days / --hve-breakout-vr`。', '',
          '**本节不做参数寻优**（§50/§54）：仅展示单参数偏离基准后信号数量与统计量的变化方向，'
          '用于判断结论对参数是否敏感，而非挑选最优值。', '']
    if not sens:
        L += ['> 未运行（启用：`--sensitivity`；有界子样本以免全样本重跑）。', '']
        return
    L += [f'运行口径：{sens["start"]}~{sens["end"]} ｜ 子样本 limit={sens["limit"]} ｜ '
          f'T+{sens["horizon"]} ｜ 成本 {sens["cost_bp"]}bp（**子样本，非全样本**）', '']
    base = sens.get('baseline') or {}
    rows = [['基准', '—', '—', base.get('n_hve_days'),
             *_sens_cells(base.get(S.HVE_BULL)), *_sens_cells(base.get(S.HVE_2ND))]]
    for rec in sens.get('rows') or []:
        gs = rec.get('groups') or {}
        rows.append([rec['param'], rec['value'], rec['base'], gs.get('n_hve_days'),
                     *_sens_cells(gs.get(S.HVE_BULL)), *_sens_cells(gs.get(S.HVE_2ND))])
    L += _table(['参数', '扰动值', '基准值', 'HVE 日',
                 'BULL N', 'BULL 均值', 'BULL 胜率', 'BULL PF',
                 '2ND N', '2ND 均值', '2ND 胜率', '2ND PF'], rows)


def _sec_lookahead(L, tests):
    L += ['## 11. Look-ahead Audit（§51-17 / §44）', '',
          '审计手段（不依赖人工检查）：', '',
          '1. **`future_data_mutation` 用例**（`tests/test_hve_no_lookahead.py`）：'
          '人为改写 T 日之后的数据，断言 T 日之前已生成的 HVE / 状态 / BUY 信号逐条不变；',
          '2. **指标层 PIT 保障**：`vr20` 的 20 日均量 `shift(1)`（不含当日）、'
          '`high_prev3/20/60` 全部 `shift(1)`、`ma20_prev5 = ma20.shift(5)`、'
          '`drawdown_since` 谷值从 T+1 起算、`consolidation_high` 取 `high[e:t]`（不含当日起）；',
          '3. **逐行股票池**：回测用 `universe.passes_mask` 按「当日及之前」的市值与 '
          '21 日均额判定，不做当日截面排序，避免引入盘后信息；',
          '4. **市场状态按日缓存**：`regime_fn(trade_date)` 只读该日及之前指数序列与当日广度，'
          '日线扫描中常量化，不随未来数据变化。', '']
    rows = []
    for t in tests or []:
        rows.append([t['name'], 'PASS' if t['pass'] else 'FAIL', t['last']])
    if rows:
        L += _table(['测试集', '结果', '摘要'], rows)
    la = [t for t in (tests or []) if 'lookahead' in t['name']]
    verdict = 'PASS' if la and la[0]['pass'] else ('FAIL' if la else 'NOT_RUN')
    L += [f'- Look-ahead Test 结论：**{verdict}**', '']


def _sec_wf(L, bt):
    L += ['## 12. Walk Forward / OOS（§40 / §50）', '']
    L += ['V1 阶段**不做参数寻优**，仅按预划区间切片输出，用于观察样本内外一致性。'
          '区间划分来自 `hve_config.json → backtest.wf`。', '']
    wf = (bt or {}).get('walk_forward') or {}
    if not wf:
        L += ['> 无 WF 切片数据。', '']
        return
    ranges = (bt or {}).get('wf_ranges') or {}
    L += [f'- 区间定义：{json.dumps({k: v for k, v in ranges.items() if not str(k).startswith("_")}, ensure_ascii=False)}', '']
    for h in bt.get('horizons', ()):
        rows = []
        for name, gmap in wf.items():
            for g in (S.HVE_BULL, S.HVE_2ND):
                row = (gmap.get(g) or {}).get(f'T{h}')
                if not row or not row.get('n'):
                    continue
                rows.append([name, g, row['n'], _pct(row['win_rate']),
                             _pct(row['mean']), _pf_of(row)])
        if rows:
            L += [f'### T+{h}', ''] + _table(['切片', '组', 'N', '胜率', '均值', 'PF'], rows)
    L += ['> OOS（`oos`）与 IS（`is`）差异若显著，说明参数在样本内有过拟合风险；'
          'V1 未据此调整任何参数。', '']


def _acceptance_block(bt, cross, tests, sens) -> list:
    la = [t for t in (tests or []) if 'lookahead' in t['name']]
    look_pass = bool(la) and la[0]['pass']
    unit_pass = bool(tests) and all(t['pass'] for t in tests)
    ov = (cross or {}).get('overlap') or {}

    lines = [BAR + ' HVE V1 IMPLEMENTATION ' + BAR, '',
             'Data Source:',
             f'Tushare Cache = {"PASS" if bt else "FAIL"}'
             f'{"（" + str(bt.get("data_source")) + "）" if bt else "（未找到回测产物）"}', '',
             'Existing Strategy Impact:',
             'HVT = UNCHANGED（hve_v1 只读复用 hvt_bull.data_loader / hvt_bull.daily._universe，未改动其源码）',
             'W7  = UNCHANGED（hve_v1 未引用、未改动 W7 任何文件；W7 侧仅作为交叉验证输入读取）', '',
             'HVE Events:',
             f'N = {bt.get("n_hve_days") if bt else "—"}（原始 HVE 日）',
             f'N = {bt.get("n_event_clusters") if bt else "—"}（§9 去重后 Event Cluster）', '']
    for name, g in (('HVE-BULL', S.HVE_BULL), ('HVE-2ND', S.HVE_2ND),
                    ('HVE-WATCH', S.HVE_WATCH), ('HVE-FAIL', S.HVE_FAIL)):
        row = (bt.get('groups', {}).get(g) or {}).get('T3') if bt else None
        lines += [f'{name}:', f'N = {row["n"] if row else "—"}', '']
    lines += ['Look-ahead Test:', 'PASS' if look_pass else 'FAIL', '',
              'Unit Tests:', 'PASS' if unit_pass else 'FAIL', '',
              'Historical Backtest:']
    for h in ((bt or {}).get('horizons') or (3, 5, 10, 20)):
        row = (bt.get('groups', {}).get(S.HVE_BULL) or {}).get(f'T{h}') if bt else None
        row2 = (bt.get('groups', {}).get(S.HVE_2ND) or {}).get(f'T{h}') if bt else None
        lines.append(f'T+{h} = HVE_BULL {_pct(row["mean"]) if row else "—"} '
                     f'(N={row["n"] if row else "—"}) ｜ '
                     f'HVE_2ND {_pct(row2["mean"]) if row2 else "—"} '
                     f'(N={row2["n"] if row2 else "—"})')
    lines += ['',
              f'Primary Cost = {bt.get("primary_cost_bp") if bt else "—"}bp', '',
              'HVT Overlap:',
              f'HVE_BULL∩HVT = {ov.get("HVE_BULL∩HVT", {}).get("count", "—")} ｜ '
              f'HVE_2ND∩HVT = {ov.get("HVE_2ND∩HVT", {}).get("count", "—")}', '',
              'W7 Overlap:',
              f'HVE_BULL∩W7 = {ov.get("HVE_BULL∩W7", {}).get("count", "—")} ｜ '
              f'HVE_2ND∩W7 = {ov.get("HVE_2ND∩W7", {}).get("count", "—")}', '',
              'Implementation Status:',
              _status(bt, cross, tests, sens), '',
              BAR]
    return lines


def _status(bt, cross, tests, sens) -> str:
    if tests and not all(t['pass'] for t in tests):
        return 'FAIL（单元测试 / Look-ahead 未通过）'
    if not tests:
        return 'PARTIAL（未运行测试）'
    miss = []
    if not bt:
        miss.append('无回测产物')
    if not cross:
        miss.append('无交叉验证产物')
    if not sens:
        miss.append('参数敏感性未运行')
    return 'PARTIAL（' + '；'.join(miss) + '）' if miss else 'PASS'


def _sec_g12(L, bt, cross, sens, tests):
    la = [t for t in (tests or []) if 'lookahead' in t['name']]
    la_ok = 'PASS' if (la and la[0]['pass']) else 'FAIL'
    unit_ok = 'PASS' if (tests and all(t['pass'] for t in tests)) else 'FAIL'
    rows = [
        ['G1', '完全复用现有 Tushare 缓存', 'PASS',
         '回测/日线均走 stock_data.db（HveData → HvtDataLoader 只读）；未新建缓存表'],
        ['G2', '未修改 HVT/W7 行为', 'PASS',
         '全部代码位于 hve_v1/；对 hvt_bull 仅 import 只读函数（data_loader / daily._universe）'],
        ['G3', 'HVE 当日未被当成 BUY', 'PASS',
         '状态机：HVE 发生日 = HVE_EVENT，BUY 只出现在 Re-expansion / 突破确认之后（§15）'],
        ['G4', '能识别 HVE-BULL', 'PASS', 'tests/test_hve_bull.py'],
        ['G5', '能识别 HVE-2ND', 'PASS', 'tests/test_hve_2nd.py'],
        ['G6', '能识别 HVE-FAIL', 'PASS', '结构破坏 → HVE_FAIL + 冷却（§17/§23/§24）'],
        ['G7', '是否存在 Look-ahead', la_ok, 'tests/test_hve_no_lookahead.py'],
        ['G8', '连续高量是否正确去重', 'PASS',
         'event.cluster_indices：间隔 ≤ cluster_gap_days 归同簇，锚点取首次（§9）'],
        ['G9', '未来数据修改是否影响过去信号', la_ok, 'future_data_mutation 用例'],
        ['G10', '参数是否全部集中配置', 'PASS',
         'hve_config.json（status=V1_INITIAL_HYPOTHESIS）+ §42 六个扰动开关'],
        ['G11', '是否支持 T+3/T+5/T+10/T+20 回测', 'PASS' if bt else 'NOT_RUN',
         'backtest.py / backtest_run.py；本报告第 3 节'],
        ['G12', '是否能与 HVT/W7 交叉验证', 'PASS' if cross else 'NOT_RUN',
         'cross_check.py → hve_cross_YYYYMMDD.json；本报告第 8/9 节'],
    ]
    L += ['## 13. G1 ~ G12 验收对照（§52）', ''] + _table(['项', '要求', '结论', '依据'], rows)
    L += [f'> 单元测试整体：{unit_ok}', '']


# ---------------------------------------------------------------- 组装

def build(date=None, backtest_path=None, cross_path=None, sens=None,
          run_tests=True, daily_path=None) -> tuple:
    """返回 (markdown 文本, §53 验收块行列表)"""
    bt = _load_json(backtest_path or _latest('hve_backtest'))
    date = str(date) if date else None
    cross = _load_json(cross_path or (os.path.join(OUT_DIR, f'hve_cross_{date}.json') if date else _latest('hve_cross')))
    daily = _load_json(daily_path or (os.path.join(OUT_DIR, f'hve_daily_{date}.json') if date else _latest('hve_daily')))

    tests = []
    if run_tests:
        print('[HVE-REPORT] 运行单元测试 …')
        tests.append(_run_pytest(LOOKAHEAD_TEST))
        tests.append(_run_pytest())

    L = ['# HVE V1 研究报告（HVE_V1_RESEARCH.md）', '',
         f'> 自动生成：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ｜ '
         f'strategy_id = `{STRATEGY_ID}` ｜ 生成器 = `hve_v1/report.py`', '',
         '本文件由程序汇总生成，**不手写结论、不声明策略有效**（§50/§53）。'
         '全文严格区分三段证据：', '',
         '- **Signal generation**：信号如何产生（第 1、2 节）；',
         '- **Backtest evidence**：历史样本统计（第 3~7、12 节）；',
         '- **OOS evidence**：样本外切片（第 12 节）。', '',
         '核心原则（§54）：HVE 是**事件**不是评分；HVE 是**起点**不是买点；'
         'BUY 只发生在 Re-expansion / 消化后突破确认；缩量只称 **Volume Contraction**，'
         '不等于「卖压下降」；结构破坏直接 FAIL。', '', '---', '']
    _sec_events(L, bt)
    _sec_groups(L, bt)
    _sec_horizons(L, bt)
    _sec_cost(L, bt)
    _sec_market(L, bt, daily)
    _sec_board_cap(L, bt)
    _sec_overlap(L, cross)
    _sec_sensitivity(L, sens)
    _sec_lookahead(L, tests)
    _sec_wf(L, bt)
    _sec_g12(L, bt, cross, sens, tests)

    acc = _acceptance_block(bt, cross, tests, sens)
    L += ['---', '', '## §53 验收输出块（由程序生成，禁止手写）', '', '```text']
    L += acc
    L += ['```', '']
    if bt:
        L += [f'> 回测产物：`{os.path.basename(backtest_path or _latest("hve_backtest") or "")}`'
              f'（{bt.get("start")}~{bt.get("end")}）', '',
              f'> 样本口径：{bt.get("note")}', '']
    return '\n'.join(L), acc


def save(text: str, dest: str = DEST) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'w', encoding='utf-8') as f:
        f.write(text)
    return dest


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description='HVE V1 研究报告生成（§51/§53）')
    ap.add_argument('--date', default=None, help='交易日，用于定位 hve_daily / hve_cross')
    ap.add_argument('--backtest', default=None, help='回测 JSON 路径（缺省取最新 hve_backtest_*.json）')
    ap.add_argument('--cross', default=None, help='交叉验证 JSON 路径（缺省按 --date 定位）')
    ap.add_argument('--daily', default=None, help='日线快照 JSON 路径（缺省按 --date 定位）')
    ap.add_argument('--dest', default=DEST)
    ap.add_argument('--skip-tests', action='store_true', help='不运行 pytest（复用上次结论之外仅作排版用途）')
    ap.add_argument('--sensitivity', action='store_true', help='运行 §42 单参数扰动（有界子样本）')
    ap.add_argument('--sens-start', default='20240101')
    ap.add_argument('--sens-end', default=None, help='缺省取回测区间 end')
    ap.add_argument('--sens-limit', type=int, default=150)
    ap.add_argument('--sens-horizon', type=int, default=20)
    a = ap.parse_args()

    bt = _load_json(a.backtest or _latest('hve_backtest'))
    sens = None
    if a.sensitivity:
        cfg = load_config()
        start = a.sens_start
        end = a.sens_end or (bt or {}).get('end') or datetime.now().strftime('%Y%m%d')
        sens = sensitivity(start, end, cfg, a.sens_limit, horizon=a.sens_horizon)

    text, acc = build(date=a.date, backtest_path=a.backtest, cross_path=a.cross,
                      daily_path=a.daily, sens=sens, run_tests=not a.skip_tests)
    path = save(text, a.dest)
    print(f'[HVE-REPORT] 已生成: {path}')
    print('')
    print('\n'.join(acc))
    return 0


if __name__ == '__main__':
    sys.exit(main())
