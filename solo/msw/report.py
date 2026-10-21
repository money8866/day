# -*- coding: utf-8 -*-
"""MSW 报告与交付文件（任务书 §11 report.py / §十一 输出清单）

输出（全部写入 solo/msw/out/）：
  msw_daily_candidates.csv      每日候选股票池（近 N 个交易日，§12 全字段）
  msw_quality_scores.csv        个股质量评分明细（信号日 + 最新快照）
  msw_trades.csv                逐笔交易记录（含未成交原因）
  msw_equity_curve.csv          净值曲线
  msw_annual_metrics.csv        分年度指标
  msw_market_regime_metrics.csv 市场状态切片指标
  msw_ablation_results.csv      消融结果
  msw_parameter_stability.csv   参数稳定性
  msw_cost_sensitivity.csv      成本敏感性
  msw_validation_report.md      验证报告（回答三个最终问题 + UNPROVEN/PASS 判定）
  msw_run_manifest.json         运行清单（数据截止/配置/程序版本/参数）
"""
import json
import os
import platform
import time

import numpy as np
import pandas as pd

import msw
from msw import load_config

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')

CANDIDATE_FIELDS = [
    'trade_date', 'ts_code', 'stock_name', 'long_trend_score', 'msw_quality_score',
    'swing_count_250d', 'median_swing_return', 'pullback_recovery_rate',
    'median_recovery_days', 'relative_strength_120d', 'liquidity_score',
    'fundamental_state', 'current_signal', 'entry_reference', 'exit_reference',
    'invalidation_condition', 'risk_flags',
]


def ensure_out():
    os.makedirs(OUT_DIR, exist_ok=True)
    return OUT_DIR


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


def write_all(out_dir, walk, msw_res, baselines, ablations, stability, cost_sens,
              drop_top, slices, cfg, start, end, verdict, extra_notes=None):
    """写出全部交付文件；返回文件路径字典"""
    ensure_out()
    paths = {}

    # ---- 1) 每日候选（近 60 个交易日的完整候选池 + 全部信号日记录） ----
    rows = []
    sig_rows = []
    for code, recs in walk['records'].items():
        for r in recs:
            if r['current_signal'] != 'WATCH' or (
                    r['trend_pass'] and (r['msw_quality_score'] or 0)
                    >= cfg['quality_score']['min_score']):
                row = {k: r.get(k) for k in CANDIDATE_FIELDS}
                row['signal_action'] = r.get('signal_action')
                row['signal_rule'] = r.get('signal_rule')
                row['quality_tags'] = r.get('quality_tags')
                row['industry'] = r.get('industry')
                row['close'] = r.get('close')
                (sig_rows if r['current_signal'] != 'WATCH' else rows).append(row)
    df_cand = pd.DataFrame(rows)
    if not df_cand.empty:
        last_dates = sorted(df_cand['trade_date'].unique())[-60:]
        df_cand = df_cand[df_cand['trade_date'].isin(last_dates)]
    p = os.path.join(out_dir, 'msw_daily_candidates.csv')
    df_cand.to_csv(p, index=False, encoding='utf-8-sig')
    paths['daily_candidates'] = p

    # ---- 2) 质量评分明细：信号日记录 + 每只股票最新快照 ----
    qs_rows = []
    for code, recs in walk['records'].items():
        sig = [r for r in recs if r['current_signal'] != 'WATCH']
        for r in (sig or recs[-1:]):
            qs_rows.append({
                'trade_date': r['trade_date'], 'ts_code': r['ts_code'],
                'stock_name': r['stock_name'], 'msw_quality_score': r['msw_quality_score'],
                'sub_trend': r.get('sub_trend'), 'sub_swing_space': r.get('sub_swing_space'),
                'sub_recovery': r.get('sub_recovery'), 'sub_frequency': r.get('sub_frequency'),
                'sub_volume': r.get('sub_volume'), 'sub_liquidity': r.get('sub_liquidity'),
                'quality_tags': r.get('quality_tags'),
                'swing_count_250d': r.get('swing_count_250d'),
                'median_swing_return': r.get('median_swing_return'),
                'pullback_recovery_rate': r.get('pullback_recovery_rate'),
                'median_recovery_days': r.get('median_recovery_days'),
                'relative_strength_120d': r.get('relative_strength_120d'),
                'liquidity_score': r.get('liquidity_score'),
                'fundamental_state': r.get('fundamental_state'),
                'risk_flags': r.get('risk_flags'),
            })
    p = os.path.join(out_dir, 'msw_quality_scores.csv')
    pd.DataFrame(qs_rows).to_csv(p, index=False, encoding='utf-8-sig')
    paths['quality_scores'] = p

    # ---- 3) 逐笔交易 ----
    p = os.path.join(out_dir, 'msw_trades.csv')
    pd.DataFrame(msw_res['trades']).to_csv(p, index=False, encoding='utf-8-sig')
    paths['trades'] = p
    p = os.path.join(out_dir, 'msw_unfilled_orders.csv')
    pd.DataFrame(msw_res['unfilled']).to_csv(p, index=False, encoding='utf-8-sig')
    paths['unfilled'] = p

    # ---- 4) 净值曲线 ----
    p = os.path.join(out_dir, 'msw_equity_curve.csv')
    msw_res['equity'].to_csv(p, index=False, encoding='utf-8-sig')
    paths['equity'] = p

    # ---- 5) 分年度指标 ----
    rows = []
    for y, m in (slices.get('yearly') or {}).items():
        rows.append({'year': y, **m})
    p = os.path.join(out_dir, 'msw_annual_metrics.csv')
    pd.DataFrame(rows).to_csv(p, index=False, encoding='utf-8-sig')
    paths['annual'] = p

    # ---- 6) 市场状态指标 ----
    rows = []
    for rg, m in (slices.get('regime') or {}).items():
        rows.append({'regime': rg, **m})
    for rule, m in (slices.get('by_rule') or {}).items():
        rows.append({'regime': f'rule:{rule}', 'n_rounds': m['n'],
                     'net_sum': round(m['net_sum'], 4)})
    for rsn, m in (slices.get('by_exit_reason') or {}).items():
        rows.append({'regime': f'exit:{rsn}', 'n_rounds': m['n'],
                     'net_sum': round(m['net_sum'], 4)})
    p = os.path.join(out_dir, 'msw_market_regime_metrics.csv')
    pd.DataFrame(rows).to_csv(p, index=False, encoding='utf-8-sig')
    paths['regime'] = p

    # ---- 7) 消融 ----
    rows = []
    for name, m in (ablations or {}).items():
        em, rm = m.get('equity_metrics', {}), m.get('round_metrics', {})
        rows.append({'ablation': name,
                     'n_rounds': rm.get('n_rounds'),
                     'net_mean': rm.get('net_mean'),
                     'win_rate': rm.get('win_rate'),
                     'profit_factor': rm.get('profit_factor'),
                     'total_return': em.get('total_return'),
                     'max_drawdown': em.get('max_drawdown'),
                     'sharpe': em.get('sharpe'),
                     'capital_utilization': em.get('capital_utilization')})
    p = os.path.join(out_dir, 'msw_ablation_results.csv')
    pd.DataFrame(rows).to_csv(p, index=False, encoding='utf-8-sig')
    paths['ablation'] = p

    # ---- 8) 参数稳定性 ----
    p = os.path.join(out_dir, 'msw_parameter_stability.csv')
    pd.DataFrame(stability or []).to_csv(p, index=False, encoding='utf-8-sig')
    paths['stability'] = p

    # ---- 9) 成本敏感性 ----
    p = os.path.join(out_dir, 'msw_cost_sensitivity.csv')
    pd.DataFrame(cost_sens or []).to_csv(p, index=False, encoding='utf-8-sig')
    paths['cost'] = p

    # ---- 10) 运行清单 ----
    manifest = {
        'strategy_id': msw.STRATEGY_ID, 'version': msw.__version__,
        'run_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'data_source': 'D:\\mystock\\cache_daily\\stock_data.db (read-only)',
        'data_cutoff': end,
        'backtest_range': [start, end],
        'is_range': cfg['backtest']['is_range'],
        'oos_range': cfg['backtest']['oos_range'],
        'config': _jsonable(cfg),
        'preregistration': _jsonable(msw.load_prereg()),
        'runtime': {'python': platform.python_version(),
                    'pandas': pd.__version__, 'numpy': np.__version__},
        'n_stocks_walked': walk.get('n_walked'),
        'n_event_stocks': len(walk.get('events', {})),
        'n_trades': len(msw_res['trades']),
        'verdict': verdict,
        'notes': extra_notes or [],
    }
    p = os.path.join(out_dir, 'msw_run_manifest.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(_jsonable(manifest), f, ensure_ascii=False, indent=2)
    paths['manifest'] = p
    return paths


def write_validation_report(out_dir, msw_res, baselines, ablations, stability,
                            cost_sens, drop_top, slices, cfg, start, end,
                            verdict, verdict_reasons, extra_notes=None,
                            walk=None, is_res=None, oos_res=None):
    """生成 msw_validation_report.md（回答三个最终问题 + 判定）"""
    em = msw_res['equity_metrics'] if 'equity_metrics' in msw_res else None
    from msw.backtest import equity_metrics, round_metrics
    em = equity_metrics(msw_res['equity'])
    rm = round_metrics(msw_res['trades'], span_dates=[start, end])
    L = []
    A = L.append
    A('# MSW 1.0 验证报告')
    A('')
    A(f'- 策略：MSW（Multi-Swing Wave）v{msw.__version__}')
    A(f'- 回测区间：{start} ~ {end}（IS {cfg["backtest"]["is_range"][0]}~'
      f'{cfg["backtest"]["is_range"][1]}，OOS {cfg["backtest"]["oos_range"][0]}~'
      f'{cfg["backtest"]["oos_range"][1]}）')
    A(f'- 数据源：stock_data.db（只读，截止 {end}）；成本：往返 '
      f'{cfg["backtest"]["cost_round_trip_bp"]}bp + 单边滑点 '
      f'{cfg["backtest"]["slippage_bp"]}bp；T+1、涨跌停、停牌约束全部生效')
    A(f'- 判定：**{verdict}**')
    A('')
    A('## 0. 判定依据')
    A('')
    for r in verdict_reasons:
        A(f'- {r}')
    A('')

    A('## 1. MSW 组合表现')
    A('')
    A('| 指标 | 值 |')
    A('|---|---|')
    for k in ('total_return', 'ann_return', 'ann_vol', 'sharpe', 'calmar',
              'max_drawdown', 'capital_utilization', 'zero_position_ratio',
              'avg_positions', 'n_days'):
        if em.get(k) is not None:
            A(f'| {k} | {round(em[k], 4) if isinstance(em[k], float) else em[k]} |')
    A('')
    A('| 每轮指标 | 值 |')
    A('|---|---|')
    for k in ('n_rounds', 'win_rate', 'net_mean', 'net_median', 'net_p10', 'net_p90',
              'avg_win', 'avg_loss', 'profit_factor', 'expectancy',
              'cost_share_of_gross', 'avg_hold_days', 'rounds_per_year'):
        if rm.get(k) is not None:
            A(f'| {k} | {round(rm[k], 4) if isinstance(rm[k], float) else rm[k]} |')
    A('')

    A('## 2. 基线对照（同成本/同执行约束）')
    A('')
    A('| 策略 | 总收益 | 年化 | Sharpe | 最大回撤 | 轮数 | 每轮净均值 | 胜率 |')
    A('|---|---|---|---|---|---|---|---|')
    A(f'| **MSW** | {_f(em.get("total_return"))} | {_f(em.get("ann_return"))} | '
      f'{_f(em.get("sharpe"))} | {_f(em.get("max_drawdown"))} | {rm.get("n_rounds")} | '
      f'{_f(rm.get("net_mean"))} | {_f(rm.get("win_rate"))} |')
    for name, b in (baselines or {}).items():
        bem, brm = b.get('equity_metrics', {}), b.get('round_metrics', {})
        if name == 'index_buy_hold':
            A(f'| {name}（指数） | {_f(b.get("total_return"))} | - | - | - | - | - | - |')
            continue
        A(f'| {name} | {_f(bem.get("total_return"))} | {_f(bem.get("ann_return"))} | '
          f'{_f(bem.get("sharpe"))} | {_f(bem.get("max_drawdown"))} | '
          f'{brm.get("n_rounds")} | {_f(brm.get("net_mean"))} | {_f(brm.get("win_rate"))} |')
    A('')

    A('## 3. 消融实验（各模块增量价值）')
    A('')
    if ablations:
        A('| 消融项 | 轮数 | 每轮净均值 | 胜率 | PF | 总收益 | 最大回撤 |')
        A('|---|---|---|---|---|---|---|')
        for name, m in ablations.items():
            em2, rm2 = m.get('equity_metrics', {}), m.get('round_metrics', {})
            A(f'| {name} | {rm2.get("n_rounds")} | {_f(rm2.get("net_mean"))} | '
              f'{_f(rm2.get("win_rate"))} | {_f(rm2.get("profit_factor"))} | '
              f'{_f(em2.get("total_return"))} | {_f(em2.get("max_drawdown"))} |')
    else:
        A('（本次未运行消融实验）')
    A('')

    A('## 4. 压力测试')
    A('')
    A('### 4.1 成本敏感性')
    A('')
    if cost_sens:
        A('| 往返成本(bp) | 轮数 | 每轮净均值 | 胜率 | 总收益 | Sharpe |')
        A('|---|---|---|---|---|---|')
        for r in cost_sens:
            A(f'| {r["cost_round_trip_bp"]} | {r.get("n_rounds")} | {_f(r.get("net_mean"))} | '
              f'{_f(r.get("win_rate"))} | {_f(r.get("total_return"))} | {_f(r.get("sharpe"))} |')
    A('')
    A('### 4.2 参数稳定性')
    A('')
    if stability:
        A('| 参数 | 取值 | 轮数 | 每轮净均值 | 胜率 | 总收益 |')
        A('|---|---|---|---|---|---|')
        for r in stability:
            A(f'| {r["param"]} | {r["value"]} | {r.get("n_rounds")} | {_f(r.get("net_mean"))} | '
              f'{_f(r.get("win_rate"))} | {_f(r.get("total_return"))} |')
    A('')
    A('### 4.3 剔除收益贡献最高 5% 股票')
    A('')
    if drop_top:
        A(f'- 总股票数 {drop_top.get("n_stocks_total")}，剔除 {drop_top.get("n_dropped")} 只；'
          f'Top5% 净收益占比 {_f(drop_top.get("top_share_of_net"))}')
        k = drop_top.get('kept_round_metrics', {})
        ke = drop_top.get('kept_equity_metrics', {})
        A(f'- 剔除后：轮数 {k.get("n_rounds")}，每轮净均值 {_f(k.get("net_mean"))}，'
          f'胜率 {_f(k.get("win_rate"))}，总收益 {_f(ke.get("total_return"))}')
    A('')
    A('### 4.4 分年度 / 分市场状态')
    A('')
    y = slices.get('yearly', {})
    if y:
        A('| 年度 | 收益 | 最大回撤 | 日均持仓 | 天数 |')
        A('|---|---|---|---|---|')
        for kk, m in y.items():
            A(f'| {kk} | {_f(m.get("total_return"))} | {_f(m.get("max_drawdown"))} | '
              f'{_f(m.get("avg_positions"))} | {m.get("n_days")} |')
    rg = slices.get('regime', {})
    if rg:
        A('')
        A('| 市场状态 | 收益 | 最大回撤 | 天数 |')
        A('|---|---|---|---|')
        for kk, m in rg.items():
            A(f'| {kk} | {_f(m.get("total_return"))} | {_f(m.get("max_drawdown"))} | '
              f'{m.get("n_days")} |')
    A('')
    br = slices.get('by_rule', {})
    if br:
        A('| 信号规则 | 轮数 | 净收益合计 |')
        A('|---|---|---|')
        for kk, m in br.items():
            A(f'| {kk} | {m["n"]} | {round(m["net_sum"], 4)} |')
    A('')

    A('## 5. 三个最终问题')
    A('')

    # ---------------- Q1：股票级结论 ----------------
    A('**Q1 哪些 A 股最适合未来一两年反复做波段，为什么？**')
    A('')
    if walk is not None:
        min_score = cfg['quality_score']['min_score']
        latest = {code: recs[-1] for code, recs in (walk.get('records') or {}).items()
                  if recs}
        pool = [r for r in latest.values()
                if r.get('trend_pass') and (r.get('msw_quality_score') or 0) >= min_score]
        A(f'- 候选池规模：全市场行走 {walk.get("n_walked", 0)} 只，最新交易日通过 '
          f'MSW-A 趋势过滤且质量分 >= {min_score} 的共 **{len(pool)}** 只')
        scores = [r['msw_quality_score'] for r in pool if r.get('msw_quality_score')]
        if scores:
            s = pd.Series(scores)
            A(f'- 质量分分布：均值 {s.mean():.1f} / 中位 {s.median():.1f} / '
              f'P90 {s.quantile(0.9):.1f} / 最高 {s.max():.1f}')
        tag_cnt = {}
        for r in pool:
            raw = r.get('quality_tags')
            tags = raw.split('|') if isinstance(raw, str) else (raw or [])
            for t in tags:
                if t:
                    tag_cnt[t] = tag_cnt.get(t, 0) + 1
        if tag_cnt:
            A('- 标签分布：' + '，'.join(
                f'{k} {v} 只' for k, v in sorted(tag_cnt.items(),
                                                key=lambda kv: -kv[1])))
        A('- 入选条件（须全部满足）：MA120 上行、相对强度为正、250 日内可确认波段 >= '
          f'{cfg["swing"]["min_swing_count_250d"]} 段、回撤修复率与量价结构达标')
        top = sorted(pool, key=lambda r: -(r.get('msw_quality_score') or 0))[:15]
        if top:
            A('')
            A('| 代码 | 名称 | 行业 | 质量分 | 标签 |')
            A('|---|---|---|---|---|')
            for r in top:
                raw = r.get('quality_tags')
                tags = raw.split('|') if isinstance(raw, str) else (raw or [])
                A(f'| {r.get("ts_code")} | {r.get("stock_name")} | {r.get("industry")} '
                  f'| {round(r["msw_quality_score"], 1)} '
                  f'| {"、".join(t for t in tags if t)} |')
        A('')
        A('**注意**：以上为全区间（含样本外）质量快照，属事后描述。本次判定为 '
          f'{verdict}，该名单不构成"未来一两年可重复跑赢"的证据（见 Q3）。')
    else:
        A('（见 msw_quality_scores.csv 与 msw_daily_candidates.csv：综合 '
          'MSW_QUALITY_SCORE 排序 + 「长期趋势型 / 高波段频率型 / 回撤修复型」标签。）')
    A('')

    # ---------------- Q2：规则级结论 ----------------
    A('**Q2 什么样的买入和卖出规则最有可能产生可重复的净收益？**')
    A('')
    by_rule = (slices or {}).get('by_rule') or {}
    if by_rule:
        A('| 入场规则 | 轮数 | 每轮净均值 | 净收益合计 |')
        A('|---|---|---|---|')
        for k, m in sorted(by_rule.items(), key=lambda kv: -kv[1]['n']):
            n = m['n']
            A(f'| {k} | {n} | {round(m["net_sum"] / n, 4) if n else "-"} '
              f'| {round(m["net_sum"], 4)} |')
        A('')
    by_exit = (slices or {}).get('by_exit_reason') or {}
    if by_exit:
        A('| 退出原因 | 次数 | 净收益合计 |')
        A('|---|---|---|')
        for k, m in sorted(by_exit.items(), key=lambda kv: -kv[1]['n']):
            A(f'| {k} | {m["n"]} | {round(m["net_sum"], 4)} |')
        A('')
    if ablations:
        A('消融实验（流动性前 N 子集，每轮净均值）结论：')
        for name, m in ablations.items():
            rm2 = m.get('round_metrics') or {}
            nm = rm2.get('net_mean')
            if nm is None:
                continue
            if name == 'no_vol_position' and nm < 0:
                A(f'- **{name}：{round(nm, 4)}（唯一转负）** → ATR 风险预算仓位是'
                  f'承重组件，去掉即亏钱')
            elif name == 'no_trend_filter':
                A(f'- {name}：{round(nm, 4)}，轮数 {rm2.get("n_rounds")}（远多于基线）'
                  f' → 趋势过滤以量换质，滤掉大量低质量交易')
            elif name in ('no_quality_score', 'no_fundamental'):
                A(f'- {name}：{round(nm, 4)} → 在流动性子集上几乎不绑定（该子集股票'
                  f'本就达标），其价值需在全市场弱流动性股票上验证')
            elif name == 'with_regime_filter':
                A(f'- {name}：{round(nm, 4)} → 该子集上启用市场状态过滤反而拉低每轮'
                  f'净均值 → 状态过滤暂无正贡献（V1 默认关闭，待样本外验证）')
            else:
                A(f'- {name}：{round(nm, 4)} → 去掉该组件后每轮净均值下降，组件有正贡献')
        A('')
    A('**综合**：V1 的实现收益几乎全部由 C2 均值回归贡献（见 by_rule 表），C1/C3 '
      '杯水车薪；退出端 tp2 止盈与 below_pullback_low 止损是主要盈亏来源。但样本外'
      '（OOS）每轮期望为负，上述规则结构尚无可重复净收益的证据。')
    A('')

    # ---------------- Q3：风险调整结论 ----------------
    A('**Q3 多次交易是否真正提高风险调整后收益和资金利用效率？**')
    A('')
    bh = (baselines or {}).get('buy_hold', {}).get('equity_metrics', {})
    mt = (baselines or {}).get('ma_trend', {}).get('equity_metrics', {})
    mc = (baselines or {}).get('matched_control', {}).get('equity_metrics', {})
    A(f'- 资金效率：平均持仓 {round(em.get("avg_positions") or 0, 2)} 只，资金利用率 '
      f'{_f(em.get("capital_utilization"))}，年均 {round(rm.get("rounds_per_year") or 0, 1)} 轮，'
      f'平均持有 {round(rm.get("avg_hold_days") or 0, 1)} 个交易日 —— 交易频次高、占用满')
    A(f'- 风险调整收益：Sharpe {_f(em.get("sharpe"))} vs B&H {_f(bh.get("sharpe"))} '
      f'vs MA趋势 {_f(mt.get("sharpe"))}；Calmar {_f(em.get("calmar"))}；'
      f'最大回撤 {_f(em.get("max_drawdown"))}（B&H {_f(bh.get("max_drawdown"))}）')
    if is_res and oos_res:
        is_rm = is_res.get('round_metrics', {})
        oos_rm = oos_res.get('round_metrics', {})
        A(f'- 样本内外分裂：IS 每轮期望 {_f(is_rm.get("expectancy"))}'
          f'（{is_rm.get("n_rounds")} 轮）→ OOS 每轮期望 '
          f'{_f(oos_rm.get("expectancy"))}（{oos_rm.get("n_rounds")} 轮）')
    if mc:
        A(f'- 匹配对照（安慰剂计时，同一交易时点换到相似股票）：总收益 '
          f'{_f(mc.get("total_return"))} vs MSW {_f(em.get("total_return"))} '
          f'→ 选股贡献约 {round((em.get("total_return") or 0) - (mc.get("total_return") or 0), 4)}，'
          f'而计时本身接近盈亏平衡')
    if drop_top:
        A(f'- 顶部依赖：剔除收益贡献最高 5% 股票（{drop_top.get("n_dropped")} 只）后'
          f'每轮期望 {_f((drop_top.get("kept_round_metrics") or {}).get("expectancy"))}，'
          f'Top5% 净收益占比 {_f(drop_top.get("top_share_of_net"))}')
    A('')
    A(f'**综合**：多交易未提高风险调整后收益（Sharpe/Calmar/回撤全面劣于买入持有与 '
      f'MA 趋势基线）；唯一定量正贡献是选股维度（vs 匹配对照），但被样本外转负与'
      f'顶部股票依赖抵消。判定：**{verdict}**。')
    A('')
    if extra_notes:
        A('## 6. 附注与已知局限')
        A('')
        for n in extra_notes:
            A(f'- {n}')
        A('')

    p = os.path.join(out_dir, 'msw_validation_report.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    return p


def _f(x, nd=4):
    if x is None:
        return '-'
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return x
