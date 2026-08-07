#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine (TAE) V1.2 —— 基于 V15 股票池的市场风格增强排序
====================================================================

数据源（V1.1 起改用 theme_score_v2.py 的成份股对照表与主题分析结果）：
  - 成份股对照表: d:\\mystock\\cache_daily\\theme_stock_map_v2_{date}.json
      stocks 映射（每只股票 -> themes / dominant_theme），决定 V15 股票所属主题
      顶层 themes 映射（每主题 -> 成份股及 via 档位：leader_company 龙头 / core_company 核心 / dc_industry_board 普通成分）
  - 主题分析结果: report_daily/theme_scores_v2_{date}.csv
      lifecycle(启动/升温/主升/分歧/高潮/退潮) / composite_score / strength_score /
      fund_score / heat_v3 / leader_v3_score / breadth_score / mti_level(主线/准主线/轮动/补涨/非主线)
  - 大盘分析:    cache_backbone_tushare/market_analysis_{date}.txt（报告用）

设计原则（与 V15 完全解耦）：
  - 不修改 V15（double_score.py）任何代码、评分与输出。
  - FinalScore 永不改变，仅新增 ThemeRankScore 字段做动态排序。
  - Fundamental（V15）回答"买什么"；Theme Alpha（本模块）回答"今天先买谁"。

输入：
  - V15 输出 CSV（默认 report_daily/double_score_full.csv，可 --input 指定）
    至少包含 code/name/theme/FinalScore/IndustryRank/Recommendation，其余字段原样保留。

V1.2 新增交易面三因子（仅用可靠列：涨停次数/连板能力/筹码面/波段属性，资金流入等全 NaN 列禁用）：
  MoneyAttack      资金攻击性 = 0.45×涨停攻击分 + 0.35×连板攻击分 + 0.20×主题资金热度
  LeaderUniqueness 龙头唯一性 = 市场选龙头分（连板≥3 最高）+ via 档位加持 + 主题内龙头数修正
  BuyPointQuality  买点质量   = 0.40×筹码面 + 0.40×波段属性 + 0.20×追高适度性
  TradeScore       = 0.40×MoneyAttack + 0.35×LeaderUniqueness + 0.25×BuyPointQuality
  DynamicRank      改按 TradeRankScore = 0.70×ThemeRankScore + 0.30×TradeScore 排序

新增字段（14 个）：
  ThemeStrength / ThemeStage / StyleScore / MoneyScore / ThemeAlpha /
  ThemeRankScore / MoneyAttack / LeaderUniqueness / BuyPointQuality / TradeScore /
  TradeRankScore / DynamicRank / Action / Reason

核心公式：
  ThemeAlpha     = 0.40×ThemeStrength + 0.25×ThemeStage + 0.20×StyleScore + 0.15×MoneyScore
  ThemeRankScore = 0.70×FinalScore    + 0.30×ThemeAlpha        （FinalScore 原值不动）
  TradeRankScore = 0.70×ThemeRankScore + 0.30×TradeScore        （V1.2 动态排序依据）
  DynamicRank    = 按 TradeRankScore 降序排名

ThemeStrength   = 主题分析结果 composite_score（趋势+情绪综合）
ThemeStage      = lifecycle → 六阶段分值（启动80/升温90/主升100/高潮95/分歧60/退潮30）
StyleScore      = mti_level → 市场风格适配（主线100/准主线90/轮动70/补涨50/非主线30）
MoneyScore      = 0.30×fund_score + 0.25×heat_v3 + 0.25×leader_v3_score + 0.20×breadth_score
                  （资金流 / 主题热度 / 龙头强度 / 涨停扩散，与 TAE 规格一致）

输出：
  - report_daily/v15_theme_alpha.csv      （原 CSV 全部字段 + 9 个新字段）
  - report_daily/theme_alpha_report.md    （今日市场风格/主线/资金/动态Top20/变化/新机会/风险）
"""
import os
import re
import glob
import json
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==================== 路径 ====================
V15_CSV_DEFAULT = os.path.join(BASE_DIR, "report_daily", "double_score_full.csv")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
V2_MAP_DIR = r"d:\mystock\cache_daily"          # theme_stock_map_v2 成份股对照表
MARKET_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
OUT_CSV = os.path.join(REPORT_DIR, "v15_theme_alpha.csv")
OUT_MD = os.path.join(REPORT_DIR, "theme_alpha_report.md")

# ==================== 主题生命周期映射（theme_scores_v2.lifecycle → 六阶段分值） ====================
# 用户阶段体系：Birth(筑底)90 / Recovery(启动)80 / MainTrend(主升)100 /
#              Acceleration(加速)95 / Distribution(派发分歧)60 / Decline(衰退)30
STAGE_SCORE = {
    "启动": 80.0,      # Recovery
    "升温": 90.0,      # 启动→主升之间（更接近主升）
    "主升": 100.0,     # MainTrend
    "高潮": 95.0,      # Acceleration（高潮=加速见顶）
    "分歧": 60.0,      # Distribution（分歧=派发/调整）
    "退潮": 30.0,      # Decline
}

# ==================== 市场风格适配（mti_level → StyleScore） ====================
STYLE_MAP = {
    "主线": 100.0,
    "准主线": 90.0,
    "轮动主题": 70.0,
    "补涨主题": 50.0,
    "非主线": 30.0,
}

# 资金偏好权重（与 TAE 规格一致：成交额占比30% / 主题热度25% / 龙头强度25% / 涨停扩散20%）
MONEY_W = {"fund": 0.30, "heat": 0.25, "leader": 0.25, "breadth": 0.20}

# ==================== 行业兜底映射（V2 无归属时的最终兜底） ====================
# V2 主题体系未收录传统行业（地产/环保/钢铁/传媒等），用行业映射补主题，消除 theme 列空白
INDUSTRY_FALLBACK = {
    # 地产链
    "全国地产": "地产链", "区域地产": "地产链", "园区开发": "地产链",
    "房地产": "地产链", "房产服务": "地产链", "房地产服务": "地产链",
    # 节能环保
    "环境保护": "节能环保", "水务": "节能环保", "环保工程": "节能环保",
    "环保设备": "节能环保", "供气供热": "节能环保",
    # 钢铁
    "普钢": "钢铁", "钢加工": "钢铁", "特钢": "钢铁", "钢铁": "钢铁",
    "金属制品": "钢铁",
    # 传媒
    "广告包装": "传媒", "文教休闲": "传媒", "出版": "传媒",
    "影视音像": "传媒", "传媒": "传媒",
    # 建筑装饰
    "装修装饰": "建筑装饰", "建筑工程": "建筑装饰", "基础建设": "建筑装饰",
    # 其他
    "商贸代理": "商贸零售", "日用化工": "化工", "电器仪表": "仪器仪表",
    "公共交通": "交通运输", "综合类": "综合", "化工机械": "化工机械",
    "汽车服务": "汽车服务", "服饰": "纺织服饰",
}

NEUTRAL = 50.0  # 无主题覆盖时的中性默认值


# ==================== 数据加载（theme_score_v2 体系） ====================

def _latest_trade_date():
    """从 theme_scores_v2 CSV 中找最新交易日 YYYYMMDD"""
    files = glob.glob(os.path.join(REPORT_DIR, "theme_scores_v2_*.csv"))
    dates = []
    for f in files:
        m = re.search(r"_(\d{8})\.csv$", f)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else datetime.now().strftime("%Y%m%d")


def load_theme_scores_v2(trade_date):
    """加载 theme_score_v2 的主题分析结果 CSV -> {主题名: row dict}"""
    path = os.path.join(REPORT_DIR, f"theme_scores_v2_{trade_date}.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, encoding="utf-8-sig")
    return {str(r["theme"]): r.to_dict() for _, r in df.iterrows()}


def load_stock_map_v2(trade_date):
    """
    加载 theme_stock_map_v2 成份股对照表
    返回 (stocks, theme_refs)：
      stocks    -> {ts_code: {themes, dominant_theme, name, industry}}（股票 → 所属主题）
      theme_refs-> {主题名: {ts_code: via}}（每主题的成份股及 via 档位：
                   leader_company 龙头 / core_company 核心 / dc_industry_board 普通成分）
    """
    paths = [
        os.path.join(V2_MAP_DIR, f"theme_stock_map_v2_{trade_date}.json"),
        os.path.join(V2_MAP_DIR, f"theme_stock_map_{trade_date}.json"),
        os.path.join(V2_MAP_DIR, "theme_stock_map_v2_latest.json"),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            stocks = data.get("stocks", {})
            theme_refs = {}
            for tname, items in (data.get("themes") or {}).items():
                refs = {}
                for it in items or []:
                    if isinstance(it, dict) and it.get("code"):
                        refs[it["code"]] = it.get("via", "dc_industry_board")
                theme_refs[str(tname)] = refs
            return stocks, theme_refs
    return {}, {}


def load_market_analysis(trade_date):
    """解析大盘分析 txt -> dict（市场状态/趋势分/涨停/最高连板/仓位）"""
    path = os.path.join(MARKET_DIR, f"market_analysis_{trade_date}.txt")
    info = {"市场状态": "未知", "趋势分": None, "涨停": None, "最高连板": None, "仓位": None}
    if not os.path.exists(path):
        return info
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        m = re.search(r"市场状态:\s*(.+)", txt)
        if m:
            info["市场状态"] = m.group(1).strip()
        m = re.search(r"总趋势分 \(TrendScore\):\s*(\d+\.?\d*)", txt)
        if m:
            info["趋势分"] = float(m.group(1))
        m = re.search(r"涨停:\s*(\d+)", txt)
        if m:
            info["涨停"] = int(m.group(1))
        m = re.search(r"最高连板:\s*(\d+)", txt)
        if m:
            info["最高连板"] = int(m.group(1))
        m = re.search(r"总体仓位建议:\s*(\d+)%", txt)
        if m:
            info["仓位"] = int(m.group(1))
    except Exception:
        pass
    return info


def _to_ts_code(code):
    """V15 纯数字代码 → tushare ts_code（如 1309 → 0001309.SZ, 688578 → 688578.SH）"""
    s = str(code).strip().split(".")[0].zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"


# ==================== 主题级评分 ====================

def build_theme_metrics(scores_v2, theme_refs=None):
    """
    基于 theme_scores_v2 构建 {主题名: {strength, stage_score, stage_cn, style, money, ...}}
    - strength: ThemeStrength = composite_score
    - stage:    ThemeStage = lifecycle 映射分值
    - style:    StyleScore = mti_level 市场风格适配
    - money:    MoneyScore = 0.30×fund + 0.25×heat + 0.25×leader + 0.20×breadth
    - lc_count: 主题内龙头集中度（leader_company + core_company 数量），供龙头唯一性修正
    """
    tm = {}
    for name, r in scores_v2.items():
        composite = float(r.get("composite_score") or NEUTRAL)
        lifecycle = str(r.get("lifecycle") or "分歧")
        stage_score = STAGE_SCORE.get(lifecycle, NEUTRAL)
        mti_level = str(r.get("mti_level") or "非主线")
        style = STYLE_MAP.get(mti_level, NEUTRAL)
        money = round(
            MONEY_W["fund"] * float(r.get("fund_score") or NEUTRAL)
            + MONEY_W["heat"] * float(r.get("heat_v3") or NEUTRAL)
            + MONEY_W["leader"] * float(r.get("leader_v3_score") or NEUTRAL)
            + MONEY_W["breadth"] * float(r.get("breadth_score") or NEUTRAL),
            1,
        )
        refs = (theme_refs or {}).get(name, {})
        lc_count = sum(
            1 for via in refs.values() if via in ("leader_company", "core_company"))
        tm[name] = {
            "strength": round(float(composite), 1),
            "stage_score": float(stage_score),
            "stage_cn": lifecycle,
            "style": round(float(style), 1),
            "money": money,
            "composite": float(composite),
            "mti": float(r.get("mti") or 0.0),
            "mti_level": mti_level,
            "final_trade": float(r.get("final_trade_score") or 0.0),
            "trade_action": str(r.get("trade_action") or ""),
            "rank": int(r.get("rank") or 0),
            "lc_count": lc_count,
        }
    # 补充按 composite 的跨主题排名（供主题变化对比）
    ranked = sorted(tm, key=lambda x: -tm[x]["composite"])
    for i, n in enumerate(ranked, 1):
        tm[n]["comp_rank"] = i
    return tm


def theme_of_stock(code, row_theme, scores_v2, stock_map, industry=None):
    """
    确定 V15 股票所属主题：
    1. 优先 theme_stock_map_v2 成份股对照表的 themes 正式归属（themes[0]）
       —— dominant_theme 是相关性算法产物（corr_scores 最高），对跨界叙事
       (is_cross_narrative=1) 股票不可靠（如九安医疗 concepts 含 DeepSeek/Kimi
       概念被判 AI算力，但正式归属为创新药），故降为兜底
    2. 兜底 dominant_theme（themes 为空或未命中时）
    3. 再兜底：V15 theme 列精确命中主题分析结果
    4. 最终兜底：V2 themes[0] / dominant_theme 无条件回填（即使不在 scores_v2），
       确保 V15 中所有 V2 有归属的股票都带上主题，消除 theme 列空白
    5. 行业兜底：V2 也未收录（地产/环保/钢铁/传媒等传统行业）时，按行业映射补主题
    6. 均无 → None（中性降级）
    """
    ts = _to_ts_code(code)
    info = stock_map.get(ts)
    if info:
        themes = info.get("themes") or []
        for t in themes:
            if t in scores_v2:
                return t
        dom = info.get("dominant_theme")
        if dom and dom in scores_v2:
            return dom
    if row_theme is not None and str(row_theme).strip() in scores_v2:
        return str(row_theme).strip()
    # V2 无条件兜底：不要求主题在 scores_v2 中，直接用 V2 关联回填
    if info:
        themes = info.get("themes") or []
        if themes:
            return themes[0]
        dom = info.get("dominant_theme")
        if dom:
            return dom
    # 行业兜底：V2 未收录的传统行业（地产/环保/钢铁/传媒等）
    if industry:
        ind = str(industry).strip()
        if ind in INDUSTRY_FALLBACK:
            return INDUSTRY_FALLBACK[ind]
    return None


# ==================== V1.2 交易面三因子 ====================
# 仅使用可靠列：涨停次数 / 连板能力 / 筹码面 / 波段属性（资金流入等全 NaN 列禁用）
VIA_RANK = {"leader_company": 12, "core_company": 6, "dc_industry_board": 0}


def _num(v, default=0.0):
    """哨兵数值转换：NaN/None → default"""
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _zt_attack(zt):
    """涨停次数 → 资金攻击分（0~31 次映射 20~98）"""
    zt = _num(zt)
    if zt <= 0:
        return 20.0
    if zt <= 3:
        return 20 + zt * 10 / 3          # 0-3 → 20~50
    if zt <= 10:
        return 50 + (zt - 3) * 25 / 7    # 3-10 → 50~75
    if zt <= 20:
        return 75 + (zt - 10) * 2.3      # 10-20 → 75~98
    return 98.0


def _lb_attack(lb):
    """连板能力（最大连板数）→ 资金攻击分（0~10 映射 20~98）"""
    lb = _num(lb)
    if lb <= 0:
        return 20.0
    if lb == 1:
        return 50.0
    if lb == 2:
        return 72.0
    if lb == 3:
        return 85.0
    return 98.0


def money_attack(zt, lb, theme_money):
    """
    资金攻击性 = 0.45×涨停攻击分 + 0.35×连板攻击分 + 0.20×主题资金热度
    涨停/连板是资金"用脚投票"的结果：存储链(德明利20/3)、机器人龙头等涨停多者高分；
    资源股(宝丰2/1、紫金1/1)涨停少 → 低分。
    """
    return round(0.45 * _zt_attack(zt) + 0.35 * _lb_attack(lb) + 0.20 * _num(theme_money, NEUTRAL), 1)


def leader_uniqueness(zt, lb, via, lc_count):
    """
    龙头唯一性：
      市场选龙头（核心）：连板≥3 → 90；连板2且涨停≥8 → 85；涨停≥15 → 82；
                         涨停≥8 → 72；涨停≥4 → 62；否则 45（资金未投票）
      + via 档位加持：leader_company +12 / core_company +6 / 普通成分 0
      + 主题内龙头集中度修正：leader+core ≤5（集中，龙头唯一性强）+10；
                             6~8 +5；≥15（分散，龙头不唯一）-8
    市场行为优先于静态标签：德明利等 dc_industry_board 成分但连板3/涨停20 →
    唯一性仍超 core_company 但无连板的资源股。
    """
    lb = _num(lb)
    zt = _num(zt)
    if lb >= 3:
        base = 90.0
    elif lb >= 2 and zt >= 8:
        base = 85.0
    elif zt >= 15:
        base = 82.0
    elif zt >= 8:
        base = 72.0
    elif zt >= 4:
        base = 62.0
    else:
        base = 45.0
    base += VIA_RANK.get(via or "", 0)
    if lc_count and lc_count <= 5:
        base += 10
    elif lc_count and lc_count <= 8:
        base += 5
    elif lc_count and lc_count >= 15:
        base -= 8
    return round(max(0.0, min(100.0, base)), 1)


def buy_point_quality(chip, wave, zt, lb):
    """
    买点质量 = 0.40×筹码面 + 0.40×波段属性 + 0.20×追高适度性
    - 筹码面/波段属性：V15 已有 0~100 标尺，直接加权
    - 追高适度性（防追高）：连板≥4 → 20（高位过热）；连板3 → 35；
      涨停≥15且连板≥2 → 40；涨停≥10 → 65；涨停≥5 → 75；否则 90（低位健康）
    低位低涨停（宝丰2/1）适度性满分；高位巨量连板（株冶8/4、德明利20/3）被惩罚
    """
    chip = _num(chip, NEUTRAL)
    wave = _num(wave, NEUTRAL)
    lb = _num(lb)
    zt = _num(zt)
    if lb >= 4:
        fit = 20.0
    elif lb == 3:
        fit = 35.0
    elif zt >= 15 and lb >= 2:
        fit = 40.0
    elif zt >= 10:
        fit = 65.0
    elif zt >= 5:
        fit = 75.0
    else:
        fit = 90.0
    return round(0.40 * chip + 0.40 * wave + 0.20 * fit, 1)


def trade_score(money_attack_v, leader_uniq, buy_point):
    """TradeScore = 0.40×资金攻击性 + 0.35×龙头唯一性 + 0.25×买点质量"""
    return round(0.40 * money_attack_v + 0.35 * leader_uniq + 0.25 * buy_point, 1)


# ==================== 操作建议 ====================

def action_for(theme_rank, stage_score, strength, vetoed):
    """六档操作建议：★★★★★ 重点关注 ~ ★ 回避"""
    if vetoed or pd.isna(theme_rank):
        return "★ 回避"
    if stage_score <= 30:
        return "★ 回避"
    if theme_rank >= 80 and strength >= 70:
        return "★★★★★ 重点关注"
    if theme_rank >= 70 or (strength >= 70 and stage_score >= 90):
        return "★★★★☆ 逢低布局"
    if theme_rank >= 60:
        return "★★★★ 继续跟踪"
    if theme_rank >= 50:
        return "★★★ 观察等待"
    if theme_rank >= 40:
        return "★★ 暂不参与"
    return "★ 回避"


def reason_for(theme, tm_info, action, vetoed):
    """自动一句话解释"""
    if vetoed:
        return "一票否决未通过，暂不参与。"
    if tm_info is None:
        return "主题无引擎覆盖，暂按中性处理。"
    stage_cn = tm_info["stage_cn"]
    strength = tm_info["strength"]
    mti_lv = tm_info["mti_level"]
    ta = tm_info["trade_action"] or "持有观望"
    action_txt = re.sub(r"[★☆]", "", action).strip() or "观察"
    return f"{theme}进入{stage_cn}阶段({mti_lv})，主题强度{strength:.0f}分，{ta}，建议{action_txt}。"


# ==================== 主流程 ====================

def run_tae(v15_csv=V15_CSV_DEFAULT, trade_date=None):
    if trade_date is None:
        trade_date = _latest_trade_date()

    # 1. 读 V15 输出（原字段全部保留）
    df = pd.read_csv(v15_csv, encoding="utf-8-sig")
    original_cols = list(df.columns)

    # 2. 加载 theme_score_v2 体系数据
    scores_v2 = load_theme_scores_v2(trade_date)
    stock_map, theme_refs = load_stock_map_v2(trade_date)
    market_info = load_market_analysis(trade_date)
    tm = build_theme_metrics(scores_v2, theme_refs)
    hit_themes = len(tm)
    hit_stocks = 0
    print(f"[TAE] 交易日 {trade_date} | V15 输入 {len(df)} 只 | 主题分析结果 {hit_themes} 主题")

    # 3. 逐股计算 14 个新字段
    theme_col = "theme" if "theme" in df.columns else None
    zt_col = "涨停次数" if "涨停次数" in df.columns else None
    lb_col = "连板能力" if "连板能力" in df.columns else None
    chip_col = "筹码面" if "筹码面" in df.columns else None
    wave_col = "波段属性" if "波段属性" in df.columns else None

    rows = []
    eng_themes = []
    for idx, r in df.iterrows():
        row_theme = r.get(theme_col) if theme_col else None
        eng_theme = theme_of_stock(r.get("code"), row_theme, scores_v2, stock_map,
                                   industry=r.get("industry"))
        eng_themes.append(eng_theme if eng_theme else "")
        ti = tm.get(eng_theme) if eng_theme else None
        if ti is None:
            strength = stage_score = style = money = NEUTRAL
        else:
            hit_stocks += 1
            strength = ti["strength"]
            stage_score = ti["stage_score"]
            style = ti["style"]
            money = ti["money"]
        theme_alpha = round(0.40 * strength + 0.25 * stage_score + 0.20 * style + 0.15 * money, 1)
        fs = r.get("FinalScore")
        if pd.notna(fs):
            theme_rank = round(0.70 * float(fs) + 0.30 * theme_alpha, 1)
        else:
            theme_rank = np.nan
        vetoed = pd.isna(r.get("FinalScore"))
        action = action_for(theme_rank, stage_score, strength, vetoed=vetoed)
        # ---- V1.2 交易面三因子 ----
        zt = r.get(zt_col) if zt_col else None
        lb = r.get(lb_col) if lb_col else None
        chip = r.get(chip_col) if chip_col else None
        wave = r.get(wave_col) if wave_col else None
        via = ""
        if eng_theme and theme_refs.get(eng_theme):
            via = theme_refs[eng_theme].get(_to_ts_code(r.get("code")), "")
        ma = money_attack(zt, lb, money if ti else NEUTRAL)
        lu = leader_uniqueness(zt, lb, via, ti["lc_count"] if ti else 0)
        bq = buy_point_quality(chip, wave, zt, lb)
        ts = trade_score(ma, lu, bq)
        trade_rank = round(0.70 * theme_rank + 0.30 * ts, 1) if pd.notna(theme_rank) else np.nan
        rows.append({
            "ThemeStrength": round(strength, 1),
            "ThemeStage": round(stage_score, 1),
            "StyleScore": round(style, 1),
            "MoneyScore": round(money, 1),
            "ThemeAlpha": theme_alpha,
            "ThemeRankScore": theme_rank,
            "MoneyAttack": ma,
            "LeaderUniqueness": lu,
            "BuyPointQuality": bq,
            "TradeScore": ts,
            "TradeRankScore": trade_rank,
            "Action": action,
            "Reason": reason_for(eng_theme, ti, action, vetoed=vetoed),
        })
    tae_df = pd.DataFrame(rows, index=df.index)
    print(f"[TAE] 成份股对照表命中 {hit_stocks}/{len(df)} 只（{hit_stocks * 100.0 / max(len(df), 1):.0f}%）")

    # 4. DynamicRank（按 TradeRankScore 降序，NaN 排最后）
    df["_trs"] = tae_df["TradeRankScore"]
    df["_trs_rank"] = df["_trs"].rank(ascending=False, method="min", na_option="bottom")
    tae_df["DynamicRank"] = df.apply(
        lambda r: int(r["_trs_rank"]) if pd.notna(r["_trs"]) else np.nan, axis=1)

    # 5. 合并输出（原字段 + 新字段）
    # 5.1 V2 主题回填：theme 列为空的行用引擎归属（V2）回填，消除空白
    if theme_col is not None:
        eng_series = pd.Series(eng_themes, index=df.index)
        fill_mask = df[theme_col].isna() | (df[theme_col].astype(str).str.strip() == "")
        if fill_mask.any():
            df.loc[fill_mask, theme_col] = eng_series[fill_mask].values
            print(f"[TAE] V2主题回填 {int(fill_mask.sum())} 只空 theme 行")
    tae_df["_eng_theme"] = eng_themes  # 引擎主题（报告用，不写入 CSV）
    out = pd.concat([df.drop(columns=["_trs", "_trs_rank"]), tae_df], axis=1)
    new_cols = ["ThemeStrength", "ThemeStage", "StyleScore", "MoneyScore",
                "ThemeAlpha", "ThemeRankScore", "MoneyAttack", "LeaderUniqueness",
                "BuyPointQuality", "TradeScore", "TradeRankScore",
                "DynamicRank", "Action", "Reason"]
    col_order = [c for c in original_cols if c in out.columns] + new_cols
    out = out[col_order + ["_eng_theme"]]
    out = out.sort_values("DynamicRank", ascending=True, na_position="last")

    # 6. 保存（_eng_theme 仅报告用，不写入 CSV）
    out.drop(columns=["_eng_theme"]).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"[TAE] 输出: {OUT_CSV}（{len(out)} 只，新增 {len(new_cols)} 字段）")

    # 7. 生成报告
    _write_report(out, df, tm, market_info, trade_date)
    return out


# ==================== 报告 ====================

def _write_report(out, v15_df, tm, market_info, trade_date):
    lines = []
    sep = "─" * 78
    W = "━" * 78

    lines.append(W)
    lines.append(f"  Theme Alpha 今日动态报告（TAE V1.2）- {trade_date}")
    lines.append(W)
    lines.append("")

    # 1. 今日市场风格
    lines.append("━━━ 1. 今日市场风格 ━━━")
    lines.append(sep)
    lines.append(f"大盘状态: {market_info['市场状态']}"
                 + (f" | 总趋势分: {market_info['趋势分']:.0f}" if market_info["趋势分"] else "")
                 + (f" | 涨停: {market_info['涨停']}" if market_info["涨停"] else "")
                 + (f" | 最高连板: {market_info['最高连板']}板" if market_info["最高连板"] else "")
                 + (f" | 建议仓位: {market_info['仓位']}%" if market_info["仓位"] else ""))
    style_top = sorted(tm.items(), key=lambda x: -x[1]["style"])[:5]
    if style_top:
        lines.append("风格适配 Top5: " + "、".join(f"{n}({v['style']:.0f}分,{v['mti_level']})" for n, v in style_top))
    lines.append("")

    # 2. 今日主线主题
    lines.append("━━━ 2. 今日主线主题（theme_scores_v2 综合强度） ━━━")
    lines.append(sep)
    main_top = sorted(tm.items(), key=lambda x: -x[1]["composite"])[:10]
    for n, v in main_top:
        lines.append(f"{v['comp_rank']:>2}. {n:<8} 强度{v['composite']:.0f} | 阶段:{v['stage_cn']:<4} | {v['mti_level']:<4} | MTI:{v['mti']:.0f} | 建议:{v['trade_action']}")
    lines.append("")

    # 3. 资金偏好
    lines.append("━━━ 3. 资金偏好（MoneyScore Top10） ━━━")
    lines.append(sep)
    money_top = sorted(tm.items(), key=lambda x: -x[1]["money"])[:10]
    for n, v in money_top:
        lines.append(f"{n:<8} Money {v['money']:.0f} | 资金/热度/龙头/广度 已加权")
    lines.append("")

    # 4. 动态 Top20
    lines.append("━━━ 4. 今日动态 Top20（TradeRankScore = 0.70×ThemeRankScore + 0.30×TradeScore） ━━━")
    lines.append(sep)
    dyn = out.dropna(subset=["DynamicRank"]).sort_values("DynamicRank").head(20)
    for _, r in dyn.iterrows():
        et = r.get("_eng_theme") or str(r.get("theme", ""))
        lines.append(f"{int(r['DynamicRank']):>2}. {str(r['code']):<8} {str(r.get('name', '')):<8} "
                     f"| 主题:{et:<8} | F:{r['FinalScore']:.1f} → T:{r['TradeRankScore']:.1f} "
                     f"| α:{r['ThemeAlpha']:.0f} | 攻:{r['MoneyAttack']:.0f} 独:{r['LeaderUniqueness']:.0f} 买:{r['BuyPointQuality']:.0f} | {r['Action']}")
    lines.append("")

    # 4b. 三因子解读（资金攻击性 / 龙头唯一性 / 买点质量 Top10）
    lines.append("━━━ 4b. 交易面三因子 Top10（资金攻击性 / 龙头唯一性 / 买点质量） ┋")
    lines.append(sep)
    for label, col in [("资金攻击性", "MoneyAttack"), ("龙头唯一性", "LeaderUniqueness"), ("买点质量", "BuyPointQuality")]:
        top = out.dropna(subset=[col]).sort_values(col, ascending=False).head(10)
        lines.append(f"【{label}】" + "、".join(f"{r.get('name','')}({r[col]:.0f})" for _, r in top.iterrows()))
    lines.append("")

    # 5. 主题变化（对比前一日 theme_scores_v2 composite 排名）
    lines.append("━━━ 5. 主题变化（vs 前一日） ┋")
    lines.append(sep)
    prev_date = _prev_trade_date(trade_date)
    prev_scores = load_theme_scores_v2(prev_date) if prev_date else {}
    if prev_scores:
        prev_comp = {n: float(r.get("composite_score") or 0.0) for n, r in prev_scores.items()}
        cur_comp = {n: v["composite"] for n, v in tm.items()}
        prev_rank = {n: i + 1 for i, n in enumerate(sorted(prev_comp, key=lambda x: -prev_comp[x]))}
        cur_rank = {n: i + 1 for i, n in enumerate(sorted(cur_comp, key=lambda x: -cur_comp[x]))}
        chg = []
        for n in cur_rank:
            if n in prev_rank:
                d = prev_rank[n] - cur_rank[n]
                if abs(d) >= 2:
                    chg.append((d, n))
        chg.sort(key=lambda x: -x[0])
        lines.append(f"对比 {prev_date} 综合强度排名变化（上升↑ / 下降↓）:")
        if chg:
            for d, n in chg[:8]:
                arrow = "↑" if d > 0 else "↓"
                lines.append(f"  {n:<8} {arrow}{abs(d)} 位")
        else:
            lines.append("  无明显排名变化")
    else:
        lines.append(f"无 {prev_date} 主题分析结果，跳过主题变化对比")
    lines.append("")

    # 6. 新晋机会（动态排名相对 V15 原 FinalScore 排名上升最多的通过股票）
    lines.append("━━━ 6. 新晋机会（动态榜相对基本面榜上升最多） ┋")
    lines.append(sep)
    v15_rank = v15_df["FinalScore"].rank(ascending=False, method="min")
    dyn_rank = out["DynamicRank"]
    mask = dyn_rank.notna()
    lift = pd.DataFrame({
        "code": out.loc[mask, "code"], "name": out.loc[mask, "name"],
        "theme": out.loc[mask, "_eng_theme"],
        "v15_rank": v15_rank[mask], "dyn_rank": dyn_rank[mask],
        "Action": out.loc[mask, "Action"],
    })
    lift["上升"] = lift["v15_rank"] - lift["dyn_rank"]
    lift = lift.sort_values("上升", ascending=False).head(10)
    if not lift.empty:
        for _, r in lift.iterrows():
            lines.append(f"  {str(r['code']):<8} {str(r['name']):<8} {str(r['theme']):<8} "
                         f"V15第{int(r['v15_rank'])} → 动态第{int(r['dyn_rank'])}（上升{int(r['上升'])}位）{r['Action']}")
    else:
        lines.append("  无")
    lines.append("")

    # 7. 风险提示
    lines.append("━━━ 7. 风险提示 ┋")
    lines.append(sep)
    risk = out[out["Action"].isin(["★ 回避", "★★ 暂不参与"])].head(10)
    decline_themes = [n for n, v in tm.items() if v["stage_score"] <= 60]
    if decline_themes:
        lines.append("主题进入退潮/分歧（谨慎）：" + "、".join(decline_themes[:10]))
    lines.append(f"回避/暂不参与标的 {len(risk)} 只，示例："
                 + "、".join(f"{r['code']}({r.get('name', '')})" for _, r in risk.head(5).iterrows()))
    lines.append("")
    lines.append(W)
    lines.append("  注：本报告基于 theme_score_v2 成份股对照表与主题分析结果，不构成投资建议。V15 基本面评分未被修改。")
    lines.append("  三因子（资金攻击性/龙头唯一性/买点质量）仅用于今日动态排序（TradeRankScore），不参与基本面评分。")
    lines.append(W)

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[TAE] 报告: {OUT_MD}")


def _prev_trade_date(trade_date):
    """找 theme_scores_v2 CSV 中 trade_date 的前一个交易日"""
    files = glob.glob(os.path.join(REPORT_DIR, "theme_scores_v2_*.csv"))
    dates = []
    for f in files:
        m = re.search(r"_(\d{8})\.csv$", f)
        if m:
            dates.append(m.group(1))
    dates.sort()
    if trade_date in dates:
        idx = dates.index(trade_date)
        if idx > 0:
            return dates[idx - 1]
    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Theme Alpha Engine V1.2")
    parser.add_argument("--input", type=str, default=V15_CSV_DEFAULT, help="V15 输出 CSV 路径")
    parser.add_argument("--date", type=str, default=None, help="交易日 YYYYMMDD（默认最新）")
    args = parser.parse_args()
    run_tae(v15_csv=args.input, trade_date=args.date)
