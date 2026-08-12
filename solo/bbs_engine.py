# -*- coding: utf-8 -*-
"""
BBS 100（Bottom Breakout Score）
底部确认 + 右侧突破买点评分模块

设计原则（用户规格）：
- 不猜底，只做"底部确认 → 平台整理 → 有效突破 → 放量确认 → 缩量回踩 → 再次转强"
- 宁晚一点买，不要猜底；宁可等突破确认，不要因超跌直接买入
- 独立模块，不修改 MBS/SRS/主题评分/主线评分/大盘仓位 V9.9
- 数据缺失时降低置信度，绝不用默认值强行制造买点

输出：Score / Level / Stage / BuySignal / FailureReason + 8 模块明细
"""
import os
import sys
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 数据层依赖（复用现有缓存，不新增数据链路）──
from stock_cache import cached_daily  # noqa: E402

try:
    from multi_factor_picker.data_fetcher import DataFetcher  # noqa: E402
except Exception:
    DataFetcher = None

# ── 常量 ──
BOTTOM_WINDOW = 90        # 底部识别窗口（交易日）
PLATFORM_MAX_DAYS = 35    # 平台最长扫描天数（含突破容忍）
PLATFORM_MIN_DAYS = 10    # 有效平台最短天数
PULLBACK_WINDOW = 10      # 回踩观察窗口（突破后）
MIN_BARS = 60             # 最少 K 线数，少于则置信度极低

STAGE_CN = {
    1: 'Bottoming',   # 底部形成
    2: 'Platform',    # 平台整理
    3: 'Breakout',    # 突破确认
    4: 'Pullback',    # 突破后回踩
    5: 'Rebreakout',  # 回踩后再次转强
    6: 'Failed',      # 突破失败
    7: 'Extended',    # 突破过度/短线过热
}

LEVEL_CN = {'S': 'S级', 'A': 'A级', 'B': 'B级', 'C': 'C级', 'D': 'D级'}

BUY_CN = {
    'NO_BUY': '不买',
    'WATCH': '观察',
    'BREAKOUT_BUY': '首仓',
    'PULLBACK_BUY': '★最佳买点',
    'ADD_POSITION': '加仓',
}


# ═════════════════════════════════════════════
# 结果数据结构
# ═════════════════════════════════════════════
@dataclass
class BBSResult:
    ts_code: str = ''
    name: str = ''
    bbs: Optional[float] = None            # 总分 0~100
    level: str = ''                        # S/A/B/C/D
    stage: int = 0                         # 1~7
    stage_cn: str = ''                     # Bottoming/Platform/...
    buy_signal: str = 'NO_BUY'
    failure_reason: str = ''
    confidence: float = 0.5                # 0~1，数据缺失时降低
    # ── 8 模块明细 ──
    bottom_score: Optional[float] = None   # 20 分
    platform_score: Optional[float] = None # 15 分
    breakout_score: Optional[float] = None # 15 分
    volume_score: Optional[float] = None   # 15 分
    ma_score: Optional[float] = None       # 15 分
    pullback_score: Optional[float] = None # 10 分
    rsi_macd_score: Optional[float] = None # 5 分
    market_score: Optional[float] = None   # 5 分
    # ── 关键结构信息（供人工核验）──
    bottom_date: str = ''
    bottom_price: Optional[float] = None
    platform_high: Optional[float] = None
    platform_low: Optional[float] = None
    platform_width: Optional[float] = None
    platform_days: int = 0
    breakout_date: str = ''
    breakout_price: Optional[float] = None
    breakout_pct: Optional[float] = None
    vol_ratio: Optional[float] = None
    pullback_low: Optional[float] = None
    pullback_pct: Optional[float] = None
    rsi: Optional[float] = None
    macd_gold: bool = False
    ma20_slope: Optional[float] = None
    # ── 其他 ──
    hard_rules_hit: List[str] = field(default_factory=list)
    core_reason: str = ''                  # 核心原因一句话
    n_bars: int = 0


# ═════════════════════════════════════════════
# 数据层
# ═════════════════════════════════════════════
def to_ts_code(code6) -> str:
    """6位代码 → tushare 带后缀代码（60/68→.SH，0/2/3→.SZ，4/8→.BJ）"""
    code6 = str(code6).strip().zfill(6)
    if code6.startswith(('60', '68', '51', '50')):
        return f'{code6}.SH'
    if code6.startswith(('4', '8')):
        return f'{code6}.BJ'
    return f'{code6}.SZ'


def get_last_trade_date() -> str:
    """最近交易日 YYYYMMDD（优先 DataFetcher，失败用今天）"""
    if DataFetcher is not None:
        try:
            return DataFetcher().get_last_trade_date()
        except Exception:
            pass
    return datetime.now().strftime('%Y%m%d')


def load_daily(ts_code: str, end_date: str = None, lookback_days: int = 420) -> Optional[pd.DataFrame]:
    """加载日线 OHLCV（cached_daily 缓存优先）。返回升序 DataFrame 或 None。"""
    end = end_date or get_last_trade_date()
    start = (datetime.strptime(end, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
    try:
        df = cached_daily(ts_code, start, end)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df = df.sort_values('trade_date').reset_index(drop=True)
    for col in ('open', 'high', 'low', 'close', 'vol', 'amount', 'pre_close', 'pct_chg'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算 MA5/10/20/30/60、RSI14、MACD、ATR14，返回增强后的 df"""
    df = df.copy()
    close = df['close']
    for n in (5, 10, 20, 30, 60):
        df[f'ma{n}'] = close.rolling(n).mean()
    # RSI14（Wilder 平滑）
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['rsi'] = 100 - 100 / (1 + rs)
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['dif'] = ema12 - ema26
    df['dea'] = df['dif'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['dif'] - df['dea']
    # ATR14（简化 Wilder）
    pc = close.shift(1)
    tr = pd.concat([df['high'] - df['low'],
                    (df['high'] - pc).abs(),
                    (df['low'] - pc).abs()], axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1 / 14, min_periods=14).mean()
    return df


# ═════════════════════════════════════════════
# 底部确认（20 分）
# ═════════════════════════════════════════════
def score_bottom(df: pd.DataFrame, r: BBSResult) -> float:
    """底部确认 20 分。识别阶段低点，检查不再创新低/放量恐慌/缩量/均线拐头/RSI 修复"""
    score = 0.0
    n = len(df)
    w = min(BOTTOM_WINDOW, n)
    seg = df.iloc[-w:]
    bottom_idx_rel = int(np.argmin(seg['low'].values))
    bottom_idx = n - w + bottom_idx_rel
    bottom_price = float(df['low'].iloc[bottom_idx])
    r.bottom_price = bottom_price
    r.bottom_date = str(df['trade_date'].iloc[bottom_idx])
    low_series = df['low']
    high_series = df['high']
    close_series = df['close']
    vol_series = df['vol']

    # 1) 阶段低点后不再创新低（5 分）
    after_low = low_series.iloc[bottom_idx + 1:]
    has_new_low = after_low.min() < bottom_price * 0.995 if len(after_low) > 0 else True
    # 若最低点就在最近 3 日内（还在下跌途中）也算创新低
    if bottom_idx >= n - 3:
        has_new_low = True
    if not has_new_low:
        score += 5.0
    else:
        r.hard_rules_hit.append('仍在创新低/底部未确认')

    # 2) 底部放量/恐慌释放（4 分）
    pre_vol = float(vol_series.iloc[max(0, bottom_idx - 10):bottom_idx].mean()) or 1.0
    bottom_vol = float(vol_series.iloc[bottom_idx])
    vol_burst = bottom_vol / pre_vol if pre_vol > 0 else 0
    panic = False
    try:
        pc = float(df['pct_chg'].iloc[bottom_idx]) if 'pct_chg' in df.columns else 0
        panic = pc < -4.0
    except Exception:
        pass
    if vol_burst >= 1.5:
        score += 2.0
    if panic:
        score += 2.0
    elif vol_burst >= 1.2:
        score += 1.0

    # 3) 恐慌后成交量逐渐缩小（4 分）
    if len(after_low) >= 6:
        post_vol = float(vol_series.iloc[bottom_idx + 1:bottom_idx + 8].mean())
        shrink = post_vol / bottom_vol if bottom_vol > 0 else 1.0
        if shrink <= 0.7:
            score += 4.0
        elif shrink <= 0.9:
            score += 2.0

    # 4) MA5/MA10 拐头向上（3 分）
    ma5_now = float(df['ma5'].iloc[-1]) if not math.isnan(df['ma5'].iloc[-1]) else 0
    ma5_prev = float(df['ma5'].iloc[-5]) if n >= 5 and not math.isnan(df['ma5'].iloc[-5]) else 0
    ma10_now = float(df['ma10'].iloc[-1]) if not math.isnan(df['ma10'].iloc[-1]) else 0
    ma10_prev = float(df['ma10'].iloc[-5]) if n >= 5 and not math.isnan(df['ma10'].iloc[-5]) else 0
    ma5_up = ma5_now > ma5_prev > 0
    ma10_up = ma10_now > ma10_prev > 0
    if ma5_up and ma10_up:
        score += 3.0
    elif ma5_up:
        score += 1.5

    # 5) MA20 走平或向上（2 分）
    ma20_now = float(df['ma20'].iloc[-1]) if not math.isnan(df['ma20'].iloc[-1]) else 0
    ma20_prev = float(df['ma20'].iloc[-6]) if n >= 6 and not math.isnan(df['ma20'].iloc[-6]) else 0
    if ma20_now >= ma20_prev * 0.995 and ma20_prev > 0:
        score += 2.0

    # 6) RSI 从超卖区修复（2 分）
    rsi_now = float(df['rsi'].iloc[-1]) if not math.isnan(df['rsi'].iloc[-1]) else 50
    rsi_20 = df['rsi'].iloc[-20:] if n >= 20 else df['rsi']
    oversold_seen = (rsi_20 < 32).any()
    if oversold_seen and rsi_now > 35:
        score += 2.0
    elif rsi_now >= 55:
        score += 2.0

    r.bottom_score = round(min(score, 20.0), 1)
    return r.bottom_score


# ═════════════════════════════════════════════
# 平台整理质量（15 分）
# ═════════════════════════════════════════════
def _find_platform(df: pd.DataFrame, bottom_idx: int, r: BBSResult):
    """在底部后扫描平台与突破日。
    返回 (platform_start, platform_end, platform_high, platform_low, breakout_day)"""
    n = len(df)
    ps = bottom_idx + 1
    if ps >= n - 3:
        return ps, ps, None, None, None
    scan_end = min(n, ps + PLATFORM_MAX_DAYS)
    breakout_day = None
    platform_high = None
    for i in range(ps + 1, scan_end):
        ph = float(df['high'].iloc[ps:i].max())
        atr_i = float(df['atr'].iloc[i])
        close_i = float(df['close'].iloc[i])
        ok = close_i > ph * 1.01
        if atr_i > 0 and not math.isnan(atr_i):
            ok = ok or close_i > ph + 0.5 * atr_i
        if ok and (i - ps) >= PLATFORM_MIN_DAYS:
            breakout_day = i
            platform_high = ph
            break
    if breakout_day is not None:
        return ps, breakout_day, platform_high, float(df['low'].iloc[ps:breakout_day].min()), breakout_day
    pe = min(n, ps + 30)
    return ps, pe, float(df['high'].iloc[ps:pe].max()), float(df['low'].iloc[ps:pe].min()), None


def score_platform(df: pd.DataFrame, r: BBSResult, platform: tuple) -> float:
    """平台整理质量 15 分"""
    ps, pe, ph, pl, _ = platform
    if ph is None or pl is None or ph <= 0:
        r.platform_score = 0.0
        return 0.0
    days = pe - ps
    width = (ph - pl) / pl * 100.0
    r.platform_high, r.platform_low, r.platform_width, r.platform_days = ph, pl, width, days
    score = 0.0
    valid = True
    # 持续创新低检查（后 1/3 低点低于前 2/3 → 无效平台）
    if days >= 9:
        seg = df.iloc[ps:pe]
        first_low = float(seg['low'].iloc[: int(days * 2 / 3)].min())
        last_low = float(seg['low'].iloc[int(days * 2 / 3):].min())
        if last_low < first_low * 0.98:
            valid = False

    if not valid:
        r.platform_score = 0.0
        r.hard_rules_hit.append('平台持续创新低')
        return 0.0

    # 1) 平台存在时间 >=10 日（4 分）
    if days >= PLATFORM_MIN_DAYS:
        score += 4.0
    elif days >= 7:
        score += 2.0
    elif days >= 5:
        score += 1.0

    # 2) 平台振幅 5%~20%（4 分）
    if 5.0 <= width <= 20.0:
        score += 4.0
    elif (3.0 <= width < 5.0) or (20.0 < width <= 25.0):
        score += 2.0
    else:
        score += 1.0  # 过宽(>25%)或过窄(<3%)均降低质量

    # 3) 平台低点逐渐抬高（3 分）
    if days >= 8:
        seg = df.iloc[ps:pe]
        first_half = float(seg['low'].iloc[: days // 2].min())
        second_half = float(seg['low'].iloc[days // 2:].min())
        if second_half > first_half * 1.01:
            score += 3.0

    # 4) 平台期间成交量逐渐萎缩（2 分）
    if days >= 10:
        seg_vol = df['vol'].iloc[ps:pe]
        first_vol = float(seg_vol.iloc[: days // 2].mean())
        last_vol = float(seg_vol.iloc[days // 2:].mean())
        if last_vol <= first_vol * 0.85:
            score += 2.0

    # 5) 压力位被多次测试（2 分）
    if days >= 10:
        seg = df.iloc[ps:pe]
        touch = int((seg['high'] >= ph * 0.99).sum())
        if touch >= 3:
            score += 2.0
        elif touch == 2:
            score += 1.0

    r.platform_score = round(min(score, 15.0), 1)
    return r.platform_score


# ═════════════════════════════════════════════
# 突破有效性（15 分）
# ═════════════════════════════════════════════
def score_breakout(df: pd.DataFrame, r: BBSResult, platform: tuple) -> float:
    """突破有效性 15 分。必须针对平台高，非随便站上均线"""
    ps, pe, ph, pl, breakout_day = platform
    if breakout_day is None or ph is None or ph <= 0:
        r.breakout_score = 0.0
        return 0.0
    close_b = float(df['close'].iloc[breakout_day])
    high_b = float(df['high'].iloc[breakout_day])
    low_b = float(df['low'].iloc[breakout_day])
    open_b = float(df['open'].iloc[breakout_day])
    pct = (close_b / ph - 1.0) * 100.0
    r.breakout_date = str(df['trade_date'].iloc[breakout_day])
    r.breakout_price = close_b
    r.breakout_pct = pct

    # 突破幅度主体分
    if pct < 1.0:
        amp_score = 0.0
    elif pct < 2.0:
        amp_score = 5.0
    elif pct < 3.0:
        amp_score = 4.0
    elif pct <= 8.0:
        amp_score = 3.0
    else:
        amp_score = 1.0  # 单日暴涨过大 → 降分防追高

    # 质量检查（共 10 分）
    q_score = 0.0
    # 1) 收盘位于当日高位（3 分）
    mid = (high_b + low_b) / 2
    if close_b >= mid:
        q_score += 3.0
    # 2) 无明显长上影（2 分）
    upper_shadow = high_b - max(open_b, close_b)
    if upper_shadow <= 0.3 * (high_b - low_b) or (high_b - low_b) <= 0:
        q_score += 2.0
    # 3) 收盘确认非盘中突破（3 分）
    if close_b > ph * 1.01:
        q_score += 3.0
    # 4) 突破后没有立即跌回平台（2 分）——按当前已确认数据判断
    if breakout_day + 2 < len(df):
        post_low = float(df['low'].iloc[breakout_day + 1:breakout_day + 3].min())
        if post_low >= ph * 0.98:
            q_score += 2.0

    total = amp_score + q_score
    # 过热检查：突破日单日暴涨过大 → 硬禁止（留给信号层）
    try:
        pc = float(df['pct_chg'].iloc[breakout_day]) if 'pct_chg' in df.columns else pct
        if pc > 9.0 or pct > 8.0:
            r.hard_rules_hit.append('突破日暴涨过热')
    except Exception:
        pass

    r.breakout_score = round(min(total, 15.0), 1)
    return r.breakout_score


# ═════════════════════════════════════════════
# 成交量质量（15 分）
# ═════════════════════════════════════════════
def score_volume(df: pd.DataFrame, r: BBSResult, platform: tuple) -> float:
    """成交量质量 15 分。理想突破量 1.3~2.5 倍"""
    ps, pe, ph, pl, breakout_day = platform
    if breakout_day is None:
        r.volume_score = 0.0
        return 0.0
    base = df['vol'].iloc[ps:pe] if pe - ps >= 5 else df['vol'].iloc[max(0, breakout_day - 20):breakout_day]
    base_mean = float(base.mean()) if len(base) else 0
    if base_mean <= 0:
        r.volume_score = 0.0
        return 0.0
    vr = float(df['vol'].iloc[breakout_day]) / base_mean
    r.vol_ratio = vr
    if vr < 1.0:
        s = 0.0
    elif vr < 1.3:
        s = 5.0
    elif vr < 1.8:
        s = 10.0
    elif vr <= 2.5:
        s = 15.0
    elif vr <= 3.0:
        s = 10.0
    else:
        s = 5.0  # 巨量突破 → 情绪高潮/短线兑现风险
    r.volume_score = s
    return s


# ═════════════════════════════════════════════
# 均线趋势（15 分）
# ═════════════════════════════════════════════
def score_ma(df: pd.DataFrame, r: BBSResult) -> float:
    """均线趋势 15 分。MA5/10/20/30/60 多头排列 + 斜率"""
    n = len(df)
    if n < 60:
        r.ma_score = 0.0
        return 0.0
    close = float(df['close'].iloc[-1])
    def _v(col, idx=-1):
        v = df[col].iloc[idx]
        return float(v) if not math.isnan(v) else None
    ma5, ma10, ma20 = _v('ma5'), _v('ma10'), _v('ma20')
    ma30, ma60 = _v('ma30'), _v('ma60')
    score = 0.0
    if ma5 and ma10 and ma5 > ma10:
        score += 3.0
    if ma10 and ma20 and ma10 >= ma20:
        score += 3.0
    ma20_prev = _v('ma20', -6)
    if ma20 and ma20_prev and ma20 >= ma20_prev:
        score += 3.0
    if ma20 and close > ma20:
        score += 2.0
    ma30_prev = _v('ma30', -6)
    if ma30 and ma30_prev and ma30 >= ma30_prev:
        score += 2.0
    if ma60 and close > ma60:
        score += 2.0
    # MA20 斜率（5 日前）
    slope = None
    if ma20 and ma20_prev and ma20_prev > 0:
        slope = (ma20 / ma20_prev - 1.0) * 100.0
    r.ma20_slope = slope
    # MA20 明显向下 → 降低评分
    if slope is not None and slope < -1.0:
        score -= 3.0
    r.ma_score = round(max(score, 0.0), 1)
    return r.ma_score


# ═════════════════════════════════════════════
# 突破后回踩确认（10 分）
# ═════════════════════════════════════════════
def score_pullback(df: pd.DataFrame, r: BBSResult, platform: tuple) -> float:
    """回踩确认 10 分。放量突破→缩量回踩→不破位→再次转强"""
    ps, pe, ph, pl, breakout_day = platform
    if breakout_day is None or ph is None:
        r.pullback_score = 0.0
        return 0.0
    n = len(df)
    w_end = min(n, breakout_day + 1 + PULLBACK_WINDOW)
    if w_end <= breakout_day + 1:
        r.pullback_score = 0.0
        return 0.0
    seg = df.iloc[breakout_day + 1:w_end]
    pb_low = float(seg['low'].min())
    close_b = float(df['close'].iloc[breakout_day])
    vol_b = float(df['vol'].iloc[breakout_day])
    r.pullback_low = pb_low
    r.pullback_pct = (close_b - pb_low) / close_b * 100.0 if close_b > 0 else 0

    # 破位硬判定
    if pb_low < pl:
        r.hard_rules_hit.append('回踩跌破平台低点')
        r.pullback_score = 0.0
        return 0.0
    if pb_low < ph * 0.95:
        r.pullback_score = 0.0
        r.hard_rules_hit.append('回踩跌破突破位>5%')
        return 0.0

    score = 0.0
    # 1) 回踩幅度 <=5%（2 分）
    if r.pullback_pct <= 5.0:
        score += 2.0
    # 2) 回踩缩量（2 分）
    pb_vol = float(seg['vol'].mean())
    if pb_vol <= vol_b * 0.8:
        score += 2.0
    # 3) 回踩中无放量破位（2 分）
    if not ((seg['vol'] > vol_b * 1.3) & (seg['close'] < ph)).any():
        score += 2.0
    # 4) 平台压力转支撑（2 分）
    if pb_low >= ph * 0.98:
        score += 2.0
    # 5) 回踩后重新站上 MA5/MA10（2 分）
    close_now = float(df['close'].iloc[-1])
    ma5_now = float(df['ma5'].iloc[-1]) if not math.isnan(df['ma5'].iloc[-1]) else 0
    ma10_now = float(df['ma10'].iloc[-1]) if not math.isnan(df['ma10'].iloc[-1]) else 0
    if ma5_now and ma10_now and close_now > ma5_now and close_now > ma10_now:
        score += 2.0
    r.pullback_score = round(min(score, 10.0), 1)
    return r.pullback_score


# ═════════════════════════════════════════════
# RSI/MACD 辅助确认（5 分）
# ═════════════════════════════════════════════
def score_rsi_macd(df: pd.DataFrame, r: BBSResult) -> float:
    """RSI/MACD 5 分。仅作辅助，不能绕过底部/平台/突破条件"""
    n = len(df)
    score = 0.0
    rsi = float(df['rsi'].iloc[-1]) if not math.isnan(df['rsi'].iloc[-1]) else None
    r.rsi = rsi
    if rsi is not None:
        if 50 <= rsi <= 70:
            score += 2.0
        elif rsi > 70:
            score += 1.0  # 过热，给 1 分但不鼓励追高
    # MACD 金叉（近 10 日内 DIF 上穿 DEA）
    dif = df['dif'].values
    dea = df['dea'].values
    gold = False
    for i in range(max(1, n - 10), n):
        if not math.isnan(dif[i]) and not math.isnan(dea[i]) \
           and dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            gold = True
            break
    r.macd_gold = gold
    if gold:
        score += 2.0
    # 红柱扩大
    hist = df['macd_hist']
    if n >= 6:
        h_now = float(hist.iloc[-1]) if not math.isnan(hist.iloc[-1]) else 0
        h_prev = float(hist.iloc[-6]) if not math.isnan(hist.iloc[-6]) else 0
        if h_now > h_prev > 0:
            score += 1.0
    r.rsi_macd_score = round(min(score, 5.0), 1)
    return r.rsi_macd_score


# ═════════════════════════════════════════════
# 市场/主题环境（5 分）
# ═════════════════════════════════════════════
def score_market(market_env: Optional[dict], r: BBSResult) -> float:
    """市场/主题环境 5 分。只调整买点质量，不改变技术结构结论"""
    if not market_env:
        # 环境缺失 → 保守给 2 分（震荡+一般），并降低置信度
        r.confidence = max(0.0, r.confidence - 0.15)
        r.market_score = 2.0
        return 2.0
    regime = str(market_env.get('regime', '震荡'))
    theme = str(market_env.get('theme', '中'))
    if regime in ('熊市', '快速下跌', '冰点', '退潮', '情绪冰点/退潮'):
        s = 0.0
    elif regime in ('牛市', '强趋势'):
        s = 5.0 if theme == '强' else 4.0
    else:  # 震荡
        s = 4.0 if theme == '强' else (3.0 if theme == '中' else 2.0)
    r.market_score = s
    return s


# ═════════════════════════════════════════════
# Stage / BuySignal / Level 判定
# ═════════════════════════════════════════════
def decide_stage(df: pd.DataFrame, r: BBSResult, platform: tuple, bottom_valid: bool) -> int:
    """阶段 Stage：1=Bottoming 2=Platform 3=Breakout 4=Pullback 5=Rebreakout 6=Failed 7=Extended"""
    ps, pe, ph, pl, breakout_day = platform
    close_now = float(df['close'].iloc[-1])
    # 跌破平台低 → Failed
    if breakout_day is not None and ph is not None and r.pullback_low is not None:
        if r.pullback_low < pl:
            return 6
    # 未突破
    if breakout_day is None:
        if not bottom_valid or r.platform_days < 5:
            return 1
        return 2
    # 突破后过热
    ma20 = float(df['ma20'].iloc[-1]) if not math.isnan(df['ma20'].iloc[-1]) else 0
    dist_ma20 = (close_now / ma20 - 1) * 100 if ma20 > 0 else 0
    if dist_ma20 > 15.0:
        return 7
    # 突破后回踩再转强（当前重新站上 MA5/MA10 且脱离回踩低点）
    ma5 = float(df['ma5'].iloc[-1]) if not math.isnan(df['ma5'].iloc[-1]) else 0
    ma10 = float(df['ma10'].iloc[-1]) if not math.isnan(df['ma10'].iloc[-1]) else 0
    pb = r.pullback_low
    if pb is not None and close_now > ma5 and close_now > ma10 and close_now > pb * 1.02:
        return 5
    # 回踩进行中（突破后已过 3+ 日且有回踩低点）
    if r.breakout_date and df.index[-1] > breakout_day + 2:
        return 4
    return 3


def apply_hard_blocks(df: pd.DataFrame, r: BBSResult, platform: tuple,
                      market_env: Optional[dict], breakout_day: Optional[int]) -> bool:
    """硬性禁止条件。命中任一 → BuySignal=NO_BUY。返回是否被禁止"""
    blocked = []
    ps, pe, ph, pl, _ = platform
    close_now = float(df['close'].iloc[-1])
    ma20 = float(df['ma20'].iloc[-1]) if not math.isnan(df['ma20'].iloc[-1]) else 0

    # 1. 底部尚未确认
    if r.bottom_score is not None and r.bottom_score < 10:
        blocked.append('底部未确认')
    # 2. 价格仍在持续创新低
    if '仍在创新低/底部未确认' in r.hard_rules_hit:
        blocked.append('持续创新低')
    # 3. MA20 明显向下且价格只是反弹
    if r.ma20_slope is not None and r.ma20_slope < -1.0 and close_now < ma20:
        blocked.append('MA20向下仅反弹')
    # 4. 突破后立即放量跌回平台
    if breakout_day is not None and breakout_day + 2 < len(df):
        seg = df.iloc[breakout_day + 1:breakout_day + 3]
        base = float(df['vol'].iloc[max(0, breakout_day - 20):breakout_day].mean())
        if base > 0 and (seg['vol'] > base * 1.5).any() and float(seg['close'].min()) < ph:
            blocked.append('突破后放量跌回平台')
    # 5. 回踩跌破平台 Low（stage=Failed）
    if r.stage == 6:
        blocked.append('跌破平台低点突破失败')
    # 6. 突破日巨量+长上影
    if breakout_day is not None:
        hi = float(df['high'].iloc[breakout_day]); lo = float(df['low'].iloc[breakout_day])
        op = float(df['open'].iloc[breakout_day]); cl = float(df['close'].iloc[breakout_day])
        upper = hi - max(op, cl)
        if r.vol_ratio is not None and r.vol_ratio > 3.0 and (hi - lo) > 0 and upper > 0.5 * (hi - lo):
            blocked.append('巨量长上影')
    # 7. 大盘快速下跌/极端退潮
    if market_env:
        regime = str(market_env.get('regime', ''))
        if regime in ('熊市', '快速下跌', '冰点', '情绪冰点/退潮'):
            blocked.append('大盘弱势')
    # 9. 偏离 MA20 过远
    if ma20 > 0 and close_now > ma20 * 1.15:
        blocked.append('偏离MA20过远')
    # 10. 突破日暴涨过热
    if '突破日暴涨过热' in r.hard_rules_hit:
        blocked.append('单日暴涨过热')

    r.failure_reason = '；'.join(blocked[:3]) if blocked else ''
    return len(blocked) > 0


def decide_signal(r: BBSResult, df: pd.DataFrame, platform: tuple, blocked: bool) -> str:
    """买点信号判定"""
    ps, pe, ph, pl, breakout_day = platform
    bbs = r.bbs or 0
    if blocked or r.bbs is None:
        return 'NO_BUY'
    # ADD_POSITION：BBS>=85 且 Stage=5 且再次突破回踩高点
    if r.stage == 5 and bbs >= 85:
        return 'ADD_POSITION'
    # PULLBACK_BUY：最高优先级，BBS>=80 且 Stage=5 且未破位
    if r.stage == 5 and bbs >= 80:
        return 'PULLBACK_BUY'
    # BREAKOUT_BUY：BBS>=75 且 Stage=3 且放量且 MA20 不明显向下
    if r.stage == 3 and bbs >= 75 and r.vol_ratio is not None and r.vol_ratio >= 1.3:
        if r.ma20_slope is None or r.ma20_slope >= -1.0:
            return 'BREAKOUT_BUY'
    if bbs >= 65:
        return 'WATCH'
    return 'NO_BUY'


def decide_level(bbs: float) -> str:
    if bbs >= 85:
        return 'S'
    if bbs >= 75:
        return 'A'
    if bbs >= 65:
        return 'B'
    if bbs >= 50:
        return 'C'
    return 'D'


def build_core_reason(r: BBSResult) -> str:
    """核心原因一句话（十六节示例格式）"""
    if r.stage == 6:
        return '突破失败'
    if r.buy_signal == 'NO_BUY' and r.confidence < 0.4:
        return '数据不足，降置信度'
    if r.buy_signal == 'NO_BUY' and r.failure_reason:
        return '禁止：%s' % r.failure_reason
    parts = []
    bs = r.bottom_score or 0
    if bs >= 16:
        parts.append('强底部确认')
    elif bs >= 12:
        parts.append('底部确认')
    elif bs >= 8:
        parts.append('弱底部确认')
    if r.platform_score and r.platform_score >= 10:
        parts.append('平台有效')
    if r.breakout_score and r.breakout_score >= 8:
        parts.append('有效突破')
    if r.vol_ratio is not None and r.vol_ratio >= 1.3:
        parts.append('放量确认')
    if r.stage == 5:
        parts.append('缩量回踩转强')
    elif r.stage == 4:
        parts.append('回踩中')
    if r.stage == 7:
        parts.append('短线过热')
    return '+'.join(parts) if parts else '底部修复中'


# ═════════════════════════════════════════════
# 主引擎
# ═════════════════════════════════════════════
class BBSEngine:
    def __init__(self):
        self._daily_cache: Dict[str, Optional[pd.DataFrame]] = {}
        self.market_env: Optional[dict] = None

    def set_market_env(self, env: Optional[dict]):
        """设置市场环境：{regime, theme, mainline}。缺省自动保守处理。"""
        self.market_env = env

    def score_one(self, ts_code: str, name: str = '', df: Optional[pd.DataFrame] = None,
                  market_env: Optional[dict] = None) -> BBSResult:
        """单股评分。df 可直接传入（升序 OHLCV），否则自动加载。"""
        r = BBSResult(ts_code=ts_code, name=name)
        env = market_env if market_env is not None else self.market_env
        if df is None:
            df = self._load(ts_code)
        if df is None or len(df) == 0:
            r.confidence = 0.1
            r.bbs = 0.0
            r.level = 'D'
            r.stage = 1
            r.stage_cn = STAGE_CN[1]
            r.failure_reason = '行情数据缺失'
            r.core_reason = '数据缺失，无法评分'
            return r
        r.n_bars = len(df)
        # 数据不足 → 降低置信度，绝不强行制造买点
        if len(df) < MIN_BARS:
            r.confidence = max(0.1, 0.3 * len(df) / MIN_BARS)
        elif len(df) < 90:
            r.confidence = 0.6
        else:
            r.confidence = 0.9
        df = compute_indicators(df)
        n = len(df)
        if n < 40:
            r.bbs = 0.0
            r.level = 'D'
            r.stage = 1
            r.stage_cn = STAGE_CN[1]
            r.failure_reason = 'K线数据不足40根'
            r.core_reason = '数据不足，无法评分'
            return r

        # ① 底部确认
        bottom_score = score_bottom(df, r)
        bottom_valid = bottom_score >= 10
        # ② 平台识别（底部后）
        bottom_idx = n - min(BOTTOM_WINDOW, n) + int(np.argmin(df['low'].iloc[-min(BOTTOM_WINDOW, n):].values))
        platform = _find_platform(df, bottom_idx, r)
        platform_score = score_platform(df, r, platform)
        # ③ 突破
        breakout_score = score_breakout(df, r, platform)
        # ④ 成交量
        volume_score = score_volume(df, r, platform)
        # ⑤ 均线
        ma_score = score_ma(df, r)
        # ⑥ 回踩
        pullback_score = score_pullback(df, r, platform)
        # ⑦ RSI/MACD
        rsi_macd_score = score_rsi_macd(df, r)
        # ⑧ 市场环境
        market_score = score_market(env, r)

        total = (bottom_score + platform_score + breakout_score + volume_score
                 + ma_score + pullback_score + rsi_macd_score + market_score)

        # 硬规则：底部确认 <10 → 上限 69
        if bottom_score < 10:
            total = min(total, 69.0)
        # 硬规则：MA20/30/60 全部向下 → 上限 74
        ma20, ma30, ma60 = (float(df[f'ma{n2}'].iloc[-1]) if not math.isnan(df[f'ma{n2}'].iloc[-1]) else 0
                            for n2 in (20, 30, 60))
        ma20_p, ma30_p, ma60_p = (float(df[f'ma{n2}'].iloc[-6]) if n >= 6 and not math.isnan(df[f'ma{n2}'].iloc[-6]) else 0
                                  for n2 in (20, 30, 60))
        if ma20 > 0 and ma30 > 0 and ma60 > 0 and ma20_p > 0 and ma30_p > 0 and ma60_p > 0:
            if ma20 < ma20_p and ma30 < ma30_p and ma60 < ma60_p:
                total = min(total, 74.0)

        r.bbs = round(total, 1)
        r.level = decide_level(total)

        # Stage
        r.stage = decide_stage(df, r, platform, bottom_valid)
        r.stage_cn = STAGE_CN.get(r.stage, str(r.stage))

        # 硬禁止 → NO_BUY
        blocked = apply_hard_blocks(df, r, platform, env, platform[4])
        r.buy_signal = decide_signal(r, df, platform, blocked)
        if r.bbs < 50:
            r.buy_signal = 'NO_BUY'
        if r.stage == 6:
            r.buy_signal = 'NO_BUY'
        if r.confidence < 0.35:
            r.buy_signal = 'NO_BUY'
            if not r.failure_reason:
                r.failure_reason = '数据不足，置信度低'

        r.core_reason = build_core_reason(r)
        return r

    def _load(self, ts_code: str) -> Optional[pd.DataFrame]:
        code6 = str(ts_code).split('.')[0]
        if code6 in self._daily_cache:
            return self._daily_cache[code6]
        full = to_ts_code(code6)
        df = load_daily(full)
        self._daily_cache[code6] = df
        return df

    # ── 批量 ──
    def run_pool(self, pool: List[dict], market_env: Optional[dict] = None) -> List[BBSResult]:
        """pool: [{'code': '002414', 'name': '高德红外'}, ...]"""
        if market_env is not None:
            self.set_market_env(market_env)
        results = []
        n = len(pool)
        for i, item in enumerate(pool, 1):
            try:
                r = self.score_one(str(item.get('code', '')), str(item.get('name', '')))
                results.append(r)
            except Exception as e:
                results.append(BBSResult(ts_code=str(item.get('code', '')), name=str(item.get('name', '')),
                                         bbs=0.0, level='D', stage=1, stage_cn=STAGE_CN[1],
                                         confidence=0.1, failure_reason='评分异常: %s' % e,
                                         core_reason='评分异常'))
            if i % 100 == 0 or i == n:
                print(f'[BBS] 进度 {i}/{n}')
        return results

    # ── 输出 ──
    def to_report(self, results: List[BBSResult]) -> pd.DataFrame:
        rows = []
        for r in results:
            rows.append({
                '代码': r.ts_code, '名称': r.name, 'BBS': r.bbs, '等级': r.level,
                '等级说明': LEVEL_CN.get(r.level, ''), '阶段': r.stage, '阶段说明': r.stage_cn,
                '买点': BUY_CN.get(r.buy_signal, r.buy_signal), '买点代码': r.buy_signal,
                '核心原因': r.core_reason, '置信度': round(r.confidence, 2), '失败原因': r.failure_reason,
                '底部确认(20)': r.bottom_score, '平台整理(15)': r.platform_score,
                '突破(15)': r.breakout_score, '量能(15)': r.volume_score,
                '均线(15)': r.ma_score, '回踩(10)': r.pullback_score,
                'RSI_MACD(5)': r.rsi_macd_score, '市场(5)': r.market_score,
                '底部日期': r.bottom_date, '底部价': None if r.bottom_price is None else round(r.bottom_price, 2),
                '平台高': None if r.platform_high is None else round(r.platform_high, 2),
                '平台低': None if r.platform_low is None else round(r.platform_low, 2),
                '平台宽幅%': None if r.platform_width is None else round(r.platform_width, 1),
                '平台天数': r.platform_days,
                '突破日期': r.breakout_date, '突破幅度%': None if r.breakout_pct is None else round(r.breakout_pct, 1),
                '量比': None if r.vol_ratio is None else round(r.vol_ratio, 2),
                '回踩低点': None if r.pullback_low is None else round(r.pullback_low, 2),
                '回踩幅度%': None if r.pullback_pct is None else round(r.pullback_pct, 1),
                'RSI': None if r.rsi is None else round(r.rsi, 1),
                'MACD金叉': r.macd_gold, 'MA20斜率%': None if r.ma20_slope is None else round(r.ma20_slope, 2),
                'K线数': r.n_bars,
            })
        return pd.DataFrame(rows)

    def save(self, rep: pd.DataFrame, csv_path: str, txt_path: str = None):
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        rep.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f'[BBS] CSV 已保存: {csv_path} ({len(rep)}只)')
        if txt_path:
            self._save_txt(rep, txt_path)

    def _save_txt(self, rep: pd.DataFrame, txt_path: str):
        """十六节要求的简洁输出：股票 | BBS | 等级 | 阶段 | 买点 | 核心原因"""
        lines = []
        lines.append('BBS 100 底部右侧突破买点（最新交易日）')
        lines.append('═' * 60)
        buy_rank = {'★最佳买点': 0, '首仓': 1, '加仓': 2, '观察': 3, '不买': 4}
        rep['_buy_rank'] = rep['买点'].map(buy_rank).fillna(9)
        rep = rep.sort_values(['_buy_rank', 'BBS'], ascending=[True, False])
        for _, row in rep.iterrows():
            if row['买点'] == '不买' and row['BBS'] < 65:
                continue  # 低分不买不占版面（保留 CSV 全量）
            lines.append('%s | %s | %s | %s | %s | %s' % (
                row['名称'], row['BBS'], row['等级'], row['阶段说明'],
                row['买点'], row['核心原因']))
        lines.append('═' * 60)
        n_buy = int((rep['买点'].isin(['首仓', '★最佳买点', '加仓'])).sum())
        n_watch = int((rep['买点'] == '观察').sum())
        lines.append('买点统计：首仓/最佳买点/加仓 %d 只，观察 %d 只，共 %d 只' % (n_buy, n_watch, len(rep)))
        os.makedirs(os.path.dirname(txt_path), exist_ok=True)
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f'[BBS] TXT 已保存: {txt_path}')


# ═════════════════════════════════════════════
# 市场环境自动加载（复用现有系统）
# ═════════════════════════════════════════════
def load_market_env(trade_date: str = None) -> Optional[dict]:
    """自动获取市场环境：优先解析 market_analysis_{date}.txt（V9.9 大盘引擎成果），
    失败则调用 entry_timing_engine.detect_market_regime。"""
    env = None
    date = trade_date or get_last_trade_date()
    txt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'cache_backbone_tushare', f'market_analysis_{date}.txt')
    if os.path.exists(txt):
        try:
            with open(txt, 'r', encoding='utf-8') as f:
                content = f.read()
            regime = '震荡'
            if '强趋势' in content:
                regime = '牛市'
            elif any(k in content for k in ('冰点', '快速下跌', '退潮')):
                regime = '熊市'
            theme = '中'
            for line in content.splitlines():
                if '主线：' in line:
                    ml = line.split('主线：')[-1].strip()
                    if '强' in ml:
                        theme = '强'
                    elif '弱' in ml:
                        theme = '弱'
                    break
            env = {'regime': regime, 'theme': theme, 'mainline': theme}
        except Exception:
            env = None
    if env is None:
        try:
            from entry_timing_engine import detect_market_regime  # noqa
            regime = detect_market_regime(date)
            env = {'regime': regime, 'theme': '中', 'mainline': '中'}
        except Exception:
            env = None
    return env


def load_pool(csv_path: str, limit: int = None) -> List[dict]:
    """从候选池 CSV 加载股票列表（兼容 bull_stocks_all / double_score 等，含 code/name）"""
    df = pd.read_csv(csv_path, dtype={'code': str})
    # 兼容不同列名
    code_col = 'code' if 'code' in df.columns else ('代码' if '代码' in df.columns else None)
    name_col = 'name' if 'name' in df.columns else ('名称' if '名称' in df.columns else None)
    if code_col is None:
        raise ValueError('候选池缺少 code/代码 列')
    pool = []
    for _, row in df.iterrows():
        code = str(row[code_col]).strip()
        if not code or code == 'nan':
            continue
        code6 = code.split('.')[0].zfill(6)
        if code6.startswith(('4', '8')):
            continue  # 不交易北交所
        pool.append({'code': code6, 'name': str(row[name_col]) if name_col else ''})
        if limit and len(pool) >= limit:
            break
    return pool


# ═════════════════════════════════════════════
# 独立入口
# ═════════════════════════════════════════════
def main():
    import argparse
    parser = argparse.ArgumentParser(description='BBS 100 底部右侧突破买点评分')
    parser.add_argument('--csv', default=r'd:\mystock\solo\report_daily\bull_stocks_all.csv',
                        help='候选池 CSV 路径')
    parser.add_argument('--limit', type=int, default=None, help='只评前 N 只（调试用）')
    parser.add_argument('--date', default=None, help='交易日 YYYYMMDD')
    parser.add_argument('--code', default=None, help='单股调试：6位代码')
    args = parser.parse_args()

    if args.code:
        engine = BBSEngine()
        env = load_market_env(args.date)
        engine.set_market_env(env)
        r = engine.score_one(args.code, args.code, market_env=env)
        print('═' * 60)
        print('%s | BBS %s | %s级 | %s | %s | %s' % (
            r.ts_code, r.bbs, r.level, r.stage_cn, BUY_CN.get(r.buy_signal, r.buy_signal), r.core_reason))
        print('置信度 %.2f | 失败原因 %s' % (r.confidence, r.failure_reason))
        print('明细：底部%.1f/20 平台%.1f/15 突破%.1f/15 量能%.1f/15 均线%.1f/15 回踩%.1f/10 RSI_MACD%.1f/5 市场%.1f/5' % (
            r.bottom_score or 0, r.platform_score or 0, r.breakout_score or 0, r.volume_score or 0,
            r.ma_score or 0, r.pullback_score or 0, r.rsi_macd_score or 0, r.market_score or 0))
        print('平台高%.2f 平台低%.2f 宽幅%.1f%% 平台%d日 | 突破%s %+.1f%% 量比%.2f | 回踩低%.2f' % (
            r.platform_high or 0, r.platform_low or 0, r.platform_width or 0, r.platform_days,
            r.breakout_date or '-', r.breakout_pct or 0, r.vol_ratio or 0, r.pullback_low or 0))
        return

    pool = load_pool(args.csv, args.limit)
    print(f'[BBS] 候选池 {len(pool)} 只，开始评分…')
    engine = BBSEngine()
    env = load_market_env(args.date)
    print(f'[BBS] 市场环境: {env}')
    results = engine.run_pool(pool, market_env=env)
    rep = engine.to_report(results)
    date = args.date or get_last_trade_date()
    csv_out = os.path.join(r'd:\mystock\solo\report_daily', f'bbs_{date}.csv')
    txt_out = os.path.join(r'd:\mystock\solo\report_daily', f'bbs_{date}.txt')
    engine.save(rep, csv_out, txt_out)


if __name__ == '__main__':
    main()
