#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF 中线趋势评级算法 V1.0
========================
基于中线趋势（1-3个月）的 A+/A/B/C 四级评级体系。

核心因子：
  - RRS_20: 相对强弱 = ETF_20d_Return - HS300_20d_Return
  - EMA20/EMA60 发散度
  - MA5/10/20 多头排列
  - 最大回撤（10日/20日）
  - 周线级别支撑（MA60不破）

评级体系：
  A+: EMA20 > EMA60 且发散，RRS 前15%，周线无破位
  A:  MA5/10/20 多头排列，10日最大回撤 < 5%，含红利/防御类
  B:  站上MA20，短线分歧但中线完好（捕捉洗盘买点）
  C:  仅当 MA60 下方或 EMA20 下穿 EMA60，RRS 后30%

特殊规则：
  - EOS < 50 仅扣5-10分，不能直接判C
  - Force Rank：即使全市场不佳，也必须输出 Top 2-3 最有韧性的ETF
"""

import os
import sys
import struct
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv("d:/mystock/config/.env")

TDX_ROOT = r"C:\new_tdx"
HS300_CODE = "000300.SH"

ETF_POOL = {
    '酒': '512690.SH',
    '银行': '512800.SH',
    '医疗器械': '159883.SZ',
    '游戏': '159869.SZ',
    '煤炭': '515220.SH',
    '电力': '159611.SZ',
    '人工智能': '159819.SZ',
    '科创半导体': '588170.SH',
    '光伏': '515790.SH',
    '有色金属': '516650.SH',
    '军工': '512660.SH',
    '机器人': '562500.SH',
}

DEFENSIVE_ETFS = {'银行', '电力', '煤炭', '红利'}


def ts_code_to_tdx_file(ts_code: str, tdx_root: str = TDX_ROOT) -> Optional[str]:
    sym, market = ts_code.split(".")
    if market == "SH":
        prefix, subdir = "sh", "sh"
    elif market == "SZ":
        prefix, subdir = "sz", "sz"
    else:
        return None
    # 指数代码保留前导零（如 000300 → sh000300.day）
    if sym.startswith("000"):
        tdx_sym = sym
    else:
        tdx_sym = sym.lstrip("0") or "0"
    return os.path.join(tdx_root, "vipdoc", subdir, "lday", f"{prefix}{tdx_sym}.day")


def parse_tdx_day_file(filepath: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_val = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            records.append({
                "trade_date": str(date_int),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "vol": vol_shares,
                "amount": round(amount_val / 1000.0, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    df["pct_chg"] = (df["close"].pct_change() * 100.0).fillna(0.0)
    return df


def load_etf_data(ts_code: str) -> pd.DataFrame:
    filepath = ts_code_to_tdx_file(ts_code)
    if filepath is None:
        return pd.DataFrame()
    df = parse_tdx_day_file(filepath)
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def calc_ema(series: np.ndarray, period: int) -> np.ndarray:
    """计算指数移动平均"""
    result = np.full_like(series, np.nan, dtype=np.float64)
    if len(series) < period:
        return result
    alpha = 2.0 / (period + 1)
    result[period - 1] = np.mean(series[:period])
    for i in range(period, len(series)):
        result[i] = alpha * series[i] + (1 - alpha) * result[i - 1]
    return result


def calc_max_drawdown(close: np.ndarray, window: int) -> float:
    """计算滚动窗口内的最大回撤（百分比）"""
    if len(close) < window:
        return 0.0
    segment = close[-window:]
    peak = np.maximum.accumulate(segment)
    dd = (segment - peak) / peak * 100
    return abs(float(np.min(dd)))


def calc_weekly_breakdown(df: pd.DataFrame) -> bool:
    """
    检测周线是否破位：
    取最近5根周线（约5周），计算周线MA10（约10周），
    如果周线收盘价跌破周线MA10，则判定破位。
    """
    if len(df) < 50:
        return False
    df_w = df.copy()
    df_w["trade_date"] = pd.to_datetime(df_w["trade_date"], format="%Y%m%d")
    df_w.set_index("trade_date", inplace=True)
    weekly = df_w["close"].resample("W").last().dropna()
    if len(weekly) < 15:
        return False
    w_close = weekly.values
    w_ma10 = pd.Series(w_close).rolling(10).mean().values
    if len(w_ma10) < 2 or np.isnan(w_ma10[-1]):
        return False
    return float(w_close[-1]) < float(w_ma10[-1])


def calc_volume_ratio(df: pd.DataFrame) -> float:
    """计算近5日均量 / 近20日均量（使用绝对值过滤除权负量）"""
    if len(df) < 20:
        return 1.0
    vol = np.abs(df["vol"].values.astype(float))
    vol5 = np.mean(vol[-5:])
    vol20 = np.mean(vol[-20:])
    if vol20 == 0:
        return 1.0
    return float(vol5 / vol20)


def detect_split(df: pd.DataFrame, lookback: int = 30) -> bool:
    """检测最近N日内是否有除权除息（TDX用负成交量+极端价格跳变标记）"""
    if len(df) < lookback:
        return False
    vol = df["vol"].values.astype(float)[-lookback:]
    close = df["close"].values.astype(float)
    if len(close) < lookback + 1:
        return False
    close_recent = close[-lookback-1:]
    for i in range(1, len(close_recent)):
        if vol[i-1] < 0:
            pct = abs((close_recent[i] / close_recent[i-1] - 1.0) * 100)
            if pct > 20:
                return True
    return False


def calc_ret_from_pct(df: pd.DataFrame, window: int) -> float:
    """用逐日涨跌幅复利计算N日收益率（自动处理除权除息）"""
    if len(df) < window + 1:
        return 0.0
    pct = df["pct_chg"].values.astype(float)[-(window):]
    cumulative = 1.0
    for p in pct:
        cumulative *= (1.0 + p / 100.0)
    return float((cumulative - 1.0) * 100.0)


def calc_volatility(close: np.ndarray, window: int = 20) -> float:
    """计算年化波动率"""
    if len(close) < window:
        return 0.0
    returns = np.diff(close[-window:]) / close[-window:-1]
    return float(np.std(returns) * np.sqrt(252) * 100)


def calculate_midterm_etf_rating(
    df_etf: pd.DataFrame,
    df_benchmark: pd.DataFrame,
    etf_name: str = "",
    eos_score: float = 50.0,
) -> Dict:
    """
    计算单只ETF的中线趋势评级。

    参数:
        df_etf: ETF日线数据 (含 trade_date, open, high, low, close, vol, amount)
        df_benchmark: 沪深300日线数据 (同上)
        etf_name: ETF名称
        eos_score: 外部传入的EOS综合评分 (0-100)，默认50

    返回:
        Dict: 包含所有评级因子和最终评级的字典
    """
    if df_etf.empty or len(df_etf) < 60:
        return {
            "name": etf_name,
            "rating": "C",
            "rating_score": 0,
            "reason": "数据不足（<60个交易日）",
            "rrs_20": 0,
            "force_rank": 99,
        }

    close = df_etf["close"].values.astype(float)
    n = len(close)

    # ============================================================
    # 1. 均线计算
    # ============================================================
    ma5 = pd.Series(close).rolling(5).mean().values
    ma10 = pd.Series(close).rolling(10).mean().values
    ma20 = pd.Series(close).rolling(20).mean().values
    ma60 = pd.Series(close).rolling(60).mean().values
    ema20 = calc_ema(close, 20)
    ema60 = calc_ema(close, 60)

    latest_close = close[-1]
    latest_ma5 = ma5[-1] if not np.isnan(ma5[-1]) else latest_close
    latest_ma10 = ma10[-1] if not np.isnan(ma10[-1]) else latest_close
    latest_ma20 = ma20[-1] if not np.isnan(ma20[-1]) else latest_close
    latest_ma60 = ma60[-1] if not np.isnan(ma60[-1]) else latest_close
    latest_ema20 = ema20[-1] if not np.isnan(ema20[-1]) else latest_close
    latest_ema60 = ema60[-1] if not np.isnan(ema60[-1]) else latest_close

    # ============================================================
    # 2. RRS (Relative Strength) 计算
    # ============================================================
    etf_ret_20d = calc_ret_from_pct(df_etf, 20)
    bm_ret_20d = calc_ret_from_pct(df_benchmark, 20)

    rrs_20 = etf_ret_20d - bm_ret_20d

    # ============================================================
    # 3. EMA20 vs EMA60 发散度
    # ============================================================
    divergence = 0.0
    if not np.isnan(latest_ema20) and not np.isnan(latest_ema60) and latest_ema60 > 0:
        divergence = (latest_ema20 / latest_ema60 - 1.0) * 100

    # 发散增强：最近5日发散度变化
    div_5d_ago = 0.0
    if n >= 6 and not np.isnan(ema20[-6]) and not np.isnan(ema60[-6]) and ema60[-6] > 0:
        div_5d_ago = (ema20[-6] / ema60[-6] - 1.0) * 100
    div_trend = divergence - div_5d_ago

    # ============================================================
    # 4. 均线多头排列检查
    # ============================================================
    bull_alignment = (latest_ma5 > latest_ma10 > latest_ma20)
    above_ma20 = latest_close > latest_ma20
    above_ma60 = latest_close > latest_ma60

    # EMA多头
    ema_bull = latest_ema20 > latest_ema60

    # ============================================================
    # 5. 回撤计算
    # ============================================================
    dd_10d = calc_max_drawdown(close, 10)
    dd_20d = calc_max_drawdown(close, 20)

    # ============================================================
    # 6. 周线破位检测
    # ============================================================
    weekly_broken = calc_weekly_breakdown(df_etf)

    # ============================================================
    # 7. 量能因子
    # ============================================================
    vol_ratio = calc_volume_ratio(df_etf)

    # ============================================================
    # 8. 波动率
    # ============================================================
    volatility = calc_volatility(close, 20)

    # ============================================================
    # 8.5 除权检测
    # ============================================================
    has_split = detect_split(df_etf, 30)

    # ============================================================
    # 9. 评级计算
    # ============================================================
    is_defensive = etf_name in DEFENSIVE_ETFS
    rating = "C"
    rating_score = 0
    reasons = []

    # --- A+ 条件 ---
    # EMA20 > EMA60 且发散度 > 0.5%，RRS 表现优异，周线无破位，不是纯下跌反弹
    aplus_conditions = (
        ema_bull
        and divergence > 0.5
        and rrs_20 > 2.0
        and not weekly_broken
        and above_ma20
        and dd_10d < 5.0
    )

    # --- A 条件 ---
    # MA5/10/20 多头排列 或 站上MA20 + 小回撤 + 不破MA60
    a_conditions = (
        (bull_alignment or (above_ma20 and above_ma60))
        and dd_10d < 5.0
        and not weekly_broken
        and rrs_20 > -2.0
    )

    # --- B 条件 ---
    # 站上MA20，短线分歧但中线完好（捕捉洗盘买点）
    b_conditions = (
        above_ma20
        and above_ma60
        and dd_20d < 12.0
        and not weekly_broken
    )

    # --- C 条件 ---
    # 仅当 MA60 下方 或 EMA20 下穿 EMA60，RRS 后30%
    c_conditions = (
        (not above_ma60 or not ema_bull)
        and rrs_20 < -1.0
    )

    # 综合评级
    if aplus_conditions:
        rating = "A+"
        rating_score = 90
        reasons.append("EMA20/60多头发散")
        reasons.append(f"RRS优秀({rrs_20:+.1f}%)")
        if div_trend > 0:
            reasons.append("发散度持续增强")
            rating_score += 5
    elif a_conditions:
        rating = "A"
        rating_score = 75
        if bull_alignment:
            reasons.append("MA5/10/20多头排列")
        else:
            reasons.append("站上MA20/MA60均线")
        reasons.append(f"10日回撤仅{dd_10d:.1f}%")
        if is_defensive:
            reasons.append("防御类资产")
            rating_score += 3
    elif b_conditions:
        rating = "B"
        rating_score = 55
        reasons.append("站上MA20/MA60，中线完好")
        if dd_10d > 3.0:
            reasons.append(f"短线回撤{dd_10d:.1f}%（洗盘买点）")
        if not bull_alignment:
            reasons.append("短均线分歧，中线趋势未破")
    elif c_conditions:
        rating = "C"
        rating_score = 25
        reasons.append("MA60下方或EMA20/E60死叉")
        reasons.append(f"RRS弱势({rrs_20:+.1f}%)")
    else:
        rating = "B"
        rating_score = 50
        reasons.append("中线趋势中性，待观察")

    # ============================================================
    # 10. EOS 软修正（仅扣分，不直接降级）
    # ============================================================
    if eos_score < 50:
        penalty = (50 - eos_score) * 0.15
        rating_score -= penalty
        reasons.append(f"EOS偏低({eos_score:.0f})，扣{penalty:.1f}分")

    # ============================================================
    # 11. 量能加分
    # ============================================================
    if vol_ratio > 1.3:
        rating_score += 3
        reasons.append(f"放量({vol_ratio:.1f}x)")
    elif vol_ratio < 0.6:
        rating_score -= 2
        reasons.append(f"缩量({vol_ratio:.1f}x)")

    # ============================================================
    # 12. 波动率惩罚
    # ============================================================
    if volatility > 45:
        rating_score -= 5
        reasons.append(f"高波动({volatility:.0f}%)")

    # ============================================================
    # 13. 除权除息惩罚
    # ============================================================
    if has_split:
        rating_score -= 8
        reasons.append("近期除权除息，数据失真")

    rating_score = max(0, min(100, rating_score))

    return {
        "name": etf_name,
        "rating": rating,
        "rating_score": round(rating_score, 1),
        "reason": "；".join(reasons),
        "close": round(latest_close, 3),
        "rrs_20": round(rrs_20, 2),
        "etf_ret_20d": round(etf_ret_20d, 2),
        "bm_ret_20d": round(bm_ret_20d, 2),
        "divergence": round(divergence, 2),
        "div_trend": round(div_trend, 2),
        "bull_alignment": bull_alignment,
        "above_ma20": above_ma20,
        "above_ma60": above_ma60,
        "ema_bull": ema_bull,
        "dd_10d": round(dd_10d, 2),
        "dd_20d": round(dd_20d, 2),
        "weekly_broken": weekly_broken,
        "vol_ratio": round(vol_ratio, 2),
        "volatility": round(volatility, 1),
        "is_defensive": is_defensive,
        "has_split": has_split,
        "eos_penalty": (50 - eos_score) * 0.15 if eos_score < 50 else 0,
        "force_rank": 99,
    }


def apply_force_rank(results: List[Dict]) -> List[Dict]:
    """
    Force Rank 机制：
    即使全市场不佳，也必须选出 Top 2-3 最有韧性的 ETF。
    基于 RRS + MA支撑 + 回撤控制 综合排序。
    """
    if not results:
        return results

    for r in results:
        if r["rating"] == "C":
            rrs_bonus = max(0, r["rrs_20"] + 5) * 0.5
            ma_bonus = 3 if r["above_ma60"] else 0
            dd_penalty = r["dd_20d"] * 0.3
            force_score = rrs_bonus + ma_bonus - dd_penalty
            r["force_score"] = force_score
        else:
            r["force_score"] = r["rating_score"]

    sorted_results = sorted(results, key=lambda x: x["force_score"], reverse=True)

    all_c = all(r["rating"] == "C" for r in sorted_results)
    top_n = 3 if all_c else 2

    for i, r in enumerate(sorted_results):
        if i < top_n:
            r["force_rank"] = i + 1
            if all_c and r["rating"] == "C":
                r["force_tag"] = "📌 弱势市场中相对最强"
            elif r["rating"] in ("A+", "A"):
                r["force_tag"] = "⭐ 首选"
            else:
                r["force_tag"] = "🔍 关注"
        else:
            r["force_rank"] = 99
            r["force_tag"] = ""

    return sorted_results


def calculate_all_etf_ratings(
    etf_pool: Dict[str, str] = None,
    end_date: str = "",
    eos_scores: Dict[str, float] = None,
) -> pd.DataFrame:
    """
    批量计算所有ETF的中线趋势评级。

    参数:
        etf_pool: ETF名称 -> ts_code 映射，默认使用 ETF_POOL
        end_date: 截止日期，默认最新
        eos_scores: ETF名称 -> EOS评分 映射（可选，不传则默认50）

    返回:
        DataFrame: 评级矩阵，按 force_rank 排序
    """
    if etf_pool is None:
        etf_pool = ETF_POOL
    if eos_scores is None:
        eos_scores = {}

    df_benchmark = load_etf_data(HS300_CODE)
    if df_benchmark.empty:
        print("⚠ 无法加载沪深300基准数据")
        return pd.DataFrame()

    results = []
    for etf_name, ts_code in etf_pool.items():
        df_etf = load_etf_data(ts_code)
        if df_etf.empty:
            print(f"  ⚠ {etf_name}({ts_code}) 无数据，跳过")
            continue

        if end_date:
            df_etf = df_etf[df_etf["trade_date"] <= end_date].copy()
            df_bm = df_benchmark[df_benchmark["trade_date"] <= end_date].copy()
        else:
            df_bm = df_benchmark.copy()

        eos = eos_scores.get(etf_name, 50.0)

        result = calculate_midterm_etf_rating(
            df_etf=df_etf,
            df_benchmark=df_bm,
            etf_name=etf_name,
            eos_score=eos,
        )
        results.append(result)

    results = apply_force_rank(results)

    df = pd.DataFrame(results)
    if df.empty:
        return df

    col_order = [
        "force_rank", "force_tag", "name", "rating", "rating_score",
        "close", "rrs_20", "etf_ret_20d", "bm_ret_20d",
        "divergence", "div_trend", "bull_alignment", "above_ma20",
        "above_ma60", "ema_bull", "dd_10d", "dd_20d",
        "weekly_broken", "vol_ratio", "volatility",
        "is_defensive", "has_split", "eos_penalty", "reason",
    ]
    df = df[[c for c in col_order if c in df.columns]]
    df = df.sort_values(["force_rank", "rating_score"], ascending=[True, False])
    df = df.reset_index(drop=True)

    return df


def print_rating_matrix(df: pd.DataFrame):
    """打印评级矩阵（格式化输出）"""
    if df.empty:
        print("无数据")
        return

    print("\n" + "=" * 100)
    print("  ETF 中线趋势评级矩阵")
    print("=" * 100)

    display_cols = [
        "force_rank", "force_tag", "name", "rating", "rating_score",
        "rrs_20", "dd_10d", "divergence", "ema_bull", "above_ma60", "has_split",
    ]
    display_df = df[[c for c in display_cols if c in df.columns]].copy()

    header = f"{'排名':<5} {'标签':<8} {'ETF':<10} {'评级':<4} {'得分':<6} {'RRS%':<8} {'10日回撤':<8} {'发散%':<8} {'EMA多头':<8} {'MA60上':<6} {'除权':<4}"
    print(header)
    print("-" * 100)

    for _, row in display_df.iterrows():
        rank = row.get("force_rank", 99)
        rank_str = str(rank) if rank < 99 else "-"
        tag = row.get("force_tag", "")
        name = row.get("name", "")
        rating = row.get("rating", "")
        score = row.get("rating_score", 0)
        rrs = row.get("rrs_20", 0)
        dd = row.get("dd_10d", 0)
        div = row.get("divergence", 0)
        ema_bull = "✓" if row.get("ema_bull") else "✗"
        abv_ma60 = "✓" if row.get("above_ma60") else "✗"
        split = "⚠" if row.get("has_split") else ""

        print(f"{rank_str:<5} {tag:<8} {name:<10} {rating:<4} {score:<6.1f} {rrs:<+8.2f} {dd:<8.2f} {div:<+8.2f} {ema_bull:<8} {abv_ma60:<6} {split:<4}")

    print("-" * 100)

    print("\n📊 评级分布:")
    for tier in ["A+", "A", "B", "C"]:
        count = (df["rating"] == tier).sum()
        bar = "█" * count
        print(f"  {tier}: {bar} ({count}只)")

    print("\n🏆 Force Rank 推荐:")
    top = df[df["force_rank"] < 99].sort_values("force_rank")
    for _, row in top.iterrows():
        print(f"  #{row['force_rank']} {row['force_tag']} {row['name']} [{row['rating']}] — {row['reason']}")

    print("\n📋 C类ETF详情:")
    c_df = df[df["rating"] == "C"]
    if not c_df.empty:
        for _, row in c_df.iterrows():
            tag = row.get("force_tag", "")
            extra = f" {tag}" if tag else ""
            print(f"  {row['name']}: {row['reason']}{extra}")
    else:
        print("  (无)")

    print("=" * 100)


def send_pushplus(msg: str, token: str = None, title: str = None):
    """通过 PushPlus 推送微信消息（支持markdown）"""
    if token is None:
        token = os.getenv("PUSHPLUS")
    if not token:
        print("⚠ PushPlus token 为空，跳过推送")
        return
    today = datetime.now().strftime('%Y%m%d')
    if title is None:
        title = f"ETF中线趋势评级 - {today}"
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": title,
        "content": msg,
        "template": "markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()
        if result.get('code') == 200:
            print("✅ PushPlus 已发送")
        else:
            print(f"⚠ PushPlus 发送失败: {result.get('msg', '未知错误')}")
    except Exception as e:
        print(f"⚠ PushPlus 异常: {e}")


def build_wechat_report(df: pd.DataFrame) -> str:
    """构建微信推送用的 Markdown 报告"""
    if df.empty:
        return "### ETF 中线趋势评级\n\n⚠ 无数据"

    today = datetime.now().strftime('%Y-%m-%d')
    lines = [
        f"## ETF 中线趋势评级 ({today})",
        "",
        "---",
        "",
        "### 🏆 Force Rank 推荐",
        "",
    ]

    top = df[df["force_rank"] < 99].sort_values("force_rank")
    for _, row in top.iterrows():
        tag = row.get("force_tag", "")
        name = row["name"]
        rating = row["rating"]
        score = row["rating_score"]
        rrs = row["rrs_20"]
        dd = row["dd_10d"]
        reason = row.get("reason", "")
        lines.append(
            f"**#{row['force_rank']} {tag} {name} [{rating}]**  "
            f"得分{score:.0f} | RRS {rrs:+.1f}% | 回撤{dd:.1f}% | {reason}"
        )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### 📊 评级矩阵")
    lines.append("")
    lines.append("| 评级 | ETF | 得分 | RRS% | 10日回撤 | 发散% | EMA多头 | MA60上 |")
    lines.append("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|")

    for _, row in df.iterrows():
        ema = "✓" if row.get("ema_bull") else "✗"
        ma60 = "✓" if row.get("above_ma60") else "✗"
        split = " ⚠" if row.get("has_split") else ""
        lines.append(
            f"| {row['rating']} | {row['name']}{split} | {row['rating_score']:.0f} | "
            f"{row['rrs_20']:+.1f} | {row['dd_10d']:.1f}% | {row['divergence']:+.1f} | {ema} | {ma60} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 📋 评级分布")
    lines.append("")
    for tier in ["A+", "A", "B", "C"]:
        count = (df["rating"] == tier).sum()
        bar = "█" * count
        lines.append(f"- **{tier}**: {bar} ({count}只)")

    a_count = (df["rating"].isin(["A+", "A"])).sum()
    b_count = (df["rating"] == "B").sum()
    lines.append("")
    lines.append(f"> 可操作标的: {a_count}只A级 + {b_count}只B级，共{a_count + b_count}只")

    return "\n".join(lines)


if __name__ == "__main__":
    print("ETF 中线趋势评级算法 V1.0")
    print(f"数据源: TDX本地 ({TDX_ROOT})")
    print(f"基准: 沪深300 (000300.SH)")
    print()

    df_result = calculate_all_etf_ratings()

    print_rating_matrix(df_result)

    output_path = os.path.join(os.path.dirname(__file__), "report_daily", "etf_midterm_rating.csv")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df_result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n结果已保存至: {output_path}")

    wechat_msg = build_wechat_report(df_result)
    send_pushplus(wechat_msg)