"""
ETF Alpha V5 — 基于成分股V5评分的ETF主题共振扫描
直接从ETF成分股列表运行V5扫描，按ETF分组聚合评分
输出: ETF共振排序（综合排序分↓）
==============================================================
"""
import os
import sys
import json
import time
import pickle
import threading
import requests
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv("d:/mystock/config/.env")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chip_alpha_v5 import ChipAlphaV5Engine, calc_opportunity_score
from chip_alpha_engine_v2 import ChipAlphaEngineV2, _get_trade_dates


# =========================
# V2分析结果缓存（pickle）
# =========================
_V2_CACHE_LOCK = threading.Lock()

def _get_v2_cache_dir(engine_cache_dir: str) -> str:
    """V2分析结果缓存目录（与芯片数据缓存同级但分文件夹）"""
    d = os.path.join(engine_cache_dir, 'v2_analysis')
    os.makedirs(d, exist_ok=True)
    return d

def _v2_cache_path(cache_dir: str, ts_code: str, end_date: str) -> str:
    return os.path.join(cache_dir, f"v2_{ts_code}_{end_date}.pkl")

def _load_v2_cache(cache_dir: str, ts_code: str, end_date: str) -> Optional[Dict]:
    p = _v2_cache_path(cache_dir, ts_code, end_date)
    if not os.path.exists(p):
        return None
    try:
        with _V2_CACHE_LOCK:
            with open(p, 'rb') as f:
                return pickle.load(f)
    except Exception:
        return None

def _save_v2_cache(cache_dir: str, ts_code: str, end_date: str, result: Dict):
    p = _v2_cache_path(cache_dir, ts_code, end_date)
    try:
        with _V2_CACHE_LOCK:
            with open(p + '.tmp', 'wb') as f:
                pickle.dump(result, f)
            os.replace(p + '.tmp', p)
    except Exception:
        pass


# =========================
# ETF持仓权重加载（fund_portfolio + cache）
# =========================
def _load_etf_weights(etf_code: str, end_date: str,
                      cache_dir: str = '') -> Dict[str, float]:
    """
    获取ETF成分股权重 {stock_code: weight_pct}。
    优先读缓存，无缓存则调 fund_portfolio API。
    """
    if not cache_dir:
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chip_cache', 'v2_analysis')
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"weights_{etf_code}_{end_date}.pkl")

    # 读缓存
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                return pickle.load(f)
        except Exception:
            pass

    # 调 API
    import tushare as ts
    try:
        _pro = ts.pro_api()
        df = _pro.fund_portfolio(ts_code=etf_code, end_date=end_date)
        time.sleep(0.15)
        if df is not None and not df.empty and 'stk_mkv_ratio' in df.columns:
            weights = {}
            for _, row in df.iterrows():
                sym = str(row.get('symbol', '')).strip()
                ratio = float(row.get('stk_mkv_ratio', 0)) if pd.notna(row.get('stk_mkv_ratio')) else 0
                if sym and ratio > 0:
                    # 补全后缀
                    if sym.startswith('6') or sym.startswith('9'):
                        sym += '.SH'
                    else:
                        sym += '.SZ'
                    weights[sym] = ratio
            # 缓存
            try:
                with open(cache_path + '.tmp', 'wb') as f:
                    pickle.dump(weights, f)
                os.replace(cache_path + '.tmp', cache_path)
            except Exception:
                pass
            return weights
    except Exception:
        pass
    return {}


# =========================
# ETF Alpha V3.0 — 增强分析引擎
# Rotation · Crowding · Stage Probability · Dual Matrix · Market State
# =========================

def _calc_crowding(etf_df: pd.DataFrame) -> int:
    """
    ETF拥挤度模型。
    因子: 20日涨幅(25%) + 成交额异常(30%) + 资金流速度(25%) + 换手变化(20%)
    返回: 0(Low) / 1(Medium) / 2(High)
    """
    if etf_df is None or len(etf_df) < 20:
        return 0
    last = etf_df.iloc[-1]
    score = 0
    # 1. 20日涨幅 (25%)
    ret_20d = float(last.get('ret_20d', 0))
    if ret_20d > 25:   score += 25
    elif ret_20d > 15: score += 15
    elif ret_20d > 8:  score += 8

    # 2. 成交额异常 (30%) — 近5日均额 / 前20日均额
    amt = etf_df['amount'].values.astype(float)
    if len(amt) >= 20:
        recent_amt = np.mean(amt[-5:])
        prior_amt = np.mean(amt[-20:-5])
        amt_ratio = recent_amt / prior_amt if prior_amt > 0 else 1
        if amt_ratio > 2.5:   score += 30
        elif amt_ratio > 1.8: score += 20
        elif amt_ratio > 1.3: score += 10

    # 3. 资金流速度 (25%) — 使用vol_ratio近似
    vol = etf_df['volume'].values.astype(float)
    if len(vol) >= 20:
        recent_vol = np.mean(vol[-5:])
        prior_vol = np.mean(vol[-20:-5])
        vol_ratio = recent_vol / prior_vol if prior_vol > 0 else 1
        if vol_ratio > 2.0:   score += 25
        elif vol_ratio > 1.5: score += 15
        elif vol_ratio > 1.2: score += 8

    # 4. 换手变化 (20%) — turnover_rate 近5日均值 vs 前20日
    if 'turnover_rate' in etf_df.columns:
        tr = etf_df['turnover_rate'].values.astype(float)
        if len(tr) >= 20:
            recent_tr = np.mean(tr[-5:])
            prior_tr = np.mean(tr[-20:-5])
            tr_ratio = recent_tr / prior_tr if prior_tr > 0 else 1
            if tr_ratio > 2.0:   score += 20
            elif tr_ratio > 1.5: score += 12
            elif tr_ratio > 1.2: score += 6
    else:
        score += 10  # 无换手数据时中性

    if score >= 60:   return 2   # High
    elif score >= 30: return 1   # Medium
    return 0                      # Low


def _calc_next_stage_probs(stage: str, eos: float, breadth: float,
                           etf_trend: float, flow: float, avg_risk: float,
                           etf_df) -> list:
    """计算下一阶段转移概率，返回 [(next_stage, prob%), ...]"""
    from random import Random
    rng = Random(int(eos * 100 + breadth * 10 + flow))
    ret_20d = float(etf_df.iloc[-1].get('ret_20d', 0)) if etf_df is not None and len(etf_df) > 0 else 0

    stage_map = {
        'Birth':      [('Early', 55, 80), ('Expansion', 10, 30), ('Breakdown', 5, 15)],
        'Early':      [('Expansion', 50, 75), ('Acceleration', 10, 30), ('Recovery', 5, 20)],
        'Expansion':  [('Acceleration', 40, 68), ('Climax', 10, 25), ('Distribution', 5, 20)],
        'Acceleration': [('Climax', 35, 55), ('Distribution', 10, 30), ('Expansion', 5, 25)],
        'Climax':     [('Distribution', 40, 60), ('Breakdown', 10, 30), ('Recovery', 5, 15)],
        'Distribution': [('Breakdown', 35, 55), ('Recovery', 10, 30), ('Climax', 0, 10)],
        'Breakdown':  [('Recovery', 30, 50), ('Birth', 5, 20), ('Distribution', 5, 15)],
        'Recovery':   [('Early', 40, 60), ('Expansion', 10, 30), ('Breakdown', 5, 20)],
    }
    defaults = stage_map.get(stage, [('Early', 30, 50)])

    # 根据当前指标调整概率
    adjustments = []
    for ns, lo, hi in defaults:
        base = (lo + hi) / 2
        # EOS上升 → 升级概率加大
        if ns in ('Acceleration', 'Expansion', 'Early'):
            if eos > 75:   base += 12
            elif eos > 65: base += 6
            elif eos < 50: base -= 10
        # Breadth上升 → 健康
        if ns in ('Expansion', 'Acceleration'):
            if breadth > 50: base += 8
            elif breadth < 20: base -= 8
        # Flow强 → 上涨延续
        if ns in ('Acceleration', 'Climax'):
            if flow > 60: base += 8
        # Risk高 → 下行风险
        if ns in ('Breakdown', 'Distribution'):
            if avg_risk > 25: base += 10
        # 20日涨幅过大 → 接近Climax
        if ns == 'Climax' and ret_20d > 25:
            base += 10
        # 总概率归一化
        base = max(5, min(85, base))
        adjustments.append((ns, base))

    total = sum(p for _, p in adjustments)
    result = [(ns, round(p / total * 100)) for ns, p in adjustments]
    result.sort(key=lambda x: -x[1])
    return result


def _detect_market_state(etf_df_all: pd.DataFrame) -> str:
    """市场风险状态检测: Risk ON / Neutral / Risk OFF"""
    if etf_df_all is None or etf_df_all.empty:
        return 'Neutral'
    avg_eos = etf_df_all['EOS'].mean()
    top5_avg_eos = etf_df_all['EOS'].head(5).mean()
    avg_risk = etf_df_all['平均风险'].mean() if '平均风险' in etf_df_all.columns else 20
    n_positive = sum(etf_df_all['EOS'] >= 60) if 'EOS' in etf_df_all.columns else 0
    pct_positive = n_positive / len(etf_df_all) * 100

    if avg_eos > 65 and top5_avg_eos > 75 and avg_risk < 18 and pct_positive > 40:
        return 'Risk ON'
    elif avg_eos < 50 and (avg_risk > 25 or pct_positive < 15):
        return 'Risk OFF'
    return 'Neutral'


def _calc_dual_matrix_class(etf_eos: float, alpha_quality: float,
                             oss: np.ndarray = None) -> str:
    """V4.0: ETF-Stock双矩阵分类 A+(主升) / A(主题增强) / B(局部Alpha) / C(无效)
    A+: ETF EOS>75 + 存在OS>80的成分股
    A:  ETF EOS 60-75 + 存在OS>80的成分股
    B:  ETF EOS<65 + Alpha≥65
    C:  其余
    """
    has_high_os = (oss is not None and len(oss) > 0 and np.max(oss) > 80)
    if etf_eos > 75 and has_high_os:
        return 'A+'
    if etf_eos >= 60 and alpha_quality >= 65 and has_high_os:
        return 'A'
    if etf_eos < 65 and alpha_quality >= 65:
        return 'B'
    return 'C'


# 主题去重映射：高度相关的ETF只保留最高EOS
_THEME_GROUPS = [
    ('半导体', '芯片', '科创半导体', '半导体设备'),
    ('人工智能', '软件', '通信', '金融科技'),
    ('创新药', '医疗器械', '医药'),
    ('消费', '食品饮料', '酒', '家电'),
    ('新能源', '光伏', '储能', '电池', '新能源车'),
    ('军工', '航空航天'),
    ('化工', '有色金属'),
    ('煤炭', '钢铁'),
    ('证券', '银行', '红利'),
    ('机器人', '工业母机'),
    ('电力', '电网设备'),
    ('消费电子', '游戏'),
]

def _theme_dedup(etf_df: pd.DataFrame) -> pd.DataFrame:
    """按主题分组去重，每组只保留最高EOS的ETF"""
    if etf_df.empty:
        return etf_df
    df = etf_df.copy().sort_values('EOS', ascending=False)
    keep = []
    excluded_names = set()
    for _, row in df.iterrows():
        name = row['ETF']
        if name in excluded_names:
            continue
        keep.append(name)
        # 找出同组所有ETF并排除
        for group in _THEME_GROUPS:
            if name in group:
                for g in group:
                    if g != name:
                        excluded_names.add(g)
                break
    return df[df['ETF'].isin(keep)].reset_index(drop=True)


# =========================
# ETF Alpha V4.0 — 组合分配引擎
# Market Score · Stock Classification · Conviction · Portfolio Allocation
# =========================

def _calc_market_score(etf_df_all: pd.DataFrame) -> int:
    """
    V4.0: Market Score (0-100) — 市场风险量化评分。
    100=最佳环境, 0=最差环境
    因子: 平均EOS(30%) + Top5平均EOS(20%) + 平均风险(25%) + EOS≥60占比(25%)
    """
    if etf_df_all is None or etf_df_all.empty:
        return 50
    avg_eos = float(etf_df_all['EOS'].mean())
    top5_avg = float(etf_df_all['EOS'].head(5).mean()) if len(etf_df_all) >= 5 else avg_eos
    avg_risk = float(etf_df_all['平均风险'].mean()) if '平均风险' in etf_df_all.columns else 20
    pos_pct = float(np.mean(etf_df_all['EOS'] >= 60)) * 100 if 'EOS' in etf_df_all.columns else 0
    score = 0
    score += max(0, min(30, (avg_eos - 30) / 60 * 30))        # 平均EOS (30%)
    score += max(0, min(20, (top5_avg - 40) / 50 * 20))       # Top5平均 (20%)
    score += max(0, min(25, (40 - avg_risk) / 30 * 25))       # 平均风险 (25%)
    score += max(0, min(25, pos_pct / 60 * 25))                # EOS≥60占比 (25%)
    return round(min(max(score, 0), 100))


_STAGE_CONFIDENCE = {
    'Birth': 0.5, 'Early': 0.7, 'Expansion': 0.9,
    'Acceleration': 0.8, 'Climax': 0.4,
    'Distribution': 0.3, 'Breakdown': 0.1, 'Recovery': 0.5,
}

def _classify_stock(stock_os: float, alpha: float, stage: str, risk: float,
                    is_etf_leader: bool) -> str:
    """V4.0: 股票分类 Leader / Core / Emerging / Risk"""
    if is_etf_leader and stock_os >= 75 and stage in ('Expansion', 'Acceleration', 'Early'):
        return 'Leader'
    if stock_os >= 70 and alpha >= 65 and risk <= 15:
        return 'Core'
    if stock_os >= 60 and stage in ('Early', 'Recovery', 'Birth'):
        return 'Emerging'
    if stage in ('Climax', 'Distribution') or risk > 30:
        return 'Risk'
    if stock_os >= 55:
        return 'Core'
    return 'Emerging'


def _calc_risk_adjusted_score(opportunity: float, market_state: str) -> float:
    """PATCH2: Risk Adjusted Score = Opportunity × MarketRiskFactor
    Risk ON=1.0, Neutral=0.8, Risk OFF=0.5
    """
    mf = {'Risk ON': 1.0, 'Neutral': 0.8, 'Risk OFF': 0.5}.get(market_state, 0.8)
    return round(min(opportunity * mf, 100), 1)


# =========================
# ETF Alpha Ultimate — FINAL PATCH 1~5
# =========================

def _calc_opportunity_score(eos: float, stage_confidence: float, breadth: float) -> float:
    """PATCH1: Opportunity Score (0-100) — 主题是否值得投资
    = 50% EOS + 30% StageConfidence + 20% BreadthFactor
    """
    sc_part = stage_confidence * 100
    bf = 0.4
    if breadth > 60:   bf = 1.0
    elif breadth > 40: bf = 0.85
    elif breadth > 20: bf = 0.65
    return round(min(eos * 0.50 + sc_part * 0.30 + bf * 100 * 0.20, 100), 1)


def _calc_execution_score(etf_trend: float, market_state: str,
                          rotation: str, crowding_label: str) -> float:
    """PATCH1: Execution Score (0-100) — 当前是否适合买入
    = 40% Trend + 25% MarketRegime + 20% Rotation + 15% Crowding
    """
    mf = {'Risk ON': 100, 'Neutral': 65, 'Risk OFF': 30}.get(market_state, 65)
    rot_v = {'↑ 加速': 85, '→ 稳定': 65, '→ 放缓': 45, '↓ 衰退': 20}.get(rotation, 50)
    crowd_v = {'Low': 90, 'Medium': 55, 'High': 20}.get(crowding_label, 60)
    return round(min(etf_trend * 0.40 + mf * 0.25 + rot_v * 0.20 + crowd_v * 0.15, 100), 1)


def _calc_position_score(opportunity: float, execution: float) -> float:
    """PATCH1: Position Score = 60% Opportunity + 40% Execution
    (不再用乘法，避免Risk OFF环境下优秀主题归零)
    """
    return round(min(opportunity * 0.60 + execution * 0.40, 100), 1)


def _map_position_alloc(pos_score: float, market_state: str) -> float:
    """PATCH1: Position Score → 仓位百分比映射"""
    if market_state == 'Risk ON':
        if pos_score > 80:   return 0.40  # 30-50% 取中值40%
        elif pos_score > 60: return 0.22  # 15-30% 取22%
        else:                return 0.05  # 0-10% 取5%
    elif market_state == 'Neutral':
        if pos_score > 80:   return 0.30  # 20-40%
        elif pos_score > 60: return 0.15  # 10-20%
        else:                return 0.0
    else:  # Risk OFF
        if pos_score > 80:   return 0.20  # 15-25%
        elif pos_score > 60: return 0.10  # 5-15%
        else:                return 0.0


def _calc_etf_action(eos: float, opportunity: float, position: float,
                     stage: str, market_state: str, breadth: float) -> str:
    """PATCH3: ETF Action 升级
    BUY / ACCUMULATE / HOLD / WATCH / AVOID
    """
    if eos > 80 and position > 80 and stage in ('Early', 'Expansion'):
        return 'BUY'
    if eos > 65 and opportunity > 70 and stage in ('Early', 'Expansion') and market_state != 'Risk ON':
        return 'ACCUMULATE'
    if stage == 'Acceleration':
        return 'HOLD'
    if eos < 50 or stage in ('Distribution', 'Breakdown'):
        return 'AVOID'
    if breadth < 20 or position < 50:
        return 'WATCH'
    if stage in ('Early', 'Expansion', 'Recovery'):
        return 'HOLD'
    return 'WATCH'


def _calc_final_signal(dual_matrix: str, stock_os: float,
                       stock_stage: str, etf_stage: str) -> str:
    """PATCH: 股票最终交易信号（加入ACCUMULATE状态）"""
    if dual_matrix in ('A+', 'A') and stock_os > 80 and stock_stage in ('Early', 'Expansion'):
        return 'BUY'
    if stock_stage in ('Climax', 'Distribution', 'Breakdown'):
        return 'REDUCE'
    if dual_matrix in ('A+', 'A', 'B') and stock_stage == 'Acceleration':
        return 'HOLD'
    if stock_os > 80 and dual_matrix in ('B', 'C'):
        return 'WATCH'
    if stock_stage in ('Early', 'Expansion', 'Recovery'):
        return 'WATCH'
    return 'AVOID'


def _get_trading_leader(hit_stocks: list) -> str:
    """PATCH4: Trading Leader = 40%Momentum + 30%Alpha + 20%Flow + 10%Risk"""
    if not hit_stocks:
        return ''
    def tl_score(s):
        mom = s.get('动量分', 50)
        alpha = s['复合Alpha']
        flow = s.get('资金分', 50)
        risk = 100 - max(s.get('风险分', 20), 0)
        return mom * 0.40 + alpha * 0.30 + flow * 0.20 + risk * 0.10
    best = max(hit_stocks, key=tl_score)
    return best.get('名称', '')


def _get_portfolio_leader(stock_rows: list, etf_leader: str) -> str:
    """PATCH4: Portfolio Leader — 最终组合应该买谁
    = 40% OS + 25% ETF贡献(是否ThemeLeader) + 20% PositionStage + 15% RiskReward
    """
    if not stock_rows:
        return ''
    def pl_score(s):
        os_val = s['Opportunity_Score']
        is_leader = 100 if s.get('名称', '') == etf_leader else 30
        stg = s['趋势阶段']
        stg_val = {'Early': 85, 'Expansion': 75, 'Acceleration': 60,
                   'Recovery': 50, 'Birth': 40, 'Climax': 20,
                   'Distribution': 10, 'Breakdown': 5}.get(stg, 40)
        risk = s.get('风险分', 20)
        rr = max(100 - risk, 10)
        return os_val * 0.40 + is_leader * 0.25 + stg_val * 0.20 + rr * 0.15
    best = max(stock_rows, key=pl_score)
    return best.get('名称', '')


def _build_exit_reasons() -> list:
    """PATCH5: 退出条件清单"""
    return [
        '1. Structure<70',
        '2. 筹码质心跌破20日趋势',
        '3. Flow连续5日下降',
        '4. Expansion→Distribution',
        '5. ETF主题失效(EOS<50或Breadth<20%)',
    ]


def _build_daily_investment_summary(etf_df: pd.DataFrame) -> str:
    """PATCH5: 【今日投资结论】基金经理决策摘要"""
    if etf_df.empty:
        return "  数据不足，无法生成结论\n"
    market_state = etf_df['MarketState'].iloc[0] if 'MarketState' in etf_df.columns else 'Neutral'
    ms = int(etf_df['MarketScore'].iloc[0]) if 'MarketScore' in etf_df.columns else 50

    aplus = etf_df[etf_df['DualMatrix'] == 'A+']['ETF'].tolist() if 'DualMatrix' in etf_df.columns else []
    a_list = etf_df[etf_df['DualMatrix'] == 'A']['ETF'].tolist() if 'DualMatrix' in etf_df.columns else []
    top1 = etf_df.head(1)
    lines = []
    lines.append("【今日投资结论】")

    # 市场状态
    if market_state == 'Risk ON':     lines.append(f"  1. 市场Risk ON(Score={ms}) — 积极配置")
    elif market_state == 'Neutral':   lines.append(f"  1. 市场Neutral(Score={ms}) — 选择性配置")
    else:                              lines.append(f"  1. 市场Risk OFF(Score={ms}) — 严格控制仓位")

    # 核心机会
    if aplus:
        lines.append(f"  2. 核心机会: A+主升 {', '.join(aplus[:3])} — 重点配置")
    elif a_list:
        lines.append(f"  2. 核心机会: A主题增强 {', '.join(a_list[:3])} — 配置龙头")
    else:
        lines.append(f"  2. 核心机会: 当前无A+或A主题，等待信号")

    # 推荐策略
    if not top1.empty:
        t = top1.iloc[0]
        act = t.get('Action', '')
        leader = t.get('Leader', '')
        etf_name = t['ETF']
        if act in ('BUY', 'ACCUMULATE'):
            lines.append(f"  3. 策略: {act} {etf_name}，关注{leader}")
        elif act in ('HOLD',):
            lines.append(f"  3. 策略: 持有{etf_name}，等待Acceleration/Climax信号")
        else:
            lines.append(f"  3. 策略: 观望等待，不急于入场")

    # 主要风险
    risks = []
    crowded = etf_df[etf_df['Crowding'] == 'High']['ETF'].tolist() if 'Crowding' in etf_df.columns else []
    if crowded:   risks.append(f'高拥挤{", ".join(crowded[:2])}')
    if market_state == 'Risk OFF': risks.append('市场Risk OFF')
    if risks:
        lines.append(f"  4. 主要风险: {'; '.join(risks)}")
    else:
        lines.append(f"  4. 主要风险: 当前风险可控")
    return '\n'.join(lines)


def _build_portfolio_ultimate(etf_df: pd.DataFrame, scan_df: pd.DataFrame) -> str:
    """PATCH1+4+5: 组合构建（仓位分段映射 + Portfolio Leader + 退出条件）
    返回格式化字符串
    """
    if etf_df.empty:
        return "  当前无符合条件的组合\n"
    market_state = etf_df['MarketState'].iloc[0] if 'MarketState' in etf_df.columns else 'Neutral'
    lines = []
    if market_state == 'Risk ON':       pos_str = '70%-100%'
    elif market_state == 'Neutral':     pos_str = '40%-70%'
    else:                                pos_str = '0%-40%'

    const_map = load_constituents()
    # 过滤掉 AVOID 的ETF
    etf_filtered = etf_df[etf_df['Action'] != 'AVOID'] if 'Action' in etf_df.columns else etf_df
    top_etfs = etf_filtered.head(5)
    exit_reasons = _build_exit_reasons()
    found = False
    for _, erow in top_etfs.iterrows():
        act = erow.get('Action', '')
        if act == 'AVOID':
            continue
        eos = float(erow['EOS'])
        dual = erow.get('DualMatrix', 'C')
        pos_score = float(erow.get('PositionScore', 0))
        if eos < 50 or dual == 'C':
            continue
        found = True
        # PATCH1: 分段映射仓位
        etf_alloc = _map_position_alloc(pos_score, market_state)
        if etf_alloc <= 0:
            continue

        lines.append(f"  ─────────────────")
        lines.append(f"  组合: {erow['ETF']}")
        lines.append(f"  ETF仓位: {etf_alloc*100:.0f}%  |  PositionScore: {pos_score:.0f}  |  Dual: {dual}")

        # 选股
        etf_code = erow['代码']
        cons = const_map.get(etf_code, [])
        if not cons:
            short = etf_code.split('.')[0]
            for k, v in const_map.items():
                if k.split('.')[0] == short or k == short: cons = v; break
        scan_codes = set(scan_df['代码'].tolist())
        stock_rows = []
        for con in cons:
            if con in scan_codes:
                hit = scan_df[scan_df['代码'] == con]
                if not hit.empty: stock_rows.append(hit.iloc[0])
            else:
                con_short = con.split('.')[0]
                hit2 = scan_df[scan_df['代码'] == con_short]
                if not hit2.empty: stock_rows.append(hit2.iloc[0])

        if stock_rows:
            # PATCH4: Portfolio Leader
            theme_leader = erow.get('Leader', '')
            portfolio_leader = _get_portfolio_leader(stock_rows, theme_leader)
            lines.append(f"  Portfolio Leader: {portfolio_leader}")

            stock_rows.sort(key=lambda r: r['Opportunity_Score'], reverse=True)
            top3 = stock_rows[:3]
            for s in top3:
                name = s.get('名称', '')
                os_val = s['Opportunity_Score']
                stg = s['趋势阶段']
                is_pl = '←PL' if name == portfolio_leader else ''
                fs = _calc_final_signal(dual, os_val, stg, erow.get('Stage', ''))
                stock_alloc = min(etf_alloc / 3, 0.10)
                lines.append(f"  股票: {name}{is_pl}  OS={os_val:.0f}  Stage={stg}  仓位:{stock_alloc*100:.0f}%  信号:{fs}")

        lines.append(f"  买入条件: EOS>60, 市场{market_state}, 双矩阵{dual}")
        lines.append(f"  退出条件:")
        for ex in exit_reasons:
            lines.append(f"    {ex}")

    if not found:
        lines.append("  当前无符合条件的组合")
    lines.append(f"  仓位参考: {pos_str}")
    return '\n'.join(lines)


# =========================
# ETF池
# =========================
ETF_POOL = {
    # === 科技半导体 ===
    '半导体': '512480.SH', '芯片': '159995.SZ', '半导体设备': '159516.SZ',
    '人工智能': '159819.SZ', '软件': '515230.SH', '通信': '515880.SH',
    '消费电子': '159732.SZ', '金融科技': '159851.SZ', '游戏': '159869.SZ',
     '科创半导体': '588170.SH',

    # === 新能源链 ===
    '新能源': '516160.SH', '光伏': '515790.SH', '储能': '159566.SZ',
    '电池': '159755.SZ', '新能源车': '515030.SH', '电力': '159611.SZ',
    '电网设备': '561380.SH',

    # === 医药消费 ===
    '创新药': '159992.SZ', '医疗器械': '159883.SZ', '医药': '512010.SH',
    '消费': '159928.SZ', '食品饮料': '159736.SZ', '酒': '512690.SH',
    '家电': '159996.SZ',

    # === 周期制造 ===
    '化工': '159870.SZ', '有色金属': '516650.SH', '煤炭': '515220.SH',
    '钢铁': '515210.SH', '军工': '512660.SH', '航空航天': '159227.SZ',
    '机器人': '562500.SH', '工业母机': '159667.SZ',

    # === 金融红利 ===
    '证券': '512880.SH', '银行': '512800.SH', '红利': '515180.SH',
}


# =========================
# 加载ETF成分股数据
# =========================
_CONST_MAP = None  # {etf_code: [con_code, ...]}

def load_constituents(cache_path: str = "D:/mystock/cache_daily/etf_constituents_all.json") -> dict:
    """加载ETF成分股映射 {etf_code: [股票代码列表]}，自动过滤非股票代码"""
    global _CONST_MAP
    if _CONST_MAP is not None:
        return _CONST_MAP
    if not os.path.exists(cache_path):
        print(f"[ETF] 成分股文件不存在: {cache_path}")
        _CONST_MAP = {}
        return {}
    with open(cache_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 过滤非A股代码（如 159xxx、16xxxx、51xxxx 等基金/ETF代码混入成分股列表）
    import re
    # 允许的股票代码模式：上证(60xxxx/68xxxx) + 深证(00xxxx/30xxxx)
    _STOCK_RE = re.compile(r'^(60\d{4}\.SH|68\d{4}\.SH|00\d{4}\.SZ|30\d{4}\.SZ)$')

    filtered = {}
    total_removed = 0
    for etf_code, cons in data.items():
        # 兼容两种格式：list[str] 或 dict{'constituents':[{con_code,...}]}
        if isinstance(cons, dict) and 'constituents' in cons:
            cons = [c.get('con_code', '') for c in cons['constituents'] if isinstance(c, dict)]
        valid = [c for c in cons if _STOCK_RE.match(c)]
        removed = len(cons) - len(valid)
        total_removed += removed
        if removed > 0:
            invalid_samples = [c for c in cons if not _STOCK_RE.match(c)][:3]
            print(f"  [过滤] {etf_code}: 移除 {removed} 个无效代码 (如 {', '.join(str(c) for c in invalid_samples)})")
        filtered[etf_code] = valid

    _CONST_MAP = filtered
    print(f"[ETF] 加载 {len(_CONST_MAP)} 只ETF成分股映射 (共过滤 {total_removed} 个非股票代码)")
    return _CONST_MAP


def get_constituents(etf_code: str) -> list:
    """获取某只ETF的成分股列表"""
    const_map = load_constituents()
    candidates = const_map.get(etf_code, [])
    if not candidates:
        short = etf_code.split('.')[0] if '.' in etf_code else etf_code
        for k, v in const_map.items():
            if k.split('.')[0] == short:
                candidates = v
                break
    return candidates


def collect_etf_stocks(etf_pool: dict = None) -> list:
    """
    收集所有ETF成分股中的唯一股票列表。
    返回 [{代码, 名称}, ...]，名称留空后续从V2引擎获取。
    """
    if etf_pool is None:
        etf_pool = ETF_POOL
    const_map = load_constituents()
    seen = set()
    stocks = []
    for _, code in etf_pool.items():
        cons = const_map.get(code, [])
        if not cons:
            short = code.split('.')[0]
            for k, v in const_map.items():
                if k.split('.')[0] == short:
                    cons = v
                    break
        for con in cons:
            if con not in seen:
                seen.add(con)
                stocks.append({'代码': con, '名称': ''})
    print(f"[ETF] 共收集 {len(stocks)} 只去重成分股")
    return stocks


# =========================
# 批量预取 + 并行扫描
# =========================

def batch_prefetch_daily_by_date(candidates: list, cache_dir: str,
                                 end_date: str, lookback_days: int = 20):
    """
    按交易日批量获取日线/基本面数据，写入引擎缓存。
    效果: daily 从1335次API调用降至~20次，daily_basic同理。
    共约40次API调用，~5秒完成。
    """
    import tushare as ts
    trade_dates = _get_trade_dates(end_date, lookback_days)
    start_date = trade_dates[0]
    codes_set = {s['代码'] for s in candidates}
    pro = ts.pro_api()

    # 预计算cache文件名
    def _daily_path(code):
        return os.path.join(cache_dir, f"daily_{code}_{start_date}_{end_date}.parquet")
    def _basic_path(code):
        return os.path.join(cache_dir, f"daily_basic_{code}_{start_date}_{end_date}.parquet")

    # 检查哪些股票已有完整缓存
    need_daily = [s for s in candidates if not os.path.exists(_daily_path(s['代码']))]
    need_basic = [s for s in candidates if not os.path.exists(_basic_path(s['代码']))]
    need_codes = {s['代码'] for s in need_daily + need_basic}
    if not need_codes:
        print(f"[预取] 日线缓存已全部就绪 ({len(candidates)}只)，跳过")
        return start_date

    # 少量未缓存(<5只)时跳过批量预取，让引擎走逐股票API即可
    if len(need_codes) < 5:
        print(f"[预取] 仅 {len(need_codes)} 只未缓存({', '.join(list(need_codes)[:3])}...)，"
              f"跳过批量预取，由引擎按需获取")
        return start_date

    # 按交易日分批次汇总，边拉边写缓存（避免中断后丢失全部数据）
    # 内存中只累积当天的数据，写完后释放
    daily_acc = {code: [] for code in need_codes}
    basic_acc = {code: [] for code in need_codes}

    for idx, td in enumerate(trade_dates):
        # 每3个交易日打印一次进度
        if idx % 3 == 0 or idx == len(trade_dates) - 1:
            done_d = sum(1 for s in candidates if os.path.exists(_daily_path(s['代码'])))
            done_b = sum(1 for s in candidates if os.path.exists(_basic_path(s['代码'])))
            print(f"[预取] 日线进度: {done_d}/{len(candidates)} 基本面: {done_b}/{len(candidates)}")

        # daily
        # V2: 优先 daily_cache 表
        df = None
        try:
            from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
            if get_daily_by_date_count(td) > 0:
                df = get_daily_by_date(td)
        except Exception:
            pass
        if df is None or df.empty:
            df = pro.daily(trade_date=td)
            if df is not None and not df.empty:
                try:
                    from stock_cache import batch_insert_daily_cache
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
        if df is not None and len(df) > 0:
            for _, r in df.iterrows():
                code = r['ts_code']
                if code in daily_acc:
                    daily_acc[code].append(r.to_dict())
        time.sleep(0.13)

        # daily_basic
        df_b = pro.daily_basic(trade_date=td)
        if df_b is not None and len(df_b) > 0:
            for _, r in df_b.iterrows():
                code = r['ts_code']
                if code in basic_acc:
                    basic_acc[code].append(r.to_dict())
        time.sleep(0.13)

        # 每处理完一个交易日，立即追加写入缓存（增量写入，中断后不丢失）
        for code, rows_list in daily_acc.items():
            if rows_list:
                df_part = pd.DataFrame(rows_list).sort_values('trade_date').reset_index(drop=True)
                path = _daily_path(code)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                # 如果已有缓存（之前交易日已写入），合并后写回
                if os.path.exists(path):
                    existing = pd.read_parquet(path)
                    df_part = pd.concat([existing, df_part]).drop_duplicates(subset=['trade_date']).sort_values('trade_date').reset_index(drop=True)
                df_part.to_parquet(path, index=False)
                daily_acc[code] = []  # 清空内存，下一个交易日重新累积
        for code, rows_list in basic_acc.items():
            if rows_list:
                df_part = pd.DataFrame(rows_list).sort_values('trade_date').reset_index(drop=True)
                path = _basic_path(code)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.exists(path):
                    existing = pd.read_parquet(path)
                    df_part = pd.concat([existing, df_part]).drop_duplicates(subset=['trade_date']).sort_values('trade_date').reset_index(drop=True)
                df_part.to_parquet(path, index=False)
                basic_acc[code] = []  # 清空内存
        # 内存中累积的当天数据已写入，清空并行字典
    return start_date


def scan_etf_stocks(v2_engine, v5_engine, candidates: list,
                    lookback_days: int = 20,
                    end_date: Optional[str] = None,
                    output_csv: str = '',
                    max_workers: int = 8) -> pd.DataFrame:
    """
    批量扫描ETF成分股 — 使用日线批量预取 + 线程池并行加速。
    
    Parameters
    ----------
    max_workers : int
        线程池并发数，默认8。设为1则退化为串行。
    """
    t0 = time.time()

    # Step 0: 准备日期参数
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    end_date = str(end_date).replace('-', '')
    trade_dates = _get_trade_dates(end_date, lookback_days)
    start_date = trade_dates[0]

    # Step 1: 加载股票名称映射（一次API调用）
    import tushare as ts
    try:
        print("[ETF] 加载股票名称映射...")
        stock_basic = ts.pro_api().stock_basic(fields='ts_code,name')
        name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
        print(f"[ETF] 已加载 {len(name_map)} 只股票名称")
    except Exception as e:
        print(f"[ETF] 股票名称加载失败: {e}")
        name_map = {}

    # Step 2: 按交易日批量预取日线 + 基本面数据（写入引擎缓存）
    print("[ETF] 按交易日批量预取日线/基本面数据...")
    batch_prefetch_daily_by_date(candidates, v2_engine.cache_dir,
                                 end_date, lookback_days)
    print(f"[ETF] 日线预取完成，耗时 {time.time()-t0:.0f}s")

    # Step 3: 线程池并行扫描
    total = len(candidates)
    rows = []
    results_lock = threading.Lock()
    pbar_lock = threading.Lock()
    completed = [0]  # 用列表封装以便闭包修改

    def _process_one(ts_code: str, name: str) -> Optional[dict]:
        try:
            # V2分析结果缓存（避免重复CPU因子计算）
            v2_cache_dir = _get_v2_cache_dir(v2_engine.cache_dir)
            v2_result = _load_v2_cache(v2_cache_dir, ts_code, end_date)
            if v2_result is None:
                v2_result = v2_engine.analyze(ts_code, end_date=end_date,
                                              lookback_days=lookback_days)
                _save_v2_cache(v2_cache_dir, ts_code, end_date, v2_result)
            v5_profile = v5_engine.analyze_from_v2(v2_result)
            _os = calc_opportunity_score(v5_profile)
            v5_profile['Opportunity_Score'] = _os['score']

            a = v5_profile.get('alpha', {})
            r = v5_profile.get('risk', {})
            t = v5_profile.get('trend', {})
            d = v5_profile.get('decision', {})
            tr = t.get('transition', {})
            fs = v5_profile.get('raw_factors', {})

            return {
                '代码': ts_code,
                '名称': name or v2_result.get('stock_name', ''),
                '现价': v5_profile.get('current_price', 0),
                '筹码质心': v5_profile.get('chip_center', 0),
                'V2_Score': v5_profile.get('v2_score', 50),
                'V2_Grade': v5_profile.get('v2_grade', 'C'),
                '结构分': a.get('Structure', 50),
                '资金分': a.get('Flow', 50),
                '动量分': a.get('Momentum', 50),
                '复合Alpha': a.get('Composite', 50),
                'Alpha等级': a.get('Grade', 'C'),
                '风险分': r.get('Composite', 50),
                '风险等级': r.get('Level', 'Medium'),
                '趋势阶段': t.get('current_state', ''),
                '阶段描述': t.get('description', ''),
                '下一阶段': tr.get('primary_next', ''),
                '转移概率': tr.get('primary_prob', 0),
                '操作建议': d.get('action', ''),
                '信心度': d.get('confidence', 50),
                'Opportunity_Score': v5_profile.get('Opportunity_Score', 50),
                'CostResilience': fs.get('Resilience', 50),
                'PressureDecay': fs.get('PressureDecay', 50),
                'CRE': fs.get('CRE', 50),
                'ChipMomentum': fs.get('ChipMomentum', 50),
                '20日涨幅': v2_result.get('price_return_20d', 0),
            }
        except Exception as e:
            return None

    def _scan_with_progress(ts_code, name):
        row = _process_one(ts_code, name)
        with pbar_lock:
            completed[0] += 1
            done = completed[0]
            elapsed = time.time() - t0
            avg = elapsed / done if done > 0 else 0
            eta = avg * (total - done)
            name_str = name or ts_code
            if row:
                print(f"[{done}/{total}] {name_str}({ts_code}) "
                      f"V5={row['复合Alpha']:.1f}({row['Alpha等级']}) "
                      f"OS={row['Opportunity_Score']:.0f} "
                      f"风险={row['风险分']:.0f} "
                      f"阶段={row['趋势阶段']}→{row['下一阶段']}({row['转移概率']*100:.0f}%) "
                      f"建议={row['操作建议']} "
                      f"ETA={eta:.0f}s")
            else:
                print(f"[{done}/{total}] {name_str}({ts_code}) 失败 ETA={eta:.0f}s")
        if row:
            with results_lock:
                rows.append(row)

    print(f"[ETF] 线程池并行扫描 {total} 只 (workers={max_workers})...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for stock in candidates:
            ts_code = stock['代码']
            name = stock.get('名称', '') or name_map.get(ts_code, '')
            futures.append(pool.submit(_scan_with_progress, ts_code, name))
        for f in as_completed(futures):
            pass  # 异常已在内部处理

    total_time = time.time() - t0
    print(f"\n完成! 共 {len(rows)}/{total} 只成功, 总耗时 {total_time:.0f}s")

    df = pd.DataFrame(rows)
    if output_csv:
        df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        print(f"扫描结果已保存至: {output_csv}")
    return df


# =========================
# 核心聚合函数
# =========================
# =========================
# EOS 评分系统
# =========================
# EOS = 0.30×ComponentAlpha + 0.20×Breadth + 0.15×ETFTrend
#      + 0.15×Flow + 0.10×LeaderStrength + 0.10×(100−Risk)
# 20日涨幅不进入EOS，仅作回测验证

_ETF_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chip_cache')

def _norm(val, vmin=0, vmax=100, clip=True):
    """min-max归一化到0-100"""
    if vmax <= vmin:
        return 50
    n = (val - vmin) / (vmax - vmin) * 100
    if clip:
        return min(max(n, 0), 100)
    return n


def _fetch_etf_daily(etf_code: str, end_date: str, lookback_days: int = 60):
    """获取ETF自身日线数据（带缓存），返回DataFrame含close/ma20/volume等"""
    # 防御：确保 end_date 不为 None
    if not end_date or end_date == 'None':
        end_date = datetime.now().strftime('%Y%m%d')
    import tushare as ts
    cache_path = os.path.join(_ETF_CACHE_DIR, f"etf_{etf_code}.parquet")
    # 从累积缓存读取
    df = None
    if os.path.exists(cache_path):
        try:
            df = pd.read_parquet(cache_path)
            df['trade_date'] = df['trade_date'].astype(str)
        except:
            df = None

    # 确定缺失日期
    trade_dates = _get_trade_dates(end_date, lookback_days)
    if df is not None and len(df) > 0:
        existing = set(df['trade_date'].unique())
        need = [d for d in trade_dates if d not in existing]
    else:
        need = trade_dates

    if need:
        pro = ts.pro_api()
        start = need[0]
        end_inner = need[-1]
        time.sleep(0.13)  # rate limit
        try:
            new = pro.fund_daily(ts_code=etf_code, start_date=start, end_date=end_inner)
            if new is not None and len(new) > 0:
                new['trade_date'] = new['trade_date'].astype(str)
                if df is not None and len(df) > 0:
                    combined = pd.concat([df, new], ignore_index=True)
                    combined = combined.drop_duplicates(subset=['trade_date']).sort_values('trade_date').reset_index(drop=True)
                else:
                    combined = new.sort_values('trade_date').reset_index(drop=True)
                combined.to_parquet(cache_path, index=False)
                df = combined
        except Exception as e:
            print(f"  [ETF日线] {etf_code} 获取失败: {e}")

    if df is None or len(df) == 0:
        return pd.DataFrame()

    # 计算均线和指标
    df = df.sort_values('trade_date').reset_index(drop=True)
    # close可能叫'close'或其它，统一
    close_col = 'close' if 'close' in df.columns else ('fund_close' if 'fund_close' in df.columns else None)
    if close_col is None:
        return pd.DataFrame()
    closes = df[close_col].values.astype(float)
    df['ma20'] = pd.Series(closes).rolling(window=min(20, len(closes))).mean().values
    df['ma60'] = pd.Series(closes).rolling(window=min(60, len(closes))).mean().values
    df['volume'] = df.get('vol', df.get('fund_vol', 0)).astype(float)
    df['amount'] = df.get('amount', df.get('fund_amount', 0)).astype(float)

    # 涨幅
    if len(df) >= 2:
        df['ret_1d'] = pd.Series(closes).pct_change().values * 100
        df['ret_20d'] = pd.Series(closes).pct_change(periods=min(20, len(closes))).values * 100
        df['ret_60d'] = pd.Series(closes).pct_change(periods=min(60, len(closes))).values * 100
    else:
        df['ret_1d'] = 0
        df['ret_20d'] = 0
        df['ret_60d'] = 0

    return df


def _calc_etf_trend_and_flow(df_etf: pd.DataFrame) -> tuple:
    """
    从ETF日线计算趋势分和资金流分。
    返回 (trend_score, flow_score)
    """
    if df_etf.empty or len(df_etf) < 20:
        return 50, 50

    latest = df_etf.iloc[-1]
    close = float(latest.get('close', latest.get('fund_close', 0)))
    ma20 = float(latest.get('ma20', 0))
    ma60 = float(latest.get('ma60', 0)) if pd.notna(latest.get('ma60')) else 0
    ret_20d = float(latest.get('ret_20d', 0))
    ret_60d = float(latest.get('ret_60d', 0))

    # ─── 趋势分 ───
    # 1. 价格与均线关系 (40%)
    ma_score = 50
    if ma20 > 0:
        dist_ma20 = (close - ma20) / ma20 * 100
        ma_score = _norm(dist_ma20, -10, 15)  # -10%~+15%
    ma_part = ma_score * 0.40

    # 2. 相对强度 (30%)
    rs_score = _norm(ret_20d, -15, 30)
    rs_part = rs_score * 0.30

    # 3. MA20趋势方向 (30%)
    if len(df_etf) >= 25:
        ma20_series = df_etf['ma20'].values
        ma20_slope = (ma20_series[-1] - ma20_series[-5]) / ma20_series[-5] * 100
        ma_dir_score = _norm(ma20_slope * 50, -5, 10)
    else:
        ma_dir_score = 50
    ma_dir_part = ma_dir_score * 0.30

    trend_score = round(ma_part + rs_part + ma_dir_part, 1)

    # ─── 资金流分 ───
    # 1. 成交量趋势 (50%)
    if len(df_etf) >= 20:
        vol_series = df_etf['volume'].values.astype(float)
        recent_vol = np.mean(vol_series[-5:])
        prior_vol = np.mean(vol_series[-20:-5])
        vol_ratio = recent_vol / prior_vol if prior_vol > 0 else 1
        vol_score = _norm((vol_ratio - 1) * 100, -30, 80)
    else:
        vol_score = 50
    vol_part = vol_score * 0.50

    # 2. 成交额趋势 (50%)
    if len(df_etf) >= 20:
        amt_series = df_etf['amount'].values.astype(float)
        recent_amt = np.mean(amt_series[-5:])
        prior_amt = np.mean(amt_series[-20:-5])
        amt_ratio = recent_amt / prior_amt if prior_amt > 0 else 1
        amt_score = _norm((amt_ratio - 1) * 100, -30, 80)
    else:
        amt_score = 50
    amt_part = amt_score * 0.50

    flow_score = round(vol_part + amt_part, 1)

    return trend_score, flow_score


def _detect_etf_stage(trend_score, flow_score, alpha_quality, breadth, avg_risk, etf_trend_data) -> str:
    """
    基于多维数据判断ETF生命周期阶段。
    返回: Birth / Early / Expansion / Acceleration / Climax / Distribution / Breakdown / Recovery
    """
    ret_20d = 0
    ma_state = ''
    if etf_trend_data is not None and len(etf_trend_data) > 0:
        last = etf_trend_data.iloc[-1]
        ret_20d = float(last.get('ret_20d', 0))
        close = float(last.get('close', 0))
        ma20 = float(last.get('ma20', 0))
        if ma20 > 0:
            dist = (close - ma20) / ma20 * 100
            if dist > 5:
                ma_state = '多头'
            elif dist > -3:
                ma_state = '粘合'
            else:
                ma_state = '空头'

    # Breakdown
    if (ret_20d < -10 and avg_risk > 35) or ma_state == '空头':
        return 'Breakdown'
    # Recovery
    if ret_20d > -5 and ma_state == '粘合' and flow_score < 45:
        return 'Recovery'
    # Climax
    if ret_20d > 25 and avg_risk > 20:
        return 'Climax'
    # Distribution
    if alpha_quality < 60 and flow_score < 40 and avg_risk > 15:
        return 'Distribution'
    # Acceleration
    if trend_score > 75 and flow_score > 60 and alpha_quality > 65:
        return 'Acceleration'
    # Expansion
    if breadth > 30 and alpha_quality > 60 and ret_20d > 5:
        return 'Expansion'
    # Early
    if flow_score > 45 and alpha_quality > 55:
        return 'Early'
    # Birth
    if ret_20d > 3 or flow_score > 40:
        return 'Birth'
    return 'Birth'


def _calc_eos_single(etf_name: str, etf_code: str, hit_stocks: list,
                     total_const: int, end_date: str) -> dict:
    """
    V4.0: ETF Allocation Score — EOS评分系统。
    
    EOS = 0.25×AlphaQuality + 0.25×Breadth + 0.20×ETFTrend + 0.15×Flow + 0.10×Leader + 0.05×(100−Risk)
    
    V4.0新增:
      - Breadth分段映射（>60→100, 40~60→85, 20~40→65, <20→40）
      - A+双矩阵（EOS>75 + 股票OS>80）
      - Success/Failure概率
      - Conviction成分（StageConfidence + BreadthFactor）
    """
    n_hit = len(hit_stocks)
    if n_hit == 0:
        return {
            'ETF': etf_name, '代码': etf_code,
            'EOS': 0, 'Stage': '', 'Breadth': 0, 'BreadthScore': 40,
            'Health': 0, 'Leader': '', 'Rotation': '―',
            'Crowding': 'Low', 'NextStage': '', 'DualMatrix': 'C',
            'Action': 'Avoid', 'StageConfidence': 0.5, 'BreadthFactor': 0.4,
            '20日涨幅_验证': 0,
        }

    # ─── 提取基础数据 ───
    alphas = np.array([s['复合Alpha'] for s in hit_stocks])
    risks = np.array([s['风险分'] for s in hit_stocks])
    oss = np.array([s['Opportunity_Score'] for s in hit_stocks])
    names = [s.get('名称', '') for s in hit_stocks]
    codes = [s.get('代码', '') for s in hit_stocks]
    flows = np.array([s.get('资金分', 50) for s in hit_stocks])
    momentums = np.array([s.get('动量分', 50) for s in hit_stocks])

    avg_risk = float(np.mean(risks))
    ret20s = np.array([s.get('20日涨幅', 0) for s in hit_stocks])
    avg_ret20 = float(np.mean(ret20s))

    sorted_idx = np.argsort(alphas)[::-1]
    sorted_a = alphas[sorted_idx]
    n_top5 = min(5, len(sorted_a))
    top5_avg = float(np.mean(sorted_a[:n_top5]))
    med_a = float(np.median(alphas))

    # ─── 1. Alpha Quality Score (0.25) ───
    gt70_pct = float(np.mean(alphas > 70)) * 100
    gt60_pct = float(np.mean(alphas > 60)) * 100
    gt80_pct = float(np.mean(alphas > 80)) * 100
    top5_ret20s = [ret20s[i] for i in sorted_idx[:n_top5]]
    top5_avg_ret20 = float(np.mean(top5_ret20s)) if top5_ret20s else 0
    alpha_trend = max(0, min(100, (top5_avg_ret20 + 30) / 80 * 100))

    alpha_quality = round(min(
        top5_avg * 0.40 + med_a * 0.30 + gt70_pct * 0.20 + alpha_trend * 0.10, 100), 1)

    # ─── 2. Breadth (0.25) — V4.0分段映射 ───
    breadth_raw = (np.sum(alphas > 70) / max(total_const, 1)) * 100
    if breadth_raw > 60:   breadth = 100
    elif breadth_raw > 40: breadth = 85
    elif breadth_raw > 20: breadth = 65
    else:                  breadth = 40

    # ─── 3. ETFTrend (0.20) + Flow (0.15) ───
    etf_df = _fetch_etf_daily(etf_code, end_date)
    trend_score, flow_score = _calc_etf_trend_and_flow(etf_df)
    etf_trend = round(trend_score, 1)
    flow = round(flow_score, 1)

    # ─── 5. LeaderStrength (0.10) ───
    weights = _load_etf_weights(etf_code, end_date)
    max_weight = max(weights.values()) if weights else 0
    best_leader_idx = 0
    best_leader_score = -1
    for i, code in enumerate(codes):
        wt = weights.get(code, 0)
        wt_contrib = min(wt / max_weight * 100, 100) if max_weight > 0 else 50
        leader_candidate = (
            0.30 * oss[i] + 0.25 * wt_contrib +
            0.20 * alphas[i] + 0.15 * flows[i] + 0.10 * momentums[i]
        )
        if leader_candidate > best_leader_score:
            best_leader_score = leader_candidate
            best_leader_idx = i
    leader_strength = round(min(best_leader_score, 100), 1)
    top_stock_name = names[best_leader_idx]

    # ─── 6. Risk (0.05) ───
    risk_score = round(max(100 - avg_risk, 0), 1)

    # ─── EOS ───
    eos = round(0.25 * alpha_quality + 0.25 * breadth +
                0.20 * etf_trend + 0.15 * flow +
                0.10 * leader_strength + 0.05 * risk_score, 1)

    # ─── 生命周期阶段 ───
    stage = _detect_etf_stage(trend_score, flow_score, alpha_quality, breadth, avg_risk, etf_df)

    # ─── Crowding ───
    cval = _calc_crowding(etf_df)
    crowding_label = {0: 'Low', 1: 'Medium', 2: 'High'}.get(cval, 'Low')

    # ─── Next Stage Probabilities + Success/Failure ───
    next_probs = _calc_next_stage_probs(stage, eos, breadth, etf_trend, flow, avg_risk, etf_df)
    next_stage_str = ' | '.join(f'{ns} {p}%' for ns, p in next_probs[:2])
    success_prob = next_probs[0][1] if next_probs else 0
    failure_prob = next_probs[-1][1] if len(next_probs) > 1 else 0

    # ─── Rotation ───
    rot_score = 0
    if etf_trend > 65: rot_score += 15
    elif etf_trend > 50: rot_score += 5
    if breadth > 40: rot_score += 10
    elif breadth > 20: rot_score += 3
    if flow > 55: rot_score += 8
    if stage in ('Acceleration', 'Expansion'): rot_score += 10
    elif stage in ('Climax',): rot_score += 3
    elif stage in ('Distribution', 'Breakdown'): rot_score -= 10
    if rot_score >= 25:       rotation = '↑ 加速'
    elif rot_score >= 10:     rotation = '→ 稳定'
    elif rot_score >= 0:      rotation = '→ 放缓'
    else:                     rotation = '↓ 衰退'

    # ─── Health ───
    health = round(alpha_quality * 0.5 + risk_score * 0.3 + etf_trend * 0.2, 1)

    # ─── Dual Matrix (V4.0 with A+) ───
    dual_matrix = _calc_dual_matrix_class(eos, alpha_quality, oss)

    # ─── Conviction 成分 ───
    sc = _STAGE_CONFIDENCE.get(stage, 0.5)
    bf = 0.4
    if breadth_raw > 60:   bf = 1.0
    elif breadth_raw > 40: bf = 0.85
    elif breadth_raw > 20: bf = 0.65

    # ─── Action (临时: aggregate中会覆盖为PATCH3版本) ───
    if eos > 80 and stage in ('Early', 'Expansion'):
        action = 'BUY'
    elif eos > 65 and stage in ('Early', 'Expansion'):
        action = 'ACCUMULATE'
    elif stage == 'Acceleration':
        action = 'HOLD'
    elif eos < 50 or stage in ('Distribution', 'Breakdown'):
        action = 'AVOID'
    else:
        action = 'WATCH'

    return {
        'ETF': etf_name, '代码': etf_code,
        'EOS': eos, 'Stage': stage,
        'Breadth': round(breadth_raw, 1),
        'BreadthScore': breadth,
        'Health': health,
        'Leader': top_stock_name,
        'Rotation': rotation,
        'Crowding': crowding_label,
        'NextStage': next_stage_str,
        'SuccessProb': success_prob,
        'FailureProb': failure_prob,
        'DualMatrix': dual_matrix,
        'Action': action,
        'ComponentAlpha': alpha_quality,
        'ETFTrend': etf_trend, 'Flow': flow,
        'LeaderStrength': leader_strength, 'RiskScore': risk_score,
        'StageConfidence': sc, 'BreadthFactor': bf,
        'AQ_Top5Avg': round(top5_avg, 1), 'AQ_Median': round(med_a, 1),
        'AQ_gt70': round(gt70_pct, 1), 'AQ_gt60': round(gt60_pct, 1),
        'AQ_gt80': round(gt80_pct, 1), 'AQ_Trend': round(alpha_trend, 1),
        '20日涨幅_验证': round(avg_ret20, 1),
        '平均Alpha': round(float(np.mean(alphas)), 1),
        '平均风险': round(avg_risk, 1),
        '成分股命中': n_hit,
    }


def aggregate_by_etf(scan_df: pd.DataFrame,
                     etf_pool: dict = None,
                     end_date: str = '') -> pd.DataFrame:
    """
    V3.0: EOS聚合 + 市场状态检测 + 主题去重。
    """
    if etf_pool is None:
        etf_pool = ETF_POOL
    const_map = load_constituents()
    scan_codes = set(scan_df['代码'].tolist())
    scan_codes_short = {c.split('.')[0] for c in scan_codes}

    _ed = (end_date or datetime.now().strftime('%Y%m%d')).replace('-', '')

    rows = []
    for etf_name, etf_code in etf_pool.items():
        cons = const_map.get(etf_code, [])
        if not cons:
            short = etf_code.split('.')[0]
            for k, v in const_map.items():
                if k.split('.')[0] == short or k == short:
                    cons = v
                    break
        total_const = len(cons)
        if total_const == 0:
            continue

        hit_stocks = []
        for con in cons:
            if con in scan_codes:
                hit = scan_df[scan_df['代码'] == con]
                if not hit.empty:
                    hit_stocks.append(hit.iloc[0])
            else:
                con_short = con.split('.')[0]
                hit2 = scan_df[scan_df['代码'] == con_short]
                if not hit2.empty:
                    hit_stocks.append(hit2.iloc[0])

        result = _calc_eos_single(etf_name, etf_code, hit_stocks, total_const, _ed)
        rows.append(result)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # V4.0: 市场状态检测 + 量化Market Score
    market_state = _detect_market_state(df)
    df['MarketState'] = market_state
    market_score = _calc_market_score(df)
    df['MarketScore'] = market_score

    # PATCH2: Risk Adjusted Score（替代Conviction）
    ras = []
    for _, r in df.iterrows():
        ras.append(_calc_risk_adjusted_score(
            _calc_opportunity_score(r['EOS'], r['StageConfidence'], r['Breadth']),
            market_state))
    df['RiskAdjusted'] = ras

    # PATCH1: Opportunity / Execution / Position 三层评分
    ops, exes, poss = [], [], []
    for _, r in df.iterrows():
        opp = _calc_opportunity_score(r['EOS'], r['StageConfidence'], r['Breadth'])
        exe = _calc_execution_score(r['ETFTrend'], market_state, r['Rotation'], r['Crowding'])
        pos = _calc_position_score(opp, exe)
        ops.append(opp); exes.append(exe); poss.append(pos)
    df['OpportunityScore'] = ops
    df['ExecutionScore'] = exes
    df['PositionScore'] = poss

    # PATCH3: ETF Action 覆盖（需要market_state）
    actions = []
    for _, r in df.iterrows():
        act = _calc_etf_action(r['EOS'], r['OpportunityScore'], r['PositionScore'],
                               r['Stage'], market_state, r['Breadth'])
        actions.append(act)
    df['Action'] = actions

    # V3.0: 主题去重
    df = _theme_dedup(df)

    df = df.sort_values('PositionScore', ascending=False).reset_index(drop=True)
    return df


def format_etf_report(etf_df: pd.DataFrame, top_n: int = 10) -> str:
    """Ultimate: 生成终端报告（排序: PositionScore ↓，含三层评分）"""
    if etf_df.empty:
        return ""
    lines = []
    lines.append("")
    lines.append("━" * 110)
    lines.append("  ETF Alpha Rotation Ultimate 排名 (PositionScore ↓)")
    lines.append("━" * 110)
    hdr = (f"  {'#':<2}{'ETF':<10}{'EOS':>6}{'Opp':>6}{'Exe':>6}{'Pos':>6}{'RAdj':>6}"
           f"{'Stage':<14}{'Brd':>5}{'Crowd':<7}{'Rot':<8}{'Dual':<4}{'Next':<24}")
    lines.append(hdr)
    lines.append("  " + "─" * 112)
    for i, (_, r) in enumerate(etf_df.head(top_n).iterrows(), 1):
        name = r['ETF']
        eos = r['EOS']
        opp = r.get('OpportunityScore', 0)
        exe = r.get('ExecutionScore', 0)
        pos = r.get('PositionScore', 0)
        radj = r.get('RiskAdjusted', 0)
        stage = str(r.get('Stage', ''))[:10]
        brd = r.get('Breadth', 0)
        crowd = r.get('Crowding', 'Low')
        rot = r.get('Rotation', '―')
        dm = r.get('DualMatrix', '')
        ns = str(r.get('NextStage', ''))[:22]
        act = r.get('Action', '')
        lines.append(f"  {i:<2}{name:<10}{eos:>6.1f}{opp:>6.0f}{exe:>6.0f}{pos:>6.0f}{radj:>6.0f}"
                     f"{stage:<14}{brd:>5.0f}{crowd:<7}{rot:<8}{dm:<4}{ns:<24}{act:<6}")
    lines.append("  " + "─" * 106)
    if 'MarketState' in etf_df.columns and 'MarketScore' in etf_df.columns:
        ms_val = etf_df['MarketScore'].iloc[0]
        mst = etf_df['MarketState'].iloc[0]
        lines.append(f"  市场: {mst}  Score: {ms_val:.0f}/100")
    return '\n'.join(lines)


def save_etf_csv(etf_df: pd.DataFrame, output_path: str):
    """Ultimate: 保存ETF排名CSV"""
    if etf_df.empty:
        print("[ETF] 无数据，跳过保存")
        return
    core_cols = ['ETF', '代码', 'EOS', 'OpportunityScore', 'ExecutionScore', 'PositionScore',
                 'RiskAdjusted', 'Stage', 'Breadth', 'BreadthScore', 'Health',
                 'Leader', 'DualMatrix', 'Crowding',
                 'NextStage', 'SuccessProb', 'FailureProb',
                 'Rotation', 'Action', 'MarketState', 'MarketScore',
                 '20日涨幅_验证', '平均Alpha', '平均风险', '成分股命中',
                 'ComponentAlpha', 'ETFTrend', 'Flow', 'LeaderStrength', 'RiskScore',
                 'StageConfidence', 'BreadthFactor',
                 'AQ_gt60', 'AQ_gt70', 'AQ_gt80',
                 'AQ_Top5Avg', 'AQ_Median', 'AQ_Trend']
    avail = [c for c in core_cols if c in etf_df.columns]
    out = etf_df[avail].copy()
    out.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"[ETF] 已保存: {output_path} ({len(etf_df)} 只ETF)")


def generate_comprehensive_report(etf_df: pd.DataFrame, scan_df: pd.DataFrame,
                                  top_n_etf: int = 5, top_n_stock: int = 5) -> str:
    """
    ETF Alpha Rotation 投研日报
    遵循：风控第一原则、自上而下逻辑锁、状态动态修正
    """
    const_map = load_constituents()
    market_state = etf_df['MarketState'].iloc[0] if 'MarketState' in etf_df.columns else 'Neutral'
    market_score = int(etf_df['MarketScore'].iloc[0]) if 'MarketScore' in etf_df.columns else 50

    # ─── 仓位映射 ───
    if market_state == 'Risk ON':
        pos_str, pos_range, core_cmd, core_tone = '70%-100%', '高仓位', '积极建仓', '进攻'
    elif market_state == 'Neutral':
        pos_str, pos_range, core_cmd, core_tone = '40%-70%', '中仓位', '观察待变', '中性'
    else:
        pos_str, pos_range, core_cmd, core_tone = '0%-40%', '低仓位', '坚决防守', '防御'

    # ─── 过滤 AVOID ───
    etf_display = etf_df[etf_df['Action'] != 'AVOID'] if 'Action' in etf_df.columns else etf_df
    top_etfs = etf_display.head(top_n_etf)

    # ─── 矩阵分类 ───
    dm_col = 'DualMatrix' if 'DualMatrix' in etf_df.columns else None
    aplus = etf_df[etf_df[dm_col] == 'A+']['ETF'].tolist() if dm_col else []
    a_list = etf_df[etf_df[dm_col] == 'A']['ETF'].tolist() if dm_col else []
    b_list = etf_df[etf_df[dm_col] == 'B']['ETF'].tolist() if dm_col else []
    c_list = etf_df[etf_df[dm_col] == 'C']['ETF'].tolist() if dm_col else []
    all_c = len(aplus) + len(a_list) + len(b_list) == 0

    def _fix_stage(stg: str) -> str:
        """规则3: Birth/Recovery 加前缀"""
        if not stg:
            return ''
        stg_str = str(stg)
        if 'Birth' in stg_str:
            return f"假突破风险/{stg_str}"
        if 'Recovery' in stg_str:
            return f"弱反弹/{stg_str}"
        return stg_str

    def _load_stocks_for_etf(etf_code: str) -> list:
        """加载ETF成分股扫描结果"""
        cons = const_map.get(etf_code, [])
        if not cons:
            short = etf_code.split('.')[0]
            for k, v in const_map.items():
                if k.split('.')[0] == short or k == short:
                    cons = v; break
        scan_codes = set(scan_df['代码'].tolist())
        stock_rows = []
        for con in cons:
            if con in scan_codes:
                hit = scan_df[scan_df['代码'] == con]
                if not hit.empty: stock_rows.append(hit.iloc[0])
            else:
                con_short = con.split('.')[0]
                hit2 = scan_df[scan_df['代码'] == con_short]
                if not hit2.empty: stock_rows.append(hit2.iloc[0])
        return stock_rows

    def _trend_arrow(tag: str) -> str:
        """打分: 简化的多空方向提示"""
        m = {'↑': '偏多', '↓': '偏空', '→': '中性', '—': '不明'}
        return m.get(tag, '')

    def _breadth_trend(v: float) -> str:
        if v >= 40: return '↑ 宽度充足'
        if v >= 20: return '→ 宽度收缩'
        return '↓ 宽度不足'

    def _crowd_str(c: str) -> str:
        if c == 'High': return '拥挤 ↑ 风险积聚'
        return '正常'

    def _rotation_str(r: str) -> str:
        if r in ('Accelerating', 'Improving'): return '加速 ↑ 动量增强'
        if r in ('Stable', 'Steady'): return '稳定 →'
        return '衰减 ↓ 轮动弱化'

    lines = []
    lines.append("")
    lines.append("━" * 64)
    lines.append("ETF Alpha Rotation 投研日报")
    lines.append(f"日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("━" * 64)

    # ════════════════════════════════════════
    # 1. 核心投资决策
    # ════════════════════════════════════════
    lines.append("")
    lines.append("## 1. 核心投资决策")
    lines.append(f"- **市场状态**: {market_state} (得分: {market_score}/100)")
    lines.append(f"- **建议仓位**: {pos_str}")
    lines.append(f"- **核心操作指令**: {core_cmd}")
    mainline = f"当前处于{core_tone}状态"
    if market_state == 'Risk OFF':
        mainline += "，市场广度急剧下降，高位股补跌风险突出。核心策略：收缩仓位、严控回撤、等待企稳信号。"
    elif market_state == 'Neutral':
        mainline += "，主线不清晰，轮动加速。核心策略：中等仓位，聚焦B/A类板块局部机会。"
    else:
        mainline += "，主线明确。核心策略：提升仓位，聚焦A+主升板块。"
    if all_c:
        mainline += " **当前全市场无有效主线板块，所有ETF处于C类无效状态。**"
    lines.append(f"- **主线与逻辑**: {mainline}")

    # ════════════════════════════════════════
    # 2. 行业/主题 ETF 矩阵概览
    # ════════════════════════════════════════
    lines.append("")
    lines.append("## 2. 行业/主题 ETF 矩阵概览")
    lines.append(f"| 分级 | 板块/ETF名称 | 代码 | 状态生命周期 | 核心评价与警示 |")
    lines.append(f"| :--- | :--- | :--- | :--- | :--- |")

    # A+ 主升
    if aplus:
        for ename in aplus:
            erow = etf_df[etf_df['ETF'] == ename].iloc[0]
            ecode = erow['代码']
            stg = _fix_stage(erow['Stage'])
            eos = erow['EOS']
            brd = _breadth_trend(erow.get('Breadth', 0))
            lines.append(f"| **A+ 主升** | **{ename}** | {ecode} | {stg} | EOS={eos:.0f}, {brd} |")
    else:
        lines.append(f"| **A+ 主升** | 无 | - | - | - |")

    # A 主题增强
    if a_list:
        for ename in a_list:
            erow = etf_df[etf_df['ETF'] == ename].iloc[0]
            ecode = erow['代码']
            stg = _fix_stage(erow['Stage'])
            eos = erow['EOS']
            brd = _breadth_trend(erow.get('Breadth', 0))
            lines.append(f"| **A 主题增强** | **{ename}** | {ecode} | {stg} | EOS={eos:.0f}, {brd} |")
    else:
        lines.append(f"| **A 主题增强** | 无 | - | - | - |")

    # B 局部Alpha
    if b_list:
        for ename in b_list:
            erow = etf_df[etf_df['ETF'] == ename].iloc[0]
            ecode = erow['代码']
            stg = _fix_stage(erow['Stage'])
            eos = erow['EOS']
            brd = _breadth_trend(erow.get('Breadth', 0))
            crowd = _crowd_str(erow.get('Crowding', 'Low'))
            lines.append(f"| **B 局部Alpha** | **{ename}** | {ecode} | {stg} | EOS={eos:.0f}, {brd}, {crowd} |")
    else:
        lines.append(f"| **B 局部Alpha** | 无 | - | - | - |")

    # C 风险/无效
    if c_list:
        for ename in c_list:
            erow = etf_df[etf_df['ETF'] == ename].iloc[0]
            ecode = erow['代码']
            stg = _fix_stage(erow['Stage'])
            eos = erow['EOS']
            rot = _rotation_str(erow.get('Rotation', '―'))
            lines.append(f"| **C 风险/无效** | {ename} | {ecode} | {stg} | EOS={eos:.0f}, {rot}, 严禁追高 |")
    else:
        lines.append(f"| **C 风险/无效** | 无 | - | - | - |")

    # ════════════════════════════════════════
    # 3. 龙头标的跟踪
    # ════════════════════════════════════════
    lines.append("")
    lines.append("## 3. 龙头标的跟踪")
    ab_etfs = [e for e in aplus + a_list + b_list if e in etf_df['ETF'].values]
    if ab_etfs:
        for ename in ab_etfs:
            erow = etf_df[etf_df['ETF'] == ename].iloc[0]
            ecode = erow['代码']
            stg = _fix_stage(erow['Stage'])
            theme_ld = erow.get('Leader', '未识别')
            stock_rows = _load_stocks_for_etf(ecode)
            trade_ld = _get_trading_leader(stock_rows) if stock_rows else '未识别'
            lines.append(f"- **{ename}({ecode})**：产业龙頭：{theme_ld}｜交易龙头：{trade_ld}（{stg}）")
    else:
        # 全C类 → 简要点评避风港/相对韧性标的
        lines.append(f"- 当前全市场均为 C 类无效状态，无明显主线龙头。")
        # 找出评分相对最高的ETF
        if not etf_df.empty:
            top = etf_df.sort_values('EOS', ascending=False).head(3)
            names = [f"{r['ETF']}({r['代码']}, EOS={r['EOS']:.0f})" for _, r in top.iterrows()]
            lines.append(f"- 相对韧性标的：{'、'.join(names)}（仅作为流动性观察，不构成买入建议）")

    # ════════════════════════════════════════
    # 4. 个股 Alpha 精选（受逻辑锁过滤）
    # ════════════════════════════════════════
    lines.append("")
    lines.append("## 4. 个股 Alpha 精选")
    lines.append("> ⚠ **风控硬拦截**：")
    if market_state == 'Risk OFF':
        lines.append("> 当前市场处于 Risk OFF，已触发防守逻辑锁。")
    if all_c:
        lines.append("> 目标 ETF 均为 C 类无效状态，自上而下逻辑拦截生效。")
    lines.append("")

    if all_c or market_state == 'Risk OFF':
        # 全C类或Risk OFF → 无开仓推荐
        lines.append("- **今日开仓推荐**：**无（逻辑硬拦截，防止弱势补跌）**")
        # 底层观察池（仅限极小仓位/左侧跟踪）
        lines.append("- **底层观察池（仅限极小仓位/左侧跟踪，不作买入建议）**：")
        obs_count = 0
        for _, erow in etf_df.head(5).iterrows():
            ecode = erow['代码']
            ename = erow['ETF']
            stg = _fix_stage(erow['Stage'])
            stock_rows = _load_stocks_for_etf(ecode)
            if stock_rows:
                stock_rows.sort(key=lambda r: r['Opportunity_Score'], reverse=True)
                for s in stock_rows[:2]:
                    name_s = s.get('名称', '')
                    os_val = s.get('Opportunity_Score', 0)
                    s_stg = _fix_stage(s.get('趋势阶段', ''))
                    lines.append(f"  - **{name_s}**(所属{ename}) | OS={os_val:.0f} | 状态: {s_stg} | 风险提示: 随板块C类承压")
                    obs_count += 1
                    if obs_count >= 4:
                        break
            if obs_count >= 4:
                break
    else:
        # 有A/B类板块 → 输出推荐
        lines.append("- **今日开仓推荐**：")
        for ename in ab_etfs:
            erow = etf_df[etf_df['ETF'] == ename].iloc[0]
            ecode = erow['代码']
            stg = _fix_stage(erow['Stage'])
            stock_rows = _load_stocks_for_etf(ecode)
            if not stock_rows:
                continue
            stock_rows.sort(key=lambda r: r['Opportunity_Score'], reverse=True)
            top3 = stock_rows[:3]
            for s in top3:
                name_s = s.get('名称', '')
                os_val = s.get('Opportunity_Score', 0)
                alpha = s.get('复合Alpha', 0)
                s_stg = _fix_stage(s.get('趋势阶段', ''))
                raw_act = s.get('操作建议', '')
                lines.append(f"  - **{name_s}**(所属{ename}) | OS={os_val:.0f} | Alpha={alpha:.0f} | 状态: {s_stg} | 信号: {raw_act}")

        # 底层观察池
        lines.append("- **底层观察池（仅限极小仓位/左侧跟踪）**：")
        for _, erow in etf_df.iterrows():
            if erow['ETF'] in ab_etfs:
                continue
            ecode = erow['代码']
            ename = erow['ETF']
            stg = _fix_stage(erow['Stage'])
            stock_rows = _load_stocks_for_etf(ecode)
            if not stock_rows:
                continue
            stock_rows.sort(key=lambda r: r['Opportunity_Score'], reverse=True)
            for s in stock_rows[:1]:
                name_s = s.get('名称', '')
                os_val = s.get('Opportunity_Score', 0)
                s_stg = _fix_stage(s.get('趋势阶段', ''))
                lines.append(f"  - **{name_s}**(所属{ename}) | OS={os_val:.0f} | 状态: {s_stg} | 随板块C类承压")

    # ════════════════════════════════════════
    # 5. 尾部风险与应对策略
    # ════════════════════════════════════════
    lines.append("")
    lines.append("## 5. 尾部风险与应对策略")
    # 防守重点
    risk_items = []
    crowded_etfs = etf_df[etf_df['Crowding'] == 'High']['ETF'].tolist() if 'Crowding' in etf_df.columns else []
    if crowded_etfs:
        risk_items.append(f"高拥挤ETF：{' '.join(crowded_etfs[:3])} → 容量下降，准备减仓")
    if market_state == 'Risk OFF':
        risk_items.append(f"市场广度急剧下降，高位股补跌风险突出，优先保本")
    climax_etfs = etf_df[etf_df['Stage'] == 'Climax']['ETF'].tolist() if 'Stage' in etf_df.columns else []
    if climax_etfs:
        risk_items.append(f"Climax阶段ETF：{' '.join(climax_etfs[:3])} → 情绪过热，随时反转")
    low_breadth_etfs = etf_df[(etf_df['Breadth'] < 15) & (etf_df['EOS'] > 50)]['ETF'].tolist() if 'Breadth' in etf_df.columns else []
    if low_breadth_etfs:
        risk_items.append(f"成分股宽度不足：{' '.join(low_breadth_etfs[:3])} → 仅少数个股撑指数")
    if all_c:
        risk_items.append("全市场C类失效，无安全边际，静待市场企稳")
    if not risk_items:
        risk_items.append("当前无明显尾部风险信号")

    lines.append(f"- **防守重点**：{'；'.join(risk_items)}")

    # 翻多条件
    if market_state == 'Risk OFF':
        lines.append(f"- **翻多条件**：市场分值回升至40分以上，且出现A类主升板块（当前市场评分{market_score}分，距翻多阈值尚有{40-market_score}分差距）")
        lines.append(f"- **短期关注**：若出现宽度回升+Breadth反弹+量能放大，可左侧小仓位试错B类板块龙头")
    elif market_state == 'Neutral':
        lines.append(f"- **翻多条件**：市场分值突破60分，且出现A+主升板块确认主线，可逐步加仓")
        lines.append(f"- **防转空条件**：市场分值跌破30分，或C类板块占比超过70%，需启动防守模式")
    else:
        lines.append(f"- **维持条件**：市场分值保持在60分以上，龙头板块保持Breadth>40%")
        lines.append(f"- **防转弱条件**：若市场分值跌破50分，或出现Climax/高拥挤板块，减仓至中性")

    lines.append("")
    lines.append("━" * 64)
    lines.append("Opportunity = 50%EOS + 30%StageConf + 20%Breadth")
    lines.append("Execution = 40%Trend + 25%Regime + 20%Rotation + 15%Crowding")
    lines.append("Position = 60%Opportunity + 40%Execution")
    lines.append("免责声明: 基于筹码结构指标生成，不构成投资建议。")
    return '\n'.join(lines)


def save_comprehensive_report(report_text: str, output_path: str):
    """保存综合报告"""
    if not report_text.strip():
        print("[ETF] 综合报告为空，跳过保存")
        return
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
    print(f"[ETF] 综合报告已保存: {output_path}")


# =========================
# 微信推送
# =========================
def send_wechat(msg, key):
    """通过 Server酱 推送微信消息"""
    if not key:
        return
    today = datetime.now().strftime('%Y%m%d')
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {"title": f"ETF Alpha V5 综合选股报告 - {today}", "desp": msg}
    try:
        requests.post(url, data=data, timeout=15)
        print("✅ Server酱 已推送")
    except Exception as e:
        print(f"⚠️ Server酱 推送异常: {e}")


def send_pushplus(msg, token):
    """通过 PushPlus 推送微信消息（支持markdown）"""
    if not token:
        return
    today = datetime.now().strftime('%Y%m%d')
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": f"ETF Alpha V5 综合选股报告 - {today}",
        "content": msg,
        "template": "markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()
        if result.get('code') == 200:
            print("✅ PushPlus 已发送")
        else:
            print(f"⚠️ PushPlus 发送失败: {result.get('msg', '未知错误')}")
    except Exception as e:
        print(f"⚠️ PushPlus 异常: {e}")


# =========================
# AI 报告提炼
# =========================

def _call_ai_report(prompt: str, use_flash: bool = True) -> str:
    """AI 报告生成（优先 GLM-5.2 联网搜索，自动 fallback 到 DeepSeek）
    独立实现，不依赖 tushare_quant.py 的导入链。
    """
    zhipu_key = os.getenv('ZHIPU_API_KEY') or os.getenv('GLM_API_KEY')
    ds_key = os.getenv('DEEPSEEK_API_KEY')

    # 优先 GLM-5.2 + 联网搜索
    if zhipu_key:
        try:
            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            headers = {"Authorization": f"Bearer {zhipu_key}", "Content-Type": "application/json"}
            data = {
                "model": "glm-5.2",
                "messages": [
                    {"role": "system", "content": "你是A股顶级投资分析师，严格基于用户提供的数据进行分析，绝不编造任何数据。股票名称和代码必须严格引用用户提供的数据，不得自行修改或臆造。输出简洁精炼，适合手机阅读。"},
                    {"role": "user", "content": prompt}
                ],
                "reasoning_effort": "max",
                "max_tokens": 65536,
                "temperature": 0.1,
                "tools": [{"type": "web_search", "web_search": {"enable": True, "search_result": True}}],
            }
            r = requests.post(url, headers=headers, json=data, timeout=300)
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content']
            print(f"⚠️ GLM 调用失败 ({r.status_code})，回退 DeepSeek")
        except Exception as e:
            print(f"⚠️ GLM 异常: {e}，回退 DeepSeek")

    # Fallback: DeepSeek
    if not ds_key:
        print("⚠️ 无可用 AI API Key")
        return ""
    try:
        url = "https://api.deepseek.com/chat/completions"
        headers = {"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"}
        model = "deepseek-v4-flash" if use_flash else "deepseek-v4-pro"
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是A股顶级投资分析师，严格基于用户提供的数据进行分析，绝不编造任何数据。股票名称和代码必须严格引用用户提供的数据。输出简洁精炼，适合手机阅读。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
        }
        r = requests.post(url, headers=headers, json=data, timeout=120)
        if r.status_code == 200:
            return r.json()['choices'][0]['message']['content']
        print(f"⚠️ DeepSeek 调用失败: {r.text}")
    except Exception as e:
        print(f"⚠️ DeepSeek 异常: {e}")
    return ""


def refine_report_with_ai(report_text: str, report_date: str) -> str:
    """将综合报告交给 AI 提炼，输出手机友好格式"""
    if not report_text.strip():
        return report_text

    prompt = f"""以下是我自己计算的 ETF Alpha Rotation 投研日报：

【报告日期】{report_date}

【评分体系】
- Opportunity Score = 50%EOS + 30%StageConf + 20%Breadth
- Execution Score = 40%Trend + 25%Regime + 20%Rotation + 15%Crowding
- Position Score = 60%Opportunity + 40%Execution
- Risk Adjusted Score = Opportunity × MarketRiskFactor（ON=1.0, Neutral=0.8, OFF=0.5）

【原始数据】
{report_text}

---
请基于以上数据，对原始报告进行精炼，输出适合手机微信阅读的版本。要求：
- 保留原报告5段结构
- 每行不超过45字，适合手机阅读
- 不用表格，用纯文本+简洁符号
- 标题用**加粗**标记
- 必须保留所有ETF代码和股票代码
- 只基于给定数据，绝不编造
- 核心操作指令和风险提示保留原文结论

输出格式：
**ETF Alpha Rotation 投研日报 ({report_date})**

**1、核心投资决策**
- 市场状态：{{直接引用原文}}
- 建议仓位：{{直接引用原文}}
- 核心操作指令：{{直接引用原文}}
- 主线与逻辑：{{一句话精炼}}

**2、ETF 矩阵概览**
按原文格式精炼

**3、龙头标的跟踪**
按原文格式精炼

**4、个股 Alpha 精选**
按原文格式精炼（保留逻辑锁阻断提示）

**5、尾部风险与应对策略**
按原文格式精炼
"""
    result = _call_ai_report(prompt, use_flash=True)
    return result if result else report_text


# =========================
# 主入口
# =========================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='ETF Alpha V5 — 独立扫描+共振排序')
    parser.add_argument('--output', default='report_daily/etf_alpha_v5_ranking.csv',
                        help='ETF排序输出CSV路径 (默认 report_daily/etf_alpha_v5_ranking.csv)')
    parser.add_argument('--scan_output', default='',
                        help='个股扫描结果CSV路径 (默认 auto: 由 --output 派生)')
    parser.add_argument('--top', type=int, default=15,
                        help='终端显示前N只 (默认15)')
    parser.add_argument('--days', type=int, default=20, help='回看天数 (默认20)')
    parser.add_argument('--date', type=str, default='',
                        help='回溯截止日期 YYYYMMDD (默认当天)')
    parser.add_argument('--no_push', action='store_true',
                        help='跳过微信推送 (默认自动推送)')
    parser.add_argument('--workers', type=int, default=8,
                        help='线程池并发数 (默认8, 设为1退化为串行)')
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = args.output if os.path.isabs(args.output) else os.path.join(base_dir, args.output)
    scan_output_path = args.scan_output

    end_date = args.date.strip() or None
    if end_date:
        base, ext = os.path.splitext(output_path)
        output_path = f"{base}_{end_date}{ext}"
        # 自动派生个股扫描输出路径
        if not scan_output_path:
            scan_output_path = output_path.replace('_ranking', '_scan_result')
        print(f"[ETF Alpha V5] 回溯日期: {end_date}")
    else:
        if not scan_output_path:
            scan_output_path = output_path.replace('_ranking', '_scan_result')

    if scan_output_path:
        scan_output_path = scan_output_path if os.path.isabs(scan_output_path) else os.path.join(base_dir, scan_output_path)

    # 确定实际分析的交易日（最近一个交易日）
    _end = end_date or datetime.now().strftime('%Y%m%d')
    _trade_dates = _get_trade_dates(_end, 1)
    report_date = _trade_dates[-1] if _trade_dates else _end

    # Step 1: 收集所有ETF成分股
    print("[ETF Alpha V5] 加载ETF成分股映射...")
    stocks = collect_etf_stocks()
    if not stocks:
        print("[ETF Alpha V5] 无成分股数据，退出")
        return

    # Step 2: 初始化引擎并扫描
    print(f"[ETF Alpha V5] 初始化引擎，扫描 {len(stocks)} 只成分股...")
    v2 = ChipAlphaEngineV2()
    v5 = ChipAlphaV5Engine()

    scan_df = scan_etf_stocks(v2, v5, stocks,
                              lookback_days=args.days,
                              end_date=end_date,
                              output_csv=scan_output_path,
                              max_workers=args.workers)

    if scan_df.empty:
        print("[ETF Alpha V5] 扫描无结果")
        return

    # Step 3: ETF聚合（EOS评分）
    print(f"[ETF Alpha V5] 运行EOS评分聚合...")
    etf_df = aggregate_by_etf(scan_df, end_date=end_date)

    if etf_df.empty:
        print("[ETF Alpha V5] 聚合无结果")
        return

    # Step 4: 报告
    report = format_etf_report(etf_df, top_n=args.top)
    print(report)

    # Step 5: 保存
    save_etf_csv(etf_df, output_path)

    # Step 6: 综合报告（TOP5 ETF × TOP5 个股）+ AI 提炼
    report_text = generate_comprehensive_report(etf_df, scan_df, top_n_etf=5, top_n_stock=5)
    report_path = output_path.replace('_ranking', f'_综合报告_{report_date}')
    save_comprehensive_report(report_text, report_path)
    print(report_text)

    # AI 提炼（适合手机阅读）
    ai_report = ""
    print("\n[AI] 正在调用 AI 提炼报告...")
    try:
        ai_report = refine_report_with_ai(report_text, report_date)
        if ai_report and ai_report != report_text:
            ai_path = output_path.replace('_ranking', f'_AI报告_{report_date}')
            if not ai_path.endswith('.txt'):
                ai_path = ai_path.replace('.csv', '.txt')
            with open(ai_path, 'w', encoding='utf-8') as f:
                f.write(ai_report)
            print(f"[AI] AI报告已保存: {ai_path}")
            print("\n" + "═" * 50)
            print(ai_report)
            print("═" * 50)
        else:
            print("[AI] AI 未返回有效结果，使用原始报告推送")
            ai_report = report_text
    except Exception as e:
        print(f"[AI] AI 调用失败: {e}")
        ai_report = report_text

    # Step 7: 微信推送（推送 AI 提炼版）
    if not args.no_push:
        push_msg = (ai_report if ai_report and ai_report != report_text
                    else report_text).replace('\n', '\n\n')
        send_wechat(push_msg, os.getenv('WECHAT_SCKEY'))
        send_pushplus(push_msg, os.getenv('PUSHPLUS'))

    print(f"[ETF Alpha V5] 完成!")
    return etf_df


if __name__ == '__main__':
    df = main()
