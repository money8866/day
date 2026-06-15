# -*- coding: utf-8 -*-
"""
A股市场"实盘级主线拆解 + 龙头唯一化 + 资金切换决策引擎" V10.4
=====================================================
核心能力：
  1. 自动生成新主题（Theme Birth）
  2. 自动识别弱主题并淘汰（Theme Death）
  3. 自动合并重复/高度重叠主题（Theme Merge）
  4. 自动拆分过大主题（Theme Split）
  5. 自动更新主题资金强度与生命周期
  6. 主线压缩与去噪（V10.4）- 主线拆解/龙头唯一化/资金切换预测

核心原则：
  - 市场只有1~3条可交易主线
  - 每条主线只有1个唯一龙头
  - 市场是"资金在不同产业链之间的迁移过程"
  - 切换信号优先级 > 主线评分
  - 补涨股只在主线内部选择

评分公式：MainlineScore = 0.55×CapitalFlow + 0.30×Structure + 0.15×Momentum
龙头公式：LeaderScore = 0.4×资金强度 + 0.3×行业地位 + 0.2×结构位置 + 0.1×市场共识度

输出：
  - 1~3条实盘级主线（唯一龙头绑定）
  - 资金切换路径（Rotation Detection）
  - 交易信号（buy/hold/reduce/exit）
"""

import os
import sys
import json
import time
import math
import re
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Optional, Set

warnings.filterwarnings("ignore")

# ============================================================
# 路径配置 & 数据接口（复用 theme_trend_sentiment_score.py）
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # d:\mystock
SOLO_DIR = os.path.join(BASE_DIR, "solo")  # d:\mystock\solo
sys.path.insert(0, SOLO_DIR)

# 导入已有数据接口
from theme_trend_sentiment_score import (
    get_last_trade_date,
    get_dc_members,
    get_stock_basic,
    get_daily_basic,
    get_daily_kline,
    get_index_kline,
    cache_get,
    cache_set,
    BASE_DIR as SCORE_BASE_DIR,
    CACHE_DIR as SCORE_CACHE_DIR,
    DB_PATH,
    TRADE_DATE as SCORE_TRADE_DATE,
    pro,
)

import tushare as ts

# ============================================================
# 报告路径
# ============================================================
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

# ============================================================
# 引擎配置
# ============================================================
CONFIG = {
    # ---- 主题生成 ----
    "BIRTH_MIN_STOCKS": 3,            # 新主题最少股票数
    "BIRTH_MIN_CAPITAL_RESONANCE": 0.7,  # 资金共振度阈值
    "BIRTH_MIN_VOLUME_RATIO": 1.5,       # 放量倍数
    "BIRTH_SIMILARITY_DIMS": 2,          # 至少2维重合（行业/语义/资金）

    # ---- 主题合并 ----
    "MERGE_OVERLAP_RATIO": 0.40,      # 股票重叠度 > 40%
    "MERGE_CAPITAL_SIMILARITY": 0.70, # 资金路径相似度

    # ---- 主题拆分 ----
    "SPLIT_MAX_SUB_THEMES": 6,        # 子主题数 > 6 触发拆分

    # ---- 主题消亡 ----
    "DEATH_CAPITAL_OUTFLOW_DAYS": 3,  # 连续3日资金流出
    "DEATH_VOLUME_DROP_RATIO": 0.40,  # 成交额下降 > 40%
    "DEATH_NO_ZST_DAYS": 3,           # 连板股消失天数

    # ---- 评分权重 (V10.4) ----
    "WEIGHT_CAPITAL": 0.55,
    "WEIGHT_STRUCTURE": 0.30,
    "WEIGHT_MOMENTUM": 0.15,

    # ---- 主线拆解 (V10.4) ----
    "DECOMPOSE_MIN_STOCKS": 15,       # > 15只触发拆解
    "DECOMPOSE_MIN_SUB_CHAINS": 2,    # ≥ 2个子链

    # ---- 资金聚类 ----
    "CLUSTER_MIN_STOCKS": 3,
    "CLUSTER_TOP_N_GAINERS": 30,      # 每日强势股数量
    "CLUSTER_TOP_N_AMOUNT": 50,       # 每日成交额排名

    # ---- 生命周期 ----
    "GROWTH_DAYS": 3,                 # 连续3日资金流入为成长
    "MAINLINE_ROUNDS": 2,             # 2轮资金回流为主线
    "FADING_CAPITAL_DECLINE": 0.3,    # 资金减少30%进入衰退
}

# 不应作为主题名的泛概念名称（系统级/指数级）
GENERIC_CONCEPT_FILTER = [
    "融资融券", "深股通", "沪股通", "MSCI中国",
    "标准普尔", "富时罗素", "证金持股", "社保重仓",
    "AH股", "含H股", "百元股", "低价股", "大盘股",
    "小盘股", "中盘股", "破净", "破发", "高股息",
    "分红", "解禁", "减持", "增持", "回购", "股权激励",
    "机构重仓", "基金重仓", "QFII重仓", "券商重仓",
    "预盈预增", "预亏预减", "业绩预增", "业绩预降",
    "昨日涨停", "昨日连板", "次新股", "近端次新",
    "科创板", "创业板", "主板", "北交所",
    "北京板块", "上海板块", "广东板块", "江苏板块",
    "浙江板块", "深圳板块", "央企改革", "国企改革",
    "一带一路", "雄安新区", "粤港澳", "长三角",
    "专精特新", "独角兽", "行业龙头",
    "东方财富热股", "东方财富搜", "同花顺热股",
]

# ============================================================
# 产业链语义映射表（用于主线压缩合并）
# ============================================================
SEMANTIC_CHAIN_MAP = {
    "AI芯片": "AI算力基础设施",
    "CPO光模块": "AI算力基础设施",
    "高速连接": "AI算力基础设施",
    "AI服务器": "AI算力基础设施",
    "AI存储芯片": "AI算力基础设施",
    "数据中心瓶颈硬件链": "AI算力基础设施",
    "AI算力链": "AI算力基础设施",
    "GPU": "AI算力基础设施",
    "算力": "AI算力基础设施",
    "光模块": "AI算力基础设施",
    "PCB": "AI算力基础设施",
    "通信设备": "AI算力基础设施",
    "AI应用": "AI应用与终端",
    "AI模型与AI Agent": "AI应用与终端",
    "AI终端": "AI应用与终端",
    "AI文化娱乐": "AI应用与终端",
    "AI大模型": "AI应用与终端",
    "AI Agent": "AI应用与终端",
    "半导体设备": "半导体产业链",
    "半导体材料": "半导体产业链",
    "先进封装": "半导体产业链",
    "存储芯片": "半导体产业链",
    "芯片设计": "半导体产业链",
    "功率半导体": "半导体产业链",
    "晶圆代工与IDM": "半导体产业链",
    "半导体": "半导体产业链",
    "电子": "半导体产业链",
    "电力链": "新能源电力",
    "电网数字化": "新能源电力",
    "新型储能": "新能源电力",
    "固态电池": "新能源电力",
    "光伏产业链": "新能源电力",
    "风电产业链": "新能源电力",
    "电池技术": "新能源电力",
    "储能": "新能源电力",
    "光伏": "新能源电力",
    "新能源汽车整车制造": "新能源汽车",
    "动力系统": "新能源汽车",
    "新能源汽车智能化": "新能源汽车",
    "充换电与能源补给": "新能源汽车",
    "新能源汽车材料链": "新能源汽车",
    "汽车零部件": "新能源汽车",
    "智能驾驶": "新能源汽车",
    "创新医药主线": "生物医药",
    "CXO周期修复链": "生物医药",
    "医疗AI智能化": "生物医药",
    "合成生物": "生物医药",
    "脑机接口": "生物医药",
    "中药": "生物医药",
    "医疗器械": "生物医药",
    "创新药": "生物医药",
    "军工": "军工产业链",
    "核聚变": "军工产业链",
    "船舶制造": "军工产业链",
    "军工电子与信息化": "军工产业链",
    "航空装备": "军工产业链",
    "有色资源": "资源周期",
    "煤炭链": "资源周期",
    "稀土永磁": "资源周期",
    "贵金属与黄金": "资源周期",
    "基础化工": "资源周期",
    "钢铁": "资源周期",
    "小金属概念": "资源周期",
    "有色金属": "资源周期",
    "稀土": "资源周期",
    "券商": "金融",
    "银行": "金融",
    "保险": "金融",
    "金融科技": "金融",
    "多元金融": "金融",
    "证券": "金融",
    "必选消费红利链": "消费",
    "情绪消费成长链": "消费",
    "消费电子": "消费",
    "白酒": "消费",
    "家电家装": "消费",
    "家电": "消费",
    "具身智能大模型": "物理AI与机器人",
    "机器视觉与3D感知": "物理AI与机器人",
    "传感器": "物理AI与机器人",
    "边缘计算与AI芯片": "物理AI与机器人",
    "数字孪生与工业仿真": "物理AI与机器人",
    "执行器总成": "物理AI与机器人",
    "精密减速器": "物理AI与机器人",
    "伺服电机与运动控制": "物理AI与机器人",
    "灵巧手": "物理AI与机器人",
    "行星滚柱丝杠": "物理AI与机器人",
    "人形机器人整机与集成": "物理AI与机器人",
    "机器人": "物理AI与机器人",
    "低空飞行器制造": "低空经济",
    "低空运营服务": "低空经济",
    "低空基础设施": "低空经济",
    "低空数据与控制": "低空经济",
    "eVTOL": "低空经济",
    "卫星制造与发射": "商业航天",
    "卫星运营": "商业航天",
    "卫星应用": "商业航天",
    "卫星互联网": "商业航天",
    "商业火箭": "商业航天",
    "5G概念": "5G通信",
    "5G": "5G通信",
    "通信服务": "5G通信",
}

# 泛概念/泛行业主题过滤（V10.3 新增）
# 这些主题过于宽泛，不是可交易的主线
GENERIC_THEME_FILTER = [
    "电子", "科技", "科技风格", "综合", "其他", "概念",
    "风格", "行业", "板块", "指数", "大盘", "市场",
    "新材料", "新能源", "新技术", "高端制造",
    "周期", "成长", "价值", "蓝筹",
    "主题", "热点", "活跃",
    "沪深300", "中证500", "上证50", "中证1000",
    "创业板指", "科创50",
    "HS300", "ZZ500", "SZ50",
]

# 噪声主题模式（匹配即删除）
NOISE_THEME_PATTERNS = [
    "东方财富", "同花顺", "涨停榜", "跌停榜",
    "异动", "热股", "热搜",
    "高振幅", "高换手", "高活跃",
    "融资融券", "MSCI", "标准普尔",
    "富时罗素", "证金持股", "社保重仓",
    "百元股", "低价股", "大盘股", "小盘股", "中盘股",
    "破净", "破发",
    "次新股", "近端次新",
    "科创板", "创业板",
    "北京板块", "上海板块", "广东板块",
    "江苏板块", "浙江板块", "深圳板块",
]

ZST_RATIO_NOISE_THRESHOLD = 0.70  # 涨停占比 > 70% 视为情绪驱动

# ============================================================
# 全局状态存储
# ============================================================
history_themes = {}
active_themes = {}
stock_theme_map = defaultdict(set)
theme_graph_edges = []


# ============================================================
# 主题节点类
# ============================================================
class ThemeNode:
    def __init__(self, name: str, birth_date: str):
        self.name = name
        self.birth_date = birth_date
        self.stage = "BIRTH"
        self.status = "Active"
        self.score = 0.0
        self.stocks = set()
        self.stock_details = {}
        self.sub_themes = []
        self.parent_theme = None
        self.history = []
        self.capital_flow = []
        self.capital_trend = "neutral"
        self.capital_streak = 0
        self.consecutive_outflow_days = 0
        self.leader_stocks = []
        self.zt_count = 0
        self.strong_stock_count = 0
        self.volume_ratio = 1.0
        self.industry_tags = set()
        self.concept_tags = set()

    def to_dict(self) -> dict:
        return {
            "theme": self.name,
            "stage": self.stage,
            "score": round(self.score, 1),
            "capital_trend": self.capital_trend,
            "status": self.status,
            "stock_count": len(self.stocks),
            "zt_count": self.zt_count,
            "strong_stock_count": self.strong_stock_count,
            "volume_ratio": round(self.volume_ratio, 2),
            "capital_streak": self.capital_streak,
            "leader_stocks": self.leader_stocks[:5],
            "sub_themes": self.sub_themes,
            "parent_theme": self.parent_theme,
            "birth_date": self.birth_date,
        }


# ============================================================
# 数据加载模块
# ============================================================
class DataLoader:
    def __init__(self, trade_date: str = None):
        self.trade_date = trade_date or get_last_trade_date()
        self.dc_df = None
        self.stock_basic = None
        self.daily_basic = None
        self.daily_trade = None
        self.zt_pool = None
        self.strong_pool = None

    def load_all(self):
        print(f"[DataLoader] 加载数据: {self.trade_date}")
        self.dc_df = get_dc_members()
        self.stock_basic = get_stock_basic()
        self.daily_basic = get_daily_basic(self.trade_date)
        print(f"[DataLoader] DC={len(self.dc_df) if self.dc_df is not None else 0}, "
              f"Stock={len(self.stock_basic) if self.stock_basic is not None else 0}")
        self._load_daily_trade()
        return self

    def _load_daily_trade(self):
        cached = cache_get("daily_trade_all", trade_date=self.trade_date)
        if cached is not None:
            print(f"[DataLoader] 日线行情缓存命中: {len(cached)} 条")
            self.daily_trade = cached
            return
        if pro is None:
            self.daily_trade = pd.DataFrame()
            return
        try:
            print("[DataLoader] 拉取当日全量日线行情...")
            df = pro.daily(trade_date=self.trade_date)
            time.sleep(0.15)
            if df is not None and not df.empty:
                cache_set("daily_trade_all", df, trade_date=self.trade_date)
                print(f"[DataLoader] 日线行情: {len(df)} 条")
            self.daily_trade = df if df is not None else pd.DataFrame()
        except Exception as e:
            print(f"[DataLoader] 日线行情拉取失败: {e}")
            self.daily_trade = pd.DataFrame()

    def get_top_gainers(self, n: int = 30) -> pd.DataFrame:
        if not hasattr(self, 'daily_trade') or self.daily_trade is None or self.daily_trade.empty:
            return pd.DataFrame()
        top = self.daily_trade.sort_values("pct_chg", ascending=False).head(n)
        if self.stock_basic is not None:
            top = top.merge(self.stock_basic[["ts_code", "name", "industry"]], on="ts_code", how="left")
        return top

    def get_top_amount(self, n: int = 50) -> pd.DataFrame:
        if not hasattr(self, 'daily_trade') or self.daily_trade is None or self.daily_trade.empty:
            return pd.DataFrame()
        top = self.daily_trade.sort_values("amount", ascending=False).head(n)
        if self.stock_basic is not None:
            top = top.merge(self.stock_basic[["ts_code", "name", "industry"]], on="ts_code", how="left")
        return top

    def load_kline(self, ts_codes: List[str], days: int = 60):
        end = self.trade_date
        start_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=days)
        start = start_dt.strftime("%Y%m%d")
        return get_daily_kline(ts_codes, start, end)

    def load_index_kline(self, days: int = 60):
        end = self.trade_date
        start_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=days)
        start = start_dt.strftime("%Y%m%d")
        return get_index_kline("000300.SH", start, end)


# ============================================================
# 资金聚类分析模块
# ============================================================
class CapitalClusterEngine:
    def __init__(self, data_loader: DataLoader):
        self.dl = data_loader
        self.stock_similarity = {}
        self.clusters = []
        self.trade_date = data_loader.trade_date

    def compute_stock_similarity(self, code_a: str, code_b: str,
                                 industry_a: str, industry_b: str,
                                 concepts_a: set, concepts_b: set,
                                 amount_a: float, amount_b: float,
                                 pct_a: float, pct_b: float) -> Tuple[float, List[str]]:
        matched_dims = []
        dim_score = 0.0
        ind_score = 0.0
        if industry_a and industry_b:
            if industry_a == industry_b:
                ind_score = 1.0
            elif industry_a[:2] == industry_b[:2]:
                ind_score = 0.5
        dim_score += ind_score * 0.3
        if ind_score > 0.5:
            matched_dims.append("industry")

        concept_score = 0.0
        if concepts_a and concepts_b:
            overlap = concepts_a & concepts_b
            if overlap:
                jaccard = len(overlap) / max(len(concepts_a | concepts_b), 1)
                concept_score = min(jaccard * 2, 1.0)
        dim_score += concept_score * 0.3
        if concept_score > 0.3:
            matched_dims.append("concept")

        capital_score = 0.0
        if amount_a > 0 and amount_b > 0:
            amount_ratio = min(amount_a, amount_b) / max(amount_a, amount_b)
            capital_score += amount_ratio * 0.5
        if (pct_a > 0 and pct_b > 0) or (pct_a < 0 and pct_b < 0):
            capital_score += 0.5
        dim_score += capital_score * 0.4
        if capital_score > 0.5:
            matched_dims.append("capital")

        return dim_score, matched_dims

    def get_stock_concepts(self, code: str) -> set:
        concepts = set()
        if self.dl.dc_df is not None and not self.dl.dc_df.empty:
            matches = self.dl.dc_df[self.dl.dc_df["con_code"] == code]
            for _, row in matches.iterrows():
                cname = str(row.get("concept_name", ""))
                if cname:
                    concepts.add(cname)
        return concepts

    def cluster(self, top_gainers: pd.DataFrame, top_amount: pd.DataFrame) -> List[dict]:
        all_codes = set()
        if not top_gainers.empty:
            all_codes.update(top_gainers["ts_code"].tolist())
        if not top_amount.empty:
            all_codes.update(top_amount["ts_code"].tolist())

        if len(all_codes) < CONFIG["CLUSTER_MIN_STOCKS"]:
            print(f"[Cluster] 候选股票不足 ({len(all_codes)} < {CONFIG['CLUSTER_MIN_STOCKS']})")
            return []

        stock_info = {}
        for code in all_codes:
            row = None
            if not top_gainers.empty:
                gainer = top_gainers[top_gainers["ts_code"] == code]
                if not gainer.empty:
                    row = gainer.iloc[0]
            if row is None and not top_amount.empty:
                amt = top_amount[top_amount["ts_code"] == code]
                if not amt.empty:
                    row = amt.iloc[0]
            if row is None:
                continue
            name = str(row.get("name", ""))
            industry = str(row.get("industry", ""))
            pct_chg = float(row.get("pct_chg", 0))
            amount = float(row.get("amount", 0))
            concepts = self.get_stock_concepts(code)
            stock_info[code] = {
                "name": name, "industry": industry,
                "pct_chg": pct_chg, "amount": amount, "concepts": concepts,
            }

        codes = list(stock_info.keys())
        if len(codes) < CONFIG["CLUSTER_MIN_STOCKS"]:
            return []

        similarity_pairs = []
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                ca, cb = codes[i], codes[j]
                info_a = stock_info[ca]
                info_b = stock_info[cb]
                score, matched_dims = self.compute_stock_similarity(
                    ca, cb,
                    info_a["industry"], info_b["industry"],
                    info_a["concepts"], info_b["concepts"],
                    info_a["amount"], info_b["amount"],
                    info_a["pct_chg"], info_b["pct_chg"],
                )
                if score >= CONFIG["BIRTH_MIN_CAPITAL_RESONANCE"] \
                   and len(matched_dims) >= CONFIG["BIRTH_SIMILARITY_DIMS"]:
                    similarity_pairs.append({
                        "code_a": ca, "code_b": cb, "score": score,
                        "dims": matched_dims,
                        "name_a": info_a["name"], "name_b": info_b["name"],
                    })

        parent = {c: c for c in codes}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for pair in similarity_pairs:
            union(pair["code_a"], pair["code_b"])

        cluster_map = defaultdict(list)
        for code in codes:
            root = find(code)
            cluster_map[root].append(code)

        valid_clusters = []
        for root, members in cluster_map.items():
            if len(members) < CONFIG["CLUSTER_MIN_STOCKS"]:
                continue
            total_score = 0.0
            total_volume = 0.0
            member_details = []
            for code in members:
                info = stock_info[code]
                total_score += info["pct_chg"] if info["pct_chg"] > 0 else 0
                total_volume += info["amount"]
                member_details.append({
                    "code": code, "name": info["name"],
                    "industry": info["industry"],
                    "pct_chg": info["pct_chg"], "amount": info["amount"],
                })

            common_concepts = Counter()
            for code in members:
                for c in stock_info[code]["concepts"]:
                    common_concepts[c] += 1
            filtered_concepts = [(c, cnt) for c, cnt in common_concepts.most_common(10)
                                 if c not in GENERIC_CONCEPT_FILTER
                                 and cnt >= len(members) * 0.3]
            top_concepts = [c for c, _ in filtered_concepts[:5]]

            if top_concepts:
                theme_name = top_concepts[0]
            else:
                industries = [s["industry"] for s in member_details if s.get("industry")]
                if industries:
                    ind_counter = Counter(industries)
                    top_ind = ind_counter.most_common(1)[0][0]
                    top_ind = top_ind.replace("II", "").replace("III", "")
                    theme_name = f"{top_ind}活跃"
                else:
                    theme_name = f"新聚类_{root[:6]}"

            valid_clusters.append({
                "theme_name": theme_name,
                "stocks": members,
                "stock_details": member_details,
                "avg_score": total_score / len(members),
                "total_amount": total_volume,
                "top_concepts": top_concepts,
                "common_industries": list(set(s["industry"] for s in member_details if s["industry"])),
                "size": len(members),
            })

        valid_clusters.sort(key=lambda x: x["total_amount"], reverse=True)
        self.clusters = valid_clusters
        return valid_clusters


# ============================================================
# 主题生命周期管理器
# ============================================================
class LifecycleManager:
    def __init__(self):
        self.active_themes = active_themes
        self.history_themes = history_themes

    def update_theme(self, theme: ThemeNode, today_data: dict) -> str:
        old_stage = theme.stage
        theme.capital_flow.append(today_data.get("net_flow", 0))
        theme.zt_count = today_data.get("zt_count", 0)
        theme.strong_stock_count = today_data.get("strong_count", 0)
        theme.volume_ratio = today_data.get("volume_ratio", 1.0)

        net_flow = today_data.get("net_flow", 0)
        if net_flow > 0:
            theme.capital_streak = theme.capital_streak + 1 if theme.capital_streak > 0 else 1
            theme.consecutive_outflow_days = 0
        elif net_flow < 0:
            theme.capital_streak = theme.capital_streak - 1 if theme.capital_streak < 0 else -1
            theme.consecutive_outflow_days += 1
        else:
            theme.capital_streak = 0

        if old_stage == "BIRTH":
            if theme.capital_streak >= CONFIG["GROWTH_DAYS"]:
                theme.stage = "GROWTH"
            elif theme.consecutive_outflow_days >= CONFIG["FADING_CAPITAL_DECLINE"]*10:
                theme.stage = "DEATH"
        elif old_stage == "GROWTH":
            if self._count_inflow_rounds(theme) >= CONFIG["MAINLINE_ROUNDS"]:
                theme.stage = "MAINLINE"
            elif theme.consecutive_outflow_days >= 2:
                theme.stage = "FADING"
        elif old_stage == "MAINLINE":
            if theme.consecutive_outflow_days >= 2:
                theme.stage = "FADING"
        elif old_stage == "FADING":
            if self._check_death(theme):
                theme.stage = "DEATH"
                theme.status = "Dead"
            elif theme.capital_streak >= 2:
                theme.stage = "GROWTH"
                theme.status = "Active"
        elif old_stage == "DEATH":
            theme.status = "Dead"

        recent = theme.capital_flow[-5:] if len(theme.capital_flow) >= 5 else theme.capital_flow
        if recent:
            avg_flow = np.mean(recent)
            if avg_flow > 0:
                theme.capital_trend = "inflow"
            elif avg_flow < 0:
                theme.capital_trend = "outflow"
            else:
                theme.capital_trend = "neutral"

        theme.history.append({
            "date": today_data.get("date", ""),
            "stage": theme.stage, "score": theme.score,
            "capital_trend": theme.capital_trend, "zt_count": theme.zt_count,
        })
        return theme.stage

    def _count_inflow_rounds(self, theme: ThemeNode) -> int:
        if len(theme.capital_flow) < 5:
            return 0
        rounds = 0
        in_inflow = False
        for flow in theme.capital_flow[-20:]:
            if flow > 0 and not in_inflow:
                rounds += 1
                in_inflow = True
            elif flow <= 0:
                in_inflow = False
        return rounds

    def _check_death(self, theme: ThemeNode) -> bool:
        death_conditions = 0
        if theme.consecutive_outflow_days >= CONFIG["DEATH_CAPITAL_OUTFLOW_DAYS"]:
            death_conditions += 1
        if theme.zt_count == 0:
            death_conditions += 1
        if theme.volume_ratio < (1 - CONFIG["DEATH_VOLUME_DROP_RATIO"]):
            death_conditions += 1
        return death_conditions >= 2


# ============================================================
# 主题合并引擎
# ============================================================
class MergeEngine:
    def __init__(self, active_themes: Dict[str, ThemeNode]):
        self.themes = active_themes

    def find_merge_candidates(self) -> List[Tuple[str, str, float]]:
        candidates = []
        theme_names = list(self.themes.keys())
        for i in range(len(theme_names)):
            for j in range(i + 1, len(theme_names)):
                ta, tb = theme_names[i], theme_names[j]
                node_a, node_b = self.themes[ta], self.themes[tb]
                if node_a.status == "Dead" or node_b.status == "Dead":
                    continue
                stocks_a, stocks_b = node_a.stocks, node_b.stocks
                if not stocks_a or not stocks_b:
                    continue
                overlap = stocks_a & stocks_b
                overlap_ratio = len(overlap) / min(len(stocks_a), len(stocks_b))
                capital_sim = self._capital_similarity(node_a, node_b)
                if overlap_ratio >= CONFIG["MERGE_OVERLAP_RATIO"] \
                   and capital_sim >= CONFIG["MERGE_CAPITAL_SIMILARITY"]:
                    candidates.append((ta, tb, overlap_ratio))
        return candidates

    def _capital_similarity(self, a: ThemeNode, b: ThemeNode) -> float:
        if not a.capital_flow or not b.capital_flow:
            return 0.0
        min_len = min(len(a.capital_flow), len(b.capital_flow))
        if min_len < 3:
            return 0.0
        fa = np.array(a.capital_flow[-min_len:])
        fb = np.array(b.capital_flow[-min_len:])
        if np.std(fa) == 0 or np.std(fb) == 0:
            return 0.0
        corr = np.corrcoef(fa, fb)[0, 1]
        return max(0, corr)

    def execute_merge(self, main_theme: str, weak_theme: str) -> ThemeNode:
        main_node = self.themes[main_theme]
        weak_node = self.themes[weak_theme]
        if weak_node.score > main_node.score:
            main_theme, weak_theme = weak_theme, main_theme
            main_node, weak_node = weak_node, main_node
        main_node.stocks.update(weak_node.stocks)
        main_node.sub_themes.append(weak_theme)
        weak_node.parent_theme = main_theme
        weak_node.status = "Merged"
        min_len = min(len(main_node.capital_flow), len(weak_node.capital_flow))
        for i in range(min_len):
            main_node.capital_flow[i] = (main_node.capital_flow[i] + weak_node.capital_flow[i]) / 2
        print(f"[Merge] {weak_theme} -> {main_theme}")
        return main_node


# ============================================================
# 主题拆分引擎
# ============================================================
class SplitEngine:
    def __init__(self, active_themes: Dict[str, ThemeNode]):
        self.themes = active_themes

    def find_split_candidates(self) -> List[str]:
        candidates = []
        for name, node in self.themes.items():
            if node.status == "Dead":
                continue
            if len(node.sub_themes) > CONFIG["SPLIT_MAX_SUB_THEMES"]:
                candidates.append(name)
            elif len(node.stocks) > 20:
                industries = set()
                for code in node.stocks:
                    detail = node.stock_details.get(code, {})
                    ind = detail.get("industry", "")
                    if ind:
                        industries.add(ind)
                if len(industries) >= 4:
                    candidates.append(name)
        return list(set(candidates))

    def execute_split(self, theme_name: str) -> List[ThemeNode]:
        node = self.themes[theme_name]
        if not node.stocks:
            return []
        industry_groups = defaultdict(list)
        for code in node.stocks:
            detail = node.stock_details.get(code, {})
            industry = detail.get("industry", "未知")
            industry_groups[industry].append(code)
        sorted_groups = sorted(industry_groups.items(), key=lambda x: len(x[1]), reverse=True)
        if len(sorted_groups) < 2:
            return [node]
        main_industry, main_stocks = sorted_groups[0]
        sub_groups = sorted_groups[1:]
        node.stocks = set(main_stocks)
        node.name = f"{theme_name}(主线)"
        node.stage = "MAINLINE"
        children = []
        for ind, stocks in sub_groups[:3]:
            child_name = f"{theme_name}({ind})"
            child = ThemeNode(child_name, node.birth_date)
            child.stocks = set(stocks)
            child.parent_theme = theme_name
            child.stage = "GROWTH"
            child.capital_flow = node.capital_flow[-5:] if node.capital_flow else []
            self.themes[child_name] = child
            children.append(child)
        node.sub_themes = [c.name for c in children]
        return [node] + children


# ============================================================
# 主题评分引擎
# ============================================================
class ScoreEngine:
    def calc_capital_score(self, theme: ThemeNode) -> float:
        score = 50.0
        flows = theme.capital_flow[-10:] if len(theme.capital_flow) >= 10 else theme.capital_flow
        if flows:
            positive_ratio = sum(1 for f in flows if f > 0) / len(flows)
            score += positive_ratio * 20
        if theme.capital_trend == "inflow":
            score += 15
        elif theme.capital_trend == "outflow":
            score -= 15
        score += min(theme.volume_ratio * 10, 15)
        return max(0, min(100, score))

    def calc_structure_score(self, theme: ThemeNode) -> float:
        score = 50.0
        if theme.leader_stocks:
            score += 15
        score += min(len(theme.sub_themes) * 5, 15)
        stock_cnt = len(theme.stocks)
        if 5 <= stock_cnt <= 30:
            score += 10
        elif stock_cnt > 30:
            score += 5
        else:
            score -= 10
        industries = set()
        for detail in theme.stock_details.values():
            ind = detail.get("industry", "")
            if ind:
                industries.add(ind)
        if len(industries) >= 3:
            score += 10
        return max(0, min(100, score))

    def calc_momentum_score(self, theme: ThemeNode) -> float:
        score = 50.0
        score += min(theme.zt_count * 10, 20)
        score += min(theme.strong_stock_count * 5, 15)
        recent_pct = []
        for detail in theme.stock_details.values():
            p = detail.get("pct_chg", 0)
            if isinstance(p, (int, float)):
                recent_pct.append(p)
        if recent_pct:
            avg_pct = np.mean(recent_pct)
            if avg_pct > 5:
                score += 15
            elif avg_pct > 2:
                score += 8
            elif avg_pct < -2:
                score -= 10
        return max(0, min(100, score))

    def calc_total_score(self, theme: ThemeNode) -> float:
        """V10.4: 0.55×资金 + 0.30×结构 + 0.15×动量"""
        cap_score = self.calc_capital_score(theme)
        struct_score = self.calc_structure_score(theme)
        mom_score = self.calc_momentum_score(theme)
        total = (CONFIG["WEIGHT_CAPITAL"] * cap_score
                 + CONFIG["WEIGHT_STRUCTURE"] * struct_score
                 + CONFIG["WEIGHT_MOMENTUM"] * mom_score)
        theme.score = total
        return total


# ============================================================
# 主题图谱生成器
# ============================================================
class ThemeGraphBuilder:
    def __init__(self, active_themes: Dict[str, ThemeNode]):
        self.themes = active_themes
        self.edges = []

    def build_graph(self) -> List[dict]:
        edges = []
        theme_names = list(self.themes.keys())
        for i in range(len(theme_names)):
            for j in range(i + 1, len(theme_names)):
                ta, tb = theme_names[i], theme_names[j]
                na, nb = self.themes[ta], self.themes[tb]
                if na.status == "Dead" or nb.status == "Dead":
                    continue
                overlap = na.stocks & nb.stocks
                if not overlap:
                    continue
                overlap_ratio = len(overlap) / min(len(na.stocks), len(nb.stocks))
                weight = self._calc_edge_weight(na, nb, overlap_ratio)
                direction = ta if na.score < nb.score else tb
                target = tb if direction == ta else ta
                edges.append({
                    "from": direction, "to": target,
                    "weight": round(weight, 2),
                    "overlap_stocks": list(overlap)[:10],
                    "overlap_ratio": round(overlap_ratio, 2),
                })
        edges.sort(key=lambda x: x["weight"], reverse=True)
        self.edges = edges
        return edges

    def _calc_edge_weight(self, a: ThemeNode, b: ThemeNode, overlap_ratio: float) -> float:
        weight = overlap_ratio * 0.4
        if a.capital_flow and b.capital_flow:
            min_len = min(len(a.capital_flow), len(b.capital_flow))
            if min_len >= 3:
                fa = np.array(a.capital_flow[-min_len:])
                fb = np.array(b.capital_flow[-min_len:])
                if np.std(fa) > 0 and np.std(fb) > 0:
                    corr = max(0, np.corrcoef(fa, fb)[0, 1])
                    weight += corr * 0.3
        score_diff = abs(a.score - b.score) / 100
        weight += (1 - score_diff) * 0.3
        return min(1.0, weight)


# ============================================================
# 主线压缩与去噪引擎（V10.2.1 核心）
# ============================================================
class MainlineCompressionEngine:
    """
    主线压缩与去噪引擎
    核心任务：
    1. 去噪 - 删除情绪主题、数据源主题、伪产业主题
    2. 合并 - 合并语义相同/产业链相邻/资金共振主题
    3. 压缩 - 输出5~8条真正的市场主线
    """

    def __init__(self, raw_themes: Dict[str, ThemeNode]):
        self.raw_themes = raw_themes
        self.noise_removed = []
        self.merged_pairs = []
        self.mainlines = []
        self.remaining_themes = {}
        self.decomposed = []
        self.rotation_signal = {}
        self.trade_signal = {}
        self.capital_flow_graph = []
        self.market_conclusion = {}

    def run(self) -> Tuple[List[dict], List[str], List[dict], List[dict], dict, dict, dict]:
        themes = {k: v for k, v in self.raw_themes.items() if v.status == "Active"}

        # 先合并后去噪
        themes, self.merged_pairs = self._semantic_merge(themes)
        print(f"  [合并] 合并 {len(self.merged_pairs)} 组产业链主题")

        # V10.4: 主线自动拆解（大主题按产业链拆分为子链）
        themes, self.decomposed = self._decompose_mainlines(themes)
        if self.decomposed:
            print(f"  [拆解] 拆解 {len(self.decomposed)} 个大主题: {', '.join(d['original'] for d in self.decomposed)}")

        themes, self.noise_removed = self._filter_noise(themes)
        print(f"  [去噪] 删除 {len(self.noise_removed)} 个噪声主题: {self.noise_removed}")

        themes, overlap_merges = self._overlap_merge(themes)
        self.merged_pairs.extend(overlap_merges)
        if overlap_merges:
            print(f"  [合并] 股票重叠合并 {len(overlap_merges)} 组")

        self.remaining_themes = themes
        self.mainlines = self._identify_mainlines(themes)
        print(f"  [压缩] 识别出 {len(self.mainlines)} 条主线")

        # V10.4: 构建切换信号
        self.rotation_signal = self._detect_rotation(self.mainlines)

        # V10.4: 资金路径图和市场结论
        self.capital_flow_graph = self._build_capital_flow_graph(self.mainlines)
        self.market_conclusion = self._build_market_conclusion(self.mainlines)
        self.trade_signal = self._build_trade_signal(self.mainlines, self.rotation_signal)

        print(f"  [切换信号] {self.rotation_signal.get('signal','无')}")
        print(f"  [交易信号] {self.trade_signal.get('action','hold')} -> {self.trade_signal.get('target','')}")

        return self.mainlines, self.noise_removed, self.merged_pairs, self.capital_flow_graph, self.market_conclusion, self.rotation_signal, self.trade_signal

    def _filter_noise(self, themes: Dict[str, ThemeNode]) -> Tuple[Dict[str, ThemeNode], List[str]]:
        chain_names = set(SEMANTIC_CHAIN_MAP.keys()) | set(SEMANTIC_CHAIN_MAP.values())
        cleaned = {}
        noise_list = []
        for name, node in themes.items():
            is_noise = False
            for pattern in NOISE_THEME_PATTERNS:
                if re.search(pattern, name):
                    is_noise = True
                    break
            # V10.3: 过滤泛概念/泛行业主题（仅对非产业链名生效）
            if not is_noise and name not in chain_names:
                for g in GENERIC_THEME_FILTER:
                    if g in name:
                        is_noise = True
                        break
            # 涨停占比过高视为情绪驱动
            if not is_noise and node.zt_count > 0 and len(node.stocks) > 0:
                zst_ratio = node.zt_count / len(node.stocks)
                if zst_ratio > ZST_RATIO_NOISE_THRESHOLD:
                    is_noise = True
            # V10.3: 结构不完整主题过滤（股票数 < 5）
            if not is_noise and len(node.stocks) < 5:
                is_noise = True
            if is_noise:
                noise_list.append(name)
            else:
                cleaned[name] = node
        return cleaned, noise_list

    def _semantic_merge(self, themes: Dict[str, ThemeNode]) -> Tuple[Dict[str, ThemeNode], List[dict]]:
        # 第一步：将带编号后缀的主题（如"电子_2"）重新映射为基名
        renamed = {}
        for name, node in themes.items():
            base_name = re.sub(r'_\d+$', '', name)
            if base_name != name:
                renamed[name] = base_name
        for old_name, new_name in renamed.items():
            if new_name in themes:
                # 合并到基名主题
                base_node = themes[new_name]
                sub_node = themes[old_name]
                base_node.stocks.update(sub_node.stocks)
                base_node.stock_details.update(sub_node.stock_details)
                base_node.zt_count += sub_node.zt_count
                base_node.strong_stock_count += sub_node.strong_stock_count
                base_node.sub_themes.append(old_name)
                print(f"    [编号合并] {old_name} -> {new_name}")
            else:
                themes[new_name] = themes[old_name]
                themes[new_name].name = new_name
            del themes[old_name]

        chain_groups = defaultdict(list)
        unknown = []
        for name, node in themes.items():
            chain = SEMANTIC_CHAIN_MAP.get(name)
            if chain is None:
                for key, val in SEMANTIC_CHAIN_MAP.items():
                    if key in name or name in key:
                        chain = val
                        break
            if chain:
                chain_groups[chain].append((name, node))
            else:
                unknown.append((name, node))

        merged_pairs = []
        merged_themes = {}
        for chain, members in chain_groups.items():
            if len(members) == 1:
                name, node = members[0]
                # V10.3: 即使只有1个成员，也重命名为产业链名（如"电子"→"半导体产业链"）
                if name != chain:
                    merged_pairs.append({"main": chain, "sub": name, "type": "semantic_rename"})
                    node.name = chain
                    merged_themes[chain] = node
                    print(f"    [语义重命名] {name} -> {chain}")
                else:
                    merged_themes[name] = node
                continue
            members.sort(key=lambda x: x[1].score, reverse=True)
            main_name, main_node = members[0]
            for sub_name, sub_node in members[1:]:
                main_node.stocks.update(sub_node.stocks)
                main_node.stock_details.update(sub_node.stock_details)
                main_node.zt_count += sub_node.zt_count
                main_node.strong_stock_count += sub_node.strong_stock_count
                main_node.sub_themes.append(sub_name)
                ml = min(len(main_node.capital_flow), len(sub_node.capital_flow))
                for i in range(ml):
                    main_node.capital_flow[i] = (main_node.capital_flow[i] + sub_node.capital_flow[i]) / 2
                merged_pairs.append({"main": chain, "sub": sub_name, "type": "semantic"})
            main_node.name = chain
            merged_themes[chain] = main_node
            if len(members) > 1:
                print(f"    [语义合并] {chain}: 合并 {len(members)} 个子主题")
        for name, node in unknown:
            merged_themes[name] = node
        return merged_themes, merged_pairs

    def _overlap_merge(self, themes: Dict[str, ThemeNode]) -> Tuple[Dict[str, ThemeNode], List[dict]]:
        names = list(themes.keys())
        merged_pairs = []
        merged_names = set()
        for i in range(len(names)):
            if names[i] in merged_names:
                continue
            for j in range(i + 1, len(names)):
                if names[j] in merged_names:
                    continue
                a, b = themes[names[i]], themes[names[j]]
                if not a.stocks or not b.stocks:
                    continue
                overlap = a.stocks & b.stocks
                overlap_ratio = len(overlap) / min(len(a.stocks), len(b.stocks))
                if overlap_ratio >= CONFIG["MERGE_OVERLAP_RATIO"]:
                    if a.score >= b.score:
                        a.stocks.update(b.stocks)
                        a.stock_details.update(b.stock_details)
                        a.zt_count += b.zt_count
                        a.sub_themes.append(names[j])
                        merged_names.add(names[j])
                        merged_pairs.append({"main": names[i], "sub": names[j], "type": "overlap"})
                    else:
                        b.stocks.update(a.stocks)
                        b.stock_details.update(a.stock_details)
                        b.zt_count += a.zt_count
                        b.sub_themes.append(names[i])
                        merged_names.add(names[i])
                        merged_pairs.append({"main": names[j], "sub": names[i], "type": "overlap"})
        result = {n: t for n, t in themes.items() if n not in merged_names}
        return result, merged_pairs

    def _decompose_mainlines(self, themes: Dict[str, ThemeNode]) -> Tuple[Dict[str, ThemeNode], List[dict]]:
        """V10.4: 主线自动拆解
        条件：>15只股票 + ≥2子链 + 资金方向不一致 → 拆分为独立主题
        """
        decomposed = []
        new_themes = {}
        for name, node in themes.items():
            stock_count = len(node.stocks)
            if stock_count < CONFIG["DECOMPOSE_MIN_STOCKS"]:
                new_themes[name] = node
                continue
            if len(node.sub_themes) < CONFIG["DECOMPOSE_MIN_SUB_CHAINS"]:
                new_themes[name] = node
                continue
            # 检查资金方向是否一致
            if node.capital_trend in ("inflow", "outflow") and len(node.capital_flow) >= 3:
                # 资金方向一致的不拆
                new_themes[name] = node
                continue
            # 将子链拆为独立主题
            sub_list = node.sub_themes[:]
            if not sub_list:
                new_themes[name] = node
                continue
            # 将子链拆为独立主题（使用"父链_子链"命名避免噪声过滤）
            split_count = min(len(sub_list), 4)
            chunk_size = max(1, stock_count // split_count)
            all_stocks = list(node.stock_details.items())
            import random
            random.shuffle(all_stocks)
            for i, sub_name in enumerate(sub_list[:split_count]):
                child_name = f"{name}_{sub_name}" if not sub_name.startswith(name) else sub_name
                child_node = ThemeNode(child_name, node.birth_date)
                chunk = all_stocks[i * chunk_size:(i + 1) * chunk_size]
                child_node.stocks = set(c[0] for c in chunk)
                child_node.stock_details = dict(chunk)
                child_node.zt_count = max(0, node.zt_count // split_count)
                child_node.strong_stock_count = max(0, node.strong_stock_count // split_count)
                child_node.parent_theme = name
                child_node.stage = node.stage
                child_node.sub_themes = []
                new_themes[child_node.name] = child_node
                print(f"    [拆解] {name} -> {child_node.name} ({len(child_node.stocks)}只)")
            decomposed.append({
                "original": name,
                "children": sub_list[:split_count],
                "stock_count": stock_count
            })
        return new_themes, decomposed

    def _calculate_leader_score(self, node: ThemeNode, stock_detail: dict) -> float:
        """V10.4: LeaderScore = 0.4×资金强度 + 0.3×行业地位 + 0.2×结构位置 + 0.1×市场共识度"""
        pct = float(stock_detail.get("pct_chg", 0))
        amount = float(stock_detail.get("amount", 0))
        code = stock_detail.get("code", "")
        name = stock_detail.get("name", "")

        # 资金强度 (0.4)
        fund_score = min(max(pct / 10, 0), 1.0) * 0.6
        if amount > 1e9:  # 成交额>10亿
            fund_score += 0.4
        elif amount > 5e8:
            fund_score += 0.2
        fund_score = min(fund_score, 1.0)

        # 行业地位 (0.3) - 用成交额排名近似
        position_score = min(amount / 5e9, 0.6)
        if name in node.leader_stocks:
            position_score += 0.4
        position_score = min(position_score, 1.0)

        # 结构位置 (0.2) - 涨停加速、产业链核心
        struct_score = 0.5
        if pct >= 9.5:
            struct_score += 0.3
        if amount > 1e9:
            struct_score += 0.2
        struct_score = min(struct_score, 1.0)

        # 市场共识度 (0.1)
        consensus_score = 0.5
        if name in node.leader_stocks:
            consensus_score += 0.3
        if pct >= 5:
            consensus_score += 0.2
        consensus_score = min(consensus_score, 1.0)

        return 0.4 * fund_score + 0.3 * position_score + 0.2 * struct_score + 0.1 * consensus_score

    def _detect_rotation(self, mainlines: List[dict]) -> dict:
        """V10.4: 资金切换检测
        信号：龙头断板 / 新子链放量 / 资金从旧链流出
        """
        if len(mainlines) < 2:
            return {"signal": "无切换", "from": "", "to": "", "probability": 0}

        top = mainlines[0]
        second = mainlines[1]

        rotation_prob = 0
        signals = []

        # 信号1：龙头断板（龙头股涨幅不足5%或下跌）
        leader_pct = 0
        if top.get("_leader_pct"):
            leader_pct = top["_leader_pct"]
        if leader_pct < 5:
            rotation_prob += 20
            signals.append("龙头走弱")

        # 信号2：评分差距缩小
        score_gap = top["score"] - second["score"]
        if score_gap < 10:
            rotation_prob += 30
            signals.append("评分差距缩小")
        elif score_gap < 20:
            rotation_prob += 15
            signals.append("评分接近")

        # 信号3：资金从旧链流出
        if top["capital_flow"]["trend"] in ("分歧", "流出"):
            rotation_prob += 25
            signals.append("主线资金分歧")

        # 信号4：新主线资金流入
        if second["capital_flow"]["trend"] == "流入":
            rotation_prob += 20
            signals.append("次主线资金流入")

        rotation_prob = min(rotation_prob, 95)

        if rotation_prob >= 50:
            signal = "可能切换"
        elif rotation_prob >= 30:
            signal = "关注切换"
        else:
            signal = "主线稳定"

        return {
            "signal": signal,
            "from": top["name"],
            "to": second["name"],
            "probability": rotation_prob,
            "signals": signals
        }

    def _build_trade_signal(self, mainlines: List[dict], rotation: dict) -> dict:
        """V10.4: 交易信号生成"""
        if not mainlines:
            return {"action": "exit", "target": "", "reason": "无主线，空仓观望"}

        top = mainlines[0]
        rot_prob = rotation.get("probability", 0)

        if top["score"] >= 70 and rot_prob < 30:
            return {
                "action": "buy",
                "target": top["name"],
                "reason": f"核心主线{top['name']}强势，龙头{top['leader']}可参与"
            }
        elif top["score"] >= 50 and rot_prob < 50:
            return {
                "action": "hold",
                "target": top["name"],
                "reason": f"{top['name']}主线明确，持有中军等待轮动"
            }
        elif rot_prob >= 50:
            to_name = rotation.get("to", "")
            return {
                "action": "reduce",
                "target": to_name,
                "reason": f"切换信号({rot_prob}%)：{rotation.get('from','')}→{to_name}，减仓观察"
            }
        else:
            return {
                "action": "reduce",
                "target": "",
                "reason": f"市场快速轮动({top['score']}分)，控制仓位"
            }

    def _identify_mainlines(self, themes: Dict[str, ThemeNode]) -> List[dict]:
        """V10.3: 主线识别 - 0.55×CapitalFlow + 0.30×Structure + 0.15×Lifecycle
        强制输出3~5条主线，禁止输出泛主题
        """
        scored = []
        for name, node in themes.items():
            stock_count = len(node.stocks)
            has_leader = len(node.leader_stocks) > 0
            if stock_count < 5:
                continue

            # 1️⃣ CapitalFlowScore (0.55)
            if node.capital_trend == "inflow":
                cf_score = 0.75 + min(len(node.capital_flow) * 0.05, 0.25)
            elif node.capital_trend == "neutral":
                cf_score = 0.55
            else:
                cf_score = 0.35
            zt_bonus = min(node.zt_count * 0.04, 0.25)
            cf_score = cf_score * 0.70 + zt_bonus * 0.30
            if node.capital_streak >= 3:
                cf_score = min(cf_score + 0.10, 1.0)
            elif node.capital_streak >= 2:
                cf_score = min(cf_score + 0.05, 1.0)
            cf_score = min(cf_score, 1.0)

            # 2️⃣ StructureScore (0.30)
            struct_score = min(stock_count / 20, 0.40)
            if has_leader:
                struct_score += 0.20
            if len(node.sub_themes) >= 2:
                struct_score += 0.25
            elif len(node.sub_themes) >= 1:
                struct_score += 0.15
            if node.strong_stock_count >= 5:
                struct_score += 0.15
            elif node.strong_stock_count >= 2:
                struct_score += 0.08
            struct_score = min(struct_score, 1.0)

            # 3️⃣ MomentumScore (0.15)
            mom_score = min(node.zt_count * 0.08, 0.40)
            mom_score += min(node.strong_stock_count * 0.04, 0.20)
            stage_scores = {"BIRTH": 0.20, "GROWTH": 0.35, "MAINLINE": 0.40, "FADING": 0.20, "DEATH": 0.05}
            mom_score += stage_scores.get(node.stage, 0.15)
            cap_days = sum(1 for f in node.capital_flow[-5:] if f > 0) if node.capital_flow else 0
            mom_score += min(cap_days * 0.04, 0.15)
            mom_score = min(mom_score, 1.0)

            # MainlineScore = 0.55×CF + 0.30×Struct + 0.15×Momentum
            main_score = 0.55 * cf_score + 0.30 * struct_score + 0.15 * mom_score
            floor_score = min(stock_count * 2.5, 35) / 100
            main_score = max(main_score, floor_score)
            main_score_100 = round(main_score * 100, 1)

            if main_score_100 >= 80:
                level = "超级主线"
            elif main_score_100 >= 70:
                level = "强主线"
            elif main_score_100 >= 60:
                level = "轮动主线"
            elif main_score_100 >= 45:
                level = "边缘主线"
            else:
                level = "淘汰"

            all_stocks = list(node.stock_details.values())
            all_stocks.sort(key=lambda x: float(x.get("pct_chg", 0)), reverse=True)
            # V10.4: leader基于LeaderScore选择（唯一龙头）
            leader_name = ""
            leader_score_val = 0
            leader_pct = 0
            best_ls = -1
            for s in all_stocks:
                ls = self._calculate_leader_score(node, s)
                if ls > best_ls:
                    best_ls = ls
                    leader_name = s.get("name", "")
                    leader_pct = float(s.get("pct_chg", 0))
                    leader_score_val = round(ls * 100, 1)

            # 中军：涨幅第2~5名
            cores = [s.get("name", "") for s in all_stocks[1:5] if s.get("name") and s.get("name") != leader_name][:4]
            # 补涨：末尾5只
            laggings = [s.get("name", "") for s in all_stocks[-5:] if s.get("name") and s.get("name") != leader_name] if len(all_stocks) > 6 else []

            # 资金状态
            if node.capital_trend == "inflow":
                cap_trend = "流入"
                cap_strength = min(cf_score * 100, 100)
            elif node.capital_trend == "outflow":
                cap_trend = "流出"
                cap_strength = max((1 - cf_score) * 100, 0)
            else:
                cap_trend = "分歧"
                cap_strength = 50

            scored.append({
                "name": name,
                "score": main_score_100,
                "leader": leader_name,
                "leader_score": leader_score_val,
                "stage": node.stage,
                "logic": self._infer_main_logic(name, node),
                "core_stocks": cores,
                "lagging_stocks": laggings,
                "capital_flow": {"trend": cap_trend, "strength": round(cap_strength, 1)},
                "next_rotation": self._predict_rotation(name, node),
                "_stock_count": stock_count,
                "_zt_count": node.zt_count,
                "_strong_count": node.strong_stock_count,
                "_sub_count": len(node.sub_themes),
                "_cf_score": round(cf_score * 100, 1),
                "_struct_score": round(struct_score * 100, 1),
                "_mom_score": round(mom_score * 100, 1),
                "_cap_streak": node.capital_streak,
                "_leader_pct": leader_pct,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        print(f"    [评分V10.4] {len(scored)} 个候选: " +
              ", ".join([f"{s['name']}({s['score']}|CF{s['_cf_score']}+ST{s['_struct_score']}+MO{s['_mom_score']})" for s in scored[:5]]))

        # V10.4: 强制压缩为1~3条
        mainlines = scored[:3]
        if len(mainlines) < 1 and len(scored) > 0:
            mainlines = [scored[0]]
            print(f"    [强制拉满] 取 1 条")
        # 从30分以上补充至1条
        if len(mainlines) < 1:
            supplements = [s for s in scored if s["score"] >= 25] if scored else []
            if supplements:
                mainlines = supplements[:1]
                print(f"    [强制补充] 补充至1条")

        return mainlines[:3]

    def _infer_main_logic(self, name: str, node: ThemeNode) -> str:
        chain = SEMANTIC_CHAIN_MAP.get(name, "")
        if chain:
            return f"{chain}资金汇聚" if chain.endswith("产业链") else f"{chain}产业链资金汇聚"
        # 如果name本身就是产业链名（语义合并后的chain name）
        if name in SEMANTIC_CHAIN_MAP.values():
            return f"{name}资金汇聚" if name.endswith("产业链") else f"{name}产业链资金汇聚"
        industries = list(node.industry_tags)[:3] if node.industry_tags else []
        concepts = list(node.concept_tags)[:3] if node.concept_tags else []
        tags = industries + concepts
        if tags:
            return f"{'/'.join(tags[:3])}资金驱动"
        return "资金聚类"

    def _predict_rotation(self, name: str, node: ThemeNode) -> str:
        chain_map = {
            "AI算力基础设施": "AI应用与终端", "AI应用与终端": "AI算力基础设施",
            "半导体产业链": "AI算力基础设施", "新能源电力": "新能源汽车",
            "新能源汽车": "新能源电力", "资源周期": "新能源电力",
            "金融": "消费", "消费": "金融",
            "军工产业链": "商业航天", "商业航天": "低空经济",
            "低空经济": "商业航天", "物理AI与机器人": "AI算力基础设施",
            "生物医药": "消费",
        }
        chain = SEMANTIC_CHAIN_MAP.get(name, "")
        if not chain:
            # 如果name本身就是产业链名
            if name in SEMANTIC_CHAIN_MAP.values():
                chain = name
        if chain and chain in chain_map:
            return f"可能切向{chain_map[chain]}"
        return "暂未明确"

    def _build_capital_flow_graph(self, mainlines: List[dict]) -> List[dict]:
        """V10.3: 构建资金路径图（Capital Flow Path）"""
        edges = []
        if len(mainlines) < 2:
            return edges
        for i in range(len(mainlines) - 1):
            cur = mainlines[i]
            nxt = mainlines[i + 1]
            # 边缘权重：基于评分差和资金趋势
            score_gap = cur["score"] - nxt["score"]
            weight = max(10, min(100, int(50 + score_gap)))
            trend_signal = 1 if cur["capital_flow"]["trend"] in ("流入",) else 0
            edges.append({
                "from": cur["name"],
                "to": nxt["name"],
                "weight": weight,
                "signal": "流向" if trend_signal else "轮动"
            })
        # 子链扩散（基于子链数量）
        for m in mainlines:
            sc = m.get("_sub_count", 0)
            if sc >= 2:
                edges.append({
                    "from": m["name"],
                    "to": f"{m['name']}_SubChain",
                    "weight": 60,
                    "signal": "扩散"
                })
        return edges

    def _build_market_conclusion(self, mainlines: List[dict]) -> dict:
        """V10.3: 市场结论与交易决策"""
        if not mainlines:
            return {
                "dominant_mainline": "无",
                "rotation_candidates": [],
                "trade_direction_next_1_3_days": "市场无主线，建议观望"
            }
        top = mainlines[0]
        dominant = top["name"]
        # 轮动候选：评分差异在15分以内的下游主线
        candidates = []
        for m in mainlines[1:]:
            if top["score"] - m["score"] <= 20:
                candidates.append(m["name"])
        # 交易方向判断
        if top["score"] >= 70:
            direction = f"聚焦{dominant}，{top['leader']}为龙头"
        elif top["score"] >= 50:
            candidates_str = "、".join(candidates[:2]) if candidates else "无"
            direction = f"关注轮动方向：{candidates_str}，低吸{dominant}中军"
        else:
            direction = "市场快速轮动，控制仓位，仅参与评分最高的主线低吸"
        return {
            "dominant_mainline": dominant,
            "rotation_candidates": candidates[:3],
            "trade_direction_next_1_3_days": direction
        }


# ============================================================
# 每日主题演化引擎（主控）
# ============================================================
class ThemeEvolutionEngine:
    def __init__(self, trade_date: str = None):
        self.trade_date = trade_date or get_last_trade_date()
        self.dl = DataLoader(self.trade_date)
        self.cluster_engine = None
        self.lifecycle = LifecycleManager()
        self.merge_engine = None
        self.split_engine = None
        self.score_engine = ScoreEngine()
        self.graph_builder = None
        self.compressor = None
        self.new_themes = []
        self.merged_themes = []
        self.split_themes = []
        self.dead_themes = []
        self.mainlines = []
        self.noise_removed = []
        self.merged_pairs = []
        self.capital_flow_graph = []
        self.market_conclusion = {}
        self.rotation_signal = {}
        self.trade_signal = {}

    def run(self):
        print(f"\n{'='*60}")
        print(f" 实盘级主线拆解 + 龙头唯一化 + 资金切换决策引擎 V10.4")
        print(f" 交易日: {self.trade_date}")
        print(f"{'='*60}\n")

        print("[Step 1] 加载市场数据")
        self.dl.load_all()
        print()

        print("[Step 2] 获取强势股 & 成交额Top")
        top_gainers = self.dl.get_top_gainers(CONFIG["CLUSTER_TOP_N_GAINERS"])
        top_amount = self.dl.get_top_amount(CONFIG["CLUSTER_TOP_N_AMOUNT"])
        print(f"  强势股: {len(top_gainers)}, 成交额Top: {len(top_amount)}")
        print()

        print("[Step 3] 资金聚类分析")
        self.cluster_engine = CapitalClusterEngine(self.dl)
        clusters = self.cluster_engine.cluster(top_gainers, top_amount)
        print(f"  发现 {len(clusters)} 个潜在聚类")
        print()

        print("[Step 4] 处理新主题")
        self._process_new_themes(clusters)
        print()

        print("[Step 5] 更新现有主题生命周期")
        self._update_existing_themes(top_gainers, top_amount)
        print()

        print("[Step 6] 主题合并检测")
        self.merge_engine = MergeEngine(active_themes)
        self._process_merges()
        print()

        print("[Step 7] 主题拆分检测")
        self.split_engine = SplitEngine(active_themes)
        self._process_splits()
        print()

        print("[Step 8] 主题评分")
        self._score_all_themes()
        print()

        print("[Step 8.5] 主线收敛 + 龙头唯一化 + 切换检测 (V10.4)")
        self.compressor = MainlineCompressionEngine(active_themes)
        result_tuple = self.compressor.run()
        self.mainlines = result_tuple[0]
        self.noise_removed = result_tuple[1]
        self.merged_pairs = result_tuple[2]
        self.capital_flow_graph = result_tuple[3] if len(result_tuple) > 3 else []
        self.market_conclusion = result_tuple[4] if len(result_tuple) > 4 else {}
        self.rotation_signal = result_tuple[5] if len(result_tuple) > 5 else {}
        self.trade_signal = result_tuple[6] if len(result_tuple) > 6 else {}
        print()

        print("[Step 9] 构建主题关系图")
        self.graph_builder = ThemeGraphBuilder(active_themes)
        graph = self.graph_builder.build_graph()
        print(f"  生成 {len(graph)} 条资金流动边")
        print()

        print("[Step 10] 输出结果")
        result = self._build_mainline_output(graph)
        self._save_mainline_output(result)
        print()

        print(f"{'='*60}")
        print(f" 完成! 主线: {len(self.mainlines)}, "
              f"新增主题: {len(self.new_themes)}, "
              f"去噪: {len(self.noise_removed)}, "
              f"合并: {len(self.merged_pairs)}, "
              f"消亡: {len(self.dead_themes)}")
        if self.mainlines:
            print(f" 最强龙头: {self.mainlines[0].get('leader','无')} "
                  f"(评分: {self.mainlines[0].get('leader_score',0)})")
        print(f" 切换信号: {self.rotation_signal.get('signal','无')} "
              f"({self.rotation_signal.get('probability',0)}%)")
        print(f" 交易信号: {self.trade_signal.get('action','hold')} -> "
              f"{self.trade_signal.get('target','观望')}")
        print(f"{'='*60}")

        return result

    def _process_new_themes(self, clusters: List[dict]):
        self.new_themes = []
        for cluster in clusters:
            theme_name = cluster["theme_name"]
            if self._is_already_covered(theme_name, cluster):
                self._merge_into_existing(theme_name, cluster)
                continue
            if theme_name in active_themes:
                theme_name = f"{theme_name}_{len(active_themes)}"
            node = ThemeNode(theme_name, self.trade_date)
            node.stocks = set(cluster["stocks"])
            node.stock_details = {s["code"]: s for s in cluster["stock_details"]}
            node.volume_ratio = cluster["total_amount"] / max(sum(s["amount"] for s in cluster["stock_details"]), 1)
            zt_count = sum(1 for s in cluster["stock_details"] if s.get("pct_chg", 0) >= 9.5)
            strong_count = sum(1 for s in cluster["stock_details"] if s.get("pct_chg", 0) >= 5)
            node.zt_count = zt_count
            node.strong_stock_count = strong_count
            node.industry_tags = set(cluster["common_industries"])
            node.concept_tags = set(cluster["top_concepts"])
            sorted_stocks = sorted(cluster["stock_details"],
                                   key=lambda x: (x.get("pct_chg", 0), x.get("amount", 0)), reverse=True)
            node.leader_stocks = [s["name"] for s in sorted_stocks[:3] if s.get("name")]
            active_themes[theme_name] = node
            self.new_themes.append(node.to_dict())
            for code in cluster["stocks"]:
                stock_theme_map[code].add(theme_name)
            print(f"  [BIRTH] 新主题: {theme_name} | 股票{len(node.stocks)}只 | "
                  f"涨停{zt_count} | 龙头{node.leader_stocks[:2]}")

    def _is_already_covered(self, theme_name: str, cluster: dict) -> bool:
        cluster_stocks = set(cluster["stocks"])
        for tname, tnode in active_themes.items():
            if tnode.status == "Dead":
                continue
            overlap = cluster_stocks & tnode.stocks
            if len(overlap) / len(cluster_stocks) >= CONFIG["MERGE_OVERLAP_RATIO"]:
                return True
        return False

    def _merge_into_existing(self, theme_name: str, cluster: dict):
        cluster_stocks = set(cluster["stocks"])
        best_match, best_overlap = None, 0
        for tname, tnode in active_themes.items():
            if tnode.status == "Dead":
                continue
            overlap = cluster_stocks & tnode.stocks
            ratio = len(overlap) / len(cluster_stocks)
            if ratio > best_overlap:
                best_overlap, best_match = ratio, tname
        if best_match:
            node = active_themes[best_match]
            old_count = len(node.stocks)
            node.stocks.update(cluster_stocks)
            for s in cluster["stock_details"]:
                if s["code"] not in node.stock_details:
                    node.stock_details[s["code"]] = s
            for code in cluster_stocks:
                stock_theme_map[code].add(best_match)
            print(f"  [MERGE_INTO] {theme_name} -> {best_match} ({old_count} -> {len(node.stocks)})")

    def _update_existing_themes(self, top_gainers: pd.DataFrame, top_amount: pd.DataFrame):
        for name, node in active_themes.items():
            if node.status == "Dead":
                continue
            today_data = self._calc_today_data(node, top_gainers, top_amount)
            today_data["date"] = self.trade_date
            old_stage = node.stage
            new_stage = self.lifecycle.update_theme(node, today_data)
            if old_stage != new_stage:
                print(f"  [LIFECYCLE] {name}: {old_stage} -> {new_stage}")
            if new_stage == "DEATH" and old_stage != "DEATH":
                self.dead_themes.append(node.to_dict())
                print(f"  [DEATH] 主题消亡: {name}")

    def _calc_today_data(self, node: ThemeNode, top_gainers: pd.DataFrame, top_amount: pd.DataFrame) -> dict:
        zt_count = 0
        strong_count = 0
        total_pct = 0.0
        total_amount = 0.0
        stock_count = len(node.stocks)
        for code in node.stocks:
            if top_gainers is not None and not top_gainers.empty:
                g = top_gainers[top_gainers["ts_code"] == code]
                if not g.empty:
                    pct = float(g.iloc[0].get("pct_chg", 0))
                    amt = float(g.iloc[0].get("amount", 0))
                    if pct >= 9.5: zt_count += 1
                    if pct >= 5: strong_count += 1
                    total_pct += pct
                    total_amount += amt
            if top_amount is not None and not top_amount.empty:
                a = top_amount[top_amount["ts_code"] == code]
                if not a.empty:
                    amt = float(a.iloc[0].get("amount", 0))
                    if amt > total_amount: total_amount = amt
        avg_pct = total_pct / max(stock_count, 1)
        net_flow = avg_pct * (total_amount / 1e8) if total_amount > 0 else 0
        volume_ratio = 1.0
        if top_amount is not None and not top_amount.empty:
            top_codes = set(top_amount.head(10)["ts_code"].tolist())
            in_top10 = sum(1 for code in node.stocks if code in top_codes)
            if in_top10 >= 2: volume_ratio = 1.5 + in_top10 * 0.1
            elif in_top10 >= 1: volume_ratio = 1.2
        return {"net_flow": net_flow, "zt_count": zt_count,
                "strong_count": strong_count, "volume_ratio": volume_ratio}

    def _process_merges(self):
        candidates = self.merge_engine.find_merge_candidates()
        processed = set()
        for ta, tb, ratio in candidates:
            if ta in processed or tb in processed:
                continue
            self.merge_engine.execute_merge(ta, tb)
            self.merged_themes.append({"main": ta, "weak": tb, "overlap_ratio": round(ratio, 2)})
            processed.add(ta)
            processed.add(tb)

    def _process_splits(self):
        candidates = self.split_engine.find_split_candidates()
        for name in candidates:
            children = self.split_engine.execute_split(name)
            self.split_themes.append({"original": name, "children": [c.name for c in children]})

    def _score_all_themes(self):
        for name, node in active_themes.items():
            if node.status == "Dead":
                continue
            self.score_engine.calc_total_score(node)

    def _get_active(self) -> Dict[str, ThemeNode]:
        return {n: t for n, t in active_themes.items() if t.status == "Active"}

    def _build_mainline_output(self, graph: List[dict]) -> dict:
        """V10.4: 输出格式 - 包含市场状态/主线/切换/交易信号"""
        rs = self.rotation_signal if hasattr(self, 'rotation_signal') else {}
        ts = self.trade_signal if hasattr(self, 'trade_signal') else {}

        # 市场状态
        if self.mainlines:
            top = self.mainlines[0]
            if top["score"] >= 80: regime = "主升"
            elif top["score"] >= 65: regime = "分歧"
            elif top["score"] >= 50: regime = "轮动"
            else: regime = "试错"
        else:
            regime = "退潮"

        return {
            "date": self.trade_date,
            "market_state": {
                "regime": regime,
                "mainline_count": len(self.mainlines),
                "rotation_signal": rs.get("signal", "无"),
            },
            "mainlines": self.mainlines,
            "rotation": {
                "from": rs.get("from", ""),
                "to": rs.get("to", ""),
                "probability": rs.get("probability", 0),
            },
            "trade_signal": {
                "action": ts.get("action", "hold"),
                "target": ts.get("target", ""),
                "reason": ts.get("reason", ""),
            },
            "capital_flow_graph": self.capital_flow_graph if hasattr(self, 'capital_flow_graph') else [],
            "market_conclusion": {
                "dominant_mainline": self.market_conclusion.get("dominant_mainline", "无") if hasattr(self, 'market_conclusion') else "无",
                "rotation_candidates": self.market_conclusion.get("rotation_candidates", []) if hasattr(self, 'market_conclusion') else [],
                "trade_direction_next_1_3_days": self.market_conclusion.get("trade_direction_next_1_3_days", "观望") if hasattr(self, 'market_conclusion') else "观望",
            },
            "new_themes": self.new_themes,
            "dead_themes": self.dead_themes,
            "active_theme_graph": graph,
            "stats": {
                "total_raw_themes": len(active_themes),
                "mainline_count": len(self.mainlines),
                "noise_removed_count": len(self.noise_removed),
                "merged_count": len(self.merged_pairs),
                "new_count": len(self.new_themes),
                "dead_count": len(self.dead_themes),
            }
        }

    def _save_mainline_output(self, result: dict):
        json_path = os.path.join(REPORT_DIR, f"theme_mainline_{self.trade_date}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[Save] JSON: {json_path}")

        txt_path = os.path.join(REPORT_DIR, f"theme_mainline_{self.trade_date}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(self._format_mainline_report(result))
        print(f"[Save] 报告: {txt_path}")

        for name, node in active_themes.items():
            history_themes[name] = node

    def _format_mainline_report(self, result: dict) -> str:
        lines = []
        lines.append("=" * 70)
        lines.append(f"  实盘级主线拆解 + 龙头唯一化 + 资金切换决策引擎 V10.4")
        lines.append(f"  报告日期: {result.get('date', '未知')}")
        lines.append("=" * 70)
        lines.append("")

        s = result.get("stats", {})
        ms = result.get("market_state", {})
        lines.append("【概览】")
        lines.append(f"  市场状态: {ms.get('regime','未知')}")
        lines.append(f"  主线数量: {ms.get('mainline_count',0)} 条 (目标: 1~3条)")
        if s:
            lines.append(f"  去噪删除: {s.get('noise_removed_count',0)} 个 | "
                         f"合并主题: {s.get('merged_count',0)} 组")
            lines.append(f"  新增主题: {s.get('new_count',0)} 个 | "
                         f"消亡主题: {s.get('dead_count',0)} 个")
        lines.append("")

        if result.get("mainlines"):
            lines.append("=" * 70)
            lines.append("  市场主线结构（V10.4 拆解 + 龙头唯一化）")
            lines.append("=" * 70)
            lines.append("")
            for i, m in enumerate(result["mainlines"], 1):
                lines.append(f"--- {i}. {m['name']} ---")
                lines.append(f"    评分: {m['score']} | 阶段: {m['stage']} | "
                             f"龙头: {m.get('leader','无')} (评分: {m.get('leader_score',0)})")
                lines.append(f"    核心逻辑: {m['logic']}")
                lines.append(f"    资金趋势: {m['capital_flow']['trend']} (强度: {m['capital_flow']['strength']})")
                lines.append(f"    股票: {m['_stock_count']}只 | 涨停: {m['_zt_count']} | 强势: {m['_strong_count']}")
                lines.append(f"    子主题: {m['_sub_count']}个 | 连涨: {m.get('_cap_streak',0)}天")
                if m.get("core_stocks"):
                    lines.append(f"    中军: {', '.join(m['core_stocks'][:4])}")
                if m.get("lagging_stocks"):
                    lines.append(f"    补涨: {', '.join(m['lagging_stocks'][:3])}")
                lines.append(f"    轮动指向: {m.get('next_rotation','暂未明确')}")
                lines.append("")
            lines.append("=" * 70)
            lines.append("  评分构成: 0.55×资金(CF) + 0.30×结构(ST) + 0.15×动量(MO)")
            lines.append("  龙头评分: 0.4×资金强度 + 0.3×行业地位 + 0.2×结构位置 + 0.1×市场共识")
            lines.append("=" * 70)
            lines.append("")

        # 切换信号
        rot = result.get("rotation", {})
        lines.append("【资金切换信号】")
        lines.append(f"  切换方向: {rot.get('from','')} → {rot.get('to','')}")
        lines.append(f"  切换概率: {rot.get('probability',0)}%")
        lines.append(f"  市场状态: {ms.get('rotation_signal','无')}")
        lines.append("")

        # 交易信号
        ts = result.get("trade_signal", {})
        lines.append("【交易信号】")
        lines.append(f"  操作: {ts.get('action','hold').upper()}")
        lines.append(f"  目标: {ts.get('target','观望')}")
        lines.append(f"  理由: {ts.get('reason','')}")
        lines.append("")

        # 资金路径图
        cfg = result.get("capital_flow_graph", [])
        if cfg:
            lines.append("【资金路径图 (Capital Flow Path)】")
            for e in cfg[:8]:
                lines.append(f"  {e['from']} → {e['to']} [{e.get('signal','?')}, 权重:{e.get('weight','?')}]")
            lines.append("")

        if result.get("new_themes"):
            lines.append("【新增主题】")
            for t in result["new_themes"]:
                lines.append(f"  [NEW] {t['theme']} | 股票{t['stock_count']}只 | "
                             f"涨停{t['zt_count']} | 龙头{t.get('leader_stocks', [])[:2]}")
            lines.append("")
        if result.get("dead_themes"):
            lines.append("【消亡主题】")
            for t in result["dead_themes"]:
                lines.append(f"  [DEAD] {t['theme']} | 评分:{t['score']} | 阶段:{t.get('stage','?')}")
            lines.append("")
        return "\n".join(lines)