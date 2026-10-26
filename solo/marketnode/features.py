# -*- coding: utf-8 -*-
"""每日全市场特征计算。

数据源：
    - daily_cache       : 全市场日线 (ts_code, trade_date, pct_chg, high, low, close, pre_close, amount, vol)
    - index_daily_cache : 主要指数日线
    - full_market_stats_*.json : 官方涨跌停/炸板统计（涨停池、炸板率）

输出特征（按交易日一行）：
    指数趋势 / 全市场广度 / 成交活跃度 / 短线情绪（涨停梯队、晋级率、炸板率、昨日强势股反馈）

涨停/跌停判定沿用现平台口径：主板 10%、创业板(300)/科创板(688)/301 20%，
    zt:  pct_chg >= limit*0.99 且 < limit+0.1
    dt:  pct_chg <= -limit*0.99 且 > -limit-0.1
连板高度 = 个股连续涨停交易日数（用运行时长度计算）。
炸板率：优先取 full_market_stats 官方值；缺失时用「盘中触及涨停但未封板」的日线代理值。
"""
import os
import json

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_market_daily(db_path):
    """加载全市场日线精简列，按 (ts_code, trade_date) 排序。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    cols = "ts_code, trade_date, pct_chg, high, low, close, pre_close, amount, vol"
    df = pd.read_sql_query(f"SELECT {cols} FROM daily_cache ORDER BY ts_code, trade_date", conn)
    conn.close()
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def load_index_daily(db_path, codes):
    """加载指定指数日线，返回 {code: DataFrame}（含 pct_chg）。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" for _ in codes)
    df = pd.read_sql_query(
        f"SELECT * FROM index_daily_cache WHERE ts_code IN ({placeholders}) ORDER BY ts_code, trade_date",
        conn, params=list(codes),
    )
    conn.close()
    if df.empty:
        return {}
    df["trade_date"] = df["trade_date"].astype(str)
    if "pct_chg" not in df.columns:
        df = df.sort_values(["ts_code", "trade_date"])
        df["pre_close"] = df.groupby("ts_code")["close"].shift(1)
        df["pct_chg"] = (df["close"] / df["pre_close"] - 1) * 100
    # 补充缺失的前收盘（首个交易日 NaN 处理为 0）
    df["pct_chg"] = df["pct_chg"].fillna(0.0)
    return {c: g.drop(columns="ts_code") for c, g in df.groupby("ts_code")}


def load_official_zhaban(stats_dir):
    """读取 cache_daily/full_market_stats_*.json 中的官方炸板率与涨停/跌停数。

    返回 DataFrame: index=trade_date, columns=[zhaban_rate_official, zt_count_official, dt_count_official]
    """
    rows = []
    for fn in os.listdir(stats_dir):
        if not fn.startswith("full_market_stats_") or not fn.endswith(".json"):
            continue
        path = os.path.join(stats_dir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
        except Exception:
            continue
        d = j.get("trade_date")
        if not d:
            continue
        rows.append({
            "trade_date": str(d),
            "zhaban_rate_official": j.get("broken_rate"),
            "zt_count_official": j.get("zt_count"),
            "dt_count_official": j.get("dt_count"),
        })
    if not rows:
        return pd.DataFrame(columns=["trade_date", "zhaban_rate_official", "zt_count_official", "dt_count_official"])
    out = pd.DataFrame(rows)
    out["trade_date"] = out["trade_date"].astype(str)
    return out.set_index("trade_date")


# ---------------------------------------------------------------------------
# 特征计算
# ---------------------------------------------------------------------------

def _limit_ratio(ts_code_series, prefixes):
    """返回 Series: 涨停幅度（百分比数值，如 10.0 / 20.0）。"""
    ratio = pd.Series(10.0, index=ts_code_series.index)
    for p in prefixes:
        ratio = ratio.where(~ts_code_series.str.startswith(p), 20.0)
    return ratio


def compute_features(df_market, df_official_zhaban, index_map, cfg):
    """计算每日市场特征。返回 DataFrame（index=trade_date）。"""
    pref = cfg["data"]["limit_20pct_prefix"]
    df = df_market.copy()

    # 1) 涨停/跌停判定
    df["lr"] = _limit_ratio(df["ts_code"], pref)
    df["is_zt"] = (df["pct_chg"] >= df["lr"] * 0.99) & (df["pct_chg"] < df["lr"] + 0.1)
    df["is_dt"] = (df["pct_chg"] <= -df["lr"] * 0.99) & (df["pct_chg"] > -df["lr"] - 0.1)

    # 2) 炸板代理：盘中触及涨停价(high)但未封板(收盘非涨停)
    df["limit_price"] = df["pre_close"] * (1 + df["lr"] / 100.0)
    df["touched_limit"] = df["high"] >= df["limit_price"] * 0.995
    df["is_zhaban_proxy"] = df["touched_limit"] & (~df["is_zt"])

    # 3) 连板高度（连续涨停运行长度）
    df = df.sort_values(["ts_code", "trade_date"])
    df["prev_zt"] = df.groupby("ts_code")["is_zt"].shift(1).fillna(False)
    df["grp_chg"] = (df["is_zt"] != df["prev_zt"]).astype(int)
    df["grp_id"] = df.groupby("ts_code")["grp_chg"].cumsum()
    df["streak"] = df.groupby(["ts_code", "grp_id"]).cumcount() + 1
    df.loc[~df["is_zt"], "streak"] = 0
    # 昨日强势股标记
    df["prev_streak"] = df.groupby("ts_code")["streak"].shift(1).fillna(0)
    df["prev_is_zt"] = df["prev_zt"].fillna(False)
    df["prev_is_lb"] = df["prev_streak"] >= 2

    # 4) 均线 / 新高新低
    df["ma20"] = df.groupby("ts_code")["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["above_ma20"] = df["close"] > df["ma20"]
    df["hh20"] = df.groupby("ts_code")["high"].transform(lambda s: s.rolling(20, min_periods=20).max())
    df["prev_hh20"] = df.groupby("ts_code")["hh20"].shift(1)
    df["ll20"] = df.groupby("ts_code")["low"].transform(lambda s: s.rolling(20, min_periods=20).min())
    df["prev_ll20"] = df.groupby("ts_code")["ll20"].shift(1)
    df["is_new_high"] = (df["high"] > df["prev_hh20"]) & df["prev_hh20"].notna()
    df["is_new_low"] = (df["low"] < df["prev_ll20"]) & df["prev_ll20"].notna()

    # 5) 涨停梯队
    df["board1"] = df["is_zt"] & (df["streak"] == 1)
    df["board2"] = df["is_zt"] & (df["streak"] == 2)
    df["board3"] = df["is_zt"] & (df["streak"] == 3)
    df["board4p"] = df["is_zt"] & (df["streak"] >= 4)

    # 6) 按交易日期聚合（全向量化）
    df["up_b"] = df["pct_chg"] > 0
    df["down_b"] = df["pct_chg"] < 0
    df["flat_b"] = df["pct_chg"] == 0
    df["zt_y_pcnt_v"] = df["pct_chg"].where(df["prev_is_zt"])
    df["zt_y_pos_v"] = (df["pct_chg"] > 0).where(df["prev_is_zt"])
    df["lb_y_pcnt_v"] = df["pct_chg"].where(df["prev_is_lb"])
    df["lb_y_pos_v"] = (df["pct_chg"] > 0).where(df["prev_is_lb"])

    g = df.groupby("trade_date")
    feat = pd.DataFrame({
        "total": g["ts_code"].size(),
        "up_count": g["up_b"].sum(),
        "down_count": g["down_b"].sum(),
        "flat_count": g["flat_b"].sum(),
        "zt_count": g["is_zt"].sum(),
        "dt_count": g["is_dt"].sum(),
        "zhaban_proxy": g["is_zhaban_proxy"].sum(),
        "board1": g["board1"].sum(),
        "board2": g["board2"].sum(),
        "board3": g["board3"].sum(),
        "board4p": g["board4p"].sum(),
        "max_board": g["streak"].max(),
        "new_high": g["is_new_high"].sum(),
        "new_low": g["is_new_low"].sum(),
        "above_ma20": g["above_ma20"].sum(),
        "amount_total": g["amount"].sum(),
        "zt_y_pcnt": g["zt_y_pcnt_v"].mean(),
        "zt_y_pos": g["zt_y_pos_v"].mean() * 100,
        "lb_y_pcnt": g["lb_y_pcnt_v"].mean(),
        "lb_y_pos": g["lb_y_pos_v"].mean() * 100,
    })
    feat = feat.sort_index()
    feat.index.name = "trade_date"

    # 7) 派生：涨幅比例 / 站上MA20比例 / 炸板率(代理)
    feat["up_ratio"] = feat["up_count"] / feat["total"].replace(0, np.nan) * 100
    feat["down_ratio"] = feat["down_count"] / feat["total"].replace(0, np.nan) * 100
    feat["above_ma20_ratio"] = feat["above_ma20"] / feat["total"].replace(0, np.nan) * 100
    touched = feat["zt_count"] + feat["zhaban_proxy"]
    feat["zhaban_rate_proxy"] = (feat["zhaban_proxy"] / touched.replace(0, np.nan)) * 100

    # 8) 晋级率（今日二板 / 昨日首板；今日三板 / 昨日二板）
    feat["board1_prev"] = feat["board1"].shift(1)
    feat["board2_prev"] = feat["board2"].shift(1)
    feat["promo12"] = feat["board2"] / feat["board1_prev"].replace(0, np.nan) * 100
    feat["promo23"] = feat["board3"] / feat["board2_prev"].replace(0, np.nan) * 100

    # 9) 成交额（千元 -> 亿元）及相对20日均值
    feat["amount_yi"] = feat["amount_total"] / 1e5
    feat["amount_ma20"] = feat["amount_yi"].rolling(20, min_periods=10).mean()
    feat["amount_ratio"] = feat["amount_yi"] / feat["amount_ma20"].replace(0, np.nan)

    # 10) 合并官方炸板率
    if df_official_zhaban is not None and not df_official_zhaban.empty:
        feat = feat.join(df_official_zhaban, how="left")
    else:
        for c in ["zhaban_rate_official", "zt_count_official", "dt_count_official"]:
            feat[c] = np.nan
    feat["zhaban_rate"] = feat["zhaban_rate_official"].combine_first(feat["zhaban_rate_proxy"])
    feat["zhaban_source"] = np.where(feat["zhaban_rate_official"].notna(), "official", "high_proxy")

    # 11) 指数特征：基准指数 / 大小盘强弱
    small = index_map.get(cfg["data"]["small_cap_index"])
    large = index_map.get(cfg["data"]["large_cap_index"])
    bench = index_map.get(cfg["data"]["benchmark_index"])

    def _idx_series(idx_df, col="pct_chg"):
        if idx_df is None:
            return pd.Series(dtype=float)
        return idx_df.set_index("trade_date")[col]

    bench_pct = _idx_series(bench).rename("index_pct")
    small_pct = _idx_series(small).rename("small_pct")
    large_pct = _idx_series(large).rename("large_pct")
    feat = feat.join(bench_pct, how="left").join(small_pct, how="left").join(large_pct, how="left")
    feat["small_large_diff"] = feat["small_pct"] - feat["large_pct"]

    # 指数趋势（MA5 相对 MA20）
    def _idx_ma_trend(idx_df):
        if idx_df is None or "close" not in idx_df.columns:
            return pd.Series(dtype=float)
        s = idx_df.set_index("trade_date")["close"]
        ma5 = s.rolling(5).mean()
        ma20 = s.rolling(20).mean()
        return (ma5 / ma20 - 1) * 100

    feat["index_ma_trend"] = _idx_ma_trend(bench).reindex(feat.index)

    # 12) 变化量（用于规则引擎）
    for col in ["zt_count", "dt_count", "board1", "board2", "board3", "max_board",
                "promo12", "promo23", "zhaban_rate", "zt_y_pcnt", "lb_y_pcnt",
                "up_ratio", "above_ma20_ratio", "amount_ratio", "index_pct",
                "new_high", "new_low"]:
        feat[f"{col}_d1"] = feat[col].diff(1)
        feat[f"{col}_d3"] = feat[col].diff(3)
        feat[f"{col}_d5"] = feat[col].diff(5)

    return feat


def _db_last_trade_date(db_path):
    """数据库中全市场日线的最新交易日（用于判断特征缓存是否过期）。"""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()
        conn.close()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def build_features(cfg=None, force=False):
    """构建（并缓存）全历史每日特征。返回 DataFrame。"""
    if cfg is None:
        from . import load_config
        cfg = load_config()
    out_dir = cfg["data"]["output_dir"]
    cache_file = os.path.join(out_dir, cfg["data"]["feature_cache_file"])
    if os.path.exists(cache_file) and not force:
        feat = pd.read_parquet(cache_file)
        # 缓存已覆盖到数据库最新交易日则直接复用，否则重建（避免用旧缓存判断当天节点）
        db_last = _db_last_trade_date(cfg["data"]["db_path"])
        if not db_last or (len(feat) and str(feat.index[-1]) >= db_last):
            return feat
        print(f"[features] 缓存末日 {feat.index[-1]} < 数据库最新 {db_last}，重建 ...")

    print("[features] 加载全市场日线 ...")
    df = load_market_daily(cfg["data"]["db_path"])
    print(f"[features] 日线 {len(df)} 行")
    print("[features] 加载指数日线 ...")
    codes = [cfg["data"]["small_cap_index"], cfg["data"]["large_cap_index"], cfg["data"]["benchmark_index"]]
    index_map = load_index_daily(cfg["data"]["db_path"], codes)
    print("[features] 加载官方炸板统计 ...")
    official = load_official_zhaban(cfg["data"]["stats_dir"])
    print(f"[features] 官方炸板统计 {len(official)} 天")

    print("[features] 计算特征（可能耗时 1-3 分钟）...")
    feat = compute_features(df, official, index_map, cfg)

    os.makedirs(out_dir, exist_ok=True)
    feat.to_parquet(cache_file)
    print(f"[features] 已缓存 {len(feat)} 个交易日 -> {cache_file}")
    return feat


if __name__ == "__main__":
    build_features()