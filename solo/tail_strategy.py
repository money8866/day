# -*- coding: utf-8 -*-
"""
「猎尾」尾盘突袭战法 - 独立策略模块 v2
从 realtime_theme_monitor.py 提炼,用于历史回测

评分模型 (100分 + 加分项 - 扣分项):
- 全天结构   (25分): 阳线实体 + 缩量程度 + 连续性
- 尾盘攻击力 (20分): 尾盘量占比 + 距最高价 + 尾盘拉升
- 位置安全   (15分): 距MA5/MA10 + 距20日高回撤
- 趋势一致性 (10分): MA5>MA10/MA10>MA20/Close>MA20
- 主题共振   (20分): 主题排名 + 生命周期 + 前瞻 + 龙头
- 相对强度   (15分): RS(相对主题) + Alpha(相对指数)
- 新高突破   ( 8分): Close突破10日高/距20日高<2%
- 技术形态   (10分): KDJ金叉 + RSI健康度
- 波动率扣分 (≤10分): ATR过大扣分
- 诱多扣分   (≤30分): 四大诱多红旗

硬过滤: 涨停/跌停/振幅>8%/跌>2.5%/涨幅<1%/不在主题/连板≥2/距MA20>25%
        5日涨>15%/换手>15%或<0.5%/主题退潮/市值<8亿

实盘入表筛选(方案K):
    总分≥88 + 无诱多 + 技术分≥12 + 排北交所 + 每主题TOP2
"""
import math
from theme_engine_v3 import theme_score_v3
from role_engine_v3 import detect_stock_role, calc_stock_role_score_from_layer
from capital_engine_v3 import capital_score_v3


class TailStrategy:
    """尾盘突袭战法评分引擎(纯函数,无外部依赖)"""

    # 信号阈值
    STRONG_BUY_THRESHOLD = 75
    BUY_THRESHOLD = 65
    WATCH_THRESHOLD = 50

    # 实盘入表筛选条件(方案K)
    TRACK_MIN_SCORE = 88
    TRACK_MIN_TECH = 12
    TRACK_TOP_N_PER_THEME = 2

    def __init__(self):
        pass

    # ═══════════════════════════════════════════════════════
    # 硬过滤
    # ═══════════════════════════════════════════════════════
    def hard_filter(self, ts_code, q, kline, turnover, total_mv, theme_strength):
        pct = q.get('pct_chg', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        price = q.get('price', 0)

        # 1. 涨停/跌停排除
        limit_up = 19.5 if ts_code.startswith(('300', '688')) else 9.5
        if pct >= limit_up:
            return False, '涨停'
        if pct <= -9.5:
            return False, '跌停'

        # 2. 振幅>8%排除
        if last_close > 0 and high > 0 and low > 0:
            amplitude = (high - low) / last_close * 100
            if amplitude > 8:
                return False, f'振幅{amplitude:.1f}%'

        # 3. 收盘跌>2.5%排除
        if pct < -2.5:
            return False, f'跌{pct:.1f}%'

        # 3.5 收阴线或微涨<1%排除
        if pct < 1.0:
            return False, f'涨幅{pct:.1f}%过低'

        # 4. 不在任何主题中排除(由调用方保证)

        # 5. 连续涨停≥2天排除
        if kline is not None and len(kline) >= 3:
            prev_pct = float(kline['pct_chg'].iloc[-1]) if 'pct_chg' in kline.columns else 0
            prev2_pct = float(kline['pct_chg'].iloc[-2]) if 'pct_chg' in kline.columns else 0
            prev_limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
            if prev_pct >= prev_limit and prev2_pct >= prev_limit:
                return False, '连板2天'

        # 6. 距MA20太远(>25%)排除
        if kline is not None and len(kline) >= 20:
            ma20 = kline['close'].iloc[-20:].mean()
            if price > 0 and price > ma20 * 1.25:
                return False, '距MA20>25%'

        # 7. 近5日涨幅>15%排除
        if kline is not None and len(kline) >= 6:
            close_5d_ago = float(kline['close'].iloc[-6])
            if close_5d_ago > 0:
                gain_5d = (price - close_5d_ago) / close_5d_ago * 100
                if gain_5d > 15:
                    return False, f'5日涨{gain_5d:.1f}%'

        # 8. 换手率异常
        if turnover > 0:
            if turnover > 15:
                return False, f'换手{turnover:.1f}%过高'
            if turnover < 0.5:
                return False, f'换手{turnover:.1f}%过低'

        # 9. 主题强度<-1排除
        if theme_strength < -1:
            return False, '主题退潮'

        # 10. 总市值<8亿排除
        if total_mv > 0 and total_mv < 80000:
            return False, f'市值{total_mv/10000:.1f}亿'

        return True, 'OK'

    # ═══════════════════════════════════════════════════════
    # 全天结构 (25分): 阳线实体 + 缩量 + 连续性
    # ═══════════════════════════════════════════════════════
    def structure_score(self, q, kline):
        """全天结构质量 (25分)"""
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        pct = q.get('pct_chg', 0)
        vol = q.get('vol', 0)
        price = q.get('price', 0)
        score = 0
        detail = {}

        # 1. 阳线实体 (10分)
        if pct > 2:
            score += 10
            detail['yang_line'] = True
        elif pct > 1:
            score += 7
            detail['yang_line'] = True
        elif pct > 0:
            score += 4
            detail['yang_line'] = True
        else:
            detail['yang_line'] = False

        # 2. 缩量程度 (10分)
        if kline is not None and len(kline) >= 5:
            avg_vol_5d = kline['vol'].iloc[-5:].mean()
            if avg_vol_5d > 0:
                vol_ratio = vol / avg_vol_5d
                detail['vol_ratio_5d'] = round(vol_ratio, 2)
                if vol_ratio < 0.7:
                    score += 10
                elif vol_ratio < 0.85:
                    score += 8
                elif vol_ratio < 1.0:
                    score += 5
                elif vol_ratio < 1.2:
                    score += 3
                else:
                    score += 1
        else:
            detail['vol_ratio_5d'] = 0

        # 3. 连续性: 3日连阳 (5分)
        if kline is not None and len(kline) >= 3:
            pct_today = pct
            pct_t1 = float(kline['pct_chg'].iloc[-1]) if 'pct_chg' in kline.columns else 0
            pct_t2 = float(kline['pct_chg'].iloc[-2]) if 'pct_chg' in kline.columns else 0
            yang_count = sum(1 for p in [pct_t2, pct_t1, pct_today] if p > 0)
            if yang_count == 3:
                score += 5
                detail['continuity'] = '3连阳'
            elif yang_count == 2:
                score += 3
                detail['continuity'] = '2阳1阴'
            else:
                detail['continuity'] = f'{yang_count}阳'
        else:
            detail['continuity'] = '?'

        return min(score, 25), detail

    # ═══════════════════════════════════════════════════════
    # 尾盘攻击力 (20分): 量占比 + 距最高价 + 拉升
    # ═══════════════════════════════════════════════════════
    def attack_score(self, q, snap):
        """
        尾盘攻击力 (20分)
        snap: 分时快照 dict(tail_vol_ratio, tail_base_price, ...)
              回测模式下 snap 可为空
        """
        score = 0
        detail = {}
        current_price = q.get('price', 0)
        high = q.get('high', 0)
        open_p = q.get('open', 0)

        # 1. 尾盘量占全天比例 (10分) — 比量能放大更稳定
        tail_vol_ratio = snap.get('tail_vol_ratio', 0) if snap else 0
        detail['tail_vol_ratio'] = round(tail_vol_ratio, 2)
        if tail_vol_ratio > 0.35:
            score += 10
        elif tail_vol_ratio > 0.30:
            score += 8
        elif tail_vol_ratio > 0.25:
            score += 5
        elif tail_vol_ratio > 0.20:
            score += 2
        # 回测模式下无分时数据,不给分

        # 2. 距今日最高价距离 (8分) — 尾盘重要指标
        if high > 0 and current_price > 0:
            dist_to_high = (high - current_price) / current_price * 100
            detail['dist_to_high'] = round(dist_to_high, 2)
            if dist_to_high < 0.3:
                score += 8
            elif dist_to_high < 0.6:
                score += 6
            elif dist_to_high < 1.0:
                score += 3
        else:
            detail['dist_to_high'] = 0

        # 3. 尾盘拉升幅度 (2分) — 权重降低,回测不可靠
        tail_base_price = snap.get('tail_base_price', 0) if snap else 0
        if tail_base_price > 0 and current_price > 0:
            tail_rally = (current_price - tail_base_price) / tail_base_price * 100
            detail['tail_rally'] = round(tail_rally, 2)
            if tail_rally > 1.0:
                score += 2
            elif tail_rally > 0.5:
                score += 1
        else:
            if open_p > 0 and current_price > 0:
                tail_rally = (current_price - open_p) / open_p * 100
                detail['tail_rally'] = round(tail_rally, 2)
                if tail_rally > 2.0:
                    score += 2
                elif tail_rally > 1.0:
                    score += 1
            else:
                detail['tail_rally'] = 0

        return min(score, 20), detail

    # ═══════════════════════════════════════════════════════
    # 位置安全 (15分)
    # ═══════════════════════════════════════════════════════
    def position_score(self, q, kline):
        """位置安全边际 (15分)"""
        price = q.get('price', 0)
        score = 0
        detail = {}

        if kline is None or len(kline) < 20:
            return 3, detail

        ma5 = kline['close'].iloc[-5:].mean()
        ma10 = kline['close'].iloc[-10:].mean()
        high_20d = kline['high'].iloc[-20:].max()

        # 1. 距MA5 (5分)
        if price > 0 and ma5 > 0:
            ma5_dist = abs(price - ma5) / ma5 * 100
            detail['ma5_dist'] = round(ma5_dist, 1)
            if ma5_dist < 2:
                score += 5
            elif ma5_dist < 4:
                score += 3
            elif ma5_dist < 6:
                score += 1
        else:
            detail['ma5_dist'] = 0

        # 2. 距MA10 (5分)
        if price > 0 and ma10 > 0:
            ma10_ratio = price / ma10
            detail['ma10_ratio'] = round(ma10_ratio, 2)
            if 0.97 <= ma10_ratio <= 1.05:
                score += 5
            elif 0.94 <= ma10_ratio <= 1.08:
                score += 3
            else:
                score += 1
        else:
            detail['ma10_ratio'] = 0

        # 3. 距20日高回撤 (5分)
        if price > 0 and high_20d > 0:
            pullback = (high_20d - price) / high_20d * 100
            detail['pullback'] = round(pullback, 1)
            if pullback > 5:
                score += 5
            elif pullback > 2:
                score += 3
            elif pullback > 0:
                score += 1
        else:
            detail['pullback'] = 0

        return min(score, 15), detail

    # ═══════════════════════════════════════════════════════
    # 趋势一致性 (10分): MA排列替代MACD
    # ═══════════════════════════════════════════════════════
    def trend_consistency_score(self, q, kline):
        """
        趋势一致性 (10分)
        MA5>MA10>MA20 多头排列,比MACD更稳定
        """
        score = 0
        detail = {}
        price = q.get('price', 0)

        if kline is None or len(kline) < 20:
            return 0, detail

        ma5 = kline['close'].iloc[-5:].mean()
        ma10 = kline['close'].iloc[-10:].mean()
        ma20 = kline['close'].iloc[-20:].mean()

        # MA5 > MA10: +3
        if ma5 > ma10:
            score += 3
            detail['ma5_gt_ma10'] = True
        else:
            detail['ma5_gt_ma10'] = False

        # MA10 > MA20: +3
        if ma10 > ma20:
            score += 3
            detail['ma10_gt_ma20'] = True
        else:
            detail['ma10_gt_ma20'] = False

        # Close > MA20: +4
        if price > ma20:
            score += 4
            detail['close_gt_ma20'] = True
        else:
            detail['close_gt_ma20'] = False

        return min(score, 10), detail

    # ═══════════════════════════════════════════════════════
    # 主题共振 (20分): 排名 + 生命周期 + 前瞻 + 龙头 (旧版,保留兼容)
    # ═══════════════════════════════════════════════════════
    def theme_score(self, theme_rank, lifecycle_score, forward_score, layer):
        """
        主题共振 (20分) — 使用自有主题系统数据
        theme_rank: 主题强度排名(1-based)
        lifecycle_score: 生命周期分
        forward_score: T+1前瞻分
        layer: 'leader'/'middle'/'follower'
        """
        score = 0
        detail = {
            'theme_rank': theme_rank,
            'lifecycle': round(lifecycle_score, 1) if lifecycle_score else 0,
            'forward': round(forward_score, 1) if forward_score else 0,
            'layer': layer,
        }

        # 1. 主题排名 (5分) — 顺势
        if theme_rank <= 2:
            score += 5
        elif theme_rank <= 5:
            score += 3
        elif theme_rank <= 10:
            score += 1

        # 2. 生命周期分 (8分) — 比主题强度更精准
        if lifecycle_score and lifecycle_score > 80:
            score += 8
        elif lifecycle_score and lifecycle_score > 60:
            score += 6
        elif lifecycle_score and lifecycle_score > 40:
            score += 4
        elif lifecycle_score and lifecycle_score > 20:
            score += 2

        # 3. 前瞻分 (4分) — T+1预测
        if forward_score and forward_score > 80:
            score += 4
        elif forward_score and forward_score > 50:
            score += 3
        elif forward_score and forward_score > 30:
            score += 1

        # 4. 龙头地位 (3分)
        if layer == 'leader':
            score += 3
        elif layer == 'middle':
            score += 2
        else:
            score += 1

        return min(score, 20), detail

    # ═══════════════════════════════════════════════════════
    # V2 实时主题动量 (20分): 5个轻量实时指标
    # ═══════════════════════════════════════════════════════
    def v2_trade_score(self, up_ratio, avg_return, leader_return, bullish_count, tail_momentum=None):
        """
        V2 实时主题动量 (20分) — 替代旧版 theme_score
        仅使用盘中实时可计算的5个轻量指标,不重算复杂因子

        up_ratio:      上涨家数比例 (0-100)
        avg_return:    主题平均涨幅 (%)
        leader_return: 龙头涨幅 (%)
        bullish_count: 大涨股数量评分 (0-100, >7% +2分/家, >5% +1分/家)
        tail_momentum: 尾盘动量 (%), None=回测模式

        权重: 35% + 25% + 20% + 10% + 10% = 100% → 缩放到20分
        """
        score = 0
        detail = {
            'up_ratio': round(up_ratio, 1),
            'avg_return': round(avg_return, 2),
            'leader_return': round(leader_return, 2),
            'bullish_count': round(bullish_count, 1),
            'tail_momentum': round(tail_momentum, 2) if tail_momentum is not None else None,
        }

        # 1. 上涨家数比例 (7分 = 35% × 20)
        if up_ratio >= 80:
            score += 7
        elif up_ratio >= 60:
            score += 5
        elif up_ratio >= 40:
            score += 3
        elif up_ratio >= 20:
            score += 1

        # 2. 平均涨幅 (5分 = 25% × 20)
        if avg_return > 3:
            score += 5
        elif avg_return > 2:
            score += 4
        elif avg_return > 1:
            score += 3
        elif avg_return > 0:
            score += 1

        # 3. 龙头表现 (4分 = 20% × 20)
        if leader_return > 5:
            score += 4
        elif leader_return > 3:
            score += 3
        elif leader_return > 0:
            score += 2
        # 龙头下跌不加分(0分)

        # 4. 大涨股数量 (2分 = 10% × 20)
        if bullish_count >= 80:
            score += 2
        elif bullish_count >= 50:
            score += 1

        # 5. 尾盘动量 (2分 = 10% × 20)
        if tail_momentum is not None:
            if tail_momentum > 1.0:
                score += 2
            elif tail_momentum > 0.5:
                score += 1
        # 回测模式无尾盘动量,权重自动重新分配

        return min(score, 20), detail

    # ═══════════════════════════════════════════════════════
    # V2 入场信号检测: 实时动量 + 盘后生命周期 → 入场判断
    # ═══════════════════════════════════════════════════════
    def v2_entry_signal(self, v2_momentum, lifecycle_stage, theme_rank, layer):
        """
        V2 入场信号检测
        基于实时主题动量 + 盘后生命周期,判断入场类型

        返回: (entry_type, confidence)
        entry_type: 'breakout'/'pullback'/'pre_rotate'/None
        confidence: 0-100

        breakout:    动量>80 + 生命周期MainUp/Recovery + 排名前3
        pullback:    动量>60 + 生命周期Recovery/Consolidation + 回调充分
        pre_rotate:  动量>70 + 生命周期Recovery + 排名前5 + 龙头
        """
        entry_type = None
        confidence = 0

        # ── Breakout: 强势突破信号 ──
        if v2_momentum > 80 and lifecycle_stage in ('MainUp', 'Recovery') and theme_rank <= 3:
            entry_type = 'breakout'
            confidence = min(95, v2_momentum)
        # ── Pre-Rotate: 轮动预备信号 (比pullback更具体,先判断) ──
        elif v2_momentum > 70 and lifecycle_stage == 'Recovery' and theme_rank <= 5 and layer == 'leader':
            entry_type = 'pre_rotate'
            confidence = min(85, v2_momentum + 5)
        # ── Pullback: 回调低吸信号 ──
        elif v2_momentum > 60 and lifecycle_stage in ('Recovery', 'Consolidation'):
            entry_type = 'pullback'
            confidence = min(80, v2_momentum + 10)

        return entry_type, confidence

    # ═══════════════════════════════════════════════════════
    # 相对强度 (15分): RS + Alpha
    # ═══════════════════════════════════════════════════════
    def relative_strength_score(self, q, theme_avg_pct, index_pct):
        """
        相对强度 (15分)
        RS = 个股涨幅 - 主题均涨幅
        Alpha = 个股涨幅 - 指数涨幅
        """
        pct = q.get('pct_chg', 0)
        score = 0
        detail = {}

        # 1. Relative Strength vs 主题 (8分)
        if theme_avg_pct is not None:
            rs = pct - theme_avg_pct
            detail['rs_vs_theme'] = round(rs, 2)
            if rs > 3:
                score += 8
            elif rs > 2:
                score += 6
            elif rs > 1:
                score += 4
            elif rs > 0:
                score += 2
        else:
            detail['rs_vs_theme'] = 0

        # 2. Alpha vs 指数 (7分)
        if index_pct is not None:
            alpha = pct - index_pct
            detail['alpha_vs_index'] = round(alpha, 2)
            if alpha > 3:
                score += 7
            elif alpha > 2:
                score += 5
            elif alpha > 1:
                score += 3
        else:
            detail['alpha_vs_index'] = 0

        return min(score, 15), detail

    # ═══════════════════════════════════════════════════════
    # 新高突破 (8分)
    # ═══════════════════════════════════════════════════════
    def breakout_score(self, q, kline):
        """
        新高突破 (8分)
        Close突破10日高 = 资金流最好的代理变量
        """
        score = 0
        detail = {}
        price = q.get('price', 0)

        if kline is None or len(kline) < 20:
            return 0, detail

        high_10d = kline['high'].iloc[-10:].max()
        high_20d = kline['high'].iloc[-20:].max()

        # 1. Close突破10日最高 (8分)
        if price > high_10d:
            score += 8
            detail['breakout_10d'] = True
        elif price > 0 and high_20d > 0:
            # 2. Close距20日最高<2% (5分)
            dist_20d = (high_20d - price) / price * 100
            detail['dist_20d_high'] = round(dist_20d, 2)
            if dist_20d < 2:
                score += 5
                detail['near_20d_high'] = True
        else:
            detail['breakout_10d'] = False

        return min(score, 8), detail

    # ═══════════════════════════════════════════════════════
    # 技术形态 (10分): KDJ + RSI
    # ═══════════════════════════════════════════════════════
    def technical_score(self, factor_row, prev_factor_row=None):
        """
        技术形态 (10分): KDJ金叉 + RSI健康度
        MACD和BOLL已移除,由趋势一致性替代
        """
        score = 0
        detail = {}
        if factor_row is None:
            return 0, detail

        # 1. KDJ金叉/位置 (5分)
        try:
            kdj_k = float(factor_row.get('kdj_k', 50) or 50)
            kdj_d = float(factor_row.get('kdj_d', 50) or 50)
            kdj_j = float(factor_row.get('kdj_j', 50) or 50)
            detail['kdj_k'] = round(kdj_k, 1)
            detail['kdj_j'] = round(kdj_j, 1)

            if prev_factor_row is not None:
                prev_k = float(prev_factor_row.get('kdj_k', 50) or 50)
                prev_d = float(prev_factor_row.get('kdj_d', 50) or 50)
                is_golden_cross = (prev_k <= prev_d) and (kdj_k > kdj_d)
                is_dead_cross = (prev_k >= prev_d) and (kdj_k < kdj_d)
            else:
                is_golden_cross = (kdj_k > kdj_d) and (kdj_k < 80)
                is_dead_cross = False

            if is_golden_cross:
                if kdj_k < 20:
                    score += 5; detail['kdj'] = '低位金叉'
                elif kdj_k < 50:
                    score += 4; detail['kdj'] = '金叉'
                elif kdj_k < 80:
                    score += 3; detail['kdj'] = '中位金叉'
                else:
                    score += 1; detail['kdj'] = '高位金叉'
            elif is_dead_cross:
                detail['kdj'] = '死叉'
            elif kdj_j < 80 and kdj_j > 20:
                score += 2; detail['kdj'] = '健康'
        except Exception:
            pass

        # 2. RSI健康度 (5分)
        try:
            rsi_6 = float(factor_row.get('rsi_6', 50) or 50)
            detail['rsi_6'] = round(rsi_6, 1)
            if 40 <= rsi_6 <= 70:
                score += 5
            elif 30 <= rsi_6 < 40:
                score += 3
            elif 70 < rsi_6 <= 80:
                score += 2
        except Exception:
            pass

        return min(score, 10), detail

    # ═══════════════════════════════════════════════════════
    # 波动率扣分 (≤10分): ATR-based
    # ═══════════════════════════════════════════════════════
    def volatility_penalty(self, factor_row, q, kline):
        """
        波动率扣分 (≤10分)
        ATR过大 = 波动过激,不适合尾盘策略
        """
        penalty = 0
        detail = {}

        try:
            if factor_row is not None:
                atr = float(factor_row.get('atr_bfq', 0) or 0)
                close = float(factor_row.get('close', 0) or 0)
                if atr > 0 and close > 0:
                    atr_pct = atr / close * 100
                    detail['atr_pct'] = round(atr_pct, 2)
                    # ATR超过2倍20日均值 = 波动过大
                    if kline is not None and len(kline) >= 20:
                        avg_atr_20d = 0
                        prices = kline['close'].iloc[-20:].values
                        if len(prices) >= 20:
                            # 简化:用20日平均振幅
                            amp_20d = (kline['high'].iloc[-20:].values - kline['low'].iloc[-20:].values) / prices
                            avg_atr_20d = float(amp_20d.mean() * 100)
                            detail['atr_20d_avg'] = round(avg_atr_20d, 2)
                            if avg_atr_20d > 0 and atr_pct > avg_atr_20d * 2:
                                penalty += 10
                                detail['vol_penalty'] = 'ATR过大'
                            elif avg_atr_20d > 0 and atr_pct > avg_atr_20d * 1.5:
                                penalty += 5
                                detail['vol_penalty'] = 'ATR偏高'
        except Exception:
            pass

        return min(penalty, 10), detail

    # ═══════════════════════════════════════════════════════
    # 诱多风险扣分 (≤30分)
    # ═══════════════════════════════════════════════════════
    def trap_penalty(self, q, kline, theme_strength, theme_zt_count, snap=None):
        penalty = 0
        detail = {}
        open_p = q.get('open', 0)
        close = q.get('price', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        pct = q.get('pct_chg', 0)
        tail_base_price = snap.get('tail_base_price', 0) if snap else open_p

        # 红旗1: 全天弱势+尾盘急拉
        if tail_base_price > 0 and close > 0 and open_p > 0:
            tail_rally = (close - tail_base_price) / tail_base_price * 100
            day_change = (close - open_p) / open_p * 100
            if tail_rally > 0.5 and day_change < -0.3:
                penalty += 18
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'
            elif tail_rally > 0.3 and day_change < 0:
                penalty += 10
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'
            elif tail_rally > 0.5 and day_change < 1:
                penalty += 5
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'

        # 红旗2: 长下影线+尾盘拉回
        if last_close > 0 and high > 0 and low > 0 and close > 0 and open_p > 0:
            body = abs(close - open_p)
            lower_shadow = min(open_p, close) - low
            upper_shadow = high - max(open_p, close)
            price_range = high - low
            if price_range > 0:
                lower_ratio = lower_shadow / price_range
                body_ratio = body / price_range
                if lower_ratio > 0.4 and body_ratio < 0.3:
                    penalty += 12
                    detail['trap_long_lower'] = f'下影{lower_ratio:.0%}实体{body_ratio:.0%}'
                elif upper_shadow > body * 2 and close < (high + low) / 2:
                    penalty += 6
                    detail['trap_upper_shadow'] = True

        # 红旗3: 高位滞涨+尾盘偷袭
        if kline is not None and len(kline) >= 6:
            close_5d_ago = float(kline['close'].iloc[-6])
            if close_5d_ago > 0 and close > 0:
                gain_5d = (close - close_5d_ago) / close_5d_ago * 100
                if gain_5d > 8 and pct < 1 and tail_base_price > 0:
                    tail_rally = (close - tail_base_price) / tail_base_price * 100 if tail_base_price > 0 else 0
                    if tail_rally > 0.3:
                        penalty += 10
                        detail['trap_high_stall'] = f'5日{gain_5d:.1f}%今{pct:+.1f}%'

        # 红旗4: 孤立拉升无配合
        if theme_strength < 1 and theme_zt_count == 0:
            penalty += 5
            detail['trap_isolated'] = f'强度{theme_strength:.1f}涨停0'

        return min(penalty, 30), detail

    # ═══════════════════════════════════════════════════════
    # 完整评分 (V2)
    # ═══════════════════════════════════════════════════════
    def score(self, ts_code, q, kline, factor_row, turnover, total_mv,
              theme_name, theme_strength, layer, theme_zt_count,
              snap=None, prev_factor_row=None,
              theme_avg_pct=None, index_pct=None,
              theme_rank=99, lifecycle_score=0, forward_score=0,
              up_ratio=0, avg_return=0, leader_return=0, bullish_count=0, tail_momentum=None):
        """
        完整评分流程 (V2: 实时主题动量替代旧版theme_score)
        返回: signal_dict 或 None

        V2新增参数(实时主题动量):
        - up_ratio: 上涨家数比例 (0-100)
        - avg_return: 主题平均涨幅 (%)
        - leader_return: 龙头涨幅 (%)
        - bullish_count: 大涨股数量评分 (0-100)
        - tail_momentum: 尾盘动量 (%), None=回测模式

        旧版参数(保留兼容):
        - theme_avg_pct: 主题平均涨幅 (Relative Strength)
        - index_pct: 指数涨幅 (Alpha)
        - theme_rank: 主题排名 (Theme Rank)
        - lifecycle_score: 生命周期分
        - forward_score: 前瞻分 (T+1预测)
        """
        # 硬过滤
        passed, reason = self.hard_filter(
            ts_code, q, kline, turnover, total_mv, theme_strength
        )
        if not passed:
            return None

        # 多维评分
        str_s, str_d = self.structure_score(q, kline)
        atk_s, atk_d = self.attack_score(q, snap)
        pos_s, pos_d = self.position_score(q, kline)
        trd_s, trd_d = self.trend_consistency_score(q, kline)
        # V2: 实时主题动量替代旧版 theme_score
        thm_s, thm_d = self.v2_trade_score(up_ratio, avg_return, leader_return, bullish_count, tail_momentum)
        rel_s, rel_d = self.relative_strength_score(q, theme_avg_pct, index_pct)
        brk_s, brk_d = self.breakout_score(q, kline)
        tech_s, tech_d = self.technical_score(factor_row, prev_factor_row)

        # 扣分
        vol_p, vol_d = self.volatility_penalty(factor_row, q, kline)
        trap_p, trap_d = self.trap_penalty(q, kline, theme_strength, theme_zt_count, snap)

        total = str_s + atk_s + pos_s + trd_s + thm_s + rel_s + brk_s + tech_s - vol_p - trap_p

        if total < self.WATCH_THRESHOLD:
            return None

        if total >= self.STRONG_BUY_THRESHOLD:
            signal = '强买入'
        elif total >= self.BUY_THRESHOLD:
            signal = '买入'
        else:
            signal = '关注'

        # V2 入场信号
        v2_entry, v2_conf = self.v2_entry_signal(thm_s, 'Recovery', theme_rank, layer)

        return {
            'ts_code': ts_code,
            'theme': theme_name,
            'total_score': total,
            'structure_score': str_s,
            'attack_score': atk_s,
            'position_score': pos_s,
            'trend_score': trd_s,
            'theme_score': thm_s,
            'rel_strength_score': rel_s,
            'breakout_score': brk_s,
            'tech_score': tech_s,
            'vol_penalty': vol_p,
            'trap_penalty': trap_p,
            'signal': signal,
            'pct_chg': q.get('pct_chg', 0),
            'price': q.get('price', 0),
            'v2_entry': v2_entry,
            'v2_confidence': v2_conf,
            'detail': {**str_d, **atk_d, **pos_d, **trd_d, **thm_d, **rel_d, **brk_d, **tech_d, **vol_d, **trap_d},
        }

    # ═══════════════════════════════════════════════════════
    # 实盘入表筛选(方案K)
    # ═══════════════════════════════════════════════════════
    def filter_for_tracking(self, signals):
        """
        实盘入表筛选(方案K):
        1. 总分 >= 88
        2. 无诱多风险 (trap_penalty == 0)
        3. 技术分 >= 12
        4. 排除北交所 (9xxx/4xxx)
        5. 每主题最多TOP2
        """
        candidates = []
        for s in signals:
            if s.get('signal') not in ('强买入', '买入'):
                continue
            if s.get('total_score', 0) < self.TRACK_MIN_SCORE:
                continue
            if s.get('trap_penalty', 0) != 0:
                continue
            if s.get('tech_score', 0) < self.TRACK_MIN_TECH:
                continue
            code = s.get('ts_code', '')
            if code.startswith(('9', '4')):
                continue
            candidates.append(s)

        theme_groups = {}
        for s in candidates:
            theme = s.get('theme', '其他')
            theme_groups.setdefault(theme, []).append(s)

        final = []
        for theme, stocks in theme_groups.items():
            stocks_sorted = sorted(stocks, key=lambda x: -x.get('total_score', 0))
            final.extend(stocks_sorted[:self.TRACK_TOP_N_PER_THEME])

        return final

    # ═══════════════════════════════════════════════════════
    # V3 信号阈值与交易类型
    # ═══════════════════════════════════════════════════════
    V3_STRONG_BUY = 85       # 强买入
    V3_BUY_OBSERVE = 75      # 买入观察
    V3_WATCH = 65            # 关注
    V3_MIN_SCORE = 65        # 最低入池分

    # ═══════════════════════════════════════════════════════
    # V3 硬过滤 (优化: 允许高位龙头进入)
    # ═══════════════════════════════════════════════════════
    def hard_filter_v3(self, ts_code, q, kline, turnover, total_mv, theme_strength,
                       is_theme_leader=False, role_detail=None):
        """
        V3硬过滤, 与V2相比的优化:
        - 近5日涨幅>15%的龙头允许进入(如果距5日高<5%)
        """
        pct = q.get('pct_chg', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        price = q.get('price', 0)

        # 1. 涨停/跌停排除
        limit_up = 19.5 if ts_code.startswith(('300', '688')) else 9.5
        if pct >= limit_up:
            return False, '涨停'
        if pct <= -9.5:
            return False, '跌停'

        # 2. 振幅>8%排除
        if last_close > 0 and high > 0 and low > 0:
            amplitude = (high - low) / last_close * 100
            if amplitude > 8:
                return False, f'振幅{amplitude:.1f}%'

        # 3. 收盘跌>2.5%排除
        if pct < -2.5:
            return False, f'跌{pct:.1f}%'

        # 3.5 收阴线或微涨<0.5%排除 (强势回调低吸形态允许低开高走收盘微涨)
        if pct < 0.5:
            return False, f'涨幅{pct:.1f}%过低'

        # 4. 连续涨停≥2天排除
        if kline is not None and len(kline) >= 3:
            prev_pct = float(kline['pct_chg'].iloc[-1]) if 'pct_chg' in kline.columns else 0
            prev2_pct = float(kline['pct_chg'].iloc[-2]) if 'pct_chg' in kline.columns else 0
            prev_limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
            if prev_pct >= prev_limit and prev2_pct >= prev_limit:
                return False, '连板2天'

        # 5. 距MA20太远(>25%)排除
        if kline is not None and len(kline) >= 20:
            ma20 = kline['close'].iloc[-20:].mean()
            if price > 0 and price > ma20 * 1.25:
                return False, '距MA20>25%'

        # 6. 近5日涨幅>15% — V3优化: 龙头且距5日高<5%允许进入
        if kline is not None and len(kline) >= 6:
            close_5d_ago = float(kline['close'].iloc[-6])
            if close_5d_ago > 0:
                gain_5d = (price - close_5d_ago) / close_5d_ago * 100
                if gain_5d > 15:
                    # 龙头特权: 距5日高<5%允许进入
                    if is_theme_leader:
                        high_5d = kline['high'].iloc[-5:].max()
                        if high_5d > 0:
                            dist_5d_high = (high_5d - price) / price * 100
                            if dist_5d_high < 5:
                                pass  # 龙头豁免
                            else:
                                return False, f'5日涨{gain_5d:.1f}%距高{dist_5d_high:.1f}%'
                        else:
                            pass  # 龙头豁免
                    else:
                        return False, f'5日涨{gain_5d:.1f}%'

        # 7. 换手率异常
        if turnover > 0:
            if turnover > 15:
                return False, f'换手{turnover:.1f}%过高'
            if turnover < 0.5:
                return False, f'换手{turnover:.1f}%过低'

        # 8. 主题强度<-1排除
        if theme_strength < -1:
            return False, '主题退潮'

        # 9. 总市值<8亿排除
        if total_mv > 0 and total_mv < 80000:
            return False, f'市值{total_mv/10000:.1f}亿'

        # 10. 强势基因回调低吸形态 (V3核心): 20天涨停多+回调阴线+低开承接
        passed_gap, gap_reason, _ = self.pullback_gap_signal_v3(q, kline, ts_code)
        if not passed_gap:
            return False, gap_reason

        return True, 'OK'

    # ═══════════════════════════════════════════════════════
    # V3 强势基因回调低吸形态 (核心)
    # 识别: 20天内涨停多 → 回调阴线 → 第一个低开承接 → 尾盘确认
    # ═══════════════════════════════════════════════════════
    def count_limit_up_20d(self, kline, ts_code):
        """
        统计近20个交易日涨停次数(不含当日)
        涨停阈值: 双创19.5%, 主板9.5%
        """
        if kline is None or len(kline) < 3:
            return 0
        kl = kline.iloc[-21:-1] if len(kline) >= 21 else kline.iloc[:-1]
        limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
        cnt = 0
        for _, row in kl.iterrows():
            pct = float(row.get('pct_chg', 0))
            if pct >= limit:
                cnt += 1
        return cnt

    def pullback_gap_signal_v3(self, q, kline, ts_code):
        """
        强势基因回调低吸形态硬过滤:
        1. 20天涨停>=2次 (强势基因)
        2. 回调阴线: 昨日收阴线(回调中) + 从20日高回撤>=5%
        3. 第一个低开: 今日低开(open<pre_close), 且昨日未低开
        4. 阴线后第一根阳线: 低开高走(price>open)收阳
        5. 低开承接: 低开幅度0.3%~6%
        返回 (passed, reason, detail)
        """
        detail = {}
        price = q.get('price', 0)
        open_p = q.get('open', 0)
        last_close = q.get('last_close', 0)
        if kline is None or len(kline) < 6 or price <= 0:
            return False, 'K线数据不足', detail

        # 1. 20天涨停>=2 (强势基因)
        limit_up_20d = self.count_limit_up_20d(kline, ts_code)
        detail['limit_up_20d'] = limit_up_20d
        if limit_up_20d < 2:
            return False, f'20日涨停{limit_up_20d}次<2', detail

        # 2a. 近5日有回调阴线 (不含当日)
        recent = kline.iloc[-6:-1] if len(kline) >= 6 else kline.iloc[:-1]
        has_yin = any(float(r.get('pct_chg', 0)) < 0 for _, r in recent.iterrows())
        if not has_yin:
            return False, '近5日无回调阴线', detail

        # 2c. 阴线后第一根阳线: 昨日收阴线(回调中), 今日才出现第一根阳线
        if len(kline) >= 2:
            yesterday_pct = float(kline.iloc[-2].get('pct_chg', 0))
            detail['yesterday_pct'] = round(yesterday_pct, 2)
            if yesterday_pct >= 0:
                return False, '昨日非阴线(非阴线后第一根阳线)', detail

        # 2b. 从20日高回撤>=5%
        if len(kline) >= 20:
            high_20d = kline['high'].iloc[-20:].max()
            if high_20d > 0:
                drawdown = (high_20d - price) / high_20d * 100
                detail['drawdown_from_20d_high'] = round(drawdown, 1)
                if drawdown < 5:
                    return False, f'距20日高仅{drawdown:.1f}%回调不足', detail

        # 3. 第一个低开: 今日低开 + 昨日未低开(回调阴线后首次低开承接)
        if open_p <= 0 or last_close <= 0:
            return False, '无开盘数据', detail
        gap_pct = (open_p - last_close) / last_close * 100
        detail['gap_pct'] = round(gap_pct, 2)
        if open_p >= last_close:
            return False, '今日非低开', detail
        if gap_pct < -6:
            return False, f'低开{gap_pct:.1f}%过大', detail
        # 昨日(不含今日)是否有低开
        idx = -2
        if len(kline) >= 3:
            r_open = float(kline.iloc[idx].get('open', 0))
            r_pre = float(kline.iloc[idx - 1].get('close', 0))
            if r_open > 0 and r_pre > 0 and r_open < r_pre:
                return False, f'昨日已有低开(非第一个)', detail

        # 4. 低开承接: 低开高走
        if price <= open_p:
            return False, '低开后未翻红承接弱', detail

        detail['gap_pullback_pass'] = True
        return True, 'OK', detail

    def pullback_gap_score_v3(self, q, kline, ts_code):
        """
        强势基因回调低吸形态加分 (最高+15):
        涨停基因+6 + 回调质量+4 + 低开承接+5
        """
        score = 0
        detail = {}
        price = q.get('price', 0)
        open_p = q.get('open', 0)
        last_close = q.get('last_close', 0)

        # 1. 涨停基因 (6分): 20天涨停次数
        limit_up_20d = self.count_limit_up_20d(kline, ts_code)
        if limit_up_20d >= 4:
            gene_score = 6
        elif limit_up_20d >= 3:
            gene_score = 4
        elif limit_up_20d >= 2:
            gene_score = 2
        else:
            gene_score = 0
        detail['limit_up_20d'] = limit_up_20d
        detail['gene_score'] = gene_score
        score += gene_score

        # 2. 回调质量 (4分): 距20日高回撤深度
        pb_score = 0
        if kline is not None and len(kline) >= 20 and price > 0:
            high_20d = kline['high'].iloc[-20:].max()
            if high_20d > 0:
                drawdown = (high_20d - price) / high_20d * 100
                detail['drawdown_from_20d_high'] = round(drawdown, 1)
                if 10 <= drawdown <= 20:
                    pb_score = 4
                elif 5 <= drawdown < 10:
                    pb_score = 3
                elif 3 <= drawdown < 5:
                    pb_score = 1
        detail['pullback_quality_score'] = pb_score
        score += pb_score

        # 3. 低开承接 (5分): 低开幅度+高走强度
        gap_score = 0
        if open_p > 0 and last_close > 0 and price > 0:
            gap_pct = (open_p - last_close) / last_close * 100
            detail['gap_pct'] = round(gap_pct, 2)
            day_gain = (price - open_p) / open_p * 100  # 低开后的盘中涨幅
            detail['intraday_gain'] = round(day_gain, 2)
            if open_p < last_close and price > open_p:
                if -5 <= gap_pct <= -2 and day_gain >= 1.5:
                    gap_score = 5
                elif -2 < gap_pct <= -0.3 and day_gain >= 1.0:
                    gap_score = 4
                elif -5 <= gap_pct <= -0.3 and day_gain >= 0:
                    gap_score = 3
                else:
                    gap_score = 2
            elif price > last_close:
                gap_score = 1
        detail['gap_absorb_score'] = gap_score
        score += gap_score

        detail['gap_pullback_total'] = min(score, 15)
        return min(score, 15), detail

    # ═══════════════════════════════════════════════════════
    # V3 技术结构评分 (15分): 压缩后的趋势+回踩+突破
    # ═══════════════════════════════════════════════════════
    def technical_structure_v3(self, q, kline):
        """
        V3 技术结构 (15分): 趋势 + 回踩质量 + 突破结构
        将V2的趋势一致性(10)、新高突破(8)、位置安全(15)部分维度
        压缩整合为15分
        """
        score = 0
        detail = {}
        price = q.get('price', 0)

        if kline is None or len(kline) < 20:
            return 3, detail

        ma5 = kline['close'].iloc[-5:].mean()
        ma10 = kline['close'].iloc[-10:].mean()
        ma20 = kline['close'].iloc[-20:].mean()
        high_10d = kline['high'].iloc[-10:].max()
        high_20d = kline['high'].iloc[-20:].max()

        # ── 1. 趋势 (5分) ──
        trend_score = 0
        if ma5 > ma10 > ma20:
            trend_score = 5
        elif ma10 > ma20:
            trend_score = 3
        detail['trend_v3'] = trend_score
        detail['ma5'] = round(ma5, 2) if hasattr(ma5, '__float__') else 0
        detail['ma20'] = round(ma20, 2) if hasattr(ma20, '__float__') else 0
        score += trend_score

        # ── 2. 回踩质量 (5分) ──
        pullback_score = 0
        if price > 0 and ma20 > 0:
            dist_ma20 = (price - ma20) / ma20 * 100
            detail['dist_ma20_pct'] = round(dist_ma20, 1)
            if 2 <= dist_ma20 <= 5:
                pullback_score = 5
            elif 5 < dist_ma20 <= 8:
                pullback_score = 3
            elif 0 <= dist_ma20 < 2:
                pullback_score = 2
        detail['pullback_v3'] = pullback_score
        score += pullback_score

        # ── 3. 突破结构 (5分) ──
        breakout_score = 0
        if price > high_10d:
            breakout_score = 5
            detail['breakout_10d_v3'] = True
        elif price > 0 and high_20d > 0:
            dist_20d = (high_20d - price) / price * 100
            detail['dist_20d_high_v3'] = round(dist_20d, 2)
            if dist_20d < 3:
                breakout_score = 3
                detail['near_20d_high_v3'] = True
        detail['breakout_v3'] = breakout_score
        score += breakout_score

        return min(score, 15), detail

    # ═══════════════════════════════════════════════════════
    # V3 尾盘交易模型 (10分): 保留核心尾盘攻击力
    # ═══════════════════════════════════════════════════════
    def tail_timing_v3(self, q, snap):
        """
        V3 尾盘交易 (10分): 保留尾盘量占比 + 接近最高价 + 尾盘拉升
        从V2的20分压缩到10分
        """
        score = 0
        detail = {}
        current_price = q.get('price', 0)
        high = q.get('high', 0)
        open_p = q.get('open', 0)

        # 1. 尾盘量占全天比例 (5分)
        tail_vol_ratio = snap.get('tail_vol_ratio', 0) if snap else 0
        if tail_vol_ratio <= 0 and snap:
            # 兜底: 用14:30后量能增量/早盘量估算尾盘量占比
            tbv = snap.get('tail_base_vol', 0)
            mv = snap.get('morning_vol', 0)
            cv = q.get('vol', 0)
            if tbv > 0 and mv > 0 and cv > tbv:
                tail_vol_ratio = (cv - tbv) / mv
            elif mv > 0 and cv > 0:
                tail_vol_ratio = cv / mv
        detail['tail_vol_ratio'] = round(tail_vol_ratio, 2)
        if tail_vol_ratio > 0.35:
            score += 5
        elif tail_vol_ratio > 0.30:
            score += 4
        elif tail_vol_ratio > 0.25:
            score += 3
        elif tail_vol_ratio > 0.20:
            score += 1

        # 2. 收盘接近最高 (3分)
        if high > 0 and current_price > 0:
            dist_to_high = (high - current_price) / current_price * 100
            detail['dist_to_high'] = round(dist_to_high, 2)
            if dist_to_high < 0.3:
                score += 3
            elif dist_to_high < 0.6:
                score += 2
            elif dist_to_high < 1.0:
                score += 1
        else:
            detail['dist_to_high'] = 0

        # 3. 尾盘主动拉升 (2分)
        tail_base_price = snap.get('tail_base_price', 0) if snap else 0
        if tail_base_price > 0 and current_price > 0:
            tail_rally = (current_price - tail_base_price) / tail_base_price * 100
            detail['tail_rally'] = round(tail_rally, 2)
            if tail_rally > 1.0:
                score += 2
            elif tail_rally > 0.5:
                score += 1
        else:
            if open_p > 0 and current_price > 0:
                tail_rally = (current_price - open_p) / open_p * 100
                detail['tail_rally'] = round(tail_rally, 2)
                if tail_rally > 2.0:
                    score += 2
                elif tail_rally > 1.0:
                    score += 1
            else:
                detail['tail_rally'] = 0

        return min(score, 10), detail

    # ═══════════════════════════════════════════════════════
    # V3 次日收益空间 (加分项, 最高+5)
    # ═══════════════════════════════════════════════════════
    def tomorrow_room_score(self, q, kline):
        """
        次日收益空间: 距前高/压力位/筹码密集区的上涨空间
        避免买入已经涨满的股票
        """
        price = q.get('price', 0)
        detail = {}
        if kline is None or len(kline) < 20 or price <= 0:
            return 0, detail

        high_20d = kline['high'].iloc[-20:].max()
        high_60d = kline['high'].iloc[-60:].max() if len(kline) >= 60 else high_20d

        # 最近压力位: 取20日高和60日高中的较近者
        if high_20d > price:
            room_20d = (high_20d - price) / price * 100
        else:
            room_20d = 999  # 已突破,无压力

        if high_60d > price:
            room_60d = (high_60d - price) / price * 100
        else:
            room_60d = 999

        room = min(room_20d, room_60d)
        detail['pressure_room_pct'] = round(room, 1)
        detail['high_20d'] = round(high_20d, 2)
        detail['high_60d'] = round(high_60d, 2)

        if room > 10:
            return 5, detail
        elif room > 5:
            return 3, detail
        elif room > 3:
            return 0, detail
        else:
            return -5, detail

    # ═══════════════════════════════════════════════════════
    # V3 风险控制 (最高-20分)
    # ═══════════════════════════════════════════════════════
    def risk_penalty_v3(self, q, kline, turnover, theme_up_ratio, theme_limit_count,
                        is_theme_leader=False, snap=None):
        """
        V3 风险扣分: 高位风险 + 高换手 + 孤立上涨 + 尾盘诱多
        最高扣20分
        """
        penalty = 0
        detail = {}
        pct = q.get('pct_chg', 0)
        price = q.get('price', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        open_p = q.get('open', 0)
        close = price

        # ── 1. 高位风险 (最高-8) ──
        if kline is not None and len(kline) >= 6:
            close_5d_ago = float(kline['close'].iloc[-6])
            if close_5d_ago > 0:
                gain_5d = (price - close_5d_ago) / close_5d_ago * 100
                if gain_5d > 15 and high > 0:
                    dist_high = (high - price) / price * 100
                    if dist_high < 3:
                        # 龙头豁免高位风险
                        if not is_theme_leader:
                            penalty += 8
                            detail['risk_high'] = f'5日涨{gain_5d:.1f}%距高{dist_high:.1f}%'

        # ── 2. 高换手风险 (最高-5) ──
        if turnover > 20:
            penalty += 5
            detail['risk_high_turnover'] = f'换手{turnover:.1f}%'

        # ── 3. 孤立上涨 (最高-5) ──
        if theme_limit_count == 0 and theme_up_ratio < 40:
            penalty += 5
            detail['risk_isolated'] = f'涨停0 上涨比{theme_up_ratio:.0f}%'

        # ── 4. 尾盘诱多 (最高-10) — 保留V2逻辑
        tail_base_price = snap.get('tail_base_price', 0) if snap else open_p
        if tail_base_price > 0 and close > 0 and open_p > 0:
            tail_rally = (close - tail_base_price) / tail_base_price * 100
            day_change = (close - open_p) / open_p * 100
            if tail_rally > 0.5 and day_change < -0.3:
                penalty += 10
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'
            elif tail_rally > 0.3 and day_change < 0:
                penalty += 5
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'
            elif tail_rally > 0.5 and day_change < 1:
                penalty += 3
                detail['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_rally:+.1f}%'

        # 长下影线诱多
        if open_p > 0 and high > 0 and low > 0 and close > 0 and open_p > 0:
            body = abs(close - open_p)
            lower_shadow = min(open_p, close) - low
            price_range = high - low
            if price_range > 0:
                lower_ratio = lower_shadow / price_range
                body_ratio = body / price_range
                if lower_ratio > 0.4 and body_ratio < 0.3:
                    if penalty == 0 or 'trap_weak_day' not in detail:
                        penalty += 5
                        detail['trap_long_lower'] = f'下影{lower_ratio:.0%}'

        return min(penalty, 20), detail

    # ═══════════════════════════════════════════════════════
    # V3 买入类型分类
    # ═══════════════════════════════════════════════════════
    def classify_buy_type_v3(self, role, theme_score, theme_up_ratio, theme_limit_count,
                             technical_score, pullback_quality):
        """
        V3 交易标签分类:
        1. LEADER_BREAKOUT: 龙头 + 主题强 + 突破
        2. CORE_PULLBACK:   中军 + 主线 + 回踩
        3. ROTATION_ENTRY:  主题升温 + 个股资金增强
        """
        # LEADER_BREAKOUT
        if role == 'leader' and theme_score >= 20 and technical_score >= 10:
            return 'LEADER_BREAKOUT', 90

        # CORE_PULLBACK
        if role in ('leader', 'core') and theme_score >= 15 and pullback_quality >= 3:
            return 'CORE_PULLBACK', 80

        # ROTATION_ENTRY
        if theme_up_ratio >= 50 and theme_limit_count >= 1:
            return 'ROTATION_ENTRY', 70

        return 'TAIL_TIMING', 60

    # ═══════════════════════════════════════════════════════
    # V3 完整评分
    # ═══════════════════════════════════════════════════════
    def score_v3(self, ts_code, q, kline, factor_row, turnover, total_mv,
                 theme_name, theme_strength, layer, theme_limit_count,
                 theme_up_ratio, theme_avg_pct, leader_change, theme_amount_ratio=None,
                 snap=None, prev_factor_row=None, index_pct=None,
                 role_override=None, theme_stocks=None, quotes=None):
        """
        V3 完整评分流程:
        Final = Theme(30) + Capital(25) + Role(20) + Technical(15) + Timing(10)
                - Risk(20) + TomorrowRoom(±5)

        返回: signal_dict 或 None
        """
        # ── 股票角色识别 ──
        if role_override:
            role = role_override
            role_score = 20 if role == 'leader' else (12 if role == 'core' else 0)
        else:
            # 基于layer+实时数据判断角色
            role = 'follower'
            role_score = 0
            if layer == 'leader':
                role = 'leader'
                role_score = 20
            elif layer == 'middle':
                role = 'core'
                role_score = 12
            else:
                # 检测是否有升级潜力
                if theme_stocks and quotes and ts_code in quotes:
                    role, role_score, _ = calc_stock_role_score_from_layer(
                        layer, ts_code, theme_name, theme_stocks, quotes,
                        kline_cache={ts_code: kline} if kline is not None else None
                    )

        is_theme_leader = (role == 'leader')

        # ── 硬过滤(V3) ──
        passed, reason = self.hard_filter_v3(
            ts_code, q, kline, turnover, total_mv, theme_strength,
            is_theme_leader=is_theme_leader
        )
        if not passed:
            return None

        # ── 1. 主题资金层 (30分) ──
        thm_s, thm_d = theme_score_v3(
            theme_limit_count=theme_limit_count,
            theme_up_ratio=theme_up_ratio,
            leader_change=leader_change,
            theme_amount_ratio=theme_amount_ratio,
            theme_avg_change=theme_avg_pct,
            theme_strength=theme_strength,
        )

        # ── 2. 个股资金行为 (25分) ──
        cap_s, cap_d = capital_score_v3(ts_code, q, kline, turnover, snap=snap)

        # ── 3. 股票角色 (20分) ──
        role_d = {'role': role, 'role_score': role_score, 'layer': layer}

        # ── 4. 技术结构 (15分) ──
        tech_s, tech_d = self.technical_structure_v3(q, kline)

        # ── 5. 尾盘交易 (10分) ──
        tail_s, tail_d = self.tail_timing_v3(q, snap)

        # ── 6. 强势基因回调低吸形态加分 (最高+15) ──
        gap_s, gap_d = self.pullback_gap_score_v3(q, kline, ts_code)

        # ── 加分: 次日收益空间 (±5) ──
        room_s, room_d = self.tomorrow_room_score(q, kline)

        # ── 扣分: 风险控制 (max -20) ──
        risk_p, risk_d = self.risk_penalty_v3(
            q, kline, turnover, theme_up_ratio, theme_limit_count,
            is_theme_leader=is_theme_leader, snap=snap
        )

        total = min(100, thm_s + cap_s + role_score + tech_s + tail_s + gap_s + room_s - risk_p)

        # ── 信号分级 ──
        if total >= self.V3_STRONG_BUY:
            signal = '强买入'
        elif total >= self.V3_BUY_OBSERVE:
            signal = '买入观察'
        elif total >= self.V3_WATCH:
            signal = '关注'
        else:
            return None

        # ── 买入类型 ──
        pullback_quality = tech_d.get('pullback_v3', 0)
        buy_type, confidence = self.classify_buy_type_v3(
            role, thm_s, theme_up_ratio, theme_limit_count, tech_s, pullback_quality
        )
        # 强势回调低吸形态: 形态分高时优先标记
        if gap_s >= 10:
            buy_type = 'PULLBACK_GAP'
            confidence = max(confidence, 85)

        # ── 次日预期 ──
        next_day_expectation = self._estimate_next_day(room_s, role, thm_s, cap_s)
        if gap_s >= 10:
            next_day_expectation = '强势回调低吸,次日反抽概率大'

        signal_data = {
            'ts_code': ts_code,
            'theme': theme_name,
            'total_score': total,
            'theme_score': thm_s,
            'capital_score': cap_s,
            'role_score': role_score,
            'technical_score': tech_s,
            'timing_score': tail_s,
            'gap_score': gap_s,
            'room_score': room_s,
            'risk_penalty': risk_p,
            'signal': signal,
            'role': role,
            'buy_type': buy_type,
            'confidence': confidence,
            'next_day_expectation': next_day_expectation,
            'pct_chg': q.get('pct_chg', 0),
            'price': q.get('price', 0),
            'detail': {
                **thm_d, **cap_d, **role_d, **tech_d, **tail_d, **gap_d, **room_d, **risk_d,
                'v3_filter_reason': reason,
            },
        }

        # 可解释性文本
        signal_data['explain'] = self.explain_score_v3(signal_data)

        return signal_data

    def _estimate_next_day(self, room_score, role, theme_score, capital_score):
        """估算次日预期表现"""
        if room_score < 0:
            return '空间有限,谨慎'
        if role == 'leader' and theme_score >= 25 and capital_score >= 15:
            return '龙头强主题,次日高开概率大'
        if role == 'leader' and theme_score >= 20:
            return '龙头领涨,次日有望延续'
        if role == 'core' and theme_score >= 15:
            return '中军稳健,次日小幅上涨'
        if capital_score >= 15:
            return '资金关注,次日有冲高机会'
        return '关注次日开盘确认'

    def explain_score_v3(self, sig):
        """
        生成V3评分的可解释性文本
        输入: score_v3返回的signal dict
        输出: 多行中文解释文本
        """
        lines = []
        ts_code = sig.get('ts_code', '')
        name = sig.get('name', ts_code)
        total = sig.get('total_score', 0)
        role = sig.get('role', '')
        buy_type = sig.get('buy_type', '')
        signal = sig.get('signal', '')
        detail = sig.get('detail', {})

        role_cn = {'leader': '龙头', 'core': '中军', 'follow': '跟风', 'weak': '弱关联'}.get(role, role)
        buy_cn = {
            'LEADER_BREAKOUT': '龙头突破',
            'CORE_PULLBACK': '中军回踩',
            'ROTATION_ENTRY': '轮动入场',
            'TAIL_TIMING': '尾盘择时',
            'PULLBACK_GAP': '强势回调低吸',
        }.get(buy_type, buy_type)

        lines.append(f"【{name}({ts_code})】V3评分: {total}分 | {signal} | {role_cn} | {buy_cn}")

        # 主题资金层
        thm_s = sig.get('theme_score', 0)
        lines.append(f"\n 主题资金层 ({thm_s}/30分):")
        lines.append(f"   涨停数={detail.get('limit_count', 0)}只 上涨比={detail.get('up_ratio', 0)}% 龙头涨幅={detail.get('leader_change', 0)}%")
        lines.append(f"   热度={detail.get('heat_score', 0)} + 龙头强度={detail.get('leader_score', 0)} + 资金扩散={detail.get('diffusion_score', 0)}")

        # 个股资金行为
        cap_s = sig.get('capital_score', 0)
        lines.append(f"\n 个股资金行为 ({cap_s}/25分):")
        lines.append(f"   量比={detail.get('amount_ratio', 0)} 换手={detail.get('turnover', 0)}%")
        lines.append(f"   成交额异常={detail.get('amount_abnormal', 0)} + 换手质量={detail.get('turnover_quality', 0)} + 主力代理={detail.get('moneyflow_proxy', 0)}")

        # 股票角色
        role_s = sig.get('role_score', 0)
        lines.append(f"\n 股票角色 ({role_s}/20分): {role_cn}")
        lines.append(f"   主题内涨幅排名第{detail.get('rank_in_theme', '?')} 成交额排名第{detail.get('amount_rank_in_theme', '?')}")

        # 技术结构
        tech_s = sig.get('technical_score', 0)
        lines.append(f"\n 技术结构 ({tech_s}/15分):")
        lines.append(f"   趋势={detail.get('trend_v3', 0)} + 回踩={detail.get('pullback_v3', 0)}(距MA20={detail.get('dist_ma20_pct', '?')}%) + 突破={detail.get('breakout_v3', 0)}")

        # 尾盘交易
        tail_s = sig.get('timing_score', 0)
        lines.append(f"\n 尾盘交易 ({tail_s}/10分):")
        lines.append(f"   尾盘量占比={detail.get('tail_vol_ratio', 0)} 距最高={detail.get('dist_to_high', '?')}% 尾拉={detail.get('tail_rally', '?')}%")

        # 强势基因回调低吸形态
        gap_s = sig.get('gap_score', 0)
        lines.append(f"\n 回调低吸形态 ({gap_s}/15分):")
        lines.append(f"   20日涨停={detail.get('limit_up_20d', '?')}次 基因={detail.get('gene_score', 0)}")
        lines.append(f"   回撤={detail.get('drawdown_from_20d_high', '?')}% 回调质量={detail.get('pullback_quality_score', 0)}")
        lines.append(f"   低开={detail.get('gap_pct', '?')}% 承接={detail.get('gap_absorb_score', 0)} 盘中={detail.get('intraday_gain', '?')}%")

        # 次日空间
        room_s = sig.get('room_score', 0)
        lines.append(f"\n 次日空间 ({room_s:+d}分):")
        lines.append(f"   距压力位={detail.get('pressure_room_pct', '?')}%")

        # 风险扣分
        risk_p = sig.get('risk_penalty', 0)
        lines.append(f"\n 风险扣分 (-{risk_p}分):")
        risk_items = []
        for k in ['risk_high', 'risk_high_turnover', 'risk_isolated', 'trap_weak_day', 'trap_long_lower']:
            if detail.get(k):
                risk_items.append(f"   {detail[k]}")
        if risk_items:
            lines.extend(risk_items)
        else:
            lines.append("   无风险项触发")

        # 买入理由
        lines.append(f"\n 买入理由:")
        if role == 'leader':
            lines.append(f"   龙头股,主题内涨幅排名第{detail.get('rank_in_theme', '?')}位,辨识度高")
        elif role == 'core':
            lines.append(f"   中军股,成交额主题内排名第{detail.get('amount_rank_in_theme', '?')}位,趋势稳健")
        if detail.get('heat_score', 0) >= 7:
            lines.append(f"   主题涨停{detail.get('limit_count', 0)}只,热度较高")
        if detail.get('amount_ratio', 0) >= 1.5:
            lines.append(f"   量比{detail.get('amount_ratio', 0)},资金明显放大")
        if detail.get('pullback_v3', 0) >= 3:
            lines.append(f"   距MA20={detail.get('dist_ma20_pct', '?')}%,回踩到位")
        if detail.get('breakout_v3', 0) >= 5:
            lines.append("   突破10日新高,结构强势")
        if detail.get('limit_up_20d', 0) >= 2:
            lines.append(f"   20日{detail.get('limit_up_20d', 0)}次涨停,强势基因")
        if detail.get('gap_pct', 0) and detail.get('gap_pct', 0) < 0:
            lines.append(f"   今日低开{detail.get('gap_pct', 0)}%,低开高走承接好")
        if detail.get('pullback_quality_score', 0) >= 3:
            lines.append(f"   距20日高回撤{detail.get('drawdown_from_20d_high', '?')}%,回调充分")
        if detail.get('pressure_room_pct', 0) and detail.get('pressure_room_pct', 0) > 5:
            lines.append(f"   距压力位{detail.get('pressure_room_pct', 0)}%,次日空间充足")
        lines.append(f"   买入类型: {buy_cn}")

        return '\n'.join(lines)

    # ═══════════════════════════════════════════════════════
    # V3 实盘入表筛选
    # ═══════════════════════════════════════════════════════
    def filter_for_tracking_v3(self, signals):
        """
        V3 实盘入表筛选:
        1. 信号为强买入或买入观察
        2. 排除北交所
        3. 每主题最多TOP3 (V3比V2更宽松,因为多层过滤已足够)
        """
        candidates = []
        for s in signals:
            sig = s.get('signal', '')
            if sig not in ('强买入', '买入观察'):
                continue
            code = s.get('ts_code', '')
            if code.startswith(('9', '4')):
                continue
            candidates.append(s)

        theme_groups = {}
        for s in candidates:
            theme = s.get('theme', '其他')
            theme_groups.setdefault(theme, []).append(s)

        final = []
        for theme, stocks in theme_groups.items():
            stocks_sorted = sorted(stocks, key=lambda x: -x.get('total_score', 0))
            final.extend(stocks_sorted[:3])

        return final