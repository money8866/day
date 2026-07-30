"""
ETF 评分模块 — 通过主题→ETF映射计算ETF趋势得分

数据源：
  - theme_stock_map_latest.json: 股票→所属主题列表 (stock["themes"])
  - theme_config.json: 主题英文KEY→main_etf / backup_etf / etf_codes
  - cache_daily/etf_{code}.csv: ETF K线数据（列: ts_code, trade_date, close, pct_chg, vol, ...）

评分逻辑：
  - 找到股票所属主题
  - 从 theme_config 找到对应的 ETF 代码
  - 读取 ETF 近20日K线
  - 根据均线排列、动量、量价计算趋势分(0-100)
"""

from __future__ import annotations

import csv
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 文件路径 ──
_THEME_MAP_DIR = r"D:\mystock\cache_daily"
_THEME_CONFIG_PATH = r"D:\mystock\solo\theme_config.json"
_CACHE_DAILY_DIR = r"D:\mystock\cache_daily"


def _load_theme_stock_map(trade_date: str) -> dict:
    """加载 theme_stock_map_latest.json"""
    paths = [
        os.path.join(_THEME_MAP_DIR, "theme_stock_map_latest.json"),
        os.path.join(_THEME_MAP_DIR, f"theme_stock_map_v2_{trade_date}.json"),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    logger.warning("theme_stock_map not found, tried: %s", paths)
    return {}


def _load_theme_config() -> dict:
    """加载 theme_config.json，并构建 中文名→(main_etf, backup_etf, etf_codes) 映射"""
    if not os.path.exists(_THEME_CONFIG_PATH):
        return {}
    with open(_THEME_CONFIG_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return raw  # 原样返回，外部按需查找


def _build_theme_etf_map(theme_config: dict) -> dict[str, dict]:
    """构建 {中文主题名: {main_etf, backup_etf, etf_codes}}

    为每个主题缓存 ETF 代码信息，避免重复遍历。
    """
    result: dict[str, dict] = {}
    for _key, cfg in theme_config.items():
        cn_name = cfg.get("name_cn", "")
        if not cn_name:
            continue
        result[cn_name] = {
            "main_etf": cfg.get("main_etf", "") or "",
            "backup_etf": cfg.get("backup_etf", "") or "",
            "etf_codes": cfg.get("etf_codes", []) or [],
        }
    return result


def _etf_csv_path(etf_code: str) -> str:
    """根据 ETF 代码（如 515980.SH）返回 CSV 文件路径"""
    pure = etf_code.replace(".SH", "").replace(".SZ", "")
    return os.path.join(_CACHE_DAILY_DIR, f"etf_{pure}.csv")


def _find_best_etf_csv(
    main_etf: str,
    backup_etf: str,
    etf_codes: list[str],
) -> str:
    """从候选 ETF 中找第一个 CSV 存在的，返回 ETF 代码"""
    candidates = []
    if main_etf:
        candidates.append(main_etf)
    if backup_etf and backup_etf != main_etf:
        candidates.append(backup_etf)
    for code in etf_codes:
        c = str(code).strip()
        if c and c not in candidates:
            candidates.append(c)

    for code in candidates:
        if os.path.exists(_etf_csv_path(code)):
            return code
    return candidates[0] if candidates else ""


def _load_etf_csv(etf_code: str) -> list[dict]:
    """加载 ETF K线 CSV 数据"""
    path = _etf_csv_path(etf_code)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    return []


def _score_etf_trend(rows: list[dict], trade_date: str) -> float:
    """根据ETF K线计算趋势分 (0-100)

    维度：
      1. MA排列：close > MA5 > MA10 > MA20 各+20分 (上限40)
      2. 近5日动量 (0-20分)
      3. 近20日动量 (0-20分)
      4. 上涨日量比 (0-10分)
    """
    if len(rows) < 25:
        return 50.0

    # 按交易日升序排列
    rows_sorted = sorted(rows, key=lambda r: r.get("trade_date", ""))

    # 定位到 trade_date 附近（找 <= trade_date 的最新行）
    end_idx = len(rows_sorted) - 1
    for i in range(len(rows_sorted) - 1, -1, -1):
        td = rows_sorted[i].get("trade_date", "")
        if td <= trade_date:
            end_idx = i
            break

    if end_idx < 25:
        return 50.0

    recent = rows_sorted[max(0, end_idx - 24):end_idx + 1]

    try:
        closes = [float(r["close"]) for r in recent]
        vols = [float(r["vol"]) for r in recent]
        pct_chgs = [float(r.get("pct_chg", 0) or 0) for r in recent]
    except (ValueError, KeyError):
        return 50.0

    if len(closes) < 10:
        return 50.0

    score = 50.0

    # 1. MA排列 (0-40分)
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10
    current_close = closes[-1]

    ma_score = 0
    if current_close > ma5:
        ma_score += 10
    if ma5 > ma10:
        ma_score += 10
    if ma10 > ma20:
        ma_score += 15
    if current_close > ma20:
        ma_score += 5
    score += ma_score

    # 2. 近5日动量 (0-20分)
    if len(pct_chgs) >= 5:
        mom_5d = sum(pct_chgs[-5:])
        if mom_5d > 5:
            score += 20
        elif mom_5d > 2:
            score += 10
        elif mom_5d > 0:
            score += 5
        elif mom_5d < -5:
            score -= 10

    # 3. 近20日动量 (0-20分)
    if len(pct_chgs) >= 20:
        mom_20d = sum(pct_chgs[-20:])
        if mom_20d > 10:
            score += 20
        elif mom_20d > 5:
            score += 15
        elif mom_20d > 0:
            score += 5
        elif mom_20d < -10:
            score -= 10

    # 4. 上涨日量比 (0-10分)
    if len(vols) >= 20 and len(pct_chgs) >= 20:
        up_days = [(v, p) for v, p in zip(vols[-20:], pct_chgs[-20:]) if p > 0]
        if up_days:
            up_vol_avg = sum(v for v, _ in up_days) / len(up_days)
            total_vol_avg = sum(vols[-20:]) / 20
            vol_ratio = up_vol_avg / total_vol_avg if total_vol_avg > 0 else 1.0
            if vol_ratio > 1.3:
                score += 10
            elif vol_ratio > 1.1:
                score += 5

    return max(0.0, min(100.0, score))


def calc_etf_score(
    ts_code: str,
    data_source: Any = None,
    trade_date: str = "",
    theme_map: Optional[dict] = None,
    theme_config: Optional[dict] = None,
) -> float:
    """计算个股关联ETF的趋势分

    Args:
        ts_code: 股票代码
        data_source: 数据源（未使用，保留接口兼容）
        trade_date: 交易日 YYYYMMDD
        theme_map: 预加载的主题映射（可选）
        theme_config: 预加载的主题配置（可选）

    Returns:
        0-100 的 ETF 评分
    """
    if theme_map is None:
        theme_map = _load_theme_stock_map(trade_date)
    if theme_config is None:
        theme_config = _load_theme_config()

    if not theme_map or not theme_config:
        return 50.0

    # 1. 从 theme_stock_map 找到股票的主题列表
    stocks_map = theme_map.get("stocks", {})
    stock_info = stocks_map.get(ts_code, {})

    # 实际字段名是 "themes"，不是 "mapped_themes"
    theme_names: list[str] = stock_info.get("themes", []) if stock_info else []

    # 兼容旧格式：从 themes 倒查（主题映射表中 themes 是 {主题名: [股票列表]}）
    if not theme_names:
        for tname, stocks in theme_map.get("themes", {}).items():
            for s in stocks:
                if s.get("code", "") == ts_code:
                    theme_names = [tname]
                    break
        if not theme_names:
            return 50.0

    # 2. 构建中文名→ETF 映射
    theme_etf_map = _build_theme_etf_map(theme_config)

    # 3. 逐主题寻找可用的 ETF
    etf_code = ""
    for tn in theme_names:
        info = theme_etf_map.get(tn)
        if not info:
            continue
        code = _find_best_etf_csv(
            info["main_etf"],
            info["backup_etf"],
            info["etf_codes"],
        )
        if code:
            etf_code = code
            logger.debug("股票 %s 主题 %s → ETF %s", ts_code, tn, code)
            break

    if not etf_code:
        logger.debug("股票 %s 所有主题均无可读 ETF CSV 数据，ET评分中性", ts_code)
        return 50.0

    # 4. 读取 ETF K线数据并评分
    rows = _load_etf_csv(etf_code)
    if not rows:
        logger.debug("ETF %s CSV 数据不存在，ET评分中性", etf_code)
        return 50.0

    etf_score = _score_etf_trend(rows, trade_date)
    logger.debug("ETF %s score=%.1f for stock %s", etf_code, etf_score, ts_code)

    return etf_score
