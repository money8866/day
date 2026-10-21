# -*- coding: utf-8 -*-
"""MSW 1.0 主管线（任务书 §十一）

用法：
  python run_msw.py                    # 全市场基线回测 + 基线对照 + 验证
  python run_msw.py --codes 200        # 仅前 200 只股票（冒烟）
  python run_msw.py --skip-ablations   # 跳过消融/参数扰动（省时）
  python run_msw.py --ablation-top 1200  # 消融用流动性前 1200 只子集

阶段：面板构建 → 全市场行走 → MSW 模拟 → 基线对照 → 切片/成本/尾部 →
      消融 + 参数稳定性（子集）→ 判定 → 全部交付文件。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msw import load_config, load_prereg
from msw.data_adapter import MswData
from msw.feature_engine import MarketPanel
from msw.backtest import (walk_universe, simulate, set_trade_calendar,
                          equity_metrics, round_metrics)
from msw import validation as V
from msw import report as R


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def liquid_codes(data, end_date: str, n: int) -> list:
    """按近一年平均成交额取前 n 只（消融/参数扰动子集）"""
    import sqlite3
    from msw.data_adapter import norm_date
    end_c = norm_date(end_date)                  # 库内为 YYYYMMDD 文本
    start_c = f'{int(end_c[:4]) - 1}{end_c[4:]}'
    sql = ('SELECT d.ts_code, AVG(d.amount) amt FROM daily_cache d '
           'WHERE d.trade_date>=? AND d.trade_date<=? '
           "AND (d.ts_code LIKE '%.SH' OR d.ts_code LIKE '%.SZ') "
           "AND substr(d.ts_code,1,1) NOT IN ('4','8','9') "
           'GROUP BY d.ts_code ORDER BY amt DESC LIMIT ?')
    with sqlite3.connect(f'file:{data.db_path}?mode=ro', uri=True, timeout=60.0) as conn:
        rows = conn.execute(sql, (start_c, end_c, int(n))).fetchall()
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--codes', type=int, default=0, help='仅行走前 N 只股票（0=全市场）')
    ap.add_argument('--skip-ablations', action='store_true')
    ap.add_argument('--ablation-top', type=int, default=1200)
    ap.add_argument('--skip-baselines', action='store_true')
    ap.add_argument('--out', default='')
    ap.add_argument('--cache-walk', default='',
                    help='全市场行走结果缓存 pickle 路径（写入）')
    ap.add_argument('--use-cached-walk', default='',
                    help='复用行走缓存 pickle 路径（跳过阶段一；配置哈希不一致则拒绝）')
    args = ap.parse_args()

    t0 = time.time()
    cfg = load_config()
    start = cfg['backtest']['start_date']
    end = cfg['backtest']['end_date']
    is_start, is_end = cfg['backtest']['is_range']
    oos_start, oos_end = cfg['backtest']['oos_range']

    data = MswData()
    log('构建横截面面板（hfq 矩阵 + 行业代理 + 基准）...')
    panel = MarketPanel(data, '2023-01-03', end, cfg)
    set_trade_calendar(panel.dates)
    log(f'面板就绪：{len(panel.codes)} 只股票 × {len(panel.dates)} 个交易日 '
        f'（{time.time() - t0:.0f}s）')

    codes = None
    if args.codes:
        codes = data.all_codes()[:args.codes]
        log(f'冒烟模式：仅行走 {len(codes)} 只股票')

    log('阶段一：全市场行走（MSW-A/B/C）...')
    walk = None
    if args.use_cached_walk and os.path.exists(args.use_cached_walk):
        import pickle
        import hashlib
        cfg_hash = hashlib.md5(json.dumps(cfg, sort_keys=True,
                                          ensure_ascii=False).encode()).hexdigest()
        with open(args.use_cached_walk, 'rb') as f:
            cached = pickle.load(f)
        if cached.get('cfg_hash') == cfg_hash and not args.codes:
            walk = cached['walk']
            log(f'复用行走缓存 {args.use_cached_walk}：'
                f'{walk["n_walked"]} 只，{len(walk["events"])} 只产生信号')
        else:
            log('行走缓存配置哈希不符或指定了 --codes，重新行走')
    if walk is None:
        walk = walk_universe(cfg, data, panel, codes=codes, logger=log)
        log(f'行走完成：{walk["n_walked"]} 只，{len(walk["events"])} 只产生信号 '
            f'（{time.time() - t0:.0f}s）')
        if args.cache_walk:
            import pickle
            import hashlib
            cfg_hash = hashlib.md5(json.dumps(cfg, sort_keys=True,
                                              ensure_ascii=False).encode()).hexdigest()
            os.makedirs(os.path.dirname(os.path.abspath(args.cache_walk)),
                        exist_ok=True)
            with open(args.cache_walk, 'wb') as f:
                pickle.dump({'cfg_hash': cfg_hash, 'codes': codes, 'walk': walk}, f,
                            protocol=pickle.HIGHEST_PROTOCOL)
            log(f'行走缓存已写入 {args.cache_walk}')

    log('阶段二：MSW 组合模拟...')
    msw_res = simulate(cfg, walk['events'], walk['bars'], panel, start, end,
                       meta=walk['meta'])
    em = equity_metrics(msw_res['equity'])
    rm = round_metrics(msw_res['trades'], span_dates=[start, end])
    log(f'MSW：轮数 {rm.get("n_rounds")}，每轮净均值 {rm.get("net_mean")}，'
        f'总收益 {em.get("total_return")}，Sharpe {em.get("sharpe")}')

    # ---- OOS 切片 ----
    def _slice(res_start, res_end):
        r = simulate(cfg, walk['events'], walk['bars'], panel, res_start, res_end,
                     meta=walk['meta'])
        return {'equity_metrics': equity_metrics(r['equity']),
                'round_metrics': round_metrics(r['trades'],
                                               span_dates=[res_start, res_end])}
    is_res = _slice(is_start, is_end)
    oos_res = _slice(oos_start, oos_end)
    log(f'IS：{is_res["round_metrics"].get("n_rounds")} 轮，净均值 '
        f'{is_res["round_metrics"].get("net_mean")}；'
        f'OOS：{oos_res["round_metrics"].get("n_rounds")} 轮，净均值 '
        f'{oos_res["round_metrics"].get("net_mean")}')

    # ---- 基线 ----
    baselines = {}
    if not args.skip_baselines:
        log('阶段三：基线对照...')
        baselines = V.run_baselines(cfg, walk, panel, data, start, end, logger=log)
        for k, b in baselines.items():
            bem = b.get('equity_metrics', {})
            tr = bem.get('total_return', b.get('total_return'))
            log(f'  基线 {k}: 总收益 {tr} Sharpe {bem.get("sharpe")}')

    # ---- 切片 / 敏感性 / 尾部 ----
    log('阶段四：切片与压力测试...')
    slices = V.regime_and_yearly_slices(cfg, walk, panel, start, end, res=msw_res)
    cost_sens = V.cost_sensitivity(cfg, walk, panel, start, end)
    drop_top = V.drop_top_stocks(cfg, walk, panel, start, end,
                                 frac=cfg['validation']['drop_top_stocks_frac'])

    # ---- 消融 + 参数稳定性（流动性子集） ----
    ablations, stability = {}, []
    if not args.skip_ablations:
        log(f'阶段五：消融 + 参数扰动（流动性前 {args.ablation_top} 只子集）...')
        sub = liquid_codes(data, end, args.ablation_top)
        ablations = V.run_ablation(cfg, walk, panel, data, start, end, sub, logger=log)
        stability = V.parameter_stability(cfg, panel, data, start, end, sub, logger=log)

    # ---- 判定（§10.3） ----
    verdict, reasons = judge(cfg, msw_res, rm, em, is_res, oos_res, baselines,
                             ablations, stability, cost_sens, drop_top)
    log(f'判定：{verdict}')
    for r in reasons:
        log(f'  - {r}')

    # ---- 输出 ----
    out_dir = args.out or R.OUT_DIR
    notes = [
        'ST 剔除基于当前名称（无 PIT 名称历史），回测早期 ST 样本无法按当时状态剔除',
        '财务分层使用 fina_indicator_cache 现有 8 字段近似，非完整财报',
        '行业相对强度用个股 industry 字段等权中位数代理',
        '消融/参数扰动在流动性子集上运行（全量成本过高），结论外推需谨慎',
        'ZigZag 阈值 = max(10%, 1.5×ATR%)；转折点必须事后确认才进入统计',
    ]
    paths = R.write_all(out_dir, walk, msw_res, baselines, ablations, stability,
                        cost_sens, drop_top, slices, cfg, start, end, verdict, notes)
    R.write_validation_report(out_dir, msw_res, baselines, ablations, stability,
                              cost_sens, drop_top, slices, cfg, start, end,
                              verdict, reasons, notes, walk=walk,
                              is_res=is_res, oos_res=oos_res)
    log(f'全部输出已写入 {out_dir}')
    for k, p in paths.items():
        log(f'  {k}: {os.path.basename(p)}')
    log(f'总耗时 {time.time() - t0:.0f}s')

    # 汇总 JSON（供外层读取）
    summary = {
        'verdict': verdict, 'reasons': reasons,
        'msw': {'equity': em, 'rounds': rm},
        'is': is_res, 'oos': oos_res,
        'baselines': {k: {'equity': v.get('equity_metrics',
                                          {'total_return': v.get('total_return')}),
                          'rounds': v.get('round_metrics')}
                      for k, v in baselines.items()},
        'n_walked': walk['n_walked'], 'n_event_stocks': len(walk['events']),
    }
    with open(os.path.join(out_dir, 'msw_run_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(R._jsonable(summary), f, ensure_ascii=False, indent=2)
    return summary


def judge(cfg, msw_res, rm, em, is_res, oos_res, baselines, ablations,
          stability, cost_sens, drop_top):
    """§10.3 六条成功标准 → PASS / UNPROVEN / FAIL"""
    reasons = []
    ok_flags = []

    # 1) 样本外 30bp 成本后每轮期望收益 > 0
    oos_rm = oos_res.get('round_metrics', {})
    oos_exp = oos_rm.get('expectancy')
    c1 = oos_exp is not None and oos_exp > 0
    ok_flags.append(c1)
    reasons.append(f'① 样本外每轮期望收益（30bp 成本后）= {oos_exp} '
                   f'{"✓" if c1 else "✗"}')

    # 2) 多年度稳定
    is_exp = is_res.get('round_metrics', {}).get('expectancy')
    is_tr = is_res.get('equity_metrics', {}).get('total_return')
    oos_tr = oos_res.get('equity_metrics', {}).get('total_return')
    c2 = (is_exp is not None and is_exp > 0) and (oos_exp is not None and oos_exp > 0)
    ok_flags.append(c2)
    reasons.append(f'② IS 每轮期望 {is_exp} / 总收益 {is_tr}；OOS 每轮期望 {oos_exp} / '
                   f'总收益 {oos_tr} {"✓" if c2 else "✗"}')

    # 3) 相较基线的增量（至少一个维度）
    c3, detail = False, []
    bh = (baselines or {}).get('buy_hold', {}).get('equity_metrics', {})
    mt = (baselines or {}).get('ma_trend', {}).get('equity_metrics', {})
    if bh:
        if em.get('sharpe') is not None and bh.get('sharpe') is not None:
            detail.append(f'Sharpe {em["sharpe"]:.3f} vs B&H {bh["sharpe"]:.3f}')
            c3 = c3 or em['sharpe'] > bh['sharpe']
        if em.get('max_drawdown') is not None and bh.get('max_drawdown') is not None:
            detail.append(f'MaxDD {em["max_drawdown"]:.3f} vs B&H {bh["max_drawdown"]:.3f}')
            c3 = c3 or em['max_drawdown'] > bh['max_drawdown']
        if em.get('total_return') is not None and bh.get('total_return') is not None:
            detail.append(f'总收益 {em["total_return"]:.3f} vs B&H {bh["total_return"]:.3f}')
            c3 = c3 or em['total_return'] > bh['total_return']
    if mt and em.get('sharpe') is not None and mt.get('sharpe') is not None:
        detail.append(f'Sharpe vs MA趋势 {mt["sharpe"]:.3f}')
        c3 = c3 or em['sharpe'] > mt['sharpe']
    ok_flags.append(bool(c3))
    reasons.append(f'③ 基线增量：{"; ".join(detail) if detail else "无基线数据"} '
                   f'{"✓" if c3 else "✗"}')

    # 4) 剔除 Top5% 后仍可解释
    k = (drop_top or {}).get('kept_round_metrics', {})
    kept_exp = k.get('expectancy')
    c4 = kept_exp is not None and kept_exp > 0
    ok_flags.append(bool(c4))
    reasons.append(f'④ 剔除收益贡献最高 5% 股票后期望收益 = {kept_exp} '
                   f'{"✓" if c4 else "✗"}')

    # 5) 参数适度变化结论不崩溃
    c5, signs = True, []
    base_exp = rm.get('expectancy')
    for r in (stability or []):
        if r.get('net_mean') is None:
            continue
        signs.append(r['net_mean'])
    if signs and base_exp is not None:
        same = sum(1 for s in signs if (s > 0) == (base_exp > 0))
        c5 = same >= max(1, int(0.6 * len(signs)))
    ok_flags.append(bool(c5))
    reasons.append(f'⑤ 参数扰动 {len(signs)} 组，符号一致率 '
                   f'{sum(1 for s in signs if base_exp is not None and (s > 0) == (base_exp > 0))}/{len(signs)} '
                   f'{"✓" if c5 else "✗"}')

    # 6) 成本敏感性：30bp 后期望仍为正
    c6 = True
    for r in (cost_sens or []):
        if r['cost_round_trip_bp'] <= 30 and (r.get('expectancy') or 0) <= 0:
            c6 = False
    ok_flags.append(bool(c6))
    reasons.append(f'⑥ 30bp 成本档期望收益 '
                   f'{"✓" if c6 else "✗"}')

    n_ok = sum(1 for x in ok_flags if x)
    if n_ok == len(ok_flags):
        verdict = 'PASS'
    elif n_ok >= 4:
        verdict = 'UNPROVEN'
    else:
        verdict = 'FAIL'
    return verdict, reasons


if __name__ == '__main__':
    main()
