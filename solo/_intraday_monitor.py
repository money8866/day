# -*- coding: utf-8 -*-
"""盘中动态分析器

按「关键时点」抓取实时快照 → 跑 V10.1 判定 → 输出仓位建议与时点敏感度提示。

★ 为什么这样设计（依据 _bt_intraday_timing.py 的实证）：
  用 20 个交易日 × 6 个指数的 5 分钟 K 线，测「某时点读数」与「当日收盘方向」的一致性：

    时点        方向一致率   假信号率   结论
    09:45~10:30   40~44%    29~33%   不可用于决策（甚至负相关）
    11:00~11:30   51~59%    20~24%   仅作早期预警
    13:30          72.8%      13.2%   第一个可用决策点
    14:30          92.1%       1.8%   ★ 主决策点
    14:50          94.7%       0.0%   ★ 最终确认点

  所以：
    · 09:45~10:30 只做「记录」，不做「建议」
    · 11:30 输出「初步判断 + 置信折扣」
    · 13:30 起输出「可执行建议」
    · 14:30/14:50 输出「最终仓位」

用法：
    python _intraday_monitor.py                # 自动判断当前该做什么
    python _intraday_monitor.py --slot 1430    # 指定时点
    python _intraday_monitor.py --push         # 推送到微信
"""
import os
import sys
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import market_regime_v10 as v10
import market_regime_v101 as v101
import etf_mainline as em
import _realtime_snapshot as rt
import _prev_snapshot as pvs

try:
    import strong_up_history as suh   # V10.8 第四项连续性分级
except Exception:
    suh = None

try:
    import intraday_position_anchor as ipa   # ★ V10.7.5 盘后仓位锚点（纯收紧）
except Exception:
    ipa = None

BASE = os.path.dirname(os.path.abspath(__file__))

# ★ 关键时点表：名称 -> (时刻, 决策权重, 说明)
SLOTS = {
    '0945': ('09:45', 0.00, '开盘定调（仅记录，不作决策）'),
    '1030': ('10:30', 0.00, '早盘中段（仅记录，不作决策）'),
    '1130': ('11:30', 0.55, '午盘收盘（早期预警，置信打折）'),
    '1330': ('13:30', 0.75, '午后开盘（第一个可执行决策点）'),
    '1400': ('14:00', 0.80, '午后中段（可执行）'),
    '1430': ('14:30', 0.95, '★ 主决策点（读数基本等同收盘）'),
    '1450': ('14:50', 1.00, '★ 最终确认点（尾盘决策窗口）'),
}


# ── 全市场成交额日内占比曲线（U 型），用于按"已过时段占比"外推全日成交额 ──
# 校准自 realtime_theme_monitor.py：22 个交易日(2026-08-21~09-21)沪深两市
# 5 分钟 K 线累计成交额占比均值。A 股日内呈 U 型（早盘抢筹、午后清淡、尾盘放量），
# 不能用"已过交易分钟/240"线性外推（会严重高估：9/21 13:00 线性预测 3.09 万亿
# vs 实际 2.03 万亿，高估 +52%）。正确做法：预估全日 = 当前累计 ÷ 该时点累计占比。
# 键 = 开盘后已交易分钟数（跳过 11:30~13:00 午休，全天 240 分钟），值 = 累计占比。
MARKET_AMOUNT_PROGRESS = (
    (0, 0.020), (5, 0.108), (10, 0.171), (15, 0.221), (20, 0.263),
    (30, 0.335), (45, 0.417), (60, 0.479), (75, 0.535),
    (90, 0.578), (105, 0.614), (120, 0.648),
    (135, 0.697), (150, 0.738), (165, 0.776), (180, 0.811),
    (195, 0.847), (210, 0.883), (225, 0.930), (240, 1.000),
)


def elapsed_trade_minutes_from_time(h, m):
    """给定开盘后时刻(HH,MM)，返回已完成的交易分钟数(0~240)，午休不计入"""
    t = h * 60 + m
    if t <= 9 * 60 + 30:
        return 0.0
    if t <= 11 * 60 + 30:
        return t - (9 * 60 + 30)
    if t <= 13 * 60:
        return 120.0
    if t <= 15 * 60:
        return 120.0 + (t - 13 * 60)
    return 240.0


def intraday_amount_progress(minutes):
    """按 U 型经验曲线线性插值出该时点的累计成交额占比(0~1)"""
    pts = MARKET_AMOUNT_PROGRESS
    if minutes <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if minutes <= x1:
            return y1 if x1 == x0 else y0 + (y1 - y0) * (minutes - x0) / (x1 - x0)
    return pts[-1][1]


def now_slot():
    """根据当前时间判断处于哪个时点窗口，返回 (slot_key, minutes_since_slot)"""
    t = datetime.datetime.now()
    hm = t.hour * 100 + t.minute
    for key in sorted(SLOTS, reverse=True):
        h, m = SLOTS[key][0].split(':')
        target = int(h) * 100 + int(m)
        if hm >= target:
            return key, hm - target
    return None, None


def _fetch_all(trade_date):
    """抓实时快照并修正停牌"""
    mkt = rt.fetch_market(trade_date)
    etf = rt.fetch_etf(trade_date)
    return mkt, etf


def _broadth_history(snap):
    """历史广度序列 —— 从前日快照取（收盘口径，与盘后一致）

    ★ 不再全表重算：①快 ②口径与盘后报告一致（盘中重算会混入未收盘数据）
    """
    bs = (snap or {}).get('breadth_series') or []
    return [x['ratio'] for x in bs]


def _state_via_v10(trade_date, mkt, etf, snap, ratio, zt, dt_, promo,
                   strong_mean, strong_up, ma5, ma20, hist):
    """★ V10.5：盘中仓位判定 = **真实状态机 + 真实权限表**。

    做法：
      1. 用昨日收盘快照做底（指数、成交额等盘中拿不到的字段沿用昨日值）
      2. 用实时数据覆盖 breadth / limit 的可盘中计算字段
      3. 跑 V10.0 的 detect_market_nodes + decide_state（与盘后完全同一套）
      4. 用 V10.1 的 position_authority 取该状态的仓位权限

    返回 (state, cap, reasons, decision)
    """
    import copy
    import market_regime_v10 as v10
    import market_regime_v101 as v101

    v10cfg = v10.load_config()
    v101cfg = v101.load_config()

    # ---- 1) 底：昨日收盘快照 ----
    base = None
    try:
        base = v10.collect_market_snapshot(snap.get('trade_date'), v10cfg)
    except Exception:
        base = None

    if base is None:
        # 无昨日快照 → 退化为"只有广度"的最小快照
        base = v10.MarketSnapshot(trade_date=trade_date)

    s2 = copy.deepcopy(base)
    s2.trade_date = trade_date

    # ---- 2) 覆盖 breadth ----
    #   广度历史序列口径：
    #     · 基准用**昨日收盘快照**的 _hist（收盘口径，与盘后一致）
    #     · 末位替换为今日实时值（不是追加，否则今日会被算两次）
    #     · 前日广度 = 序列倒数第二位（今日实时值的前一天）
    b = s2.breadth
    hist_full = list(b._hist or [])
    if not hist_full:
        # 无基准序列 → 用快照里的 breadth_series 兜底
        bs = snap.get('breadth_series') or []
        hist_full = [x['ratio'] for x in bs]
    if hist_full:
        hist_full = hist_full[:-1] + [ratio * 100]
        prev_ratio_pct = hist_full[-2] if len(hist_full) >= 2 else (ratio * 100)
    else:
        hist_full = [ratio * 100]
        prev_ratio_pct = ratio * 100
    b._hist = hist_full
    b.breadth_ratio = ratio
    b.prev_breadth_ratio = prev_ratio_pct / 100.0
    b.delta_pp = (ratio * 100) - prev_ratio_pct

    # ---- 3) 覆盖 limit ----
    lim = s2.limit
    lim.zt_count = zt
    lim.dt_count = dt_
    if promo is not None:
        lim.promotion_rate = promo
    if strong_mean is not None:
        lim.strong_stock_mean_pct = strong_mean
    if strong_up is not None:
        lim.strong_stock_up_ratio = strong_up / 100.0

    # ---- 4) 跑真实状态机 ----
    dec = None
    try:
        node = v10.detect_market_nodes(s2, v10cfg)
        prev = v10.load_prev_state(trade_date)
        dec = v10.decide_state(s2, node, prev, v10cfg)
        state = dec.state
        reasons = list(dec.reasons or [])
    except Exception as e:
        reasons = [f"状态机盘中计算失败：{e}"]
        state = 'S3'

    # ---- 5) 权限表 ----
    try:
        pa = v101.position_authority(state, v101cfg)
        cap = int(pa.max_position)
    except Exception:
        cap = 5
    return state, cap, reasons, dec


def _v107_intraday(trade_date, mkt, up, dn, zt, dt_, amt_est, idx_proxy,
                    ml, det, base_state, dec):
    """★ V10.7：盘中三层确认 + 状态门控（与盘后同一套判定）。

    口径说明：
      · 成交额用**按日内累计占比 U 型曲线外推的预估全日值**（盘中未定稿）
      · 指数涨跌幅用**全市场等权平均涨跌幅**作为中证2000 的盘中代理
        （新浪不提供 932000.CSI 实时；等权平均与小盘高度相关）
      · 历史序列取收盘口径的 market_micro 面板，再追加盘中合成行
    """
    try:
        import market_regime_v107 as v107
        import market_micro as micro
    except Exception as e:
        return None
    try:
        v107cfg = v107.load_config()
        # ★ 历史序列必须**排除当天**（盘后已跑过的话当天已入库），
        #   否则会把「今天」算两次。
        hist = [r for r in micro.load_series(trade_date, 12)
                if str(r['trade_date']) != str(trade_date)]
        synth = {
            'trade_date': trade_date, 'up_count': up, 'down_count': dn,
            'flat_count': 0, 'total': up + dn,
            'zt_count': zt, 'dt_count': dt_, 'amount_yi': amt_est,
            'index_chg': idx_proxy,
            'up_ratio': (up / (up + dn)) if (up + dn) else None,
        }
        series = list(hist) + [synth]

        amt = v107.amount_verdict(trade_date, v107cfg, series=series)
        easing = v107.check_easing_confirmation(trade_date, v107cfg,
                                                 micro_series=series)
        # 主线模块（盘中用 ETF 实时结果构造）
        ml_mod = None
        if ml:
            ml_mod = type('ML', (), {})()
            cands = []
            for ind in ml[:3]:
                d = det.get(ind) or {}
                cands.append({
                    'industry': ind,
                    'days_strong': d.get('days_strong', 1),
                    'excess20': d.get('excess20'),
                })
            ml_mod.details = {'industries': list(ml), 'candidates': cands}
        rebound = v107.check_rebound_confirmation(trade_date, v107cfg,
                                                  mainline_module=ml_mod,
                                                  micro_series=series,
                                                  amount=amt)
        gate = v107.apply_state_gates(base_state, dec, easing, rebound,
                                       trade_date, v107cfg,
                                       micro_series=series)
        return {'amount': amt, 'easing': easing, 'rebound': rebound,
                'gate': gate, 'effective_state': gate.effective_state,
                'synth': synth}
    except Exception as e:
        return {'error': str(e)}


def analyze(trade_date, slot_key, mkt=None, etf=None, snap=None):
    """核心分析：返回结构化结果

    snap: 前日状态快照（从盘后报告提取），提供连续计算所需的基准
    """
    cfg = v101.load_config()
    slot_time, weight, note = SLOTS[slot_key]
    if snap is None:
        # ★ 必须排除当天：盘后刚跑完时今天的报告已存在，
        #   不排除会把「今日」当成「昨日」→ 晋级率恒 100%、强股表现失真
        snap = pvs.build(before_date=trade_date)

    if mkt is None or etf is None:
        mkt, etf = _fetch_all(trade_date)

    # ---- 广度 ----
    up = int((mkt.pct_chg > 0).sum()); dn = int((mkt.pct_chg < 0).sum())
    ratio = up / (up + dn) if (up + dn) else 0.0
    from market_regime_v10 import _limit_thresholds as _v10thr
    thr = mkt.ts_code.map(_v10thr)
    zt = int(((mkt.pct_chg >= thr * 0.99) & (mkt.pct_chg < thr + 0.1)).sum())
    dt_ = int(((mkt.pct_chg <= -thr * 0.99) & (mkt.pct_chg > -thr - 0.1)).sum())
    amt_so_far = mkt.amount.sum() / 1e8
    # ★ 预估全日成交额：按"日内累计占比 U 型曲线"外推（对齐 realtime_theme_monitor.py）
    #   预估全日 = 当前累计成交额 ÷ 该时点累计占比（不能用 240/已过分钟 线性外推，
    #   否则 A 股 U 型量能分布会让早盘/午盘严重高估，9/21 13:00 曾高估 +52%）。
    #   用 slot 名义时刻（而非 datetime.now()）保证定时任务在时点附近波动时口径稳定，
    #   且午休 11:30~13:00 期间已交易分钟数固定为 120，不会误判为午后。
    _sh, _sm = (int(x) for x in slot_time.split(':'))
    _minutes = elapsed_trade_minutes_from_time(_sh, _sm)
    _progress = 1.0 if _minutes >= 240 else intraday_amount_progress(_minutes)
    amt_est = amt_so_far / _progress if _progress > 0 else amt_so_far
    amt_half = amt_est      # 变量名沿用，实际存的是「预估全日」

    hist = _broadth_history(snap)
    ma5 = float(np.mean(hist[-4:] + [ratio * 100])) if len(hist) >= 4 else None
    ma20 = float(np.mean(hist[-19:] + [ratio * 100])) if len(hist) >= 19 else None

    # ---- 主线（ETF 实时）----
    ecfg = em.load_config()
    panel = em.load_etf_panel()
    panel = panel[panel.trade_date < trade_date].copy()
    rows = []
    # ★ 单位归一（实测验证）：新浪 hq.sinajs.cn 的 amount 单位是**元**，
    #   而 etf_daily 库内存的是 tushare 的**千元**（相差 1000 倍）。
    #   不归一会让量能比 amt_ratio 虚高约 1000 倍
    #   （实测传媒 amt_ratio 显示 19.71 倍，实际约 1.3 倍）。
    #
    #   换算系数的实证依据（新浪日K volume × 收盘价 反推成交额）：
    #     159805.SZ 10/30: 53,838,000 股 × 1.030 = 55,453,140 元
    #                     库内 amount = 56,861  → 比值 975
    #     512980.SH 10/30: 358,813,600 股 × 0.740 = 265,522,064 元
    #                     库内 amount = 269,892 → 比值 984
    #     512880.SH 10/30: 1,437,634,944 股 × 0.962 = 1,383,004,816 元
    #                     库内 amount = 1,390,266 → 比值 995
    #   三只独立样本都指向 1000（差异来自 VWAP 与收盘价的微小差别）。
    for _, r in etf.iterrows():
        h = panel[panel.ts_code == r.ts_code].sort_values('trade_date')
        if h.empty:
            continue
        nav_new = h.nav.iloc[-1] * (1 + r.pct_chg / 100)
        amt_new = float(r.amount) / 1000.0     # 元 → 千元
        rows.append({'ts_code': r.ts_code, 'trade_date': trade_date,
                     'nav': nav_new, 'amount': amt_new, 'pct_chg': r.pct_chg})
    if rows:
        panel_rt = pd.concat([panel, pd.DataFrame(rows)], ignore_index=True)
        ml = em.detect_mainlines(trade_date, ecfg, panel_rt)
    else:
        ml = {'industries': [], 'details': {}}
    det = ml.get('details') or {}

    # ---- 模块判定 ----
    b = cfg['strong_uptrend']['breadth']
    m = cfg['strong_uptrend']['money_effect']
    c_breadth_ma = (ma5 is None or ma20 is None or ma5 >= ma20)
    c_breadth_lv = (ma5 is None or ma5 >= b['breadth_ma5_min'])
    c_breadth_zt = zt >= b['zt_count_min']
    c_breadth_dt = dt_ <= b['dt_count_max']
    breadth_pass = all([c_breadth_ma, c_breadth_lv, c_breadth_zt, c_breadth_dt])

    # 注：赚钱效应的细分判定放在「昨日涨停股表现」计算之后

    # ---- 状态机基准：直接取前日快照（不再重算历史）----
    st_row = snap.get('state_row') or {}
    v10r = snap.get('v10') or {}
    prev_state = (st_row.get('state') or v10r.get('state')
                  or 'S1')
    prev_days = st_row.get('consecutive_days') or v10r.get('state_days') or 1
    prev_pending = (st_row.get('pending_target') or '',
                    st_row.get('pending_days') or 0)
    prev_votes = st_row.get('vote_history') or []

    # ---- 昨日涨停股今日表现 + 连板晋级率（盘中可算！用昨日名单）----
    zt_list = snap.get('limit_up_list') or []
    promo = None
    strong_mean = None
    strong_up = None
    strong_up_cnt = None
    if zt_list and mkt is not None:
        sub = mkt[mkt.ts_code.isin(zt_list)]
        if len(sub):
            from market_regime_v10 import _limit_thresholds as _v10thr
            thr = sub.ts_code.map(_v10thr)
            pcts = sub.pct_chg.values
            strong_mean = float(pcts.mean())
            strong_up = float((pcts > 0).mean() * 100)
            strong_up_cnt = int((pcts > 0).sum())
            cont = int(((pcts >= thr.values*0.99) & (pcts < thr.values+0.1)).sum())
            promo = cont / len(zt_list) * 100

    # ---- V10.8：第四项「强股上涨比」的连续性分级（信号层，不改 money_pass）----
    #   历史序列由 daily_cache 日线重算（口径与盘中一致），当日值取实时。
    #   分级：ALERT / BOOST / REPAIR / PASS / FAIL，详见 strong_up_history.py。
    su_grade = None
    if strong_up is not None and suh is not None:
        try:
            suh.sync(trade_date, strong_up)
            su_grade = suh.classify(trade_date, strong_up, cfg)
        except Exception as e:
            su_grade = {'grade': 'NA', 'label': f'连续性数据不可用（{e}）',
                        'alert': False, 'recent': [], 'recent_str': '—',
                        'streak_repair': 0, 'streak_boost': 0, 'thresholds': {}}

    # ---- 短线赚钱效应判定（跌停/炸板率需收盘，晋级率与强股表现盘中可算）----
    c_money_dt = dt_ <= m['dt_count_max']
    c_money_promo = (promo is None) or (promo <= m.get('promotion_rate_max', 100))
    c_money_strong = (strong_mean is None) or (strong_mean >= m['strong_stock_mean_pct_min'])
    c_money_su = (strong_up is None) or (strong_up >= m['strong_stock_up_ratio_min']*100)
    money_pass = all([c_money_dt, c_money_promo, c_money_strong, c_money_su])

    # ---- 仓位建议：★ V10.5 改为调用**真实的状态机 + 权限表** ----
    #   原实现是自定义启发式（自己判 S3/S4/S5、自己定 5/20/40 的档位），
    #   与盘后的 V10.1 状态机是两套独立逻辑 → 同一天必然给出不同结论。
    #   实测 2026-11-02：盘中给 20%（启发式），盘后给 5%（状态机卡在 S3）。
    #   现改为：构造盘中快照 → 跑 V10.0 状态机 → 取 V10.1 权限表，
    #   保证**盘中与盘后同一套判定**，差异只来自数据本身的时效性。
    state_guess, cap, state_reasons, dec = _state_via_v10(
        trade_date, mkt, etf, snap, ratio, zt, dt_, promo,
        strong_mean, strong_up, ma5, ma20, hist)

    # ---- V10.7：盘中三层确认 + 状态门控 ----
    #   指数涨跌幅用「全市场等权平均涨跌幅」作为中证2000 的盘中代理
    #   （新浪无 932000.CSI 实时行情；等权平均对小盘高度相关）
    idx_proxy = None
    try:
        if mkt is not None and len(mkt):
            idx_proxy = round(float(mkt.pct_chg.mean()), 3)
    except Exception:
        idx_proxy = None
    v107 = _v107_intraday(trade_date, mkt, up, dn, zt, dt_, amt_est,
                           idx_proxy, ml.get('industries') or [], det,
                           state_guess, dec)
    if v107 and not v107.get('error') and v107.get('gate'):
        eff_state = v107['effective_state']
        if eff_state != state_guess:
            # 门控生效 → 按门控后状态取权限（且不得放大）
            try:
                from market_regime_v101 import position_authority as _pa
                base_cap = int(_pa(state_guess, cfg).max_position)
                new_cap = int(_pa(eff_state, cfg).max_position)
                cap = min(cap, new_cap, base_cap)
                state_guess = eff_state
            except Exception:
                pass

    # ---- ★ V10.7.5：盘后仓位口径桥接（只收紧，不放宽） ----
    #   此前盘中链路只跑到「V10.0 状态机 → V10.1 权限表 → V10.7 状态门控」，
    #   与盘后 10.7.4 的 P0风险 / P1P2优化 / 波段闸门 / 主升段锁存结果
    #   互不知晓 → 可能给出高于盘后最终有效目标的建议。
    #   这里只读**严格早于当日**的盘后摘要做一次 min()，不重算任何盘后逻辑、
    #   不跨日继承满仓授权、不在盘中重新触发主升段锁存。
    cap_before_anchor = cap
    anchor = None
    anchor_note = ""
    try:
        if ipa is not None:
            import market_regime_v107 as _v107mod
            _vcfg = _v107mod.load_config()
            anchor = ipa.load_anchor(trade_date, _vcfg)
            cap, anchor_note = ipa.apply_anchor_cap(cap, anchor, _vcfg)
    except Exception as e:
        anchor_note = f"盘后锚点异常 → 沿用原口径：{e}"

    cap_after_anchor = cap

    # 时点置信折扣：早盘读数不可信 → 但仓位建议本身取整到 5%，
    # 避免出现 19%、28% 这种伪精确数字（真实档位只有 5/20/30/40/60/100）
    if weight > 0 and weight < 1:
        _pre_discount = cap
        cap = int(round(cap * weight / 5.0) * 5)
        cap = min(cap, _pre_discount)   # ★ 折扣只允许收紧，防止 round 放大
    cap = max(5, min(100, cap))

    return {
        'trade_date': trade_date, 'slot': slot_key, 'slot_time': slot_time,
        'weight': weight, 'note': note,
        'ratio': ratio, 'up': up, 'dn': dn, 'zt': zt, 'dt': dt_,
        'amt_half': amt_half, 'amt_so_far': amt_so_far, 'ma5': ma5, 'ma20': ma20,
        'breadth_pass': breadth_pass, 'money_pass': money_pass,
        'ma_ok': c_breadth_ma, 'lv_ok': c_breadth_lv,
        'mainlines': ml.get('industries') or [], 'ml_details': det,
        'prev_state': prev_state, 'state_guess': state_guess, 'cap': cap,
        'state_reasons': state_reasons,
        'prev_days': prev_days, 'prev_pending': prev_pending,
        'prev_votes': prev_votes,
        'promo': promo, 'strong_mean': strong_mean, 'strong_up': strong_up,
        'strong_up_cnt': strong_up_cnt,
        'strong_up_min': m['strong_stock_up_ratio_min'] * 100,
        'strong_up_grade': su_grade,
        'zt_list_count': len(zt_list),
        'prev_date': snap.get('trade_date'),
        'prev_v10': v10r, 'prev_v101': snap.get('v101') or {},
        'prev_zt': v10r.get('zt_count'), 'prev_dt': v10r.get('dt_count'),
        'prev_breadth': v10r.get('breadth_ratio'),
        'prev_amount': v10r.get('amount'),
        'prev_broken': v10r.get('broken_rate'),
        'prev_promo': v10r.get('promotion_rate'),
        'prev_target_pos': v10r.get('target_position'),
        'v107': v107,
        'cap_before_anchor': cap_before_anchor,
        'cap_after_anchor': cap_after_anchor,
        'position_anchor': anchor.as_dict() if anchor is not None else None,
        'position_anchor_note': anchor_note,
    }


def render(r):
    L = []
    flag = '★' if r['weight'] >= 0.9 else ('○' if r['weight'] >= 0.5 else '·')
    L.append(f"{flag} 盘中动态 · {r['trade_date']} {r['slot_time']}（{r['note']}）")
    L.append("")
    L.append("【实时读数】")
    L.append(f"上涨占比 {r['ratio']*100:.1f}%（{r['up']}↑ / {r['dn']}↓）")
    L.append(f"涨停 {r['zt']} 家　跌停 {r['dt']} 家")
    L.append(f"成交额 {r['amt_so_far']:.0f} 亿（预估全日 {r['amt_half']:.0f} 亿）")
    if r['ma5'] is not None and r['ma20'] is not None:
        d = r['ma5'] - r['ma20']
        L.append(f"5日均 {r['ma5']:.1f}% vs 20日均 {r['ma20']:.1f}%（{d:+.1f}pp）"
                 f" {'✓改善' if r['ma_ok'] else '✗未改善'}")
    L.append("")
    L.append("【模块判定】")
    L.append(f"市场广度：{'✓通过' if r['breadth_pass'] else '✗未过'}"
             f"（改善方向{'✓' if r['ma_ok'] else '✗'}／底线{'✓' if r['lv_ok'] else '✗'}）")
    L.append(f"短线赚钱效应：{'✓通过' if r['money_pass'] else '✗未过'}")
    if r['promo'] is not None:
        L.append(f"　连板晋级率 {r['promo']:.1f}%（要求 ≤{35:.0f}%，过热=短期顶部）"
                 f"{'✓' if r['promo'] <= 35 else '✗'}")
    if r['strong_mean'] is not None:
        L.append(f"　昨日涨停股今日均值 {r['strong_mean']:+.2f}%"
                 f"（要求 ≥0%）{'✓' if r['strong_mean'] >= 0 else '✗'}")
        # 口径提示：这里的「上涨比」分母是**昨日涨停名单**，不是全市场，
        # 与【实时读数】的「上涨占比」（全市场 up/(up+dn)）是两个不同指标。
        # 显式写出分子/分母与阈值，避免微信文案里两个数并列造成误读。
        _su_min = r.get('strong_up_min') or 50
        _su_cnt = r.get('strong_up_cnt')
        _cnt = (f"{_su_cnt}/{r['zt_list_count']} 只" if _su_cnt is not None
                else f"共 {r['zt_list_count']} 只")
        L.append(f"　昨涨停股上涨比 {r['strong_up']:.1f}%（{_cnt}，要求 ≥{_su_min:.0f}%）"
                 f"{'✓' if r['strong_up'] >= _su_min else '✗'}")
        # V10.8：连续性分级（信号层，不改变上面的 ✓/✗ 与 money_pass）
        _g = r.get('strong_up_grade') or {}
        if _g.get('grade') and _g['grade'] != 'NA':
            _th = _g.get('thresholds') or {}
            _mark = ('⚠' if _g.get('alert') else
                     ('✓' if _g['grade'] in ('PASS', 'REPAIR', 'BOOST') else '✗'))
            L.append(f"　└ 连续性：{_mark}{_g['label']}｜近3日 {_g.get('recent_str','—')}"
                     f"｜≥{_th.get('repair_min',50):.0f}%连续 {_g.get('streak_repair',0)} 日"
                     f"、≥{_th.get('boost_min',55):.0f}%连续 {_g.get('streak_boost',0)} 日")
        elif _g.get('grade') == 'NA':
            L.append(f"　└ 连续性：{_g.get('label','不可用')}")
    if r['mainlines']:
        for ind in r['mainlines'][:2]:
            x = r['ml_details'][ind]
            L.append(f"主线：{ind}｜连续强态 {x.get('days_strong')}日｜"
                     f"超额20日 {x['excess20']:.1f}%")
    else:
        L.append("主线：无")
    L.append("")
    L.append(f"【前日对照】{r['prev_date']}")
    L.append(f"　广度 {r['prev_breadth']}%　涨停 {r['prev_zt']}　跌停 {r['prev_dt']}"
             f"　炸板率 {r['prev_broken']}%　晋级率 {r['prev_promo']}%")
    L.append(f"　成交额 {r['prev_amount']} 亿　建议仓位 {r['prev_target_pos']}%"
             f"　昨日涨停名单 {r['zt_list_count']} 只")
    L.append("")
    L.append("【仓位建议】")
    L.append(f"上一交易日状态：{r['prev_state']}（持续 {r['prev_days']} 日）")
    L.append(f"当前推断：{r['state_guess']}")
    # ---- V10.7 三项确认（盘中）----
    v7 = r.get('v107') or {}
    if v7 and not v7.get('error') and v7.get('easing'):
        L.append("")
        L.append("【V10.7 三项确认】")
        L.append(f"卖压衰减确认：{v7['easing'].verdict}　"
                 f"反弹确认：{v7['rebound'].verdict}")
        amt = v7.get('amount')
        if amt is not None:
            L.append(f"量价：{amt.pattern}｜环比 "
                     + (f"{amt.chg_vs_prev:+.1f}%" if amt.chg_vs_prev is not None else "UNKNOWN")
                     + "｜相对5日均 "
                     + (f"{amt.chg_vs_ma5:+.1f}%" if amt.chg_vs_ma5 is not None else "UNKNOWN")
                     + f"｜指数(等权代理) {amt.index_chg:+.2f}%"
                     if amt.index_chg is not None else
                     "量价：数据不足")
        g = v7.get('gate')
        if g:
            for b in (g.blocked or []):
                L.append(f"  ⚠ {b}")
            for a in (g.applied or []):
                L.append(f"  ✓ {a}")
    # ---- ★ V10.7.5 盘后仓位基准审计（只收紧） ----
    try:
        if ipa is not None:
            L.extend(ipa.render_anchor_lines(
                r.get('position_anchor'),
                int(r.get('cap_before_anchor', r['cap'])),
                int(r.get('cap_after_anchor', r['cap'])),
                r.get('position_anchor_note') or ""))
    except Exception:
        pass
    if r['weight'] >= 0.5:
        L.append(f"建议仓位：{r['cap']}%")
        if r['weight'] < 0.9:
            L.append(f"※ 时点折扣已计入（该时点读数与收盘方向一致率不足）")
    else:
        # 早盘读数与收盘方向几乎无关 → 不给仓位，避免自相矛盾
        L.append("建议仓位：本时点不给出（早盘读数与收盘方向几乎无关）")
        L.append(f"参考：若按当前读数推断为 {r['state_guess']}（{r['cap']}%），仅供记录，不作决策依据")
    L.append("")
    if r['weight'] >= 0.9:
        L.append("💡 该时点读数基本等同收盘，可作为最终仓位依据。")
    elif r['weight'] >= 0.5:
        L.append("💡 午后开盘是第一个可执行决策点，但仍有 13% 假信号率，建议等 14:30 确认。")
    else:
        L.append("💡 早盘读数与收盘方向几乎无关（方向一致率约 40%），请勿据此操作。")
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slot', help='时点 key: ' + ','.join(SLOTS))
    ap.add_argument('--date', help='交易日标签 YYYYMMDD')
    ap.add_argument('--push', action='store_true', help='推送到微信')
    args = ap.parse_args()

    today = datetime.datetime.now().strftime('%Y%m%d')
    trade_date = args.date or today

    slot = args.slot
    if not slot:
        slot, _ = now_slot()
        if slot is None:
            print('当前不在交易时段')
            return
    if slot not in SLOTS:
        print(f'未知时点 {slot}，可选：{", ".join(SLOTS)}')
        return

    print(f'分析 {trade_date} {SLOTS[slot][0]} …')
    r = analyze(trade_date, slot)
    msg = render(r)
    print()
    print('=' * 66)
    print(msg)
    print('=' * 66)

    if args.push:
        try:
            from wechat_clawbot_push_local import push
            push(msg)
            print('\n✓ 已推送微信')
        except Exception as e:
            print(f'\n推送失败: {e}')


if __name__ == '__main__':
    main()
