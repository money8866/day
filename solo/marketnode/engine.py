# -*- coding: utf-8 -*-
"""规则引擎：滚动分位数 + 节点识别 + 状态判断。

设计原则：
    - 不建复杂模型，纯规则，透明可解释。
    - 情绪水平高/低 ≠ 立即见顶/见底，必须结合「边际变化」。
    - 高潮后情绪恶化 > 单纯高潮；冰点后多维改善才是修复。
    - 拒绝使用未来数据（滚动窗口只含当日及以前）。
"""
import json
import os

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _rolling_pct(s, window, min_periods):
    """滚动分位数：当日值在「截至当日的窗口」内的百分比位置（0-100）。"""
    def pct(win):
        v = win.iloc[-1]
        if pd.isna(v):
            return np.nan
        return float((win <= v).mean() * 100)
    return s.rolling(window, min_periods=min_periods).apply(pct, raw=False)


class MarketNodeEngine:
    """大盘节点识别引擎。"""

    def __init__(self, cfg, feat):
        self.cfg = cfg
        self.th = cfg["thresholds"]
        self.pwin = cfg["percentile"]["window"]
        self.pmin = cfg["percentile"]["min_history"]
        self.feat = feat.copy()
        self._compute_derived()
        self._sig_cache = None
        self._raw_cache = {}
        self._judge_cache = {}
        self._base = None

    # ------------------------------------------------------------------
    def _compute_derived(self):
        f = self.feat
        th = self.th
        # 滚动分位数
        for col in ["zt_count", "dt_count", "zhaban_rate", "promo12", "promo23",
                    "max_board", "zt_y_pcnt", "lb_y_pcnt", "up_ratio",
                    "above_ma20_ratio", "amount_ratio", "new_high", "new_low"]:
            if col not in f.columns:
                f[col] = np.nan
            f[f"pct_{col}"] = _rolling_pct(f[col], self.pwin, self.pmin)

        # 情绪温度（0-100）
        parts = [
            f["pct_zt_count"],
            f["pct_max_board"],
            100.0 - f["pct_zhaban_rate"],
            f["pct_promo12"],
            f["pct_zt_y_pcnt"],
        ]
        stacked = pd.concat(parts, axis=1)
        f["sentiment"] = stacked.mean(axis=1)
        f["sentiment_d1"] = f["sentiment"].diff(1)

        # 反转信号（封板质量恶化 / 晋级下降 / 反馈转弱）
        f["zhaban_jump"] = f["zhaban_rate"] - f["zhaban_rate"].shift(1)
        f["promo12_drop"] = f["promo12"] - f["promo12"].shift(1)
        f["zt_feedback_drop"] = f["zt_y_pcnt"] - f["zt_y_pcnt"].shift(1)

    # ------------------------------------------------------------------
    # 单信号判定
    # ------------------------------------------------------------------
    def _signals(self):
        if self._sig_cache is not None:
            return self._sig_cache
        f = self.feat
        th = self.th
        self._sig_cache = {
            "cold": f["pct_zt_count"] <= th["zt_low_pct"],
            "deep_cold": f["pct_zt_count"] <= th["zt_deep_low_pct"],
            "hot": f["pct_zt_count"] >= th["zt_extreme_pct"],
            "fear": (f["pct_dt_count"] >= 80.0) | (f["dt_count"] >= th["dt_extreme"]),
            "promo_weak": (f["promo12"] < th["promo_weak"]) | (f["promo23"] < th["promo_weak"]),
            "feedback_bad": f["zt_y_pcnt"] < th["feedback_neg"],
            "feedback_strong_bad": f["lb_y_pcnt"] < th["feedback_strong_neg"],
            "breadth_bad": f["up_ratio"] <= th["up_ratio_weak"],
            "breadth_good": f["up_ratio"] >= th["up_ratio_strong"],
            "ma20_strong": f["above_ma20_ratio"] >= th["above_ma20_strong"],
            "volume_expand": f["amount_ratio"] >= th["amount_expand_ratio"],
            "volume_shrink": f["amount_ratio"] <= th["amount_shrink_ratio"],
            "zhaban_high": f["zhaban_rate"] >= th["zhaban_high"],
            "index_weak": f["index_pct"] < 0,
            "small_good": f["small_pct"] > 0,
        }
        return self._sig_cache

    # ------------------------------------------------------------------
    # 单日原始判定（不含 2 日确认）；n6_ok / n5_ok 为前置保护开关
    # ------------------------------------------------------------------
    def _judge(self, trade_date, n6_ok=True, n5_ok=True):
        key = (trade_date, bool(n6_ok), bool(n5_ok))
        if key in self._judge_cache:
            return self._judge_cache[key]
        f = self.feat
        if trade_date not in f.index:
            return {"node_type": None, "sub_state": None, "node_score": 0.0, "reason_codes": []}
        s = f.loc[trade_date]
        sg = {k: bool(v.loc[trade_date]) if not pd.isna(v.loc[trade_date]) else False
              for k, v in self._signals().items()}

        def val(col):
            return s.get(col, np.nan) if col in s.index else np.nan

        th = self.th
        reason = []
        d = {}  # 证据明细

        # 辅助 zt_count 变化
        zt_d1 = val("zt_count_d1") if pd.notna(val("zt_count_d1")) else 0.0
        zt_d3 = val("zt_count_d3") if pd.notna(val("zt_count_d3")) else 0.0
        dt_d1 = val("dt_count_d1") if pd.notna(val("dt_count_d1")) else 0.0
        up_d1 = val("up_ratio_d1") if pd.notna(val("up_ratio_d1")) else 0.0
        zhaban_jump = val("zhaban_jump") if pd.notna(val("zhaban_jump")) else 0.0
        promo12_drop = val("promo12_drop") if pd.notna(val("promo12_drop")) else 0.0
        max_board_d1 = val("max_board_d1") if pd.notna(val("max_board_d1")) else 0.0
        feedback_drop = val("zt_feedback_drop") if pd.notna(val("zt_feedback_drop")) else 0.0

        node_type = None
        sub_state = None
        score = 0.0

        # ---- N6 退潮确认（前置：前段需处于进攻/高潮，避免弱势中误报退潮）----
        if n6_ok and (sg["feedback_strong_bad"] or (sg["feedback_bad"] and sg["breadth_bad"] and zhaban_jump > th["divergence_zhaban_jump"])) \
                and (max_board_d1 < 0 or sg["breadth_bad"]):
            node_type = "N6"
            reason = ["FEEDBACK_STRONG_NEG", "LADDER_SHRINK", "BREADTH_BAD"]
            score = 100.0 - val("pct_zt_count") if pd.notna(val("pct_zt_count")) else 50.0

        # ---- N1 情绪冰点 ----
        elif sg["deep_cold"] and (sg["fear"] or sg["promo_weak"] or sg["feedback_bad"]):
            node_type = "N1"
            reason = ["ZT_DEEP_LOW"]
            if sg["fear"]:
                reason.append("DT_SURGE")
            if sg["promo_weak"]:
                reason.append("PROMO_WEAK")
            if sg["feedback_bad"]:
                reason.append("FEEDBACK_NEG")
            score = max(0.0, 100.0 - val("pct_zt_count") if pd.notna(val("pct_zt_count")) else 0.0)
        elif sg["cold"] and ((sg["fear"] or sg["promo_weak"] or sg["feedback_bad"]) and
                             (int(sg["fear"]) + int(sg["promo_weak"]) + int(sg["feedback_bad"]) >= 2)):
            node_type = "N1"
            reason = ["ZT_LOW"]
            if sg["fear"]:
                reason.append("DT_SURGE")
            if sg["promo_weak"]:
                reason.append("PROMO_WEAK")
            if sg["feedback_bad"]:
                reason.append("FEEDBACK_NEG")
            score = max(0.0, 100.0 - val("pct_zt_count") if pd.notna(val("pct_zt_count")) else 0.0)

        # ---- N5 极度高潮（前置：近 3 日无冰点，避免冰点反弹被标为高潮）----
        elif n5_ok and sg["hot"]:
            node_type = "N5"
            reason = ["ZT_EXTREME"]
            score = val("pct_zt_count") if pd.notna(val("pct_zt_count")) else 90.0
            # 子状态
            if zhaban_jump > th["divergence_zhaban_jump"] or promo12_drop < -th["divergence_promo_drop"] or feedback_drop < -1.0:
                sub_state = "高位分歧"
                reason.append("DIVERGENCE")
            elif zt_d1 < 0 and (sg["feedback_bad"] or sg["breadth_bad"]):
                sub_state = "高潮退潮"
                reason.append("CLIMAX_FADE")
            else:
                sub_state = "高潮加速"
                reason.append("ACCELERATE")

        # ---- N2 冰点修复 ----
        elif self._recently_ice(trade_date) and self._multi_improve(trade_date):
            node_type = "N2"
            sub_state = "冰点修复"
            reason = ["ICE_REPAIR"]
            score = val("pct_zt_count") if pd.notna(val("pct_zt_count")) else 60.0
            if dt_d1 < 0:
                reason.append("DT_DECREASE")
            if zt_d1 > 0:
                reason.append("ZT_SPREAD")
            if up_d1 > 0:
                reason.append("BREADTH_RECOVER")

        # ---- N3 增量启动 ----
        elif sg["volume_expand"] and (up_d1 > 0 or sg["breadth_good"]) and (sg["small_good"] or not sg["index_weak"]) and zt_d1 >= 0:
            node_type = "N3"
            reason = ["VOLUME_EXPAND"]
            if sg["breadth_good"]:
                reason.append("BREADTH_EXPAND")
            score = min(100.0, val("pct_amount_ratio") if pd.notna(val("pct_amount_ratio")) else 70.0)

        # ---- N4 主线扩张 / 主升确认 ----
        elif self._mainline_confirm(trade_date):
            node_type = "N4"
            reason = ["MAINLINE_EXPAND"]
            score = val("pct_above_ma20_ratio") if pd.notna(val("pct_above_ma20_ratio")) else 60.0

        res = {
            "node_type": node_type,
            "sub_state": sub_state,
            "node_score": round(float(score), 1),
            "reason_codes": reason,
        }
        self._judge_cache[key] = res
        return res

    # ------------------------------------------------------------------
    # 前置保护：N6 需前段处于进攻/高潮；N5 需近 3 日无冰点
    # ------------------------------------------------------------------
    def _ensure_base(self):
        """预计算全序列「无前置保护」节点（只用当日及历史数据），供前置判断引用。"""
        if self._base is None:
            self._base = {td: self._judge(td)["node_type"] for td in self.feat.index}

    def _raw(self, trade_date):
        self._ensure_base()
        td = str(trade_date)
        if td in self._raw_cache:
            return self._raw_cache[td]
        idx = list(self.feat.index)
        i = idx.index(td)
        guard = self.cfg.get("guard", {})
        n6_look = int(guard.get("n6_heat_lookback", 5))
        n6_min = float(guard.get("n6_heat_min_pct", 70.0))
        n5_look = int(guard.get("n5_lookback", 3))
        # N6 前置：前 n6_look 日须出现过高潮（涨停分位峰值达标），避免弱势中误报退潮
        peak = self.feat["pct_zt_count"].iloc[max(0, i - n6_look):i].dropna()
        n6_ok = bool(len(peak)) and float(peak.max()) >= n6_min
        # N5 前置：近 n5_look 日不得出现冰点
        lo = max(0, i - n5_look)
        n5_ok = not any(self._base[idx[k]] == "N1" for k in range(lo, i))
        r = self._judge(td, n6_ok=n6_ok, n5_ok=n5_ok)
        self._raw_cache[td] = r
        return r

    # ------------------------------------------------------------------
    # 对外：节点判定（含连续 2 日确认；进攻状态需确认后才给出）
    # ------------------------------------------------------------------
    def detect_date(self, trade_date):
        """返回当日节点判定结果 dict。

        confirmed = 节点已成立：需连续 2 日同节点；N6 退潮确认自带确认语义，单日即成立。
        """
        td = str(trade_date)
        f = self.feat
        if td not in f.index:
            return {"trade_date": td, "node_type": None, "confirmed": False,
                    "sub_state": None, "sentiment_state": "常态", "market_state": "观察",
                    "node_score": 0.0, "confidence": "低", "reason_codes": [],
                    "invalidation_conditions": "", "missing_fields": []}
        raw = self._raw(td)
        nt = raw["node_type"]
        idx = list(f.index)
        i = idx.index(td)
        prev_nt = self._raw(idx[i - 1])["node_type"] if i > 0 else None
        need_confirm = self.cfg.get("confirm", {}).get("require_nodes", ["N1", "N2", "N3", "N4", "N5"])
        confirmed = bool(nt) and (nt not in need_confirm or nt == prev_nt)
        downgrade = self.cfg.get("confirm", {}).get("downgrade_unconfirmed", ["N1", "N2", "N3", "N4"])

        if nt is None:
            sentiment_state, market_state = "常态", "观察"
        else:
            sentiment_state, market_state = self._state_from_node(nt, raw["sub_state"], td)
            # 未确认节点只给「观察」：避免单日冲高/单日急跌带来的一日游调仓
            if nt in downgrade and not confirmed:
                market_state = "观察"

        confidence, missing = self._confidence(td)
        return {
            "trade_date": td,
            "node_type": nt,
            "confirmed": confirmed,
            "sub_state": raw["sub_state"],
            "sentiment_state": sentiment_state,
            "market_state": market_state,
            "node_score": raw["node_score"],
            "confidence": confidence,
            "reason_codes": raw["reason_codes"],
            "invalidation_conditions": self.invalidation_conditions(nt),
            "missing_fields": missing,
        }

    # ------------------------------------------------------------------
    def _recently_ice(self, trade_date, lookback=3):
        idx = list(self.feat.index)
        i = idx.index(trade_date)
        lo = max(0, i - lookback)
        recent = self.feat.iloc[lo:i]
        if recent.empty:
            return False
        return bool((recent["pct_zt_count"] <= self.th["zt_deep_low_pct"]).any())

    def _multi_improve(self, trade_date):
        """多维改善（区分有效修复 vs 下跌中继）：需至少 n2_min_dims 个维度同日改善，且修复到位（广度或涨停分位达标）。"""
        f = self.feat
        i = f.index.get_loc(trade_date)
        if i == 0:
            return False
        cur = f.iloc[i]
        dims = 0
        if pd.notna(cur["dt_count_d1"]) and cur["dt_count_d1"] < 0:
            dims += 1
        if pd.notna(cur["zt_count_d1"]) and cur["zt_count_d1"] > 0:
            dims += 1
        if pd.notna(cur["zt_y_pcnt_d1"]) and cur["zt_y_pcnt_d1"] > 0:
            dims += 1
        if pd.notna(cur["up_ratio_d1"]) and cur["up_ratio_d1"] > 0:
            dims += 1
        reached = (cur["up_ratio"] >= self.th["ice_recover_up_ratio"]) or \
                  (pd.notna(cur["pct_zt_count"]) and cur["pct_zt_count"] >= 50)
        return dims >= self.th["n2_min_dims"] and reached

    def _mainline_confirm(self, trade_date):
        f = self.feat
        cur = f.loc[trade_date]
        th = self.th
        ma20_ok = pd.notna(cur["above_ma20_ratio"]) and cur["above_ma20_ratio"] >= th["above_ma20_strong"]
        trend_ok = pd.notna(cur["above_ma20_ratio_d3"]) and cur["above_ma20_ratio_d3"] > 0
        promo_ok = pd.notna(cur["promo12"]) and cur["promo12"] >= th["promo12_strong"]
        ladder_ok = pd.notna(cur["max_board"]) and cur["max_board"] >= 3
        feed_ok = pd.notna(cur["zt_y_pcnt"]) and cur["zt_y_pcnt"] > 0
        return ma20_ok and trend_ok and promo_ok and ladder_ok and feed_ok

    # ------------------------------------------------------------------
    def _state_from_node(self, node_type, sub_state, trade_date):
        f = self.feat
        cur = f.loc[trade_date]
        sent_d1 = cur["sentiment_d1"] if pd.notna(cur["sentiment_d1"]) else 0.0

        if node_type == "N1":
            sub = "冰点恶化" if sent_d1 < 0 else "冰点稳定"
            market = "防守" if sent_d1 < 0 else "观察"
            return sub, market
        if node_type == "N2":
            return "冰点修复", "进攻"
        if node_type == "N3":
            return "正常升温", "进攻"
        if node_type == "N4":
            return "正常升温", "进攻"
        if node_type == "N5":
            if sub_state == "高位分歧":
                return "高位分歧", "观察"
            if sub_state == "高潮退潮":
                return "高潮退潮", "防守"
            return "高潮加速", "观察"
        if node_type == "N6":
            return "高潮退潮", "防守"
        return "常态", "观察"

    # ------------------------------------------------------------------
    def _confidence(self, trade_date):
        f = self.feat
        cur = f.loc[trade_date]
        req = self.cfg["confidence"]["require_fields"]
        missing = [c for c in req if c not in f.columns or pd.isna(cur.get(c))]
        if len(missing) >= 4:
            return "低", missing
        if len(missing) >= 1:
            return "中", missing
        return "高", missing

    # ------------------------------------------------------------------
    # 失效条件（文本）
    # ------------------------------------------------------------------
    def invalidation_conditions(self, node_type):
        return {
            "N1": "涨停数回升至中位数上方；或上涨家数占比>55%连续2日",
            "N2": "强势股次日反馈再度转负；或上涨家数占比回落至40%下方",
            "N3": "成交额萎缩至20日均值0.75倍以下；或上涨范围回落",
            "N4": "连板晋级率跌破30%且昨日涨停收益转负",
            "N5": "涨停数单日大幅回落；或高位连板股次日大幅低开",
            "N6": "强势股反馈转正、广度回升且跌停家数明显减少",
        }.get(node_type, "")

    # ------------------------------------------------------------------
    # 节点延续 / 切换动态（相对上一交易日，只用当日及更早数据）
    # ------------------------------------------------------------------
    def node_dynamics(self, trade_date, max_back=15):
        """返回当日节点与上一交易日的对比：延续天数、切换关系、强度与状态变化、指标边际。"""
        td = str(trade_date)
        idx = list(self.feat.index)
        if td not in idx:
            return {}

        i = idx.index(td)
        cur = self.detect_date(td)
        prev_date = idx[i - 1] if i > 0 else None
        prev = self.detect_date(prev_date) if prev_date else None
        cn = cur["node_type"]
        pn = prev["node_type"] if prev else None

        # 当前节点连续天数（中间断档即停），同时回溯上一个不同节点
        days = 1 if cn else 0
        broken = False
        last_other = None
        k = i - 1
        while k >= 0 and k >= i - max_back:
            r = self.detect_date(idx[k])
            if cn and r["node_type"] == cn:
                if not broken:
                    days += 1
            elif r["node_type"]:
                last_other = (idx[k], r["node_type"])
                break
            elif cn:
                broken = True
            k -= 1

        # 指标边际（涨停/跌停/最高连板/炸板率/上涨占比/成交额倍数）
        metrics = [
            ("zt_count", "涨停", 0, 1),
            ("dt_count", "跌停", 0, -1),
            ("max_board", "最高连板", 0, 1),
            ("zhaban_rate", "炸板率", 1, -1),
            ("up_ratio", "上涨占比", 1, 1),
            ("amount_ratio", "成交额倍数", 2, 1),
        ]
        improved = worsened = 0
        parts = []
        prev_score = None
        if prev_date:
            a, b = self.feat.loc[prev_date], self.feat.loc[td]
            prev_score = prev["node_score"] if prev else None
            for col, name, nd, direction in metrics:
                v0, v1 = a.get(col), b.get(col)
                if pd.isna(v0) or pd.isna(v1):
                    continue
                dlt = float(v1) - float(v0)
                if abs(dlt) < 1e-9:
                    continue
                if dlt * direction > 0:
                    improved += 1
                else:
                    worsened += 1
                suffix = "%" if col in ("zhaban_rate", "up_ratio") else ""
                parts.append(f"{name} {float(v0):.{nd}f}{suffix}→{float(v1):.{nd}f}{suffix}")

        # 状态迁移
        prev_state = prev["market_state"] if prev else None
        state_txt = cur["market_state"]
        state_level = {"防守": 0, "观察": 1, "进攻": 2}
        if prev_state:
            state_txt = f"{prev_state}→{cur['market_state']}"
            if prev_state != cur["market_state"]:
                if state_level.get(cur["market_state"], 1) > state_level.get(prev_state, 1):
                    state_txt += "（升级）"
                else:
                    state_txt += "（降级）"
            else:
                state_txt += "（持平）"

        # 定性解读
        unconfirmed = bool(cn) and not cur["confirmed"]
        if cn and pn and cn != pn:
            summary = f"节点由 {pn} 切换为 {cn}"
        elif cn and cn == pn:
            summary = f"{cn} 延续第 {days} 日"
        elif cn:
            summary = f"切入 {cn}（上一交易日无节点）"
        elif pn:
            summary = f"节点 {pn} 结束，回到常态"
        else:
            summary = "连续处于常态，无节点信号"
        if unconfirmed:
            summary += "（单日信号，待次日确认）"
        if cn:
            if improved > worsened:
                summary += "，指标边际改善"
            elif worsened > improved:
                summary += "，指标边际转弱"
            else:
                summary += "，指标边际持平"
            if cn == pn and prev_score is not None and abs(cur["node_score"] - prev_score) >= 1:
                direction = "走强" if cur["node_score"] > prev_score else "走弱"
                summary += f"，节点强度{direction}"

        return {
            "prev_date": prev_date,
            "prev_node": pn,
            "prev_state": prev_state,
            "prev_score": prev_score,
            "today_node": cn,
            "days": days,
            "last_other_node": last_other[1] if last_other else None,
            "last_other_date": last_other[0] if last_other else None,
            "improved": improved,
            "worsened": worsened,
            "metrics_text": "、".join(parts),
            "state_text": state_txt,
            "summary": summary,
            # 最近 5 个交易日节点轨迹（便于看节点延续与切换）
            "path": [(idx[k], self.detect_date(idx[k])["node_type"])
                     for k in range(max(0, i - 4), i + 1)],
        }

    # ------------------------------------------------------------------
    # 回填历史节点（去重）
    # ------------------------------------------------------------------
    def backfill(self):
        """回填节点历史：只记录「连续 2 日成立」的确认节点，单日冲高仅作情绪子状态。

        first_detected 回溯到确认日的前一日（同节点首次出现日），
        confirmed_date 为确认日，invalidated_date 为节点结束日。
        """
        records = []
        meta = []
        active_idx = -1
        last_record_idx = {}
        cooldown = self.cfg["dedup"]["min_days_between_same_node"]
        idx = list(self.feat.index)

        for i, td in enumerate(idx):
            r = self.detect_date(td)
            nt = r["node_type"]
            ok = bool(nt) and bool(r.get("confirmed"))

            # 未确认（含单日冲高）：不落节点；已有节点视为结束
            if not ok:
                if active_idx >= 0 and meta[active_idx]["invalidated_date"] is None:
                    meta[active_idx]["invalidated_date"] = td
                active_idx = -1
                continue

            # 同节点延续
            if active_idx >= 0 and nt == records[active_idx]["node_type"]:
                continue

            # 冷却期抑制同类型重复记录
            last_i = last_record_idx.get(nt, -10 ** 9)
            if i - last_i <= cooldown:
                active_idx = -1
                continue

            # 结束上一个激活节点
            if active_idx >= 0 and meta[active_idx]["invalidated_date"] is None:
                meta[active_idx]["invalidated_date"] = td

            first = idx[i - 1] if i > 0 and self._raw(idx[i - 1])["node_type"] == nt else td
            record = {
                "trade_date": td,   # 记录确认日，保证该行的状态/强度与实际执行日一致
                "node_type": nt,
                "market_state": r["market_state"],
                "sentiment_state": r["sentiment_state"],
                "node_score": r["node_score"],
                "confidence": r["confidence"],
                "reason_codes": ";".join(r["reason_codes"]),
                "invalidation_conditions": self.invalidation_conditions(nt),
            }
            records.append(record)
            meta.append({
                "node_type": nt,
                "first_detected": first,
                "confirmed_date": td,
                "invalidated_date": None,
                "trigger_reason": ";".join(r["reason_codes"]),
            })
            last_record_idx[nt] = i
            active_idx = len(records) - 1

        hist = pd.DataFrame(records, columns=[
            "trade_date", "node_type", "market_state", "sentiment_state",
            "node_score", "confidence", "reason_codes", "invalidation_conditions",
        ])
        if meta:
            meta_out = pd.DataFrame(meta)
        else:
            meta_out = pd.DataFrame(columns=[
                "node_type", "first_detected", "confirmed_date", "invalidated_date", "trigger_reason"])
        return hist, meta_out

    # ------------------------------------------------------------------
    def nodes_in_range(self, start=None, end=None):
        """返回指定区间每一日的节点判定（供报告/复核）。"""
        feats = self.feat
        if start is not None:
            feats = feats.loc[feats.index >= str(start)]
        if end is not None:
            feats = feats.loc[feats.index <= str(end)]
        rows = [self.detect_date(td) for td in feats.index]
        return pd.DataFrame(rows)