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
import time

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

# ── §V2.5 主题演变引擎（Theme Evolution Engine）常量 ──
# V2.5 不修改主题定义、成份股与热度基础计算（§一/§二），只在已有热度序列上做「变化」分析。
EVO_SERIES_DAYS = 60             # §三 序列保留交易日数（至少 60，最好 120）
EVO_LOOKBACK = 20                # §四~§八 演变回溯窗口（HM20 / Persistence 窗口）
EVO_TOP5, EVO_TOP10, EVO_TOP20 = 5, 10, 20   # §八 Persistence 的三个名次门槛
EVO_STRONG_RANK = 10             # §九 Rank ≤ 10 → 视为「靠前 / 强势区」
EVO_COOL_RANK = 15               # §九 Rank ≥ 15 → 视为「已降温」
EVO_HM_EPS = 0.5                 # §四 |HM| ≤ 0.5 → 「基本持平」噪声带
EVO_HM_ACCEL = 5.0               # §五 HM5 ≥ 5.0 → 「明显升温」（加速判定）
EVO_BREADTH_EPS = 0.5            # §六 |ΔBreadth| ≤ 0.5pp → 「持平」
EVO_PERSIST_HI = 8               # §八 近20日 ≥8 天处于 TOP10 → 已在强势区且具备持续性
EVO_PERSIST_LO = 2               # §八 近20日 ≤2 天处于 TOP10 → 低持续性
EVO_STREAK_MIN = 3               # §八 连续 TOP10 天数 ≥3 → 视为已站稳强势区
EVO_LOWSAMPLE_N = 15             # §十 N < 15 → LOW_SAMPLE（V2.5 更严格口径）
EVO_REL_MIN = 0.50               # §十 reliability < 0.50 → LOW_SAMPLE
EVO_HIST_MIN = EVO_LOOKBACK + 1  # §三/§四 序列少于该天数 → EVOLUTION_INSUFFICIENT
EVO_MIN_TRANS_N = 8              # §十二 状态迁移最小样本量（不足 → 只作观察信号）
EVO_PERTURB = (0.8, 1.2)         # §十二 参数扰动系数（阈值缩放，检验状态稳定性）
EVO_HORIZONS = (5, 10, 20)       # §十一 未来 T+5 / T+10 / T+20

# 序列落盘（长表：一行 = 一个主题一个交易日）
SERIES_PATH = os.path.join(REPORT_DIR, 'theme_heat_series.csv')
SERIES_FIELDS = ('date', 'theme', 'heat', 'base_heat', 'reliability', 'rank',
                 'breadth', 'up_ratio', 'activity', 'n', 'flags')

SCHEMA_VERSION = 'V2.5.1'         # 清洗版：只统一口径，不新增因子
EVO_RELAUNCH_STREAK = 2          # 再启动确认：连续 TOP10 天数 ≥2
EVO_RELAUNCH_PERSIST = 5         # 再启动确认：近20日 TOP10 天数 ≥5

# §九 生命周期（底层英文状态；中文+Emoji 只用于展示层）
# V2.5.1：六状态「定义」不变，「二次启动」拆为 RELAUNCH（真正再启动）/ REBOUND（反弹）→ 共 7 态
STATE_INFO = {
    'LEADING':      ('当前主线',   '🔥'),
    'ACCELERATING': ('加速升温',   '🚀'),
    'RELAUNCH':     ('真正再启动', '🔄'),
    'REBOUND':      ('反弹',       '↩️'),
    'EMERGING':     ('新出现',     '🌱'),
    'PEAKING':      ('高位钝化',   '⚠️'),
    'COOLING':      ('降温',       '❄️'),
}
STATE_ORDER = ('LEADING', 'ACCELERATING', 'RELAUNCH', 'REBOUND',
               'EMERGING', 'PEAKING', 'COOLING')

# §九 标准解释（只用已计算出来的数据，不含任何预测性断言）
STATE_DESC = {
    'LEADING':      '目前仍处于市场强势区域，而且已经持续了一段时间；不过强势并不等于未来一定继续上涨，需要继续观察热度是否维持。',
    'ACCELERATING': '近期热度明显上升、排名持续改善，上涨范围也在扩大；目前更像是「正在升温」，而不是已经完全确认的主线。',
    'RELAUNCH':     '此前经历过一轮热度下降，最近重新升温、重新回到强势区，并且上涨范围同步扩大；属于结构与扩散都得到确认的再启动。',
    'REBOUND':      '此前经历过一轮热度下降，最近虽然重新升温，但要么还没回到强势区，要么上涨范围没有同步扩大；目前更像反弹，还不能当作真正的再启动。',
    'EMERGING':     '近期开始受到资金关注，热度和参与股票数量都有改善，但历史持续性还不足，需要继续观察。',
    'PEAKING':      '目前排名仍然靠前，但最近的升温速度已经明显放缓，内部扩散也开始减弱；重点不是立即判断见顶，而是观察它能否重新恢复升温。',
    'COOLING':      '近期热度、排名和内部参与度都在下降，说明市场关注度正在减弱。',
}

# 「边际状态」：只看最近 5 日的方向，与主题当前处于哪个层级无关（↗转强 / →持平 / ↘转弱）
MARGINAL_INFO = {'IMPROVING': ('转强', '↗'),
                 'FLAT': ('持平', '→'),
                 'DETERIORATING': ('转弱', '↘')}

# 短期（约5个交易日）/ 中期（约20个交易日）各自独立的状态倾向，不做 T+5→T+10→T+20 串联
NEXT_STATE = {
    'LEADING':      {'up': 'LEADING',      'flat': 'LEADING',      'down': 'PEAKING'},
    'ACCELERATING': {'up': 'LEADING',      'flat': 'ACCELERATING', 'down': 'PEAKING'},
    'RELAUNCH':     {'up': 'LEADING',      'flat': 'RELAUNCH',     'down': 'COOLING'},
    'REBOUND':      {'up': 'RELAUNCH',     'flat': 'REBOUND',      'down': 'COOLING'},
    'EMERGING':     {'up': 'ACCELERATING', 'flat': 'EMERGING',     'down': 'COOLING'},
    'PEAKING':      {'up': 'LEADING',      'flat': 'PEAKING',      'down': 'COOLING'},
    'COOLING':      {'up': 'REBOUND',      'flat': 'COOLING',      'down': 'COOLING'},
}

# 自然语言四段之「确认条件 / 失效条件」：短期（约5个交易日）与中期（约20个交易日）严格分开
CONFIRM_INVALID = {
    'LEADING': (
        '未来约5个交易日，热度继续上升（近5日热度维持为正）且上涨股票数量继续扩大 → 主线延续得到确认。',
        '未来约5个交易日，热度转为明显下降、或参与上涨的股票数量开始收缩 → 主线延续的判断失效。',
        '未来约20个交易日，排名保持在前十、且近20日处于前十的天数继续增加 → 中期主线地位巩固。',
        '未来约20个交易日，排名持续下滑并跌出强势区、参与度同步收缩 → 中期主线地位失效。'),
    'ACCELERATING': (
        '未来约5个交易日，热度继续加速上升且上涨股票数量继续扩大 → 加速升温得到确认。',
        '未来约5个交易日，热度回落或上涨股票数量不再扩大 → 加速升温未能延续。',
        '未来约20个交易日，排名进入并站稳前十、持续性天数增加 → 向主线转化得到确认。',
        '未来约20个交易日，仍未进入强势区且热度回落 → 加速升温失效。'),
    'RELAUNCH': (
        '未来约5个交易日，热度继续上升、上涨股票数量继续扩大 → 再启动得到确认。',
        '未来约5个交易日，热度回落或上涨股票数量收缩 → 本次再启动不成立。',
        '未来约20个交易日，排名维持或重回前十、持续性天数增加 → 第二轮行情成立。',
        '未来约20个交易日，排名重新跌出强势区、扩散收缩 → 第二轮行情不成立。'),
    'REBOUND': (
        '未来约5个交易日，热度继续上升且上涨股票数量开始扩大 → 反弹有望升级为再启动。',
        '未来约5个交易日，仅少数股票上涨、上涨股票数量没有扩大 → 仍是反弹，未升级为再启动。',
        '未来约20个交易日，排名回到前十并保持 → 反弹升级为再启动。',
        '未来约20个交易日，排名未能回到强势区、热度重新回落 → 反弹结束。'),
    'EMERGING': (
        '未来约5个交易日，热度与上涨股票数量继续改善 → 本次升温得到确认。',
        '未来约5个交易日，热度或上涨股票数量回落 → 本次升温未能形成。',
        '未来约20个交易日，排名进入前十并具备持续性 → 由新出现转为强势方向。',
        '未来约20个交易日，热度回落且未进入强势区 → 新出现未能延续。'),
    'PEAKING': (
        '未来约5个交易日，热度重新加速、上涨股票数量重新扩大 → 高位钝化被修复。',
        '未来约5个交易日，热度继续走弱、上涨股票数量继续收缩 → 钝化转为降温。',
        '未来约20个交易日，排名维持在前十且热度重新上行 → 中期强势延续。',
        '未来约20个交易日，排名跌出前十并持续下滑 → 中期转入降温。'),
    'COOLING': (
        '未来约5个交易日，热度止跌回升且上涨股票数量改善 → 出现重新活跃迹象。',
        '未来约5个交易日，热度继续下降 → 降温仍在延续。',
        '未来约20个交易日，热度与排名同步回升并重回强势区 → 可能形成二次启动。',
        '未来约20个交易日，热度与排名继续下降 → 降温过程延续。'),
}

# §十六 禁止 AI 使用的表达（自然语言层生成后校验；模板本身即规避）
BANNED_PHRASES = ('必涨', '必跌', '确定成为主线', '下个月一定爆发', '资金已经全面进场',
                  '主力正在布局', '庄家吸筹', '即将起飞', '板块见顶', '绝对安全',
                  '最佳板块', '最值得买')

# V2.5.1 落盘 Schema（所有主题共用；save_evolution 逐行强校验，缺一即报错）
EVO_FIELDS = (
    'schema_version', 'theme', 'state', 'state_cn', 'emoji',
    'marginal', 'marginal_cn',
    'heat', 'base_heat', 'hm5', 'hm10', 'hm20', 'acc',
    'breadth', 'breadth_d5', 'breadth_d20',
    'rank_today', 'rank_5d', 'rank_10d', 'rank_20d', 'rank_gain20', 'rank_pool_today',
    'top5_count_20d', 'top10_count_20d', 'top20_count_20d', 'top10_streak', 'days_20d',
    'reliability', 'n', 'history_ok', 'flags',
    'outlook_short_dir', 'outlook_short_state', 'outlook_long_dir', 'outlook_long_state',
    'status', 'evidence', 'confirm_short', 'invalid_short', 'confirm_long', 'invalid_long',
    'radar')


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

def compute_core(trade_date=None, verbose=True, df_override=None):
    """V2.4 热度核心计算（不产出报告，不落盘）

    计算逻辑与 V2.4 完全一致（§二：V2.5 不修改主题定义、成份股与热度基础计算），
    只是把中间结果整体返回，供 run() 与 V2.5 序列回补/演变复用。

    df_override：已预取的日线长表（覆盖该日窗口即可）。回补序列时整段一次预取、
    逐日切片，避免每日重复的「逐代码 SQL + 缺口预取」，结果与逐日单独取数完全一致
    （build_matrices 会按当日窗口日期过滤，Activity 的 20 日回看仍在窗口内）。
    """
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
    if verbose:
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
        if verbose:
            print('[V2.4] DATA_INSUFFICIENT：排名池为空，无法计算热度')
        return None

    df = df_override if df_override is not None else fetch_kline(all_codes, dates[0], dates[-1])
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

    return {'trade_date': trade_date, 'dates': dates, 'n_days': n,
            'theme_members': theme_members, 'scored': scored, 'universe': universe,
            'missing_themes': missing_themes, 'windows': windows,
            'small_watch': small_watch, 'mapping': mapping, 'inflation': inflation,
            'no_valid': no_valid, 'config': config, 'mainbiz': mainbiz,
            'map_path': map_path}


def run(trade_date=None):
    """V2.4 报告 + V2.5 演变报告（共用同一份热度核心；V2.4 口径与产物完全不变）"""
    core = compute_core(trade_date, verbose=True)
    if core is None:
        return None
    report = build_report(core['trade_date'], core['theme_members'], core['scored'],
                          core['universe'], core['missing_themes'], core['windows'],
                          core['small_watch'], core['mapping'], core['inflation'],
                          core['no_valid'], core['config'], core['mainbiz'])
    save_outputs(report, core['scored'], core['universe'], core['trade_date'],
                 core['small_watch'], core['mapping'], core['inflation'])
    print(report['text'])
    run_evolution(core)                       # §V2.5 主题演变 + 自然语言解释层
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


# ═════════════════════════ §V2.5 主题演变引擎 ═════════════════════════
#
# 计算与解释严格分离（§二）：
#   计算层：HM5/HM10/HM20、ACC、Breadth 变化、Rank 迁移、Persistence、
#          Lifecycle State、Historical Transition —— 全部由本模块算出并落盘；
#   解释层：narrate_* 只把已算出的字段翻译成自然语言，不新增任何数据、
#          不修改主题状态、不预测涨跌幅/收益率（§十一/§十四/§十五）。

def _val(seq, k):
    """序列倒数第 k+1 个元素（k=0 即最新）；越界 / None / NaN → None"""
    if seq is None or len(seq) <= k:
        return None
    x = seq[-1 - k]
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _delta(seq, a, b):
    """seq[今-a] − seq[今-b]；任一缺失 → None"""
    x, y = _val(seq, a), _val(seq, b)
    return None if (x is None or y is None) else x - y


def _snapshot_row(date, theme, d):
    """当日 TODAY 指标 → 序列长表一行（§三 的 11 个字段）"""
    act = d['activity']
    act = None if (isinstance(act, float) and np.isnan(act)) else round(float(act), 4)
    return {'date': date, 'theme': theme,
            'heat': round(float(d['score']), 4),
            'base_heat': round(float(d['heat_raw']), 4),
            'reliability': round(float(d['reliability']), 4),
            'rank': int(d['rank']),
            'breadth': round(float(d['breadth']) * 100.0, 4),
            'up_ratio': round(float(d['up_ratio']) * 100.0, 4),
            'activity': act, 'n': int(d['n']),
            'flags': str(d.get('flags') or '')}


def _series_load():
    """读取序列 CSV → {(date, theme): row}；缺失/损坏 → {}"""
    if not os.path.exists(SERIES_PATH):
        return {}
    try:
        df = pd.read_csv(SERIES_PATH, dtype={'date': str})
    except Exception:
        return {}
    out = {}
    for r in df.to_dict('records'):
        d, t = str(r.get('date') or ''), str(r.get('theme') or '')
        if d and t:
            out[(d, t)] = r
    return out


def _series_save(store):
    """整表重写（按 date, theme 排序；同日重跑幂等覆盖）"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    rows = [store[k] for k in sorted(store)]
    pd.DataFrame(rows, columns=list(SERIES_FIELDS)).to_csv(
        SERIES_PATH, index=False, encoding='utf-8-sig')
    return len(rows)


def series_append(core):
    """§三 把当日 TODAY 快照写入时间序列（同日期覆盖）"""
    td, store = core['trade_date'], _series_load()
    for t, v in core['scored'].items():
        d = v.get('TODAY')
        if d:
            store[(td, t)] = _snapshot_row(td, t, d)
    _series_save(store)
    return store


def backfill_series(n_days=EVO_SERIES_DAYS, end_date=None):
    """§三 回补主题热度序列：逐日重跑 V2.4 热度核心，只落盘 TODAY 快照（不出报告）

    只使用本地 daily_cache；不使用任何未来数据（每日快照仅依赖 ≤ 当日的数据）。
    性能：整段区间只做一次日线预取（覆盖最早一日窗口的起点 ~ 最后一个交易日），
    之后逐日切片计算，避免逐日重复的「逐代码 SQL + 缺口预取」。
    """
    from stock_cache import get_recent_trade_dates
    dates = [str(d) for d in get_recent_trade_dates(n=n_days, end_date=end_date)]
    if not dates:
        raise SystemExit('daily_cache 无交易日数据，无法回补序列')
    print(f"[V2.5 回补] 目标 {len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}")

    # ── 一次性预取：并集代码 × [最早窗口起点, 最后交易日] ──
    theme_members, _mp, _raw = load_theme_members(dates[-1])
    mv2, _ = load_membership_v2()
    codes = {c for recs in theme_members.values() for c in recs}
    codes |= {c for v in mv2.values() for c in (v['cores'] | v['extensions'])}
    w0 = str(get_recent_trade_dates(n=HIST_DAYS, end_date=dates[0])[0])
    print(f"[V2.5 回补] 预取日线：{len(codes)} 只 × {w0} ~ {dates[-1]}（一次预取，逐日切片）")
    t0 = time.time()
    df_all = fetch_kline(sorted(codes), w0, dates[-1])
    print(f"[V2.5 回补] 预取完成：{len(df_all)} 行，用时 {time.time()-t0:.0f}s")

    store, ok = _series_load(), 0
    for i, d in enumerate(dates, 1):
        try:
            core = compute_core(d, verbose=False, df_override=df_all)
        except SystemExit as e:
            print(f"  [{i}/{len(dates)}] {d} 跳过：{e}")
            continue
        if core is None:
            print(f"  [{i}/{len(dates)}] {d} 跳过：排名池为空")
            continue
        for t, v in core['scored'].items():
            dd = v.get('TODAY')
            if dd:
                store[(d, t)] = _snapshot_row(d, t, dd)
        ok += 1
        if i % 10 == 0 or i == len(dates):
            _series_save(store)
            print(f"  [{i}/{len(dates)}] {d} 完成（已写 {ok} 日，用时 {time.time()-t0:.0f}s）")
    n_row = _series_save(store)
    print(f"[V2.5 回补] 完成：{ok}/{len(dates)} 个交易日 → {os.path.basename(SERIES_PATH)}"
          f"（共 {n_row} 行，用时 {time.time()-t0:.0f}s）")


def load_evolution(trade_date):
    """消费端入口：读取 theme_heat_v25_{trade_date}.json；缺失 → None

    只输出「状态类」字段（Theme State / Momentum / Rank Migration / Persistence /
    Future Evolution），供下游主题层使用；不含任何 BUY（§二十一）。
    """
    path = os.path.join(REPORT_DIR, f'theme_heat_v25_{trade_date}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            rows = json.load(f)
    except Exception:
        return None
    return {str(r.get('theme')): r for r in rows if r.get('theme')} or None


def evolution_summary(evo):
    """§二十 一句话市场总结（消费端可直接调用；evo = load_evolution() 结果）

    与 V2.5 报告正文使用同一函数，避免下游重复实现导致口径分叉。
    """
    if not evo:
        return ''
    rows = [{'theme': r.get('theme'), 'state': r.get('state'),
             'metrics': {'heat': r.get('heat')}}
            for r in evo.values() if r.get('theme')]
    return _market_summary(rows) if rows else ''


def build_panel(end_date, themes):
    """§三 构建 [交易日历 × 主题] 演变面板（值来自序列 CSV；缺失 → None）

    同时返回逐日「排名池大小」pool（当日有有效 Heat 的主题数）。V2.5.1 起 Rank 语义为
    「当日 Heat 在当日排名池内的名次（1 = 最强）」，缺少池大小则名次不可解释。
    """
    from stock_cache import get_recent_trade_dates
    dates = [str(d) for d in get_recent_trade_dates(n=EVO_LOOKBACK + 1, end_date=end_date)]
    store = _series_load()
    panel = {}
    for t in themes:
        seq = []
        for d in dates:
            r = store.get((d, t))
            flags = str((r or {}).get('flags') or '')
            seq.append({'heat': _num(r.get('heat')) if r else None,
                        'base_heat': _num(r.get('base_heat')) if r else None,
                        'breadth': _num(r.get('breadth')) if r else None,
                        'rank': _num(r.get('rank')) if r else None,
                        'n': _num(r.get('n')) if r else None,
                        'reliability': _num(r.get('reliability')) if r else None,
                        'flags': flags})
        panel[t] = seq
    pool = {}
    for j, d in enumerate(dates):
        pool[d] = sum(1 for t in themes
                      if j < len(panel[t]) and panel[t][j]['rank'] is not None)
    return dates, panel, pool


def compute_evolution_metrics(seq, rank_pool=None):
    """§四~§八 单主题变化指标（全部为已有数据的确定性计算）

    rank_pool：「当日排名池大小」（当日有有效 Heat 的主题数）。V2.5.1 起 Rank 语义为
    「当日 Heat 在当日排名池内的名次（1 = 最强）」，缺少池大小则名次不可解释。
    """
    heat = [x['heat'] for x in seq]
    rank = [x['rank'] for x in seq]
    br = [x['breadth'] for x in seq]
    m = {'heat': _val(heat, 0),
         'base_heat': _val([x.get('base_heat') for x in seq], 0),
         'rank_today': _val(rank, 0), 'rank_5d': _val(rank, 5),
         'rank_10d': _val(rank, 10), 'rank_20d': _val(rank, 20),
         'hm5': _delta(heat, 0, 5), 'hm10': _delta(heat, 0, 10),
         'hm20': _delta(heat, 0, 20),
         'breadth': _val(br, 0), 'breadth_d5': _delta(br, 0, 5),
         'breadth_d20': _delta(br, 0, 20),
         'n': _val([x['n'] for x in seq], 0),
         'reliability': _val([x['reliability'] for x in seq], 0),
         'flags': seq[-1]['flags'] if seq else ''}
    # §五 ACC = HM5 − (HM10 − HM5)
    m['acc'] = (None if (m['hm5'] is None or m['hm10'] is None)
                else m['hm5'] - (m['hm10'] - m['hm5']))
    # §七 RankGain20 = 20日前排名 − 今日排名（>0 → 排名提升）
    m['rank_gain20'] = (None if (m['rank_today'] is None or m['rank_20d'] is None)
                        else m['rank_20d'] - m['rank_today'])
    # §八 Persistence：近 20 个交易日的 TOP5 / TOP10 / TOP20 天数与连续 TOP10
    rk = [_val(rank, k) for k in range(EVO_LOOKBACK)]
    m['top5_count_20d'] = sum(1 for x in rk if x is not None and x <= EVO_TOP5)
    m['top10_count_20d'] = sum(1 for x in rk if x is not None and x <= EVO_TOP10)
    m['top20_count_20d'] = sum(1 for x in rk if x is not None and x <= EVO_TOP20)
    m['days_20d'] = sum(1 for x in rk if x is not None)
    streak = 0
    for x in rk:
        if x is not None and x <= EVO_TOP10:
            streak += 1
        else:
            break
    m['top10_streak'] = streak
    m['ranks_recent'] = list(reversed(rk))          # 时间正序（旧 → 新）
    m['history_ok'] = m['days_20d'] >= EVO_LOOKBACK and m['hm20'] is not None
    # §八「边际状态」（V2.5.1 新增）：只看最近 5 日方向，与当前处于哪个层级无关
    h5, b5 = m['hm5'], m['breadth_d5']
    if h5 is not None and h5 > EVO_HM_EPS and (b5 is None or b5 > -EVO_BREADTH_EPS):
        m['marginal'] = 'IMPROVING'
    elif h5 is not None and h5 < -EVO_HM_EPS:
        m['marginal'] = 'DETERIORATING'
    else:
        m['marginal'] = 'FLAT'
    m['rank_pool_today'] = rank_pool
    return m


def classify_state(m, scale=1.0):
    """§九 生命周期判定（纯规则、可由数据复现；scale 仅用于 §十二 参数扰动）

    判定顺序：LEADING/PEAKING/COOLING（当前已在强势区且具备持续性）
              → RELAUNCH/REBOUND（曾强→中间降温→重新升温；按「是否已重回强势区 + 是否扩散确认」拆分）
              → ACCELERATING → EMERGING → COOLING。
    V2.5.1：状态定义不变，「二次启动」拆为 RELAUNCH（真正再启动）/ REBOUND（反弹）两个并列状态。
    数据不足时 conservatively 归入 EMERGING/COOLING，并由 LOW_SAMPLE /
    EVOLUTION_INSUFFICIENT 标记提示可信度（§十）。
    """
    r_strong = EVO_STRONG_RANK * scale
    r_cool = EVO_COOL_RANK * scale
    hm_accel = EVO_HM_ACCEL * scale
    hm5 = m['hm5'] if m['hm5'] is not None else 0.0
    hm10 = m['hm10'] if m['hm10'] is not None else 0.0
    acc = m['acc'] if m['acc'] is not None else 0.0
    bd5 = m['breadth_d5'] if m['breadth_d5'] is not None else 0.0
    r_now, r5 = m['rank_today'], m['rank_5d']

    strong_now = r_now is not None and r_now <= r_strong
    heating = hm5 > EVO_HM_EPS
    diffusing = bd5 > EVO_BREADTH_EPS
    contracting = bd5 < -EVO_BREADTH_EPS
    accelerating = heating and hm5 >= hm_accel and acc > 0
    rank_improving = (r5 is not None and r_now is not None and r_now < r5)
    rank_worse = (r5 is not None and r_now is not None and r_now > r5)
    hm5_slow = hm10 > 0 and hm5 < 0.4 * hm10

    days = max(1, m['days_20d'])
    top10 = m['top10_count_20d']
    streak = m.get('top10_streak', 0)
    persist_hi = top10 >= max(2, round(days * (EVO_PERSIST_HI / float(EVO_LOOKBACK)) * scale))
    persist_lo = top10 <= round(days * (EVO_PERSIST_LO / float(EVO_LOOKBACK)) * scale)
    established = persist_hi or streak >= EVO_STREAK_MIN   # 已在强势区且具备一定持续性

    rr = m['ranks_recent']
    was_strong = len(rr) >= 8 and any(x is not None and x <= r_strong for x in rr[:-6])
    cooled_mid = len(rr) >= 8 and any(x is not None and x >= r_cool for x in rr[-6:-2])

    # ① 当前就在强势区且有一定持续性 → 主线 / 高位钝化 / 已转弱
    if strong_now and established:
        if acc < 0 and (contracting or (hm5_slow and not diffusing)):
            return 'PEAKING'
        if hm5 < 0 and (contracting or rank_worse):
            return 'COOLING'
        return 'LEADING'
    # ② 曾强 → 中间明显降温 → 重新升温（且当前尚未形成持续强势）→ 二次启动
    #    V2.5.1：拆为「真正再启动」（已重回强势区 + 上涨范围同步扩大）
    #            与「反弹」（重新升温但未回到强势区，或扩散未确认）
    if (was_strong and cooled_mid and heating and (diffusing or rank_improving)
            and r_now is not None and r_now <= r_cool):
        back_strong = (strong_now or streak >= EVO_RELAUNCH_STREAK
                       or top10 >= EVO_RELAUNCH_PERSIST)
        return 'RELAUNCH' if (back_strong and diffusing) else 'REBOUND'
    # ③ 正在加速升温
    if accelerating and (diffusing or strong_now or rank_improving):
        return 'ACCELERATING'
    # ④ 刚开始活跃 / 新出现
    if heating and (rank_improving or diffusing or (strong_now and persist_lo)):
        return 'EMERGING'
    # ⑤ 降温
    if hm5 < 0 and (contracting or rank_worse):
        return 'COOLING'
    return 'EMERGING' if heating else 'COOLING'


def project_states(state, m):
    """§十一 短期（约5个交易日）与中期（约20个交易日）状态倾向

    V2.5.1：短期与中期严格分开、各自查 NEXT_STATE，不做 T+5→T+10→T+20 串联；
    只给「状态倾向」，不预测涨跌幅 / 收益率。
    短期方向看最近 5 日动量（HM5 / ACC），中期方向看近 20 日动量（HM20）。
    """
    h5 = m['hm5'] if m['hm5'] is not None else 0.0
    a5 = m['acc'] if m['acc'] is not None else 0.0
    h20 = m['hm20'] if m['hm20'] is not None else 0.0
    if h5 > EVO_HM_EPS or (a5 > 0 and h5 >= -EVO_HM_EPS):
        short_dir = 'up'
    elif h5 < -EVO_HM_EPS and a5 <= 0:
        short_dir = 'down'
    else:
        short_dir = 'flat'
    long_dir = 'up' if h20 > EVO_HM_EPS else ('down' if h20 < -EVO_HM_EPS else 'flat')
    row = NEXT_STATE.get(state) or NEXT_STATE['EMERGING']
    return {'short_dir': short_dir, 'short_state': row[short_dir],
            'long_dir': long_dir, 'long_state': row[long_dir]}


def _state_series(store, dates, themes):
    """Walk-forward 计算每个主题在每个交易日的状态（只依赖 ≤ 当日数据）"""
    arr = {}
    for t in themes:
        col = {k: [] for k in ('heat', 'rank', 'breadth', 'n', 'reliability')}
        for d in dates:
            r = store.get((d, t))
            for k in col:
                col[k].append(_num(r.get(k)) if r else None)
        arr[t] = col
    states = {t: [None] * len(dates) for t in themes}
    for t in themes:
        col = arr[t]
        for i in range(len(dates)):
            if i < EVO_LOOKBACK:
                continue
            seq = [{'heat': col['heat'][k], 'rank': col['rank'][k],
                    'breadth': col['breadth'][k], 'n': col['n'][k],
                    'reliability': col['reliability'][k], 'flags': ''}
                   for k in range(i - EVO_LOOKBACK, i + 1)]
            mm = compute_evolution_metrics(seq)
            if mm['heat'] is None or mm['rank_today'] is None or not mm['history_ok']:
                continue
            states[t][i] = classify_state(mm)
    return states


def _transition_counts(states, dates, idxs, h):
    """给定样本区间 idxs 的状态迁移计数（(from, to) → n）"""
    cnt, tot = {}, 0
    for t, st in states.items():
        for i in idxs:
            j = i + h
            if j >= len(dates) or st[i] is None or st[j] is None:
                continue
            cnt[(st[i], st[j])] = cnt.get((st[i], st[j]), 0) + 1
            tot += 1
    return cnt, tot


def compute_transitions():
    """§十二 历史状态迁移（Walk-forward / 不使用未来数据 / IS-OOS 分离 /
    参数扰动 / 最小样本量 / Null Model）

    返回 {horizons: {h: {state: {...}}}, stability, is_oos, obs, dates_used, themes_used}
    """
    store = _series_load()
    if not store:
        return {}
    dates = sorted({d for (d, _t) in store})
    themes = sorted({t for (_d, t) in store})
    if len(dates) < EVO_LOOKBACK + max(EVO_HORIZONS) + 1:
        return {'obs': 0, 'dates_used': len(dates), 'themes_used': len(themes),
                'series_days': len(dates), 'series_span': (dates[0], dates[-1]),
                'horizons': {}, 'stability': None, 'is_oos': {}, 'insufficient': True}
    states = _state_series(store, dates, themes)
    usable = [i for i, d in enumerate(dates)
              if i >= EVO_LOOKBACK and any(states[t][i] is not None for t in themes)]

    used = [t for t in themes if any(states[t][i] is not None for i in usable)]
    out = {'obs': 0, 'dates_used': len(usable), 'themes_used': len(used),
           'series_days': len(dates), 'series_span': (dates[0], dates[-1]),
           'horizons': {}, 'stability': None, 'is_oos': {}, 'insufficient': False,
           'span': (dates[usable[0]] if usable else None,
                    dates[usable[-1]] if usable else None)}
    # 参数扰动：阈值 ×0.8 / ×1.2 后状态不变的比例（状态稳定性）
    if usable:
        base_states = {t: [states[t][i] for i in usable] for t in themes}
        agree = tot = 0
        for s in EVO_PERTURB:
            alt_states = _state_series_scaled(store, dates, themes, s)
            for t in themes:
                for k, i in enumerate(usable):
                    a, b = base_states[t][k], alt_states[t][i]
                    if a is None or b is None:
                        continue
                    tot += 1
                    agree += int(a == b)
        out['stability'] = (agree / tot) if tot else None

    split = usable[:max(1, int(len(usable) * 0.6))], usable[max(1, int(len(usable) * 0.6)):]
    for h in EVO_HORIZONS:
        cnt, tot = _transition_counts(states, dates, usable, h)
        out['obs'] = max(out['obs'], tot)
        base = {}
        for (a, b), c in cnt.items():
            base[b] = base.get(b, 0) + c
        per = {}
        for (a, b), c in cnt.items():
            d = per.setdefault(a, {'n': 0, 'to': {}})
            d['n'] += c
            d['to'][b] = d['to'].get(b, 0) + c
        for a, d in per.items():
            top = max(d['to'].items(), key=lambda kv: kv[1])
            d['top'] = top[0]
            d['rate'] = top[1] / d['n']
            d['base_rate'] = base.get(top[0], 0) / tot if tot else 0.0
            d['lift'] = d['rate'] - d['base_rate']
            if d['n'] < EVO_MIN_TRANS_N:
                d['confidence'] = 'LOW'
            elif d['n'] >= 30 and d['rate'] >= 0.50:
                d['confidence'] = 'HIGH'
            else:
                d['confidence'] = 'MEDIUM'
        # IS / OOS 分离：分别统计「按 IS 选出的主路径」在 IS 与 OOS 上的命中次数
        is_cnt, is_tot = _transition_counts(states, dates, split[0], h)
        oos_cnt, oos_tot = _transition_counts(states, dates, split[1], h)
        is_oos = {a: {'is_n': is_cnt.get((a, d['top']), 0),
                      'oos_n': oos_cnt.get((a, d['top']), 0)}
                  for a, d in per.items()}
        out['horizons'][h] = {'per': per, 'total': tot, 'is_total': is_tot,
                              'oos_total': oos_tot, 'is_oos': is_oos}
    return out


def _state_series_scaled(store, dates, themes, scale):
    """参数扰动版状态序列（§十二 robustness check）"""
    arr = {}
    for t in themes:
        col = {k: [] for k in ('heat', 'rank', 'breadth', 'n', 'reliability')}
        for d in dates:
            r = store.get((d, t))
            for k in col:
                col[k].append(_num(r.get(k)) if r else None)
        arr[t] = col
    states = {t: [None] * len(dates) for t in themes}
    for t in themes:
        col = arr[t]
        for i in range(len(dates)):
            if i < EVO_LOOKBACK:
                continue
            seq = [{'heat': col['heat'][k], 'rank': col['rank'][k],
                    'breadth': col['breadth'][k], 'n': col['n'][k],
                    'reliability': col['reliability'][k], 'flags': ''}
                   for k in range(i - EVO_LOOKBACK, i + 1)]
            mm = compute_evolution_metrics(seq)
            if mm['heat'] is None or mm['rank_today'] is None or not mm['history_ok']:
                continue
            states[t][i] = classify_state(mm, scale=scale)
    return states


# ── §十四/§十五 自然语言解释层（只翻译已算出的字段）──

def _trend_cn(x):
    """热度动量 → 散户可读的定性措辞（不暴露 HM 数值）"""
    if x is None:
        return '不明显'
    if x > 5.0:
        return '明显上升'
    if x > EVO_HM_EPS:
        return '小幅上升'
    if x < -5.0:
        return '明显下降'
    if x < -EVO_HM_EPS:
        return '小幅下降'
    return '基本持平'


def _sanitize(text):
    """§十六 禁止表达校验（模板本身即规避，此处作为硬性护栏）"""
    hits = [w for w in BANNED_PHRASES if w in text]
    if hits:
        raise AssertionError(f'V2.5 自然语言层出现禁止表达：{hits}')
    return text


def _reasons(m, st):
    """§十七 第二层：只挑 3 条最重要的「为什么」（用可读措辞，不用专业字段名）"""
    cand = []
    if m['hm5'] is not None:
        cand.append(f"最近5日热度{_trend_cn(m['hm5'])}")
    if m['breadth_d5'] is not None:
        if m['breadth_d5'] > EVO_BREADTH_EPS:
            cand.append('上涨正在向更多成份股扩散')
        elif m['breadth_d5'] < -EVO_BREADTH_EPS:
            cand.append('参与上涨的股票数量在减少' if st in ('COOLING', 'PEAKING')
                        else '参与上涨的股票数量没有同步增加')
    if m['rank_today'] is not None:
        if m['rank_gain20'] is not None and m['rank_gain20'] >= 3:
            cand.append(f"排名相对一个月前提升 {int(m['rank_gain20'])} 位")
        elif m['rank_gain20'] is not None and m['rank_gain20'] <= -3:
            if st in ('COOLING', 'PEAKING'):     # 非降温状态不把「排名回落」列为走强原因
                cand.append(f"排名相对一个月前回落 {int(-m['rank_gain20'])} 位")
        else:
            cand.append(f"当前市场排名第 {int(m['rank_today'])} 位")
    if m['top10_count_20d']:
        cand.append(f"过去20个交易日中有 {int(m['top10_count_20d'])} 天处于前十")
    if m['acc'] is not None and abs(m['acc']) > EVO_HM_EPS:
        cand.append('最近5天的升温速度比前期更快' if m['acc'] > 0 else '最近5天的升温速度比前期放缓')
    if m['hm20'] is not None and abs(m['hm20']) > EVO_HM_EPS:
        cand.append(f"过去一个月总体{'升温' if m['hm20'] > 0 else '降温'}")
    if not cand:
        cand.append('当前变化不明显，主题处于横盘观察状态')
    return cand[:3]


def narrate(theme, row):
    """§十四/§十五 结构化字段 → 自然语言

    V2.5.1：只输出四段 —— 当前状态 + 支持证据 + 确认条件 + 失效条件；
    「确认 / 失效」条件短期（约5个交易日）与中期（约20个交易日）严格分开。
    历史迁移统计不再进入自然语言，只在报告「状态迁移参考」中作参考展示。
    """
    m, st = row['metrics'], row['state']
    label = STATE_INFO[st][0]
    marg_cn = MARGINAL_INFO.get(m.get('marginal'), ('持平', ''))[0]
    low = 'LOW_SAMPLE' in row['flags']
    insufficient = 'EVOLUTION_INSUFFICIENT' in row['flags']

    status = f"{theme}目前处于「{label}」，边际状态为「{marg_cn}」。{STATE_DESC[st]}"
    if low:
        status += '（注意：该主题参与股票数量较少，热度变化容易受到少数股票影响，当前信号可信度有限。）'
    if insufficient:
        status += '（注意：该主题的历史热度序列还不足 20 个交易日，变化指标暂不完整。）'

    why = _reasons(m, st)
    evidence = '支持证据：' + '；'.join(
        f'{chr(0x2460 + i)}{t}' for i, t in enumerate(why)) + '。'
    cs, ivs, cl, ivl = CONFIRM_INVALID[st]
    out = {'label': label, 'emoji': STATE_INFO[st][1],
           'status': status, 'evidence': evidence,
           'confirm_short': cs, 'invalid_short': ivs,
           'confirm_long': cl, 'invalid_long': ivl}
    for k in ('status', 'evidence', 'confirm_short', 'invalid_short',
              'confirm_long', 'invalid_long'):
        _sanitize(out[k])
    return out

def _radar_text(row):
    """§十八 主题雷达「人话」列（一句话、口语化、不含预测）"""
    m, st = row['metrics'], row['state']
    dec = m['acc'] is not None and m['acc'] < -EVO_HM_EPS
    acc_up = m['acc'] is not None and m['acc'] > EVO_HM_EPS
    con = m['breadth_d5'] is not None and m['breadth_d5'] < -EVO_BREADTH_EPS
    if st == 'LEADING':
        if dec:
            return '目前仍然强，但升温速度有所放缓'
        return '目前仍然强，而且还在继续升温' if acc_up else '目前仍然强，热度维持'
    if st == 'ACCELERATING':
        return '最近明显升温，正在进入市场关注范围'
    if st == 'RELAUNCH':
        return '调整后重新回到强势区，上涨范围同步扩大'
    if st == 'REBOUND':
        return '调整后重新升温，但还没有回到强势区'
    if st == 'EMERGING':
        return '刚开始活跃，还需要持续性验证'
    if st == 'PEAKING':
        return '仍然强，但参与度开始下降' if con else '仍然强，但升温速度慢下来了'
    return '热度和排名都在下降'


def _mk_table(headers, data):
    ws = [max([_dw(h)] + [_dw(r[i]) for r in data]) if data else _dw(h)
          for i, h in enumerate(headers)]
    out = [SEP_THIN, '  ' + '  '.join(_pad(h, ws[i]) for i, h in enumerate(headers))]
    for r in data:
        out.append('  ' + '  '.join(_pad(r[i], ws[i]) for i in range(len(headers))))
    out.append(SEP_THIN)
    return out


def build_evolution_report(trade_date, rows, trans, core, dates):
    """§十七/§十八/§十九/§二十 面向普通投资者的演变报告（V2.5.1 清洗版）"""
    L = [SEP_FULL, '主题热度 V2.5.1 —— 未来一个月演变', f'交易日 {trade_date}', SEP_FULL]
    L.append('* 本模块只描述「主题热度正在往哪里移动」，不含任何买卖决策；'
             '个股买卖由独立的量价与趋势模块决定。')
    L.append(f'* 状态 = 基于最近 5/10/{EVO_LOOKBACK} 日热度变化、排名迁移、内部扩散与持续性，'
             f'判定的七种生命周期：{"/".join(STATE_INFO[s][0] for s in STATE_ORDER)}。')
    L.append('* 边际状态 = 只看最近 5 日方向，与当前层级无关：'
             + "/".join(f'{MARGINAL_INFO[k][1]}{MARGINAL_INFO[k][0]}' for k in
                        ('IMPROVING', 'FLAT', 'DETERIORATING')) + '。')
    L.append('* Rank = 当日 Heat 在当日排名池内的名次（1 = 最强），排名池 = 当日有有效 Heat 的主题数。')
    L.append('* 只预测「热度状态」可能如何变化，不预测涨跌幅、收益率或「一定上涨」（§十一）。')
    L.append('* 短期（约5个交易日）与中期（约20个交易日）严格分开，不做 T+5→T+10→T+20 串联。')
    L.append(f'* 低样本保护：N<{EVO_LOWSAMPLE_N} 或 reliability<{EVO_REL_MIN:.2f} → LOW_SAMPLE，'
             '只作观察，不据此判断后续走势（§十）。')
    L.append('')

    low = [r for r in rows if 'LOW_SAMPLE' in r['flags']]
    insuff = [r for r in rows if 'EVOLUTION_INSUFFICIENT' in r['flags']]
    L.append('【演变数据质量】')
    sspan = trans.get('series_span') or (None, None)
    L.append(f'主题热度序列：{trans.get("series_days") or 0} 个交易日'
             f'（{sspan[0]} ~ {sspan[1]}，来源 {os.path.basename(SERIES_PATH)}）')
    L.append(f'演变分析窗口：最近 {len(dates)} 个交易日（{dates[0]} ~ {dates[-1]}）'
             f'｜状态迁移观测：{trans.get("obs") or 0} 次｜主题 {trans.get("themes_used") or 0} 个'
             f'｜最小样本 {EVO_MIN_TRANS_N}')
    stab = trans.get('stability')
    L.append(f'参数扰动稳定性（阈值 ×{EVO_PERTURB[0]} / ×{EVO_PERTURB[1]}）：'
             f'{stab*100:.0f}%' if stab is not None else '参数扰动稳定性：样本不足')
    L.append(f'LOW_SAMPLE：{len(low)} 个主题｜EVOLUTION_INSUFFICIENT：{len(insuff)} 个主题'
             '（序列不足 20 个交易日）')
    L.append('')

    # ── §十九 分组自然语言（每状态最多 6 个主题，按热度降序）──
    L.append('【主题热度——未来一个月演变】')
    L.append(SEP_THIN)
    for st in STATE_ORDER:
        grp = sorted([r for r in rows if r['state'] == st],
                     key=lambda r: -(r['metrics']['heat'] or 0.0))
        if not grp:
            continue
        L.append('')
        L.append(f'{STATE_INFO[st][1]} {STATE_INFO[st][0]}（{len(grp)} 个）')
        for r in grp[:6]:
            n = r['narr']
            L.append(f'**{r["theme"]}**')
            L.append(f'> 当前状态：{n["status"]}')
            L.append(f'> {n["evidence"]}')
            L.append(f'> 确认条件（短期）：{n["confirm_short"]}')
            L.append(f'> 失效条件（短期）：{n["invalid_short"]}')
            L.append(f'> 确认条件（中期）：{n["confirm_long"]}')
            L.append(f'> 失效条件（中期）：{n["invalid_long"]}')
        if len(grp) > 6:
            L.append(f'  （其余 {len(grp) - 6} 个同类主题见下方量化明细）')
    L.append('')

    # ── §十八 主题雷达（展示层 Emoji；底层仍为英文状态）──
    radar = []
    for st in STATE_ORDER:
        grp = sorted([r for r in rows if r['state'] == st],
                     key=lambda r: -(r['metrics']['heat'] or 0.0))
        for r in grp[:5]:
            mi = MARGINAL_INFO.get(r['metrics'].get('marginal'), ('持平', ''))
            radar.append([f'{STATE_INFO[st][1]}{STATE_INFO[st][0]}',
                          f'{mi[1]}{mi[0]}', r['theme'], _radar_text(r)])
    if radar:
        L.append('【主题雷达】')
        L += _mk_table(['状态', '边际', '主题', '人话'], radar)
    else:
        L.append('【主题雷达】')
        L.append('  （无）')
    L.append('')

    # ── §十二/§十三 状态迁移（本节为统计口径，仅作参考；散户正文不给概率数字）──
    L.append('【状态迁移参考（历史统计口径，仅作参考，不构成概率判断）】')
    L.append('* 以下为历史上「同一状态」在 5 个交易日后的状态分布，只用于事后对照，'
             '不得作为当前主题的预测或确认/失效依据。')
    L.append(SEP_THIN)
    hblk = (trans.get('horizons') or {}).get(5, {})
    per5, io5 = hblk.get('per', {}), hblk.get('is_oos', {})
    shown = 0
    for st in STATE_ORDER:
        d = per5.get(st)
        if not d or d['n'] < EVO_MIN_TRANS_N:
            continue
        nxt = STATE_INFO.get(d['top'], (d['top'], ''))[0]
        io = io5.get(st, {})
        L.append(f'  · 「{STATE_INFO[st][0]}」→ 5 个交易日后更常见状态「{nxt}」'
                 f'｜transition_n={d["n"]}｜transition_rate={d["rate"]*100:.0f}%'
                 f'（整体基准 {d["base_rate"]*100:.0f}%，lift {d["lift"]*100:+.0f}pp）'
                 f'｜confidence={d["confidence"]}'
                 f'｜IS 命中 {io.get("is_n", 0)} 次 / OOS 命中 {io.get("oos_n", 0)} 次')
        shown += 1
    if not shown:
        L.append('  历史样本不足，目前只能作为观察信号，不能据此判断后续走势。')
    if trans.get('insufficient'):
        L.append('  （序列长度不足，尚未开始统计状态迁移。）')
    L.append(SEP_THIN)
    L.append('')

    # ── §二十 一句话市场总结（只允许一段）──
    L.append('【一句话市场总结】')
    L.append('> ' + _market_summary(rows))
    L.append('')

    # ── 附：量化明细（供复核；普通阅读可跳过）──
    det = []
    for r in sorted(rows, key=lambda r: (STATE_ORDER.index(r['state']),
                                         -(r['metrics']['heat'] or 0.0))):
        m, p = r['metrics'], r['proj']
        mi = MARGINAL_INFO.get(m.get('marginal'), ('持平', ''))
        det.append([r['theme'], f'{STATE_INFO[r["state"]][1]}{STATE_INFO[r["state"]][0]}',
                    f'{mi[1]}{mi[0]}',
                    _f(m['heat'], 1), _f(m['hm5'], 1), _f(m['hm10'], 1), _f(m['hm20'], 1),
                    _f(m['acc'], 1), _f(m['breadth_d5'], 1),
                    f"{_f(m['rank_today'],0)}/{_f(m['rank_5d'],0)}/{_f(m['rank_10d'],0)}/"
                    f"{_f(m['rank_20d'],0)}",
                    _f(m.get('rank_pool_today'), 0),
                    str(int(m['top10_count_20d'])), str(int(m['top5_count_20d'])),
                    _f(m['reliability'], 2), str(int(m['n'] or 0)),
                    f"{STATE_INFO[p['short_state']][1]}{STATE_INFO[p['short_state']][0]}",
                    f"{STATE_INFO[p['long_state']][1]}{STATE_INFO[p['long_state']][0]}",
                    'LOW_SAMPLE' if 'LOW_SAMPLE' in r['flags'] else ''])
    L.append('【附：量化明细（供复核，普通阅读可跳过）】')
    L += _mk_table(['主题', '状态', '边际', 'Heat', 'HM5', 'HM10', 'HM20', 'ACC', 'ΔBr5',
                    'Rank今/5/10/20', '池N', 'TOP10', 'TOP5', 'Rel', 'N',
                    '短期倾向', '中期倾向', 'Flag'], det)
    L.append(SEP_FULL)
    return '\n'.join(L)


def _market_summary(rows):
    def top(st, k):
        sel = sorted([r for r in rows if r['state'] == st],
                     key=lambda r: -(r['metrics']['heat'] or 0.0))
        return [r['theme'] for r in sel[:k]]
    cur = top('LEADING', 2)
    nxt = top('ACCELERATING', 2) + top('RELAUNCH', 1) + top('EMERGING', 1)
    dn = top('COOLING', 2)
    txt = f"当前市场主题正在从【{'、'.join(cur) or '暂无明确主线'}】向" \
          f"【{'、'.join(nxt) or '暂未出现新的升温方向'}】扩散"
    if dn:
        txt += f"，同时【{'、'.join(dn)}】开始降温"
    txt += ('。未来一个月最值得观察的不是当前热度最高的主题，而是'
            '「热度持续上升 + 排名提升 + 成份股同步扩散」的方向。')
    return _sanitize(txt)


def save_evolution(trade_date, rows):
    """落盘 theme_heat_v25_{date}.json（V2.5.1 统一 Schema，供 AI 解释层与下游消费）

    所有主题共用同一套字段（EVO_FIELDS）；缺失字段显式写 null（不省略、不变形）；
    逐行强校验，字段集合与 EVO_FIELDS 不一致即报错。
    """
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = []
    for r in sorted(rows, key=lambda x: (STATE_ORDER.index(x['state']),
                                         -(x['metrics']['heat'] or 0.0))):
        m, p = r['metrics'], r['proj']
        row = {
            'schema_version': SCHEMA_VERSION,
            'theme': r['theme'], 'state': r['state'],
            'state_cn': STATE_INFO[r['state']][0], 'emoji': STATE_INFO[r['state']][1],
            'marginal': m.get('marginal'),
            'marginal_cn': MARGINAL_INFO.get(m.get('marginal'), (None, ''))[0],
            'heat': _r(m['heat']), 'base_heat': _r(m.get('base_heat')),
            'hm5': _r(m['hm5']), 'hm10': _r(m['hm10']), 'hm20': _r(m['hm20']),
            'acc': _r(m['acc']),
            'breadth': _r(m['breadth']), 'breadth_d5': _r(m['breadth_d5']),
            'breadth_d20': _r(m['breadth_d20']),
            'rank_today': _ri(m['rank_today']), 'rank_5d': _ri(m['rank_5d']),
            'rank_10d': _ri(m['rank_10d']), 'rank_20d': _ri(m['rank_20d']),
            'rank_gain20': _ri(m['rank_gain20']),
            'rank_pool_today': _ri(m.get('rank_pool_today')),
            'top5_count_20d': int(m['top5_count_20d']),
            'top10_count_20d': int(m['top10_count_20d']),
            'top20_count_20d': int(m['top20_count_20d']),
            'top10_streak': int(m['top10_streak']),
            'days_20d': int(m['days_20d']),
            'reliability': _r(m['reliability']), 'n': _ri(m['n']),
            'history_ok': bool(m['history_ok']),
            'flags': r['flags'],
            'outlook_short_dir': p['short_dir'], 'outlook_short_state': p['short_state'],
            'outlook_long_dir': p['long_dir'], 'outlook_long_state': p['long_state'],
            'status': r['narr']['status'], 'evidence': r['narr']['evidence'],
            'confirm_short': r['narr']['confirm_short'], 'invalid_short': r['narr']['invalid_short'],
            'confirm_long': r['narr']['confirm_long'], 'invalid_long': r['narr']['invalid_long'],
            'radar': _radar_text(r),
        }
        missing = [f for f in EVO_FIELDS if f not in row]
        extra = [f for f in row if f not in EVO_FIELDS]
        if missing or extra:
            raise AssertionError(
                f'V2.5.1 Schema 不一致（主题 {r["theme"]}）：缺 {missing}｜多 {extra}')
        out.append({f: row[f] for f in EVO_FIELDS})
    base = os.path.join(REPORT_DIR, f'theme_heat_v25_{trade_date}')
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[保存] {os.path.basename(base)}.json / {os.path.basename(SERIES_PATH)}")
    return out


def _r(x, d=4):
    return None if x is None else round(float(x), d)


def _ri(x):
    return None if x is None else int(round(float(x)))


def run_evolution(core):
    """§V2.5 主入口：序列落盘 → 指标/状态 → 迁移统计 → 自然语言 → 报告"""
    trade_date = core['trade_date']
    series_append(core)                                   # §三 当日快照
    themes = sorted(core['scored'])
    dates, panel, pool = build_panel(trade_date, themes)
    pool_today = pool.get(dates[-1]) if dates else None
    mapping = core['mapping']
    rows = []
    for t in themes:
        m = compute_evolution_metrics(panel[t], rank_pool=pool_today)
        if m['heat'] is None:
            continue
        st = classify_state(m)
        flags = set()
        if _evolution_low_sample(m, t, mapping):
            flags.add('LOW_SAMPLE')
        if not m['history_ok']:
            flags.add('EVOLUTION_INSUFFICIENT')
        rows.append({'theme': t, 'state': st, 'metrics': m, 'flags': sorted(flags),
                     'proj': project_states(st, m)})
    trans = compute_transitions()
    for r in rows:
        r['narr'] = narrate(r['theme'], r)
    md = build_evolution_report(trade_date, rows, trans, core, dates)
    save_evolution(trade_date, rows)
    base = os.path.join(REPORT_DIR, f'theme_heat_v25_{trade_date}')
    with open(base + '.md', 'w', encoding='utf-8') as f:
        f.write(md + '\n')
    print(md)
    return {'trade_date': trade_date, 'rows': rows, 'transitions': trans}


def _evolution_low_sample(m, t, mapping):
    """§十 低样本保护（N<15 或 reliability<0.50 或 V2.4 已判 LOW_SAMPLE）"""
    mq = mapping.get(t) or {}
    n = m.get('n')
    rel = m.get('reliability')
    if rel is None:
        rel = mq.get('sample_reliability')
    return (n is None or n < EVO_LOWSAMPLE_N
            or (rel is not None and rel < EVO_REL_MIN)
            or str(mq.get('sample_flag') or '') == 'LOW_SAMPLE')


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg and str(arg).lower() in ('backfill', 'back', '回补'):
        n = int(sys.argv[2]) if len(sys.argv) > 2 else EVO_SERIES_DAYS
        backfill_series(n)
        return
    run(arg)


if __name__ == '__main__':
    main()
