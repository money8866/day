"""
金融文本数据采集管线 (Data Collector Pipeline)
================================================
每日从公开财经媒体 / 公告平台抓取文本，做清洗 -> LLM 语义标注 -> 向量嵌入
-> 分别写入 MongoDB (结构化元数据) 与 Milvus (向量检索库)。

模块组成:
    NewsFetcher       文本抓取 (东方财富 RSS / 巨潮资讯 / 模拟数据)
    TextCleaner      HTML/特殊字符清洗
    LLMAnnotator     异步并发 DeepSeek Chat 标注
    Vectorizer       sentence-transformers 向量化
    DataStore        MongoDB + Milvus 持久化 + 去重
    DataPipeline      主流程编排

依赖:
    pip install requests feedparser beautifulsoup4 lxml httpx aiohttp
    pip install pymongo pymilvus sentence-transformers torch
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import asyncio
import hashlib
import random
import logging
import datetime
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 路径兼容：允许作为脚本独立运行
# --------------------------------------------------------------------------- #
_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR: str = os.path.dirname(_CURRENT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from modules.db_connector import (   # noqa: E402
    MongoConnector, MilvusConnector, CONFIG,   # noqa: E402
)
from modules.utils import (    # noqa: E402
    setup_logger, retry, handle_exception, chunk_list, today_str,  # noqa: E402
)

# --------------------------------------------------------------------------- #
# 全局配置缓存 (import-time)
# --------------------------------------------------------------------------- #
logger: logging.Logger = setup_logger(
    name="data_collector",
    log_dir=os.path.join(_PARENT_DIR, "logs"),
    log_file="data_collector.log",
)

# DeepSeek 配置（用户的 API KEY 与 BASE_URL 从 d:\mystock\config\.env 注入）
_DEEPSEEK_CFG: Dict[str, Any] = CONFIG["api_keys"]["deepseek"]
DEEPSEEK_API_KEY: str = str(_DEEPSEEK_CFG.get("api_key", ""))
DEEPSEEK_BASE_URL: str = str(_DEEPSEEK_CFG.get("base_url", "https://api.deepseek.com"))
DEEPSEEK_MODEL: str = str(_DEEPSEEK_CFG.get("chat_model", "deepseek-chat"))
DEEPSEEK_TIMEOUT: int = int(_DEEPSEEK_CFG.get("timeout", 60))
DEEPSEEK_RPM_LIMIT: int = int(_DEEPSEEK_CFG.get("rpm_limit", 60))

# Milvus collection 名
MILVUS_COLLECTION: str = "news_vectors"
# MongoDB 集合名
MONGO_COLLECTION_META: str = "news_metadata"
MONGO_COLLECTION_DEDUP: str = "news_dedup"


# ============================================================================ #
# 1. 数据结构
# ============================================================================ #
@dataclass
class RawNewsItem:
    """原始抓取文本项"""
    title: str
    content: str
    source: str                   # 来源："eastmoney" / "cninfo" / "mock"
    publish_time: str              # ISO 字符串: "YYYY-MM-DD HH:MM:SS"
    url: str = ""                # 原始 URL（用作去重 key）
    raw_extra: Dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """生成去重用哈希：优先 url，否则用 title+content 的 MD5"""
        if self.url:
            return "url::" + self.url
        combined: str = self.title + "||" + self.content[:200]
        return "hash::" + hashlib.md5(combined.encode("utf-8")).hexdigest()


@dataclass
class CleanedNewsItem:
    """清洗后文本项 + LLM 标注 + 向量"""
    dedup_key: str
    title: str
    content: str
    source: str
    publish_time: str

    # --- LLM 填充 ---
    primary_theme: str = ""
    secondary_theme: str = ""
    related_stock_codes: List[str] = field(default_factory=list)
    importance: int = 0

    # --- 向量 (生成时填充) ---
    vector: Optional[List[float]] = field(default=None)
    vector_dim: int = 0

    # --- 系统字段 ---
    processed_at: str = ""

    def to_mongo_doc(self) -> Dict[str, Any]:
        """输出给 MongoDB 的文档"""
        return {
            "dedup_key": self.dedup_key,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "publish_time": self.publish_time,
            "primary_theme": self.primary_theme,
            "secondary_theme": self.secondary_theme,
            "related_stock_codes": self.related_stock_codes,
            "importance": self.importance,
            "vector_dim": self.vector_dim,
            "processed_at": self.processed_at or today_str("%Y-%m-%d %H:%M:%S"),
        }


# ============================================================================ #
# 2. 文本抓取
# ============================================================================ #
class NewsFetcher:
    """
    公开财经新闻 / 公告抓取器。

    策略：
      - 东方财富 RSS (http://data.eastmoney.com/notices/stock 公告 RSS)
      - 巨潮资讯 RSS / cninfo (需要 cookie，这里使用模拟数据作为 fallback)
      - 所有真实抓取失败时，使用 MockDataGenerator 生成可复现的测试数据
    """

    # 东方财富公告/新闻 RSS 列表（可按需扩展）
    EASTMONEY_RSS_FEEDS: List[str] = [
        "http://data.eastmoney.com/notices/getdata.ashx?FirstType=1&SecCode=all",
    ]

    # 巨潮资讯 RSS（公告）
    CNINFO_RSS: str = "http://www.cninfo.com.cn/new/commonUrl?url=/disclosure/fulltext/plate/sz_main_rss.xml"

    # --- 公开: 抓取 -----------------------------------------------------------------

    def fetch_daily(self, target_date: Optional[str] = None) -> List[RawNewsItem]:
        """每日抓取。target_date 默认为今日"""
        target_date = target_date or today_str()
        logger.info("[NewsFetcher] 开始抓取 %s 的文本数据", target_date)

        items: List[RawNewsItem] = []

        # 2.1 尝试东方财富 RSS
        try:
            items.extend(self._fetch_eastmoney_rss(target_date))
            logger.info("[NewsFetcher] 东方财富 RSS 抓到 %d 条", len(items))
        except Exception as exc:
            logger.warning("[NewsFetcher] 东方财富 RSS 失败: %s", exc)

        # 2.2 尝试巨潮资讯
        try:
            items.extend(self._fetch_cninfo(target_date))
        except Exception as exc:
            logger.warning("[NewsFetcher] 巨潮资讯抓取失败: %s", exc)

        # 2.3 若无任何数据，则使用模拟数据生成器
        if not items:
            logger.info("[NewsFetcher] 无任何抓取成功，回退到模拟数据生成器")
            items = self._fetch_mock(target_date)

        logger.info("[NewsFetcher] 抓取完成，共 %d 条原始文本", len(items))
        return items

    # --- 内部: 东方财富 RSS ----------------------------------------------------------

    @retry(max_attempts=3, delay=1.5, backoff=2.0)
    def _fetch_eastmoney_rss(self, target_date: str) -> List[RawNewsItem]:
        """尝试解析东方财富 RSS"""
        import requests
        try:
            import feedparser
        except ImportError:
            logger.warning("[NewsFetcher] 未安装 feedparser，跳过 RSS 解析")
            return []

        items: List[RawNewsItem] = []
        for url in self.EASTMONEY_RSS_FEEDS:
            try:
                parsed = feedparser.parse(url)
                for entry in parsed.entries:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    link = entry.get("link", "")
                    # 发布时间解析
                    pub_time = entry.get("published", "")
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        try:
                            dt = datetime.datetime(*entry.published_parsed[:6])
                            pub_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass
                    if title and (summary or title):
                        items.append(RawNewsItem(
                            title=title,
                            content=summary or title,
                            source="eastmoney_rss",
                            publish_time=pub_time or today_str("%Y-%m-%d %H:%M:%S"),
                            url=link,
                        ))
            except Exception as exc:
                logger.debug("[NewsFetcher] 东方财富 RSS %s 解析异常: %s", url, exc)
                continue

        return items

    # --- 内部: 巨潮资讯 ------------------------------------------------------------

    def _fetch_cninfo(self, target_date: str) -> List[RawNewsItem]:
        """巨潮资讯公告抓取（简化版：返回空让 Mock 接管）"""
        # 巨潮资讯需要动态 cookie 与复杂接口调用
        # 这里只保留扩展入口，真实抓取交给 mock
        return []

    # --- 内部: 模拟数据生成器 --------------------------------------------------------

    def _fetch_mock(self, target_date: str) -> List[RawNewsItem]:
        """模拟测试数据生成器：构造覆盖不同主题的新闻样例"""
        mock_templates: List[Dict[str, Any]] = [
            {
                "title": "新能源汽车销量再创新高，锂矿龙头业绩爆发",
                "content": "2026年6月，国内新能源汽车销量突破100万辆，同比增长45%。"
                           "上游锂矿资源需求强劲，赣锋锂业(002460)、天齐锂业(002466)等龙头公司业绩大幅增长。"
                           "业内分析人士表示，下半年锂价有望维持高位。",
                "source": "mock_news_energy",
            },
            {
                "title": "半导体国产替代持续推进，设备厂商订单饱满",
                "content": "随着国内晶圆厂扩产计划陆续落地，半导体设备公司北方华创(002371)、"
                           "中微公司(688012)订单饱满。国产替代率持续提升，"
                           "政策支持力度加大。",
                "source": "mock_news_tech",
            },
            {
                "title": "大消费板块迎利好政策，白酒龙头业绩稳健",
                "content": "消费刺激政策陆续出台，贵州茅台(600519)、五粮液(000858)等白酒龙头"
                           "企业业绩保持稳健增长。消费升级趋势明显。",
                "source": "mock_news_consume",
            },
            {
                "title": "医药创新药研发取得突破，龙头公司管线进展顺利",
                "content": "恒瑞医药(600276)、药明康德(603259)等创新药龙头公司的新药研发"
                           "取得重要进展，多个品种进入临床后期。",
                "source": "mock_news_medical",
            },
            {
                "title": "军工板块景气度持续上行，航空装备订单大增",
                "content": "中航沈飞(600760)、航发动力(600893)等军工装备龙头公司"
                           "迎来订单高峰，军工信息化加速推进。",
                "source": "mock_news_military",
            },
            {
                "title": "资源品价格持续上涨，铜铝板块业绩弹性大",
                "content": "全球大宗商品市场波动加剧，江西铜业(600362)、"
                           "中国铝业(601600)等资源龙头公司业绩预期向好。",
                "source": "mock_news_resource",
            },
            {
                "title": "金融监管政策优化，银行保险估值修复可期",
                "content": "工商银行(601398)、招商银行(600036)、中国平安(601318)等金融"
                           "龙头企业受益于监管政策优化，估值修复预期增强。",
                "source": "mock_news_finance",
            },
            {
                "title": "光伏装机量超预期，光伏龙头业绩亮眼",
                "content": "隆基绿能(601012)、通威股份(600438)等光伏龙头公司"
                           "受益于全球光伏装机量超预期增长。",
                "source": "mock_news_solar",
            },
        ]

        items: List[RawNewsItem] = []
        now = datetime.datetime.now()
        for i, tpl in enumerate(mock_templates):
            pub_dt = now - datetime.timedelta(hours=i + 1)
            items.append(RawNewsItem(
                title=tpl["title"],
                content=tpl["content"],
                source=tpl["source"],
                publish_time=pub_dt.strftime("%Y-%m-%d %H:%M:%S"),
                url=f"mock://{target_date}/{i}",
            ))

        logger.info("[NewsFetcher] 模拟数据生成器输出 %d 条", len(items))
        return items


# ============================================================================ #
# 3. 文本清洗
# ============================================================================ #
class TextCleaner:
    """去除 HTML 标签、特殊字符，统一日期格式"""

    # HTML 标签正则
    _HTML_TAG: re.Pattern = re.compile(r"<[^>]+>")
    # 多余空白与 HTML entity
    _WHITESPACE: re.Pattern = re.compile(r"\s+")
    _HTML_ENTITY: re.Pattern = re.compile(r"&[a-zA-Z0-9#]+;")
    # 常见中文标点统一 (保持中文标点，仅清理无效符号)
    _INVALID_CHARS: re.Pattern = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    # 日期格式识别: 支持 2026-06-15 / 2026/06/15 / 2026年6月15日 / 06-15 等
    _DATE_PATTERNS: List[Tuple[re.Pattern, str]] = [
        (re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?"), "%Y-%m-%d"),
    ]

    @classmethod
    def clean_html(cls, text: str) -> str:
        if not text:
            return ""
        # 尝试用 BeautifulSoup 做更可靠的去标签
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, "lxml")
            text = soup.get_text(separator=" ", strip=True)
        except Exception:
            # 回退到正则去标签
            text = cls._HTML_TAG.sub(" ", text)
        return text

    @classmethod
    def clean_special_chars(cls, text: str) -> str:
        if not text:
            return ""
        text = cls._HTML_ENTITY.sub(" ", text)      # &nbsp; 等
        text = cls._INVALID_CHARS.sub("", text)       # 控制字符
        text = cls._WHITESPACE.sub(" ", text).strip()
        return text

    @classmethod
    def normalize_dates(cls, text: str) -> str:
        """将文本内的各种日期格式统一为 YYYY-MM-DD"""
        if not text:
            return ""
        for pattern, _ in cls._DATE_PATTERNS:
            def repl(m: re.Match) -> str:
                year, month, day = m.group(1), m.group(2), m.group(3)
                try:
                    dt = datetime.date(int(year), int(month), int(day))
                    return dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    return m.group(0)
            text = pattern.sub(repl, text)
        return text

    @classmethod
    def clean(cls, raw: RawNewsItem) -> CleanedNewsItem:
        """对一条 RawNewsItem 执行完整清洗"""
        title_clean: str = cls.clean_special_chars(cls.clean_html(raw.title))
        content_clean: str = cls.normalize_dates(cls.clean_special_chars(cls.clean_html(raw.content)))
        return CleanedNewsItem(
            dedup_key=raw.dedup_key(),
            title=title_clean,
            content=content_clean,
            source=raw.source,
            publish_time=raw.publish_time,
        )


# ============================================================================ #
# 4. LLM 语义标注（异步并发 + QPS 限制）
# ============================================================================ #
class LLMAnnotator:
    """
    调用 DeepSeek Chat API 做文本 -> 主题 / 个股关联 / 重要程度 标注。
    使用 asyncio + httpx.AsyncClient + Semaphore 实现并发控制。
    """

    # 标准提示词
    PROMPT_TEMPLATE: str = """你是一名资深 A 股研究员，擅长从财经文本做主题识别。请对下面这篇财经文本做语义标注，返回严格的 JSON。

要求:
1. **一级主题** (primary_theme)：从下面选择最贴合的一个：资源, 科技, 消费, 医药, 金融, 军工, 新能源, 其他
2. **二级主题** (secondary_theme)：用 2-8 字描述细分方向（如"锂矿"/"半导体设备"）
3. **关联个股代码** (related_stock_codes)：数组，从文本中明确提到的 A 股公司代码（6位数字，不需要市场后缀）；若未提到则返回空数组
4. **重要程度** (importance)：1~5 整数，5 为最高（影响市场走势 + 产业地位 + 业绩弹性综合判断）

返回 JSON 格式示例:
{"primary_theme": "新能源", "secondary_theme": "锂矿", "related_stock_codes": ["002460", "002466"], "importance": 4}

【文本标题】{title}

【文本内容】{content}

仅输出 JSON，不要任何其他文字和解释。"""

    # JSON 响应的 Schema 强制约束
    _EXPECTED_KEYS: List[str] = [
        "primary_theme", "secondary_theme", "related_stock_codes", "importance",
    ]

    def __init__(self) -> None:
        self.api_key: str = DEEPSEEK_API_KEY
        self.base_url: str = DEEPSEEK_BASE_URL.rstrip("/")
        self.model: str = DEEPSEEK_MODEL
        self.timeout: int = DEEPSEEK_TIMEOUT
        self.rpm_limit: int = DEEPSEEK_RPM_LIMIT
        # 每秒并发 = rpm / 60，至少保留 1
        self._concurrency: int = max(1, self.rpm_limit // 30)

        if not self.api_key or self.api_key.startswith("${"):
            logger.warning(
                "[LLMAnnotator] 未检测到有效 DEEPSEEK_API_KEY，"
                "将使用随机 mock 标注（用于测试）",
            )

    # ------------------------------------------------------------------ 主入口

    async def annotate_batch(self, items: List[CleanedNewsItem]) -> List[CleanedNewsItem]:
        """对一批 CleanedNewsItem 执行并发标注"""
        if not items:
            return items

        logger.info(
            "[LLMAnnotator] 开始并发标注 %d 条文本，并发=%d，RPM<= %d",
            len(items), self._concurrency, self.rpm_limit,
        )

        sem = asyncio.Semaphore(self._concurrency)

        async def _annotate_one(item: CleanedNewsItem) -> CleanedNewsItem:
            async with sem:
                return await self._annotate_single(item)

        tasks = [asyncio.create_task(_annotate_one(it)) for it in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤异常（出错的 item 保持未标注状态，但不阻断整体流程）
        annotated: List[CleanedNewsItem] = []
        for item, res in zip(items, results):
            if isinstance(res, Exception):
                logger.warning("[LLMAnnotator] 单条标注失败：%s", res)
                continue
            annotated.append(res)

        logger.info("[LLMAnnotator] 标注完成，成功 %d / %d", len(annotated), len(items))
        return annotated

    # ------------------------------------------------------------------ 单条标注

    async def _annotate_single(self, item: CleanedNewsItem) -> CleanedNewsItem:
        """对单条执行 DeepSeek 标注"""
        # 若 api_key 未配置，则走 mock
        if not self.api_key or self.api_key.startswith("${"):
            return self._annotate_mock(item)

        try:
            import httpx
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            prompt = self.PROMPT_TEMPLATE.format(
                title=item.title, content=item.content[:2000],
            )
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant that outputs JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "timeout": self.timeout,
            }

            async with httpx.AsyncClient(timeout=self.timeout + 10) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # 解析响应
            content_str = data["choices"][0]["message"]["content"]
            parsed = self._parse_json_response(content_str)

            item.primary_theme = parsed.get("primary_theme", "其他")
            item.secondary_theme = parsed.get("secondary_theme", "")
            codes_raw = parsed.get("related_stock_codes") or []
            # 仅保留 6 位数字代码
            item.related_stock_codes = [c for c in codes_raw if isinstance(c, str) and c.isdigit() and len(c) == 6]
            try:
                item.importance = int(parsed.get("importance", 0))
            except (TypeError, ValueError):
                item.importance = 0
            return item
        except Exception as exc:
            logger.warning("[LLMAnnotator] 标注调用失败：%s", exc)
            # 降级为 mock
            return self._annotate_mock(item)

    # ------------------------------------------------------------------ mock 降级

    def _annotate_mock(self, item: CleanedNewsItem) -> CleanedNewsItem:
        """当 API 不可用时的本地降级标注：基于关键词匹配"""
        text: str = (item.title + " " + item.content)

        # 一级主题关键词表
        keyword_map: Dict[str, List[str]] = {
            "资源": ["锂", "铜", "铝", "黄金", "稀土", "大宗商品", "江西铜业", "中国铝业"],
            "科技": ["半导体", "芯片", "人工智能", "软件", "北方华创", "中微公司"],
            "消费": ["白酒", "食品饮料", "消费", "茅台", "五粮液"],
            "医药": ["医药", "创新药", "恒瑞", "药明康德"],
            "金融": ["银行", "保险", "证券", "金融", "工商银行", "招商银行", "中国平安"],
            "军工": ["军工", "航空", "沈飞", "航发", "装备"],
            "新能源": ["新能源", "光伏", "锂电", "电动车", "隆基", "通威"],
        }
        # 个股代码提取
        code_match = re.findall(r"\((\d{6})\)", text)

        # 简单重要性：文本越长、主题命中越多则越高
        score = 0
        chosen_theme: str = "其他"
        best_hits = 0
        for theme, kws in keyword_map.items():
            hits = sum(1 for kw in kws if kw in text)
            if hits > best_hits:
                best_hits = hits
                chosen_theme = theme
        score += best_hits * 2
        score += min(3, len(code_match))
        importance: int = max(1, min(5, score + 1))

        item.primary_theme = chosen_theme
        item.secondary_theme = (chosen_theme + "相关")
        item.related_stock_codes = list(dict.fromkeys(code_match))
        item.importance = importance
        logger.debug("[LLMAnnotator] mock 标注完成：%s", chosen_theme)
        return item

    # ------------------------------------------------------------------ JSON 解析

    @staticmethod
    def _parse_json_response(raw: str) -> Dict[str, Any]:
        """健壮地解析 LLM 返回的 JSON（兼容带 ```json ... ```）"""
        if not raw:
            return {}
        text: str = raw.strip()
        # 去掉 markdown code fence
        if "```" in text:
            # 找到第一对 ``` 之间的内容
            start = text.find("```")
            end = text.find("```", start + 3)
            if end > start:
                text = text[start + 3:end].strip()
                # 去掉可能的 "json" 前缀
                if text.lower().startswith("json"):
                    text = text[4:].strip()

        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            # 尝试用正则提取 { ... }
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        return {}


# ============================================================================ #
# 5. 向量嵌入（sentence-transformers 多语言模型）
# ============================================================================ #
class Vectorizer:
    """
    使用 sentence-transformers 的 paraphrase-multilingual-MiniLM-L12-v2
    将清洗后的 title + content 转换为向量。

    模型会在首次实例化时从 HuggingFace / 本地缓存加载。
    """

    _MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
    _EXPECTED_DIM: int = 384

    _shared_model: Any = None   # 类级共享模型缓存

    def __init__(self) -> None:
        self._ensure_model_loaded()

    # ------------------------------------------------------------------

    @classmethod
    def _ensure_model_loaded(cls) -> None:
        if cls._shared_model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("[Vectorizer] 加载 %s 模型 ...", cls._MODEL_NAME)
            cls._shared_model = SentenceTransformer(cls._MODEL_NAME)
            logger.info("[Vectorizer] 模型加载完成，维度 = %d", cls._EXPECTED_DIM)
        except Exception as exc:
            logger.error("[Vectorizer] 模型加载失败：%s", exc)
            raise

    # ------------------------------------------------------------------

    def vectorize_batch(self, items: List[CleanedNewsItem]) -> List[CleanedNewsItem]:
        """批量向量化（输入多条文本，返回同一 list，原地填充 vector 字段）"""
        if not items:
            return items
        if self._shared_model is None:
            raise RuntimeError("Vectorizer 模型未初始化")

        texts: List[str] = [f"{it.title}。{it.content}" for it in items]

        logger.info("[Vectorizer] 开始向量化 %d 条文本 ...", len(texts))
        t0 = time.time()
        try:
            # encode() 返回 numpy.ndarray
            vecs = self._shared_model.encode(
                texts,
                batch_size=16,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        except Exception as exc:
            logger.error("[Vectorizer] 向量化失败：%s", exc)
            raise

        for item, vec in zip(items, vecs):
            item.vector = vec.tolist()
            item.vector_dim = len(item.vector)

        logger.info(
            "[Vectorizer] 完成 %d 条，耗时 %.2fs，维度 %d",
            len(items), time.time() - t0, self._EXPECTED_DIM,
        )
        return items

    @property
    def dim(self) -> int:
        return self._EXPECTED_DIM


# ============================================================================ #
# 6. 存储（MongoDB 元数据 + Milvus 向量）
# ============================================================================ #
class DataStore:
    """
    负责：
      1) 去重（基于 dedup_key 在 MongoDB 中检查是否已处理过）
      2) 写入 MongoDB（结构化元数据）
      3) 写入 Milvus（向量）
    """

    def __init__(self, milvus_collection_suffix: str = MILVUS_COLLECTION) -> None:
        self.milvus_suffix: str = milvus_collection_suffix
        self._ensure_mongo_indexes()
        self._ensure_milvus_collection()

    # --------------------------------------------------------- 初始化索引/集合

    def _ensure_mongo_indexes(self) -> None:
        try:
            with MongoConnector() as db:
                col = db[MONGO_COLLECTION_META]
                col.create_index([("dedup_key", 1)], unique=True)
                col.create_index([("publish_time", -1)])
                col.create_index([("primary_theme", 1)])
                col.create_index([("related_stock_codes", 1)])
                logger.info("[DataStore] MongoDB 索引就绪")
        except Exception as exc:
            logger.warning("[DataStore] MongoDB 索引创建异常：%s", exc)

    def _ensure_milvus_collection(self) -> None:
        """创建/确认 Milvus collection"""
        try:
            from pymilvus import (
                connections, utility, Collection,
                FieldSchema, CollectionSchema, DataType,
            )
        except Exception as exc:
            logger.warning("[DataStore] pymilvus 不可用，跳过 Milvus 集合创建: %s", exc)
            return

        try:
            mc = MilvusConnector()
            full_name = mc.get_collection_name(self.milvus_suffix)
            dim = Vectorizer._EXPECTED_DIM
            if utility.has_collection(full_name, using=mc.alias):
                logger.info("[DataStore] Milvus 集合 %s 已存在", full_name)
                return

            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="dedup_key", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="primary_theme", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="secondary_theme", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="importance", dtype=DataType.INT64),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
            ]
            schema = CollectionSchema(fields, description="Financial news semantic vectors")
            collection = Collection(full_name, schema, using=mc.alias)
            # 创建 IVF_FLAT 索引
            index_params = {
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128},
            }
            collection.create_index(field_name="embedding", index_params=index_params)
            logger.info("[DataStore] Milvus 集合 %s 创建完成，维度=%d", full_name, dim)
        except Exception as exc:
            logger.warning("[DataStore] Milvus 集合创建异常：%s", exc)

    # --------------------------------------------------------- 去重

    def filter_new_items(self, items: List[CleanedNewsItem]) -> List[CleanedNewsItem]:
        """检查 MongoDB，返回尚未处理过的 item"""
        if not items:
            return items
        keys: List[str] = [it.dedup_key for it in items]
        try:
            with MongoConnector() as db:
                col = db[MONGO_COLLECTION_META]
                existing = set(col.distinct("dedup_key", {"dedup_key": {"$in": keys}}))
        except Exception as exc:
            logger.warning("[DataStore] 去重查询失败，假定全部为新数据: %s", exc)
            return items

        new_items: List[CleanedNewsItem] = [it for it in items if it.dedup_key not in existing]
        logger.info("[DataStore] 去重：原 %d 条，新 %d 条（已处理 %d 条）",
                    len(items), len(new_items), len(existing))
        return new_items

    # --------------------------------------------------------- 写入

    def save(self, items: List[CleanedNewsItem]) -> Tuple[int, int]:
        """写入 MongoDB + Milvus，返回 (mongo_written, milvus_written)"""
        if not items:
            return 0, 0

        # 6.1 MongoDB
        mongo_count: int = 0
        try:
            with MongoConnector() as db:
                col = db[MONGO_COLLECTION_META]
                docs = [it.to_mongo_doc() for it in items]
                result = col.insert_many(docs, ordered=False)
                mongo_count = len(result.inserted_ids)
            logger.info("[DataStore] MongoDB 写入 %d 条", mongo_count)
        except Exception as exc:
            logger.error("[DataStore] MongoDB 写入失败：%s", exc)

        # 6.2 Milvus
        milvus_count: int = 0
        try:
            from pymilvus import Collection
            mc = MilvusConnector()
            full_name = mc.get_collection_name(self.milvus_suffix)
            collection = Collection(full_name, using=mc.alias)

            # 仅写入 vector 非空的 item
            vec_items = [it for it in items if it.vector]
            if vec_items:
                data = [
                    [it.dedup_key for it in vec_items],
                    [it.title[:512] for it in vec_items],
                    [it.source[:128] for it in vec_items],
                    [it.primary_theme[:64] for it in vec_items],
                    [it.secondary_theme[:128] for it in vec_items],
                    [it.importance for it in vec_items],
                    [it.vector for it in vec_items],
                ]
                mr = collection.insert(data)
                collection.load()
                milvus_count = mr.insert_count if hasattr(mr, "insert_count") else len(vec_items)
                logger.info("[DataStore] Milvus 写入 %d 条向量", milvus_count)
        except Exception as exc:
            logger.error("[DataStore] Milvus 写入失败：%s", exc)

        return mongo_count, milvus_count


# ============================================================================ #
# 7. 主流程编排
# ============================================================================ #
class DataPipeline:
    """完整的数据采集管线：抓取 -> 清洗 -> 标注 -> 向量化 -> 存储"""

    def __init__(self) -> None:
        self.fetcher = NewsFetcher()
        self.cleaner = TextCleaner()
        self.annotator = LLMAnnotator()
        self.vectorizer = Vectorizer()
        self.store = DataStore()

    # ------------------------------------------------------------------

    def run(self, target_date: Optional[str] = None) -> Dict[str, int]:
        """执行完整管线。返回各步骤统计。"""
        target_date = target_date or today_str()
        logger.info("=" * 60)
        logger.info("[DataPipeline] 开始执行，日期 = %s", target_date)

        # Step 1 抓取
        raw_items: List[RawNewsItem] = self.fetcher.fetch_daily(target_date)
        stats = {"raw_count": len(raw_items)}
        if not raw_items:
            logger.warning("[DataPipeline] 无数据，提前结束")
            return stats

        # Step 2 清洗
        cleaned: List[CleanedNewsItem] = [self.cleaner.clean(it) for it in raw_items]
        stats["cleaned_count"] = len(cleaned)

        # Step 3 去重（在 LLM 之前做以节省 token）
        new_items: List[CleanedNewsItem] = self.store.filter_new_items(cleaned)
        stats["new_count"] = len(new_items)
        if not new_items:
            logger.info("[DataPipeline] 全部已处理，结束")
            return stats

        # Step 4 LLM 标注（异步并发）
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            annotated: List[CleanedNewsItem] = loop.run_until_complete(
                self.annotator.annotate_batch(new_items)
            )
        finally:
            loop.close()
        stats["annotated_count"] = len(annotated)
        if not annotated:
            logger.warning("[DataPipeline] 标注结果为空，跳过向量化与存储")
            return stats

        # Step 5 向量化
        vectorized: List[CleanedNewsItem] = self.vectorizer.vectorize_batch(annotated)
        stats["vectorized_count"] = len(vectorized)

        # Step 6 存储
        mongo_written, milvus_written = self.store.save(vectorized)
        stats["mongo_written"] = mongo_written
        stats["milvus_written"] = milvus_written

        logger.info("[DataPipeline] 执行完成，统计 = %s", stats)
        logger.info("=" * 60)
        return stats

    # ------------------------------------------------------------------

    def run_once_and_report(self) -> Dict[str, int]:
        """同 run()，但同时打印摘要到 stdout"""
        stats = self.run()
        print("=" * 50)
        print("  主题投资系统 - 数据采集结果")
        print("=" * 50)
        for k, v in stats.items():
            print(f"  {k:<20s}: {v}")
        print("=" * 50)
        return stats


# ============================================================================ #
# 8. Command-line 独立运行入口
# ============================================================================ #
if __name__ == "__main__":
    pipeline = DataPipeline()
    pipeline.run_once_and_report()
