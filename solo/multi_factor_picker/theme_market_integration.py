"""
主题-市场-ETF 三维融合模块
===========================
从 institution 系统的算法中提取三个关键维度：
1. 主题强度评分 (Theme Strength) — 借鉴 InstitutionThemeEngine 的 multi-factor 主题评分
2. ETF共振评分 (ETF Resonance) — 个股所属主题的ETF趋势强度
3. 市场状态门控 (Market Gate) — 大盘环境对入场信号的调整

数据源：复用 multi_factor_picker 现有的 stk_factor_pro parquet 缓存
"""

import json, os, numpy as np, pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ─── 主题 → ETF 映射（从 theme_config.json 加载） ───
_THEME_ETF_MAP = None
_THEME_ETF_CACHE_TIME = None


def _load_theme_etf_map() -> Dict[str, str]:
    """加载 theme_config.json 中的 主题名→main_etf 映射"""
    global _THEME_ETF_MAP, _THEME_ETF_CACHE_TIME
    now = datetime.now()
    if _THEME_ETF_MAP is not None and _THEME_ETF_CACHE_TIME and (now - _THEME_ETF_CACHE_TIME).seconds < 3600:
        return _THEME_ETF_MAP

    paths = [
        r"D:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json",
        r"D:\mystock\solo\theme_kg_v3\config\theme_config.json",
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            mapping = {}
            for key, val in cfg.items():
                if key.startswith('_'):
                    continue
                cn_name = val.get('name_cn', key)
                etf = val.get('main_etf', '')
                if cn_name and etf:
                    mapping[cn_name] = etf
            _THEME_ETF_MAP = mapping
            _THEME_ETF_CACHE_TIME = now
            return mapping
    return {}


def load_theme_etf_map() -> Dict[str, str]:
    """公开接口：{主题中文名: ETF代码}"""
    return _load_theme_etf_map()


# ═══════════════════════════════════════════════════════════
#  1. 主题强度评分 (借鉴 InstitutionThemeEngine)
# ═══════════════════════════════════════════════════════════

def calc_theme_scores(fetcher, trade_date: str,
                      theme_stock_map: dict = None) -> Dict[str, float]:
    """快速计算每个主题的综合强度分 (0-1)

    直接从JSON的个股评分聚合计算，零额外数据加载:
      - avg_score       (40%) — 主题内个股平均匹配分(归一化)
      - high_score_ratio (30%) — 高分股占比(score>=70)
      - size_factor     (30%) — 主题成分股数量(对数归一化)

    相比 institution 系统的 multi-factor 计算，
    本方法更快（仅聚合已有数据），但趋势/动量维度需个股补充。
    """
    if theme_stock_map is None:
        return {}

    scores = {}
    for theme_name, stocks in theme_stock_map.items():
        codes = [s if isinstance(s, str) else s.get('code', '') for s in stocks]
        scores_list = [s['score'] if isinstance(s, dict) and 'score' in s else 50 for s in stocks]

        if not scores_list:
            continue

        # 1) 平均匹配分 → 主题强度基线
        avg_s = float(np.mean(scores_list))
        norm_avg = min(1.0, avg_s / 100.0)

        # 2) 高分股占比 → 主题质量 (score>=70)
        high_ratio = sum(1 for x in scores_list if x >= 70) / max(len(scores_list), 1)

        # 3) 成分股数量 → 主题活跃度 (对数归一化)
        n = len(codes)
        size_factor = min(1.0, np.log2(n + 1) / 8.0)  # 256只=满分

        # 复合：平均分(40%) + 高分占比(30%) + 活跃度(30%)
        comp = norm_avg * 0.40 + high_ratio * 0.30 + size_factor * 0.30
        scores[theme_name] = round(float(comp), 4)

    return scores


# ═══════════════════════════════════════════════════════════
#  2. ETF共振评分
# ═══════════════════════════════════════════════════════════

def calc_etf_trend_score(fetcher, etf_code: str, trade_date: str) -> float:
    """计算单只ETF的趋势强度 (0-1)

    算法（借鉴 institution 的 _calc_etf_trend）:
      - MA20之上 +0.15
      - MA20>MA60 +0.15
      - 20日正收益 +0.05~0.10
      - 20日新高 +0.10
    """
    if not etf_code:
        return 0.5

    ref_date = datetime.strptime(trade_date, '%Y%m%d')
    start = (ref_date - timedelta(days=250)).strftime('%Y%m%d')

    # 使用 fund_daily 获取ETF日线
    try:
        df = fetcher.get_fund_daily(etf_code, start_date=start, end_date=trade_date)
    except Exception:
        df = None

    if df is None or len(df) < 20:
        return 0.5

    close = df.sort_values('trade_date')['close'].values.astype(float)

    ma20 = pd.Series(close).rolling(20).mean().iloc[-1]
    ma60 = pd.Series(close).rolling(60).mean().iloc[-1] if len(close) >= 60 else ma20

    s = 0.5
    if close[-1] > ma20:
        s += 0.15
    if not np.isnan(ma20) and not np.isnan(ma60) and ma20 > ma60:
        s += 0.15

    ret_20d = close[-1] / close[-min(20, len(close))] - 1
    if ret_20d > 0.05:
        s += 0.10
    elif ret_20d > 0:
        s += 0.05

    if len(close) >= 20:
        n_high = float(np.max(close[-20:]))
        if close[-1] >= n_high * 0.995:
            s += 0.10

    return min(1.0, max(0.0, s))


def calc_stock_etf_resonance(fetcher, ts_code: str, etf_code: str, trade_date: str) -> Dict:
    """计算个股与主题ETF的共振评分

    Returns:
        {'score': 0-1复合分, 'etf_trend': ETF趋势分,
         'corr': 收益率相关系数(近20日), 'vol_synergy': 量能协同}
    """
    result = {'score': 0.5, 'etf_trend': 0.5, 'corr': 0.0, 'vol_synergy': 0.0}

    if not etf_code:
        return result

    etf_score = calc_etf_trend_score(fetcher, etf_code, trade_date)
    result['etf_trend'] = etf_score

    # 个股与ETF收益率相关性
    ref_date = datetime.strptime(trade_date, '%Y%m%d')
    start = (ref_date - timedelta(days=60)).strftime('%Y%m%d')

    stock_df = fetcher.get_stk_factor_pro_range(ts_code, start_date=start, end_date=trade_date)
    try:
        etf_df = fetcher.get_fund_daily(etf_code, start_date=start, end_date=trade_date)
    except Exception:
        etf_df = None

    if stock_df is not None and etf_df is not None and len(stock_df) > 20 and len(etf_df) > 20:
        stock_df = stock_df.sort_values('trade_date')
        etf_df = etf_df.sort_values('trade_date')

        # 对齐日期
        common = pd.merge(
            stock_df[['trade_date', 'close_hfq']].rename(columns={'close_hfq': 's_close'}),
            etf_df[['trade_date', 'close']].rename(columns={'close': 'e_close'}),
            on='trade_date', how='inner'
        )
        if len(common) >= 10:
            s_ret = common['s_close'].pct_change().dropna().values[-20:]
            e_ret = common['e_close'].pct_change().dropna().values[-20:]
            if len(s_ret) >= 5 and len(e_ret) >= 5 and np.std(s_ret) > 1e-8 and np.std(e_ret) > 1e-8:
                corr = float(np.corrcoef(s_ret, e_ret)[0, 1])
                result['corr'] = round(corr, 4)

    # 复合得分 = ETF趋势(70%) + 相关性(30%)
    corr_score = max(0.0, (result['corr'] + 1.0) / 2.0)  # -1~1 映射到 0~1
    result['score'] = round(etf_score * 0.7 + corr_score * 0.3, 4)

    return result


# ═══════════════════════════════════════════════════════════
#  3. 市场状态门控
# ═══════════════════════════════════════════════════════════

def market_state_adjustment(regime_name: str) -> Dict:
    """根据市场状态返回入场调整参数

    借鉴 InstitutionPullbackAlphaV2 的市场状态门控逻辑:
      - 强势/主升浪 → 正常交易，放宽条件
      - 震荡/调整 → 谨慎，收严条件
      - 退潮/主跌 → 不开仓，评分降级

    Returns:
        {'multiplier': float 评分乘数,
         'grade_downgrade': int 降级档位,
         'max_position': str 建议仓位,
         'description': str}
    """
    if any(k in regime_name for k in ['强势', '主升浪', '牛市']):
        return {
            'multiplier': 1.0,        # 评分不折扣
            'grade_downgrade': 0,      # 不降级
            'max_position': '70%',
            'description': '强势市场，正常执行',
        }
    elif any(k in regime_name for k in ['偏强', '上升']):
        return {
            'multiplier': 0.95,
            'grade_downgrade': 0,
            'max_position': '50%',
            'description': '偏强市场，适度参与',
        }
    elif any(k in regime_name for k in ['震荡', '调整', '轮动']):
        return {
            'multiplier': 0.90,
            'grade_downgrade': 1,      # 入场评级降1档
            'max_position': '30%',
            'description': '震荡市场，轻仓试探',
        }
    elif any(k in regime_name for k in ['偏弱', '退潮']):
        return {
            'multiplier': 0.80,
            'grade_downgrade': 2,
            'max_position': '15%',
            'description': '偏弱市场，严格控制仓位',
        }
    else:  # 主跌/恐慌
        return {
            'multiplier': 0.60,
            'grade_downgrade': 3,
            'max_position': '0%',
            'description': '弱势市场，不开新仓',
        }


def apply_market_adjustment(entry_grade: str, adjustment: Dict) -> str:
    """根据市场状态调整入场评级"""
    downgrade = adjustment.get('grade_downgrade', 0)
    if downgrade <= 0:
        return entry_grade

    levels = ['A级最佳买点', 'B级可入场', 'C级观望', 'D级等待']
    try:
        idx = levels.index(entry_grade)
        new_idx = min(idx + downgrade, len(levels) - 1)
        return levels[new_idx]
    except ValueError:
        return entry_grade


# ═══════════════════════════════════════════════════════════
#  4. 综合融合评分
# ═══════════════════════════════════════════════════════════

def compute_boosted_score(base_score: float, theme_score: float,
                           etf_score: float, market_adj: Dict) -> Tuple[float, Dict]:
    """综合融合评分

    公式:
      boosted = base_score × market_multiplier × (1 + theme_boost) × (1 + etf_boost)

    Args:
        base_score: 震荡缩量原始总分 (0-100)
        theme_score: 主题强度分 (0-1)
        etf_score: ETF共振分 (0-1)
        market_adj: market_state_adjustment() 的返回值

    Returns:
        (boosted_score: float, detail: dict)
    """
    multiplier = market_adj.get('multiplier', 1.0)

    # 主题加分（线性映射）：主题分0.8+ → +10%, 0.6+ → +5%, 否则不加
    if theme_score >= 0.80:
        theme_boost = 0.10
    elif theme_score >= 0.60:
        theme_boost = 0.05
    else:
        theme_boost = 0.0

    # ETF加分：趋势强(+0.75) → +8%, 偏多(+0.6) → +4%
    if etf_score >= 0.75:
        etf_boost = 0.08
    elif etf_score >= 0.60:
        etf_boost = 0.04
    else:
        etf_boost = 0.0

    boosted = base_score * multiplier * (1 + theme_boost) * (1 + etf_boost)

    detail = {
        '原始分': round(base_score, 1),
        '市场乘数': multiplier,
        '主题加分': round(theme_boost * 100, 1),
        'ETF加分': round(etf_boost * 100, 1),
        '最终分': round(boosted, 1),
    }

    return round(boosted, 1), detail
