"""
ETF Alpha Engine V2
Institutional ETF + Component Stock Intelligence System

基于ETF本身表现 + ETF成份股Chip Alpha数据，
构建机构级ETF趋势识别、主题轮动和成份股选股系统。

架构:
  ETF Layer → ETF Trend Engine → Component Alpha Engine
  → ETF Quality Model → Lifecycle Engine → Rotation Ranking → Portfolio

==================================================
"""
import os
import sys
import json
import time
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import requests

load_dotenv("d:/mystock/config/.env")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tushare as ts

from chip_alpha_v5 import ChipAlphaV5Engine, calc_opportunity_score
from chip_alpha_engine_v2 import ChipAlphaEngineV2


# ================================================
# 配置 & ETF池
# ================================================
ETF_POOL = {
    '半导体':'512480.SH','芯片':'159995.SZ','半导体设备':'159516.SZ',
    '人工智能':'159819.SZ','软件':'515230.SH','通信':'515880.SH',
    '消费电子':'159732.SZ','金融科技':'159851.SZ','游戏':'159869.SZ',
    '科创半导体':'588170.SH',
    '新能源':'516160.SH','光伏':'515790.SH','储能':'159566.SZ',
    '电池':'159755.SZ','新能源车':'515030.SH','电力':'159611.SZ',
    '电网设备':'561380.SH',
    '创新药':'159992.SZ','医疗器械':'159883.SZ','医药':'512010.SH',
    '消费':'159928.SZ','食品饮料':'159736.SZ','酒':'512690.SH','家电':'159996.SZ',
    '化工':'159870.SZ','有色金属':'516650.SH','煤炭':'515220.SH',
    '钢铁':'515210.SH','军工':'512660.SH','航空航天':'159227.SZ',
    '机器人':'562500.SH','工业母机':'159667.SZ',
    '证券':'512880.SH','银行':'512800.SH','红利':'515180.SH',
}

CONST_MAP = None

def load_constituents(cache_path="D:/mystock/cache_daily/etf_constituents_all.json"):
    global CONST_MAP
    if CONST_MAP is not None:
        return CONST_MAP
    if not os.path.exists(cache_path):
        CONST_MAP = {}
        return {}
    with open(cache_path, 'r', encoding='utf-8') as f:
        CONST_MAP = json.load(f)
    return CONST_MAP

def get_constituents(etf_code):
    const_map = load_constituents()
    cons = const_map.get(etf_code, [])
    if not cons:
        short = etf_code.split('.')[0]
        for k, v in const_map.items():
            if k.split('.')[0] == short:
                cons = v
                break
    return cons


# ================================================
# 辅助工具
# ================================================
def _safe_div(a, b, default=0):
    """安全除法，支持标量和pandas Series"""
    if isinstance(b, pd.Series):
        return a / b.replace(0, float('nan'))
    return a / b if b != 0 else default

def _norm(val, vmin, vmax):
    if vmax == vmin:
        return 50.0
    return (val - vmin) / (vmax - vmin) * 100.0

def _ema(series: list, period: int = 5) -> float:
    """计算指数移动平均"""
    if not series:
        return 0
    k = 2 / (period + 1)
    ema = series[0]
    for v in series[1:]:
        ema = v * k + ema * (1 - k)
    return ema

def _linear_slope(values: list) -> float:
    """线性回归斜率"""
    n = len(values)
    if n < 2:
        return 0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    return _safe_div(num, den, 0)


def init_tushare():
    token = os.getenv('TUSHARE_TOKEN')
    if not token:
        raise ValueError("TUSHARE_TOKEN 环境变量未设置")
    ts.set_token(token)
    return ts.pro_api()


# ================================================
# ETF Alpha Engine V2
# ================================================
class ETFAlphaEngineV2:
    """
    机构级ETF趋势识别+主题轮动+成份股选股系统。

    核心评分:
        ETF Opportunity Score =
            0.35 × ComponentAlphaQuality +
            0.20 × LeaderStrength +
            0.20 × ETFTrend +
            0.15 × ETFFlow +
            0.10 × RiskAdjustment
    """

    # ─── 生命周期状态定义 ───
    LIFECYCLE_STAGES = [
        'Birth', 'Early', 'Expansion', 'Acceleration',
        'Climax', 'Distribution', 'Breakdown', 'Recovery'
    ]

    # ─── 趋势状态: 均线排列 ───
    TREND_MA_STATES = ['多头排列', '多头但发散', '震荡粘合', '空头排列', '空头但收敛']

    # ─── 评分权重 ───
    W_ALPHA = 0.35
    W_LEADER = 0.20
    W_TREND = 0.20
    W_FLOW = 0.15
    W_RISK = 0.10

    def __init__(self, token: Optional[str] = None, verbose: bool = True,
                 weights: Optional[Dict[str, float]] = None):
        self.verbose = verbose
        self.pro = init_tushare() if token is None else ts.pro_api(token)
        self.v2_engine = ChipAlphaEngineV2()
        self.v5_engine = ChipAlphaV5Engine()

        # 评分权重（可覆盖）
        if weights:
            self.W_ALPHA = weights.get('alpha', 0.35)
            self.W_LEADER = weights.get('leader', 0.20)
            self.W_TREND = weights.get('trend', 0.20)
            self.W_FLOW = weights.get('flow', 0.15)
            self.W_RISK = weights.get('risk', 0.10)
        else:
            self.W_ALPHA = 0.35
            self.W_LEADER = 0.20
            self.W_TREND = 0.20
            self.W_FLOW = 0.15
            self.W_RISK = 0.10

        # 缓存: ETF日线, 成分股扫描结果
        self._etf_daily_cache: Dict[str, pd.DataFrame] = {}
        self._component_cache: Dict[str, pd.DataFrame] = {}

        # 历史排名（用于竞争分析）
        self._prev_ranking: Optional[pd.DataFrame] = None

        # 股票名称映射（延迟加载）
        self._name_map: Dict[str, str] = {}
        self._name_map_loaded = False

        self.log("[V2] ETF Alpha Engine V2 初始化完成")

    def _load_name_map(self):
        """加载A股股票名称映射"""
        if self._name_map_loaded:
            return
        try:
            sb = self.pro.stock_basic(fields='ts_code,name')
            self._name_map = dict(zip(sb['ts_code'], sb['name']))
            self._name_map_loaded = True
            self.log(f"  名称映射已加载 ({len(self._name_map)} 只)")
        except Exception as e:
            self.log(f"  [WARN] 名称映射加载失败: {e}")
            self._name_map_loaded = True  # 避免重复失败

    def _get_stock_name(self, ts_code: str) -> str:
        """获取股票中文名称"""
        self._load_name_map()
        name = self._name_map.get(ts_code, '')
        if not name:
            short = ts_code.split('.')[0]
            for k, v in self._name_map.items():
                if k.split('.')[0] == short:
                    name = v
                    break
        return name

    def log(self, msg):
        if self.verbose:
            print(f"[ETFV2] {msg}")

    # ================================================
    # 1. 数据层
    # ================================================

    def fetch_etf_daily(self, etf_code: str, days: int = 120,
                         end_date: str = '') -> pd.DataFrame:
        """获取ETF日线数据（使用 fund_daily 接口）"""
        cache_key = f"{etf_code}_{days}_{end_date}"
        if cache_key in self._etf_daily_cache:
            return self._etf_daily_cache[cache_key]

        end = end_date or datetime.now().strftime('%Y%m%d')
        start_dt = datetime.strptime(end, '%Y%m%d') - timedelta(days=int(days * 1.3))
        start = start_dt.strftime('%Y%m%d')
        try:
            time.sleep(0.12)
            df = self.pro.fund_daily(ts_code=etf_code, start_date=start, end_date=end)
            if df is None or df.empty:
                self.log(f"  [WARN] {etf_code} fund_daily 返回空")
                return pd.DataFrame()
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['trade_date'] = df['trade_date'].astype(str)
            if 'vol' not in df.columns and 'volume' in df.columns:
                df.rename(columns={'volume': 'vol'}, inplace=True)
            need_cols = ['open', 'high', 'low', 'close', 'vol', 'amount']
            missing = [c for c in need_cols if c not in df.columns]
            if missing:
                self.log(f"  [WARN] {etf_code} fund_daily 缺少列: {missing}")
                return pd.DataFrame()

            # 计算常用指标
            closes = df['close'].values
            df['ma5'] = pd.Series(closes).rolling(5).mean().values
            df['ma10'] = pd.Series(closes).rolling(10).mean().values
            df['ma20'] = pd.Series(closes).rolling(20).mean().values
            df['ma60'] = pd.Series(closes).rolling(60).mean().values
            df['ret_5d'] = df['close'].pct_change(5) * 100
            df['ret_20d'] = df['close'].pct_change(20) * 100
            df['ret_60d'] = df['close'].pct_change(60) * 100
            df['vol_ma5'] = pd.Series(df['vol'].values).rolling(5).mean().values
            df['vol_ratio'] = _safe_div(df['vol'], df['vol_ma5'], 1)
            df['amount_ma5'] = pd.Series(df['amount'].values).rolling(5).mean().values

            # 判断均线状态
            ma_states = []
            for i in range(len(df)):
                if i < 20:
                    ma_states.append('')
                    continue
                row = df.iloc[i]
                if row['ma5'] > row['ma10'] > row['ma20']:
                    ma_states.append('多头排列')
                elif row['ma5'] > row['ma10'] and row['ma10'] > row['ma20']:
                    ma_states.append('多头但发散')
                elif row['ma5'] < row['ma10'] and row['ma10'] < row['ma20']:
                    if row['ma5'] > row['ma60']:
                        ma_states.append('震荡粘合')
                    else:
                        ma_states.append('空头排列')
                else:
                    ma_states.append('震荡粘合')
            df['ma_state'] = ma_states

            self._etf_daily_cache[cache_key] = df
            return df
        except Exception as e:
            self.log(f"  [ERROR] {etf_code} 日线获取失败: {e}")
            return pd.DataFrame()

    def load_component_scan(self, etf_code: str, end_date: str = '') -> pd.DataFrame:
        """
        加载ETF成分股的V5扫描结果。
        优先从缓存CSV读取，其次实时扫描。
        """
        cache_key = f"{etf_code}_{end_date}"
        if cache_key in self._component_cache:
            return self._component_cache[cache_key]

        # 尝试从已有的ETF scan result读取
        scan_csv = ''
        if end_date:
            scan_csv = f"report_daily/etf_alpha_v5_scan_result_{end_date}.csv"
        if not scan_csv or not os.path.exists(scan_csv):
            scan_csv = "report_daily/etf_alpha_v5_scan_result.csv"
        if not os.path.exists(scan_csv):
            self.log(f"  [WARN] 扫描结果不存在: {scan_csv}")
            return pd.DataFrame()

        scan_df = pd.read_csv(scan_csv)
        if scan_df.empty:
            return pd.DataFrame()

        # 筛选该ETF的成分股
        cons = get_constituents(etf_code)
        if not cons:
            return pd.DataFrame()

        mask = scan_df['代码'].isin(cons)
        if not mask.any():
            # 尝试短代码匹配
            con_short = {c.split('.')[0] for c in cons}
            mask = scan_df['代码'].isin(con_short)

        result = scan_df[mask].copy()
        self._component_cache[cache_key] = result

        # 补充缺失的名称（先确保列类型为字符串）
        if '名称' in result.columns:
            if result['名称'].dtype != object:
                result['名称'] = result['名称'].astype(object)
            na_mask = result['名称'].isna() | (result['名称'] == '')
            if na_mask.any():
                for idx in result[na_mask].index:
                    code = result.at[idx, '代码']
                    name = self._get_stock_name(code)
                    if name:
                        result.at[idx, '名称'] = name

        return result

    def scan_components_for_etf(self, etf_code: str, end_date: str = '') -> pd.DataFrame:
        """实时扫描ETF成分股（无缓存时使用）"""
        cons = get_constituents(etf_code)
        if not cons:
            return pd.DataFrame()

        rows = []
        for i, con in enumerate(cons):
            try:
                v2_r = self.v2_engine.analyze(con, end_date=end_date or None, lookback_days=20)
                v5_r = self.v5_engine.analyze_from_v2(v2_r)
                os_info = calc_opportunity_score(v5_r)
                a = v5_r.get('alpha', {})
                stock_name = v2_r.get('stock_name', '') or self._get_stock_name(con)
                rows.append({
                    '代码': con,
                    '名称': stock_name,
                    '复合Alpha': a.get('Composite', 50),
                    '结构分': a.get('Structure', 50),
                    '资金分': a.get('Flow', 50),
                    '动量分': a.get('Momentum', 50),
                    'Alpha等级': a.get('Grade', 'C'),
                    '风险分': v5_r.get('risk', {}).get('Composite', 50),
                    'Opportunity_Score': os_info['score'],
                    '筹码质心': v5_r.get('chip_center', 0),
                    '现价': v5_r.get('current_price', 0),
                    '20日涨幅': v2_r.get('price_return_20d', 0),
                    '趋势阶段': v5_r.get('trend', {}).get('current_state', ''),
                    '操作建议': v5_r.get('decision', {}).get('action', ''),
                    '信心度': v5_r.get('decision', {}).get('confidence', 50),
                })
            except Exception as e:
                self.log(f"  [WARN] {con} 扫描失败: {e}")
                continue

        return pd.DataFrame(rows)

    # ================================================
    # 2. ETF Alpha质量模型
    # ================================================

    def calc_component_alpha_quality(self, df_comp: pd.DataFrame) -> Dict:
        """
        成份股Alpha质量 (35%)
          = 0.4 × Top10平均Alpha + 0.3 × Alpha中位数 + 0.3 × Alpha Breadth
        """
        if df_comp.empty or '复合Alpha' not in df_comp.columns:
            return {'score': 50, 'avg_alpha': 0, 'median_alpha': 0, 'breadth': 0, 'top10_avg': 0}

        alphas = df_comp['复合Alpha'].dropna().values
        if len(alphas) == 0:
            return {'score': 50, 'avg_alpha': 0, 'median_alpha': 0, 'breadth': 0, 'top10_avg': 0}

        n_hit = len(alphas)
        total = len(df_comp)
        top10_avg = float(np.mean(sorted(alphas, reverse=True)[:min(10, n_hit)]))
        median_alpha = float(np.median(alphas))
        breadth = sum(1 for a in alphas if a >= 70) / max(total, 1)

        raw = 0.4 * top10_avg + 0.3 * median_alpha + 0.3 * breadth * 100
        score = round(min(max(raw, 0), 100), 1)

        return {
            'score': score,
            'avg_alpha': round(float(np.mean(alphas)), 1),
            'median_alpha': round(median_alpha, 1),
            'breadth': round(breadth * 100, 1),
            'top10_avg': round(top10_avg, 1),
            'n_hit': n_hit,
            'total': total,
        }

    def calc_leader_strength(self, df_comp: pd.DataFrame) -> Dict:
        """
        龙头强度 (20%)
          Leader Score = 0.4 × Composite Alpha + 0.3 × Structure + 0.2 × Flow + 0.1 × Momentum
        输出 Top Leader, Core Stock, Potential Leader 分类
        """
        if df_comp.empty or '复合Alpha' not in df_comp.columns:
            return {'score': 50, 'top_leader': '', 'core_stocks': [], 'potential_leaders': []}

        # 计算每只股票的Leader Score
        scores = []
        for _, row in df_comp.iterrows():
            ls = (
                0.4 * row.get('复合Alpha', 50) +
                0.3 * row.get('结构分', 50) +
                0.2 * row.get('资金分', 50) +
                0.1 * row.get('动量分', 50)
            )
            scores.append((row.get('代码', ''), row.get('名称', ''), ls, row.get('复合Alpha', 50),
                           row.get('风险分', 50), row.get('趋势阶段', ''), row.get('操作建议', '')))

        scores.sort(key=lambda x: x[2], reverse=True)

        leader_score_raw = scores[0][2] if scores else 50
        leader_score = round(min(max(leader_score_raw, 0), 100), 1)

        top_leader = {'代码': scores[0][0], '名称': scores[0][1], 'LeaderScore': round(scores[0][2], 1),
                       'Alpha': round(scores[0][3], 1)} if scores else {}

        # 分类
        core_stocks = []
        potential_leaders = []
        avoid = []
        for code, name, ls, alpha, risk, stage, action in scores:
            item = {'代码': code, '名称': name, 'LeaderScore': round(ls, 1), 'Alpha': round(alpha, 1),
                    '风险': round(risk, 1), '阶段': stage, '建议': action}
            if alpha >= 70 and risk <= 15 and stage in ('Expansion', 'Acceleration'):
                if ls >= 70:
                    continue  # 已作为龙头
                core_stocks.append(item)
            elif alpha >= 60 and stage in ('Birth', 'Early') and ls > 50:
                potential_leaders.append(item)
            elif stage in ('Climax', 'Distribution') or risk > 30:
                avoid.append(item)

        return {
            'score': leader_score,
            'top_leader': top_leader,
            'core_stocks': core_stocks[:5],
            'potential_leaders': potential_leaders[:5],
            'avoid': avoid[:5],
            'n_stocks': len(scores),
        }

    def calc_etf_trend_score(self, df_etf: pd.DataFrame) -> Dict:
        """
        ETF趋势强度 (20%)
        包括: 20日趋势, 60日趋势, 均线状态, 突破状态, 趋势稳定性
        """
        if df_etf.empty or len(df_etf) < 20:
            return {'score': 50, 'trend_20d': 0, 'trend_60d': 0, 'ma_state': '', 'is_breakout': False}

        latest = df_etf.iloc[-1]
        ret_20 = latest.get('ret_20d', 0) or 0
        ret_60 = latest.get('ret_60d', 0) or 0
        ma_state = latest.get('ma_state', '')

        # 20日趋势分（收紧范围，使强趋势获更高分）
        trend20_score = min(max(_norm(ret_20, -15, 30), 0), 100) * 0.30

        # 60日趋势分
        trend60_score = min(max(_norm(ret_60, -20, 50), 0), 100) * 0.20

        # 均线状态分
        ma_scores = {'多头排列': 90, '多头但发散': 70, '震荡粘合': 50, '空头排列': 20, '空头但收敛': 35}
        ma_score = ma_scores.get(ma_state, 50) * 0.20

        # 突破检测: 最近20日有无突破MA20信号
        is_breakout = False
        breakout_score = 0
        recent = df_etf.tail(20)
        if len(recent) >= 5:
            for i in range(1, len(recent)):
                if recent.iloc[i]['close'] > recent.iloc[i]['ma20'] and recent.iloc[i - 1]['close'] <= recent.iloc[i - 1]['ma20']:
                    is_breakout = True
                    break
        breakout_score = 80 if is_breakout else 40
        breakout_part = breakout_score * 0.10

        # ─── 趋势持续性因子 ───
        # 衡量趋势的"厚度": MA20之上持续天数 + 趋势健康度 + 新高频率
        persistence_score = 50
        if len(df_etf) >= 30:
            closes = df_etf['close'].values
            ma20s = df_etf['ma20'].values

            # 1. 连续在MA20之上的天数
            days_above = 0
            for i in range(len(df_etf)-1, -1, -1):
                if closes[i] > ma20s[i]:
                    days_above += 1
                else:
                    break
            ma20_dur = min(days_above / 60, 1.0) * 100

            # 2. 近60日在MA20之上的比率
            n60 = min(60, len(df_etf))
            above_count = sum(1 for i in range(len(df_etf)-n60, len(df_etf)) if closes[i] > ma20s[i])
            ma20_ratio = above_count / n60 * 100

            # 3. 近20日创20日新高的频率
            new_highs = 0
            for i in range(max(0, len(df_etf)-20), len(df_etf)):
                window = closes[max(0, i-20):i+1]
                if closes[i] == max(window):
                    new_highs += 1
            nh_freq = min(new_highs / 5, 1.0) * 100

            persistence_score = 0.4 * ma20_dur + 0.3 * ma20_ratio + 0.3 * nh_freq
            persistence_score = min(max(persistence_score, 0), 100)
        persist_part = persistence_score * 0.20

        score = round(trend20_score + trend60_score + ma_score + breakout_part + persist_part, 1)

        return {
            'score': score,
            'trend_20d': round(float(ret_20), 2),
            'trend_60d': round(float(ret_60), 2),
            'ma_state': ma_state,
            'is_breakout': is_breakout,
            'persistence': round(persistence_score, 1),
        }

    def calc_etf_flow_score(self, df_etf: pd.DataFrame) -> Dict:
        """
        资金流强度 (15%)
        包括: 成交额变化, 量比, 成交持续性
        """
        if df_etf.empty or len(df_etf) < 20:
            return {'score': 50, 'amount_change': 0, 'vol_ratio': 0, 'flow_persistence': 0}

        recent = df_etf.tail(10)
        latest = df_etf.iloc[-1]

        # 成交额变化: 最近5日平均 vs 前5日平均
        if len(df_etf) >= 10:
            recent5_avg = df_etf.tail(5)['amount'].mean()
            prev5_avg = df_etf.tail(10).head(5)['amount'].mean()
            amount_change = _safe_div(recent5_avg - prev5_avg, prev5_avg, 0) * 100
        else:
            amount_change = 0
        amount_score = min(max(_norm(amount_change, -30, 60), 0), 100) * 0.4

        # 量比（收紧范围，使量比>1.5即有较高分数）
        vol_ratio = latest.get('vol_ratio', 1)
        vol_score = min(max(_norm(vol_ratio, 0.5, 2.0), 0), 100) * 0.3

        # 成交持续性: 持续放量天数
        if len(recent) >= 5:
            persistent_days = sum(1 for i in range(-5, 0) if df_etf.iloc[i].get('vol_ratio', 1) > 1.0)
        else:
            persistent_days = 0
        persist_score = min(persistent_days / 5 * 100, 100) * 0.3

        score = round(amount_score + vol_score + persist_score, 1)

        return {
            'score': score,
            'amount_change': round(float(amount_change), 1),
            'vol_ratio': round(float(vol_ratio), 2),
            'persistent_days': persistent_days,
        }

    def calc_risk_adjustment(self, df_comp: pd.DataFrame) -> Dict:
        """
        风险调整 (10%)
          Risk Adjustment = 100 - ETF成份股平均Risk
        """
        if df_comp.empty or '风险分' not in df_comp.columns:
            return {'score': 50, 'avg_risk': 50, 'risk_level': 'Medium'}

        risks = df_comp['风险分'].dropna().values
        if len(risks) == 0:
            return {'score': 50, 'avg_risk': 50, 'risk_level': 'Medium'}

        avg_risk = float(np.mean(risks))
        score = round(max(100 - avg_risk, 0), 1)

        if avg_risk <= 10:
            level = 'Very Low'
        elif avg_risk <= 20:
            level = 'Low'
        elif avg_risk <= 30:
            level = 'Medium'
        elif avg_risk <= 45:
            level = 'High'
        else:
            level = 'Very High'

        return {'score': score, 'avg_risk': round(avg_risk, 1), 'risk_level': level}

    # ================================================
    # 3. ETF 综合评分
    # ================================================

    def calc_etf_alpha_score(self, etf_code: str, etf_name: str = '',
                              end_date: str = '', force_scan: bool = False) -> Dict:
        """
        计算ETF Alpha综合评分

        ETF Opportunity Score =
            0.35 × ComponentAlphaQuality +
            0.20 × LeaderStrength +
            0.20 × ETFTrend +
            0.15 × ETFFlow +
            0.10 × RiskAdjustment
        """
        # 1. 加载数据
        df_etf = self.fetch_etf_daily(etf_code, days=120, end_date=end_date)
        if df_etf.empty:
            return {'etf_code': etf_code, 'etf_name': etf_name, 'score': 0, 'error': '无ETF日线数据'}

        if force_scan:
            df_comp = self.scan_components_for_etf(etf_code, end_date)
        else:
            df_comp = self.load_component_scan(etf_code, end_date)
            if df_comp.empty and end_date:
                df_comp = self.load_component_scan(etf_code, '')
            if df_comp.empty:
                df_comp = self.scan_components_for_etf(etf_code, end_date)

        # 2. 计算各维度
        alpha_quality = self.calc_component_alpha_quality(df_comp)
        leader = self.calc_leader_strength(df_comp)
        trend = self.calc_etf_trend_score(df_etf)
        flow = self.calc_etf_flow_score(df_etf)
        risk_adj = self.calc_risk_adjustment(df_comp)

        # 3. 综合评分
        score = round(
            self.W_ALPHA * alpha_quality['score'] +
            self.W_LEADER * leader['score'] +
            self.W_TREND * trend['score'] +
            self.W_FLOW * flow['score'] +
            self.W_RISK * risk_adj['score'],
            1
        )

        # 评级（校准版：更贴合实际分数分布）
        if score >= 85:
            grade = 'S'
            grade_label = 'S级机会'
        elif score >= 75:
            grade = 'A'
            grade_label = 'A级机会'
        elif score >= 65:
            grade = 'B'
            grade_label = '观察'
        else:
            grade = 'C'
            grade_label = '回避'

        return {
            'etf_code': etf_code,
            'etf_name': etf_name,
            'score': score,
            'grade': grade,
            'grade_label': grade_label,
            'component_alpha_quality': alpha_quality,
            'leader_strength': leader,
            'etf_trend': trend,
            'etf_flow': flow,
            'risk_adjustment': risk_adj,
            'n_components': len(df_comp),
            'end_date': end_date or datetime.now().strftime('%Y%m%d'),
        }

    # ================================================
    # 4. ETF生命周期引擎
    # ================================================

    def detect_lifecycle_stage(self, etf_result: Dict) -> Dict:
        """
        基于ETF评分和趋势数据判断生命周期阶段。

        判断规则:
          Birth:   趋势刚启动, Alpha Breadth < 30%
          Early:   Leader出现, 资金开始进入
          Expansion: 多个成份股Alpha提升, 上涨扩散
          Acceleration: Leader突破, 资金加速
          Climax: 涨幅过快, Risk升高
          Distribution: 成份股Alpha下降, 资金撤离
          Breakdown: 趋势破坏
          Recovery: 从低位回升
        """
        trend = etf_result.get('etf_trend', {})
        alpha_q = etf_result.get('component_alpha_quality', {})
        leader = etf_result.get('leader_strength', {})
        risk = etf_result.get('risk_adjustment', {})
        flow = etf_result.get('etf_flow', {})

        trend_20d = trend.get('trend_20d', 0)
        trend_60d = trend.get('trend_60d', 0)
        breadth = alpha_q.get('breadth', 0)
        avg_alpha = alpha_q.get('avg_alpha', 50)
        is_breakout = trend.get('is_breakout', False)
        avg_risk = risk.get('avg_risk', 50)
        flow_score = flow.get('score', 50)
        ma_state = trend.get('ma_state', '')
        has_leader = bool(leader.get('top_leader', {}).get('代码', '')) if isinstance(leader.get('top_leader'), dict) else False

        # 多条件判断
        score = etf_result.get('score', 0)
        stage = 'Birth'
        reasons = []

        # Breakdown: 趋势破坏
        if (trend_20d < -10 and trend_60d < -15) or avg_risk > 40:
            stage = 'Breakdown'
            reasons.append(f'趋势破坏(20日{trend_20d:.1f}% 风险{avg_risk:.0f})')

        # Recovery: 从低位回升
        elif trend_60d < -10 and trend_20d > -5 and ma_state in ('震荡粘合',):
            stage = 'Recovery'
            reasons.append('从低位回升')

        # Climax: 涨幅过快+风险升高
        elif trend_60d > 30 and avg_risk > 20:
            stage = 'Climax'
            reasons.append(f'涨幅过快(60日{trend_60d:.1f}% 风险{avg_risk:.0f})')

        # Distribution: 成份股Alpha下降+资金撤离
        elif avg_alpha < 60 and flow_score < 40 and avg_risk > 15:
            stage = 'Distribution'
            reasons.append(f'成份股Alpha下降({avg_alpha:.0f}) 资金撤离({flow_score:.0f})')

        # Acceleration: 突破+资金加速+Leader
        elif is_breakout and flow_score > 60 and has_leader and avg_alpha > 65:
            stage = 'Acceleration'
            reasons.append(f'突破+资金加速({flow_score:.0f})')

        # Expansion: 多个成份股Alpha提升+上涨扩散
        elif breadth > 30 and avg_alpha > 60 and trend_20d > 5:
            stage = 'Expansion'
            reasons.append(f'Alpha扩散({breadth:.0f}%) 上涨({trend_20d:.1f}%)')

        # Early: Leader出现+资金进入
        elif has_leader and flow_score > 45 and avg_alpha > 55:
            stage = 'Early'
            reasons.append(f'Leader出现+资金进入({flow_score:.0f})')

        # Birth: 趋势刚启动
        elif trend_20d > 3 and breadth > 10:
            stage = 'Birth'
            reasons.append(f'趋势刚启动(20日{trend_20d:.1f}%)')

        return {
            'stage': stage,
            'score': score,
            'reasons': reasons,
            'trend_20d': trend_20d,
            'breadth': breadth,
            'avg_alpha': avg_alpha,
            'avg_risk': avg_risk,
            'flow_score': flow_score,
            'has_leader': has_leader,
            'is_breakout': is_breakout,
        }

    def predict_transition(self, lifecycle: Dict, prev_lifecycle: Optional[Dict] = None) -> Dict:
        """
        预测下一阶段转移概率。

        基于:
          - 当前阶段
          - 评分变化 (score delta)
          - Alpha Breadth变化
          - Leader变化
          - 资金变化
          - 风险变化
        """
        stage = lifecycle['stage']
        score = lifecycle['score']
        breadth = lifecycle['breadth']
        flow_score = lifecycle['flow_score']
        avg_risk = lifecycle['avg_risk']
        has_leader = lifecycle['has_leader']

        # 默认转移路径
        transitions = {
            'Birth': [('Early', 0.60), ('Birth', 0.25), ('Expansion', 0.15)],
            'Early': [('Expansion', 0.55), ('Acceleration', 0.20), ('Early', 0.15), ('Distribution', 0.10)],
            'Expansion': [('Acceleration', 0.45), ('Climax', 0.25), ('Expansion', 0.20), ('Distribution', 0.10)],
            'Acceleration': [('Climax', 0.40), ('Distribution', 0.25), ('Expansion', 0.20), ('Acceleration', 0.15)],
            'Climax': [('Distribution', 0.50), ('Climax', 0.25), ('Breakdown', 0.15), ('Recovery', 0.10)],
            'Distribution': [('Breakdown', 0.40), ('Recovery', 0.25), ('Distribution', 0.20), ('Acceleration', 0.15)],
            'Breakdown': [('Recovery', 0.40), ('Breakdown', 0.35), ('Birth', 0.15), ('Distribution', 0.10)],
            'Recovery': [('Birth', 0.45), ('Recovery', 0.25), ('Expansion', 0.15), ('Breakdown', 0.15)],
        }

        base = transitions.get(stage, [('Recovery', 0.5), ('Birth', 0.5)])

        # 动态调整转移概率
        adj_transitions = []
        for next_stage, prob in base:
            adj = prob

            # 评分高 → 正向转移概率增高
            if next_stage in ('Early', 'Expansion', 'Acceleration') and score > 75:
                adj *= 1.3
            elif next_stage in ('Distribution', 'Breakdown') and score < 50:
                adj *= 1.3

            # Breadth高 → 扩散强化
            if next_stage == 'Acceleration' and breadth > 50:
                adj *= 1.2

            # 资金强 → 正向
            if next_stage in ('Expansion', 'Acceleration') and flow_score > 60:
                adj *= 1.2

            # 风险高 → 负向
            if next_stage in ('Distribution', 'Breakdown') and avg_risk > 25:
                adj *= 1.3

            adj = min(adj, 1.0)
            adj_transitions.append((next_stage, round(adj, 4)))

        # 归一化
        total = sum(p for _, p in adj_transitions)
        adj_transitions = [(s, round(p / total, 4)) for s, p in adj_transitions]
        adj_transitions.sort(key=lambda x: x[1], reverse=True)

        primary = adj_transitions[0] if adj_transitions else ('', 0)

        return {
            'current_stage': stage,
            'transitions': adj_transitions,
            'primary_next': primary[0],
            'primary_prob': round(primary[1] * 100, 1),
        }

    # ================================================
    # 5. ETF内部股票筛选
    # ================================================

    def screen_etf_stocks(self, df_comp: pd.DataFrame) -> Dict:
        """
        ETF内部股票自动分类

        Leader: 龙头 - Composite Alpha高, Structure高, Risk低, 趋势Expansion/Acceleration
        Core: 中军 - Alpha稳定, 权重较高, 风险低
        Emerging: 新晋强股 - Alpha快速提升, Stage Early, Momentum增强
        Avoid: 回避 - Climax, Distribution, Risk升高
        """
        if df_comp.empty:
            return {'leader': [], 'core': [], 'emerging': [], 'avoid': []}

        candidates = []
        for _, row in df_comp.iterrows():
            ls = (
                0.4 * row.get('复合Alpha', 50) +
                0.3 * row.get('结构分', 50) +
                0.2 * row.get('资金分', 50) +
                0.1 * row.get('动量分', 50)
            )
            candidates.append({
                '代码': row.get('代码', ''),
                '名称': row.get('名称', ''),
                '复合Alpha': row.get('复合Alpha', 50),
                '结构分': row.get('结构分', 50),
                '资金分': row.get('资金分', 50),
                '动量分': row.get('动量分', 50),
                '风险分': row.get('风险分', 50),
                'OS': row.get('Opportunity_Score', 50),
                '趋势阶段': row.get('趋势阶段', ''),
                '操作建议': row.get('操作建议', ''),
                '20日涨幅': row.get('20日涨幅', 0),
                'leader_score': round(ls, 1),
            })

        # 分类
        leader_stocks = []
        core_stocks = []
        emerging_stocks = []
        avoid_stocks = []

        for s in candidates:
            if (s['复合Alpha'] >= 70 and s['风险分'] <= 15 and
                s['趋势阶段'] in ('Expansion', 'Acceleration')):
                leader_stocks.append(s)
            elif (s['复合Alpha'] >= 60 and s['风险分'] <= 20 and
                  s['趋势阶段'] not in ('Climax', 'Distribution', 'Breakdown')):
                core_stocks.append(s)
            elif (s['趋势阶段'] in ('Birth', 'Early') and s['leader_score'] > 50 and
                  s['复合Alpha'] >= 55):
                emerging_stocks.append(s)
            elif s['风险分'] > 30 or s['趋势阶段'] in ('Climax', 'Distribution'):
                avoid_stocks.append(s)

        # 排序
        leader_stocks.sort(key=lambda x: x['leader_score'], reverse=True)
        core_stocks.sort(key=lambda x: x['复合Alpha'], reverse=True)
        emerging_stocks.sort(key=lambda x: x['leader_score'], reverse=True)
        avoid_stocks.sort(key=lambda x: x['风险分'], reverse=True)

        return {
            'leader': leader_stocks[:5],
            'core': core_stocks[:5],
            'emerging': emerging_stocks[:5],
            'avoid': avoid_stocks[:5],
            'n_total': len(candidates),
        }

    # ================================================
    # 6. ETF轮动排名
    # ================================================

    def rank_etfs(self, etf_pool: dict = None, end_date: str = '',
                  force_scan: bool = False) -> pd.DataFrame:
        """
        全ETF轮动排名

        输出 TOP ETF Ranking 含所有评分维度
        """
        if etf_pool is None:
            etf_pool = ETF_POOL

        results = []
        total = len(etf_pool)
        self.log(f"开始排名 {total} 只ETF...")

        for i, (name, code) in enumerate(etf_pool.items(), 1):
            t1 = time.time()
            result = self.calc_etf_alpha_score(code, name, end_date, force_scan)
            elapsed = time.time() - t1
            score = result.get('score', 0)
            grade = result.get('grade_label', '')
            stage_info = self.detect_lifecycle_stage(result)
            stage = stage_info['stage']
            t_info = self.predict_transition(stage_info)
            next_stage = t_info['primary_next']
            next_prob = t_info['primary_prob']

            tl = result.get('leader_strength', {}).get('top_leader', {})
            leader_name = tl.get('名称', '') if isinstance(tl, dict) else ''

            self.log(f"[{i}/{total}] {name}({code}) Score={score}({grade}) "
                     f"Stage={stage}→{next_stage}({next_prob}%) Leader={leader_name} "
                     f"耗时={elapsed:.1f}s")

            results.append({
                'ETF名称': name,
                'ETF代码': code,
                '综合评分': score,
                '评级': grade,
                '生命周期': stage,
                '下一阶段': next_stage,
                '转移概率': next_prob,
                '龙头': leader_name,
                '成份股质量': result.get('component_alpha_quality', {}).get('score', 0),
                '龙头强度': result.get('leader_strength', {}).get('score', 0),
                '趋势强度': result.get('etf_trend', {}).get('score', 0),
                '持续性': result.get('etf_trend', {}).get('persistence', 50),
                '资金强度': result.get('etf_flow', {}).get('score', 0),
                '风险调整': result.get('risk_adjustment', {}).get('score', 0),
                '平均Alpha': result.get('component_alpha_quality', {}).get('avg_alpha', 0),
                'Alpha宽度': result.get('component_alpha_quality', {}).get('breadth', 0),
                '20日涨幅': result.get('etf_trend', {}).get('trend_20d', 0),
                '平均风险': result.get('risk_adjustment', {}).get('avg_risk', 0),
                '成份股数': result.get('n_components', 0),
                '均线': result.get('etf_trend', {}).get('ma_state', ''),
                '突破': result.get('etf_trend', {}).get('is_breakout', False),
            })

        if not results:
            return pd.DataFrame()

        # ─── 逆境强势加分 ───
        # 市场弱势（中位数20日涨幅<0）时，对保持相对强势的ETF给予加分
        all_ret20 = [r['20日涨幅'] for r in results]
        median_ret20 = float(np.median(all_ret20))
        if median_ret20 < 0:
            self.log(f"市场偏弱(中位数20日涨幅{median_ret20:.1f}%)，启用逆境强势加分")
            # 前期主线识别：使用持续性分的中位数作为阈值
            all_persist = [r.get('持续性', 0) for r in results]
            median_persist = float(np.median(all_persist))
            for r in results:
                ret20 = r['20日涨幅']
                if ret20 > max(median_ret20, 0):
                    # 基础加分：相对强度
                    bonus = min((ret20 - median_ret20) * 0.3, 5.0)
                    r['综合评分'] = round(min(r['综合评分'] + bonus, 100), 1)

                    # 主题记忆加分：
                    # 1) 仍处主线阶段(Early/Expansion/Acceleration) → +2
                    # 2) 前期主线回调中维持强势(Birth/Recovery+正收益+高持续性) → +3
                    stage = r['生命周期']
                    if stage in ('Expansion', 'Acceleration', 'Early'):
                        r['综合评分'] = round(min(r['综合评分'] + 2.0, 100), 1)
                    elif stage in ('Birth', 'Recovery') and ret20 > 0 and r.get('持续性', 0) >= median_persist:
                        # 曾是主线：回调筑底阶段仍保持正收益+持续性高于中位数
                        r['综合评分'] = round(min(r['综合评分'] + 3.0, 100), 1)

                    # 重新评级
                    s = r['综合评分']
                    if s >= 85: r['评级'] = 'S级机会'
                    elif s >= 75: r['评级'] = 'A级机会'
                    elif s >= 65: r['评级'] = '观察'
                    else: r['评级'] = '回避'

        df = pd.DataFrame(results)
        df = df.sort_values('综合评分', ascending=False).reset_index(drop=True)
        df['排名'] = range(1, len(df) + 1)

        self._prev_ranking = df
        return df

    # ================================================
    # 7. 竞争分析
    # ================================================

    def competition_analysis(self, current_df: pd.DataFrame,
                             prev_df: Optional[pd.DataFrame] = None) -> List[Dict]:
        """
        ETF竞争分析:
          - Score变化
          - 趋势加强/退潮识别
          - 新崛起ETF
        """
        if prev_df is None and self._prev_ranking is not None:
            prev_df = self._prev_ranking

        changes = []
        if prev_df is not None and not prev_df.empty:
            curr_map = {r['ETF代码']: r for _, r in current_df.iterrows()}
            prev_map = {r['ETF代码']: r for _, r in prev_df.iterrows()}

            for code, curr in curr_map.items():
                prev = prev_map.get(code)
                if prev is not None:
                    score_chg = curr['综合评分'] - prev['综合评分']
                else:
                    score_chg = 0

                curr_score = curr['综合评分']
                trend = curr.get('20日涨幅', 0)

                if score_chg > 10 and curr_score > 70:
                    cat = '新崛起'
                elif score_chg > 0 and curr_score > 75:
                    cat = '趋势加强'
                elif score_chg < -10 and curr_score < 60:
                    cat = '退潮'
                else:
                    cat = '稳定'

                changes.append({
                    'ETF名称': curr['ETF名称'],
                    'ETF代码': code,
                    '当前评分': curr_score,
                    '评分变化': round(score_chg, 1),
                    '类别': cat,
                    '趋势': trend,
                })

        return sorted(changes, key=lambda x: x['评分变化'], reverse=True)

    # ================================================
    # 8. 风险过滤
    # ================================================

    def risk_filter(self, etf_result: Dict) -> Dict:
        """
        ETF风险过滤: 降低排名的条件

        1. ETF上涨但成份股Alpha下降
        2. Leader跌入Distribution
        3. Risk快速增加
        4. Breadth下降
        5. 资金连续流出
        """
        risk_flags = []
        deductions = 0

        # 条件1: ETF涨但Alpha低
        trend_20d = etf_result.get('etf_trend', {}).get('trend_20d', 0)
        avg_alpha = etf_result.get('component_alpha_quality', {}).get('avg_alpha', 50)
        if trend_20d > 10 and avg_alpha < 60:
            risk_flags.append('ETF上涨但Alpha偏低')
            deductions += 10

        # 条件2: Leader高风险
        leader = etf_result.get('leader_strength', {}).get('top_leader', {})
        leader_stage = leader.get('阶段', '')
        if leader_stage in ('Climax', 'Distribution'):
            risk_flags.append(f"龙头{leader.get('名称','')}处于{leader_stage}")
            deductions += 15

        # 条件3: 风险分过高
        avg_risk = etf_result.get('risk_adjustment', {}).get('avg_risk', 50)
        if avg_risk > 25:
            risk_flags.append(f'风险偏高({avg_risk:.0f})')
            deductions += 10

        # 条件4: Breadth过低
        breadth = etf_result.get('component_alpha_quality', {}).get('breadth', 0)
        if breadth < 15:
            risk_flags.append(f'Alpha宽度过低({breadth:.0f}%)')
            deductions += 10

        # 条件5: 资金流出
        flow_score = etf_result.get('etf_flow', {}).get('score', 50)
        if flow_score < 35:
            risk_flags.append(f'资金流出({flow_score:.0f})')
            deductions += 10

        return {
            'risk_flags': risk_flags,
            'deductions': deductions,
            'adjusted_score': max(etf_result.get('score', 0) - deductions, 0),
        }

    # ================================================
    # 9. 报告生成
    # ================================================

    def generate_terminal_report(self, ranking_df: pd.DataFrame,
                                  comp_analysis: List[Dict] = None,
                                  top_n: int = 10) -> str:
        """生成终端报告"""
        if ranking_df.empty:
            return "无数据"

        lines = []
        lines.append("")
        lines.append("═" * 75)
        lines.append("ETF Alpha Intelligence Report")
        lines.append("═" * 75)

        display = ranking_df.head(top_n)
        for i, (_, r) in enumerate(display.iterrows(), 1):
            name = r['ETF名称']
            score = r['综合评分']
            grade = r['评级']
            stage = r['生命周期']
            next_s = r['下一阶段']
            prob = r['转移概率']
            alpha_q = r['成份股质量']
            trend = r['趋势强度']
            flow = r['资金强度']
            risk = r['风险调整']
            leader = r['龙头']
            ret20 = r['20日涨幅']
            ma = r['均线']

            star = '★★★★★' if score >= 90 else '★★★★☆' if score >= 80 else '★★★☆☆' if score >= 70 else '★★☆☆☆'

            lines.append(f"")
            lines.append(f"  TOP {i:2d}. {name}({r['ETF代码']})")
            lines.append(f"  Score: {score} ({grade}) {star}")
            lines.append(f"  Stage: {stage} → {next_s}({prob}%)")
            lines.append(f"  Alpha: {alpha_q:.0f} | Trend: {trend:.0f} | Flow: {flow:.0f} | Risk: {risk:.0f}")
            lines.append(f"  Leader: {leader or '无'} | 20日涨幅: {ret20:+.1f}% | {ma}")
            lines.append(f"  Alpha宽度: {r['Alpha宽度']:.0f}% | 平均Alpha: {r['平均Alpha']:.0f} | 平均风险: {r['平均风险']:.0f}")

        # 竞争分析
        if comp_analysis:
            lines.append("")
            lines.append("─" * 75)
            lines.append("  ETF竞争分析")
            lines.append("─" * 75)
            for c in comp_analysis:
                tag = '↑' if c['评分变化'] > 5 else ('↓' if c['评分变化'] < -5 else '→')
                lines.append(f"  {c['ETF名称']}: {c['当前评分']:.0f}点 ({tag}{c['评分变化']:+.1f}) [{c['类别']}]")

        lines.append("")
        lines.append("═" * 75)
        return '\n'.join(lines)

    def generate_html_report(self, ranking_df: pd.DataFrame,
                              etf_details: Dict[str, Dict] = None) -> str:
        """生成HTML日报"""
        if ranking_df.empty:
            return "<html><body>无数据</body></html>"

        date_str = datetime.now().strftime('%Y-%m-%d')
        rows_html = ''
        for i, (_, r) in enumerate(ranking_df.head(20).iterrows(), 1):
            stage_color = {'Birth':'#e8f5e9','Early':'#fff3e0','Expansion':'#e3f2fd',
                          'Acceleration':'#fce4ec','Climax':'#ffebee','Distribution':'#f3e5f5',
                          'Breakdown':'#ef9a9a','Recovery':'#c8e6c9'}.get(r['生命周期'], '#f5f5f5')
            rows_html += f"""
            <tr>
                <td>{i}</td>
                <td><b>{r['ETF名称']}</b></td>
                <td style="background:{(lambda s:'#d32f2f' if s<60 else '#f57c00' if s<70 else '#fbc02d' if s<80 else '#388e3c')(r['综合评分'])};color:white;font-weight:bold">{r['综合评分']}</td>
                <td>{r['评级']}</td>
                <td style="background:{stage_color}">{r['生命周期']}→{r['下一阶段']}({r['转移概率']:.0f}%)</td>
                <td>{r['龙头'] or '无'}</td>
                <td>{r['成份股质量']:.0f}</td>
                <td>{r['趋势强度']:.0f}</td>
                <td>{r['资金强度']:.0f}</td>
                <td>{r['风险调整']:.0f}</td>
                <td style="color:{'red' if r['20日涨幅']>0 else 'green'}">{r['20日涨幅']:+.1f}%</td>
                <td>{r['Alpha宽度']:.0f}%</td>
                <td>{r['均线']}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
        <html><head><meta charset="utf-8"><title>ETF Alpha Intelligence Report - {date_str}</title>
        <style>
            body {{font-family: 'Microsoft YaHei', sans-serif; margin:20px; background:#fafafa}}
            h1 {{color:#1976d2; border-bottom:3px solid #1976d2; padding-bottom:8px}}
            h3 {{color:#455a64}}
            table {{border-collapse:collapse; width:100%; font-size:13px}}
            th {{background:#1976d2; color:white; padding:8px; text-align:center; position:sticky; top:0}}
            td {{padding:6px 8px; border-bottom:1px solid #e0e0e0; text-align:center}}
            tr:hover {{background:#e3f2fd}}
            .header {{background:#e3f2fd; padding:12px; border-radius:8px; margin-bottom:16px}}
        </style></head><body>
        <div class="header"><h1>ETF Alpha Intelligence Report</h1>
        <p>报告日期: {date_str} | 排名ETF: {len(ranking_df)} 只</p></div>
        <table><thead><tr>
            <th>#</th><th>ETF名称</th><th>综合评分</th><th>评级</th><th>生命周期</th>
            <th>龙头</th><th>Alpha质</th><th>趋势</th><th>资金</th><th>风险</th>
            <th>20日涨幅</th><th>Alpha宽度</th><th>均线</th>
        </tr></thead><tbody>
        {rows_html}
        </tbody></table>
        <p style="color:#999;font-size:12px;margin-top:30px">免责声明: 本报告基于筹码结构技术指标生成，不构成投资建议。</p>
        </body></html>"""
        return html

    def generate_json_report(self, ranking_df: pd.DataFrame,
                              etf_details: Dict[str, Dict] = None) -> Dict:
        """生成JSON报告"""
        if ranking_df.empty:
            return {'date': datetime.now().strftime('%Y%m%d'), 'etfs': []}

        etfs = []
        for _, r in ranking_df.iterrows():
            etf = {
                'name': r['ETF名称'],
                'code': r['ETF代码'],
                'score': r['综合评分'],
                'grade': r['评级'],
                'stage': r['生命周期'],
                'next_stage': r['下一阶段'],
                'transition_prob': r['转移概率'],
                'leader': r['龙头'],
                'alpha_quality': r['成份股质量'],
                'trend': r['趋势强度'],
                'flow': r['资金强度'],
                'risk': r['风险调整'],
                'ret20': r['20日涨幅'],
                'breadth': r['Alpha宽度'],
            }
            if etf_details and r['ETF代码'] in etf_details:
                detail = etf_details[r['ETF代码']]
                screening = detail.get('screening', {})
                etf['stocks'] = {
                    'leader': screening.get('leader', [])[:3],
                    'core': screening.get('core', [])[:3],
                    'emerging': screening.get('emerging', [])[:3],
                    'avoid': screening.get('avoid', [])[:3],
                }
            etfs.append(etf)

        return {
            'date': datetime.now().strftime('%Y%m%d'),
            'n_etfs': len(etfs),
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'etfs': etfs,
        }


# ================================================
# 10. 回测系统
# ================================================

class ETFBacktester:
    """
    ETF Alpha Engine V2 回测系统

    支持:
      - ETF Score分层回测
      - 未来5/10/20/60日收益
      - 超额收益
      - 最大回撤
      - 胜率
      - Rank IC
    """

    def __init__(self, engine: ETFAlphaEngineV2):
        self.engine = engine
        self.pro = engine.pro

    def backtest_ranking(self, ranking_df: pd.DataFrame,
                         forward_days: List[int] = [5, 10, 20, 60],
                         end_date: str = '') -> pd.DataFrame:
        """
        对排名结果进行未来收益回测。
        对于每个ETF，计算其排名后 forward_days 天的收益。

        Parameters:
            ranking_df: 排名结果
            forward_days: 未来天数列表
            end_date: 排名日期 YYYYMMDD，用于定位基准价格
        """
        if ranking_df.empty:
            return pd.DataFrame()

        results = []

        for _, r in ranking_df.iterrows():
            code = r['ETF代码']
            score = r['综合评分']
            rank = r['排名']

            # 获取更宽范围的ETF日线（排名日前+未来足够天数）
            extra_days = max(forward_days) + 10  # 额外多取一些
            base_end = end_date or datetime.now().strftime('%Y%m%d')
            start_dt = datetime.strptime(base_end, '%Y%m%d') - timedelta(days=200)
            end_dt = datetime.strptime(base_end, '%Y%m%d') + timedelta(days=int(extra_days * 1.3))
            end_str = end_dt.strftime('%Y%m%d')
            start_str = start_dt.strftime('%Y%m%d')
            try:
                time.sleep(0.12)
                df = self.pro.fund_daily(ts_code=code, start_date=start_str, end_date=end_str)
            except Exception:
                continue
            if df is None or df.empty or len(df) < 20:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)

            # 定位排名日在日线中的位置
            base_mask = df['trade_date'].astype(str) == base_end
            if not base_mask.any():
                self.engine.log(f"  [BT] {code} 排名日{base_end}无数据，跳过")
                continue
            base_idx = df[base_mask].index[0]
            base_close = df.iloc[base_idx]['close']

            row = {'ETF名称': r['ETF名称'], 'ETF代码': code, '排名': rank, '综合评分': score}

            for fd in forward_days:
                fwd_idx = base_idx + fd
                if fwd_idx < len(df):
                    future_close = df.iloc[fwd_idx]['close']
                    ret = (future_close - base_close) / base_close * 100
                else:
                    ret = None
                row[f'未来{fd}日收益'] = ret

            results.append(row)

        return pd.DataFrame(results)

    def calc_ic(self, bt_df: pd.DataFrame, score_col: str = '综合评分',
                forward_days: int = 20) -> Dict:
        """计算Rank IC"""
        if bt_df.empty or score_col not in bt_df.columns:
            return {'spearman_ic': 0, 'pearson_ic': 0, 'n': 0}

        col = f'未来{forward_days}日收益'
        if col not in bt_df.columns:
            return {'spearman_ic': 0, 'pearson_ic': 0, 'n': 0}

        valid = bt_df[[score_col, col]].dropna()
        if len(valid) < 5:
            return {'spearman_ic': 0, 'pearson_ic': 0, 'n': len(valid)}

        from scipy.stats import spearmanr, pearsonr
        try:
            sp, _ = spearmanr(valid[score_col], valid[col])
            pr, _ = pearsonr(valid[score_col], valid[col])
        except Exception:
            sp, pr = 0, 0

        return {
            'spearman_ic': round(sp, 4),
            'pearson_ic': round(pr, 4),
            'n': len(valid),
            'forward_days': forward_days,
        }

    def calc_layer_returns(self, bt_df: pd.DataFrame,
                           forward_days: int = 20) -> Dict:
        """分层回测: 按评分分层计算未来收益"""
        if bt_df.empty:
            return {}

        col = f'未来{forward_days}日收益'
        if col not in bt_df.columns:
            return {}

        bt_df = bt_df.dropna(subset=[col, '综合评分']).copy()
        if bt_df.empty:
            return {}

        # 按评分分为5组
        bt_df['分层'] = pd.qcut(bt_df['综合评分'], q=5, labels=['Q1(低)', 'Q2', 'Q3', 'Q4', 'Q5(高)'],
                                  duplicates='drop')

        result = {}
        for layer in sorted(bt_df['分层'].unique()):
            group = bt_df[bt_df['分层'] == layer]
            rets = group[col]
            win_rate = sum(1 for r in rets if r > 0) / max(len(rets), 1) * 100
            result[f'{layer}({len(group)}只)'] = {
                '平均收益': round(float(rets.mean()), 2),
                '中位数': round(float(rets.median()), 2),
                '胜率': round(win_rate, 1),
                '最大': round(float(rets.max()), 2),
                '最小': round(float(rets.min()), 2),
            }

        # Q5 - Q1 多空收益
        q5 = bt_df[bt_df['分层'] == 'Q5(高)'][col].mean()
        q1 = bt_df[bt_df['分层'] == 'Q1(低)'][col].mean()
        result['多头(Q5)-空头(Q1)'] = {
            '收益差': round(float(q5 - q1), 2),
            'Q5收益': round(float(q5), 2),
            'Q1收益': round(float(q1), 2),
        }

        return result


# ================================================
# 主入口
# ================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='ETF Alpha Engine V2 — 机构级ETF趋势轮动')
    parser.add_argument('--date', type=str, default='', help='回溯日期 YYYYMMDD')
    parser.add_argument('--top', type=int, default=15, help='终端显示前N只')
    parser.add_argument('--output', default='report_daily/etf_v2_ranking.csv',
                        help='排名CSV输出路径')
    parser.add_argument('--html', action='store_true', help='生成HTML报告')
    parser.add_argument('--json', action='store_true', help='生成JSON报告')
    parser.add_argument('--backtest', action='store_true', help='执行回测')
    parser.add_argument('--no_push', action='store_true', help='跳过微信推送')
    parser.add_argument('--force_scan', action='store_true', help='强制实时扫描(慢)')
    args = parser.parse_args()

    engine = ETFAlphaEngineV2()
    end_date = args.date.strip() or ''

    # 1. 排名
    ranking_df = engine.rank_etfs(etf_pool=ETF_POOL, end_date=end_date,
                                   force_scan=args.force_scan)

    if ranking_df.empty:
        print("[ERROR] 排名无结果")
        return

    # 2. 竞争分析
    comp = engine.competition_analysis(ranking_df)

    # 3. 终端报告
    report = engine.generate_terminal_report(ranking_df, comp, top_n=args.top)
    print(report)

    # 4. 保存CSV
    output_path = args.output
    if end_date:
        base, ext = os.path.splitext(output_path)
        output_path = f"{base}_{end_date}{ext}"
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
    ranking_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"[ETF V2] 排名CSV已保存: {output_path} ({len(ranking_df)} 只ETF)")

    # 5. 内部股票筛选 + 详情
    etf_details = {}
    for _, r in ranking_df.head(10).iterrows():
        code = r['ETF代码']
        name = r['ETF名称']
        try:
            if args.force_scan:
                df_comp = engine.scan_components_for_etf(code, end_date)
            else:
                df_comp = engine.load_component_scan(code, end_date)
            if df_comp.empty:
                df_comp = engine.load_component_scan(code, '')
            screening = engine.screen_etf_stocks(df_comp)
        except Exception as e:
            screening = {'leader': [], 'core': [], 'emerging': [], 'avoid': []}
        etf_details[code] = {'screening': screening}

    # 6. HTML/JSON报告
    if args.html:
        html = engine.generate_html_report(ranking_df, etf_details)
        html_path = output_path.replace('.csv', '_report.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"[ETF V2] HTML报告已保存: {html_path}")

    if args.json:
        json_data = engine.generate_json_report(ranking_df, etf_details)
        json_path = output_path.replace('.csv', '_report.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"[ETF V2] JSON报告已保存: {json_path}")

    # 7. 回测
    if args.backtest:
        print("\n[ETF V2] 执行回测...")
        bt = ETFBacktester(engine)
        bt_df = bt.backtest_ranking(ranking_df, end_date=end_date)
        if not bt_df.empty:
            bt_path = output_path.replace('.csv', '_backtest.csv')
            bt_df.to_csv(bt_path, index=False, encoding='utf-8-sig')
            print(f"  回测结果已保存: {bt_path}")

            for fd in [5, 10, 20]:
                ic = bt.calc_ic(bt_df, forward_days=fd)
                print(f"  {fd}日 Rank IC: {ic.get('spearman_ic', 0):.4f} (n={ic.get('n', 0)})")

            for fd in [5, 20]:
                layers = bt.calc_layer_returns(bt_df, forward_days=fd)
                if layers:
                    print(f"\n  {fd}日分层收益:")
                    for k, v in layers.items():
                        if isinstance(v, dict):
                            avg = v.get('平均收益', 0)
                            win = v.get('胜率', 0)
                            print(f"    {k}: 平均{avg:+.2f}% 胜率{win:.0f}%")

    # 8. 微信推送
    if not args.no_push:
        push_msg = report.replace('\n', '\n\n')
        from scan_etf_alpha_v5 import send_wechat, send_pushplus
        send_wechat(push_msg, os.getenv('WECHAT_SCKEY'))
        send_pushplus(push_msg, os.getenv('PUSHPLUS'))

    print(f"[ETF V2] 完成!")
    return ranking_df


if __name__ == '__main__':
    main()
