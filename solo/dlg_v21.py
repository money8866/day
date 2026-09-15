# -*- coding: utf-8 -*-
"""
DLG Picker V2.1 —— 红利安全垫 × 成长 × 趋势参与（三支柱）
================================================================
V1 是「高股息 + 低估值 + 高成长」的静态基本面横截面筛选；
V2.1 的核心修正：**股息是安全垫，不是目标**。真正要买的是
「便宜且能持续赚钱、且市场开始愿意给钱」的公司。

四条核心原则
  ① 股息是安全垫，不是目标 —— 股息率 ≥3% 即具备缓冲，但光有股息不构成买入理由
  ② 合理估值 + 可持续成长 —— 必须区分「周期陷阱(CYCLE_TRAP)」与「股息成长(DIVIDEND_GROWTH)」
  ③ 不因股价上涨而机械禁止买入 —— 趋势本身就是信息，用双通道解决"贵"与"强"的矛盾
  ④ 双通道：价值通道(VALUE)看便宜与分红，趋势通道(TREND)看强度与确认

评分模型（三支柱，均归一化 0~100）
  DLG_V21 = w_DP×下行保护(DP) + w_GQ×成长质量(GQ) + w_UP×上行参与(UP)
  权重随市场状态自适应（五态）：
     STRONG_BULL  DP 0.25 / GQ 0.35 / UP 0.40
     BULL         DP 0.30 / GQ 0.35 / UP 0.35
     NEUTRAL      DP 0.35 / GQ 0.35 / UP 0.30   ← 基准
     WEAK         DP 0.45 / GQ 0.35 / UP 0.20
     BEAR         DP 0.55 / GQ 0.35 / UP 0.10

三类标签
  优先级：DIVIDEND_GROWTH > DIVIDEND_TREND > RED_DIVIDEND > CYCLE_DIVIDEND
  避雷：  CYCLE_TRAP（高股息伪装下的周期顶部 / 业绩下滑 / 一次性分红）
  决策：  VALUE_BUY / TREND_BUY / WAIT / AVOID

股息率分区间处理
  ≥3% 为门槛；3~4% 正常；4~6% 优秀；>6% 进入「核查区」——
  必须排查 ①股价崩塌 ②业绩下滑 ③一次性分红 ④周期顶部，任一项命中即降级。

数据源（全部复用项目既有缓存，缺失才按需补接口）
  ① 估值/股息/市值/换手  daily_basic_cache
  ② 行情/成交额          daily_cache
  ③ 成长与质量(多期)     fina_indicator_cache
  ④ 扣非同比/单季加速    cache_daily/fin_ind_{tag}_full.parquet
  ⑤ 股息率历史持续性     daily_basic_cache 逐日 dv_ttm 回看 3 年
  ⑥ 趋势/均线/位置       daily_cache 批量自算（不依赖 stk_factor_pro 宽表）
  ⑦ 市场状态             index_daily_cache :: 000001.SH
  ⑧ 行业与上市日         cache_daily/stock_basic.csv

无未来函数：行情仅用 trade_date ≤ scan_date，财报仅用 ann_date ≤ scan_date。

用法:
  python dlg_v21.py                              # 默认取有效交易日
  python dlg_v21.py --date 20260914 --top 30
  python dlg_v21.py --no-api                     # 纯本地缓存
  python dlg_v21.py --no-pdf --min-yield 3.5
"""
import os
import sys
import time
import sqlite3
import argparse

import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)

from stock_cache import get_effective_date, DB_PATH  # noqa: E402
from dlg_picker import (  # noqa: E402
    load_market_snapshot, load_fin_history, load_fin_full,
    load_div_history, load_industry, markdown_to_pdf,
    _rank_score, _fv, FINANCIAL_INDUSTRIES, OUT_DIR,
)

# ═══════════════════════════════════════════════════════
# 参数配置
# ═══════════════════════════════════════════════════════
DLG_V21_CONFIG = {
    # ── 硬门槛 ──
    'min_dividend_yield': 3.0,       # 股息率下限 %（安全垫门槛）
    'max_dividend_yield': 18.0,      # 上限仅剔极端异常，>6% 走核查区而非直接剔除
    'inspect_dividend_yield': 6.0,   # 股息率核查区起点 %
    'min_div_persist_ratio': 0.5,    # 近3年分红未中断占比下限
    'div_history_years': 3,
    'min_pe_ttm': 0.0,
    'max_pe_ttm': 45.0,              # V2.1 放宽（趋势通道需要给成长溢价空间）
    'max_pb': 10.0,
    'min_netprofit_yoy': 0.0,        # V2.1 放宽到 0（由 GQ 支柱细分优劣）
    'min_roe': 5.0,
    'min_gross_margin': 8.0,
    'max_debt_to_assets': 80.0,
    'min_total_mv_yi': 60.0,
    'min_amount_yi': 0.5,            # V2.1 提高流动性要求（趋势通道需要可交易）
    'min_list_days': 250,
    'require_latest_period': True,
    'exclude_st': True,
    'exclude_bj': True,
    'exclude_financial': True,

    # ── 三支柱基准权重（NEUTRAL）──
    'w_dp': 0.35,                    # 下行保护 Downside Protection
    'w_gq': 0.35,                    # 成长质量 Growth Quality
    'w_up': 0.30,                    # 上行参与 Upside Participation

    # ── 支柱内部权重：DP ──
    'dp_div': 0.40,                  # 股息安全垫（当期+持续性）
    'dp_val': 0.30,                  # 估值安全边际（行业分位+个股历史分位）
    'dp_cash': 0.18,                 # 现金流质量
    'dp_balance': 0.12,              # 资产质量
    'dp_div_current': 0.55,          # DP 股息内：当期水平
    'dp_div_persist': 0.45,          # DP 股息内：历史持续性

    # ── 支柱内部权重：GQ ──
    'gq_growth': 0.32,               # 增长强度（净利+营收同比）
    'gq_accel': 0.18,                # 增长加速（单季环比加速）
    'gq_roe': 0.26,                  # 资本回报（ROE 水平+趋势）
    'gq_margin': 0.12,               # 盈利结构（毛利率）
    'gq_quality': 0.12,              # 盈利含金量（扣非/现金流匹配）

    # ── 支柱内部权重：UP ──
    'up_trend': 0.32,                # 趋势结构（均线排列+斜率）
    'up_rs': 0.26,                   # 相对强度（横截面 RS 分位）
    'up_position': 0.20,             # 位置（不追高：距高点/距均线）
    'up_volume': 0.12,               # 量能配合
    'up_industry': 0.10,             # 行业强度

    # ── 市场状态权重（五态自适应）──
    'regime_weights': {
        'STRONG_BULL': {'w_dp': 0.25, 'w_gq': 0.35, 'w_up': 0.40},
        'BULL':        {'w_dp': 0.30, 'w_gq': 0.35, 'w_up': 0.35},
        'NEUTRAL':     {'w_dp': 0.35, 'w_gq': 0.35, 'w_up': 0.30},
        'WEAK':        {'w_dp': 0.45, 'w_gq': 0.35, 'w_up': 0.20},
        'BEAR':        {'w_dp': 0.55, 'w_gq': 0.35, 'w_up': 0.10},
    },
    # 牛市放宽规则：允许新高/站上MA20/突破后回踩，但需同时满足质量条件
    'bull_relax': {
        'min_dividend_yield': 3.0,
        'min_earnings_growth': 15.0,
        'min_roe': 10.0,
        'min_rs': 50.0,
        'require_ma20_above_ma60': True,
    },

    # ── 分类门槛 ──
    'cls_growth_npy': 15.0,          # DIVIDEND_GROWTH：净利同比下限
    'cls_growth_roe': 10.0,          # DIVIDEND_GROWTH：ROE 下限
    'cls_trend_up': 65.0,            # DIVIDEND_TREND：UP 支柱下限
    'cls_cycle_yield': 5.0,          # CYCLE_DIVIDEND：高股息起点
    'trap_yield': 4.5,               # CYCLE_TRAP 触发所需的最低股息率
    'trap_payout': 0.9,              # 分红支付率上限（≈股息率×PE），超过视为不可持续
    'trap_div_trend': 0.75,          # 分红趋势低于此值疑分红能力衰减

    # ── 决策门槛 ──
    'buy_score': 70.0,               # 决策基础分门槛
    'value_buy_dp': 70.0,            # VALUE_BUY：DP 支柱下限
    'value_buy_pe_pct': 45.0,        # VALUE_BUY：PE 历史分位上限（越低越便宜）
    'trend_buy_up': 70.0,            # TREND_BUY：UP 支柱下限
    'trend_buy_rs': 60.0,            # TREND_BUY：RS 分位下限

    # ── 其它 ──
    'min_industry_n': 10,
    'trend_lookback': 320,           # 趋势面板回看交易日数（约 15 个月）
    'val_lookback_days': 1000,       # 估值历史分位回看自然日数
    'top_n': 30,
    'make_pdf': True,
}

# 周期属性行业（用于 CYCLE_TRAP / CYCLE_DIVIDEND 判定）
# 名称严格对齐 stock_basic.csv 的行业口径（共 110 个行业名），避免简称不匹配导致漏判。
# 不含水务/电力/公路/铁路/路桥/港口/机场/燃气等稳定基础设施——它们高股息是常态而非陷阱。
CYCLICAL_INDUSTRIES = {
    # 资源与中游材料
    '普钢', '特种钢', '钢加工', '焦炭加工', '煤炭开采', '石油开采', '石油加工', '石油贸易',
    '铜', '铝', '铅锌', '小金属', '黄金', '矿物制品',
    '化工原料', '化纤', '染料涂料', '橡胶', '塑料', '玻璃', '水泥', '陶瓷', '其他建材', '造纸',
    # 地产建筑链
    '全国地产', '区域地产', '园区开发', '房产服务', '建筑工程', '装修装饰', '家居用品',
    # 设备与制造周期
    '工程机械', '机床制造', '化工机械', '轻工机械', '农用机械', '机械基件',
    '船舶', '运输设备', '汽车整车', '汽车配件', '汽车服务', '摩托车', '电器仪表',
    # 运输周期
    '水运', '空运', '仓储物流',
    # 消费周期
    '纺织', '服饰', '商品城', '商贸代理', '批发业', '其他商业',
    '旅游景点', '旅游服务', '酒店餐饮',
    # 农业周期
    '农药化肥', '农业综合', '种植业', '饲料', '渔业', '林业',
}

# V2.1 绝对阈值 → 分数映射表
_MAP21 = {
    'dividend':   ([-1, 1, 2, 3, 4, 5, 6, 8], [0, 10, 30, 50, 68, 82, 92, 100]),
    'div_trend':  ([0, 0.4, 0.7, 1.0, 1.3, 2.0], [0, 25, 55, 75, 90, 100]),
    'div_floor':  ([0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0], [0, 20, 40, 60, 78, 92, 100]),
    'netprofit_yoy': ([-30, -10, 0, 10, 20, 30, 50, 100], [0, 0, 20, 50, 70, 85, 95, 100]),
    'or_yoy':     ([-30, -10, 0, 5, 10, 20, 30, 50], [0, 10, 25, 45, 60, 80, 90, 100]),
    'accel':      ([-40, -20, -5, 0, 10, 20, 40], [0, 10, 35, 55, 75, 90, 100]),
    'mean4':      ([-40, -10, 0, 10, 20, 30], [0, 10, 40, 65, 85, 100]),
    'roe':        ([-5, 0, 5, 8, 12, 15, 20, 30], [0, 10, 35, 50, 70, 85, 95, 100]),
    'roe_trend':  ([-0.5, -0.2, -0.05, 0, 0.05, 0.15, 0.3], [0, 10, 35, 50, 68, 85, 100]),
    'gross_margin': ([0, 5, 10, 20, 30, 40, 60], [0, 15, 35, 60, 80, 92, 100]),
    'debt':       ([0, 20, 30, 40, 50, 60, 70, 85], [100, 100, 96, 86, 70, 50, 30, 5]),
    'ocf_to_or':  ([0, 2, 5, 10, 15, 20, 30], [0, 25, 50, 72, 85, 92, 100]),
    'cash_quality': ([0, 0.3, 0.6, 0.9, 1.2, 1.8], [0, 20, 50, 75, 90, 100]),
    # 估值分位：分位越低越便宜 → 分越高（反向映射）
    'pe_pct':     ([0, 10, 20, 30, 40, 60, 80, 100], [100, 95, 88, 78, 66, 45, 25, 5]),
    'pb_pct':     ([0, 10, 20, 30, 40, 60, 80, 100], [100, 95, 88, 78, 66, 45, 25, 5]),
    # 趋势结构：均线多头排列档位
    'ma_align':   ([0, 1, 2, 3], [20, 45, 72, 95]),
    # 距 60 日高点（负值＝回撤幅度）：越接近高点分越高，但不奖励追高
    'dist_hi60':  ([-0.5, -0.35, -0.25, -0.15, -0.08, -0.03, 0, 0.05], [10, 25, 45, 62, 80, 92, 100, 100]),
    # 量能比（5日均量/20日均量）
    'vol_ratio':  ([0.4, 0.7, 1.0, 1.3, 1.6, 2.2, 3.0], [25, 45, 65, 80, 90, 96, 100]),
    # 距 MA20 偏离度（%）：温和偏离最优，过度偏离扣分
    'ma20_dev':   ([-15, -8, -3, 0, 3, 6, 10, 18], [15, 35, 55, 68, 80, 85, 72, 45]),
    'ret20':      ([-0.25, -0.15, -0.08, -0.03, 0, 0.05, 0.12, 0.25], [0, 15, 32, 48, 55, 68, 82, 95]),
    'ma20_slope': ([-0.08, -0.04, -0.01, 0, 0.01, 0.03, 0.06], [0, 15, 40, 55, 72, 88, 100]),
}


def _score21(value, key):
    """按 V2.1 映射表把绝对值线性插值为 0~100 分（支持标量与 Series，缺失列返回中性 50）"""
    x, y = _MAP21[key]
    if value is None:
        if isinstance(value, pd.Series):
            return pd.Series(50.0, index=value.index)
        return np.float64(50.0)
    v = np.asarray(pd.to_numeric(value, errors='coerce'), dtype=float)
    out = np.interp(np.nan_to_num(v, nan=x[0]), x, y)
    return out


def _col(d, name):
    """取列（缺失返回 None），避免 d.get 在缺列时返回 None 导致后续报错"""
    return d[name] if name in d.columns else None


def _numcol(d, name):
    """取数值列（缺失返回全 NaN Series），保证缺列时下游逻辑仍可运行"""
    if name in d.columns:
        return pd.to_numeric(d[name], errors='coerce')
    return pd.Series(np.nan, index=d.index, dtype=float)


# ═══════════════════════════════════════════════════════
# 市场状态引擎（五态）
# ═══════════════════════════════════════════════════════
def load_index_series(ts_code='000001.SH', end_date=''):
    """指数日线（index_daily_cache 优先，回退 TDX .day），仅取 <= end_date"""
    df = None
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            df = pd.read_sql_query(
                'SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? ORDER BY trade_date',
                con, params=(str(ts_code),))
        finally:
            con.close()
    except Exception:
        df = None
    if df is None or df.empty:
        try:
            from bts.data import parse_tdx_day_file
            from bts.config import TDX_PATH
            f = os.path.join(TDX_PATH, 'vipdoc', 'sh', 'lday',
                             'sh999999.day' if ts_code == '000001.SH' else 'sh000001.day')
            df = parse_tdx_day_file(f)
            if df is not None and not df.empty:
                df = df[['trade_date', 'close']]
        except Exception:
            return None
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    if end_date:
        df = df[df['trade_date'] <= str(end_date)]
    return df.sort_values('trade_date').reset_index(drop=True)


def market_state_v21(date, idx=None):
    """五态市场判定：STRONG_BULL / BULL / NEUTRAL / WEAK / BEAR

    依据：指数与 MA20/MA60/MA120 的位置关系 + 20日/60日涨幅。
    仅使用 <= date 的指数数据，无未来函数。
    """
    if idx is None:
        idx = load_index_series('000001.SH', date)
    if idx is None or len(idx) < 130:
        return 'NEUTRAL', {}
    c = idx['close'].reset_index(drop=True)
    now = float(c.iloc[-1])
    ma20 = float(c.rolling(20).mean().iloc[-1])
    ma60 = float(c.rolling(60).mean().iloc[-1])
    ma120 = float(c.rolling(120).mean().iloc[-1])
    ret20 = now / float(c.iloc[-21]) - 1
    ret60 = now / float(c.iloc[-61]) - 1
    ma20_prev = float(c.rolling(20).mean().iloc[-6])
    ma20_slope = ma20 / ma20_prev - 1 if ma20_prev else 0.0

    if now > ma20 > ma60 > ma120 and ret20 > 0.05:
        st = 'STRONG_BULL'
    elif now < ma20 and now < ma60 and ret20 < -0.05:
        st = 'BEAR'
    elif now > ma20 and ma20 > ma60 and ret20 > 0.0:
        st = 'BULL'
    elif now < ma20 * 0.99 or ret20 < -0.02:
        st = 'WEAK'
    else:
        st = 'NEUTRAL'
    detail = {
        'close': now, 'ma20': ma20, 'ma60': ma60, 'ma120': ma120,
        'ret20': ret20, 'ret60': ret60, 'ma20_slope': ma20_slope,
        'above_ma20': now > ma20, 'ma20_above_ma60': ma20 > ma60,
        'ma60_above_ma120': ma60 > ma120,
    }
    return st, detail


def regime_weights(state, cfg):
    """按市场状态取三支柱权重"""
    w = cfg['regime_weights'].get(state, cfg['regime_weights']['NEUTRAL'])
    return dict(w)


# ═══════════════════════════════════════════════════════
# 趋势引擎
# ═══════════════════════════════════════════════════════
def load_price_panel(date, codes, lookback=320):
    """批量读取候选股日线并计算趋势指标（仅用 trade_date <= date 的 K 线）

    返回每只股票一行的面板：均线、均线排列、斜率、区间涨幅、距高点、
    量能比、距 MA20 偏离等。不依赖 stk_factor_pro 宽表。
    """
    if not codes:
        return pd.DataFrame()
    cols = ['ts_code', 'trade_date', 'high', 'low', 'close', 'vol']
    frames = []
    con = sqlite3.connect(DB_PATH)
    try:
        for i in range(0, len(codes), 400):
            chunk = [str(c) for c in codes[i:i + 400]]
            ph = ','.join('?' * len(chunk))
            q = (f'SELECT {", ".join(cols)} FROM daily_cache '
                 f'WHERE trade_date <= ? AND ts_code IN ({ph}) '
                 f'ORDER BY ts_code, trade_date')
            frames.append(pd.read_sql_query(q, con, params=[str(date)] + chunk))
    finally:
        con.close()
    if not frames:
        return pd.DataFrame()
    px = pd.concat(frames, ignore_index=True)
    if px.empty:
        return pd.DataFrame()
    px['trade_date'] = px['trade_date'].astype(str)
    for c in ('high', 'low', 'close', 'vol'):
        px[c] = pd.to_numeric(px[c], errors='coerce')

    rows = []
    for code, g in px.groupby('ts_code', sort=False):
        g = g.tail(int(lookback))
        c = g['close'].to_numpy(dtype=float)
        h = g['high'].to_numpy(dtype=float)
        v = g['vol'].to_numpy(dtype=float)
        n = len(c)
        if n < 25:
            continue
        last = c[-1]

        def _ma(k):
            return float(np.mean(c[-k:])) if n >= k else np.nan

        ma5, ma10, ma20, ma60, ma120 = _ma(5), _ma(10), _ma(20), _ma(60), _ma(120)
        # 均线多头排列档位：0 空头 / 1 短多(MA5>MA20) / 2 中多(+MA20>MA60) / 3 全多(+MA20>MA120)
        align = 0
        if not np.isnan(ma5) and not np.isnan(ma20) and ma5 > ma20:
            align = 1
            if not np.isnan(ma60) and ma20 > ma60:
                align = 2
                if not np.isnan(ma120) and ma20 > ma120:
                    align = 3
        ma20_prev = float(np.mean(c[-25:-5])) if n >= 25 else np.nan
        ma20_slope = (ma20 / ma20_prev - 1) if (ma20_prev and not np.isnan(ma20)) else np.nan
        ret20 = last / c[-21] - 1 if n >= 21 else np.nan
        ret60 = last / c[-61] - 1 if n >= 61 else np.nan
        hi60 = float(np.max(h[-60:])) if n >= 60 else float(np.max(h))
        lo60 = float(np.min(c[-60:])) if n >= 60 else float(np.min(c))
        dist_hi60 = last / hi60 - 1 if hi60 else np.nan
        pos_in_range = (last - lo60) / (hi60 - lo60) if hi60 > lo60 else np.nan
        vol5 = float(np.mean(v[-5:])) if n >= 5 else np.nan
        vol20 = float(np.mean(v[-20:])) if n >= 20 else np.nan
        vol_ratio = vol5 / vol20 if vol20 else np.nan
        ma20_dev = (last / ma20 - 1) * 100 if ma20 else np.nan
        new_hi60 = bool(n >= 60 and last >= hi60 * 0.999)
        new_hi120 = bool(n >= 120 and last >= float(np.max(h[-120:])) * 0.999)

        rows.append({
            'ts_code': code, 'close_t': last,
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60, 'ma120': ma120,
            'ma_align': align, 'ma20_slope': ma20_slope,
            'above_ma20': bool(ma20 and last > ma20),
            'ma20_above_ma60': bool(ma20 and ma60 and ma20 > ma60),
            'ma60_above_ma120': bool(ma60 and ma120 and ma60 > ma120),
            'ret20': ret20, 'ret60': ret60,
            'dist_hi60': dist_hi60, 'pos_in_range60': pos_in_range,
            'vol_ratio': vol_ratio, 'ma20_dev': ma20_dev,
            'new_hi60': new_hi60, 'new_hi120': new_hi120,
            'bars': n,
        })
    return pd.DataFrame(rows)


def load_val_history(date, codes, lookback_days=1000):
    """个股估值历史分位：PE/PB 在过去 N 自然日内的分位（越低越便宜）

    数据源 daily_basic_cache，仅用 trade_date <= date。
    """
    empty = pd.DataFrame(columns=['ts_code', 'pe_pct_hist', 'pb_pct_hist', 'dv_pct_hist'])
    if not codes:
        return empty
    dt = pd.to_datetime(str(date), format='%Y%m%d')
    start = (dt - pd.Timedelta(days=int(lookback_days))).strftime('%Y%m%d')
    frames = []
    con = sqlite3.connect(DB_PATH)
    try:
        for i in range(0, len(codes), 400):
            chunk = [str(c) for c in codes[i:i + 400]]
            ph = ','.join('?' * len(chunk))
            q = (f'SELECT ts_code, trade_date, pe_ttm, pb, dv_ttm FROM daily_basic_cache '
                 f'WHERE trade_date <= ? AND trade_date >= ? AND ts_code IN ({ph})')
            frames.append(pd.read_sql_query(q, con, params=[str(date), start] + chunk))
    finally:
        con.close()
    if not frames:
        return empty
    h = pd.concat(frames, ignore_index=True)
    if h.empty:
        return empty
    for c in ('pe_ttm', 'pb', 'dv_ttm'):
        h[c] = pd.to_numeric(h[c], errors='coerce')
    h = h.sort_values(['ts_code', 'trade_date'])

    def _pct(s):
        s = s.dropna()
        if len(s) < 60:
            return np.nan
        cur = s.iloc[-1]
        return float((s <= cur).mean() * 100)

    g = h.groupby('ts_code')
    out = pd.DataFrame({
        'pe_pct_hist': g['pe_ttm'].apply(lambda s: _pct(s[s > 0])),
        'pb_pct_hist': g['pb'].apply(lambda s: _pct(s[s > 0])),
        'dv_pct_hist': g['dv_ttm'].apply(_pct),
    }).reset_index()
    return out


def load_roe_history(date, codes):
    """近 4 期 ROE 均值（用于 ROE 趋势：最新期 − 近4期均值）

    数据源 fina_indicator_cache，仅用 ann_date <= date 的记录。
    """
    empty = pd.DataFrame(columns=['ts_code', 'roe_mean4', 'roe_min4'])
    if not codes:
        return empty
    con = sqlite3.connect(DB_PATH)
    try:
        frames = []
        for i in range(0, len(codes), 500):
            chunk = [str(c) for c in codes[i:i + 500]]
            ph = ','.join('?' * len(chunk))
            q = (f'SELECT ts_code, end_date, roe FROM fina_indicator_cache '
                 f'WHERE ann_date <= ? AND ts_code IN ({ph})')
            frames.append(pd.read_sql_query(q, con, params=[str(date)] + chunk))
    finally:
        con.close()
    if not frames:
        return empty
    h = pd.concat(frames, ignore_index=True)
    if h.empty:
        return empty
    h['roe'] = pd.to_numeric(h['roe'], errors='coerce')
    h['end_date'] = h['end_date'].astype(str)
    h = h.sort_values(['ts_code', 'end_date'], ascending=[True, False])
    h['_rk'] = h.groupby('ts_code').cumcount()
    h4 = h[h['_rk'] < 4].groupby('ts_code')['roe']
    return pd.DataFrame({
        'roe_mean4': h4.mean(),
        'roe_min4': h4.min(),
    }).reset_index()


# ═══════════════════════════════════════════════════════
# 门槛过滤（V2.1）
# ═══════════════════════════════════════════════════════
def apply_gates_v21(df, cfg, date):
    """V2.1 门槛：股息率≥3% 为安全垫门槛，其余为质量与流动性底线"""
    funnel = []

    def _step(name, mask):
        before = len(df)
        funnel.append((name, before, int(mask.sum()), before - int(mask.sum())))
        return df[mask]

    df = _step('基础池(当日有估值快照)', df['ts_code'].notna())
    df = _step(f'股息率 dv_ttm ≥ {cfg["min_dividend_yield"]}%（安全垫门槛）',
               df['dv_ttm'].fillna(0) >= cfg['min_dividend_yield'])
    df = _step(f'股息率 ≤ {cfg["max_dividend_yield"]}%（剔极端异常）',
               df['dv_ttm'].fillna(0) <= cfg['max_dividend_yield'])
    if cfg['min_div_persist_ratio'] > 0:
        df = _step(f'近{cfg["div_history_years"]}年分红未中断(占比≥{cfg["min_div_persist_ratio"]:.0%})',
                   df['dv_pos_ratio'].fillna(0) >= cfg['min_div_persist_ratio'])
    pe = pd.to_numeric(df['pe_ttm'], errors='coerce')
    df = _step(f'{cfg["min_pe_ttm"]} < PE(TTM) ≤ {cfg["max_pe_ttm"]}',
               (pe > cfg['min_pe_ttm']) & (pe <= cfg['max_pe_ttm']))
    pb = pd.to_numeric(df['pb'], errors='coerce')
    df = _step(f'PB ≤ {cfg["max_pb"]} 且 > 0', (pb > 0) & (pb <= cfg['max_pb']))
    df = _step(f'总市值 ≥ {cfg["min_total_mv_yi"]}亿', df['total_mv_yi'] >= cfg['min_total_mv_yi'])
    df = _step(f'成交额 ≥ {cfg["min_amount_yi"]}亿（流动性）',
               df['amount_yi'].fillna(0) >= cfg['min_amount_yi'])
    if cfg['exclude_st']:
        df = _step('剔除 ST/*ST/退市', ~df['name'].fillna('').str.contains('ST|退', regex=True))
    if cfg['exclude_bj']:
        df = _step('剔除北交所', ~df['ts_code'].astype(str).str.endswith('.BJ'))
    if cfg['exclude_financial']:
        df = _step('剔除金融行业', ~df['industry'].fillna('').isin(FINANCIAL_INDUSTRIES))
    ld = pd.to_datetime(df['list_date'], format='%Y%m%d', errors='coerce')
    age = (pd.to_datetime(str(date), format='%Y%m%d') - ld).dt.days
    df = _step(f'上市满 {cfg["min_list_days"]} 天', age >= cfg['min_list_days'])
    df = _step(f'净利同比 ≥ {cfg["min_netprofit_yoy"]}%', df['npy_latest'] >= cfg['min_netprofit_yoy'])
    df = _step(f'ROE ≥ {cfg["min_roe"]}%', df['roe_latest'] >= cfg['min_roe'])
    df = _step(f'毛利率 ≥ {cfg["min_gross_margin"]}%',
               df['margin_latest'].fillna(0) >= cfg['min_gross_margin'])
    df = _step(f'资产负债率 ≤ {cfg["max_debt_to_assets"]}%',
               df['debt_latest'].fillna(999) <= cfg['max_debt_to_assets'])
    df = _step('具备趋势面板(近60日K线≥25根)', df['bars'].fillna(0) >= 25)
    if cfg['require_latest_period']:
        df = _step(f'已披露最新报告期({str(date)[:4]}年最新)', df['is_latest_period'].fillna(False))
    return df, funnel


# ═══════════════════════════════════════════════════════
# 三支柱打分
# ═══════════════════════════════════════════════════════
def score_pillars_v21(df, cfg, state):
    """DP / GQ / UP 三支柱打分 + regime 加权总分 + 分类 + 决策"""
    d = df.copy()
    for c in ('dv_ttm', 'pe_ttm', 'pb', 'ps', 'roe_latest', 'margin_latest',
              'debt_latest', 'npy_latest', 'or_yoy_latest', 'npy_mean4', 'npy_min4'):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors='coerce')

    # ── A. 下行保护 DP ──
    # A1 股息安全垫 = 当期水平 55% + 历史持续性 45%
    s_div_lvl = _score21(d['dv_ttm'], 'dividend')
    s_div_floor = _score21(_col(d, 'dv_min3'), 'div_floor')
    s_div_trend = _score21(_col(d, 'dv_trend'), 'div_trend')
    poss = pd.to_numeric(_col(d, 'dv_pos_ratio'), errors='coerce')
    poss = poss if poss is not None else pd.Series(np.nan, index=d.index)
    s_div_persist = 0.45 * s_div_floor + 0.35 * s_div_trend + 0.20 * (poss.fillna(0) * 100)
    d['S_DP_股息'] = (cfg['dp_div_current'] * s_div_lvl + cfg['dp_div_persist'] * s_div_persist).round(1)

    # A2 估值安全边际 = 行业中性分位 60% + 个股历史分位 40%
    s_val_ind = _rank_score(-d['pe_ttm'], higher_better=True, df_ref=d,
                            ind_col='industry', min_n=cfg['min_industry_n'])
    s_val_hist = 0.5 * pd.Series(_score21(_col(d, 'pe_pct_hist'), 'pe_pct'), index=d.index) + \
        0.5 * pd.Series(_score21(_col(d, 'pb_pct_hist'), 'pb_pct'), index=d.index)
    s_val_hist = s_val_hist.fillna(50.0)
    d['S_DP_估值'] = (0.60 * s_val_ind + 0.40 * s_val_hist).round(1)

    # A3 现金流质量：经营现金流/营收 与 现金流同比
    ocf_ratio = pd.to_numeric(_col(d, 'ocf_to_or_latest'), errors='coerce')
    ocf_yoy = pd.to_numeric(_col(d, 'ocf_yoy_latest'), errors='coerce')
    s_cash = 0.6 * (pd.Series(_score21(ocf_ratio, 'ocf_to_or'), index=d.index) if ocf_ratio is not None
                    else pd.Series(50.0, index=d.index)) + \
        0.4 * (pd.Series(_score21(ocf_yoy, 'mean4'), index=d.index) if ocf_yoy is not None
               else pd.Series(50.0, index=d.index))
    d['S_DP_现金流'] = pd.Series(s_cash, index=d.index).fillna(50.0).round(1)

    # A4 资产质量
    d['S_DP_资产'] = _score21(d['debt_latest'], 'debt').round(1)

    d['DP'] = (cfg['dp_div'] * d['S_DP_股息'] + cfg['dp_val'] * d['S_DP_估值'] +
               cfg['dp_cash'] * d['S_DP_现金流'] + cfg['dp_balance'] * d['S_DP_资产']).round(1)

    # ── B. 成长质量 GQ ──
    s_npy = _score21(d['npy_latest'], 'netprofit_yoy')
    s_or = _score21(_col(d, 'or_yoy_latest'), 'or_yoy')
    d['S_GQ_增长'] = (0.60 * s_npy + 0.40 * s_or).round(1)

    if 'dt_netprofit_yoy' in d.columns:
        s_dt = _score21(pd.to_numeric(d['dt_netprofit_yoy'], errors='coerce'), 'accel')
        d['S_GQ_加速'] = (0.5 * s_dt + 0.5 * _score21(d['npy_mean4'], 'mean4')).round(1)
    else:
        d['S_GQ_加速'] = pd.Series(_score21(d['npy_mean4'], 'mean4'), index=d.index).round(1)

    # ROE 水平 + 趋势（趋势＝最新期 ROE − 近 4 期均值，无多期数据时取中性 50）
    s_roe_lvl = _score21(d['roe_latest'], 'roe')
    if 'roe_mean4' in d.columns:
        rt = pd.to_numeric(d['roe_latest'], errors='coerce') - pd.to_numeric(d['roe_mean4'], errors='coerce')
        s_roe_trd = pd.Series(_score21(rt, 'roe_trend'), index=d.index).fillna(50.0)
    else:
        s_roe_trd = pd.Series(50.0, index=d.index)
    d['S_GQ_ROE'] = (0.70 * s_roe_lvl + 0.30 * s_roe_trd).round(1)

    d['S_GQ_毛利'] = _score21(d['margin_latest'], 'gross_margin').round(1)

    # 盈利含金量：扣非/净利 与 经营现金流匹配
    if 'dt_netprofit_yoy' in d.columns and 'npy_latest' in d.columns:
        npy = d['npy_latest']
        dt = pd.to_numeric(d['dt_netprofit_yoy'], errors='coerce')
        gap = (npy - dt).abs()
        s_qual = pd.Series(np.clip(100 - gap * 1.2, 0, 100), index=d.index)
        s_qual = s_qual.where(npy.notna() & dt.notna(), 55.0)
    else:
        s_qual = pd.Series(55.0, index=d.index)
    d['S_GQ_含金'] = s_qual.round(1)

    d['GQ'] = (cfg['gq_growth'] * d['S_GQ_增长'] + cfg['gq_accel'] * d['S_GQ_加速'] +
               cfg['gq_roe'] * d['S_GQ_ROE'] + cfg['gq_margin'] * d['S_GQ_毛利'] +
               cfg['gq_quality'] * d['S_GQ_含金']).round(1)

    # ── C. 上行参与 UP ──
    s_align = _score21(d['ma_align'].fillna(0), 'ma_align')
    s_slope = pd.Series(_score21(d['ma20_slope'], 'ma20_slope'), index=d.index).fillna(50.0)
    d['S_UP_趋势'] = (0.55 * s_align + 0.45 * s_slope).round(1)

    # RS 相对强度：20日涨幅的行业内/全市场分位
    d['S_UP_RS'] = _rank_score(d['ret20'], higher_better=True, df_ref=d,
                               ind_col='industry', min_n=cfg['min_industry_n']).round(1)

    # 位置：距 60 日高点 + 距 MA20 偏离（两者都不奖励过度追高）
    d['S_UP_位置'] = (0.6 * pd.Series(_score21(d['dist_hi60'], 'dist_hi60'), index=d.index) +
                      0.4 * pd.Series(_score21(d['ma20_dev'], 'ma20_dev'), index=d.index)).round(1)

    d['S_UP_量能'] = pd.Series(_score21(d['vol_ratio'], 'vol_ratio'), index=d.index).round(1)

    # 行业强度：行业内成员 20 日涨幅均值 → 横截面分位
    ind_ret = d.groupby('industry')['ret20'].transform('mean')
    d['S_UP_行业'] = _rank_score(ind_ret, higher_better=True, df_ref=d,
                                 ind_col='industry', min_n=cfg['min_industry_n']).round(1)

    d['UP'] = (cfg['up_trend'] * d['S_UP_趋势'] + cfg['up_rs'] * d['S_UP_RS'] +
               cfg['up_position'] * d['S_UP_位置'] + cfg['up_volume'] * d['S_UP_量能'] +
               cfg['up_industry'] * d['S_UP_行业']).round(1)

    # ── D. regime 加权总分 ──
    w = regime_weights(state, cfg)
    d['w_DP'], d['w_GQ'], d['w_UP'] = w['w_dp'], w['w_gq'], w['w_up']
    d['DLG21'] = (w['w_dp'] * d['DP'] + w['w_gq'] * d['GQ'] + w['w_up'] * d['UP']).round(1)

    return d


# ═══════════════════════════════════════════════════════
# 分类 / 风险核查 / 通道路由 / 决策
# ═══════════════════════════════════════════════════════
def classify_and_decide(d, cfg, state):
    """CYCLE_TRAP 核查 + 四类优先级分类 + 双通道路由 + 四种决策"""
    d = d.copy()
    dv = _numcol(d, 'dv_ttm')
    dv_mean3 = _numcol(d, 'dv_mean3')
    dv_trend = _numcol(d, 'dv_trend')
    npy = _numcol(d, 'npy_latest')
    dt_npy = _numcol(d, 'dt_netprofit_yoy')
    roe = _numcol(d, 'roe_latest')
    pe_pct = _numcol(d, 'pe_pct_hist')
    dist_hi60 = _numcol(d, 'dist_hi60')
    is_cyc = d['industry'].fillna('').isin(CYCLICAL_INDUSTRIES)

    # ── ① >6% 高股息核查区：四项排查 ──
    # 分红支付率 ≈ 股息率 × PE(TTM)，用于判断分红是否由盈利支撑
    pe_ttm = _numcol(d, 'pe_ttm')
    payout = dv * pe_ttm / 100.0
    d['分红支付率'] = payout.round(3)
    hi_div = dv >= cfg['inspect_dividend_yield']
    r_crash = hi_div & (dist_hi60 <= -0.35)                       # 股价崩塌（距60日高点回撤≥35%）
    r_decline = hi_div & (npy < 0)                                # 净利同比转负
    r_dt_decline = hi_div & (dt_npy < 0)                          # 扣非同比转负
    r_onetime = hi_div & (payout >= cfg['trap_payout'])            # 股息超过盈利：不可持续
    r_cycle_top = hi_div & is_cyc & (_numcol(d, 'margin_latest') >= 30) & \
        ((npy < 10) | (dt_npy < 0)) & (roe < 12)                  # 周期高毛利但增长停滞
    d['核查_股价崩塌'] = np.where(r_crash, '是', '')
    d['核查_净利下滑'] = np.where(r_decline, '是', '')
    d['核查_扣非下滑'] = np.where(r_dt_decline, '是', '')
    d['核查_一次性分红'] = np.where(r_onetime, '是', '')
    d['核查_周期顶部'] = np.where(r_cycle_top, '是', '')

    # ── ② CYCLE_TRAP：高股息伪装下的陷阱（信号计数，避免单一指标误伤）──
    sig_profit = (npy < 0)                                        # 净利实亏
    sig_dt = (dt_npy < 0)                                         # 扣非同比为负
    sig_dt_big = (dt_npy < -15)                                   # 扣非大幅下滑
    sig_divcut = (dv_trend < cfg['trap_div_trend'])               # 分红趋势衰减
    sig_drawdown = (dist_hi60 <= -0.30)                           # 明显回撤
    sig_payout = (payout >= cfg['trap_payout']) & (dv >= cfg['cls_cycle_yield'])  # 分红超盈利（不可持续）
    sig_cnt = (sig_profit.astype(int) + sig_dt.astype(int) + sig_divcut.astype(int) +
               sig_drawdown.astype(int) + sig_payout.astype(int))
    # 单信号即定性：净利实亏 / 扣非大幅下滑；≥2 信号视为陷阱；核查区(>6%)单信号即警戒
    severe = sig_profit | sig_dt_big | (sig_drawdown & (npy < 5) & sig_dt.astype(bool))
    trap_core = (dv >= cfg['trap_yield']) & (
        severe | (sig_cnt >= 2) |
        ((dv >= cfg['inspect_dividend_yield']) & (sig_profit | sig_dt | sig_divcut | sig_payout))
    )
    # 周期行业高股息 + 景气/回报走弱 → 周期顶部特征
    trap_cyc = is_cyc & (dv >= cfg['cls_cycle_yield']) & (
        (npy < 5) | (dt_npy < 0) | (roe < 8)
    )
    d['CYCLE_TRAP'] = (trap_core | trap_cyc).fillna(False)

    # ── ③ 四类优先级分类（非陷阱才有意义）──
    g_growth = (npy >= cfg['cls_growth_npy']) & (dt_npy.fillna(0) >= 0) & (roe >= cfg['cls_growth_roe'])
    g_trend = (d['UP'] >= cfg['cls_trend_up'])
    g_cyc_div = is_cyc & (dv >= cfg['cls_cycle_yield'])

    cls = pd.Series('RED_DIVIDEND', index=d.index)
    cls = cls.mask(g_cyc_div, 'CYCLE_DIVIDEND')
    cls = cls.mask(g_trend, 'DIVIDEND_TREND')
    cls = cls.mask(g_growth, 'DIVIDEND_GROWTH')
    cls = cls.mask(d['CYCLE_TRAP'], 'CYCLE_TRAP')
    d['分类'] = cls
    d['优先级'] = d['分类'].map({
        'DIVIDEND_GROWTH': 1, 'DIVIDEND_TREND': 2,
        'RED_DIVIDEND': 3, 'CYCLE_DIVIDEND': 4, 'CYCLE_TRAP': 9,
    })

    # ── ④ 双通道路由 ──
    cheap = (pe_pct.fillna(50) <= cfg['value_buy_pe_pct']) & (d['S_DP_估值'] >= 60)
    strong = (d['UP'] >= cfg['trend_buy_up']) & (d['S_UP_RS'] >= cfg['trend_buy_rs'])
    strong = strong | (d['ma_align'] >= 2) & (d['S_UP_RS'] >= 45) & (d['S_UP_趋势'] >= 65)
    channel = pd.Series('—', index=d.index)
    channel = channel.mask(cheap & ~strong, 'VALUE')
    channel = channel.mask(strong & ~cheap, 'TREND')
    channel = channel.mask(cheap & strong, 'BOTH')
    d['通道'] = channel

    # ── ⑤ 四种决策 ──
    base_ok = (d['DLG21'] >= cfg['buy_score']) & (~d['CYCLE_TRAP'])
    value_buy = base_ok & (d['通道'].isin(['VALUE', 'BOTH'])) & (d['DP'] >= cfg['value_buy_dp']) & \
        (pe_pct.fillna(100) <= cfg['value_buy_pe_pct']) & (d['分类'].isin(['DIVIDEND_GROWTH', 'DIVIDEND_TREND', 'RED_DIVIDEND']))
    trend_buy = base_ok & (d['通道'].isin(['TREND', 'BOTH'])) & (d['UP'] >= cfg['trend_buy_up']) & \
        (d['S_UP_RS'] >= cfg['trend_buy_rs']) & (d['分类'].isin(['DIVIDEND_GROWTH', 'DIVIDEND_TREND']))

    decision = pd.Series('AVOID', index=d.index)
    decision = decision.mask(d['DLG21'] >= cfg['buy_score'] - 10, 'WAIT')
    decision = decision.mask(value_buy, 'VALUE_BUY')
    decision = decision.mask(trend_buy, 'TREND_BUY')
    decision = decision.mask(d['CYCLE_TRAP'], 'AVOID')
    d['决策'] = decision

    # ── ⑥ 风险提示 ──
    risks = []
    for _, r in d.iterrows():
        items = []

        def _g(k):
            return r.get(k, np.nan)

        if r['CYCLE_TRAP']:
            if pd.notna(_g('npy_latest')) and _g('npy_latest') < 0:
                items.append('净利同比转负')
            if pd.notna(_g('dt_netprofit_yoy')) and _g('dt_netprofit_yoy') < 0:
                items.append(f'扣非同比{_g("dt_netprofit_yoy"):.1f}%')
            if pd.notna(_g('dv_trend')) and _g('dv_trend') < cfg['trap_div_trend']:
                items.append(f'分红趋势{_g("dv_trend"):.2f}(衰减)')
            if pd.notna(_g('dist_hi60')) and _g('dist_hi60') <= -0.30:
                items.append(f'距60日高{_g("dist_hi60") * 100:.1f}%')
            if r.get('industry', '') in CYCLICAL_INDUSTRIES:
                items.append('周期行业')
            if pd.notna(_g('roe_latest')) and r.get('industry', '') in CYCLICAL_INDUSTRIES \
                    and _g('roe_latest') < 8:
                items.append(f'ROE{_g("roe_latest"):.1f}%偏低')
            if pd.notna(_g('分红支付率')) and _g('分红支付率') >= cfg['trap_payout'] \
                    and pd.notna(_g('dv_ttm')) and _g('dv_ttm') >= cfg['cls_cycle_yield']:
                items.append(f'分红支付率{_g("分红支付率") * 100:.0f}%(超盈利)')
            if not items:
                items.append('高股息但分红可持续性存疑')
        if pd.notna(_g('dv_ttm')) and _g('dv_ttm') >= cfg['inspect_dividend_yield']:
            for k, lab in (('核查_股价崩塌', '股价崩塌'), ('核查_净利下滑', '净利下滑'),
                           ('核查_扣非下滑', '扣非下滑'), ('核查_一次性分红', '支付率≥90%'),
                           ('核查_周期顶部', '周期顶部')):
                if r.get(k, '') == '是':
                    items.append(f'{lab}(>6%核查)')
        if r.get('分类', '') == 'CYCLE_DIVIDEND' and not items:
            items.append('周期红利：景气下行时股息与股价双杀')
        if pd.notna(_g('pe_pct_hist')) and _g('pe_pct_hist') >= 80 and not items:
            items.append('估值处历史高位')
        if pd.notna(_g('ma20_dev')) and _g('ma20_dev') >= 10:
            items.append('短期偏离MA20过大')
        if not items:
            items.append('无显著风险')
        risks.append('；'.join(items))
    d['风险提示'] = risks

    # ── ⑦ 牛市放宽标记 ──
    # 牛市放宽：不因股价新高/站上MA20 而扣分，但必须同时满足质量条件
    # （股息≥3%、经营现金流为正、净利增速≥15%、ROE≥10%、RS>50、MA20>MA60）
    br = cfg['bull_relax']
    bull_mode = state in ('STRONG_BULL', 'BULL')
    ocf_yoy = _numcol(d, 'ocf_yoy_latest')
    relax_ok = pd.Series(bool(bull_mode), index=d.index) & (dv >= br['min_dividend_yield']) & \
        (ocf_yoy.fillna(1) > 0) & (npy >= br['min_earnings_growth']) & \
        (roe >= br['min_roe']) & (d['S_UP_RS'] >= br['min_rs'])
    if br['require_ma20_above_ma60']:
        relax_ok = relax_ok & d['ma20_above_ma60'].fillna(False).astype(bool)
    relax_ok = relax_ok.fillna(False)
    d['牛市放宽'] = np.where(relax_ok, '是', '')
    # 放宽生效：接近 60 日高点/站上 MA20 的位置分不因"已涨"而被压低
    hot = relax_ok & (dist_hi60 >= -0.03)
    d.loc[hot, 'S_UP_位置'] = np.maximum(d.loc[hot, 'S_UP_位置'], 88.0)
    return d


# ═══════════════════════════════════════════════════════
# 报告
# ═══════════════════════════════════════════════════════
_STATE_CN = {
    'STRONG_BULL': '强势牛市', 'BULL': '牛市', 'NEUTRAL': '震荡中性',
    'WEAK': '弱势', 'BEAR': '熊市',
}
_CH_CLS = {
    'DIVIDEND_GROWTH': '股息成长', 'DIVIDEND_TREND': '股息趋势',
    'RED_DIVIDEND': '纯红利', 'CYCLE_DIVIDEND': '周期红利', 'CYCLE_TRAP': '高股息陷阱',
}


def build_report_v21(res, cfg, date, funnel, meta):
    L = []
    st = meta.get('state', 'NEUTRAL')
    det = meta.get('state_detail', {}) or {}
    w = meta.get('weights', {})
    L.append(f'# DLG V2.1 红利安全垫×成长×趋势 选股 {date}')
    L.append('')
    L.append('════════════════════════════════════════')
    L.append('')
    L.append('## 一、核心逻辑')
    L.append('')
    L.append('V2.1 对 V1 的核心修正：**股息是安全垫，不是目标**。')
    L.append('要买的是「便宜且能持续赚钱、且市场开始愿意给钱」的公司，'
             '而不是单纯的股息率排序。')
    L.append('')
    L.append('- 下行保护(DP)：股息率≥3% 提供缓冲 + 估值安全边际 + 现金流与资产质量')
    L.append('- 成长质量(GQ)：增长强度 + 加速 + ROE + 毛利结构 + 盈利含金量')
    L.append('- 上行参与(UP)：趋势结构 + 相对强度 + 位置 + 量能 + 行业强度')
    L.append('')
    L.append(f'**总分 = {w.get("w_dp", 0.35):.2f}×DP + {w.get("w_gq", 0.35):.2f}×GQ + '
             f'{w.get("w_up", 0.30):.2f}×UP**（权重随市场状态自适应）')
    L.append('')
    L.append('股息率分区间处理：≥3% 为门槛；3~4% 正常；4~6% 优秀；'
             f'**>{cfg["inspect_dividend_yield"]:.0f}% 进入核查区**，'
             '须排查股价崩塌 / 净利下滑 / 扣非下滑 / 分红支付率过高 / 周期顶部，命中即降级。')
    L.append('')
    L.append('高股息陷阱（CYCLE_TRAP）判定：净利转负或扣非大幅下滑单信号即定性；'
             '扣非为负、分红趋势衰减、明显回撤、分红支付率过高四项信号累计 ≥2 项；'
             f'股息率 >{cfg["inspect_dividend_yield"]:.0f}% 时单信号即警戒；'
             f'周期行业股息率 ≥{cfg["cls_cycle_yield"]:.0f}% 且景气/回报走弱。')
    L.append('')
    L.append('## 二、市场状态')
    L.append('')
    L.append(f'当前状态：**{st}（{_STATE_CN.get(st, st)}）**')
    L.append('')
    if det:
        L.append('| 指标 | 数值 |')
        L.append('| --- | --- |')
        L.append(f'| 上证收盘 | {det.get("close", float("nan")):.2f} |')
        L.append(f'| MA20 / MA60 / MA120 | {det.get("ma20", float("nan")):.2f} / '
                 f'{det.get("ma60", float("nan")):.2f} / {det.get("ma120", float("nan")):.2f} |')
        L.append(f'| 20日涨幅 | {det.get("ret20", 0) * 100:.2f}% |')
        L.append(f'| 60日涨幅 | {det.get("ret60", 0) * 100:.2f}% |')
        L.append(f'| MA20 斜率(5日) | {det.get("ma20_slope", 0) * 100:.2f}% |')
        L.append(f'| 站上 MA20 / MA20>MA60 / MA60>MA120 | '
                 f'{"是" if det.get("above_ma20") else "否"} / '
                 f'{"是" if det.get("ma20_above_ma60") else "否"} / '
                 f'{"是" if det.get("ma60_above_ma120") else "否"} |')
        L.append('')
    L.append('状态权重映射（五态）：')
    L.append('')
    L.append('| 市场状态 | DP | GQ | UP |')
    L.append('| --- | --- | --- | --- |')
    for k, v in cfg['regime_weights'].items():
        mark = ' ←当前' if k == st else ''
        L.append(f'| {_STATE_CN.get(k, k)}{mark} | {v["w_dp"]:.2f} | {v["w_gq"]:.2f} | {v["w_up"]:.2f} |')
    L.append('')
    L.append('## 三、筛选漏斗')
    L.append('')
    L.append('| 过滤条件 | 剩余只数 | 本步淘汰 |')
    L.append('| --- | --- | --- |')
    for name, _before, after, cut in funnel:
        L.append(f'| {name} | {after} | {cut} |')
    L.append('')

    if res.empty:
        L.append('当日无股票满足全部门槛，建议放宽股息率或流动性门槛。')
        L.append('')
        L.append('════════════════════════════════════════')
        return '\n'.join(L)

    n_vb = int((res['决策'] == 'VALUE_BUY').sum())
    n_tb = int((res['决策'] == 'TREND_BUY').sum())
    n_wait = int((res['决策'] == 'WAIT').sum())
    n_avoid = int((res['决策'] == 'AVOID').sum())
    L.append(f'通过门槛 {len(res)} 只：VALUE_BUY {n_vb} 只、TREND_BUY {n_tb} 只、'
             f'WAIT {n_wait} 只、AVOID {n_avoid} 只。')
    L.append('')
    L.append('分类分布：' + '｜'.join(
        f'{_CH_CLS[k]} {int((res["分类"] == k).sum())} 只'
        for k in ('DIVIDEND_GROWTH', 'DIVIDEND_TREND', 'RED_DIVIDEND', 'CYCLE_DIVIDEND', 'CYCLE_TRAP')))
    L.append('')

    # ── 买入清单 ──
    L.append('## 四、双通道买入清单')
    L.append('')
    for dec, title, desc in (
        ('VALUE_BUY', '价值通道买入（VALUE_BUY）',
         '估值处历史/行业低位 + 股息安全垫厚 + 无陷阱。买的是"便宜且分红可靠"。'),
        ('TREND_BUY', '趋势通道买入（TREND_BUY）',
         '趋势结构确认 + 相对强度领先 + 成长达标 + 无陷阱。买的是"强者恒强"，不因已涨而排除。'),
    ):
        sub = res[res['决策'] == dec].sort_values('DLG21', ascending=False)
        L.append(f'### {title}（{len(sub)} 只）')
        L.append('')
        L.append(desc)
        L.append('')
        if sub.empty:
            L.append('当日无标的满足该通道全部条件。')
            L.append('')
            continue
        L.append('| 代码 | 名称 | 行业 | 分类 | 通道 | 总分 | DP | GQ | UP | 股息率% | PE历史分位 | RS | 距60日高% | 净利同比% | ROE% | 决策 |')
        L.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for _, r in sub.head(cfg['top_n']).iterrows():
            L.append('| {c} | {n} | {i} | {cl} | {ch} | **{s}** | {dp} | {gq} | {up} | {dv} | {pp} | {rs} | {dh} | {npy} | {roe} | {dec} |'.format(
                c=r['ts_code'], n=r['name'], i=r.get('industry', '—'),
                cl=_CH_CLS.get(r['分类'], r['分类']), ch=r['通道'], s=_fv(r['DLG21']),
                dp=_fv(r['DP']), gq=_fv(r['GQ']), up=_fv(r['UP']),
                dv=_fv(r['dv_ttm'], 2), pp=_fv(r.get('pe_pct_hist')),
                rs=_fv(r.get('S_UP_RS')), dh=_fv(pd.to_numeric(r.get('dist_hi60'), errors='coerce') * 100),
                npy=_fv(r.get('npy_latest')), roe=_fv(r.get('roe_latest')), dec=r['决策']))
        L.append('')

    # ── 分类清单（不含回避股） ──
    res_show = res[res['决策'] != 'AVOID']
    L.append('## 五、分类清单（优先级序，不含回避股）')
    L.append('')
    L.append('优先级：**股息成长 > 股息趋势 > 纯红利 > 周期红利**；周期陷阱单列避雷。')
    L.append('')
    for k in ('DIVIDEND_GROWTH', 'DIVIDEND_TREND', 'RED_DIVIDEND', 'CYCLE_DIVIDEND'):
        sub = res_show[res_show['分类'] == k].sort_values(['DLG21'], ascending=False)
        L.append(f'### {_CH_CLS[k]}（{k}，{len(sub)} 只）')
        L.append('')
        if sub.empty:
            L.append('无。')
            L.append('')
            continue
        L.append('| 代码 | 名称 | 行业 | 总分 | DP | GQ | UP | 股息率% | PE | PE分位 | 净利同比% | 扣非同比% | ROE% | MA排列 | RS | 决策 |')
        L.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for _, r in sub.head(cfg['top_n']).iterrows():
            L.append('| {c} | {n} | {i} | **{s}** | {dp} | {gq} | {up} | {dv} | {pe} | {pp} | {npy} | {dt} | {roe} | {ma} | {rs} | {dec} |'.format(
                c=r['ts_code'], n=r['name'], i=r.get('industry', '—'), s=_fv(r['DLG21']),
                dp=_fv(r['DP']), gq=_fv(r['GQ']), up=_fv(r['UP']), dv=_fv(r['dv_ttm'], 2),
                pe=_fv(r['pe_ttm']), pp=_fv(r.get('pe_pct_hist')),
                npy=_fv(r.get('npy_latest')), dt=_fv(r.get('dt_netprofit_yoy')),
                roe=_fv(r.get('roe_latest')), ma=int(r['ma_align']) if pd.notna(r.get('ma_align')) else '—',
                rs=_fv(r.get('S_UP_RS')), dec=r['决策']))
        L.append('')

    # ── >6% 核查区 ──
    ins = res[res['dv_ttm'] >= cfg['inspect_dividend_yield']]
    L.append(f'## 六、>{cfg["inspect_dividend_yield"]:.0f}% 高股息核查区（{len(ins)} 只）')
    L.append('')
    if ins.empty:
        L.append('无。')
        L.append('')
    else:
        L.append('高股息需核实来源，命中任一项即视为不可持续'
                 '（支付率＝股息率×PE，衡量分红是否由盈利支撑）：')
        L.append('')
        L.append('| 代码 | 名称 | 行业 | 股息率% | 3年均值% | 支付率 | 分红趋势 | 核查结果 | 决策 |')
        L.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for _, r in ins.sort_values('dv_ttm', ascending=False).iterrows():
            hits = [lab for kk, lab in (('核查_股价崩塌', '股价崩塌'), ('核查_净利下滑', '净利下滑'),
                                        ('核查_扣非下滑', '扣非下滑'), ('核查_一次性分红', '支付率≥90%'),
                                        ('核查_周期顶部', '周期顶部'))
                    if r.get(kk) == '是']
            m3 = pd.to_numeric(r.get('dv_mean3'), errors='coerce')
            L.append('| {c} | {n} | {i} | {dv} | {m3} | {ra} | {tr} | {h} | {d} |'.format(
                c=r['ts_code'], n=r['name'], i=r.get('industry', '—'), dv=_fv(r['dv_ttm'], 2),
                m3=_fv(m3, 2), ra=_fv(pd.to_numeric(r.get('分红支付率'), errors='coerce'), 2),
                tr=_fv(r.get('dv_trend'), 2),
                h=('、'.join(hits) if hits else '未命中'), d=r['决策']))
        L.append('')

    # ── 避雷名单 ──
    traps = res[res['分类'] == 'CYCLE_TRAP'].sort_values('dv_ttm', ascending=False)
    L.append(f'## 七、高股息陷阱避雷名单（CYCLE_TRAP，{len(traps)} 只）')
    L.append('')
    if traps.empty:
        L.append('无。')
        L.append('')
    else:
        L.append('高股息 + 业绩/分红/回报走弱，或周期行业景气顶部特征。**不参与**'
                 '（周期顶部的高股息最危险：股息与股价可能双杀）。')
        L.append('')
        L.append('判定规则：净利转负或扣非大幅下滑单信号即定性；其余信号'
                 '（扣非为负/分红趋势衰减/明显回撤/分红支付率过高）累计 ≥2 项；'
                 f'股息率 >{cfg["inspect_dividend_yield"]:.0f}% 时单信号即警戒；'
                 f'周期行业股息率 ≥{cfg["cls_cycle_yield"]:.0f}% 且景气/回报走弱。')
        L.append('')
        L.append('| 代码 | 名称 | 行业 | 股息率% | 净利同比% | 扣非同比% | ROE% | 分红趋势 | 距60日高% | 风险 |')
        L.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for _, r in traps.iterrows():
            L.append('| {c} | {n} | {i} | {dv} | {npy} | {dt} | {roe} | {tr} | {dh} | {rk} |'.format(
                c=r['ts_code'], n=r['name'], i=r.get('industry', '—'), dv=_fv(r['dv_ttm'], 2),
                npy=_fv(r.get('npy_latest')), dt=_fv(r.get('dt_netprofit_yoy')),
                roe=_fv(r.get('roe_latest')), tr=_fv(r.get('dv_trend'), 2),
                dh=_fv(pd.to_numeric(r.get('dist_hi60'), errors='coerce') * 100),
                rk=r.get('风险提示', '—')))
        L.append('')

    # ── 重点关注 ──
    L.append(f'## 八、重点关注（前 {min(cfg["top_n"], len(res))} 只要点）')
    L.append('')
    focus = res[res['决策'].isin(['VALUE_BUY', 'TREND_BUY'])].sort_values('DLG21', ascending=False)
    if focus.empty:
        focus = res.sort_values('DLG21', ascending=False)
    for _, r in focus.head(cfg['top_n']).iterrows():
        L.append(f'**{r["ts_code"]} {r["name"]}**（{r.get("industry", "—")}）'
                 f'总分 {_fv(r["DLG21"])}｜{_CH_CLS.get(r["分类"], r["分类"])}｜'
                 f'{r["通道"]} 通道｜**{r["决策"]}**'
                 + ('｜牛市放宽生效' if r.get('牛市放宽') == '是' else ''))
        L.append('')
        L.append(f'- 下行保护 DP {_fv(r["DP"])}：股息 {_fv(r["S_DP_股息"])}'
                 f'（股息率 {_fv(r["dv_ttm"], 2)}%，3年最低正股息 {_fv(r.get("dv_min3"), 2)}%，'
                 f'分红趋势 {_fv(r.get("dv_trend"), 2)}）｜估值 {_fv(r["S_DP_估值"])}'
                 f'（PE {_fv(r["pe_ttm"])}，PE历史分位 {_fv(r.get("pe_pct_hist"))}%）｜'
                 f'现金流 {_fv(r["S_DP_现金流"])}｜资产 {_fv(r["S_DP_资产"])}')
        L.append(f'- 成长质量 GQ {_fv(r["GQ"])}：增长 {_fv(r["S_GQ_增长"])}'
                 f'（净利同比 {_fv(r.get("npy_latest"))}%，营收同比 {_fv(r.get("or_yoy_latest"))}%）｜'
                 f'加速 {_fv(r["S_GQ_加速"])}（扣非同比 {_fv(r.get("dt_netprofit_yoy"))}%）｜'
                 f'ROE {_fv(r["S_GQ_ROE"])}（{_fv(r.get("roe_latest"))}%）｜'
                 f'毛利 {_fv(r["S_GQ_毛利"])}（{_fv(r.get("margin_latest"))}%）｜含金 {_fv(r["S_GQ_含金"])}')
        L.append(f'- 上行参与 UP {_fv(r["UP"])}：趋势 {_fv(r["S_UP_趋势"])}（MA排列 {int(r["ma_align"]) if pd.notna(r.get("ma_align")) else "—"}，'
                 f'MA20斜率 {_fv(pd.to_numeric(r.get("ma20_slope"), errors="coerce") * 100, 2)}%）｜'
                 f'RS {_fv(r["S_UP_RS"])}（20日 {_fv(pd.to_numeric(r.get("ret20"), errors="coerce") * 100)}%）｜'
                 f'位置 {_fv(r["S_UP_位置"])}（距60日高 {_fv(pd.to_numeric(r.get("dist_hi60"), errors="coerce") * 100)}%，'
                 f'距MA20 {_fv(r.get("ma20_dev"), 2)}%）｜量能 {_fv(r["S_UP_量能"])}（量比 {_fv(r.get("vol_ratio"), 2)}）｜'
                 f'行业 {_fv(r["S_UP_行业"])}')
        L.append(f'- 风险提示：{r.get("风险提示", "—")}')
        L.append('')

    ind_stat = res.groupby('industry').agg(只数=('ts_code', 'count'), 平均总分=('DLG21', 'mean'),
                                          平均DP=('DP', 'mean'), 平均UP=('UP', 'mean'))
    ind_stat = ind_stat.sort_values('只数', ascending=False).head(15)
    L.append('## 九、行业分布（前 15）')
    L.append('')
    L.append('| 行业 | 只数 | 平均总分 | 平均DP | 平均UP |')
    L.append('| --- | --- | --- | --- | --- |')
    for ind, r in ind_stat.iterrows():
        L.append(f'| {ind} | {int(r["只数"])} | {r["平均总分"]:.1f} | {r["平均DP"]:.1f} | {r["平均UP"]:.1f} |')
    L.append('')
    L.append('════════════════════════════════════════')
    L.append('')
    L.append('## 十、数据来源与使用说明')
    L.append('')
    L.append(f'- 估值/股息/市值/流动性：daily_basic_cache（trade_date={date}）')
    L.append(f'- 行情与趋势：daily_cache 批量自算（回看 {cfg["trend_lookback"]} 交易日，'
             'MA5/10/20/60/120、区间涨幅、距高点、量能比）')
    L.append(f'- 估值历史分位：daily_basic_cache 回看 {cfg["val_lookback_days"]} 自然日 PE/PB 分位')
    L.append(f'- 成长与质量：fina_indicator_cache 多期财报，最新报告期 {meta.get("period", "—")}，'
             f'披露日 ≤ {meta.get("ann_max", "—")}')
    L.append(f'- 扣非同比/单季加速：{meta.get("full_file", "未使用（文件缺失）")}')
    L.append(f'- 股息率持续性：daily_basic_cache 逐日 dv_ttm 回看 {cfg["div_history_years"]} 年')
    L.append('- 市场状态：index_daily_cache 上证指数（缺失回退 TDX .day）')
    L.append('- 行业/上市日：stock_basic.csv')
    L.append('')
    L.append('使用说明与风险提示：')
    L.append('- 三支柱权重随市场状态自适应：牛市抬升 UP（趋势参与），弱市抬升 DP（下行保护）。')
    L.append('- VALUE_BUY 与 TREND_BUY 是两条独立通道，同一只股票可能同时满足（BOTH）。')
    L.append('- 股息率 >6% 不等于更好，往往是股价下跌或一次性分红所致，报告已单列核查区。')
    L.append('- CYCLE_TRAP 为避雷名单，即使总分高也不参与（周期顶部的高股息最危险）。')
    L.append('- 本报告为基本面+趋势的横截面筛选结果，不含仓位与止损规则，建仓前请另行制定交易计划。')
    L.append('- 数据均来自本地缓存，使用前请确认缓存已更新至最新交易日。')
    return '\n'.join(L)


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════
def run(date='', use_api=True, cfg=None, verbose=True):
    cfg = dict(DLG_V21_CONFIG, **(cfg or {}))
    date = get_effective_date(date) if not date else str(date)
    t0 = time.time()

    # ① 市场状态
    idx = load_index_series('000001.SH', date)
    state, state_detail = market_state_v21(date, idx)
    weights = regime_weights(state, cfg)

    # ② 基本面快照（复用 V1 加载器）
    snap, _ = load_market_snapshot(date, use_api=use_api)
    hist, latest_period = load_fin_history(date)
    full = load_fin_full(date, hist)
    sb = load_industry()

    df = snap.merge(sb[['ts_code', 'name', 'industry', 'list_date']], on='ts_code', how='left')
    df = df.merge(hist, on='ts_code', how='left')
    if not full.empty:
        df = df.merge(full, on='ts_code', how='left')
    pre = df.loc[pd.to_numeric(df['dv_ttm'], errors='coerce') >= cfg['min_dividend_yield'], 'ts_code']
    divh = load_div_history(date, pre.tolist(), years=cfg['div_history_years'])
    df = df.merge(divh, on='ts_code', how='left')
    df['is_latest_period'] = df['period'].astype(str) == str(latest_period)

    # ③ 先按股息率粗筛，减少趋势面板的读取量（趋势计算成本与股票数成正比）
    pre_codes = df.loc[pd.to_numeric(df['dv_ttm'], errors='coerce') >= cfg['min_dividend_yield'], 'ts_code']
    pre_codes = [c for c in pre_codes.tolist() if not str(c).endswith('.BJ')]
    panel = load_price_panel(date, pre_codes, lookback=cfg['trend_lookback'])
    valh = load_val_history(date, pre_codes, lookback_days=cfg['val_lookback_days'])
    roeh = load_roe_history(date, pre_codes)
    if not panel.empty:
        df = df.merge(panel, on='ts_code', how='left')
    else:
        for c in ('bars', 'ma_align', 'ret20', 'dist_hi60', 'vol_ratio', 'ma20_dev',
                  'ma20_slope', 'ma20_above_ma60', 'new_hi60'):
            df[c] = np.nan
    if not valh.empty:
        df = df.merge(valh, on='ts_code', how='left')
    if not roeh.empty:
        df = df.merge(roeh, on='ts_code', how='left')

    # ④ 门槛 → 打分 → 分类决策
    df_pass, funnel = apply_gates_v21(df, cfg, date)
    scored = score_pillars_v21(df_pass, cfg, state)
    res = classify_and_decide(scored, cfg, state)
    res = res.sort_values(['决策', 'DLG21'], ascending=[True, False]).reset_index(drop=True)

    full_file = ''
    if not full.empty and latest_period:
        p = str(latest_period)
        tag = f'{p[:4]}H1_full' if p[4:] == '0630' else (
            f'{p[:4]}Q1_full' if p[4:] == '0331' else f'{p[:4]}_full')
        full_file = f'fin_ind_{tag}.parquet'
    meta = {
        'period': latest_period, 'ann_max': date, 'full_file': full_file,
        'state': state, 'state_detail': state_detail, 'weights': weights,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, f'dlg_v21_{date}.md')
    csv_path = os.path.join(OUT_DIR, f'dlg_v21_{date}.csv')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(build_report_v21(res, cfg, date, funnel, meta))

    out_cols = ['ts_code', 'name', 'industry', '决策', '分类', '优先级', '通道', 'DLG21',
                'DP', 'GQ', 'UP', 'S_DP_股息', 'S_DP_估值', 'S_DP_现金流', 'S_DP_资产',
                'S_GQ_增长', 'S_GQ_加速', 'S_GQ_ROE', 'S_GQ_毛利', 'S_GQ_含金',
                'S_UP_趋势', 'S_UP_RS', 'S_UP_位置', 'S_UP_量能', 'S_UP_行业',
                'dv_ttm', 'dv_mean3', 'dv_min3', 'dv_trend', 'dv_pos_ratio', '分红支付率',
                'pe_ttm', 'pb', 'pe_pct_hist', 'pb_pct_hist',
                'npy_latest', 'dt_netprofit_yoy', 'or_yoy_latest', 'roe_latest',
                'margin_latest', 'debt_latest', 'npy_mean4',
                'ma_align', 'ma20_slope', 'ret20', 'ret60', 'dist_hi60',
                'vol_ratio', 'ma20_dev', 'new_hi60', '牛市放宽', 'CYCLE_TRAP', '风险提示']
    out_cols = [c for c in out_cols if c in res.columns]
    res[out_cols].to_csv(csv_path, index=False, encoding='utf-8-sig')

    pdf_path = ''
    if cfg.get('make_pdf', True):
        pdf_path = os.path.join(OUT_DIR, f'dlg_v21_{date}.pdf')
        try:
            markdown_to_pdf(md_path, pdf_path)
        except Exception as e:
            pdf_path = ''
            print(f'[提示] PDF 生成失败({type(e).__name__}: {e})，已保留 Markdown/CSV')

    if verbose:
        n_vb = int((res['决策'] == 'VALUE_BUY').sum())
        n_tb = int((res['决策'] == 'TREND_BUY').sum())
        n_wait = int((res['决策'] == 'WAIT').sum())
        n_avoid = int((res['决策'] == 'AVOID').sum())
        print(f'[DLG21] 交易日 {date}｜市场状态 {state}({_STATE_CN.get(state, state)})｜'
              f'权重 DP{weights["w_dp"]:.2f}/GQ{weights["w_gq"]:.2f}/UP{weights["w_up"]:.2f}')
        print(f'[DLG21] 候选池 {len(df)} 只 → 通过门槛 {len(df_pass)} 只 → 输出 {len(res)} 只'
              f'（VALUE_BUY {n_vb} / TREND_BUY {n_tb} / WAIT {n_wait} / AVOID {n_avoid}）'
              f'｜耗时 {time.time() - t0:.1f}s')
        if not res.empty:
            show = res[~res['决策'].isin(['AVOID'])].head(cfg['top_n'])
            if show.empty:
                show = res.head(cfg['top_n'])
            print(show[['ts_code', 'name', 'industry', '决策', '分类', '通道', 'DLG21',
                        'DP', 'GQ', 'UP', 'dv_ttm', 'pe_ttm', 'S_UP_RS']].to_string(index=False))
        print(f'[DLG21] 报告: {md_path}')
        print(f'[DLG21] 明细: {csv_path}')
        if pdf_path:
            print(f'[DLG21] PDF : {pdf_path}')
    return res, {'md': md_path, 'csv': csv_path, 'pdf': pdf_path,
                 'funnel': funnel, 'date': date, 'state': state}


def main():
    ap = argparse.ArgumentParser(description='DLG V2.1 红利安全垫×成长×趋势 选股')
    ap.add_argument('--date', default='', help='交易日 YYYYMMDD，默认取有效交易日')
    ap.add_argument('--top', type=int, default=DLG_V21_CONFIG['top_n'], help='报告重点展示只数')
    ap.add_argument('--min-yield', type=float, default=DLG_V21_CONFIG['min_dividend_yield'],
                    help='股息率下限%%（安全垫门槛）')
    ap.add_argument('--inspect-yield', type=float, default=DLG_V21_CONFIG['inspect_dividend_yield'],
                    help='高股息核查区起点%%')
    ap.add_argument('--max-pe', type=float, default=DLG_V21_CONFIG['max_pe_ttm'], help='PE(TTM) 上限')
    ap.add_argument('--max-pb', type=float, default=DLG_V21_CONFIG['max_pb'], help='PB 上限')
    ap.add_argument('--min-growth', type=float, default=DLG_V21_CONFIG['min_netprofit_yoy'],
                    help='净利同比下限%%')
    ap.add_argument('--min-mv', type=float, default=DLG_V21_CONFIG['min_total_mv_yi'],
                    help='总市值下限(亿元)')
    ap.add_argument('--buy-score', type=float, default=DLG_V21_CONFIG['buy_score'],
                    help='决策基础分门槛')
    ap.add_argument('--keep-dividend-gap', action='store_true', help='放宽分红中断门槛')
    ap.add_argument('--no-api', action='store_true', help='纯本地缓存，不调用接口补数')
    ap.add_argument('--no-pdf', action='store_true', help='不生成 PDF')
    ap.add_argument('--allow-stale', action='store_true', help='允许使用非最新报告期')
    ap.add_argument('--keep-financial', dest='exclude_financial', action='store_false',
                    help='保留金融行业')
    args = ap.parse_args()

    cfg = {
        'top_n': args.top,
        'min_dividend_yield': args.min_yield,
        'inspect_dividend_yield': args.inspect_yield,
        'min_div_persist_ratio': 0.0 if args.keep_dividend_gap else DLG_V21_CONFIG['min_div_persist_ratio'],
        'max_pe_ttm': args.max_pe,
        'max_pb': args.max_pb,
        'min_netprofit_yoy': args.min_growth,
        'min_total_mv_yi': args.min_mv,
        'buy_score': args.buy_score,
        'require_latest_period': not args.allow_stale,
        'exclude_financial': args.exclude_financial,
        'make_pdf': not args.no_pdf,
    }
    run(date=args.date, use_api=not args.no_api, cfg=cfg)


if __name__ == '__main__':
    main()
