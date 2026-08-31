# -*- coding: utf-8 -*-
"""HVT-BULL 每日主流程

对全A股扫描：
  1) 当日新发生的 HVT 事件（HVT_DETECTED）
  2) 近40个交易日内 HVT 事件的持续跟踪（锁筹/突破/失败）
输出：
  report_daily/hvt_bull_{date}.json
  report_daily/hvt_bull_report_{date}.md
"""

import os
import sys
import json
import copy
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.data_loader import HvtDataLoader
from hvt_bull.engine import HvtBullEngine, similarity
from hvt_bull import context as ctx
from hvt_bull.models import state_rank
from hvt_bull.future_expansion import compute_future_expansion
from hvt_bull.trade_execution import compute_trade_execution, TE_JSON_FIELDS

import yaml


def _load_config():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def _universe(loader: HvtDataLoader, trade_date: str, cfg: dict):
    """构建股票池：沪深A股、排除ST/北交所/上市不足120日、市值与流动性下限"""
    import stock_cache as sc
    uni_cfg = cfg.get('universe', {})
    sb = sc.load_stock_basic()
    if sb is None or sb.empty:
        return []
    sb = sb.copy()
    cs = loader.query_cross_section(trade_date, fields=('ts_code', 'turnover_rate', 'amount', 'total_mv', 'vol', 'close'))
    if cs is None or cs.empty:
        return []
    cs = cs.dropna(subset=['close'])
    sb_names = dict(zip(sb['ts_code'], sb.get('name', pd.Series(dtype=str))))
    list_dates = dict(zip(sb['ts_code'], sb.get('list_date', pd.Series(dtype=str)))) if 'list_date' in sb.columns else {}

    min_mv = float(uni_cfg.get('min_market_cap', 30.0)) * 10000.0   # 万元
    min_amt = float(uni_cfg.get('min_avg_amount_20', 3000.0))       # 万元
    min_days = int(uni_cfg.get('min_listed_days', 120))

    # 20日均成交额截面近似：用当日截面 + 过去20日截面均值
    dates = loader.trade_dates('', trade_date)
    dates = [d for d in dates if d >= (pd.to_datetime(trade_date) - pd.Timedelta(days=40)).strftime('%Y%m%d')]
    amt_map = {}
    small_cap_flag = {}
    for d in dates[-21:]:
        c = loader.query_cross_section(d, fields=('ts_code', 'amount', 'total_mv'))
        if c is not None and not c.empty:
            for _, r in c.iterrows():
                amt_map.setdefault(r['ts_code'], []).append(r['amount'])
                small_cap_flag[r['ts_code']] = r.get('total_mv', np.nan)

    out = []
    for _, r in cs.iterrows():
        code = r['ts_code']
        if not (code.endswith('.SH') or code.endswith('.SZ')):
            continue
        if code.startswith(('8', '4', '9')):
            continue
        name = str(sb_names.get(code, ''))
        if uni_cfg.get('exclude_st', True) and ('ST' in name.upper() or '退' in name):
            continue
        ld = list_dates.get(code, '')
        if ld:
            try:
                listed_days = (pd.to_datetime(trade_date) - pd.to_datetime(str(ld), format='%Y%m%d')).days
                if listed_days < min_days:
                    continue
            except Exception:
                pass
        total_mv = r.get('total_mv', np.nan)
        if not np.isfinite(total_mv):
            continue
        mv_w = float(total_mv) / 10.0 if total_mv > 1e6 else float(total_mv)
        # stk_factor_pro total_mv 单位：万元
        avg_amt_w = float(np.mean(amt_map.get(code, [np.nan]))) / 10.0 if code in amt_map else np.nan
        small_cap = mv_w < min_mv
        if small_cap and not (np.isfinite(avg_amt_w) and avg_amt_w > min_amt * 3):
            continue
        if np.isfinite(avg_amt_w) and avg_amt_w < min_amt:
            continue
        out.append({'ts_code': code, 'name': name, 'small_cap': small_cap,
                    'total_mv': float(total_mv), 'avg_amount': avg_amt_w})
    return out


def _fe_num(v, default=0.0):
    """V3.1 FE 字段安全取数：None/NaN/异常 → default"""
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except Exception:
        return default


def _build_rs_maps(loader: HvtDataLoader, trade_date: str) -> dict:
    """构建指定交易日的全市场 RS5/RS10/RS20/RS60/RS120 百分位图（V3 相对强度 + V3.1 Future Expansion）"""
    cal = loader.trade_dates('', trade_date)
    if trade_date not in cal:
        return {}
    pos = cal.index(trade_date)
    if pos < 20:
        return {}
    c0 = loader.query_cross_section(trade_date, fields=('ts_code', 'close'))
    if c0 is None or c0.empty:
        return {}
    c0 = pd.to_numeric(c0.set_index('ts_code')['close'], errors='coerce').dropna()
    m = {}
    for h in (5, 10, 20, 60, 120):
        if pos < h:
            continue
        c1 = loader.query_cross_section(cal[pos - h], fields=('ts_code', 'close'))
        if c1 is None or c1.empty:
            continue
        c1 = pd.to_numeric(c1.set_index('ts_code')['close'], errors='coerce').dropna()
        both = c0.div(c1.reindex(c0.index)).sub(1.0)
        both = both.replace([np.inf, -np.inf], np.nan).dropna()
        m[f'rs{h}'] = both.rank(pct=True) * 100.0
    return {trade_date: m}


def _load_calibration(cfg: dict) -> dict:
    """加载 V3 校准表（由全量回测生成）；不存在时返回空 dict，运行方标记 SAMPLE_LOW"""
    path = cfg.get('v3', {}).get('calib_file', '')
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _load_leader_codes(trade_date: str, cfg: dict):
    """加载 sli_v2 细分龙头 Top5 代码集（关联过滤，标准接口）。

    返回 None 表示未启用或加载失败（不过滤）；否则返回 ts_code 集合。
    走 sli.reader.get_subsector_top5（全量 359 赛道 Top5 快照）。
    """
    d = cfg.get('sli_v2', {})
    if not d.get('leader_filter', False):
        return None
    try:
        from sli.reader import get_subsector_top5
        df = get_subsector_top5()
    except Exception as e:
        print(f"[HVT-BULL] 警告: sli_v2 龙头加载失败 {e}，跳过龙头过滤")
        return None
    if df is None or df.empty or 'ts_code' not in df.columns:
        return None
    return set(df['ts_code'].astype(str).str.strip())


def run_daily(trade_date: str = None, cfg: dict = None, top_n: int = None) -> dict:
    """每日扫描主入口。返回结果 dict 并落盘 JSON/MD。"""
    if cfg is None:
        cfg = _load_config()
    if trade_date is None:
        import stock_cache as sc
        trade_date = sc.get_effective_date()
    loader = HvtDataLoader()
    engine = HvtBullEngine(cfg)

    uni = _universe(loader, trade_date, cfg)
    print(f"[HVT-BULL] 股票池: {len(uni)}只 ({trade_date})")
    if not uni:
        return {'trade_date': trade_date, 'events': []}

    # sli_v2 细分龙头关联过滤：不在龙头 Top5 表中的股票直接过滤
    leader_codes = _load_leader_codes(trade_date, cfg)
    if leader_codes is not None:
        uni = [u for u in uni if u['ts_code'] in leader_codes]
        print(f"[HVT-BULL] sli_v2 龙头过滤: 股票池 {len(uni)}只")
        if not uni:
            return {'trade_date': trade_date, 'events': []}

    theme_map = ctx.load_theme_map(trade_date)
    v3_enabled = bool(cfg.get('v3', {}).get('enabled', True))
    # V3.5 Trade Execution 增量层开关（依赖 V3.0 评分与 FE 字段；关闭时输出与 V3.1 完全一致）
    te_cfg = cfg.get('trade_execution') or {}
    te_enabled = v3_enabled and bool(te_cfg.get('enabled', False))
    rs_maps = _build_rs_maps(loader, trade_date) if v3_enabled else {}
    calib = _load_calibration(cfg)
    lookback_days = 40
    anchor_date = str(cfg.get('hvt', {}).get('anchor_date', '') or '')
    # 数据加载起点强制锚定天量锚点日（默认 20240801），不再按 550 天动态回退
    hist_start = anchor_date or (pd.to_datetime(trade_date) - pd.Timedelta(days=550)).strftime('%Y%m%d')
    # 全局交易日历：精确取"近 lookback_days 个交易日"的窗口起点日期（停牌股不挤占窗口）
    cal_all = loader.trade_dates(hist_start, trade_date)
    cal_pos = len(cal_all) - 1
    if trade_date in cal_all:
        cal_pos = cal_all.index(trade_date)
    win_start_date = cal_all[max(60, cal_pos - lookback_days)]

    # V3.6 再入 streak 基准：读取昨日报告的 BUY 池（te_buy_pool，与回测 buy_daily_top
    # 口径一致——连续处于每日 BUY 池的决策日数，首次入池=0、连续入池+1、中断归零）
    out_dir = cfg.get('report', {}).get('output_dir', os.path.join(BASE_DIR, 'report_daily'))
    os.makedirs(out_dir, exist_ok=True)
    prev_pool = {}
    if te_enabled and cal_pos > 0:
        try:
            with open(os.path.join(out_dir, f'hvt_bull_{cal_all[cal_pos - 1]}.json'), encoding='utf-8') as f:
                _prev = json.load(f)
            prev_pool = {str(it.get('ts_code')): int(it.get('reentry_streak', 0) or 0)
                         for it in (_prev.get('te_buy_pool') or []) if it.get('ts_code')}
        except Exception:
            prev_pool = {}

    events = []
    n_new = 0
    total = len(uni)
    for i, u in enumerate(uni, 1):
        code = u['ts_code']
        df = loader.load(code, hist_start, trade_date)
        if df is None or len(df) < 260:
            continue
        df = df.reset_index(drop=True)
        # 在近 lookback_days 个交易日内找 HVT 事件（从最近往回找，取最近一次）
        # 窗口起点由全局交易日历精确定位，停牌股不挤占窗口、不跨月越界
        last_idx = len(df) - 1
        start_i = int(df['trade_date'].searchsorted(win_start_date, side='left'))
        for idx in range(last_idx, max(60, start_i) - 1, -1):
            if str(df['trade_date'].iloc[idx]) > trade_date:
                continue
            ev = engine.detect_hvt(df, idx)
            if ev is None:
                continue
            ev.name = u['name']
            engine.evaluate_event(df, ev)
            if not engine.price_strength_ok(ev):
                continue
            if engine.distribution_risk(ev):
                ev.state = 'DISTRIBUTION'
            engine.update_tracking(df, ev, end_idx=last_idx + 1)
            # 上下文
            ev.fundamental_score, ev.fundamental_grade = ctx.fundamental_score(code)
            if ev.fundamental_score == 0.0:
                ev.fundamental_score = 50.0
                ev.fundamental_grade = 'B'
            ev.sector_strength, ev.sector_name = ctx.sector_resonance(code, theme_map, trade_date)
            ev.money_quality_score = ctx.money_quality(code, ev.t0_date, cfg)
            if v3_enabled:
                # ---- V3.0：双评分（ENTRY / T20_EXPANSION）+ 硬否决 + 双轴分类 ----
                engine.compute_rs(ev, rs_maps, trade_date)
                engine.entry_score(ev)
                engine.expansion_score(df, ev, asof_idx=last_idx)
                engine.hard_veto(ev)
                engine.apply_tail_calibration(ev, calib)
                engine.classify_v3(ev)
                ev.signal_tier = ev.signal_tier or 'T3'
                # V3.1 Future Expansion 增强层（只读叠加：不改V3.0状态/评分/否决，仅新增FE字段）
                rs_row = {}
                for k, v in (rs_maps.get(trade_date) or {}).items():
                    try:
                        rs_row[k] = float(v.get(code, np.nan))
                    except Exception:
                        continue
                try:
                    fe = compute_future_expansion(df, ev, rs_row)
                except Exception as _fe_err:
                    if os.environ.get('HVT_FE_DEBUG'):
                        print(f"[FE] {code} 计算异常: {_fe_err}")
                    fe = {}
                ev.fe_score = _fe_num(fe.get('fe_score'))
                ev.fe10 = _fe_num(fe.get('fe10'))
                ev.fe20 = _fe_num(fe.get('fe20'))
                ev.fe60 = _fe_num(fe.get('fe60'))
                ev.fe120 = _fe_num(fe.get('fe120'))
                ev.lifecycle = str(fe.get('lifecycle') or '')
                ev.trend_gain = _fe_num(fe.get('trend_gain'))
                ev.base_price = _fe_num(fe.get('base_price'))
                ev.base_date = str(fe.get('base_date') or '')
                ev.base_method = str(fe.get('base_method') or '')
                ev.continuation_score = _fe_num(fe.get('continuation_score'))
                ev.extension_risk = _fe_num(fe.get('extension_risk'))
                ev.expansion_type = str(fe.get('expansion_type') or '')
                ev.fe_mode = str(fe.get('mode') or '')
                ev.fe_parts = dict(fe.get('fe_parts') or {})
                ev.why_space = list(fe.get('why_space') or [])
                ev.why_risk = list(fe.get('why_risk') or [])
            else:
                engine.score(ev)
            ev.similarity_score = similarity(ev, cfg)
            ev.wait_reasons = engine.wait_reasons(ev)
            engine.build_trade_plan(ev)
            if te_enabled:
                # V3.5 Trade Execution 增量层（只读叠加：不改V3.0状态/评分/否决与候选池）
                try:
                    te = compute_trade_execution(df, ev, te_cfg)
                    for k, v in te.items():
                        setattr(ev, k, v)
                    # V3.6 再入口径：streak=昨日te_buy_pool连续入池数（首入=0、连续=昨日+1、中断归零）
                    ev.reentry_streak = (prev_pool.get(code, 0) + 1) if code in prev_pool else 0
                    # V3.6 决策点：今日是突破后第几个 pb_verdict=GOOD 回踩日（首个=PULLBACK、后续=PULLBACK_RE）
                    ev.te_decision_point = ''
                    if getattr(ev, 'pb_verdict', '') == 'GOOD':
                        ev.te_decision_point = _te_decision_point(engine, df, ev, idx, last_idx)
                except Exception as _te_err:
                    if os.environ.get('HVT_TE_DEBUG'):
                        print(f"[TE] {code} 计算异常: {_te_err}")
            events.append(ev)
            if ev.t0_date == trade_date:
                n_new += 1
            break  # 每只股票取最近一次事件
        if i % 500 == 0:
            print(f"[HVT-BULL] 进度 {i}/{total}，事件 {len(events)}")

    if v3_enabled:
        # V3 排序原则（V3§22）：TAIL > 期望收益 > ENTRY > 风险收益比 > 市场
        events.sort(key=lambda e: (-e.tail_score, -e.expansion_score, -e.entry_score, -e.score))
    else:
        events.sort(key=lambda e: e.score, reverse=True)
    top_n = top_n or int(cfg.get('report', {}).get('top_n', 20))
    out_dir = cfg.get('report', {}).get('output_dir', os.path.join(BASE_DIR, 'report_daily'))
    os.makedirs(out_dir, exist_ok=True)

    # V3.5：关闭 Trade Execution 时过滤全部新增字段，保证 JSON 与 V3.1 完全一致（§37.8）
    events_json = []
    for e in events[:top_n]:
        d = e.to_dict()
        if not te_enabled:
            for k in TE_JSON_FIELDS:
                d.pop(k, None)
        events_json.append(d)

    result = {
        'trade_date': trade_date,
        'universe': len(uni),
        'n_events': len(events),
        'n_new_today': n_new,
        'v3_enabled': v3_enabled,
        'calib_loaded': bool(calib),
        'events': events_json,
        'all_states': pd.Series([e.state for e in events]).value_counts().to_dict() if events else {},
    }
    if te_enabled:
        result['te_enabled'] = True
        result['te_max_buy'] = int(te_cfg.get('max_buy_candidates', 3))
        # V3.6：BUY 池落盘用剔除前原始 top3 作 streak 递推源（回测模拟口径：R1/R2 只过滤当日显示，不回馈 streak）
        _raw, _kept, _dropped = _te_buy_pool_filter(events, result['te_max_buy'])
        result['te_buy_pool'] = [{'ts_code': e.ts_code, 'name': e.name,
                                  'reentry_streak': int(getattr(e, 'reentry_streak', 0) or 0),
                                  'next_day_action': getattr(e, 'next_day_action', ''),
                                  'te_decision_point': getattr(e, 'te_decision_point', '') or '',
                                  'execution_score': round(float(getattr(e, 'execution_score', 0.0) or 0.0), 1)}
                                 for e in _raw]
    with open(os.path.join(out_dir, f'hvt_bull_{trade_date}.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    md = _render_md(result, events, events[:top_n])
    with open(os.path.join(out_dir, f'hvt_bull_report_{trade_date}.md'), 'w', encoding='utf-8') as f:
        f.write(md)

    print(f"[HVT-BULL] 完成: 股票池{len(uni)} 事件{len(events)} 今日新增{n_new}")
    print(f"[HVT-BULL] 状态分布: {result['all_states']}")
    return result


def _pos_suggest(e) -> str:
    """仓位建议（V3 简化版：按 TAIL/ENTRY 双高分定档，遵守总仓位约束）"""
    if e.hard_veto:
        return '-'
    if e.tail_score >= 80 and e.entry_score >= 80:
        return '15%'
    if e.tail_score >= 70 and e.entry_score >= 70:
        return '10%'
    return '5%'


def _v3_ten_questions(e) -> list:
    """PRIMARY 十问（V3§21）：为什么可能成为大牛股"""
    subs = e.exp_subs or {}
    es = e.entry_subs or {}
    q = []
    if e.breakout_date:
        q.append(f"① 为何现在启动：突破日{e.breakout_date}，距天量日仅{e.t0_to_breakout_days}个交易日，分层{e.signal_tier or 'T3'}，天量日涨{e.t0_pct_chg:.1f}%、收盘位{e.t0_close_pos:.2f}。")
    else:
        q.append(f"① 为何现在启动：天量日{e.t0_date}已出现，但尚未二次突破，处于锁筹观察期。")
    q.append(f"② 未来20日空间：扩张空间分{subs.get('扩张空间', 0):.1f}/20，距120日高点{e.dist_high_120:.1f}%（上方空间参考）。")
    q.append(f"③ 上方套牢盘：距120日高点{e.dist_high_120:.1f}%，套牢盘{'较多' if e.dist_high_120 > 20 else '相对有限' if e.dist_high_120 > 8 else '较少'}。")
    q.append(f"④ 压缩→扩张：压缩结构分{subs.get('压缩结构', 0):.1f}/15（ATR/量能/均线收敛度）。")
    if np.isfinite(e.rs5) and np.isfinite(e.rs20):
        q.append(f"⑤ RS加速：RS5={e.rs5:.0f} RS10={e.rs10:.0f} RS20={e.rs20:.0f}，加速度={e.rs_accel:+.0f}（{'快速上升' if e.rs_accel > 10 else '平稳' if e.rs_accel > 0 else '衰减'}）。")
    else:
        q.append(f"⑤ RS加速：截面数据不足，标记 SAMPLE_LOW。")
    q.append(f"⑥ 资金吸收：供给吸收分{subs.get('供给吸收', 0):.1f}/15，锁筹={'是' if e.locked_chip else '否'}，5日量缩比{e.vol_5d_ratio:.2f}，天量后回撤{e.post_max_drawdown:.1f}%。")
    q.append(f"⑦ 盈利加速：基本面{e.fundamental_grade}（{e.fundamental_score:.0f}），资金质量{e.money_quality_score:.0f}。")
    q.append(f"⑧ 催化剂：无结构化事件数据源，标记 SAMPLE_LOW（不伪造催化）。")
    risks = e.hard_veto or []
    stop_pct = abs(e.stop_loss / e.entry - 1) * 100 if e.entry else 0.0
    q.append(f"⑨ 最大失败风险：{'；'.join(risks) if risks else f'回踩失守T0_High（止损距离约{stop_pct:.1f}%）'}。")
    q.append(f"⑩ 证伪信号：放量跌破T0_High且2日不收复 → 结构止损离场；基本面恶化为C/资金质量跌破40 → 提前退出。")
    return q


def _render_md(result: dict, all_events, detail_events) -> str:
    """渲染日报：A/B/C/D 分类表基于全部事件，明细段取 top_n（detail_events）"""
    lines = ['# HVT-BULL V3.1 DAILY REPORT（T+20 右尾捕获 × Future Expansion）', '']
    lines.append(f"日期：{result['trade_date']}")
    lines.append(f"股票池：{result['universe']}只 | HVT事件：{result['n_events']}只 | 今日新增：{result['n_new_today']}只")
    v3on = result.get('v3_enabled', False)
    fe_n = sum(1 for e in all_events if getattr(e, 'fe_score', 0.0) > 0.0)
    lines.append(f"引擎：V3.0 双评分（{'启用' if v3on else '关闭'}） | 校准表：{'已加载' if result.get('calib_loaded') else '未加载→SAMPLE_LOW'} | FE增强层：已计算{fe_n}只")
    lines.append('')
    lines.append('状态分布：' + '，'.join(f"{k}={v}" for k, v in result.get('all_states', {}).items()))
    lines.append('')

    def _f(v, fmt='{:.0f}', na='N/A'):
        try:
            if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                return na
            return fmt.format(v)
        except Exception:
            return na

    def _sub(e, key, default=0.0):
        try:
            return (e.exp_subs or {}).get(key, default)
        except Exception:
            return default

    def _entry_sub(e, key, default=0.0):
        try:
            return (e.entry_subs or {}).get(key, default)
        except Exception:
            return default

    # ========== A. PRIMARY_BUY ==========
    pb = [e for e in all_events if e.state == 'PRIMARY_BUY' and not e.hard_veto]
    # V3.1 §十五/§十六：PRIMARY_BUY 内部改按 FE 排序（A=FE≥70，B=FE中高；只排序，不改入选资格）
    pb = sorted(pb, key=lambda e: (-getattr(e, 'fe_score', 0.0), -getattr(e, 'entry_score', 0.0)))
    if pb:
        lines.append('## A. ★★★ PRIMARY_BUY（ENTRY≥70 × 供给吸收≥12 × 放量A/A+ × RS20≥70，无硬否决；FE≥70标记为A）')
        lines.append('')
        lines.append('| 排名 | 级 | 代码 | 名称 | ENTRY | EXPANSION | TAIL | FE | 分层 | 触发价 | 止损 | 目标 | 仓位 | 核心理由 |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for i, e in enumerate(pb[:10], 1):
            reason = f"锁筹+突破{e.signal_tier or 'T3'}，扩张{_sub(e, '扩张空间'):.0f}/20"
            ab = 'A' if getattr(e, 'fe_score', 0.0) >= 70.0 else 'B'
            lines.append(f"| {i} | {ab} | {e.ts_code} | {e.name} | {e.entry_score:.0f} | {e.expansion_score:.0f} | {e.tail_score:.0f} | "
                         f"{_f(getattr(e, 'fe_score', None))} | {e.signal_tier or 'T3'} | {e.entry:.2f} | {e.stop_loss:.2f} | {e.target1:.2f} | "
                         f"{_pos_suggest(e)} | {reason} |")
        lines.append('')
        lines.append('### 每只 PRIMARY 的“为什么可能成为大牛股”十问')
        lines.append('')
        for e in pb[:5]:
            lines.append(f"**{e.name}（{e.ts_code}）**")
            lines.extend(f"- {q}" for q in _v3_ten_questions(e))
            lines.append('')

    # ========== B. T20_ROCKET_WATCH ==========
    rk = [e for e in all_events if e.state == 'T20_ROCKET_WATCH']
    if rk:
        lines.append('## B. 🚀 T20_ROCKET_WATCH（EXPANSION≥75，右尾潜力观察池，当前未必是最佳买点）')
        lines.append('')
        lines.append('| 排名 | 代码 | 名称 | EXPANSION | TAIL | 当前阶段 | 缺少条件 | 潜在催化 |')
        lines.append('|---|---|---|---|---|---|---|---|')
        for i, e in enumerate(rk[:15], 1):
            lacking = '入场确认(ENTRY<70)' if e.entry_score < 70 else '收盘位置'
            lines.append(f"| {i} | {e.ts_code} | {e.name} | {e.expansion_score:.0f} | {e.tail_score:.0f} | {e.state} | "
                         f"{lacking} | 扩张空间{_sub(e, '扩张空间'):.0f}，供给吸收{_sub(e, '供给吸收'):.0f} |")
        lines.append('')

    # ========== C. BREAKOUT_READY ==========
    br = [e for e in all_events if e.state == 'BREAKOUT_READY']
    if br:
        lines.append('## C. BREAKOUT_READY（结构进入候选池，扩张/确认不足）')
        lines.append('')
        lines.append('| 代码 | 名称 | HVT | ENTRY | EXPANSION | 主要阻碍 |')
        lines.append('|---|---|---|---|---|---|')
        for e in br[:20]:
            block = f"扩张分不足(<70)" if e.expansion_score < 70 else f"入场分不足(<70)"
            lines.append(f"| {e.ts_code} | {e.name} | {e.score:.0f} | {e.entry_score:.0f} | {e.expansion_score:.0f} | {block} |")
        lines.append('')

    # ========== D. FAILED / DISTRIBUTION ==========
    fd = [e for e in all_events if e.state in ('FAILED', 'DISTRIBUTION', 'EXIT')]
    if fd:
        lines.append('## D. FAILED / DISTRIBUTION / EXIT（风险对照）')
        lines.append('')
        lines.append('| 代码 | 名称 | 失败原因 | 触发时间 | 建议 |')
        lines.append('|---|---|---|---|---|')
        for e in fd[:20]:
            reason = (e.hard_veto and '；'.join(e.hard_veto)) or e.state
            lines.append(f"| {e.ts_code} | {e.name} | {reason} | {e.t0_date} | 跌破T0_High且2日不收复即离场，不接飞刀 |")
        lines.append('')

    # ========== E. RIGHT_TAIL 右侧持有跟踪（V3.3） ==========
    rt = [e for e in all_events if getattr(e, 'right_tail_hold', False)]
    if rt:
        lines.append('## E. RIGHT_TAIL 右侧持有跟踪（T+35~120 主升捕获）')
        lines.append('')
        lines.append('| 代码 | 名称 | 持有动作 | 主升高点 | 高点日期 | 距峰回撤 | MA10 | 备注 |')
        lines.append('|---|---|---|---|---|---|---|---|')
        for e in sorted(rt, key=lambda x: getattr(x, 'right_tail_dd_from_peak', 0.0))[:20]:
            dd = getattr(e, 'right_tail_dd_from_peak', 0.0)
            exit_sig = getattr(e, 'right_tail_exit', False)
            if exit_sig:
                act = 'EXIT'
                note = '回撤>15%且跌破MA10，止盈离场'
            elif dd < 8:
                act = 'HOLD'
                note = '主升中，站上MA10继续持有'
            else:
                act = 'TRIMMING'
                note = '回撤8~15%，分批兑现并上移止损'
            lines.append(f"| {e.ts_code} | {e.name} | {act} | {getattr(e, 'right_tail_max_close', 0.0):.2f} | "
                         f"{getattr(e, 'right_tail_max_date', '')} | {dd:.1f}% | {getattr(e, 'right_tail_ma10', 0.0):.2f} | {note} |")
        lines.append('')

    # ========== F. 突破回踩信号（V3.4） ==========
    pb = [e for e in all_events
          if getattr(e, 'pb_verdict', 'NA') in ('GOOD', 'NEAR')
          and e.state not in ('FAILED', 'EVENT_SPIKE')]
    if pb:
        lines.append('## F. 突破回踩信号（二次突破后缩量承接买点）')
        lines.append('')
        lines.append('判定口径：GOOD=回踩缩量(≤0.8×突破日量)+低点守住T0_High+当前收复突破收盘 | NEAR=部分满足')
        lines.append('')
        lines.append('| 代码 | 名称 | 判定 | 突破日 | 回踩低点 | 低点日期 | 缩量比 | 低点vs T0_High | 当前vs突破 |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|')
        for e in sorted(pb, key=lambda x: 0 if x.pb_verdict == 'GOOD' else 1)[:20]:
            lines.append(f"| {e.ts_code} | {e.name} | {e.pb_verdict} | {e.breakout_date} | "
                         f"{e.pb_low_close:.2f} | {e.pb_low_date} | {e.pb_shrink_ratio:.2f} | "
                         f"{e.pb_low_vs_t0high:+.1f}% | {e.pb_cur_vs_break:+.1f}% |")
        lines.append('')

    # ========== G. Future Expansion 未来扩张空间（V3.1 增强层，只排序不改V3.0状态） ==========
    fe_pool = [e for e in all_events
               if getattr(e, 'fe_score', 0.0) > 0.0
               and e.state in ('PRIMARY_BUY', 'HVT_STRONG', 'LOCKED', 'LOCKING',
                               'BREAKOUT_READY', 'T20_ROCKET_WATCH')]
    if fe_pool:
        lines.append('## G. Future Expansion 未来扩张空间（从当前价起的 T+10/20/60/120 扩张潜力/风险比）')
        lines.append('')
        lines.append('| 代码 | 名称 | 状态 | ENTRY | HVT | FE | FE10 | FE20 | FE60 | FE120 | Lifecycle | TrendGain | Continuation | ExtRisk | ExpansionType |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for e in sorted(fe_pool, key=lambda x: -getattr(x, 'fe_score', 0.0))[:20]:
            lines.append(f"| {e.ts_code} | {e.name} | {e.state} | {e.entry_score:.0f} | {e.score:.0f} | "
                         f"{_f(getattr(e, 'fe_score', None))} | {_f(getattr(e, 'fe10', None))} | {_f(getattr(e, 'fe20', None))} | "
                         f"{_f(getattr(e, 'fe60', None))} | {_f(getattr(e, 'fe120', None))} | {getattr(e, 'lifecycle', '') or '-'} | "
                         f"{getattr(e, 'trend_gain', 0.0):.0f}% | {_f(getattr(e, 'continuation_score', None))} | "
                         f"{_f(getattr(e, 'extension_risk', None))} | {getattr(e, 'expansion_type', '') or '-'} |")
        lines.append('')
        big = sorted((e for e in fe_pool if getattr(e, 'trend_gain', 0.0) > 300.0),
                     key=lambda x: -getattr(x, 'fe_score', 0.0))[:5]
        if big:
            lines.append('### 大涨股空间审辩（TrendGain>300% 强制输出：已经大涨 ≠ 已经涨完）')
            lines.append('')
            for e in big:
                lines.append(f"**{e.name}（{e.ts_code}）** TrendGain={getattr(e, 'trend_gain', 0.0):.0f}% "
                             f"Lifecycle={getattr(e, 'lifecycle', '') or '-'} Type={getattr(e, 'expansion_type', '') or '-'} "
                             f"FE={getattr(e, 'fe_score', 0.0):.0f} Continuation={getattr(e, 'continuation_score', 0.0):.0f} "
                             f"ExtRisk={getattr(e, 'extension_risk', 0.0):.0f}")
                lines.append(f"- 为什么还有空间：{'；'.join(getattr(e, 'why_space', []) or []) or '（无明确证据）'}")
                lines.append(f"- 为什么可能没有空间：{'；'.join(getattr(e, 'why_risk', []) or []) or '（无明确证据）'}")
                lines.append('')

    # ========== H. NEXT-DAY TRADE EXECUTION（V3.5 次日执行决策层，可开关） ==========
    if result.get('te_enabled'):
        lines.extend(_render_te(result, all_events))

    # ========== 全部事件明细（含 V3 双评分分解） ==========
    lines.append('## 全部事件明细（按 TAIL > EXPANSION > ENTRY > HVT分 排序）')
    lines.append('')
    for e in detail_events:
        lines.append(f"### 【{e.state}】{e.name}（{e.ts_code}）")
        lines.append('')
        lines.append(f"- V3双评分：ENTRY={e.entry_score:.1f} | EXPANSION={e.expansion_score:.1f} | TAIL={e.tail_score:.1f}"
                     f"{'(已校准)' if e.tail_calibrated else '(SAMPLE_LOW)'} | HVT分={e.score:.1f} | 分层={e.signal_tier or 'T3'}")
        lines.append(f"- ENTRY分解：价格结构{_entry_sub(e, '价格结构'):.1f} 量能{_entry_sub(e, '成交量质量'):.1f} 回踩{_entry_sub(e, '回踩结构'):.1f} "
                     f"RS{_entry_sub(e, '相对强度'):.1f} 板块{_entry_sub(e, '板块'):.1f} 盈亏比{_entry_sub(e, '风险收益比'):.1f}")
        lines.append(f"- EXPANSION分解：空间{_sub(e, '扩张空间'):.1f} 压缩{_sub(e, '压缩结构'):.1f} 动量{_sub(e, '动量加速'):.1f} "
                     f"RS加速{_sub(e, 'RS加速'):.1f} 量效{_sub(e, '量效'):.1f} 吸收{_sub(e, '供给吸收'):.1f} "
                     f"基本面{_sub(e, '基本面加速'):.1f} 催化{_sub(e, '催化剂'):.1f}")
        if getattr(e, 'fe_score', 0.0) > 0.0:
            lines.append(f"- FE未来扩张：FE={_f(e.fe_score)} FE10={_f(e.fe10)} FE20={_f(e.fe20)} FE60={_f(e.fe60)} "
                         f"FE120={_f(e.fe120)} | Lifecycle={e.lifecycle or '-'} TrendGain={e.trend_gain:.0f}% "
                         f"Continuation={_f(e.continuation_score)} ExtRisk={_f(e.extension_risk)} Type={e.expansion_type or '-'}")
            fp = getattr(e, 'fe_parts', {}) or {}
            fe_parts_str = ' '.join(f"{k}={v:.0f}" for k, v in sorted(fp.items())
                                    if isinstance(v, (int, float)) and not isinstance(v, bool))
            if fe_parts_str:
                lines.append(f"- FE分解：{fe_parts_str}")
            if getattr(e, 'trend_gain', 0.0) > 300.0:
                lines.append(f"- 为什么还有空间：{'；'.join(getattr(e, 'why_space', []) or []) or '（无明确证据）'}")
                lines.append(f"- 为什么可能没有空间：{'；'.join(getattr(e, 'why_risk', []) or []) or '（无明确证据）'}")
        lines.append(f"- 分级：收盘位置={e.close_pos_grade or '-'} 放量={e.volume_grade or '-'} "
                     f"RS5={_f(e.rs5)} RS10={_f(e.rs10)} RS20={_f(e.rs20)} RS加速度={_f(e.rs_accel, '{:+.0f}')}")
        lines.append(f"- 天量日 {e.t0_date}：涨幅{e.t0_pct_chg:.1f}% 收盘位{e.t0_close_pos:.2f} 换手{e.t0_turnover:.1f}% "
                     f"| 天量后回撤{e.post_max_drawdown:.1f}% 量缩比{e.vol_5d_ratio:.2f} 锁筹={'是' if e.locked_chip else '否'}")
        if e.breakout_date:
            lines.append(f"- 二次突破：{e.breakout_date} 放量{e.breakout_turnover_ratio:.1f}x 收盘位{e.breakout_close_pos:.2f} "
                         f"幅度{e.breakout_pct_above_t0_high:.1f}% 距T0={e.t0_to_breakout_days}日 "
                         f"假突破={'是' if e.false_breakout else '否'}")
            if getattr(e, 'pb_verdict', 'NA') != 'NA':
                lines.append(f"- 突破回踩：判定{e.pb_verdict} 缩量比{e.pb_shrink_ratio:.2f} "
                             f"低点{e.pb_low_close:.2f}@{e.pb_low_date} "
                             f"低点vsT0_High{e.pb_low_vs_t0high:+.1f}% 当前vs突破{e.pb_cur_vs_break:+.1f}%")
        lines.append(f"- 板块：{e.sector_name or '-'}（强度{_f(e.sector_strength)}） | 基本面：{e.fundamental_grade}（{_f(e.fundamental_score)}） | 资金质量：{_f(e.money_quality_score)}")
        lines.append(f"- 交易计划：入场={e.entry:.2f} 止损={e.stop_loss:.2f} 目标1={e.target1:.2f} 目标2={e.target2:.2f} 建议仓位={_pos_suggest(e)}")
        if e.hard_veto:
            lines.append(f"- ⛔ 硬否决：{'；'.join(e.hard_veto)}")
        if e.wait_reasons:
            lines.append(f"- WAIT_REASON：{'；'.join(e.wait_reasons)}")
        why = _why_like_case(e)
        if why:
            lines.append(f"- 为什么它像{why[0]}：{why[1]}")
        lines.append('')
    return '\n'.join(lines)


def _why_like_case(e):
    """规格§29：说明与哪个案例最像、差在哪（相似度辅助，不代替行情判断）"""
    from hvt_bull.engine import CASE_FEATURES
    best_code, best_name, best_sim = None, None, -1.0
    names = {'300308.SZ': '中际旭创', '603186.SH': '华正新材', '601882.SH': '海天精工'}
    for code, case in CASE_FEATURES.items():
        f = case['features']
        same_rank = (e.hvt_rank_250 <= 2) == (f['turnover_rank'] <= 2)
        same_chg = (e.t0_pct_chg >= 3) == (f['pct_chg'] >= 3)
        pts = 0
        if same_rank:
            pts += 1
        if same_chg:
            pts += 1
        if abs(e.vol_5d_ratio - f['volume_contraction']) <= 0.15:
            pts += 1
        if e.similarity_score >= 75:
            pts += 1
        if pts > best_sim:
            best_sim, best_code = pts, code
    if best_code is None:
        return None
    diffs = []
    f = CASE_FEATURES[best_code]['features']
    if e.vol_5d_ratio > f['volume_contraction'] + 0.15:
        diffs.append(f"缩量不足(量缩比{e.vol_5d_ratio:.2f} vs 案例{f['volume_contraction']:.2f})")
    if e.post_max_drawdown > f['post_drawdown'] + 4:
        diffs.append(f"回撤更深({e.post_max_drawdown:.1f}% vs 案例{f['post_drawdown']:.1f}%)")
    if not e.locked_chip:
        diffs.append('尚未锁筹')
    if not e.breakout_date:
        diffs.append('尚未二次突破')
    reason = f"同为{ '历史级' if e.hvt_rank_250 <= 2 else '阶段级'}天量(量比{e.turnover_ratio_20:.1f})、天量日价格强(涨{e.t0_pct_chg:.1f}%/收盘位{e.t0_close_pos:.2f})、相似度{e.similarity_score}"
    if diffs:
        reason += '；差异：' + '，'.join(diffs)
    return names.get(best_code, best_code), reason


TE_DP_MAX_LAG = 40  # 与 te_backtest.TE_DECISION_MAX_LAG 口径一致：突破后40个交易日内的决策才有回测证据


def _te_decision_point(engine, df, ev, b_idx: int, last_idx: int) -> str:
    """V3.6：今日是突破后第几个 pb_verdict=GOOD 回踩日。

    首个=PULLBACK、后续=PULLBACK_RE（与回测 _pullback_decision_points 同法：
    detect_hvt 重建干净 T0 事件后从突破日次日起逐日截断重放 update_tracking，
    判定完全复用 engine，重放只看 <=j 的数据，无未来函数）。
    仅在今日 pb_verdict=GOOD 时调用；超出决策窗口或重建失败返回 ''（保守保留原行为）。
    """
    if last_idx - b_idx > TE_DP_MAX_LAG:
        return ''
    try:
        ev0 = engine.detect_hvt(df, b_idx)
        if ev0 is None:
            return ''
        # 与回测采样序列一致（te_backtest L367-372）：t0_close 等字段须由 evaluate_event 初始化，
        # 否则 update_tracking 的 post_max_drawdown 会除零（t0_close=0）
        engine.evaluate_event(df, ev0)
    except Exception:
        return ''
    for j in range(b_idx + 1, last_idx + 1):
        ev_j = copy.copy(ev0)
        engine.update_tracking(df, ev_j, end_idx=j + 1)
        if getattr(ev_j, 'pb_verdict', 'NA') == 'GOOD':
            return 'PULLBACK_RE' if j < last_idx else 'PULLBACK'
    return ''


def _te_buy_pool_filter(te_pool: list, max_buy: int):
    """V3.6 BUY 池规则（与回测 R1+R2 模拟口径严格一致：先按 SCORE 取 top，再池内剔除，不补位）。

    R1：streak>=1（连续第2+日入池）且今日仅 BUY_ON_CONFIRM → 剔除（回测 pf 0.48~0.61）。
    R2：streak==0（首日入池）且今日为 PULLBACK_RE（非首个回踩 GOOD 日）→ 剔除（回测 pf 0.76）。
    保留 first/PULLBACK/BUY(pf1.61)、first/PULLBACK/BOC(pf1.47)、re2plus/PULLBACK_RE/BUY(pf1.55)。
    返回 (cand, kept, dropped)：cand=剔除前原始 top3（streak 递推源，与回测模拟口径一致）、
    kept=当日显示池、dropped=[(事件, 理由), ...]。
    """
    def _sc(e, k):
        return float(getattr(e, k, 0.0) or 0.0)

    cand = sorted((e for e in te_pool
                   if getattr(e, 'execution_state', '') in ('READY_BUY', 'PULLBACK_BUY')
                   and getattr(e, 'next_day_action', '') in ('BUY', 'BUY_ON_CONFIRM')),
                  key=lambda x: (-_sc(x, 'execution_score'), -_sc(x, 'buyability')))[:max_buy]
    kept, dropped = [], []
    for e in cand:
        streak = int(getattr(e, 'reentry_streak', 0) or 0)
        dp = getattr(e, 'te_decision_point', '') or ''
        act = getattr(e, 'next_day_action', '')
        if streak >= 1 and act == 'BUY_ON_CONFIRM':
            dropped.append((e, f'R1 再入确认买：连续第{streak + 1}日入池且今日仅BUY_ON_CONFIRM（回测pf 0.48~0.61）'))
        elif streak == 0 and dp == 'PULLBACK_RE':
            dropped.append((e, 'R2 首入再入日：首日入池但今日为非首个回踩GOOD日（回测pf 0.76）'))
        else:
            kept.append(e)
    return cand, kept, dropped


def _render_te(result: dict, all_events) -> list:
    """V3.5 §29/§30/§31/§36：NEXT-DAY TRADE EXECUTION + FINAL DECISION 渲染。

    分组：①买入候选(≤max_buy) ②突破后才买 ③高FE不追 ④等待确认 ⑤明日不买；
    EXECUTE 只取 SCORE≥85 的第一候选（每日实际执行 0~1 只），NONE 是合法结果。
    """
    max_buy = int(result.get('te_max_buy', 3))
    te_pool = [e for e in all_events if getattr(e, 'execution_state', '')]
    lines = ['============================================================',
             'NEXT-DAY TRADE EXECUTION（V3.5 次日执行决策层）',
             '============================================================', '']
    if not te_pool:
        lines.append('TODAY_EXECUTION = NONE（今日无满足执行条件的事件；空仓等待是合法结果）')
        lines.append('')
        return lines

    def _score(e, key):
        return float(getattr(e, key, 0.0) or 0.0)

    def _row(e):
        zl, zh = _score(e, 'buy_zone_low'), _score(e, 'buy_zone_high')
        return (f"| {e.ts_code} | {e.name} | {getattr(e, 'stock_type', '') or '-'} "
                f"| {_score(e, 'execution_score'):.1f} | {_score(e, 'buyability'):.1f} "
                f"| {getattr(e, 'primary_horizon', '') or '-'} | {getattr(e, 'next_day_action', '') or '-'} "
                f"| {_score(e, 'entry_trigger'):.2f} | {zl:.2f}~{zh:.2f} "
                f"| {_score(e, 'invalidation'):.2f} | {_score(e, 'no_chase_level'):.2f} "
                f"| {getattr(e, 'position_size', '') or '-'} |")

    head = ('| 代码 | 名称 | 类型 | SCORE | BUYAB | HORIZON | ACTION | TRIGGER '
            '| BUY_ZONE | INVAL | NO_CHASE | 仓位 |')
    sep = '|---|---|---|---|---|---|---|---|---|---|---|---|'

    # V3.6：BUY 池走 R1/R2 再入规则过滤（先按 SCORE 取 top、再池内剔除、不补位——与回测模拟口径严格一致）
    _, buy_pool, te_dropped = _te_buy_pool_filter(te_pool, max_buy)
    buy_codes = {e.ts_code for e in buy_pool}
    bw_pool = [e for e in te_pool if e.execution_state == 'BREAKOUT_WAIT']
    nc_pool = [e for e in te_pool
               if (e.execution_state == 'NO_CHASE' or getattr(e, 'next_day_action', '') == 'NO_CHASE')
               and e.ts_code not in buy_codes]
    wc_pool = [e for e in te_pool if e.execution_state == 'WAIT_CONFIRM' and e.ts_code not in buy_codes]
    sk_pool = [e for e in te_pool if e.execution_state == 'SKIP']

    # ① 次日买入候选（§29 PRIMARY EXECUTION / §36 明日第N优先）
    lines.append(f'### ① 次日买入候选（READY_BUY / PULLBACK_BUY，≤{max_buy}只）')
    lines.append('')
    if buy_pool:
        lines.append(head)
        lines.append(sep)
        for e in buy_pool:
            lines.append(_row(e))
        lines.append('')
        for i, e in enumerate(buy_pool, 1):
            rank = '明日第一优先' if i == 1 else f'明日第{i}优先'
            lines.append(f"**【{rank}】{e.name}（{e.ts_code}）** {e.execution_state} / {e.next_day_action} "
                         f"SCORE={_score(e, 'execution_score'):.1f} BUYABILITY={_score(e, 'buyability'):.1f}")
            lines.append(f"- 执行理由：{getattr(e, 'execution_reason', '') or '-'}")
            intra = getattr(e, 'intraday_available', False)
            lines.append(f"- 确认等级：{getattr(e, 'confirmation_level', '') or '-'} | 盘中确认："
                         + ('可用（分钟数据）' if intra
                            else 'INTRADAY_CONFIRMATION_UNAVAILABLE（无分钟数据，以量价与收盘位替代 §37.11）'))
            for pl in (getattr(e, 'open_playbook', None) or []):
                lines.append(f"- 开盘预案：{pl}")
            lines.append('')
    else:
        lines.append('（无——今日无满足条件的买入候选，TODAY_EXECUTION = NONE 是合法结果）')
        lines.append('')

    # ①R 再入规则剔除（V3.6：R1/R2 回测证据剔除，从 top 池内剔除、不补位）
    if te_dropped:
        lines.append(f'### ①R 再入规则剔除（池内剔除、不补位，{len(te_dropped)}只）')
        lines.append('')
        lines.append('| 代码 | 名称 | STREAK | DP | ACTION | SCORE | 剔除规则 |')
        lines.append('|---|---|---|---|---|---|---|')
        for e, why in te_dropped:
            lines.append(f"| {e.ts_code} | {e.name} | {int(getattr(e, 'reentry_streak', 0) or 0)} "
                         f"| {getattr(e, 'te_decision_point', '') or '-'} | {getattr(e, 'next_day_action', '') or '-'} "
                         f"| {_score(e, 'execution_score'):.1f} | {why} |")
        lines.append('')

    # ② BREAKOUT WAIT（§6：LOCKED/HVT_STRONG 尚未有效突破，不能因ENTRY高直接买）
    lines.append(f'### ② BREAKOUT WAIT（突破后才买，{len(bw_pool)}只）')
    lines.append('')
    lines.append('触发口径：次日放量突破 TRIGGER（T0_High/平台高点×确认系数）且收盘站住，才允许转为 READY_BUY')
    lines.append('')
    if bw_pool:
        lines.append(head)
        lines.append(sep)
        for e in bw_pool[:10]:
            lines.append(_row(e))
        lines.append('')
    else:
        lines.append('（无）')
        lines.append('')

    # ③ NO CHASE（§9/§18：高开过大/偏离过远/急拉爆量，FE再高也不追）
    lines.append(f'### ③ NO CHASE（高FE但不追，{len(nc_pool)}只）')
    lines.append('')
    if nc_pool:
        lines.append(head)
        lines.append(sep)
        for e in nc_pool[:10]:
            lines.append(_row(e))
        lines.append('')
    else:
        lines.append('（无）')
        lines.append('')

    # EXTENDED CONTINUATION（§21/§22：不因过去涨幅机械剔除）
    ext_pool = [e for e in te_pool if getattr(e, 'stock_type', '') == 'EXTENDED_CONTINUATION']
    if ext_pool:
        lines.append(f'### EXTENDED CONTINUATION（过去大涨但未来条件未恶化，不机械剔除，{len(ext_pool)}只）')
        lines.append('')
        for e in ext_pool[:8]:
            lines.append(f"- {e.name}（{e.ts_code}）TrendGain={_score(e, 'trend_gain'):.0f}% "
                         f"{e.execution_state} SCORE={_score(e, 'execution_score'):.1f} "
                         f"{getattr(e, 'primary_horizon', '') or '-'}")
        lines.append('')

    # ④ WAIT_CONFIRM（§8：HVT成立FE较高但确认不足/距关键位过远）
    lines.append(f'### ④ WAIT_CONFIRM（需观察确认，{len(wc_pool)}只）')
    lines.append('')
    if wc_pool:
        lines.append(head)
        lines.append(sep)
        for e in wc_pool[:10]:
            lines.append(_row(e))
        lines.append('')
    else:
        lines.append('（无）')
        lines.append('')

    # ⑤ SKIP（§10：硬风控/跌回平台/流动性与风险收益不足）
    lines.append(f'### ⑤ SKIP（明日不买，{len(sk_pool)}只）')
    lines.append('')
    if sk_pool:
        lines.append('| 代码 | 名称 | V3状态 | SCORE | 原因 |')
        lines.append('|---|---|---|---|---|')
        for e in sk_pool[:10]:
            why = '；'.join(getattr(e, 'why_not_buy', None) or []) or '—'
            lines.append(f"| {e.ts_code} | {e.name} | {e.state} | {_score(e, 'execution_score'):.1f} | {why} |")
        lines.append('')
    else:
        lines.append('（无）')
        lines.append('')

    # WHY_NOT_BUY（§31：对FE很高但未BUY的股票必须说明原因）
    lines.append('### WHY_NOT_BUY（为何未进买入候选）')
    lines.append('')
    for e, why in te_dropped[:8]:
        lines.append(f"- **{e.name}（{e.ts_code}）** STREAK={int(getattr(e, 'reentry_streak', 0) or 0)} "
                     f"DP={getattr(e, 'te_decision_point', '') or '-'} → {why}")
    for e in bw_pool[:8] + nc_pool[:8] + wc_pool[:8]:
        why = '；'.join(getattr(e, 'why_not_buy', None) or []) or '（无明确输出）'
        lines.append(f"- **{e.name}（{e.ts_code}）** FE={_score(e, 'fe_score'):.0f} "
                     f"ExtRisk={_score(e, 'extension_risk'):.0f} → {why}")
    lines.append('')

    # FINAL DECISION（§26/§30：EXECUTE=0~1，NONE 合法）
    execute = buy_pool[0] if buy_pool and _score(buy_pool[0], 'execution_score') >= 85.0 else None
    confirm_buys = [e for e in buy_pool if e is not execute]
    lines.append('============================================================')
    lines.append('FINAL DECISION')
    lines.append('============================================================')
    lines.append('')
    if execute:
        e = execute
        lines.append(f"★ 明日第一买入：{e.name}（{e.ts_code}）SCORE={_score(e, 'execution_score'):.1f} "
                     f"{getattr(e, 'primary_horizon', '')} TRIGGER={_score(e, 'entry_trigger'):.2f} "
                     f"买入区{_score(e, 'buy_zone_low'):.2f}~{_score(e, 'buy_zone_high'):.2f} "
                     f"止损{_score(e, 'invalidation'):.2f} 追高上限{_score(e, 'no_chase_level'):.2f} "
                     f"| {e.position_size}")
    else:
        lines.append('★ 明日第一买入：NONE（无SCORE≥85的候选；没有确认就不买）')
    if te_dropped:
        lines.append('★ 再入规则剔除（R1/R2，不进买入池）：'
                     + '、'.join(f"{e.name}({int(getattr(e, 'reentry_streak', 0) or 0)}连入)" for e, _ in te_dropped[:3]))
    if confirm_buys:
        lines.append('★ 确认后买入（BUY_ON_CONFIRM）：'
                     + '、'.join(f"{e.name}({_score(e, 'execution_score'):.1f})" for e in confirm_buys))
    lines.append('★ 突破后才买：' + ('、'.join(f"{e.name}({_score(e, 'entry_trigger'):.2f})" for e in bw_pool[:3]) or '无'))
    lines.append('★ 高FE但不追：' + ('、'.join(e.name for e in nc_pool[:3]) or '无'))
    lines.append('★ 明日不买：' + ('、'.join(e.name for e in sk_pool[:5]) or '无'))
    lines.append('★ 如果全部没有确认：NO TRADE')
    lines.append('')
    lines.append('核心原则：只执行“未来扩张 + 当前执行条件”同时成立的股票；没有确认就不买，'
                 '不因为FE高而追涨，不因为过去涨幅大而机械剔除。')
    lines.append('')
    return lines


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--top', type=int, default=None)
    args = ap.parse_args()
    run_daily(trade_date=args.date, top_n=args.top)
