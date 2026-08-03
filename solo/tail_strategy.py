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


class TailStrategy:
    """尾盘突袭战法评分引擎(纯函数,无外部依赖)"""

    # 信号阈值
    STRONG_BUY_THRESHOLD = 85
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