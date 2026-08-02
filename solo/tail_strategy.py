# -*- coding: utf-8 -*-
"""
「猎尾」尾盘突袭战法 - 独立策略模块
从 realtime_theme_monitor.py 提炼,用于历史回测

评分模型 (100分 + 20技术分 - 诱多扣分):
- 全天结构   (35分): 振幅控制 + 阳线实体 + 缩量程度
- 尾盘攻击力 (25分): 尾盘拉升幅度 + 收盘位置(回测模式不可靠,降低权重)
- 主题共振   (20分): 主题强度 + 龙头地位 + 涨停配合
- 位置安全   (20分): 距MA5/MA10 + 距20日高回撤
- 技术形态   (20分): MACD/KDJ/RSI/BOLL/CCI
- 诱多扣分   (≤30分): 四大诱多红旗

硬过滤: 涨停/跌停/振幅>8%/跌>2.5%/不在主题/连板≥2/距MA20>25%
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
        """
        硬过滤: 返回 (True/False, reason)
        q: 行情 dict(open/high/low/price/last_close/pct_chg/vol)
        kline: DataFrame 含 close/high/low/vol/pct_chg 列
        turnover: 换手率%
        total_mv: 总市值(万元)
        theme_strength: 最强主题强度分
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

        # 4. 不在任何主题中排除(由调用方保证,这里不检查)

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
            close_5d_ago = float(kline['close'].iloc[-6]) if len(kline) >= 6 else float(kline['close'].iloc[0])
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

        # 10. 总市值<8亿(80000万)排除
        if total_mv > 0 and total_mv < 80000:
            return False, f'市值{total_mv/10000:.1f}亿'

        return True, 'OK'

    # ═══════════════════════════════════════════════════════
    # 尾盘攻击力 (25分)
    # ═══════════════════════════════════════════════════════
    def attack_score(self, q, snap):
        """
        尾盘攻击力 (25分) — 降低权重,因回测模式收盘-开盘模拟不可靠
        snap: 分时快照 dict(tail_base_price, tail_base_vol, morning_vol)
              回测模式下 snap 可为空,用开盘价代替tail_base_price
        """
        score = 0
        detail = {}

        tail_base_price = snap.get('tail_base_price', 0) if snap else 0
        current_price = q.get('price', 0)
        high = q.get('high', 0)

        # ── 1. 尾盘拉升幅度 (6分) ──
        if tail_base_price > 0 and current_price > 0:
            tail_rally = (current_price - tail_base_price) / tail_base_price * 100
            detail['tail_rally'] = round(tail_rally, 2)
            if tail_rally > 1.0:
                score += 6
            elif tail_rally > 0.5:
                score += 4
            elif tail_rally > 0.2:
                score += 2
            elif tail_rally > 0:
                score += 1
        else:
            # 回测模式: 无分时数据,用收盘-开盘估算尾盘拉升
            open_p = q.get('open', 0)
            if open_p > 0 and current_price > 0:
                tail_rally = (current_price - open_p) / open_p * 100
                detail['tail_rally'] = round(tail_rally, 2)
                # 回测模式保守给分
                if tail_rally > 2.0:
                    score += 4
                elif tail_rally > 1.0:
                    score += 3
                elif tail_rally > 0.5:
                    score += 2
                elif tail_rally > 0:
                    score += 1
            else:
                detail['tail_rally'] = 0

        # ── 2. 尾盘量能爆发 (10分) — 回测无数据,不给分 ──
        detail['tail_vol_ratio'] = 0

        # ── 3. 收盘位置 (9分): 光头阳线=次日惯性高开 ──
        if high > 0 and current_price > 0:
            close_ratio = current_price / high
            detail['close_ratio'] = round(close_ratio, 2)
            if close_ratio > 0.98:
                score += 9
            elif close_ratio > 0.95:
                score += 6
            elif close_ratio > 0.90:
                score += 3
        else:
            detail['close_ratio'] = 0

        return min(score, 25), detail

    # ═══════════════════════════════════════════════════════
    # 全天结构 (35分)
    # ═══════════════════════════════════════════════════════
    def structure_score(self, q, kline):
        """全天结构质量 (35分)"""
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        pct = q.get('pct_chg', 0)
        vol = q.get('vol', 0)
        score = 0
        detail = {}

        # 1. 振幅控制 (12分)
        if last_close > 0 and high > 0 and low > 0:
            amplitude = (high - low) / last_close * 100
            detail['amplitude'] = round(amplitude, 1)
            if amplitude < 3:
                score += 12
            elif amplitude < 5:
                score += 8
            elif amplitude < 7:
                score += 4
        else:
            detail['amplitude'] = 0

        # 2. 阳线实体 (10分)
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

        # 3. 缩量程度 (13分)
        if kline is not None and len(kline) >= 5:
            avg_vol_5d = kline['vol'].iloc[-5:].mean()
            if avg_vol_5d > 0:
                vol_ratio = vol / avg_vol_5d
                detail['vol_ratio_5d'] = round(vol_ratio, 2)
                if vol_ratio < 0.7:
                    score += 13
                elif vol_ratio < 0.85:
                    score += 10
                elif vol_ratio < 1.0:
                    score += 7
                elif vol_ratio < 1.2:
                    score += 4
                else:
                    score += 1
        else:
            detail['vol_ratio_5d'] = 0

        return min(score, 35), detail

    # ═══════════════════════════════════════════════════════
    # 位置安全 (20分)
    # ═══════════════════════════════════════════════════════
    def position_score(self, q, kline):
        """位置安全边际 (20分)"""
        price = q.get('price', 0)
        score = 0
        detail = {}

        if kline is None or len(kline) < 20:
            detail['ma5_dist'] = 0
            detail['ma10_dist'] = 0
            detail['pullback'] = 0
            return 5, detail

        ma5 = kline['close'].iloc[-5:].mean()
        ma10 = kline['close'].iloc[-10:].mean()
        high_20d = kline['high'].iloc[-20:].max()

        # 1. 距MA5 (8分)
        if price > 0 and ma5 > 0:
            ma5_dist = abs(price - ma5) / ma5 * 100
            detail['ma5_dist'] = round(ma5_dist, 1)
            if ma5_dist < 2:
                score += 8
            elif ma5_dist < 4:
                score += 5
            elif ma5_dist < 6:
                score += 2
        else:
            detail['ma5_dist'] = 0

        # 2. 距MA10 (7分)
        if price > 0 and ma10 > 0:
            ma10_ratio = price / ma10
            detail['ma10_ratio'] = round(ma10_ratio, 2)
            if 0.97 <= ma10_ratio <= 1.05:
                score += 7
            elif 0.94 <= ma10_ratio <= 1.08:
                score += 4
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

        return min(score, 20), detail

    # ═══════════════════════════════════════════════════════
    # 主题共振 (20分)
    # ═══════════════════════════════════════════════════════
    def theme_score(self, theme_strength, layer, theme_zt_count):
        """
        主题共振 (20分)
        theme_strength: 主题强度分
        layer: 'leader'/'middle'/'follower'
        theme_zt_count: 主题内涨停股数
        """
        score = 0
        detail = {
            'theme_strength': round(theme_strength, 1),
            'theme_zt': theme_zt_count,
            'layer': layer,
        }

        # 1. 主题强度 (8分)
        if theme_strength > 2:
            score += 8
        elif theme_strength > 0:
            score += 6
        elif theme_strength > -1:
            score += 4
        else:
            score += 1

        # 2. 龙头地位 (8分)
        if layer == 'leader':
            score += 8
        elif layer == 'middle':
            score += 5
        else:
            score += 2

        # 3. 主题内有涨停配合 (4分)
        if theme_zt_count >= 3:
            score += 4
        elif theme_zt_count >= 2:
            score += 3
        elif theme_zt_count >= 1:
            score += 1

        return min(score, 20), detail

    # ═══════════════════════════════════════════════════════
    # 技术形态 (20分)
    # ═══════════════════════════════════════════════════════
    def technical_score(self, factor_row):
        """
        技术形态加分 (20分)
        factor_row: stk_factor_pro 一行数据 (已重命名为简洁字段名)
        """
        score = 0
        detail = {}
        if factor_row is None:
            return 0, detail

        # 1. MACD趋势 (6分)
        try:
            dif = float(factor_row.get('macd_dif', 0) or 0)
            dea = float(factor_row.get('macd_dea', 0) or 0)
            if dif > dea:
                score += 4
                detail['macd'] = '多头'
                if dif > 0 and dea > 0:
                    score += 2
                    detail['macd'] = '零上多头'
        except Exception:
            pass

        # 2. KDJ超买控制 (5分)
        try:
            kdj_j = float(factor_row.get('kdj_j', 50) or 50)
            kdj_k = float(factor_row.get('kdj_k', 50) or 50)
            detail['kdj_j'] = round(kdj_j, 1)
            if kdj_j < 80:
                if kdj_j > kdj_k:
                    score += 5
                    detail['kdj'] = '金叉'
                elif kdj_j > 20:
                    score += 3
                    detail['kdj'] = '健康'
        except Exception:
            pass

        # 3. RSI健康度 (5分)
        try:
            rsi_6 = float(factor_row.get('rsi_6', 50) or 50)
            rsi_12 = float(factor_row.get('rsi_12', 50) or 50)
            detail['rsi_6'] = round(rsi_6, 1)
            if 40 <= rsi_6 <= 70:
                score += 5
            elif 30 <= rsi_6 < 40:
                score += 3
            elif 70 < rsi_6 <= 80:
                score += 2
        except Exception:
            pass

        # 4. BOLL位置 (4分)
        try:
            close = float(factor_row.get('close', 0) or 0)
            boll_mid = float(factor_row.get('boll_mid', 0) or 0)
            boll_upper = float(factor_row.get('boll_upper', 0) or 0)
            boll_lower = float(factor_row.get('boll_lower', 0) or 0)
            if close > 0 and boll_mid > 0:
                if close > boll_mid:
                    score += 2
                    detail['boll'] = '中轨上方'
                    if boll_upper > close and (boll_upper - close) / close * 100 < 3:
                        score += 2
                        detail['boll'] = '接近上轨'
                elif boll_lower > 0 and (close - boll_lower) / boll_lower * 100 < 2:
                    score += 1
                    detail['boll'] = '下轨支撑'
        except Exception:
            pass

        return min(score, 20), detail

    # ═══════════════════════════════════════════════════════
    # 诱多风险扣分 (≤30分)
    # ═══════════════════════════════════════════════════════
    def trap_penalty(self, q, kline, theme_strength, theme_zt_count, snap=None):
        """
        诱多风险扣分 (≤30分)
        回测模式: snap=None,用开盘价代替tail_base_price
        """
        penalty = 0
        detail = {}

        open_p = q.get('open', 0)
        close = q.get('price', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        last_close = q.get('last_close', 0)
        pct = q.get('pct_chg', 0)
        tail_base_price = snap.get('tail_base_price', 0) if snap else open_p  # 回测用开盘价代替

        # 红旗1: 全天弱势+尾盘急拉 — 最大陷阱,扣分加重
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
            if price_range > 0 and last_close > 0:
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
    # 完整评分
    # ═══════════════════════════════════════════════════════
    def score(self, ts_code, q, kline, factor_row, turnover, total_mv,
              theme_name, theme_strength, layer, theme_zt_count, snap=None):
        """
        完整评分流程
        返回: signal_dict 或 None(未通过硬过滤/分数太低)
        """
        # 硬过滤
        passed, reason = self.hard_filter(
            ts_code, q, kline, turnover, total_mv, theme_strength
        )
        if not passed:
            return None

        # 五维评分
        atk_s, atk_d = self.attack_score(q, snap)
        str_s, str_d = self.structure_score(q, kline)
        pos_s, pos_d = self.position_score(q, kline)
        thm_s, thm_d = self.theme_score(theme_strength, layer, theme_zt_count)
        tech_s, tech_d = self.technical_score(factor_row)

        # 诱多扣分
        trap_p, trap_d = self.trap_penalty(q, kline, theme_strength, theme_zt_count, snap)

        total = atk_s + str_s + pos_s + thm_s + tech_s - trap_p

        if total < self.WATCH_THRESHOLD:
            return None

        if total >= self.STRONG_BUY_THRESHOLD:
            signal = '强买入'
        elif total >= self.BUY_THRESHOLD:
            signal = '买入'
        else:
            signal = '关注'

        return {
            'ts_code': ts_code,
            'theme': theme_name,
            'total_score': total,
            'attack_score': atk_s,
            'structure_score': str_s,
            'position_score': pos_s,
            'theme_score': thm_s,
            'tech_score': tech_s,
            'trap_penalty': trap_p,
            'signal': signal,
            'pct_chg': q.get('pct_chg', 0),
            'price': q.get('price', 0),
            'detail': {**atk_d, **str_d, **pos_d, **thm_d, **tech_d, **trap_d},
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

        # 每主题TOP2
        theme_groups = {}
        for s in candidates:
            theme = s.get('theme', '其他')
            theme_groups.setdefault(theme, []).append(s)

        final = []
        for theme, stocks in theme_groups.items():
            stocks_sorted = sorted(stocks, key=lambda x: -x.get('total_score', 0))
            final.extend(stocks_sorted[:self.TRACK_TOP_N_PER_THEME])

        return final
