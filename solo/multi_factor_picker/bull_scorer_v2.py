# -*- coding: utf-8 -*-
"""
BullScore v3.0 — 中长线牛股选股系统（超预期持续成长版）

v3.0 核心变更：
├── 移除 Alpha 因子 (8%) — 与成长/质量/流动性因子高度重叠
├── 预期差权重 12%→14% — 强化超预期增长
├── 业绩质量权重 10%→12% — 强化持续增长+加速度
├── 龙头地位权重 6%→8% — 强化细分产业头部公司
├── 机构认可权重 7%→8% — 多维度机构交叉验证
├── 市值弹性权重 4%→5% — 微调
│
★ 评分理念：从"宽泛多因子渔网"→"超预期驱动的持续成长王者评分"

评分结构（v3.0）：
  核心增长因子 (54%)：产业景气14% | 订单爆发14% | 业绩质量12% | 预期差14%
  护城河因子 (26%)：技术壁垒10% | 龙头地位8% | 机构认可8%
  估值+弹性因子 (12%)：估值安全7% | 市值弹性5%
  历史辨识度 (8%)

  BullScore_v3.0 = 0.14*ind + 0.14*order + 0.10*tech + 0.12*earn
                  + 0.14*expect + 0.08*leader + 0.08*inst + 0.05*mc
                  + 0.07*safety + 0.08*recognition

  FinalScore = 0.88 * BullScore_v3.0 + 0.12 * ThemeScore_v2

历史辨识度评分器 (YRI):
  - 资金活跃度（25%）：日均成交额、换手率、热度排名
  - 涨停基因（25%）：涨停次数、最大连板数、连板频率
  - 空间记忆（20%）：历史新高次数、波动弹性、趋势强度
  - 股性画像（15%）：股性标签、风格特征
  - 舆情热度（15%）：新闻曝光、研报覆盖、市场讨论度

Alpha因子评分器（已弃用v3.0）:
  - 质量因子（20%）：ROE稳定性、盈利质量、现金流
  - 成长因子（20%）：营收增速、利润增速、研发投入
  - 估值因子（15%）：PE/PB分位、估值性价比
  - 动量因子（15%）：价格动量、趋势强度、相对强弱
  - 流动性因子（15%）：成交额、换手率、买卖价差
  - 情绪因子（15%）：资金流向、市场情绪beta

龙头/中军识别逻辑:
  - 龙头股：高辨识度 + 高弹性 + 涨停基因强 + 主题相关性高
  - 中军股：大市值 + 高流动性 + 机构持仓多 + 业绩稳定
  - 输出类型：龙头、中军、龙二、补涨、普通

依赖的 Tushare 8000分及以上接口：
  - pro.daily_basic — 日线基础数据
  - pro.daily — 日线行情
  - pro.moneyflow — 资金流向
  - pro.stk_holdernumber — 股东人数
  - pro.fund_portfolio — 公募基金持仓
  - pro.stk_holdertrade — 股东增减持
  - pro.pledge_stat — 股权质押
  - pro.share_float — 限售股解禁
"""
import os
import sys
import time
import math
import json
import threading
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    "固态电池": 72, "钠离子电池": 65, "氢能源": 68, "CPO": 80,
    "先进封装": 78, "AI应用": 72, "算力基础设施": 75,
}

# 主营产业映射（申万行业 → 主营产业名）
# 主题评分优先使用主营业务（IndustryTheme），概念标签(chain_tag)仅作参考
# 避免"仓储物流→低空经济、水运→低空经济"这类概念炒作误配
INDUSTRY_THEME_MAP = {
    '仓储物流': '物流', '快递': '物流', '物流': '物流',
    '水运': '航运', '航运': '航运', '港口': '航运',
    '轻工机械': '机器人/智能制造', '机械基件': '智能制造', '专用机械': '高端装备',
    '航空': '航空运输', '机场': '航空运输',
    '公路铁路': '交通运输', '铁路': '交通运输', '公路': '交通运输',
    '银行': '银行', '保险': '保险', '证券': '证券', '多元金融': '金融',
    '煤炭开采': '煤炭', '焦炭': '煤炭',
    '石油开采': '石油石化', '石油加工': '石油石化',
    '房地产开发': '房地产', '房地产': '房地产',
    '白酒': '白酒', '啤酒': '食品饮料', '食品': '食品饮料', '软饮料': '食品饮料',
    '家用电器': '家电', '家电': '家电',
    '汽车整车': '汽车', '汽车配件': '汽车零部件', '摩托车': '汽车',
    '化学制药': '医药', '中成药': '医药', '生物制品': '医药', '医疗保健': '医疗器械',
    '医疗器械': '医疗器械',
    '电力': '电力', '火力发电': '电力', '水力发电': '电力', '电力设备': '电力设备',
    '半导体': '半导体', '元器件': '电子元件', '光学光电': '消费电子', 'IT设备': '消费电子',
    '软件服务': '软件', '互联网': '互联网', '通信设备': '通信', '电信运营': '通信',
    '传媒': '传媒', '游戏': '游戏', '影视音像': '传媒',
    '钢铁': '钢铁', '铜': '有色金属', '铝': '有色金属', '铅锌': '有色金属',
    '小金属': '小金属', '能源金属': '能源金属', '黄金': '黄金', '稀土': '稀土永磁',
    '化工原料': '化工', '化学原料': '化工', '农药化肥': '农化', '化纤': '化工', '塑料': '化工',
    '水泥': '建材', '玻璃': '建材', '建材': '建材',
    '纺织': '纺织服装', '服装': '纺织服装',
    '造纸': '轻工制造', '包装印刷': '包装印刷', '家居用品': '家居',
    '船舶': '船舶制造', '航天': '航天军工', '兵器': '军工', '国防军工': '军工',
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
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),  # d:\mystock\solo\.env
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


# 模块级 DataFetcher 单例（统一缓存入口）
_DF_SINGLETON = None


def _get_df():
    """获取 DataFetcher 单例（懒加载，所有评分器共用同一份缓存）"""
    global _DF_SINGLETON
    if _DF_SINGLETON is not None:
        return _DF_SINGLETON
    try:
        from data_fetcher import DataFetcher  # type: ignore
        token = _get_token()
        if not token:
            logger.warning("_get_df: _get_token() 返回 None，无法创建 DataFetcher")
            return None
        config = {
            'cache': {
                'enabled': True,
                'expire_hours': 168,  # 7 天
            },
            'tushare': {'max_retry': 3, 'retry_delay': 5},
        }
        _DF_SINGLETON = DataFetcher(token, config)
    except Exception as e:
        logger.warning(f"_get_df 创建 DataFetcher 失败: {type(e).__name__}: {e}")
        return None
    return _DF_SINGLETON


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
    def __init__(self, pro=None, df=None):
        self._pro = None
        self._pro_owned = False
        self._df = df
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

    def _get_df(self):
        if self._df is None:
            self._df = _get_df()
        return self._df

    def score_moneyflow(self, ts_code: str, window_days: int = 20) -> Tuple[float, Dict]:
        """① 主力资金流向评分 (0~100)"""
        details = {}
        df = self._get_df()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=window_days * 1.5)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        if df is not None:
            try:
                mf = df.get_moneyflow_by_code(ts_code, start_date=start_str, end_date=end_str)
            except Exception:
                mf = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                mf = pro.moneyflow(ts_code=ts_code, start_date=start_str, end_date=end_str)
            except Exception:
                mf = None

        try:
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
        df = self._get_df()
        if df is not None:
            try:
                hn = df.get_stk_holdernumber_raw(ts_code, limit=3)
            except Exception:
                hn = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                hn = pro.stk_holdernumber(ts_code=ts_code, limit=3)
            except Exception:
                hn = None
        try:
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
        df = self._get_df()
        if df is not None:
            try:
                fp = df.get_fund_portfolio_raw(ts_code, limit=2)
            except Exception:
                fp = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                # 注意：fund_portfolio 用 fund ts_code，这里用 stock ts_code 查基金持仓
                fp = pro.fund_portfolio(ts_code=ts_code, limit=2)
            except Exception:
                fp = None
        try:
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
        df = self._get_df()
        start_str = (datetime.now()-timedelta(90)).strftime('%Y%m%d')
        if df is not None:
            try:
                ht = df.get_stk_holdertrade_raw(ts_code, start_date=start_str)
            except Exception:
                ht = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                ht = pro.stk_holdertrade(ts_code=ts_code, start_date=start_str)
            except Exception:
                ht = None
        try:
            if ht is None or len(ht) == 0:
                return 50.0, {"data_count": 0}

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
    def __init__(self, pro=None, df=None):
        self._pro = None
        self._pro_owned = False
        self._df = df
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

    def _get_df(self):
        if self._df is None:
            self._df = _get_df()
        return self._df

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
        df = self._get_df()
        if df is not None:
            try:
                ps = df.get_pledge_stat_raw(ts_code)
            except Exception:
                ps = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                ps = pro.pledge_stat(ts_code=ts_code)
            except Exception:
                ps = None
        try:
            if ps is None or len(ps) == 0:
                return 50.0, {"no_data": True}

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
        df = self._get_df()
        if df is not None:
            try:
                sf = df.get_share_float_raw(ts_code)
            except Exception:
                sf = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                sf = pro.share_float(ts_code=ts_code)
            except Exception:
                sf = None
        try:
            if sf is None or len(sf) == 0:
                return 50.0, {"no_data": True}

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
    def __init__(self, pro=None, df=None):
        self._pro = None
        self._df = df
        if pro is not None:
            self._pro = pro
        else:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()

    def _get_df(self):
        if self._df is None:
            self._df = _get_df()
        return self._df

    def score_by_mainbz(self, ts_code: str, period: str = None) -> Tuple[float, str, Dict]:
        """
        通过主营业务构成匹配主题
        Returns: (主题分 0~100, 主题名称, 详情)
        """
        df = self._get_df()
        if df is None and self._pro is None:
            return 0.0, "", {"error": "no token"}
        try:
            if period is None:
                period = f"{datetime.now().year}1231"
            if df is not None:
                mz = df.get_fina_mainbz_raw(ts_code, period=period)
            else:
                mz = self._pro.fina_mainbz(ts_code=ts_code, period=period)
            if mz is None or len(mz) == 0:
                # 尝试更早的报告期
                period = f"{datetime.now().year - 1}1231"
                if df is not None:
                    mz = df.get_fina_mainbz_raw(ts_code, period=period)
                else:
                    mz = self._pro.fina_mainbz(ts_code=ts_code, period=period)
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

    def score_by_industry(self, industry: str) -> Tuple[float, str]:
        """
        主营产业兜底：用申万行业名直接映射主营产业(IndustryTheme)
        主题只使用主营业务，不使用概念炒作标签。
        例: 仓储物流→物流, 水运→航运, 轻工机械→机器人/智能制造
        """
        if not industry or str(industry) in ('nan', ''):
            return 0.0, ""
        ind = str(industry).strip()
        # 精确匹配
        if ind in INDUSTRY_THEME_MAP:
            theme = INDUSTRY_THEME_MAP[ind]
            return float(HOT_THEME_BASE.get(theme, 55.0)), theme
        # 关键词子串匹配（如 "船舶制造" → "船舶"）
        for ind_kw, theme in INDUSTRY_THEME_MAP.items():
            if ind_kw in ind:
                return float(HOT_THEME_BASE.get(theme, 55.0)), theme
        return 0.0, ""

    def score_fallback(self, chain_tag: str) -> Tuple[float, str]:
        """
        回退方案：chain_tag 白名单匹配
        chain_tag 来自 theme_stock_map_latest.json, 本身就是有效主题名
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

        # chain_tag 来自 JSON 主题映射表,虽不在白名单中但仍是有效主题
        return 55.0, chain_tag


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


# ════════════════════════════════════════════════════════
# 历史辨识度评分器 (YRI - Year Recognition Index)
# ════════════════════════════════════════════════════════

class RecognitionScorer:
    """
    历史辨识度评分器 (0~100, 权重6%)
    
    基于YRI历史辨识度模型，从多个维度评估股票的市场关注度和辨识度：
    
    5个子因子：
    ① 资金活跃度（25%）— 日均成交额、换手率、热度排名
    ② 涨停基因（25%）— 涨停次数、最大连板数、连板频率
    ③ 空间记忆（20%）— 历史新高次数、波动弹性、趋势强度
    ④ 股性画像（15%）— 股性标签、风格特征
    ⑤ 舆情热度（15%）— 新闻曝光、研报覆盖、市场讨论度
    """
    
    def __init__(self, pro=None, df=None):
        self._pro = pro
        self._pro_owned = False
        self._df = df
        if pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
                self._pro_owned = True

        # 缓存
        self._cache: Dict[str, Tuple[float, Dict]] = {}

    def _get_pro(self):
        if self._pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
        return self._pro

    def _get_df(self):
        if self._df is None:
            self._df = _get_df()
        return self._df

    def _score_activity(self, ts_code: str) -> Tuple[float, Dict]:
        """① 资金活跃度评分"""
        details = {}
        df = self._get_df()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        if df is not None:
            try:
                db = df.get_daily_basic_by_code(ts_code, start_date=start_str, end_date=end_str)
            except Exception:
                db = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                db = pro.daily_basic(
                    ts_code=ts_code, start_date=start_str, end_date=end_str,
                    fields='ts_code,trade_date,turnover_rate,volume_ratio,circ_mv',
                )
            except Exception:
                db = None

        try:
            if db is None or len(db) < 10:
                return 50.0, {"data_count": len(db) if db is not None else 0}
            
            avg_turnover = db['turnover_rate'].mean()
            avg_volume_ratio = db['volume_ratio'].mean()
            circ_mv = db.iloc[0].get('circ_mv', 1) / 1e8  # 亿元
            
            # 活跃度评分
            # 换手率越高、量比越高、流通市值适中越好
            turnover_score = min(100, avg_turnover * 5)  # 20%换手率=100分
            volume_score = min(100, avg_volume_ratio * 30)  # 3.3量比=100分
            
            # 市值适中加分（太小流动性差，太大弹性不足）
            if 50 <= circ_mv <= 500:
                cap_score = 100
            elif 20 <= circ_mv < 50 or 500 < circ_mv <= 1000:
                cap_score = 75
            elif circ_mv < 20:
                cap_score = 40
            else:
                cap_score = 50
            
            score = 0.4 * turnover_score + 0.3 * volume_score + 0.3 * cap_score
            
            details['avg_turnover'] = round(avg_turnover, 2)
            details['avg_volume_ratio'] = round(avg_volume_ratio, 2)
            details['circ_mv_b'] = round(circ_mv, 1)
            return float(score), details
        
        except Exception as e:
            logger.debug(f"activity score {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}
    
    def _score_limit_up_history(self, ts_code: str, daily_df: pd.DataFrame = None) -> Tuple[float, Dict]:
        """② 涨停基因评分"""
        details = {}
        fetcher = self._get_df()
        if fetcher is None and self._get_pro() is None:
            return 50.0, {"error": "no token"}

        try:
            if daily_df is None:
                end_date = datetime.now()
                start_date = end_date - timedelta(days=365)
                start_str = start_date.strftime('%Y%m%d')
                end_str = end_date.strftime('%Y%m%d')
                if fetcher is not None:
                    daily_df = fetcher.get_daily_by_code(
                        ts_code, start_date=start_str, end_date=end_str,
                    )
                else:
                    daily_df = self._get_pro().daily(
                        ts_code=ts_code, start_date=start_str, end_date=end_str,
                    )

            if daily_df is None or len(daily_df) == 0:
                return 50.0, {"data_count": 0}

            # 计算涨停次数（涨幅>=9.9%视为涨停）
            daily_df['pct_chg'] = daily_df['pct_chg'].fillna(0)
            limit_up_count = len(daily_df[daily_df['pct_chg'] >= 9.9])

            # 计算连板能力（连续涨停的最大天数）
            max_consecutive = 0
            current_streak = 0
            for pct in daily_df['pct_chg'].values:
                if pct >= 9.9:
                    current_streak += 1
                    max_consecutive = max(max_consecutive, current_streak)
                else:
                    current_streak = 0

            trading_days = len(daily_df)
            
            # 评分
            # 涨停频率
            freq_score = min(100, (limit_up_count / trading_days) * 500)  # 20%涨停率=100分
            # 连板能力
            streak_score = min(100, max_consecutive * 25)  # 4连板=100分
            
            score = 0.6 * freq_score + 0.4 * streak_score
            
            details['limit_up_count'] = limit_up_count
            details['max_consecutive_zt'] = max_consecutive
            details['freq_pct'] = round(limit_up_count / trading_days * 100, 2)
            return float(score), details
        
        except Exception as e:
            logger.debug(f"limit_up history {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}
    
    def _score_price_momentum(self, ts_code: str, daily_df: pd.DataFrame = None) -> Tuple[float, Dict]:
        """③ 空间记忆评分 — 新高能力和趋势强度"""
        details = {}
        fetcher = self._get_df()
        if fetcher is None and self._get_pro() is None:
            return 50.0, {"error": "no token"}

        try:
            if daily_df is None:
                end_date = datetime.now()
                start_date = end_date - timedelta(days=180)
                start_str = start_date.strftime('%Y%m%d')
                end_str = end_date.strftime('%Y%m%d')
                if fetcher is not None:
                    daily_df = fetcher.get_daily_by_code(
                        ts_code, start_date=start_str, end_date=end_str,
                    )
                else:
                    daily_df = self._get_pro().daily(
                        ts_code=ts_code, start_date=start_str, end_date=end_str,
                        fields='ts_code,trade_date,high,close',
                    )

            if daily_df is None or len(daily_df) < 60:
                return 50.0, {"data_count": len(daily_df) if daily_df is not None else 0}

            daily_df = daily_df.sort_values('trade_date')

            # 统计创新高次数
            highs = daily_df['high'].values
            new_high_count = 0
            running_high = highs[0]
            for h in highs[1:]:
                if h > running_high:
                    new_high_count += 1
                    running_high = h

            # 计算波动率（弹性）
            returns = daily_df['close'].pct_change().dropna()
            volatility = returns.std() * math.sqrt(252)  # 年化波动率

            # 计算趋势强度（近60日收益）
            if len(daily_df) >= 60:
                trend_return = (daily_df['close'].iloc[-1] / daily_df['close'].iloc[-60] - 1) * 100
            else:
                trend_return = 0
            
            # 评分
            high_score = min(100, new_high_count * 10)  # 10次新高=100分
            vol_score = min(100, volatility * 200)  # 50%波动率=100分
            trend_score = min(100, max(-100, trend_return) + 100)  # 归一化
            
            score = 0.4 * high_score + 0.3 * vol_score + 0.3 * trend_score
            
            details['new_high_count'] = new_high_count
            details['volatility'] = round(volatility, 3)
            details['trend_return_60d'] = round(trend_return, 2)
            return float(score), details
        
        except Exception as e:
            logger.debug(f"momentum score {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}
    
    def _score_stock_personality(self, market_cap: float, industry: str) -> Tuple[float, Dict]:
        """④ 股性画像评分 — 根据市值和行业判断股性特征"""
        details = {}
        
        # 根据市值和行业推断股性
        personality_tags = []
        
        # 市值维度
        if market_cap < 50e8:  # 50亿以下
            personality_tags.append("小盘股")
            personality_tags.append("高弹性")
        elif market_cap < 200e8:  # 200亿以下
            personality_tags.append("中盘股")
            personality_tags.append("均衡型")
        elif market_cap < 1000e8:  # 1000亿以下
            personality_tags.append("大盘股")
            personality_tags.append("稳健型")
        else:
            personality_tags.append("权重股")
            personality_tags.append("防御型")
        
        # 行业维度
        aggressive_industries = ["半导体", "AI", "算力", "机器人", "创新药", "新能源"]
        stable_industries = ["银行", "保险", "地产", "公用事业", "消费"]
        
        if any(ind in industry for ind in aggressive_industries):
            personality_tags.append("成长风格")
        elif any(ind in industry for ind in stable_industries):
            personality_tags.append("价值风格")
        else:
            personality_tags.append("均衡风格")
        
        # 根据股性标签评分
        score = 60  # 基础分
        
        if "高弹性" in personality_tags:
            score += 15
        if "成长风格" in personality_tags:
            score += 10
        if "稳健型" in personality_tags:
            score += 5
        
        # 小盘股额外加分（更容易成为龙头）
        if "小盘股" in personality_tags:
            score += 10
        
        score = min(100, score)
        
        details['personality_tags'] = personality_tags
        return float(score), details
    
    def compute(self, ts_code: str, market_cap: float = 0, industry: str = "") -> Tuple[float, Dict]:
        """
        综合历史辨识度评分 (0~100)
        权重: 资金活跃度 30% + 涨停基因 30% + 空间记忆 20% + 股性画像 20%
        """
        cache_key = f"{ts_code}_{market_cap:.0f}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # 预拉取日线数据（一次拉取365天，共享给涨停基因+空间记忆两个子评分）
        _shared_daily = None
        fetcher = self._get_df()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')
        if fetcher is not None:
            try:
                _shared_daily = fetcher.get_daily_by_code(
                    ts_code, start_date=start_str, end_date=end_str,
                )
            except Exception:
                pass
        else:
            # V2: 优先 daily_cache 表
            try:
                from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                _, max_date = get_daily_cache_range(ts_code)
                if max_date is not None and str(max_date) >= str(end_str):
                    cached = get_daily_cache(ts_code, start_str, end_str)
                    if cached is not None and not cached.empty:
                        cached['trade_date'] = cached['trade_date'].astype(str)
                        _shared_daily = cached
            except Exception:
                pass
            if _shared_daily is None:
                pro = self._get_pro()
                if pro is not None:
                    try:
                        _shared_daily = pro.daily(
                            ts_code=ts_code,
                            start_date=start_str,
                            end_date=end_str,
                            fields='ts_code,trade_date,high,close,pct_chg',
                        )
                        if _shared_daily is not None and not _shared_daily.empty:
                            try:
                                batch_insert_daily_cache(_shared_daily)
                            except Exception:
                                pass
                    except Exception:
                        pass

        s1, d1 = self._score_activity(ts_code)
        s2, d2 = self._score_limit_up_history(ts_code, daily_df=_shared_daily)
        s3, d3 = self._score_price_momentum(ts_code, daily_df=_shared_daily)
        s4, d4 = self._score_stock_personality(market_cap, industry)
        
        score = 0.30 * s1 + 0.30 * s2 + 0.20 * s3 + 0.20 * s4
        
        result = (round(score, 1), {
            "activity": {"score": s1, **d1},
            "limit_up": {"score": s2, **d2},
            "momentum": {"score": s3, **d3},
            "personality": {"score": s4, **d4},
        })
        
        self._cache[cache_key] = result
        return result


# ════════════════════════════════════════════════════════
# Alpha因子评分器
# ════════════════════════════════════════════════════════

class AlphaScorer:
    """
    Alpha因子评分器 (0~100, 权重6%)
    
    基于多因子模型评估股票的超额收益潜力：
    
    6个子因子：
    ① 质量因子（20%）— ROE稳定性、盈利质量、现金流
    ② 成长因子（20%）— 营收增速、利润增速、一致性
    ③ 估值因子（15%）— PE/PB分位、估值性价比
    ④ 动量因子（15%）— 价格动量、趋势强度
    ⑤ 流动性因子（15%）— 成交额、换手率、买卖价差
    ⑥ 情绪因子（15%）— 资金流向、市场情绪beta
    """
    
    def __init__(self, pro=None, df=None):
        self._pro = pro
        self._pro_owned = False
        self._df = df
        if pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
                self._pro_owned = True

        # 缓存
        self._cache: Dict[str, Tuple[float, Dict]] = {}

    def _get_pro(self):
        if self._pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
        return self._pro

    def _get_df(self):
        if self._df is None:
            self._df = _get_df()
        return self._df

    def _score_quality(self, roe: float, profit_yoy: float, cash_flow_ratio: float) -> Tuple[float, Dict]:
        """① 质量因子评分"""
        details = {}
        
        # ROE评分
        if roe >= 25:
            roe_score = 100
        elif roe >= 15:
            roe_score = 80
        elif roe >= 10:
            roe_score = 60
        elif roe >= 5:
            roe_score = 40
        else:
            roe_score = 20
        
        # 盈利稳定性评分（基于利润增速绝对值）
        if abs(profit_yoy) < 30:
            stability_score = 80
        elif abs(profit_yoy) < 60:
            stability_score = 60
        else:
            stability_score = 40
        
        # 现金流评分
        if cash_flow_ratio >= 0.2:
            cf_score = 100
        elif cash_flow_ratio >= 0.1:
            cf_score = 75
        elif cash_flow_ratio >= 0:
            cf_score = 50
        else:
            cf_score = 25
        
        score = 0.5 * roe_score + 0.3 * stability_score + 0.2 * cf_score
        
        details['roe'] = roe
        details['profit_yoy'] = profit_yoy
        details['cash_flow_ratio'] = cash_flow_ratio
        return float(score), details
    
    def _score_growth(self, revenue_yoy: float, profit_yoy: float, rd_ratio: float) -> Tuple[float, Dict]:
        """② 成长因子评分"""
        details = {}
        
        # 营收增长评分
        if revenue_yoy >= 50:
            rev_score = 100
        elif revenue_yoy >= 30:
            rev_score = 80
        elif revenue_yoy >= 15:
            rev_score = 60
        elif revenue_yoy >= 0:
            rev_score = 40
        else:
            rev_score = 20
        
        # 利润增长评分
        if profit_yoy >= 100:
            profit_score = 100
        elif profit_yoy >= 50:
            profit_score = 80
        elif profit_yoy >= 20:
            profit_score = 60
        elif profit_yoy >= 0:
            profit_score = 40
        else:
            profit_score = 20
        
        # 研发投入评分
        if rd_ratio >= 15:
            rd_score = 100
        elif rd_ratio >= 10:
            rd_score = 80
        elif rd_ratio >= 5:
            rd_score = 60
        elif rd_ratio >= 2:
            rd_score = 40
        else:
            rd_score = 20
        
        score = 0.4 * rev_score + 0.4 * profit_score + 0.2 * rd_score
        
        details['revenue_yoy'] = revenue_yoy
        details['profit_yoy'] = profit_yoy
        details['rd_ratio'] = rd_ratio
        return float(score), details
    
    def _score_valuation(self, pe: float, pb: float, industry: str = "") -> Tuple[float, Dict]:
        """③ 估值因子评分"""
        details = {}
        
        # 不同行业估值基准不同
        pe_baselines = {
            "半导体": 50, "AI": 60, "创新药": 45, "新能源": 35,
            "消费": 25, "金融": 15, "公用事业": 20, "周期": 18
        }
        
        baseline_pe = pe_baselines.get(industry, 30)
        
        # PE评分（适中为好）
        if pe > 0:
            if pe < baseline_pe * 0.5:
                pe_score = 80  # 低估
            elif pe < baseline_pe * 1.2:
                pe_score = 60  # 合理
            elif pe < baseline_pe * 2:
                pe_score = 40  # 偏高
            else:
                pe_score = 20  # 高估
        else:
            pe_score = 50
        
        # PB评分
        if pb > 0:
            if pb < 2:
                pb_score = 80
            elif pb < 5:
                pb_score = 60
            elif pb < 10:
                pb_score = 40
            else:
                pb_score = 20
        else:
            pb_score = 50
        
        score = 0.6 * pe_score + 0.4 * pb_score
        
        details['pe'] = pe
        details['pb'] = pb
        details['baseline_pe'] = baseline_pe
        return float(score), details
    
    def _score_momentum(self, ts_code: str) -> Tuple[float, Dict]:
        """④ 动量因子评分"""
        details = {}
        fetcher = self._get_df()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=60)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        if fetcher is not None:
            try:
                df = fetcher.get_daily_by_code(
                    ts_code, start_date=start_str, end_date=end_str,
                )
            except Exception:
                df = None
        else:
            # V2: 优先 daily_cache 表
            try:
                from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                _, max_date = get_daily_cache_range(ts_code)
                if max_date is not None and str(max_date) >= str(end_str):
                    cached = get_daily_cache(ts_code, start_str, end_str)
                    if cached is not None and not cached.empty:
                        cached['trade_date'] = cached['trade_date'].astype(str)
                        df = cached
            except Exception:
                pass
            if df is None:
                pro = self._get_pro()
                if pro is None:
                    return 50.0, {"error": "no token"}
                try:
                    df = pro.daily(
                        ts_code=ts_code, start_date=start_str, end_date=end_str,
                        fields='ts_code,trade_date,close',
                    )
                    if df is not None and not df.empty:
                        try:
                            batch_insert_daily_cache(df)
                        except Exception:
                            pass
                except Exception:
                    df = None

        try:
            if df is None or len(df) < 20:
                return 50.0, {"data_count": len(df) if df is not None else 0}

            df = df.sort_values('trade_date')

            # 计算60日收益
            ret_60d = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100

            # 计算相对强弱（RS）
            if len(df) >= 20:
                ret_20d = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
                ret_60d_excl = (df['close'].iloc[-20] / df['close'].iloc[0] - 1) * 100
                rs_ratio = ret_20d / (ret_60d_excl + 0.01) if ret_60d_excl != 0 else 1
            else:
                rs_ratio = 1
            
            # 动量评分
            if ret_60d >= 30:
                ret_score = 100
            elif ret_60d >= 15:
                ret_score = 80
            elif ret_60d >= 5:
                ret_score = 60
            elif ret_60d >= -5:
                ret_score = 40
            else:
                ret_score = 20
            
            # RS评分（近期相对强势加分）
            if rs_ratio >= 1.5:
                rs_score = 100
            elif rs_ratio >= 1.2:
                rs_score = 80
            elif rs_ratio >= 1.0:
                rs_score = 60
            else:
                rs_score = 40
            
            score = 0.6 * ret_score + 0.4 * rs_score
            
            details['ret_60d'] = round(ret_60d, 2)
            details['rs_ratio'] = round(rs_ratio, 2)
            return float(score), details
        
        except Exception as e:
            logger.debug(f"momentum alpha {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}
    
    def _score_liquidity(self, ts_code: str) -> Tuple[float, Dict]:
        """⑤ 流动性因子评分"""
        details = {}
        fetcher = self._get_df()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=20)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        if fetcher is not None:
            try:
                df = fetcher.get_daily_by_code(
                    ts_code, start_date=start_str, end_date=end_str,
                )
            except Exception:
                df = None
        else:
            # V2: 优先 daily_cache 表
            try:
                from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                _, max_date = get_daily_cache_range(ts_code)
                if max_date is not None and str(max_date) >= str(end_str):
                    cached = get_daily_cache(ts_code, start_str, end_str)
                    if cached is not None and not cached.empty:
                        cached['trade_date'] = cached['trade_date'].astype(str)
                        df = cached
            except Exception:
                pass
            if df is None:
                pro = self._get_pro()
                if pro is None:
                    return 50.0, {"error": "no token"}
                try:
                    df = pro.daily(
                        ts_code=ts_code, start_date=start_str, end_date=end_str,
                        fields='ts_code,trade_date,amount,vol,close',
                    )
                    if df is not None and not df.empty:
                        try:
                            batch_insert_daily_cache(df)
                        except Exception:
                            pass
                except Exception:
                    df = None

        try:
            if df is None or len(df) < 10:
                return 50.0, {"data_count": len(df) if df is not None else 0}

            # 日均成交额（亿元）
            avg_amount = df['amount'].mean() / 1e8

            # 日均换手率
            if fetcher is not None:
                try:
                    turnover_rate = fetcher.get_daily_basic_by_code(
                        ts_code, start_date=start_str, end_date=end_str,
                    )
                except Exception:
                    turnover_rate = None
            else:
                turnover_rate = self._get_pro().daily_basic(
                    ts_code=ts_code, start_date=start_str, end_date=end_str,
                    fields='turnover_rate',
                )

            if turnover_rate is not None and len(turnover_rate) > 0:
                avg_turnover = turnover_rate['turnover_rate'].mean()
            else:
                avg_turnover = 0
            
            # 流动性评分
            if avg_amount >= 5:
                amount_score = 100
            elif avg_amount >= 2:
                amount_score = 80
            elif avg_amount >= 0.5:
                amount_score = 60
            elif avg_amount >= 0.1:
                amount_score = 40
            else:
                amount_score = 20
            
            if avg_turnover >= 8:
                turnover_score = 100
            elif avg_turnover >= 4:
                turnover_score = 80
            elif avg_turnover >= 2:
                turnover_score = 60
            elif avg_turnover >= 0.5:
                turnover_score = 40
            else:
                turnover_score = 20
            
            score = 0.6 * amount_score + 0.4 * turnover_score
            
            details['avg_amount_b'] = round(avg_amount, 2)
            details['avg_turnover'] = round(avg_turnover, 2)
            return float(score), details
        
        except Exception as e:
            logger.debug(f"liquidity alpha {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}
    
    def _score_sentiment(self, ts_code: str) -> Tuple[float, Dict]:
        """⑥ 情绪因子评分"""
        details = {}
        fetcher = self._get_df()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=20)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        if fetcher is not None:
            try:
                mf = fetcher.get_moneyflow_by_code(ts_code, start_date=start_str, end_date=end_str)
            except Exception:
                mf = None
        else:
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no token"}
            try:
                mf = pro.moneyflow(ts_code=ts_code, start_date=start_str, end_date=end_str)
            except Exception:
                mf = None

        try:
            if mf is None or len(mf) == 0:
                return 50.0, {"data_count": 0}
            
            # 计算累计净流入/流通市值比例
            net_inflow = mf['net_amount'].sum() / 10000  # 亿元
            mcap = mf.iloc[0].get('total_mv', 1) / 1e8  # 亿元
            
            if mcap > 0:
                flow_ratio = net_inflow / mcap * 100
            else:
                flow_ratio = 0
            
            # 情绪评分
            if flow_ratio >= 5:
                score = 100
            elif flow_ratio >= 2:
                score = 80
            elif flow_ratio >= 0:
                score = 60
            elif flow_ratio >= -2:
                score = 40
            else:
                score = 20
            
            details['net_inflow_b'] = round(net_inflow, 2)
            details['flow_ratio'] = round(flow_ratio, 2)
            return float(score), details
        
        except Exception as e:
            logger.debug(f"sentiment alpha {ts_code}: {e}")
            return 50.0, {"error": str(e)[:40]}
    
    def compute(self, ts_code: str, roe: float = 0, profit_yoy: float = 0,
                revenue_yoy: float = 0, rd_ratio: float = 0, pe: float = 0,
                pb: float = 0, cash_flow_ratio: float = 0,
                industry: str = "", market_cap: float = 0) -> Tuple[float, Dict]:
        """
        综合Alpha因子评分 (0~100)
        权重: 质量因子 20% + 成长因子 20% + 估值因子 15% 
              + 动量因子 15% + 流动性因子 15% + 情绪因子 15%
        """
        cache_key = f"{ts_code}_{roe:.1f}_{profit_yoy:.0f}_{pe:.0f}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        s1, d1 = self._score_quality(roe, profit_yoy, cash_flow_ratio)
        s2, d2 = self._score_growth(revenue_yoy, profit_yoy, rd_ratio)
        s3, d3 = self._score_valuation(pe, pb, industry)
        s4, d4 = self._score_momentum(ts_code)
        s5, d5 = self._score_liquidity(ts_code)
        s6, d6 = self._score_sentiment(ts_code)
        
        score = 0.20 * s1 + 0.20 * s2 + 0.15 * s3 + 0.15 * s4 + 0.15 * s5 + 0.15 * s6
        
        result = (round(score, 1), {
            "quality": {"score": s1, **d1},
            "growth": {"score": s2, **d2},
            "valuation": {"score": s3, **d3},
            "momentum": {"score": s4, **d4},
            "liquidity": {"score": s5, **d5},
            "sentiment": {"score": s6, **d6},
        })
        
        self._cache[cache_key] = result
        return result


# ════════════════════════════════════════════════════════
# 龙头/中军识别器
# ════════════════════════════════════════════════════════

class LeaderRecognizer:
    """
    龙头/中军识别器
    
    识别逻辑：
    - 龙头股：高辨识度 + 高弹性 + 涨停基因强 + 主题相关性高
    - 中军股：大市值 + 高流动性 + 机构持仓多 + 业绩稳定
    
    输出：
    - leader_type: "龙头" / "中军" / "龙二" / "补涨" / "普通"
    - leader_score: 龙头评分 (0~100)
    - central_score: 中军评分 (0~100)
    """
    
    def __init__(self, pro=None, df=None):
        self._pro = pro
        self._df = df
        if pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()

        # 缓存
        self._cache: Dict[str, Dict] = {}
    
    @staticmethod
    def _leader_cap_score(market_cap_b: float) -> float:
        """龙头市值评分：50~300亿最佳区间"""
        if 50 <= market_cap_b <= 300:
            return 100
        elif 300 < market_cap_b <= 500:
            return 80
        elif 20 <= market_cap_b < 50:
            return 70
        else:
            return 50

    def _get_pro(self):
        if self._pro is None:
            token = _get_token()
            if token:
                ts.set_token(token)
                self._pro = ts.pro_api()
        return self._pro
    
    def recognize(self, ts_code: str, market_cap: float, industry: str,
                  recognition_score: float, alpha_score: float,
                  theme_score: float, chip_score: float,
                  max_consecutive_zt: int = 0) -> Dict:
        """
        识别股票类型并评分
        
        Args:
            ts_code: 股票代码
            market_cap: 市值（元）
            industry: 行业
            recognition_score: 辨识度评分
            alpha_score: (v3.0已弃用，传0，权重重分配给recognition/theme/chip)
            theme_score: 主题评分
            chip_score: 筹码面评分
            max_consecutive_zt: 近365日最大连板数（硬门槛：≥3板才能认定为龙头）
        
        Returns:
            Dict with: leader_type, leader_score, central_score, features
        """
        cache_key = ts_code
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        market_cap_b = market_cap / 1e8  # 转换为亿元
        
        # 龙头评分因子（v3.0：alpha已移除，权重重分配）
        # 1. 辨识度（权重35%）
        # 2. 主题匹配（权重20%）
        # 3. 筹码面（权重15%）
        # 4. 市值适中（权重30%）- 太小不够分量，太大弹性不足
        
        leader_score = (
            0.35 * recognition_score +
            0.20 * min(theme_score, 100) +
            0.15 * chip_score +
            0.30 * self._leader_cap_score(market_cap_b)
        )
        
        # 中军评分因子（v3.0：alpha移除，权重重分配）
        # 1. 市值规模（权重40%）- 越大越好
        # 2. 流动性/筹码面（权重35%）- 通过chip_score反映
        # 3. 辨识度/稳定性（权重25%）
        
        # 市值越大越可能是中军
        if market_cap_b >= 500:
            cap_score = 100
        elif market_cap_b >= 300:
            cap_score = 80
        elif market_cap_b >= 150:
            cap_score = 60
        else:
            cap_score = 30
        
        central_score = (
            0.40 * cap_score +
            0.35 * chip_score +
            0.25 * recognition_score
        )
        
        # 判定类型
        # 硬约束：龙头判定必须满足最大连板≥3板（无论评分多高）
        # 连板<3板的高分高市值股认定为中军，不认定为龙头
        features = []
        leader_type = "普通"
        leader_locked = False  # 是否已锁定为龙头类
        
        if leader_score >= 75 and max_consecutive_zt >= 3:
            leader_type = "龙头"
            leader_locked = True
            features.append("高辨识度龙头")
            features.append(f"最大连板{max_consecutive_zt}板")
        elif leader_score >= 75 and max_consecutive_zt < 3:
            # 评分够但连板不足：降级为中军候选
            features.append(f"连板{max_consecutive_zt}板不足3板，未达龙头门槛")
        elif leader_score >= 65 and max_consecutive_zt >= 3:
            leader_type = "龙二"
            leader_locked = True
            features.append("强势股")
            features.append(f"最大连板{max_consecutive_zt}板")
        elif leader_score >= 55 and max_consecutive_zt >= 2:
            leader_type = "补涨"
            leader_locked = True
            features.append("活跃股")
        
        if central_score >= 65:
            if not leader_locked:
                if leader_type == "普通":
                    leader_type = "中军"
                    features.append("中军股")
                else:
                    features.append("兼具中军特征")
            else:
                features.append("兼具龙头与中军特征")
        elif central_score >= 55:
            features.append("准中军")
        
        # 添加特征描述
        if recognition_score >= 80:
            features.append("高辨识度")
        if theme_score >= 80:
            features.append("主题纯正")
        if chip_score >= 80:
            features.append("筹码健康")
        
        result = {
            "leader_type": leader_type,
            "leader_score": round(leader_score, 1),
            "central_score": round(central_score, 1),
            "features": features,
            "market_cap_b": round(market_cap_b, 1),
            "max_consecutive_zt": max_consecutive_zt,
        }
        
        self._cache[cache_key] = result
        return result


# ════════════════════════════════════════════════════════
# 原有非线性放大函数继续
# ════════════════════════════════════════════════════════

def expectation_nonlinear_boost(profit_yoy: float, base_score: float,
                                 revenue_yoy: float = 0.0,
                                 growth_trend: str = 'stable') -> float:
    """
    预期差非线性放大专用 — v3优化：增长趋势校验

    v3新增：增长趋势信号
    - growth_trend='falling': Q1营收增速 < 年报的50%，高增长可持续性存疑，额外降权
    - growth_trend='rising': Q1营收增速 > 年报，增长在加速，可信度提升
    
    v2优化：利润高增长必须有营收增长支撑，否则降低放大倍数
    - 利润增速 > 200% 但营收 < 50%：可信度 0.5，放大系数减半
    - 利润增速 > 100% 但营收 < 30%：可信度 0.7
    - 利润增速 > 50% 但营收 < 15%：可信度 0.85
    """
    # v2：低基数可信度校验
    credibility = 1.0
    if profit_yoy > 2.0 and revenue_yoy < 0.5:
        credibility = 0.5
    elif profit_yoy > 1.0 and revenue_yoy < 0.3:
        credibility = 0.7
    elif profit_yoy > 0.5 and revenue_yoy < 0.15:
        credibility = 0.85

    # v3：增长趋势额外校验
    trend_penalty = 1.0
    if growth_trend == 'falling':
        trend_penalty = 0.7  # 增长趋势衰减，额外降权30%
    elif growth_trend == 'rising':
        trend_penalty = 1.05  # 增长加速，轻微提升5%

    adjusted_yoy = profit_yoy * credibility * trend_penalty

    if adjusted_yoy > 5.0:       # >500%
        return base_score * 1.30
    elif adjusted_yoy > 3.0:     # >300%
        return base_score * 1.20
    elif adjusted_yoy > 1.5:     # >150%
        return base_score * 1.12
    elif adjusted_yoy > 0.5:     # >50%
        return base_score * 1.04
    return base_score


# ════════════════════════════════════════════════════════
# BullScore v2 主计算器
# ════════════════════════════════════════════════════════

@dataclass
class BullScoreV2Result:
    """BullScore v2.1 评分结果"""
    ts_code: str
    name: str
    industry: str
    chain_tag: str = ""

    # 原 BullScorer 的 8 因子 (从 bull_scorer.py 继承)
    industry_demand_score: float = 0.0
    tech_barrier_score: float = 0.0
    order_explosion_score: float = 0.0
    earnings_quality_score: float = 0.0
    leader_score: float = 0.0
    expectation_score: float = 0.0
    institution_score: float = 0.0
    marketcap_score: float = 0.0
    valuation_score: float = 0.0

    # ★★★ v2 新增因子
    chip_score: float = 0.0          # 筹码面
    safety_score: float = 0.0        # 估值安全(增强版)
    
    # ★★★ v2.1 新增因子
    recognition_score: float = 0.0   # 历史辨识度评分 (YRI)
    # alpha_score字段保留但v3.0已弃用（Alpha因子与已有因子高度重叠）
    leader_type: str = ""            # 龙头类型: 龙头/中军/龙二/补涨/普通
    alpha_score: float = 0.0         # 保留字段(已弃用, v3.0不再使用)
    leader_features: List[str] = field(default_factory=list)  # 特征标签
    # v3.2 新增: 从 v1 层透传的业绩超预期 + 波段属性评分
    earnings_surprise_score: float = 0.0   # 业绩超预期(预告vs卖方预期偏离)
    swing_quality_score: float = 0.0       # 波段属性(适合反复波段操作)
    forecast_profit_change: float = 0.0       # 预告净利润变动幅度(%)
    forecast_vs_analyst_gap: float = 0.0       # 预告vs卖方预期偏离(百分点)
    forecast_ann_date: str = ""                # 预告公告日期
    quarterly_net_profit: float = 0.0          # 季度净利润
    quarterly_net_profit_prev: float = 0.0     # 上年同期季度净利润
    sequential_qoq_growth: float = 0.0         # 环比增速(最新季度 vs 上一季度)
    # v3.3 估值空间(从 v1 层透传) → v4.0 成长兑现模型
    fair_value: float = 0.0            # 基准估值(亿元)
    optimistic_value: float = 0.0      # 乐观估值(亿元)
    valuation_space: float = 0.0      # 期望估值空间(%)
    fair_pe: float = 0.0               # 合理PE
    pe_ttm: float = 0.0                # 当前PE_TTM
    pb: float = 0.0                    # 当前PB
    close_price: float = 0.0           # 当前收盘价(元)
    fair_price: float = 0.0            # 基准目标价(元) — 兼容旧字段
    optimistic_price: float = 0.0      # 乐观目标价(元) — 兼容旧字段
    # v4.0 Bear/Base/Bull 三档目标价 + 概率分布
    bear_pe: float = 0.0               # 悲观PE
    bull_pe: float = 0.0               # 乐观PE
    bear_price: float = 0.0            # 悲观目标价(元)
    base_price: float = 0.0            # 基准目标价(元)
    bull_price: float = 0.0            # 乐观目标价(元)
    bear_prob: int = 25                # 悲观概率(%)
    base_prob: int = 50                # 基准概率(%)
    bull_prob: int = 25                # 乐观概率(%)

    # 汇总
    bull_score_v2: float = 0.0

    # 主题
    theme: str = ""
    theme_score_v2: float = 0.0
    industry_theme: str = ""  # 主营产业(IndustryTheme, 来自申万行业映射)
    concept_theme: str = ""   # 概念主题(ConceptTheme, 来自chain_tag, 仅作轮动参考)
    final_score: float = 0.0
    bull_level: str = ""

    # 原始数据
    revenue: float = 0.0
    net_profit: float = 0.0
    n_income_attr_p: float = 0.0   # 扣非净利润
    non_recurring_ratio: float = 0.0  # 非经常性损益占比(%)
    roe: float = 0.0
    gross_margin: float = 0.0
    rd_expense_ratio: float = 0.0
    revenue_yoy: float = 0.0
    profit_yoy: float = 0.0
    q1_profit_yoy: float = None  # Q1净利润同比（可能为None）
    deduct_profit_yoy: float = 0.0  # 扣非净利润同比(%)
    profit_cagr_3y: float = 0.0     # 近3年净利润CAGR(%)
    cashflow_ratio: float = 0.0     # 经营现金流/营收
    market_cap: float = 0.0

    # 细节
    sub_details: Dict = field(default_factory=dict)


class BullScorerV2:
    """
    BullScore v2.1 — 在 v2 基础上叠加历史辨识度评分 + Alpha因子评分 + 龙头/中军识别
    
    评分结构：
    ┌─────────────────────────────────────────────────────────────┐
    │  BullScore v2.1 (100分)                                    │
    │  ├── 原8因子 (70%)                                         │
    │  │   ├── 产业景气 (14%)    │ 订单爆发 (14%)                │
    │  │   ├── 技术壁垒 (10%)    │ 业绩质量 (10%)                │
    │  │   ├── 预期差 (8%)       │ 龙头地位 (6%)                 │
    │  │   ├── 机构认可 (4%)     │ 市值弹性 (4%)                 │
    │  ├── 筹码面 (7%)                                           │
    │  ├── 估值安全 (7%)                                         │
    │  ├── 历史辨识度 (8%) ← 新增                                │
    │  └── Alpha因子 (8%) ← 新增                                 │
    └─────────────────────────────────────────────────────────────┘
    
    最终得分: FinalScore = 0.82 * BullScore_v2.1 + 0.18 * ThemeScore_v2
    
    龙头/中军识别: 基于市值(60亿-5000亿)、辨识度、Alpha、主题匹配度判定股票类型
    """

    def __init__(self, token: str = None):
        self.token = token or _get_token()
        ts.set_token(self.token)
        self.pro = ts.pro_api()

        # 统一缓存入口：所有评分器共用同一份 DataFetcher 单例
        self.df = _get_df()

        # 原有评分器
        self.chip_scorer = ChipScorer(self.pro, df=self.df)
        self.safety_scorer = SafetyScorer(self.pro, df=self.df)
        self.theme_scorer = ThemeScorerV2(self.pro, df=self.df)

        # 新增评分器
        self.recognition_scorer = RecognitionScorer(self.pro, df=self.df)
        self.alpha_scorer = AlphaScorer(self.pro, df=self.df)
        self.leader_recognizer = LeaderRecognizer(self.pro, df=self.df)

        # 缓存
        self._chip_cache: Dict[str, Tuple[float, Dict]] = {}
        self._safety_cache: Dict[str, Tuple[float, Dict]] = {}
        self._theme_cache: Dict[str, Tuple[float, str, Dict]] = {}
        self._recognition_cache: Dict[str, Tuple[float, Dict]] = {}
        self._alpha_cache: Dict[str, Tuple[float, Dict]] = {}
        self._leader_cache: Dict[str, Dict] = {}
        
        # 市值过滤范围（80亿-5000亿）
        self.min_market_cap = 80 * 1e8   # 80亿
        self.max_market_cap = 5000 * 1e8 # 5000亿
        
        # 持久化文件缓存（同一天不重拉Tushare）— 统一到 cache_config.PARQUET_DIR
        try:
            from cache_config import PARQUET_DIR
            self._cache_dir = Path(PARQUET_DIR)
        except Exception:
            self._cache_dir = Path(__file__).parent / 'cache'
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_date = datetime.now().strftime('%Y%m%d')
        self._file_caches: Dict[str, dict] = {}
        # 线程锁：保护缓存字典和文件写入
        self._cache_lock = threading.Lock()

    def _load_file_cache(self, name: str) -> dict:
        """加载持久化文件缓存（线程安全）"""
        with self._cache_lock:
            if name in self._file_caches:
                return self._file_caches[name]
        # 文件读取在锁外进行（避免持有锁时做IO阻塞其他线程）
        path = self._cache_dir / f'{name}.json'
        data = None
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('_date') != self._cache_date:
                    data = None
            except:
                pass
        if data is None:
            data = {'_date': self._cache_date}
        with self._cache_lock:
            if name not in self._file_caches:  # double-check
                self._file_caches[name] = data
            return self._file_caches[name]

    def _save_file_cache(self, name: str):
        """批量模式下跳过增量写入，由外层统一写一次"""
        # 批量模式下 _cache_save_counter 为 0，跳过所有增量写入
        pass

    def _flush_file_cache(self, name: str):
        """将指定缓存写入磁盘文件"""
        with self._cache_lock:
            if name not in self._file_caches:
                return
            data = self._file_caches[name]
        path = self._cache_dir / f'{name}.json'
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.debug(f'保存缓存 {name} 失败: {e}')

    def _get_cached_score(self, cache_name: str, ts_code: str, compute_fn) -> Tuple[float, Dict]:
        """通用带文件缓存的评分获取"""
        # 先检查内存缓存
        cache = self._load_file_cache(cache_name)
        if ts_code in cache:
            entry = cache[ts_code]
            return entry['score'], entry['details']
        # 计算并缓存
        score, details = compute_fn(ts_code)
        cache[ts_code] = {'score': score, 'details': details}
        self._save_file_cache(cache_name)
        return score, details

    def _get_chip_score(self, ts_code: str) -> Tuple[float, Dict]:
        """带缓存的筹码面评分（支持文件持久化）"""
        # 文件缓存
        cache = self._load_file_cache('chip')
        if ts_code in cache:
            entry = cache[ts_code]
            # 也写入内存缓存
            if ts_code not in self._chip_cache:
                self._chip_cache[ts_code] = (entry['score'], entry['details'])
            return entry['score'], entry['details']
        # 计算
        score, details = self.chip_scorer.compute(ts_code)
        cache[ts_code] = {'score': score, 'details': details}
        self._save_file_cache('chip')
        return score, details

    def _get_safety_score(self, ts_code: str, profit_yoy: float,
                           roe: float, net_cf: float, revenue: float) -> Tuple[float, Dict]:
        """带缓存的估值安全评分（支持文件持久化）"""
        # 文件缓存（同一天同一只股safety不变，ts_code即唯一标识）
        cache = self._load_file_cache('safety')
        if ts_code in cache:
            entry = cache[ts_code]
            return entry['score'], entry['details']
        score, details = self.safety_scorer.compute(
            ts_code, profit_yoy, roe, net_cf, revenue
        )
        cache[ts_code] = {'score': score, 'details': details}
        self._save_file_cache('safety')
        return score, details

    def _get_theme_score_v2(self, ts_code: str, chain_tag: str, industry: str = "") -> Tuple[float, str, Dict]:
        """带缓存的主题加成评分（支持文件持久化）"""
        cache = self._load_file_cache('theme')
        if ts_code in cache:
            entry = cache[ts_code]
            return entry['score'], entry['theme'], entry['details']
        score, theme, details = self.theme_scorer.score_by_mainbz(ts_code)
        if score < 1.0:
            # 优先主营产业(IndustryTheme)兜底，避免概念标签(chain_tag)误配
            # 例: 仓储物流→物流, 水运→航运, 轻工机械→机器人/智能制造
            score, theme = self.theme_scorer.score_by_industry(industry)
            if score >= 1.0:
                details = {"fallback": True, "method": "industry", "industry": industry}
            else:
                score, theme = self.theme_scorer.score_fallback(chain_tag)
                details = {"fallback": True, "method": "chain_tag", "chain_tag": chain_tag}
        cache[ts_code] = {'score': score, 'theme': theme, 'details': details}
        self._save_file_cache('theme')
        return score, theme, details

    def _get_recognition_score(self, ts_code: str, market_cap: float, industry: str) -> Tuple[float, Dict]:
        """带缓存的历史辨识度评分（支持文件持久化）"""
        cache = self._load_file_cache('recognition')
        if ts_code in cache:
            entry = cache[ts_code]
            return entry['score'], entry['details']
        score, details = self.recognition_scorer.compute(ts_code, market_cap, industry)
        cache[ts_code] = {'score': score, 'details': details}
        self._save_file_cache('recognition')
        return score, details

    def _get_alpha_score(self, ts_code: str, roe: float, profit_yoy: float,
                         revenue_yoy: float, rd_ratio: float, pe: float, pb: float,
                         cash_flow_ratio: float, industry: str, market_cap: float) -> Tuple[float, Dict]:
        """带缓存的Alpha因子评分"""
        cache_key = f"{ts_code}_{roe:.1f}_{profit_yoy:.0f}_{pe:.0f}"
        if cache_key not in self._alpha_cache:
            self._alpha_cache[cache_key] = self.alpha_scorer.compute(
                ts_code, roe, profit_yoy, revenue_yoy, rd_ratio, pe, pb,
                cash_flow_ratio, industry, market_cap
            )
        return self._alpha_cache[cache_key]

    def _get_leader_recognition(self, ts_code: str, market_cap: float, industry: str,
                                recognition_score: float, alpha_score: float,
                                theme_score: float, chip_score: float,
                                max_consecutive_zt: int = 0) -> Dict:
        """带缓存的龙头/中军识别（含连板硬门槛）"""
        cache_key = ts_code
        if cache_key not in self._leader_cache:
            self._leader_cache[cache_key] = self.leader_recognizer.recognize(
                ts_code, market_cap, industry, recognition_score, alpha_score,
                theme_score, chip_score, max_consecutive_zt
            )
        return self._leader_cache[cache_key]

    def compute_v2(self,
                   base_result: 'BullScoreResult'  # 从 bull_scorer 继承的结果
                   ) -> BullScoreV2Result:
        """
        BullScore v3.0 完整评分计算（超预期持续成长版）
        
        评分理念：从"宽泛多因子渔网"→"超预期驱动的持续成长王者评分"
        
        评分结构：
          核心增长因子 (54%)：产业景气14% | 订单爆发14% | 业绩质量12% | 预期差14%
          护城河因子 (26%)：技术壁垒10% | 龙头地位8% | 机构认可8%
          估值+弹性因子 (12%)：估值安全7% | 市值弹性5%
          历史辨识度 (8%)
          ★ Alpha因子已移除 — 与成长/质量/流动性因子高度重叠
        
        总分公式：
          BullScore_v3.0 = 0.14*ind + 0.14*order + 0.10*tech + 0.12*earn_qual
                         + 0.14*expect + 0.08*leader + 0.08*inst + 0.05*mc
                         + 0.07*safety + 0.08*recognition
        
          FinalScore = 0.88 * BullScore_v3.0 + 0.12 * ThemeScore_v2
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

        # 4. 预期差非线性放大（v3.1: 增加增长趋势信号）
        profit_yoy = base_result.profit_yoy / 100 if base_result.profit_yoy else 0
        revenue_yoy = base_result.revenue_yoy / 100 if base_result.revenue_yoy else 0
        growth_trend = getattr(base_result, 'growth_trend', 'stable')  # v3.1新增
        expect_boosted = expectation_nonlinear_boost(
            profit_yoy, base_result.expectation_score, revenue_yoy, growth_trend
        )

        # 5. 历史辨识度评分 ← 新增
        recognition_score, recognition_detail = self._get_recognition_score(
            base_result.ts_code,
            base_result.market_cap or 0,
            base_result.industry or ""
        )

        # 6. 龙头/中军识别（Alpha因子已移除，传入0.0占位）
        # 从历史辨识度详情中提取最大连板数，用于龙头硬门槛校验
        max_consecutive_zt = 0
        if isinstance(recognition_detail, dict):
            limit_up_info = recognition_detail.get('limit_up', {})
            if isinstance(limit_up_info, dict):
                max_consecutive_zt = int(limit_up_info.get('max_consecutive_zt', 0))
        leader_result = self._get_leader_recognition(
            base_result.ts_code,
            base_result.market_cap or 0,
            base_result.industry or "",
            recognition_score,
            0.0,        # alpha_score已移除(v3.0)
            theme_score_v2,
            chip_score,
            max_consecutive_zt
        )

        # 7. BullScore v3.0 计算（Alpha因子已移除）
        ind_w = 0.12        # 0.14->0.12
        order_w = 0.12      # 0.14->0.12
        tech_w = 0.09       # 0.10->0.09
        earn_w = 0.11       # 0.12->0.11
        expect_w = 0.12     # 0.14->0.12（部分权重让给超预期因子,避免重复计权）
        leader_w = 0.07     # 0.08->0.07
        inst_w = 0.07       # 0.08->0.07
        mc_w = 0.04         # 0.05->0.04
        chip_w = 0.00       # 筹码面已移除
        safety_w = 0.06     # 0.07->0.06
        recognition_w = 0.07  # 0.08->0.07
        # v3.2 新增: 业绩超预期(基于中报预告PEAD信号) + 波段属性(适合反复波段)
        earn_surp_w = 0.08  # 业绩超预期 8%
        swing_w = 0.05      # 波段属性 5%
        total_weight_check = sum([ind_w, order_w, tech_w, earn_w, expect_w,
                                  leader_w, inst_w, mc_w, safety_w, recognition_w,
                                  earn_surp_w, swing_w])
        # v3.0: Alpha因子已移除，权重重新分配至核心增长因子

        bull_v3 = (
            ind_w * base_result.industry_demand_score +
            order_w * base_result.order_explosion_score +
            tech_w * base_result.tech_barrier_score +
            earn_w * base_result.earnings_quality_score +
            expect_w * expect_boosted +    # 非线性放大后的预期差
            leader_w * base_result.leader_score +
            inst_w * base_result.institution_score +
            mc_w * base_result.marketcap_score +
            safety_w * safety_score +
            recognition_w * recognition_score +
            earn_surp_w * base_result.earnings_surprise_score +   # v3.2 新增: 业绩超预期
            swing_w * base_result.swing_quality_score             # v3.2 新增: 波段属性
        )

        # 8. 最终分 = BullScore_v3.2 + 主题加成（v3.2修复: 恢复主题分作用,最高+5分）
        theme_bonus_v2 = theme_score_v2 / 100.0 * 5.0
        final = bull_v3 + theme_bonus_v2

        # 等级判定
        level = self._get_level(len([base_result]), 0)

        # 构建详情（移除alpha_detail）
        sub_details = dict(base_result.sub_details)
        sub_details['chip'] = chip_detail
        sub_details['safety'] = safety_detail
        sub_details['theme_v2'] = theme_detail
        sub_details['recognition'] = recognition_detail
        sub_details['leader'] = leader_result
        sub_details['expect_boosted'] = round(expect_boosted - base_result.expectation_score, 1)
        sub_details['weights'] = {
            'ind_demand': ind_w, 'order': order_w,
            'tech_barrier': tech_w, 'earnings': earn_w,
            'expectation': expect_w, 'leader': leader_w,
            'institution': inst_w, 'marketcap': mc_w,
            'chip': 0.0, 'safety': safety_w,
            'recognition': recognition_w, 'alpha': 0.0,
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
            # 新增字段
            recognition_score=round(recognition_score, 2),
            alpha_score=0.0,  # Alpha因子已移除(v3.0)
            leader_type=leader_result.get('leader_type', ''),
            leader_features=leader_result.get('features', []),
            # v3.2 新增: 透传 v1 层的业绩超预期 + 波段属性评分
            earnings_surprise_score=round(base_result.earnings_surprise_score, 2),
            swing_quality_score=round(base_result.swing_quality_score, 2),
            forecast_profit_change=round(base_result.forecast_profit_change, 2),
            forecast_vs_analyst_gap=round(base_result.forecast_vs_analyst_gap, 2),
            forecast_ann_date=base_result.forecast_ann_date,
            quarterly_net_profit=round(base_result.quarterly_net_profit, 2),
            quarterly_net_profit_prev=round(base_result.quarterly_net_profit_prev, 2),
            sequential_qoq_growth=round(base_result.sequential_qoq_growth, 2),
            # v3.3 估值空间透传 → v4.0 成长兑现模型
            fair_value=base_result.fair_value,
            optimistic_value=base_result.optimistic_value,
            valuation_space=base_result.valuation_space,
            fair_pe=base_result.fair_pe,
            pe_ttm=base_result.pe_ttm,
            pb=base_result.pb,
            close_price=base_result.close_price,
            fair_price=base_result.fair_price,
            optimistic_price=base_result.optimistic_price,
            # v4.0 Bear/Base/Bull 三档
            bear_pe=base_result.bear_pe,
            bull_pe=base_result.bull_pe,
            bear_price=base_result.bear_price,
            base_price=base_result.base_price,
            bull_price=base_result.bull_price,
            bear_prob=base_result.bear_prob,
            base_prob=base_result.base_prob,
            bull_prob=base_result.bull_prob,
            # 原有字段
            bull_score_v2=round(bull_v3, 2),  # v3.0改名为bull_v3
            theme=theme_name,
            theme_score_v2=round(theme_score_v2, 2),
            industry_theme=self.theme_scorer.score_by_industry(base_result.industry or "")[1],
            concept_theme=base_result.chain_tag or "",
            final_score=round(final, 2),
            bull_level=level,
            revenue=base_result.revenue,
            net_profit=base_result.net_profit,
            n_income_attr_p=base_result.n_income_attr_p,
            non_recurring_ratio=base_result.non_recurring_ratio,
            roe=base_result.roe,
            gross_margin=base_result.gross_margin,
            rd_expense_ratio=base_result.rd_expense_ratio,
            revenue_yoy=base_result.revenue_yoy,
            profit_yoy=base_result.profit_yoy,
            q1_profit_yoy=base_result.q1_profit_yoy,
            deduct_profit_yoy=base_result.deduct_profit_yoy,
            profit_cagr_3y=base_result.profit_cagr_3y,
            cashflow_ratio=base_result.cashflow_ratio,
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

    def _prewarm_caches(self, base_results: List['BullScoreResult']):
        """
        预热缓存：先用 DataFetcher 本地 DB 获取真实评分；不可用时回退默认 0 分。
        """
        total = len(base_results)
        df = _get_df()
        if df is None:
            # DataFetcher 不可用 → 默认 0 分
            logger.info("DataFetcher 不可用，使用默认 0 分")
            for cache_name in ['chip', 'safety', 'theme', 'recognition']:
                cache = self._load_file_cache(cache_name)
                count = sum(1 for br in base_results if br.ts_code not in cache)
                for br in base_results:
                    if br.ts_code not in cache:
                        entry = {'score': 0.0, 'details': {}}
                        if cache_name == 'theme':
                            entry['theme'] = ''
                        cache[br.ts_code] = entry
                if count:
                    self._flush_file_cache(cache_name)
                    logger.info(f"  {cache_name}缓存: 填充{count}/{total}只默认0分")
            return

        # DataFetcher 可用 → 检查缓存是否已足够，不足才预热
        for cache_name in ['chip', 'safety', 'theme', 'recognition']:
            cache = self._load_file_cache(cache_name)
            # 如果缓存已有 ≥90% 的股票，说明今天已预热过，跳过
            if len(cache) >= total * 0.9:
                continue
            # 缓存不足，删掉重建
            path = self._cache_dir / f'{cache_name}.json'
            if path.exists():
                path.unlink()
            self._file_caches.pop(cache_name, None)
        # 检查是否所有缓存都足够
        all_ready = True
        for cache_name in ['chip', 'safety', 'theme', 'recognition']:
            cache = self._load_file_cache(cache_name)
            if len(cache) < total * 0.9:
                all_ready = False
                break
        if all_ready:
            logger.info(f"缓存已预热（{total} 只），跳过重复预热")
            return
        t_start = time.time()
        for i, br in enumerate(base_results):
            # chip
            try:
                self._get_chip_score(br.ts_code)
            except Exception:
                c = self._load_file_cache('chip')
                if br.ts_code not in c:
                    c[br.ts_code] = {'score': 0.0, 'details': {}}
            # safety
            try:
                self._get_safety_score(
                    br.ts_code,
                    br.profit_yoy / 100 if br.profit_yoy else 0,
                    br.roe / 100 if br.roe else 0,
                    br.sub_details.get('earnings_quality', {}).get('cashflow_growth_rank', 0),
                    br.revenue,
                )
            except Exception:
                c = self._load_file_cache('safety')
                if br.ts_code not in c:
                    c[br.ts_code] = {'score': 0.0, 'details': {}}
            # theme
            try:
                self._get_theme_score_v2(br.ts_code, br.chain_tag, br.industry or "")
            except Exception:
                c = self._load_file_cache('theme')
                if br.ts_code not in c:
                    c[br.ts_code] = {'score': 0.0, 'theme': '', 'details': {}}
            # recognition
            try:
                self._get_recognition_score(br.ts_code, br.market_cap or 0, br.industry or "")
            except Exception:
                c = self._load_file_cache('recognition')
                if br.ts_code not in c:
                    c[br.ts_code] = {'score': 0.0, 'details': {}}

            if (i + 1) % 100 == 0 or (i + 1) == total:
                elapsed = time.time() - t_start
                speed = (i + 1) / max(elapsed, 1)
                rem = (total - i - 1) / max(speed, 1)
                # 统计各缓存已填充数
                chip_n = len(self._load_file_cache('chip')) - 1
                safe_n = len(self._load_file_cache('safety')) - 1
                theme_n = len(self._load_file_cache('theme')) - 1
                recog_n = len(self._load_file_cache('recognition')) - 1
                logger.info(f"  预热 {i+1}/{total}  chip{chip_n} 安全{safe_n} 主题{theme_n} 辨识{recog_n}  {speed:.0f}只/分  剩余{rem/60:.0f}分钟")

        for cache_name in ['chip', 'safety', 'theme', 'recognition']:
            self._flush_file_cache(cache_name)
        elapsed = time.time() - t_start
        logger.info(f"预热完成! {total}只 耗时{elapsed/60:.1f}分钟")

    def _batch_prewarm_and_score(self, base_results, batch_size=12, delay=0.15, filter_market_cap=True):
        """预热缓存 → 批量并行评分（两步法）"""
        # 市值过滤
        if filter_market_cap:
            filtered = []
            for br in base_results:
                mc = br.market_cap or 0
                if self.min_market_cap <= mc <= self.max_market_cap:
                    filtered.append(br)
            logger.info(f"市值过滤前: {len(base_results)}只, 过滤后: {len(filtered)}只")
            base_results = filtered

        # Step 1: 串行预热缓存
        self._prewarm_caches(base_results)

        # Step 2: 批量并行评分（此时所有缓存已命中，零 API 调用）
        return self._batch_score_only(base_results, batch_size, delay)

    def _batch_score_only(self, base_results, batch_size=12, delay=0.15):
        """只做评分计算（假设缓存已预热），不进行任何 tushare API 调用"""
        self._batch_mode = True
        logger.remove()
        results = []
        total = len(base_results)
        logger.info(f"批量评分计算 {total} 只（缓存预热完毕，零API调用）...")
        results_lock = threading.Lock()

        def _process_one(br):
            try:
                r = self.compute_v2(br)
                with results_lock:
                    results.append(r)
            except Exception as e:
                logger.debug(f"v2评分失败 {br.ts_code} {br.name}: {e}")
                with results_lock:
                    results.append(BullScoreV2Result(
                        ts_code=br.ts_code, name=br.name, industry=br.industry,
                        chain_tag=br.chain_tag, final_score=br.final_score,
                        bull_score_v2=br.bull_score, bull_level=br.bull_level,
                    ))

        for i in range(0, total, batch_size):
            batch = base_results[i:i + batch_size]
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                futures = {executor.submit(_process_one, br): br for br in batch}
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception:
                        pass
            if i + batch_size < total:
                time.sleep(delay)
            done = min(i + batch_size, total)
            if done % 50 == 0 or done == total:
                print(f"  v2评分: {done}/{total}", flush=True)

        logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
        for cache_name in ['chip', 'safety', 'theme', 'recognition']:
            self._flush_file_cache(cache_name)
        results.sort(key=lambda r: r.final_score, reverse=True)
        self._assign_leader_types(results)
        logger.info(f"评分计算完成: {len(results)} 只")
        return results

    def _assign_leader_types(self, results):
        """同行业龙头识别 + 等级分配"""
        industry_groups = {}
        for r in results:
            ind = r.industry or "未知"
            industry_groups.setdefault(ind, []).append(r)
        for ind, group in industry_groups.items():
            if len(group) < 3:
                continue
            group.sort(key=lambda x: x.revenue or 0, reverse=True)
            group[0].leader_type = "行业龙头"
            group[0].leader_features = group[0].leader_features + ["行业龙头"]
            if len(group) >= 2:
                group[1].leader_type = "行业龙二"
                group[1].leader_features = group[1].leader_features + ["行业龙二"]
        for idx, r in enumerate(results):
            r.bull_level = self._get_level(len(results), idx + 1)

    def batch_compute(self, base_results: List['BullScoreResult'],
                       batch_size: int = 12, delay: float = 0.15,
                       filter_market_cap: bool = True) -> List[BullScoreV2Result]:
        """
        批量计算（两步法：先串行预热缓存，再并行评分）
        
        Args:
            base_results: 来自 bull_scorer.py 的基础评分结果
            batch_size: 每批并发数(=线程数)
            delay: 每批间隔(秒)
            filter_market_cap: 是否过滤市值（60亿-5000亿）
        """
        return self._batch_prewarm_and_score(base_results, batch_size, delay, filter_market_cap)

    def to_dataframe(self, results: List[BullScoreV2Result]) -> pd.DataFrame:
        """转 DataFrame"""
        rows = []
        for r in results:
            code = r.ts_code.split('.')[0]
            chip_d = r.sub_details.get('chip', {})
            safety_d = r.sub_details.get('safety', {})
            theme_d = r.sub_details.get('theme_v2', {})
            recog_d = r.sub_details.get('recognition', {})
            alpha_d = r.sub_details.get('alpha', {})

            rows.append({
                'code': code, 'name': r.name, 'industry': r.industry,
                'theme': r.theme,
                '主营产业': r.industry_theme,
                '概念主题': r.concept_theme,
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
                # v2 新增
                '筹码面': r.chip_score,
                # v2.1 新增
                '历史辨识度': r.recognition_score,
                '业绩超预期': r.earnings_surprise_score,
                '波段属性': r.swing_quality_score,
                '预告变动%': r.forecast_profit_change,
                '预告偏离': r.forecast_vs_analyst_gap,
                # v3.3 估值空间 → v4.0 成长兑现模型
                'PE_TTM': r.pe_ttm,
                'PB': r.pb,
                '现价': r.close_price,
                '合理PE': r.fair_pe,
                '合理估值(亿)': r.fair_value,
                '乐观估值(亿)': r.optimistic_value,
                '估值空间%': r.valuation_space,
                'Bear价': r.bear_price,
                'Base价': r.base_price,
                'Bull价': r.bull_price,
                'Bear概率%': r.bear_prob,
                'Base概率%': r.base_prob,
                'Bull概率%': r.bull_prob,
                '龙头类型': r.leader_type,
                '特征标签': ','.join(r.leader_features),
                # 总分
                'Bull_v2.1分': round(r.bull_score_v2, 1),
                '主题分v2': r.theme_score_v2,
                '最终分': r.final_score,
                '等级': r.bull_level,
                # 关键财务
                '营收同比': r.revenue_yoy,
                '利润同比': r.profit_yoy,
                'Q1利润同比': round(r.q1_profit_yoy * 100, 1) if r.q1_profit_yoy is not None else '',
                '扣非利润同比': round(r.deduct_profit_yoy, 1) if r.deduct_profit_yoy else '',
                '3年利润CAGR': round(r.profit_cagr_3y, 1) if r.profit_cagr_3y else '',
                '现金流/营收比': round(r.cashflow_ratio, 3) if r.cashflow_ratio else '',
                '扣非净利润(亿)': round(r.n_income_attr_p / 1e8, 2) if r.n_income_attr_p else '',
                '非经常损益%': r.non_recurring_ratio,
                'ROE': r.roe,
                '毛利率': r.gross_margin,
                '研发投入%': r.rd_expense_ratio,
                '市值(亿)': round(r.market_cap / 1e8, 1) if r.market_cap else '',
                # 筹码面详情
                '资金流入(亿)': chip_d.get('moneyflow', {}).get('net_inflow_b', ''),
                '股东数变化%': chip_d.get('holder', {}).get('change_pct', ''),
                '公募持仓变化%': chip_d.get('fund', {}).get('change_pct', ''),
                # 安全面详情
                'PEG': safety_d.get('peg', {}).get('peg', ''),
                '质押率%': safety_d.get('pledge', {}).get('pledge_ratio', ''),
                '解禁占比%': safety_d.get('float', {}).get('float_ratio_60d', ''),
                # 主题详情
                '主题匹配方式': theme_d.get('method', 'fina_mainbz'),
                # 辨识度详情
                '涨停次数': recog_d.get('limit_up', {}).get('limit_up_count', ''),
                '连板能力': recog_d.get('limit_up', {}).get('max_consecutive_zt', ''),
                # Alpha详情
                '60日收益%': alpha_d.get('momentum', {}).get('ret_60d', ''),
                '日均成交额(亿)': alpha_d.get('liquidity', {}).get('avg_amount_b', ''),
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

        print(f"\n{'='*110}")
        print(f"BullScore v2.1 中长线牛股选股结果")
        print(f"{'='*110}")
        print(f"扫描范围: {len(results)} 只")
        print(f"\n牛股等级分布:")
        for lv in ["A级产业龙头", "B级成长股", "观察名单", "淘汰"]:
            print(f"  {lv}: {levels.get(lv, 0)}只")
        
        # 统计龙头类型分布
        leader_types = {}
        for r in results:
            leader_types[r.leader_type] = leader_types.get(r.leader_type, 0) + 1
        
        print(f"\n龙头类型分布:")
        for lt in ["行业龙头", "行业龙二", "龙头", "中军", "龙二", "补涨", "普通"]:
            print(f"  {lt}: {leader_types.get(lt, 0)}只")

        top = results[:top_n]
        print(f"\nTop {top_n} 牛股:")
        header = f"{'排名':>4} {'代码':>8} {'名称':<8} {'主题':<10} {'龙头':<4} {'Bull':>6} {'超预期':>6} {'波段':>6} {'估值空间':>6} {'最终':>6} {'等级':<12}"
        print(header)
        print("-" * 115)
        for i, r in enumerate(top, 1):
            code = r.ts_code.split('.')[0]
            vs_str = f"{r.valuation_space:>+5.0f}%" if r.valuation_space else "  无"
            print(f"{i:>4} {code:>8} {r.name:<8} {r.theme:<10} {r.leader_type:<4} {r.bull_score_v2:>6.1f} {r.earnings_surprise_score:>6.1f} {r.swing_quality_score:>6.1f} {vs_str:>6} {r.final_score:>6.1f} {r.bull_level:<12}")

        # 新增因子专项排名
        print(f"\n★ Top 10 筹码面最强:")
        for r in sorted(results, key=lambda x: x.chip_score, reverse=True)[:10]:
            print(f"  {r.name:<8} ({r.ts_code.split('.')[0]}) chip={r.chip_score:.1f}")

        print(f"\n★ Top 10 估值最安全:")
        for r in sorted(results, key=lambda x: x.safety_score, reverse=True)[:10]:
            print(f"  {r.name:<8} ({r.ts_code.split('.')[0]}) safety={r.safety_score:.1f}")

        print(f"\n★ Top 10 历史辨识度最高:")
        for r in sorted(results, key=lambda x: x.recognition_score, reverse=True)[:10]:
            print(f"  {r.name:<8} ({r.ts_code.split('.')[0]}) recognition={r.recognition_score:.1f}")

        print(f"\n★ Top 10 业绩超预期最强(中报预告PEAD信号):")
        for r in sorted(results, key=lambda x: x.earnings_surprise_score, reverse=True)[:10]:
            print(f"  {r.name:<8} ({r.ts_code.split('.')[0]}) 超预期={r.earnings_surprise_score:.1f} "
                  f"预告变动={r.forecast_profit_change:.0f}% 偏离={r.forecast_vs_analyst_gap:.0f}")

        print(f"\n★ Top 10 波段属性最强(适合反复波段):")
        for r in sorted(results, key=lambda x: x.swing_quality_score, reverse=True)[:10]:
            print(f"  {r.name:<8} ({r.ts_code.split('.')[0]}) 波段分={r.swing_quality_score:.1f}")

        print(f"\n★ Top 10 估值空间最大(成长兑现模型 Bear/Base/Bull):")
        for r in sorted(results, key=lambda x: x.valuation_space, reverse=True)[:10]:
            print(f"  {r.name:<8} ({r.ts_code.split('.')[0]}) "
                  f"空间={r.valuation_space:>+5.0f}% "
                  f"PE(TTM)={r.pe_ttm:.1f}→合理={r.fair_pe:.1f} "
                  f"现价={r.close_price:.2f} "
                  f"Bear={r.bear_price:.1f}({r.bear_prob}%) "
                  f"Base={r.base_price:.1f}({r.base_prob}%) "
                  f"Bull={r.bull_price:.1f}({r.bull_prob}%)")

        print(f"\n★ 龙头股列表:")
        leaders = [r for r in results if r.leader_type == "龙头"]
        for r in leaders[:10]:
            code = r.ts_code.split('.')[0]
            features = ",".join(r.leader_features[:3])
            print(f"  {r.name:<8} ({code}) theme={r.theme:<8} final={r.final_score:.1f} [{features}]")

        print(f"\n★ 中军股列表:")
        centrals = [r for r in results if r.leader_type == "中军"]
        for r in centrals[:10]:
            code = r.ts_code.split('.')[0]
            features = ",".join(r.leader_features[:3])
            print(f"  {r.name:<8} ({code}) theme={r.theme:<8} final={r.final_score:.1f} [{features}]")


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
