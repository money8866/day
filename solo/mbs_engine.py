# -*- coding: utf-8 -*-
"""
MBS 中线可买性评分引擎 (Mid-term Buyability Score)
=====================================================
在现有基本面评分(DoubleScore/MoatScore/RiskScore)之上新增的独立买点层。

核心原则: Fundamental Score(公司质量) ≠ Buyability Score(当前是否值得买)
    Fundamental Quality 只评价"是不是好公司"
    MBS 只回答"当前价格+当前景气+当前市场环境下,3~12个月是否值得配置"

六维公式(100分制):
    MBS Raw = Fundamental Quality×30% + Growth Quality×20% + Valuation Safety×15%
            + Industry Cycle×10% + Technical Position×15% + Theme & Style×10%

流程:
    Raw MBS → Hard Cap → Confidence 修正 → Market Regime 修正 → Final MBS
    Final MBS = min(Raw, Cap) × (0.70 + 0.30×Conf/100) + MarketAdj, clip[0,100]

分级: CORE BUY(≥85) / BUY ON PULLBACK(75-85) / WATCH(65-75) / WAIT(55-65) / AVOID(<55)

数据缺失铁律: 缺失≠0。真实0=0, 缺失=unavailable(剔除该项+权重重分配+降Confidence), 异常=审计。
"""
import os
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 便于直接运行: 引入项目路径
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / 'multi_factor_picker'))
sys.path.insert(0, str(_HERE))

from data_fetcher import DataFetcher  # noqa: E402
from market_regime import detect_market_regime  # noqa: E402

CACHE_DIR = r'D:\mystock\cache_daily'
CSV_PATH = r'D:\mystock\solo\report_daily\double_score_20260819_194702.csv'
THEME_MAP_PATH = r'D:\mystock\cache_daily\theme_stock_map_latest.json'
OUT_CSV = r'D:\mystock\solo\report_daily\mbs_buyability_20260819.csv'

# ─────────────────────────────────────────────
# 权重配置
# ─────────────────────────────────────────────
W = {
    'FQ': 0.30, 'GQ': 0.20, 'VS': 0.15, 'IC': 0.10, 'TP': 0.15, 'TS': 0.10,
}

# ─────────────────────────────────────────────
# 龙头类型 → 龙头地位分 (spec 第三节)
# ─────────────────────────────────────────────
LEADER_SCORE = {
    '行业龙头': 100, '细分龙头': 90, '行业龙二': 85, '龙二': 80,
    '中军': 75, '普通': 55, '补涨': 40,
}

# ─────────────────────────────────────────────
# 行业景气阶段 → 分数 (spec 第十三节)
# ─────────────────────────────────────────────
CYCLE_STAGE_SCORE = {
    '主升': 100, '强上行': 100, '景气上行': 90, '复苏': 75,
    '震荡': 55, '横盘': 55, '下行': 30, '衰退': 10,
}

# 周期行业清单 (spec 第十节 Cycle Normalization)
CYCLICAL_INDUSTRIES = {
    '航运', '有色', '工业金属', '小金属', '能源金属', '化工', '煤炭',
    '能源', '钢铁', '石油', '资源', '有色资源', '矿业', '贵金属', '金属',
}

# ─────────────────────────────────────────────
# 非线性分档函数 (spec 第五节: 避免+900%碾压+100%)
# ─────────────────────────────────────────────
def growth_bucket(yoy: float) -> int:
    if yoy is None or (isinstance(yoy, float) and math.isnan(yoy)):
        return None
    if yoy < 0:
        return 20
    if yoy < 20:
        return 50
    if yoy < 40:
        return 65
    if yoy < 70:
        return 75
    if yoy < 100:
        return 85
    if yoy < 200:
        return 92
    return 95


# PEG 阶梯 (spec 第十节)
def peg_bucket(peg: float) -> int:
    if peg is None or (isinstance(peg, float) and math.isnan(peg)):
        return None
    if peg <= 0.20:
        return 100
    if peg <= 0.35:
        return 90
    if peg <= 0.50:
        return 80
    if peg <= 0.80:
        return 65
    if peg <= 1.20:
        return 50
    if peg <= 1.50:
        return 35
    return 20


# 估值空间阶梯 (spec 第十一节)
def upside_bucket(upside: float) -> int:
    if upside is None or (isinstance(upside, float) and math.isnan(upside)):
        return None
    if upside >= 100:
        return 100
    if upside >= 60:
        return 90
    if upside >= 30:
        return 80
    if upside >= 10:
        return 70
    if upside >= 0:
        return 60
    if upside >= -10:
        return 50
    if upside >= -30:
        return 35
    return 20


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _num(row, col):
    """读取数值列, 缺失返回 None(区别于真实0)"""
    if col not in row:
        return None
    v = row[col]
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


@dataclass
class MBSResult:
    ts_code: str = ''
    name: str = ''
    market: str = ''
    theme: str = ''
    fq: Optional[float] = None
    gq: Optional[float] = None
    vs: Optional[float] = None
    ic: Optional[float] = None
    tp: Optional[float] = None
    ts_score: Optional[float] = None
    conf: Optional[float] = None
    cap: Optional[float] = None
    market_adj: float = 0.0
    raw: Optional[float] = None
    final: Optional[float] = None
    signal: str = 'AVOID'
    tech_grade: str = 'N/A'
    research_score: Optional[float] = None
    research_rank: Optional[int] = None
    buy_rank: Optional[int] = None
    # ── MBS V2: Entry Score 买点状态 ──
    entry: Optional[float] = None
    entry_state: str = 'N/A'          # 健康回踩/接近MA20/偏高/深度回撤/趋势破坏...
    pullback_quality: str = 'N/A'     # A/B/C 健康回踩等级
    matrix_cell: str = ''             # Quality×Timing 二维矩阵状态
    d_ma20: Optional[float] = None    # 距MA20%
    d_ma30: Optional[float] = None    # 距MA30%
    d_ma60: Optional[float] = None    # 距MA60%
    d_hi: Optional[float] = None      # 距52周高%
    price: Optional[float] = None
    ma20_v: Optional[float] = None
    ma30_v: Optional[float] = None
    ma60_v: Optional[float] = None
    next_triggers: str = ''           # 升级触发条件
    flags: List[str] = field(default_factory=list)
    reason: str = ''
    # ── MBS V3: Pullback Confirmation Score & 成熟度 ──
    pcs: Optional[float] = None       # 回踩确认分 0~100
    entry_maturity: str = ''          # A/B/C/D 买点成熟度
    vol_ratio_v: Optional[float] = None  # 量比(直接存值便于输出)
    # ── MBS V4: 决策系统完整字段 ──
    rebound_quality: str = ''         # 反弹质量 A/B/C
    trading_score: Optional[float] = None  # 交易评分(用于排序)
    position: Optional[float] = None  # 建议仓位 %
    trigger: str = ''                 # 下一步触发条件(精简)
    invalidation: str = ''            # 失效条件
    one_line: str = ''                # 一句话结论
    # 四子维度保留 (研究价值拆解)
    quality_score: Optional[float] = None
    growth_score: Optional[float] = None
    valuation_score: Optional[float] = None
    cycle_score: Optional[float] = None
    # ── MBS V5: 调整充分性 / 黄金坑 / 买点类型 ──
    acs: Optional[float] = None       # ACS 调整充分性评分
    golden_pit: str = ''              # 黄金坑等级 GP_A / GP_B / GP_C
    buy_point_type: str = ''          # 买点类型
    adj_days_v: Optional[int] = None  # 调整天数
    max_dd_v: Optional[float] = None  # 最大回撤%
    vol_shrink_v: Optional[float] = None  # 10日量缩比
    # ── MBS V6: 底部形态 / 双底 / 均线纠缠 / 底部质量 ──
    bqs: Optional[float] = None       # BQS 底部质量评分 0~100
    double_bottom: str = ''           # 双底类型 DOUBLE_BOTTOM / W_BOTTOM / POTENTIAL_DB
    db_conf: Optional[float] = None   # 双底确认度 0~100
    db_rebound: Optional[float] = None # 右底反弹幅度%
    ma_conv: Optional[float] = None   # 均线纠缠评分 0~100
    bottom_dwell: Optional[int] = None # 底部震荡天数
    # ── MBS V7: 下跌质量 / 头部形态 / 量价背离 / 支撑位 ──
    dqs: Optional[float] = None       # DQS 下跌质量评分 0~100
    top_pattern: str = ''             # 头部形态 SHARP_PEAK / ROUNDING_TOP / SINGLE_PEAK
    top_quality: Optional[float] = None # 头部质量 0~100
    fall_pattern: str = ''            # 下跌模式 SHARP_THEN_SOFT / STEADY_DECLINE / PANIC_DROP
    vpd_type: str = ''                # 量价背离类型 BULLISH_DIVERGENCE / BEARISH_DIVERGENCE
    vpd_strength: Optional[float] = None # 背离强度 0~100
    support_hit: str = ''             # 支撑位 MA60_SUPPORT / PREV_LOW_SUPPORT / NONE
    support_strength: Optional[float] = None # 支撑力度 0~100
    # ── MBS V8: 强势回踩 (回测纠偏: 强者恒强, 放弃超跌筑底) ──
    srs: Optional[float] = None       # SRS 强势回踩评分 0~100
    srs_parts: str = ''               # SRS 分维度明细


# ─────────────────────────────────────────────
# 主引擎
# ─────────────────────────────────────────────
class MBSEngine:
    def __init__(self, csv_path: str = CSV_PATH, theme_map_path: str = THEME_MAP_PATH):
        self.df = pd.read_csv(csv_path, dtype={'代码': str})
        self.df['代码6'] = self.df['代码'].astype(str).str.split('.').str[0].str.zfill(6)
        self.df['市场'] = np.where(self.df['代码6'].str.startswith(('30', '68')), '双创', '主板')
        self._theme_map = self._load_theme_map(theme_map_path)
        self._tech_cache: Dict[str, dict] = {}
        self._last_trade_date: Optional[str] = None
        self._market_adj = 0.0
        self._market_regime = '震荡'
        self._df_singleton = None

    # ── 数据源 ──
    def _load_theme_map(self, path: str) -> Dict[str, List[Tuple[str, float]]]:
        """theme_stock_map_latest.json → {代码6: [(主题, score)]}"""
        result = {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for theme, stocks in (data.get('themes') or {}).items():
                for st in stocks:
                    code = str(st.get('code', '')).split('.')[0].zfill(6)
                    if not code or code == '000000':
                        continue
                    try:
                        score = float(st.get('score') or 0)
                    except (TypeError, ValueError):
                        score = 0.0
                    result.setdefault(code, []).append((theme, score))
        except Exception as e:
            print(f'[MBS] 主题映射加载失败: {e}')
        return result

    def _get_fetcher(self) -> Optional[DataFetcher]:
        if self._df_singleton is not None:
            return self._df_singleton
        token = os.environ.get('TUSHARE_TOKEN')
        if not token:
            for ep in [r'D:\mystock\solo\.env', r'D:\mystock\config\.env', r'D:\mystock\.env']:
                if os.path.exists(ep):
                    for line in open(ep, encoding='utf-8'):
                        line = line.strip()
                        if line.startswith('TUSHARE_TOKEN='):
                            token = line.split('=', 1)[1].strip().strip('"').strip("'")
                            break
                    if token:
                        break
        if not token:
            print('[MBS] 未找到 TUSHARE_TOKEN')
            return None
        config = {'cache': {'enabled': True, 'expire_hours': 168},
                  'tushare': {'max_retry': 3, 'retry_delay': 5}}
        self._df_singleton = DataFetcher(token, config)
        return self._df_singleton

    # ── 市场状态 ──
    def load_market_regime(self):
        """沪深300 → detect_market_regime → 修正分"""
        fetcher = self._get_fetcher()
        if fetcher is None:
            self._market_regime, self._market_adj = '震荡', 0.0
            return
        try:
            if self._last_trade_date:
                end = self._last_trade_date
                start = (datetime.strptime(end, '%Y%m%d') - timedelta(days=200)).strftime('%Y%m%d')
            else:
                end = datetime.now().strftime('%Y%m%d')
                start = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')
            hs = fetcher.get_index_daily('000300.SH', start, end)
            if hs is None or hs.empty:
                raise ValueError('hs300 empty')
            info = detect_market_regime(hs)
            self._market_regime = info.get('regime_name', '震荡市')
            adj_map = {'强势市场': 5, '震荡偏强': 3, '震荡市': 0,
                       '震荡偏弱': -5, '弱势市场': -10, '极端暴跌': -10}
            self._market_adj = adj_map.get(self._market_regime, 0)
        except Exception as e:
            print(f'[MBS] 市场状态检测失败({e}), 按震荡市处理')
            self._market_regime, self._market_adj = '震荡市', 0.0
        print(f'[MBS] 市场状态: {self._market_regime}  → 修正 {self._market_adj:+d}')

    # ── 技术面 ──
    @staticmethod
    def to_ts_code(code6: str) -> str:
        """6位代码 → tushare 带后缀代码 (60x/68x/51x→.SH, 其余→.SZ)"""
        code6 = code6.strip().zfill(6)
        if code6.startswith(('60', '68', '51', '50')):
            return f'{code6}.SH'
        return f'{code6}.SZ'

    def _fetch_tech(self, ts_code: str) -> Optional[pd.DataFrame]:
        """stk_factor_pro_range 近260个交易日。缓存 + 增量补全。ts_code 可为6位或带后缀。"""
        code6 = str(ts_code).split('.')[0].zfill(6)
        if code6 in self._tech_cache:
            return self._tech_cache[code6]
        fetcher = self._get_fetcher()
        if fetcher is None:
            return None
        try:
            full = self.to_ts_code(code6)
            if self._last_trade_date is None:
                self._last_trade_date = fetcher.get_last_trade_date()
            end = self._last_trade_date
            start = (datetime.strptime(end, '%Y%m%d') - timedelta(days=400)).strftime('%Y%m%d')
            df = fetcher.get_stk_factor_pro_range(full, start, end)
            if df is None or len(df) == 0:
                self._tech_cache[code6] = None
                return None
            df = df.sort_values('trade_date').reset_index(drop=True)
            self._tech_cache[code6] = df
            return df
        except Exception:
            self._tech_cache[code6] = None
            return None

    @staticmethod
    def compute_technical(df: pd.DataFrame) -> Optional[dict]:
        """从 stk_factor_pro_range 计算技术位置指标"""
        try:
            if df is None or len(df) < 25:
                return None
            close = float(df['close'].iloc[-1])
            highs = df['high'].astype(float).values
            closes = df['close'].astype(float).values
            vols = df['vol'].astype(float).values

            def ma(arr, n):
                return float(np.mean(arr[-n:])) if len(arr) >= n else None

            ma20 = ma(closes, 20)
            ma30 = ma(closes, 30)
            ma60 = ma(closes, 60)
            ma120 = ma(closes, 120)
            # 52周高点(近250个交易日)
            win = min(250, len(highs))
            hi52 = float(np.max(highs[-win:]))
            dist_hi = (close / hi52 - 1.0) * 100 if hi52 > 0 else None
            # MA20 斜率(5日前)
            ma20_prev = float(np.mean(closes[-25:-5])) if len(closes) >= 25 else None
            ma20_slope = (ma20 / ma20_prev - 1.0) * 100 if ma20_prev else None
            # MA30 斜率(5日前)
            ma30_prev = float(np.mean(closes[-35:-5])) if len(closes) >= 35 else None
            ma30_slope = (ma30 / ma30_prev - 1.0) * 100 if ma30_prev else None
            # MA60 斜率(5日前)
            ma60_prev = float(np.mean(closes[-65:-5])) if len(closes) >= 65 else None
            ma60_slope = (ma60 / ma60_prev - 1.0) * 100 if ma60_prev else None
            # 量比(当日/20日均量) + 当日涨跌
            prev_close = closes[-2] if len(closes) >= 2 else close
            price_chg = (close / prev_close - 1.0) * 100 if prev_close else 0.0
            vol_20 = float(np.mean(vols[-21:-1])) if len(vols) >= 21 and np.mean(vols[-21:-1]) > 0 else None
            vol_ratio = vols[-1] / vol_20 if vol_20 else None
            # ── PCS 需要的近5日数据(价格企稳/短期反弹) ──
            n5 = min(5, len(closes))
            last5_closes = closes[-n5:].tolist()
            last5_lows = df['low'].astype(float).values[-n5:].tolist()
            last5_highs = highs[-n5:].tolist()
            # 最近5日低点是否逐步抬高(企稳)
            lows_rising = None
            if n5 >= 3:
                seq = last5_lows[-3:]
                lows_rising = seq[2] >= seq[1] >= seq[0]
            # 最近2日是否不创新低
            no_new_low_2d = None
            if n5 >= 4:
                no_new_low_2d = last5_lows[-1] >= min(last5_lows[:-1])
            # 最近1日收阳 + 收盘接近当日高点
            last_up = price_chg > 0 if price_chg is not None else None
            close_to_high = None
            if last5_highs and last5_highs[-1] > 0:
                close_to_high = (close - last5_lows[-1]) / (last5_highs[-1] - last5_lows[-1]) if last5_highs[-1] != last5_lows[-1] else 1.0
            # 最近3日涨跌幅
            chg_3d = None
            if n5 >= 4:
                chg_3d = (close / last5_closes[-4] - 1.0) * 100 if last5_closes[-4] > 0 else None
            # ── ACS(调整充分性) 需要的指标 ──
            # 1. 调整天数: 距52周高点过了多少个交易日
            adj_days = None
            win = min(250, len(highs))
            hi_idx = np.argmax(highs[-win:])
            adj_days = win - 1 - hi_idx  # 从高点到现在的交易日数
            # 2. 本轮最大回撤(从52周高点到现在的最大回撤)
            lo_after_hi = float(np.min(closes[-win:][hi_idx:])) if hi_idx < win - 1 else close
            max_dd_from_hi = (close / hi52 - 1) * 100 if hi52 > 0 else None
            # 3. 缩量趋势: 近5日均量/20日均量, 近10日均量/20日均量
            vol_5 = float(np.mean(vols[-6:-1])) if len(vols) >= 6 and np.mean(vols[-6:-1]) > 0 else None
            vol_10 = float(np.mean(vols[-11:-1])) if len(vols) >= 11 and np.mean(vols[-11:-1]) > 0 else None
            vol_shrink_5d = vol_5 / vol_20 if vol_5 and vol_20 else None
            vol_shrink_10d = vol_10 / vol_20 if vol_10 and vol_20 else None
            # 4. 60日低点 + 距60日低的距离
            lo_win = min(60, len(closes))
            lo_60 = float(np.min(closes[-lo_win:]))
            dist_lo60 = (close / lo_60 - 1) * 100 if lo_60 > 0 else None
            # 5. 黄金分割位 (从前高到前一轮低点 - 用 52周高 / 52周低)
            lo52 = float(np.min(closes[-win:]))
            hi_amp = hi52 - lo52
            fib_382 = hi52 - hi_amp * 0.382 if hi_amp > 0 else None
            fib_500 = hi52 - hi_amp * 0.500 if hi_amp > 0 else None
            fib_618 = hi52 - hi_amp * 0.618 if hi_amp > 0 else None
            # 距382 = 回撤到了吗 (回撤黄金分割位%
            dist_fib50 = (close / fib_500 - 1) * 100 if fib_500 and fib_500 > 0 else None
            return {
                'close': close, 'ma20': ma20, 'ma30': ma30, 'ma60': ma60, 'ma120': ma120,
                'hi52': hi52, 'dist_hi': dist_hi, 'ma20_slope': ma20_slope,
                'ma30_slope': ma30_slope, 'ma60_slope': ma60_slope,
                'vol_ratio': vol_ratio, 'price_chg': price_chg,
                'lows_rising': lows_rising,
                'no_new_low_2d': no_new_low_2d,
                'last_up': last_up,
                'close_to_high': close_to_high,
                'chg_3d': chg_3d,
                'last5_low': last5_lows[-1],
                'last5_high': last5_highs[-1],
                'last_date': str(df['trade_date'].iloc[-1]),
                'n_days': len(df),
                # ACS相关
                'adj_days': adj_days,
                'max_dd': max_dd_from_hi,
                'vol_shrink_5d': vol_shrink_5d,
                'vol_shrink_10d': vol_shrink_10d,
                'lo_60': lo_60,
                'dist_lo60': dist_lo60,
                'fib_382': fib_382,
                'fib_500': fib_500,
                'fib_618': fib_618,
                'dist_fib50': dist_fib50,
                # ── V6: 双底/W底识别相关 ──
                **MBSEngine._detect_double_bottom(closes, vols, hi_idx, win, lo52, hi52),
                # ── V6: 均线纠缠度 ──
                **MBSEngine._ma_convergence(closes),
                # ── V6: 底部震荡区间 ──
                **MBSEngine._bottom_range_features(closes, vols, lo52),
                # ── V7: 头部形态 + 下跌节奏 + 量价背离 + 支撑位 ──
                **MBSEngine._detect_top_pattern(closes, vols, hi_idx, win, hi52),
                **MBSEngine._fall_quality(closes, vols, hi_idx, win),
                **MBSEngine._volume_price_divergence(closes, vols, hi_idx, win),
                **MBSEngine._support_levels(closes, hi_idx, win, lo52),
            }
        except Exception:
            return None

    @staticmethod
    def _detect_double_bottom(closes, vols, hi_idx, win, lo52, hi52) -> dict:
        """V6: 双底/W底形态识别
        在52周高点之后的下跌区间中，寻找两次探底的W形态。
        返回: 双底等级、双底确认度、两底距离%、右底距左底天数、右底反弹幅度%、右底放量比
        """
        result = {
            'db_type': '',            # DOUBLE_BOTTOM / W_BOTTOM / POTENTIAL_DB / ''
            'db_confidence': 0.0,     # 双底确认度 0~100
            'db_left_low': None,      # 左底价格
            'db_right_low': None,     # 右底价格
            'db_bottom_spread': None, # 两底价差%
            'db_days_between': None,  # 两底间隔交易日
            'db_right_rebound': None, # 右底反弹幅度%
            'db_right_vol_ratio': None, # 右底反弹日量比
        }
        try:
            n = len(closes)
            if n < 30 or hi_idx >= win - 5:
                return result
            # 只看高点之后的下跌段
            post_hi_closes = closes[-win:][hi_idx:]
            post_hi_vols = vols[-win:][hi_idx:]
            m = len(post_hi_closes)
            if m < 15:
                return result
            # 找到第一个低点(左底): 下跌后出现的局部低点
            # 简化: 找最低点作为左底候选
            left_low_idx = int(np.argmin(post_hi_closes))
            left_low = float(post_hi_closes[left_low_idx])
            # 左底之后必须有反弹至少5%才能算一个底
            if left_low_idx >= m - 3:
                return result
            after_left = post_hi_closes[left_low_idx:]
            peak_after_left = float(np.max(after_left[:min(20, len(after_left))]))
            bounce_from_left = (peak_after_left / left_low - 1) * 100 if left_low > 0 else 0
            if bounce_from_left < 5:
                # 反弹不够,可能只是下跌中继,不是双底
                result['db_type'] = 'POTENTIAL_DB' if bounce_from_left > 2 else ''
                result['db_confidence'] = 20 if bounce_from_left > 2 else 0
                return result
            # 找到左底反弹后的局部高点位置
            peak_idx = left_low_idx + int(np.argmax(after_left[:min(20, len(after_left))]))
            if peak_idx >= m - 3:
                result['db_type'] = 'POTENTIAL_DB'
                result['db_confidence'] = 30
                result['db_left_low'] = left_low
                return result
            # 在右半段找右底: 从高点回落后的低点
            right_segment = post_hi_closes[peak_idx:]
            if len(right_segment) < 5:
                result['db_type'] = 'POTENTIAL_DB'
                result['db_confidence'] = 35
                result['db_left_low'] = left_low
                return result
            # 右底 = 右段的最低点(如果右段低点高于左底=成功双底)
            right_low_idx = int(np.argmin(right_segment))
            right_low = float(right_segment[right_low_idx])
            right_low_abs_idx = peak_idx + right_low_idx
            days_between = right_low_abs_idx - left_low_idx
            bottom_spread = (right_low / left_low - 1) * 100 if left_low > 0 else 0
            # 右底到当前的反弹幅度
            if right_low_abs_idx < m - 1:
                current_after_right = post_hi_closes[right_low_abs_idx:]
                right_rebound = (post_hi_closes[-1] / right_low - 1) * 100 if right_low > 0 else 0
            else:
                right_rebound = 0
            # 右底反弹放量
            if right_low_abs_idx < m - 1 and right_low_abs_idx + 3 <= m:
                vol_right_rebound = float(np.mean(post_hi_vols[right_low_abs_idx:right_low_abs_idx+3]))
                vol_left_bottom = float(np.mean(post_hi_vols[max(0,left_low_idx-2):left_low_idx+1]))
                right_vol_ratio = vol_right_rebound / vol_left_bottom if vol_left_bottom > 0 else None
            else:
                right_vol_ratio = None
            # 判定双底类型
            conf = 0
            db_type = ''
            # 核心: 右底不破左底(允许2%误差)
            if bottom_spread >= -2:
                conf += 30
            elif bottom_spread >= -5:
                conf += 15
            # 两底间隔合理(10~60天)
            if 10 <= days_between <= 60:
                conf += 15
            elif 5 <= days_between < 10:
                conf += 5
            # 右底反弹幅度
            if right_rebound >= 5:
                conf += 20
            elif right_rebound >= 3:
                conf += 10
            elif right_rebound >= 1:
                conf += 5
            # 右底放量
            if right_vol_ratio is not None and right_vol_ratio >= 1.2:
                conf += 15
            elif right_vol_ratio is not None and right_vol_ratio >= 1.0:
                conf += 8
            # 形态完美度: 右底接近左底+间隔合理+右底反弹确认
            if bottom_spread >= -2 and right_rebound >= 3 and days_between >= 10:
                db_type = 'DOUBLE_BOTTOM'
            elif bottom_spread >= -5 and right_rebound >= 2:
                db_type = 'W_BOTTOM'
            elif bottom_spread >= -8:
                db_type = 'POTENTIAL_DB'
            # 综合确认度
            result['db_type'] = db_type
            result['db_confidence'] = min(100, conf)
            result['db_left_low'] = left_low
            result['db_right_low'] = right_low
            result['db_bottom_spread'] = bottom_spread
            result['db_days_between'] = days_between
            result['db_right_rebound'] = right_rebound
            result['db_right_vol_ratio'] = right_vol_ratio
        except Exception:
            pass
        return result

    @staticmethod
    def _ma_convergence(closes) -> dict:
        """V6: 均线纠缠度
        衡量 MA5/MA10/MA20/MA60 的收敛程度。
        值越小 = 均线越粘合 = 震荡越充分 = 变盘概率越大
        """
        result = {
            'ma_conv_score': None,    # 均线纠缠评分 0~100 (越高越粘合)
            'ma_spread_5_10': None,   # MA5 vs MA10 价差%
            'ma_spread_10_20': None,  # MA10 vs MA20 价差%
            'ma_spread_5_20': None,   # MA5 vs MA20 价差%
            'ma_spread_all': None,    # MA5/MA10/MA20 最大价差%
        }
        try:
            n = len(closes)
            if n < 20:
                return result
            ma5 = float(np.mean(closes[-5:]))
            ma10 = float(np.mean(closes[-10:]))
            ma20 = float(np.mean(closes[-20:]))
            s_5_10 = abs(ma5 / ma10 - 1) * 100 if ma10 > 0 else None
            s_10_20 = abs(ma10 / ma20 - 1) * 100 if ma20 > 0 else None
            s_5_20 = abs(ma5 / ma20 - 1) * 100 if ma20 > 0 else None
            spread_all = max(filter(None, [s_5_10, s_10_20, s_5_20])) if any(x is not None for x in [s_5_10, s_10_20, s_5_20]) else None
            # 评分: spread越小分越高
            #  spread < 0.5% → 95分, <1% → 85, <2% → 70, <3% → 55, <5% → 40, >5% → 20
            score = None
            if spread_all is not None:
                if spread_all < 0.5:
                    score = 95
                elif spread_all < 1.0:
                    score = 85
                elif spread_all < 2.0:
                    score = 70
                elif spread_all < 3.0:
                    score = 55
                elif spread_all < 5.0:
                    score = 40
                else:
                    score = 20
            result['ma_conv_score'] = score
            result['ma_spread_5_10'] = s_5_10
            result['ma_spread_10_20'] = s_10_20
            result['ma_spread_5_20'] = s_5_20
            result['ma_spread_all'] = spread_all
        except Exception:
            pass
        return result

    @staticmethod
    def _bottom_range_features(closes, vols, lo52) -> dict:
        """V6: 底部震荡区间特征
        计算价格在底部区间的震荡特征: 底部震荡天数、底部振幅、底部缩量程度
        """
        result = {
            'bottom_dwell_days': None,  # 在底部区间(最低点+15%以内)的天数
            'bottom_range_amp': None,   # 底部区间振幅%
            'bottom_vol_shrink': None,  # 底部区间量缩比(底部均量/全段均量)
        }
        try:
            n = len(closes)
            if n < 20 or lo52 <= 0:
                return result
            # 底部区间定义: 最低点上方0%~15%的价格带
            bottom_top = lo52 * 1.15
            # 从近60日中统计底部天数
            lookback = min(60, n)
            recent = closes[-lookback:]
            bottom_mask = recent <= bottom_top
            dwell_days = int(np.sum(bottom_mask))
            if dwell_days < 3:
                result['bottom_dwell_days'] = dwell_days
                return result
            # 底部区间内的振幅
            bottom_prices = recent[bottom_mask]
            bottom_hi = float(np.max(bottom_prices))
            bottom_lo = float(np.min(bottom_prices))
            bottom_amp = (bottom_hi / bottom_lo - 1) * 100 if bottom_lo > 0 else None
            # 底部量缩: 底部日均量 / 60日均量
            recent_vols = vols[-lookback:]
            bottom_vols = recent_vols[bottom_mask]
            avg_vol_bottom = float(np.mean(bottom_vols)) if len(bottom_vols) > 0 else None
            avg_vol_all = float(np.mean(recent_vols)) if len(recent_vols) > 0 else None
            bottom_vol_shrink = avg_vol_bottom / avg_vol_all if avg_vol_bottom and avg_vol_all and avg_vol_all > 0 else None
            result['bottom_dwell_days'] = dwell_days
            result['bottom_range_amp'] = bottom_amp
            result['bottom_vol_shrink'] = bottom_vol_shrink
        except Exception:
            pass
        return result

    @staticmethod
    def _detect_top_pattern(closes, vols, hi_idx, win, hi52) -> dict:
        """V7: 头部形态识别
        在52周高点附近,识别是双顶/头肩顶/尖顶/圆弧顶。
        顶部形态越"尖",说明上涨越急促→后续调整越可能是技术性回调
        顶部越"复杂"(双顶/头肩顶),说明资金出逃越充分→后续调整越健康
        """
        result = {
            'top_pattern': '',       # DOUBLE_TOP / HEAD_SHOULDERS / SHARP_PEAK / ROUNDING_TOP / ''
            'top_quality': 0.0,      # 头部质量 0~100 (越高=出逃越充分=下跌越健康)
            'top_days': None,        # 顶部构筑天数
            'top_drop_from_peak': None, # 从最高点到现在的跌幅%
        }
        try:
            n = len(closes)
            if n < 30 or hi_idx < 2:
                return result
            post_hi = closes[-win:][hi_idx:]
            top_days = max(1, hi_idx)
            result['top_days'] = int(top_days)
            # 取高点前后各10天看顶部形态
            top_start = max(0, hi_idx - 10)
            top_end = min(win - 1, hi_idx + 10)
            top_segment = closes[-win:][top_start:top_end+1]
            if len(top_segment) < 5:
                return result
            # 找高点附近的局部高点数量
            peak_idx_in_seg = hi_idx - top_start
            # 左半段: 从 segment[0] 到 peak
            left_half = top_segment[:peak_idx_in_seg+1]
            # 右半段: 从 peak 到 segment[-1]
            right_half = top_segment[peak_idx_in_seg:]
            if len(left_half) < 2 or len(right_half) < 2:
                return result
            # 尖顶检测: 左右各2天内快速冲高又快速回落
            left_rise = (left_half[-1] / left_half[0] - 1) * 100 if left_half[0] > 0 else 0
            right_fall = (right_half[-1] / right_half[0] - 1) * 100 if right_half[0] > 0 else 0
            # 量能: 顶部是否放量
            top_vols = vols[-win:][top_start:top_end+1]
            vol_at_peak = top_vols[peak_idx_in_seg] if len(top_vols) > peak_idx_in_seg else 0
            avg_vol_top = float(np.mean(top_vols)) if len(top_vols) > 0 else 1
            vol_peak_ratio = vol_at_peak / avg_vol_top if avg_vol_top > 0 else 1
            # 形态判断
            quality = 0
            pattern = ''
            # 尖顶: 左升>5% 且 右跌>5% (快速拉升+快速回落)
            if left_rise > 5 and right_fall < -5:
                pattern = 'SHARP_PEAK'
                quality = 30  # 尖顶=资金急速进出,不可靠
            # 双顶: 左右各有一个接近的高点 (需要更长窗口)
            # 简化: 如果 hi_idx 附近有两个接近的高点
            elif abs(left_rise) < 3 and abs(right_fall) < 3 and top_days >= 5:
                pattern = 'ROUNDING_TOP'
                quality = 70  # 圆弧顶=缓慢筑顶,出逃充分
            else:
                # 普通单顶
                quality = 50
                pattern = 'SINGLE_PEAK'
            # 顶部构筑时间越长,质量越高
            if top_days >= 20:
                quality += 15
            elif top_days >= 10:
                quality += 8
            # 顶部放量+下跌缩量=健康调整
            if vol_peak_ratio > 1.3:
                quality += 10
            result['top_pattern'] = pattern
            result['top_quality'] = min(100, quality)
            result['top_drop_from_peak'] = (closes[-1] / hi52 - 1) * 100 if hi52 > 0 else None
        except Exception:
            pass
        return result

    @staticmethod
    def _fall_quality(closes, vols, hi_idx, win) -> dict:
        """V7: 下跌节奏/质量评估
        健康的下跌: 急跌→缓跌→缩量企稳
        不健康的下跌: 一路阴跌+放量下跌+无抵抗
        """
        result = {
            'fall_pattern': '',       # SHARP_THEN_SOFT / STEADY_DECLINE / PANIC_DROP / ''
            'fall_quality_score': 0.0, # 下跌质量 0~100 (越高=越健康)
            'fall_vol_ratio': None,    # 下跌段量能 vs 上涨段量能
            'max_down_day': None,      # 单日最大跌幅%
            'down_days_ratio': None,   # 下跌天数占比
        }
        try:
            n = len(closes)
            if n < 20 or hi_idx < 3:
                return result
            post_hi_closes = closes[-win:][hi_idx:]
            post_hi_vols = vols[-win:][hi_idx:]
            m = len(post_hi_closes)
            if m < 10:
                return result
            # 计算每日涨跌
            daily_chg = np.diff(post_hi_closes) / post_hi_closes[:-1] * 100
            down_days = np.sum(daily_chg < 0)
            up_days = np.sum(daily_chg > 0)
            total_days = len(daily_chg)
            down_ratio = down_days / total_days if total_days > 0 else 0
            # 前半段跌幅 vs 后半段跌幅
            half = m // 2
            first_half_drop = (post_hi_closes[half] / post_hi_closes[0] - 1) * 100 if post_hi_closes[0] > 0 else 0
            second_half_drop = (post_hi_closes[-1] / post_hi_closes[half] - 1) * 100 if post_hi_closes[half] > 0 else 0
            # 急跌缓跌模式: 前半段跌得多,后半段跌得少
            pattern = ''
            quality = 0
            if first_half_drop < -8 and second_half_drop > -5:
                # 急跌+缓跌=最健康的下跌模式
                pattern = 'SHARP_THEN_SOFT'
                quality = 80
            elif first_half_drop < -5 and second_half_drop <= 0:
                pattern = 'SHARP_THEN_SOFT'
                quality = 65
            elif down_ratio > 0.7:
                # 几乎全绿=一路阴跌=不健康
                pattern = 'STEADY_DECLINE'
                quality = 35
            elif abs(first_half_drop) > 15:
                pattern = 'PANIC_DROP'
                quality = 40
            else:
                pattern = 'NORMAL_FALL'
                quality = 55
            # 下跌缩量加分 (下跌段均量 vs 高点前5日均量)
            if hi_idx >= 5:
                pre_hi_vol = float(np.mean(vols[-win:][max(0,hi_idx-5):hi_idx]))
                post_hi_vol_avg = float(np.mean(post_hi_vols))
                if pre_hi_vol > 0:
                    fall_vol_r = post_hi_vol_avg / pre_hi_vol
                    result['fall_vol_ratio'] = fall_vol_r
                    if fall_vol_r < 0.8:
                        quality += 15  # 下跌缩量=健康
                    elif fall_vol_r > 1.2:
                        quality -= 15  # 下跌放量=恐慌出逃
            # 单日最大跌幅
            max_down = float(np.min(daily_chg)) if len(daily_chg) > 0 else 0
            result['max_down_day'] = max_down
            if max_down < -7:
                quality -= 5  # 单日暴跌=恐慌
            # 下跌天数占比
            result['down_days_ratio'] = round(down_ratio, 2)
            result['fall_pattern'] = pattern
            result['fall_quality_score'] = max(0, min(100, quality))
        except Exception:
            pass
        return result

    @staticmethod
    def _volume_price_divergence(closes, vols, hi_idx, win) -> dict:
        """V7: 量价背离检测 (底部区域)
        价创新低+量不创新低 = 看涨背离 = 抛压衰竭
        价创新低+量也创新低 = 恐慌下跌,还没结束
        """
        result = {
            'vpd_type': '',          # BULLISH_DIVERGENCE / BEARISH_DIVERGENCE / NO_DIVERGENCE
            'vpd_strength': 0.0,     # 背离强度 0~100
            'price_new_low': False,  # 当前价格接近/创阶段新低
            'vol_new_low': False,    # 成交量创阶段新低
        }
        try:
            n = len(closes)
            if n < 30 or hi_idx < 5:
                return result
            post_hi_closes = closes[-win:][hi_idx:]
            post_hi_vols = vols[-win:][hi_idx:]
            m = len(post_hi_closes)
            if m < 15:
                return result
            # 近10日最低点 vs 整个下跌段最低点
            recent_low = float(np.min(post_hi_closes[-10:]))
            all_low = float(np.min(post_hi_closes))
            price_near_low = (recent_low / all_low - 1) < 3 if all_low > 0 else False  # 距最低点3%以内
            result['price_new_low'] = price_near_low
            # 近10日均量 vs 整个下跌段最低量
            recent_vol_avg = float(np.mean(post_hi_vols[-10:]))
            all_vol_min = float(np.min(post_hi_vols))
            vol_near_low = recent_vol_avg <= all_vol_min * 1.3  # 接近最低量
            result['vol_new_low'] = vol_near_low
            # 背离判断
            strength = 0
            vtype = 'NO_DIVERGENCE'
            if price_near_low and vol_near_low:
                # 价在低位 + 量也在低位 = 缩量见底 = 看涨背离
                vtype = 'BULLISH_DIVERGENCE'
                strength = 75
                # 量缩越厉害,背离越强
                avg_vol_all = float(np.mean(post_hi_vols))
                vol_ratio = recent_vol_avg / avg_vol_all if avg_vol_all > 0 else 1
                if vol_ratio < 0.6:
                    strength += 15
                elif vol_ratio < 0.75:
                    strength += 8
            elif not price_near_low and vol_near_low:
                # 价格不低但量缩到极致 = 可能是调整中续
                vtype = 'VOLUME_SHRINK'
                strength = 45
            elif price_near_low and not vol_near_low:
                # 价新低但量没缩 = 还有抛压 = 偏空
                vtype = 'BEARISH_DIVERGENCE'
                strength = 20
            result['vpd_type'] = vtype
            result['vpd_strength'] = min(100, strength)
        except Exception:
            pass
        return result

    @staticmethod
    def _support_levels(closes, hi_idx, win, lo52) -> dict:
        """V7: 支撑位识别
        检测当前价格是否在关键支撑位附近,以及支撑力度
        """
        result = {
            'support_hit': '',        # MA60_SUPPORT / PREV_LOW_SUPPORT / FIB_SUPPORT / NONE
            'support_strength': 0.0,  # 支撑力度 0~100
            'dist_to_support': None,  # 距最强支撑位%
        }
        try:
            n = len(closes)
            if n < 60:
                return result
            close = float(closes[-1])
            ma60 = float(np.mean(closes[-60:]))
            dist_ma60 = (close / ma60 - 1) * 100 if ma60 > 0 else 0
            # 前低支撑: 60日低点
            lo_60 = float(np.min(closes[-60:]))
            dist_lo60 = (close / lo_60 - 1) * 100 if lo_60 > 0 else 0
            # 找最强支撑
            strength = 0
            support = 'NONE'
            dist = None
            # MA60支撑: 在MA60上方附近
            if 0 <= dist_ma60 < 5:
                support = 'MA60_SUPPORT'
                strength = 60
                dist = dist_ma60
                if dist_ma60 < 2:
                    strength += 15
            # 前低支撑
            elif 0 <= dist_lo60 < 5:
                support = 'PREV_LOW_SUPPORT'
                strength = 55
                dist = dist_lo60
                if dist_lo60 < 2:
                    strength += 10
            # MA20支撑
            elif n >= 20:
                ma20 = float(np.mean(closes[-20:]))
                dist_ma20 = (close / ma20 - 1) * 100 if ma20 > 0 else 0
                if 0 <= dist_ma20 < 3:
                    support = 'MA20_SUPPORT'
                    strength = 50
                    dist = dist_ma20
            result['support_hit'] = support
            result['support_strength'] = strength
            result['dist_to_support'] = dist
        except Exception:
            pass
        return result

    # ── 六维评分 ──
    def fundamental_quality(self, row) -> Optional[float]:
        moat = _num(row, 'MoatScore')
        roe = _num(row, 'ROE%')
        gm = _num(row, '毛利率%')
        leader = str(row.get('龙头类型', '')).strip()
        risk = _num(row, 'RiskScore')
        weights = {'护城河': 0.30, 'ROE': 0.25, '毛利率': 0.15, '龙头': 0.20, '风险': 0.10}
        scores = {}
        if moat is not None:
            scores['护城河'] = _clip(moat)
        if roe is not None:
            scores['ROE'] = _clip(min(roe / 25, 1.5) * 100)
        if gm is not None:
            scores['毛利率'] = _clip(min(gm / 40, 1.0) * 100)
        if leader:
            scores['龙头'] = LEADER_SCORE.get(leader, 55)
        if risk is not None:
            scores['风险'] = _clip(100 - risk)
        if not scores:
            return None
        total_w = sum(weights[k] for k in scores)
        if total_w <= 0:
            return None
        return _clip(sum(scores[k] * weights[k] for k in scores) / total_w)

    def growth_quality(self, row) -> Tuple[Optional[float], List[str]]:
        """Growth Quality: 缺失项剔除+权重重分配"""
        profit_yoy = _num(row, '利润YoY%')
        q1_yoy = _num(row, 'Q1利润YoY%')
        acc = _num(row, '加速度分')
        adj_growth = _num(row, 'AdjustedProfitGrowth')
        non_recur = _num(row, '非经常损益%')
        pqf = _num(row, 'ProfitQualityFactor')
        cfo = _num(row, 'CFO分')
        flags = []

        def prof_score(y):
            b = growth_bucket(y)
            return b

        weights = {'利润': 0.25, 'Q1': 0.15, '加速度': 0.15, '连续': 0.20, '扣非': 0.15, '现金流': 0.10}
        scores = {}
        if profit_yoy is not None:
            scores['利润'] = prof_score(profit_yoy)
        else:
            flags.append('利润YoY缺失')
        if q1_yoy is not None:
            scores['Q1'] = prof_score(q1_yoy)
        else:
            flags.append('Q1增速缺失')
        if acc is not None:
            scores['加速度'] = _clip(acc)
        else:
            flags.append('加速度缺失')
        # 盈利连续性: 4季度数据缺失 → 用 对数压缩增长(低基数修正) + 非经常损益低(扣非主营)
        if adj_growth is not None:
            conti = _clip(min(adj_growth / 150, 1.0) * 100)
            if non_recur is not None:
                conti = 0.6 * conti + 0.4 * _clip(100 - min(non_recur, 100))
            scores['连续'] = conti
        else:
            flags.append('连续性数据缺失')
        if pqf is not None:
            scores['扣非'] = _clip(pqf * 100)
        else:
            flags.append('扣非占比缺失')
        if cfo is not None:
            scores['现金流'] = _clip(cfo)
        else:
            flags.append('现金流缺失')
        if not scores:
            return None, flags
        total_w = sum(weights[k] for k in scores)
        if total_w <= 0:
            return None, flags
        score = _clip(sum(scores[k] * weights[k] for k in scores) / total_w)
        return score, flags

    def valuation_safety(self, row) -> Tuple[Optional[float], List[str]]:
        peg = _num(row, 'PEG')
        upside = _num(row, '估值空间%')
        theme = str(row.get('主题', ''))
        cycle_stage = str(row.get('行业景气阶段', ''))
        cycle_score = _num(row, 'IndustryCycleScore')
        flags = []

        weights = {'PEG': 0.50, '估值空间': 0.30, '归一': 0.20}
        scores = {}
        if peg is not None:
            scores['PEG'] = peg_bucket(peg)
        else:
            flags.append('PEG缺失')
        if upside is not None:
            scores['估值空间'] = upside_bucket(upside)
        else:
            flags.append('估值空间缺失')
        if peg is not None:
            # Normalized Valuation: PEG越低越安全(连续)
            scores['归一'] = _clip(100 / (1 + peg))
        elif upside is not None:
            scores['归一'] = upside_bucket(upside)
        else:
            flags.append('归一估值缺失')
        if not scores:
            return None, flags
        total_w = sum(weights[k] for k in scores)
        vs = _clip(sum(scores[k] * weights[k] for k in scores) / total_w)

        # ── Cycle Normalization (spec 第十/十二节): 周期股利润高位时 PEG 失真 ──
        is_cyclical = any(c in theme for c in CYCLICAL_INDUSTRIES)
        high_cycle = (cycle_score or 0) >= 75 or cycle_stage in ('主升', '景气上行')
        if is_cyclical and high_cycle:
            deduct = 10
            if peg is not None and peg < 0.35:
                deduct += 10   # 极低PEG + 周期高位 → 利润或处周期顶
            if cycle_stage in ('主升',):
                deduct += 5
            deduct = min(deduct, 25)
            vs = _clip(vs - deduct)
            flags.append(f'周期股景气高位,估值安全-{deduct}')
        return vs, flags

    def industry_cycle(self, row) -> Tuple[Optional[float], List[str]]:
        stage = str(row.get('行业景气阶段', '')).strip()
        cycle_score = _num(row, 'IndustryCycleScore')
        hint = str(row.get('增强提示', ''))
        flags = []
        stage_score = CYCLE_STAGE_SCORE.get(stage, None)
        if stage_score is None and cycle_score is not None:
            stage_score = _clip(cycle_score)
            flags.append('景气阶段未知,用分数')
        if stage_score is None and cycle_score is None:
            return None, flags
        # 景气方向变化: 用增强提示辅助 (spec 第十三节)
        if '景气下行' in hint:
            stage_score = _clip(stage_score - 15)
            flags.append('增强提示景气下行-15')
        if cycle_score is not None:
            score = _clip(stage_score * 0.8 + cycle_score * 0.2)
        else:
            score = _clip(stage_score)
        return score, flags

    def technical_position(self, tech: Optional[dict]) -> Tuple[Optional[float], str, List[str]]:
        """spec 十四~十六节: A/B/C/D/E 五级 + 追高惩罚"""
        if tech is None:
            return None, 'N/A', ['技术数据缺失']
        close, ma20, ma60 = tech['close'], tech['ma20'], tech['ma60']
        dist_hi, slope20, slope60 = tech['dist_hi'], tech['ma20_slope'], tech['ma60_slope']
        vr = tech['vol_ratio']
        flags = []
        if ma20 is None or ma60 is None:
            return None, 'N/A', ['均线数据不足']

        grade, score = 'N/A', None
        if close > ma20 > ma60:
            # 趋势向上
            if slope20 is not None and slope20 > 0 and dist_hi is not None:
                # A级: 趋势+回踩(距前高5~15%, 缩量)
                if 5 <= dist_hi <= 15 and vr is not None and vr < 1.2:
                    base = 95
                    if vr is not None and 0.6 <= vr <= 0.95:
                        base = 100
                    elif vr is not None and vr >= 1.5:
                        base = 88
                    score, grade = base, 'A-趋势回踩'
                # C级: MA20附近回踩
                elif abs(close / ma20 - 1) <= 0.03 and slope20 > 0 and \
                        (vr is None or vr < 1.2) and (slope60 is None or slope60 > 0):
                    score, grade = 90, 'C-MA20回踩'
                # B级: 趋势良好但接近前高
                elif dist_hi is not None and dist_hi < 5:
                    score = 78 if dist_hi >= 2 else 72
                    grade = 'B-接近前高'
                else:
                    score, grade = 82, 'A-趋势健康'
            else:
                score, grade = 78, 'B-趋势良好'
        elif ma60 is not None and close > ma60:
            # D级: 中期调整, MA60 仍向上
            base = 78 if (slope60 or 0) > 0 else 68
            score, grade = base, 'D-中期调整'
        else:
            # E级: 趋势破坏
            score, grade = 45, 'E-趋势破坏'
            if slope60 is not None and slope60 < 0:
                score, grade = 38, 'E-趋势破坏(MA60向下)'

        # 追高惩罚 (spec 十六节)
        if dist_hi is not None and dist_hi < 3 and score > 80:
            score = min(score, 80)
            flags.append(f'距52周高仅{dist_hi:.1f}%,追高惩罚→80')
        # 距高>25%: 不自动便宜, 检查趋势
        if dist_hi is not None and dist_hi > 25 and grade in ('B-接近前高', 'A-趋势健康'):
            flags.append('距52周高>25%,需确认趋势未破坏')
        # 量比异常放大(非缩量健康)
        if vr is not None and vr > 2.5 and dist_hi is not None and dist_hi < 10:
            flags.append(f'量比{vr:.1f}放量滞涨,警惕')
        return _clip(score), grade, flags

    # ─────────────────────────────────────────────
    # MBS V2: Entry Score 买点状态引擎
    # Entry Score 只回答"当前位置是否适合中线介入", 不重新评价基本面
    # ─────────────────────────────────────────────
    def entry_score(self, tech: Optional[dict]) -> Tuple[Optional[float], str, str, List[str]]:
        """返回 (EntryScore, 回踩状态, PullbackQuality, flags)"""
        if tech is None or tech['ma20'] is None or tech['ma60'] is None:
            return None, 'N/A', 'N/A', ['技术数据缺失']
        close = tech['close']
        ma20, ma30, ma60 = tech['ma20'], tech['ma30'], tech['ma60']
        slope20, slope60 = tech['ma20_slope'], tech['ma60_slope']
        vr, chg = tech['vol_ratio'], tech['price_chg']
        dist_hi = tech['dist_hi']
        d20 = (close / ma20 - 1.0) * 100 if ma20 else None
        d30 = (close / ma30 - 1.0) * 100 if ma30 else None
        d60 = (close / ma60 - 1.0) * 100 if ma60 else None
        drawdown = dist_hi if dist_hi is not None else None  # (close-hi52)/hi52
        flags = []
        parts = {}

        # ① Trend Structure 25%
        if close > ma20 > ma60 and slope20 is not None and slope20 > 0 and slope60 is not None and slope60 > 0:
            ts, state = 98, '强趋势'
        elif close > ma20 > ma60:
            ts = 90
            state = '上升趋势'
        elif close < ma20 and close > ma60 and (slope60 or 0) > 0:
            ts = 75
            state = '中期调整'
        else:
            ts = 38 if (slope60 or 0) < 0 else 50
            state = '趋势破坏'
        parts['趋势'] = ts * 0.25

        # ② MA20/MA30 Position 25% (中线回踩核心)
        ref = d30 if (d30 is not None and d20 is not None and abs(d30) < abs(d20)) else d20
        ma_up = slope20 is not None and slope20 > 0
        if d20 is None:
            mp = 40
            state += '/均线偏离未知'
        elif -3 <= d20 <= 3 and ma_up:
            mp = 95 if vr is None or vr < 1.0 else 90
            if state == '强趋势' or state == '上升趋势':
                state = '接近MA20回踩' if d20 < 0 else 'MA20附近'
        elif 3 < d20 <= 7:
            mp = 82
            if state in ('强趋势', '上升趋势'):
                state = '略高于MA20'
        elif 7 < d20 <= 12:
            mp = 62
            if state in ('强趋势', '上升趋势'):
                state = '偏高'
        elif d20 > 12:
            mp = 40
            state = '严重偏离'
        else:  # d20 < -3 (深度回撤)
            if (slope60 or 0) > 0:
                mp = 68
                state = '深度回撤-趋势未坏'
            else:
                mp = 35
                state = '深度回撤-趋势破坏'
        # MA30 加分参考: 价格站上MA30 且 MA30向上 → 结构更稳
        if d30 is not None and d30 >= 0 and tech['ma30_slope'] is not None and tech['ma30_slope'] > 0 and mp < 95:
            mp = min(mp + 3, 95)
        parts['MA位置'] = mp * 0.25

        # ③ MA60 Position 15%
        if d60 is not None and d60 > 0 and (slope60 or 0) > 0:
            ms = 95
        elif d60 is not None and abs(d60) <= 2 and (slope60 or 0) > 0:
            ms = 82
        elif d60 is not None and d60 < 0 and (slope60 or 0) < 0:
            ms = 30
        elif d60 is not None and d60 < 0:
            ms = 55
        else:
            ms = 70
        parts['MA60'] = ms * 0.15

        # ④ Drawdown Quality 20%
        dd = drawdown if drawdown is not None else 0.0
        if -12 <= dd <= -5 and (slope20 or 0) > 0 and (slope60 or 0) > 0:
            dq, dd_state = 95, '健康回踩'
        elif -5 <= dd <= 0:
            dq, dd_state = 78, '小幅回撤'
        elif -20 < dd < -12:
            dq = 62 if (slope60 or 0) > 0 else 45
            dd_state = '深度回撤' if (slope60 or 0) > 0 else '深度回撤-破位'
        elif dd <= -20:
            dq = 50 if (slope60 or 0) > 0 and (vr is not None and vr < 1.0) else 35
            dd_state = '极端回撤-企稳' if dq >= 50 else '极端回撤'
        else:  # dd > 0 创新高
            dq = 65
            dd_state = '创新高'
        parts['回撤'] = dq * 0.20

        # ⑤ Volume Behavior 15%
        if vr is None:
            vq = 70
        elif vr < 0.8 and (d20 is not None and -3 <= d20 <= 3):
            vq = 95   # 缩量回踩
        elif 0.8 <= vr <= 1.2:
            vq = 78
        elif vr > 1.5 and chg is not None and chg < 0:
            vq = 30   # 放量下跌
        elif vr > 1.2:
            vq = 60
        else:
            vq = 75
        if vr is not None and vr < 0.8 and state == '健康回踩':
            state = '健康回踩-缩量'
        parts['量能'] = vq * 0.15

        entry = _clip(sum(parts.values()))
        # 回踩状态合并
        if state not in ('深度回撤-趋势未坏', '深度回撤-趋势破坏', '极端回撤', '极端回撤-企稳', '趋势破坏'):
            state = dd_state if dd_state in ('健康回踩',) else state

        # ── 追高惩罚 (spec 十节) ──
        if dist_hi is not None and dist_hi > -3 and d20 is not None and d20 > 8 and entry > 65:
            entry = min(entry, 65)
            flags.append(f'接近前高({dist_hi:.1f}%)且距MA20({d20:.1f}%)过远,Entry≤65')

        # ── 健康回踩识别器 (spec 十一节) ──
        pq = 'N/A'
        healthy = (close > ma60) and (slope60 or 0) > 0 and -8 <= dd <= -3 and \
                  (d20 is not None and -3 <= d20 <= 3) and (vr is None or vr < 1.0)
        if healthy:
            pq = 'A'
            if entry < 85:
                entry = min(entry + 4, 85)
        elif (close > ma60) and (slope60 or 0) > 0 and -3 < dd < 0:
            pq = 'B'
        elif (close > ma60) and (slope60 or 0) > 0 and (d20 is not None and -3 <= d20 <= 3):
            pq = 'C'
        return _clip(entry), state, pq, flags

    # ── Quality × Timing 二维矩阵 (spec 十五节) ──
    @staticmethod
    def matrix_cell(mbs: float, entry: float) -> str:
        def band(e):
            return '高' if e >= 80 else ('中' if e >= 65 else '低')
        eb = band(entry)
        if mbs >= 85:
            return 'CORE BUY' if eb == '高' else ('WAIT' if eb == '中' else 'WAIT PULLBACK')
        if mbs >= 78:
            return 'PULLBACK READY' if eb == '高' else ('BUY ON PULLBACK' if eb == '中' else 'WAIT')
        if mbs >= 70:
            return 'WATCH BUY' if eb == '高' else ('WATCH' if eb == '中' else 'WAIT')
        return 'WATCH' if eb == '高' else 'AVOID'

    # ── V2 最终信号 (spec 十三节): MBS × Entry × Confidence ──
    @staticmethod
    def signal_v2(final: float, entry: Optional[float], conf: float, fq: Optional[float]) -> str:
        if fq is not None and fq < 50:
            return 'WATCH' if final >= 65 else 'AVOID'
        if final >= 85 and entry is not None and entry >= 85 and conf >= 80:
            return 'CORE BUY'
        if final >= 78 and entry is not None and entry >= 85 and conf >= 75:
            return 'PULLBACK READY'
        if final >= 75 and entry is not None and 70 <= entry < 85:
            return 'BUY ON PULLBACK'
        if final >= 75 and (entry is None or entry < 70):
            return 'WAIT FOR DEEPER PULLBACK'
        if 65 <= final < 75:
            return 'WATCH'
        return 'AVOID'

    # ── 升级触发条件 (spec 十八节) ──
    @staticmethod
    def next_triggers(state: str, tech: Optional[dict]) -> str:
        if tech is None:
            return '技术数据缺失,补齐后给出触发条件'
        tr = []
        if tech['ma20'] is not None:
            tr.append('1.回踩MA20 ±3%')
        if tech['vol_ratio'] is not None:
            tr.append(f"2.量比降至{max(0.8, round((tech['vol_ratio'] or 0) * 0.8, 2))}以下(缩量)")
        if tech['ma20_slope'] is not None:
            tr.append('3.MA20维持向上')
        tr.append('4.不出现放量跌破MA60')
        return '  '.join(tr) + '; 满足3/4条 → 信号升级'

    # ─────────────────────────────────────────────
    # MBS V3: Pullback State / PCS / Maturity / Signal
    # ─────────────────────────────────────────────
    @staticmethod
    def pullback_state_v3(tech: Optional[dict]) -> str:
        """V3 八状态回踩分类:按距MA20 + 距前高 + MA斜率综合判定"""
        if tech is None or tech.get('ma20') is None or tech.get('ma60') is None:
            return 'N/A'
        close = tech['close']
        ma20, ma60 = tech['ma20'], tech['ma60']
        d20 = (close / ma20 - 1.0) * 100
        d60 = (close / ma60 - 1.0) * 100
        dd = tech.get('dist_hi')  # (close-hi)/hi, 负数表示回撤
        slope20 = tech.get('ma20_slope')
        slope60 = tech.get('ma60_slope')
        up20 = slope20 is not None and slope20 > 0
        up60 = slope60 is not None and slope60 > 0
        below20 = close < ma20
        above60 = close > ma60
        drawdown = dd if dd is not None else 0.0

        # ⑧ 趋势破坏 (优先级最高)
        if (not above60 and not up60) or (close < ma60 and ma20 < ma60 and not up20 and not up60):
            return '趋势破坏'

        # ① 健康回踩: Price 在 MA20 ±3%, MA60向上, 回撤 -5%~-20%
        if -3 <= d20 <= 3 and up60 and -20 <= drawdown <= -5:
            return '健康回踩'

        # ② MA20附近浅回踩: Price 在 MA20 ±3%, 但回撤 < -5% 也就是幅度小
        if -3 <= d20 <= 3 and drawdown > -5:
            return 'MA20附近浅回踩'

        # ⑦ MA60附近回踩: deeper 一级的中线回踩
        if -3 <= d60 <= 3 and up60 and below20:
            return 'MA60附近回踩'

        # ③ 回踩接近区: +3%~+7% + 回撤≥5% + MA20/60向上
        if 3 < d20 <= 7 and drawdown <= -5 and up20 and up60:
            return '回踩接近区'

        # ④ 深度回撤-趋势未坏: Price<MA20 且 Price>MA60 且 MA60向上 且 回撤≤-15%
        if below20 and above60 and up60 and drawdown <= -15:
            return '深度回撤-趋势未坏'

        # ⑤ 偏高: +7%~+12%, MA20向上
        if 7 < d20 <= 12 and up20:
            return '偏高'

        # ⑥ 高位严重偏离: d20 > +12%
        if d20 > 12:
            if drawdown > -5:
                return '高位严重偏离'
            return '严重偏离'

        # 兜底: MA20上方一点但其他条件不满足 → "略高于MA20"
        if 0 <= d20 <= 3 and up20:
            return '略高于MA20'
        if -3 <= d20 < 0 and up20:
            return '接近MA20回踩'
        if below20 and above60 and up60:
            return '中期调整'
        return '趋势待观察'

    @staticmethod
    def pcs_score(tech: Optional[dict]) -> Optional[float]:
        """Pullback Confirmation Score 回踩确认分
        PCS = MA支撑×30% + 缩量×25% + 价格企稳×25% + 短期反弹×20%
        范围 0~100, 判断"回踩之后是否出现止跌企稳"
        """
        if tech is None or tech.get('ma20') is None:
            return None
        close = tech['close']
        ma20 = tech['ma20']
        ma60 = tech.get('ma60')
        d20 = (close / ma20 - 1.0) * 100 if ma20 else None
        d60 = (close / ma60 - 1.0) * 100 if ma60 else None
        vr = tech.get('vol_ratio')
        chg = tech.get('price_chg')
        lows_rising = tech.get('lows_rising')
        no_new_low_2d = tech.get('no_new_low_2d')
        last_up = tech.get('last_up')
        close_to_high = tech.get('close_to_high')
        slope60 = tech.get('ma60_slope')

        parts = {}

        # ① MA支撑 30%
        if d20 is not None and abs(d20) <= 2:
            # 最近低点没有有效跌破MA20(简化: 当前价距MA20±2%以内)
            ma_sup = 95
        elif d20 is not None and 2 < abs(d20) <= 5:
            ma_sup = 78
        elif d20 is not None and d20 < -5 and ma60 is not None and close > ma60 and (slope60 or 0) > 0:
            # MA20失守但MA60支撑(MA60向上)
            ma_sup = 70 if abs(d60) <= 5 else 60
        elif d20 is not None and d20 > 5:
            # 偏离MA20上方,不存在支撑
            ma_sup = 30
        else:
            ma_sup = 50
        parts['MA支撑'] = ma_sup * 0.30

        # ② Volume Contraction 缩量 25%
        if vr is None:
            vc = 65
        elif vr < 0.70:
            vc = 100
        elif vr < 0.85:
            vc = 90
        elif vr < 1.00:
            vc = 75
        elif vr < 1.20:
            vc = 55
        elif vr < 1.50:
            vc = 35
        else:
            vc = 20
        # 放量下跌强惩罚
        if vr is not None and vr > 1.5 and chg is not None and chg < 0:
            vc = min(vc, 15)
        parts['缩量'] = vc * 0.25

        # ③ Price Stabilization 价格企稳 25%
        if lows_rising is True and no_new_low_2d is True:
            ps = 95
        elif no_new_low_2d is True and lows_rising is not False:
            ps = 78
        elif no_new_low_2d is False:
            ps = 35
        elif lows_rising is False:
            ps = 40
        else:
            ps = 55
        parts['企稳'] = ps * 0.25

        # ④ Short-term Rebound 短期反弹 20%
        sr = 40
        if last_up is True and close_to_high is not None and close_to_high > 0.6:
            sr = 60
        if last_up is True and no_new_low_2d is True and close_to_high is not None and close_to_high > 0.5:
            sr = 82
        if last_up is True and lows_rising is True and close_to_high is not None and close_to_high > 0.6:
            sr = 92
        if last_up is False or (chg is not None and chg < -1.5):
            sr = min(sr, 35)
        parts['反弹'] = sr * 0.20

        return _clip(sum(parts.values()))

    @staticmethod
    def entry_maturity(entry: Optional[float], pcs: Optional[float]) -> str:
        """Entry Maturity A/B/C/D 四级成熟度"""
        if entry is None or pcs is None:
            return ''
        if entry >= 80 and pcs >= 75:
            return 'A'
        if entry >= 75 and 50 <= pcs < 75:
            return 'B'
        if entry >= 65 and pcs < 50:
            return 'C'
        if entry < 65:
            return 'D'
        return 'C'

    @staticmethod
    def signal_v3(final: float, entry: Optional[float], pcs: Optional[float],
                  conf: float, fq: Optional[float], pullback_state: str,
                  d_ma20: Optional[float], d_hi: Optional[float]) -> str:
        """V3 七态信号: CORE BUY / PULLBACK READY / BUY ON PULLBACK /
        WATCH PULLBACK / WAIT FOR DEEPER PULLBACK / WATCH / AVOID
        """
        # 基本面太差 → 直接 AVOID / WATCH
        if fq is not None and fq < 50:
            return 'WATCH' if final >= 65 else 'AVOID'
        # 趋势破坏 → ≤ WATCH
        if pullback_state == '趋势破坏':
            return 'WATCH' if final >= 65 else 'AVOID'

        # Rule 1: 距MA20 > +12% → 不能 BUY ON PULLBACK
        # Rule 2: 距MA20 > +7% 且 距前高 > -5% → WAIT FOR DEEPER PULLBACK
        high_extended = d_ma20 is not None and d_ma20 > 12
        near_high_extended = (d_ma20 is not None and d_ma20 > 7 and
                              d_hi is not None and d_hi > -5)

        # CORE BUY
        if final >= 85 and entry is not None and entry >= 85 and pcs is not None and pcs >= 75 and conf >= 80:
            return 'CORE BUY'
        # PULLBACK READY
        if final >= 78 and entry is not None and entry >= 80 and pcs is not None and pcs >= 70 and conf >= 75:
            return 'PULLBACK READY'

        # BUY ON PULLBACK: 位置不错 (PCS 高则更接近确认, PCS 低则还需等待)
        if final >= 75 and entry is not None and entry >= 70 and not high_extended:
            return 'BUY ON PULLBACK'

        # WATCH PULLBACK: 值得跟踪但当前位置不够理想
        if final >= 75 and entry is not None and entry >= 65 and not high_extended:
            if pcs is not None and pcs < 50:
                return 'WATCH PULLBACK'

        # WAIT FOR DEEPER PULLBACK: 好公司但位置偏高
        if final >= 75:
            if high_extended or near_high_extended:
                return 'WAIT FOR DEEPER PULLBACK'
            if entry is not None and entry < 70:
                return 'WAIT FOR DEEPER PULLBACK'
            if entry is None:
                return 'WAIT FOR DEEPER PULLBACK'

        # WATCH
        if 65 <= final < 75:
            return 'WATCH'
        return 'AVOID'

    # ─────────────────────────────────────────────
    # MBS V4: 状态机 / 反弹质量 / 成熟度V2 / 信号V4 /
    #         Trading Score / Position / 触发失效 / 一句话结论
    # ─────────────────────────────────────────────

    # --- 状态常量(优先级顺序) ---
    _STATES_V4 = [
        'TREND_BREAKDOWN',      # 0 趋势破坏
        'EXTREME_EXTENSION',    # 1 高位严重偏离
        'MA60_PULLBACK',        # 2 MA60附近回踩
        'DEEP_PULLBACK_TREND_INTACT',  # 3 深度回撤趋势未坏
        'HEALTHY_PULLBACK',     # 4 健康回踩
        'PULLBACK_APPROACHING', # 5 回踩接近区
        'SHALLOW_PULLBACK',     # 6 MA20附近浅回踩
        'TREND_HEALTHY',        # 7 趋势良好
        'BREAKOUT',             # 8 突破
        'EXTENDED',             # 9 偏高
        'TREND_AT_RISK',        # 10 趋势转弱(Price>MA60且MA60向上,但MA20走平/拐头)
        'TREND_WEAK',           # 11 中期偏弱(Price>MA20>MA60但MA60斜率略负,不算破坏)
    ]
    _STATE_CN = {
        'TREND_BREAKDOWN': '趋势破坏',
        'EXTREME_EXTENSION': '高位严重偏离',
        'MA60_PULLBACK': 'MA60附近回踩',
        'DEEP_PULLBACK_TREND_INTACT': '深度回撤-趋势未坏',
        'HEALTHY_PULLBACK': '健康回踩',
        'PULLBACK_APPROACHING': '回踩接近区',
        'SHALLOW_PULLBACK': 'MA20附近浅回踩',
        'TREND_HEALTHY': '趋势良好',
        'BREAKOUT': '突破新高',
        'EXTENDED': '偏高',
        'TREND_AT_RISK': '趋势转弱',
        'TREND_WEAK': '中期偏弱',
    }

    def pullback_state_v4(self, tech: Optional[dict]) -> str:
        """V4 十状态 + 严格优先级。返回英文状态码。"""
        if tech is None or tech.get('ma20') is None or tech.get('ma60') is None:
            return 'N/A'
        close = tech['close']
        ma20, ma60 = tech['ma20'], tech['ma60']
        d20 = (close / ma20 - 1.0) * 100
        d60 = (close / ma60 - 1.0) * 100
        dd = tech.get('dist_hi') or 0.0
        s20 = tech.get('ma20_slope') or 0.0
        s60 = tech.get('ma60_slope') or 0.0
        up20 = s20 > 0
        up60 = s60 > 0

        # 1. TREND_BREAKDOWN: 跌破MA60且MA60下行
        if close < ma60 and not up60:
            return 'TREND_BREAKDOWN'
        if close < ma60 and ma20 < ma60 and not up20:
            return 'TREND_BREAKDOWN'

        # 2. EXTREME_EXTENSION: 距MA20 > +12%
        if d20 > 12:
            return 'EXTREME_EXTENSION'

        # 3. MA60_PULLBACK: MA60 ±3% 且 Price<MA20 且 MA60向上
        if abs(d60) <= 3 and close < ma20 and up60:
            return 'MA60_PULLBACK'

        # 4. DEEP_PULLBACK_TREND_INTACT: Price<MA20且<-3% 且 回撤≤-15% 且 >MA60 且 MA60向上
        if d20 < -3 and close > ma60 and up60 and dd <= -15:
            return 'DEEP_PULLBACK_TREND_INTACT'

        # 5. HEALTHY_PULLBACK: MA20 ±3% 且 MA60向上 且 回撤-5%~-20%
        if -3 <= d20 <= 3 and up60 and -20 <= dd <= -5:
            return 'HEALTHY_PULLBACK'

        # 6. PULLBACK_APPROACHING: +3%~+7% 且 回撤≥5% 且 MA20/60向上
        if 3 < d20 <= 7 and dd <= -5 and up20 and up60:
            return 'PULLBACK_APPROACHING'

        # 7. SHALLOW_PULLBACK: MA20 ±3% 但 回撤 <5% (幅度小)
        if -3 <= d20 <= 3 and dd > -5 and up20:
            return 'SHALLOW_PULLBACK'

        # 8. TREND_HEALTHY: Price>MA20>MA60 且 双向上 且 偏离不大
        if close > ma20 > ma60 and up20 and up60 and 0 <= d20 <= 5:
            return 'TREND_HEALTHY'

        # 9. BREAKOUT: 接近前高(>-3%) 且 距MA20+7%~+12% 且 MA20向上
        if dd > -3 and up20 and d20 > 7:
            return 'BREAKOUT'

        # 10. EXTENDED: +7%~+12% 且 MA20向上
        if 7 < d20 <= 12 and up20:
            return 'EXTENDED'

        # 兜底分层:
        # Price > MA20 → 永远不是趋势破坏,充其量中期偏弱
        if close > ma20:
            return 'TREND_WEAK' if not up60 else 'TREND_AT_RISK'
        # Price 在 MA60 上方且 MA60 向上 → 趋势未坏
        if close > ma60 and up60:
            return 'TREND_AT_RISK'
        # 跌破 MA60 且 MA60 下行 → 趋势破坏
        return 'TREND_BREAKDOWN'

    def rebound_quality_v4(self, tech: Optional[dict]) -> str:
        """反弹质量 A/B/C:健康反弹/普通反弹/情绪反抽"""
        if tech is None or tech.get('ma20') is None:
            return ''
        vr = tech.get('vol_ratio')
        chg = tech.get('price_chg')
        last_up = tech.get('last_up')
        lows_rising = tech.get('lows_rising')
        no_new_low = tech.get('no_new_low_2d')
        close = tech['close']
        ma20 = tech['ma20']
        below_ma20 = close < ma20

        # C: 情绪反抽 — 暴跌后巨量反弹但仍低于MA20
        if vr is not None and vr > 1.5 and last_up and chg is not None and chg > 3 and below_ma20:
            return 'C'
        # A: 健康反弹 — 缩量下跌→企稳→温和放量上涨
        if (vr is not None and 0.8 <= vr <= 1.2 and last_up is True and
            no_new_low is True and lows_rising is True):
            return 'A'
        # B: 普通反弹 — 缩量下跌后单日反弹
        if last_up is True and no_new_low is True:
            return 'B'
        if last_up is True and vr is not None and vr < 1.0:
            return 'B'
        return ''

    def maturity_v4(self, mbs: Optional[float], entry: Optional[float],
                    pcs: Optional[float], state: str) -> str:
        """Maturity V2: A=CONFIRMED B=READY C=WATCHING D=INVALID
        必须同时考虑 MBS/Entry/PCS/State
        """
        if mbs is None or entry is None or pcs is None:
            return ''
        # D: INVALID
        if entry < 65 or state in ('TREND_BREAKDOWN', 'EXTREME_EXTENSION', 'N/A'):
            return 'D'
        # A: CONFIRMED
        if mbs >= 78 and entry >= 80 and pcs >= 75:
            return 'A'
        # B: READY
        if mbs >= 75 and entry >= 75 and pcs >= 60:
            return 'B'
        # C: WATCHING
        if mbs >= 75:
            return 'C'
        return 'D'

    def quality_pullback_watch(self, mbs: Optional[float], entry: Optional[float],
                               pcs: Optional[float]) -> bool:
        """QUALITY_PULLBACK_WATCH 标签:好公司但买点没完全成熟"""
        if mbs is None or entry is None or pcs is None:
            return False
        return mbs >= 80 and 65 <= entry < 80 and pcs < 70

    def signal_v4(self, mbs: Optional[float], entry: Optional[float],
                  pcs: Optional[float], conf: float, fq: Optional[float],
                  state: str, d_ma20: Optional[float], d_hi: Optional[float],
                  market: str = '震荡市') -> str:
        """V4 信号: S/A/B/C/D/F 七级
        S=CORE BUY  A=PULLBACK CONFIRMED  B=BUY ON PULLBACK
        C=QUALITY WATCH  D=WAIT  F=AVOID
        """
        if mbs is None:
            return 'F'
        # F: AVOID (最高优先级)
        if mbs < 65:
            return 'F'
        if state == 'TREND_BREAKDOWN':
            return 'F' if mbs < 70 else 'D'
        if fq is not None and fq < 50:
            return 'F'

        is_bear = '弱' in market or '熊' in market
        high_ext = d_ma20 is not None and d_ma20 > 12
        near_high_ext = (d_ma20 is not None and d_ma20 > 7 and
                         d_hi is not None and d_hi > -5)

        # S: CORE BUY
        if mbs >= 85 and entry is not None and entry >= 85 and pcs is not None and pcs >= 75 \
           and conf >= 80 and not is_bear and not high_ext:
            return 'S'

        # A: PULLBACK CONFIRMED
        if mbs >= 78 and entry is not None and entry >= 80 and pcs is not None and pcs >= 75 \
           and conf >= 75 and state in ('HEALTHY_PULLBACK', 'PULLBACK_APPROACHING', 'MA60_PULLBACK') \
           and not high_ext:
            return 'A'

        # C: QUALITY WATCH (高MBS+中等Entry+低PCS,优先于B)
        if self.quality_pullback_watch(mbs, entry, pcs):
            return 'C'

        # B: BUY ON PULLBACK
        if mbs >= 75 and entry is not None and entry >= 70 and pcs is not None and pcs >= 50 \
           and not high_ext and state != 'EXTREME_EXTENSION':
            if pcs < 60:
                return 'B/W'  # BUY ON PULLBACK / WAIT CONFIRMATION
            return 'B'

        # D: WAIT / WAIT FOR DEEPER PULLBACK
        if mbs >= 75:
            if high_ext or near_high_ext:
                return 'D+'  # WAIT FOR DEEPER PULLBACK
            if entry is not None and entry < 70:
                return 'D'
        # WATCH PULLBACK
        if mbs >= 70 and entry is not None and entry >= 60 and state != 'TREND_BREAKDOWN':
            return 'D'
        # 默认
        return 'D' if mbs >= 65 else 'F'

    def trading_score_v4(self, mbs: Optional[float], entry: Optional[float],
                         pcs: Optional[float], conf: float, market_adj: float) -> Optional[float]:
        """Trading Score = MBS×40% + Entry×25% + PCS×15% + Conf×10% + Market Fit×10%
        Market Fit 用 market_adj 映射: +5→95  0→75  -5→60  -10→40
        """
        if mbs is None or entry is None or pcs is None:
            return None
        # Market Fit 分数化
        if market_adj >= 5:
            mfit = 95
        elif market_adj >= 3:
            mfit = 85
        elif market_adj >= 0:
            mfit = 75
        elif market_adj >= -5:
            mfit = 60
        else:
            mfit = 40
        ts = mbs * 0.40 + entry * 0.25 + pcs * 0.15 + conf * 0.10 + mfit * 0.10
        return _clip(ts)

    def position_v4(self, signal: str, conf: float, market: str,
                    mbs: Optional[float] = None) -> Optional[float]:
        """建议仓位(%): S=8~12 A=5~8 B=3~5 C=0~3 D/F=0
        再 × Market Multiplier × Conf Factor
        """
        base_map = {
            'S': 10.0,
            'A': 6.5,
            'B': 4.0,
            'B/W': 3.0,
            'C': 2.0,
            'C+': 2.0,
            'D': 0.0,
            'D+': 0.0,
            'F': 0.0,
        }
        base = base_map.get(signal, 0.0)
        if base == 0:
            return 0.0
        # Market Multiplier
        if '强牛' in market or '强势' in market:
            mm = 1.00
        elif '牛' in market or '偏强' in market:
            mm = 0.95
        elif '复苏' in market or '恢复' in market:
            mm = 0.85
        elif '震荡' in market or '中性' in market:
            mm = 0.75
        elif '弱' in market:
            mm = 0.60
        elif '熊' in market:
            mm = 0.40
        else:
            mm = 0.75
        # Confidence Factor
        if conf >= 90:
            cf = 1.0
        elif conf >= 80:
            cf = 0.95
        elif conf >= 70:
            cf = 0.85
        elif conf >= 60:
            cf = 0.70
        else:
            cf = 0.50
        return round(base * mm * cf, 1)

    def trigger_v4(self, signal: str, tech: Optional[dict], mbs: Optional[float],
                   entry: Optional[float], pcs: Optional[float]) -> str:
        """下一步触发条件(动态生成,避免模板化)"""
        if tech is None:
            return '技术数据缺失'
        if signal in ('S', 'A'):
            return '已达买点,执行买入'
        items = []
        d20 = (tech['close'] / tech['ma20'] - 1) * 100 if tech.get('ma20') else None
        vr = tech.get('vol_ratio')
        # 位置条件
        if d20 is not None and d20 > 3:
            items.append(f'① 回踩MA20附近(当前+{d20:.1f}%→±3%)')
        elif d20 is not None and d20 < -5:
            items.append(f'① 企稳于MA20上方(当前{d20:.1f}%)')
        else:
            items.append('① 价格维持在MA20附近')
        # PCS条件
        if pcs is not None and pcs < 70:
            items.append(f'② PCS升至70+(当前{pcs:.0f})')
        else:
            items.append('② PCS维持在70以上')
        # 量能条件
        if vr is not None and vr > 1.0:
            items.append(f'③ 缩量企稳(vr {vr:.2f}→<0.9)')
        else:
            items.append('③ 成交量维持健康水平')
        # 趋势条件
        items.append('④ MA20/MA60保持向上,不出现放量跌破MA60')
        return '  '.join(items)

    def invalidation_v4(self, tech: Optional[dict]) -> str:
        """失效条件"""
        if tech is None:
            return '技术数据缺失'
        items = [
            '① 放量跌破MA60',
            '② MA60转为下降趋势',
            '③ 基本面预期明显恶化',
            '④ 行业景气度转弱',
        ]
        return '  '.join(items)

    def one_line_conclusion(self, name: str, mbs: Optional[float], entry: Optional[float],
                            pcs: Optional[float], state: str, signal: str,
                            d_ma20: Optional[float], d_hi: Optional[float]) -> str:
        """一句话结论(根据实际数据动态生成,非模板化)"""
        if mbs is None:
            return f'{name}:数据不足,暂不评估'
        state_cn = self._STATE_CN.get(state, state)
        mbs_desc = 'MBS极高' if mbs >= 85 else ('MBS高' if mbs >= 78 else ('MBS较高' if mbs >= 75 else 'MBS中等'))

        def _d20fmt(v):
            if v is None:
                return 'N/A'
            return f'+{v:.1f}%' if v >= 0 else f'{v:.1f}%'

        # 按状态生成不同句式
        if state == 'EXTREME_EXTENSION':
            d20s = _d20fmt(d_ma20)
            return f'{name}:{mbs_desc},基本面优秀;但当前距MA20 {d20s},接近前高,属于高位延伸,不追,等更深回踩'
        if state == 'HEALTHY_PULLBACK':
            if pcs is not None and pcs >= 75:
                return f'{name}:{mbs_desc},当前处于健康回踩区域,PCS {pcs:.0f} 已确认止跌,中线买点就绪'
            else:
                pcs_s = f'{pcs:.0f}' if pcs else 'N/A'
                return f'{name}:{mbs_desc},高质量成长股,当前处于健康回踩区域;PCS {pcs_s} 尚未完全确认,等待止跌信号'
        if state == 'PULLBACK_APPROACHING':
            d20s = _d20fmt(d_ma20)
            return f'{name}:{mbs_desc},已进入回踩接近区,距MA20 {d20s};继续等待回踩MA20附近并出现缩量企稳'
        if state == 'EXTENDED':
            d20s = _d20fmt(d_ma20)
            return f'{name}:{mbs_desc},龙头质量较高,但当前距MA20 {d20s},位置偏高;等待价格重新靠近MA20后再评估'
        if state == 'DEEP_PULLBACK_TREND_INTACT':
            return f'{name}:{mbs_desc},深度回撤但中期趋势未坏;关注MA60支撑和止跌信号'
        if state == 'MA60_PULLBACK':
            return f'{name}:{mbs_desc},回踩至MA60附近;若能在MA60上方缩量企稳可考虑'
        if state == 'TREND_BREAKDOWN':
            return f'{name}:中期趋势破坏,不考虑中线配置'
        if state == 'SHALLOW_PULLBACK':
            return f'{name}:{mbs_desc},MA20附近浅回踩,调整幅度有限;观察后续是否进一步消化'
        if state == 'TREND_HEALTHY':
            d20s = _d20fmt(d_ma20)
            return f'{name}:{mbs_desc},趋势结构良好,当前距MA20 {d20s};Entry 水平尚可,等待回踩确认'
        if state == 'BREAKOUT':
            return f'{name}:{mbs_desc},突破走势,接近前高;不追高,等回踩MA20/MA30后再评估'
        # 默认
        e_s = f'{entry:.0f}' if entry is not None else 'N/A'
        p_s = f'{pcs:.0f}' if pcs is not None else 'N/A'
        return f'{name}:{mbs_desc},{state_cn};Entry {e_s} PCS {p_s},需结合整体评估'

    # ─────────────────────────────────────────────
    # MBS V5: 调整充分性 / 黄金坑 / 买点类型 / Signal V5
    # 核心: 买跌不买涨,调整到位才是真买点
    # ─────────────────────────────────────────────

    def acs_score(self, tech: Optional[dict], mbs: Optional[float],
                  fq: Optional[float], val_score: Optional[float]) -> Optional[float]:
        """ACS = Adjustment Completeness Score 调整充分性评分
        判断"这轮回踩/调整是否到位",用于识别"跌出来的机会"。

        五维:
        - 空间到位 25% (回撤幅度 + 关键支撑位)
        - 时间到位 20% (调整天数够不够)
        - 量能萎缩 20% (缩量到极致 = 抛压衰竭)
        - 结构企稳 20% (止跌信号)
        - 估值消化 15% (回撤后估值是否回到合理)
        """
        if tech is None or tech.get('ma20') is None:
            return None
        dd = tech.get('dist_hi') or 0.0  # 回撤%, 负
        adj_days = tech.get('adj_days')
        vs5 = tech.get('vol_shrink_5d')
        vs10 = tech.get('vol_shrink_10d')
        s60 = tech.get('ma60_slope') or 0
        no_new_low = tech.get('no_new_low_2d')
        lows_r = tech.get('lows_rising')

        parts = {}

        # ① 空间到位 25%
        # 最佳回撤区间: -20%~-35% (中级调整,不破坏趋势)
        if -35 <= dd <= -20:
            space = 95
        elif -20 < dd <= -12:
            space = 82
        elif -12 < dd <= -7:
            space = 68
        elif -7 < dd <= -3:
            space = 55  # 小幅回踩,空间不够
        elif dd > -3:
            space = 30  # 接近新高,没调整
        elif dd < -35:
            space = 45 if s60 > 0 else 25  # 太深,趋势可能破坏
        # 额外:接近黄金分割50%位加分
        dfib = tech.get('dist_fib50')
        if dfib is not None and abs(dfib) <= 5 and s60 >= 0:
            space = min(space + 8, 100)
        parts['空间'] = space * 0.25

        # ② 时间到位 20%
        # 中线级调整至少20~40个交易日(1~2个月)才充分
        if adj_days is None:
            time_s = 50
        elif 20 <= adj_days <= 40:
            time_s = 90
        elif adj_days > 40:
            time_s = 95
        elif 10 <= adj_days < 20:
            time_s = 65
        else:
            time_s = 35  # <10天,下跌中继概率高
        parts['时间'] = time_s * 0.20

        # ③ 量能萎缩 20%
        # 缩量到20日均量的60%以下 = 抛压衰竭
        if vs10 is not None:
            if vs10 < 0.60:
                vol_s = 95
            elif vs10 < 0.75:
                vol_s = 85
            elif vs10 < 0.90:
                vol_s = 70
            elif vs10 < 1.10:
                vol_s = 50
            else:
                vol_s = 25
        elif vs5 is not None:
            vol_s = 70 if vs5 < 0.8 else 40
        else:
            vol_s = 50
        # 近5日持续缩量(5日<10日<20日)是最佳信号
        if vs5 is not None and vs10 is not None and vs5 < vs10 < 1.0:
            vol_s = min(vol_s + 10, 100)
        parts['量能'] = vol_s * 0.20

        # ④ 结构企稳 20% (直接复用PCS的企稳逻辑)
        if no_new_low is True and lows_r is True:
            struct = 90
        elif no_new_low is True:
            struct = 70
        elif lows_r is False:
            struct = 35
        else:
            struct = 50
        # 企稳在MA60上方更好
        close = tech['close']
        ma60 = tech['ma60']
        if ma60 is not None and close > ma60 and s60 >= 0:
            struct = min(struct + 5, 100)
        parts['结构'] = struct * 0.20

        # ⑤ 估值消化 15%
        # 好公司(MBS高)深跌后,估值往往更有吸引力
        if val_score is not None and mbs is not None:
            # 估值安全分本身高 + MBS质量高 → 估值消化到位
            val = min(val_score, 95)
        elif val_score is not None:
            val = val_score
        else:
            val = 60
        parts['估值'] = val * 0.15

        return _clip(sum(parts.values()))

    def golden_pit(self, mbs: Optional[float], entry: Optional[float],
                   pcs: Optional[float], acs: Optional[float],
                   fq: Optional[float], state_code: str,
                   tech: Optional[dict]) -> str:
        """黄金坑识别器
        黄金坑 = 好公司 + 深跌 + 缩量 + 企稳
        这是中长线最佳买点类型之一。

        返回: GOLD_PIT_A / GOLD_PIT_B / GOLD_PIT_C / ''
        """
        if mbs is None or acs is None or pcs is None:
            return ''
        # 基本前提:好公司(MBS≥75)
        if mbs < 75:
            return ''
        # 基本面不能太差
        if fq is not None and fq < 60:
            return ''
        # 调整充分(ACS≥65 且回撤≥12%且≤35%)
        dd = tech.get('dist_hi') if tech else None
        if acs < 65:
            return ''
        if dd is None or dd > -10:
            return ''  # 没跌够
        if dd < -40:
            return ''  # 跌太深,可能有基本面问题

        # A级黄金坑: 调整充分 + 止跌确认 + 量缩
        vs10 = tech.get('vol_shrink_10d') if tech else None
        if acs >= 80 and pcs >= 70 and (vs10 is None or vs10 < 0.85):
            return 'GOLD_PIT_A'
        # B级黄金坑: 调整较充分 + 初步企稳
        if acs >= 70 and pcs >= 55:
            return 'GOLD_PIT_B'
        # C级黄金坑: 接近调整到位,跟踪观察
        if acs >= 60:
            return 'GOLD_PIT_C'
        return ''

    def buy_point_type(self, state_code: str, acs: Optional[float],
                       golden_pit: str, tech: Optional[dict]) -> str:
        """买点类型分类
        返回: MA20_PULLBACK / MA60_PULLBACK / GOLD_PIT / BREAKOUT / TREND_FOLLOW / DEEP_ADJUST
        """
        if golden_pit:
            return '黄金坑'
        if state_code == 'HEALTHY_PULLBACK' or state_code == 'SHALLOW_PULLBACK':
            return 'MA20回踩'
        if state_code == 'MA60_PULLBACK':
            return 'MA60回踩'
        if state_code == 'DEEP_PULLBACK_TREND_INTACT':
            return '深度回踩'
        if state_code == 'PULLBACK_APPROACHING':
            return '接近回踩区'
        if state_code == 'EXTENDED' or state_code == 'EXTREME_EXTENSION':
            return '等待回踩'
        if state_code == 'TREND_HEALTHY' or state_code == 'TREND_AT_RISK' or state_code == 'TREND_WEAK':
            return '趋势跟踪'
        if state_code == 'BREAKOUT':
            return '突破型'
        if state_code == 'TREND_BREAKDOWN':
            return '趋势破坏'
        return '待观察'

    def signal_v5(self, mbs: Optional[float], entry: Optional[float],
                  pcs: Optional[float], acs: Optional[float],
                  conf: float, fq: Optional[float], state_code: str,
                  golden_pit: str, market: str,
                  d_ma20: Optional[float], d_hi: Optional[float]) -> str:
        """V5 信号: 融合ACS和黄金坑的"调整到位"逻辑
        核心: 买跌不买涨,调整充分+企稳才是真买点

        新增黄金坑信号:
          GP_A = 黄金坑A级 (调整充分+企稳确认)
          GP_B = 黄金坑B级 (调整较充分+初步企稳)
        """
        if mbs is None:
            return 'F'
        # F: AVOID
        if mbs < 65:
            return 'F'
        if state_code == 'TREND_BREAKDOWN':
            return 'F' if mbs < 70 else 'D'
        if fq is not None and fq < 50:
            return 'F'

        is_bear = '弱' in market or '熊' in market
        high_ext = d_ma20 is not None and d_ma20 > 12
        near_high_ext = (d_ma20 is not None and d_ma20 > 7 and
                         d_hi is not None and d_hi > -5)

        # ── 黄金坑优先识别 ──
        if golden_pit == 'GOLD_PIT_A' and mbs >= 78 and pcs is not None and pcs >= 70 \
           and acs is not None and acs >= 75 and conf >= 75 and not high_ext:
            return 'GP_A'  # 黄金坑A级
        if golden_pit == 'GOLD_PIT_B' and mbs >= 75 and pcs is not None and pcs >= 55 \
           and acs is not None and acs >= 65 and not high_ext:
            return 'GP_B'  # 黄金坑B级

        # ── 传统分级(保持V4逻辑) ──
        # S: CORE BUY
        if mbs >= 85 and entry is not None and entry >= 85 and pcs is not None and pcs >= 75 \
           and conf >= 80 and not is_bear and not high_ext:
            return 'S'
        # A: PULLBACK CONFIRMED
        if mbs >= 78 and entry is not None and entry >= 80 and pcs is not None and pcs >= 75 \
           and conf >= 75 and state_code in ('HEALTHY_PULLBACK', 'PULLBACK_APPROACHING', 'MA60_PULLBACK') \
           and not high_ext:
            return 'A'
        # B: BUY ON PULLBACK
        if mbs >= 75 and entry is not None and entry >= 70 and pcs is not None and pcs >= 50 \
           and not high_ext and state_code != 'EXTREME_EXTENSION':
            return 'B'
        # C: QUALITY WATCH (高MBS+中等Entry+低PCS)
        if mbs >= 80 and entry is not None and 65 <= entry < 80 and (pcs is None or pcs < 70):
            return 'C'
        # C+: 黄金坑观察
        if golden_pit == 'GOLD_PIT_C' and mbs >= 75:
            return 'C+'

        # D: WAIT
        if mbs >= 75:
            if high_ext or near_high_ext:
                return 'D+'
            if entry is not None and entry < 70:
                return 'D'
        if mbs >= 70 and entry is not None and entry >= 60 and state_code != 'TREND_BREAKDOWN':
            return 'D'
        return 'D' if mbs >= 65 else 'F'

    def position_v5(self, signal: str, conf: float, market: str,
                    mbs: Optional[float] = None) -> Optional[float]:
        """V5 仓位: S/A/B/C/D/F + GP_A/GP_B + C+ + D+
        GP_A 仓位介于 A 和 S 之间
        GP_B 仓位介于 B 和 A 之间
        """
        base_map = {
            'S': 10.0,
            'GP_A': 8.0,
            'A': 6.5,
            'GP_B': 5.0,
            'B': 4.0,
            'B/W': 3.0,
            'C+': 2.5,
            'C': 2.0,
            'D': 0.0,
            'D+': 0.0,
            'F': 0.0,
        }
        base = base_map.get(signal, 0.0)
        if base == 0:
            return 0.0
        # Market Multiplier
        if '强牛' in market or '强势' in market:
            mm = 1.00
        elif '牛' in market or '偏强' in market:
            mm = 0.95
        elif '复苏' in market or '恢复' in market:
            mm = 0.85
        elif '震荡' in market or '中性' in market:
            mm = 0.75
        elif '弱' in market:
            mm = 0.60
        elif '熊' in market:
            mm = 0.40
        else:
            mm = 0.75
        # Confidence Factor
        if conf >= 90:
            cf = 1.0
        elif conf >= 80:
            cf = 0.95
        elif conf >= 70:
            cf = 0.85
        elif conf >= 60:
            cf = 0.70
        else:
            cf = 0.50
        return round(base * mm * cf, 1)

    # ── V6: 底部质量评分 BQS ──
    def bottom_quality_score(self, tech: Optional[dict],
                             acs: Optional[float],
                             pcs: Optional[float]) -> Optional[float]:
        """BQS = Bottom Quality Score 底部质量评分
        综合评估底部形态的"可靠程度"，越高=底部越扎实=上涨概率越大。

        五维加权:
        - 双底形态 30% (W底/双底确认度)
        - 均线纠缠 20% (MA5/MA10/MA20粘合程度)
        - 调整充分 20% (ACS直接用)
        - 止跌确认 15% (PCS企稳部分)
        - 底部震荡 15% (底部停留天数+缩量)
        """
        if tech is None:
            return None
        scores = {}
        weights = {}

        # ① 双底形态 30%
        db_type = tech.get('db_type', '')
        db_conf = tech.get('db_confidence') or 0
        if db_type == 'DOUBLE_BOTTOM':
            db_s = min(95, db_conf + 10)
        elif db_type == 'W_BOTTOM':
            db_s = min(85, db_conf + 5)
        elif db_type == 'POTENTIAL_DB':
            db_s = min(60, db_conf)
        else:
            db_s = max(20, db_conf * 0.5)
        scores['双底'] = db_s
        weights['双底'] = 0.30

        # ② 均线纠缠 20%
        ma_conv = tech.get('ma_conv_score')
        if ma_conv is not None:
            scores['均线纠缠'] = ma_conv
        else:
            scores['均线纠缠'] = 40
        weights['均线纠缠'] = 0.20

        # ③ 调整充分 20% (ACS)
        if acs is not None:
            scores['调整充分'] = acs
        else:
            scores['调整充分'] = 50
        weights['调整充分'] = 0.20

        # ④ 止跌确认 15% (PCS)
        if pcs is not None:
            scores['止跌'] = pcs
        else:
            scores['止跌'] = 45
        weights['止跌'] = 0.15

        # ⑤ 底部震荡 15%
        dwell = tech.get('bottom_dwell_days')
        bvs = tech.get('bottom_vol_shrink')
        bottom_s = 40
        if dwell is not None:
            # 底部停留天数: >20天=90, 10-20=75, 5-10=55, <5=35
            if dwell >= 20:
                bottom_s = 90
            elif dwell >= 10:
                bottom_s = 75
            elif dwell >= 5:
                bottom_s = 55
            else:
                bottom_s = 35
        # 底部缩量加分
        if bvs is not None and bvs < 0.8:
            bottom_s = min(bottom_s + 10, 100)
        elif bvs is not None and bvs < 0.9:
            bottom_s = min(bottom_s + 5, 100)
        scores['底部震荡'] = bottom_s
        weights['底部震荡'] = 0.15

        total_w = sum(weights.values())
        if total_w <= 0:
            return None
        return _clip(sum(scores[k] * weights[k] for k in scores) / total_w)

    # ── V6: 黄金坑 V2 ──
    def golden_pit_v2(self, mbs: Optional[float], entry: Optional[float],
                      pcs: Optional[float], acs: Optional[float],
                      bqs: Optional[float], fq: Optional[float],
                      state_code: str, tech: Optional[dict]) -> str:
        """黄金坑识别器 V2
        在 V1 基础上加入 BQS 底部质量和双底形态确认。
        GP_A+: 双底确认 + BQS≥75 + ACS≥80 + PCS≥70
        GP_A:  双底或高质量单底 + BQS≥70 + ACS≥80 + PCS≥70
        GP_B:  BQS≥60 + ACS≥70 + PCS≥55
        GP_C:  BQS≥50 + ACS≥60
        """
        if mbs is None or acs is None or pcs is None or bqs is None:
            return ''
        if mbs < 75:
            return ''
        if fq is not None and fq < 60:
            return ''
        dd = tech.get('dist_hi') if tech else None
        if acs < 60:
            return ''
        if dd is None or dd > -10:
            return ''
        if dd < -40:
            return ''

        db_type = tech.get('db_type', '') if tech else ''
        has_db = db_type in ('DOUBLE_BOTTOM', 'W_BOTTOM')

        # GP_A+: 顶级黄金坑 (双底确认 + 全维度高分)
        if has_db and bqs >= 75 and acs >= 80 and pcs >= 70 and mbs >= 78:
            return 'GOLD_PIT_A_PLUS'
        # GP_A: A级黄金坑 (双底或高质量单底)
        if (has_db or bqs >= 72) and bqs >= 70 and acs >= 80 and pcs >= 70 and mbs >= 76:
            return 'GOLD_PIT_A'
        # GP_B: B级黄金坑
        if bqs >= 60 and acs >= 70 and pcs >= 55:
            return 'GOLD_PIT_B'
        # GP_C: C级黄金坑 (观察)
        if bqs >= 50 and acs >= 60:
            return 'GOLD_PIT_C'
        return ''

    # ── V6: 信号决策 ──
    def signal_v6(self, mbs: Optional[float], entry: Optional[float],
                  pcs: Optional[float], acs: Optional[float],
                  bqs: Optional[float], conf: float, fq: Optional[float],
                  state_code: str, golden_pit: str, market: str,
                  d_ma20: Optional[float], d_hi: Optional[float]) -> str:
        """V6 信号: 底部质量驱动的买跌不买涨体系
        核心: BQS 高的才是真底部，否则可能是下跌中继

        信号等级: GP_A+ / GP_A / S / GP_B / A / B / B/W / C+ / C / D+ / D / F
        """
        if mbs is None:
            return 'F'
        if mbs < 65:
            return 'F'
        if state_code == 'TREND_BREAKDOWN':
            return 'F' if mbs < 70 else 'D'
        if fq is not None and fq < 50:
            return 'F'

        is_bear = '弱' in market or '熊' in market
        high_ext = d_ma20 is not None and d_ma20 > 12
        near_high_ext = (d_ma20 is not None and d_ma20 > 7 and
                         d_hi is not None and d_hi > -5)

        # ── 黄金坑优先 (V2) ──
        if golden_pit == 'GOLD_PIT_A_PLUS' and mbs >= 78 and conf >= 75 and not high_ext:
            return 'GP_A+'
        if golden_pit == 'GOLD_PIT_A' and mbs >= 76 and conf >= 70 and not high_ext:
            return 'GP_A'
        if golden_pit == 'GOLD_PIT_B' and mbs >= 75 and not high_ext:
            return 'GP_B'

        # ── 传统分级 ──
        if mbs >= 85 and entry is not None and entry >= 85 and pcs is not None and pcs >= 75 \
           and conf >= 80 and not is_bear and not high_ext:
            return 'S'
        if mbs >= 78 and entry is not None and entry >= 80 and pcs is not None and pcs >= 75 \
           and conf >= 75 and state_code in ('HEALTHY_PULLBACK', 'PULLBACK_APPROACHING', 'MA60_PULLBACK') \
           and not high_ext:
            return 'A'
        if mbs >= 75 and entry is not None and entry >= 70 and pcs is not None and pcs >= 50 \
           and not high_ext and state_code != 'EXTREME_EXTENSION':
            return 'B'
        if mbs >= 80 and entry is not None and 65 <= entry < 80 and (pcs is None or pcs < 70):
            return 'C'
        # C+: 黄金坑观察 (GP_C 但 MBS 够)
        if golden_pit == 'GOLD_PIT_C' and mbs >= 75:
            return 'C+'

        # D 级
        if mbs >= 75:
            if high_ext or near_high_ext:
                return 'D+'
            if entry is not None and entry < 70:
                return 'D'
        if mbs >= 70 and entry is not None and entry >= 60 and state_code != 'TREND_BREAKDOWN':
            return 'D'
        return 'D' if mbs >= 65 else 'F'

    # ── V6: 仓位建议 ──
    def position_v6(self, signal: str, conf: float, market: str,
                    mbs: Optional[float] = None,
                    bqs: Optional[float] = None) -> Optional[float]:
        """V6 仓位: 加入 GP_A+ 和 BQS 微调
        GP_A+ = 顶级黄金坑,仓位最高(9%)
        GP_A  = 8%
        GP_B  = 5%
        BQS 加成: BQS≥75 加5%, BQS≥65 加3%
        """
        base_map = {
            'S': 10.0,
            'GP_A+': 9.0,
            'GP_A': 8.0,
            'A': 6.5,
            'GP_B': 5.0,
            'B': 4.0,
            'B/W': 3.0,
            'C+': 2.5,
            'C': 2.0,
            'D': 0.0,
            'D+': 0.0,
            'F': 0.0,
        }
        base = base_map.get(signal, 0.0)
        if base == 0:
            return 0.0
        # BQS 加成 (最多+5%)
        if bqs is not None and signal in ('GP_B', 'B', 'C+'):
            if bqs >= 75:
                base *= 1.05
            elif bqs >= 65:
                base *= 1.03
        # Market Multiplier
        if '强牛' in market or '强势' in market:
            mm = 1.00
        elif '牛' in market or '偏强' in market:
            mm = 0.95
        elif '复苏' in market or '恢复' in market:
            mm = 0.85
        elif '震荡' in market or '中性' in market:
            mm = 0.75
        elif '弱' in market:
            mm = 0.60
        elif '熊' in market:
            mm = 0.40
        else:
            mm = 0.75
        # Confidence Factor
        if conf >= 90:
            cf = 1.0
        elif conf >= 80:
            cf = 0.95
        elif conf >= 70:
            cf = 0.85
        elif conf >= 60:
            cf = 0.70
        else:
            cf = 0.50
        return round(base * mm * cf, 1)

    # ── V7: 下跌质量评分 DQS ──
    def drop_quality_score(self, tech: Optional[dict]) -> Optional[float]:
        """DQS = Drop Quality Score 下跌质量评分
        评估"这波下跌健康不健康"——健康的下跌是机会,不健康的下跌是陷阱。

        五维加权:
        - 头部质量 20% (顶部构筑越充分=出逃越彻底)
        - 下跌节奏 25% (急跌缓跌>匀速阴跌>放量恐慌)
        - 量价背离 25% (价跌量缩=抛压衰竭)
        - 支撑位 15% (关键支撑位企稳)
        - 下跌缩量 15% (下跌段整体缩量)
        """
        if tech is None:
            return None
        scores = {}
        weights = {}

        # ① 头部质量 20%
        top_q = tech.get('top_quality')
        if top_q is not None:
            scores['头部'] = top_q
        else:
            scores['头部'] = 50
        weights['头部'] = 0.20

        # ② 下跌节奏 25%
        fq = tech.get('fall_quality_score')
        if fq is not None:
            scores['节奏'] = fq
        else:
            scores['节奏'] = 50
        weights['节奏'] = 0.25

        # ③ 量价背离 25%
        vpd = tech.get('vpd_strength')
        if vpd is not None:
            scores['背离'] = vpd
        else:
            scores['背离'] = 40
        weights['背离'] = 0.25

        # ④ 支撑位 15%
        sup = tech.get('support_strength')
        if sup is not None:
            scores['支撑'] = sup
        else:
            scores['支撑'] = 30
        weights['支撑'] = 0.15

        # ⑤ 下跌缩量 15%
        fvr = tech.get('fall_vol_ratio')
        if fvr is not None:
            if fvr < 0.6:
                vs_s = 95
            elif fvr < 0.75:
                vs_s = 85
            elif fvr < 0.9:
                vs_s = 70
            elif fvr < 1.1:
                vs_s = 55
            elif fvr < 1.3:
                vs_s = 35
            else:
                vs_s = 15
        else:
            vs_s = 50
        scores['缩量'] = vs_s
        weights['缩量'] = 0.15

        total_w = sum(weights.values())
        if total_w <= 0:
            return None
        return _clip(sum(scores[k] * weights[k] for k in scores) / total_w)

    # ── V7: 黄金坑 V3 ──
    def golden_pit_v3(self, mbs: Optional[float], entry: Optional[float],
                      pcs: Optional[float], acs: Optional[float],
                      bqs: Optional[float], dqs: Optional[float],
                      fq: Optional[float], state_code: str,
                      tech: Optional[dict]) -> str:
        """黄金坑识别器 V3
        在 V2 基础上加入 DQS 下跌质量。
        真正的黄金坑 = 好公司 + 跌得健康 + 底部扎实

        GP_A+: 双底确认 + BQS≥75 + DQS≥70 + ACS≥80 + PCS≥70 + MBS≥78
        GP_A:  双底或高BQS + BQS≥70 + DQS≥65 + ACS≥80 + PCS≥70 + MBS≥76
        GP_B:  BQS≥60 + DQS≥55 + ACS≥70 + PCS≥55 + MBS≥75
        GP_C:  BQS≥50 + DQS≥45 + ACS≥60 + MBS≥75
        """
        if mbs is None or acs is None or pcs is None or bqs is None or dqs is None:
            return ''
        if mbs < 75:
            return ''
        if fq is not None and fq < 60:
            return ''
        dd = tech.get('dist_hi') if tech else None
        if acs < 60:
            return ''
        if dd is None or dd > -10:
            return ''
        if dd < -40:
            return ''

        db_type = tech.get('db_type', '') if tech else ''
        has_db = db_type in ('DOUBLE_BOTTOM', 'W_BOTTOM')

        # GP_A+: 顶级黄金坑 (全维度共振)
        if has_db and bqs >= 75 and dqs >= 70 and acs >= 80 and pcs >= 70 and mbs >= 78:
            return 'GOLD_PIT_A_PLUS'
        # GP_A: A级黄金坑
        if (has_db or bqs >= 72) and bqs >= 70 and dqs >= 65 and acs >= 80 and pcs >= 70 and mbs >= 76:
            return 'GOLD_PIT_A'
        # GP_B: B级黄金坑
        if bqs >= 60 and dqs >= 55 and acs >= 70 and pcs >= 55:
            return 'GOLD_PIT_B'
        # GP_C: C级黄金坑 (观察)
        if bqs >= 50 and dqs >= 45 and acs >= 60:
            return 'GOLD_PIT_C'
        return ''

    # ── V7: 信号决策 ──
    def signal_v7(self, mbs: Optional[float], entry: Optional[float],
                  pcs: Optional[float], acs: Optional[float],
                  bqs: Optional[float], dqs: Optional[float],
                  conf: float, fq: Optional[float],
                  state_code: str, golden_pit: str, market: str,
                  d_ma20: Optional[float], d_hi: Optional[float]) -> str:
        """V7 信号: BQS + DQS 双质量驱动
        买跌不买涨的核心: 不仅要底部好(BQS),还要跌的方式健康(DQS)
        DQS 低的=可能是价值陷阱,即使看起来便宜也不能买
        """
        if mbs is None:
            return 'F'
        if mbs < 65:
            return 'F'
        if state_code == 'TREND_BREAKDOWN':
            return 'F' if mbs < 70 else 'D'
        if fq is not None and fq < 50:
            return 'F'
        # V7新增: DQS极低的下跌=危险,即使便宜也降级
        dangerous_drop = dqs is not None and dqs < 35

        is_bear = '弱' in market or '熊' in market
        high_ext = d_ma20 is not None and d_ma20 > 12
        near_high_ext = (d_ma20 is not None and d_ma20 > 7 and
                         d_hi is not None and d_hi > -5)

        # ── 黄金坑优先 (V3) ──
        if golden_pit == 'GOLD_PIT_A_PLUS' and mbs >= 78 and conf >= 75 and not high_ext and not dangerous_drop:
            return 'GP_A+'
        if golden_pit == 'GOLD_PIT_A' and mbs >= 76 and conf >= 70 and not high_ext and not dangerous_drop:
            return 'GP_A'
        if golden_pit == 'GOLD_PIT_B' and mbs >= 75 and not high_ext:
            return 'GP_B'

        # ── 传统分级 ──
        if dangerous_drop and mbs < 80:
            # 下跌质量差的股票,最高只能到D(观察),不能给买入信号
            pass
        else:
            if mbs >= 85 and entry is not None and entry >= 85 and pcs is not None and pcs >= 75 \
               and conf >= 80 and not is_bear and not high_ext:
                return 'S'
            if mbs >= 78 and entry is not None and entry >= 80 and pcs is not None and pcs >= 75 \
               and conf >= 75 and state_code in ('HEALTHY_PULLBACK', 'PULLBACK_APPROACHING', 'MA60_PULLBACK') \
               and not high_ext:
                return 'A'
            if mbs >= 75 and entry is not None and entry >= 70 and pcs is not None and pcs >= 50 \
               and not high_ext and state_code != 'EXTREME_EXTENSION':
                return 'B'
            if mbs >= 80 and entry is not None and 65 <= entry < 80 and (pcs is None or pcs < 70):
                return 'C'
        # C+: 黄金坑观察
        if golden_pit == 'GOLD_PIT_C' and mbs >= 75:
            return 'C+'

        # D 级
        if mbs >= 75:
            if high_ext or near_high_ext:
                return 'D+'
            if entry is not None and entry < 70:
                return 'D'
        if mbs >= 70 and entry is not None and entry >= 60 and state_code != 'TREND_BREAKDOWN':
            return 'D'
        return 'D' if mbs >= 65 else 'F'

    # ── V7: 仓位建议 ──
    def position_v7(self, signal: str, conf: float, market: str,
                    mbs: Optional[float] = None,
                    bqs: Optional[float] = None,
                    dqs: Optional[float] = None) -> Optional[float]:
        """V7 仓位: DQS 调整系数
        DQS≥70: 仓位×1.05 (下跌健康,安全边际高)
        DQS<40: 仓位×0.85 (下跌质量差,谨慎)
        """
        base_map = {
            'S': 10.0,
            'GP_A+': 9.0,
            'GP_A': 8.0,
            'A': 6.5,
            'GP_B': 5.0,
            'B': 4.0,
            'B/W': 3.0,
            'C+': 2.5,
            'C': 2.0,
            'D': 0.0,
            'D+': 0.0,
            'F': 0.0,
        }
        base = base_map.get(signal, 0.0)
        if base == 0:
            return 0.0
        # BQS 加成
        if bqs is not None and signal in ('GP_B', 'B', 'C+'):
            if bqs >= 75:
                base *= 1.05
            elif bqs >= 65:
                base *= 1.03
        # DQS 调整
        if dqs is not None:
            if dqs >= 70:
                base *= 1.05
            elif dqs < 40:
                base *= 0.85
        # Market Multiplier
        if '强牛' in market or '强势' in market:
            mm = 1.00
        elif '牛' in market or '偏强' in market:
            mm = 0.95
        elif '复苏' in market or '恢复' in market:
            mm = 0.85
        elif '震荡' in market or '中性' in market:
            mm = 0.75
        elif '弱' in market:
            mm = 0.60
        elif '熊' in market:
            mm = 0.40
        else:
            mm = 0.75
        # Confidence Factor
        if conf >= 90:
            cf = 1.0
        elif conf >= 80:
            cf = 0.95
        elif conf >= 70:
            cf = 0.85
        elif conf >= 60:
            cf = 0.70
        else:
            cf = 0.50
        return round(base * mm * cf, 1)

    # ── V8: 强势回踩评分 SRS ──
    def strong_retracement_score(self, tech: Optional[dict],
                                 acs: Optional[float]) -> Tuple[Optional[float], str]:
        """SRS = Strong Retracement Score 强势回踩评分
        回测纠偏结论(2026-01~08, 10208样本):
          - d_hi(距前高) IC=+0.029 五分位单调+5.10% → 强者恒强, 越接近前高越好
          - ACS IC=+0.038 有效 → 保留
          - BQS/DQS 反向 → 放弃超跌筑底, 改买强势股回踩

        五维加权:
        - 强势位置 30% (d_hi 距前高: 贴前高=最强)
        - 回踩质量 25% (ACS 调整充分性)
        - 趋势支撑 20% (MA60 向上 + 价格在 MA60 附近)
        - 缩量回踩 15% (10日量缩比)
        - 时间节奏 10% (调整天数: 强势股快速回调 5~40天最佳)
        """
        if tech is None:
            return None, ''
        parts = {}

        # ① 强势位置 30%: d_hi 越大(越接近前高)越好
        d_hi = tech.get('dist_hi')
        if d_hi is not None:
            if d_hi > -5:
                pos_s = 95        # 贴前高,最强
            elif d_hi > -15:
                pos_s = 90        # 浅回踩
            elif d_hi > -25:
                pos_s = 80        # 正常回踩
            elif d_hi > -40:
                pos_s = 60        # 回踩加深
            elif d_hi > -60:
                pos_s = 35        # 深度回撤=弱势股
            else:
                pos_s = 15        # 超跌阴跌
        else:
            pos_s = 50
        parts['位置'] = round(pos_s * 0.30, 1)

        # ② 回踩质量 25%: ACS 直接用
        acs_s = acs if acs is not None else 50
        parts['回踩'] = round(acs_s * 0.25, 1)

        # ③ 趋势支撑 20%: MA60 向上 + 价格在 MA60 附近
        s60 = tech.get('ma60_slope') if tech else None
        ma60 = tech.get('ma60')
        close = tech.get('close')
        d60 = (close / ma60 - 1) * 100 if (close is not None and ma60 and ma60 > 0) else None
        if s60 is not None and s60 > 0 and d60 is not None and -3 <= d60 <= 5:
            trend_s = 90          # 均线向上 + 贴着MA60
        elif s60 is not None and s60 > 0:
            trend_s = 75          # 均线向上
        elif d60 is not None and d60 >= 0:
            trend_s = 60          # 价格在MA60上方
        else:
            trend_s = 35          # 趋势向下
        parts['趋势'] = round(trend_s * 0.20, 1)

        # ④ 缩量回踩 15%
        vs10 = tech.get('vol_shrink_10d')
        if vs10 is not None:
            if vs10 < 0.60:
                vol_s = 95
            elif vs10 < 0.75:
                vol_s = 85
            elif vs10 < 0.90:
                vol_s = 70
            elif vs10 < 1.10:
                vol_s = 50
            else:
                vol_s = 25
        else:
            vol_s = 50
        parts['缩量'] = round(vol_s * 0.15, 1)

        # ⑤ 时间节奏 10%: 强势股回调 5~40 天最佳
        adj_days = tech.get('adj_days')
        if adj_days is None:
            time_s = 50
        elif 5 <= adj_days <= 20:
            time_s = 90           # 快速回调=强势
        elif 20 < adj_days <= 40:
            time_s = 80
        elif 40 < adj_days <= 60:
            time_s = 60
        else:
            time_s = 35           # 调整太久=转弱
        parts['节奏'] = round(time_s * 0.10, 1)

        total = sum(parts.values())
        detail = ' '.join(f'{k}{v:.0f}' for k, v in parts.items())
        return _clip(total), detail

    # ── V8: 黄金坑 V4 ──
    def golden_pit_v4(self, mbs: Optional[float], entry: Optional[float],
                      pcs: Optional[float], acs: Optional[float],
                      srs: Optional[float], fq: Optional[float],
                      state_code: str, tech: Optional[dict]) -> str:
        """黄金坑识别器 V4 (回测纠偏版)
        放弃"超跌筑底"(BQS/DQS), 改买"强势回踩"(SRS + d_hi 浅 + ACS 高)。

        GP_A+: SRS≥80 + MBS≥72 + PCS≥55 + d_hi>-20% + ACS≥70 + 趋势完好
        GP_A:  SRS≥72 + MBS≥68 + d_hi>-25% + ACS≥65 + PCS≥50
        GP_B:  SRS≥62 + MBS≥65 + d_hi>-35% + ACS≥60
        GP_C:  SRS≥52 + MBS≥60
        """
        if mbs is None or acs is None or srs is None:
            return ''
        if srs < 52:
            return ''
        if mbs < 60:
            return ''
        if fq is not None and fq < 50:
            return ''
        dd = tech.get('dist_hi') if tech else None
        ma60 = tech.get('ma60') if tech else None
        close = tech.get('close') if tech else None
        trend_ok = True
        if ma60 is not None and close is not None and close < ma60:
            # 价格跌破MA60 = 趋势受损, 黄金坑降级
            trend_ok = False

        # GP_A+: 顶级强势回踩
        if (srs >= 80 and mbs >= 72 and pcs is not None and pcs >= 55
                and acs >= 70 and dd is not None and dd > -20 and trend_ok):
            return 'GOLD_PIT_A_PLUS'
        # GP_A
        if (srs >= 72 and mbs >= 68 and pcs is not None and pcs >= 50
                and acs >= 65 and dd is not None and dd > -25 and trend_ok):
            return 'GOLD_PIT_A'
        # GP_B
        if srs >= 62 and mbs >= 65 and dd is not None and dd > -35 and acs >= 60:
            return 'GOLD_PIT_B'
        # GP_C
        if srs >= 52 and mbs >= 60:
            return 'GOLD_PIT_C'
        return ''

    # ── V8: 信号决策 ──
    def signal_v8(self, mbs: Optional[float], entry: Optional[float],
                  pcs: Optional[float], acs: Optional[float],
                  srs: Optional[float], bqs: Optional[float], dqs: Optional[float],
                  conf: float, fq: Optional[float],
                  state_code: str, golden_pit: str, market: str,
                  d_ma20: Optional[float], d_hi: Optional[float]) -> str:
        """V8 信号: 强势回踩驱动 (回测纠偏版)
        核心: 强者恒强, 买强势股回踩, 放弃超跌筑底。
        - SRS 高 + d_hi 浅 = 最佳买点
        - BQS/DQS 高分(超跌筑底) = 反向降级
        """
        if mbs is None:
            return 'F'
        if mbs < 65:
            return 'F'
        if state_code == 'TREND_BREAKDOWN':
            return 'F' if mbs < 70 else 'D'
        if fq is not None and fq < 50:
            return 'F'

        is_bear = '弱' in market or '熊' in market
        high_ext = d_ma20 is not None and d_ma20 > 12
        near_high_ext = (d_ma20 is not None and d_ma20 > 7 and
                         d_hi is not None and d_hi > -5)
        # V8: 超跌筑底(弱势股画像)反向惩罚
        weak_bottom = (bqs is not None and bqs >= 75) or (dqs is not None and dqs >= 75)
        strong_stock = srs is not None and srs >= 60

        # ── 黄金坑优先 (V4) ──
        if golden_pit == 'GOLD_PIT_A_PLUS' and mbs >= 72 and conf >= 70 and not high_ext and not weak_bottom:
            return 'GP_A+'
        if golden_pit == 'GOLD_PIT_A' and mbs >= 68 and conf >= 65 and not high_ext and not weak_bottom:
            return 'GP_A'
        if golden_pit == 'GOLD_PIT_B' and mbs >= 65 and not high_ext and not weak_bottom:
            return 'GP_B'

        # ── 传统分级 (强化 SRS / d_hi) ──
        if mbs >= 85 and entry is not None and entry >= 85 and pcs is not None and pcs >= 75 \
           and conf >= 80 and not is_bear and not high_ext and strong_stock:
            return 'S'
        if mbs >= 78 and entry is not None and entry >= 80 and pcs is not None and pcs >= 75 \
           and conf >= 75 and state_code in ('HEALTHY_PULLBACK', 'PULLBACK_APPROACHING', 'MA60_PULLBACK') \
           and not high_ext and strong_stock:
            return 'A'
        if mbs >= 75 and entry is not None and entry >= 70 and pcs is not None and pcs >= 50 \
           and not high_ext and state_code != 'EXTREME_EXTENSION' and not weak_bottom:
            return 'B'
        if mbs >= 80 and entry is not None and 65 <= entry < 80 and (pcs is None or pcs < 70):
            return 'C'
        # C+: 黄金坑观察
        if golden_pit == 'GOLD_PIT_C' and mbs >= 60:
            return 'C+'

        # D 级
        if mbs >= 75:
            if high_ext or near_high_ext:
                return 'D+'
            if entry is not None and entry < 70:
                return 'D'
        if mbs >= 70 and entry is not None and entry >= 60 and state_code != 'TREND_BREAKDOWN':
            return 'D'
        return 'D' if mbs >= 65 else 'F'

    # ── V8: 仓位建议 ──
    def position_v8(self, signal: str, conf: float, market: str,
                    mbs: Optional[float] = None,
                    srs: Optional[float] = None,
                    bqs: Optional[float] = None,
                    dqs: Optional[float] = None) -> Optional[float]:
        """V8 仓位: SRS 主导加成, 超跌筑底反向减仓
        SRS≥75: ×1.08   SRS≥65: ×1.04
        BQS≥70 或 DQS≥70 (超跌筑底): ×0.90
        """
        base_map = {
            'S': 10.0,
            'GP_A+': 9.0,
            'GP_A': 8.0,
            'A': 6.5,
            'GP_B': 5.0,
            'B': 4.0,
            'B/W': 3.0,
            'C+': 2.5,
            'C': 2.0,
            'D': 0.0,
            'D+': 0.0,
            'F': 0.0,
        }
        base = base_map.get(signal, 0.0)
        if base == 0:
            return 0.0
        # SRS 加成 (强势回踩=安全边际)
        if srs is not None and signal in ('GP_A', 'GP_A+', 'GP_B', 'B', 'C+'):
            if srs >= 75:
                base *= 1.08
            elif srs >= 65:
                base *= 1.04
        # BQS/DQS 反向减仓 (超跌筑底=弱势)
        if (bqs is not None and bqs >= 70) or (dqs is not None and dqs >= 70):
            base *= 0.90
        # Market Multiplier
        if '强牛' in market or '强势' in market:
            mm = 1.00
        elif '牛' in market or '偏强' in market:
            mm = 0.95
        elif '复苏' in market or '恢复' in market:
            mm = 0.85
        elif '震荡' in market or '中性' in market:
            mm = 0.75
        elif '弱' in market:
            mm = 0.60
        elif '熊' in market:
            mm = 0.40
        else:
            mm = 0.75
        # Confidence Factor
        if conf >= 90:
            cf = 1.0
        elif conf >= 80:
            cf = 0.95
        elif conf >= 70:
            cf = 0.85
        elif conf >= 60:
            cf = 0.70
        else:
            cf = 0.50
        return round(base * mm * cf, 1)

    def theme_style(self, row) -> Tuple[Optional[float], List[str]]:
        code6 = str(row.get('代码6', ''))
        theme_name = str(row.get('主题', '')).strip()
        hint = str(row.get('增强提示', ''))
        flags = []
        entries = self._theme_map.get(code6, [])
        if not entries:
            # 兜底: 用 CSV 主题名的其他股票平均分
            scores_other = []
            if theme_name:
                for c, lst in self._theme_map.items():
                    for t, s in lst:
                        if t == theme_name:
                            scores_other.append(s)
            if scores_other:
                avg = float(np.mean(scores_other))
                return self._theme_score_from_raw(avg, hint, flags), flags
            return None, ['无主题映射']
        best_theme, best_score = max(entries, key=lambda x: x[1])
        return self._theme_score_from_raw(best_score, hint, flags), flags

    @staticmethod
    def _theme_score_from_raw(raw: float, hint: str, flags: List[str]) -> Optional[float]:
        if raw is None:
            return None
        if raw >= 95:
            s = 100
        elif raw >= 85:
            s = 90
        elif raw >= 75:
            s = 78
        elif raw >= 60:
            s = 65
        elif raw >= 45:
            s = 52
        elif raw >= 30:
            s = 30
        else:
            s = 10
        if '主题存疑' in hint:
            s = _clip(s - 15)
            flags.append('主题存疑-15')
        return s

    # ── Confidence (spec 十九~二十二节) ──
    def confidence(self, row, tech: Optional[dict], growth_flags: List[str]) -> float:
        comp = 0.0
        # 财务完整度 25%
        fin = [c for c in ['ROE%', '毛利率%', '利润YoY%', '市值(亿)'] if _num(row, c) is not None]
        comp += 25.0 * (len(fin) / 4)
        # 盈利完整度 20%
        earn = [c for c in ['Q1利润YoY%', '加速度分', 'AdjustedProfitGrowth', 'ProfitQualityFactor'] if _num(row, c) is not None]
        comp += 20.0 * (len(earn) / 4)
        # 估值可靠性 15%
        val = [c for c in ['PEG', '估值空间%'] if _num(row, c) is not None]
        comp += 15.0 * (len(val) / 2)
        # 技术完整度 15%
        comp += 15.0 if tech is not None else 0.0
        # 行业完整度 15%
        ind = 0.0
        if str(row.get('行业景气阶段', '')).strip():
            ind += 0.5
        if _num(row, 'IndustryCycleScore') is not None:
            ind += 0.5
        comp += 15.0 * ind
        # 时效性 10%
        if tech is not None and tech.get('last_date'):
            try:
                last = datetime.strptime(str(tech['last_date']), '%Y%m%d')
                age = (datetime.now() - last).days
                comp += 10.0 * max(0.3, 1.0 - age / 10.0) if age >= 0 else 10.0
            except ValueError:
                comp += 10.0
        else:
            comp += 5.0
        return _clip(comp)

    # ── Hard Cap (spec 二十三节) ──
    def hard_caps(self, raw, row, tech: Optional[dict], conf: float, fq: Optional[float]) -> Tuple[float, List[str]]:
        caps = []
        upside = _num(row, '估值空间%')
        if upside is not None and upside < -30:
            caps.append(('估值空间<-30%', 70.0))
        if tech is not None and tech['ma20'] is not None and tech['ma60'] is not None:
            if tech['ma20'] < tech['ma60'] and tech.get('ma60_slope') is not None and tech['ma60_slope'] < 0:
                caps.append(('MA20<MA60且MA60下行', 65.0))
        profit_yoy = _num(row, '利润YoY%')
        q1_yoy = _num(row, 'Q1利润YoY%')
        stage = str(row.get('行业景气阶段', ''))
        if (profit_yoy is not None and profit_yoy < 0) and \
                (q1_yoy is None or q1_yoy < 0) and stage in ('下行', '衰退'):
            caps.append(('盈利恶化+景气下行', 55.0))
        if conf < 50:
            caps.append(('Confidence<50', 70.0))
        if fq is not None and fq < 50:
            caps.append(('Fundamental<50', 65.0))
        cap = min([c for _, c in caps], default=100.0)
        return cap, [t for t, _ in caps]

    # ── 最终合成 ──
    @staticmethod
    def finalize(raw, cap, conf, market_adj):
        capped = min(raw, cap)
        adjusted = capped * (0.70 + 0.30 * conf / 100.0)
        final = _clip(adjusted + market_adj)
        return final

    @staticmethod
    def signal_of(final, fq):
        if fq is not None and fq < 50:
            return 'WATCH' if final >= 65 else 'WAIT' if final >= 55 else 'AVOID'
        if final >= 85:
            return 'CORE BUY'
        if final >= 75:
            return 'BUY ON PULLBACK'
        if final >= 65:
            return 'WATCH'
        if final >= 55:
            return 'WAIT'
        return 'AVOID'

    # ── 解释器 (spec 二十七节) ──
    def build_reason(self, r: MBSResult, row, tech, growth_flags, vs_flags) -> str:
        parts = []
        # 核心优势
        pros = []
        if r.fq is not None and r.fq >= 80:
            pros.append(f'公司质量高({r.fq:.0f})')
        g = _num(row, '利润YoY%')
        if g is not None and g >= 40:
            pros.append(f'利润高增{g:.0f}%')
        peg = _num(row, 'PEG')
        if peg is not None and peg < 0.5:
            pros.append(f'PEG低({peg:.2f})')
        up = _num(row, '估值空间%')
        if up is not None and up >= 30:
            pros.append(f'估值空间{up:.0f}%')
        if r.ic is not None and r.ic >= 75:
            pros.append('行业景气向上')
        if r.tp is not None and r.tp >= 85:
            pros.append(f'技术位置佳({r.tech_grade})')
        leader = str(row.get('龙头类型', ''))
        if leader in ('行业龙头', '行业龙二', '龙二'):
            pros.append(f'{leader}')
        for p in pros[:4]:
            parts.append(f'+ {p}')
        # 主要风险
        risks = []
        if r.tp is not None and r.tp < 55:
            risks.append(f'技术破位({r.tech_grade})')
        if vs_flags:
            risks.append(';'.join(vs_flags[:2]))
        if r.tp is not None and r.tp >= 85 and r.vs is not None and r.vs < 60:
            risks.append('技术高位+估值不便宜,回踩再买')
        if '主题存疑' in str(row.get('增强提示', '')):
            risks.append('主题存疑')
        for rk in risks[:2]:
            parts.append(f'- {rk}')
        # 缺失提示
        if growth_flags:
            parts.append(f'~ 数据缺失:{";".join(growth_flags[:2])}(Confidence已降)')
        # 结论
        signal_note = {
            'CORE BUY': '基本面+买点+置信度三重共振,中线核心配置',
            'PULLBACK READY': '高质量+已进入理想回踩区+止跌确认,中线买点就绪',
            'BUY ON PULLBACK': '位置不错,但止跌确认不充分,继续观察',
            'WATCH PULLBACK': '值得跟踪,当前没有明确买点',
            'WAIT FOR DEEPER PULLBACK': '公司好,当前位置不理想,等更深回踩',
            'WATCH': '有研究价值,未达中线买入池',
            'WAIT': '暂不配置',
            'AVOID': '中线不考虑',
        }.get(r.signal, '')
        # QUALITY ≠ BUY 提示
        if r.fq is not None and r.fq >= 85 and r.final is not None and r.final < 70:
            parts.append(f'! 高质量公司(研究{r.fq:.0f})但当前不是理想买点(MBS{r.final:.0f})')
        # MBS V2: 买点状态叙述
        if r.entry is not None:
            parts.append(f'° Entry={r.entry:.0f}[{r.entry_state}]')
        # MBS V3: PCS + 成熟度
        if r.pcs is not None and r.entry_maturity:
            parts.append(f'° PCS={r.pcs:.0f}[成熟度{r.entry_maturity}]')
        if r.signal in ('WAIT FOR DEEPER PULLBACK', 'WATCH', 'WAIT', 'WATCH PULLBACK', 'BUY ON PULLBACK') and r.next_triggers:
            parts.append(f'→ 升级条件: {r.next_triggers}')
        parts.append(f'→ {r.signal}({signal_note})')
        return '; '.join(parts)

    # ── 单股全流程 ──
    def score_one(self, row, tech) -> MBSResult:
        r = MBSResult(
            ts_code=str(row.get('代码', '')), name=str(row.get('名称', '')),
            market=str(row.get('市场', '')), theme=str(row.get('主题', '')),
        )
        r.fq = self.fundamental_quality(row)
        r.gq, growth_flags = self.growth_quality(row)
        r.vs, vs_flags = self.valuation_safety(row)
        r.ic, _ = self.industry_cycle(row)
        r.tp, r.tech_grade, tp_flags = self.technical_position(tech)
        r.ts_score, ts_flags = self.theme_style(row)

        r.conf = self.confidence(row, tech, growth_flags)
        # Raw MBS
        dims = {'FQ': r.fq, 'GQ': r.gq, 'VS': r.vs, 'IC': r.ic, 'TP': r.tp, 'TS': r.ts_score}
        total_w = sum(W[k] for k, v in dims.items() if v is not None)
        if total_w <= 0:
            r.final = 0.0
            r.signal = 'AVOID'
            r.flags = ['全部维度缺失']
            r.reason = '→ AVOID(数据严重不足)'
            return r
        raw = _clip(sum(dims[k] * W[k] for k, v in dims.items() if v is not None) / total_w)
        r.raw = raw

        r.cap, cap_flags = self.hard_caps(raw, row, tech, r.conf, r.fq)
        r.final = self.finalize(raw, r.cap, r.conf, self._market_adj)

        # ── MBS V2: Entry Score 买点状态 ──
        r.entry, old_state, r.pullback_quality, entry_flags = self.entry_score(tech)
        # ── MBS V3: PCS (回踩确认分) ──
        r.pcs = self.pcs_score(tech)
        r.vol_ratio_v = tech.get('vol_ratio') if tech else None
        # ── MBS V4: 十状态机 / 反弹质量 / 成熟度V2 / 信号V4 / Trading Score / Position ──
        state_code = self.pullback_state_v4(tech)
        r.entry_state = self._STATE_CN.get(state_code, state_code)  # 中文展示
        r._state_code = state_code  # 英文内部状态
        r.rebound_quality = self.rebound_quality_v4(tech)
        r.entry_maturity = self.maturity_v4(r.final, r.entry, r.pcs, state_code)
        r.signal = self.signal_v4(r.final, r.entry, r.pcs, r.conf, r.fq,
                                  state_code, r.d_ma20, r.d_hi,
                                  self._market_regime)
        r.trading_score = self.trading_score_v4(r.final, r.entry, r.pcs, r.conf, self._market_adj)
        r.position = self.position_v4(r.signal, r.conf, self._market_regime, r.final)
        r.trigger = self.trigger_v4(r.signal, tech, r.final, r.entry, r.pcs)
        r.invalidation = self.invalidation_v4(tech)
        if tech is not None and tech['ma20'] is not None:
            r.price = tech['close']
            r.ma20_v = tech['ma20']
            r.ma30_v = tech['ma30']
            r.ma60_v = tech['ma60']
            r.d_ma20 = (tech['close'] / tech['ma20'] - 1) * 100
            r.d_ma30 = (tech['close'] / tech['ma30'] - 1) * 100 if tech['ma30'] else None
            r.d_ma60 = (tech['close'] / tech['ma60'] - 1) * 100 if tech['ma60'] else None
            r.d_hi = tech['dist_hi']
        r.one_line = self.one_line_conclusion(
            r.name, r.final, r.entry, r.pcs, state_code, r.signal, r.d_ma20, r.d_hi)
        if r.final is not None and r.entry is not None:
            r.matrix_cell = self.matrix_cell(r.final, r.entry)
        r.next_triggers = r.trigger  # 兼容旧字段
        r.research_score = _num(row, 'FinalScore')

        # ── MBS V5: ACS / 黄金坑 / 买点类型 / Signal V5 ──
        r.quality_score = r.fq
        r.growth_score = r.gq
        r.valuation_score = r.vs
        r.cycle_score = r.ic
        if tech is not None and tech.get('ma20') is not None:
            r.acs = self.acs_score(tech, r.final, r.fq, r.vs)
            r.adj_days_v = tech.get('adj_days')
            r.max_dd_v = tech.get('max_dd')
            r.vol_shrink_v = tech.get('vol_shrink_10d')
            r.golden_pit = self.golden_pit(r.final, r.entry, r.pcs, r.acs,
                                           r.fq, state_code, tech)
            r.buy_point_type = self.buy_point_type(state_code, r.acs, r.golden_pit, tech)
        # ── MBS V7: DQS / 头部形态 / 量价背离 / 黄金坑V3 / Signal V7 ──
        if tech is not None and tech.get('ma20') is not None:
            # V6 技术形态字段 (保留)
            r.double_bottom = tech.get('db_type', '') or ''
            r.db_conf = tech.get('db_confidence')
            r.db_rebound = tech.get('db_right_rebound')
            r.ma_conv = tech.get('ma_conv_score')
            r.bottom_dwell = tech.get('bottom_dwell_days')
            r.bqs = self.bottom_quality_score(tech, r.acs, r.pcs)
            # V7 头部形态 + 下跌模式 + 量价背离 + 支撑位
            r.top_pattern = tech.get('top_pattern', '') or ''
            r.top_quality = tech.get('top_quality')
            r.fall_pattern = tech.get('fall_pattern', '') or ''
            r.vpd_type = tech.get('vpd_type', '') or ''
            r.vpd_strength = tech.get('vpd_strength')
            r.support_hit = tech.get('support_hit', '') or ''
            r.support_strength = tech.get('support_strength')
            # DQS 下跌质量评分
            r.dqs = self.drop_quality_score(tech)
            # ── MBS V8: 强势回踩 (回测纠偏: 强者恒强, 放弃超跌筑底) ──
            # SRS 强势回踩评分
            r.srs, r.srs_parts = self.strong_retracement_score(tech, r.acs)
            # 黄金坑 V4 (SRS驱动, 覆盖 V3)
            gp_v4 = self.golden_pit_v4(r.final, r.entry, r.pcs, r.acs, r.srs,
                                       r.fq, state_code, tech)
            if gp_v4:
                r.golden_pit = gp_v4
        # 升级到 V8 信号和仓位
        r.signal = self.signal_v8(r.final, r.entry, r.pcs, r.acs, r.srs,
                                  r.bqs, r.dqs, r.conf, r.fq, state_code,
                                  r.golden_pit, self._market_regime,
                                  r.d_ma20, r.d_hi)
        r.position = self.position_v8(r.signal, r.conf, self._market_regime,
                                      r.final, r.srs, r.bqs, r.dqs)
        # 重新生成一句话结论(V8版,含SRS+黄金坑V4)
        if r.golden_pit and r.acs is not None and r.srs is not None:
            gplabel = {'GOLD_PIT_A_PLUS': '顶级黄金坑', 'GOLD_PIT_A': 'A级黄金坑',
                       'GOLD_PIT_B': 'B级黄金坑', 'GOLD_PIT_C': 'C级黄金坑观察'
                       }.get(r.golden_pit, '黄金坑')
            srs_note = f'SRS {r.srs:.0f},'
            db_note = ''
            if r.double_bottom == 'DOUBLE_BOTTOM':
                db_note = '双底形态,'
            elif r.double_bottom == 'W_BOTTOM':
                db_note = 'W底形态,'
            r.one_line = (f'{r.name}:MBS {r.final:.0f},{gplabel}候选;'
                          f'{srs_note}{db_note}'
                          f'调整{r.adj_days_v or "?"}天,'
                          f'ACS {r.acs:.0f},PCS {r.pcs or 0:.0f};'
                          f'等待{"进一步确认" if "C" in r.golden_pit else "加仓信号"}')
        elif r.srs is not None and r.srs >= 65 and r.final is not None and r.final >= 70:
            r.one_line = (f'{r.name}:强势回踩,SRS {r.srs:.0f}分,'
                          f'调整{r.adj_days_v or "?"}天;'
                          f'MBS {r.final:.0f},Entry {r.entry or 0:.0f},'
                          f'ACS {r.acs or 0:.0f},PCS {r.pcs or 0:.0f}')
        elif r.acs is not None and r.acs >= 70 and r.final is not None and r.final >= 70:
            r.one_line = (f'{r.name}:调整{r.adj_days_v or "?"}天,'
                          f'ACS {r.acs:.0f}分,{r.buy_point_type}型买点;'
                          f'MBS {r.final:.0f},Entry {r.entry or 0:.0f},PCS {r.pcs or 0:.0f}')

        r.flags = cap_flags + tp_flags + vs_flags[:1] + entry_flags
        r.reason = self.build_reason(r, row, tech, growth_flags, vs_flags)
        return r

    # ── 全池 ──
    def run(self, limit: int = None, tech_fetch: bool = True) -> List[MBSResult]:
        self.load_market_regime()
        df = self.df
        if limit:
            df = df.head(limit)
        results = []
        n = len(df)
        for i, (_, row) in enumerate(df.iterrows(), 1):
            code6 = str(row['代码6'])
            ts_code = str(row['代码'])
            tech = None
            if tech_fetch:
                tech_df = self._fetch_tech(ts_code)
                tech = self.compute_technical(tech_df)
            r = self.score_one(row, tech)
            results.append(r)
            if i % 100 == 0 or i == n:
                print(f'[MBS] 进度 {i}/{n}')
        return results

    # ── 输出 ──
    def to_report(self, results: List[MBSResult]) -> pd.DataFrame:
        rows = []
        for r in results:
            rows.append({
                '代码': r.ts_code, '名称': r.name, '市场': r.market, '主题': r.theme,
                'Fundamental': None if r.fq is None else round(r.fq, 1),
                'Growth': None if r.gq is None else round(r.gq, 1),
                'Valuation': None if r.vs is None else round(r.vs, 1),
                'Cycle': None if r.ic is None else round(r.ic, 1),
                'Technical': None if r.tp is None else round(r.tp, 1),
                'Theme': None if r.ts_score is None else round(r.ts_score, 1),
                'Confidence': None if r.conf is None else round(r.conf, 1),
                'Cap': None if r.cap is None else round(r.cap, 1),
                'MarketAdj': r.market_adj,
                'MBS': None if r.final is None else round(r.final, 1),
                'Entry': None if r.entry is None else round(r.entry, 1),
                'PCS': None if r.pcs is None else round(r.pcs, 1),
                '回踩状态': r.entry_state,
                '回踩质量': r.pullback_quality,
                '成熟度': r.entry_maturity,
                '矩阵': r.matrix_cell,
                '信号': r.signal,
                # 买点距离 (spec 十七节)
                '现价': None if r.price is None else round(r.price, 2),
                'MA20': None if r.ma20_v is None else round(r.ma20_v, 2),
                'MA30': None if r.ma30_v is None else round(r.ma30_v, 2),
                'MA60': None if r.ma60_v is None else round(r.ma60_v, 2),
                '距MA20%': None if r.d_ma20 is None else round(r.d_ma20, 1),
                '距MA30%': None if r.d_ma30 is None else round(r.d_ma30, 1),
                '距MA60%': None if r.d_ma60 is None else round(r.d_ma60, 1),
                '距前高%': None if r.d_hi is None else round(r.d_hi, 1),
                '量比': None if r.vol_ratio_v is None else round(r.vol_ratio_v, 2),
                'ResearchScore': None if r.research_score is None else round(r.research_score, 1),
                'TradingScore': None if r.trading_score is None else round(r.trading_score, 1),
                '技术位置': r.tech_grade,
                '反弹质量': r.rebound_quality,
                '建议仓位%': r.position,
                'Flags': '|'.join(r.flags),
                '触发条件': r.trigger,
                '失效条件': r.invalidation,
                '一句话结论': r.one_line,
                '升级条件': r.next_triggers,
                'BUYABILITY_REASON': r.reason,
                # V5: 调整充分性 / 黄金坑
                'ACS': None if r.acs is None else round(r.acs, 1),
                '黄金坑': r.golden_pit,
                '买点类型': r.buy_point_type,
                '调整天数': r.adj_days_v,
                '最大回撤%': None if r.max_dd_v is None else round(r.max_dd_v, 1),
                '量缩比': None if r.vol_shrink_v is None else round(r.vol_shrink_v, 2),
                '基本面分': None if r.quality_score is None else round(r.quality_score, 1),
                '成长分': None if r.growth_score is None else round(r.growth_score, 1),
                '估值分': None if r.valuation_score is None else round(r.valuation_score, 1),
                '景气分': None if r.cycle_score is None else round(r.cycle_score, 1),
                # V6: 底部质量 / 双底 / 均线纠缠
                'BQS': None if r.bqs is None else round(r.bqs, 1),
                '双底': r.double_bottom,
                '双底确认度': None if r.db_conf is None else round(r.db_conf, 1),
                '右底反弹%': None if r.db_rebound is None else round(r.db_rebound, 1),
                '均线纠缠分': None if r.ma_conv is None else round(r.ma_conv, 1),
                '底部天数': r.bottom_dwell,
                # V7: 下跌质量 / 头部形态 / 量价背离 / 支撑位
                'DQS': None if r.dqs is None else round(r.dqs, 1),
                '头部形态': r.top_pattern,
                '头部质量': None if r.top_quality is None else round(r.top_quality, 1),
                '下跌模式': r.fall_pattern,
                '量价背离': r.vpd_type,
                '背离强度': None if r.vpd_strength is None else round(r.vpd_strength, 1),
                '支撑位': r.support_hit,
                '支撑力度': None if r.support_strength is None else round(r.support_strength, 1),
                # V8: 强势回踩
                'SRS': None if r.srs is None else round(r.srs, 1),
                'SRS明细': r.srs_parts,
            })
        rep = pd.DataFrame(rows)
        # 双排名
        rep['ResearchRank'] = rep['ResearchScore'].rank(ascending=False, method='min').astype('Int64')
        rep['BuyRank'] = rep['MBS'].rank(ascending=False, method='min').astype('Int64')
        rep['TradingRank'] = rep['TradingScore'].rank(ascending=False, method='min').astype('Int64')
        return rep

    def save(self, rep: pd.DataFrame, path: str):
        rep.to_csv(path, index=False, encoding='utf-8-sig')
        print(f'[MBS] 报告已保存: {path} ({len(rep)}只)')


# ─────────────────────────────────────────────
# 异常案例测试 (spec 三十节)
# ─────────────────────────────────────────────
def run_edge_tests(engine: MBSEngine):
    print('\n' + '═' * 70)
    print('异常案例测试 (10类)')
    print('═' * 70)

    def make_row(**kw):
        base = {k: None for k in ['代码', '名称', '主题', '龙头类型', '行业景气阶段',
                                  '增强提示', '市值(亿)', '利润YoY%', 'Q1利润YoY%', 'ROE%',
                                  '毛利率%', 'PEG', '估值空间%', 'MoatScore', 'RiskScore',
                                  'AdjustedProfitGrowth', '加速度分', 'ProfitQualityFactor',
                                  'CFO分', '非经常损益%', 'IndustryCycleScore', 'FinalScore']}
        base.update(kw)
        return base

    # 技术位模拟
    def tech(close, ma20, ma60, dist_hi, slope20=1.0, slope60=1.0, vr=0.8, chg=0.0,
             lows_rising=True, no_new_low_2d=True, last_up=True, close_to_high=0.7):
        ma30 = (ma20 + ma60) / 2
        return {'close': close, 'ma20': ma20, 'ma30': ma30, 'ma60': ma60,
                'ma120': (ma20 + ma60) / 2,
                'hi52': close / (1 + dist_hi / 100), 'dist_hi': dist_hi,
                'ma20_slope': slope20, 'ma30_slope': slope20 * 0.9, 'ma60_slope': slope60,
                'vol_ratio': vr, 'price_chg': chg,
                'lows_rising': lows_rising, 'no_new_low_2d': no_new_low_2d,
                'last_up': last_up, 'close_to_high': close_to_high,
                'chg_3d': chg * 2, 'last5_low': close * 0.98, 'last5_high': close * 1.02,
                'last_date': '20260807', 'n_days': 260}

    good = {
        '主题': '半导体', '龙头类型': '行业龙头', '行业景气阶段': '景气上行', '增强提示': '',
        '市值(亿)': 300, '利润YoY%': None, 'Q1利润YoY%': None, 'ROE%': 20, '毛利率%': 40,
        'PEG': None, '估值空间%': 50, 'MoatScore': 85, 'RiskScore': 25,
        'AdjustedProfitGrowth': 80, '加速度分': 70, 'ProfitQualityFactor': 0.9,
        'CFO分': 75, '非经常损益%': 5, 'IndustryCycleScore': 85, 'FinalScore': 82,
    }

    cases = [
        ('1.高增长+低PEG+技术高位(追高)',
         {**good, '利润YoY%': 150, 'PEG': 0.2, 'Q1利润YoY%': 120, 'AdjustedProfitGrowth': 140, 'ProfitQualityFactor': 0.95},
         tech(100, 50, 45, 1.5)),
        ('2.高增长+数据缺失(Q1=NaN)',
         {**good, '利润YoY%': 750, 'Q1利润YoY%': None, '加速度分': None, 'PEG': 0.2, 'AdjustedProfitGrowth': 600},
         tech(60, 55, 50, 10)),
        ('3.高ROE+高估值',
         {**good, 'ROE%': 30, 'PEG': 2.0, '估值空间%': -35, 'Q1利润YoY%': 30},
         tech(55, 50, 48, 8)),
        ('4.低PEG+周期利润高点',
         {**good, '主题': '航运', 'PEG': 0.2, '行业景气阶段': '主升', 'IndustryCycleScore': 95, '估值空间%': 10, 'Q1利润YoY%': 80},
         tech(58, 55, 52, 6)),
        ('5.好公司+MA20/MA60破位',
         {**good, 'Q1利润YoY%': 40},
         tech(45, 50, 55, -15, slope60=-1.5)),
        ('6.好公司+主线退潮',
         {**good, '增强提示': '主题存疑', 'Q1利润YoY%': 40},
         tech(62, 55, 50, 12)),
        ('7.普通公司+强主题',
         {**good, '龙头类型': '普通', 'MoatScore': 45, 'ROE%': 8, '毛利率%': 15, 'RiskScore': 60},
         tech(65, 55, 50, 10)),
        ('8.高质量+缩量回踩MA20(A级)',
         {**good, 'Q1利润YoY%': 60},
         tech(55, 54, 50, 9, vr=0.7)),
        ('9.高质量+距前高2%',
         {**good, 'Q1利润YoY%': 60},
         tech(98, 50, 45, 2.0, slope20=2.0)),
        ('10.高增长但扣非利润下降',
         {**good, '利润YoY%': 320, 'ProfitQualityFactor': 0.45, 'Q1利润YoY%': 200, '非经常损益%': 60, 'AdjustedProfitGrowth': 260},
         tech(60, 55, 50, 8)),
    ]
    for title, row_dict, t in cases:
        row = pd.Series({k: v for k, v in row_dict.items()})
        row['代码6'] = '000000'
        row['市场'] = '主板'
        r = engine.score_one(row, t)
        print(f'\n[{title}]')
        print(f'  FQ={None if r.fq is None else round(r.fq,1)} GQ={None if r.gq is None else round(r.gq,1)} '
              f'VS={None if r.vs is None else round(r.vs,1)} IC={None if r.ic is None else round(r.ic,1)} '
              f'TP={None if r.tp is None else round(r.tp,1)}({r.tech_grade}) TS={None if r.ts_score is None else round(r.ts_score,1)}')
        print(f'  Conf={None if r.conf is None else round(r.conf,1)} Cap={None if r.cap is None else round(r.cap,1)} '
              f'Raw={None if r.raw is None else round(r.raw,1)} MBS={None if r.final is None else round(r.final,1)}')
        print(f'  Entry={None if r.entry is None else round(r.entry,1)}[{r.entry_state}]/回踩{r.pullback_quality} '
              f'矩阵={r.matrix_cell} → {r.signal}')
        print(f'  Reason: {r.reason}')

    # ── MBS V2 买点状态测试 (spec 二十节 A~E 区分) ──
    print('\n' + '═' * 70)
    print('MBS V2 买点状态测试 (A.好公司好买点 / B.好公司差买点 / C.差公司好买点 / D.高增长低置信 / E.高估优质)')
    print('═' * 70)
    good2 = {**good, '利润YoY%': 120, 'Q1利润YoY%': 90, 'PEG': 0.35, '估值空间%': 40}
    v2_cases = [
        ('A.好公司+健康回踩MA20(缩量)', good2, tech(55, 54, 50, -6, vr=0.6, chg=0.2)),
        ('B.好公司+严重偏离/追高', good2, tech(115, 55, 52, -1.5, vr=1.6, chg=3.0)),
        ('B2.好公司+深度回撤等企稳', good2, tech(46, 55, 52, -22, slope60=0.5, vr=0.9)),
        ('C.普通公司+技术位置极佳', {**good2, 'MoatScore': 45, '龙头类型': '普通', 'ROE%': 6, 'RiskScore': 55},
         tech(52, 51, 48, -8, vr=0.65)),
        ('D.高增长但Q1/加速度缺失(低置信)', {**good2, '利润YoY%': 400, 'Q1利润YoY%': None, '加速度分': None,
               'AdjustedProfitGrowth': None, 'ProfitQualityFactor': None},
         tech(57, 54, 50, -5, vr=0.8)),
        ('E.高ROE高估值优质', {**good2, 'ROE%': 30, 'PEG': 2.2, '估值空间%': -40}, tech(56, 53, 50, -4, vr=0.8)),
    ]
    for title, row_dict, t in v2_cases:
        row = pd.Series({k: v for k, v in row_dict.items()})
        row['代码6'] = '000000'
        row['市场'] = '主板'
        r = engine.score_one(row, t)
        print(f'\n[{title}]')
        print(f'  MBS={None if r.final is None else round(r.final,1)} '
              f'Entry={None if r.entry is None else round(r.entry,1)} '
              f'PCS={None if r.pcs is None else round(r.pcs,1)} '
              f'[{r.entry_state}] 成熟度={r.entry_maturity} '
              f'Conf={None if r.conf is None else round(r.conf,1)}')
        print(f'  距MA20={None if r.d_ma20 is None else round(r.d_ma20,1)}% '
              f'距前高={None if r.d_hi is None else round(r.d_hi,1)}% → {r.signal}')
        print(f'  升级条件: {r.next_triggers}')
        print(f'  Reason: {r.reason}')

    # ── MBS V3: 回踩状态分类诊断 ──
    print('\n' + '═' * 70)
    print('MBS V3 回踩状态诊断 (艾力斯/中信证券/巨人网络/睿创微纳/松发股份案例)')
    print('═' * 70)
    v3_diag = [
        ('①艾力斯: 距MA20=-1.3% 距前高=-15.4% → 应为健康回踩',
         tech(116.99, 118.57, 103.62, -15.4, slope20=1.2, slope60=1.0, vr=0.9, chg=0.3)),
        ('②中信证券: 距MA20=-0.9% 距前高=-14.1% → 应为健康回踩',
         tech(27.95, 28.20, 27.39, -14.1, slope20=0.5, slope60=0.3, vr=0.85, chg=0.1)),
        ('③巨人网络: 距MA20=+4.3% 距前高=-45% → 应为回踩接近区/略高于MA20',
         tech(29.34, 28.13, 27.02, -45.0, slope20=2.0, slope60=1.5, vr=1.1, chg=0.5)),
        ('④睿创微纳: 距MA20=+17.5% 距前高=-0.4% → 高位严重偏离',
         tech(170.04, 144.73, 140.11, -0.4, slope20=3.0, slope60=2.0, vr=1.3, chg=2.0,
              lows_rising=False, no_new_low_2d=False, last_up=True, close_to_high=0.9)),
        ('⑤松发股份: 距MA20=+16.7% 距前高=-2.3% → 高位严重偏离',
         tech(183.11, 156.88, 150.16, -2.3, slope20=2.5, slope60=1.8, vr=1.4, chg=1.5,
              lows_rising=False, no_new_low_2d=False, last_up=True, close_to_high=0.85)),
        ('⑥紫金矿业: 距MA20=+11.8% 偏高',
         tech(35.15, 31.44, 29.88, -21.8, slope20=1.8, slope60=1.2, vr=1.2, chg=0.8)),
    ]
    for title, t in v3_diag:
        state = engine.pullback_state_v3(t)
        pcs = engine.pcs_score(t)
        d20 = (t['close'] / t['ma20'] - 1) * 100
        print(f'\n{title}')
        print(f'  距MA20={d20:.1f}% 回踩状态={state} PCS={None if pcs is None else round(pcs,1)}')


# ─────────────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--no-tech', action='store_true', help='跳过技术面拉取(离线测试)')
    ap.add_argument('--test', action='store_true', help='只跑异常案例测试')
    ap.add_argument('--date', type=str, default=None, help='技术面截止交易日 YYYYMMDD (默认最新交易日)')
    args = ap.parse_args()

    engine = MBSEngine()
    if args.date:
        engine._last_trade_date = args.date
    if args.test:
        run_edge_tests(engine)
        return
    results = engine.run(limit=args.limit, tech_fetch=not args.no_tech)
    rep = engine.to_report(results)
    engine.save(rep, OUT_CSV)

    # 控制台 Top30 (V2: 含 Entry)
    pd.set_option('display.width', 280)
    pd.set_option('display.max_columns', 50)
    top = rep.sort_values('MBS', ascending=False).head(30)
    cols = ['代码', '名称', '市场', '主题', 'MBS', 'Entry', 'PCS', 'ACS', 'SRS', 'BQS', 'DQS', '买点类型',
            '黄金坑', '双底', '下跌模式', '信号', '建议仓位%', '距MA20%', '距前高%']
    print('\n' + '═' * 70)
    print(f'市场状态: {engine._market_regime} (修正{engine._market_adj:+d})')
    print('═' * 70)
    print(top[cols].to_string(index=False))
    # 信号分布 (V8 含黄金坑V4 + SRS)
    print('\n=== 信号分布 (V8 强势回踩 GP_A+/GP_A/GP_B) ===')
    print(rep['信号'].value_counts().to_string())
    # 黄金坑分布
    gp = rep[rep['黄金坑'].str.len() > 0]
    if len(gp) > 0:
        print(f'\n=== 黄金坑候选(V4): {len(gp)}只 ===')
        print(gp[['代码', '名称', 'MBS', 'ACS', 'PCS', 'SRS', '黄金坑', '信号', '距前高%']].sort_values('MBS', ascending=False).to_string(index=False))
    # SRS 分布
    print('\n=== SRS 强势回踩分布 ===')
    if 'SRS' in rep.columns:
        bins = [0, 40, 50, 60, 70, 80, 90, 100]
        labels = ['<40超跌', '40-50偏弱', '50-60一般', '60-70尚可', '70-80强势', '80-90很强', '90+贴前高']
        srs_valid = rep[rep['SRS'].notna()].copy()
        srs_valid['SRS段'] = pd.cut(srs_valid['SRS'], bins=bins, labels=labels)
        print(srs_valid['SRS段'].value_counts().sort_index().to_string())
    # DQS 分布 (风险监控, V8 反向惩罚)
    print('\n=== DQS 分布 (V8反向惩罚参考) ===')
    if 'DQS' in rep.columns:
        bins = [0, 30, 45, 60, 70, 80, 90, 100]
        labels = ['<30危险', '30-45差', '45-60一般', '60-70良好', '70-80优秀', '80-90极佳', '90+']
        dqs_valid = rep[rep['DQS'].notna()].copy()
        dqs_valid['DQS段'] = pd.cut(dqs_valid['DQS'], bins=bins, labels=labels)
        print(dqs_valid['DQS段'].value_counts().sort_index().to_string())
    # 下跌模式分布
    print('\n=== 下跌模式分布 ===')
    print(rep['下跌模式'].value_counts().to_string())
    # 量价背离分布
    print('\n=== 量价背离分布 ===')
    print(rep['量价背离'].value_counts().to_string())
    # SRS 高的股票 (强势回踩候选)
    srs_high = rep[(rep['SRS'].notna()) & (rep['SRS'] >= 70) & (rep['MBS'] >= 70)]
    if len(srs_high) > 0:
        print(f'\n=== SRS≥70 & MBS≥70 强势回踩候选: {len(srs_high)}只 ===')
        print(srs_high[['代码', '名称', 'MBS', 'ACS', 'PCS', 'SRS', '黄金坑', '距前高%', '信号']].sort_values('SRS', ascending=False).head(15).to_string(index=False))
    # 双底分布
    print('\n=== 双底形态分布 ===')
    print(rep['双底'].value_counts().to_string())
    # 买点类型分布
    print('\n=== 买点类型分布 ===')
    print(rep['买点类型'].value_counts().to_string())
    # 回踩状态分布 (V4 十状态)
    print('\n=== 回踩状态分布 ===')
    print(rep['回踩状态'].value_counts().to_string())
    # 成熟度分布
    print('\n=== 买点成熟度分布 ===')
    print(rep['成熟度'].value_counts().to_string())
    # 仓位分布
    pos_positive = rep[rep['建议仓位%'] > 0]
    print(f'\n=== 建议仓位>0 : {len(pos_positive)}只 ===')
    if len(pos_positive) > 0:
        print(pos_positive[['代码', '名称', 'MBS', 'Entry', 'PCS', '信号', '建议仓位%']].sort_values('建议仓位%', ascending=False).head(10).to_string(index=False))
    # 矩阵分布
    print('\n=== Quality×Timing 矩阵分布 ===')
    print(rep['矩阵'].value_counts().to_string())
    # 三排名对比(Research / Buyability / Trading)
    print('\n=== 三排名对比 Top15 (研究价值 vs 可买性 vs 交易价值) ===')
    show = rep.sort_values('ResearchScore', ascending=False).head(15)
    print(show[['代码', '名称', 'ResearchScore', 'ResearchRank',
                'MBS', 'BuyRank',
                'TradingScore', 'TradingRank',
                'Entry', 'PCS', '信号']].to_string(index=False))
    # V4: 一句话结论 Top10
    print('\n=== 一句话结论 (按 TradingScore 排序 Top10) ===')
    top_ts = rep.sort_values('TradingScore', ascending=False).head(10)
    for _, row in top_ts.iterrows():
        print(f'  [{row["信号"]}] {row["一句话结论"]}')


if __name__ == '__main__':
    main()
