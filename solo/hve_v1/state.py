# -*- coding: utf-8 -*-
"""HVE V1 状态机（§25）+ 信号判定（§12~§24、§29、§30）

状态流转（§25，逐日可追踪）：
    NORMAL → HVE_EVENT → { HVE_BULL_WATCH → (Re-expansion) → HVE_BULL
                          DIGESTION       → (Breakout)     → HVE_2ND
                          HVE_FAIL }

分径判据（V1 假设，见 hve_config._branch_rule）：以 §18 的 Volume Contraction 作为
「消化路径」的判据 —— HVE 后 T+1..T+5 的均量 / HVE 日量 <= volume_decay_max 即认为
进入消化路径（否则走强势保持路径）。该判据只用 HVE 后已发生的量，PIT 安全。

硬规则：
  - HVE 当日永不产生 BUY（§15）：state=HVE_EVENT，signal=WATCH
  - 结构破坏直接 FAIL（§23），FAIL 后 fail_cooldown_days 内不许新的 BUY（§24）
  - MARKET_RISK 不删信号，改 signal=CONDITIONAL（§32）
"""
import numpy as np
import pandas as pd

from hve_v1 import signals as S
from hve_v1.indicators import drawdown_since, consolidation_high
from hve_v1.tradability import tradability_flag, LIQUID
from hve_v1.market import apply_regime, MARKET_NEUTRAL

_FLOAT_COLS = (('open', 'o'), ('high', 'h'), ('low', 'l'), ('close', 'c'),
               ('pre_close', 'pc'), ('vol', 'v'), ('vol_ma20', 'vma'),
               ('vr20', 'vr'), ('clv', 'clv'), ('daily_return', 'ret'),
               ('ma20', 'ma20'), ('ma60', 'ma60'), ('ma20_prev5', 'ma20p5'),
               ('atr20', 'atr'), ('high_prev3', 'hp3'))


def _f(v, default=np.nan) -> float:
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def active_event_of_day(events: list, n: int, window: int) -> np.ndarray:
    """返回长度 n 的数组 active_idx：每一天生效的事件 primary 下标，无则 -1。

    同一天被多个事件覆盖时取**最近的一次**（后写覆盖先写）。
    """
    active = np.full(n, -1, dtype=int)
    for ev in events:
        p = ev['primary_idx']
        hi = min(p + window, n - 1)
        if hi >= p:
            active[p:hi + 1] = p
    return active


class _StockView:
    """把 DataFrame 转成 numpy 视图，避免逐日 .iloc 的开销"""

    __slots__ = ('d', 'n', 'trade_date') + tuple(a for _, a in _FLOAT_COLS)

    def __init__(self, d: pd.DataFrame):
        self.d = d
        self.n = len(d)
        self.trade_date = d['trade_date'].astype(str).to_numpy()
        for col, attr in _FLOAT_COLS:
            arr = pd.to_numeric(d[col], errors='coerce').to_numpy(dtype=float) \
                if col in d.columns else np.full(self.n, np.nan)
            setattr(self, attr, arr)


def _fail_gate(sv: _StockView, e: int, t: int, cfg: dict, dd_base: float) -> bool:
    """§23 结构破坏三路径：回撤 > second_max_drawdown / close < MA20 − k×ATR20 / 跌破 HVE 后整理区最低价"""
    dd_max = float(cfg['second_max_drawdown'])
    k = float(cfg['structure_atr_k'])
    if np.isfinite(dd_base) and dd_base > dd_max:
        return True
    ma20, atr, c = sv.ma20[t], sv.atr[t], sv.c[t]
    if np.isfinite(ma20) and np.isfinite(atr) and np.isfinite(c) and c < ma20 - k * atr:
        return True
    if t > e:
        lo_prev = float(np.nanmin(sv.l[e:t]))
        if np.isfinite(lo_prev) and np.isfinite(c) and c < lo_prev:
            return True
    return False


def _invalid_price(sv: _StockView, e: int, t: int, trigger: float) -> tuple:
    """§30 invalidation：明确「哪个结构失效条件先触发」，不用裸 max/min。

    返回 (invalid_price, rule)：
      - HVE-2ND：Close < trigger_price（整理区突破价）
      - HVE-BULL：取 MA20 与 HVE 后结构低点中**更靠近价格**的那个（先被触及者）
    """
    c = sv.c[t]
    if np.isfinite(trigger) and np.isfinite(c) and c > trigger:
        return float(trigger), 'TRIGGER_PRICE'
    ma20 = sv.ma20[t]
    lo_struct = float(np.nanmin(sv.l[e:t + 1])) if t >= e else np.nan
    cands = []
    if np.isfinite(ma20):
        cands.append((float(ma20), 'MA20'))
    if np.isfinite(lo_struct):
        cands.append((lo_struct, 'STRUCTURE_LOW'))
    if not cands:
        return np.nan, ''
    return max(cands, key=lambda x: x[0])


def evaluate_day(sv: _StockView, ts_code: str, name: str, ev: dict, t: int,
                 cfg: dict, regime: str = MARKET_NEUTRAL) -> dict:
    """单日状态判定（只使用 <= t 的数据）"""
    e = ev['primary_idx']
    c, o, h, l = sv.c[t], sv.o[t], sv.h[t], sv.l[t]
    ma20, ma60, ma20p5 = sv.ma20[t], sv.ma60[t], sv.ma20p5[t]
    k = float(cfg['structure_atr_k'])

    dd = drawdown_since(sv.d, e, t)
    struct_high = consolidation_high(sv.d, e, t)
    vd = ev.get('volume_decay_5d', np.nan)
    decay_win = int(cfg.get('volume_decay_window', 5))
    vd_avail = np.isfinite(vd) and (t - e) >= decay_win

    structure_ok = (np.isfinite(ma20) and np.isfinite(sv.atr[t])
                    and np.isfinite(c) and c >= ma20 - k * sv.atr[t])
    ma20_up = np.isfinite(ma20) and np.isfinite(ma20p5) and ma20 > ma20p5

    vol_ok = lambda ratio: (np.isfinite(sv.vma[t]) and sv.vma[t] > 0
                            and np.isfinite(sv.v[t]) and sv.v[t] >= ratio * sv.vma[t])
    shape_ok = (np.isfinite(c) and np.isfinite(o) and c > o
                and np.isfinite(sv.clv[t]) and sv.clv[t] >= float(cfg['clv_min']))

    bar = tradability_flag(sv.d, t, ts_code)

    rec = {
        'trade_date': sv.trade_date[t],
        'ts_code': ts_code,
        'name': name,
        'signal_type': S.HVE_WATCH,
        'state': S.S_HVE_EVENT,
        'signal': S.SIG_WATCH,
        'event_date': ev['event_date'],
        'days_since_hve': int(t - e),
        'event_cluster_id': ev['cluster_id'],
        'vr20': _f(sv.vr[e]),
        'hve_return': _f(sv.ret[e]),
        'hve_clv': _f(sv.clv[e]),
        'current_return': (float(c) / float(sv.c[e]) - 1.0) if np.isfinite(c) and sv.c[e] > 0 else np.nan,
        'current_volume_ratio': _f(sv.vr[t]),
        'ma20': _f(ma20),
        'ma60': _f(ma60),
        'ma20_up': bool(ma20_up),
        'drawdown_from_hve': _f(dd),
        'volume_decay_5d': _f(vd) if vd_avail else np.nan,
        'consolidation_high': _f(struct_high),
        'breakout_distance': (float(c) / float(struct_high) - 1.0)
                             if np.isfinite(c) and np.isfinite(struct_high) and struct_high > 0 else np.nan,
        'entry_price': _f(c),
        'trigger_price': np.nan,
        'invalid_price': np.nan,
        'invalid_rule': '',
        'market_regime': regime,
        'reason': S.REASON_WATCH,
        'tradability_flag': bar,
        'executable': True,
        'theme': '', 'theme_heat': np.nan, 'theme_rank': np.nan,
        'close': _f(c),
    }

    # ---- §15 HVE 当日只观察，绝不 BUY ----
    if t == e:
        if not structure_ok or _fail_gate(sv, e, t, cfg, dd):
            rec.update(signal_type=S.HVE_FAIL, state=S.S_FAIL,
                       signal=S.SIG_NONE, reason=S.REASON_FAIL)
            inv, rule = _invalid_price(sv, e, t, np.nan)
            rec.update(invalid_price=inv, invalid_rule=rule)
        return rec

    # ---- §23 结构破坏 ----
    if _fail_gate(sv, e, t, cfg, dd):
        rec.update(signal_type=S.HVE_FAIL, state=S.S_FAIL,
                   signal=S.SIG_NONE, reason=S.REASON_FAIL)
        inv, rule = _invalid_price(sv, e, t, np.nan)
        rec.update(invalid_price=inv, invalid_rule=rule)
        return rec

    digest_path = bool(vd_avail and vd <= float(cfg['volume_decay_max']))

    if digest_path:
        # ---- §16~§21 HVE-2ND 路径 ----
        buy = (int(t - e) >= int(cfg['min_digest_days'])
               and structure_ok
               and np.isfinite(dd) and dd <= float(cfg['second_max_drawdown'])
               and np.isfinite(struct_high) and np.isfinite(c) and c > struct_high
               and vol_ok(float(cfg['breakout_volume_ratio']))
               and shape_ok)
        rec['trigger_price'] = _f(struct_high)
        if buy:
            rec.update(signal_type=S.HVE_2ND, state=S.S_2ND, signal=S.SIG_BUY,
                       reason=S.REASON_2ND)
        else:
            rec.update(signal_type=S.HVE_WATCH, state=S.S_DIGESTION, signal=S.SIG_WATCH,
                       reason=S.REASON_WATCH)
        inv, rule = _invalid_price(sv, e, t, rec['trigger_price'])
    else:
        # ---- §12~§14 HVE-BULL 路径 ----
        bull_gate = (structure_ok and ma20_up
                     and np.isfinite(c) and np.isfinite(ma20) and c > ma20
                     and np.isfinite(dd) and dd <= float(cfg['bull_max_drawdown']))
        if bull_gate:
            reexpand = (np.isfinite(sv.hp3[t]) and c > sv.hp3[t]
                        and vol_ok(float(cfg['reexpansion_volume_ratio']))
                        and shape_ok)
            rec['trigger_price'] = _f(sv.hp3[t])
            if reexpand:
                rec.update(signal_type=S.HVE_BULL, state=S.S_BULL, signal=S.SIG_BUY,
                           reason=S.REASON_BULL)
            else:
                rec.update(signal_type=S.HVE_WATCH, state=S.S_BULL_WATCH,
                           signal=S.SIG_WATCH, reason=S.REASON_WATCH)
        elif structure_ok and np.isfinite(dd) and dd <= float(cfg['second_max_drawdown']):
            rec.update(signal_type=S.HVE_WATCH, state=S.S_DIGESTION,
                       signal=S.SIG_WATCH, reason=S.REASON_WATCH)
        else:
            rec.update(signal_type=S.HVE_WATCH, state=S.S_HVE_EVENT,
                       signal=S.SIG_WATCH, reason=S.REASON_WATCH)
        inv, rule = _invalid_price(sv, e, t, np.nan)
    rec['invalid_price'] = inv
    rec['invalid_rule'] = rule

    # ---- §32 市场环境：RISK 不删信号，改 CONDITIONAL ----
    rec['signal'] = apply_regime(rec['signal'], regime)

    # ---- §46 涨停/一字板：研究信号保留，但不默认产生「实际 BUY」 ----
    if bar != LIQUID:
        if rec['signal'] in (S.SIG_BUY, S.SIG_CONDITIONAL):
            rec['executable'] = False
            rec['reason'] += (S.SUF_ONE_PRICE if bar == 'ONE_PRICE_BOARD' else S.SUF_LIMIT_UP)
    return rec


def walk_stock(d: pd.DataFrame, ts_code: str, cfg: dict, events=None,
               name: str = '', regime_fn=None, eligible=None) -> list:
    """逐日推进状态机，返回每个「有活跃事件」的交易日记录。

    参数
      d          indicators.compute_indicators + event.annotate_events 之后的 DataFrame
      events     event.annotate_events 返回的事件簇列表；None → 自行标注
      regime_fn  callable(trade_date) -> MARKET_*；None → MARKET_NEUTRAL
      eligible   callable(idx) -> bool（股票池过滤）；None → 全通过
    """
    if d is None or len(d) == 0:
        return []
    if events is None:
        from hve_v1.event import annotate_events
        d, events = annotate_events(d, ts_code, cfg)
    if not events:
        return []

    n = len(d)
    window = int(cfg['post_hve_window'])
    cooldown = int(cfg['fail_cooldown_days'])
    sv = _StockView(d)
    active = active_event_of_day(events, n, window)
    ev_map = {ev['primary_idx']: ev for ev in events}

    out = []
    last_fail = None
    regime_cache = {}
    for t in range(n):
        p = active[t]
        if p < 0:
            continue
        if eligible is not None and not eligible(t):
            continue
        if regime_fn is not None:
            dt = sv.trade_date[t]
            if dt not in regime_cache:
                regime_cache[dt] = regime_fn(dt)
            regime = regime_cache[dt]
        else:
            regime = MARKET_NEUTRAL

        rec = evaluate_day(sv, ts_code, name, ev_map[p], t, cfg, regime)
        if rec['state'] == S.S_FAIL:
            last_fail = t
        # §24 冷却期：FAIL 后 fail_cooldown_days 内不允许新的 BUY
        if rec['signal'] in (S.SIG_BUY, S.SIG_CONDITIONAL) and last_fail is not None \
                and (t - last_fail) < cooldown:
            rec['signal'] = S.SIG_WATCH
            rec['signal_type'] = S.HVE_WATCH
            rec['executable'] = False
            rec['reason'] += S.SUF_COOLDOWN
        out.append(rec)
    return out
