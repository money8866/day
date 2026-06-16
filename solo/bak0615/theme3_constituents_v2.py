#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
theme3_constituents_v2.py
=========================
针对 theme3.json 中所有二级主题（themes）生成成分股列表。

- 主题配置来源：theme3.json（13 个一级目录 / 共 70 个子主题，V2 字段）
- 东财板块接口 + 缓存：复用 theme_trend_sentiment_score.py 的
    * get_dc_members()  -> 调 Tushare dc_index/dc_member 拉东财板块成分股
    * match_theme_stocks() -> 按 industry / concept / keywords 匹配
    * SQLite 缓存 (cache_backbone_tushare/cache.db)
- V2 -> 旧字段桥接：自动把 theme3.json 的 V2 字段映射成
    industry / concept / keywords / exclude_keywords / core_companies / leader_companies

输出：
  cache_backbone_tushare/theme3_constituents_{TRADE_DATE}.json  -> 全量 JSON
  cache_backbone_tushare/theme3_constituents_{TRADE_DATE}.csv   -> 扁平 CSV（便于 Excel 查看）

使用： python theme3_constituents_v2.py
"""
import os
import sys
import json
import csv
import time
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 1) 复用 tushare 量化模块中的工具函数（东财接口 + 缓存）
import theme_trend_sentiment_score as tts

TRADE_DATE = tts.TRADE_DATE
CACHE_DIR = tts.CACHE_DIR

THEME3_PATH = os.path.join(BASE_DIR, "theme3.json")

# =====================================================================
# V2 字段 -> 旧字段（供 match_theme_stocks 用）的桥接函数
#
# theme3.json 子主题字段：
#   theme_name, version, theme_type, core_semantic[],
#   industry_roles{}, business_dna_tags[], weak_positive_tags[],
#   negative_pressure_tags{}, industry_soft_constraints{},
#   stock_role_mapping{}, matching_strategy{}
#
# match_theme_stocks 需要的旧字段：
#   industry[], concept[], keywords[], exclude_keywords[],
#   core_companies[], leader_companies[]
# =====================================================================

def build_v2_bridge_factory(dc_df):
    """基于 dc_df 构建一个桥接函数：运行时把 V2 字段映射成东财板块名。

    theme3.json 中不保存 industry/concept/keywords 等匹配字段，
    全部在这里动态生成。
    """
    # 所有东财板块名（分行业 / 概念）——标准化为不带罗马数字的形式
    INDUSTRY_BOARDS = set()
    CONCEPT_BOARDS = set()
    for is_industry, grp in [(True, dc_df[dc_df["is_industry"] == 1]),
                             (False, dc_df[dc_df["is_industry"] == 0])]:
        names = set(grp["concept_name"].astype(str).tolist())
        if is_industry:
            INDUSTRY_BOARDS = names
        else:
            CONCEPT_BOARDS = names

    def _norm(s):
        return s.replace("Ⅱ", "").replace("Ⅲ", "").strip()

    IND_NORM = {_norm(n): n for n in INDUSTRY_BOARDS}
    CON_NORM = {_norm(n): n for n in CONCEPT_BOARDS}

    # 噪声词：在多个主题中出现但不代表真实主题归属的词
    INDUSTRY_NOISE = {"半导体", "消费电子", "医药", "白酒", "煤炭", "新能源", "军工", "电子", "房地产", "银行", "保险", "券商"}
    CONCEPT_NOISE = {"消费电子", "新能源车", "新能源", "医药医疗风格"}

    def _match_boards(tokens, boards_norm, noise_set, topn, min_len=2):
        """给 token 集合打分，挑出最像东财板块名的 N 个。"""
        scored = []
        for board_norm, board_orig in boards_norm.items():
            score = 0
            reasons = []
            for tok in tokens:
                tn = _norm(tok)
                if not tn or len(tn) < 2:
                    continue
                if tn == board_norm:
                    score += 100
                    reasons.append(f"={tn}")
                elif len(tn) >= 2 and tn in board_norm:
                    bonus = 10 + len(tn)
                    if tn in noise_set:
                        bonus = max(2, bonus // 4)
                    score += bonus
                    reasons.append(f"<{tn}>")
                elif len(board_norm) >= 3 and board_norm in tn:
                    score += 4
                    reasons.append(f">{board_norm}")
            if score > 0:
                scored.append((board_orig, score))
        scored.sort(key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))

        picked = []
        seen = set()
        for name, s in scored:
            n = _norm(name)
            if n in seen:
                continue
            # 仅依赖噪声词命中的、且已经有 2 个以上结果的，不再选入
            token_hits = {_norm(t) for t in tokens if len(_norm(t)) >= 2}
            non_noise_hit = any(
                (t == n or t in n or n in t) and t not in noise_set
                for t in token_hits
            )
            if not non_noise_hit and len(picked) >= 2:
                continue
            picked.append(name)
            seen.add(n)
            if len(picked) >= topn:
                break
        return picked

    def v2_to_old(cfg: dict) -> dict:
        # tokens = business_dna_tags + weak_positive_tags + core_semantic +
        #          industry_soft_constraints.keys + industry_roles.keys
        tag_tokens = list(cfg.get("business_dna_tags", []) or [])
        weak_tokens = list(cfg.get("weak_positive_tags", []) or [])
        sem_tokens = list(cfg.get("core_semantic", []) or [])
        ind_soft = list(cfg.get("industry_soft_constraints", {}).keys() or [])
        ind_roles = list(cfg.get("industry_roles", {}).keys() or [])

        all_tokens = ind_soft + ind_roles + tag_tokens + weak_tokens + sem_tokens
        # industry 用"更像行业名"的 token：industry_soft_constraints / industry_roles 的键
        industry_tokens = {_norm(t) for t in (ind_soft + ind_roles) if 2 <= len(_norm(t)) <= 10}
        # concept 用 business / weak / semantic
        concept_tokens = {_norm(t) for t in all_tokens if 2 <= len(_norm(t)) <= 10}

        industry_list = _match_boards(industry_tokens, IND_NORM, INDUSTRY_NOISE, topn=6)
        concept_list = _match_boards(concept_tokens, CON_NORM, CONCEPT_NOISE, topn=8)

        # 手工补强：对于几个专业标签难以映射的主题，注入已知的东财概念板块名
        # key 可以是 theme_name，也可以是 business_dna_tags 里的关键子串
        MANUAL_CONCEPT = {
            "AI应用": ["人工智能", "AI应用", "AI智能体", "多模态AI", "ChatGPT概念", "AIGC概念", "数字人"],
            "AI模型与AI Agent": ["人工智能", "AI应用", "AI智能体", "多模态AI", "ChatGPT概念", "AIGC概念"],
            "AI算力芯片": ["AI芯片", "算力概念", "GPU概念", "人工智能", "半导体概念", "先进封装"],
            "光模块与CPO": ["CPO概念", "光通信模块", "光纤概念", "光通信", "5G概念"],
            "数据中心网络": ["东数西算", "算力概念", "云计算", "数字经济"],
            "数据中心散热": ["液冷概念", "算力概念", "数据中心", "绿色电力"],
            "高速铜连接": ["铜缆高速连接", "连接器", "算力概念"],
            "行星滚柱丝杠": ["人形机器人", "减速器", "工业母机", "高端装备"],
            "核聚变": ["核能核电", "高温超导", "军工"],
        }
        theme_name = cfg.get("theme_name", "")
        extra = MANUAL_CONCEPT.get(theme_name, [])
        if extra:
            for b in extra:
                # 只保留确实存在于东财概念板块的名（用 norm 匹配）
                bn = _norm(b)
                if bn in CON_NORM and b not in concept_list:
                    concept_list.append(b)

        keywords = []
        for t in tag_tokens + weak_tokens + sem_tokens:
            if t and t not in keywords:
                keywords.append(t)

        exclude_keywords = list(cfg.get("negative_pressure_tags", {}).keys() or [])

        # ===== 新增: DNA Gate Concept 强约束 =====
        # 对于 semantic_business_hybrid 模式，必须匹配至少1个 business_dna_tags
        # 映射到的东财概念板块名，才真正进入该主题（防止行业溢出）
        mode = (cfg.get("matching_strategy", {}) or {}).get("mode", "")
        dna_concept_required = []
        if mode == "semantic_business_hybrid" and tag_tokens:
            # 增加 token 最小长度到 4，避免短词如 "PVD" 被误匹配成 "PVDF概念"
            dna_concept_required = _match_boards(
                {_norm(t) for t in tag_tokens if 4 <= len(_norm(t)) <= 15},
                CON_NORM, CONCEPT_NOISE, topn=10
            )

        return {
            "industry": industry_list,
            "concept": concept_list,
            "keywords": keywords[:25],
            "exclude_keywords": exclude_keywords,
            "core_companies": [],
            "leader_companies": [],
            "dna_concept_required": dna_concept_required,  # 新增: DNA Gate 强约束
            "_v2": {
                "theme_name": cfg.get("theme_name"),
                "theme_type": cfg.get("theme_type"),
                "core_semantic": cfg.get("core_semantic"),
                "industry_roles": cfg.get("industry_roles"),
                "industry_soft_constraints": cfg.get("industry_soft_constraints"),
                "business_dna_tags": cfg.get("business_dna_tags"),
                "weak_positive_tags": cfg.get("weak_positive_tags"),
                "negative_pressure_tags": cfg.get("negative_pressure_tags"),
            },
        }

    return v2_to_old


# =====================================================================
# 动态龙头 / 中军识别（每日根据最新行情重新计算）
# =====================================================================

def compute_kline_metrics(codes, trade_date):
    """
    拉取每只股票近 100 日 K 线，计算动态指标：
      - limit_up_days       : 近 10 日内连续涨停板数（>=9.7% 视为涨停）
      - recent_up_days      : 近 10 日内涨停天数
      - change_5d           : 近 5 日涨跌幅 (%)
      - change_10d          : 近 10 日涨跌幅 (%)
      - change_20d          : 近 20 日涨跌幅 (%)     [新增]
      - change_60d          : 近 60 日涨跌幅 (%)     [新增]
      - avg_amount_5d       : 近 5 日均成交额（元）
      - avg_amount_20d      : 近 20 日均成交额（元）  [新增]
      - ma10_slope          : MA10 近 5 日斜率（%）
      - ma20_slope          : MA20 近 5 日斜率（%）   [新增]
      - ma60_slope          : MA60 近 5 日斜率（%）   [新增]
      - close_above_ma5     : 最新收盘价是否站上 MA5
      - close_above_ma20    : 最新收盘价是否站上 MA20  [新增]
      - close_above_ma60    : 最新收盘价是否站上 MA60  [新增]
      - trend_score_0_100   : 趋势综合分（0-100）
    """
    if not codes:
        return {}

    end = trade_date
    start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=100)).strftime("%Y%m%d")
    print(f"[KLine] 拉取 {len(codes)} 只股票 {start} ~ {end} K 线数据 ...")
    t0 = time.time()

    # 分批次拉，避免一次性请求过多
    batch_size = 500
    all_klines = []
    for i in range(0, len(codes), batch_size):
        chunk = codes[i:i + batch_size]
        df = tts.get_daily_kline(chunk, start, end)
        if df is not None and not df.empty:
            all_klines.append(df)

    if not all_klines:
        print(f"[KLine] ❌ 未拉到任何 K 线数据，跳过动态识别")
        return {}

    kdf = pd.concat(all_klines, ignore_index=True)
    if 'trade_date' in kdf.columns:
        kdf['trade_date'] = kdf['trade_date'].astype(str)
        kdf = kdf.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

    print(f"[KLine] ✅ 拉到 {len(kdf)} 行 K 线，耗时 {time.time()-t0:.1f}s")

    # ===== 计算每只股票的指标 =====
    metrics = {}
    LIMIT_UP_THRESH = 9.7  # 涨停阈值（%）

    for code, grp in kdf.groupby('ts_code'):
        if len(grp) < 5:
            continue

        grp_sorted = grp.sort_values('trade_date').reset_index(drop=True)
        n = len(grp_sorted)
        last = grp_sorted.iloc[-1]

        # --- 基础价格/成交 ---
        close_last = float(last.get('close', 0) or 0)
        amount_last = float(last.get('amount', 0) or 0)

        # --- 5 日 / 10 日涨跌幅 ---
        change_5d = 0.0
        if n >= 5:
            prev_5 = float(grp_sorted.iloc[n - 5].get('close', close_last) or close_last)
            if prev_5 > 0:
                change_5d = round((close_last - prev_5) / prev_5 * 100, 2)
        change_10d = 0.0
        if n >= 10:
            prev_10 = float(grp_sorted.iloc[n - 10].get('close', close_last) or close_last)
            if prev_10 > 0:
                change_10d = round((close_last - prev_10) / prev_10 * 100, 2)
        # --- [新增] 20 日 / 60 日涨跌幅 ---
        change_20d = 0.0
        if n >= 20:
            prev_20 = float(grp_sorted.iloc[n - 20].get('close', close_last) or close_last)
            if prev_20 > 0:
                change_20d = round((close_last - prev_20) / prev_20 * 100, 2)
        change_60d = 0.0
        if n >= 60:
            prev_60 = float(grp_sorted.iloc[n - 60].get('close', close_last) or close_last)
            if prev_60 > 0:
                change_60d = round((close_last - prev_60) / prev_60 * 100, 2)

        # --- 近 5 日 / 20 日平均成交额 ---
        amt_series = grp_sorted['amount'].astype(float).fillna(0).tolist()
        # 换算为 元: Tushare amount 单位为 千元
        avg_amount_5d = round(np.mean(amt_series[-5:]) * 1000, 0) if len(amt_series) >= 5 else 0.0
        avg_amount_20d = round(np.mean(amt_series[-20:]) * 1000, 0) if len(amt_series) >= 20 else 0.0

        # --- 连板数：从最新日往回数，连续涨停的天数 ---
        pct_chg_list = grp_sorted.get('pct_chg', grp_sorted.get('pct_change', None))
        if pct_chg_list is not None:
            pct_list = pct_chg_list.astype(float).fillna(0).tolist()
        else:
            # 用 close 推算
            closes = grp_sorted['close'].astype(float).tolist()
            pct_list = [0.0] + [round((closes[i] - closes[i-1])/closes[i-1]*100, 2) for i in range(1, len(closes))]

        limit_up_days = 0
        for v in reversed(pct_list):
            if v >= LIMIT_UP_THRESH:
                limit_up_days += 1
            else:
                break

        # --- 近 10 日涨停天数 ---
        recent_up_days = sum(1 for v in pct_list[-10:] if v >= LIMIT_UP_THRESH)

        # --- MA10 / MA20 / MA60 斜率 ---
        def _ma_slope(ma_col, lookback):
            col = grp_sorted.get(ma_col, None)
            if col is None:
                return 0.0
            vals = col.astype(float).dropna().tolist()
            if len(vals) >= lookback + 1 and vals[-lookback] > 0:
                return round((vals[-1] - vals[-lookback]) / vals[-lookback] * 100, 2)
            return 0.0

        ma10_slope = _ma_slope('ma10', 5)
        ma20_slope = _ma_slope('ma20', 5)   # [新增]
        ma60_slope = _ma_slope('ma60', 5)   # [新增]

        # --- 收盘价是否站上 MA5 / MA20 / MA60 ---
        def _above_ma(ma_col):
            col = grp_sorted.get(ma_col, None)
            if col is None or len(col) < 5:
                return False
            val = float(col.iloc[-1])
            return val > 0 and close_last >= val

        close_above_ma5 = _above_ma('ma5')
        close_above_ma20 = _above_ma('ma20')   # [新增]
        close_above_ma60 = _above_ma('ma60')   # [新增]

        # --- 趋势分（0-100）：5d/10d/20d/60d 涨跌幅 + MA10/MA20 斜率 + 站上均线 ---
        trend_raw = 0.0
        trend_raw += min(max(change_5d, -20), 20) * 1.5    # 5日涨跌幅，±30
        trend_raw += min(max(change_10d, -30), 30) * 1.0   # 10日涨跌幅，±30
        trend_raw += min(max(change_20d, -50), 50) * 0.5   # [新增] 20日涨跌幅，±25
        trend_raw += min(max(change_60d, -80), 80) * 0.3   # [新增] 60日涨跌幅，±24
        trend_raw += min(max(ma10_slope, -5), 5) * 2.0     # MA10 斜率，±10
        trend_raw += min(max(ma20_slope, -5), 5) * 1.5     # [新增] MA20 斜率，±7.5
        trend_raw += (20 if close_above_ma5 else -10)        # 站上MA5 奖励
        trend_raw += (10 if close_above_ma20 else -5)       # [新增] 站上MA20 奖励
        trend_raw += (5 if close_above_ma60 else 0)         # [新增] 站上MA60 奖励
        trend_score_0_100 = int(max(0, min(100, trend_raw + 50)))  # 归一到 0-100

        metrics[code] = {
            'close': close_last,
            'limit_up_days': limit_up_days,
            'recent_up_days': recent_up_days,
            'change_5d': change_5d,
            'change_10d': change_10d,
            'change_20d': change_20d,    # [新增]
            'change_60d': change_60d,    # [新增]
            'avg_amount_5d': avg_amount_5d,
            'avg_amount_20d': avg_amount_20d,  # [新增]
            'ma10_slope': ma10_slope,
            'ma20_slope': ma20_slope,   # [新增]
            'ma60_slope': ma60_slope,   # [新增]
            'close_above_ma5': close_above_ma5,
            'close_above_ma20': close_above_ma20,  # [新增]
            'close_above_ma60': close_above_ma60,  # [新增]
            'trend_score': trend_score_0_100,
        }

    print(f"[KLine] ✅ 成功计算 {len(metrics)} 只股票的动态指标")
    return metrics


def compute_market_cap_metrics(codes, trade_date):
    """
    从 daily_basic 拉取每只股票的总市值 / 流通市值 / 换手率。
    返回: {ts_code: {'total_mv': 万, 'circ_mv': 万, 'turnover_rate': %}}
    """
    if not codes:
        return {}

    print(f"[MCap] 拉取 {len(codes)} 只股票的市值数据 ({trade_date}) ...")
    # daily_basic 是按交易日的，需要拉一次
    try:
        df = tts.get_daily_basic(trade_date=trade_date)
    except Exception as e:
        print(f"[MCap] daily_basic 失败: {e}, 尝试其他日期")
        # 尝试前一天
        prev = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=2)).strftime("%Y%m%d")
        try:
            df = tts.get_daily_basic(trade_date=prev)
        except Exception:
            df = None

    result = {}
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            code = row['ts_code']
            if code not in codes:
                continue
            result[code] = {
                'total_mv': float(row.get('total_mv', 0) or 0),  # 单位：万
                'circ_mv': float(row.get('circ_mv', 0) or 0),
                'turnover_rate': float(row.get('turnover_rate', 0) or 0),
            }
    print(f"[MCap] ✅ 共拿到 {len(result)} 只股票的市值数据")
    return result


def classify_role(metrics_item, mcap_item, theme_context):
    """
    动态判断每只股票的角色（龙头 / 中军 / 补涨）。

    theme_context: dict，提供该主题内所有股票的分位数：
      {'amount_75p': float, 'amount_25p': float, 'mv_75p': float, ...}
    """
    role = "补涨"
    sub_role = ""

    # 基础字段
    limit_up = metrics_item.get('limit_up_days', 0)
    recent_up = metrics_item.get('recent_up_days', 0)
    change_5d = metrics_item.get('change_5d', 0)
    change_10d = metrics_item.get('change_10d', 0)
    avg_amount = metrics_item.get('avg_amount_5d', 0)
    ma10_slope = metrics_item.get('ma10_slope', 0)
    close_above_ma5 = metrics_item.get('close_above_ma5', False)
    trend_score = metrics_item.get('trend_score', 0)

    total_mv = (mcap_item or {}).get('total_mv', 0)

    amt_p75 = theme_context.get('amount_75p', 0)
    amt_p25 = theme_context.get('amount_25p', 0)
    mv_p75 = theme_context.get('mv_75p', 0)
    mv_p25 = theme_context.get('mv_25p', 0)
    chg10_p50 = theme_context.get('change10d_50p', 0)

    # ===== 龙头判断 =====
    # 条件：近期有涨停（连板或 10日内≥2次涨停）+ 趋势向上 + 涨幅领先
    is_leader = False
    if (limit_up >= 2) or (recent_up >= 2 and change_5d > 5):
        is_leader = True
    elif limit_up >= 1 and change_5d > 8 and trend_score >= 55:
        is_leader = True

    # ===== 中军判断 =====
    # 条件：大成交额 + 大市值 + MA10向上 + 站上MA5（不一定涨停，稳步上涨）
    is_middle = False
    if (avg_amount >= amt_p75 or total_mv >= mv_p75):
        if ma10_slope > 0 and close_above_ma5:
            # 涨幅在主题内不算最猛（避免把龙头也算成中军）
            if change_5d < 15:
                is_middle = True

    # ===== 角色分配 =====
    if is_leader and not is_middle:
        role = "龙头"
        if limit_up >= 3:
            sub_role = f"{limit_up}连板"
        elif limit_up >= 2:
            sub_role = f"{limit_up}连板"
        else:
            sub_role = f"近10日{recent_up}次涨停,5日+{change_5d:.0f}%"
    elif is_middle:
        role = "中军"
        amt_yi = round(avg_amount / 1e8, 1) if avg_amount else 0
        mv_yi = round(total_mv / 1e4, 1) if total_mv else 0
        sub_role = f"5日均成交{amt_yi}亿,市值{mv_yi}亿,MA10+{ma10_slope:.1f}%"
    else:
        role = "补涨"
        if change_10d > chg10_p50 or (close_above_ma5 and ma10_slope > -1):
            sub_role = f"均线转暖,5日+{change_5d:.0f}%"
        elif change_5d > 0:
            sub_role = f"小幅上涨+{change_5d:.0f}%"
        else:
            sub_role = f"蓄势,趋势分{trend_score}"

    # ===== 综合评分：把静态 score 和动态 trend_score/成交 合并 =====
    # score_0_100: 静态 chain_score(约 0-50) + 动态趋势分(0-50)
    return role, sub_role


def load_theme3_flat() -> dict:
    """把 theme3.json 读成 {子主题名: V2_cfg} 的扁平字典，保留一级目录名"""
    with open(THEME3_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # theme3.json 可能两种结构：
    # 1) {"CATEGORIES": {"AI": {"name":..., "themes": {...}}}, "THEME_FLAT_MAP": {...}}
    # 2) {"AI": {"themes": {...}}, ...}       （直接目录名 -> 目录对象）
    cats = data.get("CATEGORIES") if "CATEGORIES" in data else data

    flat = {}
    for cat_name, cat_obj in cats.items():
        if not isinstance(cat_obj, dict):
            continue
        themes = cat_obj.get("themes") or {}
        for theme_name, cfg in themes.items():
            cfg = dict(cfg or {})
            cfg["_top_category"] = cat_name
            flat[theme_name] = cfg
    return flat


# =====================================================================
# 主流程
# =====================================================================

def main():
    print("=" * 70)
    print("theme3_constituents_v2: 基于 theme3.json (V2) 生成各子主题成分股")
    print("=" * 70)
    print(f"[Config] 交易日期    : {TRADE_DATE}")
    print(f"[Config] theme3 路径 : {THEME3_PATH}")
    print(f"[Config] 缓存目录    : {CACHE_DIR}")
    print()

    # ---------- Step 1: 读 theme3.json ----------
    theme3_flat = load_theme3_flat()
    print(f"[Theme3] 共 {len(theme3_flat)} 个子主题")
    top_cats = defaultdict(list)
    for tname, cfg in theme3_flat.items():
        top_cats[cfg["_top_category"]].append(tname)
    for cat, themes in top_cats.items():
        print(f"  - {cat}: {len(themes)} 个 ({', '.join(themes[:3])}{'...' if len(themes)>3 else ''})")

    # ---------- Step 2: 拉东财板块数据（必须先有 dc_df 才能动态匹配板块名）----------
    print()
    print("[DC] 拉取东财板块数据 (通过 Tushare dc_index/dc_member)...")
    dc_df = tts.get_dc_members()
    if dc_df is None or len(dc_df) == 0:
        print("[DC] ❌ 未拉到板块数据，请检查 Tushare token / 网络")
        sys.exit(1)
    print(f"[DC] ✅ 共 {len(dc_df)} 条板块-股票映射记录")
    if "is_industry" in dc_df.columns:
        n_ind = int(dc_df["is_industry"].sum())
        n_con = len(dc_df) - n_ind
        print(f"     其中行业板块成员 {n_ind} 条，概念板块成员 {n_con} 条")

    # ---------- Step 3: V2 字段 -> 旧字段（运行时动态映射东财板块名）----------
    v2_to_old = build_v2_bridge_factory(dc_df)
    old_style = {}
    for tname, cfg in theme3_flat.items():
        old_style[tname] = v2_to_old(cfg)

    print()
    print("[Bridge] 桥接样例 (前 3 个主题):")
    for i, (tname, old) in enumerate(list(old_style.items())[:3]):
        print(f"  {i+1}. {tname}")
        print(f"     industry        : {old['industry']}")
        print(f"     concept         : {old['concept']}")
        print(f"     keywords        : {old['keywords'][:5]}")
        print(f"     exclude_keywords: {old['exclude_keywords']}")

    # ---------- Step 4: 股票基础信息 ----------
    print()
    print("[Stock] 股票基础信息...")
    stock_basic = tts.get_stock_basic()
    if stock_basic is not None and not stock_basic.empty:
        print(f"[Stock] ✅ stock_basic: {len(stock_basic)} 只")
    else:
        print("[Stock] ⚠️  未拉到 stock_basic，仅使用东财板块字段匹配")

    # ---------- Step 5: 逐个主题匹配成分股 ----------
    print()
    print("[Match] 开始按主题匹配成分股 ...")
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = \
        tts.match_theme_stocks(old_style, dc_df, stock_basic)

    # 汇总所有被匹配到的股票代码
    all_matched_codes = []
    for _, stocks in theme_stock_map.items():
        all_matched_codes.extend(stocks.keys())
    all_matched_codes = list(set(all_matched_codes))
    print(f"[Match] ✅ 共匹配到 {len(all_matched_codes)} 只唯一股票")

    # ---------- Step 6: 动态指标（连板 / 成交 / 趋势）----------
    # 每天重新跑，用于识别龙头/中军
    print()
    print("[Dynamic] 开始计算动态行情指标（连板/成交额/趋势）...")
    kline_metrics = compute_kline_metrics(all_matched_codes, TRADE_DATE)
    mcap_metrics = compute_market_cap_metrics(all_matched_codes, TRADE_DATE)
    print(f"[Dynamic] ✅ K线指标: {len(kline_metrics)}, 市值指标: {len(mcap_metrics)}")

    # ---------- Step 7: 逐个主题计算分位数并分配角色 ----------
    print()
    print("[Role] 开始为每个主题的股票分配龙头/中军/补涨角色 ...")

    results = []      # 供 CSV / JSON 导出
    total_stocks = 0
    all_codes = set()

    for idx, (tname, stock_dict) in enumerate(theme_stock_map.items(), 1):
        cat = theme3_flat.get(tname, {}).get("_top_category", "")
        n = len(stock_dict)
        total_stocks += n
        all_codes.update(stock_dict.keys())

        # --- 计算该主题的分位数上下文 ---
        amt_list = []
        mv_list = []
        chg10d_list = []
        for code in stock_dict:
            km = kline_metrics.get(code, {})
            mm = mcap_metrics.get(code, {})
            if km.get('avg_amount_5d', 0) > 0:
                amt_list.append(km['avg_amount_5d'])
            if mm.get('total_mv', 0) > 0:
                mv_list.append(mm['total_mv'])
            if km.get('change_10d', 0) != 0:
                chg10d_list.append(km['change_10d'])

        theme_context = {
            'amount_75p': float(np.percentile(amt_list, 75)) if amt_list else 1e8,
            'amount_25p': float(np.percentile(amt_list, 25)) if amt_list else 1e7,
            'mv_75p':     float(np.percentile(mv_list, 75))  if mv_list else 1e6,
            'mv_25p':     float(np.percentile(mv_list, 25))  if mv_list else 1e5,
            'change10d_50p': float(np.percentile(chg10d_list, 50)) if chg10d_list else 0,
        }

        # --- 为每只股票生成详情（含动态指标 + 角色）---
        stocks_detail = []
        for code, meta in stock_dict.items():
            name = name_map_basic.get(code, code)
            km = kline_metrics.get(code, {})
            mm = mcap_metrics.get(code, {})

            # 调用角色识别
            role, sub_role = classify_role(km, mm, theme_context)

            # 综合评分 = 静态 chain_score × 0.4 + 趋势分 × 0.4 + 成交分 × 0.2
            static_score = float(meta.get("score", 0) or 0)  # 约 0-50 区间
            trend_score = float(km.get('trend_score', 0))   # 0-100
            amt_norm = 0
            amt = km.get('avg_amount_5d', 0)
            if amt > 0 and theme_context['amount_75p'] > 0:
                amt_norm = min(100, (amt / theme_context['amount_75p']) * 50)  # 0-100
            combined_score = round(static_score * 2 * 0.4 + trend_score * 0.4 + amt_norm * 0.2, 1)

            stocks_detail.append({
                # 基础 & 静态
                "ts_code": code,
                "name": name,
                "score": meta.get("score", 0),
                "chain_distance": meta.get("chain_distance", 9),
                "industry_match": meta.get("industry_match", False),
                "via": meta.get("via", ""),

                # 新增：角色
                "role": role,
                "role_desc": sub_role,

                # 新增：动态指标
                "close": round(km.get('close', 0), 2),
                "change_5d_pct": km.get('change_5d', 0),
                "change_10d_pct": km.get('change_10d', 0),
                "change_20d_pct": km.get('change_20d', 0),   # [新增]
                "change_60d_pct": km.get('change_60d', 0),   # [新增]
                "avg_amount_5d": km.get('avg_amount_5d', 0),
                "avg_amount_20d": km.get('avg_amount_20d', 0),  # [新增]
                "limit_up_days": km.get('limit_up_days', 0),
                "recent_up_days": km.get('recent_up_days', 0),
                "ma10_slope_pct": km.get('ma10_slope', 0),
                "ma20_slope_pct": km.get('ma20_slope', 0),   # [新增]
                "ma60_slope_pct": km.get('ma60_slope', 0),   # [新增]
                "close_above_ma5": km.get('close_above_ma5', False),
                "close_above_ma20": km.get('close_above_ma20', False),  # [新增]
                "close_above_ma60": km.get('close_above_ma60', False),  # [新增]
                "trend_score": km.get('trend_score', 0),
                "total_mv_wan": mm.get('total_mv', 0),

                # 综合分（用于排序）
                "combined_score": combined_score,
            })

        # 按 combined_score 降序排列
        stocks_detail.sort(key=lambda s: -s["combined_score"])

        results.append({
            "top_category": cat,
            "theme_name": tname,
            "version": theme3_flat.get(tname, {}).get("version", "V2"),
            "theme_type": theme3_flat.get(tname, {}).get("theme_type", ""),
            "core_semantic": theme3_flat.get(tname, {}).get("core_semantic", []),
            "industry_roles": theme3_flat.get(tname, {}).get("industry_roles", {}),
            "v2_bridge": old_style.get(tname, {}),
            "theme_context": {
                "p75_amount_yi": round(theme_context['amount_75p'] / 1e8, 2),
                "p75_total_mv_yi": round(theme_context['mv_75p'] / 1e4, 2),
                "median_change_10d_pct": round(theme_context['change10d_50p'], 2),
            },
            "n_stocks": n,
            "stocks": stocks_detail,
        })

    # 汇总统计
    print(f"\n[Summary] {len(theme_stock_map)} 个主题, 合计 {total_stocks} 条记录, 去重后 {len(all_codes)} 只股票")

    # ---------- Step 8: 导出 JSON / CSV ----------
    out_json = os.path.join(CACHE_DIR, f"theme3_constituents_{TRADE_DATE}.json")
    out_csv = os.path.join(CACHE_DIR, f"theme3_constituents_{TRADE_DATE}.csv")

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "trade_date": TRADE_DATE,
            "version": "V3",
            "note": "V2主题配置 + 每日动态识别龙头(连板)/中军(成交额+趋势)/补涨",
            "source": "theme3.json + Tushare 东财板块 + Tushare K线 + daily_basic",
            "match_summary": {
                "themes": len(theme_stock_map),
                "total_constituents": total_stocks,
                "dedup_stocks": len(all_codes),
                "kline_metrics_count": len(kline_metrics),
                "mcap_metrics_count": len(mcap_metrics),
            },
            "themes": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON 输出: {out_json}")

    # 扁平 CSV：每只股票一行（包含角色、动态指标）
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "一级目录", "子主题", "角色", "角色描述",
            "代码", "名称",
            "静态评分", "综合分", "链距",
            "收盘价", "5日涨跌幅%", "10日涨跌幅%", "20日涨跌幅%", "60日涨跌幅%",
            "5日均成交额(元)", "20日均成交额(元)", "连板天数", "近10日涨停次数",
            "MA10斜率%", "MA20斜率%", "MA60斜率%",
            "是否站上MA5", "是否站上MA20", "是否站上MA60",
            "趋势分(0-100)", "总市值(万)",
            "行业匹配", "匹配路径",
        ])
        for r in results:
            for s in r["stocks"]:
                w.writerow([
                    r["top_category"], r["theme_name"],
                    s["role"], s["role_desc"],
                    s["ts_code"], s["name"],
                    s["score"], s["combined_score"], s["chain_distance"],
                    s["close"],
                    s["change_5d_pct"], s["change_10d_pct"],
                    s.get("change_20d_pct", 0), s.get("change_60d_pct", 0),
                    s["avg_amount_5d"], s.get("avg_amount_20d", 0),
                    s["limit_up_days"], s["recent_up_days"],
                    s["ma10_slope_pct"],
                    s.get("ma20_slope_pct", 0), s.get("ma60_slope_pct", 0),
                    s["close_above_ma5"],
                    s.get("close_above_ma20", False), s.get("close_above_ma60", False),
                    s["trend_score"], s["total_mv_wan"],
                    s["industry_match"], s["via"],
                ])
    print(f"✅ CSV  输出: {out_csv}")

    # ---------- Step 9: 快速打印样例（前 3 个主题，每主题前 3 只）----------
    print(f"\n=== 样例展示（前 3 个主题，每个主题 Top 3）===")
    for r in results[:3]:
        print(f"\n[{r['top_category']}] {r['theme_name']}（{r['n_stocks']} 只）")
        for s in r["stocks"][:3]:
            print(f"  - {s['role']:>4} {s['name']:<10} ({s['ts_code']}) "
                  f"综合分={s['combined_score']:>6} 5日={s['change_5d_pct']:>+6.1f}% "
                  f"连板={s['limit_up_days']} 5日均额={s['avg_amount_5d']/1e8:>5.1f}亿 "
                  f"{s['role_desc']}")


if __name__ == "__main__":
    main()
