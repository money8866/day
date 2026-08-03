#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题评分分析程序 V2
基于 theme_stock_map_v2 映射 + theme_kg_v3 配置，
复用 theme_trend_sentiment_score.py 的评分算法。

用法：
    python theme_score_v2.py
    python theme_score_v2.py 20260724  # 指定日期
"""

import sys, os, json, csv, time, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

# Windows GBK 控制台
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.dirname(BASE_DIR))

import numpy as np
import pandas as pd

# ─────────── 复用主题评分工具 ───────────
from theme_trend_sentiment_score import (
    get_daily_kline, get_index_kline, get_dc_members,
    get_stock_basic, get_dc_hot, get_stock_hot_rank,
    get_daily_basic, per_stock_features,
    calc_trend_score, calc_sentiment_score,
    calc_theme_hot_score, get_theme_hot_score_percentile,
    judge_hot_phase, calc_theme_state,
    get_prev_day_theme_data, analyze_style_trend,
    get_dc_hot_multi_days,
    linear, sigmoid,
    cache_get, cache_set, TRADE_DATE as GLOBAL_TRADE_DATE,
)

# ─────────── 配置 ───────────
N_DAYS = 60
MIN_STOCKS = 3
TOP_N_PER_THEME = 30
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
OUTPUT_DB = os.path.join(REPORT_DIR, "theme_scores.db")
OUTPUT_CSV = os.path.join(REPORT_DIR, "theme_scores_v2.csv")
CACHE_DIR = r"d:\mystock\cache_daily"
V2_MAP_DIR = CACHE_DIR  # v2 映射文件同一目录

os.makedirs(REPORT_DIR, exist_ok=True)

# ─────────── 辅助函数 ───────────
def load_v2_mapping(trade_date):
    """加载 v2 主题-个股映射 JSON"""
    path = os.path.join(V2_MAP_DIR, f"theme_stock_map_v2_{trade_date}.json")
    if not os.path.exists(path):
        # 尝试不带 v2 后缀
        path = os.path.join(V2_MAP_DIR, f"theme_stock_map_{trade_date}.json")
    if not os.path.exists(path):
        # 尝试不带日期
        path = os.path.join(V2_MAP_DIR, "theme_stock_map_v2_latest.json")
    if not os.path.exists(path):
        print(f"[错误] 未找到 v2 映射文件: {path}")
        return None, None

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    themes = data.get("themes", {})
    stocks = data.get("stocks", {})

    # 转换为 {主题名: {码: {name, code, via, ...}}} 格式
    theme_stock_map = {}
    for theme_name, stock_list in themes.items():
        theme_stock_map[theme_name] = {}
        for s in stock_list:
            code = s["code"]
            theme_stock_map[theme_name][code] = {
                "name": s["name"],
                "via": s.get("via", ""),
                "score": s.get("score", 0),
                "irs_score": s.get("irs_score", 0),
                "industry": s.get("industry", ""),
            }

    print(f"  v2 映射加载完成: {len(theme_stock_map)} 主题, {len(stocks)} 个股")
    return theme_stock_map, stocks


def load_kg_v3_config():
    """加载 theme_kg_v3 配置"""
    paths = [
        os.path.join(BASE_DIR, "theme_kg_v3", "theme_kg_v3", "config", "theme_config.json"),
        os.path.join(BASE_DIR, "theme_kg_v3", "config", "theme_config.json"),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    print(f"[错误] 未找到 theme_config.json")
    return None


def load_stock_concepts(ts_codes):
    """加载个股概念标签（复用 theme_ts 的缓存）"""
    from theme_trend_sentiment_score import get_dc_members
    dc_df = get_dc_members()
    stock_concepts = {}
    if dc_df is not None and not dc_df.empty:
        # {code: [concept1, concept2, ...]}
        grp = dc_df.groupby('ts_code')['name'].apply(list)
        for code in ts_codes:
            if code in grp:
                stock_concepts[code] = grp[code]
            else:
                stock_concepts[code] = []
    return stock_concepts


# ─────────── 主评分流程 ───────────
def run_v2_analysis(trade_date=None):
    """对 v2 映射运行主题评分分析"""
    global TRADE_DATE, START_DATE, TRADE_DATE_str

    if trade_date is None:
        trade_date = GLOBAL_TRADE_DATE
    TRADE_DATE_str = str(trade_date)

    # 计算周期起始日
    dt = datetime.strptime(TRADE_DATE_str, "%Y%m%d")
    START_DATE = (dt - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")

    print(f"{'='*60}")
    print(f"主题评分分析 V2 - {TRADE_DATE_str}")
    print(f"{'='*60}")

    # 1. 加载 v2 映射
    print("\n[1/6] 加载 v2 主题-个股映射...")
    theme_stock_map, stock_map = load_v2_mapping(TRADE_DATE_str)
    if theme_stock_map is None:
        return

    # 2. 加载 kg_v3 配置
    print("\n[2/6] 加载 kg_v3 主题配置...")
    kg_v3_cfg = load_kg_v3_config()
    if kg_v3_cfg is None:
        return

    # 构建中英对照映射和主题配置
    theme_config_map = {}  # {中文名: {industry, concept, keywords, ...}}
    en_to_cn = {}          # {英文KEY: 中文名}
    for key, cfg in kg_v3_cfg.items():
        if key.startswith('_'):
            continue
        cn_name = cfg.get("name_cn", key)
        en_to_cn[key] = cn_name

        # 构建旧格式配置（给评分函数用）
        theme_config_map[cn_name] = {
            "industry": list(set(cfg.get("sw_industry_match", []) + cfg.get("cx_industry_match", []))),
            "concept": list(set(cfg.get("eastmoney_concepts", []) + cfg.get("ths_concepts", []))),
            "keywords": cfg.get("keywords", []),
            "exclude_keywords": cfg.get("exclude_keywords", []),
            "core_companies": cfg.get("core_stocks", []) + cfg.get("brand_keywords", []),
            "leader_companies": cfg.get("leaders", []),
            "etf_codes": cfg.get("etf_codes", []),
            "main_etf": cfg.get("main_etf", ""),
            "level": cfg.get("level", 1),
        }

    print(f"  加载完成: {len(theme_config_map)} 个主题配置")

    # 3. 预热数据
    print("\n[3/6] 预热基础数据...")
    all_codes = set()
    for tn, m in theme_stock_map.items():
        all_codes.update(m.keys())
    print(f"  总共 {len(all_codes)} 只个股")

    dc_hot = get_dc_hot(TRADE_DATE_str)
    daily_basic = get_daily_basic(TRADE_DATE_str)

    # 4. 获取 K 线数据
    print("\n[4/6] 获取日线数据...")
    kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE_str)
    kline_groups = {}
    if kline_df is not None and not kline_df.empty:
        for code, sub in kline_df.groupby('ts_code'):
            kline_groups[code] = sub
        print(f"  K线数据: {len(kline_groups)} 只个股有数据")
    else:
        print("  [警告] 无 K 线数据!")
        return

    # 5. 获取指数数据（用于计算相对强度）
    idx_df = get_index_kline("000300.SH")
    market_ret_10 = 0.0
    if idx_df is not None and not idx_df.empty:
        idx_df = idx_df.sort_values('trade_date')
        closes = idx_df['close'].astype(float).values
        if len(closes) >= 11:
            market_ret_10 = (closes[-1] / closes[-11] - 1) * 100

    # 加载概念标签
    stock_concepts = load_stock_concepts(list(all_codes))
    name_map_basic = {s.get("code", ""): s.get("name", "") for s in stock_map.values()} if stock_map else {}

    # 6. 主题评分
    print("\n[5/6] 开始主题评分...")

    # ── 6a. 预加载热榜数据（批量获取热榜分）──
    print("  预加载热榜数据...")
    dc_hot_multi = get_dc_hot_multi_days(days=2, force_refresh=False)

    # ── 6b. 读取前一日数据（用于判断状态变化）──
    prev_theme_data = get_prev_day_theme_data()

    results = []
    rows_per_theme = {}

    for theme_name, cfg in theme_config_map.items():
        matched = theme_stock_map.get(theme_name, {})
        if not matched:
            results.append({
                'theme': theme_name, 'n_stocks': 0,
                'trend_score': 0.0, 'sentiment_score': 0.0, 'composite_score': 0.0,
            })
            continue

        # 当日均线基础数据
        mcap_dict = {}
        if daily_basic is not None and not daily_basic.empty:
            mcap_dict = {r['ts_code']: r for _, r in daily_basic.iterrows()}

        industry_list = cfg.get('industry', [])
        concept_list = cfg.get('concept', [])
        keyword_list = cfg.get('keywords', [])

        rows = []
        for code, meta in matched.items():
            kdf = kline_groups.get(code)
            if kdf is None or len(kdf) < 6:
                continue
            feat = per_stock_features_v2(kdf)  # V2 增强版特征提取
            if feat is None:
                continue

            # 合并换手率
            if daily_basic is not None and not daily_basic.empty:
                db_one = daily_basic[daily_basic['ts_code'] == code]
                if not db_one.empty:
                    turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                    feat['turnover'] = float(turnover)

            # 概念纯度
            concepts = stock_concepts.get(code, [])
            concepts_str = "|".join(concepts)
            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1

            feat['ts_code'] = code
            feat['name'] = meta.get('name', name_map_basic.get(code, code))
            feat['purity'] = purity
            feat['total_mv'] = mcap_dict.get(code, {}).get('total_mv', 0) or 0
            feat['industry_match'] = 0  # v2 已匹配，默认真
            feat['hot_rank_score'] = get_stock_hot_rank(code)
            rows.append(feat)

        if len(rows) < MIN_STOCKS:
            results.append({
                'theme': theme_name, 'n_stocks': len(rows),
                'trend_score': 0.0, 'sentiment_score': 0.0, 'composite_score': 0.0,
            })
            rows_per_theme[theme_name] = []
            continue

        # 统计全成份股
        all_rows = rows
        all_zt_count = sum(1 for r in all_rows if r.get("zt_flag") == 1)
        all_total = len(all_rows)

        # 按市值权重排序，取前30只用于趋势分计算
        for r in rows:
            r['mcap_w'] = (r['total_mv'] / 10000) ** 0.5 * 0.8 + r['purity'] * 2
        rows.sort(key=lambda x: x['mcap_w'], reverse=True)
        top_rows = rows[:TOP_N_PER_THEME]

        t_score, t_detail = calc_trend_score_v2(top_rows, market_ret_10)
        s_score, s_detail = calc_sentiment_score_v2(all_rows, market_ret_10)

        # 热度得分
        hot_score, hot_detail = calc_theme_hot_score(all_rows)
        hot_percentile, _ = get_theme_hot_score_percentile(theme_name, hot_score, days=60)
        hot_phase, hot_warning = judge_hot_phase(
            hot_score=hot_score,
            percentile=hot_percentile,
            top10_count=hot_detail.get('top10_count', 0),
            top5_count=hot_detail.get('top5_count', 0),
            total_stocks=all_total,
        )

        # V2 综合分：更平衡的权重
        if t_score >= 30 and s_score < 15:
            # 趋势尚可但无情绪→偏向趋势
            composite = round(0.65 * t_score + 0.35 * s_score, 1)
        elif s_score >= 30 and t_score < 15:
            # 情绪活跃但趋势弱→偏向情绪（连板股的逆势行为）
            composite = round(0.35 * t_score + 0.65 * s_score, 1)
        else:
            composite = round(0.50 * t_score + 0.50 * s_score, 1)

        # 龙头评分
        leader_scores = []
        for r in top_rows:
            lb = r.get('lb_height', 0)
            pct = abs(r.get('pct_chg', 0))
            amt = r.get('amount_latest', 0)
            p = r.get('purity', 0)
            ls = 0.4 * min(lb * 20, 100) + 0.3 * min(pct * 5, 100) + 0.2 * min(amt * 2, 100) + 0.1 * min(p * 20, 100)
            leader_scores.append((r, ls))
        leader_scores.sort(key=lambda x: x[1], reverse=True)
        leader_stock = leader_scores[0][0] if leader_scores else None
        leader_name = leader_stock['name'] if leader_stock else ""
        leader_code = leader_stock['ts_code'] if leader_stock else ""

        # 中军评分
        core_candidates = [r for r in top_rows if r.get('total_mv', 0) > 2000000 and r.get('purity', 0) >= 1]
        core_scores = []
        for r in core_candidates:
            amt = r.get('amount_latest', 0)
            mv = r.get('total_mv', 0) / 10000
            pct = abs(r.get('pct_chg', 0))
            cs = 0.5 * min(amt * 2, 100) + 0.3 * min(mv / 10, 100) + 0.2 * min(pct * 5, 100)
            core_scores.append((r, cs))
        core_scores.sort(key=lambda x: x[1], reverse=True)
        core_stock = core_scores[0][0] if core_scores else None
        core_name = core_stock['name'] if core_stock else ""
        core_code = core_stock['ts_code'] if core_stock else ""

        results.append({
            'theme': theme_name, 'n_stocks': all_total,
            'trend_score': t_score, 'sentiment_score': s_score,
            'composite_score': composite,
            'trend_detail': t_detail, 'sentiment_detail': s_detail,
            'leader_name': leader_name, 'leader_code': leader_code,
            'leader_score': round(leader_scores[0][1], 1) if leader_scores else 0,
            'core_name': core_name, 'core_code': core_code,
            'core_score': round(core_scores[0][1], 1) if core_scores else 0,
            'hot_score': round(hot_score, 2), 'hot_percentile': hot_percentile,
            'hot_phase': hot_phase, 'hot_warning': hot_warning,
            'hot_detail': hot_detail,
        })
        rows_per_theme[theme_name] = top_rows

    # 排序
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r['rank'] = i

    # 计算主题状态 + 年龄追踪
    print("  计算主题状态...")
    theme_age = {}
    for r in results:
        prev = prev_theme_data.get(r['theme'])
        theme_state = calc_theme_state_v2(r, prev)
        r['theme_state'] = theme_state
        # 年龄追踪：连续同一状态的天数
        prev_state = prev.get('theme_state', '') if prev else ''
        if prev_state == theme_state and theme_state:
            theme_age[r['theme']] = theme_age.get(r['theme'], 1) + 1
        else:
            theme_age[r['theme']] = 1

    # 阶段迁移预测 + 交易动作建议
    print("  计算阶段迁移预测...")
    for r in results:
        migration = calc_phase_migration(
            r, market_ret_10, idx_df,
            prev_data=prev_theme_data.get(r['theme']),
            age_days=theme_age.get(r['theme'], 1),
        )
        r.update(migration)

    # 风格分析（top5）
    style_result = analyze_style_trend(results[:5])

    # ─── 7. 保存结果 ───
    print("\n[6/6] 保存结果...")

    # CSV（v2 独立文件）
    save_to_csv_v2(results)

    # SQLite
    save_to_sqlite_v2(results)

    # 文本报告
    save_to_text_report_v2(results, kg_v3_cfg, en_to_cn)

    # 打印排名
    print(f"\n{'='*80}")
    print(f"主题评分排名 V2 - {TRADE_DATE_str}")
    print(f"{'='*80}")
    print(f"{'排名':<4} {'主题':<12} {'趋势':<6} {'情绪':<6} {'综合':<6} {'涨停':<4} {'迁移分':<6} {'目标状态':<12} {'交易动作':<8}")
    print(f"{'-'*80}")
    for r in results[:20]:  # 只打印前20
        sd = r.get('sentiment_detail', {}) or {}
        zt = sd.get('zt_count', 0)
        state = r.get('theme_state', '')
        mig = r.get('migration_score', 0)
        target = r.get('target_state', '')
        action = r.get('trade_action', '')
        print(f"{r['rank']:<4} {r['theme']:<12} {r['trend_score']:<6.1f} {r['sentiment_score']:<6.1f} {r['composite_score']:<6.1f} {zt:<4} {mig:<6.1f} {target:<12} {action:<8}")

    print(f"\n完成! 共 {len(results)} 个主题评分")
    return results


def save_to_csv_v2(results):
    """保存 v2 CSV（含迁移预测字段）"""
    flat = []
    for r in results:
        sd = r.get('sentiment_detail', {}) or {}
        mf = r.get('migration_factors', {}) or {}
        climax_warning = 1 if (r["trend_score"] >= 70 and r["sentiment_score"] >= 85) else 0
        row = {"rank": r["rank"], "theme": r["theme"], "n_stocks": r["n_stocks"], 
               "trend_score": r["trend_score"], "sentiment_score": r["sentiment_score"],
               "composite_score": r["composite_score"], "climax_warning": climax_warning,
               "leader_name": r.get("leader_name", ""), "leader_code": r.get("leader_code", ""),
               "leader_score": r.get("leader_score", 0),
               "core_name": r.get("core_name", ""), "core_code": r.get("core_code", ""),
               "core_score": r.get("core_score", 0),
               "hot_phase": r.get("hot_phase", ""), "theme_state": r.get("theme_state", ""),
               "zt_count": sd.get("zt_count", 0), "up_ratio": sd.get("up_ratio", 0),
               # 迁移预测字段
               "migration_score": r.get("migration_score", 0),
               "migration_direction": r.get("migration_direction", ""),
               "target_state": r.get("target_state", ""),
               "trade_action": r.get("trade_action", ""),
               "action_reason": r.get("action_reason", ""),
               "proximity": mf.get("proximity", 0), "momentum": mf.get("momentum", 0),
               "confirmation": mf.get("confirmation", 0), "money_resonance": mf.get("money_resonance", 0),
               "leader_health": mf.get("leader_health", 0), "regime": mf.get("regime", 0),
               "age_penalty": mf.get("age_penalty", 0), "macro_filter": mf.get("macro_filter", 0),
               }
        row.update({f"t_{k}": v for k, v in (r.get("trend_detail") or {}).items()})
        row.update({f"s_{k}": v for k, v in sd.items()})
        flat.append(row)

    path = os.path.join(REPORT_DIR, f"theme_scores_v2_{TRADE_DATE_str}.csv")
    pd.DataFrame(flat).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[保存] CSV: {path} ({len(flat)} 条)")


def save_to_sqlite_v2(results):
    """保存到 SQLite（含迁移预测字段）"""
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()

    # 创建/更新表
    cur.execute("""CREATE TABLE IF NOT EXISTS theme_scores (
        rank INTEGER, theme TEXT, n_stocks INTEGER, trend_score REAL, sentiment_score REAL, composite_score REAL,
        climax_warning INTEGER DEFAULT 0, leader_name TEXT, leader_code TEXT, leader_score REAL,
        core_name TEXT, core_code TEXT, core_score REAL, ret_5 REAL, ret_10 REAL, ret_20 REAL, up_ratio REAL, zt_count INTEGER, 
        trade_date TEXT, theme_state TEXT, hot_score REAL, hot_percentile REAL, hot_phase TEXT, hot_warning TEXT
    )""")
    # 新增迁移预测列（兼容旧表）
    for col in ["migration_score REAL", "migration_direction TEXT", "target_state TEXT", "trade_action TEXT"]:
        try:
            cur.execute(f"ALTER TABLE theme_scores ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # 列已存在

    # 先删除该日期的旧数据
    cur.execute("DELETE FROM theme_scores WHERE trade_date = ?", (TRADE_DATE_str,))

    for r in results:
        td = r.get("trend_detail", {}) or {}
        sd = r.get("sentiment_detail", {}) or {}
        climax_warning = 1 if (r["trend_score"] >= 70 and r["sentiment_score"] >= 85) else 0
        theme_state = r.get("theme_state", "弱势")
        cur.execute("""INSERT INTO theme_scores 
            (rank, theme, n_stocks, trend_score, sentiment_score, composite_score,
             climax_warning, leader_name, leader_code, leader_score,
             core_name, core_code, core_score,
             ret_5, ret_10, ret_20, up_ratio, zt_count,
             trade_date, theme_state, hot_score, hot_percentile, hot_phase, hot_warning,
             migration_score, migration_direction, target_state, trade_action)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r['rank'], r['theme'], r['n_stocks'], r['trend_score'], r['sentiment_score'], r['composite_score'],
             climax_warning, r.get('leader_name', ''), r.get('leader_code', ''), r.get('leader_score', 0),
             r.get('core_name', ''), r.get('core_code', ''), r.get('core_score', 0),
             td.get('avg_ret_5', 0), td.get('avg_ret_10', 0), td.get('avg_ret_20', 0),
             sd.get('up_ratio', 0), sd.get('zt_count', 0),
             TRADE_DATE_str, theme_state,
             r.get('hot_score', 0), r.get('hot_percentile', 50),
             r.get('hot_phase', '正常'), r.get('hot_warning', ''),
             r.get('migration_score', 0), r.get('migration_direction', 'sideways'),
             r.get('target_state', ''), r.get('trade_action', '')))

    conn.commit()
    conn.close()
    print(f"[保存] SQLite: {OUTPUT_DB} ({len(results)} 条)")


def save_to_text_report_v2(results, kg_v3_cfg, en_to_cn):
    """生成文本报告（含主题配置信息）"""
    report_path = os.path.join(REPORT_DIR, f"theme_analysis_v2_{TRADE_DATE_str}.txt")
    buf = []
    def w(s=""):
        buf.append(s)

    w("━" * 80)
    w(f"  主题评分分析报告 V2 - {TRADE_DATE_str}")
    w("━" * 80)
    w()

    # ── 重点机会 ──
    divergence_to_consensus = [r for r in results if r.get("theme_state") == "分歧转一致"]
    if divergence_to_consensus:
        w("━ 分歧转一致（重点关注）")
        w("─" * 60)
        for r in divergence_to_consensus:
            sd = r.get("sentiment_detail", {}) or {}
            w(f"  {r['theme']:<12} 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 上涨:{sd.get('up_ratio', 0):.0f}%")
            w(f"              龙头:{r.get('leader_name', '')}")
        w()

    start_rising = [r for r in results if r.get("theme_state") in ["启动", "强趋势"]]
    if start_rising:
        w("━ 启动/强趋势（趋势向好）")
        w("─" * 60)
        for r in start_rising[:5]:
            sd = r.get("sentiment_detail", {}) or {}
            w(f"  {r['theme']:<12} 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 龙头:{r.get('leader_name', '')}")
        w()

    # ── 排名表 ──
    w("─" * 80)
    w(f"{'排名':<4} {'主题':<12} {'趋势':<6} {'情绪':<6} {'综合':<6} {'ETF'} {'状态':<10}")
    w("─" * 80)
    for r in results:
        # 查找对应 ETF
        theme_name = r['theme']
        etf_info = ""
        for key, cfg in kg_v3_cfg.items():
            if key.startswith('_'):
                continue
            if cfg.get("name_cn", key) == theme_name:
                etf = cfg.get("main_etf", "")
                if etf:
                    # 去后缀
                    etf_info = etf.replace('.SH', '').replace('.SZ', '')[:8]
                break

        state = r.get('theme_state', '')
        state_icon = ""
        if state == "抱团主升": state_icon = "抱团主升"
        elif state == "强趋势": state_icon = "强趋势↑"
        elif state == "分歧转一致": state_icon = "转一致⭐"
        elif state == "启动": state_icon = "启动↑"
        elif state == "分歧": state_icon = "分歧~"
        elif state == "退潮": state_icon = "退潮↓"
        elif state == "弱趋势": state_icon = "弱趋势→"
        elif state == "震荡": state_icon = "震荡→"
        else: state_icon = state
        w(f"{r['rank']:<4} {r['theme']:<12} {r['trend_score']:<6.1f} {r['sentiment_score']:<6.1f} {r['composite_score']:<6.1f} {etf_info:<8} {state_icon:<10}")
    w("─" * 80)
    w()

    # ── 阶段迁移预测 + 交易动作建议 ──
    w("━" * 80)
    w("  阶段迁移预测 & 交易动作建议")
    w("─" * 80)
    w(f"{'排名':<4} {'主题':<12} {'当前状态':<10} {'迁移分':<6} {'预测方向':<8} {'目标状态':<10} {'交易动作':<10} {'因子详解':<30}")
    w(f"{'-'*80}")
    for r in results:
        direction_icon = ""
        if r.get('migration_direction') == 'upward': direction_icon = '↑向上'
        elif r.get('migration_direction') == 'downward': direction_icon = '↓向下'
        else: direction_icon = '→震荡'
        mf = r.get('migration_factors', {}) or {}
        factor_str = f"P:{mf.get('proximity',0):.0f} M:{mf.get('momentum',0):.0f} C:{mf.get('confirmation',0):.0f} $:{mf.get('money_resonance',0):.0f} L:{mf.get('leader_health',0):.0f} R:{mf.get('regime',0):.0f}"
        w(f"{r['rank']:<4} {r['theme']:<12} {r.get('theme_state',''):<10} {r.get('migration_score',0):<6.1f} {direction_icon:<8} {r.get('target_state',''):<10} {r.get('trade_action',''):<10} {factor_str}")
    w("─" * 80)
    w()

    w("━" * 80)
    w(f"报告生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w()

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(buf))
    print(f"[保存] 文本报告: {report_path}")


# ══════════════════════════════════════════════════════════════
# V2 优化版评分算法（A股因子参数经验优化版）
# ══════════════════════════════════════════════════════════════

def per_stock_features_v2(df_one):
    """
    增强版个股特征提取（A股因子参数经验优化版 V3）
    
    A股实战因子优化：
    1. 炸板信号(boom_flag)：开盘涨停但收盘不板=弱势信号
    2. 缩量调整比(volume_shrink)：缩量回调是A股重要洗盘形态
    3. 5日-10日均线关系(ma5_ma10_status)：金叉/死叉状态
    4. 连续阳线计数(consec_up)：连阳是A股强势蓄力信号
    5. 开盘强度(open_strength)：开盘涨幅反映当日进攻意图
    6. 量比计算优化：使用前20日均量避开近期放量干扰
    """
    if df_one is None or df_one.empty or len(df_one) < 6:
        return None

    df_one = df_one.sort_values("trade_date").reset_index(drop=True)
    close = df_one["close"].astype(float).values
    high = df_one["high"].astype(float).values
    low = df_one["low"].astype(float).values
    vol = df_one["vol"].astype(float).values
    pct = df_one["pct_chg"].astype(float).values
    open_p = df_one.get("open", df_one["close"]).astype(float).values

    n = len(close)
    last = n - 1

    def safe_pct(a, b):
        return (a / b - 1.0) * 100.0 if b and b > 0 else 0.0

    def calc_slope(prices):
        if len(prices) < 3:
            return 0.0
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        slope_norm = (slope / np.mean(prices)) * 100 if np.mean(prices) > 0 else 0
        return slope_norm

    # ── 原版特征 ──
    ret_5 = safe_pct(close[last], close[last - 5]) if last - 5 >= 0 else safe_pct(close[last], close[0])
    ret_10 = safe_pct(close[last], close[last - 10]) if last - 10 >= 0 else safe_pct(close[last], close[0])
    ret_20 = safe_pct(close[last], close[last - 20]) if last - 20 >= 0 else safe_pct(close[last], close[0])

    ma5 = close[max(0, last - 4) : last + 1].mean()
    ma10 = close[max(0, last - 9) : last + 1].mean()
    ma20 = close[max(0, last - 19) : last + 1].mean()
    ma60 = close[max(0, last - 59) : last + 1].mean() if n >= 60 else ma20
    ma240 = close[max(0, last - 239) : last + 1].mean() if n >= 240 else ma60
    ma5_b = (close[last] / ma5 - 1) * 100 if ma5 > 0 else 0
    ma10_b = (close[last] / ma10 - 1) * 100 if ma10 > 0 else 0
    ma20_b = (close[last] / ma20 - 1) * 100 if ma20 > 0 else 0
    ma60_b = (close[last] / ma60 - 1) * 100 if ma60 > 0 else 0

    win10 = close[max(0, last - 9) : last + 1]
    slope10 = calc_slope(win10)
    win60 = close[max(0, last - 59) : last + 1]
    slope60 = calc_slope(win60)
    win240 = close[max(0, last - 239) : last + 1]
    slope240 = calc_slope(win240)

    acc_5_10 = ret_5 - ret_10

    # 量比：改用前20日（排除最近5日活跃区）的均量做基准
    v_base_20 = vol[max(0, last - 24) : max(0, last - 4)].mean() if last >= 25 else vol[max(0, last - 19) : last + 1].mean()
    v5 = vol[max(0, last - 4) : last + 1].mean()
    vol_ratio = v5 / v_base_20 if v_base_20 > 0 else 1.0

    running_max = np.maximum.accumulate(close[max(0, last - 9) : last + 1])
    drawdown = (close[max(0, last - 9) : last + 1] / running_max - 1.0)
    max_dd_10 = drawdown.min() * 100 if len(drawdown) > 0 else 0.0

    # ── 涨停阈值按板型区分（修复：原统一用9.5%判涨停，20cm板涨10-19%被漏计）──
    # 创业板(300/301)/科创板(688) 涨停20%，沪深主板10%（ST 5%近似按10%处理，容差内）
    _code_prefix = str(df_one.get('ts_code', '')).split('.')[0] if 'ts_code' in df_one.columns else ''
    _zt_th = 19.5 if _code_prefix.startswith(('300', '301', '688')) else 9.5

    zt_flag = 1 if (pct[last] is not None and pct[last] >= _zt_th) else 0
    strong_flag = 1 if (pct[last] is not None and pct[last] >= 5.0) else 0
    amount_latest = float(df_one.iloc[last].get("amount", 0) or 0) / 100000

    # 连板检测（按板型阈值）
    lb_height = 0
    for j in range(last, -1, -1):
        p = float(pct[j]) if pct[j] is not None else 0
        if p >= _zt_th:
            lb_height += 1
        else:
            break

    # ── V2 原有新增因子 ──
    # 1. 跳空幅度
    prev_close = close[last - 1] if last - 1 >= 0 else close[last]
    gap_up_pct = safe_pct(open_p[last], prev_close) if last > 0 else 0.0

    # 2. 3日收益率（更高频动量）
    ret_3 = safe_pct(close[last], close[last - 3]) if last - 3 >= 0 else ret_5

    # 3. 3日斜率
    win3 = close[max(0, last - 2) : last + 1]
    slope_3 = calc_slope(win3) if len(win3) >= 3 else 0.0

    # 4. 3日-5日加速度（捕捉加速启动）
    acc_3_5 = ret_3 - ret_5

    # 5. 距20日高点百分比（突破or回撤状态）
    high_20 = np.max(close[max(0, last - 19) : last + 1])
    low_20 = np.min(close[max(0, last - 19) : last + 1])
    high_20_b = safe_pct(close[last], high_20)
    low_20_b = safe_pct(close[last], low_20)

    # 6. 当日量比
    vol_base = vol[max(0, last - 9) : last].mean() if last >= 10 else vol[max(0, last - 4) : last].mean()
    vol_ratio_today = vol[last] / vol_base if vol_base > 0 else 1.0

    # 7. 20日收益动量
    ret_20_ret = safe_pct(close[last], close[last - 20]) if last - 20 >= 0 else ret_10

    # ── V3 新增因子（A股实战经验优化）──
    # 8. 炸板信号：开盘涨停（开>=涨停价附近，含容差）但收盘不板（按板型阈值）
    prev_c = close[last - 1] if last - 1 >= 0 else close[last]
    zt_price = prev_c * (1 + (_zt_th - 0.5) / 100)
    boom_flag = 1 if (open_p[last] >= zt_price and pct[last] < _zt_th) else 0

    # 9. 缩量调整比：当日量/前20日均量，<0.7=明显缩量（A股洗盘信号）
    vol_ma20 = vol[max(0, last - 19) : last + 1].mean()
    volume_shrink = vol[last] / vol_ma20 if vol_ma20 > 0 else 1.0

    # 10. 5日-10日均线关系：1=金叉(ma5>ma10), 0=缠绕, -1=死叉
    ma5_t = close[max(0, last - 4) : last + 1].mean()
    ma10_t = close[max(0, last - 9) : last + 1].mean()
    ma5_prev = close[max(0, last - 5) : max(0, last)].mean() if last >= 5 else ma5_t
    ma10_prev = close[max(0, last - 10) : max(0, last - 1)].mean() if last >= 10 else ma10_t
    # 当前是否多头排列
    ma5_above_ma10 = 1 if ma5_t > ma10_t else -1 if ma5_t < ma10_t * 0.98 else 0
    # 金叉信号：之前ma5<ma10现在ma5>ma10
    golden_cross = 1 if (ma5_prev <= ma10_prev and ma5_t > ma10_t) else 0
    # 死叉信号
    dead_cross = 1 if (ma5_prev >= ma10_prev and ma5_t < ma10_t) else 0

    # 11. 连续阳线计数（连阳=蓄力信号）
    consec_up = 0
    for j in range(last, -1, -1):
        if pct[j] is not None and float(pct[j]) > 0:
            consec_up += 1
        else:
            break

    # 12. 开盘强度：开盘涨幅反应进攻意图
    open_strength = safe_pct(open_p[last], prev_c) if last > 0 else 0.0

    # 13. 价格位置：在20日区间的分位数（0=底部, 1=顶部）
    if high_20 > low_20:
        pos_in_20 = (close[last] - low_20) / (high_20 - low_20)
    else:
        pos_in_20 = 0.5

    return {
        # ── 原版特征 ──
        "ret_5": ret_5, "ret_10": ret_10, "ret_20": ret_20,
        "ma5_b": ma5_b, "ma10_b": ma10_b, "ma20_b": ma20_b,
        "ma60_b": ma60_b,
        "slope_10": slope10, "slope_60": slope60, "slope_240": slope240,
        "acc_5_10": acc_5_10, "vol_ratio": vol_ratio, "max_dd_10": max_dd_10,
        "zt_flag": zt_flag, "strong_flag": strong_flag,
        "pct_chg": float(pct[last]) if pct[last] is not None else 0.0,
        "turnover": float(df_one.iloc[last].get("turnover_rate", 0) or 0),
        "amount_latest": amount_latest, "lb_height": lb_height,
        # ── V2 新增因子 ──
        "gap_up_pct": gap_up_pct,
        "lb_consec": lb_height,
        "ret_3": ret_3,
        "slope_3": slope_3,
        "acc_3_5": acc_3_5,
        "high_20_b": high_20_b,
        "low_20_b": low_20_b,
        "vol_ratio_today": vol_ratio_today,
        "ret_20_mom": ret_20_ret,
        # ── V3 新增因子（A股实战）──
        "boom_flag": boom_flag,           # 炸板信号
        "volume_shrink": volume_shrink,   # 缩量比
        "ma5_above_ma10": ma5_above_ma10, # 均线排列
        "golden_cross": golden_cross,     # 金叉
        "dead_cross": dead_cross,         # 死叉
        "consec_up": consec_up,           # 连阳
        "open_strength": open_strength,   # 开盘强度
        "pos_in_20": round(pos_in_20, 3), # 20日位置分位数
    }


def calc_trend_score_v2(stock_feats, market_index_ret):
    """
    V3 优化版趋势评分（A股动量因子参数经验优化版）
    
    A股动量经验优化：
    1. ret_score: 降ret_20权重(0.25→0.15)，提ret_5权重(0.40→0.45)
        原因：A股10日以上动量开始衰减，5日动量最有效
    2. ma_score: 维持高频，MA5=0.40 MA10=0.30 MA20=0.15 MA60=0.15
        原因：60日线在A股趋势确认上仍有参考价值
    3. leader_score: 连板非线性评分更严格，真正区分龙头
        1板=5, 2板=20, 3板=50, 4板=80, 5板+=100
        原因：A股2板以下不具龙头辨识度，3板才算启动
    4. leader_score 权重提升至 0.18，A股龙头效应最核心
    5. 新增金叉加分、炸板惩罚
    6. today_adjust 增加正向增强（大涨日加分）
    7. pos_in_20 突破加分：突破20日高点的主题加分
    """
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    if n == 0:
        return 0.0, {}

    avg_ret_3 = np.mean([s["ret_3"] for s in stock_feats])
    avg_ret_5 = np.mean([s["ret_5"] for s in stock_feats])
    avg_ret_10 = np.mean([s["ret_10"] for s in stock_feats])
    avg_ret_20 = np.mean([s["ret_20"] for s in stock_feats])

    # ── 1. 收益分（A股短期动量更有效，降长期权重）──
    # A股强势主题5日涨20%即很强，20日后往往开始分化
    ret_score = (
        linear(avg_ret_5, -10, 25) * 0.45 +   # 5日收益权重最高（从0.40→0.45）
        linear(avg_ret_10, -15, 35) * 0.35 +   # 10日收益维持
        linear(avg_ret_20, -25, 50) * 0.15 +   # 20日收益权重降低（0.25→0.15）
        linear(avg_ret_3, -5, 15) * 0.05       # 新增3日收益作为前置信号
    )

    # ── 2. 均线分（维持短期聚焦）──
    pct_above_ma5 = sum(1 for s in stock_feats if s["ma5_b"] > 0) / n
    pct_above_ma10 = sum(1 for s in stock_feats if s["ma10_b"] > 0) / n
    pct_above_ma20 = sum(1 for s in stock_feats if s["ma20_b"] > 0) / n
    pct_above_ma60 = sum(1 for s in stock_feats if s["ma60_b"] > 0) / n
    ma_score = (
        pct_above_ma5 * 0.40 +
        pct_above_ma10 * 0.30 +
        pct_above_ma20 * 0.15 +   # 略降（0.18→0.15）
        pct_above_ma60 * 0.15     # 略升（0.12→0.15，60日线具趋势确认价值）
    )

    # ── 3. 斜率分（3日斜率增加截断防极端值）──
    avg_slope3 = np.mean([s["slope_3"] for s in stock_feats])
    avg_slope10 = np.mean([s["slope_10"] for s in stock_feats])
    avg_slope60 = np.mean([s["slope_60"] for s in stock_feats])
    # 3日斜率截断：A股连板股斜率可达50+，截断到±15避免主导评分
    slope3_clamped = max(-15, min(15, avg_slope3))
    slope_score = (
        sigmoid(slope3_clamped, k=0.6, c=0) * 0.35 +
        sigmoid(avg_slope10, k=0.5, c=0) * 0.35 +
        sigmoid(avg_slope60, k=0.35, c=0) * 0.30
    )

    # ── 4. 加速度/动能变化分 ──
    avg_acc_3_5 = np.mean([s["acc_3_5"] for s in stock_feats])
    avg_acc_5_10 = np.mean([s["acc_5_10"] for s in stock_feats])
    acc_score = (
        sigmoid(avg_acc_3_5, k=0.4, c=0) * 0.50 +
        sigmoid(avg_acc_5_10, k=0.3, c=0) * 0.50
    )

    # ── 5. 龙头效应/连板高度分（A股最核心动量因子，非线性优化）──
    lb_heights = [s.get("lb_height", 0) for s in stock_feats]
    max_lb = max(lb_heights) if lb_heights else 0
    avg_lb = np.mean(lb_heights) if lb_heights else 0
    
    # 非线性连板评分：更符合A股市场认知
    # 1板=5  2板=20  3板=50  4板=80  5板+=100
    # 真正龙头辨识度从3板开始，2板以下不具龙头效应
    def _lb_score(n_lb):
        if n_lb <= 0: return 0
        if n_lb == 1: return 5
        if n_lb == 2: return 20
        if n_lb == 3: return 50
        if n_lb == 4: return 80
        return 100  # 5板+
    
    # 龙头连板分
    leader_lb_score = _lb_score(max_lb) / 100.0
    # 板块连板密度加分（2板以上个股数量）
    multi_lb_count = sum(1 for lb in lb_heights if lb >= 2)
    density_bonus = min(multi_lb_count * 0.05, 0.20)  # 每多一个2板+5%，上限20%
    
    # 强势股密度
    strong_ratio = sum(1 for s in stock_feats if s.get("pct_chg", 0) >= 5) / n
    leader_density = linear(strong_ratio, 0, 0.25)
    
    leader_score = leader_lb_score * 0.50 + density_bonus * 0.25 + leader_density * 0.25

    # ── 6. 中军强度分（大市值资金参与度）──
    # 中军门槛：总市值>200亿，代表了机构和大资金的认可
    mid_cap_threshold = 2000000  # 200亿（万元）
    mid_cap_stocks = [s for s in stock_feats if s.get('total_mv', 0) >= mid_cap_threshold]
    mid_cap_active = sum(1 for s in mid_cap_stocks if s.get('zt_flag', 0) == 1 or s.get('pct_chg', 0) >= 5)
    mid_cap_force = mid_cap_active / max(len(mid_cap_stocks), 1) if mid_cap_stocks else 0
    mid_cap_force_score = linear(mid_cap_force, 0, 0.25)  # 25%中军活跃=满分
    
    # 中军数量加分：中军涨停/大涨的绝对数量
    mid_cap_count_bonus = min(mid_cap_active * 0.04, 0.12)  # 每只+4%，上限12%

    # ── 7. 回撤控制分 ──
    avg_dd = np.mean([s["max_dd_10"] for s in stock_feats])
    dd_score = linear(-avg_dd, -3, 12)

    # ── 8. 相对强度分 ──
    rel_ret = avg_ret_10 - market_index_ret
    rel_score = sigmoid(rel_ret, k=0.25, c=0)

    # ── 9. 当日动量分 ──
    pcts_today = [s["pct_chg"] for s in stock_feats]
    avg_pct_today = np.mean(pcts_today)
    up_n = sum(1 for p in pcts_today if p > 0)
    breadth_today = up_n / n if n > 0 else 0.5
    today_intensity = linear(avg_pct_today, -4, 4) * 0.5 + linear(breadth_today, 0.15, 0.85) * 0.5
    today_momentum_score = max(0.0, min(1.0, today_intensity))

    # ── 10. 跳空因子 ──
    gap_ups = [s.get("gap_up_pct", 0) for s in stock_feats]
    avg_gap = np.mean(gap_ups)
    gap_score = sigmoid(avg_gap, k=0.4, c=0)

    # ── 11. 金叉/死叉信号分 ──
    golden_cross_count = sum(1 for s in stock_feats if s.get("golden_cross", 0) == 1)
    dead_cross_count = sum(1 for s in stock_feats if s.get("dead_cross", 0) == 1)
    gc_bonus = min(golden_cross_count / max(n * 0.20, 1), 1.0) * 0.08
    dc_penalty = min(dead_cross_count / max(n * 0.20, 1), 1.0) * 0.05

    # ── 12. 突破位置分 ──
    breakout_count = sum(1 for s in stock_feats if s.get("high_20_b", -100) >= 0)
    breakout_ratio = breakout_count / n
    pos_score = linear(breakout_ratio, 0, 0.30)

    # ── 13. 炸板惩罚分 ──
    boom_count = sum(1 for s in stock_feats if s.get("boom_flag", 0) == 1)
    boom_ratio = boom_count / n
    boom_penalty = min(boom_ratio * 0.20, 0.08)

    # ── 当日调整（双向）──
    if avg_pct_today >= 3.0 and breadth_today >= 0.70:
        today_adjust = 1.08  # 大涨+普涨，加分8%
    elif avg_pct_today >= 1.5 and breadth_today >= 0.55:
        today_adjust = 1.03  # 温和上涨，加分3%
    elif avg_pct_today < -2.5 and breadth_today < 0.25:
        today_adjust = 0.85
    elif avg_pct_today < -1.5 and breadth_today < 0.35:
        today_adjust = 0.92
    elif avg_pct_today < -0.5 and breadth_today < 0.40:
        today_adjust = 0.97
    else:
        today_adjust = 1.0

    # ── 趋势确认条件 ──
    mid_trend_ok = (
        avg_slope60 > 0 and 
        avg_slope10 > 0 and 
        avg_ret_10 > -3
    )

    # ── 最终评分（V4 梯队优化权重）──
    score01 = (
        ret_score * 0.16 +             # 收益分 16%（降2%给中军）
        ma_score * 0.09 +              # 均线分 9%（降1%给中军）
        slope_score * 0.13 +           # 斜率分 13%（降1%给中军）
        acc_score * 0.06 +             # 加速度分 6%（降2%给中军）
        leader_score * 0.18 +          # 龙头发力分 18%（不变，核心）
        mid_cap_force_score * 0.07 +   # 中军活跃分 7%（新增！大资金参与度）
        dd_score * 0.04 +              # 回撤分 4%
        rel_score * 0.10 +             # 相对强度分 10%
        today_momentum_score * 0.11 +  # 当日动量分 11%（降1%）
        gap_score * 0.02 +             # 跳空因子 2%（降1%）
        pos_score * 0.02               # 突破位置因子 2%（降1%）
    ) * today_adjust + gc_bonus - dc_penalty - boom_penalty + mid_cap_count_bonus

    score01 = max(0.0, min(1.0, score01))

    detail = {
        "avg_ret_3": round(avg_ret_3, 2),
        "avg_ret_5": round(avg_ret_5, 2), "avg_ret_10": round(avg_ret_10, 2), "avg_ret_20": round(avg_ret_20, 2),
        "pct_above_ma5": round(pct_above_ma5 * 100, 1), "pct_above_ma10": round(pct_above_ma10 * 100, 1),
        "pct_above_ma20": round(pct_above_ma20 * 100, 1), "pct_above_ma60": round(pct_above_ma60 * 100, 1),
        "avg_slope_3": round(avg_slope3, 3), "avg_slope_10": round(avg_slope10, 3), "avg_slope_60": round(avg_slope60, 3),
        "avg_acc_3_5": round(avg_acc_3_5, 2), "avg_acc_5_10": round(avg_acc_5_10, 2),
        "max_lb": max_lb, "avg_lb": round(avg_lb, 1),
        "strong_ratio": round(strong_ratio * 100, 1), "leader_density": round(leader_density * 100, 1),
        "avg_max_dd_10": round(avg_dd, 2), "rel_ret_10": round(rel_ret, 2),
        "mid_trend_ok": 1 if mid_trend_ok else 0,
        "avg_pct_today": round(avg_pct_today, 2), "breadth_today": round(breadth_today * 100, 1),
        "today_adjust": today_adjust,
        "avg_gap": round(avg_gap, 2),
        "gc_bonus": round(gc_bonus * 100, 1),
        "dc_penalty": round(dc_penalty * 100, 1),
        "boom_penalty": round(boom_penalty * 100, 1),
        "leader_lb_score": round(leader_lb_score * 100, 1),
        "density_bonus": round(density_bonus * 100, 1),
        "pos_in_20_score": round(pos_score * 100, 1),
        "mid_cap_force": round(mid_cap_force * 100, 1),
        "mid_cap_active": mid_cap_active,
        "mid_cap_total": len(mid_cap_stocks),
        "mid_cap_count_bonus": round(mid_cap_count_bonus * 100, 1),
    }
    return round(score01 * 100, 1), detail


def calc_sentiment_score_v2(stock_feats, market_index_ret):
    """
    V4 梯队优化版情绪评分
    
    私募量化视角的梯队分析要点：
    1. 涨停梯队完整性（龙头+中军+跟风三层结构）决定题材持续力
       - 有高度板(3板+)=打开了空间 → 题材有想象力
       - 有中军涨停(市值>200亿)=大资金认可 → 题材有容量
       - 有跟风首板=情绪扩散 → 题材有广度
       - 三层齐全=最完整梯队，持续性最强
    2. 龙头质量影响持续性
       - 换手板龙头 > 一字板龙头（换手板有充分博弈，分歧转一致更强）
       - 一字板龙头多是一波流，开板即见顶
    3. 多只中军涨停=机构级别行情
       - 中军可以不涨停但趋势上涨，代表大资金持续布局
    """
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    if n == 0:
        return 0.0, {}

    pcts = [s["pct_chg"] for s in stock_feats]
    up_n = sum(1 for p in pcts if p > 0)
    down_n = sum(1 for p in pcts if p < 0)
    zt_n = sum(1 for s in stock_feats if s["zt_flag"] == 1)
    strong_n = sum(1 for s in stock_feats if s["strong_flag"] == 1)

    # ── 1. 广度分 ──
    breadth = up_n / n
    breadth_score = linear(breadth, 0.15, 0.80)

    # ── 2. 涨停分 ──
    zt_ratio = zt_n / n
    zt_score = linear(zt_ratio, 0, 0.25)

    # ── 3. 强势股分 ──
    strong_ratio = strong_n / n
    strong_score = linear(strong_ratio, 0, 0.50)

    # ── 4. 量比分 ──
    vol_ratios = [s.get("vol_ratio", 0) for s in stock_feats]
    avg_vol_ratio = float(np.nanmean(vol_ratios)) if vol_ratios else 0.0
    vol_score = linear(avg_vol_ratio, 0.6, 3.5)

    # ── 5. 换手率分 ──
    turnovers = [s.get("turnover", 0) for s in stock_feats]
    avg_turnover = float(np.nanmean(turnovers)) if turnovers else 0.0
    turnover_score = linear(avg_turnover, 0.5, 15.0)

    # ── 6. 盈亏效应分 ──
    median_pct = float(np.median(pcts))
    mean_pct = float(np.mean(pcts))
    profit_score = sigmoid(median_pct * 0.5 + mean_pct * 0.5, k=0.3, c=0)

    # ── 7. 共振评分（涨停连板共振）──
    top1 = max(pcts) if pcts else 0
    zt_effective = zt_n / max(n * 0.10, 1)
    resonance_raw = np.tanh(zt_effective * 0.8 + max(0, top1 - 7) / 10 * 0.2)
    resonance_score = min(resonance_raw, 1.0)

    # ── 8. 连板密度因子 ──
    lb_counts = [s.get("lb_height", 0) for s in stock_feats]
    multi_lb_count = sum(1 for lb in lb_counts if lb >= 2)
    max_lb = max(lb_counts) if lb_counts else 0
    lb_density = multi_lb_count / max(n * 0.05, 1)
    lb_score = min(np.tanh(lb_density * 0.6 + max_lb / 10 * 0.4), 1.0)

    # ── 9. 梯队完整度（龙头+中军+跟风三层结构，核心新增）──
    mid_cap_threshold = 2000000  # 200亿
    # 9a. 高度板梯队：3板+ 打开空间
    high_board_count = sum(1 for s in stock_feats if s.get('lb_height', 0) >= 3)
    has_high_board = 1 if high_board_count > 0 else 0
    high_board_bonus = min(high_board_count * 0.10, 0.20)  # 每只3板+加10%，上限20%
    # 9b. 中军涨停梯队：市值>200亿的涨停（大资金认可）
    mid_cap_zt_count = sum(1 for s in stock_feats if s.get('total_mv', 0) >= mid_cap_threshold and s.get('zt_flag', 0) == 1)
    mid_cap_strong_count = sum(1 for s in stock_feats if s.get('total_mv', 0) >= mid_cap_threshold and s.get('pct_chg', 0) >= 5 and s.get('zt_flag', 0) == 0)
    has_mid_cap_zt = 1 if mid_cap_zt_count > 0 else 0
    mid_cap_zt_bonus = min(mid_cap_zt_count * 0.08 + mid_cap_strong_count * 0.03, 0.20)
    # 9c. 跟风首板梯队：lb_height==1的涨停（情绪扩散）
    follower_zt_count = sum(1 for s in stock_feats if s.get('lb_height', 0) == 1 and s.get('zt_flag', 0) == 1)
    has_followers = 1 if follower_zt_count > 0 else 0
    follower_bonus = min(follower_zt_count * 0.04, 0.12)  # 每只首板+4%，上限12%
    # 9d. 梯队完整性基础分
    echelon_levels = has_high_board + has_mid_cap_zt + has_followers
    echelon_base = (echelon_levels / 3.0) ** 0.7  # 非线性 0.44/0.76/1.0，三层齐全更突出

    # ── 10. 龙头质量（换手板vs一字板）──
    # 连板2板+的个股中，换手率中位数越高=换手板质量越高
    leaders_2plus = [s for s in stock_feats if s.get('lb_height', 0) >= 2]
    if leaders_2plus:
        # 取最高连板股为龙头
        leaders_2plus.sort(key=lambda s: s.get('lb_height', 0), reverse=True)
        top_leader = leaders_2plus[0]
        leader_turnover = top_leader.get('turnover', 0)
        # 换手率判断：
        # <0.5% = 一字板（质量差，一波流）
        # 0.5-3% = 弱换手（一般）
        # 3-10% = 健康换手（好，有充分博弈）
        # >10% = 爆量分歧（分歧大，但换手龙）
        if leader_turnover < 0.5:
            leader_quality = 0.2  # 一字板
        elif leader_turnover < 3:
            leader_quality = linear(leader_turnover, 0.5, 3) * 0.5 + 0.2  # 0.2-0.7
        elif leader_turnover <= 10:
            leader_quality = linear(leader_turnover, 3, 10) * 0.3 + 0.7  # 0.7-1.0
        else:
            leader_quality = max(1.0 - (leader_turnover - 10) * 0.02, 0.5)  # 爆量分歧递减
    else:
        leader_quality = 0

    # ── 11. 情绪脆弱性调整（炸板率惩罚）──
    boom_count = sum(1 for s in stock_feats if s.get("boom_flag", 0) == 1)
    boom_ratio = boom_count / n if n > 0 else 0
    boom_penalty = min(max(boom_ratio - 0.10, 0) * 0.5, 0.10)

    # ── 11b. 封板质量检测 ──
    # 封板率=涨停未炸板比例，封板率低于60%说明封板意愿弱
    board_seal_rate = max(zt_n - boom_count, 0) / max(zt_n, 1) if zt_n > 0 else 1.0
    board_quality_penalty = min(max(0.6 - board_seal_rate, 0) * 0.30, 0.12)  # 封板率每低10%扣3%，上限12%

    # ── 基础情绪分（梯队优化权重）──
    base_score = (
        breadth_score * 0.15 +   # 18→15 广度
        zt_score * 0.15 +        # 20→15 涨停
        strong_score * 0.08 +    # 10→8  强势股
        vol_score * 0.05 +       # 5     量比
        turnover_score * 0.05 +  # 6→5   换手率
        profit_score * 0.05 +    # 6→5   盈亏效应
        resonance_score * 0.10 + # 12→10 共振
        lb_score * 0.10 +        # 12→10 连板密度
        echelon_base * 0.15      # 10→15 梯队完整度（提高权重）
    )

    # ── 12. 梯队综合加分（高度板+中军+跟风，龙头质量独立计算）──
    echelon_bonus = (
        high_board_bonus +       # 高度板加分
        mid_cap_zt_bonus +       # 中军涨停加分
        follower_bonus           # 跟风首板加分
    )

    # ── 13. 热榜加分（降低权重，梯队更重要）──
    hot_scores = [s.get("hot_rank_score", 0) for s in stock_feats]
    avg_hot_score = np.mean(hot_scores) if hot_scores else 0
    hot_bonus = sigmoid(avg_hot_score, k=0.35, c=4) * 0.07  # 10%→7%（梯队更重要）

    # ── 14. 极端情绪判定 ──
    climax_flag = 1 if zt_n >= 15 else 0

    # ── 龙头质量独立加分项（最高10%）──
    leader_quality_bonus = leader_quality * 0.10

    # ── 最终得分 ──
    score01 = min(base_score + hot_bonus + echelon_bonus + leader_quality_bonus - boom_penalty - board_quality_penalty, 1.0)
    score01 = max(0.0, score01)

    detail = {
        "up_ratio": round(breadth * 100, 1), "down_ratio": round(down_n / n * 100, 1),
        "zt_count": zt_n, "zt_ratio": round(zt_ratio * 100, 1),
        "strong_ratio": round(strong_ratio * 100, 1),
        "avg_vol_ratio": round(avg_vol_ratio, 2), "avg_turnover": round(avg_turnover, 2),
        "median_pct": round(median_pct, 2), "mean_pct": round(mean_pct, 2),
        "top1_pct": round(top1, 2), "resonance": round(resonance_raw, 3),
        "multi_lb_count": multi_lb_count, "max_lb": max_lb,
        "avg_hot_score": round(avg_hot_score, 1), "hot_bonus": round(hot_bonus * 100, 1),
        "boom_count": boom_count, "boom_penalty": round(boom_penalty * 100, 1),
        "climax_flag": climax_flag,
        # 梯队结构明细
        "high_board_count": high_board_count,
        "mid_cap_zt_count": mid_cap_zt_count,
        "mid_cap_strong_count": mid_cap_strong_count,
        "follower_zt_count": follower_zt_count,
        "echelon_levels": echelon_levels,
        "echelon_base": round(echelon_base * 100, 1),
        "leader_quality": round(leader_quality * 100, 1),
        "leader_quality_bonus": round(leader_quality_bonus * 100, 1),
        "board_seal_rate": round(board_seal_rate * 100, 1),
        "board_quality_penalty": round(board_quality_penalty * 100, 1),
        "echelon_bonus": round(echelon_bonus * 100, 1),
    }
    return round(score01 * 100, 1), detail


def calc_theme_state_v2(r, prev_data=None):
    """
    V3 优化版主题状态判断（A股主题轮动经验优化）
    
    优化要点：
    1. "分歧"条件提升：t_score>=55（从>=45），避免弱主题也被标记为分歧
    2. "退潮"新增趋势下降检测：当日趋势分明显低于前日时更敏感
    3. "启动"条件细化：3日涨幅+斜率向上+涨停大于1家
    4. "强趋势"新增连板要求：有连板才叫强趋势
    5. 退潮条件增加均值回归检测：高位回落+情绪消退
    """
    t_score = r.get("trend_score", 0)
    s_score = r.get("sentiment_score", 0)
    composite = r.get("composite_score", 0)
    td = r.get("trend_detail", {}) or {}
    sd = r.get("sentiment_detail", {}) or {}

    avg_ret_3 = td.get("avg_ret_3", 0)
    avg_ret_5 = td.get("avg_ret_5", 0)
    avg_ret_10 = td.get("avg_ret_10", 0)
    avg_pct_today = td.get("avg_pct_today", 0)
    pct_above_ma5 = td.get("pct_above_ma5", 0)
    avg_slope_3 = td.get("avg_slope_3", 0)
    avg_slope_10 = td.get("avg_slope_10", 0)
    avg_acc_3_5 = td.get("avg_acc_3_5", 0)
    max_lb = td.get("max_lb", 0)

    zt_count = sd.get("zt_count", 0)
    up_ratio = sd.get("up_ratio", 0)
    multi_lb_count = sd.get("multi_lb_count", 0)
    max_lb_senti = sd.get("max_lb", 0)

    prev_t_score = t_score
    prev_s_score = s_score
    prev_up_ratio = up_ratio
    if prev_data:
        prev_t_score = prev_data.get("trend_score", t_score)
        prev_s_score = prev_data.get("sentiment_score", s_score)
        prev_sd = prev_data.get("sentiment_detail", {}) or {}
        prev_up_ratio = prev_sd.get("up_ratio", up_ratio)
        prev_td = prev_data.get("trend_detail", {}) or {}
        prev_avg_slope_3 = prev_td.get("avg_slope_3", 0)
    else:
        prev_avg_slope_3 = 0

    # ── 1. 抱团主升 ──
    if (t_score >= 70 and s_score >= 70 and
        zt_count >= 3 and up_ratio >= 60 and
        (max_lb >= 3 or multi_lb_count >= 2)):
        return "抱团主升"

    # ── 2. 加速主升 ──
    if (t_score >= 55 and s_score >= 55 and
        avg_acc_3_5 > 0 and avg_slope_3 > avg_slope_10 * 0.8 and
        zt_count >= 2):
        return "加速主升"

    # ── 3. 强趋势（增加连板要求）──
    if t_score >= 55 and s_score >= 50 and max_lb >= 2:
        return "强趋势"

    # ── 4. 分歧转一致 ──
    if (prev_data and
        40 <= prev_t_score < 55 and
        t_score > prev_t_score + 3 and
        s_score > prev_s_score + 5 and
        up_ratio >= 60 and zt_count >= 2):
        return "分歧转一致"

    # ── 5. 启动（条件更精确）──
    if (35 <= t_score < 55 and
        avg_ret_3 > 2 and     # 3日涨幅>2%（略降低灵敏度）
        avg_slope_3 > 0 and   # 3日斜率向上
        zt_count >= 1 and     # 至少1只涨停（从2放松到1，抓早）
        avg_pct_today > -1):  # 当日不能大跌
        return "启动"

    # ── 6. 分歧（阈值提升，避免误判）──
    if (t_score >= 55 and      # 从>=45提升到>=55
        abs(avg_pct_today) < 2.0 and  # 放宽震荡范围
        up_ratio < 55 and zt_count > 0 and
        t_score < prev_t_score + 2):
        return "分歧"

    # ── 7. 退潮（增加趋势下降检测 + MA5位置保护）──
    # 当半数以上股票在MA5上方时，不判退潮（避免银行等低波动蓝筹误判）
    if pct_above_ma5 > 50:
        pass  # 跳过退潮判定
    else:
        trend_declining = (prev_data and t_score < prev_t_score - 3)
        if ((t_score < 45 and s_score < 40) or   # 双低
            (trend_declining and s_score < 45)):  # 趋势拐头+情绪消退
            if (avg_slope_10 < -0.03 or trend_declining) and (up_ratio < 35 or zt_count == 0):
                return "退潮"

    # ── 8. 弱趋势 ──
    if t_score >= 40 and s_score >= 35 and abs(avg_slope_10) < 0.05:
        return "弱趋势"

    # ── 9. 震荡/弱势 ──
    if t_score >= 40:
        return "震荡"
    else:
        return "弱势"


# ═══════════════════════════════════════════════════════════
# 阶段迁移预测 + 交易动作建议（6因子模型）
# ═══════════════════════════════════════════════════════════

# 宏观敏感主题列表（受宏观变量影响较大，需要特殊修正）
# 防御性主题：市场差→受益（资金避险），市场好→承压（资金流出）
DEFENSIVE_THEMES = {'黄金', '银行'}
# 周期性主题：市场差→承压，市场好→受益
CYCLICAL_THEMES = {'证券', '工业金属', '能源金属', '小金属', '稀土永磁', '煤炭'}
MACRO_SENSITIVE_THEMES = DEFENSIVE_THEMES | CYCLICAL_THEMES

# 状态迁移表（向上）
STATE_TRANSITION_UP = {
    '弱势': '启动', '震荡': '启动', '启动': '强趋势',
    '分歧': '分歧转一致', '分歧转一致': '强趋势',
    '强趋势': '加速主升', '加速主升': '抱团主升', '抱团主升': '抱团主升',
    '退潮': '震荡',
}

# 状态迁移表（向下）
STATE_TRANSITION_DOWN = {
    '弱势': '弱势', '震荡': '弱势', '启动': '震荡',
    '分歧': '退潮', '分歧转一致': '分歧',
    '强趋势': '分歧', '加速主升': '分歧', '抱团主升': '退潮',
    '退潮': '弱势',
}


def calc_phase_migration(r, market_ret_10, idx_df, prev_data=None, age_days=1):
    """
    阶段迁移预测引擎（6因子模型）

    预测未来3-5个交易日最可能发生的阶段迁移，结合资金与市场环境给出交易动作建议。

    6因子权重设计：
      1. Proximity（阶段距离）      25%  距离下一生命周期阈值还有多远
      2. Momentum（趋势加速度）     20%  动能是否支持迁移方向
      3. Confirmation（扩散确认）   15%  是否由板块整体而非个股推动
      4. Money Resonance（资金共振） 15%  成交额/资金/ETF是否同步改善
      5. Leader Health（龙头健康度） 15%  龙头趋势/相对强度/创新高能力
      6. Regime（市场适配）         10%  当前市场风格是否有利于该主题

    修正项：
      - Age Penalty: 热点持续时间过长时降低迁移概率
      - Macro Filter: 对宏观敏感主题进行宏观环境修正

    Parameters:
        r: 主题结果dict
        market_ret_10: 沪深300近10日收益
        idx_df: 指数K线DataFrame
        prev_data: 前一日主题数据
        age_days: 当前状态持续天数

    Returns:
        dict with migration_score, direction, target_state, trade_action, ...
    """
    td = r.get('trend_detail', {}) or {}
    sd = r.get('sentiment_detail', {}) or {}
    theme_state = r.get('theme_state', '弱势')
    t_score = r.get('trend_score', 0)
    s_score = r.get('sentiment_score', 0)

    # ────────────────────────────────────────
    # 1. Proximity 阶段距离 (25%)
    # ────────────────────────────────────────
    if theme_state in ('弱势', '震荡'):
        # 向上目标：启动 (t>=35, zt>=1, avg_ret_3>2)
        t_gap = max(0, (35 - t_score) / 35)
        zt_ok = 1.0 if sd.get('zt_count', 0) >= 1 else 0.0
        slope_ok = 1.0 if td.get('avg_slope_3', 0) > 0 else 0.0
        proximity_up = 0.3 * (1 - t_gap) + 0.4 * zt_ok + 0.3 * slope_ok
        proximity_down = 0.25  # 弱势向下空间有限
    elif theme_state in ('启动', '强趋势'):
        # 向上：接近强趋势/加速主升
        t_gap_up = max(0, (55 - t_score) / 55)
        s_gap_up = max(0, (55 - s_score) / 55)
        proximity_up = 0.5 * (1 - t_gap_up) + 0.5 * (1 - s_gap_up)
        # 向下：加速度转负或斜率转负
        acc_neg = 1.0 if td.get('avg_acc_3_5', 0) < 0 else 0.0
        slope_neg = 1.0 if td.get('avg_slope_3', 0) < 0 else 0.0
        proximity_down = 0.5 * acc_neg + 0.5 * slope_neg
    elif theme_state == '分歧':
        # 向上：分歧转一致（广度回升+涨停增加+趋势站稳）
        up_breadth = sd.get('up_ratio', 0) / 100.0
        proximity_up = 0.5 * up_breadth + 0.3 * min(sd.get('zt_count', 0) / 3.0, 1.0) + 0.2 * (1.0 if t_score > 50 else 0.0)
        # 向下：退潮（炸板+广度下降）
        boom_risk = 1.0 if sd.get('boom_count', 0) > 0 else 0.0
        proximity_down = 0.5 * (1.0 - up_breadth) + 0.5 * boom_risk
    elif theme_state in ('抱团主升', '加速主升'):
        # 高处不胜寒，向上空间有限
        proximity_up = 0.2
        proximity_down = 0.7
    elif theme_state == '退潮':
        # 可能继续退潮，或缩量企稳后反弹
        vol_shrink = 1.0 if sd.get('avg_vol_ratio', 1.0) < 1.0 else 0.0
        proximity_up = 0.3 * vol_shrink + 0.3 * (1.0 - min(abs(td.get('avg_slope_10', 0)) / 0.1, 1.0))
        proximity_down = 0.6  # 惯性向下
    else:
        proximity_up, proximity_down = 0.5, 0.5

    proximity_net = max(-1.0, min(1.0, proximity_up - proximity_down))

    # ────────────────────────────────────────
    # 2. Momentum 趋势加速度 (20%)
    # ────────────────────────────────────────
    acc_3_5 = td.get('avg_acc_3_5', 0)
    acc_5_10 = td.get('avg_acc_5_10', 0)
    momentum_raw = acc_3_5 * 0.6 + acc_5_10 * 0.4
    momentum = max(-1.0, min(1.0, momentum_raw / 3.0))

    # ────────────────────────────────────────
    # 3. Confirmation 扩散确认 (15%)
    # ────────────────────────────────────────
    up_ratio = sd.get('up_ratio', 0) / 100.0
    zt_ratio = sd.get('zt_ratio', 0) / 100.0
    strong_ratio = sd.get('strong_ratio', 0) / 100.0
    # 广度>60%+涨停+强势股 = 板块整体推动, 非个股行情
    confirmation_raw = up_ratio * 0.4 + zt_ratio * 0.3 + strong_ratio * 0.3
    confirmation = confirmation_raw * 2.0 - 1.0  # [0,1] → [-1,1]

    # ────────────────────────────────────────
    # 4. Money Resonance 资金共振 (15%)
    # ────────────────────────────────────────
    vol_ratio = sd.get('avg_vol_ratio', 1.0)
    # vol_ratio=1.0中性, >1.5放量=正, <0.7缩量=负
    money_resonance = max(-1.0, min(1.0, (vol_ratio - 1.0) / 1.0))

    # ────────────────────────────────────────
    # 5. Leader Health 龙头健康度 (15%)
    # ────────────────────────────────────────
    leader_quality = sd.get('leader_quality', 0) / 100.0  # 0-1
    max_lb = sd.get('max_lb', 0)
    multi_lb = sd.get('multi_lb_count', 0)
    # 健康换手龙头 + 高连板 + 多只连板 = 龙头梯队完整
    lh_quality = (leader_quality * 2.0 - 1.0) * 0.5  # [-1,1] 换手质量
    lh_height = min(max_lb / 5.0, 1.0) * 0.3           # [0,1] 连板高度
    lh_density = min(multi_lb / 3.0, 1.0) * 0.2         # [0,1] 连板密度
    leader_health = max(-1.0, min(1.0, lh_quality + lh_height + lh_density))

    # ────────────────────────────────────────
    # 6. Regime 市场适配 (10%)
    # ────────────────────────────────────────
    regime = 0.0
    if idx_df is not None and len(idx_df) > 5:
        idx_closes = idx_df['close'].astype(float).values
        if len(idx_closes) >= 6:
            idx_ret_5 = (idx_closes[-1] / idx_closes[-6] - 1.0) * 100.0
        else:
            idx_ret_5 = 0
        if len(idx_closes) >= 21:
            idx_ret_20 = (idx_closes[-1] / idx_closes[-21] - 1.0) * 100.0
        else:
            idx_ret_20 = 0
        regime = max(-1.0, min(1.0, (idx_ret_5 * 0.6 + idx_ret_20 * 0.4) / 10.0))

    # ────────────────────────────────────────
    # 综合迁移力
    # ────────────────────────────────────────
    migration_force = (
        proximity_net * 0.25 +
        momentum * 0.20 +
        confirmation * 0.15 +
        money_resonance * 0.15 +
        leader_health * 0.15 +
        regime * 0.10
    )

    # ─── 修正项1: Age Penalty 热点老化惩罚 ───
    age_penalty = 0.0
    if age_days > 5:
        age_penalty = -min((age_days - 5) * 0.02, 0.15)

    # ─── 修正项2: Macro Filter 宏观过滤 ───
    theme_name = r.get('theme', '')
    macro_filter = 0.0
    if theme_name in MACRO_SENSITIVE_THEMES:
        is_defensive = theme_name in DEFENSIVE_THEMES
        if regime < -0.2:
            # 市场差 → 周期性主题承压，防御性主题受益（避险资金流入）
            macro_filter = -0.15 if not is_defensive else 0.15
        elif regime > 0.3:
            # 市场好 → 周期性主题受益，防御性主题承压（资金流出防御板块）
            macro_filter = 0.10 if not is_defensive else -0.10
        # else: regime中性 → 无修正

    # 应用修正
    migration_force = max(-1.0, min(1.0, migration_force + age_penalty + macro_filter))

    # ────────────────────────────────────────
    # 确定迁移方向和目标状态
    # ────────────────────────────────────────
    if migration_force > 0.20:
        direction = 'upward'
        target_state = STATE_TRANSITION_UP.get(theme_state, theme_state)
    elif migration_force < -0.20:
        direction = 'downward'
        target_state = STATE_TRANSITION_DOWN.get(theme_state, theme_state)
    else:
        direction = 'sideways'
        target_state = theme_state

    confidence = abs(migration_force)
    migration_score = round(confidence * 100.0, 1)

    # ────────────────────────────────────────
    # 交易动作建议
    # ────────────────────────────────────────
    if direction == 'upward' and confidence > 0.35:
        trade_action = '买入加仓'
        action_reason = f'强向上迁移信号: {theme_state}→{target_state}, 6因子综合{migration_score}分'
    elif direction == 'upward':
        trade_action = '逢低布局'
        action_reason = f'弱向上迁移信号: {theme_state}→{target_state}, 因子{migration_score}分'
    elif direction == 'downward' and confidence > 0.35:
        trade_action = '卖出回避'
        action_reason = f'强下行风险: {theme_state}→{target_state}, 因子{migration_score}分'
    elif direction == 'downward':
        trade_action = '减仓'
        action_reason = f'弱下行风险: {theme_state}→{target_state}, 因子{migration_score}分'
    else:
        trade_action = '持有观望'
        action_reason = f'方向不明: {theme_state}→{target_state}, 等待信号确认'

    factors_detail = {
        'proximity': round(proximity_net * 100, 1),
        'momentum': round(momentum * 100, 1),
        'confirmation': round(confirmation * 100, 1),
        'money_resonance': round(money_resonance * 100, 1),
        'leader_health': round(leader_health * 100, 1),
        'regime': round(regime * 100, 1),
        'age_penalty': round(age_penalty * 100, 1),
        'macro_filter': round(macro_filter * 100, 1),
    }

    return {
        'migration_score': migration_score,
        'migration_direction': direction,
        'target_state': target_state,
        'trade_action': trade_action,
        'action_reason': action_reason,
        'migration_factors': factors_detail,
    }


# ─────────── CLI ───────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        run_v2_analysis(sys.argv[1])
    else:
        run_v2_analysis()
