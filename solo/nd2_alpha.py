# -*- coding: utf-8 -*-
"""
「猎尾V5」ND2 Alpha Engine 主评分器
四层决策系统: L0市场环境 -> L1硬过滤 -> L2形态分类 -> L3多维评分 -> S/A/B分级

评分体系 (Base 100 + Bonus 10 - Risk 20):
  Trend Structure   15
  Pattern Quality   15
  Tail Flow         25   (V5核心)
  Strong Gene       10
  ND2 Potential     15
  Theme Alpha       12
  Market Alpha       8

最终排序: rank_score = 0.40*P_UP_2 + 0.20*(1-P_DD_2) + 0.20*norm(FinalScore)
                     + 0.10*confidence + 0.10*expected_alpha
"""

from nd2_config import (MARKET_MULTIPLIER, TREND_TO_MARKET, HARD_FILTER, SCORE_WEIGHTS,
                         BONUS_MAX, RISK_PENALTY_MAX, GRADE_THRESHOLDS, ALPHA_WEIGHTS,
                         RANK_WEIGHTS, PULLBACK_QUALITY, STRONG_GENE, THEME_ALPHA,
                         BREAKOUT_MIN_MULTIPLIER)
from nd2_pattern import PatternClassifier, PULLBACK_GAP, BREAKOUT_TAIL, STEALTH_ACCUMULATION, OTHER
from nd2_tailflow import TailFlowEngine
from nd2_engine import ND2Engine
from nd2_risk import RiskEngine


class ND2AlphaEngine:
    """猎尾V5 主引擎: 输入单只股票的上下文,输出完整信号字典"""

    def __init__(self, db_path=None):
        self.nd2 = ND2Engine(db_path)
        self.classifier = PatternClassifier()
        self.tailflow = TailFlowEngine()
        self.risk = RiskEngine()

    # ══════════════════════════════════════════
    # L0: 市场环境乘数
    # ══════════════════════════════════════════
    @staticmethod
    def market_multiplier(trend_score, market_status=None):
        """趋势总评分 -> 市场乘数 0.50~1.15"""
        if market_status and market_status in MARKET_MULTIPLIER:
            return MARKET_MULTIPLIER[market_status]
        for bound, mkt in TREND_TO_MARKET:
            if trend_score >= bound:
                return MARKET_MULTIPLIER[mkt]
        return 1.0

    # ══════════════════════════════════════════
    # L1: 硬过滤 (V5分层版)
    # ══════════════════════════════════════════
    @staticmethod
    def hard_filter(ts_code, q, kline, f, turnover, total_mv, theme_strength,
                    tail_flow_score=None, snap=None):
        """
        返回 (passed, reason)
        V5升级: 涨幅分层 + 振幅豁免路径, 替代V3一刀切
        """
        hf = HARD_FILTER
        pct = f.get('pct', 0)
        price = f.get('price', 0)

        # 1. 涨停/跌停排除
        limit_up = 19.5 if ts_code.startswith(('300', '688')) else 9.5
        if pct >= limit_up:
            return False, '涨停'
        if pct <= -9.5:
            return False, '跌停'

        # 2. 涨幅分层
        layer = 'reject'
        for (lo, hi), tag in hf['pct_layers'].items():
            if (lo is None or pct >= lo) and (hi is None or pct < hi):
                layer = tag
                break
        if layer == 'reject':
            return False, f'涨幅{pct:.1f}%>8%'
        if layer == 'weak':
            # <0.5%: 需 TailFlow强 + ClosePosition>=0.80
            if tail_flow_score is None or tail_flow_score < hf['weak_pct_tailflow_min']:
                return False, f'涨幅{pct:.1f}%低且尾盘弱'
            if f.get('close_position', 0) < hf['weak_pct_closepos_min']:
                return False, f'涨幅{pct:.1f}%低且收盘位弱'
        if layer == 'strong_only':
            # 6.5~8%: 仅强资金抢筹(尾盘量比>=1.8且有效放量)允许
            ratio = f.get('tail_vs_noon_ratio')
            if not (ratio and ratio >= 1.8):
                return False, f'涨幅{pct:.1f}%高且无强资金'

        # 3. 振幅分层: >8% 走豁免路径(早盘震荡+午后缩量+尾盘上攻)
        if f.get('last_close', 0) > 0 and f.get('high', 0) > 0 and f.get('low', 0) > 0:
            amplitude = (f['high'] - f['low']) / f['last_close'] * 100
            if amplitude > hf['max_amplitude']:
                ex = hf['amplitude_exemption']
                # 豁免条件: 午后(14:00)量 <= 早盘量*1.3 (午后缩量稳定) 且 14:30后回涨
                morning_vol = f.get('morning_vol', 0)
                noon_vol = f.get('noon_vol', 0)
                tail_rebound = 0
                if f.get('tail_base_price', 0) > 0 and price > 0:
                    tail_rebound = (price - f['tail_base_price']) / f['tail_base_price'] * 100
                exempt = (morning_vol > 0 and noon_vol > 0
                          and noon_vol <= morning_vol * ex['morning_vs_noon_vol_ratio_max'] * 1.5
                          and tail_rebound >= ex['tail_rebound_min_pct'])
                if not exempt:
                    return False, f'振幅{amplitude:.1f}%'

        # 4. 连续涨停>=2板
        if kline is not None and len(kline) >= 3:
            try:
                prev_pct = float(kline['pct_chg'].iloc[-1])
                prev2_pct = float(kline['pct_chg'].iloc[-2])
                prev_limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
                if prev_pct >= prev_limit and prev2_pct >= prev_limit:
                    return False, '连板2天'
            except (TypeError, ValueError, IndexError):
                pass

        # 5. 距MA20>25%
        if f.get('ma20', 0) > 0 and price > 0 and price > f['ma20'] * 1.25:
            return False, '距MA20>25%'

        # 6. 近5日涨幅>15% (形态豁免: PULLBACK_GAP天然含强势基因)
        g5 = f.get('gain_5d', 0)
        if g5 > hf['max_gain_5d'] and f.get('_pattern') not in (PULLBACK_GAP,):
            return False, f'5日涨{g5:.1f}%'

        # 7. 换手异常
        if turnover > 0:
            if turnover > hf['max_turnover']:
                return False, f'换手{turnover:.1f}%高'
            if turnover < hf['min_turnover']:
                return False, f'换手{turnover:.1f}%低'

        # 8. 主题退潮
        if theme_strength is not None and theme_strength < hf['min_theme_strength']:
            return False, '主题退潮'

        # 9. 市值
        if total_mv and 0 < total_mv < hf['min_mv']:
            return False, f'市值{total_mv/10000:.1f}亿'

        # 10. 北交所
        if ts_code.startswith(('8', '4', '92')):
            return False, '北交所'

        return True, 'OK'

    # ══════════════════════════════════════════
    # L3 各维度评分
    # ══════════════════════════════════════════

    @staticmethod
    def trend_structure_score(f):
        """趋势结构 (15分): MA排列 + 20日趋势强度 + 位置"""
        if not f.get('kline_ok'):
            return 3, {}
        score = 0
        d = {}
        ma5, ma10, ma20 = f.get('ma5', 0), f.get('ma10', 0), f.get('ma20', 0)
        price = f.get('price', 0)

        # MA多头排列 (6)
        if ma5 > ma10 > ma20:
            score += 6
        elif ma10 > ma20:
            score += 3
        d['ma_aligned'] = score

        # 20日涨幅趋势 (5)
        g20 = f.get('gain_20d', 0)
        if 5 <= g20 <= 20:
            score += 5
        elif 0 <= g20 < 5:
            score += 3
        elif g20 > 30:
            score += 1
        elif g20 > 20:
            score += 3
        else:
            score += 1
        d['gain_20d'] = round(g20, 1)

        # 价格位置: MA20上方但不过远 (4)
        if ma20 > 0 and price > 0:
            dist = (price / ma20 - 1) * 100
            d['ma20_dist'] = round(dist, 1)
            if 0 < dist <= 8:
                score += 4
            elif dist <= 0:
                score += 1
            elif dist <= 15:
                score += 2
        return min(score, 15), d

    @staticmethod
    def pattern_quality_score(f, pattern):
        """
        形态质量 (15分):
        PULLBACK_GAP -> Pullback Quality
        其他形态    -> Breakout/Accumulation Quality 映射
        """
        if pattern == PULLBACK_GAP:
            return ND2AlphaEngine._pullback_quality(f)
        elif pattern == BREAKOUT_TAIL:
            return ND2AlphaEngine._breakout_quality(f)
        elif pattern == STEALTH_ACCUMULATION:
            return ND2AlphaEngine._accumulation_quality(f)
        return 0, {'pattern_quality': 'OTHER形态无质量分'}

    @staticmethod
    def _pullback_quality(f):
        """Pullback Quality (15分)"""
        pq = PULLBACK_QUALITY
        score = 0
        d = {}
        # 回调2~7天
        last_zt = f.get('last_zt_days_ago')
        if last_zt is not None and pq['days']['range'][0] <= last_zt <= pq['days']['range'][1]:
            score += pq['days']['score']
            d['pb_days'] = last_zt
        # 回撤5~15%
        dd = f.get('drawdown_20d', 0)
        if pq['depth']['range'][0] <= dd <= pq['depth']['range'][1]:
            score += pq['depth']['score']
            d['pb_depth'] = round(dd, 1)
        # 缩量
        vs = f.get('vol_shrink_ratio')
        if vs is not None and vs <= pq['vol_shrink']['max_ratio']:
            score += pq['vol_shrink']['score']
            d['pb_vol_shrink'] = vs
        # MA10企稳
        if f.get('ma10', 0) > 0 and f.get('price', 0) > 0:
            dist_ma10 = abs(f['price'] - f['ma10']) / f['ma10'] * 100
            if dist_ma10 <= pq['ma10_stabilize']['max_dist_pct']:
                score += pq['ma10_stabilize']['score']
                d['pb_ma10'] = round(dist_ma10, 1)
        # 不破突破K中轴: 用"价格在MA5上方或20日高的92%以上"近似
        if f.get('high_20d_pre', 0) > 0 and f.get('price', 0) > 0:
            mid = (f['high_20d_pre'] + f.get('low_20d', f['price'])) / 2
            if f['price'] >= mid * 0.99:
                score += pq['break_kline_mid']['score']
                d['pb_mid_hold'] = True
        # 尾盘重新放量
        ratio = f.get('tail_vs_noon_ratio')
        if ratio and ratio >= pq['tail_reflow']['min_vol_ratio']:
            score += pq['tail_reflow']['score']
            d['pb_tail_reflow'] = round(ratio, 2)
        return min(score, 15), d

    @staticmethod
    def _breakout_quality(f):
        """Breakout Quality (15分)"""
        score = 0
        d = {}
        # 突破强度: 现价超平台上沿幅度 (温和突破1~3%最优)
        ph = f.get('high_20d_pre', 0)
        if ph > 0 and f.get('price', 0) > 0:
            brk = (f['price'] / ph - 1) * 100
            d['brk_pct'] = round(brk, 2)
            if 0.5 <= brk <= 3:
                score += 6
            elif brk <= 0.5:
                score += 4   # 贴沿蓄势
            elif brk <= 4:
                score += 3
        # 突破量能
        ratio = f.get('tail_vs_noon_ratio')
        if ratio:
            d['brk_vol_ratio'] = round(ratio, 2)
            if ratio >= 1.5:
                score += 5
            elif ratio >= 1.2:
                score += 3
        # 平台时长(近似: 用20日涨幅低+波动小)
        g20 = f.get('gain_20d', 0)
        if -5 <= g20 <= 10:
            score += 2
            d['platform_mature'] = True
        # 收盘位置
        cp = f.get('close_position', 0.5)
        if cp >= 0.9:
            score += 2
        return min(score, 15), d

    @staticmethod
    def _accumulation_quality(f):
        """Accumulation Quality (15分)"""
        score = 0
        d = {}
        # 尾盘量能阶梯
        ratio = f.get('tail_vs_noon_ratio')
        if ratio:
            d['acc_vol_ratio'] = round(ratio, 2)
            if ratio >= 2.0:
                score += 6
            elif ratio >= 1.5:
                score += 4
            elif ratio >= 1.2:
                score += 2
        # 价格阶梯抬升(尾盘涨幅)
        tail_ret = 0
        if f.get('tail_base_price', 0) > 0 and f.get('price', 0) > 0:
            tail_ret = (f['price'] - f['tail_base_price']) / f['tail_base_price'] * 100
            d['acc_tail_rally'] = round(tail_ret, 2)
            if 0.5 <= tail_ret <= 2:
                score += 5
            elif tail_ret > 2:
                score += 3
        # 回撤浅
        if f.get('high', 0) > 0 and f.get('price', 0) > 0:
            dist_high = (f['high'] - f['price']) / f['price'] * 100
            if dist_high < 0.5:
                score += 4
            elif dist_high < 1.0:
                score += 2
        return min(score, 15), d

    @staticmethod
    def strong_gene_score(f):
        """强势基因 (10分): 涨停基因+趋势基因+质量修正"""
        sg = STRONG_GENE
        score = 0
        d = {}
        zt = f.get('limit_up_20d', 0)
        d['limit_up_20d'] = zt
        if zt >= 3:
            score += sg['limit_up_default']
        elif zt == 2:
            score += 5
        elif zt == 1:
            score += 3
        # 趋势基因
        ma5, ma10, ma20 = f.get('ma5', 0), f.get('ma10', 0), f.get('ma20', 0)
        if ma5 > ma10 > ma20:
            score += sg['trend_ma_aligned']
        if len(f.get('_ma20_slope_up', ())) > 0:
            score += sg['trend_ma20_up']
        # 质量修正: 连续暴涨后高位涨停基因降质
        if f.get('gain_20d', 0) > sg['high_gain_threshold']:
            score = max(0, score - sg['gene_quality_penalty'])
            d['gene_degraded'] = True
        return min(score, 10), d

    @staticmethod
    def theme_alpha_score(f, theme_strength, theme_up_ratio, theme_limit_count,
                          stock_pct, leader_pct):
        """Theme Alpha (12分)"""
        ta = THEME_ALPHA
        score = 0
        d = {}
        # 主题主线强度 (0~4)
        if theme_strength is not None:
            ts = theme_strength
        else:
            ts = 0
        if ts >= 8:
            score += 4
        elif ts >= 5:
            score += 3
        elif ts >= 2:
            score += 2
        elif ts >= 0:
            score += 1
        d['theme_strength'] = ts
        # 主题当日资金回流 (0~3): 上涨比+涨停数
        if theme_up_ratio >= 70 and theme_limit_count >= 2:
            score += 3
        elif theme_up_ratio >= 60 and theme_limit_count >= 1:
            score += 2
        elif theme_up_ratio >= 50:
            score += 1
        d['theme_up_ratio'] = round(theme_up_ratio, 0)
        # 个股在主题中的强度 (0~3)
        if leader_pct is not None and stock_pct is not None:
            if stock_pct >= leader_pct - 0.5:
                score += 3   # 个股即最强
            elif stock_pct >= leader_pct * 0.7:
                score += 2
            elif stock_pct >= leader_pct * 0.4:
                score += 1
        # 龙头联动 (0~2)
        if leader_pct is not None and leader_pct >= 5:
            score += 2
        elif leader_pct is not None and leader_pct >= 2:
            score += 1
        # Individual Alpha Bonus: 个股尾盘行为明显强于主题
        ratio = f.get('tail_vs_noon_ratio')
        if ratio and ratio >= 1.8 and (theme_up_ratio or 50) < 55:
            score += 1
            d['individual_alpha'] = True
        return min(score, 12), d

    @staticmethod
    def market_alpha_score(market_multiplier, index_pct=None):
        """Market Alpha (8分)"""
        score = 0
        d = {}
        # 乘数越高分越高 (0~5)
        mm = market_multiplier
        if mm >= 1.15:
            score += 5
        elif mm >= 1.05:
            score += 4
        elif mm >= 1.0:
            score += 3
        elif mm >= 0.9:
            score += 2
        elif mm >= 0.75:
            score += 1
        # 指数当日涨跌 (0~3)
        if index_pct is not None:
            d['index_pct'] = index_pct
            if index_pct > 1:
                score += 3
            elif index_pct > 0.3:
                score += 2
            elif index_pct > -0.3:
                score += 1
        d['multiplier'] = mm
        return min(score, 8), d

    # ══════════════════════════════════════════
    # S/A/B 分级
    # ══════════════════════════════════════════
    @staticmethod
    def grade(final_score, tail_flow, pattern_quality, nd2_score, risk_penalty,
              market_multiplier, tailflow_detail):
        """多因子门槛 S/A/B 分级"""
        gt = GRADE_THRESHOLDS

        # S级检测
        s_cfg = gt['S']
        s_ok = (final_score >= s_cfg['final_score']
                and tail_flow >= s_cfg['tail_flow']
                and pattern_quality >= s_cfg['pattern_quality']
                and nd2_score >= s_cfg['nd2_potential']
                and risk_penalty <= s_cfg['risk_penalty_max']
                and market_multiplier >= s_cfg['market_multiplier_min'])
        if s_ok:
            # S级禁止项: 尾盘诱多
            if s_cfg.get('forbid_tail_distribution') and (
                    tailflow_detail.get('invalid_volume')
                    or tailflow_detail.get('distribution_suspect')):
                return 'A', 'S门槛达标但尾盘疑似派发降级'
            return 'S', '达标'

        # A级
        a_cfg = gt['A']
        if (final_score >= a_cfg['final_score']
                and tail_flow >= a_cfg['tail_flow']
                and nd2_score >= a_cfg['nd2_potential']
                and risk_penalty <= a_cfg['risk_penalty_max']):
            return 'A', '达标'

        # B级
        if final_score >= gt['B']['final_score']:
            return 'B', '观察池'

        return 'REJECT', '淘汰'

    # ══════════════════════════════════════════
    # 主评分入口
    # ══════════════════════════════════════════
    def evaluate(self, ts_code, q, kline, snap, turnover, total_mv,
                 theme_name='', theme_strength=None, theme_up_ratio=50,
                 theme_limit_count=0, theme_leader_pct=None,
                 trend_score=60, market_status=None, index_pct=None):
        """
        完整评估一只股票,返回信号字典或None(被过滤)
        """
        # ── L2 形态分类 (前置,供硬过滤豁免) ──
        pattern, f, pattern_detail = PatternClassifier.classify(q, kline, snap, ts_code)
        f['_pattern'] = pattern
        f['_pattern_detail'] = pattern_detail
        # 补充: 近3日缩量比 (供pullback质量评分)
        if kline is not None and len(kline) >= 4:
            try:
                import pandas as pd
                vols = pd.to_numeric(kline['vol'], errors='coerce').fillna(0)
                v3d = float(vols.iloc[-4:-1].mean())
                vmax = float(vols.iloc[-21:-1].max()) if len(vols) >= 21 else float(vols.max())
                f['vol_shrink_ratio'] = round(v3d / vmax, 2) if vmax > 0 else None
                f['vol_shrink_ratio_3d'] = f['vol_shrink_ratio']
            except Exception:
                pass

        # ── L0 市场乘数 ──
        mm = self.market_multiplier(trend_score, market_status)
        f['_market_multiplier'] = mm

        # ── 先算TailFlow (硬过滤的weak层需要) ──
        tail_flow, tf_detail = self.tailflow.score(f)

        # ── L1 硬过滤 ──
        passed, reason = self.hard_filter(ts_code, q, kline, f, turnover, total_mv,
                                          theme_strength, tail_flow_score=tail_flow, snap=snap)
        if not passed:
            return None

        # BREAKOUT_TAIL 环境门槛
        if pattern == BREAKOUT_TAIL and mm < BREAKOUT_MIN_MULTIPLIER:
            return None

        # ── L3 各维度 ──
        trend_s, trend_d = self.trend_structure_score(f)
        pq_s, pq_d = self.pattern_quality_score(f, pattern)
        sg_s, sg_d = self.strong_gene_score(f)
        ta_s, ta_d = self.theme_alpha_score(f, theme_strength, theme_up_ratio,
                                            theme_limit_count, q.get('pct_chg', 0),
                                            theme_leader_pct if theme_leader_pct is not None else q.get('pct_chg', 0))
        ma_s, ma_d = self.market_alpha_score(mm, index_pct)
        nd2_s, nd2_d = self.nd2.score(f, pattern, market_status=market_status or '正常市场',
                                      theme_alpha=ta_s, strong_gene=sg_s)
        risk_p, risk_d = self.risk.score(f, pattern, turnover=turnover,
                                         theme_up_ratio=theme_up_ratio,
                                         theme_limit_count=theme_limit_count,
                                         theme_leader_pct=theme_leader_pct or 0)

        # ── Bonus (最高+10): 形态+尾盘共振 ──
        bonus = 0
        bd = {}
        if pattern == PULLBACK_GAP and tail_flow >= 18:
            bonus += 4
            bd['pb_tailflow_resonance'] = True
        if pattern == STEALTH_ACCUMULATION and tf_detail.get('effective_volume'):
            bonus += 3
            bd['stealth_effective'] = True
        if pattern == BREAKOUT_TAIL and tf_detail.get('close_position', 0) >= 0.9:
            bonus += 3
            bd['brk_strong_close'] = True
        if nd2_d.get('p_up_2', 0) >= 0.6 and nd2_d.get('probability_confidence', 0) >= 0.7:
            bonus += 3
            bd['high_p_up_2'] = True
        bonus = min(bonus, BONUS_MAX)

        # ── 综合分 ──
        base = (trend_s + pq_s + tail_flow + sg_s + nd2_s + ta_s + ma_s)
        final_score = max(0, min(100, round(base + bonus - risk_p)))

        # ── S/A/B 分级 ──
        grade, grade_reason = self.grade(final_score, tail_flow, pq_s, nd2_s,
                                         risk_p, mm, tf_detail)

        # ── final_alpha ──
        aw = ALPHA_WEIGHTS
        final_alpha = (aw['tail_flow'] * tail_flow / 25
                       + aw['pattern_quality'] * pq_s / 15
                       + aw['nd2_potential'] * nd2_s / 15
                       + aw['strong_gene'] * sg_s / 10
                       + aw['theme_alpha'] * ta_s / 12
                       + aw['market_alpha'] * ma_s / 8
                       - risk_p / RISK_PENALTY_MAX)

        # ── 概率与期望 ──
        p_up = nd2_d.get('p_up_2', 0.45)
        p_close = nd2_d.get('p_close_2', 0.30)
        p_dd = nd2_d.get('p_dd_2', 0.25)
        conf = nd2_d.get('probability_confidence', 0.0)
        # expected_alpha = P_UP_2*E[gain|up] - P_DD_2*E[loss|dd], 简化用+2%/-2%目标
        expected_alpha = p_up * 0.02 - p_dd * 0.02

        # ── rank_score ──
        rw = RANK_WEIGHTS
        rank_score = (rw['p_up_2'] * p_up
                      + rw['no_drawdown'] * (1 - p_dd)
                      + rw['final_score'] * final_score / 100
                      + rw['probability_confidence'] * conf
                      + rw['expected_alpha'] * expected_alpha * 10)  # alpha放大10倍便于比较

        # ── 信号字典 ──
        signal = {
            'ts_code': ts_code,
            'name': q.get('name', ''),
            'theme': theme_name,
            'pattern': pattern,
            'final_score': final_score,
            'grade': grade,
            'grade_reason': grade_reason,
            # 分项
            'trend_structure': trend_s,
            'pattern_quality': pq_s,
            'tail_flow': tail_flow,
            'strong_gene': sg_s,
            'nd2_potential': nd2_s,
            'theme_alpha': ta_s,
            'market_alpha': ma_s,
            'bonus': bonus,
            'risk_penalty': risk_p,
            # 概率
            'p_up_2': round(p_up, 3),
            'p_close_2': round(p_close, 3),
            'p_dd_2': round(p_dd, 3),
            'probability_confidence': conf,
            'sample_size': nd2_d.get('sample_size', 0),
            'expected_alpha': round(expected_alpha, 4),
            'final_alpha': round(final_alpha, 3),
            'rank_score': round(rank_score, 4),
            # 行情
            'pct_chg': q.get('pct_chg', 0),
            'price': q.get('price', 0),
            'market_multiplier': mm,
            # 明细
            'detail': {
                'pattern_detail': pattern_detail,
                'trend': trend_d,
                'pattern_q': pq_d,
                'tailflow': tf_detail,
                'strong_gene': sg_d,
                'theme': ta_d,
                'market': ma_d,
                'nd2': nd2_d,
                'risk': risk_d,
                'bonus': bd,
            },
        }
        return signal
