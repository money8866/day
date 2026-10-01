#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题热度 V2.4 —— 三周期最强主题 + 成份股映射质量校验
════════════════════════════════════════════════════════════

这个模块只回答四个问题：

    今日最强主题是谁？      → TODAY TOP / 【今日最强】
    本周最强主题是谁？      → WEEK TOP / 【本周最强】
    本月最强主题是谁？      → MONTH TOP / 【本月最强】
    这些主题的成份股真的属于该主题吗？ → 成份股映射质量校验

V2.4 相对 V2.3 的改动（§三~§九/§十三/§十六~§十八/§二十四/§二十九/§三十二）：
  · 新增映射质量层（先校验成份股，再算热度）：
      证据 = 人工确认名单(leaders/core_stocks) → 来源(via) → 主营文本×(关键词/exclude_keywords)
      分层 = CORE / NORMAL / WEAK；MappingQuality = (CORE+NORMAL)/成员总数
      状态 = MAPPING_OK ≥85% / MAPPING_WARN 70~85% / MAPPING_BAD <70%
  · Breadth / UpRatio 只在 CORE+NORMAL 成员上计算，WEAK 成员不稀释扩散度
  · 样本分层：N<10 LOW_SAMPLE（不得进入「最强主题」结论，排名显示 #1*）；
      10≤N<30 SMALL_SAMPLE；N≥30 正常
  · Heat = 50%×HeatRaw + 50%×ReliabilityAdjustedHeat（§十八）
  · 新增异常检查：LOW_SAMPLE_LEADER / LOW_BREADTH / NARROW_STRONG /
      MAPPING_BAD / MEMBER_COUNT_ANOMALY / HEAT_INFLATION_WARNING
  · COOLING 门槛改为 Today >10（§二十八）；新增【三个核心答案】与成份股映射审计

不设计也不输出（§十六/§三十六 永久禁止）：BUY / SELL / 仓位 / 止损 / 买点 / 交易评级 /
主线交易建议，以及 WEAK / RECOVERY / STARTING / STRONG_TREND / RETREAT / EXHAUSTION /
MAINLINE / CONFIRMATION / PERSISTENCE / LEADERSHIP / CHASE_RISK / StrengthScore /
TradeScore / 资金流 / 情绪周期 / 迁移评分。这些属于后续股票级交易模块。

映射质量只报告、绝不自动改写 theme_config.json / subtheme_map.json（§三十四）。

不改动任何其他量化模块的算法逻辑（§二：不碰 HVT / 突破 / DLG / F120 的执行与选股规则）。
下游消费统一到 V2.4（20260927 打通）：load_heat / top_heat 读 theme_heat_v24_*，
hvt_bull.context._HEAT_PREFIX 同步改为 v24；theme_heat_v23_* 为历史遗留文件，已无模块读取。

数据源（不新增数据源、不重新下载全量数据）：
  · 现有 stock_cache SQLite：cache_daily/stock_data.db 的 daily_cache 表（个股日线）
  · 现有主题成员池：cache_daily/theme_stock_map_v2_{date}.json
    （该文件由 theme_config.json + subtheme_map.json 生成，即 §2 所称「现有主题成员映射」）

用法：
    python theme_heat_v22.py              # 最近一个交易日
    python theme_heat_v22.py 20260923     # 指定交易日
"""

import os
import sys
import json

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

REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
CACHE_DIR = r'd:\mystock\cache_daily'

# ── §10 核心权重（价格是核心、扩散是确认、活跃度只是辅助；不得自动优化权重）──
W_PRICE, W_BREADTH, W_ACTIVITY = 0.60, 0.30, 0.10
# ── §6.3 PriceRaw 内部：P50 权重必须高于 P75（先反映「整体是否普遍变强」）──
W_P50, W_P75 = 0.70, 0.30
# ── §7 BreadthRaw 内部：上涨占比 / 强涨(≥3%)占比 ──
W_UP, W_STRONG = 0.70, 0.30

STRONG_PCT = 3.0                 # §7 强涨门槛（%）
RET_CLIP = 20.0                  # §20 成员收益率 clip 到 [-20%, +20%]（只用于热度统计）
WIN_WEEK, WIN_MONTH = 5, 20      # §4 WEEK / MONTH 观察窗口（交易日）
ACT_LOOKBACK = 20                # §8 过去 20 日平均成交额
ACT_MIN_PERIODS = 10             # 成交额历史不足 10 日则不算 Activity
ACT_FLOOR, ACT_CAP = 0.5, 3.0    # §8 单股 Activity_i 双侧截断（缩量不奖励、妖股不拉爆）
MIN_SAMPLE = 5                   # §三 N < 5 → 不进入正式排行榜（仅 LOW_SAMPLE 观察）
FULL_RELIABILITY_N = 50          # §十六 Reliability = min(1, sqrt(N/50))；N ≥ 50 → 不收缩
LOW_SAMPLE_MAX = 10              # §十六/§十七 N < 10 → LOW_SAMPLE（不得进入「最强主题」结论）
SMALL_SAMPLE_MAX = 30            # §十七 10 ≤ N < 30 → SMALL_SAMPLE（允许排名，必须标记）
LOW_BREADTH_UP = 0.40            # §十三 UpRatio 只作解释性展示，不单独触发 LOW_BREADTH（见 §二十九 B）
TOP_N = 10                       # §二十一/§二十五~§二十八 榜单条数与强弱门槛
MID_N = 10                       # §二十五~§二十八 跨周期观察表的次级门槛
CROSS_N = 10                     # §二十八 COOLING 今日门槛（Month ≤10 AND Week >10 AND Today >10）
HIST_DAYS = 60                   # 需覆盖 20(月窗口) + 20(活跃度回看) + 缓冲；实测 60 充足

# ── §九 成份股映射质量阈值（只降低数据可信度，不删除主题、不改热度公式）──
MAP_OK, MAP_WARN = 0.85, 0.70
# ── §二十九 异常检查阈值 ──
LOW_BREADTH_HEAT, LOW_BREADTH_MIN = 80.0, 0.25   # Heat ≥ 80 且 Breadth < 25% → LOW_BREADTH
NARROW_GAP = 8.0                                 # P75 − P50 ≥ 8pp → NARROW_STRONG（极端上涨集中）
MEMBER_COUNT_RATIO = 1.5                         # 成员数 ≥1.5× 或 ≤1/1.5× → MEMBER_COUNT_ANOMALY
INFLATION_SHARE = 0.30                           # §三十一 Heat ≥ 80 的主题占比 > 30% → HEAT_INFLATION_WARNING
CROSS_THEME_MIN = 5                              # §六 G3 同一股票被 ≥5 个主题纳入 → CROSS_THEME_POLLUTION
AUDIT_SAMPLE = 5                                 # §三十二 每个审计主题抽样的成份股数
# §三十三 历史污染股：只报告 + 只提示人工确认，绝不自动改写配置
AUDIT_STOCKS = {'600186.SH': '莲花控股', '002851.SZ': '麦格米特',
                '300033.SZ': '同花顺', '300059.SZ': '东方财富'}
# 已人工裁定但核验未完成（保留在主题内、层级不变），审计行附提示，避免被读成「已确认」
PENDING_REVIEW = {('002851.SZ', '汽车'): '待核验（人工裁定 20260930：暂时保留，电控占比未核）'}

# ── §3/§19/§20/§21 V2.0 成员池与排名引擎（AI 五主题四级成员体系）──
N_CORE_MIN = 3                   # §3  CORE ≥ 3 → 允许进入主题排名（取代旧「N ≥ 5」硬门槛）
LOW_SAMPLE_CORE = 5              # §21 CORE < 5 → LOW_SAMPLE；CORE < 3 → INSUFFICIENT_SAMPLE
FULL_RELIABILITY_CORE_N = 10     # §20 sample_reliability = min(1, √(core_n/10))
EXT_WEIGHT = 0.65                # §19 membership_quality 中 EXTENSION 的折算权重
# 五个 AI 一级主题（与 theme_stock_map_v2 / theme_config.json 的 name_cn 口径一致）
AI5_KEYS = ('CPO', '液冷', 'AI服务器', '算力运营', 'AI应用')

CONFIG_V3_PATH = os.path.join(BASE_DIR, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json')
MEMBERSHIP_V2_PATH = os.path.join(BASE_DIR, 'theme_membership_v2.json')
MAINBIZ_PATH = os.path.join(CACHE_DIR, 'stock_company_mainbiz.json')

SEP_FULL = "═" * 62
SEP_THIN = "─" * 62


# ═════════════════════════ 数据读取 ═════════════════════════

def resolve_trade_date(arg=None):
    from stock_cache import get_recent_trade_dates
    if arg:
        return str(arg)
    ds = get_recent_trade_dates(n=1)
    if not ds:
        raise SystemExit('daily_cache 无数据，无法确定最近交易日')
    return str(ds[-1])


def load_theme_members(trade_date):
    """主题 → {code: {name, via, ...}}，并返回映射文件路径与原始 JSON。优先精确日期，其次 latest。"""
    cands = [os.path.join(CACHE_DIR, f'theme_stock_map_v2_{trade_date}.json'),
             os.path.join(CACHE_DIR, 'theme_stock_map_v2_latest.json'),
             os.path.join(REPORT_DIR, 'theme_stock_map_latest_v2.json')]
    for p in cands:
        if not os.path.exists(p):
            continue
        with open(p, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        themes = raw.get('themes') or {}
        out = {}
        for t, lst in themes.items():
            recs = {}
            for s in lst:
                if isinstance(s, dict) and s.get('code'):
                    recs[s['code']] = {'name': s.get('name') or '',
                                       'via': s.get('via') or '',
                                       'irs_layer': s.get('irs_layer') or '',
                                       'industry': s.get('industry') or ''}
            if recs:
                out[t] = recs
        if out:
            return out, p, raw
    raise SystemExit('未找到 theme_stock_map_v2 映射文件')


# ═════════════════════════ §三~§九 成份股映射质量校验 ═════════════════════════

def load_theme_config_v3():
    """§四 G1 / §五 G2：读取 theme_kg_v3 完整主题配置（32 主题，含关键词与人工确认名单）"""
    if not os.path.exists(CONFIG_V3_PATH):
        raise SystemExit(f'未找到主题配置 {CONFIG_V3_PATH}')
    with open(CONFIG_V3_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    return {cfg.get('name_cn') or k: cfg for k, cfg in raw.items() if not k.startswith('_')}


def load_mainbiz():
    """§五 G2：个股主营文本（判断公司业务是否真的落在主题语义内）"""
    if not os.path.exists(MAINBIZ_PATH):
        return {}
    with open(MAINBIZ_PATH, 'r', encoding='utf-8') as f:
        return json.load(f) or {}


def _hits(txt, words):
    return [w for w in (words or []) if w and w in txt]


def load_membership_v2():
    """§4~§12/§17/§18 读取 theme_membership_v2.json（AI 五主题四级成员体系）

    返回 ({theme: {core_n, extension_n, related_n, membership_quality, sample_reliability,
                   sample_flag, cores, extensions, relateds}}, {code: name})；
    文件不存在或解析失败 → ({}, {})，引擎自动回退到 V2.4 旧口径（不影响其余主题）。
    """
    if not os.path.exists(MEMBERSHIP_V2_PATH):
        return {}, {}
    try:
        with open(MEMBERSHIP_V2_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return {}, {}
    out = {}
    for t, v in (raw.get('themes') or {}).items():
        out[t] = {
            'core_n': int(v.get('core_n') or 0),
            'extension_n': int(v.get('extension_n') or 0),
            'related_n': int(v.get('related_n') or 0),
            'membership_quality': float(v.get('membership_quality') or 0.0),
            'sample_reliability': float(v.get('sample_reliability') or 0.0),
            'sample_flag': str(v.get('sample_flag') or ''),
            'cores': set(v.get('cores') or []),
            'extensions': set(v.get('extensions') or []),
            'relateds': set(v.get('relateds') or []),
        }
    names = {r.get('ts_code'): (r.get('name') or '')
             for r in (raw.get('records') or []) if r.get('ts_code')}
    return (out or {}), names


def apply_membership_v2(mapping, mv2, config=None):
    """§17/§18/§19/§20/§21：给每个主题补上四级成员计数与样本可靠性字段

    · AI 五主题（出现在 theme_membership_v2.json）→ 直接采用人工核验分层；
    · 其余主题 → 以 V2.4 映射分层折算（CORE→core_n / NORMAL→extension_n / WEAK→related_n）。
      纯字段新增，不改写任何原始映射（§三十四）。
    """
    config = config or {}
    for t, mq in mapping.items():
        v = mv2.get(t)
        if v:
            mq.update({'core_n': v['core_n'], 'extension_n': v['extension_n'],
                       'related_n': v['related_n'],
                       'membership_quality': v['membership_quality'],
                       'sample_reliability': v['sample_reliability'],
                       'sample_flag': v['sample_flag'], 'membership_src': 'membership_v2',
                       'theme_cn': v.get('theme') or '', 'theme_full': v.get('theme_cn') or ''})
            continue
        cn, en, rn = mq['core'], mq['normal'], mq['weak']
        denom = cn + en
        qual = ((cn * 1.0 + en * EXT_WEIGHT) / denom) if denom else 0.0     # §19
        rel = min(1.0, float(np.sqrt(cn / float(FULL_RELIABILITY_CORE_N))))  # §20
        # 非 AI 主题的 sample_flag 沿用 V2.4 的「有效成员 N」口径（见 _low_sample）
        valid = mq['valid']
        flag = ('LOW_SAMPLE' if valid < LOW_SAMPLE_MAX                      # §21
                else ('SMALL_SAMPLE' if valid < SMALL_SAMPLE_MAX else 'NORMAL'))
        mq.update({'core_n': cn, 'extension_n': en, 'related_n': rn,
                   'membership_quality': qual, 'sample_reliability': rel,
                   'sample_flag': flag, 'membership_src': 'mapping',
                   'theme_cn': (config.get(t) or {}).get('name_cn') or t, 'theme_full': ''})
    return mapping


def classify_member(code, via, txt, cfg):
    """§八 G5：单只成员 → (CORE / NORMAL / WEAK, 归属依据)

    纯规则、无人工打分、可用证据复现：
      CORE   = 人工确认名单(leaders / core_stocks / manual_override)
               或主营文本命中 core_keywords / industry_keywords
      NORMAL = 主营文本命中 keywords / product_keywords / brand_keywords /
               concept_keywords / industry_chains（有明确业务关联，但非唯一核心方向）
      WEAK   = 其余（仅概念、合作、历史事件、边缘业务关联）
    只分三层（§八）；exclude_keywords 仅写进依据文本，不新增第四层。
    """
    manual = set(cfg.get('leaders') or []) | set(cfg.get('core_stocks') or [])
    if via == 'manual_override':
        return 'CORE', f'人工补漏映射（{via}）'
    if code in manual:
        return 'CORE', f'人工确认名单（{via}）'
    if not txt:
        return 'WEAK', f'无主营文本（{via}）'
    core_hit = _hits(txt, cfg.get('core_keywords')) or _hits(txt, cfg.get('industry_keywords'))
    if core_hit:
        return 'CORE', f'{via}｜主营命中核心词 {core_hit[0]}'
    norm = []
    for k in ('keywords', 'product_keywords', 'brand_keywords', 'concept_keywords', 'industry_chains'):
        norm += _hits(txt, cfg.get(k))
    # 环节清单（HC V1.0 §九 主题定义重构）：segments[*].keywords 是「允许纳入的环节」，
    # 命中即视为有明确业务关联 → NORMAL（RELATED），不再因关键词表缺口被误判 WEAK
    for _seg in (cfg.get('segments') or []):
        if isinstance(_seg, dict):
            norm += _hits(txt, _seg.get('keywords'))
    if norm:
        return 'NORMAL', f'{via}｜主营命中 {norm[0]}'
    excl = _hits(txt, cfg.get('exclude_keywords'))
    if excl:
        return 'WEAK', f'{via}｜命中排除词 {excl[0]}'
    return 'WEAK', f'{via}｜无主题相关主营证据'


def load_prev_member_counts(trade_date):
    """§七 G4：读取上一个可用交易日的映射，用于成员数量异常比对"""
    import glob
    import re
    best, best_d = None, ''
    for p in glob.glob(os.path.join(CACHE_DIR, 'theme_stock_map_v2_*.json')):
        m = re.search(r'theme_stock_map_v2_(\d{8})\.json$', os.path.basename(p))
        if not m:
            continue
        d = m.group(1)
        if d < str(trade_date) and d > best_d:
            best, best_d = p, d
    if not best:
        return {}
    try:
        with open(best, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return {}
    return {t: len(v) for t, v in (raw.get('themes') or {}).items()}


def build_mapping_quality(theme_members, config, mainbiz, prev_counts):
    """§三/§六/§七/§九：成份股映射质量 = 有效成员(CORE+NORMAL) / 总成员

    返回 {theme: {total, core, normal, weak, valid, quality, status, layers,
                  cross_pollution, count_anomaly, prev_total, multi}}
    只报告，绝不自动改写原始映射（§三十四）。
    """
    multi = {}                                    # §六 G3 同一股票被多少主题纳入
    for recs in theme_members.values():
        for c in recs:
            multi[c] = multi.get(c, 0) + 1
    out = {}
    for t, recs in theme_members.items():
        cfg = config.get(t)
        layers = {}
        for c, rec in recs.items():
            if cfg is None:
                layer, why = 'WEAK', '无对应主题配置'
            else:
                layer, why = classify_member(c, rec['via'], mainbiz.get(c, ''), cfg)
            layers[c] = {'layer': layer, 'why': why, 'name': rec['name'], 'via': rec['via']}
        cnt = {'CORE': 0, 'NORMAL': 0, 'WEAK': 0}
        for v in layers.values():
            cnt[v['layer']] += 1
        total = len(recs)
        valid = cnt['CORE'] + cnt['NORMAL']
        q = (valid / total) if total else 0.0
        status = 'OK' if q >= MAP_OK else ('WARN' if q >= MAP_WARN else 'BAD')
        prev = prev_counts.get(t)
        anom = bool(prev and total and (total / prev >= MEMBER_COUNT_RATIO
                                        or prev / total >= MEMBER_COUNT_RATIO))
        out[t] = {'total': total, 'core': cnt['CORE'], 'normal': cnt['NORMAL'],
                  'weak': cnt['WEAK'], 'valid': valid, 'quality': q, 'status': status,
                  'layers': layers, 'prev_total': prev, 'count_anomaly': anom,
                  'cross_pollution': sum(1 for c in recs if multi.get(c, 0) >= CROSS_THEME_MIN),
                  'multi': multi}
    return out


def sample_members(mq, k=AUDIT_SAMPLE):
    """§三十二 固定等间隔抽样：按 CORE→NORMAL→WEAK 排序后等间距取 k 只"""
    order = {'CORE': 0, 'NORMAL': 1, 'WEAK': 2}
    items = sorted(mq['layers'].items(),
                   key=lambda kv: (order.get(kv[1]['layer'], 9), kv[1]['name'], kv[0]))
    if not items:
        return []
    if len(items) <= k:
        return items
    idx = sorted({round(i * (len(items) - 1) / (k - 1)) for i in range(k)})
    return [items[i] for i in idx]


def mapping_advice(v):
    """§三十四 只给建议，不自动修改配置"""
    if v['layer'] == 'CORE':
        return '建议保留（人工确认名单或主营核心词命中）'
    if v['layer'] == 'NORMAL':
        return '建议保留，可人工复核主营占比'
    return '建议人工确认（若主营与主题无关，建议移除）'


def fetch_kline(codes, start, end):
    from theme_trend_sentiment_score import get_daily_kline
    df = get_daily_kline(sorted(codes), start, end)
    if df is None or df.empty:
        raise SystemExit(f'未取到任何日线数据 [{start} ~ {end}]')
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for c in ('close', 'pct_chg', 'amount'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def build_matrices(df, dates):
    """长表 → 三个对齐到交易日历的宽表 + Activity 宽表"""
    keep = set(dates)
    df = df[df['trade_date'].isin(keep)]
    P = df.pivot(index='trade_date', columns='ts_code', values='pct_chg').reindex(dates)
    C = df.pivot(index='trade_date', columns='ts_code', values='close').reindex(dates)
    A = df.pivot(index='trade_date', columns='ts_code', values='amount').reindex(dates)
    # §6 Activity_i = 今日成交额 / 过去20日平均成交额，单股双侧截断 clip(0.5, 3.0)
    MA = A.shift(1).rolling(ACT_LOOKBACK, min_periods=ACT_MIN_PERIODS).mean()
    ACT = (A / MA).clip(lower=ACT_FLOOR, upper=ACT_CAP)
    return P, C, ACT


# ═════════════════════════ 指标计算 ═════════════════════════

def window_metrics(P, C, ACT, cols, n, w):
    """单主题单窗口 -> {n, price, breadth, activity, up_ratio, p25/p50/p75, ...}

    w=1 为 TODAY（§四），w=5 为 WEEK，w=20 为 MONTH，口径完全一致。
    有效成员（§三 的 N）：窗口末日涨跌幅、基线收盘价、末日前收盘价三者俱全
    （基线 = 窗口起始日的前一交易日）。三个窗口的 N 可以不同（新股在长窗口会被剔除）。
    """
    lo = n - w
    Pw = P.iloc[lo:][cols]
    mask = Pw.notna().values
    pairs = int(mask.sum())
    if pairs == 0:
        return None
    # §7 广度：窗口内「成员×交易日」逐格统计，w=1 时退化为当日口径（用原始涨跌幅，不 clip）
    up_ratio = float(((Pw.values > 0) & mask).sum()) / pairs
    strong_ratio = float(((Pw.values >= STRONG_PCT) & mask).sum()) / pairs
    breadth = W_UP * up_ratio + W_STRONG * strong_ratio

    c_last = C.iloc[-1][cols].astype(float)
    c_base = C.iloc[n - w - 1][cols].astype(float)
    ok = c_last.notna() & c_base.notna() & (c_base != 0) & P.iloc[-1][cols].notna()
    if not bool(ok.any()):
        return None
    # §20 成员收益率先 clip 到 [-20%, +20%]，防极端涨停/异常复牌/数据错误污染主题 Price
    r = ((c_last[ok] / c_base[ok] - 1.0) * 100.0).clip(-RET_CLIP, RET_CLIP).values
    p25, p50, p75 = (float(np.percentile(r, q)) for q in (25, 50, 75))
    # §6.3 价格强度：中位数 70% + 75分位 30%
    price = W_P50 * p50 + W_P75 * p75

    # §8 主题 Activity = 成员 Activity_i 的中位数（逐日），周/月窗口取窗口内日均
    day_med = ACT.iloc[lo:][cols].median(axis=1)
    activity = float(day_med.mean()) if day_med.notna().any() else float('nan')
    return {'n': int(ok.sum()), 'price': price, 'breadth': breadth, 'activity': activity,
            'up_ratio': up_ratio, 'strong_ratio': strong_ratio,
            'p25': p25, 'p50': p50, 'p75': p75, 'ret_med': p50, 'ret_p75': p75}


def pct_rank(vals):
    """§7 横截面 Percentile Rank → 0~100（并列取平均名次；未经 rank 的原始值不做任何截尾）"""
    v = np.asarray([float(x) for x in vals], dtype=float)
    m = v.size
    if m == 0:
        return v
    if m == 1:
        return np.full(1, 50.0)
    order = np.argsort(v, kind='mergesort')
    sv = v[order]
    pos = np.empty(m, dtype=float)
    i = 0
    while i < m:
        j = i
        while j + 1 < m and sv[j + 1] == sv[i]:
            j += 1
        pos[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return pos / (m - 1) * 100.0


def dist_stats(vals):
    """§21 分布统计（不据此自动改权重或阈值）"""
    a = np.asarray([x for x in vals if x is not None and not (isinstance(x, float) and np.isnan(x))],
                   dtype=float)
    keys = ('P01', 'P10', 'P25', 'P50', 'P75', 'P90', 'P95', 'MAX')
    if a.size == 0:
        return {k: float('nan') for k in keys}
    return {'P01': float(np.percentile(a, 1)), 'P10': float(np.percentile(a, 10)),
            'P25': float(np.percentile(a, 25)), 'P50': float(np.percentile(a, 50)),
            'P75': float(np.percentile(a, 75)), 'P90': float(np.percentile(a, 90)),
            'P95': float(np.percentile(a, 95)), 'MAX': float(a.max())}


# ═════════════════════════ 消费端读取入口 ═════════════════════════

def _num(v):
    """JSON 单元格 → float 或 None（空值/NaN → None）"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else x


def load_heat(trade_date):
    """读取本地已生成的 theme_heat_v24_{trade_date}.json（消费端唯一入口）

    返回 {theme: {...}}；文件不存在或解析失败返回 None，由调用方回退旧主题源。
    strength = TODAY Heat 与 WEEK Heat 的均值（TODAY+WEEK 综合口径），
    某一窗口无数据时以另一窗口为准；两窗口都缺 → strength = None。
    """
    path = os.path.join(REPORT_DIR, f'theme_heat_v24_{trade_date}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            rows = json.load(f)
    except Exception:
        return None
    out = {}
    for r in rows:
        t = str(r.get('theme') or '')
        if not t:
            continue
        td, wk = _num(r.get('today_heat')), _num(r.get('week_heat'))
        avail = [x for x in (td, wk) if x is not None]
        out[t] = {
            'theme': t,
            'strength': round(float(np.mean(avail)), 2) if avail else None,
            'today_heat': td, 'week_heat': wk, 'month_heat': _num(r.get('month_heat')),
            'today_rank': _num(r.get('today_rank')), 'week_rank': _num(r.get('week_rank')),
            'month_rank': _num(r.get('month_rank')),
            'flags': str(r.get('today_flags') or ''),
            'n': _num(r.get('today_n')),
            'up_ratio': _num(r.get('today_up_ratio')),
            'reliability': _num(r.get('today_reliability')),
        }
    return out or None


def top_heat(n=3, trade_date=None):
    """按 TODAY+WEEK 综合强度取前 n 名 → [(theme, strength), ...]；无数据返回 []"""
    heat = load_heat(trade_date or resolve_trade_date())
    if not heat:
        return []
    ranked = [v for v in heat.values() if v['strength'] is not None]
    ranked.sort(key=lambda v: -v['strength'])
    return [(v['theme'], v['strength']) for v in ranked[:n]]


# V2 trend_score → 本模块 Heat 的等分位锚点（由 14 个交易日 × 32 主题的配对样本拟合，
# 样本区间 20260724~20260923）。两边在同一分位上取值，故折算后主题强弱次序与选择性不变。
V2_TO_HEAT_ANCHORS = ((0.0, 0.0), (30.0, 40.0), (40.0, 54.0), (45.0, 62.0),
                      (65.0, 81.0), (70.0, 85.0), (80.0, 94.0), (86.4, 100.0))


def v2_to_heat(score):
    """把 theme_score_v2 的 trend_score 折算成本模块 Heat 的同一分布尺度。

    仅供消费端在 V2.3 结果缺失、回退 theme_score_v2 时使用，避免阈值口径漂移。
    """
    x = _num(score)
    if x is None:
        return None
    return round(float(np.interp(x, [a for a, _ in V2_TO_HEAT_ANCHORS],
                                 [b for _, b in V2_TO_HEAT_ANCHORS])), 2)


# ═════════════════════════ 主流程 ═════════════════════════

def run(trade_date=None):
    from stock_cache import get_recent_trade_dates
    trade_date = resolve_trade_date(trade_date)
    all_dates = [str(d) for d in get_recent_trade_dates(n=HIST_DAYS, end_date=trade_date)]
    if not all_dates or all_dates[-1] != trade_date:
        raise SystemExit(f'{trade_date} 不在 daily_cache 交易日历中')
    n = len(all_dates)
    if n < WIN_MONTH + ACT_LOOKBACK + 2:
        raise SystemExit(f'历史交易日不足（{n}），无法计算 MONTH 与 Activity')
    dates = all_dates

    # ── §三 第一优先级：先校验成份股，再算热度 ──
    theme_members, map_path, _raw_map = load_theme_members(trade_date)
    config = load_theme_config_v3()
    mainbiz = load_mainbiz()
    prev_counts = load_prev_member_counts(trade_date)
    mv2, mv2_names = load_membership_v2()
    mapping = apply_membership_v2(
        build_mapping_quality(theme_members, config, mainbiz, prev_counts), mv2, config)

    # 热度只在「有效成员（CORE + NORMAL）」上计算（§三/§九/§十三）
    valid_members = {}
    for t, recs in theme_members.items():
        vc = {c: r for c, r in recs.items() if mapping[t]['layers'][c]['layer'] in ('CORE', 'NORMAL')}
        if vc:
            valid_members[t] = vc

    # §17 排名池（THEME_RANK_POOL）与成员池分离：
    #   AI 五主题 → theme_membership_v2.json 的 CORE + EXTENSION（RELATED 不进排名）；
    #   其余主题 → 沿用 V2.4 口径（CORE + NORMAL）。
    rank_members = {}
    miss_map = {}
    for t, recs in theme_members.items():
        if t in mv2:
            keep = mv2[t]['cores'] | mv2[t]['extensions']
            sel = {}
            for c in sorted(keep):
                if c in recs:
                    sel[c] = recs[c]
                else:                       # 已核验成员但不在映射文件（来源差异）→ 兜底记录
                    miss_map.setdefault(t, []).append(c)
                    sel[c] = {'name': mv2_names.get(c, ''), 'via': 'membership_v2',
                              'irs_layer': '', 'industry': ''}
            rank_members[t] = sel
        else:
            rank_members[t] = valid_members.get(t, {})
    all_codes = sorted({c for m in rank_members.values() for c in m})
    n_map = sum(len(v) for v in theme_members.values())
    n_ok = sum(1 for m in mapping.values() if m['status'] == 'OK')
    n_warn = sum(1 for m in mapping.values() if m['status'] == 'WARN')
    n_bad = sum(1 for m in mapping.values() if m['status'] == 'BAD')
    print(f"[V2.4] 交易日 {trade_date}｜主题 {len(theme_members)} 个｜映射 {os.path.basename(map_path)}")
    print(f"[V2.4] 交易日历 {dates[0]} ~ {dates[-1]}（{n} 日）｜映射成员 {n_map} 条"
          f"｜排名池成员 {len(all_codes)} 只")
    print(f"[V2.4] MappingQuality OK {n_ok} / WARN {n_warn} / BAD {n_bad}"
          f"｜成员数量异常 {sum(1 for m in mapping.values() if m['count_anomaly'])} 个主题")
    if mv2:
        print(f"[V2.4] 成员体系 V2.0：{len(mv2)} 个主题读 theme_membership_v2.json"
              f"（CORE≥{N_CORE_MIN} 准入；AdjustedHeat = RawHeat × sample_reliability × "
              f"membership_quality）")
    for t, cs in miss_map.items():
        print(f"[V2.4] 提示：{t} 有 {len(cs)} 只 V2.0 核验成员不在 theme_stock_map"
              f"（{'、'.join(cs[:5])}{'…' if len(cs) > 5 else ''}）")

    if not all_codes:
        print('[V2.4] DATA_INSUFFICIENT：排名池为空，无法计算热度')
        return None

    df = fetch_kline(all_codes, dates[0], dates[-1])
    P, C, ACT = build_matrices(df, dates)

    windows = {'TODAY': 1, 'WEEK': WIN_WEEK, 'MONTH': WIN_MONTH}
    raw = {wk: {} for wk in windows}
    missing_themes = []
    no_valid = [t for t in theme_members if not rank_members.get(t)]
    for t, members in rank_members.items():
        cols = [c for c in members if c in P.columns]
        if not cols:
            missing_themes.append(t)
            continue
        for wk, w in windows.items():
            m = window_metrics(P, C, ACT, cols, n, w)
            if m:
                raw[wk][t] = m

    # §3/§21 排名准入：AI 五主题改为 CORE ≥ 3（取代旧「N ≥ 5」）；其余主题沿用「有有效成员即可」；
    # 不达标的主题保留原始指标供观察（§21 保留主题数据，不删除主题）
    def _admit(t):
        mq = mapping[t]
        if mq.get('membership_src') == 'membership_v2':
            return mq['core_n'] >= N_CORE_MIN
        return True

    small_watch = {}
    for wk in windows:
        for t, v in list(raw[wk].items()):
            if not _admit(t):
                small_watch.setdefault(t, {})[wk] = v

    # ── §十五 Percentile Rank → HeatRaw → §十八 Reliability 收缩 ──
    scored = {}
    inflation = {}
    for wk in windows:
        elig = {t: v for t, v in raw[wk].items() if _admit(t)}
        if not elig:
            continue
        names = list(elig.keys())

        def _fill(key):
            """缺失分量以横截面中位数补齐（中性），保持主题仍可排序"""
            arr = [elig[t][key] for t in names]
            good = [x for x in arr if not (isinstance(x, float) and np.isnan(x))]
            med = float(np.median(good)) if good else 0.0
            return np.asarray([med if (isinstance(x, float) and np.isnan(x)) else x for x in arr],
                              dtype=float)

        # §十五 HeatRaw = 0.60×PricePct + 0.30×BreadthPct + 0.10×ActivityPct（不变）
        price_s = pct_rank(_fill('price'))
        bread_s = pct_rank(_fill('breadth'))
        act_s = pct_rank(_fill('activity'))
        heat_raw = W_PRICE * price_s + W_BREADTH * bread_s + W_ACTIVITY * act_s
        mkt_median = float(np.median(heat_raw))               # §十八 MarketMedianHeat

        for i, t in enumerate(names):
            v = elig[t]
            mq = mapping[t]
            # §十六 Reliability = min(1, sqrt(N/50))（旧口径，保留下游兼容）
            rel = min(1.0, (v['n'] / FULL_RELIABILITY_N) ** 0.5)
            # §十八 ReliabilityAdjustedHeat = HeatRaw×Rel + MarketMedianHeat×(1−Rel)
            adj = float(heat_raw[i]) * rel + mkt_median * (1.0 - rel)
            # §十八 Heat = 50%×HeatRaw + 50%×ReliabilityAdjustedHeat（不变，下游消费口径）
            heat = 0.50 * float(heat_raw[i]) + 0.50 * adj
            # §22 AdjustedHeat = RawHeat × sample_reliability × membership_quality
            adj_heat = (float(heat_raw[i]) * mq['sample_reliability']
                        * mq['membership_quality'])
            row = dict(v)
            row.update({'theme': t, 'heat_raw': float(heat_raw[i]), 'reliability': rel,
                        'adj_heat': adj, 'score': float(heat),
                        'adjusted_heat': adj_heat,
                        'sample_reliability': mq['sample_reliability'],
                        'membership_quality': mq['membership_quality'],
                        'core_n': mq['core_n'], 'extension_n': mq['extension_n'],
                        'related_n': mq['related_n'], 'sample_flag': mq['sample_flag'],
                        'price_score': float(price_s[i]),
                        'breadth_score': float(bread_s[i]),
                        'activity_score': float(act_s[i])})
            scored.setdefault(t, {})[wk] = row

        # §二十一 排名：Rank 仍按 Heat（下游口径不变）；另给 rank_adj 按 AdjustedHeat（§22）
        order = sorted(names, key=lambda t: -scored[t][wk]['score'])
        for rk, t in enumerate(order, 1):
            scored[t][wk]['rank'] = rk
        order_adj = sorted(names, key=lambda t: -scored[t][wk]['adjusted_heat'])
        for rk, t in enumerate(order_adj, 1):
            scored[t][wk]['rank_adj'] = rk
        for t in names:
            scored[t][wk]['flags'] = _flags(scored[t][wk], scored[t][wk]['rank'], mapping[t])

        # §三十一 防止评分通胀
        share = sum(1 for t in names if scored[t][wk]['score'] >= 80.0) / len(names)
        if share > INFLATION_SHARE:
            inflation[wk] = share

    # ── 三窗口都可排名的共同样本池（跨周期观察表在此池内定义，避免缺数据歧义）──
    universe = [t for t, v in scored.items() if all(wk in v for wk in windows)]

    report = build_report(trade_date, theme_members, scored, universe, missing_themes,
                          windows, small_watch, mapping, inflation, no_valid, config, mainbiz)
    save_outputs(report, scored, universe, trade_date, small_watch, mapping, inflation)
    print(report['text'])
    return report


# ═════════════════════════ 输出 ═════════════════════════

def _f(x, d=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return '—'
    return f'{x:.{d}f}'


def _dw(s):
    """字符串显示宽度（中文/全角按 2 计），保证表格在等宽与移动端都能对齐"""
    return sum(2 if ('\u1100' <= ch <= '\u115f' or '\u2e80' <= ch <= '\ua4cf'
                     or '\uac00' <= ch <= '\ud7a3' or '\uf900' <= ch <= '\ufaff'
                     or '\ufe30' <= ch <= '\ufe6f' or '\uff00' <= ch <= '\uff60'
                     or '\uffe0' <= ch <= '\uffe6') else 1 for ch in str(s))


def _pad(s, w, align='<'):
    s = str(s)
    gap = ' ' * max(0, w - _dw(s))
    return s + gap if align == '<' else gap + s


def _low_sample(row, mq):
    """§21 低样本判定：AI 五主题（membership_v2）按 CORE 数；其余主题沿用 V2.4 的 N 口径

    这样 §21 的「CORE<5 → LOW_SAMPLE」只作用于新的四级成员体系，
    不会因 34 个既有主题的人工 CORE 名单偏短而大面积误标（保持下游口径稳定）。
    """
    if mq.get('membership_src') == 'membership_v2':
        return mq['core_n'] < LOW_SAMPLE_CORE
    return row['n'] < LOW_SAMPLE_MAX


def _flags(row, rk, mq):
    """§21 低样本保护 + §23 防低样本霸榜 + §二十九 异常检查（纯标记，不参与评分、不扣分）"""
    f = []
    if _low_sample(row, mq):                                # §21 LOW_SAMPLE
        f.append('LOW_SAMPLE')
        if rk <= 3:
            f.append('LOW_SAMPLE_LEADER')                   # §23 低样本霸榜
        if row['heat_raw'] >= LOW_BREADTH_HEAT:
            f.append('HIGH_HEAT_LOW_SAMPLE')                # §23 高热度但低样本
    elif row['n'] < SMALL_SAMPLE_MAX:                       # 旧口径保留：成员总量偏少
        f.append('SMALL_SAMPLE')
    if row['score'] >= LOW_BREADTH_HEAT and row['breadth'] < LOW_BREADTH_MIN:
        f.append('LOW_BREADTH')                         # §二十九 B 低扩散高热度
    if (row['p75'] - row['p50']) >= NARROW_GAP:
        f.append('NARROW_STRONG')                       # §二十九 C 极端上涨集中
    if mq['status'] == 'BAD':                           # §二十九 D + §二十三
        f.append('MAPPING_BAD')
        f.append('DATA_WARNING')
    if mq['count_anomaly']:                             # §二十九 E
        f.append('MEMBER_COUNT_ANOMALY')
    return '|'.join(f)


def _table(scored, universe, wk, ret_label, mapping):
    """§二十一 【TODAY/WEEK/MONTH TOP10】

    Rank 主题 Heat RawHt AdjHt N CORE 涨幅 Breadth UpRatio Act Rel Mapping Status Flags
    · N 与 Breadth 只用 CORE+NORMAL 有效成员（§十三）
    · RawHt = HeatRaw；AdjHt = AdjustedHeat = RawHeat×sample_reliability×membership_quality
      （§22：不改线上 Heat；G11 要求 RawHeat 与 AdjustedHeat 同时展示）
    · CORE = §4 四级体系中 CORE 级成员数；Rank 带 * = 低样本（§21/§23）
    """
    L = []
    L.append(_pad('#', 5) + _pad('主题', 12) + _pad('Heat', 7, '>') + _pad('RawHt', 8, '>')
             + _pad('AdjHt', 8, '>') + _pad('N', 6, '>') + _pad('CORE', 6, '>')
             + _pad(ret_label, 10, '>') + _pad('Breadth', 9, '>') + _pad('UpRatio', 9, '>')
             + _pad('Act', 8, '>') + _pad('Rel', 6, '>') + _pad('Mapping', 9, '>')
             + _pad('Status', 8, '>') + '  Flags')
    sel = sorted([t for t in universe if wk in scored[t]], key=lambda t: scored[t][wk]['rank'])
    for t in sel[:TOP_N]:
        r = scored[t][wk]
        mq = mapping[t]
        low = _low_sample(r, mq)
        rk = f"{r['rank']}{'*' if low else ''}"
        L.append(_pad(rk, 5) + _pad(t, 12) + _pad(_f(r['score'], 1), 7, '>')
                 + _pad(_f(r.get('heat_raw'), 1), 8, '>')
                 + _pad(_f(r.get('adjusted_heat'), 1), 8, '>')
                 + _pad(r['n'], 6, '>') + _pad(r.get('core_n', 0), 6, '>')
                 + _pad(f"{r['price']:+.2f}%", 10, '>')
                 + _pad(f"{r['breadth']*100:.1f}%", 9, '>')
                 + _pad(f"{r['up_ratio']*100:.1f}%", 9, '>')
                 + _pad(f"{r['activity']:.2f}x", 8, '>')
                 + _pad(f"{r['reliability']:.2f}", 6, '>')
                 + _pad(f"{mq['quality']*100:.0f}%", 9, '>')
                 + _pad(mq['status'], 8, '>') + '  ' + r['flags'])
    if not sel:
        L.append('  （无满足样本要求的主题）')
    return L


def _ai5_block(scored, mapping, wk='MONTH'):
    """§33-A/§33-B：AI 五个一级主题的规模表与 AdjustedHeat 排名表

    · A 表：CORE / EXTENSION / RELATED / 总成员 / sample_reliability / membership_quality
    · B 表：按 AdjustedHeat 排序，同时输出 RawHeat / 线上 Heat / CORE / Reliability / Quality / SampleFlag
    """
    keys = [k for k in AI5_KEYS if k in mapping]
    if not keys:
        return []

    def _label(k):
        m = mapping[k]
        return m.get('theme_cn') or k

    L = []
    L.append('【§33-A AI 五主题规模（V2.0 四级成员体系）】')
    L.append(_pad('主题', 14) + _pad('CORE', 6, '>') + _pad('EXT', 6, '>') + _pad('REL', 6, '>')
             + _pad('成员', 6, '>') + _pad('SampleRel', 10, '>') + _pad('Quality', 9, '>')
             + '  SampleFlag')
    for k in keys:
        m = mapping[k]
        L.append(_pad(_label(k), 14) + _pad(m['core_n'], 6, '>')
                 + _pad(m['extension_n'], 6, '>') + _pad(m['related_n'], 6, '>')
                 + _pad(m['core_n'] + m['extension_n'], 6, '>')
                 + _pad(f"{m['sample_reliability']:.4f}", 10, '>')
                 + _pad(f"{m['membership_quality']:.4f}", 9, '>') + '  ' + m['sample_flag'])
    L.append('  §17 RELATED 不进入主题排名；THEME_RANK_POOL = CORE + EXTENSION')
    L.append('')

    rows = [k for k in keys if wk in scored.get(k, {})]
    L.append(f'【§33-B AI 五主题 AdjustedHeat 排名（{wk} 窗口）】')
    if not rows:
        L.append(f'  （{wk} 窗口无 AI 五主题可排名样本）')
        return L
    rows.sort(key=lambda k: -scored[k][wk]['adjusted_heat'])
    L.append(_pad('RankAdj', 8) + _pad('Rank', 6, '>') + _pad('主题', 14)
             + _pad('RawHeat', 9, '>') + _pad('AdjHeat', 9, '>') + _pad('Heat', 7, '>')
             + _pad('CORE', 6, '>') + _pad('Rel', 7, '>') + _pad('Quality', 9, '>')
             + '  SampleFlag')
    for i, k in enumerate(rows, 1):
        d, m = scored[k][wk], mapping[k]
        L.append(_pad(i, 8) + _pad(d['rank'], 6, '>') + _pad(_label(k), 14)
                 + _pad(_f(d['heat_raw'], 1), 9, '>') + _pad(_f(d['adjusted_heat'], 1), 9, '>')
                 + _pad(_f(d['score'], 1), 7, '>') + _pad(m['core_n'], 6, '>')
                 + _pad(f"{d['sample_reliability']:.3f}", 7, '>')
                 + _pad(f"{d['membership_quality']:.3f}", 9, '>') + '  ' + m['sample_flag'])
    L.append('  RawHeat = 线上 HeatRaw（§22 不改 RawHeat）；'
             'AdjHeat = RawHeat × sample_reliability × membership_quality')
    L.append('  线上 Rank/Heat 口径保持不变（下游兼容）；RankAdj 为 V2.0 建议排名主键')
    for k in rows:                                            # §23 高热度但低样本
        m = mapping[k]
        if m['sample_flag'] != 'NORMAL':
            d = scored[k][wk]
            L.append(f"  §23 提示：{_label(k)} 高热度但低样本"
                     f"（RawHeat {d['heat_raw']:.1f} / AdjHeat {d['adjusted_heat']:.1f} / "
                     f"CORE {m['core_n']} / {m['sample_flag']}），不得直接判为最强主题")
    return L



def _answer(scored, universe, wk, mapping):
    """§二十四 该窗口的三个核心答案之一（严格字段顺序）"""
    sel = sorted([t for t in universe if wk in scored[t]], key=lambda t: scored[t][wk]['rank'])
    if not sel:
        return ['  DATA_INSUFFICIENT（该窗口无满足样本要求的主题）']
    L = []
    top = sel[0]
    if _low_sample(scored[top][wk], mapping[top]):      # §二十一/§二十三 反作弊
        d = scored[top][wk]
        L.append(f"  小样本高热度（#{d['rank']}*）：{top}  Heat {d['score']:.1f}"
                 f"  CORE {d.get('core_n', 0)}  N {d['n']}"
                 f"  LOW_SAMPLE，非可靠最强")
        sel = [t for t in sel if not _low_sample(scored[t][wk], mapping[t])]
    if not sel:
        L.append('  DATA_INSUFFICIENT（无满足样本要求的有效主题，不作「最强主题」结论）')
        return L
    t = sel[0]
    d, mq = scored[t][wk], mapping[t]
    L.append(f"  主题：{t}")
    L.append(f"  Heat：{d['score']:.1f}")
    L.append(f"  RawHeat：{d['heat_raw']:.1f}")
    L.append(f"  AdjustedHeat：{d['adjusted_heat']:.1f}")
    L.append(f"  N：{d['n']}")
    L.append(f"  CORE：{mq['core_n']}（EXTENSION {mq['extension_n']} / RELATED {mq['related_n']}）")
    L.append(f"  sample_reliability：{mq['sample_reliability']:.4f}")
    L.append(f"  membership_quality：{mq['membership_quality']:.4f}")
    L.append(f"  Breadth：{d['breadth']*100:.1f}%")
    L.append(f"  Price：{d['price']:+.2f}%")
    L.append(f"  MappingQuality：{mq['quality']*100:.1f}%（MAPPING_{mq['status']}）")
    L.append(f"  状态：{mq['status']}")
    if mq['status'] == 'BAD':                           # §二十三
        L.append(f"  DATA_WARNING：成份股映射质量 <{MAP_WARN:.0%}，不作为高置信度最强主题")
    return L


def build_report(trade_date, theme_members, scored, universe, missing_themes,
                 windows, small_watch, mapping, inflation, no_valid, config, mainbiz):
    L = []
    L.append(SEP_FULL)
    L.append('主题热度 V2.4')
    L.append(f'交易日 {trade_date}')
    L.append(SEP_FULL)
    L.append('* 本模块只回答：今日/本周/本月最强主题 + 成份股是否真的属于该主题；不含任何交易决策。')
    L.append(f'* HeatRaw = Price {W_PRICE:.0%} + Breadth {W_BREADTH:.0%} + Activity {W_ACTIVITY:.0%}；'
             f'PriceRaw = {W_P50:.0%}×P50 + {W_P75:.0%}×P75（成员收益率 clip±{RET_CLIP:.0f}%）。')
    L.append('* Breadth / N 只用 CORE+NORMAL 有效成员；WEAK 成员不参与热度，只进入映射审计。')
    L.append(f'* Reliability = min(1, √(N/{FULL_RELIABILITY_N}))；'
             'Heat = 50%×HeatRaw + 50%×(HeatRaw×Rel + MarketMedianHeat×(1−Rel))。')
    L.append(f'* N<{LOW_SAMPLE_MAX} → LOW_SAMPLE，Rank 加 *，不进入「最强主题」结论；'
             f'{LOW_SAMPLE_MAX}≤N<{SMALL_SAMPLE_MAX} → SMALL_SAMPLE。')
    L.append(f'* 【V2.0】AI 五主题改用四级成员体系：CORE≥{N_CORE_MIN} 才进入排名，'
             f'CORE<{LOW_SAMPLE_CORE} → LOW_SAMPLE；'
             f'AdjustedHeat = RawHeat × sample_reliability × membership_quality；'
             f'线上 Heat/Rank 口径不变，RankAdj 为建议排名主键（§18/§21/§22）。')
    L.append('* 映射质量只报告、绝不自动改写 theme_config.json / subtheme_map.json。')
    L.append('')

    # ── §三十五【数据质量】──
    ls_themes = sorted({t for wk in windows for t, v in scored.items()
                        if wk in v and _low_sample(v[wk], mapping[t])})
    L.append('【数据质量】')
    L.append(f'主题数：{len(theme_members)}')
    L.append(f'有效主题：{len(scored)}（至少一个窗口满足排名准入：'
             f'AI 五主题 CORE≥{N_CORE_MIN} / 其余主题 N≥{MIN_SAMPLE}）')
    L.append(f'LOW_SAMPLE：{len(ls_themes)}（AI 五主题 CORE<{LOW_SAMPLE_CORE}；'
             f'其余主题 N<{LOW_SAMPLE_MAX}）')
    L.append(f'未入榜：{len(small_watch)} 个主题所有窗口不满足准入；'
             f'{len(no_valid)} 个主题无 CORE+EXTENSION 排名池成员')
    L.append(f'Mapping WARN：{sum(1 for m in mapping.values() if m["status"] == "WARN")}')
    L.append(f'Mapping BAD：{sum(1 for m in mapping.values() if m["status"] == "BAD")}')
    L.append(f'成员异常：{sum(1 for m in mapping.values() if m["count_anomaly"])}'
             f'（MEMBER_COUNT_ANOMALY；仅报告，不改原始成份股）')
    if missing_themes:
        L.append(f'无日线数据的主题：{"、".join(missing_themes)}')
    for wk, label in (('TODAY', 'Today'), ('WEEK', 'Week'), ('MONTH', 'Month')):
        heats = [scored[t][wk]['score'] for t in scored if wk in scored[t]]
        st = dist_stats(heats)
        share = (sum(1 for h in heats if h >= 80.0) / len(heats)) if heats else 0.0
        L.append(f'  {label}Heat 分布：P50 {_f(st["P50"],1)}｜P90 {_f(st["P90"],1)}｜'
                 f'MAX {_f(st["MAX"],1)}｜≥80 占比 {share*100:.0f}%')
        if wk in inflation:
            L.append(f'    HEAT_INFLATION_WARNING：{label} Heat≥80 的主题占比 '
                     f'{inflation[wk]*100:.0f}% > {INFLATION_SHARE:.0%}，Percentile 或缩放可能异常')
    L.append('')

    # ── §33-A/§33-B AI 五主题专项（V2.0 四级成员体系 + AdjustedHeat 排名）──
    ai5 = _ai5_block(scored, mapping)
    if ai5:
        L += ai5
        L.append('')

    # ── §二十一 三个核心榜单 ──
    for wk, label, ret in (('TODAY', '【TODAY TOP10】', '涨幅'),
                           ('WEEK', '【WEEK TOP10】', '5D'),
                           ('MONTH', '【MONTH TOP10】', '20D')):
        L.append(label)
        L += _table(scored, universe, wk, ret, mapping)
        L.append('')

    # ── §二十四 三个核心答案 ──
    L.append('【三个核心答案】')
    for wk, label in (('TODAY', '今日最强'), ('WEEK', '本周最强'), ('MONTH', '本月最强')):
        L.append(f'{label}：')
        L += _answer(scored, universe, wk, mapping)
    L.append('')

    # ── §二十五~§二十八 跨周期标签：只依据三个 Rank，不重新计算综合分 ──
    def _rk(t, wk):
        return scored[t][wk]['rank']

    def _ok(t, *wks):
        """§21 LOW_SAMPLE 不得进入「最强主题」结论 → 标签块只在有效样本上成立"""
        return all(not _low_sample(scored[t][w], mapping[t]) for w in wks)

    def _block(title, rule, pred, sort_key):
        L.append(f'【{title}】')
        L.append(rule)
        L.append(SEP_THIN)
        sel = sorted([t for t in universe if pred(t)], key=sort_key)
        for t in sel:
            L.append(f"  {t}（今日 #{_rk(t, 'TODAY')} / 本周 #{_rk(t, 'WEEK')}"
                     f" / 本月 #{_rk(t, 'MONTH')}）")
        if not sel:
            L.append('  （无）')
        L.append('')

    _block('SUSTAINED STRONG', f'Today ≤{TOP_N} AND Week ≤{TOP_N} AND Month ≤{TOP_N}',
           lambda t: (max(_rk(t, 'TODAY'), _rk(t, 'WEEK'), _rk(t, 'MONTH')) <= TOP_N
                      and _ok(t, 'TODAY', 'WEEK', 'MONTH')),
           lambda t: _rk(t, 'TODAY') + _rk(t, 'WEEK') + _rk(t, 'MONTH'))

    _block('HOT TODAY', f'Today ≤{TOP_N} AND Week >{MID_N}',
           lambda t: (_rk(t, 'TODAY') <= TOP_N and _rk(t, 'WEEK') > MID_N
                      and _ok(t, 'TODAY')),
           lambda t: _rk(t, 'TODAY'))

    _block('HOT WEEK', f'Week ≤{TOP_N} AND Month >{MID_N}',
           lambda t: (_rk(t, 'WEEK') <= TOP_N and _rk(t, 'MONTH') > MID_N
                      and _ok(t, 'WEEK')),
           lambda t: _rk(t, 'WEEK'))

    _block('COOLING', f'Month ≤{TOP_N} AND Week >{MID_N} AND Today >{CROSS_N}（仅描述热度下降）',
           lambda t: (_rk(t, 'MONTH') <= TOP_N and _rk(t, 'WEEK') > MID_N
                      and _rk(t, 'TODAY') > CROSS_N),
           lambda t: _rk(t, 'MONTH'))

    # ── §三十二 成份股映射审计 + §三十三 历史污染股专项审计 ──
    L.append('【成份股映射审计】')
    L.append(f'抽样规则：三个窗口 TOP{TOP_N} 各抽 {AUDIT_SAMPLE} 只（CORE→NORMAL→WEAK 排序后等间隔固定抽样）；'
             '归属依据 = 映射来源 via + 主营关键词命中（§三十二）')
    L.append('WEAK 里标注「无主营文本」的属于数据缺口（缺主营记录），需人工确认，不等于已判定无关。')
    L.append(SEP_THIN)
    audited = []
    for wk, label in (('TODAY', 'TODAY'), ('WEEK', 'WEEK'), ('MONTH', 'MONTH')):
        sel = sorted([t for t in universe if wk in scored[t]],
                     key=lambda t: scored[t][wk]['rank'])[:TOP_N]
        for t in sel:
            if t in audited:
                continue
            audited.append(t)
            mq = mapping[t]
            L.append(f'▸ {label} #{scored[t][wk]["rank"]} {t}｜MappingQuality '
                     f'{mq["quality"]*100:.1f}%｜{mq["status"]}｜成员 {mq["total"]}'
                     f'（CORE {mq["core"]} / NORMAL {mq["normal"]} / WEAK {mq["weak"]}）')
            for c, v in sample_members(mq):
                L.append(f'    {_pad(v["name"], 10)} {c}  {_pad(v["layer"], 8)} {v["why"]}')
            weak = [c for c, v in mq['layers'].items() if v['layer'] == 'WEAK']
            if weak:
                names = '、'.join(f'{mq["layers"][c]["name"]}({c})' for c in weak[:8])
                L.append(f'    需人工确认（WEAK 成员共 {len(weak)} 只）：{names}'
                         f'{"…" if len(weak) > 8 else ""}')
            if mq['cross_pollution']:
                L.append(f'    CROSS_THEME_POLLUTION：{mq["cross_pollution"]} 只成员同时被 '
                         f'≥{CROSS_THEME_MIN} 个主题纳入')
            if mq['count_anomaly']:
                L.append(f'    MEMBER_COUNT_ANOMALY：上一交易日 {mq["prev_total"]} → 本次 '
                         f'{mq["total"]}')
    L.append('')

    L.append('【历史污染股专项审计】')
    L.append('§三十三：以主营业务 / 主题定义 / 官方概念成员重新判断，只报告不修改配置')
    L.append(SEP_THIN)
    for code, name in AUDIT_STOCKS.items():
        hit = [(t, mq['layers'][code]) for t, mq in mapping.items() if code in mq['layers']]
        if not hit:
            L.append(f'  {name} {code}：本次映射未纳入任何主题')
            continue
        txt = mainbiz.get(code, '')
        for t, v in hit:
            cfg = config.get(t) or {}
            if not txt:
                mb = '主营文本缺失'
            else:
                ev = []
                for k in ('core_keywords', 'industry_keywords', 'keywords', 'product_keywords',
                          'brand_keywords', 'concept_keywords', 'industry_chains'):
                    ev += _hits(txt, cfg.get(k))
                mb = ('主营命中 ' + ev[0]) if ev else '主营文本未见主题关键词'
            conflict = v['layer'] in ('CORE', 'NORMAL') and mb == '主营文本未见主题关键词'
            adv = '建议人工确认（人工名单与主营证据不一致）' if conflict else mapping_advice(v)
            note = PENDING_REVIEW.get((code, t))
            if note:
                adv = f'{adv}｜{note}'
            L.append(f'  {name} {code}｜{t}｜{v["layer"]}｜{v["why"]}｜{mb}｜{adv}')
    L.append('')

    # ── §三十四 只报告：需要人工复核的主题清单 ──
    review = []
    for t in sorted(mapping, key=lambda x: mapping[x]['quality']):
        mq = mapping[t]
        if mq['status'] == 'BAD' or mq['count_anomaly'] or mq['weak'] / max(1, mq['total']) > 0.30:
            review.append(t)
    L.append('【MAPPING REVIEW REQUIRED】')
    L.append(SEP_THIN)
    if review:
        for t in review:
            mq = mapping[t]
            L.append(f'  {t}｜MappingQuality {mq["quality"]*100:.1f}%｜{mq["status"]}'
                     f'｜成员 {mq["total"]}（CORE {mq["core"]} / NORMAL {mq["normal"]} / '
                     f'WEAK {mq["weak"]}）｜建议人工复核成员池')
        L.append('  结论：仅报告；未修改 theme_config.json / subtheme_map.json，'
                 '「建议移除 / 建议保留 / 建议人工确认」由人工决定。')
    else:
        L.append('  （无）')
    L.append(SEP_FULL)
    return {'text': '\n'.join(L), 'trade_date': trade_date,
            'universe': universe, 'windows': windows}


WINDOWS_ORDER = ('TODAY', 'WEEK', 'MONTH')
# §二十二 每窗口固定字段（v24 版：base_heat → heat_raw，并新增 adj_heat）
# V2.0 §18/§22：新增 rank_adj（按 AdjustedHeat 的排名）+ adjusted_heat
CSV_FIELDS = ('rank', 'rank_adj', 'heat', 'heat_raw', 'adj_heat', 'adjusted_heat',
              'reliability', 'price', 'p25', 'p50', 'p75', 'breadth', 'up_ratio',
              'activity', 'n', 'flags')
# §二十二 主题级「成份股质量」列（MappingQuality / MappingStatus 等）
# V2.0 §18/§19/§20/§21：新增四级成员计数 + membership_quality/sample_reliability/sample_flag
THEME_FIELDS = ('mapping_quality', 'mapping_status', 'n_map', 'n_core', 'n_normal', 'n_weak',
                'n_valid', 'cross_pollution', 'count_anomaly', 'core_n', 'extension_n',
                'related_n', 'membership_quality', 'sample_reliability', 'sample_flag',
                'membership_src')


def _csv_row(t, in_universe):
    r = {'theme': t, 'in_universe': in_universe}
    for k in THEME_FIELDS:
        r[k] = None
    for wk in WINDOWS_ORDER:
        pre = wk.lower()
        for k in CSV_FIELDS:
            r[f'{pre}_{k}'] = None
    return r


def _fill_theme_cols(r, t, mapping):
    mq = mapping[t]
    r['mapping_quality'] = round(mq['quality'] * 100, 2)
    r['mapping_status'] = mq['status']
    r['n_map'] = mq['total']
    r['n_core'] = mq['core']
    r['n_normal'] = mq['normal']
    r['n_weak'] = mq['weak']
    r['n_valid'] = mq['valid']
    r['cross_pollution'] = mq['cross_pollution']
    r['count_anomaly'] = int(bool(mq['count_anomaly']))
    r['core_n'] = mq['core_n']
    r['extension_n'] = mq['extension_n']
    r['related_n'] = mq['related_n']
    r['membership_quality'] = round(mq['membership_quality'], 4)
    r['sample_reliability'] = round(mq['sample_reliability'], 4)
    r['sample_flag'] = mq['sample_flag']
    r['membership_src'] = mq.get('membership_src', '')


def _fill_window_cols(r, pre, d, flags=None):
    r[f'{pre}_price'] = round(d['price'], 3)
    r[f'{pre}_p25'] = round(d['p25'], 3)
    r[f'{pre}_p50'] = round(d['p50'], 3)
    r[f'{pre}_p75'] = round(d['p75'], 3)
    r[f'{pre}_breadth'] = round(d['breadth'] * 100, 2)
    r[f'{pre}_up_ratio'] = round(d['up_ratio'] * 100, 2)
    r[f'{pre}_activity'] = None if np.isnan(d['activity']) else round(d['activity'], 3)
    r[f'{pre}_n'] = d['n']
    if flags is not None:
        r[f'{pre}_flags'] = flags


def save_outputs(report, scored, universe, trade_date, small_watch, mapping, inflation):
    os.makedirs(REPORT_DIR, exist_ok=True)
    base = os.path.join(REPORT_DIR, f'theme_heat_v24_{trade_date}')
    with open(base + '.md', 'w', encoding='utf-8') as f:
        f.write(report['text'] + '\n')

    rows = []
    order = sorted(scored, key=lambda x: (scored[x]['TODAY']['rank'] if 'TODAY' in scored[x] else 9999))
    for t in order:
        r = _csv_row(t, t in universe)
        _fill_theme_cols(r, t, mapping)
        for wk in WINDOWS_ORDER:
            pre = wk.lower()
            d = scored[t].get(wk)
            if d:
                r[f'{pre}_rank'] = d['rank']
                r[f'{pre}_rank_adj'] = d.get('rank_adj')
                r[f'{pre}_heat'] = round(d['score'], 2)
                r[f'{pre}_heat_raw'] = round(d['heat_raw'], 2)
                r[f'{pre}_adj_heat'] = round(d['adj_heat'], 2)
                r[f'{pre}_adjusted_heat'] = round(d['adjusted_heat'], 2)
                r[f'{pre}_reliability'] = round(d['reliability'], 3)
                _fill_window_cols(r, pre, d, d['flags'])
            elif wk in small_watch.get(t, {}):
                # 该窗口不满足排名准入（AI 五主题 CORE<3 / 其余主题 N<5）：仅留档
                _fill_window_cols(r, pre, small_watch[t][wk],
                                  mapping[t].get('sample_flag') or 'LOW_SAMPLE')
        rows.append(r)
    for t in small_watch:                       # 所有窗口均不满足准入：仅观察行，rank/heat 留空
        if t in scored:
            continue
        r = _csv_row(t, False)
        _fill_theme_cols(r, t, mapping)
        for wk in WINDOWS_ORDER:
            d = small_watch[t].get(wk)
            if d:
                _fill_window_cols(r, wk.lower(), d,
                                  mapping[t].get('sample_flag') or 'LOW_SAMPLE')
        rows.append(r)
    for t in scoring_skipped(scored, small_watch, mapping):
        r = _csv_row(t, False)
        _fill_theme_cols(r, t, mapping)
        rows.append(r)
    pd.DataFrame(rows).to_csv(base + '.csv', index=False, encoding='utf-8-sig')
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n[保存] {os.path.basename(base)}.md / .csv / .json")


def scoring_skipped(scored, small_watch, mapping):
    """无 CORE+NORMAL 成员（或完全无数据）的主题：仍落盘映射质量，供审计"""
    return [t for t in mapping if t not in scored and t not in small_watch]


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(arg)


if __name__ == '__main__':
    main()
