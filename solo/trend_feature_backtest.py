"""
确定性走强评分公式回测器

基于 AI 提炼的"确定性走强评分公式"（0~100分），对历史K线数据进行逐日打分，
统计 S级信号(≥80分) 次日买入后的阶段涨幅（5日/10日/20日）。

使用方式：
  python trend_feature_backtest.py                       # 默认5只股票
  python trend_feature_backtest.py 600498 688003 002409  # 指定股票
"""

import os
import sys
import json
import sqlite3
import copy
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import pandas as pd
import numpy as np

# =========================
# 路径与配置
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r"D:\mystock\cache_daily"
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "trend_feature_output")

# 回测参数
LOOKBACK_DAYS = 120          # 回测读取历史天数（越多数据越全）
RECENT_DAYS = 44             # 只分析最近N天（近2个月约44个交易日）
SIGNAL_THRESHOLD = 80        # S级信号阈值
DEFAULT_STOCKS = [
    # 原有5只核心股
    "600498.SH",  # 烽火通信 - AI通信/光通信
    "688003.SH",  # 天准科技 - 机器视觉/AI设备
    "002409.SZ",  # 雅克科技 - 半导体材料
    "600460.SH",  # 士兰微 - 功率半导体
    "002747.SZ",  # 埃斯顿 - 工业机器人

    # AI/科技
    "688256.SH",  # 寒武纪 - AI芯片
    "300308.SZ",  # 中际旭创 - 光模块/AI算力
    "688981.SH",  # 中芯国际 - 芯片制造
    "300502.SZ",  # 新易盛 - 光模块
    "002371.SZ",  # 北方华创 - 半导体设备
    "688012.SH",  # 中微公司 - 半导体设备
    "002230.SZ",  # 科大讯飞 - AI应用
    "603986.SH",  # 兆易创新 - 芯片设计
    "002129.SZ",  # TCL中环 - 半导体材料
    "000063.SZ",  # 中兴通讯 - 通信设备

    # 新能源/制造
    "300750.SZ",  # 宁德时代 - 新能源电池
    "002594.SZ",  # 比亚迪 - 新能源车
    "300124.SZ",  # 汇川技术 - 工控自动化

    # 消费/金融
    "600519.SH",  # 贵州茅台 - 白酒消费
    "000333.SZ",  # 美的集团 - 家电
    "601318.SH",  # 中国平安 - 保险金融

    # 医药/军工/有色
    "688396.SH",  # 华润微 - 半导体
    "600150.SH",  # 中国船舶 - 军工造船
    "601899.SH",  # 紫金矿业 - 有色金属
]


# =========================
# 工具函数
# =========================

def normalize_ts_code(code: str) -> str:
    code = code.strip().upper()
    if '.' in code:
        return code
    if len(code) != 6:
        return code
    if code.startswith(('60', '68', '51', '11', '13', '90')):
        return f"{code}.SH"
    if code.startswith(('00', '30', '15', '16', '20')):
        return f"{code}.SZ"
    if code.startswith(('43', '83', '87', '92')):
        return f"{code}.BJ"
    return f"{code}.SZ"


def get_stock_data(ts_code: str, lookback_days: int = 120,
                    end_date: str = None) -> Optional[pd.DataFrame]:
    """
    从 SQLite 读取历史K线+技术因子
    自动从最近N个交易日中选取（按日期过滤，非行数）
    """
    if not os.path.exists(DB_PATH):
        print(f"[错误] 数据库不存在: {DB_PATH}")
        return None

    # 计算结束日期（默认今天）
    from datetime import datetime
    if end_date is None:
        end_dt = datetime.now()
    else:
        end_dt = datetime.strptime(str(end_date), '%Y%m%d')

    # 计算开始日期
    start_dt = end_dt - timedelta(days=lookback_days * 2)  # 多取几天保证覆盖

    conn = sqlite3.connect(DB_PATH)
    try:
        # 按日期范围过滤，确保拿到的是最近 lookback_days 个交易日
        df = pd.read_sql_query(
            """
            SELECT * FROM stk_factor_pro
            WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            conn,
            params=(ts_code, start_dt.strftime('%Y%m%d'), end_dt.strftime('%Y%m%d'))
        )
    finally:
        conn.close()

    if df.empty:
        return None

    # 只保留最后 lookback_days 行（最近N个交易日）
    df = df.tail(lookback_days).reset_index(drop=True)
    return df


# =========================
# 确定性走强评分公式（AI提炼版）
# =========================

def calc_signal_score(row: pd.Series, prev_row: pd.Series = None,
                       ma60_series: pd.Series = None,
                       dif_series: pd.Series = None) -> Dict:
    """
    计算单日"确定性走强评分"（0~100分）

    公式：均线趋势(20) + MACD强度(25) + RSI强势(20) + 量能健康(20) + 价格形态(15)
    """
    score = {}
    detail = {}

    # ── 1. 均线趋势分 (0~20) ──────────────────────────────────────────
    ma_trend = 0
    ma5 = row.get('ma_bfq_5', 0) or 0
    ma10 = row.get('ma_bfq_10', 0) or 0
    ma20 = row.get('ma_bfq_20', 0) or 0
    ma60 = row.get('ma_bfq_60', 0) or 0
    close = row['close']
    prev_ma60 = prev_row.get('ma_bfq_60', 0) if prev_row is not None else 0

    # 长期地基：MA60 是否上翘
    if ma60 > 0 and prev_ma60 > 0:
        if ma60 > prev_ma60 * 1.005:
            ma_trend += 5  # MA60上翘>0.5%
        elif ma60 >= prev_ma60 * 0.995:
            ma_trend += 3  # MA60走平

    # 多头排列
    if ma5 > ma10 > ma20 > ma60 > 0:
        ma_trend += 5  # 完美多头排列
    elif ma5 > ma10 > ma20 > 0:
        ma_trend += 3  # 短期强势

    # 趋势强度：收盘价与MA20偏离度
    if ma20 > 0:
        dev_ma20 = (close / ma20 - 1) * 100
        detail['dev_ma20_pct'] = round(dev_ma20, 2)
        if dev_ma20 > 20:
            ma_trend += 5
        elif dev_ma20 > 10:
            ma_trend += 3
        else:
            ma_trend += 0

    # 短期启动：收盘站上MA5且MA5向上
    prev_ma5 = prev_row.get('ma_bfq_5', 0) if prev_row is not None else 0
    if close > ma5 > 0 and ma5 >= prev_ma5:
        ma_trend += 5
    elif close > ma5 > 0:
        ma_trend += 2

    score['ma_trend'] = min(ma_trend, 20)
    detail['ma5'] = round(ma5, 2)
    detail['ma10'] = round(ma10, 2)
    detail['ma20'] = round(ma20, 2)
    detail['ma60'] = round(ma60, 2)

    # ── 2. MACD强度分 (0~25) ─────────────────────────────────────────
    macd_score = 0
    dif = row.get('macd_dif_bfq', 0) or 0
    dea = row.get('macd_dea_bfq', 0) or 0
    macd_bar = row.get('macd_bfq', 0) or 0
    prev_dif = prev_row.get('macd_dif_bfq', 0) if prev_row is not None else 0
    prev_dea = prev_row.get('macd_dea_bfq', 0) if prev_row is not None else 0
    prev_macd_bar = prev_row.get('macd_bfq', 0) if prev_row is not None else 0

    # 水上结构
    if dif > 0 and dea > 0:
        macd_score += 5

    # 二次加速：拒绝死叉或二次张嘴
    # DIF>DEA（金叉）且 DIF 向上张口（今日DIF > 前日DIF）
    if dif > dea and dif > prev_dif:
        macd_score += 10  # 零轴上金叉+张口
    elif dif > dea:
        macd_score += 6  # 零轴上金叉（未张口）

    # 动能强度：MACD柱
    macd_bar_val = macd_bar * 2  # 放大后的柱值
    if macd_bar_val > 3.0:
        macd_score += 5
    elif macd_bar_val > 1.0:
        macd_score += 3

    # 额外加分：今日 DIF 上穿 DEA（金叉日）
    prev_dif_above = prev_dif > prev_dea if prev_dif != 0 and prev_dea != 0 else False
    if dif > dea and not prev_dif_above:
        macd_score = min(macd_score + 3, 25)  # 金叉日额外加分

    score['macd_strength'] = min(macd_score, 25)
    detail['dif'] = round(dif, 4)
    detail['dea'] = round(dea, 4)
    detail['macd_bar'] = round(macd_bar, 4)

    # ── 3. RSI强势分 (0~20) ──────────────────────────────────────────
    rsi_score = 0
    rsi6 = row.get('rsi_bfq_6', 0) or 0
    rsi12 = row.get('rsi_bfq_12', 0) or 0
    rsi24 = row.get('rsi_bfq_24', 0) or 0
    prev_rsi6 = prev_row.get('rsi_bfq_6', 0) if prev_row is not None else 0

    # RSI黄金区域（65~85 = 强势超买区 = 主升浪特征）
    if 65 <= rsi6 <= 85:
        rsi_score += 10
    elif 50 <= rsi6 < 65:
        rsi_score += 6
    elif rsi6 > 85 or (0 < rsi6 < 50):
        rsi_score += 3

    # 短期>中期>长期
    if rsi6 > rsi12 > rsi24 > 0:
        rsi_score += 5
    elif rsi6 > rsi12 > 0:
        rsi_score += 3

    # 当日RSI勾头向上（拒绝死叉）
    if rsi6 > prev_rsi6 and rsi6 > 50:
        rsi_score += 5
    elif rsi6 >= prev_rsi6:
        rsi_score += 2

    score['rsi_strength'] = min(rsi_score, 20)
    detail['rsi6'] = round(rsi6, 1)
    detail['rsi12'] = round(rsi12, 1)
    detail['rsi24'] = round(rsi24, 1)

    # ── 4. 量能健康分 (0~20) ─────────────────────────────────────────
    vol_score = 0
    vol_ratio = row.get('volume_ratio', 0) or 0
    turnover = row.get('turnover_rate', 0) or 0
    pct_chg = row.get('pct_chg', 0) or 0
    vol = row['vol']
    prev_vol = prev_row['vol'] if prev_row is not None else 1

    # 量比（缩量加速是控盘特征）
    if 0.8 < vol_ratio < 2.0:
        vol_score += 10  # 完美缩量加速
    elif 2.0 <= vol_ratio < 4.0:
        vol_score += 6  # 倍量，次选
    elif 0.5 <= vol_ratio <= 0.8 or vol_ratio >= 4.0:
        vol_score += 2

    # 换手率（5%~15%为最优区间）
    if 5 <= turnover <= 15:
        vol_score += 5
    elif 3 <= turnover < 5 or 15 < turnover <= 20:
        vol_score += 3

    # 价量关系：阳线+放量
    if pct_chg > 0 and vol > prev_vol:
        vol_score += 5
    elif pct_chg > 0:
        vol_score += 3

    score['volume_health'] = min(vol_score, 20)
    detail['vol_ratio'] = round(vol_ratio, 2)
    detail['turnover'] = round(turnover, 2)

    # ── 5. 价格形态分 (0~15) ─────────────────────────────────────────
    price_score = 0
    open_p = row['open']
    high_p = row['high']
    low_p = row['low']
    # 实体涨幅
    body_pct = (close - open_p) / open_p * 100 if open_p > 0 else 0

    # K线实体
    if body_pct > 5:
        price_score += 10
    elif body_pct > 3:
        price_score += 6

    # BOLL位置
    boll_upper = row.get('boll_upper_bfq', 0) or 0
    boll_mid = row.get('boll_mid_bfq', 0) or 0
    if boll_upper > 0 and close >= boll_upper * 0.98:
        price_score += 5  # 突破/紧贴上轨
    elif boll_upper > 0 and close > boll_mid:
        price_score += 3  # 在中轨与上轨之间

    score['price_pattern'] = min(price_score, 15)
    detail['body_pct'] = round(body_pct, 2)
    detail['boll_upper'] = round(boll_upper, 2)
    detail['boll_mid'] = round(boll_mid, 2)

    # ── 总分 ──────────────────────────────────────────────────────────
    total = score['ma_trend'] + score['macd_strength'] + score['rsi_strength'] + score['volume_health'] + score['price_pattern']
    score['total'] = min(total, 100)

    # 等级
    if total >= 80:
        grade = 'S'
    elif total >= 70:
        grade = 'A'
    elif total >= 60:
        grade = 'B'
    elif total >= 50:
        grade = 'C'
    else:
        grade = 'D'

    score['grade'] = grade
    score['detail'] = detail

    return score


# =========================
# 严苛版中线买点评分公式
# =========================

def calc_strict_signal_score(row: pd.Series, prev_row: pd.Series = None,
                              prev_5_scores: List[float] = None,
                              macd_golden_cross: bool = False,
                              pullback_confirmed: bool = False,
                              recent_high_60d: float = 0,
                              dmi_pdi: float = 0,
                              dmi_mdi: float = 0,
                              dmi_adx: float = 0) -> Dict:
    """
    更严苛的中线买点评分（适用于趋势中期持有，非次日短线）

    7项新增严苛过滤（评分≥85分基础上）：
    1. 评分门槛≥85分（原版80分）
    2. 二次回踩确认：信号前5日内有缩量回踩MA10或MA20（加分项，无则降权）
    3. 量能健康约束：当日量比必须在0.5~2.5之间
    4. 趋势连续性：前5日评分均值≥60
    5. MACD零轴上方张口：DIF>0 且 DEA>0 且 DIF>DEA
    6. RSI健康区间：当日RSI6在60~85区间（排除<60太弱、>85过热）
    7. MACD柱强度：不做硬过滤（弱MACD柱的信号可通过后续放量长阳确认入场）
        MACD柱≥3.0加分，≥5.0加更多分

    额外加分项（来自二波形态系统回测成果）：
    - DMI趋势确认：PDI>MDI且ADX>25 → +3分
    - 上方空间充足(距60日前高<10%) → +3分（无套牢盘压力）
    - 上方空间不足(距60日前高>20%) → -5分（有套牢盘压力）
    """
    # 调用原版基础评分
    base = calc_signal_score(row, prev_row)

    score = copy.deepcopy(base)
    score['strict_pass'] = False
    score['strict_reasons'] = []
    score['strict_filters'] = {
        'score_85': False,
        'pullback_confirmed': False,
        'volume_healthy': False,
        'trend_continuous': False,
        'macd_zero_axis': False,
        'rsi_healthy': False,
    }
    score['strict_bonus'] = 0  # 严苛加分
    score['upside_penalty'] = 0  # 上方空间惩罚

    # ── 严苛过滤 1：评分门槛≥85分 ──────────────────────────────────
    if base['total'] < 85:
        score['strict_reasons'].append(f"评分{base['total']:.0f}<85分")
        return score
    score['strict_filters']['score_85'] = True

    # ── 严苛过滤 2：二次回踩确认（加分项） ──────────────────────────
    if pullback_confirmed:
        score['strict_filters']['pullback_confirmed'] = True
        score['strict_bonus'] += 5  # 有二次回踩确认，加5分
    # else: 不阻止，只是不加分

    # ── 严苛过滤 3：量能健康约束（0.5~2.5） ─────────────────────────
    vol_ratio = row.get('volume_ratio', 0) or 0
    if not (0.5 <= vol_ratio <= 2.5):
        score['strict_reasons'].append(f"量比{vol_ratio:.2f}不在0.5~2.5区间")
        return score
    score['strict_filters']['volume_healthy'] = True

    # ── 严苛过滤 4：趋势连续性（前5日评分均值≥60） ──────────────────
    if prev_5_scores and len(prev_5_scores) >= 5:
        avg_5 = np.mean(prev_5_scores)
        if avg_5 < 60:
            score['strict_reasons'].append(f"前5日均分{avg_5:.1f}<60")
            return score
        score['strict_filters']['trend_continuous'] = True
    elif prev_5_scores:
        # 数据不足5日时，放宽要求
        if base['total'] < 88:
            score['strict_reasons'].append("趋势数据不足5日且评分<88")
            return score
        score['strict_filters']['trend_continuous'] = True
    else:
        # 无历史评分时，跳过此项
        score['strict_filters']['trend_continuous'] = True

    # ── 严苛过滤 5：MACD零轴上方张口 ─────────────────────────────────
    dif = row.get('macd_dif_bfq', 0) or 0
    dea = row.get('macd_dea_bfq', 0) or 0
    prev_dif = prev_row.get('macd_dif_bfq', 0) if prev_row is not None else 0
    prev_dea = prev_row.get('macd_dea_bfq', 0) if prev_row is not None else 0

    if not (dif > 0 and dea > 0 and dif > dea):
        score['strict_reasons'].append("MACD未在零轴上方张口")
        return score
    score['strict_filters']['macd_zero_axis'] = True

    # 检查MACD开口是否继续扩大（加分项）
    gap = dif - dea
    prev_gap = prev_dif - prev_dea
    if gap > prev_gap > 0:
        score['strict_bonus'] += 3  # MACD开口继续扩大，加3分

    # ── 严苛过滤 6：RSI健康区间（60~85） ───────────────────────────
    rsi6 = row.get('rsi_bfq_6', 0) or 0
    if not (60 <= rsi6 <= 85):
        if rsi6 < 60:
            score['strict_reasons'].append(f"RSI6={rsi6:.1f}<60太弱")
        else:
            score['strict_reasons'].append(f"RSI6={rsi6:.1f}>85过热")
        return score
    score['strict_filters']['rsi_healthy'] = True

    # ── 追加过滤：MACD柱强度（10日大涨的核心预测因子）─────────────────
    # 分析406个信号发现：MACD柱≥3.0的信号，10日≥25%概率显著更高
    # 注意：MACD柱弱不直接过滤信号（早期信号可通过后面的趋势确认入场）
    # 但记录分值供MOMENTUM入场判断使用
    macd_bar = row.get('macd_bfq', 0) or 0
    if macd_bar >= 5.0:
        score['strict_bonus'] += 3
        score['strict_reasons'].append(f"MACD柱强({macd_bar:.2f}≥5.0)")
    elif macd_bar >= 3.0:
        score['strict_bonus'] += 1
        score['strict_reasons'].append(f"MACD柱尚可({macd_bar:.2f}≥3.0)")
    elif macd_bar < 1.0:
        score['strict_reasons'].append(f"MACD柱偏弱({macd_bar:.2f}<1.0)")

    # ── 加分/惩罚（来自二波形态系统）─────────────────────────────────
    # DMI趋势确认：PDI>MDI多头 + ADX>25强趋势 = 趋势确认
    if dmi_pdi > dmi_mdi > 0 and dmi_adx > 25:
        score['strict_bonus'] += 3
        score['strict_reasons'].append(f"DMI确认(PDI{dmi_pdi:.0f}>MDI{dmi_mdi:.0f},ADX{dmi_adx:.0f})")

    # 上方空间评估：距前高>20%=套牢盘重，直接过滤
    close = row['close']
    if recent_high_60d > 0 and close > 0:
        gap_to_high = (recent_high_60d - close) / close
        if gap_to_high > 0.20:
            score['strict_reasons'].append(f"上方压力(距60日高{gap_to_high*100:.0f}%>20%)")
            return score
        elif gap_to_high < 0.10:
            score['strict_bonus'] += 3
            score['strict_reasons'].append(f"上方充足(距前高{gap_to_high*100:.0f}%)")
    else:
        # 无前高数据时放宽
        pass

    # ── 全部严苛过滤通过 ────────────────────────────────────────────
    score['strict_pass'] = True
    # 基础分 + 严苛加分
    score['total'] = base['total'] + score['strict_bonus']
    score['strict_reasons'].append(f"6项严苛过滤通过(加分{score['strict_bonus']}分)")

    return score


def check_pullback_confirmed(df: pd.DataFrame, signal_idx: int,
                               lookback: int = 5) -> bool:
    """
    检查信号日前N日内是否有缩量回踩MA10/MA20的确认

    Returns:
        True if pullback is confirmed within lookback days before signal
    """
    start_idx = max(0, signal_idx - lookback)
    signal_row = df.iloc[signal_idx]
    signal_vol = signal_row.get('vol', 0)
    signal_ma10 = signal_row.get('ma_bfq_10', 0) or 0
    signal_ma20 = signal_row.get('ma_bfq_20', 0) or 0

    for i in range(start_idx, signal_idx):
        row = df.iloc[i]
        vol_ratio = row.get('volume_ratio', 0) or 0
        close = row.get('close', 0) or 0
        ma10 = row.get('ma_bfq_10', 0) or 0
        ma20 = row.get('ma_bfq_20', 0) or 0

        # 缩量：量比<0.8
        if vol_ratio >= 0.8:
            continue

        # 回踩MA10或MA20（价格在线下方5%以内）
        if ma10 > 0 and close >= ma10 * 0.95 and close <= ma10 * 1.02:
            return True
        if ma20 > 0 and close >= ma20 * 0.95 and close <= ma20 * 1.02:
            return True

    return False


# =========================
# 回测核心
# =========================

def backtest_stock(df: pd.DataFrame, ts_code: str,
                   signal_threshold: int = 80,
                   recent_days: int = None) -> Dict:
    """
    对单只股票进行回测

    Args:
        df: 历史K线数据（按trade_date升序，至少120天）
        ts_code: 股票代码
        signal_threshold: 信号阈值，默认80分（S级）

    Returns:
        回测统计结果字典
    """
    if df is None or len(df) < 30:
        return {}

    # 如果指定了recent_days，只取最近N天（但保留足够的历史数据用于计算指标）
    if recent_days is not None and recent_days > 0:
        df = df.tail(recent_days + 60).reset_index(drop=True)  # 多取60天保证指标计算正确

    # 计算 MA60 变化序列（用于判断MA60是否上翘）
    ma60_arr = df['ma_bfq_60'].values
    dif_arr = df['macd_dif_bfq'].values
    dea_arr = df['macd_dea_bfq'].values

    all_scores = []
    for i in range(len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1] if i > 0 else None

        # 传入MA60和MACD序列（用于评分函数）
        sc = calc_signal_score(row, prev_row)
        sc['trade_date'] = row['trade_date']
        sc['close'] = row['close']
        sc['pct_chg'] = row.get('pct_chg', 0)
        sc['vol'] = row['vol']
        sc['vol_ratio'] = row.get('volume_ratio', 0)
        sc['turnover'] = row.get('turnover_rate', 0)
        sc['rsi6'] = row.get('rsi_bfq_6', 0)
        sc['macd_bar'] = row.get('macd_bfq', 0)
        all_scores.append(sc)

    # 转DataFrame方便分析
    scores_df = pd.DataFrame(all_scores)

    # 找出所有 S级 信号日
    sig_mask = scores_df['total'] >= signal_threshold
    sig_indices = scores_df[sig_mask].index.tolist()

    # 计算每次信号的次日买入后阶段涨幅
    future_windows = [1, 5, 10, 20]
    signal_results = []

    for idx in sig_indices:
        entry_idx = idx + 1
        if entry_idx >= len(df):
            continue
        entry_date = df.iloc[entry_idx]['trade_date']
        entry_price = df.iloc[entry_idx]['close']
        signal_date = df.iloc[idx]['trade_date']
        signal_price = df.iloc[idx]['close']

        sig_data = {
            'ts_code': ts_code,
            'signal_date': signal_date,
            'entry_date': entry_date,
            'entry_price': entry_price,
            'signal_price': signal_price,
            'signal_score': scores_df.loc[idx, 'total'],
            'grade': scores_df.loc[idx, 'grade'],
            'signal_pct_chg': round(scores_df.loc[idx, 'pct_chg'], 2),
            'signal_rsi6': round(scores_df.loc[idx, 'rsi6'], 1),
            'signal_macd_bar': round(scores_df.loc[idx, 'macd_bar'], 4),
            'signal_vol_ratio': round(scores_df.loc[idx, 'vol_ratio'], 2),
        }

        for w in future_windows:
            future_idx = entry_idx + w
            if future_idx >= len(df):
                future_idx = len(df) - 1
            future_price = df.iloc[future_idx]['close']
            future_date = df.iloc[future_idx]['trade_date']
            ret = (future_price / entry_price - 1) * 100
            sig_data[f'return_{w}d'] = round(ret, 2)
            sig_data[f'high_after_{w}d'] = round(
                (df.iloc[entry_idx:future_idx + 1]['high'].max() / entry_price - 1) * 100, 2
            )

        signal_results.append(sig_data)

    # 汇总统计
    summary = {
        'ts_code': ts_code,
        'total_days': len(df),
        'score_range': f"{scores_df['total'].min():.0f}~{scores_df['total'].max():.0f}",
        'score_mean': round(scores_df['total'].mean(), 1),
        'score_median': round(scores_df['total'].median(), 1),
        'signal_count': len(signal_results),
        's_count': len(scores_df[scores_df['grade'] == 'S']),
        'a_count': len(scores_df[scores_df['grade'] == 'A']),
    }

    # S级信号收益统计
    if signal_results:
        for w in future_windows:
            rets = [s[f'return_{w}d'] for s in signal_results if f'return_{w}d' in s]
            wins = [r for r in rets if r > 0]
            max_rets = [s[f'high_after_{w}d'] for s in signal_results if f'high_after_{w}d' in s]
            summary[f'{w}d_avg'] = round(np.mean(rets), 2) if rets else 0
            summary[f'{w}d_win_rate'] = round(len(wins) / len(rets) * 100, 1) if rets else 0
            summary[f'{w}d_max'] = round(max(rets), 2) if rets else 0
            summary[f'{w}d_min'] = round(min(rets), 2) if rets else 0
            summary[f'{w}d_avg_high'] = round(np.mean(max_rets), 2) if max_rets else 0

    summary['signals'] = signal_results

    return summary, scores_df


def backtest_stock_strict(df: pd.DataFrame, ts_code: str,
                          recent_days: int = None) -> Dict:
    """
    对单只股票进行严苛版回测（6项严苛过滤）

    Returns:
        回测统计结果字典
    """
    if df is None or len(df) < 30:
        return {}

    # 如果指定了recent_days，只取最近N天
    if recent_days is not None and recent_days > 0:
        df = df.tail(recent_days + 60).reset_index(drop=True)

    # 计算所有日期的原版评分
    all_scores = []
    for i in range(len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1] if i > 0 else None
        sc = calc_signal_score(row, prev_row)
        sc['trade_date'] = row['trade_date']
        sc['close'] = row['close']
        all_scores.append(sc)

    scores_df = pd.DataFrame(all_scores)

    # 计算前5日评分序列（用于趋势连续性判断）
    prev_5_scores_list = []
    strict_signals = []

    for i in range(len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1] if i > 0 else None

        # 获取前5日评分
        prev_5 = prev_5_scores_list[max(0, len(prev_5_scores_list) - 5):]

        # 检查二次回踩确认
        pullback = check_pullback_confirmed(df, i, lookback=5)

        # 计算60日最高价（上方空间评估）
        high_60d = df.iloc[max(0, i-60):i+1]['close'].max()
        dmi_p = row.get('dmi_pdi_hfq', 0) or 0
        dmi_m = row.get('dmi_mdi_hfq', 0) or 0
        dmi_a = row.get('dmi_adx_hfq', 0) or 0

        # 严苛版评分
        sc = calc_strict_signal_score(row, prev_row, prev_5, pullback_confirmed=pullback,
                                       recent_high_60d=high_60d,
                                       dmi_pdi=dmi_p, dmi_mdi=dmi_m, dmi_adx=dmi_a)
        sc['trade_date'] = row['trade_date']
        sc['close'] = row['close']
        sc['pct_chg'] = row.get('pct_chg', 0)
        sc['vol_ratio'] = row.get('volume_ratio', 0)
        sc['turnover'] = row.get('turnover_rate', 0)
        sc['rsi6'] = row.get('rsi_bfq_6', 0)
        sc['macd_bar'] = row.get('macd_bfq', 0)

        # 严苛信号
        if sc.get('strict_pass', False):
            entry_idx = i + 1
            if entry_idx >= len(df):
                continue

            entry_date = df.iloc[entry_idx]['trade_date']
            entry_price = df.iloc[entry_idx]['close']
            future_windows = [1, 5, 10, 20]
            sig_data = {
                'ts_code': ts_code,
                'signal_date': row['trade_date'],
                'entry_date': entry_date,
                'entry_price': entry_price,
                'signal_score': sc['total'],
                'signal_pct_chg': round(sc['pct_chg'], 2),
                'signal_rsi6': round(sc['rsi6'], 1),
                'signal_vol_ratio': round(sc['vol_ratio'], 2),
                'strict_filters': sc.get('strict_filters', {}),
            }

            for w in future_windows:
                future_idx = entry_idx + w
                if future_idx >= len(df):
                    future_idx = len(df) - 1
                future_price = df.iloc[future_idx]['close']
                ret = (future_price / entry_price - 1) * 100
                sig_data[f'return_{w}d'] = round(ret, 2)
                sig_data[f'high_after_{w}d'] = round(
                    (df.iloc[entry_idx:future_idx + 1]['high'].max() / entry_price - 1) * 100, 2
                )

            strict_signals.append(sig_data)

        # 记录评分用于下一日判断
        prev_5_scores_list.append(sc['total'])

    # 汇总统计
    future_windows = [1, 5, 10, 20]
    summary = {
        'ts_code': ts_code,
        'total_days': len(df),
        'score_range': f"{scores_df['total'].min():.0f}~{scores_df['total'].max():.0f}",
        'score_mean': round(scores_df['total'].mean(), 1),
        'score_median': round(scores_df['total'].median(), 1),
        'signal_count': len(strict_signals),
        's_count': len(scores_df[scores_df['grade'] == 'S']),
        'strict_pass_count': len(strict_signals),
    }

    # 严苛信号收益统计
    if strict_signals:
        for w in future_windows:
            rets = [s[f'return_{w}d'] for s in strict_signals if f'return_{w}d' in s]
            wins = [r for r in rets if r > 0]
            max_rets = [s[f'high_after_{w}d'] for s in strict_signals if f'high_after_{w}d' in s]
            summary[f'{w}d_avg'] = round(np.mean(rets), 2) if rets else 0
            summary[f'{w}d_win_rate'] = round(len(wins) / len(rets) * 100, 1) if rets else 0
            summary[f'{w}d_max'] = round(max(rets), 2) if rets else 0
            summary[f'{w}d_min'] = round(min(rets), 2) if rets else 0
            summary[f'{w}d_avg_high'] = round(np.mean(max_rets), 2) if max_rets else 0

    summary['signals'] = strict_signals

    return summary, scores_df


def _macd_bar_turning(df: pd.DataFrame, idx: int, lookback: int = 3) -> bool:
    """
    MACD柱是否即将拐头向上（绿柱缩短或红柱开始增长）
    用于判断下跌动能是否衰竭
    """
    bar = df.iloc[idx].get('macd_bfq', 0) or 0
    prev_bar = df.iloc[idx - 1].get('macd_bfq', 0) if idx > 0 else bar

    # 情况A：绿柱缩短（负值在减小）—— 最可靠的空方衰竭信号
    if bar < 0 and bar > prev_bar:
        return True
    # 情况B：红柱刚转正（负→正）
    if prev_bar < 0 < bar:
        return True
    return False


def _kdj_j_turning(df: pd.DataFrame, idx: int) -> bool:
    """
    KDJ的J值从超卖区（<0）勾头向上，或从低位金叉
    对强势股浅调非常敏感，很多时候价格还没到MA10，J值已经先触底反弹了
    """
    j = df.iloc[idx].get('kdj_j_bfq', 0) or 0
    prev_j = df.iloc[idx - 1].get('kdj_j_bfq', 0) if idx > 0 else 0
    k = df.iloc[idx].get('kdj_k_bfq', 0) or 0
    d = df.iloc[idx].get('kdj_d_bfq', 0) or 0
    prev_k = df.iloc[idx - 1].get('kdj_k_bfq', 0) if idx > 0 else 0
    prev_d = df.iloc[idx - 1].get('kdj_d_bfq', 0) if idx > 0 else 0

    # 情况A：J值从<0超卖区反弹（最敏感的信号）
    if prev_j < 0 and j > prev_j:
        return True
    # 情况B：J值从低位(<20)勾头且K上穿D（金叉）
    if prev_j < 20 and j > prev_j and prev_k < prev_d and k > d:
        return True
    # 情况C：J值连续下跌后首次拐头（配合其他指标）
    if j > prev_j and j < 30 and prev_j <= prev_j:
        return True
    return False


def _volume_shrunk_to_floor(df: pd.DataFrame, idx: int, lookback: int = 5) -> bool:
    """
    成交量是否缩到地量（调整末端特征）
    当日量比 <= 0.8 且 低于前5日均量
    """
    vol = df.iloc[idx].get('vol', 0) or 1
    vol_ratio = df.iloc[idx].get('volume_ratio', 0) or 1
    if vol_ratio >= 1.0:
        return False
    # 看是否低于前5日均量
    avg_vol_5 = np.mean([df.iloc[k].get('vol', 0) or 0 for k in range(max(0, idx - lookback), idx)])
    if avg_vol_5 > 0 and vol < avg_vol_5 * 0.85:
        return True
    return vol_ratio <= 0.8


def _rsi_turning_up(df: pd.DataFrame, idx: int, oversold_threshold: int = 45) -> bool:
    """
    RSI从低位勾头向上（调整末端反弹信号）
    """
    rsi6 = df.iloc[idx].get('rsi_bfq_6', 0) or 50
    prev_rsi6 = df.iloc[idx - 1].get('rsi_bfq_6', 0) if idx > 0 else 50

    # RSI曾跌到过50以下（确认有调整），且今日勾头向上
    if rsi6 > prev_rsi6:
        return True
    return False


def _boll_lower_support(df: pd.DataFrame, idx: int, tolerance: float = 0.02) -> bool:
    """
    价格是否在BOLL下轨附近获得支撑
    """
    close = df.iloc[idx]['close']
    boll_lower = df.iloc[idx].get('boll_lower_bfq', 0) or 0
    if boll_lower > 0 and close <= boll_lower * (1 + tolerance):
        return True
    return False


def _volume_start_increase(df: pd.DataFrame, idx: int, lookback: int = 3) -> bool:
    """
    地量之后的温和放量（确认反弹启动）
    """
    vol = df.iloc[idx].get('vol', 0) or 0
    prev_vol = df.iloc[idx - 1].get('vol', 0) if idx > 0 else 0
    vol_ratio = df.iloc[idx].get('volume_ratio', 0) or 0

    # 今日放量超过昨日
    if prev_vol > 0 and vol > prev_vol * 1.1 and vol_ratio > 0.5:
        return True
    return False


def _check_momentum_buy(df: pd.DataFrame, signal_idx: int) -> bool:
    """
    检查信号日是否满足趋势突破条件，适合次日直接入场

    主板实证（300只样本分析）：
    - 所有MOMENTUM信号均在60日高点附近（距前高=0%）
    - 均线多头排列率100%
    - 成功(+10日>5%) vs 失败(<0%)的核心差异：
      * 量比: 1.29 vs 0.97 ← 最大区分因子
      * 距MA10: 9.8% vs 13.2% ← 拉太远=过热
      * 信号日涨幅: 5.5% vs 7.8% ← 大涨易冲高回落

    动量因子（满足≥4条即通过）：
    1. MACD柱 ≥ 4.0（动能强劲）
    2. 距60日前高 < 10%（创新高附近，无套牢盘）
    3. MA5乖离 3%~8%（快速上涨通道中，但不过热）
    4. 信号日涨幅 4%~10%（太弱=动能不足，太猛=过热）
    5. MACD开口扩大（今日DIF-DEA > 前日DIF-DEA）
    6. 量比 0.8~1.5（温和放量，非爆量见顶）
    """
    row = df.iloc[signal_idx]
    prev_row = df.iloc[signal_idx - 1] if signal_idx > 0 else None

    hits = 0

    # 1. MACD柱 ≥ 4.0
    macd_bar = row.get('macd_bfq', 0) or 0
    if macd_bar >= 4.0:
        hits += 1

    # 2. 距60日前高 < 10%（所有成功信号均满足）
    high_60d = df.iloc[max(0, signal_idx-60):signal_idx+1]['close'].max()
    close = row['close']
    gap = (high_60d - close) / close if high_60d > 0 and close > 0 else 0
    if gap < 0.10:
        hits += 1

    # 3. MA5乖离 3%~8%
    ma5 = row.get('ma_bfq_5', 0) or 0
    dev_ma5 = (close / ma5 - 1) * 100 if ma5 > 0 else 0
    if 3 <= dev_ma5 <= 8:
        hits += 1

    # 4. 信号日涨幅 4%~10%（太弱<4%不算突破，太猛>10%易冲高回落）
    pct_chg = row.get('pct_chg', 0) or 0
    if 4 <= pct_chg <= 10:
        hits += 1

    # 5. MACD开口扩大
    if prev_row is not None:
        dif = row.get('macd_dif_bfq', 0) or 0
        dea = row.get('macd_dea_bfq', 0) or 0
        prev_dif = prev_row.get('macd_dif_bfq', 0) or 0
        prev_dea = prev_row.get('macd_dea_bfq', 0) or 0
        gap_now = dif - dea
        gap_prev = prev_dif - prev_dea
        if gap_now > gap_prev > 0:
            hits += 1

    # 6. 量比（主板最强区分因子：成功1.29 vs 失败0.97）
    vol_ratio = row.get('volume_ratio', 0) or 0
    if 0.8 <= vol_ratio <= 1.5:
        hits += 1
    elif 1.5 < vol_ratio <= 2.0:
        hits += 0  # 倍量不扣分但不加分
    elif vol_ratio > 2.0:
        hits -= 2  # 爆量=>直接降低评分

    # 前置过滤1：前20日涨幅不超过20%（比之前更严，排除末期加速）
    idx_start = max(0, signal_idx - 20)
    price_20d_ago = df.iloc[idx_start]['close']
    gain_20d = (close / price_20d_ago - 1) * 100 if price_20d_ago > 0 else 0
    if gain_20d > 20:
        return False

    # 前置过滤2：距MA10不能太远（>15%=过度拉伸，回调风险大）
    ma10 = row.get('ma_bfq_10', 0) or 0
    dev_ma10 = (close / ma10 - 1) * 100 if ma10 > 0 else 0
    if dev_ma10 > 15:
        return False

    return hits >= 4


def calc_prior_rally_gain(df: pd.DataFrame, signal_idx: int, lookback: int = 60) -> float:
    """
    计算信号日之前最近一波完整上涨的涨幅

    从信号日往前找60日内最低收盘价（波段起点），
    直接计算到信号日收盘价的涨幅。
    参考 _has_limitup_in_wave 做涨停过滤来剔除弱票。
    """
    if signal_idx <= 0 or signal_idx >= len(df):
        return 0.0

    start_idx = max(0, signal_idx - lookback)
    segment = df.iloc[start_idx:signal_idx + 1]

    if len(segment) < 5:
        return 0.0

    low_idx = segment['close'].idxmin()
    low_price = df.loc[low_idx, 'close']

    if low_price <= 0:
        return 0.0

    signal_price = df.iloc[signal_idx]['close']
    gain = (signal_price / low_price - 1) * 100

    return round(gain, 2)


def _has_limitup_in_wave(df: pd.DataFrame, signal_idx: int, lookback: int = 60) -> bool:
    """
    检查信号日之前的上涨波段中是否包含涨停（涨幅≥9.8%）

    与 calc_prior_rally_gain 使用同样的低点→波峰区间逻辑
    """
    if signal_idx <= 0 or signal_idx >= len(df):
        return False

    start_idx = max(0, signal_idx - lookback)
    segment = df.iloc[start_idx:signal_idx + 1]

    if len(segment) < 5:
        return False

    low_idx_label = segment['close'].idxmin()
    low_pos = df.index.get_loc(low_idx_label)

    # 最低点和信号日之间找波峰（不含信号日本身）
    mid = df.iloc[low_pos:signal_idx]
    if len(mid) < 2:
        return False

    peak_idx_label = mid['close'].idxmax()
    peak_pos = df.index.get_loc(peak_idx_label)

    # 检查低点到波峰之间是否有涨停
    wave = df.iloc[low_pos:peak_pos + 1]
    return (wave['pct_chg'].fillna(0) >= 9.8).any()


def find_pullback_entry(df: pd.DataFrame, signal_idx: int,
                        max_lookahead: int = 15,
                        market_type: str = 'dual') -> Optional[Tuple[Optional[int], str]]:
    """
    信号发出后，等待深度回调洗盘后，趋势确认入场

    核心逻辑：
    1. 信号发出后，价格必须跌破MA20（深度回调洗盘）
    2. 在MA20下方找到底部多指标共振点
    3. 底部确认后，等待放量长阳站上MA20 → 趋势确认入场

    趋势确认4项条件（缺一不可）：
    - 大阳线涨幅 ≥4%（强势突破K线）
    - 量比 ≥1.3（资金进场确认）
    - KDJ J>K 且 J>50（动能配合）
    - 回撤深度 ≥10%（洗盘充分，非浅回调）
    """
    # 市场类型参数
    is_dual = (market_type == 'dual')
    resonance_threshold = 2 if is_dual else 1

    # 1. 检查价格是否跌破MA20（必须深度回调）
    went_below_ma20 = False
    below_ma20_idx = None
    for j in range(signal_idx + 1, min(signal_idx + max_lookahead + 1, len(df))):
        close = df.iloc[j]['close']
        ma20 = df.iloc[j].get('ma_bfq_20', 0) or 0
        if ma20 > 0 and close < ma20 * 0.98:
            went_below_ma20 = True
            below_ma20_idx = j
            break

    if not went_below_ma20:
        return None, None

    # 2. 在MA20下方找底部多指标共振
    low_idx = None
    for j in range(below_ma20_idx, min(below_ma20_idx + max_lookahead + 1, len(df))):
        kdj_turn = _kdj_j_turning(df, j)
        macd_turning = _macd_bar_turning(df, j)
        vol_floor = _volume_shrunk_to_floor(df, j)
        rsi_up = _rsi_turning_up(df, j)
        boll_support = _boll_lower_support(df, j)

        signals = sum([kdj_turn, macd_turning, vol_floor, rsi_up, boll_support])
        if signals >= resonance_threshold:
            low_idx = j
            break

    if low_idx is None:
        return None, None

    # 3. 底部确认后，找趋势确认入场点
    signal_high = df.iloc[signal_idx].get('high', df.iloc[signal_idx]['close'])
    pullback_low = df.iloc[low_idx]['low'] if 'low' in df.columns else df.iloc[low_idx]['close']
    pullback_depth = (signal_high - pullback_low) / signal_high * 100 if signal_high > 0 else 0

    for j in range(low_idx + 1, min(low_idx + max_lookahead + 1, len(df))):
        close = df.iloc[j]['close']
        ma20 = df.iloc[j].get('ma_bfq_20', 0) or 0
        if ma20 <= 0 or close <= ma20 * 1.01:
            continue

        pct_chg = df.iloc[j].get('pct_chg', 0) or 0
        vol_ratio = df.iloc[j].get('volume_ratio', 0) or 0
        kdj_j = df.iloc[j].get('kdj_bfq', 0) or 0
        kdj_k = df.iloc[j].get('kdj_k_bfq', 0) or 0

        is_big_candle = pct_chg >= 4.0 and vol_ratio >= 1.3
        kdj_ok = kdj_j > kdj_k and kdj_j > 50
        deep_pullback = pullback_depth >= 10.0

        if is_big_candle and kdj_ok and deep_pullback:
            return j, '趋势确认'

    return None, None


def find_trend_confirm_signal(df: pd.DataFrame, idx: int) -> Optional[Dict]:
    """
    检查第idx天是否为"趋势确认"信号日

    趋势确认 = 深度回调后放量长阳站上MA20，确认新趋势启动

    判定条件（缺一不可）：
    1. 当日站上MA20（close > MA20 * 1.01）
    2. 大阳线涨幅 ≥4%
    3. 量比 ≥1.3（放量）
    4. KDJ J>K 且 J>50（动能配合）
    5. 前一波上涨 ≥25%（有趋势基础）
    6. 之前曾深度回调跌破MA20（洗盘充分，回撤≥10%）

    返回信号详情字典，不满足返回None
    """
    if idx < 30 or idx >= len(df):
        return None

    row = df.iloc[idx]
    close = row['close']
    ma20 = row.get('ma_bfq_20', 0) or 0
    ma10 = row.get('ma_bfq_10', 0) or 0
    pct_chg = row.get('pct_chg', 0) or 0
    vol_ratio = row.get('volume_ratio', 0) or 0
    kdj_j = row.get('kdj_bfq', 0) or 0
    kdj_k = row.get('kdj_k_bfq', 0) or 0
    rsi6 = row.get('rsi_bfq_6', 0) or 0

    # 条件1：站上MA20
    if ma20 <= 0 or close <= ma20 * 1.01:
        return None

    # 条件2：大阳线
    if pct_chg < 4.0:
        return None

    # 条件3：量比适当放大（突破不一定需要巨量，1.15以上即可）
    if vol_ratio < 1.3:
        return None

    # 条件4：KDJ多头
    if not (kdj_j > kdj_k and kdj_j > 50):
        return None

    # 条件5：前90天内该波段必须有涨停，且涨幅在30~80%
    prior_gain = calc_prior_rally_gain(df, idx, lookback=90)
    if prior_gain < 30.0 or prior_gain > 80.0:
        return None
    if not _has_limitup_in_wave(df, idx, lookback=90):
        return None

    # 条件6：之前曾深度回调跌破MA20（往前20天内）
    went_below_ma20 = False
    pullback_low = close
    signal_high = close
    for j in range(max(0, idx - 20), idx):
        prev_close = df.iloc[j]['close']
        prev_ma20 = df.iloc[j].get('ma_bfq_20', 0) or 0
        if prev_ma20 > 0 and prev_close < prev_ma20 * 0.98:
            went_below_ma20 = True
        if prev_close < pullback_low:
            pullback_low = prev_close
        if prev_close > signal_high:
            signal_high = prev_close

    if not went_below_ma20:
        return None

    # 回撤深度
    pullback_depth = (signal_high - pullback_low) / signal_high * 100 if signal_high > 0 else 0
    if pullback_depth < 10.0:
        return None

    above_ma20_pct = (close / ma20 - 1) * 100

    # 条件7：距MA20不能过远（追高）也不能过近（弱反）
    if above_ma20_pct < 5.0 or above_ma20_pct > 25.0:
        return None

    # 条件8：RSI不能极度超买（排除赶顶）
    if rsi6 >= 85:
        return None

    return {
        'signal_date': str(row['trade_date']),
        'signal_close': round(close, 2),
        'signal_score': round(pct_chg + vol_ratio, 0),
        'pullback_depth': round(pullback_depth, 1),
        'pct_chg': round(pct_chg, 2),
        'vol_ratio': round(vol_ratio, 2),
        'rsi6': round(rsi6, 1),
        'kdj_j': round(kdj_j, 1),
        'ma20': round(ma20, 2),
        'ma10': round(ma10, 2),
        'above_ma20_pct': round(above_ma20_pct, 2),
    }


def backtest_stock_strict_pullback(df: pd.DataFrame, ts_code: str,
                                    recent_days: int = None,
                                    max_lookahead: int = 15) -> Dict:
    """
    趋势确认信号回测

    直接扫描每一天是否满足趋势确认条件，满足则次日入场
    """
    if df is None or len(df) < 30:
        return {}

    if recent_days is not None and recent_days > 0:
        df = df.tail(recent_days + 60).reset_index(drop=True)

    strict_signals = []

    for i in range(30, len(df)):
        sig = find_trend_confirm_signal(df, i)
        if sig is None:
            continue

        # 次日入场
        entry_idx = i + 1
        if entry_idx >= len(df):
            continue

        entry_date = df.iloc[entry_idx]['trade_date']
        entry_price = df.iloc[entry_idx]['open']
        future_windows = [1, 5, 10, 20]

        sig_data = {
            'ts_code': ts_code,
            'signal_date': sig['signal_date'],
            'entry_date': str(entry_date),
            'entry_price': entry_price,
            'signal_score': sig['signal_score'],
            'signal_pct_chg': sig['pct_chg'],
            'signal_rsi6': sig['rsi6'],
            'signal_vol_ratio': sig['vol_ratio'],
            'pullback_depth': sig['pullback_depth'],
            'above_ma20_pct': sig['above_ma20_pct'],
            'kdj_j': sig['kdj_j'],
            'entry_method': '趋势确认',
            'signal_close': sig['signal_close'],
        }

        for w in future_windows:
            future_idx = entry_idx + w
            if future_idx >= len(df):
                future_idx = len(df) - 1
            future_price = df.iloc[future_idx]['close']
            ret = (future_price / entry_price - 1) * 100
            sig_data[f'return_{w}d'] = round(ret, 2)
            sig_data[f'high_after_{w}d'] = round(
                (df.iloc[entry_idx:future_idx + 1]['high'].max() / entry_price - 1) * 100, 2
            )

        strict_signals.append(sig_data)

    # 汇总统计
    future_windows = [1, 5, 10, 20]
    summary = {
        'ts_code': ts_code,
        'total_days': len(df),
        'score_range': f"{min([s['signal_score'] for s in strict_signals], default=0):.0f}~{max([s['signal_score'] for s in strict_signals], default=0):.0f}",
        'score_mean': round(np.mean([s['signal_score'] for s in strict_signals]), 1) if strict_signals else 0,
        'score_median': round(np.median([s['signal_score'] for s in strict_signals]), 1) if strict_signals else 0,
        'signal_count': len(strict_signals),
        's_count': len(strict_signals),
        'strict_pass_count': len(strict_signals),
        'entry_method': '趋势确认',
    }

    if strict_signals:
        for w in future_windows:
            rets = [s[f'return_{w}d'] for s in strict_signals if f'return_{w}d' in s]
            wins = [r for r in rets if r > 0]
            max_rets = [s[f'high_after_{w}d'] for s in strict_signals if f'high_after_{w}d' in s]
            summary[f'{w}d_avg'] = round(np.mean(rets), 2) if rets else 0
            summary[f'{w}d_win_rate'] = round(len(wins) / len(rets) * 100, 1) if rets else 0
            summary[f'{w}d_max'] = round(max(rets), 2) if rets else 0
            summary[f'{w}d_min'] = round(min(rets), 2) if rets else 0
            summary[f'{w}d_avg_high'] = round(np.mean(max_rets), 2) if max_rets else 0

    summary['signals'] = strict_signals

    return summary, pd.DataFrame()


def backtest_stock_strict_deduped(df: pd.DataFrame, ts_code: str,
                                   recent_days: int = None) -> Dict:
    """
    严苛版 + 20天信号去重
    先用6项严苛过滤筛选信号，再每20天只保留1个最高分信号
    """
    result, scores_df = backtest_stock_strict(df, ts_code, recent_days=recent_days)
    if not result:
        return {}, scores_df

    raw_signals = result.get('signals', [])
    raw_count = len(raw_signals)

    # 20天去重
    deduped = deduplicate_20d_signals(raw_signals)
    dedup_count = raw_count - len(deduped)

    result['signal_count'] = len(deduped)
    result['raw_signal_count'] = raw_count
    result['dedup_count'] = dedup_count
    result['signals'] = deduped

    # 重新计算收益统计
    future_windows = [1, 5, 10, 20]
    if deduped:
        for w in future_windows:
            rets = [s[f'return_{w}d'] for s in deduped if f'return_{w}d' in s]
            wins = [r for r in rets if r > 0]
            max_rets = [s[f'high_after_{w}d'] for s in deduped if f'high_after_{w}d' in s]
            result[f'{w}d_avg'] = round(np.mean(rets), 2) if rets else 0
            result[f'{w}d_win_rate'] = round(len(wins) / len(rets) * 100, 1) if rets else 0
            result[f'{w}d_max'] = round(max(rets), 2) if rets else 0
            result[f'{w}d_min'] = round(min(rets), 2) if rets else 0
            result[f'{w}d_avg_high'] = round(np.mean(max_rets), 2) if max_rets else 0
    else:
        for w in future_windows:
            result[f'{w}d_avg'] = 0
            result[f'{w}d_win_rate'] = 0
            result[f'{w}d_max'] = 0
            result[f'{w}d_min'] = 0
            result[f'{w}d_avg_high'] = 0

    return result, scores_df


def print_strict_deduped_report(dedup_results: List[Dict], output_file: str = None):
    """打印严苛版+20天去重回测报告"""

    lines = []
    lines.append("=" * 80)
    lines.append("严苛版+20天去重 中线买点 回测报告")
    lines.append(f"回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"过滤条件: 评分≥85 + 6项严苛过滤 + 20天去重")
    lines.append("=" * 80)

    if not dedup_results:
        lines.append("\n[警告] 无信号")
        report_text = "\n".join(lines)
        print(report_text)
        return report_text

    grand_total = sum(r.get('signal_count', 0) for r in dedup_results)
    grand_raw = sum(r.get('raw_signal_count', 0) for r in dedup_results)
    grand_dedup = sum(r.get('dedup_count', 0) for r in dedup_results)

    lines.append(f"\n总计扫描: {len(dedup_results)} 只股票")
    lines.append(f"累计交易日: {sum(r.get('total_days', 0) for r in dedup_results)} 天")
    lines.append(f"去重前信号: {grand_raw} 个")
    lines.append(f"去重后信号: {grand_total} 个 (过滤掉 {grand_dedup} 个)")
    lines.append("")

    lines.append("─" * 80)
    lines.append("一、各股票评分统计")
    lines.append("-" * 80)
    lines.append(f"{'代码':<14} {'评分范围':>12} {'均值':>6} {'S级':>4} {'去重前':>6} {'信号数':>6} {'去重':>5}")
    lines.append("-" * 80)

    for res in dedup_results:
        lines.append(
            f"{res['ts_code']:<14} {res['score_range']:>12} {res['score_mean']:>6.1f} "
            f"{res['s_count']:>4} {res['raw_signal_count']:>6} {res['signal_count']:>6} {res['dedup_count']:>5}"
        )

    lines.append("")
    lines.append("─" * 80)
    lines.append("二、去重后信号汇总")
    lines.append("-" * 80)

    all_signals = []
    for res in dedup_results:
        all_signals.extend(res.get('signals', []))

    if all_signals:
        lines.append(f"{'代码':<14} {'信号日':<12} {'入场日':<12} {'方式':>5} {'评分':>5} "
                     f"{'信号涨幅':>8} {'入场价':>8} "
                     f"{'+5日':>8} {'+10日':>8} {'+20日':>8} {'RSI6':>6}")
        lines.append("-" * 110)

        for sig in all_signals:
            method = sig.get('entry_method', '-')
            lines.append(
                f"{sig['ts_code']:<14} {sig['signal_date']:<12} {sig['entry_date']:<12} "
                f"{method:>5} {sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+7.2f}% {sig['entry_price']:>8.2f} "
                f"{sig.get('return_5d', 0):>+8.2f}% {sig.get('return_10d', 0):>+8.2f}% "
                f"{sig.get('return_20d', 0):>+8.2f}% {sig['signal_rsi6']:>6.1f}"
            )

    lines.append("")
    lines.append("─" * 80)
    lines.append("三、去重后信号 各窗口平均收益统计")
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        all_avgs = [r.get(f'{w}d_avg', 0) for r in dedup_results if r.get(f'{w}d_avg') is not None]
        all_wins = [r.get(f'{w}d_win_rate', 0) for r in dedup_results if r.get(f'{w}d_win_rate') is not None]
        all_maxs = [r.get(f'{w}d_max', 0) for r in dedup_results if r.get(f'{w}d_max') is not None]
        all_mins = [r.get(f'{w}d_min', 0) for r in dedup_results if r.get(f'{w}d_min') is not None]
        all_highs = [r.get(f'{w}d_avg_high', 0) for r in dedup_results if r.get(f'{w}d_avg_high') is not None]

        if all_avgs:
            lines.append(
                f"  +{w}日: 平均收益 {np.mean(all_avgs):>+7.2f}%  "
                f"胜率 {np.mean(all_wins):>6.1f}%  "
                f"最大 {np.mean(all_maxs):>+7.2f}%  "
                f"最小 {np.mean(all_mins):>+7.2f}%  "
                f"盘中最高均值 {np.mean(all_highs):>+7.2f}%"
            )

    lines.append("")
    lines.append("─" * 80)
    lines.append("四、各股票信号明细（已去重）")
    lines.append("-" * 80)

    for res in dedup_results:
        signals = res.get('signals', [])
        if not signals:
            lines.append(f"\n{res['ts_code']}: 无信号 (评分均值={res['score_mean']})")
            continue

        lines.append(f"\n{'='*60}")
        lines.append(f"  {res['ts_code']}  |  评分区间 {res['score_range']}  |  均值 {res['score_mean']}  |  去重后 {res['signal_count']}次 (去重{res['dedup_count']}次)")
        lines.append(f"{'='*60}")
        lines.append(f"  {'信号日':<12} {'方式':>5} {'评分':>5} {'信号涨幅':>8} {'+10日':>7} {'+20日':>7} {'RSI6':>6} {'量比':>5}")
        lines.append(f"  {'-'*65}")

        for sig in signals:
            method = sig.get('entry_method', '-')
            lines.append(
                f"  {sig['signal_date']:<12} {method:>5} {sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+7.2f}% "
                f"{sig.get('return_10d', 0):>+6.2f}% "
                f"{sig.get('return_20d', 0):>+6.2f}% "
                f"{sig['signal_rsi6']:>6.1f} "
                f"{sig['signal_vol_ratio']:>5.2f}"
            )

        if len(signals) > 1:
            for w in [1, 5, 10, 20]:
                rets = [s.get(f'return_{w}d', 0) for s in signals]
                wins = [r for r in rets if r > 0]
                lines.append(
                    f"  → +{w}日 均值={np.mean(rets):>+6.2f}% "
                    f"胜率={len(wins)/len(rets)*100:>5.1f}% "
                    f"最大={max(rets):>+6.2f}% "
                    f"最小={min(rets):>+6.2f}%"
                )

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n报告已保存: {output_file}")

    return report_text


def print_four_way_comparison(
    all_results: List[Tuple[Dict, pd.DataFrame]],
    strict_results: List[Dict],
    opt_results: List[Dict],
    dedup_results: List[Dict],
    output_file: str = None
):
    """四版本对比：原版 vs 严苛版 vs 10日优化版 vs 严苛版+去重"""

    lines = []
    lines.append("=" * 80)
    lines.append("四版本中线买点回测对比报告")
    lines.append(f"回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 80)

    def get_stats(results, is_orig=False):
        stats = {}
        if is_orig:
            total_sigs = sum(r.get('signal_count', 0) for r, _ in results)
            for w in [1, 5, 10, 20]:
                all_avgs = [r.get(f'{w}d_avg', 0) for r, _ in results if r.get(f'{w}d_avg') is not None]
                all_wins = [r.get(f'{w}d_win_rate', 0) for r, _ in results if r.get(f'{w}d_win_rate') is not None]
                all_highs = [r.get(f'{w}d_avg_high', 0) for r, _ in results if r.get(f'{w}d_avg_high') is not None]
                stats[f'{w}d_avg'] = np.mean(all_avgs) if all_avgs else 0
                stats[f'{w}d_win'] = np.mean(all_wins) if all_wins else 0
                stats[f'{w}d_high'] = np.mean(all_highs) if all_highs else 0
        else:
            total_sigs = sum(r.get('signal_count', 0) for r in results)
            for w in [1, 5, 10, 20]:
                all_avgs = [r.get(f'{w}d_avg', 0) for r in results if r.get(f'{w}d_avg') is not None]
                all_wins = [r.get(f'{w}d_win_rate', 0) for r in results if r.get(f'{w}d_win_rate') is not None]
                all_highs = [r.get(f'{w}d_avg_high', 0) for r in results if r.get(f'{w}d_avg_high') is not None]
                stats[f'{w}d_avg'] = np.mean(all_avgs) if all_avgs else 0
                stats[f'{w}d_win'] = np.mean(all_wins) if all_wins else 0
                stats[f'{w}d_high'] = np.mean(all_highs) if all_highs else 0
        stats['signal_count'] = total_sigs
        return stats

    a = get_stats(all_results, is_orig=True)
    b = get_stats(strict_results)
    c = get_stats(opt_results)
    d = get_stats(dedup_results)

    versions = [
        ('原版(≥80分)', a),
        ('严苛版(6项过滤)', b),
        ('10日优化版(去重)', c),
        ('严苛版+去重', d),
    ]

    lines.append("")
    lines.append("─" * 80)
    lines.append("一、信号数量对比")
    lines.append("-" * 80)
    for name, s in versions:
        lines.append(f"  {name:<20} {s['signal_count']:>4}个")

    lines.append("")
    lines.append("─" * 80)
    lines.append("二、各窗口平均收益对比")
    lines.append("-" * 80)
    header = f"{'窗口':>6}"
    for name, _ in versions:
        header += f" {name:>16}"
    lines.append(header)
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        line = f"+{w}日  "
        for _, s in versions:
            line += f"{s.get(f'{w}d_avg', 0):>+15.2f}%"
        lines.append(line)

    lines.append("")
    lines.append("─" * 80)
    lines.append("三、各窗口胜率对比")
    lines.append("-" * 80)
    header = f"{'窗口':>6}"
    for name, _ in versions:
        header += f" {name:>16}"
    lines.append(header)
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        line = f"+{w}日  "
        for _, s in versions:
            line += f"{s.get(f'{w}d_win', 0):>14.1f}%"
        lines.append(line)

    lines.append("")
    lines.append("─" * 80)
    lines.append("四、各窗口盘中最高收益对比")
    lines.append("-" * 80)
    header = f"{'窗口':>6}"
    for name, _ in versions:
        header += f" {name:>16}"
    lines.append(header)
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        line = f"+{w}日  "
        for _, s in versions:
            line += f"{s.get(f'{w}d_high', 0):>+15.2f}%"
        lines.append(line)

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n对比报告已保存: {output_file}")

    return report_text


def deduplicate_20d_signals(signals: List[Dict]) -> List[Dict]:
    """
    信号去重：每只股票每20天只保留评分最高的1个信号
    按signal_date排序后，20天滑动窗口内只保留最高分
    """
    if not signals:
        return []

    sorted_sigs = sorted(signals, key=lambda x: x['signal_date'])

    def date_to_num(d):
        if isinstance(d, str):
            return int(d.replace('-', ''))
        return int(d)

    result = []
    window_start = sorted_sigs[0]['signal_date']
    window_sigs = []

    for sig in sorted_sigs:
        cur_date = date_to_num(sig['signal_date'])
        win_start = date_to_num(window_start)

        if cur_date - win_start <= 20:
            window_sigs.append(sig)
        else:
            if window_sigs:
                best = max(window_sigs, key=lambda x: x.get('signal_score', 0))
                result.append(best)
            window_sigs = [sig]
            window_start = sig['signal_date']

    if window_sigs:
        best = max(window_sigs, key=lambda x: x.get('signal_score', 0))
        result.append(best)

    return result


def calc_10d_optimized_score(row: pd.Series, prev_row: pd.Series = None) -> Dict:
    """
    以10日持有收益为目标的优化版评分公式
    权重：均线25+MACD30+RSI15+量能15+形态15=100分
    """
    score = {}
    detail = {}

    close = row['close']
    ma5 = row.get('ma_bfq_5', 0) or 0
    ma10 = row.get('ma_bfq_10', 0) or 0
    ma20 = row.get('ma_bfq_20', 0) or 0
    ma60 = row.get('ma_bfq_60', 0) or 0

    ma_trend = 0

    if ma60 > 0:
        if prev_row is not None and (prev_row.get('ma_bfq_60', 0) or 0) > 0:
            if ma60 > prev_row.get('ma_bfq_60', 0):
                ma_trend += 8
        else:
            if close > ma60 * 1.08:
                ma_trend += 8
            elif close > ma60:
                ma_trend += 4

    if ma5 > ma10 > ma20 > ma60 and ma60 > 0:
        ma_trend += 8
    elif ma5 > ma10 > ma20 and ma20 > 0:
        ma_trend += 5

    if close >= ma5 and ma5 > 0:
        ma_trend += 4

    if ma20 > 0:
        ma20_dev = (close / ma20 - 1) * 100
        if 5 <= ma20_dev <= 25:
            ma_trend += 5
        elif 25 < ma20_dev <= 35:
            ma_trend += 3
        detail['ma20_dev'] = round(ma20_dev, 1)

    score['ma_trend'] = min(ma_trend, 25)
    detail['ma5'] = round(ma5, 2)
    detail['ma10'] = round(ma10, 2)
    detail['ma20'] = round(ma20, 2)
    detail['ma60'] = round(ma60, 2)

    macd_score = 0
    dif = row.get('macd_dif_bfq', 0) or 0
    dea = row.get('macd_dea_bfq', 0) or 0
    macd_bar = row.get('macd_bfq', 0) or 0
    prev_dif = prev_row.get('macd_dif_bfq', 0) if prev_row is not None else 0
    prev_dea = prev_row.get('macd_dea_bfq', 0) if prev_row is not None else 0
    prev_macd_bar = prev_row.get('macd_bfq', 0) if prev_row is not None else 0

    if dif > 0 and dea > 0:
        macd_score += 6

    if dif > dea:
        macd_score += 8

    if dif > prev_dif > 0:
        macd_score += 8
    elif dif > prev_dif:
        macd_score += 4

    macd_bar_val = abs(macd_bar)
    if macd_bar_val > 0.5 and macd_bar > 0:
        macd_score += 4
    elif macd_bar > 0:
        macd_score += 2

    if macd_bar > prev_macd_bar > 0:
        macd_score += 4
    elif macd_bar > prev_macd_bar:
        macd_score += 2

    score['macd_strength'] = min(macd_score, 30)
    detail['dif'] = round(dif, 4)
    detail['dea'] = round(dea, 4)
    detail['macd_bar'] = round(macd_bar, 4)

    rsi_score = 0
    rsi6 = row.get('rsi_bfq_6', 0) or 0
    rsi12 = row.get('rsi_bfq_12', 0) or 0
    rsi24 = row.get('rsi_bfq_24', 0) or 0
    prev_rsi6 = prev_row.get('rsi_bfq_6', 0) if prev_row is not None else 0

    if 65 <= rsi6 <= 80:
        rsi_score += 7
    elif 55 <= rsi6 < 65:
        rsi_score += 5
    elif 80 < rsi6 <= 88:
        rsi_score += 3
    elif rsi6 > 88 or rsi6 < 50:
        rsi_score += 1

    if rsi6 > rsi12 > rsi24 > 0:
        rsi_score += 4
    elif rsi6 > rsi12 > 0:
        rsi_score += 2

    if rsi6 <= 82 and rsi6 > prev_rsi6:
        rsi_score += 4
    elif rsi6 > prev_rsi6:
        rsi_score += 2

    score['rsi_strength'] = min(rsi_score, 15)
    detail['rsi6'] = round(rsi6, 1)
    detail['rsi12'] = round(rsi12, 1)
    detail['rsi24'] = round(rsi24, 1)

    vol_score = 0
    vol_ratio = row.get('volume_ratio', 0) or 0
    turnover = row.get('turnover_rate', 0) or 0
    pct_chg = row.get('pct_chg', 0) or 0
    vol = row['vol']
    prev_vol = prev_row['vol'] if prev_row is not None else 1

    if 0.8 < vol_ratio < 1.8:
        vol_score += 8
    elif 1.8 <= vol_ratio < 3.0:
        vol_score += 5
    elif 0.5 <= vol_ratio <= 0.8:
        vol_score += 3
    elif vol_ratio >= 3.0:
        vol_score += 1

    if 3 <= turnover <= 12:
        vol_score += 4
    elif 12 < turnover <= 18:
        vol_score += 2

    if pct_chg > 0 and vol > prev_vol * 0.8:
        vol_score += 3

    score['volume_health'] = min(vol_score, 15)
    detail['vol_ratio'] = round(vol_ratio, 2)
    detail['turnover'] = round(turnover, 2)

    price_score = 0
    open_p = row['open']
    body_pct = (close - open_p) / open_p * 100 if open_p > 0 else 0

    if 3 <= body_pct <= 8:
        price_score += 7
    elif 0 <= body_pct < 3:
        price_score += 4
    elif body_pct > 8:
        price_score += 3

    boll_upper = row.get('boll_upper_bfq', 0) or 0
    boll_mid = row.get('boll_mid_bfq', 0) or 0
    if boll_upper > 0 and boll_mid > 0:
        boll_pos = (close - boll_mid) / (boll_upper - boll_mid) if (boll_upper - boll_mid) > 0 else 0
        if 0.3 <= boll_pos <= 0.8:
            price_score += 8
        elif 0 < boll_pos < 0.3:
            price_score += 5
        elif boll_pos > 0.8:
            price_score += 2

    score['price_pattern'] = min(price_score, 15)
    detail['body_pct'] = round(body_pct, 2)
    detail['boll_upper'] = round(boll_upper, 2)
    detail['boll_mid'] = round(boll_mid, 2)

    total = score['ma_trend'] + score['macd_strength'] + score['rsi_strength'] + score['volume_health'] + score['price_pattern']
    score['total'] = min(total, 100)

    if total >= 80:
        grade = 'S'
    elif total >= 70:
        grade = 'A'
    elif total >= 60:
        grade = 'B'
    elif total >= 50:
        grade = 'C'
    else:
        grade = 'D'

    score['grade'] = grade
    score['detail'] = detail

    return score


def get_future_returns(df: pd.DataFrame, entry_idx: int,
                       future_windows: List[int]) -> Dict:
    result = {}
    for w in future_windows:
        future_idx = entry_idx + w
        if future_idx >= len(df):
            future_idx = len(df) - 1
        future_price = df.iloc[future_idx]['close']
        ret = (future_price / df.iloc[entry_idx]['close'] - 1) * 100
        result[f'return_{w}d'] = round(ret, 2)
        result[f'high_after_{w}d'] = round(
            (df.iloc[entry_idx:future_idx + 1]['high'].max() / df.iloc[entry_idx]['close'] - 1) * 100, 2
        )
    return result


def backtest_stock_10d_optimized(df: pd.DataFrame, ts_code: str,
                                  recent_days: int = None) -> Dict:
    """
    10日持有优化版回测 + 20天信号去重
    """
    if df is None or len(df) < 30:
        return {}

    if recent_days is not None and recent_days > 0:
        df = df.tail(recent_days + 60).reset_index(drop=True)

    all_scores = []
    for i in range(len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1] if i > 0 else None
        sc = calc_10d_optimized_score(row, prev_row)
        sc['trade_date'] = row['trade_date']
        sc['close'] = row['close']
        sc['pct_chg'] = row.get('pct_chg', 0)
        sc['vol_ratio'] = row.get('volume_ratio', 0)
        sc['turnover'] = row.get('turnover_rate', 0)
        sc['rsi6'] = row.get('rsi_bfq_6', 0)
        sc['macd_bar'] = row.get('macd_bfq', 0)
        all_scores.append(sc)

    scores_df = pd.DataFrame(all_scores)

    future_windows = [1, 5, 10, 20]
    raw_signals = []

    for i in range(len(df)):
        row = df.iloc[i]
        sc = all_scores[i]

        if sc['total'] >= 80:
            entry_idx = i + 1
            if entry_idx >= len(df):
                continue

            sig_data = {
                'ts_code': ts_code,
                'signal_date': row['trade_date'],
                'entry_date': df.iloc[entry_idx]['trade_date'],
                'entry_price': df.iloc[entry_idx]['close'],
                'signal_score': sc['total'],
                'signal_pct_chg': round(sc['pct_chg'], 2),
                'signal_rsi6': round(sc['rsi6'], 1),
                'signal_vol_ratio': round(sc['vol_ratio'], 2),
            }
            returns = get_future_returns(df, entry_idx, future_windows)
            sig_data.update(returns)
            raw_signals.append(sig_data)

    deduped_signals = deduplicate_20d_signals(raw_signals)

    summary = {
        'ts_code': ts_code,
        'total_days': len(df),
        'score_range': f"{scores_df['total'].min():.0f}~{scores_df['total'].max():.0f}",
        'score_mean': round(scores_df['total'].mean(), 1),
        'score_median': round(scores_df['total'].median(), 1),
        'signal_count': len(deduped_signals),
        'raw_signal_count': len(raw_signals),
        'dedup_count': len(raw_signals) - len(deduped_signals),
        's_count': len(scores_df[scores_df['grade'] == 'S']),
    }

    if deduped_signals:
        for w in future_windows:
            rets = [s[f'return_{w}d'] for s in deduped_signals if f'return_{w}d' in s]
            wins = [r for r in rets if r > 0]
            max_rets = [s[f'high_after_{w}d'] for s in deduped_signals if f'high_after_{w}d' in s]
            summary[f'{w}d_avg'] = round(np.mean(rets), 2) if rets else 0
            summary[f'{w}d_win_rate'] = round(len(wins) / len(rets) * 100, 1) if rets else 0
            summary[f'{w}d_max'] = round(max(rets), 2) if rets else 0
            summary[f'{w}d_min'] = round(min(rets), 2) if rets else 0
            summary[f'{w}d_avg_high'] = round(np.mean(max_rets), 2) if max_rets else 0

    summary['signals'] = deduped_signals

    return summary, scores_df


def print_10d_optimized_report(opt_results: List[Dict], output_file: str = None):
    """打印并保存10日优化版回测报告"""

    lines = []
    lines.append("=" * 80)
    lines.append("10日持有优化版 中线买点 回测报告（权重调优+20天去重）")
    lines.append(f"回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"优化权重: 均线25+MACD30+RSI15+量能15+形态15=100分")
    lines.append(f"去重规则: 每20天只保留1个最高分信号")
    lines.append("=" * 80)

    if not opt_results:
        lines.append("\n[警告] 无信号")
        report_text = "\n".join(lines)
        print(report_text)
        return report_text

    grand_total_signals = sum(r.get('signal_count', 0) for r in opt_results)
    grand_raw_signals = sum(r.get('raw_signal_count', 0) for r in opt_results)
    grand_dedup = sum(r.get('dedup_count', 0) for r in opt_results)

    lines.append(f"\n总计扫描: {len(opt_results)} 只股票")
    lines.append(f"累计交易日: {sum(r.get('total_days', 0) for r in opt_results)} 天")
    lines.append(f"去重前信号: {grand_raw_signals} 个")
    lines.append(f"去重后信号: {grand_total_signals} 个 (过滤掉 {grand_dedup} 个)")
    lines.append("")

    lines.append("─" * 80)
    lines.append("一、各股票评分统计")
    lines.append("-" * 80)
    lines.append(f"{'代码':<14} {'评分范围':>12} {'均值':>6} {'S级':>4} {'去重前':>6} {'信号数':>6} {'去重':>5}")
    lines.append("-" * 80)

    for res in opt_results:
        lines.append(
            f"{res['ts_code']:<14} {res['score_range']:>12} {res['score_mean']:>6.1f} "
            f"{res['s_count']:>4} {res['raw_signal_count']:>6} {res['signal_count']:>6} {res['dedup_count']:>5}"
        )

    lines.append("")
    lines.append("─" * 80)
    lines.append("二、优化版信号汇总（已去重）")
    lines.append("-" * 80)

    all_signals = []
    for res in opt_results:
        all_signals.extend(res.get('signals', []))

    if all_signals:
        lines.append(f"{'代码':<14} {'信号日':<12} {'入场日':<12} {'评分':>5} "
                     f"{'信号涨幅':>8} {'次日开仓':>8} "
                     f"{'+1日':>8} {'+5日':>8} {'+10日':>8} {'+20日':>8}")
        lines.append("-" * 110)

        for sig in all_signals:
            lines.append(
                f"{sig['ts_code']:<14} {sig['signal_date']:<12} {sig['entry_date']:<12} "
                f"{sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+7.2f}% {sig['entry_price']:>8.2f} "
                f"{sig.get('return_1d', 0):>+8.2f}% {sig.get('return_5d', 0):>+8.2f}% "
                f"{sig.get('return_10d', 0):>+8.2f}% {sig.get('return_20d', 0):>+8.2f}%"
            )

    lines.append("")
    lines.append("─" * 80)
    lines.append("三、优化版信号 各窗口平均收益统计")
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        avg_key = f'{w}d_avg'
        win_key = f'{w}d_win_rate'
        max_key = f'{w}d_max'
        min_key = f'{w}d_min'
        high_key = f'{w}d_avg_high'

        all_avgs = [r.get(avg_key, 0) for r in opt_results if r.get(avg_key) is not None]
        all_wins = [r.get(win_key, 0) for r in opt_results if r.get(win_key) is not None]
        all_maxs = [r.get(max_key, 0) for r in opt_results if r.get(max_key) is not None]
        all_mins = [r.get(min_key, 0) for r in opt_results if r.get(min_key) is not None]
        all_highs = [r.get(high_key, 0) for r in opt_results if r.get(high_key) is not None]

        if all_avgs:
            lines.append(
                f"  +{w}日: 平均收益 {np.mean(all_avgs):>+7.2f}%  "
                f"胜率 {np.mean(all_wins):>6.1f}%  "
                f"最大 {np.mean(all_maxs):>+7.2f}%  "
                f"最小 {np.mean(all_mins):>+7.2f}%  "
                f"盘中最高均值 {np.mean(all_highs):>+7.2f}%"
            )

    lines.append("")
    lines.append("─" * 80)
    lines.append("四、各股票优化版信号明细（已去重）")
    lines.append("-" * 80)

    for res in opt_results:
        signals = res.get('signals', [])
        if not signals:
            lines.append(f"\n{res['ts_code']}: 无信号 (评分均值={res['score_mean']})")
            continue

        lines.append(f"\n{'='*60}")
        lines.append(f"  {res['ts_code']}  |  评分区间 {res['score_range']}  |  均值 {res['score_mean']}  |  去重后 {res['signal_count']}次 (去重{res['dedup_count']}次)")
        lines.append(f"{'='*60}")
        lines.append(f"  {'信号日':<12} {'方式':>5} {'评分':>5} {'信号涨幅':>8} {'+10日':>7} {'+20日':>7} {'RSI6':>6} {'量比':>5}")
        lines.append(f"  {'-'*65}")

        for sig in signals:
            method = sig.get('entry_method', '-')
            lines.append(
                f"  {sig['signal_date']:<12} {method:>5} {sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+7.2f}% "
                f"{sig.get('return_10d', 0):>+6.2f}% "
                f"{sig.get('return_20d', 0):>+6.2f}% "
                f"{sig['signal_rsi6']:>6.1f} "
                f"{sig['signal_vol_ratio']:>5.2f}"
            )

        if len(signals) > 1:
            for w in [1, 5, 10, 20]:
                rets = [s.get(f'return_{w}d', 0) for s in signals]
                wins = [r for r in rets if r > 0]
                lines.append(
                    f"  → +{w}日 均值={np.mean(rets):>+6.2f}% "
                    f"胜率={len(wins)/len(rets)*100:>5.1f}% "
                    f"最大={max(rets):>+6.2f}% "
                    f"最小={min(rets):>+6.2f}%"
                )

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n报告已保存: {output_file}")

    return report_text


def print_three_way_comparison(
    all_results: List[Tuple[Dict, pd.DataFrame]],
    strict_results: List[Dict],
    opt_results: List[Dict],
    output_file: str = None
):
    """原版 vs 严苛版 vs 10日优化版 三版本对比"""

    lines = []
    lines.append("=" * 80)
    lines.append("原版 vs 严苛版 vs 10日优化版 三版本对比")
    lines.append(f"回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 80)

    def get_avg_stats(results, is_orig=False):
        stats = {}
        if is_orig:
            total_sigs = sum(r.get('signal_count', 0) for r, _ in results)
            stats['signal_count'] = total_sigs
            for w in [1, 5, 10, 20]:
                all_avgs = [r.get(f'{w}d_avg', 0) for r, _ in results if r.get(f'{w}d_avg') is not None]
                all_wins = [r.get(f'{w}d_win_rate', 0) for r, _ in results if r.get(f'{w}d_win_rate') is not None]
                all_highs = [r.get(f'{w}d_avg_high', 0) for r, _ in results if r.get(f'{w}d_avg_high') is not None]
                stats[f'{w}d_avg'] = np.mean(all_avgs) if all_avgs else 0
                stats[f'{w}d_win'] = np.mean(all_wins) if all_wins else 0
                stats[f'{w}d_high'] = np.mean(all_highs) if all_highs else 0
        else:
            total_sigs = sum(r.get('signal_count', 0) for r in results)
            stats['signal_count'] = total_sigs
            for w in [1, 5, 10, 20]:
                all_avgs = [r.get(f'{w}d_avg', 0) for r in results if r.get(f'{w}d_avg') is not None]
                all_wins = [r.get(f'{w}d_win_rate', 0) for r in results if r.get(f'{w}d_win_rate') is not None]
                all_highs = [r.get(f'{w}d_avg_high', 0) for r in results if r.get(f'{w}d_avg_high') is not None]
                stats[f'{w}d_avg'] = np.mean(all_avgs) if all_avgs else 0
                stats[f'{w}d_win'] = np.mean(all_wins) if all_wins else 0
                stats[f'{w}d_high'] = np.mean(all_highs) if all_highs else 0
        return stats

    orig = get_avg_stats(all_results, is_orig=True)
    strict = get_avg_stats(strict_results)
    opt = get_avg_stats(opt_results)

    lines.append("")
    lines.append("─" * 80)
    lines.append("一、信号数量对比")
    lines.append("-" * 80)
    lines.append(f"{'策略':<25} {'信号数':>8}")
    lines.append("-" * 40)
    lines.append(f"{'原版(≥80分)':<25} {orig['signal_count']:>8}")
    lines.append(f"{'严苛版(6项过滤)':<25} {strict['signal_count']:>8}")
    lines.append(f"{'10日优化版(去重)':<25} {opt['signal_count']:>8}")

    lines.append("")
    lines.append("─" * 80)
    lines.append("二、各窗口平均收益对比")
    lines.append("-" * 80)
    lines.append(f"{'窗口':>8} {'原版':>10} {'严苛版':>10} {'10日优化版':>12} {'对比原版提升':>10}")
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        lines.append(
            f"+{w}日  "
            f"{orig.get(f'{w}d_avg', 0):>+9.2f}% "
            f"{strict.get(f'{w}d_avg', 0):>+9.2f}% "
            f"{opt.get(f'{w}d_avg', 0):>+11.2f}% "
            f"{(opt.get(f'{w}d_avg', 0) - orig.get(f'{w}d_avg', 0)):>+9.2f}%"
        )

    lines.append("")
    lines.append("─" * 80)
    lines.append("三、各窗口胜率对比")
    lines.append("-" * 80)
    lines.append(f"{'窗口':>8} {'原版':>10} {'严苛版':>10} {'10日优化版':>12} {'对比原版提升':>10}")
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        lines.append(
            f"+{w}日  "
            f"{orig.get(f'{w}d_win', 0):>9.1f}% "
            f"{strict.get(f'{w}d_win', 0):>9.1f}% "
            f"{opt.get(f'{w}d_win', 0):>10.1f}% "
            f"{(opt.get(f'{w}d_win', 0) - orig.get(f'{w}d_win', 0)):>+9.1f}%"
        )

    lines.append("")
    lines.append("─" * 80)
    lines.append("四、各窗口盘中最高收益对比")
    lines.append("-" * 80)
    lines.append(f"{'窗口':>8} {'原版':>10} {'严苛版':>10} {'10日优化版':>12} {'对比原版提升':>10}")
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        lines.append(
            f"+{w}日  "
            f"{orig.get(f'{w}d_high', 0):>+9.2f}% "
            f"{strict.get(f'{w}d_high', 0):>+9.2f}% "
            f"{opt.get(f'{w}d_high', 0):>+11.2f}% "
            f"{(opt.get(f'{w}d_high', 0) - orig.get(f'{w}d_high', 0)):>+9.2f}%"
        )

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n对比报告已保存: {output_file}")

    return report_text


# =========================
# 打印严苛版回测报告
# =========================

def print_strict_backtest_report(strict_results: List[Dict], output_file: str = None):
    """打印并保存严苛版回测报告"""

    lines = []
    lines.append("=" * 80)
    lines.append("严苛版中线买点 回测报告（评分≥85分 + 6项过滤 + 回踩入场）")
    lines.append(f"回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"入场方式: 信号后等待回踩MA10/MA20确认")
    lines.append(f"过滤条件: 评分≥85 + 6项(量比0.5~2.5+趋势连续+MACD零轴张口+RSI60~85)")
    lines.append("=" * 80)

    if not strict_results:
        lines.append("\n[警告] 无严苛信号")
        report_text = "\n".join(lines)
        print(report_text)
        return report_text

    grand_total_signals = sum(r.get('signal_count', 0) for r in strict_results)

    lines.append(f"\n总计扫描: {len(strict_results)} 只股票")
    lines.append(f"累计交易日: {sum(r.get('total_days', 0) for r in strict_results)} 天")
    lines.append(f"严苛信号数: {grand_total_signals} 个")
    lines.append("")

    # ── 各股票统计 ──
    lines.append("─" * 80)
    lines.append("一、各股票评分统计")
    lines.append("-" * 80)
    lines.append(f"{'代码':<14} {'评分范围':>12} {'均值':>6} {'S级':>4} {'严苛信号':>8}")
    lines.append("-" * 80)

    for res in strict_results:
        lines.append(
            f"{res['ts_code']:<14} {res['score_range']:>12} {res['score_mean']:>6.1f} "
            f"{res['s_count']:>4} {res['signal_count']:>8}"
        )

    # ── 严苛信号汇总 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("二、严苛信号汇总")
    lines.append("-" * 80)

    all_signals = []
    for res in strict_results:
        all_signals.extend(res.get('signals', []))

    if all_signals:
        lines.append(f"{'代码':<14} {'信号日':<12} {'入场日':<12} {'评分':>5} "
                     f"{'信号涨幅':>8} {'次日开仓':>8} "
                     f"{'+1日':>8} {'+5日':>8} {'+10日':>8} {'+20日':>8}")
        lines.append("-" * 110)

        for sig in all_signals:
            lines.append(
                f"{sig['ts_code']:<14} {sig['signal_date']:<12} {sig['entry_date']:<12} "
                f"{sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+7.2f}% {sig['entry_price']:>8.2f} "
                f"{sig.get('return_1d', 0):>+8.2f}% {sig.get('return_5d', 0):>+8.2f}% "
                f"{sig.get('return_10d', 0):>+8.2f}% {sig.get('return_20d', 0):>+8.2f}%"
            )

    # ── 阶段收益统计 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("三、严苛信号 各窗口平均收益统计")
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        avg_key = f'{w}d_avg'
        win_key = f'{w}d_win_rate'
        max_key = f'{w}d_max'
        min_key = f'{w}d_min'
        high_key = f'{w}d_avg_high'

        all_avgs = [r.get(avg_key, 0) for r in strict_results if r.get(avg_key) is not None]
        all_wins = [r.get(win_key, 0) for r in strict_results if r.get(win_key) is not None]
        all_maxs = [r.get(max_key, 0) for r in strict_results if r.get(max_key) is not None]
        all_mins = [r.get(min_key, 0) for r in strict_results if r.get(min_key) is not None]
        all_highs = [r.get(high_key, 0) for r in strict_results if r.get(high_key) is not None]

        if all_avgs:
            lines.append(
                f"  +{w}日: 平均收益 {np.mean(all_avgs):>+7.2f}%  "
                f"胜率 {np.mean(all_wins):>6.1f}%  "
                f"最大 {np.mean(all_maxs):>+7.2f}%  "
                f"最大回撤 {np.mean(all_mins):>+7.2f}%  "
                f"盘中最高均值 {np.mean(all_highs):>+7.2f}%"
            )

    # ── 各股明细 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("四、各股票严苛信号明细")
    lines.append("-" * 80)

    for res in strict_results:
        signals = res.get('signals', [])
        if not signals:
            lines.append(f"\n{res['ts_code']}: 无严苛信号 (评分均值={res['score_mean']})")
            continue

        lines.append(f"\n{'='*60}")
        lines.append(f"  {res['ts_code']}  |  评分区间 {res['score_range']}  |  均值 {res['score_mean']}  |  严苛信号 {res['signal_count']}次")
        lines.append(f"{'='*60}")
        lines.append(f"  {'信号日':<12} {'方式':>5} {'评分':>5} {'信号涨幅':>8} {'+10日':>7} {'+20日':>7} {'RSI6':>6} {'量比':>5}")
        lines.append(f"  {'-'*65}")

        for sig in signals:
            method = sig.get('entry_method', '-')
            lines.append(
                f"  {sig['signal_date']:<12} {method:>5} {sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+7.2f}% "
                f"{sig.get('return_10d', 0):>+6.2f}% "
                f"{sig.get('return_20d', 0):>+6.2f}% "
                f"{sig['signal_rsi6']:>6.1f} "
                f"{sig['signal_vol_ratio']:>5.2f}"
            )

        if len(signals) > 1:
            for w in [1, 5, 10, 20]:
                rets = [s.get(f'return_{w}d', 0) for s in signals]
                wins = [r for r in rets if r > 0]
                lines.append(
                    f"  → +{w}日 均值={np.mean(rets):>+6.2f}% "
                    f"胜率={len(wins)/len(rets)*100:>5.1f}% "
                    f"最大={max(rets):>+6.2f}% "
                    f"最小={min(rets):>+6.2f}%"
                )

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n报告已保存: {output_file}")

    return report_text


# =========================
# 原版 vs 严苛版 对比报告
# =========================

def print_comparison_report(
    all_results: List[Tuple[Dict, pd.DataFrame]],
    strict_results: List[Dict],
    output_file: str = None
):
    """打印原版 vs 严苛版 对比报告"""

    lines = []
    lines.append("=" * 80)
    lines.append("原版 vs 严苛版 中线买点 回测对比报告")
    lines.append(f"回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 80)

    # ── 信号数量对比 ──
    orig_signals = sum(r.get('signal_count', 0) for r, _ in all_results)
    strict_signals = sum(r.get('signal_count', 0) for r in strict_results)

    lines.append("")
    lines.append("─" * 80)
    lines.append("一、信号数量对比")
    lines.append("-" * 80)
    lines.append(f"{'策略':<20} {'S级信号数':>12} {'过滤后严苛信号':>15} {'筛选率':>10}")
    lines.append("-" * 60)
    lines.append(f"{'原版(≥80分)':<20} {orig_signals:>12} {'-':>15} {'-':>10}")
    lines.append(f"{'严苛版(6项过滤)':<20} {orig_signals:>12} {strict_signals:>15} "
                 f"{(1 - strict_signals/max(orig_signals,1))*100:>9.1f}%")
    lines.append("")

    # ── 收益对比 ──
    future_windows = [1, 5, 10, 20]

    # 原版汇总
    orig_stats = {}
    for w in future_windows:
        all_avgs = [r.get(f'{w}d_avg', 0) for r, _ in all_results if r.get(f'{w}d_avg') is not None]
        all_wins = [r.get(f'{w}d_win_rate', 0) for r, _ in all_results if r.get(f'{w}d_win_rate') is not None]
        all_highs = [r.get(f'{w}d_avg_high', 0) for r, _ in all_results if r.get(f'{w}d_avg_high') is not None]
        if all_avgs:
            orig_stats[f'{w}d_avg'] = np.mean(all_avgs)
            orig_stats[f'{w}d_win'] = np.mean(all_wins)
            orig_stats[f'{w}d_high'] = np.mean(all_highs)

    # 严苛版汇总
    strict_stats = {}
    for w in future_windows:
        all_avgs = [r.get(f'{w}d_avg', 0) for r in strict_results if r.get(f'{w}d_avg') is not None]
        all_wins = [r.get(f'{w}d_win_rate', 0) for r in strict_results if r.get(f'{w}d_win_rate') is not None]
        all_highs = [r.get(f'{w}d_avg_high', 0) for r in strict_results if r.get(f'{w}d_avg_high') is not None]
        if all_avgs:
            strict_stats[f'{w}d_avg'] = np.mean(all_avgs)
            strict_stats[f'{w}d_win'] = np.mean(all_wins)
            strict_stats[f'{w}d_high'] = np.mean(all_highs)

    lines.append("")
    lines.append("─" * 80)
    lines.append("二、各窗口平均收益对比")
    lines.append("-" * 80)
    lines.append(f"{'窗口':>8} {'原版均值':>12} {'严苛版均值':>12} {'提升':>10} "
                 f"{'原版胜率':>10} {'严苛版胜率':>10} {'胜率提升':>10}")
    lines.append("-" * 80)

    for w in future_windows:
        orig_avg = orig_stats.get(f'{w}d_avg', 0)
        strict_avg = strict_stats.get(f'{w}d_avg', 0)
        orig_win = orig_stats.get(f'{w}d_win', 0)
        strict_win = strict_stats.get(f'{w}d_win', 0)
        avg_imp = strict_avg - orig_avg
        win_imp = strict_win - orig_win

        lines.append(
            f"+{w}日  "
            f"{orig_avg:>+11.2f}% {strict_avg:>+11.2f}% {avg_imp:>+9.2f}%  "
            f"{orig_win:>9.1f}% {strict_win:>9.1f}% {win_imp:>+9.1f}%"
        )

    lines.append("")
    lines.append("─" * 80)
    lines.append("三、各窗口盘中最高收益对比")
    lines.append("-" * 80)
    lines.append(f"{'窗口':>8} {'原版均值':>12} {'严苛版均值':>12} {'提升':>10}")
    lines.append("-" * 80)

    for w in future_windows:
        orig_high = orig_stats.get(f'{w}d_high', 0)
        strict_high = strict_stats.get(f'{w}d_high', 0)
        high_imp = strict_high - orig_high

        lines.append(
            f"+{w}日  {orig_high:>+11.2f}% {strict_high:>+11.2f}% {high_imp:>+9.2f}%"
        )

    # ── 过滤条件分析 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("四、严苛过滤条件效果分析")
    lines.append("-" * 80)

    all_strict_filters = {}
    for res in strict_results:
        for sig in res.get('signals', []):
            filters = sig.get('strict_filters', {})
            for k, v in filters.items():
                if v:
                    all_strict_filters[k] = all_strict_filters.get(k, 0) + 1

    if all_strict_filters:
        total = sum(all_strict_filters.values())
        lines.append(f"各严苛条件通过次数（所有信号日）：")
        filter_names = {
            'score_90': '评分≥90',
            'pullback_confirmed': '二次回踩确认',
            'volume_healthy': '量比0.6~2.0',
            'trend_continuous': '趋势连续性',
            'macd_zero_axis': 'MACD零轴张口',
            'rsi_not_hot': 'RSI不过热',
        }
        for k, v in sorted(all_strict_filters.items(), key=lambda x: -x[1]):
            lines.append(f"  {filter_names.get(k, k)}: {v}次 ({v/max(total,1)*100:.1f}%)")

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n对比报告已保存: {output_file}")

    return report_text


# =========================
# 打印回测报告
# =========================

def print_backtest_report(all_results: List[Tuple[Dict, pd.DataFrame]], output_file: str = None):
    """打印并保存回测报告"""

    lines = []
    lines.append("=" * 80)
    lines.append("确定性走强评分公式 回测报告")
    lines.append(f"回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"信号阈值: S级 ≥80分 | A级 70-79分")
    lines.append("=" * 80)

    grand_total_signals = 0
    for res, scores_df in all_results:
        grand_total_signals += res.get('signal_count', 0)

    lines.append(f"\n总计扫描: {len(all_results)} 只股票")
    lines.append(f"累计交易日: {sum(r.get('total_days',0) for r, _ in all_results)} 天")
    lines.append(f"总S级信号数: {grand_total_signals} 个")
    lines.append("")
    lines.append("─" * 80)
    lines.append("一、各股票评分统计")
    lines.append("-" * 80)
    lines.append(f"{'代码':<14} {'评分范围':>12} {'均值':>6} {'中位数':>7} "
                 f"{'S级':>4} {'A级':>4} {'信号数':>6}")
    lines.append("-" * 80)

    for res, scores_df in all_results:
        lines.append(
            f"{res['ts_code']:<14} {res['score_range']:>12} {res['score_mean']:>6.1f} "
            f"{res['score_median']:>7.1f} {res['s_count']:>4} {res['a_count']:>4} "
            f"{res['signal_count']:>6}"
        )

    # ── S级信号汇总 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("二、S级信号(≥80分)汇总")
    lines.append("-" * 80)

    all_signals = []
    for res, scores_df in all_results:
        all_signals.extend(res.get('signals', []))

    if all_signals:
        lines.append(f"{'代码':<14} {'信号日':<12} {'入场日':<12} {'评分':>5} "
                     f"{'信号日涨幅':>9} {'次日开仓':>8} "
                     f"{'+1日':>8} {'+5日':>8} {'+10日':>8} {'+20日':>8}")
        lines.append("-" * 100)

        for sig in all_signals:
            lines.append(
                f"{sig['ts_code']:<14} {sig['signal_date']:<12} {sig['entry_date']:<12} "
                f"{sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+8.2f}% {sig['entry_price']:>8.2f} "
                f"{sig.get('return_1d', 0):>+8.2f}% {sig.get('return_5d', 0):>+8.2f}% "
                f"{sig.get('return_10d', 0):>+8.2f}% {sig.get('return_20d', 0):>+8.2f}%"
            )

    # ── 阶段收益统计 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("三、S级信号 各窗口平均收益统计")
    lines.append("-" * 80)

    for w in [1, 5, 10, 20]:
        avg_key = f'{w}d_avg'
        win_key = f'{w}d_win_rate'
        max_key = f'{w}d_max'
        min_key = f'{w}d_min'
        high_key = f'{w}d_avg_high'

        all_avgs = [r.get(avg_key, 0) for r, _ in all_results if r.get(avg_key) is not None]
        all_wins = [r.get(win_key, 0) for r, _ in all_results if r.get(win_key) is not None]
        all_maxs = [r.get(max_key, 0) for r, _ in all_results if r.get(max_key) is not None]
        all_mins = [r.get(min_key, 0) for r, _ in all_results if r.get(min_key) is not None]
        all_highs = [r.get(high_key, 0) for r, _ in all_results if r.get(high_key) is not None]

        if all_avgs:
            lines.append(
                f"  +{w}日: 平均收益 {np.mean(all_avgs):>+7.2f}%  "
                f"胜率 {np.mean(all_wins):>6.1f}%  "
                f"最大 {np.mean(all_maxs):>+7.2f}%  "
                f"最大回撤 {np.mean(all_mins):>+7.2f}%  "
                f"盘中最高均值 {np.mean(all_highs):>+7.2f}%"
            )

    # ── 各股明细 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("四、各股票 S级信号明细")
    lines.append("-" * 80)

    for res, scores_df in all_results:
        r = res
        signals = r.get('signals', [])
        if not signals:
            lines.append(f"\n{r['ts_code']}: 无S级信号 (评分均值={r['score_mean']})")
            continue

        lines.append(f"\n{'='*60}")
        lines.append(f"  {r['ts_code']}  |  评分区间 {r['score_range']}  |  均值 {r['score_mean']}  |  S级信号 {r['signal_count']}次")
        lines.append(f"{'='*60}")
        lines.append(f"  {'信号日':<12} {'评分':>5} {'信号涨幅':>8} {'+1日':>7} {'+5日':>7} {'+10日':>7} {'+20日':>7} {'RSI6':>6} {'量比':>5}")
        lines.append(f"  {'-'*65}")

        for sig in signals:
            lines.append(
                f"  {sig['signal_date']:<12} {sig['signal_score']:>5.0f} "
                f"{sig['signal_pct_chg']:>+7.2f}% "
                f"{sig.get('return_1d', 0):>+6.2f}% "
                f"{sig.get('return_5d', 0):>+6.2f}% "
                f"{sig.get('return_10d', 0):>+6.2f}% "
                f"{sig.get('return_20d', 0):>+6.2f}% "
                f"{sig['signal_rsi6']:>6.1f} "
                f"{sig['signal_vol_ratio']:>5.2f}"
            )

        # 各窗口统计
        if len(signals) > 1:
            for w in [1, 5, 10, 20]:
                rets = [s.get(f'return_{w}d', 0) for s in signals]
                wins = [r for r in rets if r > 0]
                lines.append(
                    f"  → +{w}日 均值={np.mean(rets):>+6.2f}% "
                    f"胜率={len(wins)/len(rets)*100:>5.1f}% "
                    f"最大={max(rets):>+6.2f}% "
                    f"最小={min(rets):>+6.2f}%"
                )

    # ── 等级分布 ──
    lines.append("")
    lines.append("─" * 80)
    lines.append("五、评分等级分布（全部扫描日）")
    lines.append("-" * 80)

    for res, scores_df in all_results:
        r = res
        total_days = r['total_days']
        s = r['s_count']
        a = r['a_count']
        b = len(scores_df[scores_df['grade'] == 'B'])
        c = len(scores_df[scores_df['grade'] == 'C'])
        d = len(scores_df[scores_df['grade'] == 'D'])
        lines.append(
            f"  {r['ts_code']}: "
            f"S级={s}({s/total_days*100:.1f}%) "
            f"A级={a}({a/total_days*100:.1f}%) "
            f"B级={b}({b/total_days*100:.1f}%) "
            f"C级={c}({c/total_days*100:.1f}%) "
            f"D级={d}({d/total_days*100:.1f}%)"
        )

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n报告已保存: {output_file}")

    return report_text


# =========================
# 主函数
# =========================

def load_qualified_pool() -> list:
    """从 bull_stocks 相关 CSV 读取合格股票池"""
    # 候选路径（按优先级）
    candidates = [
        # 1) output 目录下最新的 bull_stocks_*.csv
        r"D:\mystock\solo\multi_factor_picker\output",
        # 2) report_daily 目录的固定文件名
        r"D:\mystock\report_daily",
    ]

    csv_path = None
    for base_dir in candidates:
        if not os.path.isdir(base_dir):
            continue
        # 找 bull_stocks_*.csv 中的最新一个
        files = sorted(
            [f for f in os.listdir(base_dir) if f.startswith('bull_stocks_') and f.endswith('.csv')],
            reverse=True
        )
        if files:
            csv_path = os.path.join(base_dir, files[0])
            break
        # 也试试固定文件名
        fixed = os.path.join(base_dir, "bull_stocks_qualified.csv")
        if os.path.exists(fixed):
            csv_path = fixed
            break

    if csv_path is None:
        print(f"[警告] 未找到 bull_stocks_*.csv，请先运行 main.py 生成")
        return []

    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    # 兼容两种列名
    code_col = 'ts_code' if 'ts_code' in df.columns else 'code'
    codes = df[code_col].dropna().unique().tolist()
    # 统一补齐交易所后缀
    codes = [normalize_ts_code(str(c).strip().zfill(6)) for c in codes]
    print(f"[股票池] 从 {csv_path} 读取 {len(codes)} 只合格标的")
    return codes


def main():
    import argparse
    parser = argparse.ArgumentParser(description='确定性走强评分回测器（中线趋势选股）')
    parser.add_argument('codes', nargs='*', help='股票代码，不指定则使用默认池')
    parser.add_argument('--pool', choices=['default', 'qualified'], default='default',
                        help='股票池: default(24只核心股) / qualified(bull_stocks_qualified.csv)')
    parser.add_argument('--recent', type=int, default=RECENT_DAYS,
                        help=f'只分析最近N天 (默认{RECENT_DAYS if RECENT_DAYS else "全部"}天)')
    parser.add_argument('--lookback', type=int, default=LOOKBACK_DAYS,
                        help='读取历史天数 (默认%d)' % LOOKBACK_DAYS)
    args = parser.parse_args()

    # 确定股票池
    if args.codes:
        stock_codes = [normalize_ts_code(c) for c in args.codes]
    elif args.pool == 'qualified':
        stock_codes = load_qualified_pool()
        if not stock_codes:
            return
    else:
        stock_codes = [normalize_ts_code(c) for c in DEFAULT_STOCKS]

    recent_days = args.recent if args.recent > 0 else None
    lookback_days = args.lookback

    print("=" * 70)
    print("中线趋势选股 — 确定性走强评分回测器（严苛版 + 回踩入场）")
    print("=" * 70)
    print(f"股票池: {args.pool} ({len(stock_codes)} 只)")
    print(f"评分: ≥85分 + 6项严苛过滤")
    print(f"入场: MOMENTUM / MA10 / MA20 / MULTI / 趋势确认")
    print(f"回测范围: 最近{recent_days or '全部'}天 (读取{lookback_days}天数据)")
    print()

    # 读取数据并回测
    strict_results = []

    for code in stock_codes:
        print(f"  处理 {code} ...", end=" ")
        df = get_stock_data(code, lookback_days=lookback_days)
        if df is None or len(df) < 30:
            print("无数据")
            continue

        strict_result, _ = backtest_stock_strict_pullback(df, code, recent_days=recent_days)
        if strict_result and strict_result.get('signal_count', 0) > 0:
            strict_results.append(strict_result)
            print(f"信号={strict_result['signal_count']}个")
        elif strict_result:
            print(f"无信号 (评分均值={strict_result['score_mean']})")
        else:
            print("失败")

    if not strict_results:
        print("[结果] 没有可用回测结果")
        return

    # 输出报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = OUTPUT_DIR

    # 严苛版报告
    pool_tag = f"_{args.pool}"
    strict_file = os.path.join(output_dir, f"backtest_strict_report_{timestamp}{pool_tag}.txt")
    print_strict_backtest_report(strict_results, strict_file)

    # 保存信号明细 JSON
    def convert(obj):
        if isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [convert(x) for x in obj]
        elif isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return obj

    strict_signals = []
    for res in strict_results:
        strict_signals.extend(res.get('signals', []))

    strict_json_file = os.path.join(output_dir, f"backtest_strict_signals_{timestamp}{pool_tag}.json")
    with open(strict_json_file, 'w', encoding='utf-8') as f:
        json.dump(convert(strict_signals), f, ensure_ascii=False, indent=2)

    # 保存信号明细 CSV
    strict_csv_file = os.path.join(output_dir, f"backtest_strict_signals_{timestamp}{pool_tag}.csv")
    if strict_signals:
        signal_df = pd.DataFrame(strict_signals)
        # 保留关键列
        cols = ['signal_date', 'entry_date', 'ts_code', 'entry_method', 'signal_score',
                'pullback_depth', 'above_ma20_pct', 'signal_rsi6',
                'signal_pct_chg', 'signal_vol_ratio',
                'return_1d', 'return_5d', 'return_10d', 'return_20d',
                'entry_price', 'signal_close']
        cols = [c for c in cols if c in signal_df.columns]
        signal_df = signal_df[cols].sort_values('signal_date', ascending=False)
        signal_df.to_csv(strict_csv_file, index=False, encoding='utf-8-sig')
        print(f"  汇总CSV: {strict_csv_file}")

    # 汇总
    print(f"\n{'='*70}")
    print(f"  扫描完成！{len(strict_results)} 只有信号, 共 {len(strict_signals)} 个信号")
    print(f"  报告: {strict_file}")
    print(f"  明细: {strict_json_file}")
    print(f"  CSV:  {strict_csv_file}")

    # 输出信号汇总
    print(f"\n  {'信号日':<10}  {'股票':<12}  {'方式':<10}  {'评分':>4}  {'+10日':>7}  {'+20日':>7}")
    print(f"  {'-'*55}")
    for s in sorted(strict_signals, key=lambda x: x['signal_date'], reverse=True):
        print(f"  {s['signal_date']:<10}  {s['ts_code']:<12}  {s.get('entry_method','?'):<10}  {s['signal_score']:>4}  {s.get('return_10d',0):>7.2f}%  {s.get('return_20d',0):>7.2f}%")


if __name__ == "__main__":
    main()
