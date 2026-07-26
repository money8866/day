"""ETF相关性自动分析模块.

每日收盘后自动执行：
  1. 获取各ETF的持仓数据 (Tushare fund_portfolio)
  2. 计算ETF与主题的持仓重叠度
  3. 根据重叠度更新主题的ETF映射配置
  4. 自动回写 theme_config.json 和 etf_mapping.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import tushare as ts

from theme_kg_v3.config.settings import (
    CONFIG_DIR,
    DAILY_CACHE_DIR,
    ENV_PATH,
    ETF_ANALYSIS,
    ETF_HOLDINGS_CACHE_DIR,
    ETF_MAPPING_PATH,
    THEME_CONFIG_PATH,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# Tushare 客户端单例
# ────────────────────────────────────────────────────────────

_pro: Optional[ts.pro_api] = None


def init_tushare() -> ts.pro_api:
    """初始化 Tushare Pro 客户端（加载 .env 中的 token）.

    Returns:
        Tushare pro_api 实例.
    """
    global _pro
    if _pro is not None:
        return _pro

    token = os.environ.get("TUSHARE_TOKEN")
    if not token and ENV_PATH.exists():
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TUSHARE_TOKEN"):
                    token = line.split("=", 1)[1].strip().strip("\"' ")
                    if token:
                        os.environ["TUSHARE_TOKEN"] = token
                        break

    if not token:
        logger.error("TUSHARE_TOKEN 未配置，请在 .env 中设置")
        raise RuntimeError("TUSHARE_TOKEN 未配置")

    ts.set_token(token)
    _pro = ts.pro_api()
    logger.info("Tushare 客户端初始化成功")
    return _pro


def get_pro() -> ts.pro_api:
    """获取 Tushare Pro 客户端（已初始化则复用）."""
    if _pro is None:
        return init_tushare()
    return _pro


# ────────────────────────────────────────────────────────────
# 交易日判定
# ────────────────────────────────────────────────────────────

def get_trade_date() -> str:
    """获取最近可用交易日.

    逻辑:
      - 当前时间 < 16:00 → 取前一交易日
      - 当前时间 >= 16:00 → 取当天（若当天是交易日）或最近前一交易日

    Returns:
        YYYYMMDD 格式的交易日期.
    """
    pro = get_pro()
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    start = (now - timedelta(days=10)).strftime("%Y%m%d")

    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=today)
        if cal is None or cal.empty:
            logger.warning("交易日历获取失败，回退到今天")
            return today

        if now.hour < 16:
            # 收盘前 → 前一个交易日
            open_days = cal[(cal["is_open"] == 1) & (cal["cal_date"] < today)]
            if open_days.empty:
                return today
            return str(open_days.iloc[-1]["cal_date"])

        # 收盘后 → 如果今天是交易日则用今天
        today_row = cal[cal["cal_date"] == today]
        if not today_row.empty and today_row.iloc[0]["is_open"] == 1:
            return today

        open_days = cal[(cal["is_open"] == 1) & (cal["cal_date"] < today)]
        if open_days.empty:
            return today
        return str(open_days.iloc[-1]["cal_date"])

    except Exception as e:
        logger.warning("交易日判定失败: %s，回退到今天", e)
        return today


# ────────────────────────────────────────────────────────────
# ETF 持仓数据获取（带缓存）
# ────────────────────────────────────────────────────────────

def _ensure_cache_dir():
    """确保缓存目录存在."""
    ETF_HOLDINGS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_etf_holdings(etf_code: str, trade_date: str) -> pd.DataFrame:
    """获取单只ETF的持仓数据（优先从缓存读取）.

    Args:
        etf_code: ETF 代码（如 159819.SZ）.
        trade_date: 交易日期 YYYYMMDD.

    Returns:
        DataFrame，包含 stk_code, stk_name, stk_weight 等字段.
    """
    _ensure_cache_dir()
    cache_file = ETF_HOLDINGS_CACHE_DIR / f"{etf_code}_{trade_date}.pkl"

    # 尝试从缓存读取
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and not df.empty:
                logger.debug("缓存命中: %s", cache_file)
                return df
        except Exception:
            pass

    # 从 Tushare 获取
    pro = get_pro()
    max_holdings = ETF_ANALYSIS["max_holdings_per_etf"]

    try:
        # fund_portfolio 获取 ETF 持仓
        df = pro.fund_portfolio(ts_code=etf_code, date=trade_date)
        time.sleep(0.12)  # 限速

        if df is None or df.empty:
            # 尝试使用近期日期
            for days_back in [7, 14, 30, 60]:
                past_date = (
                    datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=days_back)
                ).strftime("%Y%m%d")
                df = pro.fund_portfolio(ts_code=etf_code, date=past_date)
                time.sleep(0.12)
                if df is not None and not df.empty:
                    break

        if df is not None and not df.empty:
            # 按权重排序取前N大持仓
            if "stk_weight" in df.columns:
                df = df.sort_values("stk_weight", ascending=False).head(max_holdings)

            # 标准化 stk_code 格式（补齐后缀）
            if "stk_code" in df.columns:
                df["stk_code"] = df["stk_code"].apply(normalize_code)

            df.to_pickle(cache_file)
            logger.info("获取 %s 持仓: %d 只", etf_code, len(df))
            return df

        logger.debug("%s 无持仓数据", etf_code)
        return pd.DataFrame()

    except Exception as e:
        logger.warning("获取 %s 持仓失败: %s", etf_code, e)
        return pd.DataFrame()


def normalize_code(code: str) -> str:
    """补齐股票代码后缀格式.

    例如: '600519' -> '600519.SH', '000858' -> '000858.SZ'

    Args:
        code: 原始股票代码.

    Returns:
        带后缀的标准 ts_code.
    """
    code = code.strip()
    if "." in code:
        return code.upper()

    code_num = int(code)
    if code_num >= 600000 or (600000 <= code_num <= 699999):
        return f"{code}.SH"
    elif code_num >= 900000:
        return f"{code}.SH"
    elif code_num >= 300000:
        return f"{code}.SZ"
    elif code_num >= 200000:
        return f"{code}.SZ"
    elif code_num >= 159000:
        return f"{code}.SZ"
    else:
        return f"{code}.SZ"


# ────────────────────────────────────────────────────────────
# 主题-ETF 匹配度计算
# ────────────────────────────────────────────────────────────

def _get_theme_stock_set(theme_cfg: Dict[str, Any]) -> set:
    """从主题配置提取核心股票集合.

    Args:
        theme_cfg: 主题配置字典.

    Returns:
        股票 ts_code 集合.
    """
    stocks = set()
    for key in ("leaders", "core_stocks", "secondary"):
        codes = theme_cfg.get(key, [])
        if isinstance(codes, list):
            for c in codes:
                stocks.add(normalize_code(c))
    return stocks


def calc_etf_theme_match_score(
    etf_code: str,
    theme_stocks: set,
    trade_date: str,
) -> Tuple[float, int, List[str]]:
    """计算单只ETF与主题的匹配分数.

    算法:
      取 ETF 前 N 大持仓，统计与主题核心股票的
      重叠数量 / min(ETF持仓数, 主题核心股数)

    Args:
        etf_code: ETF 代码.
        theme_stocks: 主题核心股票 ts_code 集合.
        trade_date: 交易日期.

    Returns:
        (match_score, overlap_count, matched_stocks)
        match_score: 0~100 的匹配分数.
        overlap_count: 重叠股票数量.
        matched_stocks: 重叠股票代码列表.
    """
    holdings = fetch_etf_holdings(etf_code, trade_date)
    if holdings.empty:
        return 0.0, 0, []

    # ETF 持仓股票集合
    if "stk_code" in holdings.columns:
        etf_stocks = set(holdings["stk_code"].dropna().unique())
    else:
        return 0.0, 0, []

    if not etf_stocks:
        return 0.0, 0, []

    # 计算重叠
    matched = theme_stocks & etf_stocks
    overlap = len(matched)
    if overlap == 0:
        return 0.0, 0, []

    # 匹配分 = 重叠数 / min(ETF持仓数, 主题核心股数) * 100
    denominator = min(len(etf_stocks), len(theme_stocks))
    score = round((overlap / denominator) * 100, 1) if denominator > 0 else 0.0

    return score, overlap, list(matched)


def calc_all_theme_etf_matches(
    theme_config: Dict[str, Any],
    trade_date: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """计算所有主题与所有已知ETF的匹配度.

    遍历每个主题，对该主题配置中已关联的ETF计算匹配分.

    Args:
        theme_config: theme_config.json 完整内容.
        trade_date: 交易日期.

    Returns:
        {theme_code: [{etf_code, score, overlap, matched_stocks}, ...]}
    """
    results: Dict[str, List[Dict[str, Any]]] = {}

    for code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue

        theme_stocks = _get_theme_stock_set(cfg)
        if not theme_stocks:
            logger.debug("主题 %s 无核心股票，跳过", code)
            continue

        # 收集该主题关联的所有ETF
        etf_codes = cfg.get("etf_codes", [])
        main_etf = cfg.get("main_etf", "")

        # 也检查 etf_mapping.json 中的关联
        all_etfs = list(etf_codes)
        if main_etf and main_etf not in all_etfs:
            all_etfs.insert(0, main_etf)

        if not all_etfs:
            logger.debug("主题 %s 无关联ETF，跳过", code)
            continue

        scores = []
        for etf in all_etfs:
            if not etf:
                continue
            score, overlap, matched = calc_etf_theme_match_score(etf, theme_stocks, trade_date)
            scores.append({
                "etf_code": etf,
                "score": score,
                "overlap": overlap,
                "matched_stocks": matched,
            })

        # 按分数降序排列
        scores.sort(key=lambda x: x["score"], reverse=True)
        results[code] = scores

    return results


# ────────────────────────────────────────────────────────────
# 配置更新
# ────────────────────────────────────────────────────────────

def update_config_etf_fields(
    theme_config: Dict[str, Any],
    match_results: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """根据ETF匹配结果更新 theme_config.json 中的ETF字段.

    更新规则:
      1. main_etf → 匹配分最高的ETF
      2. etf_codes → 按匹配分降序保留所有得分 >= min_match_score 的ETF
      3. correlation_etfs → 用得分适中但非主ETF的作为关联ETF

    Args:
        theme_config: 原始配置字典.
        match_results: calc_all_theme_etf_matches() 的返回结果.

    Returns:
        更新后的配置字典.
    """
    config = theme_config.copy()
    min_score = ETF_ANALYSIS["min_match_score"]
    top_n_corr = ETF_ANALYSIS["top_n_correlation"]

    for theme_code, scores in match_results.items():
        if theme_code not in config:
            continue

        cfg = config[theme_code]
        if not scores:
            continue

        # 筛选出有效匹配
        valid = [s for s in scores if s["score"] >= min_score]
        if not valid:
            continue

        # main_etf: 最高分的ETF
        best = valid[0]
        cfg["main_etf"] = best["etf_code"]

        # etf_codes: 所有有效匹配按分降序
        cfg["etf_codes"] = [s["etf_code"] for s in valid]

        # correlation_etfs: 除主ETF外的前N个作为关联
        corr_etfs = []
        for s in valid[1:top_n_corr]:
            corr_etfs.append({
                "code": s["etf_code"],
                "name": s["etf_code"],
                "correlation": round(s["score"] / 100, 2),
            })
        if corr_etfs:
            cfg["correlation_etfs"] = corr_etfs

        # 日志输出
        logger.info(
            "   %s(%s): main_etf=%s score=%.1f overlap=%d",
            cfg.get("name_cn", ""), theme_code,
            best["etf_code"], best["score"], best["overlap"],
        )

    return config


def update_etf_mapping_from_config(
    theme_config: Dict[str, Any],
) -> Dict[str, Any]:
    """根据 theme_config.json 同步更新 etf_mapping.json.

    对每个主题，收集其 ETF 信息写入 etf_mapping.json 格式.

    Args:
        theme_config: 最新版 theme_config.

    Returns:
        完整的 etf_mapping 字典.
    """
    from theme_kg_v3.config.settings import ETF_MAPPING_PATH

    # 读取现有映射作为基础
    etf_mapping: Dict[str, Any] = {}
    if ETF_MAPPING_PATH.exists():
        try:
            with open(ETF_MAPPING_PATH, encoding="utf-8") as f:
                etf_mapping = json.load(f)
        except Exception:
            etf_mapping = {"_meta": {"version": "3.0", "description": "自动生成", "last_updated": ""}}

    if "_meta" not in etf_mapping:
        etf_mapping["_meta"] = {"version": "3.0", "description": "自动生成", "last_updated": ""}

    today = datetime.now().strftime("%Y-%m-%d")

    for code, cfg in theme_config.items():
        if not isinstance(cfg, dict):
            continue

        name_cn = cfg.get("name_cn", code)
        main_etf = cfg.get("main_etf", "")
        etf_codes = cfg.get("etf_codes", [])
        corr_etfs = cfg.get("correlation_etfs", [])

        # 构建或更新 etf_mapping 条目
        entry = etf_mapping.get(code, {})
        entry["name_cn"] = name_cn

        # main_etf
        if main_etf:
            entry["main_etf"] = {
                "code": main_etf,
                "name": _guess_etf_name(main_etf),
                "market": "SH" if main_etf.endswith(".SH") else "SZ",
            }

        # backup_etfs: 非主ETF的其余ETF
        backup = [c for c in etf_codes if c != main_etf]
        entry["backup_etfs"] = [
            {"code": c, "name": _guess_etf_name(c)} for c in backup
        ]

        # etf_industry_tags
        if "etf_industry_tags" not in entry or not entry["etf_industry_tags"]:
            tag = name_cn[:8]
            entry["etf_industry_tags"] = [tag]

        # correlation_etfs (来自配置)
        mapped_corr = []
        for corr in corr_etfs:
            if isinstance(corr, dict):
                c_code = corr.get("code", "")
                if c_code:
                    mapped_corr.append({
                        "code": c_code,
                        "name": _guess_etf_name(c_code),
                        "correlation": corr.get("correlation", 0.5),
                    })
        entry["correlation_etfs"] = mapped_corr

        etf_mapping[code] = entry

    etf_mapping["_meta"]["last_updated"] = today
    return etf_mapping


def _guess_etf_name(etf_code: str) -> str:
    """根据ETF代码推测名称（兜底用，实际应从映射中读取）."""
    known = {
        "159819.SZ": "人工智能ETF",
        "512480.SH": "半导体ETF",
        "562500.SH": "机器人ETF",
        "159858.SZ": "创新药ETF",
        "159732.SZ": "消费电子ETF",
        "516520.SH": "智能驾驶ETF",
        "512660.SH": "军工ETF",
        "515030.SH": "新能源车ETF",
        "518880.SH": "黄金ETF",
        "512800.SH": "银行ETF",
        "512880.SH": "证券ETF",
        "515220.SH": "煤炭ETF",
        "159611.SZ": "电力ETF",
        "159928.SZ": "消费ETF",
        "159869.SZ": "游戏ETF",
        "512690.SH": "酒ETF",
        "515170.SH": "食品饮料ETF",
        "159540.SZ": "信创ETF",
        "159356.SZ": "低空经济ETF",
    }
    return known.get(etf_code, etf_code)


def write_config(config: Dict[str, Any], path: Path) -> bool:
    """将配置字典写入 JSON 文件（格式化输出）.

    Args:
        config: 配置字典.
        path: 输出路径.

    Returns:
        写入成功返回 True.
    """
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info("已写入: %s", path)
        return True
    except Exception as e:
        logger.error("写入 %s 失败: %s", path, e)
        return False


# ────────────────────────────────────────────────────────────
# 主编排
# ────────────────────────────────────────────────────────────

def run_etf_analysis(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """执行完整的ETF相关性分析流水线.

    Args:
        trade_date: 交易日期（自动判定若为None）.

    Returns:
        执行摘要.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    logger.info("=" * 60)
    logger.info("ETF 相关性自动分析 [%s]", trade_date)
    logger.info("=" * 60)

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "themes_analyzed": 0,
        "etfs_checked": 0,
        "config_updated": False,
        "mapping_updated": False,
    }

    # 1. 加载当前配置
    if not THEME_CONFIG_PATH.exists():
        logger.error("theme_config.json 不存在: %s", THEME_CONFIG_PATH)
        return summary

    with open(THEME_CONFIG_PATH, encoding="utf-8") as f:
        theme_config = json.load(f)

    logger.info("已加载 %d 个主题配置", len(theme_config))

    # 2. 计算所有主题的ETF匹配度
    match_results = calc_all_theme_etf_matches(theme_config, trade_date)
    summary["themes_analyzed"] = len(match_results)
    summary["etfs_checked"] = sum(
        len(scores) for scores in match_results.values()
    )

    # 3. 更新 theme_config.json
    updated_config = update_config_etf_fields(theme_config, match_results)
    if write_config(updated_config, THEME_CONFIG_PATH):
        summary["config_updated"] = True

    # 4. 同步更新 etf_mapping.json
    etf_mapping = update_etf_mapping_from_config(updated_config)
    if write_config(etf_mapping, ETF_MAPPING_PATH):
        summary["mapping_updated"] = True

    # 5. 输出摘要
    logger.info("-" * 60)
    logger.info("执行摘要:")
    logger.info("  分析主题: %d", summary["themes_analyzed"])
    logger.info("  检查ETF: %d", summary["etfs_checked"])
    logger.info("  theme_config 更新: %s", summary["config_updated"])
    logger.info("  etf_mapping 更新: %s", summary["mapping_updated"])
    logger.info("=" * 60)

    return summary


def quick_etf_check(etf_code: str, trade_date: Optional[str] = None) -> None:
    """快速检查单只ETF的持仓情况（调试用）.

    Args:
        etf_code: ETF 代码.
        trade_date: 交易日期.
    """
    if trade_date is None:
        trade_date = get_trade_date()

    holdings = fetch_etf_holdings(etf_code, trade_date)
    if holdings.empty:
        print(f"{etf_code} 无持仓数据或获取失败")
        return

    print(f"\n{etf_code} 持仓 ({len(holdings)} 只):")
    print("-" * 60)
    for _, row in holdings.head(10).iterrows():
        code = row.get("stk_code", "")
        name = row.get("stk_name", "")
        weight = row.get("stk_weight", "")
        print(f"  {code:<12s} {name:<10s} 权重={weight}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    run_etf_analysis()
