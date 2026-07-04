"""
趋势性上涨选股模型（三因子框架）v1.0
基于：兆易创新/烽火通信/雅克科技 2026年4-6月趋势行情研究

模型核心理念：
    趋势性上涨 ≠ 随机上涨，背后必有基本面逻辑、资金推动、技术形态三重共振。
    通过三因子筛选，捕捉"主线赛道 + 机构重仓 + 均线突破"的趋势股。

因子框架：
    基本面因子（40%）：F1赛道属性(15%) + F2业绩拐点(15%) + F3市值区间(10%)
    资金面因子（45%）：F4机构持仓(15%) + F5资金流向(20%) + F6换手率(10%)
    技术面因子（15%）：F7均线系统(10%) + F8成交量(5%) + F9技术指标(5%)

判断标准：
    总分 ≥ 14分（18分制）：强趋势，可考虑参与
    总分 10-13分：中等趋势，需结合买点判断
    总分 < 10分：趋势不稳或已终结，回避
"""

import sys
import time
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

import pandas as pd
import numpy as np
from loguru import logger

# 复用现有模块
sys.path.insert(0, str(Path(__file__).parent))
from data_fetcher import DataFetcher, get_cache_dir, load_cache, save_cache


# ════════════════════════════════════════════════════════
# 数据结构定义
# ════════════════════════════════════════════════════════

@dataclass
class TrendFactor:
    """单个因子评分结果"""
    name: str
    code: str
    weight: float
    raw_score: float  # 0-2分
    weighted_score: float
    detail: Dict = field(default_factory=dict)


@dataclass
class TrendResult:
    """趋势选股结果"""
    ts_code: str
    name: str
    industry: str
    
    # 三类因子总分
    fundamental_score: float = 0.0  # 基本面总分
    capital_score: float = 0.0      # 资金面总分
    technical_score: float = 0.0    # 技术面总分
    total_score: float = 0.0        # 总分（18分制）
    normalized_score: float = 0.0   # 标准化分（100分制）
    
    # 趋势状态
    trend_status: str = "unknown"   # strong/moderate/weak/terminated
    buy_signal: str = ""            # A/B/C买点类型
    stop_loss_price: float = 0.0    # 止损价
    
    # 因子明细
    factors: Dict[str, TrendFactor] = field(default_factory=dict)
    
    # 原始数据快照
    raw_data: Dict = field(default_factory=dict)


# ════════════════════════════════════════════════════════
# 主线赛道定义
# ════════════════════════════════════════════════════════

TREND_THEMES = {
    # 半导体主线
    "半导体设备": ["北方华创", "中微公司", "拓荆科技", "华海清科", "芯源微"],
    "半导体材料": ["雅克科技", "华特气体", "安集科技", "鼎龙股份", "江丰电子"],
    "存储芯片": ["兆易创新", "北京君正", "澜起科技", "聚辰股份", "普冉股份"],
    
    # AI算力主线
    "AI算力": ["中科曙光", "浪潮信息", "工业富联", "寒武纪", "海光信息"],
    "光模块": ["中际旭创", "新易盛", "华工科技", "光迅科技", "剑桥科技"],
    "PCB": ["沪电股份", "深南电路", "生益科技", "景旺电子", "胜宏科技"],
    
    # 高端制造主线
    "机器人": ["汇川技术", "埃斯顿", "绿的谐波", "双环传动", "中大力德"],
    "商业航天": ["中国卫星", "航天电子", "烽火通信", "海格通信", "华力创通"],
    
    # 国产替代主线
    "国产软件": ["金山办公", "用友网络", "中国软件", "麒麟信安", "诚迈科技"],
    "数据要素": ["太极股份", "易华录", "云赛智联", "广电运通", "深桑达A"]
}

# 主线行业映射
STRATEGIC_INDUSTRIES = [
    "半导体", "集成电路", "芯片", "存储", "GPU", "CPU",
    "AI算力", "人工智能", "机器学习", "深度学习",
    "高端制造", "机器人", "工业母机", "数控系统",
    "商业航天", "低空经济", "卫星互联网",
    "国产替代", "自主可控", "信创"
]


# ════════════════════════════════════════════════════════
# 数据获取函数（复用DataFetcher）
# ════════════════════════════════════════════════════════

def get_daily_data(fetcher: DataFetcher, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取日线数据（合并 daily + daily_basic，合并结果有业务语义，单独缓存）

    注：daily 与 daily_basic 的原始 API 调用已委托给 DataFetcher 的
    get_daily_by_code / get_daily_basic_by_code，享受 _rate_limit（220ms 间隔）
    与 _retry_call 重试逻辑；此处仅保留"合并结果"的业务缓存。
    """
    # 合并多个数据源的业务缓存 key（区别于 DataFetcher 内的单 API 缓存）
    cache_key = f"daily_merged_{ts_code}_{start_date}_{end_date}"
    cache_dir = get_cache_dir(fetcher.config)

    # 尝试加载缓存
    cached = load_cache(cache_dir, cache_key, expire_hours=24)
    if cached is not None and len(cached) > 0:
        return cached

    try:
        # 通过 DataFetcher 方法层调用（含速率限制与重试）
        df = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)
        if df is None or len(df) == 0:
            return pd.DataFrame()

        # 获取换手率等基本面字段（从 daily_basic，同样走 DataFetcher 方法层）
        try:
            df_basic = fetcher.get_daily_basic_by_code(ts_code, start_date=start_date, end_date=end_date)
            if df_basic is not None and len(df_basic) > 0:
                # 合并换手率
                df = df.merge(df_basic, on=['ts_code', 'trade_date'], how='left')
        except Exception as e:
            logger.debug(f"换手率数据缺失 {ts_code}: {e}")

        # 确保有turnover_rate列
        if 'turnover_rate' not in df.columns:
            df['turnover_rate'] = 0.0

        df = df.sort_values('trade_date').reset_index(drop=True)
        save_cache(df, cache_dir, cache_key)
        return df

    except Exception as e:
        logger.warning(f"获取日线数据失败 {ts_code}: {e}")

    return pd.DataFrame()


def get_holder_data(fetcher: DataFetcher, ts_code: str) -> pd.DataFrame:
    """获取十大股东

    注：DataFetcher 暂未封装 top10_holders 接口，这里通过 fetcher._retry_call
    包裹 pro.top10_holders，以复用 DataFetcher 的 _rate_limit（220ms 间隔）
    与重试逻辑；缓存仍由本函数管理（一周有效）。
    """
    cache_key = f"top10_holders_{ts_code}"
    cache_dir = get_cache_dir(fetcher.config)

    cached = load_cache(cache_dir, cache_key, expire_hours=168)  # 缓存一周
    if cached is not None and len(cached) > 0:
        return cached

    try:
        # 直调 pro.top10_holders，但通过 _retry_call 享受速率限制与重试
        df = fetcher._retry_call(fetcher.pro.top10_holders, ts_code=ts_code)
        if df is not None and len(df) > 0:
            save_cache(df, cache_dir, cache_key)
            return df
    except Exception as e:
        logger.debug(f"股东数据缺失 {ts_code}: {e}")

    return pd.DataFrame()


# ════════════════════════════════════════════════════════
# 因子计算函数
# ════════════════════════════════════════════════════════

def score_fundamental(fetcher: DataFetcher, ts_code: str, industry: str, 
                      income: pd.DataFrame, daily_basic: pd.DataFrame) -> Tuple[float, Dict]:
    """
    基本面因子评分（权重40%，满分7.2分）
    
    F1: 赛道属性（15%，满分2分）
        - 行业属主线 +1分
        - 政策支持 +0.5分
        - 行业景气上行 +0.5分
        
    F2: 业绩拐点（15%，满分2分）
        - 营收增速>20% +1分
        - 毛利率稳定/上升 +0.5分
        - PE合理 +0.5分
        
    F3: 市值区间（10%，满分2分）
        - 50-200亿：2分（最佳）
        - 200-500亿：1分（次选）
        - <50亿或>500亿：0分（回避）
    """
    score = 0.0
    detail = {'F1': {}, 'F2': {}, 'F3': {}}
    
    # ── F1: 赛道属性 ──
    f1_score = 0.0
    
    # 检查行业是否属主线
    is_strategic = any(si in industry for si in STRATEGIC_INDUSTRIES)
    if is_strategic:
        f1_score += 1.0
        detail['F1']['strategic_industry'] = True
    
    # 检查是否在主线主题池
    in_theme = False
    for theme, members in TREND_THEMES.items():
        if ts_code in [m + '.SH' if m.startswith('6') else m + '.SZ' for m in members]:
            in_theme = True
            detail['F1']['theme'] = theme
            break
    
    if in_theme:
        f1_score += 0.5
        f1_score = min(f1_score, 2.0)  # 上限2分
    
    # 行业景气度（简化：用营收增速判断）
    if len(income) > 0:
        latest_rev = income.iloc[0].get('revenue', 0)
        prev_rev = income.iloc[1].get('revenue', 0) if len(income) > 1 else 0
        if prev_rev > 0 and latest_rev > prev_rev:
            rev_growth = (latest_rev - prev_rev) / prev_rev
            if rev_growth > 0.15:  # 行业增速>15%
                f1_score += 0.5
                detail['F1']['industry_growth'] = round(rev_growth * 100, 1)
    
    f1_score = min(f1_score, 2.0)
    score += f1_score * 0.75  # 权重调整：15%/20% = 0.75
    detail['F1']['score'] = round(f1_score, 2)
    
    # ── F2: 业绩拐点 ──
    f2_score = 0.0
    
    if len(income) >= 2:
        curr = income.iloc[0]
        prev = income.iloc[1]
        
        # 营收增速
        curr_rev = curr.get('revenue', 0) or 0
        prev_rev = prev.get('revenue', 0) or 0
        if prev_rev > 0:
            rev_yoy = (curr_rev - prev_rev) / prev_rev
            if rev_yoy > 0.2:  # YoY > 20%
                f2_score += 1.0
                detail['F2']['revenue_yoy'] = round(rev_yoy * 100, 1)
        
        # 毛利率
        curr_gp = curr.get('gross_profit', 0) or 0
        prev_gp = prev.get('gross_profit', 0) or 0
        if curr_rev > 0 and prev_rev > 0:
            curr_gm = curr_gp / curr_rev
            prev_gm = prev_gp / prev_rev
            if curr_gm >= prev_gm:  # 毛利率稳定或上升
                f2_score += 0.5
                detail['F2']['gross_margin'] = round(curr_gm * 100, 1)
        
        # PE合理性（从daily_basic获取）
        if len(daily_basic) > 0:
            pe = daily_basic.iloc[0].get('pe', 0) or 0
            if 0 < pe < 50:  # PE合理区间
                f2_score += 0.5
                detail['F2']['pe'] = round(pe, 1)
    
    f2_score = min(f2_score, 2.0)
    score += f2_score * 0.75
    detail['F2']['score'] = round(f2_score, 2)
    
    # ── F3: 市值区间 ──
    f3_score = 0.0
    
    if len(daily_basic) > 0:
        circ_mv = daily_basic.iloc[0].get('circ_mv', 0) or 0  # 流通市值（万元）
        if circ_mv > 0:
            circ_mv_yi = circ_mv / 10000  # 转为亿元
            if 50 <= circ_mv_yi <= 200:  # 最佳区间
                f3_score = 2.0
                detail['F3']['circ_mv'] = round(circ_mv_yi, 1)
            elif 200 < circ_mv_yi <= 500:  # 次选区间
                f3_score = 1.0
                detail['F3']['circ_mv'] = round(circ_mv_yi, 1)
            else:
                detail['F3']['circ_mv'] = round(circ_mv_yi, 1)
    
    score += f3_score * 0.5  # 权重10%
    detail['F3']['score'] = round(f3_score, 2)
    
    return round(score, 2), detail


def score_capital(fetcher: DataFetcher, ts_code: str, moneyflow: pd.DataFrame,
                  daily: pd.DataFrame) -> Tuple[float, Dict]:
    """
    资金面因子评分（权重45%，满分8.1分）
    
    F4: 机构持仓（15%，满分2分）
        - 机构持股>30% +1分
        - 北向/南向净买入 +0.5分
        - 融资余额上升 +0.5分
        
    F5: 资金流向（20%，满分2分）
        - 启动日超大单净流入>5亿 +2分
        - 3日累计净流入>10亿 +1分
        - 连续净流出 = 0分（趋势终结）
        
    F6: 换手率（10%，满分2分）
        - 启动前<3% +1分（筹码锁定）
        - 突破日>5% +1分（资金进场）
        - 连续>10% = 0分（过热）
    """
    score = 0.0
    detail = {'F4': {}, 'F5': {}, 'F6': {}}
    
    if len(daily) < 20:
        return 0.0, detail
    
    latest = daily.iloc[-1]
    latest_date = str(latest['trade_date'])
    
    # ── F4: 机构持仓 ──
    f4_score = 0.0
    
    # 获取股东数据
    holders = get_holder_data(fetcher, ts_code)
    if len(holders) > 0:
        # 统计机构持股比例（简化：前10大股东）
        inst_ratio = 0.0
        for _, row in holders.iterrows():
            holder_name = str(row.get('holder_name', ''))
            # 判断是否为机构（公募、社保、券商等）
            if any(kw in holder_name for kw in ['基金', '社保', '券商', '保险', 'QFII', '北向']):
                inst_ratio += float(row.get('hold_ratio', 0) or 0)
        
        if inst_ratio > 30:
            f4_score += 1.0
            detail['F4']['inst_ratio'] = round(inst_ratio, 1)
    
    # 融资余额变化（需额外接口，暂时跳过）
    
    f4_score = min(f4_score, 2.0)
    score += f4_score * 0.75
    detail['F4']['score'] = round(f4_score, 2)
    
    # ── F5: 资金流向 ──
    f5_score = 0.0
    
    if len(moneyflow) > 0:
        # 找到最大涨幅日作为"启动日"
        daily_sorted = daily.sort_values('pct_chg', ascending=False)
        if len(daily_sorted) > 0:
            launch_date = str(daily_sorted.iloc[0]['trade_date'])
            launch_flow = moneyflow[moneyflow['trade_date'] == int(launch_date)]
            
            if len(launch_flow) > 0:
                buy_elg_vol = float(launch_flow.iloc[0].get('buy_elg_vol', 0) or 0)  # 超大单买入（万手）
                sell_elg_vol = float(launch_flow.iloc[0].get('sell_elg_vol', 0) or 0)
                net_elg_vol = (buy_elg_vol - sell_elg_vol) * 100  # 转为手
                
                # 简化判断：超大单净流入>5亿股（实际需结合价格计算金额）
                if net_elg_vol > 5000:  # 5000万手 ≈ 5亿股（简化）
                    f5_score = 2.0
                    detail['F5']['launch_net_flow'] = round(net_elg_vol / 100, 0)  # 百万手
        
        # 检查最近3日资金流向
        recent_flow = moneyflow.sort_values('trade_date', ascending=False).head(3)
        if len(recent_flow) == 3:
            net_flows = []
            for _, row in recent_flow.iterrows():
                buy_elg = float(row.get('buy_elg_vol', 0) or 0)
                sell_elg = float(row.get('sell_elg_vol', 0) or 0)
                net_flows.append((buy_elg - sell_elg) * 100)
            
            total_net = sum(net_flows)
            if total_net > 10000:  # 3日累计净流入>1亿手（简化）
                f5_score = max(f5_score, 1.0)
                detail['F5']['three_day_net'] = round(total_net / 100, 0)
            
            # 趋势终结信号：连续3日净流出
            if all(nf < 0 for nf in net_flows):
                f5_score = 0.0
                detail['F5']['trend_terminated'] = True
    
    f5_score = min(f5_score, 2.0)
    score += f5_score * 1.0  # 权重20%
    detail['F5']['score'] = round(f5_score, 2)
    
    # ── F6: 换手率 ──
    f6_score = 0.0
    
    # 从daily获取换手率（已合并daily_basic）
    if 'turnover_rate' in daily.columns:
        turnover_series = daily['turnover_rate'].fillna(0)
        
        # 突破日换手率（取最近涨幅最大日）
        max_pct_idx = daily['pct_chg'].idxmax()
        launch_turnover = float(turnover_series.loc[max_pct_idx]) if max_pct_idx < len(turnover_series) else 0.0
        
        if 5 <= launch_turnover <= 10:  # 活跃区间
            f6_score = 1.0
            detail['F6']['launch_turnover'] = round(launch_turnover, 1)
        elif launch_turnover > 10:  # 过热
            detail['F6']['overheated'] = True
        
        # 启动前换手率（前5日均值）
        if len(turnover_series) > 20:
            pre_turnover = float(turnover_series.iloc[-20:-15].mean())
            if pre_turnover < 3 and pre_turnover > 0:  # 筹码锁定
                f6_score += 1.0
                detail['F6']['pre_turnover'] = round(pre_turnover, 1)
    
    f6_score = min(f6_score, 2.0)
    score += f6_score * 0.5  # 权重10%
    detail['F6']['score'] = round(f6_score, 2)
    
    return round(score, 2), detail


def score_technical(daily: pd.DataFrame) -> Tuple[float, Dict]:
    """
    技术面因子评分（权重15%，满分2.7分）
    
    F7: 均线系统（10%，满分2分）
        - MA5/10/20多头排列 +2分
        - 价格在MA20上方 +1分
        - MA5拐头向下 = 0分
        
    F8: 成交量（5%，满分2分）
        - 突破日量比>2 +2分
        - 持续放量 +1分
        
    F9: 技术指标（5%，满分2分）
        - MACD金叉 +1分
        - RSI(6)在50-70 +1分
        - RSI>80或KDJ-J>100 = 0分（过热）
    """
    score = 0.0
    detail = {'F7': {}, 'F8': {}, 'F9': {}}
    
    if len(daily) < 30:
        return 0.0, detail
    
    # ── F7: 均线系统 ──
    f7_score = 0.0
    
    # 计算均线
    close = daily['close']
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    
    latest_close = float(close.iloc[-1])
    latest_ma5 = float(ma5.iloc[-1])
    latest_ma10 = float(ma10.iloc[-1])
    latest_ma20 = float(ma20.iloc[-1])
    
    # 多头排列判断
    if latest_ma5 > latest_ma10 > latest_ma20:
        f7_score += 2.0
        detail['F7']['alignment'] = 'bullish'
    elif latest_close > latest_ma20:
        f7_score += 1.0
        detail['F7']['alignment'] = 'above_ma20'
    
    # MA5斜率判断
    if len(ma5) > 5:
        ma5_slope = (latest_ma5 - float(ma5.iloc[-5])) / 5
        if ma5_slope < 0:
            f7_score = min(f7_score, 0.5)  # 趋势转弱
            detail['F7']['ma5_turning_down'] = True
    
    f7_score = min(f7_score, 2.0)
    score += f7_score * 0.5  # 权重10%
    detail['F7']['score'] = round(f7_score, 2)
    
    # ── F8: 成交量 ──
    f8_score = 0.0
    
    vol = daily['vol']
    vol_ma5 = vol.rolling(5).mean()
    
    # 找突破日（涨幅最大日）
    max_pct_idx = daily['pct_chg'].idxmax()
    launch_vol = float(vol.loc[max_pct_idx])
    pre_vol_ma = float(vol_ma5.loc[max_pct_idx - 1]) if max_pct_idx > 0 else launch_vol
    
    if pre_vol_ma > 0:
        volume_ratio = launch_vol / pre_vol_ma
        if volume_ratio > 2:  # 放量2倍以上
            f8_score += 2.0
            detail['F8']['volume_ratio'] = round(volume_ratio, 1)
        elif volume_ratio > 1.5:
            f8_score += 1.0
            detail['F8']['volume_ratio'] = round(volume_ratio, 1)
    
    f8_score = min(f8_score, 2.0)
    score += f8_score * 0.25  # 权重5%
    detail['F8']['score'] = round(f8_score, 2)
    
    # ── F9: 技术指标 ──
    f9_score = 0.0
    
    # 计算RSI(6)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(6).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
    rs = gain / loss.replace(0, 0.0001)
    rsi6 = 100 - (100 / (1 + rs))
    latest_rsi6 = float(rsi6.iloc[-1])
    
    # RSI评分
    if 50 <= latest_rsi6 <= 70:
        f9_score += 1.0
        detail['F9']['rsi6'] = round(latest_rsi6, 1)
    elif latest_rsi6 > 80:
        detail['F9']['overbought'] = True
        f9_score = 0.0
    
    # MACD金叉（简化：用MA5与MA10交叉）
    if len(ma5) > 10:
        if latest_ma5 > latest_ma10 and float(ma5.iloc[-2]) <= float(ma10.iloc[-2]):
            f9_score += 1.0
            detail['F9']['macd_cross'] = 'golden'
    
    f9_score = min(f9_score, 2.0)
    score += f9_score * 0.25  # 权重5%
    detail['F9']['score'] = round(f9_score, 2)
    
    return round(score, 2), detail


# ════════════════════════════════════════════════════════
# 买点判断函数
# ════════════════════════════════════════════════════════

def identify_buy_signal(daily: pd.DataFrame, total_score: float) -> Tuple[str, float]:
    """
    识别买点类型并计算止损价
    
    Returns:
        (buy_signal, stop_loss_price)
        buy_signal: "A"/"B"/"C"/""（无信号）
    """
    if len(daily) < 20 or total_score < 10:
        return "", 0.0
    
    close = daily['close']
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    
    latest_close = float(close.iloc[-1])
    latest_ma5 = float(ma5.iloc[-1])
    latest_ma10 = float(ma10.iloc[-1])
    latest_ma20 = float(ma20.iloc[-1])
    
    # 昨日均线
    prev_ma5 = float(ma5.iloc[-2])
    prev_ma10 = float(ma10.iloc[-2])
    prev_ma20 = float(ma20.iloc[-2])
    
    # 买点A：刚呈多头排列（昨日非多头，今日多头）
    prev_bullish = prev_ma5 > prev_ma10 > prev_ma20
    now_bullish = latest_ma5 > latest_ma10 > latest_ma20
    
    if not prev_bullish and now_bullish and total_score >= 14:
        # 放量突破判断
        vol = daily['vol']
        vol_ratio = float(vol.iloc[-1]) / (vol.iloc[-6:-1].mean() + 0.0001)
        if vol_ratio > 2:
            stop_loss = round(latest_ma10 * 0.97, 2)  # MA10下方3%止损
            return "A", stop_loss
    
    # 买点B：首次回调至MA5或MA10
    if now_bullish and latest_close < latest_ma5 * 1.02:  # 接近MA5
        # 检查是否缩量
        vol = daily['vol']
        recent_vol = vol.iloc[-3:].mean()
        launch_vol = vol.iloc[-10:-5].mean()
        if recent_vol < launch_vol * 0.7:  # 缩量30%以上
            stop_loss = round(latest_ma20 * 0.98, 2)  # MA20下方2%止损
            return "B", stop_loss
    
    # 买点C：缩量整理后再次放量
    if len(daily) > 30:
        # 检查是否突破前高
        prev_high = close.iloc[-30:-5].max()
        if latest_close > prev_high and total_score >= 14:
            vol = daily['vol']
            vol_ratio = float(vol.iloc[-1]) / (vol.iloc[-6:-1].mean() + 0.0001)
            if vol_ratio > 1.5:
                stop_loss = round(latest_ma10 * 0.95, 2)  # MA10下方5%止损
                return "C", stop_loss
    
    return "", 0.0


# ════════════════════════════════════════════════════════
# 主扫描函数
# ════════════════════════════════════════════════════════

def trend_scan(fetcher: DataFetcher, stocks: pd.DataFrame, 
               start_date: str, end_date: str) -> List[TrendResult]:
    """
    全市场趋势扫描
    
    Args:
        fetcher: 数据获取器
        stocks: 股票列表（含ts_code, name, industry）
        start_date: 回溯起始日（用于获取历史数据）
        end_date: 扫描截止日（通常是最新交易日）
    
    Returns:
        List[TrendResult]: 符合趋势的股票列表
    """
    results = []
    
    logger.info(f"趋势扫描启动: {len(stocks)}只股票")
    logger.info(f"数据区间: {start_date} ~ {end_date}")
    
    for idx, row in stocks.iterrows():
        ts_code = row['ts_code']
        name = row.get('name', '')
        industry = row.get('industry', '')
        
        if idx % 50 == 0:
            logger.info(f"进度: {idx}/{len(stocks)}")
        
        try:
            # 获取数据
            daily = get_daily_data(fetcher, ts_code, start_date, end_date)
            if len(daily) < 30:
                continue
            
            moneyflow = fetcher.get_moneyflow_by_code(ts_code, start_date=start_date, end_date=end_date)
            if len(moneyflow) > 0:
                moneyflow = moneyflow.sort_values('trade_date').reset_index(drop=True)
            # daily_basic 按 trade_date 缓存全市场，再过滤到当前股票
            daily_basic_all = fetcher.get_daily_basic(end_date)
            daily_basic = daily_basic_all[daily_basic_all['ts_code'] == ts_code] if len(daily_basic_all) > 0 else pd.DataFrame()
            
            # 获取财务数据（复用DataFetcher）
            income = fetcher.get_income(ts_code)
            
            # 三因子评分
            fund_score, fund_detail = score_fundamental(fetcher, ts_code, industry, income, daily_basic)
            cap_score, cap_detail = score_capital(fetcher, ts_code, moneyflow, daily)
            tech_score, tech_detail = score_technical(daily)
            
            # 总分计算
            total_score = fund_score + cap_score + tech_score
            normalized_score = round(total_score / 18.0 * 100, 1)  # 转为百分制
            
            # 判断趋势强度
            if total_score >= 14:
                trend_status = "strong"
            elif total_score >= 10:
                trend_status = "moderate"
            elif total_score >= 7:
                trend_status = "weak"
            else:
                trend_status = "terminated"
            
            # 买点识别（仅对中等以上趋势）
            buy_signal, stop_loss_price = "", 0.0
            if total_score >= 10:
                buy_signal, stop_loss_price = identify_buy_signal(daily, total_score)
            
            result = TrendResult(
                ts_code=ts_code,
                name=name,
                industry=industry,
                fundamental_score=fund_score,
                capital_score=cap_score,
                technical_score=tech_score,
                total_score=total_score,
                normalized_score=normalized_score,
                trend_status=trend_status,
                buy_signal=buy_signal,
                stop_loss_price=stop_loss_price,
                factors={
                    'F1': TrendFactor('F1', '赛道属性', 0.15, fund_detail.get('F1', {}).get('score', 0), 0, fund_detail.get('F1', {})),
                    'F2': TrendFactor('F2', '业绩拐点', 0.15, fund_detail.get('F2', {}).get('score', 0), 0, fund_detail.get('F2', {})),
                    'F3': TrendFactor('F3', '市值区间', 0.10, fund_detail.get('F3', {}).get('score', 0), 0, fund_detail.get('F3', {})),
                    'F4': TrendFactor('F4', '机构持仓', 0.15, cap_detail.get('F4', {}).get('score', 0), 0, cap_detail.get('F4', {})),
                    'F5': TrendFactor('F5', '资金流向', 0.20, cap_detail.get('F5', {}).get('score', 0), 0, cap_detail.get('F5', {})),
                    'F6': TrendFactor('F6', '换手率', 0.10, cap_detail.get('F6', {}).get('score', 0), 0, cap_detail.get('F6', {})),
                    'F7': TrendFactor('F7', '均线系统', 0.10, tech_detail.get('F7', {}).get('score', 0), 0, tech_detail.get('F7', {})),
                    'F8': TrendFactor('F8', '成交量', 0.05, tech_detail.get('F8', {}).get('score', 0), 0, tech_detail.get('F8', {})),
                    'F9': TrendFactor('F9', '技术指标', 0.05, tech_detail.get('F9', {}).get('score', 0), 0, tech_detail.get('F9', {})),
                },
                raw_data={
                    'latest_close': float(daily.iloc[-1]['close']),
                    'latest_pct_chg': float(daily.iloc[-1]['pct_chg']),
                    'latest_turnover': float(daily.iloc[-1].get('turnover_rate', 0)),
                }
            )
            
            # 仅保留中等以上趋势
            if total_score >= 7:
                results.append(result)
        
        except Exception as e:
            logger.debug(f"扫描失败 {ts_code}: {e}")
        
        # API限速
        time.sleep(0.05)
    
    # 按总分降序排序
    results.sort(key=lambda x: x.total_score, reverse=True)
    
    logger.info(f"扫描完成: {len(results)}只趋势股")
    return results


# ════════════════════════════════════════════════════════
# 输出函数
# ════════════════════════════════════════════════════════

def to_dataframe(results: List[TrendResult]) -> pd.DataFrame:
    """结果转DataFrame"""
    rows = []
    for r in results:
        rows.append({
            'ts_code': r.ts_code,
            'name': r.name,
            'industry': r.industry,
            '总分': r.total_score,
            '标准化分': r.normalized_score,
            '趋势强度': r.trend_status,
            '买点': r.buy_signal,
            '止损价': r.stop_loss_price,
            '基本面分': r.fundamental_score,
            '资金面分': r.capital_score,
            '技术面分': r.technical_score,
            '最新价': r.raw_data.get('latest_close', 0),
            '最新涨跌%': r.raw_data.get('latest_pct_chg', 0),
            '换手率%': r.raw_data.get('latest_turnover', 0),
        })
    
    return pd.DataFrame(rows)


def generate_report(results: List[TrendResult], output_dir: Path) -> Tuple[str, str]:
    """
    生成PDF报告（简化版，返回JSON+CSV）
    
    Returns:
        (csv_path, json_path)
    """
    import json
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成CSV
    df = to_dataframe(results)
    csv_path = output_dir / f"trend_stocks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    
    # 生成JSON（含详细因子）
    json_data = []
    for r in results:
        json_data.append({
            'ts_code': r.ts_code,
            'name': r.name,
            'industry': r.industry,
            'scores': {
                'total': r.total_score,
                'normalized': r.normalized_score,
                'fundamental': r.fundamental_score,
                'capital': r.capital_score,
                'technical': r.technical_score,
            },
            'trend_status': r.trend_status,
            'buy_signal': r.buy_signal,
            'stop_loss': r.stop_loss_price,
            'factors': {k: {'name': v.name, 'score': v.raw_score, 'detail': v.detail} 
                       for k, v in r.factors.items()},
            'raw_data': r.raw_data
        })
    
    json_path = output_dir / f"trend_stocks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    
    logger.info(f"报告已生成: {csv_path}")
    logger.info(f"详情已保存: {json_path}")
    
    return str(csv_path), str(json_path)


# ════════════════════════════════════════════════════════
# 主函数
# ════════════════════════════════════════════════════════

def main():
    """主函数：趋势选股扫描"""
    import argparse
    
    parser = argparse.ArgumentParser(description='趋势性上涨选股模型')
    parser.add_argument('--config', type=str, default='../config.json', help='配置文件路径')
    parser.add_argument('--output', type=str, default='../report_daily', help='输出目录')
    parser.add_argument('--days', type=int, default=60, help='回溯天数')
    args = parser.parse_args()
    
    # 加载配置
    config_path = Path(args.config)
    if not config_path.exists():
        config_path = Path(__file__).parent / 'config.json'
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 初始化数据获取器
    token = config.get('tushare', {}).get('token', '')
    if not token:
        logger.error("未配置Tushare token")
        return
    
    fetcher = DataFetcher(token, config)
    
    # 确定扫描日期范围
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y%m%d')
    
    # 获取股票列表
    logger.info("获取股票列表...")
    stocks = fetcher.get_stock_list()
    if stocks is None or len(stocks) == 0:
        logger.error("无法获取股票列表")
        return
    
    logger.info(f"股票列表: {len(stocks)}只")
    
    # 执行扫描
    results = trend_scan(fetcher, stocks, start_date, end_date)
    
    if len(results) == 0:
        logger.warning("未找到符合趋势的股票")
        return
    
    # 生成报告
    output_dir = Path(args.output)
    csv_path, json_path = generate_report(results, output_dir)
    
    # 输出TOP20
    logger.info("\n" + "="*60)
    logger.info("趋势性上涨选股TOP20")
    logger.info("="*60)
    
    df = to_dataframe(results)
    for i, row in df.head(20).iterrows():
        logger.info(f"{row['name']:10s} | 总分={row['总分']:.1f} | "
                   f"趋势={row['趋势强度']:10s} | 买点={row['买点']} | 止损={row['止损价']}")


if __name__ == '__main__':
    main()
