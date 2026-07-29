"""
幻方风格多因子量化择时评分模块
=================================
仿顶级量化私募(幻方/九坤/明汯)的因子研究方法论：
- 6大因子族，每个因子先算原始值，再交叉截面百分位排名
- 因子等权合成 → 得到0-100的量化择时分
- 完全消除硬编码档位，分数天然服从均匀分布
- 每个因子都有清晰的金融学含义，可独立做IC/IR检验

因子体系:
  F1: 趋势强度(25%) - 价格偏离MA20的程度 × 均线方向
  F2: 均线排列度(20%) - 多周期均线斜率与发散程度的连续度量
  F3: 动量复合(20%) - 短/中/长周期收益率的加权合成
  F4: 量价配合(15%) - 放量上涨>缩量上涨>缩量下跌>放量下跌
  F5: 波动调整收益(10%) - 夏普比率思想：收益/波动
  F6: 资金流向(10%) - 主力资金净流入占成交额比
"""
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple


def _safe_float(series, idx=-1):
    """安全取float值"""
    try:
        return float(series.iloc[idx])
    except:
        return float(series.values[idx])


def _calc_washout_recovery(closes, highs, lows, vols):
    """
    洗盘后修复因子 (washout_recovery)

    检测"极端洗盘 → 缩量止跌 → 温和放量修复 → 均线支撑"的完整结构，
    即闰土股份(002440)式：跌停洗盘→缩量企稳→中阳修复→二波涨停启动。

    Returns:
        float: 连续因子值，正值越大说明洗盘修复结构越完整
    """
    n = len(closes)
    if n < 25:
        return 0.0

    price = closes[-1]

    # 1. 近30日涨跌幅序列
    ret_30d = np.diff(closes[-31:]) / closes[-31:-1] * 100 if n >= 31 else \
              np.diff(closes) / closes[:-1] * 100
    if len(ret_30d) == 0:
        return 0.0
    ret_30d_full = np.concatenate([[0], ret_30d]) if len(ret_30d) < len(closes[-31:]) else ret_30d

    max_gain_30d = np.max(ret_30d)  # 最大单日涨幅
    max_loss_10d = np.min(ret_30d[-10:]) if len(ret_30d) >= 10 else np.min(ret_30d)  # 近10日最大单日跌幅

    # 2. 找最近一次大跌日(洗盘日): 跌幅 <= -7% 或 跌停
    washout_idx = -1
    for i in range(1, min(15, n)):
        ret = (closes[-i] / closes[-i-1] - 1) * 100
        if ret <= -7:
            washout_idx = n - i
            break

    # 3. 量价分析
    vol_ratio_latest = vols[-1] / np.mean(vols[-20:-1]) if n >= 20 else 1.0  # 最新量比
    latest_ret = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0

    # 缩量程度: 洗盘后3日均量 vs 洗盘前10日均量
    vol_shrink = 1.0
    if washout_idx > 0 and washout_idx + 3 < n:
        post_vol = np.mean(vols[washout_idx+1:washout_idx+4])  # 洗盘后3天
        pre_vol = np.mean(vols[max(0, washout_idx-10):washout_idx])  # 洗盘前10天
        vol_shrink = post_vol / pre_vol if pre_vol > 0 else 1.0

    # 无洗盘事件时，用近5日/近20日均量比代替
    if washout_idx < 0:
        vol_shrink = np.mean(vols[-5:]) / np.mean(vols[-20:]) if n >= 20 else 1.0

    # 4. 均线结构
    ma5 = np.mean(closes[-5:])
    ma10 = np.mean(closes[-10:]) if n >= 10 else ma5
    ma20 = np.mean(closes[-20:]) if n >= 20 else ma10

    # 5. 回撤健康度: 从近30日高点回撤
    peak_30d = np.max(closes[-30:]) if n >= 30 else np.max(closes)
    drawdown = (peak_30d - price) / peak_30d * 100

    # ================================================================
    # 连续因子合成 (负值=无此形态, 正值=形态越完整)
    # ================================================================
    # 子项A: 前期有过一波上涨 (0~2)
    prev_wave_score = min(max_gain_30d / 10, 2.0)

    # 子项B: 洗盘事件强度 (0~1.5) - 需要有大跌洗盘
    washout_score = abs(min(max_loss_10d, 0)) / 10
    washout_score = min(washout_score, 1.5)

    # 子项C: 缩量止跌程度 (0~3) - 缩量越充分越高分
    if washout_idx > 0:
        # 有明确洗盘事件: 缩量越极致越好
        shrink_score = max(0, 1.0 - vol_shrink) * 2.0 + 0.3
    else:
        # 无洗盘但近期缩量: 给基础分
        shrink_score = max(0, 1.0 - vol_shrink) * 1.0

    # 子项D: 温和放量修复 (-0.5~2.0)
    # 中阳+2%~+7% 且 量比0.8~1.5 = 最高分
    # 涨幅过高(涨停) = 追高风险, 涨幅为负 = 仍在下行
    if 2 <= latest_ret <= 7 and 0.8 <= vol_ratio_latest <= 1.5:
        recovery_score = 1.5 + (latest_ret - 2) / 10  # 2%得1.5, 7%得2.0
    elif 0 < latest_ret < 2 and 0.8 <= vol_ratio_latest <= 1.5:
        recovery_score = 1.0  # 小阳线给基础分
    elif latest_ret > 7:
        recovery_score = 0.5  # 大涨但可能是追涨，不是温和修复
    elif latest_ret <= 0:
        recovery_score = -0.5  # 仍在下跌
    else:
        recovery_score = 0.0
    recovery_score = max(-0.5, min(2.0, recovery_score))

    # 子项E: 均线结构 (0~1.5)
    if ma5 > ma10 > ma20:
        # 完美多头: 按发散程度加分
        ma_spread = (ma5 - ma20) / ma20 * 100
        ma_score = 1.0 + min(ma_spread / 5, 0.5)
    elif ma5 > ma20:
        ma_score = 0.5  # 短期强但中期一般
    elif price > ma20:
        ma_score = 0.3  # 仅站上MA20
    else:
        ma_score = -0.5  # 空头

    # 子项F: 回撤健康度 (0~1)
    # 回撤5%~15%为最佳(充分调整但未破位)
    if 5 <= drawdown <= 15:
        dd_score = 1.0
    elif 0 <= drawdown < 5:
        dd_score = 0.3  # 调整不够充分
    elif 15 < drawdown <= 25:
        dd_score = 0.6  # 调整过深但可接受
    else:
        dd_score = 0.0  # 无回撤或回撤过大

    # 加权合成
    factor = (
        prev_wave_score * 0.15 +
        washout_score * 0.20 +
        shrink_score * 0.25 +
        recovery_score * 0.20 +
        ma_score * 0.12 +
        dd_score * 0.08
    )

    return factor


def compute_raw_factors(daily: pd.DataFrame, moneyflow: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    """
    计算单只股票的6个原始因子值（未归一化，用于后续交叉截面排名）

    Args:
        daily: 日线数据，需含 close/high/low/open/vol/amount 列，按trade_date升序
        moneyflow: 资金流向数据，需含 net_mf_amount 列

    Returns:
        dict: {factor_name: raw_value}
    """
    closes = daily['close'].values.astype(float)
    highs = daily['high'].values.astype(float)
    lows = daily['low'].values.astype(float)
    vols = daily['vol'].values.astype(float)

    n = len(closes)
    if n < 20:
        return {f: 0.0 for f in ['trend_strength', 'ma_alignment', 'momentum',
                                  'volume_price', 'vol_adj_return', 'money_flow']}

    price = closes[-1]

    # 计算均线
    ma5 = np.mean(closes[-5:]) if n >= 5 else price
    ma10 = np.mean(closes[-10:]) if n >= 10 else price
    ma20 = np.mean(closes[-20:]) if n >= 20 else price
    ma60 = np.mean(closes[-60:]) if n >= 60 else ma20

    # ============================================================
    # F1: 趋势强度 (trend_strength) — 权重 25%
    # 价格偏离MA20的百分比，乘以均线排列方向符号
    # 正值 = 多头趋势，负值 = 空头趋势
    # ============================================================
    deviation = (price - ma20) / ma20 * 100  # 偏离度 %

    # 均线排列方向: 多头=+1, 空头=-1, 震荡=0
    if ma5 > ma10 > ma20 > ma60:
        direction = 1.0
    elif ma5 < ma10 < ma20 < ma60:
        direction = -1.0
    else:
        # 部分多头: 按多头均线对数量打分
        pairs = [(ma5, ma10), (ma10, ma20), (ma20, ma60)]
        direction = sum(1 if a > b else -1 for a, b in pairs) / 3.0

    trend_strength = deviation * direction

    # ============================================================
    # F2: 均线排列度 (ma_alignment) — 权重 20%
    # 连续度量多周期均线的发散/收敛程度
    # 多头排列+发散=高分，空头排列+收敛=低分，纠缠=中间
    # ============================================================
    # 三个斜率: 短期/中期/长期
    slope_short = (ma5 - ma10) / ma10 * 100 if ma10 > 0 else 0
    slope_mid = (ma10 - ma20) / ma20 * 100 if ma20 > 0 else 0
    slope_long = (ma20 - ma60) / ma60 * 100 if ma60 > 0 else 0

    # 发散度: 短端与长端的距离
    divergence = (ma5 - ma60) / ma60 * 100 if ma60 > 0 else 0

    # 加权合成: 短端权重大，反映近期趋势结构
    ma_alignment = slope_short * 0.40 + slope_mid * 0.35 + slope_long * 0.15 + divergence * 0.10

    # ============================================================
    # F3: 动量复合 (momentum) — 权重 20%
    # 多周期收益率加权，融入反转惩罚
    # ============================================================
    ret_1d = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
    ret_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
    ret_10d = (closes[-1] / closes[-11] - 1) * 100 if n >= 11 else 0
    ret_20d = (closes[-1] / closes[-21] - 1) * 100 if n >= 21 else 0

    # 反转惩罚: 极端单日涨幅往往伴随短期反转
    reversal_penalty = 0
    if ret_1d > 8:
        reversal_penalty = (ret_1d - 8) * 0.5
    elif ret_1d < -8:
        reversal_penalty = (ret_1d + 8) * 0.5  # 负的反转 = 暴跌后反弹

    momentum = ret_5d * 0.40 + ret_10d * 0.30 + ret_20d * 0.20 - reversal_penalty * 0.10

    # ============================================================
    # F4: 量价配合 (volume_price) — 权重 15%
    # 量价关系: 放量上涨=最佳, 缩量下跌=次佳, 放量下跌=最差
    # ============================================================
    vol_5d_avg = np.mean(vols[-5:]) if n >= 5 else np.mean(vols)
    vol_20d_avg = np.mean(vols[-20:]) if n >= 20 else np.mean(vols)
    vol_ratio = vol_5d_avg / vol_20d_avg if vol_20d_avg > 0 else 1.0

    # 量价配合: 正收益+放量=高分, 负收益+放量=低分
    if ret_5d > 0:
        volume_price = (vol_ratio - 1.0) * 2 + 0.5  # 放量上涨加分
    elif ret_5d < 0:
        volume_price = (1.0 - vol_ratio) * 2 - 0.5  # 缩量下跌略好, 放量下跌扣分
    else:
        volume_price = 0

    # ============================================================
    # F5: 波动调整收益 (vol_adj_return) — 权重 10%
    # 夏普比率思想: 收益/波动，衡量单位风险下的回报
    # ============================================================
    if n >= 20:
        rets_20d = np.diff(closes[-21:]) / closes[-21:-1] * 100
        vol_20d = np.std(rets_20d) if len(rets_20d) > 1 else 1.0
        vol_adj_return = ret_20d / (vol_20d + 0.001)
    else:
        vol_adj_return = 0

    # ============================================================
    # F6: 资金流向 (money_flow) — 权重 10%
    # 主力净流向占比 = (大单+特大单净买入) / (大单+特大单总成交)
    # 分子分母同口径，正=主力净买入，负=主力净卖出，范围(-100%, +100%)
    # ============================================================
    money_flow = 0.0
    if moneyflow is not None and len(moneyflow) > 0:
        try:
            mf = moneyflow.sort_values('trade_date').reset_index(drop=True)
            tail = mf.iloc[-5:]
            has_lg = 'buy_lg_amount' in mf.columns and 'sell_lg_amount' in mf.columns
            has_elg = 'buy_elg_amount' in mf.columns and 'sell_elg_amount' in mf.columns
            if has_lg and has_elg:
                main_buy = float(tail['buy_lg_amount'].sum()) + float(tail['buy_elg_amount'].sum())
                main_sell = float(tail['sell_lg_amount'].sum()) + float(tail['sell_elg_amount'].sum())
                main_net = main_buy - main_sell
                main_turnover = main_buy + main_sell
                if main_turnover > 0:
                    money_flow = main_net / main_turnover * 100
            elif has_lg:
                main_buy = float(tail['buy_lg_amount'].sum())
                main_sell = float(tail['sell_lg_amount'].sum())
                main_net = main_buy - main_sell
                main_turnover = main_buy + main_sell
                if main_turnover > 0:
                    money_flow = main_net / main_turnover * 100
            elif 'net_mf_amount' in mf.columns:
                net_5d = float(tail['net_mf_amount'].sum())
                money_flow = net_5d / 1e8  # 退化为绝对金额(亿)
        except:
            money_flow = 0.0

    # 第7因子: 洗盘后修复因子 (washout_recovery)
    washout_recovery = _calc_washout_recovery(closes, highs, lows, vols)

    return {
        'trend_strength': trend_strength,
        'ma_alignment': ma_alignment,
        'momentum': momentum,
        'volume_price': volume_price,
        'money_flow': money_flow,
        'vol_adj_return': vol_adj_return,
        'washout_recovery': washout_recovery,
    }


def cross_sectional_score(factor_df: pd.DataFrame) -> pd.Series:
    """
    交叉截面百分位排名 → 加权合成 → 0-100量化择时分

    对每个因子在横截面上做百分位排名(rank pct)，
    保证分数分布均匀，不会出现"100分扎堆"的问题。

    Args:
        factor_df: 包含6个原始因子列的DataFrame，每行一只股票

    Returns:
        pd.Series: 0-100的量化择时分
    """
    FACTOR_WEIGHTS = {
        'trend_strength': 0.22,
        'ma_alignment': 0.18,
        'momentum': 0.18,
        'volume_price': 0.13,
        'vol_adj_return': 0.09,
        'money_flow': 0.10,
        'washout_recovery': 0.10,
    }

    scores = pd.Series(0.0, index=factor_df.index)

    for factor_name, weight in FACTOR_WEIGHTS.items():
        if factor_name not in factor_df.columns:
            continue
        raw = factor_df[factor_name].astype(float)

        # 百分位排名: 值越大排名越高 (0-100)
        ranked = raw.rank(pct=True) * 100

        scores += ranked * weight

    return scores.clip(0, 100)


def quant_score_single(daily: pd.DataFrame, moneyflow: Optional[pd.DataFrame] = None) -> Dict:
    """
    单只股票量化评分(用于非批量场景，使用Sigmoid映射代替交叉截面排名)

    当无法获取全市场横截面数据时，使用预设的Sigmoid参数将原始因子值
    映射到0-100。参数基于历史数据校准。

    Returns:
        dict: {score, factors_detail}
    """
    factors = compute_raw_factors(daily, moneyflow)

    # Sigmoid映射参数: (center, scale) - 基于历史分布校准
    SIGMOID_PARAMS = {
        'trend_strength': (0, 8),       # 偏离度中心0，尺度8
        'ma_alignment': (0, 3),         # 排列度中心0，尺度3
        'momentum': (0, 8),             # 动量中心0，尺度8
        'volume_price': (0.2, 0.8),     # 量价中心0.2，尺度0.8
        'vol_adj_return': (0, 0.5),     # 夏普中心0，尺度0.5
        'money_flow': (0, 5),           # 主力净流向占比%，中心0，尺度5
        'washout_recovery': (0.5, 0.8), # 洗盘修复中心0.5，尺度0.8
    }

    FACTOR_WEIGHTS = {
        'trend_strength': 0.22,
        'ma_alignment': 0.18,
        'momentum': 0.18,
        'volume_price': 0.13,
        'vol_adj_return': 0.09,
        'money_flow': 0.10,
        'washout_recovery': 0.10,
    }

    total_score = 0.0
    factor_scores = {}

    for fname, weight in FACTOR_WEIGHTS.items():
        raw_val = factors.get(fname, 0)
        center, scale = SIGMOID_PARAMS[fname]
        # Sigmoid: 1 / (1 + exp(-(x-center)/scale)) * 100
        z = (raw_val - center) / scale
        sigmoid = 1.0 / (1.0 + np.exp(-z))
        factor_score = sigmoid * 100
        factor_scores[fname] = factor_score
        total_score += factor_score * weight

    return {
        'score': round(total_score, 1),
        'factors': factors,
        'factor_scores': factor_scores,
    }