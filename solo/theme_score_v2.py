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
    judge_hot_phase, is_hot_climax_phase, calc_theme_state,
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


def get_limit_up_pool(trade_date):
    """获取当日涨停池 {ts_code: {'zt_time': 首次封板时间, 'zt_order': 涨停序号}}

    数据源: limit_list_d（官方涨停列表，含 first_time 首次封板时间 HHMMSS）
    序号 = 全市场按首次封板时间排序的名次（越早封板序号越小）
    """
    out = {}
    try:
        from theme_trend_sentiment_score import pro as _pro
    except ImportError:
        print("  [涨停池] 无法导入 tushare 客户端，跳过")
        return out
    try:
        df = _pro.limit_list_d(trade_date=trade_date, limit_type='U')
        if df is not None and not df.empty and 'first_time' in df.columns:
            df = df.copy()
            df['first_time'] = df['first_time'].astype(str).str.split('.').str[0].str.zfill(6)
            df = df.sort_values('first_time').reset_index(drop=True)
            for i, r in df.iterrows():
                code = str(r.get('ts_code', '') or '')
                if not code:
                    continue
                ft = str(r.get('first_time', '') or '')
                ft = ft[:2] + ':' + ft[2:4] + ':' + ft[4:] if len(ft) == 6 and ft.isdigit() else ''
                out[code] = {'zt_time': ft, 'zt_order': i + 1}
            print(f"  涨停池: {len(out)} 只")
    except Exception as e:
        print(f"  [涨停池] 获取失败: {e}")
    return out


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

    # ── 6d. 涨停池（用于 top5 强势股的涨停时间/序号标记）──
    print("  获取涨停池（limit_list_d）...")
    zt_pool_map = get_limit_up_pool(TRADE_DATE_str)

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

        # ── Top5 强势股：按龙头强度公式对全成份股排序，记录涨停时间/序号、标记领涨股 ──
        strength_ranked = []
        for r in rows:
            lb = r.get('lb_height', 0)
            pct = abs(r.get('pct_chg', 0))
            amt = r.get('amount_latest', 0)
            p = r.get('purity', 0)
            ls = 0.4 * min(lb * 20, 100) + 0.3 * min(pct * 5, 100) + 0.2 * min(amt * 2, 100) + 0.1 * min(p * 20, 100)
            strength_ranked.append((r, ls))
        strength_ranked.sort(key=lambda x: x[1], reverse=True)
        top5_stocks = []
        for i, (r, ls) in enumerate(strength_ranked[:5], 1):
            code = r.get('ts_code', '')
            in_pool = code in zt_pool_map
            top5_stocks.append({
                'rank_top': i,
                'ts_code': code,
                'name': r.get('name', ''),
                'pct_chg': r.get('pct_chg', 0),
                'lb_height': r.get('lb_height', 0),
                'amount': r.get('amount_latest', 0),
                'strength': round(ls, 1),
                'is_leader': 1 if i == 1 else 0,
                'zt_flag': 1 if in_pool else 0,
                'zt_time': zt_pool_map.get(code, {}).get('zt_time', ''),
                'zt_order': zt_pool_map.get(code, {}).get('zt_order', 0) if in_pool else 0,
            })

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
            # 主题级近5日动量：门禁 L2/L1 的「衰减反弹不确认」硬条件依赖它。
            # 必须在此显式落到顶层，否则 calc_mainline_tier_v4 读 r['ret_5'] 恒为 0
            # → L2/L1 数学上不可达（历史 BUG：gate_feat.ret5 全量为 0）
            'ret_5': round(float(t_detail.get('avg_ret_5', 0) or 0), 2),
            'leader_name': leader_name, 'leader_code': leader_code,
            'leader_score': round(leader_scores[0][1], 1) if leader_scores else 0,
            'core_name': core_name, 'core_code': core_code,
            'core_score': round(core_scores[0][1], 1) if core_scores else 0,
            'top5_stocks': top5_stocks,
            'hot_score': round(hot_score, 2), 'hot_percentile': hot_percentile,
            'hot_phase': hot_phase, 'hot_warning': hot_warning,
            # 热度来源：hot_list(热榜) / zt_count_backup(无热榜日涨停数替代)，
            # 替代模式下分位与历史热榜口径不可比，供过热判定降级使用
            'hot_source': hot_detail.get('source', ''),
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
    # 上一交易日 / 近5日主题序列：直读本库（主题引擎的 prev 库日期稀疏且无 lifecycle，
    # 会让 days_strong 恒为 1、L2/L1 永不触发，见 _load_prev_theme_rows 注释）
    prev_rows, hist_rows = _load_prev_theme_rows(TRADE_DATE_str, days=5)
    if not prev_rows:
        print("  ⚠ 未取到上一交易日主题行：days_strong / 资金状态判定退化为单日口径")
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
        # 资金状态：撤离 / 高位分歧 / 分歧转一致 / 一致加速（只用日频可持久化序列，可审计）
        r['capital_state'] = classify_capital_state(r, prev_rows.get(r['theme']))
        # V4.1 五级主线门禁（L2 确认 / L1 试探 / R 修复跟踪 / L0 启动候选 / NONE 回避）
        _prev_r = prev_rows.get(r['theme']) or {}
        r['gate_tier'] = calc_mainline_tier_v4(
            r,
            prev_lc=str(_prev_r.get('lifecycle', '') or ''),
            prev_state=str(_prev_r.get('theme_state', '') or ''),
            hist_rows=hist_rows.get(r['theme']))

    # 确认主线天然稀缺：L2 每日最多 GATE_L2_MAX_PER_DAY 个，超额降为 L1（试探档轻仓），
    # 避免普涨日"批量确认主线"稀释信号（实测 0805 有 4 个主题同时满足 L2 判据）
    _l2_hits = sorted([x for x in results if x['gate_tier'] == 'L2'],
                      key=lambda x: -float(x.get('composite_score', 0) or 0))
    for _x in results:
        _x['gate_demoted'] = False
    for _x in _l2_hits[GATE_L2_MAX_PER_DAY:]:
        _x['gate_tier'] = 'L1'
        _x['gate_demoted'] = True
    # 主线细分穿透：仅对最终 L2 确认主线执行（最佳子主题 / 龙头 / 中军）
    for r in results:
        if r['gate_tier'] == 'L2':
            pen = analyze_mainline_penetration(
                r['theme'], r.get('stock_rows', []), theme_stock_map, subtheme_map, mf_map)
            if pen:
                r['penetration'] = pen

    # V1.1 主题轮动决策引擎：ThemeGate / PositionMultiplier / StockOverride
    # 主题决定风险预算与交易权限，个股决定最终执行；不改变 Trade Execution 核心计算，
    # 乘数在报告归一化（_apply_gate_v41_alloc）之后应用，保证报告/决策表/落库三口径一致。
    _tier_dist = {}
    for r in results:
        _t = str(r.get('gate_tier', 'NONE'))
        _tier_dist[_t] = _tier_dist.get(_t, 0) + 1
    _cap_dist = {}
    for r in results:
        _c = str((r.get('capital_state') or {}).get('state', '') or '')
        if _c and _c != '常态':
            _cap_dist[_c] = _cap_dist.get(_c, 0) + 1
    print(f"[V4.1] 门禁分档: {_tier_dist} | 资金状态异常: {_cap_dist or '无'}")
    apply_theme_gate_v11(results, TRADE_DATE_str)

    # ── 前兆主题探测（爆发前 3~5 日）──
    # 只新增观察池与子主题穿透，不改变 trade_action / position_pct（配仓仍由 V4.1 门禁决定）
    print("  探测前兆主题与子主题...")
    prev_stats = {th: {'hot': [float(x.get('hot_score') or 0) for x in rows],
                       'zt_prev': int((prev_rows.get(th) or {}).get('zt_count') or 0)}
                  for th, rows in hist_rows.items()}
    prec_list = detect_theme_precursors(results, theme_stock_map, subtheme_map, prev_stats)
    _prec_pool = [p for p in prec_list if p['n_hits'] > 0]
    print(f"  前兆主题 {len(_prec_pool)} 个 / 仅拥挤警告 {sum(1 for p in prec_list if not p['n_hits'])} 个")
    for _p in _prec_pool:
        _sub = _p['subthemes'][0]['name'] if _p['subthemes'] else '—'
        print(f"    {'/'.join(_p['hits'])} {_p['theme']:<8} 涨停{_p['zt_count']}家(昨{_p['zt_prev']}) "
              f"放量共振{_p['n_resonance']}家 首个子主题:{_sub}")

    # Trade 排序（交易优先级）
    results_trade_sorted = sorted(results, key=lambda x: x['final_trade_score'], reverse=True)
    for i, r in enumerate(results_trade_sorted, 1):
        r['trade_rank'] = i

    # 风格分析（top5）
    style_result = analyze_style_trend(results[:5])

    # ─── 7. 保存结果 ───
    print("\n[6/6] 保存结果...")

    # V2.1 回填模式（--v21-backfill）：只补 V2.1 历史序列，V2 既有 CSV/txt/DB 一律保持原样，
    # 避免重跑管线污染 V2 基线与历史归档（A/B Test 的前提是 V2 侧零改动）。
    _save_v2 = not V21_BACKFILL_MODE
    if not _save_v2:
        print("  [V2.1 回填] 跳过 V2 产物落盘（保留既有 CSV/txt/DB）")

    if _save_v2:
        # 文本报告（V3 规范）—— 内部执行 V4.1 分级归一化（_apply_gate_v41_alloc 会就地改写
        # trade_action/position_label/position_pct：L2 满配 / L1 试探≤1/3池 / L0 观察0% /
        # NONE 强制"清仓回避(0%)"，保证报告、决策表与落库三口径一致）。
        save_to_text_report_v2(results, kg_v3_cfg, en_to_cn,
                               market_ret_10=market_ret_10, etf_kline_map=etf_kline_map)

        # CSV —— 必须在文本报告之后写：txt 报告内部完成 V4.1 分级归一化 + V1.1 乘数 +
        # NONE 清仓回写（就地改写 trade_action/position_pct/position_label），
        # CSV 与之共用同一 results，三端口径才能一致（否则 CSV 残留 V3 裸建议）。
        save_to_csv_v2(results)

        # SQLite —— 必须在文本报告之后落库，保证 DB 存的是 V4 终判、与报告完全一致；
        # 若先于 txt 落库，DB 只会留下 V3 单主题裸建议（如"逢低分批加仓10-15%"），
        # 与报告"无主线/观察0%"矛盾，导致下游（tushare_quant 主题喂料）口径失真。
        save_to_sqlite_v2(results)

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

    # ─── 8. V2.1 附加层（只读 results；不改动 V2 的 txt/CSV/SQLite 产物）───
    # 强度用于发现 / 状态用于判断阶段 / 确认用于判断是否真形成趋势 / 持续性用于验证是否一天行情 /
    # 交易许可用于执行 / ChaseRisk 决定怎么进（而不是只决定买不买）。
    # 附加层异常一律不得影响 V2 主链路，故此处单独兜底。
    try:
        run_v21_layer(results, TRADE_DATE_str, idx_df=idx_df, market_ret_10=market_ret_10)
    except Exception as _e21:
        import traceback
        print(f"[V2.1] 附加层执行异常（V2 输出不受影响）: {_e21}")
        traceback.print_exc()

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
    #    ⚠ 以上只是"情绪极致"的必要条件。涨停潮爆发首日涨停数必然高，若直接判高潮，
    #    会把一致加速日误杀成见顶（实测 0916 半导体 涨停22家·上涨占比97.9%·
    #    主力资金强度100 → 判高潮 → 门禁 NONE → 强制清仓回避，前一日还是 L0 观察）。
    #    故必须叠加退潮确认：主力净流出 / 封板率<60% / 广度崩塌，才判高潮。
    #    注：sd['zt_ratio'] 为百分数（如 11.0），非 0.11，原 `>= 0.025` 恒真属量纲错误。
    climax_signal = (climax == 1 or (max_lb >= 3 and zt_ratio >= 2.5)
                     or is_hot_climax_phase(hot_phase))
    if climax_signal:
        seal = float(sd.get('board_seal_rate', 1.0) or 1.0)
        net_ratio = float((r.get('fund_detail', {}) or {}).get('fund_net_ratio', 0) or 0)
        if net_ratio < 0 or seal < 0.60 or up_ratio < 50:
            return '高潮'
        # 涨停潮但资金未撤、封板健康 → 一致加速，按强度落主升/升温（不进见顶处置）
        return '主升' if (trend >= 55 and emotion >= 50) else '升温'
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


# ══════════════════════════════════════════════════════════════
# 资金状态判定：区分「真退潮（资金撤离）」与「高位分歧（洗盘待修复）」
# 原体系里两者都只剩 gate_tier=NONE → 一律清仓回避，无法跟踪分歧转一致。
# 只用日频可持久化字段（涨停数 / 上涨占比 / 主力资金强度）做判定：
# 可回测、可审计、不依赖当日才有的盘中量。
# ══════════════════════════════════════════════════════════════
CAPITAL_STATES = ('一致加速', '分歧转一致', '高位分歧', '资金撤离', '常态')


def classify_capital_state(r, prev_row):
    """判定主题资金状态 → dict（就地供门禁/报告使用，不参与评分）

    一致加速  涨停实质性增加(≥2家) + 成规模(≥5家) + 主力资金强(≥60) + 广度扩散(≥65) → 主升延续
    分歧转一致 前日弱势(分歧/退潮/高潮) + 昨日广度确实弱(≤55%) + 今日涨停实质性增加(≥2家)
             + 今日广度≥60% + 资金由弱转强(前日<40 → 今日≥40) → 右侧买点
    高位分歧  前日成规模涨停(≥5) + 今日涨停回落 + 资金未撤(≥40) + 广度未崩(≥45) → 观察，等修复
    资金撤离  资金弱(<30) + 广度崩塌(<45) + 涨停回落 → 真退潮，清仓
    常态      其余（含无前一日数据）

    ⚠ 阈值收紧原因：原判据「d_zt>0 且 fund>=40 且 up>=55」在全市场普涨反包日会批量命中
      （实测 0916 普涨日 15/32 主题被判"分歧转一致"，等于把"稀缺右侧买点"退化为噪声）。
      收紧为：涨停需有实质增量(≥2家)、昨日广度确实弱(≤55%)、资金需由弱转强，
      使"分歧转一致"回归"主题独立走强的拐点"。

    Returns: dict{state, delta_zt, fund_now, fund_prev, up_now, up_prev, lc_prev, evidence}
    """
    sd = r.get('sentiment_detail', {}) or {}
    zt = int(sd.get('zt_count', 0) or 0)
    up = float(sd.get('up_ratio', 0) or 0)
    fund = float(r.get('fund_acc', 0) or 0)
    prev = prev_row or {}
    zt_prev = int(prev.get('zt_count', 0) or 0)
    up_prev = float(prev.get('up_ratio', 0) or 0)
    fund_prev = float(prev.get('fund_acc', 0) or 0)
    lc_prev = str(prev.get('lifecycle', '') or '')
    d_zt = zt - zt_prev

    if not prev_row:
        state = '常态'
    elif fund < 30 and up < 45 and d_zt < 0 and zt_prev >= 3:
        state = '资金撤离'
    elif (lc_prev in ('分歧', '退潮', '高潮') and d_zt >= 2 and up_prev <= 55
          and up >= 60 and fund >= 40 and fund_prev < 40):
        state = '分歧转一致'
    elif zt_prev >= 5 and d_zt < 0 and fund >= 40 and up >= 45:
        state = '高位分歧'
    elif d_zt >= 2 and zt >= 5 and fund >= 60 and up >= 65:
        state = '一致加速'
    else:
        state = '常态'

    return {
        'state': state,
        'delta_zt': d_zt,
        'zt_now': zt, 'zt_prev': zt_prev,
        'fund_now': round(fund, 1), 'fund_prev': round(fund_prev, 1),
        'up_now': round(up, 1), 'up_prev': round(up_prev, 1),
        'lc_prev': lc_prev,
        'evidence': (f"涨停{zt_prev}→{zt}({d_zt:+d})；主力资金强度{fund_prev:.0f}→{fund:.0f}；"
                     f"上涨占比{up_prev:.0f}%→{up:.0f}%" if prev_row
                     else "无上一交易日数据（单日口径）"),
    }


def _capital_state_cn(r):
    """资金状态展示：'高位分歧' + 证据（无异常时不输出常态噪声）"""
    cs = r.get('capital_state') or {}
    st = str(cs.get('state', '') or '')
    return f"{st}（{cs.get('evidence', '')}）" if st else ''


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
               # ── V4.1 三级主线门禁 ──
               "gate_tier": r.get("gate_tier", "NONE"),
               "days_strong": r.get("days_strong", 0),
               "gate_cap_amt": r.get("gate_cap_amt", 0),
               "gate_feat": r.get("gate_feat", ""),
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
    # 新增迁移预测 + V3/V4.1 列（兼容旧表）
    for col in ["migration_score REAL", "migration_direction TEXT", "target_state TEXT", "trade_action TEXT",
                "position_pct REAL", "position_label TEXT", "suggested_position TEXT",
                # V3 Rotation 引擎
                "lifecycle TEXT", "base_trade_score REAL", "final_trade_score REAL",
                "trade_rank INTEGER", "mainline_type TEXT", "fund_acc REAL",
                # V4.1 三级主线门禁
                "gate_tier TEXT", "days_strong INTEGER", "gate_cap_amt REAL", "gate_feat TEXT",
                # V1.1 主题轮动决策引擎（ThemeGate / IGE / StockOverride）
                "theme_state_v11 TEXT", "theme_quality_v11 TEXT", "theme_gate TEXT", "position_multiplier REAL",
                "ige_effective REAL", "ige_persistence REAL", "ige_mom REAL",
                "elasticity_type TEXT", "industry_lc TEXT", "elasticity_trap INTEGER",
                "t120_ok INTEGER", "stock_override INTEGER", "override_stocks TEXT", "gate_reason TEXT"]:
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
             position_pct, position_label, suggested_position,
             lifecycle, base_trade_score, final_trade_score, trade_rank, mainline_type, fund_acc,
             gate_tier, days_strong, gate_cap_amt, gate_feat,
             theme_state_v11, theme_quality_v11, theme_gate, position_multiplier,
             ige_effective, ige_persistence, ige_mom, elasticity_type, industry_lc,
             elasticity_trap, t120_ok, stock_override, override_stocks, gate_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?)""",
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
             r.get('position_pct', 0), r.get('position_label', ''), r.get('suggested_position', ''),
             r.get('lifecycle', ''), r.get('base_trade_score', 0), r.get('final_trade_score', 0),
             r.get('trade_rank', 0), r.get('mainline_type', ''), r.get('fund_acc', 0),
             r.get('gate_tier', 'NONE'), r.get('days_strong', 0),
             r.get('gate_cap_amt', 0), r.get('gate_feat', ''),
             r.get('theme_state_v11', ''), r.get('theme_quality_v11', ''),
             r.get('theme_gate', ''), r.get('position_multiplier', 1.0),
             r.get('ige_effective', 0), r.get('ige_persistence', 0), r.get('ige_mom', 0),
             r.get('elasticity_type', ''), r.get('industry_lc', ''),
             1 if r.get('elasticity_trap') else 0, 1 if r.get('t120_ok') else 0,
             1 if r.get('stock_override') else 0, str(r.get('override_stocks', '')),
             r.get('gate_reason', '')))

    # ── 主题强势股 Top5 表（含涨停时间/序号、领涨股标记）──
    cur.execute("""CREATE TABLE IF NOT EXISTS theme_top_stocks (
        trade_date TEXT, theme TEXT, rank_top INTEGER, ts_code TEXT, name TEXT,
        pct_chg REAL, lb_height INTEGER, amount REAL, strength REAL,
        is_leader INTEGER DEFAULT 0, zt_flag INTEGER DEFAULT 0,
        zt_time TEXT, zt_order INTEGER
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tts_date_theme ON theme_top_stocks(trade_date, theme)")
    cur.execute("DELETE FROM theme_top_stocks WHERE trade_date = ?", (TRADE_DATE_str,))
    top5_total = 0
    for r in results:
        for s in r.get('top5_stocks', []):
            cur.execute("""INSERT INTO theme_top_stocks
                (trade_date, theme, rank_top, ts_code, name, pct_chg, lb_height, amount,
                 strength, is_leader, zt_flag, zt_time, zt_order)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (TRADE_DATE_str, r['theme'], s['rank_top'], s['ts_code'], s['name'],
                 s['pct_chg'], s['lb_height'], s['amount'], s['strength'],
                 s['is_leader'], s['zt_flag'], s['zt_time'], s['zt_order']))
            top5_total += 1

    # ── 前兆主题表（爆发前观察池 + 子主题穿透）──
    # 口径与阈值见 detect_theme_precursors / PRECURSOR_CFG，经 backtest_theme_precursor.py 校准。
    # 仅登记观察池，不参与配仓（theme_scores.position_pct 仍是 V4.1 门禁终判）。
    cur.execute("""CREATE TABLE IF NOT EXISTS theme_precursor (
        trade_date TEXT, theme TEXT, n_hits INTEGER, hits TEXT,
        zt_count INTEGER, zt_prev INTEGER, n_resonance INTEGER,
        p5_warn INTEGER DEFAULT 0, hot_gap REAL,
        subthemes TEXT, resonance_stocks TEXT,
        lifecycle TEXT, gate_tier TEXT, precursor_score REAL
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tp_date ON theme_precursor(trade_date)")
    cur.execute("DELETE FROM theme_precursor WHERE trade_date = ?", (TRADE_DATE_str,))
    prec_total = 0
    for r in results:
        p = r.get('precursor')
        if not p:
            continue
        # 子主题与代表股压成文本，便于 tushare_quant / HTML 喂料直接引用
        sub_txt = "；".join(
            f"{s['name']}(共振{s['n_res']}家/涨停{s['n_zt']}家/均涨{s['avg_chg']:+.1f}%)"
            for s in p['subthemes'])
        stock_txt = "、".join(
            f"{s.get('name', '')}{float(s.get('pct_chg', 0) or 0):+.1f}%"
            f"/量比{float(s.get('vol_ratio_5d', 0) or 0):.1f}"
            for s in p['top_resonance'])
        cur.execute("""INSERT INTO theme_precursor
            (trade_date, theme, n_hits, hits, zt_count, zt_prev, n_resonance,
             p5_warn, hot_gap, subthemes, resonance_stocks, lifecycle, gate_tier,
             precursor_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (TRADE_DATE_str, p['theme'], p['n_hits'], "/".join(p['hits']),
             p['zt_count'], p['zt_prev'], p['n_resonance'],
             1 if p['p5_warn'] else 0, p['hot_gap'], sub_txt, stock_txt,
             p['lifecycle'], p['gate_tier'], p['precursor_score']))
        prec_total += 1

    conn.commit()
    conn.close()
    print(f"[保存] SQLite: {OUTPUT_DB} ({len(results)} 条, top5强势股 {top5_total} 条, 前兆主题 {prec_total} 条)")


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


# ═══════════════════════════════════════════════════════════
# V4.1 三级主线门禁（替代已移除的 V4 二元 _is_mainline）
#   L2 确认主线(满配) / L1 准主线(试探≤1/3) / L0 启动候选(仅登记)
#   L0->L1->L2 带宽梯度消除"74.9 vs 75"二元悬崖，介入窗口前移，
#   并强制结构确认（持续性/宽度/容量/资金/热度拦截）。
# ═══════════════════════════════════════════════════════════
STRONG_LC_V4 = {'主升', '升温'}
def _hot_overheat_v4(r):
    """热度过热拦截：热榜高位(≥85分位) 或 情绪-趋势双高潮 或 热榜相位于高潮

    降级规则：无热榜日（hot_source=zt_count_backup）热度由涨停数替代，其得分与
    历史热榜口径不可比，分位会被系统性推到高位 → 此时不参与过热判定，
    仅保留"情绪-趋势双高潮 / 相位=高潮"两条，避免强势主题被误杀。
    """
    hot_pct = float(r.get('hot_percentile', 50) or 50)
    climax = 1 if (float(r.get('trend_score', 0) or 0) >= 70
                   and float(r.get('sentiment_score', 0) or 0) >= 85) else 0
    is_proxy = str(r.get('hot_source', '') or '') == 'zt_count_backup'
    return (hot_pct >= 85 and not is_proxy) or (climax == 1) or is_hot_climax_phase(r.get('hot_phase'))


# ── V4.1 门禁口径（唯一真源：calc_mainline_tier_v4 与 _gate_gap_v41 共用同一组常量）──
GATE_L2_COMP, GATE_L2_TREND = 68.0, 68.0        # L2 标准确认：质量 + 趋势 双门槛
GATE_L2_ALT = (75.0, 65.0, 75.0)                 # L2 趋势极强通道：(趋势, 综合, 宽度)
GATE_L2_UP, GATE_L2_FUND, GATE_L2_DAYS = 60.0, 50.0, 2
GATE_L1_DAYS, GATE_L1_MIG, GATE_L1_UP = 1, 8.0, 55.0
GATE_L1_TREND_MIN, GATE_L1_COMP_MIN = 50.0, 50.0   # 试探档趋势/质量底线（剔除"涨停多但趋势没起来"的一日游）
GATE_CAPACITY_MIN = 8.0                          # 梯队最大个股成交额（亿）
GATE_EUPHORIC_UP, GATE_EUPHORIC_ZT = 95.0, 15    # 情绪透支：广度极致 / 涨停潮 → 禁 L2，降 R 等次日确认
GATE_L2_MAX_PER_DAY = 2                          # 确认主线天然稀缺：每日最多 2 个


def _is_strong_day(row):
    """单日「强态」原子判据 —— 持续性窗口计数的组成单位

    强态 = 生命周期已标主升/升温，或 当日量价三维同时成立（综合≥60 且 趋势≥60
    且 广度≥65）且近5日动量为正。后一条是为了在 lifecycle 缺失/抖动时仍能识别
    实质强势日（历史库 0727~0902 无 lifecycle 字段，仅靠标签会让持续性恒为 0）。
    """
    row = row or {}
    if str(row.get('lifecycle') or '') in ('主升', '升温'):
        return True
    return (float(row.get('composite_score') or 0) >= 60
            and float(row.get('trend_score') or 0) >= 60
            and float(row.get('up_ratio') or 0) >= 65
            and float(row.get('ret_5') or 0) > 0)


def calc_mainline_tier_v4(r, prev_lc=None, prev_state=None, hist_rows=None):
    """V4.1 分级主线判定，返回 tier ∈ {'L2','L1','R','L0','NONE'}

    结构确认（L1/L2 共用）：
      * 持续性 days_strong>=阈值 —— 近 5 日窗口内「强态」天数（含今日），非"逐日连续"
      * 强度：涨停>=4 或 趋势>=70（量或价至少一维成型）
      * 宽度：题材内上涨占比 up_ratio
      * 容量：当日梯队最大个股成交额 >=8亿（能容纳大资金）
    L2 确认主线：近5日动量 ret_5>0 + 强度确认（综合/趋势双68，或趋势75+综合65+宽度75）
        + 持续性>=2 + 结构确认 + 非情绪透支 + 非过热。
        （原为"lifecycle=='主升' 且 双75"。两处均不可达：① 双75 在真实分布下不存在
          —— 0903 以来"主升"主题综合分峰值仅 71.6；② 硬绑 lifecycle 标签，导致历史
          强共振日（趋势 80+、广度 90+、近5日正收益）因该库无 lifecycle 字段而永不入选。）
    L1 试探档：升温/分歧的加速初期，弱转强第 1 日起可试探；另有趋势/质量底线防一日游。
    R  修复跟踪档（0%观察，不配仓）：资金状态为 高位分歧/分歧转一致/一致加速 且强度未散，
        用于跟踪"下一次分歧转一致"。
    L0 仅登记：启动/升温/分歧 + (涨停>=2 或 强广度首日) + 迁移>0（过热不登记）。
    """
    sd = r.get('sentiment_detail', {}) or {}
    lc = str(r.get('lifecycle', '') or '')
    trend = float(r.get('trend_score', 0) or 0)
    sent = float(r.get('sentiment_score', 0) or 0)
    comp = float(r.get('composite_score', 0) or 0)
    mig = float(r.get('migration_score', 0) or 0)
    zt = int(sd.get('zt_count', 0) or 0)
    up_ratio = float(sd.get('up_ratio', 0) or 0)
    fund_acc = float(r.get('fund_acc', 0) or 0)
    # 兜底：主题行未显式带 ret_5 时回落到趋势明细的 avg_ret_5，避免该硬条件静默恒为 0
    ret_5 = float(r.get('ret_5', 0) or 0)
    if r.get('ret_5') is None:
        ret_5 = float((r.get('trend_detail') or {}).get('avg_ret_5', 0) or 0)
    target = str(r.get('target_state', '') or '')
    top5 = r.get('top5_stocks') or []
    max_amt = max((float(s.get('amount', 0) or 0) for s in top5), default=0.0)

    # 持续性：近 5 日窗口内强态天数（含今日）——不用"标签逐日连续"
    # 原实现 days_strong = 今日强 + 昨日强（值域 0/1/2），依赖 lifecycle 标签逐日连续，
    # 而实测「主升」仅占 1.1%（15/1306）且从不连续（0903~0917 共 15 条主升，无一与前日相连）
    # → days_strong 恒 ≤1（全量分布 {0:1202, 1:38, ≥2:0}）→ L2/L1 数学上不可达。
    # 改为窗口计数后，单日标签抖动不再打断持续性链条。
    strong_hist = sum(1 for x in (hist_rows or []) if _is_strong_day(x))
    strong_today = _is_strong_day({'lifecycle': lc, 'composite_score': comp,
                                   'trend_score': trend, 'up_ratio': up_ratio,
                                   'ret_5': ret_5})
    days_strong = int(strong_today) + strong_hist

    overheat = _hot_overheat_v4(r)
    capacity = max_amt >= GATE_CAPACITY_MIN
    # 结构确认分两档：L2 满配要求更强（量或价成型到高阶），L1 试探档放宽一档，
    # 避免"涨停/趋势都不差、只是没到满配线"的主题直接掉进杂毛区
    struct_l2 = (zt >= 4 or trend >= 70) and capacity and not overheat
    struct_l1 = (zt >= 3 or trend >= 60) and capacity and not overheat
    # 情绪透支：广度极致或涨停潮 → 当日是情绪脉冲顶点。仅禁 L2 满配（转 R 等次日确认），
    # 不拦 L1 试探（试探档本就是小仓位试错，见"分歧转一致"当日即可试探的设计）
    euphoric = (up_ratio >= GATE_EUPHORIC_UP) or (zt >= GATE_EUPHORIC_ZT)
    _alt_trend, _alt_comp, _alt_up = GATE_L2_ALT
    l2_confirm = ((comp >= GATE_L2_COMP and trend >= GATE_L2_TREND)
                  or (trend >= _alt_trend and comp >= _alt_comp and up_ratio >= _alt_up))

    # 门禁输入特征（回测校准 / 可审计）
    cap_state = str((r.get('capital_state') or {}).get('state', '') or '')
    r['days_strong'] = days_strong
    r['gate_cap_amt'] = round(max_amt, 1)
    r['gate_feat'] = json.dumps(
        {'lc': lc, 'trend': trend, 'comp': comp, 'mig': mig, 'zt': zt,
         'up': up_ratio, 'fund': fund_acc, 'hot_pct': r.get('hot_percentile', 50),
         'hot_src': r.get('hot_source', ''), 'ret5': ret_5,
         'prev_lc': prev_lc or '', 'days': days_strong, 'days_hist': strong_hist,
         'cap': round(max_amt, 1), 'overheat': int(overheat), 'euphoric': int(euphoric),
         'l2_confirm': int(l2_confirm), 'cap_state': cap_state}, ensure_ascii=False)

    # L2 确认主线：近5日动量为正 + 强度确认 + 持续性 + 结构/宽度/资金确认 + 非情绪透支
    if (ret_5 > 0 and l2_confirm and days_strong >= GATE_L2_DAYS and struct_l2
            and up_ratio >= GATE_L2_UP and fund_acc >= GATE_L2_FUND
            and not euphoric and lc not in ('高潮', '退潮')):
        return 'L2'
    # L1 准主线（试探档）：升温/分歧/主升 的加速初期，弱转强首日起可试探。
    # 同样要求近5日动量为正 —— 这条把"已涨完、近5日转负的衰减型反弹"挡在试探之外
    # （实测 0907 有 6 个主题被判"主升"，但 ret_5 全为负 -0.1~-4.8，次日全线走弱）。
    if ((lc in ('升温', '主升') or (lc == '分歧' and '分歧转一致' in target))
            and ret_5 > 0 and trend >= GATE_L1_TREND_MIN and comp >= GATE_L1_COMP_MIN
            and struct_l1 and days_strong >= GATE_L1_DAYS and mig >= GATE_L1_MIG
            and up_ratio >= GATE_L1_UP):
        return 'L1'
    # R 修复跟踪档（0%观察，不配仓）：用于跟踪"下一次分歧转一致"。
    # 三类进 R：① 资金状态异常（高位分歧/分歧转一致/一致加速，即"只是分歧不是撤离"）；
    # ② 当日或前日为「主升」但未达 L1/L2（前一天的强势品种不许直接掉进杂毛区）；
    # ③ 强度未散（涨停≥2 或 趋势≥50）。前提：cap_state 不是「资金撤离」。
    if (cap_state != '资金撤离'
            and (cap_state in ('高位分歧', '分歧转一致', '一致加速')
                 or lc == '主升' or prev_lc == '主升')
            and (zt >= 2 or trend >= 50)):
        return 'R'
    # L0 启动候选（仅登记观察，0仓）：涨停>=2，或首日强广度（up_ratio>=70 且 趋势/情绪达标，
    # 与升温判定同口径）——避免"无涨停但广度扩散"的首次升温/启动主题直接落入 NONE 回避区
    if (lc in ('启动', '升温', '分歧') and not overheat and mig > 0
            and (zt >= 2 or (up_ratio >= 70 and (trend >= 45 or sent >= 45)))):
        return 'L0'
    return 'NONE'


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


def _gate_gap_v41(r):
    """按 gate_feat 快照解析当前档位距 L1/L2 的未满足条件明细（可审计差距输出）

    Returns: ["距L1: ...", "距L2: ..."]（已达标的档位不出现在列表中）
    """
    try:
        f = json.loads(r.get('gate_feat', '{}') or '{}')
    except Exception:
        f = {}
    lc = str(f.get('lc', '') or r.get('lifecycle', '') or '')
    trend = float(f.get('trend', 0) or 0)
    comp = float(f.get('comp', 0) or 0)
    mig = float(f.get('mig', 0) or 0)
    zt = int(f.get('zt', 0) or 0)
    up = float(f.get('up', 0) or 0)
    fund = float(f.get('fund', 0) or 0)
    ret5 = float(f.get('ret5', 0) or 0)
    days = int(f.get('days', 0) or 0)
    cap = float(f.get('cap', 0) or 0)
    overheat = int(f.get('overheat', 0) or 0)
    euphoric = int(f.get('euphoric', 0) or 0)
    target = str(r.get('target_state', '') or '')
    _alt_trend, _alt_comp, _alt_up = GATE_L2_ALT

    gaps = []
    l1_items = []
    if not (lc in ('升温', '主升') or (lc == '分歧' and '分歧转一致' in target)):
        l1_items.append(f"生命周期{lc or '—'}∉升温/主升/分歧转一致")
    if ret5 <= 0:
        l1_items.append(f"近5日动量{ret5:+.1f}%≤0")
    if trend < GATE_L1_TREND_MIN:
        l1_items.append(f"趋势{trend:.0f}<{GATE_L1_TREND_MIN:.0f}")
    if comp < GATE_L1_COMP_MIN:
        l1_items.append(f"综合{comp:.0f}<{GATE_L1_COMP_MIN:.0f}")
    if not (zt >= 3 or trend >= 60):
        l1_items.append(f"强度不足(涨停{zt}<3且趋势{trend:.0f}<60)")
    if cap < GATE_CAPACITY_MIN:
        l1_items.append(f"容量{cap:.1f}亿<{GATE_CAPACITY_MIN:.0f}亿")
    if overheat:
        l1_items.append("热度过热")
    if mig < GATE_L1_MIG:
        l1_items.append(f"迁移{mig:.1f}<{GATE_L1_MIG:.0f}")
    if up < GATE_L1_UP:
        l1_items.append(f"宽度{up:.0f}%<{GATE_L1_UP:.0f}%")
    if l1_items:
        gaps.append("距L1: " + "、".join(l1_items))

    l2_items = []
    if ret5 <= 0:
        l2_items.append(f"近5日动量{ret5:+.1f}%≤0（衰减反弹不确认）")
    if not ((comp >= GATE_L2_COMP and trend >= GATE_L2_TREND)
            or (trend >= _alt_trend and comp >= _alt_comp and up >= _alt_up)):
        l2_items.append(f"强度未确认(未达综合{GATE_L2_COMP:.0f}+趋势{GATE_L2_TREND:.0f}，"
                        f"也未达趋势{_alt_trend:.0f}+综合{_alt_comp:.0f}+宽度{_alt_up:.0f}；"
                        f"现值 综合{comp:.0f}/趋势{trend:.0f}/宽度{up:.0f})")
    if days < GATE_L2_DAYS:
        l2_items.append(f"持续性{days}/{GATE_L2_DAYS}日")
    if not (zt >= 4 or trend >= 70):
        l2_items.append(f"强度不足(涨停{zt}<4且趋势{trend:.0f}<70)")
    if cap < GATE_CAPACITY_MIN:
        l2_items.append(f"容量{cap:.1f}亿<{GATE_CAPACITY_MIN:.0f}亿")
    if overheat:
        l2_items.append("热度过热")
    if euphoric:
        l2_items.append("情绪透支(广度≥95%或涨停≥15家→转R等次日确认)")
    if up < GATE_L2_UP:
        l2_items.append(f"宽度{up:.0f}%<{GATE_L2_UP:.0f}%")
    if fund < GATE_L2_FUND:
        l2_items.append(f"主力强度{fund:.0f}<{GATE_L2_FUND:.0f}")
    if lc in ('高潮', '退潮'):
        l2_items.append(f"生命周期{lc}禁入")
    if l2_items:
        gaps.append("距L2: " + "、".join(l2_items))
    return gaps


def _junk_reeval_cond(r):
    """回避主题的再评估触发条件（按生命周期给出可观察信号）"""
    lc = str(r.get('lifecycle', '') or '')
    ts = str(r.get('target_state', '') or '')
    if lc == '退潮' or '退潮' in ts:
        return "缩量企稳3日 + 龙头反包涨停"
    if lc == '高潮' or '高潮' in ts:
        return "高潮转分歧后首日强承接（涨停≥5）"
    if lc == '分歧' or '分歧' in ts:
        return "分歧转一致（涨停≥5 + 龙头封板）"
    if lc == '启动' or '启动' in ts:
        return "涨停≥5 + 梯队最大成交额≥8亿"
    return "趋势站回20日线 + 涨停≥3 + 迁移>10"


def _repair_trigger(r):
    """R 修复跟踪档的「转一致」确认条件（按资金状态给出次日可观察信号）"""
    cs = r.get('capital_state') or {}
    st = str(cs.get('state', '') or '')
    zt_prev = int(cs.get('zt_prev', 0) or 0)
    if st == '高位分歧':
        return f"涨停数回升至≥{max(5, zt_prev)}家 + 龙头封板 + 主力资金转正（三者同时满足才升 L1）"
    if st == '分歧转一致':
        return "次日涨停数不减少 + 封板率≥80% → 可直接升 L1 试探"
    if st == '一致加速':
        return "趋势/广度不回落 + 炸板率<40% 连续2日 → 升 L1；断板即离场"
    return "涨停≥5 + 梯队最大成交额≥8亿"


# ══════════════════════════════════════════════════════════════
# V1.1 主题轮动决策引擎：ThemeQuality × ThemeState × IGE × StockOverride
# 只改主题决策层——不改原主题评分/排序/IGE/SIA/Trade Execution 计算。
# 主题决定风险预算与交易权限（ThemeGate），个股强度决定最终执行（StockOverride）。
# ══════════════════════════════════════════════════════════════

GATE_ORDER_V11 = ['OPEN', 'SELECTIVE', 'ER20_ONLY', 'HOLD_ONLY', 'BLOCK']
GATE_MULT_V11 = {'OPEN': 1.00, 'SELECTIVE': 0.70, 'ER20_ONLY': 0.50, 'HOLD_ONLY': 0.0, 'BLOCK': 0.0}
_IGE_TYPE_PERSIST_V11 = {'GROWTH': 70, 'TECHNOLOGY': 70, 'CYCLICAL': 55, 'PRICE_DRIVEN': 45,
                         'FINANCIAL_BETA': 40, 'DEFENSIVE': 50, 'LOW_ELASTICITY': 30}
_IGE_LC_PERSIST_ADJ_V11 = {'EARLY_EXPANSION': 15, 'ACCELERATION': 20, 'MATURE_GROWTH': 5,
                           'DECELERATION': -10, 'CONTRACTION': -20}


def _fnum_v11(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _load_ige_v11(trade_date):
    """加载 IGE v1.2 输出（个股全表 + ER20候选表），供 ThemeGate / StockOverride 使用。

    不重新设计行业弹性模型：ThemeElasticity = IGE_EFFECTIVE(ige_adj)；
    ThemePersistence 由 elasticity_type + industry_lifecycle + acceleration_confirm 派生；
    IGE_MOM = acceleration_score。Returns: {code6: info} / 数据缺失返回 {}
    """
    base = os.path.join(BASE_DIR, 'ige', 'output')
    stock_map = {}
    fp = os.path.join(base, f'ige_full_{trade_date}.csv')
    if os.path.exists(fp):
        try:
            with open(fp, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    code = str(row.get('code', '') or '')
                    if len(code) >= 6:
                        stock_map[code[:6]] = {
                            'sia': _fnum_v11(row.get('sia')), 'ige_adj': _fnum_v11(row.get('ige_adj')),
                            'mom': _fnum_v11(row.get('acceleration_score')),
                            'el_type': row.get('industry_elasticity_type', '') or '',
                            'el_state': row.get('industry_elasticity_state', '') or '',
                            'ind_lc': row.get('industry_lifecycle', '') or '',
                            'accel': str(row.get('acceleration_confirm', '')).strip().lower() == 'true',
                        }
        except Exception as e:
            print(f"[V1.1] ige_full 读取失败: {e}")
    fp2 = os.path.join(base, f'ige_primary_buy_{trade_date}.csv')
    if os.path.exists(fp2):
        try:
            with open(fp2, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    code = str(row.get('code', '') or '')
                    if len(code) < 6:
                        continue
                    s = stock_map.setdefault(code[:6], {})
                    s['er20_ok'] = str(row.get('ok', '')).strip().lower() == 'true'
                    s['breakout'] = str(row.get('breakout_5d', '')).strip().lower() == 'true'
                    s['retest'] = (str(row.get('reclaim_ma10_today', '')).strip().lower() == 'true'
                                   or str(row.get('reclaim_2d', '')).strip().lower() == 'true')
        except Exception as e:
            print(f"[V1.1] ige_primary_buy 读取失败: {e}")
    return stock_map


def _theme_state_v11(r):
    """ThemeState 七态（与 ThemeQuality 彻底分离）：V3 lifecycle + target_state 映射，不改原计算"""
    lc = str(r.get('lifecycle', '') or '')
    target = str(r.get('target_state', '') or '')
    if lc == '主升':
        return '一致'
    if lc == '分歧' and '一致' in target:
        return '分歧转一致'
    return lc if lc in ('启动', '升温', '分歧', '高潮', '退潮') else '分歧'


def _theme_quality_v11(r):
    """ThemeQuality 五档：mainline_quality 分数分层映射（保留原评分体系，只做分层）"""
    q = _fnum_v11(r.get('mainline_quality', 0))
    if q >= 90:
        return 'CORE'
    if q >= 80:
        return 'STRONG'
    if q >= 70:
        return 'NORMAL'
    if q >= 60:
        return 'WEAK'
    return 'AVOID'


def _stock_explosion_v11(s):
    """个股爆发分(0-100)：涨停/连板驱动 + 20日新高 + 量能（≥80=极强爆发，炸板扣分）"""
    pct = _fnum_v11(s.get('pct_chg'))
    lb = int(_fnum_v11(s.get('lb_height')))
    score = 0.0
    if pct >= 9.7:
        score += 45
    elif pct >= 7:
        score += 30
    elif pct >= 5:
        score += 18
    elif pct >= 3:
        score += 8
    score += min(lb, 4) * 10
    if _fnum_v11(s.get('high_20_b'), -100) >= 0 or int(_fnum_v11(s.get('new_high_flag'))) == 1:
        score += 15
    amt = _fnum_v11(s.get('amount_latest'))
    if amt >= 25:
        score += 15
    elif amt >= 10:
        score += 10
    elif amt >= 5:
        score += 5
    if int(_fnum_v11(s.get('boom_flag'))) == 1:
        score -= 10
    return max(0.0, min(100.0, score))


def calc_theme_gate_v11(r, stock_map):
    """V1.1 ThemeGate：输出 OPEN/SELECTIVE/ER20_ONLY/HOLD_ONLY/BLOCK + PositionMultiplier + StockOverride

    优先级：①ThemeState ②ThemeQuality ③ThemeElasticity ④ThemePersistence ⑤先验胜率 ⑥转化概率
    高潮无论质量多高 → HOLD_ONLY；BLOCK 仅由显式规则触发（AVOID×坏状态 / 双极低），StockOverride 不可突破。
    """
    state = _theme_state_v11(r)
    qual = _theme_quality_v11(r)
    rows = r.get('stock_rows', []) or []

    # ThemeElasticity / Persistence / IGE_MOM：取主题成员股所覆盖行业链的最大有效弹性
    best = None
    for s in rows:
        info = stock_map.get(str(s.get('ts_code', ''))[:6])
        if info and (best is None or info.get('ige_adj', 0) > best.get('ige_adj', 0)):
            best = info
    ela_type, el_state, ind_lc, mom = '', '', '', 0.0
    if best:
        ela = round(best.get('ige_adj', 0.0), 1)
        ela_type, el_state, ind_lc = best.get('el_type', ''), best.get('el_state', ''), best.get('ind_lc', '')
        mom = round(best.get('mom', 0.0), 1)
        per = round(max(0.0, min(100.0, _IGE_TYPE_PERSIST_V11.get(ela_type, 50)
                                 + _IGE_LC_PERSIST_ADJ_V11.get(ind_lc, 0)
                                 + (8 if best.get('accel') else 0))), 1)
    else:
        # 无 IGE 覆盖 → 中性弹性50（不足以支持 T120≥75，也不视为极低）
        ela, per = 50.0, 50.0
    trap = 'ELASTICITY_TRAP' in str(el_state)

    wr = _est_winrate(r)
    prob = _est_mainline_prob(r)

    # ── 门禁判定（状态优先于质量）──
    reasons = []
    if qual == 'AVOID' and state in ('退潮', '分歧', '高潮'):
        gate = 'BLOCK'
        reasons.append(f'质量AVOID×状态{state}')
    elif per <= 30 and ela <= 40:
        gate = 'BLOCK'
        reasons.append(f'Persistence{per:.0f}/Elasticity{ela:.0f}双极低')
    elif state == '高潮':
        gate = 'HOLD_ONLY'
        reasons.append('高潮：禁止新增追涨，仅持仓管理')
    elif state == '退潮':
        gate = 'HOLD_ONLY'
        reasons.append('退潮：降低交易权限，仅持仓管理')
    elif qual in ('CORE', 'STRONG') and state in ('启动', '升温', '分歧转一致', '一致'):
        gate = 'OPEN'
    elif qual == 'STRONG' and state == '分歧':
        gate = 'SELECTIVE'
        reasons.append('强主线分歧：仅强个股/确认突破/回踩确认')
    elif qual == 'WEAK':
        gate = 'ER20_ONLY'
        reasons.append(f'弱主题{state}：仅ER20，仓位减半')
    elif qual == 'NORMAL' and ela < 75:
        gate = 'ER20_ONLY'
        reasons.append(f'弹性不足({ela:.0f}<75)：不支持T120，仅ER20')
    elif qual == 'NORMAL':
        gate = 'SELECTIVE'
    elif qual == 'AVOID' and state in ('启动', '升温') and any(
            ((stock_map.get(str(s.get('ts_code', ''))[:6]) or {}).get('sia') or 0) >= 85 for s in rows):
        gate = 'ER20_ONLY'
        _sia_leads = []
        for s in rows:
            _m = stock_map.get(str(s.get('ts_code', ''))[:6]) or {}
            if (_m.get('sia') or 0) >= 85:
                _sia_leads.append({
                    'code': str(s.get('ts_code', ''))[:6],
                    'name': s.get('name', '') or '—',
                    'sia': float(_m.get('sia', 0) or 0),
                })
        _sia_leads.sort(key=lambda x: x['sia'], reverse=True)
        r['sia_leading_stocks'] = _sia_leads[:3]
        reasons.append(f'AVOID×{state}但个股领先(SIA≥85)：仅ER20，仓位减半')
    elif qual == 'AVOID':
        gate = 'HOLD_ONLY'
        reasons.append(f'质量AVOID（{state}）：不开新仓')
    else:
        gate = 'HOLD_ONLY'
        reasons.append('兜底：不开新仓')

    # 先验胜率/转化概率 → 门禁升降一级（高潮/退潮不重开追涨权限；降级不自动到 BLOCK）
    if state not in ('高潮', '退潮'):
        _i = GATE_ORDER_V11.index(gate)
        if wr < 40 and prob < 30:
            gate = GATE_ORDER_V11[min(_i + 1, GATE_ORDER_V11.index('HOLD_ONLY'))]
            reasons.append(f'胜率{wr}/转化{prob}%双低 → 降一级')
        elif wr >= 55 and prob >= 50:
            gate = GATE_ORDER_V11[max(_i - 1, 0)]
            reasons.append(f'胜率{wr}/转化{prob}%双高 → 升一级')

    # T120_ROCKET：必须 OPEN + 行业/主题支持；ELASTICITY_TRAP 一票否决
    t120_ok = (gate == 'OPEN' and ela >= 75 and per >= 60 and mom >= 60
               and ind_lc in ('EARLY_EXPANSION', 'ACCELERATION') and not trap)

    # ── StockOverride：仅 ThemeGate=ER20_ONLY 时极强个股可有限突破（BLOCK 不可突破）──
    # 基础：SIA≥85 AND 过ER20技术门槛 AND 爆发分≥80 AND Breakout
    # 升级（ER20_PRIORITY）：SIA≥90 AND 爆发分≥85 AND Retest成功；仓位≤标准×50%
    override_stocks, override_flag = [], False
    if gate == 'ER20_ONLY':
        for s in rows:
            info = stock_map.get(str(s.get('ts_code', ''))[:6])
            if not info:
                continue
            sia = _fnum_v11(info.get('sia'))
            exp = _stock_explosion_v11(s)
            brk = bool(info.get('breakout')) or int(_fnum_v11(s.get('new_high_flag'))) == 1
            if not (sia >= 85 and info.get('er20_ok') and exp >= 80 and brk):
                continue
            premium = sia >= 90 and exp >= 85 and bool(info.get('retest'))
            override_stocks.append({'code': s.get('ts_code', ''), 'name': s.get('name', ''),
                                    'sia': round(sia, 1), 'explosion': round(exp), 'premium': premium})
            override_flag = True

    r.update({
        'theme_state_v11': state, 'theme_quality_v11': qual,
        'ige_effective': ela, 'ige_persistence': per, 'ige_mom': mom,
        'elasticity_type': ela_type, 'industry_lc': ind_lc, 'elasticity_trap': trap,
        'theme_gate': gate, 'position_multiplier': GATE_MULT_V11[gate],
        't120_ok': t120_ok, 'stock_override': override_flag,
        'override_stocks': override_stocks, 'gate_reason': '；'.join(reasons),
    })
    return r


def apply_theme_gate_v11(results, trade_date):
    """V1.1 决策层入口：加载 IGE 输出 → 逐主题计算 ThemeGate/StockOverride（就地写回 r 字段）"""
    stock_map = _load_ige_v11(trade_date)
    if not stock_map:
        print("[V1.1] 未找到 IGE 输出（先运行 ige 模块），ThemeGate 按中性弹性兜底")
    for r in results:
        calc_theme_gate_v11(r, stock_map)
    gates = {}
    for r in results:
        gates[r['theme_gate']] = gates.get(r['theme_gate'], 0) + 1
    ov = sum(1 for r in results if r.get('stock_override'))
    print(f"[V1.1] ThemeGate 分布: {gates} | StockOverride=TRUE: {ov}")


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


def _resolve_subthemes(theme_name, subtheme_map):
    """取主题对应的子主题配置；未命中则按名称双向包含模糊匹配，仍无则以主题名自兜底"""
    subs = subtheme_map.get(theme_name)
    if not subs:
        for k in subtheme_map:
            if theme_name in k or k in theme_name:
                subs = subtheme_map[k]
                break
    if not subs:
        # 兜底：无子主题配置时，用主题本身作为子主题（保证每条主线都有穿透输出）
        subs = {theme_name: {}}
    return subs


def _attribute_subthemes(theme_name, rows, theme_stock_map, subtheme_map):
    """成份股 → 子主题 归属（前兆穿透与主线穿透共用）

    匹配优先级：核心公司名(100) > 行业(40, 双向子串含"专用机械→机械/化工原料→化工") >
                名称关键词(20)
    未匹配股票不丢弃：纯兜底主题全归入主题本身，其余归入"未细分"桶
    （否则涨停股被丢弃 → 涨停集中度/放量共振家数统计失真）

    Returns: {子主题名: [个股row]}，保证非空
    """
    subs = _resolve_subthemes(theme_name, subtheme_map)
    # 无子主题配置的纯兜底（如小金属）：未匹配股票归入主题本身，而不是"未细分"
    fallback_only = not any(cfg for cfg in subs.values())

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
    return sub_stocks


def analyze_mainline_penetration(theme_name, rows, theme_stock_map, subtheme_map, mf_map):
    """主线细分穿透算法：定位最佳子主题 + 龙头/中军

    Step1: 将主线成份股按子主题归属（核心公司名 > 行业 > 关键词）
    Step2: 子主题得分 = 涨停集中度(涨停数*2+最高连板)*3 + 资金迁移(净流入万元/1e5)
           → 锁定 TOP1 最佳子主题
    Step3: 最佳子主题内选龙头（市值50~300亿、连板/涨停/领涨最优）
           与中军（市值>500亿、日成交额最高）

    Returns: dict 或 None
    """
    sub_stocks = _attribute_subthemes(theme_name, rows, theme_stock_map, subtheme_map)

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


# ══════════════════════════════════════════════════════════════
# 前兆主题探测（爆发前 3~5 日）+ 子主题穿透
# ══════════════════════════════════════════════════════════════
# 口径与阈值来自 backtest_theme_precursor.py（20260724~20260915，38 交易日 / 64 次主题爆发）：
#   P3 涨停递增     当日涨停数 >= 3 且 > 前一交易日
#                  窗口口径 lift 1.88；当日口径 P(爆发|信号) 39.5% vs 无信号 27.4%，lift 1.44
#   P6 个股放量共振  主题内 量比(前5日均量)>=2.0 且 涨幅>=3.0% 的成份股 >= 3 家
#                  窗口口径 lift 1.62；当日口径 P(爆发|信号) 42.9% vs 无信号 19.9%，lift 2.16
#   P5 热度跳升     热度超前5日均值 >= 30（仅作拥挤警告）
#                  信号后 5 日主题超额 -1.99%（t=-4.34），显著为负 → 不是买点
# 已回测剔除（无区分度或反向，不纳入前兆体系）：
#   P1 广度普涨(up_ratio>=70) lift 0.94 / P4 迁移分抬升(mig>=10) lift 0.97 /
#   P7 龙头率先涨停 lift 0.80（反向）/ P2 资金流放大（fund_acc 历史覆盖不足，样本<20）
PRECURSOR_CFG = {
    'p3_zt': 3,       # P3 涨停递增：涨停家数下限
    'p6_n': 3,        # P6 放量共振：最少家数
    'p6_vr': 2.0,     # P6 放量共振：量比下限（前5日均量基准）
    'p6_chg': 3.0,    # P6 放量共振：涨幅下限 %
    'p5_hot': 30.0,   # P5 热度跳升：热度超前5日均值下限
}
# 前兆 → 爆发概率提升倍数（回测当日口径 lift），用于观察池排序加权
PRECURSOR_LIFT = {'P6': 2.16, 'P3': 1.44}


_PREV_ROW_COLS = ('lifecycle', 'theme_state', 'zt_count', 'up_ratio', 'hot_score', 'fund_acc',
                  'composite_score', 'trend_score', 'sentiment_score', 'target_state',
                  'migration_score', 'gate_tier', 'ret_5')


def _load_prev_theme_rows(trade_date, days=5):
    """读 theme_scores.db 最近 days 个交易日（不含当日）的主题行

    Returns: (prev_rows, hist_rows)
      prev_rows: {theme: 上一交易日行 dict}
      hist_rows: {theme: [近 days 日行 dict]}（含 trade_date，按查询顺序）

    必须直读本库，不能用主题引擎的 get_prev_day_theme_data()：它读
    cache_backbone_tushare/theme_trend_sentiment.db，该库
      (a) 日期稀疏（实测最新为 0805/0806/0807/0811 + 当日），"前一日"会落到一个月前；
      (b) 无 lifecycle 字段 → prev_lc 恒为空串 → strong_prev 恒 False →
          days_strong 上限 1，而 L2/L1 硬要求 ≥2 → 实测 40 个交易日 L2/L1 命中 0 次。
    取到错误"前一日"的后果：P3 涨停递增全部退化为"昨0"、持续性/资金撤离判定失真。
    """
    if not os.path.exists(OUTPUT_DB):
        return {}, {}
    conn = sqlite3.connect(OUTPUT_DB)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM theme_scores WHERE trade_date < ? "
                    "ORDER BY trade_date DESC LIMIT ?", (str(trade_date), int(days)))
        dts = [row[0] for row in cur.fetchall()]  # 降序，dts[0] = 上一交易日
        if not dts:
            return {}, {}
        ph = ",".join("?" * len(dts))
        cur.execute(f"SELECT theme, trade_date, {', '.join(_PREV_ROW_COLS)} FROM theme_scores "
                    f"WHERE trade_date IN ({ph})", dts)
        keys = ('theme', 'trade_date') + _PREV_ROW_COLS
        prev_rows, hist_rows = {}, defaultdict(list)
        for row in cur.fetchall():
            d = dict(zip(keys, row))
            hist_rows[d['theme']].append(d)
            if d['trade_date'] == dts[0]:
                prev_rows[d['theme']] = d
        return prev_rows, hist_rows
    finally:
        conn.close()


def _prec_feat(s):
    """个股是否满足 P6 放量共振：量比(前5日均量)>=2.0 且 涨幅>=3.0%"""
    return (float(s.get('vol_ratio_5d', 0) or 0) >= PRECURSOR_CFG['p6_vr']
            and float(s.get('pct_chg', 0) or 0) >= PRECURSOR_CFG['p6_chg'])


def detect_theme_precursors(results, theme_stock_map, subtheme_map, prev_stats=None):
    """探测"前兆主题"并穿透到驱动子主题，就地写入 r['precursor']

    前兆（严格档，阈值均经回测）：
      P3 涨停递增      当日涨停数 >= 3 且 > 前一交易日
      P6 个股放量共振   量比(前5日均量)>=2.0 且涨幅>=3.0% 的成份股 >= 3 家
      P5 热度跳升      热度超前5日均值 >= 30 —— 拥挤警告，信号后 5 日超额显著为负

    不改变配仓（配仓仍由 V4.1 门禁决定）：前兆只用于收敛观察范围，
    入场必须靠个股层真突破+真放量。

    Returns: list[dict]，按 (命中前兆数, 放量共振家数) 降序；仅拥挤主题排在末尾
    """
    prev_stats = prev_stats or {}
    out = []
    for r in results:
        rows = r.get('stock_rows', []) or []
        if not rows:
            continue
        sd = r.get('sentiment_detail', {}) or {}
        zt = int(sd.get('zt_count', 0) or 0)
        st = prev_stats.get(r['theme']) or {}
        zt_prev = int(st.get('zt_prev', 0) or 0)

        reso = [s for s in rows if _prec_feat(s)]
        hits = []
        if zt >= PRECURSOR_CFG['p3_zt'] and zt > zt_prev:
            hits.append('P3')
        if len(reso) >= PRECURSOR_CFG['p6_n']:
            hits.append('P6')

        hs = st.get('hot') or []
        hot_gap = (float(r.get('hot_score', 0) or 0) - float(np.mean(hs))) if hs else 0.0
        p5_warn = bool(hs) and hot_gap >= PRECURSOR_CFG['p5_hot']
        if not hits and not p5_warn:
            continue  # 既无前兆也无拥挤警告，不进观察池

        # ── 子主题穿透：复用 subtheme_map 归属，按"放量共振家数→涨停数→均涨幅"排序 ──
        sub_stocks = _attribute_subthemes(r['theme'], rows, theme_stock_map, subtheme_map)
        subs = []
        for sub_name, srows in sub_stocks.items():
            if sub_name == '未细分' and len(sub_stocks) > 1:
                # 映射未覆盖的桶：仅当其中有放量共振/涨停个股时才展示
                # （否则会出现"放量共振4家"但三个子主题全为0的误导性输出）
                if not any(_prec_feat(s) or s.get('zt_flag', 0) == 1 for s in srows):
                    continue
            s_reso = [s for s in srows if _prec_feat(s)]
            s_zt = sum(1 for s in srows if s.get('zt_flag', 0) == 1)
            chgs = [float(s.get('pct_chg', 0) or 0) for s in srows]
            subs.append({
                'name': sub_name,
                'n_res': len(s_reso),
                'n_zt': s_zt,
                'avg_chg': float(np.mean(chgs)) if chgs else 0.0,
                'stocks': sorted(s_reso, key=lambda x: (float(x.get('vol_ratio_5d', 0) or 0),
                                                        float(x.get('pct_chg', 0) or 0)),
                                 reverse=True)[:3],
            })
        subs.sort(key=lambda x: (x['n_res'], x['n_zt'], x['avg_chg']), reverse=True)

        top_reso = sorted(reso, key=lambda x: (float(x.get('vol_ratio_5d', 0) or 0),
                                               float(x.get('pct_chg', 0) or 0)),
                          reverse=True)[:3]

        item = {
            'theme': r['theme'],
            'hits': hits,
            'n_hits': len(hits),
            'zt_count': zt,
            'zt_prev': zt_prev,
            'n_resonance': len(reso),
            'p5_warn': p5_warn,
            'hot_gap': round(hot_gap, 1),
            'top_resonance': top_reso,
            'subthemes': subs[:3],
            'lifecycle': r.get('lifecycle', ''),
            'gate_tier': r.get('gate_tier', 'NONE'),
            # 观察池排序键 = 命中前兆的回测 lift 之和（P6 2.16 > P3 1.44）
            'precursor_score': round(sum(PRECURSOR_LIFT.get(p, 1.0) for p in hits), 2),
        }
        r['precursor'] = item
        out.append(item)

    out.sort(key=lambda x: (x['n_hits'], x['n_resonance'], x['precursor_score']), reverse=True)
    return out


def _prec_stock_str(s):
    """个股展示：名称 涨幅%(量比)"""
    return (f"{s.get('name', '')} {float(s.get('pct_chg', 0) or 0):+.1f}%"
            f"(量比{float(s.get('vol_ratio_5d', 0) or 0):.1f})")


def _precursor_report_lines(prec_list):
    """前兆主题与子主题的报告文本行（无段落缩进，移动端友好）"""
    lines = []
    lines.append("━" * 60)
    lines.append("### ★ 前兆主题与子主题（爆发前 3~5 日观察池）")
    lines.append("━" * 60)
    lines.append("")
    lines.append("说明：前兆口径经 38 交易日 / 64 次主题爆发回测校准（backtest_theme_precursor.py，严格档）")
    lines.append("      P6 个股放量共振（量比≥2.0 且涨幅≥3% 的家数≥3）：未来5日爆发概率 42.9% vs 无信号 19.9%，lift 2.16")
    lines.append("      P3 涨停递增（涨停≥3 家且超前一交易日）：未来5日爆发概率 39.5% vs 无信号 27.4%，lift 1.44")
    lines.append("      ⚠ P5 热度跳升（热度超前5日均值30+）：信号后5日超额 -1.99%(t=-4.34)，是拥挤警告，不是买点")
    lines.append("      前兆只用于收敛观察范围，不改变配仓；入场仍需个股层真突破+真放量，勿凭前兆直接买入")
    lines.append("─" * 120)

    pool = [p for p in prec_list if p['n_hits'] > 0]
    warn = [p for p in prec_list if p['n_hits'] == 0 and p['p5_warn']]
    if not pool and not warn:
        lines.append("* 今日无主题出现前兆信号（P3/P6 均未触发），不产生前兆观察池")
        lines.append("")
        return lines

    for p in pool:
        warn_tag = "  ⚠拥挤警告" if p['p5_warn'] else ""
        lines.append(f"* 【{'/'.join(p['hits'])}】{p['theme']}（生命周期 {p['lifecycle']}｜档位 {p['gate_tier']}）"
                     f" 涨停{p['zt_count']}家(昨{p['zt_prev']})｜放量共振{p['n_resonance']}家"
                     f"｜前兆分 {p['precursor_score']}{warn_tag}")
        if p['p5_warn']:
            lines.append(f"  - 热度超前5日均值 +{p['hot_gap']:.1f}，属拥挤区；前兆信号后5日超额为负，只做观察不追高")
        for sub in p['subthemes']:
            reps = "、".join(_prec_stock_str(s) for s in sub['stocks']) or "—"
            lines.append(f"  - 子主题：{sub['name']}（放量共振{sub['n_res']}家/涨停{sub['n_zt']}家/"
                         f"均涨{sub['avg_chg']:+.1f}%）代表：{reps}")
        if not p['subthemes']:
            reps = "、".join(_prec_stock_str(s) for s in p['top_resonance']) or "—"
            lines.append(f"  - 放量共振代表股：{reps}")
    if warn:
        names = "、".join(f"{x['theme']}(+{x['hot_gap']:.0f})" for x in warn)
        lines.append(f"* ⚠ 仅拥挤警告（无 P3/P6 前兆）：{names}")
        lines.append("  - 热度跳升但缺放量共振/涨停递增配合，属情绪透支区，回避追高")
    lines.append("")
    return lines


def _apply_gate_v41_alloc(l2, l1, r_rep, l0, junk, ma):
    """V4.1 分级配仓归一化（报告输出层唯一配仓入口，替代已移除的 V4 _apply_rotation_v4）

    分级：
      * L2 确认主线 → 满配池（大盘目标仓位或默认30%上限），按 final_trade_score 比例折算，
        龙头/中军 4:6 穿透（与原 V4 主线一致）
      * L1 准主线试探档 → 试探总池 = 满配池 × 1/3，按 final_trade_score 比例分配；
        大盘 mainline_only（只做主线）时清零，动作改观望
      * R 修复跟踪档 → 0%（仅跟踪分歧转一致，不承担隔夜风险）
      * L0 启动候选 → 0%（仅登记观察）
      * junk(NONE) → 0% 清仓回避

    就地回写 r['position_pct'] / position_label / trade_action / leader_target_pos 等，
    使报告正文、决策表与 save_to_sqlite_v2 落库口径一致。
    """
    cap_limit = ((ma.get('target_pos') or 0) / 100.0) if ma.get('target_pos') else 0.30
    strict = ma.get('mainline_only')

    # ── L2 满配池 ──
    raw_w = [max(0.0, float(r.get('final_trade_score', 0) or 0)) for r in l2]
    total_w = sum(raw_w)
    if total_w <= 0 and l2:
        total_w = float(len(l2))
    if l2 and total_w > 0:
        for r in l2:
            raw = max(0.0, float(r.get('final_trade_score', 0) or 0))
            allocated = round(raw / total_w * cap_limit, 4)
            r['allocated_position'] = allocated
            r['leader_target_pos'] = round(allocated * 0.40, 4)
            r['core_target_pos'] = round(allocated * 0.60, 4)
            r['position_pct'] = round(allocated * 100, 1)
            r['position_label'] = '主线配仓'

    # ── L1 试探档（总额 ≤ 满配池×1/3；只做主线模式下不试探）──
    probe_cap = cap_limit / 3.0
    for r in l1:
        r['leader_target_pos'] = 0.0
        r['core_target_pos'] = 0.0
    if strict:
        for r in l1:
            r['position_pct'] = 0.0
            r['position_label'] = '试探档(0%)'
            r['trade_action'] = '空仓观望（只做主线）'
    elif l1:
        raw_p = [max(0.0, float(r.get('final_trade_score', 0) or 0)) for r in l1]
        total_p = sum(raw_p) or float(len(l1))
        for r in l1:
            raw = max(0.0, float(r.get('final_trade_score', 0) or 0))
            allocated = round(raw / total_p * probe_cap, 4)
            r['allocated_position'] = allocated
            r['position_pct'] = round(allocated * 100, 1)
            r['position_label'] = '试探档'
            r['trade_action'] = r.get('trade_action') or '试探建仓(≤1/3仓)'

    # ── L0 启动候选：0% 观察 ──
    for r in l0:
        r['position_pct'] = 0.0
        r['position_label'] = '观察(0%)'
        r['leader_target_pos'] = 0.0
        r['core_target_pos'] = 0.0
        if '建仓' in str(r.get('trade_action', '')) or '加仓' in str(r.get('trade_action', '')):
            r['trade_action'] = '空仓观望 / 启动候选跟踪'

    # ── R 修复跟踪档：0% 观察（等分歧转一致，不承担隔夜风险）──
    for r in r_rep:
        r['position_pct'] = 0.0
        r['position_label'] = '观察(0%)'
        r['allocated_position'] = 0.0
        r['leader_target_pos'] = 0.0
        r['core_target_pos'] = 0.0
        r['trade_action'] = '修复跟踪（0%观察·等分歧转一致）'



def save_to_text_report_v2(results, kg_v3_cfg, en_to_cn, market_ret_10=0.0, etf_kline_map=None):
    """生成 V3 规范文本报告（主线优先结构，对接实盘 QMT/CTP）

    结构：
      0. 大盘择时指令（读取 market_analysis 报告）
      1. 第一部分：核心主线阵营（建议配仓 80%~90%）+ 主线细分穿透（最佳子主题/龙头/中军）
      2. 第二部分：潜在轮动与接力机会（L1 试探≤1/3池 / R 修复跟踪0% / L0 观察0%）
      2.5 前兆主题与子主题（爆发前 3~5 日观察池，不参与配仓）
      3. 第三部分：杂毛/退潮与风险回避区（资金撤离型，建议仓位 0%）
      3.5 重点主题深度分析（高潮=风险处置 / 启动=机会跟踪）
      4. 主线与轮动交易决策表（全量，含主线属性 / 胜率 / 转化概率）
      5. 机构配置策略建议（整体仓位 / 核心风险）

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

    # 按 Trade 排序
    results_trade = sorted(results, key=lambda x: x.get('final_trade_score', 0), reverse=True)

    # 大盘择时指令
    ma = _load_market_directive(TRADE_DATE_str)

    w("━" * 60)
    w(f"  主题评分分析报告 V4（主线优先 · V4.1 主升门禁）- {TRADE_DATE_str}")
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

    # 市场状态统一口径仲裁：动作/策略侧（带数据）> 一句话描述；冲突时显式标注修正
    def _extract_regime(*texts):
        joined = " ".join(t for t in texts if t)
        if '弱势' in joined:
            return '弱势'
        if '强势' in joined:
            return '强势'
        if '震荡' in joined:
            return '震荡'
        return ''
    regime_action = _extract_regime(ma['action'], ma['strategy'])
    regime_dir = _extract_regime(ma['directive'])
    if regime_action and regime_dir and regime_action != regime_dir:
        w(f"* 市场状态（统一口径）：{regime_action}（以择时动作数据为准；指令一句话称\"{regime_dir}\"，已按数据侧修正）")
    elif regime_action or regime_dir:
        w(f"* 市场状态（统一口径）：{regime_action or regime_dir}（当日唯一状态口径，下文各部分均以此为准）")
    w()

    # ── V4.1 五级分桶：L2 确认主线 / L1 准主线试探档 / R 修复跟踪档 / L0 启动候选 / NONE 杂毛回避 ──
    l2, l1, r_rep, l0, junk = [], [], [], [], []
    for r in results:
        tier = str(r.get('gate_tier', 'NONE') or 'NONE')
        if tier == 'L2':
            l2.append(r)
        elif tier == 'L1':
            l1.append(r)
        elif tier == 'R':
            r_rep.append(r)
        elif tier == 'L0':
            l0.append(r)
        else:
            junk.append(r)

    l2.sort(key=lambda x: x.get('final_trade_score', 0), reverse=True)
    l1.sort(key=lambda x: x.get('final_trade_score', 0), reverse=True)
    # R 档排序：分歧转一致（右侧已确认）> 一致加速 > 高位分歧；同状态按综合分
    _R_ORDER = {'分歧转一致': 0, '一致加速': 1, '高位分歧': 2}
    r_rep.sort(key=lambda x: (_R_ORDER.get(str((x.get('capital_state') or {}).get('state', '')), 9),
                              -float(x.get('composite_score', 0) or 0)))
    l0.sort(key=lambda x: x.get('final_trade_score', 0), reverse=True)
    junk.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
    mainlines = l2  # 决策表/穿透沿用"mainlines"名（L2 确认主线）
    rotations = l1 + r_rep + l0  # 决策表轮动区 = 试探档 + 修复跟踪档 + 观察档

    # ── V4.1 分级仓位归一化（L2 满配 / L1 试探≤1/3池 / R·L0 观察0% / junk 回避）──
    _apply_gate_v41_alloc(l2, l1, r_rep, l0, junk, ma)

    # ── V1.1 PositionMultiplier 应用：FinalPosition = StrategyPosition × PositionMultiplier ──
    # 主题层只降不升（乘数≤1.0）；不覆盖 Trade Execution 的 WAIT/AVOID 终判（只会乘得更低或为0）。
    # HOLD_ONLY（高潮/退潮）→ 新仓位 0%、仅持仓管理；BLOCK → 清仓回避（StockOverride 不可突破 BLOCK）。
    for r in (l2 + l1):
        g = str(r.get('theme_gate', ''))
        act = str(r.get('trade_action', ''))
        mult = float(r.get('position_multiplier', 1.0) or 1.0)
        if g in ('HOLD_ONLY', 'BLOCK'):
            r['position_pct'] = 0.0
            r['allocated_position'] = 0.0
            r['leader_target_pos'] = 0.0
            r['core_target_pos'] = 0.0
            if g == 'BLOCK':
                r['trade_action'] = '清仓回避（ThemeGate=BLOCK）'
            elif not ('减仓' in act or '清仓' in act or '回避' in act):
                r['trade_action'] = '仅持仓管理·禁新增（ThemeGate=HOLD_ONLY）'
        elif mult < 1.0 and r.get('position_pct', 0) > 0:
            r['position_pct'] = round(r['position_pct'] * mult, 1)
            r['allocated_position'] = round(float(r.get('allocated_position', 0) or 0) * mult, 4)
            r['leader_target_pos'] = round(float(r.get('leader_target_pos', 0) or 0) * mult, 4)
            r['core_target_pos'] = round(float(r.get('core_target_pos', 0) or 0) * mult, 4)
            tag = 'ER20_ONLY·仅ER20' if g == 'ER20_ONLY' else 'SELECTIVE·仅强个股'
            if r.get('stock_override'):
                tag += '·个股突破'
            r['trade_action'] = f"{act}｜Gate:{tag}" if act else f"Gate:{tag}"

    # ── 终判口径统一（报告决策表输出层与 SQLite 落库共用）──
    # 杂毛/回避区在报告决策表固定展示"0% / 清仓回避"（见下方渲染），此处同步回写 r 字段，
    # 使紧随本函数之后的 save_to_sqlite_v2 落库值 = 报告展示值，杜绝 DB 残留 V3 裸建议歧义。
    for r in junk:
        r['trade_action'] = '清仓回避'
        r['position_pct'] = 0.0
        r['position_label'] = '0%'
        r['suggested_position'] = '0%'

    # ── 1. 第一部分：L2 核心主线阵营（满配）──
    w("━" * 60)
    w("### 第一部分：核心主线阵营（L2 确认主线 · V4.1 满配，合计≤大盘目标仓位）")
    w("━" * 60)
    if not l2:
        w("* 今日无 L2 确认主线（需同时满足：近5日动量为正 + 强度确认(综合/趋势双68 或 趋势75+综合65+宽度75)")
        w("  + 持续性≥2日(近5日窗口强态计数) + 宽度≥60% + 主力强度≥50 + 容量≥8亿 + 非过热 + 非情绪透支），")
        w("  空仓或等待确认。注意：情绪透支日（广度≥95% 或 涨停≥15家）一律不在当日确认，")
        w("  转第二部分 R 档跟踪，等次日不破再介入——不在情绪顶点满配。")
        # 距 L2 最近候选：把"主线为什么不出现"变成可操作信息（差几项 / 卡在哪一条），
        # 而不是只给一句"今日无 L2"——避免把"接近确认"误读为"没有机会"。
        _near = []
        for r in results:
            if str(r.get('gate_tier')) == 'L2':
                continue
            if str((r.get('capital_state') or {}).get('state', '')) == '资金撤离':
                continue
            _g2 = next((g for g in _gate_gap_v41(r) if g.startswith('距L2: ')), None)
            if not _g2:
                continue
            _near.append((_g2.count('、') + 1, -float(r.get('composite_score', 0) or 0), r, _g2))
        if _near:
            _near.sort(key=lambda x: (x[0], x[1]))
            w()
            w("  ── 距 L2 最近候选（按未满足项数升序，最多 3 个）──")
            for _n, _negc, r, _g2 in _near[:3]:
                _lc = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
                w(f"  · {r['theme']} [{_lc}] 综合{r.get('composite_score', 0):.0f} "
                  f"趋势{r.get('trend_score', 0):.0f} 持续{str(r.get('days_strong', 0))}日 "
                  f"→ 还差 {_n} 项")
                w(f"      {_g2}")
            w("  注：以上为「差一点就能确认」的主题，按第二/三部分对应档位处置（不因接近 L2 而提前满配）。")
        w()
    for r in l2:
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
          f"情绪{r.get('sentiment_score', 0):.0f} 涨停{zt} 迁移{mig:.1f} | 持续{str(r.get('days_strong', 0))}日")
        w(f"    先验胜率 {wr}% · 先验盈亏比 {rr}:1（规则估值，未回测校准） | 建议仓位 {pos_str}")
        w(f"    实盘买点：{action}")
    # 主线细分穿透：最佳子主题 / 龙头 / 中军
    if l2:
        w()
        w("─" * 60)
        w("### 主线细分穿透（最佳子主题 / 龙头 / 中军）")
        w("─" * 60)
        for r in l2:
            pen_lines = _mainline_penetration_rows(r)
            if pen_lines:
                for pl in pen_lines:
                    w(pl)
                w()
        w()

    # ── 2. 第二部分：L1 准主线试探档 + R 修复跟踪档 + L0 启动候选观察档 ──
    w("━" * 60)
    w("### 第二部分：潜在轮动与接力机会（L1 试探≤1/3池 / R·L0 观察0% · Rotation）")
    w("━" * 60)
    if not rotations:
        w("* 今日无潜在轮动机会")
        w()
    if l1:
        w("── 准主线试探档（L1 · 累计≤大盘目标仓位×1/3，弱转强第2日，试探介入）──")
        for r in l1:
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
            _gaps = _gate_gap_v41(r)
            if _gaps:
                w(f"    升档差距：{'；'.join(_gaps)}")
            w(f"    交易动作：{action}")
        w()
    if r_rep:
        w("── 修复跟踪档（R · 0%观察不配仓，等「分歧转一致」确认）──")
        w("   说明：本档不是「资金撤离」（真退潮，应清仓），而是资金未撤、结构未破的高位分歧/前日强势")
        w("         品种，只登记不介入；等下列确认条件成立才升 L1 试探，避免在分歧洗盘中被反复收割。")
        w("         入档三类：① 资金状态异常（高位分歧/分歧转一致/一致加速）；② 当日或前日「主升」")
        w("         但未达试探线；③ 强度未散（涨停≥2 或 趋势≥50）。前提：非「资金撤离」。")
        for r in r_rep:
            theme = r['theme']
            lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
            sd = r.get('sentiment_detail', {}) or {}
            zt = sd.get('zt_count', 0)
            mig = r.get('migration_score', 0)
            cs = r.get('capital_state') or {}
            try:
                _f = json.loads(r.get('gate_feat', '{}') or '{}')
            except Exception:
                _f = {}
            _lc, _plc = str(_f.get('lc', '') or ''), str(_f.get('prev_lc', '') or '')
            _st = str(cs.get('state', '') or '')
            if _st in ('高位分歧', '分歧转一致', '一致加速'):
                _why = f"资金未撤（{_st}），结构未破"
            elif _lc == '主升' or _plc == '主升':
                _why = f"前日/当日主升（{_plc or '—'}→{_lc or '—'}）未达试探线，洗盘观察"
            else:
                _why = "强度未散（涨停≥2 或 趋势≥50）"
            w(f"▸ {theme} [{lc_disp}] {r.get('mainline_type', '')} 质量{r.get('mainline_quality', 0):.0f} | "
              f"趋势{r.get('trend_score', 0):.0f} 综合{r.get('composite_score', 0):.0f} 涨停{zt} 迁移{mig:.1f}")
            w(f"    入档原因：{_why}")
            w(f"    资金状态：{cs.get('state', '—')}（{cs.get('evidence', '')}）")
            w(f"    转一致触发：{_repair_trigger(r)} | 建议仓位 {r.get('position_label', '0%')}")
            _gaps = _gate_gap_v41(r)
            if _gaps:
                w(f"    升档差距：{'；'.join(_gaps)}")
            w("    失效边界：主力资金转负 或 涨停归零 或 上涨占比<45% → 移出观察池（按退潮/资金撤离处理）")
            w(f"    交易动作：{r.get('trade_action', '')}")
        w()
    if l0:
        w("── 启动候选观察档（L0 · 仅登记跟踪，0仓等待转 L1）──")
        for r in l0:
            theme = r['theme']
            lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
            sd = r.get('sentiment_detail', {}) or {}
            zt = sd.get('zt_count', 0)
            mig = r.get('migration_score', 0)
            prob = _est_mainline_prob(r)
            confirm = _mainline_confirm(r)
            action = r.get('trade_action', '')
            pos_str = r.get('position_label', '0%') or '0%'
            w(f"▸ {theme} [{lc_disp}] {r.get('mainline_type', '')} 质量{r.get('mainline_quality', 0):.0f} | "
              f"趋势{r.get('trend_score', 0):.0f} 综合{r.get('composite_score', 0):.0f} "
              f"涨停{zt} 迁移{mig:.1f}")
            w(f"    成为主线概率 {prob}% | 确认条件：{confirm} | 建议仓位 {pos_str}")
            _gaps = _gate_gap_v41(r)
            if _gaps:
                w(f"    升档差距：{'；'.join(_gaps)}")
            w(f"    交易动作：{action}")
        w()

    # ── 3. 第三部分：杂毛/退潮与风险回避区 ──
    w("━" * 60)
    w("### 第三部分：杂毛/退潮与风险回避区（建议仓位 0%）")
    w("━" * 60)
    if not junk:
        w("* 今日无退潮/低分回避主题")
        w()
    else:
        lc_dist = {}
        for r in results:
            d = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', '')) or '未知'
            lc_dist[d] = lc_dist.get(d, 0) + 1
        dist_str = "、".join(f"{k}{v}" for k, v in sorted(lc_dist.items(), key=lambda x: -x[1]))
        w(f"* 生命周期分布（全{len(results)}主题）：{dist_str}")
        w(f"* 共 {len(junk)} 只未达主线交易标准，回避原因：强度不足 / 轮动过快 / 无持续性 / 过热")
        w("  注：判定为「高位分歧/一致加速」（资金未撤离）的主题已上移至第二部分 R 档跟踪，")
        w("      留在本区的以「资金撤离」型为主——真退潮，不加仓不抄底。")
        w("重点回避：")
        for r in junk[:3]:
            theme = r['theme']
            lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
            comp = r.get('composite_score', 0)
            _st = str((r.get('capital_state') or {}).get('state', '') or '')
            _tag = f"｜{_st}" if _st and _st != '常态' else ''
            w(f"  ✕ {theme} [{lc_disp}] 综合{comp:.0f}{_tag} → 【坚决回避/清仓】")
        if len(junk) > 3:
            rest = "、".join(r['theme'] for r in junk[3:8])
            w(f"  … 其余 {len(junk) - 3} 只同类回避（{rest}…）")
        w()
        w("再评估触发（出现下列信号时重新纳入 L0 观察，而非永久拉黑）：")
        for r in junk[:5]:
            theme = r['theme']
            lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
            w(f"  ↻ {theme} [{lc_disp}] → {_junk_reeval_cond(r)}")
        w()

    # ── 3.5 重点主题深度分析（高潮=风险处置 / 启动=机会跟踪）──
    focus_climax = [r for r in results if str(r.get('lifecycle', '') or '') == '高潮'
                    or is_hot_climax_phase(r.get('hot_phase'))]
    focus_start = [r for r in results if str(r.get('lifecycle', '') or '') == '启动']
    focus_climax.sort(key=lambda x: x.get('hot_score', 0) or 0, reverse=True)
    focus_start.sort(key=lambda x: x.get('final_trade_score', 0) or 0, reverse=True)

    def _top5_line(r):
        parts = []
        for s in (r.get('top5_stocks') or [])[:5]:
            lb = int(s.get('lb_height', 0) or 0)
            flag = f"{lb}板" if lb >= 2 else ("涨停" if s.get('zt_flag') else "")
            t = str(s.get('zt_time', '') or '')
            tag = s.get('name', '—') or '—'
            if flag:
                tag += f"({flag}{'·' + t if t else ''})"
            parts.append(tag)
        return "、".join(parts) if parts else "—"

    def _overheat_evidence(r):
        hot_pct = float(r.get('hot_percentile', 50) or 50)
        trend = float(r.get('trend_score', 0) or 0)
        sent = float(r.get('sentiment_score', 0) or 0)
        ev = []
        if hot_pct >= 85:
            ev.append(f"热度60日分位{hot_pct:.0f}≥85")
        if trend >= 70 and sent >= 85:
            ev.append(f"趋势{trend:.0f}×情绪{sent:.0f}双高（情绪透支形态）")
        if is_hot_climax_phase(r.get('hot_phase')):
            ev.append("热榜阶段判定=高潮")
        return "；".join(ev) or "生命周期标记=高潮（热度分位未达85，按情绪相位判定）"

    def _migration_cn(r):
        _MAP = {'upward': '资金流入·向上迁移', 'downward': '资金流出·向下迁移',
                'sideways': '横盘迁移·方向未明'}
        return _MAP.get(str(r.get('migration_direction', '') or ''), '—')

    if focus_climax or focus_start:
        w("━" * 60)
        w("### 重点主题深度分析（高潮=风险处置 / 启动=机会跟踪）")
        w("━" * 60)
        w(f"* 本节仅穿透两类极值主题：高潮{len(focus_climax)}只（只讲怎么撤）· 启动{len(focus_start)}只（只讲怎么验）；其余主题见前三部分与决策表")
        w()
        for r in focus_climax:
            gate = str(r.get('theme_gate', ''))
            sd = r.get('sentiment_detail', {}) or {}
            zt = int(sd.get('zt_count', 0) or 0)
            up = float(sd.get('up_ratio', 0) or 0)
            mig = float(r.get('migration_score', 0) or 0)
            lc_disp = LC_DISPLAY.get(r.get('lifecycle', ''), r.get('lifecycle', ''))
            w(f"【风险处置·高潮】{r['theme']}  [{lc_disp}] ThemeGate={gate}（乘数{float(r.get('position_multiplier', 0) or 0):.0%}）")
            w("─" * 60)
            w(f"  热度画像：hot {float(r.get('hot_score', 0) or 0):.1f}（60日分位{float(r.get('hot_percentile', 50) or 50):.0f}%·{r.get('hot_phase', '')}）"
              f" 情绪{float(r.get('sentiment_score', 0) or 0):.0f} 趋势{float(r.get('trend_score', 0) or 0):.0f} 综合{float(r.get('composite_score', 0) or 0):.0f} "
              f"| 涨停{zt}家 上涨占比{up:.0f}%")
            w(f"  梯队结构：龙头 {r.get('leader_name', '') or '—'} / 中军 {r.get('core_name', '') or '—'} | 前5强：{_top5_line(r)}")
            w(f"  资金迁移：{mig:.1f}（{_migration_cn(r)}）| 梯队最大成交额 {float(r.get('gate_cap_amt', 0) or 0):.1f}亿 "
              f"| 资金分{float(r.get('fund_score', 0) or 0):.0f} MTI{float(r.get('mti', 0) or 0):.0f}")
            w(f"  高潮依据：{_overheat_evidence(r)}")
            w(f"  后续路径：乐观=高潮→{LC_NEXT_UP.get('高潮', '分歧')}（首日强承接则看二波） / 悲观=高潮→{LC_NEXT_DOWN.get('高潮', '退潮')}")
            w(f"  再评估触发：{_junk_reeval_cond(r)}")
            if gate == 'BLOCK':
                w("  持仓处置：BLOCK——存量全部清仓，反弹即兑现，不等分歧确认")
            else:
                w("  持仓处置：HOLD_ONLY——0%新仓；龙头可博弈惯性冲高（断板/大面即走），跟风股逢冲高优先兑现")
            w("  明日观察：①涨停数（<5家=承接不足，按退潮预期处理）②龙头封板/断板 ③炸板与亏钱效应是否扩散")
            w(f"  门禁理由：{r.get('gate_reason', '')}")
            w()
        for r in focus_start:
            tier = str(r.get('gate_tier', 'NONE') or 'NONE')
            gate = str(r.get('theme_gate', ''))
            sd = r.get('sentiment_detail', {}) or {}
            zt = int(sd.get('zt_count', 0) or 0)
            up = float(sd.get('up_ratio', 0) or 0)
            mig = float(r.get('migration_score', 0) or 0)
            cap_amt = float(r.get('gate_cap_amt', 0) or 0)
            if gate == 'ER20_ONLY':
                ovs = r.get('override_stocks') or []
                if ovs:
                    ovs_str = "、".join(
                        f"{o.get('name', '—')}({str(o.get('code', ''))[:6]}·SIA{float(o.get('sia', 0) or 0):.0f}"
                        f"·爆发{float(o.get('explosion', 0) or 0):.0f}{'·PRIORITY' if o.get('premium') else ''})"
                        for o in ovs[:3])
                else:
                    _leads = r.get('sia_leading_stocks') or []
                    if _leads:
                        ovs_str = ("、".join(f"{o.get('name', '—')}({o.get('code', '')}·SIA{float(o.get('sia', 0) or 0):.0f})"
                                             for o in _leads)
                                   + "（仅SIA≥85领先，未达爆发≥80+突破，个股需自行确认）")
                    else:
                        ovs_str = "无（暂无 SIA≥85 个股领先，ER20 权限按板块结构给到）"
            else:
                ovs_str = "—（Gate≠ER20_ONLY，个股无需突破即可参与）"
            w(f"【机会跟踪·启动】{r['theme']}  [启动] 档位={tier} ThemeGate={gate}（乘数{float(r.get('position_multiplier', 0) or 0):.0%}）")
            w("─" * 60)
            w(f"  启动画像：情绪{float(r.get('sentiment_score', 0) or 0):.0f} 趋势{float(r.get('trend_score', 0) or 0):.0f} 综合{float(r.get('composite_score', 0) or 0):.0f} "
              f"| 涨停{zt}家 上涨占比{up:.0f}% 迁移{mig:.1f}（{_migration_cn(r)}）")
            w(f"  梯队结构：龙头 {r.get('leader_name', '') or '—'} / 中军 {r.get('core_name', '') or '—'} | 前5强：{_top5_line(r)}")
            w(f"  容量验证：梯队最大成交额 {cap_amt:.1f}亿（{'≥8亿·可容纳大资金' if cap_amt >= 8 else '<8亿·容纳不足，升L1需放量'}）")
            w(f"  个股领先：{ovs_str}")
            _gaps = _gate_gap_v41(r)
            w(f"  档位判定：{tier}（0仓观察，不抢跑）" + (f"；升档差距：{'；'.join(_gaps)}" if _gaps else ""))
            w(f"  晋级路径：启动→{LC_NEXT_UP.get('启动', '升温')}→主升；确认条件：{_mainline_confirm(r)}")
            w(f"  晋级触发：{_junk_reeval_cond(r)}")
            w("  失效边界：迁移回落至0 / 涨停归零 / 高位放量滞涨 → 移出观察池")
            w(f"  门禁理由：{r.get('gate_reason', '')}")
            w()
        w()

    # ── 4. 主线与轮动交易决策表（全量） ──
    w("━" * 60)
    w("### 主线与轮动交易决策表（全量）")
    w("━" * 60)
    w()
    w("─" * 120)
    w("说明：跟踪序=关注优先级（非买入优先级）；ThemeGate 决定策略权限与仓位上限——OPEN 100% / SELECTIVE 70% / ER20_ONLY 50% / HOLD_ONLY·BLOCK 0%")
    w("      主题弱≠全部禁做：ER20_ONLY 档中 SIA≥85+爆发分≥80+突破 的极强个股可 StockOverride 有限参与（仓位≤标准×50%）；BLOCK 不可被个股突破")
    w("      先验胜率/转化概率为规则估值（未回测校准），仅用于 ThemeGate 升降级与同档排序，≠个股上涨概率；T120 需 Gate=OPEN+弹性≥75+持续≥60+MOM≥60")
    w("─" * 120)
    w(f"{'跟踪序':<5}{'主题':<9}{'质量':<10}{'ThemeState':<12}{'胜率%':<6}{'转化%':<6}{'弹性':<5}{'持续':<5}{'ThemeGate':<11}{'仓位上限':<9}{'ER20':<5}{'T20':<4}{'T120':<5}{'交易动作':<28}{'StockOverride'}")
    w("─" * 120)

    def _v11_row(r, order, action=None, wr=None, pb=None):
        q = str(r.get('theme_quality_v11', ''))
        q_str = f"{q} {r.get('mainline_quality', 0):.0f}"
        gate = str(r.get('theme_gate', ''))
        er20 = '✓' if gate in ('OPEN', 'SELECTIVE', 'ER20_ONLY') else '—'
        t20 = '✓' if gate in ('OPEN', 'SELECTIVE') else '—'
        t120 = '✓' if r.get('t120_ok') else '—'
        cap = f"{float(r.get('position_multiplier', 1.0) or 0):.0%}"
        ov = 'TRUE' if r.get('stock_override') else ''
        wr_s = '—' if wr is None else f"{wr}%"
        pb_s = '—' if pb is None else f"{pb}%"
        act = action if action is not None else str(r.get('trade_action', ''))
        w(f"{order:<5}{r['theme']:<9}{q_str:<10}{r.get('theme_state_v11', ''):<12}{wr_s:<6}{pb_s:<6}"
          f"{r.get('ige_effective', 0):<5.0f}{r.get('ige_persistence', 0):<5.0f}{gate:<11}{cap:<9}"
          f"{er20:<5}{t20:<4}{t120:<5}{act}{ov}")

    order = 0
    for r in mainlines:
        order += 1
        _v11_row(r, order, wr=_est_winrate(r), pb=_est_mainline_prob(r))
    for r in rotations[:20]:
        order += 1
        _v11_row(r, order, wr=_est_winrate(r), pb=_est_mainline_prob(r))
    for r in junk[:20]:
        order += 1
        # 回避区不推荐交易：胜率/转化留空；BLOCK 不可被 StockOverride 突破
        _v11_row(r, order, action='清仓回避', wr=None, pb=None)
    w()

    # ── 2.5. 前兆主题与子主题（爆发前观察池）──
    # 数据源为 detect_theme_precursors 写入的 r['precursor']，此处不改配仓，纯观察池展示
    prec_list = sorted([r['precursor'] for r in results if r.get('precursor')],
                       key=lambda x: (x['n_hits'], x['n_resonance'], x['precursor_score']),
                       reverse=True)
    for line in _precursor_report_lines(prec_list):
        w(line)

    # ── 3. 机构配置策略建议 ──
    w("━" * 60)
    w("### 机构配置策略建议")
    w("━" * 60)
    w()

    # 整体仓位建议（优先引用 market_analysis 的大盘目标仓位，未读到才用主题生命周期兜底）
    ma_pos = ma.get('target_pos')
    # 兜底指标（风险提示也引用）：主题生命周期分布
    pos_cnt = sum(1 for r in results if r.get('lifecycle') in ('启动', '升温', '主升', '分歧'))
    neg_cnt = sum(1 for r in results if r.get('lifecycle') in ('高潮', '退潮'))
    total = len(results) or 1
    pos_ratio = pos_cnt / total
    neg_ratio = neg_cnt / total
    if ma_pos is not None:
        position = float(ma_pos)
        pos_lv = "高仓位" if position >= 70 else "中等仓位" if position >= 45 else "低仓位"
        w(f"* 整体仓位建议：{position:.0f}%（{pos_lv}；引用大盘仓位引擎 market_analysis）")
    else:
        base = 50.0 + (pos_ratio - neg_ratio) * 60.0
        # 大盘环境修正（沪深300 近10日收益，最多 ±15%）
        mkt_adj = max(-15.0, min(15.0, market_ret_10))
        position = max(0.0, min(100.0, base + mkt_adj))
        pos_lv = "高仓位" if position >= 70 else "中等仓位" if position >= 45 else "低仓位"
        w(f"* 整体仓位建议：{position:.0f}%（{pos_lv}；兜底：启动/升温类{pos_cnt}只 vs 高潮/退潮类{neg_cnt}只，"
          f"沪深300近10日{market_ret_10:+.1f}%，未读取到大盘仓位引擎）")

    # 档位分布与无主线日纪律
    w(f"* 今日档位分布：L2确认 {len(l2)} / L1试探 {len(l1)} / L0观察 {len(l0)} / 回避 {len(junk)}")
    if not l2 and not l1:
        if l0:
            w(f"* 无主线日纪律：空仓等待，仅跟踪 L0 观察档（首选 {l0[0]['theme']}）的升档触发，不抢跑")
        else:
            w("* 无主线日纪律：全档位空仓，等待次日门禁重新评估")

    # 核心风险提示
    risks = []
    if market_ret_10 < -2:
        risks.append(f"大盘环境偏弱（沪深300近10日{market_ret_10:+.1f}%），注意控制仓位")
    elif market_ret_10 > 3:
        risks.append(f"大盘强势（沪深300近10日{market_ret_10:+.1f}%），但需防范高位题材退潮")
    if neg_ratio > 0.3:
        risks.append(f"高潮/退潮主题 {neg_cnt}/{total}（{neg_ratio:.0%}）占比偏高，注意高位股退潮与补跌风险")
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

    # 6b. 前兆探测用量比：当日量 / 前5日均量
    #     与 backtest_theme_precursor.py 的 P6 个股放量共振同口径（严格档阈值 2.0），
    #     基准更短故比 vol_ratio_today 更敏感，实盘与回测必须用同一字段避免口径分叉
    vol_base_5 = vol[max(0, last - 5) : last].mean() if last >= 5 else vol_base
    vol_ratio_5d = vol[last] / vol_base_5 if vol_base_5 > 0 else 1.0

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
        "vol_ratio_5d": vol_ratio_5d,     # 前兆 P6 用：当日量/前5日均量
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


# ═══════════════════════════════════════════════════════════
# V2.1 主题状态识别 + 持续性确认 + 交易许可引擎（附加层）
#
# 核心原则：强度用于发现 / 状态用于判断阶段 / 确认用于判断是否真形成趋势 /
#           持续性用于验证是否一天行情 / 交易许可用于执行 / ChaseRisk 决定怎么进
# 禁止映射：高综合分≠主线；高迁移≠强趋势；高情绪≠可买入
#
# 定位：只读 V2 的 results，产出独立文件与独立表，不改动 V2 的 CSV/txt/SQLite 表，
#       供 A/B Test 与下游选股/执行层调用（本层不参与配仓）。
# ═══════════════════════════════════════════════════════════

V21_STATES = ('WEAK', 'RECOVERY', 'EARLY_FLOW', 'STARTING', 'STRONG_TREND',
              'ACCELERATION', 'OSCILLATION', 'DIVERGENCE', 'EXHAUSTION', 'RETREAT')
# 「改善阶梯序」：仅用于判定状态转换方向（UPGRADE/DOWNGRADE），不是健康度排序
V21_STATE_LADDER = ('RETREAT', 'WEAK', 'EXHAUSTION', 'DIVERGENCE', 'RECOVERY',
                    'OSCILLATION', 'EARLY_FLOW', 'STARTING', 'STRONG_TREND', 'ACCELERATION')
V21_STATE_CN = {
    'WEAK': '弱势', 'RECOVERY': '修复', 'EARLY_FLOW': '资金先行', 'STARTING': '启动确认',
    'STRONG_TREND': '强趋势', 'ACCELERATION': '加速', 'OSCILLATION': '震荡未确认',
    'DIVERGENCE': '资金价格背离', 'EXHAUSTION': '情绪透支', 'RETREAT': '退潮',
}
V21_PERMISSIONS = ('NO_TRADE', 'WATCH', 'CONDITIONAL', 'TRADEABLE')
V21_FLOW_CAP = 40.0                                   # migration_score 实测值域≈[0,40]

# ── V2.1 分层：已移除四维 AND 门槛（2026-09 架构纠偏）──
# 实证依据（11 轮扫描，样本 20250508~20260922 / 339 交易日 / 32 主题）：
#   · 四维（confirmation/breadth/leadership/persistence）在本窗口是**反向**因子：
#     十分档 D9 在 7 个维度中 6 个最差；旧 TRADEABLE 再剥主题固定效应后 T+5 −2.33%（p=0.0168★）。
#   · 旧门槛把「候选 / 确认 / 核心主线」压在同一套四维 AND 里，而四维（顺势拥挤）与低吸条件
#     方向相反，AND 求交集趋近空集 —— 62 日仅 1 例 TRADEABLE。这是样本归零的结构性原因，
#     调阈值无法解决（调松则反向 alpha 渗入，调紧则归零）。
#   · 状态机同病：STRONG_TREND 62 日仅 4 条、ACCELERATION 仅 6 条，故主线层不得依赖它们。
#   · 唯一跨 regime 方向一致的维度是**拥挤度回避**：当日横截面（距20日前高位置 + 量能 5/20日）
#     前 20% 的主题未来跑输，5 个 regime 段中 4 段为负、3 段显著，无任何段显著为正。
# 结论：分层改为「当日横截面分位」定档（跨 regime 自可比、样本量天然有保证），
#       四维退出许可逻辑（仅保留为描述性字段），拥挤度作为唯一风险闸。
V21_MAINLINE_TOP = 0.20      # 核心主线：强度与广度当日横截面双前 20%
V21_COND_TOP = 0.50          # 条件确认：强度当日横截面前 50%
V21_CROWD_TOP = 0.20         # 拥挤闸：拥挤度当日横截面前 20% → 不得进入确认层与主线层
V21_BLOCK_STATES = ('RETREAT', 'EXHAUSTION')          # 硬阻断：不参与交易
V21_WATCH_ONLY_STATES = ('DIVERGENCE',)               # 仅观察
V21_CHASE_LIMIT = 75.0                                # ≥75 → 只可回踩买（只改 buy_mode，不再降许可层级）
V21_EARLY_BREADTH_DELTA = 3.0                         # EARLY_FLOW「广度实质改善」门槛（0~100 分制下的可观测增量）
V21_HIST_DAYS = 25                                    # 历史窗口（覆盖 D20 + 状态转换 D5）
V21_TABLE = 'theme_v21_daily'
V21_BACKFILL_MODE = False                             # True：只产 V2.1 历史，不写任何 V2 产物


def _v21_lin(x, lo, hi):
    """x 从 lo→hi 线性映射到 0→1（超出裁剪），V2.1 内部唯一归一化工具"""
    x = float(x or 0)
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _v21_clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(x or 0)))


def _v21_flow(mig):
    """Migration → Flow 0~100。migration_score = confidence*100（实测∈[0,40]）线性拉满。"""
    return round(_v21_clamp(float(mig or 0) / V21_FLOW_CAP * 100.0), 1)


def _v21_theme_ret1(r):
    """主题当日收益（%）= 成分股等权 pct_chg 均值 —— 回测/持续性/跑赢市场的唯一收益口径"""
    rows = r.get('stock_rows') or []
    if not rows:
        return 0.0
    return round(float(np.mean([float(x.get('pct_chg', 0) or 0) for x in rows])), 4)


def calc_breadth_v21(rows, market_ret_1=0.0):
    """Breadth 主题内部广度 0~100
    上涨20% + 强势(≥5%)15% + 跑赢市场15% + MA20上方15% + MA60上方15% + 20日新高10% + 涨停10%
    关键：不再只数涨停数 —— 「10只涨停+100只下跌」与「5只涨停+80%上涨」必须区分开。
    """
    n = len(rows)
    if n == 0:
        return 0.0, {}
    def ratio(f):
        return sum(1 for x in rows if f(x)) / n
    up = ratio(lambda x: float(x.get('pct_chg', 0) or 0) > 0)
    strong = ratio(lambda x: float(x.get('pct_chg', 0) or 0) >= 5.0)
    rs = ratio(lambda x: float(x.get('pct_chg', 0) or 0) > float(market_ret_1 or 0))
    ma20 = ratio(lambda x: int(x.get('above_ma20_flag', 0) or 0) == 1)
    ma60 = ratio(lambda x: float(x.get('ma60_b', 0) or 0) > 0)
    nh = ratio(lambda x: int(x.get('new_high_flag', 0) or 0) == 1)
    zt = ratio(lambda x: int(x.get('zt_flag', 0) or 0) == 1)
    score = 100.0 * (0.20 * up + 0.15 * strong + 0.15 * rs + 0.15 * ma20 + 0.15 * ma60
                     + 0.10 * nh + 0.10 * zt)
    return round(_v21_clamp(score), 1), {
        'b_up': round(up * 100, 1), 'b_strong': round(strong * 100, 1),
        'b_rs': round(rs * 100, 1), 'b_ma20': round(ma20 * 100, 1),
        'b_ma60': round(ma60 * 100, 1), 'b_newhigh': round(nh * 100, 1),
        'b_zt': round(zt * 100, 1),
    }


def _v21_stock_strength(s):
    """个股强度（与 V3 龙头评分同式，保证「龙头」定义与既有体系一致）"""
    lb = float(s.get('lb_height', 0) or 0)
    pct = abs(float(s.get('pct_chg', 0) or 0))
    amt = float(s.get('amount_latest', 0) or 0)
    p = float(s.get('purity', 0) or 0)
    return 0.4 * min(lb * 20, 100) + 0.3 * min(pct * 5, 100) + 0.2 * min(amt * 2, 100) + 0.1 * min(p * 20, 100)


def calc_leadership_v21(rows, theme_avg_ret5=0.0):
    """Leadership 龙头质量 0~100 + SingleLeaderRisk(HIGH/MEDIUM/LOW)
    LeaderCount梯队20 + LeaderStrength25 + LeaderTrend20 + LeaderBreadth20 + LeaderConsistency15

    SingleLeaderRisk：主题是否靠单一龙头撑起来。HIGH 时禁止据此认定主线（见 _v21_mainline_candidate）。
    """
    n = len(rows)
    if n == 0:
        return 0.0, {}, 'HIGH'
    ranked = sorted(rows, key=_v21_stock_strength, reverse=True)
    leader = ranked[0]
    # 龙头梯队：优先连板 → 涨停 → 强度前3
    pool = [x for x in rows if float(x.get('lb_height', 0) or 0) >= 2]
    if not pool:
        pool = [x for x in rows if int(x.get('zt_flag', 0) or 0) == 1]
    if not pool:
        pool = ranked[:3]
    leader_count = len(pool)
    # LeaderStrength：龙头强度 + 连板高度
    ls = _v21_stock_strength(leader)
    s_strength = 100.0 * (0.7 * min(ls / 80.0, 1.0) + 0.3 * min(float(leader.get('lb_height', 0) or 0) / 3.0, 1.0))
    # LeaderTrend：龙头是否站稳关键均线 / 突破
    s_trend = (40.0 if float(leader.get('ma5_b', 0) or 0) > 0 else 0.0) \
        + (30.0 if float(leader.get('ma20_b', 0) or 0) > 0 else 0.0) \
        + (30.0 if int(leader.get('new_high_flag', 0) or 0) == 1 else 0.0)
    # LeaderBreadth：强势前列的跟随度（强度前10中上涨比例）
    top10 = ranked[:min(10, n)]
    s_breadth = 100.0 * sum(1 for x in top10 if float(x.get('pct_chg', 0) or 0) > 0) / max(1, len(top10))
    # LeaderConsistency：龙头持续强于主题平均 / 连板延续 / 成交额确认
    s_consist = (50.0 if float(leader.get('ret_5', 0) or 0) > float(theme_avg_ret5 or 0) else 0.0) \
        + (30.0 if float(leader.get('lb_height', 0) or 0) >= 2 else 0.0) \
        + (20.0 if float(leader.get('amount_latest', 0) or 0) >= 8.0 else 0.0)
    s_count = 100.0 * min(leader_count / 4.0, 1.0)
    score = 0.20 * s_count + 0.25 * s_strength + 0.20 * s_trend + 0.20 * s_breadth + 0.15 * s_consist
    # ── SingleLeaderRisk：单一龙头依赖度 ──
    amt_total = sum(float(x.get('amount_latest', 0) or 0) for x in rows) or 1e-9
    amt_share = float(leader.get('amount_latest', 0) or 0) / amt_total
    pcts = sorted(float(x.get('pct_chg', 0) or 0) for x in rows)
    median_pct = pcts[len(pcts) // 2] if pcts else 0.0
    up_ratio = 100.0 * sum(1 for x in rows if float(x.get('pct_chg', 0) or 0) > 0) / n
    zt_count = sum(1 for x in rows if int(x.get('zt_flag', 0) or 0) == 1)
    lead_pct = float(leader.get('pct_chg', 0) or 0)
    if (amt_share >= 0.50 and leader_count <= 1) \
            or (up_ratio < 45 and lead_pct - median_pct >= 9.0) \
            or (zt_count <= 1 and up_ratio < 45):
        risk = 'HIGH'
    elif amt_share >= 0.35 or (leader_count <= 2 and up_ratio < 55) or (up_ratio < 50 and lead_pct - median_pct >= 6.0):
        risk = 'MEDIUM'
    else:
        risk = 'LOW'
    detail = {
        'lead_name': leader.get('name', ''), 'lead_count': leader_count,
        'lead_lb': float(leader.get('lb_height', 0) or 0), 'lead_pct': round(lead_pct, 2),
        'lead_amt_share': round(amt_share, 3), 'lead_pool_ratio': round(leader_count / n * 100, 1),
        'lead_up_ratio': round(up_ratio, 1), 'lead_median_pct': round(median_pct, 2),
        's_lead_count': round(s_count, 1), 's_lead_strength': round(s_strength, 1),
        's_lead_trend': round(s_trend, 1), 's_lead_breadth': round(s_breadth, 1),
        's_lead_consist': round(s_consist, 1),
    }
    return round(_v21_clamp(score), 1), detail, risk


def calc_persistence_v21(series):
    """Persistence 持续性 0~100 —— 不是简单平均，必须看形态
    基础：D3 35% + D5 30% + D10 20% + D20 15%（各窗口为强度均值）
    修正：+ 持续强化(8) / + 连续跑赢市场(≤6) / - 脉冲型(48→75→51 判脉冲,15)
          - 断崖式下降(单日≤-20,12) / - 波动惩罚(≤10)
    窗口不足（历史天数<5）时按 n/5 收缩，避免首日虚高。

    series: [{'strength':.., 'ret_1':.., 'mkt_ret_1':..}, ...] 升序，含今日
    """
    s = [float(x.get('strength') or 0) for x in series]
    n = len(s)
    if n == 0:
        return 0.0, {'pers_days': 0, 'pers_d3': 0, 'pers_d5': 0, 'pers_d10': 0, 'pers_d20': 0,
                     'pers_shape': 'NO_DATA', 'pers_bonus': 0.0, 'pers_penalty': 0.0, 'pers_beat': 0}
    def wmean(k):
        seg = s[-k:]
        return sum(seg) / len(seg)
    d3, d5, d10, d20 = wmean(3), wmean(5), wmean(10), wmean(20)
    base = 0.35 * d3 + 0.30 * d5 + 0.20 * d10 + 0.15 * d20
    shape, bonus, penalty = 'FLAT', 0.0, 0.0
    beat = 0
    seg5 = s[-5:] if n >= 5 else s
    if n >= 3:
        diffs = [seg5[i + 1] - seg5[i] for i in range(len(seg5) - 1)]
        max_drop = min(diffs) if diffs else 0.0
        slope = (seg5[-1] - seg5[0]) / max(1, len(seg5) - 1)
        up_days = sum(1 for d in diffs if d > 0) / max(1, len(diffs))
        if up_days >= 0.6 and slope > 0 and max_drop > -10:
            shape, bonus = 'STRENGTHENING', bonus + 8.0
        if n >= 10:
            seg10 = s[-10:]
            i_max = max(range(len(seg10)), key=lambda i: seg10[i])
            if seg10[i_max] - s[-1] >= 15.0 and i_max <= len(seg10) - 3:
                shape, penalty = 'PULSE', penalty + 15.0
        if max_drop <= -20.0:
            shape, penalty = 'CLIFF', penalty + 12.0
        if n >= 2:
            penalty += min(10.0, float(np.std(seg5)) * 0.5)
    m = min(5, n)
    for i in range(1, m + 1):
        rr = series[-i].get('ret_1')
        mm = series[-i].get('mkt_ret_1')
        if rr is not None and mm is not None and float(rr) > float(mm):
            beat += 1
    bonus += min(6.0, beat * 1.5)
    score = base + bonus - penalty
    if n < 5:
        score *= max(0.5, n / 5.0)
    detail = {'pers_days': n, 'pers_d3': round(d3, 1), 'pers_d5': round(d5, 1),
              'pers_d10': round(d10, 1), 'pers_d20': round(d20, 1), 'pers_shape': shape,
              'pers_bonus': round(bonus, 1), 'pers_penalty': round(penalty, 1), 'pers_beat': beat}
    return round(_v21_clamp(score), 1), detail


def calc_chase_risk_v21(f):
    """ChaseRisk 追高风险 0~100
    Emotion25 + 当日涨幅20 + 距MA20乖离15 + 20日位置15 + 涨停集中度15 + 情绪-广度背离10
    ≥75 → 即使 TradePermission 允许，也只能 BUY_MODE=PULLBACK_ONLY（与「巨量日不追，只等回踩」一致）。

    各分项 lo/hi 按 A 股主题日度实测分位标定：原区间（emotion 55~90 / 涨幅 2~9 / MA20乖离 5~20 /
    20日位置 0.6~1.0 / 背离 15~40）全部设在理论极值上，实测 1920 样本最高仅 61.2，阈值 75 永不可达；
    标定后同一窗口最高 84.4，75 成为真正可触发的「巨量日」警戒线。
    """
    s_emo = _v21_lin(f.get('emotion'), 55, 85) * 100
    s_r1 = _v21_lin(f.get('ret_1'), 1.5, 6.0) * 100
    s_ma = _v21_lin(f.get('ma20_b'), 3, 12) * 100
    s_pos = _v21_lin(f.get('pos_in_20'), 0.5, 0.98) * 100
    up = float(f.get('up_ratio') or 0)
    # 涨停集中度 = 涨停家数 / 上涨家数（原式 zt_count×(1-up_ratio) 自我抵消恒≈0：涨停多必然上涨家数多）
    up_cnt = up / 100.0 * float(f.get('n_stocks') or 0)
    s_conc = _v21_lin((float(f.get('zt_count') or 0) / up_cnt) if up_cnt > 0 else 0.0,
                      0.08, 0.35) * 100
    s_div = _v21_lin(float(f.get('emotion') or 0) - up, 5, 30) * 100
    risk = (0.25 * s_emo + 0.20 * s_r1 + 0.15 * s_ma + 0.15 * s_pos
            + 0.15 * s_conc + 0.10 * s_div)
    return round(_v21_clamp(risk), 1), {
        'c_emotion': round(s_emo, 1), 'c_ret1': round(s_r1, 1), 'c_ma20': round(s_ma, 1),
        'c_pos': round(s_pos, 1), 'c_concentration': round(s_conc, 1), 'c_divergence': round(s_div, 1),
    }


def classify_state_v21(f):
    """V2.1 状态机（10 态）—— 自上而下先命中先返回，顺序即优先级

    1 RETREAT      趋势/情绪/广度/持续性四低 → 退潮
    2 EXHAUSTION   情绪高 + 广度或持续性走弱 + 趋势钝化 + 涨停潮 → 情绪透支（禁追高）
    3 WEAK         趋势<40 且 情绪<50 且 广度<45 且 确认<45
    4 ACCELERATION 趋势70+广度65+龙头65+确认65 且 (D3>D5 或 趋势加速>0) 且 (涨停扩张 或 广度扩张)
    5 STRONG_TREND 趋势65+广度60+龙头60+确认60+持续性55（禁止 趋势<50 判强趋势）
    6 EARLY_FLOW   低趋势(<50) + 高迁移(≥20) + 高情绪(≥60) + 广度【已达标或实质改善】→ 资金先行（机会）
    7 DIVERGENCE  同签名但广度弱(<45)且未实质改善 → 资金与价格背离（风险）
    8 OSCILLATION  综合≥60 但确认<60 —— 高强度低确认（解决"综合分高但没形成趋势"）
    9 STARTING     趋势45+广度50+情绪55+迁移10+确认50
   10 OSCILLATION  趋势高但持续性弱、广度不一致
   11 RECOVERY     弱势/退潮/透支后趋势与广度同步改善
   12 WEAK         兜底

    注：EARLY_FLOW 与 DIVERGENCE 是同一「高迁移+低趋势」签名的两个出口（规格 7.7 明示二者并列），
    区分依据是广度方向 —— 广度已在合理水平或实质改善（≥V21_EARLY_BREADTH_DELTA）= 资金先行；
    广度弱且未改善 = 背离风险。EARLY_FLOW 必须先于 OSCILLATION 判定，否则高情绪样本会被
    「综合≥60 但确认<60」整批吞掉，导致该状态不可达（实测 1920 样本仅 1 条）。
    """
    trend = float(f.get('trend') or 0)
    emo = float(f.get('emotion') or 0)
    comp = float(f.get('composite') or 0)
    brd = float(f.get('breadth') or 0)
    lead = float(f.get('leadership') or 0)
    pers = float(f.get('persistence') or 0)
    conf = float(f.get('confirmation') or 0)
    mig = float(f.get('migration') or 0)
    up = float(f.get('up_ratio') or 0)
    zt = int(f.get('zt_count') or 0)
    d_brd = float(f.get('d_breadth') or 0)
    d_trend = float(f.get('d_trend') or 0)
    d_pers = float(f.get('d_persistence') or 0)
    d3, d5 = float(f.get('pers_d3') or 0), float(f.get('pers_d5') or 0)
    prev_state = str(f.get('prev_state') or '')

    if trend < 45 and emo < 45 and brd < 40 and pers < 45:
        return 'RETREAT'
    if (emo >= 70 and (d_brd < 0 or up < 55) and (d_pers < 0 or pers < 50)
            and d_trend <= 0 and (zt >= 8 or comp >= 60)):
        return 'EXHAUSTION'
    if trend < 40 and emo < 50 and brd < 45 and conf < 45:
        return 'WEAK'
    if (trend >= 70 and brd >= 65 and lead >= 65 and conf >= 65
            and (d3 > d5 or d_trend > 0)
            and (bool(f.get('zt_expansion')) or bool(f.get('breadth_expansion')))):
        return 'ACCELERATION'
    if trend >= 65 and brd >= 60 and lead >= 60 and conf >= 60 and pers >= 55 and trend >= 50:
        return 'STRONG_TREND'
    # 资金先行（机会版）：情绪/资金已起 + 趋势未确认 + 广度已达标或实质改善
    if (trend < 50 and mig >= 20 and emo >= 60
            and (brd >= 45 or d_brd >= V21_EARLY_BREADTH_DELTA)):
        return 'EARLY_FLOW'
    # 资金价格背离（风险版）：同签名但广度弱且未实质改善
    if mig >= 25 and trend < 50 and emo >= 65 and brd < 45:
        return 'DIVERGENCE'
    if comp >= 60 and conf < 60:
        return 'OSCILLATION'
    if trend >= 45 and brd >= 50 and emo >= 55 and mig >= 10 and conf >= 50:
        return 'STARTING'
    if trend >= 55 and pers < 50 and abs(d_brd) <= 3.0 and conf < 60:
        return 'OSCILLATION'
    if (prev_state in ('WEAK', 'RETREAT', 'EXHAUSTION') and d_trend > 0 and d_brd > 0 and trend >= 40) \
            or (d_trend > 3 and d_brd > 3 and trend >= 40):
        return 'RECOVERY'
    return 'WEAK'


def _v21_mainline_candidate(f):
    """主线候选（YES/CANDIDATE/NO）—— MAINLINE_CANDIDATE=YES 仍不代表可买（可买看 TradePermission）

    YES 门槛取规格第十三节「CORE_MAINLINE 进入主线交易池」的硬条件（确认70/持续60/广度65/龙头65），
    目的是让「主线」极难伪造：高迁移、高情绪、高涨停数都不能单独把主题送进 YES。
    """
    comp = float(f.get('composite') or 0)
    conf = float(f.get('confirmation') or 0)
    brd = float(f.get('breadth') or 0)
    lead = float(f.get('leadership') or 0)
    pers = float(f.get('persistence') or 0)
    if (comp >= 65 and conf >= 70 and pers >= 60 and brd >= 65 and lead >= 65
            and f.get('single_leader_risk') != 'HIGH'
            and f.get('state') not in ('EXHAUSTION', 'DIVERGENCE', 'RETREAT')):
        return 'YES'
    if comp >= 55 and conf >= 50 and brd >= 50:
        return 'CANDIDATE'
    return 'NO'


def _v21_quadrant(f):
    """强度—确认度二维四象限（比单纯排名更有交易价值）

    I   CORE_MAINLINE          强度≥60 且 确认≥60
    II  HOT_BUT_UNCONFIRMED    强度≥60 但 确认<60
    III EARLY_FLOW             强度 40~60（有基础但未强）+ 迁移≥20（资金先行），退潮/透支不列此象限
    IV  WEAK                   其余
    """
    comp = float(f.get('composite') or 0)
    conf = float(f.get('confirmation') or 0)
    mig = float(f.get('migration') or 0)
    state = f.get('state')
    if comp >= 60 and conf >= 60:
        return 'CORE_MAINLINE'
    if comp >= 60:
        return 'HOT_BUT_UNCONFIRMED'
    if 40.0 <= comp < 60.0 and mig >= 20 and state not in ('RETREAT', 'EXHAUSTION'):
        return 'EARLY_FLOW'
    return 'WEAK'


def _v21_permission_xs(f, pct):
    """TradePermission 三层定档 —— 横截面分位口径（不再使用四维 AND 绝对门槛）

    pct：该主题在当日全部主题中的分位（0~1，越大越高）
         comp 强度(composite) / brd 广度(breadth) / crowd 拥挤度(距前高位置 + 量能)

    分层语义（每层独立可达，不再互相嵌套成空集）：
      NO_TRADE     退潮 / 情绪透支 —— 强度再高也不参与
      WATCH        入池观察（默认层，也是拥挤档的降落层）
      CONDITIONAL  条件确认：强度前 50% + 未背离 + 非单龙头依赖 + 非拥挤
      TRADEABLE    核心主线：强度与广度双双前 20% + 非背离 + 非单龙头依赖 + 非拥挤

    注意：TRADEABLE 只表示「当日最值得聚焦」，不代表已证实正超额 —— 顺势与反转因子
         在本样本内符号随 regime 翻转（详见 backtest_theme_v21 逐段结论）。
    """
    state = str(f.get('state') or '')
    if state in V21_BLOCK_STATES:
        return 'NO_TRADE'
    crowded = float(pct.get('crowd') or 0) >= 1.0 - V21_CROWD_TOP
    blocked = crowded or f.get('single_leader_risk') == 'HIGH' or state in V21_WATCH_ONLY_STATES
    comp = float(pct.get('comp') or 0)
    brd = float(pct.get('brd') or 0)
    if not blocked and comp >= 1.0 - V21_MAINLINE_TOP and brd >= 1.0 - V21_MAINLINE_TOP:
        return 'TRADEABLE'
    if not blocked and comp >= 1.0 - V21_COND_TOP:
        return 'CONDITIONAL'
    return 'WATCH'


def _v21_assign_layers(rows):
    """当日横截面分层 —— 必须在当日全部主题算完后调用（单主题看不到自己的分位）

    拥挤度 = 距20日前高位置 与 量能(5日/20日) 的当日横截面秩均值，
    直接回答「这个主题是不是又高又放量」。这是全部扫描中唯一跨 regime 方向一致的维度，
    因此作为唯一风险闸；四维仅作描述性字段，不参与许可判定。
    """
    n = len(rows)
    if n == 0:
        return rows

    def pct_rank(key):
        order = sorted(range(n), key=lambda i: float(rows[i].get(key) or 0))
        return {i: (p / (n - 1) if n > 1 else 0.0) for p, i in enumerate(order)}

    c_rank = pct_rank('composite')
    b_rank = pct_rank('breadth')
    p_rank = pct_rank('pos20')
    v_rank = pct_rank('vol_ratio')
    for i, r in enumerate(rows):
        r['trade_permission'] = _v21_permission_xs(r, {
            'comp': c_rank[i], 'brd': b_rank[i],
            'crowd': 0.5 * (p_rank[i] + v_rank[i]),
        })
        _, buy_mode, action, _ = _v21_action(r)
        r['buy_mode'], r['action'] = buy_mode, action
    return rows


def _v21_action(f):
    """统一动作语言（禁止模糊表达）：NO_TRADE/WATCH/CONDITIONAL/TRADEABLE + PULLBACK_ONLY/AVOID_CHASING"""
    perm = f['trade_permission']
    chase = float(f.get('chase_risk') or 0)
    avoid = chase >= V21_CHASE_LIMIT
    if perm == 'TRADEABLE':
        buy_mode = 'PULLBACK_ONLY' if avoid else 'MARKET_BUY'
        action = ('可交易，但仅限回踩买（不追高）' if avoid else '可交易（趋势/广度/龙头/持续性共同确认）')
    elif perm == 'CONDITIONAL':
        buy_mode = 'PULLBACK_ONLY'
        action = '条件交易：等分歧转一致 / 放量突破 / 龙头确认 / 回踩不破'
        if avoid:
            action += '（追高风险高，只可回踩）'
    elif perm == 'WATCH':
        buy_mode = 'NO_BUY'
        action = '只观察，不追，等待确认'
    else:
        buy_mode = 'NO_BUY'
        action = '不交易（确认不足）'
    return perm, buy_mode, action, ('AVOID_CHASING' if avoid else '')


def calc_v21_theme(r, hist=None, mkt_ret_1=0.0, market_ret_10=0.0):
    """单主题 V2.1 全字段计算（附加层核心）

    r:    V2 主题结果（只读，不修改 V2 既有键）
    hist: 该主题历史 V2.1 行（升序，来自 theme_v21_daily，不含今日）
    """
    hist = hist or []
    sd = r.get('sentiment_detail', {}) or {}
    td = r.get('trend_detail', {}) or {}
    rows = r.get('stock_rows') or []
    zt_count = int(sd.get('zt_count', 0) or 0)
    up_ratio = float(sd.get('up_ratio', 0) or 0)
    ret_1 = _v21_theme_ret1(r)
    ma20_b = float(np.mean([float(x.get('ma20_b', 0) or 0) for x in rows])) if rows else 0.0
    pos_in_20 = float(np.mean([float(x.get('pos_in_20', 0) or 0) for x in rows])) if rows else 0.0
    # 拥挤度原料（当日横截面分层用，不进表）：距20日前高位置 + 量能(5日/20日)
    pos20 = pos_in_20 * 100.0
    vol_ratio = float(np.mean([float(x.get('vol_ratio', 1.0) or 1.0) for x in rows])) if rows else 1.0

    breadth, b_detail = calc_breadth_v21(rows, mkt_ret_1)
    leadership, l_detail, slr = calc_leadership_v21(
        rows, float(td.get('avg_ret_5', 0) or 0))
    strength = float(r.get('composite_score', 0) or 0)
    flow = _v21_flow(r.get('migration_score', 0))

    # ── 序列（升序，含今日）：强度 + 主题收益 + 市场收益，用于持续性/跑赢市场 ──
    series = [{'strength': h.get('strength'), 'ret_1': h.get('ret_1'), 'mkt_ret_1': h.get('mkt_ret_1')}
              for h in hist if h.get('strength') is not None]
    series.append({'strength': strength, 'ret_1': ret_1, 'mkt_ret_1': mkt_ret_1})
    persistence, p_detail = calc_persistence_v21(series)

    prev = hist[-1] if hist else {}
    prev_state = str(prev.get('state') or '')
    d_breadth = breadth - float(prev.get('breadth') or 0) if prev else 0.0
    d_trend = float(r.get('trend_score', 0) or 0) - float(prev.get('trend') or 0) if prev else 0.0
    d_pers = persistence - float(prev.get('persistence') or 0) if prev else 0.0
    d_emotion = float(r.get('sentiment_score', 0) or 0) - float(prev.get('emotion') or 0) if prev else 0.0
    d_migration = float(r.get('migration_score', 0) or 0) - float(prev.get('migration') or 0) if prev else 0.0

    # ── Confirmation 25%Trend + 20%Breadth + 20%Leadership + 20%Persistence + 15%Flow ──
    confirmation = (0.25 * float(r.get('trend_score', 0) or 0) + 0.20 * breadth
                    + 0.20 * leadership + 0.20 * persistence + 0.15 * flow)
    # ── MainlineConfirmation 20/20/20/20 + Migration10 + Emotion10（禁止情绪或涨停数单独决定）──
    mainline_conf = (0.20 * float(r.get('trend_score', 0) or 0) + 0.20 * breadth
                     + 0.20 * leadership + 0.20 * persistence + 0.10 * flow
                     + 0.10 * float(r.get('sentiment_score', 0) or 0))

    f = {
        'trend': float(r.get('trend_score', 0) or 0), 'emotion': float(r.get('sentiment_score', 0) or 0),
        'composite': strength, 'strength': strength, 'migration': float(r.get('migration_score', 0) or 0),
        'breadth': breadth, 'leadership': leadership, 'persistence': persistence,
        'confirmation': round(_v21_clamp(confirmation), 1),
        'mainline_conf': round(_v21_clamp(mainline_conf), 1), 'flow': flow,
        'limitup': zt_count, 'up_ratio': up_ratio, 'zt_count': zt_count,
        'ret_1': ret_1, 'mkt_ret_1': mkt_ret_1, 'ma20_b': ma20_b, 'pos_in_20': pos_in_20,
        'n_stocks': len(rows),
        'd_breadth': d_breadth, 'd_trend': d_trend, 'd_persistence': d_pers,
        'prev_state': prev_state, 'single_leader_risk': slr,
        'zt_expansion': zt_count > int(prev.get('limitup') or 0) if prev else False,
        'breadth_expansion': d_breadth > 0,
    }
    f['confirmation'] = round(_v21_clamp(confirmation), 1)
    f.update(p_detail)
    chase, c_detail = calc_chase_risk_v21(f)
    f['chase_risk'] = chase
    state = classify_state_v21(f)
    f['state'] = state
    f['quadrant'] = _v21_quadrant(f)
    f['mainline_candidate'] = _v21_mainline_candidate(f)
    # trade_permission / buy_mode / action 依赖当日横截面分位（单主题看不到全局），
    # 统一由 run_v21_layer → _v21_assign_layers 赋值；此处只算 ChaseRisk 供 flags 记录。
    avoid = chase >= V21_CHASE_LIMIT

    # ── 状态转换检测（D-1 / D-3 / D-5）──
    d3_state = str(hist[-3].get('state') or '') if len(hist) >= 3 else ''
    d5_state = str(hist[-5].get('state') or '') if len(hist) >= 5 else ''
    if not prev_state:
        change = 'NEW'
    else:
        i0 = V21_STATE_LADDER.index(prev_state) if prev_state in V21_STATE_LADDER else 1
        i1 = V21_STATE_LADDER.index(state) if state in V21_STATE_LADDER else 1
        change = 'UPGRADE' if i1 > i0 else ('DOWNGRADE' if i1 < i0 else 'FLAT')
    reason = (f"Migration {d_migration:+.1f}, Emotion {d_emotion:+.1f}, "
              f"Breadth {d_breadth:+.1f}, Trend {d_trend:+.1f}")
    flags = json.dumps({**b_detail, **l_detail, **c_detail, 'chase_flag': avoid},
                       ensure_ascii=False)

    return {
        'trade_date': TRADE_DATE_str, 'theme': r.get('theme', ''), 'rank': r.get('rank', 0),
        'trend': round(f['trend'], 1), 'emotion': round(f['emotion'], 1),
        'composite': round(strength, 1), 'strength': round(strength, 1),
        'strength_v3': round(float(r.get('strength_score', 0) or 0), 1),
        'limitup': zt_count, 'migration': round(f['migration'], 1),
        'breadth': breadth, 'leadership': leadership, 'persistence': persistence,
        'confirmation': f['confirmation'], 'mainline_conf': f['mainline_conf'], 'flow': flow,
        'state': state, 'state_d3': d3_state, 'state_d5': d5_state, 'prev_state': prev_state,
        'state_change': change, 'change_reason': reason, 'quadrant': f['quadrant'],
        'mainline_candidate': f['mainline_candidate'], 'chase_risk': chase,
        # 占位：由 _v21_assign_layers 按当日横截面分位覆写
        'trade_permission': 'WATCH', 'buy_mode': 'NO_BUY', 'action': '待当日横截面定档',
        'single_leader_risk': slr, 'ret_1': ret_1, 'mkt_ret_1': round(float(mkt_ret_1 or 0), 4),
        'pers_shape': p_detail['pers_shape'], 'flags': flags,
        # 拥挤度原料（不进表，仅当日横截面分层用）
        'pos20': round(pos20, 1), 'vol_ratio': round(vol_ratio, 3),
        # 透传诊断（不进表，仅 JSON/MD 用）
        '_b': b_detail, '_l': l_detail, '_c': c_detail, '_p': p_detail,
    }


# ─────────── V2.1 持久化 / 输出 ───────────

V21_COLS = ('trade_date', 'theme', 'rank', 'trend', 'emotion', 'composite', 'strength',
            'strength_v3', 'limitup', 'migration', 'breadth', 'leadership', 'persistence',
            'confirmation', 'mainline_conf', 'flow', 'state', 'state_d3', 'state_d5',
            'prev_state', 'state_change', 'change_reason', 'quadrant', 'mainline_candidate',
            'chase_risk', 'trade_permission', 'buy_mode', 'action', 'single_leader_risk',
            'ret_1', 'mkt_ret_1', 'pers_shape', 'flags')


def _v21_ensure_table(conn):
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {V21_TABLE} (
        trade_date TEXT, theme TEXT, rank INTEGER, trend REAL, emotion REAL,
        composite REAL, strength REAL, strength_v3 REAL, limitup INTEGER, migration REAL,
        breadth REAL, leadership REAL, persistence REAL, confirmation REAL,
        mainline_conf REAL, flow REAL, state TEXT, state_d3 TEXT, state_d5 TEXT,
        prev_state TEXT, state_change TEXT, change_reason TEXT, quadrant TEXT,
        mainline_candidate TEXT, chase_risk REAL, trade_permission TEXT, buy_mode TEXT,
        action TEXT, single_leader_risk TEXT, ret_1 REAL, mkt_ret_1 REAL,
        pers_shape TEXT, flags TEXT, PRIMARY KEY (trade_date, theme))""")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_v21_date ON {V21_TABLE}(trade_date)")


def save_v21_sqlite(rows, trade_date_str, mkt_ret_1=0.0):
    """V2.1 落库（独立表 theme_v21_daily，与 V2 的 theme_scores 互不影响）"""
    conn = sqlite3.connect(OUTPUT_DB)
    try:
        _v21_ensure_table(conn)
        conn.execute(f"DELETE FROM {V21_TABLE} WHERE trade_date = ?", (str(trade_date_str),))
        ph = ",".join("?" * len(V21_COLS))
        conn.executemany(
            f"INSERT INTO {V21_TABLE} ({', '.join(V21_COLS)}) VALUES ({ph})",
            [tuple(x.get(c) for c in V21_COLS) for x in rows])
        conn.commit()
        print(f"[保存] SQLite {V21_TABLE}: {trade_date_str} {len(rows)} 条")
    finally:
        conn.close()


def _load_v21_history(trade_date, days=V21_HIST_DAYS):
    """读 V2.1 历史（截至 trade_date 前，最近 days 个交易日），返回 {theme: [升序行]}"""
    if not os.path.exists(OUTPUT_DB):
        return {}
    conn = sqlite3.connect(OUTPUT_DB)
    conn.row_factory = sqlite3.Row
    try:
        _v21_ensure_table(conn)
        dts = [r[0] for r in conn.execute(
            f"SELECT DISTINCT trade_date FROM {V21_TABLE} WHERE trade_date < ? "
            f"ORDER BY trade_date DESC LIMIT ?", (str(trade_date), int(days))).fetchall()]
        if not dts:
            return {}
        ph = ",".join("?" * len(dts))
        out = defaultdict(list)
        for row in conn.execute(
                f"SELECT * FROM {V21_TABLE} WHERE trade_date IN ({ph}) ORDER BY trade_date ASC", dts):
            out[row['theme']].append(dict(row))
        return out
    finally:
        conn.close()


def save_v21_outputs(rows, trade_date_str):
    """V2.1 输出：theme_v21_CSV/JSON/MD + theme_v2_CSV 别名（A/B 配对用）"""
    flat = [{k: v for k, v in x.items() if not k.startswith('_')} for x in rows]
    p_csv = os.path.join(REPORT_DIR, f"theme_v21_{trade_date_str}.csv")
    pd.DataFrame(flat).to_csv(p_csv, index=False, encoding="utf-8-sig")
    p_json = os.path.join(REPORT_DIR, f"theme_v21_{trade_date_str}.json")
    with open(p_json, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    p_md = os.path.join(REPORT_DIR, f"theme_v21_{trade_date_str}.md")
    with open(p_md, 'w', encoding='utf-8') as f:
        f.write(_v21_report_md(rows, trade_date_str))
    # V2 原始排名表别名（内容 = V2 既有 CSV，不重算，仅供 A/B 配对读取）
    src = os.path.join(REPORT_DIR, f"theme_scores_v2_{trade_date_str}.csv")
    if os.path.exists(src):
        with open(src, 'rb') as fi, open(os.path.join(REPORT_DIR, f"theme_v2_{trade_date_str}.csv"), 'wb') as fo:
            fo.write(fi.read())
    print(f"[保存] V2.1: {os.path.basename(p_csv)} / {os.path.basename(p_json)} / {os.path.basename(p_md)}")


def _v21_pick(rows, key, limit=None):
    out = sorted([x for x in rows if x['quadrant'] == key], key=lambda x: -x['strength'])
    return out[:limit] if limit else out


def _v21_line(x):
    return (f"  · {x['theme']} [Strength {x['strength']:.1f} | Confirmation {x['confirmation']:.1f} | "
            f"{x['state']} | Mainline {x['mainline_candidate']} | Trade {x['trade_permission']}"
            f"{' / ' + x['buy_mode'] if x['buy_mode'] != 'NO_BUY' else ''}] "
            f"广度{x['breadth']:.0f} 龙头{x['leadership']:.0f} 持续{x['persistence']:.0f} "
            f"迁移{x['migration']:.0f} 追高{x['chase_risk']:.0f}")


def _v21_report_md(rows, trade_date_str):
    """V2.1 盘后报告：四层结论 + 状态转换 + 全字段排名表"""
    L = []
    W = L.append
    sep = "━" * 60
    W(f"{sep}")
    W(f"# 主题量化分析 V2.1（状态识别 / 持续性确认 / 交易许可）- {trade_date_str}")
    W(f"{sep}")
    W(f"* 核心：强度用于发现，确认用于判断，持续性用于验证，交易许可用于执行，追高风险决定怎么进")
    W(f"* 禁止映射：高综合分≠主线；高迁移≠强趋势；高情绪≠可买入")
    dist = {}
    for x in rows:
        dist[x['state']] = dist.get(x['state'], 0) + 1
    W(f"* 状态分布（{len(rows)} 主题）：" + "、".join(
        f"{k}{v}" for k, v in sorted(dist.items(), key=lambda z: -z[1])))
    chg = [x for x in rows if x['state_change'] in ('UPGRADE', 'DOWNGRADE')]
    W(f"* 状态转换：{len(chg)} 个主题发生方向变化（UPGRADE/DOWNGRADE），详见第 6 节")
    W("")

    W("## 1. 当前主线（CORE_MAINLINE：强度≥60 且 确认≥60，最多 3 个）")
    W("  注意：进入本象限只代表「强 + 确认相对高」= 主线候选，不等于可交易；可否交易一律看 Trade 列。")
    core = _v21_pick(rows, 'CORE_MAINLINE', 3)
    if not core:
        W("  无。今日没有主题同时具备高强度与高确认（这正是必须空仓或等待的原因）。")
    for x in core:
        W(_v21_line(x))
        W(f"      Action: {x['action']}")
    W("")

    W("## 2. 高强度但未确认（HOT_BUT_UNCONFIRMED：强度≥60 且 确认<60）")
    W("  重点跟踪：强主题 → 分歧 → 再确认（等确认上来才是机会）")
    hot = _v21_pick(rows, 'HOT_BUT_UNCONFIRMED')
    if not hot:
        W("  无。")
    for x in hot:
        W(_v21_line(x))
    W("")

    W("## 3. 资金提前流入（EARLY_FLOW：强度 40~60 且 迁移≥20，退潮/透支不计入）")
    W("  重点跟踪：Migration↑ → Breadth↑ → Trend↑；连续 2~3 日确认才升级")
    ef = _v21_pick(rows, 'EARLY_FLOW')
    if not ef:
        W("  无（今日无主题达到「资金先行」门槛：迁移≥20）。")
    for x in ef:
        W(_v21_line(x))
    W("")

    W("## 4. 退潮 / 风险主题（EXHAUSTION / RETREAT / DIVERGENCE，用于降低暴露）")
    risk = [x for x in rows if x['state'] in ('EXHAUSTION', 'RETREAT', 'DIVERGENCE')]
    risk.sort(key=lambda x: -x['chase_risk'])
    if not risk:
        W("  无。")
    for x in risk:
        W(_v21_line(x))
    W("")

    W("## 5. 主题排名（Strength 与 Confirmation 共同解释，不只看 Composite）")
    W("  | # | 主题 | Strength | Confirmation | State | Mainline | Trade | 追高 | Action |")
    W("  |---|------|---------:|-------------:|-------|----------|-------|-----:|--------|")
    for i, x in enumerate(sorted(rows, key=lambda z: -z['strength']), 1):
        W(f"  | {i} | {x['theme']} | {x['strength']:.1f} | {x['confirmation']:.1f} | "
          f"{x['state']} | {x['mainline_candidate']} | {x['trade_permission']} | "
          f"{x['chase_risk']:.0f} | {x['action']} |")
    W("")

    W("## 6. 状态转换明细（D-1 / D-3 / D-5）")
    if not chg:
        W("  今日无状态方向变化。")
    for x in sorted(chg, key=lambda z: z['state_change']):
        W(f"  · {x['theme']}：{x['prev_state'] or '—'} → {x['state']}（{x['state_change']}）")
        W(f"      原因：{x['change_reason']}；D-3 {x['state_d3'] or '—'} / D-5 {x['state_d5'] or '—'}")
    W("")
    W("注：V2.1 为附加层，不改变 V2 原有输出与配仓；V2/V2.1 双跑便于 A/B Test。")
    return "\n".join(L) + "\n"


def _print_v21_layers(rows, trade_date_str):
    print(f"\n{'='*100}")
    print(f"主题量化分析 V2.1 - {trade_date_str}")
    print(f"{'='*100}")
    dist = {}
    for x in rows:
        dist[x['state']] = dist.get(x['state'], 0) + 1
    print(f"状态分布: {dist}")
    for name, key, lim in (("CORE_MAINLINE", 'CORE_MAINLINE', 3),
                           ("HOT_BUT_UNCONFIRMED", 'HOT_BUT_UNCONFIRMED', None),
                           ("EARLY_FLOW", 'EARLY_FLOW', None)):
        sel = _v21_pick(rows, key, lim)
        print(f"{name} ({len(sel)}): " + ("; ".join(
            f"{x['theme']} S{x['strength']:.0f}/C{x['confirmation']:.0f}/{x['state']}/{x['trade_permission']}"
            for x in sel) if sel else "无"))
    risk = [x for x in rows if x['state'] in ('EXHAUSTION', 'RETREAT', 'DIVERGENCE')]
    print("退潮/风险: " + ("; ".join(f"{x['theme']}({x['state']})" for x in risk) if risk else "无"))


def run_v21_layer(results, trade_date_str=None, idx_df=None, market_ret_10=0.0):
    """V2.1 主入口：读 V2 results → 计算 → 落库 → 输出（不改动 V2 任何产物）"""
    trade_date_str = str(trade_date_str or TRADE_DATE_str)
    mkt_daily = {}
    if idx_df is not None and not idx_df.empty:
        _idx = idx_df.sort_values('trade_date')
        _dt = _idx['trade_date'].astype(str).values
        _cl = _idx['close'].astype(float).values
        for i in range(1, len(_cl)):
            mkt_daily[_dt[i]] = (_cl[i] / _cl[i - 1] - 1.0) * 100.0 if _cl[i - 1] else 0.0
    mkt_ret_1 = float(mkt_daily.get(trade_date_str, 0.0))
    hist_map = _load_v21_history(trade_date_str, V21_HIST_DAYS)
    rows = []
    for r in results:
        if not r.get('stock_rows'):
            continue
        v = calc_v21_theme(r, hist_map.get(r['theme']), mkt_ret_1, market_ret_10)
        r['v21'] = v
        rows.append(v)
    rows.sort(key=lambda x: -float(x['strength']))
    for i, v in enumerate(rows, 1):
        v['rank'] = i
    # 当日横截面分层：许可层级 / buy_mode / action 必须看到全部主题才可定
    _v21_assign_layers(rows)
    save_v21_sqlite(rows, trade_date_str, mkt_ret_1)
    if not V21_BACKFILL_MODE:
        save_v21_outputs(rows, trade_date_str)
        _print_v21_layers(rows, trade_date_str)
    return rows


def v21_backfill(days=60):
    """按时间顺序回填最近 N 个交易日的 V2.1 历史（只写 theme_v21_daily，不动 V2 产物）

    用途：Persistence D10/D20、状态转换、60 日回测必须的历史序列。
    """
    global V21_BACKFILL_MODE
    from stock_cache import get_recent_trade_dates
    dates = sorted(str(d) for d in get_recent_trade_dates(n=int(days)))
    print(f"[V2.1 回填] 目标 {len(dates)} 个交易日: {dates[0]} ~ {dates[-1]}")
    V21_BACKFILL_MODE = True
    ok, fail = 0, []
    _t0 = time.time()
    try:
        for i, d in enumerate(dates, 1):
            _td = time.time()
            print(f"\n{'#'*60}\n[V2.1 回填] {i}/{len(dates)} {d}\n{'#'*60}")
            try:
                run_v2_analysis(d)
                ok += 1
            except Exception as e:
                fail.append((d, str(e)))
                print(f"[V2.1 回填] {d} 失败: {e}")
            print(f"[V2.1 回填] {d} 用时 {time.time() - _td:.1f}s，累计 {(time.time() - _t0)/60:.1f}min")
    finally:
        V21_BACKFILL_MODE = False
    print(f"\n[V2.1 回填] 完成 {ok}/{len(dates)}，失败 {len(fail)}")
    for d, e in fail:
        print(f"   失败 {d}: {e}")
    return ok, fail


# ─────────── CLI ───────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] in ('--v21-backfill', '--backfill'):
        v21_backfill(int(sys.argv[2]))
    elif len(sys.argv) > 2 and sys.argv[1] == '--no-v21':
        _orig = run_v21_layer
        globals()['run_v21_layer'] = lambda *a, **k: None
        run_v2_analysis(sys.argv[2])
        globals()['run_v21_layer'] = _orig
    elif len(sys.argv) > 1:
        run_v2_analysis(sys.argv[1])
    else:
        run_v2_analysis()
