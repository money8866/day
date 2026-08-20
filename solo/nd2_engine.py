# -*- coding: utf-8 -*-
"""
「猎尾V5」L3 ND2 Engine 次日+2%概率引擎 (15分)

预测:
  P_UP_2       = P(次日最高价 >= 买入价×1.02)
  P_CLOSE_2    = P(次日收盘价 >= 买入价×1.02)
  P_DRAWDOWN_2 = P(次日最低价 <= 买入价×0.98)

第一阶段: 规则引擎 + 历史条件统计(分桶)
第二阶段: 预留ML接口(Logistic/LightGBM/XGBoost)
"""

import os
import sqlite3
from datetime import datetime

from nd2_config import ND2_POTENTIAL, PROBABILITY, SNAPSHOT

# ────────────────────────────────────────────
# 1. 规则引擎: 特征 -> 0~15分
# ────────────────────────────────────────────


def nd2_rule_score(f, pattern):
    """
    ND2 Potential 规则评分 (0~15)
    f: precompute特征; pattern: 形态类型
    """
    raw = 0.0
    d = {}
    w = ND2_POTENTIAL['feature_weights']

    # ① 距20日高点距离 (2.0): 3~8%最优(有空间不过近), <1%透支, >15%太远趋势弱
    dd = f.get('drawdown_20d', 0)
    if dd < 1:
        s = 0.2
    elif dd <= 3:
        s = 0.7
    elif dd <= 8:
        s = 1.0
    elif dd <= 15:
        s = 0.6
    else:
        s = 0.3
    raw += w['dist_20d_high'] * s
    d['dist_20d_high_s'] = round(s, 2)

    # ② 上方套牢盘压力 (2.0): 距前高越近压力越大
    #    用20日高距近似: 已在①中体现, 此处用20日涨幅反向修正(高位套牢盘多)
    g20 = f.get('gain_20d', 0)
    if g20 > 30:
        s = 0.2
    elif g20 > 20:
        s = 0.5
    elif g20 > 10:
        s = 0.8
    else:
        s = 1.0
    raw += w['overhead_pressure'] * s
    d['overhead_s'] = round(s, 2)

    # ③ 近5日涨幅 (1.5): 0~10%最优, >15%透支
    g5 = f.get('gain_5d', 0)
    if g5 < -5:
        s = 0.4   # 深度回调趋势受损
    elif g5 <= 0:
        s = 0.7
    elif g5 <= 10:
        s = 1.0
    elif g5 <= 15:
        s = 0.6
    else:
        s = 0.2
    raw += w['gain_5d'] * s
    d['gain_5d_s'] = round(s, 2)

    # ④ 当日涨幅 (1.0): 1.5~4.5最优(有动能不透支)
    pct = f.get('pct', 0)
    if 1.5 <= pct <= 4.5:
        s = 1.0
    elif 0.5 <= pct < 1.5 or 4.5 < pct <= 6.5:
        s = 0.7
    elif pct > 6.5:
        s = 0.3
    else:
        s = 0.5
    raw += w['today_pct'] * s
    d['today_pct_s'] = round(s, 2)

    # ⑤ 尾盘价格加速度 (2.0): 温和(0.3~1.8%)最优, 暴力(>3%)透支
    tail_ret = 0
    if f.get('tail_base_price', 0) > 0 and f.get('price', 0) > 0:
        tail_ret = (f['price'] - f['tail_base_price']) / f['tail_base_price'] * 100
    if 0.3 <= tail_ret <= 1.8:
        s = 1.0
    elif tail_ret < 0.3:
        s = 0.5
    elif tail_ret <= 3:
        s = 0.6
    else:
        s = 0.2
    raw += w['tail_accel'] * s
    d['tail_accel_s'] = round(s, 2)

    # ⑥ 尾盘量能变化 (1.5): 放量1.2~2.5最优
    ratio = f.get('tail_vs_noon_ratio')
    if ratio is None:
        s = 0.4
    elif 1.2 <= ratio <= 2.5:
        s = 1.0
    elif ratio < 1.2:
        s = 0.5
    elif ratio <= 3.5:
        s = 0.7
    else:
        s = 0.3   # 异常爆量
    raw += w['tail_vol_change'] * s
    d['tail_vol_s'] = round(s, 2)

    # ⑦ 收盘位置 (1.5): 越高越好
    cp = f.get('close_position', 0.5)
    if cp > 0.93:
        s = 1.0
    elif cp > 0.85:
        s = 0.8
    elif cp > 0.75:
        s = 0.6
    else:
        s = 0.3
    raw += w['close_position'] * s
    d['close_pos_s'] = round(s, 2)

    # ⑧ 涨停基因 (1.5): 1次0.6, 2次0.85, >=3次1.0
    zt = f.get('limit_up_20d', 0)
    if zt >= 3:
        s = 1.0
    elif zt == 2:
        s = 0.85
    elif zt == 1:
        s = 0.6
    else:
        s = 0.3
    raw += w['limit_up_gene'] * s
    d['limit_gene_s'] = round(s, 2)

    # ⑨ 回踩完成度 (1.5): PULLBACK_GAP形态时, 回调天数与缩量确认
    if pattern == 'PULLBACK_GAP':
        dd_ok = 5 <= f.get('drawdown_20d', 0) <= 15
        vs = f.get('vol_shrink_ratio_3d')
        if dd_ok and vs is not None and vs <= 0.8:
            s = 1.0
        elif dd_ok:
            s = 0.7
        else:
            s = 0.4
    else:
        s = 0.5  # 非回踩形态中性
    raw += w['pullback_complete'] * s
    d['pullback_s'] = round(s, 2)

    # ⑩ 缺口空间 (1.5): 用近10日平均缺口近似, 简化用距MA5距离(强势股次日惯性)
    ma5 = f.get('ma5', 0)
    if ma5 > 0 and f.get('price', 0) > 0:
        dist_ma5 = (f['price'] - ma5) / ma5 * 100
        if 0 < dist_ma5 <= 3:
            s = 1.0    # 贴5日线强势, 次日惯性概率高
        elif dist_ma5 <= 0:
            s = 0.8
        elif dist_ma5 <= 6:
            s = 0.5
        else:
            s = 0.2
    else:
        s = 0.5
    raw += w['gap_room'] * s
    d['gap_room_s'] = round(s, 2)

    # 映射到 0~15
    score = min(15, round(raw))
    d['raw'] = round(raw, 2)
    d['nd2_score'] = score
    # 分档
    if score >= 13:
        d['grade'] = '极强'
    elif score >= 10:
        d['grade'] = '较强'
    elif score >= 7:
        d['grade'] = '中等'
    elif score >= 4:
        d['grade'] = '一般'
    else:
        d['grade'] = '极差'
    return score, d


# ────────────────────────────────────────────
# 2. 历史条件统计: 分桶 -> P_UP_2 等
# ────────────────────────────────────────────


def _bucketize(value, boundaries):
    """值 -> 分桶索引; boundaries 为升序边界"""
    if value is None:
        return 0
    b = 0
    for i, bound in enumerate(boundaries):
        if value >= bound:
            b = i
    return b


class ND2HistoryStats:
    """
    历史分桶统计: 从 nd2_snapshot.db 读取已回填标签的快照,
    按 (pattern, tail_flow_bin, nd2_bin, strong_gene_bin, theme_bin, market_bin) 分桶统计
    P_UP_2 / P_CLOSE_2 / P_DRAWDOWN_2
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or SNAPSHOT['db_path']
        self._cache = None          # {(bucket_key): (n, p_up, p_close, p_dd)}
        self._loaded_date = None

    def reset(self):
        """清除缓存,下次查询时重新加载(标签回填后调用)"""
        self._cache = None

    def _load(self):
        """加载历史统计(每个进程只加载一次,或按日期刷新)"""
        if self._cache is not None:
            return
        if not os.path.exists(self.db_path):
            self._cache = {}
            return
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            # 读取已回填标签的快照
            rows = conn.execute('''
                SELECT s.pattern, s.tail_flow, s.nd2_score, s.strong_gene, s.theme_alpha,
                       s.market_status, l.next_high_return, l.next_close_return, l.next_low_return
                FROM nd2_snapshot s
                JOIN nd2_label l ON s.signal_date = l.signal_date AND s.ts_code = l.ts_code
                WHERE l.next_high_return IS NOT NULL
            ''').fetchall()
            conn.close()

            buckets_cfg = PROBABILITY['buckets']
            stats = {}
            for (pattern, tf, nd2, sg, ta, mkt, hi_ret, cl_ret, lo_ret) in rows:
                try:
                    hi_ret = float(hi_ret); cl_ret = float(cl_ret); lo_ret = float(lo_ret)
                except (TypeError, ValueError):
                    continue
                key = (
                    pattern or 'OTHER',
                    _bucketize(tf, buckets_cfg['tail_flow_bin']),
                    _bucketize(nd2, buckets_cfg['nd2_bin']),
                    _bucketize(sg, buckets_cfg['strong_gene_bin']),
                    _bucketize(ta, buckets_cfg['theme_bin']),
                    mkt or '正常市场',
                )
                if key not in stats:
                    stats[key] = [0, 0, 0, 0]  # n, up2, close2, dd2
                st = stats[key]
                st[0] += 1
                if hi_ret >= 0.02:
                    st[1] += 1
                if cl_ret >= 0.02:
                    st[2] += 1
                if lo_ret <= -0.02:
                    st[3] += 1
            self._cache = stats
        except Exception:
            self._cache = {}

    def query(self, pattern, tail_flow, nd2_score, strong_gene, theme_alpha, market_status):
        """
        查询历史相似样本统计
        返回: (n_samples, p_up_2, p_close_2, p_dd_2, confidence)
        """
        self._load()
        if not self._cache:
            return 0, None, None, None, 0.0

        buckets_cfg = PROBABILITY['buckets']
        key = (
            pattern or 'OTHER',
            _bucketize(tail_flow, buckets_cfg['tail_flow_bin']),
            _bucketize(nd2_score, buckets_cfg['nd2_bin']),
            _bucketize(strong_gene, buckets_cfg['strong_gene_bin']),
            _bucketize(theme_alpha, buckets_cfg['theme_bin']),
            market_status or '正常市场',
        )

        # 逐级降维查找: 完整key -> 去theme -> 去market -> 去strong_gene -> 只pattern
        key_levels = [
            key,
            (key[0], key[1], key[2], key[3], key[4], None),   # 忽略market
            (key[0], key[1], key[2], None, None, None),        # pattern+tailflow+nd2
            (key[0], key[1], None, None, None, None),          # pattern+tailflow
            (key[0], None, None, None, None, None),            # 只pattern
            (None, None, None, None, None, None),              # 全体
        ]
        for k in key_levels:
            if k in self._cache:
                n, up2, close2, dd2 = self._cache[k]
                if n > 0:
                    p_up = up2 / n
                    p_close = close2 / n
                    p_dd = dd2 / n
                    # 置信度: 样本量函数
                    min_n = PROBABILITY['min_sample_size']
                    good_n = PROBABILITY['good_sample_size']
                    if n >= good_n:
                        conf = 1.0
                    elif n >= min_n:
                        conf = PROBABILITY['confidence_base'] + 0.7 * (n - min_n) / (good_n - min_n)
                    else:
                        conf = PROBABILITY['confidence_base'] * n / min_n
                    return n, p_up, p_close, p_dd, round(conf, 2)
        return 0, None, None, None, 0.0


class ND2Engine:
    """ND2 主引擎: 规则分 + 历史统计概率"""

    def __init__(self, db_path=None):
        self.stats = ND2HistoryStats(db_path)

    def score(self, f, pattern, market_status='正常市场',
              theme_alpha=6, strong_gene=5):
        """
        返回 (nd2_score 0~15, detail)
        detail 包含 p_up_2/p_close_2/p_dd_2/confidence/sample_size
        """
        score, d = nd2_rule_score(f, pattern)

        # 历史统计修正: 若相似桶样本充足, 用统计概率微调规则分 (±2)
        n, p_up, p_close, p_dd, conf = self.stats.query(
            pattern, d.get('raw', score), score, strong_gene, theme_alpha, market_status
        )
        d['sample_size'] = n
        d['probability_confidence'] = conf
        if n > 0 and p_up is not None:
            d['p_up_2'] = round(p_up, 3)
            d['p_close_2'] = round(p_close, 3)
            d['p_dd_2'] = round(p_dd, 3)
            # 概率映射调整: p_up>0.6 加2, >0.5加1, <0.35减2
            if conf >= 0.5:
                if p_up >= 0.60:
                    score = min(15, score + 2)
                elif p_up >= 0.50:
                    score = min(15, score + 1)
                elif p_up < 0.35:
                    score = max(0, score - 2)
                d['hist_adjusted'] = True
        else:
            d['p_up_2'] = PROBABILITY['fallback_p_up_2']
            d['p_close_2'] = PROBABILITY['fallback_p_close_2']
            d['p_dd_2'] = PROBABILITY['fallback_p_dd_2']

        d['nd2_score_final'] = score
        return score, d
