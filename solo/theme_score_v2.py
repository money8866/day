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

import sys, os, json, csv, time, sqlite3, re
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


def get_moneyflow_thsc(trade_date, force_refresh=False):
    """拉取全市场同花顺资金流单日数据（moneyflow_ths），带 CSV 缓存

    moneyflow_ths 字段（均为净值，负=流出）：
      net_amount(总净额), buy_lg_amount(大单净额), buy_md_amount(中单净额), buy_sm_amount(小单净额)
    单位：万元。用于 V3 Rotation 引擎的 Fund（主力资金净流入）因子。
    """
    cache_path = os.path.join(CACHE_DIR, f"moneyflow_ths_{trade_date}.csv")
    if os.path.exists(cache_path) and not force_refresh:
        try:
            df = pd.read_csv(cache_path)
            if not df.empty:
                print(f"[Moneyflow] 缓存命中: {trade_date}, {len(df)} 条")
                return df
        except Exception:
            pass

    try:
        from theme_trend_sentiment_score import pro as _pro
    except Exception:
        _pro = None
    if _pro is None:
        print("[Moneyflow] 缺少 Tushare pro，跳过资金流")
        return pd.DataFrame()

    print(f"[Moneyflow] 拉取全市场资金流: {trade_date}")
    all_data = []
    offset = 0
    limit = 5000  # tushare 单次返回上限
    for _ in range(12):
        try:
            df = _pro.moneyflow_ths(trade_date=trade_date, offset=offset, limit=limit)
        except Exception as e:
            print(f"[Moneyflow] 拉取失败: {e}")
            break
        if df is None or df.empty:
            break
        all_data.append(df)
        if len(df) < limit:
            break
        offset += limit
        time.sleep(0.15)

    if not all_data:
        return pd.DataFrame()
    mf = pd.concat(all_data, ignore_index=True)
    if 'ts_code' in mf.columns:
        mf = mf.drop_duplicates(subset=['ts_code'])
    mf.to_csv(cache_path, index=False, encoding='utf-8-sig')
    print(f"[Moneyflow] 完成: {len(mf)} 条 → {os.path.basename(cache_path)}")
    return mf


def get_etf_kline(ts_code, start=None, end=None):
    """拉取 ETF 日线（fund_daily），带 SQLite 缓存

    ETF 是基金代码，pro.index_daily 对其返回空，必须用 pro.fund_daily。
    fund_daily.amount 单位：千元（与 daily 一致，/100000 后为亿元）。
    """
    if start is None:
        start = START_DATE
    if end is None:
        end = TRADE_DATE
    cached = cache_get("etf_kline", ts_code=ts_code, start=start, end=end)
    if cached is not None and 'trade_date' in cached.columns:
        max_date = str(cached['trade_date'].max())
        if max_date == str(end):
            if not cached['trade_date'].is_monotonic_increasing:
                cached = cached.sort_values('trade_date').reset_index(drop=True)
            return cached
        print(f"[ETF] 缓存过期（最新: {max_date}, 需要: {end}），重新拉取")

    try:
        from theme_trend_sentiment_score import pro as _pro
    except Exception:
        _pro = None
    if _pro is None:
        return pd.DataFrame()

    print(f"[ETF] 拉取 {ts_code} 数据: {start} ~ {end}")
    try:
        df = _pro.fund_daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception as e:
        print(f"[ETF] 拉取失败: {e}")
        return pd.DataFrame()
    time.sleep(0.15)
    if df is not None and not df.empty:
        df = df.sort_values('trade_date').reset_index(drop=True)
        cache_set("etf_kline", df, ts_code=ts_code, start=start, end=end)
        print(f"[ETF] 已缓存: {ts_code} ({len(df)} 条)")
    return df


ETF_NAME_MAP_FILE = os.path.join(CACHE_DIR, "etf_name_map.json")


def get_etf_name_map(force_refresh=False):
    """获取场内 ETF 代码→名称映射（fund_basic market='E'），缓存 JSON

    theme_config.json 的 main_etf 只有代码（如 159869.SZ），报告需显示真实
    ETF 名称（如"游戏ETF"）才能给出可执行的配置建议。
    """
    if os.path.exists(ETF_NAME_MAP_FILE) and not force_refresh:
        try:
            with open(ETF_NAME_MAP_FILE, 'r', encoding='utf-8') as f:
                m = json.load(f)
            if m:
                return m
        except Exception:
            pass

    try:
        from theme_trend_sentiment_score import pro as _pro
    except Exception:
        _pro = None
    if _pro is None:
        return {}

    print(f"[ETF] 拉取场内 ETF 名称列表...")
    try:
        fb = _pro.fund_basic(market='E', fields='ts_code,name')
    except Exception as e:
        print(f"[ETF] fund_basic 拉取失败: {e}")
        return {}
    if fb is None or fb.empty:
        return {}

    name_map = dict(zip(fb['ts_code'].astype(str), fb['name'].astype(str)))
    try:
        with open(ETF_NAME_MAP_FILE, 'w', encoding='utf-8') as f:
            json.dump(name_map, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    print(f"[ETF] ETF 名称缓存完成: {len(name_map)} 只")
    return name_map


def judge_etf_trend(ek):
    """基于 ETF K线判断自身趋势（多头/回踩/弱势），供配置建议参考

    数据来自 run_v2_analysis 预取的 etf_kline_map（fund_daily，已按日期升序）。
    """
    if ek is None or ek.empty or len(ek) < 20:
        return None
    try:
        close = ek['close'].astype(float).values
        cur = float(close[-1])
        ma5 = float(close[-5:].mean())
        ma10 = float(close[-10:].mean())
        ma20 = float(close[-20:].mean())
        ret5 = (cur / close[-6] - 1) * 100 if len(close) >= 6 else 0.0
    except Exception:
        return None
    if cur >= ma20 and ret5 > 1.0:
        state = '多头'
    elif cur >= ma20:
        state = '回踩'
    else:
        state = '弱势'
    return {'state': state, 'ret5': round(ret5, 1), 'above_ma20': cur >= ma20}


ETF_NAME_PREFIXES = ['华夏', '华富', '万家', '华宝', '易方达', '广发', '南方', '嘉实', '富国',
                     '汇添富', '国泰', '招商', '华泰柏瑞', '天弘', '博时', '鹏华', '工银',
                     '平安', '大成', '兴全', '银华', '景顺长城', '华安', '中欧', '建信',
                     '交银', '农银', '上投摩根', '诺安', '国联安', '海富通', '浦银安盛',
                     '长信', '新华', '中银', '方正', '东方', '泰康', '永赢', '国寿',
                     '前海开源', '西部利得', '创金合信', '德邦', '东财', '国金', '民生加银',
                     '摩根', '融通', '瑞银', '申万菱信', '泰达宏利', '太平', '同泰',
                     '兴业', '圆信永丰', '中加', '中信保诚', '中信建投', '朱雀']
ETF_INDEX_PREFIXES = ['中证', '上证', '深证', '国证', '创业板', '科创50', '科创', '沪深300', '中债']


def short_etf_name(name):
    """简化 ETF 全名（如"华夏中证动漫游戏ETF"→"动漫游戏ETF"），便于移动端展示"""
    if not name:
        return name
    for p in ETF_NAME_PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
            break
    for p in ETF_INDEX_PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
            break
    if not name.endswith('ETF'):
        name = name + 'ETF'
    return name


# ─────────── 主评分流程 ───────────
def run_v2_analysis(trade_date=None):
    """对 v2 映射运行主题评分分析"""
    global TRADE_DATE, START_DATE, TRADE_DATE_str

    if trade_date is None:
        trade_date = GLOBAL_TRADE_DATE
    TRADE_DATE = str(trade_date)  # 同步模块级 TRADE_DATE（get_etf_kline 等默认 end 参数依赖）
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

    # ── 6c. V3 Rotation 引擎数据准备 ──
    print("  准备 V3 资金流数据（moneyflow_ths）...")
    mf_df = get_moneyflow_thsc(TRADE_DATE_str)
    mf_map = {}
    if mf_df is not None and not mf_df.empty and 'ts_code' in mf_df.columns and 'net_amount' in mf_df.columns:
        mf_map = dict(zip(mf_df['ts_code'].astype(str), mf_df['net_amount'].astype(float)))
        print(f"  资金流可用: {len(mf_map)} 只")

    # 全市场活跃池成交额：所有主题成分股去重后的当日成交额之和（单位十亿）
    print("  计算全市场活跃池成交额...")
    pool_amount = 0.0
    for code in all_codes:
        kdf = kline_groups.get(code)
        if kdf is None or len(kdf) < 6:
            continue
        feat0 = per_stock_features_v2(kdf)
        if feat0 is None:
            continue
        pool_amount += float(feat0.get('amount_latest', 0) or 0)
    print(f"  活跃池成交额: {pool_amount:.1f} 亿元")

    # ETF K线缓存（各主题 main_etf 去重后预取，ETF 用 fund_daily）
    print("  预取主题 ETF K线...")
    etf_kline_map = {}
    for _key, _cfg in theme_config_map.items():
        _etf = _cfg.get('main_etf', '')
        if _etf and _etf not in etf_kline_map:
            try:
                _ek = get_etf_kline(_etf)
                if _ek is not None and not _ek.empty:
                    etf_kline_map[_etf] = _ek
            except Exception as _e:
                print(f"  [ETF] {_etf} 预取异常: {_e}")
    print(f"  ETF K线可用: {len(etf_kline_map)} 只")

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

        # ── V3 Rotation Engine 因子计算 ──
        etf_kline = etf_kline_map.get(cfg.get('main_etf', ''), None)
        fund_score, fund_detail = calc_fund_score_v3(all_rows, mf_map, pool_amount, etf_kline=etf_kline)
        breadth_score, breadth_detail = calc_breadth_score_v3(all_rows)
        leader_score, leader_detail = calc_leader_score_v3(all_rows, market_ret_10)
        persistence = calc_persistence_v3(t_detail)
        heat_v3 = hot_percentile  # Heat 用热榜百分位（0-100）
        # Strength = 30%Trend + 25%Emotion + 20%Fund + 15%Breadth + 10%Leader
        strength = round(0.30 * t_score + 0.25 * s_score + 0.20 * fund_score +
                         0.15 * breadth_score + 0.10 * leader_score, 1)
        # MTI = 25%Strength + 25%Fund + 20%Leader + 15%Heat + 15%Persistence
        mti = calc_mti_v3(strength, fund_score, leader_score, heat_v3, persistence)
        mti_level = get_mti_level(mti)

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
            # ── V3 Rotation Engine 字段 ──
            'fund_score': fund_score, 'fund_detail': fund_detail,
            'breadth_score': breadth_score, 'breadth_detail': breadth_detail,
            'leader_v3_score': leader_score, 'leader_v3_detail': leader_detail,
            'persistence': persistence, 'heat_v3': heat_v3,
            'strength_score': strength, 'mti': mti, 'mti_level': mti_level,
            # 主线穿透用：完整成份股特征（全量 rows，非 top30）
            'stock_rows': rows,
            # 主线类型分级用：主ETF代码（ETF共振维度）
            'main_etf': cfg.get('main_etf', ''),
        })
        rows_per_theme[theme_name] = top_rows

    # 排序
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r['rank'] = i

    # ── MTI 横截面分层（修复压缩：让主线/准主线/轮动真正拉开层次）──
    # 原因：Strength/Fund 分项被压缩在 0~50，绝对加权 MTI 永远到不了 65/80 阈值
    #       （8/3 全部主题 MTI 挤在 18.8~54.8，16/28 个"非主线"）
    # 方案：MTI = 30%×原始加权分 + 70%×五因子横截面百分位加权分
    #       保留绝对水平发言权的同时，保证每期最强主题能进入主线/准主线档
    print("  计算 MTI 横截面分层...")
    mti_factor_keys = ['strength_score', 'fund_score', 'leader_v3_score', 'heat_v3', 'persistence']
    mti_rank_map = {}
    for fk in mti_factor_keys:
        vals = pd.Series([r.get(fk, 0) for r in results])
        mti_rank_map[fk] = vals.rank(pct=True).values * 100.0
    for i, r in enumerate(results):
        raw_mti = r.get('mti', 0)
        rank_mti = (0.25 * mti_rank_map['strength_score'][i] +
                    0.25 * mti_rank_map['fund_score'][i] +
                    0.20 * mti_rank_map['leader_v3_score'][i] +
                    0.15 * mti_rank_map['heat_v3'][i] +
                    0.15 * mti_rank_map['persistence'][i])
        mti_new = 0.3 * raw_mti + 0.7 * rank_mti
        r['mti'] = round(mti_new, 1)
        r['mti_level'] = get_mti_level(mti_new)

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

    # ── V3 Rotation Engine：生命周期分类 + Trade Score + Final 修正 ──
    print("  计算 V3 生命周期与交易得分...")
    # 主线细分穿透：加载子主题映射（仅主线执行）
    subtheme_map = _load_subtheme_map_v2()
    for r in results:
        lifecycle = classify_v3_lifecycle(r)
        r['lifecycle'] = lifecycle
        # FundAcc = 主力净流入强度（净流入占比 1%~3% 为强）
        net_ratio = (r.get('fund_detail', {}) or {}).get('fund_net_ratio', 0)
        fund_acc = max(0.0, min(100.0, linear(net_ratio, -0.01, 0.03) * 100))
        r['fund_acc'] = round(fund_acc, 1)
        leader_quality = (r.get('sentiment_detail', {}) or {}).get('leader_quality', 50)
        # Base Trade Score = 30%Strength + 25%Stage + 20%Transition + 15%LeaderQ + 10%FundAcc
        base_trade = calc_trade_score_v3(
            r.get('strength_score', 50), lifecycle,
            r.get('migration_score', 50), leader_quality, fund_acc,
        )
        r['base_trade_score'] = round(base_trade, 1)
        # 小主题可信度修正：Adjusted = Trade×Confidence + History×(1-Confidence)
        conf = calc_confidence_v3(r['n_stocks'])
        r['confidence'] = round(conf, 3)
        history = r.get('persistence', 50)  # History Score 用持续性分代理
        if lifecycle in ('高潮', '退潮'):
            # 高潮/退潮主题禁止通过持续性高分通道回补（否则防御性长牛板块如黄金，
            # 退潮时仍凭高持续性霸榜），直接按基础交易分，确保"退潮坚决回避"
            adjusted = base_trade
        else:
            adjusted = base_trade * conf + history * (1 - conf)
        # 成长性因子
        growth = get_growth_factor(r['theme'])
        r['growth_factor'] = growth
        # Lifecycle 乘法修正（硬主导）：退潮×0.55 / 高潮×0.75 / 升温×1.10 / 启动×1.15
        lc_mult = LC_TRADE_MULT.get(lifecycle, 1.0)
        r['lc_mult'] = lc_mult
        r['final_trade_score'] = round(adjusted * growth * lc_mult, 1)
        # 基于 Trade 而非 Strength 的推荐排序
        r['trade_rank'] = 0
        # V3 实盘交易动作 + 建议仓位（覆盖原迁移引擎的简单 trade_action）
        act = calc_trade_action_v3(r)
        r.update(act)
        # A股主线类型分级 V1.0（MainlineType / MainlineQuality / TradingStyle）
        r.update(calc_mainline_type_v3(r, etf_kline_map))
        # 主线细分穿透：仅对核心主线执行（最佳子主题 / 龙头 / 中军）
        is_m, _mtype = _is_mainline(r)
        if is_m:
            pen = analyze_mainline_penetration(
                r['theme'], r.get('stock_rows', []), theme_stock_map, subtheme_map, mf_map)
            if pen:
                r['penetration'] = pen

    # Trade 排序（交易优先级）
    results_trade_sorted = sorted(results, key=lambda x: x['final_trade_score'], reverse=True)
    for i, r in enumerate(results_trade_sorted, 1):
        r['trade_rank'] = i

    # 风格分析（top5）
    style_result = analyze_style_trend(results[:5])

    # ─── 7. 保存结果 ───
    print("\n[6/6] 保存结果...")

    # CSV（v2 独立文件）
    save_to_csv_v2(results)

    # SQLite
    save_to_sqlite_v2(results)

    # 文本报告（V3 规范）
    save_to_text_report_v2(results, kg_v3_cfg, en_to_cn,
                           market_ret_10=market_ret_10, etf_kline_map=etf_kline_map)

    # 打印排名
    print(f"\n{'='*100}")
    print(f"主题评分排名 V2 - {TRADE_DATE_str}")
    print(f"{'='*100}")
    print(f"{'排名':<4} {'主题':<12} {'趋势':<6} {'情绪':<6} {'综合':<6} {'涨停':<4} {'迁移分':<6} {'目标状态':<10} {'建议仓位':<10} {'实盘交易动作':<20}")
    print(f"{'-'*100}")
    for r in results[:20]:  # 只打印前20
        sd = r.get('sentiment_detail', {}) or {}
        zt = sd.get('zt_count', 0)
        state = r.get('target_state', '')
        mig = r.get('migration_score', 0)
        action = r.get('trade_action', '')
        pos = r.get('position_label', '')
        pos_pct = r.get('position_pct', 0)
        pos_str = f"{pos}({pos_pct:.0f}%)" if pos_pct > 0 else pos
        print(f"{r['rank']:<4} {r['theme']:<12} {r['trend_score']:<6.1f} {r['sentiment_score']:<6.1f} {r['composite_score']:<6.1f} {zt:<4} {mig:<6.1f} {state:<10} {pos_str:<10} {action:<20}")

    print(f"\n完成! 共 {len(results)} 个主题评分")
    return results


# ═══════════════════════════════════════════════════════════
# V3 Rotation Engine（MTI / Strength / Fund / Breadth / Leader / Lifecycle / Trade Score）
# 规格依据：A股机构主题轮动分析系统（Theme Rotation Engine V3）
# ═══════════════════════════════════════════════════════════

# 成长性因子（Growth Factor）：避免无成长性的低弹性防御/周期板块长期霸榜
GROWTH_RULES = [
    ("AI", 1.00), ("算力", 1.00), ("机器人", 1.00), ("低空", 1.00), ("商业航天", 1.00),
    ("军工", 1.00), ("智能驾驶", 1.00), ("半导体", 1.00), ("游戏", 1.00),
    ("新能源", 0.95), ("光伏", 0.95), ("储能", 0.95), ("汽车", 0.95), ("核聚变", 0.95),
    ("周期", 0.90), ("有色", 0.90), ("稀土", 0.90), ("战略", 0.90), ("工业金属", 0.90),
    ("煤炭", 0.90), ("钢铁", 0.90), ("石油", 0.90), ("化工", 0.90),
    ("红利", 0.85), ("银行", 0.85), ("公用", 0.85), ("电力", 0.85), ("证券", 0.85), ("保险", 0.85),
    ("消费", 0.80), ("食品", 0.80), ("白酒", 0.80), ("医药", 0.80), ("医疗", 0.80),
    ("农牧", 0.80), ("家电", 0.80), ("零售", 0.80),
]


def get_growth_factor(theme_name):
    """按主题名关键词匹配成长性因子，默认 0.95"""
    for kw, gf in GROWTH_RULES:
        if kw in theme_name:
            return gf
    return 0.95


def calc_fund_score_v3(rows, mf_map, pool_amount, etf_kline=None):
    """Fund 资金流向分（0-100）
    = 40%成交额增速 + 30%主力资金净流入 + 20%ETF资金变化 + 10%成交额占活跃池比例
    moneyflow_ths: net_amount 单位万元（负=流出）；amount_latest 单位十亿。
    """
    amt_today = sum(float(r.get('amount_latest', 0) or 0) for r in rows)
    amt_ma5 = sum(float(r.get('amount_ma5', 0) or 0) for r in rows)
    growth = (amt_today / amt_ma5 - 1) if amt_ma5 > 0 else 0.0
    growth_score = linear(growth, 0.0, 0.5) * 100

    # 主力净流入：moneyflow_ths net_amount 单位万元 → 亿元；与成交额统一口径
    # 注意：amount_latest = tushare daily.amount(千元)/100000 = 亿元
    net_sum = 0.0
    mf_hit = 0
    for r in rows:
        mf = mf_map.get(r.get('ts_code', ''))
        if mf is not None:
            net_sum += mf
            mf_hit += 1
    amt_yi = amt_today  # 亿元
    net_ratio = (net_sum / 10000.0) / amt_yi if amt_yi > 0 else 0.0
    net_score = linear(net_ratio, -0.02, 0.04) * 100

    # ETF 资金变化：ETF K线成交额增速（无 ETF 数据用主题成交额增速替代）
    etf_score = growth_score
    if etf_kline is not None and len(etf_kline) >= 6:
        try:
            etf_amt = etf_kline['amount'].astype(float).values
            e_now = etf_amt[-1]
            e_ma5 = etf_amt[-6:-1].mean()
            e_growth = (e_now / e_ma5 - 1) if e_ma5 > 0 else 0.0
            etf_score = linear(e_growth, -0.2, 0.5) * 100
        except Exception:
            pass

    # 成交额占活跃池比例
    share = (amt_today / pool_amount) if pool_amount > 0 else 0.0
    share_score = linear(share, 0.02, 0.15) * 100

    fund = 0.4 * growth_score + 0.3 * net_score + 0.2 * etf_score + 0.1 * share_score
    return round(max(0.0, min(100.0, fund)), 1), {
        'fund_growth': round(growth, 3), 'fund_net_ratio': round(net_ratio, 4),
        'fund_net': round(net_sum / 10000.0, 2), 'fund_mf_hit': mf_hit,
        'fund_etf_score': round(etf_score, 1), 'fund_share': round(share, 4),
        'fund_score': round(fund, 1),
    }


def calc_breadth_score_v3(rows):
    """Breadth 赚钱效应广度（0-100）：上涨25% + 创新高25% + MA20 25% + 放量25%"""
    n = len(rows) or 1
    up = sum(1 for r in rows if (r.get('pct_chg', 0) or 0) > 0) / n
    nh = sum(1 for r in rows if r.get('new_high_flag', 0) == 1) / n
    ma20 = sum(1 for r in rows if r.get('above_ma20_flag', 0) == 1) / n
    vl = sum(1 for r in rows if (r.get('vol_ratio_today', 0) or 0) > 1.2) / n
    score = 100 * (0.25 * up + 0.25 * nh + 0.25 * ma20 + 0.25 * vl)
    return round(max(0.0, min(100.0, score)), 1), {
        'breadth_up': round(up * 100, 1), 'breadth_new_high': round(nh * 100, 1),
        'breadth_ma20': round(ma20 * 100, 1), 'breadth_vol': round(vl * 100, 1),
    }


def _lb_score_v3(lb):
    """连板非线性评分：1板15 / 2板40 / 3板70 / 4板90 / 5板+100"""
    if lb <= 0:
        return 0
    return {1: 15, 2: 40, 3: 70, 4: 90}.get(lb, 100)


def calc_leader_score_v3(rows, market_ret):
    """Leader 龙头效应（0-100）：龙头涨停20% + 成交额20% + RS 20% + 连续性20% + 新高20%"""
    if not rows:
        return 0.0, {}
    # 龙头识别：2连板优先，其次大成交上涨股
    leaders = [r for r in rows if r.get('lb_height', 0) >= 2]
    if not leaders:
        leaders = sorted([r for r in rows if (r.get('pct_chg', 0) or 0) > 0],
                         key=lambda x: x.get('amount_latest', 0), reverse=True)
    if not leaders:
        leaders = sorted(rows, key=lambda x: x.get('amount_latest', 0), reverse=True)
    ld = leaders[0]
    lb = ld.get('lb_height', 0)
    lb_score = _lb_score_v3(lb)
    amt_score = min(float(ld.get('amount_latest', 0) or 0) / 10.0, 1.0) * 100  # 10亿成交满分
    rs = (ld.get('pct_chg', 0) or 0) - market_ret
    rs_score = linear(rs, -3, 12) * 100
    cont_score = min(lb * 20 + max(ld.get('ret_5', 0) or 0, 0) * 3, 100)
    nh_score = 100 if ld.get('new_high_flag', 0) == 1 else 0
    score = 0.2 * lb_score + 0.2 * amt_score + 0.2 * rs_score + 0.2 * cont_score + 0.2 * nh_score
    return round(max(0.0, min(100.0, score)), 1), {
        'leader_v3': ld.get('name', ''), 'leader_lb_v3': lb,
        'leader_amt_v3': round(float(ld.get('amount_latest', 0) or 0), 2),
        'leader_rs_v3': round(rs, 2), 'leader_new_high_v3': ld.get('new_high_flag', 0),
    }


def calc_persistence_v3(t_detail):
    """Persistence 持续性（0-100）：5日40% + 10日30% + 10日斜率20% + 加速度10%"""
    r5 = t_detail.get('avg_ret_5', 0) or 0
    r10 = t_detail.get('avg_ret_10', 0) or 0
    slope = t_detail.get('avg_slope_10', 0) or 0
    acc = t_detail.get('avg_acc_5_10', 0) or 0
    score = (linear(r5, -2, 6) * 0.4 + linear(r10, -5, 12) * 0.3 +
             linear(slope, -0.5, 0.5) * 0.2 + linear(acc, -2, 4) * 0.1) * 100
    return round(max(0.0, min(100.0, score)), 1)


def classify_v3_lifecycle(r):
    """六大生命周期分类（唯一）：高潮 > 退潮 > 分歧 > 主升 > 升温 > 启动

    全部基于 V3 因子判定（不依赖 V2 theme_state 状态机），避免"趋势弱但情绪强"
    的题材（如 AI算力 回撤后当日反抽）被兜底误判为退潮。
    """
    trend = r.get('trend_score', 0)
    emotion = r.get('sentiment_score', 0)
    sd = r.get('sentiment_detail', {}) or {}
    fund = r.get('fund_score', 0)
    breadth = r.get('breadth_score', 0)
    max_lb = sd.get('max_lb', 0)
    boom = sd.get('boom_count', 0)
    climax = sd.get('climax_flag', 0)
    up_ratio = sd.get('up_ratio', 0)
    zt_ratio = sd.get('zt_ratio', 0)
    hot_phase = r.get('hot_phase', '')

    # 1. 高潮：情绪极致化（涨停密度极高 / 连板极高 / 热榜顶峰 / 涨停绝对数≥15）
    if climax == 1 or (max_lb >= 3 and zt_ratio >= 0.025) or hot_phase == '高潮':
        return '高潮'
    # 2. 退潮：趋势+情绪双弱 且 赚钱效应消失（上涨率<40%）
    if trend < 35 and emotion < 35 and up_ratio < 40:
        return '退潮'
    # 3. 分歧：炸板增加（涨停被砸）→ 情绪与趋势不协调
    if boom >= 2:
        return '分歧'
    # 4. 主升：趋势与情绪共振
    if trend >= 55 and emotion >= 50:
        return '主升'
    # 5. 升温：广度扩散（普涨）+ 趋势或情绪达标
    if up_ratio >= 70 and (trend >= 45 or emotion >= 45):
        return '升温'
    # 6. 启动：趋势初现（趋势站上40）或 情绪活跃+上涨过半
    if trend >= 40 or (emotion >= 55 and up_ratio >= 55):
        return '启动'
    # 7. 低强度分歧：情绪与趋势不协调但未死
    if emotion >= 40 and trend >= 30:
        return '分歧'
    # 8. 兜底：三弱 → 退潮
    return '退潮'


# 生命周期阶段加减分（Base Trade Score 的 Stage 项）
# '分歧' 与 '分歧转一致' 同档：分歧即分歧转一致的前置买点（逢低低吸），故共享同档
# V3 升级：Bonus 区间拉大（-25 ~ +22），配合 Stage 权重 35%，让生命周期真正主导 Trade Score
STAGE_BONUS = {'启动': 22, '升温': 16, '分歧': 12, '分歧转一致': 12, '主升': 8, '震荡': 0, '高潮': -10, '退潮': -25}

# 生命周期乘法修正（最终 Trade Score 的硬主导）
# 即使退潮主题强度/资金极高，×0.55 后也难超越普通升温主题（×1.10）
LC_TRADE_MULT = {'启动': 1.15, '升温': 1.10, '分歧': 1.05, '分歧转一致': 1.05, '主升': 1.00, '震荡': 0.90, '高潮': 0.75, '退潮': 0.55}


def calc_stage_score_v3(lifecycle):
    bonus = STAGE_BONUS.get(lifecycle, 0)
    return max(0.0, min(100.0, 50.0 + bonus))


def calc_trade_score_v3(strength, lifecycle, migration_score, leader_quality, fund_acc):
    """Base Trade Score = 25%Strength + 35%Stage + 15%Transition + 15%LeaderQ + 10%FundAcc

    V3 升级：Stage 权重升至 35%（原 25%），Lifecycle 成为 Base Trade 的第一权重。
    """
    stage = calc_stage_score_v3(lifecycle)
    base = strength * 0.25 + stage * 0.35 + migration_score * 0.15 + leader_quality * 0.15 + fund_acc * 0.10
    return max(0.0, min(100.0, base))


# ═══════════════════════════════════════════════════════════
# A股主线分级 V1.0（MainlineType / MainlineQuality / TradingStyle）
# 不重构现有评分体系，只增加一层分类判断；六维度独立计算后判定主线类型
# ═══════════════════════════════════════════════════════════
def _ml_clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def calc_mainline_type_v3(r, etf_kline_map=None):
    """主线类型分级 V1.0

    独立计算 6 维度（不改变原 ThemeScore/TrendScore/LifecycleScore）：
      ml_emotion_score        情绪强度：涨停数量30% + 连板高度30% + 情绪分25% + 涨停密度15%
      ml_trend_score          趋势强度：现有趋势分
      ml_capital_persistence  资金持续性：资金分60% + 持续性40%
      ml_leader_score         龙头强度：龙头效应70% + 龙头连板30%
      ml_center_score         中军强度：大市值核心(>200亿)上涨率40% + 成交25% + 相对强度35%
      ml_resonance_score      ETF/行业共振：ETF自身趋势50% + 板块趋势50%

    类型判定（简单规则）：
      情绪强>趋势强 → 情绪主线
      趋势强>情绪强 → 趋势主线
      情绪强+趋势强 → 情绪+趋势共振（必须同时 龙头强+中军强+资金持续+板块趋势强，否则退化为单类型）
      双弱          → 非主线（不得进入核心推荐）

    MainlineQuality（主线质量，独立于类型）：
      90+ 核心主线 / 80-89 强主线 / 70-79 次主线 / 60-69 轮动主题 / <60 非主线

    TradingStyle（主线类型决定交易方式，结合生命周期）：
      情绪主线→龙头/前排优先（注意高位风险）
      趋势主线→中军/核心优先，回踩低吸
      情绪+趋势共振→优先级最高，可同时做龙头和中军
      轮动/非主线→降低仓位，不追涨
    """
    sd = r.get('sentiment_detail', {}) or {}
    zt = int(sd.get('zt_count', 0) or 0)
    max_lb = int(sd.get('max_lb', 0) or 0)
    zt_ratio = float(sd.get('zt_ratio', 0) or 0)
    sentiment = float(r.get('sentiment_score', 0) or 0)
    trend = float(r.get('trend_score', 0) or 0)
    fund = float(r.get('fund_score', 0) or 0)
    persist = float(r.get('persistence', 0) or 0)
    leader_v3 = float(r.get('leader_v3_score', 0) or 0)

    # 1) 情绪强度：涨停数量(30%) + 连板高度(30%) + 情绪分(25%) + 涨停密度(15%)
    zt_score = _ml_clamp(zt / 10.0 * 100)              # 10家涨停满分
    lb_score = _lb_score_v3(max_lb)                     # 复用连板非线性评分（1板15/4板90/5板+100）
    zt_density_score = _ml_clamp(zt_ratio * 400)        # 2.5% 涨停密度满分（与高潮判定口径一致）
    emotion = round(0.30 * zt_score + 0.30 * lb_score + 0.25 * sentiment + 0.15 * zt_density_score, 1)

    # 2) 趋势强度：现有趋势分
    trend_s = round(_ml_clamp(trend), 1)

    # 3) 资金持续性：资金强度(60%) + 持续性(40%)
    capital = round(0.60 * fund + 0.40 * persist, 1)

    # 4) 龙头强度：龙头效应(70%) + 龙头连板高度(30%)
    leader = round(0.70 * leader_v3 + 0.30 * lb_score, 1)

    # 5) 中军强度：大市值核心(>200亿)整体走强（上涨率/成交额/相对强度）
    rows = r.get('stock_rows', []) or []
    big = [x for x in rows if (x.get('total_mv', 0) or 0) > 2000000]  # 市值>200亿(万元)
    if big:
        up_r = sum(1 for x in big if (x.get('pct_chg', 0) or 0) > 0) / len(big)
        amt_sum = sum(float(x.get('amount_latest', 0) or 0) for x in big)  # 亿元
        avg_pct = sum(float(x.get('pct_chg', 0) or 0) for x in big) / len(big)
        center = 100 * (0.40 * up_r + 0.25 * _ml_clamp(amt_sum / 80.0, 0, 1) + 0.35 * linear(avg_pct, -2, 4))
    else:
        center = 30.0  # 无大市值核心 → 中军弱
    center = round(_ml_clamp(center), 1)

    # 6) ETF/行业共振：ETF自身趋势(50%) + 板块趋势(50%)；无ETF数据用板块趋势兜底
    etf_state = None
    main_etf = r.get('main_etf', '')
    if etf_kline_map and main_etf:
        etf_state = judge_etf_trend(etf_kline_map.get(main_etf, None))
    if etf_state is not None:
        etf_base = 100 if etf_state['state'] == '多头' else (60 if etf_state['state'] == '回踩' else 25)
        etf_ret = float(etf_state.get('ret5', 0) or 0)
        etf_trend_score = _ml_clamp(0.7 * etf_base + 0.3 * linear(etf_ret, -3, 5) * 100)
    else:
        etf_trend_score = trend_s
    resonance = round(0.50 * etf_trend_score + 0.50 * trend_s, 1)

    # ── 类型判定 ──
    # 共振必须同时：龙头强 + 中军强 + 资金持续 + 板块趋势强 + ETF/行业共振
    # 阈值从严（龙头60/中军55/资金55/共振55），避免"涨停多就判共振"
    e_strong = emotion >= 50
    t_strong = trend_s >= 55
    l_strong = leader >= 60
    c_strong = center >= 55
    cap_strong = capital >= 55
    r_strong = resonance >= 55
    if e_strong and t_strong:
        # 情绪+趋势共振：必须同时 龙头强+中军强+资金持续+板块趋势强+行业共振
        if l_strong and c_strong and cap_strong and r_strong:
            mainline_type = '情绪+趋势共振'
        else:
            # 双强但缺共振要素 → 退化为强势侧主导的单类型
            mainline_type = '情绪主线' if emotion >= trend_s else '趋势主线'
    elif e_strong:
        mainline_type = '情绪主线'
    elif t_strong:
        mainline_type = '趋势主线'
    else:
        mainline_type = '非主线'

    # ── 主线质量（独立于类型）──
    if mainline_type == '情绪+趋势共振':
        quality = 0.30 * emotion + 0.30 * trend_s + 0.15 * capital + 0.10 * leader + 0.10 * center + 0.05 * resonance
    elif mainline_type == '情绪主线':
        quality = 0.40 * emotion + 0.15 * trend_s + 0.15 * capital + 0.15 * leader + 0.10 * center + 0.05 * resonance
    elif mainline_type == '趋势主线':
        quality = 0.15 * emotion + 0.40 * trend_s + 0.15 * capital + 0.10 * leader + 0.15 * center + 0.05 * resonance
    else:
        quality = 0.25 * emotion + 0.25 * trend_s + 0.20 * capital + 0.10 * leader + 0.10 * center + 0.10 * resonance
    quality = round(_ml_clamp(quality), 1)

    def _qlabel(q):
        if q >= 90:
            return '核心主线'
        if q >= 80:
            return '强主线'
        if q >= 70:
            return '次主线'
        if q >= 60:
            return '轮动主题'
        return '非主线'

    # ── 交易方式（类型决定方式，结合生命周期）──
    lc = r.get('lifecycle', '')
    if mainline_type == '情绪+趋势共振':
        style = {'主升': '龙头+中军', '升温': '龙头+中军', '启动': '龙头+中军',
                 '分歧': '等回踩', '高潮': '龙头打高度/注意兑现', '退潮': '空仓等待'}.get(lc, '龙头+中军')
    elif mainline_type == '情绪主线':
        style = {'主升': '龙头接力/分歧低吸', '高潮': '龙头/分歧低吸·防高位兑现',
                 '升温': '龙头/前排跟随', '启动': '龙头/前排跟随',
                 '分歧': '分歧低吸', '退潮': '空仓等待'}.get(lc, '龙头/分歧低吸')
    elif mainline_type == '趋势主线':
        style = {'主升': '趋势低吸/回踩MA20', '升温': '中军/核心低吸',
                 '启动': '中军/核心低吸', '分歧': '回踩确认',
                 '高潮': '持有/逐步减仓', '退潮': '观望'}.get(lc, '趋势低吸')
    else:
        style = '降低仓位/不追涨'

    return {
        'mainline_type': mainline_type,
        'mainline_quality': quality,
        'mainline_quality_label': _qlabel(quality),
        'trading_style': style,
        'ml_emotion_score': emotion,
        'ml_trend_score': trend_s,
        'ml_capital_persistence': capital,
        'ml_leader_score': leader,
        'ml_center_score': center,
        'ml_resonance_score': resonance,
    }


def calc_trade_action_v3(r):
    """实盘交易动作 + 建议仓位（QMT/CTP 对接用）

    输入字段（来自 result dict）：
      trend_score / sentiment_score / composite_score / zt_count / migration_score / lifecycle(target_state)
    输出：dict { trade_action, position_pct, position_label, suggested_position, action_reason }

    动作优先级（严格从上到下）：
      1. 退潮期优先     : Status==退潮 或 Composite<30 → 坚决止损
      2. 启动/升温修复   : Status∈{启动,升温} 且 Migration>=15 → 试错建仓低吸
                        : Status∈{启动,升温} 且 Composite<50 → 底仓观察等突破（防误杀）
      3. 分歧转一致     : Status==分歧转一致 → 右侧突破追买（极佳右侧点）
      4. 打板/接力      : LimitUp>=20 且 Status∈{抱团, 分歧转一致}
      5. 强力加仓/低吸   : Migration>=15 或 (Migration>=10 且 Composite>=75)
      6. 趋势持股/做T   : Trend>=80 且 LimitUp<10
      7. 逢高减仓       : Status==分歧 或 (Sentiment<45 且 Migration<5)
      8. 观望等待       : 其余且 Composite<70
      9. 兜底持有跟随    : Composite>=70

    仓位（单主题上限比例）：
      极高配 25%~30% : Composite>=80 且 Migration>=10
      高配  15%~20%  : Composite>=75 或 LimitUp>=20
      中配  10%~15%  : 65<=Composite<75
      低配  5%~10%   : 50<=Composite<65
      清仓  0%       : Composite<30 或 Status==退潮/弱势
    """
    trend = float(r.get('trend_score', 0) or 0)
    sentiment = float(r.get('sentiment_score', 0) or 0)
    composite = float(r.get('composite_score', 0) or 0)
    sd = r.get('sentiment_detail', {}) or {}
    limit_up = int(sd.get('zt_count', 0) or 0)
    migration = float(r.get('migration_score', 0) or 0)
    # 目标状态优先（迁移引擎输出），次选当前生命周期
    status = r.get('target_state', '') or r.get('lifecycle', '') or r.get('theme_state', '')
    status = str(status)

    # ── 仓位基准（动作无特殊覆盖时使用） ──
    if composite < 30 or '退潮' in status or status == '弱势':
        position_pct = 0.0
        position_label = '清仓/空仓(0%)'
    elif composite >= 80 and migration >= 10:
        position_pct = 27.5   # 25~30% 区间中位
        position_label = '极高配置(25%-30%)'
    elif composite >= 75 or limit_up >= 20:
        position_pct = 17.5   # 15~20%
        position_label = '高配置(15%-20%)'
    elif composite >= 65:
        position_pct = 12.5   # 10~15%
        position_label = '中配置(10%-15%)'
    else:  # 50<=composite<65
        position_pct = 7.5    # 5~10%
        position_label = '低配置(5%-10%)'

    def _out(action, reason, pct, label, sp=None):
        return {'trade_action': action, 'action_reason': reason,
                'position_pct': pct, 'position_label': label,
                'suggested_position': sp or label}

    # ── 交易动作（严格优先级） ──
    # 1. 退潮期优先：坚决止损（Composite<30 不再细分状态；弱势=资金持续流出同清仓）
    if '退潮' in status or status == '弱势' or composite < 30:
        return _out('清仓离场 / 止损出局',
                    f'退潮期/极弱 坚决止损 Status={status}, Composite={composite:.0f}',
                    0.0, '清仓/空仓(0%)')

    # 2. 启动/升温期修复：防止低综合分误杀高迁移分的启动板块
    if '启动' in status or '升温' in status:
        if migration >= 15:  # 资金强力流入的启动板
            return _out('试错建仓 / 回踩分批吸筹',
                        f'启动期资金强力流入 Migration={migration:.0f}，试错建仓',
                        7.5, '低配置(5%-10%)')
        elif composite < 50:
            return _out('底仓观察 / 等待放量突破',
                        f'启动期但综合分偏低({composite:.0f})，底仓观察等放量',
                        2.5, '试错(0%-5%)')

    # 3. 分歧转一致：极佳右侧点
    if '分歧转一致' in status:
        return _out('右侧突破追买 / 确认放量加仓',
                    f'分歧转一致，极佳右侧点 Composite={composite:.0f}',
                    12.5, '中配置(10%-15%)')

    # 4. 打板/接力
    if limit_up >= 20 and ('抱团' in status or '分歧转一致' in status):
        return _out('龙头打板 / 板块补涨接力',
                    f'情绪极高(涨停{limit_up}家)且状态={status}，梯队完整',
                    position_pct, position_label)

    # 5. 强力加仓/低吸
    if migration >= 15 or (migration >= 10 and composite >= 75):
        action = '逢低分批加仓（重点关注5日/10日线）'
        reason = f'资金强力净流入 Migration={migration:.0f}, Composite={composite:.0f}'
        pct = min(30.0, position_pct * 1.15) if position_pct > 0 else 0
        return _out(action, reason, round(pct, 1), position_label)

    # 6. 趋势持股/做T
    if trend >= 80 and limit_up < 10:
        return _out('通道持股 / 动态网格做T',
                    f'机构慢牛趋势 Trend={trend:.0f}, 涨停仅{limit_up}家',
                    position_pct, position_label)

    # 7. 逢高减仓
    if status == '分歧' or (sentiment < 45 and migration < 5):
        pct = max(0.0, position_pct * 0.5)
        return _out('逢高分批减仓 / 防御性收缩',
                    f'情绪见顶/分歧加剧 Status={status}, Sentiment={sentiment:.0f}, Migration={migration:.0f}',
                    round(pct, 1), position_label)

    # 8. 观望等待
    if composite < 70:
        return _out('空仓观望 / 保持关注',
                    f'无明确趋势和资金流向，震荡板块 Composite={composite:.0f}',
                    0.0, '观望(0%)')

    # 9. 兜底（Composite>=70 但不满足4-6任何一条 → 偏积极持有）
    return _out('持有跟随 / 5日线止盈',
                f'综合分尚可 Composite={composite:.0f}，无明确买卖信号',
                position_pct, position_label)


def calc_confidence_v3(n_stocks):
    """小主题可信度修正：min(1, sqrt(成份股数量/60))，防止小板块偶发暴涨"""
    return min(1.0, float(np.sqrt(n_stocks / 60.0)))


def calc_mti_v3(strength, fund, leader, heat, persistence):
    """MTI 原始加权分 = 25%Strength + 25%Fund + 20%Leader + 15%Heat + 15%Persistence

    注意：此函数只做绝对加权，最终 MTI 在 run_v2_analysis 中再做横截面分层
    （30%原始分 + 70%横截面百分位加权），以修复分项压缩导致的分层不足。
    """
    return round(0.25 * strength + 0.25 * fund + 0.20 * leader + 0.15 * heat + 0.15 * persistence, 1)


def get_mti_level(mti):
    if mti >= 80:
        return '主线'
    if mti >= 65:
        return '准主线'
    if mti >= 50:
        return '轮动主题'
    if mti >= 35:
        return '补涨主题'
    return '非主线'


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
               "position_pct": r.get("position_pct", 0),
               "position_label": r.get("position_label", ""),
               "suggested_position": r.get("suggested_position", ""),
               "proximity": mf.get("proximity", 0), "momentum": mf.get("momentum", 0),
               "confirmation": mf.get("confirmation", 0), "money_resonance": mf.get("money_resonance", 0),
               "leader_health": mf.get("leader_health", 0), "regime": mf.get("regime", 0),
               "age_penalty": mf.get("age_penalty", 0), "macro_filter": mf.get("macro_filter", 0),
               # ── V3 Rotation Engine 字段 ──
               "lifecycle": r.get("lifecycle", ""),
               "strength_score": r.get("strength_score", 0),
               "fund_score": r.get("fund_score", 0),
               "breadth_score": r.get("breadth_score", 0),
               "leader_v3_score": r.get("leader_v3_score", 0),
               "persistence": r.get("persistence", 0),
               "heat_v3": r.get("heat_v3", 0),
               "mti": r.get("mti", 0), "mti_level": r.get("mti_level", ""),
               "base_trade_score": r.get("base_trade_score", 0),
               "final_trade_score": r.get("final_trade_score", 0),
               "trade_rank": r.get("trade_rank", 0),
               # ── A股主线类型分级 V1.0 ──
               "mainline_type": r.get("mainline_type", ""),
               "mainline_quality": r.get("mainline_quality", 0),
               "mainline_quality_label": r.get("mainline_quality_label", ""),
               "trading_style": r.get("trading_style", ""),
               "ml_emotion_score": r.get("ml_emotion_score", 0),
               "ml_trend_score": r.get("ml_trend_score", 0),
               "ml_capital_persistence": r.get("ml_capital_persistence", 0),
               "ml_leader_score": r.get("ml_leader_score", 0),
               "ml_center_score": r.get("ml_center_score", 0),
               "ml_resonance_score": r.get("ml_resonance_score", 0),
               "fund_acc": r.get("fund_acc", 0),
               "lc_mult": r.get("lc_mult", 0),
               "confidence": r.get("confidence", 0),
               "growth_factor": r.get("growth_factor", 0),
               "fund_net": (r.get("fund_detail") or {}).get("fund_net", 0),
               "fund_net_ratio": (r.get("fund_detail") or {}).get("fund_net_ratio", 0),
               "fund_growth": (r.get("fund_detail") or {}).get("fund_growth", 0),
               "fund_share": (r.get("fund_detail") or {}).get("fund_share", 0),
               "leader_v3": (r.get("leader_v3_detail") or {}).get("leader_v3", ""),
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
    for col in ["migration_score REAL", "migration_direction TEXT", "target_state TEXT", "trade_action TEXT",
                "position_pct REAL", "position_label TEXT", "suggested_position TEXT"]:
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
             migration_score, migration_direction, target_state, trade_action,
             position_pct, position_label, suggested_position)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r['rank'], r['theme'], r['n_stocks'], r['trend_score'], r['sentiment_score'], r['composite_score'],
             climax_warning, r.get('leader_name', ''), r.get('leader_code', ''), r.get('leader_score', 0),
             r.get('core_name', ''), r.get('core_code', ''), r.get('core_score', 0),
             td.get('avg_ret_5', 0), td.get('avg_ret_10', 0), td.get('avg_ret_20', 0),
             sd.get('up_ratio', 0), sd.get('zt_count', 0),
             TRADE_DATE_str, theme_state,
             r.get('hot_score', 0), r.get('hot_percentile', 50),
             r.get('hot_phase', '正常'), r.get('hot_warning', ''),
             r.get('migration_score', 0), r.get('migration_direction', 'sideways'),
             r.get('target_state', ''), r.get('trade_action', ''),
             r.get('position_pct', 0), r.get('position_label', ''), r.get('suggested_position', '')))

    conn.commit()
    conn.close()
    print(f"[保存] SQLite: {OUTPUT_DB} ({len(results)} 条)")


# ══════════════════════════════════════════════════════════════
# 大盘择时指令 + 主线判定（Mainline Gatekeeper）+ 胜率/概率估算
# ══════════════════════════════════════════════════════════════

def _load_market_directive(trade_date):
    """读取大盘择时报告，提取最高指令与目标仓位

    Returns: dict {directive, target_pos, action, strategy, mainline_only}
    """
    ma_path = os.path.join(BASE_DIR, "cache_backbone_tushare", f"market_analysis_{trade_date}.txt")
    out = {'directive': '', 'target_pos': None, 'action': '', 'strategy': '', 'mainline_only': False}
    if not os.path.exists(ma_path):
        return out
    try:
        with open(ma_path, 'r', encoding='utf-8') as f:
            txt = f.read()
        # 一句话
        m = re.search(r'一句话[:：]\s*(.+)', txt)
        if m:
            out['directive'] = m.group(1).strip()
        # 当前目标仓位
        m = re.search(r'当前目标[:：]\s*(\d+)%', txt)
        if m:
            out['target_pos'] = int(m.group(1))
        # 动作
        m = re.search(r'【动作】\s*(.+)', txt)
        if m:
            out['action'] = m.group(1).strip()
        # 策略
        m = re.search(r'【策略】\s*(.+)', txt)
        if m:
            out['strategy'] = m.group(1).strip()
        # 主线模式判断：只做主线 / 少做非主线 → 严格过滤
        if re.search(r'只做主线|不追杂毛|非主线：少做|只做主线，不追杂毛', txt):
            out['mainline_only'] = True
    except Exception as e:
        print(f"[报告] 大盘指令读取失败: {e}")
    return out


def _is_mainline(r):
    """主线判定（Mainline Gatekeeper）

    条件A（绝对主线）：Status∈[主升,高潮] 且 (LimitUp>=10 或 Trend>=80) 且 Composite>=75
    条件B（加速主线）：Migration>=20 且 Composite>=75 且 Trend>=75
    返回: (is_mainline, 条件标签)
    """
    trend = float(r.get('trend_score', 0) or 0)
    composite = float(r.get('composite_score', 0) or 0)
    sd = r.get('sentiment_detail', {}) or {}
    zt = int(sd.get('zt_count', 0) or 0)
    mig = float(r.get('migration_score', 0) or 0)
    lc = str(r.get('lifecycle', '') or '')
    if lc in ('主升', '高潮') and (zt >= 10 or trend >= 80) and composite >= 75:
        return True, 'A-绝对主线'
    if mig >= 20 and composite >= 75 and trend >= 75:
        return True, 'B-加速主线'
    return False, ''


def _est_winrate(r):
    """预估交易胜率 (%)：生命周期基准 + 综合分/资金/涨停修正"""
    lc = r.get('lifecycle', '')
    composite = float(r.get('composite_score', 0) or 0)
    sd = r.get('sentiment_detail', {}) or {}
    zt = int(sd.get('zt_count', 0) or 0)
    mig = float(r.get('migration_score', 0) or 0)
    base = {'主升': 60, '升温': 55, '启动': 50, '分歧': 52, '高潮': 58, '退潮': 28}
    wr = base.get(lc, 45) + (composite - 60) * 0.25 + min(5, mig * 0.15)
    if zt >= 10:
        wr += 3
    if zt >= 20:
        wr += 2
    return int(max(15, min(72, wr)))


def _est_rr(r):
    """预期盈亏比 R:R"""
    lc = r.get('lifecycle', '')
    composite = float(r.get('composite_score', 0) or 0)
    base = {'主升': 2.8, '升温': 2.2, '启动': 1.8, '分歧': 2.0, '高潮': 2.5, '退潮': 0.8}
    rr = base.get(lc, 1.5)
    if composite >= 80:
        rr += 0.3
    elif composite < 60:
        rr -= 0.3
    return round(max(0.5, min(3.5, rr)), 1)


def _est_mainline_prob(r):
    """轮动板块 → 主线的转化概率估算 (%)"""
    mig = float(r.get('migration_score', 0) or 0)
    trend = float(r.get('trend_score', 0) or 0)
    composite = float(r.get('composite_score', 0) or 0)
    sd = r.get('sentiment_detail', {}) or {}
    zt = int(sd.get('zt_count', 0) or 0)
    prob = 10 + min(25, mig * 1.0) + max(0, trend - 50) * 0.6 + max(0, composite - 55) * 0.5
    if zt >= 5:
        prob += 10
    if zt >= 10:
        prob += 5
    return int(max(5, min(80, prob)))


def _mainline_confirm(r):
    """触发成为主线的确认条件"""
    lc = str(r.get('lifecycle', '') or '')
    ts = str(r.get('target_state', '') or '')
    if lc == '升温' or '升温' in ts:
        return "放量突破20日线 + 涨停≥10家"
    if lc == '分歧' or '分歧转一致' in ts:
        return "连续2日涨停≥10家 + 龙头封板"
    if lc == '启动' or '启动' in ts:
        return "突破20日线 + 涨停≥5家"
    return "资金连续3日净流入 + 趋势站上60日线"


def _load_subtheme_map_v2():
    """加载 subtheme_map.json：{母主题: {子主题: {industry, concept, keywords, core_companies}}}"""
    paths = [
        os.path.join(BASE_DIR, "theme_kg_v3", "theme_kg_v3", "config", "subtheme_map.json"),
        os.path.join(BASE_DIR, "theme_kg_v3", "config", "subtheme_map.json"),
        os.path.join(BASE_DIR, "theme_kg_v3", "config", "subtheme_map.json"),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[子主题] 加载失败 {p}: {e}")
    print("[子主题] 未找到 subtheme_map.json，主线穿透跳过")
    return {}


def analyze_mainline_penetration(theme_name, rows, theme_stock_map, subtheme_map, mf_map):
    """主线细分穿透算法：定位最佳子主题 + 龙头/中军

    Step1: 将主线成份股按子主题归属（核心公司名 > 行业 > 关键词）
    Step2: 子主题得分 = 涨停集中度(涨停数*2+最高连板)*3 + 资金迁移(净流入万元/1e5)
           → 锁定 TOP1 最佳子主题
    Step3: 最佳子主题内选龙头（市值50~300亿、连板/涨停/领涨最优）
           与中军（市值>500亿、日成交额最高）

    Returns: dict 或 None
    """
    subs = subtheme_map.get(theme_name)
    if not subs:
        for k in subtheme_map:
            if theme_name in k or k in theme_name:
                subs = subtheme_map[k]
                break
    if not subs:
        # 兜底：无子主题配置时，用主题本身作为子主题（保证每条主线都有穿透输出）
        subs = {theme_name: {}}
    # 无子主题配置的纯兜底（如小金属）：未匹配股票归入主题本身，而不是"未细分"
    fallback_only = not any(cfg for cfg in subs.values())

    # ── Step1: 股票 → 子主题 归属 ──
    # 匹配优先级：核心公司名(100) > 行业(40, 双向子串含"专用机械→机械/化工原料→化工") > 名称关键词(20)
    # 未匹配股票不丢弃，归入"未细分"桶（否则涨停股被丢弃→涨停集中度统计失真）
    sub_stocks = defaultdict(list)
    theme_meta = theme_stock_map.get(theme_name, {})
    for row in rows:
        code = row.get('ts_code', '')
        name = row.get('name', '')
        industry = (theme_meta.get(code) or {}).get('industry', '')
        best_sub, best_score = None, 0
        for sub_name, sub_cfg in subs.items():
            sc = 0
            core_names = sub_cfg.get('core_companies', [])
            inds = sub_cfg.get('industry', [])
            kws = sub_cfg.get('keywords', [])
            if name and name in core_names:
                sc += 100
            if industry and inds:
                if industry in inds or any(ind in industry for ind in inds):
                    sc += 40
            if name:
                for kw in kws:
                    if kw and kw in name:
                        sc += 20
                        break
            if sc > best_score:
                best_score, best_sub = sc, sub_name
        if best_sub:
            sub_stocks[best_sub].append(row)
        elif fallback_only:
            # 纯兜底主题：全部归入主题本身（保证显示为主题名而非"未细分"）
            sub_stocks[theme_name].append(row)
        else:
            sub_stocks["未细分"].append(row)

    if not sub_stocks:
        # 兜底2：全部成份股未匹配到任何子主题（如无子主题配置的主题），
        # 用主题本身作为子主题承载全部股票，保证穿透必有输出
        sub_stocks[theme_name] = list(rows)

    # ── Step2: 子主题得分 → TOP1 ──
    # 得分 = 涨停集中度(涨停数*2+最高连板)*3 + 资金净流入(万元/1e5，仅计净流入)
    # "未细分"桶不参与最佳子主题评选（细分映射未覆盖，避免无意义胜出）；
    # 若所有具名子主题为空才退化为用未细分桶兜底。
    scored = []
    for sub_name, stock_rows in sub_stocks.items():
        if sub_name == "未细分" and len(sub_stocks) > 1:
            continue
        n = len(stock_rows)
        zt = sum(1 for x in stock_rows if x.get('zt_flag', 0) == 1)
        lb_max = max([x.get('lb_height', 0) for x in stock_rows], default=0)
        mig = sum(max(0.0, mf_map.get(x.get('ts_code', ''), 0) or 0) for x in stock_rows)
        zt_density = zt * 2 + lb_max
        score = zt_density * 3 + mig / 1e5
        scored.append((score, sub_name, stock_rows, zt, lb_max, n, mig))

    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return None
    _, best_sub, best_stocks, best_zt, best_lb, best_n, best_mig = scored[0]

    # 推荐理由
    if best_zt >= 3:
        reason = "涨停梯队最齐（含高位连板）"
    elif best_mig > 0:
        reason = "资金强力沉淀（迁移分净流入最密集）"
    else:
        reason = "涨停集中度+资金双优"

    # ── Step3: 龙头 / 中军 ──
    leader = engine = None
    # 龙头判定：连板/涨停优先级最高（历史连板数=龙头辨识度第一因子）；
    # 市值 50~300亿 区间仅为偏好（带内优先），不再是硬过滤——
    # 避免 4连板领涨股因市值>300亿被挡在候选外、0连板股当选龙头。
    leader_cands = [x for x in best_stocks if x.get('total_mv', 0) > 0]
    if not leader_cands:
        leader_cands = list(best_stocks)  # 全无市值信息时全量兜底
    if leader_cands:

        def _leader_key(x):
            mv = x.get('total_mv', 0)
            in_band = 1 if (5e5 <= mv <= 3e6) else 0  # 50~300亿 带内优先
            lb = x.get('lb_height', 0)
            zt = x.get('zt_flag', 0)
            return (1 if (lb > 0 or zt > 0) else 0, lb, zt, in_band,
                    abs(x.get('pct_chg', 0) or 0), -mv)

        leader = max(leader_cands, key=_leader_key)
    # 中军：市值>500亿 且日成交额最高（排除已当选龙头，避免与龙头重复）
    engine_cands = [x for x in best_stocks
                    if x is not leader and x.get('total_mv', 0) > 5e6]
    if engine_cands:
        engine = max(engine_cands, key=lambda x: x.get('amount_latest', 0) or 0)

    return {
        'best_subtheme': best_sub, 'reason': reason,
        'zt_count': best_zt, 'lb_max': best_lb, 'n_stocks': best_n,
        'migration_sum': round(best_mig, 1),
        'leader': leader, 'engine': engine,
    }


def _mainline_penetration_rows(r):
    """辅助：从主线结果 dict 输出穿透报告的龙头/中军文本行"""
    pen = r.get('penetration')
    if not pen:
        return []
    lines = []
    lines.append(f"#### 核心主线：{r['theme']}")
    lines.append(f"* **最佳子主题**：{pen['best_subtheme']}（推荐理由：{pen['reason']}；"
                 f"涨停{pen['zt_count']}家/最高{pen['lb_max']}连板/资金净流入{pen['migration_sum']:.0f}万元）")
    ld = pen.get('leader')
    if ld:
        mv_yi = (ld.get('total_mv', 0) or 0) / 10000
        ld_pos = r.get('leader_target_pos')
        ld_pos_str = f"建议仓位 {ld_pos * 100:.1f}%" if ld_pos is not None else "建议仓位 10%"
        lines.append(f"* **【龙头】标的**：{ld.get('ts_code', '')} {ld.get('name', '')}")
        lines.append(f"  - 角色：情绪领涨 / 超短爆发（{ld.get('lb_height', 0)}连板, "
                     f"市值{mv_yi:.0f}亿, 成交额{(ld.get('amount_latest', 0) or 0):.1f}亿）")
        lines.append(f"  - 匹配动作：打板接力 / 右侧突破追买（{ld_pos_str}）")
    eng = pen.get('engine')
    if eng:
        mv_yi = (eng.get('total_mv', 0) or 0) / 10000
        core_pos = r.get('core_target_pos')
        core_pos_str = f"建议仓位 {core_pos * 100:.1f}%" if core_pos is not None else "建议仓位 15%-20%"
        lines.append(f"* **【中军】标的**：{eng.get('ts_code', '')} {eng.get('name', '')}")
        lines.append(f"  - 角色：容量承载 / 趋势慢牛（市值{mv_yi:.0f}亿, "
                     f"日成交额{(eng.get('amount_latest', 0) or 0):.1f}亿）")
        lines.append(f"  - 匹配动作：回踩5日/10日线分批低吸 / 通道网格做T（{core_pos_str}）")
    return lines


def _apply_rotation_v4(mainlines, rotations, ma):
    """V4 主线轮动仓位归一化（适配 process_theme_rotation_v4 逻辑）

    Step1: 大盘 strict_mainline_only → 轮动板块仓位全部清零（只做主线）
    Step2: 主线 raw_weight（取 final_trade_score）求和
    Step3: 每条主线 allocated_position = raw/total × 大盘目标仓位上限（默认30%）
    Step4: 穿透至龙头/中军（4:6 固定分配：龙头40% / 中军60%）

    就地回写：主线 r 增加 allocated_position / leader_target_pos / core_target_pos，
    并同步 position_pct / position_label（报告输出层统一使用）。
    """
    # Step1: 严格主线模式下，轮动仓位清零（动作同步改为观望，避免仓位/动作矛盾）
    if ma.get('mainline_only'):
        for r in rotations:
            r['position_pct'] = 0.0
            r['position_label'] = '观察(0%)'
            r['trade_action'] = '空仓观望 / 保持关注'

    # Step2-3: 主线归一化折算
    raw_w = [max(0.0, float(r.get('final_trade_score', 0) or 0)) for r, _ in mainlines]
    total_w = sum(raw_w)
    if total_w <= 0:
        total_w = len(raw_w)  # 极端兜底：平均分配
    cap_limit = (ma.get('target_pos') or 0) / 100.0 if ma.get('target_pos') else 0.30
    for r, _mtype in mainlines:
        raw = max(0.0, float(r.get('final_trade_score', 0) or 0))
        allocated = round(raw / total_w * cap_limit, 4)
        r['allocated_position'] = allocated
        # Step4: 龙头/中军 4:6 固定分配
        r['leader_target_pos'] = round(allocated * 0.40, 4)
        r['core_target_pos'] = round(allocated * 0.60, 4)
        # 回写展示字段（报告/决策表输出层统一读取）
        r['position_pct'] = round(allocated * 100, 1)
        r['position_label'] = '主线配仓'


def save_to_text_report_v2(results, kg_v3_cfg, en_to_cn, market_ret_10=0.0, etf_kline_map=None):
    """生成 V3 规范文本报告（主线优先结构，对接实盘 QMT/CTP）

    结构：
      0. 大盘择时指令（读取 market_analysis 报告）
      1. 第一部分：核心主线阵营（建议配仓 80%~90%）+ 主线细分穿透（最佳子主题/龙头/中军）
      2. 第二部分：潜在轮动与接力机会（建议配仓 0%~20%）
      3. 第三部分：杂毛/退潮与风险回避区（建议仓位 0%）
      4. 主线与轮动交易决策表（全量，含主线属性 / 胜率 / 转化概率）
      5. 机构配置策略建议（ETF 配置 / 整体仓位 / 核心风险）

    输出偏好：分隔线使用全角 ━(U+2550) / ─(U+2500)，正文无段落缩进（移动端浏览）
    """
    if etf_kline_map is None:
        etf_kline_map = {}
    report_path = os.path.join(REPORT_DIR, f"theme_analysis_v2_{TRADE_DATE_str}.txt")
    buf = []
    def w(s=""):
        buf.append(s)

    # 生命周期显示名：'分歧' → '分歧转一致'（规范六类）
    LC_DISPLAY = {'启动': '启动', '升温': '升温', '主升': '主升',
                  '分歧': '分歧转一致', '高潮': '高潮', '退潮': '退潮'}
    # V3 生命周期未来3日迁移路径（与左侧分类同语言，消除"启动却预判震荡/弱势"的矛盾）
    # 方向取自 V2 迁移引擎 migration_direction；向上/中性走乐观路径，向下走谨慎路径
    LC_NEXT_UP = {'启动': '升温', '升温': '主升', '分歧': '升温', '主升': '高潮', '高潮': '分歧', '退潮': '启动'}
    LC_NEXT_DOWN = {'启动': '震荡', '升温': '分歧', '分歧': '退潮', '主升': '分歧', '高潮': '退潮', '退潮': '退潮'}

    # 主题 → 主 ETF（保留完整代码如 159869.SZ，与 etf_kline_map / fund_basic 的 key 一致）
    etf_map = {}
    for _key, _cfg in kg_v3_cfg.items():
        if _key.startswith('_'):
            continue
        _cn = _cfg.get("name_cn", _key)
        etf_map[_cn] = str(_cfg.get("main_etf", "") or "")

    # 按 Trade 排序
    results_trade = sorted(results, key=lambda x: x.get('final_trade_score', 0), reverse=True)

    # 大盘择时指令
    ma = _load_market_directive(TRADE_DATE_str)

    w("━" * 60)
    w(f"  主题评分分析报告 V3（主线优先 · Theme Rotation Engine）- {TRADE_DATE_str}")
    w("━" * 60)
    w()

    # ── 0. 大盘择时指令 ──
    w("━" * 60)
    w("### ★★★ 大盘择时指令（Market Directive）")
    w("━" * 60)
    if ma['directive']:
        w(f"* 一句话：{ma['directive']}")
    if ma['action'] or ma['strategy']:
        w(f"* 择时动作：{ma['action'] or '—'} | 策略：{ma['strategy'] or '—'}")
    if ma['target_pos'] is not None:
        w(f"* 大盘目标仓位：{ma['target_pos']}%（正常区间上限建议不超过此值）")
    if ma['mainline_only']:
        w("* 最高指令：大盘环境仅允许做主线，严格过滤非主线与杂毛轮动")
    elif ma['directive'] or ma['action']:
        w("* 最高指令：主线优先，轮动机会轻仓试探，杂毛坚决回避")
    else:
        w("* 最高指令：未读取到大盘择时报告，按中性环境执行（主线优先）")
    w()

    # ── 主线 / 轮动 / 杂毛 三分类 ──
    mainlines, rotations, junk = [], [], []
    for r in results:
        is_m, mtype = _is_mainline(r)
        lc = str(r.get('lifecycle', '') or '')
        ts = str(r.get('target_state', '') or '')
        mig = float(r.get('migration_score', 0) or 0)
        comp = float(r.get('composite_score', 0) or 0)
        # 与交易动作同源的"状态"（target_state 优先）
        status = ts or lc
        # 启动/升温高迁移保护：资金强力流入的启动板块不可归入回避区（防误杀）
        is_protected = ('启动' in status or '升温' in status) and mig >= 15
        if is_m:
            mainlines.append((r, mtype))
        elif lc == '退潮' or (comp < 50 and not is_protected):
            junk.append(r)
        elif (lc in ('分歧', '升温') or '分歧转一致' in ts or '升温' in ts) or mig >= 15:
            rotations.append(r)
        else:
            rotations.append(r)  # 其余一律归轮动观察（低概率但非杂毛）

    mainlines.sort(key=lambda x: x[0].get('final_trade_score', 0), reverse=True)
    rotations.sort(key=lambda x: x.get('final_trade_score', 0), reverse=True)
    junk.sort(key=lambda x: x.get('composite_score', 0), reverse=True)

    # ── V4 主线仓位归一化（process_theme_rotation_v4：轮动清零 / 主线按比例折算 / 穿透4:6）──
    _apply_rotation_v4(mainlines, rotations, ma)

    # ── 1. 第一部分：核心主线阵营 ──
    w("━" * 60)
    w("### 第一部分：核心主线阵营（Mainline Core · V4归一化配仓，合计≤大盘目标仓位）")
    w("━" * 60)
    if not mainlines:
        w("* 今日无符合主线判定逻辑的核心主线，建议空仓或极轻仓等待确认")
        w()
    for r, mtype in mainlines:
        theme = r['theme']
        lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
        sd = r.get('sentiment_detail', {}) or {}
        zt = sd.get('zt_count', 0)
        mig = r.get('migration_score', 0)
        wr = _est_winrate(r)
        rr = _est_rr(r)
        action = r.get('trade_action', '')
        pos_lbl = r.get('position_label', '')
        pos_pct = r.get('position_pct', 0)
        pos_str = f"{pos_lbl}({pos_pct:.0f}%)" if pos_pct > 0 else pos_lbl
        w(f"▶ {theme} [{lc_disp}] {r.get('mainline_type', '')} 质量{r.get('mainline_quality', 0):.0f} | "
          f"策略:{r.get('trading_style', '')} | 趋势{r.get('trend_score', 0):.0f} "
          f"情绪{r.get('sentiment_score', 0):.0f} 涨停{zt} 迁移{mig:.1f}")
        w(f"    胜率预估 {wr}% | 盈亏比 {rr}:1 | 建议仓位 {pos_str}")
        w(f"    实盘买点：{action}")
    # 主线细分穿透：最佳子主题 / 龙头 / 中军
    if mainlines:
        w()
        w("─" * 60)
        w("### 主线细分穿透（最佳子主题 / 龙头 / 中军）")
        w("─" * 60)
        for r, _mtype in mainlines:
            pen_lines = _mainline_penetration_rows(r)
            if pen_lines:
                for pl in pen_lines:
                    w(pl)
                w()
        w()

    # ── 2. 第二部分：潜在轮动与接力机会 ──
    w("━" * 60)
    w("### 第二部分：潜在轮动与接力机会（Rotation · 建议配仓 0%~20%，仅观察/轻仓防守）")
    w("━" * 60)
    if not rotations:
        w("* 今日无潜在轮动机会")
        w()
    for r in rotations[:15]:
        theme = r['theme']
        lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
        sd = r.get('sentiment_detail', {}) or {}
        zt = sd.get('zt_count', 0)
        mig = r.get('migration_score', 0)
        prob = _est_mainline_prob(r)
        confirm = _mainline_confirm(r)
        action = r.get('trade_action', '')
        pos_lbl = r.get('position_label', '')
        pos_pct = r.get('position_pct', 0)
        pos_str = f"{pos_lbl}({pos_pct:.0f}%)" if pos_pct > 0 else pos_lbl
        w(f"▸ {theme} [{lc_disp}] {r.get('mainline_type', '')} 质量{r.get('mainline_quality', 0):.0f} | "
          f"趋势{r.get('trend_score', 0):.0f} 综合{r.get('composite_score', 0):.0f} "
          f"涨停{zt} 迁移{mig:.1f}")
        w(f"    成为主线概率 {prob}% | 确认条件：{confirm} | 建议仓位 {pos_str}")
        w(f"    交易动作：{action}")
    w()

    # ── 3. 第三部分：杂毛/退潮与风险回避区 ──
    w("━" * 60)
    w("### 第三部分：杂毛/退潮与风险回避区（Filtered · 建议仓位 0%）")
    w("━" * 60)
    if not junk:
        w("* 今日无退潮/低分回避主题")
        w()
    for r in junk[:15]:
        theme = r['theme']
        lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
        comp = r.get('composite_score', 0)
        w(f"✕ {theme} [{lc_disp}] 综合{comp:.0f} → 【坚决回避/清仓】")
    if len(junk) > 15:
        w(f"  … 其余 {len(junk) - 15} 只同类回避主题省略")
    w()

    # ── 4. 主线与轮动交易决策表（全量） ──
    w("━" * 60)
    w("### 主线与轮动交易决策表（全量）")
    w("━" * 60)
    w()
    w("─" * 100)
    w(f"{'优先级':<4}{'主题':<10}{'类型/质量':<18}{'胜率':<5}{'转化概率':<8}{'目标状态':<10}{'建议仓位':<14}{'交易动作'}")
    w("─" * 100)
    order = 0
    for r, mtype in mainlines:
        order += 1
        sd = r.get('sentiment_detail', {}) or {}
        zt = sd.get('zt_count', 0)
        mig = r.get('migration_score', 0)
        lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
        pos_lbl = r.get('position_label', '')
        pos_pct = r.get('position_pct', 0)
        pos_str = f"{pos_lbl}({pos_pct:.0f}%)" if pos_pct > 0 else pos_lbl
        ml_str = f"{r.get('mainline_type', '')} {r.get('mainline_quality', 0):.0f}"
        w(f"{order:<4}{r['theme']:<10}{ml_str:<18}{_est_winrate(r):<5}{'-':<8}{lc_disp:<10}{pos_str:<14}{r.get('trade_action', '')}")
    for r in rotations[:20]:
        order += 1
        lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
        pos_lbl = r.get('position_label', '')
        pos_pct = r.get('position_pct', 0)
        pos_str = f"{pos_lbl}({pos_pct:.0f}%)" if pos_pct > 0 else pos_lbl
        ml_str = f"{r.get('mainline_type', '')} {r.get('mainline_quality', 0):.0f}"
        w(f"{order:<4}{r['theme']:<10}{ml_str:<18}{_est_winrate(r):<5}{str(_est_mainline_prob(r)) + '%':<8}{lc_disp:<10}{pos_str:<14}{r.get('trade_action', '')}")
    for r in junk[:20]:
        order += 1
        lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
        ml_str = f"{r.get('mainline_type', '')} {r.get('mainline_quality', 0):.0f}"
        w(f"{order:<4}{r['theme']:<10}{ml_str:<18}{_est_winrate(r):<5}{'-':<8}{lc_disp:<10}{'0%':<14}{'清仓回避'}")
    w()

    # ── 3. 机构配置策略建议 ──
    w("━" * 60)
    w("### 机构配置策略建议")
    w("━" * 60)
    w()

    # ETF 配置建议（按 Trade 排序，剔除高潮/退潮；含 ETF 名称与自身趋势）
    w("* ETF 配置建议（Trade TOP5，剔除高潮/退潮；括号内为 ETF 自身趋势）")
    etf_name_map = get_etf_name_map()
    etf_pace = {'启动': '回踩5日线分批建仓', '升温': '逢低分批加仓',
                '主升': '持有跟随，破10日线减仓', '分歧': '急跌低吸博一致'}
    top_etf = []
    for r in results_trade:
        if r.get('lifecycle') in ('高潮', '退潮'):
            continue
        etf = etf_map.get(r['theme'], '')  # 完整代码，如 159869.SZ
        if not etf:
            continue
        etf_code = etf.replace('.SH', '').replace('.SZ', '')
        # ETF 真实名称（如"动漫游戏ETF"），缺失时用主题名兜底
        name = short_etf_name(etf_name_map.get(etf, ''))
        if not name:
            name = f"{r['theme']}ETF"
        # ETF 自身趋势（多头/回踩/弱势）来自预取的 fund_daily K线
        etf_state = judge_etf_trend(etf_kline_map.get(etf, None))
        # 未来3日方向（与第一部分同语言）
        direction = r.get('migration_direction', 'sideways')
        lc = r.get('lifecycle', '')
        lc_raw = '分歧' if lc == '分歧' else lc
        if lc_raw == '退潮' or direction == 'downward':
            lc_next = LC_NEXT_DOWN.get(lc_raw, lc_raw)
        else:
            lc_next = LC_NEXT_UP.get(lc_raw, lc_raw)
        # 节奏：ETF 破位时收紧，否则按生命周期
        if etf_state and etf_state['state'] == '弱势':
            pace = 'ETF破位，暂缓/轻仓'
        else:
            pace = etf_pace.get(lc, '分批布局')
        etf_tag = ""
        if etf_state:
            etf_tag = f"ETF{etf_state['state']}({etf_state['ret5']:+.1f}%)"
        else:
            etf_tag = "ETF趋势未知"
        top_etf.append((r, etf_code, name, f"{LC_DISPLAY.get(lc, lc)}→{lc_next}", etf_tag, pace))
        if len(top_etf) >= 5:
            break
    if top_etf:
        for r, etf_code, name, path, etf_tag, pace in top_etf:
            w(f"  ▸ {name}({etf_code}) | {path} | Trade {r.get('final_trade_score', 0):.1f} | {etf_tag} | {pace}")
    else:
        w("  暂无明显主线 ETF 标的，等待启动确认")

    # 整体仓位建议
    pos_cnt = sum(1 for r in results if r.get('lifecycle') in ('启动', '升温', '主升', '分歧'))
    neg_cnt = sum(1 for r in results if r.get('lifecycle') in ('高潮', '退潮'))
    total = len(results) or 1
    pos_ratio = pos_cnt / total
    neg_ratio = neg_cnt / total
    base = 50.0 + (pos_ratio - neg_ratio) * 60.0
    # 大盘环境修正（沪深300 近10日收益，最多 ±15%）
    mkt_adj = max(-15.0, min(15.0, market_ret_10))
    position = max(0.0, min(100.0, base + mkt_adj))
    pos_lv = "高仓位" if position >= 70 else "中等仓位" if position >= 45 else "低仓位"
    w(f"* 整体仓位建议：{position:.0f}%（{pos_lv}；启动/升温类{pos_cnt}只 vs 高潮/退潮类{neg_cnt}只，沪深300近10日{market_ret_10:+.1f}%）")

    # 核心风险提示
    risks = []
    if market_ret_10 < -2:
        risks.append(f"大盘环境偏弱（沪深300近10日{market_ret_10:+.1f}%），注意控制仓位")
    elif market_ret_10 > 3:
        risks.append(f"大盘强势（沪深300近10日{market_ret_10:+.1f}%），但需防范高位题材退潮")
    if neg_ratio > 0.3:
        risks.append("高潮/退潮主题占比偏高，注意高位股退潮与补跌风险")
    if not risks:
        risks.append("市场中性环境，跟随主线节奏，避免追高已加速主题")
    w("* 核心风险提示：" + "；".join(risks))
    w()

    w("━" * 60)
    w(f"报告生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w()

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(buf))
    print(f"[保存] 文本报告(V3规范): {report_path}")


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
    # 修复：停牌日 pct_chg 为 NaN，会污染下游 median/mean 计算导致评分失真
    pct = np.where(np.isnan(pct), 0.0, pct)
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

    # ── V3 Rotation 引擎新增因子 ──
    # 14. 前5日日均成交额（不含今日，用于 Fund 成交额增速）
    _amount_arr = df_one["amount"].astype(float).values if "amount" in df_one.columns else np.zeros(n)
    amount_ma5 = float(np.mean(_amount_arr[max(0, last - 5): last])) / 100000 if last >= 5 else amount_latest

    # 15. 创20日新高（今日收盘 ≥ 前20日最高收盘，不含今日）
    if last >= 20:
        new_high_flag = 1 if close[last] >= np.max(close[last - 20: last]) else 0
    elif last > 0:
        new_high_flag = 1 if close[last] >= np.max(close[: last]) else 0
    else:
        new_high_flag = 1

    # 16. 站上 MA20
    above_ma20_flag = 1 if (n >= 20 and close[last] > ma20) else (1 if n < 20 and close[last] > ma5 else 0)

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
        # ── V3 Rotation 引擎字段 ──
        "amount_ma5": amount_ma5,         # 前5日日均成交额（十亿）
        "new_high_flag": new_high_flag,   # 创20日新高
        "above_ma20_flag": above_ma20_flag,  # 站上 MA20
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
    V5 情绪评分（修复版：龙头质量 + 资金共振为核心因子）

    2026-08-03 结合实盘数据修复（私募量化视角）：
    1. 涨停判定已按板型区分（20cm/10cm），修复涨停数与连板高度低估
    2. 涨停强度改为「绝对数+相对比例」混合，修复大主题稀释（294只化工 vs 11只可控核聚变）
    3. 中军门槛 200亿→100亿：实盘 25/28 主题中军因子为0，失去区分度
    4. 量比因子修复：原线性区间(0.6,3.5)在普涨缩量日(均值0.85-1.1)全部失效且与情绪分负相关，
       改为「放量上涨占比」量价共振
    5. 龙头质量重构：原仅看最高连板股换手率且无连板直接=0（19/28主题为0），
       改为 龙头识别(无连板取大成交额上涨股) + 连板地位/成交规模/换手博弈/主线纯度/封板质量 五维
    6. 新增资金共振因子：主题总成交规模 + 大额成交广度 + 龙头资金集中度 + 量价共振
    7. 权重重构：龙头质量18% + 资金共振14% 成为核心支柱，替代原加性 bonus 堆叠
    """
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    if n == 0:
        return 0.0, {}

    # 修复：防御性清洗 NaN（停牌成分股 pct_chg/amount/turnover 可能为 NaN，
    # 未清洗会污染 median/mean → profit_score=sigmoid(NaN)=NaN → score01 被 min/max 误钳为满分）
    stock_feats = [dict(s) for s in stock_feats]
    for s in stock_feats:
        for k in ("pct_chg", "amount_latest", "turnover", "vol_ratio_today", "vol_ratio"):
            v = s.get(k)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                s[k] = 0.0

    pcts = [s["pct_chg"] for s in stock_feats]
    up_n = sum(1 for p in pcts if p > 0)
    down_n = sum(1 for p in pcts if p < 0)
    zt_n = sum(1 for s in stock_feats if s["zt_flag"] == 1)
    strong_n = sum(1 for s in stock_feats if s["strong_flag"] == 1)

    # ── 1. 广度分（普涨效应）──
    breadth = up_n / n
    breadth_score = linear(breadth, 0.15, 0.80)

    # ── 2. 涨停强度分（修复：绝对数+相对比例混合，避免大主题稀释）──
    zt_ratio = zt_n / n
    zt_score = 0.5 * linear(zt_n, 0, 8) + 0.5 * linear(zt_ratio, 0, 0.15)

    # ── 3. 强势股分 ──
    strong_ratio = strong_n / n
    strong_score = linear(strong_ratio, 0, 0.50)

    # ── 4. 盈亏效应分 ──
    median_pct = float(np.nanmedian(pcts))
    mean_pct = float(np.nanmean(pcts))
    profit_score = sigmoid(median_pct * 0.5 + mean_pct * 0.5, k=0.3, c=0)

    # ── 5. 量价共振分（修复：原量比分在缩量日失效且方向错误）──
    # 放量上涨占比：当日量比>1.2 且上涨的个股比例（量价配合才是真实情绪）
    vol_up_n = sum(1 for s in stock_feats
                   if s.get("vol_ratio_today", 0) > 1.2 and s.get("pct_chg", 0) > 0)
    up_vol_ratio = vol_up_n / n if n > 0 else 0
    vol_resonance_score = linear(up_vol_ratio, 0, 0.40)

    # ── 6. 连板高度分（非线性：3板+ 才具龙头辨识度）──
    lb_counts = [s.get("lb_height", 0) for s in stock_feats]
    max_lb = max(lb_counts) if lb_counts else 0
    multi_lb_count = sum(1 for lb in lb_counts if lb >= 2)

    def _lb_score(n_lb):
        if n_lb <= 0: return 0
        if n_lb == 1: return 15
        if n_lb == 2: return 40
        if n_lb == 3: return 70
        if n_lb == 4: return 90
        return 100  # 5板+
    lb_score = _lb_score(max_lb) / 100.0
    lb_density_bonus = min(multi_lb_count * 0.05, 0.15)  # 每多一只2板+加5%，上限15%

    # ── 7. 梯队完整度（高度板+中军+跟风；修复中军门槛200亿→100亿）──
    mid_cap_threshold = 1000000  # 100亿（万元）
    high_board_count = sum(1 for s in stock_feats if s.get('lb_height', 0) >= 3)
    has_high_board = 1 if high_board_count > 0 else 0
    # 中军：市值>=100亿 的涨停或大涨(>=5%)
    mid_cap_zt_count = sum(1 for s in stock_feats
                           if s.get('total_mv', 0) >= mid_cap_threshold and s.get('zt_flag', 0) == 1)
    mid_cap_strong_count = sum(1 for s in stock_feats
                               if s.get('total_mv', 0) >= mid_cap_threshold
                               and s.get('pct_chg', 0) >= 5 and s.get('zt_flag', 0) == 0)
    has_mid_cap_zt = 1 if (mid_cap_zt_count + mid_cap_strong_count) > 0 else 0
    # 跟风首板：lb_height==1 的涨停
    follower_zt_count = sum(1 for s in stock_feats
                            if s.get('lb_height', 0) == 1 and s.get('zt_flag', 0) == 1)
    has_followers = 1 if follower_zt_count > 0 else 0
    echelon_levels = has_high_board + has_mid_cap_zt + has_followers
    echelon_base = (echelon_levels / 3.0) ** 0.7

    # ── 8. 龙头质量因子（重构：五维综合，无连板也不丢分）──
    # 8a. 龙头识别：最高连板股优先；无连板则取当日上涨且成交额最大的股
    leaders_2plus = [s for s in stock_feats if s.get('lb_height', 0) >= 2]
    if leaders_2plus:
        leaders_2plus.sort(key=lambda s: (s.get('lb_height', 0), s.get('amount_latest', 0)), reverse=True)
        leader = leaders_2plus[0]
    else:
        candidates = [s for s in stock_feats if s.get('pct_chg', 0) > 0] or stock_feats
        candidates.sort(key=lambda s: s.get('amount_latest', 0), reverse=True)
        leader = candidates[0]
    leader_lb = leader.get('lb_height', 0)
    leader_turnover = leader.get('turnover', 0)
    leader_amount = leader.get('amount_latest', 0)
    leader_purity = leader.get('purity', 0)
    leader_boom = leader.get('boom_flag', 0)
    # 8b. 连板地位：龙头辨识度（3板+=强龙头）
    lb_q = _lb_score(leader_lb) / 100.0
    # 8c. 成交规模：龙头当日成交额（亿），20亿+ 大资金真金白银=满分
    amt_q = min(leader_amount / 20.0, 1.0)
    # 8d. 换手博弈：1-10%=健康换手（充分博弈），<1%=一字板，>25%=爆量分歧
    if np.isnan(leader_turnover) or leader_turnover < 1:
        tq_q = 0.15
    elif leader_turnover < 10:
        tq_q = 0.15 + 0.75 * (leader_turnover - 1) / 9.0
    else:
        tq_q = max(0.90 - (leader_turnover - 10) * 0.015, 0.40)
    # 8e. 主线纯度：龙头与主题概念贴合度
    purity_q = min(leader_purity / 4.0, 1.0)
    # 8f. 封板质量：炸板龙头扣分
    boom_q = 0.8 if leader_boom == 1 else 1.0
    leader_quality = (
        lb_q * 0.35 +
        amt_q * 0.25 +
        tq_q * 0.20 +
        purity_q * 0.10 +
        boom_q * 0.10
    )

    # ── 9. 资金共振因子（新增）──
    total_amount = float(np.sum([s.get("amount_latest", 0) for s in stock_feats]))
    money_scale_score = linear(total_amount, 30, 300)      # 主题当日总成交（亿），30亿冷清→300亿+活跃
    big_amount_n = sum(1 for s in stock_feats if s.get("amount_latest", 0) >= 10)
    big_amount_ratio = big_amount_n / n if n > 0 else 0
    big_amount_score = linear(big_amount_ratio, 0, 0.15)   # 10亿+大额成交股占比
    leader_amount_ratio = leader_amount / total_amount if total_amount > 0 else 0
    if 0.15 <= leader_amount_ratio <= 0.60:
        focus_score = 1.0                                  # 龙头资金集中度健康区
    elif leader_amount_ratio < 0.15:
        focus_score = linear(leader_amount_ratio, 0, 0.15)
    else:
        focus_score = max(1.0 - (leader_amount_ratio - 0.60) * 1.2, 0.3)  # 过度抱团递减
    money_resonance = (
        money_scale_score * 0.35 +
        big_amount_score * 0.30 +
        focus_score * 0.20 +
        vol_resonance_score * 0.15
    )

    # ── 10. 热榜加分（辅助）──
    hot_scores = [s.get("hot_rank_score", 0) for s in stock_feats]
    avg_hot_score = np.mean(hot_scores) if hot_scores else 0
    hot_bonus = sigmoid(avg_hot_score, k=0.35, c=4) * 0.05

    # ── 11. 情绪脆弱性惩罚（炸板率 + 封板率）──
    boom_count = sum(1 for s in stock_feats if s.get("boom_flag", 0) == 1)
    boom_ratio = boom_count / n if n > 0 else 0
    boom_penalty = min(max(boom_ratio - 0.10, 0) * 0.5, 0.10)
    board_seal_rate = max(zt_n - boom_count, 0) / max(zt_n, 1) if zt_n > 0 else 1.0
    board_quality_penalty = min(max(0.6 - board_seal_rate, 0) * 0.30, 0.12)

    # ── 12. 极端情绪判定 ──
    climax_flag = 1 if zt_n >= 15 else 0

    # ── 最终得分（权重重构：龙头质量18% + 资金共振14% 为核心支柱）──
    score01 = (
        breadth_score * 0.10 +      # 广度
        zt_score * 0.13 +           # 涨停强度
        strong_score * 0.05 +       # 强势股
        profit_score * 0.08 +       # 盈亏效应
        lb_score * 0.12 +           # 连板高度
        echelon_base * 0.12 +       # 梯队完整
        leader_quality * 0.18 +     # 龙头质量（核心）
        money_resonance * 0.14 +    # 资金共振（核心）
        hot_bonus                   # 热榜
    ) + lb_density_bonus - boom_penalty - board_quality_penalty

    score01 = max(0.0, min(1.0, score01))

    detail = {
        "up_ratio": round(breadth * 100, 1), "down_ratio": round(down_n / n * 100, 1),
        "zt_count": zt_n, "zt_ratio": round(zt_ratio * 100, 1),
        "strong_ratio": round(strong_ratio * 100, 1),
        "avg_vol_ratio": round(float(np.nanmean([s.get("vol_ratio", 0) for s in stock_feats])), 2),
        "avg_turnover": round(float(np.nanmean([s.get("turnover", 0) for s in stock_feats])), 2),
        "median_pct": round(median_pct, 2), "mean_pct": round(mean_pct, 2),
        "top1_pct": round(max(pcts), 2) if pcts else 0,
        "resonance": round(float(np.tanh(zt_n / max(n * 0.10, 1) * 0.8 + max(0, max(pcts) - 7) / 10 * 0.2)), 3),
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
        # 龙头质量明细（新）
        "leader_name": leader.get('name', ''),
        "leader_lb": leader_lb,
        "leader_turnover": round(leader_turnover, 2),
        "leader_amount": round(leader_amount, 1),
        "leader_purity": leader_purity,
        "leader_boom": leader_boom,
        "leader_quality": round(leader_quality * 100, 1),
        "leader_quality_bonus": round(leader_quality * 0.18 * 100, 1),
        # 资金共振明细（新）
        "money_scale": round(total_amount, 1),
        "big_amount_ratio": round(big_amount_ratio * 100, 1),
        "leader_amount_ratio": round(leader_amount_ratio * 100, 1),
        "up_vol_ratio": round(up_vol_ratio * 100, 1),
        "focus_score": round(focus_score * 100, 1),
        "money_resonance": round(money_resonance * 100, 1),
        # 封板质量
        "board_seal_rate": round(board_seal_rate * 100, 1),
        "board_quality_penalty": round(board_quality_penalty * 100, 1),
        "echelon_bonus": round(echelon_base * 0.12 * 100 + lb_density_bonus * 100, 1),
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
CYCLICAL_THEMES = {'证券', '工业金属', '能源金属', '战略与小金属', '煤炭'}
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
