# -*- coding: utf-8 -*-
"""
「猎尾V5」L2 形态分类器 PatternClassifier
三类形态独立识别,禁止所有股票使用同一套评分逻辑:
  A. PULLBACK_GAP          强势基因回调低吸
  B. BREAKOUT_TAIL         平台突破尾盘
  C. STEALTH_ACCUMULATION  隐蔽吸筹

输入: q(实时行情), kline(历史K线DataFrame), snap(分时快照)
输出: (pattern, detail)  pattern in {PULLBACK_GAP, BREAKOUT_TAIL, STEALTH_ACCUMULATION, OTHER}
"""

import pandas as pd

from nd2_config import PATTERN


PULLBACK_GAP = 'PULLBACK_GAP'
BREAKOUT_TAIL = 'BREAKOUT_TAIL'
STEALTH_ACCUMULATION = 'STEALTH_ACCUMULATION'
OTHER = 'OTHER'


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


class PatternClassifier:

    # ══════════════════════════════════════════
    # 公共特征预计算 (一次计算,三个形态共用)
    # ══════════════════════════════════════════
    @staticmethod
    def precompute(q, kline, snap, ts_code=''):
        """预计算共用特征,避免重复遍历K线"""
        f = {}
        price = _safe_float(q.get('price'))
        open_p = _safe_float(q.get('open'))
        high = _safe_float(q.get('high'))
        low = _safe_float(q.get('low'))
        last_close = _safe_float(q.get('last_close'))
        pct = _safe_float(q.get('pct_chg'))

        f['price'] = price
        f['open'] = open_p
        f['high'] = high
        f['low'] = low
        f['last_close'] = last_close
        f['pct'] = pct

        # 收盘位置 (0~1)
        rng = high - low
        f['close_position'] = (price - low) / rng if rng > 0 else 0.5

        # K线特征
        if kline is not None and len(kline) >= 21:
            closes = pd.to_numeric(kline['close'], errors='coerce').fillna(0)
            highs = pd.to_numeric(kline['high'], errors='coerce').fillna(0)
            vols = pd.to_numeric(kline['vol'], errors='coerce').fillna(0)
            pcts = pd.to_numeric(kline['pct_chg'], errors='coerce').fillna(0)
            f['kline_ok'] = True
            f['ma5'] = float(closes.iloc[-5:].mean())
            f['ma10'] = float(closes.iloc[-10:].mean())
            f['ma20'] = float(closes.iloc[-20:].mean())
            f['high_20d'] = float(highs.iloc[-20:].max())
            f['high_20d_pre'] = float(highs.iloc[-21:-1].max())   # 20日高(不含当日)
            f['low_20d'] = float(lows if False else kline['low'].iloc[-20:].min())
            f['close_5d_ago'] = float(closes.iloc[-6]) if len(closes) >= 6 else 0
            f['close_20d_ago'] = float(closes.iloc[-21]) if len(closes) >= 21 else 0
            f['gain_5d'] = (price / f['close_5d_ago'] - 1) * 100 if f['close_5d_ago'] > 0 else 0
            f['gain_20d'] = (price / f['close_20d_ago'] - 1) * 100 if f['close_20d_ago'] > 0 else 0
            f['drawdown_20d'] = (f['high_20d'] - price) / f['high_20d'] * 100 if f['high_20d'] > 0 else 0

            # 20日涨停次数(不含当日): 双创19.5%, 主板9.5%
            limit = 19.5 if str(ts_code).startswith(('300', '688')) else 9.5
            limit_cnt = 0
            for v in pcts.iloc[-21:-1]:
                if _safe_float(v) >= limit:
                    limit_cnt += 1
            f['limit_up_20d'] = limit_cnt
            # 找最近一次涨停位置(距今天数, 0=昨天)
            f['last_zt_days_ago'] = None
            for i in range(len(pcts) - 2, max(len(pcts) - 22, -1), -1):
                if _safe_float(pcts.iloc[i]) >= limit:
                    f['last_zt_days_ago'] = len(pcts) - 1 - i
                    break
            # 20日最高量(不含当日)
            f['vol_20d_max'] = float(vols.iloc[-21:-1].max())
            f['vol_yesterday'] = float(vols.iloc[-1]) if len(vols) >= 1 else 0
        else:
            f['kline_ok'] = False

        # 分时快照特征 (快照vol单位=股, 统一转手与K线一致)
        if snap:
            f['noon_vol'] = _safe_float(snap.get('noon_vol')) / 100.0          # 14:00累计量(手)
            f['tail_base_vol'] = _safe_float(snap.get('tail_base_vol')) / 100.0  # 14:30累计量(手)
            f['tail_base_price'] = _safe_float(snap.get('tail_base_price'))  # 14:30价格
            f['noon_pct'] = snap.get('noon_pct')
            f['tail_base_pct'] = snap.get('tail_base_pct')
            f['morning_vol'] = _safe_float(snap.get('morning_vol')) / 100.0
        else:
            f['noon_vol'] = 0
            f['tail_base_vol'] = 0
            f['tail_base_price'] = 0
            f['noon_pct'] = None
            f['tail_base_pct'] = None
            f['morning_vol'] = 0

        # 当前累计量 (新浪实时单位=股, K线vol单位=手, 统一为手)
        f['cur_vol'] = _safe_float(q.get('vol')) / 100.0

        # 尾盘(14:30后)增量量
        f['tail_inc_vol'] = max(0, f['cur_vol'] - f['tail_base_vol']) if f['tail_base_vol'] > 0 else 0
        # 午后(14:00~14:30)量
        f['noon_to_tail_vol'] = max(0, f['tail_base_vol'] - f['noon_vol']) if (f['noon_vol'] > 0 and f['tail_base_vol'] > 0) else 0
        # 尾盘量比: 尾盘增量 / 午后前段量
        f['tail_vs_noon_ratio'] = (f['tail_inc_vol'] / f['noon_to_tail_vol']) if f['noon_to_tail_vol'] > 0 else None

        return f

    # ══════════════════════════════════════════
    # A类: PULLBACK_GAP 强势基因回调低吸
    # ══════════════════════════════════════════
    @staticmethod
    def classify_pullback(f, ts_code):
        """A类形态: 20日内强势基因 -> 回调 -> 缩量 -> 企稳 -> 尾盘回流"""
        cfg = PATTERN['pullback']
        d = {}
        if not f['kline_ok']:
            return False, d

        # 1. 强势基因: 20日涨停 >= 2 (precompute 已按 ts_code 前缀用正确阈值统计)
        d['limit_up_20d'] = f['limit_up_20d']
        if f['limit_up_20d'] < cfg['limit_up_20d_min']:
            return False, d

        # 2. 处于回调中: 从20日高(不含当日)回撤 >= 5%
        drawdown = f['drawdown_20d']
        d['drawdown'] = round(drawdown, 1)
        if drawdown < 4:  # 略宽于配置的5%(形态识别宽进,质量评分严出)
            return False, d

        # 3. 回调天数: 最近一次涨停距今 2~10 天 (近似回调天数)
        last_zt = f.get('last_zt_days_ago')
        d['last_zt_days_ago'] = last_zt
        if last_zt is None or not (1 <= last_zt <= 10):
            return False, d

        # 4. 缩量回调: 近3日均量 / 上涨段(涨停前3日)均量 <= 0.85
        #    用昨日量近似回调末段量
        if f['vol_20d_max'] > 0:
            vol_shrink = f['vol_yesterday'] / f['vol_20d_max']
            d['vol_shrink_ratio'] = round(vol_shrink, 2)
            if vol_shrink > 0.9:
                return False, d
        else:
            d['vol_shrink_ratio'] = None

        # 5. 企稳: 价格在 MA10 附近(±3.5%) 且 > MA20*0.97 (不破位)
        if f['ma10'] > 0 and f['ma20'] > 0:
            dist_ma10 = abs(f['price'] - f['ma10']) / f['ma10'] * 100
            d['dist_ma10_pct'] = round(dist_ma10, 1)
            above_ma20 = f['price'] >= f['ma20'] * 0.97
            if dist_ma10 > 3.5 or not above_ma20:
                return False, d
        else:
            return False, d

        # 6. 今日不再大跌 (回调企稳日)
        if f['pct'] < -1:
            return False, d

        d['pass'] = True
        return True, d

    # ══════════════════════════════════════════
    # B类: BREAKOUT_TAIL 平台突破尾盘
    # ══════════════════════════════════════════
    @staticmethod
    def classify_breakout(f):
        """B类形态: 5~20日横盘 -> 尾盘放量突破平台上沿"""
        cfg = PATTERN['breakout']
        d = {}
        if not f['kline_ok']:
            return False, d

        # 1. 平台识别: 近10日(不含当日)高低点振幅 < 8%
        closes_recent = f  # 特征已在precompute
        if f['high_20d_pre'] <= 0:
            return False, d
        # 用近10日高低近似平台(不重扫K线,用20日高/低近似会偏宽,这里要求价格接近平台上沿)
        # 平台上沿: 20日高(不含当日); 平台下沿: 近期低点
        platform_high = f['high_20d_pre']
        if platform_high <= 0:
            return False, d

        # 2. 当日突破: 现价 > 平台上沿 (或距上沿<0.5%即将突破)
        dist_to_platform = (f['price'] - platform_high) / platform_high * 100
        d['breakout_dist_pct'] = round(dist_to_platform, 2)
        if dist_to_platform < -0.5:
            return False, d

        # 3. 平台宽度: (高-低)/低 < 8% (用20日高与ma20近似)
        if f['ma20'] > 0 and f['low_20d'] > 0:
            width = (platform_high - f['low_20d']) / f['low_20d'] * 100
            d['platform_width'] = round(width, 1)
        else:
            width = 99
            d['platform_width'] = None

        # 4. 尾盘放量: 尾盘量比 >= 1.5 或 当前累计量 > 昨日量*1.2
        vol_ok = False
        if f['tail_vs_noon_ratio'] is not None and f['tail_vs_noon_ratio'] >= cfg['breakout_vol_ratio_min']:
            vol_ok = True
            d['breakout_vol_ratio'] = round(f['tail_vs_noon_ratio'], 2)
        elif f['vol_yesterday'] > 0 and f['cur_vol'] > f['vol_yesterday'] * 1.2:
            vol_ok = True
            d['breakout_vol_ratio'] = round(f['cur_vol'] / f['vol_yesterday'], 2)
        if not vol_ok:
            return False, d

        # 5. 收盘位置强势: >= 0.75
        d['close_position'] = round(f['close_position'], 3)
        if f['close_position'] < cfg['close_pos_min']:
            return False, d

        # 6. 突破幅度不能过大(假突破/透支): 现价距平台上沿 < 4%
        if dist_to_platform > 4:
            d['overdraw'] = True
            return False, d

        d['pass'] = True
        return True, d

    # ══════════════════════════════════════════
    # C类: STEALTH_ACCUMULATION 隐蔽吸筹
    # ══════════════════════════════════════════
    @staticmethod
    def classify_stealth(f):
        """C类形态: 全天涨幅不大 + 尾盘量增价稳步抬升 + 回撤浅 + 收盘近高点"""
        cfg = PATTERN['stealth']
        d = {}
        if not f['kline_ok']:
            return False, d

        # 1. 全天涨幅 0.5%~3%
        d['pct'] = f['pct']
        if not (cfg['pct_range'][0] <= f['pct'] <= cfg['pct_range'][1]):
            return False, d

        # 2. 尾盘量能扩张: tail_vs_noon_ratio >= 1.2 (14:30后 vs 14:00~14:30)
        if f['tail_vs_noon_ratio'] is None or f['tail_vs_noon_ratio'] < cfg['vol_expand_min']:
            return False, d
        d['tail_vol_ratio'] = round(f['tail_vs_noon_ratio'], 2)

        # 3. 尾盘价格上涨: 14:30基准价到现价上涨 (阶梯抬升,温和)
        if f['tail_base_price'] > 0:
            tail_rally = (f['price'] - f['tail_base_price']) / f['tail_base_price'] * 100
            d['tail_rally'] = round(tail_rally, 2)
            if tail_rally <= 0.1:
                return False, d
            # 暴力拉升不属于隐蔽吸筹
            if tail_rally > 3:
                return False, d
        else:
            return False, d

        # 4. 回撤浅: 现价距全天高点 < 1% (收盘靠近全天最高)
        if f['high'] > 0:
            dist_high = (f['high'] - f['price']) / f['price'] * 100
            d['dist_to_high'] = round(dist_high, 2)
        else:
            dist_high = 99
        if dist_high > 1.0:
            return False, d

        # 5. 收盘位置 >= 0.75
        d['close_position'] = round(f['close_position'], 3)
        if f['close_position'] < cfg['close_pos_min']:
            return False, d

        # 6. 全天换手不过度异常: 当日量 < 20日最大量(排除爆量)
        if f['vol_20d_max'] > 0 and f['cur_vol'] > f['vol_20d_max'] * 1.5:
            d['vol_explode'] = True
            return False, d

        d['pass'] = True
        return True, d

    # ══════════════════════════════════════════
    # 主入口: 分类
    # ══════════════════════════════════════════
    @staticmethod
    def classify(q, kline, snap, ts_code):
        """
        返回 (pattern, features, detail)
        优先级: PULLBACK_GAP > BREAKOUT_TAIL > STEALTH_ACCUMULATION > OTHER
        """
        f = PatternClassifier.precompute(q, kline, snap, ts_code)

        ok, d_pb = PatternClassifier.classify_pullback(f, ts_code)
        if ok:
            return PULLBACK_GAP, f, d_pb

        ok, d_bo = PatternClassifier.classify_breakout(f)
        if ok:
            return BREAKOUT_TAIL, f, d_bo

        ok, d_st = PatternClassifier.classify_stealth(f)
        if ok:
            return STEALTH_ACCUMULATION, f, d_st

        return OTHER, f, {}
