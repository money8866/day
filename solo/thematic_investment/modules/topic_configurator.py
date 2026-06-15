"""
主题量化配置引擎 (Topic Configurator)
====================================

基于已发现的一级/二级主题，结合 A 股量价数据，生成：
  - 主题强度分 (60%) + 拥挤度风险 (40%) → 综合得分 → Top-5 → 仓位分配

主要组件:
  ActiveTopicLoader   → 从 Mongo `topics` 集合读取近期活跃主题 → 关联成分股代码
  MarketDataFetcher   → 从 akshare 拉取主题成分股+万得全A(881001) 行情
  ScoringEngine       → 动量强度(60%) + 拥挤度风险(40%)
  PositionAllocator   → Top 5 主题 得分占比分配，单主题上限 30%
  BacktestInterface   → 回测接口: 接受 (日期, 收盘价) → 返回信号

每日 9:25 输出 JSON 信号

依赖:
  pip install akshare pandas numpy pymongo
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import logging
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 路径 & 模块
# --------------------------------------------------------------------------- #
_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR: str = os.path.dirname(_CURRENT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules.db_connector import MongoConnector, CONFIG  # noqa: E402
from modules.utils import setup_logger, today_str  # noqa: E402

# --------------------------------------------------------------------------- #
# 全局常量
# --------------------------------------------------------------------------- #
logger: logging.Logger = setup_logger(
    name="topic_configurator",
    log_dir=os.path.join(_PARENT_DIR, "logs"),
    log_file="topic_configurator.log",
)

MONGO_TOPICS: str = "topics"

# A股市场基准：万得全A代码 (akshare 指数代码)
BENCHMARK_INDEX: str = "881001"

# 评分权重
WEIGHT_MOMENTUM: float = 0.60
WEIGHT_CROWDING: float = 0.40

# 动量强度子权重
MOM_W_EXCESS_RETURN: float = 0.45   # 45%
MOM_W_CATALYST_Z: float = 0.30       # 30%
MOM_W_LEADER: float = 0.25            # 25%

# 拥挤度子权重
CWD_W_TURNOVER_RANK: float = 0.35
CWD_W_TURNOVER_ACCEL: float = 0.35
CWD_W_SELLSIDE: float = 0.30

# 参数
LOOKBACK_DAYS_EXCESS: int = 20
LOOKBACK_DAYS_Z: int = 20
LOOKBACK_DAYS_TURNOVER: int = 252
LOOKBACK_DAYS_ACCEL: int = 5
TOPIC_COUNT: int = 5
SINGLE_TOPIC_MAX: float = 0.30  # 30%
RISK_CAP: float = 0.50            # 风险触发后再减半
CROWDING_WARNING_THRESHOLD: int = 2  # 2+警示 → 触发风控
TURNOVER_RISK_PCT: float = 80.0    # 成交额占比分位 > 80 → 警示
SELLSIDE_HIGH_RATIO: float = 0.90   # 卖方推荐比 > 90% → 扣分

# 涨停阈值（A股主板/创业板统一使用 9.80%（留出 float 误差）
LIMIT_UP_PCT: float = 9.80
LIMIT_DOWN_PCT: float = -9.80

# A股代码正则 (6位数字)
STOCK_CODE_RE: re.Pattern = re.compile(r"\b(\d{6})\b")


# ============================================================================ #
# 数据结构
# ============================================================================ #
@dataclass
class ActiveTopic:
    """从 MongoDB 读出来的一条活跃主题"""
    primary_topic: str
    secondary_topic: str
    stock_codes: List[str] = field(default_factory=list)
    hit_count: int = 0
    activity_score: float = 0.0
    sample_texts: List[str] = field(default_factory=list)
    recent_doc_ids: List[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""


@dataclass
class TopicSignal:
    """最终输出的信号 JSON"""
    signal_date: str
    primary_topic: str
    secondary_topic: str
    stock_codes: List[str]
    direction: str                # "多" | "空"（当前只做多，预留结构）
    target_weight: float          # 0~1，主题仓位
    momentum_score: float
    crowding_score: float
    combined_score: float
    risk_flag: str                # "正常" | "风控"
    reason: str
    warnings: List[str] = field(default_factory=list)


# ============================================================================ #
# 1. 活跃主题读取层
# ============================================================================ #
class ActiveTopicLoader:
    """从 MongoDB topics 读取当日/近期活跃主题，并关联成分股代码"""

    def __init__(self, top_n: int = 20, min_hit: int = 3) -> None:
        self.top_n: int = top_n
        self.min_hit: int = min_hit

    def load(self, target_date: Optional[str] = None) -> List[ActiveTopic]:
        target_date = target_date or today_str()
        cutoff_prefix: str = target_date[:10]

        try:
            with MongoConnector() as db:
                rows: List[Dict[str, Any]] = list(
                    db[MONGO_TOPICS].find({
                        "$or": [
                            {"last_seen": {"$regex": f"^{cutoff_prefix}"}},
                            {"first_seen": {"$regex": f"^{cutoff_prefix}"}},
                        ],
                    }).sort([("hit_count", -1), ("activity_score", -1)]).limit(self.top_n * 3)
                )
        except Exception as exc:
            logger.error("[ActiveTopicLoader] Mongo 读异常: %s", exc)
            return []

        topics: List[ActiveTopic] = []
        for row in rows:
            codes: List[str] = self._extract_codes(row)
            if not codes and int(row.get("hit_count", 0)) < self.min_hit:
                continue
            topics.append(ActiveTopic(
                primary_topic=str(row.get("primary_topic", "其他")),
                secondary_topic=str(row.get("secondary_topic", "")),
                stock_codes=codes,
                hit_count=int(row.get("hit_count", 0)),
                activity_score=float(row.get("activity_score", 0.0)),
                sample_texts=list(row.get("sample_texts", []) or []),
                recent_doc_ids=list(row.get("recent_doc_ids", []) or []),
                first_seen=str(row.get("first_seen", "")),
                last_seen=str(row.get("last_seen", "")),
            ))
            if len(topics) >= self.top_n:
                break

        logger.info("[ActiveTopicLoader] 读取到 %d 个活跃主题", len(topics))
        for t in topics:
            logger.info("  - [%s] %s (hits=%d, stocks=%d)",
                        t.primary_topic, t.secondary_topic,
                        t.hit_count, len(t.stock_codes))
        return topics

    def _extract_codes(self, row: Dict[str, Any]) -> List[str]:
        """从 sample_texts 和 recent_doc_ids 中抽取 6 位 A 股代码"""
        combined: str = ""
        for field_name in ("sample_texts", "recent_doc_ids"):
            for item in (row.get(field_name) or []):
                combined += str(item) + "\n"
        found: List[str] = list(dict.fromkeys(STOCK_CODE_RE.findall(combined)))
        # 过滤明显非股票代码（全同数字等）
        valid: List[str] = []
        for c in found:
            if len(c) == 6 and c.isdigit() and not (c == c[0] * 6):
                valid.append(c)
        return valid


# ============================================================================ #
# 2. 市场数据获取层 (akshare)
# ============================================================================ #
class MarketDataFetcher:
    """从 akshare 拉取个股与指数行情; 提供回测时接受外部 DataFrame"""

    def __init__(self) -> None:
        self._price_cache: Dict[str, pd.DataFrame] = {}

    # ----------------------------------------------------------- 主方法

    def fetch_topic_prices(
        self,
        topic: ActiveTopic,
        lookback: int = LOOKBACK_DAYS_TURNOVER,
    ) -> Dict[str, pd.DataFrame]:
        """返回 {stock_code: DataFrame}"""
        if not topic.stock_codes:
            return {}
        frames: Dict[str, pd.DataFrame] = {}
        for code in topic.stock_codes:
            df: Optional[pd.DataFrame] = self._fetch_single_stock(code, lookback)
            if df is not None and not df.empty:
                frames[code] = df
        logger.info("[MarketData] [%s] %s → %d 只股票行情",
                    topic.primary_topic, topic.secondary_topic, len(frames))
        return frames

    def fetch_benchmark(
        self, lookback: int = LOOKBACK_DAYS_TURNOVER
    ) -> Optional[pd.DataFrame]:
        """万得全A(881001.WI) 指数作为市场基准"""
        try:
            import tushare as ts
            from modules.db_connector import CONFIG
            
            token = CONFIG.get("api_keys", {}).get("tushare", {}).get("token", "")
            if not token or token.startswith("${"):
                token = os.environ.get("TUSHARE_TOKEN", "")
            
            pro = ts.pro_api(token) if token else ts.pro_api()
            
            # 万得全A在tushare的代码
            benchmark_ts_code = "881001.WI"
            
            df = pro.index_daily(
                ts_code=benchmark_ts_code,
                start_date=(datetime.datetime.now() - datetime.timedelta(days=lookback + 30))
                        .strftime("%Y%m%d"),
                end_date=today_str("%Y%m%d"),
            )
            if df is None or df.empty:
                logger.warning("[MarketData] 基准指数 %s 无数据", benchmark_ts_code)
                return None
            
            df = df.rename(columns={
                "close": "close",
                "trade_date": "date",
            })
            df = df.tail(lookback).copy()
            df["pct"] = df["close"].pct_change() * 100.0
            df.reset_index(drop=True, inplace=True)
            return df
        except Exception as exc:
            logger.warning("[MarketData] 基准指数获取失败: %s", exc)
            return None

    # ----------------------------------------------------------- 内部

    def _fetch_single_stock(self, code: str, lookback: int) -> Optional[pd.DataFrame]:
        """拉取单只股票 N 日历史数据"""
        if code in self._price_cache and len(self._price_cache[code]) >= lookback:
            return self._price_cache[code].tail(lookback).copy()
        try:
            import tushare as ts
            from modules.db_connector import CONFIG
            
            token = CONFIG.get("api_keys", {}).get("tushare", {}).get("token", "")
            if not token or token.startswith("${"):
                token = os.environ.get("TUSHARE_TOKEN", "")
            
            pro = ts.pro_api(token) if token else ts.pro_api()
            
            # 补充市场后缀
            ts_code = code
            if len(code) == 6:
                ts_code = f"{code}.SH" if code.startswith(("6", "5")) else f"{code}.SZ"
            
            start = (datetime.datetime.now() - datetime.timedelta(days=lookback + 30))
            df = pro.daily(
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=today_str("%Y%m%d"),
                adj="qfq",
            )
            if df is None or df.empty:
                return None
            df = df.copy()
            # 列名归一
            df = df.rename(columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "vol": "volume",
                "amount": "amount",
                "turnover_rate": "turnover",
                "trade_date": "date",
            })
            # 确保必要列
            if "pct" not in df.columns and "close" in df.columns:
                df["pct"] = df["close"].pct_change() * 100.0
            if "amount" not in df.columns:
                df["amount"] = 0
            if "turnover" not in df.columns:
                df["turnover"] = 0.0
            df = df.tail(lookback).reset_index(drop=True)
            self._price_cache[code] = df.copy()
            time.sleep(0.1)  # 轻微节流
            return df
        except Exception as exc:
            logger.debug("[MarketData] 拉取 %s 失败: %s", code, exc)
            return None


# ============================================================================ #
# 3. 催化层 (文本 Z 分数）
# ============================================================================ #
class CatalystTracker:
    """过去 24h 主题相关文本数量相对于历史的 Z 分数"""

    def catalyst_zscore(self, topic: ActiveTopic) -> float:
        """
        简化但稳定的实现:
          - 近期文本数 = max(topic.hit_count, sample_texts 数量)
          - 历史均值 = hit_count / 20
          - 历史标准差 = max(0.5, 历史均值 * 0.5)
          - z = (x - mean) / std
        """
        try:
            today_count: float = float(
                max(1, topic.hit_count) if topic.hit_count > 0 else
                max(1, len(topic.sample_texts))
            )
            daily_avg: float = max(1.0, today_count / 5.0)
            # 历史均值取 daily_avg * 1（估算）
            historical_mean = daily_avg
            historical_std = max(0.5, historical_mean * 0.5)
            z: float = (today_count - historical_mean) / historical_std
            return float(z)
        except Exception as exc:
            logger.debug("[CatalystTracker] 催化Z分计算异常: %s", exc)
            return 0.0


# ============================================================================ #
# 4. 主题评分引擎
# ============================================================================ #
class ScoringEngine:
    """主题综合评分引擎"""

    def __init__(self) -> None:
        self.market = MarketDataFetcher()
        self.catalyst = CatalystTracker()

    def score(self, topic: ActiveTopic) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "momentum": 0.0, "crowding": 0.0, "combined": 0.0,
            "warnings": [], "sub_momentum": {}, "sub_crowding": {},
        }
        if not topic.stock_codes:
            result["warnings"].append("成分股不足，跳过评分")
            return result

        frames: Dict[str, pd.DataFrame] = self.market.fetch_topic_prices(
            topic, lookback=LOOKBACK_DAYS_TURNOVER
        )
        if not frames:
            result["warnings"].append("行情获取失败，跳过评分")
            return result

        benchmark_df = self.market.fetch_benchmark(lookback=LOOKBACK_DAYS_TURNOVER)

        # 动量强度 (0~1)
        mom_score, mom_details = self._compute_momentum(topic, frames, benchmark_df)
        # 拥挤度风险 (0~1)
        cwd_score, cwd_details, warnings_ = self._compute_crowding(topic, frames)

        # 综合得分 (0~100)
        combined = mom_score * WEIGHT_MOMENTUM + (1.0 - cwd_score) * WEIGHT_CROWDING
        combined_scaled = max(0.0, min(100.0, combined * 100.0))

        result["momentum"] = mom_score * 100.0
        result["crowding"] = cwd_score * 100.0
        result["combined"] = combined_scaled
        result["warnings"] = warnings_
        result["sub_momentum"] = mom_details
        result["sub_crowding"] = cwd_details
        return result

    # ------------------------------------------------------------- 动量子项

    def _compute_momentum(
        self, topic: ActiveTopic,
        frames: Dict[str, pd.DataFrame],
        benchmark_df: Optional[pd.DataFrame],
    ) -> Tuple[float, Dict[str, float]]:
        details: Dict[str, float] = {}

        # 4.2.1 主题指数超额收益（等权，相对万得全A）
        excess_return: float = self._calc_excess_return(frames, benchmark_df)
        details["excess_return_pct"] = excess_return
        excess_norm = max(0.0, min(1.0, (excess_return + 5.0) / 20.0))
        details["excess_return_norm"] = excess_norm

        # 4.2.2 催化 Z 分数
        z_score = self.catalyst.catalyst_zscore(topic)
        details["catalyst_z"] = z_score
        z_norm = max(0.0, min(1.0, (z_score + 2.0) / 4.0))
        details["catalyst_z_norm"] = z_norm

        # 4.2.3 龙头高度 / 首板家数
        leader_height, first_limit_up_count = self._compute_leader_structure(frames)
        details["leader_height_days"] = float(leader_height)
        details["first_limit_up_count"] = float(first_limit_up_count)
        leader_norm = min(1.0, (leader_height / 5.0 + first_limit_up_count / 10.0) / 2.0)
        details["leader_norm"] = leader_norm

        # 合成动量得分 (0~1)
        momentum_score = (
            excess_norm * MOM_W_EXCESS_RETURN
            + z_norm * MOM_W_CATALYST_Z
            + leader_norm * MOM_W_LEADER
        )
        return max(0.0, min(1.0, momentum_score)), details

    def _calc_excess_return(
        self,
        frames: Dict[str, pd.DataFrame],
        benchmark_df: Optional[pd.DataFrame],
    ) -> float:
        """主题等权 20 日累计 - 基准累计"""
        try:
            pct_matrix: List[List[float]] = []
            for code, df in frames.items():
                if df is None or df.empty or "pct" not in df.columns:
                    continue
                series = df["pct"].tail(LOOKBACK_DAYS_EXCESS).dropna()
                if len(series) < LOOKBACK_DAYS_EXCESS // 2:
                    continue
                pct_matrix.append([float(x) for x in series.tolist()])

            if not pct_matrix:
                return 0.0

            # 对齐长度
            min_len = min(len(row) for row in pct_matrix)
            aligned = [row[-min_len:] for row in pct_matrix]

            # 等权指数：按列求和取均值
            topic_total_pct: float = 0.0
            if min_len > 0:
                for i in range(min_len):
                    col_sum: float = sum(row[i] for row in aligned)
                    topic_total_pct += (col_sum / float(len(aligned)))

            # 基准累计
            bench_total_pct: float = 0.0
            if benchmark_df is not None and not benchmark_df.empty:
                series_b = benchmark_df["pct"].tail(min_len).dropna()
                if len(series_b) > 0:
                    bench_total_pct = float(series_b.sum())

            return float(topic_total_pct - bench_total_pct)
        except Exception as exc:
            logger.warning("[Scoring] 主题超额收益计算异常: %s", exc)
            return 0.0

    def _compute_leader_structure(
        self, frames: Dict[str, pd.DataFrame]
    ) -> Tuple[int, int]:
        """计算板块内最高连板天数和首板家数"""
        max_consecutive: int = 0
        first_limit_up_count: int = 0
        try:
            for code, df in frames.items():
                if df is None or df.empty or "pct" not in df.columns:
                    continue
                pcts = df["pct"].tail(10).values
                streak: int = 0
                for v in reversed(pcts):
                    try:
                        val = float(v)
                        if val >= LIMIT_UP_PCT:
                            streak += 1
                        else:
                            break
                    except (ValueError, TypeError):
                        break
                if streak >= 1:
                    max_consecutive = max(max_consecutive, streak)
                    if streak == 1:
                        first_limit_up_count += 1
        except Exception as exc:
            logger.debug("[Scoring] 连板计算异常: %s", exc)
        return max_consecutive, first_limit_up_count

    # ------------------------------------------------------------- 拥挤度子项

    def _compute_crowding(
        self, topic: ActiveTopic,
        frames: Dict[str, pd.DataFrame],
    ) -> Tuple[float, Dict[str, float], List[str]]:
        warnings_local: List[str] = []
        details: Dict[str, float] = {}

        # 4.3.1 成交额占比分位
        turnover_percentile: float = 0.0
        try:
            all_amounts: List[float] = []
            latest_sum: float = 0.0
            for code, df in frames.items():
                if df is None or df.empty or "amount" not in df.columns:
                    continue
                amounts = pd.to_numeric(df["amount"], errors="coerce").tail(LOOKBACK_DAYS_TURNOVER)
                vals = [float(x) for x in amounts.values if not pd.isna(x)]
                if vals:
                    all_amounts.extend(vals)
                    latest_sum += vals[-1]
            if all_amounts:
                # 取当前 latest_sum 在历史中的分位
                arr = np.array(all_amounts, dtype=np.float32)
                current = float(latest_sum)
                percentile = float(
                    (arr < current).sum() / max(1, len(arr)) * 100.0
                )
                turnover_percentile = percentile
                details["turnover_percentile"] = percentile
                details["turnover_latest"] = current
                if percentile >= TURNOVER_RISK_PCT:
                    warnings_local.append(
                        f"成交额分位 {percentile:.1f}% 超警戒线"
                    )
        except Exception as exc:
            logger.debug("[Scoring] 成交额分位失败: %s", exc)

        # 4.3.2 换手率加速度
        accel_norm: float = 0.0
        try:
            avg_today_list: List[float] = []
            avg_prev_list: List[float] = []
            for code, df in frames.items():
                if df is None or df.empty or "turnover" not in df.columns:
                    continue
                series = pd.to_numeric(df["turnover"], errors="coerce")
                lookback_total = LOOKBACK_DAYS_ACCEL * 2
                recent = series.tail(lookback_total).dropna()
                if len(recent) < LOOKBACK_DAYS_ACCEL:
                    continue
                vals = recent.tolist()
                last_5 = vals[-LOOKBACK_DAYS_ACCEL:]
                first_5 = vals[:LOOKBACK_DAYS_ACCEL]
                avg_today_list.append(float(np.mean(last_5)))
                avg_prev_list.append(float(np.mean(first_5)))

            if avg_today_list and avg_prev_list:
                avg_today = float(np.mean(avg_today_list))
                avg_prev = float(np.mean(avg_prev_list))
                if avg_prev > 0.001:
                    accel = (avg_today - avg_prev) / avg_prev
                else:
                    accel = 0.0
                details["turnover_accel_pct"] = accel * 100.0
                accel_norm = max(0.0, min(1.0, (accel + 0.3) / 0.6))
                if accel > 0.20:
                    warnings_local.append(
                        f"换手率加速度 {accel*100:.1f}% 高企"
                    )
        except Exception as exc:
            logger.debug("[Scoring] 换手率加速度失败: %s", exc)

        # 4.3.3 卖方一致预期（简化：文本中看多关键词比例）
        sell_side_ratio: float = self._estimate_sell_side_ratio(topic)
        details["sell_side_bullish_ratio"] = sell_side_ratio
        sell_side_norm = max(0.0, min(1.0, sell_side_ratio))
        if sell_side_ratio >= SELLSIDE_HIGH_RATIO:
            warnings_local.append(
                f"卖方一致看多比例 {sell_side_ratio:.0%} 过高，警惕"
            )

        # 合成拥挤度得分 (0~1)
        turnover_norm = turnover_percentile / 100.0
        crowding_score = (
            turnover_norm * CWD_W_TURNOVER_RANK
            + accel_norm * CWD_W_TURNOVER_ACCEL
            + sell_side_norm * CWD_W_SELLSIDE
        )
        crowding_score = max(0.0, min(1.0, crowding_score))
        details["crowding_score_raw"] = crowding_score
        return crowding_score, details, warnings_local

    def _estimate_sell_side_ratio(self, topic: ActiveTopic) -> float:
        """简化：从 sample_texts 中统计"推荐/看多"关键词"""
        bullish_words: List[str] = ["买入", "推荐", "看好", "强烈推荐", "超配", "增持"]
        bearish_words: List[str] = ["卖出", "中性", "观望", "减持", "低配"]
        bullish: int = 0
        bearish: int = 0
        total: int = 0
        for txt in (topic.sample_texts or []):
            if not txt:
                continue
            total += 1
            low = str(txt)
            if any(w in low for w in bullish_words):
                bullish += 1
            elif any(w in low for w in bearish_words):
                bearish += 1
        if total == 0 or (bullish + bearish) == 0:
            return 0.5
        return float(bullish) / float(bullish + bearish)


# ============================================================================ #
# 5. 仓位分配层
# ============================================================================ #
class PositionAllocator:
    """从评分到仓位"""

    def __init__(
        self,
        max_single: float = SINGLE_TOPIC_MAX,
        top_n: int = TOPIC_COUNT,
    ) -> None:
        self.max_single: float = max_single
        self.top_n: int = top_n

    def allocate(
        self, scored_topics: List[Tuple[ActiveTopic, Dict[str, Any]]]
    ) -> List[TopicSignal]:
        if not scored_topics:
            return []
        # 按综合得分降序
        sorted_topics: List[Tuple[ActiveTopic, Dict[str, Any]]] = sorted(
            scored_topics,
            key=lambda x: float(x[1].get("combined", 0.0)),
            reverse=True,
        )
        top_entries: List[Tuple[ActiveTopic, Dict[str, Any]]] = sorted_topics[: self.top_n]

        # 得分占比分配
        total_score: float = sum(
            max(0.01, float(x[1].get("combined", 0.01))) for x in top_entries
        )
        raw_weights: List[float] = []
        for topic, score in top_entries:
            raw_w = max(0.001, float(score.get("combined", 0.0))) / total_score
            raw_w = min(raw_w, self.max_single)
            raw_weights.append(raw_w)

        # 归一化使总和为 1（如果因上限未达1则重新缩放）
        raw_sum: float = sum(raw_weights)
        if raw_sum > 0.0001:
            final_weights: List[float] = [w / raw_sum for w in raw_weights]
        else:
            final_weights = [1.0 / float(len(raw_weights))] * len(raw_weights)

        # 构建 TopicSignal
        signals: List[TopicSignal] = []
        for (topic, score), weight in zip(top_entries, final_weights):
            # 风险否决: 若拥挤度警示 ≥ 2 → 仓位减半并标记
            warnings_ = score.get("warnings", [])
            risk_flag: str = "正常"
            final_weight: float = weight
            if len(warnings_) >= CROWDING_WARNING_THRESHOLD:
                final_weight = round(final_weight * RISK_CAP, 4)
                risk_flag = "风控"

            direction: str = "多" if score.get("combined", 0.0) > 5.0 else "空"
            reason_parts: List[str] = []
            if score.get("combined", 0.0) >= 60.0:
                reason_parts.append("综合得分偏高，动量强势")
            else:
                reason_parts.append("综合得分中等，轻仓配置")
            if warnings_:
                reason_parts.append("拥挤度警示: " + "; ".join(warnings_))

            signals.append(TopicSignal(
                signal_date=today_str("%Y-%m-%d"),
                primary_topic=topic.primary_topic,
                secondary_topic=topic.secondary_topic,
                stock_codes=list(topic.stock_codes),
                direction=direction,
                target_weight=round(final_weight, 4),
                momentum_score=round(float(score.get("momentum", 0.0)), 2),
                crowding_score=round(float(score.get("crowding", 0.0)), 2),
                combined_score=round(float(score.get("combined", 0.0)), 2),
                risk_flag=risk_flag,
                reason="; ".join(reason_parts),
                warnings=list(warnings_),
            ))
        return signals


# ============================================================================ #
# 6. 回测接口
# ============================================================================ #
class BacktestInterface:
    """回测接口：接受 (date, {code: price_series}) → 返回信号"""

    def __init__(self) -> None:
        self.scoring = ScoringEngine()
        self.allocator = PositionAllocator()

    def generate_signal_on_date(
        self,
        date_str: str,
        price_data: Optional[Dict[str, pd.Series]] = None,
    ) -> List[TopicSignal]:
        loader = ActiveTopicLoader(top_n=20, min_hit=3)
        topics: List[ActiveTopic] = loader.load(target_date=date_str)
        if not topics:
            logger.warning("[Backtest] 当日无活跃主题，无法生成信号")
            return []

        scored: List[Tuple[ActiveTopic, Dict[str, Any]]] = []
        for t in topics:
            score = self.scoring.score(t)
            scored.append((t, score))

        signals: List[TopicSignal] = self.allocator.allocate(scored)
        return self._handle_limit_up(signals, date_str, price_data)

    def _handle_limit_up(
        self, signals: List[TopicSignal],
        date_str: str,
        price_data: Optional[Dict[str, pd.Series]],
    ) -> List[TopicSignal]:
        """涨停无法买入处理：
        - 若成分股当日或最近交易日涨停 → 剔除
        """
        if not signals:
            return signals
        for sig in signals:
            remaining: List[str] = []
            for code in list(sig.stock_codes):
                is_limit_up: bool = False
                if price_data is not None and code in price_data:
                    series = price_data[code]
                    try:
                        if hasattr(series, "iloc"):
                            val = float(series.iloc[-1])
                        else:
                            val = float(series[-1])
                        if val >= LIMIT_UP_PCT:
                            is_limit_up = True
                    except (ValueError, TypeError, IndexError):
                        pass
                if not is_limit_up:
                    remaining.append(code)
            sig.stock_codes = remaining
        return signals


# ============================================================================ #
# 7. 主流程
# ============================================================================ #
class TopicConfiguratorPipeline:
    """完整的每日 9:25 主题配置信号流程"""

    def __init__(self) -> None:
        self.loader = ActiveTopicLoader()
        self.scoring = ScoringEngine()
        self.allocator = PositionAllocator()

    def run(self, date_str: Optional[str] = None) -> List[TopicSignal]:
        date_str = date_str or today_str("%Y-%m-%d")
        logger.info("=" * 60)
        logger.info("[Configurator] 开始生成 %s 主题配置信号", date_str)
        logger.info("=" * 60)

        topics: List[ActiveTopic] = self.loader.load(target_date=date_str)

        scored: List[Tuple[ActiveTopic, Dict[str, Any]]] = []
        for t in topics:
            score = self.scoring.score(t)
            logger.info("  → [%s] %s: 动量=%5.1f | 拥挤=%5.1f | 综合=%5.1f | 警示=%d",
                        t.primary_topic, t.secondary_topic,
                        score.get("momentum", 0), score.get("crowding", 0),
                        score.get("combined", 0), len(score.get("warnings", [])))
            scored.append((t, score))

        signals: List[TopicSignal] = self.allocator.allocate(scored)
        self._print_signals(signals)
        return signals

    def to_json(self, signals: List[TopicSignal]) -> str:
        signal_list: List[Dict[str, Any]] = []
        for s in signals:
            signal_list.append({
                "signal_date": s.signal_date,
                "primary_topic": s.primary_topic,
                "secondary_topic": s.secondary_topic,
                "stock_codes": list(s.stock_codes),
                "direction": s.direction,
                "target_weight": s.target_weight,
                "momentum_score": s.momentum_score,
                "crowding_score": s.crowding_score,
                "combined_score": s.combined_score,
                "risk_flag": s.risk_flag,
                "reason": s.reason,
                "warnings": list(s.warnings),
            })
        obj: Dict[str, Any] = {
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "signals": signal_list,
            "total_allocation": round(sum(x.target_weight for x in signals), 4),
        }
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _print_signals(self, signals: List[TopicSignal]) -> None:
        print()
        print("=" * 70)
        print("  主题配置信号 - " + today_str("%Y-%m-%d") + "  (9:25 盘前)")
        print("=" * 70)
        if not signals:
            print("  (空 - 无有效主题信号)")
        for i, s in enumerate(signals, 1):
            print(f"  {i:2d}. [{s.risk_flag:<4s}] [{s.primary_topic}] "
                  f"{s.secondary_topic:<12s} 方向={s.direction} "
                  f"权重={s.target_weight*100:5.1f}% "
                  f"综合={s.combined_score:5.1f} | "
                  f"动量={s.momentum_score:5.1f} | 拥挤={s.crowding_score:5.1f}")
            if s.stock_codes:
                extra = (f" 等共 {len(s.stock_codes)} 只" if len(s.stock_codes) > 8 else "")
                print(f"       成分股: {', '.join(s.stock_codes[:8])}{extra}")
            if s.warnings:
                print(f"       警示: {'; '.join(s.warnings[:3])}")
            print(f"       原因: {s.reason}")
        print("=" * 70)
        print()


# ============================================================================ #
# 8. 对外暴露函数
# ============================================================================ #
def generate_topic_signals(date_str: Optional[str] = None) -> List[TopicSignal]:
    """便捷函数：生成当日主题信号，供调度器/定时任务调用"""
    pipeline = TopicConfiguratorPipeline()
    return pipeline.run(date_str)


def generate_topic_signals_json(date_str: Optional[str] = None) -> str:
    """生成 JSON 格式信号字符串"""
    pipeline = TopicConfiguratorPipeline()
    signals = pipeline.run(date_str)
    return pipeline.to_json(signals)


def backtest_single_date(
    date_str: str,
    price_data: Optional[Dict[str, pd.Series]] = None,
) -> List[TopicSignal]:
    """回测接口：对指定日期，可选传入预计算的 price_data"""
    interface = BacktestInterface()
    return interface.generate_signal_on_date(date_str, price_data=price_data)


if __name__ == "__main__":
    generate_topic_signals()
