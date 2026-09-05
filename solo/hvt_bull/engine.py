# -*- coding: utf-8 -*-
"""HVT-BULL 核心引擎（V3.0：右尾捕获导向）

职责：
  1. detect_hvt(df, idx): 判断第 idx 日是否构成历史天量换手事件（HVT-A/B/C）
  2. evaluate_event(df, ev): 计算天量日价格强度、趋势、涨幅结构
  3. update_tracking(df, event): 天量后跟踪（锁筹/回撤/二次突破/状态机）
  4. score(ev): 综合评分 100 分制（V1 遗留，用于兼容）
  5. V3 新增：
     - compute_rs(df, idx, cross_section): 全市场截面相对强度
     - entry_score(ev): 入场时点评分
     - expansion_score(df, ev, idx): T20 扩张潜力评分（右尾核心）
     - hard_veto(ev): 硬否决检查
     - classify_v3(ev): 双轴分类（PRIMARY/T20_ROCKET/BREAKOUT_READY）

V3 核心原则：
  - 优化目标是 T20 右尾捕获（Top10%/Top5%、+20%/+30%/+50%），不是 T1 胜率
  - Close_Position / Volume 采用 A+~D 分级，不做绝对淘汰
  - 高扩张潜力但入场未确认的股票进入 T20_ROCKET_WATCH，不删除
"""

import numpy as np
import pandas as pd

from .models import HvtEvent


def _arr(df: pd.DataFrame, col: str) -> np.ndarray:
    return df[col].to_numpy(dtype=float)


def _dense(dates, i0, i1) -> bool:
    """检查行区间 [i0, i1] 的交易日密度：日历跨度 <= 交易日数×1.9"""
    if i1 < i0:
        return False
    n = i1 - i0 + 1
    if n <= 0:
        return False
    try:
        d0 = pd.to_datetime(str(dates[i0]), format='%Y%m%d')
        d1 = pd.to_datetime(str(dates[i1]), format='%Y%m%d')
        return (d1 - d0).days <= n * 1.9
    except Exception:
        return False


class HvtBullEngine:
    """单股 HVT-BULL 事件引擎"""

    def __init__(self, config: dict = None):
        self.cfg = config or {}
        self.hvt_cfg = self.cfg.get('hvt', {})
        self.ps_cfg = self.cfg.get('price_strength', {})
        self.tr_cfg = self.cfg.get('trend', {})
        self.gs_cfg = self.cfg.get('gain_structure', {})
        self.cl_cfg = self.cfg.get('chip_lock', {})
        self.dd_cfg = self.cfg.get('drawdown', {})
        self.bo_cfg = self.cfg.get('breakout', {})
        self.ex_cfg = self.cfg.get('exit', {})
        self.sm_cfg = self.cfg.get('state_machine', {})
        self.sc_cfg = self.cfg.get('scoring', {})
        self.grades_cfg = self.cfg.get('grades', {})
        self.v3_cfg = self.cfg.get('v3', {})

    # ------------------------------------------------------------------
    # 阶段1: HVT 事件判定
    # ------------------------------------------------------------------
    def detect_hvt(self, df: pd.DataFrame, idx: int):
        """判断 df 第 idx 行（T0）是否构成 HVT 事件。返回事件或 None。

        天量口径 rank_mode（hvt 配置段）：
          anchor  : T0 换手率 = anchor_date 以来最高（rank_anchor == 1），按 20 日量比分 A/B/C
          rolling : 250 日滚动窗口排名口径（rank_a/b + pct120 分级，V1 遗留）
          both    : 两者并集，anchor 优先定级；anchor 不通过时回退 rolling 判定
        未配置 rank_mode 时向后兼容：anchor_date 非空 -> anchor，否则 -> rolling。
        """
        if idx < 30 or idx >= len(df):
            return None
        turnover = _arr(df, 'turnover_rate')
        amount = _arr(df, 'amount')
        if not np.isfinite(turnover[idx]) or turnover[idx] <= 0:
            return None

        lb = int(self.hvt_cfg.get('lookback_250', 250))
        lo = max(0, idx - lb)
        hist = turnover[lo:idx]
        if len(hist) < 60:
            return None

        rank = int(np.sum(hist >= turnover[idx])) + 1  # 250日窗口排名（参考）
        h120 = turnover[max(0, idx - 120):idx]
        if len(h120) < 60:
            return None
        pct120 = float(np.mean(h120 < turnover[idx]) * 100.0)

        h20 = turnover[max(0, idx - 20):idx]
        ma20t = float(np.nanmean(h20)) if len(h20) >= 10 else 0.0
        tratio = turnover[idx] / ma20t if ma20t > 0 else 0.0

        a20 = amount[max(0, idx - 20):idx]
        ma20a = float(np.nanmean(a20)) if len(a20) >= 10 else 0.0
        aratio = amount[idx] / ma20a if ma20a > 0 else 0.0

        ratio_a = float(self.hvt_cfg.get('ratio_a', 3.0))
        ratio_b = float(self.hvt_cfg.get('ratio_b', 2.0))
        ratio_c = float(self.hvt_cfg.get('ratio_c', 1.8))

        anchor = str(self.hvt_cfg.get('anchor_date', '') or '')
        mode = str(self.hvt_cfg.get('rank_mode', '') or '').strip().lower()
        if mode not in ('anchor', 'rolling', 'both'):
            mode = 'anchor' if anchor else 'rolling'
        if mode == 'anchor' and not anchor:
            mode = 'rolling'
        rank_anchor = 0
        grade = None
        if mode in ('anchor', 'both'):
            dates = df['trade_date'].tolist()
            a0 = next((i for i, d in enumerate(dates) if str(d) >= anchor), None)
            anchor_ok = a0 is not None and a0 <= idx
            if anchor_ok:
                hist_a = turnover[a0:idx]
                if len(hist_a) >= int(self.hvt_cfg.get('anchor_min_hist', 60)):
                    rank_anchor = int(np.sum(hist_a >= turnover[idx])) + 1
                    anchor_ok = rank_anchor == 1 and tratio >= ratio_c
                else:
                    anchor_ok = False
            if anchor_ok:
                grade = 'A' if tratio >= ratio_a else ('B' if tratio >= ratio_b else 'C')
            elif mode == 'anchor':
                return None
        if grade is None and mode in ('rolling', 'both'):
            rank_a = int(self.hvt_cfg.get('rank_a', 1))
            rank_b = int(self.hvt_cfg.get('rank_b', 2))
            pct_a = float(self.hvt_cfg.get('pct_a', 99))
            pct_b = float(self.hvt_cfg.get('pct_b', 98))
            pct_c = float(self.hvt_cfg.get('pct_c', 95))
            if rank <= rank_a and pct120 >= pct_a and tratio >= ratio_a:
                grade = 'A'
            elif rank <= rank_b and pct120 >= pct_b and tratio >= ratio_b:
                grade = 'B'
            elif pct120 >= pct_c and tratio >= ratio_c and rank <= int(self.hvt_cfg.get('top_rank_cap', 2) + 8):
                grade = 'C'
            else:
                return None
        if grade is None:
            return None

        ev = HvtEvent(ts_code=str(df['ts_code'].iloc[0]),
                      t0_date=str(df['trade_date'].iloc[idx]),
                      t0_index=idx)
        ev.hvt_grade = grade
        ev.hvt_rank_250 = rank
        ev.hvt_rank_anchor = rank_anchor
        ev.turnover_pct_120 = pct120
        ev.turnover_ratio_20 = tratio
        ev.amount_ratio_20 = aratio
        ev.t0_turnover = float(turnover[idx])
        ev.t0_amount = float(amount[idx]) if np.isfinite(amount[idx]) else 0.0
        return ev

    # ------------------------------------------------------------------
    # 阶段2: 天量日价格强度 + 前期趋势 + 涨幅结构
    # ------------------------------------------------------------------
    def evaluate_event(self, df: pd.DataFrame, ev: HvtEvent) -> HvtEvent:
        idx = ev.t0_index
        close = _arr(df, 'close')
        open_ = _arr(df, 'open')
        high = _arr(df, 'high')
        low = _arr(df, 'low')
        pct = _arr(df, 'pct_chg')
        dates = df['trade_date'].tolist()

        ev.t0_close = float(close[idx])
        ev.t0_high = float(high[idx])
        ev.t0_low = float(low[idx])
        ev.t0_mid = (ev.t0_high + ev.t0_low) / 2.0
        ev.t0_pct_chg = float(pct[idx]) if np.isfinite(pct[idx]) else 0.0
        rng = high[idx] - low[idx]
        ev.t0_close_pos = (close[idx] - low[idx]) / rng if rng > 0 else 0.5
        ev.t0_body = (close[idx] - open_[idx]) / rng if rng > 0 else 0.0

        def _ma(n):
            if idx + 1 >= n and _dense(dates, idx + 1 - n, idx):
                return float(np.nanmean(close[idx + 1 - n:idx + 1]))
            return float('nan')

        ev.ma20 = _ma(20)
        ev.ma60 = _ma(60)
        ev.ma120 = _ma(120)
        s20 = int(self.tr_cfg.get('ma20_slope_days', 10))
        s60 = int(self.tr_cfg.get('ma60_slope_days', 20))
        if idx + 1 >= 20 + s20 and _dense(dates, idx + 1 - 20 - s20, idx):
            m_now = np.nanmean(close[idx + 1 - 20:idx + 1])
            m_prev = np.nanmean(close[idx + 1 - 20 - s20:idx + 1 - s20])
            ev.ma20_slope = (m_now - m_prev) / m_prev if m_prev > 0 else 0.0
        if idx + 1 >= 60 + s60 and _dense(dates, idx + 1 - 60 - s60, idx):
            m_now = np.nanmean(close[idx + 1 - 60:idx + 1])
            m_prev = np.nanmean(close[idx + 1 - 60 - s60:idx + 1 - s60])
            ev.ma60_slope = (m_now - m_prev) / m_prev if m_prev > 0 else 0.0

        # 平台突破豁免
        if idx >= 60:
            pre_high_60 = np.nanmax(high[idx - 60:idx])
            ev.platform_breakout = bool(close[idx] > pre_high_60)
        if idx >= 20 and not ev.platform_breakout:
            # 20日平台强势突破（收盘 >= 前20日最高收盘×1.05）
            pre_close_20 = np.nanmax(close[idx - 20:idx])
            if close[idx] >= pre_close_20 * 1.05:
                ev.platform_breakout = True

        def _ret(n):
            if idx - n >= 0 and _dense(dates, idx - n, idx) and close[idx - n] > 0:
                return float(close[idx] / close[idx - n] - 1.0) * 100.0
            return float('nan')

        ev.r5 = _ret(5)
        ev.r10 = _ret(10)
        ev.r20 = _ret(20)
        ev.r60 = _ret(60)
        ev.r120 = _ret(120)
        ev.r250 = _ret(250)
        if idx >= 120 and _dense(dates, idx - 120, idx):
            h120 = np.nanmax(high[idx - 120:idx + 1])
            ev.dist_high_120 = float((h120 - close[idx]) / close[idx] * 100.0)
        else:
            ev.dist_high_120 = float('nan')

        if idx >= 15:
            tr_arr = np.maximum(high[idx - 14:idx + 1] - low[idx - 14:idx + 1],
                                np.maximum(abs(high[idx - 14:idx + 1] - close[idx - 15:idx]),
                                           abs(low[idx - 14:idx + 1] - close[idx - 15:idx])))
            ev.atr14 = float(np.nanmean(tr_arr))
        return ev

    def price_strength_ok(self, ev: HvtEvent) -> bool:
        """天量当天必须“价格强”（规格§4 + §5 平台突破豁免）"""
        min_pct = float(self.ps_cfg.get('min_pct_chg', 3.0))
        min_pos = float(self.ps_cfg.get('min_close_pos', 0.70))
        ok_chg = ev.t0_pct_chg >= min_pct
        ok_pos = ev.t0_close_pos >= min_pos
        if not (ok_chg and ok_pos):
            return False
        # MA20 条件（20日窗口密度几乎总是足够）
        if np.isfinite(ev.ma20) and ev.t0_close <= ev.ma20:
            return False
        # MA60 条件：豁免 = 数据不足 / 接近收复(≤2%) / 平台突破
        if np.isfinite(ev.ma60) and ev.t0_close <= ev.ma60:
            near = ev.t0_close >= ev.ma60 * 0.98
            if not (near or ev.platform_breakout):
                return False
        return True

    def distribution_risk(self, ev: HvtEvent) -> bool:
        """涨幅<0 + 长上影 + 资金流出 -> DISTRIBUTION_RISK（规格§4排除项）"""
        if ev.t0_pct_chg < 0:
            shadow = 1.0 - ev.t0_close_pos
            if shadow >= 0.5 and ev.money_quality_score < 40:
                return True
        return False

    # ------------------------------------------------------------------
    # 阶段3: 天量后跟踪
    # ------------------------------------------------------------------
    def update_tracking(self, df: pd.DataFrame, ev: HvtEvent, end_idx: int = None) -> HvtEvent:
        idx = ev.t0_index
        n = len(df)
        end = n if end_idx is None else min(end_idx, n)
        if end <= idx + 1:
            return ev
        close = _arr(df, 'close')
        high = _arr(df, 'high')
        low = _arr(df, 'low')
        vol = _arr(df, 'vol')
        turnover = _arr(df, 'turnover_rate')
        dates = df['trade_date'].tolist()

        # 跟踪窗口上限：防止一年后的普通回调污染状态
        max_track = int(self.sm_cfg.get('max_locked_days', 30)) + 5
        track_end = min(end, idx + max_track)
        # 二次突破检测窗口独立放宽（V3.4：捕获 T+35 之后的慢突破）
        bw = int(self.sm_cfg.get('breakout_window', 0))
        break_end = min(end, idx + (bw if bw > 0 else max_track))

        ev.days_after = min(end - 1 - idx, max_track)

        # ── 锁筹/回撤统计：T+1 ~ T+5 ──
        t0_vol = vol[idx]
        w_end = min(end, idx + 6)
        if w_end >= idx + 4:
            v5w = np.nanmean(vol[idx + 1:w_end])
            closes_w = close[idx + 1:w_end]
            lows_w = low[idx + 1:w_end]
            ratio = float(v5w / t0_vol) if t0_vol > 0 else 1.0
            ev.vol_5d_ratio = ratio
            v3w = np.nanmean(vol[idx + 1:min(end, idx + 4)])
            ev.vol_3d_ratio = float(v3w / t0_vol) if t0_vol > 0 else 1.0
            min_low = float(np.nanmin(lows_w)) if len(lows_w) else ev.t0_close
            last_close = float(close[w_end - 1])
            vr = float(self.cl_cfg.get('volume_5d_ratio', 0.60))
            svr = float(self.cl_cfg.get('strong_volume_5d_ratio', 0.50))
            min_low_r = float(self.cl_cfg.get('min_low_vs_close', 0.93))
            min_close_r = float(self.cl_cfg.get('min_close_vs_close', 0.95))
            strong_close_r = float(self.cl_cfg.get('strong_close_vs_close', 0.97))
            ev.locked_chip = bool(ratio <= vr and min_low >= ev.t0_close * min_low_r
                                  and last_close >= ev.t0_close * min_close_r)
            ev.strong_locked_chip = bool(ratio <= svr and last_close >= ev.t0_close * strong_close_r)

        # 回撤（跟踪窗口内）
        post_close = close[idx + 1:track_end]
        if len(post_close):
            min_close = float(np.nanmin(post_close))
            ev.post_max_drawdown = float((ev.t0_close - min_close) / ev.t0_close * 100.0)
            ev.post_min_low = float(np.nanmin(low[idx + 1:track_end]))
        if ev.atr14 and ev.atr14 > 0 and ev.t0_close > 0:
            dd_price = ev.t0_close * max(0.0, ev.post_max_drawdown) / 100.0
            ev.normalized_drawdown = float(dd_price / ev.atr14)

        # ── 二次突破（带持有检验的确认突破，规格§15/§16/§18）──
        confirm_ratio = float(self.bo_cfg.get('confirm_ratio', 1.01))
        level = ev.t0_high * confirm_ratio
        confirmed_idx = None
        failed_break = False
        j = idx + 1
        while j < break_end:
            if close[j] > level:
                # 持有检验：随后3日内是否连续2日收盘跌回 T0_High 下方
                hold_ok = True
                below_cnt = 0
                for k in range(j + 1, min(break_end, j + 4)):
                    if close[k] < ev.t0_high:
                        below_cnt += 1
                        if below_cnt >= 2:
                            hold_ok = False
                            break
                    else:
                        below_cnt = 0
                if hold_ok:
                    confirmed_idx = j
                    break
                failed_break = True
                # 跳过这次失败突破，继续找下一次
                j += 3
            else:
                j += 1
        if confirmed_idx is not None:
            ev.breakout_date = str(dates[confirmed_idx])
            ev.false_breakout = False
            t20 = np.nanmean(turnover[max(0, confirmed_idx - 20):confirmed_idx]) if confirmed_idx >= 20 else 0.0
            ev.breakout_turnover_ratio = float(turnover[confirmed_idx] / t20) if t20 > 0 else 0.0
            rng = high[confirmed_idx] - low[confirmed_idx]
            ev.breakout_close_pos = float((close[confirmed_idx] - low[confirmed_idx]) / rng) if rng > 0 else 0.5
            ev.breakout_pct_above_t0_high = float(
                (close[confirmed_idx] / ev.t0_high - 1.0) * 100.0
            ) if ev.t0_high > 0 else 0.0
            ev.t0_to_breakout_days = int(confirmed_idx - idx)
            v20b = np.nanmean(vol[max(0, confirmed_idx - 20):confirmed_idx]) if confirmed_idx >= 20 else 0.0
            breakout_vol_ratio = float(vol[confirmed_idx] / v20b) if v20b > 0 else 0.0
            self._assign_signal_tier(ev, breakout_vol_ratio)
            # PRIMARY_BUY 后结构止损（规格§18）：跌破T0_High+放量+连续2日不收复 -> EXIT
            v20 = np.nanmean(vol[max(0, confirmed_idx - 20):confirmed_idx]) if confirmed_idx >= 20 else 0.0
            below = 0
            for k in range(confirmed_idx + 1, break_end):
                if close[k] < ev.t0_high and vol[k] >= v20 * float(self.ex_cfg.get('vol_down_ratio', 1.3)):
                    below += 1
                    if below >= int(self.ex_cfg.get('structural_loss_days', 2)):
                        ev.false_breakout = True
                        break
                else:
                    below = 0
        else:
            ev.false_breakout = failed_break

        # ── 出货判定（跟踪窗口内，规格§13）──
        self._check_distribution(df, ev, track_end)

        # ── 状态机 ──
        self._advance_state(df, ev, track_end)

        # ── 右侧持有跟踪（T+35~T+right_track_days，主升捕获 V3.3）──
        # 状态判定窗口在 max_track 截断，但主升浪常在 T+40+ 才展开，
        # 对存活事件继续跟踪到 right_track_days，不再 35 日即扔。
        rt_days = int(self.sm_cfg.get('right_track_days', 120))
        rt_end = min(end, idx + rt_days)
        self._track_right_tail(df, ev, idx, track_end, rt_end)
        self._eval_pullback(df, ev, end)
        return ev

    def _track_right_tail(self, df: pd.DataFrame, ev: HvtEvent, idx: int,
                          track_end: int, rt_end: int) -> None:
        """右侧持有跟踪（V3.3）：T+35 之后到 T+right_track_days 的主升捕获。

        状态判定窗口（max_track）已结束，此处不再重判状态，只对存活事件
        跟踪主升结构：最高收盘、距高点回撤、MA10 持有位、止盈信号。
        已失败事件（FAILED/DISTRIBUTION/EXIT/EVENT_SPIKE）直接返回。
        """
        if rt_end <= track_end:
            return
        # 存活判据：只排除结构性死亡（FAILED=收盘<MA60或回撤>18%，EVENT_SPIKE=脉冲失效）。
        # DISTRIBUTION/EXIT 是状态机中间产物，classify_v3 可能按"洗盘→收复→再突破"
        # 修正回存活态；右侧窗口用价格（距峰回撤+MA10）自行表达持有/离场。
        if ev.state in ('FAILED', 'EVENT_SPIKE'):
            return
        close = _arr(df, 'close')
        dates = df['trade_date'].tolist()
        seg = range(track_end, rt_end)
        if not seg:
            return
        seg_close = [float(close[j]) for j in seg]
        if not seg_close:
            return
        max_c = max(seg_close)
        max_j = track_end + seg_close.index(max_c)
        ev.right_tail_max_close = max_c
        ev.right_tail_max_date = str(dates[max_j])
        last = seg_close[-1]
        ev.right_tail_dd_from_peak = (max_c - last) / max_c * 100.0 if max_c > 0 else 0.0
        ma10 = np.nanmean(close[max(0, rt_end - 10):rt_end]) if rt_end >= 10 else float('nan')
        ev.right_tail_ma10 = float(ma10) if np.isfinite(ma10) else 0.0
        ev.right_tail_hold = bool(last > ev.right_tail_ma10)
        exit_dd = float(self.sm_cfg.get('right_tail_exit_dd', 15.0))
        ev.right_tail_exit = bool(ev.right_tail_dd_from_peak > exit_dd and not ev.right_tail_hold)

    def _eval_pullback(self, df: pd.DataFrame, ev: HvtEvent, end: int) -> None:
        """突破回踩结构（V3.4）：二次突破后缩量承接判定。

        GOOD: 回踩缩量(均量≤0.8×突破日量) + 低点守住T0_High + 当前收复突破收盘
        NEAR: 部分满足（缩量或守住但未全成）
        POOR: 放量回踩 或 破位
        """
        if not ev.breakout_date or ev.false_breakout:
            ev.pb_verdict = 'NA'
            return
        dates = df['trade_date'].tolist()
        if ev.breakout_date not in dates:
            ev.pb_verdict = 'NA'
            return
        close = _arr(df, 'close')
        low = _arr(df, 'low')
        vol = _arr(df, 'vol')
        b_idx = dates.index(ev.breakout_date)
        b_close = float(close[b_idx])
        b_vol = float(vol[b_idx])
        seg = range(b_idx + 1, end)
        if not seg or b_vol <= 0:
            ev.pb_verdict = 'NA'
            return
        seg_vol = [float(vol[j]) for j in seg]
        seg_low = [float(low[j]) for j in seg]
        v_ratio = float(np.nanmean(seg_vol) / b_vol)
        min_low = min(seg_low)
        min_j = b_idx + 1 + seg_low.index(min_low)
        cur = float(close[end - 1])
        t0h = float(ev.t0_high or 0.0)
        ev.pb_shrink_ratio = round(v_ratio, 2)
        ev.pb_low_close = min_low
        ev.pb_low_date = str(dates[min_j])
        ev.pb_low_vs_t0high = (min_low / t0h - 1.0) * 100.0 if t0h > 0 else 0.0
        ev.pb_cur_vs_t0high = (cur / t0h - 1.0) * 100.0 if t0h > 0 else 0.0
        ev.pb_cur_vs_break = (cur / b_close - 1.0) * 100.0 if b_close > 0 else 0.0
        shrink = v_ratio <= 0.8
        keep = min_low >= t0h * 0.995
        reclaim = cur >= b_close * 0.98 and cur >= t0h
        if shrink and keep and reclaim:
            ev.pb_verdict = 'GOOD'
        elif (shrink and keep) or reclaim:
            # 缩量且守住T0_High（回踩未完） / 或已收复突破收盘（回踩确认结束）
            ev.pb_verdict = 'NEAR'
        else:
            ev.pb_verdict = 'POOR'

    def _assign_signal_tier(self, ev: HvtEvent, breakout_vol_ratio: float) -> str:
        """突破日归因分层（基于1,636样本跨年验证结果）

        放量倍数采用 vol/前20日均vol 口径，与突破日归因回测完全一致。
        T1: 放量>=1.7x + 突破幅度1%~4% + T0后<=8日   独占n=292，T+20胜率61.4% (2025:61.6% / 2026:60.9%)
        T2: 放量>=2.3x + 突破幅度<=4% + T0后<=12日（T1优先，独占n=18，胜率61.1%；
            含T1重叠的完整组 n=137，胜率63.4%）
        其余符合突破条件的事件为 T3（胜率53.6%）。
        板块/业绩维度因历史快照覆盖不足（各6条）未纳入分层。
        """
        vr = breakout_vol_ratio
        amp = ev.breakout_pct_above_t0_high
        days = ev.t0_to_breakout_days
        t1 = vr >= 1.7 and 1.0 <= amp <= 4.0 and days <= 8
        t2 = vr >= 2.3 and amp <= 4.0 and days <= 12
        if t1:
            ev.signal_tier = 'T1'
        elif t2:
            ev.signal_tier = 'T2'
        else:
            ev.signal_tier = 'T3'
        return ev.signal_tier

    # ------------------------------------------------------------------
    # V3.0：双评分系统 + 分级门控 + 硬否决 + 双轴分类
    # ------------------------------------------------------------------
    def grade_close_pos(self, ev: HvtEvent) -> str:
        """收盘位置分级（V3§八）：A+/A/B/C/D，不绝对淘汰"""
        cp = ev.breakout_close_pos if ev.breakout_date else ev.t0_close_pos
        if cp >= 0.80:
            return 'A+'
        if cp >= 0.70:
            return 'A'
        if cp >= 0.60:
            return 'B'
        if cp >= 0.50:
            return 'C'
        return 'D'

    def grade_volume(self, ev: HvtEvent, breakout_vol_ratio: float = None) -> str:
        """放量分级（V3§九）：A+/A/B/C/D"""
        vr = breakout_vol_ratio if breakout_vol_ratio is not None else ev.breakout_turnover_ratio
        if vr >= 2.0:
            return 'A+'
        if vr >= 1.5:
            return 'A'
        if vr >= 1.2:
            return 'B'
        if vr >= 1.0:
            return 'C'
        return 'D'

    def compute_rs(self, ev: HvtEvent, rs_maps: dict, trade_date: str) -> None:
        """从全市场截面百分位图设置 RS5/RS10/RS20/RS加速度"""
        m = rs_maps.get(trade_date) if rs_maps else None
        if not m:
            return
        ev.rs20 = float(m.get('rs20', {}).get(ev.ts_code, np.nan)) if 'rs20' in m else np.nan
        ev.rs10 = float(m.get('rs10', {}).get(ev.ts_code, np.nan)) if 'rs10' in m else np.nan
        ev.rs5 = float(m.get('rs5', {}).get(ev.ts_code, np.nan)) if 'rs5' in m else np.nan
        if np.isfinite(ev.rs5) and np.isfinite(ev.rs20):
            ev.rs_accel = ev.rs5 - ev.rs20

    def entry_score(self, ev: HvtEvent) -> float:
        """ENTRY_SCORE：现在是否适合买（V3§五）"""
        w = self.v3_cfg.get('entry_weights', {})
        cp = ev.breakout_close_pos if ev.breakout_date else ev.t0_close_pos
        vr = ev.breakout_turnover_ratio if ev.breakout_date else ev.turnover_ratio_20

        # ① 价格结构 25：突破有效性、收盘位置、站稳T0_High、脱离平台
        s1 = 8.0
        s1 += min(8.0, cp * 8.0)                          # 收盘位置
        if ev.breakout_date and ev.breakout_pct_above_t0_high >= 1.0:
            s1 += min(5.0, ev.breakout_pct_above_t0_high * 1.5)  # 有效脱离平台
        if ev.locked_chip:
            s1 += 4.0
        s1 = min(25.0, s1)

        # ② 成交量质量 20：放量 + 有效上涨 + 上影控制
        s2 = 6.0
        s2 += min(8.0, max(0.0, (vr - 1.0)) * 5.0)        # 放量
        if ev.t0_pct_chg > 0:
            s2 += min(4.0, ev.t0_pct_chg * 0.5)
        s2 += min(4.0, (1.0 - max(0.0, 1.0 - cp)) * 4.0)   # 上影控制
        s2 = min(20.0, max(0.0, s2))

        # ③ 回踩结构 15：缩量回踩 + 守住T0_High + 回撤受控（截至当前，无前视）
        s3 = 5.0
        if ev.vol_5d_ratio <= 0.60:
            s3 += 5.0
        elif ev.vol_5d_ratio <= 0.80:
            s3 += 3.0
        if ev.post_max_drawdown <= 5.0:
            s3 += 3.0
        elif ev.post_max_drawdown <= 8.0:
            s3 += 1.5
        if ev.locked_chip:
            s3 += 2.0
        s3 = min(15.0, max(0.0, s3))

        # ④ 相对强度 15：RS20/RS10/RS5 + 加速度
        s4 = 4.0
        if np.isfinite(ev.rs20):
            s4 += ev.rs20 / 100.0 * 5.0
        if np.isfinite(ev.rs5):
            s4 += ev.rs5 / 100.0 * 4.0
        if np.isfinite(ev.rs_accel) and ev.rs_accel > 0:
            s4 += min(2.0, ev.rs_accel / 50.0 * 2.0)
        s4 = min(15.0, max(0.0, s4))

        # ⑤ 板块 10
        s5 = 0.0 if ev.sector_strength == 0 else min(10.0, ev.sector_strength / 10.0)

        # ⑥ 风险收益比 15：止损距离 + 上方空间 + ATR
        s6 = 5.0
        if ev.atr14 > 0 and ev.t0_close > 0:
            stop_dist = 1.2 * ev.atr14 / ev.t0_close * 100.0
            if 3.0 <= stop_dist <= 10.0:
                s6 += 5.0
            elif stop_dist < 3.0:
                s6 += 2.0
        if np.isfinite(ev.dist_high_120) and ev.dist_high_120 > 0:
            s6 += min(5.0, ev.dist_high_120 / 6.0)
        s6 = min(15.0, max(0.0, s6))

        subs = {'价格结构': round(s1, 1), '成交量质量': round(s2, 1), '回踩结构': round(s3, 1),
                '相对强度': round(s4, 1), '板块': round(s5, 1), '风险收益比': round(s6, 1)}
        ev.entry_subs = subs
        ev.entry_score = round(s1 + s2 + s3 + s4 + s5 + s6, 1)
        ev.close_pos_grade = self.grade_close_pos(ev)
        ev.volume_grade = self.grade_volume(ev)
        return ev.entry_score

    def expansion_score(self, df: pd.DataFrame, ev: HvtEvent, asof_idx: int = None) -> float:
        """T20_EXPANSION_SCORE：未来20日大幅扩张潜力（V3§六，核心）"""
        close = _arr(df, 'close')
        high = _arr(df, 'high')
        low = _arr(df, 'low')
        vol = _arr(df, 'vol')
        open_ = _arr(df, 'open')
        idx = asof_idx if asof_idx is not None else (ev.t0_index + ev.t0_to_breakout_days)
        idx = int(idx)
        n = len(df)
        if idx < 20 or idx >= n:
            ev.expansion_score = 50.0
            return 50.0
        c = close[idx]

        def _high(k):
            if idx - k >= 0:
                return float(np.nanmax(high[idx - k:idx + 1]))
            return float('nan')

        # ① EXPANSION_ROOM 20：上方空间（距60/120/250日高点）
        room = 0.0
        for k in (60, 120, 250):
            h = _high(k)
            if np.isfinite(h) and h > 0:
                room = max(room, (h / c - 1.0) * 100.0)
        r1 = min(20.0, max(0.0, room / 25.0 * 20.0))

        # ② COMPRESSION 15：ATR压缩 + 量能压缩 + MA20/60收敛
        tr = np.maximum(high - low, np.maximum(abs(high - np.roll(close, 1)),
                                               abs(low - np.roll(close, 1))))
        atr20 = float(np.nanmean(tr[max(0, idx - 20):idx])) if idx >= 20 else float('nan')
        atr60 = float(np.nanmean(tr[max(0, idx - 60):idx])) if idx >= 60 else float('nan')
        r2 = 5.0
        if np.isfinite(atr60) and atr60 > 0 and np.isfinite(atr20):
            if atr20 <= atr60 * 0.85:
                r2 += 5.0
            elif atr20 <= atr60:
                r2 += 3.0
        if idx >= 20:
            v20 = float(np.nanmean(vol[max(0, idx - 20):idx]))
            v5 = float(np.nanmean(vol[max(0, idx - 5):idx]))
            if v20 > 0 and v5 <= v20 * 0.7:
                r2 += 3.0
            elif v20 > 0 and v5 <= v20:
                r2 += 1.5
        ma20 = float(np.nanmean(close[max(0, idx - 19):idx + 1])) if idx >= 19 else float('nan')
        ma60 = float(np.nanmean(close[max(0, idx - 59):idx + 1])) if idx >= 59 else float('nan')
        if np.isfinite(ma20) and np.isfinite(ma60) and ma60 > 0:
            gap = abs(ma20 / ma60 - 1.0)
            if gap <= 0.03:
                r2 += 2.0
            elif gap <= 0.06:
                r2 += 1.0
        r2 = min(15.0, max(0.0, r2))

        # ③ MOMENTUM_ACCELERATION 15：加速度（5D vs 20D日均×5），衰减扣分
        def _r(k):
            if idx - k >= 0 and close[idx - k] > 0:
                return (c / close[idx - k] - 1.0) * 100.0
            return float('nan')

        r3d, r5d, r10d, r20d, r60d = _r(3), _r(5), _r(10), _r(20), _r(60)
        r3 = 6.0
        if np.isfinite(r5d) and np.isfinite(r20d):
            avg_d = r20d / 20.0
            if avg_d > 0 and r5d > avg_d * 5.0:
                r3 += 5.0
            elif r5d > 0:
                r3 += 2.0
        if np.isfinite(r3d) and np.isfinite(r5d) and r3d > r5d:
            r3 += 2.0
        if np.isfinite(r10d) and np.isfinite(r20d) and r10d > 0 and r20d > 0:
            if r5d < r10d / 2.0 and r10d < r20d / 2.0:
                r3 -= 4.0  # 动量衰减
        r3 = min(15.0, max(0.0, r3))

        # ④ RS_ACCELERATION 10：RS5 > RS10 > RS20（正在快速上升）
        r4 = 3.0
        if np.isfinite(ev.rs5) and np.isfinite(ev.rs10) and np.isfinite(ev.rs20):
            if ev.rs5 >= ev.rs10 >= ev.rs20:
                r4 += 5.0
            elif ev.rs5 >= ev.rs20:
                r4 += 3.0
            if ev.rs5 >= 80:
                r4 += 2.0
        r4 = min(10.0, max(0.0, r4))

        # ⑤ VOLUME_EFFICIENCY 10：单位成交量产生的有效推进
        r5 = 3.0
        if idx - 1 >= 0 and close[idx] > 0:
            body = (close[idx] - open_[idx]) / max(high[idx] - low[idx], 1e-9)
            rng = high[idx] - low[idx]
            upper_shadow = (high[idx] - close[idx]) / rng if rng > 0 else 0.0
            v20b = float(np.nanmean(vol[max(0, idx - 20):idx])) if idx >= 20 else float('nan')
            vr = float(vol[idx] / v20b) if np.isfinite(v20b) and v20b > 0 else 0.0
            pct = (close[idx] / close[idx - 1] - 1.0) * 100.0 if close[idx - 1] > 0 else 0.0
            if body >= 0.6:
                r5 += 3.0
            elif body >= 0.4:
                r5 += 2.0
            if upper_shadow <= 0.2:
                r5 += 2.0
            elif upper_shadow >= 0.5:
                r5 -= 2.0
            if vr > 1.0 and pct / max(vr, 1.0) > 2.0:
                r5 += 2.0  # 高量效：涨幅/量比 > 2
        r5 = min(10.0, max(0.0, r5))

        # ⑥ SUPPLY_ABSORPTION 15：天量→缩量→不跌→再突破（最核心）
        r6 = 4.0
        if ev.locked_chip:
            r6 += 5.0
        if ev.strong_locked_chip:
            r6 += 3.0
        if ev.post_max_drawdown <= 4.0:
            r6 += 3.0
        elif ev.post_max_drawdown <= 8.0:
            r6 += 1.5
        if ev.breakout_date and ev.breakout_pct_above_t0_high > 0:
            r6 += 2.0
        r6 = min(15.0, max(0.0, r6))

        # ⑦ FUNDAMENTAL_ACCELERATION 10：盈利/订单加速
        r7 = 5.0  # 无历史覆盖时中性，标 SAMPLE_LOW
        if ev.fundamental_score > 0 and ev.fundamental_score != 50.0:
            if ev.fundamental_score >= 90:
                r7 = 10.0
            elif ev.fundamental_score >= 78:
                r7 = 8.0
            elif ev.fundamental_score >= 65:
                r7 = 6.0
            else:
                r7 = 3.0

        # ⑧ CATALYST 5：无结构化数据源，固定中性并声明 SAMPLE_LOW
        r8 = 3.0

        v3 = self.v3_cfg
        q40_120 = float(v3.get('rally_r120_q40', 23.3))
        q20_120 = float(v3.get('rally_r120_q20', 41.2))
        q40_250 = float(v3.get('rally_r250_q40', 35.4))
        q20_250 = float(v3.get('rally_r250_q20', 67.8))
        rally_bonus = 0.0
        if np.isfinite(ev.r120):
            if ev.r120 >= q20_120:
                rally_bonus += 2.0
            elif ev.r120 >= q40_120:
                rally_bonus += 1.0
        if np.isfinite(ev.r250):
            if ev.r250 >= q20_250:
                rally_bonus += 3.0
            elif ev.r250 >= q40_250:
                rally_bonus += 2.0
        if np.isfinite(ev.r120) and np.isfinite(ev.r250) and \
                ev.r120 >= q20_120 and ev.r250 >= q20_250:
            rally_bonus += 1.0
        rally_bonus = min(float(v3.get('rally_bonus_cap', 8)), rally_bonus)

        subs = {'扩张空间': round(r1, 1), '压缩结构': round(r2, 1), '动量加速': round(r3, 1),
                'RS加速': round(r4, 1), '量效': round(r5, 1), '供给吸收': round(r6, 1),
                '基本面加速': round(r7, 1), '催化剂': round(r8, 1), '中长期涨幅': round(rally_bonus, 1)}
        ev.exp_subs = subs
        ev.expansion_score = round(r1 + r2 + r3 + r4 + r5 + r6 + r7 + r8 + rally_bonus, 1)
        return ev.expansion_score

    def hard_veto(self, ev: HvtEvent, df: pd.DataFrame = None, asof_idx: int = None) -> list:
        """硬否决（V3§十三）：仅真正重大风险，不因CP/量略低否决"""
        veto = []
        if ev.false_breakout:
            veto.append('假突破/结构止损')
        # 结构状态否决：仅当无有效突破时才成立。
        # 决策时点（截至突破日）的 DISTRIBUTION 由 _check_distribution 扫描
        # [事件日+1, 突破日] 得到，任意一日收盘跌破 t0_low 即触发——这正是
        # "天量→洗盘跌破→收复→再突破"的供给吸收形态（V3§六⑥），并非派发。
        # 全量回测 n=1636 验证：被该条误杀的事件 T+20 胜率 62.7%、均值+9.57%、
        # P90 30.9%，显著强于未被拦截组（47.4%/+3.45%/P90 23.2），故已确认
        # 有效突破（breakout_date 且非假突破）时不得否决。
        # FAILED/EXIT 由突破后失败（false_breakout/跌破MA60）派生，仍否决。
        if ev.state in ('FAILED', 'DISTRIBUTION', 'EXIT') and \
                not (ev.breakout_date and not ev.false_breakout):
            veto.append(f'结构状态:{ev.state}')
        # 流动性不足：市值过低
        if ev.t0_amount > 0 and ev.t0_amount < 2000.0:
            veto.append('流动性不足')
        # 长上影+巨量+收盘弱（派发结构）
        cp = ev.breakout_close_pos if ev.breakout_date else ev.t0_close_pos
        vr = ev.breakout_turnover_ratio if ev.breakout_date else ev.turnover_ratio_20
        if cp < 0.50 and vr >= 2.0:
            veto.append('巨量长上影收盘弱(派发风险)')
        # 基本面重大恶化（仅当有真实基本面数据）
        if ev.fundamental_score > 0 and ev.fundamental_score < 40.0 and ev.fundamental_grade == 'C':
            veto.append('基本面重大恶化')
        ev.hard_veto = veto
        return veto

    def apply_tail_calibration(self, ev: HvtEvent, calib: dict = None) -> float:
        """T20_TAIL_SCORE：用历史校准表加权概率（V3§十一）"""
        if calib and calib.get('bands'):
            bands = calib['bands']
            es = ev.expansion_score
            row = None
            for b in bands:
                lo, hi = b['lo'], b['hi']
                if es >= lo and (hi is None or es < hi):
                    row = b
                    break
            if row and row.get('n', 0) >= 30:
                p10, p20, p30, p50 = (row.get('p10', 0), row.get('p20', 0),
                                      row.get('p30', 0), row.get('p50', 0))
                e_ret = row.get('mean', 0)
                ev.tail_score = round(0.30 * p20 + 0.25 * p30 + 0.20 * p50
                                      + 0.15 * min(20.0, max(0.0, e_ret))
                                      + 0.10 * p10, 1)
                ev.tail_calibrated = True
                return ev.tail_score
        # 样本不足/无校准表：退回扩张分 + 标记
        ev.tail_score = round(0.85 * ev.expansion_score + 0.15 * ev.entry_score, 1)
        ev.tail_calibrated = False
        return ev.tail_score

    def classify_v3(self, ev: HvtEvent) -> str:
        """双轴分类（V3§十）：PRIMARY / T20_ROCKET / CONFIRMED / BREAKOUT_READY / WATCH

        V3.2 门控（来自无前视全量回测 n=1610 的 Gate Ablation，§18）：
          PRIMARY = ENTRY>=70 + trio 三要素，即
            供给吸收 >= 12（天量→缩量→不跌→再突破，最强右尾门控）
            放量分级 A/A+
            RS20 >= 70（相对强度加速）
          证据（决策时点无泄漏口径，r_break_20）：es>=70&trio 组 n=724，
          T+20 胜率 56.6%、均值 +7.14%、P90 28.98、≥20% 16.2%、≥30% 9.4%、
          ≥50% 4.4%、Top10均收 60.4%、Top10贡献 84.1%（RIGHT_TAIL），
          全面优于基线（n=1610，胜率55.7%、均值+5.61%、P90 26.06）。

          扩张分硬门控已按§18规则降权删除：
            - es>=70&xs>=70&trio → n=178，P90 25.57/≥20% 14.6%，右尾劣于
              无扩张门控组（P90 28.98/≥20% 16.2%），即"提高扩张分门槛牺牲
              右尾收益"，违反第一目标，故删除硬门控，扩张维度改由
              T20_TAIL_SCORE 校准表（扩张分区间经验概率）在排序中体现。
            - 结构状态否决修正：决策时点 DISTRIBUTION 实为"洗盘→收复→再突破"
              供给吸收形态（被误杀组 n=437，胜率62.7%/均值+9.57%/P90 30.9，
              显著强于未被拦截组 n=287，胜率47.4%/+3.45%/P90 23.2），
              已确认有效突破时不再否决（见 hard_veto）。
            - T20_ROCKET_WATCH 阈值由 85 下调至 75：扩张分分布 p90≈76、max≈87.5，
              原阈值实际不可达（全量仅 2~3 事件）。ROCKET 为"观察/待确认"池，
              独立右尾弱于基线（n≈53~105，均值+1.3%），不做买入，仅供跟踪。
        """
        veto = ev.hard_veto or self.hard_veto(ev)
        if veto:
            # 硬否决禁止 PRIMARY/ROCKET/买入：风险状态原样保留，
            # 其余降级为 WATCH（观察，不可买），避免被否决事件仍标成买入状态
            if ev.state in ('FAILED', 'DISTRIBUTION', 'EXIT', 'EVENT_SPIKE'):
                return ev.state
            ev.state = 'WATCH'
            return ev.state
        es = ev.entry_score
        xs = ev.expansion_score
        th_p = float(self.v3_cfg.get('primary_entry', 70))
        th_rocket = float(self.v3_cfg.get('rocket_expansion', 75))
        supply = (ev.exp_subs or {}).get('供给吸收', 0.0)
        vg = ev.volume_grade or ''
        rs_ok = np.isfinite(ev.rs20) and ev.rs20 >= 70.0
        if es >= th_p and supply >= 12.0 and vg in ('A', 'A+') and rs_ok:
            ev.state = 'PRIMARY_BUY'
        elif xs >= th_rocket:
            ev.state = 'T20_ROCKET_WATCH'
        elif ev.breakout_date and ev.locked_chip:
            ev.state = 'BREAKOUT_READY'
        elif ev.locked_chip:
            ev.state = 'LOCKED'
        else:
            ev.state = 'HVT_STRONG'
        return ev.state

    def _check_distribution(self, df: pd.DataFrame, ev: HvtEvent, track_end: int) -> None:
        close = _arr(df, 'close')
        vol = _arr(df, 'vol')
        idx = ev.t0_index
        v20 = np.nanmean(vol[max(0, idx - 20):idx]) if idx >= 20 else 0.0
        if v20 <= 0:
            return
        seg = range(idx + 1, track_end)
        # 1) 收盘 < T0_Low
        if any(close[j] < ev.t0_low for j in seg):
            ev.state = 'DISTRIBUTION'
            return
        # 2) 连续3日放量下跌
        cnt = 0
        for j in seg:
            if close[j] < close[j - 1] and vol[j] >= v20 * float(self.ex_cfg.get('vol_down_ratio', 1.3)):
                cnt += 1
                if cnt >= 3:
                    ev.state = 'DISTRIBUTION'
                    return
            else:
                cnt = 0

    def _advance_state(self, df: pd.DataFrame, ev: HvtEvent, track_end: int) -> None:
        close = _arr(df, 'close')
        end = track_end
        ma60 = np.nanmean(close[max(0, end - 60):end]) if end >= 60 else float('nan')
        if ev.state == 'DISTRIBUTION':
            if np.isfinite(ma60) and close[end - 1] < ma60:
                ev.state = 'FAILED'
            return
        if not self.price_strength_ok(ev):
            ev.state = 'EVENT_SPIKE'
            return
        if ev.false_breakout:
            ev.state = 'EXIT'
            return
        if ev.breakout_date:
            if ev.locked_chip and ev.breakout_turnover_ratio >= float(self.bo_cfg.get('min_turnover_ratio', 1.3)) \
                    and ev.breakout_close_pos >= float(self.bo_cfg.get('min_close_pos', 0.75)):
                ev.state = 'PRIMARY_BUY' if ev.signal_tier in ('T1', 'T2') else 'BREAKOUT_READY'
                # 突破后继续创新高 -> CONFIRMED
                if ev.breakout_date in df['trade_date'].tolist():
                    b_idx = df['trade_date'].tolist().index(ev.breakout_date)
                    if end - 1 > b_idx and close[end - 1] > close[b_idx]:
                        if ev.signal_tier in ('T1', 'T2'):
                            ev.state = 'CONFIRMED'
                        else:
                            ev.state = 'BREAKOUT_READY'
            else:
                ev.state = 'BREAKOUT_READY' if ev.locked_chip else 'HVT_STRONG'
            return
        if ev.locked_chip:
            ev.state = 'LOCKED'
        elif ev.days_after >= 3 and ev.vol_5d_ratio < 0.85 and ev.post_max_drawdown <= 8.0:
            ev.state = 'LOCKING'
        elif ev.post_max_drawdown > float(self.dd_cfg.get('fail', 18.0)):
            ev.state = 'FAILED'
        elif ev.days_after >= int(self.sm_cfg.get('max_locked_days', 30)):
            ev.state = 'EVENT_SPIKE'
        else:
            ev.state = 'HVT_STRONG'

    # ------------------------------------------------------------------
    # 阶段4: 综合评分（按实际权重归一）
    # ------------------------------------------------------------------
    def score(self, ev: HvtEvent) -> float:
        w = self.sc_cfg.get('weights', {})
        subs = {}
        subs['hvt_exception'] = self._s_hvt(ev)
        subs['price_strength'] = self._s_price(ev)
        subs['trend_structure'] = self._s_trend(ev)
        subs['money_quality'] = self._s_money(ev)
        subs['chip_lock'] = self._s_lock(ev)
        subs['drawdown_quality'] = self._s_dd(ev)
        subs['breakout_strength'] = self._s_breakout(ev)
        subs['sector_resonance'] = ev.sector_strength if ev.sector_strength else 50.0
        subs['fundamental'] = ev.fundamental_score if ev.fundamental_score else 50.0
        ev.subs = {k: round(v, 1) for k, v in subs.items()}
        used = [(subs[k], w.get(k, 0)) for k in subs if k in w and w.get(k, 0) > 0]
        total_w = sum(x[1] for x in used)
        total = sum(v * wk for v, wk in used)
        ev.score = round(float(min(100.0, max(0.0, total / total_w))) * (total_w / 100.0), 1) if total_w > 0 else 0.0
        g = self.grades_cfg
        if ev.score >= g.get('super_bull', 90):
            ev.grade = 'HVT_SUPER_BULL'
        elif ev.score >= g.get('bull', 85):
            ev.grade = 'HVT_BULL'
        elif ev.score >= g.get('watch', 78):
            ev.grade = 'HVT_WATCH'
        elif ev.score >= g.get('observe', 70):
            ev.grade = 'HVT_OBSERVE'
        else:
            ev.grade = ''
        return ev.score

    def _s_hvt(self, ev: HvtEvent) -> float:
        if ev.hvt_grade == 'A':
            s = 95.0
        elif ev.hvt_grade == 'B':
            s = 80.0
        else:
            s = 62.0
        if ev.turnover_ratio_20 > 2:
            s += min(5.0, (ev.turnover_ratio_20 - 2.0) * 2.0)
        return min(100.0, s)

    def _s_price(self, ev: HvtEvent) -> float:
        if ev.t0_pct_chg >= float(self.ps_cfg.get('super_pct_chg', 7.0)) and \
                ev.t0_close_pos >= float(self.ps_cfg.get('super_close_pos', 0.85)):
            s = 100.0
        elif ev.t0_pct_chg >= float(self.ps_cfg.get('strong_pct_chg', 5.0)) and \
                ev.t0_close_pos >= float(self.ps_cfg.get('strong_close_pos', 0.80)):
            s = 85.0
        elif ev.t0_pct_chg >= float(self.ps_cfg.get('min_pct_chg', 3.0)) and \
                ev.t0_close_pos >= float(self.ps_cfg.get('min_close_pos', 0.70)):
            s = 70.0
        else:
            s = 30.0
        if ev.t0_body > 0.3:
            s += (ev.t0_body - 0.3) * 30.0
        return min(100.0, max(0.0, s))

    def _s_trend(self, ev: HvtEvent) -> float:
        s = 50.0
        if np.isfinite(ev.ma20) and np.isfinite(ev.ma60):
            if ev.ma20 > ev.ma60:
                s += 15
            if ev.t0_close > ev.ma20:
                s += 10
        if ev.ma20_slope > 0:
            s += 10
        if ev.ma60_slope >= 0:
            s += 5
        if ev.platform_breakout:
            s += 10
        gs = self.gs_cfg
        if np.isfinite(ev.r20) and gs.get('r20_ideal_min', 10) <= ev.r20 <= gs.get('r20_ideal_max', 40):
            s += 5
        if np.isfinite(ev.r60) and gs.get('r60_ideal_min', 10) <= ev.r60 <= gs.get('r60_ideal_max', 80):
            s += 5
        if np.isfinite(ev.dist_high_120) and gs.get('dist_high_120_min', 5) <= ev.dist_high_120 <= gs.get('dist_high_120_max', 30):
            s += 5
        return min(100.0, max(0.0, s))

    def _s_money(self, ev: HvtEvent) -> float:
        return ev.money_quality_score if ev.money_quality_score else 50.0

    def _s_lock(self, ev: HvtEvent) -> float:
        if ev.strong_locked_chip:
            return 100.0
        if ev.locked_chip:
            return 85.0
        if ev.vol_5d_ratio <= 0.5:
            return 65.0
        if ev.vol_5d_ratio <= 0.6:
            return 55.0
        if ev.vol_5d_ratio <= 0.8:
            return 40.0
        return 25.0

    def _s_dd(self, ev: HvtEvent) -> float:
        dd = ev.post_max_drawdown
        if ev.normalized_drawdown and ev.normalized_drawdown > 0:
            nd = ev.normalized_drawdown
            if nd <= float(self.dd_cfg.get('atr_norm_denom', 2.5)) * 0.6:
                return 95.0
            if nd <= float(self.dd_cfg.get('atr_norm_denom', 2.5)):
                return 80.0
        if dd <= self.dd_cfg.get('star5', 5.0):
            return 100.0
        if dd <= self.dd_cfg.get('star4', 8.0):
            return 85.0
        if dd <= self.dd_cfg.get('star3', 12.0):
            return 65.0
        if dd <= self.dd_cfg.get('star2', 18.0):
            return 45.0
        return 20.0

    def _s_breakout(self, ev: HvtEvent) -> float:
        if not ev.breakout_date:
            return 35.0 if ev.locked_chip else 20.0
        if ev.breakout_turnover_ratio >= float(self.bo_cfg.get('strong_turnover_ratio', 1.5)):
            s = 90.0
        elif ev.breakout_turnover_ratio >= float(self.bo_cfg.get('min_turnover_ratio', 1.3)):
            s = 75.0
        else:
            s = 55.0
        if ev.breakout_close_pos >= float(self.bo_cfg.get('min_close_pos', 0.75)):
            s += 8
        if ev.false_breakout:
            s = 25.0
        return min(100.0, s)

    # ------------------------------------------------------------------
    # 阶段5: WAIT_REASON（规格§33：非PRIMARY_BUY必须说明为什么现在不买）
    # ------------------------------------------------------------------
    def wait_reasons(self, ev: HvtEvent) -> list:
        reasons = []
        if ev.state == 'PRIMARY_BUY':
            return reasons
        if not ev.locked_chip:
            reasons.append('等待缩量锁筹')
        if ev.days_after < 3:
            reasons.append('天量后观察不足3日')
        if not ev.breakout_date:
            reasons.append('等待突破T0_High')
        if ev.breakout_date and ev.false_breakout:
            reasons.append('假突破/结构止损，等待重新站上T0_High')
        if ev.breakout_date and not ev.false_breakout and ev.signal_tier == 'T3':
            reasons.append('突破日特征未达T1/T2归因分层（放量倍数/突破幅度/距T0天数）')
        if ev.sector_strength and ev.sector_strength < 60:
            reasons.append('板块强度不足')
        if ev.money_quality_score < 40:
            reasons.append('资金承接不足')
        if ev.post_max_drawdown > 8:
            reasons.append('回撤过大')
        if ev.fundamental_score and ev.fundamental_score < 50:
            reasons.append('基本面不足')
        if ev.state in ('FAILED', 'DISTRIBUTION', 'EXIT', 'EVENT_SPIKE'):
            reasons.append(f'结构状态:{ev.state}')
        if not reasons:
            reasons.append('等待板块/资金共振确认')
        return reasons

    # ------------------------------------------------------------------
    # 阶段6: 交易计划（规格§17~§19）
    # ------------------------------------------------------------------
    def build_trade_plan(self, ev: HvtEvent) -> None:
        if ev.state in ('PRIMARY_BUY', 'CONFIRMED'):
            ev.entry = round(ev.t0_high * 1.01, 2)
            ev.stop_loss = round(max(ev.t0_high * 0.95, ev.entry - 1.2 * ev.atr14), 2)
            platform = ev.t0_low
            ev.target1 = round(ev.entry + (ev.entry - platform), 2)
            ev.target2 = round(ev.entry + 3.0 * ev.atr14, 2)
        elif ev.state in ('LOCKED', 'LOCKING', 'BREAKOUT_READY'):
            ev.entry = round(ev.t0_high * 1.01, 2)
            ev.stop_loss = round(ev.t0_low, 2)
            ev.target1 = round(ev.t0_high * 1.20, 2)
            ev.target2 = round(ev.t0_high * 1.35, 2)
        else:
            ev.stop_loss = round(ev.t0_low, 2)


# 案例特征向量（相似度模型，规格§25）
CASE_FEATURES = {
    '300308.SZ': {  # 中际旭创 2025-05-08
        'date': '20250508',
        'features': {
            'turnover_rank': 1, 'turnover_ratio': 2.5, 'pct_chg': 11.36,
            'close_pos': 0.85, 'ma20_slope': 3.0, 'ma60_slope': 0.0,
            'r20': 28.0, 'r60': -10.0, 'dist_high': 15.0,
            'volume_contraction': 0.48, 'post_drawdown': 6.6,
        },
    },
    '603186.SH': {  # 华正新材 2025-08-12
        'date': '20250812',
        'features': {
            'turnover_rank': 1, 'turnover_ratio': 3.2, 'pct_chg': 3.99,
            'close_pos': 0.81, 'ma20_slope': 3.7, 'ma60_slope': 1.5,
            'r20': 16.0, 'r60': 38.0, 'dist_high': 1.0,
            'volume_contraction': 0.53, 'post_drawdown': 2.7,
        },
    },
    '601882.SH': {  # 海天精工 2026-08-11
        'date': '20260811',
        'features': {
            'turnover_rank': 2, 'turnover_ratio': 5.3, 'pct_chg': 5.89,
            'close_pos': 0.84, 'ma20_slope': 2.6, 'ma60_slope': 1.0,
            'r20': 9.0, 'r60': 4.0, 'dist_high': 11.0,
            'volume_contraction': 0.72, 'post_drawdown': 6.2,
        },
    },
}


def similarity(ev: HvtEvent, config: dict = None) -> float:
    """与历史牛股案例的特征相似度 0~100（辅助变量，不能代替当前行情判断）"""
    cfg = (config or {}).get('similarity', {})
    w = cfg.get('weights', {})
    if not w:
        w = {'turnover_rank': 8, 'turnover_ratio': 12, 'pct_chg': 10, 'close_pos': 10,
             'ma20_slope': 8, 'ma60_slope': 5, 'r20': 8, 'r60': 8, 'dist_high': 8,
             'volume_contraction': 12, 'post_drawdown': 11}
    ev_feat = {
        'turnover_rank': ev.hvt_rank_250,
        'turnover_ratio': ev.turnover_ratio_20,
        'pct_chg': ev.t0_pct_chg,
        'close_pos': ev.t0_close_pos,
        'ma20_slope': ev.ma20_slope * 100,
        'ma60_slope': ev.ma60_slope * 100,
        'r20': ev.r20 if np.isfinite(ev.r20) else 0,
        'r60': ev.r60 if np.isfinite(ev.r60) else 0,
        'dist_high': ev.dist_high_120 if np.isfinite(ev.dist_high_120) else 0,
        'volume_contraction': ev.vol_5d_ratio,
        'post_drawdown': ev.post_max_drawdown,
    }
    scale = {
        'turnover_rank': (1, 10), 'turnover_ratio': (1.5, 6.0), 'pct_chg': (0, 15),
        'close_pos': (0.5, 1.0), 'ma20_slope': (-5, 10), 'ma60_slope': (-3, 6),
        'r20': (-20, 60), 'r60': (-20, 100), 'dist_high': (0, 40),
        'volume_contraction': (0.3, 1.2), 'post_drawdown': (0, 15),
    }

    def _norm(k, v):
        lo, hi = scale.get(k, (0, 1))
        return (v - lo) / (hi - lo) if hi > lo else 0.5

    best = 0.0
    for case in CASE_FEATURES.values():
        f = case['features']
        sim_w = 0.0
        total_w = 0.0
        for k, wk in w.items():
            if k not in f or wk <= 0:
                continue
            a = _norm(k, ev_feat.get(k, 0))
            b = _norm(k, f[k])
            sim_w += wk * (1.0 - min(1.0, abs(a - b)))
            total_w += wk
        if total_w > 0:
            best = max(best, sim_w / total_w * 100.0)
    return round(best, 1)
