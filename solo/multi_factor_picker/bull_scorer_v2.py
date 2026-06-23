# -*- coding: utf-8 -*-
"""
BullScore v2 — 新增筹码面 + 估值安全增强 + 主题加成修复 + 非线性放大

依赖 bull_scorer.py 的 BullScorer 基类，在其基础上叠加 3 个新因子 + 1 个增强因子。

评分结构（v2）：
  BullScore_v2 =
    0.18 × IndustryDemandScore    (产业景气 — 再降权2%，区分度仍不足)
    0.15 × TechBarrierScore       (技术壁垒)
    0.15 × OrderExplosionScore    (订单爆发)
    0.15 × EarningsQualityScore   (业绩质量)
    0.08 × LeaderScore            (龙头地位 — 降权2%，与机构信号有重叠)
    0.13 × ExpectationScore       (预期差 — 提权3%，超额收益核心)
    0.05 × InstitutionScore       (机构认可)
    0.05 × MarketCapElasticity    (市值弹性)
    0.07 × ChipScore              (★★★★ 新增 筹码面 — 资金流向+股东数+公募持仓)
    0.07 × SafetyMarginScore      (★★★★ 增强 估值安全 — PEG+质押+解禁+回购+现金流)

  FinalScore = 0.82 × BullScore_v2 + 0.18 × ThemeScore_v2

  主题加成增加非线性放大：FinalScore × (1 + ThemeBonus_v2 × 0.25)
"""
import os
import sys
import time
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger

import tushare as ts


# ════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════

# 主题→关键词映射（用于 fina_mainbz 主营业务匹配）
THEME_KEYWORDS = {
    "AI算力":        ["服务器", "AI芯片", "GPU", "算力", "加速卡", "AI推理", "数据中心"],
    "半导体设备":     ["半导体设备", "晶圆", "刻蚀", "薄膜沉积", "光刻", "检测设备", "CMP"],
    "半导体材料":     ["硅片", "光刻胶", "电子特气", "靶材", "CMP抛光", "封装材料"],
    "PCB":           ["PCB", "印制电路板", "HDI", "IC载板", "软板", "FPC"],
    "光模块":         ["光模块", "光收发", "光器件", "光有源", "光无源", "WDM"],
    "机器人":         ["机器人", "减速器", "伺服", "执行器", "关节模组", "工业机器人"],
    "低空经济":       ["无人机", "eVTOL", "低空", "飞行器", "空管", "通航"],
    "商业航天":       ["航天", "卫星", "火箭", "卫星通信", "北斗", "星链"],
    "创新药":         ["创新药", "生物药", "抗体", "双抗", "ADC", "GLP-1", "CAR-T"],
    "新能源车":       ["新能源车", "锂电池", "动力电池", "电驱", "充电桩", "三电"],
    "军工":           ["军工", "军品", "武器", "雷达", "电子对抗", "导弹"],
    "消费电子":       ["消费电子", "手机", "可穿戴", "VR", "AR", "智能终端"],
    "液冷服务器":     ["液冷", "散热", "温控", "冷却液", "水冷"],
    "存储芯片":       ["存储", "存储器", "NAND", "DRAM", "SSD", "闪存"],
    "数据要素":       ["数据", "大数据", "数据要素", "数据资产", "数据交易"],
    "电力链":         ["电力", "电网", "变压器", "特高压", "智能电网", "虚拟电厂"],
    "氟化工制冷剂":   ["氟化工", "制冷剂", "氟", "含氟", "PVDF", "氟树脂"],
    "化工农药链":     ["农药", "化肥", "磷化工", "煤化工", "石油化工"],
}

# 高景气主题基础分（无数据库时的白名单）
HOT_THEME_BASE = {
    "AI算力": 85, "半导体设备": 82, "半导体材料": 80, "光模块": 78,
    "机器人": 78, "低空经济": 75, "商业航天": 72, "PCB": 72,
    "创新药": 70, "新能源车": 70, "液冷服务器": 75, "存储芯片": 72,
    "数据要素": 68, "锂电": 68, "电力链": 65, "氟化工制冷剂": 65,
    "化工农药链": 60, "消费电子": 60, "军工": 65,
}

# Tushare token来源
TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"


def _get_token():
    """获取Tushare token"""
    token = os.environ.get(TUSHARE_TOKEN_ENV)
    if token:
        return token
    # 从项目 .env 文件读取
    env_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", ".env"),
    ]
    for ep in env_paths:
        ep_abs = os.path.abspath(ep)
        if os.path.exists(ep_abs):
            with open(ep_abs, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        k, v = line.split('=', 1)
                        if k.strip() == TUSHARE_TOKEN_ENV:
                            v = v.strip().strip('"\'')
                            os.environ[TUSHARE_TOKEN_ENV] = v
                            return v
    return None


# ════════════════════════════════════════════════════════
# 新因子评分
# ════════════════════════════════════════════════════════

class ChipScorer:
    """
    筹码面评分 (0~100, 权重7%)

    4个子因子：
    ① 主力资金流向（30%）— moneyflow 近20日大单净流入/流通市值
    ② 股东人数变化（25%）— stk_holdernumber 近3期股东数缩减
    ③ 公募持仓变化（25%）— fund_portfolio 近2期基金持仓变化
    ④ 股东增减持（20%）— stk_holdertrade 近90日净增持/股本
    """
    def __init__(self, pro=None):
        self._pro = None
        self._pro_owned = False
        if pro is not None:
            self._pro = pro
        else:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
                self._pro_owned = True

    def _get_pro(self):
        if self._pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
        return self._pro

    def score_moneyflow(self, ts_code: str, window_days: int = 20) -> Tuple[float, Dict]:
        """① 主力资金流向评分 (0~100)"""
        details = {}
        pro = self._get_pro()
        if pro is None:
            return 50.0, {"error": "no token"}

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=window_days * 1.5)
            mf = pro.moneyflow(
                ts_code=ts_code,
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d')
            )
            if mf is None or len(mf) == 0:
                return 50.0, {"data_count": 0}

            # 累计净流入(万元)
            net_total = mf['net_amount'].sum() / 10000  # 亿元
            mcap = mf.iloc[0].get('total_mv', 0) / 1e8  # 亿元

            # 净流入/市值比例
            if mcap > 0:
                flow_ratio = net_total / mcap * 100
            else:
                flow_ratio = 0

            # 近5日 vs 近20日趋势
            if len(mf) >= 5:
                recent5 = mf.head(5)['net_amount'].sum() / 10000
                earlier = mf.tail(len(mf)-5)['net_amount'].sum() / 10000 if len(mf) > 5 else 0
            else:
                recent5 = net_total
                earlier = 0

            trend = "improving" if recent5 > earlier else "weakening"

            # 评分：净流入比例越高越好，近期流入加速更好
            if flow_ratio > 5:
                base = 90
            elif flow_ratio > 2:
                base = 75
            elif flow_ratio > 0.5:
                base = 60
            elif flow_ratio > -0.5:
                base = 50
            elif flow_ratio > -2:
                base = 35
            elif flow_ratio > -5:
                base = 20
            else:
                base = 10

            # 趋势加分
            if trend == "improving":
                base = min(95, base + 10)

            details['net_inflow_b'] = round(net_total, 2)
            details['flow_pct'] = round(flow_ratio, 2)
            details['trend'] = trend
            details['data_days'] = len(mf)
            return float(base), details

        except Exception as e:
            logger.debug(f"moneyflow {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}

    def score_holder_change(self, ts_code: str) -> Tuple[float, Dict]:
        """② 股东人数变化评分 (0~100)"""
        details = {}
        pro = self._get_pro()
        if pro is None:
            return 50.0, {"error": "no token"}
        try:
            hn = pro.stk_holdernumber(ts_code=ts_code, limit=3)
            if hn is None or len(hn) < 2:
                return 50.0, {"data_count": len(hn) if hn is not None else 0}

            hn = hn.sort_values('end_date')
            current = hn.iloc[-1]['holder_num']
            prev = hn.iloc[0]['holder_num']

            if prev > 0:
                holder_change = (current / prev - 1)  # 负数=股东缩减=利好
            else:
                holder_change = 0

            # 股东数缩减越多越好
            if holder_change < -0.20:
                score = 95  # 大幅缩减>20%
            elif holder_change < -0.10:
                score = 85  # 缩减10-20%
            elif holder_change < -0.05:
                score = 75  # 缩减5-10%
            elif holder_change < -0.02:
                score = 65  # 缩减2-5%
            elif holder_change < 0:
                score = 55  # 微减
            elif holder_change < 0.05:
                score = 45  # 微增
            elif holder_change < 0.10:
                score = 30  # 增5-10%
            else:
                score = 15  # 大幅增加

            details['current'] = int(current)
            details['prev'] = int(prev)
            details['change_pct'] = round(holder_change * 100, 2)
            return float(score), details

        except Exception as e:
            logger.debug(f"stk_holdernumber {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}

    def score_fund_holding(self, ts_code: str) -> Tuple[float, Dict]:
        """③ 公募持仓变化评分 (0~100)"""
        details = {}
        pro = self._get_pro()
        if pro is None:
            return 50.0, {"error": "no token"}
        try:
            # 注意：fund_portfolio 用 fund ts_code，这里用 stock ts_code 查基金持仓
            fp = pro.fund_portfolio(ts_code=ts_code, limit=2)
            if fp is None or len(fp) < 2:
                return 50.0, {"data_count": len(fp) if fp is not None else 0}

            fp = fp.sort_values('end_date')
            # 基金数量变化
            current_funds = fp.iloc[-1]['fund_code'] if isinstance(fp.iloc[-1], dict) else len(fp)
            prev_funds = fp.iloc[0]['fund_code'] if isinstance(fp.iloc[0], dict) else len(fp)

            # 用持仓市值/股本变化衡量
            try:
                cur_amount = fp.iloc[-1].get('amount', 0) or 0
                prev_amount = fp.iloc[0].get('amount', 0) or 0
                if prev_amount > 0:
                    amount_change = (cur_amount / prev_amount - 1)
                else:
                    amount_change = 0

                details['cur_amount'] = float(cur_amount)
                details['prev_amount'] = float(prev_amount)
                details['change_pct'] = round(amount_change * 100, 2)
            except:
                amount_change = 0

            if amount_change > 0.50:
                score = 90
            elif amount_change > 0.20:
                score = 80
            elif amount_change > 0.05:
                score = 70
            elif amount_change > -0.05:
                score = 55
            elif amount_change > -0.20:
                score = 40
            else:
                score = 20

            return float(score), details

        except Exception as e:
            logger.debug(f"fund_portfolio {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}

    def score_holdertrade(self, ts_code: str) -> Tuple[float, Dict]:
        """④ 股东增减持评分 (0~100)"""
        details = {}
        pro = self._get_pro()
        if pro is None:
            return 50.0, {"error": "no token"}
        try:
            ht = pro.stk_holdertrade(ts_code=ts_code, start_date=(datetime.now()-timedelta(90)).strftime('%Y%m%d'))
            if ht is None or len(ht) == 0:
                return 55.0, {"data_count": 0}  # 无数据 = 中性

            net_change = ht['chg_vol'].sum() if 'chg_vol' in ht.columns else ht.iloc[:, -2].sum()

            if net_change > 0:
                score = 80 if net_change > 1000000 else 70
            elif net_change < -1000000:
                score = 15
            elif net_change < 0:
                score = 30
            else:
                score = 50

            details['net_change'] = int(net_change)
            details['trade_count'] = len(ht)
            return float(score), details

        except Exception as e:
            logger.debug(f"stk_holdertrade {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}

    def compute(self, ts_code: str) -> Tuple[float, Dict]:
        """
        综合筹码面评分 (0~100)
        权重: 资金流向 30% + 股东人数 25% + 公募持仓 25% + 增减持 20%
        """
        s1, d1 = self.score_moneyflow(ts_code)
        s2, d2 = self.score_holder_change(ts_code)
        s3, d3 = self.score_fund_holding(ts_code)
        s4, d4 = self.score_holdertrade(ts_code)

        score = 0.30 * s1 + 0.25 * s2 + 0.25 * s3 + 0.20 * s4
        return round(score, 1), {
            "moneyflow": {"score": s1, **d1},
            "holder": {"score": s2, **d2},
            "fund": {"score": s3, **d3},
            "holdertrade": {"score": s4, **d4},
        }


class SafetyScorer:
    """
    估值安全边际评分 (0~100, 权重7%)

    4个子因子：
    ① PEG质量（30%）— 利润增速/PE，PEG<1加分 >2扣分
    ② 质押风险（25%）— pledge_stat 质押比例
    ③ 解禁压力（20%）— share_float 未来60天解禁占比
    ④ 现金流安全（25%）— 经营现金流/营收
    """
    def __init__(self, pro=None):
        self._pro = None
        self._pro_owned = False
        if pro is not None:
            self._pro = pro
        else:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
                self._pro_owned = True

    def _get_pro(self):
        if self._pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
        return self._pro

    def score_peg(self, profit_yoy: float, roe: float) -> Tuple[float, Dict]:
        """① PEG质量评分"""
        details = {}
        if roe <= 0 or profit_yoy <= 0:
            return 40.0, {"reason": "neg_profit_or_roe"}

        # PEG = PE / 利润增速，PE ≈ 1/ROE
        pe = 1.0 / max(roe, 0.01)
        peg = pe / max(profit_yoy * 100, 1.0)

        if peg < 0.3:
            score = 95
        elif peg < 0.6:
            score = 85
        elif peg < 1.0:
            score = 72
        elif peg < 1.5:
            score = 55
        elif peg < 2.0:
            score = 38
        elif peg < 3.0:
            score = 22
        else:
            score = 8

        details['peg'] = round(peg, 2)
        details['pe_proxy'] = round(pe, 2)
        return float(score), details

    def score_pledge(self, ts_code: str) -> Tuple[float, Dict]:
        """② 质押风险评分"""
        details = {}
        pro = self._get_pro()
        if pro is None:
            return 50.0, {"error": "no token"}
        try:
            ps = pro.pledge_stat(ts_code=ts_code)
            if ps is None or len(ps) == 0:
                return 70.0, {"no_data": True}  # 无质押=安全

            total_ratio = 0
            if 'pledge_ratio' in ps.columns:
                total_ratio = ps.iloc[0].get('pledge_ratio', 0) or 0
            elif 'mortgage_ratio' in ps.columns:
                total_ratio = ps.iloc[0].get('mortgage_ratio', 0) or 0

            if total_ratio < 5:
                score = 90
            elif total_ratio < 15:
                score = 75
            elif total_ratio < 30:
                score = 55
            elif total_ratio < 50:
                score = 30
            else:
                score = 10  # >50%质押 = 高危

            details['pledge_ratio'] = round(total_ratio, 1)
            return float(score), details

        except Exception as e:
            logger.debug(f"pledge_stat {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}

    def score_share_float(self, ts_code: str) -> Tuple[float, Dict]:
        """③ 解禁压力评分"""
        details = {}
        pro = self._get_pro()
        if pro is None:
            return 50.0, {"error": "no token"}
        try:
            sf = pro.share_float(ts_code=ts_code)
            if sf is None or len(sf) == 0:
                return 70.0, {"no_data": True}

            # 未来60天解禁总量
            today = datetime.now()
            cutoff = today + timedelta(days=60)
            future = sf[pd.to_datetime(sf['float_date'], errors='coerce') >= today]
            future_60 = future[pd.to_datetime(future['float_date'], errors='coerce') <= cutoff]

            if len(future_60) == 0:
                return 80.0, {"future_60d_count": 0}

            total_ratio = future_60['float_ratio'].sum() if 'float_ratio' in future_60.columns else 0

            if total_ratio < 0.5:
                score = 85
            elif total_ratio < 2:
                score = 70
            elif total_ratio < 5:
                score = 50
            elif total_ratio < 10:
                score = 30
            else:
                score = 10

            details['float_ratio_60d'] = round(total_ratio, 2)
            details['count_60d'] = len(future_60)
            return float(score), details

        except Exception as e:
            logger.debug(f"share_float {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}

    def score_cashflow(self, net_cf: float, revenue: float) -> Tuple[float, Dict]:
        """④ 现金流安全评分"""
        details = {}
        if revenue <= 0:
            return 40.0, {"reason": "zero_revenue"}
        ratio = net_cf / revenue if revenue > 0 else 0

        if ratio > 0.30:
            score = 95
        elif ratio > 0.20:
            score = 85
        elif ratio > 0.10:
            score = 72
        elif ratio > 0.05:
            score = 60
        elif ratio > 0:
            score = 48
        elif ratio > -0.10:
            score = 30
        else:
            score = 10

        details['cf_rev_ratio'] = round(ratio, 3)
        return float(score), details

    def compute(self, ts_code: str, profit_yoy: float, roe: float,
                net_cf: float, revenue: float) -> Tuple[float, Dict]:
        """
        综合估值安全评分 (0~100)
        PEG 30% + 质押 25% + 解禁 20% + 现金流 25%
        """
        s1, d1 = self.score_peg(profit_yoy, roe)
        s2, d2 = self.score_pledge(ts_code)
        s3, d3 = self.score_share_float(ts_code)
        s4, d4 = self.score_cashflow(net_cf, revenue)

        score = 0.30 * s1 + 0.25 * s2 + 0.20 * s3 + 0.25 * s4
        return round(score, 1), {
            "peg": {"score": s1, **d1},
            "pledge": {"score": s2, **d2},
            "float": {"score": s3, **d3},
            "cashflow": {"score": s4, **d4},
        }


# ════════════════════════════════════════════════════════
# 主题加成修复 (ThemeScore_v2)
# ════════════════════════════════════════════════════════

class ThemeScorerV2:
    """
    主题加成评分 v2 — 从 fina_mainbz 主营业务关键词匹配

    替代方案：若 fina_mainbz 无数据，回退到 chain_tag 白名单匹配
    """
    def __init__(self, pro=None):
        self._pro = None
        if pro is not None:
            self._pro = pro
        else:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()

    def score_by_mainbz(self, ts_code: str, period: str = None) -> Tuple[float, str, Dict]:
        """
        通过主营业务构成匹配主题
        Returns: (主题分 0~100, 主题名称, 详情)
        """
        pro = self._pro
        if pro is None:
            return 0.0, "", {"error": "no token"}
        try:
            if period is None:
                period = f"{datetime.now().year}1231"
            mz = pro.fina_mainbz(ts_code=ts_code, period=period)
            if mz is None or len(mz) == 0:
                # 尝试更早的报告期
                period = f"{datetime.now().year - 1}1231"
                mz = pro.fina_mainbz(ts_code=ts_code, period=period)
                if mz is None or len(mz) == 0:
                    return 0.0, "", {"no_data": True}

            bz_items = []
            bz_ratios = {}
            for _, row in mz.iterrows():
                item = str(row.get('bz_item', ''))
                ratio = float(row.get('bz_ratio', 0) or 0)
                bz_items.append(item)
                bz_ratios[item] = ratio

            # 关键词匹配
            match_scores = {}
            for theme, keywords in THEME_KEYWORDS.items():
                score = 0.0
                matched_items = []
                for item, ratio in bz_ratios.items():
                    for kw in keywords:
                        if kw.lower() in item.lower():
                            # 按营收占比加权
                            score += min(ratio or 5, 50)  # 单条上限50%
                            matched_items.append((item, ratio))
                            break
                if score > 0:
                    # 归一化到 0~100
                    theme_base = HOT_THEME_BASE.get(theme, 50)
                    match_intensity = min(score / 30, 1.0)  # 匹配强度
                    # 基础分 + 匹配加成
                    match_scores[theme] = theme_base * (0.5 + 0.5 * match_intensity)

            if not match_scores:
                return 0.0, "", {"no_match": True, "bz_items_sample": bz_items[:3]}

            # 取最高分主题
            best_theme = max(match_scores, key=match_scores.get)
            best_score = match_scores[best_theme]

            details = {
                "theme": best_theme,
                "score": round(best_score, 1),
                "matched_themes": match_scores,
                "bz_items": bz_items[:5],
            }
            return round(best_score, 1), best_theme, details

        except Exception as e:
            logger.debug(f"fina_mainbz {ts_code}: {e}")
            return 0.0, "", {"error": str(e)[:40]}

    def score_fallback(self, chain_tag: str) -> Tuple[float, str]:
        """
        回退方案：chain_tag 白名单匹配
        """
        if not chain_tag or chain_tag == 'nan':
            return 0.0, ""

        # 精确匹配
        if chain_tag in HOT_THEME_BASE:
            return float(HOT_THEME_BASE[chain_tag]), chain_tag

        # 模糊匹配
        for theme, base in HOT_THEME_BASE.items():
            if theme in chain_tag or chain_tag in theme:
                return float(base), theme

        return 0.0, ""


# ════════════════════════════════════════════════════════
# 非线性放大函数
# ════════════════════════════════════════════════════════

def nonlinear_boost(raw_score: float, magnitude: float,
                     baseline: float = 0.5, max_boost: float = 0.30) -> float:
    """
    非线性放大：极端值额外加分

    对 magnitude (如利润增速/100) 做 sigmoid 映射，
    magnitude < baseline: 无加成
    magnitude > baseline: sigmoid 加成，上限 max_boost

    Args:
        raw_score: 原始评分
        magnitude: 极端程度指标
        baseline: 触发放大的基线
        max_boost: 最大加成的百分比
    """
    # sigmoid: 1/(1+e^(-k*(x-b))) ，k控制陡峭度
    k = 3.0  # 陡峭度
    if magnitude <= baseline:
        boost_ratio = 0.0
    else:
        raw_boost = 1.0 / (1.0 + math.exp(-k * (magnitude - baseline)))
        boost_ratio = raw_boost * max_boost

    return min(100.0, raw_score * (1.0 + boost_ratio))


def expectation_nonlinear_boost(profit_yoy: float, base_score: float) -> float:
    """
    预期差非线性放大专用

    profit_yoy > 100% → 额外加成
    profit_yoy > 300% → 显著加成
    """
    if profit_yoy > 5.0:       # >500%
        return base_score * 1.30
    elif profit_yoy > 3.0:     # >300%
        return base_score * 1.20
    elif profit_yoy > 1.5:     # >150%
        return base_score * 1.12
    elif profit_yoy > 0.5:     # >50%
        return base_score * 1.04
    return base_score


# ════════════════════════════════════════════════════════
# BullScore v2 主计算器
# ════════════════════════════════════════════════════════

@dataclass
class BullScoreV2Result:
    """BullScore v2 评分结果"""
    ts_code: str
    name: str
    industry: str
    chain_tag: str = ""

    # 原 BullScorer 的 7 因子 (从 bull_scorer.py 继承)
    industry_demand_score: float = 0.0
    tech_barrier_score: float = 0.0
    order_explosion_score: float = 0.0
    earnings_quality_score: float = 0.0
    leader_score: float = 0.0
    expectation_score: float = 0.0
    institution_score: float = 0.0
    marketcap_score: float = 0.0
    valuation_score: float = 0.0

    # ★★★ 新增因子
    chip_score: float = 0.0          # 筹码面
    safety_score: float = 0.0        # 估值安全(增强版)

    # 汇总
    bull_score_v2: float = 0.0

    # 主题
    theme: str = ""
    theme_score_v2: float = 0.0
    final_score: float = 0.0
    bull_level: str = ""

    # 原始数据
    revenue: float = 0.0
    net_profit: float = 0.0
    roe: float = 0.0
    gross_margin: float = 0.0
    rd_expense_ratio: float = 0.0
    revenue_yoy: float = 0.0
    profit_yoy: float = 0.0
    market_cap: float = 0.0

    # 细节
    sub_details: Dict = field(default_factory=dict)


class BullScorerV2:
    """
    BullScore v2 — 在 v1 基础上叠加筹码面 + 估值安全增强 + 主题加成修复
    """

    def __init__(self, token: str = None):
        self.token = token or _get_token()
        ts.set_token(self.token)
        self.pro = ts.pro_api()

        self.chip_scorer = ChipScorer(self.pro)
        self.safety_scorer = SafetyScorer(self.pro)
        self.theme_scorer = ThemeScorerV2(self.pro)

        # 缓存
        self._chip_cache: Dict[str, Tuple[float, Dict]] = {}
        self._safety_cache: Dict[str, Tuple[float, Dict]] = {}
        self._theme_cache: Dict[str, Tuple[float, str, Dict]] = {}

    def _get_chip_score(self, ts_code: str) -> Tuple[float, Dict]:
        """带缓存的筹码面评分"""
        if ts_code not in self._chip_cache:
            self._chip_cache[ts_code] = self.chip_scorer.compute(ts_code)
        return self._chip_cache[ts_code]

    def _get_safety_score(self, ts_code: str, profit_yoy: float,
                           roe: float, net_cf: float, revenue: float) -> Tuple[float, Dict]:
        """带缓存的估值安全评分"""
        cache_key = f"{ts_code}_{profit_yoy:.2f}_{roe:.2f}"
        if cache_key not in self._safety_cache:
            self._safety_cache[cache_key] = self.safety_scorer.compute(
                ts_code, profit_yoy, roe, net_cf, revenue
            )
        return self._safety_cache[cache_key]

    def _get_theme_score_v2(self, ts_code: str, chain_tag: str) -> Tuple[float, str, Dict]:
        """带缓存的主题加成评分"""
        if ts_code not in self._theme_cache:
            # 方案A: fina_mainbz 主营业务匹配
            score, theme, details = self.theme_scorer.score_by_mainbz(ts_code)
            if score < 1.0:
                # 方案B: 回退 chain_tag 白名单
                score, theme = self.theme_scorer.score_fallback(chain_tag)
                details = {"fallback": True, "chain_tag": chain_tag}
            self._theme_cache[ts_code] = (score, theme, details)
        return self._theme_cache[ts_code]

    def compute_v2(self,
                   base_result: 'BullScoreResult'  # 从 bull_scorer 继承的结果
                   ) -> BullScoreV2Result:
        """
        在原有 BullScore 结果上叠加 v2 新增因子

        原 BullScore 因子权重调整：
          industry_demand 20%→18%
          expectation 15%→13%
          leader 10%→8%
          institution 5%→5% (不变)
          marketcap 5%→5% (不变)

        新增：
          chip_score (筹码面) — 7%
          safety_score (估值安全增强) — 7%

        总分公式：
          BullScore_v2 = 0.18*ind + 0.15*tech + 0.15*order + 0.15*earn_qual
                        + 0.08*leader + 0.13*expect + 0.05*inst
                        + 0.05*mc + 0.07*chip + 0.07*safety

          FinalScore = 0.82 * BullScore_v2 + 0.18 * ThemeScore_v2

          ★★ 如果 ThemeScore_v2 > 60，额外非线性放大：
             FinalScore = FinalScore * (1 + 0.15 * (ThemeScore_v2 - 60) / 40)
        """
        # 1. 筹码面评分
        chip_score, chip_detail = self._get_chip_score(base_result.ts_code)

        # 2. 估值安全增强
        safety_score, safety_detail = self._get_safety_score(
            base_result.ts_code,
            base_result.profit_yoy / 100 if base_result.profit_yoy else 0,
            base_result.roe / 100 if base_result.roe else 0,
            base_result.sub_details.get('earnings_quality', {}).get('cashflow_growth_rank', 0),
            base_result.revenue,
        )

        # 3. 主题加成 v2
        theme_score_v2, theme_name, theme_detail = self._get_theme_score_v2(
            base_result.ts_code, base_result.chain_tag
        )

        # 4. 预期差非线性放大
        profit_yoy = base_result.profit_yoy / 100 if base_result.profit_yoy else 0
        expect_boosted = expectation_nonlinear_boost(
            profit_yoy, base_result.expectation_score
        )

        # 5. BullScore v2 计算
        # 原权重调整版 + 新因子
        ind_w = 0.18
        tech_w = 0.15
        order_w = 0.15
        earn_w = 0.15
        leader_w = 0.08
        expect_w = 0.13
        inst_w = 0.05
        mc_w = 0.05
        chip_w = 0.07
        safety_w = 0.07

        bull_v2 = (
            ind_w * base_result.industry_demand_score +
            tech_w * base_result.tech_barrier_score +
            order_w * base_result.order_explosion_score +
            earn_w * base_result.earnings_quality_score +
            leader_w * base_result.leader_score +
            expect_w * expect_boosted +    # 非线性放大后的预期差
            inst_w * base_result.institution_score +
            mc_w * base_result.marketcap_score +
            chip_w * chip_score +
            safety_w * safety_score
        )

        # 6. 最终分 = 0.82 * BullScore_v2 + 0.18 * ThemeScore_v2
        final = 0.82 * bull_v2 + 0.18 * theme_score_v2

        # 7. 主题非线性放大加成
        if theme_score_v2 > 60:
            bonus = 1.0 + 0.15 * (theme_score_v2 - 60) / 40
            final = final * bonus

        # 等级判定
        level = self._get_level(len([base_result]), 0)  # 找不到排名信息时用分数

        # 构建详情
        sub_details = dict(base_result.sub_details)
        sub_details['chip'] = chip_detail
        sub_details['safety'] = safety_detail
        sub_details['theme_v2'] = theme_detail
        sub_details['expect_boosted'] = round(expect_boosted - base_result.expectation_score, 1)
        sub_details['weights'] = {
            'ind_demand': ind_w, 'tech_barrier': tech_w,
            'order': order_w, 'earnings': earn_w,
            'leader': leader_w, 'expectation': expect_w,
            'institution': inst_w, 'marketcap': mc_w,
            'chip': chip_w, 'safety': safety_w,
        }

        return BullScoreV2Result(
            ts_code=base_result.ts_code,
            name=base_result.name,
            industry=base_result.industry,
            chain_tag=base_result.chain_tag,
            industry_demand_score=base_result.industry_demand_score,
            tech_barrier_score=base_result.tech_barrier_score,
            order_explosion_score=base_result.order_explosion_score,
            earnings_quality_score=base_result.earnings_quality_score,
            leader_score=base_result.leader_score,
            expectation_score=round(expect_boosted, 2),
            institution_score=base_result.institution_score,
            marketcap_score=base_result.marketcap_score,
            valuation_score=base_result.valuation_score,
            chip_score=round(chip_score, 2),
            safety_score=round(safety_score, 2),
            bull_score_v2=round(bull_v2, 2),
            theme=theme_name,
            theme_score_v2=round(theme_score_v2, 2),
            final_score=round(final, 2),
            bull_level=level,
            revenue=base_result.revenue,
            net_profit=base_result.net_profit,
            roe=base_result.roe,
            gross_margin=base_result.gross_margin,
            rd_expense_ratio=base_result.rd_expense_ratio,
            revenue_yoy=base_result.revenue_yoy,
            profit_yoy=base_result.profit_yoy,
            market_cap=base_result.market_cap,
            sub_details=sub_details,
        )

    def _get_level(self, results_len, rank):
        """根据排名确定等级"""
        if rank is not None:
            if rank <= 10:
                return "A级产业龙头"
            elif rank <= 20:
                return "B级成长股"
            else:
                return "观察名单"
        return "未排名"

    def batch_compute(self, base_results: List['BullScoreResult'],
                       batch_size: int = 5, delay: float = 0.3) -> List[BullScoreV2Result]:
        """
        批量计算，控制 Tushare API 调用频率

        Args:
            base_results: 来自 bull_scorer.py 的基础评分结果
            batch_size: 每批并发数
            delay: 每批间隔(秒)，避免限频
        """
        results = []
        total = len(base_results)
        logger.info(f"BullScore v2 开始计算 {total} 只股票...")

        for i in range(0, total, batch_size):
            batch = base_results[i:i+batch_size]
            for br in batch:
                try:
                    r = self.compute_v2(br)
                    results.append(r)
                except Exception as e:
                    logger.debug(f"v2评分失败 {br.ts_code} {br.name}: {e}")
                    # 降级：复制基础评分
                    results.append(BullScoreV2Result(
                        ts_code=br.ts_code, name=br.name, industry=br.industry,
                        chain_tag=br.chain_tag, final_score=br.final_score,
                        bull_score_v2=br.bull_score, bull_level=br.bull_level,
                    ))
            if i + batch_size < total:
                time.sleep(delay)
            if (i // batch_size + 1) % 5 == 0:
                logger.info(f"  v2进度: {min(i+batch_size, total)}/{total}")

        # 排序
        results.sort(key=lambda r: r.final_score, reverse=True)

        # 分配等级
        for idx, r in enumerate(results):
            r.bull_level = self._get_level(len(results), idx + 1)

        logger.info(f"BullScore v2 计算完成: {len(results)} 只")
        return results

    def to_dataframe(self, results: List[BullScoreV2Result]) -> pd.DataFrame:
        """转 DataFrame"""
        rows = []
        for r in results:
            code = r.ts_code.split('.')[0]
            chip_d = r.sub_details.get('chip', {})
            safety_d = r.sub_details.get('safety', {})
            theme_d = r.sub_details.get('theme_v2', {})

            rows.append({
                'code': code, 'name': r.name, 'industry': r.industry,
                'theme': r.theme,
                # 原8因子
                '产业景气': r.industry_demand_score,
                '技术壁垒': r.tech_barrier_score,
                '订单爆发': r.order_explosion_score,
                '业绩质量': r.earnings_quality_score,
                '龙头地位': r.leader_score,
                '预期差': r.expectation_score,
                '机构认可': r.institution_score,
                '市值弹性': r.marketcap_score,
                '估值安全': r.safety_score,
                # ★ 新增
                '筹码面': r.chip_score,
                # 总分
                'Bull_v2分': round(r.bull_score_v2, 1),
                '主题分v2': r.theme_score_v2,
                '最终分': r.final_score,
                '等级': r.bull_level,
                # 关键财务
                '营收同比': r.revenue_yoy,
                '利润同比': r.profit_yoy,
                'ROE': r.roe,
                # 筹码面详情
                '资金流入(亿)': chip_d.get('moneyflow', {}).get('net_inflow_b', ''),
                '股东数变化%': chip_d.get('holder', {}).get('change_pct', ''),
                '公募持仓变化%': chip_d.get('fund', {}).get('change_pct', ''),
                # 安全面详情
                'PEG': safety_d.get('peg', {}).get('peg', ''),
                '质押率%': safety_d.get('pledge', {}).get('pledge_ratio', ''),
                '解禁占比%': safety_d.get('float', {}).get('float_ratio_60d', ''),
                # 主题详情
                '主题匹配方式': 'fina_mainbz' if not theme_d.get('fallback') else 'chain_tag',
            })
        return pd.DataFrame(rows)

    def print_summary(self, results: List[BullScoreV2Result], top_n: int = 50):
        """打印摘要"""
        if not results:
            print("无结果")
            return

        levels = {}
        for r in results:
            levels[r.bull_level] = levels.get(r.bull_level, 0) + 1

        print(f"\n{'='*90}")
        print(f"BullScore v2 中长线牛股选股结果")
        print(f"{'='*90}")
        print(f"扫描范围: {len(results)} 只")
        print(f"\n牛股等级分布:")
        for lv in ["A级产业龙头", "B级成长股", "观察名单", "淘汰"]:
            print(f"  {lv}: {levels.get(lv, 0)}只")

        top = results[:top_n]
        print(f"\nTop {top_n} 龙头股:")
        header = f"{'排名':>4} {'代码':>8} {'名称':<8} {'主题':<10} {'Bull':>6} {'主题分':>6} {'筹码':>6} {'安全':>6} {'最终':>6} {'等级':<12}"
        print(header)
        print("-" * 80)
        for i, r in enumerate(top, 1):
            code = r.ts_code.split('.')[0]
            print(f"{i:>4} {code:>8} {r.name:<8} {r.theme:<10} {r.bull_score_v2:>6.1f} {r.theme_score_v2:>6.1f} {r.chip_score:>6.1f} {r.safety_score:>6.1f} {r.final_score:>6.1f} {r.bull_level:<12}")

        # 新增因子专项排名
        print(f"\nTop 10 筹码面最强:")
        for r in sorted(results, key=lambda x: x.chip_score, reverse=True)[:10]:
            print(f"  {r.name:<8} chip={r.chip_score:.1f}")

        print(f"\nTop 10 估值最安全:")
        for r in sorted(results, key=lambda x: x.safety_score, reverse=True)[:10]:
            print(f"  {r.name:<8} safety={r.safety_score:.1f}")

        print(f"\n差于原BullScore最多的（权重调整后排名变化）:")
        for r in results[:5]:
            code = r.ts_code.split('.')[0]
            print(f"  {r.name:<8} ({code}) → v2: {r.final_score:.1f}")


# ════════════════════════════════════════════════════════
# 独立运行入口
# ════════════════════════════════════════════════════════

def run_v2_pipeline(base_results_path: str = None):
    """
    独立运行 v2 评分流水线

    用法：
      - 方式A: 从已有的 bull_scorer 结果文件（pickle/json）加载
      - 方式B: 直接调用 main.py 的 pipeline 获取 base_results
    """
    from bull_scorer import BullScoreResult  # noqa

    logger.info("BullScore v2 启动...")

    # 获取 token
    token = _get_token()
    if not token:
        logger.error("未找到 Tushare Token!")
        return

    # 初始化 v2
    scorer_v2 = BullScorerV2(token)

    # 测试单只股票
    if base_results_path is None and len(sys.argv) > 1:
        base_results_path = sys.argv[1]

    if not base_results_path:
        # 演示模式：创建示例数据
        logger.info("演示模式：使用示例数据测试")
        from bull_scorer import BullScoreResult  # noqa

        demo = BullScoreResult(
            ts_code="688525.SH", name="佰维存储", industry="半导体",
            chain_tag="AI算力",
            industry_demand_score=95.0, tech_barrier_score=80.0,
            order_explosion_score=88.0, earnings_quality_score=92.0,
            leader_score=79.0, expectation_score=85.0,
            institution_score=70.0, marketcap_score=80.0,
            valuation_score=65.0, bull_score=82.0, theme_score=0.0,
            final_score=65.6, bull_level="观察名单",
            revenue=1e9, net_profit=2e8, roe=22.3,
            gross_margin=35.0, rd_expense_ratio=8.5,
            revenue_yoy=55.0, profit_yoy=135.0,
            market_cap=1.5e10,
            sub_details={'earnings_quality': {}},
        )
        result = scorer_v2.compute_v2(demo)
        print("\n★ 测试结果:")
        df = scorer_v2.to_dataframe([result])
        print(df.T.to_string())
        return

    logger.info(f"加载基础评分结果: {base_results_path}")
    # 实际使用时加载已有的 bull_scorer 结果
    # results_v2 = scorer_v2.batch_compute(base_results)
    # scorer_v2.print_summary(results_v2)


if __name__ == "__main__":
    run_v2_pipeline()
