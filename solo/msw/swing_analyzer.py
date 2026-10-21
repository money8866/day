# -*- coding: utf-8 -*-
"""MSW 历史波段分析器（任务书 §11 swing_analyzer.py，核心创新点）

PIT 铁律（任务书 §5.1）：
  ZigZag 转折点必须被「事后价格确认」后才进入统计 —— 一个高点只有在价格从其
  回撤超过阈值之后才成为「已确认高点」。实盘信号与评分只能使用截至当日已确认的
  转折点，严禁使用当日之后才出现的低点/高点信息。

实现：
  build_swings()   离线 ZigZag → 已确认转折点 + 完整摆动段（leg）
  SwingHistory    按日查询接口：截至 T 的 legs / 统计快照 / 最近阶段高低点
"""
import bisect
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- ZigZag

def zigzag_pivots(high, low, close, thr_rel):
    """离线百分比/ATR ZigZag，返回已确认转折点列表（按确认顺序）。

    thr_rel: 与 high/low/close 等长的相对阈值数组（如 max(10%, 1.5×ATR%/close)）
    每个转折点: {'bar': int, 'price': float, 'kind': 'high'|'low', 'confirm_bar': int}
      - bar        极值所在 K 线
      - confirm_bar 该转折点被确认的 K 线（>= bar+1），只有 confirm_bar <= T 才可在 T 使用
    方向判定：上涨段中收盘自候选高点回撤 >= thr → 确认高点；下跌段中收盘自候选低点
    反弹 >= thr → 确认低点。候选极值在段内持续更新（取最极端值）。
    """
    n = len(close)
    pivots = []
    if n < 3:
        return pivots
    thr = np.asarray(thr_rel, dtype=float)
    thr = np.where(np.isfinite(thr) & (thr > 0), thr, 0.10)

    # 初始：从 bar0 的低点开始寻找第一个上涨段
    direction = 'up'
    cand_idx, cand_price = 0, float(low[0])
    first_high_done = False
    for i in range(1, n):
        t = thr[i]
        if direction == 'up':
            if float(high[i]) >= cand_price * (1.0 + t):
                cand_idx, cand_price = i, float(high[i])
            elif float(close[i]) <= cand_price * (1.0 - t):
                if not first_high_done:
                    # 初始低点 = 第一个确认高点之前的最低价
                    seg = low[:cand_idx + 1]
                    li = int(np.argmin(seg))
                    pivots.append({'bar': li, 'price': float(seg[li]),
                                   'kind': 'low', 'confirm_bar': i})
                    first_high_done = True
                pivots.append({'bar': cand_idx, 'price': cand_price,
                               'kind': 'high', 'confirm_bar': i})
                direction = 'down'
                cand_idx, cand_price = i, float(low[i])
        else:
            if float(low[i]) <= cand_price * (1.0 - t):
                cand_idx, cand_price = i, float(low[i])
            elif float(close[i]) >= cand_price * (1.0 + t):
                pivots.append({'bar': cand_idx, 'price': cand_price,
                               'kind': 'low', 'confirm_bar': i})
                direction = 'up'
                cand_idx, cand_price = i, float(high[i])
    return pivots


def build_swings(df_feats: pd.DataFrame, cfg: dict) -> list:
    """由特征 DataFrame 构建完整摆动段列表（每段 = 相邻两个已确认转折点）。

    返回 leg dict 列表（按 confirm_bar 升序）：
      kind        'up'（低→高）/ 'down'（高→低）
      start_bar/end_bar, start_price/end_price
      ret         涨跌幅（up: high/low-1；down: low/high-1，为负）
      dur         持续 K 线数
      confirm_bar 该段被确认的 K 线（= 终点转折点的 confirm_bar）
      avg_amount  段内平均成交额（千元）
      prev_high   前一个已确认高点（up 段用，判断是否突破前高）
      lower_high  up 段终点未过前高 → True（突破失败）
      recovery_bar/recovery_days  down 段之后的修复确认（收市价回到段初高点）
      resolved     down 段修复结果是否已有定论
    """
    sw = cfg.get('swing', {})
    pct = float(sw.get('zigzag_pct', 0.10))
    atr_mult = float(sw.get('zigzag_atr_mult', 1.5))
    deadline = int(sw.get('recovery_deadline_days', 60))

    high = df_feats['hfq_high'].to_numpy(dtype=float)
    low = df_feats['hfq_low'].to_numpy(dtype=float)
    close = df_feats['hfq_close'].to_numpy(dtype=float)
    amount = df_feats['amount'].to_numpy(dtype=float)
    atr_pct = df_feats['atr_pct'].to_numpy(dtype=float)
    n = len(close)

    thr_rel = np.maximum(pct, atr_mult * np.nan_to_num(atr_pct, nan=0.0))
    pivots = zigzag_pivots(high, low, close, thr_rel)
    if len(pivots) < 2:
        return []

    legs = []
    prev_high_price = None
    for k in range(1, len(pivots)):
        p0, p1 = pivots[k - 1], pivots[k]
        kind = 'up' if p0['kind'] == 'low' else 'down'
        sb, eb = p0['bar'], p1['bar']
        sp, ep = p0['price'], p1['price']
        seg_amt = amount[sb:eb + 1]
        leg = {
            'kind': kind,
            'start_bar': sb, 'end_bar': eb,
            'start_price': sp, 'end_price': ep,
            'ret': (ep / sp - 1.0) if kind == 'up' else (ep / sp - 1.0),
            'dur': eb - sb,
            'confirm_bar': p1['confirm_bar'],
            'avg_amount': float(np.nanmean(seg_amt)) if len(seg_amt) else float('nan'),
            'prev_high': prev_high_price,
            'lower_high': None,
            'recovery_bar': None, 'recovery_days': None, 'resolved': False,
        }
        if kind == 'up':
            # 突破失败：本段高点未超过前一个已确认高点
            if prev_high_price is not None:
                leg['lower_high'] = bool(ep < prev_high_price * (1.0 - 1e-9))
            prev_high_price = ep
        else:
            # 修复：段末低点之后，收盘价回到段初高点的首日
            hp = sp  # down 段起点即前高
            rb = None
            for j in range(eb + 1, min(n, eb + 1 + deadline + 250)):
                if close[j] >= hp:
                    rb = j
                    break
            leg['recovery_bar'] = rb
            # deadline_bar 必须无条件记录（PIT）：stats(T) 据此判定「deadline 已过
            # 且 T 前未见修复 → 失败」。若仅在未找到修复时才设置，该字段的存在性
            # 将取决于未来数据，导致截断/全量运行的统计不一致（未来函数泄漏）。
            leg['deadline_bar'] = eb + deadline
            if rb is not None:
                leg['recovery_days'] = rb - eb
                leg['resolved'] = True
            else:
                # 未修复：需等到 eb+deadline 之后才能下结论
                leg['resolved'] = False
        legs.append(leg)
    return legs


class SwingHistory:
    """按日查询接口（全部 PIT：只返回 confirm_bar <= T_idx 的信息）"""

    def __init__(self, df_feats: pd.DataFrame, cfg: dict):
        self.legs = build_swings(df_feats, cfg)
        self._snap_bars = []
        self._snap_legs = []
        cum = []
        for leg in sorted(self.legs, key=lambda x: x['confirm_bar']):
            cum.append(leg)
            self._snap_bars.append(leg['confirm_bar'])
            self._snap_legs.append(list(cum))

    # ---------------- 基础查询 ----------------

    def legs_asof(self, t_idx: int) -> list:
        """截至 t_idx（含）已确认的全部 legs"""
        i = bisect.bisect_right(self._snap_bars, t_idx) - 1
        if i < 0:
            return []
        return self._snap_legs[i]

    def last_leg(self, t_idx: int):
        legs = self.legs_asof(t_idx)
        return legs[-1] if legs else None

    def pivots_asof(self, t_idx: int) -> list:
        """截至 t_idx 已确认转折点（由 legs 还原：起点 + 各终点）"""
        legs = self.legs_asof(t_idx)
        out = []
        for lg in legs:
            out.append({'bar': lg['start_bar'], 'price': lg['start_price'],
                        'kind': 'low' if lg['kind'] == 'up' else 'high'})
            out.append({'bar': lg['end_bar'], 'price': lg['end_price'],
                        'kind': 'high' if lg['kind'] == 'up' else 'low'})
        return out

    def stage_high(self, t_idx: int, lookback: int = 250):
        """截至 T 最近一个已确认高点（stage high），及其距今 K 线数"""
        best = None
        for lg in self.legs_asof(t_idx):
            if lg['kind'] == 'up' and lg['end_bar'] >= t_idx - lookback:
                if best is None or lg['end_bar'] > best[1]:
                    best = (lg['end_price'], lg['end_bar'])
        return best  # (price, bar) or None

    def stage_low(self, t_idx: int, lookback: int = 250):
        best = None
        for lg in self.legs_asof(t_idx):
            if lg['kind'] == 'down' and lg['end_bar'] >= t_idx - lookback:
                if best is None or lg['end_bar'] > best[1]:
                    best = (lg['end_price'], lg['end_bar'])
        return best

    def prev_breakout_level(self, t_idx: int, lookback: int = 250):
        """截至 T 的「前突破位」= 最近一个已确认高点之前的那一个已确认高点
        （旧阻力突破后回落形成的潜在支撑）"""
        highs = [lg for lg in self.legs_asof(t_idx)
                 if lg['kind'] == 'up' and lg['end_bar'] >= t_idx - lookback]
        highs.sort(key=lambda x: x['end_bar'])
        if len(highs) >= 2:
            return highs[-2]['end_price']
        return None

    # ---------------- 统计 ----------------

    def stats(self, t_idx: int, window: int = 250) -> dict:
        """截至 T 的 window 日历史波段统计（任务书 §5.1 全部 12 项）"""
        legs = self.legs_asof(t_idx)
        win_legs = [lg for lg in legs if lg['end_bar'] >= t_idx - window]
        out = {
            'swing_count': len(win_legs),
            'up_legs': 0, 'down_legs': 0,
            'up_ret_mean': None, 'up_ret_median': None,
            'pullback_depth_mean': None, 'pullback_depth_median': None,
            'pullback_days_mean': None,
            'recovery_rate': None, 'recovery_days_median': None,
            'recovery_unresolved': 0,
            'breakout_fail_rate': None,
            'up_down_vol_ratio': None,
            'avg_leg_days': None,
            'opportunity_density': None,
        }
        if not win_legs:
            return out
        ups = [lg for lg in win_legs if lg['kind'] == 'up']
        downs = [lg for lg in win_legs if lg['kind'] == 'down']
        out['up_legs'] = len(ups)
        out['down_legs'] = len(downs)
        if ups:
            r = np.array([lg['ret'] for lg in ups], dtype=float)
            out['up_ret_mean'] = float(r.mean())
            out['up_ret_median'] = float(np.median(r))
            fh = [lg for lg in ups if lg['lower_high'] is not None]
            if fh:
                out['breakout_fail_rate'] = float(np.mean([lg['lower_high'] for lg in fh]))
        if downs:
            dd = np.array([-lg['ret'] for lg in downs], dtype=float)
            du = np.array([lg['dur'] for lg in downs], dtype=float)
            out['pullback_depth_mean'] = float(dd.mean())
            out['pullback_depth_median'] = float(np.median(dd))
            out['pullback_days_mean'] = float(du.mean())
        # 修复率：只统计已「有定论」的回撤（PIT：修复事件必须发生在 T 及之前；
        # 未修复的必须已过 deadline 才能计入失败）
        resolved, rec_days = [], []
        for lg in downs:
            rb = lg['recovery_bar']
            if rb is not None and rb <= t_idx:
                resolved.append(True)
                rec_days.append(lg['recovery_days'])
            elif lg['end_bar'] + self._deadline_of(lg) <= t_idx:
                # deadline 已过且 T 之前未见修复 → 失败（此时 rb 必为 None 或 > T）
                resolved.append(False)
        out['recovery_unresolved'] = len(downs) - len(resolved)
        if resolved:
            out['recovery_rate'] = float(np.mean(resolved))
            if rec_days:
                out['recovery_days_median'] = float(np.median(rec_days))
        # 量能结构：上涨段均量 / 回撤段均量
        ua = [lg['avg_amount'] for lg in ups if np.isfinite(lg['avg_amount'])]
        da = [lg['avg_amount'] for lg in downs if np.isfinite(lg['avg_amount'])]
        if ua and da and np.mean(da) > 0:
            out['up_down_vol_ratio'] = float(np.mean(ua) / np.mean(da))
        durs = [lg['dur'] for lg in win_legs]
        out['avg_leg_days'] = float(np.mean(durs))
        out['opportunity_density'] = float(len(win_legs)) / float(window)
        return out

    @staticmethod
    def _deadline_of(lg) -> int:
        return int(lg.get('deadline_bar', 10**9)) - lg['end_bar']
