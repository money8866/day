# -*- coding: utf-8 -*-
"""
主线归因 + 产业β过滤器 V2

在 BullScore 多因子评分基础上，新增一层"主线归因"判断：
剔除价格周期股，保留真正由产业趋势（AI/科技升级）驱动的龙头。

核心逻辑：
  1. 判断股票是否属于"当前市场主线产业β驱动资产"
  2. 识别周期股（价格驱动、非结构性需求）
  3. 修正评分（主线加分、周期扣分）
  4. 输出经过主线过滤的精选池

输出关键字段：
  mainline_flag        - 是否属于产业β主线
  mainline_strength    - 主线强度 (0~1)
  beta_source          - 产业驱动来源描述
  cycle_flag           - 是否周期股
  adjusted_score       - 修正后得分 (最终排序依据)
"""
from typing import Dict, List, Tuple
from loguru import logger


# ============================================================
# 主线产业链定义（硬编码，不可配置）
# ============================================================

# 核心主线：AI/科技升级驱动的产业链
MAINLINE_CHAINS = {
    # 1) AI算力链 - 最核心主线
    "AI算力链": {
        "beta_source": "AI算力需求爆发（GPU/服务器/光模块）",
        "customer_tech_score": 1.0,   # 下游全是科技龙头
        "position_score": 0.9,        # 核心环节，卡脖子
        "order_sustainability_base": 0.85,  # 订单持续性极强
    },
    # 2) PCB链 - AI服务器核心受益
    "PCB链": {
        "beta_source": "AI服务器高多层PCB/IC载板需求",
        "customer_tech_score": 0.9,
        "position_score": 0.8,
        "order_sustainability_base": 0.80,
    },
    # 3) 半导体设备链 - 国产替代+AI扩产
    "半导体设备链": {
        "beta_source": "先进制程扩产+国产替代",
        "customer_tech_score": 1.0,
        "position_score": 1.0,  # 最卡脖子环节
        "order_sustainability_base": 0.85,
    },
    # 4) 半导体材料链
    "半导体材料链": {
        "beta_source": "先进制程材料国产化",
        "customer_tech_score": 0.9,
        "position_score": 0.9,
        "order_sustainability_base": 0.75,
    },
    # 5) 机器人链 - AI具身智能
    "机器人链": {
        "beta_source": "AI具身智能/人形机器人产业化",
        "customer_tech_score": 0.8,
        "position_score": 0.7,
        "order_sustainability_base": 0.70,
    },
    # 6) 消费电子链（仅限AI驱动部分，非传统周期）
    "消费电子链": {
        "beta_source": "AI终端/消费电子创新周期",
        "customer_tech_score": 0.7,
        "position_score": 0.6,
        "order_sustainability_base": 0.65,
    },
    # 7) 低空经济链
    "低空经济链": {
        "beta_source": "低空经济产业化",
        "customer_tech_score": 0.6,
        "position_score": 0.6,
        "order_sustainability_base": 0.60,
    },
}

# ============================================================
# 高风险周期行业识别（无AI/科技绑定）
# ============================================================

CYCLE_INDUSTRIES = {
    # 化工类（价格驱动 > 需求驱动）
    "化工", "化学原料", "化学纤维", "橡胶", "塑料",
    # 传统制造
    "钢铁", "有色金属", "煤炭", "石油石化",
    "建筑材料", "建筑装饰", "水泥", "玻璃",
    # 资源品
    "采掘", "矿业", "工业金属", "贵金属",
    # 传统周期制造
    "通用机械", "纺织制造", "造纸", "包装印刷",
    # 农业
    "农产品加工", "养殖", "饲料",
}

# 即使 chain_tag 是主线，但 industry 属于这些的仍要标记为 cycle_risk
CYCLE_WITHIN_CHAIN_INDUSTRIES = {
    "新能源链": {"化学原料", "化学制品", "有色金属", "钢铁"},
    "光伏链": {"有色金属", "钢铁", "化工"},
}

# ============================================================
# 周期股识别函数
# ============================================================

def _is_cycle_stock(result) -> Tuple[bool, List[str]]:
    """
    判断是否为周期股

    Returns:
        (is_cycle, reasons)
    """
    reasons = []

    # 规则1: 利润增长 > 100% 但 order_explosion_score < 30
    if result.profit_yoy > 100 and result.order_explosion_score < 30:
        reasons.append(f"利润暴增{result.profit_yoy:.0f}%但无订单支撑")

    # 规则2: industry_demand_score 高 (>70) 但不在主线链中
    if result.industry_demand_score > 70 and result.chain_tag not in MAINLINE_CHAINS:
        reasons.append(f"行业景气高({result.industry_demand_score:.0f})但非AI主线")

    # 规则3: 毛利率波动大（通过子维度中的毛利率变化推断）
    # 用 gross_margin 绝对值判断: 高毛利一般非周期，低毛利波动大是周期特征
    gm = result.gross_margin
    if gm < 15 and result.industry_demand_score > 60:
        reasons.append(f"低毛利({gm:.0f}%)高景气，价格周期特征")

    # 规则4: 行业属于已知周期行业
    ind = result.industry or ""
    if ind in CYCLE_INDUSTRIES:
        reasons.append(f"行业[{ind}]属于典型周期行业")

    # 规则5: chain_tag 是主线但 industry 是周期子行业
    ct = result.chain_tag
    if ct in CYCLE_WITHIN_CHAIN_INDUSTRIES and ind in CYCLE_WITHIN_CHAIN_INDUSTRIES[ct]:
        reasons.append(f"[{ct}]内[{ind}]仍为周期属性")

    # 规则6: 无明确科技客户绑定 — 通过 chain_tag 和 industry 推断
    if ct not in MAINLINE_CHAINS and ind not in CYCLE_INDUSTRIES:
        # 如果既不是主线也不是明确周期，检查是否有科技升级逻辑
        tech_keywords = ["电子", "通信", "计算机", "医药", "汽车"]
        if not any(k in ind for k in tech_keywords):
            reasons.append(f"无明确科技升级逻辑")

    is_cycle = len(reasons) >= 2
    return is_cycle, reasons


# ============================================================
# 产业β匹配度评分
# ============================================================

def _beta_match_score(chain_tag: str, industry: str) -> float:
    """判断股票是否匹配产业β主线 (0~1)"""
    # 在 MAINLINE_CHAINS 中 = 明确匹配
    if chain_tag in MAINLINE_CHAINS:
        return 1.0

    # 行业关键词部分匹配（扩展匹配）
    tech_mainline_keywords = {
        "AI", "算力", "光模块", "光通信", "液冷", "服务器",
        "PCB", "半导体", "芯片", "集成电路", "封测",
        "机器人", "人形机器人", "自动化",
        "数据要素", "云计算", "数据中心",
    }
    matched = sum(1 for k in tech_mainline_keywords if k in industry)
    if matched >= 2:
        return 0.8
    elif matched >= 1:
        return 0.5

    return 0.0


def _customer_tech_attr(chain_tag: str, industry: str) -> float:
    """客户科技属性评分 (0~1)"""
    if chain_tag in MAINLINE_CHAINS:
        return MAINLINE_CHAINS[chain_tag]["customer_tech_score"]

    # 行业推断
    tech_industries = {
        "计算机": 0.9, "电子": 0.8, "通信": 0.8,
        "汽车": 0.6, "电力设备": 0.5, "机械设备": 0.5,
    }
    for k, v in tech_industries.items():
        if k in industry:
            return v
    return 0.2


def _industry_position_score(chain_tag: str) -> float:
    """行业位置评分：上游/卡脖子环节越高"""
    if chain_tag in MAINLINE_CHAINS:
        return MAINLINE_CHAINS[chain_tag]["position_score"]
    return 0.3


# ============================================================
# 主线强度评分
# ============================================================

def _mainline_strength(result) -> Tuple[float, str]:
    """
    计算主线强度 (0~1)

    公式：
      mainline_strength =
        0.35 × β匹配度
      + 0.25 × 订单可持续性
      + 0.20 × 客户科技属性
      + 0.20 × 行业位置

    Returns:
        (strength, beta_source)
    """
    # 1) β匹配度
    beta_match = _beta_match_score(result.chain_tag, result.industry or "")

    # 2) 订单可持续性
    ct = result.chain_tag
    if ct in MAINLINE_CHAINS:
        order_base = MAINLINE_CHAINS[ct]["order_sustainability_base"]
    else:
        order_base = 0.3

    # 用 order_explosion_score 微调
    order_signal = result.order_explosion_score / 100.0  # 0~1

    # 综合订单可持续性：基线 + 实际信号修正
    order_sustainability = min(1.0, order_base * 0.6 + order_signal * 0.4)

    # 3) 客户科技属性
    customer_tech = _customer_tech_attr(ct, result.industry or "")

    # 4) 行业位置
    position = _industry_position_score(ct)

    # 综合
    strength = (
        0.35 * beta_match +
        0.25 * order_sustainability +
        0.20 * customer_tech +
        0.20 * position
    )
    strength = max(0.0, min(1.0, strength))

    # beta_source
    if beta_match >= 1.0 and ct in MAINLINE_CHAINS:
        beta_source = MAINLINE_CHAINS[ct]["beta_source"]
    elif beta_match >= 0.8:
        beta_source = f"AI/科技产业链关联行业"
    elif beta_match > 0:
        beta_source = "弱科技关联"
    else:
        beta_source = "非科技主线"

    return strength, beta_source


# ============================================================
# 评分修正
# ============================================================

def _adjust_score(final_score: float,
                   mainline_flag: bool,
                   cycle_flag: bool,
                   mainline_strength: float) -> float:
    """
    根据主线/周期状态修正 final_score

    规则：
      - cycle_flag=true: 扣 15~30 分（视严重程度）
      - mainline_flag=true: 加 5~15 分（视强度）
      - mainline_strength >= 0.8: 强制进入核心池（保底 85 分）
    """
    adjusted = final_score

    if cycle_flag:
        # 强度越高扣分越少（强主线周期的周期属性可以轻扣）
        if mainline_strength >= 0.6:
            penalty = 15
        else:
            penalty = 25
        adjusted -= penalty

    if mainline_flag:
        bonus = 5 + int(mainline_strength * 10)  # 5~15
        adjusted += bonus

    # mainline_strength >= 0.8 保底
    if mainline_strength >= 0.8 and adjusted < 85:
        adjusted = 85

    return max(0, min(100, adjusted))


# ============================================================
# 主入口：对 BullScoreResult 列表应用主线归因过滤
# ============================================================

# 为 BullScoreResult 的子维度新增 key 常量（确保一致性）
KEY_MAINLINE_FLAG = "mainline_flag"
KEY_MAINLINE_STRENGTH = "mainline_strength"
KEY_BETA_SOURCE = "beta_source"
KEY_CYCLE_FLAG = "cycle_flag"
KEY_CYCLE_REASONS = "cycle_reasons"
KEY_ADJUSTED_SCORE = "adjusted_score"


def apply_mainline_filter(results: list) -> list:
    """
    对 BullScoreResult 列表执行主线归因 + 产业β过滤

    Args:
        results: 已排序的 BullScoreResult 列表

    Returns:
        经过主线过滤、评分修正、TOP 20 的精选结果列表
        每个结果的 sub_details 中会注入主线/周期标注
    """
    if not results:
        return []

    logger.info("")
    logger.info("=" * 60)
    logger.info("主线归因 + 产业β过滤器 V2")
    logger.info("=" * 60)

    # ── Step 1: 对每个结果进行主线归因 ──
    for r in results:
        is_cycle, cycle_reasons = _is_cycle_stock(r)
        mainline_strength, beta_source = _mainline_strength(r)

        # 主线判定：strength >= 0.5 且必须实际匹配下游科技需求
        mainline_flag = mainline_strength >= 0.5

        # 评分修正
        adjusted_score = _adjust_score(
            r.final_score, mainline_flag, is_cycle, mainline_strength
        )

        # 注入 sub_details
        r.sub_details[KEY_MAINLINE_FLAG] = mainline_flag
        r.sub_details[KEY_MAINLINE_STRENGTH] = round(mainline_strength, 4)
        r.sub_details[KEY_BETA_SOURCE] = beta_source
        r.sub_details[KEY_CYCLE_FLAG] = is_cycle
        r.sub_details[KEY_CYCLE_REASONS] = cycle_reasons
        r.sub_details[KEY_ADJUSTED_SCORE] = round(adjusted_score, 2)

    # ── Step 2: 统计分布 ──
    total = len(results)
    mainline_count = sum(1 for r in results if r.sub_details.get(KEY_MAINLINE_FLAG))
    cycle_count = sum(1 for r in results if r.sub_details.get(KEY_CYCLE_FLAG))
    strong_mainline = sum(1 for r in results
                          if r.sub_details.get(KEY_MAINLINE_STRENGTH, 0) >= 0.8)

    logger.info(f"  总样本: {total}")
    logger.info(f"  主线标的: {mainline_count} ({mainline_count/total*100:.0f}%)")
    logger.info(f"  周期股识别: {cycle_count} ({cycle_count/total*100:.0f}%)")
    logger.info(f"  主线核心(强度>=0.8): {strong_mainline}")

    # ── Step 3: 筛选 ──
    filtered = [
        r for r in results
        if r.sub_details.get(KEY_MAINLINE_FLAG) is True
        and r.sub_details.get(KEY_CYCLE_FLAG) is False
        and r.sub_details.get(KEY_ADJUSTED_SCORE, 0) >= 80
    ]

    # 按 adjusted_score 排序
    filtered.sort(key=lambda r: r.sub_details.get(KEY_ADJUSTED_SCORE, 0), reverse=True)
    final = filtered[:20]

    logger.info(f"  最终主线精选: {len(final)} 只 (mainline=true, cycle=false, adjusted>=80)")

    # ── 详细输出 ──
    if final:
        logger.info("")
        logger.info("▼ 主线精选 TOP 20")
        logger.info(f"{'  #':>4} {'名称':<8} {'链标签':<14} {'主线强度':<8} {'原分':<6} {'修正分':<8} {'来源'}")
        for i, r in enumerate(final, 1):
            sd = r.sub_details
            if i <= 5 or i == len(final):
                logger.info(
                    f"  {i:>2}. {r.name:<8} {r.chain_tag:<14} "
                    f"{sd.get(KEY_MAINLINE_STRENGTH, 0):.2f}     "
                    f"{r.final_score:<6.0f} {sd.get(KEY_ADJUSTED_SCORE, 0):<8.0f} "
                    f"{sd.get(KEY_BETA_SOURCE, '')[:18]}"
                )

    # ── 输出被剔除的常见模式（便于调优） ──
    excluded = [
        r for r in results
        if r not in final
        and r.bull_level != "淘汰"
    ]
    if excluded:
        excluded_by_cycle = sum(1 for r in excluded
                                 if r.sub_details.get(KEY_CYCLE_FLAG))
        excluded_by_non_mainline = sum(1 for r in excluded
                                        if not r.sub_details.get(KEY_MAINLINE_FLAG))
        excluded_by_score = sum(1 for r in excluded
                                 if r.sub_details.get(KEY_ADJUSTED_SCORE, 0) < 80)
        logger.info("")
        logger.info(f"  排除明细:")
        logger.info(f"    非主线: {excluded_by_non_mainline}")
        logger.info(f"    周期股: {excluded_by_cycle}")
        logger.info(f"    修正分<80: {excluded_by_score}")

    return final


# ============================================================
# 打印主线分析摘要（独立调用用）
# ============================================================

def print_mainline_analysis(results: list):
    """打印完整的主线归因分析"""
    if not results:
        return

    # 按 mainline_strength 排序展示
    sorted_r = sorted(
        results,
        key=lambda r: r.sub_details.get(KEY_ADJUSTED_SCORE, 0),
        reverse=True,
    )[:30]

    logger.info("")
    logger.info("=" * 80)
    logger.info(f"{'股票':<10} {'链':<14} {'主':<5} {'强':<5} {'周':<5} {'原分':<6} {'修正分':<8} {'来源/周期原因'}")
    logger.info("-" * 80)
    for r in sorted_r:
        sd = r.sub_details
        mainline = "Y" if sd.get(KEY_MAINLINE_FLAG) else "N"
        cycle = "Y" if sd.get(KEY_CYCLE_FLAG) else "N"
        strength = sd.get(KEY_MAINLINE_STRENGTH, 0)
        adj = sd.get(KEY_ADJUSTED_SCORE, r.final_score)
        source = sd.get(KEY_BETA_SOURCE, "")
        cycle_reasons = "; ".join(sd.get(KEY_CYCLE_REASONS, []))
        desc = source if not cycle else f"⚠{cycle_reasons[:30]}"
        logger.info(
            f"{r.name:<10} {r.chain_tag:<14} {mainline:<5} "
            f"{strength:<5.2f} {cycle:<5} {r.final_score:<6.0f} {adj:<8.0f} {desc[:35]}"
        )
