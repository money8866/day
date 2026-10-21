# -*- coding: utf-8 -*-
"""MSW 波段信号引擎（任务书 §11 signal_engine.py：MSW-C）

三个相互独立的交易子策略（任务书 §六）：
  C1 长期趋势中的缩量回踩     —— 主策略
  C2 波段区间内的均值回归     —— 独立可选
  C3 回调结束后的二次突破     —— 独立可选
优先级 C1 > C3 > C2（§6.4），单股同时只允许一个持仓。

卖出/止盈/再入场（§七）：分级止盈（1×ATR 兑现 50%，2×ATR/前高兑现剩余）、
追踪止盈、趋势失效退出、冷却后再入场（需新结构 + 风险收益比门槛）。

状态机（§12）：WATCH / BUY_SETUP / BUY_CONFIRMED / HOLD / REDUCE / EXIT / WAIT_REENTRY。

无未来函数：step(t) 只读取 feats 第 t 行及之前的数据；理想成交假设 T+1 开盘价
（真实执行约束由 backtest.py 负责，本模块产出事件流）。
"""
import bisect
import numpy as np

from msw import scanner as SC          # 趋势过滤 / 质量评分（MSW-A/B）

WATCH = 'WATCH'
BUY_SETUP = 'BUY_SETUP'
BUY_CONFIRMED = 'BUY_CONFIRMED'
HOLD = 'HOLD'
REDUCE = 'REDUCE'
EXIT = 'EXIT'
WAIT_REENTRY = 'WAIT_REENTRY'

RULE_PRIORITY = ('C1', 'C3', 'C2')


# ---------------------------------------------------------------- 持仓状态

class PosState:
    """理想持仓状态（单股）"""

    __slots__ = ('entry_bar', 'entry_date', 'entry_price', 'rule', 'quality',
                 'atr_at_entry', 'stage_high_at_entry', 'pullback_low',
                 'ma120_at_entry', 'high_watermark', 'bars_held',
                 'tp1_done', 'stop_level', 'tp1_level', 'tp2_level')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


# ---------------------------------------------------------------- 规则评估

def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return np.nan
    return v if np.isfinite(v) else np.nan


def eval_c1(row, sh, st, trend_pass, cfg):
    """C1 缩量回踩。返回 (status, detail)；status: 'buy'/'setup'/None"""
    c1 = cfg.get('signal_c1_pullback', {})
    if not c1.get('enabled', True) or not trend_pass:
        return None, {}
    close = _f(row['hfq_close'])
    atr = _f(row['atr20'])
    if not np.isfinite(close) or not np.isfinite(atr) or atr <= 0:
        return None, {}
    shp = sh.stage_high(row['_t'], 250)
    if not shp:
        return None, {}
    sh_price, sh_bar = shp
    depth = 1.0 - close / sh_price
    atr_mult = (sh_price - close) / atr
    in_band = ((c1['pullback_min_pct'] <= depth <= c1['pullback_max_pct'])
               or (c1['pullback_min_atr'] <= atr_mult <= c1['pullback_max_atr']))
    if not in_band:
        return None, {'depth': depth, 'atr_mult': atr_mult}

    # 支撑接近：MA20 / MA60 / 前突破位 任一在 3% 内（不要求精准触及，§C1 注）
    cands = []
    for col in ('ma20', 'ma60'):
        v = _f(row.get(col))
        if np.isfinite(v) and v > 0:
            cands.append(abs(close / v - 1.0))
    pb = sh.prev_breakout_level(row['_t'], 250)
    if pb and pb > 0:
        cands.append(abs(close / pb - 1.0))
    support = min(cands) if cands else np.nan
    near = np.isfinite(support) and support <= c1['support_near_pct']

    # 缩量：5 日均量 <= 0.75 × 20 日均量（回撤卖压衰减）
    a5, a20 = _f(row['amount_ma5']), _f(row['amount_ma20'])
    vol_contract = np.isfinite(a5) and np.isfinite(a20) and a20 > 0 \
        and a5 <= c1['vol_contract_max'] * a20
    if not c1.get('require_vol_contract', True):
        vol_contract = True                 # 消融项 c1_volume_cond：关闭缩量条件

    # 转强：收复 MA5 或突破近 10 日高点（不含当日，high_10_prev 列在行走器预计算）
    h10_prev = _f(row.get('high_10_prev'))
    ma5 = _f(row['ma5'])
    reclaim = (np.isfinite(ma5) and close > ma5) or \
              (np.isfinite(h10_prev) and close > h10_prev)

    detail = {'depth': depth, 'atr_mult': atr_mult, 'support': support,
              'vol_contract': vol_contract, 'reclaim': reclaim}
    if near and vol_contract and reclaim:
        return 'buy', detail
    if near or vol_contract:
        return 'setup', detail
    return None, detail


def eval_c2(row, st, trend_pass, cfg):
    """C2 均值回归（服从趋势过滤）。返回 (status, detail)"""
    c2 = cfg.get('signal_c2_meanrev', {})
    if not c2.get('enabled', True):
        return None, {}
    if c2.get('require_trend_filter', True) and not trend_pass:
        return None, {}
    rsi = _f(row['rsi6'])
    dev = _f(row['dev_ma20'])
    boll = _f(row['boll_pos'])
    oversold = ((np.isfinite(rsi) and rsi <= c2['rsi_max'])
                or (np.isfinite(dev) and dev <= c2['dev_ma20_max'])
                or (np.isfinite(boll) and boll <= c2['boll_pos_max']))
    if not oversold:
        return None, {}
    vr20 = _f(row['vr20'])
    no_panic = (not np.isfinite(vr20)) or vr20 <= c2['max_vol_ratio']
    detail = {'rsi6': rsi, 'dev_ma20': dev, 'boll_pos': boll, 'vr20': vr20}
    return ('buy' if no_panic else None), detail


def eval_c3(row, feats, t_idx, sh, trend_pass, cfg):
    """C3 二次突破。返回 (status, detail)

    性能预检查：先验证突破日条件（涨幅/量能/跳空），再扫描整理窗口。
    row 为 numpy 行字典；feats 仅用于整理窗口切片（低频路径）。
    """
    c3 = cfg.get('signal_c3_breakout', {})
    if not c3.get('enabled', True) or not trend_pass:
        return None, {}
    close = _f(row['hfq_close'])
    pct = _f(row['pct_chg'])
    amt = _f(row['amount'])
    a20 = _f(row['amount_ma20'])
    if not np.isfinite(close):
        return None, {}
    # 跳空不追
    pre_close = _f(row['pre_close'])
    gap = (_f(row['open']) / pre_close - 1.0) if pre_close > 0 else 0.0
    if np.isfinite(gap) and gap > c3['max_gap_up_pct']:
        return None, {'gap': gap}
    # 预检查：突破日量价条件
    if not (np.isfinite(pct) and pct >= c3['breakout_min_pct']):
        return None, {}
    if not (np.isfinite(amt) and np.isfinite(a20) and a20 > 0
            and amt >= c3['breakout_vol_ratio'] * a20):
        return None, {}
    n_cons = None
    cons_high = None
    lo, hi = int(c3['consolidation_min_days']), int(c3['consolidation_max_days'])
    for w in range(lo, hi + 1):
        seg = feats.iloc[t_idx - w:t_idx]           # 整理区间 = [T-w, T-1]
        if len(seg) < w:
            continue
        hi_p = _f(seg['hfq_high'].max())
        lo_p = _f(seg['hfq_low'].min())
        if not (np.isfinite(hi_p) and np.isfinite(lo_p)) or lo_p <= 0:
            continue
        rng = (hi_p - lo_p) / lo_p
        if rng > c3['consolidation_max_range']:
            continue
        # 缩量整理：整理期均量 <= 0.8 × 整理前 20 日均量
        pre = feats.iloc[max(0, t_idx - w - 20):t_idx - w]
        cons_amt = _f(seg['amount'].mean())
        pre_amt = _f(pre['amount'].mean()) if len(pre) >= 10 else np.nan
        vol_ok = (not np.isfinite(pre_amt)) or pre_amt <= 0 or \
                 cons_amt <= c3['pre_vol_contract_max'] * pre_amt
        # 回撤上下文：近 60 日自阶段高点回撤过 >= 6%
        shp = sh.stage_high(t_idx, 120)
        dip_ok = False
        if shp:
            sh_price = shp[0]
            win = feats.iloc[max(0, t_idx - 60):t_idx]
            dip = (sh_price - _f(win['hfq_close'].min())) / sh_price
            dip_ok = dip >= cfg.get('signal_c1_pullback', {}).get('pullback_min_pct', 0.06)
        if vol_ok and dip_ok:
            n_cons = w
            cons_high = hi_p
            break
    if n_cons is None:
        return None, {}
    breakout = close > cons_high
    detail = {'cons_days': n_cons, 'cons_high': cons_high, 'pct': pct}
    return ('buy' if breakout else 'setup'), detail


# ---------------------------------------------------------------- 退出评估

def eval_exit(row, pos, rs_break_streak, cfg):
    """持仓管理。返回 (action, reason)；action: 'hold'/'reduce'/'exit'

    趋势失效退出按入场规则区别对待（§7.2）：
      C1/C3（趋势跟随入场）：收盘跌破 MA120 即失效；
      C2（均值回归入场）：入场价本就在 MA120 下方，改用回撤低点/RS/时间止损，
      不以 MA120 为失效条件（否则信号当日即被止损，属规则错配）。
    """
    ex = cfg.get('exit', {})
    close = _f(row['hfq_close'])
    atr = _f(row['atr20'])
    ma120 = _f(row.get('ma120'))
    if not np.isfinite(close):
        return 'hold', 'no_data'
    pos.high_watermark = max(pos.high_watermark or close, close)
    pos.bars_held += 1

    # 1) 趋势失效
    if ex.get('invalidation_close_below_ma120', True) and pos.rule in ('C1', 'C3') \
            and np.isfinite(ma120) and close < ma120:
        return 'exit', 'close_below_ma120'
    if pos.pullback_low and close < pos.pullback_low * (
            1.0 - ex.get('invalidation_below_pullback_low_pct', 0.03)):
        return 'exit', 'below_pullback_low'
    if rs_break_streak >= ex.get('invalidation_rs_break_days', 10):
        return 'exit', 'rs_breakdown'
    if pos.bars_held >= ex.get('max_hold_days', 120):
        return 'exit', 'max_hold_days'

    # 2) 分级止盈
    atr_e = pos.atr_at_entry if (pos.atr_at_entry and np.isfinite(pos.atr_at_entry)) else atr
    if not pos.tp1_done:
        tp1_price = (pos.entry_price + ex['tp1_atr'] * atr_e) if atr_e else np.inf
        near_high = pos.stage_high_at_entry and close >= pos.stage_high_at_entry * (
            1.0 - ex.get('prior_high_near_pct', 0.02))
        if (np.isfinite(tp1_price) and close >= tp1_price) or near_high:
            return 'reduce', 'tp1'
    else:
        tp2_price = (pos.entry_price + ex['tp2_atr'] * atr_e) if atr_e else np.inf
        if np.isfinite(tp2_price) and close >= tp2_price:
            return 'exit', 'tp2'
        if np.isfinite(atr) and atr > 0 and close <= pos.high_watermark - ex['trail_atr'] * atr:
            return 'exit', 'trailing_stop'
    return 'hold', ''


def risk_reward(row, sh, cfg):
    """风险收益比 = 至阶段高点空间 / 至止损空间；止损 = min(近20日低点, MA120)"""
    close = _f(row['hfq_close'])
    if not np.isfinite(close) or close <= 0:
        return None, None
    shp = sh.stage_high(row['_t'], 250)
    reward = (shp[0] / close - 1.0) if shp else None
    low20 = _f(row.get('low_20'))
    ma120 = _f(row.get('ma120'))
    stops = [v for v in (low20, ma120) if np.isfinite(v) and v > 0 and v < close]
    if not stops or reward is None:
        return None, None
    stop = min(stops)
    risk = (close - stop) / close
    if risk <= 0:
        return None, stop
    return reward / risk, stop


# ---------------------------------------------------------------- 单股行走器

class StockWalker:
    """单股理想行走器：逐日状态机 + 事件流（T+1 开盘理想成交）

    records: 每日状态记录（§12 字段，start_idx 起）
    events : 交易事件流（ENTRY/REDUCE/EXIT，date 为信号日 T；成交价由回测决定）
    """

    def __init__(self, ts_code, feats, sh, cfg, panel=None, name='',
                 industry='', fina_series=None, start_idx=0, end_idx=None,
                 uni_mask=None):
        self.ts_code = ts_code
        self.feats = feats
        self.sh = sh
        self.cfg = cfg
        self.panel = panel
        self.name = name or ts_code
        self.industry = industry or '未知'
        self.fina_series = fina_series or []
        self._fina_dates = [r['ann_date'] for r in self.fina_series]
        self.start_idx = start_idx
        self.end_idx = (len(feats) - 1) if end_idx is None else end_idx
        self._uni_mask = uni_mask          # 与 feats 等长的 bool 数组（或 None=不判定）
        # 20 日成交额中位数（万元）列
        amt_wan = feats['amount'] / 10.0
        self._amt_med20 = amt_wan.rolling(20, min_periods=20).median().to_numpy(dtype=float)
        # 前 10 日高点（不含当日）——C1 转强确认用
        feats['high_10_prev'] = feats['high_10'].shift(1)
        # 相对强度预计算（向量化对齐，O(1) 逐日查询）
        self._rs120 = self._rs250 = self._ind_rs120 = None
        if panel is not None:
            dates = feats['trade_date'].astype(str)
            b120 = panel.bench_cum.get(120)
            b250 = panel.bench_cum.get(250)
            ind = panel.industry.get(ts_code, '未知')
            i120 = panel.ind_cum.get((ind, 120))
            s120 = feats['ret_120d'].to_numpy(dtype=float)
            s250 = feats['ret_250d'].to_numpy(dtype=float)
            if b120 is not None:
                self._rs120 = s120 - b120.reindex(dates).to_numpy(dtype=float)
            if b250 is not None:
                self._rs250 = s250 - b250.reindex(dates).to_numpy(dtype=float)
            if i120 is not None:
                self._ind_rs120 = s120 - i120.reindex(dates).to_numpy(dtype=float)
        # 运行状态
        self.pos = None
        self.pb_active = False
        self.cooldown_until = None          # bar index
        self.rs_break_streak = 0
        self.records = []
        self.events = []
        self._snap_i = -1
        self._st = None
        # numpy 行缓存（逐日行访问热点优化：dict 行代替 DataFrame.iloc）
        self._row_cols = (
            'hfq_close', 'hfq_open', 'atr20', 'ma5', 'ma20', 'ma60', 'ma120',
            'ma120_slope20', 'ma250_slope20', 'ret_120d', 'ret_250d',
            'dist_high_250d', 'ma_align_score', 'atr_pct', 'rsi6', 'dev_ma20',
            'boll_pos', 'vr20', 'amount_ma5', 'amount_ma20', 'amount',
            'pct_chg', 'pre_close', 'open', 'high_10_prev', 'low_20',
            'mdd_250d', 'close')
        self._np = {}
        for c in self._row_cols:
            if c in feats.columns:
                self._np[c] = feats[c].to_numpy(dtype=float)
        self._dates_arr = feats['trade_date'].astype(str).to_numpy()

    def _row(self, t):
        """第 t 行的轻量 dict 视图（含 _t 供 swing 查询）"""
        row = {'_t': t}
        for c in self._row_cols:
            arr = self._np.get(c)
            if arr is not None and t < len(arr):
                row[c] = float(arr[t])
        return row

    # ---------------- 内部 ----------------

    def _stats(self, t):
        """截至 t 的 250 日波段统计（快照指针，仅在新段确认时重算）"""
        bars = self.sh._snap_bars
        while self._snap_i + 1 < len(bars) and bars[self._snap_i + 1] <= t:
            self._snap_i += 1
            self._st = None
        if self._st is None:
            legs = self.sh._snap_legs[self._snap_i] if self._snap_i >= 0 else []
            win = [lg for lg in legs if lg['end_bar'] >= t - self.cfg['swing']['swing_stats_window']]
            self._st = _stats_from_legs(win, t, self.cfg['swing']['recovery_deadline_days'])
        return self._st

    def _rs(self, t):
        def g(arr):
            if arr is None or t >= len(arr):
                return np.nan
            v = arr[t]
            return float(v) if np.isfinite(v) else np.nan
        return {'rs_120d': g(self._rs120), 'rs_250d': g(self._rs250),
                'industry_rs_120d': g(self._ind_rs120)}

    def _funda_state(self, t):
        if not self.cfg.get('fundamental', {}).get('enabled', True):
            return 'neutral'                # 消融项 fundamental：关闭基本面分层
        date = str(self._dates_arr[t])
        i = bisect.bisect_right(self._fina_dates, date) - 1
        if i < 0:
            return 'unknown'
        return SC.fundamental_state(self.fina_series[i], self.cfg)

    # ---------------- 主循环 ----------------

    def run(self):
        for t in range(self.start_idx, self.end_idx + 1):
            self.step(t)
        return self.records, self.events

    def step(self, t):
        cfg = self.cfg
        row = self._row(t)
        date = str(self._dates_arr[t])
        st = self._stats(t)
        rs = self._rs(t)
        funda = self._funda_state(t)
        uni_row = {'stage_high': self.sh.stage_high(t, 250),
                   'amount_med20_wan': _f(self._amt_med20[t])}
        uni_ok = bool(self._uni_mask[t]) if self._uni_mask is not None else True

        tf = SC.trend_filter(row, st, rs, funda, uni_ok, cfg)
        qs = SC.quality_score(row, st, rs, uni_row, cfg)
        trend_pass = tf['pass']
        if not cfg.get('trend_filter', {}).get('enabled', True):
            trend_pass = uni_ok              # 消融项 trend_filter：仅保留股票池过滤
        # 市场状态过滤（消融项 regime_filter，默认关闭）
        regime_block = False
        mr = cfg.get('market_regime', {})
        if mr.get('enabled', False) and self.panel is not None:
            br = self.panel.breadth(date)
            if np.isfinite(br) and br < float(mr.get('bear_max_breadth', 0.35)):
                regime_block = True
        # 质量分入场门槛（消融项 quality_score）
        q_gate_ok = True
        qcfg = cfg.get('quality_score', {})
        if qcfg.get('gate_entries', True) and qs['total'] is not None:
            q_gate_ok = qs['total'] >= float(qcfg.get('min_score', 55.0))

        # 回撤结构跟踪（C1 前置）
        shp = uni_row['stage_high']
        close = _f(row['hfq_close'])
        if shp and np.isfinite(close):
            depth = 1.0 - close / shp[0]
            c1 = cfg['signal_c1_pullback']
            if depth >= c1['pullback_min_pct']:
                self.pb_active = True
            if depth > c1['pullback_max_pct'] * 1.3 or depth < 0.01:
                self.pb_active = False

        action, rule, reason, ref = 'wait', '', '', None
        state = WATCH

        if self.pos is not None:
            # ---- 持仓管理 ----
            act, rsn = eval_exit(row, self.pos, self.rs_break_streak, cfg)
            if act == 'exit':
                state = EXIT
                action, rule, reason = 'exit', self.pos.rule, rsn
                ref = close
                self._close_pos(t, date, 'EXIT', rsn)
            elif act == 'reduce':
                state = REDUCE
                action, rule, reason = 'reduce', self.pos.rule, rsn
                ref = close
                self._reduce_pos(t, date, rsn)
            else:
                state = HOLD
                action = 'hold'
        else:
            # ---- 冷却期 ----
            if self.cooldown_until is not None and t < self.cooldown_until:
                state = WAIT_REENTRY
            else:
                # 优先级 C1 > C3 > C2
                c1_status, c1_detail = eval_c1(row, self.sh, st,
                                               trend_pass, cfg)
                c3_status, c3_detail = eval_c3(row, self.feats, t, self.sh,
                                               trend_pass, cfg)
                c2_status, c2_detail = eval_c2(row, st, trend_pass, cfg)
                rr, stop = risk_reward(row, self.sh, cfg)
                rr_ok = rr is not None and rr >= cfg['exit']['reentry_min_rr']

                chosen = None
                if c1_status == 'buy':
                    chosen = ('C1', c1_detail)
                elif c3_status == 'buy':
                    chosen = ('C3', c3_detail)
                elif c2_status == 'buy':
                    chosen = ('C2', c2_detail)
                if chosen and rr_ok and q_gate_ok and not regime_block:
                    rule, detail = chosen
                    state = BUY_CONFIRMED
                    action, reason = 'buy', f'{rule}_confirmed'
                    ref = close
                    self._open_pos(t, date, rule, detail, rr, stop, qs['total'])
                elif chosen is not None and not rr_ok:
                    # 结构就绪但风险收益比不足：等待（与 C2 口径一致，§6.4）
                    state = BUY_SETUP
                    action = 'setup'
                elif c1_status == 'setup' or c3_status == 'setup':
                    state = BUY_SETUP
                    action = 'setup'
                else:
                    state = WATCH

        # RS 连续恶化跟踪（失效条件之一）
        rs120 = rs.get('rs_120d')
        if rs120 is not None and np.isfinite(rs120) and rs120 < 0:
            self.rs_break_streak += 1
        else:
            self.rs_break_streak = 0

        self.records.append(self._record(t, date, state, action, rule, reason,
                                         ref, st, rs, qs, tf, funda, uni_row, uni_ok, row))

    # ---------------- 持仓操作（理想成交：T+1 开盘） ----------------

    def _fill(self, t):
        """T+1 开盘理想成交；返回 (fill_date, fill_price) 或 (None, None)"""
        j = t + 1
        if j > self.end_idx:
            return None, None
        op = self._np['hfq_open'][j]
        return str(self._dates_arr[j]), (float(op) if np.isfinite(op) else None)

    def _open_pos(self, t, date, rule, detail, rr, stop, quality=None):
        fill_date, fill_price = self._fill(t)
        if fill_price is None or not np.isfinite(fill_price) or fill_price <= 0:
            return
        row = self._row(t)
        self.pos = PosState(
            entry_bar=t + 1, entry_date=fill_date, entry_price=fill_price,
            rule=rule, quality=quality, atr_at_entry=_f(row['atr20']),
            stage_high_at_entry=(self.sh.stage_high(t, 250) or (None,))[0],
            pullback_low=_f(row['low_20']), ma120_at_entry=_f(row['ma120']),
            high_watermark=fill_price, bars_held=0, tp1_done=False,
            stop_level=stop, tp1_level=None, tp2_level=None)
        self.pb_active = False
        self.events.append({'type': 'ENTRY', 'signal_date': date, 'rule': rule,
                            'fill_date': fill_date, 'fill_price': fill_price,
                            'rr': rr, 'quality': quality,
                            'detail': {k: (round(v, 6) if isinstance(v, float) else v)
                                       for k, v in detail.items()}})

    def _reduce_pos(self, t, date, rsn):
        fill_date, fill_price = self._fill(t)
        if fill_price is None:
            return
        self.pos.tp1_done = True
        self.events.append({'type': 'REDUCE', 'signal_date': date,
                            'fill_date': fill_date, 'fill_price': fill_price,
                            'frac': self.cfg['exit']['tp1_frac'], 'reason': rsn})

    def _close_pos(self, t, date, kind, rsn):
        fill_date, fill_price = self._fill(t)
        pos, self.pos = self.pos, None
        if fill_price is None:
            fill_date, fill_price = date, _f(self._np['hfq_close'][t])
        self.cooldown_until = t + 1 + int(self.cfg['exit']['cooldown_after_exit_days'])
        self.events.append({'type': kind, 'signal_date': date,
                            'fill_date': fill_date, 'fill_price': fill_price,
                            'frac': 1.0, 'reason': rsn,
                            'rule': pos.rule if pos else '',
                            'entry_date': pos.entry_date if pos else '',
                            'entry_price': pos.entry_price if pos else None})

    # ---------------- 记录 ----------------

    def _record(self, t, date, state, action, rule, reason, ref, st, rs, qs,
                tf, funda, uni_row, uni_ok, row):
        ex = self.cfg['exit']
        if state in (BUY_CONFIRMED,):
            entry_ref = f"T+1开盘; 触发={rule}; RR>={ex['reentry_min_rr']}"
        elif state == BUY_SETUP:
            entry_ref = (f'等待风险收益比>= {ex["reentry_min_rr"]} '
                         '或转强确认(收复MA5/突破10日高)')
        elif state == HOLD:
            entry_ref = ''
        else:
            entry_ref = ''
        if self.pos is not None:
            exit_ref = (f"TP1=entry+{ex['tp1_atr']}ATR; TP2=entry+{ex['tp2_atr']}ATR; "
                        f"追踪={ex['trail_atr']}ATR; 失效=跌破MA120/回撤低点-"
                        f"{int(ex['invalidation_below_pullback_low_pct']*100)}%")
            inval = '收盘跌破MA120 或 跌破回撤低点-3% 或 相对强度连续10日恶化'
        else:
            exit_ref = ''
            inval = '长期趋势失效(MA120走平转下/相对强度转负/回撤超18%)'
        return {
            'trade_date': date,
            'ts_code': self.ts_code,
            'stock_name': self.name,
            'long_trend_score': _r3(qs['sub']['trend_quality']),
            'msw_quality_score': _r3(qs['total']),
            'swing_count_250d': st.get('swing_count'),
            'median_swing_return': _r4(st.get('up_ret_median')),
            'pullback_recovery_rate': _r3(st.get('recovery_rate')),
            'median_recovery_days': _r1(st.get('recovery_days_median')),
            'relative_strength_120d': _r4(rs.get('rs_120d')),
            'liquidity_score': _r3(SC.liquidity_score(uni_row, row)),
            'fundamental_state': funda,
            'current_signal': state,
            'signal_action': action,
            'signal_rule': rule,
            'signal_reason': reason,
            'entry_reference': entry_ref,
            'exit_reference': exit_ref,
            'invalidation_condition': inval,
            'risk_flags': '|'.join(SC.risk_flags(row, st, rs, funda)),
            'quality_tags': '|'.join(qs['tags']),
            'sub_trend': _r3(qs['sub']['trend_quality']),
            'sub_swing_space': _r3(qs['sub']['swing_space']),
            'sub_recovery': _r3(qs['sub']['recovery']),
            'sub_frequency': _r3(qs['sub']['frequency']),
            'sub_volume': _r3(qs['sub']['volume_structure']),
            'sub_liquidity': _r3(qs['sub']['liquidity_risk']),
            'industry': self.industry,
            'close': _r2(row['hfq_close']),
            'uni_ok': int(uni_ok),
            'trend_pass': int(tf['pass']),
            'trend_fail_reasons': '|'.join(tf['reasons']),
        }


def _r1(x):
    return None if x is None else round(float(x), 1)


def _r2(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


def _r3(x):
    try:
        return round(float(x), 3)
    except (TypeError, ValueError):
        return None


def _r4(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def _stats_from_legs(win_legs, t_idx, deadline):
    """与 SwingHistory.stats 同口径的轻量统计（行走器内部使用）"""
    out = {'swing_count': len(win_legs), 'up_ret_median': None,
           'recovery_rate': None, 'recovery_days_median': None,
           'breakout_fail_rate': None, 'up_down_vol_ratio': None,
           'opportunity_density': None, 'pullback_depth_mean': None,
           'recovery_unresolved': 0, 'up_ret_mean': None}
    if not win_legs:
        return out
    ups = [lg for lg in win_legs if lg['kind'] == 'up']
    downs = [lg for lg in win_legs if lg['kind'] == 'down']
    if ups:
        r = np.array([lg['ret'] for lg in ups], dtype=float)
        out['up_ret_mean'] = float(r.mean())
        out['up_ret_median'] = float(np.median(r))
        fh = [lg for lg in ups if lg['lower_high'] is not None]
        if fh:
            out['breakout_fail_rate'] = float(np.mean([lg['lower_high'] for lg in fh]))
    if downs:
        out['pullback_depth_mean'] = float(np.mean([-lg['ret'] for lg in downs]))
    resolved, rec_days = [], []
    for lg in downs:
        rb = lg['recovery_bar']
        if rb is not None and rb <= t_idx:
            resolved.append(True)
            rec_days.append(lg['recovery_days'])
        elif lg['end_bar'] + (lg.get('deadline_bar', 10 ** 9) - lg['end_bar']) <= t_idx:
            resolved.append(False)
    out['recovery_unresolved'] = len(downs) - len(resolved)
    if resolved:
        out['recovery_rate'] = float(np.mean(resolved))
        if rec_days:
            out['recovery_days_median'] = float(np.median(rec_days))
    ua = [lg['avg_amount'] for lg in ups if np.isfinite(lg['avg_amount'])]
    da = [lg['avg_amount'] for lg in downs if np.isfinite(lg['avg_amount'])]
    if ua and da and np.mean(da) > 0:
        out['up_down_vol_ratio'] = float(np.mean(ua) / np.mean(da))
    out['opportunity_density'] = len(win_legs) / 250.0
    return out
