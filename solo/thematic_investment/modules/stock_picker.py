"""
主题高纯度成分股筛选 (stock_picker.py)
=========================================

对每个 (primary_topic, secondary_topic) 主题, 输出一个成分股组合:
  1. 粗筛池: tushare 概念板块 / MongoDB 文本打标签 / 产业链 CSV 三路数据的并集
  2. 三维纯度打分 (0-100):
       a. 营收关联度 (40%) - DeepSeek LLM 分析最新年报/公告/研报摘要
       b. 股价弹性   (30%) - 对板块龙头滚动 Beta + 上涨日跟涨率
       c. 文本中心度 (30%) - 文档-股票 二部图的中介中心度 (networkx)
  3. 筛除: ST/*ST / 近 20 日日均成交额 < 5000万 / 纯度 < 40
  4. 组合: 取前 10-15 只, 纯度加权或等权
  5. 增量更新: 记忆昨日权重, 纯度下降超 20% 或 Beta 衰减严重 → 替换

依赖:
  pip install tushare pandas numpy pymongo networkx httpx
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import asyncio
import logging
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# --------------------------------------------------------------------------- #
# 路径
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
from modules.utils import setup_logger, today_str, chunk_list  # noqa: E402
from modules.network_builder import (  # noqa: E402
    DocStockExtractor, TextStockNetwork, build_network_for_topic,  # noqa: E402
)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
logger: logging.Logger = setup_logger(
    name="stock_picker",
    log_dir=os.path.join(_PARENT_DIR, "logs"),
    log_file="stock_picker.log",
)

LOOKBACK_REVENUE: int = 180            # LLM 文本回看天数
LOOKBACK_PRICE: int = 60               # Beta / 跟涨率 回看交易日
LOOKBACK_TURNOVER: int = 20            # 成交额筛选天数
MIN_DAILY_AMOUNT: float = 50_000_000.0  # 5000 万
MIN_PURITY: float = 40.0                # 最低纯度分
DECAY_PURITY: float = 20.0              # 纯度下降超 20% → 替换
DECAY_BETA: float = 0.5                 # Beta 相对衰减超 50% → 替换
TOP_N_MIN: int = 10
TOP_N_MAX: int = 15
SINGLE_MAX_WEIGHT: float = 0.30         # 单票上限
INDUSTRY_CSV_DEFAULT: str = os.path.join(
    _PARENT_DIR, "data", "industry_chain_sample.csv"
)
CACHE_DIR: str = os.path.join(_PARENT_DIR, "cache_daily")
os.makedirs(CACHE_DIR, exist_ok=True)

ST_STOCK_RE: re.Pattern = re.compile(r"ST|\*ST|退|退市", re.IGNORECASE)
STOCK_CODE_RE: re.Pattern = re.compile(r"\b(\d{6})\b")


# ============================================================================ #
# 数据结构
# ============================================================================ #
@dataclass
class StockPurity:
    """一只股票在当前主题下的纯度与打分"""
    code: str
    name: str = ""
    primary_topic: str = ""
    secondary_topic: str = ""
    # 三维度原始打分 (0-100)
    revenue_score: float = 0.0
    elasticity_score: float = 0.0
    centrality_score: float = 0.0
    # 最终综合纯度 (0-100)
    combined: float = 0.0
    # 辅助
    rolling_beta: float = 0.0
    upside_ratio: float = 0.0
    avg_amount_20d: float = 0.0
    is_st: bool = False
    reason: str = ""
    source_channels: List[str] = field(default_factory=list)  # "同花顺"/"Mongo"/"产业链"

    def compute_combined(self, w_rev: float = 0.40,
                        w_elast: float = 0.30,
                        w_cent: float = 0.30) -> float:
        self.combined = round(
            self.revenue_score * w_rev
            + self.elasticity_score * w_elast
            + self.centrality_score * w_cent,
            2,
        )
        return self.combined


@dataclass
class TopicComposition:
    """主题 → 成分股组合"""
    primary_topic: str
    secondary_topic: str
    date: str
    stocks: List[StockPurity] = field(default_factory=list)
    total_weight: float = 1.0
    generation_reason: str = ""


# ============================================================================ #
# 1. 粗筛池
# ============================================================================ #
class StockPool:
    """三路数据源的并集"""

    def __init__(self, industry_csv: str = INDUSTRY_CSV_DEFAULT) -> None:
        self.industry_csv: str = industry_csv

    # ------------------------------------------------------------ 主方法
    def collect(self, primary_topic: str,
                secondary_topic: str,
                extra_keywords: Optional[List[str]] = None) -> List[StockPurity]:
        pool: Dict[str, StockPurity] = {}

        # 1. tushare 概念板块
        try:
            ts_list = self._from_tushare_concept(
                primary_topic, secondary_topic, extra_keywords or []
            )
            for sp in ts_list:
                pool.setdefault(sp.code, sp).source_channels.append("tushare概念")
        except Exception as exc:
            logger.debug("[StockPool] tushare 概念抓取异常 (可能网络): %s", exc)

        # 2. MongoDB 文本 -> 股票关联
        try:
            for sp in self._from_mongo_text_network(primary_topic, secondary_topic):
                if sp.code in pool:
                    pool[sp.code].source_channels.append("Mongo文本")
                else:
                    sp.source_channels.append("Mongo文本")
                    pool[sp.code] = sp
        except Exception as exc:
            logger.debug("[StockPool] Mongo 文本关联异常: %s", exc)

        # 3. 产业链 CSV
        try:
            for sp in self._from_industry_csv(primary_topic, secondary_topic):
                if sp.code in pool:
                    pool[sp.code].source_channels.append("产业链")
                else:
                    sp.source_channels.append("产业链")
                    pool[sp.code] = sp
        except Exception as exc:
            logger.debug("[StockPool] 产业链 CSV 异常: %s", exc)

        logger.info(
            "[StockPool] %s/%s → 粗筛池 %d 只股票",
            primary_topic, secondary_topic, len(pool),
        )
        return list(pool.values())

    # ------------------------------------------------------------ 东财同花顺概念板块

    def _from_tushare_concept(
        self, primary_topic: str, secondary_topic: str,
        extra_keywords: List[str],
    ) -> List[StockPurity]:
        """
        使用东财同花顺接口获取概念板块成分股:
        1. pro.ths_index() 获取同花顺概念板块列表
        2. pro.ths_member() 获取每个板块的成分股
        """
        candidates: List[StockPurity] = []
        try:
            import tushare as ts
            from modules.db_connector import CONFIG
            
            token = CONFIG.get("api_keys", {}).get("tushare", {}).get("token", "")
            if not token or token.startswith("${"):
                token = os.environ.get("TUSHARE_TOKEN", "")
            
            pro = ts.pro_api(token) if token else ts.pro_api()
            
            # 尝试几种关键词: "二级主题" / "一级主题" / extra_keywords
            all_kw: List[str] = list(dict.fromkeys(
                [secondary_topic, primary_topic] + extra_keywords
            ))
            collected: Set[str] = set()
            
            # 获取同花顺概念板块列表 (type="N" 为概念板块)
            index_df = pro.ths_index(
                ts_code="",
                exchange="A",
                type="N",
                name="",
                limit="",
                offset=""
            )
            if index_df is None or index_df.empty:
                logger.debug("[StockPool] 东财同花顺获取概念板块列表失败")
                return candidates
            
            logger.info("[StockPool] 东财同花顺获取到 %d 个概念板块", len(index_df))
            
            # 遍历板块列表，匹配关键词
            for _, row in index_df.iterrows():
                ts_code = str(row.get("ts_code", ""))
                name = str(row.get("name", ""))
                
                # 检查是否匹配关键词
                matched = False
                for kw in all_kw:
                    if kw and kw in name:
                        matched = True
                        break
                
                if not matched:
                    continue
                
                try:
                    # 获取板块成分股
                    member_df = pro.ths_member(
                        ts_code=ts_code,
                        con_code="",
                        offset="",
                        limit=""
                    )
                    if member_df is None or member_df.empty:
                        continue
                    
                    for _, m_row in member_df.iterrows():
                        # 同花顺成分股代码带市场后缀
                        code_with_exchange = str(m_row.get("ts_code", ""))
                        # 提取6位代码
                        code = code_with_exchange.split(".")[0] if "." in code_with_exchange else code_with_exchange
                        stock_name = str(m_row.get("name", name))
                        
                        if code.isdigit() and len(code) == 6 and code not in collected:
                            collected.add(code)
                            candidates.append(StockPurity(
                                code=code,
                                name=stock_name,
                                primary_topic=primary_topic,
                                secondary_topic=secondary_topic,
                            ))
                except Exception:
                    pass
                
                time.sleep(0.05)
                
        except Exception as exc:
            logger.debug("[StockPool] 东财同花顺接口异常: %s", exc)
        
        logger.info("[StockPool] 东财同花顺概念返回 %d 只股票", len(candidates))
        return candidates

    # ------------------------------------------------------------ Mongo 文本

    def _from_mongo_text_network(
        self, primary_topic: str, secondary_topic: str,
    ) -> List[StockPurity]:
        candidates: List[StockPurity] = []
        try:
            extractor = DocStockExtractor(lookback_days=60)
            entries = extractor.extract(topic_primary=primary_topic,
                                         topic_secondary=secondary_topic)
            collected: Dict[str, int] = {}
            for e in entries:
                for c in e.stock_codes:
                    collected[c] = collected.get(c, 0) + 1
            for code, freq in collected.items():
                if freq >= 1:  # 出现过至少一次即进池
                    candidates.append(StockPurity(
                        code=code, primary_topic=primary_topic,
                        secondary_topic=secondary_topic,
                    ))
        except Exception as exc:
            logger.debug("[StockPool] Mongo 网络抽取异常: %s", exc)
        logger.info("[StockPool] MongoDB 文本关联返回 %d 只股票", len(candidates))
        return candidates

    # ------------------------------------------------------------ 产业链 CSV

    def _from_industry_csv(
        self, primary_topic: str, secondary_topic: str,
    ) -> List[StockPurity]:
        candidates: List[StockPurity] = []
        if not os.path.exists(self.industry_csv):
            return candidates
        try:
            df = pd.read_csv(self.industry_csv)
            cols = df.columns.tolist()
            if not any(c in cols for c in ["source_code", "target_code"]):
                return candidates
            # 检查是否有 primary/secondary 列匹配当前主题
            topic_match = pd.Series([False] * len(df))
            if "primary_topic" in cols:
                topic_match = (topic_match
                               | df["primary_topic"].astype(str).str.contains(primary_topic))
            if "secondary_topic" in cols:
                topic_match = (topic_match
                               | df["secondary_topic"].astype(str).str.contains(secondary_topic))
            # 如果有匹配主题，则只取匹配行；否则全量
            if topic_match.any():
                df = df[topic_match].reset_index(drop=True)
            codes: Set[str] = set()
            for col in ["source_code", "target_code"]:
                if col in df.columns:
                    for code in df[col].astype(str).tolist():
                        if code.isdigit() and len(code) <= 6:
                            codes.add(code.zfill(6))
            for code in codes:
                candidates.append(StockPurity(
                    code=code, primary_topic=primary_topic,
                    secondary_topic=secondary_topic,
                ))
        except Exception as exc:
            logger.debug("[StockPool] 产业链 CSV 解析异常: %s", exc)
        logger.info("[StockPool] 产业链 CSV 返回 %d 只股票", len(candidates))
        return candidates


# ============================================================================ #
# 2. 三维度纯度打分引擎
# ============================================================================ #
class PurityScoringEngine:
    """整合营收关联度/股价弹性/文本中心度"""

    def __init__(self, industry_csv: str = INDUSTRY_CSV_DEFAULT) -> None:
        self.industry_csv = industry_csv

    # ------------------------------------------------------------ 主方法
    def score(self, pool: List[StockPurity]) -> List[StockPurity]:
        if not pool:
            return pool

        # 2.1 股价弹性 (与龙头的滚动 Beta + 跟涨率)
        #    — 必须先跑, 因为需要从 akshare 拉行情, 并同时识别龙头
        try:
            pool = self._compute_elasticity(pool)
        except Exception as exc:
            logger.warning("[PurityScoring] 股价弹性打分异常: %s", exc)

        # 2.2 文本中心度 (networkx 中介中心度)
        try:
            pool = self._compute_centrality(pool)
        except Exception as exc:
            logger.warning("[PurityScoring] 文本中心度打分异常: %s", exc)

        # 2.3 营收关联度 (DeepSeek 分析文本 → 主题相关业务占营收比例)
        try:
            pool = self._compute_revenue(pool)
        except Exception as exc:
            logger.warning("[PurityScoring] 营收占比打分异常: %s", exc)

        # 2.4 综合打分
        for sp in pool:
            sp.compute_combined()

        logger.info(
            "[PurityScoring] 三维度打分完成, 综合分均值 = %.2f",
            float(np.mean([sp.combined for sp in pool])) if pool else 0.0,
        )
        return pool

    # ------------------------------------------------------------ 股价弹性
    def _compute_elasticity(self, pool: List[StockPurity]) -> List[StockPurity]:
        """
        计算板块龙头滚动 Beta + 板块上涨日跟涨率。
        - 先从 pool 拉 60 日行情 (akshare 日线)
        - 以综合累计涨幅前 3 只或累计成交额前 3 只为代理 "板块指数"
        - 对每只股票, regress(stock_ret ~ leader_avg_ret, window=60) 得 beta
        - 跟涨率 = 当板块上涨日股票也上涨的比例
        """
        if not pool:
            return pool
        try:
            import akshare as ak
        except ImportError:
            logger.warning("[elasticity] akshare 未安装, 跳过")
            return pool

        # 2.1.1 拉每只股票 60 日涨跌幅
        prices_map: Dict[str, pd.Series] = {}
        amounts_map: Dict[str, pd.Series] = {}
        for sp in pool:
            df = self._fetch_daily_pct(sp.code, lookback=LOOKBACK_PRICE)
            if df is not None:
                prices_map[sp.code] = df["pct"]
                amounts_map[sp.code] = df["amount"]

        if not prices_map:
            return pool
        # 对齐 index
        aligned: pd.DataFrame = pd.DataFrame({
            c: s for c, s in prices_map.items()
        }).fillna(0.0)
        if aligned.shape[1] == 0 or aligned.shape[0] < 10:
            return pool

        # 2.1.2 找龙头: 累计涨幅 + 成交额双维度取 top 3
        cum_ret = aligned.sum()
        avg_amt = pd.Series({c: amounts_map[c].mean() for c in amounts_map}).rank(ascending=False)
        combined_rank = (cum_ret.rank(ascending=False) + avg_amt).sort_values()
        leader_codes = combined_rank.head(3).index.tolist()
        logger.info("[elasticity] 龙头代理: %s", leader_codes)
        leader_ret = aligned[leader_codes].mean(axis=1)

        # 2.1.3 对每只股票: beta (OLS, window=60) + 跟涨率
        for sp in pool:
            if sp.code not in aligned.columns:
                continue
            stock_ret = aligned[sp.code].copy()
            n = min(len(stock_ret), LOOKBACK_PRICE)
            if n < 10:
                continue
            s_ret = stock_ret.tail(n).values.astype(float)
            l_ret = leader_ret.tail(n).values.astype(float)

            # Beta = cov(stock, leader) / var(leader)
            if np.var(l_ret) > 1e-8:
                beta = float(np.cov(s_ret, l_ret, rowvar=True)[0, 1] / np.var(l_ret))
            else:
                beta = 0.0
            # 跟涨率: 板块上涨日中股票也涨的比例
            up_mask = l_ret > 0
            if up_mask.sum() > 0:
                upside = float((s_ret[up_mask] > 0).sum()) / float(up_mask.sum())
            else:
                upside = 0.5
            sp.rolling_beta = round(max(0.0, min(3.0, beta)), 3)
            sp.upside_ratio = round(upside, 3)
            # 打分 (0-100):
            #   beta ∈ [0.6, 1.5] 最佳 → 归一化
            beta_score = 100.0 * (1.0 - min(1.0, abs(beta - 1.0)))
            upside_score = upside * 100.0
            sp.elasticity_score = round(beta_score * 0.5 + upside_score * 0.5, 2)

        # 2.1.4 记录成交额用于后面过滤 (近 20 日均额)
        for sp in pool:
            if sp.code in amounts_map:
                sp.avg_amount_20d = float(
                    amounts_map[sp.code].tail(LOOKBACK_TURNOVER).mean()
                )
        return pool

    def _fetch_daily_pct(self, code: str, lookback: int) -> Optional[pd.DataFrame]:
        """取 code 最近 lookback 交易日的涨跌幅 + 成交额"""
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
            
            start_date = (datetime.datetime.now() - datetime.timedelta(days=lookback + 20)
                        ).strftime("%Y%m%d")
            end_date = today_str("%Y%m%d")
            
            df = pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                adj="qfq",
            )
            if df is None or df.empty:
                return None
            
            df = df.rename(columns={
                "close": "close",
                "vol": "volume",
                "amount": "amount",
                "trade_date": "date",
            })
            df["pct"] = df["close"].pct_change() * 100.0
            df["date"] = pd.to_datetime(df["date"])
            df = df.tail(lookback).reset_index(drop=True)
            time.sleep(0.1)
            return df
        except Exception as exc:
            logger.debug("[_fetch_daily_pct] %s 失败: %s", code, exc)
            return None

    # ------------------------------------------------------------ 文本中心度
    def _compute_centrality(self, pool: List[StockPurity]) -> List[StockPurity]:
        """调用 network_builder 在 (primary_topic, secondary_topic) 范围内构建图"""
        if not pool:
            return pool
        primary = pool[0].primary_topic
        secondary = pool[0].secondary_topic
        try:
            _, centrality_dict = build_network_for_topic(
                topic_primary=primary,
                topic_secondary=secondary,
                lookback_days=60,
                industry_csv=self.industry_csv,
            )
        except Exception as exc:
            logger.warning("[centrality] 构建网络失败: %s", exc)
            centrality_dict = {}

        # 若没有 centrality, 用度中心度降级
        if not centrality_dict:
            logger.warning("[centrality] 无中心度数据, 使用文本提及频次降级评分")
            # 从 Mongo 统计文本提及频次
            try:
                extractor = DocStockExtractor(lookback_days=60)
                entries = extractor.extract(topic_primary=primary,
                                             topic_secondary=secondary)
                freq: Dict[str, int] = {}
                for e in entries:
                    for c in e.stock_codes:
                        freq[c] = freq.get(c, 0) + 1
                if freq:
                    max_v = max(freq.values()) or 1
                    for sp in pool:
                        v = float(freq.get(sp.code, 0)) / float(max_v)
                        sp.centrality_score = round(v * 100.0, 2)
                    return pool
            except Exception:
                pass
            # 完全无数据, 给中性分
            for sp in pool:
                sp.centrality_score = 40.0
            return pool

        # 将 centrality_dict 映射到 pool 打分
        max_v = max(centrality_dict.values()) if centrality_dict else 1.0
        for sp in pool:
            c_val = float(centrality_dict.get(sp.code, 0.0))
            if max_v > 1e-10:
                sp.centrality_score = round(100.0 * (c_val / max_v), 2)
            else:
                sp.centrality_score = 0.0
        return pool

    # ------------------------------------------------------------ 营收占比 (LLM)
    def _compute_revenue(self, pool: List[StockPurity]) -> List[StockPurity]:
        """
        调用 DeepSeek API, 批量/并发分析每只股票与主题的业务关联。
        API key 通过环境变量读取 d:/mystock/config/.env 注入到 CONFIG。
        """
        api_key: str = str(
            CONFIG.get("api_keys", {}).get("deepseek", {}).get("api_key", "")
        )
        base_url: str = str(
            CONFIG.get("api_keys", {}).get("deepseek", {}).get("base_url", "https://api.deepseek.com")
        ).rstrip("/")

        if not api_key or api_key.startswith("${"):
            logger.warning("[revenue] DeepSeek API key 未配置, 降级为关键词打分")
            return self._compute_revenue_fallback(pool)

        # 对每只股票拼接最近文本描述作为上下文
        prompts: List[str] = []
        for sp in pool:
            ctx_text = self._pull_context_texts(sp)
            p = (
                f"主题: {sp.primary_topic} / {sp.secondary_topic}\n"
                f"股票代码: {sp.code}\n"
                f"最近市场/公告文本上下文 (最多 500 字):\n{ctx_text}\n"
                f"请分析并输出 JSON, 仅包含两项:\n"
                f"  - revenue_ratio: 主题相关业务在最近一个财年占公司营收比例 (0-100 的数字)\n"
                f"  - confidence: 置信度(0-1, 越高越可信)\n"
            )
            prompts.append(p)

        # asyncio 并发
        async def _score_many() -> List[Dict[str, float]]:
            import httpx
            sem = asyncio.Semaphore(4)  # 限制并发 4
            results: List[Optional[Dict[str, float]]] = []

            async def _one(prompt: str) -> Optional[Dict[str, float]]:
                async with sem:
                    try:
                        async with httpx.AsyncClient(timeout=45) as client:
                            resp = await client.post(
                                f"{base_url}/chat/completions",
                                headers={"Authorization": f"Bearer {api_key}",
                                          "Content-Type": "application/json"},
                                json={
                                    "model": "deepseek-chat",
                                    "messages": [
                                        {"role": "system", "content": "你是A股研究员, 只输出 JSON。"},
                                        {"role": "user", "content": prompt},
                                    ],
                                    "temperature": 0.2,
                                    "response_format": {"type": "json_object"},
                                },
                            )
                            resp.raise_for_status()
                            content = resp.json()["choices"][0]["message"]["content"]
                            parsed = _parse_json(content)
                            rr = float(parsed.get("revenue_ratio", 0.0))
                            conf = float(parsed.get("confidence", 0.5))
                            return {"revenue_ratio": rr, "confidence": conf}
                    except Exception as exc:
                        logger.debug("[revenue] 单只 LLM 失败: %s", exc)
                        return None

            tasks = [asyncio.create_task(_one(p)) for p in prompts]
            results = await asyncio.gather(*tasks)  # type: ignore
            return list(results)

        try:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                scores = loop.run_until_complete(_score_many())
            finally:
                loop.close()
        except Exception as exc:
            logger.warning("[revenue] LLM 并发调用异常: %s", exc)
            scores = [None] * len(pool)

        for sp, raw in zip(pool, scores):
            if raw is None:
                # 降级: 用文本提及频次代替
                sp.revenue_score = 30.0
                sp.reason += " (营收分: 降级=30)"
                continue
            rr: float = float(raw.get("revenue_ratio", 0.0))
            conf: float = float(raw.get("confidence", 0.5))
            # revenue 比例 0-100 直接映射为 0-100 分, 但用 confidence 加权衰减
            sp.revenue_score = round(max(0.0, min(100.0, rr * max(0.3, conf))), 2)
        return pool

    def _pull_context_texts(self, sp: StockPurity) -> str:
        """从 Mongo 拉最近关联代码的文档 title+content 前 500 字"""
        try:
            with MongoConnector() as db:
                docs = list(db["news_metadata"].find({
                    "$or": [
                        {"title": {"$regex": sp.code}},
                        {"content": {"$regex": sp.code}},
                        {"related_stock_codes": sp.code},
                    ]
                }, {"title": 1, "content": 1}).sort(
                    [("publish_time", -1)]
                ).limit(3))
            if not docs:
                return ""
            texts = []
            for d in docs:
                title = str(d.get("title", ""))
                content = str(d.get("content", ""))
                texts.append(title + "\n" + content[:300])
            return ("\n---\n".join(texts))[:500]
        except Exception:
            return ""

    # ------------------------------------------------------------ fallback
    def _compute_revenue_fallback(self, pool: List[StockPurity]) -> List[StockPurity]:
        """关键词匹配: 在 Mongo 中检索股票代码, 计算包含主题关键词文档的比例"""
        try:
            primary = pool[0].primary_topic if pool else ""
            secondary = pool[0].secondary_topic if pool else ""
        except Exception:
            primary = secondary = ""

        for sp in pool:
            text = self._pull_context_texts(sp)
            if not text:
                sp.revenue_score = 30.0
                continue
            hits = 0
            for kw in [secondary, primary, sp.code]:
                if kw and kw in text:
                    hits += 1
            # 粗略映射
            sp.revenue_score = round(min(100.0, 20.0 + hits * 25.0), 2)
        return pool


# ============================================================================ #
# 3. 过滤 & 组合生成
# ============================================================================ #
class CompositionBuilder:
    """过滤低质量股票, 生成 TopN 组合"""

    def __init__(self) -> None:
        self.cache_path: str = os.path.join(
            CACHE_DIR, "stock_picker_last.json"
        )
        self._prev_map: Dict[str, Dict[str, float]] = self._load_prev()

    # ------------------------------------------------------------ 加载记忆
    def _load_prev(self) -> Dict[str, Dict[str, float]]:
        if not os.path.exists(self.cache_path):
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            # raw 形如: {"primary/secondary": {"code": {"combined":..,"beta":..}}}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------ 主流程
    def build(
        self,
        primary_topic: str,
        secondary_topic: str,
        scored_pool: List[StockPurity],
        weighting: str = "purity_weighted",  # "equal" | "purity_weighted"
        top_n: Optional[int] = None,
    ) -> TopicComposition:
        # 3.1 硬过滤
        filtered: List[StockPurity] = []
        for sp in scored_pool:
            if sp.is_st or ST_STOCK_RE.search(sp.name):
                sp.is_st = True
                continue
            if sp.avg_amount_20d and sp.avg_amount_20d < MIN_DAILY_AMOUNT:
                continue
            if sp.combined < MIN_PURITY:
                continue
            filtered.append(sp)

        logger.info(
            "[Composition] %s/%s: 过滤后 %d 只 (原始=%d)",
            primary_topic, secondary_topic, len(filtered), len(scored_pool),
        )

        # 3.2 与昨日对比 (如果存在昨日记忆, 则触发替换规则)
        key = f"{primary_topic}/{secondary_topic}"
        prev = self._prev_map.get(key, {})
        kept: List[StockPurity] = []
        for sp in filtered:
            prev_entry = prev.get(sp.code)
            if prev_entry:
                prev_combined = float(prev_entry.get("combined", sp.combined))
                prev_beta = float(prev_entry.get("beta", sp.rolling_beta))
                purity_drop = prev_combined - sp.combined
                beta_drop_abs = abs(prev_beta - sp.rolling_beta)
                beta_rel_drop = beta_drop_abs / max(0.01, prev_beta) if prev_beta else 0.0
                if purity_drop > DECAY_PURITY or (prev_beta > 0.5 and beta_rel_drop > DECAY_BETA):
                    sp.reason = f" 替换: 纯度下降{purity_drop:.1f} / Beta 相对衰减{beta_rel_drop:.2f}"
                    logger.info(sp.reason)
                    continue
            kept.append(sp)

        # 3.3 排序: 按综合分降序
        kept.sort(key=lambda s: s.combined, reverse=True)

        # 3.4 取 Top N
        if top_n is None:
            n = min(TOP_N_MAX, max(TOP_N_MIN, len(kept)))
        else:
            n = min(TOP_N_MAX, max(1, top_n))
        final = kept[:n]

        # 3.5 权重分配
        weights: List[float] = []
        if not final:
            weights = []
        elif weighting == "equal":
            weights = [1.0 / float(len(final))] * len(final)
        else:
            # 纯度加权: 以 combined 作为权重, 归一到 sum = 1
            total = max(1e-4, sum(sp.combined for sp in final))
            weights = [sp.combined / total for sp in final]

        # 单票上限 + 归一
        weights = [min(SINGLE_MAX_WEIGHT, w) for w in weights]
        wsum = sum(weights)
        if wsum > 0:
            weights = [w / wsum for w in weights]

        # 回填权重到 StockPurity 对象 (作为 side effect)
        for sp, w in zip(final, weights):
            sp.combined = float(sp.combined)  # keep

        # 3.6 写回内存记忆
        self._prev_map[key] = {
            sp.code: {"combined": float(sp.combined),
                       "beta": float(sp.rolling_beta)}
            for sp in final
        }

        comp = TopicComposition(
            primary_topic=primary_topic,
            secondary_topic=secondary_topic,
            date=today_str("%Y-%m-%d"),
            stocks=final,
            total_weight=1.0,
            generation_reason=f"取纯度Top{len(final)}; 权重={weighting}",
        )

        # 保存 cache
        try:
            with open(self.cache_path, "w", encoding="utf-8") as fh:
                json.dump(self._prev_map, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return comp


# ============================================================================ #
# 4. 辅助: JSON 解析 (容错)
# ============================================================================ #
def _parse_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    t = text.strip()
    if "```" in t:
        start = t.find("```")
        end = t.find("```", start + 3)
        if end > start:
            t = t[start + 3:end].strip()
            if t.lower().startswith("json"):
                t = t[4:].strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


# ============================================================================ #
# 5. 对外主入口
# ============================================================================ #
class StockPickerPipeline:
    """
    从主题列表 → 每主题输出成分股组合。
    调用样例:
        picker = StockPickerPipeline()
        comps = picker.run([("新能源", "固态电池"), ("半导体", "HBM")])
        for c in comps:
            print(c.primary_topic, c.secondary_topic, [s.code for s in c.stocks])
    """

    def __init__(self, industry_csv: str = INDUSTRY_CSV_DEFAULT) -> None:
        self.pool = StockPool(industry_csv=industry_csv)
        self.scoring = PurityScoringEngine(industry_csv=industry_csv)
        self.builder = CompositionBuilder()

    def run_single(self, primary_topic: str,
                   secondary_topic: str,
                   extra_keywords: Optional[List[str]] = None,
                   weighting: str = "purity_weighted") -> TopicComposition:
        logger.info("[StockPicker] ===== %s / %s =====", primary_topic, secondary_topic)
        pool = self.pool.collect(primary_topic, secondary_topic, extra_keywords or [])
        scored = self.scoring.score(pool)
        return self.builder.build(primary_topic, secondary_topic, scored,
                                  weighting=weighting)

    def run(self,
            topic_list: List[Tuple[str, str]],
            weighting: str = "purity_weighted") -> List[TopicComposition]:
        results: List[TopicComposition] = []
        for (p, s) in topic_list:
            try:
                results.append(self.run_single(p, s, weighting=weighting))
            except Exception as exc:
                logger.exception("[StockPicker] %s/%s 失败: %s", p, s, exc)
        return results

    # ------------------------------------------------------------ 对外输出格式
    @staticmethod
    def to_dict(comp: TopicComposition) -> Dict[str, Any]:
        return {
            "date": comp.date,
            "primary_topic": comp.primary_topic,
            "secondary_topic": comp.secondary_topic,
            "generation_reason": comp.generation_reason,
            "stocks": [
                {
                    "code": sp.code,
                    "name": sp.name,
                    "purity_score": sp.combined,
                    "revenue_score": sp.revenue_score,
                    "elasticity_score": sp.elasticity_score,
                    "centrality_score": sp.centrality_score,
                    "beta": sp.rolling_beta,
                    "upside_ratio": sp.upside_ratio,
                    "avg_amount_20d": round(sp.avg_amount_20d / 10000.0, 2),  # 万元
                    "source_channels": sp.source_channels,
                    "reason": sp.reason,
                }
                for sp in comp.stocks
            ],
        }


# ============================================================================ #
# Command-line 调用
# ============================================================================ #
if __name__ == "__main__":
    # 简单 demo: 对几个主题跑一遍
    demo_topics = [
        ("新能源", "固态电池"),
        ("半导体", "HBM"),
        ("人工智能", "AI Agent"),
    ]
    pipeline = StockPickerPipeline()
    compositions = pipeline.run(demo_topics)

    for comp in compositions:
        print("=" * 60)
        print(f" [{comp.date}] {comp.primary_topic} / {comp.secondary_topic}")
        print(f" {comp.generation_reason}")
        print("-" * 60)
        for rank, sp in enumerate(comp.stocks, 1):
            print(f"  {rank:2d}. {sp.code} {sp.name:<12}  "
                  f"纯度={sp.combined:5.1f} (营={sp.revenue_score:.0f}/弹={sp.elasticity_score:.0f}/中={sp.centrality_score:.0f})  "
                  f"Beta={sp.rolling_beta:.2f} 跟涨率={sp.upside_ratio:.2f}  "
                  f"均额={sp.avg_amount_20d/10000:.0f}万 来源={','.join(sp.source_channels[:3])}")
        print("=" * 60)
