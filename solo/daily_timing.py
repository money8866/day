"""
每日择时信号扫描 — 针对已生成的合格股池

读取 report_daily/bull_stocks_qualified.csv 中的标的，
计算趋势/低吸/突破择时信号，输出操作建议清单。

用法:
    python daily_timing.py                         # 默认最近交易日
    python daily_timing.py --date 20260717         # 指定日期
    python daily_timing.py --top 50                # 显示前50名
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 技术指标计算（轻量版，无外部依赖）
# ──────────────────────────────────────────────

def _ma(series: pd.Series, n: int) -> float:
    if len(series) < n:
        return float(series.iloc[-1]) if len(series) > 0 else 0.0
    return float(series.tail(n).mean())


def _volume_ratio(vol: pd.Series) -> float:
    if len(vol) < 2:
        return 1.0
    today = float(vol.iloc[-1])
    avg = float(vol.tail(min(6, len(vol))).iloc[:-1].mean())
    return today / avg if avg > 0 else 1.0


def _macd(close: pd.Series) -> tuple:
    if len(close) < 26:
        return 0.0, 0.0, 0.0
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    bar = dif - dea
    return float(dif.iloc[-1]), float(dea.iloc[-1]), float(bar.iloc[-1])


def _rsi(close: pd.Series, n: int = 14) -> float:
    if len(close) < n + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_g = float(gain.tail(n + 1).mean())
    avg_l = float(loss.tail(n + 1).mean())
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def _kdj(high, low, close, n=9) -> tuple:
    if len(close) < n:
        return 50.0, 50.0, 50.0
    hn = float(high.tail(n).max())
    ln = float(low.tail(n).min())
    if hn == ln:
        return 50.0, 50.0, 50.0
    rsv = (float(close.iloc[-1]) - ln) / (hn - ln) * 100
    k = 2 / 3 * 50 + 1 / 3 * rsv
    d = 2 / 3 * 50 + 1 / 3 * k
    j = 3 * k - 2 * d
    return k, d, j


def _drawdown(close: pd.Series, lookback: int = 60) -> float:
    if len(close) < 2:
        return 0.0
    period = close.tail(min(lookback, len(close)))
    peak = float(period.max())
    cur = float(close.iloc[-1])
    if peak <= 0:
        return 0.0
    return (peak - cur) / peak * 100


def _lower_shadow(row) -> float:
    amp = row['high'] - row['low']
    if amp <= 0:
        return 0.0
    body_low = min(row['open'], row['close'])
    return (body_low - row['low']) / amp


# ──────────────────────────────────────────────
# 参数管理
# ──────────────────────────────────────────────

def load_timing_params(path: str = None) -> dict:
    """加载最优择时参数"""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), 'config', 'optimal_timing_params.json')
    if os.path.exists(path):
        with open(path) as f:
            params = json.load(f)
        logger.info(f"加载择时参数: {path}")
        return params
    logger.warning("参数文件不存在，使用默认参数")
    return {}


# ──────────────────────────────────────────────
# 黄金梯队过滤器（防追高降级 + 买点萃取）
# ──────────────────────────────────────────────

def filter_golden_tier(df: pd.DataFrame, params: dict = None) -> pd.DataFrame:
    """
    对择时结果进行防追高过滤与黄金梯队精准提炼。

    输入: daily_timing 的全量结果 DataFrame
    输出: 新增 tier / buy_value_score / final_suggestion 列，并返回黄金梯队子集
    """
    p = params or {}
    # 门槛参数（可配置）
    TREND_GOLDEN_TH = p.get('gt_trend_th', 90)        # 黄金梯队趋势分门槛
    DIP_GOLDEN_TH   = p.get('gt_dip_th', 25)          # 低吸型黄金梯队 dip 门槛
    BRK_GOLDEN_TH   = p.get('gt_brk_th', 30)          # 突破型黄金梯队 breakout 门槛
    CHASE_TREND_TH  = p.get('gt_chase_trend_th', 95)  # 防追高：趋势分阈值
    CHASE_DIP_TH    = p.get('gt_chase_dip_th', 10)    # 防追高：dip 下限
    CHASE_BRK_TH    = p.get('gt_chase_brk_th', 15)    # 防追高：breakout 下限

    data = df.copy()

    # 1. 安全类型转换
    for col in ['trend_score', 'dip_score', 'breakout_score']:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors='coerce').fillna(0)

    # 2. 防追高逻辑降级
    #    趋势很强但无买点（dip 和 breakout 双低）→ 标记为"观望/持股"
    chasing_risk_mask = (
        (data['trend_score'] >= CHASE_TREND_TH) &
        (data['dip_score'] < CHASE_DIP_TH) &
        (data['breakout_score'] < CHASE_BRK_TH)
    )
    data['tier'] = '其他'
    data.loc[chasing_risk_mask, 'tier'] = '👀 观望/持股(无买点)'

    # 3. 黄金梯队判定（三档通道）
    # 通道A：标准黄金 — 趋势强(trend≥90) + 低吸(dip≥25) 或 突破(brk≥30)
    dip_golden = (data['trend_score'] >= TREND_GOLDEN_TH) & (data['dip_score'] >= DIP_GOLDEN_TH)
    brk_golden = (data['trend_score'] >= TREND_GOLDEN_TH) & (data['breakout_score'] >= BRK_GOLDEN_TH)

    # 通道B：低吸主导型豁免 — 趋势偏强(trend≥80) + 低吸极佳(dip≥35)
    #         适用于趋势稍弱但回调到位的"右侧低吸"标的（如002440在7/20的情况）
    DIP_DOMINANT_TREND_TH = p.get('gt_dip_dominant_trend_th', 80)
    DIP_DOMINANT_DIP_TH   = p.get('gt_dip_dominant_dip_th', 35)
    dip_dominant = (data['trend_score'] >= DIP_DOMINANT_TREND_TH) & (data['dip_score'] >= DIP_DOMINANT_DIP_TH)

    # 合并：任一通道达标即为低吸型黄金
    dip_golden = dip_golden | dip_dominant

    # 先标记低吸型 / 突破型
    data.loc[dip_golden & ~brk_golden, 'tier'] = '🥇 黄金梯队(低吸接棒)'
    data.loc[brk_golden & ~dip_golden, 'tier'] = '🥇 黄金梯队(强力突破)'
    data.loc[dip_golden & brk_golden, 'tier'] = '💎 超级黄金(低吸+突破共振)'

    # 4. 买入性价比得分 = dip*0.6 + breakout*0.4
    data['buy_value_score'] = data['dip_score'] * 0.6 + data['breakout_score'] * 0.4
    data['buy_value_score'] = data['buy_value_score'].round(1)

    # 5. 生成最终操作建议
    data['final_suggestion'] = data['suggestion']
    # 防追高标的：覆盖建议
    data.loc[chasing_risk_mask, 'final_suggestion'] = '👀 观望/持股(无买点)'
    # 黄金梯队：补充操作建议
    dip_only = data['tier'] == '🥇 黄金梯队(低吸接棒)'
    brk_only = data['tier'] == '🥇 黄金梯队(强力突破)'
    super_golden = data['tier'] == '💎 超级黄金(低吸+突破共振)'
    data.loc[dip_only, 'final_suggestion'] = '🥇 回踩逢低吸纳'
    data.loc[brk_only, 'final_suggestion'] = '🥇 突破放量追击'
    data.loc[super_golden, 'final_suggestion'] = '💎 低吸+突破共振，重点参与'

    # 6. 提取黄金梯队子集（含三种黄金标签）
    golden_df = data[data['tier'].str.contains('黄金')].copy()
    golden_df = golden_df.sort_values(
        by=['buy_value_score', 'trend_score'], ascending=[False, False]
    ).reset_index(drop=True)

    return data, golden_df


# ──────────────────────────────────────────────
# 核心评分逻辑
# ──────────────────────────────────────────────

def score_stock(ts_code: str, df: pd.DataFrame, params: dict = None,
                index_pct_chg: float = None) -> dict:
    """
    对单只股票计算择时信号分。
    df: 该股票的日线DataFrame, 需含 trade_date, open, high, low, close, vol, pct_chg
    index_pct_chg: 当日大盘涨跌幅（用于相对强弱计算）
    """
    if df is None or len(df) < 20:
        return {'ts_code': ts_code, 'composite_score': 0, 'signal_level': '数据不足',
                'trend_score': 0, 'dip_score': 0, 'breakout_score': 0,
                'signal_type': 'none', 'signals': [], 'suggestion': '数据不足'}

    p = params or {}
    TREND_MA_STRONG = p.get('trend_ma_strong', 25)
    TREND_MA_WEAK = p.get('trend_ma_weak', 12)
    TREND_MACD_STRONG = p.get('trend_macd_strong', 20)
    TREND_MACD_WEAK = p.get('trend_macd_weak', 10)
    TREND_MACD_BOTTOM = p.get('trend_macd_bottom', 15)
    TREND_MA_BREAK = p.get('trend_ma_break', 15)
    TREND_REL_STRONG = p.get('trend_rel_strong', 15)
    TREND_REL_MID = p.get('trend_rel_mid', 8)
    TREND_VOL_CONT = p.get('trend_vol_cont', 10)
    TREND_ABOVE_MA = p.get('trend_above_ma', 8)
    TREND_VOL_STRONG = p.get('trend_vol_strong', 20)
    TREND_VOL_WEAK = p.get('trend_vol_weak', 10)
    TREND_CHG20 = p.get('trend_chg20', 15)
    TREND_KDJ = p.get('trend_kdj', 15)
    MIN_STRONG_SIGNALS = p.get('min_strong_signals', 3)
    STRONG_REQUIRED_FACTORS = p.get('strong_required_factors', 2)
    DIP_MA10 = p.get('dip_ma10', 20)
    DIP_MA10_PCT = p.get('dip_ma10_pct', 3.0)
    DIP_MA20 = p.get('dip_ma20', 15)
    DIP_MA20_PCT = p.get('dip_ma20_pct', 5.0)
    DIP_MA20_WEAK = p.get('dip_ma20_weak', 8)
    DIP_MA20_WEAK_PCT = p.get('dip_ma20_weak_pct', 8.0)
    DIP_VOL_LOW = p.get('dip_vol_low', 20)
    DIP_VOL_LOW_PCT = p.get('dip_vol_low_pct', 0.8)
    DIP_VOL_MID = p.get('dip_vol_mid', 8)
    DIP_VOL_MID_PCT = p.get('dip_vol_mid_pct', 1.0)
    DIP_SHADOW = p.get('dip_shadow', 15)
    DIP_RSI_OVER = p.get('dip_rsi_over', 25)
    DIP_RSI_OVER_VAL = p.get('dip_rsi_over_val', 35)
    DIP_RSI_LOW = p.get('dip_rsi_low', 15)
    DIP_RSI_LOW_VAL = p.get('dip_rsi_low_val', 45)
    DIP_RSI_MID = p.get('dip_rsi_mid', 5)
    DIP_RSI_MID_VAL = p.get('dip_rsi_mid_val', 55)
    DIP_DD = p.get('dip_dd', 10)
    DIP_DD_VAL = p.get('dip_dd_val', 10.0)
    BRK_VOL_STRONG = p.get('brk_vol_strong', 30)
    BRK_VOL_STRONG_CHG = p.get('brk_vol_strong_chg', 3.0)
    BRK_VOL_STRONG_RATIO = p.get('brk_vol_strong_ratio', 1.5)
    BRK_VOL_MID = p.get('brk_vol_mid', 15)
    BRK_VOL_MID_CHG = p.get('brk_vol_mid_chg', 2.0)
    BRK_VOL_MID_RATIO = p.get('brk_vol_mid_ratio', 1.3)
    BRK_HIGH20 = p.get('brk_high20', 20)
    BRK_GOLDEN = p.get('brk_golden', 15)
    BRK_BOX = p.get('brk_box', 15)
    BRK_BOX_AMP = p.get('brk_box_amp', 15.0)
    BRK_ZT = p.get('brk_zt', 10)
    THRESHOLD_STRONG = p.get('threshold_strong', 80)
    THRESHOLD_MED = p.get('threshold_med', 60)
    THRESHOLD_LOW = p.get('threshold_low', 40)

    df = df.sort_values('trade_date').reset_index(drop=True)
    c = df['close']
    v = df['vol']
    h = df['high']
    l = df['low']
    row = df.iloc[-1]
    pct = float(row.get('pct_chg', 0) or 0)
    cur = float(c.iloc[-1])

    # 指标
    ma5 = _ma(c, 5)
    ma10 = _ma(c, 10)
    ma20 = _ma(c, 20)
    ma60 = _ma(c, 60)
    vr = _volume_ratio(v)
    dif, dea, bar = _macd(c)
    rsi = _rsi(c)
    k, d, j = _kdj(h, l, c)
    dd = _drawdown(c)
    sr = _lower_shadow(row)

    # 20日涨跌幅
    chg20 = ((c.iloc[-1] / c.iloc[-min(21, len(c))]) - 1) * 100 if len(c) >= 21 else 0
    # 20日振幅
    amp20 = (float(h.tail(20).max()) - float(l.tail(20).min())) / cur * 100 if len(c) >= 20 else 0
    high20 = float(h.tail(20).max())
    # 涨停次数(60日)
    zt60 = int((df.tail(60)['pct_chg'].fillna(0) >= 9.5).sum()) if 'pct_chg' in df.columns else 0

    signals = []

    # ── 趋势信号 ──
    ts = 0
    if ma5 > ma10 > ma20:
        ts += TREND_MA_STRONG
        signals.append("MA多头排列")
    elif ma5 > ma20:
        ts += TREND_MA_WEAK
        signals.append("MA偏多")
    # 股价站上MA10/MA20独立加分（需配合放量或涨幅）
    if cur > ma20 and (vr >= 1.2 or abs(pct) >= 2):
        ts += TREND_ABOVE_MA
        signals.append("站上MA20")
    if cur > ma10 and (vr >= 1.2 or abs(pct) >= 2):
        ts += max(TREND_ABOVE_MA - 3, 5)
    if cur > ma20 and vr >= 1.3:
        ts += TREND_VOL_STRONG
        signals.append("站MA20+放量")
    elif cur > ma20:
        ts += TREND_VOL_WEAK
    if dif > dea > 0:
        ts += TREND_MACD_STRONG
        signals.append("MACD多头")
    elif dif > dea:
        ts += TREND_MACD_WEAK
    # MACD底部刚启动（DIF上穿0轴附近，适合底部反转股）
    if dif > dea > 0 and dif < 0.3 and dea > -0.05:
        ts += TREND_MACD_BOTTOM
        signals.append("MACD底部刚启动")
    # 放量突破均线压制（大阳线突破MA10/MA20但MA尚未多头排列）
    if pct >= 3 and vr >= 1.3 and cur > max(ma10, ma20) and not (ma5 > ma10 > ma20):
        ts += TREND_MA_BREAK
        signals.append("放量突破均线压制")
    # 量能持续放大（今日量比>1.2且昨日量比>1.0）
    if len(v) >= 3:
        vr_yesterday = _volume_ratio(v.iloc[:-1])
        if vr >= 1.2 and vr_yesterday >= 1.0:
            ts += TREND_VOL_CONT
            signals.append("量能持续放大")
    if 5 <= chg20 <= 20:
        ts += TREND_CHG20
    elif chg20 > 0:
        ts += 5
    if j > k > 50:
        ts += TREND_KDJ
        signals.append("KDJ多头")
    # 大盘相对强弱（抗跌/超涨）
    if index_pct_chg is not None:
        rel = pct - index_pct_chg
        if rel >= 3:
            ts += TREND_REL_STRONG
            signals.append(f"相对强势(+{rel:.1f}%)")
        elif rel >= 1:
            ts += TREND_REL_MID
            signals.append(f"相对偏强(+{rel:.1f}%)")
    ts = min(ts, 100)

    # ── 低吸信号 ──
    ds = 0
    d10 = abs(cur - ma10) / ma10 * 100 if ma10 > 0 else 999
    d20 = abs(cur - ma20) / ma20 * 100 if ma20 > 0 else 999
    if d10 <= DIP_MA10_PCT:
        ds += DIP_MA10
        signals.append(f"回踩MA10({d10:.1f}%)")
    elif d20 <= DIP_MA20_PCT:
        ds += DIP_MA20
        signals.append(f"回踩MA20({d20:.1f}%)")
    elif d20 <= DIP_MA20_WEAK_PCT:
        ds += DIP_MA20_WEAK
        signals.append(f"近MA20({d20:.1f}%)")
    if vr < DIP_VOL_LOW_PCT:
        ds += DIP_VOL_LOW
        signals.append(f"缩量(vr={vr:.2f})")
    elif vr < DIP_VOL_MID_PCT:
        ds += DIP_VOL_MID
    if sr > 0.5:
        ds += DIP_SHADOW
        signals.append(f"下影({sr:.0%})")
    if rsi < DIP_RSI_OVER_VAL:
        ds += DIP_RSI_OVER
        signals.append(f"RSI超卖({rsi:.0f})")
    elif rsi < DIP_RSI_LOW_VAL:
        ds += DIP_RSI_LOW
        signals.append(f"RSI偏低({rsi:.0f})")
    elif rsi < DIP_RSI_MID_VAL:
        ds += DIP_RSI_MID
    if dd >= DIP_DD_VAL:
        ds += DIP_DD
        signals.append(f"回撤{dd:.0f}%")
    ds = min(ds, 100)

    # ── 突破信号 ──
    bs = 0
    if abs(pct) >= BRK_VOL_STRONG_CHG and vr >= BRK_VOL_STRONG_RATIO:
        bs += BRK_VOL_STRONG
        signals.append(f"放量突破(+{pct:.1f}% vr={vr:.2f})")
    elif abs(pct) >= BRK_VOL_MID_CHG and vr >= BRK_VOL_MID_RATIO:
        bs += BRK_VOL_MID
        signals.append("放量上涨")
    if cur >= high20:
        bs += BRK_HIGH20
        signals.append("突破20日高")
    if len(c) >= 11:
        ma5_y = _ma(c.iloc[:-1], 5)
        ma10_y = _ma(c.iloc[:-1], 10)
        if ma5_y <= ma10_y and ma5 > ma10:
            bs += BRK_GOLDEN
            signals.append("MA5金叉MA10")
    if amp20 < BRK_BOX_AMP and cur >= high20 * 0.98:
        bs += BRK_BOX
        signals.append("箱体突破")
    if zt60 >= 1:
        bs += BRK_ZT
        signals.append(f"60日{zt60}次涨停")
    bs = min(bs, 100)

    scores = [('trend', ts), ('dip', ds), ('breakout', bs)]
    best_type, best_val = max(scores, key=lambda x: x[1])

    # 多因子共振约束：强烈信号需≥MIN_STRONG_SIGNALS个信号因子
    n_signals = len(signals)
    strong_signal_ok = n_signals >= MIN_STRONG_SIGNALS

    # 核心因子门槛：必须满足≥STRONG_REQUIRED_FACTORS个核心因子
    core_factors = ['MA多头排列', 'MACD多头', 'KDJ多头', '站MA20+放量', '量能持续放大', 'MACD底部刚启动', '放量突破均线压制']
    n_core = sum(1 for cf in core_factors if cf in signals)
    core_ok = n_core >= STRONG_REQUIRED_FACTORS

    if best_val >= THRESHOLD_STRONG and strong_signal_ok and core_ok:
        level = "强烈"
        sug = "★ 强烈建议" + ("逢低介入" if best_type == 'dip' else "追涨参与" if best_type == 'breakout' else "积极关注")
    elif best_val >= THRESHOLD_STRONG and strong_signal_ok:
        # 分数够但核心因子不足，降为中等
        level = "中等"
        sug = f"可适度关注（核心因子{n_core}/{STRONG_REQUIRED_FACTORS}）"
    elif best_val >= THRESHOLD_STRONG:
        # 分数够但信号因子不足，降为中等
        level = "中等"
        sug = "可适度关注（信号因子不足）"
    elif best_val >= THRESHOLD_MED:
        level = "中等"
        sug = "可" + ("逢低建仓" if best_type == 'dip' else "小仓参与" if best_type == 'breakout' else "适度关注")
    elif best_val >= THRESHOLD_LOW:
        level = "一般"
        sug = "观察等待" if pct > 3 else "保持观望"
    else:
        level = "观望"
        sug = "暂不参与"

    return dict(
        ts_code=ts_code,
        trend_score=round(ts, 1),
        dip_score=round(ds, 1),
        breakout_score=round(bs, 1),
        composite_score=round(best_val, 1),
        signal_type=best_type,
        signal_level=level,
        signals=list(dict.fromkeys(signals))[:6],
        suggestion=sug,
    )


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

def load_qualified(path: str = None) -> pd.DataFrame:
    """加载合格股池"""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), 'report_daily', 'bull_stocks_qualified.csv')
    if not os.path.exists(path):
        logger.error(f"文件不存在: {path}")
        # 尝试读取 bull_stocks_all.csv
        alt = path.replace('bull_stocks_qualified', 'bull_stocks_all')
        if os.path.exists(alt):
            logger.info(f"回退读取: {alt}")
            path = alt
        else:
            sys.exit(1)
    df = pd.read_csv(path)
    logger.info(f"加载合格标的: {len(df)} 只")
    return df


def _load_token() -> str:
    """从 config/.env 读取 Tushare Token"""
    # 优先从环境变量
    token = os.environ.get('TUSHARE_TOKEN')
    if token:
        return token
    # 直接读取 config/.env
    for parent in [os.path.dirname(__file__), os.path.join(os.path.dirname(__file__), '..')]:
        env_path = os.path.join(parent, 'config', '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith('TUSHARE_TOKEN'):
                        token = line.split('=', 1)[1].strip().strip('"\' ')
                        if token:
                            os.environ['TUSHARE_TOKEN'] = token
                            return token
    logger.error("TUSHARE_TOKEN 未配置")
    sys.exit(1)


def fetch_daily_data(qualified_df: pd.DataFrame, end_date: str, days: int = 120) -> pd.DataFrame:
    """通过 DataFetcher 批量拉取日线数据"""
    from multi_factor_picker.data_fetcher import DataFetcher

    token = _load_token()
    config = {'cache': {'enabled': True, 'dir': 'cache'}, 'tushare': {'max_retry': 3, 'retry_delay': 5}}
    fetcher = DataFetcher(token, config)

    logger.info(f"拉取日线数据 (截止{end_date}, {days}天)...")
    daily = fetcher.get_daily_history(end_date, days)
    logger.info(f"日线数据: {len(daily)} 条")

    # 限幅到合格标的
    codes = qualified_df['code'].tolist()
    # 统一转成 ts_code 格式 (000001.SZ)
    ts_codes = set()
    for c in codes:
        c = str(c).strip().zfill(6)
        if c.startswith('6'):
            ts_codes.add(f"{c}.SH")
        elif c.startswith('0') or c.startswith('3'):
            ts_codes.add(f"{c}.SZ")
        elif c.startswith('8') or c.startswith('4'):
            ts_codes.add(f"{c}.SZ")  # 北交所/三板
        elif c.startswith('9'):
            ts_codes.add(f"{c}.SZ")

    daily = daily[daily['ts_code'].isin(ts_codes)].copy()
    logger.info(f"匹配到 {len(daily)} 条日线 (共 {len(ts_codes)} 只标的)")
    return daily


def fetch_index_daily(end_date: str, days: int = 120, ts_code: str = '000001.SH') -> pd.DataFrame:
    """拉取大盘指数日线数据（默认上证指数）"""
    import tushare as ts
    from datetime import datetime, timedelta
    token = _load_token()
    ts.set_token(token)
    pro = ts.pro_api()

    # 计算起始日期
    date_obj = datetime.strptime(end_date, '%Y%m%d')
    start_date = (date_obj - timedelta(days=int(days * 1.5))).strftime('%Y%m%d')

    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        return df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        logger.warning(f"获取指数数据失败: {e}")
        return pd.DataFrame()


def get_index_pct_chg(index_df: pd.DataFrame, trade_date: str) -> float:
    """从指数日线中获取指定交易日的涨跌幅"""
    if index_df is None or len(index_df) == 0:
        return None
    td = str(trade_date).replace('-', '')
    row = index_df[index_df['trade_date'].astype(str).str.replace('-', '') == td]
    if len(row) > 0:
        return float(row.iloc[0]['pct_chg'])
    return None


def _resolve_trade_date(trade_date: str = None) -> str:
    """
    解析交易日：
    - 若显式传入 trade_date，直接使用
    - 否则取当前时间：
        * 周一~周五 16:00 之后 → 当天
        * 周一~周五 16:00 之前 → 上一个交易日（周五）
        * 周末 → 最近一个周五
    """
    if trade_date:
        return trade_date

    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()  # 0=Mon, 6=Sun

    # 计算偏移天数
    if weekday == 5:  # 周六
        offset = -1
    elif weekday == 6:  # 周日
        offset = -2
    elif hour < 16:  # 工作日但盘前
        if weekday == 0:   # 周一盘前 → 上周五
            offset = -3
        else:
            offset = -1
    else:  # 工作日盘后
        offset = 0

    target = now + timedelta(days=offset)
    return target.strftime('%Y%m%d')


def run_timing_scan(qualified_path: str = None, trade_date: str = None,
                    top_n: int = 50):
    """主入口"""
    # ── 1. 加载股池 ──
    qualified = load_qualified(qualified_path)
    if trade_date is None:
        trade_date = _resolve_trade_date(trade_date)
        logger.info(f"未指定日期，自动解析为上一个交易日: {trade_date}")

    # ── 2. 拉取日线 ──
    daily = fetch_daily_data(qualified, trade_date, days=120)
    if daily is None or len(daily) == 0:
        logger.error("无日线数据")
        return

    # ── 2.2 拉取大盘指数数据 ──
    index_df = fetch_index_daily(trade_date, days=120)
    index_pct = get_index_pct_chg(index_df, trade_date)
    if index_pct is not None:
        logger.info(f"大盘指数(上证)当日涨跌幅: {index_pct:+.2f}%")

    # ── 2.5 加载最优参数 ──
    params = load_timing_params()
    min_bull_score = params.get('min_bull_score_strong', 65)

    # ── 3. 逐只评分 ──
    logger.info("计算择时信号...")
    results = []
    for _, row in qualified.iterrows():
        code = str(row['code']).strip().zfill(6)
        if code.startswith('6'):
            ts = f"{code}.SH"
        else:
            ts = f"{code}.SZ"
        stock_df = daily[daily['ts_code'] == ts]
        if len(stock_df) < 20:
            continue
        bull_score = round(row.get('最终分', 0), 1) if not pd.isna(row.get('最终分', 0)) else 0
        r = score_stock(ts, stock_df, params, index_pct_chg=index_pct)
        r['name'] = row.get('name', '')
        r['theme'] = row.get('theme', '')
        r['final_score'] = bull_score
        # Bull分二次过滤：强烈信号需Bull分≥min_bull_score
        if r['signal_level'] == '强烈' and bull_score < min_bull_score:
            r['signal_level'] = '中等'
            r['suggestion'] = f"可适度关注（Bull分{bull_score:.1f}低于{min_bull_score}）"
        results.append(r)

    results.sort(key=lambda x: x['composite_score'], reverse=True)

    # 综合排名分 = 择时分*0.6 + Bull分*0.4（用于配额排序，结合择时信号和基本面质量）
    for r in results:
        r['rank_score'] = round(r['composite_score'] * 0.6 + r['final_score'] * 0.4, 1)

    # 按综合排名分重新排序后再做配额限制
    results.sort(key=lambda x: x['rank_score'], reverse=True)

    # Top-N配额限制：每日强烈信号不超过max_strong_per_day个
    max_strong = params.get('max_strong_per_day', 15)
    strong_count = 0
    for r in results:
        if r['signal_level'] == '强烈':
            strong_count += 1
            if strong_count > max_strong:
                r['signal_level'] = '中等'
                r['suggestion'] = "可适度关注（当日配额已满）"

    # 最终按综合排名分排序输出
    results.sort(key=lambda x: x['rank_score'], reverse=True)

    # ── 4. 打印报告 ──
    _print_report(results, top_n)

    # ── 5. 保存结果（全量，按排名分降序） ──
    out_dir = os.path.join(os.path.dirname(__file__), 'report_daily')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'daily_timing_{trade_date}.csv')
    out_df = pd.DataFrame(results)
    out_df['ts_code'] = out_df['ts_code'].str.replace(r'\.(SH|SZ|BJ)', '', regex=True)
    # 列顺序整理
    cols = ['ts_code', 'name', 'theme', 'final_score', 'trend_score', 'dip_score',
            'breakout_score', 'composite_score', 'rank_score', 'signal_type',
            'signal_level', 'signals', 'suggestion']
    cols = [c for c in cols if c in out_df.columns]
    out_df = out_df[cols]
    out_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"择时结果已保存: {out_path} (共{len(out_df)}只)")

    # ── 6. 保存强烈信号清单 ──
    strong_df = out_df[out_df['signal_level'] == '强烈'].copy()
    strong_path = os.path.join(out_dir, f'daily_timing_strong_{trade_date}.csv')
    strong_df.to_csv(strong_path, index=False, encoding='utf-8-sig')
    logger.info(f"强烈信号清单已保存: {strong_path} (共{len(strong_df)}只)")

    # ── 7. 黄金梯队过滤 + 保存 ──
    full_with_tier, golden_df = filter_golden_tier(out_df, params)
    # 7.1 全量结果（含 tier/buy_value_score/final_suggestion）覆盖保存
    full_with_tier.to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"择时结果(含梯队)已覆盖: {out_path}")
    # 7.2 黄金梯队清单
    golden_path = os.path.join(out_dir, f'daily_timing_golden_{trade_date}.csv')
    golden_df.to_csv(golden_path, index=False, encoding='utf-8-sig')
    logger.info(f"黄金梯队清单已保存: {golden_path} (共{len(golden_df)}只)")

    # ── 8. 打印黄金梯队报告 ──
    _print_golden_report(golden_df, full_with_tier)


def _print_report(results: list, top_n: int):
    """打印择时报告"""
    strong = [r for r in results if r['signal_level'] == '强烈']
    medium = [r for r in results if r['signal_level'] == '中等']

    print()
    print("━" * 100)
    print("  择时信号矩阵 — 综合评分排名")
    print("━" * 100)
    header = f"  {'序号':>3} {'代码':>8} {'名称':>10} {'主题':>14} {'Bull分':>6} {'综合分':>6} {'排名分':>6} {'趋势':>6} {'低吸':>6} {'突破':>6} {'类型':>8} {'信号等级':>8}  操作建议"
    print(header)
    print("─" * 110)

    for i, r in enumerate(results[:top_n], 1):
        code = r['ts_code'].rsplit('.', 1)[0]
        theme_str = str(r.get('theme', '') or '')[:12]
        name_str = str(r.get('name', '') or '')[:8]
        print(f"  {i:>3} {code:>8} {name_str:>10} "
              f"{theme_str:>14} "
              f"{r['final_score']:>6.1f} {r['composite_score']:>6.1f} {r.get('rank_score',0):>6.1f} "
              f"{r['trend_score']:>6.1f} {r['dip_score']:>6.1f} "
              f"{r['breakout_score']:>6.1f} "
              f"{r['signal_type']:>8} {r['signal_level']:>8}  {r['suggestion']}")

    print("─" * 110)
    print(f"  共{len(results)}只 | 强烈{len(strong)}只 | 中等{len(medium)}只 | "
          f"观望{len(results)-len(strong)-len(medium)}只")
    print(f"  排名分 = 综合分×0.6 + Bull分×0.4")

    # 强烈信号详情
    if strong:
        print()
        print("━" * 100)
        print("  ★ 强烈信号标的 — 详细信号")
        print("━" * 100)
        for r in strong:
            code = r['ts_code'].rsplit('.', 1)[0]
            sigs = ', '.join(r['signals'][:4])
            print(f"  {code} {r.get('name','')} | {r['suggestion']} | {sigs}")

    print()


def _print_golden_report(golden_df: pd.DataFrame, full_df: pd.DataFrame):
    """打印黄金梯队报告"""
    if golden_df is None or len(golden_df) == 0:
        print()
        print("━" * 100)
        print("  💎 黄金梯队报告 — 无符合条件标的")
        print("━" * 100)
        print()
        return

    # 统计各梯队数量
    tier_counts = full_df['tier'].value_counts()
    n_super = tier_counts.get('💎 超级黄金(低吸+突破共振)', 0)
    n_dip = tier_counts.get('🥇 黄金梯队(低吸接棒)', 0)
    n_brk = tier_counts.get('🥇 黄金梯队(强力突破)', 0)
    n_chase = tier_counts.get('👀 观望/持股(无买点)', 0)

    print()
    print("━" * 110)
    print("  💎 黄金梯队报告 — 趋势+买点双共振标的")
    print("━" * 110)
    print(f"  💎 超级黄金: {n_super}只 | 🥇 低吸接棒: {n_dip}只 | 🥇 强力突破: {n_brk}只 | 👀 防追高降级: {n_chase}只")
    print("─" * 110)
    header = f"  {'序号':>3} {'代码':>8} {'名称':>10} {'主题':>14} {'Bull分':>6} {'趋势':>6} {'低吸':>6} {'突破':>6} {'排名分':>6} {'性价比':>6}  梯队属性 / 操作建议"
    print(header)
    print("─" * 110)

    for i, (_, r) in enumerate(golden_df.iterrows(), 1):
        code = str(r.get('ts_code', ''))[:6]
        theme_str = str(r.get('theme', '') or '')[:12]
        name_str = str(r.get('name', '') or '')[:8]
        tier = str(r.get('tier', ''))
        final_sug = str(r.get('final_suggestion', ''))
        print(f"  {i:>3} {code:>8} {name_str:>10} "
              f"{theme_str:>14} "
              f"{float(r.get('final_score', 0)):>6.1f} "
              f"{float(r.get('trend_score', 0)):>6.1f} "
              f"{float(r.get('dip_score', 0)):>6.1f} "
              f"{float(r.get('breakout_score', 0)):>6.1f} "
              f"{float(r.get('rank_score', 0)):>6.1f} "
              f"{float(r.get('buy_value_score', 0)):>6.1f}  "
              f"{tier} / {final_sug}")

    print("─" * 110)
    print(f"  性价比 = 低吸分×0.6 + 突破分×0.4 | 黄金门槛: 趋势≥90 + (低吸≥25 或 突破≥30)")
    print()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='每日择时信号扫描')
    parser.add_argument('--date', type=str, default=None, help='交易日 YYYYMMDD')
    parser.add_argument('--top', type=int, default=50, help='显示前N只')
    parser.add_argument('--input', type=str, default=None, help='输入股池路径')
    args = parser.parse_args()
    run_timing_scan(args.input, args.date, args.top)
